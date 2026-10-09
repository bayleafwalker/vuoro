"""Native reserve replay through the real shell and pinned PostgreSQL owner."""
from dataclasses import replace
import uuid

from fastapi.testclient import TestClient
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from .sprintctl_binding import SprintctlProvider


@pytest.fixture
def native_reserve_owner(request):
    url = request.config.getoption("--lease-pg-url")
    if url is None:
        pytest.skip("requires configured disposable PostgreSQL owner")
    from sprintctl.vuoro_adapter import register_work_catalog
    owner = SprintctlProvider(url)
    identity = Identity(actor="A", environment="lease-conformance",
        authorities=frozenset({"work:read", "work:write", "work:evidence"}),
        principal_id="github:100:0", workspace_id="lease-conformance",
        repo_ids=frozenset({owner.store.repo_id}))
    identities = {"bound": identity,
        "reader": replace(identity, authorities=frozenset({"work:read"})),
        "unbound": replace(identity, principal_id=None, workspace_id=None),
        "other": replace(identity, principal_id="github:200:0")}
    registry = CatalogRegistry()
    register_work_catalog(registry, owner.app)
    try:
        with TestClient(create_app(settings=ServiceSettings(environment_name="lease-conformance"),
            registry=registry, identity_resolver=StaticBearerIdentityResolver(identities))) as client:
            item = int(owner.new_subject())
            args = {"item_id": item, "actor": "A", "session_id": "http-reserve",
                "expected_revision": owner.pg.item_release_revision(owner.store, item),
                "acceptance_contract": {"review_required": True, "effect_verification_required": True}}
            def invoke(arguments=args, *, token="bound", key="http-reserve-key", operation="work.reservation.reserve-v1"):
                envelope = {"schema_version": "invocation/v1", "request_id": uuid.uuid4().hex,
                    "repo_id": owner.store.repo_id, "catalog_revision": registry.revision,
                    "operation": operation, "arguments": arguments}
                if key is not None: envelope["idempotency_key"] = key
                return client.post("/api/invoke/v1", headers={"Authorization": "Bearer " + token,
                    "X-Vuoro-Client-Protocol": "1"}, json=envelope)
            yield owner, args, invoke
    finally:
        owner.close()


def counts(owner):
    with owner.store.conn.cursor() as cursor:
        cursor.execute("SELECT (SELECT count(*) FROM reservation WHERE repo_id=%s) AS rows, "
            "(SELECT count(*) FROM work_idempotency_ledger WHERE repo_id=%s AND tool='reservation.reserve-v1') AS keys, "
            "(SELECT count(*) FROM event WHERE repo_id=%s AND event_type='reservation.reserved') AS events",
            (owner.store.repo_id,) * 3)
        return dict(cursor.fetchone())


@pytest.mark.essential_safety
def test_native_reserve_http_forwards_envelope_key_and_replays_owner_receipt(native_reserve_owner):
    owner, args, invoke = native_reserve_owner
    first, replay = invoke(), invoke()
    assert first.status_code == replay.status_code == 200
    initial = first.json()["result"]["reservation"]
    current = replay.json()["result"]["reservation"]
    assert not initial["replayed"] and current["replayed"]
    assert initial["id"] == current["id"]
    assert initial["admission_snapshot"] == current["admission_snapshot"]
    assert initial["last_activity_at"] == current["last_activity_at"]
    assert counts(owner) == {"rows": 1, "keys": 1, "events": 1}
    response = invoke({"release_digest": current["release_digest"]}, key=None, operation="work.read.release")
    assert response.status_code == 200
    release = response.json()["result"]["release"]
    assert release["item_revision"] == args["expected_revision"]
    assert release["acceptance_contract"]["effect_verification_required"]
    changed = invoke({**args, "session_id": "changed"})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency-conflict"
    assert counts(owner) == {"rows": 1, "keys": 1, "events": 1}


@pytest.mark.essential_safety
@pytest.mark.parametrize("token,key,status,code", [
    ("reader", "http-reserve-key", 403, "authority-required"),
    ("unbound", "http-reserve-key", 403, "identity-unbound"),
    ("bound", None, 400, "idempotency-key-required"),
])
def test_native_reserve_http_refuses_before_any_effect(native_reserve_owner, token, key, status, code):
    owner, args, invoke = native_reserve_owner
    response = invoke(token=token, key=key)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert counts(owner) == {"rows": 0, "keys": 0, "events": 0}
    assert not owner.pg.list_releases(owner.store, args["item_id"])


@pytest.mark.essential_safety
def test_native_reserve_http_same_key_different_principal_uses_distinct_namespace(native_reserve_owner):
    owner, args, invoke = native_reserve_owner
    first, other = invoke(), invoke(token="other")
    assert first.status_code == other.status_code == 200
    a, b = [r.json()["result"]["reservation"] for r in (first, other)]
    assert a["id"] != b["id"] and not b["replayed"]
    assert b["conflict"] and b["conflict_severity"] == "warning"
    assert counts(owner) == {"rows": 2, "keys": 2, "events": 2}
    with owner.store.conn.cursor() as cursor:
        cursor.execute("SELECT principal_id FROM work_idempotency_ledger WHERE repo_id=%s AND tool='reservation.reserve-v1' ORDER BY principal_id", (owner.store.repo_id,))
        assert [r["principal_id"] for r in cursor.fetchall()] == ["github:100:0", "github:200:0"]
