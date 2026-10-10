"""Owner-read projections: exact binding, redaction and current authority."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import httpx
from fastapi.testclient import TestClient

from edge_support import assertion, call, identity_headers, key_pair, resolver, rpc
from vuoro_mcp_edge.composition import build_toolsets
from vuoro_mcp_edge.errors import WorkSourceUnavailable
from vuoro_mcp_edge.read_tools import DECISIONS, EFFECT, ITEM, LEASES, NEXT, RELEASE, build_toolset
from vuoro_mcp_edge.runs import UnavailableRunRegistry
from vuoro_mcp_edge.server import create_edge_app
from vuoro_mcp_edge.toolsets import ToolsetContext
from vuoro_mcp_edge.work_source import ForwardedIdentity

REPO = "repo-a"
DIGEST = "a" * 64
RELEASE_DIGEST = "b" * 64
REVISION = "item:" + "a" * 36 + "@description:v1@sha256:" + "c" * 64
SECRET = "PRIVATE-SENTINEL-NEVER-EMIT"


def owner_records() -> dict[str, dict[str, Any]]:
    return {
        EFFECT: {"repo_id": REPO, "intent": {
            "intent_id": "effect_1", "revision": 1, "item_id": 7,
            "canonical_intent_digest": DIGEST, "release_digest": RELEASE_DIGEST,
            "state": "accepted", "rationale": SECRET, "unified_diff": SECRET,
            "acceptance": {"verification": {"evidence_digest": "sha256:" + "d" * 64,
                "receipt": {"checks": [{"name": SECRET, "revision": "sha256:" + "e" * 64,
                                         "status": "passed"}]}}},
        }},
        ITEM: {"repo_id": REPO, "item": {"id": 7, "sprint_id": 2,
            "edit_revision": REVISION, "title": "Safe title", "status": "active",
            "description": SECRET}, "refs": [{"ref_type": "doc", "url": "https://private/" + SECRET,
                                            "label": SECRET}]},
        RELEASE: {"repo_id": REPO, "release": {"work_item_id": 7,
            "release_digest": RELEASE_DIGEST, "item_revision": REVISION + "@revise:0",
            "acceptance_contract": {"review_required": True,
                                    "evidence_obligations": ["unit-test", "security-review"]},
            "context_refs": [{"ref_type": "doc", "url": "https://private/" + SECRET,
                              "label": SECRET}]}, "commits": []},
        DECISIONS: {"repo_id": REPO, "item_id": 7, "status": "active",
            "terminal_decision_id": None, "decisions": [{"id": 11, "kind": "revise",
                "release_digest": RELEASE_DIGEST, "rationale": SECRET,
                "evidence_digests": ["f" * 64]}]},
        LEASES: {"repo_id": REPO, "item_id": 7,
            "verification": {"private_detail": SECRET}, "leases": [], "outcome_reports": []},
        NEXT: {"sprint": {"id": 2}, "checkpointed_unacked": [{
            "item_id": 7, "checkpoint_note_id": 19,
            "created_at": "2026-10-10T07:00:00Z", "release_digest": RELEASE_DIGEST,
            "sha": "0" * 40, "detail": SECRET, "worktree_host": SECRET,
        }]},
    }


class OwnerSource:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = owner_records() if records is None else records
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self._client = httpx.AsyncClient(base_url="http://shell", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"operations": [
                {"name": name} for name in self.records]})))

    async def _invoke(self, op: str, args: dict[str, Any], forwarded: ForwardedIdentity) -> Any:
        self.calls.append((op, args, forwarded.repo_id))
        return deepcopy(self.records[op])

    async def aclose(self) -> None:
        await self._client.aclose()


def specs(source: OwnerSource):
    context = ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry())
    return {tool.name: tool for tool in build_toolset(context).tools}


def caller(repo: str = REPO) -> ForwardedIdentity:
    return ForwardedIdentity(assertion="test", request_id="request", repo_id=repo)


PREVIEW_ARGS = {"intent_id": "effect_1", "revision": 1, "canonical_intent_digest": DIGEST}
DELTA_ARGS = {"work_link": {"kind": "work", "id": 7}}


def run(source: OwnerSource, name: str, args: dict[str, Any]):
    spec = specs(source)[name]
    return asyncio.run(spec.run(spec.parse(args), caller()))


def test_preview_keeps_obligations_separate_and_redacts_private_owner_fields() -> None:
    source = OwnerSource()
    report = run(source, "preview_acceptance", PREVIEW_ARGS)
    assert report["basis_status"] == "matching"
    assert report["authorizes_acceptance"] is False
    assert report["snapshot_is_permit"] is False
    assert report["consistency"] == "non-atomic-owner-read-sequence"
    assert [row["evidence_mapping"] for row in report["obligations"]] == ["unknown", "unknown"]
    assert report["owner_referenced_evidence_digests"] == ["f" * 64, "sha256:" + "d" * 64]
    assert SECRET not in str(report)
    assert set(op for op, _, _ in source.calls) == {EFFECT, ITEM, RELEASE, DECISIONS, LEASES}


def test_preview_identity_changes_with_artifact_work_or_check_revision() -> None:
    base = run(OwnerSource(), "preview_acceptance", PREVIEW_ARGS)
    for field in ("artifact", "work", "check"):
        records = owner_records()
        if field == "artifact":
            records[EFFECT]["intent"]["canonical_intent_digest"] = "0" * 64
        elif field == "work":
            records[ITEM]["item"]["edit_revision"] = REVISION.replace("v1", "v2")
        else:
            records[EFFECT]["intent"]["acceptance"]["verification"]["receipt"]["checks"][0]["revision"] = "sha256:" + "0" * 64
        changed = run(OwnerSource(records), "preview_acceptance", PREVIEW_ARGS)
        assert changed["snapshot_id"] != base["snapshot_id"]
        if field == "artifact":
            assert changed["basis_status"] == "historical"
        if field == "work":
            assert changed["release_item_revision"] == "stale"
            assert changed["basis_status"] == "historical"


def test_mid_read_owner_change_is_unstable_not_a_current_verdict() -> None:
    class MovingSource(OwnerSource):
        async def _invoke(self, op, args, forwarded):
            result = await super()._invoke(op, args, forwarded)
            if op == EFFECT and sum(call[0] == EFFECT for call in self.calls) == 2:
                result["intent"]["canonical_intent_digest"] = "0" * 64
            return result

    report = run(MovingSource(), "preview_acceptance", PREVIEW_ARGS)
    assert report["basis_status"] == "unstable"
    assert not report["authorizes_acceptance"]


def test_delta_work_link_is_identifier_and_baseline_is_only_comparison_data() -> None:
    source = OwnerSource()
    first = run(source, "read_work_delta", DELTA_ARGS)
    assert first["baseline_provenance"] == "absent"
    assert set(first["changed"].values()) == {"unknown"}
    assert first["unacknowledged_checkpoint"]["state"] == "owner-derived-unacknowledged"
    assert first["source_revisions"].startswith("unknown")
    assert not first["authorizes_action"] and SECRET not in str(first)
    baseline = first["current"]
    records = owner_records()
    records[DECISIONS]["decisions"].append({"id": 12, "kind": "accept", "evidence_digests": []})
    changed = run(OwnerSource(records), "read_work_delta", {**DELTA_ARGS, "baseline": baseline})
    assert changed["changed"]["latest_decision_id"] == "changed"
    assert changed["latest_decision"]["id"] == 12
    assert changed["changed"]["item_revision"] == "unchanged"


def test_wrong_repository_owner_response_is_refused_without_content() -> None:
    source = OwnerSource()
    source.records[ITEM]["repo_id"] = "foreign"
    try:
        run(source, "read_work_delta", DELTA_ARGS)
    except Exception as error:
        assert getattr(error, "code", None) == "owner-response-invalid"
        assert SECRET not in str(error)
    else:
        raise AssertionError("foreign owner response was accepted")


def test_missing_release_is_explicit_and_does_not_hide_current_item_refs() -> None:
    class MissingRelease(OwnerSource):
        async def _invoke(self, op, args, forwarded):
            if op == RELEASE:
                raise WorkSourceUnavailable("release-not-found", "private owner text", upstream=True)
            return await super()._invoke(op, args, forwarded)

    report = run(MissingRelease(), "preview_acceptance", PREVIEW_ARGS)
    assert report["basis_status"] == "unknown"
    assert report["release_binding"] == "missing"
    delta = run(MissingRelease(), "read_work_delta", DELTA_ARGS)
    assert delta["release_item_revision"] == "missing"
    assert len(delta["current"]["reference_ids"]) == 1
    assert SECRET not in str(delta)


def test_owner_authority_refusal_is_generic_and_never_emits_owner_message() -> None:
    class RefusingSource(OwnerSource):
        async def _invoke(self, op, args, forwarded):
            raise WorkSourceUnavailable("authority-required", SECRET, upstream=True)

    try:
        run(RefusingSource(), "preview_acceptance", PREVIEW_ARGS)
    except Exception as error:
        assert getattr(error, "code", None) == "authority-required"
        assert SECRET not in str(error)
    else:
        raise AssertionError("owner authority refusal was hidden")


def test_preview_requires_both_authorities_before_listing_or_invocation(tmp_path) -> None:
    key_path, private = key_pair(tmp_path)
    source = OwnerSource()
    context = ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry())
    app = create_edge_app(identity_resolver=resolver(key_path), work_source=source,
                          toolsets=(build_toolset(context),))
    client = TestClient(app)
    for authorities, expected in [(["work:read"], ["list_ready_work", "describe_work", "read_work_delta"]),
                                  (["work.effect.get"], []),
                                  (["work:read", "work.effect.get"], ["list_ready_work", "describe_work", "preview_acceptance", "read_work_delta"])]:
        token = assertion(private, authorities=authorities)
        listed = client.post("/mcp", headers=identity_headers(token),
                             json=rpc("tools/list")).json()["result"]["tools"]
        assert [tool["name"] for tool in listed] == expected
    for authorities in (["work:read"], ["work.effect.get"]):
        token = assertion(private, authorities=authorities)
        refused = client.post("/mcp", headers=identity_headers(token),
                              json=call("preview_acceptance", PREVIEW_ARGS)).json()["result"]
        assert refused["isError"] and refused["structuredContent"]["error"]["code"] == "authority-required"
    foreign = assertion(private, authorities=["work:read", "work.effect.get"], repo_ids=["foreign"])
    denied = client.post("/mcp", headers=identity_headers(foreign),
                         json=call("preview_acceptance", PREVIEW_ARGS))
    assert denied.status_code == 401
    assert source.calls == []


def test_two_authorized_client_bindings_can_read_same_work_link_without_transcript(tmp_path) -> None:
    key_path, private = key_pair(tmp_path)
    source = OwnerSource()
    context = ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry())
    app = create_edge_app(identity_resolver=resolver(key_path), work_source=source,
                          toolsets=(build_toolset(context),))
    client = TestClient(app)
    reports = []
    for client_id, subject in (("claude-connector", "01KAAAAAAAAAAAAAAAAAAAAAA"),
                               ("chatgpt-connector", "01KBBBBBBBBBBBBBBBBBBBBBB")):
        token = assertion(private, authorities=["work:read"], client_id=client_id,
                          grant_id="grant-" + client_id, subject=subject)
        response = client.post("/mcp", headers=identity_headers(token),
                               json=call("read_work_delta", DELTA_ARGS))
        assert response.status_code == 200
        body = response.json()["result"]
        assert body["isError"] is False
        reports.append(body["structuredContent"])
    assert reports[0]["work_link"] == reports[1]["work_link"] == DELTA_ARGS["work_link"]
    assert reports[0]["snapshot_id"] == reports[1]["snapshot_id"]
    assert all(SECRET not in str(report) and not report["authorizes_action"] for report in reports)
    assert all(repo == REPO for _, _, repo in source.calls)


def test_default_composition_does_not_list_read_tools_without_owner_operations() -> None:
    source = OwnerSource({})
    context = ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry())
    tools = [tool for toolset in build_toolsets(context) for tool in toolset.tools]
    assert all(asyncio.run(tool.describe()) is None for tool in tools if tool.describe)
