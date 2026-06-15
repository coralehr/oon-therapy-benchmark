# Therapy Out-of-Network Reimbursement Benchmark

An open-source **backend** for typical out-of-network reimbursement amounts for
outpatient **therapy** CPT codes, by geography. Built from public data, shows its
work, scoped to one vertical on purpose.

Most out-of-network estimators are black boxes that emit a number and ask you to
trust it. This one is transparent: every figure carries a `basis`, an observation
count, and a source, and traces back to a public file and a documented formula.

The product is the backend (a Python library + CLI + read-only HTTP API), not a UI.
A demo calculator lives in `examples/` but is not the deliverable.

## The backend (`oon_bench`)

```bash
pip install -e ".[api]"                      # editable install + API deps
uvicorn oon_bench.api:app                     # serve the HTTP API
curl 'localhost:8000/v1/rates/90837?region=CA'
```

Or from Python / the CLI:

```python
from oon_bench import get_rate
get_rate("90837", "CA")   # -> {basis, estimate{low,mid,high}, confidence, n_obs, source, ...}
```

```bash
python -m oon_bench query 90837 --region CA
```

The backend serves the v0 Medicare-anchored dataset out of the box, and the richer
v1 Transparency-in-Coverage numbers once a TiC build has produced `data/v1/`.
Pipeline + surfaces are documented in [oon_bench/README.md](oon_bench/README.md) and
[v1_tic/README.md](v1_tic/README.md).

## The dataset (what the backend serves)

`data/therapy_oon_benchmark_v0_national.csv` — one row per therapy code, national.
`data/therapy_oon_benchmark_v0_by_locality.csv` — code x CMS locality (~109 localities).
`data/therapy_oon_benchmark_v0.json` — calculator-friendly, nested by code.

Scope is the bread-and-butter of a private-pay practice (19 codes, see
`therapy_codes.py`): individual (90832/34/37), intake (90791/92), family (90846/47),
group (90853), crisis (90839/40), brief assessment (96127), psychological and
neuropsychological **testing** (96130/31/32/33/36/37), psychoanalysis (90845), and
the interactive-complexity add-on (90785). That gives 19 codes × 109 CMS localities =
2,071 localized rows.

### v0 national snapshot (2026, non-facility / office)

| CPT | Service | Medicare | OON range* |
|-----|---------|---------:|-----------:|
| 90791 | Diagnostic intake | $173.35 | $173–$347 |
| 90834 | Individual therapy, 45 min | $113.90 | $114–$228 |
| 90837 | Individual therapy, 60 min | $167.00 | $167–$334 |
| 90847 | Family therapy, with client | $109.55 | $110–$219 |
| 90853 | Group therapy | $30.39 | $30–$61 |
| 96132 | Neuropsych testing eval, 1st hr | $122.25 | $122–$245 |

\* v0 OON range is Medicare x a documented placeholder band (1.0–2.0x). It is an
assumption, not a measurement. **v1 replaces it with real numbers** (see Roadmap).

## How it works

```
Medicare allowed (non-facility) = (workRVU·workGPCI + peRVU·peGPCI + mpRVU·mpGPCI) × CF
OON estimate range              = Medicare × [oon_mult_low, oon_mult_high]
```

CF (2026 conversion factor) = `33.4009`, read from the CMS file. Full detail in
[METHODOLOGY.md](METHODOLOGY.md).

## Example consumer (a calculator, not the product)

A self-contained demo calculator lives in `examples/calculator/`. It is a reference
consumer of the dataset, not the open-source deliverable (the backend is). It reads
the v0 JSON and shows an honest reimbursement range with a "show your work" panel:

```bash
python3 -m http.server 8000        # from the repo root
# then open http://localhost:8000/examples/calculator/
```

It runs entirely in the browser and sends nothing anywhere. See
`examples/calculator/README.md`.

## Rebuild the v0 dataset

```bash
./fetch_cms_data.sh        # downloads public CMS RVU + GPCI files
python3 build_baseline.py  # stdlib only, writes data/*.csv + .json
python3 -m pytest -q        # full suite (v0 + v1 backend)
```

## Run the v1 pipeline on the bundled fixtures

No real payer data needed — synthetic MRFs prove the whole pipeline end to end:

```bash
python -m oon_bench.run_local   # filter -> aggregate -> merge -> sample queries
```

To run against real payers (the terabyte-scale operational step), see
[v1_tic/README.md](v1_tic/README.md) and `oon_bench/ingest.py`.

## Documentation

- [oon_bench/README.md](oon_bench/README.md) — the backend: library, CLI, HTTP API.
- [METHODOLOGY.md](METHODOLOGY.md) — the v0 formula, sources, and placeholder band.
- [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) — every column and JSON field.
- [docs/positioning.md](docs/positioning.md) — why therapy-only, why snapshots.
- [docs/scope-recommendations.md](docs/scope-recommendations.md) — code-scope rationale.
- [v1_tic/README.md](v1_tic/README.md) — the Transparency-in-Coverage enrichment + real-payer run.
- [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md)

## Roadmap

- **v0 (shipped): Medicare-anchored.** Firm Medicare numbers + a placeholder OON
  band. Public CMS data only. No payer data, no treadmill.
- **v1 (backend built, this release): Transparency-in-Coverage enrichment.** The
  pipeline (filter → aggregate → percentiles → merge → query/API) is built and tested
  end to end on synthetic MRF fixtures, with strict basis precedence
  `tic_oon_actual > tic_innetwork_proxy > medicare_multiple`. The remaining step is the
  operational run against real payer files (UHC/Aetna/Cigna) to produce `data/v1/` —
  a narrow therapy slice on quarterly snapshots, not a live index.

Why a narrow slice and periodic snapshots: the general price-transparency space is
a terabyte-scale, monthly-changing treadmill that has killed every volunteer who
tried to boil the whole ocean. We boil a teacup, on a schedule. The thin therapy
filter rejects ~99.99% of each payer file, so terabytes collapse to a few MB.

## What this is and isn't

- This is the **commodity** layer: public data, free, transparent. Take it, use it.
- It is **not** anyone's real claims data. Actual paid-reimbursement data stays
  with the systems that process the claims.
- It is **not** medical, billing, or legal advice. Estimates only, not guarantees.

## Licensing

- Code: MIT — see [LICENSE](LICENSE).
- Compiled dataset: CC-BY-4.0 — see [LICENSE-DATA](LICENSE-DATA). CC-BY attaches to the
  dataset's selection, labels, and arrangement; the underlying CMS RVU/GPCI numeric
  values are U.S. Government works in the public domain (17 U.S.C. 105).
- **CPT® is a registered trademark of the American Medical Association.** This repo
  ships code numbers, CMS RVU facts, and our own plain-language labels. It does **not**
  redistribute AMA CPT descriptor text. License CPT from the AMA before shipping
  official descriptors at scale.
