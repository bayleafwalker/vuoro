"""Prove the pinned composition starts safely against the live pre-migration state.

Item #2092. Exercises a candidate image against a disposable PostgreSQL 16
instance (production runs CNPG major 16) held at the exact observed production
state -- work ledger schema 5 with staged maintenance storage, execution ledger
prefix 1..3 -- and then across the migration boundary to schema 6.

States proven:

  1. work 5 + maintenance extension, execution 3  -> starts and serves
  2. the same state after restart                 -> starts again, no DDL
  3. work 6 + execution 6, marker SELECT granted  -> starts and serves
  4. work 6 without marker SELECT                 -> deterministic fail-closed

State 4 is the one the cutover must not discover in production: structural
verification is privilege-independent as of sprintctl 0.2.16, but the capability
marker is data and still requires SELECT, so the v6 migration has to grant it.

All schema construction runs *inside the candidate image*, using the pinned
wheels it actually ships, rather than this repository's working tree.

Usage: python scripts/verify_pre_migration_startup.py IMAGE [--json OUT] [--keep]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

PG_IMAGE = "docker.io/library/postgres:16"
ADMIN = "postgres"
PASSWORD = "disposable-proof-only"
DB = "vuoro_proof"
DOMAINS = ("work", "execution", "knowledge", "audit")
HOST_PORT = 55440
SERVICE_PORT = 8080


def run(*argv: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        argv, check=False, text=True, capture_output=True, input=stdin, timeout=900
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"command failed ({result.returncode}): {' '.join(argv)}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return result


def psql(container: str, sql: str) -> str:
    return run(
        "podman", "exec", "-e", f"PGPASSWORD={PASSWORD}", container,
        "psql", "-v", "ON_ERROR_STOP=1", "-tA", "-U", ADMIN, "-d", DB, "-c", sql,
    ).stdout.strip()


def dsn(role: str) -> str:
    return f"postgresql://{role}:{PASSWORD}@127.0.0.1:{HOST_PORT}/{DB}"


def in_image(image: str, script: str, **env: str) -> str:
    # -i is load-bearing: without it stdin is not attached and `python -` reads
    # an empty program, exits 0, and every construction step silently no-ops.
    argv = ["podman", "run", "--rm", "-i", "--network", "host", "--entrypoint", "python"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    argv += [image, "-"]
    return run(*argv, stdin=script).stdout


def http_get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except Exception as error:  # noqa: BLE001 - refused while starting
        return 0, str(error)


# --------------------------------------------------------------------------
# schema construction, executed with the image's own pinned wheels
# --------------------------------------------------------------------------

WORK_V5 = """
import os, psycopg
from psycopg.rows import dict_row
from sprintctl import pg as spg
conn = psycopg.connect(os.environ["URL"], row_factory=dict_row)
conn.autocommit = False
with conn.cursor() as cur:
    cur.execute("SET search_path TO work")
    cur.execute(spg.PG_DDL)
    for version in (2, 3, 4, 5):
        getattr(spg, f"_apply_schema_version_{version}")(cur)
    cur.execute("UPDATE schema_version SET version = %s", (5,))
conn.commit()
with conn.cursor() as cur:
    cur.execute("SET search_path TO work")
    cur.execute("SELECT max(version) AS v FROM schema_version")
    print(cur.fetchone()["v"])
"""

EXECUTION_V3 = """
import os, psycopg
from actionq import schema as contract
conn = psycopg.connect(os.environ["URL"], autocommit=True)
schema = os.environ["SCHEMA"]
conn.execute(f'CREATE TABLE IF NOT EXISTS "{schema}".schema_migrations ('
             ' domain TEXT NOT NULL, version INTEGER NOT NULL CHECK (version > 0),'
             ' name TEXT NOT NULL, checksum TEXT NOT NULL,'
             ' applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),'
             ' PRIMARY KEY (domain, version))')
