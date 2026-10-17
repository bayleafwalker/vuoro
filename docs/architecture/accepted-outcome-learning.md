---
doc_id: vuoro-accepted-outcome-learning
title: Accepted-outcome learning record and bounded review inputs
purpose: explanation
lifecycle: draft
effective: 2026-10-17
subjects: [learning, evidence]
related: [vuoro-product-delivery-plan-2026-10-17]
---

# Accepted-outcome learning records

`vuoro_evidence.experiment` implements the source record in the
[product direction](../direction/2026-10-15-product-direction.md#learning-from-outcomes).
It produces read-only comparison inputs from supplied owner observations. It
cannot accept work, authenticate a referenced owner record, select a runtime,
compile instructions, change a grant, or establish causal attribution.

An `ExperimentRecord` names one external revision selector (`recipe_id`, `model_id`,
`harness_build` or a digest of the whole observed profile), task grouping,
population reference and explicit unique case IDs. Every referenced run must
match that revision arm. The population and arm are bound to a supplied
SHA-256 snapshot, registration reference and owner timestamp; changing the case
selection without updating its binding is refused. This does not authenticate
the registration or establish that it preceded the runs. Those checks require
resolving the immutable owner registration and run histories outside this package. Each `OutcomeObservation`
names its case, all participating run IDs, immutable owner record/evidence
references, evidence class, acceptance disposition, and capture observation.
`RunManifest` objects retain harness/model/recipe/observed-profile provenance
and composition digests. Retries belong to the same case; one run cannot count
as two cases. Missing cases remain in the denominator. An owner-record reference
must bind a revision or immutable record; this package preserves the reference
but does not resolve it. The caller owns provenance verification and task-group
selection. A provider completion event cannot be an accepted disposition.

Acceptance time, review, rework, recovery effort and cost may be unknown.
Measurements use nonnegative integer seconds and exact Decimal amounts with
canonical uppercase three-letter currency codes. Unknown
cost is never zero. Known totals retain separate currencies; no exchange rate is
invented. Effort and cost summaries explicitly cover **accepted cases**, with
known and unknown counts for each measure; rejected and pending cases retain
their records and remain in population/capture denominators. `population_burden`
also includes their known costs and review/rework/recovery effort, with unknown
counts including missing cases. `burden_by_disposition` exposes accepted,
rejected, pending and unknown-state costs separately; unsuccessful work is not
free merely because it was never accepted.

The reviewer must supply both a minimum accepted-case count and an exact capture
fraction to `record.summarize(min_accepted_cases=..., min_capture=...)`. These
values, an immutable policy reference and a reviewer reference are recorded in
the summary. Threshold predeclaration is explicitly unverified, so these inputs
remain descriptive until the owning reviewer verifies their pre-run policy. This source sets no global policy for the
open product threshold decision. Too few accepted cases or insufficient capture
produces `inconclusive`. Passing those thresholds means `eligible_for_review`,
with `causal_attribution: not_established`; it never means one revision is better.
A reviewer must examine task selection, evidence classes and uncertainty.

```python
from fractions import Fraction

# record is constructed from verified owner exports and their RunManifests.
# Thresholds come from the reviewer-approved experiment, not package defaults.
summary = record.summarize(
    min_accepted_cases=review_policy.minimum_cases,
    min_capture=Fraction(review_policy.minimum_capture),
    review_policy_ref=review_policy.immutable_ref,
    reviewer_ref=review_policy.reviewer_ref,
)
```

`packages/vuoro-evidence/tests/test_experiment.py` exercises missing populations,
completion versus acceptance, rejected/pending cases, unknown measures, exact
mixed-currency costs, repeated-case inflation, changed run composition, and
caller-provided thresholds, mismatched revision arms, population digest drift
and costs of unsuccessful work. Capture is case-level only; it does not
establish coverage by individual source. Tests use synthetic records. No real accepted
outcome sample, cross-provider superiority, accepted threshold policy, sustained
commitment product, automatic re-evaluation, or authority expansion is qualified.
