"""Protected consumer fault cases with real local Git and fake owner replies.

Native owner acceptance remains the authority; these synthetic replies isolate
the external-effect boundary. They do not assert deployed owner verification.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from fakes import FakeProviderClient
from conftest import base_commit_of
from vuoro_reconciler.cli import main
from vuoro_reconciler.intents import EffectIntent, OperatorAcceptor, canonical_digest
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.sprintctl_source import SprintctlIntentSource

DIFF = "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n"


def owner(bare_remote):
    intent = EffectIntent("intent_" + "1" * 26, "run_" + "2" * 26, "repo-a",
        base_commit_of(bare_remote), "Fix typo", "Protected change", DIFF, "ws", "proposer", item_id=1)
    digest = canonical_digest(intent)
    release = {"release_digest": "a" * 64, "work_item_id": 1,
        "item_revision": "item:fixture@description:v0@sha256:" + "b" * 64 + "@revise:0",
        "acceptance_contract": {"effect_verification_required": True}}
    detail = {"schema": "sprintctl-protected-artifact-verification/v1", "intent_id": intent.intent_id,
        "intent_revision": 1, "canonical_intent_digest": digest, "release_digest": release["release_digest"],
        "artifact": {"domain": "utf8-unified-diff/v1", "digest": "sha256:" + hashlib.sha256(DIFF.encode()).hexdigest()},
        "checks": [{"name": "protected-patch-check", "revision": "sha256:" + "c" * 64, "status": "passed"}]}
    proof = {"run_id": "run_" + "3" * 26, "item_id": "protected-check", "entry_digest": "sha256:" + "e" * 64,
        "evidence_digest": body_digest(detail), "verifier_principal": "operator", "workspace_id": "protected-ws",
        "client_id": None, "grant_id": None, "receipt": detail}
    acceptance = {"intent_id": intent.intent_id, "intent_revision": 1, "canonical_intent_digest": digest,
        "acceptor_principal": "operator", "acceptor_policy_version": None,
        "accepted_at": "2026-10-06T00:00:00Z", "verification": proof}
    row = {name: getattr(intent, name) for name in ("intent_id", "run_id", "item_id", "revision", "repository",
        "base_commit", "title", "rationale", "unified_diff", "proposer_principal")}
    row.update(canonical_intent_digest=digest, release_digest=release["release_digest"], acceptance=acceptance, state="accepted")
    return SimpleNamespace(row=row, release=release,
        item={"id": 1, "edit_revision": release["item_revision"].rsplit("@revise:", 1)[0]}, calls=[])


def body_digest(body):
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def source(state):
    async def invoke(operation, arguments):
        state.calls.append((operation, deepcopy(arguments)))
        if operation == "work.effect.list-accepted-v1":
            return {"intents": [deepcopy(state.row)] if state.row["state"] == "accepted" else []}
        if operation in {"work.effect.get-v1", "work.effect.accept-v1"}:
            return {"intent": deepcopy(state.row)}
        if operation == "work.read.release":
            return {"release": deepcopy(state.release)}
        if operation == "work.read.item":
            return {"item": deepcopy(state.item)}
        if operation == "work.effect.mark-applied-v1":
            state.row["state"] = "applied"
            return {"intent": deepcopy(state.row)}
        raise AssertionError(operation)
    return SprintctlIntentSource(invoke, workspace_id="applier-ws", principal_id="operator")


def reconciler(state, bare_remote, key, *, provider=None, native_source=None):
    provider = provider or FakeProviderClient(repositories={"repo-a": bare_remote})
    return Reconciler(intent_source=native_source or source(state), provider=provider, signing_key=key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"}))), provider


def test_stale_release_is_refused_before_git(bare_remote, reconciler_signing_key):
    state = owner(bare_remote)
    state.release["release_digest"] = "f" * 64
    runtime, provider = reconciler(state, bare_remote, reconciler_signing_key)
    (result,) = asyncio.run(runtime.run_once())
    assert result.state == "failed" and result.reason == "effect-release-mismatch"
    assert provider.pushed_branches == [] and provider.pull_requests == []
    assert provider.clones == []
    assert not any(op == "work.effect.mark-applied-v1" for op, _ in state.calls)


def test_exact_proof_survives_source_and_is_rechecked_at_publication(bare_remote, reconciler_signing_key):
    state = owner(bare_remote)
    original = deepcopy(state.row["acceptance"])
    native = source(state)
    (decoded,) = asyncio.run(native.poll_accepted())
    assert decoded.release_digest == state.release["release_digest"]
    assert decoded.acceptance == original
    runtime, provider = reconciler(state, bare_remote, reconciler_signing_key, native_source=native)
    (result,) = asyncio.run(runtime.run_once())
    assert result.state == "applied" and len(provider.pushed_branches) == 1 and len(provider.pull_requests) == 1
    assert state.row["acceptance"] == original
    assert sum(op == "work.effect.get-v1" for op, _ in state.calls) == 4


@pytest.mark.parametrize("fault", ["artifact", "domain", "intent-id", "revision", "canonical", "receipt-release", "failed-check", "unknown-check", "body-digest", "verifier", "missing-proof", "edited-work", "malformed-requirement"])
def test_wrong_protected_binding_never_reaches_external_write(bare_remote, reconciler_signing_key, fault):
    state = owner(bare_remote)
    proof = state.row["acceptance"]["verification"]
    body = proof["receipt"]
    if fault == "artifact": body["artifact"]["digest"] = "sha256:" + "0" * 64
    if fault == "domain": body["artifact"]["domain"] = "provider-artifact"
    if fault == "intent-id": body["intent_id"] = "intent_other"
    if fault == "revision": body["intent_revision"] += 1
    if fault == "canonical": body["canonical_intent_digest"] = "0" * 64
    if fault == "receipt-release": body["release_digest"] = "0" * 64
    if fault == "failed-check": body["checks"][0]["status"] = "failed"
    if fault == "unknown-check": body["checks"][0]["revision"] = "unknown"
    if fault == "verifier": proof["verifier_principal"] = "public-proposer"
    if fault == "missing-proof": state.row["acceptance"].pop("verification")
    if fault == "edited-work": state.item["edit_revision"] += "changed"
    if fault == "malformed-requirement": state.release["acceptance_contract"]["effect_verification_required"] = []
    proof["evidence_digest"] = "sha256:" + "0" * 64 if fault == "body-digest" else body_digest(body)
    runtime, provider = reconciler(state, bare_remote, reconciler_signing_key)
    (result,) = asyncio.run(runtime.run_once())
    assert result.state == "failed"
    assert provider.pushed_branches == [] and provider.pull_requests == []
    assert provider.clones == []
    assert state.row["state"] == "accepted"


@pytest.mark.parametrize("stage", ["checkout", "lookup", "push"])
def test_changed_work_is_rechecked_at_each_external_boundary(bare_remote, reconciler_signing_key, stage, monkeypatch):
    state = owner(bare_remote)
    class ChangingProvider(FakeProviderClient):
        async def find_branch(self, *args, **kwargs):
            if stage == "lookup": state.item["edit_revision"] += "changed"
            return await super().find_branch(*args, **kwargs)
        async def push_branch(self, *args, **kwargs):
            await super().push_branch(*args, **kwargs)
            if stage == "push": state.item["edit_revision"] += "changed"
    provider = ChangingProvider(repositories={"repo-a": bare_remote})
    runtime, _ = reconciler(state, bare_remote, reconciler_signing_key, provider=provider)
    original = Reconciler._prepare_commit
    def changed_after_checkout(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if stage == "checkout": state.item["edit_revision"] += "changed"
        return result
    monkeypatch.setattr(Reconciler, "_prepare_commit", changed_after_checkout)
    (result,) = asyncio.run(runtime.run_once())
    assert result.state == "failed" and result.reason == "effect-release-mismatch"
    assert len(provider.pushed_branches) == (1 if stage == "push" else 0)
    assert provider.pull_requests == [] and state.row["state"] == "accepted"


def test_receipt_reference_is_forwarded_only_through_authenticated_accept(bare_remote):
    state = owner(bare_remote)
    native = source(state)
    ref = {"run_id": "run_" + "3" * 26, "item_id": "protected-check"}
    asyncio.run(native.accept(state.row["intent_id"], OperatorAcceptor("operator"), revision=1,
        canonical_intent_digest=state.row["canonical_intent_digest"], verification_ref=ref))
    assert state.calls[-1][0] == "work.effect.accept-v1"
    assert state.calls[-1][1]["verification_ref"] == ref
    assert not any("principal" in key or "acceptor" in key for key in state.calls[-1][1])


def test_cli_partial_reference_refuses_without_owner_call():
    import io
    class NoCalls:
        async def poll_proposed(self): raise AssertionError("must refuse before lookup")
    out = io.StringIO()
    assert main(["accept", "intent_fixture", "--operator", "operator", "--yes", "--verification-run-id", "run_fixture"],
        intent_source=NoCalls(), stdout=out) == 1
    assert "both run and item IDs" in out.getvalue()


def test_cli_complete_reference_reaches_the_protected_source(bare_remote):
    import io
    state = owner(bare_remote)
    row = deepcopy(state.row)
    row["acceptance"] = None
    pending = source(state)._intent(row)
    captured = []
    class Spy:
        async def poll_proposed(self): return [pending]
        async def accept(self, intent_id, acceptor, **binding): captured.append((intent_id, acceptor, binding))
    out = io.StringIO()
    assert main(["accept", pending.intent_id, "--operator", "operator", "--yes",
        "--verification-run-id", "run_protected", "--verification-item-id", "receipt"],
        intent_source=Spy(), stdout=out) == 0
    assert captured[0][2] == {"revision": 1, "canonical_intent_digest": pending.canonical_intent_digest,
        "verification_ref": {"run_id": "run_protected", "item_id": "receipt"}}


def test_bound_custom_source_without_preflight_cannot_publish(bare_remote, reconciler_signing_key):
    state = owner(bare_remote)
    pending = source(state)._intent(state.row)
    class OldSource:
        async def poll_proposed(self): return []
        async def poll_accepted(self): return [pending]
        async def report_failed(self, *args, **kwargs): pass
    runtime, provider = reconciler(state, bare_remote, reconciler_signing_key, native_source=OldSource())
    (result,) = asyncio.run(runtime.run_once())
    assert result.state == "failed" and result.reason == "protected-preflight-unavailable"
    assert provider.clones == provider.pushed_branches == provider.pull_requests == []


def test_bound_intent_missing_current_release_has_a_stable_refusal(bare_remote):
    from vuoro_reconciler.verification import PreflightRefused
    state = owner(bare_remote)
    pending = source(state)._intent(state.row)
    class GoneRelease(Exception): code = "release-not-found"
    async def invoke(operation, arguments):
        if operation == "work.effect.get-v1": return {"intent": state.row}
        raise GoneRelease()
    native = SprintctlIntentSource(invoke, workspace_id="ws", principal_id="applier")
    with pytest.raises(PreflightRefused, match="effect-release-mismatch"):
        asyncio.run(native.preflight(pending))


def test_custom_preflight_cannot_bypass_raw_receipt_binding(bare_remote, reconciler_signing_key):
    state = owner(bare_remote)
    body = state.row["acceptance"]["verification"]["receipt"]
    body["artifact"]["digest"] = "sha256:" + "0" * 64
    state.row["acceptance"]["verification"]["evidence_digest"] = body_digest(body)
    pending = source(state)._intent(state.row)
    class CustomSource:
        async def poll_proposed(self): return []
        async def poll_accepted(self): return [pending]
        async def preflight(self, intent): pass
        async def report_failed(self, *args, **kwargs): pass
    runtime, provider = reconciler(state, bare_remote, reconciler_signing_key, native_source=CustomSource())
    (result,) = asyncio.run(runtime.run_once())
    assert result.state == "failed" and result.reason == "effect-verification-refused"
    assert provider.clones == provider.pushed_branches == provider.pull_requests == []
