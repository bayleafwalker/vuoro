"""Shared fixtures for the MCP edge tests: keys, assertions, a fake shell."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from vuoro_service.gateway_identity import GatewayAssertionIdentityResolver

WORKSPACE_ID = "01K11111111111111111111111"
ENVIRONMENT = "vuoro-cloud-ws-01k111111111"
ISSUER = "vuoro-cloud-control"
AUDIENCE = "vuoro-service"
KEY_ID = "gateway-2026-01"
REQUEST_ID = "01K33333333333333333333333"
SUBJECT = "01K44444444444444444444444"
REPO_ID = "repo-a"
CATALOG_REVISION = "rev-1"
AS_OF = "2026-09-23T10:00:00Z"

#: Literal contract names, written out rather than imported from the module
#: under test, so a renamed constant fails the catalog check here.
CATALOG_OPERATIONS: tuple[str, ...] = ("work.public.list-v1", "work.public.item-v1")


def key_pair(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    path = tmp_path / "gateway-public.pem"
    path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return path, private


def resolver(
    key_path: Path, *, allowed_repo_ids: frozenset[str] = frozenset({REPO_ID})
) -> GatewayAssertionIdentityResolver:
    return GatewayAssertionIdentityResolver.from_file(
        key_path,
        issuer=ISSUER,
        audience=AUDIENCE,
        environment=ENVIRONMENT,
        expected_workspace_id=WORKSPACE_ID,
        allowed_repo_ids=allowed_repo_ids,
        key_id=KEY_ID,
    )


def claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "github:123",
        "actor": "github:123",
        "subject": SUBJECT,
        "principal_epoch": 0,
        "workspace_id": WORKSPACE_ID,
        "authorities": ["work:read"],
        "repo_ids": [REPO_ID],
        "request_id": REQUEST_ID,
        "jti": REQUEST_ID,
        "iat": now,
        "nbf": now - 1,
        "exp": now + 30,
    }
    base.update(overrides)
    return base


def assertion(
    private: Ed25519PrivateKey, *, kid: str = KEY_ID, **claim_overrides: Any
) -> str:
    return jwt.encode(
        claims(**claim_overrides),
        private,
        algorithm="EdDSA",
        headers={"kid": kid, "typ": "JWT"},
    )


def identity_headers(token: str, request_id: str = REQUEST_ID) -> dict[str, str]:
    return {"X-Vuoro-Identity": token, "X-Request-Id": request_id}


def list_record(work_id: int, *, blocked: bool = False, **overrides: Any) -> dict[str, Any]:
    record = {
        "work_id": work_id,
        "title": f"Item {work_id}",
        "priority": 1,
        "status": "blocked" if blocked else "pending",
        "blocked": blocked,
        "updated_at": "2026-09-22T09:00:00Z",
    }
    record.update(overrides)
    return record


def item_record(work_id: int, *, blocked_by: list[int] | None = None, **overrides: Any) -> dict[str, Any]:
    blocked_by = blocked_by or []
    record = {
        **list_record(work_id, blocked=bool(blocked_by)),
        "created_at": "2026-09-20T09:00:00Z",
        "resolution": None,
        "blocked_by": blocked_by,
    }
    record.update(overrides)
    return record


def list_result(*records: dict[str, Any]) -> dict[str, Any]:
    return {"authority": "sprintctl", "as_of": AS_OF, "state": "ok", "items": list(records)}


def item_result(record: dict[str, Any]) -> dict[str, Any]:
    return {"authority": "sprintctl", "as_of": AS_OF, "state": "ok", "item": record}


def accepted(result: Any, operation: str = "work.public.list-v1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "schema_version": "invocation-result/v1",
            "request_id": REQUEST_ID,
            "operation": operation,
            "catalog_revision": CATALOG_REVISION,
            "status": "accepted",
            "result": result,
            "error": None,
        },
    )


def rejected(status: int, code: str, message: str = "rejected") -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "schema_version": "invocation-result/v1",
            "request_id": REQUEST_ID,
            "operation": "work.public.list-v1",
            "catalog_revision": CATALOG_REVISION,
            "status": "rejected",
            "result": None,
            "error": {"code": code, "message": message},
        },
    )


class FakeShell:
    """An httpx MockTransport handler standing in for the runtime shell."""

    def __init__(self, *responses: Any, operations: tuple[str, ...] = CATALOG_OPERATIONS) -> None:
        self._responses = list(responses)
        self.operations = operations
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(
                200,
                json={
                    "revision": CATALOG_REVISION,
                    "operations": [{"name": name} for name in self.operations],
                },
            )
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {request.url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def catalog_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/api/catalog/v1"]

    @property
    def invoke_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/api/invoke/v1"]

    @property
    def invocations(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.invoke_requests]


def edge_client(key_path: Path, shell: Any, **resolver_kwargs: Any) -> TestClient:
    from vuoro_mcp_edge.server import create_edge_app
    from vuoro_mcp_edge.work_source import ShellWorkSource

    transport = shell if isinstance(shell, httpx.AsyncBaseTransport) else httpx.MockTransport(shell)
    source = ShellWorkSource(base_url="http://127.0.0.1:8080", transport=transport)
    app = create_edge_app(
        identity_resolver=resolver(key_path, **resolver_kwargs), work_source=source
    )
    return TestClient(app)


def rpc(method: str, params: dict[str, Any] | None = None, id_: int = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return body


def call(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"name": tool}
    if arguments is not None:
        params["arguments"] = arguments
    return rpc("tools/call", params)
