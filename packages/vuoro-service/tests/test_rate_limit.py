from __future__ import annotations

import pytest

from vuoro_service.rate_limit import RateLimitExceededError, RateLimiter, rate_limit_key


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_requests_within_capacity_all_succeed() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_second=1, clock=clock)
    for _ in range(3):
        limiter.check("k")


def test_request_past_capacity_is_rejected_with_retry_after() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_second=1, clock=clock)
    limiter.check("k")
    limiter.check("k")
    with pytest.raises(RateLimitExceededError) as excinfo:
        limiter.check("k")
    assert excinfo.value.retry_after_seconds > 0


def test_bucket_refills_over_time_and_allows_more_requests() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=1, clock=clock)
    limiter.check("k")
    with pytest.raises(RateLimitExceededError):
        limiter.check("k")
    clock.advance(1.0)
    limiter.check("k")  # refilled exactly one token


def test_distinct_keys_have_independent_budgets() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=1, clock=clock)
    limiter.check("token-a\x1f1.2.3.4")
    with pytest.raises(RateLimitExceededError):
        limiter.check("token-a\x1f1.2.3.4")
    # A different key (different token, or different IP) has its own budget.
    limiter.check("token-b\x1f1.2.3.4")
    limiter.check("token-a\x1f5.6.7.8")


def test_rate_limit_key_combines_token_and_ip_so_neither_alone_evades_it() -> None:
    same_token_diff_ip_a = rate_limit_key(token="t", client_ip="1.1.1.1")
    same_token_diff_ip_b = rate_limit_key(token="t", client_ip="2.2.2.2")
    diff_token_same_ip = rate_limit_key(token="u", client_ip="1.1.1.1")
    assert same_token_diff_ip_a != same_token_diff_ip_b
    assert same_token_diff_ip_a != diff_token_same_ip


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        RateLimiter(capacity=0, refill_per_second=1)
    with pytest.raises(ValueError):
        RateLimiter(capacity=1, refill_per_second=0)
