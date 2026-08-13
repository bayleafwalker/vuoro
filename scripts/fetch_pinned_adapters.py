"""Fetch and verify the immutable adapter wheels named by a composition manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen


_ARTIFACT_FIELDS = {
    "source_repository", "source_revision", "artifact_url", "artifact_sha256",
    "distribution", "distribution_version",
}
_LOCK_KINDS = {"adapter", "owner-dependency", "shared-dependency"}
_CANONICAL_VUORO_SOURCE_REPOSITORY = "https://github.com/bayleafwalker/vuoro"
_SHARED_DEPENDENCY_DISTRIBUTIONS = {"vuoro-schema-runtime", "vuoro-adapter-kit"}
_REQUIRED_DOMAINS = {"work", "execution", "knowledge", "audit"}
_DESCRIPTOR_FIELDS = {
    "domain", "lock_id", "dependency_lock_ids", "adapter_module", "register",
    "api_version", "schema_version",
}
_RELEASE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _release_wheel_filename(source_repository: str, artifact_url: str) -> str:
    try:
        source = urlsplit(source_repository)
        artifact = urlsplit(artifact_url)
    except ValueError as error:
        raise SystemExit("malformed release URL") from error
    for value, parsed in ((source_repository, source), (artifact_url, artifact)):
        if (parsed.scheme != "https" or parsed.hostname != "github.com"
                or parsed.netloc != "github.com" or parsed.query or parsed.fragment
                or "%" in value):
            raise SystemExit("release artifacts require canonical GitHub HTTPS URLs")
    source_parts = source.path.strip("/").split("/")
    artifact_parts = artifact.path.strip("/").split("/")
    if (len(source_parts) != 2 or len(artifact_parts) != 6
            or artifact_parts[:2] != source_parts
            or artifact_parts[2:4] != ["releases", "download"]
            or any(not _RELEASE_SEGMENT.fullmatch(part) for part in source_parts)
            or not _RELEASE_SEGMENT.fullmatch(artifact_parts[4])
            or artifact_parts[4] in {".", ".."}
            or not _RELEASE_SEGMENT.fullmatch(artifact_parts[5])
            or not artifact_parts[5].endswith(".whl")):
        raise SystemExit("artifact URL must identify one canonical GitHub release wheel")
    return artifact_parts[5]


def artifact_pins(manifest: object) -> list[tuple[str, dict[str, str]]]:
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "release_locks", "runtime_descriptors"
    }:
        raise SystemExit("invalid composition manifest")
    if manifest["schema_version"] != "vuoro-composition/v3":
        raise SystemExit("unsupported composition schema_version")
    locks = manifest["release_locks"]
    if not isinstance(locks, list) or not isinstance(manifest["runtime_descriptors"], list):
        raise SystemExit("composition release locks and runtime descriptors must be arrays")
    pins: list[tuple[str, dict[str, str]]] = []
    by_id: dict[str, dict[str, str]] = {}
    seen_distributions: set[str] = set()
    seen_filenames: set[str] = set()
    for pin in locks:
        if not isinstance(pin, dict) or set(pin) != _ARTIFACT_FIELDS | {"lock_id", "lock_kind"}:
            raise SystemExit("release lock fields do not match the v3 contract")
        if not all(isinstance(pin[key], str) and pin[key] for key in _ARTIFACT_FIELDS | {"lock_id", "lock_kind"}):
            raise SystemExit("invalid release lock")
        if pin["lock_kind"] not in _LOCK_KINDS:
            raise SystemExit("invalid release lock kind")
        if not re.fullmatch(r"[0-9a-f]{40}", pin["source_revision"]):
            raise SystemExit("invalid source revision")
        if not re.fullmatch(r"[0-9a-f]{64}", pin["artifact_sha256"]):
            raise SystemExit("invalid artifact digest")
        distribution = pin["distribution"]
        filename = _release_wheel_filename(pin["source_repository"], pin["artifact_url"])
        if distribution in seen_distributions:
            raise SystemExit(f"duplicate distribution: {distribution}")
        if filename in seen_filenames:
            raise SystemExit(f"artifact filename collision: {filename}")
        lock_id = pin["lock_id"]
        if lock_id in by_id:
            raise SystemExit(f"duplicate lock identifier: {lock_id}")
        seen_distributions.add(distribution)
        seen_filenames.add(filename)
        by_id[lock_id] = pin
        pins.append((lock_id, pin))

    descriptors = manifest["runtime_descriptors"]
    if not all(isinstance(descriptor, dict) for descriptor in descriptors):
        raise SystemExit("runtime descriptors must be objects")
    if len(descriptors) != len(_REQUIRED_DOMAINS):
        raise SystemExit("composition contains duplicate runtime domains")
    if any(
        set(descriptor) != _DESCRIPTOR_FIELDS
        or not isinstance(descriptor.get("domain"), str)
        or not descriptor["domain"]
        for descriptor in descriptors
    ):
        raise SystemExit("runtime descriptor fields do not match the v3 contract")
    if {descriptor["domain"] for descriptor in descriptors} != _REQUIRED_DOMAINS:
        raise SystemExit("composition must pin exactly work, execution, knowledge, and audit")
    referenced: list[str] = []
    primary_references: dict[str, int] = {}
    dependency_references: dict[str, int] = {}
    for descriptor in descriptors:
        if not all(
            isinstance(descriptor[field], str) and descriptor[field]
            for field in _DESCRIPTOR_FIELDS - {"dependency_lock_ids"}
        ):
            raise SystemExit("runtime descriptor values must be non-empty strings")
        dependency_ids = descriptor["dependency_lock_ids"]
        if (
            not isinstance(dependency_ids, list)
            or not all(isinstance(lock_id, str) and lock_id for lock_id in dependency_ids)
            or len(dependency_ids) != len(set(dependency_ids))
            or descriptor["lock_id"] in dependency_ids
        ):
            raise SystemExit(f"{descriptor['domain']}: invalid dependency lock references")
        primary_id = descriptor["lock_id"]
        primary = by_id.get(primary_id)
        if primary is None:
            raise SystemExit(f"{descriptor['domain']}: unknown primary release lock")
        if primary["lock_kind"] != "adapter":
            raise SystemExit(f"{descriptor['domain']}: primary release lock must be an adapter")
        primary_references[primary_id] = primary_references.get(primary_id, 0) + 1
        referenced.append(primary_id)
        for dependency_id in dependency_ids:
            dependency = by_id.get(dependency_id)
            if dependency is None:
                raise SystemExit(f"{descriptor['domain']}: unknown dependency release lock")
            dependency_references[dependency_id] = dependency_references.get(dependency_id, 0) + 1
            referenced.append(dependency_id)
            if dependency["lock_kind"] == "adapter":
                raise SystemExit(f"{descriptor['domain']}: adapter release locks cannot be dependencies")
            if dependency["lock_kind"] == "owner-dependency":
                if dependency["source_repository"] != primary["source_repository"]:
                    raise SystemExit(
                        f"{descriptor['domain']}: owner dependencies must come from the same owner repository"
                    )
            elif dependency["source_repository"] != _CANONICAL_VUORO_SOURCE_REPOSITORY:
                raise SystemExit(
                    f"{dependency_id}: shared dependencies must come from the canonical Vuoro repository"
                )
            elif dependency["distribution"] not in _SHARED_DEPENDENCY_DISTRIBUTIONS:
                raise SystemExit(f"{dependency_id}: shared dependency distribution is not allowed")
    for lock_id, lock in by_id.items():
        primary_count = primary_references.get(lock_id, 0)
        dependency_count = dependency_references.get(lock_id, 0)
        if lock["lock_kind"] == "adapter" and (primary_count != 1 or dependency_count):
            raise SystemExit("adapter release locks must be exclusive primaries")
        if lock["lock_kind"] == "owner-dependency" and dependency_count != 1:
            raise SystemExit(
                f"{lock_id}: owner dependency must be referenced by exactly one runtime descriptor"
            )
        if lock["lock_kind"] == "shared-dependency" and dependency_count == 0:
            raise SystemExit(f"{lock_id}: shared dependency must be referenced by a runtime descriptor")
    if set(referenced) != set(by_id):
        raise SystemExit("composition contains an orphan release lock")
    return pins


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: fetch_pinned_adapters.py MANIFEST DESTINATION")
    manifest_path, destination = map(Path, argv)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    for label, adapter in artifact_pins(manifest):
        url = adapter["artifact_url"]
        target = destination / _release_wheel_filename(
            adapter["source_repository"], adapter["artifact_url"]
        )
        with urlopen(url) as response:  # noqa: S310 - manifest allows only GitHub URLs
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != adapter["artifact_sha256"]:
            raise SystemExit(f"checksum mismatch for {label}")
        if target.exists():
            existing = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing != digest:
                raise SystemExit(f"existing artifact mismatch for {target.name}")
        else:
            target.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
