# Disposable native settlement demonstration

This optional distribution is a conformance consumer. It is not installed in
the production service image, and adds no owner API, runner, state store or
client transport behavior. Install the wheel in a fresh Python 3.12 environment;
its metadata binds released client 0.1.2, service 0.1.92 and Sprintctl 0.18.0
artifacts by URL and SHA256. PostgreSQL 16 tools and Git must be installed.

```sh
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt proof.json
```

The command creates its own private PostgreSQL cluster and Unix socket under
`/tmp`, separates migration and runtime roles, verifies runtime DDL refusal,
and starts a loopback HTTP shell. It accepts no database URL, existing backend,
live mode, served profile or cloud credential. Fixture bearer identities use
the shipped evaluation resolver; this proves neither vendor OAuth nor hosted
workers. The demo takes approximately 35 seconds because it waits for natural
owner lease expiry, rather than editing lease timestamps.

The native client creates X and its dependent Y, reserves an exact Release,
creates a real Git commit with its Release trailer, and harvests that observation
through Sprintctl's existing producer and ingest protocol. A proposal admits
the exact revision, reserve key, commit and evidence head. A separate fixture
verifier checks file content, patch applicability and path scope, records a
protected receipt for the raw patch and canonical intent, then accepts that
artifact. Its named checks are scripted conformance assertions, not independent
human review or external execution attestations.

Artifact acceptance leaves X unsettled and Y blocked. The command kills caller
A's real process, proves a fresh B acquisition is refused, waits for the owner
to evaluate A's lease stale, then takes it over. A's late heartbeat and result
are refused; the owner retains its payload. B's named `tests` and `review`
checks settle work via the sole owner Decision writer. The receipt verifies
that Decision's Release and outcome digest and Y's readiness. Raw patch,
canonical intent, verification receipt, Release and outcome digests remain
distinct. Native recovery is demonstrated by reopening and exporting one
incident observation; it does not replay or claim authority.

All successful operation results and expected refusals appear in a private
machine-readable receipt. The service and database stop before success is
reported; fixture data and bearer credentials are deleted. Receipts are created
exclusively with mode 0600. Reusing an output path refuses before startup.
If owned process shutdown cannot be confirmed, the command reports incomplete
and retains private scratch diagnostics instead of deleting a running cluster.

```sh
# Both commands must fail honestly and produce status=incomplete receipts.
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt missing-proof.json --omit-required-verification
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt missing-check.json --omit-required-check
```

This proves client/process interruption and existing owner settlement on a
disposable database. It does not prove database crash durability, hosted access,
external patch execution or production rollout. Run standalone tests with the
demo installed: `python -m pytest packages/vuoro-demo/tests`.
