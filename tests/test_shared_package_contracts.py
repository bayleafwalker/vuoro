import pytest

from vuoro_adapter_kit import object_schema, operation_spec

pytest.importorskip("pydantic")

from vuoro_service.contracts import OperationDefinition


def test_adapter_kit_spec_is_accepted_by_service_operation_definition() -> None:
    spec = operation_spec(
        "work.read.items",
        input_schema=object_schema({"limit": {"type": "integer"}}, required=("limit",)),
        result_schema=object_schema({"items": {"type": "array"}}, required=("items",)),
        required_authority="work.read",
        execution_semantics="read",
        idempotency="not-allowed",
        repo_scoped=True,
    )
    definition = OperationDefinition(**spec)
    assert definition.name == spec["name"]
    assert definition.input_schema == spec["input_schema"]
