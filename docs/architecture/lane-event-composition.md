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

## Python floor and image packaging

Lanectl's first owner release (`0.2.0`) requires Python 3.13 or later. The default
three-owner service image retains its existing `python:3.12-slim` base. Merely
adding a lane descriptor to a manifest does not make that default image
compatible: fetch/install, attestation and runtime composition refuse a lane
manifest on Python 3.12.

After releasing the owner's wheel and preparing a reviewed manifest containing
its exact source revision, URL and SHA-256, place that manifest inside the build
context, for example at `composition/lane-adapter-pins.json`, then build:

```sh
docker build \
  --build-arg PYTHON_BASE_IMAGE=python:3.13-slim \
  --build-arg VUORO_COMPOSITION_FILE=composition/lane-adapter-pins.json \
  --tag vuoro-service:lane-candidate .
```

Both arguments are explicit image-build inputs; neither changes a running
service's catalog. The image fetches and hash-verifies the selected manifest's
wheels, installs every and only pinned owner/shared wheel without dependency or
index resolution, runs `pip check`, and records installed-composition
attestation. The same generic installer covers the unchanged default closure
and an additional lane owner; there is no hardcoded wheel list that omits the
optional package. The alternate Python image and new manifest require the
normal image qualification before promotion. This source change has not built,
published or deployed a qualified lane image.
