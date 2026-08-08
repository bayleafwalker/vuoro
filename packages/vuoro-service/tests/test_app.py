from __future__ import annotations

import httpx
import pytest

from vuoro_service.app import create_app
from vuoro_service.cli import build_parser, main


def test_bootstrap_exposes_only_operational_probes() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/health/live" in paths
    assert "/health/ready" in paths
    assert "/api/meta/v1/handshake" in paths
    assert "/api/catalog/v1" in paths
    assert "/api/invoke/v1" in paths


def test_service_commands_are_process_scoped() -> None:
    parser = build_parser()
    assert {"serve"} <= set(
        parser._subparsers._group_actions[0].choices
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_liveness_is_independent_of_runtime_readiness() -> None:
    app = create_app(readiness_check=lambda: False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


@pytest.mark.anyio
async def test_health_readiness_projects_generic_runtime_callback_and_fails_closed() -> None:
    app = create_app(readiness_check=lambda: False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not-ready",
        "compatibility": "degraded",
        "error": {
            "code": "runtime-unavailable",
            "message": "an essential service runtime dependency is unavailable",
        },
    }


@pytest.mark.anyio
async def test_health_readiness_hides_callback_failures_in_the_same_503_envelope() -> None:
    def unavailable() -> bool:
        raise RuntimeError("database connection detail must not leak")

    app = create_app(readiness_check=unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "runtime-unavailable",
        "message": "an essential service runtime dependency is unavailable",
    }


@pytest.mark.anyio
async def test_health_readiness_is_200_only_when_compatibility_and_runtime_are_ready() -> None:
    app = create_app(readiness_check=lambda: True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "compatibility": "degraded"}
