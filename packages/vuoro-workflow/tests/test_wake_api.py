import copy
import io
import json

import pytest

from vuoro_workflow import codex, store, wake_api

HEAD = "a" * 40
THREAD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "identity": {"workspace": "tools", "principal": "builder", "harness": "agent", "instance": "a"},
        "destination": {"kind": "codex", "instance": "workstation", "thread": THREAD},
        "scopes": ["wake.read", "wake.write"],
        "max_ttl_seconds": 1000,
        "sources": {
            "cloud": {
                "origin": "https://forge.example.org", "repository": "owner/cloud",
                "policies": {
                    wake_api.CI: {
                        "workflow": "ci.yaml", "required": ["gate", "tests"],
                        "base": "main", "event": "pull_request",
                    },
                    wake_api.PROMOTION: {
                        "workflow": "promote.yaml", "required": ["promote"],
                        "environment": "private", "event": "workflow_dispatch",
                        "digest_input": "image_digest",
                    },
                },
            },
        },
    }))
    return path


@pytest.fixture
def registration():
    return {
        "template": wake_api.CI, "source_alias": "cloud",
        "params": {"run": 22, "pr": 7, "head": HEAD},
        "ttl_seconds": 600, "idempotency_key": "my-request-v1",
    }


def test_typed_registration_is_durable_idempotent_and_uses_profile(tmp_path, profile, registration):
    path = tmp_path / "watch.sqlite"
    db = store.connect(path)
    binding = wake_api.load_profile(profile)
    api = wake_api.WakeApi(db, binding)
    recorded = api.call("wake.register", registration)
    internal, = store.status(db)
    assert internal["spec"]["required"] == ["gate", "tests"]
    assert internal["spec"]["base"] == "main"
    assert internal["spec"]["thread"] == codex.destination_channel(THREAD, "workstation")
    assert internal["spec"]["thread"] != THREAD
    assert internal["spec"]["repository"] == "owner/cloud"
    assert "origin" not in recorded
    db.close()

    with store.connect(path) as reopened:
        api = wake_api.WakeApi(reopened, binding)
        assert api.call("wake.register", registration) == recorded
        assert api.call("wake.get", {"rule_id": recorded["rule_id"]}) == recorded
        mutation = {**registration, "ttl_seconds": 601}
        with pytest.raises(wake_api.Rejected, match="idempotency key conflict"):
            api.call("wake.register", mutation)
        assert len(store.status(reopened)) == 1


@pytest.mark.parametrize("override", ["origin", "repository", "thread", "command", "required"])
def test_rejects_request_supplied_authority(tmp_path, profile, registration, override):
    with store.connect(tmp_path / "watch.sqlite") as db:
        api = wake_api.WakeApi(db, wake_api.load_profile(profile))
        registration["params"][override] = "untrusted"
        with pytest.raises(wake_api.Rejected, match="unexpected"):
            api.call("wake.register", registration)
        assert store.status(db) == []


def test_binding_scopes_templates_and_owner_identity(tmp_path, profile, registration):
    with store.connect(tmp_path / "watch.sqlite") as db:
        binding = wake_api.load_profile(profile)
        first = wake_api.WakeApi(db, binding)
        owned = first.call("wake.register", registration)["rule_id"]
        foreign = copy.deepcopy(binding)
        foreign["identity"]["principal"] = "other"
        foreign["scopes"] = ["wake.read"]
        foreign["sources"]["cloud"]["policies"].pop(wake_api.PROMOTION)
        second = wake_api.WakeApi(db, foreign)
        assert second.call("wake.capabilities", {})["templates"] == [wake_api.CI]
        for method in ("wake.get", "wake.receipts"):
            with pytest.raises(wake_api.Rejected, match="rule not found"):
                second.call(method, {"rule_id": owned})
        with pytest.raises(wake_api.Rejected, match="forbidden"):
            second.call("wake.register", registration)
        with pytest.raises(wake_api.Rejected, match="forbidden"):
            first.call("wake.register", {**registration, "source_alias": "unknown"})


def test_namespaced_inbox_destinations_do_not_share_threads(tmp_path, profile):
    binding = wake_api.load_profile(profile)
    binding["destination"] = {"kind": "inbox", "instance": "workstation"}
    with store.connect(tmp_path / "watch.sqlite") as db:
        first = wake_api.WakeApi(db, binding)
        foreign = copy.deepcopy(binding)
        foreign["identity"]["harness"] = "different"
        second = wake_api.WakeApi(db, foreign)
        assert first.owner != second.owner
        assert first.thread != second.thread
        assert first.call("wake.capabilities", {})["delivery"] == "inbox"


def test_receipts_page_and_cancel_queued_while_preserving_inflight(tmp_path, profile, registration):
    with store.connect(tmp_path / "watch.sqlite") as db:
        api = wake_api.WakeApi(db, wake_api.load_profile(profile))
        rule = api.call("wake.register", registration)["rule_id"]
        store.reconcile(db, lambda _: {"state": "failure", "attempt": 1})
        store.reconcile(db, lambda _: {"state": "success", "attempt": 2})
        first = api.call("wake.receipts", {"rule_id": rule, "limit": 1})
        one, = first["receipts"]
        assert first["has_more"] and one["state"] == "failure"
        assert store.claim_delivery(db, api.thread, one["event_id"])
        result = api.call("wake.cancel", {"rule_id": rule})
        assert result["suppressed"] == result["delivery_uncertain"] == 1
        assert not result["active"] and store.deliverable(db, api.thread) == []
        second = api.call("wake.receipts", {"rule_id": rule, "cursor": first["next_cursor"]})
        two, = second["receipts"]
        assert not second["has_more"] and two["state"] == "success"
        assert two["cancelled_at"] and not two["acknowledged_at"]


def test_wire_parse_errors_and_frame_bound_do_not_create_rule(tmp_path, profile):
    with store.connect(tmp_path / "watch.sqlite") as db:
        api = wake_api.WakeApi(db, wake_api.load_profile(profile))
        assert wake_api.response(api, b'{"id":1,"id":2}')["error"]["code"] == -32700
        assert wake_api.response(api, b'[]')["error"]["code"] == -32600
        assert wake_api.response(api, b'{"jsonrpc":"2.0","id":2,"method":"unknown"}')["error"]["code"] == -32601
        sink = io.StringIO()
        assert wake_api.serve(api, io.BytesIO(b" " * (wake_api.MAX_FRAME + 1)), sink) == 1
        assert json.loads(sink.getvalue())["error"]["code"] == -32600
        assert store.status(db) == []


def test_profile_declines_unbound_destination(tmp_path, profile):
    value = json.loads(profile.read_text())
    value["destination"]["command"] = "unexpected"
    profile.write_text(json.dumps(value))
    with pytest.raises(wake_api.Rejected, match="unexpected"):
        wake_api.load_profile(profile)
