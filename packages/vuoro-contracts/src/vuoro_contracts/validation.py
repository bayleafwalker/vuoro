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
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
_STATUS = re.compile(r"^[1-5][0-9]{2}$")
_SCHEMA_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


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


def _finite_json(value: Any, *, depth: int = 0) -> bool:
    if depth > 256:
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
        return all(_finite_json(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(type(key) is str
                   and not any(0xD800 <= ord(character) <= 0xDFFF for character in key)
                   and _finite_json(item, depth=depth + 1)
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
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant,
                           parse_float=_float)
        if not _finite_json(value):
            raise ContractError("json")
        return value
    except ContractError:
        raise
    except Exception:
        raise ContractError("json") from None


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
            registry = Registry(retrieve=_no_remote).with_resources(
                (identifier, Resource.from_contents(schema)) for identifier, schema in resources.items())
            validators = {identifier: Draft202012Validator(schema, registry=registry)
                          for identifier, schema in resources.items()}
            instance = cls()
            instance.root = base
            instance.manifest = manifest
            instance.schemas = resources
            instance.validators = validators
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
