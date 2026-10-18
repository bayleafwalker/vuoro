"""Executable proto/OpenAPI client slice against one persisted owner oracle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests/experiments/ec00/generated/protobuf/python"))
sys.path.insert(0, str(ROOT / "tests/experiments/ec00/generated/typespec/clients/python"))

from oa import v1_pb2
from ec00_oa_python_client.api.default import publish, revise
from ec00_oa_python_client.client import Client
from ec00_oa_python_client.models import PublishRequest, ReviseRequest
from ec00_oa_python_client.types import UNSET
from candidate_owner_http import CandidateOwnerServer, initialize_fixture, persisted_request


def _protobuf_call(url: str, method: str, message: object, key: str) -> tuple[int, bytes]:
    data = message.SerializeToString()
    request = Request(url, data=data, method=method, headers={
        "Content-Type": "application/protobuf", "Idempotency-Key": key,
    })
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


class CandidateSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ec00-candidate-")
        self.db_path = Path(self.temp.name) / "owner.sqlite"
        initialize_fixture(self.db_path)
        self.server = CandidateOwnerServer(self.db_path)
        self.server.__enter__()

    def tearDown(self) -> None:
        self.server.__exit__(None, None, None)
        self.temp.cleanup()

    def test_generated_python_clients_preserve_presence_large_id_and_owner_cas(self) -> None:
        missing_objective = v1_pb2.PublishRequest(request_id="9007199254740992")
        status, _ = _protobuf_call(
            self.server.base_url + "/proto/v1/requests", "POST", missing_objective,
            "py-proto-publish-missing-objective",
        )
        self.assertEqual(422, status)
        empty_objective_id = "9007199254740994"
        empty_objective = v1_pb2.PublishRequest(request_id=empty_objective_id, objective="")
        self.assertTrue(empty_objective.HasField("objective"))
        status, _ = _protobuf_call(
            self.server.base_url + "/proto/v1/requests", "POST", empty_objective,
            "py-proto-publish-empty-objective",
        )
        self.assertEqual(200, status)
        self.assertEqual({"objective": ""}, json.loads(persisted_request(self.db_path, empty_objective_id)[1]))

        proto_null_note_id = "9007199254740998"
        status, _ = _protobuf_call(
            self.server.base_url + "/proto/v1/requests", "POST",
            v1_pb2.PublishRequest(request_id=proto_null_note_id, objective="protobuf null note",
                                  note_null=0),
            "py-proto-publish-null-note",
        )
        self.assertEqual(200, status)
        self.assertEqual({"objective": "protobuf null note", "note": None},
                         json.loads(persisted_request(self.db_path, proto_null_note_id)[1]))

        proto_id = "9007199254740993"
        status, raw = _protobuf_call(
            self.server.base_url + "/proto/v1/requests", "POST",
            v1_pb2.PublishRequest(request_id=proto_id, objective="protobuf Python publish"),
            "py-proto-publish-large",
        )
        self.assertEqual(200, status)
        result = v1_pb2.MutationResult.FromString(raw)
        self.assertEqual((proto_id, 1), (result.request_id, result.revision))
        stored = persisted_request(self.db_path, proto_id)
        self.assertIsNotNone(stored)
        self.assertEqual({"objective": "protobuf Python publish"}, json.loads(stored[1]))

        for expected, status_expected, label in ((None, 422, "absent"), (0, 409, "zero")):
            message = v1_pb2.ReviseRequest(request_id=proto_id, objective=f"protobuf {label}")
            if expected is not None:
                message.expected_revision = expected
            status, _ = _protobuf_call(
                self.server.base_url + f"/proto/v1/requests/{proto_id}", "PATCH", message,
                f"py-proto-revise-{label}",
            )
            self.assertEqual(status_expected, status)
        good = v1_pb2.ReviseRequest(request_id=proto_id, expected_revision=1,
                                    objective="protobuf Python revision", note="")
        status, raw = _protobuf_call(
            self.server.base_url + f"/proto/v1/requests/{proto_id}", "PATCH", good,
            "py-proto-revise-valid",
        )
        self.assertEqual(200, status)
        self.assertEqual(2, v1_pb2.MutationResult.FromString(raw).revision)
        self.assertEqual({"objective": "protobuf Python revision", "note": ""},
                         json.loads(persisted_request(self.db_path, proto_id)[1]))

        http_id = "9007199254740995"
        py_client = Client(base_url=self.server.base_url,
                           headers={"Idempotency-Key": "py-http-publish-large"},
                           raise_on_unexpected_status=False)
        request = PublishRequest(request_id=http_id, objective="OpenAPI Python publish", note=None)
        self.assertIs(UNSET, PublishRequest(request_id="small", objective="unset").note)
        self.assertNotIn("note", PublishRequest(request_id="small", objective="unset").to_dict())
        self.assertIsNone(request.to_dict()["note"])
        response = publish.sync_detailed(client=py_client, body=request)
        self.assertEqual(200, response.status_code)
        self.assertEqual(http_id, response.parsed.request_id)
        self.assertEqual({"objective": "OpenAPI Python publish", "note": None},
                         json.loads(persisted_request(self.db_path, http_id)[1]))
        http_empty_note_id = "9007199254740999"
        empty_note = publish.sync_detailed(
            client=Client(base_url=self.server.base_url,
                          headers={"Idempotency-Key": "py-http-publish-note-empty"},
                          raise_on_unexpected_status=False),
            body=PublishRequest(request_id=http_empty_note_id, objective="OpenAPI Python empty note", note=""),
        )
        self.assertEqual(200, empty_note.status_code)
        self.assertEqual({"objective": "OpenAPI Python empty note", "note": ""},
                         json.loads(persisted_request(self.db_path, http_empty_note_id)[1]))
        http_absent_note_id = "9007199254740997"
        absent_note_client = Client(base_url=self.server.base_url,
                                    headers={"Idempotency-Key": "py-http-publish-note-unset"},
                                    raise_on_unexpected_status=False)
        absent_note = publish.sync_detailed(
            client=absent_note_client,
            body=PublishRequest(request_id=http_absent_note_id, objective="OpenAPI Python absent note"),
        )
        self.assertEqual(200, absent_note.status_code)
        self.assertEqual({"objective": "OpenAPI Python absent note"},
                         json.loads(persisted_request(self.db_path, http_absent_note_id)[1]))

        for expected, status_expected, label in ((UNSET, 422, "absent"), (None, 422, "null"), (0, 409, "zero")):
            client = Client(base_url=self.server.base_url,
                            headers={"Idempotency-Key": f"py-http-revise-{label}"},
                            raise_on_unexpected_status=False)
            body = ReviseRequest(request_id=http_id, objective=f"OpenAPI {label}", expected_revision=expected)
            result = revise.sync_detailed(http_id, client=client, body=body)
            self.assertEqual(status_expected, result.status_code)
        valid_client = Client(base_url=self.server.base_url,
                              headers={"Idempotency-Key": "py-http-revise-valid"},
                              raise_on_unexpected_status=False)
        valid = revise.sync_detailed(http_id, client=valid_client,
                                     body=ReviseRequest(request_id=http_id, objective="OpenAPI Python revision",
                                                        expected_revision=1, note=None))
        self.assertEqual((200, 2), (valid.status_code, valid.parsed.revision))
        self.assertEqual({"objective": "OpenAPI Python revision", "note": None},
                         json.loads(persisted_request(self.db_path, http_id)[1]))

    def test_generated_typescript_clients_reach_same_persisted_oracle(self) -> None:
        node = os.environ.get("EC00_NODE")
        if not node:
            self.fail("EC00_NODE must identify pinned Node 24.21.0; see experiment README")
        candidates = HERE / "candidates"
        tsx = candidates / "node_modules/.bin/tsx"
        self.assertTrue(tsx.exists(), "run npm ci in candidates first")
        env = dict(os.environ, EC00_BASE_URL=self.server.base_url,
                   NODE_PATH=str(candidates / "node_modules"),
                   PATH=f"{Path(node).resolve().parent}:{candidates / 'node_modules/.bin'}:{os.environ['PATH']}")
        result = subprocess.run([str(tsx), str(candidates / "run_typescript_clients.ts")],
                                cwd=candidates, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + "\n" + result.stderr)
        output = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(("9007199254740993", 2), (output["protoId"], output["protoRevision"]))
        self.assertEqual(("9007199254740995", 2), (output["httpId"], output["httpRevision"]))
        self.assertEqual((422, 409, 422, 422, 409), (
            output["protoAbsentStatus"], output["protoZeroStatus"],
            output["httpAbsentStatus"], output["httpNullStatus"], output["httpZeroStatus"],
        ))
        for request_id in (output["protoId"], output["httpId"], output["protoNullNoteId"],
                           output["httpEmptyNoteId"]):
            self.assertIsNotNone(persisted_request(self.db_path, request_id))
        self.assertEqual({"objective": "protobuf TypeScript null note", "note": None},
                         json.loads(persisted_request(self.db_path, output["protoNullNoteId"])[1]))
        self.assertEqual({"objective": "TypeSpec TypeScript absent note"},
                         json.loads(persisted_request(self.db_path, output["httpAbsentNoteId"])[1]))
        self.assertEqual({"objective": "TypeSpec TypeScript empty note", "note": ""},
                         json.loads(persisted_request(self.db_path, output["httpEmptyNoteId"])[1]))
        self.assertEqual({"objective": "protobuf TypeScript revision", "note": ""},
                         json.loads(persisted_request(self.db_path, output["protoId"])[1]))
        self.assertEqual({"objective": "TypeSpec TypeScript revision", "note": None},
                         json.loads(persisted_request(self.db_path, output["httpId"])[1]))


if __name__ == "__main__":
    unittest.main()
