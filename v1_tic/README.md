# v1 — Transparency-in-Coverage (TiC) enrichment

**Goal:** replace the v0 placeholder OON band (`Medicare × [1.0, 2.0]`) with
**real payer allowed-amount percentiles**, therapy-CPT-only, by region.

v0 ships firm Medicare numbers and an honest, labeled *assumption* for the
out-of-network range. v1 makes that single number real. Nothing else about the
schema or the honesty contract changes — we swap the basis of the OON columns
from `medicare_multiple` to measured payer data, and we carry provenance on
every row so a consumer always knows which it is.

This directory is **scaffold + design only**. Nothing here downloads payer
files. It defines the method, the streaming filter, and the merge contract so a
future run can execute it deterministically.

---

## The teacup, not the ocean

The price-transparency space is a trap. Each major payer's
Transparency-in-Coverage machine-readable file (MRF) set is **terabytes**,
republished **monthly**, and spread across thousands of per-plan / per-state
files. Every volunteer who tried to index *the whole thing* drowned: storage,
compute, and a treadmill of monthly re-ingestion that never ends.

We are not building a price index. We are building a **narrow, periodic
benchmark for the ~19 therapy CPT codes**. That reframing is the entire trick:

- **Stream-filter, keep ~0.01%.** Each MRF is a flat-ish list of per-code
  records. We stream it record-by-record and keep ONLY records whose
  `billing_code` is in the therapy set (`therapy_codes.THERAPY_CODES`). A payer
  file has tens of thousands of distinct codes; we want ~11. We **reject
  ~99.99% of every file**, so a multi-GB (TB-at-the-payer-level) input collapses
  to a few MB of therapy JSONL. The ocean becomes a teacup *at ingestion time* —
  we never store the ocean.

- **Quarterly snapshots, not a live index.** We publish a periodic release of a
  narrow slice (matching the CMS RVU release cadence used by v0: RVU26A,
  RVU26B, …). We do **not** track every monthly payer republish. A therapy
  allowed-amount distribution does not move materially month to month; a
  quarterly refresh is honest and sustainable for a volunteer-scale project.

- **Never load a whole file into memory.** The filter (`filter_mrf.py`) streams
  the (possibly gzipped) JSON incrementally. The only full-load path is a
  guarded stdlib fallback for tiny fixtures; production runs require `ijson`
  and refuse to full-load a large file (hard size cap).

If v0 is "boil nothing, anchor on Medicare," v1 is "boil a teacup, on a
schedule." Both deliberately avoid the treadmill.

---

## Two data shapes — proxy vs. real target

A payer publishes (at least) two kinds of MRF. They are **not interchangeable**,
and v1 treats them with different `basis` provenance:

| MRF kind | What it contains | Density | Role in v1 | `basis` stamp |
|----------|------------------|---------|-----------|---------------|
| **in-network-rates** | Negotiated rates the payer pays *in-network* providers per code | **Rich** — most codes, many provider groups | **Proxy**. OON allowed amounts often track in-network negotiated rates (an OON plan typically reimburses a % of an allowed amount that is itself anchored near in-network/UCR). Use when real OON data is missing or too sparse for a region. | `tic_innetwork_proxy` |
| **allowed-amounts** (OON) | Historical *allowed amounts* the payer applied to **out-of-network** claims | **Sparse** — only codes/regions with enough OON claim volume; many gaps; region often absent | **The real target.** This is literally what an OON claim was allowed. Prefer it wherever it exists with enough N. | `tic_oon_actual` |

**Why we need both:** the OON allowed-amounts file is the bullseye but it is
sparse and patchy (see `payer_targets.md` for the per-payer caveats). The
in-network file is dense and reliable but is a *proxy*. v1's merge prefers real
OON data and **falls back to the in-network proxy** only where OON is missing or
under-powered — and it **labels which one it used on every row** so the dataset
never silently passes off a proxy as the real thing.

