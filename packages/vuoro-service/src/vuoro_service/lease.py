"""Lease expiry with heartbeat (ADR-02; agentops#2464, E0 acceptance (b)).

Expiry is a contract property of a lease, not a behaviour queue implementers
opt into: a lease that no one heartbeats past its TTL releases itself, and
once a subject has been reclaimed by a new holder, the superseded lease id is
permanently dead -- a heartbeat or completion replayed against it must fail,
even if it arrives holding the correct former-holder identity, because
`lease_id` identity (not `holder` identity alone) is what "current" means.

A lease grants authority, not ownership of the result (INV-L1, the
operator's lease contract in agentops docs/plans/2026-09-27-backlog-ideation.md
R4, agentops#2540): a completion submitted under an expired or superseded
lease never settles, but its result is retained as evidence
(`disposition="stale"`, `settlement_effect="none"`) rather than discarded.
Only the lease's own holder's result is retained; an unknown lease id or
another holder's completion leaves nothing.

This is new surface with no existing consumer (see
docs/plans/2026-09-20-e0-hardening-status-and-design.md): the store is
in-memory, keyed by subject, with an injectable clock so tests do not sleep.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import time
import uuid


class LeaseError(ValueError):
    """Base class for lease-contract violations."""


class LeaseConflictError(LeaseError):
    """The subject is already held by a live (non-expired) lease."""


class LeaseHolderMismatchError(LeaseError):
    """The caller is not the current holder of the named lease."""


class LeaseNotCurrentError(LeaseError):
    """The lease id no longer names the current lease for its subject.

    Raised both when the lease id is unknown and when it has been
    superseded by a reclaim -- the two are indistinguishable from a stale
    caller's point of view, and both must be refused identically so a
    replayed completion from a holder who lost the lease cannot succeed by
    probing for a different error.
    """


@dataclass(frozen=True)
class Lease:
    lease_id: str
    subject: str
    holder: str
    issued_at: float
    ttl_seconds: float
    last_heartbeat_at: float

    def is_expired(self, *, now: float) -> bool:
        return now > self.last_heartbeat_at + self.ttl_seconds


@dataclass(frozen=True)
class RetainedOutcome:
    """A completion refused because its lease was no longer current, kept
    as evidence (INV-L1).  It settled nothing."""

    lease_id: str
    subject: str
    holder: str
    result: object
    reason: str  # "expired" or "superseded"
    retained_at: float
    disposition: str = "stale"
    settlement_effect: str = "none"


class LeaseStore:
    """In-memory lease store. One active lease per subject at a time."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._by_subject: dict[str, Lease] = {}
        # Every lease ever issued, so a late completion can be attributed
        # to its own holder and retained (INV-L1).
        self._issued: dict[str, Lease] = {}
        self._retained: list[RetainedOutcome] = []

    def _now(self) -> float:
        return self._clock()

    def _current(self, subject: str) -> Lease | None:
        return self._by_subject.get(subject)

    def claim(self, subject: str, holder: str, *, ttl_seconds: float) -> Lease:
        """Claim `subject` for `holder`. Fails if a live lease already holds it."""
        now = self._now()
        current = self._current(subject)
        if current is not None and not current.is_expired(now=now):
            raise LeaseConflictError(f"subject {subject!r} is already leased")
        lease = Lease(
            lease_id=str(uuid.uuid4()),
            subject=subject,
            holder=holder,
            issued_at=now,
            ttl_seconds=ttl_seconds,
            last_heartbeat_at=now,
        )
        self._by_subject[subject] = lease
        self._issued[lease.lease_id] = lease
        return lease

    # Alias matching claim_work-style vocabulary used elsewhere in the
    # ecosystem (agentops/actionq), so callers reading for that pattern find
    # the same operation under the name they expect.
    claim_work = claim

    def reclaim(self, subject: str, holder: str, *, ttl_seconds: float) -> Lease:
        """Reclaim `subject` for a new `holder`. Fails unless the current
        lease (if any) is expired -- a live lease must be heartbeated or
        released by its own holder, never taken by force."""
        now = self._now()
        current = self._current(subject)
        if current is not None and not current.is_expired(now=now):
            raise LeaseConflictError(
                f"subject {subject!r} is still leased and has not expired"
            )
        lease = Lease(
            lease_id=str(uuid.uuid4()),
            subject=subject,
            holder=holder,
            issued_at=now,
            ttl_seconds=ttl_seconds,
            last_heartbeat_at=now,
        )
        self._by_subject[subject] = lease
        self._issued[lease.lease_id] = lease
        return lease

    def _current_for_lease_id(self, lease_id: str) -> Lease | None:
        for lease in self._by_subject.values():
            if lease.lease_id == lease_id:
                return lease
        return None

    def heartbeat(self, lease_id: str, holder: str) -> Lease:
        """Refresh `last_heartbeat_at`. Rejects an unknown/superseded lease
        id, a holder mismatch, or a lease that has already expired (expiry
        is checked before the heartbeat lands, so a heartbeat racing a
        reclaim loses -- it can never resurrect a lease someone else now
        holds)."""
        now = self._now()
        current = self._current_for_lease_id(lease_id)
        if current is None:
            raise LeaseNotCurrentError(f"lease {lease_id!r} is not current")
        if current.holder != holder:
            raise LeaseHolderMismatchError(
                f"holder {holder!r} does not match lease {lease_id!r}"
            )
        if current.is_expired(now=now):
            raise LeaseNotCurrentError(f"lease {lease_id!r} has already expired")
        renewed = replace(current, last_heartbeat_at=now)
        self._by_subject[current.subject] = renewed
        return renewed

    def complete(self, lease_id: str, holder: str, *, result: object = None) -> None:
        """Release the lease as successfully completed by its holder.

        A completion replayed against a lease id that is no longer current
        -- because it expired and was reclaimed, or never existed -- must
        fail exactly like an unknown lease, not silently no-op: a stale
        holder's late completion must never be mistaken for the new
        holder's work.  It is not discarded either (INV-L1): when the
        lease id was issued to `holder`, the attempt and its `result` are
        retained as a non-settling outcome (:meth:`retained_outcomes`)
        before the refusal is raised.
        """
        now = self._now()
        current = self._current_for_lease_id(lease_id)
        if current is None:
            issued = self._issued.get(lease_id)
            if issued is not None and issued.holder == holder:
                self._retain(issued, result, "superseded", now)
            raise LeaseNotCurrentError(f"lease {lease_id!r} is not current")
        if current.holder != holder:
            raise LeaseHolderMismatchError(
                f"holder {holder!r} does not match lease {lease_id!r}"
            )
        if current.is_expired(now=now):
            self._retain(current, result, "expired", now)
            raise LeaseNotCurrentError(f"lease {lease_id!r} has already expired")
        del self._by_subject[current.subject]

    def _retain(self, lease: Lease, result: object, reason: str, now: float) -> None:
        self._retained.append(
            RetainedOutcome(
                lease_id=lease.lease_id,
                subject=lease.subject,
                holder=lease.holder,
                result=result,
                reason=reason,
                retained_at=now,
            )
        )

    def retained_outcomes(self, subject: str) -> tuple[RetainedOutcome, ...]:
        """The non-settling outcomes kept for `subject`, oldest first."""
        return tuple(o for o in self._retained if o.subject == subject)

    def is_expired(self, subject: str) -> bool | None:
        """`True`/`False` for a known subject's current lease, `None` if
        the subject has no active lease at all."""
        current = self._current(subject)
        if current is None:
            return None
        return current.is_expired(now=self._now())
