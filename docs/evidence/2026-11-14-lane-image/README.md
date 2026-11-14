# Optional lane image: local qualification, 14 November 2026

The final image uses exact committed Vuoro source74f132a981816264c6ff9f0efbc10c81a9be625b,
including the optional manifest, consumer regression and schema evidence correction.
The lane manifest SHA-256 is d8d0ec67…fab43. The default manifest is unchanged.
The base image is digest-pinned Python3.13; the added owner is the released
lanectl0.2.12 wheel from b56f9cf, SHA-256934f0393…ac30. Existing five owner/shared
pins remain unchanged. The actual local image built in21.152s, installed its
hash-locked runtime dependencies, passed pip check and recorded the six-wheel
installed attestation. The build's temporary PEP517 build-tool resolution is not
a claim of reproducible image bytes; reproducible wheel evidence is separate.

The final real-image probe ran as UID65532, with network disabled, a read-only
root, no capabilities and an isolated tmpfs. All11 groups passed. Existing
three-owner catalog registration retained its exact150 definitions and revision;
the optional owner added exactly two scoped operations, yielding152. Four
mutated catalog controls failed the same oracle. Actual released-wheel corruption
and removal were refused before installation. The Python3.12 guard used a
synthetic version tuple; no3.12 negative image was built.

The actual owner refused an unmigrated database without creating it, then
composed after explicit migration. Read/append, exact replay, changed payload,
other principal, stale revision, invalid atomic batch and scope refusals passed.
A two-thread/two-connection history produced one winner and one conflict.
Snapshot paging excluded a later append while its unpinned control observed it.
SQLite online backup and a separate quiescent restore preserved events; an
incompatible restore was refused. The first probe had a fixture-only descriptor
mutation error; its source and log are retained. The initial image did not change; that proof is retained under
initial-base-plus-manifest. The final source image independently repeated all11groups.

These are bounded local image/owner observations. Baseline work/audit/knowledge
registration used explicit no-database fixtures. This does not qualify their
actual database migrations, a full composed HTTP boot, real gateway identities,
a deployed service, persistent hosted storage, backup policy or production
restore drills. JSONL remains authoritative. No credentials, grants, profile,
production deployment, registry promotion or authority selection changed.

The container and its temporary data were removed after exit0. The exact image
is retained locally for independent review and is not cross-host durable. Source
and retained evidence become cross-host-replicated when canonically landed.

The unfiltered source suite passed3869tests with153skips and17warnings, using
the pinned contract-tool inventory and PostgreSQL16. The source0.1.106schema-map
reference was corrected to its verified public tag commit; its existing test was
left unchanged and demonstrated red then green. Skipped tests remain unqualified.
The final image ID is sha256:a29aa4f2baf467575be11833cba003a873d2edcfada4e17c63895e633ff40c91.
