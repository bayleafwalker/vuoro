from __future__ import annotations

import httpx
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, OperationRejectedError
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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
            "prod-token": Identity(
                actor="human:operator",
                environment="production",
                authorities=frozenset({"work.transition"}),
            ),
        }
    )
    return registry, create_app(
        settings=settings, registry=registry, identity_resolver=resolver
    )


@pytest.mark.anyio
async def test_handshake_and_etag_catalog_contract() -> None:
    registry, app = configured_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        handshake = (await client.get("/api/meta/v1/handshake")).json()
        assert handshake["environment"] == {
            "name": "vuoro-dev",
            "environment_class": "development",
        }
        assert handshake["client_protocol"] == {"minimum": 1, "maximum": 1}
        assert handshake["catalog_revision"] == registry.revision
        assert handshake["compatibility"]["domains"]["work"]["state"] == "compatible"

        first = await client.get(
            "/api/catalog/v1", headers={"X-Vuoro-Client-Protocol": "1"}
        )
        assert first.status_code == 200
        assert first.headers["etag"] == f'"{registry.revision}"'
        cached = await client.get(
            "/api/catalog/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "If-None-Match": first.headers["etag"],
            },
        )
        assert cached.status_code == 304
        incompatible = await client.get(
            "/api/catalog/v1", headers={"X-Vuoro-Client-Protocol": "2"}
        )
        assert incompatible.status_code == 426
        assert incompatible.json()["error"]["code"] == "client-protocol-incompatible"


@pytest.mark.anyio
async def test_invocation_derives_identity_and_enforces_contract() -> None:
    registry, app = configured_service()
    request = {
        "schema_version": "invocation/v1",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": registry.revision,
        "idempotency_key": "transition-7",
    }
    protocol = {"X-Vuoro-Client-Protocol": "1"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing_identity = await client.post(
            "/api/invoke/v1", headers=protocol, json=request
        )
        assert missing_identity.status_code == 401
        assert missing_identity.json()["error"]["code"] == "identity-required"

        wrong_environment = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer prod-token"},
            json=request,
        )
        assert wrong_environment.status_code == 403
        assert wrong_environment.json()["error"]["code"] == "environment-mismatch"

        missing_key = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer dev-token"},
            json={**request, "idempotency_key": None},
        )
        assert missing_key.status_code == 400
        assert missing_key.json()["error"]["code"] == "idempotency-key-required"

        invalid_input = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer dev-token"},
            json={**request, "arguments": {"value": "seven"}},
        )
        assert invalid_input.status_code == 422
        assert invalid_input.json()["error"]["code"] == "schema-validation-failed"

        accepted = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer dev-token"},
            json=request,
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["result"] == {"accepted": 7}

        stale = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer dev-token"},
            json={**request, "catalog_revision": "0" * 64},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "stale-catalog"


@pytest.mark.anyio
async def test_invalid_adapter_result_stays_in_envelope_without_leaking_details() -> (
    None
):
    registry, app = configured_service(
        lambda arguments, context: {"private_failure": "not the declared result"}
    )
    request = {
        "schema_version": "invocation/v1",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": registry.revision,
        "idempotency_key": "transition-7",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=request,
        )
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert response.json()["error"]["code"] == "adapter-result-invalid"
    assert "private_failure" not in response.text


@pytest.mark.anyio
async def test_domain_rejection_is_distinct_from_transport_or_handler_failure() -> None:
    def reject(arguments, context):
        raise OperationRejectedError("stale-basis", "basis revision has advanced")

    registry, app = configured_service(reject)
    request = {
        "schema_version": "invocation/v1",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": registry.revision,
        "idempotency_key": "transition-7",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=request,
        )
    assert response.status_code == 409
    assert response.json()["status"] == "rejected"
    assert response.json()["error"] == {
        "code": "stale-basis",
        "message": "basis revision has advanced",
    }
