#!/usr/bin/env python3
"""
Stage 2 — AGGREGATE: JSONL rate rows -> percentile records.

INPUT  (contract #1): the JSONL emitted by ``v1_tic/filter_mrf.py``, one object
per observed price:

    {"billing_code": "90837", "billing_code_type": "CPT", "amount": 142.5,
     "amount_kind": "negotiated"|"allowed",
     "negotiation_arrangement": "ffs"|..., "billing_class": "professional"|...,
     "region": "TX"|null, "source_file": "...", "payer": "aetna",
     "provider_tin": "..."|null}

OUTPUT (contract #2): one record per (cpt_code, region, amount_kind, payer):

    {"cpt_code": "90837", "region": "TX", "amount_kind": "allowed",
     "payer": "aetna", "n_obs": 23,
     "p25": ..., "p50": ..., "p75": ..., "p90": ..., "min": ..., "max": ...,
     "snapshot_date": "2026-06-07"}

Rules (the contract, enforced here):
  * drop non-positive amounts;
  * require billing_class == "professional" (institutional/facility dropped);
  * for the in-network (negotiated) proxy, keep only ffs arrangements
    (bundle/capitation are not per-session-meaningful);
  * dedupe identical (provider_tin, amount) within a group;
  * clip absurd outliers (beyond OUTLIER_CLIP_MULT x median);
  * only EMIT a group when n_obs >= MIN_N;
  * percentiles via linear interpolation (schemas.percentile).

Stdlib only. Importable (``aggregate_rows`` / ``aggregate_file``) so the
end-to-end test and ``run_local.py`` call it directly without shelling out.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable, Iterator, Optional

from oon_bench import schemas


# --------------------------------------------------------------------------- #
# Stage-2 output record (CONTRACT shape #2).
#
# ``aggregate_rows`` returns plain dicts because that is what the downstream
# merge/query stages consume (merge accepts either a dict or its own
# AggregateRecord via ``_coerce_record``). This dataclass is the brief's named
# type for callers that want a typed object; ``aggregate_records`` returns these
# and ``AggregateRecord.from_dict`` round-trips a dict produced by
# ``aggregate_rows``. The two views are kept byte-identical in field set + order.
# --------------------------------------------------------------------------- #
_RECORD_FIELDS = (
    "cpt_code",
    "region",
    "amount_kind",
    "payer",
    "n_obs",
    "p25",
    "p50",
    "p75",
    "p90",
    "min",
    "max",
    "snapshot_date",
)


@dataclass(frozen=True)
class AggregateRecord:
    """One aggregated distribution for a (cpt_code, region, amount_kind, payer)."""

    cpt_code: str
    region: str
    amount_kind: str  # "allowed" | "negotiated"
    payer: str
    n_obs: int
    p25: float
    p50: float
    p75: float
    p90: float
    min: float
    max: float
    snapshot_date: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AggregateRecord":
        return cls(**{k: d[k] for k in _RECORD_FIELDS})


# --------------------------------------------------------------------------- #
# Row-level filtering — the contract's "drop bad rows" rules.
# --------------------------------------------------------------------------- #
def _row_is_eligible(row: dict) -> bool:
    """True if a filter JSONL row may enter a percentile group.

    Encodes every drop rule that applies BEFORE grouping. Region/payer/code are
    grouping keys (handled by the caller); here we reject rows that must never
    contribute to any percentile regardless of group.
    """
    # Therapy-CPT scope (defense in depth; the filter already constrains this).
    if not schemas.is_therapy_code(row.get("billing_code", "")):
        return False

    # Amount must be a positive number.
    amount = row.get("amount")
    if not isinstance(amount, (int, float)):
        return False
    if amount <= 0:
        return False

    kind = row.get("amount_kind")
    if kind not in (schemas.AMOUNT_ALLOWED, schemas.AMOUNT_NEGOTIATED):
        return False

    # Professional (office) only. allowed-amounts rows frequently omit
    # billing_class (the OON files don't always carry it); the contract says
    # "require billing_class == professional" for the GROUP. We treat a MISSING
    # billing_class on an allowed row as acceptable (OON files routinely lack
    # it), but a row that POSITIVELY declares a non-professional class is
    # dropped. A negotiated (in-network) row must positively be professional —
    # those files reliably carry the field.
    billing_class = row.get("billing_class")
    if kind == schemas.AMOUNT_NEGOTIATED:
        if billing_class != schemas.PROFESSIONAL_CLASS:
            return False
        # in-network proxy: only fee-for-service is per-session meaningful.
        arrangement = (row.get("negotiation_arrangement") or "").strip().lower()
        if arrangement and arrangement != "ffs":
            return False
    else:  # allowed (OON)
        if billing_class is not None and billing_class != schemas.PROFESSIONAL_CLASS:
            return False

    return True


def _group_key(row: dict) -> tuple:
    """(cpt_code, region, amount_kind, payer) — the contract's output grain."""
    return (
        str(row["billing_code"]).strip(),
        schemas.normalize_region(row.get("region")),
        row["amount_kind"],
        str(row.get("payer") or "unknown"),
    )


