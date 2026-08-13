"""Pure JSON-Schema and operation-spec builders for Vuoro adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
    required: Sequence[str] = (),
    additional_properties: bool = False,
    definitions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a strict Draft 2020-12 object schema with isolated inputs."""

    copied_properties = _copy_mapping(properties, "properties")
    if isinstance(required, (str, bytes)) or not isinstance(required, Sequence):
        raise TypeError("required must be a sequence of strings")
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
    allowed = {"$schema", "type", "properties", "additionalProperties", "required", "$defs"}
    unknown = sorted(set(result) - allowed)
    if unknown:
        raise ValueError(f"{label} has unsupported root fields: {unknown}")
    if result.get("$schema") != SCHEMA_DIALECT:
        raise ValueError(f"{label}.$schema must be {SCHEMA_DIALECT}")
    if result.get("type") != "object":
        raise ValueError(f"{label}.type must be object")
    properties = result.get("properties")
    if not isinstance(properties, Mapping):
        raise TypeError(f"{label}.properties must be a mapping")
    if any(not isinstance(key, str) or not key for key in properties):
        raise ValueError(f"{label}.properties keys must be non-empty strings")
    if not isinstance(result.get("additionalProperties"), bool):
        raise TypeError(f"{label}.additionalProperties must be bool")
    if "required" in result:
        required = result["required"]
        if isinstance(required, (str, bytes)) or not isinstance(required, Sequence):
            raise TypeError(f"{label}.required must be a sequence of strings")
        if any(not isinstance(item, str) or not item for item in required):
            raise ValueError(f"{label}.required must contain non-empty strings")
        if len(required) != len(set(required)):
            raise ValueError(f"{label}.required must not contain duplicates")
        missing = sorted(set(required) - set(properties))
        if missing:
            raise ValueError(f"{label}.required contains undeclared properties: {missing}")
    if "$defs" in result and not isinstance(result["$defs"], Mapping):
        raise TypeError(f"{label}.$defs must be a mapping")
    return result


def _deprecation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is not None and not isinstance(value, Mapping):
        raise TypeError("deprecation must be a mapping")
    result = dict(value or {
        "deprecated": False, "replacement": None, "sunset_at": None
    })
    if set(result) != {"deprecated", "replacement", "sunset_at"}:
        raise ValueError("deprecation must contain exactly deprecated, replacement, sunset_at")
    if not isinstance(result["deprecated"], bool):
        raise TypeError("deprecation.deprecated must be bool")
    for field in ("replacement", "sunset_at"):
        if result[field] is not None and (
            not isinstance(result[field], str) or not result[field]
        ):
            raise TypeError(f"deprecation.{field} must be a non-empty string or None")
    return deepcopy(result)


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
    handler_name: str | None = None,
) -> dict[str, Any]:
    """Return an isolated, validated operation-spec dictionary."""

    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("name must be a three-segment lowercase operation name")
    if owning_domain is not None and not isinstance(owning_domain, str):
        raise TypeError("owning_domain must be str or None")
    domain = name.split(".", 1)[0] if owning_domain is None else owning_domain
    if not _DOMAIN.fullmatch(domain) or not name.startswith(domain + "."):
        raise ValueError("owning_domain must match the operation name prefix")
    if required_authority is not None and (
        not isinstance(required_authority, str) or not required_authority
    ):
        raise TypeError("required_authority must be a non-empty string or None")
    if authority is not None and (
        not isinstance(authority, str) or not authority
    ):
        raise TypeError("authority must be a non-empty string or None")
    if (
        required_authority is not None
        and authority is not None
        and required_authority != authority
    ):
        raise ValueError("required_authority and authority aliases disagree")
    if required_authority is None:
        required_authority = authority
    if execution_semantics is None:
        execution_semantics = semantics
    if execution_semantics is not None and not isinstance(execution_semantics, str):
        raise TypeError("execution_semantics must be str")
    if semantics is not None and not isinstance(semantics, str):
        raise TypeError("semantics must be str")
    if (
        execution_semantics is not None
        and semantics is not None
        and execution_semantics != semantics
    ):
        raise ValueError("execution_semantics and semantics aliases disagree")
    if execution_semantics not in _SEMANTICS:
        raise ValueError(f"execution_semantics must be one of {sorted(_SEMANTICS)}")
    if not isinstance(idempotency, str):
        raise TypeError("idempotency must be str")
    if idempotency not in _IDEMPOTENCY:
        raise ValueError(f"idempotency must be one of {sorted(_IDEMPOTENCY)}")
    if not isinstance(repo_scoped, bool):
        raise TypeError("repo_scoped must be bool")
    if isinstance(required_client_schema_features, (str, bytes)) or not isinstance(required_client_schema_features, Sequence):
        raise TypeError("required_client_schema_features must be a sequence of strings")
    features = tuple(required_client_schema_features)
    if any(not isinstance(feature, str) or not feature for feature in features):
        raise ValueError("required client schema features must be non-empty strings")
    if len(features) != len(set(features)):
        raise ValueError("required client schema features must be unique")
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
        "deprecation": _deprecation(deprecation),
        "required_client_schema_features": list(features),
    }
    if result_contract is not None:
        contract = _copy_mapping(result_contract, "result_contract")
        if set(contract) != {"mode", "resource_kind"}:
            raise ValueError("result_contract must contain exactly mode and resource_kind")
        if contract["mode"] != "resource-reference":
            raise ValueError("result_contract.mode must be resource-reference")
        if not isinstance(contract["resource_kind"], str) or not re.fullmatch(
            r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$",
            contract["resource_kind"],
        ):
            raise ValueError("result_contract.resource_kind is invalid")
        result["result_contract"] = contract
    if failure_disclosure is not None:
        if failure_disclosure != "resource-not-found/v1":
            raise ValueError("failure_disclosure must be resource-not-found/v1")
        result["failure_disclosure"] = failure_disclosure
    if handler_name is not None:
        if not isinstance(handler_name, str) or not handler_name:
            raise TypeError("handler_name must be a non-empty string or None")
        result["_handler_name"] = handler_name
    return result


build_object_schema = object_schema
build_operation_spec = operation_spec
