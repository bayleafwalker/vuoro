"""Read-only Native receipt of gateway-signed invocation correlation."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from edge_support import assertion, identity_headers
from vuoro_mcp_edge import record_tools
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
