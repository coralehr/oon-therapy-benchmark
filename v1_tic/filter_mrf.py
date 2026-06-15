#!/usr/bin/env python3
"""
v1 Transparency-in-Coverage (TiC) streaming filter — the "teacup" stage.

A payer's machine-readable file (MRF) can be tens of GB compressed. We do NOT
want any of it except the therapy billing codes (the THERAPY_CODES set). This module STREAMS the
file and emits a compact JSONL of only the therapy rows, rejecting ~99.99% of
the records so terabytes collapse to megabytes (see v1_tic/README.md, the
"teacup, not the ocean" principle).

What it emits (one JSON object per line):
    {
      "billing_code": "90837",
      "billing_code_type": "CPT",          # almost always "CPT" for these
      "amount": 142.50,                     # negotiated_rate OR allowed amount
      "amount_kind": "negotiated",          # "negotiated" | "allowed"
      "negotiation_arrangement": "ffs",     # in-network only; null for OON
      "billing_class": "professional",      # in-network only; null for OON
      "region": "TX",                       # provider region if discoverable
      "source_file": "aetna_in-network_TX.json.gz",
      "payer": "aetna"
    }

Two MRF shapes feed this, and they are NOT the same target (see README):
  - in-network-rates files  -> `amount_kind="negotiated"` (a PROXY for OON)
  - allowed-amounts files   -> `amount_kind="allowed"`   (the REAL OON target)

Design constraints:
  - Stdlib only to RUN (gzip, json). ijson is used if importable for true
    incremental parsing of huge files, but the --dry-run path and the unit
    fixtures work without it.
  - Never load the whole document into memory. The streaming code paths read
    record-by-record. The ONLY full-load path is the documented stdlib fallback
    for SMALL files (fixtures, dry-run), which json.load()s deliberately.
  - Therapy-code membership is the hot filter and must be O(1): a frozenset
    imported from therapy_codes.THERAPY_CODES (the single source of truth).

TODOs are marked where real payer-specific parsing differs from this scaffold.
The CMS TiC JSON schema is stable in shape but payers vary in nesting,
chunking (some publish one giant file, some publish per-state/per-plan files),
field presence (OON allowed-amount files frequently omit region), and whether
they gzip. Do not assume; validate against each payer's actual sample first.

Usage:
    # Real run (streams a possibly-gzipped MRF, writes JSONL to stdout or -o):
    python3 filter_mrf.py --payer aetna --kind in-network INPUT.json.gz -o out.jsonl

    # Self-test against the tiny bundled fixture (no network, no big file):
    python3 filter_mrf.py --dry-run
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
from typing import Iterable, Iterator, Optional, TextIO

# Single source of truth for the therapy codes (imported, currently 19). Keep honest:
# if therapy_codes.py grows (e.g. the testing block 96130-96137 from the scope
# audit), this filter automatically tracks it.
try:
    from therapy_codes import THERAPY_CODES
except ImportError:  # pragma: no cover - allow running from inside v1_tic/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from therapy_codes import THERAPY_CODES

THERAPY_CODE_SET = frozenset(c["code"] for c in THERAPY_CODES)

# CMS TiC billing_code_type values that denote a CPT/HCPCS professional code.
# Therapy codes are CPT; HCPCS Level II is included defensively. Anything else
# (DRG, NDC, ICD, etc.) is not in scope and is rejected by the code-set filter
# anyway, but we also guard on type to avoid a stray numeric collision.
ACCEPTED_CODE_TYPES = frozenset({"CPT", "HCPCS"})


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def _open_maybe_gzip(path: str) -> TextIO:
    """Open a path as text, transparently decompressing .gz.

    Returns a text stream. Caller is responsible for closing.
    """
    if path.endswith(".gz"):
        # gzip.open in text mode streams the decompressed bytes; it does NOT
        # decompress the whole file up front, so memory stays flat.
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _has_ijson() -> bool:
    try:
        import ijson  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Record extraction — turns a raw MRF "code block" into 0+ emit dicts.
#
# A TiC in-network-rates "code block" looks roughly like:
#   {
#     "billing_code": "90837",
#     "billing_code_type": "CPT",
#     "negotiation_arrangement": "ffs",
#     "negotiated_rates": [
#       { "negotiated_prices": [ {"negotiated_rate": 142.50,
#                                 "billing_class": "professional", ...}, ... ],
#         "provider_groups"|"provider_references": [...] }, ...
#     ]
#   }
#
# An allowed-amounts "code block" looks roughly like:
#   {
#     "billing_code": "90837",
#     "billing_code_type": "CPT",
#     "allowed_amounts": [
#       { "tin": {...},
#         "payments": [ {"allowed_amount": 118.20,
#                        "providers": [...]}, ... ] }, ...
#     ]
#   }
#
# These shapes vary per payer — see the TODOs.
# ---------------------------------------------------------------------------
def _accept_code(block: dict) -> bool:
    code = str(block.get("billing_code", "")).strip()
    ctype = str(block.get("billing_code_type", "")).strip().upper()
    if code not in THERAPY_CODE_SET:
        return False
    # Type guard is advisory: some payers stamp "CPT" inconsistently. If the
    # code matches our therapy set we keep it even on an odd/missing type, but
    # we record the discrepancy via the emitted billing_code_type as-found.
    if ctype and ctype not in ACCEPTED_CODE_TYPES:
        # TODO(payer-specific): a few payers mislabel CPT as "CDT"/"HIPPS" etc.
        # Decide per payer whether to keep. Default: keep (code-set already
        # constrains to our therapy CPTs), but flag for audit.
        pass
    return True


def _iter_in_network_amounts(
    block: dict, *, payer: str, source_file: str, region_hint: Optional[str]
) -> Iterator[dict]:
    """Yield emit dicts for an in-network code block (negotiated rates)."""
    code = str(block.get("billing_code", "")).strip()
    ctype = str(block.get("billing_code_type", "")).strip() or "CPT"
    arrangement = block.get("negotiation_arrangement")
    for nr in block.get("negotiated_rates", []) or []:
        # TODO(payer-specific): map provider_references / provider_groups to a
        # region. Many payers only give a provider_group_id here and define the
        # group (with a TIN + service area) elsewhere in the file or in a
        # separate provider-reference file. For the scaffold we take an explicit
        # region_hint (e.g. derived from the per-state filename) and leave
        # finer geo resolution as a documented v1 task. See README "Region".
        region = _region_from_provider(nr) or region_hint
        for price in nr.get("negotiated_prices", []) or []:
            rate = price.get("negotiated_rate")
            if rate is None:
                continue
            yield {
                "billing_code": code,
                "billing_code_type": ctype,
                "amount": _to_float(rate),
                "amount_kind": "negotiated",
                "negotiation_arrangement": arrangement,
                "billing_class": price.get("billing_class"),
                "region": region,
                "source_file": source_file,
                "payer": payer,
            }


def _iter_allowed_amounts(
    block: dict, *, payer: str, source_file: str, region_hint: Optional[str]
) -> Iterator[dict]:
    """Yield emit dicts for an allowed-amounts (OON) code block."""
    code = str(block.get("billing_code", "")).strip()
    ctype = str(block.get("billing_code_type", "")).strip() or "CPT"
    for aa in block.get("allowed_amounts", []) or []:
        # TODO(payer-specific): the OON allowed-amounts schema nests payments
        # under varying keys ("payments", "out_of_network", ...). Region is
        # frequently ABSENT in OON files (the headline data-quality caveat in
        # payer_targets.md). When absent we emit region=None and the percentile
        # stage treats it as national-only.
        region = _region_from_provider(aa) or region_hint
        for pay in aa.get("payments", []) or []:
            amt = pay.get("allowed_amount")
            if amt is None:
                continue
            yield {
                "billing_code": code,
                "billing_code_type": ctype,
                "amount": _to_float(amt),
                "amount_kind": "allowed",
                "negotiation_arrangement": None,
                "billing_class": None,
                "region": _region_from_provider(pay) or region,
                "source_file": source_file,
                "payer": payer,
            }


def _region_from_provider(node: dict) -> Optional[str]:
    """Best-effort region extraction from a provider-ish node.

    TODO(payer-specific): real implementations resolve a provider TIN/NPI or a
    provider_group service area to a state/region. Payers encode this very
    differently (some inline a "service_area", some require joining a separate
    provider-reference file by provider_group_id). The scaffold checks a couple
    of common inline keys and otherwise returns None so the caller falls back to
    the filename-derived region_hint.
    """
    if not isinstance(node, dict):
        return None
    for key in ("region", "state", "service_area"):
        val = node.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Common nesting: the geo sits on a provider_groups entry, not the top node.
    for gkey in ("provider_groups", "providers"):
        groups = node.get(gkey)
        if isinstance(groups, list):
            for g in groups:
                r = _region_from_provider(g) if isinstance(g, dict) else None
                if r:
                    return r
    return None


def _to_float(x) -> Optional[float]:
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Streaming drivers
# ---------------------------------------------------------------------------
def _stream_blocks_ijson(stream: TextIO) -> Iterator[dict]:
    """True incremental parse of the top-level array of code blocks.

    Uses ijson to walk the document without materializing it. CMS TiC files put
    the per-code records under either an "in_network" array (in-network-rates
    files) or an "out_of_network" array (allowed-amounts files). We try both
    prefixes so the same driver handles either MRF shape.
    """
    import ijson  # imported lazily; only when available

    # The file is read once per prefix attempt would be wrong for a non-seekable
    # gzip stream, so we sniff the top-level key set is impractical mid-stream.
    # Instead we parse with a prefix that matches either array via ijson's
    # "item" event on the known container keys. We default to "in_network.item"
    # and document the allowed-amounts switch via the caller's --kind flag.
    #
    # TODO(payer-specific): some payers wrap records one extra level deep, or
    # publish newline-delimited JSON instead of a single array. Confirm the
    # top-level shape from a `head -c` sample before trusting the prefix.
    container = getattr(_stream_blocks_ijson, "_container", "in_network")
    for block in ijson.items(stream, f"{container}.item"):
        yield block


def _stream_blocks_fallback(stream: TextIO, *, container: str) -> Iterator[dict]:
    """Stdlib fallback for SMALL files (fixtures, --dry-run).

    This deliberately json.load()s the whole document — acceptable ONLY because
    this path is reserved for small inputs. For multi-GB production MRFs, ijson
    MUST be installed; this fallback would blow memory and is guarded by a size
    check in run_filter().

    A fully-stdlib TRUE streaming parser for arbitrary JSON is possible but
    fragile to hand-roll (you must tokenize the array element-by-element). The
    documented production path is: `pip install ijson`. See README
    "Streaming strategy".
    """
    doc = json.load(stream)
    blocks = doc.get(container)
    if blocks is None:
        # Some payers name the array differently; accept a couple of aliases.
        for alt in ("in_network", "out_of_network", "items", "data"):
            if alt in doc:
                blocks = doc[alt]
                break
    if blocks is None:
        # A bare top-level array of blocks (rare but seen).
        blocks = doc if isinstance(doc, list) else []
    for block in blocks:
        yield block


# Size above which the stdlib full-load fallback is refused (bytes). 50 MB.
MAX_FALLBACK_BYTES = 50 * 1024 * 1024


def run_filter(
    input_path: str,
    *,
    payer: str,
    kind: str,
    region_hint: Optional[str],
    out: TextIO,
) -> int:
    """Stream INPUT, emit therapy-only JSONL to `out`. Returns rows written.

    kind: "in-network"  -> read negotiated rates (proxy)
          "allowed"     -> read OON allowed amounts (real target)
    """
    container = "in_network" if kind == "in-network" else "out_of_network"
    source_file = os.path.basename(input_path)
    extract = (
        _iter_in_network_amounts if kind == "in-network" else _iter_allowed_amounts
    )

    use_ijson = _has_ijson()
    if not use_ijson:
        size = os.path.getsize(input_path)
        if size > MAX_FALLBACK_BYTES:
            raise SystemExit(
                f"refusing to full-load a {size/1e6:.0f} MB file without ijson.\n"
                f"Install the streaming parser:  pip install ijson\n"
                f"(The stdlib fallback is for small fixtures only; see README.)"
            )

    written = 0
    stream = _open_maybe_gzip(input_path)
    try:
        if use_ijson:
            _stream_blocks_ijson._container = container  # type: ignore[attr-defined]
            blocks: Iterable[dict] = _stream_blocks_ijson(stream)
        else:
            blocks = _stream_blocks_fallback(stream, container=container)

        for block in blocks:
            if not _accept_code(block):
                continue  # the ~99.99% rejection happens HERE, per record
            for rec in extract(
                block, payer=payer, source_file=source_file, region_hint=region_hint
            ):
                if rec["amount"] is None:
                    continue
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                written += 1
    finally:
        stream.close()
    return written


# ---------------------------------------------------------------------------
# Dry-run fixture: a tiny in-memory MRF exercising accept + reject paths.
# ---------------------------------------------------------------------------
_FIXTURE = {
    "reporting_entity_name": "FixtureHealth",
    "reporting_entity_type": "payer",
    "in_network": [
        {
            "billing_code": "90837",  # KEEP (therapy)
            "billing_code_type": "CPT",
            "negotiation_arrangement": "ffs",
            "negotiated_rates": [
                {
                    "provider_groups": [{"state": "TX"}],
                    "negotiated_prices": [
                        {"negotiated_rate": 142.50, "billing_class": "professional"},
                        {"negotiated_rate": 138.00, "billing_class": "professional"},
                    ],
                }
            ],
        },
        {
            "billing_code": "90834",  # KEEP (therapy)
            "billing_code_type": "CPT",
            "negotiation_arrangement": "ffs",
            "negotiated_rates": [
                {
                    "provider_groups": [{"state": "CA"}],
                    "negotiated_prices": [
                        {"negotiated_rate": 110.25, "billing_class": "professional"}
                    ],
                }
            ],
        },
        {
            "billing_code": "99213",  # REJECT (office E/M, not therapy)
            "billing_code_type": "CPT",
            "negotiation_arrangement": "ffs",
            "negotiated_rates": [
                {"negotiated_prices": [{"negotiated_rate": 95.00}]}
            ],
        },
        {
            "billing_code": "0202U",  # REJECT (lab PLA code)
            "billing_code_type": "CPT",
            "negotiated_rates": [{"negotiated_prices": [{"negotiated_rate": 500.0}]}],
        },
    ],
}


def _dry_run() -> int:
    """Write the fixture to a temp file, filter it, print the JSONL + a summary."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        json.dump(_FIXTURE, tf)
        fixture_path = tf.name

    buf = io.StringIO()
    n = run_filter(
        fixture_path,
        payer="fixture",
        kind="in-network",
        region_hint=None,
        out=buf,
    )
    os.unlink(fixture_path)

    sys.stdout.write(buf.getvalue())

    expected = 3  # two 90837 prices + one 90834; 99213 and 0202U rejected
    verdict = "PASS" if n == expected else "FAIL"
    sys.stderr.write(
        f"\n[dry-run] therapy rows kept: {n} (expected {expected})\n"
        f"[dry-run] 90837 kept (2 prices), 90834 kept (1); 99213 and 0202U rejected.\n"
        f"[dry-run] {verdict}\n"
    )
    return 0 if n == expected else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        description="Stream-filter a TiC MRF to therapy-CPT-only JSONL."
    )
    p.add_argument("input", nargs="?", help="path to MRF (.json or .json.gz)")
    p.add_argument(
        "--payer", default="unknown", help="payer slug, e.g. aetna / cigna / uhc"
    )
    p.add_argument(
        "--kind",
        choices=["in-network", "allowed"],
        default="in-network",
        help="in-network = negotiated-rate proxy; allowed = real OON allowed amounts",
    )
    p.add_argument(
        "--region-hint",
        default=None,
        help="region (e.g. state) to stamp when the file is per-state and the "
        "record carries no inline geo (see README 'Region').",
    )
    p.add_argument("-o", "--out", default=None, help="output JSONL path (default stdout)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="filter the bundled tiny fixture and self-check (no input needed)",
    )
    args = p.parse_args(argv)

    if args.dry_run:
        return _dry_run()

    if not args.input:
        p.error("INPUT is required unless --dry-run is given")

    out_stream = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        n = run_filter(
            args.input,
            payer=args.payer,
            kind=args.kind,
            region_hint=args.region_hint,
            out=out_stream,
        )
    finally:
        if args.out:
            out_stream.close()

    sys.stderr.write(f"wrote {n} therapy rows from {args.input}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
