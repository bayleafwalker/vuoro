"""Tool behaviour: ready filtering, strict emission, failures as tool errors."""

from __future__ import annotations

import json

import httpx
import pytest
from edge_support import (
    FakeShell,
    accepted,
    call,
    edge_client,
    item_record,
    item_result,
    list_record,
    list_result,
    rejected,
)
from vuoro_mcp_edge import server
from vuoro_mcp_edge.server import MCP_PATH

LIST_KEYS = {"work_id", "title", "priority", "status", "blocked", "updated_at"}
ITEM_KEYS = LIST_KEYS | {"created_at", "resolution", "blocked_by"}


def _call(keys, auth, shell, tool, arguments=None):
    response = edge_client(keys[0], shell).post(
        MCP_PATH, headers=auth, json=call(tool, arguments)
    )
    assert response.status_code == 200, response.text
    return response, response.json()["result"]


def _assert_tool_error(result, code: str) -> None:
    assert result["isError"] is True
    assert result["structuredContent"] == {
        "error": {"code": code, "message": result["structuredContent"]["error"]["message"]}
    }
    assert "items" not in result["structuredContent"]
    assert "item" not in result["structuredContent"]
    assert result["ttlMs"] == 0


# -- list_ready_work ---------------------------------------------------------


def test_list_returns_only_unblocked_items_verbatim_in_owner_order(keys, auth) -> None:
    records = [list_record(3), list_record(1, blocked=True), list_record(2, priority=None)]
    shell = FakeShell(accepted(list_result(*records)))
    _, result = _call(keys, auth, shell, "list_ready_work")
    assert result["isError"] is False
    items = result["structuredContent"]["items"]
    assert items == [records[0], records[2]]
    assert all(set(item) == LIST_KEYS for item in items)
    assert result["structuredContent"]["as_of"] == "2026-09-23T10:00:00Z"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert (result["resultType"], result["ttlMs"], result["cacheScope"]) == (
        "tool-call-result",
        15_000,
        "private",
    )


def test_blocked_item_is_absent_from_list_but_describe_shows_it_blocked(keys, auth) -> None:
    shell = FakeShell(
        accepted(list_result(list_record(1), list_record(7, blocked=True))),
        accepted(item_result(item_record(7, blocked_by=[1])), "work.public.item-v1"),
    )
    client = edge_client(keys[0], shell)
    listed = client.post(MCP_PATH, headers=auth, json=call("list_ready_work")).json()["result"]
    assert [item["work_id"] for item in listed["structuredContent"]["items"]] == [1]
    described = client.post(
        MCP_PATH, headers=auth, json=call("describe_work", {"work_id": 7})
    ).json()["result"]
    assert described["isError"] is False
    item = described["structuredContent"]["item"]
    assert item["blocked"] is True
    assert item["blocked_by"] == [1]


def test_an_empty_list_is_a_success_meaning_no_ready_work(keys, auth) -> None:
    _, result = _call(keys, auth, FakeShell(accepted(list_result())), "list_ready_work")
    assert result["isError"] is False
    assert result["structuredContent"]["items"] == []


def test_limit_caps_after_the_ready_filter(keys, auth) -> None:
    records = [list_record(1, blocked=True)] + [list_record(n) for n in range(2, 6)]
    _, result = _call(
        keys, auth, FakeShell(accepted(list_result(*records))), "list_ready_work", {"limit": 2}
    )
    assert [item["work_id"] for item in result["structuredContent"]["items"]] == [2, 3]


def test_default_limit_is_fifty(keys, auth) -> None:
    records = [list_record(n) for n in range(1, 61)]
    _, result = _call(keys, auth, FakeShell(accepted(list_result(*records))), "list_ready_work")
    assert len(result["structuredContent"]["items"]) == 50


