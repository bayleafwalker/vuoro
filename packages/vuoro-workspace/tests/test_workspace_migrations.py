"""Migration gate: explicit operator migrate, read-only runtime check, MIN == MAX == CURRENT."""

from __future__ import annotations

import json

import pytest
from vuoro_schema_runtime import SchemaCompatibilityError

from workspace_fixtures import Clock, clock, env, pg_conn, pg_dsn, schema  # noqa: F401  (fixtures)
from vuoro_workspace import FsObjectStore, WorkspaceStore, compatibility, migrate_schema, startup_schema_handshake
from vuoro_workspace.cli import main as cli_main
from vuoro_workspace.migrations import (
    CURRENT_SCHEMA_VERSION,
    OPERATOR_MIGRATE_STARTUP_MODE,
    STARTUP_MODE_ENV,
    SchemaMigrationError,
    require_compatible_schema,
)


def tables(conn, schema):
    return {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)).fetchall()}


def test_runtime_refuses_unmigrated_schema_and_never_creates_it(pg_conn, schema, tmp_path):
    report = compatibility(pg_conn, schema)
    assert not report.compatible and "schema_not_initialized" in report.reasons
    with pytest.raises(SchemaCompatibilityError):
        WorkspaceStore(pg_conn, FsObjectStore(tmp_path), schema=schema)
    with pytest.raises(SchemaCompatibilityError):
        startup_schema_handshake(pg_conn, {}, schema)  # default mode is read-only
    assert pg_conn.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0] is None


def test_migrate_is_idempotent_and_installs_every_table(pg_conn, schema):
    first = migrate_schema(pg_conn, schema)
    assert first.applied_versions == (1,) and first.installed_version == CURRENT_SCHEMA_VERSION
    second = migrate_schema(pg_conn, schema)
    assert second.applied_versions == ()
    assert {"workspace", "workspace_member", "knowledge_item", "knowledge_revision", "proposal", "artifact",
            "artifact_version", "relation", "audit_event", "workspace_event", "outbox", "idempotency_ledger",
            "extraction", "schema_version", "schema_migration"} <= tables(pg_conn, schema)
    assert require_compatible_schema(pg_conn, schema).compatible
    assert pg_conn.execute(f'SELECT count(*) FROM "{schema}".schema_version').fetchone()[0] == 1


def test_operator_migrate_startup_mode_is_explicit(pg_conn, schema):
    with pytest.raises(SchemaCompatibilityError):
        startup_schema_handshake(pg_conn, {STARTUP_MODE_ENV: "auto"}, schema)
    report = startup_schema_handshake(pg_conn, {STARTUP_MODE_ENV: OPERATOR_MIGRATE_STARTUP_MODE}, schema)
    assert report.compatible and report.installed_schema_version == 1


def test_newer_drifted_or_ambiguous_schema_is_refused(pg_conn, schema):
    migrate_schema(pg_conn, schema)
    pg_conn.execute(f'UPDATE "{schema}".schema_version SET version = 2')
    report = compatibility(pg_conn, schema)
    assert not report.compatible
    assert "schema_version_ledger_mismatch" in report.reasons
    with pytest.raises(SchemaMigrationError, match="newer"):
        migrate_schema(pg_conn, schema)
    pg_conn.execute(f'UPDATE "{schema}".schema_version SET version = 1')
    pg_conn.execute(f"UPDATE \"{schema}\".schema_migration SET sha256 = 'x'")
    assert "migration_ledger_drift" in compatibility(pg_conn, schema).reasons
    with pytest.raises(SchemaMigrationError, match="drift"):
        migrate_schema(pg_conn, schema)
    pg_conn.execute(f'DELETE FROM "{schema}".schema_version')
    assert "schema_version_ambiguous" in compatibility(pg_conn, schema).reasons


def test_runtime_role_mismatch_is_refused(pg_conn, schema):
    migrate_schema(pg_conn, schema)
    report = compatibility(pg_conn, schema, runtime_role="vuoro_knowledge_runtime")
    assert not report.compatible and report.reasons == ("role_kind_mismatch",)


def test_store_runs_no_ddl(pg_conn, schema, tmp_path, pg_dsn):
    """The runtime path holds no DDL: event triggers would see any CREATE/ALTER/DROP."""
    import psycopg

    from vuoro_workspace import Principal

    migrate_schema(pg_conn, schema)
    pg_conn.execute("CREATE TABLE IF NOT EXISTS public.vw_ddl_log (tag text)")
    pg_conn.execute("TRUNCATE public.vw_ddl_log")
    pg_conn.execute(
        "CREATE OR REPLACE FUNCTION public.vw_log_ddl() RETURNS event_trigger LANGUAGE plpgsql AS "
        "$$ BEGIN INSERT INTO public.vw_ddl_log VALUES (tg_tag); END $$"
    )
    pg_conn.execute("DROP EVENT TRIGGER IF EXISTS vw_ddl")
    pg_conn.execute("CREATE EVENT TRIGGER vw_ddl ON ddl_command_end EXECUTE FUNCTION public.vw_log_ddl()")
    try:
        store = WorkspaceStore(psycopg.connect(pg_dsn, autocommit=True), FsObjectStore(tmp_path), schema=schema)
        admin = Principal("a", {"knowledge:read", "knowledge:contribute", "knowledge:review", "knowledge:admin"})
        ws = store.workspace_create(admin, "w")["workspace_id"]
        store.item_create(admin, ws, "t", "b")
        store.search(admin, ws, "b", scope="working")
        store.close()
        assert pg_conn.execute("SELECT count(*) FROM public.vw_ddl_log").fetchone()[0] == 0
    finally:
        pg_conn.execute("DROP EVENT TRIGGER IF EXISTS vw_ddl")
        pg_conn.execute("DROP TABLE IF EXISTS public.vw_ddl_log")


def test_cli_check_and_migrate(pg_dsn, schema, capsys):
    assert cli_main(["check", "--dsn", pg_dsn, "--schema", schema]) == 2
    assert "schema_not_initialized" in json.loads(capsys.readouterr().out)["reasons"]
    assert cli_main(["migrate", "--dsn", pg_dsn, "--schema", schema]) == 0
    assert json.loads(capsys.readouterr().out)["applied_versions"] == [1]
    assert cli_main(["check", "--dsn", pg_dsn, "--schema", schema]) == 0
    assert json.loads(capsys.readouterr().out)["compatible"] is True
