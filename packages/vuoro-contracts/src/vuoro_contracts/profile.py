"""Finite offline JSON Schema profile shared by generation and runtime loading."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ContractError

DIALECT = "https://json-schema.org/draft/2020-12/schema"
_RESOURCE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

_ANNOTATIONS = {
    "title", "description", "$comment", "deprecated", "readOnly", "writeOnly",
    "examples", "contentEncoding", "contentMediaType",
}
_ATOMS = {
    "$id", "$schema", "$ref", "type", "enum", "const", "required", "minItems",
    "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
    "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minProperties",
    "maxProperties",
}
_MAPS = {"properties", "$defs"}
_SEQUENCES = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SINGLE = {"not", "items", "additionalProperties", "unevaluatedProperties"}
_SUPPORTED = _ANNOTATIONS | _ATOMS | _MAPS | _SEQUENCES | _SINGLE


def validate_bundle(bundle: Mapping[str, Any]) -> None:
    """Refuse unsupported vocabulary and non-local/ambiguous schema references."""
    try:
        if not isinstance(bundle, Mapping) or bundle.get("$schema") != DIALECT:
            raise ValueError
        definitions = bundle.get("$defs")
        if not isinstance(definitions, Mapping) or not definitions:
            raise ValueError
        ids: set[str] = set()
        for definition in definitions.values():
            if not isinstance(definition, Mapping):
                raise ValueError
            identifier = definition.get("$id")
            if (not isinstance(identifier, str) or not _RESOURCE_ID.fullmatch(identifier) or
                    identifier in ids):
                raise ValueError
            ids.add(identifier)
        root_id = bundle.get("$id")
        if root_id is not None and (not isinstance(root_id, str) or
                                    not _RESOURCE_ID.fullmatch(root_id) or root_id in ids):
            raise ValueError

        def walk(node: Any, *, root: bool = False, resource: bool = False) -> None:
            if isinstance(node, bool):
                return
            if not isinstance(node, Mapping):
                raise ValueError
            keys = set(node)
            if not all(isinstance(key, str) for key in keys) or keys - _SUPPORTED:
                raise ValueError
            if "$schema" in node and node["$schema"] != DIALECT:
                raise ValueError
            if "$id" in node and not (root or resource):
                raise ValueError
            if "$ref" in node and (not isinstance(node["$ref"], str) or node["$ref"] not in ids):
                raise ValueError
            for key in _MAPS & keys:
                children = node[key]
                if not isinstance(children, Mapping) or not all(isinstance(k, str) for k in children):
                    raise ValueError
                for child in children.values():
                    walk(child, resource=(root and key == "$defs"))
            for key in _SEQUENCES & keys:
                children = node[key]
                if not isinstance(children, (list, tuple)):
                    raise ValueError
                for child in children:
                    walk(child)
            for key in _SINGLE & keys:
                walk(node[key])

        walk(bundle, root=True)
        graph: dict[str, set[str]] = {identifier: set() for identifier in ids}

        def references(node: Any, found: set[str]) -> None:
            """Visit schemas only; annotation/example/enum/const payloads are opaque JSON."""
            if isinstance(node, bool) or not isinstance(node, Mapping):
                return
            ref = node.get("$ref")
            if isinstance(ref, str):
                found.add(ref)
            for key in _MAPS & node.keys():
                for child in node[key].values():
                    references(child, found)
            for key in _SEQUENCES & node.keys():
                for child in node[key]:
                    references(child, found)
            for key in _SINGLE & node.keys():
                references(node[key], found)

        for resource in definitions.values():
            references(resource, graph[resource["$id"]])
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise ValueError
            if identifier in visited:
                return
            visiting.add(identifier)
            for target in graph[identifier]:
                visit(target)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in ids:
            visit(identifier)
        # The schema library catches malformed keyword values and structural errors
        # after the explicit finite vocabulary gate has rejected unknown semantics.
        Draft202012Validator.check_schema(dict(bundle))
    except ContractError:
        raise
    except Exception:
        raise ContractError("schema_profile") from None
