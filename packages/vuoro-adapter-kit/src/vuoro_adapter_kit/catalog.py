"""Pure JSON-Schema and operation-spec builders for Vuoro adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
import re
from typing import Any, Protocol


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_FEATURES = ("json-schema-draft-2020-12",)
_NAME = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*){2,}$")
_DOMAIN = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMANTICS = {"read", "write", "enqueue", "admin"}
_IDEMPOTENCY = {"not-allowed", "optional", "required"}


class CatalogRegistry(Protocol):
    """Typing-only structural surface; no registration implementation lives here."""

    def register(self, definition: Any, handler: Callable[..., Any]) -> None: ...


def _copy_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    if any(not isinstance(key, str) or not key for key in value):
        raise ValueError(f"{label} keys must be non-empty strings")
    return deepcopy(dict(value))


def object_schema(
    properties: Mapping[str, Any],
    *,
    required: Iterable[str] = (),
    additional_properties: bool = False,
    definitions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a strict Draft 2020-12 object schema with isolated inputs."""

    copied_properties = _copy_mapping(properties, "properties")
    required_values = tuple(required)
    if any(not isinstance(item, str) or not item for item in required_values):
        raise ValueError("required names must be non-empty strings")
    if len(required_values) != len(set(required_values)):
        raise ValueError("required names must be unique")
    missing = sorted(set(required_values) - set(copied_properties))
    if missing:
        raise ValueError(f"required properties are undeclared: {missing}")
    if not isinstance(additional_properties, bool):
        raise TypeError("additional_properties must be bool")
    result: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "properties": copied_properties,
        "additionalProperties": additional_properties,
    }
    if required_values:
        result["required"] = list(required_values)
    if definitions is not None:
        result["$defs"] = _copy_mapping(definitions, "definitions")
    return result


def _schema(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    result = _copy_mapping(value, label)
    if result.get("$schema") != SCHEMA_DIALECT:
        raise ValueError(f"{label}.$schema must be {SCHEMA_DIALECT}")
    if result.get("type") != "object":
        raise ValueError(f"{label}.type must be object")
    return result


def operation_spec(
    name: str,
    *,
    owning_domain: str | None = None,
    input_schema: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    required_authority: str | None = None,
    execution_semantics: str | None = None,
    idempotency: str,
    authority: str | None = None,
    semantics: str | None = None,
    repo_scoped: bool = False,
    required_client_schema_features: Iterable[str] = SCHEMA_FEATURES,
    deprecation: Mapping[str, Any] | None = None,
    result_contract: Mapping[str, Any] | None = None,
    failure_disclosure: str | None = None,
) -> dict[str, Any]:
    """Return an isolated, validated operation-spec dictionary."""

    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("name must be a three-segment lowercase operation name")
    domain = owning_domain or name.split(".", 1)[0]
    if not _DOMAIN.fullmatch(domain) or not name.startswith(domain + "."):
        raise ValueError("owning_domain must match the operation name prefix")
    if required_authority is None:
        required_authority = authority
    if execution_semantics is None:
        execution_semantics = semantics
    if execution_semantics not in _SEMANTICS:
        raise ValueError(f"execution_semantics must be one of {sorted(_SEMANTICS)}")
    if idempotency not in _IDEMPOTENCY:
        raise ValueError(f"idempotency must be one of {sorted(_IDEMPOTENCY)}")
    if not isinstance(repo_scoped, bool):
        raise TypeError("repo_scoped must be bool")
    features = tuple(required_client_schema_features)
    if any(not isinstance(feature, str) or not feature for feature in features):
        raise ValueError("required client schema features must be non-empty strings")
    if SCHEMA_FEATURES[0] not in features:
        raise ValueError("required client schema features must include the JSON-Schema dialect")
    result: dict[str, Any] = {
        "name": name,
        "owning_domain": domain,
        "input_schema": _schema(input_schema, "input_schema"),
        "result_schema": _schema(result_schema, "result_schema"),
        "required_authority": required_authority,
        "execution_semantics": execution_semantics,
        "idempotency": idempotency,
        "repo_scoped": repo_scoped,
        "deprecation": deepcopy(dict(deprecation or {
            "deprecated": False, "replacement": None, "sunset_at": None
        })),
        "required_client_schema_features": list(features),
    }
    if result_contract is not None:
        result["result_contract"] = _copy_mapping(result_contract, "result_contract")
    if failure_disclosure is not None:
        if not isinstance(failure_disclosure, str) or not failure_disclosure:
            raise ValueError("failure_disclosure must be a non-empty string")
        result["failure_disclosure"] = failure_disclosure
    return result


build_object_schema = object_schema
build_operation_spec = operation_spec
