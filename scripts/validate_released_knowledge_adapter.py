"""Exercise the released Kctl knowledge catalog through Vuoro's service shell."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import httpx

from kctl.vuoro import catalog_operation_specs, register_operations
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


def _knowledge_pins(manifest_path: Path) -> tuple[dict, dict, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "vuoro-composition/v3":
        raise SystemExit("unsupported composition schema_version")
    descriptor = next(
        item for item in manifest["runtime_descriptors"] if item["domain"] == "knowledge"
    )
    locks = {lock["lock_id"]: lock for lock in manifest["release_locks"]}
    if descriptor["lock_id"] not in locks:
        raise SystemExit("knowledge descriptor has no primary release lock")
    if descriptor["dependency_lock_ids"] != [
        "vuoro-adapter-kit", "vuoro-schema-runtime"
    ]:
        raise SystemExit(
            "knowledge descriptor must identify exactly the adapter-kit and schema-runtime dependencies"
        )
    adapter = locks[descriptor["lock_id"]]
    adapter_kit = locks[descriptor["dependency_lock_ids"][0]]
    schema_runtime = locks[descriptor["dependency_lock_ids"][1]]
    if adapter.get("lock_kind") != "adapter":
        raise SystemExit("knowledge descriptor primary lock must be an adapter")
    if any(
        dependency.get("lock_kind") != "shared-dependency"
        for dependency in (adapter_kit, schema_runtime)
    ):
        raise SystemExit("knowledge dependencies must be shared dependencies")
    if adapter_kit.get("artifact_url") != _EXPECTED_ADAPTER_KIT:
        raise SystemExit("knowledge dependency is not the released adapter-kit wheel")
    if schema_runtime.get("artifact_url") != _EXPECTED_SCHEMA_RUNTIME:
        raise SystemExit("knowledge dependency is not the released schema-runtime wheel")
    requirements = importlib.metadata.requires(adapter["distribution"]) or []
    if _EXPECTED_SCHEMA_RUNTIME_REQUIREMENT not in requirements:
        raise SystemExit("knowledge owner metadata does not require the locked schema runtime")
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


def _candidate() -> dict[str, object]:
    return {
        "candidate_id": "11111111-1111-4111-8111-111111111111",
        "repo_id": "agentops",
        "local_candidate_id": 1,
        "source_event_id": 1,
        "source_sprint_id": 1,
        "source_item_id": None,
        "source_track": None,
        "source_actor": None,
        "source_type": None,
        "source_created_at": None,
        "source_payload": {},
        "event_type": "released-wheel-gate",
        "candidate_kind": "durable",
        "summary": "Released knowledge adapter",
        "detail": None,
        "tags": [],
        "confidence": None,
        "status": "candidate",
        "content_digest": "sha256:" + "a" * 64,
        "basis_git_revision": "b" * 40,
        "extracted_at": "2026-08-13T00:00:00Z",
        "imported_at": "2026-08-13T00:00:00Z",
    }


class StubKnowledgeApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_candidates(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list_candidates", kwargs))
        return {"candidates": [_candidate()], "count": 1, "limit": kwargs["limit"]}


def _assert_catalog_invariant(registry: CatalogRegistry) -> None:
    expected = {
        spec["name"]: {key: value for key, value in spec.items() if key != "_handler_name"}
        for spec in catalog_operation_specs()
    }
    registered = {
        operation.name: operation.model_dump(mode="json")
        for operation in registry.catalog().operations
    }
    if registered != expected:
        raise AssertionError("released knowledge catalog differs from owner catalog metadata")
    if set(registered) != {
        "knowledge.candidate.intake",
        "knowledge.candidate.list",
        "knowledge.candidate.show",
        "knowledge.candidate.approve",
        "knowledge.candidate.reject",
        "knowledge.publication-reference.record",
        "knowledge.publication-reference.supersede",
        "knowledge.publication-reference.list",
        "knowledge.publication-reference.show",
        "knowledge.schema.compatibility",
    }:
        raise AssertionError("released knowledge catalog is not fully populated")


async def _exercise() -> None:
    application = StubKnowledgeApplication()
    registry = CatalogRegistry()
    register_operations(registry, application=application)
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
                    authorities=frozenset({"knowledge.read"}),
                    repo_ids=frozenset({"agentops"}),
                ),
                "unprivileged-token": Identity(
                    actor="release-gate:unprivileged",
                    environment="released-wheel-gate",
                    repo_ids=frozenset({"agentops"}),
                ),
            }
        ),
    )
    request = {
        "schema_version": "invocation/v1",
        "request_id": "released-knowledge-accepted",
        "operation": "knowledge.candidate.list",
        "arguments": {"repo_id": "agentops", "limit": 50},
        "catalog_revision": registry.revision,
        "repo_id": "agentops",
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
        assert body["result"]["count"] == 1
        assert body["result"]["candidates"][0]["repo_id"] == "agentops"

        forbidden = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer unprivileged-token"},
            json={**request, "request_id": "released-knowledge-forbidden"},
        )
        assert forbidden.status_code == 403, forbidden.text
        assert forbidden.json()["error"]["code"] == "authority-required"

        malformed = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer reader-token"},
            json={
                **request,
                "request_id": "released-knowledge-malformed",
                "arguments": {"repo_id": "agentops", "unsupported": True},
            },
        )
        assert malformed.status_code == 422, malformed.text
    assert application.calls == [("list_candidates", {"repo_id": "agentops", "status": None, "candidate_kind": None, "limit": 50})]
    assert registry.revision == before_revision
    assert registry.catalog().model_dump(mode="json") == before_catalog


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit(
            "usage: validate_released_knowledge_adapter.py MANIFEST WHEEL_DIRECTORY"
        )
    manifest, wheel_directory = Path(argv[0]), Path(argv[1])
    pins = _knowledge_pins(manifest)
    for pin in pins:
        _assert_wheel(pin, wheel_directory)
    asyncio.run(_exercise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
