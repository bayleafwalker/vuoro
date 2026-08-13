"""Common catalog and JSON-Schema construction primitives.

The registry is a Protocol on purpose: adapters can register into Vuoro's
service registry, a small owner-side test double, or another compatible
runtime without importing the service package.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_FEATURES = ("json-schema-draft-2020-12",)


class CatalogRegistry(Protocol):
    """Structural registry surface required by adapter registration."""

    def register(self, definition: Any, handler: Callable[..., Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class Definition:
    """Immutable, driver-neutral operation definition.

    ``as_dict`` emits the wire-compatible shape consumed by the Vuoro service
    while keeping the kit free of Pydantic and service imports.
    """

    name: str
    owning_domain: str
    input_schema: dict[str, Any]
    result_schema: dict[str, Any]
    required_authority: str | None
    execution_semantics: str
    idempotency: str
    repo_scoped: bool = False
    required_client_schema_features: tuple[str, ...] = SCHEMA_FEATURES
    deprecation: Mapping[str, Any] | None = None
    result_contract: Mapping[str, Any] | None = None
    failure_disclosure: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "owning_domain": self.owning_domain,
            "input_schema": self.input_schema,
            "result_schema": self.result_schema,
            "required_authority": self.required_authority,
            "execution_semantics": self.execution_semantics,
            "idempotency": self.idempotency,
            "repo_scoped": self.repo_scoped,
            "deprecation": dict(self.deprecation or {
                "deprecated": False, "replacement": None, "sunset_at": None
            }),
            "required_client_schema_features": list(self.required_client_schema_features),
        }
        if self.result_contract is not None:
            result["result_contract"] = dict(self.result_contract)
        if self.failure_disclosure is not None:
            result["failure_disclosure"] = self.failure_disclosure
        return result


@dataclass(frozen=True, slots=True)
class AdapterOperation:
    """An operation definition paired with its owner handler."""

    definition: Any
    handler: Callable[..., Any]


OperationDefinition = Definition
CatalogOperation = AdapterOperation


def object_schema(
    properties: Mapping[str, Any],
    *,
    required: Iterable[str] = (),
    additional_properties: bool = False,
    additional: bool | None = None,
    definitions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the strict object-schema skeleton used by all adapters.

    ``additional`` is accepted as the historical spelling used by ActionQ;
    ``additional_properties`` is the descriptive spelling used by Auditctl.
    ``definitions`` is emitted as ``$defs`` for local references.
    """

    if additional is not None:
        additional_properties = additional
    schema: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": additional_properties,
    }
    required_values = tuple(required)
    if required_values:
        schema["required"] = list(required_values)
    if definitions:
        schema["$defs"] = dict(definitions)
    return schema


# Existing adapters use both private helper spellings. Public aliases make a
# future owner migration mechanical without forcing a wire-contract change.
_object_schema = object_schema
_object = object_schema
build_object_schema = object_schema


def definition(
    name: str,
    *,
    owning_domain: str | None = None,
    input_schema: dict[str, Any],
    result_schema: dict[str, Any],
    required_authority: str | None = None,
    authority: str | None = None,
    execution_semantics: str | None = None,
    semantics: str | None = None,
    idempotency: str,
    repo_scoped: bool = False,
    required_client_schema_features: Iterable[str] = SCHEMA_FEATURES,
    deprecation: Mapping[str, Any] | None = None,
    result_contract: Mapping[str, Any] | None = None,
    failure_disclosure: str | None = None,
    handler_name: str | None = None,
) -> dict[str, Any] | Definition:
    """Create a wire-compatible operation definition.

    By default this returns a dict, matching the historical ActionQ, Kctl,
    and Auditctl helpers. ``owning_domain`` may be omitted when callers only
    need the metadata dict and can be inferred by their service boundary.
    """

    domain = owning_domain or (name.split(".", 1)[0] if "." in name else "")
    authority_value = required_authority if required_authority is not None else authority
    semantics_value = execution_semantics if execution_semantics is not None else semantics
    if not domain:
        raise ValueError("owning_domain is required for an operation without a domain prefix")
    if not semantics_value:
        raise ValueError("execution_semantics is required")
    result: dict[str, Any] = {
        "name": name,
        "owning_domain": domain,
        "input_schema": input_schema,
        "result_schema": result_schema,
        "required_authority": authority_value,
        "execution_semantics": semantics_value,
        "idempotency": idempotency,
        "repo_scoped": repo_scoped,
        "deprecation": dict(deprecation or {
            "deprecated": False, "replacement": None, "sunset_at": None
        }),
        "required_client_schema_features": list(required_client_schema_features),
    }
    if result_contract is not None:
        result["result_contract"] = dict(result_contract)
    if failure_disclosure is not None:
        result["failure_disclosure"] = failure_disclosure
    if handler_name is not None:
        result["_handler_name"] = handler_name
    return result


_operation = definition
_definition = definition
build_operation_definition = definition
operation_definition = definition


def operation(definition_value: Any, handler: Callable[..., Any]) -> AdapterOperation:
    return AdapterOperation(definition_value, handler)


def register(
    registry: CatalogRegistry,
    definition_value: Any,
    handler: Callable[..., Any],
) -> None:
    """Register one operation using only the structural registry protocol."""

    registry.register(definition_value, handler)


def register_operations(
    registry: CatalogRegistry,
    operations: Iterable[AdapterOperation | tuple[Any, Callable[..., Any]]],
) -> None:
    """Register a sequence of operation/handler pairs in declared order."""

    for item in operations:
        if isinstance(item, AdapterOperation):
            register(registry, item.definition, item.handler)
        else:
            definition_value, handler = item
            register(registry, definition_value, handler)


register_catalog = register_operations
