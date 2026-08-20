---
doc_id: vuoro-execution-federation-alignment-2026-08-20
status: current
supersedes:
  - vuoro-cp-2026-01-multi-host-control-plane
  - portable-governed-execution-future-state
---

# Native-runtime and execution-federation alignment

This note aligns Vuoro's future-state plans to three owner decisions that
postdate the plans they affect:

- ActionQ's adopted constraint is **not to own an agent execution plane**.
  Its target is a federation layer with external-execution references,
  assurance metadata, reconciliation, and owner-defined acceptance semantics;
  the worker daemon, queue, claims, leases, runner, and fan-out engine are
  deletion targets.
- Sprintctl v0.3 reservations are credential-free advisory coordination. They
  permit overlap, report conflicts, and support deliberate interruption. They
  are not exclusive claims, leases, fencing tokens, or mutation authority.
- Outctl was removed from the canonical Vuoro project binding. It is not a
  required Vuoro member or a future execution/evidence-plane dependency.

The governing inputs are ActionQ `HANDOFF.md`,
`docs/plans/2026-08-19-execution-plane-deletion-constraint.md`, and
`docs/plans/2026-08-20-execution-plane-deletion-order.md`; Sprintctl
`origin/main` at `15afc8762ce99beb3ec0239a5ce4a8713dd6f934`, especially
`docs/plans/v3-reservation-model-plan.md` and
`docs/protocols/reservation-model.md`; and Agentops merge `ac710c7`, which
removed Outctl from the canonical Vuoro project membership.

## Current future-state boundary

| Concern | Current direction |
| --- | --- |
| Agent execution | Claude Code, Codex, OpenCode, Copilot, or another qualified product-native runtime executes directly. Provider-native subscription, session, context, and configuration semantics remain visible rather than being hidden behind a homemade runner. |
| Cross-runtime coordination | ActionQ federates externally performed executions. It records provider/handle references, observed status, binding-assurance level, required evidence, acceptance, and reconciliation without spawning or supervising the agent process. |
| Work coordination | Sprintctl stores plans, work state, dependencies, revisions, and advisory reservations. An overlap is a visible coordination conflict, not a rejected acquisition or authorization failure. |
| Service composition | Vuoro transports and composes released owner capabilities. It does not add `vuoro-runnerd`, an execution queue, placement, leases, harness adapters, a raw-output store, or lifecycle inference. |
| Evidence | Native runtimes and ordinary host tools retain their own bounded output. Git holds authored artifacts; Auditctl holds durable findings; ActionQ holds federated execution/evidence references and assurance metadata. Raw output may remain host-local. No Outctl service or contract is required. |
| Deployment | Appservice may deploy the Vuoro service and owner adapters, but no namespaced Vuoro execution control plane follows from this note. |

Released ActionQ adapters, historical execution receipts, qualification
records, and prior deployment evidence remain valid statements about the
versions that produced them. They do not establish the future ownership of a
daemon, queue, lease, runner, or Outctl boundary.

## Plan disposition

- `2026-08-15-vuoro-control-plane-implementation-plan.md`, its generated
  YAML, and its companion DOCX snapshot are superseded for backlog creation.
  The binary snapshot remains historical and the Markdown status governs. Do
  not implement `vuoro-runnerd`, ActionQ placement/lease/capacity machinery,
  the Outctl evidence envelope, or the namespaced execution control plane
  from those preserved task bodies.
- `../architecture/portable-execution.md` is retained as the historical
  owner-staged runner design. Its envelope, immutable-candidate, grouping,
  lease, and runner milestones are not the future-state implementation plan.
- `Vuoro_Interface_Agent_Runtime.md` retains useful operator-interface and
  comparative-runtime questions, but native products are now the execution
  baseline. A Vuoro-native runtime or common harness adapter layer must not be
  built merely to recreate the deleted execution plane.
- Existing release, promotion, canary, and composition-evidence documents are
  historical evidence and are not rewritten by this alignment.

## Takeover experiment, redefined

The host-local `cluster-alignment-mvp` package has not landed in a Git
authority. Its cheap-explorer/premium-planner experiment remains useful with
the following execution model:

1. A native runtime performs the bounded, non-mutating exploration.
2. Sprintctl records the work item, material findings, checkpoint, planner
   decision, and an advisory reservation. Another reservation may overlap and
   must be surfaced; no exclusive acquisition, token, heartbeat, or lease is
   required.
3. A stronger native runtime performs the planner takeover at the existing
   deterministic checkpoint. `CONTINUE`, `REFRAME`, `EXECUTE`, and `STOP`
   retain their existing meanings.
4. ActionQ may register the two external executions and reconcile their
   provider handles, assurance levels, evidence references, and outcomes. It
   does not run either session.
5. Bounded native output or host-local captures support selective inspection.
   Material durable findings go to Auditctl and authored changes to Git. The
   experiment neither requires Outctl nor promotes Sprintctl into a log store.
6. A mutation still requires separately authorized executor capability.
   Credential or RBAC expiry may bound that capability, but it is distinct
   from a Sprintctl reservation.

The experiment's falsifying gates do not change. It loses support if:

1. planner intervention is required so frequently that the cost/context benefit disappears;
2. the planner must reopen most raw captures to reconstruct the situation;
3. cheap explorers frequently form a harmful investigation direction before checkpoints despite non-mutating scope;
4. recording events materially degrades agent execution;
5. premium-only operation materially outperforms the split model on important findings or operational correctness;
6. Outctl evidence references are rarely useful to planners;
7. fresh-session reframing loses critical context often enough that transcript continuity is actually superior; or
8. capability bounding requires so much custom machinery that the orchestration savings disappear.

Gate 6 retains the original package wording so the falsifier is not weakened.
For the aligned run, `Outctl evidence references` is the legacy label for the
same capability under test: selectively retrievable bounded evidence from the
native runtime or host, optionally registered as an ActionQ federation
reference. It does not require the Outctl product.

## Decisions still held by the operator

This alignment does not make the two deletion decisions recorded by ActionQ:

1. whether to remove the declared `actionq-server` cluster deployment; and
2. whether to stop `actionq-dispatch.service` on devbox permanently.

It also does not authorize a cluster dogfood run, mutation, release,
deployment, or migration. Those remain separately approved actions.
