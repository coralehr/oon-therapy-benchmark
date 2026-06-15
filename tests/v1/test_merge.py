"""Unit tests for v1 stage 3 — the MERGE step (``oon_bench/merge.py``).

The merge joins stage-2 TiC percentiles onto the v0 Medicare locality grid and picks
a ``basis`` per row by the CONTRACT precedence:

    tic_oon_actual  (allowed, n>=MIN_N)
       >  tic_innetwork_proxy (negotiated, n>=MIN_N)
       >  medicare_multiple   (low = medicare, high = round(2x))

These tests build a TINY baseline-like CSV fixture and a handful of AggregateRecords
in-memory, run ``merge`` into a temp directory, and assert:

  * tic_oon_actual wins over a same-state proxy, which wins over medicare_multiple;
  * a state with allowed-data gets tic_oon_actual on EVERY one of its localities;
  * a state with only negotiated-data gets tic_innetwork_proxy on all its localities;
  * a state with neither gets medicare_multiple with low == medicare, high == round(2x);
  * the OON column mapping (p25/p50/p75/p90 -> low/mid/high/p90) is correct;
  * payer_scope is "single" for one payer and "multi" for several;
  * the MIN_N gate drops under-powered groups (they fall back to medicare);
  * the per-basis meta counts add up to the total row count.

Stdlib + pytest only. Nothing here downloads or reads real payer data.
"""
import csv
import os

import pytest

from oon_bench import merge as merge_mod
from oon_bench.merge import MEDICARE_OON_MULT_HIGH, MIN_N, AggregateRecord, merge

SNAPSHOT = "2026-06-08"

# ── A tiny baseline-like grid: 3 codes x a few localities across 4 states. ──
# Columns mirror data/therapy_oon_benchmark_v0_by_locality.csv exactly. We deliberately
# give CA two localities so "all of a state's localities get the same basis" is testable,
# and put a comma in a service_label so the csv-quoting path is exercised on write+read.
_BASELINE_ROWS = [
    # cpt, label, status, state, locality, medicare, snapshot, method_version
    ("90837", "Individual therapy, 60 minutes", "A", "CA", "LOS ANGELES", "179.29"),
    ("90837", "Individual therapy, 60 minutes", "A", "CA", "SAN DIEGO", "180.50"),
    ("90837", "Individual therapy, 60 minutes", "A", "TX", "TEXAS", "165.00"),
    ("90837", "Individual therapy, 60 minutes", "A", "NY", "MANHATTAN", "190.00"),
    ("90837", "Individual therapy, 60 minutes", "A", "FL", "FLORIDA", "170.00"),
    ("90834", "Individual therapy, 45 minutes", "A", "CA", "LOS ANGELES", "120.00"),
    ("90834", "Individual therapy, 45 minutes", "A", "TX", "TEXAS", "110.00"),
    ("90791", "Diagnostic intake / first evaluation", "A", "CA", "LOS ANGELES", "186.51"),
    ("90791", "Diagnostic intake / first evaluation", "A", "TX", "TEXAS", "167.00"),
]

_V0_HEADER = [
    "cpt_code",
    "service_label",
    "medicare_status",
    "state",
    "locality_name",
    "medicare_nonfacility_usd",
    "oon_estimate_low_usd",
    "oon_estimate_high_usd",
    "basis",
    "snapshot_date",
    "methodology_version",
]


@pytest.fixture()
def baseline_csv(tmp_path):
    """Write a v0-shaped by-locality CSV fixture and return its path."""
    path = tmp_path / "therapy_oon_benchmark_v0_by_locality.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_V0_HEADER)
        for cpt, label, status, state, loc, med in _BASELINE_ROWS:
            low = f"{float(med):.2f}"
            high = f"{float(med) * 2:.2f}"
            w.writerow(
                [cpt, label, status, state, loc, med, low, high, "medicare_multiple",
                 "2026-06-07", "v0-medicare-2026A"]
            )
    return str(path)


