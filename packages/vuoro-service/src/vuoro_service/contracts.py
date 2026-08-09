"""Protocol-v1 service contracts shared by the HTTP shell and catalog."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImmutableStrictModel(StrictModel):
    """Catalog metadata that cannot be changed after registration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


_TRANSIENT_CREDENTIAL_KEY = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_TRANSIENT_CREDENTIALS = 8


class ClientProtocolRange(StrictModel):
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)


class EnvironmentMetadata(StrictModel):
    name: str = Field(min_length=1)
    environment_class: Literal["local", "development", "production", "recovery"]
    constraints: list[str] = Field(default_factory=list)
    runbook_refs: list[str] = Field(default_factory=list)


class DomainCompatibility(StrictModel):
    api_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    state: Literal["compatible", "incompatible", "unavailable"]
    reason: str | None = None


class CompatibilityState(StrictModel):
    state: Literal["compatible", "degraded", "incompatible"]
    domains: dict[str, DomainCompatibility] = Field(default_factory=dict)


class HandshakeResponse(StrictModel):
    schema_version: Literal["handshake/v1"] = "handshake/v1"
    environment: EnvironmentMetadata
    service_version: str
    api_versions: dict[str, str]
    schema_versions: dict[str, str]
    invocation_schema_versions: list[str] = Field(
        default_factory=lambda: ["invocation/v1"]
    )
    client_protocol: ClientProtocolRange
    catalog_revision: str
    compatibility: CompatibilityState


class DeprecationMetadata(StrictModel):
    deprecated: bool = False
    replacement: str | None = None
    sunset_at: str | None = None


class ResourceResultContract(ImmutableStrictModel):
    mode: Literal["resource-reference"] = "resource-reference"
    resource_kind: str = Field(
        pattern=r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$"
    )


class ResourceObservationContract(ImmutableStrictModel):
    snapshot_operation: str = Field(
        pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*){2,}$"
    )
    changes_operation: str = Field(
        pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*){2,}$"
    )
    cursor_schema: str = Field(min_length=1, max_length=128)
    supports_terminality: bool


class ResourceKindDefinition(ImmutableStrictModel):
    resource_kind: str = Field(
        pattern=r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$"
    )
    observation: ResourceObservationContract


class BoundedLongPollCapability(ImmutableStrictModel):
    transport: Literal["bounded-long-poll"] = "bounded-long-poll"
    maximum_wait_seconds: int = Field(ge=1, le=300)


class OperationDefinition(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*){2,}$")
    owning_domain: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    input_schema: dict[str, Any]
    result_schema: dict[str, Any]
    required_authority: str | None = None
    execution_semantics: Literal["read", "write", "enqueue", "admin"]
    idempotency: Literal["not-allowed", "optional", "required"]
    # True for an operation whose result depends on which repository it
    # targets (e.g. sprintctl's work.* catalog): the caller must supply
    # repo_id in the invocation envelope, and the identity must authorize it
    # (Identity.authorizes_repo). False for operations with no repository
    # concept at all (execution/knowledge/audit domains today).
    repo_scoped: bool = False
    deprecation: DeprecationMetadata = Field(default_factory=DeprecationMetadata)
    required_client_schema_features: list[str] = Field(
        default_factory=lambda: ["json-schema-draft-2020-12"]
    )
    result_contract: ResourceResultContract | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class CatalogResponse(StrictModel):
    schema_version: Literal["operation-catalog/v1"] = "operation-catalog/v1"
    revision: str
    operations: list[OperationDefinition]
    resource_kinds: tuple[ResourceKindDefinition, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    observation_transports: tuple[BoundedLongPollCapability, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class _InvocationFields(StrictModel):
    request_id: str = Field(min_length=1, max_length=256)
    operation: str = Field(min_length=1)
    arguments: Any
    catalog_revision: str | None = None
    basis_revision: str | None = Field(default=None, min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    repo_id: str | None = Field(default=None, min_length=1, max_length=256)


class InvocationRequest(_InvocationFields):
    schema_version: Literal["invocation/v1"] = "invocation/v1"


class InvocationRequestV2(_InvocationFields):
    """Additive invocation envelope carrying transient, out-of-band credentials.

    ``transient_credentials`` is a transport facility, not a work-domain
    argument: keys are non-secret ``sha256:<64-lowercase-hex>`` references and
    values are the proof strings they resolve to. The service passes bindings
    through the in-memory invocation context only; they are never persisted,
    cataloged, or logged.
    """

    schema_version: Literal["invocation/v2"] = "invocation/v2"
    transient_credentials: dict[str, str] = Field(default_factory=dict)

    @field_validator("transient_credentials")
    @classmethod
    def _validate_transient_credentials(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        if len(value) > MAX_TRANSIENT_CREDENTIALS:
            raise ValueError(
                "transient_credentials accepts at most "
                f"{MAX_TRANSIENT_CREDENTIALS} bindings"
            )
        for key, binding_value in value.items():
            if not _TRANSIENT_CREDENTIAL_KEY.fullmatch(key):
                raise ValueError(
                    "transient_credentials key must match "
                    f"sha256:<64-lowercase-hex>: {key!r}"
                )
            if not isinstance(binding_value, str) or not binding_value:
                raise ValueError(
                    f"transient_credentials value for {key!r} must be a non-empty string"
                )
        return value


class InvocationError(StrictModel):
    code: str
    message: str


class InvocationResponse(StrictModel):
    schema_version: Literal["invocation-result/v1"] = "invocation-result/v1"
    request_id: str
    operation: str
    catalog_revision: str
    status: Literal["accepted", "rejected", "error"]
    result: Any | None = None
    error: InvocationError | None = None
