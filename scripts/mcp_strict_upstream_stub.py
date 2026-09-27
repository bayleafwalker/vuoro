"""A stand-in runtime shell for the `mcp-strict-client` CI job (agentops#2522).

`vuoro-service mcp-serve` reads work through the runtime shell's invoke API
(`GET /api/catalog/v1`, `POST /api/invoke/v1`).  The real shell needs
Postgres and the pinned adapters; this stub answers the two public-work
operations for exactly one workspace so the edge under test returns real
MCP envelopes, successes and tool errors alike.

It runs inside the service image (``--entrypoint python``), so it may use
only what that image installs: the standard library plus ``jwt``.

Every invocation is recorded and served back at ``GET /_calls`` so the
strict client can prove the D-044 isolation property from the upstream
side: an edge configured for workspace A forwards only A's verified
assertions to A's upstream, and nothing of B's ever arrives here.

Configuration (environment):

``STUB_WORKSPACE_ID``, ``STUB_REPO_ID``
    The one workspace and repository this upstream serves.
``STUB_ITEMS``
    JSON array of item records (the ``work.public.item-v1`` record shape).
``STUB_GATEWAY_PUBLIC_KEY_FILE``, ``STUB_ISSUER``, ``STUB_AUDIENCE``, ``STUB_KEY_ID``
    How to verify the forwarded ``X-Vuoro-Identity`` assertion.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt

CATALOG_REVISION = "ci-strict-rev-1"
OPERATION_LIST = "work.public.list-v1"
OPERATION_ITEM = "work.public.item-v1"
AS_OF = "2026-09-27T12:00:00Z"
_LIST_FIELDS = ("work_id", "title", "priority", "status", "blocked", "updated_at")

WORKSPACE_ID = os.environ["STUB_WORKSPACE_ID"]
REPO_ID = os.environ["STUB_REPO_ID"]
ITEMS: list[dict[str, Any]] = json.loads(os.environ["STUB_ITEMS"])
with open(os.environ["STUB_GATEWAY_PUBLIC_KEY_FILE"], "rb") as handle:
    PUBLIC_KEY = handle.read()
ISSUER = os.environ["STUB_ISSUER"]
AUDIENCE = os.environ.get("STUB_AUDIENCE", "vuoro-service")
KEY_ID = os.environ["STUB_KEY_ID"]

_calls: list[dict[str, Any]] = []
_lock = threading.Lock()


def _result(
    status_code: int,
    envelope: dict[str, Any],
    *,
    result: Any = None,
    error: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    return status_code, {
        "schema_version": "invocation-result/v1",
        "request_id": envelope.get("request_id"),
        "operation": envelope.get("operation"),
        "catalog_revision": CATALOG_REVISION,
        "status": "rejected" if error else "accepted",
        "result": None if error else result,
        "error": error,
    }


def _invoke(headers: Any, envelope: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    record: dict[str, Any] = {
        "operation": envelope.get("operation"),
        "repo_id": envelope.get("repo_id"),
        "request_id": envelope.get("request_id"),
        "arguments": envelope.get("arguments"),
        "edge_proof_present": headers.get("X-Vuoro-Edge-Proof") is not None,
        "verified": False,
        "workspace_id": None,
        "refusal": None,
    }
    token = headers.get("X-Vuoro-Identity") or ""
    try:
        header = jwt.get_unverified_header(token)
        if header.get("kid") != KEY_ID:
            raise jwt.InvalidTokenError("unexpected kid")
        claims = jwt.decode(
            token, PUBLIC_KEY, algorithms=["EdDSA"], audience=AUDIENCE, issuer=ISSUER
        )
    except jwt.InvalidTokenError as error:
        record["refusal"] = f"assertion does not verify: {error}"
        return (*_result(401, envelope, error={"code": "unauthenticated", "message": "bad assertion"}), record)
    record["workspace_id"] = claims.get("workspace_id")
    record["verified"] = True
    request_id = headers.get("X-Request-Id")
    if not (request_id == claims.get("request_id") == envelope.get("request_id")):
        record["refusal"] = "request id correlation failed"
        return (*_result(401, envelope, error={"code": "unauthenticated", "message": "correlation"}), record)
    if claims.get("workspace_id") != WORKSPACE_ID:
        record["refusal"] = "assertion names a foreign workspace"
        return (*_result(403, envelope, error={"code": "workspace-mismatch", "message": "foreign"}), record)
    if envelope.get("repo_id") != REPO_ID or REPO_ID not in (claims.get("repo_ids") or ()):
        record["refusal"] = "repository outside this workspace"
        return (*_result(403, envelope, error={"code": "repo-not-bound", "message": "repo"}), record)
    if envelope.get("catalog_revision") != CATALOG_REVISION:
        record["refusal"] = "stale catalog"
        return (*_result(409, envelope, error={"code": "stale-catalog", "message": "stale"}), record)
    operation = envelope.get("operation")
    arguments = envelope.get("arguments") or {}
    if operation == OPERATION_LIST:
        items = [
            {key: item[key] for key in _LIST_FIELDS}
            for item in ITEMS
            if item["status"] != "done"
        ]
        result = {"authority": "sprintctl", "as_of": AS_OF, "state": "ok", "items": items}
        return (*_result(200, envelope, result=result), record)
    if operation == OPERATION_ITEM:
        work_id = arguments.get("work_id")
        found = next((item for item in ITEMS if item["work_id"] == work_id), None)
        if found is None:
            return (*_result(404, envelope, error={"code": "item-not-found", "message": "no such work item"}), record)
        result = {"authority": "sprintctl", "as_of": AS_OF, "state": "ok", "item": found}
        return (*_result(200, envelope, result=result), record)
    record["refusal"] = "unknown operation"
    return (*_result(404, envelope, error={"code": "unknown-operation", "message": "unknown"}), record)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/health":
            self._send(200, {"status": "live", "workspace_id": WORKSPACE_ID})
        elif self.path == "/api/catalog/v1":
            self._send(
                200,
                {
                    "revision": CATALOG_REVISION,
                    "operations": [{"name": OPERATION_LIST}, {"name": OPERATION_ITEM}],
                },
            )
        elif self.path == "/_calls":
            with _lock:
                self._send(200, {"workspace_id": WORKSPACE_ID, "calls": list(_calls)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path == "/_calls/reset":
            with _lock:
                _calls.clear()
            self._send(200, {"reset": True})
            return
        if self.path != "/api/invoke/v1":
            self._send(404, {"error": "not found"})
            return
        try:
            envelope = json.loads(raw)
        except ValueError:
            self._send(400, {"error": "invalid JSON"})
            return
        status, body, record = _invoke(self.headers, envelope)
        record["status"] = status
        with _lock:
            _calls.append(record)
        self._send(status, body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write(f"stub[{WORKSPACE_ID}] {format % args}\n")


def main() -> None:
    port = int(os.environ.get("STUB_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"stub upstream for {WORKSPACE_ID}/{REPO_ID} on :{port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