@pytest.mark.parametrize("limit", [0, 51, -1, "5", 2.5, True, None])
def test_out_of_range_limit_is_invalid_params(keys, auth, limit) -> None:
    shell = FakeShell()
    response = edge_client(keys[0], shell).post(
        MCP_PATH, headers=auth, json=call("list_ready_work", {"limit": limit})
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
    assert shell.invoke_requests == []


def test_unknown_argument_is_invalid_params(keys, auth) -> None:
    response = edge_client(keys[0], FakeShell()).post(
        MCP_PATH, headers=auth, json=call("list_ready_work", {"sprint_id": 3})
    )
    assert response.json()["error"]["code"] == -32602


# -- strict emission ----------------------------------------------------------

LEAK = "internal notes: customer X contract renewal at risk"


def test_upstream_list_record_carrying_description_is_a_tool_error_and_does_not_leak(
    keys, auth
) -> None:
    shell = FakeShell(accepted(list_result(list_record(1), list_record(2, description=LEAK))))
    response, result = _call(keys, auth, shell, "list_ready_work")
    assert LEAK not in response.text
    assert "customer" not in response.text
    _assert_tool_error(result, "contract-violation")


def test_upstream_item_record_carrying_description_is_a_tool_error_and_does_not_leak(
    keys, auth
) -> None:
    shell = FakeShell(
        accepted(item_result(item_record(1, description=LEAK)), "work.public.item-v1")
    )
    response, result = _call(keys, auth, shell, "describe_work", {"work_id": 1})
    _assert_tool_error(result, "contract-violation")
    assert LEAK not in response.text


@pytest.mark.parametrize("dropped", sorted(LIST_KEYS))
def test_upstream_list_record_missing_a_key_is_a_tool_error(keys, auth, dropped) -> None:
    record = list_record(1)
    del record[dropped]
    _, result = _call(keys, auth, FakeShell(accepted(list_result(record))), "list_ready_work")
    _assert_tool_error(result, "contract-violation")


@pytest.mark.parametrize("dropped", sorted(ITEM_KEYS - LIST_KEYS))
def test_upstream_item_record_missing_a_detail_key_is_a_tool_error(keys, auth, dropped) -> None:
    record = item_record(1)
    del record[dropped]
    shell = FakeShell(accepted(item_result(record), "work.public.item-v1"))
    _, result = _call(keys, auth, shell, "describe_work", {"work_id": 1})
    _assert_tool_error(result, "contract-violation")


def test_extra_envelope_key_is_a_tool_error(keys, auth) -> None:
    body = {**list_result(list_record(1)), "repo_id": "repo-a"}
    _, result = _call(keys, auth, FakeShell(accepted(body)), "list_ready_work")
    _assert_tool_error(result, "contract-violation")


@pytest.mark.parametrize(
    "overrides",
    [
        {"work_id": 0},
        {"work_id": "1"},
        {"title": ""},
        {"title": "x" * 161},
        {"priority": "high"},
        {"status": "archived"},
        {"blocked": 0},
        {"updated_at": None},
    ],
)
def test_ill_typed_list_field_is_a_tool_error(keys, auth, overrides) -> None:
    record = {**list_record(1), **overrides}
    _, result = _call(keys, auth, FakeShell(accepted(list_result(record))), "list_ready_work")
    _assert_tool_error(result, "invalid-response")


@pytest.mark.parametrize(
    "overrides",
    [{"blocked_by": [0]}, {"blocked_by": "3"}, {"resolution": 5}, {"created_at": 1}],
)
def test_ill_typed_item_field_is_a_tool_error(keys, auth, overrides) -> None:
    shell = FakeShell(accepted(item_result(item_record(1, **overrides)), "work.public.item-v1"))
    _, result = _call(keys, auth, shell, "describe_work", {"work_id": 1})
    _assert_tool_error(result, "invalid-response")


def test_non_ok_state_is_a_tool_error_never_an_empty_list(keys, auth) -> None:
    body = {**list_result(), "state": "unavailable"}
    _, result = _call(keys, auth, FakeShell(accepted(body)), "list_ready_work")
    _assert_tool_error(result, "authority-unavailable")


def test_wrong_authority_is_a_tool_error(keys, auth) -> None:
    body = {**list_result(), "authority": "auditctl"}
    _, result = _call(keys, auth, FakeShell(accepted(body)), "list_ready_work")
    _assert_tool_error(result, "invalid-response")


# -- upstream failures ----------------------------------------------------------


def test_upstream_503_rejection_is_a_tool_error_with_no_items(keys, auth) -> None:
    shell = FakeShell(rejected(503, "postgres-runtime-unavailable", "work runtime unavailable"))
    response, result = _call(keys, auth, shell, "list_ready_work")
    assert "items" not in response.text
    _assert_tool_error(result, "postgres-runtime-unavailable")


def test_unknown_work_id_is_item_not_found_tool_error(keys, auth) -> None:
    shell = FakeShell(rejected(404, "item-not-found", "Item #99 not found"))
    _, result = _call(keys, auth, shell, "describe_work", {"work_id": 99})
    _assert_tool_error(result, "item-not-found")
    assert shell.invocations[0]["arguments"] == {"work_id": 99}
    assert shell.invocations[0]["operation"] == "work.public.item-v1"


def test_shell_identity_rejection_is_a_tool_error(keys, auth) -> None:
    shell = FakeShell(rejected(401, "identity-required", "gateway identity assertion is invalid"))
    _, result = _call(keys, auth, shell, "list_ready_work")
    _assert_tool_error(result, "identity-required")


def test_transport_failure_is_a_tool_error(keys, auth) -> None:
    shell = FakeShell(httpx.ConnectError("connection refused"))
    _, result = _call(keys, auth, shell, "list_ready_work")
    _assert_tool_error(result, "transport-error")


def test_http_200_rejected_envelope_is_a_tool_error(keys, auth) -> None:
    body = rejected(503, "postgres-runtime-unavailable").json()
    shell = FakeShell(httpx.Response(200, json=body))
    _, result = _call(keys, auth, shell, "list_ready_work")
    _assert_tool_error(result, "postgres-runtime-unavailable")


def test_non_json_upstream_is_a_tool_error(keys, auth) -> None:
    shell = FakeShell(httpx.Response(502, text="<html>bad gateway</html>"))
    _, result = _call(keys, auth, shell, "list_ready_work")
    _assert_tool_error(result, "invalid-response")


def test_catalog_without_the_contract_operations_is_a_tool_error_naming_them(keys, auth) -> None:
    shell = FakeShell(operations=("work.read.next-work", "work.read.item"))
    _, result = _call(keys, auth, shell, "list_ready_work")
    _assert_tool_error(result, "catalog-mismatch")
    message = result["structuredContent"]["error"]["message"]
    assert "work.public.list-v1" in message and "work.public.item-v1" in message
    assert shell.invoke_requests == []


def test_stale_catalog_refetches_and_retries_exactly_once(keys, auth) -> None:
    shell = FakeShell(
        rejected(409, "stale-catalog"),
        accepted(list_result(list_record(1))),
    )
    _, result = _call(keys, auth, shell, "list_ready_work")
    assert result["isError"] is False
    assert len(shell.catalog_requests) == 2
    assert len(shell.invoke_requests) == 2


def test_second_stale_catalog_surfaces_as_a_tool_error(keys, auth) -> None:
    shell = FakeShell(rejected(409, "stale-catalog"), rejected(409, "stale-catalog"))
    _, result = _call(keys, auth, shell, "list_ready_work")
    _assert_tool_error(result, "stale-catalog")
    assert len(shell.invoke_requests) == 2


def test_catalog_is_checked_once_across_calls_and_results_are_never_cached(keys, auth) -> None:
    shell = FakeShell(accepted(list_result(list_record(1))), accepted(list_result()))
    client = edge_client(keys[0], shell)
    first = client.post(MCP_PATH, headers=auth, json=call("list_ready_work")).json()["result"]
    second = client.post(MCP_PATH, headers=auth, json=call("list_ready_work")).json()["result"]
    assert len(first["structuredContent"]["items"]) == 1
    assert second["structuredContent"]["items"] == []
    assert len(shell.catalog_requests) == 1
    assert len(shell.invoke_requests) == 2


# -- describe_work arguments -----------------------------------------------------


@pytest.mark.parametrize("work_id", ["repo-a#1", "1", 0, -3, 1.5, True, None])
def test_describe_work_rejects_a_non_positive_integer_id(keys, auth, work_id) -> None:
    shell = FakeShell()
    response = edge_client(keys[0], shell).post(
        MCP_PATH, headers=auth, json=call("describe_work", {"work_id": work_id})
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
    assert shell.requests == []


def test_describe_work_returns_the_item_verbatim(keys, auth) -> None:
    record = item_record(4, resolution="shipped", status="done", priority=None)
    shell = FakeShell(accepted(item_result(record), "work.public.item-v1"))
    _, result = _call(keys, auth, shell, "describe_work", {"work_id": 4})
    assert result["structuredContent"]["item"] == record
    assert set(result["structuredContent"]["item"]) == ITEM_KEYS


# -- scope table ---------------------------------------------------------------


def test_a_tool_without_a_scope_row_is_neither_listed_nor_callable(
    keys, auth, monkeypatch
) -> None:
    monkeypatch.delitem(server.TOOL_SCOPES, "describe_work")
    shell = FakeShell()
    client = edge_client(keys[0], shell)
    tools = client.post(
        MCP_PATH, headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["list_ready_work"]
    response = client.post(MCP_PATH, headers=auth, json=call("describe_work", {"work_id": 1}))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
    assert shell.requests == []


def test_every_defined_tool_has_a_scope_row_and_every_scope_an_authority() -> None:
    assert set(server.TOOL_ORDER) == set(server.TOOL_SCOPES)
    assert set(server.TOOL_SCOPES.values()) <= set(server.SCOPE_AUTHORITIES)
    assert set(server.TOOL_SCOPES.values()) == {"read"}
