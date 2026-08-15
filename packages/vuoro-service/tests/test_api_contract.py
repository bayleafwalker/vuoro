from __future__ import annotations

import logging

import httpx
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, OperationRejectedError
from vuoro_service.contracts import (
    DomainCompatibility, OperationDefinition, ResourceKindDefinition,
    ResourceObservationContract,
)
from vuoro_service.identity import (
    Identity,
    IdentityResolutionError,
    StaticBearerIdentityResolver,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def configured_service(handler=None, identity_resolver=None) -> tuple[CatalogRegistry, object]:
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
        settings=settings,
        registry=registry,
        identity_resolver=identity_resolver or resolver,
    )


@pytest.mark.anyio
async def test_identity_is_bound_to_the_parsed_invocation_request_id() -> None:
    calls: list[str] = []

    def resolver(request):
        if getattr(request.state, "vuoro_invocation_request_id", None) != "body-id":
            raise IdentityResolutionError("request correlation failed")
        return Identity(
            actor="human:developer",
            environment="vuoro-dev",
            authorities=frozenset({"work.transition"}),
        )

    registry, app = configured_service(
        handler=lambda arguments, context: calls.append(context.request_id)
        or {"accepted": arguments["value"]},
        identity_resolver=resolver,
    )
    request = {
        "schema_version": "invocation/v1",
        "request_id": "body-id",
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
            headers={"X-Vuoro-Client-Protocol": "1"},
            json=request,
        )
    assert response.status_code == 200
    assert calls == ["body-id"]


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
            "constraints": [],
            "runbook_refs": [],
        }
        assert handshake["client_protocol"] == {"minimum": 1, "maximum": 1}
        assert handshake["service_release"] == {
            "distribution": "vuoro-service",
            "version": "0.1.46",
        }
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
async def test_resource_non_disclosure_is_ordered_constant_and_pre_handler() -> None:
    registry = CatalogRegistry(); handler_calls, guard_calls = [], []
    visible = {
        "smr1_" + "F" * 43: ("foreign", "human:viewer"),
        "smr1_" + "U" * 43: ("sprintctl", "human:other"),
        "smr1_" + "V" * 43: ("sprintctl", "human:viewer"),
    }
    def guard(resource_ref, context):
        guard_calls.append((resource_ref, context.repo_id, context.identity.actor))
        return visible.get(resource_ref) == (context.repo_id, context.identity.actor)
    def handler(arguments, context):
        handler_calls.append(arguments)
        return {"visible": True}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["resource_ref"], "properties": {"resource_ref": {"type": "string"}},
        "additionalProperties": False,
    }
    result_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "additionalProperties": True,
    }
    for name in ("work.maintenance.resource.get", "work.maintenance.resource.changes"):
        registry.register(
            OperationDefinition(
                name=name, owning_domain="work", input_schema=schema,
                result_schema=result_schema, required_authority="work:maintenance",
                execution_semantics="read", idempotency="not-allowed", repo_scoped=True,
            ), handler,
        )
    registry.register_resource_kind(ResourceKindDefinition(
        resource_kind="work.maintenance-capability",
        observation=ResourceObservationContract(
            snapshot_operation="work.maintenance.resource.get",
            changes_operation="work.maintenance.resource.changes",
            cursor_schema="sprintctl-maintenance-cursor/v1", supports_terminality=True,
        ),
    ))
    registry.register_resource_visibility(
        "work.maintenance-capability", guard,
        visibility_reference_pattern=r"^smr1_[A-Za-z0-9_-]{43}$",
    )
    resolver = StaticBearerIdentityResolver({
        "viewer": Identity(actor="human:viewer", environment="vuoro-dev", authorities=frozenset({"work:maintenance"}), repo_ids=frozenset({"sprintctl"})),
        "no-authority": Identity(actor="human:noauth", environment="vuoro-dev", authorities=frozenset(), repo_ids=frozenset({"sprintctl"})),
    })
    app = create_app(settings=ServiceSettings(environment_name="vuoro-dev", environment_class="development", compatibility_state="compatible"), registry=registry, identity_resolver=resolver)
    base = {
        "schema_version": "invocation/v1", "operation": "work.maintenance.resource.get",
        "catalog_revision": registry.revision, "idempotency_key": None,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied_authority = await client.post("/api/invoke/v1", headers={"Authorization": "Bearer no-authority", "X-Vuoro-Client-Protocol": "1"}, json=base | {"request_id": "auth", "repo_id": "sprintctl", "arguments": {"resource_ref": "smr1_" + "V" * 43}})
        denied_repo = await client.post("/api/invoke/v1", headers={"Authorization": "Bearer viewer", "X-Vuoro-Client-Protocol": "1"}, json=base | {"request_id": "repo", "repo_id": "foreign", "arguments": {"resource_ref": "smr1_" + "V" * 43}})
        assert denied_authority.status_code == denied_repo.status_code == 403
        assert guard_calls == []
        responses = []
        for label, reference in (
            ("malformed", "bad"), ("absent", "smr1_" + "A" * 43),
            ("foreign", "smr1_" + "F" * 43), ("unauthorized", "smr1_" + "U" * 43),
        ):
            response = await client.post("/api/invoke/v1", headers={"Authorization": "Bearer viewer", "X-Vuoro-Client-Protocol": "1"}, json=base | {"request_id": label, "repo_id": "sprintctl", "arguments": {"resource_ref": reference}})
            responses.append(response)
    expected = b'{"schema_version":"invocation-result/v1","request_id":"00000000-0000-0000-0000-000000000000","operation":"resource-observation","catalog_revision":"redacted","status":"rejected","result":null,"error":{"code":"resource_not_found","message":"resource not found"}}'
    assert all(response.status_code == 404 and response.content == expected for response in responses)
    assert all([(key.lower(), value) for key, value in response.headers.raw if key.lower() in (b"cache-control", b"content-type")] == [(b"cache-control", b"no-store"), (b"content-type", b"application/json")] for response in responses)
    assert handler_calls == []
    assert [entry[0] for entry in guard_calls] == ["smr1_" + "A" * 43, "smr1_" + "F" * 43, "smr1_" + "U" * 43]


@pytest.mark.anyio
async def test_invocation_derives_identity_and_enforces_contract() -> None:
    registry, app = configured_service()
    request = {
        "schema_version": "invocation/v1",
        "request_id": "request-7",
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
        assert accepted.json()["request_id"] == "request-7"
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
        "request_id": "invalid-adapter-result",
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
        "request_id": "domain-rejection",
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


@pytest.mark.anyio
async def test_invalid_invocation_body_uses_stable_result_envelope() -> None:
    registry, app = configured_service()
    valid = {
        "schema_version": "invocation/v1",
        "request_id": "spoof-attempt",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": registry.revision,
        "idempotency_key": "transition-7",
        "actor": "forged:administrator",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        extra_field = await client.post(
            "/api/invoke/v1",
            headers={"X-Vuoro-Client-Protocol": "1"},
            json=valid,
        )
        malformed = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Content-Type": "application/json",
            },
            content=b"{",
        )

    assert extra_field.status_code == 422
    assert extra_field.json() == {
        "schema_version": "invocation-result/v1",
        "request_id": "spoof-attempt",
        "operation": "work.pilot.transition",
        "catalog_revision": registry.revision,
        "status": "rejected",
        "result": None,
        "error": {
            "code": "invalid-invocation-envelope",
            "message": "invocation envelope is invalid",
        },
    }
    assert malformed.status_code == 422
    assert malformed.json()["schema_version"] == "invocation-result/v1"
    assert malformed.json()["request_id"] == "invalid-request"
    assert malformed.json()["error"]["code"] == "invalid-invocation-envelope"


@pytest.mark.anyio
async def test_invocation_context_contains_transport_and_idempotency_metadata() -> None:
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
                "request_id": "caller-request-id",
                "operation": "work.pilot.transition",
                "arguments": {"value": 8},
                "catalog_revision": registry.revision,
                "basis_revision": "work-basis-42",
                "idempotency_key": "transition-8",
            },
        )

    assert response.status_code == 200
    assert observed is not None
    assert observed.request_id == "caller-request-id"
    assert observed.basis_revision == "work-basis-42"
    assert observed.catalog_revision == registry.revision
    assert observed.idempotency_requirement == "required"
    assert observed.idempotency_key == "transition-8"


