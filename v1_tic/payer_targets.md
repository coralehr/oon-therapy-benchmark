# v1 payer targets

The three initial payers to filter for the v1 therapy OON benchmark, where each
publishes its Transparency-in-Coverage (TiC) index, and the data-quality caveats
that matter specifically for the **out-of-network allowed-amount** files.

We start with three because they are the largest national commercial payers,
which means (a) the widest therapy-CPT coverage across regions and (b) the most
OON claim volume, so their allowed-amount files have the best chance of clearing
`MIN_N` for `tic_oon_actual`. Add more payers only after the pipeline is proven
on these.

> **No URLs are downloaded by anything in this repo.** This file documents the
> published *entry points* so a future, deliberate run can fetch them. Payers
> move these paths; always confirm the current index location from the payer's
> public "Transparency in Coverage" / "machine-readable files" page before a run.

---

## Why these three

| # | Payer | Why first |
|---|-------|-----------|
| 1 | **UnitedHealthcare / Optum** | Largest US commercial membership → broadest code × region coverage and the deepest OON allowed-amount volume. Publishes a well-structured (very large) index. |
| 2 | **Aetna (CVS Health)** | Large national footprint; cleanly structured CMS-schema MRFs; commonly used as a reference implementation for TiC parsers. |
| 3 | **Cigna** | National commercial scale; independent third data point so percentiles aren't dominated by a single payer's contracting. |

These three give a defensible multi-payer percentile (`payer_scope =
uhc+aetna+cigna`) without the long tail of regional Blues plans, which can be a
later expansion.

---

## Where to find each payer's TiC index

Each payer publishes a public "Transparency in Coverage" landing page that links
the **Table-of-Contents (index) file**. The index is the only stable entry
point — individual in-network and allowed-amount file URLs are generated per
publish and rotate, so always start from the index, not a bookmarked file URL.

### 1. UnitedHealthcare / Optum
- **Public page:** UnitedHealthcare "Transparency in Coverage" machine-readable
  files page (`transparency-in-coverage.uhc.com`).
- **Index → file links:** the index lists in-network-rate files and
  allowed-amount (OON) files, typically chunked by plan/EIN. The index itself is
  large and should be **streamed**, not loaded whole.
- **Discovery note:** filter the index to the in-network-rate and
  allowed-amount entries for the plan(s)/region(s) you want; ignore the rest.

### 2. Aetna (CVS Health)
- **Public page:** Aetna "Transparency in Coverage" MRF page
  (`health1.aetna.com` / Aetna's TiC portal).
- **Index → file links:** standard CMS-schema TOC pointing at in-network-rate
  files and allowed-amount files, generally gzipped.
- **Discovery note:** Aetna's files are usually close to the CMS reference
  schema, which makes them the best first integration test for `filter_mrf.py`'s
  in-network path.

### 3. Cigna
- **Public page:** Cigna "Transparency in Coverage" MRF page
  (`cigna.com` transparency-in-coverage section).
- **Index → file links:** TOC listing in-network and allowed-amount files; often
  partitioned by plan/market.
- **Discovery note:** confirm whether Cigna publishes a separate OON
  allowed-amount file set vs. only in-network for the markets you care about
  before committing to `tic_oon_actual` there.

> **Verify before fetching:** payers periodically restructure these pages and the
> index schema. Pull a small `head -c` sample of any index/MRF and confirm the
> top-level shape (array key, gzip, per-file chunking) against
> `filter_mrf.py`'s assumptions. Several `TODO(payer-specific)` markers in that
> file flag exactly where shapes diverge.

---

## Data-quality caveats — OON allowed-amount files

These are the reasons the OON allowed-amount file is the *real target* but
cannot be trusted blindly. The filter and the (TODO) percentile stage must
account for each:

1. **Sparsity / coverage gaps.** OON allowed-amount files only carry codes ×
   regions with sufficient OON claim history. A given payer may simply have **no
   rows** for `90846` in a small state. This is exactly why v1 falls back to the
   in-network proxy, and then to the Medicare band, with the `basis` stamp making
   the fallback explicit. → enforce `MIN_N`.

2. **Region frequently absent.** OON files often omit provider geography, so many
   observations can only feed the **national** percentile, not a state one. Do
   not invent a region; emit `region=None` and aggregate nationally. (The filter
   does this; `--region-hint` only applies when a per-state *file* implies the
   region.)

3. **Mixed units / facility vs. professional.** Allowed amounts can mix
   professional and facility claims, and occasionally include amounts that aren't
   per-session. The v0 benchmark is **non-facility professional** (office). Keep
   `billing_class` where present and **drop facility/non-professional rows** at
   the percentile stage; treat missing `billing_class` cautiously.

4. **Outliers and bad values.** Expect `0.00`, negative, or absurd values
   (data-entry artifacts, reversals). Drop non-positive amounts and clip extreme
   outliers (e.g. beyond a generous multiple of the median) before computing
   percentiles. Record how many rows were dropped.

5. **Stale / lagged data.** Allowed-amount files reflect *historical* claims over
   a payer-defined lookback window that may lag the in-network file's effective
   period. Stamp the payer's published snapshot date per payer in `meta`; do not
   assume all three payers share a period.

6. **Bundling / capitation contamination (in-network proxy).** When falling back
   to the in-network file, only `negotiation_arrangement = ffs` rows are
   per-session-meaningful. Exclude `bundle` and `capitation`. The filter keeps
   `negotiation_arrangement` so the percentile stage can enforce this.

7. **Provider-reference indirection.** In-network rates may attach to a
   `provider_group_id` defined in a *separate* provider-reference file. Resolving
   it to a region needs a join we do **not** perform in the scaffold (marked
   `TODO`); until then, region for those rows comes from the filename hint or is
   national.

8. **Duplicate / overlapping plans.** The same negotiated/allowed amount can
   appear across many plan files for one payer, over-weighting it. Dedupe by
   `(payer, billing_code, region, amount, source-tin)` where a TIN is available
   before computing percentiles, so one contract isn't counted dozens of times.

9. **File size and republish cadence.** Each payer republishes monthly and the
   index alone can be very large. v1 takes a **quarterly** snapshot and streams
   the index; never store the full file set. (See README "teacup, not the
   ocean.")

---

## Per-payer integration order (suggested)

1. **Aetna in-network** first — closest to the CMS reference schema, best for
   validating `filter_mrf.py`'s in-network path end-to-end.
2. **Aetna allowed-amounts** — exercise the OON path and the region-absent
   handling.
3. **UHC**, then **Cigna** — broaden coverage and confirm the schema TODOs hold
   (provider-reference joins, chunking) across payers.

Each integration should land a small committed **fixture** (a few synthetic
records in the payer's exact shape, *not* real payer data) so the parser's
payer-specific branch is regression-tested without re-downloading multi-GB files.
