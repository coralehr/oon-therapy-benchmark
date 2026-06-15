"""oon_bench — v1 Transparency-in-Coverage (TiC) enrichment for the OON therapy benchmark.

This package is the v1 build pipeline that turns the streaming MRF filter's JSONL
output (``v1_tic/filter_mrf.py``) into measured out-of-network allowed-amount
percentiles, merges them onto the v0 Medicare baseline by CMS locality, and serves
the result over a small read-only HTTP API.

The package is deliberately layered so each stage has a single, testable contract
(see ``oon_bench.schemas`` for the shared types + math that every stage imports):

    filter (JSONL FilterRow)            v1_tic/filter_mrf.py   -> upstream, not in this pkg
        -> aggregate (AggregateRecord)  oon_bench.aggregate    -> percentiles per code x region
        -> merge     (MergedRow)        oon_bench.merge        -> extend v0 by-locality grid
        -> query     (QueryResult)      oon_bench.query        -> get_rate(cpt, region)
        -> api       (FastAPI)          oon_bench.api          -> GET /v1/rates/{cpt}

Every served figure carries provenance: a ``basis`` (tic_oon_actual >
tic_innetwork_proxy > medicare_multiple), the observation count behind it, and a
snapshot date. We never present an in-network proxy as a measured OON amount, and
we never ship AMA CPT descriptor text — only our own plain-language labels from
``therapy_codes.py``.

Stdlib only for the data stages; the HTTP API layer may use FastAPI + uvicorn.
"""

from .query import RateStore, get_rate

__version__ = "0.1.0"
__all__ = ["RateStore", "get_rate", "__version__"]
