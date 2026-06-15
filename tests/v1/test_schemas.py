"""Unit tests for ``oon_bench.schemas`` — the shared v1 contract module.

Two halves:

  * The percentile math (the single source of truth every stage imports). We
    pin known inputs to known type-7 / linear-interpolation quantiles, covering
    n == 1, odd n, even n, duplicates, input-order independence, and rounding.
  * JSONL (de)serialization round-trips for the typed row dataclasses
    (FilterRow / AggregateRecord / MergedRow / QueryResult) plus the dict
    fast-path, so the wire format is byte-stable and forward-compatible.

Stdlib + pytest only. The repo-root sys.path insertion in tests/v1/conftest.py
(and tests/conftest.py) makes ``import oon_bench`` / ``import therapy_codes``
resolve to the package at the repo root.
"""

from __future__ import annotations

import io

import pytest

import therapy_codes
from oon_bench import schemas
from oon_bench.schemas import (
    BASES,
    BASIS_PRECEDENCE,
    MIN_N,
    AggregateRecord,
    Estimate,
    FilterRow,
    MergedRow,
    QueryResult,
    dump_jsonl,
    from_jsonl_line,
    iter_jsonl_stream,
    label_for,
    percentile,
    percentiles,
    to_jsonl_line,
)


# --------------------------------------------------------------------------- #
# Contract constants
# --------------------------------------------------------------------------- #
class TestConstants:
    def test_min_n_is_ten(self):
        assert MIN_N == 10

    def test_basis_precedence_order(self):
        # Strongest first: real OON > in-network proxy > Medicare fallback.
        assert BASIS_PRECEDENCE == (
            "tic_oon_actual",
            "tic_innetwork_proxy",
            "medicare_multiple",
        )

    def test_bases_is_membership_set_of_precedence(self):
        assert BASES == set(BASIS_PRECEDENCE)
        assert "tic_oon_actual" in BASES
        assert "medicare_multiple" in BASES
        assert "nonsense" not in BASES

    def test_amount_kind_to_basis_mapping(self):
        assert schemas.AMOUNT_KIND_TO_BASIS["allowed"] == "tic_oon_actual"
        assert schemas.AMOUNT_KIND_TO_BASIS["negotiated"] == "tic_innetwork_proxy"


# --------------------------------------------------------------------------- #
# label_for — our plain-language labels, never AMA descriptors
# --------------------------------------------------------------------------- #
class TestLabelFor:
    def test_known_codes_resolve_to_our_labels(self):
        assert label_for("90837") == "Individual therapy, 60 minutes"
        assert label_for("90791") == "Diagnostic intake / first evaluation"
        assert label_for("90853") == "Group therapy session"

    def test_whitespace_is_stripped(self):
        assert label_for("  90837 ") == "Individual therapy, 60 minutes"

    def test_unknown_code_returns_none(self):
        assert label_for("99999") is None
        assert label_for("") is None

    def test_every_therapy_code_has_a_label(self):
        for c in therapy_codes.THERAPY_CODES:
            assert label_for(c["code"]) == c["label"]


