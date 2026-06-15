"""Unit tests for the v0 pipeline in ``build_baseline.py``.

Covers the pure helpers (``_fnum``, ``national_rate``, ``locality_rate``,
``oon_band``) and the two CMS parsers (``load_rvus``, ``load_gpci``) which read
the committed raw fixtures under ``data/raw/``.

GOLDEN VALUES are hand-derived from the CMS source files and the documented
formula::

    allowed = (work*pw_gpci_floored + pe_nonfac*pe_gpci + mp*mp_gpci) * CF
    national = total_nonfac * CF        (GPCI == 1.0)

Conversion factor (2026 non-QPP) = 33.4009.

Stdlib + pytest only.
"""
import os

import pytest

import build_baseline as bb
import therapy_codes

CF = 33.4009

# Expected component RVUs straight from PPRRVU2026_Jan_nonQPP.csv (status, work,
# pe_nonfac, mp, total_nonfac). Used to validate load_rvus() parses the correct
# positional columns and to derive locality golden values independently.
EXPECTED_RVUS = {
    "90791": {"status": "A", "work": 3.84, "pe_nonfac": 1.33, "mp": 0.02, "total_nonfac": 5.19},
    "90792": {"status": "A", "work": 4.16, "pe_nonfac": 1.72, "mp": 0.17, "total_nonfac": 6.05},
    "90832": {"status": "A", "work": 1.94, "pe_nonfac": 0.62, "mp": 0.01, "total_nonfac": 2.57},
    "90834": {"status": "A", "work": 2.56, "pe_nonfac": 0.83, "mp": 0.02, "total_nonfac": 3.41},
    "90837": {"status": "A", "work": 3.78, "pe_nonfac": 1.20, "mp": 0.02, "total_nonfac": 5.00},
    "90839": {"status": "A", "work": 3.58, "pe_nonfac": 1.19, "mp": 0.03, "total_nonfac": 4.80},
    "90840": {"status": "A", "work": 1.71, "pe_nonfac": 0.58, "mp": 0.02, "total_nonfac": 2.31},
    "90846": {"status": "R", "work": 2.74, "pe_nonfac": 0.40, "mp": 0.03, "total_nonfac": 3.17},
    "90847": {"status": "A", "work": 2.86, "pe_nonfac": 0.40, "mp": 0.02, "total_nonfac": 3.28},
    "90853": {"status": "A", "work": 0.67, "pe_nonfac": 0.23, "mp": 0.01, "total_nonfac": 0.91},
    "96127": {"status": "A", "work": 0.00, "pe_nonfac": 0.14, "mp": 0.01, "total_nonfac": 0.15},
    # v0.1 testing block + add-ons.
    "90785": {"status": "A", "work": 0.33, "pe_nonfac": 0.11, "mp": 0.00, "total_nonfac": 0.44},
    "90845": {"status": "A", "work": 2.40, "pe_nonfac": 0.83, "mp": 0.04, "total_nonfac": 3.27},
    "96130": {"status": "A", "work": 2.56, "pe_nonfac": 1.06, "mp": 0.09, "total_nonfac": 3.71},
    "96131": {"status": "A", "work": 1.96, "pe_nonfac": 0.62, "mp": 0.01, "total_nonfac": 2.59},
    "96132": {"status": "A", "work": 2.56, "pe_nonfac": 1.03, "mp": 0.07, "total_nonfac": 3.66},
    "96133": {"status": "A", "work": 1.96, "pe_nonfac": 0.95, "mp": 0.02, "total_nonfac": 2.93},
    "96136": {"status": "A", "work": 0.55, "pe_nonfac": 0.74, "mp": 0.02, "total_nonfac": 1.31},
    "96137": {"status": "A", "work": 0.46, "pe_nonfac": 0.65, "mp": 0.00, "total_nonfac": 1.11},
}

RAW_PRESENT = os.path.exists(bb.PPRRVU) and os.path.exists(bb.GPCI)
requires_raw = pytest.mark.skipif(
    not RAW_PRESENT,
    reason="CMS raw files absent (data/raw/ is gitignored); run ./fetch_cms_data.sh first",
)


