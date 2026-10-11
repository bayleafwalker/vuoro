from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from typing import Any

import httpx
import pytest
from edge_support import (
    ISSUER,
    REPO_ID,
    SUBJECT,
    WORKSPACE_ID,
    assertion,
    call,
    identity_headers,
    key_pair,
    resolver,
    rpc,
)
from vuoro_mcp_edge.composition import build_toolsets
from vuoro_mcp_edge.read_tools import CONTINUATION_DELTA
from vuoro_mcp_edge.runs import InMemoryRunRegistry, RunBinding
from vuoro_mcp_edge.server import MCP_PATH, create_edge_app
from vuoro_mcp_edge.toolsets import ToolFailure, ToolsetContext
from vuoro_mcp_edge.work_link_tools import URI_PREFIX, build_toolset, decode_work_link
from vuoro_mcp_edge.work_source import ForwardedIdentity
from vuoro_service.identity import Identity

WORKSPACE = "workspace-a"
REPO = "repo-a"
RUN_A = "run_00000000000000000000000000"
RUN_B = "run_11111111111111111111111111"
_MANIFEST = {
    "harness_id": "test",
    "harness_build": "1",
    "model_id": "test",
    "recipe_id": "test",
    "observed_profile": {},
}


def _identity(
    principal: str = "principal-a",
    *,
    workspace: str = WORKSPACE,
    repo: str = REPO,
    client: str = "client-a",
    grant: str = "grant-a",
    authorities: frozenset[str] = frozenset({"work:read"}),
) -> ForwardedIdentity:
    principal_id = principal if ":" in principal else f"github:{principal}:0"
    identity = Identity(
        actor=principal_id,
        environment="test",
        authorities=authorities,
        repo_ids=frozenset({repo}),
        workspace_id=workspace,
        principal_id=principal_id,
        client_id=client,
        grant_id=grant,
    )
    return ForwardedIdentity(
        assertion="test.assertion",
        request_id="request-1",
        repo_id=repo,
        identity=identity,
    )


class OwnerSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], ForwardedIdentity]] = []
        self.predecessors: dict[str, str | None] = {}
        self.response_mutation: str | None = None
        self.stale = False
        self._client = httpx.AsyncClient(
            base_url="http://shell",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"operations": [{"name": CONTINUATION_DELTA}]}
                )
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _invoke(
        self, operation: str, args: dict[str, Any], caller: ForwardedIdentity
    ) -> dict[str, Any]:
        self.calls.append((operation, deepcopy(args), caller))
        if self.stale:
            from vuoro_mcp_edge.errors import WorkSourceUnavailable

            raise WorkSourceUnavailable(
                "stale-checkpoint", "owner checkpoint is stale", upstream=True
            )
        item = args["item_id"]
        delta: dict[str, Any] = {
            "schema": "sprintctl-work-continuation-delta/v1",
            "authorization_scope": {
                "repo_id": caller.repo_id,
                "run_id": args["run_id"],
                "run_owned": True,
            },
            "predecessor_run_id": self.predecessors.get(args["run_id"]),
            "work": {"item_id": item},
            "current": {
                "repo_id": caller.repo_id,
                "item_id": item,
                "work_edit_revision": "edit:3",
                "status_revision": "status:2",
                "current_release_digest": "a" * 64,
                "current_decision_digest": None,
                "current_reference_set_digest": None,
            },
            "authorizes_action": False,
        }
        if self.response_mutation == "run":
            delta["authorization_scope"]["run_id"] = "run_22222222222222222222222222"
        elif self.response_mutation == "repo":
            delta["current"]["repo_id"] = "repo-foreign"
        elif self.response_mutation == "item":
            delta["work"]["item_id"] = item + 1
        elif self.response_mutation == "item-bool":
            delta["work"]["item_id"] = True
        elif self.response_mutation == "not-owned":
            delta["authorization_scope"]["run_owned"] = False
        elif self.response_mutation == "missing-predecessor":
            delta.pop("predecessor_run_id")
        elif self.response_mutation == "authorizes":
            delta["authorizes_action"] = True
        envelope = {"repo_id": caller.repo_id, "delta": delta}
        if self.response_mutation == "extra-envelope":
            envelope["unexpected"] = "forged"
        return envelope


