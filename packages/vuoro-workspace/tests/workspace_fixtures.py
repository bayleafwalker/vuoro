"""Disposable PostgreSQL for the workspace store tests.

Set ``VUORO_WORKSPACE_TEST_PG_BIN`` to a PostgreSQL bin directory (initdb,
pg_ctl): a throwaway cluster is created on a private Unix socket and removed
afterwards, as the demo package does. Alternatively set
``VUORO_WORKSPACE_TEST_DSN`` to a disposable server you own (for example a
``postgres:16`` container). Without either, database tests are skipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

PG_BIN_ENV = "VUORO_WORKSPACE_TEST_PG_BIN"
DSN_ENV = "VUORO_WORKSPACE_TEST_DSN"


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    if os.environ.get(DSN_ENV):
        yield os.environ[DSN_ENV]
        return
    pg_bin = os.environ.get(PG_BIN_ENV)
    if not pg_bin:
        pytest.skip(f"set {PG_BIN_ENV} (PostgreSQL bin dir) or {DSN_ENV} for disposable PostgreSQL tests")
    bin_dir = Path(pg_bin)
    base = Path(tempfile.mkdtemp(prefix="vwpg-", dir="/tmp" if Path("/tmp").is_dir() else None))
    data, sock = base / "data", base / "s"
    sock.mkdir()
    subprocess.run([str(bin_dir / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust", "--no-sync",
                    "-E", "UTF8", "--locale=C"], check=True, capture_output=True)
    subprocess.run([str(bin_dir / "pg_ctl"), "-D", str(data), "-l", str(base / "log"), "-w", "-o",
                    f"-k {sock} -c listen_addresses='' -c fsync=off -c max_connections=50", "start"],
                   check=True, capture_output=True)
    try:
        yield f"host={sock} user=postgres dbname=postgres"
    finally:
        subprocess.run([str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def pg_conn(pg_dsn: str) -> Iterator[Any]:
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def schema(pg_conn: Any) -> Iterator[str]:
    name = f"kw_{uuid.uuid4().hex[:12]}"
    yield name
    pg_conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def env(pg_dsn: str, pg_conn: Any, schema: str, clock: Clock, tmp_path: Path) -> Iterator[Any]:
    """A migrated schema, a store, an fs object store and principals for each role."""
    import psycopg
    from vuoro_workspace import FsObjectStore, Principal, WorkspaceStore, migrate_schema

    migrate_schema(pg_conn, schema)
    objects = FsObjectStore(tmp_path / "objects")
    store = WorkspaceStore(psycopg.connect(pg_dsn, autocommit=True), objects, schema=schema, clock=clock)
    every = {"knowledge:read", "knowledge:contribute", "knowledge:review", "knowledge:admin"}

    class Env:
        pass

    e = Env()
    e.store, e.objects, e.clock, e.schema, e.tmp = store, objects, clock, schema, tmp_path
    e.admin = Principal("admin", every)
    e.ws = store.workspace_create(e.admin, "Research")["workspace_id"]
    e.p = {}
    for name, role, agent in (("reader", "reader", False), ("contrib", "contributor", False),
                              ("contrib2", "contributor", False), ("reviewer", "reviewer", False),
                              ("reviewer2", "reviewer", False), ("agent", "contributor", True)):
        store.member_set(e.admin, e.ws, name, role, is_agent=agent)
        e.p[name] = Principal(name, every, is_agent=agent)
    yield e
    store.close()
