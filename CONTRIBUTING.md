# Contributing

Thanks for helping improve the Therapy Out-of-Network Reimbursement Benchmark.
This is a small, deliberately narrow dataset: public CMS data, transparent
formula, one vertical (outpatient therapy). Contributions should keep it that
way — credible, reproducible, and honest about what it is.

Please read [`METHODOLOGY.md`](METHODOLOGY.md) before proposing changes to the
formula or the data.

## Ground rules (read these first)

### 1. The AMA CPT-descriptor rule — non-negotiable

CPT(R) is a registered trademark of the American Medical Association, and the
official CPT code descriptors are copyright the AMA.

- **Never** add AMA CPT descriptor text to any committed file — not in
  `therapy_codes.py`, not in CSV/JSON outputs, not in docs, not in tests.
- Service labels in `therapy_codes.py` MUST be **our own original
  plain-language wording**, not the AMA descriptor. Describe the service in
  everyday language (e.g. `"Individual therapy, 45 minutes"`), not the
  official short/long descriptor.
- The build pipeline reads CMS RVU/GPCI columns by positional index and
  **must never read the PPRRVU `DESCRIPTION` column** (index 2). If you touch
  `load_rvus()`, keep it that way — AMA text must be structurally incapable of
  reaching an output file.
- Shipping the bare five-digit code numbers plus CMS RVU facts is fine. See
  [`LICENSE-DATA`](LICENSE-DATA) for the full caveat.

A pull request that introduces AMA descriptor text will not be merged.

### 2. Public data only

Every committed figure must trace back to a public CMS source file and the
documented formula. Do not add private payer data, real claims data, or any
PHI. v0 is Medicare-anchored; the out-of-network band is a documented
placeholder (Medicare x [1.0, 2.0]) that v1 replaces with
Transparency-in-Coverage-derived percentiles.

### 3. Keep it reproducible

Anyone should be able to delete `data/raw/` and regenerate the committed
outputs from scratch with the two commands in [Rebuild](#rebuild-the-dataset).
The build uses the Python standard library only — do not add third-party
runtime dependencies to `build_baseline.py`.

## Adding or changing CPT codes

The full scope lives in [`therapy_codes.py`](therapy_codes.py) as a single
list, `THERAPY_CODES`. To add a code:

1. **Confirm it belongs.** This benchmark targets the non-prescriber,
   outpatient private-pay therapy practice (LCSW/LMFT/LPC/PhD/PsyD). Good
   candidates: psychological/neuropsychological testing (96130–96137),
   interactive-complexity add-on (90785), psychoanalysis (90845). Out of
   scope: prescriber psychotherapy-with-E/M add-ons (90833/90836/90838).

2. **Verify it is Medicare-payable.** Open the CMS source
   `data/raw/PPRRVU2026_Jan_nonQPP.csv` (after running `fetch_cms_data.sh`)
   and confirm the code has a clean **blank-modifier** row with **status A**
   (active) and a non-empty non-facility total RVU. Codes that are
   non-covered (status N, e.g. biofeedback 90875/90876) or have no Medicare
   anchor cannot be benchmarked by this Medicare-multiple method — leave them
   out.

3. **Add the entry** to `THERAPY_CODES` with the code number and an **original
   plain-language label** (NOT the AMA descriptor — see rule 1):

   ```python
   {"code": "96130", "label": "Psychological testing evaluation by clinician, first hour"},
   ```

4. **Rebuild and review.** Run the pipeline (below). Sanity-check the new
   row's Medicare amount against the source RVUs: it should equal
   `total_nonfac * CF` at the national level.

5. **Flag edge cases honestly.** If a code is Medicare-restricted (status R,
   e.g. 90846) or a tiny add-on (e.g. 96127), note it in `METHODOLOGY.md`
   under "Known limitations" so consumers understand the figure.

## Rebuild the dataset

Re-run quarterly when CMS publishes a new RVU release (RVU26A, RVU26B, ...),
or whenever you change the code list:

```bash
./fetch_cms_data.sh        # downloads + extracts the public CMS RVU + GPCI files into data/raw/
python3 build_baseline.py  # stdlib only; rewrites data/*.csv + data/*.json
```

`fetch_cms_data.sh` accepts an optional release-URL argument if you need a
newer CMS zip than the default:

```bash
./fetch_cms_data.sh https://www.cms.gov/files/zip/rvu26b.zip
```

Commit the regenerated `data/*.csv` and `data/*.json` — those compiled outputs
**are** the deliverable and are intentionally tracked in git. The raw CMS files
under `data/raw/` are **not** committed (they are re-fetchable; see
`.gitignore`).

## Run the tests

```bash
python3 -m pytest -q
```

Tests should confirm the pipeline math and the integrity of the committed
outputs (e.g. every code resolves, no AMA descriptor text leaks into outputs,
the OON band is applied correctly). If you change the formula, the code list,
or an output schema, update or add tests in the same pull request.

If you add Python, also run a linter before pushing:

```bash
ruff check .
```

## Pull request checklist

- [ ] No AMA CPT descriptor text in any committed file (rule 1).
- [ ] New/changed labels are our own original plain-language wording.
- [ ] Every figure traces to a public CMS source + the documented formula.
- [ ] `build_baseline.py` stays standard-library only.
- [ ] Dataset rebuilt; `data/*.csv` and `data/*.json` regenerated and committed.
- [ ] `python3 -m pytest -q` passes; tests updated for any behavior change.
- [ ] `METHODOLOGY.md` / `CHANGELOG.md` updated if methodology or scope changed.
