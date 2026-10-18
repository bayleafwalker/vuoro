"""Observe exact owner CAS versus generated-client int64 representation limits."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from lifecycle_slice_checks import (
    CandidateOwnerServer, initialize_fixture, OwnerOracle, Client, revise,
    ReviseRequest, grpc, v1_pb2, v1_pb2_grpc, HERE,
)


class LargeCasTests(unittest.TestCase):
    def snapshot(self, path):
        with closing(sqlite3.connect(path)) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("request", "event", "idem")}

    def run_case(self, arm, expected):
        with tempfile.TemporaryDirectory(prefix="ec00-large-cas-") as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_fixture(path)
            owner = OwnerOracle.connect_as(path, "requester-a")
            request_id = "9007199254741199"
            try:
                owner.publish(request_id, {"objective": "initial"}, ["work-a"], "seed")
            finally:
                owner.close()
            # Synthetic counter fixture, not a claim of trillions of observed transitions.
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE request SET revision=?,material_revision=?", (expected, expected))
                db.commit()
            before = self.snapshot(path)
            with CandidateOwnerServer(path) as server:
                if arm.startswith("typescript"):
                    node = os.environ.get("EC00_NODE")
                    self.assertTrue(node, "run in the pinned EC00 toolchain")
                    candidates = HERE / "candidates"
                    env = {**os.environ, "PATH": str(Path(node).parent) + os.pathsep + os.environ["PATH"],
                           "NODE_PATH": str(candidates / "node_modules"),
                           "EC00_BASE_URL": server.base_url, "EC00_EXPECTED_REVISION": str(expected),
                           "EC00_CAS_ARM": "openapi" if arm.endswith("openapi") else "protobuf"}
                    proc = subprocess.run([str(candidates / "node_modules/.bin/tsx"),
                                           str(candidates / "run_large_cas_clients.ts")],
                                          cwd=candidates, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(0, proc.returncode, proc.stderr)
                    result = json.loads(proc.stdout)
                elif arm == "python-openapi":
                    with Client(base_url=server.base_url, headers={"Idempotency-Key": "large-cas"}) as client:
                        response = revise.sync_detailed(request_id, client=client,
                            body=ReviseRequest(request_id=request_id, expected_revision=expected, objective="large CAS effect"))
                    result = {"status": response.status_code, "clientRevision": str(response.parsed.revision)}
                else:
                    with grpc.insecure_channel(server.grpc_target) as channel:
                        response = v1_pb2_grpc.OwnerOperationsStub(channel).Revise(
                            v1_pb2.ReviseRequest(request_id=request_id, expected_revision=expected,
                                                objective="large CAS effect"),
                            metadata=(("idempotency-key", "large-cas"),), timeout=5)
                    result = {"status": 200, "clientRevision": str(response.revision)}
            after = self.snapshot(path)
            if arm == "typescript-openapi":
                sent = json.loads(result["wireRequest"])["expectedRevision"]
                if expected == 9007199254740995:
                    self.assertEqual(9007199254740996, sent)
                    self.assertEqual(409, result["status"])
                    self.assertEqual(before, after)
                    self.assertIsNone(result["clientRevision"])
                    return
                self.assertEqual(expected, sent)
                self.assertEqual(expected + 1, json.loads(result["wireResponse"])["revision"])
                if expected == 9007199254740996:
                    # The owner committed correctly; generated TS parsed the result imprecisely.
                    self.assertEqual("9007199254740996", result["clientRevision"])
                else:
                    self.assertEqual(str(expected + 1), result["clientRevision"])
            else:
                self.assertEqual(str(expected + 1), result["clientRevision"])
                if arm == "typescript-protobuf":
                    self.assertEqual(str(expected), result["sentRevision"])
                    self.assertEqual(request_id, result["requestId"])
            self.assertEqual(200, result["status"])
            row = after["request"][0]
            self.assertEqual((expected + 1, expected + 1, '{"objective":"large CAS effect"}'),
                             (row[2], row[3], row[5]))
            self.assertEqual(before["event"], after["event"][:-1])
            self.assertEqual((request_id, "revised", expected + 1),
                             tuple(after["event"][-1][i] for i in (2, 3, 4)))
            self.assertEqual(before["idem"], [r for r in after["idem"] if r[2] == "seed"])
            committed = [r for r in after["idem"] if r[2] == "large-cas"]
            self.assertEqual(1, len(committed))
            self.assertEqual(expected + 1, json.loads(committed[0][-1])["revision"])

    def test_safe_and_unsafe_revision_requests_and_results(self):
        for arm in ("python-openapi", "python-grpc", "typescript-openapi", "typescript-protobuf"):
            for expected in (1, 9007199254740995, 9007199254740996):
                with self.subTest(arm=arm, expected=expected):
                    self.run_case(arm, expected)


if __name__ == "__main__":
    unittest.main()