### Selection rule (per code × region)

```
if oon_allowed has >= MIN_N observations for (code, region):
    use oon_allowed percentiles      -> basis = "tic_oon_actual"
elif in_network has >= MIN_N observations for (code, region):
    use in_network percentiles       -> basis = "tic_innetwork_proxy"
else:
    fall back to v0 Medicare band    -> basis = "medicare_multiple"   (unchanged)
```

`MIN_N` (a minimum observation count, e.g. 20) guards against publishing a
"percentile" computed from two data points. The exact threshold is a v1
calibration task; it must be recorded in `meta` and on the row.

---

## Pipeline (file-discovery → filter → percentiles → merge)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 0. DISCOVERY                                                             │
│    Payer Table-of-Contents (TOC) index file (the published MRF entry     │
│    point) -> lists in-network-rate file URLs + allowed-amount file URLs  │
│    (often per-plan / per-state). Pick the therapy-relevant subset.       │
│    -> payer_targets.md documents where each payer's index lives.         │
├─────────────────────────────────────────────────────────────────────────┤
│ 1. STREAM-FILTER  (filter_mrf.py)  — the teacup stage                    │
│    For each selected MRF: stream record-by-record, keep ONLY therapy     │
│    billing_codes, emit compact JSONL                                      │
│      {billing_code, billing_code_type, amount, amount_kind,              │
│       negotiation_arrangement, billing_class, region, source_file, payer}│
│    ~99.99% rejected. GB/TB -> MB.                                        │
├─────────────────────────────────────────────────────────────────────────┤
│ 2. AGGREGATE -> PERCENTILES  (v1 build step, TODO)                       │
│    Group the JSONL by (billing_code, region, amount_kind). Drop bad      │
│    rows (<= 0, absurd outliers). Compute p25 / p50 / p75 / p90 and N.    │
│    Apply MIN_N. Pick OON-actual over in-network-proxy per the rule above.│
├─────────────────────────────────────────────────────────────────────────┤
│ 3. MERGE into the existing schema  (v1 build step, TODO)                 │
│    Join percentiles onto the v0 code × locality grid by region, stamp    │
│    provenance, write v1 outputs alongside v0.                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### File discovery, in detail

Each payer publishes an **index / Table-of-Contents file** — the single
published URL that lists every MRF for every plan. Its records point at:

- **in-network-rate file** link(s) — one or many, frequently chunked by plan
  and/or state, gzipped.
- **allowed-amount file** link(s) — the OON historical allowed amounts.

The discovery step parses that TOC, selects only the file links we need (the
ones whose plan/region we want to benchmark, and only the two relevant file
types), and hands those URLs to the filter. We do **not** crawl the whole TOC's
file universe; we cherry-pick. `payer_targets.md` records each payer's index
location and the practical selection notes (some payers' TOCs are themselves
huge and must *also* be streamed).

> Note on `negotiation_arrangement`: in-network records carry `ffs`
> (fee-for-service), `bundle`, or `capitation`. For a per-session therapy
> benchmark only **`ffs`** is meaningful; bundled/capitated rows are filtered
> out at the percentile stage (the filter keeps the field so that stage can
> decide).

---

## Running against real payers

Everything above is the design. This section is the **operational path you
actually run** against real payer MRFs. It is honest about the cost: a full run
is hours long and moves tens of GB of transient data, because the payer files
are enormous and the therapy filter rejects about 99.99 percent of every one of
them.

### The four stages, end to end

```
ingest  ->  aggregate  ->  merge  ->  serve
(0/1)        (2)            (3)        (read)
```

1. **ingest** (`oon_bench/ingest.py`): point it at a payer's published TiC
   Table-of-Contents (index) URL or a local copy. It discovers the
   in-network-rate and allowed-amount file links inside that index, streams each
   selected MRF through `v1_tic/filter_mrf.py`, and appends the therapy-only rows
   into a per-payer JSONL. This is the only stage that touches the network or
   moves large files.
