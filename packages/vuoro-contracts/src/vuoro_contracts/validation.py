"""Strict JSON parsing and pure validation for generated contract bundles."""
from __future__ import annotations

from hashlib import sha256
from importlib import resources
from importlib.resources.abc import Traversable
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

from .errors import ContractError
from .profile import DIALECT, validate_bundle

ABSENT = object()
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_BUNDLE_FILES = 4096
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_WIRE_BODY_BYTES = 4 * 1024 * 1024
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 100_000
_STATUS = re.compile(r"^[1-5][0-9]{2}$")
_SCHEMA_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_MCP_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MCP_SCHEMA_KEYS = {
    "$ref", "$schema", "$id", "$anchor", "$comment", "$defs",
    "title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly",
    "type", "enum", "const", "multipleOf", "maximum", "exclusiveMaximum",
    "minimum", "exclusiveMinimum", "maxLength", "minLength", "pattern",
    "maxItems", "minItems", "uniqueItems", "maxContains", "minContains",
    "maxProperties", "minProperties", "required", "additionalProperties",
    "unevaluatedProperties", "properties", "patternProperties", "propertyNames",
    "dependentRequired", "dependentSchemas", "allOf", "anyOf", "oneOf", "not",
    "if", "then", "else", "items", "prefixItems", "contains",
}
_MCP_SCHEMA_MAPS = {"$defs", "properties", "patternProperties", "dependentSchemas"}
_MCP_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_MCP_SCHEMA_SINGLE = {"additionalProperties", "unevaluatedProperties", "propertyNames",
                      "not", "if", "then", "else", "items", "contains"}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ValueError


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result


def _check_json_complexity(text: str) -> None:
    """Bound JSON structure before the standard decoder materializes it."""
    offset = 0
    nodes = 0
    size = len(text)

    def space() -> None:
        nonlocal offset
        while offset < size and text[offset] in " \t\r\n":
            offset += 1

    def string() -> None:
        nonlocal offset
        if offset >= size or text[offset] != '"':
            raise ValueError
        offset += 1
        while offset < size:
            character = text[offset]
            offset += 1
            if character == '"':
                return
            if character == "\\":
                if offset >= size:
                    raise ValueError
                escape = text[offset]
                offset += 1
                if escape == "u":
                    if offset + 4 > size or any(
                        digit not in "0123456789abcdefABCDEF"
                        for digit in text[offset:offset + 4]
                    ):
                        raise ValueError
                    offset += 4
                elif escape not in '"\\/bfnrt':
                    raise ValueError
            elif ord(character) < 0x20:
                raise ValueError
        raise ValueError

    def number() -> None:
        nonlocal offset
        if offset < size and text[offset] == "-":
            offset += 1
        if offset < size and text[offset] == "0":
            offset += 1
            if offset < size and text[offset].isdigit() and text[offset].isascii():
                raise ValueError
        elif offset < size and text[offset] in "123456789":
            offset += 1
            while offset < size and text[offset] in "0123456789":
                offset += 1
        else:
            raise ValueError
        if offset < size and text[offset] == ".":
            offset += 1
            start = offset
            while offset < size and text[offset] in "0123456789":
                offset += 1
            if offset == start:
                raise ValueError
        if offset < size and text[offset] in "eE":
            offset += 1
            if offset < size and text[offset] in "+-":
                offset += 1
            start = offset
            while offset < size and text[offset] in "0123456789":
                offset += 1
            if offset == start:
                raise ValueError

    def value(depth: int) -> None:
        nonlocal offset, nodes
        nodes += 1
        if depth > _MAX_JSON_DEPTH or nodes > _MAX_JSON_NODES or offset >= size:
            raise ValueError
        character = text[offset]
        if character == '"':
            string()
        elif character == "[":
            offset += 1
            space()
            if offset < size and text[offset] == "]":
                offset += 1
                return
            while True:
                value(depth + 1)
                space()
                if offset >= size:
                    raise ValueError
                delimiter = text[offset]
                offset += 1
                if delimiter == "]":
                    return
                if delimiter != ",":
                    raise ValueError
                space()
        elif character == "{":
            offset += 1
            space()
            if offset < size and text[offset] == "}":
                offset += 1
                return
            while True:
                string()
                space()
                if offset >= size or text[offset] != ":":
                    raise ValueError
                offset += 1
                space()
                value(depth + 1)
                space()
                if offset >= size:
                    raise ValueError
                delimiter = text[offset]
                offset += 1
                if delimiter == "}":
                    return
                if delimiter != ",":
                    raise ValueError
                space()
        elif text.startswith("true", offset):
            offset += 4
        elif text.startswith("false", offset):
            offset += 5
        elif text.startswith("null", offset):
            offset += 4
        else:
            number()

    space()
    value(0)
    space()
    if offset != size:
        raise ValueError


