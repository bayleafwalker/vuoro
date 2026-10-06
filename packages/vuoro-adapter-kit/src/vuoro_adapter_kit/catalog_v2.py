"""Explicit strict-view operation metadata, separate from legacy spec bytes."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Literal

from .admission import CATALOG_REQUIRED_PROFILE, _AUTHORITY
from .catalog import SCHEMA_FEATURES, _copy_mapping, _schema, operation_spec

_CONTRACT = re.compile(r"^[a-z][a-z0-9.-]*/v[1-9][0-9]*$")


def _schema_v2(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    """V1 object authoring vocabulary plus root oneOf, with isolated branches.

    This structural builder is not a complete JSON Schema evaluator. The owner
    registry still validates the complete Draft 2020-12 schema. Branch order is
    semantic metadata and must never be sorted or normalized.
    """
    copied = _copy_mapping(value, label)
    if "oneOf" not in copied:
        return _schema(copied, label)
    branches = copied.pop("oneOf")
    base = _schema(copied, label)
    if isinstance(branches, (str, bytes)) or not isinstance(branches, Sequence):
        raise TypeError(f"{label}.oneOf must be a sequence of schemas")
    if not branches:
        raise ValueError(f"{label}.oneOf must not be empty")
    for branch in branches:
        if type(branch) is bool:
            continue
        if not isinstance(branch, Mapping):
            raise TypeError(f"{label}.oneOf branches must be schema mappings or booleans")
        if any(type(key) is not str or not key for key in branch):
            raise ValueError(f"{label}.oneOf branch keys must be non-empty strings")
        if "required" in branch:
            names = branch["required"]
            if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
                raise TypeError(f"{label}.oneOf required must be a sequence")
            if any(type(name) is not str or not name for name in names) or len(names) != len(set(names)):
                raise ValueError(f"{label}.oneOf required must contain unique non-empty strings")
    base["oneOf"] = branches
    return base


def operation_spec_v2(
    name: str,
    *,
    admission_profile: Literal["catalog-required/v2"],
    legacy_availability: Literal["released-legacy", "strict-only"],
    required_authorities: Sequence[str],
    input_schema: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    execution_semantics: str,
    idempotency: str,
    owning_domain: str | None = None,
    repo_scoped: bool = False,
    required_client_schema_features: Sequence[str] = SCHEMA_FEATURES,
    deprecation: Mapping[str, Any] | None = None,
    result_contract: Mapping[str, Any] | None = None,
    failure_disclosure: str | None = None,
    owner_admission_contract: str | None = None,
) -> dict[str, Any]:
    """Build strict metadata only; no registration, admission or authentication.

    Owners explicitly declare placement. Required authorities are an AND set;
    ordering does not convey policy and is normalized for deterministic metadata.
    Inner schemas retain their array order and are deep-copied, as in v1.
    """
    if type(name) is not str:
        raise TypeError("name must be exact str")
    if type(execution_semantics) is not str or type(idempotency) is not str:
        raise TypeError("execution_semantics and idempotency must be exact str")
    if type(admission_profile) is not str or admission_profile != CATALOG_REQUIRED_PROFILE:
        raise ValueError("unsupported admission profile")
    if type(legacy_availability) is not str or legacy_availability not in {"released-legacy", "strict-only"}:
        raise ValueError("invalid legacy availability")
    if isinstance(required_authorities, (str, bytes)) or not isinstance(required_authorities, Sequence):
        raise TypeError("required_authorities must be a sequence")
    authorities = tuple(required_authorities)
    if any(type(value) is not str or not _AUTHORITY.fullmatch(value) for value in authorities):
        raise ValueError("required authorities must be exact non-wildcard names")
    if len(authorities) != len(set(authorities)):
        raise ValueError("required authorities must be unique")
    if execution_semantics in {"write", "enqueue", "admin"} and not authorities:
        raise ValueError("strict mutations require explicit authorities")
    if owner_admission_contract is not None:
        if type(owner_admission_contract) is not str or not _CONTRACT.fullmatch(owner_admission_contract):
            raise ValueError("owner admission contract must be a versioned identifier")
        if (
            legacy_availability != "strict-only"
            or repo_scoped is not True
            or idempotency != "required"
            or execution_semantics not in {"write", "enqueue", "admin"}
            or not authorities
        ):
            raise ValueError("owner admission requires strict-only repo-scoped required-key mutation metadata")
    # Validate complete v2 schemas here. The unchanged v1 metadata builder sees
    # its original vocabulary; the v2 result retains the complete validated
    # schema snapshots, including oneOf, rather than publishing substitutes.
    input_snapshot = _schema_v2(input_schema, "input_schema")
    result_snapshot = _schema_v2(result_schema, "result_schema")
    spec = operation_spec(
        name, owning_domain=owning_domain,
        input_schema={key: value for key, value in input_snapshot.items() if key != "oneOf"},
        result_schema={key: value for key, value in result_snapshot.items() if key != "oneOf"}, execution_semantics=execution_semantics,
        idempotency=idempotency, repo_scoped=repo_scoped,
        required_client_schema_features=required_client_schema_features,
        deprecation=deprecation, result_contract=result_contract,
        failure_disclosure=failure_disclosure,
    )
    spec["input_schema"] = input_snapshot
    spec["result_schema"] = result_snapshot
    del spec["required_authority"]
    spec.update(
        schema_version="operation-definition/v2",
        admission_profile=admission_profile,
        legacy_availability=legacy_availability,
        required_authorities=sorted(authorities),
    )
    if owner_admission_contract is not None:
        spec["owner_admission_contract"] = owner_admission_contract
    return spec


build_operation_spec_v2 = operation_spec_v2
