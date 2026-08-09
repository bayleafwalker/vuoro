import asyncio

import httpx
import pytest

from vuoro_client import AsyncVuoroClient, Profile
from vuoro_client.errors import ClientIncompatibleError
from vuoro_client.errors import InvocationRejectedError
from vuoro_client.resources import ResourceChanges, ResourceReference, ResourceSnapshot


CATALOG = {
    "schema_version": "operation-catalog/v1", "revision": "catalog-resource-1",
    "operations": [
        {"name": name, "owning_domain": "execution", "input_schema": {}, "result_schema": {},
         "required_authority": "execution.read", "execution_semantics": "read",
         "idempotency": "not-allowed", "repo_scoped": False,
         "deprecation": {"deprecated": False, "replacement": None, "sunset_at": None},
         "required_client_schema_features": ["json-schema-draft-2020-12"]}
        for name in ("execution.action.get", "execution.action.changes")
    ],
    "resource_kinds": [{"resource_kind": "execution.action", "observation": {
        "snapshot_operation": "execution.action.get", "changes_operation": "execution.action.changes",
        "cursor_schema": "actionq-cursor/v1", "supports_terminality": True}}],
    "observation_transports": [{"transport": "bounded-long-poll", "maximum_wait_seconds": 30}],
}


def _profile():
    return Profile(name="test", endpoint="http://test", credential_ref="token:test")


def test_get_changes_are_catalog_driven_and_deduplicate_at_least_once_delivery():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=CATALOG)
        payload = __import__("json").loads(request.content)
        if payload["operation"].endswith(".get"):
            result = {"schema_version": "resource-snapshot/v1", "reference": "aqr1_opaque",
                      "revision": "opaque-r1", "cursor": "opaque-c1", "terminal": False,
                      "state": {"owner": "opaque"}}
        else:
            result = {"schema_version": "resource-changes/v1", "reference": "aqr1_opaque",
                      "next_cursor": "opaque-c2",
                      "events": [{"event_id": "opaque-e2", "terminal": False, "data": {}}, {"event_id": "opaque-e2", "terminal": False, "data": {}}]}
        return httpx.Response(200, json={"schema_version": "invocation-result/v1", "request_id": payload["request_id"], "status": "accepted", "result": result, "error": None})
    async def run():
        async with AsyncVuoroClient(_profile(), lambda _: "secret", transport=httpx.MockTransport(handler)) as client:
            snapshot = await client.get("execution.action", "aqr1_opaque")
            first = await client.changes("execution.action", "aqr1_opaque", snapshot["cursor"], wait_seconds=30)
            second = await client.changes("execution.action", "aqr1_opaque", snapshot["cursor"])
            assert [x["event_id"] for x in first["events"]] == ["opaque-e2"]
            assert second["events"] == []
    asyncio.run(run())
    assert all(b"aqr1_opaque" not in request.headers.get("authorization", "").encode() for request in requests)


def test_wait_bound_is_discovered_not_hard_coded():
    async def run():
        async with AsyncVuoroClient(_profile(), lambda _: "secret", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=CATALOG))) as client:
            with pytest.raises(ClientIncompatibleError, match="wait_seconds=31"):
                await client.changes("execution.action", "ref", "cursor", wait_seconds=31)
    asyncio.run(run())


def test_wait_rejects_resource_kind_without_owner_declared_terminality():
    client = AsyncVuoroClient(_profile(), lambda _: "secret")
    descriptor_value = {**CATALOG["resource_kinds"][0], "observation": {**CATALOG["resource_kinds"][0]["observation"], "supports_terminality": False}}
    async def descriptor(_kind): return CATALOG, descriptor_value
    client._resource_descriptor = descriptor
    async def run():
        try:
            with pytest.raises(ClientIncompatibleError, match="does not advertise terminality"):
                await client.wait("execution.action", "ref")
        finally:
            await client.aclose()
    asyncio.run(run())


