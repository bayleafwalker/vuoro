from __future__ import annotations

import pytest

from vuoro_service.catalog import CatalogRegistrationError, CatalogRegistry
from vuoro_service.contracts import OperationDefinition


OBJECT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
}


def operation(name: str) -> OperationDefinition:
    return OperationDefinition(
        name=name,
        owning_domain=name.split(".", 1)[0],
        input_schema=OBJECT_SCHEMA,
        result_schema=OBJECT_SCHEMA,
        execution_semantics="read",
        idempotency="not-allowed",
    )


def test_revision_is_deterministic_and_catalog_is_sorted() -> None:
    first = CatalogRegistry()
    second = CatalogRegistry()
    for registry, names in (
        (first, ["work.pilot.zeta", "audit.observation.alpha"]),
        (second, ["audit.observation.alpha", "work.pilot.zeta"]),
    ):
        for name in names:
            registry.register(operation(name), lambda arguments, context: arguments)

    assert first.revision == second.revision
    assert [value.name for value in first.catalog().operations] == [
        "audit.observation.alpha",
        "work.pilot.zeta",
    ]


def test_duplicate_names_are_rejected() -> None:
    registry = CatalogRegistry()
    registry.register(
        operation("work.pilot.inspect"), lambda arguments, context: arguments
    )
    with pytest.raises(CatalogRegistrationError, match="duplicate operation name"):
        registry.register(
            operation("work.pilot.inspect"), lambda arguments, context: arguments
        )


def test_external_and_dynamic_schema_references_are_rejected() -> None:
    registry = CatalogRegistry()
    for reference_key, reference in (
        ("$ref", "https://attacker.invalid/schema.json"),
        ("$dynamicRef", "#node"),
    ):
        definition = operation(f"work.schema.{reference_key[1:].lower()}").model_copy(
            update={"input_schema": {reference_key: reference}}
        )
        with pytest.raises(
            CatalogRegistrationError, match="references are not supported|only local"
        ):
            registry.register(definition, lambda arguments, context: arguments)


def test_local_defs_reference_is_accepted() -> None:
    registry = CatalogRegistry()
    definition = operation("work.schema.local-ref").model_copy(
        update={
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$defs": {"identifier": {"type": "string"}},
                "$ref": "#/$defs/identifier",
            }
        }
    )
    registry.register(definition, lambda arguments, context: arguments)


def test_missing_local_reference_target_is_rejected_during_registration() -> None:
    registry = CatalogRegistry()
    definition = operation("work.schema.missing-ref").model_copy(
        update={
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": "#/$defs/missing",
            }
        }
    )
    with pytest.raises(CatalogRegistrationError, match="target does not exist"):
        registry.register(definition, lambda arguments, context: arguments)
