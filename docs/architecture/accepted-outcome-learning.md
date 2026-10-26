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
uppercase three-letter currency identifiers. Unknown
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
remain descriptive until the owning reviewer verifies their pre-run policy.
`accepted_cases` counts source-qualified acceptance; the raw owner count remains
available as `owner_reported_accepted_cases`. An accepted observation qualifies
only when the supplied source report matches its case and owner record, its
Release digest matches, and it cites an independent verification and actual use.
`OwnerOutcomeQualification` is a caller-supplied report with an immutable source
snapshot and current owner revision reference; Vuoro does not resolve or
authenticate those references. Missing or incomplete qualification leaves the
owner-reported outcome and its burden visible but excludes it from accepted
counts, accepted cost and effort, and model-comparison outcome deltas.

Too few qualified accepted cases, incomplete qualification of reported
acceptances, or insufficient capture produces `inconclusive`. Passing those
thresholds means `eligible_for_review`, with `causal_attribution:
not_established`; it never means one revision is better. A reviewer must examine
task selection, source qualifications, evidence classes and uncertainty.

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

## Supplied Sprintctl owner-record intake

`vuoro_evidence.owner_outcome_intake.intake_owner_outcome(case)` adds a pure
structural mapping into the existing `OutcomeObservation`. It performs no
network or storage access, accepts no provider credentials, and cannot accept
work. This is a supplied-record projection contract, not a Sprintctl export
format or a claim that a production export has been qualified. Callers extract
these fields from independently obtained records; the package has no Sprintctl
runtime dependency.

The envelope uses `schema_version: vuoro-owner-outcome-intake/v1`. Required
fields are `case_id`, unique nonempty `run_ids`, `owner_record_ref`, unique
nonempty `evidence_refs`, `evidence_class`, explicit `captured` (true, false or
null), nonempty text `repository_id`, positive integer `item_id`, and `item`. Optional
`decision`, `release` and `measurements` fields may be omitted. Unknown fields
and malformed values fail validation rather than silently removing a case.
References are nonempty opaque text without control characters. Their
immutability, resolution and authenticity remain the caller's responsibility.

The supplied projections contain these exact fields:

| Projection | Fields |
| --- | --- |
| `item` | `repository_id`, `id`, `status`, `resolution`, `terminal_decision_id` |
| `decision` | `repository_id`, `id`, `work_item_id`, `kind`, `release_digest` |
| `release` | `repository_id`, `work_item_id`, `release_digest` |

Repository scopes (native Sprintctl `repo_id` text) and item IDs must match the envelope. Item status uses Sprintctl's
`pending`, `active`, `blocked` and `done` vocabulary. A Decision ID must match
the item's terminal pointer. Release digests are compared as supplied 64-character
lowercase hexadecimal strings: the adapter does not recompute or certify the
owner's canonical digest. The optional Decision is the terminal Decision, not
an arbitrary latest `revise` record.

| Supplied owner facts | Observation disposition |
| --- | --- |
| Done/accepted item, matching terminal `accept` Decision and matching Release | `accepted` |
| Done/rejected item, matching terminal `reject` Decision and any required Release join | `rejected` |
| Open item with no terminal pointer, resolution or supplied terminal Decision | `pending` |
| Missing or contradictory terminal joins, legacy done, withdrawn or superseded | `unknown` |

Rejection may omit a Release when its Decision has no Release digest; acceptance
requires one. Provider completion, an accepted effect intent, an applied attempt,
or a matching receipt alone cannot supply this terminal work join. An effect
acceptance snapshot is insufficient input for this adapter.

The result retains `observation`, `join_status` (`consistent`, `incomplete` or
`conflicting`), stable `reason_codes`, a detached `supplied_records` copy,
`measurement_provenance` and `limitations`. Every result explicitly states
`source_verification: not_performed_by_this_package`. Even a consistent accepted
join means **structural mapping of supplied owner records**, under the existing
learning package's external-source trust contract. It is not authenticated
acceptance. Missing records produce an incomplete join, contradictory supplied
facts a conflicting join; both map to unknown. Known independent measurements
remain usable for population burden. Measurement exclusions do not change the
owner join status. The observation retains the supplied `owner_release_digest`
for later comparison with an owner-side qualification report; intake does not
create that report.

Reasons include `terminal-decision-missing`, `terminal-decision-id-mismatch`,
`decision-item-mismatch`, `decision-kind-resolution-mismatch`,
`release-record-missing`, `release-item-mismatch`, `release-digest-mismatch`,
`legacy-terminal-unqualified`, `terminal-disposition-unmapped`,
`measurement-provenance-missing` and `measurement-coverage-incomplete`.
Caller-supplied capture is preserved independently of join status; it is a
case-level capture observation, not source verification.

### Supplied measurements and retained sidecar

`measurements` maps any of `acceptance_seconds`, `review_seconds`,
`rework_seconds`, `recovery_seconds` and `known_cost` to:

```json
{
  "value": 60,
  "unit": "seconds",
  "coverage": "complete",
  "source_ref": "owner:measurement@immutable-revision",
  "window": {
    "start": "2026-10-17T12:00:00Z",
    "end": "2026-10-17T12:05:00Z"
  }
}
```

Effort values are nonnegative integer seconds; cost uses an exact nonnegative
**decimal string** and an uppercase three-letter currency unit, such as
`"value": "0.10", "unit": "EUR"`. One case has at most one supplied cost.
No conversion, rate inference or combination of currencies occurs in intake.
Measurements remain caller reports even with complete provenance. The window
must contain supplied UTC timestamps in nondecreasing order; no duration is
inferred from those timestamps or provider completion.

