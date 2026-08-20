---
doc_id: vuoro-architectural-simplification-alignment
status: ratified
ratified_at: 2026-07-28
ratified_by: operator
supersedes: []
---

# Vuoro architectural simplification alignment

> **Alignment update, 2026-08-20.** Completed release, migration, and canary
> evidence below remains historical fact. For future execution ownership,
> [Native-runtime and execution-federation alignment](2026-08-20-execution-federation-alignment.md)
> supersedes references to ActionQ as an execution kernel/coordinator and to
> the portable-runner plan. ActionQ's target is federation without a daemon,
> queue, claims, leases, runner, or fan-out engine; Sprintctl reservations are
> advisory; Outctl is outside the canonical Vuoro project.

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
  Release wheels, not PyPI packages. Kctl 0.1.3, auditctl 0.1.2, ActionQ
  0.1.22, and Sprintctl 0.2.24 are the four adapter-kit consumers. P2.2 is
  source-complete: the first three releases also reuse the one exact
  `vuoro-schema-runtime` 0.1.0 lock. Sprintctl remains adapter-kit-only.
- Sprintctl completed the final P2.3 promotion at source
  `75fd4a7bc01472f941c923444cabe6451bb1afd0`; Vuoro composition `c4e3357`
  reuses the shared adapter-kit lock rather than duplicating it. The
  released-work gate covers the exact 43-operation owner metadata hash,
  scoped invocation, project routing, resource registration/result decoding,
  and the exact four-domain catalog revision
  `fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.
- `vuoro-service` 0.1.45 is a released, attested artifact at
  `sha256:9ce139991c376855448134544b1d3cc7a5c6bdb1ea4f1aa5acff7509e9141ed3`.
  Appservice activated it only in `vuoro-dev` after a backup, drain, and
  schema-11 preflight. The dev structural canary reports schema 11, four
  compatible domains, and the 84-operation revision above. Positive
  completion canaries subsequently passed in both dev and shared through the
  real identity authority. Cleanup/revocation structural verification is also
  complete.
  Shared activation was subsequently explicitly approved and completed: after
  drain, backup, and exact quiescence preflight, schema 11 was applied without
  retry; the service and proxy both run the same attested 0.1.45 digest. The
  shared handshake now reports schema 11 and the 84-operation revision above.

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
catalog at 84 operations. Sprintctl 0.2.24 is pinned by `c4e3357`, closing
P2.3 at four of four consumers.

### V-S4 — Activate ActionQ schema 11 only with separated completion authority

**Appservice-owned prerequisite; not a Vuoro service release.**

The ActionQ adapter composition was source-ready. Appservice has now completed
the following checks in `vuoro-dev` only; they are evidence for the dev
structural canary, not authorization to alter `vuoro-shared`:

1. ran ActionQ migration 011 using the migration identity, after a completed
   backup and a drained/preflight-clean dev database;
2. provisioned separate queue-runtime, completion-ingest, and completion-read
   roles, secrets, and DSNs;
3. granted completion ingest only append/projection rights and completion read
   only SELECT rights; and
4. proved neither completion identity can mutate queue actions/events, create
   schema objects, or write the migration ledger.

The composed service rejects missing, empty, runtime-equal, or
ingest/read-equal completion DSNs before constructing the ActionQ application;
explicit factories prevent a fallback to the queue-runtime connection. The
dev service runs the attested 0.1.45 image, reports schema 11, and reports the
84-operation catalog revision
`fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.
Positive completion-operation canaries have passed in dev and shared through
the provisioned non-self-asserted identity authority. Cleanup/revocation is
structurally complete; do not weaken the authority boundary in future
exercises. Shared activation completed under explicit operator approval after
drain, backup, and zero-active-work preflight: migration 011 applied schema 11
with no retry, preserved the seven retained actions and 1,192 retained events,
and left no forbidden completion privileges. Both shared containers run the
same attested 0.1.45 digest and the shared handshake reports schema 11 and the
exact 84-operation revision. V-S4 runtime activation and its functional canary
are complete.

P2.2 remains separate from the completed schema-11 activation. Kctl 0.1.3,
auditctl 0.1.2, and ActionQ 0.1.22 are accepted immutable schema-runtime
consumers. The composition gate proves their exact released metadata, the
single shared runtime lock, unchanged domain schema descriptors, and the
unchanged 84-operation catalog revision. A subsequent Vuoro service release
and rollout remain separate actions; no migration is required by this source
promotion. `vuoro-service-v0.1.46` is now published from source
`6bc23212edd611965f067781fb6c6af090ac1ed5`, with wheel SHA-256
`978ef5a764932957636f9e6915e92f75ec773967f15a16dc5f9834a5ed71e938` and
attested OCI digest
`sha256:aeeb8088b8485c9637526b63d8557a68db618772979330ed5950f0e09c4a0f5c`.
Provenance verification passed. Appservice deployed the same digest via dev
Flux revision `648be1` (canary PASS) and shared revision `352b32` (deployment
PASS). Both handshakes report schema 11 and the unchanged 84-operation
revision; retained counts are dev queue/completion `0/1` and shared
queue/events/completion `7/1192/1`.

## P2.2 — Schema-runtime consumer migration

**Completed 2026-08-13 — 3/3 consumers.** Kctl 0.1.3, Auditctl 0.1.2, and
ActionQ 0.1.22 declare the exact digest-pinned `vuoro-schema-runtime` 0.1.0
wheel in their published metadata. Composition v3 records that wheel once and
references it from knowledge, audit, and execution alongside the existing
adapter-kit and ActionQ contracts locks. The released-wheel gates install and
attest all locks, run `pip check`, and preserve `knowledge-schema/v1`,
`audit-schema/v1`, and `actionq-schema/v11`. The complete catalog remains 84
operations at revision
`fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.

The accepted adapter artifacts are kctl 0.1.3
(`789b5aadfc4c31171d574c76b79af9999b08b5cf212969cefc8504eb2e99e43d`),
auditctl 0.1.2
(`b76d9d7aab727c77a7dcfcdc4e5de423b61a07c8f89369101347a4dc6eaf33d1`),
and ActionQ 0.1.22
(`5ffce20b2e9b53305a522b25f8504081442311392b025ea220fc8792e8e50bd2`).
Each reuses the one `vuoro-schema-runtime` 0.1.0 wheel
(`b66c9357c99aa9e1a7353991ce54105a8621958ecfac47f8c121d80b90b77912`).

## P2.3 — Adapter-kit consumer migration

**Completed 2026-08-13 — 4/4 consumers.** The four owner adapters now consume
the released shared `vuoro-adapter-kit` through composition v3 locks: kctl
0.1.2, auditctl 0.1.1, ActionQ 0.1.21, and Sprintctl 0.2.24. Sprintctl's
source and immutable release wheel were accepted by the released-work gate,
then pinned in composition `c4e3357`. The assembled service catalog contains
exactly 84 operations at revision
`fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.
P2.3 completion did not itself authorize a service rollout; the separately
approved V-S4 activation and positive canaries are now complete. P2.2 is also
source-complete, while its service release and deployment remain independently
controlled.

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
