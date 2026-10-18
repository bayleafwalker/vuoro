"""Generated Go/Rust clients against the same disposable persisted owner oracle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests/experiments/ec00/generated/protobuf/python"))

from candidate_owner_http import CandidateOwnerServer, initialize_fixture, persisted_request


class GeneratedGoRustClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ec00-multilang-")
        self.db_path = Path(self.temp.name) / "owner.sqlite"
        initialize_fixture(self.db_path)
        self.server = CandidateOwnerServer(self.db_path)
        self.server.__enter__()

    def tearDown(self) -> None:
        self.server.__exit__(None, None, None)
        self.temp.cleanup()

    def run_client(self, command: list[str], cwd: Path) -> dict[str, object]:
        env = dict(os.environ, EC00_BASE_URL=self.server.base_url,
                   EC00_GRPC_TARGET=self.server.grpc_target)
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

    def test_generated_go_grpc_and_openapi_clients(self) -> None:
        output = self.run_client(["go", "run", "./candidates/go-client"], HERE)
        self.assert_shared_results(output, "go", "9007199254741011", "9007199254741013")

    def test_generated_rust_grpc_and_openapi_clients(self) -> None:
        manifest = HERE / "candidates/rust-client/Cargo.toml"
        output = self.run_client(["cargo", "run", "--manifest-path", str(manifest), "--locked", "--quiet"], ROOT)
        self.assert_shared_results(output, "rust", "9007199254741021", "9007199254741023")


if __name__ == "__main__":
    unittest.main()