def _finite_json(value: Any, *, depth: int = 0, _nodes: list[int] | None = None) -> bool:
    if _nodes is None:
        _nodes = [0]
    _nodes[0] += 1
    if depth > _MAX_JSON_DEPTH or _nodes[0] > _MAX_JSON_NODES:
        return False
    if value is None or type(value) is bool:
        return True
    if type(value) is str:
        return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if type(value) is int:
        # Keep integer tokens exact in both Python and JavaScript SDKs. Larger
        # exact counters belong in decimal strings, not rounded JSON numbers.
        return abs(value) <= _MAX_SAFE_JSON_INTEGER
    if type(value) is float:
        return (math.isfinite(value)
                and (not value.is_integer() or abs(value) <= _MAX_SAFE_JSON_INTEGER))
    if isinstance(value, list):
        return all(_finite_json(item, depth=depth + 1, _nodes=_nodes) for item in value)
    if isinstance(value, dict):
        return all(type(key) is str
                   and not any(0xD800 <= ord(character) <= 0xDFFF for character in key)
                   and _finite_json(item, depth=depth + 1, _nodes=_nodes)
                   for key, item in value.items())
    return False


def _canonical_size(value: Any) -> int:
    if not _finite_json(value):
        raise ContractError("json")
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"))
    except Exception:
        raise ContractError("json") from None


