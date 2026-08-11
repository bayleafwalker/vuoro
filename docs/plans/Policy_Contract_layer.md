Policy_Contract_layer
# Agentic Work Control Plane

*Design handoff: policy, derived contracts, execution events, observers, and analytical projections*

| **Status** | Conceptual direction agreed            | Implementation not yet selected                   |
|------------|----------------------------------------|---------------------------------------------------|
| **Scope**  | Agentic software-development workflows | Designed to generalize beyond one runner or model |

Naming note: this document uses “sprint control / sprintctl”, “knowledge control / KCTL”, and “audit control” as the existing system boundaries described in the session.

> **Decision snapshot**
>
> - Do not build “Git for actions” or another task store.
> - Add a policy layer adjacent to work-state, dispatch, knowledge, and audit systems.
> - Treat a contract as a session- or invocation-specific proposal constrained by policy—not as a deterministic projection that policy must generate.
> - Make contracts cheap and normally ephemeral; persist only when policy or irreversibility requires it.
> - Emit execution events once. Keep observers factual. Run analysis separately. Ratify policy changes through an explicit governance path.
> - The main product value is shared policy consumption plus reusable observations and projections—not policy files themselves.

# 1. Problem being solved

**Current state.** Operating modes, autonomy levels, model tiers, cost controls, release behavior, worker delegation, and evidence capture are spread across prompts, skills, session notes, and repository-specific configuration. The coordinator often has to remember both the implementation task and the analytical instrumentation.

**Failure mode.** Prompt discipline becomes the control plane. Important observations are omitted, context grows, analytical work competes with delivery work, and configurations are copied between repositories without a stable semantic model.

**Desired outcome.** A coordinator can focus on implementation while the surrounding system determines which contracts are admissible, equips workers with minimal scoped instructions, captures execution facts, and produces reusable projections for later analysis.

# 2. Architectural direction

| **Layer**            | **Owns**                                                                                                                                                                | **Must not own**                                                                |
|----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| Sprint control       | Work streams, work items, claims, dependencies, blocked/unblocked state, current execution state.                                                                       | Governance semantics, analytical conclusions, or long-term knowledge promotion. |
| Policy control       | Global and local constraints, defaults, overrides, delegation limits, evidence requirements, retention modes, and allowed contract space.                               | Selecting the single “correct” contract or owning project work state.           |
| Contract projection  | A concrete operating agreement for one session, invocation, worker, or sub-worker: objective, authority, constraints, resources, stop conditions, evidence obligations. | Permanent source-of-truth state by default.                                     |
| Dispatch / execution | Runner selection, worker invocation, action execution, and emission of normalized events.                                                                               | Policy authorship or retrospective analysis.                                    |
| Audit / observation  | Capture, normalization, correlation, and readable projections of events and state.                                                                                      | Recommendations, prescriptions, or policy mutation.                             |
| Analysis workload    | Human- or agent-run interpretation of observations; comparisons, conclusions, and recommendations.                                                                      | Direct policy change unless separately authorized.                              |
| Knowledge control    | Promotion of validated findings, reusable patterns, decision context, and durable knowledge.                                                                            | Raw event ingestion as its primary interface.                                   |

Boundary rule: observations report what happened; analyses explain what it may mean; policy processes decide what is allowed next.

# 3. Policy and contract model

**Policy.** A versioned description of admissible behavior within a scope. Policies can be global, repository-local, environment-specific, role-specific, or session-overridden where permitted. Git is an appropriate storage and review mechanism; the missing capability is consistent consumption and evaluation across runners.

**Contract.** A chosen or proposed operating agreement for a particular unit of execution. It combines intention, current work state, actor capability, environment, and selected operating mode. Policy validates whether the proposed contract is allowed; it does not need to generate the contract mechanically.

**Contract validity.** The important property is admissibility under the effective policy at the time—not byte-for-byte reproducibility. Two sessions may produce different valid contracts from the same policy and intention.

## Contract contents

- Objective and scope of work.
- Actor or worker class and available capabilities.
- Authority: files, branches, environments, deployment targets, credentials, and direct-to-main permissions.
- Delegation rights: whether sub-workers are allowed, which worker/model classes may be used, fan-out limits, and budget ceilings.
- Operating mode: interactive, delegated, background/autopilot, planner depth, and escalation behavior.
- Stop conditions and failure handling.
- Evidence and observation obligations.
- Retention mode for the contract and generated assignments.

