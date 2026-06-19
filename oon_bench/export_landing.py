#!/usr/bin/env python3
"""Export a slim, bundle-friendly dataset for the coralehr.com landing tool.

The full data/v1 dataset is ~1MB (19 codes x 109 localities + percentiles). A
consumer "what insurers pay for therapy" tool only needs, per code: the national
Medicare anchor, the blended national proxy, the per-payer national medians (the
headline spread), and a per-STATE collapse of the geo-blended estimate. That
reduces to tens of KB, safe to bundle into the Astro build.

Usage:
    python3 -m oon_bench.export_landing <out.json>
    # default out: ../coralehr-landing-page/src/data/therapy-reimbursement.json
"""
from __future__ import annotations

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V1 = os.path.join(HERE, "data", "v1", "therapy_oon_benchmark_v1.json")
DEFAULT_OUT = os.path.join(
    HERE, "..", "coralehr-landing-page", "src", "data", "therapy-reimbursement.json")


def _r2(x):
    return None if x is None else round(float(x), 2)


def build() -> dict:
    d = json.load(open(V1))
    codes_out = []
    for c in d["codes"]:
        nat = c.get("national", {})
        # collapse localities -> one value per state (median across the state's localities)
        by_state_acc: dict = {}
        for loc in c.get("localities", []) or []:
            st = (loc.get("state") or "").strip().upper()
            if not st:
                continue
            by_state_acc.setdefault(st, {"med": [], "low": [], "mid": [], "high": []})
            by_state_acc[st]["med"].append(loc.get("medicare_usd"))
            by_state_acc[st]["low"].append(loc.get("oon_low_usd"))
            by_state_acc[st]["mid"].append(loc.get("oon_mid_usd"))
            by_state_acc[st]["high"].append(loc.get("oon_high_usd"))
        states = {}
        for st, acc in by_state_acc.items():
            def med(key):
                vals = [v for v in acc[key] if v is not None]
                return _r2(statistics.median(vals)) if vals else None
            states[st] = {"medicare": med("med"), "low": med("low"),
                          "mid": med("mid"), "high": med("high")}
        by_payer = {p: _r2(v.get("median")) for p, v in (c.get("by_payer") or {}).items()
                    if v.get("median") is not None}
        codes_out.append({
            "cpt": c["cpt_code"],
            "label": c["service_label"],
            "medicare_national": _r2(nat.get("medicare_usd")),
            "national": {"low": _r2(nat.get("oon_low_usd")), "mid": _r2(nat.get("oon_mid_usd")),
                         "high": _r2(nat.get("oon_high_usd"))},
            "by_payer": by_payer,
            "states": states,
        })
    return {
        "meta": {
            "snapshot_date": d["meta"].get("snapshot_date"),
            "payers": d["meta"].get("payers", []),
            "source": "CMS Physician Fee Schedule + payer Transparency-in-Coverage MRFs",
            "basis": "in-network negotiated rates as an out-of-network proxy",
            "disclaimer": ("Estimates, not guarantees. In-network negotiated rates used as the "
                           "OON proxy (payer OON allowed-amount files are effectively empty); "
                           "per-state values are the national ratio scaled by Medicare GPCI. "
                           "Not medical, billing, or legal advice."),
        },
        "codes": codes_out,
    }


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ds = build()
    json.dump(ds, open(out, "w"), separators=(",", ":"))
    size = os.path.getsize(out)
    print(f"wrote {out} ({size/1024:.0f} KB) | codes={len(ds['codes'])} "
          f"states={len(ds['codes'][0]['states'])} payers={ds['meta']['payers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
