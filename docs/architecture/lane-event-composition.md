# Optional lanectl event-owner composition

Vuoro Service may compose an immutable release pin for lanectl's `lane` domain in
addition to its required `work`, `audit` and `knowledge` domains. Existing
three-owner manifests remain unchanged. Runtime environment configuration cannot
add operations; a source-supported adapter becomes available only when its wheel
and full source/artifact identities appear in the release composition manifest.

The lane descriptor selects distribution `lanectl`, module
`lanectl.vuoro_adapter`, registration `register_lane_catalog`, API `v1`, schema
`1`. Set exactly one of `VUORO_LANE_DATABASE` (SQLite path) or
`VUORO_LANE_POSTGRES_DSN` (PostgreSQL connection with a compatible pinned owner).
Startup constructs the owner's already-migrated store and refuses unavailable or
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
context. The reviewed optional manifest is
`packages/vuoro-service/composition/lane-adapter-pins.json` (lanectl0.2.12); build:

```sh
docker build \
  --build-arg PYTHON_BASE_IMAGE=python:3.13-slim \
  --build-arg VUORO_COMPOSITION_FILE=packages/vuoro-service/composition/lane-adapter-pins.json \
  --tag vuoro-service:lane-candidate .
```

Both arguments are explicit image-build inputs; neither changes a running
service's catalog. The image fetches and hash-verifies the selected manifest's
wheels, installs every and only pinned owner/shared wheel without dependency or
index resolution, runs `pip check`, and records installed-composition
attestation. The same generic installer covers the unchanged default closure
and an additional lane owner; there is no hardcoded wheel list that omits the
optional package. The alternate Python image and new manifest require the
normal image qualification before promotion. No lane image has been published
or deployed by this source change.

The [14November local image proof](../evidence/2026-11-14-lane-image/README.md)
records the exact-source optional build and its bounded controls. It is separate
from a deployed or persistent hosted composition.

## Separate optional-image publication

`publish-lane-service-image.yaml` builds the reviewed lane manifest explicitly on
`python:3.13-slim` for `linux/amd64`. The existing default image publisher and its
tags remain unchanged. Service tags and manual dispatch first publish only
`lane-candidate-sha-<commit>`, with build provenance and an SBOM. Candidate
publication is not a qualification result.
Manual dispatch selects `main` or a `vuoro-service-v*` tag. Other refs are
rejected before a registry effect, including names colliding with reserved
source and candidate aliases.

After GitHub attestation, `scripts/publish_lane_image.py` verifies the exact
registry output digest and its source/workflow identity. It pulls and runs that
digest, checks the selected manifest SHA-256, all installed pins and recomputed
wheel/file attestation, and runs the existing provenance, dependency, preview and
MCP entrypoint checks. Index inspection requires one linux/amd64 runtime and
attached BuildKit provenance and SPDX SBOM manifests bound to that runtime.
Only success permits `lane-<ref>` and `lane-sha-<commit>`
aliases. Each alias is reread and must retain the same top-level index digest,
including its attached attestations. PR CI also builds and checks the optional
closure without publishing.

The publication receipt records the source, qualified digest, platform,
manifest, checks and each alias result. Alias updates are separate registry
effects: a failure can leave a candidate or one changed alias. An attempted
update with no confirmed response remains uncertain in the receipt; it is not
reported as absent or rolled back. Runs for the same commit are serialized.
GitHub may replace an older pending run with a newer one; a cancelled pending
run has no publication receipt and establishes no qualification.
The Python base tag remains mutable, so this is exact-artifact qualification,
not a claim of reproducible builds. Build provenance records the base material.
The fresh installed-composition check executes the shipped attester and compares
its records with the reviewed manifest and actual installed versions; it is a
drift check, not an independent audit of a hostile interpreter. The existing
provenance checker additionally rejects workspace packages resolved from an index.

These public artifacts provide no hosted storage, tenant migration, gateway
forwarding, deployment or authority cutover. Those retain their owner contracts
and separate qualification requirements.
