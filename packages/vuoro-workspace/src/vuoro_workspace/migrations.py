"""Owner-held migrations for the PostgreSQL ``knowledge`` schema.

Follows the sprintctl migrator pattern: a global advisory transaction lock,
a one-row ``schema_version`` table (plus a digest ledger checked through
``vuoro_schema_runtime``), and a MIN == MAX == CURRENT startup gate.
Runtime code calls :func:`require_compatible_schema` (SELECT only); DDL is
reachable only through :func:`migrate_schema`, used by the
``vuoro-workspace migrate`` operator command or the explicit
``operator-migrate`` startup mode.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Mapping

from vuoro_schema_runtime import (
    CompatibilityReport,
    MigrationResult,
    SchemaCompatibilityError,
    compatibility_report,
    identifier,
    migration_asset,
    render_schema_sql,
    validate_contiguous_migrations,
)

DOMAIN_API_VERSION = "vuoro-workspace/v1"
DEFAULT_SCHEMA = "knowledge"
CURRENT_SCHEMA_VERSION = 1
MINIMUM_SCHEMA_VERSION = 1
MAXIMUM_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
STARTUP_MODE_ENV = "VUORO_WORKSPACE_SCHEMA_MODE"
MIGRATION_LOCK_TIMEOUT_ENV = "VUORO_WORKSPACE_MIGRATION_LOCK_TIMEOUT"
READ_ONLY_STARTUP_MODE = "read-only"
OPERATOR_MIGRATE_STARTUP_MODE = "operator-migrate"
# "KNOW", "WKSP": stable across releases; taken before inspecting state.
SCHEMA_MIGRATION_LOCK_KEYS = (0x4B4E4F57, 0x574B5350)


class SchemaMigrationError(RuntimeError):
    """The migration ledger is malformed or newer than this migrator."""


_V1 = r"""
CREATE TABLE __SCHEMA__.schema_version (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  version integer NOT NULL CHECK (version >= 1)
);
CREATE TABLE __SCHEMA__.schema_migration (
  version integer PRIMARY KEY,
  name text NOT NULL,
  sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE __SCHEMA__.workspace (
  workspace_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  name text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL,
  origin jsonb,
  retention jsonb NOT NULL DEFAULT '{}'::jsonb,
  deleted_at timestamptz,
  deleted_by text
);
CREATE TABLE __SCHEMA__.workspace_seq (
  workspace_id text PRIMARY KEY REFERENCES __SCHEMA__.workspace,
  last_seq bigint NOT NULL DEFAULT 0
);
CREATE TABLE __SCHEMA__.workspace_member (
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  principal_id text NOT NULL,
  role text NOT NULL CHECK (role IN ('reader','contributor','reviewer','administrator')),
  is_agent boolean NOT NULL DEFAULT false,
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, principal_id),
  CHECK (NOT is_agent OR role IN ('reader','contributor'))
);

CREATE TABLE __SCHEMA__.knowledge_item (
  item_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  approved_revision_id text,
  latest_revision_id text,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL,
  deleted_at timestamptz,
  deleted_by text,
  UNIQUE (workspace_id, item_id)
);
CREATE TABLE __SCHEMA__.knowledge_revision (
  revision_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  item_id text NOT NULL,
  parent_revision_id text REFERENCES __SCHEMA__.knowledge_revision,
  title text NOT NULL,
  body_md text NOT NULL,
  content_sha256 text NOT NULL,
  author_id text NOT NULL,
  sources jsonb NOT NULL DEFAULT '[]'::jsonb,
  state text NOT NULL CHECK (state IN ('draft','proposed','approved','rejected','withdrawn','superseded')),
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL,
  search_tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english'::regconfig, title), 'A') ||
    setweight(to_tsvector('english'::regconfig, body_md), 'B')
  ) STORED,
  FOREIGN KEY (workspace_id, item_id) REFERENCES __SCHEMA__.knowledge_item (workspace_id, item_id)
);
CREATE INDEX knowledge_revision_tsv ON __SCHEMA__.knowledge_revision USING gin (search_tsv);
CREATE INDEX knowledge_revision_item ON __SCHEMA__.knowledge_revision (workspace_id, item_id);

CREATE TABLE __SCHEMA__.proposal (
  proposal_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  item_id text NOT NULL,
  revision_id text NOT NULL UNIQUE REFERENCES __SCHEMA__.knowledge_revision,
  expected_base_revision_id text,
  rationale text NOT NULL DEFAULT '',
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  author_id text NOT NULL,
  state text NOT NULL CHECK (state IN ('open','approved','rejected','withdrawn')),
  reviewer_id text,
  review_note text,
  decided_at timestamptz,
  created_at timestamptz NOT NULL
);

