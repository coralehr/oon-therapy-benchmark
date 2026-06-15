# Positioning

## The one-line strategy

Open-source the commodity, keep the flywheel. The commodity is public data that
anyone can rebuild from CMS and payer transparency files. The flywheel is the private
loop a real EHR sits on: actual claims, ERAs, and paid-reimbursement records that
only flow through the systems processing those claims. We give away the first and
compound the second.

## What is the commodity, and why give it away

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
its work. Giving it away is the correct move. It builds authority, it earns links,
and it makes us the reference that other people cite.

## What is the flywheel, and why keep it

The thing that is actually scarce is a clinic's real reimbursement history: what each
payer actually paid on each claim, after adjudication, for that clinic's specific
contracts and patient mix. That data does not live in any public file. It lives in
the claim lifecycle, in the ERAs and remittances that land back in the practice
management system. An EHR that processes claims sees this stream first-hand and
accumulates it over time. The public benchmark tells you what a code is roughly worth
in your region. A clinic's own claims data tells you what that clinic actually
collects, which is the number that pays the rent.

That private data is the moat. It improves with every claim processed, it cannot be
scraped, and it is exactly the input that turns a generic benchmark into a
clinic-specific revenue model. We open-source the generic layer and let the private
layer compound where it already lives, inside the EHR.

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

## How this fits the EHR: tool-first SEO

This dataset and the calculator built on top of it are a top-of-funnel,
tool-first-SEO asset for the EHR. The two pieces play distinct roles.

The dataset is the authority and backlink asset. It is open, sourced, and rebuildable
from public files, which is exactly the kind of artifact that other sites cite and
link to. Citations and links are what earn ranking and credibility over time. The
dataset's job is to make this the reference that practice-management blogs, billing
forums, and clinician communities point at.

The calculator is the conversion surface. A clinician or a patient arrives with a
concrete question, what does code 90837 reimburse out-of-network in my area, and the
calculator answers it on the spot. That is the moment the visitor experiences the
product's competence first-hand, before any signup. The calculator turns search
traffic into engaged users, and engaged users are the top of the funnel into the EHR
itself.

Together: the dataset earns the authority that brings people in, and the calculator
turns that attention into a first useful interaction with the product. Both run on
the open commodity layer. The conversion downstream, into a clinic running its claims
through the EHR, is where the private flywheel starts.

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
