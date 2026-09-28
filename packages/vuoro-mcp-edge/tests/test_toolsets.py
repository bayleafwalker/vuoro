"""The E2/E3 seam: toolsets, run handles and idempotency (shared contract)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from edge_support import FakeShell, assertion, call, edge_client, identity_headers, rpc
from vuoro_mcp_edge.composition import build_toolsets
from vuoro_mcp_edge.idempotency import (
    IDEMPOTENCY_KEY_SCHEMA,
    InMemoryIdempotencyLedger,
    StoredResult,
    replay_or_conflict,
    request_digest,
    require_key,
)
from vuoro_mcp_edge.runs import (
    RUN_ID,
    InMemoryRunRegistry,
    RunBinding,
    UnavailableRunRegistry,
    binding_for,
)
from vuoro_mcp_edge.server import CURRENT_PROTOCOL_VERSION, MCP_PATH, TOOL_ORDER
from vuoro_mcp_edge.toolsets import (
    WRITE_ANNOTATIONS,
    ToolFailure,
    ToolSet,
    ToolsetContext,
    ToolSpec,
)
from vuoro_mcp_edge.work_source import ForwardedIdentity, ShellWorkSource

EVIDENCE = "work:evidence"


def _record_toolset(seen: list[Any]) -> ToolSet:
    """A stand-in E2 tool: echoes its caller's run binding."""

    def parse(arguments: dict[str, Any]) -> dict[str, Any]:
        if set(arguments) - {"note", "idempotency_key"}:
            raise ToolFailure("invalid-arguments", "unexpected argument")
        require_key(arguments)
        return arguments

    async def run(parsed: dict[str, Any], forwarded: Any) -> dict[str, Any]:
        binding = binding_for(forwarded)
        seen.append(binding)
        if parsed.get("note") == "fail":
            raise ToolFailure("note-refused", "refused on purpose")
        return {"principal": binding.principal_id, "repo": binding.repo_id}

    definition = {
        "name": "write_test_note",
        "title": "Write a test note",
        "description": "Test tool.",
        "inputSchema": {
            "type": "object",
            "properties": {"note": {"type": "string"}, "idempotency_key": IDEMPOTENCY_KEY_SCHEMA},
            "required": ["note", "idempotency_key"],
            "additionalProperties": False,
        },
        "annotations": WRITE_ANNOTATIONS,
    }
    return ToolSet(
        name="record-test",
        tools=(ToolSpec("write_test_note", "record", definition, parse, run),),
    )


def _write_auth(keys: Any) -> dict[str, str]:
    return identity_headers(assertion(keys[1], authorities=["work:read", EVIDENCE]))


def test_default_composition_ships_no_toolset_without_durable_stores() -> None:
    """E2's record bucket lists nothing without a durable run registry, and
    E3's propose bucket lists nothing without a durable intent store: a
    bucket that could only fail every call advertises no tools."""

    context = ToolsetContext(
        env={},
        work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"),
        runs=UnavailableRunRegistry(),
    )
    toolsets = build_toolsets(context)
    names = [tool.name for toolset in toolsets for tool in toolset.tools]
    assert names == []


def test_toolset_tools_follow_the_builtins_in_list_and_discover(keys, auth) -> None:
    client = edge_client(keys[0], FakeShell(), toolsets=(_record_toolset([]),))
    listed = client.post(MCP_PATH, headers=_write_auth(keys), json=rpc("tools/list"))
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == [*TOOL_ORDER, "write_test_note"]
    discovered = client.post(
        MCP_PATH,
        headers={**_write_auth(keys), "MCP-Protocol-Version": CURRENT_PROTOCOL_VERSION},
        json=rpc("server/discover"),
    ).json()["result"]
    assert [tool["name"] for tool in discovered["tools"]] == names
    assert discovered["resultType"] == "complete"


def test_a_toolset_tool_runs_with_the_callers_binding(keys) -> None:
    seen: list[Any] = []
    client = edge_client(keys[0], FakeShell(), toolsets=(_record_toolset(seen),))
    result = client.post(
        MCP_PATH,
        headers=_write_auth(keys),
        json=call("write_test_note", {"note": "hi", "idempotency_key": "key-0001"}),
    ).json()["result"]
    assert result["resultType"] == "complete"
    assert result["isError"] is False
    assert result["structuredContent"] == {"principal": seen[0].principal_id, "repo": "repo-a"}
    assert seen[0].workspace_id
    # A workspace-token-shaped assertion carries no OAuth client or grant.
    assert (seen[0].client_id, seen[0].grant_id) == (None, None)


def test_the_run_binding_carries_the_asserted_client_and_grant(keys) -> None:
    """Contract section 4: once the gateway asserts the OAuth client and
    grant, a run binds to both."""

    seen: list[Any] = []
    client = edge_client(keys[0], FakeShell(), toolsets=(_record_toolset(seen),))
    token = assertion(
        keys[1],
        authorities=["work:read", EVIDENCE],
        client_id="claude-connector",
        grant_id="grant-1",
    )
    result = client.post(
        MCP_PATH,
        headers=identity_headers(token),
        json=call("write_test_note", {"note": "hi", "idempotency_key": "key-0001"}),
    ).json()["result"]
    assert result["isError"] is False
    assert (seen[0].client_id, seen[0].grant_id) == ("claude-connector", "grant-1")


