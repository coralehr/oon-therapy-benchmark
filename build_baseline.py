#!/usr/bin/env python3
"""
Build the v0 therapy out-of-network reimbursement benchmark from public CMS data.

Anchor: Medicare Physician Fee Schedule, NON-FACILITY (office, POS 11) amount,
which is what an outpatient private-pay therapist bills against.

OON estimate (v0): Medicare rate x a DOCUMENTED multiplier band. The band is a
placeholder assumption, not a measurement. v1 replaces it with real
Transparency-in-Coverage-derived percentiles per payer/region.

Inputs (CMS, public domain -- see METHODOLOGY.md):
  data/raw/PPRRVU2026_Jan_nonQPP.csv   relative value units per HCPCS
  data/raw/GPCI2026.csv                geographic practice cost indices per locality

Outputs:
  data/therapy_oon_benchmark_v0_national.csv
  data/therapy_oon_benchmark_v0_by_locality.csv
  data/therapy_oon_benchmark_v0.json   (calculator-friendly)

Stdlib only. Run: python3 build_baseline.py

NOTE: CPT(R) is copyright the AMA. This tool ships CMS RVU facts and our own
plain-language labels only (see therapy_codes.py). It does NOT redistribute AMA
CPT descriptor text.
"""
import csv
import json
import os

from therapy_codes import THERAPY_CODES

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
OUT = os.path.join(HERE, "data")
PPRRVU = os.path.join(RAW, "PPRRVU2026_Jan_nonQPP.csv")
GPCI = os.path.join(RAW, "GPCI2026.csv")

# 2026 non-QPP conversion factor (also embedded per-row in PPRRVU). Read from the
# file when present; this is the documented fallback.
DEFAULT_CF = 33.4009

# OON multiplier band over Medicare. PLACEHOLDER for v0 -- a documented assumption,
# not measured. v1 replaces this with Transparency-in-Coverage percentiles.
OON_MULT_LOW = 1.0
OON_MULT_HIGH = 2.0

METHODOLOGY_VERSION = "v0-medicare-2026A"
SNAPSHOT = "2026-06-07"
SOURCE_RVU = "CMS PFS RVU26A / PPRRVU2026_Jan_nonQPP.csv"
SOURCE_GPCI = "CMS PFS RVU26A / GPCI2026.csv"


def _fnum(x):
    try:
        return float(str(x).strip())
    except (ValueError, TypeError):
        return None


def load_rvus():
    """Return {code: {status, work, pe_nonfac, mp, total_nonfac, cf}}."""
    wanted = {c["code"] for c in THERAPY_CODES}
    found = {}
    with open(PPRRVU, newline="", encoding="latin-1") as f:
        for r in csv.reader(f):
            if len(r) < 26:
                continue
            hcpcs = r[0].strip().strip('"')
            mod = r[1].strip()
            if hcpcs in wanted and mod == "":
                found[hcpcs] = {
                    "status": r[3].strip(),
                    "work": _fnum(r[5]),
                    "pe_nonfac": _fnum(r[6]),
                    "mp": _fnum(r[10]),
                    "total_nonfac": _fnum(r[11]),
                    "cf": _fnum(r[25]) or DEFAULT_CF,
                }
    return found


def load_gpci():
    """Return list of {state, locality_number, locality_name, pw_gpci, pe_gpci, mp_gpci}."""
    locs = []
    started = False
    with open(GPCI, newline="", encoding="latin-1") as f:
        for r in csv.reader(f):
            if not r:
                continue
            if r[0].strip().startswith("Medicare Administrative Contractor"):
                started = True
                continue
            if not started or len(r) < 8:
                continue
            state = r[1].strip()
            if len(state) != 2:
                continue
            pw, pe, mp = _fnum(r[5]), _fnum(r[6]), _fnum(r[7])  # PW uses 1.0-floor column
            if None in (pw, pe, mp):
                continue
            locs.append({
                "state": state,
                "locality_number": r[2].strip(),
                "locality_name": r[3].strip(),
                "pw_gpci": pw,
                "pe_gpci": pe,
                "mp_gpci": mp,
            })
    return locs


def national_rate(rvu):
    """Non-facility national Medicare allowed amount (USD)."""
    return round(rvu["total_nonfac"] * rvu["cf"], 2)


def locality_rate(rvu, loc):
    """Non-facility locality-adjusted Medicare allowed amount (USD)."""
    rate = (
        rvu["work"] * loc["pw_gpci"]
        + rvu["pe_nonfac"] * loc["pe_gpci"]
        + rvu["mp"] * loc["mp_gpci"]
    ) * rvu["cf"]
    return round(rate, 2)


def oon_band(medicare_usd):
    return round(medicare_usd * OON_MULT_LOW, 2), round(medicare_usd * OON_MULT_HIGH, 2)


