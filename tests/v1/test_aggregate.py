"""Unit tests for ``oon_bench.aggregate`` — v1 stage 2 (percentile rollup).

These feed SYNTHETIC filter-output rows (CONTRACT shape #1) into the aggregate
stage and assert it honors the AGGREGATE OUTPUT contract (shape #2):

  * grouping by (cpt_code, region, amount_kind, payer);
  * the MIN_N gate — a group of 9 is dropped, a group of 10 is kept;
  * dedupe of identical (provider_tin, amount) observations;
  * professional-only filtering (institutional/facility rows dropped);
  * non-positive amounts dropped;
  * percentile values computed by linear interpolation;
  * region=None normalized to "US" (national);
  * the file-to-file ``aggregate_jsonl`` round-trip and the typed
    ``AggregateRecord`` view.

Stdlib + pytest only. The repo-root sys.path insertion in tests/v1/conftest.py
makes ``import oon_bench`` resolve to the package at the repo root.
"""

from __future__ import annotations

import json


from oon_bench import schemas
from oon_bench.aggregate import (
    AggregateRecord,
    aggregate_jsonl,
    aggregate_records,
    aggregate_rows,
)

# The contract's MIN_N. We read it from schemas so the test tracks the source of
# truth (and fails loudly if someone silently changes the gate).
MIN_N = schemas.MIN_N
SNAPSHOT = schemas.SNAPSHOT_DATE

# A code that is definitely in the therapy scope (individual therapy, 60 min).
CODE = "90837"
# A second in-scope code for multi-group tests.
CODE2 = "90834"


# --------------------------------------------------------------------------- #
# Row factory — builds a single CONTRACT-shape-#1 filter row.
# --------------------------------------------------------------------------- #
def _row(
    amount,
    *,
    code=CODE,
    amount_kind="allowed",
    region="CA",
    payer="aetna",
    billing_class=None,
    negotiation_arrangement=None,
    provider_tin=None,
):
    """One filter-output row. Defaults model an OON allowed-amount observation
    (billing_class absent, no arrangement), the real v1 target."""
    return {
        "billing_code": code,
        "billing_code_type": "CPT",
        "amount": amount,
        "amount_kind": amount_kind,
        "negotiation_arrangement": negotiation_arrangement,
        "billing_class": billing_class,
        "region": region,
        "source_file": "synthetic.json",
        "payer": payer,
        "provider_tin": provider_tin,
    }


def _allowed_rows(amounts, **kw):
    """N allowed (OON) rows, each with a UNIQUE provider_tin so none dedupe."""
    rows = []
    for i, amt in enumerate(amounts):
        rows.append(_row(amt, provider_tin=f"tin-{i:04d}", **kw))
    return rows


def _negotiated_rows(amounts, **kw):
    """N in-network (negotiated) rows: professional + ffs + unique TINs."""
    kw.setdefault("billing_class", "professional")
    kw.setdefault("negotiation_arrangement", "ffs")
    rows = []
    for i, amt in enumerate(amounts):
        rows.append(
            _row(amt, amount_kind="negotiated", provider_tin=f"tin-{i:04d}", **kw)
        )
    return rows


def _only(records):
    """Assert exactly one group came back and return it."""
    assert len(records) == 1, f"expected exactly one group, got {len(records)}"
    return records[0]


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #
def test_groups_by_code_region_kind_payer():
    """Distinct (cpt, region, amount_kind, payer) tuples => distinct records."""
    rows = []
    # Group A: 90837 / CA / allowed / aetna  (12 obs)
    rows += _allowed_rows([100 + i for i in range(12)], code=CODE, region="CA", payer="aetna")
    # Group B: 90837 / TX / allowed / aetna  (same code+payer+kind, diff region)
    rows += _allowed_rows([200 + i for i in range(11)], code=CODE, region="TX", payer="aetna")
    # Group C: 90834 / CA / allowed / aetna  (diff code)
    rows += _allowed_rows([50 + i for i in range(10)], code=CODE2, region="CA", payer="aetna")
    # Group D: 90837 / CA / allowed / cigna  (diff payer)
    rows += _allowed_rows([100 + i for i in range(13)], code=CODE, region="CA", payer="cigna")
    # Group E: 90837 / CA / negotiated / aetna (diff amount_kind)
    rows += _negotiated_rows([90 + i for i in range(10)], code=CODE, region="CA", payer="aetna")

    records = aggregate_rows(rows)
    keys = {(r["cpt_code"], r["region"], r["amount_kind"], r["payer"]) for r in records}
    assert keys == {
        (CODE, "CA", "allowed", "aetna"),
        (CODE, "TX", "allowed", "aetna"),
        (CODE2, "CA", "allowed", "aetna"),
        (CODE, "CA", "allowed", "cigna"),
        (CODE, "CA", "negotiated", "aetna"),
    }
    # Records are emitted in a deterministic sort order.
    assert records == sorted(
        records,
        key=lambda r: (r["cpt_code"], r["region"], r["amount_kind"], r["payer"]),
    )


