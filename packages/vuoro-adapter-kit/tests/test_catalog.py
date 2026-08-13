from vuoro_adapter_kit import (
    SCHEMA_DIALECT,
    AdapterOperation,
    definition,
    object_schema,
    register_operations,
)


def test_object_schema_is_strict_and_supports_local_defs() -> None:
    schema = object_schema({"value": {"type": "string"}}, required=("value",))
    assert schema == {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
        "required": ["value"],
    }


def test_definition_and_registration_preserve_order() -> None:
    first = definition(
        "work.read.items",
        input_schema=object_schema({}), result_schema=object_schema({}),
        authority="work.read", semantics="read", idempotency="not-allowed",
    )
    second = definition(
        "work.item.note", input_schema=object_schema({}), result_schema=object_schema({}),
        authority="work.write", semantics="write", idempotency="required",
    )
    calls = []
    register_operations(calls_registry := _Registry(calls), [
        AdapterOperation(first, lambda *_: None), (second, lambda *_: None)
    ])
    assert [item[0]["name"] for item in calls] == ["work.read.items", "work.item.note"]


class _Registry:
    def __init__(self, calls):
        self.calls = calls

    def register(self, definition, handler):
        self.calls.append((definition, handler))