Only complete coverage with both a source reference and a window maps to a
scalar. Partial or unknown coverage, absent provenance, and unqualified
acceptance time map to `None`, with the original supplied measurement and
exclusion reasons retained in `measurement_provenance`. Unknown is never zero.
`acceptance_seconds` additionally requires the structurally mapped accepted
disposition. Qualified review, rework, recovery and cost remain attached to
rejected, pending and unknown cases.

Pass **all** intake observations into the existing `ExperimentRecord`, preserve
its original `expected_case_ids`, and carry intake limitations into the record's
`limitations`. Retain intake results alongside the summary: the existing summary
does not propagate measurement windows, supplied projections or exclusion
sidecars. The adapter cannot choose a smaller population, authenticate a
registration, set a review threshold or rank workflows.

The synthetic `test_owner_outcome_intake.py` fixtures check identity/digest
mismatches, absent joins, unsupported terminal states, independent capture,
measurement exclusions, exact cost and integration with the existing summary.
The mixed-population example preserves one unknown conflicting case and one
missing case while separating accepted, captured and expected counts. This
qualifies the supplied-input software contract only. Real source authentication,
representative outcome samples, recovery-effort comparisons and causal workflow
claims still require independent evidence.

## Two-arm comparison over a committed case set

`vuoro_evidence.comparison` adds the baseline/candidate pair that a single
`ExperimentRecord` lacks.

A `CaseSet` (`schema_version: vuoro-case-set/v1`) is a committed JSON file with
a slug `name`, an integer `version`, a description, selection `limitations` and
cases. Each case has `case_id`, `workload`, `source_ref` (the real work it was
drawn from), `criteria` (`pass_fail`, or `scored` with `max_score` and
`pass_score`) and an optional `observed_on` ISO date. Unknown keys are refused.
`digest` is the SHA-256 of the canonical document with cases sorted by ID, so
editing any case or criterion changes it. `ref`
(`case-set:<name>@v<version>+sha256:...`) is the population reference that both
arms' records must carry. `load_case_set(path, expected_digest=...)` refuses a
file that differs from a pinned digest.

`ExperimentComparison(comparison_id, case_set, baseline, candidate, limitations)`
requires both records to reference that case set and enumerate exactly its
cases. They must share a task group and revision selector, name different
revisions, and share no run. Every supplied run must also match on the named
`harness_id`, `harness_build`, nonselected `model_id`, `recipe_id`, observed
profile, and workflow-binding snapshot. This prevents changing a second named
run-composition axis alongside the selected revision. Grant/claim IDs and
workflow receipt references remain run-specific; this package cannot establish
that their underlying authority or receipt meaning is equivalent, so reviewers
must verify that context from the owner sources. `compare(min_cases=...,
min_coverage=..., review_policy_ref=..., reviewer_ref=...)` has no default
thresholds. It reports
candidate-minus-baseline deltas as exact fractions:

- Acceptance, rejection and capture rates use all case-set cases as the
  denominator. Acceptance and rejection counts use only the same source-qualified
  case pairs in both arms. Rejected, pending, unknown, unqualified and missing
  cases stay in the denominator, and `delta_bounds` shows the range still
  possible if every unresolved case went either way. `transitions` counts
  per-case disposition pairs, including `unqualified` and `missing`.
- Acceptance, review, rework and recovery seconds, and cost in each currency,
  compare only cases where both arms know the value. Known and unknown counts
  are reported. Pairs in different currencies are counted and never converted.

`OwnerOutcomeQualification` is a separate caller-supplied report tied to one
case and `owner_record_ref`. A qualified report cites the qualification record,
immutable source snapshot and digest, current owner revision and exact Release
digest. An accepted outcome also needs independent verification and actual-use
references. The package checks the report's structure and its case, owner and
Release-digest joins; it does not resolve or authenticate the report. The output
states `authentication: not_performed_by_this_package`. Missing, stale,
conflicting or mismatched qualifications make that outcome `unqualified` for
comparison. A provider completion or the intake adapter's structurally matching
join cannot supply this qualification.

The comparison's outcome and burden metrics are suppressed, with explicit
reasons and `null` deltas, when the case count, observed paired count or
source-qualified paired count is below `min_cases`, or when either observed or
source-qualified paired coverage is below `min_coverage`. Unqualified outcomes
remain in the case-set denominator and are reported with their reason codes;
they cannot contribute to acceptance, rejection, effort or cost deltas. Those
metrics use only the intersection of cases whose sources qualify in both arms;
all other cases remain unresolved in each arm's acceptance bounds. Capture
remains a separate observation and can still be reported when its observed-pair
gate passes. A measure with too few known source-qualified pairs is suppressed
on its own while the rest are still computed. Strata by case-set `workload`,
`observed_on` month and pairwise capture apply the same minimums, so thin strata
are suppressed too. Summary and comparison outputs use schema v2 and expose an
explicit `comparison_readiness: inconclusive` plus reasons whenever the
qualified gates fail. The output records `decision: not_recorded_by_this_package`
and `causal_attribution: not_established`. It does not run either arm, accept
work or change a review boundary. `tests/test_comparison.py` uses synthetic
case-set and qualification fixtures. No real case set or ablation result has
been qualified by this package.