def test_region_none_becomes_us_national():
    """A row with region=None aggregates into the national 'US' bucket."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)], region=None)
    rec = _only(aggregate_rows(rows))
    assert rec["region"] == "US"


# --------------------------------------------------------------------------- #
# MIN_N gate
# --------------------------------------------------------------------------- #
def test_min_n_gate_drops_group_below_threshold():
    """A group with MIN_N - 1 distinct observations is NOT published."""
    rows = _allowed_rows([100 + i for i in range(MIN_N - 1)])  # 9 when MIN_N=10
    assert aggregate_rows(rows) == []


def test_min_n_gate_keeps_group_at_threshold():
    """A group with exactly MIN_N distinct observations IS published."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)])  # 10 when MIN_N=10
    rec = _only(aggregate_rows(rows))
    assert rec["n_obs"] == MIN_N


def test_min_n_gate_applies_after_dedupe():
    """Dedupe can pull a group from >= MIN_N raw rows down below the gate.

    11 raw rows but only 8 distinct (provider_tin, amount) pairs -> dropped.
    """
    # 8 unique tin'd observations + 3 exact duplicates of existing ones.
    rows = []
    for i in range(8):
        rows.append(_row(100 + i, provider_tin=f"tin-{i}"))
    # duplicates (same tin AND same amount) collapse away:
    rows.append(_row(100, provider_tin="tin-0"))
    rows.append(_row(101, provider_tin="tin-1"))
    rows.append(_row(102, provider_tin="tin-2"))
    assert len(rows) == 11
    assert aggregate_rows(rows) == []  # 8 distinct < MIN_N


