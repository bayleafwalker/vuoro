"""Owner-bound JSON-RPC over an inherited local stdio channel."""

import json
import re
import sys
import time
from uuid import NAMESPACE_URL, uuid5

from . import codex, store

CI = "forgejo.ci.completed/v1"
PROMOTION = "forgejo.promotion.completed/v1"
TEMPLATES = (CI, PROMOTION)
METHODS = {
    "wake.capabilities": "wake.read",
    "wake.register": "wake.write",
    "wake.get": "wake.read",
    "wake.receipts": "wake.read",
    "wake.cancel": "wake.write",
}
MAX_FRAME = 65536
NAME = r"[A-Za-z0-9_.-]{1,100}"
LABEL = r"[A-Za-z0-9_.@/-]{1,200}"
THREAD = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
HASH = r"[0-9a-f]{64}"


class Rejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def invalid(message):
    raise Rejected(-32602, message)


def fields(value, required=(), optional=()):
    if not isinstance(value, dict):
        invalid("expected an object")
    if not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        invalid("missing or unexpected fields")
    return value


def string(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        invalid("invalid string identifier")
    if any(segment in {".", ".."} for segment in value.split("/")):
        invalid("invalid string identifier")
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        invalid("integer out of range")
    return value


def exact_json(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("invalid JSON constant")

    return json.loads(payload, object_pairs_hook=unique, parse_constant=constant)


def load_profile(path):
    """Load a launcher-controlled binding. Request frames never supply this file."""
    with path.open("rb") as file:
        payload = file.read(MAX_FRAME + 1)
    if len(payload) > MAX_FRAME:
        invalid("profile exceeds size bound")
    profile = fields(exact_json(payload), (
        "identity", "destination", "scopes", "max_ttl_seconds", "sources",
    ))
    identity = fields(profile["identity"], ("workspace", "principal", "harness", "instance"))
    for value in identity.values():
        string(value, NAME)
    destination = fields(profile["destination"], ("kind", "instance"), ("thread",))
    string(destination["instance"], NAME)
    if destination["kind"] == "codex":
        string(destination.get("thread"), THREAD)
    elif destination["kind"] != "inbox" or "thread" in destination:
        invalid("invalid destination")
    scopes = profile["scopes"]
    if not isinstance(scopes, list) or not scopes or any(
        scope not in ("wake.read", "wake.write") for scope in scopes
    ):
        invalid("invalid profile scopes")
    integer(profile["max_ttl_seconds"], 1, 604800)
    sources = profile["sources"]
    if not isinstance(sources, dict) or not sources:
        invalid("invalid profile sources")
    for alias, source in sources.items():
        string(alias, NAME)
        fields(source, ("origin", "repository", "policies"))
        string(source["origin"], r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?")
        string(source["repository"], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
        if not isinstance(source["policies"], dict) or not source["policies"]:
            invalid("invalid source policies")
        for template, policy in source["policies"].items():
            validate_policy(template, policy)
    return profile


def validate_policy(template, policy):
    if template == CI:
        fields(policy, ("workflow", "event", "required", "base"))
        string(policy["base"], LABEL)
        if policy["event"] != "pull_request":
            invalid("CI policy event must be pull_request")
    elif template == PROMOTION:
        fields(policy, ("workflow", "event", "required", "environment"), ("digest_input",))
        string(policy["environment"], LABEL)
        string(policy.get("digest_input", "image_digest"), LABEL)
        if policy["event"] != "workflow_dispatch":
            invalid("promotion policy event must be workflow_dispatch")
    else:
        invalid("unknown rule template")
    string(policy["workflow"], LABEL)
    required = policy["required"]
    if not isinstance(required, list) or not 1 <= len(required) <= 100:
        invalid("invalid required jobs")
    for name in required:
        string(name, LABEL)
    if len(required) != len(set(required)):
        invalid("duplicate required jobs")


class WakeApi:
    def __init__(self, db, profile):
        self.db, self.profile = db, profile
        self.destination = profile["destination"]
        self.owner = store.identity({
            "identity": profile["identity"],
            "destination": self.destination,
        })
        if self.destination["kind"] == "codex":
            self.thread = codex.destination_channel(
                self.destination["thread"], self.destination["instance"]
            )
        else:
            self.thread = str(uuid5(NAMESPACE_URL, f"vuoro:workflow:inbox:{self.owner}"))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS wake_registrations (
                owner TEXT NOT NULL, request_key TEXT NOT NULL, request TEXT NOT NULL,
                watch TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY (owner, request_key)
            );
            CREATE INDEX IF NOT EXISTS wake_registrations_watch
            ON wake_registrations (owner, watch);
            CREATE TABLE IF NOT EXISTS wake_suppressions (
                event TEXT PRIMARY KEY, cancelled REAL NOT NULL
            );
        """)

    def call(self, method, params):
        scope = METHODS.get(method)
        if not scope:
            raise Rejected(-32601, "unknown method")
        if scope not in self.profile["scopes"]:
            raise Rejected(-32003, "method forbidden")
        if method == "wake.capabilities":
            return self.capabilities(params)
        if method == "wake.register":
            return self.register(params)
        if method == "wake.get":
            fields(params, ("rule_id",))
            return self.find(params["rule_id"])
        if method == "wake.receipts":
            return self.receipts(params)
        fields(params, ("rule_id",))
        return self.cancel(params["rule_id"])

    def capabilities(self, params):
        fields(params)
        return {
            "protocol": "vuoro.wake/v1",
            "templates": sorted({
                template for source in self.profile["sources"].values()
                for template in source["policies"]
            }),
            "sources": {
                name: sorted(source["policies"])
                for name, source in self.profile["sources"].items()
            },
            "scopes": self.profile["scopes"],
            "max_ttl_seconds": self.profile["max_ttl_seconds"],
            # A host installer may enable the relay independently of this API.
            "delivery": "inbox" if self.destination["kind"] == "inbox" else "codex-relay-eligible",
        }

    def to_watch(self, request):
        template = request["template"]
        alias = string(request["source_alias"], NAME)
        if template not in TEMPLATES:
            invalid("unsupported rule template")
        source = self.profile["sources"].get(alias, {})
        policy = source.get("policies", {}).get(template)
        if policy is None:
            raise Rejected(-32003, "source or template forbidden")
        params = request["params"]
        keys = ("run", "head", "pr") if template == CI else ("run", "head", "ref", "digest")
        fields(params, keys)
        return {
            "owner": self.owner,
            "origin": source["origin"], "repository": source["repository"],
            "source_alias": alias, "template": template, "thread": self.thread,
            "kind": "ci" if template == CI else "promotion",
            "run": integer(params["run"], 1, 2**53 - 1),
            "head": string(params["head"], r"[0-9a-f]{40}"),
            "pr": integer(params["pr"], 1, 2**53 - 1) if template == CI else None,
            "ref": string(params["ref"], LABEL) if template == PROMOTION else None,
            "digest": string(params["digest"], r"sha256:[0-9a-f]{64}") if template == PROMOTION else None,
            "workflow": policy["workflow"], "event": policy["event"],
            "required": sorted(policy["required"]), "base": policy.get("base"),
            "environment": policy.get("environment"),
            "digest_input": policy.get("digest_input", "image_digest"),
        }

    def register(self, request):
        fields(request, ("template", "source_alias", "params", "ttl_seconds", "idempotency_key"))
        key = string(request["idempotency_key"], r"[A-Za-z0-9._:-]{8,128}")
        ttl = integer(request["ttl_seconds"], 1, self.profile["max_ttl_seconds"])
        spec = self.to_watch(request)
        watch = store.identity(spec)
        encoded = store.encode({"spec": spec, "ttl_seconds": ttl})
        now = time.time()
        with self.db:
            # Start with the write so concurrent identical requests serialize.
            result = self.db.execute(
                """INSERT OR IGNORE INTO wake_registrations
                   (owner, request_key, request, watch, created) VALUES (?, ?, ?, ?, ?)""",
                (self.owner, key, encoded, watch, now),
            )
            if result.rowcount:
                self.db.execute(
                    "INSERT OR IGNORE INTO watches (id, spec, expires) VALUES (?, ?, ?)",
                    (watch, store.encode(spec), now + ttl),
                )
            else:
                previous = self.db.execute(
                    """SELECT request FROM wake_registrations
                       WHERE owner = ? AND request_key = ?""",
                    (self.owner, key),
                ).fetchone()
                if previous["request"] != encoded:
                    raise Rejected(-32009, "idempotency key conflict")
        return self.find(watch)

    def find(self, watch):
        string(watch, HASH)
        row = self.db.execute(
            """SELECT id, spec, active, observation, expires, checked FROM watches
               WHERE id = ? AND EXISTS (
                   SELECT 1 FROM wake_registrations WHERE owner = ? AND watch = watches.id
               )""",
            (watch, self.owner),
        ).fetchone()
        if row is None:
            raise Rejected(-32004, "rule not found")
        spec = json.loads(row["spec"])
        return {
            "rule_id": watch, "template": spec["template"], "source_alias": spec["source_alias"],
            "active": bool(row["active"]) and row["expires"] > time.time(),
            "expires_at": row["expires"], "observed_at": row["checked"],
            "observation": json.loads(row["observation"]) if row["observation"] else None,
        }

    def receipts(self, params):
        fields(params, ("rule_id",), ("cursor", "limit"))
        watch = self.find(params["rule_id"])["rule_id"]
        cursor = integer(params.get("cursor", 0), 0, 2**53 - 1)
        limit = integer(params.get("limit", 25), 1, 100)
        rows = self.db.execute(
            """SELECT outbox.rowid AS cursor, outbox.id, outbox.body, outbox.created,
                      outbox.acknowledged, deliveries.claimed, deliveries.turn,
                      wake_suppressions.cancelled
               FROM outbox LEFT JOIN deliveries ON deliveries.event = outbox.id
               LEFT JOIN wake_suppressions ON wake_suppressions.event = outbox.id
               WHERE outbox.watch = ? AND outbox.rowid > ? ORDER BY outbox.rowid LIMIT ?""",
            (watch, cursor, limit + 1),
        ).fetchall()
        returned = rows[:limit]
        receipts = []
        for row in returned:
            event = json.loads(row["body"])
            receipts.append({
                "event_id": row["id"], "state": event["state"], "attempt": event["attempt"],
                "observed_at": row["created"],
                "acknowledged_at": row["acknowledged"] if row["cancelled"] is None else None,
                "cancelled_at": row["cancelled"],
                "delivery_claimed_at": row["claimed"], "turn_id": row["turn"],
            })
        return {
            "receipts": receipts, "next_cursor": returned[-1]["cursor"] if returned else cursor,
            "has_more": len(rows) > limit,
        }

    def cancel(self, watch):
        self.find(watch)
        with self.db:
            self.db.execute("UPDATE watches SET active = 0 WHERE id = ?", (watch,))
            now = time.time()
            suppressed = self.db.execute(
                """INSERT OR IGNORE INTO wake_suppressions (event, cancelled)
                   SELECT id, ? FROM outbox WHERE watch = ? AND acknowledged IS NULL
                     AND NOT EXISTS (SELECT 1 FROM deliveries WHERE deliveries.event = outbox.id)""",
                (now, watch),
            ).rowcount
            self.db.execute(
                """UPDATE outbox SET acknowledged = ?
                   WHERE watch = ? AND acknowledged IS NULL
                     AND EXISTS (
                         SELECT 1 FROM wake_suppressions WHERE wake_suppressions.event = outbox.id
                     )""",
                (now, watch),
            )
            inflight, = self.db.execute(
                """SELECT count(*) FROM outbox JOIN deliveries ON deliveries.event = outbox.id
                   WHERE outbox.watch = ? AND deliveries.turn IS NULL""", (watch,),
            ).fetchone()
        return {"rule_id": watch, "active": False, "suppressed": suppressed, "delivery_uncertain": inflight}


def response(api, payload):
    request_id = None
    try:
        frame = exact_json(payload)
    except (ValueError, RecursionError):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
    try:
        if (
            not isinstance(frame, dict)
            or not {"jsonrpc", "id", "method"} <= frame.keys()
            or frame.keys() - {"jsonrpc", "id", "method", "params"}
        ):
            raise Rejected(-32600, "invalid request")
        request_id = frame["id"]
        if (frame["jsonrpc"] != "2.0" or type(request_id) not in (str, int)
                or not isinstance(frame["method"], str)):
            request_id = None
            raise Rejected(-32600, "invalid request")
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": api.call(frame["method"], frame.get("params", {})),
        }
    except Rejected as error:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": error.code, "message": str(error)}}


def serve(api, source=None, target=None):
    source = sys.stdin.buffer if source is None else source
    target = sys.stdout if target is None else target
    while frame := source.readline(MAX_FRAME + 1):
        if len(frame) > MAX_FRAME:
            print(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "frame exceeds size bound"},
            }), file=target, flush=True)
            return 1
        print(json.dumps(response(api, frame)), file=target, flush=True)
    return 0
