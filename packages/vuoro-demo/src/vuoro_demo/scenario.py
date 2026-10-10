"""Native HTTP consumer of existing owner protocols on an owned fixture."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

from vuoro_client.client import AsyncVuoroClient
from vuoro_client.errors import InvocationRejectedError
from vuoro_client.profile import Profile
from vuoro_client.recovery import RecoveryLog

REPO = "settlement-demo"
ENVIRONMENT = "disposable-demo"


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class Caller:
    def __init__(self, endpoint: str, token: str, receipts: list[dict]):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("demo callers require the owned loopback HTTP endpoint")
        self.client = AsyncVuoroClient(Profile("demo", endpoint, "fixture",
            ENVIRONMENT), lambda _: token)
        self.receipts = receipts

    async def call(self, operation: str, arguments: dict, *, key: str | None = None):
        try:
            result = await self.client.invoke(operation, arguments, repo_id=REPO,
                idempotency_key=key)
        except InvocationRejectedError as error:
            self.receipts.append({"operation": operation, "refusal": error.code})
            raise
        self.receipts.append({"operation": operation, "arguments": arguments,
            "result": result})
        return result

    async def refuse(self, operation: str, arguments: dict, code: str, **kwargs):
        try:
            await self.call(operation, arguments, **kwargs)
        except InvocationRejectedError as error:
            if error.code != code:
                raise
            return
        raise AssertionError(f"{operation} did not refuse {code}")


async def register(caller: Caller, name: str) -> str:
    return (await caller.call("work.run.register-v1", {
        "harness_id": "vuoro-demo", "harness_build": "0.1.0",
        "model_id": "scripted-fixture", "recipe_id": "native-settlement/v1",
        "observed_profile": {"instruction_digest": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "skill_digests": []}, "idempotency_key": "demo-run-" + name,
    }))["run"]["run_id"]


def git_command(root: Path, *arguments: str) -> str:
    # Fixture repository only; no inherited credential helpers or signing hooks.
    env = {"PATH": os.environ["PATH"], "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
        "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid"}
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c",
        "commit.gpgsign=false", *arguments], cwd=root, env=env, check=True,
        capture_output=True, text=True, timeout=20).stdout.strip()


async def artifact(caller: Caller, verifier: Caller, item: int, root: Path,
        run: str, verifier_run: str, *, omit_verification: bool,
        wrong_artifact_digest: bool) -> dict:
    from sprintctl import outbox, pg, release_trailers
    from sprintctl.application import batch_idempotency_key, record_to_dict

    revision = (await caller.call("work.read.item", {"item_id": item}))["item"]["edit_revision"]
    reserved = (await caller.call("work.reservation.reserve-v1", {
        "item_id": item, "actor": "demo-B", "session_id": "demo-source",
        "expected_revision": revision,
        "acceptance_contract": {"effect_verification_required": True,
            "evidence_obligations": ["tests", "review"]},
    }, key="demo-reserve"))["reservation"]
    release = (await caller.call("work.read.release", {
        "release_digest": reserved["release_digest"]}))["release"]
    checkout = root / "artifact"
    checkout.mkdir(mode=0o700)
    git_command(checkout, "init", "-b", "main")
    (checkout / "fixture.txt").write_text("before\n")
    git_command(checkout, "add", "fixture.txt")
    git_command(checkout, "commit", "-m", "Fixture baseline")
    base = git_command(checkout, "rev-parse", "HEAD")
    (checkout / "fixture.txt").write_text("after\n")
    git_command(checkout, "add", "fixture.txt")
    git_command(checkout, "commit", "-m", "Fixture change\n\nVuoro-Release: " + release["release_digest"])
    commit = git_command(checkout, "rev-parse", "HEAD")
    patch = git_command(checkout, "diff", base, commit) + "\n"
    producer = outbox.open_outbox(root / "trailer-observations.sqlite")
    try:
        harvested = release_trailers.harvest_release_trailers(producer,
            repo_root=checkout, actor="demo-B")
        records = outbox.list_records(producer)
        assert harvested.status == "harvested" and len(records) == 1
        await caller.call("work.evidence.ingest", {"records": [record_to_dict(r)
            for r in records]}, key=batch_idempotency_key(records))
    finally:
        producer.close()
    confirmed = await caller.call("work.read.release", {"release_digest": release["release_digest"]})
    assert any(row["commit_sha"] == commit for row in confirmed["commits"])
    evidence = {"run_id": run, "item_id": "source-check", "kind": "test",
        "ref": "fixture:source-check", "digest": "sha256:" + hashlib.sha256(
            (checkout / "fixture.txt").read_bytes()).hexdigest(), "collector": "demo",
        "validity": {"basis": "indefinite", "valid_from": datetime.now(timezone.utc).isoformat(),
            "valid_until": None, "component_digests": {}}, "claims": [],
        "provenance": {}, "chain_seq": 0, "chain_prev_digest": None,
        "idempotency_key": "source-check"}
    await caller.call("work.evidence.append-v1", evidence)
    proposal = await caller.call("work.effect.propose-bound-v1", {
        "run_id": run, "item_id": item, "repository": "https://example.invalid/demo",
        "base_commit": base, "title": "Fixture change", "rationale": "Disposable proof",
        "unified_diff": patch, "idempotency_key": "bound-proposal",
        "causal_basis": {"expected_revision": release["item_revision"], "release_digest": release["release_digest"],
            "reserve_idempotency_key": "demo-reserve", "commit_sha": commit,
            "evidence_tail": {"item_id": evidence["item_id"], "chain_seq": 0,
                "entry_digest": pg.evidence_entry_digest(evidence)}}})
    intent = proposal["intent"]
    binding = {k: intent[k] for k in ("intent_id", "revision", "canonical_intent_digest")}
    await verifier.refuse("work.effect.accept-v1", dict(binding, revision=intent["revision"] + 1),
        "effect-revision-mismatch")
    await verifier.refuse("work.effect.accept-v1", binding, "effect-verification-required")
    patch_file = root / "fixture.patch"
    patch_file.write_text(patch)
    git_command(checkout, "apply", "--check", "--reverse", str(patch_file))
    assert git_command(checkout, "diff", "--name-only", base, commit) == "fixture.txt"
    body = {"schema": "sprintctl-protected-artifact-verification/v1",
        "intent_id": intent["intent_id"], "intent_revision": intent["revision"],
        "canonical_intent_digest": intent["canonical_intent_digest"],
        "release_digest": release["release_digest"], "artifact": {
            "domain": "utf8-unified-diff/v1", "digest": "sha256:" + hashlib.sha256(patch.encode()).hexdigest()},
        "checks": [{"name": "fixture-content", "revision": digest("fixture-content/v1"),
            "status": "passed" if (checkout / "fixture.txt").read_text() == "after\n" else "failed"},
            {"name": "patch-and-scope", "revision": digest("patch-and-scope/v1"), "status": "passed"}]}
    if wrong_artifact_digest:
        # Corrupt only the literal patch digest; preserve intent and Release.
        body["artifact"]["digest"] = "sha256:" + "0" * 64
    proof = dict(evidence, run_id=verifier_run, item_id="artifact-verification",
        kind="protected-artifact-verification", digest=digest(body),
        claims=[{"claim_type": "observation", "subject": intent["intent_id"],
            "grant_id": None, "freshness": None, "confirms": None, "detail": body}],
        idempotency_key="artifact-verification")
    if omit_verification:
        raise AssertionError("required artifact verification deliberately omitted; acceptance refused")
    await verifier.call("work.evidence.append-v1", proof)
    if wrong_artifact_digest:
        await verifier.refuse("work.effect.accept-v1", dict(binding,
            verification_ref={"run_id": verifier_run, "item_id": proof["item_id"]}),
            "effect-verification-refused")
        unchanged = (await verifier.call("work.effect.get-v1", {"intent_id": intent["intent_id"]}))["intent"]
        assert unchanged == intent
        assert not (await verifier.call("work.read.item-decisions", {"item_id": item}))["decisions"]
        raise AssertionError("wrong literal artifact digest; protected owner acceptance refused")
    accepted = (await verifier.call("work.effect.accept-v1", dict(binding,
        verification_ref={"run_id": verifier_run, "item_id": proof["item_id"]})))["intent"]
    assert accepted["acceptance"]["verification"]["receipt"] == body
    # Artifact acceptance alone must not settle work or release its dependency.
    assert (await verifier.call("work.read.item", {"item_id": item}))["item"]["status"] != "done"
    return {"release": release, "base_commit": base, "commit": commit,
        "causal_basis": proposal["admission"]["causal_basis"],
        "accepted_intent": accepted, "verification_receipt": body,
        "verification_evidence_digest": proof["digest"]}


async def worker(endpoint: str, token: str, item: int):
    caller = Caller(endpoint, token, [])
    try:
        run = await register(caller, "A")
        lease = (await caller.call("work.lease.acquire-v1", {"item_id": item,
            "run_id": run, "idempotency_key": "demo-lease-A"}))["lease"]
        await caller.call("work.lease.heartbeat-v1", {"lease_id": lease["lease_id"], "run_id": run})
        print(json.dumps({"run_id": run, "lease": lease}), flush=True)
        await asyncio.sleep(300)
    finally:
        await caller.client.aclose()


async def scenario(endpoint: str, tokens: dict[str, str], root: Path, receipts: list[dict],
        *, omit_verification: bool = False, omit_check: bool = False,
        wrong_artifact_digest: bool = False) -> dict:
    callers = {name: Caller(endpoint, token, receipts) for name, token in tokens.items()}
    b, verifier, a = callers["B"], callers["verifier"], callers["A"]
    child = None
    try:
        sprint = (await b.call("work.sprint.create", {"name": "Disposable settlement demo",
            "goal": "Loopback fixture only", "status": "active"}))["sprint"]["id"]
        items = []
        for title in ("X fixture artifact", "Y depends on X"):
            items.append((await b.call("work.item.create", {"sprint_id": sprint,
                "track_name": "fixture", "title": title, "description": "Disposable demo"}))["item"]["id"])
        x, y = items
        await b.call("work.item.dep.add", {"item_id": x, "blocked_item_id": y})
        run_b, verifier_run = await register(b, "B"), await register(verifier, "verifier")
        try:
            source = await artifact(b, verifier, x, root, run_b, verifier_run,
                omit_verification=omit_verification, wrong_artifact_digest=wrong_artifact_digest)
        except AssertionError:
            if wrong_artifact_digest:
                assert not (await b.call("work.read.item-decisions", {"item_id": x}))["decisions"]
                assert all(row["id"] != y for row in (await b.call("work.read.next-work",
                    {"sprint_id": sprint}))["ready_items"])
            raise
        assert not (await b.call("work.read.item-decisions", {"item_id": x}))["decisions"]
        assert all(row["id"] != y for row in (await b.call("work.read.next-work",
            {"sprint_id": sprint}))["ready_items"])
        child_env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        child_env["PYTHONNOUSERSITE"] = "1"
        child = subprocess.Popen([sys.executable, "-m", "vuoro_demo.cli", "_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=root, env=child_env)
        child.stdin.write(json.dumps({"endpoint": endpoint, "token": tokens["A"], "item": x}))
        child.stdin.close()
        # A owns an actual process. Read its bounded handshake off the event loop.
        line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=30)
        if not line:
            raise RuntimeError("fixture worker failed before acquiring lease")
        first = json.loads(line)
        child.send_signal(signal.SIGKILL)
        await asyncio.to_thread(child.wait, 10)
        assert child.returncode == -signal.SIGKILL
        receipts.append({"process": "A", "signal": "SIGKILL", "returncode": child.returncode,
            "run_id": first["run_id"], "lease": first["lease"]})
        await b.refuse("work.lease.acquire-v1", {"item_id": x, "run_id": run_b,
            "idempotency_key": "lease-B-early"}, "lease-held")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = await b.call("work.lease.read-v1", {"item_id": x})
            if current["current_lease"]["stale"]:
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("owner did not evaluate lease as stale")
        second = (await b.call("work.lease.acquire-v1", {"item_id": x, "run_id": run_b,
            "idempotency_key": "demo-lease-B"}))["lease"]
        assert second["generation"] == 2
        assert second["takeover_of"] == first["lease"]["lease_id"]
        report = {"lease_id": first["lease"]["lease_id"], "run_id": first["run_id"],
            "outcome": "succeeded", "summary": "Late A fixture", "payload": {"marker": "late-A"},
            "checks": [{"name": name, "status": "passed", "ref": "fixture:" + name}
                for name in ("tests", "review")], "idempotency_key": "demo-late-A"}
        await a.refuse("work.lease.report-outcome-v1", report, "claim-superseded")
        await a.refuse("work.lease.heartbeat-v1", {"lease_id": first["lease"]["lease_id"],
            "run_id": first["run_id"]}, "claim-superseded")
        retained = await b.call("work.lease.read-v1", {"item_id": x})
        assert any(r["reason_code"] == "claim-superseded" and r["disposition"] == "rejected"
            and r["payload"] == {"marker": "late-A"} for r in retained["outcome_reports"])
        outcome = dict(report, lease_id=second["lease_id"], run_id=run_b,
            summary="Verified fixture artifact", payload={"accepted_intent_id": source["accepted_intent"]["intent_id"],
                "canonical_intent_digest": source["accepted_intent"]["canonical_intent_digest"],
                "artifact_digest": source["verification_receipt"]["artifact"]["digest"],
                "release_digest": source["release"]["release_digest"]}, idempotency_key="report-B")
        if omit_check:
            outcome["checks"] = [outcome["checks"][0]]
            await b.refuse("work.lease.report-outcome-v1", outcome, "verification-unsatisfied")
            assert not (await b.call("work.read.item-decisions", {"item_id": x}))["decisions"]
            assert all(row["id"] != y for row in (await b.call("work.read.next-work",
                {"sprint_id": sprint}))["ready_items"])
            raise AssertionError("required review deliberately omitted; settlement refused")
        settled = await b.call("work.lease.report-outcome-v1", outcome)
        assert settled["settlement_effect"] == "settled"
        decisions = (await b.call("work.read.item-decisions", {"item_id": x}))["decisions"]
        assert len(decisions) == 1 and decisions[0]["kind"] == "accept"
        assert decisions[0]["actor"] == "sprintctl:lease-settlement"
        assert decisions[0]["release_digest"] == source["release"]["release_digest"]
        assert decisions[0]["evidence_digests"] == [settled["report"]["payload_digest"]]
        ready = await b.call("work.read.next-work", {"sprint_id": sprint})
        assert any(row["id"] == y for row in ready["ready_items"])
        log = RecoveryLog(root / "recovery", "demo-interruption").begin()
        log.append(record_kind="observation", summary="Fixture caller interrupted",
            created_at=datetime.now(timezone.utc).isoformat(), record_id="interruption",
            detail={"lease_id": first["lease"]["lease_id"]})
        export = RecoveryLog(root / "recovery", "demo-interruption").export()
        assert len(export) == 1 and export[0]["record_id"] == "interruption"
        return {"source": source, "decision": decisions[0], "dependency_unlocked": y,
            "outcome_payload_digest": settled["report"]["payload_digest"],
            "digest_domains": {"intent": "canonical EffectIntent", "artifact": "UTF8 unified diff",
                "verification": "canonical JSON protected receipt", "outcome": "owner outcome payload",
                "release": "owner Release"},
            "recovery": {"mode": "export-only; no replay or authority mutation", "records": export}}
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.wait, 10)
        for caller in callers.values():
            await caller.client.aclose()