## Delegation rule

**Attenuation by default.** A coordinator may project subcontracts, but a subcontract cannot exceed the coordinator’s own effective authority. It may narrow scope, budget, capabilities, or duration; expansion requires an explicitly authorized re-evaluation.

**Minimal context transfer.** Workers should receive a contract reference or compact materialized view plus the task-specific context they need. The coordinator should not carry every subcontract verbatim in its active context.

# 4. Contract persistence and cost implications

**Default.** Contracts are derived, short-lived execution material—not durable records. Keeping every contract would create a second, noisy history without guaranteed analytical value.

**Persistence is itself policy.** The system should support retention levels rather than one universal rule.

| **Mode**     | **Persist**                                                                                                | **Use case**                                                             | **Trade-off**                                           |
|--------------|------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|---------------------------------------------------------|
| Ephemeral    | No full contract; keep execution correlation ID and essential outcome facts.                               | Routine low-risk implementation.                                         | Lowest storage and context cost; weaker reconstruction. |
| Delta        | Persist only non-derivable choices, overrides, approvals, or exceptions.                                   | Normal governed work where human judgment matters.                       | Good default balance.                                   |
| Materialized | Persist the effective contract and worker assignments.                                                     | Benchmarking, regulated work, expensive runs, or incident-prone changes. | Higher volume; strongest replay and forensic value.     |
| Forensic     | Persist contract, policy snapshot/hash, prompts or manifests, events, outputs, assignments, and approvals. | Investigations and controlled experiments.                               | Expensive and noisy; enable intentionally.              |

**Cost objective.** Move repetitive governance and instrumentation out of the expensive coordinator context. Store addressable artifacts and event streams externally; load compact views on demand. The coordinator should retain only the active objective, current decision boundary, relevant work state, and handles to delegated work.

# 5. Event, observer, and analytics model

**Execution events are the shared substrate.** Runners and workers emit normalized facts about what was requested, selected, attempted, consumed, changed, produced, and verified. Events should be independent of any single analytical question.

**Observers remain non-prescriptive.** They subscribe to execution events and state changes, correlate them, and create readable projections. They do not decide whether a model is good, whether a policy should change, or what configuration should be preferred.

**Analysis is a separate workload.** A human or analysis agent consumes projections to compare alternatives, test hypotheses, and write recommendations. The analysis output is a report or proposed change—not a policy mutation.

## Example: evaluating worker/model economics

1. Intent marks the run or work stream as part of a model/provider evaluation.
2. Effective policy enables deeper capture for relevant sessions and workers.
3. Execution emits provider, model, role, task class, tokens, latency, retries, tool use, cost, completion state, verification results, and downstream rework signals.
4. Observers build per-session and cross-session projections without distracting the implementation coordinator.
5. A separate analysis session compares models such as inexpensive workers versus frontier alternatives for cost, throughput, quality, and rework.
6. Any recommendation to alter defaults, budgets, routing, or policy proceeds through the normal review and ratification path.

## Core event families

- Intent and contract lifecycle: proposed, validated, denied, overridden, started, superseded, completed.
- Delegation: assignment created, claimed, subcontract issued, worker started/stopped, result accepted/rejected.
- Resource use: model/provider, tokens, compute time, wall time, tool calls, monetary cost, quotas.
- Work state: files changed, commits, tests, releases, deployments, rollbacks, incidents, verification evidence.
- Quality signals: task outcome, reviewer feedback, rework, regression, acceptance, confidence, unresolved questions.
- Knowledge/audit: observation produced, analysis linked, finding promoted, policy proposal linked, policy decision recorded.

# 6. What the core tooling should become

**Not a monolith.** The design is a small control-plane kernel plus adapters. Existing systems keep their responsibilities; the new work standardizes policy evaluation, contract materialization, event emission, correlation, and projection interfaces.

## Minimum core components

