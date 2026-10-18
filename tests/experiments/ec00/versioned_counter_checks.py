"""v2 decimal counters, simultaneous v1 support and explicit no-downgrade checks."""
from contextlib import closing
import base64
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from lifecycle_slice_checks import HERE, OwnerOracle, initialize_fixture, CandidateOwnerServer
from versioned_counter_http import VersionedCounterServer
sys.path.insert(0, str(HERE / "generated/typespec-v2/clients/python"))
from ec00_oa_v2_python_client.client import Client
from ec00_oa_v2_python_client.api.default import revise
from ec00_oa_v2_python_client.models import ReviseRequest

REQUEST_ID = "9007199254741199"
COUNTERS = (1, 9007199254740995, 9007199254740996, 9223372036854775806)


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("request", "event", "idem")}


def initialize_counter(path, expected):
    initialize_fixture(path)
    owner = OwnerOracle.connect_as(path, "requester-a")
    try:
        owner.publish(REQUEST_ID, {"objective": "initial"}, ["work-a"], "seed")
    finally:
        owner.close()
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE request SET revision=?,material_revision=?", (expected, expected))
        db.commit()


class CounterAssertions:
    def counter_case(self, arm, expected):
        with tempfile.TemporaryDirectory(prefix="ec00-versioned-counter-") as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_counter(path, expected)
            before = snapshot(path)
            with VersionedCounterServer(path) as server:
                if arm == "python":
                    with Client(base_url=server.base_url, headers={"Idempotency-Key": "v2-cas"}) as client:
                        body = ReviseRequest(request_id=REQUEST_ID, expected_revision=str(expected),
                                             objective="versioned counter effect")
                        self.assertEqual(str(expected), body.to_dict()["expectedRevision"])
                        first = revise.sync_detailed(REQUEST_ID, client=client, body=body)
                        replay = revise.sync_detailed(REQUEST_ID, client=client, body=body)
                        changed = revise.sync_detailed(REQUEST_ID, client=client, body=ReviseRequest(
                            request_id=REQUEST_ID, expected_revision=str(expected), objective="changed versioned effect"))
                    self.assertEqual((200, 200, 409), (first.status_code, replay.status_code, changed.status_code))
                    self.assertEqual(REQUEST_ID, replay.parsed.request_id)
                    result = {"requestId": first.parsed.request_id, "revision": first.parsed.revision,
                              "replayRevision": replay.parsed.revision, "changedStatus": changed.status_code}
                else:
                    candidates = HERE / "candidates"
                    env = {**os.environ, "EC00_BASE_URL": server.base_url, "EC00_EXPECTED_REVISION": str(expected)}
                    if arm == "typescript":
                        node = env.get("EC00_NODE")
                        self.assertTrue(node, "use the pinned EC00 Node toolchain")
                        env.update(NODE_PATH=str(candidates / "node_modules"),
                                   PATH=str(Path(node).parent) + os.pathsep + env["PATH"])
                        command = [str(candidates / "node_modules/.bin/tsx"), str(candidates / "run_versioned_counter_client.ts")]
                        cwd = candidates
                    elif arm == "go":
                        self.assertTrue(env.get("EC00_GO"), "use the pinned EC00 Go toolchain")
                        command, cwd = [env["EC00_GO"], "run", "./candidates/go-client-v2"], HERE
                    else:
                        self.assertTrue(env.get("EC00_CARGO"), "use the pinned EC00 Rust toolchain")
                        command = [env["EC00_CARGO"], "run", "--quiet", "--locked", "--manifest-path",
                                   str(candidates / "rust-client/Cargo.toml"), "--bin", "counter_v2"]
                        cwd = HERE
                    proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
                    self.assertEqual(0, proc.returncode, proc.stderr[-5000:])
                    result = json.loads(proc.stdout)
                    if arm == "typescript":
                        self.assertEqual(3, len(result["wire"]))
                        for row in result["wire"]:
                            self.assertEqual(str(expected), json.loads(row["request"])["expectedRevision"])
                        self.assertEqual(str(expected + 1), json.loads(result["wire"][0]["response"])["revision"])
                self.assertEqual(REQUEST_ID, result["requestId"])
                self.assertIsInstance(result["revision"], str)
                self.assertEqual((str(expected + 1), str(expected + 1), 409),
                                 (result["revision"], result["replayRevision"], result["changedStatus"]))
            after = snapshot(path)
            self.assertEqual(before["event"], after["event"][:-1])
            self.assertEqual((REQUEST_ID, "revised", expected + 1), tuple(after["event"][-1][i] for i in (2, 3, 4)))
            self.assertEqual((expected + 1, expected + 1, '{"objective":"versioned counter effect"}'),
                             tuple(after["request"][0][i] for i in (2, 3, 5)))
            self.assertEqual(2, len(after["idem"]))
            self.assertEqual(before["idem"], [r for r in after["idem"] if r[2] == "seed"])
            self.assertEqual(expected + 1, json.loads([r for r in after["idem"] if r[2] == "v2-cas"][0][-1])["revision"])


