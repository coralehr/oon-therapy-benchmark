"""
The therapy CPT scope for the benchmark. This is the entire universe of the
dataset: outpatient behavioral-health session codes a private-pay practice bills.

IMPORTANT: CPT(R) codes and their official descriptors are copyright the AMA.
The `label` values below are OUR OWN plain-language descriptions, NOT the AMA
CPT descriptors. We ship code numbers + CMS RVU facts + our labels. We do not
redistribute AMA descriptor text. License CPT from the AMA before shipping
official descriptors at scale.
"""

THERAPY_CODES = [
    {"code": "90791", "label": "Diagnostic intake / first evaluation"},
    {"code": "90792", "label": "Diagnostic intake with medical services (prescriber)"},
    {"code": "90832", "label": "Individual therapy, 30 minutes"},
    {"code": "90834", "label": "Individual therapy, 45 minutes"},
    {"code": "90837", "label": "Individual therapy, 60 minutes"},
    {"code": "90846", "label": "Family therapy without the client present, 50 minutes"},
    {"code": "90847", "label": "Family therapy with the client present, 50 minutes"},
    {"code": "90853", "label": "Group therapy session"},
    {"code": "90839", "label": "Crisis therapy, first 60 minutes"},
    {"code": "90840", "label": "Crisis therapy, each additional 30 minutes"},
    {"code": "96127", "label": "Brief emotional/behavioral check (per standardized measure)"},
    # v0.1 scope additions (verified status A in PPRRVU2026). Psychological /
    # neuropsychological testing is the highest-dollar OON line for PhD/PsyD
    # practices; 90785 and 90845 round out non-prescriber therapist billing.
    {"code": "96130", "label": "Psychological testing evaluation by clinician, first hour"},
    {"code": "96131", "label": "Psychological testing evaluation, each additional hour"},
    {"code": "96132", "label": "Neuropsychological testing evaluation by clinician, first hour"},
    {"code": "96133", "label": "Neuropsychological testing evaluation, each additional hour"},
    {"code": "96136", "label": "Test administration and scoring by clinician, first 30 minutes"},
    {"code": "96137", "label": "Test administration and scoring, each additional 30 minutes"},
    {"code": "90785", "label": "Interactive complexity add-on (complicating communication factors)"},
    {"code": "90845", "label": "Psychoanalysis session"},
]
