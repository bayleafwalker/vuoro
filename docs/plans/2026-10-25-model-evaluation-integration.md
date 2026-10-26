---
doc_id: vuoro-model-evaluation-integration-2026-10-25
title: Model portfolio experiments on existing accepted-outcome evidence
purpose: proposal
lifecycle: proposed
effective: 2026-10-25
subjects: [learning, evidence, model-evaluation, authority]
related: [vuoro-accepted-outcome-learning, vuoro-product-delivery-plan-2026-10-17]
---

# Model and decision-model trials without a Vuoro router

Status: **operator-requested work plan**, not implemented experiment results,
new model routing authority, or deployment approval. Packets AE-04/AE-05/AE-07
are coordinated by the
[Agentops evidence-guided plan](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-25-evidence-guided-agent-workflows.md).
This extends [product delivery](2026-10-17-product-delivery-plan.md), not the
October 15 [product direction](../direction/2026-10-15-product-direction.md).

## What already exists

[Accepted-outcome learning](../architecture/accepted-outcome-learning.md)
documents `vuoro_evidence.experiment.ExperimentRecord`,
`RunManifest`, `OutcomeObservation` and supplied Sprintctl owner-record
intake. These are **implemented pure projections tested with synthetic
records**, not a qualified cross-model/live comparison or an authenticated
owner-record importer. The record binds expected case IDs, immutable population
and revision selectors, keeps missing/rejected/pending cases and unknown
costs visible, and makes no causal/supplier superiority claim.

The `vuoro-reconciler` package handles trusted **protected effect intents**.
It neither provides the work completion evaluator nor houses cheap inference.
Native harnesses choose the provider/model and execute sessions. Vuoro records
the resulting model, harness, profile revision and accepted owner outcome.
No standalone provider router, session supervisor, classifier service or new
Acceptance writer belongs in `vuoro-service`.

## VU-ME1 — freeze a matched, authorized model cohort [AE-05]

Select 5–10 independently verifiable *isolated* coding issues, same starting
commit, tests and acceptance oracle. Register the expected population and
existing task/RunManifest/Release/Decision source refs **before** observing
model outcomes. The user currently uses high-judgment Sol/Luna/Opus/Sonnet and
DeepSeek V4.1 Flash as a cheap OpenCode worker; these are dated observations,
not hard-coded product defaults.

Evaluate:

| Arm | Intended hypothesis | Do not infer |
| --- | --- | --- |
| DeepSeek V4.1 Flash | Existing cheap worker baseline | A universal price/performance winner |
| GLM Flash | Better completion or lower total burden on the same issues | Token rate implies lower outcome cost |
| Qwen Flash | Better steering/iteration under the same acceptance criteria | Good conversations mean accepted changes |
| Gemini Flash (small separate cohort) | Distinct long-context/multimodal value | A justification for another subscription |

Model names and API capability are *runtime-preflight inputs*, not verified
catalog IDs or entitlements in this plan. For each run: actual API/provider,
billing type, observed model/version, native session ref, instructions/profile
digest, prompt/tool policy, cache hit information if supplied, all retries,
test/patch digests, independent checker and exact owner disposition.
No invisible API failover or move of private sources across providers.

**Acceptance:** accepted cases and independent functional checks are reported
separately. `ExperimentRecord` includes every expected case, unsuccessful
attempts, capture unknowns, review/rework/recovery, unconverted currencies and
cache/provider uncertainty. An arm with missing or incomparable observations
reports inconclusive, not a winner. Compare **cost and effort per accepted
and independently verified case**, not $/million output tokens.

## VU-ME2 — bounded cheap decision model replay [AE-04]

Reuse Agentops' existing Jev routing/route-state corpus and measured error
types. Freeze only 30–50 *prelabeled* decisions for which state, bounded legal
choices, outcome and error costs are actually known; exclude hindsight guesses
from the gold standard. Compare deterministic rule/known small-generative
baseline with Jev, Cloudflare Clef, OpenAI Decisions and d1-3B *only when
the account/API permissions, model IDs and payload are verified*. Record
abstentions and calibrate per choice/error class. Model confidence is not a
Grant or truth status.

**Two-evening stop rule:** the initial integration experiment does not expand
into a new router. If the current baseline wins or label effort dwarfs
benefit, close with evidence and retain no production dependency. An explicit
model route change remains a native harness/operator change after owner review.
This does **not** reopen Outctl.

## VU-ME3 — owner-qualified comparison integration

Exercise real supplied Sprintctl owner export/read records into
`intake_owner_outcome` and `ExperimentRecord` on a frozen input set. First
prove source existence, authority, exact Release digest and current revision
outside the pure adapter. A structurally consistent projection is not a
verified acceptance. Preserve the existing independent requirement/use
companion in the
[Agentops measurement procedure](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-22-opportunistic-follow-through/outcome-measurement.md).

If a new schema/tool operation is unavoidable, inventory it in EC-01 and
derive the interface through the selected EC-00 TypeSpec/OpenAPI convention;
keep owner-specific semantics in the owner package. Source contracts, released
adapter, deployed composition and observed outcome are separate proof gates.

**Acceptance:** an independent reconstruction links at least one accepted
case and one failed/unqualified case to the correct owner record, immutable
artifact digest, run identity and actual cost/unknown cost. A provider's
successful session cannot enter `accepted` by itself.

## Comparison protocol and exit gates [AE-04/AE-05]

