"""In-process reference provider for agent directory and inbox contract v1.

Stdlib only. It is the always-run executable statement of
docs/architecture/agent-directory-and-inbox.md: state is plain JSON-able data,
every stamp comes from the owner's controllable clock, and restart() proves the
state survives a JSON round trip.
"""
import copy
import datetime as dt
import hashlib
import json
import re

from .inbox_contract import (CLOCK, CONCURRENT_COMMIT_ORDER, RESTART, ContractRefusal,
                             Ctx, ReversedSends, ulid)

_ULID = "[0-9A-HJKMNP-TV-Z]{26}"
_INSTANCE = re.compile(f"^ins_{_ULID}$")
_MESSAGE = re.compile(f"^msg_{_ULID}$")
_RUN = re.compile(f"^run_{_ULID}$")
_CHANNEL = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")
_CODE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_WORK_LINK = re.compile(r"^vuoro-work://link/v1/[A-Za-z0-9_-]+$")
_CORRELATION = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_BODY_BYTES = 16 * 1024
_FACTS = ("observed", "reachable", "executing", "attended")
_DECISIONS = ("accepted", "refused")
_OUTCOMES = ("completed", "failed", "canceled", "abandoned", "not-started")
_RECEIPT_FIELDS = {
    "observed": set(), "acknowledged": set(), "accepted": set(), "refused": {"reason"},
    "run-linked": {"run_id"}, "launch-ambiguous": {"detail", "run_id"},
    "result-linked": {"outcome", "evidence_refs", "summary", "reason"},
}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(stamp) -> float:
    try:
        return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        raise ContractRefusal("invalid-arguments") from None


def _bad(condition):
    if not condition:
        raise ContractRefusal("invalid-arguments")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class _Pending:
    """A started, not yet committed send transaction."""
    def __init__(self, scope, message, sender, replay):
        self.scope, self.message, self.sender, self.replay, self.stamps = scope, message, sender, replay, None


