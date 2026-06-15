#!/usr/bin/env python3
"""v1 stage 3 — MERGE: join TiC percentiles onto the v0 Medicare locality grid.

This is the heart of v1. It takes:

  * the **v0 Medicare baseline** (``data/therapy_oon_benchmark_v0_by_locality.csv``):
    one firm Medicare non-facility dollar per ``(cpt_code, CMS locality)``, plus the
    code's ``service_label`` / ``medicare_status`` / ``state`` and the v0 snapshot;
  * **AggregateRecords** (stage-2 output): measured allowed-amount and negotiated-rate
    percentiles per ``(cpt_code, region, amount_kind, payer)``.

…and produces the v1 dataset: for every ``(cpt_code, locality)`` it picks a ``basis``
by the CONTRACT precedence, attaches the matching *state*'s percentiles, and falls back
to the Medicare band where no qualifying TiC exists. Output keeps every v0 column (so
existing consumers don't break) and adds the v1 OON columns + provenance.

────────────────────────────────────────────────────────────────────────────────────
BASIS PRECEDENCE  (per cpt_code x state)
────────────────────────────────────────────────────────────────────────────────────
    tic_oon_actual        amount_kind == "allowed",    pooled n >= MIN_N   (the real OON target)
       >  tic_innetwork_proxy   amount_kind == "negotiated", pooled n >= MIN_N   (a proxy)
       >  medicare_multiple     no qualifying TiC -> v0 band (low = medicare, high = round(2x))

TiC in v1 is **state-level**: a state's percentiles are attached to *every* CMS
locality in that state. Where no TiC clears MIN_N for a state, that state's
localities fall back to ``medicare_multiple`` exactly as v0 did — a fallback row is
never mistaken for measured data because ``basis`` is stamped per row.

────────────────────────────────────────────────────────────────────────────────────
OON COLUMN MAPPING  (CONTRACT shape 3)
────────────────────────────────────────────────────────────────────────────────────
    TiC bases (tic_oon_actual / tic_innetwork_proxy):
        oon_low_usd  = p25      oon_high_usd = p75
        oon_mid_usd  = p50      oon_p90_usd  = p90
        oon_obs_n    = pooled observation count
        payer_scope  = "single" if exactly one payer contributed, else "multi"
    medicare_multiple (fallback):
        oon_low_usd  = medicare_nonfacility_usd
        oon_high_usd = round(2 x medicare, 2)
        oon_mid_usd  = None   oon_p90_usd = None   oon_obs_n = None   payer_scope = None

────────────────────────────────────────────────────────────────────────────────────
Pooling multiple payers for one (code, state, amount_kind)
────────────────────────────────────────────────────────────────────────────────────
Stage 2 emits **one record per payer** (it computes percentiles from that payer's
observations only). Merge needs ONE percentile set per (code, state, amount_kind).
We cannot re-pool raw observations here (we only have each payer's percentiles + n),
so we combine payers with an **n-weighted** representative percentile: each percentile
is the n-weighted mean of the contributing payers' same percentile, and ``oon_obs_n``
is the summed n. This is an approximation (true pooled percentiles would need the raw
rows), documented and honest — ``payer_scope = "multi"`` flags that more than one
payer's contracting shaped the figure. With a single payer it is exact.

Stdlib only. No network. Importable; ``merge(...)`` is the entry point.
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

from oon_bench import schemas
from oon_bench.schemas import AggregateRecord

# ── Contract constants — single-sourced from oon_bench.schemas ───────────────────
# merge imports every contract constant + the percentile math + the typed row
# shapes from schemas so the parallel stages (aggregate/merge/query) can NEVER
# disagree on MIN_N, basis strings, the fallback multiplier, or column order.
# We re-bind a few under merge-local names for readability and back-compat with
# callers/tests that import them from this module.
MIN_N = schemas.MIN_N

# Medicare fallback band: low = 1x medicare, high = MEDICARE_OON_MULT_HIGH x medicare.
# Mirrors build_baseline.OON_MULT_HIGH via schemas so the fallback band is byte-
# identical to v0. (Exposed as MEDICARE_OON_MULT_HIGH for back-compat.)
MEDICARE_OON_MULT_HIGH = schemas.MEDICARE_MULT_HIGH

# The three bases, strongest first (schemas.BASIS_PRECEDENCE order).
BASIS_TIC_OON = schemas.BASIS_OON_ACTUAL
BASIS_TIC_PROXY = schemas.BASIS_INNETWORK_PROXY
BASIS_MEDICARE = schemas.BASIS_MEDICARE_MULTIPLE

# amount_kind -> basis it produces when it clears MIN_N (from schemas).
_KIND_TO_BASIS = dict(schemas.AMOUNT_KIND_TO_BASIS)
# Precedence order to try (highest first): allowed (oon_actual) before negotiated
# (proxy). Derived from BASIS_PRECEDENCE so it can't drift from the basis order.
_BASIS_TO_KIND = {b: k for k, b in _KIND_TO_BASIS.items()}
_KIND_PRECEDENCE = tuple(
    _BASIS_TO_KIND[b] for b in schemas.BASIS_PRECEDENCE if b in _BASIS_TO_KIND
)

METHODOLOGY_VERSION = schemas.METHODOLOGY_VERSION
DISCLAIMER = schemas.DISCLAIMER

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_BASELINE_CSV = os.path.join(
    REPO_ROOT, "data", "therapy_oon_benchmark_v0_by_locality.csv"
)

# Canonical merged CSV column order = the MergedRow dataclass field order in schemas
# (v0 columns first for back-compat, then the v1 additive columns). Splitting it the
# same way merge builds rows keeps the header derived from the single source of truth.
_MERGED_COLUMNS = list(schemas.MERGED_CSV_COLUMNS)
# The v1 additive columns (everything after the v0 block).
V1_EXTRA_FIELDS = [
    "basis",
    "oon_low_usd",
    "oon_high_usd",
    "oon_mid_usd",
    "oon_p90_usd",
    "oon_obs_n",
    "payer_scope",
]
# The v0 columns carried through unchanged = the canonical order minus the v1 extras.
V0_FIELDS = [c for c in _MERGED_COLUMNS if c not in V1_EXTRA_FIELDS]


# ── Shared data shapes ───────────────────────────────────────────────────────────
# AggregateRecord (CONTRACT shape 2) is imported from schemas above — the single
# typed input type the aggregate stage produces and merge consumes. ``_coerce_record``
# below also accepts the plain dicts the aggregate stage serializes.


@dataclass
class _StateKindPick:
    """The pooled percentile set chosen for one (cpt_code, region, amount_kind)."""

    p25: float
    p50: float
    p75: float
    p90: float
    n_obs: int
    payers: tuple  # sorted, distinct payer slugs that contributed
    snapshot_date: str

    @property
    def payer_scope(self) -> str:
        return "single" if len(self.payers) == 1 else "multi"


# ── Helpers ──────────────────────────────────────────────────────────────────────
def _coerce_record(rec) -> AggregateRecord:
    """Normalize any stage-2 record form into a schemas.AggregateRecord.

    Accepts:
      * a ``schemas.AggregateRecord`` (returned as-is);
      * a plain dict (CONTRACT shape 2 — what the aggregate stage serializes to JSONL);
      * any other typed record exposing ``to_dict()`` (e.g. ``aggregate.AggregateRecord``,
        a sibling dataclass of the same shape) — duck-typed through its dict form.
    ``AggregateRecord.from_dict`` ignores unknown keys and fills contract defaults, so
    a forward-compatible producer can add fields without breaking merge.
    """
    if isinstance(rec, AggregateRecord):
        return rec
    if isinstance(rec, dict):
        return AggregateRecord.from_dict(rec)
    to_dict = getattr(rec, "to_dict", None)
    if callable(to_dict):
        return AggregateRecord.from_dict(to_dict())
    raise TypeError(
        f"aggregate record must be AggregateRecord, dict, or expose to_dict(); "
        f"got {type(rec).__name__}"
    )


def _round2(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 2)


def _pool_payers(records: list[AggregateRecord]) -> _StateKindPick:
    """Combine multiple same-(code,region,kind) payer records into one percentile set.

    n-weighted mean per percentile; summed n; distinct payer set; latest snapshot date.
    With one payer this is exact (weights collapse to that payer). See module docstring
    for why a weighted mean (not true pooled percentiles) is the honest choice here.
    """
    total_n = sum(r.n_obs for r in records)
    # total_n is guaranteed > 0 by the caller (records only kept when pooled n >= MIN_N),
    # but guard anyway so a degenerate all-zero input can't divide by zero.
    if total_n <= 0:
        # Fall back to a plain mean so we never raise; this branch is unreachable
        # under the MIN_N gate but keeps the function total.
        k = len(records)
        return _StateKindPick(
            p25=round(sum(r.p25 for r in records) / k, 2),
            p50=round(sum(r.p50 for r in records) / k, 2),
            p75=round(sum(r.p75 for r in records) / k, 2),
            p90=round(sum(r.p90 for r in records) / k, 2),
            n_obs=0,
            payers=tuple(sorted({r.payer for r in records if r.payer})),
            snapshot_date=max((r.snapshot_date for r in records), default=""),
        )

    def wmean(attr: str) -> float:
        return round(
            sum(getattr(r, attr) * r.n_obs for r in records) / total_n, 2
        )

    return _StateKindPick(
        p25=wmean("p25"),
        p50=wmean("p50"),
        p75=wmean("p75"),
        p90=wmean("p90"),
        n_obs=total_n,
        payers=tuple(sorted({r.payer for r in records if r.payer})),
        snapshot_date=max((r.snapshot_date for r in records), default=""),
    )


def _index_aggregates(
    records: Iterable,
) -> dict[tuple[str, str, str], _StateKindPick]:
    """Group aggregate records by (cpt_code, region, amount_kind), enforce MIN_N, pool.

    Returns only the (code, region, kind) groups whose POOLED n >= MIN_N. Groups under
    MIN_N are dropped here so the selection step only ever sees publishable percentiles.
    """
    grouped: dict[tuple[str, str, str], list[AggregateRecord]] = defaultdict(list)
    for raw in records:
        rec = _coerce_record(raw)
        if rec.amount_kind not in _KIND_TO_BASIS:
            # Unknown amount_kind — ignore defensively (contract only defines two).
            continue
        key = (rec.cpt_code, rec.region, rec.amount_kind)
        grouped[key].append(rec)

    picks: dict[tuple[str, str, str], _StateKindPick] = {}
    for key, recs in grouped.items():
        pooled = _pool_payers(recs)
        if pooled.n_obs >= MIN_N:
            picks[key] = pooled
    return picks


def _select_basis(
    cpt_code: str,
    state: str,
    picks: dict[tuple[str, str, str], _StateKindPick],
) -> tuple[str, Optional[_StateKindPick]]:
    """Apply CONTRACT precedence for one (cpt_code, state).

    Returns (basis, pick). ``pick`` is None for the medicare_multiple fallback.
    Precedence: allowed (tic_oon_actual) > negotiated (tic_innetwork_proxy) > medicare.
    """
    for kind in _KIND_PRECEDENCE:
        pick = picks.get((cpt_code, state, kind))
        if pick is not None:  # already MIN_N-gated in _index_aggregates
            return _KIND_TO_BASIS[kind], pick
    return BASIS_MEDICARE, None


def _load_baseline(baseline_csv: str) -> list[dict]:
    """Read the v0 by-locality CSV with the stdlib csv reader (handles quoted commas).

    Returns the rows as dicts with the v0 columns. ``medicare_nonfacility_usd`` is
    coerced to float; everything else stays as the source string.
    """
    rows: list[dict] = []
    with open(baseline_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                {
                    "cpt_code": r["cpt_code"].strip(),
                    "service_label": r["service_label"],
                    "medicare_status": r["medicare_status"].strip(),
                    "state": r["state"].strip(),
                    "locality_name": r["locality_name"],
                    "medicare_nonfacility_usd": float(r["medicare_nonfacility_usd"]),
                    "snapshot_date": r["snapshot_date"].strip(),
                    "methodology_version": r["methodology_version"].strip(),
                }
            )
    return rows


def _build_row(
    base: dict,
    basis: str,
    pick: Optional[_StateKindPick],
    snapshot_date: str,
) -> dict:
    """Assemble one v1 output row (CONTRACT shape 3) from a baseline row + a basis pick."""
    medicare = base["medicare_nonfacility_usd"]
    row = {
        "cpt_code": base["cpt_code"],
        "service_label": base["service_label"],
        "medicare_status": base["medicare_status"],
        "state": base["state"],
        "locality_name": base["locality_name"],
        "medicare_nonfacility_usd": medicare,
        "snapshot_date": snapshot_date,
        "methodology_version": METHODOLOGY_VERSION,
        "basis": basis,
    }
    if basis == BASIS_MEDICARE:
        row.update(
            {
                "oon_low_usd": _round2(medicare),
                "oon_high_usd": _round2(medicare * MEDICARE_OON_MULT_HIGH),
                "oon_mid_usd": None,
                "oon_p90_usd": None,
                "oon_obs_n": None,
                "payer_scope": None,
            }
        )
    else:
        assert pick is not None
        row.update(
            {
                "oon_low_usd": _round2(pick.p25),
                "oon_high_usd": _round2(pick.p75),
                "oon_mid_usd": _round2(pick.p50),
                "oon_p90_usd": _round2(pick.p90),
                "oon_obs_n": int(pick.n_obs),
                "payer_scope": pick.payer_scope,
            }
        )
    return row


# ── Writers ──────────────────────────────────────────────────────────────────────
def _write_by_locality_csv(path: str, rows: list[dict]) -> None:
    fieldnames = V0_FIELDS + V1_EXTRA_FIELDS
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_national_csv(path: str, national_rows: list[dict]) -> None:
    """National CSV: one row per cpt_code. Same columns as by-locality minus the
    locality-specific name (kept as 'US' so the column set is stable for consumers)."""
    fieldnames = V0_FIELDS + V1_EXTRA_FIELDS
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in national_rows:
            w.writerow(r)


def _write_json(
    path: str,
    json_codes: list[dict],
    meta: dict,
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "codes": json_codes}, f, indent=2)


# ── Entry point ──────────────────────────────────────────────────────────────────
def merge(
    aggregate_records: Iterable,
    baseline_csv: str = DEFAULT_BASELINE_CSV,
    out_dir: Optional[str] = None,
    *,
    snapshot_date: Optional[str] = None,
) -> dict:
    """Merge TiC percentiles onto the v0 Medicare locality grid and write v1 outputs.

    Parameters
    ----------
    aggregate_records :
        Iterable of :class:`AggregateRecord` (or the equivalent dicts) — stage-2 output.
    baseline_csv :
        Path to ``therapy_oon_benchmark_v0_by_locality.csv`` (the v0 fallback layer).
    out_dir :
        Directory to write the three v1 artifacts into. Defaults to ``<repo>/data/v1``.
        Created if missing.
    snapshot_date :
        Snapshot stamp for the v1 release. Defaults to today (ISO ``YYYY-MM-DD``).

    Returns
    -------
    dict
        ``{"by_locality_rows", "national_rows", "json_codes", "meta", "paths"}`` — the
        in-memory result, so callers/tests can assert without re-reading the files.

    Writes
    ------
    ``<out_dir>/therapy_oon_benchmark_v1_by_locality.csv``
    ``<out_dir>/therapy_oon_benchmark_v1_national.csv``
    ``<out_dir>/therapy_oon_benchmark_v1.json``
    """
    if out_dir is None:
        out_dir = os.path.join(REPO_ROOT, "data", "v1")
    if snapshot_date is None:
        # Default to the methodology's pinned snapshot (kept in lockstep with v0 via
        # schemas.SNAPSHOT_DATE) rather than wall-clock today, so a rebuild is
        # reproducible and the merged row's Medicare + TiC halves share a release.
        snapshot_date = schemas.SNAPSHOT_DATE

    baseline = _load_baseline(baseline_csv)
    picks = _index_aggregates(aggregate_records)

    # ── by-locality: one row per (cpt_code, locality) ──
    by_locality_rows: list[dict] = []
    basis_counts = {BASIS_TIC_OON: 0, BASIS_TIC_PROXY: 0, BASIS_MEDICARE: 0}
    for base in baseline:
        basis, pick = _select_basis(base["cpt_code"], base["state"], picks)
        row = _build_row(base, basis, pick, snapshot_date)
        by_locality_rows.append(row)
        basis_counts[basis] += 1

    # ── national: one row per cpt_code (region "US" TiC if present, else medicare) ──
    # Per CONTRACT (3): the national row uses the state="US" TiC percentiles if they
    # clear MIN_N, otherwise the Medicare band. The Medicare national anchor is the
    # code's TRUE national rate (total_nonfac x CF, GPCI == 1.0), which lives in the
    # committed v0 national CSV next to the by-locality file — NOT the locality average.
    # _load_national_medicare reads that file and only falls back to a per-code mean
    # if the national CSV is absent (e.g. a fixture that ships only by-locality data).
    national_medicare = _load_national_medicare(baseline_csv)
    # Per-code label/status/snapshot from the first baseline row for that code.
    code_meta: dict[str, dict] = {}
    for base in baseline:
        code_meta.setdefault(
            base["cpt_code"],
            {
                "service_label": base["service_label"],
                "medicare_status": base["medicare_status"],
            },
        )

    national_rows: list[dict] = []
    national_basis_counts = {
        BASIS_TIC_OON: 0,
        BASIS_TIC_PROXY: 0,
        BASIS_MEDICARE: 0,
    }
    # Stable code order = order of first appearance in the baseline.
    seen: list[str] = []
    for base in baseline:
        if base["cpt_code"] not in seen:
            seen.append(base["cpt_code"])
    for code in seen:
        meta_c = code_meta[code]
        med_nat = national_medicare.get(code)
        if med_nat is None:
            # No national anchor available; skip this code from the national file
            # rather than fabricate one. (Should not happen with the committed data.)
            continue
        basis, pick = _select_basis(code, "US", picks)
        national_base = {
            "cpt_code": code,
            "service_label": meta_c["service_label"],
            "medicare_status": meta_c["medicare_status"],
            "state": "US",
            "locality_name": "US",
            "medicare_nonfacility_usd": med_nat,
        }
        nrow = _build_row(national_base, basis, pick, snapshot_date)
        national_rows.append(nrow)
        national_basis_counts[basis] += 1

    # ── JSON: mirror v0 nesting (meta + codes[].national + codes[].localities[]) ──
    json_codes = _build_json_codes(
        seen, code_meta, national_rows, by_locality_rows
    )

    meta = {
        "methodology_version": METHODOLOGY_VERSION,
        "snapshot_date": snapshot_date,
        "min_observations": MIN_N,
        "percentiles": list(schemas.PERCENTILES),
        "oon_multiplier_band": [schemas.MEDICARE_MULT_LOW, MEDICARE_OON_MULT_HIGH],
        "basis_precedence": list(schemas.BASIS_PRECEDENCE),
        "basis_counts_by_locality": basis_counts,
        "basis_counts_national": national_basis_counts,
        "column_mapping": {
            "oon_low_usd": "p25 (TiC) or medicare (fallback)",
            "oon_high_usd": "p75 (TiC) or 2x medicare (fallback)",
            "oon_mid_usd": "p50 (TiC) or null (fallback)",
            "oon_p90_usd": "p90 (TiC) or null (fallback)",
        },
        "sources": [
            "CMS PFS RVU26A (v0 Medicare anchor)",
            "Payer Transparency-in-Coverage MRFs (v1 percentiles)",
        ],
        "disclaimer": DISCLAIMER,
    }

    os.makedirs(out_dir, exist_ok=True)
    by_loc_path = os.path.join(out_dir, "therapy_oon_benchmark_v1_by_locality.csv")
    nat_path = os.path.join(out_dir, "therapy_oon_benchmark_v1_national.csv")
    json_path = os.path.join(out_dir, "therapy_oon_benchmark_v1.json")

    _write_by_locality_csv(by_loc_path, by_locality_rows)
    _write_national_csv(nat_path, national_rows)
    _write_json(json_path, json_codes, meta)

    return {
        "by_locality_rows": by_locality_rows,
        "national_rows": national_rows,
        "json_codes": json_codes,
        "meta": meta,
        "paths": {
            "by_locality_csv": by_loc_path,
            "national_csv": nat_path,
            "json": json_path,
        },
    }


def _load_national_medicare(baseline_csv: str) -> dict[str, float]:
    """Return {cpt_code: national medicare non-facility USD}.

    Prefers the committed v0 national CSV (``therapy_oon_benchmark_v0_national.csv``)
    that sits next to the by-locality file, because that carries the TRUE national
    Medicare rate (total_nonfac x CF, GPCI == 1.0). If that file is absent (e.g. a
    test passes only a by-locality fixture), fall back to the per-code mean of the
    by-locality Medicare values as a stable, deterministic anchor.
    """
    national_csv = os.path.join(
        os.path.dirname(os.path.abspath(baseline_csv)),
        "therapy_oon_benchmark_v0_national.csv",
    )
    if os.path.isfile(national_csv):
        out: dict[str, float] = {}
        with open(national_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                code = r["cpt_code"].strip()
                try:
                    out[code] = float(r["medicare_nonfacility_usd"])
                except (KeyError, ValueError):
                    continue
        if out:
            return out

    # Fallback: per-code mean of by-locality medicare values.
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    with open(baseline_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r["cpt_code"].strip()
            try:
                sums[code] += float(r["medicare_nonfacility_usd"])
                counts[code] += 1
            except (KeyError, ValueError):
                continue
    return {c: round(sums[c] / counts[c], 2) for c in sums if counts[c]}


def _build_json_codes(
    code_order: list[str],
    code_meta: dict[str, dict],
    national_rows: list[dict],
    by_locality_rows: list[dict],
) -> list[dict]:
    """Build the codes[] array mirroring v0's nesting plus the v1 basis/percentile fields."""
    nat_by_code = {r["cpt_code"]: r for r in national_rows}
    loc_by_code: dict[str, list[dict]] = defaultdict(list)
    for r in by_locality_rows:
        loc_by_code[r["cpt_code"]].append(r)

    codes: list[dict] = []
    for code in code_order:
        nat = nat_by_code.get(code)
        if nat is None:
            continue
        meta_c = code_meta[code]
        national_block = {
            "medicare_usd": nat["medicare_nonfacility_usd"],
            "basis": nat["basis"],
            "oon_low_usd": nat["oon_low_usd"],
            "oon_high_usd": nat["oon_high_usd"],
            "oon_mid_usd": nat["oon_mid_usd"],
            "oon_p90_usd": nat["oon_p90_usd"],
            "oon_obs_n": nat["oon_obs_n"],
            "payer_scope": nat["payer_scope"],
        }
        localities = [
            {
                "state": r["state"],
                "locality_name": r["locality_name"],
                "medicare_usd": r["medicare_nonfacility_usd"],
                "basis": r["basis"],
                "oon_low_usd": r["oon_low_usd"],
                "oon_high_usd": r["oon_high_usd"],
                "oon_mid_usd": r["oon_mid_usd"],
                "oon_p90_usd": r["oon_p90_usd"],
                "oon_obs_n": r["oon_obs_n"],
                "payer_scope": r["payer_scope"],
            }
            for r in loc_by_code.get(code, [])
        ]
        codes.append(
            {
                "cpt_code": code,
                "service_label": meta_c["service_label"],
                "medicare_status": meta_c["medicare_status"],
                "national": national_block,
                "localities": localities,
            }
        )
    return codes


