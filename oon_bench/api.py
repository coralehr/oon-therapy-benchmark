"""oon_bench.api — the read-only HTTP surface over the OON therapy benchmark.

This is the open-source "backend": a tiny FastAPI app that loads the merged v1
dataset ONCE at startup (falling back to the v0 calculator JSON so it boots even
before any TiC build has run) and answers per-code, per-region rate questions
purely from memory. No request handler touches the network or the disk — the
only I/O is the one-time load in :func:`build_store`.

Endpoints (the contract):

    GET /health
        -> {"status": "ok", "snapshot_date": "2026-06-07", "codes": 19}

    GET /v1/codes
        -> [{"cpt_code": "90837", "service_label": "...", "medicare_status": "A"}, ...]

    GET /v1/rates/{cpt}?region=CA
        -> the QUERY RESULT dict (see oon_bench.query.RateStore.get_rate):
           {cpt_code, service_label, region, basis,
            estimate:{low, mid, high}, confidence, n_obs, source,
            snapshot_date, disclaimer}
        -> 404 JSON {"detail": "..."} for an unknown CPT. An unknown *region*
           is not an error: it degrades to the national row (then to the
           Medicare-anchored fallback), exactly as the query layer specifies.

Dataset selection (resolved once, at startup):

    OON_V1_DATA env var, if set         (explicit override)
        else  data/v1/therapy_oon_benchmark_v1.json   (the merged v1 dataset)
        else  data/therapy_oon_benchmark_v0.json       (v0 fallback so the API
                                                        always boots)

Run it:

    pip install -r requirements-api.txt
    uvicorn oon_bench.api:app --reload
    # or:  python -m oon_bench.api

Every served figure carries its ``basis`` (how it was derived), a ``confidence``,
the observation count behind it, and a plain-language ``disclaimer`` — no
unattributed dollar amounts, and never an AMA CPT descriptor (only our own
plain-language ``service_label``).
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from .query import RateStore

# Resolve dataset locations relative to the repo root (the parent of this file's
# package directory), so the API runs the same from any working directory.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_PKG_DIR)
DEFAULT_V1_PATH = os.path.join(
    _REPO_ROOT, "data", "v1", "therapy_oon_benchmark_v1.json"
)
DEFAULT_V0_PATH = os.path.join(_REPO_ROOT, "data", "therapy_oon_benchmark_v0.json")


def resolve_dataset_path() -> str:
    """Pick the dataset file to load at startup.

    Priority:
      1. ``OON_V1_DATA`` env var (explicit override — used as-is, even if missing,
         so a misconfiguration surfaces loudly rather than silently serving v0).
      2. The merged v1 dataset, if it exists on disk.
      3. The v0 calculator JSON (guaranteed present) so the API always boots.
    """
    env_path = os.environ.get("OON_V1_DATA")
    if env_path:
        return env_path
    if os.path.exists(DEFAULT_V1_PATH):
        return DEFAULT_V1_PATH
    return DEFAULT_V0_PATH


def build_store(path: Optional[str] = None) -> RateStore:
    """Load a :class:`RateStore` from ``path`` (or the resolved default).

    Centralized so the app factory and tests construct the store identically.
    """
    return RateStore.from_file(path or resolve_dataset_path())


def create_app(store: Optional[RateStore] = None) -> FastAPI:
    """Build the FastAPI app, optionally with a pre-loaded store (for tests).

    When ``store`` is None the dataset is loaded once here, at app-construction
    time. Handlers close over the resulting store and never re-read it, keeping
    every request network- and disk-free.
    """
    if store is None:
        store = build_store()

    app = FastAPI(
        title="Therapy OON Reimbursement Benchmark API",
        version="1.0",
        summary="Read-only out-of-network reimbursement benchmark for outpatient therapy CPT codes.",
        description=(
            "Typical out-of-network reimbursement ranges for ~19 outpatient "
            "therapy CPT codes, by US state, derived from public CMS data and "
            "payers' published Transparency-in-Coverage files. Every figure "
            "carries its basis, confidence, and a disclaimer. Estimates only, "
            "not a guarantee of payment."
        ),
    )
    # Expose the store for tests / introspection without re-loading it.
    app.state.store = store

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        """Liveness + dataset stamp. Confirms the dataset loaded at startup."""
        return {
            "status": "ok",
            "snapshot_date": store.snapshot_date,
            "codes": len(store.list_codes()),
        }

    @app.get("/v1/codes", tags=["catalog"])
    def list_codes() -> list:
        """The code catalog: cpt_code + our plain-language label + Medicare status."""
        return store.list_codes()

    @app.get("/v1/rates/{cpt}", tags=["rates"])
    def get_rate(
        cpt: str,
        region: str = Query(
            "US",
            description=(
                "Two-letter US state (e.g. CA) for a state-level estimate, or 'US' "
                "for the national figure. Unknown/uncovered states fall back to "
                "national, then to the Medicare-anchored band."
            ),
            examples=["CA"],
        ),
    ) -> dict:
        """Resolve a single CPT x region to the QUERY RESULT dict (404 if unknown CPT)."""
        result = store.get_rate(cpt, region)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Unknown CPT code '{cpt}'. This benchmark covers ~19 outpatient "
                    f"therapy codes; see GET /v1/codes for the full list."
                ),
            )
        return result

    return app


# Module-level app for `uvicorn oon_bench.api:app`. Constructed at import time so
# the dataset loads once when the server process starts.
app = create_app()


def main() -> None:
    """Run the API with uvicorn. Honors HOST / PORT env vars (defaults 127.0.0.1:8000)."""
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
