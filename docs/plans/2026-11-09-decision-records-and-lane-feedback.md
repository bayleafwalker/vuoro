---
doc_id: vuoro-decision-records-and-lane-feedback-2026-11-09
title: Decision records and practical lane feedback
purpose: proposal
lifecycle: ratified
effective: 2026-11-09
subjects: [evidence, learning, capacity, continuation]
related: [vuoro-product-delivery-plan-2026-10-17, vuoro-accepted-outcome-learning, vuoro-model-evaluation-integration-2026-10-25]
---

# Decision records and practical lane feedback

**Status:** Operator-requested planning direction, 9 November 2026. Record
shapes, adapters and budgets below are implementation proposals, not shipped
interfaces or installed qualification. This incorporates the operator's review
of the telemetry assessment and replaces its staged delivery recommendation.

Every dispatch, rerun, escalation, allocation and acceptance decision retains
its basis, eligible alternatives, actual selection mechanism, chosen action,
policy revision and validity. Later outcomes join back, including refused,
expired, superseded and unapplied recommendations. Correctness rules are
qualified through fault cases and then enforced within that scope. Efficiency
choices report quota, useful work and total burden; stronger comparisons are
optional where volume and configuration stability support them. Record depth
scales with the effect. The concurrent operating loops are **next useful
observation** and **capacity allocation across lanes and subscription windows**.
Cheap agents and bounded Jev consumers use the records; an operator UI is a
secondary projection.

## Reuse and ownership

The four Vuoro domains remain work identity, evidence, authority and learning.
[Observable resources](../architecture/observable-resources.md) supply owner
references and observations; [knowledge manifests](../architecture/knowledge-resolution.md)
bind supplied context; [accepted-outcome learning](../architecture/accepted-outcome-learning.md)
already supplies comparisons. These joins need no new writable resource graph.
Source normalization and structural validation do not authenticate owner facts.

