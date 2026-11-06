"""Source contract checks for the versioned agent declaration schemas."""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "architecture"


def load_schema(version: int) -> dict:
    return json.loads((ARCH / f"agent-declaration-v{version}.schema.json").read_text())


def cloud_receiver_v2() -> dict:
    document = (ARCH / "agent-declaration-v2.md").read_text()
    match = re.search(r"```json\s*(\{.*?\})\s*```", document, re.DOTALL)
    assert match, "versioned OAuth receiver example is missing"
    return json.loads(match.group(1))


def test_v2_keeps_the_v1_shape_and_channel_contract():
    v1 = load_schema(1)
    v2 = load_schema(2)
    Draft202012Validator.check_schema(v2)

    assert set(v1["required"]) == {
        "schema", "harness", "adapter", "host_class", "interactivity",
        "accepted_kinds", "channels",
    }
    assert v2["required"] == v1["required"]
    assert v2["properties"]["channels"] == v1["properties"]["channels"]
    assert v2["properties"]["schema"]["const"] == "agent-declaration/v2"
    comparable_v1 = json.loads(json.dumps(v1))
    comparable_v2 = json.loads(json.dumps(v2))
    comparable_v1["$id"] = comparable_v2["$id"]
    comparable_v1["title"] = comparable_v2["title"]
    comparable_v1["properties"]["schema"]["const"] = comparable_v2["properties"]["schema"]["const"]
    comparable_v1["properties"]["accepted_kinds"]["items"]["enum"] = comparable_v2["properties"]["accepted_kinds"]["items"]["enum"]
    assert comparable_v1 == comparable_v2


def test_v2_accepts_oauth_receiver_example_without_channel_claims():
    example = cloud_receiver_v2()
    Draft202012Validator(load_schema(2)).validate(example)
    assert example["accepted_kinds"] == ["request-resolution/v1"]
    assert example["channels"] == []


@pytest.mark.parametrize("kinds", [
    ["unsupported"],
    ["continue", "continue"],
    ["request-resolution/v1", "request-resolution/v1"],
    ["continue"] * 9,
])
def test_v2_rejects_unknown_duplicate_and_oversized_kind_lists(kinds):
    declaration = cloud_receiver_v2()
    declaration["accepted_kinds"] = kinds
    assert not Draft202012Validator(load_schema(2)).is_valid(declaration)


def test_v1_remains_narrow_and_rejects_v2_resolution_kind():
    declaration = cloud_receiver_v2()
    declaration["schema"] = "agent-declaration/v1"
    assert not Draft202012Validator(load_schema(1)).is_valid(declaration)


def directory_entry(declaration: dict, version: int) -> dict:
    return {
        "schema": f"agent-directory-entry/v{version}",
        "principal_id": "principal-1",
        "evaluated_at": "2026-11-06T10:00:00Z",
        "instances": [{
            "instance_id": "ins_0123456789ABCDEFGHJKMNPQRS",
            "declaration": declaration,
            "declaration_revision": 1,
            "registered": {
                "state": "current", "registered_at": "2026-11-06T10:00:00Z",
                "retired_at": None, "retire_reason": None,
            },
            "observed": {
                "state": "unknown", "observed_at": None,
                "valid_until": None, "source": None,
            },
            "reachable": [],
            "executing": [],
            "attended": {
                "state": "unknown", "observed_at": None,
                "valid_until": None, "source": None,
            },
        }],
    }


def test_directory_entry_v2_is_additive_and_v1_stays_frozen():
    declaration_schemas = [load_schema(1), load_schema(2)]
    directory_v1 = json.loads((ARCH / "agent-directory-entry-v1.schema.json").read_text())
    directory_v2 = json.loads((ARCH / "agent-directory-entry-v2.schema.json").read_text())
    Draft202012Validator.check_schema(directory_v2)

    assert directory_v2["properties"]["instances"]["items"]["properties"]["declaration"] == {
        "oneOf": [
            {"$ref": "urn:vuoro:agent-declaration:v1"},
            {"$ref": "urn:vuoro:agent-declaration:v2"},
        ]
    }
    assert directory_v1["properties"]["instances"]["items"]["properties"]["declaration"] == {
        "$ref": "urn:vuoro:agent-declaration:v1"
    }

    schemas = declaration_schemas + [directory_v1, directory_v2]
    registry = Registry().with_resources(
        [(schema["$id"], Resource.from_contents(schema)) for schema in schemas]
    )
    v1_validator = Draft202012Validator(directory_v1, registry=registry)
    v2_validator = Draft202012Validator(directory_v2, registry=registry)
    declaration_v1 = {
        **cloud_receiver_v2(), "schema": "agent-declaration/v1",
        "accepted_kinds": ["continue"],
    }
    declaration_v2 = cloud_receiver_v2()

    v1_validator.validate(directory_entry(declaration_v1, 1))
    assert not v1_validator.is_valid(directory_entry(declaration_v2, 1))
    v2_validator.validate(directory_entry(declaration_v1, 2))
    v2_validator.validate(directory_entry(declaration_v2, 2))