# --------------------------------------------------------------------------- #
# Dedupe
# --------------------------------------------------------------------------- #
def test_dedupes_identical_tin_amount_pairs():
    """Identical (provider_tin, amount) rows collapse to a single observation."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)])  # 10 unique tins
    # Add 5 EXACT duplicates (same tin + same amount as existing rows).
    for i in range(5):
        rows.append(_row(100 + i, provider_tin=f"tin-{i:04d}"))
    rec = _only(aggregate_rows(rows))
    assert rec["n_obs"] == MIN_N  # duplicates did not inflate the count


def test_same_amount_different_tin_not_deduped():
    """Same amount but DIFFERENT TINs are distinct contracts -> both kept."""
    rows = [_row(150.0, provider_tin=f"tin-{i}") for i in range(MIN_N)]
    rec = _only(aggregate_rows(rows))
    assert rec["n_obs"] == MIN_N
    assert rec["min"] == rec["max"] == 150.0


def test_untinned_rows_are_not_deduped():
    """Rows with provider_tin=None are kept individually (can't prove identity).

    Two None-TIN rows with the SAME amount are both counted — deliberately
    conservative so sparse OON-actual observations aren't under-counted.
    """
    rows = [_row(120.0, provider_tin=None) for _ in range(MIN_N)]
    rec = _only(aggregate_rows(rows))
    assert rec["n_obs"] == MIN_N


# --------------------------------------------------------------------------- #
# Professional-only filter
# --------------------------------------------------------------------------- #
def test_institutional_rows_dropped_allowed():
    """An allowed row positively marked institutional/facility is dropped."""
    good = _allowed_rows([100 + i for i in range(MIN_N)])
    bad = [
        _row(9999.0, billing_class="institutional", provider_tin=f"inst-{i}")
        for i in range(5)
    ]
    rec = _only(aggregate_rows(good + bad))
    assert rec["n_obs"] == MIN_N
    assert rec["max"] < 9999.0  # the facility rows never entered the percentile


def test_allowed_rows_with_missing_billing_class_are_kept():
    """OON allowed-amount files routinely omit billing_class; those rows count."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)], billing_class=None)
    rec = _only(aggregate_rows(rows))
    assert rec["n_obs"] == MIN_N


def test_negotiated_requires_explicit_professional():
    """In-network rows must positively declare professional; others dropped.

    Negotiated files reliably carry billing_class, so a missing/non-professional
    class on a negotiated row is a real signal to drop it.
    """
    pro = _negotiated_rows([90 + i for i in range(MIN_N)])
    missing = [
        _row(90 + i, amount_kind="negotiated", billing_class=None,
             negotiation_arrangement="ffs", provider_tin=f"miss-{i}")
        for i in range(5)
    ]
    facility = [
        _row(90 + i, amount_kind="negotiated", billing_class="institutional",
             negotiation_arrangement="ffs", provider_tin=f"fac-{i}")
        for i in range(5)
    ]
    rec = _only(aggregate_rows(pro + missing + facility))
    assert rec["amount_kind"] == "negotiated"
    assert rec["n_obs"] == MIN_N  # only the explicitly-professional rows survive


def test_negotiated_non_ffs_arrangement_dropped():
    """Bundled/capitated in-network rows are not per-session and are dropped."""
    ffs = _negotiated_rows([100 + i for i in range(MIN_N)])
    bundle = [
        _row(500 + i, amount_kind="negotiated", billing_class="professional",
             negotiation_arrangement="bundle", provider_tin=f"bun-{i}")
        for i in range(6)
    ]
    rec = _only(aggregate_rows(ffs + bundle))
    assert rec["n_obs"] == MIN_N
    assert rec["max"] < 500.0


# --------------------------------------------------------------------------- #
# Non-positive amounts
# --------------------------------------------------------------------------- #
def test_non_positive_amounts_dropped():
    """Zero, negative, and the MIN_N gate interact: only positives count."""
    good = _allowed_rows([100 + i for i in range(MIN_N)])
    junk = [
        _row(0.0, provider_tin="z0"),
        _row(-50.0, provider_tin="z1"),
        _row(0, provider_tin="z2"),
        _row(-0.01, provider_tin="z3"),
    ]
    rec = _only(aggregate_rows(good + junk))
    assert rec["n_obs"] == MIN_N
    assert rec["min"] > 0


def test_non_positive_can_drop_group_below_min_n():
    """If removing non-positive rows leaves < MIN_N positives, drop the group."""
    rows = _allowed_rows([100 + i for i in range(MIN_N - 1)])  # 9 positives
    rows.append(_row(0.0, provider_tin="zero"))  # padding that doesn't count
    rows.append(_row(-1.0, provider_tin="neg"))
    assert aggregate_rows(rows) == []


# --------------------------------------------------------------------------- #
# Out-of-scope code rejection
# --------------------------------------------------------------------------- #
def test_non_therapy_code_rejected():
    """A code outside THERAPY_CODES never contributes, even at high volume."""
    therapy = _allowed_rows([100 + i for i in range(MIN_N)], code=CODE)
    em = _allowed_rows([95 + i for i in range(MIN_N)], code="99213")  # office E/M
    records = aggregate_rows(therapy + em)
    rec = _only(records)
    assert rec["cpt_code"] == CODE


# --------------------------------------------------------------------------- #
# Percentile values (linear interpolation)
# --------------------------------------------------------------------------- #
def test_percentiles_linear_interpolation_known_values():
    """Exact percentile values for a known 10-point ramp [100,110,...,190].

    Linear interpolation (numpy 'linear' / type 7) on n=10:
      p25: rank 2.25 -> 120 + 0.25*10 = 122.5
      p50: rank 4.50 -> 140 + 0.50*10 = 145.0
      p75: rank 6.75 -> 160 + 0.75*10 = 167.5
      p90: rank 8.10 -> 180 + 0.10*10 = 181.0
    """
    amounts = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
    rows = _allowed_rows(amounts)
    rec = _only(aggregate_rows(rows))
    assert rec["n_obs"] == 10
    assert rec["p25"] == 122.5
    assert rec["p50"] == 145.0
    assert rec["p75"] == 167.5
    assert rec["p90"] == 181.0
    assert rec["min"] == 100.0
    assert rec["max"] == 190.0
    # Percentiles are monotonically non-decreasing.
    assert rec["min"] <= rec["p25"] <= rec["p50"] <= rec["p75"] <= rec["p90"] <= rec["max"]


def test_percentiles_match_two_point_interpolation():
    """A clean 2-value distribution interpolates exactly (sanity on the math).

    With [100, 200] (padded to MIN_N by repeating across DISTINCT tins so the
    distribution stays exactly {100, 200} half-and-half):
      sorted = [100,100,100,100,100, 200,200,200,200,200]
      p25: rank 0.25*9 = 2.25 -> 100 (both straddling values are 100) = 100.0
      p50: rank 4.5         -> between idx4(100) and idx5(200) = 150.0
      p75: rank 6.75        -> 200.0
      p90: rank 8.1         -> 200.0
    """
    amounts = [100.0] * 5 + [200.0] * 5
    rows = [_row(a, provider_tin=f"tin-{i}") for i, a in enumerate(amounts)]
    rec = _only(aggregate_rows(rows))
    assert rec["p25"] == 100.0
    assert rec["p50"] == 150.0
    assert rec["p75"] == 200.0
    assert rec["p90"] == 200.0


def test_extreme_outlier_clipped():
    """A value far beyond OUTLIER_CLIP_MULT x median is clipped before percentiles."""
    base = [100 + i for i in range(MIN_N)]  # median ~104.5
    outlier = 100000.0  # >> 10x median
    rows = _allowed_rows(base) + [_row(outlier, provider_tin="huge")]
    rec = _only(aggregate_rows(rows))
    # The clipped outlier does not become the max.
    assert rec["max"] < outlier
    assert rec["max"] <= max(base)


# --------------------------------------------------------------------------- #
# Output shape / provenance
# --------------------------------------------------------------------------- #
def test_output_record_has_full_contract_shape():
    """Every emitted record carries exactly the AGGREGATE OUTPUT fields."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)])
    rec = _only(aggregate_rows(rows))
    assert set(rec.keys()) == {
        "cpt_code", "region", "amount_kind", "payer", "n_obs",
        "p25", "p50", "p75", "p90", "min", "max", "snapshot_date",
    }
    assert rec["snapshot_date"] == SNAPSHOT
    assert rec["amount_kind"] in ("allowed", "negotiated")


def test_empty_input_yields_no_records():
    assert aggregate_rows([]) == []


# --------------------------------------------------------------------------- #
# Typed view + file round-trip
# --------------------------------------------------------------------------- #
def test_aggregate_records_typed_view_matches_dicts():
    """aggregate_records() returns AggregateRecord objects equal to the dicts."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)])
    dicts = aggregate_rows(rows)
    typed = aggregate_records(rows)
    assert len(typed) == len(dicts) == 1
    assert isinstance(typed[0], AggregateRecord)
    assert typed[0].to_dict() == dicts[0]
    assert AggregateRecord.from_dict(dicts[0]) == typed[0]


def test_aggregate_jsonl_round_trip(tmp_path):
    """aggregate_jsonl reads filter JSONL and writes AggregateRecord JSONL."""
    rows = (
        _allowed_rows([100 + i for i in range(MIN_N)], region="CA", payer="aetna")
        + _negotiated_rows([90 + i for i in range(MIN_N)], region="CA", payer="aetna")
        + _allowed_rows([100 + i for i in range(MIN_N - 1)], region="TX")  # dropped (n=9)
    )
    in_path = tmp_path / "filter.jsonl"
    out_path = tmp_path / "aggregate.jsonl"
    in_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    n = aggregate_jsonl(str(in_path), str(out_path))
    assert n == 2  # CA/allowed and CA/negotiated kept; TX (n=9) dropped

    written = [
        json.loads(line)
        for line in out_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(written) == 2
    # Output equals the in-memory aggregation of the same rows.
    assert written == aggregate_rows(rows)
    kinds = {(r["region"], r["amount_kind"]) for r in written}
    assert kinds == {("CA", "allowed"), ("CA", "negotiated")}


def test_blank_lines_in_jsonl_skipped(tmp_path):
    """iter_jsonl tolerates blank lines in the filter output."""
    rows = _allowed_rows([100 + i for i in range(MIN_N)])
    in_path = tmp_path / "filter.jsonl"
    out_path = tmp_path / "agg.jsonl"
    body = "\n".join(json.dumps(r) for r in rows)
    in_path.write_text("\n\n" + body + "\n\n", encoding="utf-8")
    n = aggregate_jsonl(str(in_path), str(out_path))
    assert n == 1