class ContinuationRegistry(InMemoryRunRegistry):
    supports_continuation = True


def _set(source: OwnerSource, registry: InMemoryRunRegistry):
    context = ToolsetContext(
        env={},
        work_source=source,
        runs=registry,
    )
    toolset = build_toolset(context)
    assert toolset is not None
    return {tool.name: tool for tool in toolset.tools}


def _call(tools, name: str, arguments: dict[str, Any], caller: ForwardedIdentity):
    spec = tools[name]
    return asyncio.run(spec.run(spec.parse(arguments), caller))


def _run(
    registry: InMemoryRunRegistry,
    caller: ForwardedIdentity,
    run_id: str,
    *,
    predecessor: str | None = None,
) -> None:
    registry._runs[run_id] = RunBinding(
        principal_id=caller.identity.principal_id,
        workspace_id=caller.identity.workspace_id,
        repo_id=caller.repo_id,
        client_id=caller.identity.client_id,
        grant_id=caller.identity.grant_id,
    )
    registry._predecessors[run_id] = predecessor


def _pair():
    source = OwnerSource()
    registry = ContinuationRegistry()
    maker = _identity()
    reader = _identity("principal-b", client="client-b", grant="grant-b")
    _run(registry, maker, RUN_A)
    _run(registry, reader, RUN_B, predecessor=RUN_A)
    source.predecessors[RUN_A] = None
    source.predecessors[RUN_B] = RUN_A
    return source, registry, _set(source, registry), maker, reader


def test_maker_reads_owned_run_and_mints_canonical_link_with_full_checkpoint():
    source, _, tools, maker, _ = _pair()
    made = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)
    link = decode_work_link(made["work_link"])
    assert link["workspace_id"] == WORKSPACE and link["repo_id"] == REPO
    assert link["item_id"] == 17 and link["predecessor_run_id"] == RUN_A
    assert link["owner_checkpoint"] == {
        "repo_id": REPO,
        "item_id": 17,
        "work_edit_revision": "edit:3",
        "status_revision": "status:2",
        "current_release_digest": "a" * 64,
        "current_decision_digest": None,
        "current_reference_set_digest": None,
    }
    assert source.calls[0][0:2] == (
        CONTINUATION_DELTA,
        {"run_id": RUN_A, "item_id": 17, "limit": 1},
    )


def test_reader_uses_b_run_and_returns_the_owner_delta_unchanged():
    source, _, tools, maker, reader = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    result = _call(
        tools, "read_work_link", {"run_id": RUN_B, "work_link": link}, reader
    )
    assert result["predecessor_run_id"] == RUN_A
    assert result["authorizes_action"] is False
    assert source.calls[-1][0:2] == (
        CONTINUATION_DELTA,
        {
            "run_id": RUN_B,
            "item_id": 17,
            "limit": 1,
            "checkpoint": decode_work_link(link)["owner_checkpoint"],
        },
    )


def test_inspector_returns_local_identifiers_with_unknown_facts_only():
    source, _, tools, maker, reader = _pair()
    uri = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    call_count = len(source.calls)
    result = _call(tools, "inspect_work_link", {"work_link": uri}, reader)
    assert result == {
        "predecessor_run_id": RUN_A,
        "item_id": 17,
        "owner_facts": "UNKNOWN",
        "authorizes_action": False,
    }
    assert len(source.calls) == call_count


@pytest.mark.parametrize(
    "change", [{"workspace": "workspace-foreign"}, {"repo": "repo-foreign"}]
)
def test_inspector_refuses_foreign_scope_without_owner_call(change):
    source, _, tools, maker, _ = _pair()
    uri = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    call_count = len(source.calls)
    caller = _identity("principal-c", client="client-c", grant="grant-c", **change)
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "inspect_work_link", {"work_link": uri}, caller)
    assert failure.value.code == "work-link-scope-mismatch"
    assert len(source.calls) == call_count


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": "workspace-foreign"},
        {"repo": "repo-foreign"},
    ],
)
def test_foreign_workspace_or_repository_refuses_before_owner_read(change):
    source, _, tools, maker, _ = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    before = len(source.calls)
    reader = _identity("principal-b", client="client-b", grant="grant-b", **change)
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "read_work_link", {"run_id": RUN_B, "work_link": link}, reader)
    assert failure.value.code == "work-link-scope-mismatch"
    assert len(source.calls) == before