class ReferenceInboxProvider:
    capabilities = frozenset({CLOCK, RESTART, CONCURRENT_COMMIT_ORDER})

    def __init__(self):
        self.now = 1_800_000_000.0
        self.state = {"scopes": {}}
        self.next_item = 1

    # -- helpers -----------------------------------------------------------
    def _scope(self, ctx: Ctx, create=True):
        key = f"{ctx.workspace_id}|{ctx.repo_id}"
        scopes = self.state["scopes"]
        if key not in scopes and create:
            scopes[key] = {"instances": {}, "observations": [], "messages": {}, "order": [],
                           "seq": 0, "idem": {}, "items": [], "runs": []}
        return scopes.get(key)

    def _idempotent(self, scope, ctx, op, key, args, run):
        _bad(isinstance(key, str) and _KEY.match(key))
        slot = f"{ctx.principal_id}|{op}|{key}"
        d = digest(args)
        if slot in scope["idem"]:
            stored_digest, result = scope["idem"][slot]
            if stored_digest != d:
                raise ContractRefusal("idempotency-conflict")
            return {**copy.deepcopy(result), "replayed": True}
        result = run()
        scope["idem"][slot] = [d, copy.deepcopy(result)]
        return {**result, "replayed": False}

    def _own_instance(self, scope, ctx, instance_id, allow_retired=False):
        _bad(isinstance(instance_id, str) and _INSTANCE.match(instance_id))
        inst = scope["instances"].get(instance_id)
        if inst is None:
            raise ContractRefusal("not-found")
        if inst["principal_id"] != ctx.principal_id:
            raise ContractRefusal("instance-not-owned")
        if inst["retired_at"] is not None and not allow_retired:
            raise ContractRefusal("instance-retired")
        return inst

    # -- test hooks --------------------------------------------------------
    def new_item(self, ctx):
        scope = self._scope(ctx)
        item = len(scope["items"]) + 1
        scope["items"].append(item)
        return item

    def new_run(self, ctx):
        run_id = "run_" + ulid()
        self._scope(ctx)["runs"].append(run_id)
        return run_id

    def advance_clock(self, seconds):
        self.now += seconds

    def restart(self):
        self.state = json.loads(json.dumps(self.state))

    # -- work.agent.* ------------------------------------------------------
    def _check_declaration(self, declaration):
        _bad(isinstance(declaration, dict))
        required = {"schema", "harness", "adapter", "host_class", "interactivity", "accepted_kinds", "channels"}
        _bad(set(declaration) == required and declaration["schema"] == "agent-declaration/v1")
        for part in (declaration["harness"], declaration["adapter"]):
            if part is not None:
                _bad(isinstance(part, dict) and set(part) == {"name", "version"}
                     and all(isinstance(v, str) and 1 <= len(v) <= 64 for v in part.values()))
        _bad(declaration["harness"] is not None)
        _bad(declaration["host_class"] in ("workstation", "devbox", "cloud-interactive", "cloud-unattended", "other"))
        _bad(declaration["interactivity"] in ("interactive", "non-interactive"))
        kinds = declaration["accepted_kinds"]
        _bad(isinstance(kinds, list) and len(kinds) <= 8 and len(set(kinds)) == len(kinds) and set(kinds) <= {"continue"})
        channels = declaration["channels"]
        _bad(isinstance(channels, list) and len(channels) <= 8)
        for entry in channels:
            _bad(isinstance(entry, dict) and set(entry) == {"channel", "wake_modes"}
                 and isinstance(entry["channel"], str) and _CHANNEL.match(entry["channel"])
                 and isinstance(entry["wake_modes"], list) and entry["wake_modes"]
                 and len(set(entry["wake_modes"])) == len(entry["wake_modes"])
                 and set(entry["wake_modes"]) <= {"read-while-running", "attention-during-run", "start-after-stop"})

    def register(self, ctx, *, instance_id, declaration, idempotency_key):
        _bad(isinstance(instance_id, str) and _INSTANCE.match(instance_id))
        self._check_declaration(declaration)
        scope = self._scope(ctx)

        def run():
            inst = scope["instances"].get(instance_id)
            if inst is None:
                inst = scope["instances"][instance_id] = {
                    "principal_id": ctx.principal_id, "registered_at": self.now, "retired_at": None,
                    "retire_reason": None, "declarations": []}
            elif inst["principal_id"] != ctx.principal_id:
                raise ContractRefusal("instance-not-owned")
            elif inst["retired_at"] is not None:
                raise ContractRefusal("instance-retired")
            if not inst["declarations"] or inst["declarations"][-1]["declaration"] != declaration:
                inst["declarations"].append({"declaration": copy.deepcopy(declaration), "at": self.now})
            return {"instance_id": instance_id, "declaration_revision": len(inst["declarations"])}
        return self._idempotent(scope, ctx, "register", idempotency_key,
                                {"instance_id": instance_id, "declaration": declaration}, run)

    def observe(self, ctx, *, instance_id, fact, valid_for_seconds, idempotency_key, channel=None, run_id=None):
        _bad(fact in _FACTS)
        _bad(_is_int(valid_for_seconds) and 1 <= valid_for_seconds <= 3600)
        if fact == "reachable":
            _bad(isinstance(channel, str) and _CHANNEL.match(channel))
        else:
            _bad(channel is None)
        if fact == "executing":
            _bad(isinstance(run_id, str) and _RUN.match(run_id))
        else:
            _bad(run_id is None)
        scope = self._scope(ctx)

        def run():
            inst = self._own_instance(scope, ctx, instance_id)
            if fact == "attended" and inst["declarations"][-1]["declaration"]["interactivity"] == "non-interactive":
                raise ContractRefusal("fact-not-applicable")
            row = {"instance_id": instance_id, "fact": fact, "channel": channel, "run_id": run_id,
                   "observed_at": self.now, "valid_until": self.now + valid_for_seconds}
            scope["observations"].append(row)
            return {"instance_id": instance_id, "fact": fact, "observed_at": iso(row["observed_at"]),
                    "valid_until": iso(row["valid_until"]), "source": "self-reported"}
        return self._idempotent(scope, ctx, "observe", idempotency_key,
                                {"instance_id": instance_id, "fact": fact, "channel": channel, "run_id": run_id,
                                 "valid_for_seconds": valid_for_seconds}, run)

    def retire(self, ctx, *, instance_id, reason, idempotency_key):
        _bad(isinstance(reason, str) and 1 <= len(reason) <= 2048)
        scope = self._scope(ctx)

        def run():
            inst = self._own_instance(scope, ctx, instance_id)
            inst["retired_at"], inst["retire_reason"] = self.now, reason
            return {"instance_id": instance_id, "retired_at": iso(self.now)}
        return self._idempotent(scope, ctx, "retire", idempotency_key,
                                {"instance_id": instance_id, "reason": reason}, run)

    def _fact(self, row):
        if row is None:
            return {"state": "unknown", "observed_at": None, "valid_until": None, "source": None}
        return {"state": "current" if self.now < row["valid_until"] else "stale",
                "observed_at": iso(row["observed_at"]), "valid_until": iso(row["valid_until"]),
                "source": "self-reported"}

    def _latest(self, scope, instance_id, fact, **match):
        found = None
        for row in scope["observations"]:
            if row["instance_id"] == instance_id and row["fact"] == fact and \
                    all(row[k] == v for k, v in match.items()):
                found = row
        return found

    def _entry(self, scope, principal_id):
        instances = []
        for instance_id, inst in scope["instances"].items():
            if inst["principal_id"] != principal_id:
                continue
            declaration = inst["declarations"][-1]["declaration"]
            channels = [c["channel"] for c in declaration["channels"]]
            channels += sorted({r["channel"] for r in scope["observations"]
                                if r["instance_id"] == instance_id and r["fact"] == "reachable"} - set(channels))
            runs = []
            for r in scope["observations"]:
                if r["instance_id"] == instance_id and r["fact"] == "executing" and r["run_id"] not in runs:
                    runs.append(r["run_id"])
            if declaration["interactivity"] == "non-interactive":
                attended = {"state": "not-applicable", "observed_at": None, "valid_until": None, "source": None}
            else:
                attended = self._fact(self._latest(scope, instance_id, "attended"))
            instances.append({
                "instance_id": instance_id, "declaration": copy.deepcopy(declaration),
                "declaration_revision": len(inst["declarations"]),
                "registered": {"state": "retired" if inst["retired_at"] is not None else "current",
                               "registered_at": iso(inst["registered_at"]),
                               "retired_at": None if inst["retired_at"] is None else iso(inst["retired_at"]),
                               "retire_reason": inst["retire_reason"]},
                "observed": self._fact(self._latest(scope, instance_id, "observed")),
                "reachable": [{**self._fact(self._latest(scope, instance_id, "reachable", channel=c)), "channel": c}
                              for c in channels],
                "executing": [{**self._fact(self._latest(scope, instance_id, "executing", run_id=r)), "run_id": r}
                              for r in runs],
                "attended": attended})
        return {"schema": "agent-directory-entry/v1", "principal_id": principal_id,
                "evaluated_at": iso(self.now), "instances": instances}

    def list(self, ctx):
        scope = self._scope(ctx, create=False)
        principals = sorted({i["principal_id"] for i in scope["instances"].values()}) if scope else []
        return {"entries": [self._entry(scope, p) for p in principals]}

    def describe(self, ctx, *, principal_id):
        _bad(isinstance(principal_id, str) and principal_id)
        scope = self._scope(ctx, create=False)
        if scope is None or not any(i["principal_id"] == principal_id for i in scope["instances"].values()):
            raise ContractRefusal("not-found")
        return self._entry(scope, principal_id)

    # -- work.message.* ----------------------------------------------------
    def _validate_message(self, message):
        _bad(isinstance(message, dict))
        allowed = {"schema", "message_id", "recipient", "kind", "mode", "context", "body", "artifacts",
                   "not_after", "created_at"}
        _bad(set(message) <= allowed and {"schema", "message_id", "recipient", "kind", "mode", "context",
                                          "body", "created_at"} <= set(message))
        _bad(message["schema"] == "agent-message/v1" and message["kind"] == "continue")
        _bad(isinstance(message["message_id"], str) and _MESSAGE.match(message["message_id"]))
        _bad(message["mode"] in ("request", "delegation"))
        recipient = message["recipient"]
        _bad(isinstance(recipient, dict) and set(recipient) <= {"principal_id", "instance_id"}
             and isinstance(recipient.get("principal_id"), str) and 1 <= len(recipient["principal_id"]) <= 512)
        _bad("instance_id" not in recipient or (isinstance(recipient["instance_id"], str)
                                               and _INSTANCE.match(recipient["instance_id"])))
        context = message["context"]
        _bad(isinstance(context, dict) and set(context) <= {"item_id", "predecessor_run_id", "work_link", "correlation_id"})
        _bad("item_id" in context or "predecessor_run_id" in context)
        _bad("item_id" not in context or (_is_int(context["item_id"]) and context["item_id"] >= 1))
        _bad("predecessor_run_id" not in context or (isinstance(context["predecessor_run_id"], str)
                                                    and _RUN.match(context["predecessor_run_id"])))
        _bad("work_link" not in context or (isinstance(context["work_link"], str)
                                           and _WORK_LINK.match(context["work_link"])
                                           and len(context["work_link"].encode()) <= 4096))
        _bad("correlation_id" not in context or (isinstance(context["correlation_id"], str)
                                                and _CORRELATION.match(context["correlation_id"])))
        body = message["body"]
        _bad(isinstance(body, dict) and set(body) == {"text"} and isinstance(body["text"], str))
        artifacts = message.get("artifacts", [])
        _bad(isinstance(artifacts, list))
        if len(artifacts) > 16:
            raise ContractRefusal("payload-too-large")
        for artifact in artifacts:
            _bad(isinstance(artifact, dict) and set(artifact) == {"uri", "sha256", "size_bytes"}
                 and isinstance(artifact["uri"], str) and 1 <= len(artifact["uri"]) <= 2048
                 and isinstance(artifact["sha256"], str) and _SHA256.match(artifact["sha256"])
                 and _is_int(artifact["size_bytes"]) and artifact["size_bytes"] >= 0)
        if "not_after" in message:
            _parse(message["not_after"])
        _parse(message["created_at"])

    def _begin_send(self, ctx, message):
        self._validate_message(message)
        if len(message["body"]["text"].encode("utf-8")) > MAX_BODY_BYTES or \
                len(canonical(message)) > MAX_ENVELOPE_BYTES:
            raise ContractRefusal("payload-too-large")
        scope = self._scope(ctx)
        existing = scope["messages"].get(message["message_id"])
        if existing is not None:
            if existing["sender_principal_id"] != ctx.principal_id or \
                    existing["envelope_digest"] != digest(message):
                raise ContractRefusal("message-id-conflict")
            return _Pending(scope, message, ctx.principal_id, existing)
        if message["mode"] == "delegation":
            raise ContractRefusal("mode-not-supported")
        recipient = message["recipient"]
        owned = [(i, v) for i, v in scope["instances"].items() if v["principal_id"] == recipient["principal_id"]]
        if not owned:
            raise ContractRefusal("recipient-unknown")
        if "instance_id" in recipient:
            target = dict(owned).get(recipient["instance_id"])
            if target is None or target["retired_at"] is not None:
                raise ContractRefusal("recipient-unknown")
        context = message["context"]
        if "item_id" in context and context["item_id"] not in scope["items"]:
            raise ContractRefusal("context-not-found")
        if "predecessor_run_id" in context and context["predecessor_run_id"] not in scope["runs"]:
            raise ContractRefusal("context-not-found")
        return _Pending(scope, message, ctx.principal_id, None)

    def _commit_send(self, ctx, pending):
        if pending.replay is not None:
            return self._stamps(pending.replay) | {"replayed": True}
        scope = pending.scope
        scope["seq"] += 1  # allocated at commit: commit order equals sequence order
        record = {"envelope": copy.deepcopy(pending.message), "sender_principal_id": pending.sender,
                  "workspace_id": ctx.workspace_id, "repo_id": ctx.repo_id, "persisted_at": iso(self.now),
                  "envelope_digest": digest(pending.message), "inbox_seq": scope["seq"], "receipts": []}
        scope["messages"][pending.message["message_id"]] = record
        scope["order"].append(pending.message["message_id"])
        return self._stamps(record) | {"replayed": False}

    @staticmethod
    def _stamps(record):
        return {k: record[k] for k in ("sender_principal_id", "workspace_id", "repo_id", "persisted_at",
                                       "envelope_digest", "inbox_seq")}

    def send(self, ctx, *, message):
        return self._commit_send(ctx, self._begin_send(ctx, message))

    def send_concurrently_reversed(self, sender, message_a, message_b, reader):
        pending_a = self._begin_send(sender, message_a)
        pending_b = self._begin_send(sender, message_b)
        self._commit_send(sender, pending_b)
        first = self.read(reader, after_seq=0, limit=100)
        self._commit_send(sender, pending_a)
        second = self.read(reader, after_seq=first["next_after_seq"], limit=100)
        return ReversedSends([message_b["message_id"], message_a["message_id"]], first, second)

    def _stamped(self, record):
        out = copy.deepcopy(record["envelope"])
        out.update(self._stamps(record))
        out["receipts"] = sorted(copy.deepcopy(record["receipts"]), key=lambda r: r["recorded_at"])
        return out

    def read(self, ctx, *, after_seq, limit, instance_id=None):
        _bad(_is_int(after_seq) and after_seq >= 0 and _is_int(limit) and 1 <= limit <= 100)
        scope = self._scope(ctx, create=False)
        if instance_id is not None:
            _bad(isinstance(instance_id, str) and _INSTANCE.match(instance_id))
            inst = scope["instances"].get(instance_id) if scope else None
            if inst is None:
                raise ContractRefusal("not-found")
            if inst["principal_id"] != ctx.principal_id:
                raise ContractRefusal("instance-not-owned")
        messages = []
        for message_id in (scope["order"] if scope else []):
            record = scope["messages"][message_id]
            recipient = record["envelope"]["recipient"]
            if recipient["principal_id"] != ctx.principal_id or record["inbox_seq"] <= after_seq:
                continue
            if "instance_id" in recipient and recipient["instance_id"] != instance_id:
                continue
            messages.append(record)
        messages.sort(key=lambda r: r["inbox_seq"])
        page = messages[:limit]
        return {"messages": [self._stamped(r) for r in page],
                "next_after_seq": page[-1]["inbox_seq"] if page else after_seq}

    def _visible(self, ctx, message_id):
        scope = self._scope(ctx, create=False)
        record = scope["messages"].get(message_id) if scope else None
        if record is None:
            raise ContractRefusal("not-found")
        return scope, record

    def status(self, ctx, *, message_id):
        _bad(isinstance(message_id, str) and _MESSAGE.match(message_id))
        _, record = self._visible(ctx, message_id)
        if ctx.principal_id not in (record["sender_principal_id"], record["envelope"]["recipient"]["principal_id"]):
            raise ContractRefusal("not-found")
        stamped = self._stamped(record)
        return {"message": stamped, "receipts": stamped["receipts"]}

    @staticmethod
    def _find(record, kind, instance_id=None):
        for r in record["receipts"]:
            if r["kind"] == kind and (instance_id is None or r["instance_id"] == instance_id):
                return r
        return None

    @staticmethod
    def _own(receipt):
        return {k: v for k, v in receipt.items()
                if k not in ("schema", "message_id", "kind", "principal_id", "instance_id", "recorded_at")}

    def receipt(self, ctx, *, message_id, instance_id, kind, **fields):
        _bad(isinstance(message_id, str) and _MESSAGE.match(message_id))
        scope, record = self._visible(ctx, message_id)
        recipient = record["envelope"]["recipient"]
        if ctx.principal_id not in (record["sender_principal_id"], recipient["principal_id"]):
            raise ContractRefusal("not-found")
        if ctx.principal_id != recipient["principal_id"]:
            raise ContractRefusal("forbidden")
        _bad(kind in _RECEIPT_FIELDS and set(fields) <= _RECEIPT_FIELDS[kind])
        self._own_instance(scope, ctx, instance_id)
        if kind in _DECISIONS and "instance_id" in recipient and recipient["instance_id"] != instance_id:
            raise ContractRefusal("forbidden")
        own = self._check_fields(kind, fields)
        found = self._conflict_or_replay(scope, record, instance_id, kind, own)
        if found is not None:
            return {**copy.deepcopy(found), "replayed": True}
        self._check_prerequisites(scope, record, instance_id, kind, own)
        new = {"schema": "agent-message-receipt/v1", "message_id": message_id, "kind": kind,
               "principal_id": ctx.principal_id, "instance_id": instance_id,
               "recorded_at": iso(self.now), **copy.deepcopy(own)}
        record["receipts"].append(new)
        return {**copy.deepcopy(new), "replayed": False}

    def _check_fields(self, kind, fields):
        if kind == "refused":
            reason = fields.get("reason")
            _bad(isinstance(reason, dict) and set(reason) == {"code", "text"} and isinstance(reason["code"], str)
                 and _CODE.match(reason["code"]) and isinstance(reason["text"], str) and len(reason["text"]) <= 2048)
            return {"reason": copy.deepcopy(reason)}
        if kind == "run-linked":
            _bad(isinstance(fields.get("run_id"), str) and _RUN.match(fields["run_id"]))
            return {"run_id": fields["run_id"]}
        if kind == "launch-ambiguous":
            _bad(isinstance(fields.get("detail"), str) and len(fields["detail"].encode()) <= 2048)
            own = {"detail": fields["detail"]}
            if "run_id" in fields:
                _bad(isinstance(fields["run_id"], str) and _RUN.match(fields["run_id"]))
                own["run_id"] = fields["run_id"]
            return own
        if kind == "result-linked":
            _bad(fields.get("outcome") in _OUTCOMES)
            refs = fields.get("evidence_refs", [])
            _bad(isinstance(refs, list) and len(refs) <= 32 and all(
                isinstance(r, dict) and set(r) == {"kind", "ref"} and isinstance(r["kind"], str)
                and re.match(r"^[a-z][a-z0-9-]{0,31}$", r["kind"]) and isinstance(r["ref"], str)
                and 1 <= len(r["ref"]) <= 512 for r in refs))
            summary = fields.get("summary", "")
            _bad(isinstance(summary, str) and len(summary.encode()) <= 2048)
            own = {"outcome": fields["outcome"], "evidence_refs": copy.deepcopy(refs), "summary": summary}
            if "reason" in fields:
                reason = fields["reason"]
                _bad(isinstance(reason, dict) and set(reason) == {"code", "text"} and isinstance(reason["code"], str)
                     and _CODE.match(reason["code"]) and isinstance(reason["text"], str) and len(reason["text"]) <= 2048)
                own["reason"] = copy.deepcopy(reason)
            _bad(fields["outcome"] != "not-started" or "reason" in own)
            return own
        return {}

    def _conflict_or_replay(self, scope, record, instance_id, kind, own):
        """Natural-key uniqueness. Returns the replayed receipt, raises the kind's conflict code, or None."""
        if kind in ("observed", "acknowledged"):
            return self._find(record, kind, instance_id)
        if kind in _DECISIONS:
            decided = self._find(record, "accepted") or self._find(record, "refused")
            if decided is None:
                return None
            if decided["instance_id"] == instance_id and decided["kind"] == kind and \
                    digest(self._own(decided)) == digest(own):
                return decided
            raise ContractRefusal("already-decided", {"instance_id": decided["instance_id"], "kind": decided["kind"]})
        if kind == "run-linked":
            linked = self._find(record, "run-linked")
            if linked is None:
                return None
            if linked["run_id"] == own["run_id"] and linked["instance_id"] == instance_id:
                return linked
            raise ContractRefusal("run-already-linked")
        if kind == "launch-ambiguous":
            found = self._find(record, "launch-ambiguous", instance_id)
            if found is None:
                return None
            if digest(self._own(found)) == digest(own):
                return found
            raise ContractRefusal("already-recorded")
        found = self._find(record, "result-linked")
        if found is None:
            return None
        if digest(self._own(found)) == digest(own):
            return found
        raise ContractRefusal("already-resulted")

    def _check_prerequisites(self, scope, record, instance_id, kind, own):
        accepted = self._find(record, "accepted")
        if kind == "run-linked":
            if own["run_id"] not in scope["runs"]:
                raise ContractRefusal("run-not-found")
            if accepted is None or accepted["instance_id"] != instance_id:
                raise ContractRefusal("not-accepted")
        elif kind == "launch-ambiguous":
            if accepted is None or accepted["instance_id"] != instance_id:
                raise ContractRefusal("not-accepted")
            if "run_id" in own and own["run_id"] not in scope["runs"]:
                raise ContractRefusal("run-not-found")
        elif kind == "result-linked":
            if accepted is None:
                raise ContractRefusal("not-accepted")
            outcome = own["outcome"]
            winner = scope["instances"][accepted["instance_id"]]
            if outcome in ("completed", "failed", "canceled"):
                linked = self._find(record, "run-linked")
                if linked is None or linked["instance_id"] != instance_id:
                    raise ContractRefusal("not-accepted")
            elif accepted["instance_id"] != instance_id and not (
                    outcome == "abandoned" and winner["retired_at"] is not None):
                raise ContractRefusal("not-accepted")

    def cancel(self, ctx, *, message_id, reason):
        _bad(isinstance(message_id, str) and _MESSAGE.match(message_id))
        _bad(isinstance(reason, str) and len(reason.encode()) <= 2048)
        scope, record = self._visible(ctx, message_id)
        if record["sender_principal_id"] != ctx.principal_id:
            raise ContractRefusal("not-sender")
        first = self._find(record, "cancel-requested")
        if first is not None:
            return {**copy.deepcopy(first), "replayed": True}
        new = {"schema": "agent-message-receipt/v1", "message_id": message_id, "kind": "cancel-requested",
               "principal_id": ctx.principal_id, "instance_id": None, "recorded_at": iso(self.now),
               "reason": reason}
        record["receipts"].append(new)
        return {**copy.deepcopy(new), "replayed": False}

