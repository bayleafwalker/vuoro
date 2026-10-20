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
from artifact_semantics import make_signed_artifact
from oa import v1_pb2
from oa import v1_pb2_grpc
import grpc


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, path: Path, principal_id: str, fault_plan: set[tuple[str, str]] | None = None,
                 address: tuple[str, int] = ("127.0.0.1", 0)):
        self.db_path = path
        self.principal_id = principal_id
        self.fault_plan = set(fault_plan or ())
        self.fault_consumed: set[tuple[str, str]] = set()
        super().__init__(address, _Handler)

    def owner_call(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        once_operation = {
            "publish": "publish", "revise": "revise", "withdraw_dependent": "withdraw-dependent",
            "report_performed": "report-performed", "verify": "verify", "decide_artifact": "decide-artifact",
            "resolve": "resolve", "acknowledge_continuation": "ack-continuation",
        }
        fault = (once_operation.get(operation, ""), str(args[-1]) if operation in once_operation else "")
        if fault in self.fault_plan and fault not in self.fault_consumed:
            kwargs["drop_after_commit"] = True
        owner = self.connect_owner()
        try:
            try:
                return getattr(owner, operation)(*args, **kwargs)
            except ResponseLost:
                self.fault_consumed.add(fault)
                raise
        finally:
            owner.close()

    def connect_owner(self) -> OwnerOracle:
        return OwnerOracle.connect_as(self.db_path, self.principal_id)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *_: Any) -> None:
        pass

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A departed client does not reverse an already committed owner call.
            self.close_connection = True

    def _json(self, status: int, body: dict[str, Any]) -> None:
        self._reply(status, json.dumps(body, separators=(",", ":")).encode(), "application/json")

    def _read(self) -> bytes:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 1024 * 1024:
            raise ValueError("test request outside 1 MiB harness bound")
        return self.rfile.read(size)

    def _owner(self) -> OwnerOracle:
        return self.server.connect_owner()

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
        names = ("artifact_digest", "artifact_key_id", "artifact_bytes",
                 "artifact_envelope", "artifact_signature")
        present = [message.HasField(name) for name in names]
        if not any(present):
            return None
        if not all(present):
            raise ValueError("signed artifact fields must be supplied together")
        return {"artifact_digest": message.artifact_digest, "key_id": message.artifact_key_id,
                "original_bytes": message.artifact_bytes, "envelope_bytes": message.artifact_envelope,
                "signature": message.artifact_signature}

    def _json_read(self, value: Any) -> None:
        self._json(200, value)

    @staticmethod
    def _request_read(value: dict[str, Any]) -> dict[str, Any]:
        body = json.loads(value.pop("body_json"))
        return {
            "requestId": value["request_id"],
            "revision": str(value["revision"]),
            "materialRevision": str(value["material_revision"]),
            "state": value["state"],
            "body": {"objective": body["objective"], **({"note": body["note"]} if "note" in body else {})},
            "reported": bool(value["reported"]),
            "verified": bool(value["verified"]),
            "evidenceRef": value["evidence_ref"],
            "artifactDecisionDigest": value["artifact_decision_digest"],
            "createdSeq": str(value["created_seq"]),
        }

    @staticmethod
    def _dependants_read(value: dict[str, dict[str, int]]) -> dict[str, Any]:
        return {"items": [
            {"dependentId": dep_id, "active": bool(row["active"]),
             "continuationAck": bool(row["continuation_ack"])}
            for dep_id, row in sorted(value.items())
        ]}

    @staticmethod
    def _artifact_read(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "materialRevision": str(value["material_revision"]),
            "materialDigest": value["material_digest"],
            "artifactDigest": value["artifact_digest"],
            "keyId": value["key_id"],
            "originalBytes": base64.b64encode(value["original_bytes"]).decode("ascii"),
            "envelopeBytes": base64.b64encode(value["envelope_bytes"]).decode("ascii"),
            "signature": base64.b64encode(value["signature"]).decode("ascii"),
        }

    @staticmethod
    def _request_page(value: dict[str, Any]) -> dict[str, Any]:
        return {"items": [
            {"requestId": row["request_id"], "revision": str(row["revision"]),
             "state": row["state"], "createdSeq": str(row["created_seq"])}
            for row in value["items"]
        ], "nextCursor": value["next_cursor"]}

    @staticmethod
    def _watch_page(value: dict[str, Any]) -> dict[str, Any]:
        return {"events": [
            {"seq": str(row["seq"]), "requestId": row["request_id"],
             "action": row["action"], "revision": str(row["revision"])}
            for row in value["events"]
        ], "nextCursor": value["next_cursor"], "more": value["more"]}

    @staticmethod
    def _request_state(value: str) -> int:
        try:
            return {"open": v1_pb2.REQUEST_STATE_OPEN,
                    "resolved": v1_pb2.REQUEST_STATE_RESOLVED}[value]
        except KeyError as exc:
            raise ValueError("owner returned an unknown request state") from exc

    @classmethod
    def _proto_request_read(cls, value: dict[str, Any]) -> Any:
        body = json.loads(value["body_json"])
        result = v1_pb2.RequestRead(
            request_id=value["request_id"], revision=value["revision"],
            material_revision=value["material_revision"], state=cls._request_state(value["state"]),
            body=v1_pb2.RequestBody(objective=body["objective"]), reported=bool(value["reported"]),
            verified=bool(value["verified"]), created_seq=value["created_seq"])
        if "note" in body:
            if body["note"] is None:
                result.body.note_null = 0
            else:
                result.body.note = body["note"]
        if value["evidence_ref"] is None:
            result.evidence_ref_null = 0
        else:
            result.evidence_ref = value["evidence_ref"]
        if value["artifact_decision_digest"] is None:
            result.artifact_decision_digest_null = 0
        else:
            result.artifact_decision_digest = value["artifact_decision_digest"]
        return result

    @staticmethod
    def _proto_dependants_read(value: dict[str, dict[str, int]]) -> Any:
        return v1_pb2.DependantsRead(items=[
            v1_pb2.DependentRead(dependent_id=dep_id, active=bool(row["active"]),
                                 continuation_ack=bool(row["continuation_ack"]))
            for dep_id, row in sorted(value.items())])

    @staticmethod
    def _proto_artifact_read(value: dict[str, Any]) -> Any:
        return v1_pb2.ArtifactRead(material_revision=value["material_revision"],
            material_digest=value["material_digest"], artifact_digest=value["artifact_digest"],
            key_id=value["key_id"], original_bytes=value["original_bytes"],
            envelope_bytes=value["envelope_bytes"], signature=value["signature"])

    @classmethod
    def _proto_request_page(cls, value: dict[str, Any]) -> Any:
        result = v1_pb2.RequestPage(items=[
            v1_pb2.RequestSummary(request_id=row["request_id"], revision=row["revision"],
                state=cls._request_state(row["state"]), created_seq=row["created_seq"])
            for row in value["items"]])
        if value["next_cursor"] is not None:
            result.next_cursor = value["next_cursor"]
        return result

    @staticmethod
    def _proto_watch_page(value: dict[str, Any]) -> Any:
        result = v1_pb2.WatchPage(events=[
            v1_pb2.WatchEvent(seq=row["seq"], request_id=row["request_id"],
                              action=row["action"], revision=row["revision"])
            for row in value["events"]], more=value["more"])
        if value["next_cursor"] is not None:
            result.next_cursor = value["next_cursor"]
        return result

    @staticmethod
    def _wire_artifact(value: dict[str, Any]) -> dict[str, Any]:
        return {"materialRevision": value["material_revision"],
                "artifactDigest": value["artifact_digest"], "keyId": value["key_id"],
                "originalBytes": base64.b64encode(value["original_bytes"]).decode("ascii"),
                "envelopeBytes": base64.b64encode(value["envelope_bytes"]).decode("ascii"),
                "signature": base64.b64encode(value["signature"]).decode("ascii")}

    def do_POST(self) -> None:
        try:
            if self.path in {"/proto/v2/list-requests", "/proto/v2/watch"}:
                from google.protobuf.message import DecodeError
                try:
                    request = v1_pb2.PageRequestV2.FromString(self._read())
                except DecodeError:
                    self._reply(400, b"", "application/protobuf")
                    return
                cursor = request.cursor if request.HasField("cursor") else None
                limit = request.limit if request.HasField("limit") else 2
                operation = "watch" if self.path.endswith("/watch") else "list_requests"
                value = self._call_owner(operation, cursor, limit=limit)
                result = self._proto_watch_page(value) if operation == "watch" else self._proto_request_page(value)
                self._reply(200, result.SerializeToString(), "application/protobuf")
                return
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
                value = self._call_owner("dependants", unquote(match.group(1)))
                self._json_read(self._dependants_read(value))
                return
            match = re.fullmatch(r"/v1/requests/([^/]+)/artifact/(\d+)", path)
            if match:
                value = self._call_owner("signed_artifact", unquote(match.group(1)), int(match.group(2)))
                self._json_read(self._artifact_read(value))
                return
            match = re.fullmatch(r"/v1/requests/([^/]+)", path)
            if match:
                value = self._call_owner("request", unquote(match.group(1)))
                self._json_read(self._request_read(value))
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            cursor = query.get("cursor", [None])[0]
            limit = int(query.get("limit", ["2"])[0])
            if path == "/v1/requests":
                value = self._call_owner("list_requests", cursor, limit=limit)
                self._json_read(self._request_page(value))
                return
            if path == "/v1/events":
                value = self._call_owner("watch", cursor, limit=limit)
                self._json_read(self._watch_page(value))
                return
            if path.startswith("/proto/v1/"):
                self._get_proto(path, query)
                return
            self._json(404, {"error": "not found"})
        except Conflict as exc:
            self._json(409, {"error": str(exc)})
        except ResponseLost:
            self.close_connection = True
        except (ValueError, TypeError, KeyError) as exc:
            self._json(400, {"error": str(exc)})

    def _get_proto(self, path: str, query: dict[str, list[str]]) -> None:
        match = re.fullmatch(r"/proto/v1/requests/([^/]+)/dependants", path)
        if match:
            value = self._proto_dependants_read(
                self._call_owner("dependants", unquote(match.group(1))))
        else:
            match = re.fullmatch(r"/proto/v1/requests/([^/]+)/artifact/(\d+)", path)
            if match:
                value = self._proto_artifact_read(self._call_owner(
                    "signed_artifact", unquote(match.group(1)), int(match.group(2))))
            else:
                match = re.fullmatch(r"/proto/v1/requests/([^/]+)", path)
                if match:
                    value = self._proto_request_read(
                        self._call_owner("request", unquote(match.group(1))))
                elif path == "/proto/v1/requests":
                    value = self._proto_request_page(self._call_owner(
                        "list_requests", query.get("cursor", [None])[0],
                        limit=int(query.get("limit", ["2"])[0])))
                elif path == "/proto/v1/events":
                    value = self._proto_watch_page(self._call_owner(
                        "watch", query.get("cursor", [None])[0],
                        limit=int(query.get("limit", ["2"])[0])))
                else:
                    self._reply(404, b"", "application/protobuf")
                    return
        self._reply(200, value.SerializeToString(), "application/protobuf")


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
        names = ("artifact_digest", "artifact_key_id", "artifact_bytes",
                 "artifact_envelope", "artifact_signature")
        present = [message.HasField(name) for name in names]
        if not any(present):
            return None
        if not all(present):
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
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness response unavailable")
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
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness response unavailable")
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
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness response unavailable")
        return v1_pb2.MutationResult(request_id=result["request_id"], revision=result["revision"])

    def ReadRequest(self, request: object, context: grpc.ServicerContext) -> object:
        value = self._call("request", request.request_id)
        return _Handler._proto_request_read(value)

    def ReadDependants(self, request: object, context: grpc.ServicerContext) -> object:
        return _Handler._proto_dependants_read(self._call("dependants", request.request_id))

    def ReadArtifact(self, request: object, context: grpc.ServicerContext) -> object:
        value = self._call("signed_artifact", request.request_id, request.material_revision)
        return _Handler._proto_artifact_read(value)

    def ListRequests(self, request: object, context: grpc.ServicerContext) -> object:
        return _Handler._proto_request_page(self._call("list_requests", request.cursor or None,
                                    limit=request.limit or 2))

    def Watch(self, request: object, context: grpc.ServicerContext) -> object:
        try:
            return _Handler._proto_watch_page(self._call("watch", request.cursor or None,
                                        limit=request.limit or 2))
        except Conflict as exc:
            context.abort(grpc.StatusCode.ABORTED, str(exc))
        except ResponseLost:
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness response unavailable")

    def ListRequestsV2(self, request: object, context: grpc.ServicerContext) -> object:
        return _Handler._proto_request_page(self._read_v2("list_requests", request, context))

    def WatchV2(self, request: object, context: grpc.ServicerContext) -> object:
        return _Handler._proto_watch_page(self._read_v2("watch", request, context))

    def _read_v2(self, operation: str, request: object, context: grpc.ServicerContext) -> dict[str, Any]:
        cursor = request.cursor if request.HasField("cursor") else None
        limit = request.limit if request.HasField("limit") else 2
        try:
            return self._call(operation, cursor, limit=limit)
        except Conflict as exc:
            context.abort(grpc.StatusCode.ABORTED, str(exc))
        except ResponseLost:
            context.abort(grpc.StatusCode.UNAVAILABLE, "test harness response unavailable")

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


