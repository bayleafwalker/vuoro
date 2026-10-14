"""Report what one interpreter's pinned composition admits; read-only.

Runs inside an immutable service image (``python -I -``) or in-process. It
opens no database and no network connection. Output: one JSON object.
"""

import hashlib
import json
import os


def _version(name):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _read_json(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None, None
    try:
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except ValueError:
        return None, hashlib.sha256(raw).hexdigest()


def _schema_ranges():
    ranges = {}
    try:
        from sprintctl import pg_migrations as work

        ranges["work"] = {
            "minimum": int(work.MINIMUM_SCHEMA_VERSION),
            "maximum": int(work.MAXIMUM_SCHEMA_VERSION),
            "current_code": int(getattr(work, "CURRENT_SCHEMA_VERSION", work.MAXIMUM_SCHEMA_VERSION)),
        }
    except Exception as error:  # noqa: BLE001 - report, never raise
        ranges["work"] = {"error": type(error).__name__}
    try:
        from auditctl import central_schema as audit

        ranges["audit"] = {
            "minimum": int(audit.MIN_RUNTIME_SCHEMA_VERSION),
            "maximum": int(audit.MAX_RUNTIME_SCHEMA_VERSION),
            "current_code": int(getattr(audit, "CURRENT_SCHEMA_VERSION", audit.MAX_RUNTIME_SCHEMA_VERSION)),
        }
    except Exception as error:  # noqa: BLE001
        ranges["audit"] = {"error": type(error).__name__}
    return ranges


def _catalog():
    try:
        from auditctl.vuoro_adapter import VuoroAuditAdapter
        from sprintctl.vuoro_adapter import register_work_catalog
        from vuoro_service.catalog import CatalogRegistry

        class WorkStub:
            @staticmethod
            def maintenance_resource_schema_available():
                return False

        registry = CatalogRegistry()
        register_work_catalog(registry, WorkStub())
        VuoroAuditAdapter(connection_factory=lambda: None).register(registry)
        catalog = registry.catalog().model_dump(mode="json")
    except Exception as error:  # noqa: BLE001
        return {"error": type(error).__name__}
    counts = {}
    for operation in catalog["operations"]:
        counts[operation["owning_domain"]] = counts.get(operation["owning_domain"], 0) + 1
    return {
        "revision": registry.revision,
        "total": len(catalog["operations"]),
        "domain_counts": counts,
        "operations": sorted(operation["name"] for operation in catalog["operations"]),
    }


def observe(env):
    manifest_path = env.get("VUORO_COMPOSITION_MANIFEST") or os.environ.get(
        "VUORO_COMPOSITION_MANIFEST", "/opt/vuoro/composition/adapter-pins.json"
    )
    attestation_path = env.get("VUORO_INSTALLED_COMPOSITION_PATH") or os.environ.get(
        "VUORO_INSTALLED_COMPOSITION_PATH", "/opt/vuoro/composition/installed-composition.json"
    )
    manifest, manifest_sha256 = _read_json(manifest_path)
    attestation, _ = _read_json(attestation_path)
    observation = {
        "schema_version": "vuoro-ops-image-composition/v1",
        "service_version": _version("vuoro-service"),
        "distributions": {
            name: _version(name)
            for name in ("sprintctl", "auditctl", "vuoro-adapter-kit", "vuoro-schema-runtime")
        },
        "manifest_sha256": manifest_sha256,
        "manifest_locks": [
            {"distribution": lock.get("distribution"), "distribution_version": lock.get("distribution_version")}
            for lock in (manifest or {}).get("release_locks", [])
        ],
        "attestation": None if attestation is None else {
            "verified": attestation.get("verified"),
            "manifest_sha256": attestation.get("manifest_sha256"),
        },
        "schema_ranges": _schema_ranges(),
    }
    if env.get("VUORO_OPS_DERIVE_CATALOG") == "1":
        observation["catalog"] = _catalog()
    return observation


if __name__ == "__main__":
    print(json.dumps(observe(dict(os.environ)), sort_keys=True))
