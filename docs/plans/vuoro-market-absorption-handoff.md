---
doc_id: TBD  # assign only if this draft is ratified
status: assessed-draft
supersedes: []
title: Vuoro — market-absorption posture, handoff to implementation planning
date: 2026-07-29
note: >
  2026-08-22 — this draft's planning question ("which parts are worth owning
  through the transition") is answered by 2026-08-22-long-term-direction.md,
  §2 ("How the intention arrived here") and §3 (ideological rules). Body
  retained unchanged for provenance.
---

# Purpose

Input to an implementation planning session. Distilled from an external
forecast document on commercial harnesses absorbing agent-orchestration
functionality, plus assessment of it.

Operating assumption (settled, not up for planning): market tooling will
co-opt parts of Vuoro. That is an accepted outcome, not a risk to mitigate.
Planning question is only which parts are worth owning through the
transition.

# Assessment and ratification boundary

Assessed 2026-07-29 against the live Sprintctl backlogs, the ratified
[portable-execution architecture](../architecture/portable-execution.md), and
the shared Auditctl NDJSON shards. This assessment does not ratify D1–D7.

The following are already-settled ecosystem constraints and do not need a new
market-absorption decision:

- Sprintctl retains work definition, readiness, dependencies, and acceptance.
- Actionq retains action, claim, lease, retry, cancellation, and terminal
  outcome semantics.
- Runner implementations own harness invocation, workspace effects,
  verification, and immutable candidate publication.
- Auditctl retains independent findings and rebuildable NDJSON evidence.
- Vuoro composes released owner capabilities. It does not become a planner,
  runner, queue, audit authority, or internal MCP domain model.
- No executor SDK or behavioural interface is justified before two independent
  runner implementations exercise the owner-defined portable contracts.

The following remain proposed human decisions:

- whether export/reconstruction must cover only authoritative semantic records
  or also bulky operational exhaust;
- the exact retained and forbidden Actionq capability set;
- whether every roadmap component must receive the
  strategic/mechanism/disposable classification;
- whether the cockpit should be deliberately narrowed or merely allowed to
  stagnate;
- whether the available incident history is sufficient to commission an
  initial conformance corpus.

Until those decisions are ratified, downstream work is `dispatch-plan`, not
`dispatch-build`.

# Live-state disposition

Existing work already covers substantial parts of the proposal:

- agentops `#2036` and Vuoro `#2037` cover portable envelopes and composition
  without moving execution authority into Vuoro;
- agentops `#2061` parks native-runtime comparison until two portable runners
  have demonstrated the common seam;
- Actionq `#1445` parks an external-runtime contract until an external runtime
  is actually selected;
- Sprintctl `#1162`, `#1163`, `#1219`, and `#1233` provide the implemented
  projection/watermark and remote-to-recovery export precedent;
- Vuoro `#2042` owns disposition of the disconnected-recovery prototype;
- agentops `#2039` already governs cockpit convergence on owner-mediated Vuoro
  operations.

Do not create generic executor, mirror, recovery, cockpit, or native-runtime
implementation items from this draft. The missing backlog unit is a bounded
cross-owner ratification and authoritative-record inventory.

# Audit evidence available for D6

The repository-local Auditctl ledger returned no events. The shared immutable
shards under `/projects/dev/_artifacts/*/audit/` contained 102 events:

- 88 `knowledge.landed`;
- 11 sprint open/close/takeup/release events;
- two Git commit events;
- one manual rollout-validation event.

Only two event details describe an operational failure, and both are the same
failure family: audit publication was unavailable because
`AUDITCTL_ARTIFACTS_ROOT` or repository wiring was missing, followed by a late
backfill using the original event timestamp. This can seed one conformance
history covering non-fatal publication failure, late durable backfill, stable
identity, and rebuild. The current evidence does not justify an eight-scenario
corpus, and it contains no recorded Actionq claim/lease, runner cancellation,
candidate publication, or settlement incident.

D6 should therefore be interpreted as a cap, not a target. Add scenarios only
when a real incident record identifies the failure and expected recovery
semantics. Synthetic protocol histories may still verify owner contracts, but
must be labelled as such rather than presented as incident-derived.