def _repo_scoped_service(handler=None) -> tuple[CatalogRegistry, object]:
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
            repo_scoped=True,
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
            "scoped-token": Identity(
                actor="human:developer",
                environment="vuoro-dev",
                authorities=frozenset({"work.transition"}),
                repo_ids=frozenset({"sprintctl"}),
            ),
            "wildcard-token": Identity(
                actor="human:operator",
                environment="vuoro-dev",
                authorities=frozenset({"work.transition"}),
                repo_ids=frozenset({"*"}),
            ),
        }
    )
    return registry, create_app(
        settings=settings, registry=registry, identity_resolver=resolver
    )


@pytest.mark.anyio
async def test_repo_scoped_operation_rejects_a_missing_repo_id() -> None:
    registry, app = _repo_scoped_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer scoped-token",
            },
            json={
                "schema_version": "invocation/v1",
                "request_id": "req-1",
                "operation": "work.pilot.transition",
                "arguments": {"value": 1},
                "catalog_revision": registry.revision,
                "idempotency_key": "req-1",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "repo-id-required"


@pytest.mark.anyio
async def test_repo_scoped_operation_rejects_an_unauthorized_repo_id() -> None:
    registry, app = _repo_scoped_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer scoped-token",
            },
            json={
                "schema_version": "invocation/v1",
                "request_id": "req-2",
                "operation": "work.pilot.transition",
                "arguments": {"value": 1},
                "catalog_revision": registry.revision,
                "idempotency_key": "req-2",
                "repo_id": "some-other-repo",
            },
        )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "repo-unauthorized"


