---
doc_id: vuoro-knowledge-workspace-phase0
title: Knowledge workspace Phase 0 baseline and contract
purpose: proposal
lifecycle: proposed
effective: 2026-10-14
subjects: [knowledge-workspace, knowledge-resolution]
related: [vuoro-knowledge-workspace-intention, vuoro-knowledge-workspace-design, vuoro-knowledge-workspace-roadmap, vuoro-knowledge-resolution]
---

# Knowledge workspace: Phase 0 baseline and contract

Status: proposed, 2026-10-14. On 2026-10-14 the operator approved implementing
the knowledge workspace
([intention](2026-10-14-knowledge-workspace-intention.md),
[design](2026-10-14-knowledge-workspace-design.md),
[roadmap](2026-10-14-knowledge-workspace-roadmap.md)) across the ecosystem.
This note records what exists today, the proposed resolution of the conflicts
with existing Vuoro boundaries, and the Phase 1 contract that implementation
follows. Ratifying this note stays with the operator. Roadmap Phase 0's
pilot-team, corpus and performance items are adapted to this estate in §6.

## 1. Baseline: what exists (read-only inventory, 2026-10-14)

| Need | Today | Consequence |
| --- | --- | --- |
| Workspace / tenancy | A free-text `workspace_id` on a bearer identity (issuer metadata such as `vuoro-native:agentops-mi1:0`), or one Cloud-assertion ULID per deployment in hosted mode. There is no membership table and no tenant. | Workspace membership and the four roles are new. |
| Authorization | Each operation has one `required_authority`, checked as set membership against `identity.authorities` (`vuoro_service/app.py` invoke path). Authorities are a flat list (`work:read`, `work:evidence`, ...). | Roles map onto new `knowledge:*` authorities (§3.2). |
| Database | CNPG `vuoro-postgres`, database `vuoro`, holds the `work`, `execution`, `knowledge` and `audit` schemas. The `knowledge` schema and the `vuoro_knowledge_migration`/`vuoro_knowledge_runtime` roles have been provisioned but unbound since S2 item 4. | The store goes in the `knowledge` schema. Roles, grants and the hourly `cnpg-pgdump-age` backup already cover it. |
| Migrations | An owner-held migrator: advisory lock, one-row `schema_version`, a min = max = current startup gate, and a manual quiesce → migrate → deploy cutover. The service never migrates. | Reuse the sprintctl pattern. |
| Idempotency | `work_idempotency_ledger` keyed (repo, workspace, principal, tool, key) with `request_digest`. The same key with a different digest is a conflict. | Reuse the shape, keyed (workspace, principal, operation, key). |
| Outbox / events | A per-aggregate `event_seq` and an SQLite producer outbox. There is no PG transactional outbox and no workspace-wide sequence. | New. |
| Change feed | Owner-issued opaque cursors; `cursor_expired` → `snapshot_required` (`docs/architecture/observable-resources.md`). | Reuse the semantics. |
| Object storage | None in Vuoro and none in-cluster. External Hetzner hel1 buckets serve backups, Loki and Langfuse. The vuoro-cloud PoC is BYO-S3. | A pluggable object store (§3.4). |
| Serving | The service requires exactly the `work` and `audit` domains (`composition.py` `_REQUIRED_DOMAINS`). The MCP edge has fixed tool buckets. | Phase 2 needs a coordinator composition change and an edge toolset. |
| `vuoro-knowledge` | A Git-derived catalog, search and resolve over repository docs. It holds no publication state. | Kept as is. Its retrieval code can be reused. |

## 2. Proposed resolution: one authority per collection

