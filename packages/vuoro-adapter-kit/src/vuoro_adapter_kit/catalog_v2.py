"""Explicit strict-view operation metadata, separate from legacy spec bytes."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Literal

from .admission import CATALOG_REQUIRED_PROFILE, _AUTHORITY
from .catalog import SCHEMA_FEATURES, operation_spec

_CONTRACT = re.compile(r"^[a-z][a-z0-9.-]*/v[1-9][0-9]*$")


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
    spec = operation_spec(
        name, owning_domain=owning_domain, input_schema=input_schema,
        result_schema=result_schema, execution_semantics=execution_semantics,
        idempotency=idempotency, repo_scoped=repo_scoped,
        required_client_schema_features=required_client_schema_features,
        deprecation=deprecation, result_contract=result_contract,
        failure_disclosure=failure_disclosure,
    )
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
