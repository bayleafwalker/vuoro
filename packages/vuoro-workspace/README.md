# vuoro-workspace

The knowledge workspace store: workspace collections of reviewed knowledge
and versioned artifacts, with publication state, held in the PostgreSQL
`knowledge` schema plus a pluggable object store. Contract:
[`docs/plans/2026-10-14-knowledge-workspace-phase0.md`](../../docs/plans/2026-10-14-knowledge-workspace-phase0.md) §3–§4.

## Ownership boundary

- **Owns** the workspace domain: workspaces and membership (reader,
  contributor, reviewer, administrator), knowledge items and immutable
  revisions, proposals and their review, artifacts and versions (processing,
  review and extraction states), version-pinned relations, the per-workspace
  event sequence, the transactional outbox, the audit trail, the idempotency
  ledger, the migrations for the `knowledge` schema, and the object-store
  interface.
- **Does not own** Git-authored repository documentation. Plans, decisions,
  runbooks and docs that change with code stay authoritative in Git and are
  resolved by `vuoro-knowledge`. A workspace item may *cite* a repository
  document (`repository_document` relation with repository, commit and path);
  it never writes or mirrors one.
- **Not here yet:** the Vuoro adapter registering `knowledge.*.v1`
  operations and service composition (P1b), deployment, DSNs, buckets and
  credentials (P1c, appservice). Runtime code never runs DDL; only
  `vuoro-workspace migrate` (the migration role) does.

## Use

```python
from vuoro_workspace import FsObjectStore, Principal, WorkspaceStore

store = WorkspaceStore.connect(dsn, FsObjectStore("/var/lib/vuoro-workspace"), schema="knowledge")
admin = Principal("user:alice", {"knowledge:read", "knowledge:contribute", "knowledge:review", "knowledge:admin"})
ws = store.workspace_create(admin, "Research")["workspace_id"]
```

Effective authority is the credential's `knowledge:*` authorities intersected
with the caller's current role in `workspace_member`, re-checked on every
call. Agent principals (membership flag or credential flag) are capped at
contributor; an author can never review their own proposal or artifact
version.

Operations (`WorkspaceStore` methods): `workspace_create`, `member_set`,
`item_create`, `revision_draft`, `proposal_submit`, `proposal_review`,
`proposal_withdraw`, `artifact_begin`, `artifact_upload` (Phase 1 transfer
step through the store API), `artifact_finalize`, `artifact_review`, `read`,
`download`, `search` (`approved` | `working` | `history`), `changes_since`,
`delete`, `export`, `import_bundle`, `reconcile`. Mutations accept an
`idempotency_key` (`^[A-Za-z0-9._:-]{8,128}$`).

Object stores: `FsObjectStore(root)` and `S3ObjectStore(bucket, prefix,
client=...)` (`pip install 'vuoro-workspace[s3]'` for boto3). PDF text
extraction needs the `pdf` extra (`pypdf`); without it PDFs are stored and
downloadable with extraction state `unsupported`.

Content safety and limits: only allow-listed text types (plain, Markdown,
CSV, TSV, JSON, YAML) and magic-verified binaries keep their declared media
type; anything else (HTML, SVG, XML, unknown binaries) is stored as
`application/octet-stream`, and `download()` always reports
`content_disposition: attachment`. `body_md` is capped at 1,000,000
characters and extracted text is truncated to the same size
(`extraction_truncated`); only a 200,000-character prefix is indexed,
because a PostgreSQL tsvector is limited to 1 MB. Version pointers
(`latest_version_id`, `approved_version_id`) only move forward in reservation
order. A finalize stranded in `verifying` is re-verified by any retry, and
`reconcile` resets it (or expires it) after `verify_timeout`.

## Migrations

```bash
VUORO_WORKSPACE_DSN=... vuoro-workspace migrate --schema knowledge   # migration role
VUORO_WORKSPACE_DSN=... vuoro-workspace check --schema knowledge --runtime-role vuoro_knowledge_runtime
```

The runtime admits exactly schema version 1 (min = max = current). An
embedding service may call `startup_schema_handshake(conn, os.environ)`;
`VUORO_WORKSPACE_SCHEMA_MODE=operator-migrate` is the only way it migrates.

## Tests

```bash
VUORO_WORKSPACE_TEST_PG_BIN=/usr/lib/postgresql/16/bin \
  uv run --package vuoro-workspace --extra test pytest packages/vuoro-workspace/tests
```

`VUORO_WORKSPACE_TEST_PG_BIN` creates a throwaway cluster on a private socket;
`VUORO_WORKSPACE_TEST_DSN` points at a disposable server instead. Without
either, database tests skip.
