# Positioning

## The one-line strategy

Serve the public reimbursement reference layer openly, while keeping clinic-specific
claims history private. The public layer is data anyone can rebuild from CMS and
payer transparency files. The private layer is a real clinic's claims, ERAs, and
paid-reimbursement records. Clinicians benefit from the first as a transparent
benchmark, and the second stays where it belongs: inside the systems processing a
practice's own claims.

## What is the public reference layer, and why publish it

Medicare RVUs, GPCIs, the conversion factor, and payer Transparency-in-Coverage
files are public. CMS data is a U.S. Government work in the public domain. The
machine-readable files mandated by the Transparency in Coverage rule are published by
the payers themselves. None of it is proprietary, and any sufficiently motivated
person can recompute the Medicare anchor and parse the payer files. Trying to fence
that off would be both futile and slightly dishonest.

So the value here is not the raw numbers. It is the selection, the labeling, the
narrow therapy filter, and the documented method that makes the numbers trustworthy
and usable. That work is genuinely useful to clinicians and patients, it costs little
to maintain at this scope, and it earns trust precisely because it is open and shows
its work. Publishing it is the correct move because clinicians should be able to
inspect the assumptions behind a reimbursement estimate instead of trusting an
opaque calculator.

## What stays private, and why

The thing that is actually scarce is a clinic's real reimbursement history: what each
payer actually paid on each claim, after adjudication, for that clinic's specific
contracts and patient mix. That data does not live in any public file. It lives in
the claim lifecycle, in the ERAs and remittances that land back in the practice
management system. An EHR that processes claims sees this stream first-hand and
accumulates it over time. The public benchmark tells you what a code is roughly worth
in your region. A clinic's own claims data tells you what that clinic actually
collects, which is the number that pays the rent.

That private data should not be published or mixed into this repo. It improves with
every claim processed, but it is specific to one practice and can include sensitive
administrative context. This project open-sources the generic reference layer and
keeps practice-specific reimbursement history inside the EHR.

## Why therapy only

Scope discipline is the whole product. The general price-transparency space is a
terabyte-scale, monthly-refreshing firehose. Every volunteer who has tried to index
the entire space has either burned out or let the data rot. The graveyard of
abandoned Transparency-in-Coverage repositories is full of ambitious crawlers that
went stale within a quarter, because the maintenance cost of boiling the whole ocean
is unbounded and the payoff is diffuse.

Therapy is a deliberately narrow vertical: roughly a dozen outpatient
behavioral-health session codes that a private-pay practice actually bills. That
narrowness is a feature in three ways. First, it is the vertical we know and serve,
so the labels and scope decisions are informed rather than generic. Second, the thin
therapy filter rejects the overwhelming majority of every payer file, which collapses
terabytes into a few megabytes and makes periodic refreshes tractable for one person.
Third, a focused dataset is more credible to the audience that needs it than a sprawling
index nobody trusts. We boil a teacup, on a schedule.

## Why periodic snapshots, not a live index

A live, always-current index of the whole transparency space is the exact thing that
kills these projects. It demands continuous crawling, continuous storage, and
continuous reconciliation against files that change every month. We do not promise
that. We publish periodic, versioned snapshots of a narrow slice. Each release is
stamped with a `methodology_version` and a `snapshot_date`, so a consumer always
knows exactly what they are looking at and when it was true.

Periodic-and-narrow is the only shape of this project that survives. It sets an
honest expectation, it keeps the maintenance load inside what a small team can carry,
and it avoids the treadmill that turned earlier attempts into abandonware.

## How this fits clinician workflows

This dataset and the calculator built on top of it serve two practical clinician
needs.

The dataset is the inspectable reference. It is open, sourced, and rebuildable from
public files, so a clinician, biller, or developer can see where a number came from
and decide whether it is useful for their situation.

The calculator is the usable surface. A clinician can ask a concrete question, such
as what code 90837 typically reimburses in a state or payer sample, and get an answer
with the method visible nearby. The tool should help with context-setting, fee review,
and reimbursement conversations without pretending to know a specific patient's plan.

Together: the dataset provides the source-backed files, and the calculator makes the
same files usable at the point of need. If a clinic wants its own paid-claims history,
that belongs in its private billing workflow, not in this public benchmark.

## The honest caveat

v0 does not measure out-of-network reimbursement. The Medicare figures are firm and
fully sourced, but the out-of-network range is the Medicare amount multiplied by a
placeholder band of 1.0x to 2.0x. That band is a documented assumption, not a
measurement, and every row is labeled `basis = medicare_multiple` so no one mistakes
it for observed data. v1 replaces that single placeholder with real
Transparency-in-Coverage percentiles per payer and region. Until then, the
out-of-network range should be read as a rough bracket anchored on a firm Medicare
number, and the dataset says so plainly in its disclaimer, its README, and its
methodology. The credibility of this asset rests on that honesty, so we keep it
front and center rather than buried.
