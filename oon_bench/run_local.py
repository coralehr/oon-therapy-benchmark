#!/usr/bin/env python3
"""
run_local — the canonical "does the whole v1 backend work?" smoke entrypoint.

Runs the FULL pipeline against the bundled synthetic fixtures, with NO network
and NO real payer data:

    filter (v1_tic/filter_mrf.py)  -> aggregate (oon_bench.aggregate)
        -> merge (oon_bench.merge) -> a sample v1 dataset under data/v1_sample/

Then prints a summary (rows per basis) and a couple of example queries so a human
can eyeball that the dataset is sane. Everything here is stdlib + this repo's own
modules; the only inputs are the committed fixtures and the committed v0 baseline.

Usage:
    python3 -m oon_bench.run_local
    python3 oon_bench/run_local.py            # also works

Outputs (overwritten each run):
    data/v1_sample/therapy_oon_benchmark_v1_by_locality.csv
    data/v1_sample/therapy_oon_benchmark_v1_national.csv
    data/v1_sample/therapy_oon_benchmark_v1.json
    data/v1_sample/_filtered_innetwork.jsonl   (intermediate, kept for inspection)
    data/v1_sample/_filtered_allowed.jsonl     (intermediate, kept for inspection)
    data/v1_sample/_aggregate.jsonl            (intermediate, kept for inspection)
"""
from __future__ import annotations

import io
import os
import sys

# Make repo-root imports work (therapy_codes, v1_tic.filter_mrf) regardless of cwd.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# v1_tic is a plain directory (no package); add it so `import filter_mrf` resolves.
V1_TIC_DIR = os.path.join(REPO_ROOT, "v1_tic")
if V1_TIC_DIR not in sys.path:
    sys.path.insert(0, V1_TIC_DIR)

from oon_bench import aggregate as agg_stage  # noqa: E402
from oon_bench import merge as merge_stage  # noqa: E402
from oon_bench import query as query_stage  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures")
INNETWORK_FIXTURE = os.path.join(FIXTURE_DIR, "mrf_innetwork_sample.json")
ALLOWED_FIXTURE = os.path.join(FIXTURE_DIR, "mrf_allowed_sample.json")

DATA_DIR = os.path.join(REPO_ROOT, "data")
V0_BY_LOCALITY = os.path.join(DATA_DIR, "therapy_oon_benchmark_v0_by_locality.csv")
SAMPLE_OUT_DIR = os.path.join(DATA_DIR, "v1_sample")


def _filter_fixture(input_path: str, *, payer: str, kind: str, out_path: str) -> int:
    """Stream one fixture through the MRF filter, writing therapy-only JSONL.

    Imports v1_tic/filter_mrf.py and calls its documented run_filter() entry point
    so we exercise the REAL filter (not a reimplementation). region_hint is None:
    geo comes from inline state fields in the fixtures (or stays national).
    """
    import filter_mrf  # from v1_tic (added to sys.path above)

    buf = io.StringIO()
    n = filter_mrf.run_filter(
        input_path,
        payer=payer,
        kind=kind,
        region_hint=None,
        out=buf,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    return n


def run(out_dir: str = SAMPLE_OUT_DIR) -> dict:
    """Run filter -> aggregate -> merge on the fixtures. Returns the merge result."""
    os.makedirs(out_dir, exist_ok=True)

    innet_jsonl = os.path.join(out_dir, "_filtered_innetwork.jsonl")
    allowed_jsonl = os.path.join(out_dir, "_filtered_allowed.jsonl")
    agg_jsonl = os.path.join(out_dir, "_aggregate.jsonl")

    # 1. FILTER — both MRF shapes through the real streaming filter.
    n_innet = _filter_fixture(
        INNETWORK_FIXTURE, payer="fixturehealth", kind="in-network", out_path=innet_jsonl
    )
    n_allowed = _filter_fixture(
        ALLOWED_FIXTURE, payer="fixturehealth", kind="allowed", out_path=allowed_jsonl
    )

    # 2. AGGREGATE — pool both filtered files into percentile records.
    records = agg_stage.aggregate_files([innet_jsonl, allowed_jsonl])
    agg_stage.write_records(records, agg_jsonl)

    # 3. MERGE — join onto the v0 baseline grid, write the v1 sample dataset.
    result = merge_stage.merge(
        records,
        baseline_csv=V0_BY_LOCALITY,
        out_dir=out_dir,
    )

    _print_summary(
        n_innet=n_innet,
        n_allowed=n_allowed,
        records=records,
        result=result,
        out_dir=out_dir,
    )
    return result


def _print_summary(*, n_innet, n_allowed, records, result, out_dir) -> None:
    meta = result["meta"]
    loc_counts = meta.get("basis_counts_by_locality", {})
    nat_counts = meta.get("basis_counts_national", {})

    print("=" * 70)
    print("OON THERAPY BENCHMARK v1 — LOCAL PIPELINE SMOKE RUN")
    print("=" * 70)
    print(f"fixtures:  {INNETWORK_FIXTURE}")
    print(f"           {ALLOWED_FIXTURE}")
    print()
    print("1. FILTER (therapy-only rows kept):")
    print(f"     in-network : {n_innet}")
    print(f"     allowed    : {n_allowed}")
    print()
    print(f"2. AGGREGATE (percentile records, n_obs >= {meta.get('min_observations')}):")
    if not records:
        print("     (none cleared MIN_N)")
    for r in records:
        print(
            f"     {r['cpt_code']:>6}  {r['region']:>3}  {r['amount_kind']:<10}"
            f"  n={r['n_obs']:<3}  p25={r['p25']:<8} p50={r['p50']:<8} p75={r['p75']:<8} p90={r['p90']}"
        )
    print()
    print("3. MERGE (v1 dataset rows per basis):")
    print(f"     by-locality: {dict(loc_counts)}")
    print(f"     national   : {dict(nat_counts)}")
    print()
    print("   outputs:")
    for k, v in result["paths"].items():
        print(f"     {k}: {v}")
    print()

    # Example queries off the freshly-written JSON, to prove the read path works.
    json_path = result["paths"]["json"]
    store = query_stage.RateStore.from_file(json_path)
    print("4. EXAMPLE QUERIES (off the merged dataset):")
    for cpt, region in (("96132", "CA"), ("90837", "CA"), ("90791", "US"), ("90834", "NY")):
        res = store.get_rate(cpt, region)
        if res is None:
            print(f"     {cpt} / {region}: UNKNOWN CODE")
            continue
        est = res["estimate"]
        print(
            f"     {cpt} / {region:<3}  basis={res['basis']:<20} "
            f"conf={res['confidence']:<6} n={res['n_obs']} "
            f"low={est['low']} mid={est['mid']} high={est['high']}"
        )
    print("=" * 70)


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