@pytest.mark.parametrize(
    "caller",
    [
        _identity("principal-a", grant="grant-revoked"),
        _identity("principal-b", client="client-a", grant="grant-a"),
    ],
)
def test_maker_cannot_use_revoked_or_another_principals_grant(caller):
    source, _, tools, _, _ = _pair()
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, caller)
    assert failure.value.code == "run-not-found"
    assert source.calls == []


def test_reader_cannot_treat_a_predecessor_delta_as_a_successor_handoff():
    source, _, tools, maker, _ = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "read_work_link", {"run_id": RUN_A, "work_link": link}, maker)
    assert failure.value.code == "run-predecessor-mismatch"
    assert len(source.calls) == 2


def test_successor_cannot_use_the_predecessors_client_and_grant():
    source, _, tools, maker, _ = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    successor_with_predecessor_grant = _identity(
        "principal-b",
        client="client-a",
        grant="grant-a",
    )
    with pytest.raises(ToolFailure) as failure:
        _call(
            tools,
            "read_work_link",
            {
                "run_id": RUN_B,
                "work_link": link,
            },
            successor_with_predecessor_grant,
        )
    assert failure.value.code == "run-not-found"
    assert len(source.calls) == 1


def test_owner_stale_checkpoint_refusal_is_not_replaced_with_a_delta():
    source, _, tools, maker, reader = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    source.stale = True
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "read_work_link", {"run_id": RUN_B, "work_link": link}, reader)
    assert failure.value.code == "owner-read-unavailable"


@pytest.mark.parametrize(
    "mutation", ["run", "repo", "item", "not-owned", "extra-envelope", "authorizes"]
)
def test_maker_rejects_forged_owner_response_before_minting(mutation):
    source, _, tools, maker, _ = _pair()
    source.response_mutation = mutation
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)
    assert failure.value.code == "owner-response-invalid"


@pytest.mark.parametrize(
    "mutation",
    ["run", "repo", "item", "not-owned", "missing-predecessor", "authorizes"],
)
def test_reader_rejects_forged_or_unlinked_owner_response(mutation):
    source, _, tools, maker, reader = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    source.response_mutation = mutation
    with pytest.raises(ToolFailure):
        _call(tools, "read_work_link", {"run_id": RUN_B, "work_link": link}, reader)


def test_missing_identity_and_missing_successor_predecessor_fail_closed():
    source, registry, tools, maker, _ = _pair()
    link = _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, maker)[
        "work_link"
    ]
    no_identity = ForwardedIdentity(assertion="x", request_id="r", repo_id=REPO)
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 17}, no_identity)
    assert failure.value.code == "run-binding-unavailable"
    # B is a valid current owner run, but is not recorded as a successor of A.
    _run(
        registry,
        _identity("principal-c", client="client-c", grant="grant-c"),
        "run_22222222222222222222222222",
    )
    bad_reader = _identity("principal-c", client="client-c", grant="grant-c")
    with pytest.raises(ToolFailure) as unlinked:
        _call(
            tools,
            "read_work_link",
            {
                "run_id": "run_22222222222222222222222222",
                "work_link": link,
            },
            bad_reader,
        )
    assert unlinked.value.code == "run-predecessor-mismatch"
    assert len(source.calls) == 2


def test_link_reader_rejects_noncanonical_locator_before_owner_read():
    source, _, tools, _, _reader = _pair()
    with pytest.raises(ToolFailure) as failure:
        tools["read_work_link"].parse(
            {"run_id": RUN_B, "work_link": "https://example.test/"}
        )
    assert failure.value.code == "invalid-work-link"
    assert source.calls == []