def _agg(cpt, region, kind, payer, n, p25, p50, p75, p90):
    """Build an AggregateRecord with sensible min/max derived from the percentiles."""
    return AggregateRecord(
        cpt_code=cpt,
        region=region,
        amount_kind=kind,
        payer=payer,
        n_obs=n,
        p25=p25,
        p50=p50,
        p75=p75,
        p90=p90,
        min=round(p25 * 0.8, 2),
        max=round(p90 * 1.2, 2),
        snapshot_date=SNAPSHOT,
    )


def _rows_for(result, cpt, state):
    return [
        r
        for r in result["by_locality_rows"]
        if r["cpt_code"] == cpt and r["state"] == state
    ]


# ─────────────────────────────────────────────────────────────────────────────────
# Precedence: tic_oon_actual > tic_innetwork_proxy > medicare_multiple
# ─────────────────────────────────────────────────────────────────────────────────
class TestBasisPrecedence:
    def test_allowed_beats_negotiated_in_same_state(self, baseline_csv, tmp_path):
        # 90837/CA has BOTH allowed (n>=MIN_N) and negotiated (n>=MIN_N). Allowed wins.
        records = [
            _agg("90837", "CA", "allowed", "aetna", MIN_N, 200.0, 230.0, 260.0, 300.0),
            _agg("90837", "CA", "negotiated", "aetna", 50, 150.0, 160.0, 170.0, 185.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        ca_rows = _rows_for(result, "90837", "CA")
        assert ca_rows, "expected CA localities for 90837"
        for r in ca_rows:
            assert r["basis"] == "tic_oon_actual"
            # allowed percentiles, not the negotiated ones
            assert r["oon_low_usd"] == 200.0
            assert r["oon_high_usd"] == 260.0

    def test_negotiated_used_when_no_allowed(self, baseline_csv, tmp_path):
        # 90837/TX has only negotiated -> proxy.
        records = [
            _agg("90837", "TX", "negotiated", "cigna", 30, 140.0, 150.0, 160.0, 175.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        tx_rows = _rows_for(result, "90837", "TX")
        assert tx_rows
        for r in tx_rows:
            assert r["basis"] == "tic_innetwork_proxy"
            assert r["oon_low_usd"] == 140.0
            assert r["oon_high_usd"] == 160.0

    def test_medicare_fallback_when_no_tic(self, baseline_csv, tmp_path):
        # 90837/NY and /FL get NO TiC at all -> medicare_multiple.
        records = [
            _agg("90837", "CA", "allowed", "aetna", MIN_N, 200.0, 230.0, 260.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        ny_rows = _rows_for(result, "90837", "NY")
        assert ny_rows
        for r in ny_rows:
            assert r["basis"] == "medicare_multiple"
            med = r["medicare_nonfacility_usd"]
            assert r["oon_low_usd"] == round(med, 2)
            assert r["oon_high_usd"] == round(med * MEDICARE_OON_MULT_HIGH, 2)
            # fallback rows carry no measured percentiles
            assert r["oon_mid_usd"] is None
            assert r["oon_p90_usd"] is None
            assert r["oon_obs_n"] is None
            assert r["payer_scope"] is None

    def test_full_precedence_chain_one_run(self, baseline_csv, tmp_path):
        """One merge where CA=allowed, TX=negotiated, NY/FL=fallback — all three bases."""
        records = [
            _agg("90837", "CA", "allowed", "aetna", MIN_N, 200.0, 230.0, 260.0, 300.0),
            _agg("90837", "TX", "negotiated", "cigna", 30, 140.0, 150.0, 160.0, 175.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        bases = {
            (r["state"]): r["basis"]
            for r in _rows_for_code(result, "90837")
        }
        assert bases["CA"] == "tic_oon_actual"
        assert bases["TX"] == "tic_innetwork_proxy"
        assert bases["NY"] == "medicare_multiple"
        assert bases["FL"] == "medicare_multiple"


def _rows_for_code(result, cpt):
    return [r for r in result["by_locality_rows"] if r["cpt_code"] == cpt]


# ─────────────────────────────────────────────────────────────────────────────────
# State-level fan-out: a state's percentiles attach to ALL its localities.
# ─────────────────────────────────────────────────────────────────────────────────
class TestStateFanOut:
    def test_allowed_state_applies_to_all_localities(self, baseline_csv, tmp_path):
        # CA has two 90837 localities (LOS ANGELES, SAN DIEGO). One state-level allowed
        # record must stamp BOTH localities identically with tic_oon_actual.
        records = [
            _agg("90837", "CA", "allowed", "aetna", 25, 200.0, 230.0, 260.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        ca_rows = _rows_for(result, "90837", "CA")
        assert {r["locality_name"] for r in ca_rows} == {"LOS ANGELES", "SAN DIEGO"}
        for r in ca_rows:
            assert r["basis"] == "tic_oon_actual"
            assert r["oon_low_usd"] == 200.0
            assert r["oon_mid_usd"] == 230.0
            assert r["oon_high_usd"] == 260.0
            assert r["oon_p90_usd"] == 300.0
            assert r["oon_obs_n"] == 25
        # Medicare differs between the two localities -> it is the v0 value, untouched.
        meds = {r["locality_name"]: r["medicare_nonfacility_usd"] for r in ca_rows}
        assert meds["LOS ANGELES"] == 179.29
        assert meds["SAN DIEGO"] == 180.50

    def test_negotiated_only_state_applies_proxy_to_all_localities(self, baseline_csv,
                                                                   tmp_path):
        records = [
            _agg("90837", "CA", "negotiated", "uhc", 40, 150.0, 165.0, 180.0, 200.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        ca_rows = _rows_for(result, "90837", "CA")
        assert len(ca_rows) == 2
        for r in ca_rows:
            assert r["basis"] == "tic_innetwork_proxy"
            assert r["oon_low_usd"] == 150.0
            assert r["oon_high_usd"] == 180.0


# ─────────────────────────────────────────────────────────────────────────────────
# OON column mapping + payer_scope
# ─────────────────────────────────────────────────────────────────────────────────
class TestColumnMappingAndScope:
    def test_percentile_to_column_mapping(self, baseline_csv, tmp_path):
        records = [
            _agg("90837", "CA", "allowed", "aetna", 15, 201.0, 222.0, 243.0, 264.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        r = _rows_for(result, "90837", "CA")[0]
        assert r["oon_low_usd"] == 201.0   # p25
        assert r["oon_mid_usd"] == 222.0   # p50
        assert r["oon_high_usd"] == 243.0  # p75
        assert r["oon_p90_usd"] == 264.0   # p90

    def test_payer_scope_single(self, baseline_csv, tmp_path):
        records = [
            _agg("90837", "CA", "allowed", "aetna", 20, 200.0, 230.0, 260.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        r = _rows_for(result, "90837", "CA")[0]
        assert r["payer_scope"] == "single"
        assert r["oon_obs_n"] == 20

    def test_payer_scope_multi_and_n_summed(self, baseline_csv, tmp_path):
        # Two payers contribute to CA allowed -> multi; n_obs is the SUM.
        records = [
            _agg("90837", "CA", "allowed", "aetna", 12, 200.0, 220.0, 240.0, 280.0),
            _agg("90837", "CA", "allowed", "cigna", 8, 210.0, 230.0, 250.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        r = _rows_for(result, "90837", "CA")[0]
        assert r["payer_scope"] == "multi"
        assert r["oon_obs_n"] == 20  # 12 + 8
        # n-weighted p25: (200*12 + 210*8)/20 = (2400 + 1680)/20 = 204.0
        assert r["oon_low_usd"] == pytest.approx(204.0)


# ─────────────────────────────────────────────────────────────────────────────────
# MIN_N gate
# ─────────────────────────────────────────────────────────────────────────────────
class TestMinNGate:
    def test_under_min_n_falls_back_to_medicare(self, baseline_csv, tmp_path):
        # A single allowed record with n < MIN_N must NOT publish; falls back to medicare.
        records = [
            _agg("90837", "CA", "allowed", "aetna", MIN_N - 1, 200.0, 230.0, 260.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        for r in _rows_for(result, "90837", "CA"):
            assert r["basis"] == "medicare_multiple"

    def test_pooled_n_can_clear_min_n(self, baseline_csv, tmp_path):
        # Two payers each under MIN_N but together >= MIN_N -> published as multi.
        records = [
            _agg("90837", "CA", "allowed", "aetna", 6, 200.0, 220.0, 240.0, 280.0),
            _agg("90837", "CA", "allowed", "cigna", 5, 210.0, 230.0, 250.0, 300.0),
        ]
        assert 6 + 5 >= MIN_N
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        r = _rows_for(result, "90837", "CA")[0]
        assert r["basis"] == "tic_oon_actual"
        assert r["oon_obs_n"] == 11
        assert r["payer_scope"] == "multi"


# ─────────────────────────────────────────────────────────────────────────────────
# Meta counts add up; per-code independence; output files written.
# ─────────────────────────────────────────────────────────────────────────────────
class TestMetaAndOutputs:
    def test_basis_counts_sum_to_total_rows(self, baseline_csv, tmp_path):
        records = [
            _agg("90837", "CA", "allowed", "aetna", 20, 200.0, 230.0, 260.0, 300.0),
            _agg("90837", "TX", "negotiated", "cigna", 30, 140.0, 150.0, 160.0, 175.0),
            _agg("90834", "CA", "allowed", "uhc", 15, 130.0, 140.0, 150.0, 165.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        counts = result["meta"]["basis_counts_by_locality"]
        total = sum(counts.values())
        assert total == len(result["by_locality_rows"]) == len(_BASELINE_ROWS)

    def test_counts_match_actual_row_bases(self, baseline_csv, tmp_path):
        records = [
            _agg("90837", "CA", "allowed", "aetna", 20, 200.0, 230.0, 260.0, 300.0),
            _agg("90837", "TX", "negotiated", "cigna", 30, 140.0, 150.0, 160.0, 175.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        counts = result["meta"]["basis_counts_by_locality"]
        actual = {"tic_oon_actual": 0, "tic_innetwork_proxy": 0, "medicare_multiple": 0}
        for r in result["by_locality_rows"]:
            actual[r["basis"]] += 1
        assert counts == actual
        # 90837/CA = 2 localities allowed; 90837/TX = 1 negotiated; everything else medicare.
        assert counts["tic_oon_actual"] == 2
        assert counts["tic_innetwork_proxy"] == 1
        assert counts["medicare_multiple"] == len(_BASELINE_ROWS) - 3

    def test_per_code_independence(self, baseline_csv, tmp_path):
        # TiC for 90837/CA must NOT leak onto 90834/CA or 90791/CA.
        records = [
            _agg("90837", "CA", "allowed", "aetna", 20, 200.0, 230.0, 260.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        for r in _rows_for(result, "90834", "CA"):
            assert r["basis"] == "medicare_multiple"
        for r in _rows_for(result, "90791", "CA"):
            assert r["basis"] == "medicare_multiple"

    def test_output_files_written(self, baseline_csv, tmp_path):
        out_dir = tmp_path / "out"
        records = [
            _agg("90837", "CA", "allowed", "aetna", 20, 200.0, 230.0, 260.0, 300.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(out_dir),
                       snapshot_date=SNAPSHOT)
        for key in ("by_locality_csv", "national_csv", "json"):
            assert os.path.isfile(result["paths"][key])
        # by-locality CSV header carries the v0 columns + the v1 extras.
        with open(result["paths"]["by_locality_csv"], newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        for col in ("cpt_code", "basis", "oon_low_usd", "oon_high_usd",
                    "oon_mid_usd", "oon_p90_usd", "oon_obs_n", "payer_scope"):
            assert col in header

    def test_methodology_version_bumped(self, baseline_csv, tmp_path):
        result = merge([], baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        assert result["meta"]["methodology_version"] == "v1-tic-2026A"
        for r in result["by_locality_rows"]:
            assert r["methodology_version"] == "v1-tic-2026A"


# ─────────────────────────────────────────────────────────────────────────────────
# National output (region "US")
# ─────────────────────────────────────────────────────────────────────────────────
class TestNational:
    def test_national_uses_us_tic_when_present(self, baseline_csv, tmp_path):
        records = [
            _agg("90837", "US", "allowed", "aetna", 50, 205.0, 235.0, 265.0, 305.0),
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        nat = {r["cpt_code"]: r for r in result["national_rows"]}
        assert nat["90837"]["basis"] == "tic_oon_actual"
        assert nat["90837"]["oon_low_usd"] == 205.0
        assert nat["90837"]["oon_high_usd"] == 265.0

    def test_national_falls_back_to_medicare(self, baseline_csv, tmp_path):
        # No US-level TiC -> national row is medicare_multiple. (Fixture has no v0
        # national CSV, so the national medicare anchor is the per-code mean fallback;
        # the band relationship low == medicare, high == 2x must still hold.)
        result = merge([], baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        nat = {r["cpt_code"]: r for r in result["national_rows"]}
        assert nat["90837"]["basis"] == "medicare_multiple"
        med = nat["90837"]["medicare_nonfacility_usd"]
        assert nat["90837"]["oon_low_usd"] == round(med, 2)
        assert nat["90837"]["oon_high_usd"] == round(med * MEDICARE_OON_MULT_HIGH, 2)


# ─────────────────────────────────────────────────────────────────────────────────
# AggregateRecord plumbing + dict input
# ─────────────────────────────────────────────────────────────────────────────────
class TestAggregateRecordInput:
    def test_merge_accepts_plain_dicts(self, baseline_csv, tmp_path):
        # The aggregate stage serializes dicts (CONTRACT shape 2); merge must accept them.
        records = [
            {
                "cpt_code": "90837", "region": "CA", "amount_kind": "allowed",
                "payer": "aetna", "n_obs": 20, "p25": 200.0, "p50": 230.0,
                "p75": 260.0, "p90": 300.0, "min": 160.0, "max": 360.0,
                "snapshot_date": SNAPSHOT,
            }
        ]
        result = merge(records, baseline_csv=baseline_csv, out_dir=str(tmp_path / "out"),
                       snapshot_date=SNAPSHOT)
        r = _rows_for(result, "90837", "CA")[0]
        assert r["basis"] == "tic_oon_actual"
        assert r["oon_low_usd"] == 200.0

    def test_from_dict_roundtrip(self):
        d = {
            "cpt_code": "90834", "region": "TX", "amount_kind": "negotiated",
            "payer": "cigna", "n_obs": 12, "p25": 100.0, "p50": 110.0,
            "p75": 120.0, "p90": 135.0, "min": 80.0, "max": 150.0,
            "snapshot_date": SNAPSHOT,
        }
        rec = AggregateRecord.from_dict(d)
        assert rec.cpt_code == "90834"
        assert rec.region == "TX"
        assert rec.amount_kind == "negotiated"
        assert rec.n_obs == 12
        assert rec.p75 == 120.0


# ─────────────────────────────────────────────────────────────────────────────────
# Module constants sanity
# ─────────────────────────────────────────────────────────────────────────────────
class TestConstants:
    def test_min_n_value(self):
        assert MIN_N == 10

    def test_medicare_high_multiplier(self):
        assert MEDICARE_OON_MULT_HIGH == 2.0

    def test_basis_strings(self):
        assert merge_mod.BASIS_TIC_OON == "tic_oon_actual"
        assert merge_mod.BASIS_TIC_PROXY == "tic_innetwork_proxy"
        assert merge_mod.BASIS_MEDICARE == "medicare_multiple"
