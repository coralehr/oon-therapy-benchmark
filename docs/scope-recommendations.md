# Scope Recommendations

This document proposes additions to the benchmark's CPT scope, drawn from the scope
audit of the v0 release.

> **Status (2026-06-07): the v0.1 "add now" block is APPLIED.** The testing block
> (96130/96131/96132/96133/96136/96137) plus 90785 and 90845 are now in
> `therapy_codes.py`; scope is 19 codes. The "defer (v0.2)" set below remains open.

## Original v0 scope (11 codes)

The v0 set covers the bread-and-butter of a private-pay outpatient therapy practice:

| Code | Our label |
|------|-----------|
| 90791 | Diagnostic intake / first evaluation |
| 90792 | Diagnostic intake with medical services (prescriber) |
| 90832 | Individual therapy, 30 minutes |
| 90834 | Individual therapy, 45 minutes |
| 90837 | Individual therapy, 60 minutes |
| 90846 | Family therapy without the client present, 50 minutes |
| 90847 | Family therapy with the client present, 50 minutes |
| 90853 | Group therapy session |
| 90839 | Crisis therapy, first 60 minutes |
| 90840 | Crisis therapy, each additional 30 minutes |
| 96127 | Brief emotional/behavioral check (per standardized measure) |

This list is solid on session codes but has one real gap: psychological and
neuropsychological testing, which for PhD/PsyD practices is often the highest-grossing
out-of-network service line and the one patients most want benchmarked, because a full
battery is expensive and routinely submitted on a superbill.

## The headline gap: testing (96130-96137)

Testing is the single most important missing service line. A real testing or neuropsych
claim is assembled from an evaluation code plus administration/scoring units, billed in
multiple hours and multiple units. Shipping only one of these codes would misrepresent
the assembled cost, so the testing block should be added as a unit. All six are status
A (active, Medicare-payable) and appear as clean single blank-modifier rows that the
existing pipeline ingests with no code change.

## Proposed additions

Every code below was verified against `PPRRVU2026_Jan_nonQPP.csv` for status and clean
ingestion. The Medicare base figures are approximate, computed from the non-facility
total RVUs times the 2026 conversion factor (`33.4009`); the build pipeline produces
the authoritative values. Labels shown are proposed plain-language labels (NOT AMA
descriptors), consistent with the existing `therapy_codes.py` convention.

| Code | Proposed label | Approx. Medicare base | Status | Commonly private-pay | Verdict |
|------|----------------|----------------------:|--------|----------------------|---------|
| 96130 | Psychological testing evaluation by clinician, first hour | ~$99.55/hr | A | Yes | Add in v0.1 |
| 96131 | Psychological testing evaluation by clinician, each additional hour | ~$70.14/hr | A | Yes | Add in v0.1 |
| 96132 | Neuropsychological testing evaluation by clinician, first hour | ~$98.53/hr | A | Yes | Add in v0.1 |
| 96133 | Neuropsychological testing evaluation by clinician, each additional hour | ~$70.48/hr | A | Yes | Add in v0.1 |
| 96136 | Test administration and scoring by clinician, first 30 minutes | ~$21.38 / 30 min | A | Yes | Add in v0.1 |
| 96137 | Test administration and scoring by clinician, each additional 30 minutes | ~$16.37 / 30 min | A | Yes | Add in v0.1 |
| 90785 | Interactive complexity add-on (communication factors complicating care) | ~$11.69 | A | Yes | Add in v0.1 |
| 90845 | Psychoanalysis session | ~$88.18 | A | Yes | Add in v0.1 |
| 90849 | Multiple-family group psychotherapy | ~$30.06 | A | No | Defer to v0.2 |
| 96156 | Health behavior assessment / reassessment | ~$86.84 | A | No | Defer to v0.2 |
| 96158 | Health behavior intervention, individual, first 30 minutes | ~$59.79 / 30 min | A | No | Defer to v0.2 |
| 90880 | Hypnotherapy session | ~$79.49 | A | No | Defer to v0.2 |

### Rationale for the v0.1 additions (add now)

- **96130 / 96131 (psychological testing).** Testing is the highest-value OON line for
  PhD/PsyD practices and the largest gap in the current dataset. Patients pay out of
  pocket for full batteries and submit large superbills. 96131 (each additional hour)
  is billed alongside 96130 on nearly every real testing claim; omitting it would
  understate the true cost of a battery by half or more.
