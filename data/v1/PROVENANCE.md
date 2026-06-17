# v1 dataset provenance

- **Snapshot:** UHC Transparency-in-Coverage files dated 2026-06-01.
- **Method:** 40 real UHC in-network-rates plan files (1-8 MB band) streamed through
  `v1_tic/filter_mrf.py`, pooled, aggregated (MIN_N>=10), merged over the v0 Medicare
  baseline. ~2,982 professional therapy negotiated rates; n=148-180 per code.
- **basis = `tic_innetwork_proxy`**. In-network negotiated rates are used as the
  out-of-network proxy because payers' actual OON allowed-amount files are effectively
  empty (UHC's largest is 17 KB). See README / METHODOLOGY.
- **Per-locality via geo-blend** (`geo_method = medicare_gpci_blend`): the measured
  signal is the NATIONAL in-network/Medicare ratio per code (e.g. 90837 ~0.82). Each
  CMS locality's number is that real ratio scaled by the locality's Medicare amount
  (which already carries GPCI). So the rate signal is real data; the geographic
  variation is Medicare's GPCI. National rows are `geo_method = measured`.
  Example: 90837 median runs AL $133 / CA $142 / NY $150 / US $137.
- **Why not per-state from the MRF directly:** in-network rates are negotiated at the
  provider-group / TIN level, and groups are frequently multi-state, so attributing a
  single rate to one state is inherently fuzzy and would require resolving tens of
  thousands of NPIs through NPPES. The geo-blend is the honest, cheaper alternative.
- **Not comprehensive:** a 40-plan sample of one payer (UHC), not an all-payer build.
  Broadening (more plans/payers) tightens the national ratio the localities inherit.
