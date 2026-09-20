from __future__ import annotations

from vuoro_service.metrics import RequestMetrics


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_empty_metrics_snapshot_has_no_latency() -> None:
    snapshot = RequestMetrics().snapshot()
    assert snapshot.request_count == 0
    assert snapshot.error_count == 0
    assert snapshot.p50_latency_ms is None
    assert snapshot.p95_latency_ms is None
    assert snapshot.error_rate == 0.0


def test_records_request_and_error_counts() -> None:
    metrics = RequestMetrics()
    metrics.record(latency_ms=1.0, is_error=False)
    metrics.record(latency_ms=2.0, is_error=True)
    metrics.record(latency_ms=3.0, is_error=False)
    snapshot = metrics.snapshot()
    assert snapshot.request_count == 3
    assert snapshot.error_count == 1
    assert snapshot.error_rate == 1 / 3


def test_latency_percentiles_reflect_recorded_samples() -> None:
    metrics = RequestMetrics()
    for value in [10.0, 20.0, 30.0, 40.0, 100.0]:
        metrics.record(latency_ms=value, is_error=False)
    snapshot = metrics.snapshot()
    assert snapshot.p50_latency_ms == 30.0
    assert snapshot.p95_latency_ms is not None
    assert snapshot.p95_latency_ms >= snapshot.p50_latency_ms


def test_max_samples_bounds_latency_history_but_not_counts() -> None:
    metrics = RequestMetrics(max_samples=2)
    for _ in range(5):
        metrics.record(latency_ms=1.0, is_error=False)
    snapshot = metrics.snapshot()
    # Counts are exact lifetime totals, never truncated by the sample bound.
    assert snapshot.request_count == 5
    assert snapshot.error_count == 0


def test_start_timer_records_elapsed_time_and_error_flag() -> None:
    clock = FakeClock()
    metrics = RequestMetrics(clock=clock)
    stop = metrics.start_timer()
    clock.advance(0.05)  # 50ms
    stop(True)
    snapshot = metrics.snapshot()
    assert snapshot.request_count == 1
    assert snapshot.error_count == 1
    assert snapshot.p50_latency_ms == 50.0
