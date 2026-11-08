from __future__ import annotations

import asyncio
import json
from uuid import UUID

import httpx
import pytest

from vuoro_client import AsyncVuoroClient, Profile
from vuoro_client.errors import InvocationRejectedError


CATALOG = {
    "revision": "catalog-1",
    "operations": [
        {
            "name": "work.example",
            "input_schema": {"type": "object", "additionalProperties": False},
            "result_schema": {"type": "object", "required": ["ok"]},
        }
    ],
}


def _run_invoke(*, status_code=200, envelope=None, request_id="request-1"):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/meta/v1/handshake":
            return httpx.Response(
                200,
                json={
                    "client_protocol": {"minimum": 1, "maximum": 1},
                    "environment": {"name": "dev", "environment_class": "development"},
                    "invocation_schema_versions": ["invocation/v1"],
                },
            )
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=CATALOG, headers={"etag": "catalog-1"})
        requests.append(request)
        return httpx.Response(status_code, json=envelope or {"status": "accepted", "result": {"ok": True}})

    async def invoke():
        async with AsyncVuoroClient(
            Profile("dev", "https://vuoro.example", "file:/credential", "dev"),
            lambda _reference: "token",
            transport=httpx.MockTransport(handler),
        ) as client:
            options = {
                "basis_revision": "basis-1",
                "idempotency_key": "key-1",
                "repo_id": "repo-1",
            }
            if request_id is not None:
                options["request_id"] = request_id
            result = await client.invoke("work.example", {}, **options)
            return result, client

    result, client = asyncio.run(invoke())
    return result, client, requests


def test_v1_request_bytes_and_endpoint_are_frozen() -> None:
    result, _client, requests = _run_invoke()
    assert result == {"ok": True}
    assert requests[0].url.path == "/api/invoke/v1"
    assert requests[0].content == (
        b'{"schema_version":"invocation/v1","request_id":"request-1",'
        b'"operation":"work.example","arguments":{},"catalog_revision":"catalog-1",'
        b'"basis_revision":"basis-1","idempotency_key":"key-1","repo_id":"repo-1"}'
    )
    assert requests[0].headers["x-request-id"] == "request-1"
    # Regression guard: the retired invocation/v2 proof carrier must not
    # reappear on the wire. v1 is the only envelope the client emits.
    assert b"transient_credentials" not in requests[0].content


def test_generated_request_id_is_shared_by_header_and_body() -> None:
    _result, _client, requests = _run_invoke(request_id=None)
    payload = json.loads(requests[0].content)
    assert payload["request_id"] == requests[0].headers["x-request-id"]
    assert UUID(payload["request_id"]).version == 4


def test_request_id_header_is_only_added_to_invoke_route() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/meta/v1/handshake":
            return httpx.Response(200, json={
                "client_protocol": {"minimum": 1, "maximum": 1},
                "environment": {"name": "dev", "environment_class": "development"},
                "invocation_schema_versions": ["invocation/v1"],
            })
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=CATALOG, headers={"etag": "catalog-1"})
        return httpx.Response(200, json={"status": "accepted", "result": {"ok": True}})

    async def invoke() -> None:
        async with AsyncVuoroClient(
            Profile("dev", "https://vuoro.example", "file:/credential", "dev"),
            lambda _reference: "token",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.invoke("work.example", {}, request_id="caller-id")

    asyncio.run(invoke())
    by_path = {request.url.path: request for request in requests}
    assert "/api/catalog/v1" in by_path
    for path, request in by_path.items():
        if path != "/api/invoke/v1":
            assert "x-request-id" not in request.headers
    assert by_path["/api/invoke/v1"].headers["x-request-id"] == "caller-id"


def test_error_mapping_and_stale_catalog_reset() -> None:
    envelope = {"status": "rejected", "error": {"code": "stale-catalog", "message": "refresh"}}
    with pytest.raises(InvocationRejectedError) as exc_info:
        _run_invoke(status_code=409, envelope=envelope)
    assert exc_info.value.code == "stale-catalog"
    assert exc_info.value.status_code == 409
