"""Deliver a durable event over an existing local Codex app-server socket."""

import json
import re
import time
from pathlib import Path

from . import store


class AdapterUnavailable(Exception):
    """The connected app server did not give a usable response."""


def request(socket, number, method, params):
    socket.send(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}))
    deadline = time.monotonic() + 10
    while (remaining := deadline - time.monotonic()) > 0:
        response = json.loads(socket.recv(timeout=remaining))
        if response.get("id") != number:
            continue
        if "error" in response:
            raise AdapterUnavailable(f"Codex rejected {method}")
        return response["result"]
    raise TimeoutError


def message(event):
    return (
        f"Vuoro workflow event {event['id']}. The scoped Forgejo watcher observed "
        f"{event['state']} for {event['kind']} run {event['run']} "
        f"(attempt {event['attempt']}), repository {event['repository']}, "
        f"exact commit {event['head']}. "
        "Recheck the current source and required gates before resuming the existing task. "
        "This observation carries no approval or additional permissions."
    )


def deliver(db, thread_id, socket_path, connect=None):
    events = store.deliverable(db, thread_id)
    if not events:
        return {"state": "empty"}
    if connect is None:
        from websockets.sync.client import unix_connect

        connect = unix_connect
    try:
        with connect(str(socket_path), open_timeout=5, close_timeout=1) as socket:
            request(socket, 1, "initialize", {
                "clientInfo": {"name": "vuoro-workflow", "version": "0.1.0"},
            })
            socket.send('{"jsonrpc":"2.0","method":"initialized"}')
            thread = request(socket, 2, "thread/read", {
                "threadId": thread_id, "includeTurns": False,
            })["thread"]
            if thread["id"] != thread_id:
                raise AdapterUnavailable("Codex returned a different thread")
            path = Path(thread.get("path") or "")
            if thread.get("ephemeral") or "archived_sessions" in path.parts:
                return {"state": "thread-unavailable"}
            if thread.get("canAcceptDirectInput") is False:
                return {"state": "thread-unavailable"}
            if thread["status"]["type"] not in {"idle", "notLoaded"}:
                return {"state": "thread-busy"}
            event = events[0]
            if thread["status"]["type"] == "notLoaded":
                # Preserve the persisted thread and its policy. Supply no policy overrides.
                resumed = request(socket, 3, "thread/resume", {"threadId": thread_id})["thread"]
                if resumed["id"] != thread_id or resumed["status"]["type"] != "idle":
                    raise AdapterUnavailable("Codex did not resume the bound thread to idle")
            if not store.claim_delivery(db, thread_id, event["id"]):
                return {"state": "already-claimed"}
            try:
                turn = request(socket, 4, "turn/start", {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": message(event)}],
                })["turn"]["id"]
                if not isinstance(turn, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", turn):
                    raise AdapterUnavailable("Codex turn acknowledgement is invalid")
                store.finish_delivery(db, thread_id, event["id"], turn)
                return {"state": "delivered", "event": event["id"], "turn": turn}
            except Exception:
                # A crash or transport failure can follow an accepted turn. Keep the
                # event visible, and leave the claim in place for operator reconciliation.
                return {"state": "delivery-uncertain", "event": event["id"]}
    except Exception as error:
        return {"state": "unavailable", "error": type(error).__name__}
