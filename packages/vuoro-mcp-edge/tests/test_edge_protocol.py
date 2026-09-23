"""Protocol behaviour of the MCP server: methods, eras, envelopes, headers."""

from __future__ import annotations

import pytest
from edge_support import FakeShell, edge_client, rpc
from vuoro_mcp_edge.server import (
    CURRENT_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    MCP_PATH,
    TOOL_ORDER,
)


@pytest.fixture
def client(keys):
    return edge_client(keys[0], FakeShell())


def test_get_is_405_with_allow_post(client) -> None:
    response = client.get(MCP_PATH)
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_delete_is_405_with_allow_post(client) -> None:
    response = client.delete(MCP_PATH)
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


@pytest.mark.parametrize("version", sorted(LEGACY_PROTOCOL_VERSIONS))
def test_legacy_era_initialize_echoes_the_client_version(client, auth, version) -> None:
    response = client.post(
        MCP_PATH,
        headers=auth,
        json=rpc(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1"},
            },
        ),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == version
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["resultType"] == "initialize-result"
    assert "mcp-session-id" not in response.headers


def test_legacy_session_initialize_then_initialized_then_tools_list(client, auth) -> None:
    opened = client.post(
        MCP_PATH, headers=auth, json=rpc("initialize", {"protocolVersion": "2025-06-18"})
    )
    assert opened.json()["result"]["protocolVersion"] == "2025-06-18"
    initialized = client.post(
        MCP_PATH,
        headers=auth,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code == 202
    assert initialized.content == b""
    listed = client.post(MCP_PATH, headers=auth, json=rpc("tools/list"))
    assert [tool["name"] for tool in listed.json()["result"]["tools"]] == list(TOOL_ORDER)


def test_unknown_protocol_version_falls_back_to_current(client, auth) -> None:
    response = client.post(
        MCP_PATH, headers=auth, json=rpc("initialize", {"protocolVersion": "1999-01-01"})
    )
    assert response.json()["result"]["protocolVersion"] == CURRENT_PROTOCOL_VERSION


def test_current_era_discover_without_any_handshake(client, auth) -> None:
    response = client.post(
        MCP_PATH,
        headers={**auth, "MCP-Protocol-Version": CURRENT_PROTOCOL_VERSION},
        json=rpc("server/discover"),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == CURRENT_PROTOCOL_VERSION
    assert result["resultType"] == "discover-result"
    assert [tool["name"] for tool in result["tools"]] == list(TOOL_ORDER)
    assert CURRENT_PROTOCOL_VERSION in result["supportedVersions"]


def test_tools_list_order_is_fixed_and_envelope_carries_cache_fields(client, auth) -> None:
    result = client.post(MCP_PATH, headers=auth, json=rpc("tools/list")).json()["result"]
    assert [tool["name"] for tool in result["tools"]] == ["list_ready_work", "describe_work"]
    assert result["resultType"] == "tools-list-result"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "none"


def test_describe_work_takes_an_integer_work_id(client, auth) -> None:
    tools = client.post(MCP_PATH, headers=auth, json=rpc("tools/list")).json()["result"]["tools"]
    describe = next(tool for tool in tools if tool["name"] == "describe_work")
    assert describe["inputSchema"]["properties"]["work_id"] == {
        "type": "integer",
        "minimum": 1,
        "description": "The work item id, as listed by list_ready_work.",
    }
    listing = next(tool for tool in tools if tool["name"] == "list_ready_work")
    limit = listing["inputSchema"]["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 50, 50)


@pytest.mark.parametrize(
    ("headers", "body", "fragment"),
    [
        (
            {"MCP-Protocol-Version": "2025-06-18"},
            rpc("initialize", {"protocolVersion": "2026-07-28"}),
            "MCP-Protocol-Version",
        ),
        ({"Mcp-Method": "tools/list"}, rpc("server/discover"), "Mcp-Method"),
        (
            {"Mcp-Name": "describe_work"},
            rpc("tools/call", {"name": "list_ready_work"}),
            "Mcp-Name",
        ),
    ],
)
def test_header_body_mismatch_is_32020(client, auth, headers, body, fragment) -> None:
    response = client.post(MCP_PATH, headers={**auth, **headers}, json=body)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == -32020
    assert fragment in error["message"]


def test_matching_headers_are_accepted(client, auth) -> None:
    response = client.post(
        MCP_PATH,
        headers={**auth, "Mcp-Method": "tools/list"},
        json=rpc("tools/list"),
    )
    assert response.status_code == 200


def test_unknown_method_is_32601(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=rpc("resources/list"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


def test_invalid_json_is_parse_error(client, auth) -> None:
    response = client.post(
        MCP_PATH, headers={**auth, "content-type": "application/json"}, content=b"{not json"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


def test_non_object_body_is_invalid_request(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=[rpc("tools/list")])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


def test_responses_are_plain_json_with_no_session(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=rpc("tools/list"))
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers


def test_unknown_tool_is_invalid_params(client, auth) -> None:
    response = client.post(
        MCP_PATH, headers=auth, json=rpc("tools/call", {"name": "claim_work"})
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
