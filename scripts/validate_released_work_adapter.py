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
from sprintctl.application import WorkApplication
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


def _work_pin(manifest_path: Path) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return next(pin for pin in manifest["adapters"] if pin["domain"] == "work")


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
        registry = CatalogRegistry()
        register_work_catalog(registry, work)
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
    asyncio.run(_exercise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
