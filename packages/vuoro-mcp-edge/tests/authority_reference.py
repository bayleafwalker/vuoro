"""Trusted-side contract reference over the actual in-memory intent store.

Not an executor or deployed authority. Capability/state comparisons model the
contract; immutable proposal storage is provided by InMemoryIntentStore.
"""
import asyncio
from dataclasses import replace

from vuoro_mcp_edge.effect_tools import EffectIntent, InMemoryIntentStore
from vuoro_mcp_edge.runs import RunBinding
from vuoro_mcp_edge.idempotency import StoredResult, request_digest, replay_or_conflict
from vuoro_mcp_edge.toolsets import ToolFailure


class ReferenceEffects:
    def __init__(self):
        self.store = InMemoryIntentStore()
        self.counter = 0
        self.acceptances = {}
        self.applications = {}
        self.work_item = {"status": "pending"}

    def propose(self, *, title="Change", key=None):
        self.counter += 1
        intent = EffectIntent("effect_" + f"{self.counter:026d}", "run_" + "0" * 26,
            RunBinding("proposer", "workspace", "repo"), "repo", "a" * 40,
            title, "Why", "diff", item_id=1)
        digest = request_digest("propose_effect", {"item_id": intent.item_id, "run_id": intent.run_id,
            "repository": intent.repository, "base_commit": intent.base_commit, "title": intent.title,
            "rationale": intent.rationale, "unified_diff": intent.unified_diff})
        stored = StoredResult(digest, {"intent_id": intent.intent_id, "state": "proposed"})
        winner = asyncio.run(self.store.create("workspace", "proposer", "propose_effect",
                         key or f"proposal-{self.counter:04d}", stored, intent))
        result = replay_or_conflict(winner, digest)
        return self.get(result["intent_id"])

    def get(self, intent_id):
        intent = self.store._intents[intent_id]
        return {"intent_id": intent.intent_id, "revision": intent.revision,
                "canonical_intent_digest": intent.canonical_intent_digest,
                "title": intent.title, "state": intent.state,
                "acceptance": self.acceptances.get(intent_id),
                "application": self.applications.get(intent_id)}

    def transition(self, operation, row, *, authorities=None):
        required = "work.effect." + operation
        authorities = {required} if authorities is None else authorities
        if required not in authorities:
            raise ToolFailure("authority-required", "the distinct effect capability is required")
        current = self.get(row["intent_id"])
        if current["revision"] != row["revision"]:
            raise ToolFailure("effect-revision-mismatch", "revision differs")
        if current["canonical_intent_digest"] != row["canonical_intent_digest"]:
            raise ToolFailure("effect-digest-mismatch", "digest differs")
        expected = "accepted" if operation == "mark-applied" else "proposed"
        if current["state"] != expected:
            raise ToolFailure("effect-invalid-transition", "state differs")
        state = {"accept": "accepted", "reject": "rejected", "mark-applied": "applied"}[operation]
        if operation == "accept":
            self.acceptances[row["intent_id"]] = {"intent_id": current["intent_id"],
                "intent_revision": current["revision"], "canonical_intent_digest": current["canonical_intent_digest"],
                "acceptor_principal": "operator"}
        if operation == "mark-applied":
            self.applications[row["intent_id"]] = {"commit_sha": "b" * 40, "pr_url": "https://forge.example/repo/pulls/1"}
        self.store.set_state(row["intent_id"], state,
                             {"kind": "operator", "subject": "operator"} if operation != "reject" else None)
        return self.get(row["intent_id"])

    def try_edit(self, row):
        self.store.seed(replace(self.store._intents[row["intent_id"]], title="changed", canonical_intent_digest=""))

    def item_status(self):
        # The effect store has no work settlement path.
        return self.work_item["status"]

    def close(self):
        pass
