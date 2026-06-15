# oon_bench — the read/serve layer

`oon_bench` is the consumer side of the Therapy OON Reimbursement Benchmark. The
build side produces a dataset on disk (`build_baseline.py` for the v0 Medicare
baseline; `v1_tic/` + the v1 build for the Transparency-in-Coverage enrichment).
`oon_bench` **loads that dataset once and answers per-code, per-region rate
questions** — as a function call (`oon_bench.query`) or over HTTP (`oon_bench.api`).

Nothing here fetches anything at request time. The dataset is read from disk at
startup and held in memory. Every figure returned carries its `basis` (how the
number was derived), a `confidence`, the observation count behind it, and a
plain-language `disclaimer`. We never ship AMA CPT descriptor text — only our own
plain-language `service_label` from `therapy_codes.py`.

## Modules

| Module | Role |
|--------|------|
| `oon_bench.query` | `RateStore` — loads the merged v1 JSON (or by-locality CSV, or the v0 JSON fallback) and answers `get_rate(cpt, region)` in memory. |
| `oon_bench.api`   | A small FastAPI app exposing the read-only HTTP endpoints below, backed by a `RateStore` loaded at startup. |
| `oon_bench.aggregate` / `oon_bench.schemas` | The v1 build stages (filter JSONL -> percentiles) and the shared data contract. |

## Run the HTTP API

```bash
# 1. Install the API-only dependencies (the data pipeline itself is stdlib-only).
python -m pip install -r requirements-api.txt

# 2. Run it (auto-reload for local dev).
uvicorn oon_bench.api:app --reload
#    or, equivalently:
python -m oon_bench.api
```

The server listens on `http://127.0.0.1:8000` by default. `python -m oon_bench.api`
honors `HOST` / `PORT` env vars.

### Which dataset does it serve?

Resolved once, at startup, in this order:

1. `OON_V1_DATA` env var, if set (explicit override — used as-is).
2. `data/v1/therapy_oon_benchmark_v1.json` — the merged v1 dataset, if present.
3. `data/therapy_oon_benchmark_v0.json` — the v0 calculator JSON, so the API
   **always boots**, even before any v1 build has run. (v0 rows serve as
   `medicare_multiple` / `low`-confidence fallbacks.)

```bash
# Serve a specific dataset:
OON_V1_DATA=/abs/path/to/therapy_oon_benchmark_v1.json uvicorn oon_bench.api:app
```

## Endpoints

### `GET /health`
Liveness + dataset stamp.

```json
{ "status": "ok", "snapshot_date": "2026-06-07", "codes": 19 }
```

### `GET /v1/codes`
The code catalog (our plain-language labels).

```json
[
  { "cpt_code": "90791", "service_label": "Diagnostic intake / first evaluation", "medicare_status": "A" },
  { "cpt_code": "90837", "service_label": "Individual therapy, 60 minutes", "medicare_status": "A" }
]
```

### `GET /v1/rates/{cpt}?region=CA`
A single code × region estimate. `region` is a two-letter US state, or `US` for
national (the default). Unknown / uncovered states fall back to national, then to
the Medicare-anchored band — never an error. An **unknown CPT returns `404`**.

```json
{
  "cpt_code": "90837",
  "service_label": "Individual therapy, 60 minutes",
  "region": "CA",
  "basis": "tic_oon_actual",
  "estimate": { "low": 165.0, "mid": 198.0, "high": 230.0 },
  "confidence": "high",
  "n_obs": 512,
  "source": "Transparency-in-Coverage (multi)",
  "snapshot_date": "2026-06-07",
  "disclaimer": "Estimate only, not a guarantee of payment. ..."
}
```

`basis` is one of `tic_oon_actual` (measured OON allowed amounts) >
`tic_innetwork_proxy` (in-network rate used as a proxy) > `medicare_multiple`
(the Medicare placeholder band). `confidence` maps from `(basis, n_obs)`:
measured OON with a healthy sample is `high`; a proxy or a thin OON sample is
`medium`; the Medicare band is `low`.

## Example `curl`

```bash
# Is it up, and which snapshot is it serving?
curl -s http://127.0.0.1:8000/health

# What codes are covered?
curl -s http://127.0.0.1:8000/v1/codes

# A 60-minute individual therapy estimate for California:
curl -s "http://127.0.0.1:8000/v1/rates/90837?region=CA"

# National (default region):
curl -s http://127.0.0.1:8000/v1/rates/90837

# Unknown code -> 404 with a JSON body:
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/v1/rates/99999
```

Interactive docs (Swagger UI) are served at `http://127.0.0.1:8000/docs` once the
server is running.

## Tests

```bash
python -m pip install -r requirements-api.txt
python -m pytest tests/v1/test_api.py tests/v1/test_query.py -q
```

`tests/v1/test_api.py` is guarded by `pytest.importorskip("fastapi")`, so it is
skipped (not failed) wherever FastAPI is not installed. `tests/v1/test_query.py`
covers the `RateStore` query layer with stdlib + pytest only.

## What this is and isn't

- A **read-only** benchmark API: public CMS data + payers' published
  Transparency-in-Coverage data, surfaced with full provenance.
- **Not** medical, billing, or legal advice, and **not** a guarantee of payment.
- **Not** anyone's individual claims data — it serves derived percentiles plus
  provenance, never raw payer rows or AMA descriptor text.
