---
doc_id: vuoro-architectural-simplification-alignment
status: ratified
ratified_at: 2026-07-28
ratified_by: operator
supersedes: []
---

# Vuoro architectural simplification alignment

This owner-local plan aligns Vuoro with the cross-repository
`vuoro-substrate-simplification-refactoring` assessment in Agentops. Sprintctl
items remain the execution authority; this document records only Vuoro-owned
scope and boundaries.

## Current state

- Shared service request fields landed at `cb825c9` under Vuoro #2024. Both
  public wire models remain and v1 cannot carry transient credentials.
- Immutable release wheel URLs and adapter revision provenance landed before
  this plan. They are evidence for R10, not completion of its remaining
  descriptor decision.
- `docs/architecture/portable-execution.md` is ratified. Vuoro composes
  released execution capabilities and does not own plans, claims, leases,
  runner isolation, candidate publication, or audit findings.
- Client recovery export exists. The service-side in-memory reconciler
  (`vuoro_service.recovery.RecoveryReconciler`) was dead code — never wired
  into `create_composed_app()` — and was deleted on 2026-08-13 to close V-S2
  as disposition option 1.

## Owner-local units

### V-S1 — Finish safe invocation/profile consolidation (R8)

**Sprintctl:** Vuoro #2041 (following service-only #2024).

Share the client v1/v2 invocation mechanics without changing either wire
contract, credential behavior, schema validation, or stale-catalog handling.
Move reusable profile loading and validation into `vuoro-client`; Sprintctl
must lazy-import it only in served mode.

Gate with client and service package tests plus the repository boundary suite.

### V-S2 — Decide the recovery prototype disposition (R9)

**Sprintctl:** Vuoro #2042.

**Resolved 2026-08-13: disposition option 1.** Inventory (grep across the
composed service and its consumers) confirmed `RecoveryReconciler` had zero
references outside its own module and its own dedicated test —
`create_composed_app()` never imported or wired it. The hard constraint
(Vuoro must not become recovery authority; no in-memory production decision
path) was not being violated in practice, since the path was unreachable,
but the disposition itself had never been formally chosen. Deleted
`packages/vuoro-service/src/vuoro_service/recovery.py` and
`packages/vuoro-service/tests/test_recovery_reconciler.py`; retained
`vuoro-client`'s `RecoveryLog`/CLI export path (`vuoro recovery
begin|observe|request-command|export`) as the sole recovery surface. Full
`vuoro-service` test suite (174 tests) passes unchanged after the removal.

Original options considered:

1. ~~retain local client export and remove the disconnected service
   reconciler~~ — **chosen**; or
2. route versioned recovery operations to a durable domain-owned adapter.

Vuoro must not become recovery authority and an in-memory production decision
path is forbidden.

### V-S3 — Separate release locks from runtime descriptors (R10)

**Sprintctl:** Vuoro #2043.

Keep domain composition explicit. Define which fields prove released artifact
identity and which fields are needed at runtime; cryptographically bind the
installed artifact to the locked release. Do not generalize away domain DSN,
schema, compatibility, credential, or migration-role boundaries.

Gate with composition tests, immutable-artifact checks, both package builds,
and the repository boundary suite.

## Cross-repository dependencies

- Agentops owns browser/MCP dispatch-contract projections and cockpit
  presentation.
- Sprintctl owns local/served mode retirement and owner-only Postgres
  administration.
- Actionq owns the canonical execution kernel and coordinator lifecycle.
- Deployment, release publication, and Appservice configuration require their
  owning repositories and are outside this plan.
