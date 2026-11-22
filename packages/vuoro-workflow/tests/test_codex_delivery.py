import json

from vuoro_workflow import codex, store

THREAD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER = "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee"


class Socket:
    def __init__(self, status="idle", thread_id=THREAD, lose_ack=False, **metadata):
        self.pending = []
        self.sent = []
        self.status = status
        self.thread_id = thread_id
        self.lose_ack = lose_ack
        self.metadata = metadata

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def send(self, text):
        request = json.loads(text)
        self.sent.append(request)
        if "id" not in request:
            return
        result = {
            "initialize": {},
            "thread/read": {"thread": {
                "id": self.thread_id, "status": {"type": self.status}, **self.metadata,
            }},
            "thread/resume": {"thread": {"id": self.thread_id, "status": {"type": "idle"}}},
            "turn/start": {"turn": {"id": "turn-1"}},
        }[request["method"]]
        if request["method"] != "turn/start" or not self.lose_ack:
            self.pending.append(json.dumps({"id": request["id"], "result": result}))

    def recv(self, **_):
        if not self.pending:
            raise TimeoutError
        return self.pending.pop(0)


def populate(path):
    spec = {
        "kind": "ci", "origin": "https://forge.example.org", "repository": "owner/repo",
        "workflow": "ci.yaml", "run": 14, "head": "a" * 40, "pr": 2,
        "base": "main", "ref": None, "environment": None, "digest": None,
        "thread": THREAD,
    }
    db = store.connect(path)
    store.register(db, spec, ttl=120)
    store.reconcile(db, lambda _: {"state": "success", "attempt": 1})
    return db


def test_codex_delivery_turn_and_ack_are_exact_and_preserve_permissions(tmp_path):
    db = populate(tmp_path / "test.sqlite")
    socket = Socket(status="notLoaded")
    event, = store.inbox(db, THREAD)
    result = codex.deliver(db, THREAD, "/unused", connect=lambda *_, **__: socket)
    assert result == {"state": "delivered", "turn": "turn-1", "event": event["id"]}
    request = socket.sent[-1]
    assert request["method"] == "turn/start"
    assert set(request["params"]) == {"threadId", "input"}
    assert request["params"]["threadId"] == THREAD
    assert event["id"] in request["params"]["input"][0]["text"]
    assert store.inbox(db, THREAD) == []
    receipt, = store.deliveries(db, THREAD)
    assert (receipt["event"], receipt["turn"]) == (event["id"], "turn-1")
    assert codex.deliver(db, THREAD, "/unused") == {"state": "empty"}


def test_codex_delivery_foreign_and_busy_thread_cannot_start_turn(tmp_path):
    db = populate(tmp_path / "test.sqlite")
    for socket, expected in [
        (Socket(thread_id=OTHER), "unavailable"),
        (Socket(status="active"), "thread-busy"),
        (Socket(canAcceptDirectInput=False), "thread-unavailable"),
        (Socket(path="/home/test/.codex/archived_sessions/thread.jsonl"), "thread-unavailable"),
    ]:
        result = codex.deliver(db, THREAD, "/unused", connect=lambda *_, **__: socket)
        assert result["state"] == expected
        assert all(request["method"] != "turn/start" for request in socket.sent)
        assert store.inbox(db, THREAD)[0]["delivery"] == "pending"


def test_codex_delivery_ambiguous_ack_is_retained_and_never_replayed(tmp_path):
    path = tmp_path / "test.sqlite"
    db = populate(path)
    socket = Socket(lose_ack=True)
    result = codex.deliver(db, THREAD, "/unused", connect=lambda *_, **__: socket)
    assert result["state"] == "delivery-uncertain"
    db.close()
    with store.connect(path) as restarted:
        assert store.inbox(restarted, THREAD)[0]["delivery"] == "claimed-or-uncertain"
        assert codex.deliver(restarted, THREAD, "/unused")["state"] == "empty"
