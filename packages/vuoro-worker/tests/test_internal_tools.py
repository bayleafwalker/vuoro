from __future__ import annotations

import pytest

from vuoro_service.identity import Identity
from vuoro_service.lease import LeaseError

from vuoro_worker.internal_tools import (
    InternalToolServer,
    StaticWorkSource,
    ToolError,
    WorkItemDetail,
)


TOKEN = "test-bearer-token"
OTHER_TOKEN = "other-bearer-token"


def make_server(*, clock=None) -> InternalToolServer:
    identities = {
        TOKEN: Identity(actor="worker-a", environment="test", repo_ids=frozenset({"*"})),
        OTHER_TOKEN: Identity(actor="worker-b", environment="test", repo_ids=frozenset({"*"})),
    }
    work_source = StaticWorkSource(
        [WorkItemDetail(subject="item-1", title="Do the thing", acceptance=("it works",))]
    )
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return InternalToolServer(identities=identities, work_source=work_source, **kwargs)


def test_unknown_token_is_rejected():
    server = make_server()
    with pytest.raises(ToolError) as excinfo:
        server.list_ready_work("not-a-real-token")
    assert excinfo.value.code == "identity_required"


def test_list_and_describe_work():
    server = make_server()
    listed = server.list_ready_work(TOKEN)
    assert listed.result_type == "work_list"
    assert listed.ttl_ms == 5_000
    assert listed.payload["items"][0]["subject"] == "item-1"

    described = server.describe_work(TOKEN, subject="item-1")
    assert described.payload["acceptance"] == ["it works"]


def test_describe_unknown_subject_raises_resource_not_found():
    server = make_server()
    with pytest.raises(ToolError) as excinfo:
        server.describe_work(TOKEN, subject="does-not-exist")
    assert excinfo.value.code == "resource_not_found"


def test_claim_heartbeat_complete_round_trip():
    server = make_server()
    claimed = server.claim_work(TOKEN, subject="item-1")
    lease_id = claimed.payload["lease_id"]
    assert lease_id

    beat = server.heartbeat(TOKEN, lease_id=lease_id)
    assert beat.payload["lease_id"] == lease_id

    completed = server.complete_work(TOKEN, lease_id=lease_id)
    assert completed.payload["accepted"] is True
    assert completed.payload["evidence_chain_ok"] is True


def test_claim_is_idempotent_for_the_same_holder():
    server = make_server()
    first = server.claim_work(TOKEN, subject="item-1")
    second = server.claim_work(TOKEN, subject="item-1")
    assert first.payload["lease_id"] == second.payload["lease_id"]


def test_claim_conflict_for_a_different_holder():
    server = make_server()
    server.claim_work(TOKEN, subject="item-1")
    with pytest.raises(ToolError) as excinfo:
        server.claim_work(OTHER_TOKEN, subject="item-1")
    assert excinfo.value.code == "lease_conflict"


def test_heartbeat_on_unknown_lease_is_rejected():
    server = make_server()
    with pytest.raises(ToolError) as excinfo:
        server.heartbeat(TOKEN, lease_id="not-a-real-lease")
    assert excinfo.value.code == "lease_not_current"


def test_completion_replayed_after_reclaim_fails():
    # Advance the clock past ttl before reclaiming, so the first holder's
    # lease is genuinely expired -- exercising the real LeaseStore
    # contract, not a shortcut around it.
    clock_value = [1_000.0]
    server = make_server(clock=lambda: clock_value[0])
    server._lease_ttl_seconds = 10.0  # keep the test fast
    first = server.claim_work(TOKEN, subject="item-1")
    lease_id = first.payload["lease_id"]

    clock_value[0] += 20.0  # past ttl
    second = server.claim_work(OTHER_TOKEN, subject="item-1")
    assert second.payload["lease_id"] != lease_id

    with pytest.raises(ToolError) as excinfo:
        server.complete_work(TOKEN, lease_id=lease_id)
    assert excinfo.value.code == "lease_not_current"


def test_append_evidence_and_session_note_chain():
    server = make_server()
    claimed = server.claim_work(TOKEN, subject="item-1")
    lease_id = claimed.payload["lease_id"]

    first = server.append_evidence(TOKEN, lease_id=lease_id, kind="log", ref="ref-1")
    assert first.payload["chain_seq"] == 0

    second = server.write_session_note(TOKEN, lease_id=lease_id, note="did a thing")
    assert second.payload["chain_seq"] == 1


def test_propose_effect_never_executes_anything():
    server = make_server()
    claimed = server.claim_work(TOKEN, subject="item-1")
    lease_id = claimed.payload["lease_id"]

    proposed = server.propose_effect(
        TOKEN, lease_id=lease_id, description={"kind": "diff", "summary": "example"}
    )
    assert proposed.payload["status"] == "queued_for_homelab_reconciler"
    assert "effect_intent_id" in proposed.payload


def test_rate_limit_exceeded_is_a_distinct_error_from_identity():
    server = make_server()
    server._rate_limiter = server._rate_limiter.__class__(
        capacity=1, refill_per_second=0.001, clock=lambda: 0.0
    )
    server.list_ready_work(TOKEN)
    with pytest.raises(ToolError) as excinfo:
        server.list_ready_work(TOKEN)
    assert excinfo.value.code == "rate_limit_exceeded"


def test_metrics_record_every_call_including_failures():
    server = make_server()
    before = server.metrics.snapshot()
    server.list_ready_work(TOKEN)
    try:
        server.heartbeat(TOKEN, lease_id="nope")
    except ToolError:
        pass
    after = server.metrics.snapshot()
    assert after.request_count == before.request_count + 2
    assert after.error_count == before.error_count + 1
