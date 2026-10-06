"""Trusted-side served intent adapter: durable discovery, no session ID cache.

The caller supplies an authenticated invoke transport bound to the repository
and workspace. Credentials never cross to the public edge. Accepted discovery
requires work.effect.list-accepted-v1 and its separate capability; an older
owner must refuse rather than masquerade as an empty work list.
"""
from .intents import EffectIntent, OperatorAcceptor, PolicyAcceptor, canonical_digest
from .verification import PreflightRefused, validate_verification


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
            release_digest=row.get("release_digest"),
            acceptor=OperatorAcceptor(acceptance["acceptor_principal"]) if acceptance else None)

    async def poll_proposed(self):
        return [self._intent(row) for row in (await self.invoke("work.effect.list-proposed-v1", {}))["intents"]]

    async def poll_accepted(self):
        rows = (await self.invoke("work.effect.list-accepted-v1", {}))["intents"]
        if any(row["state"] != "accepted" or row["acceptance"] is None for row in rows):
            raise ValueError("accepted discovery returned a nonaccepted or unbound intent")
        return [self._intent(row) for row in rows]

    async def accept(self, intent_id, acceptor, *, revision, canonical_intent_digest, verification_ref=None):
        if isinstance(acceptor, PolicyAcceptor) or acceptor.subject != self.principal_id:
            raise ValueError("owner policy acceptance is not available")
        arguments = {"intent_id": intent_id, "revision": revision,
                     "canonical_intent_digest": canonical_intent_digest}
        if verification_ref is not None:
            arguments["verification_ref"] = dict(verification_ref)
        row = (await self.invoke("work.effect.accept-v1", arguments))["intent"]
        if row["acceptance"]["acceptor_principal"] != acceptor.subject:
            raise ValueError("authenticated acceptor differs from operator attribution")

    async def reject(self, intent_id, acceptor, reason, *, revision, canonical_intent_digest):
        if isinstance(acceptor, PolicyAcceptor) or acceptor.subject != self.principal_id:
            raise ValueError("operator attribution must match the authenticated transport principal")
        await self.invoke("work.effect.reject-v1", {"intent_id": intent_id,
            "revision": revision, "canonical_intent_digest": canonical_intent_digest, "reason": reason})

    async def preflight(self, intent):
        """Read the actual approval and current Release before external effects.

        This is a fresh observation, not a lock across Git or a publication
        grant. The owner still rechecks its Release when recording application.
        """
        row = (await self.invoke("work.effect.get-v1", {"intent_id": intent.intent_id}))["intent"]
        if (row["state"] not in {"accepted", "applied"}
                or row["revision"] != intent.revision
                or row["canonical_intent_digest"] != intent.canonical_intent_digest
                or canonical_digest(self._intent(row)) != intent.canonical_intent_digest
                or row.get("release_digest") != intent.release_digest
                or row["acceptance"] != intent.acceptance):
            raise PreflightRefused("owner-acceptance-changed")
        try:
            release = (await self.invoke("work.read.release", {"item_id": intent.item_id}))["release"]
        except Exception as error:
            if getattr(error, "code", None) == "release-not-found":
                if intent.release_digest is None:
                    validate_verification(intent, required=False)
                    return
                raise PreflightRefused("effect-release-mismatch") from None
            raise
        if not isinstance(release, dict):
            raise PreflightRefused("release-unavailable")
        required = release["acceptance_contract"].get("effect_verification_required", False)
        if type(required) is not bool:
            raise PreflightRefused("invalid-verification-requirement")
        if intent.release_digest is not None or required:
            if release["release_digest"] != intent.release_digest or release["work_item_id"] != intent.item_id:
                raise PreflightRefused("effect-release-mismatch")
            item = (await self.invoke("work.read.item", {"item_id": intent.item_id}))["item"]
            if (item["id"] != intent.item_id
                    or release["item_revision"].rsplit("@revise:", 1)[0] != item["edit_revision"]):
                raise PreflightRefused("effect-release-mismatch")
        validate_verification(intent, required=required)

    async def report_applied(self, intent_id, *, commit_sha, pr_url, acceptor, revision, canonical_intent_digest):
        await self.invoke("work.effect.mark-applied-v1", {"intent_id": intent_id,
            "revision": revision, "canonical_intent_digest": canonical_intent_digest,
            "commit_sha": commit_sha, "pr_url": pr_url})

    async def report_failed(self, intent_id, *, reason):
        # Accepted intents are immutable; the owner has no failed transition.
        # Keep it accepted and propagate so the caller records failure evidence.
        raise RuntimeError("owner has no failed transition; accepted intent remains retryable")

    async def preflight_proposed(self, intent):
        """Observe the exact proposed candidate and current bound work basis."""
        row = (await self.invoke("work.effect.get-v1", {"intent_id": intent.intent_id}))["intent"]
        if (row["state"] != "proposed" or self._intent(row) != intent
                or canonical_digest(intent) != intent.canonical_intent_digest
                or intent.acceptor is not None or intent.acceptance is not None
                or intent.release_digest is None
                or intent.proposer_principal == self.principal_id):
            raise PreflightRefused("invalid-proposed-intent")
        release = (await self.invoke("work.read.release", {"item_id": intent.item_id}))["release"]
        if type(release["acceptance_contract"].get("effect_verification_required", False)) is not bool:
            raise PreflightRefused("invalid-verification-requirement")
        item = (await self.invoke("work.read.item", {"item_id": intent.item_id}))["item"]
        if (release["release_digest"] != intent.release_digest
                or release["work_item_id"] != intent.item_id or item["id"] != intent.item_id
                or release["item_revision"].rsplit("@revise:", 1)[0] != item["edit_revision"]):
            raise PreflightRefused("effect-release-mismatch")

    async def verification_request(self, intent, runtime, *, run_id, item_id, observed_at):
        """Execute checks and prepare existing durable intake arguments; no write.

        Preserve the returned request/binding before capture. Native append and
        its retries belong to Sprintctl's existing evidence queue/sync.
        """
        import hashlib
        import json
        import re
        if (not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", item_id)
                or observed_at.tzinfo is None or observed_at.utcoffset() is None):
            raise PreflightRefused("invalid-verification-capture")
        resolved = await self.invoke("work.run.resolve-v1", {"run_id": run_id})
        binding = {key: resolved[key] for key in ("repo_id", "run_id", "principal_id", "workspace_id", "client_id", "grant_id")}
        if (binding["run_id"] != run_id or binding["principal_id"] != self.principal_id
                or binding["workspace_id"] != self.workspace_id or intent.workspace_id != self.workspace_id):
            raise PreflightRefused("verifier-run-binding-mismatch")
        await self.preflight_proposed(intent)
        checks = runtime.verify_proposed(intent)
        check_basis = runtime.check_revision_basis(intent.repository)
        check_revision = "sha256:" + hashlib.sha256(json.dumps(check_basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not checks or any(check["revision"] != check_revision for check in checks):
            raise PreflightRefused("check-revision-changed")
        await self.preflight_proposed(intent)
        detail = {"schema": "sprintctl-protected-artifact-verification/v1", "intent_id": intent.intent_id,
                  "intent_revision": intent.revision, "canonical_intent_digest": intent.canonical_intent_digest,
                  "release_digest": intent.release_digest,
                  "artifact": {"domain": "utf8-unified-diff/v1",
                               "digest": "sha256:" + hashlib.sha256(intent.unified_diff.encode("utf-8")).hexdigest()},
                  "checks": checks}
        digest = "sha256:" + hashlib.sha256(json.dumps(detail, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        tail_result = await self.invoke("work.evidence.tail-v1", {"run_id": run_id})
        if tail_result["run_id"] != run_id or tail_result["repo_id"] != binding["repo_id"]:
            raise PreflightRefused("verifier-tail-binding-mismatch")
        tail = tail_result["item"]
        if tail is None:
            seq, prev = 0, None
        else:
            # Shared chain wire contract: identity/content/position/predecessor.
            payload = {key: tail[key] for key in ("item_id", "digest", "chain_seq", "chain_prev_digest")}
            prev = "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            seq = tail["chain_seq"] + 1
        request = {"run_id": run_id, "item_id": item_id, "idempotency_key": item_id,
                   "kind": "protected-artifact-verification", "ref": "effect-intent:" + intent.intent_id,
                   "digest": digest, "collector": "vuoro-reconciler-artifact-checks/v1",
                   "validity": {"basis": "indefinite", "valid_from": observed_at.isoformat(),
                                "valid_until": None, "component_digests": {}},
                   "claims": [{"claim_type": "observation", "subject": intent.intent_id, "grant_id": None,
                               "freshness": None, "confirms": None, "detail": detail}],
                   "provenance": {"assurance": "trusted-local-check-execution", "check_revision_basis": check_basis},
                   "chain_seq": seq, "chain_prev_digest": prev}
        return {"request": request, "run_binding": binding}
