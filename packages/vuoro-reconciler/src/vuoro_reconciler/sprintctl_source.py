"""Trusted-side served intent adapter: durable discovery, no session ID cache.

The caller supplies an authenticated invoke transport bound to the repository
and workspace. Credentials never cross to the public edge. Accepted discovery
requires work.effect.list-accepted-v1 and its separate capability; an older
owner must refuse rather than masquerade as an empty work list.
"""
from .intents import EffectIntent, OperatorAcceptor, PolicyAcceptor


class SprintctlIntentSource:
    def __init__(self, invoke, *, workspace_id: str, principal_id: str):
        self.invoke = invoke
        self.workspace_id = workspace_id
        self.principal_id = principal_id

    def _intent(self, row):
        acceptance = row["acceptance"]
        if acceptance and acceptance["acceptor_policy_version"] is not None:
            raise ValueError("owner policy acceptance is not supported by this adapter")
        return EffectIntent(intent_id=row["intent_id"], run_id=row["run_id"], item_id=row["item_id"],
            revision=row["revision"], canonical_intent_digest=row["canonical_intent_digest"],
            repository=row["repository"], base_commit=row["base_commit"], title=row["title"],
            rationale=row["rationale"], unified_diff=row["unified_diff"], workspace_id=self.workspace_id,
            proposer_principal=row["proposer_principal"], acceptance=acceptance,
            acceptor=OperatorAcceptor(acceptance["acceptor_principal"]) if acceptance else None)

    async def poll_proposed(self):
        return [self._intent(row) for row in (await self.invoke("work.effect.list-proposed-v1", {}))["intents"]]

    async def poll_accepted(self):
        rows = (await self.invoke("work.effect.list-accepted-v1", {}))["intents"]
        if any(row["state"] != "accepted" or row["acceptance"] is None for row in rows):
            raise ValueError("accepted discovery returned a nonaccepted or unbound intent")
        return [self._intent(row) for row in rows]

    async def accept(self, intent_id, acceptor, *, revision, canonical_intent_digest):
        if isinstance(acceptor, PolicyAcceptor) or acceptor.subject != self.principal_id:
            raise ValueError("owner policy acceptance is not available")
        row = (await self.invoke("work.effect.accept-v1", {"intent_id": intent_id,
            "revision": revision, "canonical_intent_digest": canonical_intent_digest}))["intent"]
        if row["acceptance"]["acceptor_principal"] != acceptor.subject:
            raise ValueError("authenticated acceptor differs from operator attribution")

    async def reject(self, intent_id, acceptor, reason, *, revision, canonical_intent_digest):
        if isinstance(acceptor, PolicyAcceptor) or acceptor.subject != self.principal_id:
            raise ValueError("operator attribution must match the authenticated transport principal")
        await self.invoke("work.effect.reject-v1", {"intent_id": intent_id,
            "revision": revision, "canonical_intent_digest": canonical_intent_digest, "reason": reason})

    async def report_applied(self, intent_id, *, commit_sha, pr_url, acceptor, revision, canonical_intent_digest):
        await self.invoke("work.effect.mark-applied-v1", {"intent_id": intent_id,
            "revision": revision, "canonical_intent_digest": canonical_intent_digest,
            "commit_sha": commit_sha, "pr_url": pr_url})

    async def report_failed(self, intent_id, *, reason):
        # Accepted intents are immutable; the owner has no failed transition.
        # Keep it accepted and propagate so the caller records failure evidence.
        raise RuntimeError("owner has no failed transition; accepted intent remains retryable")
