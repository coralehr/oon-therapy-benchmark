"""
Shared data contract for every v1 stage — the single source of truth for the
JSONL row shapes, the percentile math, MIN_N, basis precedence, and the
snapshot stamp. Every stage imports from here so the parallel stages stay
compatible (see the DATA CONTRACTS block in the v1 design notes).

Stdlib only. No I/O here — just constants, light validators, and the
percentile function so aggregate/merge/query never disagree on the math.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from typing import IO, Any, Iterable, Iterator, Optional

# Make ``import therapy_codes`` work no matter where a stage is invoked from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from therapy_codes import THERAPY_CODES  # noqa: E402

# --------------------------------------------------------------------------- #
# Scope + labels (our own plain-language labels; never AMA descriptors)
# --------------------------------------------------------------------------- #
THERAPY_CODE_SET = frozenset(c["code"] for c in THERAPY_CODES)
CODE_LABELS = {c["code"]: c["label"] for c in THERAPY_CODES}

# --------------------------------------------------------------------------- #
# Contract constants
# --------------------------------------------------------------------------- #
# Minimum observations to PUBLISH a TiC percentile. Below this we fall back a
# basis tier. Documented in meta and stamped per row.
MIN_N = 10

# The snapshot stamp for the v1 methodology. Kept in lockstep with the v0
# snapshot date so a merged row's two halves (Medicare + TiC) share a release.
SNAPSHOT_DATE = "2026-06-07"
METHODOLOGY_VERSION = "v1-tic-2026A"

# The percentiles we publish, in order. p50 is the headline "mid".
PERCENTILES = (25, 50, 75, 90)

# amount_kind values, in the contract.
AMOUNT_ALLOWED = "allowed"  # real OON allowed amount  -> tic_oon_actual
AMOUNT_NEGOTIATED = "negotiated"  # in-network rate (proxy)  -> tic_innetwork_proxy

# basis values, highest precedence first. This ORDER is the selection rule.
BASIS_OON_ACTUAL = "tic_oon_actual"  # amount_kind=allowed,    n>=MIN_N
BASIS_INNETWORK_PROXY = "tic_innetwork_proxy"  # amount_kind=negotiated, n>=MIN_N
BASIS_MEDICARE_MULTIPLE = "medicare_multiple"  # v0 fallback band
BASIS_PRECEDENCE = (
    BASIS_OON_ACTUAL,
    BASIS_INNETWORK_PROXY,
    BASIS_MEDICARE_MULTIPLE,
)

# Set form of the legal basis values, for membership checks / validation. The
# tuple ``BASIS_PRECEDENCE`` carries the precedence ORDER (strongest first);
# ``BASES`` is the unordered membership set (``basis in BASES``).
BASES = frozenset(BASIS_PRECEDENCE)

# Legal amount_kind / payer_scope membership sets (used by validators + tests).
AMOUNT_KINDS = frozenset({AMOUNT_ALLOWED, AMOUNT_NEGOTIATED})
PAYER_SCOPE_SINGLE = "single"
PAYER_SCOPE_MULTI = "multi"
PAYER_SCOPES = frozenset({PAYER_SCOPE_SINGLE, PAYER_SCOPE_MULTI})

# Mapping amount_kind -> the basis a TiC percentile of that kind earns.
AMOUNT_KIND_TO_BASIS = {
    AMOUNT_ALLOWED: BASIS_OON_ACTUAL,
    AMOUNT_NEGOTIATED: BASIS_INNETWORK_PROXY,
}

# The v0 fallback multipliers (low = Medicare itself, high = 2x). Mirrors
# build_baseline.py's OON_MULT_LOW / OON_MULT_HIGH so the fallback band is
# byte-identical to v0.
MEDICARE_MULT_LOW = 1.0
MEDICARE_MULT_HIGH = 2.0

# We only benchmark non-facility PROFESSIONAL (office) claims. A TiC row whose
# billing_class is institutional/facility is dropped at the aggregate stage.
PROFESSIONAL_CLASS = "professional"

# National pseudo-region used when a row carries no state geo.
NATIONAL_REGION = "US"

# Outlier clip: drop amounts beyond this multiple of the group median (and below
# 1/this). Guards against data-entry artifacts before computing percentiles.
OUTLIER_CLIP_MULT = 10.0

DISCLAIMER = (
    "Estimate only, not a guarantee of payment. Figures labeled tic_oon_actual or "
    "tic_innetwork_proxy are derived from payers' published Transparency-in-Coverage "
    "data (a quarterly snapshot); figures labeled medicare_multiple are a "
    "Medicare-anchored placeholder band. Verify with the payer before relying on any "
    "amount."
)


# --------------------------------------------------------------------------- #
# Confidence: how strong is a served figure?
# --------------------------------------------------------------------------- #
# high   = measured OON allowed amounts with a healthy sample
# medium = in-network proxy, OR a small-n OON sample (>= MIN_N but modest)
# low    = Medicare-multiple fallback (modeled, not measured)
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# Sample size at/above which an OON-actual figure is "high" confidence.
HIGH_CONFIDENCE_N = 30


def confidence_for(basis: str, n_obs: Optional[int]) -> str:
    """Map (basis, n_obs) onto the confidence enum per the contract."""
    if basis == BASIS_OON_ACTUAL:
        if n_obs is not None and n_obs >= HIGH_CONFIDENCE_N:
            return CONFIDENCE_HIGH
        return CONFIDENCE_MEDIUM
    if basis == BASIS_INNETWORK_PROXY:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW  # medicare_multiple


# --------------------------------------------------------------------------- #
# Percentile math — ONE implementation, imported everywhere.
# --------------------------------------------------------------------------- #
def percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (a.k.a. numpy 'linear' / type 7).

    ``sorted_values`` MUST already be sorted ascending and non-empty. ``pct`` is
    0..100. This is the same definition across aggregate/merge/query so a
    percentile computed once is never recomputed differently downstream.
    """
    if not sorted_values:
        raise ValueError("percentile() requires at least one value")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def percentiles(values: Iterable[float]) -> dict:
    """Compute the published percentile summary for a set of amounts.

    Returns the CONTRACT summary dict every v1 stage consumes::

        {"p25": ..., "p50": ..., "p75": ..., "p90": ..., "min": ..., "max": ...}

    All values are floats rounded to the cent, computed with the SAME
    linear-interpolation ``percentile`` above (numpy 'linear' / type 7). This is
    the single source of truth for the math: aggregate computes it, merge stores
    it, query surfaces it — so a p75 is never recomputed differently downstream.

    Notes:
      * Input order does not matter; ``values`` is sorted once internally.
      * No filtering happens here — the caller (aggregate) drops non-positive
        amounts, dedupes, clips outliers, and applies MIN_N *before* calling.
        This helper does pure math on whatever it is handed.
      * Raises ``ValueError`` on empty input (a percentile of nothing is
        undefined). Callers gate on MIN_N first, so this should not fire in the
        pipeline.
      * n == 1: every percentile (and min/max) equals that single value.
    """
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("percentiles() requires at least one value")
    summary = {f"p{p}": round2(percentile(ordered, p)) for p in PERCENTILES}
    summary["min"] = round2(ordered[0])
    summary["max"] = round2(ordered[-1])
    return summary


