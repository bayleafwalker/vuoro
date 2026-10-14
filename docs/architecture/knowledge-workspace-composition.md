---
doc_id: vuoro-knowledge-workspace-composition
title: Knowledge workspace composition
purpose: specification
lifecycle: proposed
effective: 2026-10-14
subjects: [knowledge-workspace]
related: [vuoro-knowledge-workspace-phase0]
---

# Knowledge workspace composition (P1b)

Step P1b of `docs/plans/2026-10-14-knowledge-workspace-phase0.md` serves the
`vuoro-workspace` store as `knowledge.*` operations in vuoro-service. This page
records how the domain is bound, configured, migrated and released.

## Domain binding

- **Owner adapter.** `vuoro_workspace.vuoro_adapter:register_knowledge_catalog`
  in `packages/vuoro-workspace`. It uses the same function-registration
  protocol as Sprintctl: `register(registry, application)`, where the
  application is a `KnowledgeApplication` built from the runtime DSN and the
  object store.
- **Descriptor.** Domain `knowledge`, API version `vuoro-workspace/v1` (the
  store's own `domain_api_version`), and schema version `knowledge-schema/v1`.
  It depends on the shared `vuoro-adapter-kit` and `vuoro-schema-runtime`
  locks.
- **Admission.** `composition.py` admits `knowledge` as a third domain
  beside the required `work` and `audit` domains. The fetch and attest
  scripts apply the same rule. Until the first `vuoro-workspace` release is
  pinned, the checked-in manifest stays work + audit, so `knowledge` is
  admitted but not yet required. The change that pins the release also moves
  `knowledge` into `_REQUIRED_DOMAINS` and both scripts' domain sets (see
  "Release and pin").
- **Operations.** There are 18 operations, `knowledge.<noun>.<verb>-v1`, none
  of them repo-scoped:

  | Operation | Authority | Semantics | Idempotency |
  | --- | --- | --- | --- |
  | `workspace.create`, `member.set`, `item.delete`, `artifact.delete` | `knowledge:admin` | write | required |
  | `workspace.reconcile` | `knowledge:admin` | write | not-allowed (convergent; `grace_seconds` ≥ 3600, default 86400) |
  | `item.create`, `revision.draft`, `proposal.submit`, `proposal.withdraw`, `artifact.begin`, `artifact.finalize` | `knowledge:contribute` | write | required |
  | `artifact.upload` | `knowledge:contribute` | write | not-allowed (last write wins until finalize; see below) |
  | `proposal.review`, `artifact.review` | `knowledge:review` | write | required |
  | `item.read`, `artifact.download`, `workspace.search`, `workspace.changes` | `knowledge:read` | read | not-allowed |

  The knowledge-only catalog revision is
  `e651dd978f73772e520f28f5ab9b6f46b77578ecc459d171d01eb4c204e75e49`. Together
  with the pinned work (97) and audit (5) operations, the three-domain catalog
  has 120 operations, revision
  `270c8e6c34563b7abb8e7fad54bfd9be522a3a2cec2f256cd91784142066fb8b`
  (`scripts/validate_knowledge_catalog_composition.py`). The served catalog
  stays at 102 operations until the pin lands.
- **Authority.** The shell checks the coarse credential authority
  (`authority-required`, 403). The store then intersects the credential's
  `knowledge:*` authorities with the caller's current membership role on
  every call. A refusal from the store is served as `knowledge-forbidden`
  (403), and every other mapped store error as `knowledge-<code>`. Error
  details are filtered to a per-code allowlist, so storage internals such as
  object keys and schema diagnostics are logged server-side and never sent to
  the caller. An unmapped store code is served as a generic
  `knowledge-internal-error` (500), and a failed schema check as
  `knowledge-unavailable` (503).