def seed_typed_read_fixture(path: Path) -> dict[str, Any]:
    """Seed exact int64 and empty-original-bytes read values for generated clients."""
    request_id = "9007199254741199"
    revision = 9007199254740995
    material_revision = 9007199254740993
    sequence = 9007199254740997
    if revision < material_revision:
        raise ValueError("aggregate revision cannot precede the material revision")
    body = {"objective": "typed int64 read fixture"}
    signed = make_signed_artifact(workspace_id="workspace-a", request_id=request_id,
        material_revision=material_revision, material_digest=material_digest(body), original_bytes=b"")
    db = sqlite3.connect(path)
    try:
        db.execute("INSERT INTO request(request_id,workspace_id,revision,material_revision,state,body_json,created_seq) VALUES(?,?,?,?,?,?,?)",
                   (request_id, "workspace-a", revision, material_revision, "open",
                    json.dumps(body, sort_keys=True, separators=(",", ":")), sequence))
        db.execute("INSERT INTO dependent(request_id,dependent_id) VALUES(?,?)", (request_id, "work-a"))
        db.execute("INSERT INTO signed_artifact(workspace_id,request_id,material_revision,material_digest,artifact_digest,key_id,original_bytes,envelope_bytes,signature) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("workspace-a", request_id, material_revision, material_digest(body),
                    signed["artifact_digest"], signed["key_id"], sqlite3.Binary(signed["original_bytes"]),
                    sqlite3.Binary(signed["envelope_bytes"]), sqlite3.Binary(signed["signature"])))
        event = {"request_id": request_id, "action": "published", "revision": revision}
        db.execute("INSERT INTO event(seq,workspace_id,request_id,action,revision,event_json) VALUES(?,?,?,?,?,?)",
                   (sequence, "workspace-a", request_id, "published", revision,
                    json.dumps(event, sort_keys=True, separators=(",", ":"))))
        db.commit()
    finally:
        db.close()
    return {"request_id": request_id, "revision": revision,
            "material_revision": material_revision, "sequence": sequence,
            "body": body, "signed": signed}


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
