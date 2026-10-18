"""Pinned released owner delegation through the installed service HTTP shell."""
import hashlib
import os
import uuid

from fastapi.testclient import TestClient
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from vuoro_service.run_delegation import DelegatedBearerIdentityResolver
from .sprintctl_binding import SprintctlProvider
from .test_bound_proposal_http import assert_installed_composition


@pytest.mark.skipif(os.environ.get('VUORO_BOUND_COMPOSITION_PROOF') != '1',
                    reason='requires isolated installed release composition')
def test_released_owner_delegation_is_run_scoped_and_revocable(request):
    url = request.config.getoption('--lease-pg-url')
    if url is None:
        pytest.skip('requires an explicit disposable PostgreSQL owner')
    assert_installed_composition()
    from sprintctl.vuoro_adapter import register_work_catalog
    owner = SprintctlProvider(url)
    identity = Identity(actor='A', environment='lease-conformance',
        authorities=frozenset({'work:read', 'work:evidence'}),
        principal_id='github:100:0', workspace_id='lease-conformance',
        client_id='delegation-client', grant_id='delegation-grant',
        repo_ids=frozenset({owner.store.repo_id}))
    registry = CatalogRegistry()
    register_work_catalog(registry, owner.app)
    resolver = DelegatedBearerIdentityResolver(StaticBearerIdentityResolver({'parent': identity}),
        environment='lease-conformance', lookup=owner.app.resolve_run_delegation_digest)
    try:
        with TestClient(create_app(settings=ServiceSettings(environment_name='lease-conformance'),
                registry=registry, identity_resolver=resolver)) as client:
            def invoke(operation, arguments, token='parent'):
                return client.post('/api/invoke/v1', headers={
                    'Authorization': 'Bearer ' + token, 'X-Vuoro-Client-Protocol': '1'}, json={
                    'schema_version': 'invocation/v1', 'request_id': uuid.uuid4().hex,
                    'repo_id': owner.store.repo_id, 'catalog_revision': registry.revision,
                    'operation': operation, 'arguments': arguments,
                    **({'idempotency_key': arguments['idempotency_key']}
                       if operation in {'work.run.delegate-v1', 'work.run.delegation-revoke-v1'} else {})})

            def ok(operation, arguments, token='parent'):
                response = invoke(operation, arguments, token)
                assert response.status_code == 200, response.text
                return response.json()['result']

            run = ok('work.run.register-v1', {'harness_id': 'conformance', 'harness_build': 'test',
                'model_id': 'scripted', 'recipe_id': 'delegation-http/v1',
                'observed_profile': {'instruction_digest': 'sha256:' + 'a' * 64, 'skill_digests': []},
                'idempotency_key': uuid.uuid4().hex})['run']['run_id']
            instance = 'ins_' + 'A' * 26
            ok('work.agent.register-v1', {'instance_id': instance, 'idempotency_key': uuid.uuid4().hex,
                'declaration': {'schema': 'agent-declaration/v1', 'harness': {'name': 'test', 'version': '1'},
                    'adapter': None, 'host_class': 'workstation', 'interactivity': 'interactive',
                    'accepted_kinds': ['continue'],
                    'channels': [{'channel': 'mcp', 'wake_modes': ['read-while-running']}]}})
            mint = {'run_id': run, 'creator_instance_id': instance,
                'operations': ['work.run.resolve-v1', 'work.session-note.write-v1'],
                'ttl_seconds': 300, 'idempotency_key': uuid.uuid4().hex}
            issued = ok('work.run.delegate-v1', mint)
            token = issued['token']
            assert token.startswith('vrd_')
            assert ok('work.run.delegate-v1', mint)['token'] is None
            ok('work.run.resolve-v1', {'run_id': run}, token)
            wrong = invoke('work.run.resolve-v1', {'run_id': 'run_' + 'B' * 26}, token)
            assert wrong.status_code == 403
            forbidden = invoke('work.agent.list-v1', {}, token)
            assert forbidden.status_code == 403
            note = {'run_id': run, 'note': 'released delegation proof', 'idempotency_key': uuid.uuid4().hex}
            ok('work.session-note.write-v1', note, token)
            ok('work.session-note.write-v1', note, token)
            with owner.store.conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM session_note WHERE repo_id=%s AND run_id=%s',
                            (owner.store.repo_id, run))
                assert cur.fetchone()['n'] == 1
            owner.store.conn.rollback()
            # Cache a valid resolver result, then revoke. The owner must still
            # revalidate in its transaction when a stale edge identity is used.
            resolved = owner.app.resolve_run_delegation_digest(hashlib.sha256(token.encode()).hexdigest())
            ok('work.run.delegation-revoke-v1', {'delegation_id': issued['delegation_id'],
                'reason': 'conformance', 'idempotency_key': uuid.uuid4().hex})
            assert invoke('work.run.resolve-v1', {'run_id': run}, token).status_code == 401
            cached = DelegatedBearerIdentityResolver(StaticBearerIdentityResolver({}),
                environment='lease-conformance', lookup=lambda _: resolved)
            with TestClient(create_app(settings=ServiceSettings(environment_name='lease-conformance'),
                    registry=registry, identity_resolver=cached)) as cached_client:
                client = cached_client
                assert invoke('work.run.resolve-v1', {'run_id': run}, token).status_code == 401
                note['idempotency_key'] = uuid.uuid4().hex
                assert invoke('work.session-note.write-v1', note, token).status_code == 401
    finally:
        owner.close()
