"""Read-only Native receipt of gateway-signed invocation correlation."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from edge_support import REQUEST_ID, assertion, identity_headers
from vuoro_mcp_edge import record_tools
from vuoro_mcp_edge.record_tools import SprintctlRecordStore
from vuoro_mcp_edge.server import MCP_PATH, create_edge_app
from vuoro_mcp_edge.toolsets import ToolsetContext


class _CapturingRuns:
    supports_continuation = False

    def __init__(self) -> None:
        self.registered: list[dict[str, Any]] = []

    async def register(
        self,
        binding,
        *,
        idempotency_key,
        forwarded,
        manifest,
        predecessor_run_id,
    ) -> str:
        self.registered.append(
            {
                "binding": binding,
                "idempotency_key": idempotency_key,
                "forwarded_repo_id": forwarded.repo_id,
                "invocation_id": forwarded.identity.invocation_id,
                "attempt_id": forwarded.identity.attempt_id,
                "manifest": manifest,
                "predecessor_run_id": predecessor_run_id,
            }
        )
        return "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _call(arguments: dict[str, Any], **params: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "register_run", "arguments": arguments, **params},
    }


def _manifest() -> dict[str, Any]:
    return {
        "harness_id": "codex",
        "harness_build": "test-build",
        "model_id": "model-test",
        "recipe_id": "recipe-test",
        "observed_profile": {
            "instruction_digest": "sha256:instruction",
            "skill_digests": [{"skill_id": "review", "digest": "sha256:skill"}],
        },
        "idempotency_key": "register-key-0001",
    }


def _app(keys, allowed_repos: frozenset[str], runs: _CapturingRuns):
    toolset = record_tools.build_toolset(
        ToolsetContext(env={}, work_source=object(), runs=runs)
    )
    assert toolset is not None
    from edge_support import resolver

    return create_edge_app(
        identity_resolver=resolver(keys[0], allowed_repo_ids=allowed_repos),
        work_source=object(),
        toolsets=(toolset,),
    )


@pytest.mark.anyio
async def test_register_uses_outer_repo_without_changing_owner_arguments_or_claim_authority(
    keys,
):
    runs = _CapturingRuns()
    app = _app(keys, frozenset({"repo-a", "repo-b"}), runs)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://edge"
    ) as client:
        response = await client.post(
            MCP_PATH,
            headers=identity_headers(
                assertion(
                    keys[1],
                    authorities=["work:evidence"],
                    repo_ids=["repo-a", "repo-b"],
                    invocation_id="inv_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    attempt_id="att_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                )
            ),
            json=_call(_manifest(), repo_id="repo-b"),
        )
    result = response.json()["result"]
    assert result["isError"] is False, result
    assert result["structuredContent"]["run_id"] == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert len(runs.registered) == 1
    registered = runs.registered[0]
    assert registered["binding"].repo_id == "repo-b"
    assert registered["forwarded_repo_id"] == "repo-b"
    assert registered["idempotency_key"] == "register-key-0001"
    assert registered["manifest"] == {
        key: value for key, value in _manifest().items() if key != "idempotency_key"
    }
    assert "repo_id" not in registered["manifest"]
    assert registered["invocation_id"] == "inv_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert registered["attempt_id"] == "att_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert (
        registered["binding"].principal_id
        == "vuoro-cloud-control:01K44444444444444444444444:0"
    )


@pytest.mark.anyio
async def test_multirepo_omission_and_client_correlation_ids_are_refused_before_register(
    keys,
):
    runs = _CapturingRuns()
    app = _app(keys, frozenset({"repo-a", "repo-b"}), runs)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://edge"
    ) as client:
        missing_response = await client.post(
            MCP_PATH,
            headers=identity_headers(
                assertion(
                    keys[1],
                    authorities=["work:evidence"],
                    repo_ids=["repo-a", "repo-b"],
                )
            ),
            json=_call(_manifest()),
        )
        forged_response = await client.post(
            MCP_PATH,
            headers=identity_headers(
                assertion(
                    keys[1],
                    authorities=["work:evidence"],
                    repo_ids=["repo-a", "repo-b"],
                )
            ),
            json=_call(
                _manifest(),
                repo_id="repo-a",
                invocation_id="inv_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ),
        )
        missing = missing_response.json()["result"]
        forged = forged_response.json()["result"]
    assert missing["isError"] is True
    assert missing["structuredContent"]["error"]["code"] == "repo-id-required"
    assert forged["isError"] is True
    assert forged["structuredContent"]["error"]["code"] == "invalid-params"
    assert runs.registered == []


@pytest.mark.anyio
async def test_notifications_invalid_ids_and_header_mismatches_never_register(keys):
    runs = _CapturingRuns()
    app = _app(keys, frozenset({"repo-a"}), runs)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://edge"
    ) as client:
        notification = _call(_manifest())
        notification.pop("id")
        notification_response = await client.post(
            MCP_PATH,
            headers=identity_headers(
                assertion(keys[1], authorities=["work:evidence"], repo_ids=["repo-a"])
            ),
            json=notification,
        )
        invalid_id_responses = []
        for invalid_id in (None, True, False, 1.0, {"nested": 1}, [1]):
            invalid_id_responses.append(
                await client.post(
                    MCP_PATH,
                    headers=identity_headers(
                        assertion(
                            keys[1], authorities=["work:evidence"], repo_ids=["repo-a"]
                        )
                    ),
                    json={**_call(_manifest()), "id": invalid_id},
                )
            )
        method_mismatch = await client.post(
            MCP_PATH,
            headers={
                **identity_headers(
                    assertion(
                        keys[1], authorities=["work:evidence"], repo_ids=["repo-a"]
                    )
                ),
                "Mcp-Method": "tools/list",
            },
            json=_call(_manifest()),
        )
        name_mismatch = await client.post(
            MCP_PATH,
            headers={
                **identity_headers(
                    assertion(
                        keys[1], authorities=["work:evidence"], repo_ids=["repo-a"]
                    )
                ),
                "Mcp-Name": "list_ready_work",
            },
            json=_call(_manifest()),
        )
        protocol_mismatch = await client.post(
            MCP_PATH,
            headers={
                **identity_headers(
                    assertion(
                        keys[1], authorities=["work:evidence"], repo_ids=["repo-a"]
                    )
                ),
                "MCP-Protocol-Version": "2024-11-05",
            },
            json={
                **_call(_manifest()),
                "params": {
                    "name": "register_run",
                    "arguments": _manifest(),
                    "protocolVersion": "2026-07-28",
                },
            },
        )
        unsupported_protocol = await client.post(
            MCP_PATH,
            headers={
                **identity_headers(
                    assertion(keys[1], authorities=["work:evidence"], repo_ids=["repo-a"])
                ),
                "MCP-Protocol-Version": "unsupported-version",
            },
            json=_call(_manifest()),
        )
        null_repo = await client.post(
            MCP_PATH,
            headers=identity_headers(
                assertion(keys[1], authorities=["work:evidence"], repo_ids=["repo-a"])
            ),
            json=_call(_manifest(), repo_id=None),
        )
    assert notification_response.status_code == 202
    assert [response.status_code for response in invalid_id_responses] == [200] * 6
    assert method_mismatch.status_code == 400
    assert name_mismatch.status_code == 400
    assert protocol_mismatch.status_code == 400
    assert unsupported_protocol.status_code == 400
    assert null_repo.status_code == 200
    assert null_repo.json()["result"]["isError"] is True
    assert runs.registered == []


@pytest.mark.anyio
@pytest.mark.parametrize("owner_status", ["bound", "legacy-unbound"])
async def test_signed_pair_uses_owner_capability_and_validates_typed_receipt(
    keys, owner_status
):
    invocation_id = "inv_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    attempt_id = "att_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    token = assertion(
        keys[1],
        authorities=["work:evidence"],
        repo_ids=["repo-a"],
        client_id="client-a",
        grant_id="grant-a",
        invocation_id=invocation_id,
        attempt_id=attempt_id,
    )
    owner_requests: list[dict[str, Any]] = []

    async def owner(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(
                200,
                json={"operations": [{"name": "work.run.register-correlated-v1"}]},
            )
        owner_requests.append(
            {
                "envelope": json.loads(request.content),
                "assertion": request.headers["x-vuoro-identity"],
            }
        )
        run_id = "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
        observed_profile = _manifest()["observed_profile"]
        run = {
            "run_id": run_id,
            "principal_id": "vuoro-cloud-control:01K44444444444444444444444:0",
            "workspace_id": "01K11111111111111111111111",
            "client_id": "client-a",
            "grant_id": "grant-a",
            "harness_id": "codex",
            "harness_build": "test-build",
            "model_id": "model-test",
            "recipe_id": "recipe-test",
            "observed_profile": observed_profile,
            "grant_ids": [],
            "claim_ids": [],
            "created_at": "2026-10-11T12:34:56.123456Z",
        }
        receipt = {
            "schema": "sprintctl-run-invocation-receipt/v1",
            "repo_id": "repo-a",
            "invocation_id": invocation_id,
            "attempt_id": attempt_id,
            "owner_record_kind": "run",
            "owner_record_id": run_id,
            "owner_record_digest": "a" * 64,
            "owner_binding": {
                "repo_id": "repo-a",
                "workspace_id": "01K11111111111111111111111",
                "principal_id": "vuoro-cloud-control:01K44444444444444444444444:0",
                "principal_epoch": 0,
                "client_id": "client-a",
                "grant_id": "grant-a",
            },
        }
        return httpx.Response(
            200,
            json={
                "schema_version": "invocation-result/v1",
                "request_id": REQUEST_ID,
                "operation": "work.run.register-correlated-v1",
                "catalog_revision": "rev-1",
                "status": "accepted",
                "result": {
                    "repo_id": "repo-a",
                    "run": run,
                    "correlation_status": owner_status,
                    "receipt": receipt if owner_status == "bound" else None,
                },
                "error": None,
            },
        )

    store = SprintctlRecordStore(
        base_url="http://owner",
        timeout=5,
        transport=httpx.MockTransport(owner),
    )
    app = _app(keys, frozenset({"repo-a"}), store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://edge"
    ) as client:
        response = await client.post(
            MCP_PATH,
            headers=identity_headers(token),
            json=_call(_manifest(), repo_id="repo-a"),
        )
    tool_result = response.json()["result"]
    assert tool_result["isError"] is False, tool_result
    content = tool_result["structuredContent"]
    assert content["run_id"] == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert content["correlation_status"] == owner_status
    if owner_status == "bound":
        assert content["receipt"]["invocation_id"] == invocation_id
    else:
        assert content["receipt"] is None
        # This is an explicit owner-level unbound result. The edge preserves
        # it for the caller so the gateway can retain UNKNOWN, not acceptance.
    assert len(owner_requests) == 1
    forwarded = owner_requests[0]
    assert forwarded["assertion"] == token
    envelope = forwarded["envelope"]
    assert envelope["operation"] == "work.run.register-correlated-v1"
    assert envelope["repo_id"] == "repo-a"
    assert envelope["arguments"] == {
        key: value for key, value in _manifest().items() if key != "repo_id"
    }
    assert "invocation_id" not in envelope["arguments"]
    assert "attempt_id" not in envelope["arguments"]
    assert "principal_epoch" not in envelope["arguments"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "catalog_status,catalog_payload",
    [(200, {"operations": []}), (503, {"error": "unavailable"})],
)
async def test_missing_or_failed_owner_catalog_keeps_legacy_run_response_shape(
    keys, catalog_status, catalog_payload
):
    token = assertion(
        keys[1],
        authorities=["work:evidence"],
        repo_ids=["repo-a"],
        client_id="client-a",
        grant_id="grant-a",
        invocation_id="inv_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        attempt_id="att_01ARZ3NDEKTSV4RRFFQ69G5FAV",
    )
    observed: list[dict[str, Any]] = []

    def owner(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(catalog_status, json=catalog_payload)
        envelope = json.loads(request.content)
        observed.append(envelope)
        run = {
            "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "harness_id": "codex",
            "harness_build": "test-build",
            "model_id": "model-test",
            "recipe_id": "recipe-test",
            "observed_profile": _manifest()["observed_profile"],
            "grant_ids": [],
            "claim_ids": [],
        }
        return httpx.Response(
            200,
            json={
                "schema_version": "invocation-result/v1",
                "request_id": REQUEST_ID,
                "operation": "work.run.register-v1",
                "catalog_revision": "rev-1",
                "status": "accepted",
                "result": {"repo_id": "repo-a", "run": run},
                "error": None,
            },
        )

    store = SprintctlRecordStore(
        base_url="http://owner", timeout=5, transport=httpx.MockTransport(owner)
    )
    app = _app(keys, frozenset({"repo-a"}), store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://edge"
    ) as client:
        response = await client.post(
            MCP_PATH,
            headers=identity_headers(token),
            json=_call(_manifest(), repo_id="repo-a"),
        )
    tool_result = response.json()["result"]
    assert tool_result["isError"] is False, tool_result
    assert tool_result["structuredContent"] == {
        "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    }
    assert observed[0]["operation"] == "work.run.register-v1"
    assert "invocation_id" not in observed[0]["arguments"]
