"""E4: exact evidence, owner parking, observed reclaim, no selecting machinery."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from test_claim_tools import (
    RUN_ID, OTHER_RUN_ID, LEASE_ID, REPO_ID, RESOLVE, _accepted, _claimed,
    _context, _forwarded, _resolved, _specs, _store, REPORT_ARGS, CLAIM_ARGS,
)
from vuoro_mcp_edge.claim_tools import build_toolset
from vuoro_mcp_edge.parking import OPERATION, current_projection, validate_context
from vuoro_mcp_edge.toolsets import ToolFailure

ENTRY = "work.evidence.entry-v1"
TAIL = "work.evidence.tail-v1"
APPEND = "work.evidence.append-v1"
REPORT = "work.lease.report-outcome-v1"
REF = f"vuoro-evidence:{RUN_ID}/seq/0"


def facts(scope="family", reset="2026-10-20T01:00:00Z"):
    park = {"event_id": 12, "reason_ref": f"vuoro-evidence:{OTHER_RUN_ID}/seq/0",
            "parked_at": "2026-10-20T00:00:00Z", "report_id": "report-old", "lease_id": "lease-old",
            "run_id": OTHER_RUN_ID, "model_id": "claude-opus-5-5",
            "rate_limit": {"observed_at": "2026-10-20T00:00:00Z", "status": "rejected",
                           "limit_scope": scope, "resets_at": reset, "reset_source": "harness-structured",
                           "http_status": 429, "overage": None}}
    return {"repo_id": REPO_ID, "item_id": 7, "current": None,
            "previous": {**park, "release_event_id": 13, "released_at": "2026-10-20T01:01:00Z"},
            "lease_run": {"run_id": RUN_ID, "model_id": "claude-sonnet-5-5"},
            "evaluated_at": "2026-10-20T02:00:00Z"}


class Owner:
    def __init__(self):
        self.calls = []
        self.entry = {"chain_seq": 0, "kind": "rate_limit_event",
                      "provenance": {"observation": {"status": "rejected"}}}
        self.facts = facts()
        self.records = {}
        self.fail_append = False
        self.tail_override = None
        self.advertised = {ENTRY, OPERATION}

    def __call__(self, request):
        if request.method == "GET":
            assert request.url.path == "/api/catalog/v1"
            return httpx.Response(200, json={"operations": [{"name": name} for name in self.advertised]})
        body = json.loads(request.content)
        self.calls.append(body)
        op, args = body["operation"], body["arguments"]
        if op == RESOLVE:
            return _resolved()
        if op == ENTRY:
            return _accepted(op, {"repo_id": REPO_ID, "run_id": RUN_ID, "item": self.entry})
        if op == REPORT:
            return _accepted(op, {"report": {"parked": {"event_id": 12, "reason_ref": args["reason_ref"]}},
                                  "settlement_effect": "parked", "settled": False})
        if op == "work.lease.acquire-v1":
            return _claimed()
        if op == OPERATION:
            return _accepted(op, self.facts)
        if op == TAIL:
            return _accepted(op, {"item": self.tail_override or next(iter(self.records.values()), None)})
        if op == APPEND:
            if self.fail_append:
                from test_claim_tools import _rejected
                return _rejected(op, "test-append-refused")
            # Keep the owner's stable-key replay oracle separate from chain location.
            key = args["idempotency_key"]
            if key in self.records:
                old = self.records[key]
                assert {k: v for k, v in args.items() if k not in ("chain_seq", "chain_prev_digest")} == {
                    k: v for k, v in old.items() if k not in ("chain_seq", "chain_prev_digest")}
            else:
                self.records[key] = args
            return _accepted(op, {"item": self.records[key]})
        raise AssertionError(f"unexpected owner operation {op}")


def specs(owner):
    tools = build_toolset(_context(_store(owner)))
    return {tool.name: tool for tool in tools.tools}


def run(tool, args, forwarded=None):
    return asyncio.run(tool.run(tool.parse(args), forwarded or _forwarded()))


def recording_identity():
    forwarded = _forwarded()
    # Identity is frozen; construct the same identity with additive test scopes.
    from dataclasses import replace
    forwarded.identity = replace(forwarded.identity, authorities=frozenset({"work:claim", "work:read", "work:evidence"}))
    return forwarded


@pytest.mark.parametrize("scope", ["plan", "family", "unknown"])
def test_denial_ref_resolves_exact_older_entry_and_forwards_park_unchanged(scope):
    owner = Owner()
    owner.entry["provenance"]["observation"]["limit_scope"] = scope
    args = {**REPORT_ARGS, "outcome": "failed", "disposition": "parked", "reason_ref": REF}
    result = run(specs(owner)["report_outcome"], args)
    assert result["settlement_effect"] == "parked"
    assert [row["operation"] for row in owner.calls] == [RESOLVE, ENTRY, REPORT]
    assert owner.calls[-1]["arguments"] == args
    assert owner.calls[1]["arguments"] == {"run_id": RUN_ID, "chain_seq": 0}


@pytest.mark.parametrize("entry", [None, {"kind": "other"}, {"kind": "rate_limit_event", "provenance": {"observation": {"status": "allowed_warning"}}}])
def test_unresolved_non_limit_or_warning_never_calls_report_owner(entry):
    owner = Owner()
    owner.entry = None if entry is None else {"chain_seq": 0, **entry}
    with pytest.raises(ToolFailure) as caught:
        run(specs(owner)["report_outcome"], {**REPORT_ARGS, "outcome": "failed", "disposition": "parked", "reason_ref": REF})
    assert caught.value.code == "reason-ref-unresolved"
    assert REPORT not in [row["operation"] for row in owner.calls]


@pytest.mark.parametrize("ref,code", [(REF.replace(RUN_ID, OTHER_RUN_ID), "reason-ref-foreign-run"),
                                     (REF + "garbage", "reason-ref-unresolved"),
                                     (REF[:-1] + "-1", "reason-ref-unresolved")])
def test_foreign_and_malformed_refs_are_refused_before_any_evidence_read(ref, code):
    owner = Owner()
    with pytest.raises(ToolFailure) as caught:
        run(specs(owner)["report_outcome"], {**REPORT_ARGS, "outcome": "failed", "disposition": "parked", "reason_ref": ref})
    assert caught.value.code == code
    assert [row["operation"] for row in owner.calls] == [RESOLVE]


def test_old_owner_cannot_silently_substitute_tail_for_exact_entry():
    owner = Owner()
    owner.advertised.remove(ENTRY)
    with pytest.raises(ToolFailure) as caught:
        run(specs(owner)["report_outcome"], {**REPORT_ARGS, "outcome": "failed", "disposition": "parked", "reason_ref": REF})
    assert caught.value.code == "record-owner-incompatible"
    assert [row["operation"] for row in owner.calls] == [RESOLVE]


def test_reclaim_records_both_declared_families_once_even_after_another_append():
    owner = Owner()
    tool = specs(owner)["claim_work"]
    first = run(tool, CLAIM_ARGS, recording_identity())
    assert first["lease"]["lease_id"] == LEASE_ID
    obs = first["reclaim_observation"]["provenance"]["observation"]
    assert obs["predecessor_model_family"] == "claude-opus"
    assert obs["model_family"] == "claude-sonnet"
    assert obs["same_family"] is False
    owner.tail_override = {"item_id": "later-entry", "digest": "sha256:" + "b" * 64,
                           "chain_seq": 1, "chain_prev_digest": "sha256:" + "a" * 64}
    second = run(tool, CLAIM_ARGS, recording_identity())
    assert second["reclaim_observation"] == first["reclaim_observation"]
    assert len(owner.records) == 1
    assert all(row["arguments"].get("run_id") != OTHER_RUN_ID for row in owner.calls)


def test_unclassified_models_are_recorded_without_inventing_family_equality():
    owner = Owner()
    owner.facts["previous"]["model_id"] = "new-native-model-a"
    owner.facts["lease_run"]["model_id"] = "new-native-model-b"
    result = run(specs(owner)["claim_work"], CLAIM_ARGS, recording_identity())
    observation = result["reclaim_observation"]["provenance"]["observation"]
    assert observation["model_family"] == observation["predecessor_model_family"] == "unclassified"
    assert observation["same_family"] is None


@pytest.mark.parametrize("change", ["ordinary", "unresolved", "unsupported", "append-failed", "no-scopes", "foreign-result"])
def test_observation_never_undoes_a_successful_claim(change):
    owner = Owner()
    forwarded = recording_identity()
    if change == "ordinary": owner.facts["previous"] = None
    if change == "unresolved": owner.facts["previous"]["rate_limit"] = None
    if change == "unsupported": owner.advertised.remove(OPERATION)
    if change == "append-failed": owner.fail_append = True
    if change == "no-scopes": forwarded = _forwarded()
    if change == "foreign-result": owner.facts["repo_id"] = "other"
    result = run(specs(owner)["claim_work"], CLAIM_ARGS, forwarded)
    assert result["lease"]["lease_id"] == LEASE_ID
    assert not owner.records
    if change not in ("ordinary", "unresolved"):
        assert result["warnings"]


@pytest.mark.parametrize("reset,now,expected", [
    ("2026-10-20T03:00:00Z", "2026-10-20T02:00:00Z", False),
    ("2026-10-20T02:00:00Z", "2026-10-20T02:00:00Z", True),
    (None, "2026-10-20T23:59:59Z", False), (None, "2026-10-21T00:00:00Z", True),
])
def test_refillable_uses_owner_clock_and_recorded_reset_or_24_hour_age(reset, now, expected):
    value = facts(reset=reset)
    value["current"] = {k: v for k, v in value["previous"].items() if k not in ("release_event_id", "released_at")}
    value["evaluated_at"] = now
    checked = validate_context(value, repo_id=REPO_ID, item_id=7)
    assert current_projection(checked)["refillable"] is expected


def test_family_table_has_only_recording_consumers_and_shipped_text_has_no_selection_promises():
    root = Path(__file__).parents[1] / "src/vuoro_mcp_edge"
    users = [p.name for p in root.glob("*.py") if "from .model_family import" in p.read_text()]
    assert users == ["rate_limit_records.py"]
    from vuoro_mcp_edge.claim_tools import _CLAIM_WORK_DEFINITION, _REPORT_OUTCOME_DEFINITION
    from vuoro_mcp_edge.record_tools import _APPEND_EVIDENCE_DEFINITION
    for definition in (_CLAIM_WORK_DEFINITION, _REPORT_OUTCOME_DEFINITION, _APPEND_EVIDENCE_DEFINITION):
        text = json.dumps(definition).lower()
        assert not any(word in text for word in ("failover", "routing", "predict", "recommended_family"))


@pytest.mark.parametrize("fault", [None, "foreign-repo", "private-field"])
def test_describe_work_joins_only_advertised_owner_read_and_forwards_callers_identity(fault):
    from edge_support import accepted, item_record, item_result
    from vuoro_mcp_edge.errors import WorkSourceUnavailable
    from vuoro_mcp_edge.work_source import ShellWorkSource
    value = facts()
    value["current"] = {k: v for k, v in value["previous"].items() if k not in ("release_event_id", "released_at")}
    value["previous"] = None
    value["lease_run"] = None
    if fault == "foreign-repo": value["repo_id"] = "another"
    if fault == "private-field": value["current"]["private_transcript"] = "PRIVATE-CANARY"
    calls = []
    def shell(request):
        if request.method == "GET":
            return httpx.Response(200, json={"revision": "rev-1", "operations": [
                {"name": name, "execution_semantics": "read"}
                for name in ("work.public.list-v1", "work.public.item-v1", OPERATION)]})
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["X-Vuoro-Identity"] == forwarded.assertion
        if body["operation"] == "work.public.item-v1":
            return accepted(item_result(item_record(7)), body["operation"])
        assert body["operation"] == OPERATION
        assert body["arguments"] == {"item_id": 7}
        assert body["catalog_revision"] == "rev-1"
        return accepted(value, OPERATION)
    forwarded = _forwarded()
    async def scenario():
        source = ShellWorkSource(transport=httpx.MockTransport(shell))
        try:
            return await source.describe_work(forwarded, 7)
        finally:
            await source.aclose()
    if fault is not None:
        with pytest.raises(WorkSourceUnavailable) as caught:
            asyncio.run(scenario())
        assert caught.value.code == "parking-owner-invalid"
    else:
        result = asyncio.run(scenario())
        assert result["parked"]["event_id"] == 12
        assert result["parked"]["refillable"] is True
    assert [row["operation"] for row in calls] == ["work.public.item-v1", OPERATION]
