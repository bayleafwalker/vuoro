"""Test-only protobuf/OpenAPI bridge to the same persisted EC-00 owner oracle."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent import futures
import json
import base64
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from owner_oracle import Conflict, OwnerOracle, ResponseLost, material_digest
from oa import v1_pb2
from oa import v1_pb2_grpc
import grpc


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, path: Path, principal_id: str, fault_plan: set[tuple[str, str]] | None = None):
        self.db_path = path
        self.principal_id = principal_id
        self.fault_plan = set(fault_plan or ())
        self.fault_consumed: set[tuple[str, str]] = set()
        super().__init__(("127.0.0.1", 0), _Handler)

    def owner_call(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        once_operation = {
            "publish": "publish", "revise": "revise", "withdraw_dependent": "withdraw-dependent",
            "report_performed": "report-performed", "verify": "verify", "decide_artifact": "decide-artifact",
            "resolve": "resolve", "acknowledge_continuation": "ack-continuation",
        }
        fault = (once_operation.get(operation, ""), str(args[-1]) if operation in once_operation else "")
        if fault in self.fault_plan and fault not in self.fault_consumed:
            kwargs["drop_after_commit"] = True
        owner = OwnerOracle.connect_as(self.db_path, self.principal_id)
        try:
            try:
                return getattr(owner, operation)(*args, **kwargs)
            except ResponseLost:
                self.fault_consumed.add(fault)
                raise
        finally:
            owner.close()


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
        return OwnerOracle.connect_as(self.server.db_path, self.server.principal_id)

    def _call_owner(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.server.owner_call(operation, *args, **kwargs)

    def _artifact_from_json(self, body: dict[str, Any]) -> dict[str, Any] | None:
        names = ("artifactDigest", "artifactKeyId", "artifactBytes", "artifactEnvelope", "artifactSignature")
        present = [name in body for name in names]
        if not any(present):
            return None
        if not all(present):
            raise ValueError("signed artifact fields must be supplied together")
        return {
            "artifact_digest": body["artifactDigest"],
            "key_id": body["artifactKeyId"],
            "original_bytes": base64.b64decode(body["artifactBytes"], validate=True),
            "envelope_bytes": base64.b64decode(body["artifactEnvelope"], validate=True),
            "signature": base64.b64decode(body["artifactSignature"], validate=True),
        }

    def _artifact_from_proto(self, message: object) -> dict[str, Any] | None:
        names = (message.artifact_digest, message.artifact_key_id, message.artifact_bytes,
                 message.artifact_envelope, message.artifact_signature)
        if not any(names):
            return None
        if not all(names):
            raise ValueError("signed artifact fields must be supplied together")
        return {"artifact_digest": message.artifact_digest, "key_id": message.artifact_key_id,
                "original_bytes": message.artifact_bytes, "envelope_bytes": message.artifact_envelope,
                "signature": message.artifact_signature}

    def _json_read(self, value: Any) -> None:
        self._json(200, {"json": json.dumps(value, separators=(",", ":"))})

    @staticmethod
    def _wire_artifact(value: dict[str, Any]) -> dict[str, Any]:
        return {"materialRevision": value["material_revision"],
                "artifactDigest": value["artifact_digest"], "keyId": value["key_id"],
                "originalBytes": base64.b64encode(value["original_bytes"]).decode("ascii"),
                "envelopeBytes": base64.b64encode(value["envelope_bytes"]).decode("ascii"),
                "signature": base64.b64encode(value["signature"]).decode("ascii")}

    def do_POST(self) -> None:
        try:
            operation_match = re.fullmatch(r"/v1/requests/([^/]+)/(withdraw|report|verify|artifact-decision|resolve|acknowledge)", self.path)
            if operation_match:
                request_id, action = unquote(operation_match.group(1)), operation_match.group(2)
                body = json.loads(self._read())
                expected = body.get("expectedRevision")
                if isinstance(expected, bool) or not isinstance(expected, int) or body.get("requestId", request_id) != request_id:
                    self._json(422, {"error": "requestId and integer expectedRevision are required"})
                    return
                key = self.headers.get("Idempotency-Key", "")
                if action == "withdraw":
                    result = self._call_owner("withdraw_dependent", request_id, body["dependentId"], expected, key)
                elif action == "report":
                    result = self._call_owner("report_performed", request_id, expected, key)
                elif action == "verify":
                    result = self._call_owner("verify", request_id, expected, body["evidenceRef"], key)
                elif action == "artifact-decision":
                    result = self._call_owner("decide_artifact", request_id, expected, body["artifactDigest"], key)
                elif action == "resolve":
                    result = self._call_owner("resolve", request_id, expected, key)
                else:
                    result = self._call_owner("acknowledge_continuation", request_id, body["dependentId"], expected, key)
                self._json(200, {"requestId": result["request_id"], "revision": result["revision"]})
                return
            proto_operation = re.fullmatch(r"/proto/v1/requests/([^/]+)/(withdraw|report|verify|artifact-decision|resolve|acknowledge)", self.path)
            if proto_operation:
                request_id, action = unquote(proto_operation.group(1)), proto_operation.group(2)
                message_type = {"withdraw": v1_pb2.WithdrawDependentRequest,
                                "report": v1_pb2.ReportPerformedRequest, "verify": v1_pb2.VerifyRequest,
                                "artifact-decision": v1_pb2.DecideArtifactRequest,
                                "resolve": v1_pb2.ResolveRequest,
                                "acknowledge": v1_pb2.AcknowledgeContinuationRequest}[action]
                message = message_type.FromString(self._read())
                if message.request_id != request_id or not message.HasField("expected_revision"):
                    self._reply(422, b"", "application/protobuf")
                    return
                key = self.headers.get("Idempotency-Key", "")
                if action == "withdraw":
                    result = self._call_owner("withdraw_dependent", request_id, message.dependent_id,
                                              message.expected_revision, key)
                elif action == "report":
                    result = self._call_owner("report_performed", request_id, message.expected_revision, key)
                elif action == "verify":
                    result = self._call_owner("verify", request_id, message.expected_revision, message.evidence_ref, key)
                elif action == "artifact-decision":
                    result = self._call_owner("decide_artifact", request_id, message.expected_revision,
                                              message.artifact_digest, key)
                elif action == "resolve":
                    result = self._call_owner("resolve", request_id, message.expected_revision, key)
                else:
                    result = self._call_owner("acknowledge_continuation", request_id, message.dependent_id,
                                              message.expected_revision, key)
                self._reply(200, v1_pb2.MutationResult(request_id=result["request_id"],
                            revision=result["revision"]).SerializeToString(), "application/protobuf")
                return
            if self.path == "/v1/requests":
                body = json.loads(self._read())
                request_id = body.get("requestId")
                if not _valid_id(request_id) or not isinstance(body.get("objective"), str):
                    self._json(422, {"error": "requestId and objective are required"})
                    return
                owner_body = {"objective": body["objective"]}
                if "note" in body:
                    owner_body["note"] = body["note"]
                artifact = self._artifact_from_json(body)
                result = self._call_owner("publish", request_id, owner_body,
                                          body.get("dependants") or ["work-a"],
                                          self.headers.get("Idempotency-Key", ""), signed_artifact=artifact)
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
                    if message.note_null != 0:
                        self._reply(422, b"", "application/protobuf")
                        return
                    owner_body["note"] = None
                artifact = self._artifact_from_proto(message)
                result = self._call_owner("publish", message.request_id, owner_body,
                                          list(message.dependent_ids) or ["work-a"],
                                          self.headers.get("Idempotency-Key", ""), signed_artifact=artifact)
                self._reply(200, v1_pb2.MutationResult(
                    request_id=result["request_id"], revision=result["revision"]).SerializeToString(),
                    "application/protobuf")
                return
            self._json(404, {"error": "not found"})
        except Conflict as exc:
            self._json(409, {"error": str(exc)})
        except ResponseLost:
            self.close_connection = True
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
                    if message.note_null != 0:
                        self._reply(422, b"", "application/protobuf")
                        return
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
        except ResponseLost:
            self.close_connection = True
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def do_GET(self) -> None:
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            match = re.fullmatch(r"/v1/requests/([^/]+)/dependants", path)
            if match:
                self._json_read(self._call_owner("dependants", unquote(match.group(1))))
                return
            match = re.fullmatch(r"/v1/requests/([^/]+)/artifact/(\d+)", path)
            if match:
                value = self._call_owner("signed_artifact", unquote(match.group(1)), int(match.group(2)))
                self._json_read(self._wire_artifact(value))
                return
            match = re.fullmatch(r"/v1/requests/([^/]+)", path)
            if match:
                value = self._call_owner("request", unquote(match.group(1)))
                value["body"] = json.loads(value.pop("body_json"))
                self._json_read(value)
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            cursor = query.get("cursor", [None])[0]
            limit = int(query.get("limit", ["2"])[0])
            if path == "/v1/requests":
                self._json_read(self._call_owner("list_requests", cursor, limit=limit))
                return
            if path == "/v1/events":
                self._json_read(self._call_owner("watch", cursor, limit=limit))
                return
            if path.startswith("/proto/v1/"):
                self._get_proto(path, query)
                return
            self._json(404, {"error": "not found"})
        except Conflict as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self._json(400, {"error": str(exc)})

    def _get_proto(self, path: str, query: dict[str, list[str]]) -> None:
        match = re.fullmatch(r"/proto/v1/requests/([^/]+)/dependants", path)
        if match:
            value = self._call_owner("dependants", unquote(match.group(1)))
        else:
            match = re.fullmatch(r"/proto/v1/requests/([^/]+)/artifact/(\d+)", path)
            if match:
                value = self._wire_artifact(self._call_owner("signed_artifact", unquote(match.group(1)), int(match.group(2))))
            else:
                match = re.fullmatch(r"/proto/v1/requests/([^/]+)", path)
                if match:
                    value = self._call_owner("request", unquote(match.group(1)))
                    value["body"] = json.loads(value.pop("body_json"))
                elif path == "/proto/v1/requests":
                    value = self._call_owner("list_requests", query.get("cursor", [None])[0],
                                              limit=int(query.get("limit", ["2"])[0]))
                elif path == "/proto/v1/events":
                    value = self._call_owner("watch", query.get("cursor", [None])[0],
                                              limit=int(query.get("limit", ["2"])[0]))
                else:
                    self._reply(404, b"", "application/protobuf")
                    return
        self._reply(200, v1_pb2.JsonResult(json=json.dumps(value, separators=(",", ":"))).SerializeToString(),
                    "application/protobuf")


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,39}", value) is not None


class CandidateOwnerServer:
    """Ephemeral loopback adapter; each request opens the same SQLite owner DB."""

    def __init__(self, db_path: Path, *, principal_id: str = "requester-a",
                 fault_plan: set[tuple[str, str]] | None = None):
        self.db_path = db_path
        self.principal_id = principal_id
        self.httpd = _Server(db_path, principal_id, fault_plan)
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

    @property
    def fault_consumed(self) -> set[tuple[str, str]]:
        return set(self.httpd.fault_consumed)


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
            if message.note_null != 0:
                raise ValueError("unknown null-value enum is not equivalent to null")
            body["note"] = None
        return body

    @staticmethod
    def _artifact(message: object) -> dict[str, Any] | None:
        fields = (message.artifact_digest, message.artifact_key_id, message.artifact_bytes,
                  message.artifact_envelope, message.artifact_signature)
        if not any(fields):
            return None
        if not all(fields):
            raise ValueError("signed artifact fields must be supplied together")
        return {"artifact_digest": message.artifact_digest, "key_id": message.artifact_key_id,
                "original_bytes": message.artifact_bytes, "envelope_bytes": message.artifact_envelope,
                "signature": message.artifact_signature}

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
            result = self._call("publish", request.request_id, self._body(request),
                                list(request.dependent_ids) or ["work-a"],
                                self._idempotency_key(context), signed_artifact=self._artifact(request))
        except Conflict as exc:
            context.abort(grpc.StatusCode.ABORTED, str(exc))
        except ValueError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except ResponseLost:
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness dropped response after commit")
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
        except ValueError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except ResponseLost:
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness dropped response after commit")
        return v1_pb2.MutationResult(request_id=result["request_id"], revision=result["revision"])

    def WithdrawDependent(self, request: object, context: grpc.ServicerContext) -> object:
        return self._mutate(context, "withdraw_dependent", request.request_id, request.dependent_id,
                            request.expected_revision, require_revision=request.HasField("expected_revision"))

    def ReportPerformed(self, request: object, context: grpc.ServicerContext) -> object:
        return self._mutate(context, "report_performed", request.request_id, request.expected_revision,
                            require_revision=request.HasField("expected_revision"))

    def Verify(self, request: object, context: grpc.ServicerContext) -> object:
        return self._mutate(context, "verify", request.request_id, request.expected_revision,
                            request.evidence_ref, require_revision=request.HasField("expected_revision"))

    def DecideArtifact(self, request: object, context: grpc.ServicerContext) -> object:
        return self._mutate(context, "decide_artifact", request.request_id, request.expected_revision,
                            request.artifact_digest, require_revision=request.HasField("expected_revision"))

    def Resolve(self, request: object, context: grpc.ServicerContext) -> object:
        return self._mutate(context, "resolve", request.request_id, request.expected_revision,
                            require_revision=request.HasField("expected_revision"))

    def AcknowledgeContinuation(self, request: object, context: grpc.ServicerContext) -> object:
        return self._mutate(context, "acknowledge_continuation", request.request_id, request.dependent_id,
                            request.expected_revision, require_revision=request.HasField("expected_revision"))

    def _mutate(self, context: grpc.ServicerContext, operation: str, *args: Any,
                require_revision: bool = True) -> object:
        if not require_revision:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "expected_revision must be present")
        try:
            result = self._call(operation, *args, self._idempotency_key(context))
        except Conflict as exc:
            context.abort(grpc.StatusCode.ABORTED, str(exc))
        except ResponseLost:
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness dropped response after commit")
        return v1_pb2.MutationResult(request_id=result["request_id"], revision=result["revision"])

    def ReadRequest(self, request: object, context: grpc.ServicerContext) -> object:
        value = self._call("request", request.request_id)
        value["body"] = json.loads(value.pop("body_json"))
        return v1_pb2.JsonResult(json=json.dumps(value, separators=(",", ":")))

    def ReadDependants(self, request: object, context: grpc.ServicerContext) -> object:
        return v1_pb2.JsonResult(json=json.dumps(self._call("dependants", request.request_id), separators=(",", ":")))

    def ReadArtifact(self, request: object, context: grpc.ServicerContext) -> object:
        value = self._call("signed_artifact", request.request_id, request.material_revision)
        wire = _Handler._wire_artifact(value)
        return v1_pb2.JsonResult(json=json.dumps(wire, separators=(",", ":")))

    def ListRequests(self, request: object, context: grpc.ServicerContext) -> object:
        return v1_pb2.JsonResult(json=json.dumps(self._call("list_requests", request.cursor or None,
                                    limit=request.limit or 2), separators=(",", ":")))

    def Watch(self, request: object, context: grpc.ServicerContext) -> object:
        return v1_pb2.JsonResult(json=json.dumps(self._call("watch", request.cursor or None,
                                    limit=request.limit or 2), separators=(",", ":")))

    def _call(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.harness.httpd.owner_call(operation, *args, **kwargs)


def initialize_fixture(path: Path) -> None:
    OwnerOracle.initialize(path, [
        {"principal_id": "requester-a", "workspace_id": "workspace-a", "role": "requester"},
        {"principal_id": "requester-a2", "workspace_id": "workspace-a", "role": "requester"},
        {"principal_id": "verifier-a", "workspace_id": "workspace-a", "role": "verifier"},
        {"principal_id": "operator-a", "workspace_id": "workspace-a", "role": "operator"},
        {"principal_id": "coordinator-a", "workspace_id": "workspace-a", "role": "coordinator"},
        {"principal_id": "requester-b", "workspace_id": "workspace-b", "role": "requester"},
    ], links=[
        {"link_id": "work-a", "workspace_id": "workspace-a", "owner_principal": "requester-a",
         "repo_id": "fixture-repo", "run_ref": "fixture-run"},
        {"link_id": "work-b", "workspace_id": "workspace-a", "owner_principal": "requester-a2",
         "repo_id": "fixture-repo", "run_ref": "fixture-run-b"},
        {"link_id": "work-c", "workspace_id": "workspace-a", "owner_principal": "requester-a2",
         "repo_id": "fixture-repo", "run_ref": "fixture-run-c"},
        {"link_id": "work-z", "workspace_id": "workspace-b", "owner_principal": "requester-b",
         "repo_id": "foreign-repo", "run_ref": "foreign-run"},
    ])


def seed_evidence(path: Path, *, evidence_ref: str, request_id: str, body: dict[str, Any],
                  workspace_id: str = "workspace-a", status: str = "eligible") -> None:
    db = sqlite3.connect(path)
    try:
        db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?)",
                   (evidence_ref, workspace_id, request_id, material_digest(body), "performed", status))
        db.commit()
    finally:
        db.close()


def persisted_request(path: Path, request_id: str) -> tuple[int, str] | None:
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT revision,body_json FROM request WHERE request_id=?", (request_id,)).fetchone()
        return row
    finally:
        db.close()


def persisted_artifact(path: Path, request_id: str, revision: int) -> tuple[bytes, bytes, bytes, str] | None:
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT original_bytes,envelope_bytes,signature,artifact_digest FROM signed_artifact WHERE request_id=? AND material_revision=?",
                          (request_id, revision)).fetchone()
    finally:
        db.close()