CREATE TABLE __SCHEMA__.artifact (
  artifact_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  name text NOT NULL,
  latest_version_id text,
  approved_version_id text,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL,
  deleted_at timestamptz,
  deleted_by text,
  UNIQUE (workspace_id, artifact_id)
);
CREATE TABLE __SCHEMA__.artifact_version (
  version_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  artifact_id text NOT NULL,
  object_key text NOT NULL UNIQUE,
  expected_sha256 text NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
  expected_size bigint NOT NULL CHECK (expected_size >= 0),
  declared_media_type text NOT NULL,
  sha256 text,
  size_bytes bigint,
  media_type text,
  processing_state text NOT NULL CHECK (processing_state IN ('reserved','verifying','ready','rejected','expired')),
  review_state text NOT NULL DEFAULT 'unreviewed' CHECK (review_state IN ('unreviewed','approved','rejected')),
  extraction_state text NOT NULL DEFAULT 'pending' CHECK (extraction_state IN ('pending','ready','unsupported','failed')),
  reject_reason text,
  finalize_result jsonb,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  finalized_at timestamptz,
  reviewed_by text,
  reviewed_at timestamptz,
  FOREIGN KEY (workspace_id, artifact_id) REFERENCES __SCHEMA__.artifact (workspace_id, artifact_id),
  CHECK (review_state = 'unreviewed' OR processing_state = 'ready')
);
CREATE INDEX artifact_version_artifact ON __SCHEMA__.artifact_version (workspace_id, artifact_id);

CREATE TABLE __SCHEMA__.extraction (
  extraction_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  version_id text NOT NULL REFERENCES __SCHEMA__.artifact_version,
  extractor text NOT NULL,
  extractor_version text NOT NULL,
  text_content text NOT NULL,
  anchors jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL,
  search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text_content)) STORED,
  UNIQUE (version_id, extractor, extractor_version)
);
CREATE INDEX extraction_tsv ON __SCHEMA__.extraction USING gin (search_tsv);

CREATE TABLE __SCHEMA__.relation (
  relation_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  kind text NOT NULL CHECK (kind IN ('derived_from','supports','supersedes','cites')),
  source_kind text NOT NULL CHECK (source_kind IN ('revision','artifact_version')),
  source_id text NOT NULL,
  target_kind text NOT NULL CHECK (target_kind IN ('revision','artifact_version','repository_document')),
  target_id text,
  target_ref jsonb,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL,
  CHECK ((target_kind = 'repository_document') = (target_id IS NULL))
);
CREATE INDEX relation_source ON __SCHEMA__.relation (workspace_id, source_id);

CREATE TABLE __SCHEMA__.audit_event (
  audit_id text PRIMARY KEY,
  workspace_id text,
  actor_id text NOT NULL,
  action text NOT NULL,
  target_kind text,
  target_id text,
  outcome text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL
);
CREATE TABLE __SCHEMA__.workspace_event (
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  seq bigint NOT NULL,
  event_id text NOT NULL UNIQUE,
  kind text NOT NULL,
  entity_kind text NOT NULL,
  entity_id text NOT NULL,
  version_id text,
  visibility text NOT NULL CHECK (visibility IN ('published','working','admin')),
  tombstone boolean NOT NULL DEFAULT false,
  actor_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, seq)
);
CREATE TABLE __SCHEMA__.outbox (
  outbox_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  seq bigint NOT NULL,
  topic text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  dispatched_at timestamptz,
  FOREIGN KEY (workspace_id, seq) REFERENCES __SCHEMA__.workspace_event (workspace_id, seq)
);
CREATE INDEX outbox_pending ON __SCHEMA__.outbox (created_at) WHERE dispatched_at IS NULL;

CREATE TABLE __SCHEMA__.idempotency_ledger (
  workspace_id text NOT NULL,
  principal_id text NOT NULL,
  operation text NOT NULL,
  idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
  request_digest text NOT NULL,
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, principal_id, operation, idempotency_key)
);

