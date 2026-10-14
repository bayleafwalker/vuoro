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
  | `workspace.reconcile` | `knowledge:admin` | write | not-allowed (convergent) |
  | `item.create`, `revision.draft`, `proposal.submit`, `proposal.withdraw`, `artifact.begin`, `artifact.finalize` | `knowledge:contribute` | write | required |
  | `artifact.upload` | `knowledge:contribute` | write | not-allowed (convergent put; finalize verifies the digest) |
  | `proposal.review`, `artifact.review` | `knowledge:review` | write | required |
  | `item.read`, `artifact.download`, `workspace.search`, `workspace.changes` | `knowledge:read` | read | not-allowed |

  The knowledge-only catalog revision is
  `133e24ad0490a7e3302d510f22c59b7f3dd37456d7ae2892c4bd8d4d2fd21767`. Together
  with the pinned work (97) and audit (5) operations, the three-domain catalog
  has 120 operations, revision
  `01e47ad04f9be5a0c1e9d5b5657fdba4712d4d9d1b15b56f9a231941659b103c`
  (`scripts/validate_knowledge_catalog_composition.py`). The served catalog
  stays at 102 operations until the pin lands.
- **Authority.** The shell checks the coarse credential authority
  (`authority-required`, 403). The store then intersects the credential's
  `knowledge:*` authorities with the caller's current membership role on
  every call. A refusal from the store is served as `knowledge-forbidden`
  (403), and every other store error as `knowledge-<code>`.
- **Principal.** The store principal is the identity's minted `principal_id`,
  never the display `actor`. An identity without a `principal_id` is refused.
  The caller counts as an agent, and is capped at contributor, in either of
  two cases:
  - the identity was minted under a delegated OAuth client grant
    (`client_id` or `grant_id` set): a connected client acting for a user;
  - the credential carries the marker authority `knowledge:agent`.

  A membership row can also mark a principal as an agent.
- **Bytes.** Uploads send `content_base64` in the JSON envelope. The limit is
  8 MiB per call in Phase 1, so a version is reserved with
  `expected_size` ≤ 8 MiB. Downloads return base64 together with the
  recorded media type in JSON only, and never as an HTTP body with an active
  content type. Larger artifacts wait for presigned uploads. The MCP edge has
  its own 64 KiB body limit; P2 handles that.
- **Not served.** Workspace export and import read and write server-side
  bundle directories. They stay store-API and operator only until a
  bundle-transfer design exists.

## Runtime configuration

| Variable | Required | Meaning |
| --- | --- | --- |
| `VUORO_KNOWLEDGE_RUNTIME_DSN` (alias `VUORO_KNOWLEDGE_DSN`) | yes | runtime-role DSN; it never runs DDL |
| `VUORO_KNOWLEDGE_OBJECT_STORE` | yes | `fs:/absolute/path` or `s3://bucket[/prefix]` |
| `VUORO_KNOWLEDGE_S3_ENDPOINT_URL`, `VUORO_KNOWLEDGE_S3_REGION` | for S3-compatible stores | boto3 client endpoint and region; credentials come from the standard AWS environment |
| `VUORO_KNOWLEDGE_SCHEMA` | no (default `knowledge`) | schema name |
| `VUORO_KNOWLEDGE_RUNTIME_ROLE` | no | expected runtime role for the role check |

The S3 backend needs the `vuoro-workspace[s3]` extra (boto3) installed in the
image.

## Startup gate (handshake)

`KnowledgeApplication.compatibility()` is the store's SELECT-only
`compatibility()` check. It never migrates, and it ignores
`VUORO_WORKSPACE_SCHEMA_MODE`. The result becomes the handshake's
`compatibility.domains.knowledge` entry. If the schema is incompatible, the
store's API version differs from the pin, or the database is unreachable,
startup refuses, exactly as for work and audit. Each invocation opens its own
autocommit connection, and the store re-checks the schema when it is
constructed.

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
