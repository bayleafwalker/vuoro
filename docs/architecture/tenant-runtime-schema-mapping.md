# Tenant runtime to work schema mapping

Status: **implemented** (2026-11-08). This repository publishes the versioned
mapping as service package data at
`packages/vuoro-service/src/vuoro_service/tenant-runtime-schema-map.json`,
validated by the adjacent JSON Schema and exposed through
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
through 0.1.105. `migration_required_from_previous` compares the target schema
with the immediately previous released service version, including releases
whose owner pin did not change. Pin and owner constant rows identify the adapter
pin history and Sprintctl `pg_migrations` constants. Rows supported only by
`changelog_prose` are not represented as wheel-constant verification. For
0.1.96 the local pin history establishes Sprintctl 0.20.0 but neither a local
wheel nor changelog establishes its schema triple; those fields are null and
its migration flag is unknown rather than inferred.

The 0.1.105 owner declares schema 34 current and accepts 32–34; migration from
0.1.104's schema 31 is explicit. The map does not claim separate qualification
of historical schema 32/33 catalog shapes. This is source/package contract
information, not deployment or field acceptance.

A future service release without a mapping row is blocked by
`scripts/validate_release_contract.py`. The release workflow invokes that
validator, so the missing row blocks the whole publish workflow by design.

## Acceptance criteria (cheap checks)

The repo-local criteria are implemented and covered by
`packages/vuoro-service/tests/test_tenant_schema.py` and
`scripts/validate_release_contract.py`.

The tests validate JSON Schema conformance, all released rows and adjacent-release
migration flags, current owner constants, available pin history, and both
lookup forms plus unknown/malformed input errors. The release-contract script
validates the packaged map against its schema, requires the current service row,
and rejects malformed rows with `ValueError`.

The consumer integration remains in `vuoro-cloud`: its check must compare
`config/compatibility.json` `tenant_schema` and the pinned `vuoro-service`
version against this mapping and fail on a mismatch. Direct release asset
consumption also requires the service release workflow to attach the map and
schema (or publish an equivalent release contract asset); no workflow was
changed here.
