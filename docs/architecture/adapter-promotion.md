# Adapter promotion contract

This document governs a source-only update to
`packages/vuoro-service/composition/adapter-pins.json`. It does not authorize
an image release, deployment change, schema migration, data backfill, or
runtime validation.

## Release-lock and runtime-descriptor update

Composition v2 deliberately separates immutable release identity from
serve-time registration. `release_locks` contain only `lock_id`, owner
repository/revision, wheel URL/digest, and distribution/version. Runtime
descriptors bind one domain to a `lock_id`, any dependency lock IDs, and its
adapter module, registration entrypoint, API, and schema versions. Descriptors
contain no migration entrypoint: Vuoro never migrates an owner domain at
startup.

For every changed release lock, the reviewer must establish all of the
following before editing it:

1. The owner repository has committed the adapter implementation and its
   contract/integration tests. Record the full source commit SHA in
   `source_revision`.
2. The owner has published an immutable wheel release. The wheel URL must be
   a GitHub release asset, its SHA-256 must match the downloaded bytes, and
   its installed distribution version must equal `distribution_version`.
   Reusing a version for a different wheel is not a valid promotion.
3. The matching runtime descriptor's declared API and schema versions are
   compatible with the owner release. A version change requires the corresponding Vuoro
   compatibility review; matching Python package versions are not sufficient.
4. The descriptor lock reference, source commit, release asset, checksum, and
   distribution version describe the *same* owner release.

A descriptor may declare strict `dependency_lock_ids` when its public runtime
contract is a separate distribution. Each companion is its own release lock
and must share the adapter lock's owner repository, but it records the source
revision of *its own* published wheel. A separately released compatible
companion can therefore remain pinned when a new adapter wheel declares the
same exact companion version. Duplicate distributions, colliding filenames,
orphan locks, and shared adapter locks are refused.

Owner releases may use either a source-SHA tag or an exact semantic-version
tag. For a semantic-version tag, evidence must show that the tag resolves to
`source_revision`; the release URL, full revision, digest, and installed
version remain independently checked. ActionQ `v0.1.18` resolves to
`cbc426ff938053d2e695f3caf5a4871e701b4661` and ships `actionq` 0.1.18,
whose published metadata requires `actionq-contracts==0.1.1`. The separately
released companion remains locked to its own immutable release revision.

Update a lock and its descriptor together. Do not install from a local
checkout, mutate a downloaded wheel, or substitute a deployment overlay for
the manifest. The container build fetches exactly these release assets and
records the installed wheel files; service startup verifies that record before
importing an owner adapter. Auditctl intentionally remains an explicit
instance-registration exception because its released adapter contract exposes
`VuoroAuditAdapter.register`, not the shared function registration protocol.

## Sprintctl blind-agent promotion gate

The checked-in Sprintctl adapter must not be re-pinned merely because a source
commit contains served CLI guards. A candidate work adapter is ready for a
Vuoro pin only after its own checked-in tests prove catalog registration and
invocation for the complete blind-agent surface:

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

The released execution gate installs both the pinned ActionQ adapter and its
contracts companion, then runs `scripts/validate_released_execution_adapter.py`.
It registers the real owner catalog into the Vuoro shell with a side-effect-free
stub application and proves the portable candidate/group surface, exact owner
metadata, identity-derived provenance, schema rejection, and absence of
migration or runner operations. It opens no database and runs no startup or
migration code.

Parity fixtures must be falsifiable. For every supported filter, include at
least one independently excluded record; supply matching records out of their
expected order; invoke the real owner read boundary; and assert exact results.
Ignoring a filter, preserving input order accidentally, substituting an empty
response, or replacing the behavior under test must make the fixture fail.

CI fetches the immutable artifacts from the checked-in manifest, verifies
their digests, installs the pinned Sprintctl wheel with the built
`vuoro-service` wheel in an isolated environment, and runs
`scripts/validate_released_work_adapter.py`. The gate proves a populated read
is accepted and that both missing authority and invalid arguments are rejected
through the real Vuoro invocation envelope. It does not import Sprintctl from
a neighboring checkout.

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

For a changed execution adapter, install the pinned adapter and every pinned
companion wheel into an isolated environment with the built service wheel,
then run `scripts/validate_released_execution_adapter.py`. The gate exercises
the owner-published catalog through Vuoro's invocation shell, including
authenticated provenance, required authority, idempotency, and schema-negative
paths. It must not substitute an editable ActionQ checkout or a runner wheel
for the released service adapter.

Installation on a workstation or devbox is not release evidence. If an owner
change is committed and locally installed but has no immutable release, record
the tested source revision and stop before editing the composition manifest.
Publication by the owner and pinning by Vuoro are separate reviewable units.

## Release and operator boundary

Only after the source acceptance commands and released-wheel composition test
pass may a Vuoro maintainer create a `vuoro-service-v*` tag. That tag invokes
the image-publishing workflow and builds with the immutable checked-in pins.

Pinning or tagging does **not** authorize appservice work. An operator must
independently review and perform any image-digest update, identities or
authorization change, migration, backfill, rollout, four-domain black-box
validation, and the two-agent rehearsal. Source contributors must stop at the
published image artifact and hand those actions back to the operator.