def test_a_toolset_tool_needs_its_bucket_authority(keys, auth) -> None:
    client = edge_client(keys[0], FakeShell(), toolsets=(_record_toolset([]),))
    result = client.post(
        MCP_PATH,
        headers=auth,  # work:read only
        json=call("write_test_note", {"note": "hi", "idempotency_key": "key-0001"}),
    ).json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "authority-required"


def test_without_a_toolset_its_authority_is_refused_at_the_door(keys) -> None:
    client = edge_client(keys[0], FakeShell())
    response = client.post(MCP_PATH, headers=_write_auth(keys), json=rpc("tools/list"))
    assert response.status_code == 401


def test_tool_failures_and_bad_arguments_are_tool_errors(keys) -> None:
    client = edge_client(keys[0], FakeShell(), toolsets=(_record_toolset([]),))
    refused = client.post(
        MCP_PATH,
        headers=_write_auth(keys),
        json=call("write_test_note", {"note": "fail", "idempotency_key": "key-0001"}),
    ).json()["result"]
    assert refused["isError"] is True
    assert refused["structuredContent"]["error"]["code"] == "note-refused"
    no_key = client.post(
        MCP_PATH, headers=_write_auth(keys), json=call("write_test_note", {"note": "x"})
    ).json()["result"]
    assert no_key["structuredContent"]["error"]["code"] == "invalid-arguments"


def test_a_toolset_cannot_shadow_a_tool_or_use_an_unknown_bucket(keys) -> None:
    shadow = ToolSet(
        name="shadow",
        tools=(
            ToolSpec(
                "describe_work",
                "record",
                {"name": "describe_work"},
                lambda arguments: arguments,
                _never,
            ),
        ),
    )
    with pytest.raises(ValueError, match="already registered"):
        edge_client(keys[0], FakeShell(), toolsets=(shadow,))
    with pytest.raises(ValueError, match="unknown bucket"):
        ToolSpec("apply_it", "apply", {"name": "apply_it"}, lambda a: a, _never)


async def _never(parsed: Any, forwarded: Any) -> dict[str, Any]:  # pragma: no cover
    raise AssertionError("must not run")


_FORWARDED = ForwardedIdentity(assertion="a.b.c", request_id="req-1", repo_id="r1")
_MANIFEST = {
    "harness_id": "h", "harness_build": "1", "model_id": "m", "recipe_id": "r",
    "observed_profile": {"instruction_digest": "d", "skill_digests": []},
}


def test_run_registry_binds_runs_to_their_caller() -> None:
    registry = InMemoryRunRegistry()
    mine = RunBinding(
        principal_id="github:1:0", workspace_id="w1", repo_id="r1",
        client_id="claude-connector", grant_id="g1",
    )

    async def scenario() -> None:
        run_id = await registry.register(
            mine, idempotency_key="key-0001", forwarded=_FORWARDED, manifest=_MANIFEST
        )
        assert RUN_ID.fullmatch(run_id)
        assert (
            await registry.register(
                mine, idempotency_key="key-0001", forwarded=_FORWARDED, manifest=_MANIFEST
            )
            == run_id
        )
        assert await registry.resolve(run_id, mine, forwarded=_FORWARDED) == mine
        for other in (
            replace(mine, principal_id="github:2:0"),
            replace(mine, workspace_id="w2"),
            replace(mine, repo_id="r2"),
            replace(mine, client_id="other-client"),
            replace(mine, client_id=None),
            replace(mine, grant_id="g2"),
            replace(mine, grant_id=None),
        ):
            with pytest.raises(ToolFailure) as refused:
                await registry.resolve(run_id, other, forwarded=_FORWARDED)
            assert refused.value.code == "run-not-found"
        with pytest.raises(ToolFailure) as unknown:
            await registry.resolve("run_" + "0" * 26, mine, forwarded=_FORWARDED)
        assert (unknown.value.code, unknown.value.message) == (
            refused.value.code,
            refused.value.message,
        )

    asyncio.run(scenario())


def test_unavailable_registry_fails_closed() -> None:
    registry = UnavailableRunRegistry()
    binding = RunBinding(principal_id="p", workspace_id="w", repo_id="r")
    for attempt in (
        registry.register(
            binding, idempotency_key="key-0001", forwarded=_FORWARDED, manifest=_MANIFEST
        ),
        registry.resolve("run_" + "0" * 26, binding, forwarded=_FORWARDED),
    ):
        with pytest.raises(ToolFailure) as refused:
            asyncio.run(attempt)
        assert refused.value.code == "runs-unavailable"


def test_idempotency_digest_ignores_the_key_and_argument_order() -> None:
    first = request_digest("append_evidence", {"a": 1, "b": [1, 2], "idempotency_key": "k1"})
    again = request_digest("append_evidence", {"b": [1, 2], "a": 1, "idempotency_key": "k2"})
    other_tool = request_digest("write_session_note", {"a": 1, "b": [1, 2]})
    changed = request_digest("append_evidence", {"a": 2, "b": [1, 2]})
    assert first == again
    assert len({first, other_tool, changed}) == 3


