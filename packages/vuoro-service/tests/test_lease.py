from __future__ import annotations

import pytest

from vuoro_service.lease import (
    LeaseConflictError,
    LeaseHolderMismatchError,
    LeaseNotCurrentError,
    LeaseStore,
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
