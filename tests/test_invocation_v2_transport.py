from __future__ import annotations

import httpx
import pytest

from vuoro_client import AsyncVuoroClient, Profile
from vuoro_client.errors import ClientIncompatibleError
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _CountingTransport(httpx.AsyncBaseTransport):
    """Wraps another transport and records requests by HTTP method."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.method_counts: dict[str, int] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.method_counts[request.method] = (
            self.method_counts.get(request.method, 0) + 1
        )
        return await self._inner.handle_async_request(request)


def definition(name: str) -> OperationDefinition:
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
    )


def build_app(*, invocation_schema_versions: tuple[str, ...], handler=None):
    registry = CatalogRegistry()
    observed_context: list = []

    def default_handler(arguments, context):
        observed_context.append(context)
        return {"echo": arguments["message"]}

    registry.register(definition("work.pilot.claim"), handler or default_handler)
    settings = ServiceSettings(
        environment_name="vuoro-dev",
        environment_class="development",
        compatibility_state="compatible",
        invocation_schema_versions=invocation_schema_versions,
    )
    app = create_app(
        settings=settings,
        registry=registry,
        identity_resolver=StaticBearerIdentityResolver(
            {"token": Identity(actor="test", environment="vuoro-dev")}
        ),
    )
    return app, observed_context


@pytest.mark.anyio
async def test_proofless_invoke_uses_v1_against_v1_only_server() -> None:
    app, _ = build_app(invocation_schema_versions=("invocation/v1",))
    transport = _CountingTransport(httpx.ASGITransport(app=app))
    async with AsyncVuoroClient(
        Profile("dev", "http://test", "identity"),
        lambda reference: "token",
        transport=transport,
    ) as client:
        result = await client.invoke("work.pilot.claim", {"message": "hello"})
        assert result == {"echo": "hello"}


@pytest.mark.anyio
async def test_proofless_invoke_uses_v1_against_v2_capable_server() -> None:
    app, _ = build_app(invocation_schema_versions=("invocation/v1", "invocation/v2"))
    transport = _CountingTransport(httpx.ASGITransport(app=app))
    async with AsyncVuoroClient(
        Profile("dev", "http://test", "identity"),
        lambda reference: "token",
        transport=transport,
    ) as client:
        result = await client.invoke("work.pilot.claim", {"message": "hello"})
        assert result == {"echo": "hello"}
        # Proofless invoke never needs the v2 route.
        assert client._catalog is not None


@pytest.mark.anyio
async def test_invoke_with_transient_credentials_posts_to_v2_with_bindings() -> None:
    app, observed_context = build_app(
        invocation_schema_versions=("invocation/v1", "invocation/v2")
    )
    async with AsyncVuoroClient(
        Profile("dev", "http://test", "identity"),
        lambda reference: "token",
        transport=httpx.ASGITransport(app=app),
    ) as client:
        key = "sha256:" + "d" * 64
        result = await client.invoke(
            "work.pilot.claim",
            {"message": "claimed"},
            transient_credentials={key: "proof-value"},
        )
        assert result == {"echo": "claimed"}
        assert len(observed_context) == 1
        assert observed_context[0].transient_credentials.reveal(key) == "proof-value"


@pytest.mark.anyio
async def test_invoke_with_transient_credentials_against_v1_only_server_raises_without_post() -> (
    None
):
    app, observed_context = build_app(invocation_schema_versions=("invocation/v1",))
    transport = _CountingTransport(httpx.ASGITransport(app=app))
    async with AsyncVuoroClient(
        Profile("dev", "http://test", "identity"),
        lambda reference: "token",
        transport=transport,
    ) as client:
        # Prime the handshake capability discovery explicitly so the
        # incompatibility check below is isolated from handshake GETs.
        await client.handshake()
        post_count_before = transport.method_counts.get("POST", 0)
        with pytest.raises(ClientIncompatibleError, match="invocation/v2"):
            await client.invoke(
                "work.pilot.claim",
                {"message": "claimed"},
                transient_credentials={"sha256:" + "e" * 64: "proof-value"},
            )
        assert transport.method_counts.get("POST", 0) == post_count_before
        assert observed_context == []
