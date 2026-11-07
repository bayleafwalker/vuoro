# Tenant runtime to work schema mapping

Status: **proposed** (2026-11-07). No implementation, release pin or
deployment changes with this document. It states a contract that
`vuoro-cloud` needs and this repository must own.

## Why it is needed

`vuoro-cloud` verifies a tenant database after a migration and before a tenant
is declared ready (migration plan phases P5 and P6 in the agentops repository,
`docs/plans/2026-11-06-vuoro-cloud-migration-plan.md`). To do that it must know
which work schema version a given `vuoro-service` build expects. Today that
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
| Form | A machine-readable artifact published with each service release (a JSON file or a field in the release contract), plus the same data in `CHANGELOG.md` wording that already exists. The delivery form is open; the content above is not. |

Not in scope: how `vuoro-cloud` acts on the answer, tenant migration execution,
or any change to the public control-plane schema.

## Seed data (from `CHANGELOG.md`, to be checked by the first implementation)

| `vuoro-service` | Work schema | Explicit migration from previous |
| --- | --- | --- |
| 0.1.99 | 28 (unchanged) | no |
| 0.1.102 (candidate) | 30 | yes |
| 0.1.104 (source candidate) | 31 (Sprintctl 0.28.0; source: `packages/vuoro-service/README.md`) | yes |

0.1.104 is not in `CHANGELOG.md` yet; the first implementation should reconcile the two sources.

## Acceptance criteria (cheap checks)

Each criterion is a check of finished work, not an entry gate.

1. A test loads the published mapping and asserts one entry for every
   `vuoro-service` version listed in `CHANGELOG.md` from the first version that
   pins a work owner release onward, and that no entry is missing the schema
   integer.
2. A test derives the schema integer for the current pinned work owner release
   (`adapter-pins.json`) and asserts it equals the mapping's entry for the
   current service version.
3. A test asserts an unknown version returns `unknown`, and that `unknown` is
   distinct from any integer.
4. A test asserts `migration_required` is true exactly when the schema integer
   differs from the previous entry's.
5. A `vuoro-cloud` check (its own repository) compares `config/compatibility.json`
   `tenant_schema` and the pinned `vuoro-service` version against the mapping and
   fails on a mismatch. The current skew (cloud pins 0.1.94, `vuoro` is at
   0.1.104) is the first thing it reports.

## Open points

- Delivery form: a file attached to the release, a field in the release
  contract, or a read-only operation on the service. Choose the one `vuoro-cloud`
  can read without running a tenant.
- Whether the mapping lists schemas a release can serve in addition to the one it
  requires (the 0.1.x line has so far required one).