# --------------------------------------------------------------------------- #
# _fnum
# --------------------------------------------------------------------------- #
class TestFnum:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("33.4009", 33.4009),
            ("5.00", 5.0),
            ("0", 0.0),
            ("0.00", 0.0),
            (" 1.50 ", 1.5),  # surrounding whitespace stripped
            ("-2.5", -2.5),
            (3.84, 3.84),  # already numeric
            (7, 7.0),  # int
        ],
    )
    def test_parses_numbers(self, raw, expected):
        assert bb._fnum(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", "NA", "abc", None, "1.2.3", "$5"])
    def test_returns_none_on_blank_or_garbage(self, raw):
        assert bb._fnum(raw) is None

    def test_blank_then_real_value_distinct(self):
        # A blank field must not be coerced to 0.0 (that would silently corrupt math).
        assert bb._fnum("") is None
        assert bb._fnum("0") == 0.0


# --------------------------------------------------------------------------- #
# load_rvus
# --------------------------------------------------------------------------- #
@requires_raw
class TestLoadRvus:
    @pytest.fixture(scope="class")
    def rvus(self):
        return bb.load_rvus()

    def test_all_codes_resolved(self, rvus):
        wanted = {c["code"] for c in therapy_codes.THERAPY_CODES}
        assert set(rvus.keys()) == wanted
        assert len(rvus) == len(therapy_codes.THERAPY_CODES)

    @pytest.mark.parametrize("code", sorted(EXPECTED_RVUS))
    def test_component_columns_match_cms_source(self, rvus, code):
        exp = EXPECTED_RVUS[code]
        got = rvus[code]
        assert got["status"] == exp["status"]
        assert got["work"] == pytest.approx(exp["work"])
        assert got["pe_nonfac"] == pytest.approx(exp["pe_nonfac"])
        assert got["mp"] == pytest.approx(exp["mp"])
        assert got["total_nonfac"] == pytest.approx(exp["total_nonfac"])

    def test_conversion_factor_read_from_file(self, rvus):
        for code, rvu in rvus.items():
            assert rvu["cf"] == pytest.approx(CF), code

    def test_restricted_status_preserved(self, rvus):
        # 90846 is Medicare status 'R' (restricted) and must be carried, not dropped.
        assert rvus["90846"]["status"] == "R"

    def test_no_ama_descriptor_column_captured(self, rvus):
        # load_rvus reads positional columns and never r[2] (DESCRIPTION). The
        # parsed dict must not contain any free-text descriptor key/value.
        for rvu in rvus.values():
            assert set(rvu.keys()) == {"status", "work", "pe_nonfac", "mp", "total_nonfac", "cf"}
            for v in (rvu["work"], rvu["pe_nonfac"], rvu["mp"], rvu["total_nonfac"], rvu["cf"]):
                assert isinstance(v, float)


# --------------------------------------------------------------------------- #
# load_gpci
# --------------------------------------------------------------------------- #
@requires_raw
class TestLoadGpci:
    @pytest.fixture(scope="class")
    def locs(self):
        return bb.load_gpci()

    def test_returns_109_localities(self, locs):
        assert len(locs) == 109

    def test_every_record_has_required_fields(self, locs):
        for loc in locs:
            assert set(loc.keys()) == {
                "state",
                "locality_number",
                "locality_name",
                "pw_gpci",
                "pe_gpci",
                "mp_gpci",
            }
            assert len(loc["state"]) == 2
            assert isinstance(loc["pw_gpci"], float)
            assert isinstance(loc["pe_gpci"], float)
            assert isinstance(loc["mp_gpci"], float)

    def test_no_none_gpci_values(self, locs):
        for loc in locs:
            assert None not in (loc["pw_gpci"], loc["pe_gpci"], loc["mp_gpci"])

    def test_work_gpci_uses_floor_column(self, locs):
        # Alabama's PW GPCI is 0.988 unfloored but 1.000 with the 1.0 floor.
        # load_gpci must read the *floored* column (r[5]).
        al = next(loc for loc in locs if loc["state"] == "AL" and loc["locality_name"] == "ALABAMA")
        assert al["pw_gpci"] == pytest.approx(1.000)
        assert al["pe_gpci"] == pytest.approx(0.875)
        assert al["mp_gpci"] == pytest.approx(0.566)

    def test_known_locality_values(self, locs):
        la = next(
            loc
            for loc in locs
            if loc["state"] == "CA" and loc["locality_name"].startswith("LOS ANGELES")
        )
        assert la["pw_gpci"] == pytest.approx(1.041)
        assert la["pe_gpci"] == pytest.approx(1.183)
        assert la["mp_gpci"] == pytest.approx(0.664)

    def test_alaska_above_floor_not_clamped(self, locs):
        # Alaska PW GPCI is 1.500 -- a floor of 1.0 must not pull it down.
        ak = next(loc for loc in locs if loc["state"] == "AK")
        assert ak["pw_gpci"] == pytest.approx(1.500)


# --------------------------------------------------------------------------- #
# national_rate -- GOLDEN VALUES
# --------------------------------------------------------------------------- #
@requires_raw
class TestNationalRate:
    @pytest.fixture(scope="class")
    def rvus(self):
        return bb.load_rvus()

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("90837", 167.00),  # 5.00 * 33.4009 = 167.0045 -> 167.00
            ("90834", 113.90),  # 3.41 * 33.4009 = 113.897  -> 113.90
            ("90791", 173.35),  # 5.19 * 33.4009 = 173.351  -> 173.35
            ("90792", 202.08),
            ("90832", 85.84),
            ("90846", 105.88),
            ("90847", 109.55),
            ("90853", 30.39),
            ("90839", 160.32),
            ("90840", 77.16),
            ("96127", 5.01),
        ],
    )
    def test_golden_national_rates(self, rvus, code, expected):
        got = bb.national_rate(rvus[code])
        assert abs(got - expected) < 0.005, f"{code}: {got} != {expected}"

    def test_national_equals_total_nonfac_times_cf(self, rvus):
        for code, rvu in rvus.items():
            expected = round(rvu["total_nonfac"] * rvu["cf"], 2)
            assert bb.national_rate(rvu) == pytest.approx(expected), code

    def test_explicit_formula_90837(self):
        rvu = {"total_nonfac": 5.00, "cf": CF}
        assert bb.national_rate(rvu) == pytest.approx(167.00, abs=0.005)

    def test_result_is_rounded_to_cents(self, rvus):
        for rvu in rvus.values():
            val = bb.national_rate(rvu)
            assert round(val, 2) == val


