"""Disposable PostgreSQL restart-to-signed-PR proof for the served source.

Opt-in local proof: VUORO_EFFECT_TEST_PG_URL. Setting it requires a working
loopback lease_conformance* DB and installed candidate owner; never skipped
when configured. CI uses the immutable owner pin after its release.
"""
import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import pytest
from fakes import FakeProviderClient
from conftest import base_commit_of
from vuoro_reconciler.sprintctl_source import SprintctlIntentSource
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig


@pytest.mark.skipif(not os.environ.get("VUORO_EFFECT_TEST_PG_URL"), reason="disposable effect owner DB not configured")
def test_fresh_owner_source_after_restart_reconciles_and_marks_applied(bare_remote, reconciler_signing_key):
    from sprintctl import pg
    from sprintctl.application import WorkApplication
    url = os.environ["VUORO_EFFECT_TEST_PG_URL"]
    parsed = urlsplit(url)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.startswith("/lease_conformance")
    store = pg.get_connection(url)
    repo = "effect-proof-" + uuid.uuid4().hex
    store.repo_id = repo
    pg.init_db(store)
    def context(principal):
        return SimpleNamespace(identity=SimpleNamespace(actor=principal, principal_id=principal,
            workspace_id="ws", environment="effect-proof", client_id=None, grant_id=None,
            authorities=frozenset({"work:evidence", "work:read", "work.effect.propose", "work.effect.get",
                "work.effect.list-proposed", "work.effect.list-accepted", "work.effect.accept",
                "work.effect.reject", "work.effect.mark-applied"})), request_id="req",
                basis_revision=None, catalog_revision="test", idempotency_key=None, idempotency_requirement="not-allowed")
    app = WorkApplication.postgres(store)
    sprint = pg.create_sprint(store, "Effect proof", "Restart", "2026-01-01", "2026-12-31", "active")
    track = pg.get_or_create_track(store, sprint, "proof")
    item = pg.create_work_item(store, sprint, track, "proposed effect")
    run = app.invoke("work.run.register-v1", {"harness_id": "test", "harness_build": "test", "model_id": "scripted",
        "recipe_id": "effects", "observed_profile": {"instruction_digest": "sha256:" + "a" * 64, "skill_digests": []},
        "idempotency_key": uuid.uuid4().hex}, context("github:100:0"))["run"]["run_id"]
    proposal = app.invoke("work.effect.propose-v1", {"item_id": item, "run_id": run, "repository": "repo-a",
        "base_commit": base_commit_of(bare_remote), "title": "Fix typo", "rationale": "Proof",
        "unified_diff": "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n",
        "idempotency_key": uuid.uuid4().hex}, context("github:100:0"))["intent"]
    app.invoke("work.effect.accept-v1", {name: proposal[name] for name in
        ("intent_id", "revision", "canonical_intent_digest")}, context("github:900:0"))
    store.conn.close()
    # Restart: no proposal IDs are given to the fresh source or reconciler.
    resumed = pg.get_connection(url)
    resumed.repo_id = repo
    resumed_app = WorkApplication.postgres(resumed)
    async def invoke(operation, arguments):
        return resumed_app.invoke(operation, arguments, context("github:900:0"))
    source = SprintctlIntentSource(invoke, workspace_id="ws", principal_id="github:900:0")
    from dataclasses import replace
    class HttpNamedFakeProvider(FakeProviderClient):
        async def open_pull_request(self, *args, **kwargs):
            pr = await super().open_pull_request(*args, **kwargs)
            return replace(pr, url="https://forge.example/repo-a/pulls/1")
    provider = HttpNamedFakeProvider(repositories={"repo-a": bare_remote})
    reconciler = Reconciler(intent_source=source, provider=provider, signing_key=reconciler_signing_key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})))
    try:
        (result,) = asyncio.run(reconciler.run_once())
        assert result.state == "applied"
        assert len(provider.pull_requests) == 1
        applied = resumed_app.invoke("work.effect.get-v1", {"intent_id": result.intent_id}, context("github:900:0"))["intent"]
        assert applied["state"] == "applied"
        assert applied["application"]["commit_sha"] == result.commit_sha
        assert applied["application"]["pr_url"] == result.pr_url
        assert applied["acceptance"]["canonical_intent_digest"] == applied["canonical_intent_digest"]
        assert asyncio.run(source.poll_accepted()) == []
    finally:
        resumed.conn.close()
