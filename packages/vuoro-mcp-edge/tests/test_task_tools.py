"""Task creation is opt-in and uses the owner operation and ledger."""

import json

import httpx
import pytest

from edge_support import FakeShell, assertion, call, edge_client, identity_headers, rpc
from vuoro_mcp_edge.record_tools import SprintctlRecordStore
from vuoro_mcp_edge.server import MCP_PATH
from vuoro_mcp_edge.task_tools import OPERATION, build_toolset
from vuoro_mcp_edge.toolsets import ToolsetContext

ARGS = {
    "sprint_id": 17,
    "track_name": "release",
    "title": "Review acceptance evidence",
    "idempotency_key": "create-task-17",
}


@pytest.mark.parametrize(
    ("advertised", "authorities", "expected"),
    [
        (False, ["work:task.create"], "unknown-tool"),
        (True, ["work:read"], "authority-required"),
        (True, ["work:task.create"], None),
    ],
)
def test_task_creation_requires_owner_capability_and_narrow_authority(
    keys, advertised, authorities, expected
):
    writes = []

    def shell(request):
        if request.method == "GET":
            return httpx.Response(200, json={
                "operations": [{"name": OPERATION}] if advertised else [],
            })
        writes.append((json.loads(request.content), request.headers))
        return httpx.Response(200, json={"status": "accepted", "result": {
            "item": {"id": 42}, "track_name": "release",
        }})

    store = SprintctlRecordStore(
        base_url="http://shell", timeout=1, transport=httpx.MockTransport(shell)
    )
    toolset = build_toolset(ToolsetContext(env={}, work_source=None, runs=store))
    client = edge_client(keys[0], FakeShell(), toolsets=(toolset,))

    def post(body):
        signed = assertion(keys[1], authorities=authorities)
        return client.post(MCP_PATH, headers=identity_headers(signed), json=body).json()

    tools = post(rpc("tools/list"))["result"]["tools"]
    assert ("create_task" in [tool["name"] for tool in tools]) is (expected is None)
    result = post(call("create_task", ARGS))
    if expected is not None:
        error = result.get("result", {}).get("structuredContent", {}).get("error", {})
        assert error.get("code") == expected
        assert writes == []
        return

    assert result["result"]["structuredContent"] == {"item_id": 42}
    assert post(call("create_task", ARGS))["result"]["structuredContent"] == {"item_id": 42}
    assert len(writes) == 2
    for envelope, headers in writes:
        assert envelope["operation"] == OPERATION
        assert envelope["repo_id"] == "repo-a"
        assert envelope["arguments"] == ARGS
        assert envelope["idempotency_key"] == ARGS["idempotency_key"]
        assert "X-Vuoro-Identity" in headers

    invalid = post(call("create_task", {**ARGS, "repo_id": "other"}))
    assert invalid["result"]["structuredContent"]["error"]["code"] == "invalid-arguments"
    assert len(writes) == 2
