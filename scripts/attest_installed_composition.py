"""Attest that the installed distributions are exactly the pinned composition.

A build log showing the right wheels being fetched is not evidence that they are
what ended up installed: a later resolution step can quietly replace a pinned
distribution with a registry copy. This runs after installation, compares the
live installed metadata against the manifest, and writes the result into the
image so the running container can be asked what it actually contains.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, distribution, version
import json
from pathlib import Path
import sys
import re
from urllib.parse import urlsplit

_LOCK_KINDS = {"adapter", "owner-dependency", "shared-dependency"}
_DOMAINS = {"work", "execution", "knowledge", "audit"}
_SHARED_SOURCE = "https://github.com/bayleafwalker/vuoro"
_SHARED_DISTRIBUTIONS = {"vuoro-schema-runtime", "vuoro-adapter-kit"}
_LOCK_FIELDS = {"lock_id", "lock_kind", "source_repository", "source_revision", "artifact_url", "artifact_sha256", "distribution", "distribution_version"}
_DESCRIPTOR_FIELDS = {"domain", "lock_id", "dependency_lock_ids", "adapter_module", "register", "api_version", "schema_version"}
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _filename(lock: dict) -> str:
    source = urlsplit(lock["source_repository"])
    artifact = urlsplit(lock["artifact_url"])
    if any(
        parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.netloc != "github.com"
        or parsed.query or parsed.fragment or "%" in value
        for value, parsed in ((lock["source_repository"], source), (lock["artifact_url"], artifact))
    ):
        raise SystemExit("release artifacts require canonical GitHub HTTPS URLs")
    source_parts = source.path.strip("/").split("/")
    artifact_parts = artifact.path.strip("/").split("/")
    if (
        len(source_parts) != 2 or len(artifact_parts) != 6
        or artifact_parts[:2] != source_parts or artifact_parts[2:4] != ["releases", "download"]
        or any(not _SEGMENT.fullmatch(part) for part in source_parts)
        or not _SEGMENT.fullmatch(artifact_parts[4]) or artifact_parts[4] in {".", ".."}
        or not _SEGMENT.fullmatch(artifact_parts[5]) or not artifact_parts[5].endswith(".whl")
    ):
        raise SystemExit("artifact URL must identify one canonical GitHub release wheel")
    if not re.fullmatch(r"[0-9a-f]{40}", lock["source_revision"]):
        raise SystemExit("invalid source revision")
    if not re.fullmatch(r"[0-9a-f]{64}", lock["artifact_sha256"]):
        raise SystemExit("invalid artifact digest")
    return artifact_parts[5]


def _pinned(manifest: dict) -> list[dict]:
    entries: list[dict] = []
    if manifest.get("schema_version") != "vuoro-composition/v3":
        raise SystemExit("unsupported composition schema_version")
    locks = manifest.get("release_locks")
    if not isinstance(locks, list):
        raise SystemExit("release locks must be an array")
    seen_ids: set[str] = set()
    seen_distributions: set[str] = set()
    seen_filenames: set[str] = set()
    by_id: dict[str, dict] = {}
    for lock in locks:
        if not isinstance(lock, dict):
            raise SystemExit("release locks must be objects")
        if set(lock) != {
            "lock_id", "lock_kind", "source_repository", "source_revision",
            "artifact_url", "artifact_sha256", "distribution", "distribution_version",
        }:
            raise SystemExit("release lock fields do not match the v3 contract")
        if not all(isinstance(lock[field], str) and lock[field] for field in _LOCK_FIELDS):
            raise SystemExit("release lock values must be non-empty strings")
        if lock["lock_kind"] not in _LOCK_KINDS:
            raise SystemExit("invalid release lock kind")
        filename = _filename(lock)
        if filename in seen_filenames:
            raise SystemExit(f"artifact filename collision: {filename}")
        if lock["lock_id"] in seen_ids or lock["distribution"] in seen_distributions:
            raise SystemExit("duplicate release lock identifier or distribution")
        seen_ids.add(lock["lock_id"])
        seen_distributions.add(lock["distribution"])
        seen_filenames.add(filename)
        by_id[lock["lock_id"]] = lock
        entries.append(
            {
                "lock_id": lock["lock_id"],
                "lock_kind": lock["lock_kind"],
                "distribution": lock["distribution"],
                "expected_version": lock["distribution_version"],
                "artifact_url": lock["artifact_url"],
                "artifact_sha256": lock["artifact_sha256"],
                "source_repository": lock["source_repository"],
                "source_revision": lock["source_revision"],
            }
        )
    descriptors = manifest.get("runtime_descriptors")
    if not isinstance(descriptors, list) or len(descriptors) != len(_DOMAINS):
        raise SystemExit("composition must contain exactly four runtime descriptors")
    if any(not isinstance(item, dict) or set(item) != _DESCRIPTOR_FIELDS for item in descriptors):
        raise SystemExit("runtime descriptor fields do not match the v3 contract")
    if {item["domain"] for item in descriptors} != _DOMAINS:
        raise SystemExit("composition must pin exactly work, execution, knowledge, and audit")
    referenced: list[str] = []
    primary_counts: dict[str, int] = {}
    dependency_counts: dict[str, int] = {}
    for descriptor in descriptors:
        deps = descriptor["dependency_lock_ids"]
        if not isinstance(deps, list) or len(deps) != len(set(deps)) or descriptor["lock_id"] in deps:
            raise SystemExit(f"{descriptor['domain']}: invalid dependency lock references")
        primary = by_id.get(descriptor["lock_id"])
        if primary is None or primary["lock_kind"] != "adapter":
            raise SystemExit(f"{descriptor['domain']}: primary release lock must be an adapter")
        primary_counts[primary["lock_id"]] = primary_counts.get(primary["lock_id"], 0) + 1
        referenced.append(primary["lock_id"])
        for dependency_id in deps:
            dependency = by_id.get(dependency_id)
            if dependency is None:
                raise SystemExit(f"{descriptor['domain']}: unknown dependency release lock")
            dependency_counts[dependency_id] = dependency_counts.get(dependency_id, 0) + 1
            referenced.append(dependency_id)
            if dependency["lock_kind"] == "adapter":
                raise SystemExit("adapter release locks cannot be dependencies")
            if dependency["lock_kind"] == "owner-dependency":
                if dependency["source_repository"] != primary["source_repository"]:
                    raise SystemExit("owner dependencies must come from the same owner repository")
            elif dependency["source_repository"] != _SHARED_SOURCE:
                raise SystemExit("shared dependencies must come from the canonical Vuoro repository")
            elif dependency["distribution"] not in _SHARED_DISTRIBUTIONS:
                raise SystemExit("shared dependency distribution is not allowed")
    for lock_id, lock in by_id.items():
        primary_count = primary_counts.get(lock_id, 0)
        dependency_count = dependency_counts.get(lock_id, 0)
        if lock["lock_kind"] == "adapter" and (primary_count != 1 or dependency_count):
            raise SystemExit("adapter release locks must be exclusive primaries")
        if lock["lock_kind"] == "owner-dependency" and dependency_count != 1:
            raise SystemExit(f"{lock_id}: owner dependency must be referenced by exactly one runtime descriptor")
        if lock["lock_kind"] == "shared-dependency" and not dependency_count:
            raise SystemExit(f"{lock_id}: shared dependency must be referenced by a runtime descriptor")
    if set(referenced) != set(by_id):
        raise SystemExit("composition contains an orphan release lock")
    return entries


def _installed_files_digest(distribution_name: str) -> tuple[str, int]:
    """Hash every installed file named by the wheel's installed RECORD.

    The lock verifies the wheel before installation; this second digest binds
    that lock to the actual files which Python will import at service startup.
    """
    installed = distribution(distribution_name)
    files = installed.files
    if not files:
        raise RuntimeError(f"{distribution_name}: installed RECORD is unavailable")
    entries: list[tuple[str, str]] = []
    for file in sorted(files, key=str):
        path = installed.locate_file(file)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimeError(f"{distribution_name}: installed file is unavailable: {file}") from error
        entries.append((str(file), digest))
    encoded = json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest(), len(entries)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 3:
        raise SystemExit(
            "usage: attest_installed_composition.py MANIFEST WHEEL_DIR OUTPUT"
        )
    manifest_path, wheel_dir, output = map(Path, argv)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    failures: list[str] = []
    attested: list[dict] = []
    for entry in _pinned(manifest):
        record = dict(entry)

        wheel = wheel_dir / entry["artifact_url"].rsplit("/", 1)[-1]
        try:
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        except OSError:
            digest = None
        record["wheel_present"] = digest is not None
        if digest != entry["artifact_sha256"]:
            failures.append(
                f"{entry['distribution']}: wheelhouse artifact missing or checksum mismatch"
            )

        try:
            installed = version(entry["distribution"])
        except PackageNotFoundError:
            installed = None
        record["installed_version"] = installed
        if installed != entry["expected_version"]:
            failures.append(
                f"{entry['distribution']}: installed {installed!r}, "
                f"composition pins {entry['expected_version']!r}"
            )
        try:
            files_digest, files_count = _installed_files_digest(entry["distribution"])
        except (PackageNotFoundError, RuntimeError) as error:
            failures.append(str(error))
            files_digest, files_count = None, 0
        record["installed_files_sha256"] = files_digest
        record["installed_files_count"] = files_count
        attested.append(record)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-installed-composition/v1",
                "verified": not failures,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "distributions": attested,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    if failures:
        for failure in failures:
            print(f"composition attestation failed: {failure}", file=sys.stderr)
        return 1
    print(f"composition attestation passed for {len(attested)} pinned distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
