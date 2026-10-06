from copy import deepcopy
from datetime import datetime, timezone

import pytest

from vuoro_evidence.ingress.provider import normalize, evidence_item_draft, UNKNOWN, OBSERVED_FIELDS

AT = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)


def github(event="check_run"):
    value = {"id": 42, "head_sha": "a" * 40, "conclusion": "success", "name": "checks"}
    if event == "pull_request":
        value = {"id": 42, "head": {"sha": "a" * 40}, "merged": True}
    return {"action": "closed" if event == "pull_request" else "completed",
            "repository": {"full_name": "example/project"}, event: value}


def outcome():
    return {"type": "span.outcome_evaluation_end", "id": "event_demo", "outcome_id": "outc_demo",
            "result": "satisfied", "explanation": "Criteria met", "iteration": 0}


@pytest.mark.parametrize("event", ["check_run", "workflow_run", "pull_request"])
def test_github_commit_is_a_reference_not_exact_artifact_or_check_revision(event):
    payload = github(event); before = deepcopy(payload)
    value = normalize("github", event, payload, delivery_id="delivery1")
    assert value["commit_reference"] == "a" * 40
    assert all(value[key] == UNKNOWN for key in OBSERVED_FIELDS)
    assert value["verdict"] == (UNKNOWN if event == "pull_request" else "success")
    assert value["assurance"] == "unverified-supplied-payload"
    assert payload == before


def test_satisfied_outcome_is_only_an_observation_without_run_or_grant_identity():
    value = normalize("anthropic-managed-agents", "span.outcome_evaluation_end", outcome(), delivery_id="e1")
    draft = evidence_item_draft(value, ref="git:immutable/payload.json", collected_at=AT)
    assert value["session_reference"] == UNKNOWN
    assert value["rubric_or_check_revision"] == UNKNOWN
    claim = draft["claims"][0]
    assert claim["claim_type"] == "observation" and claim["confirms"] is None and claim["grant_id"] is None
    assert not ({"run_id", "chain_seq", "chain_prev_digest", "decision", "acceptance"} & draft.keys())


def test_same_delivery_retry_is_identical_but_conflicting_replay_keeps_key_and_changes_digest():
    payload = github()
    a = evidence_item_draft(normalize("github", "check_run", payload, delivery_id="d1"), ref="git:payload", collected_at=AT)
    b = evidence_item_draft(normalize("github", "check_run", deepcopy(payload), delivery_id="d1"), ref="git:payload", collected_at=AT)
    assert a == b
    payload["check_run"]["conclusion"] = "failure"
    c = evidence_item_draft(normalize("github", "check_run", payload, delivery_id="d1"), ref="git:payload", collected_at=AT)
    assert c["idempotency_key"] == a["idempotency_key"] and c["digest"] != a["digest"]


def test_mismatched_artifact_is_retained_as_collector_observation_not_confirmation():
    value = normalize("github", "check_run", github(), delivery_id="d1", observed={"artifact_digest": "sha256:" + "b" * 64})
    draft = evidence_item_draft(value, ref="git:payload", collected_at=AT)
    assert draft["claims"][0]["detail"]["artifact_digest"] == "sha256:" + "b" * 64
    assert draft["claims"][0]["confirms"] is None
    assert draft["validity"]["component_digests"] == {}  # historical evidence does not expire into erasure
    assert value["collector_observations"] == ["artifact_digest"]


@pytest.mark.parametrize("observed", [{"assurance": "verified"}, {"artifact_digest": "a" * 40},
    {"observed_instruction_digest": "sha256:no"}, {"provider_build": ""}])
def test_caller_cannot_lift_assurance_or_supply_untyped_digests(observed):
    with pytest.raises(ValueError):
        normalize("github", "check_run", github(), delivery_id="d1", observed=observed)


def test_payload_extensions_do_not_assert_acceptance_or_exact_artifact():
    payload = github(); payload.update(acceptance=True, artifact_digest="sha256:" + "a" * 64, assurance="verified")
    value = normalize("github", "check_run", payload, delivery_id="d1")
    assert value["artifact_digest"] == UNKNOWN and value["assurance"] == "unverified-supplied-payload"


@pytest.mark.parametrize("provider,event,payload", [("other", "check_run", github()),
    ("github", "push", {}), ("github", "check_run", {"action": "completed"}),
    ("anthropic-managed-agents", "span.outcome_evaluation_end", {"type": "other"})])
def test_malformed_or_unsupported_events_are_refused(provider, event, payload):
    with pytest.raises(ValueError):
        normalize(provider, event, payload, delivery_id="d1")


def test_nonfinite_payload_and_naive_collection_time_are_refused():
    payload = github(); payload["extra"] = float("nan")
    with pytest.raises(ValueError):
        normalize("github", "check_run", payload, delivery_id="d1")
    value = normalize("github", "check_run", github(), delivery_id="d1")
    with pytest.raises(ValueError):
        evidence_item_draft(value, ref="git:payload", collected_at=datetime(2026, 10, 6))


def test_provider_success_with_wrong_artifact_cannot_satisfy_existing_rerun_acceptance():
    from vuoro_evidence import (Claim, ClaimType, DecisionKind, EvidenceItem, EvidenceSet,
        RerunQuestion, ValidityBasis, ValidityWindow, decide_rerun, reduce)
    value = normalize("github", "check_run", github(), delivery_id="d1",
                      observed={"artifact_digest": "sha256:" + "b" * 64})
    draft = evidence_item_draft(value, ref="git:payload", collected_at=AT)
    subject = draft["idempotency_key"]
    item = EvidenceItem(draft["item_id"], draft["kind"], draft["ref"], draft["digest"], draft["collector"],
                        ValidityWindow(ValidityBasis.INDEFINITE, AT),
                        (Claim(ClaimType.OBSERVATION, subject, detail=value),))
    evidence = EvidenceSet("set_demo", (item,))
    ledger = reduce(evidence, AT, {"artifact": "sha256:" + "a" * 64})
    assert len(evidence.items) == 1  # retained, not dropped to make a gate pass
    assert decide_rerun(ledger, RerunQuestion("decision_demo", subject)).kind is DecisionKind.REACQUIRE
    assert not ledger.grant_use


def test_draft_refuses_a_supplied_record_that_promotes_its_own_assurance():
    value = normalize("github", "check_run", github(), delivery_id="d1")
    value["assurance"] = "verified"
    with pytest.raises(ValueError):
        evidence_item_draft(value, ref="git:payload", collected_at=AT)