# --------------------------------------------------------------------------- #
# locality_rate -- GOLDEN VALUES (component RVUs x a locality's GPCIs x CF)
# --------------------------------------------------------------------------- #
class TestLocalityRate:
    def _loc(self, state, name, pw, pe, mp):
        return {
            "state": state,
            "locality_number": "00",
            "locality_name": name,
            "pw_gpci": pw,
            "pe_gpci": pe,
            "mp_gpci": mp,
        }

    def test_golden_90837_alabama(self):
        rvu = {"work": 3.78, "pe_nonfac": 1.20, "mp": 0.02, "cf": CF}
        loc = self._loc("AL", "ALABAMA", 1.000, 0.875, 0.566)
        # (3.78*1.0 + 1.20*0.875 + 0.02*0.566)*33.4009 = 4.84132*33.4009 = 161.70
        assert bb.locality_rate(rvu, loc) == pytest.approx(161.70, abs=0.005)

    def test_golden_90837_los_angeles(self):
        rvu = {"work": 3.78, "pe_nonfac": 1.20, "mp": 0.02, "cf": CF}
        loc = self._loc("CA", "LOS ANGELES", 1.041, 1.183, 0.664)
        # (3.78*1.041 + 1.20*1.183 + 0.02*0.664)*33.4009 = 5.36786*33.4009 = 179.29
        assert bb.locality_rate(rvu, loc) == pytest.approx(179.29, abs=0.005)

    def test_golden_90791_alabama_matches_committed(self):
        # Cross-check against the committed by-locality CSV value (167.51).
        rvu = {"work": 3.84, "pe_nonfac": 1.33, "mp": 0.02, "cf": CF}
        loc = self._loc("AL", "ALABAMA", 1.000, 0.875, 0.566)
        assert bb.locality_rate(rvu, loc) == pytest.approx(167.51, abs=0.005)

    def test_gpci_one_equals_national(self):
        # With GPCI == 1.0 across the board, locality_rate must equal the sum of
        # the components * CF (the national basis when total == sum of components).
        rvu = {"work": 3.78, "pe_nonfac": 1.20, "mp": 0.02, "cf": CF}
        loc = self._loc("XX", "UNIT", 1.0, 1.0, 1.0)
        expected = round((3.78 + 1.20 + 0.02) * CF, 2)
        assert bb.locality_rate(rvu, loc) == pytest.approx(expected, abs=0.005)

    def test_work_floor_changes_result(self):
        # Demonstrate the floor matters: same code, floored (1.0) vs unfloored
        # (0.988) PW GPCI must produce different dollars, and the pipeline uses
        # the floored value (verified separately in TestLoadGpci).
        rvu = {"work": 3.84, "pe_nonfac": 1.33, "mp": 0.02, "cf": CF}
        floored = self._loc("AL", "ALABAMA", 1.000, 0.875, 0.566)
        unfloored = self._loc("AL", "ALABAMA", 0.988, 0.875, 0.566)
        assert bb.locality_rate(rvu, floored) > bb.locality_rate(rvu, unfloored)

    def test_result_is_rounded_to_cents(self):
        rvu = {"work": 3.78, "pe_nonfac": 1.20, "mp": 0.02, "cf": CF}
        loc = self._loc("CA", "NAPA", 1.063, 1.318, 0.508)
        val = bb.locality_rate(rvu, loc)
        assert round(val, 2) == val


