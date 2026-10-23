"""Actual HTTP/schema/unchanged-owner qualification, with independent DB snapshots."""
from contextlib import closing
import base64
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import httpx
from artifact_semantics import _TEST_PRIVATE_KEY, canonical_bytes, make_signed_artifact, transport_fields
from candidate_owner_http import CandidateOwnerServer, initialize_fixture, persisted_artifact
from emitter_schema_v3_checks import schema_corpus
from owner_oracle import OwnerOracle, material_digest
from schema_owner_http_v3 import SchemaOwnerHandler, SchemaOwnerServer

REQUEST_ID = '9007199254741199'
ORIGINAL = b'\x00{"futureSchema":{"opaque":"preserve exact spaces"} }\xff'


def snapshot(path):
    with closing(sqlite3.connect(path)) as database:
        return {table: database.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                for table in ('request', 'event', 'idem', 'dependent', 'signed_artifact')}


def seed(path):
    initialize_fixture(path)
    body = {'objective': '', 'note': None}
    signed = make_signed_artifact(workspace_id='workspace-a', request_id=REQUEST_ID,
        material_revision=1, material_digest=material_digest(body), original_bytes=ORIGINAL)
    owner = OwnerOracle.connect_as(path, 'requester-a')
    try:
        owner.publish(REQUEST_ID, body, ['work-a'], 'seed', signed_artifact=signed)
    finally:
        owner.close()
    return signed


def assert_refusal(status, before, after):
    if status not in (409, 422):
        raise AssertionError('invalid command was not explicitly refused')
    if before != after:
        raise AssertionError('refused command changed owner state')


def assert_mutation(status, before, after):
    if status != 200 or before == after:
        raise AssertionError('positive owner mutation was inert')
    for table in ('event', 'idem'):
        if len(after[table]) != len(before[table]) + 1:
            raise AssertionError('owner transaction did not record exactly one event/idempotency row')


class _InvalidResultHandler(SchemaOwnerHandler):
    def _result_projection(self, operation, result):
        if operation == 'readRequest':
            return {**result, 'state': 'future-success'}
        return result


class _AdditiveResultHandler(SchemaOwnerHandler):
    def _result_projection(self, operation, result):
        if operation == 'readRequest':
            result = copy.deepcopy(result)
            result['futureMetadata'] = {'safe': True}
            result['body']['futureMetadata'] = {'safe': True}
        return result


