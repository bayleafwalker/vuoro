#!/usr/bin/env python3
"""Explicit source-owner PostgreSQL conformance; never a release/deploy proof.

The database must satisfy Sprintctl's dedicated disposable-role/database guard.
No credential or database URL is printed. Source imports are restricted to the
explicit clean owner commit and this service checkout, in an isolated process.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-source", type=Path, required=True)
    parser.add_argument("--owner-commit", required=True)
    args = parser.parse_args()
    source = args.owner_source.resolve()
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if actual != args.owner_commit or dirty.strip():
        raise SystemExit("owner checkout must match the explicit clean source commit")
    if not os.environ.get("SPRINTCTL_TEST_PG_URL"):
        raise SystemExit("explicit disposable owner test database is required")
    sys.path[:0] = [str(source), str(Path(__file__).resolve().parents[1] / "packages/vuoro-service/src")]
    import httpx
    import psycopg
    from psycopg.rows import dict_row
    from sprintctl import delegation, pg, pg_migrations
    from sprintctl.pg_testing import assert_disposable_connection, new_test_repo_id, cleanup_test_repositories
    from sprintctl.vuoro_adapter import register_work_catalog
    from tests.pg.test_run_delegation import _world, _mint, _key
    from vuoro_service.app import ServiceSettings, create_app
    from vuoro_service.catalog import CatalogRegistry
    from vuoro_service.identity import StaticBearerIdentityResolver
    from vuoro_service.run_delegation import DelegatedBearerIdentityResolver

    repo_id = new_test_repo_id("service-delegation")
    with psycopg.connect(os.environ["SPRINTCTL_TEST_PG_URL"], row_factory=dict_row) as conn:
        assert_disposable_connection(conn)
        store = pg.PgStore(conn=conn, repo_id=repo_id,
                          authority_repo_uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, repo_id)))
        pg.init_db(store)
        store.remote_schema_version = pg_migrations.CURRENT_SCHEMA_VERSION
        application, parent_context, run_id, instance_id = _world(store)
        try:
            issued = _mint(application, parent_context, run_id, instance_id)
            digest = hashlib.sha256(issued["token"].encode("ascii")).hexdigest()
            resolved = application.resolve_run_delegation_digest(digest)
            # A cached authenticator is deliberately injected after revocation:
            # the real owner must still refuse its formerly valid context.
            registry = CatalogRegistry()
            register_work_catalog(registry, application)
            resolver = DelegatedBearerIdentityResolver(StaticBearerIdentityResolver({}),
                environment="vuoro-dev", lookup=lambda _: resolved)
            service = create_app(settings=ServiceSettings(environment_name="vuoro-dev", environment_class="development"),
                                 registry=registry, identity_resolver=resolver)

            async def exercise():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://fixture") as client:
                    async def invoke(run, operation="work.run.resolve-v1", key=None):
                        arguments = {"run_id": run}
                        if key is not None:
                            arguments.update(note="source-only delegated write", idempotency_key=key)
                        return await client.post("/api/invoke/v1", headers={
                            "authorization": "Bearer " + issued["token"], "X-Vuoro-Client-Protocol": "1"},
                            json={"schema_version": "invocation/v1", "request_id": "source-conformance-1",
                                  "operation": operation, "arguments": arguments, "repo_id": repo_id})
                    allowed = await invoke(run_id)
                    assert allowed.status_code == 200, allowed.status_code
                    wrong_run = await invoke("run_" + "A" * 26)
                    assert wrong_run.status_code == 403
                    key = _key()
                    written = await invoke(run_id, "work.session-note.write-v1", key)
                    assert written.status_code == 200, written.text
                    replay = await invoke(run_id, "work.session-note.write-v1", key)
                    assert replay.status_code == 200, replay.text
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT count(*) AS n FROM session_note WHERE repo_id=%s AND run_id=%s", (repo_id, run_id))
                        assert cursor.fetchone()["n"] == 1
                    conn.rollback()
                    application.invoke(delegation.OP_REVOKE, {"delegation_id": issued["delegation_id"],
                        "reason": "source-conformance", "idempotency_key": _key()}, parent_context)
                    refused = await invoke(run_id)
                    assert refused.status_code == 401, refused.status_code
                    assert refused.json()["error"]["code"] == "credential-invalid"
                    refused_write = await invoke(run_id, "work.session-note.write-v1", _key())
                    assert refused_write.status_code == 401, refused_write.text
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT count(*) AS n FROM session_note WHERE repo_id=%s AND run_id=%s", (repo_id, run_id))
                        assert cursor.fetchone()["n"] == 1
            asyncio.run(exercise())
        finally:
            conn.rollback()
            cleanup_test_repositories(conn, [repo_id])
    print(f"source owner {actual}: HTTP same-run, wrong-run, idempotent note and cached-context revocation conformance passed")


if __name__ == "__main__":
    main()
