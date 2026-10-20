"""Real generated-client transports through the unchanged service assertion verifier.

All identities/keys are disposable fixtures. No hosted credential or auth issuer
is provided to a candidate, browser, or test log.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, closing
from pathlib import Path
import os
import sqlite3
import tempfile
import time
import unittest

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lifecycle_slice_checks import HERE, grpc, v1_pb2, v1_pb2_grpc, OwnerOracle
from connect_owner_http import DirectConnectServer
from authenticated_owner import (AuthenticatedCandidateServer, AuthenticatedOwner, IdentityMiddleware,
    FOREIGN, ISSUER, WORKSPACE, fixture_policy, owner_snapshot, seed_fixture_owner, sign_fixture_assertion)
from connect_browser_checks import node_edge
from ec00_connect.oa_v1_connect import OwnerOperationsClientSync
from connectrpc.compat import google_protobuf_binary_codec, google_protobuf_json_codec
from connectrpc.errors import ConnectError
from connectrpc.code import Code
from ec00_oa_v2_python_client.client import Client
from ec00_oa_v2_python_client.api.default import publish
from ec00_oa_v2_python_client.models import PublishRequest

ARMS = ('http-v2', 'grpc', 'python-json', 'python-binary', 'edge-json', 'edge-binary')
DEFAULT_RID = '9007199254741201'
EDGE_DUPLICATE_REFUSAL = 'duplicate identity or correlation header refused'


snapshot = owner_snapshot


class FixtureIdentity:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.policy = fixture_policy(self.key.public_key())

    def headers(self, subject='alice', *, request_id=DEFAULT_RID, claims=None, key=None):
        """Sign an assertion whose request_id covers the OA request id in the body.

        The candidate protocol has no separate invocation envelope, so the owner
        binds the claim to the parsed message's request_id (see
        authenticated_owner.message_request_id), as production binds it to the
        parsed invocation envelope.
        """
        return sign_fixture_assertion(key or self.key, subject, request_id=request_id, claims=claims)

    def initialize(self, path):
        seed_fixture_owner(path)


@contextmanager
def endpoint(path, identity, arm):
    with ExitStack() as stack:
        if arm.startswith('python'):
            server = stack.enter_context(DirectConnectServer(path, owner_factory=AuthenticatedOwner,
                middleware=lambda app: IdentityMiddleware(app, identity.policy)))
            url = server.base_url
        else:
            server = stack.enter_context(AuthenticatedCandidateServer(path, identity.policy))
            url = stack.enter_context(node_edge(server.grpc_target)) if arm.startswith('edge') else server.base_url
        if arm == 'grpc':
            channel = stack.enter_context(grpc.insecure_channel(server.grpc_target))
            client = v1_pb2_grpc.OwnerOperationsStub(channel)
        elif arm == 'http-v2':
            client = None
        else:
            codec = google_protobuf_binary_codec if arm.endswith('binary') else google_protobuf_json_codec
            client = stack.enter_context(OwnerOperationsClientSync(url, codec=codec(),
                send_compression=None, accept_compression=[], timeout_ms=5000))
        yield Transport(arm, url, client)


class Transport:
    def __init__(self, arm, url, client):
        self.arm, self.url, self.client = arm, url, client

    def publish(self, headers, *, rid=DEFAULT_RID, subject='alice', key='publish', objective='auth fixture'):
        headers = {**headers, 'idempotency-key': key}
        if self.arm == 'http-v2':
            with Client(base_url=self.url, headers=headers) as client:
                result = publish.sync_detailed(client=client, body=PublishRequest(
                    request_id=rid, objective=objective, dependants=['work-' + subject]))
            if result.status_code == 200:
                return 'ok', result.parsed.request_id
            return {401: 'identity', 403: 'authority', 409: 'owner'}.get(result.status_code, str(result.status_code)), None
        request = v1_pb2.PublishRequest(request_id=rid, objective=objective, dependent_ids=['work-' + subject])
        try:
            if self.arm == 'grpc':
                result = self.client.Publish(request, metadata=list(headers.items()), timeout=5)
            else:
                result = self.client.publish(request, headers=headers)
            return 'ok', result.request_id
        except grpc.RpcError as exc:
            return {grpc.StatusCode.UNAUTHENTICATED: 'identity', grpc.StatusCode.PERMISSION_DENIED: 'authority',
                    grpc.StatusCode.ABORTED: 'owner'}.get(exc.code(), str(exc.code())), None
        except ConnectError as exc:
            return {Code.UNAUTHENTICATED: 'identity', Code.PERMISSION_DENIED: 'authority', Code.ABORTED: 'owner'}.get(exc.code, str(exc.code)), None


class AuthenticatedTransportTests(unittest.TestCase):
    def assertNoIdentityCarryOver(self, policy, expected):
        """Server-side records: each request entered with no identity context,
        left none behind, and the owner saw only the principal that request signed.

        ``expected`` maps the envelope request id to the one principal allowed
        to reach the owner for it.
        """
        observations, owners = list(policy.observations), list(policy.owner_observations)
        self.assertTrue(observations)
        for record in observations:
            self.assertTrue(record['entry_clear'], record)
            self.assertTrue(record['exit_clear'], record)
            self.assertEqual(expected[record['request_id']], record['principal'], record)
        self.assertTrue(owners)
        for record in owners:
            self.assertEqual(expected[record['request_id']], record['principal'], record)

    def cases(self):
        self.assertTrue(os.environ.get('EC00_NODE'), 'all six arms require the pinned Node toolchain')
        return ARMS

    def test_generated_clients_fresh_assertion_retry_and_principal_scoped_durable_effect(self):
        for arm in self.cases():
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'owner.sqlite'
                identity = FixtureIdentity(); identity.initialize(path)
                with endpoint(path, identity, arm) as transport:
                    headers = identity.headers()
                    self.assertEqual(('ok', '9007199254741201'), transport.publish(headers))
                    committed = snapshot(path)
                    self.assertEqual(('identity', None), transport.publish(headers))
                    self.assertEqual(('ok', '9007199254741201'), transport.publish(identity.headers()))
                    self.assertEqual(('owner', None), transport.publish(identity.headers(), objective='changed'))
                    self.assertEqual(committed, snapshot(path))
                    self.assertEqual((1,1,1,1), tuple(len(committed[t]) for t in ('request','dependent','event','idem')))
                    self.assertEqual((WORKSPACE, f'{ISSUER}:alice:0', 'publish'), committed['idem'][0][:3])
                    with closing(sqlite3.connect(path)) as db:
                        db.execute('UPDATE identity SET active=0 WHERE principal_id=?', (f'{ISSUER}:alice:0',))
                        db.commit()
                    # A fresh, correctly signed assertion cannot resurrect a
                    # revoked owner membership even for a previously committed key.
                    self.assertEqual(('owner', None), transport.publish(identity.headers()))
                    self.assertEqual(committed, snapshot(path))
                # Sequential requests on reused server workers: none inherits
                # or leaves behind an identity context.
                self.assertNoIdentityCarryOver(identity.policy, {DEFAULT_RID: f'{ISSUER}:alice:0'})

    def test_bad_identity_and_authority_refuse_before_any_owner_effect(self):
        for arm in self.cases():
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'owner.sqlite'
                identity = FixtureIdentity(); identity.initialize(path)
                with endpoint(path, identity, arm) as transport:
                    before = snapshot(path)
                    mutations = [('audience', {'aud':'different'}), ('issuer', {'iss':'different'}),
                        ('workspace', {'workspace_id':FOREIGN}), ('repository', {'repo_ids':['foreign']}),
                        ('expired', {'exp':int(time.time())-5, 'iat':int(time.time())-30, 'nbf':int(time.time())-30}),
                        ('future', {'iat':int(time.time())+60, 'nbf':int(time.time())+60, 'exp':int(time.time())+90}),
                        ('actor-disagrees', {'actor':'agent:bob'}), ('epoch', {'principal_epoch':-1})]
                    bad = [('missing', {}), ('unsigned-actor', {'x-actor':'agent:alice', 'x-workspace-id':WORKSPACE}),
                           ('signature', identity.headers(key=Ed25519PrivateKey.generate())),
                           ('correlation', {**identity.headers(), 'x-request-id':'different'}),
                           # Header and claim agree, but neither covers the body's request id.
                           ('envelope', identity.headers(request_id='9007199254749999'))]
                    bad.extend((name, identity.headers(claims=change)) for name,change in mutations)
                    for name,headers in bad:
                        with self.subTest(case=name):
                            self.assertEqual(('identity', None), transport.publish(headers))
                            self.assertEqual(before, snapshot(path))
                    self.assertEqual(('authority', None), transport.publish(identity.headers(claims={'authorities':['fixture:owner-read']})))
                    for subject in ('revoked', 'foreign', 'observer'):
                        with self.subTest(subject=subject):
                            self.assertEqual(('owner', None), transport.publish(identity.headers(subject), subject=subject))
                            self.assertEqual(before, snapshot(path))
                    # A display actor header cannot change the signed principal or take Bob's link.
                    self.assertEqual(('owner', None), transport.publish({**identity.headers(), 'x-actor':'agent:bob'}, subject='bob'))
                    self.assertEqual(before, snapshot(path))

    def test_repeated_identity_or_correlation_headers_are_refused(self):
        for arm in self.cases():
            for duplicate in ('x-vuoro-identity', 'x-request-id'):
                with self.subTest(arm=arm, duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'owner.sqlite'
                    identity = FixtureIdentity(); identity.initialize(path)
                    with endpoint(path, identity, arm) as transport:
                        headers = identity.headers(request_id='9007199254741501')
                        pairs = [*headers.items(), (duplicate, headers[duplicate]), ('idempotency-key','duplicate')]
                        before = snapshot(path)
                        resolutions = identity.policy.resolutions
                        if arm == 'grpc':
                            with self.assertRaises(grpc.RpcError) as caught:
                                transport.client.Publish(v1_pb2.PublishRequest(request_id='9007199254741501',
                                    objective='duplicate headers', dependent_ids=['work-alice']), metadata=pairs, timeout=5)
                            self.assertEqual(grpc.StatusCode.UNAUTHENTICATED, caught.exception.code())
                        else:
                            route = '/v2/requests' if arm == 'http-v2' else '/' + v1_pb2.DESCRIPTOR.services_by_name['OwnerOperations'].full_name + '/Publish'
                            body = dict(requestId='9007199254741501', objective='duplicate headers')
                            body['dependants' if arm=='http-v2' else 'dependentIds'] = ['work-alice']
                            response = httpx.post(transport.url + route, json=body,
                                headers=[*pairs, ('connect-protocol-version','1')], timeout=5)
                            self.assertEqual(401, response.status_code)
                            if arm.startswith('edge'):
                                # The edge's own raw-header duplicate check refused
                                # it: no upstream identity resolution was attempted.
                                self.assertEqual('duplicate-header', response.headers.get('x-ec00-edge-refusal'))
                                self.assertEqual({'code': 'unauthenticated', 'message': EDGE_DUPLICATE_REFUSAL}, response.json())
                                self.assertEqual(resolutions, identity.policy.resolutions)
                            else:
                                self.assertEqual(resolutions + 1, identity.policy.resolutions)
                        self.assertEqual(before, snapshot(path))
                        self.assertEqual(('ok', '9007199254741501'), transport.publish(identity.headers(request_id='9007199254741501'),
                            rid='9007199254741501', key='duplicate', objective='duplicate headers'))

    def test_concurrent_requests_observe_only_their_own_server_identity_context(self):
        for arm in self.cases():
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'owner.sqlite'
                identity = FixtureIdentity(); identity.initialize(path)
                with endpoint(path, identity, arm) as transport:
                    def call(index):
                        subject = 'alice' if index % 2 == 0 else 'bob'
                        rid = str(9007199254741300 + index)
                        result = transport.publish(identity.headers(subject, request_id=rid), subject=subject, rid=rid, key='shared-key-' + str(index//2))
                        return result, rid, subject
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        results = list(pool.map(call, range(16)))
                    state = snapshot(path)
                    self.assertEqual((16,16,16,16), tuple(len(state[t]) for t in ('request','dependent','event','idem')))
                    for result,rid,subject in results:
                        self.assertEqual(('ok', rid), result)
                        self.assertIn((rid, 'work-' + subject, 1, 0), state['dependent'])
                        self.assertTrue(any(row[1] == f'{ISSUER}:{subject}:0' and rid in row[5] for row in state['idem']))
                    self.assertNoIdentityCarryOver(identity.policy,
                        {rid: f'{ISSUER}:{subject}:0' for _, rid, subject in results})
                    self.assertEqual(16, len(identity.policy.owner_observations))
                    if arm in ('grpc', 'edge-json', 'edge-binary'):
                        # gRPC's four-thread pool served sixteen requests, so
                        # entry-clear above was checked on reused worker threads.
                        self.assertLess(len({r['thread'] for r in identity.policy.observations}), 16)


if __name__ == '__main__':
    unittest.main()