# --------------------------------------------------------------------------- #
# oon_band
# --------------------------------------------------------------------------- #
class TestOonBand:
    def test_low_equals_medicare(self):
        low, _ = bb.oon_band(167.00)
        assert low == pytest.approx(167.00)

    def test_high_is_double(self):
        _, high = bb.oon_band(167.00)
        assert high == pytest.approx(334.00)

    @pytest.mark.parametrize(
        "med,exp_low,exp_high",
        [
            (173.35, 173.35, 346.70),
            (30.39, 30.39, 60.78),
            (5.01, 5.01, 10.02),
            (105.88, 105.88, 211.76),
            (0.0, 0.0, 0.0),
        ],
    )
    def test_band_math(self, med, exp_low, exp_high):
        low, high = bb.oon_band(med)
        assert low == pytest.approx(exp_low)
        assert high == pytest.approx(exp_high)

    def test_band_uses_module_multipliers(self):
        med = 100.0
        low, high = bb.oon_band(med)
        assert low == pytest.approx(med * bb.OON_MULT_LOW)
        assert high == pytest.approx(med * bb.OON_MULT_HIGH)

    def test_band_rounded_to_cents(self):
        low, high = bb.oon_band(33.335)
        assert round(low, 2) == low
        assert round(high, 2) == high


# --------------------------------------------------------------------------- #
# Module constants / wiring sanity
# --------------------------------------------------------------------------- #
class TestModuleConstants:
    def test_default_cf(self):
        assert bb.DEFAULT_CF == pytest.approx(33.4009)

    def test_placeholder_band(self):
        assert bb.OON_MULT_LOW == 1.0
        assert bb.OON_MULT_HIGH == 2.0

    def test_methodology_version_stamp(self):
        assert bb.METHODOLOGY_VERSION == "v0-medicare-2026A"

    def test_raw_paths_point_into_repo(self):
        assert bb.PPRRVU.endswith(os.path.join("data", "raw", "PPRRVU2026_Jan_nonQPP.csv"))
        assert bb.GPCI.endswith(os.path.join("data", "raw", "GPCI2026.csv"))

    @requires_raw
    def test_raw_files_present(self):
        assert os.path.isfile(bb.PPRRVU)
        assert os.path.isfile(bb.GPCI)