2. **aggregate** (`oon_bench/aggregate.py`): groups the per-payer JSONL into
   percentile records per code, region, amount kind, and payer. It applies every
   drop rule (non-positive amounts, non-professional billing class, non-ffs
   in-network arrangements, dedupe, outlier clip) and suppresses any group below
   `MIN_N`.
3. **merge** (`oon_bench/merge.py`): joins those percentiles onto the v0
   Medicare baseline grid, applies the basis precedence
   (`tic_oon_actual` over `tic_innetwork_proxy` over `medicare_multiple`), and
   writes the v1 CSV and JSON outputs under `data/v1/`.
4. **serve** (`oon_bench/api.py`, `oon_bench/query.py`): reads the merged v1
   dataset once at startup and answers per-code, per-region queries. No network
   at request time.

### Commands

No payer URLs are committed. Get each payer's current index URL from its public
"Transparency in Coverage" page (see `payer_targets.md`) and put it in a config,
or pass it on the command line.

```bash
# 0. Install the streaming parser for real (huge) files. The pipeline is
#    otherwise stdlib only.
pip install ijson

# 1a. DRY RUN FIRST against any new payer. Downloads nothing; lists the files a
#     real run WOULD fetch, with kind, region hint, and checkpoint status.
python -m oon_bench.ingest --index "<PAYER_TOC_INDEX_URL>" --payer aetna \
    --dry-run --limit 20

# 1b. Real ingest from a single index. Streams each MRF through the filter and
#     appends therapy rows to data/v1/raw_jsonl/aetna.jsonl. Resumable.
python -m oon_bench.ingest --index "<PAYER_TOC_INDEX_URL>" --payer aetna

# 1c. Or drive several payers from a config (copy ingest_config.example.toml):
python -m oon_bench.ingest --config oon_bench/ingest_config.toml --dry-run
python -m oon_bench.ingest --config oon_bench/ingest_config.toml

# 2. Aggregate one or more per-payer JSONL files into percentile records.
#    Pool payers by passing several inputs (multi-payer percentiles).
python -m oon_bench aggregate \
    data/v1/raw_jsonl/aetna.jsonl data/v1/raw_jsonl/uhc.jsonl \
    -o data/v1/aggregate.jsonl

# 3. Merge percentiles onto the v0 baseline -> v1 dataset under data/v1/.
python -m oon_bench merge \
    --aggregate data/v1/aggregate.jsonl \
    --baseline data/therapy_oon_benchmark_v0_by_locality.csv \
    --out data/v1

# 4. Serve it.
OON_V1_DATA=data/v1/therapy_oon_benchmark_v1.json uvicorn oon_bench.api:app
```

### What ingest gives you (the resumable part)

- **Checkpoint and resume.** Each finished file is recorded in a sidecar
  `data/v1/raw_jsonl/<payer>.jsonl.checkpoint.json`. Re-running the same command
  skips files already in the checkpoint, so an interrupted multi-hour run picks
  up where it stopped and never double-appends a file's rows into the JSONL. The
  checkpoint is written atomically, so a kill mid-write cannot corrupt it.
- **gzip handled.** The index and the MRFs are usually gzipped. The runner
  streams the index, downloads each MRF to a single transient temp file (kept as
  published, gzip and all), filters it, then deletes it before fetching the next
  one. Disk holds one transient file at a time, not the whole payer.
- **`--limit N`** processes only the first N discovered files. Use it to sample a
  new payer cheaply before committing to the full run.
- **`--region ST`** keeps only files whose region hint is that state (repeatable).
  Files with no geo (national OON) are always included, because a region-less OON
  observation still feeds the national percentile.
- **`--id-strip-query`** checkpoints by URL path only, ignoring the query string.
  Use it when a payer serves presigned S3 URLs whose query string rotates every
  publish, so a re-published copy of the same file is recognized as already done.