def round2(x: float) -> float:
    return round(float(x), 2)


# --------------------------------------------------------------------------- #
# Light validators used by the stages (cheap, no third-party deps).
# --------------------------------------------------------------------------- #
def is_therapy_code(code: str) -> bool:
    return str(code).strip() in THERAPY_CODE_SET


def label_for(code: str) -> Optional[str]:
    """Our plain-language label for a CPT code, or ``None`` if out of scope.

    Reads from ``CODE_LABELS`` (built from ``therapy_codes.THERAPY_CODES``). Never
    returns an AMA descriptor — only the labels declared in ``therapy_codes.py``.
    This is the single accessor every stage uses to attach a ``service_label``.
    """
    return CODE_LABELS.get(str(code).strip())


def all_codes() -> list[str]:
    """The in-scope therapy CPT codes, in ``therapy_codes.py`` declaration order."""
    return [c["code"] for c in THERAPY_CODES]


def normalize_region(region: Optional[str]) -> str:
    """A filter row's region is a 2-letter state or null. Null -> national 'US'."""
    if region is None:
        return NATIONAL_REGION
    r = str(region).strip().upper()
    if not r:
        return NATIONAL_REGION
    return r


def basis_rank(basis: str) -> int:
    """Lower is better (more authoritative). Used to pick the winning basis."""
    try:
        return BASIS_PRECEDENCE.index(basis)
    except ValueError:
        return len(BASIS_PRECEDENCE)


def dedupe_key(row: dict) -> tuple:
    """Dedupe identical (provider_tin, amount) observations within a group.

    Per the contract: 'dedupe identical (provider_tin, amount)'. When a TIN is
    absent we cannot tell duplicate contracts apart, so we keep every such row
    (a None TIN never collapses with another None TIN unless the amount AND a
    stable surrogate match — here we fold on (tin, amount) and treat None TIN as
    distinct only when amounts differ). We key on (tin, amount); rows with the
    same TIN and the same amount collapse to one.
    """
    return (row.get("provider_tin"), round2(row["amount"]))


