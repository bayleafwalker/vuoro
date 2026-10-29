#!/usr/bin/env python3
"""Check source workflow/matrix references; this never establishes qualification."""
import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]

_SOURCE_REVISION = re.compile(r"[0-9a-f]{40}")
_ARTIFACT_SHA256 = re.compile(r"[0-9a-f]{64}")
_WHEEL_TAGS = re.compile(r"[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl")


def _normalized_distribution(value):
    return re.sub(r"[-_.]+", "_", value).lower()


def _require_text(value, message):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value


def _require_text_list(value, message):
    if not isinstance(value, list) or not value:
        raise ValueError(message)
    for item in value:
        _require_text(item, message)
    if len(value) != len(set(value)):
        raise ValueError(message)
    return value


def _require_records(value, message):
    if not isinstance(value, list) or not value:
        raise ValueError(message)
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError(message)
    return value


def _require_unique_field(entries, field, message):
    values = [_require_text(entry.get(field), message) for entry in entries]
    if len(values) != len(set(values)):
        raise ValueError(message)
    return values


def validate(contract, composition):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    if not isinstance(contract, dict):
        raise ValueError("distribution contract must be a mapping")
    if not isinstance(composition, dict):
        raise ValueError("composition manifest must be a mapping")

    require(
        contract.get("contract_status") == "candidate",
        "source contract status must remain candidate",
    )
    require(
        contract.get("supported_distribution") is False,
        "source contract must not advertise a supported distribution",
    )
    require(
        contract.get("qualification_status") == "pending",
        "source contract qualification must remain pending",
    )
    require(
        contract.get("deployment_status") == "unknown",
        "source contract deployment status must remain unknown",
    )

    packages = _require_records(
        contract.get("workflow_package"),
        "workflow_package must be a nonempty list of records",
    )
    matrix = _require_records(
        contract.get("support_matrix"),
        "support_matrix must be a nonempty list of records",
    )
    capabilities = _require_records(
        contract.get("capability"),
        "capability must be a nonempty list of records",
    )
    lifecycle = _require_records(
        contract.get("lifecycle"),
        "lifecycle must be a nonempty list of records",
    )
    release_locks = _require_records(
        composition.get("release_locks"),
        "release_locks must be a nonempty list of records",
    )
    runtime_descriptors = _require_records(
        composition.get("runtime_descriptors"),
        "runtime_descriptors must be a nonempty list of records",
    )

    ids = _require_unique_field(
        packages, "id", "workflow package IDs must be unique nonempty strings"
    )
    _require_unique_field(
        matrix, "id", "matrix IDs must be unique nonempty strings"
    )
    _require_unique_field(
        capabilities, "domain", "capability domains must be unique nonempty strings"
    )
    _require_unique_field(
        capabilities, "lock_id", "capability lock IDs must be unique nonempty strings"
    )
    _require_unique_field(
        lifecycle, "operation", "lifecycle operations must be unique nonempty strings"
    )
    _require_unique_field(
        release_locks, "lock_id", "release lock IDs must be unique nonempty strings"
    )
    _require_unique_field(
        runtime_descriptors,
        "lock_id",
        "runtime descriptor lock IDs must be unique nonempty strings",
    )
    _require_unique_field(
        runtime_descriptors,
        "domain",
        "runtime descriptor domains must be unique nonempty strings",
    )

    for capability in capabilities:
        for field in (
            "distribution",
            "pinned_version",
            "api_version",
            "schema_version",
            "capability_summary",
            "pin_maturity",
            "composition_maturity",
            "deployment_maturity",
            "user_qualification",
        ):
            _require_text(
                capability.get(field),
                f"capability {field} must be a nonempty string",
            )

    for entry in lifecycle:
        for field in ("source_status", "qualification", "boundary"):
            _require_text(
                entry.get(field), f"lifecycle {field} must be a nonempty string"
            )
        _require_text_list(
            entry.get("procedure"),
            "lifecycle procedure must be a nonempty list of unique strings",
        )

    for lock in release_locks:
        for field in (
            "lock_kind",
            "source_repository",
            "source_revision",
            "artifact_url",
            "artifact_sha256",
            "distribution",
            "distribution_version",
        ):
            _require_text(
                lock.get(field), f"release lock {field} must be a nonempty string"
            )
        lock_id = lock["lock_id"]
        if not _SOURCE_REVISION.fullmatch(lock["source_revision"]):
            raise ValueError(
                f"release lock {lock_id} source_revision must be 40 lowercase "
                "hex characters"
            )
        if not _ARTIFACT_SHA256.fullmatch(lock["artifact_sha256"]):
            raise ValueError(
                f"release lock {lock_id} artifact_sha256 must be 64 lowercase "
                "hex characters"
            )
        parsed = urlsplit(lock["artifact_url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                f"release lock {lock_id} artifact_url must be an HTTPS URL"
            )
        filename = unquote(parsed.path.rsplit("/", 1)[-1])
        prefix = (
            f"{_normalized_distribution(lock['distribution'])}-"
            f"{lock['distribution_version']}-"
        )
        if (
            not filename.startswith(prefix)
            or not _WHEEL_TAGS.fullmatch(filename[len(prefix):])
        ):
            raise ValueError(
                f"release lock {lock_id} artifact_url must name the declared "
                "distribution, version, and wheel tags"
            )

    for descriptor in runtime_descriptors:
        for field in (
            "domain",
            "adapter_module",
            "register",
            "api_version",
            "schema_version",
        ):
            _require_text(
                descriptor.get(field),
                f"runtime descriptor {field} must be a nonempty string",
            )
        _require_text_list(
            descriptor.get("dependency_lock_ids"),
            "runtime descriptor dependency_lock_ids must be a nonempty list of "
            "unique strings",
        )

    qualification = contract.get("qualification")
    if not isinstance(qualification, dict):
        raise ValueError("qualification must be a mapping")
    required_evidence = _require_text_list(
        qualification.get("required_evidence"),
        "qualification required_evidence must be a nonempty list of unique strings",
    )
    evidence_refs = set(required_evidence)
    contract_manifest = _require_text(
        contract.get("composition_manifest"),
        "composition manifest reference must be a nonempty string",
    )

    domains = {c["domain"] for c in capabilities}
    locks = {p["lock_id"] for p in release_locks}
    runtime_locks = {d["lock_id"] for d in runtime_descriptors}
    runtime_domains = {d["domain"] for d in runtime_descriptors}
    operations = {item["operation"] for item in lifecycle}

    require(
        domains == runtime_domains,
        "capabilities must cover exactly the composed runtime domains",
    )
    release_by_lock = {lock["lock_id"]: lock for lock in release_locks}
    descriptor_by_domain = {
        descriptor["domain"]: descriptor for descriptor in runtime_descriptors
    }
    for capability in capabilities:
        domain = capability["domain"]
        descriptor = descriptor_by_domain[domain]
        lock_id = capability["lock_id"]
        require(
            descriptor["lock_id"] == lock_id,
            f"capability {domain} lock does not match its runtime descriptor",
        )
        require(
            lock_id in release_by_lock,
            f"capability {domain} references an unknown release lock",
        )
        lock = release_by_lock[lock_id]
        require(
            capability["distribution"] == lock["distribution"],
            f"capability {domain} distribution does not match its release lock",
        )
        require(
            capability["pinned_version"] == lock["distribution_version"],
            f"capability {domain} pinned version does not match its release lock",
        )
        require(
            capability["api_version"] == descriptor["api_version"],
            f"capability {domain} API version does not match its runtime descriptor",
        )
        require(
            capability["schema_version"] == descriptor["schema_version"],
            f"capability {domain} schema version does not match its runtime descriptor",
        )
        require(
            capability["user_qualification"] == "not-qualified",
            f"capability {domain} user qualification is not established",
        )
        require(
            capability["deployment_maturity"] == "unknown",
            f"capability {domain} deployment maturity must remain unknown",
        )

    for package in packages:
        for field in (
            "revision",
            "implementation_owner",
            "source",
            "entrypoint",
            "acceptance_profile",
        ):
            _require_text(
                package.get(field), f"workflow {field} must be a nonempty string"
            )
        required = _require_text_list(
            package.get("required_capabilities"),
            "workflow required_capabilities must be a nonempty list of "
            "unique strings",
        )
        require(set(required) <= domains, "unknown required composed capability")
        _require_text_list(
            package.get("unsupported_requirements"),
            "unresolved owner requirements must be explicit",
        )
        _require_text_list(
            package.get("environment_profiles"),
            "workflow environment_profiles must be a nonempty list of "
            "unique strings",
        )
        _require_text_list(
            package.get("evidence"),
            "workflow evidence must be a nonempty list of unique strings",
        )
        require(
            package.get("authority") == "requirements-only-no-grant",
            "workflow package cannot grant authority",
        )
        require(
            package.get("qualification") == "not-qualified",
            "workflow qualification is not established",
        )

    for entry in matrix:
        profile = _require_text(
            entry.get("profile"), "matrix profile must be a nonempty string"
        )
        require(
            profile in {"hybrid", "managed-hosted", "self-hosted-reference"},
            "matrix profile is not supported",
        )
        require(
            entry.get("composition_manifest") == contract_manifest,
            "matrix composition or profile mismatch",
        )
        component_locks = _require_text_list(
            entry.get("component_lock_ids"),
            "matrix component_lock_ids must be a nonempty list of unique strings",
        )
        require(
            set(component_locks) == runtime_locks and runtime_locks <= locks,
            "matrix must reference exact composed locks",
        )
        workflow_packages = _require_text_list(
            entry.get("workflow_packages"),
            "matrix workflow_packages must be a nonempty list of unique strings",
        )
        require(
            set(workflow_packages) <= set(ids),
            "unknown or missing workflow package",
        )
        for field in (
            "native_runtimes",
            "operating_systems",
            "databases",
            "gateway_apis",
            "storage_backends",
            "qualification_references",
            "limits",
        ):
            _require_text_list(
                entry.get(field),
                f"matrix {field} must be a nonempty list of unique strings",
            )
        require(
            set(entry["qualification_references"]) <= evidence_refs,
            "matrix qualification reference is not required evidence",
        )
        upgrade = _require_text(
            entry.get("upgrade_lifecycle"),
            "matrix upgrade_lifecycle must be a nonempty string",
        )
        restore = _require_text(
            entry.get("restore_lifecycle"),
            "matrix restore_lifecycle must be a nonempty string",
        )
        require(
            upgrade in operations and restore in operations,
            "unknown lifecycle reference",
        )
        _require_text(
            entry.get("maintenance"), "maintenance status must be explicit"
        )
        _require_text(
            entry.get("end_of_support"), "maintenance status must be explicit"
        )
        require(
            entry.get("qualification") == "not-qualified",
            "matrix qualification is not established",
        )


def main():
    try:
        contract = tomllib.loads((ROOT / "deploy/reference-distribution.toml").read_text())
        composition = json.loads((ROOT / contract["composition_manifest"]).read_text())
        validate(contract, composition)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"workflow distribution invalid: {exc}", file=sys.stderr)
        return 1
    print("workflow distribution consistent; qualification not established")
    return 0


if __name__ == "__main__":
    sys.exit(main())
