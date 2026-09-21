from __future__ import annotations

import httpx
import pytest

from vuoro_service.mcp_surface import (
    BearerGrant,
    SCOPE_WORK_READ,
    TOOL_ORDER,
    WorkReleaseDetail,
    WorkReleaseSummary,
    create_mcp_app,
)
from vuoro_service.rate_limit import RateLimiter


TOKEN = "test-static-bearer-token"


class _FakeWorkSource:
    def list_ready_work(self):
        return [
            WorkReleaseSummary(
                work_id="w-2", title="Second", repo_id="vuoro", priority=1
            ),
            WorkReleaseSummary(
                work_id="w-1", title="First", repo_id="vuoro", priority=2
            ),
        ]

    def describe_work(self, work_id: str):
        if work_id != "w-1":
            return None
        return WorkReleaseDetail(
            work_id="w-1",
            title="First",
            repo_id="vuoro",
            description="the first item",
            acceptance=("does the thing",),
            provenance=("agentops#1",),
            prior_attempts=(),
        )


def _app(**kwargs):
    kwargs.setdefault(
        "tokens", {TOKEN: BearerGrant(scopes=frozenset({SCOPE_WORK_READ}))}
    )
    kwargs.setdefault("work_source", _FakeWorkSource())
    return create_mcp_app(**kwargs)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_get_is_405() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.get("/mcp")
    assert response.status_code == 405


@pytest.mark.anyio
async def test_delete_is_405() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.delete("/mcp")
    assert response.status_code == 405


@pytest.mark.anyio
async def test_missing_bearer_is_401() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.anyio
async def test_wrong_bearer_is_401() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers("not-the-token"),
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_origin_not_in_allowlist_is_rejected() -> None:
    app = _app(allowed_origins=frozenset({"https://claude.ai"}))
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**_auth_headers(), "Origin": "https://evil.example"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == -32002


@pytest.mark.anyio
async def test_origin_in_allowlist_is_accepted() -> None:
    app = _app(allowed_origins=frozenset({"https://claude.ai"}))
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**_auth_headers(), "Origin": "https://claude.ai"},
        )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_absent_origin_header_is_accepted_by_default() -> None:
    # Server-to-server MCP clients typically send no Origin header at all.
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(),
        )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_protocol_version_header_body_mismatch_is_rejected() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2026-07-28"},
            },
            headers={**_auth_headers(), "MCP-Protocol-Version": "2025-06-18"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


@pytest.mark.anyio
async def test_mcp_name_header_body_mismatch_is_rejected() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "describe_work", "arguments": {"work_id": "w-1"}},
            },
            headers={**_auth_headers(), "Mcp-Name": "list_ready_work"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


@pytest.mark.anyio
async def test_matching_headers_are_accepted() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_ready_work", "arguments": {}},
            },
            headers={
                **_auth_headers(),
                "Mcp-Name": "list_ready_work",
                "Mcp-Method": "tools/call",
            },
        )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_initialize_handshake_legacy_era() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            headers=_auth_headers(),
        )
    body = response.json()
    assert response.status_code == 200
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert body["result"]["resultType"] == "initialize-result"
    assert body["result"]["serverInfo"]["name"] == "vuoro-mcp-read-surface"


@pytest.mark.anyio
async def test_notifications_initialized_gets_no_body() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_auth_headers(),
        )
    assert response.status_code == 202
    assert response.content == b""


@pytest.mark.anyio
async def test_server_discover_current_era_no_initialize_needed() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
            headers={
                **_auth_headers(),
                "MCP-Protocol-Version": "2026-07-28",
            },
        )
    body = response.json()
    assert response.status_code == 200
    assert body["result"]["resultType"] == "discover-result"
    tool_names = [tool["name"] for tool in body["result"]["tools"]]
    assert tool_names == list(TOOL_ORDER)


@pytest.mark.anyio
async def test_tools_list_is_deterministically_ordered() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(),
        )
    body = response.json()
    tool_names = [tool["name"] for tool in body["result"]["tools"]]
    assert tool_names == ["list_ready_work", "describe_work"]
    assert body["result"]["resultType"] == "tools-list-result"


@pytest.mark.anyio
async def test_list_ready_work_call_has_result_type_ttl_and_cache_scope() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_ready_work", "arguments": {}},
            },
            headers=_auth_headers(),
        )
    body = response.json()["result"]
    assert body["resultType"] == "tool-call-result"
    assert body["ttlMs"] == 15_000
    assert body["cacheScope"] == "private"
    assert body["isError"] is False
    work_ids = [w["work_id"] for w in body["structuredContent"]["work_releases"]]
    assert work_ids == ["w-2", "w-1"]


@pytest.mark.anyio
async def test_list_ready_work_respects_limit() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_ready_work", "arguments": {"limit": 1}},
            },
            headers=_auth_headers(),
        )
    body = response.json()["result"]
    assert len(body["structuredContent"]["work_releases"]) == 1


@pytest.mark.anyio
async def test_describe_work_found() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "describe_work", "arguments": {"work_id": "w-1"}},
            },
            headers=_auth_headers(),
        )
    body = response.json()["result"]
    assert body["isError"] is False
    assert body["structuredContent"]["work_id"] == "w-1"
    assert body["structuredContent"]["acceptance"] == ["does the thing"]


@pytest.mark.anyio
async def test_describe_work_not_found_is_isError_not_jsonrpc_error() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "describe_work", "arguments": {"work_id": "nope"}},
            },
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    body = response.json()["result"]
    assert body["isError"] is True
    assert "error" not in response.json()


@pytest.mark.anyio
async def test_describe_work_missing_work_id_is_invalid_params() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "describe_work", "arguments": {}},
            },
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


@pytest.mark.anyio
async def test_unknown_tool_is_invalid_params() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "claim_work", "arguments": {}},
            },
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


@pytest.mark.anyio
async def test_unknown_method_is_method_not_found() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "propose_effect"},
            headers=_auth_headers(),
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


@pytest.mark.anyio
async def test_malformed_json_is_parse_error() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            content=b"{not json",
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


@pytest.mark.anyio
async def test_rate_limit_is_distinguishable_from_auth_failure() -> None:
    limiter = RateLimiter(capacity=1, refill_per_second=0.001)
    app = _app(rate_limiter=limiter)
    async with await _client(app) as client:
        first = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(),
        )
        second = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(),
        )
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.status_code != 401


@pytest.mark.anyio
async def test_default_empty_work_source_returns_no_results() -> None:
    app = create_mcp_app(
        tokens={TOKEN: BearerGrant(scopes=frozenset({SCOPE_WORK_READ}))}
    )
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_ready_work", "arguments": {}},
            },
            headers=_auth_headers(),
        )
    body = response.json()["result"]
    assert body["structuredContent"]["work_releases"] == []


@pytest.mark.anyio
async def test_scope_without_work_read_is_rejected() -> None:
    app = create_mcp_app(
        tokens={TOKEN: BearerGrant(scopes=frozenset({"vuoro:something.else"}))}
    )
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(),
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_mcp_method_header_body_mismatch_is_rejected() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
            headers={**_auth_headers(), "Mcp-Method": "tools/call"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


@pytest.mark.anyio
async def test_missing_method_is_invalid_request() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1},
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


@pytest.mark.anyio
async def test_json_array_body_is_invalid_request() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json=[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


@pytest.mark.anyio
async def test_basic_auth_with_correct_token_value_is_401() -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Basic {TOKEN}"},
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
