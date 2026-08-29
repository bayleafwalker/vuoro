"""Two ingress lanes into the same reducer — a HostProto object and an outctl
capture manifest.

Rewritten 2026-08-29 from the recovered bytecode's intent (see ../RECOVERY.md).
The original replayed a real 2026-08-08 outctl spool; that spool is lost, so the
manifests here are synthesized to the same shape.  The test the original named
`test_real_capture_...` is therefore named without `real` — it exercises the
same path, but it is no longer evidence about real traffic.

`test_session_log_replays_a_recorded_session` is new: `session_log` was
recovered from bytecode and had no other surviving coverage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vuoro_evidence import (ClaimType, DecisionKind, EvidenceSet, RerunQuestion, decide_rerun,
                            reduce)
from vuoro_evidence.core.reducer import EffectState
from vuoro_evidence.ingress import ingest, registered_profiles
from vuoro_evidence.ingress.hostproto import session_log

T0 = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
SUBJECT = "action-1"
GRANT = "grant-1"


def _receipt(**over):
    payload = {"schema_version": "hostproto.receipt/v1", "receipt_id": "r-1", "action_id": SUBJECT,
               "provider": "test-host", "surface": "surface-1", "revision_after": 7,
               "attempted": True, "accepted": True, "outcome": "completed", "verified": True,
               "executed": True, "state_after": "sha256:after", "effects": [], "evidence": []}
    payload.update(over)
    return payload


def _observation(**over):
    payload = {"schema_version": "hostproto.observation/v1", "provider": "test-host",
               "surface": "surface-1", "revision": 9, "data": {"state": "done"},
               "cursor": {"position": 9}}
    payload.update(over)
    return payload


def _manifest(**cmd):
    command = {"started": True, "exit_code": 0, "timed_out": False, "cancelled": False}
    command.update(cmd)
    return {"capture_id": "c-1", "command": command, "capture_status": "complete",
            "streams": {"stdout": {"sha256": "sha256:out", "bytes": 12}}}


def _decide(*items, now=T0, inputs=None):
    ledger = reduce(EvidenceSet("set-1", tuple(items)), now, inputs)
    return ledger, decide_rerun(ledger, RerunQuestion("d-1", SUBJECT))


def test_two_profiles_registered() -> None:
    assert registered_profiles() == ("command-capture", "hostproto")


def test_hostproto_receipt_is_a_completed_claim_with_freshness() -> None:
    item = ingest("hostproto", _receipt(), grant_id=GRANT, collected_at=T0)
    (claim,) = item.claims
    assert claim.claim_type is ClaimType.EFFECT_COMPLETED
    assert claim.subject == SUBJECT
    assert (claim.freshness.scope, claim.freshness.position) == ("surface-1", 7)


def test_hostproto_error_is_stale_and_not_invoked() -> None:
    payload = {"schema_version": "hostproto.error/v1", "provider": "test-host",
               "code": "target_invalidated", "host_invoked": False, "data": {}}
    item = ingest("hostproto", payload, subject=SUBJECT, grant_id=GRANT, collected_at=T0)
    kinds = [c.claim_type for c in item.claims]
    assert kinds == [ClaimType.TARGET_STALE, ClaimType.EFFECT_NOT_INVOKED]

    ledger, decision = _decide(item)
    assert ledger.subjects[SUBJECT].effect is EffectState.REFUSED
    assert decision.kind is DecisionKind.REACQUIRE


def test_unknown_outcome_then_observation_reconciles() -> None:
    uncertain = ingest("hostproto", _receipt(outcome="unknown", verified=False),
                       grant_id=GRANT, collected_at=T0)
    _, before = _decide(uncertain)
    assert before.kind is DecisionKind.RECONCILE
    assert before.requires_observation is True

    expected = uncertain.claims[0].detail["state_after"]
    observation = ingest("hostproto", _observation(), subject=SUBJECT, grant_id=GRANT,
                         collected_at=T0, confirm=lambda data: data["state"] == "done")
    ledger, after = _decide(uncertain, observation)
    assert ledger.subjects[SUBJECT].reconciled is True
    assert after.kind is DecisionKind.ACCEPT
    assert expected  # the receipt carried a state to reconcile against


def test_capture_prevents_blind_rerun_within_window_and_expires_outside() -> None:
    item = ingest("command-capture", _manifest(), subject=SUBJECT, grant_id=GRANT,
                  collected_at=T0, valid_until=T0 + timedelta(minutes=10))
    _, inside = _decide(item, now=T0 + timedelta(minutes=1))
    assert inside.kind is DecisionKind.ACCEPT, "a valid capture must prevent a blind rerun"

    ledger, outside = _decide(item, now=T0 + timedelta(hours=1))
    assert ledger.expired[item.item_id].reason == "past_valid_until"
    assert outside.kind is DecisionKind.REACQUIRE


def test_timed_out_capture_is_uncertain() -> None:
    item = ingest("command-capture", _manifest(exit_code=None, timed_out=True),
                  subject=SUBJECT, grant_id=GRANT, collected_at=T0)
    ledger, decision = _decide(item)
    assert ledger.subjects[SUBJECT].effect is EffectState.UNCERTAIN
    assert decision.kind is DecisionKind.RECONCILE


def test_session_log_replays_a_recorded_session() -> None:
    lines = [
        {"seq": 1, "tool": "host.act", "structured": _receipt()},
        {"seq": 2, "tool": "host.act", "args": {"action_id": SUBJECT},
         "structured": {"schema_version": "hostproto.error/v1", "provider": "test-host",
                        "code": "capability_blocked", "host_invoked": False, "data": {}}},
        {"seq": 3, "tool": "host.look", "structured": _observation()},
        {"seq": 4, "tool": "host.chat", "structured": {"not": "hostproto"}},
        {"seq": 5, "tool": "host.chat"},
    ]
    items = list(session_log(lines, grant_for_action={SUBJECT: GRANT},
                             observation_subject=lambda rec: (SUBJECT, lambda data: data["state"] == "done"),
                             collected_at=T0))
    # lines 4 and 5 carry no HostProto object and are skipped, not decoded
    assert [i.item_id for i in items] == ["session:1", "session:2", "session:3"]
    assert all(c.grant_id == GRANT for i in items for c in i.claims)
    assert items[2].claims[0].confirms is True
