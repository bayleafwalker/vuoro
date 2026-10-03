"""Seven provider-neutral scenarios, classified by LTD §7.1.

The configured PostgreSQL binding is required, never optional/skipped.
"""
import pytest
from .lease_contract import Refused


@pytest.mark.essential_workflow
def test_claim_heartbeat_and_settle_once(provider):
    subject = provider.new_subject()
    claim = provider.claim(subject, "A")
    provider.heartbeat(claim, "A")
    assert provider.report_outcome(claim, "A", {"result": "ok"}).settled
    assert not provider.report_outcome(claim, "A", {"result": "again"}).settled


@pytest.mark.essential_safety
def test_live_lease_refuses_another_holder(provider):
    subject = provider.new_subject()
    first = provider.claim(subject, "A")
    with pytest.raises(Refused, match="LEASE_HELD"):
        provider.claim(subject, "B")
    assert provider.current_claim_id(subject) == first.claim_id


@pytest.mark.essential_safety
def test_takeover_supersedes_old_heartbeat(provider):
    subject = provider.new_subject()
    old = provider.claim(subject, "A")
    provider.make_stale(old)
    new = provider.claim(subject, "B")
    assert new.claim_id != old.claim_id
    with pytest.raises(Refused, match="CLAIM_SUPERSEDED"):
        provider.heartbeat(old, "A")
    assert provider.current_claim_id(subject) == new.claim_id


def assert_late_report_is_nonsettling_and_retained(provider):
    subject = provider.new_subject()
    old = provider.claim(subject, "A")
    provider.make_stale(old)
    new = provider.claim(subject, "B")
    result = provider.report_outcome(old, "A", {"late": "result"})
    assert not result.settled, "superseded report must never settle"
    assert result.code == "CLAIM_SUPERSEDED"
    assert provider.current_claim_id(subject) == new.claim_id
    assert provider.retained_outcomes(subject) == [dict(claim_id=old.claim_id,
        outcome={"late": "result"}, disposition="stale", settlement_effect="none")]


@pytest.mark.essential_safety
def test_superseded_report_retained_without_settlement(provider):
    assert_late_report_is_nonsettling_and_retained(provider)


@pytest.mark.essential_workflow
def test_same_holder_stale_claim_reactivates_identity(provider):
    subject = provider.new_subject()
    old = provider.claim(subject, "A")
    provider.make_stale(old)
    assert provider.is_stale(old)
    resumed = provider.claim(subject, "A")
    assert resumed.claim_id == old.claim_id
    assert not provider.is_stale(old)  # observed before heartbeat: no cached replay
    provider.heartbeat(old, "A")
    assert provider.report_outcome(old, "A", {"resumed": True}).settled


@pytest.mark.essential_safety
def test_same_holder_cannot_resume_superseded_handle(provider):
    subject = provider.new_subject()
    old = provider.claim(subject, "A")
    provider.make_stale(old)
    provider.claim(subject, "B")
    result = provider.report_outcome(old, "A", {"resume": True})
    assert (result.settled, result.code) == (False, "CLAIM_SUPERSEDED")


@pytest.mark.essential_safety
def test_staleness_has_no_background_effect(provider):
    subject = provider.new_subject()
    old = provider.claim(subject, "A")
    provider.make_stale(old)
    assert provider.current_claim_id(subject) == old.claim_id
    assert provider.retained_outcomes(subject) == []