### The TB-scale reality (read this before you start)

- A single payer's full TiC MRF set is **terabytes**, republished **monthly**,
  spread across thousands of per-plan and per-state files. We never store the
  ocean. The filter keeps only the ~19 therapy CPT codes and **rejects about
  99.99 percent** of every file, so a multi-GB input collapses to a few MB of
  therapy JSONL.
- Even so, a real ingest of all three initial payers across many regions is
  realistically **hours of wall-clock time** and moves **tens of GB of transient
  download** (one file at a time, deleted after filtering). Plan for it. Run it
  on a machine with a fast link, start with `--dry-run --limit`, and let the
  checkpoint carry a long run across interruptions.
- The index file alone can be very large. Install `ijson` so both the index and
  the MRFs parse incrementally. Without `ijson` the runner refuses to full-load a
  large index or a large MRF (a hard size cap), exactly so a stdlib fallback
  meant for small samples can never blow memory on a production file.
- We take a **quarterly** snapshot, not a live index. A therapy allowed-amount
  distribution does not move materially month to month, and chasing every monthly
  republish is the treadmill this project exists to avoid.

### `MIN_N` suppression: why a region can come back empty

A percentile computed from two data points is not a percentile. The aggregate
stage **only publishes a TiC figure when a group has at least `MIN_N`
observations** (currently 10, defined once in `oon_bench/schemas.py`). Below that
threshold the figure is suppressed and the merge falls back a basis tier:
measured OON, then the in-network proxy, then the Medicare band. So a small state
with thin OON claim volume will legitimately show `basis = medicare_multiple`,
and that is the honest answer, not a bug. The fallback is always stamped per row,
so a reader can see at a glance which figures are measured and which are modeled.

### In-network is a PROXY, not true OON

This is the one thing not to get wrong. The runner ingests two kinds of file:

- **allowed-amount** files carry the historical amounts a payer actually allowed
  on **out-of-network** claims. This is the real target, stamped
  `tic_oon_actual`. It is sparse and patchy and the region is frequently absent.
- **in-network-rate** files carry the rates a payer pays **in-network**
  providers. This is **not** an OON amount. We use it only as a fallback proxy,
  stamped `tic_innetwork_proxy`, because OON allowed amounts tend to track
  in-network and UCR-anchored rates, and the in-network files are dense where the
  OON files are empty.

The merge always prefers real OON data and only falls back to the in-network
proxy where OON is missing or under-powered, and it **labels which one it used on
every row**. We never present an in-network proxy as a measured OON amount. When
you read a `tic_innetwork_proxy` figure, read it as "what this payer pays
in-network, used as a stand-in," not as "what an OON claim was allowed."

---

## Region

The benchmark's geography axis is the same CMS payment-locality grid v0 already
uses (state + locality). TiC files, however, encode provider geography
inconsistently:

- Some inline a state / service area on the provider group.
- Many reference a `provider_group_id` resolved elsewhere (a separate
  provider-reference file), requiring a join.
- **OON allowed-amount files frequently omit region entirely** — the headline
  data-quality caveat. Those observations contribute to the **national**
  percentile only.

`filter_mrf.py` therefore takes an optional `--region-hint` (e.g. derived from a
per-state filename) and stamps it when a record carries no inline geo. Mapping
TiC regions onto CMS localities (and ZIP→locality via `26LOCCO`) is a v1
calibration task, intentionally coarse at first: **state-level** percentiles,
backfilled to the v0 locality rows by state. Finer geo is a later refinement.

---

## Merge into the existing schema

