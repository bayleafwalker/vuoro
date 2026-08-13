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
- Composition v3 is released: it separates adapter release locks from
  owner/shared dependency locks without generalizing away domain construction.
  `vuoro-adapter-kit` and `vuoro-schema-runtime` 0.1.0 are immutable GitHub
  Release wheels, not PyPI packages. Kctl 0.1.2, auditctl 0.1.1, and ActionQ
  0.1.21 are the first three adapter-kit consumers; no domain has adopted the
  schema runtime yet. Sprintctl is the remaining P2.3 migration.
- Sprintctl P2.3 promotion is now prepared against the immutable 0.2.24
  wheel at source `75fd4a7bc01472f941c923444cabe6451bb1afd0`, with the shared
  adapter-kit lock reused rather than duplicated. The released-work gate
  covers the exact 43-operation owner metadata hash, scoped invocation,
  project routing, resource registration/result decoding, and the unchanged
  four-domain catalog revision.

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

**Completed 2026-08-13.** Composition v3 gives every released artifact one
`release_lock` and lets the runtime descriptor name its adapter lock plus
explicit `owner-dependency` or `shared-dependency` locks. The service verifies
the locked artifact bytes and installed distribution identity before loading
the descriptor. Vuoro released `vuoro-adapter-kit-v0.1.0` and
`vuoro-schema-runtime-v0.1.0` through GitHub Releases only, and promoted
kctl 0.1.2 as the first released adapter-kit consumer. This resolves the
lock/descriptor boundary; it does not make adapter or schema-runtime consumer
migration generic. Domain owners retain their handlers, operation schemas,
migration runners, database policy, and release authority.

The completed consumer promotions are deliberately narrower than a runtime
rollout. Kctl 0.1.2 and auditctl 0.1.1 promote their released adapter-kit
adapters through exact release locks. ActionQ 0.1.20 introduced the shared
builders but its wheel publication was not accepted as a dependency artifact;
the associated immutable image remains evidence only. The corrective ActionQ
0.1.21 release is the selected wheel. Vuoro composition promotion `f615404`
pins that release, selects `actionq-schema/v11`, and proves the four-domain
catalog at 84 operations. This leaves Sprintctl as the final P2.3 consumer.

### V-S4 — Activate ActionQ schema 11 only with separated completion authority

**Appservice-owned prerequisite; not a Vuoro service release.**

The ActionQ adapter composition is source-ready but schema 11 completion
operations must remain inactive until Appservice does all of the following:

1. runs ActionQ migration 011 using the migration identity;
2. provisions separate queue-runtime, completion-ingest, and completion-read
   roles, secrets, and DSNs;
3. grants completion ingest only append/projection rights and completion read
   only SELECT rights; and
4. proves neither completion identity can mutate queue actions/events, create
   schema objects, or write the migration ledger.

The composed service rejects missing, empty, runtime-equal, or
ingest/read-equal completion DSNs before constructing the ActionQ application;
explicit factories prevent a fallback to the queue-runtime connection. This is
a deployment and authority-separation blocker, not a reason to delay P2.3
consumer migration accounting. No `vuoro-service` 0.1.45 image has been
released, and this plan authorizes neither an image tag nor Appservice
activation.

## P2.3 — Adapter-kit consumer migration

The four owner adapters now consume the released shared `vuoro-adapter-kit`
through composition v3 locks. Kctl, Auditctl, and ActionQ are promoted in the
main composition; Sprintctl is represented by the pending 0.2.24 composition
change. Its source and wheel identity are immutable, and the release gate
proves the owner catalog and invocation boundary without a service release or
deployment. When this change is accepted, P2.3 is complete at 4/4 consumers;
ActionQ's separate schema-11 activation prerequisite remains tracked under V-S4.

## Cross-repository dependencies

- Agentops owns browser/MCP dispatch-contract projections and cockpit
  presentation.
- Sprintctl owns local/served mode retirement and owner-only Postgres
  administration.
- Actionq owns the canonical execution kernel and coordinator lifecycle.
- Appservice owns deployment configuration and production rollout. Vuoro
  release publication requires the release workflow and its immutable
  artifacts; this plan records its composition consequences but does not grant
  Vuoro authority to alter appservice state.
