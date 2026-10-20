"""Actual private execution → owner evidence → verification → acceptance.

Owned local services and C0 fixtures only. No hosted path, production operation,
provider OAuth, general model competence or distributed isolation is claimed.
"""

from __future__ import annotations
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

from vuoro_service.identity import Identity
from .fixture import fixture
from .scenario import Caller, ENVIRONMENT, REPO, digest, git_command
from .private_profile import (
    PROFILE,
    VerificationRefused,
    revision,
    sha,
    verify,
    instructions,
    SOURCE_DIGEST,
)

VERSIONS = {
    "vuoro-client": "0.1.2",
    "vuoro-service": "0.1.97",
    "sprintctl": "0.22.0",
    "sovite-platform": "0.1.16",
    "vuoro-adapter-kit": "0.2.2",
}


def evidence(run, item, kind, body, tail, *, ref=None):
    from sprintctl.pg import evidence_entry_digest

    return {
        "run_id": run,
        "item_id": item,
        "kind": kind,
        "ref": ref or ("workflow:" + item),
        "digest": digest(body),
        "collector": PROFILE,
        "validity": {
            "basis": "indefinite",
            "valid_from": "2026-10-20T00:00:00+00:00",
            "valid_until": None,
            "component_digests": {},
        },
        "claims": [
            {
                "claim_type": "observation",
                "subject": body.get("intent_id", "maintenance-handover"),
                "grant_id": None,
                "freshness": None,
                "confirms": None,
                "detail": body,
            }
        ],
        "provenance": {},
        "chain_seq": 0 if tail is None else tail["chain_seq"] + 1,
        "chain_prev_digest": None if tail is None else evidence_entry_digest(tail),
        "idempotency_key": "evidence-" + item,
    }


async def append(caller, run, item, kind, body, ref=None):
    tail = (await caller.call("work.evidence.tail-v1", {"run_id": run}))["item"]
    args = evidence(run, item, kind, body, tail, ref=ref)
    result = await caller.call("work.evidence.append-v1", args)
    return args, result["item"]


async def register(caller, name, *, predecessor=None):
    args = {
        "harness_id": "vuoro-private-workflow",
        "harness_build": version("vuoro-demo"),
        "model_id": "external-private-execution"
        if name == "producer"
        else "deterministic-profile-verifier",
        "recipe_id": PROFILE,
        "observed_profile": {"instruction_digest": revision(), "skill_digests": []},
        "idempotency_key": "wf01-register-" + name,
    }
    if predecessor:
        args["predecessor_run_id"] = predecessor
    return (await caller.call("work.run.register-v1", args))["run"]["run_id"]


async def producer(value):
    # The source file is explicit C0 input. No operator, signer or storage key is
    # passed to this process; stdin carries only this session's Vuoro token.
    caller = Caller(value["endpoint"], value["token"], [])
    try:
        run = await register(caller, "producer")
        source = Path(value["source"]).read_bytes()
        parsed = json.loads(source)
        args = {
            "work_id": value["work_id"],
            "run_id": run,
            "grant_id": "maintenance",
            "task": json.dumps(
                {"objective": parsed["objective"], "sources": parsed["snippets"]}
            ),
        }
        result = await caller.call(
            "sovite.task.request-v1", args, key="wf01-private-task"
        )
        repeated = await caller.call(
            "sovite.task.request-v1", args, key="wf01-private-task"
        )
        if repeated["receipt"] != result["receipt"]:
            raise RuntimeError("Task retry changed receipt")
        body = {
            "schema": "sovite-work-link/v1",
            "work_id": value["work_id"],
            "run_id": run,
            "source_digest": sha(source),
            "receipt": result["receipt"],
            "task_binding": result["task_binding"],
            "grant_id": "maintenance",
            "generation": value["generation"],
        }
        _, entry = await append(
            caller,
            run,
            "private-task-link",
            "sovite-task-receipt",
            body,
            ref="sovite:receipt:" + result["receipt"],
        )
        print(
            json.dumps({"run_id": run, "link_digest": entry["digest"], "link": body}),
            flush=True,
        )
        await asyncio.sleep(600)
    finally:
        await caller.client.aclose()


