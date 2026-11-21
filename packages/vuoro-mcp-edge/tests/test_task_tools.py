"""Task creation is opt-in and uses the owner operation and ledger."""

import json
import os

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
    client = edge_client(
        keys[0], FakeShell(), toolsets=(toolset,),
        allowed_repo_ids=frozenset({"repo-a", "repo-b"}),
    )

    def post(body, repo_ids=("repo-a",)):
        signed = assertion(keys[1], authorities=authorities, repo_ids=list(repo_ids))
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
    assert invalid["result"]["structuredContent"]["error"]["code"] == "repo-not-authorized"
    assert len(writes) == 2

    multiple = ("repo-a", "repo-b")
    unspecified = post(call("create_task", ARGS), multiple)
    assert unspecified["result"]["structuredContent"]["error"]["code"] == "repo-id-required"
    assert len(writes) == 2
    selected = post(call("create_task", {**ARGS, "repo_id": "repo-b"}), multiple)
    assert selected["result"]["structuredContent"] == {"item_id": 42}
    assert writes[-1][0]["repo_id"] == "repo-b"
    assert writes[-1][0]["arguments"] == ARGS


@pytest.mark.skipif(
    not os.environ.get("VUORO_AUTHORITY_TEST_PG_URL"),
    reason="disposable Sprintctl owner required",
)
def test_mcp_create_and_http_retry_share_the_owner_receipt(keys):
    import asyncio
    import secrets

    from authority_pg_binding import connect
    from edge_support import ENVIRONMENT, REQUEST_ID, resolver
    from sprintctl import pg
    from sprintctl.application import WorkApplication
    from sprintctl.vuoro_adapter import register_work_catalog
    from vuoro_mcp_edge.edge_proof_auth import EdgeProofAuth
    from vuoro_mcp_edge.server import create_edge_app
    from vuoro_mcp_edge.work_source import ShellWorkSource
    from vuoro_service.app import ServiceSettings, create_app
    from vuoro_service.catalog import CatalogRegistry
    from vuoro_service.edge_proof import EdgeProofVerifier

    owner = connect(os.environ["VUORO_AUTHORITY_TEST_PG_URL"])
    try:
        sprint = pg.create_sprint(
            owner, "Task retry", "Verify one durable task", "2026-01-01", "2026-12-31", "active"
        )
        pg.require_compatible_schema(owner)
        registry = CatalogRegistry()
        register_work_catalog(registry, WorkApplication.postgres(owner))

        repo = owner.repo_id
        proof_key = secrets.token_bytes(32)
        shell = create_app(
            settings=ServiceSettings(
                environment_name=ENVIRONMENT,
                environment_class="development", compatibility_state="compatible",
            ),
            registry=registry,
            identity_resolver=resolver(
                keys[0], allowed_repo_ids=frozenset({repo}),
                edge_proofs=EdgeProofVerifier(proof_key),
            ),
        )
        transport = httpx.ASGITransport(app=shell)
        source = ShellWorkSource(base_url="http://shell", transport=transport)
        store = SprintctlRecordStore(
            base_url="http://shell", timeout=5, transport=transport,
            auth=EdgeProofAuth(proof_key),
        )
        toolset = build_toolset(ToolsetContext(env={}, work_source=source, runs=store))
        edge = create_edge_app(
            identity_resolver=resolver(keys[0], allowed_repo_ids=frozenset({repo})),
            work_source=source, toolsets=(toolset,),
        )

        def headers():
            return identity_headers(assertion(
                keys[1], authorities=["work:task.create"], repo_ids=[repo],
            ))

        async def exercise():
            args = {**ARGS, "sprint_id": sprint, "require_review": True}
            try:
                async with httpx.AsyncClient(
                    base_url="http://edge", transport=httpx.ASGITransport(app=edge),
                ) as client:
                    rpc_result = await client.post(
                        MCP_PATH, json=call("create_task", args), headers=headers(),
                    )
                    result = rpc_result.json()["result"]["structuredContent"]
                    assert isinstance(result.get("item_id"), int), rpc_result.text
                async with httpx.AsyncClient(
                    base_url="http://shell", transport=transport,
                    headers={"X-Vuoro-Client-Protocol": "1"},
                ) as api:
                    envelope = {
                        "schema_version": "invocation/v1", "request_id": REQUEST_ID,
                        "operation": OPERATION, "repo_id": repo,
                        "idempotency_key": args["idempotency_key"],
                        "arguments": dict(args, description=None, priority=None),
                    }
                    replay = await api.post("/api/invoke/v1", json=envelope, headers=headers())
                    assert replay.status_code == 200, replay.text
                    assert replay.json()["result"]["item"]["id"] == result["item_id"]
                    assert replay.json()["result"]["review_release_digest"] == result["review_release_digest"]
                    release = pg.current_release(owner, result["item_id"])
                    assert release["release_digest"] == result["review_release_digest"]
                    assert release["acceptance_contract"]["review_protocol"] == "browser-outcome-v1"
                    envelope["arguments"]["title"] = "A different task"
                    conflict = await api.post("/api/invoke/v1", json=envelope, headers=headers())
                    assert conflict.status_code == 409, conflict.text
                    assert conflict.json()["error"]["code"] == "idempotency-conflict"
                assert [row["id"] for row in pg.list_work_items(owner, sprint_id=sprint)] == [
                    result["item_id"]
                ]
            finally:
                await store.aclose()
                await source.aclose()

        asyncio.run(exercise())
    finally:
        owner.conn.close()
