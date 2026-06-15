"""Data-quality tests over the COMMITTED output files.

These read the three shipped artifacts directly (national CSV, by-locality CSV,
calculator JSON) and assert the structural / integrity guarantees a downstream
consumer relies on:

  * row counts (national = one row per code, by-locality = codes x 109 localities)
  * no null / blank numeric cells anywhere
  * the JSON national figures equal the national CSV for every code
  * every therapy code appears in all three files
  * no AMA CPT descriptor text leaked into any shipped label
  * the OON band relationship (low == medicare, high == 2 x medicare) holds

Stdlib (csv, json) + pytest only. No pipeline import required -- this validates
the data as published, independent of the build code.
"""
import csv
import json

import pytest
import therapy_codes

# Derive the expected scope from the single source of truth so these integrity
# checks auto-track code additions (v0 = 11 codes, v0.1 = 19 with the testing block).
EXPECTED_CODES = {c["code"] for c in therapy_codes.THERAPY_CODES}

N_CODES = len(EXPECTED_CODES)
N_LOCALITIES = 109
N_LOCALITY_ROWS = N_CODES * N_LOCALITIES

# AMA short descriptors (from PPRRVU column 2, which the pipeline never ships).
# If any of these substrings appears in a shipped service_label, AMA copyright
# text has leaked. Compared case-insensitively.
AMA_DESCRIPTORS = [
    "Psych diagnostic evaluation",
    "Psych diag eval w/med srvcs",
    "Psytx w pt 30 minutes",
    "Psytx w pt 45 minutes",
    "Psytx w pt 60 minutes",
    "Psytx crisis initial 60 min",
    "Psytx crisis ea addl 30 min",
    "Family psytx w/o pt 50 min",
    "Family psytx w/pt 50 min",
    "Group psychotherapy",
    "Brief emotional/behav assmt",
    # v0.1 testing/add-on codes. (The 90845 AMA descriptor is the single generic
    # word "Psychoanalysis", which is the ordinary medical term and not protectable
    # distinctive wording, so it is intentionally not guarded as a leak substring.)
    "Psytx complex interactive",
    "Psycl tst eval phys/qhp 1st",
    "Psycl tst eval phys/qhp ea",
    "Nrpsyc tst eval phys/qhp 1st",
    "Nrpsyc tst eval phys/qhp ea",
    "Psycl/nrpsyc tst phy/qhp 1st",
    "Psycl/nrpsyc tst phy/qhp ea",
    # Looser fragments that would also indicate the AMA wording leaked.
    "psytx",
    "psych diag",
    "w/med srvcs",
    "tst eval phys",
    "nrpsyc",
]


# --------------------------------------------------------------------------- #
# loaders
# --------------------------------------------------------------------------- #
def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def national_rows(national_csv_path):
    return _read_csv(national_csv_path)


@pytest.fixture(scope="module")
def locality_rows(by_locality_csv_path):
    return _read_csv(by_locality_csv_path)


