"""Knowledge workspace store: plain SQL over psycopg 3, no ORM, no DDL.

Every public operation re-checks the caller's membership. Every mutation
writes its rows, one ``workspace_event`` (per-workspace ``seq`` from a locked
counter row), one ``outbox`` row and one ``audit_event`` in one transaction.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import errors as E
from .errors import WorkspaceError
from .extraction import EXTRACTOR, EXTRACTOR_VERSION, extract
from .ids import canonical_json, new_ulid, normalize, request_digest
from .migrations import DEFAULT_SCHEMA, require_compatible_schema
from .objectstore import ObjectStore, artifact_key, resolve_media_type, workspace_prefix

ROLES = ("reader", "contributor", "reviewer", "administrator")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES, start=1)}
READ, CONTRIBUTE, REVIEW, ADMIN = (
    "knowledge:read",
    "knowledge:contribute",
    "knowledge:review",
    "knowledge:admin",
)
AUTHORITY_RANK = {READ: 1, CONTRIBUTE: 2, REVIEW: 3, ADMIN: 4}
AGENT_RANK_CAP = ROLE_RANK["contributor"]
VISIBILITY_AUTHORITY = {"published": READ, "working": CONTRIBUTE, "admin": ADMIN}
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
SCOPES = ("approved", "working", "history")
RELATION_KINDS = ("derived_from", "supports", "supersedes", "cites")
GLOBAL_SCOPE = "*"
CURSOR_FORMAT = 1


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller: credential authorities plus the agent marker."""

    principal_id: str
    authorities: frozenset[str] = field(default_factory=frozenset)
    is_agent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, str) or not self.principal_id.strip():
            raise ValueError("principal_id must be non-empty")
        object.__setattr__(self, "authorities", frozenset(self.authorities))