CREATE TABLE __SCHEMA__.origin_map (
  workspace_id text NOT NULL REFERENCES __SCHEMA__.workspace,
  entity_kind text NOT NULL,
  source_id text NOT NULL,
  local_id text NOT NULL,
  PRIMARY KEY (workspace_id, entity_kind, source_id)
);
CREATE TABLE __SCHEMA__.reconcile_orphan (
  workspace_id text NOT NULL,
  object_key text NOT NULL,
  first_seen_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, object_key)
);
"""

MIGRATIONS = validate_contiguous_migrations((migration_asset(1, "initial-knowledge-workspace", _V1),))


def _schema_exists(cur: Any, schema: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS rel", (f'"{schema}".schema_version',))
    return _first(cur.fetchone()) is not None


def _first(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return next(iter(row.values()))
    return row[0]


def read_schema_state(cur: Any, schema: str) -> tuple[int | None, list[tuple[int, str, str]]]:
    """Return (version, ledger) using SELECT only. Raises on an ambiguous version table."""
    identifier(schema, "schema")
    if not _schema_exists(cur, schema):
        return None, []
    cur.execute(f'SELECT count(*), min(version), max(version) FROM "{schema}".schema_version')
    row = cur.fetchone()
    values = list(row.values()) if isinstance(row, Mapping) else list(row)
    count, minimum, maximum = values
    if count != 1 or minimum != maximum:
        raise SchemaMigrationError("schema_version must contain exactly one unambiguous version row")
    cur.execute(f'SELECT version, name, sha256 FROM "{schema}".schema_migration ORDER BY version')
    ledger = [tuple(r.values()) if isinstance(r, Mapping) else tuple(r) for r in cur.fetchall()]
    return int(minimum), ledger  # type: ignore[return-value]


def compatibility(conn: Any, schema: str = DEFAULT_SCHEMA, runtime_role: str | None = None) -> CompatibilityReport:
    """Check the installed schema against this release (SELECT only).

    ``runtime_role`` is the configured runtime role; when omitted the role
    check is satisfied by the current role (single-role development use).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        current_role = _first(cur.fetchone())
        extra: list[str] = []
        try:
            version, ledger = read_schema_state(cur, schema)
        except SchemaMigrationError:
            version, ledger = None, []
            extra.append("schema_version_ambiguous")
    if not conn.autocommit:
        conn.rollback()
    if version is not None and (not ledger or max(r[0] for r in ledger) != version):
        extra.append("schema_version_ledger_mismatch")
    report = compatibility_report(
        MIGRATIONS,
        ledger or None,
        schema=schema,
        domain_api_version=DOMAIN_API_VERSION,
        minimum_schema_version=MINIMUM_SCHEMA_VERSION,
        maximum_schema_version=MAXIMUM_SCHEMA_VERSION,
        current_role=current_role,
        configured_role=runtime_role if runtime_role is not None else current_role,
    )
    if extra:
        report = replace(report, compatible=False, reasons=tuple(dict.fromkeys(extra + list(report.reasons))))
    return report


def require_compatible_schema(conn: Any, schema: str = DEFAULT_SCHEMA, runtime_role: str | None = None) -> CompatibilityReport:
    """Fail closed unless the installed schema is exactly supported; never runs DDL."""
    report = compatibility(conn, schema, runtime_role)
    if not report.compatible:
        raise SchemaCompatibilityError(
            f"knowledge workspace schema {schema!r} is incompatible "
            f"(installed={report.installed_schema_version}, supported="
            f"{MINIMUM_SCHEMA_VERSION}..{MAXIMUM_SCHEMA_VERSION}, reasons={list(report.reasons)}); "
            "run `vuoro-workspace migrate` as the migration role first"
        )
    return report


def migrate_schema(conn: Any, schema: str = DEFAULT_SCHEMA) -> MigrationResult:
    """Apply pending migrations under the global advisory transaction lock."""
    identifier(schema, "schema")
    applied: list[int] = []
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", SCHEMA_MIGRATION_LOCK_KEYS)
            cur.execute(
                "SELECT set_config('lock_timeout', %s, true)",
                (os.environ.get(MIGRATION_LOCK_TIMEOUT_ENV, "5s"),),
            )
            version, ledger = read_schema_state(cur, schema)
            if version is not None and version > CURRENT_SCHEMA_VERSION:
                raise SchemaMigrationError(
                    f"schema is newer than this migrator (installed={version}, current={CURRENT_SCHEMA_VERSION})"
                )
            by_version = {a.version: a for a in MIGRATIONS}
            for row_version, name, digest in ledger:
                asset = by_version.get(row_version)
                if asset is None or asset.name != name or asset.sha256 != digest:
                    raise SchemaMigrationError(f"migration ledger drift at version {row_version}")
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            for asset in MIGRATIONS:
                if version is not None and asset.version <= version:
                    continue
                cur.execute(render_schema_sql(asset.sql, schema))
                cur.execute(
                    f'INSERT INTO "{schema}".schema_migration (version, name, sha256) VALUES (%s, %s, %s)',
                    (asset.version, asset.name, asset.sha256),
                )
                cur.execute(
                    f'INSERT INTO "{schema}".schema_version (singleton, version) VALUES (true, %s) '
                    "ON CONFLICT (singleton) DO UPDATE SET version = EXCLUDED.version",
                    (asset.version,),
                )
                applied.append(asset.version)
            cur.execute("SELECT current_user")
            role = _first(cur.fetchone())
    return MigrationResult(
        schema=schema,
        installed_version=CURRENT_SCHEMA_VERSION,
        applied_versions=tuple(applied),
        migration_role=role,
    )


def startup_schema_handshake(
    conn: Any,
    environ: Mapping[str, str],
    schema: str = DEFAULT_SCHEMA,
    runtime_role: str | None = None,
) -> CompatibilityReport:
    """Apply the explicit startup mode, then require a compatible schema."""
    mode = environ.get(STARTUP_MODE_ENV, READ_ONLY_STARTUP_MODE)
    if mode == OPERATOR_MIGRATE_STARTUP_MODE:
        migrate_schema(conn, schema)
    elif mode != READ_ONLY_STARTUP_MODE:
        raise SchemaCompatibilityError(
            f"invalid {STARTUP_MODE_ENV}={mode!r}; expected "
            f"{READ_ONLY_STARTUP_MODE!r} or {OPERATOR_MIGRATE_STARTUP_MODE!r}"
        )
    return require_compatible_schema(conn, schema, runtime_role)
