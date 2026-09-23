"""Protocol behaviour of the MCP server: methods, eras, envelopes, headers."""

from __future__ import annotations

import pytest
from edge_support import FakeShell, edge_client, rpc
from vuoro_mcp_edge.server import CURRENT_PROTOCOL_VERSION, MCP_PATH, TOOL_ORDER

#: Written out, not read from the module, so dropping a revision fails here.
LEGACY_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
CURRENT_VERSION = "2026-07-28"


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


def test_the_current_revision_is_2026_07_28() -> None:
    assert CURRENT_PROTOCOL_VERSION == CURRENT_VERSION


@pytest.mark.parametrize("version", [*LEGACY_VERSIONS, CURRENT_VERSION])
def test_initialize_echoes_every_supported_version(client, auth, version) -> None:
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


@pytest.mark.parametrize("version", LEGACY_VERSIONS)
def test_legacy_session_initialize_then_initialized_then_tools_list(
    client, auth, version
) -> None:
    opened = client.post(
        MCP_PATH, headers=auth, json=rpc("initialize", {"protocolVersion": version})
    )
    assert opened.json()["result"]["protocolVersion"] == version
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
    assert response.json()["result"]["protocolVersion"] == CURRENT_VERSION


@pytest.mark.parametrize("version", [*LEGACY_VERSIONS, CURRENT_VERSION])
def test_every_supported_version_is_accepted_as_a_header(client, auth, version) -> None:
    response = client.post(
        MCP_PATH, headers={**auth, "MCP-Protocol-Version": version}, json=rpc("tools/list")
    )
    assert response.status_code == 200


def test_unsupported_protocol_version_header_is_http_400(client, auth) -> None:
    response = client.post(
        MCP_PATH, headers={**auth, "MCP-Protocol-Version": "2023-01-01"}, json=rpc("tools/list")
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


def test_current_era_discover_without_any_handshake(client, auth) -> None:
    response = client.post(
        MCP_PATH,
        headers={**auth, "MCP-Protocol-Version": CURRENT_VERSION},
        json=rpc("server/discover"),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == CURRENT_VERSION
    assert result["resultType"] == "discover-result"
    assert [tool["name"] for tool in result["tools"]] == list(TOOL_ORDER)
    assert result["supportedVersions"] == sorted([*LEGACY_VERSIONS, CURRENT_VERSION])


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
    assert response.status_code == 200
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


def test_unknown_method_is_32601_over_http_200(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=rpc("resources/list"))
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601
    assert response.json()["id"] == 1


def test_ping_returns_an_empty_result(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=rpc("ping"))
    assert response.status_code == 200
    assert response.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_invalid_json_is_parse_error_over_http_200(client, auth) -> None:
    response = client.post(
        MCP_PATH, headers={**auth, "content-type": "application/json"}, content=b"{not json"
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32700


def test_batch_is_invalid_request_over_http_200(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=[rpc("tools/list")])
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32600


def test_non_object_body_is_invalid_request_over_http_200(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json="tools/list")
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32600


@pytest.mark.parametrize("jsonrpc", [None, "1.0", 2.0])
def test_missing_or_wrong_jsonrpc_version_is_invalid_request(client, auth, jsonrpc) -> None:
    body = rpc("tools/list")
    if jsonrpc is None:
        del body["jsonrpc"]
    else:
        body["jsonrpc"] = jsonrpc
    response = client.post(MCP_PATH, headers=auth, json=body)
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32600


def test_a_request_without_an_id_is_a_notification_and_runs_nothing(keys, auth) -> None:
    shell = FakeShell()
    response = edge_client(keys[0], shell).post(
        MCP_PATH,
        headers=auth,
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "list_ready_work"}},
    )
    assert response.status_code == 202
    assert response.content == b""
    assert shell.requests == []


def test_a_client_response_is_acknowledged_with_202(client, auth) -> None:
    response = client.post(
        MCP_PATH, headers=auth, json={"jsonrpc": "2.0", "id": 7, "result": {}}
    )
    assert response.status_code == 202
    assert response.content == b""


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_a_non_json_content_type_is_refused(client, auth, content_type) -> None:
    headers = dict(auth)
    if content_type is not None:
        headers["content-type"] = content_type
    response = client.post(MCP_PATH, headers=headers, content=b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
    assert response.status_code == 415


def test_json_content_type_with_charset_is_accepted(client, auth) -> None:
    response = client.post(
        MCP_PATH,
        headers={**auth, "content-type": "application/json; charset=utf-8"},
        content=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
    )
    assert response.status_code == 200


def test_a_body_over_64_kib_is_refused_before_parsing(client, auth) -> None:
    body = {**rpc("ping"), "params": {"pad": "x" * (64 * 1024)}}
    response = client.post(MCP_PATH, headers=auth, json=body)
    assert response.status_code == 413


def test_a_body_just_under_64_kib_is_accepted(client, auth) -> None:
    base = len(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":""}}')
    body = {**rpc("ping"), "params": {"pad": "x" * (64 * 1024 - base - 10)}}
    response = client.post(MCP_PATH, headers=auth, json=body)
    assert response.status_code == 200


def test_responses_are_plain_json_with_no_session(client, auth) -> None:
    response = client.post(MCP_PATH, headers=auth, json=rpc("tools/list"))
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers


@pytest.mark.parametrize("params", [{"name": "claim_work"}, {}, {"name": 3}])
def test_unknown_or_missing_tool_is_a_tool_error(client, auth, params) -> None:
    response = client.post(MCP_PATH, headers=auth, json=rpc("tools/call", params))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "unknown-tool"


def test_both_tools_carry_a_title_and_read_only_annotations(client, auth) -> None:
    tools = client.post(MCP_PATH, headers=auth, json=rpc("tools/list")).json()["result"]["tools"]
    for tool in tools:
        assert isinstance(tool["title"], str) and tool["title"]
        assert tool["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
