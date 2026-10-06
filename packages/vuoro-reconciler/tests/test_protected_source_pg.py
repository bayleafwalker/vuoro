"""Released schema-21 owner to protected preflight to real local signed Git.

Synthetic verifier assertions are distinct from execution attestations. The
separate disposable DB is mandatory when configured, never a shared backend.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import pytest
from fakes import FakeProviderClient
from conftest import base_commit_of
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.sprintctl_source import SprintctlIntentSource

_URL = os.environ.get("VUORO_PROTECTED_EFFECT_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="separate protected schema-21 owner DB not configured")


def context(principal, authorities):
    return SimpleNamespace(identity=SimpleNamespace(actor=principal, principal_id=principal,
        workspace_id="protected-pg", environment="disposable-proof", client_id=None, grant_id=None,
        authorities=frozenset(authorities)), request_id="req", basis_revision=None, catalog_revision="test",
        idempotency_key=None, idempotency_requirement="not-allowed")


@pytest.mark.parametrize("stage", ["unchanged", "before-push", "after-push"])
def test_verified_owner_acceptance_is_reconstructed_and_rechecked_before_forge_writes(
    bare_remote, reconciler_signing_key, stage
):
    from sprintctl import pg
    from sprintctl.application import WorkApplication
    from sprintctl.pg_migrations import CURRENT_SCHEMA_VERSION
    parsed = urlsplit(_URL)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.startswith("/lease_conformance")
    assert CURRENT_SCHEMA_VERSION == 21
    store = pg.get_connection(_URL)
    store.repo_id = "protected-source-" + uuid.uuid4().hex
    pg.init_db(store)
    app = WorkApplication.postgres(store)
    public = context("native:proposer:0", {"work:evidence", "work:read", "work.effect.propose", "work.effect.get"})
    verifier = context("native:verifier:0", {"work:evidence", "work:read", "work.effect.get", "work.effect.accept"})
    applier = context("native:applier:0", {"work:read", "work.effect.get", "work.effect.list-accepted", "work.effect.mark-applied"})
    def register(ctx):
        return app.invoke("work.run.register-v1", {"harness_id": "synthetic-pg-proof", "harness_build": "test", "model_id": "scripted",
            "recipe_id": "protected-effect-proof", "observed_profile": {"instruction_digest": "sha256:" + "a" * 64,
                "skill_digests": []}, "idempotency_key": uuid.uuid4().hex}, ctx)["run"]["run_id"]
    try:
        sprint = pg.create_sprint(store, "Protected proof", status="active")
        track = pg.get_or_create_track(store, sprint, "proof")
        item = pg.create_work_item(store, sprint, track, "Verified proposed effect")
        release = pg.reserve(store, item, actor="operator", session_id=uuid.uuid4().hex, role="execution",
            acceptance_contract={"effect_verification_required": True})
        proposal = app.invoke("work.effect.propose-v1", {"item_id": item, "run_id": register(public), "repository": "repo-a",
            "base_commit": base_commit_of(bare_remote), "title": "Fix typo", "rationale": "Protected proof",
            "unified_diff": "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n",
            "idempotency_key": uuid.uuid4().hex}, public)["intent"]
        detail = {"schema": "sprintctl-protected-artifact-verification/v1", "intent_id": proposal["intent_id"],
            "intent_revision": proposal["revision"], "canonical_intent_digest": proposal["canonical_intent_digest"],
            "release_digest": release["release_digest"], "artifact": {"domain": "utf8-unified-diff/v1",
                "digest": "sha256:" + hashlib.sha256(proposal["unified_diff"].encode()).hexdigest()},
            "checks": [{"name": "fixture-protected-patch-check", "revision": "sha256:" + "b" * 64, "status": "passed"}]}
        receipt_id = "verification-" + uuid.uuid4().hex
        verifier_run = register(verifier)
        app.invoke("work.evidence.append-v1", {"run_id": verifier_run, "item_id": receipt_id,
            "kind": "protected-artifact-verification", "ref": "fixture:protected-check", "collector": "synthetic-verifier/v1",
            "digest": "sha256:" + hashlib.sha256(json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
            "validity": {"basis": "indefinite", "valid_from": "2026-10-06T00:00:00+00:00", "valid_until": None, "component_digests": {}},
            "claims": [{"claim_type": "observation", "subject": proposal["intent_id"], "grant_id": None,
                "freshness": None, "confirms": None, "detail": detail}], "provenance": {},
            "chain_seq": 0, "chain_prev_digest": None, "idempotency_key": receipt_id}, verifier)
        async def verifier_invoke(operation, arguments): return app.invoke(operation, arguments, verifier)
        from vuoro_reconciler.intents import OperatorAcceptor
        trusted = SprintctlIntentSource(verifier_invoke, workspace_id="protected-pg", principal_id=verifier.identity.principal_id)
        asyncio.run(trusted.accept(proposal["intent_id"], OperatorAcceptor(verifier.identity.principal_id),
            revision=proposal["revision"], canonical_intent_digest=proposal["canonical_intent_digest"],
            verification_ref={"run_id": verifier_run, "item_id": receipt_id}))
        accepted = app.invoke("work.effect.get-v1", {"intent_id": proposal["intent_id"]}, verifier)["intent"]
        proof = deepcopy(accepted["acceptance"])
        assert pg.get_work_item(store, item)["status"] == "pending"
        repo_id = store.repo_id
        store.conn.close()
        # Fresh connection/source: no cached proposal IDs or receipt data.
        store = pg.get_connection(_URL)
        store.repo_id = repo_id
        app = WorkApplication.postgres(store)
        async def invoke(operation, arguments): return app.invoke(operation, arguments, applier)
        native = SprintctlIntentSource(invoke, workspace_id="protected-pg", principal_id=applier.identity.principal_id)
        def edit_work():
            _, revision = pg.get_work_item_with_edit_revision(store, item)
            pg.update_work_item_description(store, item, "Changed requirement", expected_revision=revision, actor="editor")
        class ChangingProvider(FakeProviderClient):
            async def find_branch(self, *args, **kwargs):
                if stage == "before-push": edit_work()
                return await super().find_branch(*args, **kwargs)
            async def push_branch(self, *args, **kwargs):
                await super().push_branch(*args, **kwargs)
                if stage == "after-push": edit_work()
            async def open_pull_request(self, *args, **kwargs):
                return replace(await super().open_pull_request(*args, **kwargs), url="https://forge.example/pr/1")
        provider = ChangingProvider(repositories={"repo-a": bare_remote})
        runtime = Reconciler(intent_source=native, provider=provider, signing_key=reconciler_signing_key,
            config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})))
        (outcome,) = asyncio.run(runtime.run_once())
        after = app.invoke("work.effect.get-v1", {"intent_id": proposal["intent_id"]}, applier)["intent"]
        assert after["acceptance"] == proof and after["release_digest"] == release["release_digest"]
        assert pg.get_work_item(store, item)["status"] == "pending"
        if stage == "unchanged":
            assert outcome.state == after["state"] == "applied"
            assert len(provider.pushed_branches) == len(provider.pull_requests) == 1
        else:
            assert outcome.state == "failed" and outcome.reason == "effect-release-mismatch"
            assert after["state"] == "accepted" and provider.pull_requests == []
            assert len(provider.pushed_branches) == (1 if stage == "after-push" else 0)
    finally:
        store.conn.close()
