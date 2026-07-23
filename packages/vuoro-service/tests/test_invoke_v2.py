from __future__ import annotations

import httpx
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


VALID_KEY_A = "sha256:" + "a" * 64
VALID_KEY_B = "sha256:" + "b" * 64


def configured_service(handler=None) -> tuple[CatalogRegistry, object]:
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
        handler or (lambda arguments, context: {"accepted": arguments["value"]}),
    )
    settings = ServiceSettings(
        environment_name="vuoro-dev",
        environment_class="development",
        compatibility_state="compatible",
        domains={
            "work": DomainCompatibility(
                api_version="work/v1",
                schema_version="work-schema/1",
                state="compatible",
            )
        },
    )
    resolver = StaticBearerIdentityResolver(
        {
            "dev-token": Identity(
                actor="human:developer",
                environment="vuoro-dev",
                authorities=frozenset({"work.transition"}),
            ),
        }
    )
    return registry, create_app(
        settings=settings, registry=registry, identity_resolver=resolver
    )


def base_request(revision: str, **overrides) -> dict:
    request = {
        "schema_version": "invocation/v2",
        "request_id": "request-v2",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": revision,
        "idempotency_key": "transition-7",
        "transient_credentials": {},
    }
    request.update(overrides)
    return request


@pytest.mark.anyio
async def test_handshake_advertises_invocation_schema_versions_with_v2() -> None:
    _, app = configured_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        handshake = (await client.get("/api/meta/v1/handshake")).json()
        assert handshake["invocation_schema_versions"] == [
            "invocation/v1",
            "invocation/v2",
        ]
        assert handshake["schema_versions"]["invocation"] == "invocation/v1"


@pytest.mark.anyio
async def test_handshake_advertises_v1_only_when_v2_not_wired() -> None:
    registry = CatalogRegistry()
    settings = ServiceSettings(
        environment_name="vuoro-dev",
        environment_class="development",
        compatibility_state="compatible",
        invocation_schema_versions=("invocation/v1",),
    )
    app = create_app(settings=settings, registry=registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        handshake = (await client.get("/api/meta/v1/handshake")).json()
        assert handshake["invocation_schema_versions"] == ["invocation/v1"]


@pytest.mark.anyio
async def test_v2_multi_binding_reaches_handler_via_reveal() -> None:
    observed = None

    def handler(arguments, context):
        nonlocal observed
        observed = context
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(
                registry.revision,
                transient_credentials={
                    VALID_KEY_A: "proof-a",
                    VALID_KEY_B: "proof-b",
                },
            ),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert observed is not None
    assert observed.transient_credentials.reveal(VALID_KEY_A) == "proof-a"
    assert observed.transient_credentials.reveal(VALID_KEY_B) == "proof-b"
    assert observed.transient_credentials.reveal("sha256:" + "c" * 64) is None
    assert sorted(observed.transient_credentials.keys()) == sorted(
        [VALID_KEY_A, VALID_KEY_B]
    )
    assert "proof-a" not in repr(observed.transient_credentials)
    assert "proof-b" not in repr(observed.transient_credentials)


@pytest.mark.anyio
async def test_v1_route_defaults_to_empty_transient_credentials() -> None:
    observed = None

    def handler(arguments, context):
        nonlocal observed
        observed = context
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json={
                "schema_version": "invocation/v1",
                "request_id": "request-v1",
                "operation": "work.pilot.transition",
                "arguments": {"value": 7},
                "catalog_revision": registry.revision,
                "idempotency_key": "transition-7",
            },
        )
    assert response.status_code == 200
    assert observed is not None
    assert bool(observed.transient_credentials) is False
    assert observed.transient_credentials.reveal(VALID_KEY_A) is None


@pytest.mark.parametrize(
    "bindings",
    [
        {"not-a-sha256": "value"},
        {"sha256:" + "g" * 64: "value"},
        {"sha256:" + "A" * 64: "value"},
        {VALID_KEY_A: ""},
        {f"sha256:{n:064x}": "value" for n in range(9)},
    ],
    ids=[
        "malformed-prefix",
        "invalid-hex-char",
        "uppercase-hex",
        "empty-value",
        "over-cap-bindings",
    ],
)
@pytest.mark.anyio
async def test_v2_structural_violations_fail_closed(bindings: dict) -> None:
    called = False

    def handler(arguments, context):
        nonlocal called
        called = True
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(registry.revision, transient_credentials=bindings),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-transient-binding"
    assert called is False


@pytest.mark.anyio
async def test_v2_route_is_exposed() -> None:
    _, app = configured_service()
    paths = {route.path for route in app.routes}
    assert "/api/invoke/v2" in paths
    assert "/api/invoke/v1" in paths
