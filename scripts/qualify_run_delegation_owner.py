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
    parser.add_argument("--currentness", action="store_true", help="qualify the exact source-only currentness read scope")
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
        def connect():
            fresh = psycopg.connect(os.environ["SPRINTCTL_TEST_PG_URL"], row_factory=dict_row)
            assert_disposable_connection(fresh)
            fresh.rollback()
            return fresh
        store = pg.PgStore(conn=conn, repo_id=repo_id, connection_factory=connect,
                          authority_repo_uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, repo_id)))
        pg.init_db(store)
        store.remote_schema_version = pg_migrations.CURRENT_SCHEMA_VERSION
        if args.currentness:
            try:
                qualify_currentness(store)
            finally:
                conn.rollback()
                cleanup_test_repositories(conn, [repo_id])
            print(f"source owner {actual}: currentness HTTP exact scope, stale basis, lost reply and cached-context revocation conformance passed")
            return
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


def qualify_currentness(store):
    """Actual owner-produced scope crosses the actual service, without writes."""
    import httpx
    from sprintctl import pg, work_currentness
    from sprintctl.application import WorkApplication
    from sprintctl.vuoro_adapter import register_work_catalog
    from tests.pg.test_work_currentness import setup, delegated_scope, counts
    from tests.pg.test_work_lease import A, _invoke
    from vuoro_service.app import ServiceSettings, create_app
    from vuoro_service.catalog import CatalogRegistry
    from vuoro_service.identity import StaticBearerIdentityResolver
    from vuoro_service.run_delegation import DelegatedBearerIdentityResolver

    item, body = setup(store)
    issued, _ = delegated_scope(store, body)
    application = WorkApplication.postgres(store)
    resolved = application.resolve_run_delegation_digest(hashlib.sha256(issued['token'].encode('ascii')).hexdigest())
    assert resolved.authorities == frozenset({'work:read'})
    assert tuple(resolved.delegation.operations) == (work_currentness.OPERATION,)
    registry = CatalogRegistry()
    register_work_catalog(registry, application)
    # Deliberately retain the old authentication projection for the final live
    # revocation proof. The operation owner must re-read its actual scope.
    resolver = DelegatedBearerIdentityResolver(StaticBearerIdentityResolver({}), environment='vuoro-dev',
        lookup=lambda _: resolved, allowed_repo_ids=frozenset({store.repo_id}))
    service = create_app(settings=ServiceSettings(environment_name='vuoro-dev', environment_class='development'),
        registry=registry, identity_resolver=resolver)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url='http://fixture') as client:
            async def invoke(arguments=body, operation=work_currentness.OPERATION, repo=None):
                return await client.post('/api/invoke/v1', headers={
                    'authorization':'Bearer '+issued['token'],'X-Vuoro-Client-Protocol':'1'}, json={
                    'schema_version':'invocation/v1','request_id':str(uuid.uuid4()),'operation':operation,
                    'arguments':arguments,'repo_id':repo or store.repo_id})
            before = counts(store)
            discarded = await invoke()
            assert discarded.status_code == 200, discarded.text
            answer = await invoke()
            assert answer.status_code == 200, answer.text
            facts = answer.json()['result']['currentness']
            assert facts['authorizes_effect'] is False and facts['checkpoint'] == body['checkpoint']
            assert facts['delegation']['operations'] == [work_currentness.OPERATION]
            assert counts(store) == before
            wrong_run = await invoke({**body,'run_id':'run_'+'A'*26})
            assert wrong_run.status_code == 403, wrong_run.text
            wrong_repo = await invoke(repo='another-repository')
            assert wrong_repo.status_code == 403, wrong_repo.text
            for operation, arguments in (
                ('work.run.resolve-v1', {'run_id':body['run_id']}),
                ('work.session-note.write-v1', {'run_id':body['run_id'],'note':'forbidden','idempotency_key':uuid.uuid4().hex}),
                ('work.run.delegate-v1', {'run_id':body['run_id']}),
            ):
                denied = await invoke(arguments, operation)
                assert denied.status_code == 403, denied.text
            assert counts(store) == before
            pg.add_ref(store,item,'doc','https://example.invalid/source-currentness')
            before = counts(store)
            stale = await invoke()
            assert stale.status_code == 409, stale.text
            assert stale.json()['error']['code'] == 'work-basis-stale'
            assert counts(store) == before
            _invoke(store,'work.run.delegation-revoke-v1',{'delegation_id':issued['delegation_id'],
                'reason':'source-conformance','idempotency_key':uuid.uuid4().hex},A)
            before = counts(store)
            revoked = await invoke()
            assert revoked.status_code == 401, revoked.text
            assert revoked.json()['error']['code'] == 'credential-invalid'
            assert counts(store) == before
    asyncio.run(exercise())


if __name__ == "__main__":
    main()
