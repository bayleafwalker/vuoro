"""Generated Go/Rust clients against the same disposable persisted owner oracle."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests/experiments/ec00/generated/protobuf/python"))

from artifact_semantics import make_signed_artifact
from candidate_owner_http import (CandidateOwnerServer, initialize_fixture, persisted_artifact,
                                  persisted_request, seed_evidence, seed_typed_read_fixture)
from owner_oracle import material_digest


class GeneratedGoRustClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ec00-multilang-")
        self.db_path = Path(self.temp.name) / "owner.sqlite"
        initialize_fixture(self.db_path)
        self.servers = {}
        fault_plan = {("publish", key) for key in (
            "go-grpc-full-publish", "go-http-full-publish",
            "rust-grpc-full-publish", "rust-http-full-publish")}
        for principal in ("requester-a", "requester-a2", "coordinator-a", "verifier-a", "operator-a"):
            server = CandidateOwnerServer(self.db_path, principal_id=principal,
                fault_plan=fault_plan if principal == "coordinator-a" else None)
            server.__enter__()
            self.servers[principal] = server
        self.server = self.servers["requester-a"]

    def tearDown(self) -> None:
        for server in self.servers.values():
            server.__exit__(None, None, None)
        self.temp.cleanup()

    def run_client(self, command: list[str], cwd: Path) -> dict[str, object]:
        endpoints = {principal: {"http": server.base_url, "grpc": server.grpc_target}
                     for principal, server in self.servers.items()}
        specs = {
            "go_proto": ("9007199254741201", "Go native gRPC full lifecycle"),
            "go_http": ("9007199254741203", "Go OpenAPI full lifecycle"),
            "rust_proto": ("9007199254741205", "Rust native gRPC full lifecycle"),
            "rust_http": ("9007199254741207", "Rust OpenAPI full lifecycle"),
            "go_empty_proto": ("9007199254741209", "Go native gRPC empty artifact"),
            "go_empty_http": ("9007199254741211", "Go OpenAPI empty artifact"),
            "rust_empty_proto": ("9007199254741213", "Rust native gRPC empty artifact"),
            "rust_empty_http": ("9007199254741215", "Rust OpenAPI empty artifact"),
        }
        artifacts = {}
        for name, (request_id, objective) in specs.items():
            body = {"objective": objective}
            signed = make_signed_artifact(workspace_id="workspace-a", request_id=request_id,
                material_revision=1, material_digest=material_digest(body),
                original_bytes=(b"" if "empty" in name else (name + " exact artifact\x00").encode()))
            if "empty" not in name and "lifecycle" in objective:
                seed_evidence(self.db_path, evidence_ref=f"evidence:source:{request_id}",
                    request_id=request_id, body=body)
            artifacts[name] = {
                "request_id": request_id,
                "objective": objective,
                "artifact_digest": signed["artifact_digest"],
                "key_id": signed["key_id"],
                "original_bytes": base64.b64encode(signed["original_bytes"]).decode("ascii"),
                "envelope_bytes": base64.b64encode(signed["envelope_bytes"]).decode("ascii"),
                "signature": base64.b64encode(signed["signature"]).decode("ascii"),
            }
        big = seed_typed_read_fixture(self.db_path)
        big_signed = big["signed"]
        artifacts["big_read"] = {
            "request_id": big["request_id"], "objective": big["body"]["objective"],
            "revision": str(big["revision"]), "material_revision": str(big["material_revision"]),
            "sequence": str(big["sequence"]), "artifact_digest": big_signed["artifact_digest"],
            "key_id": big_signed["key_id"],
            "original_bytes": base64.b64encode(big_signed["original_bytes"]).decode("ascii"),
            "envelope_bytes": base64.b64encode(big_signed["envelope_bytes"]).decode("ascii"),
            "signature": base64.b64encode(big_signed["signature"]).decode("ascii"),
        }
        self.artifacts = artifacts
        env = dict(os.environ, EC00_BASE_URL=self.server.base_url,
                   EC00_GRPC_TARGET=self.server.grpc_target,
                   EC00_ENDPOINTS=json.dumps(endpoints), EC00_ARTIFACTS=json.dumps(artifacts))
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=240)
        self.assertEqual(0, result.returncode, result.stdout + "\n" + result.stderr)
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            self.fail(f"client did not return its JSON result: {result.stdout!r}; {exc}")

    def assert_shared_results(self, output: dict[str, object], language: str,
                              proto_id: str, http_id: str) -> None:
        self.assertEqual(language, output["language"])
        self.assertEqual((proto_id, 2), (output["protoId"], output["protoRevision"]))
        self.assertEqual(("InvalidArgument", "Aborted"),
                         (output["protoAbsent"], output["protoZero"]))
        self.assertEqual((http_id, 2), (output["httpId"], output["httpRevision"]))
        self.assertEqual((422, 422, 409),
                         (output["httpAbsent"], output["httpNull"], output["httpZero"]))
        self.assertEqual({"objective": f"{language.title()} native gRPC revision", "note": ""},
                         json.loads(persisted_request(self.db_path, proto_id)[1]))
        self.assertEqual({"objective": f"{language.title()} OpenAPI revision", "note": ""},
                         json.loads(persisted_request(self.db_path, http_id)[1]))
        self.assertEqual({"objective": f"{language.title()} native gRPC null note", "note": None},
                         json.loads(persisted_request(self.db_path, output["protoNullNoteId"])[1]))
        self.assertEqual({"objective": f"{language.title()} native gRPC absent note"},
                         json.loads(persisted_request(self.db_path, output["protoAbsentNoteId"])[1]))
        self.assertEqual({"objective": f"{language.title()} OpenAPI null note", "note": None},
                         json.loads(persisted_request(self.db_path, output["httpNullNoteId"])[1]))
        self.assertEqual({"objective": f"{language.title()} OpenAPI absent note"},
                         json.loads(persisted_request(self.db_path, output["httpAbsentNoteId"])[1]))

        proto_full = "9007199254741201" if language == "go" else "9007199254741205"
        http_full = "9007199254741203" if language == "go" else "9007199254741207"
        proto_empty = "9007199254741209" if language == "go" else "9007199254741213"
        http_empty = "9007199254741211" if language == "go" else "9007199254741215"
        for request_id, objective in (
            (proto_full, f"{language.title()} native gRPC full lifecycle"),
            (http_full, f"{language.title()} OpenAPI full lifecycle"),
        ):
            row = self.db_path
            self.assertEqual({"objective": objective}, json.loads(persisted_request(row, request_id)[1]))
            db = sqlite3.connect(row)
            try:
                state = db.execute("SELECT revision,state,reported,verified,material_revision FROM request WHERE request_id=?",
                                   (request_id,)).fetchone()
                self.assertEqual((8, "resolved", 1, 1, 1), state)
                events = db.execute("SELECT action,revision FROM event WHERE request_id=? ORDER BY seq",
                                    (request_id,)).fetchall()
                self.assertEqual([("published", 1), ("dependent-withdrawn", 2),
                    ("performed-reported", 3), ("outcome-verified", 4),
                    ("artifact-decided", 5), ("resolved", 6),
                    ("continuation-acknowledged", 7), ("continuation-acknowledged", 8)], events)
                acknowledgements = db.execute("SELECT dependent_id,continuation_ack FROM dependent WHERE request_id=? ORDER BY dependent_id",
                                              (request_id,)).fetchall()
                self.assertEqual([("work-a", 1), ("work-b", 1), ("work-c", 0)], acknowledgements)
            finally:
                db.close()
        for name, request_id in (("go_proto" if language == "go" else "rust_proto", proto_full),
                                 ("go_http" if language == "go" else "rust_http", http_full),
                                 ("go_empty_proto" if language == "go" else "rust_empty_proto", proto_empty),
                                 ("go_empty_http" if language == "go" else "rust_empty_http", http_empty)):
            expected = self.artifacts[name]
            artifact = persisted_artifact(self.db_path, request_id, 1)
            self.assertIsNotNone(artifact)
            self.assertEqual((base64.b64decode(expected["original_bytes"]),
                              base64.b64decode(expected["envelope_bytes"]),
                              base64.b64decode(expected["signature"]), expected["artifact_digest"]), artifact)
        self.assertEqual({("publish", f"{language}-grpc-full-publish"),
                          ("publish", f"{language}-http-full-publish")},
                         self.servers["coordinator-a"].fault_consumed)

    def test_generated_go_grpc_and_openapi_clients(self) -> None:
        output = self.run_client(["go", "run", "./candidates/go-client"], HERE)
        self.assert_shared_results(output, "go", "9007199254741011", "9007199254741013")

    def test_generated_rust_grpc_and_openapi_clients(self) -> None:
        manifest = HERE / "candidates/rust-client/Cargo.toml"
        output = self.run_client(["cargo", "run", "--manifest-path", str(manifest), "--locked", "--quiet",
                                  "--bin", "ec00-rust-client-check"], ROOT)
        self.assert_shared_results(output, "rust", "9007199254741021", "9007199254741023")


if __name__ == "__main__":
    unittest.main()