| **Component**           | **Responsibility**                                                                                                                                                  |
|-------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Policy resolver**     | Loads global and local policy, applies permitted overrides, resolves precedence, and produces an effective policy view.                                             |
| **Contract API**        | Accepts a proposed contract, validates it against policy and current state, returns allowed/denied plus reasons, and optionally materializes a compact worker view. |
| **Delegation guard**    | Checks attenuation, budget, worker class, fan-out, and environment authority before subcontracts are issued.                                                        |
| **Event envelope**      | Defines common identifiers, timestamps, actor/run/work-item links, event type, payload version, policy/contract references, and evidence links.                     |
| **Event sink / log**    | Stores or forwards normalized events according to retention policy. It should support replay into projections without forcing a single database choice.             |
| **Observer runtime**    | Runs opt-in factual consumers and writes projections such as session summaries, cost ledgers, model-performance datasets, and deployment timelines.                 |
| **Projection registry** | Makes derived artifacts discoverable by work stream, intent, model, provider, repository, time window, and analysis purpose.                                        |

## Reuse rather than rebuild

- Keep policy definitions in Git with normal review, ownership, and history. Evaluate existing policy engines only if the rule language or authorization model justifies the integration cost.
- Use existing tracing/telemetry platforms where they can ingest the event envelope, but do not let their proprietary trace model become the domain model.
- Keep runner-specific configuration in adapters. The control plane should describe semantics such as “expensive worker allowed” rather than hard-code one provider’s configuration keys.

# 7. Recommended implementation sequence

**1. Event envelope first.** Define a small, versioned event schema and correlation model. Instrument one existing coordinator and one worker path. Capture facts only.

**2. One analytical intent.** Implement the model/provider cost-performance evaluation use case. This forces useful events, retention choices, and projections without inventing a universal ontology.

**3. Policy activation.** Add policy controls that enable observers, select evidence depth, constrain worker/model classes, and set contract retention mode for that intent.

**4. Contract validation.** Introduce a proposed-contract API for coordinator and worker invocations. Begin with a compact schema and explicit denial reasons. Avoid a sophisticated rule language initially.

**5. Delegation guard.** Enforce attenuated authority and budget across sub-workers. Move subcontract detail out of coordinator context into addressable artifacts or compact materialized views.

**6. Projection and analysis handoff.** Produce a clean dataset/report bundle that a separate human or agent analysis session can consume without reopening implementation transcripts.

**7. Generalize only after evidence.** Add new observers and policy concepts when a second real use case proves the abstraction. Do not pre-model every workflow mode.

# 8. MVP acceptance criteria

- A coordinator can start a governed implementation session without copy-pasting analytics instructions.
- The same work can run interactively or delegated by changing a proposed contract/session mode, without changing the work-item model.
- Policy can allow or deny direct-to-main changes, releases, deployments, expensive workers, sub-worker fan-out, and enhanced observation.
- A sub-worker receives less context than the coordinator and cannot exceed the coordinator’s authority.
- Implementation traces are sufficient to build a model/provider cost-performance projection after the fact.
- Observers produce facts and summaries but no prescriptions.
- An analysis session can consume projections without requiring the implementation coordinator’s live context or full transcript.
- Contract retention can be switched between ephemeral, delta, materialized, and forensic modes by policy.
- Removing the new layer leaves sprint control, knowledge control, and audit control independently coherent; integration is additive, not invasive.

# 9. Explicit non-goals and rejected directions

- No general “Git for intent/actions” datastore. Git already versions policy and repository state adequately; a new history mechanism must earn its existence.
- No append-only dogma. Histories may be rewritten, summarized, or compacted according to repository and audit policy.
- No requirement that contracts be deterministic or globally canonical.
- No requirement to persist every worker contract or assignment.
- No observer-driven policy changes and no hidden self-modifying control loop.
- No single strict contract language that attempts to encode all human judgment.
- No coordinator prompt that simultaneously acts as executor, auditor, analyst, and policy engine.

# 10. Risks and design tests

**Configuration with better branding:** If the policy layer only relocates YAML, it has failed. Success requires shared semantics, consistent consumption, actionable denial reasons, and reduced prompt/context burden.

**Semantic overreach:** A universal schema will become ceremony. Begin with a small event envelope and use-case-specific payloads; stabilize only repeated concepts.

**Telemetry volume without questions:** Capture depth must be intent- and policy-driven. Default to low-cost events; enable forensic capture intentionally.

**False quality metrics:** Cost and token counts are easy; useful quality signals are harder. Include acceptance, rework, regression, and verification evidence rather than inventing a single score.

**Authority leakage through delegation:** Subcontracts must be checked at issuance and at sensitive actions, not merely stated in a prompt.

**Tool lock-in:** Adapters should translate control-plane semantics into provider-specific settings. Persist neutral events and evidence links.

