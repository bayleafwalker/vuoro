"""Agent directory and inbox contract v1: the sixteen conformance histories (contract section 4).

Every returned receipt, directory entry and stamped envelope is validated
against the JSON schemas by a validating proxy around the provider, so no
scenario can pass on a malformed result.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .inbox_contract import (CLOCK, CONCURRENT_COMMIT_ORDER, RESTART, ContractRefusal, Ctx,
                             new_instance_id, new_message_id)

DOCS = Path(__file__).resolve().parents[4] / "docs" / "architecture"
STAMPS = ("sender_principal_id", "workspace_id", "repo_id", "persisted_at", "envelope_digest", "inbox_seq")
_RFC3339 = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$")


def _load(name):
    return json.loads((DOCS / f"agent-{name}-v1.schema.json").read_text())


def _validators():
    schemas = {name: _load(name) for name in ("declaration", "directory-entry", "message", "message-receipt")}
    registry = Registry().with_resources(
        [(s["$id"], Resource.from_contents(s)) for s in schemas.values()])
    formats = FormatChecker(formats=())
    formats.checks("date-time")(lambda value: not isinstance(value, str) or bool(_RFC3339.match(value)))
    return {name: Draft202012Validator(s, registry=registry, format_checker=formats)
            for name, s in schemas.items()}


VALIDATORS = _validators()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def valid_receipt(receipt):
    VALIDATORS["message-receipt"].validate(receipt)
    return receipt


def valid_entry(entry):
    VALIDATORS["directory-entry"].validate(entry)
    return entry


def valid_stamps(stamps):
    assert set(STAMPS) <= set(stamps)
    assert isinstance(stamps["inbox_seq"], int) and not isinstance(stamps["inbox_seq"], bool) and stamps["inbox_seq"] >= 1
    assert _RFC3339.match(stamps["persisted_at"])
    return stamps


def valid_stamped(stamped):
    """Stamped envelope: the envelope as sent (schema-valid) plus the stamps and receipts."""
    extra = set(STAMPS) | {"receipts"}
    assert extra <= set(stamped), "stamped message lacks stamps or receipts"
    envelope = {k: v for k, v in stamped.items() if k not in extra}
    VALIDATORS["message"].validate(envelope)
    valid_stamps(stamped)
    assert stamped["envelope_digest"] == hashlib.sha256(canonical(envelope)).hexdigest()
    times = [valid_receipt(r)["recorded_at"] for r in stamped["receipts"]]
    assert times == sorted(times), "receipts must be in recorded_at order"
    return stamped


class Checked:
    """Validating proxy: every directory, inbox and receipt result passes its schema."""
    def __init__(self, provider):
        self._p = provider

    def __getattr__(self, name):
        return getattr(self._p, name)

    def send(self, ctx, **kw):
        return valid_stamps(self._p.send(ctx, **kw))

    def read(self, ctx, **kw):
        result = self._p.read(ctx, **kw)
        for message in result["messages"]:
            valid_stamped(message)
        return result

    def status(self, ctx, **kw):
        result = self._p.status(ctx, **kw)
        valid_stamped(result["message"])
        for receipt in result["receipts"]:
            valid_receipt(receipt)
        assert result["receipts"] == result["message"]["receipts"]
        return result

    def receipt(self, ctx, **kw):
        return valid_receipt(self._p.receipt(ctx, **kw))

    def cancel(self, ctx, **kw):
        return valid_receipt(self._p.cancel(ctx, **kw))

    def describe(self, ctx, **kw):
        return valid_entry(self._p.describe(ctx, **kw))

    def list(self, ctx):
        result = self._p.list(ctx)
        for entry in result["entries"]:
            valid_entry(entry)
        return result


def expect(code, fn, *args, **kwargs):
    with pytest.raises(ContractRefusal) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code, f"expected {code}, got {caught.value.code}"
    return caught.value


def refused(fn, *args, **kwargs):
    with pytest.raises(ContractRefusal) as caught:
        fn(*args, **kwargs)
    return caught.value


def declaration(*, interactive=True, channels=("mcp",)):
    return {"schema": "agent-declaration/v1", "harness": {"name": "claude-code", "version": "1.0"},
            "adapter": None, "host_class": "workstation",
            "interactivity": "interactive" if interactive else "non-interactive",
            "accepted_kinds": ["continue"],
            "channels": [{"channel": c, "wake_modes": ["read-while-running"]} for c in channels]}


class World:
    """One repository with a sender S, a recipient principal R with two instances, and a stranger X."""
    def __init__(self, provider):
        self.p = provider
        self.S = Ctx("ws-a", "repo-a", "github:1:0")
        self.R = Ctx("ws-a", "repo-a", "github:2:0")
        self.X = Ctx("ws-a", "repo-a", "github:3:0")
        self.ins1, self.ins2 = new_instance_id(), new_instance_id()
        self.p.register(self.R, instance_id=self.ins1, declaration=declaration(), idempotency_key="reg-key-1")
        self.p.register(self.R, instance_id=self.ins2, declaration=declaration(), idempotency_key="reg-key-2")
        self.item = self.p.new_item(self.S)
        self.run = self.p.new_run(self.R)
        self._keys = 0

    def key(self):
        self._keys += 1
        return f"key-{self._keys:06d}"

    def message(self, *, instance=None, **overrides):
        recipient = {"principal_id": self.R.principal_id}
        if instance:
            recipient["instance_id"] = instance
        message = {"schema": "agent-message/v1", "message_id": new_message_id(), "recipient": recipient,
                   "kind": "continue", "mode": "request", "context": {"item_id": self.item},
                   "body": {"text": "please continue"}, "created_at": "2026-10-14T10:00:00Z"}
        message.update(overrides)
        return message

    def send(self, **kw):
        message = self.message(**kw)
        self.p.send(self.S, message=message)
        return message["message_id"]

    def rcpt(self, message_id, kind, instance=None, ctx=None, **fields):
        return self.p.receipt(ctx or self.R, message_id=message_id, instance_id=instance or self.ins1,
                              kind=kind, **fields)

    def accept_and_link(self, message_id, instance=None):
        self.rcpt(message_id, "accepted", instance)
        return self.rcpt(message_id, "run-linked", instance, run_id=self.run)

    def kinds(self, message_id):
        return [r["kind"] for r in self.p.status(self.S, message_id=message_id)["receipts"]]


@pytest.fixture
def w(inbox_provider):
    return World(Checked(inbox_provider))


def need(provider, capability):
    if capability not in provider.capabilities:
        pytest.skip(f"provider does not offer the {capability} test hook")


def sized_message(w, target):
    """A schema-valid envelope whose canonical JSON is exactly `target` bytes (multi-byte artifact URIs)."""
    message = w.message(artifacts=[{"uri": "a", "sha256": "0" * 64, "size_bytes": 1} for _ in range(16)])
    remaining = target - len(canonical(message))
    assert remaining > 0
    for artifact in message["artifacts"]:
        extra = min(2040, remaining // 4)
        artifact["uri"] += "\U0001F600" * extra
        remaining -= 4 * extra
    message["artifacts"][0]["uri"] += "b" * remaining
    assert len(canonical(message)) == target
    return message


def seqs(result):
    return [m["inbox_seq"] for m in result["messages"]]


# 1. Retry -------------------------------------------------------------------

@pytest.mark.essential_safety
def test_01_retry_replays_same_stamps_and_changed_envelope_is_refused(w):
    message = w.message()
    first = w.p.send(w.S, message=message)
    again = w.p.send(w.S, message=message)
    assert (first["replayed"], again["replayed"]) == (False, True)
    assert {k: first[k] for k in STAMPS} == {k: again[k] for k in STAMPS}
    assert first["sender_principal_id"] == w.S.principal_id
    assert (first["workspace_id"], first["repo_id"]) == ("ws-a", "repo-a")
    assert first["envelope_digest"] == hashlib.sha256(canonical(message)).hexdigest()
    changed = {**message, "body": {"text": "something else"}}
    expect("message-id-conflict", w.p.send, w.S, message=changed)
    expect("message-id-conflict", w.p.send, w.X, message=message)  # different sender, same id
    page = w.p.read(w.R, after_seq=0, limit=100)
    assert [m["message_id"] for m in page["messages"]] == [message["message_id"]]
    assert page["messages"][0]["inbox_seq"] == first["inbox_seq"]
    assert page["messages"][0]["body"] == {"text": "please continue"}  # the original, not the changed attempt


# 2. Redelivery --------------------------------------------------------------

@pytest.mark.essential_workflow
def test_02_redelivery_and_cursor_survive_restart(w):
    ids = [w.send() for _ in range(3)]
    first = w.p.read(w.R, after_seq=0, limit=100)
    assert first == w.p.read(w.R, after_seq=0, limit=100), "reading changes nothing and removes nothing"
    assert [m["message_id"] for m in first["messages"]] == ids
    assert seqs(first) == sorted(set(seqs(first)))
    assert first["next_after_seq"] == seqs(first)[-1]
    assert w.p.read(w.R, after_seq=first["next_after_seq"], limit=100) == {
        "messages": [], "next_after_seq": first["next_after_seq"]}
    # Page by one; a cursor kept durably resumes after restart without loss or reordering.
    page = w.p.read(w.R, after_seq=0, limit=1)
    assert [m["message_id"] for m in page["messages"]] == ids[:1]
    cursor = page["next_after_seq"]
    if RESTART in w.p.capabilities:
        w.p.restart()
    rest = w.p.read(w.R, after_seq=cursor, limit=100)
    assert [m["message_id"] for m in rest["messages"]] == ids[1:]
    assert w.p.read(w.R, after_seq=0, limit=100)["messages"] == first["messages"]  # replay from 0
    later = w.send()
    assert w.p.read(w.R, after_seq=0, limit=100)["messages"][-1]["inbox_seq"] > first["next_after_seq"]
    assert w.kinds(later) == []


@pytest.mark.essential_workflow
def test_02b_read_argument_bounds_and_instance_filter(w):
    for bad in ({"after_seq": -1, "limit": 10}, {"after_seq": 0, "limit": 0}, {"after_seq": 0, "limit": 101}):
        expect("invalid-arguments", w.p.read, w.R, **bad)
    general = w.send()
    targeted = w.send(instance=w.ins1)
    ids = lambda **kw: [m["message_id"] for m in w.p.read(w.R, after_seq=0, limit=100, **kw)["messages"]]
    assert ids() == [general]
    assert ids(instance_id=w.ins1) == [general, targeted]
    assert ids(instance_id=w.ins2) == [general]
    assert w.p.read(w.S, after_seq=0, limit=100)["messages"] == [], "only the addressed principal reads"


# 3. Single decision ---------------------------------------------------------

@pytest.mark.essential_safety
def test_03_single_decision_names_the_winner(w):
    m = w.send()
    won = w.rcpt(m, "accepted", w.ins1)
    error = expect("already-decided", w.rcpt, m, "accepted", w.ins2)
    assert error.details.get("instance_id") == w.ins1 and error.details.get("kind") == "accepted"
    error = expect("already-decided", w.rcpt, m, "refused", w.ins2, reason={"code": "busy", "text": "no"})
    assert error.details.get("instance_id") == w.ins1
    expect("already-decided", w.rcpt, m, "refused", w.ins1, reason={"code": "busy", "text": "no"})
    assert w.kinds(m) == ["accepted"]
    assert w.rcpt(m, "accepted", w.ins1)["replayed"] is True
    assert won["instance_id"] == w.ins1 and won["principal_id"] == w.R.principal_id


@pytest.mark.essential_safety
def test_03b_refused_is_a_decision_and_targeted_messages_are_decided_by_their_instance(w):
    m = w.send()
    reason = {"code": "policy", "text": "denied"}
    w.rcpt(m, "refused", w.ins1, reason=reason)
    expect("already-decided", w.rcpt, m, "accepted", w.ins2)
    assert w.rcpt(m, "refused", w.ins1, reason=reason)["replayed"] is True
    expect("already-decided", w.rcpt, m, "refused", w.ins1, reason={"code": "policy", "text": "other text"})
    targeted = w.send(instance=w.ins1)
    refused(w.rcpt, targeted, "accepted", w.ins2)
    assert w.kinds(targeted) == []


# 4. Single run --------------------------------------------------------------

@pytest.mark.essential_safety
def test_04_single_run(w):
    m = w.send()
    expect("not-accepted", w.rcpt, m, "run-linked", w.ins1, run_id=w.run)
    w.rcpt(m, "accepted", w.ins1)
    expect("run-not-found", w.rcpt, m, "run-linked", w.ins1, run_id="run_" + "0" * 26)
    expect("not-accepted", w.rcpt, m, "run-linked", w.ins2, run_id=w.run)  # accepted by another instance
    first = w.rcpt(m, "run-linked", w.ins1, run_id=w.run)
    assert first["replayed"] is False
    replay = w.rcpt(m, "run-linked", w.ins1, run_id=w.run)
    assert replay["replayed"] is True and replay["recorded_at"] == first["recorded_at"]
    expect("run-already-linked", w.rcpt, m, "run-linked", w.ins1, run_id=w.p.new_run(w.R))
    assert w.kinds(m).count("run-linked") == 1


# 5. Ack is not outcome ------------------------------------------------------

@pytest.mark.essential_workflow
def test_05_acknowledged_is_not_an_outcome(w):
    m = w.send()
    w.rcpt(m, "observed", w.ins1)
    w.rcpt(m, "acknowledged", w.ins1)
    for item in (w.p.status(w.S, message_id=m)["message"],
                 next(x for x in w.p.read(w.R, after_seq=0, limit=100)["messages"] if x["message_id"] == m)):
        assert [r["kind"] for r in item["receipts"]] == ["observed", "acknowledged"]
        derived = set(item) - set(STAMPS) - set(w.message()) - {"receipts"}
        assert derived == set(), f"no derived delivery state may be presented: {derived}"
    assert "result-linked" not in w.kinds(m)


# 6. Cancel races decision ---------------------------------------------------

@pytest.mark.essential_workflow
def test_06_cancel_before_decision_is_visible_to_the_receiver(w):
    m = w.send()
    cancel = w.p.cancel(w.S, message_id=m, reason="no longer needed")
    assert (cancel["kind"], cancel["principal_id"], cancel["instance_id"]) == (
        "cancel-requested", w.S.principal_id, None)
    seen = next(x for x in w.p.read(w.R, after_seq=0, limit=100)["messages"] if x["message_id"] == m)
    assert [r["kind"] for r in seen["receipts"]] == ["cancel-requested"]
    assert seen["receipts"][0]["reason"] == "no longer needed"
    w.rcpt(m, "refused", w.ins1, reason={"code": "canceled-by-sender", "text": "sender canceled"})
    assert w.kinds(m) == ["cancel-requested", "refused"]  # a request recorded beside the others
    expect("not-sender", w.p.cancel, w.R, message_id=m, reason="x")


@pytest.mark.essential_workflow
def test_06b_cancel_after_accepted_changes_nothing_and_is_idempotent(w):
    m = w.send()
    w.accept_and_link(m)
    before = w.p.status(w.S, message_id=m)["receipts"]
    first = w.p.cancel(w.S, message_id=m, reason="stop")
    if CLOCK in w.p.capabilities:
        w.p.advance_clock(5)
    again = w.p.cancel(w.S, message_id=m, reason="a different reason")
    assert again["replayed"] is True
    assert {k: v for k, v in again.items() if k != "replayed"} == {k: v for k, v in first.items() if k != "replayed"}
    after = w.p.status(w.S, message_id=m)["receipts"]
    assert after[:len(before)] == before
    assert [r["kind"] for r in after] == ["accepted", "run-linked", "cancel-requested"]
    w.rcpt(m, "result-linked", outcome="canceled", evidence_refs=[], summary="stopped after launch")
    assert w.kinds(m) == ["accepted", "run-linked", "cancel-requested", "result-linked"]


# 7. Scope -------------------------------------------------------------------

@pytest.mark.essential_safety
def test_07_foreign_scope_does_not_exist(w):
    m = w.send()
    w.rcpt(m, "observed", w.ins1)
    foreign = [Ctx("ws-b", "repo-a", w.R.principal_id), Ctx("ws-a", "repo-b", w.R.principal_id),
               Ctx("ws-a", "repo-b", w.S.principal_id)]
    for ctx in foreign:
        expect("not-found", w.p.describe, ctx, principal_id=w.R.principal_id)
        expect("not-found", w.p.status, ctx, message_id=m)
        expect("not-found", w.p.read, ctx, after_seq=0, limit=100, instance_id=w.ins1)
        expect("not-found", w.p.receipt, ctx, message_id=m, instance_id=w.ins1, kind="acknowledged")
        assert w.p.read(ctx, after_seq=0, limit=100)["messages"] == []
        assert w.p.list(ctx)["entries"] == []
    assert w.kinds(m) == ["observed"]


@pytest.mark.essential_safety
def test_07b_list_never_returns_foreign_entries_and_instance_ids_are_per_scope(w):
    other = Ctx("ws-b", "repo-a", "github:9:0")
    elsewhere = Ctx("ws-a", "repo-b", w.R.principal_id)
    w.p.register(other, instance_id=w.ins1, declaration=declaration(), idempotency_key="other-ws-key")  # same id, other workspace
    w.p.register(elsewhere, instance_id=w.ins1, declaration=declaration(), idempotency_key="other-repo-key")  # other repository
    assert [e["principal_id"] for e in w.p.list(w.R)["entries"]] == [w.R.principal_id]
    assert [e["principal_id"] for e in w.p.list(other)["entries"]] == [other.principal_id]
    assert [e["principal_id"] for e in w.p.list(elsewhere)["entries"]] == [w.R.principal_id]
    assert {i["instance_id"] for i in w.p.list(w.R)["entries"][0]["instances"]} == {w.ins1, w.ins2}
    assert [i["instance_id"] for i in w.p.list(elsewhere)["entries"][0]["instances"]] == [w.ins1]
    expect("not-found", w.p.describe, w.R, principal_id=other.principal_id)


# 8. Visibility --------------------------------------------------------------

@pytest.mark.essential_safety
def test_08_stranger_sees_nothing_and_writes_nothing(w):
    m = w.send()
    expect("not-found", w.p.status, w.X, message_id=m)
    expect("not-found", w.p.receipt, w.X, message_id=m, instance_id=w.ins1, kind="observed")
    stranger_instance = new_instance_id()
    w.p.register(w.X, instance_id=stranger_instance, declaration=declaration(), idempotency_key="stranger-key")
    expect("not-found", w.p.receipt, w.X, message_id=m, instance_id=stranger_instance, kind="acknowledged")
    refused(w.p.receipt, w.S, message_id=m, instance_id=w.ins1, kind="observed")  # sender is not the recipient
    assert w.p.read(w.X, after_seq=0, limit=100)["messages"] == []
    assert w.kinds(m) == []
    w.p.status(w.S, message_id=m)
    w.p.status(w.R, message_id=m)


# 9. Ownership ---------------------------------------------------------------

@pytest.mark.essential_safety
def test_09_instances_belong_to_their_principal(w):
    w.p.observe(w.R, instance_id=w.ins1, fact="observed", valid_for_seconds=60, idempotency_key=w.key())
    expect("instance-not-owned", w.p.register, w.X, instance_id=w.ins1, declaration=declaration(interactive=False),
           idempotency_key=w.key())
    expect("instance-not-owned", w.p.observe, w.X, instance_id=w.ins1, fact="observed", valid_for_seconds=60,
           idempotency_key=w.key())
    expect("instance-not-owned", w.p.retire, w.X, instance_id=w.ins1, reason="takeover", idempotency_key=w.key())
    entry = w.p.describe(w.S, principal_id=w.R.principal_id)
    mine = next(i for i in entry["instances"] if i["instance_id"] == w.ins1)
    assert mine["registered"]["state"] == "current" and mine["declaration"] == declaration()
    assert mine["declaration_revision"] == 1
    assert mine["observed"]["state"] == "current"
    # A principal cannot write receipts through another principal's instance.
    m = w.send()
    other = new_instance_id()
    w.p.register(w.X, instance_id=other, declaration=declaration(), idempotency_key="stranger-key")
    expect("instance-not-owned", w.rcpt, m, "observed", other)
    assert w.kinds(m) == []


@pytest.mark.essential_workflow
def test_09b_registration_revisions_and_idempotency(w):
    revised = declaration(channels=("mcp", "tmux"))
    first = w.p.register(w.R, instance_id=w.ins1, declaration=revised, idempotency_key="revision-key")
    assert first["declaration_revision"] == 2 and first["replayed"] is False
    again = w.p.register(w.R, instance_id=w.ins1, declaration=revised, idempotency_key="revision-key")
    assert again["replayed"] is True and again["declaration_revision"] == 2
    expect("idempotency-conflict", w.p.register, w.R, instance_id=w.ins1, declaration=declaration(),
           idempotency_key="revision-key")
    instance = next(i for i in w.p.describe(w.S, principal_id=w.R.principal_id)["instances"]
                    if i["instance_id"] == w.ins1)
    assert instance["declaration"] == revised and instance["declaration_revision"] == 2
    bad = declaration()
    bad["host_class"] = "mainframe"
    expect("invalid-arguments", w.p.register, w.R, instance_id=new_instance_id(), declaration=bad,
           idempotency_key=w.key())


# 10. Freshness --------------------------------------------------------------

def instance_of(w, instance_id):
    entry = w.p.describe(w.S, principal_id=w.R.principal_id)
    return entry, next(i for i in entry["instances"] if i["instance_id"] == instance_id)


def seconds(stamp):
    from datetime import datetime
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


@pytest.mark.essential_safety
def test_10_expired_evidence_is_stale_never_absent_or_false(w):
    need(w.p, CLOCK)
    ins = w.ins1
    _, fresh = instance_of(w, ins)
    assert fresh["observed"] == {"state": "unknown", "observed_at": None, "valid_until": None, "source": None}
    assert [r["state"] for r in fresh["reachable"]] == ["unknown"] and fresh["reachable"][0]["channel"] == "mcp"
    assert fresh["executing"] == [] and fresh["attended"]["state"] == "unknown"

    seen = w.p.observe(w.R, instance_id=ins, fact="observed", valid_for_seconds=60, idempotency_key=w.key())
    assert seconds(seen["valid_until"]) - seconds(seen["observed_at"]) == 60 and seen["source"] == "self-reported"
    w.p.observe(w.R, instance_id=ins, fact="reachable", channel="mcp", valid_for_seconds=30, idempotency_key=w.key())
    w.p.observe(w.R, instance_id=ins, fact="attended", valid_for_seconds=30, idempotency_key=w.key())
    w.p.observe(w.R, instance_id=ins, fact="executing", run_id=w.run, valid_for_seconds=120, idempotency_key=w.key())
    other_run = w.p.new_run(w.R)
    w.p.observe(w.R, instance_id=ins, fact="executing", run_id=other_run, valid_for_seconds=10, idempotency_key=w.key())
    entry, now = instance_of(w, ins)
    assert now["observed"]["state"] == now["attended"]["state"] == "current"
    assert {r["run_id"]: r["state"] for r in now["executing"]} == {w.run: "current", other_run: "current"}
    evaluated = seconds(entry["evaluated_at"])

    w.p.advance_clock(10)  # evaluated_at == valid_until is no longer current
    entry, aged = instance_of(w, ins)
    assert seconds(entry["evaluated_at"]) - evaluated == 10
    assert {r["run_id"]: r["state"] for r in aged["executing"]} == {w.run: "current", other_run: "stale"}
    w.p.advance_clock(20)
    _, aged = instance_of(w, ins)
    assert aged["observed"]["state"] == "current"
    assert aged["reachable"][0]["state"] == "stale" and aged["attended"]["state"] == "stale"
    assert aged["attended"]["observed_at"] is not None and aged["attended"]["valid_until"] is not None
    w.p.advance_clock(100)
    _, aged = instance_of(w, ins)
    assert aged["observed"]["state"] == "stale" and aged["observed"]["observed_at"] == seen["observed_at"]
    assert {r["state"] for r in aged["executing"]} == {"stale"} and len(aged["executing"]) == 2
    assert "false" not in json.dumps(w.p.describe(w.S, principal_id=w.R.principal_id)).lower()
    w.p.observe(w.R, instance_id=ins, fact="observed", valid_for_seconds=60, idempotency_key=w.key())
    assert instance_of(w, ins)[1]["observed"]["state"] == "current"  # expiry transitioned nothing
    # The other instance of the principal never recorded anything.
    assert instance_of(w, w.ins2)[1]["observed"]["state"] == "unknown"


@pytest.mark.essential_safety
def test_10b_non_interactive_instance_is_not_applicable_for_attended(w):
    robot = new_instance_id()
    w.p.register(w.R, instance_id=robot, declaration=declaration(interactive=False, channels=()), idempotency_key="robot-key")
    expect("fact-not-applicable", w.p.observe, w.R, instance_id=robot, fact="attended", valid_for_seconds=60,
           idempotency_key=w.key())
    _, entry = instance_of(w, robot)
    assert entry["attended"] == {"state": "not-applicable", "observed_at": None, "valid_until": None, "source": None}
    assert entry["reachable"] == []
    w.p.observe(w.R, instance_id=robot, fact="observed", valid_for_seconds=60, idempotency_key=w.key())
    assert instance_of(w, robot)[1]["attended"]["state"] == "not-applicable"


@pytest.mark.essential_workflow
def test_10c_observation_arguments(w):
    base = dict(instance_id=w.ins1, idempotency_key=None)
    for kw in ({"fact": "observed", "valid_for_seconds": 0}, {"fact": "observed", "valid_for_seconds": 3601},
               {"fact": "reachable", "valid_for_seconds": 5}, {"fact": "observed", "valid_for_seconds": 5, "channel": "mcp"},
               {"fact": "executing", "valid_for_seconds": 5}, {"fact": "attended", "valid_for_seconds": 5, "run_id": w.run},
               {"fact": "present", "valid_for_seconds": 5}):
        expect("invalid-arguments", w.p.observe, w.R, **{**base, "idempotency_key": w.key(), **kw})
    expect("not-found", w.p.observe, w.R, instance_id=new_instance_id(), fact="observed", valid_for_seconds=5,
           idempotency_key=w.key())
    for key in ("short", "has space!", "k" * 129, None):
        expect("invalid-arguments", w.p.observe, w.R, instance_id=w.ins1, fact="observed", valid_for_seconds=5,
               idempotency_key=key)
    same = dict(instance_id=w.ins1, fact="observed", valid_for_seconds=60, idempotency_key="same-key-1")
    first = w.p.observe(w.R, **same)
    assert w.p.observe(w.R, **same)["replayed"] is True
    assert w.p.observe(w.R, **same)["observed_at"] == first["observed_at"]
    expect("idempotency-conflict", w.p.observe, w.R, **{**same, "valid_for_seconds": 61})


# 11. Shadow queue -----------------------------------------------------------

@pytest.mark.essential_safety
def test_11_message_must_name_a_live_owner_record(w):
    elsewhere = Ctx("ws-a", "repo-elsewhere", w.S.principal_id)
    w.p.new_item(elsewhere)
    foreign_item = w.p.new_item(elsewhere)  # an id the home repository never minted
    foreign_run = w.p.new_run(elsewhere)
    ghost_ids = []
    for context in ({"item_id": w.item + 100000}, {"predecessor_run_id": "run_" + "1" * 26},
                    {"predecessor_run_id": foreign_run}, {"item_id": w.item + 100000, "predecessor_run_id": w.run}):
        message = w.message(context=context)
        expect("context-not-found", w.p.send, w.S, message=message)
        ghost_ids.append(message["message_id"])
    expect("context-not-found", w.p.send, w.S, message=w.message(context={"item_id": foreign_item}))
    expect("invalid-arguments", w.p.send, w.S, message=w.message(context={"correlation_id": "abcdefgh"}))
    for message_id in ghost_ids:
        expect("not-found", w.p.status, w.S, message_id=message_id)
    assert w.p.read(w.R, after_seq=0, limit=100)["messages"] == []
    w.p.send(w.S, message=w.message(context={"predecessor_run_id": w.run}))
    w.p.send(w.S, message=w.message(context={"item_id": w.item, "predecessor_run_id": w.run,
                                             "correlation_id": "corr-0001", "work_link": "vuoro-work://link/v1/abc_-9"}))


@pytest.mark.essential_safety
def test_11b_recipient_must_be_registered_in_scope(w):
    expect("recipient-unknown", w.p.send, w.S, message=w.message(recipient={"principal_id": "github:77:0"}))
    expect("recipient-unknown", w.p.send, w.S, message=w.message(
        recipient={"principal_id": w.R.principal_id, "instance_id": new_instance_id()}))
    mine = new_instance_id()
    w.p.register(w.X, instance_id=mine, declaration=declaration(), idempotency_key="xx-key-001")
    expect("recipient-unknown", w.p.send, w.S, message=w.message(
        recipient={"principal_id": w.R.principal_id, "instance_id": mine}))  # belongs to another principal
    assert w.p.read(w.R, after_seq=0, limit=100)["messages"] == []


# 12. Limits -----------------------------------------------------------------

@pytest.mark.essential_safety
def test_12_envelope_limit_and_delegation(w):
    at_limit = sized_message(w, 64 * 1024)
    w.p.send(w.S, message=at_limit)
    over = sized_message(w, 64 * 1024 + 1)
    expect("payload-too-large", w.p.send, w.S, message=over)
    expect("not-found", w.p.status, w.S, message_id=over["message_id"])
    delegation = w.message(mode="delegation")
    expect("mode-not-supported", w.p.send, w.S, message=delegation)
    expect("not-found", w.p.status, w.S, message_id=delegation["message_id"])
    assert [m["message_id"] for m in w.p.read(w.R, after_seq=0, limit=100)["messages"]] == [at_limit["message_id"]]


@pytest.mark.essential_safety
def test_12b_body_and_artifact_limits(w):
    w.p.send(w.S, message=w.message(body={"text": "x" * 16384}))
    # 16 KiB is counted in UTF-8 bytes: 8193 two-byte characters are one byte over.
    error = refused(w.p.send, w.S, message=w.message(body={"text": "\u00e9" * 8193}))
    assert error.code in {"payload-too-large", "invalid-arguments"}
    error = refused(w.p.send, w.S, message=w.message(
        artifacts=[{"uri": "u", "sha256": "0" * 64, "size_bytes": 1}] * 17))
    assert error.code in {"payload-too-large", "invalid-arguments"}
    expect("invalid-arguments", w.p.send, w.S, message={**w.message(), "extra": 1})
    assert len(w.p.read(w.R, after_seq=0, limit=100)["messages"]) == 1


# 13. Commit order -----------------------------------------------------------

@pytest.mark.essential_safety
def test_13_commit_order_equals_sequence_order(w):
    need(w.p, CONCURRENT_COMMIT_ORDER)
    a, b = w.message(), w.message()
    outcome = w.p.send_concurrently_reversed(w.S, a, b, w.R)
    assert sorted(outcome.commit_order) == sorted([a["message_id"], b["message_id"]])
    seen = outcome.first_poll["messages"] + outcome.second_poll["messages"]
    assert sorted(m["message_id"] for m in seen) == sorted(outcome.commit_order), \
        "a reader polling between the commits skipped a committed message"
    assert [m["message_id"] for m in seen] == outcome.commit_order
    assert seqs({"messages": seen}) == sorted(set(seqs({"messages": seen})))
    final = w.p.read(w.R, after_seq=0, limit=100)
    assert [m["message_id"] for m in final["messages"]] == outcome.commit_order


# 14. Close without run ------------------------------------------------------

@pytest.mark.essential_workflow
def test_14_accepted_message_without_run_closes_not_started(w):
    m = w.send()
    w.rcpt(m, "accepted", w.ins1)
    for outcome in ("completed", "failed", "canceled"):
        refused(w.rcpt, m, "result-linked", w.ins1, outcome=outcome, evidence_refs=[], summary="never ran")
    expect("invalid-arguments", w.rcpt, m, "result-linked", w.ins1, outcome="not-started", evidence_refs=[],
           summary="no reason")
    closed = w.rcpt(m, "result-linked", w.ins1, outcome="not-started", evidence_refs=[], summary="policy denies now",
                    reason={"code": "policy-recheck", "text": "grant removed"})
    assert closed["outcome"] == "not-started" and closed["reason"]["code"] == "policy-recheck"
    assert w.kinds(m) == ["accepted", "result-linked"]
    refused(w.rcpt, w.send(), "result-linked", w.ins1, outcome="not-started", evidence_refs=[], summary="",
            reason={"code": "x", "text": "never accepted"})


@pytest.mark.essential_workflow
def test_14b_abandoned_by_a_sibling_only_after_the_acceptor_retires(w):
    m = w.send()
    w.rcpt(m, "accepted", w.ins1)
    gone = dict(outcome="abandoned", evidence_refs=[], summary="host lost")
    refused(w.rcpt, m, "result-linked", w.ins2, **gone)  # acceptor still alive
    w.p.retire(w.R, instance_id=w.ins1, reason="host lost", idempotency_key=w.key())
    expect("instance-retired", w.rcpt, m, "result-linked", w.ins1, **gone)
    refused(w.rcpt, m, "result-linked", w.ins2, outcome="not-started", evidence_refs=[], summary="",
            reason={"code": "x", "text": "y"})  # only abandoned may be written by the sibling
    refused(w.rcpt, m, "result-linked", w.ins2, outcome="completed", evidence_refs=[], summary="")
    closed = w.rcpt(m, "result-linked", w.ins2, **gone)
    assert (closed["instance_id"], closed["principal_id"]) == (w.ins2, w.R.principal_id), "the receipt names the writer"
    assert w.kinds(m) == ["accepted", "result-linked"]


@pytest.mark.essential_workflow
def test_14c_run_closes_completed(w):
    m = w.send()
    w.accept_and_link(m)
    refs = [{"kind": "commit", "ref": "abc123"}]
    done = w.rcpt(m, "result-linked", outcome="completed", evidence_refs=refs, summary="merged")
    assert w.rcpt(m, "result-linked", outcome="completed", evidence_refs=refs, summary="merged")["replayed"] is True
    expect("already-resulted", w.rcpt, m, "result-linked", outcome="completed", evidence_refs=refs, summary="other")
    expect("already-resulted", w.rcpt, m, "result-linked", outcome="failed", evidence_refs=[], summary="merged")
    assert done["evidence_refs"] == refs and w.kinds(m) == ["accepted", "run-linked", "result-linked"]


# 15. Receipt replay ---------------------------------------------------------

@pytest.mark.essential_workflow
def test_15_same_receipt_replays_and_same_key_with_other_fields_conflicts(w):
    m = w.send()
    originals = {}
    for kind in ("observed", "acknowledged", "accepted"):
        originals[kind] = w.rcpt(m, kind, w.ins1)
        assert originals[kind]["replayed"] is False
    originals["run-linked"] = w.rcpt(m, "run-linked", w.ins1, run_id=w.run)
    originals["launch-ambiguous"] = w.rcpt(m, "launch-ambiguous", w.ins1, detail="spawn timed out")
    originals["result-linked"] = w.rcpt(m, "result-linked", w.ins1, outcome="failed", evidence_refs=[], summary="boom")
    if CLOCK in w.p.capabilities:
        w.p.advance_clock(30)
    count = len(w.kinds(m))
    fields = {"run-linked": {"run_id": w.run}, "launch-ambiguous": {"detail": "spawn timed out"},
              "result-linked": {"outcome": "failed", "evidence_refs": [], "summary": "boom"}}
    for kind, original in originals.items():
        again = w.rcpt(m, kind, w.ins1, **fields.get(kind, {}))
        assert again["replayed"] is True
        assert {k: v for k, v in again.items() if k != "replayed"} == {k: v for k, v in original.items() if k != "replayed"}
    assert len(w.kinds(m)) == count
    expect("already-recorded", w.rcpt, m, "launch-ambiguous", w.ins1, detail="a different detail")
    expect("run-already-linked", w.rcpt, m, "run-linked", w.ins1, run_id=w.p.new_run(w.R))
    expect("already-resulted", w.rcpt, m, "result-linked", w.ins1, outcome="failed", evidence_refs=[], summary="bang")
    expect("already-decided", w.rcpt, m, "refused", w.ins1, reason={"code": "no", "text": "no"})
    assert len(w.kinds(m)) == count


@pytest.mark.essential_workflow
def test_15b_per_instance_receipts_and_prerequisites(w):
    m = w.send()
    w.rcpt(m, "observed", w.ins1)
    w.rcpt(m, "observed", w.ins2)  # natural key is (message, instance)
    assert w.kinds(m) == ["observed", "observed"]
    w.accept_and_link(m)
    refused(w.rcpt, m, "launch-ambiguous", w.ins2, detail="x")  # requires accepted by the same instance
    expect("invalid-arguments", w.rcpt, m, "mystery")
    expect("invalid-arguments", w.rcpt, m, "observed", run_id=w.run)


# 16. Retirement -------------------------------------------------------------

@pytest.mark.essential_safety
def test_16_retired_instance_is_listed_but_inert(w):
    m = w.send()
    w.p.observe(w.R, instance_id=w.ins1, fact="observed", valid_for_seconds=600, idempotency_key=w.key())
    retired = w.p.retire(w.R, instance_id=w.ins1, reason="host decommissioned", idempotency_key="retire-key")
    assert w.p.retire(w.R, instance_id=w.ins1, reason="host decommissioned", idempotency_key="retire-key")["replayed"] is True
    expect("instance-retired", w.p.retire, w.R, instance_id=w.ins1, reason="again", idempotency_key=w.key())
    expect("instance-retired", w.p.observe, w.R, instance_id=w.ins1, fact="observed", valid_for_seconds=60,
           idempotency_key=w.key())
    for kind in ("observed", "acknowledged", "accepted"):
        expect("instance-retired", w.rcpt, m, kind, w.ins1)
    expect("recipient-unknown", w.p.send, w.S, message=w.message(instance=w.ins1))
    expect("instance-retired", w.p.register, w.R, instance_id=w.ins1, declaration=declaration(),
           idempotency_key=w.key())
    assert w.kinds(m) == []
    _, entry = instance_of(w, w.ins1)
    assert entry["registered"]["state"] == "retired" and entry["registered"]["retire_reason"] == "host decommissioned"
    assert entry["registered"]["retired_at"] == retired["retired_at"]
    assert entry["observed"]["state"] == "current", "a retired instance stays listed with its facts"
    w.rcpt(m, "accepted", w.ins2)  # a sibling still works
    w.send(instance=w.ins2)


# Time never changes message state --------------------------------------------

@pytest.mark.essential_safety
def test_not_after_binds_the_receiver_not_the_store(w):
    need(w.p, CLOCK)
    m = w.send(not_after="2026-10-14T10:00:01Z")
    before = w.p.status(w.S, message_id=m)
    w.p.advance_clock(10 * 365 * 24 * 3600)
    assert w.p.status(w.S, message_id=m) == before
    assert [x["message_id"] for x in w.p.read(w.R, after_seq=0, limit=100)["messages"]] == [m]
    assert w.kinds(m) == []


@pytest.mark.essential_workflow
def test_15c_launch_ambiguous_needs_acceptance_not_a_run(w):
    m = w.send()
    expect("not-accepted", w.rcpt, m, "launch-ambiguous", w.ins1, detail="spawn timed out")
    w.rcpt(m, "accepted", w.ins1)
    expect("not-accepted", w.rcpt, m, "launch-ambiguous", w.ins2, detail="spawn timed out")
    expect("run-not-found", w.rcpt, m, "launch-ambiguous", w.ins1, detail="d", run_id="run_" + "0" * 26)
    first = w.rcpt(m, "launch-ambiguous", w.ins1, detail="registration timed out")
    assert first["replayed"] is False and "run_id" not in first
    again = w.rcpt(m, "launch-ambiguous", w.ins1, detail="registration timed out")
    assert again["replayed"] is True and again["recorded_at"] == first["recorded_at"]
    expect("already-recorded", w.rcpt, m, "launch-ambiguous", w.ins1, detail="different")
    expect("already-recorded", w.rcpt, m, "launch-ambiguous", w.ins1, detail="registration timed out", run_id=w.run)
    assert w.kinds(m) == ["accepted", "launch-ambiguous"]


@pytest.mark.essential_workflow
def test_15d_launch_ambiguous_may_name_a_run(w):
    m = w.send()
    w.rcpt(m, "accepted", w.ins1)
    first = w.rcpt(m, "launch-ambiguous", w.ins1, detail="launched, outcome unknown", run_id=w.run)
    assert first["run_id"] == w.run
    assert w.rcpt(m, "launch-ambiguous", w.ins1, detail="launched, outcome unknown", run_id=w.run)["replayed"] is True
    expect("already-recorded", w.rcpt, m, "launch-ambiguous", w.ins1, detail="launched, outcome unknown",
           run_id=w.p.new_run(w.R))
    expect("already-recorded", w.rcpt, m, "launch-ambiguous", w.ins1, detail="launched, outcome unknown")
