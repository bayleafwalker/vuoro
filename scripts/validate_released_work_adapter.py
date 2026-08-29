"""Exercise the pinned Sprintctl wheel through Vuoro's real invocation shell."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile

import httpx

from sprintctl import db
from sprintctl.application import (
    ProjectMemberApplication,
    ProjectWorkApplication,
    WorkApplication,
)
from sprintctl.vuoro_adapter import catalog_operation_specs, register_work_catalog
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


_EXPECTED_ADAPTER_KIT = (
    "https://github.com/bayleafwalker/vuoro/releases/download/"
    "vuoro-adapter-kit-v0.1.1/vuoro_adapter_kit-0.1.1-py3-none-any.whl"
)
_EXPECTED_WORK_OPERATION_COUNT = 46
_EXPECTED_WORK_METADATA_SHA256 = (
    "c6759b011ab71b65699126a49db48a69ddd760e11758095ace3f3bc46c9ad713"
)
_EXPECTED_FOUR_DOMAIN_REVISION = (
    "fe7e53b64b16ffb14bb27976698b82433fae85a12ea58bd13eda52e8032dd1ce"
)


def _work_pin(manifest_path: Path) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "vuoro-composition/v3":
        raise SystemExit("unsupported composition schema_version")
    descriptor = next(item for item in manifest["runtime_descriptors"] if item["domain"] == "work")
    pin = next(lock for lock in manifest["release_locks"] if lock["lock_id"] == descriptor["lock_id"])
    if pin.get("lock_kind") != "adapter":
        raise SystemExit("work descriptor primary lock must be an adapter")
    if descriptor.get("dependency_lock_ids") != ["vuoro-adapter-kit"]:
        raise SystemExit("work descriptor must identify exactly the adapter-kit dependency")
    locks = {lock["lock_id"]: lock for lock in manifest["release_locks"]}
    dependency = locks.get("vuoro-adapter-kit")
    if dependency is None or dependency.get("lock_kind") != "shared-dependency":
        raise SystemExit("work dependency must be the shared adapter-kit lock")
    if dependency.get("artifact_url") != _EXPECTED_ADAPTER_KIT:
        raise SystemExit("work dependency is not the released adapter-kit wheel")
    if descriptor.get("adapter_module") != "sprintctl.vuoro_adapter":
        raise SystemExit("work descriptor must use the owner adapter module")
    if descriptor.get("register") != "register_work_catalog":
        raise SystemExit("work descriptor must use the owner registration entrypoint")
    if descriptor.get("api_version") != "work-api/v1":
        raise SystemExit("work descriptor changed its API compatibility contract")
    if descriptor.get("schema_version") != "work-schema/v1":
        raise SystemExit("work descriptor changed its schema compatibility contract")
    return pin


def _assert_owner_metadata() -> None:
    specs = catalog_operation_specs(resource_schema_available=False)
    assert len(specs) == _EXPECTED_WORK_OPERATION_COUNT
    encoded = json.dumps(specs, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == _EXPECTED_WORK_METADATA_SHA256


async def _exercise() -> None:
    with tempfile.TemporaryDirectory(prefix="vuoro-released-work-") as directory:
        connection = db.get_connection(Path(directory) / "work.db")
        db.init_db(connection)
        sprint_id = db.create_sprint(connection, "Released wheel", status="active")
        work = WorkApplication(
            repo_id="sprintctl",
            store=connection,
            backend=db,
            ingest_records=lambda records: [],
            arbitrate_command=lambda record, credentials: None,
            list_records=lambda after, limit: [],
            list_decisions=lambda after, limit: [],
        )
        capability_id = "mcap:00000000-0000-4000-8000-000000000001"
        connection.execute(
            "INSERT INTO maintenance_capability(capability_id,envelope_id,envelope_digest,"
            "envelope_json,plan_ref,operator_identity,not_before,expires_at,state,revision,"
            "next_sequence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                capability_id, "released-envelope", "0" * 64, "{}",
                "artifact:sha256:" + "0" * 64, "release-gate:reader",
                "2026-08-13T00:00:00Z", "2026-08-14T00:00:00Z", "prepared", 1,
                1, "2026-08-13T00:00:00Z", "2026-08-13T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO maintenance_resource(resource_ref,capability_id,recovery_floor,current_position) "
            "VALUES(?,?,?,?)",
            (
                "smr1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                capability_id, 0, 1,
            ),
        )
        connection.commit()
        project = ProjectWorkApplication(
            project_id="released-project",
            members=(ProjectMemberApplication("sprintctl", work),),
            canonical_binding={
                "project_id": "released-project",
                "home_repo": "agentops",
                "backlog_repos": ["sprintctl"],
            },
        )
        registry = CatalogRegistry()
        register_work_catalog(registry, work, project_application=project)
        # The initialized SQLite owner exposes the additive resource contract.
        assert registry.get("work.maintenance.resource.prepare") is not None
        resource_operation = registry.get("work.maintenance.resource.prepare")
        assert resource_operation is not None
        assert callable(resource_operation.result_decoder)
        assert resource_operation.result_decoder({"repo_id": "sprintctl", "capability_id": capability_id}) == {
            "schema_version": "resource-reference/v1",
            "owner": "work",
            "resource_kind": "work.maintenance-capability",
            "reference": "smr1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "revision": "sprintctl-maintenance-revision-1",
        }
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
                        authorities=frozenset({"work:read"}),
                        repo_ids=frozenset({"sprintctl"}),
                    ),
                    "project-reader-token": Identity(
                        actor="release-gate:project-reader",
                        environment="released-wheel-gate",
                        authorities=frozenset({"work:project-read"}),
                        repo_ids=frozenset({"sprintctl"}),
                    ),
                    "unprivileged-token": Identity(
                        actor="release-gate:unprivileged",
                        environment="released-wheel-gate",
                        repo_ids=frozenset({"sprintctl"}),
                    ),
                }
            ),
        )
        request = {
            "schema_version": "invocation/v1",
            "request_id": "released-wheel-accepted",
            "operation": "work.read.sprints",
            "arguments": {},
            "catalog_revision": registry.revision,
            "repo_id": "sprintctl",
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
            assert body["result"]["repo_id"] == "sprintctl"
            assert [row["id"] for row in body["result"]["sprints"]] == [sprint_id]

            project_request = {
                **request,
                "request_id": "released-wheel-project",
                "operation": "work.project.sprints",
                "repo_id": None,
            }
            project = await client.post(
                "/api/invoke/v1",
                headers={**protocol, "Authorization": "Bearer project-reader-token"},
                json=project_request,
            )
            assert project.status_code == 200, project.text
            assert project.json()["result"]["project"]["project_id"] == "released-project"
            assert project.json()["result"]["repositories"][0]["origin_repo"] == "sprintctl"

            forbidden = await client.post(
                "/api/invoke/v1",
                headers={**protocol, "Authorization": "Bearer unprivileged-token"},
                json={**request, "request_id": "released-wheel-forbidden"},
            )
            assert forbidden.status_code == 403, forbidden.text
            assert forbidden.json()["error"]["code"] == "authority-required"

            malformed = await client.post(
                "/api/invoke/v1",
                headers={**protocol, "Authorization": "Bearer reader-token"},
                json={
                    **request,
                    "request_id": "released-wheel-malformed",
                    "arguments": {"unsupported": True},
                },
            )
            assert malformed.status_code == 422, malformed.text
            assert malformed.json()["error"]["code"] == "schema-validation-failed"
        connection.close()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit(
            "usage: validate_released_work_adapter.py MANIFEST WHEEL_DIRECTORY"
        )
    manifest_path, wheel_directory = map(Path, argv)
    pin = _work_pin(manifest_path)
    wheel = wheel_directory / pin["artifact_url"].rsplit("/", 1)[-1]
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == pin["artifact_sha256"]
    assert importlib.metadata.version(pin["distribution"]) == pin["distribution_version"]
    _assert_owner_metadata()
    asyncio.run(_exercise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
