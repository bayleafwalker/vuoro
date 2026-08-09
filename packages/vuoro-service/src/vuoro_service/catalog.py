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
ResultDecoder = Callable[[Any], Any]
VisibilityGuard = Callable[[str, InvocationContext], bool | Awaitable[bool]]


class ResourceNotFoundDisclosure(RuntimeError):
    """Constant, non-correlating resource-observation rejection."""


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
    result_decoder: ResultDecoder | None = None
    visibility_guard: VisibilityGuard | None = None
    visibility_reference_pattern: re.Pattern[str] | None = None


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
        self, definition: OperationDefinition, handler: OperationHandler,
        *, result_decoder: ResultDecoder | None = None,
        visibility_guard: VisibilityGuard | None = None,
        visibility_reference_pattern: str | None = None,
    ) -> None:
        if result_decoder is not None and not callable(result_decoder):
            raise CatalogRegistrationError(
                f"{definition.name}: result_decoder must be callable"
            )
        if definition.name in self._operations:
            raise CatalogRegistrationError(
                f"duplicate operation name: {definition.name}"
            )
        disclosed = definition.failure_disclosure == "resource-not-found/v1"
        if (
            disclosed != (visibility_guard is not None)
            or disclosed != (visibility_reference_pattern is not None)
        ):
            raise CatalogRegistrationError(
                f"{definition.name}: resource-not-found disclosure requires exactly one visibility guard"
            )
        if disclosed and (
            definition.execution_semantics != "read"
            or visibility_reference_pattern is None
        ):
            raise CatalogRegistrationError(
                f"{definition.name}: resource disclosure requires a read operation and opaque-reference grammar"
            )
        try:
            compiled_visibility_pattern = (
                re.compile(visibility_reference_pattern)
                if visibility_reference_pattern is not None else None
            )
        except re.error as error:
            raise CatalogRegistrationError(
                f"{definition.name}: invalid opaque-reference grammar"
            ) from error
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
            registered_definition, handler, result_decoder,
            visibility_guard, compiled_visibility_pattern,
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

    def has_resource_kind(self, resource_kind: str) -> bool:
        """Report whether an immutable owner resource descriptor is registered."""

        return resource_kind in self._resource_kinds

    def register_resource_visibility(
        self,
        resource_kind: str,
        visibility_guard: VisibilityGuard,
        *,
        visibility_reference_pattern: str,
    ) -> None:
        """Bind uniform non-disclosure to both observations of one owner resource.

        Older owner adapters can register the frozen transport descriptors without
        knowing about this service-side disclosure seam.  Binding remains explicit,
        immutable, and scoped to the named resource kind.
        """

        definition = self._resource_kinds.get(resource_kind)
        if definition is None:
            raise CatalogRegistrationError(
                f"unregistered resource kind: {resource_kind}"
            )
        if not callable(visibility_guard):
            raise CatalogRegistrationError(
                f"{resource_kind}: visibility_guard must be callable"
            )
        try:
            compiled_pattern = re.compile(visibility_reference_pattern)
        except re.error as error:
            raise CatalogRegistrationError(
                f"{resource_kind}: invalid opaque-reference grammar"
            ) from error

        operation_names = (
            definition.observation.snapshot_operation,
            definition.observation.changes_operation,
        )
        for operation_name in operation_names:
            operation = self._operations[operation_name]
            if (
                operation.visibility_guard is not None
                or operation.definition.failure_disclosure is not None
            ):
                raise CatalogRegistrationError(
                    f"{resource_kind}: visibility is already bound"
                )

        for operation_name in operation_names:
            operation = self._operations[operation_name]
            disclosed_definition = operation.definition.model_copy(
                deep=True,
                update={"failure_disclosure": "resource-not-found/v1"},
            )
            self._operations[operation_name] = RegisteredOperation(
                disclosed_definition,
                operation.handler,
                operation.result_decoder,
                visibility_guard,
                compiled_pattern,
            )

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

    def _validate_failure_disclosures(self) -> None:
        observation_operations = {
            operation_name
            for definition in self._resource_kinds.values()
            for operation_name in (
                definition.observation.snapshot_operation,
                definition.observation.changes_operation,
            )
        }
        disclosed = {
            name for name, operation in self._operations.items()
            if operation.definition.failure_disclosure is not None
        }
        if disclosed - observation_operations:
            raise CatalogRegistrationError(
                "resource-not-found disclosure is legal only on registered resource observation operations"
            )

    @property
    def revision(self) -> str:
        self._validate_failure_disclosures()
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
            operation.definition.model_copy(deep=True), operation.handler,
            operation.result_decoder, operation.visibility_guard,
            operation.visibility_reference_pattern,
        )

    async def invoke(
        self,
        operation: RegisteredOperation,
        arguments: Any,
        context: InvocationContext,
    ) -> Any:
        if operation.visibility_guard is not None:
            resource_ref = (
                arguments.get("resource_ref") if isinstance(arguments, dict) else None
            )
            pattern = operation.visibility_reference_pattern
            if (
                not isinstance(resource_ref, str)
                or pattern is None
                or pattern.fullmatch(resource_ref) is None
            ):
                raise ResourceNotFoundDisclosure
            visible = operation.visibility_guard(resource_ref, context)
            if inspect.isawaitable(visible):
                visible = await visible
            if visible is not True:
                raise ResourceNotFoundDisclosure
        try:
            Draft202012Validator(operation.definition.input_schema).validate(arguments)
        except ValidationError as error:
            raise InvocationInputValidationError(error.message) from error
        result = operation.handler(arguments, context)
        if inspect.isawaitable(result):
            result = await result
        if operation.result_decoder is not None:
            result = operation.result_decoder(result)
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
    "ResourceNotFoundDisclosure",
    "ResultDecoder",
    "VisibilityGuard",
    "SCHEMA_DIALECT",
]