- **Principal.** The store principal is the identity's minted `principal_id`,
  never the display `actor`. An identity without a `principal_id` is refused.
  The agent cap **fails closed**. Every caller is an agent, capped at
  contributor, unless its credential carries the explicit `knowledge:human`
  marker authority. Only two issuers may mint that marker:
  - the operator-controlled static identity registry;
  - Vuoro Cloud, for human sessions.

  An identity minted under a delegated OAuth client grant (`client_id` or
  `grant_id` set) stays an agent even if it carries the marker. A workspace
  token or any other identity without the marker is an agent. A membership
  row can also mark a principal as an agent. The marker is not passed on as
  an authority.

  `knowledge:human` is only as strong as its issuer's policy. Vuoro
  verifies that a trusted issuer minted the credential, but it cannot verify
  that a person is behind it. Two issuers are allowed:
  - the operator-controlled static identity registry (`VUORO_IDENTITIES_FILE`),
    which must grant the marker only to identities a person uses directly;
  - Vuoro Cloud, which must grant it only to interactive human sessions,
    never to a connector, an OAuth client or a workspace token.

  An issuer that grants the marker to an automated credential lets that
  automation review and publish. Granting the marker is therefore an
  operator decision, made under the same review as granting
  `knowledge:review`.
- **Idempotency keys.** The shell accepts envelope keys of 1–256 characters,
  and the store's ledger takes `^[A-Za-z0-9._:-]{8,128}$`. A key that already
  matches passes through unchanged. Any other key becomes
  `vk1:<sha256 hex>` deterministically, so its replays and conflicts behave
  exactly like a conforming key's. The ledger is per principal, so a derived
  key can collide only with the same caller's own literal `vk1:` key.
- **Upload retries.** `artifact.upload` keeps no ledger. A retry with the
  same bytes converges. A retry with different bytes replaces the stored
  object, because the last write before finalize wins. Finalize then verifies
  the object against the SHA-256 and size fixed by `artifact.begin`, and
  rejects the version on a mismatch.
- **Bytes.** Uploads send `content_base64` in the JSON envelope. The limit is
  8 MiB per call in Phase 1, so a version is reserved with
  `expected_size` ≤ 8 MiB. Downloads compare the recorded size with the cap
  before fetching any bytes. They return base64 together with the recorded
  media type and `content_disposition: attachment`, in JSON only, and never
  as an HTTP body with an active content type. Larger artifacts wait for
  presigned uploads.
- **Request body limit.** The service shell refuses a
  `POST /api/invoke/v1` body over 12 MiB with `request-too-large` (413)
  before parsing it. The limit is `ServiceSettings.invoke_body_limit_bytes`
  and applies to the invoke route only. The MCP edge keeps its own 64 KiB
  limit; P2 handles that.
- **Concurrency.** Handlers are synchronous store calls, and they run in a
  worker thread (`asyncio.to_thread`), never on the service event loop.
  Connections come from a bounded pool of runtime-role connections (8 by
  default). A caller waits at most 10 s for a free connection; after that it
  gets `knowledge-unavailable` (503). Each connection has a 5 s
  `connect_timeout`, a 30 s `statement_timeout` (appended to any `options`
  already in the DSN, which are kept) and TCP keepalives (30 s idle, 3 probes
  10 s apart), so a dead network path is detected in about a minute. Before an idle
  connection is reused, the pool probes it with `SELECT 1` and replaces it
  if it is dead. The pool also discards a connection after any non-domain
  failure. The store's schema check runs once, when a pooled connection is
  opened, not on every call.

### Contract deviation: export and import are not served

Phase 0 §3.5 lists `export` and `import` among the store operations served as
`knowledge.*`. P1b deliberately does **not** serve them. They read and write
server-side bundle directories, and serving them would turn a filesystem path
into a caller-supplied argument. In Phase 1 they stay available through the
store API and to the operator only. Serving them needs a bundle-transfer
design, for example a bundle as an artifact with presigned transfer. That
design is open, and it must be decided before the Phase 1 export/import
scenario is served.

## Runtime configuration

