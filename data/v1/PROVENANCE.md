# v1 dataset provenance

- **Snapshot:** UHC Transparency-in-Coverage files dated 2026-06-01.
- **Method:** 40 real UHC in-network-rates plan files (1-8 MB band) streamed through
  `v1_tic/filter_mrf.py`, pooled, aggregated (MIN_N>=10), merged over the v0 Medicare
  baseline. ~2,982 professional therapy negotiated rates; n=148-180 per code.
- **basis = `tic_innetwork_proxy`** (national rows). In-network negotiated rates are
  used as the out-of-network proxy because payers' actual OON allowed-amount files are
  effectively empty (UHC's largest is 17 KB). See README / METHODOLOGY.
- **Scope caveat:** NATIONAL only. `region=null` on source rows (no provider-reference
  resolution yet), so per-CMS-locality rows remain `medicare_multiple`; only the
  national row carries the real proxy. Per-state needs provider-reference + NPI->state
  (NPPES) resolution — the next engineering step.
- **Not comprehensive:** a 40-plan sample of one payer (UHC), not an all-payer build.