def test_present_but_null_checkpoint_is_not_treated_as_absent():
    source, _, tools, _, _reader = _pair()
    link = {
        "schema": "vuoro-work-link/v1",
        "workspace_id": WORKSPACE,
        "repo_id": REPO,
        "item_id": 17,
        "predecessor_run_id": RUN_A,
        "owner_checkpoint": None,
    }
    raw = json.dumps(link, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    with pytest.raises(ToolFailure) as failure:
        tools["read_work_link"].parse(
            {"run_id": RUN_B, "work_link": URI_PREFIX + encoded}
        )
    assert failure.value.code == "invalid-work-link"
    assert source.calls == []


def test_boolean_work_item_id_is_not_accepted_as_integer_one():
    source, _, tools, maker, _reader = _pair()
    source.response_mutation = "item-bool"
    with pytest.raises(ToolFailure) as failure:
        _call(tools, "make_work_link", {"run_id": RUN_A, "item_id": 1}, maker)
    assert failure.value.code == "owner-response-invalid"


def test_asgi_lists_and_calls_work_link_only_with_work_read_authority(tmp_path):
    key_path, private = key_pair(tmp_path)
    maker_principal = f"{ISSUER}:{SUBJECT}:0"
    reader_subject = "01K55555555555555555555555"
    reader_principal = f"{ISSUER}:{reader_subject}:0"
    maker = _identity(
        maker_principal,
        workspace=WORKSPACE_ID,
        repo=REPO_ID,
        client="native-a",
        grant="grant-a",
    )
    reader = _identity(
        reader_principal,
        workspace=WORKSPACE_ID,
        repo=REPO_ID,
        client="native-b",
        grant="grant-b",
    )
    registry = ContinuationRegistry()
    _run(registry, maker, RUN_A)
    _run(registry, reader, RUN_B, predecessor=RUN_A)
    source = OwnerSource()
    source.predecessors.update({RUN_A: None, RUN_B: RUN_A})
    toolsets = build_toolsets(ToolsetContext(env={}, work_source=source, runs=registry))
    assert any(
        tool.name == "make_work_link" for group in toolsets for tool in group.tools
    )
    app = create_edge_app(
        identity_resolver=resolver(key_path), work_source=source, toolsets=toolsets
    )

    def maker_headers() -> dict[str, str]:
        return identity_headers(
            assertion(
                private,
                subject=SUBJECT,
                client_id="native-a",
                grant_id="grant-a",
                repo_ids=[REPO_ID],
                workspace_id=WORKSPACE_ID,
            )
        )

    def reader_headers() -> dict[str, str]:
        return identity_headers(
            assertion(
                private,
                subject=reader_subject,
                client_id="native-b",
                grant_id="grant-b",
                repo_ids=[REPO_ID],
                workspace_id=WORKSPACE_ID,
            )
        )

    def no_read_headers() -> dict[str, str]:
        return identity_headers(
            assertion(
                private,
                subject="01K66666666666666666666666",
                client_id="native-c",
                grant_id="grant-c",
                authorities=[],
                repo_ids=[REPO_ID],
                workspace_id=WORKSPACE_ID,
            )
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge"
        ) as client:
            listed_response = await client.post(
                MCP_PATH, headers=maker_headers(), json=rpc("tools/list")
            )
            listed = listed_response.json()
            names = [tool["name"] for tool in listed["result"]["tools"]]
            assert {"make_work_link", "inspect_work_link", "read_work_link"} <= set(
                names
            )
            made_response = await client.post(
                MCP_PATH,
                headers=maker_headers(),
                json=call("make_work_link", {"run_id": RUN_A, "item_id": 17}),
            )
            uri = made_response.json()["result"]["structuredContent"]["work_link"]
            owner_calls_before_inspect = len(source.calls)
            inspected_response = await client.post(
                MCP_PATH,
                headers=reader_headers(),
                json=call("inspect_work_link", {"work_link": uri}),
            )
            assert inspected_response.json()["result"]["structuredContent"] == {
                "predecessor_run_id": RUN_A,
                "item_id": 17,
                "owner_facts": "UNKNOWN",
                "authorizes_action": False,
            }
            assert len(source.calls) == owner_calls_before_inspect
            read_response = await client.post(
                MCP_PATH,
                headers=reader_headers(),
                json=call("read_work_link", {"run_id": RUN_B, "work_link": uri}),
            )
            assert (
                read_response.json()["result"]["structuredContent"][
                    "predecessor_run_id"
                ]
                == RUN_A
            )
            no_read_response = await client.post(
                MCP_PATH, headers=no_read_headers(), json=rpc("tools/list")
            )
            assert no_read_response.status_code == 401

    asyncio.run(scenario())