def parse_json(raw: bytes | str, *, max_bytes: int) -> Any:
    """Decode exactly one bounded UTF-8 JSON document with duplicate keys refused."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ContractError("body_limit")
    try:
        if isinstance(raw, bytes):
            if len(raw) > max_bytes:
                raise ContractError("body_limit")
            text = raw.decode("utf-8", errors="strict")
        elif isinstance(raw, str):
            encoded = raw.encode("utf-8", errors="strict")
            if len(encoded) > max_bytes:
                raise ContractError("body_limit")
            text = raw
        else:
            raise ContractError("json")
        _check_json_complexity(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant,
                           parse_float=_float)
        if not _finite_json(value):
            raise ContractError("json")
        return value
    except ContractError:
        raise
    except Exception:
        raise ContractError("json") from None


def validate_wire_json_limit(max_bytes: int) -> int:
    """Validate a JSON wire-body cap against the cross-language hard limit."""
    if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > _MAX_WIRE_BODY_BYTES:
        raise ContractError("body_limit")
    return max_bytes


def parse_wire_json(raw: bytes | str, *, max_bytes: int) -> Any:
    """Decode a wire body using the shared 4 MiB hard cap."""
    return parse_json(raw, max_bytes=validate_wire_json_limit(max_bytes))


def _no_remote(uri: str) -> Any:
    raise NoSuchResource(ref=uri)


def _parse_json_bytes(data: bytes) -> Any:
    try:
        if len(data) > _MAX_FILE_BYTES:
            raise ContractError("integrity")
        return parse_json(data, max_bytes=_MAX_FILE_BYTES)
    except ContractError:
        raise
    except Exception:
        raise ContractError("integrity") from None


def _safe_relative(relative: Any, *, code: str = "manifest") -> str:
    if not isinstance(relative, str) or not relative or "\\" in relative or len(relative) > 4096:
        raise ContractError(code)
    pure = PurePosixPath(relative)
    if (pure.is_absolute() or not pure.parts or
            any(part in ("", ".", "..") or len(part.encode("utf-8", "strict")) > 255
                for part in pure.parts)):
        raise ContractError(code)
    return pure.as_posix()


def _validate_document(value: Any, schema: dict[str, Any], code: str) -> None:
    try:
        Draft202012Validator(schema).validate(value)
    except Exception:
        raise ContractError(code) from None


def _manifest_checks(manifest: Any, schemas: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ContractError("manifest")
    _validate_document(manifest, _format_schema("operation-manifest.schema.json"), "manifest")
    bundle_ref = manifest["schemaBundle"]
    if bundle_ref["id"] != schemas["$id"]:
        raise ContractError("manifest")
    operations = manifest["operations"]
    mcp_tools = manifest.get("mcpTools", {})
    if not isinstance(mcp_tools, dict):
        raise ContractError("manifest")
    public_ids = {name for name, operation in operations.items()
                  if operation["exposure"] == "public"}
    if set(mcp_tools) != public_ids:
        raise ContractError("manifest")
    mcp_names = [mapping.get("name") for mapping in mcp_tools.values()
                 if isinstance(mapping, dict)]
    if (len(mcp_names) != len(mcp_tools)
            or len(set(mcp_names)) != len(mcp_names)
            or any(not isinstance(name, str) or not _MCP_TOOL_NAME.fullmatch(name)
                   for name in mcp_names)):
        raise ContractError("manifest")
    bindings: set[tuple[str, str]] = set()
    for operation in operations.values():
        identity = (operation["method"], operation["path"])
        if identity in bindings:
            raise ContractError("manifest")
        bindings.add(identity)
        if not operation["path"].startswith("/") or "//" in operation["path"]:
            raise ContractError("manifest")
        request = operation["request"]
        bound = operation["requestMaxBytes"]
        if (request is None) != (bound is None) or (bound is not None and (type(bound) is not int or bound <= 0)):
            raise ContractError("manifest")
        if request is not None and request not in schemas["resources"]:
            raise ContractError("manifest")
        seen_parameters: set[tuple[str, str]] = set()
        for parameter in operation["parameters"]:
            key = (parameter["in"], parameter["name"])
            if key in seen_parameters or parameter["schema"] not in schemas["resources"]:
                raise ContractError("manifest")
            seen_parameters.add(key)
        responses, bounds = operation["responses"], operation["responseMaxBytes"]
        if set(responses) != set(bounds):
            raise ContractError("manifest")
        for status, identifier in responses.items():
            if not _STATUS.fullmatch(status):
                raise ContractError("manifest")
            cap = bounds[status]
            if (identifier is None) != (cap is None):
                raise ContractError("manifest")
            if identifier is not None and identifier not in schemas["resources"]:
                raise ContractError("manifest")
            if cap is not None and (type(cap) is not int or cap <= 0):
                raise ContractError("manifest")


def _mcp_schema_refs(schema: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(schema, bool):
        return refs
    if not isinstance(schema, Mapping):
        raise ContractError("manifest")
    if set(schema) - _MCP_SCHEMA_KEYS:
        raise ContractError("manifest")
    ref = schema.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not _SCHEMA_ID.fullmatch(ref):
            raise ContractError("manifest")
        refs.add(ref)
    for key in _MCP_SCHEMA_MAPS:
        children = schema.get(key)
        if children is not None:
            if not isinstance(children, Mapping):
                raise ContractError("manifest")
            for child in children.values():
                refs.update(_mcp_schema_refs(child))
    for key in _MCP_SCHEMA_LISTS:
        children = schema.get(key)
        if children is not None:
            if not isinstance(children, list):
                raise ContractError("manifest")
            for child in children:
                refs.update(_mcp_schema_refs(child))
    for key in _MCP_SCHEMA_SINGLE:
        if key in schema:
            refs.update(_mcp_schema_refs(schema[key]))
    return refs


def _mcp_resource_closure(roots: set[str], resources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    closure: dict[str, Any] = {}
    state: dict[str, int] = {}
    for root in sorted(roots):
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            identifier, exiting = stack.pop()
            if exiting:
                state[identifier] = 2
                closure[identifier] = _mcp_rebase_schema(resources[identifier])
                continue
            mark = state.get(identifier, 0)
            if mark == 1:
                raise ContractError("manifest")
            if mark == 2:
                continue
            resource = resources.get(identifier)
            if resource is None:
                raise ContractError("manifest")
            state[identifier] = 1
            stack.append((identifier, True))
            for ref in sorted(_mcp_schema_refs(resource), reverse=True):
                if state.get(ref) == 1:
                    raise ContractError("manifest")
                if state.get(ref) != 2:
                    stack.append((ref, False))
    return closure


def _mcp_rebase_schema(schema: Any) -> Any:
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, Mapping):
        raise ContractError("manifest")
    if set(schema) - _MCP_SCHEMA_KEYS:
        raise ContractError("manifest")
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "$id":
            continue
        if key == "$ref":
            if not isinstance(value, str) or not _SCHEMA_ID.fullmatch(value):
                raise ContractError("manifest")
            result[key] = "#/$defs/" + value
        elif key in _MCP_SCHEMA_MAPS:
            if not isinstance(value, Mapping):
                raise ContractError("manifest")
            result[key] = {name: _mcp_rebase_schema(child) for name, child in value.items()}
        elif key in _MCP_SCHEMA_LISTS:
            if not isinstance(value, list):
                raise ContractError("manifest")
            result[key] = [_mcp_rebase_schema(child) for child in value]
        elif key in _MCP_SCHEMA_SINGLE:
            result[key] = _mcp_rebase_schema(value)
        else:
            result[key] = value
    return result


def _expected_mcp_tool(operation_id: str, operation: dict[str, Any],
                       resources_by_id: dict[str, Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    properties: dict[str, Any] = {}
    required: set[str] = set()
    arguments: dict[str, Any] = {}
    path_parameters: set[str] = set()
    schema_ids: set[str] = set()
    for parameter in operation["parameters"]:
        location, name, schema_id = parameter["in"], parameter["name"], parameter["schema"]
        if location not in {"path", "query"} or name in properties:
            raise ContractError("manifest")
        if (parameter["style"] != ("simple" if location == "path" else "form")
                or parameter["explode"] != (location == "query")
                or parameter["allowReserved"] is not False):
            raise ContractError("manifest")
        if location == "path" and parameter["required"] is not True:
            raise ContractError("manifest")
        if schema_id not in resources_by_id:
            raise ContractError("manifest")
        if resources_by_id[schema_id].get("type") not in {"string", "integer", "number", "boolean"}:
            raise ContractError("manifest")
        properties[name] = {"$ref": "#/$defs/" + schema_id}
        arguments[name] = {"in": location, "name": name, "schema": schema_id,
                           "style": parameter["style"], "explode": parameter["explode"],
                           "allowReserved": parameter["allowReserved"]}
        schema_ids.add(schema_id)
        if parameter["required"]:
            required.add(name)
        if location == "path":
            path_parameters.add(name)
    path = operation["path"]
    if path.count("{") != path.count("}"):
        raise ContractError("manifest")
    path_fields = re.findall(r"\{([^{}]+)\}", path)
    if (len(path_fields) != path.count("{")
            or len(set(path_fields)) != len(path_fields)
            or set(path_fields) != path_parameters):
        raise ContractError("manifest")

    request_id = operation["request"]
    if request_id is not None:
        body = resources_by_id.get(request_id)
        if body is None:
            raise ContractError("manifest")
        if body.get("type") == "object":
            body_properties = body.get("properties", {})
            if (not isinstance(body_properties, Mapping)
                    or body.get("additionalProperties") is not False
                    or set(body) - {"$id", "$schema", "title", "description", "type", "properties", "required", "additionalProperties"}):
                raise ContractError("manifest")
            body_required = body.get("required", [])
            if (not isinstance(body_required, list)
                    or not all(isinstance(name, str) and name in body_properties for name in body_required)
                    or len(set(body_required)) != len(body_required)):
                raise ContractError("manifest")
            if set(properties) & set(body_properties):
                raise ContractError("manifest")
            for name, schema in body_properties.items():
                if not isinstance(name, str) or not isinstance(schema, (Mapping, bool)):
                    raise ContractError("manifest")
                properties[name] = _mcp_rebase_schema(schema)
                arguments[name] = {"in": "body", "name": name, "schema": request_id}
                schema_ids.update(_mcp_schema_refs(schema))
            required.update(body_required)
        else:
            if "body" in properties:
                raise ContractError("manifest")
            properties["body"] = {"$ref": "#/$defs/" + request_id}
            arguments["body"] = {"in": "body", "name": "body", "schema": request_id}
            required.add("body")
            schema_ids.add(request_id)

    closure = _mcp_resource_closure(schema_ids, resources_by_id)
    input_schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": False,
    }
    if closure:
        input_schema["$defs"] = dict(sorted(closure.items()))
    binding = {
        "operationId": operation_id,
        "method": operation["method"],
        "path": path,
        "arguments": dict(sorted(arguments.items())),
    }
    return input_schema, binding


def _toolset_checks(toolset: Any, manifest: dict[str, Any], manifest_bytes: bytes,
                    schema_bytes: bytes, resources_by_id: dict[str, Mapping[str, Any]],
                    *, integrity_digest_supplied: bool) -> list[dict[str, Any]]:
    _validate_document(toolset, _format_schema("mcp-toolset.schema.json"), "manifest")
    expected_provenance = {
        "sourceSha256": manifest["provenance"]["sourceSha256"],
        "toolchainSha256": manifest["provenance"]["toolchainSha256"],
        "manifestSha256": sha256(manifest_bytes).hexdigest(),
        "schemaBundleSha256": sha256(schema_bytes).hexdigest(),
    }
    if (toolset["contractId"] != manifest["contractId"]
            or toolset["contractVersion"] != manifest["contractVersion"]
            or toolset["provenance"] != expected_provenance):
        raise ContractError("manifest")
    mappings = manifest.get("mcpTools", {})
    if mappings and not integrity_digest_supplied:
        raise ContractError("integrity")
    tools = toolset["tools"]
    public_ids = {name for name, operation in manifest["operations"].items()
                  if operation["exposure"] == "public"}
    if {tool["binding"]["operationId"] for tool in tools} != public_ids:
        raise ContractError("manifest")
    names: set[str] = set()
    by_operation = {tool["binding"]["operationId"]: tool for tool in tools}
    if len(by_operation) != len(tools):
        raise ContractError("manifest")
    for operation_id in sorted(public_ids):
        tool = by_operation[operation_id]
        mapping = mappings[operation_id]
        if (tool["name"] != mapping["name"]
                or tool["title"] != mapping["title"]
                or tool["description"] != mapping["description"]
                or tool["name"] in names):
            raise ContractError("manifest")
        names.add(tool["name"])
        expected_schema, expected_binding = _expected_mcp_tool(
            operation_id, manifest["operations"][operation_id], resources_by_id
        )
        if tool["inputSchema"] != expected_schema or tool["binding"] != expected_binding:
            raise ContractError("manifest")
        try:
            Draft202012Validator.check_schema(tool["inputSchema"])
        except Exception:
            raise ContractError("manifest") from None
    return tools


def _format_schema(filename: str) -> dict[str, Any]:
    path = resources.files(__package__).joinpath("formats", filename)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise ContractError("manifest") from None


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Read a bounded regular-file tree once; links and special files refuse."""
    snapshots: dict[str, bytes] = {}
    total = 0
    entries = 0
    try:
        pending: list[tuple[Path, str]] = [(root, "")]
        while pending:
            directory, prefix = pending.pop()
            for path in directory.iterdir():
                entries += 1
                if entries > _MAX_BUNDLE_FILES * 2:
                    raise ContractError("integrity")
                if path.is_symlink():
                    raise ContractError("integrity")
                relative = _safe_relative(f"{prefix}/{path.name}" if prefix else path.name, code="integrity")
                if path.is_dir():
                    pending.append((path, relative))
                    continue
                if not path.is_file() or relative in snapshots or len(snapshots) >= _MAX_BUNDLE_FILES:
                    raise ContractError("integrity")
                with path.open("rb") as stream:
                    data = stream.read(_MAX_FILE_BYTES + 1)
                total += len(data)
                if len(data) > _MAX_FILE_BYTES or total > _MAX_BUNDLE_BYTES:
                    raise ContractError("integrity")
                snapshots[relative] = data
        return snapshots
    except ContractError:
        raise
    except Exception:
        raise ContractError("integrity") from None


