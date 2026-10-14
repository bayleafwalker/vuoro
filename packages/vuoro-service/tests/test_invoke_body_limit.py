from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.body_limit import DEFAULT_INVOKE_BODY_LIMIT_BYTES, InvokeBodyLimitMiddleware


HEADERS = {"X-Vuoro-Client-Protocol": "1", "content-type": "application/json"}


def _client(limit: int) -> TestClient:
    return TestClient(create_app(settings=ServiceSettings(invoke_body_limit_bytes=limit)))


def test_default_limit_fits_a_phase1_upload_envelope() -> None:
    assert DEFAULT_INVOKE_BODY_LIMIT_BYTES == 12 * 1024 * 1024
    assert DEFAULT_INVOKE_BODY_LIMIT_BYTES > 4 * ((8 * 1024 * 1024 + 2) // 3) + 64 * 1024


def test_oversized_invoke_body_is_refused_before_parsing() -> None:
    client = _client(256)
    body = b'{"request_id":"r","operation":"x.y.z","arguments":{"pad":"' + b"a" * 400 + b'"}}'
    response = client.post("/api/invoke/v1", content=body, headers=HEADERS)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request-too-large"


def test_body_within_the_limit_reaches_the_shell_unchanged() -> None:
    client = _client(4096)
    response = client.post(
        "/api/invoke/v1",
        json={"request_id": "r", "operation": "x.y.z", "arguments": {}},
        headers={"X-Vuoro-Client-Protocol": "1"},
    )
    # The envelope was parsed: the shell answers about the operation, not the size.
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown-operation"


def test_other_routes_are_not_limited() -> None:
    client = _client(8)
    assert client.get("/health/live").status_code == 200


def test_chunked_body_is_counted_without_a_declared_length() -> None:
    seen: list[bytes] = []

    async def app(scope, receive, send):
        message = await receive()
        seen.append(message["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run(chunks: list[bytes]) -> int:
        messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
        sent: list[dict] = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "path": "/api/invoke/v1", "method": "POST", "headers": []}
        await InvokeBodyLimitMiddleware(app, limit_bytes=10)(scope, receive, send)
        return sent[0]["status"]

    assert asyncio.run(run([b"12345", b"678901"])) == 413
    assert asyncio.run(run([b"12345", b"67890"])) == 200
    assert seen == [b"1234567890"]


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        InvokeBodyLimitMiddleware(lambda *a: None, limit_bytes=0)