# --------------------------------------------------------------------------- #
# percentiles(values) -> {"p25","p50","p75","p90","min","max"}
#
# Type-7 / linear interpolation. For a sorted list of length n and probability
# q in [0,1]: rank = q*(n-1); result = v[floor] + frac*(v[floor+1]-v[floor]).
# All values rounded to the cent.
# --------------------------------------------------------------------------- #
class TestPercentilesSummary:
    def test_returns_exactly_the_contract_keys(self):
        out = percentiles([10, 20, 30, 40, 50])
        assert set(out) == {"p25", "p50", "p75", "p90", "min", "max"}

    def test_n_equals_1(self):
        # Every quantile (and min/max) collapses to the single value.
        out = percentiles([142.50])
        assert out == {
            "p25": 142.50,
            "p50": 142.50,
            "p75": 142.50,
            "p90": 142.50,
            "min": 142.50,
            "max": 142.50,
        }

    def test_n_equals_2(self):
        # rank = q*1. p25=0.25, p50=0.5, p75=0.75, p90=0.9 across [50,150].
        out = percentiles([50, 150])
        assert out == {
            "p25": 75.00,
            "p50": 100.00,
            "p75": 125.00,
            "p90": 140.00,
            "min": 50.00,
            "max": 150.00,
        }

    def test_odd_n_known_quantiles(self):
        # n=5, ranks 1.0/2.0/3.0/3.6 over [10,20,30,40,50].
        out = percentiles([10, 20, 30, 40, 50])
        assert out == {
            "p25": 20.00,
            "p50": 30.00,
            "p75": 40.00,
            "p90": 46.00,  # 40 + (50-40)*0.6
            "min": 10.00,
            "max": 50.00,
        }

    def test_even_n_known_quantiles(self):
        # n=4, ranks 0.75/1.5/2.25/2.7 over [10,20,30,40].
        out = percentiles([10, 20, 30, 40])
        assert out == {
            "p25": 17.50,  # 10 + 10*0.75
            "p50": 25.00,  # 20 + 10*0.5
            "p75": 32.50,  # 30 + 10*0.25
            "p90": 37.00,  # 30 + 10*0.7
            "min": 10.00,
            "max": 40.00,
        }

    def test_all_duplicates(self):
        out = percentiles([100, 100, 100, 100])
        assert out == {
            "p25": 100.00,
            "p50": 100.00,
            "p75": 100.00,
            "p90": 100.00,
            "min": 100.00,
            "max": 100.00,
        }

    def test_input_order_does_not_matter(self):
        shuffled = percentiles([40, 10, 50, 20, 30])
        ordered = percentiles([10, 20, 30, 40, 50])
        assert shuffled == ordered

    def test_rounds_to_the_cent(self):
        # [1, 2]: p25 rank 0.25 -> 1 + 1*0.25 = 1.25 (exact at 2dp).
        out = percentiles([1, 2])
        assert out["p25"] == 1.25
        assert out["p50"] == 1.5
        assert out["p75"] == 1.75
        assert out["p90"] == 1.9
        # A case that genuinely needs rounding: [0, 1, 2] p90 -> rank 1.8 ->
        # 1 + (2-1)*0.8 = 1.8.
        out2 = percentiles([0, 1, 2])
        assert out2["p90"] == 1.8

    def test_monotonic_non_decreasing(self):
        out = percentiles([5, 17, 23, 41, 88, 90, 120])
        assert out["min"] <= out["p25"] <= out["p50"] <= out["p75"] <= out["p90"] <= out["max"]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            percentiles([])

    def test_accepts_any_iterable(self):
        # A generator (single-pass iterable) must still work.
        out = percentiles(x for x in [10, 20, 30, 40, 50])
        assert out["p50"] == 30.00


# --------------------------------------------------------------------------- #
# percentile(sorted_values, pct) — the singular primitive percentiles() builds on
# --------------------------------------------------------------------------- #
class TestPercentilePrimitive:
    def test_requires_sorted_nonempty(self):
        with pytest.raises(ValueError):
            percentile([], 50)

    def test_single_value(self):
        assert percentile([42.0], 25) == 42.0
        assert percentile([42.0], 90) == 42.0

    def test_endpoints_are_min_and_max(self):
        v = [10.0, 20.0, 30.0, 40.0]
        assert percentile(v, 0) == 10.0
        assert percentile(v, 100) == 40.0

    def test_interpolates_between_points(self):
        # median of [10,20,30,40] = 25 via linear interpolation.
        assert percentile([10.0, 20.0, 30.0, 40.0], 50) == 25.0


