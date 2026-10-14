# Agent directory and inbox composition

Service 0.1.95 consumes the released Sprintctl 0.19.0 wheel, source
`ad66ddd030dac7134e212df328f2cb5b82639bdc`, SHA-256
`6d819cf607ccbeb65d5c6d56705388b2b005fa82bdaa30d7bd975573026b8f26`.
The owner release pins adapter-kit 0.2.2, so the composition's shared
adapter-kit lock moves from 0.2.0 to the released 0.2.2 wheel, SHA-256
`6d78c2f05a8e3a72c480b0e333232bc90ccee1a36b71fba09f5bc8fc8e5377fa`.
Auditctl 0.1.9 and schema-runtime 0.1.0 keep their pins. The installed
closure passes `pip check`.

The owner release carries work schemas 23, 24 and 25:

- 23 adds immutable run/invocation owner correlations and exact attempt
  receipts.
- 24 adds the read-only proposal-associated verification lookup index.
- 25 adds the agent directory and durable inbox from the
  [agent directory and inbox](agent-directory-and-inbox.md) contract.

All 80 previous work operation descriptors are unchanged and 17 are added,
including the ten `work.agent.*` and `work.message.*` operations. With
resources disabled, the composed catalog has 97 work operations and 5 audit
operations: 102 in total, revision
`fd49d79de14a0ec630dde511928cc76e3aceb2e159266f883dcbe6ab14853d2b`.

## Serving schema-gated owner operations

The owner refuses schema-gated operations, such as run invocation receipts
(23) and the inbox (25), unless its runtime store has recorded the schema
version it observed. Composition now records the version from its existing
read-only work handshake once that handshake is compatible. This matches the
owner's own runtime startup. It runs no DDL and happens after catalog
registration, so the catalog still derives only from the released artifacts.
Project member stores inherit the recorded version.

## Conformance

The provider-neutral inbox suite (`tests/conformance/test_inbox_contract.py`)
runs each of its 31 scenarios twice: once against the reference provider and
once against the released Sprintctl owner. The owner binding uses a loopback
disposable `inbox_conformance*` database, which it comments as disposable so
the owner will honour its test clock. CI sets `VUORO_INBOX_CONFORMANCE_PROOF`,
which turns an unavailable binding into a failure instead of a skip. CI also
asserts that all 31 owner-bound tests ran and passed.

## Explicit schema transition

Startup checks compatibility and performs no DDL. The live vuoro-shared
closure, service 0.1.93 with Sprintctl 0.18.0, serves schema 22. Service 0.1.95
refuses that schema. The CI gate
`test_schema25_composition_migration.py` starts from the actual hash-pinned
0.1.93 service and owner wheels. It writes schema-22 intent, evidence and
redeemed attempt facts, plus audit rows, and confirms that the old factory is
ready and the new factory refuses. It then migrates only through the released
owner's own `sprintctl remote-schema migrate` command, using the migration
role. That applies 23, 24 and 25 exactly once; a second run applies nothing.
After grants, the runtime-role `remote-schema check` turns compatible and the
new factory serves the 102-operation catalog.

The gate checks that:

- the preserved schema-22 attempt is still readable;
- an agent can register and be listed through the composed runtime;
- every pre-existing row is preserved;
- both runtime roles lack DDL;
- the old service and the old owner both refuse schema 25.

The existing schema-21 closure gate now migrates to schema 25.

A deployment follows the transition procedure in
[effect attempt composition](effect-attempt-composition.md):

1. Quiesce old writers.
2. Back up and capture the current schema, image, catalog and identity
   revision.
3. Migrate explicitly with the owner's migration role.
4. Verify the preserved rows.
5. Deploy the matching immutable image.
6. Qualify runtime DML, DDL refusal and served continuity.

After schema 25 is installed, recovery uses a compatible forward image.

## Store authority

Per [the M0 baseline](../plans/2026-10-14-agent-coordination-m0-baseline.md),
Sprintctl is the store owner and vuoro-shared serves these record families.
Until Q1(b), vuoro-shared is the single authoritative store for the agent
directory and inbox, and nothing is dual-written. This composition makes the
operations servable. It performs no rollout, migration of live state or task
closure; those remain deployment-owner operations.
