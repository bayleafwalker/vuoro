from __future__ import annotations

import httpx
import pytest

from vuoro_client import AsyncVuoroClient, Profile
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_handshake_serves_effective_environment_metadata() -> None:
    app = create_app(
        settings=ServiceSettings(
            environment_name="vuoro-shared",
            environment_class="production",
            environment_constraints=("production-identities-only", "separate-from-vuoro-dev"),
            environment_runbook_refs=("/docs/runbooks/vuoro-workstation-cutover.md",),
            compatibility_state="compatible",
        ),
        registry=CatalogRegistry(),
        identity_resolver=StaticBearerIdentityResolver(
            {"token": Identity(actor="test", environment="vuoro-shared")}
        ),
    )
    profile = Profile(
        name="appservice-shared",
        endpoint="http://test",
        credential_ref="shared-identity",
        expected_environment="vuoro-shared",
    )
    async with AsyncVuoroClient(
        profile, lambda reference: "token", transport=httpx.ASGITransport(app=app)
    ) as client:
        handshake = await client.handshake()
        assert handshake["environment"]["constraints"] == [
            "production-identities-only",
            "separate-from-vuoro-dev",
        ]
        assert handshake["environment"]["runbook_refs"] == [
            "/docs/runbooks/vuoro-workstation-cutover.md"
        ]

        assert client.active_environment == "vuoro-shared"
        assert client.active_environment_class == "production"
        assert client.active_environment_constraints == (
            "production-identities-only",
            "separate-from-vuoro-dev",
        )
        assert client.active_environment_runbook_refs == (
            "/docs/runbooks/vuoro-workstation-cutover.md",
        )

        description = client.describe_active_environment()
        assert "vuoro-shared (production)" in description
        assert "production-identities-only" in description
        assert "/docs/runbooks/vuoro-workstation-cutover.md" in description


@pytest.mark.anyio
async def test_handshake_defaults_to_empty_metadata_when_unconfigured() -> None:
    app = create_app(
        settings=ServiceSettings(
            environment_name="vuoro-dev",
            environment_class="development",
            compatibility_state="compatible",
        ),
        registry=CatalogRegistry(),
        identity_resolver=StaticBearerIdentityResolver(
            {"token": Identity(actor="test", environment="vuoro-dev")}
        ),
    )
    profile = Profile(
        name="appservice-dev", endpoint="http://test", credential_ref="dev-identity"
    )
    async with AsyncVuoroClient(
        profile, lambda reference: "token", transport=httpx.ASGITransport(app=app)
    ) as client:
        handshake = await client.handshake()
        assert handshake["environment"]["constraints"] == []
        assert handshake["environment"]["runbook_refs"] == []
        description = client.describe_active_environment()
        assert description == "environment: vuoro-dev (development)"
