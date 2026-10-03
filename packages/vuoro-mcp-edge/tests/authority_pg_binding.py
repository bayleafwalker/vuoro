"""Bindings to the immutable owner on explicitly disposable PostgreSQL.

Only fixture setup and the ledger lookup projection use SQL. Begin/complete
use sprintctl's own transaction-bound ledger. Effect transitions use the
served WorkApplication; no lifecycle is reimplemented in this binding.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from vuoro_mcp_edge.idempotency import StoredResult


def connect(url):
    from sprintctl import pg
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith("/lease_conformance"):
        raise ValueError("authority conformance requires a disposable loopback lease_conformance database")
    store = pg.get_connection(url)
    store.repo_id = "authority-conformance-" + uuid.uuid4().hex
    pg.init_db(store)
    return store


class PgLedgerBinding:
    def __init__(self, url):
        from sprintctl.pg import PgIdempotencyLedger
        self.owner_store = connect(url)
        self.owner = PgIdempotencyLedger(self.owner_store)

    async def lookup(self, workspace_id, principal_id, tool, key):
        # Test-only read projection of the owner's committed result. No local
        # cache: reconnect/restart sees exactly the durable row.
        with self.owner_store.conn.transaction():
            with self.owner_store.conn.cursor() as cur:
                cur.execute("SELECT request_digest, result FROM work_idempotency_ledger "
                            "WHERE repo_id=%s AND workspace_id=%s AND principal_id=%s "
                            "AND tool=%s AND idempotency_key=%s",
                            (self.owner_store.repo_id, workspace_id, principal_id, tool, key))
                row = cur.fetchone()
        return StoredResult(row["request_digest"], row["result"]) if row and row["result"] is not None else None

    async def store(self, workspace_id, principal_id, tool, key, stored):
        # Preserve the legacy first-row-returning adapter shape; the owner
        # begin itself refuses a different digest, before a second effect.
        found = await self.lookup(workspace_id, principal_id, tool, key)
        if found is not None:
            return found
        from sprintctl.pg import IdempotencyConflict
        try:
            with self.owner_store.conn.transaction():
                entry = self.owner.begin(workspace_id, principal_id, tool, key, stored.digest)
                completed = entry if entry.replayed else self.owner.complete(entry, dict(stored.result))
            return StoredResult(completed.request_digest, completed.result)
        except IdempotencyConflict:
            # A different-digest concurrent winner committed first. Match the
            # legacy store adapter; the shared replay oracle supplies refusal.
            winner = await self.lookup(workspace_id, principal_id, tool, key)
            if winner is None:
                raise
            return winner

    def close(self):
        self.owner_store.conn.close()


class PgEffectsBinding:
    def __init__(self, url):
        from types import SimpleNamespace
        from sprintctl import pg
        from sprintctl.application import WorkApplication
        self.owner_store = connect(url)
        self.app = WorkApplication.postgres(self.owner_store)
        self._namespace = SimpleNamespace
        sprint = pg.create_sprint(self.owner_store, "Effect authority contract", "Disposable",
                                  "2026-01-01", "2026-12-31", "active")
        track = pg.get_or_create_track(self.owner_store, sprint, "contract")
        self.item = pg.create_work_item(self.owner_store, sprint, track, "Effect contract")
        self.run = self.app.invoke("work.run.register-v1", {"harness_id": "test", "harness_build": "test",
            "model_id": "scripted", "recipe_id": "authority-conformance",
            "observed_profile": {"instruction_digest": "sha256:" + "a" * 64, "skill_digests": []},
            "idempotency_key": uuid.uuid4().hex}, self.context("proposer", {"work:evidence", "work:read"}))["run"]["run_id"]

    def context(self, principal, authorities):
        ns = self._namespace
        return ns(identity=ns(actor=principal, principal_id=principal, workspace_id="workspace",
                  environment="authority-conformance", client_id=None, grant_id=None, authorities=frozenset(authorities)),
                  request_id=uuid.uuid4().hex, basis_revision=None, catalog_revision="test", idempotency_key=None,
                  idempotency_requirement="not-allowed")

    def propose(self, *, title="Change", key=None):
        return self.app.invoke("work.effect.propose-v1", {"item_id": self.item, "run_id": self.run,
            "repository": "repo", "base_commit": "a" * 40, "title": title, "rationale": "Why",
            "unified_diff": "diff", "idempotency_key": key or uuid.uuid4().hex},
            self.context("proposer", {"work.effect.propose"}))["intent"]

    def get(self, intent_id):
        return self.app.invoke("work.effect.get-v1", {"intent_id": intent_id},
                               self.context("operator", {"work.effect.get"}))["intent"]

    def transition(self, operation, row, *, authorities=None):
        args = {k: row[k] for k in ("intent_id", "revision", "canonical_intent_digest")}
        if operation == "reject":
            args["reason"] = "Operator declined"
        if operation == "mark-applied":
            args.update(commit_sha="b" * 40, pr_url="https://forge.example/repo/pulls/1")
        return self.app.invoke("work.effect." + operation + "-v1", args,
            self.context("operator", {"work.effect." + operation} if authorities is None else authorities))["intent"]

    def try_edit(self, row):
        # Explicit storage-fault injection on a disposable database: proves
        # the owner's immutable-content trigger, not a reimplementation.
        from psycopg.errors import CheckViolation
        from vuoro_mcp_edge.toolsets import ToolFailure
        try:
            with self.owner_store.conn.transaction():
                with self.owner_store.conn.cursor() as cur:
                    cur.execute("UPDATE work_effect_intent SET title=%s WHERE repo_id=%s AND intent_id=%s",
                                ("changed", self.owner_store.repo_id, row["intent_id"]))
        except CheckViolation as error:
            if "immutable" not in str(error):
                raise
            raise ToolFailure("effect-immutable", "a changed intent requires a new proposal") from None

    def item_status(self):
        from sprintctl import pg
        return pg.get_work_item(self.owner_store, self.item)["status"]

    def close(self):
        self.owner_store.conn.close()