def test_idempotency_replays_same_request_and_refuses_a_changed_one() -> None:
    ledger = InMemoryIdempotencyLedger()

    async def scenario() -> None:
        digest = request_digest("t", {"a": 1})
        assert replay_or_conflict(await ledger.lookup("w", "p", "t", "key-0001"), digest) is None
        stored = await ledger.store("w", "p", "t", "key-0001", StoredResult(digest, {"ok": 1}))
        racer = await ledger.store("w", "p", "t", "key-0001", StoredResult("other", {"ok": 2}))
        assert racer == stored
        found = await ledger.lookup("w", "p", "t", "key-0001")
        assert replay_or_conflict(found, digest) == {"ok": 1}
        with pytest.raises(ToolFailure) as conflict:
            replay_or_conflict(found, request_digest("t", {"a": 2}))
        assert conflict.value.code == "idempotency-conflict"
        assert await ledger.lookup("w2", "p", "t", "key-0001") is None

    asyncio.run(scenario())


@pytest.mark.parametrize("key", [None, "", "short", "x" * 129, "has space1", "bad/slash1"])
def test_idempotency_key_shape_is_enforced(key: Any) -> None:
    with pytest.raises(ToolFailure) as refused:
        require_key({} if key is None else {"idempotency_key": key})
    assert refused.value.code == "invalid-arguments"


# ---------------------------------------------------------------------------
# tools/list and server/discover show only what the caller can call
# ---------------------------------------------------------------------------


def _bucket_toolset(bucket: str, tool: str) -> ToolSet:
    def parse(arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments:
            raise ToolFailure("invalid-arguments", "takes no arguments")
        return arguments

    async def run(parsed: dict[str, Any], forwarded: Any) -> dict[str, Any]:
        return {"bucket": bucket}

    definition = {
        "name": tool,
        "title": tool,
        "description": "Test tool.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": WRITE_ANNOTATIONS,
    }
    return ToolSet(name=f"{bucket}-test", tools=(ToolSpec(tool, bucket, definition, parse, run),))


_BUCKET_TOOLSETS = (
    _bucket_toolset("record", "record_tool"),
    _bucket_toolset("coordinate", "coordinate_tool"),
    _bucket_toolset("propose", "propose_tool"),
)


@pytest.mark.parametrize("method", ["tools/list", "server/discover"])
@pytest.mark.parametrize(
    ("authorities", "expected"),
    [
        (["work:read"], list(TOOL_ORDER)),
        (["work:evidence"], ["record_tool"]),
        (["work:claim"], ["coordinate_tool"]),
        (["effect:propose"], ["propose_tool"]),
        (["work:read", "work:claim"], [*TOOL_ORDER, "coordinate_tool"]),
        (
            ["work:read", "work:evidence", "work:claim", "effect:propose"],
            [*TOOL_ORDER, "record_tool", "coordinate_tool", "propose_tool"],
        ),
    ],
)
def test_listing_is_filtered_by_the_callers_authorities(
    keys, method: str, authorities: list[str], expected: list[str]
) -> None:
    client = edge_client(keys[0], FakeShell(), toolsets=_BUCKET_TOOLSETS)
    response = client.post(
        MCP_PATH,
        headers=identity_headers(assertion(keys[1], authorities=authorities)),
        json=rpc(method),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert [tool["name"] for tool in result["tools"]] == expected


def test_listing_with_one_write_toolset(keys) -> None:
    # The door refuses an assertion with no authority, or with one no
    # registered tool uses, so every admitted caller lists at least one tool;
    # the filter only narrows the list to the buckets it can call.
    only_claim = edge_client(keys[0], FakeShell(), toolsets=(_BUCKET_TOOLSETS[1],))
    token = assertion(keys[1], authorities=["work:read"])
    result = only_claim.post(
        MCP_PATH, headers=identity_headers(token), json=rpc("tools/list")
    ).json()["result"]
    assert [tool["name"] for tool in result["tools"]] == list(TOOL_ORDER)
    token = assertion(keys[1], authorities=["work:claim"])
    result = only_claim.post(
        MCP_PATH, headers=identity_headers(token), json=rpc("tools/list")
    ).json()["result"]
    assert [tool["name"] for tool in result["tools"]] == ["coordinate_tool"]


def test_authority_is_checked_before_arguments(keys) -> None:
    client = edge_client(keys[0], FakeShell(), toolsets=_BUCKET_TOOLSETS)
    token = assertion(keys[1], authorities=["work:read"])
    result = client.post(
        MCP_PATH,
        headers=identity_headers(token),
        json=call("coordinate_tool", {"unexpected": 1}),
    ).json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "authority-required"
    # A built-in read tool too: bad arguments from a caller without work:read.
    token = assertion(keys[1], authorities=["work:claim"])
    result = client.post(
        MCP_PATH,
        headers=identity_headers(token),
        json=call("describe_work", {"work_id": "not-an-int"}),
    ).json()["result"]
    assert result["structuredContent"]["error"]["code"] == "authority-required"