def _snapshot_resources(root: Traversable) -> dict[str, bytes]:
    """Read an importlib resource tree with the same bounds as a filesystem bundle."""
    snapshots: dict[str, bytes] = {}
    total = 0
    entries = 0
    try:
        if not root.is_dir():
            raise ContractError("integrity")
        pending: list[tuple[Traversable, str]] = [(root, "")]
        while pending:
            directory, prefix = pending.pop()
            for item in directory.iterdir():
                entries += 1
                if entries > _MAX_BUNDLE_FILES * 2:
                    raise ContractError("integrity")
                name = item.name
                if not isinstance(name, str) or "/" in name or "\\" in name:
                    raise ContractError("integrity")
                relative = _safe_relative(f"{prefix}/{name}" if prefix else name, code="integrity")
                is_symlink = getattr(item, "is_symlink", None)
                if callable(is_symlink) and is_symlink():
                    raise ContractError("integrity")
                if item.is_dir():
                    pending.append((item, relative))
                    continue
                if not item.is_file() or relative in snapshots or len(snapshots) >= _MAX_BUNDLE_FILES:
                    raise ContractError("integrity")
                opener = getattr(item, "open", None)
                if callable(opener):
                    with opener("rb") as stream:
                        data = stream.read(_MAX_FILE_BYTES + 1)
                else:
                    data = item.read_bytes()
                total += len(data)
                if len(data) > _MAX_FILE_BYTES or total > _MAX_BUNDLE_BYTES:
                    raise ContractError("integrity")
                snapshots[relative] = data
        return snapshots
    except ContractError:
        raise
    except Exception:
        raise ContractError("integrity") from None


