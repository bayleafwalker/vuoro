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

from vuoro_service.contracts import CatalogResponse, OperationDefinition
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
        self._operations[definition.name] = RegisteredOperation(definition, handler)

    @property
    def revision(self) -> str:
        canonical = [
            operation.definition.model_dump(mode="json")
            for operation in sorted(
                self._operations.values(), key=lambda value: value.definition.name
            )
        ]
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def catalog(self) -> CatalogResponse:
        return CatalogResponse(
            revision=self.revision,
            operations=[
                operation.definition
                for operation in sorted(
                    self._operations.values(), key=lambda value: value.definition.name
                )
            ],
        )

    def get(self, name: str) -> RegisteredOperation | None:
        return self._operations.get(name)

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