**Coordinator remains bloated:** Measure coordinator context size, repeated instruction tokens, and context devoted to instrumentation before and after the MVP. If those do not fall, the architecture is not delivering its main benefit.

# 11. Open decisions for the first implementation

- Canonical policy precedence: global → organization/user → repository → environment → session override, including which layers may forbid overrides.
- Initial contract serialization and whether materialized contracts use content-addressed IDs, ordinary run IDs, or both.
- Event transport and storage: local JSONL, SQLite/PostgreSQL, OpenTelemetry-compatible export, message bus, or a deliberately pluggable sink.
- Minimum quality/evidence signals for the model/provider evaluation use case.
- How observers are selected: policy labels, intent tags, work-item metadata, explicit session switches, or combinations.
- Which decisions require action-time enforcement versus start-time validation only.
- How audit control, KCTL, and sprint control expose stable references without coupling their internal schemas.

> **Recommended first build**
>
> Instrument one real model/provider evaluation workflow with a neutral event envelope, intent-triggered observation policy, and a separate analysis handoff. Add contract validation only where it removes duplicated prompts or prevents authority/cost leakage. This tests the valuable part of the design before turning it into an abstract framework.

# 12. Repository assessment and owner handoff

**Assessment date:** 2026-07-29  
**Disposition:** architectural input; not an implementation-ready Vuoro plan

This document predates the now-ratified portable-execution split in
`docs/architecture/portable-execution.md`. Its principles remain useful, but
the proposed “small control-plane kernel” is not currently one Vuoro-owned
component. Implementing it here as written would make Vuoro an authority over
policy, execution, or observation that its repository contract explicitly
forbids.

## Current implementation and active backlog

- Sprintctl owns work definition, readiness, dependencies, claims, and
  acceptance. It is not a policy store.
- Agentops owns reusable dispatch contracts and their generated projections.
  Agentops #2036 and #2040 cover immutable execution-envelope compilation and
  canonical dispatch-request semantics respectively.
- Actionq owns execution lifecycle and the portable runner boundary. Actionq
  #2031--#2035 cover runner contracts, immutable candidate evidence, a second
  runner, grouping, integration, and review actions.
- Auditctl remains the owner of independent findings and evidence. Factual
  execution receipts are not, by themselves, policy recommendations.
- Vuoro may compose released capabilities from those owners. Vuoro #2037
  already tracks that composition without acquiring planning, execution, or
  audit authority.
- Actionq #2027 and Vuoro #2028--#2030 cover observable owner resources and
  their transport projections. They do not create a general analytics event
  lake or observer-driven policy loop.

These are future backlog commitments, not shipped Vuoro behavior. The shipped
Vuoro service currently provides a compatibility-gated operation catalog and
invocation shell; it does not provide the policy resolver, contract API,
delegation guard, event sink, observer runtime, or projection registry
described above.

## Required decision before implementation

The ecosystem must first assign an owner for policy resolution and ratification
and define its authority boundary. The decision must state:

1. which repository owns policy schemas, precedence, override prohibition, and
   versioning;
2. which runtime performs start-time and action-time admission checks;
3. how effective-policy and proposed-contract references enter immutable
   execution envelopes without copying Sprintctl work state;
4. which factual events and receipts existing owners already emit, and the
   smallest neutral correlation envelope still missing;
5. whether retention applies to policy/contract evidence or to owner-domain
   histories, without creating a second authoritative history; and
6. how recommendations become reviewed policy changes without giving
   observers, analyses, Vuoro, or runners mutation authority.

Until that decision is ratified, no Vuoro runtime implementation should be
started. In particular, do not add policy evaluation, a generic event store,
observer scheduling, or policy mutation to `vuoro-service`.

## Vuoro-owned follow-through

After an owning repository publishes a released, versioned policy/admission
capability, Vuoro may decide whether to expose it through the immutable
operation catalog. That later composition must preserve:

- transport-only `vuoro-client`;
- owner-defined authority and denial reasons;
- compatibility checks at startup with no automatic migration;
- no deployment-selected catalog contents;
- no interpretation of owner lifecycle or analytical conclusions; and
- no contract or event persistence in Vuoro merely for convenience.

The relevant planning item is **Vuoro #2059**. It is intentionally a
decision/composition-seam item rather than permission to implement the
conceptual kernel in this repository.