def main():
    os.makedirs(OUT, exist_ok=True)
    rvus = load_rvus()
    locs = load_gpci()

    # All PFS codes in a release share one conversion factor. Assert it so a future
    # CMS file with a split/changed CF fails loudly instead of silently mixing.
    cfs = {rvu["cf"] for rvu in rvus.values() if rvu["cf"] is not None}
    assert len(cfs) == 1, f"Multiple conversion factors across codes: {sorted(cfs)}"
    conversion_factor = cfs.pop()

    national_rows = []
    json_codes = []
    missing = []

    for c in THERAPY_CODES:
        code = c["code"]
        rvu = rvus.get(code)
        if not rvu or rvu["total_nonfac"] is None:
            missing.append(code)
            continue
        med = national_rate(rvu)
        low, high = oon_band(med)
        national_rows.append({
            "cpt_code": code,
            "service_label": c["label"],
            "medicare_status": rvu["status"],
            "medicare_nonfacility_usd": med,
            "oon_estimate_low_usd": low,
            "oon_estimate_high_usd": high,
            "basis": "medicare_multiple",
            "oon_mult_low": OON_MULT_LOW,
            "oon_mult_high": OON_MULT_HIGH,
            "source": SOURCE_RVU,
            "snapshot_date": SNAPSHOT,
            "methodology_version": METHODOLOGY_VERSION,
        })
        loc_list = []
        for loc in locs:
            lmed = locality_rate(rvu, loc)
            llow, lhigh = oon_band(lmed)
            loc_list.append({
                "state": loc["state"],
                "locality_name": loc["locality_name"],
                "medicare_usd": lmed,
                "oon_low_usd": llow,
                "oon_high_usd": lhigh,
            })
        json_codes.append({
            "cpt_code": code,
            "service_label": c["label"],
            "medicare_status": rvu["status"],
            "national": {"medicare_usd": med, "oon_low_usd": low, "oon_high_usd": high},
            "localities": loc_list,
        })

    # national CSV
    nat_path = os.path.join(OUT, "therapy_oon_benchmark_v0_national.csv")
    with open(nat_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(national_rows[0].keys()))
        w.writeheader()
        w.writerows(national_rows)

    # by-locality CSV
    loc_path = os.path.join(OUT, "therapy_oon_benchmark_v0_by_locality.csv")
    with open(loc_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "cpt_code", "service_label", "medicare_status", "state", "locality_name",
            "medicare_nonfacility_usd", "oon_estimate_low_usd", "oon_estimate_high_usd",
            "basis", "snapshot_date", "methodology_version",
        ])
        for jc in json_codes:
            for loc in jc["localities"]:
                w.writerow([
                    jc["cpt_code"], jc["service_label"], jc["medicare_status"],
                    loc["state"], loc["locality_name"],
                    loc["medicare_usd"], loc["oon_low_usd"], loc["oon_high_usd"],
                    "medicare_multiple", SNAPSHOT, METHODOLOGY_VERSION,
                ])

    # calculator JSON
    json_path = os.path.join(OUT, "therapy_oon_benchmark_v0.json")
    with open(json_path, "w") as f:
        json.dump({
            "meta": {
                "methodology_version": METHODOLOGY_VERSION,
                "snapshot_date": SNAPSHOT,
                "conversion_factor": conversion_factor,
                "oon_multiplier_band": [OON_MULT_LOW, OON_MULT_HIGH],
                "basis": "medicare_multiple",
                "sources": [SOURCE_RVU, SOURCE_GPCI],
                "disclaimer": (
                    "Estimate only, not a guarantee. OON range is Medicare x a placeholder "
                    "multiplier band; v1 replaces it with Transparency-in-Coverage data."
                ),
            },
            "codes": json_codes,
        }, f, indent=2)

    # summary to stdout
    print(f"Conversion factor: {DEFAULT_CF}")
    print(f"Localities: {len(locs)}   Codes resolved: {len(national_rows)}/{len(THERAPY_CODES)}")
    if missing:
        print(f"NOT FOUND / inactive: {', '.join(missing)}")
    print()
    print(f"{'CPT':6} {'label':52} {'status':6} {'Medicare':>9} {'OON low':>8} {'OON high':>9}")
    for row in national_rows:
        print(f"{row['cpt_code']:6} {row['service_label'][:52]:52} {row['medicare_status']:6} "
              f"{row['medicare_nonfacility_usd']:9.2f} {row['oon_estimate_low_usd']:8.2f} "
              f"{row['oon_estimate_high_usd']:9.2f}")
    print()
    print(f"Wrote:\n  {nat_path}\n  {loc_path}\n  {json_path}")


if __name__ == "__main__":
    main()
