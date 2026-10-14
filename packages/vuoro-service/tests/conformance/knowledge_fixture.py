"""Provision the knowledge domain for the released-composition upgrade proofs.

The composition requires the knowledge workspace domain. These proofs
prepare it the way a deployment does:
- the released owner's operator entrypoint, ``vuoro-workspace migrate``, runs
  as a separate migration role;
- that role grants runtime DML to a distinct runtime role;
- composition is then given the runtime DSN, the expected runtime role and a
  filesystem object store.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg import sql

SCHEMA = "knowledge"


def validate_knowledge_urls(migration: str, runtime: str, database_prefix: str) -> None:
    """Loopback, disposable database, distinct roles, and no libpq routing overrides."""
    roles = set()
    for url in (migration, runtime):
        parsed = urlsplit(url)
        assert parsed.scheme in ("postgresql", "postgres")
        assert parsed.hostname in ("127.0.0.1", "localhost", "::1")
        assert parsed.port is not None and not parsed.fragment
        assert parsed.path.startswith("/" + database_prefix)
        assert parsed.username and parsed.password
        assert parse_qs(parsed.query, strict_parsing=False) == {}
        normalized = psycopg.conninfo.conninfo_to_dict(url)
        assert normalized["user"] == parsed.username
        roles.add(parsed.username)
    assert len(roles) == 2


def provision_knowledge(migration: str, runtime: str, objects: Path) -> dict[str, str]:
    """Run the owner's migrate entrypoint, grant runtime DML, return composition env."""
    owner_cli = Path(sys.executable).parent / "vuoro-workspace"
    assert owner_cli.is_file(), "the composition must install the released vuoro-workspace CLI"
    env = {k: v for k, v in os.environ.items() if k != "VUORO_WORKSPACE_DSN"}
    migrated = subprocess.run([str(owner_cli), "migrate", "--dsn", migration, "--schema", SCHEMA],
                              env=env, capture_output=True, text=True)
    assert migrated.returncode == 0, "vuoro-workspace migrate refused"
    assert json.loads(migrated.stdout)["installed_version"] >= 1
    runtime_role = urlsplit(runtime).username
    with psycopg.connect(migration, autocommit=True) as conn:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(SCHEMA), sql.Identifier(runtime_role)))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
            sql.Identifier(SCHEMA), sql.Identifier(runtime_role)))
    with psycopg.connect(runtime) as conn:
        assert not any(conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                                    "FROM pg_roles WHERE rolname=current_user").fetchone())
        assert not conn.execute("SELECT has_schema_privilege(current_user,%s,'CREATE')", (SCHEMA,)).fetchone()[0]
    checked = subprocess.run([str(owner_cli), "check", "--dsn", runtime, "--schema", SCHEMA,
                              "--runtime-role", runtime_role], env=env, capture_output=True, text=True)
    assert checked.returncode == 0, "knowledge runtime role does not pass the owner's compatibility check"
    objects.mkdir(parents=True, exist_ok=True)
    return {
        "VUORO_KNOWLEDGE_RUNTIME_DSN": runtime,
        "VUORO_KNOWLEDGE_RUNTIME_ROLE": runtime_role,
        "VUORO_KNOWLEDGE_OBJECT_STORE": f"fs:{objects}",
    }
