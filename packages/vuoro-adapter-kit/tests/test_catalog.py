import pytest
from typing import get_origin, get_type_hints

from vuoro_adapter_kit import SCHEMA_DIALECT, object_schema, operation_spec


def test_object_schema_matches_wire_shape_and_is_mutation_isolated() -> None:
    properties = {"value": {"type": "string"}}
    schema = object_schema(properties, required=("value",))
    expected = {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
        "required": ["value"],
    }
    assert schema == expected
    properties["value"]["type"] = "integer"
    assert schema == expected


def test_operation_spec_is_exact_and_deeply_isolated() -> None:
    input_schema = object_schema({"value": {"type": "string"}}, required=("value",))
    result_schema = object_schema({"ok": {"type": "boolean"}}, required=("ok",))
    spec = operation_spec(
        "work.read.items", input_schema=input_schema, result_schema=result_schema,
        required_authority="work.read", execution_semantics="read",
        idempotency="not-allowed",
    )
    assert spec == {
        "name": "work.read.items", "owning_domain": "work",
        "input_schema": input_schema, "result_schema": result_schema,
        "required_authority": "work.read", "execution_semantics": "read",
        "idempotency": "not-allowed", "repo_scoped": False,
        "deprecation": {"deprecated": False, "replacement": None, "sunset_at": None},
        "required_client_schema_features": ["json-schema-draft-2020-12"],
    }
    input_schema["properties"]["value"]["type"] = "integer"
    assert spec["input_schema"]["properties"]["value"]["type"] == "string"


def test_operation_spec_type_hints_resolve_public_annotations() -> None:
    hints = get_type_hints(operation_spec)
    assert hints["input_schema"]
    assert hints["result_schema"]
    assert hints["required_client_schema_features"]
    assert get_origin(hints["return"]) is dict


def test_builders_fail_closed_on_invalid_contracts() -> None:
    with pytest.raises(ValueError):
        object_schema({"value": {}}, required=("missing",))
    with pytest.raises(ValueError):
        operation_spec(
            "Work.read.items", input_schema=object_schema({}),
            result_schema=object_schema({}), execution_semantics="read",
            idempotency="not-allowed",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"required": "value"},
        {"required": ["value", "value"]},
        {"additional_properties": "false"},
        {"definitions": []},
    ],
)
def test_object_schema_rejects_malformed_root_builder_fields(kwargs) -> None:
    with pytest.raises((TypeError, ValueError)):
        object_schema({"value": {"type": "string"}}, **kwargs)


@pytest.mark.parametrize(
    "schema_mutation",
    [
        lambda schema: schema.update(properties=[]),
        lambda schema: schema.update(additionalProperties="false"),
        lambda schema: schema.update(required="value"),
        lambda schema: schema.update(required=["value", "value"]),
        lambda schema: schema.update(required=["missing"]),
        lambda schema: schema.update({"$defs": []}),
        lambda schema: schema.update(title="unsupported"),
    ],
    ids=("properties", "additional-properties", "required-type", "required-duplicate", "required-missing", "defs-type", "root-extra"),
)
def test_operation_spec_rejects_malformed_supported_schema_roots(schema_mutation) -> None:
    schema = object_schema({"value": {"type": "string"}}, required=("value",))
    schema_mutation(schema)
    with pytest.raises((TypeError, ValueError)):
        operation_spec(
            "work.read.items", input_schema=schema, result_schema=object_schema({}),
            execution_semantics="read", idempotency="not-allowed",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"required_authority": 1},
        {"owning_domain": 1},
        {"execution_semantics": 1},
        {"idempotency": 1},
        {"repo_scoped": "false"},
        {"required_client_schema_features": "json-schema-draft-2020-12"},
        {"deprecation": {"deprecated": False}},
        {"deprecation": {"deprecated": "false", "replacement": None, "sunset_at": None}},
        {"result_contract": {"mode": "wrong", "resource_kind": "work.item"}},
        {"failure_disclosure": "resource-not-found/v2"},
    ],
)
def test_operation_spec_rejects_malformed_scalar_and_metadata_fields(kwargs) -> None:
    with pytest.raises((TypeError, ValueError)):
        operation_spec(
            "work.read.items", input_schema=object_schema({}),
            result_schema=object_schema({}), execution_semantics="read",
            idempotency="not-allowed", **kwargs,
        )
