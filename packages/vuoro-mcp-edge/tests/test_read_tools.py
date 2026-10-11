"""The edge forwards bounded owner reads without rebuilding their policy."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import httpx
import pytest

from vuoro_mcp_edge.errors import WorkSourceUnavailable
from vuoro_mcp_edge.read_tools import (
    ACCEPTANCE_SNAPSHOT, CONTINUATION_DELTA, build_toolset,
)
from vuoro_mcp_edge.runs import UnavailableRunRegistry
from vuoro_mcp_edge.toolsets import ToolFailure, ToolsetContext
from vuoro_mcp_edge.work_source import ForwardedIdentity

REPO = "repo-a"
RUN_ID = "run_" + "0" * 26
INTENT_DIGEST = "a" * 64
ARTIFACT_DIGEST = "sha256:" + "b" * 64


class OwnerSource:
    def __init__(self, operations: dict[str, dict[str, Any]] | None = None) -> None:
        self.operations = operations or {
            ACCEPTANCE_SNAPSHOT: {"repo_id": REPO, "snapshot": {
                "schema": "sprintctl-effect-acceptance-snapshot/v1",
                "snapshot_digest": "sha256:" + "c" * 64,
                "authorizes_acceptance": False,
                "receipt": {"comparison": "unknown"},
                "lease_settlement": "unknown",
            }},
            CONTINUATION_DELTA: {"repo_id": REPO, "delta": {
                "schema": "sprintctl-work-continuation-delta/v1",
                "snapshot_digest": "sha256:" + "d" * 64,
                "authorizes_action": False,
                "source_bytes": "unknown",
                "external_missing_content": "unknown",
            }},
        }
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self._client = httpx.AsyncClient(base_url="http://shell", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"operations": [
                {"name": name} for name in self.operations]})))
        self.error: WorkSourceUnavailable | None = None

    async def _invoke(self, op: str, args: dict[str, Any], forwarded: ForwardedIdentity) -> Any:
        self.calls.append((op, deepcopy(args), forwarded.repo_id))
        if self.error:
            raise self.error
        return deepcopy(self.operations[op])


def _tool(source: OwnerSource, name: str):
    context = ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry())
    return next(tool for tool in build_toolset(context).tools if tool.name == name)


def _caller(repo: str = REPO) -> ForwardedIdentity:
    return ForwardedIdentity(assertion="test", request_id="request", repo_id=repo)


def test_preview_forwards_exact_basis_once_and_returns_owner_snapshot_unchanged():
    source = OwnerSource()
    args = {"intent_id": "effect_1", "revision": 3,
            "canonical_intent_digest": INTENT_DIGEST, "artifact_sha256": ARTIFACT_DIGEST,
            "verification_ref": {"run_id": RUN_ID, "item_id": "evidence-1"}}
    tool = _tool(source, "preview_acceptance")
    result = asyncio.run(tool.run(tool.parse(args), _caller()))
    assert result["authorizes_acceptance"] is False
    assert source.calls == [(ACCEPTANCE_SNAPSHOT, args, REPO)]


def test_legacy_preview_request_without_exact_artifact_digest_fails_closed():
    source = OwnerSource()
    tool = _tool(source, "preview_acceptance")
    with pytest.raises(ToolFailure) as error:
        tool.parse({"intent_id": "effect_1", "revision": 3,
                    "canonical_intent_digest": INTENT_DIGEST})
    assert error.value.code == "invalid-arguments"
    assert source.calls == []


def test_delta_requires_explicit_run_and_item_and_maps_one_owner_call():
    source = OwnerSource()
    args = {"run_id": RUN_ID, "item_id": 7, "limit": 12,
            "after_note_id": 4, "after_chain_seq": 9}
    tool = _tool(source, "read_work_delta")
    result = asyncio.run(tool.run(tool.parse(args), _caller()))
    assert result["authorizes_action"] is False
    assert source.calls == [(CONTINUATION_DELTA, args, REPO)]
    with pytest.raises(ToolFailure, match="explicit work item"):
        tool.parse({"run_id": RUN_ID})


def test_legacy_delta_request_without_explicit_item_fails_closed():
    source = OwnerSource()
    tool = _tool(source, "read_work_delta")
    with pytest.raises(ToolFailure) as error:
        tool.parse({"run_id": RUN_ID, "work_id": 7})
    assert error.value.code == "invalid-arguments"
    assert source.calls == []


def test_delta_preserves_owner_reference_truncation_markers():
    delta = {"schema": "sprintctl-work-continuation-delta/v1",
        "reference_page": [{"url": "https://example.test/" + "x" * 2028,
            "url_truncated": True, "url_is_complete": False,
            "url_sha256": "sha256:" + "e" * 64,
            "label": "label excerpt", "label_truncated": True,
            "label_sha256": "sha256:" + "f" * 64}]}
    source = OwnerSource({CONTINUATION_DELTA: {"repo_id": REPO, "delta": delta}})
    tool = _tool(source, "read_work_delta")
    result = asyncio.run(tool.run(tool.parse({"run_id": RUN_ID, "item_id": 7}), _caller()))
    assert result["reference_page"] == delta["reference_page"]


def test_bad_checkpoint_or_cursor_is_refused_before_owner_call():
    source = OwnerSource()
    tool = _tool(source, "read_work_delta")
    with pytest.raises(ToolFailure, match="checkpoint"):
        tool.parse({"run_id": RUN_ID, "item_id": 7, "checkpoint": {"item_id": 7}})
    with pytest.raises(ToolFailure, match="cursor"):
        tool.parse({"run_id": RUN_ID, "item_id": 7,
                    "cursor": {"last_reference_id": 1}})
    assert source.calls == []


def test_delta_checkpoint_round_trips_unknown_collection_digests():
    tool = _tool(OwnerSource(), "read_work_delta")
    checkpoint = {
        "repo_id": REPO, "item_id": 7, "work_edit_revision": "edit:4",
        "status_revision": "status:2", "current_release_digest": None,
        "current_decision_digest": None, "current_reference_set_digest": None,
    }
    parsed = tool.parse({"run_id": RUN_ID, "item_id": 7, "checkpoint": checkpoint})
    assert parsed["checkpoint"] == checkpoint
    assert parsed["limit"] == 32


def test_catalog_exposes_only_atomic_owner_operations():
    source = OwnerSource({CONTINUATION_DELTA: source_delta()})
    preview = _tool(source, "preview_acceptance")
    delta = _tool(source, "read_work_delta")
    assert asyncio.run(preview.describe()) is None
    assert asyncio.run(delta.describe()) is not None


def source_delta():
    return {"repo_id": REPO, "delta": {"schema": "sprintctl-work-continuation-delta/v1"}}


def test_wrong_repository_or_malformed_owner_response_is_rejected():
    for response in (
        {"repo_id": "foreign", "snapshot": {"schema": "sprintctl-effect-acceptance-snapshot/v1"}},
        {"repo_id": REPO, "snapshot": {"schema": "wrong/v1"}},
    ):
        source = OwnerSource({ACCEPTANCE_SNAPSHOT: response})
        tool = _tool(source, "preview_acceptance")
        with pytest.raises(ToolFailure, match="inconsistent read"):
            asyncio.run(tool.run(tool.parse({"intent_id": "effect_1", "revision": 1,
                "canonical_intent_digest": INTENT_DIGEST, "artifact_sha256": ARTIFACT_DIGEST}), _caller()))


def test_owner_refusals_are_generic_and_stale_cursor_is_preserved():
    source = OwnerSource()
    tool = _tool(source, "read_work_delta")
    args = {"run_id": RUN_ID, "item_id": 7}
    source.error = WorkSourceUnavailable("stale-cursor", "private owner detail", upstream=True)
    with pytest.raises(ToolFailure) as error:
        asyncio.run(tool.run(tool.parse(args), _caller()))
    assert error.value.code == "stale-cursor"
    assert "private owner detail" not in str(error.value)
