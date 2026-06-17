"""Tests for oon_bench.blend (national proxy ratio x locality Medicare GPCI)."""
from oon_bench import blend


def test_national_ratios_basic():
    nat = {"basis": "tic_innetwork_proxy", "medicare_usd": 167.0,
           "oon_low_usd": 116.4, "oon_mid_usd": 137.38, "oon_high_usd": 176.57,
           "oon_p90_usd": 209.17}
    r = blend.national_ratios(nat)
    assert abs(r["mid"] - 137.38 / 167.0) < 1e-9
    assert abs(r["low"] - 116.4 / 167.0) < 1e-9


def test_national_ratios_none_for_medicare_multiple():
    assert blend.national_ratios({"basis": "medicare_multiple", "medicare_usd": 100.0,
                                  "oon_low_usd": 100.0, "oon_high_usd": 200.0}) is None


def test_national_ratios_none_for_zero_medicare():
    assert blend.national_ratios({"basis": "tic_innetwork_proxy", "medicare_usd": 0.0,
                                  "oon_mid_usd": 50.0}) is None


def test_geo_adjust_scales_each_locality_by_its_medicare():
    code = {
        "cpt_code": "90837",
        "national": {"basis": "tic_innetwork_proxy", "medicare_usd": 167.0,
                     "oon_low_usd": 116.4, "oon_mid_usd": 137.38, "oon_high_usd": 176.57,
                     "oon_p90_usd": 209.17, "oon_obs_n": 180, "payer_scope": "single"},
        "localities": [
            {"state": "AL", "locality_name": "ALABAMA", "medicare_usd": 161.70,
             "basis": "medicare_multiple", "oon_low_usd": 161.70, "oon_high_usd": 323.40,
             "oon_mid_usd": None, "oon_p90_usd": None, "oon_obs_n": None, "payer_scope": None},
            {"state": "NY", "locality_name": "MANHATTAN", "medicare_usd": 181.97,
             "basis": "medicare_multiple", "oon_low_usd": 181.97, "oon_high_usd": 363.94,
             "oon_mid_usd": None, "oon_p90_usd": None, "oon_obs_n": None, "payer_scope": None},
        ],
    }
    blend.geo_adjust_code(code)
    ratio_mid = 137.38 / 167.0
    al, ny = code["localities"]
    assert al["basis"] == "tic_innetwork_proxy"
    assert al["geo_method"] == "medicare_gpci_blend"
    assert al["oon_obs_n"] == 180
    assert abs(al["oon_mid_usd"] - round(161.70 * ratio_mid, 2)) < 0.01
    # higher-Medicare locality must yield a higher estimate (geographic variation)
    assert ny["oon_mid_usd"] > al["oon_mid_usd"]
    assert code["national"]["geo_method"] == "measured"


def test_geo_adjust_leaves_medicare_multiple_code_untouched():
    code = {
        "cpt_code": "x", "national": {"basis": "medicare_multiple", "medicare_usd": 100.0},
        "localities": [{"state": "AL", "locality_name": "ALABAMA", "medicare_usd": 90.0,
                        "basis": "medicare_multiple", "oon_low_usd": 90.0, "oon_high_usd": 180.0}],
    }
    blend.geo_adjust_code(code)
    assert code["localities"][0]["basis"] == "medicare_multiple"
    assert "geo_method" not in code["localities"][0]
