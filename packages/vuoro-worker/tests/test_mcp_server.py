from __future__ import annotations

import httpx
import pytest

from vuoro_service.identity import Identity

from vuoro_worker.internal_tools import InternalToolServer, StaticWorkSource, WorkItemDetail
from vuoro_worker.mcp_server import create_mcp_app


TOKEN = "test-bearer-token"


@pytest.fixture()
def client(run_app) -> httpx.Client:
    server = InternalToolServer(
        identities={TOKEN: Identity(actor="worker-a", environment="test", repo_ids=frozenset({"*"}))},
        work_source=StaticWorkSource(
            [WorkItemDetail(subject="item-1", title="Do the thing", acceptance=("it works",))]
        ),
    )
    app = create_mcp_app(server)
    base_url = run_app(app)
    with httpx.Client(base_url=base_url) as c:
        yield c


def test_get_and_delete_are_405(client: httpx.Client):
    assert client.get("/mcp").status_code == 405
    assert client.delete("/mcp").status_code == 405


def test_initialize_legacy_handshake(client: httpx.Client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    body = response.json()
    assert body["result"]["serverInfo"]["name"] == "vuoro-internal-mcp"


def test_server_discover_lists_all_eight_tools_in_order(client: httpx.Client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "server/discover"})
    tools = response.json()["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == sorted(names)
    assert len(names) == 8
    descriptions = {t["name"]: t["description"] for t in tools}
    assert "expire" in descriptions["claim_work"].lower()


def test_tool_call_without_bearer_token_is_rejected(client: httpx.Client):
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "list_ready_work", "params": {}}
    )
    assert response.json()["error"]["code"] == -32001


def test_tool_call_with_bearer_token_succeeds(client: httpx.Client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "list_ready_work", "params": {}},
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    body = response.json()
    assert body["result"]["resultType"] == "work_list"
    assert body["result"]["content"]["items"][0]["subject"] == "item-1"


def test_header_body_method_disagreement_is_rejected(client: httpx.Client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "list_ready_work", "params": {}},
        headers={"authorization": f"Bearer {TOKEN}", "mcp-method": "claim_work"},
    )
    assert response.json()["error"]["code"] == -32020


def test_unsupported_protocol_version_header_is_rejected(client: httpx.Client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 6, "method": "server/discover"},
        headers={"mcp-protocol-version": "1999-01-01"},
    )
    assert response.json()["error"]["code"] == -32020


def test_origin_header_present_and_not_allowlisted_is_refused(client: httpx.Client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "server/discover"},
        headers={"origin": "https://not-allowed.example"},
    )
    assert response.status_code == 403


def test_health_metrics_reflects_calls(client: httpx.Client):
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 8, "method": "list_ready_work", "params": {}},
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    metrics = client.get("/health/metrics").json()
    assert metrics["request_count"] >= 1


def test_unknown_method_is_rejected(client: httpx.Client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 9, "method": "not_a_real_method"},
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    assert response.json()["error"]["code"] == -32601


def test_mcp_name_header_disagreement_is_rejected(client: httpx.Client):
    """The `Mcp-Name` arm of the header/body agreement check. The coordinator
    silent-pass audit for agentops#2469 found this arm had no test at all:
    deleting it outright left all 29 tests green, so only the `Mcp-Method`
    arm was actually load-bearing."""
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 8, "method": "list_ready_work", "params": {}},
        headers={"authorization": f"Bearer {TOKEN}", "mcp-name": "claim_work"},
    )
    assert response.json()["error"]["code"] == -32020
    assert "Mcp-Name" in response.json()["error"]["message"]