v1 keeps the v0 schema and the same three output shapes (national CSV,
by-locality CSV, calculator JSON). It changes only the **OON columns and their
provenance**. Proposed additive columns (v0 columns unchanged so existing
consumers don't break):

| New field | Meaning |
|-----------|---------|
| `oon_p25_usd`, `oon_p50_usd`, `oon_p75_usd`, `oon_p90_usd` | Measured allowed-amount percentiles for the code × region |
| `oon_obs_n` | Number of observations behind the percentiles (transparency / MIN_N gate) |
| `basis` | `tic_oon_actual` \| `tic_innetwork_proxy` \| `medicare_multiple` (fallback) — **set per row** |
| `payer_scope` | Which payers contributed (e.g. `aetna+cigna+uhc`, or `medicare-fallback`) |
| `methodology_version` | bumped to `v1-tic-2026A` |

The legacy `oon_estimate_low_usd` / `oon_estimate_high_usd` columns are retained
for back-compat and re-pointed at `oon_p25_usd` / `oon_p90_usd` (a defensible
"typical range"), with the band's meaning documented. Where a row falls back to
Medicare, `basis = medicare_multiple` exactly as in v0, so a fallback row is
never mistaken for measured data.

`meta` gains: `min_observations`, `percentiles: [25,50,75,90]`, the payer list
with each payer's snapshot date, and a per-`basis` row count so a reader can see
at a glance how much of the dataset is real OON vs. proxy vs. Medicare fallback.

The disclaimer is updated to state that figures are **derived from payers'
published Transparency-in-Coverage data, are not a guarantee of payment, and
reflect a quarterly snapshot.**

---

## Honesty contract (carried over from v0)

- Every row keeps `source`, `snapshot_date`, `methodology_version`, **and
  `basis`** — and in v1 `basis` is per-row, so proxy/real/fallback is never
  ambiguous.
- We never present an in-network proxy as a measured OON amount. The `basis`
  stamp and `oon_obs_n` make the strength of each figure legible.
- We do not redistribute AMA CPT descriptor text. The filter keys on bare
  5-digit `billing_code` numbers only and never reads any descriptor field. Our
  own plain-language labels (`therapy_codes.py`) remain the only labels shipped.
- We publish a periodic snapshot of a narrow slice — **not** a live index, and
  **not** anyone's raw claims data. The payer MRFs are the payers' own published
  files; v1 ships derived percentiles plus provenance, not the raw payer rows.

---

## Status / what's built here

| File | Status |
|------|--------|
| `README.md` (this file) | Design + operational runbook (done) |
| `filter_mrf.py` | Streaming filter scaffold; runnable via `--dry-run`; payer-specific parsing marked `TODO` |
| `payer_targets.md` | Initial 3 payers + index locations + OON caveats (done) |
| `oon_bench/ingest.py` | Real-payer ingest runner: index discovery, checkpoint/resume, gzip, `--limit`, `--dry-run`; per-payer schema quirks marked `TODO` |
| `oon_bench/ingest_config.example.toml` | Example UHC/Aetna/Cigna config (placeholder index URLs) (done) |
| aggregate to percentiles step | `oon_bench/aggregate.py` (done) |
| merge-into-schema step | `oon_bench/merge.py` (done) |

### Try the filter now

```bash
# Self-test against the bundled tiny fixture (no download, stdlib only):
python3 v1_tic/filter_mrf.py --dry-run

# Once you have a real (gzipped) MRF and ijson installed:
pip install ijson
python3 v1_tic/filter_mrf.py --payer aetna --kind in-network \
    --region-hint TX path/to/aetna_in-network_TX.json.gz -o aetna_tx.jsonl
```

### Streaming strategy (why `ijson`)

A correct, low-memory streaming parser for an arbitrary multi-GB JSON array is
hard to hand-roll in pure stdlib (you must tokenize element-by-element and track
nesting). The production path is therefore `pip install ijson`, which parses the
top-level array incrementally. The stdlib path (`json.load`) is reserved for
small fixtures and is hard-capped (`MAX_FALLBACK_BYTES`) so it can never be used
on a production-size file by accident. This keeps the *self-test* stdlib-only
while keeping production runs memory-flat.