| Variable | Required | Meaning |
| --- | --- | --- |
| `VUORO_KNOWLEDGE_RUNTIME_DSN` (alias `VUORO_KNOWLEDGE_DSN`) | yes | runtime-role DSN; it never runs DDL |
| `VUORO_KNOWLEDGE_OBJECT_STORE` | yes | `fs:/absolute/path` or `s3://bucket[/prefix]` |
| `VUORO_KNOWLEDGE_S3_ENDPOINT_URL`, `VUORO_KNOWLEDGE_S3_REGION` | for S3-compatible stores | boto3 client endpoint and region; credentials come from the standard AWS environment |
| `VUORO_KNOWLEDGE_SCHEMA` | no (default `knowledge`) | schema name |
| `VUORO_KNOWLEDGE_RUNTIME_ROLE` | yes | the runtime role the DSN must connect as; never defaulted to the connecting role |

The S3 backend needs the `vuoro-workspace[s3]` extra (boto3) installed in the
image.

## Startup gate (handshake)

`KnowledgeApplication.compatibility()` is the store's SELECT-only
`compatibility()` check. It never migrates, and it ignores
`VUORO_WORKSPACE_SCHEMA_MODE`. The result becomes the handshake's
`compatibility.domains.knowledge` entry. If the schema is incompatible, the
store's API version differs from the pin, or the database is unreachable,
startup refuses, exactly as for work and audit.

The gate also refuses an owner or migration DSN. The connecting role must
equal the configured `VUORO_KNOWLEDGE_RUNTIME_ROLE`. If the runtime role is
unset, schema-runtime's `role_not_configured` makes the check fail closed.
The gate further rejects a connection that owns the schema or is a member of
its owner role (`runtime_role_owns_schema`, checked with
`pg_has_role(current_user, nspowner, 'MEMBER')`, which also covers
non-inheriting membership), and a connection that is a
superuser (`runtime_role_is_superuser`).

## Migration entrypoint

The owner CLI ships in the same wheel. It runs as the migration role only,
mirroring the work Job's `sprintctl remote-schema migrate --url …`:

```bash
vuoro-workspace migrate --dsn "$VUORO_KNOWLEDGE_MIGRATION_DSN" --schema knowledge
vuoro-workspace check --dsn "$VUORO_KNOWLEDGE_RUNTIME_DSN" --schema knowledge \
  --runtime-role "$VUORO_KNOWLEDGE_RUNTIME_ROLE"   # exits 2 when incompatible
```

The runtime role needs `USAGE` on the schema and DML on its tables. P1c owns
the grants.

## Release and pin

- `vuoro-workspace` is released like the other in-repo packages: tag
  `vuoro-workspace-v<version>`, built by
  `.github/workflows/publish-python-packages.yaml`. That workflow also runs
  the workspace tests against a disposable PostgreSQL 16, then attests the
  wheel and publishes it as a GitHub release.
- The follow-up after merge, in one change:
  1. Tag `vuoro-workspace-v0.1.0`.
  2. Add the `knowledge-adapter` release lock (URL, SHA-256, source revision)
     and the descriptor above to `composition/adapter-pins.json`.
  3. Move `knowledge` into `_REQUIRED_DOMAINS` in `composition.py`,
     `fetch_pinned_adapters.py` and `attest_installed_composition.py`.
  4. Install the wheel in the `Dockerfile`'s `--no-deps` owner set.
  5. Switch the CI three-domain gate to the fetched wheel.
  6. Update the served catalog counts (102 → 120).

## Cutover requirement (P1c, appservice)

An image that pins `knowledge` refuses to start without its configuration.
Before deploying it, appservice must provide:

1. the knowledge migration Job, which runs `vuoro-workspace migrate` as the
   knowledge migration role and has completed;
2. the knowledge runtime DSN (`VUORO_KNOWLEDGE_RUNTIME_DSN`), the object
   store (`VUORO_KNOWLEDGE_OBJECT_STORE` plus a bucket-scoped credential) and
   the runtime grants.

Deploying the pinned image before both are in place fails startup closed. It
does not degrade.
