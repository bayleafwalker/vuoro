"""Native generated Go/Rust presence, refusal, and exact wire checks (pinned Nix)."""
import json
import os
import subprocess
import unittest
import read_presence_checks as primary
from read_presence_checks import HERE, v1_pb2, VersionedCounterServer, snapshot


class NativeReadPresenceTests(unittest.TestCase):
    setUp = primary.ReadPresenceTests.setUp
    native = primary.ReadPresenceTests.native

    def test_generated_native_clients(self):
        before = snapshot(self.path)
        for arm in ('go', 'rust'):
            with self.subTest(arm=arm):
                with VersionedCounterServer(self.path) as server:
                    if arm == 'go':
                        self.assertTrue(os.environ.get('EC00_GO'))
                        command = [os.environ['EC00_GO'], 'run', './candidates/go-read-presence', server.grpc_target]
                    else:
                        self.assertTrue(os.environ.get('EC00_CARGO'))
                        command = [os.environ['EC00_CARGO'], 'run', '--quiet', '--locked', '--manifest-path',
                                   str(HERE / 'candidates/rust-client/Cargo.toml'), '--bin', 'read_presence', '--', server.grpc_target]
                    result = subprocess.run(command, cwd=HERE, capture_output=True, text=True, timeout=300)
                    self.assertEqual(0, result.returncode, result.stderr[-5000:])
                    rows = json.loads(result.stdout)
                    self.assertEqual(12, len(rows))
                    fields = {'omitted': {}, 'one': {'limit': 1}, 'zero': {'limit': 0},
                              'empty': {'cursor': ''}, 'negative': {'limit': -1}, 'oversized': {'limit': 3}}
                    self.assertEqual({(r, c) for r in ('list-requests', 'watch') for c in fields},
                                     {(r['route'], r['case']) for r in rows})
                    for row in rows:
                        with self.subTest(arm=arm, route=row['route'], case=row['case']):
                            expected = v1_pb2.PageRequestV2(**fields[row['case']])
                            actual = v1_pb2.PageRequestV2.FromString(bytes.fromhex(row['requestHex']))
                            self.assertEqual(expected, actual)
                            self.assertEqual(expected.HasField('limit'), actual.HasField('limit'))
                            self.assertEqual(expected.HasField('cursor'), actual.HasField('cursor'))
                            if row['case'] in ('omitted', 'one'):
                                self.assertEqual('OK', row['status'])
                                cls = v1_pb2.WatchPage if row['route'] == 'watch' else v1_pb2.RequestPage
                                page = cls.FromString(bytes.fromhex(row['responseHex']))
                                python = self.native(server, row['route'], **fields[row['case']])
                                self.assertEqual(python, page)
                                self.assertEqual(1 if row['case'] == 'one' else 2,
                                                 len(page.events if row['route'] == 'watch' else page.items))
                            else:
                                self.assertEqual('Aborted', row['status'])
                                self.assertIsNone(row['responseHex'])
                self.assertEqual(before, snapshot(self.path))


if __name__ == '__main__':
    unittest.main()