| Owner | Addition |
| --- | --- |
| Vuoro | Portable decision/evidence links and bounded, source-qualified projections for consumers. |
| [lanectl](https://github.com/bayleafwalker/lanectl/blob/main/docs/plans/2026-11-09-decision-records-and-capacity-feedback.md) | Extend existing capacity, windows, probe, burn and learning modules; own allocation and lane decision emission. |
| [AgentOps](https://github.com/bayleafwalker/agentops/blob/main/docs/dispatch/model-routing.md) | Correlate native dispatch and closeout records; preserve existing model/data eligibility and advisory Jev boundary. |
| [Sovite](https://github.com/bayleafwalker/sovite/blob/main/docs/plans/2026-11-09-decision-observation-feedback.md) | Report actual execution/configuration, usage and observation gaps; link admitted application to the decision. |
| Work and target owners | Retain acceptance, effect authority, native deduplication and recovery. |

Baseline inspected: Vuoro `aa5d683`, lanectl `4335a1c`, AgentOps `8ad2667`,
Sovite `1687ecc`. Existing lanectl capacity code is not proof of provider quota:
its allocation policy labels its budgets and estimates as placeholders.

## Decision record: a small envelope, referenced detail

Extend existing owner events/evidence; do not mint a competing acceptance
record. The semantic minimum is:

- Stable decision ID, producer/owner and decision kind; Work/Release,
  attempt and operation references where applicable. An allocation instead
  names its account pool/window and links its downstream dispatch decisions.
- Compact basis snapshot or immutable reference: the facts available then,
  freshness, missing inputs and the exact eligible alternatives. Retain
  exclusions by reason. Reuse stored policy/configuration snapshots.
- Chosen action, policy revision, selection mechanism and probabilities when
  known. A fully specified deterministic choice is 1/0; an opaque/manual choice
  is unknown. Jev confidence and normalized heuristic scores are not selection
  probabilities. Deterministic logs provide no support for unchosen alternatives.
- Effective/event time and recorded time; expiry or invalidation conditions.
  Later records link proposal, owner decision, application and outcome without
  overwriting the original basis. No full temporal database is required.

Lane gate acceptance, evidence sufficiency and terminal Work acceptance retain
their distinct owner meanings. Derive expected decision counts from owner
attempt/admission/acceptance histories, not received traces. For decisions with
no attempt, use durable owner allocation-cycle, request or proposal triggers,
including no-dispatch cycles and pre-admission refusals. Without that independent
denominator, coverage for the decision class is unknown. Report missing,
delayed, inaccessible and unsupported capture separately.

OpenInference `DECISION` describes a decision-model call, such as Jev. Link that
span as evidence; the portable record is independent of `decision.*` attributes,
which Phoenix still identifies as changing [S2].

## Concurrent feedback loops

**Next useful observation.** Extend the existing `decide_rerun` path with a
bounded observation request and its result: reacquire expired evidence, check
current work, inspect target state or reconcile an uncertain operation. Rules
remain deterministic. A suggestion confers no authority; the owning executor
rechecks current facts. Its `ACCEPT` means sufficient evidence to avoid a
rerun, not terminal Work acceptance. A model may explain or propose; it does
not replace required correctness checks.

**Quota allocation.** Extend lanectl's existing loop to join useful landed
work and total burden to account-pool consumption. Keep provider/account/pool,
unit, applicable windows, reset basis, source, freshness, reservations,
in-flight usage and unobserved other-host consumption explicit. Shared pools
cannot be counted once per lane; concurrent windows all constrain admission.
Unknown entitlement or stale measurements retain the previously authorized
static/reactive policy and disable unsupported burn claims; placeholder values
never establish entitlement or burn capacity. Allocation cannot change data
class, provider entitlement, grants or paid-fallback permission. Reallocation
does not cancel an attempt or resolve its uncertain effects.

**Rediscovery.** Count the same source/fact revision fetched across successive
sessions on the same work, joining existing context manifests. Separate
mandatory freshness checks, changed revisions, inaccessible prior content and
intentional rereads. Exact source/anchor/revision matches are direct counts;
semantic fact matching is labelled inference. Report repeated acquisitions and
their cost against covered handoffs, including unknown capture. The historical
[78 handoff-loss observations](../evidence/2026-08-22-agentic-eventstorm.html)
motivate this work; they are not a frozen rediscovery corpus or a current rate.

## Correctness, efficiency and overhead

Fault injection qualifies correctness for the named target/configuration:
lost acknowledgement, changed payload, stale authority, duplicate delivery,
concurrent writer and recovery. Requalify affected guarantees after semantic
changes. Correct reconciliation remains required regardless of its measured
speed. Reuse target primitives: the selected publication proof already uses a
PostgreSQL transaction for pointer and journal; Delta's transaction identifiers
would be native deduplication inputs for a Delta adapter [S3]. The added value
is preserved resource/effect-intent lineage and cross-session recovery, not
reimplementing the target transaction.

Efficiency reports include failures, rework, operator effort, record/evaluator
overhead and model/configuration churn. Treat model, harness and realized
environment as a joint execution configuration: resources can change which
strategies succeed [S1]. Keep results directional unless a declared comparison
supports more. Propensity logging preserves future options; offline policy
evaluation and confidence sequences are not delivery prerequisites.

| Record depth | Retain | Proposed overhead budget |
| --- | --- | --- |
| Routine/read-only or disposable candidate | Compact envelope, reusable basis/policy refs, outcome link; no narrative or new inference call | At most 2 KiB additional metadata per decision; target p95 local capture at most 10 ms. |
| Allocation/escalation or material continuity change | Envelope plus scoped pool/window/configuration and reason evidence | Amortize snapshots; optional inference allowance at most 1% of already-observed non-adviser usage in the same account pool/window and units. |
| Protected effect or owner acceptance | All correctness/authority/receipt evidence required by that owner | Measure separately; never omit required facts to meet the optional-analysis budget. |

These are proposed engineering budgets, not measured performance. Before an
optional model call, reserve its bounded maximum charge/consumption against
that allowance, including outstanding adviser reservations. An unknown/zero
denominator or unavailable reliable bound disables optional calls; deterministic
rules remain. Reconcile actual usage afterward and stop further optional calls
on overrun. An accounting allowance is not a provider-enforced quota.
Measure referenced snapshot storage as well as envelope bytes, capture latency,
extra inference/verification and operator maintenance. On overrun, drop optional
detail or advice, retaining the minimum
decision/outcome record and all owner-required evidence. Raw context and
private selection rationale remain within their existing access/class bounds.

## Delivery and checks

Proceed concurrently with decision emission, deterministic observation,
source-qualified capacity and rediscovery where their inputs exist. None waits
for the protected-publication demo, a cockpit or a model-comparison sample.
Each owner versions its contract before emitting new fields/events.

Qualification must show: a cheap consumer resolves a bounded next action;
refused/expired choices and missing decisions remain visible; reset uncertainty,
shared-pool use and in-flight work cannot manufacture capacity; identical
source reacquisition is distinguished from a required refresh; and overhead
stays within the chosen profile. Join qualified owner outcomes to the existing
learning records without changing their acceptance meaning.

Rollback disables optional advice/new allocation policy and restores the prior
deterministic/static policy, preserving decision history and correctness rules.
It never automatically cancels or repeats in-flight work. External-effect
recovery remains with its owner.

### Source checks, 9 November 2026

- **S1:** [Anthropic infrastructure noise](https://www.anthropic.com/research/infrastructure-noise): model/resource interaction, not an additive OOM adjustment.
- **S2:** [Phoenix span kinds](https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/span-kinds) and [October release note](https://arize.com/docs/phoenix/release-notes/10-2026/10-01-2026-trace-tree-evals-and-decision-spans): decision-model observations and evolving attributes.
- **S3:** [Delta idempotent writes](https://docs.delta.io/delta-batch/#idempotent-writes): preserve transaction identity and payload; [transaction retention](https://docs.delta.io/table-properties/) bounds deduplication history.
- **S4:** [Vowpal Wabbit decision logging](https://vowpalwabbit.org/docs/vowpal_wabbit/python/latest/tutorials/python_Contextual_bandits_and_Vowpal_Wabbit.html): action-selection probability, distinct from confidence that an action is correct.
