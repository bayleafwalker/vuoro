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

### October 30 refinement: local typed classifiers

Include one locally runnable encoder classifier only when its exact weights,
license, tokenizer and runtime are available and the input class permits it.
The model family or a copied model card is insufficient qualification; no Hanzo,
Laya, Jev or other hosted endpoint is selected by this plan. Keep provider-native
execution and the existing deterministic policy as the operational baseline.

Freeze legal labels, state projection, abstention rule and asymmetric error
costs before replay. Keep training/calibration and test cases disjoint; record
their source/digests and leakage limitations. Measure per-class mistakes,
abstention/coverage, calibration, latency and total review/compute burden.
Valid typed output measures format reliability, not correct judgment. A small
or unrepresentative corpus reports uncertainty rather than a universal threshold.

Record model/ruleset revision, supplied evidence refs, proposed label,
confidence, abstention and the eventual owner outcome through the existing
evidence path. A confidence score neither authorizes an action nor lowers a
data class. Initially use shadow/advisory results. Any later operational use
requires the existing policy's authorized change path and an explicit rollback
configuration; it adds no mandatory per-decision human step. Model absence,
failure or drift leaves the existing deterministic/native workflow available.

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

## VU-ME5 — bounded verifier-first repair [AE-05 / D3]

Compare the same small repair cohort under the ordinary native workflow and
an optional bounded search before inference. The execution step belongs to the
existing isolated native/CI lab; Vuoro only compares supplied owner evidence.
This is an experiment within
[AgentOps' existing development-efficiency work](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-25-evidence-guided-agent-workflows.md),
not a new repair service or model router.

Each case freezes its starting artifact, allowed edit space, verifier/test
revision, independent acceptance criteria and candidate/time/compute limits.
Evaluate every candidate from the same clean isolated baseline. The search
cannot weaken the verifier, change expected answers, broaden permissions or
publish a passing patch. Preserve failed trials, candidate digests and verifier
results. A passing candidate enters the ordinary verification/acceptance path.
Exhaustion or no improvement ends the search; any model fallback follows the
already permitted native policy, with actual provider/attempt and quota cause
recorded. An unresolved external effect is reconciled before any retry.

Register the entire cohort before observing results. Report search-resolved,
model-assisted, unresolved and invalid-baseline cases separately, alongside the
whole-cohort acceptance and independently verified result counts. Record local
CPU/test time, inference, failures, review and recovery; zero model calls is not
zero cost. No headline speedup from trivial repairs stands in for the model-
assisted subset. Use the existing comparison record without excluding failed or
missing cases.

**Acceptance:** replay a successful small repair, unchanged/invalid baseline,
verifier failure, exhausted budget and no-improvement case. Mutating the oracle
must invalidate the trial; search cannot make a denied effect permitted. The
optional step earns retention only if matched accepted outcomes improve total
burden under a predeclared criterion. Rollback disables it, preserves evidence
and returns to the prior native workflow.

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
