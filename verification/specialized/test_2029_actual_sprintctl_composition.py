"""Published Sprintctl 0.2.22 through Vuoro's real generic composition seams."""

from __future__ import annotations

import asyncio
import httpx
import pytest

from actionq.vuoro import register_operations as register_actionq_operations
from sprintctl import db
from sprintctl.application import WorkApplication
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_client import AsyncVuoroClient, Profile
from vuoro_service.app import RESOURCE_NOT_FOUND_RESPONSE, ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition import (
    _bind_work_resource_visibility,
    _load_work_resource_observation_authorizer,
)
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_published_sprintctl_adapter_denial_and_generic_observation(
    tmp_path, monkeypatch
):
    connection = db.get_connection(tmp_path / "owner.db")
    db.init_db(connection)
    connection.execute(
        "INSERT INTO maintenance_capability(capability_id,envelope_id,envelope_digest,"
        "envelope_json,plan_ref,operator_identity,not_before,expires_at,state,revision,"
        "next_sequence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,1,?,?)",
        (
            "mcap:test", "envelope", "0" * 64, "{}",
            "artifact:sha256:" + "0" * 64, "allowed",
            "2026-08-02T19:00:00Z", "2026-08-02T20:00:00Z", "reconciled",
            "2026-08-02T19:00:00Z", "2026-08-02T19:04:00Z",
        ),
    )
    connection.commit()
    work = WorkApplication(
        repo_id="sprintctl", store=connection, backend=db,
        ingest_records=lambda records: [],
        arbitrate_command=lambda record, credentials: None,
        list_records=lambda after, limit: [],
        list_decisions=lambda after, limit: [],
    )
    resource_ref = work._maintenance_resource_store().record_current("mcap:test")
    registry = CatalogRegistry()
    register_work_catalog(registry, work)

    class ActionQReadFixture:
        def list_actions(self, **arguments):
            return []

    register_actionq_operations(registry, application=ActionQReadFixture())
    policy_path = tmp_path / "observers.json"
    policy_path.write_text(
        '{"schema_version":"vuoro-work-resource-observers/v1",'
        '"grants":[{"actor":"allowed","repo_ids":["sprintctl"]}]}'
    )
    _bind_work_resource_visibility(
        registry, work, _load_work_resource_observation_authorizer(policy_path),
    )
    handler_calls = []
    owner_invoke = WorkApplication.invoke

    def counted_invoke(self, operation, arguments, context):
        if operation.startswith("work.maintenance.resource."):
            handler_calls.append(operation)
        return owner_invoke(self, operation, arguments, context)

    monkeypatch.setattr(WorkApplication, "invoke", counted_invoke)
    identity = lambda actor: Identity(
        actor=actor, environment="vuoro-dev",
        authorities=frozenset({"work:maintenance", "execution.read"}),
        repo_ids=frozenset({"sprintctl"}),
    )
    app = create_app(
        settings=ServiceSettings(
            environment_name="vuoro-dev", environment_class="development",
            compatibility_state="compatible",
        ),
        registry=registry,
        identity_resolver=StaticBearerIdentityResolver({
            "denied": identity("denied"), "allowed": identity("allowed"),
        }),
    )
    request = {
        "schema_version": "invocation/v1", "request_id": "denied",
        "operation": "work.maintenance.resource.get",
        "arguments": {"resource_ref": resource_ref},
        "catalog_revision": registry.revision, "basis_revision": None,
        "idempotency_key": None, "repo_id": "sprintctl",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        denied = await raw.post(
            "/api/invoke/v1",
            headers={"Authorization": "Bearer denied", "X-Vuoro-Client-Protocol": "1"},
            json=request,
        )
    assert denied.status_code == 404
    assert denied.content == RESOURCE_NOT_FOUND_RESPONSE
    assert handler_calls == []

    async with AsyncVuoroClient(
        Profile("test", "http://test", "allowed", "vuoro-dev"),
        lambda _: "allowed", transport=transport,
    ) as client:
        snapshot, actionq_result = await asyncio.gather(
            client.get(
                "work.maintenance-capability", resource_ref, repo_id="sprintctl"
            ),
            client.invoke("execution.action.list", {}),
        )
        changes = await client.changes(
            "work.maintenance-capability", resource_ref, snapshot["cursor"],
            wait_seconds=0, repo_id="sprintctl",
        )
    async with AsyncVuoroClient(
        Profile("test", "http://test", "allowed", "vuoro-dev"),
        lambda _: "allowed", transport=transport,
    ) as client:
        terminal = await client.wait(
            "work.maintenance-capability", resource_ref, timeout=1,
            repo_id="sprintctl",
        )
    assert snapshot["terminal"] is True
    assert actionq_result == []
    assert changes["events"] == []
    assert terminal["terminal"] is True
    assert handler_calls == [
        "work.maintenance.resource.get",
        "work.maintenance.resource.changes",
        "work.maintenance.resource.get",
    ]
    connection.close()