@dataclass(frozen=True, slots=True)
class Access:
    role: str
    is_agent: bool
    effective: frozenset[str]

    def can(self, authority: str) -> bool:
        return authority in self.effective


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceStore:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        object_store: ObjectStore,
        *,
        schema: str = DEFAULT_SCHEMA,
        clock: Callable[[], datetime] = _utcnow,
        reservation_ttl: timedelta = timedelta(hours=1),
        max_artifact_bytes: int = 100 * 1024 * 1024,
        verify_timeout: timedelta = timedelta(minutes=15),
        runtime_role: str | None = None,
    ) -> None:
        if not conn.autocommit:
            raise ValueError("WorkspaceStore needs an autocommit connection; it manages its own transactions")
        self.schema_report = require_compatible_schema(conn, schema, runtime_role)
        conn.row_factory = dict_row  # type: ignore[assignment]
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('search_path', %s, false)", (f'"{schema}"',))
        self.conn = conn
        self.objects = object_store
        self.schema = schema
        self._clock = clock
        self.reservation_ttl = reservation_ttl
        self.max_artifact_bytes = max_artifact_bytes
        self.verify_timeout = verify_timeout

    @classmethod
    def connect(cls, dsn: str, object_store: ObjectStore, **kwargs: Any) -> "WorkspaceStore":
        return cls(psycopg.connect(dsn, autocommit=True), object_store, **kwargs)

    def close(self) -> None:
        self.conn.close()

    def now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    # ------------------------------------------------------------------ core

    def _authorize(self, cur: Any, p: Principal, ws: str, authority: str) -> Access:
        if authority not in p.authorities:
            raise WorkspaceError(E.FORBIDDEN, f"credential lacks {authority}")
        cur.execute(
            "SELECT w.deleted_at, m.role, m.is_agent FROM workspace w "
            "LEFT JOIN workspace_member m ON m.workspace_id = w.workspace_id AND m.principal_id = %s "
            "WHERE w.workspace_id = %s",
            (p.principal_id, ws),
        )
        row = cur.fetchone()
        if row is None or row["deleted_at"] is not None or row["role"] is None:
            # Same answer for "no such workspace" and "not a member": no existence leak.
            raise WorkspaceError(E.FORBIDDEN, "not a member of this workspace")
        rank = ROLE_RANK[row["role"]]
        agent = bool(row["is_agent"]) or p.is_agent
        if agent:
            rank = min(rank, AGENT_RANK_CAP)
        effective = frozenset(a for a in p.authorities if AUTHORITY_RANK.get(a, 99) <= rank)
        if authority not in effective:
            raise WorkspaceError(E.FORBIDDEN, f"role {row['role']}{' (agent)' if agent else ''} does not grant {authority}")
        return Access(row["role"], agent, effective)

    def _mutate(
        self,
        p: Principal,
        ws: str,
        operation: str,
        authority: str,
        request: Mapping[str, Any],
        key: str | None,
        fn: Callable[[Any, Access | None], dict[str, Any]],
        *,
        authorize: bool = True,
    ) -> dict[str, Any]:
        if key is not None and (not isinstance(key, str) or not IDEMPOTENCY_KEY_RE.fullmatch(key)):
            raise WorkspaceError(E.INVALID, "idempotency key must match ^[A-Za-z0-9._:-]{8,128}$")
        with self.conn.transaction(), self.conn.cursor() as cur:
            access = self._authorize(cur, p, ws, authority) if authorize else None
            digest = request_digest(operation, request)
            if key is not None:
                replay = self._ledger_lookup(cur, ws, p, operation, key, digest)
                if replay is not None:
                    return replay
            result = normalize(fn(cur, access))
            if key is not None:
                self._ledger_store(cur, ws, p, operation, key, digest, result)
            return result

    def _ledger_lookup(self, cur: Any, ws: str, p: Principal, operation: str, key: str, digest: str) -> dict[str, Any] | None:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (canonical_json([ws, p.principal_id, operation, key]),),
        )
        cur.execute(
            "SELECT request_digest, result FROM idempotency_ledger "
            "WHERE workspace_id = %s AND principal_id = %s AND operation = %s AND idempotency_key = %s",
            (ws, p.principal_id, operation, key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        if row["request_digest"] != digest:
            raise WorkspaceError(E.IDEMPOTENCY_CONFLICT, "idempotency key reused with a different request", operation=operation)
        return row["result"]

    def _ledger_store(self, cur: Any, ws: str, p: Principal, operation: str, key: str, digest: str, result: Any) -> None:
        cur.execute(
            "INSERT INTO idempotency_ledger (workspace_id, principal_id, operation, idempotency_key, "
            "request_digest, result, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (ws, p.principal_id, operation, key, digest, Jsonb(result), self.now()),
        )

    def _emit(
        self,
        cur: Any,
        ws: str,
        actor: str,
        kind: str,
        entity_kind: str,
        entity_id: str,
        *,
        version_id: str | None = None,
        visibility: str = "working",
        payload: Mapping[str, Any] | None = None,
        tombstone: bool = False,
    ) -> int:
        cur.execute(
            "UPDATE workspace_seq SET last_seq = last_seq + 1 WHERE workspace_id = %s RETURNING last_seq", (ws,)
        )
        seq = cur.fetchone()["last_seq"]
        now = self.now()
        body = normalize(dict(payload or {}))
        cur.execute(
            "INSERT INTO workspace_event (workspace_id, seq, event_id, kind, entity_kind, entity_id, version_id, "
            "visibility, tombstone, actor_id, payload, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ws, seq, new_ulid(), kind, entity_kind, entity_id, version_id, visibility, tombstone, actor, Jsonb(body), now),
        )
        envelope = {
            "workspace_id": ws, "seq": seq, "kind": kind, "entity_kind": entity_kind, "entity_id": entity_id,
            "version_id": version_id, "tombstone": tombstone, "actor_id": actor, "payload": body,
        }
        cur.execute(
            "INSERT INTO outbox (outbox_id, workspace_id, seq, topic, payload, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (new_ulid(), ws, seq, f"knowledge.{kind}", Jsonb(envelope), now),
        )
        self._audit(cur, ws, actor, kind, entity_kind, version_id or entity_id, "ok", body)
        return seq

    def _audit(self, cur: Any, ws: str | None, actor: str, action: str, target_kind: str | None,
               target_id: str | None, outcome: str, detail: Mapping[str, Any]) -> None:
        cur.execute(
            "INSERT INTO audit_event (audit_id, workspace_id, actor_id, action, target_kind, target_id, outcome, "
            "detail, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (new_ulid(), ws, actor, action, target_kind, target_id, outcome, Jsonb(normalize(dict(detail))), self.now()),
        )

    def _watermark(self, cur: Any, ws: str) -> int:
        cur.execute("SELECT last_seq FROM workspace_seq WHERE workspace_id = %s", (ws,))
        return int(cur.fetchone()["last_seq"])

    @staticmethod
    def _visible(access: Access) -> list[str]:
        return [v for v, auth in VISIBILITY_AUTHORITY.items() if access.can(auth)]

    def _visible_watermark(self, cur: Any, ws: str, access: Access) -> int:
        """Highest event seq this caller may see; hidden activity never moves it.

        One max() per visibility class so each is an index-only backward scan
        of (workspace_id, visibility, seq).
        """
        classes = self._visible(access)
        parts = ", ".join(
            "(SELECT max(seq) FROM workspace_event WHERE workspace_id = %s AND visibility = %s)" for _ in classes
        )
        params: list[Any] = []
        for visibility in classes:
            params += [ws, visibility]
        cur.execute(f"SELECT coalesce(GREATEST({parts}), 0) AS seq", params)
        return int(cur.fetchone()["seq"])

    # ------------------------------------------------------- workspace/member

    def workspace_create(self, p: Principal, name: str, *, tenant_id: str = "default",
                         idempotency_key: str | None = None) -> dict[str, Any]:
        if ADMIN not in p.authorities:
            raise WorkspaceError(E.FORBIDDEN, f"credential lacks {ADMIN}")
        if p.is_agent:
            raise WorkspaceError(E.FORBIDDEN, "agent principals cannot administer workspaces")
        _text(name, "name")

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            ws = new_ulid()
            self._insert_workspace(cur, ws, tenant_id, name, p.principal_id, None)
            seq = self._emit(cur, ws, p.principal_id, "workspace.created", "workspace", ws,
                             visibility="published", payload={"name": name})
            return {"workspace_id": ws, "name": name, "tenant_id": tenant_id, "seq": seq}

        return self._mutate(p, GLOBAL_SCOPE, "workspace.create", ADMIN, {"name": name, "tenant_id": tenant_id},
                            idempotency_key, run, authorize=False)

    def _insert_workspace(self, cur: Any, ws: str, tenant_id: str, name: str, actor: str, origin: Any) -> None:
        now = self.now()
        cur.execute(
            "INSERT INTO workspace (workspace_id, tenant_id, name, created_by, created_at, origin) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (ws, tenant_id, name, actor, now, Jsonb(origin) if origin is not None else None),
        )
        cur.execute("INSERT INTO workspace_seq (workspace_id) VALUES (%s)", (ws,))
        cur.execute(
            "INSERT INTO workspace_member (workspace_id, principal_id, role, is_agent, updated_by, updated_at) "
            "VALUES (%s,%s,'administrator',false,%s,%s)",
            (ws, actor, actor, now),
        )

    def member_set(self, p: Principal, ws: str, principal_id: str, role: str | None, *,
                   is_agent: bool | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        """Add, change (``role``) or remove (``role=None``) a member.

        ``is_agent=None`` keeps an existing member's agent marker (new members
        default to non-agent); clearing the marker needs an explicit False.
        """
        _text(principal_id, "principal_id")
        if role is not None and role not in ROLES:
            raise WorkspaceError(E.INVALID, f"role must be one of {ROLES}")
        if role is not None and is_agent and ROLE_RANK[role] > AGENT_RANK_CAP:
            raise WorkspaceError(E.INVALID, "agent principals are capped at contributor")

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            # Serialize membership changes per workspace (the counter row is
            # every mutation's lock), then re-check the caller under the lock.
            cur.execute("SELECT 1 FROM workspace_seq WHERE workspace_id = %s FOR UPDATE", (ws,))
            self._authorize(cur, p, ws, ADMIN)
            cur.execute(
                "SELECT role, is_agent FROM workspace_member WHERE workspace_id = %s AND principal_id = %s FOR UPDATE",
                (ws, principal_id),
            )
            current = cur.fetchone()
            agent = bool(current["is_agent"]) if (is_agent is None and current) else bool(is_agent)
            if role is not None and agent and ROLE_RANK[role] > AGENT_RANK_CAP:
                raise WorkspaceError(E.INVALID, "agent principals are capped at contributor")
            if current and current["role"] == "administrator" and role != "administrator":
                cur.execute(
                    "SELECT count(*) AS n FROM workspace_member WHERE workspace_id = %s AND role = 'administrator'",
                    (ws,),
                )
                if cur.fetchone()["n"] <= 1:
                    raise WorkspaceError(E.STATE_CONFLICT, "a workspace keeps at least one administrator")
            if role is None:
                if current is None:
                    raise WorkspaceError(E.NOT_FOUND, "no such member")
                cur.execute("DELETE FROM workspace_member WHERE workspace_id = %s AND principal_id = %s",
                            (ws, principal_id))
            else:
                cur.execute(
                    "INSERT INTO workspace_member (workspace_id, principal_id, role, is_agent, updated_by, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (workspace_id, principal_id) DO UPDATE SET "
                    "role = EXCLUDED.role, is_agent = EXCLUDED.is_agent, updated_by = EXCLUDED.updated_by, "
                    "updated_at = EXCLUDED.updated_at",
                    (ws, principal_id, role, agent, p.principal_id, self.now()),
                )
            result = {"principal_id": principal_id, "role": role, "is_agent": agent if role else None}
            self._emit(cur, ws, p.principal_id, "member.removed" if role is None else "member.set", "member",
                       principal_id, visibility="admin", payload=result)
            return result

        return self._mutate(p, ws, "member.set", ADMIN, {"principal_id": principal_id, "role": role,
                                                          "is_agent": is_agent}, idempotency_key, run)

    # -------------------------------------------------------------- knowledge

    def item_create(self, p: Principal, ws: str, title: str, body_md: str, *, sources: Sequence[Any] = (),
                    relations: Sequence[Mapping[str, Any]] = (), idempotency_key: str | None = None) -> dict[str, Any]:
        _text(title, "title")
        _body(body_md)
        request = {"title": title, "body_md": body_md, "sources": list(sources), "relations": list(relations)}

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            item_id, revision_id = new_ulid(), new_ulid()
            cur.execute(
                "INSERT INTO knowledge_item (item_id, workspace_id, latest_revision_id, created_by, created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (item_id, ws, revision_id, p.principal_id, self.now()),
            )
            self._insert_revision(cur, ws, item_id, revision_id, None, title, body_md, p.principal_id, sources)
            self._insert_relations(cur, ws, p.principal_id, "revision", revision_id, relations)
            self._emit(cur, ws, p.principal_id, "item.created", "item", item_id, version_id=revision_id,
                       payload={"title": title, "state": "draft"})
            return {"item_id": item_id, "revision_id": revision_id, "state": "draft"}

        return self._mutate(p, ws, "item.create", CONTRIBUTE, request, idempotency_key, run)

    def _insert_revision(self, cur: Any, ws: str, item_id: str, revision_id: str, parent: str | None, title: str,
                         body_md: str, author: str, sources: Sequence[Any], state: str = "draft",
                         reviewed_by: str | None = None, reviewed_at: Any = None, created_at: Any = None) -> None:
        cur.execute(
            "INSERT INTO knowledge_revision (revision_id, workspace_id, item_id, parent_revision_id, title, body_md, "
            "content_sha256, author_id, sources, state, reviewed_by, reviewed_at, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (revision_id, ws, item_id, parent, title, body_md, _sha256(body_md.encode()), author,
             Jsonb(normalize(list(sources))), state, reviewed_by, reviewed_at, created_at or self.now()),
        )

    def _insert_relations(self, cur: Any, ws: str, actor: str, source_kind: str, source_id: str,
                          relations: Sequence[Mapping[str, Any]]) -> list[str]:
        ids = []
        for rel in relations:
            kind, target_kind = rel.get("kind"), rel.get("target_kind")
            target_id, target_ref = rel.get("target_id"), rel.get("target_ref")
            if kind not in RELATION_KINDS:
                raise WorkspaceError(E.INVALID, f"relation kind must be one of {RELATION_KINDS}")
            if target_kind == "revision":
                cur.execute(
                    "SELECT 1 FROM knowledge_revision r JOIN knowledge_item i ON i.item_id = r.item_id "
                    "WHERE r.revision_id = %s AND r.workspace_id = %s AND i.workspace_id = %s AND i.deleted_at IS NULL",
                    (target_id, ws, ws),
                )
            elif target_kind == "artifact_version":
                cur.execute(
                    "SELECT 1 FROM artifact_version v JOIN artifact a ON a.artifact_id = v.artifact_id "
                    "WHERE v.version_id = %s AND v.workspace_id = %s AND a.workspace_id = %s "
                    "AND a.deleted_at IS NULL AND v.processing_state = 'ready'",
                    (target_id, ws, ws),
                )
            elif target_kind == "repository_document":
                if not (isinstance(target_ref, Mapping) and isinstance(target_ref.get("commit"), str)
                        and isinstance(target_ref.get("path"), str) and target_ref.get("repository")):
                    raise WorkspaceError(E.INVALID, "repository_document relations need target_ref {repository, commit, path}")
                target_id = None
            else:
                raise WorkspaceError(E.INVALID, "relation target_kind must be revision, artifact_version or repository_document")
            if target_kind != "repository_document" and cur.fetchone() is None:
                raise WorkspaceError(E.NOT_FOUND, "relation target is not an available version in this workspace",
                                     target_id=target_id)
            relation_id = new_ulid()
            cur.execute(
                "INSERT INTO relation (relation_id, workspace_id, kind, source_kind, source_id, target_kind, target_id, "
                "target_ref, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (relation_id, ws, kind, source_kind, source_id, target_kind, target_id,
                 Jsonb(normalize(dict(target_ref))) if target_ref is not None else None, actor, self.now()),
            )
            ids.append(relation_id)
        return ids

    def _item_for_update(self, cur: Any, ws: str, item_id: str) -> dict[str, Any]:
        cur.execute(
            "SELECT * FROM knowledge_item WHERE item_id = %s AND workspace_id = %s AND deleted_at IS NULL FOR UPDATE",
            (item_id, ws),
        )
        row = cur.fetchone()
        if row is None:
            raise WorkspaceError(E.NOT_FOUND, "item not found")
        return row

    def revision_draft(self, p: Principal, ws: str, item_id: str, title: str, body_md: str, *,
                       parent_revision_id: str | None = None, sources: Sequence[Any] = (),
                       relations: Sequence[Mapping[str, Any]] = (), idempotency_key: str | None = None) -> dict[str, Any]:
        _text(title, "title")
        _body(body_md)
        request = {"item_id": item_id, "title": title, "body_md": body_md, "parent_revision_id": parent_revision_id,
                   "sources": list(sources), "relations": list(relations)}

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            item = self._item_for_update(cur, ws, item_id)
            parent = parent_revision_id or item["approved_revision_id"]
            if parent_revision_id is not None:
                cur.execute("SELECT 1 FROM knowledge_revision WHERE revision_id = %s AND item_id = %s AND workspace_id = %s",
                            (parent_revision_id, item_id, ws))
                if cur.fetchone() is None:
                    raise WorkspaceError(E.NOT_FOUND, "parent revision not found on this item")
            revision_id = new_ulid()
            self._insert_revision(cur, ws, item_id, revision_id, parent, title, body_md, p.principal_id, sources)
            cur.execute("UPDATE knowledge_item SET latest_revision_id = %s WHERE item_id = %s", (revision_id, item_id))
            self._insert_relations(cur, ws, p.principal_id, "revision", revision_id, relations)
            self._emit(cur, ws, p.principal_id, "revision.drafted", "item", item_id, version_id=revision_id,
                       payload={"parent_revision_id": parent})
            return {"item_id": item_id, "revision_id": revision_id, "parent_revision_id": parent, "state": "draft"}

        return self._mutate(p, ws, "revision.draft", CONTRIBUTE, request, idempotency_key, run)

    def proposal_submit(self, p: Principal, ws: str, revision_id: str, expected_base_revision_id: str | None, *,
                        rationale: str = "", evidence: Sequence[Any] = (),
                        idempotency_key: str | None = None) -> dict[str, Any]:
        """Propose a draft. ``expected_base_revision_id`` is explicit: None means "no approved revision yet"."""
        request = {"revision_id": revision_id, "expected_base_revision_id": expected_base_revision_id,
                   "rationale": rationale, "evidence": list(evidence)}

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            cur.execute(
                "SELECT r.revision_id, r.item_id, r.state FROM knowledge_revision r JOIN knowledge_item i "
                "ON i.item_id = r.item_id WHERE r.revision_id = %s AND r.workspace_id = %s AND i.deleted_at IS NULL "
                "FOR UPDATE OF r",
                (revision_id, ws),
            )
            rev = cur.fetchone()
            if rev is None:
                raise WorkspaceError(E.NOT_FOUND, "revision not found")
            if rev["state"] != "draft":
                raise WorkspaceError(E.STATE_CONFLICT, f"revision is {rev['state']}, not draft")
            if expected_base_revision_id is not None:
                cur.execute("SELECT 1 FROM knowledge_revision WHERE revision_id = %s AND item_id = %s AND workspace_id = %s",
                            (expected_base_revision_id, rev["item_id"], ws))
                if cur.fetchone() is None:
                    raise WorkspaceError(E.NOT_FOUND, "expected base revision not found on this item")
            proposal_id = new_ulid()
            cur.execute("UPDATE knowledge_revision SET state = 'proposed' WHERE revision_id = %s", (revision_id,))
            cur.execute(
                "INSERT INTO proposal (proposal_id, workspace_id, item_id, revision_id, expected_base_revision_id, "
                "rationale, evidence, author_id, state, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'open',%s)",
                (proposal_id, ws, rev["item_id"], revision_id, expected_base_revision_id, rationale,
                 Jsonb(normalize(list(evidence))), p.principal_id, self.now()),
            )
            self._emit(cur, ws, p.principal_id, "proposal.submitted", "proposal", proposal_id, version_id=revision_id,
                       payload={"item_id": rev["item_id"], "expected_base_revision_id": expected_base_revision_id})
            return {"proposal_id": proposal_id, "item_id": rev["item_id"], "revision_id": revision_id, "state": "open"}

        return self._mutate(p, ws, "proposal.submit", CONTRIBUTE, request, idempotency_key, run)

    def proposal_review(self, p: Principal, ws: str, proposal_id: str, decision: str, *, note: str | None = None,
                        idempotency_key: str | None = None) -> dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise WorkspaceError(E.INVALID, "decision must be approve or reject")

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            cur.execute(
                "SELECT pr.*, r.author_id AS revision_author FROM proposal pr JOIN knowledge_revision r "
                "ON r.revision_id = pr.revision_id WHERE pr.proposal_id = %s AND pr.workspace_id = %s FOR UPDATE OF pr",
                (proposal_id, ws),
            )
            prop = cur.fetchone()
            if prop is None:
                raise WorkspaceError(E.NOT_FOUND, "proposal not found")
            if p.principal_id in (prop["author_id"], prop["revision_author"]):
                raise WorkspaceError(E.FORBIDDEN, "a proposal's author cannot review it")
            if prop["state"] != "open":
                raise WorkspaceError(E.STATE_CONFLICT, f"proposal is {prop['state']}")
            item = self._item_for_update(cur, ws, prop["item_id"])
            now = self.now()
            if decision == "reject":
                cur.execute("UPDATE knowledge_revision SET state = 'rejected', reviewed_by = %s, reviewed_at = %s "
                            "WHERE revision_id = %s", (p.principal_id, now, prop["revision_id"]))
                cur.execute("UPDATE proposal SET state = 'rejected', reviewer_id = %s, review_note = %s, decided_at = %s "
                            "WHERE proposal_id = %s", (p.principal_id, note, now, proposal_id))
                self._emit(cur, ws, p.principal_id, "proposal.rejected", "proposal", proposal_id,
                           version_id=prop["revision_id"], payload={"item_id": prop["item_id"]})
                return {"proposal_id": proposal_id, "state": "rejected", "item_id": prop["item_id"],
                        "revision_id": prop["revision_id"]}
            current = item["approved_revision_id"]
            if current != prop["expected_base_revision_id"]:
                raise WorkspaceError(
                    E.STALE_BASE, "the item's approved revision moved since this proposal was made",
                    expected_base_revision_id=prop["expected_base_revision_id"], current_revision_id=current,
                    current_revision=normalize(self._revision_row(cur, current)) if current else None,
                )
            if current:
                cur.execute("UPDATE knowledge_revision SET state = 'superseded' WHERE revision_id = %s", (current,))
            cur.execute("UPDATE knowledge_revision SET state = 'approved', reviewed_by = %s, reviewed_at = %s "
                        "WHERE revision_id = %s", (p.principal_id, now, prop["revision_id"]))
            cur.execute("UPDATE knowledge_item SET approved_revision_id = %s WHERE item_id = %s",
                        (prop["revision_id"], prop["item_id"]))
            cur.execute("UPDATE proposal SET state = 'approved', reviewer_id = %s, review_note = %s, decided_at = %s "
                        "WHERE proposal_id = %s", (p.principal_id, note, now, proposal_id))
            self._emit(cur, ws, p.principal_id, "proposal.approved", "item", prop["item_id"],
                       version_id=prop["revision_id"], visibility="published",
                       payload={"proposal_id": proposal_id, "superseded_revision_id": current})
            return {"proposal_id": proposal_id, "state": "approved", "item_id": prop["item_id"],
                    "approved_revision_id": prop["revision_id"], "superseded_revision_id": current}

        return self._mutate(p, ws, "proposal.review", REVIEW, {"proposal_id": proposal_id, "decision": decision,
                                                                "note": note}, idempotency_key, run)

    def proposal_withdraw(self, p: Principal, ws: str, proposal_id: str, *,
                          idempotency_key: str | None = None) -> dict[str, Any]:
        def run(cur: Any, access: Access | None) -> dict[str, Any]:
            cur.execute("SELECT * FROM proposal WHERE proposal_id = %s AND workspace_id = %s FOR UPDATE",
                        (proposal_id, ws))
            prop = cur.fetchone()
            if prop is None:
                raise WorkspaceError(E.NOT_FOUND, "proposal not found")
            if prop["author_id"] != p.principal_id and not (access and access.can(ADMIN)):
                raise WorkspaceError(E.FORBIDDEN, "only the author or an administrator can withdraw a proposal")
            if prop["state"] != "open":
                raise WorkspaceError(E.STATE_CONFLICT, f"proposal is {prop['state']}")
            cur.execute("UPDATE proposal SET state = 'withdrawn', decided_at = %s WHERE proposal_id = %s",
                        (self.now(), proposal_id))
            cur.execute("UPDATE knowledge_revision SET state = 'withdrawn' WHERE revision_id = %s", (prop["revision_id"],))
            self._emit(cur, ws, p.principal_id, "proposal.withdrawn", "proposal", proposal_id,
                       version_id=prop["revision_id"], payload={"item_id": prop["item_id"]})
            return {"proposal_id": proposal_id, "state": "withdrawn", "revision_id": prop["revision_id"]}

        return self._mutate(p, ws, "proposal.withdraw", CONTRIBUTE, {"proposal_id": proposal_id}, idempotency_key, run)

    def _revision_row(self, cur: Any, revision_id: str) -> dict[str, Any]:
        cur.execute(
            "SELECT revision_id, item_id, parent_revision_id, title, body_md, content_sha256, author_id, sources, "
            "state, reviewed_by, reviewed_at, created_at FROM knowledge_revision WHERE revision_id = %s",
            (revision_id,),
        )
        return cur.fetchone()

    # -------------------------------------------------------------- artifacts

    def artifact_begin(self, p: Principal, ws: str, *, expected_sha256: str, expected_size: int, media_type: str,
                       name: str | None = None, artifact_id: str | None = None,
                       relations: Sequence[Mapping[str, Any]] = (), idempotency_key: str | None = None) -> dict[str, Any]:
        """Reserve a new artifact version (new artifact unless ``artifact_id`` is given)."""
        if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
            raise WorkspaceError(E.INVALID, "expected_sha256 must be 64 lowercase hex characters")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or not 0 <= expected_size <= self.max_artifact_bytes:
            raise WorkspaceError(E.INVALID, f"expected_size must be 0..{self.max_artifact_bytes}")
        media_type = (media_type or "").split(";", 1)[0].strip().lower()
        if not MEDIA_TYPE_RE.fullmatch(media_type):
            raise WorkspaceError(E.INVALID, "media_type must be a type/subtype")
        if artifact_id is None:
            _text(name, "name")
        request = {"expected_sha256": expected_sha256, "expected_size": expected_size, "media_type": media_type,
                   "name": name, "artifact_id": artifact_id, "relations": list(relations)}

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            now = self.now()
            aid = artifact_id
            if aid is None:
                aid = new_ulid()
                cur.execute("INSERT INTO artifact (artifact_id, workspace_id, name, created_by, created_at) "
                            "VALUES (%s,%s,%s,%s,%s)", (aid, ws, name, p.principal_id, now))
            else:
                cur.execute("SELECT 1 FROM artifact WHERE artifact_id = %s AND workspace_id = %s AND deleted_at IS NULL "
                            "FOR UPDATE", (aid, ws))
                if cur.fetchone() is None:
                    raise WorkspaceError(E.NOT_FOUND, "artifact not found")
            version_id = new_ulid()
            key = artifact_key(ws, aid, version_id)
            expires_at = now + self.reservation_ttl
            cur.execute(
                "INSERT INTO artifact_version (version_id, workspace_id, artifact_id, object_key, expected_sha256, "
                "expected_size, declared_media_type, processing_state, created_by, created_at, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'reserved',%s,%s,%s)",
                (version_id, ws, aid, key, expected_sha256, expected_size, media_type, p.principal_id, now, expires_at),
            )
            self._insert_relations(cur, ws, p.principal_id, "artifact_version", version_id, relations)
            self._emit(cur, ws, p.principal_id, "artifact.reserved", "artifact", aid, version_id=version_id,
                       payload={"expected_size": expected_size, "media_type": media_type})
            return {"artifact_id": aid, "version_id": version_id, "object_key": key,
                    "processing_state": "reserved", "expires_at": expires_at}

        return self._mutate(p, ws, "artifact.begin", CONTRIBUTE, request, idempotency_key, run)

    def artifact_upload(self, p: Principal, ws: str, version_id: str, data: bytes) -> dict[str, Any]:
        """Transfer step: write a reserved version's bytes through the store API (Phase 1).

        The version row stays locked while the bytes are written, so a
        concurrent finalize cannot start verifying half-written content, and
        an upload is refused once verification has begun.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise WorkspaceError(E.INVALID, "data must be bytes")
        if len(data) > self.max_artifact_bytes:
            raise WorkspaceError(E.INVALID, "artifact exceeds the size limit")
        with self.conn.transaction(), self.conn.cursor() as cur:
            self._authorize(cur, p, ws, CONTRIBUTE)
            row = self._version_for_update(cur, ws, version_id)
            if row["created_by"] != p.principal_id:
                raise WorkspaceError(E.FORBIDDEN, "only the reserving principal can upload this version")
            if row["processing_state"] != "reserved":
                raise WorkspaceError(E.STATE_CONFLICT, f"version is {row['processing_state']}")
            if row["expires_at"] <= self.now():
                raise WorkspaceError(E.RESERVATION_EXPIRED, "reservation expired")
            self.objects.put(row["object_key"], bytes(data))
        return {"version_id": version_id, "object_key": row["object_key"], "size": len(data)}

    def artifact_finalize(self, p: Principal, ws: str, version_id: str, *,
                          idempotency_key: str | None = None) -> dict[str, Any]:
        """Verify stored bytes (sha256, size, sniffed type) and mark ready or rejected. Idempotent.

        A version left in ``verifying`` (a crash between the two transactions)
        is re-verified by any later finalize; reconcile resets or expires one
        that nobody retries within ``verify_timeout``.
        """
        if idempotency_key is not None and not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise WorkspaceError(E.INVALID, "idempotency key must match ^[A-Za-z0-9._:-]{8,128}$")
        op = "artifact.finalize"
        digest = request_digest(op, {"version_id": version_id})
        expired = False
        with self.conn.transaction(), self.conn.cursor() as cur:
            self._authorize(cur, p, ws, CONTRIBUTE)
            if idempotency_key:
                replay = self._ledger_lookup(cur, ws, p, op, idempotency_key, digest)
                if replay is not None:
                    return replay
            row = self._version_for_update(cur, ws, version_id)
            state = row["processing_state"]
            if state in ("ready", "rejected"):
                if idempotency_key:
                    self._ledger_store(cur, ws, p, op, idempotency_key, digest, row["finalize_result"])
                return row["finalize_result"]
            if state == "expired":
                raise WorkspaceError(E.RESERVATION_EXPIRED, "reservation expired")
            if state == "reserved" and row["expires_at"] <= self.now():
                self._expire_version(cur, ws, p.principal_id, row)
                expired = True
            else:  # reserved, or a stranded verifying being retried
                cur.execute("UPDATE artifact_version SET processing_state = 'verifying', verifying_since = %s "
                            "WHERE version_id = %s", (self.now(), version_id))
        if expired:
            raise WorkspaceError(E.RESERVATION_EXPIRED, "reservation expired")

        data = self.objects.get(row["object_key"])
        if data is None:
            with self.conn.transaction(), self.conn.cursor() as cur:
                cur.execute("UPDATE artifact_version SET processing_state = 'reserved', verifying_since = NULL "
                            "WHERE version_id = %s AND processing_state = 'verifying'", (version_id,))
            raise WorkspaceError(E.OBJECT_MISSING, "no bytes stored for this version yet", object_key=row["object_key"])
        actual_sha, actual_size = _sha256(data), len(data)
        accepted_type, sniffed = resolve_media_type(row["declared_media_type"], data)
        reasons = []
        if actual_size != row["expected_size"]:
            reasons.append("size-mismatch")
        if actual_sha != row["expected_sha256"]:
            reasons.append("checksum-mismatch")
        if accepted_type is None:
            reasons.append("media-type-mismatch")
        extraction = extract(accepted_type, data) if not reasons and accepted_type else None

        try:
            return self._finalize_commit(p, ws, version_id, idempotency_key, op, digest, reasons, extraction,
                                         actual_sha, actual_size, accepted_type, sniffed)
        except WorkspaceError as exc:
            if exc.code == E.IDEMPOTENCY_CONFLICT:
                # The key went to another version first: release this one for a later finalize.
                with self.conn.transaction(), self.conn.cursor() as cur:
                    cur.execute("UPDATE artifact_version SET processing_state = 'reserved', verifying_since = NULL "
                                "WHERE version_id = %s AND processing_state = 'verifying'", (version_id,))
            raise

    def _finalize_commit(self, p: Principal, ws: str, version_id: str, idempotency_key: str | None, op: str,
                         digest: str, reasons: list[str], extraction: Any, actual_sha: str, actual_size: int,
                         accepted_type: str | None, sniffed: str) -> dict[str, Any]:
        with self.conn.transaction(), self.conn.cursor() as cur:
            self._authorize(cur, p, ws, CONTRIBUTE)
            if idempotency_key:
                # Re-take the key lock: a concurrent first use must conflict or replay, never double-store.
                replay = self._ledger_lookup(cur, ws, p, op, idempotency_key, digest)
                if replay is not None:
                    return replay
            row = self._version_for_update(cur, ws, version_id)
            if row["processing_state"] in ("ready", "rejected"):
                result = row["finalize_result"]
            elif row["processing_state"] != "verifying":
                raise WorkspaceError(E.STATE_CONFLICT, f"version is {row['processing_state']}")
            else:
                now = self.now()
                ready = not reasons
                extraction_state = extraction.state if extraction else "pending"
                if ready and extraction and extraction.state == "ready":
                    try:
                        with self.conn.transaction():  # savepoint: indexing never fails finalize
                            cur.execute(
                                "INSERT INTO extraction (extraction_id, workspace_id, version_id, extractor, "
                                "extractor_version, text_content, truncated, anchors, created_at) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                (new_ulid(), ws, version_id, EXTRACTOR, EXTRACTOR_VERSION, extraction.text,
                                 extraction.truncated, Jsonb(extraction.anchors), now),
                            )
                    except psycopg.Error:
                        extraction_state = "failed"
                result = normalize({
                    "artifact_id": row["artifact_id"], "version_id": version_id,
                    "processing_state": "ready" if ready else "rejected",
                    "review_state": "unreviewed", "extraction_state": extraction_state,
                    "extraction_truncated": bool(extraction and extraction.truncated and extraction_state == "ready"),
                    "sha256": actual_sha, "size": actual_size, "media_type": accepted_type or sniffed,
                    "content_disposition": "attachment", "reject_reasons": reasons,
                })
                cur.execute(
                    "UPDATE artifact_version SET processing_state = %s, sha256 = %s, size_bytes = %s, media_type = %s, "
                    "extraction_state = %s, reject_reason = %s, finalize_result = %s, finalized_at = %s, "
                    "verifying_since = NULL WHERE version_id = %s",
                    (result["processing_state"], actual_sha, actual_size, result["media_type"], extraction_state,
                     ",".join(reasons) or None, Jsonb(result), now, version_id),
                )
                if ready:
                    self._advance_pointer(cur, "latest_version_id", row["artifact_id"], version_id)
                self._emit(cur, ws, p.principal_id, "artifact.ready" if ready else "artifact.rejected", "artifact",
                           row["artifact_id"], version_id=version_id, payload=result)
            if idempotency_key:
                self._ledger_store(cur, ws, p, op, idempotency_key, digest, result)
            return result

    def _advance_pointer(self, cur: Any, column: str, artifact_id: str, version_id: str) -> None:
        """Move ``latest_version_id``/``approved_version_id`` forward only.

        Rule: versions are ordered by reservation (``created_at``, then the
        ULID ``version_id``). A pointer moves to a version only if it is not
        older than the version it currently names, so finalizing or approving
        an older version never regresses it.
        """
        if column not in ("latest_version_id", "approved_version_id"):
            raise ValueError(column)
        cur.execute("SELECT 1 FROM artifact WHERE artifact_id = %s FOR UPDATE", (artifact_id,))
        cur.execute(
            f"UPDATE artifact a SET {column} = n.version_id FROM artifact_version n "
            f"WHERE n.version_id = %s AND a.artifact_id = %s AND (a.{column} IS NULL OR NOT EXISTS ("
            f"  SELECT 1 FROM artifact_version c WHERE c.version_id = a.{column} "
            f"  AND (c.created_at, c.version_id) > (n.created_at, n.version_id)))",
            (version_id, artifact_id),
        )

    def _version_for_update(self, cur: Any, ws: str, version_id: str) -> dict[str, Any]:
        cur.execute(
            "SELECT v.* FROM artifact_version v JOIN artifact a ON a.artifact_id = v.artifact_id "
            "WHERE v.version_id = %s AND v.workspace_id = %s AND a.deleted_at IS NULL FOR UPDATE OF v",
            (version_id, ws),
        )
        row = cur.fetchone()
        if row is None:
            raise WorkspaceError(E.NOT_FOUND, "version not found")
        return row

    def _expire_version(self, cur: Any, ws: str, actor: str, row: Mapping[str, Any]) -> None:
        cur.execute("UPDATE artifact_version SET processing_state = 'expired', verifying_since = NULL "
                    "WHERE version_id = %s", (row["version_id"],))
        self._emit(cur, ws, actor, "artifact.expired", "artifact", row["artifact_id"], version_id=row["version_id"],
                   payload={"expires_at": row["expires_at"]})

    def artifact_review(self, p: Principal, ws: str, version_id: str, decision: str, *, note: str | None = None,
                        idempotency_key: str | None = None) -> dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise WorkspaceError(E.INVALID, "decision must be approve or reject")

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            row = self._version_for_update(cur, ws, version_id)
            if row["created_by"] == p.principal_id:
                raise WorkspaceError(E.FORBIDDEN, "an artifact version's author cannot review it")
            if row["processing_state"] != "ready" or row["review_state"] != "unreviewed":
                raise WorkspaceError(E.STATE_CONFLICT,
                                     f"version is {row['processing_state']}/{row['review_state']}; needs ready/unreviewed")
            state = "approved" if decision == "approve" else "rejected"
            cur.execute("UPDATE artifact_version SET review_state = %s, reviewed_by = %s, reviewed_at = %s "
                        "WHERE version_id = %s", (state, p.principal_id, self.now(), version_id))
            if state == "approved":
                self._advance_pointer(cur, "approved_version_id", row["artifact_id"], version_id)
            cur.execute("SELECT approved_version_id FROM artifact WHERE artifact_id = %s", (row["artifact_id"],))
            approved = cur.fetchone()["approved_version_id"]
            self._emit(cur, ws, p.principal_id, f"artifact.{state}", "artifact", row["artifact_id"],
                       version_id=version_id, visibility="published" if state == "approved" else "working",
                       payload={"note": note, "approved_version_id": approved})
            return {"artifact_id": row["artifact_id"], "version_id": version_id, "review_state": state,
                    "approved_version_id": approved}

        return self._mutate(p, ws, "artifact.review", REVIEW, {"version_id": version_id, "decision": decision,
                                                                "note": note}, idempotency_key, run)

    # ------------------------------------------------------------------ reads

    def read(self, p: Principal, ws: str, item_id: str, version: str = "latest-approved") -> dict[str, Any]:
        """Read an exact revision or ``latest-approved``; unavailable is always ``not-found``."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            access = self._authorize(cur, p, ws, READ)
            cur.execute("SELECT * FROM knowledge_item WHERE item_id = %s AND workspace_id = %s AND deleted_at IS NULL",
                        (item_id, ws))
            item = cur.fetchone()
            if item is None:
                raise WorkspaceError(E.NOT_FOUND, "item not available")
            revision_id = item["approved_revision_id"] if version == "latest-approved" else version
            rev = None
            if revision_id:
                rev = self._revision_row(cur, revision_id)
            if rev is None or rev["item_id"] != item_id:
                raise WorkspaceError(E.NOT_FOUND, "revision not available")
            if rev["state"] not in ("approved", "superseded") and not access.can(CONTRIBUTE):
                raise WorkspaceError(E.NOT_FOUND, "revision not available")
            # Relations are filtered by what this caller may see: a reader never
            # learns the ids of drafts, unreviewed versions or deleted targets.
            cur.execute(
                "SELECT rel.relation_id, rel.kind, rel.target_kind, rel.target_id, rel.target_ref FROM relation rel "
                "LEFT JOIN knowledge_revision tr ON rel.target_kind = 'revision' AND tr.revision_id = rel.target_id "
                "LEFT JOIN knowledge_item ti ON ti.item_id = tr.item_id "
                "LEFT JOIN artifact_version tv ON rel.target_kind = 'artifact_version' AND tv.version_id = rel.target_id "
                "LEFT JOIN artifact ta ON ta.artifact_id = tv.artifact_id "
                "WHERE rel.workspace_id = %(ws)s AND rel.source_kind = 'revision' AND rel.source_id = %(src)s AND ("
                " rel.target_kind = 'repository_document'"
                " OR (rel.target_kind = 'revision' AND ti.deleted_at IS NULL"
                "     AND (%(working)s OR tr.state IN ('approved','superseded')))"
                " OR (rel.target_kind = 'artifact_version' AND ta.deleted_at IS NULL AND tv.processing_state = 'ready'"
                "     AND (%(working)s OR tv.review_state = 'approved'))"
                ") ORDER BY rel.relation_id",
                {"ws": ws, "src": revision_id, "working": access.can(CONTRIBUTE)},
            )
            relations = cur.fetchall()
            watermark = self._visible_watermark(cur, ws, access)
        return normalize({
            **rev, "workspace_id": ws, "publication_state": rev["state"],
            "approved_revision_id": item["approved_revision_id"], "sources_supplied": True,
            "relations": relations, "watermark": watermark,
        })

    def download(self, p: Principal, ws: str, artifact_id: str, version: str = "latest-approved", *,
                 max_bytes: int | None = None) -> dict[str, Any]:
        """Authenticated download; bytes are re-verified against the recorded checksum.

        Callers serving the bytes over HTTP must use ``content_disposition``
        (always ``attachment``) and ``media_type``; stored content is never
        rendered inline.
        """
        with self.conn.transaction(), self.conn.cursor() as cur:
            access = self._authorize(cur, p, ws, READ)
            cur.execute("SELECT * FROM artifact WHERE artifact_id = %s AND workspace_id = %s AND deleted_at IS NULL",
                        (artifact_id, ws))
            art = cur.fetchone()
            if art is None:
                raise WorkspaceError(E.NOT_FOUND, "artifact not available")
            version_id = art["approved_version_id"] if version == "latest-approved" else version
            cur.execute("SELECT * FROM artifact_version WHERE version_id = %s AND artifact_id = %s AND workspace_id = %s",
                        (version_id, artifact_id, ws))
            row = cur.fetchone()
        if row is None or row["processing_state"] != "ready":
            raise WorkspaceError(E.NOT_FOUND, "version not available")
        if row["review_state"] != "approved" and not access.can(CONTRIBUTE):
            raise WorkspaceError(E.NOT_FOUND, "version not available")
        if max_bytes is not None and row["size_bytes"] > max_bytes:
            # Checked against the recorded size before any bytes are fetched.
            raise WorkspaceError(E.TOO_LARGE, "artifact exceeds this download path's size limit",
                                 size=row["size_bytes"], limit=max_bytes)
        data = self.objects.get(row["object_key"])
        if data is None or _sha256(data) != row["sha256"]:
            raise WorkspaceError(E.OBJECT_MISSING, "stored object is missing or corrupt", version_id=version_id)
        return {
            "artifact_id": artifact_id, "version_id": version_id, "name": art["name"], "media_type": row["media_type"],
            "content_disposition": "attachment",
            "size": row["size_bytes"], "sha256": row["sha256"], "review_state": row["review_state"],
            "processing_state": row["processing_state"], "extraction_state": row["extraction_state"], "data": data,
        }

    def search(self, p: Principal, ws: str, query: str, *, scope: str = "approved", limit: int = 20,
               offset: int = 0) -> dict[str, Any]:
        """FTS over titles, Markdown bodies, artifact names and extracted text.

        Publication and access filters are applied in the same query, before
        ranking, counting and snippets; snippets are computed only for the
        returned page.
        """
        if scope not in SCOPES:
            raise WorkspaceError(E.INVALID, f"scope must be one of {SCOPES}")
        if not isinstance(query, str) or not query.strip():
            raise WorkspaceError(E.INVALID, "query must be non-empty")
        limit = max(1, min(int(limit), 100))
        with self.conn.transaction(), self.conn.cursor() as cur:
            access = self._authorize(cur, p, ws, READ)
            working = access.can(CONTRIBUTE)
            # Contract §3.2: working and history scopes require contributor or higher.
            if scope != "approved" and not working:
                raise WorkspaceError(E.FORBIDDEN, f"{scope} scope needs knowledge:contribute")
            if scope == "approved":
                rev_filter = "r.revision_id = i.approved_revision_id"
                art_filter = "v.version_id = a.approved_version_id AND v.review_state = 'approved'"
            elif scope == "working":
                rev_filter = "(r.revision_id = i.approved_revision_id OR r.state IN ('draft','proposed'))"
                art_filter = "v.review_state <> 'rejected'"
            else:
                rev_filter = "TRUE"
                art_filter = "TRUE"
            cur.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %(q)s) AS tsq),
                rev_hits AS (
                  SELECT 'item'::text AS kind, i.item_id AS entity_id, r.revision_id AS version_id, r.title,
                         r.state AS publication_state, NULL::text AS extraction_state,
                         ts_rank(r.search_tsv, q.tsq) AS rank
                  FROM q, knowledge_revision r
                  JOIN knowledge_item i ON i.item_id = r.item_id AND i.workspace_id = r.workspace_id
                  WHERE r.workspace_id = %(ws)s AND i.deleted_at IS NULL AND r.search_tsv @@ q.tsq AND {rev_filter}
                ),
                art_matches AS (
                  SELECT e.version_id, ts_rank(e.search_tsv, q.tsq) AS rank
                  FROM q, extraction e
                  WHERE e.workspace_id = %(ws)s AND e.search_tsv @@ q.tsq
                    AND e.extractor_version::int = (SELECT max(e2.extractor_version::int) FROM extraction e2
                                                    WHERE e2.version_id = e.version_id AND e2.extractor = e.extractor)
                  UNION ALL
                  SELECT v.version_id, ts_rank(to_tsvector('english'::regconfig, left(a.name, 1000)), q.tsq)
                  FROM q, artifact a
                  JOIN artifact_version v ON v.artifact_id = a.artifact_id AND v.workspace_id = a.workspace_id
                  WHERE a.workspace_id = %(ws)s AND to_tsvector('english'::regconfig, left(a.name, 1000)) @@ q.tsq
                ),
                art_hits AS (
                  SELECT 'artifact'::text, a.artifact_id, v.version_id, a.name, v.review_state, v.extraction_state,
                         max(m.rank)
                  FROM art_matches m
                  JOIN artifact_version v ON v.version_id = m.version_id
                  JOIN artifact a ON a.artifact_id = v.artifact_id AND a.workspace_id = v.workspace_id
                  WHERE v.workspace_id = %(ws)s AND a.deleted_at IS NULL AND v.processing_state = 'ready'
                    AND {art_filter}
                  GROUP BY a.artifact_id, v.version_id, a.name, v.review_state, v.extraction_state
                ),
                page AS (
                  SELECT h.*, count(*) OVER () AS total
                  FROM (SELECT * FROM rev_hits UNION ALL SELECT * FROM art_hits) h
                  ORDER BY h.rank DESC, h.version_id LIMIT %(limit)s OFFSET %(offset)s
                )
                SELECT page.*, ts_headline('english', left(
                         CASE WHEN page.kind = 'item'
                              THEN (SELECT body_md FROM knowledge_revision WHERE revision_id = page.version_id)
                              ELSE coalesce((SELECT text_content FROM extraction x WHERE x.version_id = page.version_id
                                             ORDER BY x.extractor_version::int DESC LIMIT 1), page.title)
                         END, 200000), q.tsq, 'MaxFragments=1,MaxWords=24,MinWords=6') AS snippet
                FROM page, q ORDER BY page.rank DESC, page.version_id
                """,
                {"q": query, "ws": ws, "limit": limit, "offset": max(0, int(offset))},
            )
            rows = cur.fetchall()
            total = rows[0]["total"] if rows else 0
            watermark = self._visible_watermark(cur, ws, access)
        results = [
            {"kind": r["kind"], "id": r["entity_id"], "version_id": r["version_id"], "title": r["title"],
             "publication_state": r["publication_state"], "extraction_state": r["extraction_state"],
             "rank": float(r["rank"]), "snippet": r["snippet"]}
            for r in rows
        ]
        return {"workspace_id": ws, "scope": scope, "results": results, "total": total,
                "index_watermark": watermark, "index_complete": True}

    # ------------------------------------------------------------ change feed

    def changes_since(self, p: Principal, ws: str, cursor: str | None = None, *, limit: int = 100) -> dict[str, Any]:
        """Visible changes and tombstones after ``cursor``. Cursors never grant access.

        The watermark and cursor only advance over events the caller may see,
        so hidden activity is not observable through them.
        """
        limit = max(1, min(int(limit), 1000))
        after = 0 if cursor is None else _decode_cursor(cursor, ws)
        with self.conn.transaction(), self.conn.cursor() as cur:
            access = self._authorize(cur, p, ws, READ)
            watermark = self._visible_watermark(cur, ws, access)
            if after > watermark:
                raise WorkspaceError(E.INVALID_CURSOR, "cursor is ahead of what this caller can see; resynchronize")
            cur.execute(
                "SELECT seq, kind, entity_kind, entity_id, version_id, tombstone, payload, created_at FROM workspace_event "
                "WHERE workspace_id = %s AND seq > %s AND seq <= %s AND visibility = ANY(%s) ORDER BY seq LIMIT %s",
                (ws, after, watermark, self._visible(access), limit + 1),
            )
            rows = cur.fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_seq = rows[-1]["seq"] if has_more else watermark
        return normalize({"workspace_id": ws, "changes": rows, "cursor": _encode_cursor(ws, next_seq),
                          "has_more": has_more, "watermark": watermark})

    # ------------------------------------------------------- delete/reconcile

    def delete(self, p: Principal, ws: str, kind: str, entity_id: str, *,
               idempotency_key: str | None = None) -> dict[str, Any]:
        """Tombstone an item or artifact: gone from every read, search and count at once.

        The tombstone event is published only if the entity was ever
        published; deleting a never-approved draft stays in the working feed.
        """
        tables = {"item": ("knowledge_item", "item_id", "approved_revision_id"),
                  "artifact": ("artifact", "artifact_id", "approved_version_id")}
        if kind not in tables:
            raise WorkspaceError(E.INVALID, "kind must be item or artifact")
        table, column, approved = tables[kind]

        def run(cur: Any, _: Access | None) -> dict[str, Any]:
            now = self.now()
            cur.execute(
                f"UPDATE {table} SET deleted_at = %s, deleted_by = %s "
                f"WHERE {column} = %s AND workspace_id = %s AND deleted_at IS NULL RETURNING {approved} AS approved",
                (now, p.principal_id, entity_id, ws),
            )
            row = cur.fetchone()
            if row is None:
                raise WorkspaceError(E.NOT_FOUND, f"{kind} not found")
            self._emit(cur, ws, p.principal_id, f"{kind}.deleted", kind, entity_id,
                       visibility="published" if row["approved"] else "working",
                       tombstone=True, payload={"deleted_at": now})
            return {"kind": kind, "id": entity_id, "deleted_at": now}

        return self._mutate(p, ws, "delete", ADMIN, {"kind": kind, "id": entity_id}, idempotency_key, run)

    def reconcile(self, p: Principal, ws: str, *, grace: timedelta = timedelta(hours=1),
                  delete_orphans: bool = False) -> dict[str, Any]:
        """Expire stale reservations, recover stranded verifications, report missing objects and orphans.

        A ``verifying`` version older than ``verify_timeout`` (a finalize that
        crashed between its transactions) is reset to ``reserved`` so it can
        be finalized again, or expired if its reservation has lapsed.
        An orphan (object with no live row, older than ``grace``) is deleted
        only when ``delete_orphans`` is set *and* an earlier pass recorded it
        at least ``grace`` ago.
        """
        now = self.now()
        with self.conn.transaction(), self.conn.cursor() as cur:
            self._authorize(cur, p, ws, ADMIN)
            cur.execute("SELECT * FROM artifact_version WHERE workspace_id = %s AND processing_state = 'verifying' "
                        "AND verifying_since <= %s ORDER BY version_id FOR UPDATE", (ws, now - self.verify_timeout))
            stranded = cur.fetchall()
            reset = []
            for row in stranded:
                if row["expires_at"] > now:
                    cur.execute("UPDATE artifact_version SET processing_state = 'reserved', verifying_since = NULL "
                                "WHERE version_id = %s", (row["version_id"],))
                    self._emit(cur, ws, p.principal_id, "artifact.verification-reset", "artifact", row["artifact_id"],
                               version_id=row["version_id"], payload={"verifying_since": row["verifying_since"]})
                    reset.append(row["version_id"])
            cur.execute("SELECT * FROM artifact_version WHERE workspace_id = %s AND processing_state IN "
                        "('reserved','verifying') AND expires_at <= %s AND (processing_state = 'reserved' "
                        "OR verifying_since <= %s) ORDER BY version_id FOR UPDATE",
                        (ws, now, now - self.verify_timeout))
            expired = cur.fetchall()
            for row in expired:
                self._expire_version(cur, ws, p.principal_id, row)
            cur.execute("SELECT version_id, object_key, processing_state FROM artifact_version WHERE workspace_id = %s",
                        (ws,))
            rows = {r["object_key"]: r for r in cur.fetchall()}
            cur.execute("SELECT object_key, first_seen_at FROM reconcile_orphan WHERE workspace_id = %s", (ws,))
            seen = {r["object_key"]: r["first_seen_at"] for r in cur.fetchall()}
        objects = {o.key: o for o in self.objects.list(workspace_prefix(ws))}
        missing = [{"version_id": r["version_id"], "object_key": k} for k, r in sorted(rows.items())
                   if r["processing_state"] == "ready" and k not in objects]
        orphans = []
        for key, info in sorted(objects.items()):
            row = rows.get(key)
            if row is not None and row["processing_state"] in ("reserved", "verifying", "ready"):
                continue
            if info.modified_at > now - grace:
                continue
            reason = "no-row" if row is None else f"{row['processing_state']}-version"
            # A second sighting only counts once ``grace`` has passed since the
            # first: two back-to-back passes never delete anything.
            confirmed = key in seen and now - seen[key] >= grace
            status = "deleted" if (delete_orphans and confirmed) else "pending-second-pass"
            orphans.append({"object_key": key, "size": info.size, "reason": reason, "status": status})
        with self.conn.transaction(), self.conn.cursor() as cur:
            self._authorize(cur, p, ws, ADMIN)
            current = {o["object_key"] for o in orphans}
            for key in set(seen) - current:
                cur.execute("DELETE FROM reconcile_orphan WHERE workspace_id = %s AND object_key = %s", (ws, key))
            for orphan in orphans:
                if orphan["status"] == "deleted":
                    self.objects.delete(orphan["object_key"])
                    cur.execute("DELETE FROM reconcile_orphan WHERE workspace_id = %s AND object_key = %s",
                                (ws, orphan["object_key"]))
                elif orphan["object_key"] not in seen:
                    cur.execute("INSERT INTO reconcile_orphan (workspace_id, object_key, first_seen_at) VALUES (%s,%s,%s)",
                                (ws, orphan["object_key"], now))
            report = normalize({
                "workspace_id": ws, "expired_versions": [r["version_id"] for r in expired],
                "reset_verifications": reset, "missing_objects": missing, "orphans": orphans,
            })
            self._emit(cur, ws, p.principal_id, "workspace.reconciled", "workspace", ws, visibility="admin",
                       payload={"expired": len(expired), "reset": len(reset), "missing": len(missing),
                                "orphans": len(orphans), "deleted": sum(o["status"] == "deleted" for o in orphans)})
        return report

    # ------------------------------------------------------- export/import

    def export(self, p: Principal, ws: str, dest: str) -> dict[str, Any]:
        from .exchange import export_workspace

        return export_workspace(self, p, ws, dest)

    def import_bundle(self, p: Principal, src: str, *, name: str | None = None, tenant_id: str = "default",
                      idempotency_key: str | None = None) -> dict[str, Any]:
        from .exchange import import_workspace

        return import_workspace(self, p, src, name=name, tenant_id=tenant_id, idempotency_key=idempotency_key)


# ------------------------------------------------------------------ helpers


def _text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise WorkspaceError(E.INVALID, f"{field_name} must be a non-empty string of at most 1000 characters")


MAX_BODY_CHARS = 1_000_000


def _body(value: Any) -> None:
    if not isinstance(value, str) or "\x00" in value:
        raise WorkspaceError(E.INVALID, "body_md must be text")
    if len(value) > MAX_BODY_CHARS:
        raise WorkspaceError(E.INVALID, f"body_md exceeds {MAX_BODY_CHARS} characters; store it as an artifact")


def _encode_cursor(ws: str, seq: int) -> str:
    raw = json.dumps({"v": CURSOR_FORMAT, "w": ws, "s": seq}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, ws: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if value.get("v") != CURSOR_FORMAT or not isinstance(value.get("s"), int) or value["s"] < 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError, binascii.Error):
        raise WorkspaceError(E.INVALID_CURSOR, "malformed cursor") from None
    if value.get("w") != ws:
        raise WorkspaceError(E.INVALID_CURSOR, "cursor belongs to another workspace")
    return value["s"]


__all__ = [
    "ADMIN", "CONTRIBUTE", "READ", "REVIEW", "ROLES", "SCOPES", "Access", "Principal", "WorkspaceStore",
]
