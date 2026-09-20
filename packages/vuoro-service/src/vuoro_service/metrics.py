"""Endpoint health/monitoring signal (agentops#2464, E0 acceptance (d)).

With no public surface exposed yet, this is the signal itself, not its
exposure: a request/error counter plus a bounded latency sample, queryable
as a snapshot. `create_app` wires it around the invoke dispatch and exposes
it at `/health/metrics`; nothing here assumes network reachability.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class MetricsSnapshot:
    request_count: int
    error_count: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count


def _percentile(sorted_samples: list[float], fraction: float) -> float:
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    index = fraction * (len(sorted_samples) - 1)
    lower = int(index)
    upper = min(lower + 1, len(sorted_samples) - 1)
    weight = index - lower
    return sorted_samples[lower] + (sorted_samples[upper] - sorted_samples[lower]) * weight


class RequestMetrics:
    """Cumulative request/error counts plus a bounded rolling latency sample.

    `max_samples` bounds memory on a long-running process; older latency
    samples are dropped first-in-first-out, but `request_count` and
    `error_count` are never truncated -- they are the exact lifetime totals.
    """

    def __init__(
        self,
        *,
        max_samples: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_samples <= 0:
            raise ValueError("max_samples must be positive")
        self._clock = clock
        self._request_count = 0
        self._error_count = 0
        self._latencies_ms: deque[float] = deque(maxlen=max_samples)

    def record(self, *, latency_ms: float, is_error: bool) -> None:
        self._request_count += 1
        if is_error:
            self._error_count += 1
        self._latencies_ms.append(latency_ms)

    def start_timer(self) -> Callable[[bool], None]:
        """Convenience: `stop = metrics.start_timer(); ...; stop(is_error)`."""
        started_at = self._clock()

        def stop(is_error: bool) -> None:
            elapsed_ms = (self._clock() - started_at) * 1000.0
            self.record(latency_ms=elapsed_ms, is_error=is_error)

        return stop

    def snapshot(self) -> MetricsSnapshot:
        samples = sorted(self._latencies_ms)
        return MetricsSnapshot(
            request_count=self._request_count,
            error_count=self._error_count,
            p50_latency_ms=_percentile(samples, 0.50) if samples else None,
            p95_latency_ms=_percentile(samples, 0.95) if samples else None,
        )
