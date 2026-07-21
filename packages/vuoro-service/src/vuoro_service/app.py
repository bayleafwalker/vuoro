"""FastAPI application factory for the protocol-v1 service shell."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import inspect
import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse, Response

from vuoro_service import __version__
from vuoro_service.catalog import (
    CatalogRegistry,
    InvocationInputValidationError,
    InvocationResultValidationError,
    OperationRejectedError,
)
from vuoro_service.contracts import (
    ClientProtocolRange,
    CompatibilityState,
    DomainCompatibility,
    EnvironmentMetadata,
    HandshakeResponse,
    InvocationError,
    InvocationRequest,
    InvocationResponse,
)
from vuoro_service.identity import (
    Identity,
    IdentityResolutionError,
    IdentityResolver,
    InvocationContext,
    deny_all_identities,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceSettings:
    environment_name: str = "unconfigured"
    environment_class: Literal["local", "development", "production", "recovery"] = (
        "local"
    )
    api_versions: Mapping[str, str] = field(
        default_factory=lambda: {"meta": "v1", "catalog": "v1", "invoke": "v1"}
    )
    schema_versions: Mapping[str, str] = field(
        default_factory=lambda: {
            "handshake": "handshake/v1",
            "catalog": "operation-catalog/v1",
            "invocation": "invocation/v1",
        }
    )
    domains: Mapping[str, DomainCompatibility] = field(default_factory=dict)
    compatibility_state: Literal["compatible", "degraded", "incompatible"] = "degraded"
    client_protocol_minimum: int = 1
    client_protocol_maximum: int = 1


def _protocol_version(request: Request) -> int | None:
    raw = request.headers.get("x-vuoro-client-protocol")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return -1


def _protocol_supported(settings: ServiceSettings, request: Request) -> bool:
    version = _protocol_version(request)
    return (
        version is not None
        and settings.client_protocol_minimum
        <= version
        <= settings.client_protocol_maximum
    )


def _invocation_response(
    *,
    request_id: str,
    operation: str,
    revision: str,
    status: Literal["accepted", "rejected", "error"],
    result: object | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    http_status: int = 200,
) -> JSONResponse:
    envelope = InvocationResponse(
        request_id=request_id,
        operation=operation,
        catalog_revision=revision,
        status=status,
        result=result,
        error=(
            InvocationError(code=error_code, message=error_message or error_code)
            if error_code
            else None
        ),
    )
    return JSONResponse(envelope.model_dump(mode="json"), status_code=http_status)


def create_app(
    *,
    settings: ServiceSettings | None = None,
    registry: CatalogRegistry | None = None,
    identity_resolver: IdentityResolver = deny_all_identities,
) -> FastAPI:
    settings = settings or ServiceSettings()
    registry = registry or CatalogRegistry()
    app = FastAPI(title="Vuoro service", version=__version__)
    app.state.settings = settings
    app.state.registry = registry

    @app.exception_handler(RequestValidationError)
    async def invalid_request_envelope(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path != "/api/invoke/v1":
            return await request_validation_exception_handler(request, error)
        body = error.body if isinstance(error.body, dict) else {}
        request_id = body.get("request_id")
        operation = body.get("operation")
        return _invocation_response(
            request_id=(
                request_id
                if isinstance(request_id, str) and request_id
                else "invalid-request"
            ),
            operation=(
                operation
                if isinstance(operation, str) and operation
                else "invalid-operation"
            ),
            revision=registry.revision,
            status="rejected",
            error_code="invalid-invocation-envelope",
            error_message="invocation envelope is invalid",
            http_status=422,
        )

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, object]:
        return {
            "status": "ready"
            if settings.compatibility_state != "incompatible"
            else "not-ready",
            "compatibility": settings.compatibility_state,
        }

    @app.get("/api/meta/v1/handshake", response_model=HandshakeResponse)
    async def handshake() -> HandshakeResponse:
        return HandshakeResponse(
            environment=EnvironmentMetadata(
                name=settings.environment_name,
                environment_class=settings.environment_class,
            ),
            service_version=__version__,
            api_versions=dict(settings.api_versions),
            schema_versions=dict(settings.schema_versions),
            client_protocol=ClientProtocolRange(
                minimum=settings.client_protocol_minimum,
                maximum=settings.client_protocol_maximum,
            ),
            catalog_revision=registry.revision,
            compatibility=CompatibilityState(
                state=settings.compatibility_state,
                domains=dict(settings.domains),
            ),
        )

    @app.get("/api/catalog/v1")
    async def catalog(request: Request) -> Response:
        if not _protocol_supported(settings, request):
            return JSONResponse(
                {
                    "error": {
                        "code": "client-protocol-incompatible",
                        "message": "client protocol is outside the supported range",
                        "supported": {
                            "minimum": settings.client_protocol_minimum,
                            "maximum": settings.client_protocol_maximum,
                        },
                    }
                },
                status_code=426,
            )
        etag = f'"{registry.revision}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(
            registry.catalog().model_dump(mode="json"),
            headers={"ETag": etag},
        )

    @app.post("/api/invoke/v1")
    async def invoke(request: Request, invocation: InvocationRequest) -> JSONResponse:
        request_id = invocation.request_id
        revision = registry.revision
        if not _protocol_supported(settings, request):
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="client-protocol-incompatible",
                error_message="client protocol is outside the supported range",
                http_status=426,
            )
        if invocation.catalog_revision and invocation.catalog_revision != revision:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="stale-catalog",
                error_message="catalog revision changed; rediscover before retrying",
                http_status=409,
            )
        operation = registry.get(invocation.operation)
        if operation is None:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="unknown-operation",
                error_message="operation is not present in the active catalog",
                http_status=404,
            )
        try:
            identity = identity_resolver(request)
            if inspect.isawaitable(identity):
                identity = await identity
        except IdentityResolutionError as error:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="identity-required",
                error_message=str(error),
                http_status=401,
            )
        assert isinstance(identity, Identity)
        if identity.environment != settings.environment_name:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="environment-mismatch",
                error_message="identity is not bound to this deployment environment",
                http_status=403,
            )
        authority = operation.definition.required_authority
        if authority and authority not in identity.authorities:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="authority-required",
                error_message="identity lacks the operation authority",
                http_status=403,
            )
        if (
            operation.definition.idempotency == "required"
            and not invocation.idempotency_key
        ):
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="idempotency-key-required",
                error_message="operation requires an idempotency key",
                http_status=400,
            )
        if (
            operation.definition.idempotency == "not-allowed"
            and invocation.idempotency_key
        ):
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="idempotency-key-not-allowed",
                error_message="operation does not accept an idempotency key",
                http_status=400,
            )
        try:
            result = await registry.invoke(
                operation,
                invocation.arguments,
                InvocationContext(
                    identity=identity,
                    request_id=request_id,
                    basis_revision=invocation.basis_revision,
                    catalog_revision=revision,
                    idempotency_requirement=operation.definition.idempotency,
                    idempotency_key=invocation.idempotency_key,
                ),
            )
        except InvocationInputValidationError as error:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code="schema-validation-failed",
                error_message=str(error),
                http_status=422,
            )
        except OperationRejectedError as error:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="rejected",
                error_code=error.code,
                error_message=str(error),
                http_status=error.http_status,
            )
        except InvocationResultValidationError:
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="error",
                error_code="adapter-result-invalid",
                error_message="operation returned a result that violates its catalog schema",
                http_status=500,
            )
        except Exception:
            LOGGER.exception(
                "Vuoro operation handler failed",
                extra={"operation": invocation.operation, "request_id": request_id},
            )
            return _invocation_response(
                request_id=request_id,
                operation=invocation.operation,
                revision=revision,
                status="error",
                error_code="operation-handler-failed",
                error_message="operation handler failed",
                http_status=500,
            )
        return _invocation_response(
            request_id=request_id,
            operation=invocation.operation,
            revision=revision,
            status="accepted",
            result=result,
        )

    return app
