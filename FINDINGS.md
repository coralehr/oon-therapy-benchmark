# What insurers actually pay for therapy

Cross-payer analysis from `data/v1/` — real in-network negotiated rates (out-of-network proxy) across **4 payers** (UHC, Cigna, Centene, Anthem), pooled from 184 real plan files (2026-06-07). Medians, USD.

## The headline

- **Cigna pays the most** (12/19 codes); **Centene the least** (13/19).
- **Which payer matters more than which state.** Per-payer spread runs 1.15x to 2.73x; geographic (GPCI) variation within a payer is far smaller.
- Therapy generally pays **below Medicare**; group therapy and testing vary most.

## Per-code medians

| CPT | Service | Medicare | Blended | UHC | Cigna | Centene | Anthem | spread |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 90785 | Interactive complexity add-on (com | $15 | $10 | $10 | $17 | $11 | $15 | 1.72x |
| 90791 | Diagnostic intake / first evaluati | $173 | $149 | $150 | $180 | $136 | $141 | 1.32x |
| 90792 | Diagnostic intake with medical ser | $202 | $163 | $165 | $202 | $188 | $145 | 1.40x |
| 90832 | Individual therapy, 30 minutes | $86 | $69 | $68 | $84 | $67 | $92 | 1.37x |
| 90834 | Individual therapy, 45 minutes | $114 | $99 | $100 | $112 | $87 | $92 | 1.28x |
| 90837 | Individual therapy, 60 minutes | $167 | $130 | $130 | $164 | $125 | $130 | 1.31x |
| 90839 | Crisis therapy, first 60 minutes | $160 | $130 | $130 | $169 | $118 | $130 | 1.43x |
| 90840 | Crisis therapy, each additional 30 | $77 | $56 | $55 | $83 | $56 | $91 | 1.67x |
| 90845 | Psychoanalysis session | $109 | $95 | $102 | $114 | — | $91 | 1.25x |
| 90846 | Family therapy without the client  | $106 | $102 | $103 | $117 | $80 | $96 | 1.45x |
| 90847 | Family therapy with the client pre | $110 | $108 | $109 | $124 | $81 | $102 | 1.53x |
| 90853 | Group therapy session | $30 | $41 | $41 | $33 | $24 | $66 | 2.73x |
| 96127 | Brief emotional/behavioral check ( | $5 | $6 | $6 | $6 | $5 | $6 | 1.15x |
| 96130 | Psychological testing evaluation b | $124 | $125 | $132 | $139 | $121 | $112 | 1.23x |
| 96131 | Psychological testing evaluation,  | $87 | $97 | $99 | $103 | $85 | $91 | 1.21x |
| 96132 | Neuropsychological testing evaluat | $122 | $141 | $150 | $144 | $122 | $118 | 1.27x |
| 96133 | Neuropsychological testing evaluat | $98 | $111 | $115 | $110 | $96 | $96 | 1.20x |
| 96136 | Test administration and scoring by | $44 | $54 | $54 | $44 | $43 | $51 | 1.25x |
| 96137 | Test administration and scoring, e | $37 | $49 | $50 | $38 | $37 | $49 | 1.36x |

## Caveats

- In-network negotiated rates used as the OON proxy (real OON allowed-amount files are effectively empty). Per-locality numbers are the national ratio scaled by Medicare GPCI, not per-state measured. A payer/plan sample, not census. See `data/v1/PROVENANCE.md`.
- Estimates, not guarantees. Not medical, billing, or legal advice.