def iter_unique(rows: Iterable[dict]) -> list[dict]:
    """Drop exact (provider_tin, amount) duplicates, preserving first occurrence.

    Rows with a None provider_tin are NOT deduped against each other (we can't
    prove they're the same contract), so two None-TIN rows with the same amount
    are both kept — this is deliberately conservative to avoid under-counting
    OON-actual observations, which are already sparse.
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        tin = row.get("provider_tin")
        if tin is None:
            out.append(row)  # keep every untinned observation
            continue
        key = (tin, round2(row["amount"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Typed row dataclasses — one per numbered shape in the DATA CONTRACT.
#
# The stages above operate on plain dicts (fast, JSONL-native). These
# dataclasses are the *typed* face of the same shapes: they give a producer a
# constructor with named fields, validate presence of required keys, and
# round-trip through JSONL. ``from_dict`` ignores unknown keys (forward-compat:
# a producer may add fields without breaking a consumer) and supplies the
# contract defaults for absent optional fields.
# --------------------------------------------------------------------------- #
def _from_dict(cls, d: dict):
    """Build a dataclass instance from ``d``, keeping only declared fields.

    Unknown keys are dropped. A missing REQUIRED field raises ``TypeError`` from
    the constructor — exactly the contract violation we want to surface loudly.
    """
    field_names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in field_names})


@dataclass
class FilterRow:
    """CONTRACT (1) — one observed price, as emitted by ``v1_tic/filter_mrf.py``.

    This is AGGREGATE's input. ``amount_kind`` distinguishes an in-network
    negotiated rate ("negotiated", a proxy) from an OON allowed amount
    ("allowed", the real target). ``region`` is a 2-letter state, "US", or
    ``None`` (OON files frequently omit geo).
    """

    billing_code: str
    amount: float
    amount_kind: str  # "negotiated" | "allowed"
    source_file: str
    payer: str
    billing_code_type: str = "CPT"
    negotiation_arrangement: Optional[str] = None
    billing_class: Optional[str] = None  # "professional" | "institutional" | None
    region: Optional[str] = None
    provider_tin: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "FilterRow":
        return _from_dict(cls, d)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AggregateRecord:
    """CONTRACT (2) — percentile summary for a (code, region, amount_kind, payer).

    Only constructed when ``n_obs >= MIN_N``; the percentile fields come from the
    shared :func:`percentiles` helper. ``amount_kind`` is retained so the merge
    stage can apply basis precedence (allowed -> tic_oon_actual,
    negotiated -> tic_innetwork_proxy via :data:`AMOUNT_KIND_TO_BASIS`).
    """

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
    snapshot_date: str = SNAPSHOT_DATE

    @classmethod
    def from_dict(cls, d: dict) -> "AggregateRecord":
        return _from_dict(cls, d)

    @classmethod
    def from_percentiles(
        cls,
        *,
        cpt_code: str,
        region: str,
        amount_kind: str,
        payer: str,
        n_obs: int,
        pct: dict,
        snapshot_date: str = SNAPSHOT_DATE,
    ) -> "AggregateRecord":
        """Construct from a :func:`percentiles` result dict + the grouping keys.

        Keeps the percentile field names in ONE place: a caller computes
        ``percentiles(values)`` and hands the dict straight here.
        """
        return cls(
            cpt_code=cpt_code,
            region=region,
            amount_kind=amount_kind,
            payer=payer,
            n_obs=n_obs,
            p25=pct["p25"],
            p50=pct["p50"],
            p75=pct["p75"],
            p90=pct["p90"],
            min=pct["min"],
            max=pct["max"],
            snapshot_date=snapshot_date,
        )

    @property
    def basis(self) -> str:
        """The merged-row basis this aggregate maps to (via ``amount_kind``)."""
        return AMOUNT_KIND_TO_BASIS[self.amount_kind]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MergedRow:
    """CONTRACT (3) — a v1 by-locality row: v0 columns + OON enrichment + basis.

    The v0 columns are carried verbatim so existing consumers don't break; the
    additive fields stamp which ``basis`` produced the OON band and how strong it
    is. For a TiC basis, ``oon_low/high`` are p25..p75 and ``oon_mid/p90`` carry
    p50/p90; for ``medicare_multiple`` they are the v0 band (low=medicare,
    high=2x) with the percentile/obs fields ``None``.
    """

    # --- v0 columns (unchanged) ---
    cpt_code: str
    service_label: str
    medicare_status: str
    state: str
    locality_name: str
    medicare_nonfacility_usd: float
    snapshot_date: str
    methodology_version: str
    # --- v1 additive columns ---
    basis: str  # one of BASIS_PRECEDENCE / BASES
    oon_low_usd: float
    oon_high_usd: float
    oon_mid_usd: Optional[float] = None
    oon_p90_usd: Optional[float] = None
    oon_obs_n: Optional[int] = None
    payer_scope: Optional[str] = None  # "single" | "multi" | None (fallback)

    @classmethod
    def from_dict(cls, d: dict) -> "MergedRow":
        return _from_dict(cls, d)

    def to_dict(self) -> dict:
        return asdict(self)


# Canonical CSV column order for the merged outputs: v0 columns first
# (back-compat), then the v1 additive columns. Derived from the dataclass so the
# CSV header and the row shape can never drift apart.
MERGED_CSV_COLUMNS = tuple(f.name for f in fields(MergedRow))


@dataclass
class Estimate:
    """The low/mid/high band a query returns. ``mid`` is ``None`` for fallback bands."""

    low: float
    high: float
    mid: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueryResult:
    """CONTRACT (4) — what ``oon_bench.query.get_rate(cpt, region)`` returns.

    ``confidence`` derives from basis + n_obs (high = tic_oon_actual large-n;
    medium = proxy or small-n; low = medicare_multiple). ``n_obs`` is ``None`` for
    a Medicare-fallback answer. ``estimate`` nests as ``{low, mid, high}``.
    """

    cpt_code: str
    service_label: str
    region: str
    basis: str
    estimate: Estimate
    confidence: str  # "high" | "medium" | "low"
    source: str
    snapshot_date: str
    disclaimer: str
    n_obs: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "QueryResult":
        data = dict(d)
        est = data.get("estimate")
        if isinstance(est, dict):
            data["estimate"] = Estimate(
                low=est.get("low"), high=est.get("high"), mid=est.get("mid")
            )
        return _from_dict(cls, data)

    def to_dict(self) -> dict:
        """JSON-ready dict; ``asdict`` recurses into the nested ``Estimate``."""
        return asdict(self)


# --------------------------------------------------------------------------- #
# JSONL (de)serialization — the FilterRow / AggregateRecord wire format.
#
# The shared, typed JSONL helpers. (``oon_bench.aggregate`` has its own
# path-based ``iter_jsonl`` for the dict fast-path; these are the typed
# equivalents that hydrate/serialize the dataclasses above and live here so the
# wire format has ONE definition.)
# --------------------------------------------------------------------------- #
def to_jsonl_line(obj: Any) -> str:
    """Serialize a dataclass row (or plain dict) to ONE compact JSON line.

    No trailing newline; callers join with ``"\\n"``. Compact separators match
    the filter's own output so a dict round-trip is byte-stable.
    """
    if hasattr(obj, "to_dict"):
        payload = obj.to_dict()
    elif hasattr(obj, "__dataclass_fields__"):
        payload = asdict(obj)
    elif isinstance(obj, dict):
        payload = obj
    else:
        raise TypeError(f"cannot serialize {type(obj)!r} to JSONL")
    return json.dumps(payload, separators=(",", ":"))


def from_jsonl_line(line: str, cls=None):
    """Parse one JSONL line. Returns a ``cls`` instance if given, else a dict.

    ``cls`` (e.g. ``FilterRow`` / ``AggregateRecord``) must expose ``from_dict``.
    A blank line raises ``ValueError`` — skip blanks before calling, or use
    :func:`iter_jsonl_stream` which skips them for you.
    """
    stripped = line.strip()
    if not stripped:
        raise ValueError("cannot parse an empty JSONL line")
    obj = json.loads(stripped)
    return obj if cls is None else cls.from_dict(obj)


def iter_jsonl_stream(stream: IO[str], cls=None) -> Iterator[Any]:
    """Yield parsed rows from a text stream of JSONL, skipping blank lines.

    Streams line-by-line (never materializes the whole file). With ``cls`` it
    yields dataclass instances; without, plain dicts.
    """
    for raw in stream:
        if not raw.strip():
            continue
        yield from_jsonl_line(raw, cls)


def load_jsonl(path: str, cls=None) -> list:
    """Read an entire JSONL file into a list (convenience for small files)."""
    with open(path, "r", encoding="utf-8") as f:
        return list(iter_jsonl_stream(f, cls))


def dump_jsonl(rows: Iterable[Any], stream: IO[str]) -> int:
    """Write an iterable of rows (dataclasses or dicts) as JSONL. Returns count."""
    n = 0
    for row in rows:
        stream.write(to_jsonl_line(row) + "\n")
        n += 1
    return n
