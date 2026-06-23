# OON Therapy Reimbursement Calculator

A self-contained, consumer-facing calculator that estimates what an insurance plan
might reimburse for **out-of-network (OON) outpatient therapy**. It is a single
HTML file (`index.html`) with inline CSS and JS — no build step, no framework, no
external CDNs except a Google Fonts `<link>`.

## What it does

You pick:

1. **Session type** — from the benchmark's CPT codes (shown with our own
   plain-language labels, e.g. "Individual therapy, 45 minutes").
2. **State**, then **area** — the locality dropdown is filtered to the chosen state.
3. **Out-of-network benefits?** — yes / no.
4. **Coinsurance %** — the share your plan pays after the deductible (default 70%).
5. **Deductible met?** — and, if not, roughly how much is left.

It then shows an estimated reimbursement **range** per visit, plus a "Show your
work" panel that cites the Medicare anchor, the placeholder OON multiplier band,
the data source and snapshot date, and the "estimate, not a guarantee" disclaimer.

### Edge states it handles explicitly

- **No OON benefits** → "Your plan likely won't reimburse out-of-network care."
- **Deductible not met (with a remaining balance)** → "$0 for now," with the
  after-deductible range previewed below.
- **Not enough input yet** → a gentle prompt instead of a number.
- **Loading** → a spinner while the dataset fetches.
- **Data failed to load** → a friendly explanation plus a manual-lookup fallback.

## Data source

At load, the page fetches the v0 dataset at:

```
../../data/therapy_oon_benchmark_v0.json
```

That file is produced by `build_baseline.py` in the repo root from public CMS
Physician Fee Schedule data (RVUs + GPCIs). The calculator reads only the
published JSON — it does no math the dataset doesn't already document. The OON
range is the locality's `oon_low_usd … oon_high_usd` band (Medicare × the
documented `1.0–2.0` placeholder multiplier) scaled by your coinsurance.

## How to run it locally

Because the page fetches a JSON file, browsers block it when you open the HTML
directly from disk (`file://`). Serve the repo over a tiny local web server
instead. From the **repo root** (`therapy-reimbursement-benchmark/`):

```bash
# Python 3 (no install needed)
python3 -m http.server 8000
```

Then open:

```
http://localhost:8000/examples/calculator/
```

Any static server works equally well, for example:

```bash
npx serve .          # Node
php -S localhost:8000  # PHP
```

If you open `index.html` directly with `file://`, the calculator detects the
failed fetch and shows the "we couldn't load the benchmark data" state with these
same instructions and a manual-lookup fallback.

## Privacy

Everything stays in the browser. No inputs are transmitted anywhere; there is no
analytics, no tracking, and no backend.

## Honesty & licensing

- **Estimate only, not a guarantee.** Actual reimbursement depends on your plan,
  your provider's billed amount, the plan's allowed amount, and claim processing.
  This is not medical, billing, or legal advice.
- The OON multiplier band in v0 is a **documented placeholder** (Medicare × 1–2),
  not a measured rate. v1 replaces it with Transparency-in-Coverage data.
- Underlying CMS RVU/GPCI values are U.S. Government works (public domain).
- **CPT® is a registered trademark of the American Medical Association.** This
  tool ships code numbers and our own plain-language labels, not AMA descriptor
  text.
