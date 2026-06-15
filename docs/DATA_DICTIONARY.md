# Data Dictionary

This file documents every field in the three shipped data artifacts. All dollar
amounts are in U.S. dollars (USD), rounded to two decimal places, and reflect the
snapshot stamped on each row. The snapshot for this release is `2026-06-07` and the
methodology version is `v0-medicare-2026A`.

A reminder on what the numbers mean: `medicare_*` fields are firm Medicare Physician
Fee Schedule non-facility allowed amounts computed from public CMS data. The `oon_*`
fields are estimates produced by multiplying the Medicare amount by a placeholder
band (1.0x to 2.0x). The band is a documented assumption, not a measurement, and is
the single thing v1 replaces with Transparency-in-Coverage data. See
[METHODOLOGY.md](../METHODOLOGY.md).

---

## File 1: `data/therapy_oon_benchmark_v0_national.csv`

One row per therapy CPT code (19 rows + header). National figures, computed with all
geographic practice cost indices set to 1.0 (the file's non-facility total times the
conversion factor).

| Column | Type | Units | Meaning |
|--------|------|-------|---------|
| `cpt_code` | string | — | The 5-digit CPT code (e.g. `90837`). Code numbers only; no AMA descriptor text is shipped. |
| `service_label` | string | — | Our own plain-language label for the service (e.g. `Individual therapy, 60 minutes`). NOT the AMA CPT descriptor. Source of truth is `therapy_codes.py`. |
| `medicare_status` | string (enum) | — | The CMS PFS status indicator for the code. See the `medicare_status` enum below. |
| `medicare_nonfacility_usd` | number | USD | The Medicare PFS non-facility (office, place of service 11) allowed amount. Firm figure computed from CMS RVUs and the conversion factor. |
| `oon_estimate_low_usd` | number | USD | Low end of the out-of-network estimate band. Equals `medicare_nonfacility_usd` times `oon_mult_low`. In v0 this equals the Medicare amount. |
| `oon_estimate_high_usd` | number | USD | High end of the out-of-network estimate band. Equals `medicare_nonfacility_usd` times `oon_mult_high`. In v0 this is twice the Medicare amount. |
| `basis` | string (enum) | — | How the OON estimate was derived. See the `basis` enum below. Always `medicare_multiple` in v0. |
| `oon_mult_low` | number | multiplier | The low multiplier applied to the Medicare amount. `1.0` in v0 (placeholder). |
| `oon_mult_high` | number | multiplier | The high multiplier applied to the Medicare amount. `2.0` in v0 (placeholder). |
| `source` | string | — | The CMS source file used for the RVU figures (e.g. `CMS PFS RVU26A / PPRRVU2026_Jan_nonQPP.csv`). |
| `snapshot_date` | string (ISO date) | — | The date this snapshot was built (`YYYY-MM-DD`). |
| `methodology_version` | string | — | The methodology version tag (e.g. `v0-medicare-2026A`). |

---

## File 2: `data/therapy_oon_benchmark_v0_by_locality.csv`

One row per CPT code per CMS payment locality (2071 rows + header = 19 codes x 109
localities). Figures are geographically adjusted using each locality's GPCIs.

| Column | Type | Units | Meaning |
|--------|------|-------|---------|
| `cpt_code` | string | — | The 5-digit CPT code. |
| `service_label` | string | — | Our plain-language label (same as the national file). |
| `medicare_status` | string (enum) | — | The CMS PFS status indicator (same value as the national file for this code). See the `medicare_status` enum below. Propagated here so consumers of the locality file alone can see that, e.g., `90846` is `R`. |
| `state` | string | — | Two-letter state abbreviation for the locality (e.g. `AL`, `CA`). |
| `locality_name` | string | — | The CMS payment-locality name (e.g. `ALABAMA`, `LOS ANGELES`). A trailing `*` reflects the name as published in the CMS GPCI file (e.g. `ALASKA*`); it carries no semantic meaning in this dataset. |
| `medicare_nonfacility_usd` | number | USD | The locality-adjusted Medicare PFS non-facility allowed amount: `(workRVU x pwGPCI + peRVU_nonfac x peGPCI + mpRVU x mpGPCI) x CF`. |
| `oon_estimate_low_usd` | number | USD | Low end of the OON band for this locality. Equals the locality Medicare amount times `1.0` in v0. |
| `oon_estimate_high_usd` | number | USD | High end of the OON band for this locality. Equals the locality Medicare amount times `2.0` in v0. |
| `basis` | string (enum) | — | Always `medicare_multiple` in v0. See the `basis` enum below. |
| `snapshot_date` | string (ISO date) | — | The build date of this snapshot. |
| `methodology_version` | string | — | The methodology version tag. |

Note: the by-locality CSV does not carry `oon_mult_low`, `oon_mult_high`, or `source`
columns. Those are present in the national CSV. The multipliers are constant across
the dataset (1.0 and 2.0 in v0) and the source is documented in
[METHODOLOGY.md](../METHODOLOGY.md). `medicare_status` IS carried here (it varies by
code and is needed by consumers of the locality file alone).

