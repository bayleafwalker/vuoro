"""Rate limiting on the invocation surface (agentops#2464, E0 acceptance (c)).

The callers of an exposed surface are vendor clouds, not the operator: this
guards the invoke path with a per-`(token, ip)` token bucket, evaluated
before the handler runs, so a caller past its configured limit gets a
distinct, non-auth rejection and never reaches `registry.invoke`.

Configuration lives alongside the other token/identity config
(`gateway_identity.py`'s trust configuration), not a new surface: callers
construct a `RateLimiter` with `capacity`/`refill_per_second` and pass it to
`create_app`.
"""

from __future__ import annotations

from collections.abc import Callable
import time


class RateLimitExceededError(Exception):
    """Raised by `RateLimiter.check` when a key is over its configured limit.

    Deliberately not an `IdentityResolutionError` subclass: a rate-limit
    rejection must be distinguishable from an auth failure by the caller,
    both in HTTP status (429, not 401/403) and in error code.
    """

    def __init__(self, key: str, *, retry_after_seconds: float) -> None:
        super().__init__(f"rate limit exceeded for {key!r}")
        self.key = key
        self.retry_after_seconds = retry_after_seconds


class _Bucket:
    __slots__ = ("tokens", "last_refill_at")

    def __init__(self, tokens: float, last_refill_at: float) -> None:
        self.tokens = tokens
        self.last_refill_at = last_refill_at


class RateLimiter:
    """A per-key token bucket. One bucket per `(token, ip)` pair by default."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self._capacity = float(capacity)
        self._refill_per_second = float(refill_per_second)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, key: str, now: float) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, last_refill_at=now)
            self._buckets[key] = bucket
            return bucket
        elapsed = max(0.0, now - bucket.last_refill_at)
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
        bucket.last_refill_at = now
        return bucket

    def check(self, key: str) -> None:
        """Consume one token for `key`. Raises `RateLimitExceededError` if
        none are available; does not reach the handler when it does."""
        now = self._clock()
        bucket = self._bucket(key, now)
        if bucket.tokens < 1.0:
            deficit = 1.0 - bucket.tokens
            retry_after = deficit / self._refill_per_second
            raise RateLimitExceededError(key, retry_after_seconds=retry_after)
        bucket.tokens -= 1.0


def rate_limit_key(*, token: str | None, client_ip: str | None) -> str:
    """The canonical `(token, ip)` bucket key. Both parts are included so a
    single abusive token cannot be laundered across source IPs, and a single
    IP sharing many tokens does not exhaust one caller's budget for another."""
    return f"{token or '-'}\x1f{client_ip or '-'}"
