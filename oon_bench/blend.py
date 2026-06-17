"""Geo-adjust the national in-network proxy onto CMS localities via Medicare GPCI.

Why not per-state from the MRF directly: payer in-network rates are negotiated at
the provider-group / TIN level, and groups are frequently multi-state, so pinning a
single negotiated rate to one state is inherently fuzzy. Resolving it "properly"
would mean joining tens of thousands of NPIs through NPPES, and even then the
group-level rate isn't cleanly one state.

Instead we use the signal we actually measured -- the NATIONAL in-network /
Medicare ratio per code (real data, n=148-180) -- and let the geographic variation
come from Medicare's GPCI, which the v0 baseline already encodes per locality:

    locality_estimate(code) = locality_medicare(code) * national_proxy_ratio(code)

The rate signal is real; the geography is Medicare's. Locality rows are labeled
basis=tic_innetwork_proxy with geo_method="medicare_gpci_blend" so it is explicit
that they are derived from the national measurement, not per-state measured.
"""
from __future__ import annotations

from typing import Optional

PROXY_BASES = {"tic_innetwork_proxy", "tic_oon_actual"}
GEO_METHOD = "medicare_gpci_blend"


def _round2(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 2)


def national_ratios(national: dict) -> Optional[dict]:
    """Return {low, mid, high, p90} = proxy / national-Medicare, or None.

    None when the national row is not a real proxy (e.g. medicare_multiple) or the
    Medicare anchor is non-positive (cannot form a ratio).
    """
    if not isinstance(national, dict):
        return None
    if national.get("basis") not in PROXY_BASES:
        return None
    med = national.get("medicare_usd")
    try:
        med = float(med)
    except (TypeError, ValueError):
        return None
    if med <= 0:
        return None

    def r(key):
        v = national.get(key)
        try:
            return float(v) / med
        except (TypeError, ValueError):
            return None

    return {"low": r("oon_low_usd"), "mid": r("oon_mid_usd"),
            "high": r("oon_high_usd"), "p90": r("oon_p90_usd")}


def geo_adjust_code(code_obj: dict) -> dict:
    """Rewrite a code's localities from the national proxy ratio x locality Medicare.

    Mutates and returns code_obj. National row gains geo_method="measured". If the
    national row is not a real proxy, localities are left untouched.
    """
    national = code_obj.get("national") or {}
    ratios = national_ratios(national)
    if ratios is None:
        return code_obj
    national["geo_method"] = "measured"

    for loc in code_obj.get("localities", []) or []:
        med = loc.get("medicare_usd")
        try:
            med = float(med)
        except (TypeError, ValueError):
            continue
        loc["oon_low_usd"] = _round2(med * ratios["low"]) if ratios["low"] is not None else None
        loc["oon_mid_usd"] = _round2(med * ratios["mid"]) if ratios["mid"] is not None else None
        loc["oon_high_usd"] = _round2(med * ratios["high"]) if ratios["high"] is not None else None
        loc["oon_p90_usd"] = _round2(med * ratios["p90"]) if ratios["p90"] is not None else None
        loc["basis"] = national.get("basis", "tic_innetwork_proxy")
        loc["geo_method"] = GEO_METHOD
        loc["oon_obs_n"] = national.get("oon_obs_n")
        loc["payer_scope"] = national.get("payer_scope")
    return code_obj


def geo_adjust_dataset(dataset: dict) -> dict:
    """Apply geo-adjustment to every code and refresh meta.basis_counts_by_locality."""
    counts: dict = {}
    for code_obj in dataset.get("codes", []) or []:
        geo_adjust_code(code_obj)
        for loc in code_obj.get("localities", []) or []:
            b = loc.get("basis", "medicare_multiple")
            counts[b] = counts.get(b, 0) + 1
    dataset.setdefault("meta", {})["basis_counts_by_locality"] = counts
    dataset["meta"]["geo_method_note"] = (
        "Locality rows with geo_method=medicare_gpci_blend are the national measured "
        "in-network/Medicare ratio scaled by each locality's Medicare GPCI, not "
        "per-state measured rates."
    )
    return dataset
