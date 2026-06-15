"""oon_bench.cli — the v1 command-line backend.

Three subcommands, one per pipeline stage downstream of the MRF filter:

    aggregate <filter.jsonl> -o <agg.jsonl>
        Group the filter's per-price JSONL into percentile records per
        (cpt x region x amount_kind x payer). Delegates to oon_bench.aggregate.

    merge --aggregate <agg.jsonl> --baseline <v0_by_locality.csv> --out data/v1
        Join the aggregated percentiles onto the v0 Medicare baseline grid,
        stamp basis/provenance, and write the v1 CSV/JSON outputs. Delegates to
        oon_bench.merge.

    query <cpt> --region CA --data <v1.json>
        Point-query the merged dataset. Uses oon_bench.query.RateStore directly
        (no sibling module needed), so `query` works even before aggregate/merge
        have been wired up.

Run as:  python -m oon_bench <subcommand> ...

The aggregate/merge stages live in sibling modules (built per the same shared
data contract). This CLI imports them lazily and calls their documented entry
points, so a missing/not-yet-built stage fails with a clear message instead of
an import error at startup, and `query` never depends on them.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from oon_bench.query import NATIONAL_REGION, RateStore


def _cmd_aggregate(args: argparse.Namespace) -> int:
    # aggregate_file(input_jsonl) -> list[record]; write_records(records, path).
    from oon_bench import aggregate as agg_stage

    inputs = args.input if isinstance(args.input, list) else [args.input]
    records = agg_stage.aggregate_files(inputs)
    n = agg_stage.write_records(records, args.out)
    sys.stderr.write(f"aggregate: wrote {n} record(s) to {args.out}\n")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    # merge(aggregate_records, baseline_csv, out_dir): the first arg is the
    # PARSED aggregate records (contract #2), not a path. Read + parse here.
    from oon_bench import merge as merge_stage

    records: list[dict] = []
    with open(args.aggregate, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    result = merge_stage.merge(records, baseline_csv=args.baseline, out_dir=args.out)
    counts = result["meta"].get("basis_counts_by_locality", {})
    sys.stderr.write(
        f"merge: wrote v1 outputs under {args.out} (basis by-locality: {counts})\n"
    )
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    store = RateStore.from_file(args.data)
    result = store.get_rate(args.cpt, args.region)
    if result is None:
        sys.stderr.write(
            f"error: unknown CPT code '{args.cpt}'. "
            f"Run `python -m oon_bench codes --data {args.data}` to list valid codes.\n"
        )
        return 2
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_codes(args: argparse.Namespace) -> int:
    store = RateStore.from_file(args.data)
    json.dump(store.list_codes(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oon_bench",
        description="OON therapy benchmark v1 — aggregate, merge, and query the dataset.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_agg = sub.add_parser(
        "aggregate",
        help="aggregate filter JSONL into percentile records",
    )
    p_agg.add_argument(
        "input", nargs="+", help="path(s) to the filter's JSONL output (pooled if several)"
    )
    p_agg.add_argument(
        "-o", "--out", required=True, help="output aggregate JSONL path"
    )
    p_agg.set_defaults(func=_cmd_aggregate)

    p_merge = sub.add_parser(
        "merge",
        help="merge aggregated percentiles onto the v0 baseline -> v1 dataset",
    )
    p_merge.add_argument(
        "--aggregate", required=True, help="path to the aggregate JSONL"
    )
    p_merge.add_argument(
        "--baseline", required=True, help="path to v0 by-locality CSV"
    )
    p_merge.add_argument(
        "--out", default="data/v1", help="output directory for v1 files (default data/v1)"
    )
    p_merge.set_defaults(func=_cmd_merge)

    p_query = sub.add_parser(
        "query",
        help="look up a single CPT x region in the merged v1 dataset",
    )
    p_query.add_argument("cpt", help="CPT code, e.g. 90837")
    p_query.add_argument(
        "--region",
        default=NATIONAL_REGION,
        help=f"two-letter state, or '{NATIONAL_REGION}' for national (default {NATIONAL_REGION})",
    )
    p_query.add_argument(
        "--data", required=True, help="path to merged v1 dataset (.json or by-locality .csv)"
    )
    p_query.set_defaults(func=_cmd_query)

    p_codes = sub.add_parser(
        "codes",
        help="list the code catalog from a merged dataset",
    )
    p_codes.add_argument(
        "--data", required=True, help="path to merged v1 dataset (.json or by-locality .csv)"
    )
    p_codes.set_defaults(func=_cmd_codes)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
