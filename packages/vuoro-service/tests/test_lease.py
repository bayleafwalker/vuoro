from __future__ import annotations

import pytest

from vuoro_service.lease import (
    LeaseConflictError,
    LeaseHolderMismatchError,
    LeaseNotCurrentError,
    LeaseStore,
    RetainedOutcome,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_claim_then_heartbeat_extends_and_complete_releases() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(9)
    renewed = store.heartbeat(lease.lease_id, "worker-a")
    assert renewed.last_heartbeat_at == clock.now
    clock.advance(9)
    # Would have expired since the *original* claim, but the heartbeat reset
    # the clock, so this must still succeed.
    store.heartbeat(lease.lease_id, "worker-a")
    store.complete(lease.lease_id, "worker-a")
    # Completed: the subject is free again immediately, no TTL wait needed.
    reclaimed = store.claim("run-1", "worker-b", ttl_seconds=10)
    assert reclaimed.holder == "worker-b"


def test_claim_conflicts_while_lease_is_live() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(5)  # still within TTL
    with pytest.raises(LeaseConflictError):
        store.claim("run-1", "worker-b", ttl_seconds=10)


def test_lease_with_no_heartbeat_past_ttl_is_reclaimable_by_a_different_holder() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    assert store.is_expired("run-1") is False
    clock.advance(10.0001)  # past ttl_seconds with no heartbeat
    assert store.is_expired("run-1") is True
    new_lease = store.reclaim("run-1", "worker-b", ttl_seconds=10)
    assert new_lease.holder == "worker-b"
    assert new_lease.lease_id != lease.lease_id


def test_reclaim_refused_while_lease_is_still_live() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(5)
    with pytest.raises(LeaseConflictError):
        store.reclaim("run-1", "worker-b", ttl_seconds=10)


def test_replayed_completion_from_a_stale_holder_after_reclaim_fails() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    stale = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)  # worker-a goes dark past TTL
    store.reclaim("run-1", "worker-b", ttl_seconds=10)
    # worker-a's late completion for its old lease id must not succeed --
    # not because the holder name is wrong (it is worker-a's own lease id),
    # but because that lease id is no longer current.
    with pytest.raises(LeaseNotCurrentError):
        store.complete(stale.lease_id, "worker-a")
    # And it must not have disturbed worker-b's live lease.
    assert store.is_expired("run-1") is False


def test_replayed_heartbeat_from_a_stale_holder_after_reclaim_fails() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    stale = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    store.reclaim("run-1", "worker-b", ttl_seconds=10)
    with pytest.raises(LeaseNotCurrentError):
        store.heartbeat(stale.lease_id, "worker-a")


def test_heartbeat_from_the_wrong_holder_is_rejected() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    with pytest.raises(LeaseHolderMismatchError):
        store.heartbeat(lease.lease_id, "worker-imposter")


def test_complete_from_the_wrong_holder_is_rejected() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    with pytest.raises(LeaseHolderMismatchError):
        store.complete(lease.lease_id, "worker-imposter")


def test_heartbeat_on_unknown_lease_id_is_rejected() -> None:
    store = LeaseStore(clock=FakeClock())
    with pytest.raises(LeaseNotCurrentError):
        store.heartbeat("does-not-exist", "worker-a")


def test_is_expired_on_unknown_subject_is_none() -> None:
    store = LeaseStore(clock=FakeClock())
    assert store.is_expired("no-such-subject") is None


def test_heartbeat_exactly_at_ttl_boundary_still_succeeds() -> None:
    # now > last_heartbeat_at + ttl_seconds is the expiry rule, so exactly
    # at the boundary the lease is not yet expired.
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(10)
    store.heartbeat(lease.lease_id, "worker-a")


def test_heartbeat_one_tick_past_ttl_boundary_fails() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(10.0001)
    with pytest.raises(LeaseNotCurrentError):
        store.heartbeat(lease.lease_id, "worker-a")


# INV-L1 (agentops#2540): a lease grants authority, not ownership of the
# result.  A late completion is refused and settles nothing, but its result
# is retained as evidence instead of being discarded.


def test_a_superseded_holders_late_result_is_retained_but_settles_nothing() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    stale = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    live = store.reclaim("run-1", "worker-b", ttl_seconds=10)
    with pytest.raises(LeaseNotCurrentError):
        store.complete(stale.lease_id, "worker-a", result={"diff": "a's work"})
    assert store.retained_outcomes("run-1") == (
        RetainedOutcome(
            lease_id=stale.lease_id, subject="run-1", holder="worker-a",
            result={"diff": "a's work"}, reason="superseded", retained_at=clock.now,
        ),
    )
    (kept,) = store.retained_outcomes("run-1")
    assert (kept.disposition, kept.settlement_effect) == ("stale", "none")
    # worker-b's lease is untouched and still settles its own work.
    assert store.is_expired("run-1") is False
    store.complete(live.lease_id, "worker-b", result={"diff": "b's work"})
    assert store.is_expired("run-1") is None
    assert len(store.retained_outcomes("run-1")) == 1


def test_an_expired_holders_result_is_retained_and_the_lease_stays() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    with pytest.raises(LeaseNotCurrentError):
        store.complete(lease.lease_id, "worker-a", result="late")
    (kept,) = store.retained_outcomes("run-1")
    assert (kept.lease_id, kept.reason, kept.result) == (lease.lease_id, "expired", "late")
    # Nothing was settled: the subject still has its (expired) lease.
    assert store.is_expired("run-1") is True


def test_a_strangers_or_unknown_completion_retains_nothing() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    stale = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    store.reclaim("run-1", "worker-b", ttl_seconds=10)
    with pytest.raises(LeaseNotCurrentError):
        store.complete(stale.lease_id, "worker-imposter", result="x")
    with pytest.raises(LeaseNotCurrentError):
        store.complete("does-not-exist", "worker-a", result="x")
    assert store.retained_outcomes("run-1") == ()


def test_a_current_completion_retains_nothing() -> None:
    store = LeaseStore(clock=FakeClock())
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    store.complete(lease.lease_id, "worker-a", result="done")
    assert store.retained_outcomes("run-1") == ()


def test_a_retry_after_the_holders_own_completion_retains_nothing() -> None:
    store = LeaseStore(clock=FakeClock())
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    store.complete(lease.lease_id, "worker-a", result="done")
    with pytest.raises(LeaseNotCurrentError):
        store.complete(lease.lease_id, "worker-a", result="done")
    assert store.retained_outcomes("run-1") == ()


def test_refusals_look_the_same_whether_or_not_anything_was_retained() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    stale = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    store.reclaim("run-1", "worker-b", ttl_seconds=10)
    messages = []
    for holder in ("worker-a", "worker-imposter"):
        with pytest.raises(LeaseNotCurrentError) as refused:
            store.complete(stale.lease_id, holder, result="x")
        messages.append(str(refused.value))
    assert messages[0] == messages[1]
    assert len(store.retained_outcomes("run-1")) == 1


def test_the_retained_result_is_a_copy() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    result = {"diff": "a"}
    with pytest.raises(LeaseNotCurrentError):
        store.complete(lease.lease_id, "worker-a", result=result)
    result["diff"] = "changed"
    assert store.retained_outcomes("run-1")[0].result == {"diff": "a"}


def test_a_stale_heartbeat_retains_nothing() -> None:
    clock = FakeClock()
    store = LeaseStore(clock=clock)
    lease = store.claim("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    with pytest.raises(LeaseNotCurrentError):
        store.heartbeat(lease.lease_id, "worker-a")
    assert store.retained_outcomes("run-1") == ()