def _clip_outliers(amounts: list[float]) -> list[float]:
    """Drop values beyond OUTLIER_CLIP_MULT x median (and below median / mult).

    Operates on a positive-amount list. Uses the median as the anchor so a
    single absurd value can't drag the bound. Returns the surviving values
    (still unsorted is fine; caller sorts).
    """
    if not amounts:
        return amounts
    srt = sorted(amounts)
    med = schemas.percentile(srt, 50)
    if med <= 0:
        return amounts
    hi = med * schemas.OUTLIER_CLIP_MULT
    lo = med / schemas.OUTLIER_CLIP_MULT
    return [a for a in amounts if lo <= a <= hi]


def aggregate_rows(rows: Iterable[dict]) -> list[dict]:
    """Aggregate an iterable of filter JSONL rows into percentile records.

    Returns a list of AGGREGATE OUTPUT records (contract #2), sorted for
    deterministic output by (cpt_code, region, amount_kind, payer). Only groups
    with n_obs >= MIN_N (AFTER dedupe + outlier clipping) are emitted.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if not _row_is_eligible(row):
            continue
        groups[_group_key(row)].append(row)

    out: list[dict] = []
    for (cpt, region, kind, payer), grp in groups.items():
        # Dedupe identical (provider_tin, amount) observations.
        unique = schemas.iter_unique(grp)
        amounts = [schemas.round2(r["amount"]) for r in unique if r["amount"] > 0]
        # Clip outliers, then re-check the threshold on the surviving sample.
        amounts = _clip_outliers(amounts)
        n = len(amounts)
        if n < schemas.MIN_N:
            continue
        amounts.sort()
        out.append(
            {
                "cpt_code": cpt,
                "region": region,
                "amount_kind": kind,
                "payer": payer,
                "n_obs": n,
                "p25": schemas.round2(schemas.percentile(amounts, 25)),
                "p50": schemas.round2(schemas.percentile(amounts, 50)),
                "p75": schemas.round2(schemas.percentile(amounts, 75)),
                "p90": schemas.round2(schemas.percentile(amounts, 90)),
                "min": schemas.round2(amounts[0]),
                "max": schemas.round2(amounts[-1]),
                "snapshot_date": schemas.SNAPSHOT_DATE,
            }
        )

    out.sort(key=lambda r: (r["cpt_code"], r["region"], r["amount_kind"], r["payer"]))
    return out


# --------------------------------------------------------------------------- #
# JSONL I/O helpers
# --------------------------------------------------------------------------- #
def iter_jsonl(path: str) -> Iterator[dict]:
    """Yield parsed objects from a JSONL file, skipping blank lines."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def aggregate_records(rows: Iterable[dict]) -> list[AggregateRecord]:
    """Like ``aggregate_rows`` but return typed ``AggregateRecord`` objects.

    Thin typed view over ``aggregate_rows`` for callers that prefer the dataclass
    named in the brief. The merge stage accepts either form.
    """
    return [AggregateRecord.from_dict(r) for r in aggregate_rows(rows)]


def aggregate_jsonl(in_path: str, out_path: str) -> int:
    """Read filter JSONL at ``in_path``, write AggregateRecord JSONL to ``out_path``.

    Returns the number of records written. This is the brief's file-to-file
    entry point: it composes ``aggregate_file`` (read + aggregate) with
    ``write_records`` (serialize CONTRACT shape #2, one JSON object per line).
    """
    return write_records(aggregate_file(in_path), out_path)


def aggregate_file(jsonl_path: str) -> list[dict]:
    """Aggregate one JSONL file (path) into percentile records."""
    return aggregate_rows(iter_jsonl(jsonl_path))


def aggregate_files(jsonl_paths: Iterable[str]) -> list[dict]:
    """Aggregate several JSONL files together (multi-payer percentiles)."""
    def _all() -> Iterator[dict]:
        for p in jsonl_paths:
            yield from iter_jsonl(p)

    return aggregate_rows(_all())


def write_records(records: list[dict], out_path: str) -> int:
    """Write aggregate records as JSONL. Returns count written."""
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return len(records)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        description="Aggregate filter JSONL into percentile records (contract #2)."
    )
    p.add_argument("inputs", nargs="+", help="one or more filter JSONL files")
    p.add_argument(
        "-o", "--out", default=None, help="output JSONL path (default stdout)"
    )
    args = p.parse_args(argv)

    records = aggregate_files(args.inputs)
    if args.out:
        n = write_records(records, args.out)
        sys.stderr.write(f"wrote {n} aggregate records to {args.out}\n")
    else:
        for rec in records:
            sys.stdout.write(json.dumps(rec, separators=(",", ":")) + "\n")
        sys.stderr.write(f"emitted {len(records)} aggregate records\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
