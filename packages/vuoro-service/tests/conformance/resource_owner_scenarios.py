"""Supported resource carriers and executable, non-qualifying gap receipts.

Gap assertions deliberately do not count as satisfying the neutral invariant.
A changed owner must update the qualification report and remove the gap rather
than silently continuing to report a defect it has fixed.
"""
import pytest
from .resource_binding import ResourceBinding


@pytest.fixture
def resource_owner(request):
    url = request.config.getoption("--lease-pg-url")
    if url is None:
        pytest.fail("published-owner observations require the configured disposable PG URL")
    binding = ResourceBinding(url)
    try:
        yield binding
    finally:
        binding.close()


@pytest.mark.essential_safety
def test_description_cas_preserves_aggregate_identity_and_refuses_stale_write(resource_owner):
    owner = resource_owner
    item = owner.item()
    before = owner.read(item)
    row = before["item"]
    args = {"item_id": item, "description": "first", "expected_revision": row["edit_revision"]}
    response = owner.invoke("work.item.edit", args)
    assert response.status_code == 200, response.text
    after = owner.read(item)["item"]
    assert after["aggregate_uuid"] == row["aggregate_uuid"]
    assert after["edit_revision"] != row["edit_revision"]
    stale = owner.invoke("work.item.edit", {**args, "description": "second"})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "item-edit-conflict"
    assert owner.read(item)["item"] == after


@pytest.mark.essential_safety
def test_reader_cannot_edit_and_stale_catalog_cannot_dispatch(resource_owner):
    owner = resource_owner
    item = owner.item()
    row = owner.read(item)["item"]
    args = {"item_id": item, "description": "forbidden", "expected_revision": row["edit_revision"]}
    denied = owner.invoke("work.item.edit", args, token="reader")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "authority-required"
    stale = owner.invoke("work.item.edit", args, revision="stale")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale-catalog"
    assert owner.read(item)["item"] == row


@pytest.mark.conformance_gap(tracker="agentops#2603")
def test_gap_reissued_principal_is_not_a_work_item_owner_fence(resource_owner):
    owner = resource_owner
    item = owner.item()
    row = owner.read(item)["item"]
    response = owner.invoke("work.item.edit", {"item_id": item, "description": "epoch one",
        "expected_revision": row["edit_revision"]}, token="reissued")
    assert response.status_code == 200, response.text
    assert owner.read(item)["item"]["description"] == "epoch one"
    # GAP: repo write authority is team coordination authority, not immutable
    # creator ownership. No neutral creator-bound resource is fabricated.


@pytest.mark.conformance_gap(tracker="agentops#2603")
def test_gap_opposite_dependencies_are_not_aggregate_cycle_checked(resource_owner):
    owner = resource_owner
    first, second = owner.item(), owner.item()
    assert owner.invoke("work.item.dep.add", {"item_id": first, "blocked_item_id": second}).status_code == 200
    response = owner.invoke("work.item.dep.add", {"item_id": second, "blocked_item_id": first})
    assert response.status_code == 200, response.text
    assert owner.read(first)["deps"]["blocked_by"]
    assert owner.read(second)["deps"]["blocked_by"]
    # GAP: a neutral source-owned parent/dependency relation must refuse this.


@pytest.mark.conformance_gap(tracker="agentops#2604")
def test_gap_missing_catalog_revision_retains_legacy_dispatch_compatibility(resource_owner):
    owner = resource_owner
    item = owner.item()
    row = owner.read(item)["item"]
    response = owner.client.post("/api/invoke/v1", headers={"Authorization": "Bearer original",
        "X-Vuoro-Client-Protocol": "1"}, json={"schema_version": "invocation/v1", "request_id": "missing-revision",
        "repo_id": owner.store.repo_id, "operation": "work.item.edit", "arguments": {"item_id": item,
        "description": "compatibility write", "expected_revision": row["edit_revision"]}})
    assert response.status_code == 200, response.text
    assert owner.read(item)["item"]["description"] == "compatibility write"


@pytest.mark.conformance_gap(tracker="agentops#2603")
def test_gap_general_resource_commands_are_unsupported(resource_owner):
    owner = resource_owner
    # Candidate carrier names, not names mandated by the neutral contract.
    # Unknown-operation receipts plus the documented catalog mapping establish
    # that this binding has no aggregate/rejected-decision carrier; an adapter
    # cannot satisfy it by locally executing a synthetic resource model.
    for operation in ("work.resource.create-v1", "work.resource.relate-v1", "work.resource.command-decision-v1"):
        response = owner.invoke(operation, {})
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "unknown-operation", response.text
