"""Real Git validation, native packet shaping and deterministic stale-work faults."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json

import pytest
from test_protected_preflight import owner, body_digest
from fakes import FakeProviderClient
from vuoro_reconciler.cli import main
from vuoro_reconciler.diff_policy import DiffPolicy
from vuoro_reconciler.intents import canonical_digest
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.sprintctl_source import SprintctlIntentSource
from vuoro_reconciler.verification import PreflightRefused

RUN = "run_" + "3" * 26
AT = datetime(2026, 10, 6, tzinfo=timezone.utc)


def setup(bare_remote, *, tail=None):
    state = owner(bare_remote)
    state.row.update(state="proposed", acceptance=None)
    state.run = {"run_id": RUN, "principal_id": "verifier", "workspace_id": "ws", "client_id": None, "grant_id": None}
    async def invoke(operation, arguments):
        state.calls.append((operation, deepcopy(arguments)))
        if operation == "work.effect.list-proposed-v1": return {"intents": [deepcopy(state.row)]}
        if operation == "work.effect.get-v1": return {"intent": deepcopy(state.row)}
        if operation == "work.read.release": return {"release": deepcopy(state.release)}
        if operation == "work.read.item": return {"item": deepcopy(state.item)}
        if operation == "work.run.resolve-v1": return {"repo_id": "agentops", **deepcopy(state.run)}
        if operation == "work.evidence.tail-v1": return {"repo_id": "agentops", "run_id": RUN, "item": tail}
        raise AssertionError("verification must not write: " + operation)
    source = SprintctlIntentSource(invoke, workspace_id="ws", principal_id="verifier")
    # Verification does not use a signing key. Any signing attempt must fail.
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    runtime = Reconciler(intent_source=source, provider=provider, signing_key=None,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})))
    return state, source, runtime, provider


def prepare(source, runtime, intent):
    return asyncio.run(source.verification_request(intent, runtime, run_id=RUN,
        item_id="protected-check-001", observed_at=AT))


def test_real_checks_emit_native_capture_without_acceptance_signing_or_publication(bare_remote):
    state, source, runtime, provider = setup(bare_remote)
    packet = prepare(source, runtime, source._intent(state.row))
    request = packet["request"]
    detail = request["claims"][0]["detail"]
    assert [check["name"] for check in detail["checks"]] == ["patch-text-and-path-safety",
        "clean-checkout-and-patch-application", "staged-content-and-diff-policy"]
    assert all(check["status"] == "passed" and len(check["revision"]) == 71 for check in detail["checks"])
    assert detail["artifact"]["digest"] == "sha256:" + hashlib.sha256(state.row["unified_diff"].encode()).hexdigest()
    assert request["digest"] == body_digest(detail)
    assert request["chain_seq"] == 0 and request["chain_prev_digest"] is None
    assert packet["run_binding"] == {"repo_id": "agentops", **state.run}
    assert len(provider.clones) == 1 and provider.pushed_branches == provider.pull_requests == []
    assert state.row["state"] == "proposed" and state.row["acceptance"] is None
    assert [op for op, _ in state.calls].count("work.effect.get-v1") == 2


@pytest.mark.parametrize("fault", ["binary", "nonapplicable", "delete", "control", "unlisted", "foreign-principal", "foreign-workspace", "canonical", "malformed-required"])
def test_failed_check_or_native_binding_emits_no_success_packet(bare_remote, fault):
    state, source, runtime, provider = setup(bare_remote)
    if fault == "binary": state.row["unified_diff"] += "GIT binary patch\n"
    if fault == "nonapplicable": state.row["unified_diff"] = state.row["unified_diff"].replace("-old", "-missing")
    if fault == "delete": state.row["unified_diff"] = "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
    if fault == "control": state.row["unified_diff"] = state.row["unified_diff"].replace("+new", "+new\x01")
    if fault == "unlisted": runtime = replace(runtime, config=ReconcilerConfig(repository_allowlist=frozenset()))
    if fault == "foreign-principal": state.run["principal_id"] = "other"
    if fault == "foreign-workspace": state.run["workspace_id"] = "other"
    if fault == "malformed-required": state.release["acceptance_contract"]["effect_verification_required"] = "yes"
    intent = source._intent(state.row)
    if fault != "canonical":
        state.row["canonical_intent_digest"] = canonical_digest(intent)
        intent = source._intent(state.row)
    else:
        state.row["unified_diff"] += "\n"
        intent = source._intent(state.row)
    with pytest.raises(PreflightRefused): prepare(source, runtime, intent)
    assert provider.pushed_branches == provider.pull_requests == []
    assert not any("append" in op or "accept-v1" in op for op, _ in state.calls)


def test_work_edit_during_real_checkout_refuses_receipt(bare_remote, monkeypatch):
    state, source, runtime, provider = setup(bare_remote)
    original = Reconciler._prepare_checkout
    def edit(self, intent, workdir):
        changes = original(self, intent, workdir)
        state.item["edit_revision"] += "changed"
        return changes
    monkeypatch.setattr(Reconciler, "_prepare_checkout", edit)
    with pytest.raises(PreflightRefused, match="effect-release-mismatch"):
        prepare(source, runtime, source._intent(state.row))
    assert provider.pushed_branches == provider.pull_requests == []


def test_check_revision_changes_with_trusted_policy(bare_remote):
    _, _, runtime, _ = setup(bare_remote)
    other = replace(runtime, config=replace(runtime.config, diff_policies={"repo-a": DiffPolicy(path_allowlist=frozenset({"docs/*"}))}))
    assert runtime._check_revision("repo-a") != other._check_revision("repo-a")


def test_native_chain_request_extends_independent_known_tail(bare_remote):
    tail = {"item_id": "first", "digest": "sha256:" + "a" * 64, "chain_seq": 4, "chain_prev_digest": "sha256:" + "b" * 64}
    state, source, runtime, _ = setup(bare_remote, tail=tail)
    request = prepare(source, runtime, source._intent(state.row))["request"]
    assert request["chain_seq"] == 5
    assert request["chain_prev_digest"] == "sha256:" + hashlib.sha256(json.dumps(tail, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_cli_verify_outputs_capture_packet(bare_remote):
    _, source, runtime, provider = setup(bare_remote)
    out = io.StringIO()
    assert main(["verify", "intent_" + "1" * 26, "--run-id", RUN, "--receipt-id", "protected-check-001"],
        intent_source=source, validation_runtime=runtime, stdout=out) == 0
    assert json.loads(out.getvalue())["request"]["kind"] == "protected-artifact-verification"
    assert provider.pushed_branches == provider.pull_requests == []


def test_fault_oracle_detects_skipped_patch_application(bare_remote, monkeypatch):
    state, source, runtime, _ = setup(bare_remote)
    state.row["unified_diff"] = state.row["unified_diff"].replace("-old", "-missing")
    state.row["canonical_intent_digest"] = canonical_digest(source._intent(state.row))
    monkeypatch.setattr(Reconciler, "_prepare_checkout", lambda *args: [])
    # Removing the actual application check manufactures a success receipt.
    # The same refusal oracle must detect this deliberately broken validator.
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        with pytest.raises(PreflightRefused, match="diff-does-not-apply"):
            prepare(source, runtime, source._intent(state.row))


def test_changed_trusted_check_revision_refuses_receipt(bare_remote, monkeypatch):
    state, source, runtime, _ = setup(bare_remote)
    revisions = iter(["sha256:" + "a" * 64, "sha256:" + "b" * 64])
    monkeypatch.setattr(Reconciler, "_check_revision", lambda *args: next(revisions))
    with pytest.raises(PreflightRefused, match="check-revision-changed"):
        prepare(source, runtime, source._intent(state.row))


def test_cli_missing_runtime_refuses_before_native_lookup(bare_remote):
    state, source, _, _ = setup(bare_remote)
    out = io.StringIO()
    assert main(["verify", "intent_fixture", "--run-id", RUN, "--receipt-id", "protected-check-001"],
        intent_source=source, stdout=out) == 1
    assert state.calls == [] and "runtime" in out.getvalue()
