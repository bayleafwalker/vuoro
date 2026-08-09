"""Deterministic operation registry for the protocol-v1 catalog."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import inspect
import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from vuoro_service.contracts import (
    BoundedLongPollCapability,
    CatalogResponse,
    OperationDefinition,
    ResourceKindDefinition,
)
from vuoro_service.identity import InvocationContext


DEFAULT_SCHEMA_FEATURES = frozenset(
    {
        "json-schema-draft-2020-12",
        "local-defs-ref",
    }
)
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_SAFE_REF = re.compile(r"^#/\$defs/(?:[^~/]|~0|~1)+(?:/(?:[^~/]|~0|~1)+)*$")
_FEATURE_KEYWORDS = {
    "$ref": "local-defs-ref",
    "unevaluatedItems": "unevaluated-properties",
    "unevaluatedProperties": "unevaluated-properties",
}

OperationHandler = Callable[[Any, InvocationContext], Any | Awaitable[Any]]


class CatalogRegistrationError(ValueError):
    """Raised when an operation cannot safely enter the catalog."""


class InvocationInputValidationError(ValueError):
    """Raised when caller-supplied arguments do not match the operation schema."""


class InvocationResultValidationError(RuntimeError):
    """Raised when an adapter violates its declared result schema."""


class OperationRejectedError(RuntimeError):
    """Intentional domain rejection returned through the invocation envelope."""

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class RegisteredOperation:
    definition: OperationDefinition
    handler: OperationHandler


def _validate_references(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "$dynamicRef":
                raise CatalogRegistrationError(
                    f"{child_path}: dynamic references are not supported"
                )
            if key == "$ref" and (
                not isinstance(child, str) or not _SAFE_REF.fullmatch(child)
            ):
                raise CatalogRegistrationError(
                    f"{child_path}: only local #/$defs/... references are allowed"
                )
            _validate_references(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_references(child, f"{path}[{index}]")


def _validate_local_ref_targets(value: Any, root: dict[str, Any], label: str) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            target: Any = root
            for encoded_segment in reference[2:].split("/"):
                segment = encoded_segment.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or segment not in target:
                    raise CatalogRegistrationError(
                        f"{label}: local reference target does not exist: {reference}"
                    )
                target = target[segment]
        for child in value.values():
            _validate_local_ref_targets(child, root, label)
    elif isinstance(value, list):
        for child in value:
            _validate_local_ref_targets(child, root, label)


def _required_schema_features(value: Any) -> set[str]:
    required = {"json-schema-draft-2020-12"}
    if isinstance(value, dict):
        for key, child in value.items():
            feature = _FEATURE_KEYWORDS.get(key)
            if feature is not None:
                required.add(feature)
            required.update(_required_schema_features(child))
    elif isinstance(value, list):
        for child in value:
            required.update(_required_schema_features(child))
    return required


def validate_schema(schema: dict[str, Any], label: str) -> set[str]:
    if schema.get("$schema") != SCHEMA_DIALECT:
        raise CatalogRegistrationError(f"{label}: $schema must be {SCHEMA_DIALECT}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise CatalogRegistrationError(
            f"{label}: invalid JSON Schema: {error.message}"
        ) from error
    _validate_references(schema, label)
    _validate_local_ref_targets(schema, schema, label)
    return _required_schema_features(schema)


class CatalogRegistry:
    def __init__(
        self, *, schema_features: frozenset[str] = DEFAULT_SCHEMA_FEATURES
    ) -> None:
        self.schema_features = schema_features
        self._operations: dict[str, RegisteredOperation] = {}
        self._resource_kinds: dict[str, ResourceKindDefinition] = {}
        self._observation_transports: dict[str, BoundedLongPollCapability] = {}

    def register(
        self, definition: OperationDefinition, handler: OperationHandler
    ) -> None:
        if definition.name in self._operations:
            raise CatalogRegistrationError(
                f"duplicate operation name: {definition.name}"
            )
        if definition.name.split(".", 1)[0] != definition.owning_domain:
            raise CatalogRegistrationError(
                f"{definition.name}: owning_domain must match the operation-name prefix"
            )
        required_features = validate_schema(
            definition.input_schema, f"{definition.name}.input_schema"
        ) | validate_schema(
            definition.result_schema, f"{definition.name}.result_schema"
        )
        declared_features = set(definition.required_client_schema_features)
        undeclared = sorted(required_features - declared_features)
        if undeclared:
            raise CatalogRegistrationError(
                f"{definition.name}: schemas use undeclared client features: {undeclared}"
            )
        unsupported = sorted(declared_features - self.schema_features)
        if unsupported:
            raise CatalogRegistrationError(
                f"{definition.name}: service does not support declared schema features: {unsupported}"
            )
        if definition.result_contract is not None:
            resource_kind = definition.result_contract.resource_kind
            if resource_kind.split(".", 1)[0] != definition.owning_domain:
                raise CatalogRegistrationError(
                    f"{definition.name}: result_contract resource kind must be owned by "
                    f"{definition.owning_domain}"
                )
            if resource_kind not in self._resource_kinds:
                raise CatalogRegistrationError(
                    f"{definition.name}: result_contract references unregistered "
                    f"resource kind: {resource_kind}"
                )
        # Adapter definitions are caller-owned Pydantic objects. Keep an isolated
        # snapshot so later mutation cannot invalidate registration checks or
        # silently change catalog bytes and revisions.
        registered_definition = definition.model_copy(deep=True)
        self._operations[definition.name] = RegisteredOperation(
            registered_definition, handler
        )

    def register_resource_kind(self, definition: ResourceKindDefinition) -> None:
        """Register immutable owner metadata after its observation operations."""

        kind = definition.resource_kind
        if kind in self._resource_kinds:
            raise CatalogRegistrationError(f"duplicate resource kind: {kind}")
        domain = kind.split(".", 1)[0]
        observation = definition.observation
        if observation.snapshot_operation == observation.changes_operation:
            raise CatalogRegistrationError(
                f"{kind}: snapshot_operation and changes_operation must differ"
            )
        for label, operation_name in (
            ("snapshot_operation", observation.snapshot_operation),
            ("changes_operation", observation.changes_operation),
        ):
            operation = self._operations.get(operation_name)
            if operation is None:
                raise CatalogRegistrationError(
                    f"{kind}: {label} references unregistered operation: {operation_name}"
                )
            if operation.definition.owning_domain != domain:
                raise CatalogRegistrationError(
                    f"{kind}: {label} must be owned by {domain}"
                )
            if operation.definition.execution_semantics != "read":
                raise CatalogRegistrationError(
                    f"{kind}: {label} must reference a read operation"
                )
        self._resource_kinds[kind] = definition

    def register_observation_transport(
        self, capability: BoundedLongPollCapability
    ) -> None:
        if capability.transport in self._observation_transports:
            raise CatalogRegistrationError(
                f"duplicate observation transport: {capability.transport}"
            )
        self._observation_transports[capability.transport] = capability

    def _canonical_operations(self) -> list[dict[str, Any]]:
        return [
            operation.definition.model_dump(mode="json")
            for operation in sorted(
                self._operations.values(), key=lambda value: value.definition.name
            )
        ]

    @property
    def revision(self) -> str:
        canonical: Any = self._canonical_operations()
        if self._resource_kinds or self._observation_transports:
            canonical = {
                "operations": canonical,
                "resource_kinds": [
                    definition.model_dump(mode="json")
                    for definition in sorted(
                        self._resource_kinds.values(),
                        key=lambda value: value.resource_kind,
                    )
                ],
                "observation_transports": [
                    capability.model_dump(mode="json")
                    for capability in sorted(
                        self._observation_transports.values(),
                        key=lambda value: value.transport,
                    )
                ],
            }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def catalog(self) -> CatalogResponse:
        return CatalogResponse(
            revision=self.revision,
            operations=[
                operation.definition.model_copy(deep=True)
                for operation in sorted(
                    self._operations.values(), key=lambda value: value.definition.name
                )
            ],
            resource_kinds=(
                tuple(
                    sorted(
                        self._resource_kinds.values(),
                        key=lambda value: value.resource_kind,
                    )
                )
                if self._resource_kinds
                else None
            ),
            observation_transports=(
                tuple(
                    sorted(
                        self._observation_transports.values(),
                        key=lambda value: value.transport,
                    )
                )
                if self._observation_transports
                else None
            ),
        )

    def get(self, name: str) -> RegisteredOperation | None:
        operation = self._operations.get(name)
        if operation is None:
            return None
        return RegisteredOperation(
            operation.definition.model_copy(deep=True), operation.handler
        )

    async def invoke(
        self,
        operation: RegisteredOperation,
        arguments: Any,
        context: InvocationContext,
    ) -> Any:
        try:
            Draft202012Validator(operation.definition.input_schema).validate(arguments)
        except ValidationError as error:
            raise InvocationInputValidationError(error.message) from error
        result = operation.handler(arguments, context)
        if inspect.isawaitable(result):
            result = await result
        try:
            Draft202012Validator(operation.definition.result_schema).validate(result)
        except ValidationError as error:
            raise InvocationResultValidationError(error.message) from error
        return result


__all__ = [
    "CatalogRegistrationError",
    "CatalogRegistry",
    "DEFAULT_SCHEMA_FEATURES",
    "InvocationInputValidationError",
    "InvocationResultValidationError",
    "OperationRejectedError",
    "SCHEMA_DIALECT",
]
