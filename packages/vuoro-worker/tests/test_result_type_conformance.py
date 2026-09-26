"""2026-07-28 result-type conformance for the internal MCP server -- the
same rule vuoro-mcp-edge's `test_results_satisfy_the_2026_07_28_client_contract`
guards: resultType is the completion kind, never a per-method or per-tool
name, and list/read results carry an integer ttlMs and a public/private
cacheScope. A per-method resultType (here, the tool's own domain-kind
label, e.g. "work_list") hid every Vuoro tool from Claude Code 2.1.283 in
the vuoro-mcp-edge incident on 2026-09-26 ("Unsupported result type
'tools-list-result' for tools/list"); this server had the identical bug.
"""

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


def _call(client: httpx.Client, method: str, params: dict | None = None, *, auth: bool = True):
    headers = {"authorization": f"Bearer {TOKEN}"} if auth else {}
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=headers,
    )


_LIST_OR_READ_METHODS = {"server/discover", "list_ready_work", "describe_work"}


@pytest.mark.parametrize(
    ("method", "params", "auth"),
    [
        ("initialize", {}, False),
        ("server/discover", {}, False),
        ("list_ready_work", {}, True),
        ("describe_work", {"subject": "item-1"}, True),
        ("claim_work", {"subject": "item-1"}, True),
    ],
)
def test_results_satisfy_the_2026_07_28_client_contract(client, method, params, auth) -> None:
    response = _call(client, method, params, auth=auth)
    result = response.json()["result"]
    assert result["resultType"] in {"complete", "input_required", "task"}
    assert result["resultType"] == "complete"
    if method in _LIST_OR_READ_METHODS:
        assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
        assert result["cacheScope"] in {"public", "private"}


def test_tool_calls_carry_their_domain_kind_separately_from_result_type(client) -> None:
    response = _call(client, "list_ready_work", {})
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["kind"] == "work_list"
    assert result["content"]["items"][0]["subject"] == "item-1"

    response = _call(client, "describe_work", {"subject": "item-1"})
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["kind"] == "work_detail"


def test_claim_work_result_type_is_complete_not_the_domain_lease_kind(client) -> None:
    response = _call(client, "claim_work", {"subject": "item-1"})
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["kind"] == "lease"
