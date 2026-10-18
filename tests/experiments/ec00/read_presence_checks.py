"""Explicit read identities preserve presence without changing legacy RPC behavior."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from lifecycle_slice_checks import HERE, initialize_fixture, OwnerOracle, grpc, v1_pb2, v1_pb2_grpc
from versioned_counter_checks import snapshot, Client
from versioned_counter_http import VersionedCounterServer
from candidate_owner_http import _GrpcOwner
from owner_oracle import ResponseLost
from ec00_oa_v2_python_client.api.default import list_requests, watch
from ec00_oa_v2_python_client.types import UNSET

FROZEN = HERE / 'compat/read-server-5d13'
MANIFEST_SHA = '4bf23960f4ac6c42ab0a17ff3ab35637ab6d286852f63d5f5288f6c8abde1ad7'
SOURCE_COMMIT = '5d13baf05b1d90348c57481cb1cfc35a620cf615'


def verified_fixture(path=FROZEN):
    raw = (path / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise AssertionError('frozen read manifest differs from reviewed pin')
    manifest = json.loads(raw)
    if manifest['source_commit'] != SOURCE_COMMIT:
        raise AssertionError('wrong frozen source commit')
    for entry in manifest['source_files'] + manifest['runner_files']:
        if hashlib.sha256((path / entry['path']).read_bytes()).hexdigest() != entry['sha256']:
            raise AssertionError('frozen read source hash mismatch: ' + entry['path'])
    return manifest


@contextmanager
def old_server(path):
    verified_fixture()
    process = subprocess.Popen([sys.executable, str(FROZEN / 'serve_old_reads.py'), str(path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not select.select([process.stdout], [], [], 10)[0]:
            raise AssertionError('frozen server readiness timed out')
        line = process.stdout.readline()
        if not line:
            raise AssertionError('frozen server exited before readiness')
        yield json.loads(line)
    finally:
        try:
            stdout, stderr = process.communicate('\n', timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AssertionError('frozen server did not stop')
        if process.returncode:
            raise AssertionError('frozen server failed: ' + stderr[-3000:])


def project(page, route):
    if route == 'watch':
        result = {'events': [{'seq': str(e.seq), 'requestId': e.request_id,
                             'action': e.action, 'revision': str(e.revision)} for e in page.events],
                  'more': page.more}
    else:
        result = {'items': [{'requestId': e.request_id, 'revision': str(e.revision),
                            'state': {1: 'open', 2: 'resolved'}[e.state],
                            'createdSeq': str(e.created_seq)} for e in page.items]}
    result['nextCursor'] = page.next_cursor if page.HasField('next_cursor') else None
    return result


class ReadPresenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ec00-read-presence-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'owner.sqlite'
        initialize_fixture(self.path)
        for i in range(5):
            owner = OwnerOracle.connect_as(self.path, 'requester-a')
            try:
                owner.publish(str(9007199254741000 + i), {'objective': f'event {i}'}, ['work-a'], f'seed-{i}')
            finally:
                owner.close()

    def native(self, server, route, **fields):
        with grpc.insecure_channel(server.grpc_target) as channel:
            stub = v1_pb2_grpc.OwnerOperationsStub(channel)
            method = stub.WatchV2 if route == 'watch' else stub.ListRequestsV2
            return method(v1_pb2.PageRequestV2(**fields), timeout=5)

    def http(self, server, route, **fields):
        with Client(base_url=server.base_url) as client:
            return (watch if route == 'watch' else list_requests).sync_detailed(
                client=client, cursor=fields.get('cursor', UNSET), limit=fields.get('limit', UNSET))

    def refuse(self, server, route, **fields):
        before = snapshot(self.path)
        self.assertEqual(409, self.http(server, route, **fields).status_code)
        with self.assertRaises(grpc.RpcError) as caught:
            self.native(server, route, **fields)
        self.assertEqual(grpc.StatusCode.ABORTED, caught.exception.code())
        self.assertEqual(before, snapshot(self.path))

    def test_presence_defaults_zero_empty_and_bounds(self):
        before = snapshot(self.path)
        with VersionedCounterServer(self.path) as server:
            for route in ('list-requests', 'watch'):
                for fields in ({}, {'limit': 1}, {'limit': 2}):
                    with self.subTest(route=route, fields=fields):
                        request = v1_pb2.PageRequestV2(**fields)
                        decoded = v1_pb2.PageRequestV2.FromString(request.SerializeToString())
                        self.assertEqual('limit' in fields, decoded.HasField('limit'))
                        self.assertFalse(decoded.HasField('cursor'))
                        page = project(self.native(server, route, **fields), route)
                        response = self.http(server, route, **fields)
                        self.assertEqual(200, response.status_code)
                        self.assertEqual(page, response.parsed.to_dict())
                        self.assertEqual(fields.get('limit', 2), len(page.get('items', page.get('events'))))
                for fields in ({'limit': 0}, {'limit': -1}, {'limit': 3}, {'cursor': ''}):
                    with self.subTest(route=route, fields=fields):
                        decoded = v1_pb2.PageRequestV2.FromString(v1_pb2.PageRequestV2(**fields).SerializeToString())
                        for key in fields:
                            self.assertTrue(decoded.HasField(key))
                            self.assertEqual(fields[key], getattr(decoded, key))
                        self.refuse(server, route, **fields)
        self.assertEqual(before, snapshot(self.path))

    def test_reconnect_same_cursor_complete_sql_rows_and_refusals(self):
        before = snapshot(self.path)
        cursors = {}
        with VersionedCounterServer(self.path) as server:
            for route in ('list-requests', 'watch'):
                cursors[route] = self.native(server, route).next_cursor
        # Reopen the same owner file; no in-process cursor/session authority.
        with VersionedCounterServer(self.path) as server:
            for route in ('list-requests', 'watch'):
                cursor = cursors[route]
                first = project(self.native(server, route), route)
                rows = list(first.get('items', first.get('events')))
                for _ in range(4):
                    native = project(self.native(server, route, cursor=cursor), route)
                    self.assertEqual(native, self.http(server, route, cursor=cursor).parsed.to_dict())
                    self.assertEqual(native, project(self.native(server, route, cursor=cursor), route))
                    entries = native.get('items', native.get('events'))
                    self.assertLessEqual(len(entries), 2)
                    rows.extend(entries)
                    if not native['nextCursor'] or not entries:
                        break
                    cursor = native['nextCursor']
                if route == 'watch':
                    expected = [{'seq': str(r[0]), 'requestId': r[2], 'action': r[3], 'revision': str(r[4])}
                                for r in before['event']]
                else:
                    expected = [{'requestId': r[0], 'revision': str(r[2]), 'state': r[4], 'createdSeq': str(r[-1])}
                                for r in before['request']]
                self.assertEqual(expected, rows)
                raw = bytearray(base64.urlsafe_b64decode(cursors[route])); raw[-1] ^= 1
                self.refuse(server, route, cursor=base64.urlsafe_b64encode(raw).decode())
                self.refuse(server, route, cursor=cursors['watch' if route == 'list-requests' else 'list-requests'])
        for principal in ('requester-a2', 'requester-b'):
            with VersionedCounterServer(self.path, principal_id=principal) as server:
                for route in cursors:
                    self.refuse(server, route, cursor=cursors[route])
        self.assertEqual(before, snapshot(self.path))
        owner = OwnerOracle.connect_as(self.path, 'coordinator-a')
        try:
            owner.compact_events_through(5)
        finally:
            owner.close()
        with VersionedCounterServer(self.path) as server:
            self.refuse(server, 'watch', cursor=cursors['watch'])

    def test_response_loss_replay_and_revocation_refusal(self):
        before = snapshot(self.path)
        with VersionedCounterServer(self.path) as server:
            original = server.httpd.owner_call
            for route in ('watch', 'list-requests'):
                cursor = self.native(server, route).next_cursor
                expected = self.native(server, route, cursor=cursor)
                for phase in ('before', 'after'):
                    calls = []
                    def lost(operation, *args, **kwargs):
                        calls.append(operation)
                        if phase == 'after':
                            original(operation, *args, **kwargs)
                        raise ResponseLost('injected read response loss')
                    server.httpd.owner_call = lost
                    try:
                        with self.assertRaises(grpc.RpcError) as caught:
                            self.native(server, route, cursor=cursor)
                        self.assertEqual(grpc.StatusCode.UNAVAILABLE, caught.exception.code())
                    finally:
                        server.httpd.owner_call = original
                    self.assertEqual(['watch' if route == 'watch' else 'list_requests'], calls)
                    self.assertEqual(expected, self.native(server, route, cursor=cursor))
                    self.assertEqual(before, snapshot(self.path))
            from contextlib import closing
            import sqlite3
            with closing(sqlite3.connect(self.path)) as db:
                db.execute("UPDATE identity SET active=0 WHERE principal_id='requester-a'")
                db.commit()
            for route in ('watch', 'list-requests'):
                self.refuse(server, route)

    def test_presence_oracle_rejects_legacy_defaulting_in_new_handler(self):
        def broken(adapter, operation, request, context):
            return adapter._call(operation, request.cursor or None, limit=request.limit or 2)
        with VersionedCounterServer(self.path) as server:
            with patch.object(_GrpcOwner, '_read_v2', broken):
                for route in ('watch', 'list-requests'):
                    for fields in ({'limit': 0}, {'cursor': ''}):
                        with self.subTest(route=route, fields=fields):
                            with self.assertRaises(AssertionError):
                                self.refuse(server, route, **fields)

    def test_frozen_old_client_server_and_no_automatic_downgrade(self):
        verified_fixture()
        before = snapshot(self.path)
        with old_server(self.path) as old:
            old_default = self.frozen_client(old['grpc_target'], 'default')
            old_zero = self.frozen_client(old['grpc_target'], 'legacy-empty-zero')
            for key in ('listHex', 'watchHex'):
                self.assertEqual(old_default[key], old_zero[key])
            with grpc.insecure_channel(old['grpc_target']) as channel:
                stub = v1_pb2_grpc.OwnerOperationsStub(channel)
                for method in (stub.ListRequestsV2, stub.WatchV2):
                    with self.assertRaises(grpc.RpcError) as caught:
                        method(v1_pb2.PageRequestV2(), timeout=5)
                    self.assertEqual(grpc.StatusCode.UNIMPLEMENTED, caught.exception.code())
            # Server snapshot is unchanged after unsupported calls; no fallback mutation.
            self.assertEqual(before, snapshot(self.path))
        with VersionedCounterServer(self.path) as server:
            for mode in ('default', 'legacy-empty-zero'):
                current = self.frozen_client(server.grpc_target, mode)
                for key in ('listHex', 'watchHex'):
                    self.assertEqual(old_default[key], current[key])
            with grpc.insecure_channel(server.grpc_target) as channel:
                stub = v1_pb2_grpc.OwnerOperationsStub(channel)
                for route, method, key in (('watch', stub.Watch, 'watchHex'),
                                            ('list-requests', stub.ListRequests, 'listHex')):
                    for request in (v1_pb2.PageRequest(), v1_pb2.PageRequest(cursor='', limit=0)):
                        self.assertEqual(old_default[key], method(request, timeout=5).SerializeToString().hex())
                    self.assertEqual(old_default[key], self.native(server, route).SerializeToString().hex())
                    self.refuse(server, route, limit=0)
                    self.refuse(server, route, cursor='')
        self.assertEqual(before, snapshot(self.path))

    def frozen_client(self, target, mode):
        result = subprocess.run([sys.executable, str(FROZEN / 'old_read_client.py'), target, mode],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_frozen_fixture_pin_rejects_drift_and_coordinated_rehash(self):
        verified_fixture()
        path = Path(self.temp.name) / 'frozen'
        shutil.copytree(FROZEN, path)
        source = path / 'source/candidate_owner_http.py'
        source.write_bytes(source.read_bytes() + b'\n# drift\n')
        with self.assertRaisesRegex(AssertionError, 'source hash mismatch'):
            verified_fixture(path)
        manifest = json.loads((path / 'manifest.json').read_bytes())
        manifest['source_files'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        (path / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(AssertionError, 'reviewed pin'):
            verified_fixture(path)

    def test_generated_typescript_binary_http_presence_and_http_v2(self):
        before = snapshot(self.path)
        candidates = HERE / 'candidates'
        node = os.environ.get('EC00_NODE')
        self.assertTrue(node, 'use pinned EC00 Node toolchain')
        env = {**os.environ, 'NODE_PATH': str(candidates / 'node_modules'),
               'PATH': str(Path(node).parent) + os.pathsep + os.environ['PATH']}
        with VersionedCounterServer(self.path) as server:
            proc = subprocess.run([str(candidates / 'node_modules/.bin/tsx'),
                                   str(candidates / 'run_read_presence_client.ts'), server.base_url],
                                  cwd=candidates, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, proc.returncode, proc.stderr)
            rows = json.loads(proc.stdout)
            self.assertEqual(12, len(rows))
            self.assertEqual({(r, c) for r in ('list-requests', 'watch')
                              for c in ('omitted', 'zero', 'empty', 'one', 'negative', 'oversized')},
                             {(row['route'], row['case']) for row in rows})
            for row in rows:
                case, route = row['case'], row['route']
                with self.subTest(case=case, route=route):
                    request = v1_pb2.PageRequestV2.FromString(bytes.fromhex(row['requestHex']))
                    fields = {'omitted': {}, 'zero': {'limit': 0}, 'empty': {'cursor': ''},
                              'one': {'limit': 1}, 'negative': {'limit': -1}, 'oversized': {'limit': 3}}
                    self.assertEqual(v1_pb2.PageRequestV2(**fields[case]), request)
                    self.assertEqual(case == 'empty', request.HasField('cursor'))
                    self.assertEqual(case not in ('empty', 'omitted'), request.HasField('limit'))
                    self.assertEqual(request.HasField('cursor'), row['decodedCursorPresent'])
                    self.assertEqual(request.HasField('limit'), row['decodedLimitPresent'])
                    if case in ('omitted', 'one'):
                        self.assertEqual((200, 200), (row['protoStatus'], row['httpStatus']))
                        cls = v1_pb2.WatchPage if route == 'watch' else v1_pb2.RequestPage
                        page = cls.FromString(bytes.fromhex(row['protoHex']))
                        self.assertEqual(project(page, route), row['httpBody'])
                        self.assertEqual(1 if case == 'one' else 2, len(page.events if route == 'watch' else page.items))
                    else:
                        self.assertEqual((409, 409), (row['protoStatus'], row['httpStatus']))
            for route in ('watch', 'list-requests'):
                with patch.object(server.httpd, 'owner_call', wraps=server.httpd.owner_call) as invoked:
                    response = httpx.post(server.base_url + '/proto/v2/' + route, content=b'\x80', timeout=5)
                    self.assertEqual(400, response.status_code)
                    invoked.assert_not_called()
        self.assertEqual(before, snapshot(self.path))


if __name__ == '__main__':
    unittest.main()
