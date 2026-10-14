"""vuoro-ops isolated-restore against disposable PostgreSQL 16 clusters.

A source cluster is built and dumped with ``pg_dump -Fc``; the operation then
restores into its own throwaway cluster.  The compatibility probe is replaced
by one that reads the restored ledger through the DSN the operation hands it,
proving the check runs as the runtime role against the restored copy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
from urllib.parse import urlsplit

import pytest

from vuoro_service.ops.restore import RestoreRefused, disposable_cluster, dump_roles, isolated_restore, pg_bin_dir

PINS = Path(__file__).resolve().parents[1] / "composition" / "adapter-pins.json"

try:
    BINS = pg_bin_dir()
except RestoreRefused:
    BINS = None
    if os.environ.get("VUORO_OPS_REQUIRE_PG16") == "1":
        raise
pytestmark = pytest.mark.skipif(BINS is None, reason="PostgreSQL 16 binaries (initdb/pg_ctl/pg_restore) not available")


def psql(cluster, sql, database="postgres"):
    completed = subprocess.run(
        [str(BINS / "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", cluster["socket_dir"], "-p", str(cluster["port"]),
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
        subprocess.run([str(BINS / "pg_dump"), "-Fc", "-h", source["socket_dir"], "-p", str(source["port"]),
                        "-U", source["admin"], "-d", "src", "-f", str(path)], check=True)
        source_socket = source["socket_dir"]
    return path, source_socket


def reading_probe(script, env):
    """Stand-in for the pinned owners: read each ledger as the handed DSN's role."""
    assert "compatibility_handshake" in script
    domains = {}
    for domain, table in (("work", "schema_version"), ("audit", "schema_migration")):
        dsn = env[f"VUORO_OPS_{domain.upper()}_DSN"]
        assert urlsplit(dsn).hostname is None and "host=%2F" in dsn  # Unix socket only, never TCP
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
    path, source_socket = dump
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
    assert source_socket.replace("/", "%2F") not in seen["VUORO_OPS_WORK_DSN"]
    assert body["restore"]["restored_as"] == "vuoro_ops_restorer" and body["restore"]["restorer_superuser"] is False
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


def test_cluster_is_socket_only_in_a_private_directory():
    with disposable_cluster(BINS) as cluster:
        assert stat.S_IMODE(os.stat(cluster["socket_dir"]).st_mode) == 0o700
        assert psql(cluster, "SHOW listen_addresses") == ""
        assert psql(cluster, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user") == "t"
    assert not os.path.exists(cluster["socket_dir"]) and not os.path.exists(cluster["root"])


def test_missing_runtime_role_never_falls_back_to_a_superuser(dump):
    path, _ = dump
    seen = {}

    def probe(script, env):
        seen.update(env)
        observation = reading_probe(script, {**env, "VUORO_OPS_AUDIT_DSN": env["VUORO_OPS_WORK_DSN"]})
        observation["domains"].pop("audit")
        return observation

    result = isolated_restore(path, manifest_path=PINS, probe_runner=probe, bin_dir=BINS,
                              roles={"audit": "audit_reader_missing"})
    assert "VUORO_OPS_AUDIT_DSN" not in seen
    assert result.result["compatibility"]["roles"]["audit"] is None
    assert "runtime-role-absent-from-dump:audit:audit_reader_missing" in result.unknowns
    assert result.status == "attention"


def test_superuser_only_statements_cannot_run_during_restore(tmp_path):
    """Superuser-only DDL in a dump fails the restore: the restorer is not a superuser.

    An event trigger stands in for the class (COPY ... TO PROGRAM, untrusted
    languages): all need superuser, so none executes on this host.
    """
    path = tmp_path / "hostile.dump"
    with disposable_cluster(BINS) as source:
        psql(source, "CREATE ROLE app LOGIN SUPERUSER")  # a superuser-owned source; never on restore
        psql(source, "CREATE DATABASE src OWNER app")
        psql(source, "SET ROLE app; CREATE FUNCTION et() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN END $$; "
                     "RESET ROLE; CREATE EVENT TRIGGER e ON ddl_command_start EXECUTE FUNCTION et(); "
                     "ALTER EVENT TRIGGER e OWNER TO app", "src")
        subprocess.run([str(BINS / "pg_dump"), "-Fc", "-h", source["socket_dir"], "-p", str(source["port"]),
                        "-U", source["admin"], "-d", "src", "-f", str(path)], check=True)
    result = isolated_restore(path, manifest_path=PINS, probe_runner=reading_probe, bin_dir=BINS)
    assert result.status == "error" and result.result["restore"]["pg_restore_exit"] != 0
    assert "event trigger" in result.result["restore"]["pg_restore_error"]


def test_runtime_roles_never_receive_create_grants(dump, tmp_path):
    path, _ = dump
    result = isolated_restore(path, manifest_path=PINS, probe_runner=reading_probe, bin_dir=BINS)
    restore = result.result["restore"]
    assert set(restore["create_withheld_from_runtime_roles"]) == {"work_runtime", "audit_runtime"}
    assert not set(restore["public_schema_create_granted_to_object_owners"]) & {"work_runtime", "audit_runtime"}

    hostile = tmp_path / "runtime-owner.dump"
    with disposable_cluster(BINS) as source:
        psql(source, "CREATE ROLE work_runtime LOGIN; CREATE ROLE audit_runtime LOGIN")
        psql(source, "CREATE DATABASE src")
        psql(source, "CREATE TABLE public.t(x int); ALTER TABLE public.t OWNER TO work_runtime", "src")
        subprocess.run([str(BINS / "pg_dump"), "-Fc", "-h", source["socket_dir"], "-p", str(source["port"]),
                        "-U", source["admin"], "-d", "src", "-f", str(hostile)], check=True)
    refused = isolated_restore(hostile, manifest_path=PINS, bin_dir=BINS,
                               probe_runner=lambda script, env: {"pins_satisfied": True, "domains": {}})
    assert refused.status == "error" and refused.result["restore"]["pg_restore_exit"] != 0
    assert refused.result["restore"]["public_schema_create_granted_to_object_owners"] == []
    assert "permission denied for schema public" in refused.result["restore"]["pg_restore_error"]


def test_a_non_16_bin_dir_is_refused(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    for name in ("initdb", "pg_restore"):
        (fake / name).write_text("#!/bin/sh\necho 'initdb (PostgreSQL) 15.4'\n")
        (fake / name).chmod(0o755)
    with pytest.raises(RestoreRefused):
        pg_bin_dir(fake)


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
