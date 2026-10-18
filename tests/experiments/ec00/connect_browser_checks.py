"""Actual Chromium-generated-client qualification; fixtures confer no production identity."""
from contextlib import contextmanager, ExitStack, closing
import base64
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from lifecycle_slice_checks import HERE, initialize_fixture, OwnerOracle
from versioned_counter_checks import snapshot, initialize_counter, REQUEST_ID
from versioned_counter_http import VersionedCounterServer
from connect_owner_http import DirectConnectServer
from browser_fixture import BrowserOrigin, CorsProxy
from artifact_semantics import make_signed_artifact, transport_fields
from owner_oracle import material_digest
from candidate_owner_http import seed_evidence, persisted_artifact
from playwright.sync_api import sync_playwright

ARMS = ('python-json', 'python-binary', 'edge-json', 'edge-binary', 'http-v2')


@contextmanager
def node_edge(target):
    candidates = HERE / 'candidates'
    env = {**os.environ, 'NODE_PATH': str(candidates / 'node_modules'),
           'PATH': str(Path(os.environ['EC00_NODE']).parent) + os.pathsep + os.environ['PATH']}
    process = subprocess.Popen([str(candidates / 'node_modules/.bin/tsx'), str(candidates / 'serve_connect_edge.ts'), target],
                               cwd=candidates, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        if not select.select([process.stdout], [], [], 10)[0]:
            raise AssertionError('Connect edge readiness timed out')
        line = process.stdout.readline()
        if not line:
            raise AssertionError('Connect edge exited before readiness: ' + process.stderr.read()[-2000:])
        yield json.loads(line)['base_url']
    finally:
        try:
            _, stderr = process.communicate('\n', timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
            raise AssertionError('Connect edge did not stop')
        if process.returncode:
            raise AssertionError('Connect edge failed: ' + stderr[-2000:])


class ConnectBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = HERE / 'candidates'
        cls.build = tempfile.TemporaryDirectory(prefix='ec00-browser-build-')
        cls.bundle_path = Path(cls.build.name) / 'client.js'
        env = {**os.environ, 'NODE_PATH': str(candidates / 'node_modules')}
        subprocess.run([os.environ['EC00_NODE'], str(candidates / 'node_modules/esbuild/bin/esbuild'),
                        str(candidates / 'connect_browser_client.ts'), '--bundle', '--format=iife',
                        '--platform=browser', '--outfile=' + str(cls.bundle_path)], env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ['EC00_CHROMIUM'], headless=True)
        cls.browser_version = cls.browser.version
        if cls.browser_version != '155.0.8059.79':
            raise AssertionError('unexpected Chromium version: ' + cls.browser_version)
        node_version = subprocess.check_output([os.environ['EC00_NODE'], '--version'], text=True).strip()
        if node_version != 'v24.21.0':
            raise AssertionError('unexpected Node version: ' + node_version)
        print(json.dumps({'runtime': 'actualChromium', 'version': cls.browser_version,
                          'node': node_version, 'python': sys.version.split()[0]}), file=sys.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ec00-connect-browser-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'owner.sqlite'
        self.origin = BrowserOrigin(self.bundle_path.read_bytes())
        self.origin.__enter__(); self.addCleanup(self.origin.__exit__)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(self.origin.base_url)
        self.page.wait_for_function('typeof window.ec00Rpc === "function" && typeof window.ec00Http === "function"')

    def reset(self):
        if self.path.exists():
            self.path.unlink()
        initialize_fixture(self.path)

    @contextmanager
    def endpoint(self, arm, principal='requester-a', fault_plan=None):
        with ExitStack() as stack:
            if arm.startswith('python'):
                server = stack.enter_context(DirectConnectServer(self.path, principal, fault_plan))
                upstream = server.base_url
                owner = server.owner
            else:
                server = stack.enter_context(VersionedCounterServer(self.path, principal_id=principal, fault_plan=fault_plan))
                upstream = stack.enter_context(node_edge(server.grpc_target)) if arm.startswith('edge') else server.base_url
                owner = server.httpd
            proxy = stack.enter_context(CorsProxy(upstream, self.origin.base_url))
            yield proxy.base_url, proxy, owner

    def invoke(self, arm, url, method, data, key='', timeout=5000, cancel=0):
        return self.page.evaluate('''async a => a.arm === 'http-v2'
            ? await window.ec00Http(a.url, a.method, a.data, a.key, a.timeout, a.cancel)
            : await window.ec00Rpc(a.url, a.arm.endsWith('binary'), a.method, a.data, a.key, a.timeout, a.cancel)''',
            {'arm': arm, 'url': url, 'method': method, 'data': data, 'key': key, 'timeout': timeout, 'cancel': cancel})

    def success(self, result, revision=None):
        self.assertTrue(result['ok'], result)
        if revision is not None:
            self.assertEqual(str(revision), result['value']['revision'])
            self.assertEqual(REQUEST_ID, result['value']['requestId'])
        return result['value']

    def refusal(self, arm, result):
        self.assertFalse(result['ok'], result)
        if arm == 'http-v2':
            self.assertEqual(409, result['status'])
        else:
            self.assertEqual(10, result['code'])  # Connect Aborted, not a generic fetch failure.

    def test_actual_chromium_full_signed_lifecycle_shared_requests_partial_continuation(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.reset()
                body = {'objective': 'browser lifecycle', 'note': 'original signed material'}
                signed = make_signed_artifact(workspace_id='workspace-a', request_id=REQUEST_ID,
                    material_revision=1, material_digest=material_digest(body), original_bytes=b'\x00browser exact artifact\xff')
                seed_evidence(self.path, evidence_ref='evidence:browser:performed', request_id=REQUEST_ID, body=body)
                publish = {'requestId': REQUEST_ID, **body, **transport_fields(signed),
                           'dependants' if arm == 'http-v2' else 'dependentIds': ['work-a', 'work-b', 'work-c']}
                steps = [
                    ('coordinator-a', 'publish', publish, 1),
                    ('requester-a2', 'withdrawDependent', {'dependentId': 'work-c', 'expectedRevision': '1'}, 2),
                    ('requester-a', 'reportPerformed', {'expectedRevision': '2'}, 3),
                    ('verifier-a', 'verify', {'expectedRevision': '3', 'evidenceRef': 'evidence:browser:performed'}, 4),
                    ('operator-a', 'decideArtifact', {'expectedRevision': '4', 'artifactDigest': signed['artifact_digest']}, 5),
                    ('verifier-a', 'resolve', {'expectedRevision': '5'}, 6),
                    ('requester-a', 'acknowledgeContinuation', {'dependentId': 'work-a', 'expectedRevision': '6'}, 7),
                ]
                for principal, method, fields, revision in steps:
                    data = {'requestId': REQUEST_ID, **fields}
                    with self.endpoint(arm, principal) as (url, proxy, owner):
                        result = self.invoke(arm, url, method, data, 'browser-' + method)
                        self.success(result, revision)
                        committed = snapshot(self.path)
                        self.assertEqual(revision, len(committed['event']))
                        self.success(self.invoke(arm, url, method, data, 'browser-' + method), revision)
                        self.assertEqual(committed, snapshot(self.path))
                        self.assertTrue(any(r['method'] == 'OPTIONS' and r['allowed'] for r in proxy.wire))
                self.assertEqual((signed['original_bytes'], signed['envelope_bytes'], signed['signature'], signed['artifact_digest']),
                                 persisted_artifact(self.path, REQUEST_ID, 1))
                with self.endpoint(arm) as (url, proxy, owner):
                    deps = self.success(self.invoke(arm, url, 'readDependants', {'requestId': REQUEST_ID}))['items']
                    self.assertEqual([(True, True), (True, False), (False, False)],
                                     [(r['active'], r['continuationAck']) for r in deps])
                    artifact = self.success(self.invoke(arm, url, 'readArtifact', {'requestId': REQUEST_ID, 'materialRevision': '1'}))
                    for source, field in (('original_bytes', 'originalBytes'), ('envelope_bytes', 'envelopeBytes'), ('signature', 'signature')):
                        self.assertEqual(base64.b64encode(signed[source]).decode(), artifact[field])
                    self.assertEqual(signed['artifact_digest'], artifact['artifactDigest'])
                with self.endpoint(arm, 'requester-a2') as (url, proxy, owner):
                    self.success(self.invoke(arm, url, 'acknowledgeContinuation',
                        {'requestId': REQUEST_ID, 'expectedRevision': '7', 'dependentId': 'work-b'}, 'browser-ack-b'), 8)
                final = snapshot(self.path)
                self.assertEqual(['published', 'dependent-withdrawn', 'performed-reported', 'outcome-verified',
                                  'artifact-decided', 'resolved', 'continuation-acknowledged', 'continuation-acknowledged'],
                                 [r[3] for r in final['event']])
                self.assertEqual(list(range(1, 9)), [r[4] for r in final['event']])
                self.assertEqual((8, 'resolved', 8), (final['request'][0][2], final['request'][0][4], len(final['idem'])))

    def test_browser_changed_signature_is_refused_without_any_owner_effect(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.reset()
                body = {'objective': 'signed browser request'}
                signed = make_signed_artifact(workspace_id='workspace-a', request_id=REQUEST_ID,
                    material_revision=1, material_digest=material_digest(body), original_bytes=b'original bytes')
                data = {'requestId': REQUEST_ID, **body, **transport_fields(signed)}
                signature = bytearray(signed['signature']); signature[0] ^= 1
                data['artifactSignature'] = base64.b64encode(signature).decode()
                before = snapshot(self.path)
                with self.endpoint(arm) as (url, proxy, owner):
                    self.refusal(arm, self.invoke(arm, url, 'publish', data, 'bad-signature'))
                self.assertEqual(before, snapshot(self.path))
                with closing(sqlite3.connect(self.path)) as db:
                    self.assertEqual(0, db.execute('SELECT COUNT(*) FROM signed_artifact').fetchone()[0])

    def test_browser_large_cas_exact_result_changed_key_and_wire(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.reset()
                # Explicit synthetic large revision; no natural-growth claim.
                owner = OwnerOracle.connect_as(self.path, 'requester-a')
                try:
                    owner.publish(REQUEST_ID, {'objective': 'initial'}, ['work-a'], 'seed')
                finally:
                    owner.close()
                expected = 9007199254740995
                with closing(sqlite3.connect(self.path)) as db:
                    db.execute('UPDATE request SET revision=?,material_revision=?', (expected, expected)); db.commit()
                data = {'requestId': REQUEST_ID, 'expectedRevision': str(expected), 'objective': 'browser CAS'}
                with self.endpoint(arm) as (url, proxy, owner):
                    self.success(self.invoke(arm, url, 'revise', data, 'browser-cas'), expected + 1)
                    committed = snapshot(self.path)
                    self.success(self.invoke(arm, url, 'revise', data, 'browser-cas'), expected + 1)
                    self.refusal(arm, self.invoke(arm, url, 'revise', {**data, 'objective': 'changed'}, 'browser-cas'))
                    self.assertEqual(committed, snapshot(self.path))
                    rows = [r for r in proxy.wire if r['method'] != 'OPTIONS']
                    self.assertEqual(3, len(rows))
                    if arm.endswith('binary'):
                        from lifecycle_slice_checks import v1_pb2
                        for row in rows:
                            self.assertEqual(expected, v1_pb2.ReviseRequest.FromString(row['request']).expected_revision)
                        self.assertEqual(expected + 1, v1_pb2.MutationResult.FromString(rows[0]['response']).revision)
                    else:
                        for row in rows:
                            self.assertEqual(str(expected), json.loads(row['request'])['expectedRevision'])
                        self.assertEqual(str(expected + 1), json.loads(rows[0]['response'])['revision'])
                after = snapshot(self.path)
                self.assertEqual((expected + 1, 'revised', expected + 1),
                                 (after['request'][0][2], after['event'][-1][3], after['event'][-1][4]))
                self.assertEqual(2, len(after['event'])); self.assertEqual(2, len(after['idem']))

    def test_browser_postcommit_response_loss_reconciles_same_key(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.reset()
                owner = OwnerOracle.connect_as(self.path, 'requester-a')
                try:
                    owner.publish(REQUEST_ID, {'objective': 'initial'}, ['work-a'], 'seed')
                finally:
                    owner.close()
                data = {'requestId': REQUEST_ID, 'expectedRevision': '1', 'objective': 'lost browser response'}
                with self.endpoint(arm, fault_plan={('revise', 'browser-lost')}) as (url, proxy, owner):
                    result = self.invoke(arm, url, 'revise', data, 'browser-lost')
                    self.assertFalse(result['ok'], result)
                    if arm == 'http-v2':
                        self.assertEqual(0, result['status'])
                    else:
                        self.assertEqual(14, result['code'])  # Unavailable, not a refusal.
                    self.assertIn(('revise', 'browser-lost'), owner.fault_consumed)
                    committed = snapshot(self.path)
                    self.assertEqual(2, committed['request'][0][2])
                    self.assertEqual((2, 2), (len(committed['event']), len(committed['idem'])))
                    self.success(self.invoke(arm, url, 'revise', data, 'browser-lost'), 2)
                    self.refusal(arm, self.invoke(arm, url, 'revise', {**data, 'objective': 'changed'}, 'browser-lost'))
                    self.assertEqual(committed, snapshot(self.path))

    def test_browser_deadline_and_cancel_before_and_after_owner_commit(self):
        from transport_failure_checks import OwnerBoundaryGate
        for arm in ARMS:
            for phase in ('before', 'after'):
                for failure in ('deadline', 'cancel'):
                    with self.subTest(arm=arm, phase=phase, failure=failure):
                        self.reset()
                        owner = OwnerOracle.connect_as(self.path, 'requester-a')
                        try:
                            owner.publish(REQUEST_ID, {'objective': 'initial'}, ['work-a'], 'seed')
                        finally:
                            owner.close()
                        before = snapshot(self.path)
                        data = {'requestId': REQUEST_ID, 'expectedRevision': '1', 'objective': 'browser uncertain effect'}
                        with self.endpoint(arm) as (url, proxy, owner):
                            gate = OwnerBoundaryGate(owner.owner_call, phase, failure)
                            owner.owner_call = gate
                            try:
                                result = self.invoke(arm, url, 'revise', data, 'browser-timeout',
                                                     timeout=1000 if failure == 'deadline' else 5000,
                                                     cancel=1000 if failure == 'cancel' else 0)
                                self.assertTrue(gate.entered.wait(5), 'browser call missed owner boundary')
                                self.assertFalse(result['ok'], result)
                                if arm == 'http-v2':
                                    self.assertEqual(0, result['status'])
                                    self.assertEqual('TimeoutError' if failure == 'deadline' else 'AbortError', result['errorName'])
                                else:
                                    self.assertEqual(4 if failure == 'deadline' else 1, result['code'])
                                during = snapshot(self.path)
                                self.assertEqual(1 if phase == 'before' else 2, during['request'][0][2])
                                if phase == 'before':self.assertEqual(before, during)
                            finally:
                                gate.release.set()
                                self.assertTrue(gate.finished.wait(5), 'owner handler did not finish')
                                owner.owner_call = gate.original
                            self.assertIsNone(gate.error)
                            committed = snapshot(self.path)
                            self.assertEqual((2, 2, 2), (committed['request'][0][2], len(committed['event']), len(committed['idem'])))
                            self.success(self.invoke(arm, url, 'revise', data, 'browser-timeout'), 2)
                            self.refusal(arm, self.invoke(arm, url, 'revise', {**data, 'objective': 'changed'}, 'browser-timeout'))
                            self.assertEqual(committed, snapshot(self.path))

    def test_browser_presence_cursor_replay_principal_and_cors_refusal(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.reset()
                owner = OwnerOracle.connect_as(self.path, 'requester-a')
                try:
                    for i in range(5):owner.publish(str(9007199254741000 + i), {'objective': str(i)}, ['work-a'], 'seed-' + str(i))
                finally:
                    owner.close()
                before = snapshot(self.path)
                cursor = None
                with self.endpoint(arm) as (url, proxy, owner):
                    for route in ('watch', 'listRequests'):
                        method = route if arm == 'http-v2' else route + 'V2'
                        page = self.success(self.invoke(arm, url, method, {}))
                        key = 'events' if route == 'watch' else 'items'
                        if route == 'watch':
                            expected_rows = [{'seq': str(r[0]), 'requestId': r[2], 'action': r[3], 'revision': str(r[4])}
                                             for r in before['event']]
                            self.assertTrue(page['more'])
                        else:
                            expected_rows = [{'requestId': r[0], 'revision': str(r[2]),
                                'state': r[4] if arm == 'http-v2' else 'REQUEST_STATE_' + r[4].upper(),
                                'createdSeq': str(r[-1])} for r in before['request']]
                        self.assertEqual(expected_rows[:2], page[key])
                        for fields in ({'limit': 0}, {'limit': -1}, {'limit': 3}, {'cursor': ''}):
                            self.refusal(arm, self.invoke(arm, url, method, fields))
                        first_cursor = page['nextCursor']
                        next_page = self.success(self.invoke(arm, url, method, {'cursor': first_cursor, 'limit': 1}))
                        self.assertEqual(next_page, self.success(self.invoke(arm, url, method, {'cursor': first_cursor, 'limit': 1})))
                        self.assertEqual(expected_rows[2:3], next_page[key])
                        if route == 'watch':cursor = first_cursor
                    # A distinct real browser origin cannot preflight or invoke the owner.
                    with BrowserOrigin(self.bundle_path.read_bytes()) as foreign:
                        foreign_page = self.context.new_page()
                        try:
                            foreign_page.goto(foreign.base_url); foreign_page.wait_for_function('typeof ec00Rpc === "function"')
                            result = foreign_page.evaluate('''async a => {try {await fetch(a.url,{method:'POST',headers:{'content-type':'application/protobuf'},body:new Uint8Array()});return 'unexpected';}catch(e){return 'blocked';}}''', {'url': url + '/ec00.oa.v1.OwnerOperations/WatchV2'})
                            self.assertEqual('blocked', result)
                            self.assertTrue(any(r['method'] == 'OPTIONS' and not r['allowed'] for r in proxy.wire))
                        finally:
                            foreign_page.close()
                for principal in ('requester-a2', 'requester-b'):
                    with self.endpoint(arm, principal) as (url, proxy, owner):
                        self.refusal(arm, self.invoke(arm, url, 'watch' if arm == 'http-v2' else 'watchV2', {'cursor': cursor}))
                self.assertEqual(before, snapshot(self.path))
                with closing(sqlite3.connect(self.path)) as db:
                    db.execute("UPDATE identity SET active=0 WHERE principal_id='requester-a'"); db.commit()
                with self.endpoint(arm) as (url, proxy, owner):
                    self.refusal(arm, self.invoke(arm, url, 'watch' if arm == 'http-v2' else 'watchV2', {'cursor': cursor}))
                self.assertEqual(before, snapshot(self.path))


if __name__ == '__main__':
    unittest.main()
