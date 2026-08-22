#!/usr/bin/env python3
"""Migrate the v3 reference composition into a v4 `shared` profile.

Freeze §5. A script rather than a hand-written file because the mapping is
mechanical and every field is derived from something v3 already records --
which is what makes "maps losslessly" checkable rather than asserted. Run
``--check`` to prove the checked-in profile is exactly what this mapping
produces from ``adapter-pins.json``; drift then fails a test instead of being
discovered when a deployment reads a stale pin.

The mapping, in the freeze's own table:

    release_locks[*]                          -> providers (artifact_kind wheel)
    runtime_descriptors[*].domain             -> dropped; a plane label is render-time
    runtime_descriptors[*].api_version        -> the capability contract id
    runtime_descriptors[*].{lock_id, adapter_module, register}
                                              -> an adapter record
    runtime_descriptors[*].dependency_lock_ids -> the provider's dependency closure
    implicit 1:1 descriptor<->lock            -> an explicit exclusive binding, scope global
    schema_version per descriptor             -> part of the deployment closure

Two fields have no v3 source and are therefore *computed*, not invented:

* ``configuration_digest`` is a sha256 over the provider's own v3 records --
  the release lock, plus the runtime descriptor where the provider is a
  primary. That is exactly "the effective configuration Vuoro pins for this
  provider" expressed as a digest, and it moves when any pin moves;
* ``attestation`` is the closure digest the validator recomputes (rule 2).

Two fields have a v3 source only for half the composition, and the script
records that rather than papering over it: ``operation_hashes`` exists as a
pinned catalog-metadata digest for work and execution
(``scripts/validate_released_{work,execution}_adapter.py``) and does not exist
for knowledge or audit. The migrated profile leaves those two absent, so rule 3
reports them, which is the honest state of v3's conformance evidence.

``migration_version`` is likewise absent throughout: v3 carries exactly one
schema version per descriptor, and splitting it into schema and migration
versions needs owner-side data that does not exist yet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "packages/vuoro-service/composition"
V3_MANIFEST = COMPOSITION / "adapter-pins.json"
V4_PROFILE = COMPOSITION / "profiles/shared.json"

# The one Vuoro protocol version the service shell speaks (`vuoro_client.PROTOCOL_VERSION`).
PROTOCOL_VERSION = "1"

# Pinned catalog-metadata digests, from the released-adapter validators that
# already assert them. Only two of the four domains have one.
OPERATION_HASHES = {
    "work-api/v1": "5988b1117763aa4f517724222f4530b948d9c088463eaca098e6b7c7036b9ff1",
    "execution/v1": "8d434e8b347e804c90e48a6598304be84b12f2a61ebc2dbed00a26053239a778",
}

# Where each domain's adapter is reached through the uniform construction
# protocol. The owner wheels do not expose build/register themselves, so the
# translation lives in the adapter kit -- one module per provider, named by the
# profile and imported by nothing in the service package.
SHIMS = {
    "work": "vuoro_adapter_kit.adapters.work",
    "execution": "vuoro_adapter_kit.adapters.execution",
    "knowledge": "vuoro_adapter_kit.adapters.knowledge",
    "audit": "vuoro_adapter_kit.adapters.audit",
}

# What each shim's build() reads, and the environment variable v3 already uses
# for it. Declared in the profile so that the service package names no DSN.
RUNTIME_SETTINGS = {
    "work": {"dsn": "VUORO_WORK_RUNTIME_DSN", "repository_id": "VUORO_WORK_REPOSITORY_ID"},
    "execution": {"dsn": "VUORO_EXECUTION_RUNTIME_DSN", "schema": "VUORO_EXECUTION_SCHEMA"},
    "knowledge": {"dsn": "VUORO_KNOWLEDGE_RUNTIME_DSN", "schema": "VUORO_KNOWLEDGE_SCHEMA"},
    "audit": {"dsn": "VUORO_AUDIT_RUNTIME_DSN", "schema": "VUORO_AUDIT_SCHEMA"},
}

ROLE = {
    "adapter": "adapter",
    "owner-dependency": "owner-dependency",
    "shared-dependency": "shared-dependency",
}


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def migrate(manifest: dict) -> dict:
    locks = {lock["lock_id"]: lock for lock in manifest["release_locks"]}
    descriptors = {item["lock_id"]: item for item in manifest["runtime_descriptors"]}

    providers = []
    for lock_id, lock in locks.items():
        descriptor = descriptors.get(lock_id)
        provider = {
            "provider_id": lock_id,
            # One release unit per lock, which is what v3's 1:1 descriptor-to-lock
            # rule guaranteed structurally and v4 now states.
            "release_unit": lock_id,
            "artifact_kind": "wheel",
            "role": ROLE[lock["lock_kind"]],
            "source_repository": lock["source_repository"],
            "source_revision": lock["source_revision"],
            "artifact": {
                "distribution": lock["distribution"],
                "distribution_version": lock["distribution_version"],
                "artifact_sha256": lock["artifact_sha256"],
                "artifact_url": lock["artifact_url"],
            },
            "capabilities": [descriptor["api_version"]] if descriptor else [],
        }
        if descriptor:
            provider["dependencies"] = list(descriptor["dependency_lock_ids"])
            closure = {
                "configuration_digest": _digest({"lock": lock, "descriptor": descriptor}),
                "schema_version": descriptor["schema_version"],
                "protocol_version": PROTOCOL_VERSION,
            }
            operation_hashes = OPERATION_HASHES.get(descriptor["api_version"])
            if operation_hashes:
                closure["operation_hashes"] = operation_hashes
            provider["closure"] = closure
            provider["closure"]["attestation"] = _digest({
                "artifact": provider["artifact"],
                "closure": {k: v for k, v in sorted(closure.items()) if v is not None},
            })
        else:
            provider["closure"] = {
                "configuration_digest": _digest({"lock": lock}),
            }
            provider["closure"]["attestation"] = _digest({
                "artifact": provider["artifact"],
                "closure": {"configuration_digest": provider["closure"]["configuration_digest"]},
            })
        providers.append(provider)

    adapters = []
    bindings = []
    for descriptor in manifest["runtime_descriptors"]:
        domain = descriptor["domain"]
        adapter_id = f"{domain}-catalog"
        adapters.append({
            "adapter_id": adapter_id,
            "provider_id": descriptor["lock_id"],
            "module": SHIMS[domain],
            "build": "build",
            "register": "register",
            "runtime_settings": RUNTIME_SETTINGS[domain],
        })
        bindings.append({
            "capability_id": descriptor["api_version"],
            "scope_kind": "global",
            "provider_id": descriptor["lock_id"],
            "adapter_id": adapter_id,
        })

    return {
        "schema_version": "vuoro-composition/v4",
        "profile": "shared",
        "providers": sorted(providers, key=lambda item: item["provider_id"]),
        "adapters": sorted(adapters, key=lambda item: item["adapter_id"]),
        "bindings": sorted(bindings, key=lambda item: item["capability_id"]),
        "migrated_from": {
            "schema_version": manifest["schema_version"],
            "manifest_sha256": hashlib.sha256(V3_MANIFEST.read_bytes()).hexdigest(),
            "equivalence_proof": "tests/test_composition_v4.py::test_v3_reference_composition_migrates_losslessly",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if the checked-in profile is not what this mapping produces")
    arguments = parser.parse_args(argv)

    manifest = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))
    produced = json.dumps(migrate(manifest), indent=2, sort_keys=False) + "\n"

    if arguments.check:
        current = V4_PROFILE.read_text(encoding="utf-8") if V4_PROFILE.is_file() else ""
        if current != produced:
            print("FAILED: the checked-in shared profile is not the migration of adapter-pins.json")
            return 1
        print("OK: shared profile matches the v3 migration")
        return 0

    V4_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    V4_PROFILE.write_text(produced, encoding="utf-8")
    print(f"wrote {V4_PROFILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
