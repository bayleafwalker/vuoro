"""Step 7.2: the core path represents each required situation generically.

Every item here is hand-built: no ingress edge is imported, so a passing run
proves the reducer and decision path express these situations without knowing
which host produced them.

Rewritten 2026-08-29 from the recovered bytecode's intent (see ../RECOVERY.md);
this is not the original source.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vuoro_evidence import (Claim, ClaimType, DecisionKind, EffectGrant, EvidenceItem, EvidenceSet,
                            GrantUse, RerunQuestion, ValidityBasis, ValidityWindow, decide_rerun,
                            reduce)
from vuoro_evidence.core.model import Freshness
from vuoro_evidence.core.reducer import EffectState

T0 = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
SUBJECT = "action-1"
GRANT = "grant-1"


def _grant() -> EffectGrant:
    return EffectGrant(GRANT, "principal-1", "write", "scope-1")


def _item(item_id: str, *claims: Claim, validity: ValidityWindow | None = None) -> EvidenceItem:
    return EvidenceItem(item_id, "test", f"ref:{item_id}", f"sha256:{item_id}", "test-collector",
                        validity or ValidityWindow(ValidityBasis.INDEFINITE, T0), claims)


def _decide(*items: EvidenceItem, now: datetime = T0, inputs: dict[str, str] | None = None):
    ledger = reduce(EvidenceSet("set-1", items, (_grant(),)), now, inputs)
    return ledger, decide_rerun(ledger, RerunQuestion("decision-1", SUBJECT))


def test_stale_target_is_refusal_and_grant_unused() -> None:
    ledger, decision = _decide(_item("i1", Claim(ClaimType.TARGET_STALE, SUBJECT, GRANT)))
    assert ledger.subjects[SUBJECT].effect is EffectState.REFUSED
    # nothing consequential happened, so the grant must not read as spent
    assert ledger.grant_use[GRANT] is GrantUse.UNUSED
    assert decision.kind is DecisionKind.REACQUIRE


def test_precondition_refusal_and_capability_unavailable() -> None:
    for claim_type in (ClaimType.PRECONDITION_REFUSED, ClaimType.CAPABILITY_UNAVAILABLE):
        ledger, decision = _decide(_item("i1", Claim(claim_type, SUBJECT, GRANT)))
        assert ledger.subjects[SUBJECT].effect is EffectState.REFUSED, claim_type
        assert ledger.grant_use[GRANT] is GrantUse.UNUSED, claim_type
        assert decision.kind is DecisionKind.REACQUIRE, claim_type


def test_persisted_evidence_is_recorded() -> None:
    ledger, _ = _decide(_item("i1", Claim(ClaimType.EFFECT_COMPLETED, SUBJECT, GRANT),
                              Claim(ClaimType.EVIDENCE_PERSISTED, SUBJECT, GRANT,
                                    detail={"ref": "blob:1"})))
    assert ledger.subjects[SUBJECT].persisted == ["i1"]


def test_completed_and_failed_consume_the_grant() -> None:
    for claim_type, state in ((ClaimType.EFFECT_COMPLETED, EffectState.COMPLETED),
                              (ClaimType.EFFECT_FAILED, EffectState.FAILED)):
        ledger, decision = _decide(_item("i1", Claim(claim_type, SUBJECT, GRANT)))
        assert ledger.subjects[SUBJECT].effect is state
        # a failed effect is still an effect: the grant was spent either way
        assert ledger.grant_use[GRANT] is GrantUse.USED, claim_type
        assert decision.kind is (DecisionKind.ACCEPT if state is EffectState.COMPLETED
                                 else DecisionKind.REACQUIRE)


def test_uncertain_outcome_marks_uncertain_use_and_demands_observation() -> None:
    ledger, decision = _decide(_item("i1", Claim(ClaimType.EFFECT_UNCERTAIN, SUBJECT, GRANT)))
    assert ledger.subjects[SUBJECT].effect is EffectState.UNCERTAIN
    assert ledger.grant_use[GRANT] is GrantUse.UNCERTAIN_USE
    assert decision.kind is DecisionKind.RECONCILE
    assert decision.requires_observation is True


def test_observation_confirms_or_contradicts() -> None:
    uncertain = _item("i1", Claim(ClaimType.EFFECT_UNCERTAIN, SUBJECT, GRANT))
    confirming = _item("i2", Claim(ClaimType.OBSERVATION, SUBJECT, GRANT,
                                   Freshness("surface-1", 7), confirms=True))
    ledger, decision = _decide(uncertain, confirming)
    assert ledger.subjects[SUBJECT].reconciled is True
    assert ledger.subjects[SUBJECT].effect is EffectState.COMPLETED
    assert decision.kind is DecisionKind.ACCEPT

    completed = _item("i1", Claim(ClaimType.EFFECT_COMPLETED, SUBJECT, GRANT))
    contradicting = _item("i2", Claim(ClaimType.OBSERVATION, SUBJECT, GRANT,
                                      Freshness("surface-1", 7), confirms=False))
    ledger, decision = _decide(completed, contradicting)
    assert ledger.subjects[SUBJECT].contradicted_by == ["i2"]
    assert decision.kind is DecisionKind.REJECT


def test_bounded_window_expires_and_names_reason() -> None:
    bounded = ValidityWindow(ValidityBasis.BOUNDED, T0, valid_until=T0 + timedelta(minutes=5))
    item = _item("i1", Claim(ClaimType.EFFECT_COMPLETED, SUBJECT, GRANT), validity=bounded)

    _, inside = _decide(item, now=T0 + timedelta(minutes=1))
    assert inside.kind is DecisionKind.ACCEPT

    ledger, outside = _decide(item, now=T0 + timedelta(minutes=30))
    assert ledger.expired["i1"].reason == "past_valid_until"
    assert outside.kind is DecisionKind.REACQUIRE
    assert "past_valid_until" in outside.rationale


def test_input_change_expiry_is_attributable_to_the_component() -> None:
    window = ValidityWindow(ValidityBasis.UNTIL_INPUTS_CHANGE, T0,
                            component_digests={"config": "sha256:a", "policy": "sha256:b"})
    item = _item("i1", Claim(ClaimType.EFFECT_COMPLETED, SUBJECT, GRANT), validity=window)

    _, unchanged = _decide(item, inputs={"config": "sha256:a", "policy": "sha256:b"})
    assert unchanged.kind is DecisionKind.ACCEPT

    ledger, changed = _decide(item, inputs={"config": "sha256:a", "policy": "sha256:CHANGED"})
    # the reason names which component moved, not merely that something did
    assert ledger.expired["i1"].reason == "input_changed:policy"
    assert "input_changed:policy" in changed.rationale
    assert changed.kind is DecisionKind.REACQUIRE


def test_no_evidence_means_reacquire() -> None:
    _, decision = _decide()
    assert decision.kind is DecisionKind.REACQUIRE
    assert decision.evidence_ids == ()
    assert decision.rationale == "no evidence for subject"