# --------------------------------------------------------------------------- #
# JSONL round-trips — FilterRow (contract 1)
# --------------------------------------------------------------------------- #
class TestFilterRowJsonl:
    def _row(self) -> FilterRow:
        return FilterRow(
            billing_code="90837",
            amount=142.50,
            amount_kind="allowed",
            source_file="aetna_oon_TX.json.gz",
            payer="aetna",
            billing_code_type="CPT",
            negotiation_arrangement=None,
            billing_class="professional",
            region="TX",
            provider_tin="123456789",
        )

    def test_round_trip_preserves_all_fields(self):
        row = self._row()
        line = to_jsonl_line(row)
        back = from_jsonl_line(line, FilterRow)
        assert back == row

    def test_line_is_compact_single_line(self):
        line = to_jsonl_line(self._row())
        assert "\n" not in line
        assert ", " not in line  # compact separators (no space after comma)

    def test_from_dict_ignores_unknown_keys(self):
        d = self._row().to_dict()
        d["some_future_field"] = "ignored"
        back = FilterRow.from_dict(d)
        assert back == self._row()

    def test_from_dict_supplies_optional_defaults(self):
        # Only the required fields present; optionals take their defaults.
        minimal = {
            "billing_code": "90834",
            "amount": 110.25,
            "amount_kind": "negotiated",
            "source_file": "f.json",
            "payer": "cigna",
        }
        row = FilterRow.from_dict(minimal)
        assert row.billing_code_type == "CPT"
        assert row.region is None
        assert row.provider_tin is None
        assert row.billing_class is None

    def test_missing_required_field_raises(self):
        with pytest.raises(TypeError):
            FilterRow.from_dict({"billing_code": "90837"})  # no amount/kind/...

    def test_dict_passthrough_serialization(self):
        # A plain dict (the aggregate fast-path shape) serializes too.
        d = self._row().to_dict()
        line = to_jsonl_line(d)
        assert from_jsonl_line(line) == d


# --------------------------------------------------------------------------- #
# JSONL round-trips — AggregateRecord (contract 2)
# --------------------------------------------------------------------------- #
class TestAggregateRecordJsonl:
    def _rec(self) -> AggregateRecord:
        return AggregateRecord(
            cpt_code="90837",
            region="US",
            amount_kind="allowed",
            payer="uhc",
            n_obs=4200,
            p25=150.0,
            p50=185.0,
            p75=210.0,
            p90=240.0,
            min=80.0,
            max=600.0,
            snapshot_date="2026-06-07",
        )

    def test_round_trip(self):
        rec = self._rec()
        back = from_jsonl_line(to_jsonl_line(rec), AggregateRecord)
        assert back == rec

    def test_from_percentiles_builds_record(self):
        pct = percentiles([100, 150, 185, 210, 240, 600, 80, 120, 175, 195, 205])
        rec = AggregateRecord.from_percentiles(
            cpt_code="90837",
            region="US",
            amount_kind="allowed",
            payer="uhc",
            n_obs=11,
            pct=pct,
            snapshot_date="2026-06-07",
        )
        assert rec.p25 == pct["p25"]
        assert rec.p50 == pct["p50"]
        assert rec.p90 == pct["p90"]
        assert rec.min == pct["min"]
        assert rec.max == pct["max"]
        assert rec.n_obs == 11

    def test_basis_property_maps_from_amount_kind(self):
        assert self._rec().basis == "tic_oon_actual"
        neg = AggregateRecord.from_dict({**self._rec().to_dict(), "amount_kind": "negotiated"})
        assert neg.basis == "tic_innetwork_proxy"


