# Optional lanectl event-owner composition

Vuoro Service may compose an immutable release pin for lanectl's `lane` domain in
addition to its required `work`, `audit` and `knowledge` domains. Existing
three-owner manifests remain unchanged. Runtime environment configuration cannot
add operations; a source-supported adapter becomes available only when its wheel
and full source/artifact identities appear in the release composition manifest.

The lane descriptor selects distribution `lanectl`, module
`lanectl.vuoro_adapter`, registration `register_lane_catalog`, API `v1`, schema
`1`. `VUORO_LANE_DATABASE` supplies only the runtime database location. Startup
constructs the owner's already-migrated store and refuses unavailable or
incompatible storage. Migrations, lane event validation, stream partitions,
optimistic concurrency and idempotency remain in lanectl's owner package; Vuoro
neither schedules lanes nor chooses models nor settles work on their behalf.

The owner registers repository-scoped `lane.stream.read-v1` (`work:read`) and
`lane.stream.append-v1` (`work:evidence`). The reusable shell enforces identity,
environment, operation authority and repository access. The owner additionally
requires verified workspace and principal context, partitions the stream by
workspace/repository/name, and binds batch replay to the principal. The cloud
gateway's runner allowlist must separately admit those exact operations before a
runner may forward them. No delete or migration operation is exposed.

A deployment must provide the owner's qualified persistent volume and backup
policy, run its explicit migration before runtime startup, and verify imported
history before selecting served authority on lane hosts. Lanectl's owner runbook
contains the hash-verified import/cutover and SQLite backup/restore requirements.
This source support changes no checked-in production pin and is not rollout or
durability evidence.