The conflict with `docs/architecture/knowledge-resolution.md` ("does not
replace the repositories where that knowledge is authored") resolves by
scope, as the design's own rule "implement one authority per content
collection" requires:

- **Ownership clarification (operator proposal intake, 2026-10-15).** Choose
  the canonical store by the owning review process and version lifecycle,
  rather than document type or physical locality. A cross-system ADR can
  belong in an architecture repository; a research conclusion can belong in
  a workspace. Other surfaces cite or project the original. Publication
  approval, engineering authority and work acceptance remain separate claims.
- **Repository-authored knowledge.** Docs whose owning review process and
  version lifecycle belong with engineering governance stay Git-authoritative.
  This includes code-coupled contracts and repository-owned plans, decisions
  and runbooks. They are
  resolved by `vuoro-knowledge` exactly as before. The workspace never writes
  them and never mirrors them as a second writable copy.
- **Workspace collections.** Research, deliverables, reviewed conclusions, and
  artifacts not tied to a code change form a new authority. PostgreSQL holds
  revisions, proposals and publication state, and the object store holds the
  bytes. Git is at most a later read-only projection (roadmap Phase 4).
- **Citing and importing.** A workspace item may cite a repository document by
  Git commit and path; that is a relation, not an import. An explicit import
  creates an independent copy that records its origin.

kctl stays `retiring` with no new operations. The workspace is a new owner,
not a kctl revival.

## 3. Phase 1 contract

### 3.1 Owner and placement

- **Owner.** A new package, `packages/vuoro-workspace` (distribution
  `vuoro-workspace`). It owns the domain state machine, the migrations under
  the `knowledge` schema, the store, the object-store interface, and, from
  P1b, a Vuoro adapter registering `knowledge.*` operations.
- **Release.** It ships as its own wheel and is composed into vuoro-service as
  a pinned release, like sprintctl and auditctl.
- **Location.** It lives in this repository because no other owner exists.
  This is an exception to the `AGENTS.md` rule that domain state machines and
  migrations stay in their owner repositories; the store PR amends
  `AGENTS.md` to record `packages/vuoro-workspace` as the owner, and ratifying
  this note confirms that exception. The `knowledge` schema and its roles were
  provisioned for kctl's domain and are reassigned to the workspace; kctl
  stays `retiring`.
- **Dependencies.** `psycopg[binary]>=3.2` and `vuoro-schema-runtime`. There is
  no ORM. The S3 backend uses `boto3` as an optional extra.

### 3.2 Roles and authorities

| Role | Authority | Allows |
| --- | --- | --- |
| reader | `knowledge:read` | search; read approved knowledge and approved artifacts; changes since; download approved |
| contributor | `knowledge:contribute` | everything a reader can do; see drafts; create items; upload artifact versions; propose |
| reviewer | `knowledge:review` | everything a contributor can do; approve or reject proposals and artifact versions |
| administrator | `knowledge:admin` | everything a reviewer can do; membership, retention, delete, export |

Effective authority is the intersection of two things: the credential's
authorities and the principal's current role in `knowledge.workspace_member`.

- Membership is checked on every call, so a cursor or a cached result never
  grants access.
- A principal marked as an agent is capped at contributor, so autonomous
  agents cannot publish.
- A proposal's author cannot review it.
- `workspace.create` requires `knowledge:admin` on the credential; the creator
  becomes the first administrator. A workspace keeps at least one administrator.
- Search scopes `working` and `history` require contributor or higher.
  Contributors see all drafts in their workspace (there are no private drafts).
- Delete, export and import require administrator.

### 3.3 State machines

- **Knowledge revision.**
  - States: `draft` → `proposed` → `approved` | `rejected`, with
    `proposed` → `withdrawn` and `approved` → `superseded` when a newer
    revision is approved.
  - Approval atomically checks `expected_base_revision_id = item.approved_revision_id`.
    It then moves the approved pointer, marks the prior approved revision
    `superseded`, and writes audit and outbox events.
  - If the base has moved, approval fails with `stale-base` and returns the
    current revision.
- **Artifact version, processing.** `reserved` → `verifying` → `ready` |
  `rejected` (checksum, size or type mismatch), and `reserved` → `expired`.
  Extraction is a separate dimension: `pending` → `ready` | `unsupported` |
  `failed`.
- **Artifact version, review.** `unreviewed` → `approved` | `rejected`, allowed
  only once processing is `ready`. Held
  in a separate column from processing. An upload never implies approval.
- **Deletion.** A tombstone (`deleted_at`, actor) removes the item from every
  read, search and count immediately. Purge is a later, separate step.

### 3.4 Storage and consistency

- **Event writes.** Every mutation's transaction writes its rows, one
  `workspace_event` (a monotonic per-workspace `seq` from a locked counter
  row), one `outbox` row and one `audit_event`.
- **Idempotency.** Retryable mutations take an idempotency key, scoped to (workspace, principal,
  operation, key) and matching
  `^[A-Za-z0-9._:-]{8,128}$`, stored with the request digest and the result.
  A replay returns the stored result; the same key with a different digest
  returns `idempotency-conflict`.
- **Object store.** It supports `put`/`head`/`get`/`delete` on keys of the form
  `ws/<workspace_id>/art/<artifact_id>/<version_id>`.
  - Backends: `fs` for tests and single-node use; `s3` for any S3-compatible
    bucket, with a per-workspace prefix and credentials scoped to the bucket.
  - Phase 1 writes the bytes through the store API. Presigned uploads come
    later.
  - Finalize re-reads the object, verifies the SHA-256 and size, sniffs the
    media type, and marks the version `ready`. It is idempotent.
- **Reconciliation.** It reports objects with no row that are older than the
  grace period, and rows whose object is missing. An orphan is deleted only on
  a second pass.
- **Search.** PostgreSQL FTS over title, Markdown body and extracted text,
  with the publication and access filters applied in the same query.
  - Phase 1 extracts Markdown and plain text, plus PDF text when `pypdf` is
    installed.
  - Anything else is marked `unsupported` and can still be downloaded.

### 3.5 Store operations (served as `knowledge.*.v1` from P1b)

`workspace.create`, `member.set`, `item.create`, `revision.draft`,
`proposal.submit`, `proposal.review`, `proposal.withdraw`, `artifact.begin`,
`artifact.finalize`, `artifact.review`, `read` (an exact version or
`latest-approved`), `download`, `search` (scope `approved` | `working` |
`history`), `changes_since` (a cursor), `delete`, `export`, `import`,
`reconcile`.

`changes_since` returns only events the caller's current role may see:
readers get published events (including tombstones of formerly approved
records), contributors and above also get working events.

Results carry stable ULIDs, version IDs, publication state, extraction state
and the index watermark.

### 3.6 Export manifest

`vuoro-workspace-export/v1` contains:

- the workspace ID and the snapshot `seq`;
- items with every revision: Markdown, hash, parent, author, sources, state and
  reviewer;
- artifacts with every version: SHA-256, size, media type, bundle path and
  states;
- relations and publication history;
- explicit `missing` entries for purged or unavailable objects.

Import creates a new workspace, records the source IDs in an `origin` mapping,
validates every reference and re-verifies every checksum.

## 4. Failure scenarios Phase 1 tests must cover

Artifact processing and review both use the word `rejected`; they are
separate columns and are always reported with their dimension.

1. A version is reserved but never finalized: it stays invisible and then expires.
2. Finalize with a wrong checksum or size: the version is `rejected` and never becomes ready.
3. Finalize is retried, or the same idempotency key is reused: the result is one version and the same response.
4. Two proposals share a base: the first approval wins and the second gets `stale-base`.
5. A contributor or agent tries to approve, or an author tries to approve their own proposal: refused.
6. A reader searches or reads the change feed: drafts and unreviewed artifacts are absent from results, counts and `changes_since`, and watermarks advance only on events the reader may see.
12. A reviewer tries to approve an artifact version whose processing is not `ready`: refused.
13. A non-administrator tries delete, export or import: refused.
7. A member is removed: their next read, search or `changes_since` is refused.
8. An item is deleted: it is absent from reads and search, and the change feed carries a tombstone.
9. An object is missing, or an object has no row: reconciliation reports it.
10. Export and then import: checksums, the ID mapping, relations and publication history are preserved.
11. Workspace isolation: nothing in workspace A can see or reference workspace B.

## 5. Sequencing across the ecosystem

| Step | Repo | Content |
| --- | --- | --- |
| P1a | vuoro | `vuoro-workspace` store, migrations, object store, and tests against a disposable PG |
| P1b | vuoro | Adapter registering `knowledge.*` ops; composition admits a third domain, `knowledge`; handshake compatibility entry |
| P1c | appservice | Knowledge migration Job and runtime DSN for vuoro-shared; object bucket (Hetzner hel1, workspace prefix, scoped key); DB PVC larger than 1 Gi; cutover runbook |
| P2 | vuoro, vuoro-cloud | MCP edge `knowledge` toolset; mapping from Cloud OAuth scopes to `knowledge:*` (vuoro-cloud only under its own release gate) |
| P2 | agentops | Agent guidance on when to save to the workspace and when to the repository |

## 6. Phase 0 items adapted to this estate

- **Pilot workflow.** Agent research that produces a report plus a data
  file. The first corpus is the evidence from the agent directory and inbox
  program.
- **Pilot "teams".** The estate's agent runtimes (Claude Code, Codex,
  OpenCode), with the operator as reviewer. The gate is three runtimes each
  completing save → review → reuse across two tasks.
- **Retrieval set.** 30 questions drawn from existing plans and evidence,
  written with P1b.
- **Envelope (proposed).**
  - Files up to 100 MiB.
  - A 5 GiB default workspace quota.
  - Search p95 under 2 s on that corpus.
  - Tombstone purge after 30 days.
  - Minimal audit metadata kept for 1 year.
  - Hetzner hel1 for object storage.
