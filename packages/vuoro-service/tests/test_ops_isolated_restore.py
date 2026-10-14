"""vuoro-ops isolated-restore against disposable PostgreSQL 16 clusters.

A source cluster is built and dumped with ``pg_dump -Fc``; the operation then
restores into its own throwaway cluster.  The compatibility probe is replaced
by one that reads the restored ledger through the DSN the operation hands it,
proving the check runs as the runtime role against the restored copy.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest

from vuoro_service.ops.restore import RestoreRefused, disposable_cluster, dump_roles, isolated_restore, pg_bin_dir

PINS = Path(__file__).resolve().parents[1] / "composition" / "adapter-pins.json"

try:
    BINS = pg_bin_dir()
except RestoreRefused:
    BINS = None
pytestmark = pytest.mark.skipif(BINS is None, reason="PostgreSQL binaries (initdb/pg_ctl/pg_restore) not available")


def psql(cluster, sql, database="postgres"):
    completed = subprocess.run(
        [str(BINS / "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", str(cluster["port"]),
         "-U", cluster["admin"], "-d", database, "-At", "-c", sql], capture_output=True, text=True, check=True)
    return completed.stdout.strip()


@pytest.fixture(scope="module")
def dump(tmp_path_factory):
    path = tmp_path_factory.mktemp("dump") / "vuoro.dump"
    with disposable_cluster(BINS) as source:
        psql(source, "CREATE ROLE work_migration LOGIN; CREATE ROLE work_runtime LOGIN; "
                     "CREATE ROLE audit_migration LOGIN; CREATE ROLE audit_runtime LOGIN")
        psql(source, "CREATE DATABASE src")
        psql(source, "CREATE SCHEMA work AUTHORIZATION work_migration; "
                     "CREATE SCHEMA audit AUTHORIZATION audit_migration; "
                     "SET ROLE work_migration; CREATE TABLE work.schema_version(version int NOT NULL); "
                     "INSERT INTO work.schema_version VALUES (25); RESET ROLE; "
                     "SET ROLE audit_migration; CREATE TABLE audit.schema_migration(version int NOT NULL); "
                     "INSERT INTO audit.schema_migration VALUES (3); RESET ROLE; "
                     "GRANT USAGE ON SCHEMA work TO work_runtime; GRANT SELECT ON work.schema_version TO work_runtime; "
                     "GRANT USAGE ON SCHEMA audit TO audit_runtime; "
                     "GRANT SELECT ON audit.schema_migration TO audit_runtime", "src")
        subprocess.run([str(BINS / "pg_dump"), "-Fc", "-h", "127.0.0.1", "-p", str(source["port"]),
                        "-U", source["admin"], "-d", "src", "-f", str(path)], check=True)
        source_port = source["port"]
    return path, source_port


def reading_probe(script, env):
    """Stand-in for the pinned owners: read each ledger as the handed DSN's role."""
    assert "compatibility_handshake" in script
    domains = {}
    for domain, table in (("work", "schema_version"), ("audit", "schema_migration")):
        dsn = env[f"VUORO_OPS_{domain.upper()}_DSN"]
        assert urlsplit(dsn).hostname == "127.0.0.1"
        completed = subprocess.run([str(BINS / "psql"), "-X", "-At", dsn, "-c",
                                    f"SELECT current_user || ':' || max(version) FROM {table}"],
                                   capture_output=True, text=True)
        role, _, version = completed.stdout.strip().partition(":")
        domains[domain] = {"compatible": version == {"work": "25", "audit": "3"}[domain],
                           "reason": None if completed.returncode == 0 else completed.stderr.strip()[-120:],
                           "handshake": {"remote_schema": {"actual": int(version) if version else None},
                                         "role": role}}
    return {"pins_satisfied": True, "installed": json.loads(env["VUORO_OPS_PINS"]), "domains": domains}


def test_dump_roles_are_extracted_from_ownership_and_grants():
    sql = ('ALTER SCHEMA work OWNER TO work_migration;\nGRANT USAGE ON SCHEMA work TO work_runtime;\n'
           'GRANT SELECT ON TABLE x TO "odd""role", PUBLIC;\nALTER DEFAULT PRIVILEGES FOR ROLE m IN SCHEMA s GRANT SELECT ON TABLES TO r;\n')
    assert dump_roles(sql) == ['m', 'odd"role', 'r', 'work_migration', 'work_runtime']


def test_restore_runs_the_pinned_check_as_runtime_roles_on_a_throwaway_copy(dump):
    path, source_port = dump
    seen = {}

    def probe(script, env):
        seen.update(env)
        return reading_probe(script, env)

    result = isolated_restore(path, manifest_path=PINS, probe_runner=probe, bin_dir=BINS)
    body = result.result
    assert result.status == "ok", body
    assert body["restore"]["pg_restore_exit"] == 0
    assert body["restore"]["restored_schemas"] == ["audit", "public", "work"]
    assert set(body["restore"]["roles_created"]) >= {"work_runtime", "work_migration", "audit_runtime"}
    assert body["compatibility"]["roles"] == {"work": "work_runtime", "audit": "audit_runtime"}
    assert body["compatibility"]["domains"]["work"]["schema"] == {"actual": 25}
    assert body["live_database_touched"] is False and body["restore"]["cluster_torn_down"] is True
    restore_port = urlsplit(seen["VUORO_OPS_WORK_DSN"]).port
    assert restore_port != source_port
    pins = json.loads(seen["VUORO_OPS_PINS"])
    assert pins["sprintctl"] and pins["auditctl"]
    assert {"kind": "dump", "id": "sha256:" + body["restore"]["dump_sha256"]} in result.work_refs


def test_incompatible_restored_state_is_attention(dump):
    path, _ = dump

    def probe(script, env):
        observation = reading_probe(script, env)
        observation["domains"]["work"]["compatible"] = False
        return observation

    result = isolated_restore(path, manifest_path=PINS, probe_runner=probe, bin_dir=BINS)
    assert result.status == "attention" and result.result["compatibility"]["state"] == "incompatible"


def test_without_pinned_owners_the_check_is_an_unknown(dump, monkeypatch):
    path, _ = dump
    monkeypatch.setattr("vuoro_service.ops.restore._installed_satisfies", lambda pins: False)
    result = isolated_restore(path, manifest_path=PINS, bin_dir=BINS)
    assert result.status == "error"
    assert "pinned-owners-not-installed-here:pass-owner-image" in result.unknowns
    assert result.result["restore"]["pg_restore_exit"] == 0


@pytest.mark.parametrize(("content", "message"), [
    (b"age-encryption.org/v1\n-> X25519 abc\n", "age-encrypted"),
    (b"--\n-- PostgreSQL database dump\n", "custom-format"),
])
def test_non_custom_or_encrypted_dumps_are_refused(tmp_path, content, message):
    path = tmp_path / "dump"
    path.write_bytes(content)
    result = isolated_restore(path, manifest_path=PINS, bin_dir=BINS)
    assert result.status == "error" and message in result.result["refused"]