class ContractBundle:
    """Validated immutable view over one complete owner-generated bundle."""

    @classmethod
    def load(cls, root: str | Path | Traversable, *, integrity_sha256: str | None = None) -> ContractBundle:
        """Load a complete bundle from a directory or importlib resource root.

        Resource trees are copied into a bounded in-memory snapshot. This supports
        installed and zip-imported SDK package data without extracting files into
        the package directory, where ordinary Python bytecode could change the
        authenticated inventory.
        """
        try:
            if isinstance(root, (str, os.PathLike)):
                base: Path | Traversable = Path(root)
                if base.is_symlink() or not base.is_dir():
                    raise ContractError("integrity")
                base = base.resolve(strict=True)
                snapshots = _snapshot_tree(base)
            else:
                base = root
                snapshots = _snapshot_resources(base)
            manifest_bytes = snapshots.get("operation-manifest.json")
            integrity_bytes = snapshots.get("integrity.json")
            if manifest_bytes is None or integrity_bytes is None:
                raise ContractError("integrity")
            manifest = _parse_json_bytes(manifest_bytes)
            integrity = _parse_json_bytes(integrity_bytes)
            _validate_document(integrity, _format_schema("integrity.schema.json"), "integrity")
            if integrity_sha256 is not None:
                if (not isinstance(integrity_sha256, str) or
                        sha256(integrity_bytes).hexdigest() != integrity_sha256):
                    raise ContractError("integrity")
            snapshot_inventory = {name: sha256(data).hexdigest()
                                  for name, data in snapshots.items() if name != "integrity.json"}
            if not isinstance(integrity.get("files"), dict) or snapshot_inventory != integrity["files"]:
                raise ContractError("integrity")
            schema_bytes = snapshots.get(_safe_relative(manifest["schemaBundle"]["path"]))
            if schema_bytes is None:
                raise ContractError("integrity")
            bundle = _parse_json_bytes(schema_bytes)
            validate_bundle(bundle)
            if manifest["schemaDialect"] != DIALECT or bundle.get("$schema") != DIALECT:
                raise ContractError("manifest")
            resources = {item["$id"]: item for item in bundle["$defs"].values()}
            wrapped = dict(bundle)
            wrapped["resources"] = resources
            _manifest_checks(manifest, wrapped)
            toolset_bytes = snapshots.get("mcp-tools.json")
            mappings = manifest.get("mcpTools", {})
            if toolset_bytes is None:
                if mappings:
                    raise ContractError("manifest")
                projected_tools: list[dict[str, Any]] = []
            else:
                projected_tools = _toolset_checks(
                    _parse_json_bytes(toolset_bytes), manifest, manifest_bytes,
                    schema_bytes, resources,
                    integrity_digest_supplied=integrity_sha256 is not None,
                )
            registry = Registry(retrieve=_no_remote).with_resources(
                (identifier, Resource.from_contents(schema)) for identifier, schema in resources.items())
            validators = {identifier: Draft202012Validator(schema, registry=registry)
                          for identifier, schema in resources.items()}
            instance = cls()
            instance.root = base
            instance.manifest = manifest
            instance.schemas = resources
            instance.validators = validators
            instance.mcp_tools = projected_tools
            return instance
        except ContractError:
            raise
        except Exception:
            raise ContractError("manifest") from None

    def validate_schema(self, identifier: str, value: Any) -> Any:
        validator = self.validators.get(identifier) if isinstance(identifier, str) else None
        if validator is None:
            raise ContractError("schema_profile")
        try:
            if not _finite_json(value):
                raise ContractError("schema_value")
            validator.validate(value)
            return value
        except ContractError:
            raise
        except Exception:
            # Instance paths can contain attacker-controlled object keys. Never
            # attach a jsonschema exception or its instance path to public errors.
            raise ContractError("schema_value") from None

    def _operation(self, operation: str) -> dict[str, Any]:
        if not isinstance(operation, str) or operation not in self.manifest["operations"]:
            raise ContractError("operation")
        return self.manifest["operations"][operation]

    def _validate_body(self, identifier: str | None, value: Any, cap: int | None) -> Any:
        if identifier is None:
            if value is not ABSENT:
                raise ContractError("schema_value")
            return ABSENT
        if value is ABSENT:
            raise ContractError("schema_value")
        if cap is None:
            raise ContractError("manifest")
        if _canonical_size(value) > cap:
            raise ContractError("body_limit")
        return self.validate_schema(identifier, value)

    def validate_request(self, operation: str, value: Any) -> Any:
        item = self._operation(operation)
        return self._validate_body(item["request"], value, item["requestMaxBytes"])

    def validate_response(self, operation: str, http_status: int, value: Any) -> Any:
        item = self._operation(operation)
        if type(http_status) is not int:
            raise ContractError("status")
        status = str(http_status)
        if status not in item["responses"]:
            raise ContractError("status")
        return self._validate_body(item["responses"][status], value,
                                   item["responseMaxBytes"][status])

    def validate_parameter(self, operation: str, location: str, name: str, value: Any) -> Any:
        item = self._operation(operation)
        if not isinstance(location, str) or not isinstance(name, str):
            raise ContractError("parameter")
        matches = [parameter for parameter in item["parameters"]
                   if parameter["in"] == location and parameter["name"] == name]
        if len(matches) != 1:
            raise ContractError("parameter")
        return self.validate_schema(matches[0]["schema"], value)
