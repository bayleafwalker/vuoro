#!/usr/bin/env python3
"""Check source workflow/matrix references; this never establishes qualification."""
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def validate(contract, composition):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    packages = contract.get("workflow_package", [])
    matrix = contract.get("support_matrix", [])
    require(packages and matrix, "workflow packages and support matrix must be present")
    ids = [p.get("id") for p in packages]
    require(all(ids) and len(ids) == len(set(ids)), "workflow package IDs must be unique")
    matrix_ids = [m.get("id") for m in matrix]
    require(all(matrix_ids) and len(matrix_ids) == len(set(matrix_ids)), "matrix IDs must be unique")
    domains = {c["domain"] for c in contract["capability"]}
    locks = {p["lock_id"] for p in composition["release_locks"]}
    runtime_locks = {d["lock_id"] for d in composition["runtime_descriptors"]}
    operations = {item["operation"] for item in contract["lifecycle"]}
    for package in packages:
        require(package.get("implementation_owner") and package.get("revision") and package.get("source") and package.get("entrypoint"), "workflow identity incomplete")
        require(package.get("required_capabilities") and set(package["required_capabilities"]) <= domains, "unknown required composed capability")
        require(package.get("unsupported_requirements"), "unresolved owner requirements must be explicit")
        require(package.get("environment_profiles") and package.get("acceptance_profile") and package.get("evidence"), "workflow requirements incomplete")
        require(package.get("authority") == "requirements-only-no-grant", "workflow package cannot grant authority")
        require(package.get("qualification") == "not-qualified", "workflow qualification is not established")
    for entry in matrix:
        require(entry.get("profile") and entry.get("composition_manifest") == contract["composition_manifest"], "matrix composition or profile mismatch")
        require(entry.get("component_lock_ids") and set(entry["component_lock_ids"]) == runtime_locks and runtime_locks <= locks, "matrix must reference exact composed locks")
        require(entry.get("workflow_packages") and set(entry["workflow_packages"]) <= set(ids), "unknown or missing workflow package")
        for field in ("native_runtimes", "operating_systems", "databases", "gateway_apis", "storage_backends", "qualification_references", "limits"):
            require(entry.get(field), f"matrix {field} must be explicit")
        require(entry.get("upgrade_lifecycle") in operations and entry.get("restore_lifecycle") in operations, "unknown lifecycle reference")
        require(entry.get("maintenance") and entry.get("end_of_support"), "maintenance status must be explicit")
        require(entry.get("qualification") == "not-qualified", "matrix qualification is not established")


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
