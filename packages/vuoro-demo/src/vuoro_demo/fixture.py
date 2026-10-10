"""Owned PostgreSQL cluster and loopback evaluation shell; never a live DSN."""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading
import time


def command(arguments: list[str], root: Path):
    return subprocess.run(arguments, cwd=root, check=True, capture_output=True,
        text=True, timeout=30, env={"PATH": os.environ["PATH"], "HOME": str(root),
            "LANG": "C.UTF-8"})


@contextmanager
def fixture(root: Path, pg_bin: Path | None, *, state: dict):
    from sprintctl import pg
    from sprintctl.application import WorkApplication
    from sprintctl.vuoro_adapter import register_work_catalog
    from vuoro_service.app import ServiceSettings, create_app
    from vuoro_service.catalog import CatalogRegistry
    from vuoro_service.identity import Identity, StaticBearerIdentityResolver
    import psycopg
    import uvicorn
    from .scenario import ENVIRONMENT, REPO

    state["cleanup"] = "not-started"
    tools = {}
    for name in ("initdb", "pg_ctl"):
        path = str(pg_bin / name) if pg_bin else shutil.which(name)
        if not path or not Path(path).is_file():
            raise ValueError("PostgreSQL 16 tools required; use --pg-bin; external DSNs are unsupported")
        tools[name] = path
    version = command([tools["initdb"], "--version"], root).stdout
    if " 16." not in version:
        raise ValueError("This qualified demo requires PostgreSQL 16")
    data, sock = root / "postgres", root / "socket"
    sock.mkdir(mode=0o700)
    command([tools["initdb"], "-D", str(data), "-U", "demo_migration",
        "--auth-local=trust", "--auth-host=reject", "--no-locale", "-E", "UTF8"], root)
    started = False
    store = None
    server = None
    thread = None
    listener = None
    previous_ttl = os.environ.get("SPRINTCTL_LEASE_TTL_SECONDS")
    try:
        state["cleanup"] = "unknown"
        started = True  # Even a failed start may leave an owned process needing cleanup.
        command([tools["pg_ctl"], "-D", str(data), "-l", str(root / "postgres.log"),
            "-o", f"-k {sock} -c listen_addresses=''", "-w", "start"], root)
        migration = f"dbname=postgres user=demo_migration host={sock}"
        with psycopg.connect(migration, autocommit=True) as connection:
            connection.execute("CREATE ROLE demo_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
            connection.execute("CREATE DATABASE demo_disposable OWNER demo_migration")
            connection.execute("COMMENT ON DATABASE demo_disposable IS 'vuoro:owned-disposable-demo'")
        store = pg.get_connection(f"dbname=demo_disposable user=demo_migration host={sock}")
        store.repo_id = REPO
        pg.init_db(store)
        with store.conn.cursor() as cursor:
            cursor.execute("GRANT USAGE ON SCHEMA public TO demo_runtime")
            cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC, demo_runtime")
            cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO demo_runtime")
            cursor.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO demo_runtime")
            cursor.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO demo_runtime")
        store.conn.commit()
        store.conn.close()
        store = pg.get_connection(f"dbname=demo_disposable user=demo_runtime host={sock}")
        store.repo_id = REPO
        try:
            with store.conn.transaction():
                store.conn.execute("CREATE TABLE forbidden_runtime_ddl (value integer)")
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("runtime unexpectedly has DDL authority")
        registry = CatalogRegistry()
        register_work_catalog(registry, WorkApplication.postgres(store))
        authorities = frozenset({"work:read", "work:write", "work:claim", "work:evidence", "work:batch",
            "work:sprint", "work:lifecycle"})
        tokens = {name: secrets.token_urlsafe(32) for name in ("A", "B", "verifier")}
        identities = {token: Identity(actor="demo-" + name, environment=ENVIRONMENT,
            authorities=authorities | ({"work.effect.accept", "work.effect.get"}
                if name == "verifier" else {"work.effect.propose", "work.effect.get"}),
            repo_ids=frozenset({REPO}), workspace_id="demo",
            principal_id="demo:" + name + ":0") for name, token in tokens.items()}
        app = create_app(settings=ServiceSettings(environment_name=ENVIRONMENT,
            environment_class="development", compatibility_state="compatible"),
            registry=registry, identity_resolver=StaticBearerIdentityResolver(identities))
        # Prebind the owned socket, avoiding a free-port race or nonloopback listener.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        endpoint = "http://127.0.0.1:" + str(listener.getsockname()[1])
        os.environ["SPRINTCTL_LEASE_TTL_SECONDS"] = "30"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("loopback shell failed to start")
            time.sleep(.05)
        yield endpoint, tokens
    finally:
        errors = []
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=15)
            if thread.is_alive():
                errors.append("shell")
        if listener:
            listener.close()
        if store:
            try:
                store.conn.close()
            except Exception:
                errors.append("connection")
        if started:
            try:
                command([tools["pg_ctl"], "-D", str(data), "-m", "immediate", "-w", "stop"], root)
            except Exception:
                errors.append("postgres")
        if previous_ttl is None:
            os.environ.pop("SPRINTCTL_LEASE_TTL_SECONDS", None)
        else:
            os.environ["SPRINTCTL_LEASE_TTL_SECONDS"] = previous_ttl
        if errors:
            state["cleanup"] = "failed"
            raise RuntimeError("owned fixture cleanup incomplete: " + ",".join(errors))
        state["cleanup"] = "complete"