class VersionedCounterTests(CounterAssertions, unittest.TestCase):
    def test_generated_python_and_typescript_exact_requests_results_and_replay(self):
        for arm in ("python", "typescript"):
            for expected in COUNTERS:
                with self.subTest(arm=arm, expected=expected):
                    self.counter_case(arm, expected)

    def test_invalid_decimal_representations_refuse_without_owner_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_counter(path, 1)
            before = snapshot(path)
            with VersionedCounterServer(path) as server:
                for bad in (1, True, 1.0, "", "01", "+1", "-1", "1.0", "1e0", " 1", "1 ",
                            "9223372036854775808", "9" * 100, [], {}):
                    with self.subTest(value=repr(bad)):
                        response = httpx.patch(server.base_url + "/v2/requests/" + REQUEST_ID,
                            json={"requestId": REQUEST_ID, "objective": "invalid", "expectedRevision": bad},
                            headers={"Idempotency-Key": "bad"})
                        self.assertEqual(400, response.status_code)
                        self.assertEqual(before, snapshot(path))
                for body in ({}, {"expectedRevision": None}):
                    response = httpx.patch(server.base_url + "/v2/requests/" + REQUEST_ID,
                        json={"requestId": REQUEST_ID, "objective": "invalid", **body})
                    self.assertEqual(422, response.status_code)
                    self.assertEqual(before, snapshot(path))
                for value in ("0", "9223372036854775807"):
                    response = httpx.patch(server.base_url + "/v2/requests/" + REQUEST_ID,
                        json={"requestId": REQUEST_ID, "objective": "stale", "expectedRevision": value})
                    self.assertEqual(409, response.status_code)
                    self.assertEqual(before, snapshot(path))

    def test_v1_numeric_contract_and_v2_decimal_contract_coexist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_counter(path, 1)
            with VersionedCounterServer(path) as server:
                response = httpx.patch(server.base_url + "/v1/requests/" + REQUEST_ID,
                    json={"requestId": REQUEST_ID, "objective": "v1 update", "expectedRevision": 1},
                    headers={"Idempotency-Key": "v1"})
                self.assertEqual(200, response.status_code)
                self.assertIsInstance(response.json()["revision"], int)
                response = httpx.patch(server.base_url + "/v2/requests/" + REQUEST_ID,
                    json={"requestId": REQUEST_ID, "objective": "v2 update", "expectedRevision": "2"},
                    headers={"Idempotency-Key": "v2"})
                self.assertEqual((200, "3"), (response.status_code, response.json()["revision"]))

    def test_generated_python_full_lifecycle_preserves_artifact_and_owner_replay(self):
        from artifact_semantics import make_signed_artifact, transport_fields
        from owner_oracle import material_digest
        from candidate_owner_http import seed_evidence, persisted_artifact
        from ec00_oa_v2_python_client.api.default import (
            publish, withdraw_dependent, report_performed, verify, decide_artifact,
            resolve, acknowledge_continuation, read_dependants, read_artifact,
        )
        from ec00_oa_v2_python_client.models import (
            PublishRequest, WithdrawRequest, RevisionRequest, VerifyRequest,
            DecideArtifactRequest, AcknowledgeRequest,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_fixture(path)
            body = {"objective": "v2 lifecycle", "note": "original signed material"}
            signed = make_signed_artifact(workspace_id="workspace-a", request_id=REQUEST_ID,
                material_revision=1, material_digest=material_digest(body), original_bytes=b"\x00v2 exact artifact\xff")
            seed_evidence(path, evidence_ref="evidence:v2:performed", request_id=REQUEST_ID, body=body)
            fields = transport_fields(signed)
            def apply(principal, operation, function, data, revision, *, publish_call=False):
                key = "v2-" + operation
                with VersionedCounterServer(path, principal_id=principal,
                                             fault_plan={(operation, key)}) as server:
                    with Client(base_url=server.base_url, headers={"Idempotency-Key": key}) as client:
                        args = {"client": client, "body": data}
                        if not publish_call:
                            args["request_id"] = REQUEST_ID
                        with self.assertRaises(httpx.TransportError):
                            function(**args)
                        self.assertIn((operation, key), server.fault_consumed)
                        committed = snapshot(path)
                        self.assertEqual(revision, len(committed["event"]))
                        result = function(**args)
                        self.assertEqual((200, str(revision)), (result.status_code, result.parsed.revision))
                        self.assertEqual(committed, snapshot(path))
            apply("coordinator-a", "publish", publish.sync_detailed, PublishRequest(
                request_id=REQUEST_ID, objective=body["objective"], note=body["note"],
                dependants=["work-a", "work-b", "work-c"], artifact_digest=fields["artifactDigest"],
                artifact_key_id=fields["artifactKeyId"], artifact_bytes=fields["artifactBytes"],
                artifact_envelope=fields["artifactEnvelope"], artifact_signature=fields["artifactSignature"]), 1, publish_call=True)
            self.assertEqual((signed["original_bytes"], signed["envelope_bytes"], signed["signature"], signed["artifact_digest"]),
                             persisted_artifact(path, REQUEST_ID, 1))
            apply("requester-a2", "withdraw-dependent", withdraw_dependent.sync_detailed,
                  WithdrawRequest(dependent_id="work-c", expected_revision="1"), 2)
            apply("requester-a", "report-performed", report_performed.sync_detailed,
                  RevisionRequest(expected_revision="2"), 3)
            apply("verifier-a", "verify", verify.sync_detailed,
                  VerifyRequest(expected_revision="3", evidence_ref="evidence:v2:performed"), 4)
            apply("operator-a", "decide-artifact", decide_artifact.sync_detailed,
                  DecideArtifactRequest(expected_revision="4", artifact_digest=signed["artifact_digest"]), 5)
            apply("verifier-a", "resolve", resolve.sync_detailed, RevisionRequest(expected_revision="5"), 6)
            apply("requester-a", "ack-continuation", acknowledge_continuation.sync_detailed,
                  AcknowledgeRequest(dependent_id="work-a", expected_revision="6"), 7)
            with VersionedCounterServer(path) as server:
                with Client(base_url=server.base_url) as client:
                    deps = read_dependants.sync_detailed(REQUEST_ID, client=client).parsed.to_dict()["items"]
                    self.assertEqual([(True, True), (True, False), (False, False)],
                                     [(r["active"], r["continuationAck"]) for r in deps])
                    artifact = read_artifact.sync_detailed(REQUEST_ID, "1", client=client).parsed.to_dict()
                    for original, field in (("original_bytes", "originalBytes"), ("envelope_bytes", "envelopeBytes"),
                                            ("signature", "signature")):
                        self.assertEqual(base64.b64encode(signed[original]).decode(), artifact[field])
                    self.assertEqual(signed["artifact_digest"], artifact["artifactDigest"])
                    self.assertEqual(material_digest(body), artifact["materialDigest"])
            # A second dependent is a distinct command, not a replay of the first acknowledgement.
            with VersionedCounterServer(path, principal_id="requester-a2") as server:
                with Client(base_url=server.base_url, headers={"Idempotency-Key": "v2-ack-b"}) as client:
                    result = acknowledge_continuation.sync_detailed(REQUEST_ID, client=client,
                        body=AcknowledgeRequest(dependent_id="work-b", expected_revision="7"))
                    self.assertEqual((200, "8"), (result.status_code, result.parsed.revision))
            final = snapshot(path)
            self.assertEqual(["published", "dependent-withdrawn", "performed-reported", "outcome-verified",
                              "artifact-decided", "resolved", "continuation-acknowledged", "continuation-acknowledged"],
                             [r[3] for r in final["event"]])
            self.assertEqual(list(range(1, 9)), [r[4] for r in final["event"]])
            self.assertEqual((8, "resolved", 8), (final["request"][0][2], final["request"][0][4], len(final["idem"])))

    def test_frozen_v1_clients_interoperate_with_versioned_server(self):
        import lifecycle_slice_checks as original
        case = original.LifecycleSliceTests("test_frozen_old_and_current_clients_interoperate_for_common_publish_revise")
        result = unittest.TestResult()
        with patch.object(original, "CandidateOwnerServer", VersionedCounterServer):
            case.run(result)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)

    def test_same_logical_command_replays_across_versions_in_both_directions(self):
        from ec00_oa_python_client.client import Client as V1Client
        from ec00_oa_python_client.api.default import revise as v1_revise
        from ec00_oa_python_client.models import ReviseRequest as V1ReviseRequest
        for first_version in (1, 2):
            for expected in (1, 9007199254740995):
                with self.subTest(first_version=first_version, expected=expected):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "owner.sqlite"
                        initialize_counter(path, expected)
                        with VersionedCounterServer(path) as server:
                            def invoke(version, objective="one cross-version effect"):
                                if version == 1:
                                    with V1Client(base_url=server.base_url,
                                            headers={"Idempotency-Key": "shared-version-command"}) as client:
                                        return v1_revise.sync_detailed(REQUEST_ID, client=client,
                                            body=V1ReviseRequest(request_id=REQUEST_ID,
                                                expected_revision=expected, objective=objective))
                                with Client(base_url=server.base_url,
                                        headers={"Idempotency-Key": "shared-version-command"}) as client:
                                    return revise.sync_detailed(REQUEST_ID, client=client,
                                        body=ReviseRequest(request_id=REQUEST_ID,
                                            expected_revision=str(expected), objective=objective))
                            first = invoke(first_version)
                            self.assertEqual(200, first.status_code)
                            self.assertEqual(expected + 1 if first_version == 1 else str(expected + 1),
                                             first.parsed.revision)
                            committed = snapshot(path)
                            replay = invoke(3 - first_version)
                            self.assertEqual(200, replay.status_code)
                            self.assertEqual(expected + 1 if first_version == 2 else str(expected + 1),
                                             replay.parsed.revision)
                            self.assertEqual(committed, snapshot(path))
                            changed = invoke(3 - first_version, "different cross-version effect")
                            self.assertEqual(409, changed.status_code)
                            self.assertEqual(committed, snapshot(path))
                            self.assertEqual((2, 2, expected + 1),
                                (len(committed["event"]), len(committed["idem"]), committed["request"][0][2]))

    def test_v2_client_does_not_downgrade_when_only_v1_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_counter(path, 1)
            before = snapshot(path)
            with CandidateOwnerServer(path) as v1_only:
                with Client(base_url=v1_only.base_url) as client:
                    response = revise.sync_detailed(REQUEST_ID, client=client,
                        body=ReviseRequest(request_id=REQUEST_ID, expected_revision="1", objective="must not downgrade"))
                self.assertEqual(404, response.status_code)
            self.assertEqual(before, snapshot(path))


if __name__ == "__main__":
    unittest.main()
