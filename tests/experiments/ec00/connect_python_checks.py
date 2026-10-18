"""Generated Python Connect clients on the qualified direct/forwarding compositions."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest

from lifecycle_slice_checks import HERE
from versioned_counter_checks import initialize_counter, snapshot, REQUEST_ID
from versioned_counter_http import VersionedCounterServer
from connect_owner_http import DirectConnectServer
from connect_browser_checks import node_edge
from ec00_connect.oa_v1_connect import OwnerOperationsClientSync, ReviseRequest
from connectrpc.compat import google_protobuf_binary_codec, google_protobuf_json_codec
from connectrpc.errors import ConnectError
from connectrpc.code import Code


class GeneratedPythonConnectTests(unittest.TestCase):
    def test_both_codecs_and_compositions_preserve_counter_replay_and_refusal(self):
        for arm in ('direct', 'edge'):
            for codec in (google_protobuf_json_codec, google_protobuf_binary_codec):
                for expected in (1, 9007199254740995):
                    with self.subTest(arm=arm, codec=codec.__name__, expected=expected):
                        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                            path = Path(directory) / 'owner.sqlite'
                            initialize_counter(path, expected)
                            if arm == 'direct':
                                server = stack.enter_context(DirectConnectServer(path)); url = server.base_url
                            else:
                                server = stack.enter_context(VersionedCounterServer(path))
                                url = stack.enter_context(node_edge(server.grpc_target))
                            # Explicitly match the uncompressed unary browser fixture; gzip remains unqualified.
                            client = stack.enter_context(OwnerOperationsClientSync(url, codec=codec(),
                                send_compression=None, accept_compression=[], timeout_ms=5000))
                            request = ReviseRequest(request_id=REQUEST_ID, expected_revision=expected,
                                                    objective='generated Python Connect')
                            result = client.revise(request, headers={'idempotency-key': 'python-connect'})
                            self.assertEqual((REQUEST_ID, expected + 1), (result.request_id, result.revision))
                            committed = snapshot(path)
                            replay = client.revise(request, headers={'idempotency-key': 'python-connect'})
                            self.assertEqual(result, replay)
                            with self.assertRaises(ConnectError) as caught:
                                client.revise(ReviseRequest(request_id=REQUEST_ID, expected_revision=expected,
                                    objective='changed'), headers={'idempotency-key': 'python-connect'})
                            self.assertEqual(Code.ABORTED, caught.exception.code)
                            self.assertEqual(committed, snapshot(path))
                            self.assertEqual((expected + 1, 2, 2), (committed['request'][0][2],
                                             len(committed['event']), len(committed['idem'])))
                            self.assertEqual((REQUEST_ID, 'revised', expected + 1),
                                             tuple(committed['event'][-1][i] for i in (2, 3, 4)))


if __name__ == '__main__':
    unittest.main()