for migration in contract.load_migrations()[:3]:
    for statement in contract._statements(contract._render(migration, schema)):
        conn.execute(statement)
    conn.execute(f'INSERT INTO "{schema}".schema_migrations (domain, version, name, checksum)'
                 ' VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                 (contract.DOMAIN, migration.version, migration.name, migration.checksum))
print(conn.execute(
    f'SELECT max(version) FROM "{schema}".schema_migrations').fetchone()[0])
"""

EXECUTION_V6 = """
import os, psycopg
from actionq import db
conn = psycopg.connect(os.environ["URL"], autocommit=True)
print(db.migrate(conn, os.environ["SCHEMA"])["applied_versions"])
"""

KNOWLEDGE_SCHEMA = """
import os, psycopg
from kctl import central_schema
conn = psycopg.connect(os.environ["URL"], autocommit=True)
central_schema.migrate(conn, schema=os.environ["SCHEMA"],
                       migration_role="knowledge_migration",
                       runtime_role="knowledge_runtime",
                       environment_name="vuoro-2092-proof",
                       environment_class="development")
print("knowledge-ok")
"""

AUDIT_SCHEMA = """
import os, psycopg
from auditctl import central_schema
conn = psycopg.connect(os.environ["URL"], autocommit=True)
central_schema.migrate(conn, schema=os.environ["SCHEMA"],
                       migration_role="audit_migration",
                       runtime_role="audit_runtime")
print("audit-ok")
"""

WORK_V6 = """
import os, psycopg
from psycopg.rows import dict_row
from sprintctl import pg as spg
from sprintctl import pg_migrations
store = spg.PgStore(conn=psycopg.connect(os.environ["URL"], row_factory=dict_row),
                    repo_id="agentops")
store.conn.execute("SET search_path TO work")
result = pg_migrations.migrate_schema(store)
store.conn.commit()
print(result["applied_versions"])
"""

STAGE_BRIDGE = """
import os, psycopg
from psycopg.rows import dict_row
from sprintctl import pg as spg
from sprintctl import pg_migrations
store = spg.PgStore(conn=psycopg.connect(os.environ["URL"], row_factory=dict_row),
                    repo_id="agentops")
store.conn.execute("SET search_path TO work")
result = pg_migrations.stage_schema5_maintenance_bridge(store)
store.conn.commit()
print(result["installed"])
"""


class Proof:
    def __init__(self, image: str, keep: bool):
        self.image = image
        self.keep = keep
        self.suffix = uuid.uuid4().hex[:8]
        self.pg_name = f"vuoro-proof-pg-{self.suffix}"
        self.service_name = f"vuoro-proof-svc-{self.suffix}"
        self.identities = Path(f"/tmp/vuoro-proof-identities-{self.suffix}.json")
        self.results: list[dict] = []

    # -- infrastructure ----------------------------------------------------

    def start_postgres(self) -> None:
        run("podman", "run", "-d", "--name", self.pg_name,
            "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", f"POSTGRES_USER={ADMIN}",
            "-e", f"POSTGRES_DB={DB}", "-p", f"{HOST_PORT}:5432", PG_IMAGE)
        for _ in range(120):
            if run("podman", "exec", self.pg_name, "pg_isready", "-U", ADMIN, "-d", DB,
                   check=False).returncode == 0:
                time.sleep(2)
                return
            time.sleep(1)
        raise SystemExit("disposable PostgreSQL did not become ready")

    def provision_roles(self) -> None:
        statements = []
        for domain in DOMAINS:
            statements += [
                f"CREATE ROLE {domain}_migration LOGIN PASSWORD '{PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;",
                f"CREATE ROLE {domain}_runtime LOGIN PASSWORD '{PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;",
                f"CREATE SCHEMA {domain} AUTHORIZATION {domain}_migration;",
                f"GRANT USAGE ON SCHEMA {domain} TO {domain}_runtime;",
                f"ALTER ROLE {domain}_migration SET search_path TO {domain};",
                f"ALTER ROLE {domain}_runtime SET search_path TO {domain};",
                # Migration roles own schema creation; runtime roles never get
                # this, which is what state 4 relies on.
                f"GRANT CREATE ON DATABASE {DB} TO {domain}_migration;",
            ]
        psql(self.pg_name, " ".join(statements))

    # Migration ledgers a runtime principal must never be able to mutate. Each
    # adapter refuses to serve if its runtime role can write these, so granting
    # blanket DML and stopping there makes every domain report incompatible.
    LEDGER_TABLES = {
        "work": ("schema_version", "sprintctl_schema_capability"),
        "execution": ("schema_migrations",),
        "knowledge": ("schema_migration", "schema_principal"),
        "audit": ("schema_migration", "schema_principal"),
    }

    def grant_runtime(self, domain: str) -> None:
        psql(self.pg_name,
             f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {domain} "
             f"TO {domain}_runtime; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "
             f"{domain} TO {domain}_runtime;")
        for table in self.LEDGER_TABLES[domain]:
            exists = psql(self.pg_name,
                          f"SELECT to_regclass('{domain}.{table}') IS NOT NULL;")
            if exists == "t":
                psql(self.pg_name,
                     f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {domain}.{table} "
                     f"FROM {domain}_runtime;")

    def write_identities(self) -> None:
        self.identities.write_text(json.dumps({
            "schema_version": "vuoro-identities/v1",
            "identities": {secrets.token_hex(32): {
                "actor": "proof", "environment": "vuoro-2092-proof",
                "authorities": ["work:read"], "repo_ids": ["agentops"]}},
        }), encoding="utf-8")
        self.identities.chmod(0o644)

    # -- service -----------------------------------------------------------

    def service_env(self) -> dict[str, str]:
        env = {
            "VUORO_ENVIRONMENT_NAME": "vuoro-2092-proof",
            "VUORO_ENVIRONMENT_CLASS": "development",
            "VUORO_IDENTITIES_FILE": "/run/vuoro/identities.json",
            "VUORO_WORK_REPOSITORY_ID": "agentops",
        }
        for domain in DOMAINS:
            env[f"VUORO_{domain.upper()}_RUNTIME_DSN"] = dsn(f"{domain}_runtime")
            if domain != "work":
                env[f"VUORO_{domain.upper()}_SCHEMA"] = domain
        return env

    def start_service(self) -> None:
        run("podman", "rm", "-f", self.service_name, check=False)
        argv = ["podman", "run", "-d", "--name", self.service_name, "--network", "host",
                "-v", f"{self.identities}:/run/vuoro/identities.json:ro"]
        for key, value in self.service_env().items():
            argv += ["-e", f"{key}={value}"]
        argv.append(self.image)
        run(*argv)

    def service_logs(self) -> str:
        probe = run("podman", "logs", self.service_name, check=False)
        return (probe.stdout + probe.stderr).strip()

    def await_ready(self, seconds: int = 60) -> tuple[bool, str]:
        deadline = time.time() + seconds
        while time.time() < deadline:
            status, body = http_get(f"http://127.0.0.1:{SERVICE_PORT}/health/ready")
            if status == 200:
                return True, body
            running = run("podman", "inspect", "-f", "{{.State.Running}}",
                          self.service_name, check=False).stdout.strip()
            if running != "true":
                return False, self.service_logs()
            time.sleep(1)
        return False, self.service_logs()

    def stop_service(self) -> None:
        run("podman", "rm", "-f", self.service_name, check=False)

    # -- observations ------------------------------------------------------

    def work_state(self) -> dict[str, str]:
        return {
            "schema_version": psql(self.pg_name,
                                   "SELECT max(version) FROM work.schema_version;"),
            "relations": psql(self.pg_name,
                              "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                              "ON n.oid=c.relnamespace WHERE n.nspname='work' "
                              "AND c.relkind='r';"),
            "catalog_md5": psql(self.pg_name,
                                "SELECT md5(string_agg(c.relname||':'||a.attname, ',' "
                                "ORDER BY c.relname, a.attnum)) FROM pg_attribute a "
                                "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n "
                                "ON n.oid=c.relnamespace WHERE n.nspname='work' "
                                "AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped;"),
            "execution_version": psql(self.pg_name,
                                      "SELECT max(version) FROM execution.schema_migrations;"),
        }

    def record(self, state: str, ok: bool, detail: dict) -> None:
        self.results.append({"state": state, "ok": ok, **detail})
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {state}: {json.dumps(detail)[:400]}", flush=True)

    # -- the four states ---------------------------------------------------

    def run_proof(self) -> bool:
        self.start_postgres()
        self.provision_roles()
        self.write_identities()

        work_version = in_image(self.image, WORK_V5, URL=dsn("work_migration")).strip()
        print(f"[build] work ledger -> {work_version!r}", flush=True)
        staged = in_image(self.image, STAGE_BRIDGE, URL=dsn("work_migration")).strip()
        print(f"[build] maintenance bridge staged -> {staged!r}", flush=True)
        execution_version = in_image(self.image, EXECUTION_V3,
                                     URL=dsn("execution_migration"),
                                     SCHEMA="execution").strip()
        print(f"[build] execution ledger -> {execution_version!r}", flush=True)
        in_image(self.image, KNOWLEDGE_SCHEMA, URL=dsn("knowledge_migration"),
                 SCHEMA="knowledge")
        in_image(self.image, AUDIT_SCHEMA, URL=dsn("audit_migration"), SCHEMA="audit")
        for domain in DOMAINS:
            self.grant_runtime(domain)

        before = self.work_state()
        assert work_version == "5", f"expected work ledger 5, got {work_version!r}"
        assert execution_version == "3", f"expected execution 3, got {execution_version!r}"

        # State 1 -- pre-migration startup
        self.start_service()
        ready, body = self.await_ready()
        after = self.work_state()
        self.record("1-work5-extension-execution3", ready and before == after, {
            "work_schema": before["schema_version"],
            "execution_schema": before["execution_version"],
            "maintenance_staged": staged,
            "no_ddl": before == after,
            "detail": body[:300],
        })
        state1 = ready and before == after

        # State 2 -- restart continuity
        self.stop_service()
        self.start_service()
        ready2, body2 = self.await_ready()
        after2 = self.work_state()
        self.record("2-restart-continuity", ready2 and before == after2, {
            "no_ddl": before == after2,
            "work_schema": after2["schema_version"],
            "detail": body2[:300],
        })
        state2 = ready2 and before == after2
        self.stop_service()

        # Migrate across the boundary.
        applied_work = in_image(self.image, WORK_V6, URL=dsn("work_migration")).strip()
        applied_execution = in_image(self.image, EXECUTION_V6,
                                     URL=dsn("execution_migration"),
                                     SCHEMA="execution").strip()
        for domain in DOMAINS:
            self.grant_runtime(domain)

        # State 4 first: v6 with the marker deliberately unreadable.
        psql(self.pg_name,
             "REVOKE SELECT ON work.sprintctl_schema_capability FROM work_runtime;")
        self.start_service()
        ready4, body4 = self.await_ready(seconds=40)
        logs4 = self.service_logs()
        failed_closed = (not ready4) and (
            "sprintctl_schema_capability" in logs4 or "permission denied" in logs4
            or "compatibility" in logs4.lower()
        )
        self.record("4-schema6-without-marker-grant-fails-closed", failed_closed, {
            "ready": ready4,
            "evidence": logs4[-500:],
        })
        self.stop_service()

        # State 3: v6 with the grant the migration must apply.
        psql(self.pg_name,
             "GRANT SELECT ON work.sprintctl_schema_capability TO work_runtime;")
        self.start_service()
        ready3, body3 = self.await_ready()
        final = self.work_state()
        self.record("3-schema6-with-marker-grant", ready3, {
            "work_schema": final["schema_version"],
            "execution_schema": final["execution_version"],
            "applied_work": applied_work,
            "applied_execution": applied_execution,
            "detail": body3[:300],
        })
        self.stop_service()

        return all([state1, state2, ready3, failed_closed])

    def cleanup(self) -> None:
        if self.keep:
            return
        run("podman", "rm", "-f", self.service_name, check=False)
        run("podman", "rm", "-f", self.pg_name, check=False)
        self.identities.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--json", dest="output")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    proof = Proof(args.image, args.keep)
    try:
        ok = proof.run_proof()
    finally:
        payload = {
            "schema_version": "vuoro-pre-migration-startup-proof/v1",
            "image": args.image,
            "states": proof.results,
        }
        if args.output:
            Path(args.output).write_text(json.dumps(payload, indent=2) + "\n",
                                         encoding="utf-8")
        proof.cleanup()
    print("PROOF PASSED" if ok else "PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
