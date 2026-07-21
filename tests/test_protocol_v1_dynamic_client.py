from __future__ import annotations

import httpx
import pytest

from vuoro_client import AsyncVuoroClient, Profile
from vuoro_client.errors import ClientIncompatibleError
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, DEFAULT_SCHEMA_FEATURES
from vuoro_service.contracts import OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def definition(
    name: str,
    *,
    required_features: list[str] | None = None,
) -> OperationDefinition:
    return OperationDefinition(
        name=name,
        owning_domain=name.split(".", 1)[0],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
            "additionalProperties": False,
        },
        result_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["echo"],
            "properties": {"echo": {"type": "string"}},
            "additionalProperties": False,
        },
        execution_semantics="read",
        idempotency="optional",
        required_client_schema_features=required_features or [],
    )


@pytest.mark.anyio
async def test_installed_protocol_v1_client_invokes_new_operation_without_reinstall() -> (
    None
):
    registry = CatalogRegistry()
    registry.register(
        definition("work.pilot.existing"),
        lambda arguments, context: {"echo": arguments["message"]},
    )
    app = create_app(
        settings=ServiceSettings(
            environment_name="vuoro-dev",
            environment_class="development",
            compatibility_state="compatible",
        ),
        registry=registry,
        identity_resolver=StaticBearerIdentityResolver(
            {"token": Identity(actor="test", environment="vuoro-dev")}
        ),
    )
    profile = Profile(
        name="appservice-dev",
        endpoint="http://test",
        credential_ref="dev-identity",
        expected_environment="vuoro-dev",
    )
    async with AsyncVuoroClient(
        profile,
        lambda reference: "token",
        transport=httpx.ASGITransport(app=app),
    ) as client:
        assert (await client.handshake())["environment"]["name"] == "vuoro-dev"
        original_catalog = await client.catalog()
        assert [operation["name"] for operation in original_catalog["operations"]] == [
            "work.pilot.existing"
        ]

        registry.register(
            definition("work.pilot.new-command"),
            lambda arguments, context: {"echo": arguments["message"]},
        )

        result = await client.invoke("work.pilot.new-command", {"message": "available"})
        assert result == {"echo": "available"}
        assert client.active_environment == "vuoro-dev"


@pytest.mark.anyio
async def test_unsupported_schema_feature_is_explicit_client_incompatibility() -> None:
    registry = CatalogRegistry(
        schema_features=DEFAULT_SCHEMA_FEATURES | frozenset({"unevaluated-properties"})
    )
    registry.register(
        definition(
            "work.pilot.future-schema",
            required_features=["unevaluated-properties"],
        ),
        lambda arguments, context: {"echo": arguments["message"]},
    )
    app = create_app(
        settings=ServiceSettings(
            environment_name="vuoro-dev",
            environment_class="development",
            compatibility_state="compatible",
        ),
        registry=registry,
        identity_resolver=StaticBearerIdentityResolver(
            {"token": Identity(actor="test", environment="vuoro-dev")}
        ),
    )
    async with AsyncVuoroClient(
        Profile("dev", "http://test", "identity"),
        lambda reference: "token",
        transport=httpx.ASGITransport(app=app),
    ) as client:
        with pytest.raises(ClientIncompatibleError, match="unevaluated-properties"):
            await client.invoke("work.pilot.future-schema", {"message": "future"})
