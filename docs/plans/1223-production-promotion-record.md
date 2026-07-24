---
doc_id: 1223-production-promotion-record
status: final
supersedes: null
---

# #1223 production promotion record

Owner: vuoro deployment. Records the promotion evidence sprintctl #1164 gate
row 10 requires: the served substrate serving sprintctl work authority is
live in production with real, parity-correct data.

## Deployment identity

- Namespace: `vuoro-shared` (appservice cluster).
- Image digest: `ghcr.io/bayleafwalker/vuoro-service@sha256:e5a767c85dbd04146e594db5daf9d33f72264f777f6853ebd2c59642826508ab`.
- Deployment: `vuoro-shared`, generation 2, observedGeneration 2, `1/1 Running`,
  no restarts (verified live 2026-07-24, 37h+ uptime at time of check).
- Config: ConfigMap `vuoro-shared-config` — `VUORO_ENVIRONMENT_NAME=vuoro-shared`,
  `VUORO_ENVIRONMENT_CLASS=production`, `VUORO_WORK_REPOSITORY_ID=sprintctl`.
  Scope is deliberately the `sprintctl` repository tenant only (matching
  #1195/#1163's non-fleet-cutover scope), not all 12 repo tenants sharing
  `sprintctl-cnpg-main`.

## Migration state

All four domains migrated and schema-compatible, verified via
`vuoro-migrate-v3-dj9p4` job logs (all init containers exit 0):

| Domain | Applied versions | Compatible |
| --- | --- | --- |
| work | 2, 3 | yes (matches `sprintctl-cnpg-main` schema version 3) |
| execution | 1 | yes |
| knowledge | 1, 2, 3 | yes |
| audit | 1, 2 | yes |

## Historical data backfill (this record's main event)

Schema migration alone left `work.sprint`/`work.work_item`/`work.event`/etc.
at zero rows — the served catalog handshake succeeded but every read
returned empty. No prior mechanism populated production with sprintctl's
existing history: `sprintctl authority sync` / `work.batch.apply` only
flushes a repo's local outbox (writes made *after* switching to served
mode), and `repository_ingest_cursor` is only a capability flag in the
schema-compatibility handshake, not a running sync. `sprintctl db
recover-from-remote` (#1233) is the opposite direction (served -> local
SQLite), not usable to seed a served authority.

Resolution: a direct one-time data copy, since `vuoro-shared`'s `work`
schema and `sprintctl-cnpg-main`'s `public` schema are the same sprintctl
Postgres schema (byte-identical table/column definitions, both
`schema_version=3`) deployed in two places, not two different data models.

Procedure (workstation, 2026-07-24, no cluster manifest or NetworkPolicy
changes — `kubectl exec` into each CNPG pod directly, no port-forward tunnel
in the final run because port-forward to `sprintctl-cnpg-main-rw` was
observed to reset after one query):

1. Dumped `public.<table>` rows WHERE `repo_id='sprintctl'` from
   `sprintctl-cnpg-main-1` via `psql \copy ... to stdout`, for `sprint`,
   `track`, `work_item`, `claim`, `dep`, `ref`, `event` (in that FK-safe
   order; `authority_decision` was empty, skipped).
2. Loaded into `vuoro-postgres-1`'s `work.<table>` via a single transaction
   (`BEGIN` / `SET session_replication_role = replica` to bypass FK/trigger
   ordering concerns during load / `COPY ... FROM STDIN` per table / `SET
   session_replication_role = DEFAULT` / sequence `setval` to `MAX(id)` per
   table / `COMMIT`).
3. Verified row-count parity, source vs. destination, both filtered to
   `repo_id='sprintctl'`:

   | Table | Source | Destination |
   | --- | --- | --- |
   | sprint | 21 | 21 |
   | track | 51 | 51 |
   | work_item | 195 | 195 |
   | claim | 3 | 3 |
   | dep | 57 | 57 |
   | ref | 141 | 141 |
   | event | 469 | 469 |

## Post-promotion health/parity check

Live served-mode client check from the workstation (`SPRINTCTL_BACKEND=served`,
`SPRINTCTL_VUORO_PROFILE=workstation-vuoro-shared.json`, marker flipped to
`served` and reverted immediately after):

- `sprintctl doctor`: `ok`, handshake `resolved=served`, full catalog
  returned (`work.read.sprints`, `work.read.item`, `work.batch.apply`,
  `work.claim.start`, etc. all present).
- `sprintctl sprint list`: returns the real sprint history (e.g. `#414`
  "Ops Upgrade Wave 1", `#406` "Phase 28: Shadow Projection Dogfood Pilot"),
  matching direct-authority reads.
- `sprintctl item show --id 1164`: returns the exact same content
  (description, refs, blockers) as the direct-authority read used
  throughout this session.

Rollback: `work` schema had zero consumers before this backfill; a bad
backfill could have been recovered with `TRUNCATE work.sprint, work.track,
work.work_item, work.claim, work.dep, work.ref, work.event,
work.authority_decision CASCADE` and a re-run. Not needed — parity held on
the first pass.

## Gate status

Row 10 ("Production promotion evidence") of sprintctl's
`docs/plans/1164-gate-evidence-ledger.md` is satisfied by this record.
