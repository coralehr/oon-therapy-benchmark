#!/usr/bin/env python3
"""
Stage 0/1 driver — INGEST: a TiC Table-of-Contents index -> per-payer therapy JSONL.

This is the OPERATIONAL runner you point at a real payer's published
Transparency-in-Coverage (TiC) index (Table of Contents). It:

  1. DISCOVERS the in-network-rate and allowed-amount (OON) file links inside
     that index (locally or over HTTP), without crawling the whole file
     universe;
  2. STREAMS each selected MRF through the existing therapy filter
     (``v1_tic/filter_mrf.py``), which rejects ~99.99% of every file and keeps
     only the therapy CPT rows;
  3. APPENDS those rows into a single per-payer JSONL whose shape is exactly the
     FILTER OUTPUT contract (#1) that ``oon_bench.aggregate`` consumes.

It is deliberately boring and resumable, because a real run is hours long and
moves tens of GB transiently (see v1_tic/README.md "the teacup, not the ocean").

Design rules honored here:
  * **No payer URLs are hardcoded.** The index URL (or local path) and the payer
    slug come from the CLI or a TOML config. ``ingest_config.example.toml`` shows
    the shape; ``v1_tic/payer_targets.md`` documents where each payer's index
    lives and the per-payer caveats.
  * **Checkpoint / resume.** A sidecar checkpoint JSON records every file we've
    already streamed (keyed by a stable file id). Re-running skips done files, so
    an interrupted multi-hour run picks up where it left off and never
    double-counts a file into the JSONL.
  * **gzip handled both ways.** MRFs and indexes are frequently gzipped. We
    download to a temp file (transparently saving the ``.gz`` if the server sends
    gzip) and hand the path to the filter, which decompresses while streaming
    (``filter_mrf._open_maybe_gzip``). The index itself can also be gzipped.
  * **``--limit`` for sampling.** Process only the first N discovered files — the
    way you validate a new payer's schema cheaply before committing to the full
    (hours-long) run.
  * **``--dry-run`` downloads nothing.** It parses the index (which may itself be
    huge, so we STREAM it) and prints the file links it WOULD fetch, with kind,
    region hint, and whether each is already checkpointed. This is the safe first
    command against any new payer.

Stdlib only to run. ``ijson`` is used if importable for truly incremental parsing
of a huge index; otherwise a guarded stdlib fallback handles small/sample indexes
(and is refused on a large index without ijson, mirroring the filter).

TODOs flag exactly where a real payer's schema diverges from this scaffold —
provider-reference indirection, region resolution, and index nesting all vary per
payer and MUST be tuned against a real ``head -c`` sample first.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from typing import IO, Iterable, Iterator, Optional

# The filter is the teacup stage; we stream every discovered MRF through it.
# Import it whether ingest is run as a module (``python -m oon_bench.ingest``) or
# as a script from inside oon_bench/. The v1_tic dir is a sibling of the repo
# root, not a package, so we add it to sys.path explicitly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_V1_TIC = os.path.join(_REPO_ROOT, "v1_tic")
for _p in (_REPO_ROOT, _V1_TIC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import filter_mrf  # noqa: E402  (the existing streaming therapy filter)

# Size above which we refuse to stdlib-full-load an INDEX without ijson. The
# filter has its own cap for MRF bodies; this guards the index parse. 50 MB.
MAX_INDEX_FALLBACK_BYTES = 50 * 1024 * 1024

# A small read chunk for streamed downloads. Flat memory regardless of file size.
_DOWNLOAD_CHUNK = 1 << 20  # 1 MiB

# How we name the two MRF kinds INTERNALLY and how that maps to the filter's
# --kind flag. The TiC index uses CMS' field names ("in_network_files",
# "allowed_amount_file"); the filter takes "in-network" | "allowed".
KIND_IN_NETWORK = "in-network"
KIND_ALLOWED = "allowed"
FILTER_KINDS = frozenset({KIND_IN_NETWORK, KIND_ALLOWED})


# --------------------------------------------------------------------------- #
# Discovered-file record — one entry the index points at.
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class MrfLink:
    """One MRF file the index points at, normalized across payer index shapes.

    ``location`` is a URL or local path. ``kind`` is the filter kind
    ("in-network" | "allowed"). ``region_hint`` is a best-effort state derived
    from the index entry / filename (see ``_region_hint_from``), stamped by the
    filter onto rows that carry no inline geo. ``description`` / ``plan_name``
    are carried for the dry-run listing and the checkpoint audit trail only.
    """

    location: str
    kind: str
    region_hint: Optional[str] = None
    description: Optional[str] = None
    plan_name: Optional[str] = None

    @property
    def file_id(self) -> str:
        """A stable id for checkpointing: sha1 of (kind, location).

        Keyed on the location string as published in the index. If a payer
        rotates the signed query string on every publish (S3 presigned URLs do
        this), pass ``--id-strip-query`` so the checkpoint keys on the path only
        and a re-publish of the SAME file is recognized as already-done. Default
        is conservative (full URL) so two genuinely different files never
        collide.
        """
        basis = f"{self.kind}|{self.location}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def file_id_stripped(self) -> str:
        """Checkpoint id ignoring the URL query string (presigned-URL friendly)."""
        parsed = urllib.parse.urlsplit(self.location)
        path_only = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, "", "")
        )
        basis = f"{self.kind}|{path_only}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Index parsing — the DISCOVERY step.
#
# A CMS TiC Table-of-Contents (index) file looks roughly like:
#   {
#     "reporting_entity_name": "...",
#     "reporting_structure": [
#       {
#         "reporting_plans": [ {"plan_name": "...", "plan_market_type": "...", ...} ],
#         "in_network_files": [ {"description": "in-network", "location": "https://.../in-network_TX.json.gz"} ],
#         "allowed_amount_file": {"description": "allowed amounts", "location": "https://.../allowed_TX.json.gz"}
#       },
#       ...
#     ]
#   }
#
# Field names and nesting VARY per payer — the TODOs flag where.
# --------------------------------------------------------------------------- #
def _open_index_stream(location: str) -> IO[str]:
    """Open an index by URL or local path as a text stream, decompressing .gz.

    For a URL we stream the body; gzip is decompressed on the fly via
    ``gzip.GzipFile`` over the response so we never buffer the whole index. For a
    local path we delegate to the filter's gzip-aware opener.
    """
    if _is_url(location):
        # TODO(payer-specific): some payers serve the index without a .gz suffix
        # but WITH Content-Encoding: gzip, or vice-versa. We sniff the magic
        # bytes below rather than trust the suffix. Confirm with a `curl -I`.
        req = urllib.request.Request(location, headers={"Accept-Encoding": "gzip"})
        resp = urllib.request.urlopen(req)  # noqa: S310 (operator-supplied URL)
        raw = resp
        # Peek the gzip magic (0x1f 0x8b). urlopen responses aren't seekable, so
        # wrap in a buffered reader we CAN peek.
        buffered = io.BufferedReader(raw)  # type: ignore[arg-type]
        head = buffered.peek(2)[:2]
        if head[:2] == b"\x1f\x8b" or location.endswith(".gz"):
            return io.TextIOWrapper(gzip.GzipFile(fileobj=buffered), encoding="utf-8")
        return io.TextIOWrapper(buffered, encoding="utf-8")
    # Local file — reuse the filter's transparent gzip opener.
    return filter_mrf._open_maybe_gzip(location)


def _iter_reporting_structures_ijson(stream: IO[str]) -> Iterator[dict]:
    """Incrementally yield each ``reporting_structure`` entry (huge indexes)."""
    import ijson  # lazy; only when available

    # TODO(payer-specific): some payers nest the structure under a different top
    # key, or publish an index that is itself a bare array. Confirm the prefix
    # against a real `head -c` sample. UHC/Aetna/Cigna currently use
    # "reporting_structure.item".
    yield from ijson.items(stream, "reporting_structure.item")


def _iter_reporting_structures_fallback(
    stream: IO[str], *, location: str
) -> Iterator[dict]:
    """Stdlib fallback for SMALL indexes (samples, dry-run on a local file).

    Refuses a large LOCAL index without ijson (mirrors the filter's cap). For a
    URL we can't cheaply stat the size, so the fallback is best-effort; install
    ijson for real payer indexes.
    """
    if not _is_url(location) and os.path.exists(location):
        size = os.path.getsize(location)
        if size > MAX_INDEX_FALLBACK_BYTES:
            raise SystemExit(
                f"refusing to full-load a {size / 1e6:.0f} MB index without ijson.\n"
                f"Install the streaming parser:  pip install ijson\n"
                f"(The stdlib fallback is for small sample indexes only.)"
            )
    doc = json.load(stream)
    structures = doc.get("reporting_structure")
    if structures is None:
        # A few payers name it differently or ship a bare array.
        for alt in ("reporting_structures", "structures", "items", "data"):
            if isinstance(doc, dict) and alt in doc:
                structures = doc[alt]
                break
    if structures is None:
        structures = doc if isinstance(doc, list) else []
    yield from structures


def _structure_region_hint(structure: dict) -> Optional[str]:
    """Best-effort region for a whole reporting-structure entry.

    TODO(payer-specific): region resolution is the single biggest per-payer
    quirk. Some payers tag the plan or the file entry with a state/market; most
    require resolving a provider_group_id via a separate provider-reference file
    (we do NOT do that join here — see payer_targets.md caveat #7). This helper
    only reads inline hints; everything else falls through to the filename or to
    national (None) so an OON row with no geo correctly feeds the NATIONAL
    percentile.
    """
    for plan in structure.get("reporting_plans", []) or []:
        for key in ("plan_market_type", "state", "region"):
            val = plan.get(key)
            if isinstance(val, str) and len(val.strip()) == 2:
                return val.strip().upper()
    return None


def _region_hint_from(
    location: str, description: Optional[str], structure_hint: Optional[str]
) -> Optional[str]:
    """Derive a 2-letter state hint from an entry, preferring the most specific.

    Order: an inline structure/plan hint > a 2-letter token in the filename
    (e.g. ``..._TX.json.gz``) > the description. Returns None (national) when
    nothing reliable is found — never inventing geo (payer_targets.md caveat #2).
    """
    if structure_hint:
        return structure_hint
    fname = os.path.basename(urllib.parse.urlsplit(location).path)
    hint = _two_letter_state_token(fname)
    if hint:
        return hint
    if description:
        return _two_letter_state_token(description)
    return None


# US state + DC postal codes — the only tokens we accept as a region hint, so a
# random 2-letter chunk of a filename (e.g. "in" from "in-network") is rejected.
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI "
    "WY".split()
)


def _two_letter_state_token(text: str) -> Optional[str]:
    """Return a US state code found as a delimited token in ``text``, else None."""
    if not text:
        return None
    # Split on common filename delimiters, keep alpha tokens, upper-case.
    for tok in _tokenize(text):
        up = tok.upper()
        if up in _US_STATES:
            return up
    return None


def _tokenize(text: str) -> Iterator[str]:
    cur = []
    for ch in text:
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                yield "".join(cur)
                cur = []
    if cur:
        yield "".join(cur)


def discover_links(
    index_location: str,
    *,
    kinds: Iterable[str] = (KIND_IN_NETWORK, KIND_ALLOWED),
    region_filter: Optional[Iterable[str]] = None,
) -> Iterator[MrfLink]:
    """Stream the index and yield the MRF links we want.

    ``kinds`` selects which MRF kinds to keep (default both). ``region_filter``,
    if given, keeps only links whose derived ``region_hint`` is in the set (case
    insensitive); links with no region hint (national) are ALWAYS kept because an
    OON file without geo still contributes to the national percentile.

    Yields lazily so a huge index never materializes. Cherry-picks only the two
    relevant file types per entry — we do not enumerate the whole TOC universe
    (v1_tic/README.md "we cherry-pick").
    """
    wanted = frozenset(kinds)
    region_set = (
        frozenset(r.strip().upper() for r in region_filter) if region_filter else None
    )

    stream = _open_index_stream(index_location)
    try:
        if filter_mrf._has_ijson():
            structures: Iterable[dict] = _iter_reporting_structures_ijson(stream)
        else:
            structures = _iter_reporting_structures_fallback(
                stream, location=index_location
            )
        for structure in structures:
            struct_hint = _structure_region_hint(structure)
            yield from _links_from_structure(
                structure,
                struct_hint=struct_hint,
                wanted=wanted,
                region_set=region_set,
            )
    finally:
        stream.close()


def _links_from_structure(
    structure: dict,
    *,
    struct_hint: Optional[str],
    wanted: frozenset,
    region_set: Optional[frozenset],
) -> Iterator[MrfLink]:
    """Pull the in-network and allowed-amount links out of one structure entry."""
    plan_name = None
    plans = structure.get("reporting_plans") or []
    if plans and isinstance(plans[0], dict):
        plan_name = plans[0].get("plan_name")

    # In-network files: an ARRAY of {description, location}.
    if KIND_IN_NETWORK in wanted:
        for entry in structure.get("in_network_files", []) or []:
            link = _link_from_entry(
                entry,
                kind=KIND_IN_NETWORK,
                struct_hint=struct_hint,
                plan_name=plan_name,
            )
            if link and _region_ok(link, region_set):
                yield link

    # Allowed-amount file: SINGULAR object in the CMS schema, but some payers
    # publish an array. Handle both.
    if KIND_ALLOWED in wanted:
        aa = structure.get("allowed_amount_file")
        aa_entries = aa if isinstance(aa, list) else ([aa] if aa else [])
        # TODO(payer-specific): a few payers key this "out_of_network_file" or
        # nest it one level deeper. Confirm against a real index sample.
        for alt in ("out_of_network_file", "allowed_amount_files"):
            extra = structure.get(alt)
            if isinstance(extra, list):
                aa_entries.extend(extra)
            elif extra:
                aa_entries.append(extra)
        for entry in aa_entries:
            link = _link_from_entry(
                entry, kind=KIND_ALLOWED, struct_hint=struct_hint, plan_name=plan_name
            )
            if link and _region_ok(link, region_set):
                yield link


def _link_from_entry(
    entry: dict, *, kind: str, struct_hint: Optional[str], plan_name: Optional[str]
) -> Optional[MrfLink]:
    """Normalize one index file entry into an ``MrfLink`` (or None if no URL)."""
    if not isinstance(entry, dict):
        return None
    location = entry.get("location") or entry.get("url") or entry.get("href")
    if not location or not isinstance(location, str):
        return None
    description = entry.get("description")
    return MrfLink(
        location=location.strip(),
        kind=kind,
        region_hint=_region_hint_from(location, description, struct_hint),
        description=description,
        plan_name=plan_name,
    )


def _region_ok(link: MrfLink, region_set: Optional[frozenset]) -> bool:
    """A link passes the region filter if no filter is set, it's national, or it
    matches. National (no hint) links always pass — they feed the US percentile.
    """
    if region_set is None:
        return True
    if link.region_hint is None:
        return True  # national contribution is always relevant
    return link.region_hint in region_set


# --------------------------------------------------------------------------- #
# Checkpoint — resume support.
# --------------------------------------------------------------------------- #
class Checkpoint:
    """A tiny on-disk record of which files have been streamed already.

    Stored as JSON next to the output JSONL (``<out>.checkpoint.json``). Each
    completed file records its id, kind, location, region hint, rows written, and
    a finished timestamp. On resume we skip any id present here, so an interrupted
    run never re-streams (and never double-appends) a file.
    """

    def __init__(self, path: str, *, strip_query: bool = False):
        self.path = path
        self.strip_query = strip_query
        self.done: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.done = data.get("done", {})
            except (json.JSONDecodeError, OSError):
                # A corrupt checkpoint should not wedge a multi-hour run. Start
                # clean but do NOT delete the file (the operator may want it).
                # TODO: consider a .bak rotation here for forensic value.
                self.done = {}

    def key(self, link: MrfLink) -> str:
        return link.file_id_stripped() if self.strip_query else link.file_id

    def is_done(self, link: MrfLink) -> bool:
        return self.key(link) in self.done

    def mark_done(self, link: MrfLink, *, rows: int, timestamp: str) -> None:
        self.done[self.key(link)] = {
            "kind": link.kind,
            "location": link.location,
            "region_hint": link.region_hint,
            "rows": rows,
            "finished_at": timestamp,
        }
        self._save()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"done": self.done}, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)  # atomic — a kill mid-write can't corrupt it


# --------------------------------------------------------------------------- #
# Download — stream a (possibly gzipped) MRF to a temp file for the filter.
# --------------------------------------------------------------------------- #
def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _download_to_temp(location: str, *, tmp_dir: Optional[str]) -> str:
    """Stream a URL to a temp file, returning the local path.

    We do NOT decompress here — we keep the bytes as-published (saving a ``.gz``
    suffix when the body is gzip) and let the filter decompress while streaming.
    This keeps memory flat (chunked copy) and disk usage to ONE transient file at
    a time (the caller unlinks it after filtering). A local path is returned
    as-is (no copy).
    """
    if not _is_url(location):
        return location  # already local; filter reads it directly

    req = urllib.request.Request(location, headers={"Accept-Encoding": "gzip"})
    resp = urllib.request.urlopen(req)  # noqa: S310 (operator-supplied URL)

    # Decide the suffix so the filter's gzip detection (by ``.gz``) works.
    is_gzip = (
        location.endswith(".gz")
        or resp.headers.get("Content-Encoding", "").lower() == "gzip"
    )
    # TODO(payer-specific): some CDNs transparently gunzip Content-Encoding for
    # the client; in that case the bytes arriving are already plain JSON even
    # though the URL ends in .gz. If the filter chokes on a "gzip" file that is
    # actually plain, sniff the first two bytes here and adjust the suffix.
    suffix = ".json.gz" if is_gzip else ".json"

    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
    except BaseException:
        # Clean up a partial download on any failure (including KeyboardInterrupt)
        # so a resumed run doesn't filter a truncated file.
        try:
            os.unlink(tmp_path)
        finally:
            raise
    return tmp_path


# --------------------------------------------------------------------------- #
# The run.
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def ingest(
    index_location: str,
    *,
    payer: str,
    out_path: str,
    kinds: Iterable[str] = (KIND_IN_NETWORK, KIND_ALLOWED),
    region_filter: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    tmp_dir: Optional[str] = None,
    strip_query: bool = False,
    keep_temp: bool = False,
    log: IO[str] = sys.stderr,
) -> dict:
    """Discover -> download -> filter every selected MRF into one per-payer JSONL.

    Appends to ``out_path`` (so a resumed run extends the same file). The
    checkpoint at ``<out_path>.checkpoint.json`` makes the append idempotent:
    already-done files are skipped, so re-running never duplicates rows.

    Returns a summary dict: files processed/skipped, total rows written, and the
    per-kind breakdown.
    """
    checkpoint_path = out_path + ".checkpoint.json"
    ckpt = Checkpoint(checkpoint_path, strip_query=strip_query)

    files_done = 0
    files_skipped = 0
    rows_total = 0
    rows_by_kind: dict[str, int] = {KIND_IN_NETWORK: 0, KIND_ALLOWED: 0}

    # Append mode: a resumed run keeps prior rows; the checkpoint prevents
    # re-streaming the same file, so append is safe and idempotent.
    out_f = open(out_path, "a", encoding="utf-8")
    try:
        for i, link in enumerate(
            discover_links(index_location, kinds=kinds, region_filter=region_filter)
        ):
            if limit is not None and (files_done + files_skipped) >= limit:
                break

            if ckpt.is_done(link):
                files_skipped += 1
                log.write(
                    f"[skip] {link.kind} {link.location} "
                    f"(already in checkpoint)\n"
                )
                continue

            log.write(
                f"[fetch] {link.kind} region={link.region_hint or 'US'} "
                f"{link.location}\n"
            )
            local_path = _download_to_temp(link.location, tmp_dir=tmp_dir)
            downloaded = local_path != link.location  # we made a temp file
            try:
                rows = filter_mrf.run_filter(
                    local_path,
                    payer=payer,
                    kind=link.kind,
                    region_hint=link.region_hint,
                    out=out_f,
                )
            finally:
                if downloaded and not keep_temp:
                    try:
                        os.unlink(local_path)
                    except OSError:
                        pass

            out_f.flush()
            ckpt.mark_done(link, rows=rows, timestamp=_now_iso())
            files_done += 1
            rows_total += rows
            rows_by_kind[link.kind] = rows_by_kind.get(link.kind, 0) + rows
            log.write(f"[done]  +{rows} therapy rows  ({link.location})\n")
    finally:
        out_f.close()

    summary = {
        "payer": payer,
        "index": index_location,
        "out": out_path,
        "checkpoint": checkpoint_path,
        "files_processed": files_done,
        "files_skipped": files_skipped,
        "rows_written": rows_total,
        "rows_by_kind": rows_by_kind,
    }
    log.write(
        f"\n[ingest] payer={payer}: processed {files_done} file(s), "
        f"skipped {files_skipped}, wrote {rows_total} therapy rows "
        f"(in-network={rows_by_kind.get(KIND_IN_NETWORK, 0)}, "
        f"allowed={rows_by_kind.get(KIND_ALLOWED, 0)}) -> {out_path}\n"
    )
    return summary


def dry_run(
    index_location: str,
    *,
    payer: str,
    out_path: str,
    kinds: Iterable[str] = (KIND_IN_NETWORK, KIND_ALLOWED),
    region_filter: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    strip_query: bool = False,
    out: IO[str] = sys.stdout,
    log: IO[str] = sys.stderr,
) -> dict:
    """List the MRF links we WOULD fetch — download nothing.

    Parses the index (streamed), applies the same kind/region/limit selection as
    a real run, and prints one line per link with its kind, region hint, checkpoint
    status, and location. This is the safe first command against any new payer:
    it proves the index parses and the discovery selection is right BEFORE moving
    a single GB.
    """
    checkpoint_path = out_path + ".checkpoint.json"
    ckpt = Checkpoint(checkpoint_path, strip_query=strip_query)

    listed = 0
    counts = {KIND_IN_NETWORK: 0, KIND_ALLOWED: 0}
    already = 0

    out.write(f"# DRY RUN: payer={payer} index={index_location}\n")
    out.write("# (no files are downloaded; this is what a real run WOULD fetch)\n")
    out.write("# status\tkind\tregion\tlocation\n")

    for link in discover_links(
        index_location, kinds=kinds, region_filter=region_filter
    ):
        if limit is not None and listed >= limit:
            break
        status = "DONE" if ckpt.is_done(link) else "todo"
        if status == "DONE":
            already += 1
        counts[link.kind] = counts.get(link.kind, 0) + 1
        out.write(
            f"{status}\t{link.kind}\t{link.region_hint or 'US'}\t{link.location}\n"
        )
        listed += 1

    log.write(
        f"\n[dry-run] {listed} link(s) selected "
        f"(in-network={counts.get(KIND_IN_NETWORK, 0)}, "
        f"allowed={counts.get(KIND_ALLOWED, 0)}); "
        f"{already} already in checkpoint, "
        f"{listed - already} would be fetched.\n"
        f"[dry-run] NOTE: in-network is a PROXY (basis=tic_innetwork_proxy), "
        f"not true OON. Only allowed-amount files yield tic_oon_actual.\n"
    )
    return {
        "listed": listed,
        "counts": counts,
        "already_done": already,
        "would_fetch": listed - already,
    }


# --------------------------------------------------------------------------- #
# Config loading (TOML) — payer URLs come from here, never hardcoded.
# --------------------------------------------------------------------------- #
def load_config(path: str) -> dict:
    """Load an ingest config TOML. Stdlib ``tomllib`` (Python 3.11+).

    See ``ingest_config.example.toml`` for the shape. A config groups one or more
    payers, each with its ``index`` (URL or local path), optional ``regions``
    filter, and optional ``kinds``. Nothing in this repo ships real URLs.
    """
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - <3.11 only
        raise SystemExit(
            "TOML config needs Python 3.11+ (tomllib). "
            "Pass --index/--payer on the CLI instead."
        ) from exc
    with open(path, "rb") as f:
        return tomllib.load(f)


def _payer_entries_from_config(cfg: dict, only: Optional[str]) -> list[dict]:
    """Flatten a config into a list of per-payer dicts: {payer, index, kinds, regions}.

    Supports two shapes (both documented in the example):
      * a top-level ``[payers.<slug>]`` table of tables, and
      * an array of tables ``[[payer]]``.
    """
    entries: list[dict] = []
    payers = cfg.get("payers")
    if isinstance(payers, dict):
        for slug, body in payers.items():
            if isinstance(body, dict):
                entries.append({"payer": slug, **body})
    for body in cfg.get("payer", []) or []:
        if isinstance(body, dict) and body.get("payer"):
            entries.append(body)
    if only:
        entries = [e for e in entries if e.get("payer") == only]
    return entries


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve_kinds(args: argparse.Namespace) -> tuple:
    if args.kind == "both":
        return (KIND_IN_NETWORK, KIND_ALLOWED)
    return (args.kind,)


def _default_out_path(payer: str, out_dir: str) -> str:
    return os.path.join(out_dir, f"{payer}.jsonl")


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="oon_bench.ingest",
        description=(
            "Discover + stream a payer's TiC index MRFs through the therapy filter "
            "into a per-payer JSONL (the aggregate stage's input). "
            "No payer URLs are hardcoded; pass --index or --config."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--index",
        help="payer TiC Table-of-Contents index URL or local path (.json/.json.gz)",
    )
    src.add_argument(
        "--config",
        help="TOML config listing payers + their index URLs "
        "(see ingest_config.example.toml)",
    )

    p.add_argument(
        "--payer",
        help="payer slug (e.g. uhc / aetna / cigna). Required with --index; "
        "with --config, restricts the run to this one payer.",
    )
    p.add_argument(
        "--kind",
        choices=["both", KIND_IN_NETWORK, KIND_ALLOWED],
        default="both",
        help="which MRF kinds to ingest (default both). "
        "in-network=negotiated proxy; allowed=real OON allowed amounts.",
    )
    p.add_argument(
        "--region",
        action="append",
        default=None,
        metavar="ST",
        help="only ingest files whose region hint is this state (repeatable). "
        "National/no-geo files are always included.",
    )
    p.add_argument(
        "--out-dir",
        default=os.path.join("data", "v1", "raw_jsonl"),
        help="directory for per-payer JSONL output (default data/v1/raw_jsonl)",
    )
    p.add_argument(
        "-o",
        "--out",
        default=None,
        help="explicit output JSONL path (overrides --out-dir/<payer>.jsonl)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process at most N discovered files (sampling a new payer cheaply)",
    )
    p.add_argument(
        "--tmp-dir",
        default=None,
        help="directory for transient downloads (default: system temp)",
    )
    p.add_argument(
        "--id-strip-query",
        action="store_true",
        help="checkpoint by URL path only, ignoring the query string "
        "(use when the payer rotates presigned-URL query strings each publish)",
    )
    p.add_argument(
        "--keep-temp",
        action="store_true",
        help="do not delete downloaded temp files after filtering (debugging)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="list the files a real run WOULD fetch; download nothing",
    )
    args = p.parse_args(argv)

    kinds = _resolve_kinds(args)
    region_filter = args.region

    # ---- config path: iterate payers from the TOML ----
    if args.config:
        cfg = load_config(args.config)
        entries = _payer_entries_from_config(cfg, only=args.payer)
        if not entries:
            p.error(
                "no payers found in config"
                + (f" matching --payer {args.payer}" if args.payer else "")
            )
        if args.out and len(entries) > 1:
            # A single --out path would make every payer append into the SAME
            # JSONL (and share one checkpoint), silently mixing payers. Per-payer
            # output paths are required; restrict to one payer or drop --out.
            p.error(
                "--out is ambiguous with multiple payers; "
                "use --out-dir (one file per payer) or narrow with --payer"
            )
        rc = 0
        for entry in entries:
            payer = entry["payer"]
            index = entry.get("index")
            if not index:
                sys.stderr.write(f"[warn] payer {payer} has no 'index'; skipping\n")
                continue
            ekinds = (
                tuple(entry["kinds"]) if entry.get("kinds") else kinds
            )
            eregions = entry.get("regions") or region_filter
            out_path = args.out or _default_out_path(payer, args.out_dir)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            if args.dry_run:
                dry_run(
                    index,
                    payer=payer,
                    out_path=out_path,
                    kinds=ekinds,
                    region_filter=eregions,
                    limit=args.limit,
                    strip_query=args.id_strip_query,
                )
            else:
                ingest(
                    index,
                    payer=payer,
                    out_path=out_path,
                    kinds=ekinds,
                    region_filter=eregions,
                    limit=args.limit,
                    tmp_dir=args.tmp_dir,
                    strip_query=args.id_strip_query,
                    keep_temp=args.keep_temp,
                )
        return rc

    # ---- single-index path ----
    if not args.payer:
        p.error("--payer is required with --index")
    out_path = args.out or _default_out_path(args.payer, args.out_dir)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if args.dry_run:
        dry_run(
            args.index,
            payer=args.payer,
            out_path=out_path,
            kinds=kinds,
            region_filter=region_filter,
            limit=args.limit,
            strip_query=args.id_strip_query,
        )
        return 0

    ingest(
        args.index,
        payer=args.payer,
        out_path=out_path,
        kinds=kinds,
        region_filter=region_filter,
        limit=args.limit,
        tmp_dir=args.tmp_dir,
        strip_query=args.id_strip_query,
        keep_temp=args.keep_temp,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
