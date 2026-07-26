# Adapter promotion contract

This document governs a source-only update to
`packages/vuoro-service/composition/adapter-pins.json`. It does not authorize
an image release, deployment change, schema migration, data backfill, or
runtime validation.

## Immutable pin update

An adapter update is one reviewable manifest change. For every changed
adapter, the reviewer must establish all of the following before editing the
pin:

1. The owner repository has committed the adapter implementation and its
   contract/integration tests. Record the full source commit SHA in
   `source_revision`.
2. The owner has published an immutable wheel release. The wheel URL must be
   a GitHub release asset, its SHA-256 must match the downloaded bytes, and
   its installed distribution version must equal `distribution_version`.
   Reusing a version for a different wheel is not a valid promotion.
3. The adapter's declared API and schema versions are compatible with the
   values in the pin. A version change requires the corresponding Vuoro
   compatibility review; matching Python package versions are not sufficient.
4. The source commit, release asset, checksum, distribution version, adapter
   module, and registration entrypoint describe the *same* owner release.

Update all related fields in the one adapter object together. Do not install
from a local checkout, mutate a downloaded wheel, or substitute a deployment
overlay for the manifest. The container build fetches exactly these release
assets, and service startup verifies their checksums before importing an
adapter.

## Sprintctl blind-agent promotion gate

The currently pinned Sprintctl adapter (`45ec765`, `0.2.0`) only supports the
deployed happy path. It must not be re-pinned merely because a source commit
contains served CLI guards. A candidate work adapter is ready for a Vuoro pin
only after its own checked-in tests prove catalog registration and invocation
for the complete blind-agent surface:

- scoped usage/context and work-item list reads;
- item-reference and dependency add/list/remove;
- claim list, list-by-sprint, show, and resume; and
- tracker handoff plus the finish/release route used by the documented
  workflow.

The Sprintctl change must also prove repository-qualified `repo#id` parsing
where a user can select a repository, sprint, or item ambiguously. Commands
that remain intentionally unavailable must fail before a direct store is
constructed with the stable served-unavailable error class; this is not a
catalog substitute.

The expected evidence is owner-side: focused adapter/application tests,
served CLI tests, and a catalog assertion that names every operation consumed
by those CLI routes. Vuoro must then run a composition-level compatibility
test against the released wheel (not an editable checkout) before its pin is
merged. A catalog operation's presence alone is insufficient: the test must
include accepted and rejected invocations appropriate to its authority and
idempotency contract.

## Source acceptance commands

Run these from the Vuoro repository after a pin update, in this order:

```bash
uv sync --all-packages --all-extras
uv run --package vuoro-client --extra test pytest packages/vuoro-client/tests
uv run --package vuoro-service --extra test pytest packages/vuoro-service/tests
uv build --package vuoro-client --wheel --out-dir dist/vuoro-client
uv build --package vuoro-service --wheel --out-dir dist/vuoro-service
uv run pytest
python /projects/dev/agentops/templates/dispatch/scripts/validate_verification_artifacts.py --root .
```

For a changed work adapter, also run the owner repository's focused catalog,
application, served-route, and served-CLI tests recorded by that release.
The owner release evidence must identify the exact commands and revision; do
not treat Vuoro's manifest-shape test as evidence that an adapter implements
new operations.

## Release and operator boundary

Only after the source acceptance commands and released-wheel composition test
pass may a Vuoro maintainer create a `vuoro-service-v*` tag. That tag invokes
the image-publishing workflow and builds with the immutable checked-in pins.

Pinning or tagging does **not** authorize appservice work. An operator must
independently review and perform any image-digest update, identities or
authorization change, migration, backfill, rollout, four-domain black-box
validation, and the two-agent rehearsal. Source contributors must stop at the
published image artifact and hand those actions back to the operator.
