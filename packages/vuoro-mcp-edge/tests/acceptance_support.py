"""Synthetic owner records; golden digest from owner reconstruction qualification."""

import hashlib

import json

from operator_projection import reconstruction as r

DIGEST = "54d0286afc0a99c2a8737a14c2a4feb174458c284c6bfe257db68970d1e2fddb"

def capture():
    intent = dict(intent_id="effect_demo", revision=1, item_id=42, run_id="run_demo",
        repository="bayleafwalker/demo", base_commit="a" * 40, title="A bounded change",
        rationale="Review the exact bytes", unified_diff="--- a/demo\n+++ b/demo\n@@ -1 +1 @@\n-old\n+new\n",
        canonical_intent_digest=DIGEST, state="applied",
        acceptance=dict(intent_id="effect_demo", intent_revision=1, canonical_intent_digest=DIGEST,
            acceptor_principal="issuer:reviewer:0", acceptor_policy_version=None, accepted_at="2026-10-06T12:00:00Z"),
        application=dict(applier_principal="issuer:reconciler:0", commit_sha="b" * 40,
            pr_url="https://github.com/bayleafwalker/demo/pull/1", applied_at="2026-10-06T12:01:00Z"))
    def entry(value, arguments):
        return dict(status="observed", value=value, arguments=arguments, observed_at="2026-10-06T12:02:00Z")
    return dict(schema=r.CAPTURE_SCHEMA, repo_id="demo", intent_id="effect_demo", results={
        r.EFFECT: entry(dict(repo_id="demo", intent=intent), dict(intent_id="effect_demo")),
        r.RELEASE: entry(dict(repo_id="demo", release=dict(work_item_id=42, release_digest="c" * 64,
            item_revision="item:demo@description:v1@revise:0", acceptance_contract={"review_required": True}), commits=[]), dict(item_id=42)),
        r.DECISIONS: entry(dict(repo_id="demo", item_id=42, decisions=[dict(id=5, kind="accept",
            release_digest="c" * 64, evidence_digests=["d" * 64])]), dict(item_id=42)),
        r.LEASES: entry(dict(repo_id="demo", item_id=42, current_lease=None,
            leases=[dict(lease_id="lease_demo", run_id="run_demo")], outcome_reports=[],
            verification={"ready": True}, evaluated_at="2026-10-06T12:02:00Z"), dict(item_id=42))})

def intent(doc):
    return doc["results"][r.EFFECT]["value"]["intent"]

def protected_capture():
    doc = capture()
    row = intent(doc)
    row["release_digest"] = "c" * 64
    receipt = dict(schema="sprintctl-protected-artifact-verification/v1",
        intent_id=row["intent_id"], intent_revision=1, canonical_intent_digest=DIGEST,
        release_digest="c" * 64,
        artifact=dict(domain="utf8-unified-diff/v1", digest="sha256:" +
            hashlib.sha256(row["unified_diff"].encode()).hexdigest()),
        checks=[dict(name="clean-patch-application", revision="sha256:" + "e" * 64, status="passed")])
    row["acceptance"]["verification"] = dict(run_id="run_verifier", item_id="proof_demo",
        evidence_digest="sha256:" + hashlib.sha256(json.dumps(receipt, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
        entry_digest="sha256:" + "f" * 64, verifier_principal="issuer:reviewer:0",
        workspace_id="native:demo:0", client_id=None, grant_id=None, receipt=receipt)
    doc["results"][r.RELEASE]["value"]["release"]["acceptance_contract"]["effect_verification_required"] = True
    return doc

def records():
    doc = protected_capture()
    values = {op: row["value"] for op, row in doc["results"].items()}
    for value in values.values():
        value["repo_id"] = "repo-a"
    return values
