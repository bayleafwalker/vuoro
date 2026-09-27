"""A bounded, expiring, one-use cache for replay protection (agentops#2519).

Each first verifier of a credential that must be accepted only once (a gateway
assertion's ``jti``, an edge proof's nonce) consumes its key here.  Properties
the callers depend on:

* **Atomic check-and-insert.**  One lock covers the purge, the lookup and the
  insert, so two concurrent requests carrying the same key cannot both be
  accepted, whether they race on event-loop tasks or on threadpool threads.
* **Expiry, not LRU.**  An entry lives until the credential it records can no
  longer be accepted anyway (the caller passes that instant: ``exp`` plus
  clock skew).  Expired entries are purged in expiry order on every call.
* **Fail closed at the cap.**  When the cache holds ``max_entries`` unexpired
  entries it refuses new keys (`ReplayCacheFull`) instead of evicting one:
  evicting an unexpired entry would make that credential replayable.  The
  refusal is logged, throttled so a flood cannot also flood the log.
* **Process memory only.**  A restart empties it.  See `docs/architecture/
  gateway-identity.md` ("Replay protection") for why that is acceptable and
  for the startup watermark that covers the fast-restart case.
"""

from __future__ import annotations

from collections.abc import Callable
import heapq
import logging
import threading
import time


LOGGER = logging.getLogger(__name__)

#: 32 s (a 30 s assertion plus 2 s skew) at 1,500 requests per second per
#: process.  Keys are fixed-size digests, so this bounds memory at a few MiB.
DEFAULT_MAX_ENTRIES = 50_000
_FULL_LOG_INTERVAL_SECONDS = 60.0


class ReplayCacheFull(RuntimeError):
    """The cache is at capacity with unexpired entries; the key was refused."""


class ReplayCache:
    """One-use keys with per-entry expiry and a hard size cap."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        name: str = "replay",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._name = name
        self._clock = clock
        self._entries: dict[bytes, float] = {}
        self._expiry: list[tuple[float, bytes]] = []
        self._lock = threading.Lock()
        self._refused_since_log = 0
        self._last_full_log: float | None = None

    def __len__(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._entries)

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def consume(self, key: bytes, expires_at: float) -> bool:
        """Record `key` until `expires_at`.  False if it is already recorded.

        Raises `ReplayCacheFull` when a new key does not fit.
        """

        with self._lock:
            now = self._clock()
            self._purge(now)
            if key in self._entries:
                return False
            self._insert(key, expires_at, now)
            return True

    def mark(self, key: bytes, expires_at: float) -> None:
        """Record `key` whether or not it is already recorded.

        For a verifier that accepts a credential through another route (an
        edge proof) and must stop its direct use from then on.  Raises
        `ReplayCacheFull` when a new key does not fit.
        """

        with self._lock:
            now = self._clock()
            self._purge(now)
            existing = self._entries.get(key)
            if existing is None:
                self._insert(key, expires_at, now)
            elif expires_at > existing:
                self._entries[key] = expires_at
                heapq.heappush(self._expiry, (expires_at, key))

    def _insert(self, key: bytes, expires_at: float, now: float) -> None:
        if len(self._entries) >= self._max_entries:
            self._refused_since_log += 1
            if (
                self._last_full_log is None
                or now - self._last_full_log >= _FULL_LOG_INTERVAL_SECONDS
            ):
                LOGGER.error(
                    "replay cache is full; refusing new credentials",
                    extra={
                        "replay_cache": self._name,
                        "max_entries": self._max_entries,
                        "refused": self._refused_since_log,
                    },
                )
                self._last_full_log = now
                self._refused_since_log = 0
            raise ReplayCacheFull(f"{self._name} replay cache is full")
        self._entries[key] = expires_at
        heapq.heappush(self._expiry, (expires_at, key))

    def _purge(self, now: float) -> None:
        expiry = self._expiry
        entries = self._entries
        while expiry and expiry[0][0] <= now:
            expires_at, key = heapq.heappop(expiry)
            # A `mark` that extended an entry leaves its older heap row
            # behind; only the row matching the live expiry removes it.
            if entries.get(key) == expires_at:
                del entries[key]


__all__ = ["DEFAULT_MAX_ENTRIES", "ReplayCache", "ReplayCacheFull"]
