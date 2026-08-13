import pytest

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


def test_builders_fail_closed_on_invalid_contracts() -> None:
    with pytest.raises(ValueError):
        object_schema({"value": {}}, required=("missing",))
    with pytest.raises(ValueError):
        operation_spec(
            "Work.read.items", input_schema=object_schema({}),
            result_schema=object_schema({}), execution_semantics="read",
            idempotency="not-allowed",
        )