async def verifier(value):
    import httpx

    caller = Caller(value["endpoint"], value["token"], [])
    try:
        run = await register(caller, "verifier", predecessor=value["predecessor_run"])
        recovered = await caller.call(
            "work.run.predecessor-context-v1", {"run_id": run}
        )
        links = [e for e in recovered["evidence"] if e["kind"] == "sovite-task-receipt"]
        if len(links) != 1:
            raise VerificationRefused("Durable predecessor receipt link unavailable")
        link = links[0]["claims"][0]["detail"]
        if link["work_id"] != value["work_id"]:
            raise VerificationRefused("Wrong work correlation")
        # Read the exact live protected bytes using independently authorized
        # Sovite access; never trust a producer-supplied result or local locator.
        with httpx.Client(
            base_url=value["sovite_origin"],
            headers={"Authorization": "Bearer " + value["artifact_reader"]},
            timeout=20,
            trust_env=False,
            follow_redirects=False,
        ) as private:
            for _ in range(1200):
                jobs = private.get("/api/jobs").raise_for_status().json()["items"]
                job = next(j for j in jobs if j["receipt"] == link["receipt"])
                if job["status"] in ("completed", "failed", "blocked", "cancelled"):
                    break
                await asyncio.sleep(0.5)
            if job["status"] != "completed" or job["generation"] != link["generation"]:
                raise VerificationRefused(
                    "The bound private execution did not complete"
                )
            artifact = (
                private.get("/api/jobs/" + job["id"] + "/artifact")
                .raise_for_status()
                .content
            )
        if value.get("deny_source"):
            raise VerificationRefused("Source access denied")
        source = Path(value["source"]).read_bytes()
        manifest = {
            "schema": "sovite-private-artifact-manifest/v1",
            "work_id": value["work_id"],
            "run_id": link["run_id"],
            "receipt": link["receipt"],
            "task_binding": link["task_binding"],
            "source_digest": link["source_digest"],
            "artifact_digest": sha(artifact),
            "generation": link["generation"],
            "profile": PROFILE,
            "profile_revision": revision(),
        }
        # The trusted stager's manifest must bind these independently observed
        # bytes. A checksum supplied only by the producer is insufficient.
        if manifest != value["manifest"]:
            raise VerificationRefused("Private artifact differs from proposed manifest")
        observed = verify(source, artifact, manifest)
        bad = dict(manifest, artifact_digest="sha256:" + "0" * 64)
        try:
            verify(source, artifact, bad)
        except VerificationRefused:
            pass
        else:
            raise AssertionError("Wrong private digest was accepted")
        # Only C0 sample content is saved into the operator-owned private state.
        output = Path(value["private_output"])
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(artifact)
        _, observation = await append(
            caller,
            run,
            "private-verification",
            "private-artifact-verification",
            {
                **manifest,
                "schema": "private-handover-observation/v1",
                "checks": observed["checks"],
            },
        )
        print(
            json.dumps(
                {
                    "run_id": run,
                    "observation_digest": observation["digest"],
                    "manifest": manifest,
                    "wrong_private_digest_refused": True,
                    "source_ids": observed["source_ids"],
                    "checks": observed["checks"],
                }
            ),
            flush=True,
        )
    finally:
        await caller.client.aclose()


