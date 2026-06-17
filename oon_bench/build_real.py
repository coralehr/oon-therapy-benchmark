#!/usr/bin/env python3
"""Boil-the-lake builder: pool hundreds of real payer plan files into data/v1/.

Streams a large, diverse sample of real in-network MRF files through the filter,
pools them, aggregates to national in-network/Medicare ratios per therapy code,
merges over the v0 Medicare baseline, and geo-blends to every CMS locality.

Runs locally (no cloud box needed); long, so run it in the background. Robust to
individual file failures (skips and continues). Writes progress to
data/v1/BUILD_LOG.txt and the final dataset to data/v1/.

Usage:
    python3 -m oon_bench.build_real            # default: 500 UHC in-network plans
    python3 -m oon_bench.build_real 800        # more plans
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "v1_tic"))

from oon_bench import aggregate, blend, merge  # noqa: E402

UHC_INDEX_URL = "https://transparency-in-coverage.uhc.com/api/v1/uhc/blobs/"
INDEX_CACHE = "/tmp/uhc-blobs.json"
BASELINE = os.path.join(HERE, "data", "therapy_oon_benchmark_v0_by_locality.csv")
OUT_DIR = os.path.join(HERE, "data", "v1")
POOL_DIR = "/tmp/br_pool"
LOG = os.path.join(OUT_DIR, "BUILD_LOG.txt")


def log(msg: str) -> None:
    line = msg.rstrip()
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def fetch_index() -> list:
    if not os.path.exists(INDEX_CACHE):
        log(f"fetching UHC index -> {INDEX_CACHE}")
        urllib.request.urlretrieve(UHC_INDEX_URL, INDEX_CACHE)
    d = json.load(open(INDEX_CACHE))
    return d if isinstance(d, list) else (d.get("blobs") or d.get("value") or d.get("data") or [])


def _size(x: dict) -> int:
    try:
        return int(x.get("size"))
    except (TypeError, ValueError):
        return -1


def select_files(items: list, n: int, lo: int, hi: int) -> list:
    """Diverse in-network sample: size-banded, strided across employers for spread."""
    inn = [x for x in items if isinstance(x, dict)
           and "in-network" in x.get("name", "").lower() and lo < _size(x) < hi]
    inn.sort(key=lambda x: x.get("name", ""))  # alpha by employer -> stride = geographic/plan spread
    if len(inn) <= n:
        return inn
    stride = len(inn) / n
    return [inn[int(i * stride)] for i in range(n)]


def download_and_filter(url: str, idx: int) -> str | None:
    """Download one gz, stream through the filter to a per-file JSONL. Returns path or None."""
    gz = os.path.join(POOL_DIR, f"f{idx}.json.gz")
    out = os.path.join(POOL_DIR, f"f{idx}.jsonl")
    try:
        urllib.request.urlretrieve(url, gz)
        subprocess.run(
            [sys.executable, os.path.join(HERE, "v1_tic", "filter_mrf.py"),
             "--payer", "uhc", "--kind", "in-network", gz, "-o", out],
            check=True, capture_output=True, timeout=300,
        )
        return out
    except Exception as e:  # noqa: BLE001 - boil-the-lake: skip a bad file, keep going
        log(f"  skip f{idx}: {type(e).__name__}")
        return None
    finally:
        if os.path.exists(gz):
            os.remove(gz)


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(POOL_DIR, exist_ok=True)
    open(LOG, "w").close()
    log(f"BUILD START: target {n} UHC in-network plans")

    items = fetch_index()
    picks = select_files(items, n, 1_000_000, 12_000_000)
    log(f"selected {len(picks)} files from {len(items)} index entries")

    ok = 0
    for i, x in enumerate(picks):
        if download_and_filter(x["downloadUrl"], i):
            ok += 1
        if (i + 1) % 25 == 0:
            log(f"  progress {i + 1}/{len(picks)} ({ok} ok)")
    log(f"filtered {ok}/{len(picks)} files")

    pool = sorted(glob.glob(os.path.join(POOL_DIR, "*.jsonl")))
    records = aggregate.aggregate_files(pool)
    log(f"aggregate records (cleared MIN_N>=10): {len(records)}")

    result = merge.merge(records, baseline_csv=BASELINE, out_dir=OUT_DIR)
    log(f"merge basis counts: {result.get('meta', {}).get('basis_counts_national')}")

    # geo-blend localities + patch the by-locality CSV
    jp = os.path.join(OUT_DIR, "therapy_oon_benchmark_v1.json")
    lp = os.path.join(OUT_DIR, "therapy_oon_benchmark_v1_by_locality.csv")
    ds = json.load(open(jp))
    ds["meta"]["plan_sample_size"] = ok
    blend.geo_adjust_dataset(ds)
    json.dump(ds, open(jp, "w"), indent=2)
    _patch_locality_csv(lp, ds)

    # summary of national proxy ratios
    log("\nNATIONAL in-network proxy (n = pooled professional observations):")
    for c in sorted(ds["codes"], key=lambda c: c["cpt_code"]):
        nat = c["national"]
        if nat.get("oon_obs_n"):
            log(f"  {c['cpt_code']}  n={nat['oon_obs_n']:>5}  "
                f"p25={nat['oon_low_usd']}  med={nat['oon_mid_usd']}  p75={nat['oon_high_usd']}")
    log(f"\nBUILD DONE: {ok} plans pooled into data/v1/")
    return 0


def _patch_locality_csv(lp: str, ds: dict) -> None:
    import csv
    idx = {(c["cpt_code"], loc["state"], loc["locality_name"]): loc
           for c in ds["codes"] for loc in c["localities"]}
    rows = list(csv.DictReader(open(lp)))
    if not rows:
        return
    cols = list(rows[0].keys())
    for r in rows:
        loc = idx.get((r["cpt_code"], r["state"], r["locality_name"]))
        if not loc:
            continue
        for col in ("basis", "oon_low_usd", "oon_high_usd", "oon_mid_usd",
                    "oon_p90_usd", "oon_obs_n", "payer_scope"):
            if col in r and col in loc:
                r[col] = "" if loc[col] is None else loc[col]
    with open(lp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
