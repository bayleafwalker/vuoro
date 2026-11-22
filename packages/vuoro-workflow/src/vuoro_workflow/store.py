"""Host-local subscription and transactional outbox."""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from .forgejo import TERMINAL, Unavailable, observe_scoped


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def identity(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def connect(path):
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS watches (
            id TEXT PRIMARY KEY, spec TEXT NOT NULL, expires REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, observation TEXT, checked REAL,
            lease TEXT, lease_expires REAL
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY, watch TEXT NOT NULL, thread TEXT NOT NULL,
            body TEXT NOT NULL, created REAL NOT NULL, acknowledged REAL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            event TEXT PRIMARY KEY, thread TEXT NOT NULL, claimed REAL NOT NULL, turn TEXT
        );
    """)
    return connection


def register(db, spec, ttl):
    identifier = identity(spec)
    with db:
        db.execute(
            "INSERT OR IGNORE INTO watches (id, spec, expires) VALUES (?, ?, ?)",
            (identifier, encode(spec), time.time() + ttl),
        )
    return identifier


def reconcile(db, observe_run=observe_scoped, clock=time.time):
    now = clock()
    with db:
        db.execute("UPDATE watches SET active = 0 WHERE active = 1 AND expires <= ?", (now,))
    identifiers = db.execute("SELECT id FROM watches WHERE active = 1 ORDER BY id").fetchall()
    for item in identifiers:
        key = item["id"]
        lease = uuid4().hex
        now = clock()
        with db:
            row = db.execute(
                """UPDATE watches SET lease = ?, lease_expires = ?
                   WHERE id = ? AND active = 1 AND expires > ?
                     AND (lease_expires IS NULL OR lease_expires <= ?)
                   RETURNING spec""",
                (lease, now + 360, key, now, now),
            ).fetchone()
        if row is None:
            continue
        spec = json.loads(row["spec"])
        try:
            observation = observe_run(spec)
        except Unavailable as error:
            observation = {"state": "unavailable", "reason": str(error)}
        now = clock()
        with db:
            updated = db.execute(
                """UPDATE watches SET observation = ?, checked = ?,
                       lease = NULL, lease_expires = NULL
                   WHERE id = ? AND lease = ? AND active = 1 AND expires > ?""",
                (encode(observation), now, key, lease, now),
            )
            if not updated.rowcount or observation["state"] not in TERMINAL:
                continue
            event = {
                "watch": key,
                "state": observation["state"],
                "attempt": observation["attempt"],
                **{field: spec[field] for field in (
                    "kind", "origin", "repository", "workflow", "run", "head", "pr",
                    "base", "ref", "environment", "digest", "thread",
                )},
            }
            event["id"] = identity(event)
            db.execute(
                "INSERT OR IGNORE INTO outbox (id, watch, thread, body, created) VALUES (?, ?, ?, ?, ?)",
                (event["id"], key, spec["thread"], encode(event), now),
            )


def status(db):
    rows = db.execute("SELECT * FROM watches ORDER BY id")
    return [
        {
            "id": row["id"], "spec": json.loads(row["spec"]),
            "active": bool(row["active"]) and row["expires"] > time.time(),
            "expires": row["expires"], "checked": row["checked"],
            "observation": json.loads(row["observation"]) if row["observation"] else None,
        }
        for row in rows
    ]


def inbox(db, thread):
    rows = db.execute(
        """SELECT body, created, deliveries.claimed FROM outbox
           LEFT JOIN deliveries ON deliveries.event = outbox.id
           WHERE outbox.thread = ? AND acknowledged IS NULL ORDER BY created, id""",
        (thread,),
    )
    return [
        {
            **json.loads(row["body"]), "observed_at": row["created"],
            "delivery": "claimed-or-uncertain" if row["claimed"] is not None else "pending",
        }
        for row in rows
    ]


def deliverable(db, thread):
    return [event for event in inbox(db, thread) if event["delivery"] == "pending"]


def claim_delivery(db, thread, event):
    with db:
        return bool(db.execute(
            """INSERT OR IGNORE INTO deliveries (event, thread, claimed)
               SELECT id, thread, ? FROM outbox
               WHERE id = ? AND thread = ? AND acknowledged IS NULL""",
            (time.time(), event, thread),
        ).rowcount)


def finish_delivery(db, thread, event, turn):
    with db:
        updated = db.execute(
            "UPDATE deliveries SET turn = ? WHERE thread = ? AND event = ? AND turn IS NULL",
            (turn, thread, event),
        )
        if not updated.rowcount:
            raise ValueError("delivery claim missing")
        db.execute(
            """UPDATE outbox SET acknowledged = coalesce(acknowledged, ?)
               WHERE thread = ? AND id = ?""",
            (time.time(), thread, event),
        )


def deliveries(db, thread):
    rows = db.execute(
        """SELECT event, claimed, turn FROM deliveries WHERE thread = ? ORDER BY claimed, event""",
        (thread,),
    )
    return [dict(row) for row in rows]


def acknowledge(db, thread, event):
    with db:
        return bool(db.execute(
            """UPDATE outbox SET acknowledged = coalesce(acknowledged, ?)
               WHERE thread = ? AND id = ?""", (time.time(), thread, event),
        ).rowcount)


def stop(db, watch):
    with db:
        return bool(db.execute("UPDATE watches SET active = 0 WHERE id = ?", (watch,)).rowcount)


def default_path():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "vuoro-workflow" / "watch.sqlite3"