# ── CLI (optional convenience; not required by the contract) ────────────────────
def _main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Merge stage-2 TiC percentile JSONL onto the v0 Medicare grid."
    )
    p.add_argument(
        "aggregate_jsonl",
        nargs="?",
        help="path to a JSONL file of AggregateRecord dicts (CONTRACT shape 2). "
        "If omitted, runs the merge with NO TiC data -> every row is medicare_multiple.",
    )
    p.add_argument("--baseline", default=DEFAULT_BASELINE_CSV, help="v0 by-locality CSV")
    p.add_argument("--out-dir", default=None, help="output dir (default <repo>/data/v1)")
    p.add_argument("--snapshot-date", default=None, help="ISO date stamp for the release")
    args = p.parse_args(argv)

    records: list[dict] = []
    if args.aggregate_jsonl:
        with open(args.aggregate_jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

    result = merge(
        records,
        baseline_csv=args.baseline,
        out_dir=args.out_dir,
        snapshot_date=args.snapshot_date,
    )
    counts = result["meta"]["basis_counts_by_locality"]
    print(
        "v1 merge complete:\n"
        f"  by-locality rows: {len(result['by_locality_rows'])}\n"
        f"  national rows:    {len(result['national_rows'])}\n"
        f"  basis (by-locality): {counts}\n"
        f"  wrote:\n"
        f"    {result['paths']['by_locality_csv']}\n"
        f"    {result['paths']['national_csv']}\n"
        f"    {result['paths']['json']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
