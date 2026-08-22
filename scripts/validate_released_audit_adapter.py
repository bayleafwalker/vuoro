"""Exercise the pinned Auditctl wheel through Vuoro's real invocation shell."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from typing import Any

import httpx

import auditctl.vuoro_adapter as audit_module
from auditctl.vuoro_adapter import VuoroAuditAdapter, catalog_operation_specs
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


_EXPECTED_ADAPTER_KIT = (
    "https://github.com/bayleafwalker/vuoro/releases/download/"
    "vuoro-adapter-kit-v0.1.1/vuoro_adapter_kit-0.1.1-py3-none-any.whl"
)
_EXPECTED_SCHEMA_RUNTIME = (
    "https://github.com/bayleafwalker/vuoro/releases/download/"
    "vuoro-schema-runtime-v0.1.0/vuoro_schema_runtime-0.1.0-py3-none-any.whl"
)
_EXPECTED_SCHEMA_RUNTIME_REQUIREMENT = (
    f"vuoro-schema-runtime @ {_EXPECTED_SCHEMA_RUNTIME}#sha256="
    "b66c9357c99aa9e1a7353991ce54105a8621958ecfac47f8c121d80b90b77912"
)


def _audit_pins(manifest_path: Path) -> tuple[dict, dict, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "vuoro-composition/v3":
        raise SystemExit("unsupported composition schema_version")
    descriptor = next(
        item for item in manifest["runtime_descriptors"] if item["domain"] == "audit"
    )
    locks = {lock["lock_id"]: lock for lock in manifest["release_locks"]}
    if descriptor["lock_id"] not in locks:
        raise SystemExit("audit descriptor has no primary release lock")
    if descriptor["dependency_lock_ids"] != [
        "vuoro-adapter-kit", "vuoro-schema-runtime"
    ]:
        raise SystemExit(
            "audit descriptor must identify exactly the adapter-kit and schema-runtime dependencies"
        )
    if descriptor["adapter_module"] != "auditctl.vuoro_adapter":
        raise SystemExit("audit descriptor must use the owner adapter module")
    if descriptor["register"] != "VuoroAuditAdapter.register":
        raise SystemExit("audit descriptor must use the owner adapter entrypoint")
    if descriptor["api_version"] != "audit/v1" or descriptor["schema_version"] != "audit-schema/v1":
        raise SystemExit("audit descriptor changed its compatibility contract")
    adapter = locks[descriptor["lock_id"]]
    adapter_kit = locks[descriptor["dependency_lock_ids"][0]]
    schema_runtime = locks[descriptor["dependency_lock_ids"][1]]
    if adapter.get("lock_kind") != "adapter":
        raise SystemExit("audit descriptor primary lock must be an adapter")
    if any(
        dependency.get("lock_kind") != "shared-dependency"
        for dependency in (adapter_kit, schema_runtime)
    ):
        raise SystemExit("audit dependencies must be shared dependencies")
    if adapter_kit.get("artifact_url") != _EXPECTED_ADAPTER_KIT:
        raise SystemExit("audit dependency is not the released adapter-kit wheel")
    if schema_runtime.get("artifact_url") != _EXPECTED_SCHEMA_RUNTIME:
        raise SystemExit("audit dependency is not the released schema-runtime wheel")
    requirements = importlib.metadata.requires(adapter["distribution"]) or []
    if _EXPECTED_SCHEMA_RUNTIME_REQUIREMENT not in requirements:
        raise SystemExit("audit owner metadata does not require the locked schema runtime")
    return adapter, adapter_kit, schema_runtime


def _assert_wheel(pin: dict, wheel_directory: Path) -> None:
    wheel = wheel_directory / pin["artifact_url"].rsplit("/", 1)[-1]
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != pin["artifact_sha256"]:
        raise SystemExit(f"{pin['distribution']}: wheel checksum does not match manifest")
    installed = importlib.metadata.version(pin["distribution"])
    if installed != pin["distribution_version"]:
        raise SystemExit(
            f"{pin['distribution']}: installed {installed!r}, "
            f"manifest requires {pin['distribution_version']!r}"
        )


class _ConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        return None


def _observation() -> dict[str, Any]:
    return {
        "ingest_offset": 1,
        "origin_stream_id": "40b89732-b2c7-4f60-98c2-199a960c2a20",
        "origin_seq": 1,
        "event_id": "ad:00000000000000000000000001",
        "schema_version": 1,
        "record_class": "observation",
        "event_type": "released-wheel-gate",
        "actor": "release-gate",
        "runtime_session_id": None,
        "occurred_at": "2026-08-13T00:00:00Z",
        "basis_revision": "a" * 40,
        "correlation_id": None,
        "causation_id": None,
        "payload": {
            "summary": "Released audit adapter",
            "detail": None,
            "refs": [],
            "source": "release-gate",
            "metadata": {},
        },
        "payload_sha256": "b" * 64,
        "record_sha256": "c" * 64,
        "producer_created_at": "2026-08-13T00:00:00Z",
        "ingested_at": "2026-08-13T00:00:00Z",
        "receipt_id": "c780bd9f-47c1-47fb-b4c6-21a90bf5e241",
    }


def _assert_catalog_invariant(registry: CatalogRegistry) -> None:
    expected = {
        spec["name"]: {
            **{key: value for key, value in spec.items() if key != "_handler_name"},
            # Vuoro's service definition defaults unscoped operations to false;
            # the owner adapter intentionally omits that transport default.
            "repo_scoped": False,
        }
        for spec in catalog_operation_specs()
    }
    registered = {
        operation.name: operation.model_dump(mode="json")
        for operation in registry.catalog().operations
    }
    if registered != expected:
        raise AssertionError("released audit catalog differs from owner catalog metadata")
    if set(registered) != {
        "audit.observation.submit",
        "audit.receipt.lookup",
        "audit.observation.list",
        "audit.stream.status",
        "audit.schema.compatibility",
    }:
        raise AssertionError("released audit catalog is not fully populated")


async def _exercise() -> None:
    calls: list[dict[str, Any]] = []

    def fake_list_observations(conn: object, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [_observation()]

    # The gate deliberately exercises invocation and catalog behavior without
    # running migration SQL or requiring a PostgreSQL service.
    original_list = audit_module.list_observations
    audit_module.list_observations = fake_list_observations
    try:
        adapter = VuoroAuditAdapter(connection_factory=_ConnectionContext, schema="audit")
        registry = CatalogRegistry()
        adapter.register(registry)
        _assert_catalog_invariant(registry)
        before_revision = registry.revision
        before_catalog = registry.catalog().model_dump(mode="json")
        app = create_app(
            settings=ServiceSettings(
                environment_name="released-wheel-gate",
                environment_class="development",
                compatibility_state="compatible",
            ),
            registry=registry,
            identity_resolver=StaticBearerIdentityResolver(
                {
                    "reader-token": Identity(
                        actor="release-gate:reader",
                        environment="released-wheel-gate",
                        authorities=frozenset({"audit.observation.read"}),
                    ),
                    "unprivileged-token": Identity(
                        actor="release-gate:unprivileged",
                        environment="released-wheel-gate",
                    ),
                }
            ),
        )
        request = {
            "schema_version": "invocation/v1",
            "request_id": "released-audit-accepted",
            "operation": "audit.observation.list",
            "arguments": {"limit": 50},
            "catalog_revision": registry.revision,
        }
        protocol = {"X-Vuoro-Client-Protocol": "1"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            accepted = await client.post(
                "/api/invoke/v1",
                headers={**protocol, "Authorization": "Bearer reader-token"},
                json=request,
            )
            assert accepted.status_code == 200, accepted.text
            body = accepted.json()
            assert body["status"] == "accepted"
            assert body["result"]["watermark"] == 1
            assert body["result"]["observations"][0]["event_id"] == (
                "ad:00000000000000000000000001"
            )

            forbidden = await client.post(
                "/api/invoke/v1",
                headers={**protocol, "Authorization": "Bearer unprivileged-token"},
                json={**request, "request_id": "released-audit-forbidden"},
            )
            assert forbidden.status_code == 403, forbidden.text
            assert forbidden.json()["error"]["code"] == "authority-required"

            malformed = await client.post(
                "/api/invoke/v1",
                headers={**protocol, "Authorization": "Bearer reader-token"},
                json={
                    **request,
                    "request_id": "released-audit-malformed",
                    "arguments": {"unsupported": True},
                },
            )
            assert malformed.status_code == 422, malformed.text
        assert calls == [{
            "schema": "audit",
            "after_offset": 0,
            "limit": 50,
            "event_type": None,
            "origin_stream_id": None,
        }]
        assert registry.revision == before_revision
        assert registry.catalog().model_dump(mode="json") == before_catalog
    finally:
        audit_module.list_observations = original_list


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: validate_released_audit_adapter.py MANIFEST WHEEL_DIRECTORY")
    manifest, wheel_directory = map(Path, argv)
    pins = _audit_pins(manifest)
    for pin in pins:
        _assert_wheel(pin, wheel_directory)
    asyncio.run(_exercise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
