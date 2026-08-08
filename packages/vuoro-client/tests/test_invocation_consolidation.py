from __future__ import annotations

import asyncio

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


def _run_invoke(*, credentials=None, status_code=200, envelope=None):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/meta/v1/handshake":
            return httpx.Response(
                200,
                json={
                    "client_protocol": {"minimum": 1, "maximum": 1},
                    "environment": {"name": "dev", "environment_class": "development"},
                    "invocation_schema_versions": ["invocation/v1", "invocation/v2"],
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
            result = await client.invoke(
                "work.example",
                {},
                request_id="request-1",
                basis_revision="basis-1",
                idempotency_key="key-1",
                repo_id="repo-1",
                transient_credentials=credentials,
            )
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
    assert b"transient_credentials" not in requests[0].content


def test_v2_request_bytes_and_endpoint_are_frozen() -> None:
    result, _client, requests = _run_invoke(credentials={"claim": "proof"})
    assert result == {"ok": True}
    assert requests[0].url.path == "/api/invoke/v2"
    assert requests[0].content == (
        b'{"schema_version":"invocation/v2","request_id":"request-1",'
        b'"operation":"work.example","arguments":{},"catalog_revision":"catalog-1",'
        b'"basis_revision":"basis-1","idempotency_key":"key-1","repo_id":"repo-1",'
        b'"transient_credentials":{"claim":"proof"}}'
    )


def test_empty_credentials_keep_v1_and_never_emit_the_field() -> None:
    _result, _client, requests = _run_invoke(credentials={})
    assert requests[0].url.path == "/api/invoke/v1"
    assert b"transient_credentials" not in requests[0].content


@pytest.mark.parametrize("credentials", [None, {"claim": "proof"}])
def test_versions_keep_error_mapping_and_stale_catalog_reset(credentials) -> None:
    envelope = {"status": "rejected", "error": {"code": "stale-catalog", "message": "refresh"}}
    with pytest.raises(InvocationRejectedError) as exc_info:
        _run_invoke(credentials=credentials, status_code=409, envelope=envelope)
    assert exc_info.value.code == "stale-catalog"
    assert exc_info.value.status_code == 409
