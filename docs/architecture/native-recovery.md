# Native incident recovery records

Vuoro's disconnected server prototype was removed in August 2026. The retained
`vuoro_client.recovery.RecoveryLog` is an offline, incident-scoped export for
observations and requested commands. It neither claims work nor commits an
owner decision. A requested command is text for review and is never executed
automatically.

Normal work delivery remains the domain owner's responsibility. Sprintctl
owns its producer outbox, ingest deduplication, synchronization and authority
reconciliation: see its [outbox ADR](https://github.com/bayleafwalker/sprintctl/blob/main/docs/plans/adr-outbox-sync-model.md),
[normal synchronization guide](https://github.com/bayleafwalker/sprintctl/blob/main/docs/guides/normal-sync.md)
and [authority commands](https://github.com/bayleafwalker/sprintctl/blob/main/docs/guides/authority-commands.md).
This export is not a generic outbox and has no import or service write path.
Once connectivity returns, inspect the export and fresh owner facts, then use
that owner's supported explicit operation with the required authorization.
Local recording and export do not establish that an owner accepted or applied
anything.

## Record an incident

Use the installed client on a POSIX local filesystem with reliable `flock` and
`fsync`. The default root is `~/.vuoro/recovery`; `VUORO_RECOVERY_ROOT` selects
an explicit alternative. Choose one incident name of at most 128 characters,
starting with a letter or digit and containing letters, digits, `.`, `_` or `-`.

```sh
vuoro recovery begin --incident service-outage-20261010
vuoro recovery observe --incident service-outage-20261010 \
  --summary 'Owner read unavailable; last observed revision recorded' \
  --basis-revision observed-revision
vuoro recovery request-command --incident service-outage-20261010 \
  --summary 'Ask the owner to inspect before any recovery action' \
  --basis-revision observed-revision \
  --command '{"command_type":"inspect","params":{}}'
vuoro recovery export --incident service-outage-20261010
```

The namespace directory is mode `0700` and its `records.jsonl` is mode `0600`.
Only a singly linked regular file owned by the current user is supported;
symlink directories, symlink files, hardlinked stores and special files are
refused. Existing ordinary storage permissions are tightened without changing
authored bytes. Protect any redirected export as well; it can contain local
incident details. Keep credentials out of record payloads.

## Interrupted writes and explicit retry

Successful append returns only after the complete line has been flushed and
fsynced. Concurrent cooperating processes lock the same incident file while
checking history and appending. The Python API accepts `record_id`; preserve
that ID and every authored field, including `created_at`, across a lost reply.
An exact JSON payload retry returns the original record without another row.
An ID reused with different content, revision or JSON types is refused. The
CLI creates a new ID and timestamp for each invocation; repeating a CLI append
is a new observation, not an exact retry of a lost reply.

A process interrupted during its write can leave an incomplete tail. Reopening
does not repair it. Export and subsequent append refuse incomplete, malformed,
duplicate-ID or foreign-incident history. Preserve a copy for inspection and
use a separately named incident for new observations; do not truncate or edit
the original and then claim continuity. A complete fsynced row followed by a
lost response is recoverable through the exact API retry above. File-system or
power-loss durability depends on the local filesystem honoring `fsync`; no
network-filesystem or hostile same-user file replacement guarantee is claimed.

The rest of the transport client remains importable when POSIX locking is
unavailable. Using this optional recovery store then refuses explicitly.

The executable tests in
`packages/vuoro-client/tests/test_recovery_log.py` cover concurrent same-ID
retries, process exit after fsync before response, interrupted partial writes,
private storage, corrupt history and the client import boundary. They do not
prove an external owner effect or production recovery.
