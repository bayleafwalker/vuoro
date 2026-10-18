"""Test-only protobuf/OpenAPI bridge to the same persisted EC-00 owner oracle."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent import futures
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
from urllib.parse import unquote

from owner_oracle import Conflict, OwnerOracle
from oa import v1_pb2
from oa import v1_pb2_grpc
import grpc


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, path: Path):
        self.db_path = path
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *_: Any) -> None:
        pass

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, body: dict[str, Any]) -> None:
        self._reply(status, json.dumps(body, separators=(",", ":")).encode(), "application/json")

    def _read(self) -> bytes:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 1024 * 1024:
            raise ValueError("test request outside 1 MiB harness bound")
        return self.rfile.read(size)

    def _owner(self) -> OwnerOracle:
        return OwnerOracle.connect_as(self.server.db_path, "requester-a")

    def _call_owner(self, operation: str, *args: Any) -> dict[str, Any]:
        owner = self._owner()
        try:
            return getattr(owner, operation)(*args)
        finally:
            owner.close()

    def do_POST(self) -> None:
        try:
            if self.path == "/v1/requests":
                body = json.loads(self._read())
                request_id = body.get("requestId")
                if not _valid_id(request_id) or not isinstance(body.get("objective"), str):
                    self._json(422, {"error": "requestId and objective are required"})
                    return
                owner_body = {"objective": body["objective"]}
                if "note" in body:
                    owner_body["note"] = body["note"]
                result = self._call_owner("publish", request_id, owner_body, ["work-a"],
                                          self.headers.get("Idempotency-Key", ""))
                self._json(200, {"requestId": result["request_id"], "revision": result["revision"]})
                return
            if self.path == "/proto/v1/requests":
                message = v1_pb2.PublishRequest.FromString(self._read())
                if not _valid_id(message.request_id) or not message.HasField("objective"):
                    self._reply(422, b"", "application/protobuf")
                    return
                owner_body = {"objective": message.objective}
                if message.WhichOneof("note_value") == "note":
                    owner_body["note"] = message.note
                elif message.WhichOneof("note_value") == "note_null":
                    owner_body["note"] = None
                result = self._call_owner("publish", message.request_id, owner_body, ["work-a"],
                                          self.headers.get("Idempotency-Key", ""))
                self._reply(200, v1_pb2.MutationResult(
                    request_id=result["request_id"], revision=result["revision"]).SerializeToString(),
                    "application/protobuf")
                return
            self._json(404, {"error": "not found"})
        except Conflict as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def do_PATCH(self) -> None:
        try:
            match = re.fullmatch(r"/v1/requests/([^/]+)", self.path)
            if match:
                request_id = unquote(match.group(1))
                body = json.loads(self._read())
                expected = body.get("expectedRevision")
                if "expectedRevision" not in body or expected is None:
                    self._json(422, {"error": "expectedRevision must be present and non-null"})
                    return
                if isinstance(expected, bool) or not isinstance(expected, int):
                    self._json(422, {"error": "expectedRevision must be an integer"})
                    return
                if body.get("requestId", request_id) != request_id or not isinstance(body.get("objective"), str):
                    self._json(422, {"error": "requestId mismatch or objective missing"})
                    return
                owner_body = {"objective": body["objective"]}
                if "note" in body:
                    owner_body["note"] = body["note"]
                result = self._call_owner("revise", request_id, expected, owner_body,
                                          self.headers.get("Idempotency-Key", ""))
                self._json(200, {"requestId": result["request_id"], "revision": result["revision"]})
                return
            match = re.fullmatch(r"/proto/v1/requests/([^/]+)", self.path)
            if match:
                request_id = unquote(match.group(1))
                message = v1_pb2.ReviseRequest.FromString(self._read())
                if message.request_id != request_id or not message.HasField("objective"):
                    self._reply(422, b"", "application/protobuf")
                    return
                if not message.HasField("expected_revision"):
                    self._reply(422, b"", "application/protobuf")
                    return
                owner_body = {"objective": message.objective}
                if message.WhichOneof("note_value") == "note":
                    owner_body["note"] = message.note
                elif message.WhichOneof("note_value") == "note_null":
                    owner_body["note"] = None
                result = self._call_owner("revise", request_id, message.expected_revision, owner_body,
                                          self.headers.get("Idempotency-Key", ""))
                self._reply(200, v1_pb2.MutationResult(
                    request_id=result["request_id"], revision=result["revision"]).SerializeToString(),
                    "application/protobuf")
                return
            self._json(404, {"error": "not found"})
        except Conflict as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,39}", value) is not None


class CandidateOwnerServer:
    """Ephemeral loopback adapter; each request opens the same SQLite owner DB."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.httpd = _Server(db_path)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        v1_pb2_grpc.add_OwnerOperationsServicer_to_server(_GrpcOwner(self), self.grpc_server)
        self.grpc_port = self.grpc_server.add_insecure_port("127.0.0.1:0")
        if not self.grpc_port:
            raise RuntimeError("could not bind disposable gRPC loopback listener")

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    @property
    def grpc_target(self) -> str:
        return f"127.0.0.1:{self.grpc_port}"

    def __enter__(self) -> "CandidateOwnerServer":
        self.thread.start()
        self.grpc_server.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.grpc_server.stop(grace=0).wait(timeout=5)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class _GrpcOwner(v1_pb2_grpc.OwnerOperationsServicer):
    """Native gRPC test transport delegating to the same SQLite OwnerOracle."""

    def __init__(self, harness: CandidateOwnerServer):
        self.harness = harness

    @staticmethod
    def _body(message: object) -> dict[str, Any]:
        body: dict[str, Any] = {"objective": message.objective}
        note_kind = message.WhichOneof("note_value")
        if note_kind == "note":
            body["note"] = message.note
        elif note_kind == "note_null":
            body["note"] = None
        return body

    @staticmethod
    def _idempotency_key(context: grpc.ServicerContext) -> str:
        for key, value in context.invocation_metadata():
            if key.lower() == "idempotency-key":
                return value
        return ""

    def Publish(self, request: object, context: grpc.ServicerContext) -> object:
        if not _valid_id(request.request_id) or not request.HasField("objective"):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "request_id and objective are required")
        try:
            result = self._call("publish", request.request_id, self._body(request), ["work-a"],
                                self._idempotency_key(context))
        except Conflict as exc:
            context.abort(grpc.StatusCode.ABORTED, str(exc))
        return v1_pb2.MutationResult(request_id=result["request_id"], revision=result["revision"])

    def Revise(self, request: object, context: grpc.ServicerContext) -> object:
        if (not _valid_id(request.request_id) or not request.HasField("objective")
                or not request.HasField("expected_revision")):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                          "request_id, objective and expected_revision are required")
        try:
            result = self._call("revise", request.request_id, request.expected_revision,
                                self._body(request), self._idempotency_key(context))
        except Conflict as exc:
            context.abort(grpc.StatusCode.ABORTED, str(exc))
        return v1_pb2.MutationResult(request_id=result["request_id"], revision=result["revision"])

    def _call(self, operation: str, *args: Any) -> dict[str, Any]:
        owner = OwnerOracle.connect_as(self.harness.db_path, "requester-a")
        try:
            return getattr(owner, operation)(*args)
        finally:
            owner.close()


def initialize_fixture(path: Path) -> None:
    OwnerOracle.initialize(path, [
        {"principal_id": "requester-a", "workspace_id": "workspace-a", "role": "requester"},
    ], links=[
        {"link_id": "work-a", "workspace_id": "workspace-a", "owner_principal": "requester-a",
         "repo_id": "fixture-repo", "run_ref": "fixture-run"},
    ])


def persisted_request(path: Path, request_id: str) -> tuple[int, str] | None:
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT revision,body_json FROM request WHERE request_id=?", (request_id,)).fetchone()
        return row
    finally:
        db.close()
