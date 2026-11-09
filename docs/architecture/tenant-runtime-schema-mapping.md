# Tenant runtime to work schema mapping

Status: **implemented** (2026-11-09). The mapping ships as service package data at
`packages/vuoro-service/src/vuoro_service/tenant-runtime-schema-map.json`,
with the adjacent JSON Schema and the standalone map and schema assets attached
to each `vuoro-service` GitHub release. During extraction, `ZipFile.read`
checks each wheel member's CRC; the workflow then compares the written file's
bytes with a fresh read of that member and verifies its SHA-256 against the
member's SHA-256 entry in the wheel's `RECORD`. Their file SHA-256 digests are
listed in release notes, and the assets are attested. The mapping is exposed through
`vuoro_service.tenant_schema.lookup_tenant_schema`. It changes no release pin or
deployment. It states a contract that
`vuoro-cloud` needs and this repository must own.

## Why it is needed

`vuoro-cloud` verifies a tenant database after a migration and before a tenant
is declared ready (migration plan phases P5 and P6 in the agentops repository,
`docs/plans/2026-11-06-vuoro-cloud-migration-plan.md`). To do that it must know
which work schema range a given `vuoro-service` build supports and the current
schema its migration targets. Today that
knowledge exists only as prose in `CHANGELOG.md` (for example, 0.1.99 keeps
schema 28; the 0.1.102 candidate requires an explicit migration to schema 30),
and `vuoro-cloud` keeps its own `tenant_schema` range in
`config/compatibility.json`. The two can drift, and the cloud side then guesses.

The service's own startup compatibility check (`AGENTS.md`, runtime invariants)
already rejects a database at the wrong schema. This contract makes the same
fact available to a caller before the service runs.

## Contract

| Part | Statement |
| --- | --- |
| Input | A tenant runtime version: a `vuoro-service` release version such as `0.1.104`, or the pinned form `vuoro-service-v<version>@sha256:<digest>`. |
| Output | The work schema target, accepted range and whether reaching the target from the immediately previous released service version needs an explicit migration (`migration_required_from_previous`: boolean). Unknown well-formed versions raise `UnknownServiceVersionError`. |
| Owner | This repository (`vuoro`). The value derives from the pinned work owner release in `packages/vuoro-service/composition/adapter-pins.json`, not from a hand-edited second source. |
| Consumers | `vuoro-cloud` P5 (tenant schema verification after `MIGRATING`) and P6 (restore and retire-dump classification), and the composition qualification that checks a candidate pin. |
| Form | A machine-readable JSON artifact packaged with the service distribution and a checked-in JSON Schema. The pinned identity digest is shape-checked only; it is not bound to an image or wheel SHA-256. |

Not in scope: how `vuoro-cloud` acts on the answer, tenant migration execution,
or any change to the public control-plane schema.

## Released mapping

The map contains one row for every released `vuoro-service` version 0.1.94
through 0.1.105. The floor is 0.1.94: 0.1.93 and earlier are intentionally
unmapped, so `lookup_tenant_schema` raises `UnknownServiceVersionError` for
them. `migration_required_from_previous` compares the target schema with the
immediately previous released service version, including releases whose owner
pin did not change. Each evidence reference identifies the `adapter-pins.json`
file at that service tag commit and the Sprintctl `pg_migrations` constants.
The full verified target/floor/ceiling triples are recorded in the history test
so they remain checked when historical owner wheels are unavailable.

The 0.1.96 CHANGELOG entry still names Sprintctl 0.19.0; that text is stale
relative to the 0.1.96 tag, whose adapter pin selects Sprintctl 0.20.0. The
0.1.95 CHANGELOG phrase “work schemas 23 to 25” describes migrations added;
Sprintctl 0.19.0 accepts schema 25 only.

The published 0.1.105 wheel predates the mapping and does not contain it. The
next `vuoro-service` release is the first release carrying the map and schema
both inside its wheel and as standalone release assets. The 0.1.105 owner
declares schema 34 current and accepts 32–34; migration from
0.1.104's schema 31 is explicit. The map does not claim separate qualification
of historical schema 32/33 catalog shapes. This is source/package contract
information, not deployment or field acceptance.

A future service release without a mapping row is blocked by
`scripts/validate_release_contract.py`. The release workflow invokes that
validator, so the missing row blocks the whole publish workflow by design.

## Acceptance criteria (cheap checks)

The repo-local criteria are implemented and covered by
`packages/vuoro-service/tests/test_tenant_schema.py` and
`scripts/validate_release_contract.py`. The service release workflow attaches
both wheel-derived JSON files, records their SHA-256 digests in release notes,
and attests each asset.

The tests validate JSON Schema conformance, cross-field range invariants, unique
and ordered service versions, all released triples and adjacent-release migration
flags, current owner constants, recorded tag pin history, and both lookup forms
plus unknown/malformed input errors. The release-contract script
validates the packaged map against its schema, requires the current service row,
and rejects malformed rows with `ValueError`.

The consumer integration remains in `vuoro-cloud`: its check must compare
`config/compatibility.json` `tenant_schema` and the pinned `vuoro-service`
version against this mapping and fail on a mismatch. Direct release asset
consumption also requires the service release workflow to attach the map and
schema (or publish an equivalent release contract asset); no workflow was
changed here.