---

## File 3: `data/therapy_oon_benchmark_v0.json`

The calculator-friendly artifact: a single `meta` block plus a `codes` array nested
by code, each code carrying its national figure and its full list of localities.

### `meta` object

| Field | Type | Units | Meaning |
|-------|------|-------|---------|
| `methodology_version` | string | — | The methodology version tag (e.g. `v0-medicare-2026A`). |
| `snapshot_date` | string (ISO date) | — | The build date of this snapshot. |
| `conversion_factor` | number | USD per RVU | The CMS PFS conversion factor used to turn total RVUs into dollars (`33.4009` for 2026 non-QPP). |
| `oon_multiplier_band` | array of two numbers | multiplier | The `[low, high]` OON multiplier band applied to Medicare. `[1.0, 2.0]` in v0 (placeholder). |
| `basis` | string (enum) | — | How OON estimates are derived. Always `medicare_multiple` in v0. |
| `sources` | array of strings | — | The CMS source files used (RVU and GPCI). |
| `disclaimer` | string | — | Plain-language disclaimer: estimate only, not a guarantee; the OON range is a placeholder band until v1. |

### `codes[]` array (one object per CPT code)

| Field | Type | Units | Meaning |
|-------|------|-------|---------|
| `cpt_code` | string | — | The 5-digit CPT code. |
| `service_label` | string | — | Our plain-language label. |
| `medicare_status` | string (enum) | — | The CMS PFS status indicator for the code. The calculator uses this to surface a "Medicare-restricted" note (e.g. for `90846`). See the `medicare_status` enum below. |
| `national` | object | — | The national figures for this code. See `national` object below. |
| `localities` | array of objects | — | One entry per CMS payment locality (109 entries). See `localities[]` object below. |

### `codes[].national` object

| Field | Type | Units | Meaning |
|-------|------|-------|---------|
| `medicare_usd` | number | USD | The national Medicare PFS non-facility allowed amount. |
| `oon_low_usd` | number | USD | Low end of the national OON estimate band (Medicare times `oon_mult_low`). |
| `oon_high_usd` | number | USD | High end of the national OON estimate band (Medicare times `oon_mult_high`). |

### `codes[].localities[]` object

| Field | Type | Units | Meaning |
|-------|------|-------|---------|
| `state` | string | — | Two-letter state abbreviation for the locality. |
| `locality_name` | string | — | The CMS payment-locality name (a trailing `*` is as published by CMS). |
| `medicare_usd` | number | USD | The locality-adjusted Medicare PFS non-facility allowed amount. |
| `oon_low_usd` | number | USD | Low end of the locality OON band (locality Medicare times `oon_mult_low`). |
| `oon_high_usd` | number | USD | High end of the locality OON band (locality Medicare times `oon_mult_high`). |

---

## Enum: `basis`

The `basis` field declares how each OON estimate was produced. It exists so a
consumer never has to guess whether a figure is measured or modeled.

| Value | Meaning |
|-------|---------|
| `medicare_multiple` | The OON estimate is the Medicare allowed amount multiplied by the documented multiplier band. This is a modeled placeholder, not a measurement of actual OON payments. It is the only value present in v0. |

Planned values for v1 (not present in v0, documented here so the enum is
forward-stable; these match the scheme in [v1_tic/README.md](../v1_tic/README.md)):

| Value | Meaning |
|-------|---------|
| `tic_oon_actual` | The estimate is derived from payers' Transparency-in-Coverage out-of-network allowed-amount files, reported as actual percentiles per payer/region. The real target. |
| `tic_innetwork_proxy` | Derived from payers' in-network negotiated-rate files used as a richer proxy where OON allowed-amount data is too sparse. |
| `medicare_multiple` | The v0 fallback (above), retained in v1 for codes/regions with insufficient TiC data. |

A consumer should treat `tic_oon_actual` / `tic_innetwork_proxy` rows as measured
figures and any `medicare_multiple` row as a modeled estimate.

---

## Enum: `medicare_status`

The CMS PFS status indicator, carried in the national CSV. It tells you whether
Medicare separately pays the code under the Physician Fee Schedule. The RVUs and the
computed amounts are present regardless of status; the flag is the honest disclosure
that some codes are not separately payable by Medicare even though we can still
compute a Medicare-anchored figure.

| Value | Meaning |
|-------|---------|
| `A` | Active. Separately payable under the PFS. Most therapy codes carry `A`. |
| `R` | Restricted. Special coverage instructions apply; not separately payable in the ordinary case. In this dataset `90846` (family therapy without the client present) carries `R`. Its dollar figure is model-computed from RVUs, not a routine Medicare-payable allowed amount. |

Other CMS status indicators exist in the source PPRRVU file (for example `N` for
non-covered, `I` for not valid for Medicare, `T` for injections). None of those
appear in the shipped therapy scope for v0; only `A` and `R` are present. If a future
snapshot adds a code with a different status, that value will surface here unchanged
from CMS.