@pytest.mark.anyio
async def test_repo_scoped_operation_passes_repo_id_through_to_the_handler() -> None:
    observed = None

    def handler(arguments, context):
        nonlocal observed
        observed = context.repo_id
        return {"accepted": arguments["value"]}

    registry, app = _repo_scoped_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer scoped-token",
            },
            json={
                "schema_version": "invocation/v1",
                "request_id": "req-3",
                "operation": "work.pilot.transition",
                "arguments": {"value": 1},
                "catalog_revision": registry.revision,
                "idempotency_key": "req-3",
                "repo_id": "sprintctl",
            },
        )
    assert response.status_code == 200
    assert observed == "sprintctl"


@pytest.mark.anyio
async def test_repo_scoped_operation_honors_the_wildcard_authorization() -> None:
    observed = None

    def handler(arguments, context):
        nonlocal observed
        observed = context.repo_id
        return {"accepted": arguments["value"]}

    registry, app = _repo_scoped_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer wildcard-token",
            },
            json={
                "schema_version": "invocation/v1",
                "request_id": "req-4",
                "operation": "work.pilot.transition",
                "arguments": {"value": 1},
                "catalog_revision": registry.revision,
                "idempotency_key": "req-4",
                "repo_id": "any-repo-at-all",
            },
        )
    assert response.status_code == 200
    assert observed == "any-repo-at-all"


@pytest.mark.anyio
async def test_handshake_advertises_the_configured_invocation_schema_versions() -> None:
    _, app = configured_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        handshake = (await client.get("/api/meta/v1/handshake")).json()
    assert handshake["invocation_schema_versions"] == ["invocation/v1"]
    assert handshake["schema_versions"]["invocation"] == "invocation/v1"


@pytest.mark.anyio
async def test_unexpected_handler_exception_fails_closed_without_leaking_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drives the ``LOGGER.exception(...)`` path in ``app.py``'s ``_dispatch``.

    ``logging.Logger.exception`` captures a full traceback by default, so a
    handler that stringifies the whole invocation context while crashing is
    the highest-value adversarial case: neither the traceback text nor the
    error envelope may carry the invocation's own data back out.
    """

    sentinel = 424_242

    def handler(arguments, context):
        raise Exception(f"handler exploded while holding context={context!r}")

    registry, app = configured_service(handler)
    caplog.set_level(logging.DEBUG, logger="vuoro_service")
    request = {
        "schema_version": "invocation/v1",
        "request_id": "handler-failure",
        "operation": "work.pilot.transition",
        "arguments": {"value": sentinel},
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
    assert response.json()["error"]["code"] == "operation-handler-failed"
    assert str(sentinel) not in response.text
    assert any(record.exc_info for record in caplog.records), (
        "expected LOGGER.exception to have fired with exc_info attached"
    )
