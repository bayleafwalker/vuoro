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
| Output | The work schema version that release expects (an integer, for example `28` or `30`), whether reaching it from the previous release needs an explicit migration (`migration_required`: boolean), and the lowest schema version the release still serves, if it serves more than one. For an unknown version the result is `unknown`, never a guess. |
| Owner | This repository (`vuoro`). The value derives from the pinned work owner release in `packages/vuoro-service/composition/adapter-pins.json`, not from a hand-edited second source. |
| Consumers | `vuoro-cloud` P5 (tenant schema verification after `MIGRATING`) and P6 (restore and retire-dump classification), and the composition qualification that checks a candidate pin. |
| Form | A machine-readable JSON artifact packaged with the service distribution and a checked-in JSON Schema. The `vuoro-service` GitHub release workflow currently publishes only the wheel; it must attach the mapping and schema from that wheel (or publish them as a release contract asset) for `vuoro-cloud` to download them directly. |

Not in scope: how `vuoro-cloud` acts on the answer, tenant migration execution,
or any change to the public control-plane schema.

## Current mapping

| `vuoro-service` | Current schema target | Lowest supported | Explicit migration from previous |
| --- | ---: | ---: | --- |
| 0.1.99 | 28 | 28 | no |
| 0.1.102 (candidate) | 30 | 30 | yes |
| 0.1.104 (source candidate) | 31 (Sprintctl 0.28.0) | 31 | yes |
| 0.1.105 (release-preparation candidate) | 34 (Sprintctl 0.28.1) | 32 | yes |

The 0.1.105 source candidate requires explicit owner migration to schema 34;
the published owner refuses schema 31 and its compatibility check accepts
version numbers 32–34. Historical schema32/33 catalog shapes are not claimed
as separately qualified by this candidate. Rows 0.1.99 and 0.1.102 are
evidenced by changelog prose; 0.1.104 is evidenced by the schema-31
architecture source contract. The 0.1.105 row is checked against its adapter
pin and the pinned owner's compatibility constants. This row is
source qualification only, not an enablement or deployment claim.

## Acceptance criteria (cheap checks)

The repo-local criteria are implemented and covered by
`packages/vuoro-service/tests/test_tenant_schema.py` and
`scripts/validate_release_contract.py`.

The tests validate JSON Schema conformance, seed row provenance and migration
flags, exact current owner pin and compatibility constants, and both accepted
lookup forms plus unknown/malformed input errors. The release-contract script
requires the mapping and schema in the service wheel and checks for an entry
matching that wheel's service version.

The consumer integration remains in `vuoro-cloud`: its check must compare
`config/compatibility.json` `tenant_schema` and the pinned `vuoro-service`
version against this mapping and fail on a mismatch. Direct release asset
consumption also requires the service release workflow to attach the map and
schema (or publish an equivalent release contract asset); no workflow was
changed here.