def test_invalid_local_wait_never_reaches_transport():
    called = False
    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(500)
    async def run():
        async with AsyncVuoroClient(_profile(), lambda _: "secret", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await client.changes("execution.action", "ref", "cursor", wait_seconds=True)
    asyncio.run(run())
    assert called is False


def test_actual_actionq_goldens_are_accepted_by_owner_supplied_decoder():
    import json
    from pathlib import Path
    fixture = Path.cwd() / "verification/external/actionq-action-resource-owner-v1/protocol-responses.json"
    owner = json.loads(fixture.read_text())["responses"]
    ref = "aqr1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    def decode(value):
        body = value["body"]
        if body["schema_version"] == "resource-reference/v1":
            return {"schema_version": "resource-reference/v1", "owner": "execution", "resource_kind": "execution.action", "reference": body["resource_ref"], "revision": f"owner-revision:{body['revision']}"}
        if body["schema_version"] == "resource-snapshot/v1":
            return {"schema_version": "resource-snapshot/v1", "reference": body["resource_ref"], "revision": f"owner-revision:{body['revision']}", "cursor": body["cursor"], "terminal": body["projection"]["terminal"], "state": body["projection"]}
        return {"schema_version": "resource-changes/v1", "reference": body["resource_ref"], "next_cursor": body["cursor"], "events": [{"event_id": f"owner-change:{item['revision']}", "terminal": item["terminal"], "data": item} for item in body["changes"]]}
    reference = decode(owner["enqueue_success"])
    snapshot = decode(owner["snapshot"])
    changes = decode(owner["changes"])
    assert ResourceReference.model_validate(reference).reference == ref
    assert ResourceSnapshot.model_validate(snapshot).state["state"] == "claimed"
    event = ResourceChanges.model_validate(changes).events[-1]
    assert event.terminal is True and event.data["state"] == "completed"
    assert event.event_id == "owner-change:5"


def test_sprintctl_goldens_use_catalog_driven_get_changes_and_wait():
    import json
    from pathlib import Path

    freeze = json.loads(
        (Path.cwd() / "verification/plans/2029-sprintctl-maintenance-owner-goldens.json").read_text()
    )
    reference = ResourceReference.model_validate(freeze["goldens"]["reference"])
    snapshot = freeze["goldens"]["snapshot"]
    changes = freeze["goldens"]["changes"]
    operations = [
        {
            "name": name, "owning_domain": "work", "input_schema": {},
            "result_schema": {}, "required_authority": "work:maintenance",
            "execution_semantics": "read", "idempotency": "not-allowed",
            "repo_scoped": True,
            "deprecation": {"deprecated": False, "replacement": None, "sunset_at": None},
            "required_client_schema_features": ["json-schema-draft-2020-12"],
            "failure_disclosure": "resource-not-found/v1",
        }
        for name in (
            "work.maintenance.resource.get", "work.maintenance.resource.changes"
        )
    ]
    catalog = {
        "schema_version": "operation-catalog/v1", "revision": "catalog-work-1",
        "operations": operations,
        "resource_kinds": [{
            "resource_kind": "work.maintenance-capability",
            "observation": {
                "snapshot_operation": "work.maintenance.resource.get",
                "changes_operation": "work.maintenance.resource.changes",
                "cursor_schema": "sprintctl-maintenance-cursor/v1",
                "supports_terminality": True,
            },
        }],
        "observation_transports": [
            {"transport": "bounded-long-poll", "maximum_wait_seconds": 30}
        ],
    }
    calls = []
    get_count = 0

    def handler(request):
        nonlocal get_count
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=catalog)
        payload = json.loads(request.content)
        calls.append((payload["operation"], payload["arguments"]))
        if payload["operation"].endswith(".get"):
            get_count += 1
            result = (
                {**snapshot, "terminal": True, "state": changes["events"][-1]["data"]}
                if get_count == 3 else snapshot
            )
        else:
            result = changes
        return httpx.Response(200, json={
            "schema_version": "invocation-result/v1",
            "request_id": payload["request_id"], "status": "accepted",
            "result": result, "error": None,
        })

    async def run():
        transport = httpx.MockTransport(handler)
        async with AsyncVuoroClient(
            _profile(), lambda _: "secret", transport=transport
        ) as client:
            observed_snapshot = await client.get(
                reference.resource_kind, reference.reference
            )
            observed_changes = await client.changes(
                reference.resource_kind, reference.reference,
                observed_snapshot["cursor"], wait_seconds=0,
            )
        async with AsyncVuoroClient(
            _profile(), lambda _: "secret", transport=transport
        ) as client:
            terminal = await client.wait(reference.resource_kind, reference.reference)
        assert observed_snapshot["cursor"] == "sprintctl-maintenance-cursor-3"
        assert observed_changes["events"][-1]["terminal"] is True
        assert terminal["terminal"] is True

    asyncio.run(run())
    assert [name for name, _ in calls] == [
        "work.maintenance.resource.get",
        "work.maintenance.resource.changes",
        "work.maintenance.resource.get",
        "work.maintenance.resource.changes",
        "work.maintenance.resource.get",
    ]
    assert calls[-2][1]["wait_seconds"] == 30