# --------------------------------------------------------------------------- #
# JSONL round-trips — MergedRow (contract 3) + QueryResult (contract 4)
# --------------------------------------------------------------------------- #
class TestMergedRowJsonl:
    def _row(self) -> MergedRow:
        return MergedRow(
            cpt_code="90837",
            service_label="Individual therapy, 60 minutes",
            medicare_status="A",
            state="CA",
            locality_name="LOS ANGELES",
            medicare_nonfacility_usd=179.29,
            snapshot_date="2026-06-07",
            methodology_version="v1-tic-2026A",
            basis="tic_oon_actual",
            oon_low_usd=165.0,
            oon_high_usd=230.0,
            oon_mid_usd=198.0,
            oon_p90_usd=265.0,
            oon_obs_n=512,
            payer_scope="multi",
        )

    def test_round_trip(self):
        row = self._row()
        assert from_jsonl_line(to_jsonl_line(row), MergedRow) == row

    def test_fallback_row_has_none_percentiles(self):
        row = MergedRow.from_dict(
            {
                "cpt_code": "90791",
                "service_label": "Diagnostic intake / first evaluation",
                "medicare_status": "A",
                "state": "AL",
                "locality_name": "ALABAMA",
                "medicare_nonfacility_usd": 167.51,
                "snapshot_date": "2026-06-07",
                "methodology_version": "v1-tic-2026A",
                "basis": "medicare_multiple",
                "oon_low_usd": 167.51,
                "oon_high_usd": 335.02,
            }
        )
        assert row.oon_mid_usd is None
        assert row.oon_p90_usd is None
        assert row.oon_obs_n is None
        assert row.payer_scope is None

    def test_merged_csv_columns_v0_first(self):
        cols = schemas.MERGED_CSV_COLUMNS
        # v0 columns lead (back-compat), then the v1 additive columns.
        assert cols[:8] == (
            "cpt_code",
            "service_label",
            "medicare_status",
            "state",
            "locality_name",
            "medicare_nonfacility_usd",
            "snapshot_date",
            "methodology_version",
        )
        assert "basis" in cols
        assert "oon_obs_n" in cols
        assert "payer_scope" in cols


class TestQueryResultJsonl:
    def _result(self) -> QueryResult:
        return QueryResult(
            cpt_code="90837",
            service_label="Individual therapy, 60 minutes",
            region="CA",
            basis="tic_oon_actual",
            estimate=Estimate(low=165.0, high=230.0, mid=198.0),
            confidence="high",
            source="CMS PFS RVU26A; TiC uhc+aetna+cigna",
            snapshot_date="2026-06-07",
            disclaimer="Estimate only, not a guarantee.",
            n_obs=512,
        )

    def test_to_dict_nests_estimate(self):
        d = self._result().to_dict()
        assert d["estimate"] == {"low": 165.0, "high": 230.0, "mid": 198.0}

    def test_round_trip_rehydrates_nested_estimate(self):
        res = self._result()
        back = from_jsonl_line(to_jsonl_line(res), QueryResult)
        assert isinstance(back.estimate, Estimate)
        assert back == res

    def test_fallback_estimate_mid_is_none(self):
        res = QueryResult(
            cpt_code="90791",
            service_label="Diagnostic intake / first evaluation",
            region="AL",
            basis="medicare_multiple",
            estimate=Estimate(low=167.51, high=335.02),
            confidence="low",
            source="CMS PFS RVU26A",
            snapshot_date="2026-06-07",
            disclaimer="Estimate only.",
        )
        d = res.to_dict()
        assert d["estimate"]["mid"] is None
        assert d["n_obs"] is None


# --------------------------------------------------------------------------- #
# Stream helpers — iter_jsonl_stream / dump_jsonl
# --------------------------------------------------------------------------- #
class TestStreamHelpers:
    def test_dump_then_iter_round_trips_many_rows(self):
        rows = [
            FilterRow(
                billing_code="90837",
                amount=float(a),
                amount_kind="allowed",
                source_file="f.json",
                payer="aetna",
            )
            for a in (120, 150, 185)
        ]
        buf = io.StringIO()
        n = dump_jsonl(rows, buf)
        assert n == 3

        buf.seek(0)
        back = list(iter_jsonl_stream(buf, FilterRow))
        assert back == rows

    def test_iter_skips_blank_lines(self):
        text = (
            to_jsonl_line({"a": 1})
            + "\n\n"  # a stray blank line
            + to_jsonl_line({"a": 2})
            + "\n"
        )
        parsed = list(iter_jsonl_stream(io.StringIO(text)))
        assert parsed == [{"a": 1}, {"a": 2}]

    def test_from_jsonl_line_blank_raises(self):
        with pytest.raises(ValueError):
            from_jsonl_line("   \n")