- **96132 / 96133 (neuropsychological testing).** Neuropsych evaluations (ADHD,
  learning, dementia, post-concussion) are a major OON cash line and the most expensive
  single service most therapy-adjacent practices bill. Distinct from the psychological
  testing codes and frequently the exact figure patients are quoted. 96133 is the
  additional-hour companion and appears on essentially every neuropsych claim.
- **96136 / 96137 (test administration and scoring).** Billed in tandem with the
  evaluation codes for the hands-on testing time. A testing benchmark that includes the
  evaluation codes but not the administration units would misrepresent the assembled
  cost. Multiple units are the norm in a real session.
- **90785 (interactive complexity add-on).** Very commonly billed by therapists
  alongside intake, individual, and group sessions for play therapy, child/family work
  with caregivers, interpreters, or high-conflict dynamics. Unlike the prescriber E/M
  add-ons (90833/36/38), it is appropriate for non-prescriber therapists and shows up on
  OON superbills. Small dollar value but high billing frequency.
- **90845 (psychoanalysis).** A standalone code, billable by appropriately credentialed
  therapists and analysts, and overwhelmingly a cash/OON modality since analytic
  practices rarely take insurance. Fills a recognized private-pay niche cleanly.

### Rationale for the v0.2 deferrals (lower priority)

- **96156 / 96158 (health behavior assessment / intervention).** Billed by health
  psychologists managing the psychological factors of physical-health conditions
  (chronic pain, diabetes, weight) and tied to a physical, not psychiatric, primary
  diagnosis. Relevant to health-psych practices, not the core LCSW/LMFT/LPC/PhD/PsyD
  therapy practice this benchmark targets. Add only if the dataset chooses to cover
  health psychology.
- **90880 (hypnotherapy).** A defensible niche modality billed by therapists offering
  clinical hypnosis, frequently cash/OON, but lower frequency than testing or 90785.
- **90849 (multiple-family group psychotherapy).** Distinct from 90853 (general group)
  and the single-family codes; used in IOP/PHP-adjacent and addiction/eating-disorder
  group programs. Lower frequency in solo private-pay therapy but a clean add for
  practices running multi-family programs.

## Explicitly excluded

- **90875 / 90876 (biofeedback).** Status N (non-covered by Medicare). No Medicare
  anchor exists, so they cannot be benchmarked by this Medicare-multiple method. Exclude
  until v1's Transparency-in-Coverage data can price them directly.
- **90833 / 90836 / 90838 (psychotherapy-with-E/M add-ons).** Active, but billed by
  MDs/DOs/NPs/PAs alongside an E/M code, not by master's or doctoral therapists. The
  benchmark is explicitly a therapist (non-prescriber) dataset, so these stay out of
  scope. This carve-out is already stated in METHODOLOGY.md.

## Existing codes to reclassify (keep, but label as edge cases)

Neither needs removal. Both should be presented as edge cases rather than core rows so
a calculator user is not misled.

- **96127 (brief emotional/behavioral check).** Status A but work RVU 0.00 and only
  ~$5.01 Medicare. It is a brief standardized-screener code (for example PHQ-9 / GAD-7
  scoring), almost never the subject of an OON reimbursement question, and its tiny
  dollar value can mislead a calculator user scanning the table. Already disclosed in
  METHODOLOGY.md; label it explicitly as a low-relevance add-on.
- **90792 (diagnostic intake with medical services).** A prescriber code, the
  prescriber twin of 90791, billed by MDs/DOs/NPs/PAs. The dataset is a therapist
  (non-prescriber) benchmark that even carves out the prescriber E/M add-ons, so 90792
  sits oddly next to its non-prescriber twin. It is a useful reference point (some
  psych-NP cash practices bill it), so retain it but label it as prescriber-billed and
  out of core scope rather than presenting it as a standard therapy session code.

## Summary

- **v0.1 (add now, 8 codes):** 96130, 96131, 96132, 96133, 96136, 96137, 90785, 90845.
  This closes the testing gap and adds the two highest-frequency non-prescriber
  additions. Expands the scope from 11 to 19 codes.
- **v0.2 (defer, 4 codes):** 96156, 96158, 90880, 90849. Niche modalities and health
  psychology, added only if the dataset broadens beyond core therapy.
- **Never (Medicare-multiple method):** 90875, 90876 (non-covered, no anchor);
  90833, 90836, 90838 (prescriber, out of scope).
- **Reclassify, do not remove:** 96127 and 90792, labeled as edge cases.

Implementing v0.1 is a change to `therapy_codes.py` only (add the eight entries with
plain-language labels); the existing pipeline ingests the new rows with no code change.
That file is owned by the scope maintainer and is intentionally not edited here.
