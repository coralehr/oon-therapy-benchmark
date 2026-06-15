# Methodology

Version: `v0-medicare-2026A` · Snapshot: 2026-06-07

## Sources (all public domain, U.S. Government works)

| File | From | Used for |
|------|------|----------|
| `PPRRVU2026_Jan_nonQPP.csv` | CMS PFS RVU26A release | work / PE / MP RVUs, status, conversion factor |
| `GPCI2026.csv` | CMS PFS RVU26A release | geographic adjustment per Medicare locality |

Fetch: `./fetch_cms_data.sh` (downloads `rvu26a.zip` from cms.gov, extracts the two CSVs).

## The formula

Medicare allowed amount, **non-facility** (place of service 11 / office, which is
how an outpatient private-pay therapist bills):

```
allowed = (workRVU × workGPCI + peRVU_nonfac × peGPCI + mpRVU × mpGPCI) × CF
```

- Work GPCI uses the **1.0-floor** column (the value Medicare actually pays on).
- `CF` = the 2026 non-QPP conversion factor, `33.4009`, **read from the RVU file**
  (the pipeline asserts all codes share one CF and echoes the parsed value into the
  JSON `meta.conversion_factor`, so the published constant provably matches the math).
- National figures use GPCI = 1.0 (i.e. the file's NON-FACILITY TOTAL × CF).
- Scope is 19 therapy codes × 109 CMS localities = 2,071 localized rows.

## The out-of-network estimate (v0 — a documented assumption)

```
oon_low  = allowed × 1.0
oon_high = allowed × 2.0
```

This band is **not measured**. Real out-of-network plans reimburse a percentage of
the plan's *allowed amount*, and that allowed amount is set per payer (often a
multiple of Medicare, commonly ~1.1x–2.5x, sometimes a third-party UCR benchmark).
v0 uses a wide placeholder band and labels every row `basis = medicare_multiple`.

**This is the single number v1 makes real.** v1 derives the allowed amount from
payers' Transparency-in-Coverage machine-readable files (filtered to therapy CPTs
only) and reports actual percentiles per payer/region, replacing the placeholder.

Note on rounding: the band multiplies the **published (already rounded to the cent)**
Medicare figure, not the raw unrounded product. For a few codes that differs by $0.01
from rounding `raw × 2` once. This is internally consistent and immaterial given the
band is an explicit placeholder; it is called out here so a reader who recomputes does
not file a "off by a penny" issue.

## What v0 deliberately does NOT model

- The patient's own deductible, coinsurance %, or remaining benefit. Those are
  personal to a plan and a person and are not public. A reimbursement *to a
  specific patient* needs those inputs (self-reported, or via an eligibility check).
- Payer-specific allowed amounts (that's v1).
- Add-on/prescriber E&M-bundled codes (90833/36/38) — out of scope.

## Scope notes

- `90846` carries CMS status `R` (restricted); included with its RVUs, flagged in
  `medicare_status` across all three outputs.
- `96127` is a brief-assessment add-on; its Medicare amount is small (~$5) by design.
- `90785` (interactive complexity) is a low-dollar add-on but commonly billed; kept.
- The testing block (`96130/31/32/33/36/37`) is hourly/per-30-min; a real battery
  stacks multiple units, so a patient's testing superbill sums several of these rows.
- GPCI localities are CMS payment localities, not ZIP codes. ZIP→locality mapping
  (via `26LOCCO`) is a v1 nicety for a consumer calculator.

## Honesty rules baked into the data

- Every row carries `source`, `snapshot_date`, `methodology_version`, and `basis`.
- The JSON `meta.disclaimer` states plainly: estimate, not a guarantee; OON range is
  a placeholder until v1.