@pytest.fixture(scope="module")
def benchmark(benchmark_json_path):
    with open(benchmark_json_path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# row counts
# --------------------------------------------------------------------------- #
class TestRowCounts:
    def test_national_has_one_row_per_code(self, national_rows):
        assert len(national_rows) == N_CODES

    def test_by_locality_has_codes_times_localities(self, locality_rows):
        assert len(locality_rows) == N_LOCALITY_ROWS

    def test_json_has_one_object_per_code(self, benchmark):
        assert len(benchmark["codes"]) == N_CODES

    def test_each_json_code_has_109_localities(self, benchmark):
        for code in benchmark["codes"]:
            assert len(code["localities"]) == N_LOCALITIES, code["cpt_code"]

    def test_locality_rows_are_blocks_of_109(self, locality_rows):
        per_code = {}
        for row in locality_rows:
            per_code.setdefault(row["cpt_code"], 0)
            per_code[row["cpt_code"]] += 1
        assert set(per_code) == EXPECTED_CODES
        assert all(n == N_LOCALITIES for n in per_code.values())


# --------------------------------------------------------------------------- #
# code coverage
# --------------------------------------------------------------------------- #
class TestCodeCoverage:
    def test_national_codes(self, national_rows):
        assert {r["cpt_code"] for r in national_rows} == EXPECTED_CODES

    def test_locality_codes(self, locality_rows):
        assert {r["cpt_code"] for r in locality_rows} == EXPECTED_CODES

    def test_json_codes(self, benchmark):
        assert {c["cpt_code"] for c in benchmark["codes"]} == EXPECTED_CODES

    def test_all_three_files_agree_on_code_set(self, national_rows, locality_rows, benchmark):
        nat = {r["cpt_code"] for r in national_rows}
        loc = {r["cpt_code"] for r in locality_rows}
        js = {c["cpt_code"] for c in benchmark["codes"]}
        assert nat == loc == js == EXPECTED_CODES


# --------------------------------------------------------------------------- #
# no null / blank numerics
# --------------------------------------------------------------------------- #
class TestNoNullNumerics:
    NATIONAL_NUMERIC = [
        "medicare_nonfacility_usd",
        "oon_estimate_low_usd",
        "oon_estimate_high_usd",
    ]
    LOCALITY_NUMERIC = [
        "medicare_nonfacility_usd",
        "oon_estimate_low_usd",
        "oon_estimate_high_usd",
    ]

    def test_national_numerics_present_and_float(self, national_rows):
        for row in national_rows:
            for col in self.NATIONAL_NUMERIC:
                val = row[col]
                assert val not in (None, ""), (row["cpt_code"], col)
                # must parse as a finite positive number
                f = float(val)
                assert f == f  # not NaN
                assert f >= 0.0

    def test_locality_numerics_present_and_float(self, locality_rows):
        for row in locality_rows:
            for col in self.LOCALITY_NUMERIC:
                val = row[col]
                assert val not in (None, ""), (row["cpt_code"], row["locality_name"], col)
                f = float(val)
                assert f == f
                assert f >= 0.0

    def test_json_numerics_present(self, benchmark):
        for code in benchmark["codes"]:
            nat = code["national"]
            for key in ("medicare_usd", "oon_low_usd", "oon_high_usd"):
                assert isinstance(nat[key], (int, float)), code["cpt_code"]
            for loc in code["localities"]:
                for key in ("medicare_usd", "oon_low_usd", "oon_high_usd"):
                    assert isinstance(loc[key], (int, float)), (code["cpt_code"], loc["locality_name"])

    def test_no_blank_label_cells(self, national_rows, locality_rows):
        for row in national_rows:
            assert row["service_label"].strip() != ""
        for row in locality_rows:
            assert row["service_label"].strip() != ""
            assert row["state"].strip() != ""
            assert row["locality_name"].strip() != ""


# --------------------------------------------------------------------------- #
# cross-file agreement
# --------------------------------------------------------------------------- #
class TestCrossFileAgreement:
    def test_json_national_equals_national_csv(self, national_rows, benchmark):
        csv_by_code = {r["cpt_code"]: r for r in national_rows}
        json_by_code = {c["cpt_code"]: c for c in benchmark["codes"]}
        for code in EXPECTED_CODES:
            crow = csv_by_code[code]
            jnat = json_by_code[code]["national"]
            assert float(crow["medicare_nonfacility_usd"]) == pytest.approx(jnat["medicare_usd"]), code
            assert float(crow["oon_estimate_low_usd"]) == pytest.approx(jnat["oon_low_usd"]), code
            assert float(crow["oon_estimate_high_usd"]) == pytest.approx(jnat["oon_high_usd"]), code

    def test_json_localities_equal_locality_csv(self, locality_rows, benchmark):
        # Key by (code, state, locality_name) and confirm every CSV locality row
        # matches the JSON locality record to the cent.
        json_index = {}
        for code in benchmark["codes"]:
            for loc in code["localities"]:
                json_index[(code["cpt_code"], loc["state"], loc["locality_name"])] = loc
        assert len(json_index) == N_LOCALITY_ROWS
        for row in locality_rows:
            key = (row["cpt_code"], row["state"], row["locality_name"])
            jloc = json_index[key]
            assert float(row["medicare_nonfacility_usd"]) == pytest.approx(jloc["medicare_usd"]), key
            assert float(row["oon_estimate_low_usd"]) == pytest.approx(jloc["oon_low_usd"]), key
            assert float(row["oon_estimate_high_usd"]) == pytest.approx(jloc["oon_high_usd"]), key

    def test_labels_consistent_across_files(self, national_rows, locality_rows, benchmark):
        nat_labels = {r["cpt_code"]: r["service_label"] for r in national_rows}
        json_labels = {c["cpt_code"]: c["service_label"] for c in benchmark["codes"]}
        loc_labels = {}
        for r in locality_rows:
            loc_labels.setdefault(r["cpt_code"], r["service_label"])
        for code in EXPECTED_CODES:
            assert nat_labels[code] == json_labels[code] == loc_labels[code], code


# --------------------------------------------------------------------------- #
# OON band relationship in the published data
# --------------------------------------------------------------------------- #
class TestOonBandInData:
    def test_national_low_equals_medicare(self, national_rows):
        for row in national_rows:
            assert float(row["oon_estimate_low_usd"]) == pytest.approx(
                float(row["medicare_nonfacility_usd"])
            ), row["cpt_code"]

    def test_national_high_is_double_medicare(self, national_rows):
        for row in national_rows:
            med = float(row["medicare_nonfacility_usd"])
            high = float(row["oon_estimate_high_usd"])
            assert high == pytest.approx(round(med * 2.0, 2), abs=0.005), row["cpt_code"]

    def test_locality_low_equals_medicare(self, locality_rows):
        for row in locality_rows:
            assert float(row["oon_estimate_low_usd"]) == pytest.approx(
                float(row["medicare_nonfacility_usd"])
            ), (row["cpt_code"], row["locality_name"])

    def test_locality_high_is_double_medicare(self, locality_rows):
        for row in locality_rows:
            med = float(row["medicare_nonfacility_usd"])
            high = float(row["oon_estimate_high_usd"])
            assert high == pytest.approx(round(med * 2.0, 2), abs=0.005), (
                row["cpt_code"],
                row["locality_name"],
            )


# --------------------------------------------------------------------------- #
# basis / methodology stamps
# --------------------------------------------------------------------------- #
class TestProvenanceStamps:
    def test_national_basis_and_version(self, national_rows):
        for row in national_rows:
            assert row["basis"] == "medicare_multiple"
            assert row["methodology_version"] == "v0-medicare-2026A"
            assert row["snapshot_date"] == "2026-06-07"

    def test_locality_basis_and_version(self, locality_rows):
        for row in locality_rows:
            assert row["basis"] == "medicare_multiple"
            assert row["methodology_version"] == "v0-medicare-2026A"
            assert row["snapshot_date"] == "2026-06-07"

    def test_json_meta_stamps(self, benchmark):
        meta = benchmark["meta"]
        assert meta["methodology_version"] == "v0-medicare-2026A"
        assert meta["basis"] == "medicare_multiple"
        assert meta["oon_multiplier_band"] == [1.0, 2.0]
        assert meta["conversion_factor"] == pytest.approx(33.4009)
        assert "estimate" in meta["disclaimer"].lower()
        assert "not a guarantee" in meta["disclaimer"].lower()


# --------------------------------------------------------------------------- #
# medicare_status propagated to every output (ship-blocker 1 regression guard)
# --------------------------------------------------------------------------- #
class TestMedicareStatusPropagation:
    def test_national_has_status(self, national_rows):
        for row in national_rows:
            assert row["medicare_status"] in {"A", "R", "T", "N", "I", "C"}, row["cpt_code"]

    def test_locality_has_status_matching_national(self, national_rows, locality_rows):
        nat = {r["cpt_code"]: r["medicare_status"] for r in national_rows}
        for row in locality_rows:
            assert row["medicare_status"] == nat[row["cpt_code"]], row["cpt_code"]

    def test_json_codes_have_status_matching_national(self, national_rows, benchmark):
        nat = {r["cpt_code"]: r["medicare_status"] for r in national_rows}
        for code in benchmark["codes"]:
            assert code["medicare_status"] == nat[code["cpt_code"]], code["cpt_code"]

    def test_restricted_code_90846_flagged_everywhere(self, national_rows, locality_rows, benchmark):
        # 90846 is Medicare status R; the calculator's restricted-status note depends
        # on this being present (and != 'A') in the JSON, not just the national CSV.
        assert next(r for r in national_rows if r["cpt_code"] == "90846")["medicare_status"] == "R"
        assert next(c for c in benchmark["codes"] if c["cpt_code"] == "90846")["medicare_status"] == "R"
        assert all(
            r["medicare_status"] == "R" for r in locality_rows if r["cpt_code"] == "90846"
        )


# --------------------------------------------------------------------------- #
# golden published values (snapshot guard)
# --------------------------------------------------------------------------- #
class TestGoldenPublishedValues:
    @pytest.mark.parametrize(
        "code,medicare",
        [
            ("90837", 167.00),
            ("90834", 113.90),
            ("90791", 173.35),
            ("90792", 202.08),
            ("90853", 30.39),
            ("96127", 5.01),
            ("96130", 123.92),
            ("96132", 122.25),
            ("90785", 14.70),
            ("90845", 109.22),
        ],
    )
    def test_national_medicare_snapshot(self, national_rows, code, medicare):
        row = next(r for r in national_rows if r["cpt_code"] == code)
        assert float(row["medicare_nonfacility_usd"]) == pytest.approx(medicare, abs=0.005)

    def test_90837_alabama_locality_snapshot(self, locality_rows):
        row = next(
            r
            for r in locality_rows
            if r["cpt_code"] == "90837" and r["locality_name"] == "ALABAMA"
        )
        assert float(row["medicare_nonfacility_usd"]) == pytest.approx(161.70, abs=0.005)


# --------------------------------------------------------------------------- #
# AMA copyright leak guard -- the critical legal bar
# --------------------------------------------------------------------------- #
class TestNoAmaDescriptorLeak:
    def _all_labels(self, national_rows, locality_rows, benchmark):
        labels = []
        labels += [r["service_label"] for r in national_rows]
        labels += [r["service_label"] for r in locality_rows]
        labels += [c["service_label"] for c in benchmark["codes"]]
        return labels

    @pytest.mark.parametrize("descriptor", AMA_DESCRIPTORS)
    def test_descriptor_absent_from_every_label(
        self, descriptor, national_rows, locality_rows, benchmark
    ):
        needle = descriptor.lower()
        for label in self._all_labels(national_rows, locality_rows, benchmark):
            assert needle not in label.lower(), f"AMA descriptor '{descriptor}' leaked into label '{label}'"

    def test_labels_match_our_plain_language(self, national_rows):
        # Spot-check a few labels are OUR wording, not the AMA wording.
        by_code = {r["cpt_code"]: r["service_label"] for r in national_rows}
        assert by_code["90791"] == "Diagnostic intake / first evaluation"
        assert by_code["90837"] == "Individual therapy, 60 minutes"
        assert by_code["90853"] == "Group therapy session"

    def test_no_raw_descriptor_column_in_outputs(self, national_csv_path, by_locality_csv_path):
        # The DESCRIPTION column from PPRRVU must never appear as a header.
        for path in (national_csv_path, by_locality_csv_path):
            with open(path, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f))
            for col in header:
                assert "description" not in col.lower()
                assert col.lower() != "ama_descriptor"