async def owner_verification(owner, verified, value, evaluation):
    """The acceptor countersigns after recovering independent evidence and bytes.

    Sprintctl binds a protected receipt to the accepting identity. Independent
    verifier evidence is an observation, never borrowed acceptance authority.
    """
    run = await register(owner, "owner", predecessor=verified["run_id"])
    context = await owner.call("work.run.predecessor-context-v1", {"run_id": run})
    entries = [
        e
        for e in context["evidence"]
        if e["kind"] == "private-artifact-verification"
        and e["digest"] == verified["observation_digest"]
    ]
    if len(entries) != 1:
        raise VerificationRefused("Independent verification evidence unavailable")
    detail = entries[0]["claims"][0]["detail"]
    if digest(detail) != entries[0]["digest"]:
        raise VerificationRefused("Independent evidence digest changed")
    manifest = value["manifest"]
    with evaluation.client() as private:
        jobs = private.get("/api/jobs").raise_for_status().json()["items"]
        job = next(j for j in jobs if j["receipt"] == manifest["receipt"])
        if job["status"] != "completed" or job["generation"] != manifest["generation"]:
            raise VerificationRefused("Private result no longer available")
        artifact = (
            private.get("/api/jobs/" + job["id"] + "/artifact")
            .raise_for_status()
            .content
        )
    observed = verify(Path(value["source"]).read_bytes(), artifact, manifest)
    expected = {
        **manifest,
        "schema": "private-handover-observation/v1",
        "checks": observed["checks"],
    }
    if detail != expected:
        raise VerificationRefused("Independent observation differs from owner read")
    await append(
        owner,
        run,
        "independent-verification-binding",
        "private-acceptance-basis",
        {
            "schema": "private-acceptance-basis/v1",
            "verifier_run": verified["run_id"],
            "verifier_evidence_digest": verified["observation_digest"],
            "manifest_digest": digest(manifest),
            "profile_revision": revision(),
        },
    )
    intent = value["intent"]
    base = {
        "schema": "sprintctl-protected-artifact-verification/v1",
        "intent_id": intent["intent_id"],
        "intent_revision": intent["revision"],
        "canonical_intent_digest": intent["canonical_intent_digest"],
        "release_digest": value["release_digest"],
        "artifact": {"domain": "utf8-unified-diff/v1", "digest": value["patch_digest"]},
        "checks": observed["checks"],
    }
    for name, body in (
        (
            "wrong-manifest-digest",
            {
                **base,
                "artifact": {
                    "domain": "utf8-unified-diff/v1",
                    "digest": "sha256:" + "0" * 64,
                },
            },
        ),
        ("missing-profile-check", {**base, "checks": base["checks"][:-1]}),
        ("handover-proof", base),
    ):
        await append(owner, run, name, "protected-artifact-verification", body)
    return run


def child_process(kind, value, root):
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    env["PYTHONNOUSERSITE"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "vuoro_demo.private_workflow", kind],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=root,
        env=env,
    )
    process.stdin.write(json.dumps(value))
    process.stdin.close()
    return process


