"""Validate immutable Python release metadata and built-wheel identity."""

from __future__ import annotations

import argparse
from email.parser import Parser
import json
from pathlib import Path
import re
import zipfile

import jsonschema


PACKAGE_NAMES = {
    "vuoro-client", "vuoro-service",
    "vuoro-schema-runtime", "vuoro-adapter-kit", "vuoro-demo",
    "vuoro-workspace", "vuoro-contracts",
}
TAG = re.compile(
    r"^(vuoro-client|vuoro-service|vuoro-schema-runtime|vuoro-adapter-kit|vuoro-demo|vuoro-workspace|vuoro-contracts)-v(.+)$"
)
MAX_METADATA = 1024 * 1024
IMMUTABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?$")


def wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise ValueError(f"{path}: expected exactly one wheel METADATA file")
        info = archive.getinfo(metadata_paths[0])
        if info.file_size > MAX_METADATA:
            raise ValueError(f"{path}: wheel METADATA exceeds the 1 MiB limit")
        with archive.open(info) as metadata_file:
            raw_bytes = metadata_file.read(MAX_METADATA + 1)
        if len(raw_bytes) > MAX_METADATA or len(raw_bytes) != info.file_size:
            raise ValueError(f"{path}: wheel METADATA exceeds the 1 MiB limit or is truncated")
        raw_metadata = raw_bytes.decode("utf-8")
        metadata = Parser().parsestr(raw_metadata)
        if len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1:
            raise ValueError(f"{path}: wheel metadata must contain exactly one Name and Version header")
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ValueError(f"{path}: wheel metadata lacks Name or Version")
    return name, version


def validate_tenant_schema_map_data(mapping: dict) -> None:
    """Validate semantic invariants that JSON Schema cannot compare across fields/items."""
    releases = mapping.get("releases") if isinstance(mapping, dict) else None
    if not isinstance(releases, list):
        raise ValueError("tenant schema map releases must be an array")
    versions = [row.get("service_version") for row in releases]
    if len(versions) != len(set(versions)):
        raise ValueError("tenant schema map service_version values must be unique")
    if versions != sorted(versions, key=lambda value: tuple(map(int, value.split(".")))):
        raise ValueError("tenant schema map service versions must be strictly ordered")
    for row in releases:
        accepted = row["accepted_schema_range"]
        minimum, maximum = accepted["minimum"], accepted["maximum"]
        target, lowest = row["work_schema_target"], row["lowest_supported_schema"]
        if minimum != lowest:
            raise ValueError(f"{row['service_version']}: accepted range minimum must equal lowest_supported_schema")
        if not minimum <= target <= maximum:
            raise ValueError(f"{row['service_version']}: target must be within accepted schema range")


def validate_service_schema_map(path: Path, service_version: str) -> None:
    required = {
        "vuoro_service/tenant-runtime-schema-map.json",
        "vuoro_service/tenant-runtime-schema-map.schema.json",
    }
    with zipfile.ZipFile(path) as archive:
        missing = required - set(archive.namelist())
        if missing:
            raise ValueError(f"{path}: service wheel is missing tenant schema map files: {sorted(missing)}")
        try:
            mapping = json.loads(archive.read("vuoro_service/tenant-runtime-schema-map.json"))
            schema = json.loads(archive.read("vuoro_service/tenant-runtime-schema-map.schema.json"))
            jsonschema.validate(mapping, schema)
            validate_tenant_schema_map_data(mapping)
        except (json.JSONDecodeError, UnicodeDecodeError, jsonschema.ValidationError, jsonschema.SchemaError, TypeError) as exc:
            raise ValueError(f"{path}: invalid tenant schema map: {exc}") from exc
    if not isinstance(mapping, dict) or not isinstance(mapping.get("releases"), list):
        raise ValueError(f"{path}: tenant schema map releases must be an array")
    versions = [row["service_version"] for row in mapping["releases"]]
    if service_version not in versions:
        raise ValueError(f"{path}: tenant schema map has no entry for service version {service_version}")


def validate(paths: list[Path], tag: str | None = None, *, release: bool = False) -> None:
    if not paths:
        raise ValueError("at least one wheel is required")
    seen: set[str] = set()
    for path in paths:
        name, version = wheel_metadata(path)
        if name not in PACKAGE_NAMES:
            raise ValueError(f"{path}: unexpected distribution {name!r}")
        if name == "vuoro-service":
            validate_service_schema_map(path, version)
        if name in seen:
            raise ValueError(f"duplicate wheel for {name}")
        seen.add(name)
        if release and not IMMUTABLE_VERSION.fullmatch(version):
            raise ValueError(f"{name}: release wheel has mutable development version {version!r}")
        if tag:
            match = TAG.fullmatch(tag)
            if not match or match.group(1) != name or match.group(2) != version:
                raise ValueError(f"{name}: wheel version {version!r} does not match tag {tag!r}")
    if tag and len(paths) != 1:
        raise ValueError("a package release tag must publish exactly one distribution")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    validate(args.wheels, args.tag, release=args.release or args.tag is not None)
    print("release contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