def test_wait_recovers_expired_cursor_with_snapshot_and_never_mutates_owner():
    async def run():
        client = AsyncVuoroClient(_profile(), lambda _: "secret", transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        snapshots = iter([
            {"reference": "ref", "cursor": "old", "terminal": False, "state": {}},
            {"reference": "ref", "cursor": "fresh", "terminal": False, "state": {}},
            {"reference": "ref", "cursor": "terminal", "terminal": True, "state": {}},
        ])
        calls = []
        async def get(kind, ref, *, repo_id=None):
            calls.append(("get", kind, ref))
            return next(snapshots)
        async def changes(kind, ref, cursor, *, wait_seconds=0, repo_id=None):
            calls.append(("changes", kind, ref, cursor, wait_seconds))
            if cursor == "old":
                raise InvocationRejectedError("cursor_expired", "fresh snapshot", status_code=409)
            return {"reference": ref, "next_cursor": "after", "events": [{"event_id": "opaque-e9", "terminal": True}]}
        client.get, client.changes = get, changes
        async def descriptor(_kind): return CATALOG, CATALOG["resource_kinds"][0]
        client._resource_descriptor = descriptor
        try:
            result = await client.wait("execution.action", "ref", wait_seconds=30)
        finally:
            await client.aclose()
        assert result["terminal"] is True
        assert calls == [
            ("get", "execution.action", "ref"),
            ("changes", "execution.action", "ref", "old", 30),
            ("get", "execution.action", "ref"),
            ("changes", "execution.action", "ref", "fresh", 30),
            ("get", "execution.action", "ref"),
        ]
    asyncio.run(run())


def test_cross_batch_cursor_regression_is_rejected_and_transport_selection_is_emitted():
    observations = []
    client = AsyncVuoroClient(_profile(), lambda _: "secret", observation_hook=observations.append)
    async def descriptor(_kind):
        return CATALOG, CATALOG["resource_kinds"][0]
    replies = iter([
        {"schema_version": "resource-changes/v1", "reference": "ref", "next_cursor": "c2", "events": [{"event_id": "e2", "terminal": False, "data": {}}]},
        {"schema_version": "resource-changes/v1", "reference": "ref", "next_cursor": "c1", "events": []},
    ])
    async def invoke(*_args, **_kwargs):
        return next(replies)
    client._resource_descriptor, client.invoke = descriptor, invoke
    async def run():
        try:
            await client.changes("execution.action", "ref", "c1", wait_seconds=30)
            with pytest.raises(ClientIncompatibleError, match="cursor chain regressed"):
                await client.changes("execution.action", "ref", "c2", wait_seconds=30)
        finally:
            await client.aclose()
    asyncio.run(run())
    assert observations[0]["transport"] == "bounded-long-poll"


def test_wait_reconnects_on_disconnect_resumes_cursor_and_honors_overall_timeout():
    observations = []
    client = AsyncVuoroClient(_profile(), lambda _: "secret", observation_hook=observations.append)
    calls = 0
    async def get(_kind, ref, *, repo_id=None):
        return {"reference": ref, "revision": "r", "cursor": "c1", "terminal": calls > 1, "state": {}}
    async def changes(_kind, ref, cursor, *, wait_seconds=0, repo_id=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("restart")
        return {"reference": ref, "next_cursor": "c2", "events": [{"event_id": "e2", "terminal": True}]}
    client.get, client.changes = get, changes
    async def descriptor(_kind): return CATALOG, CATALOG["resource_kinds"][0]
    client._resource_descriptor = descriptor
    async def run():
        try:
            result = await client.wait("execution.action", "ref", timeout=1)
            assert result["terminal"] is True
        finally:
            await client.aclose()
    asyncio.run(run())
    assert calls == 2
    assert [item["event"] for item in observations] == ["observation.reconnecting"]


def test_wait_timeout_does_not_issue_any_mutating_operation():
    client = AsyncVuoroClient(_profile(), lambda _: "secret")
    async def get(_kind, ref, *, repo_id=None):
        return {"reference": ref, "revision": "r", "cursor": "c1", "terminal": False, "state": {}}
    async def changes(*_args, **_kwargs):
        await asyncio.sleep(1)
    client.get, client.changes = get, changes
    async def descriptor(_kind): return CATALOG, CATALOG["resource_kinds"][0]
    client._resource_descriptor = descriptor
    async def run():
        try:
            with pytest.raises(TimeoutError):
                await client.wait("execution.action", "ref", timeout=.01)
        finally:
            await client.aclose()
    asyncio.run(run())


def test_actual_invoke_409_cursor_expired_recovers_with_one_fresh_snapshot():
    gets = 0
    def handler(request):
        nonlocal gets
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=CATALOG)
        payload = __import__("json").loads(request.content)
        if payload["operation"].endswith(".changes"):
            return httpx.Response(409, json={"status": "rejected", "error": {"code": "cursor_expired", "message": "fresh snapshot"}})
        gets += 1
        terminal = gets == 2
        neutral = {"schema_version": "resource-snapshot/v1", "reference": "ref", "revision": f"owner-r{gets}",
                   "cursor": f"c{gets}", "terminal": terminal,
                   "state": {"state": "completed" if terminal else "claimed"}}
        return httpx.Response(200, json={"status": "accepted", "result": neutral})
    async def run():
        async with AsyncVuoroClient(_profile(), lambda _: "credential", transport=httpx.MockTransport(handler)) as client:
            result = await client.wait("execution.action", "ref", timeout=1)
            assert result["terminal"] is True
    asyncio.run(run())
    assert gets == 2


def test_response_loss_retry_preserves_idempotency_and_returns_same_neutral_reference():
    catalog = {**CATALOG, "operations": [*CATALOG["operations"], {
        "name": "execution.action.enqueue", "owning_domain": "execution", "input_schema": {}, "result_schema": {},
        "required_authority": "execution.enqueue", "execution_semantics": "enqueue", "idempotency": "required",
        "repo_scoped": False, "deprecation": {"deprecated": False, "replacement": None, "sunset_at": None},
        "required_client_schema_features": ["json-schema-draft-2020-12"],
        "result_contract": {"mode": "resource-reference", "resource_kind": "execution.action"},
    }]}
    posts = []
    def handler(request):
        if request.url.path == "/api/catalog/v1": return httpx.Response(200, json=catalog)
        posts.append(request.content)
        if len(posts) == 1: raise httpx.ReadError("response lost")
        payload = __import__("json").loads(request.content)
        return httpx.Response(200, json={"status": "accepted", "result": {
            "schema_version": "resource-reference/v1", "owner": "execution", "resource_kind": "execution.action",
            "reference": "aqr1_original", "revision": "owner-r1"}})
    async def run():
        async with AsyncVuoroClient(_profile(), lambda _: "credential", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.ReadError):
                await client.invoke("execution.action.enqueue", {}, request_id="request-1", idempotency_key="key-1")
            result = await client.invoke("execution.action.enqueue", {}, request_id="request-1", idempotency_key="key-1")
            assert result["reference"] == "aqr1_original" and result["revision"] == "owner-r1"
    asyncio.run(run())
    assert posts[0] == posts[1]


def test_non_disclosure_bytes_pass_through_without_reference_derived_auth_or_output_calls():
    paths, auth = [], []
    body = b'{"schema_version":"invocation-result/v1","request_id":"00000000-0000-0000-0000-000000000000","operation":"resource-observation","catalog_revision":"redacted","status":"rejected","result":null,"error":{"code":"resource_not_found","message":"resource not found"}}'
    def handler(request):
        paths.append(request.url.path); auth.append(request.headers.get("authorization"))
        if request.url.path == "/api/catalog/v1": return httpx.Response(200, json=CATALOG)
        return httpx.Response(404, content=body, headers={"content-type": "application/json", "cache-control": "no-store"})
    async def run():
        async with AsyncVuoroClient(_profile(), lambda ref: "credential-for-" + ref, transport=httpx.MockTransport(handler)) as client:
            errors = []
            for ref in ("malformed", "absent", "foreign", "unauthorized"):
                with pytest.raises(InvocationRejectedError) as caught:
                    await client.get("execution.action", ref)
                errors.append((caught.value.code, caught.value.status_code, str(caught.value)))
            assert len(set(errors)) == 1
    asyncio.run(run())
    assert {value for path, value in zip(paths, auth, strict=True) if path.startswith("/api/invoke/")} == {"Bearer credential-for-token:test"}
    assert not any("output" in path or "cancel" in path for path in paths)


def test_restart_new_client_transfers_cursor_and_event_dedup_state_without_authority():
    def transport_for(next_cursor, events):
        def handler(request):
            if request.url.path == "/api/catalog/v1": return httpx.Response(200, json=CATALOG)
            payload = __import__("json").loads(request.content)
            return httpx.Response(200, json={"status": "accepted", "result": {
                "schema_version": "resource-changes/v1", "reference": "ref",
                "next_cursor": next_cursor, "events": events}})
        return httpx.MockTransport(handler)
    async def run():
        first = AsyncVuoroClient(_profile(), lambda _: "credential", transport=transport_for("c2", [
            {"event_id": "e2", "terminal": False, "data": {}}]))
        batch1 = await first.changes("execution.action", "ref", "c1")
        state = first.export_observation_state()
        await first.aclose()
        second = AsyncVuoroClient(_profile(), lambda _: "credential", observation_state=state,
                                  transport=transport_for("c3", [
                                      {"event_id": "e2", "terminal": False, "data": {}},
                                      {"event_id": "e3", "terminal": True, "data": {}}]))
        try:
            batch2 = await second.changes("execution.action", "ref", batch1["next_cursor"])
        finally:
            await second.aclose()
        assert [event["event_id"] for event in batch2["events"]] == ["e3"]
    asyncio.run(run())


def test_mock_transport_restart_resumes_saved_cursor_after_output_loss_and_expiry():
    operations = []
    phase = "expired"
    def handler(request):
        nonlocal phase
        if request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=CATALOG)
        payload = __import__("json").loads(request.content)
        operations.append((payload["operation"], payload["arguments"]))
        if payload["operation"].endswith(".changes") and phase == "expired":
            phase = "recovered"
            return httpx.Response(409, json={"status": "rejected", "error": {"code": "cursor_expired", "message": "snapshot required"}})
        if payload["operation"].endswith(".get"):
            terminal = phase == "terminal"
            result = {"schema_version": "resource-snapshot/v1", "reference": "ref", "revision": "owner-r9", "cursor": "owner-c9", "terminal": terminal, "state": {"output": "expired"}}
        else:
            phase = "terminal"
            result = {"schema_version": "resource-changes/v1", "reference": "ref", "next_cursor": "owner-c10", "events": [{"event_id": "owner-e10", "terminal": True, "data": {}}]}
        return httpx.Response(200, json={"status": "accepted", "result": result})
    async def run():
        seed = AsyncVuoroClient(_profile(), lambda _: "credential", transport=httpx.MockTransport(handler))
        seed._observation_state[("execution.action", "ref")] = {"event_ids": set(), "cursors": ["owner-c7"]}
        state = seed.export_observation_state()
        await seed.aclose()
        async with AsyncVuoroClient(_profile(), lambda _: "credential", observation_state=state, transport=httpx.MockTransport(handler)) as client:
            result = await client.wait("execution.action", "ref", timeout=1)
            assert result["terminal"] is True
    asyncio.run(run())
    assert operations == [
        ("execution.action.changes", {"resource_ref": "ref", "cursor": "owner-c7", "wait_seconds": 1}),
        ("execution.action.get", {"resource_ref": "ref"}),
        ("execution.action.changes", {"resource_ref": "ref", "cursor": "owner-c9", "wait_seconds": 1}),
        ("execution.action.get", {"resource_ref": "ref"}),
    ]


def test_real_transport_overall_timeout_calls_only_catalog_snapshot_and_changes():
    operations = []
    async def handler(request):
        if request.url.path == "/api/catalog/v1": return httpx.Response(200, json=CATALOG)
        payload = __import__("json").loads(request.content); operations.append(payload["operation"])
        if payload["operation"].endswith(".get"):
            result = {"schema_version": "resource-snapshot/v1", "reference": "ref", "revision": "owner-r1",
                      "cursor": "c1", "terminal": False, "state": {}}
            return httpx.Response(200, json={"status": "accepted", "result": result})
        await asyncio.sleep(1)
        raise AssertionError("cancelled wait handler must not complete")
    async def run():
        async with AsyncVuoroClient(_profile(), lambda _: "credential", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(TimeoutError):
                await client.wait("execution.action", "ref", timeout=.02)
    asyncio.run(run())
    assert operations == ["execution.action.get", "execution.action.changes"]
    assert not any("cancel" in operation or "enqueue" in operation for operation in operations)