### Freeze before the first run

Register separate committed `CaseSet`s for the work cohort and decision
replay, using existing work and decision references, with inclusion/exclusion
criteria and a pinned digest. For each comparison,
predeclare its task group, the one revision field being compared (for example,
`model_id` or a digest of the full observed profile), exact case IDs, initial
revision, acceptance/checking criteria, reviewer, `review_policy_ref`,
`min_accepted_cases` and `min_capture` for each record summary, and
`min_cases` and `min_coverage` for the paired comparison. Freeze the
cost-allocation rule and decision-error costs too. Do not choose thresholds,
remove cases, or change scoring after seeing outcomes.

Preflight the exact provider/model version, account permission, billing type,
data boundary, budget and native harness capability for each arm. A failed
preflight removes no registered case: mark that arm unavailable or the
observation missing, with its reason. No automatic failover or credential
expansion. Record exact provider/model requested and observed, all attempts,
harness/build/profile and instruction digests, tool policy, initial commit,
test/checker output, owner disposition, and cost provenance. For a flat
subscription, use only a pre-registered allocation basis; otherwise cost is
unknown/unallocated, never zero. Keep currencies separate.

### Run matched and blinded cohorts

For VU-ME1, start every model on the same registered issue inputs and clean
worktree at the same commit, with identical instructions, tool permissions,
tests and acceptance oracle. Randomize the arm order. Do not share patches,
intermediate model output or history between arms. Give outputs arm-neutral IDs;
where practical, lock independent-checker dispositions before revealing model
identity, and record any identity exposure as a limitation. Keep the harness
and every non-model factor fixed. If provider, harness or profile changes
together, label the arm as a full-stack comparison and do not attribute its
result to model identity alone. Treat Gemini's separate task cohort as a distinct study, not
a head-to-head arm.

For VU-ME2, freeze the legal-choice schema, deterministic baseline, prompts,
model/profile revisions, labels and error-cost table before replay. Keep scored
labels out of model inputs and have the choice labels owner-reviewed before
the run. If prompts or rules need tuning, use a disjoint development set and
leave scored cases held out. Report abstentions, coverage, false-route and
critical-error counts/cost by choice class, and end-to-end latency p50/p95.
Any predeclared critical unsafe route stops the trial; it does not trigger an
automatic routing change.

### Qualify and compare

Before interpreting VU-ME1 results, complete VU-ME3 source qualification on
real supplied Sprintctl records. Independently resolve the owner, current item
revision, terminal Release/Decision and exact artifact digest outside the pure
intake adapter. Report owner disposition separately from independent functional
verification and actual-use evidence required by the referenced measurement
procedure. A missing or contradictory join, verifier result or required use
evidence is unqualified for the relevant comparison measure; this measurement
rule never changes Sprintctl's own acceptance state.

Use the existing `CaseSet` and `ExperimentComparison` described in
[accepted-outcome learning](../architecture/accepted-outcome-learning.md#two-arm-comparison-over-a-committed-case-set).
Both arm records must retain the exact registered population and case IDs,
share the task group and revision field while naming different revisions, and
have no run IDs in common. Run the comparison only after both records and source
qualifications are complete. Missing, failed, rejected, pending and unavailable
cases stay visible in the denominator. If the configured minimums suppress the
overall comparison or a measure, report its reason and leave it inconclusive;
do not rank an arm from partial or incomparable observations.

The 5–10 issue cohort and 30–50 decision replay are exploratory. Report exact
counts, paired deltas, unknowns and the registered policy; make no
statistical-significance, causal-superiority, universal supplier ranking or
default-selection claim from these samples. No result in this plan changes
owner state, provider routing, grants or production configuration.

## VU-ME4 — optional personal operator is a *consumer* [AE-07]

Experiment with an independent, event-triggered, read-only personal narrative
client. It may read scoped, source-linked work/evidence snapshots and supply
a versioned persona and compact narrative memory, with source citations and
disagreement records. It may summarize progress and draft proposed next
actions, but it neither holds a grant nor becomes a knowledge publisher,
work writer, model router or effect executor.

Before any follow-up actions, designate a **separate application owner** in
the estate disposition register and qualify a specific existing authorization
boundary. No automatic promotion from prototype to actor. An operator may
review retrospective narratives *after* valid work completion; narration
must never become another acceptance gate.

**Acceptance:** read-only narrative replay of one representative week,
accuracy against immutable source events, "unknown" on absent capture,
attention/notification and personalization evaluation; replay after model
replacement yields the same factual claims even if the voice differs.
Kill the experiment if it hallucinates accomplishments or costs more
supervision than it removes.

## Rejection and recovery cases

- Missing case, unknown cost, failed and rejected attempts never disappear
  from denominators; mixed-currency values never sum without exchange basis.
- Wrong released artifact digest, stale owner revision or a conflicting
  supplied terminal Decision produces unknown/refusal, not acceptance.
- Model unavailable or out-of-budget leaves deterministic owner completion
  untouched; no auto-switch to another billing/data jurisdiction.
- Persona model can be deleted/replaced without loss of original evidence.
- No public or hosted tool gains protected effect scope.

**Rollback:** drop optional comparisons and read-only narrative clients;
retain owner-record provenance, raw trial result digests and accepted Decisions;
return native harness models to their previous configured setting. Reversing an
S4 migration or release composition is governed by its owner plan, not by this
experiment.