# Proposed decisions to carry into human ratification

## D1 — Freeze data contracts, defer behavioural contracts

Freeze now: task packet schema, execution event schema, evidence schema.

Do **not** define an `Executor` interface (Prepare/Start/Observe/Cancel/
Reconcile/etc.) until a second backend exists and consumes it. An interface
derived from one implementation is that implementation's shape with
`interface` typed above it.

Rationale: data contracts survive vendor churn; method sets do not. Backends
differ most where such an interface flattens them — what an observation is,
whether cancellation is real, whether evidence survives session end.

## D2 — Export/reconstruct as the semantics guarantee

Proposed new cross-owner planning item. The authoritative record set must be dumpable to plain,
human-readable, git-committable files at any moment, and reconstructable
from that dump.

This, not adapters, is the insurance policy. Adapters protect execution;
export protects semantics. Cheap, testable today, requires predicting
nothing about the market.

## D3 — Model external work state as mirror + watermark, not as executor

Anticipated integration failure mode is dual ownership, not adapter
mismatch. Commercial bundles built around issues/PRs will insist on owning
work state rather than accepting packets as dumb executors.

Reuse the sprintctl remote-feeding-local model: cursor/watermark read cache
with explicit conflict rules, not symmetric mirroring. Design cost already
paid there.

## D4 — Narrow Actionq

Enumerate explicit non-scope. Candidate exclusions: arbitrary DAG
execution, general cron scheduling, broad event transformation, worker
discovery, generic approval workflows.

Retain: dispatch identity, lifecycle transitions, claim/lease state, retry
identity, executor references, terminal observations, reconciliation
commands.

Consistent with existing position that dispatch/executions are thin
wrappers.

## D5 — Disposability classification pass over the roadmap

Three classes, not four:

- **strategic** — semantics expected to outlive any vendor
- **mechanism** — required now, replaceable
- **disposable** — allowed to stagnate or vanish

Collapse "convenience" and "experimental"; both resolve to *not
contractual*, and keeping them separate only creates a future argument.

Review question per component becomes: *would losing this destroy
information, or merely require another mechanism?*

## D6 — Conformance scenarios: incident-derived, capped

Derive scenarios from failures actually observed in auditctl history. Cap
initial set small (~8). Do not author a speculative 40+ scenario corpus.

The live evidence currently supports only one failure family. Eight is a
ceiling, not an initial quota.

Stated purpose is regression-testing own recovery paths. Vendor-procurement
framing is rejected — that evaluation will not be run at that rigour by a
single operator.

## D7 — MCP at the boundary only

MCP remains an external access surface alongside CLI and API. It does not
become the internal domain model. Restated as a standing constraint, not a
new decision.

# Open questions for the session

Require live sprintctl / auditctl state — defer to it, not to this doc.

1. **Export scope** — which streams and tables constitute the authoritative
   record set for D2? Work graph and findings clearly; execution logs and
   session transcripts unclear.
2. **Actionq non-scope enforcement** — convention, lint, or structural
   (i.e. capability simply absent)?
3. **Incident inventory** — what failure classes has auditctl actually
   recorded? D6 cannot be scoped without this.
4. **Cockpit disposition** — shrink deliberately now, or classify as
   disposable and let it stagnate? Affects whether auditctl needs its own
   presentation path.

# Explicitly out of scope for this handoff

Carried over from the source document and rejected:

- Market forecast and its probability estimates — no decision depends on
  them
- Seven-method executor interface as drafted (see D1)
- 45-scenario conformance corpus (see D6)
- Organizational policy enforcement, spend/throughput analytics, human
  approval workflows — enterprise features with no user in a
  single-operator substrate
- New "standards" / "protocol" / "executor SDK" repository — not before two
  independent consumers

# Framing to preserve

Planned partial obsolescence is a success state. Target outcome is that
half of Vuoro can be deleted when the bundle arrives without losing the
process semantics encoded in the other half.

---

Status is `assessed-draft`. D1–D7 remain subject to human ratification. This
assessment created planning state only; it made no commit, deployment, or
runtime-code change.