def configure_capability(evaluation, root, holder):
    from sovite.vuoro_adapter import (
        LocalBinding,
        LocalSoviteApplication,
        SoviteInvocationRefused,
        register_sovite_catalog,
    )

    def configure(registry, application, identities, tokens):
        roles = {
            "stager": {
                "work:read",
                "work:write",
                "work:sprint",
                "work:evidence",
                "work:batch",
                "work.effect.propose",
                "work.effect.get",
            },
            "producer": {"work:read", "work:evidence", "sovite:request", "sovite:read"},
            "verifier": {"work:read", "work:evidence"},
            "owner": {
                "work:read",
                "work:evidence",
                "work:lifecycle",
                "work.effect.accept",
                "work.effect.get",
            },
        }
        identities.clear()
        tokens.clear()
        for name, authorities in roles.items():
            token = secrets.token_urlsafe(40)
            tokens[name] = token
            identities[token] = Identity(
                actor="bayleaf-" + name,
                environment=ENVIRONMENT,
                authorities=frozenset(authorities),
                repo_ids=frozenset({REPO}),
                workspace_id="bayleaf",
                principal_id="local:" + name + ":0",
            )
        key = secrets.token_bytes(32)
        fd = os.open(
            root / "receipt-reference-key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)

        def guard(context, work_id, run_id):
            try:
                facts = application.invoke(
                    "work.run.resolve-v2",
                    {"run_id": run_id},
                    replace(context, idempotency_key=None),
                )
                item = application.invoke(
                    "work.read.item",
                    {"item_id": work_id},
                    replace(context, idempotency_key=None),
                )["item"]
                if (
                    facts["state"] != "active"
                    or item["status"] == "done"
                    or work_id != holder["work_id"]
                ):
                    raise SoviteInvocationRefused("Work attempt is not current")
            except SoviteInvocationRefused:
                raise
            except Exception:
                raise SoviteInvocationRefused("Work attempt unavailable") from None

        binding = LocalBinding(
            "bayleaf",
            REPO,
            "local:producer:0",
            evaluation.origin,
            evaluation.caller_token,
            frozenset({"maintenance"}),
            key,
        )
        register_sovite_catalog(registry, LocalSoviteApplication((binding,), guard))

    return configure


async def stage(stager, owner, root, work_id, manifest):
    from sprintctl import outbox, release_trailers, pg
    from sprintctl.application import batch_idempotency_key, record_to_dict

    run = await register(stager, "stager")
    current = (await stager.call("work.read.item", {"item_id": work_id}))["item"]
    checks = {
        name: revision()
        for name in (
            "private-artifact-integrity",
            "source-and-handover-contract",
            "receipt-manifest-binding",
        )
    }
    reserved = (
        await stager.call(
            "work.reservation.reserve-v1",
            {
                "item_id": work_id,
                "actor": "bayleaf-stager",
                "session_id": "wf01-staging",
                "expected_revision": current["edit_revision"],
                "acceptance_contract": {
                    "effect_verification_required": True,
                    "effect_check_revisions": checks,
                    "verification_profile": "checked",
                    "evidence_obligations": list(checks),
                },
            },
            key="wf01-reserve",
        )
    )["reservation"]
    release = (
        await stager.call(
            "work.read.release", {"release_digest": reserved["release_digest"]}
        )
    )["release"]
    checkout = root / "receipt-manifest"
    checkout.mkdir(mode=0o700)
    git_command(checkout, "init", "-b", "main")
    file = checkout / "handover-receipt.json"
    file.write_text("{}\n")
    git_command(checkout, "add", file.name)
    git_command(checkout, "commit", "-m", "Empty receipt manifest")
    base = git_command(checkout, "rev-parse", "HEAD")
    file.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    git_command(checkout, "add", file.name)
    git_command(
        checkout,
        "commit",
        "-m",
        "Bind private handover receipt\n\nVuoro-Release: " + release["release_digest"],
    )
    commit = git_command(checkout, "rev-parse", "HEAD")
    patch = git_command(checkout, "diff", base, commit) + "\n"
    # Coordination artifact is only this bounded manifest. Source snippets,
    # result body, credentials and private storage addresses never enter it.
    stream = outbox.open_outbox(root / "receipt-outbox.sqlite")
    try:
        harvested = release_trailers.harvest_release_trailers(
            stream, repo_root=checkout, actor="bayleaf-stager"
        )
        records = outbox.list_records(stream)
        if harvested.status != "harvested" or len(records) != 1:
            raise RuntimeError("Release trailer not harvested")
        await stager.call(
            "work.evidence.ingest",
            {"records": [record_to_dict(r) for r in records]},
            key=batch_idempotency_key(records),
        )
    finally:
        stream.close()
    entry, stored = await append(
        stager, run, "receipt-manifest", "sovite-private-artifact-reference", manifest
    )
    proposed = await stager.call(
        "work.effect.propose-bound-v1",
        {
            "run_id": run,
            "item_id": work_id,
            "repository": "https://example.invalid/bayleaf-private-workflow",
            "base_commit": base,
            "title": "Verified private maintenance handover reference",
            "rationale": "Bounded receipt and exact source/result hashes; private bytes stay in Sovite.",
            "unified_diff": patch,
            "idempotency_key": "wf01-manifest-proposal",
            "causal_basis": {
                "expected_revision": release["item_revision"],
                "release_digest": release["release_digest"],
                "reserve_idempotency_key": "wf01-reserve",
                "commit_sha": commit,
                "evidence_tail": {
                    "item_id": entry["item_id"],
                    "chain_seq": entry["chain_seq"],
                    "entry_digest": pg.evidence_entry_digest(stored),
                },
            },
        },
    )
    intent = proposed["intent"]
    binding = {
        k: intent[k] for k in ("intent_id", "revision", "canonical_intent_digest")
    }
    await owner.refuse("work.effect.accept-v1", binding, "effect-verification-required")
    return {
        "intent": intent,
        "binding": binding,
        "patch_digest": sha(patch.encode()),
        "release": release,
        "manifest_commit": commit,
    }


async def execute(endpoint, tokens, root, source, evaluation, setup, holder, steps):
    clients = {name: Caller(endpoint, token, steps) for name, token in tokens.items()}
    stager, owner, worker = clients["stager"], clients["owner"], clients["producer"]
    child = None
    try:
        sprint = (
            await stager.call(
                "work.sprint.create",
                {
                    "name": "Private household maintenance handover",
                    "goal": "Source-backed handover; no repairs performed",
                    "status": "active",
                },
            )
        )["sprint"]["id"]
        item = (
            await owner.call(
                "work.item.create",
                {
                    "sprint_id": sprint,
                    "track_name": "private-workflow",
                    "title": "Prepare a source-backed maintenance handover",
                    "description": "WF-01 C0 evaluation; owner acceptance requires independently verified private output.",
                },
            )
        )["item"]["id"]
        holder["work_id"] = item
        # Activation is a native owner-arbitrated lifecycle command, with its
        # durable producer envelope and the current status CAS token.
        from sprintctl import contracts, outbox
        from sprintctl.application import record_to_dict
        from uuid import uuid4, uuid5, NAMESPACE_URL

        current = (await owner.call("work.read.item", {"item_id": item}))["item"]
        command = contracts.AuthorityCommand(
            event_id=str(uuid4()),
            record_type="item.transition",
            schema_version="1",
            actor="bayleaf-owner",
            authored_at=datetime.now(timezone.utc).isoformat(),
            refs={
                "repo_id": str(uuid5(NAMESPACE_URL, REPO)),
                "aggregate_type": "item",
                "aggregate_uuid": current["aggregate_uuid"],
                "aggregate_id": item,
            },
            payload={"to_status": "active"},
            basis_revision=current["status_revision"],
        )
        stream = outbox.open_outbox(root / "owner-lifecycle-outbox.sqlite")
        try:
            record = outbox.append_authority_command(stream, command)
        finally:
            stream.close()
        arguments = {"record": record_to_dict(record)}
        activation = await owner.client.invoke(
            "work.lifecycle.arbitrate",
            arguments,
            repo_id=REPO,
            idempotency_key=record.event_id,
            basis_revision=record.basis_revision,
        )
        steps.append(
            {
                "operation": "work.lifecycle.arbitrate",
                "arguments": arguments,
                "result": activation,
            }
        )
        if (await owner.call("work.read.item", {"item_id": item}))["item"][
            "status"
        ] != "active":
            raise RuntimeError("Owner lifecycle activation did not commit")
        blocked_run = await register(worker, "blocked")
        task = json.loads(source.read_bytes())
        request = {
            "work_id": item,
            "run_id": blocked_run,
            "grant_id": "maintenance",
            "task": json.dumps(
                {"objective": task["objective"], "sources": task["snippets"]}
            ),
        }
        await worker.refuse(
            "sovite.task.request-v1",
            request,
            "sovite-unavailable",
            key="wf01-blocked-task",
        )
        qualification = evaluation.authorize_development(setup["generation"])
        evaluation.start_worker()
        child = child_process(
            "_producer",
            {
                "endpoint": endpoint,
                "token": tokens["producer"],
                "work_id": item,
                "source": str(source),
                "generation": setup["generation"],
            },
            root,
        )
        line = await asyncio.wait_for(
            asyncio.to_thread(child.stdout.readline), timeout=40
        )
        if not line:
            raise RuntimeError("Producer stopped before persisting its work receipt")
        handshake = json.loads(line)
        child.send_signal(signal.SIGKILL)
        await asyncio.to_thread(child.wait, 10)
        if child.returncode != -signal.SIGKILL:
            raise RuntimeError("Producer was not actually interrupted")
        child = None
        receipt = handshake["link"]["receipt"]
        status_args = {
            "work_id": item,
            "run_id": handshake["run_id"],
            "grant_id": "maintenance",
            "receipt": receipt,
            "task_binding": handshake["link"]["task_binding"],
        }
        await worker.call("sovite.task.status-v1", status_args)
        # Advisory progress only, never replay authority or a success receipt.
        (root / "workflow-progress.json").write_text(
            json.dumps({"phase": "reconciling-private-task", "receipt": receipt})
        )
        # Execution is outside the interrupted producer process. Reconcile by
        # receipt; never resubmit the model task to recover from that interruption.
        with evaluation.client() as private:
            for _ in range(1200):
                jobs = private.get("/api/jobs").raise_for_status().json()["items"]
                job = next(j for j in jobs if j["receipt"] == receipt)
                if job["status"] in ("completed", "failed", "blocked", "cancelled"):
                    break
                await asyncio.sleep(0.5)
            if job["status"] != "completed":
                raise RuntimeError("Real private execution did not complete")
            path = "/api/jobs/" + job["id"] + "/artifact"
            raw = private.get(path).raise_for_status().content
            denied = private.get(
                path, headers={"Authorization": "Bearer " + evaluation.caller_token}
            )
            if denied.status_code != 403:
                raise RuntimeError("Task caller read private result")
        if (await worker.call("sovite.task.status-v1", status_args))[
            "status"
        ] != "available":
            raise RuntimeError(
                "Completed private receipt not available through the catalog"
            )
        link = handshake["link"]
        manifest = {
            "schema": "sovite-private-artifact-manifest/v1",
            "work_id": item,
            "run_id": link["run_id"],
            "receipt": receipt,
            "task_binding": link["task_binding"],
            "source_digest": link["source_digest"],
            "artifact_digest": sha(raw),
            "generation": setup["generation"],
            "profile": PROFILE,
            "profile_revision": revision(),
        }
        staged = await stage(stager, owner, root, item, manifest)
        value = {
            "endpoint": endpoint,
            "token": tokens["verifier"],
            "work_id": item,
            "predecessor_run": handshake["run_id"],
            "sovite_origin": evaluation.origin,
            "artifact_reader": evaluation.settings.operator_token,
            "source": str(source),
            "manifest": manifest,
            "intent": staged["intent"],
            "release_digest": staged["release"]["release_digest"],
            "patch_digest": staged["patch_digest"],
            "private_output": str(root / "verified-private-artifact.json"),
        }
        # First show that inaccessible source bytes cannot produce verification.
        child = denied_process = child_process(
            "_verifier", {**value, "deny_source": True}, root
        )
        await asyncio.wait_for(
            asyncio.to_thread(denied_process.stdout.readline), timeout=40
        )
        await asyncio.to_thread(denied_process.wait, 20)
        if denied_process.returncode == 0:
            raise RuntimeError("Missing source access produced a verification")
        child = child_process("_verifier", value, root)
        line = await asyncio.wait_for(
            asyncio.to_thread(child.stdout.readline), timeout=60
        )
        await asyncio.to_thread(child.wait, 20)
        if child.returncode or not line:
            raise RuntimeError("Independent verifier rejected the real private result")
        verified = json.loads(line)
        child = None
        if verified["manifest"] != manifest:
            raise RuntimeError("Verifier changed the proposed private reference")
        # Superseded producer cannot turn recovery into another task attempt.
        late = {**request, "run_id": handshake["run_id"]}
        await worker.refuse(
            "sovite.task.request-v1", late, "sovite-unavailable", key="wf01-late-task"
        )
        accept = staged["binding"]
        await worker.refuse("work.effect.accept-v1", accept, "authority-required")
        await clients["verifier"].refuse(
            "work.effect.accept-v1", accept, "authority-required"
        )
        await owner.refuse(
            "work.effect.accept-v1",
            {**accept, "revision": accept["revision"] + 1},
            "effect-revision-mismatch",
        )
        owner_run = await owner_verification(owner, verified, value, evaluation)
        # An independent verifier's receipt cannot impersonate the acceptor.
        await owner.refuse(
            "work.effect.accept-v1",
            {
                **accept,
                "verification_ref": {
                    "run_id": verified["run_id"],
                    "item_id": "private-verification",
                },
            },
            "effect-verification-refused",
        )
        for item_id in ("wrong-manifest-digest", "missing-profile-check"):
            await owner.refuse(
                "work.effect.accept-v1",
                {
                    **accept,
                    "verification_ref": {"run_id": owner_run, "item_id": item_id},
                },
                "effect-verification-refused",
            )
            if (await owner.call("work.read.item-decisions", {"item_id": item}))[
                "decisions"
            ]:
                raise RuntimeError("Rejected evidence settled work")
        accepted = (
            await owner.call(
                "work.effect.accept-v1",
                {
                    **accept,
                    "verification_ref": {
                        "run_id": owner_run,
                        "item_id": "handover-proof",
                    },
                },
            )
        )["intent"]
        if (await owner.call("work.read.item", {"item_id": item}))["item"][
            "status"
        ] == "done":
            raise RuntimeError("Artifact acceptance unexpectedly settled work")
        await worker.refuse(
            "work.decision.record",
            {
                "item_id": item,
                "kind": "accept",
                "rationale": "unauthorized self acceptance",
                "evidence_digests": [
                    verified["observation_digest"].removeprefix("sha256:")
                ],
                "release_digest": staged["release"]["release_digest"],
            },
            "authority-required",
            key="wf01-self-accept",
        )
        decision = await owner.call(
            "work.decision.record",
            {
                "item_id": item,
                "kind": "accept",
                "rationale": "maintenance-handover-v1: independent verifier read exact private bytes, checked all M1–M3 facts, uncertainty and proposed actions; protected manifest accepted. No repairs performed.",
                "evidence_digests": [
                    verified["observation_digest"].removeprefix("sha256:")
                ],
                "release_digest": staged["release"]["release_digest"],
            },
            key="wf01-accept",
        )
        repeated = await owner.call(
            "work.decision.record",
            {
                "item_id": item,
                "kind": "accept",
                "rationale": "maintenance-handover-v1: independent verifier read exact private bytes, checked all M1–M3 facts, uncertainty and proposed actions; protected manifest accepted. No repairs performed.",
                "evidence_digests": [
                    verified["observation_digest"].removeprefix("sha256:")
                ],
                "release_digest": staged["release"]["release_digest"],
            },
            key="wf01-accept",
        )
        if (
            repeated["decision"] != decision["decision"]
            or repeated.get("replayed") is not True
        ):
            raise RuntimeError("Owner decision retry changed the result")
        decisions = (await owner.call("work.read.item-decisions", {"item_id": item}))[
            "decisions"
        ]
        if (
            decision["status"] != "done"
            or len(decisions) != 1
            or decisions[0]["actor"] != "bayleaf-owner"
        ):
            raise RuntimeError("Owner acceptance was not persisted exactly once")
        # Check every captured coordination message against source/result bodies.
        serialized = json.dumps(steps)
        if json.loads(raw)["answer"] in serialized or any(
            s["text"] in serialized for s in task["snippets"]
        ):
            raise RuntimeError("Private bytes leaked into coordination records")
        return {
            "work_id": item,
            "sprint_id": sprint,
            "producer_run": handshake["run_id"],
            "verifier_run": verified["run_id"],
            "acceptor_run": owner_run,
            "receipt": receipt,
            "manifest": manifest,
            "release_digest": staged["release"]["release_digest"],
            "manifest_commit": staged["manifest_commit"],
            "intent_id": accepted["intent_id"],
            "verification_evidence_digest": verified["observation_digest"],
            "decision": decision["decision"],
            "status": decision["status"],
            "qualification_id": qualification,
            "model": evaluation.model["model"],
            "model_digest": evaluation.model["digest"],
            "model_attempts": job["attempts"],
            "checks": {
                "unqualified_dispatch_refused": True,
                "task_retry_same_receipt": True,
                "native_catalog_receipt_status_read": True,
                "producer_process_interrupted": True,
                "durable_predecessor_context_read": True,
                "private_caller_read_refused": True,
                "missing_source_verification_refused": True,
                "wrong_private_digest_refused": True,
                "wrong_manifest_digest_refused": True,
                "missing_profile_check_refused": True,
                "superseded_source_request_refused": True,
                "foreign_verification_identity_refused": True,
                "owner_reread_private_bytes": True,
                "producer_and_verifier_self_acceptance_refused": True,
                "acceptance_retry_one_decision": True,
                "no_source_or_private_result_body_in_coordination": True,
            },
        }
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.wait, 10)
        for caller in clients.values():
            await caller.client.aclose()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["_producer"], ["_verifier"]):
        value = json.load(sys.stdin)
        try:
            asyncio.run(producer(value) if argv[0] == "_producer" else verifier(value))
            return 0
        except Exception as error:
            print(type(error).__name__, file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(prog="vuoro private-workflow")
    parser.add_argument("--model-endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        print("Receipt exists or is unavailable; workflow not started", file=sys.stderr)
        return 1
    report = {
        "schema": "vuoro-private-workflow/v1",
        "status": "incomplete",
        "profile": PROFILE,
        "scope": "Actual local C0 fixture model execution and released Work/Evidence owner acceptance; no hosted or production qualification.",
        "versions": {},
        "steps": [],
    }
    started = time.time()
    code = 1
    fixture_state = {}
    interrupted = None
    active_task = None
    active_loop = None
    original_handlers = {}

    def interrupt(signum, _frame):
        nonlocal interrupted
        if interrupted is None:
            interrupted = signum
            if active_task is not None and active_loop is not None:
                active_loop.call_soon_threadsafe(active_task.cancel)

    async def consume(*values):
        nonlocal active_task, active_loop
        active_task = asyncio.current_task()
        active_loop = asyncio.get_running_loop()
        try:
            if interrupted is not None:
                raise asyncio.CancelledError()
            return await execute(*values)
        finally:
            active_task = active_loop = None

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            original_handlers[signum] = signal.signal(signum, interrupt)
        from sovite.local_evaluation import local_model, local_evaluation

        report["versions"] = {name: version(name) for name in VERSIONS}
        if any(report["versions"][key] != value for key, value in VERSIONS.items()):
            raise ValueError("Install the exact released workflow dependencies")
        source = args.source.resolve()
        raw = source.read_bytes()
        input_data = json.loads(raw)
        if (
            input_data.get("classification") != "C0"
            or input_data.get("synthetic") is not True
        ):
            raise ValueError(
                "This owned assessment accepts only its public synthetic source fixture"
            )
        if sha(raw) != SOURCE_DIGEST:
            raise VerificationRefused(
                "Source differs from the pinned maintenance fixture"
            )
        model = local_model(args.model_endpoint, args.model, args.model_digest)
        root = args.state_dir.resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        holder = {}
        with local_evaluation(root / "sovite", model) as evaluation:
            setup = evaluation.setup(instructions=instructions())
            report["setup"] = setup
            with fixture(
                root,
                args.pg_bin,
                state=fixture_state,
                configure=configure_capability(evaluation, root, holder),
            ) as (endpoint, tokens):
                report["result"] = asyncio.run(
                    consume(
                        endpoint,
                        tokens,
                        root,
                        source,
                        evaluation,
                        setup,
                        holder,
                        report["steps"],
                    )
                )
        if fixture_state.get("cleanup") != "complete":
            raise RuntimeError("Owned Work service cleanup incomplete")
        if interrupted is not None:
            raise asyncio.CancelledError()
        report["status"] = "passed"
        code = 0
    except asyncio.CancelledError:
        report["failure_type"] = "WorkflowInterrupted"
        report["interruption"] = (
            signal.Signals(interrupted).name if interrupted else "cancelled"
        )
    except Exception as error:
        report["failure_type"] = type(error).__name__
        print("Private workflow incomplete: " + type(error).__name__, file=sys.stderr)
    finally:
        for signum, previous in original_handlers.items():
            signal.signal(signum, previous)
        report["elapsed_seconds"] = round(time.time() - started, 3)
        report["fixture_cleanup"] = fixture_state.get("cleanup", "not-started")
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