class SchemaOwnerV3Checks(unittest.TestCase):
    def test_same_command_corpus_at_real_http_owner_boundary(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-owner-corpus-') as temp:
            path = Path(temp) / 'owner.db'
            seed(path)
            with SchemaOwnerServer(path) as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                calls = []
                original_call = server.httpd.owner_call
                def observe(operation, *args, **kwargs):
                    calls.append(operation)
                    return original_call(operation, *args, **kwargs)
                server.httpd.owner_call = observe
                for index, row in enumerate(schema_corpus()):
                    if row['schema'] != 'ReviseRequest.json':
                        continue
                    command = copy.deepcopy(row['value'])
                    if command['requestId'] == '19':
                        command['requestId'] = REQUEST_ID
                    before = snapshot(path)
                    count = len(calls)
                    result = client.patch('/v3/requests/' + REQUEST_ID, json=command,
                        headers={'Idempotency-Key': f'corpus-{index}'})
                    after = snapshot(path)
                    with self.subTest(row=row['name']):
                        if not row['valid']:
                            self.assertEqual(result.status_code, 422, result.text)
                            self.assertEqual(len(calls), count)
                            assert_refusal(result.status_code, before, after)
                        else:
                            self.assertEqual(calls[count:], ['revise'])
                            self.assertIn(result.status_code, (200, 409), result.text)
                            if result.status_code == 200:
                                assert_mutation(result.status_code, before, after)
                            else:
                                assert_refusal(result.status_code, before, after)
                before = snapshot(path)
                revision = before['request'][0][2]
                positive = client.patch('/v3/requests/' + REQUEST_ID,
                    json={'requestId': REQUEST_ID, 'expectedRevision': str(revision), 'body': {'objective': 'real positive'}},
                    headers={'Idempotency-Key': 'positive'})
                assert_mutation(positive.status_code, before, snapshot(path))

    def test_publish_nested_artifact_unknowns_and_signed_meaning(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-owner-publish-') as temp:
            path = Path(temp) / 'owner.db'
            initialize_fixture(path)
            body = {'objective': '', 'note': None}
            signed = make_signed_artifact(workspace_id='workspace-a', request_id=REQUEST_ID,
                material_revision=1, material_digest=material_digest(body), original_bytes=ORIGINAL)
            baseline = {'requestId': REQUEST_ID, 'body': body, 'dependants': ['work-a'], 'artifact': transport_fields(signed)}
            variants = []
            variants.append(('unknown-top', {**baseline, 'futureAuthority': True}, 422))
            variants.append(('unknown-nested', {**baseline, 'body': {**body, 'futureAuthority': True}}, 422))
            variants.append(('unknown-artifact', {**baseline, 'artifact': {**baseline['artifact'], 'futureAuthority': True}}, 422))
            variants.append(('null-artifact', {**baseline, 'artifact': None}, 422))
            variants.append(('missing-artifact-field', {**baseline, 'artifact': {k: v for k, v in baseline['artifact'].items() if k != 'artifactEnvelope'}}, 422))
            for name, changes in [('signed-unknown-envelope', {'futureAuthority': True}),
                                  ('signed-unknown-intent', {'intent': 'future-grant'}),
                                  ('signed-wrong-workspace', {'workspace_id': 'workspace-b'})]:
                envelope = {**json.loads(signed['envelope_bytes']), **changes}
                altered = canonical_bytes(envelope)
                artifact = {**baseline['artifact'], 'artifactEnvelope': base64.b64encode(altered).decode(),
                            'artifactSignature': base64.b64encode(_TEST_PRIVATE_KEY.sign(altered)).decode()}
                variants.append((name, {**baseline, 'artifact': artifact}, 409))
            with SchemaOwnerServer(path) as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                for name, command, status in variants:
                    before = snapshot(path)
                    response = client.post('/v3/requests', json=command, headers={'Idempotency-Key': name})
                    with self.subTest(case=name):
                        self.assertEqual(response.status_code, status, response.text)
                        assert_refusal(response.status_code, before, snapshot(path))
                before = snapshot(path)
                response = client.post('/v3/requests', json=baseline, headers={'Idempotency-Key': 'positive-publish'})
                assert_mutation(response.status_code, before, snapshot(path))
                expected = (ORIGINAL, signed['envelope_bytes'], signed['signature'], signed['artifact_digest'])
                self.assertEqual(persisted_artifact(path, REQUEST_ID, 1), expected)
                stable = snapshot(path)
                replay = client.post('/v3/requests', json=baseline, headers={'Idempotency-Key': 'positive-publish'})
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json(), response.json())
                self.assertEqual(snapshot(path), stable)
                conflict = client.post('/v3/requests', json={**baseline, 'body': {'objective': 'changed'}},
                                       headers={'Idempotency-Key': 'positive-publish'})
                assert_refusal(conflict.status_code, stable, snapshot(path))
                artifact = client.get(f'/v3/requests/{REQUEST_ID}/artifact/1')
                self.assertEqual(artifact.status_code, 200, artifact.text)
                observed = artifact.json()
                self.assertEqual((base64.b64decode(observed['originalBytes']), base64.b64decode(observed['envelopeBytes']),
                                  base64.b64decode(observed['signature']), observed['artifactDigest']), expected)
                self.assertEqual(snapshot(path), stable)

    def test_owner_scope_cas_identity_and_unsupported_version(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-owner-scope-') as temp:
            path = Path(temp) / 'owner.db'
            seed(path)
            baseline = {'requestId': REQUEST_ID, 'expectedRevision': '1', 'body': {'objective': 'changed'}}
            with SchemaOwnerServer(path, principal_id='requester-b') as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                before = snapshot(path)
                response = client.patch('/v3/requests/' + REQUEST_ID, json=baseline, headers={'Idempotency-Key': 'cross-scope'})
                self.assertEqual(response.status_code, 409, response.text)
                assert_refusal(response.status_code, before, snapshot(path))
            with SchemaOwnerServer(path) as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                for name, command in [('zero', {**baseline, 'expectedRevision': '0'}),
                                      ('stale', {**baseline, 'expectedRevision': '2'}),
                                      ('wrong-kind', {**baseline, 'requestId': '20'})]:
                    before = snapshot(path)
                    response = client.patch('/v3/requests/' + REQUEST_ID, json=command, headers={'Idempotency-Key': name})
                    assert_refusal(response.status_code, before, snapshot(path))
                before = snapshot(path)
                response = client.patch('/v3/requests/' + REQUEST_ID, json=baseline)
                self.assertEqual(response.status_code, 422, response.text)
                assert_refusal(response.status_code, before, snapshot(path))
            with CandidateOwnerServer(path) as old, httpx.Client(base_url=old.base_url, timeout=5) as client:
                before = snapshot(path)
                response = client.post('/v3/requests', json={'requestId': '20', 'body': {'objective': 'unsupported'}, 'dependants': ['work-a']},
                                       headers={'Idempotency-Key': 'unsupported-v3'})
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(snapshot(path), before)

    def test_invalid_server_result_refuses_without_read_effect(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-invalid-result-') as temp:
            path = Path(temp) / 'owner.db'
            seed(path)
            with SchemaOwnerServer(path, handler=_InvalidResultHandler) as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                before = snapshot(path)
                response = client.get('/v3/requests/' + REQUEST_ID)
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(snapshot(path), before)

    def test_presence_roundtrips_and_additive_owner_result_projection(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-presence-') as temp:
            path = Path(temp) / 'owner.db'
            initialize_fixture(path)
            bodies = [{'objective': ''}, {'objective': '', 'note': None}, {'objective': '', 'note': ''}]
            with SchemaOwnerServer(path) as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                for index, body in enumerate(bodies, start=21):
                    request_id = str(index)
                    command = {'requestId': request_id, 'body': body, 'dependants': ['work-a']}
                    before = snapshot(path)
                    response = client.post('/v3/requests', json=command, headers={'Idempotency-Key': 'presence-' + request_id})
                    assert_mutation(response.status_code, before, snapshot(path))
                    stored = next(row for row in snapshot(path)['request'] if row[0] == request_id)
                    self.assertEqual(json.loads(stored[5]), body)
                    stable = snapshot(path)
                    read = client.get('/v3/requests/' + request_id)
                    self.assertEqual(read.status_code, 200, read.text)
                    self.assertEqual(read.json()['body'], body)
                    self.assertEqual(snapshot(path), stable)
            with SchemaOwnerServer(path, handler=_AdditiveResultHandler) as server, httpx.Client(base_url=server.base_url, timeout=5) as client:
                before = snapshot(path)
                read = client.get('/v3/requests/22')
                self.assertEqual(read.status_code, 200, read.text)
                self.assertEqual(read.json()['body'], {**bodies[1], 'futureMetadata': {'safe': True}})
                self.assertEqual(read.json()['futureMetadata'], {'safe': True})
                self.assertEqual(read.json()['revision'], '1')
                self.assertEqual(read.json()['state'], 'open')
                self.assertEqual(snapshot(path), before)

    def test_independent_owner_guard_is_rejecting(self):
        before = {'request': [('1', 1)], 'event': [1], 'idem': [1]}
        changed = {'request': [('1', 2)], 'event': [1, 2], 'idem': [1, 2]}
        with self.assertRaisesRegex(AssertionError, 'not explicitly refused'):
            assert_refusal(200, before, changed)
        with self.assertRaisesRegex(AssertionError, 'changed owner state'):
            assert_refusal(422, before, changed)
        with self.assertRaisesRegex(AssertionError, 'inert'):
            assert_mutation(200, before, before)
        assert_refusal(422, before, before)
        assert_mutation(200, before, changed)


if __name__ == '__main__':
    unittest.main()
