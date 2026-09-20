from __future__ import annotations

import httpx
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from vuoro_service.metrics import RequestMetrics
from vuoro_service.rate_limit import RateLimiter


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _service(*, rate_limiter=None, metrics=None):
    handler_calls: list[str] = []
    registry = CatalogRegistry()
    registry.register(
        OperationDefinition(
            name="work.pilot.transition",
            owning_domain="work",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
                "additionalProperties": False,
            },
            result_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["accepted"],
                "properties": {"accepted": {"type": "integer"}},
                "additionalProperties": False,
            },
            required_authority="work.transition",
            execution_semantics="write",
            idempotency="required",
        ),
        lambda arguments, context: handler_calls.append(context.request_id)
        or {"accepted": arguments["value"]},
    )
    settings = ServiceSettings(
        environment_name="vuoro-dev",
        environment_class="development",
        compatibility_state="compatible",
        domains={
            "work": DomainCompatibility(
                api_version="work/v1", schema_version="work-schema/1", state="compatible"
            )
        },
    )
    resolver = StaticBearerIdentityResolver(
        {
            "dev-token": Identity(
                actor="human:developer",
                environment="vuoro-dev",
                authorities=frozenset({"work.transition"}),
            )
        }
    )
    app = create_app(
        settings=settings,
        registry=registry,
        identity_resolver=resolver,
        rate_limiter=rate_limiter,
        metrics=metrics,
    )
    return registry, app, handler_calls


def _envelope(registry, request_id: str) -> dict:
    return {
        "schema_version": "invocation/v1",
        "request_id": request_id,
        "operation": "work.pilot.transition",
        "arguments": {"value": 1},
        "catalog_revision": registry.revision,
        "idempotency_key": f"key-{request_id}",
    }


@pytest.mark.anyio
async def test_request_past_the_rate_limit_gets_429_and_never_reaches_the_handler() -> None:
    limiter = RateLimiter(capacity=1, refill_per_second=0.001)
    registry, app, handler_calls = _service(rate_limiter=limiter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer dev-token", "X-Vuoro-Client-Protocol": "1"}
        first = await client.post(
            "/api/invoke/v1", headers=headers, json=_envelope(registry, "r1")
        )
        second = await client.post(
            "/api/invoke/v1", headers=headers, json=_envelope(registry, "r2")
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate-limit-exceeded"
    # The handler ran exactly once: the second (rate-limited) call never
    # reached it.
    assert handler_calls == ["r1"]


@pytest.mark.anyio
async def test_rate_limit_rejection_is_distinct_from_an_auth_failure() -> None:
    limiter = RateLimiter(capacity=1, refill_per_second=0.001)
    registry, app, _ = _service(rate_limiter=limiter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # No Authorization header at all -- would normally be a 401.
        no_auth = await client.post(
            "/api/invoke/v1",
            headers={"X-Vuoro-Client-Protocol": "1"},
            json=_envelope(registry, "unauth"),
        )
        assert no_auth.status_code == 401
        assert no_auth.json()["error"]["code"] == "identity-required"

        # Exhaust the budget for this (empty-token, ip) key, then confirm the
        # rejection code and status differ from the auth failure above.
        limited = await client.post(
            "/api/invoke/v1",
            headers={"X-Vuoro-Client-Protocol": "1"},
            json=_envelope(registry, "unauth2"),
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate-limit-exceeded"
        assert limited.status_code != no_auth.status_code
        assert limited.json()["error"]["code"] != no_auth.json()["error"]["code"]


@pytest.mark.anyio
async def test_no_rate_limiter_configured_means_no_limiting() -> None:
    registry, app, handler_calls = _service(rate_limiter=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer dev-token", "X-Vuoro-Client-Protocol": "1"}
        for i in range(5):
            response = await client.post(
                "/api/invoke/v1", headers=headers, json=_envelope(registry, f"r{i}")
            )
            assert response.status_code == 200
    assert len(handler_calls) == 5


@pytest.mark.anyio
async def test_health_metrics_endpoint_is_queryable_and_reflects_traffic() -> None:
    metrics = RequestMetrics()
    registry, app, _ = _service(metrics=metrics)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        before = await client.get("/health/metrics")
        assert before.status_code == 200
        assert before.json()["request_count"] == 0

        headers = {"Authorization": "Bearer dev-token", "X-Vuoro-Client-Protocol": "1"}
        await client.post("/api/invoke/v1", headers=headers, json=_envelope(registry, "m1"))

        after = await client.get("/health/metrics")

    body = after.json()
    assert body["request_count"] == 1
    assert body["error_count"] == 0
    assert body["p50_latency_ms"] is not None
