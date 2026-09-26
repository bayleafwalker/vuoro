"""Authorization and User-Agent behavior for handshake/catalog transport.

Regression coverage for agentops#2514: the hosted gateway (vuoro.cloud)
requires the workspace bearer token on both endpoints and answers 401
without it, and Cloudflare in front of it answers 403 to generic
default User-Agents.
"""
from __future__ import annotations

import asyncio

import httpx

from vuoro_client import AsyncVuoroClient, Profile
from vuoro_client.client import USER_AGENT


HANDSHAKE = {
    "client_protocol": {"minimum": 1, "maximum": 1},
    "environment": {"name": "dev", "environment_class": "development"},
    "invocation_schema_versions": ["invocation/v1"],
}

CATALOG = {
    "schema_version": "operation-catalog/v1",
    "revision": "catalog-1",
    "operations": [],
}


def _authenticated_profile() -> Profile:
    return Profile(
        name="test", endpoint="https://vuoro.example",
        credential_ref="file:~/.config/vuoro/credential",
    )


def _anonymous_profile() -> Profile:
    return Profile(name="test", endpoint="https://vuoro.example", credential_ref="")


def test_handshake_carries_authorization_when_credential_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=HANDSHAKE)

    async def run() -> None:
        async with AsyncVuoroClient(
            _authenticated_profile(), lambda _ref: "secret-token",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.handshake()

    asyncio.run(run())
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_handshake_omits_authorization_when_no_credential_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=HANDSHAKE)

    def resolver(_ref: str) -> str:
        raise AssertionError("credential resolver must not be called without a credential_ref")

    async def run() -> None:
        async with AsyncVuoroClient(
            _anonymous_profile(), resolver,
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.handshake()

    asyncio.run(run())
    assert "authorization" not in requests[0].headers


def test_catalog_carries_authorization_when_credential_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=CATALOG, headers={"etag": "catalog-1"})

    async def run() -> None:
        async with AsyncVuoroClient(
            _authenticated_profile(), lambda _ref: "secret-token",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.catalog()

    asyncio.run(run())
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_catalog_omits_authorization_when_no_credential_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=CATALOG, headers={"etag": "catalog-1"})

    def resolver(_ref: str) -> str:
        raise AssertionError("credential resolver must not be called without a credential_ref")

    async def run() -> None:
        async with AsyncVuoroClient(
            _anonymous_profile(), resolver,
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.catalog()

    asyncio.run(run())
    assert "authorization" not in requests[0].headers


def test_catalog_etag_304_refresh_still_works_with_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-none-match") == "catalog-1":
            return httpx.Response(304)
        return httpx.Response(200, json=CATALOG, headers={"etag": "catalog-1"})

    async def run() -> dict:
        async with AsyncVuoroClient(
            _authenticated_profile(), lambda _ref: "secret-token",
            transport=httpx.MockTransport(handler),
        ) as client:
            first = await client.catalog()
            second = await client.catalog()
            return first, second

    first, second = asyncio.run(run())
    assert first == second == CATALOG
    assert len(requests) == 2
    assert requests[1].headers["if-none-match"] == "catalog-1"
    assert all(request.headers["authorization"] == "Bearer secret-token" for request in requests)


def test_user_agent_is_named_on_every_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/meta/v1/handshake":
            return httpx.Response(200, json=HANDSHAKE)
        return httpx.Response(200, json=CATALOG, headers={"etag": "catalog-1"})

    async def run() -> None:
        async with AsyncVuoroClient(
            _authenticated_profile(), lambda _ref: "secret-token",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.handshake()
            await client.catalog()

    asyncio.run(run())
    assert len(requests) == 2
    assert USER_AGENT.startswith("vuoro-client/")
    for request in requests:
        assert request.headers["user-agent"] == USER_AGENT
