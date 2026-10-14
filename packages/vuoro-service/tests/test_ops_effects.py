"""vuoro-ops uncertain-effects and provider-outcome-check.

The journal fixture uses the reconciler's own v1 DDL so the read-only reader
is checked against the real catalog; rows are written directly because the
diagnostics read DATA and never need a protected effect.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from vuoro_service.ops.effects import (
    classify,
    file_attempt_source,
    provider_outcome_check,
    read_journal,
    uncertain_effects,
)

journal_module = pytest.importorskip("vuoro_reconciler.execution_journal")

INTENT_A, INTENT_B, INTENT_C = ("intent_" + c * 26 for c in "ABC")
COMMIT = "1" * 40


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def make_journal(directory: Path, effects: list[dict]) -> Path:
    directory.mkdir(mode=0o700)
    (directory / "journal.lock").touch(mode=0o600)
    connection = sqlite3.connect(directory / "journal.sqlite3", isolation_level=None)
    for sql in (*journal_module._TABLE_SQL.values(), *journal_module._TRIGGER_SQL.values()):
        connection.execute(sql)
    connection.execute("PRAGMA user_version=1")
    connection.execute("INSERT INTO journal_metadata VALUES (1,1,'host',?,?)", (b"{}", _sha(b"{}")))
    for effect in effects:
        key = hashlib.sha256(effect["intent_id"].encode()).hexdigest()
        binding = _canonical({"schema": "vuoro-protected-journal-binding/v1", "namespace": "agentops",
                              "intent_id": effect["intent_id"], "intent_revision": effect["revision"],
                              "publication_target": {"repository": "bayleafwalker/vuoro"}})
        connection.execute("INSERT INTO effect_bindings VALUES (?,?,?,?,?)", (key, binding, _sha(binding), b"{}", _sha(b"{}")))
        events = [("BOUND", binding)] + [(kind, _canonical(payload)) for kind, payload in effect.get("events", [])]
        for seq, (kind, raw) in enumerate(events):
            connection.execute("INSERT INTO journal_events VALUES (?,?,?,?,?)", (key, seq, kind, raw, _sha(raw)))
        for operation in effect.get("spent", []):
            connection.execute("INSERT INTO publication_spent VALUES (?,?,?,?)", (key, operation, b"{}", _sha(b"{}")))
    connection.close()
    return directory


def attempt(letter: str, state: str, *, operation="open_pull_request", intent=INTENT_A, report=None,
            branch="vuoro-effect/x", repository="bayleafwalker/vuoro") -> dict:
    attempt_id = "attempt_" + letter * 26
    events = [{"event_kind": "attempt_authorization_accepted", "payload": {}}]
    if state == "redeemed":
        events.append({"event_kind": "invocation_authorization_redeemed", "payload": {}})
    if state == "sealed_unused":
        events.append({"event_kind": "attempt_closed_without_redemption", "payload": {}})
    if report:
        events.append({"event_kind": "application_report_received", "payload": report})
    target = {"operation": operation, "branch": branch, "commit_sha": COMMIT, "repository": repository}
    if operation == "open_pull_request":
        target["base_branch"] = "main"
    return {"authorization": {"attempt_id": attempt_id, "intent_id": intent, "intent_revision": 1,
                              "work_item_id": 42, "principal_id": "github:1:0", "target": target},
            "authorization_digest": "0" * 64, "state": state, "events": events}


def source_for(*attempts):
    by_id = {a["authorization"]["attempt_id"]: a for a in attempts}
    return lambda attempt_id: (200, by_id[attempt_id]) if attempt_id in by_id else (404, {"error": {"code": "not-found"}})


@pytest.fixture
def journal(tmp_path):
    return make_journal(tmp_path / "journal", [
        {"intent_id": INTENT_A, "revision": 1, "spent": ["branch-create", "pr-create"],
         "events": [("LOCAL_READY", {"commit_oid": COMMIT, "artifact_relative_path": "artifacts/a/original.bundle",
                                     "artifact_sha256": "sha256:" + "0" * 64}),
                    ("BRANCH_TRANSMISSION_POSSIBLE", {}), ("PR_TRANSMISSION_POSSIBLE", {})]},
        {"intent_id": INTENT_B, "revision": 1, "spent": ["branch-create"],
         "events": [("LOCAL_READY", {"commit_oid": COMMIT}), ("BRANCH_TRANSMISSION_POSSIBLE", {})]},
        {"intent_id": INTENT_C, "revision": 3, "spent": ["branch-create"],
         "events": [("LOCAL_READY", {"commit_oid": COMMIT}), ("BRANCH_TRANSMISSION_POSSIBLE", {})]},
    ])


def test_journal_is_read_without_modification(journal):
    before = {path.name: path.read_bytes() for path in journal.iterdir()}
    effects = read_journal(journal)
    assert {effect.status for effect in effects.values()} == {"UNSETTLED"}
    assert {path.name: path.read_bytes() for path in journal.iterdir()} == before


def test_tampered_journal_is_an_unknown(journal):
    connection = sqlite3.connect(journal / "journal.sqlite3")
    connection.execute("DROP TRIGGER journal_events_no_update")
    connection.execute("UPDATE journal_events SET payload_utf8=X'7B7D20' WHERE seq=1")
    connection.commit()
    connection.close()
    result = uncertain_effects(["attempt_" + "A" * 26], source_for(attempt("A", "redeemed")), journal_dir=journal)
    assert "execution-journal-unreadable" in result.unknowns
    assert result.result["journal"]["state"] == "unreadable"


def test_uncertain_effects_classifies_and_links_work(journal):
    attempts = [
        attempt("A", "redeemed"),                                    # PR redeemed, no report
        attempt("B", "redeemed", intent=INTENT_B, report={"commit_sha": COMMIT, "pr_url": "https://x/pull/1"}),
        attempt("D", "accepted", intent="intent_" + "D" * 26),       # unredeemed, nothing in journal
        attempt("E", "accepted", intent=INTENT_B, operation="push_branch"),  # unredeemed but journal spent
        attempt("F", "sealed_unused", intent="intent_" + "F" * 26),
    ]
    ids = [a["authorization"]["attempt_id"] for a in attempts] + ["attempt_" + "G" * 26]
    result = uncertain_effects(ids, source_for(*attempts), journal_dir=journal,
                               work_refs=[{"kind": "incident", "id": "i-1"}])
    by_id = {entry["attempt_id"]: entry for entry in result.result["attempts"]}
    assert by_id["attempt_" + "A" * 26]["classification"] == "provider-outcome-needed"
    assert by_id["attempt_" + "A" * 26]["journal"]["spent"] == ["branch-create", "pr-create"]
    assert by_id["attempt_" + "B" * 26]["classification"] == "reported"
    assert by_id["attempt_" + "D" * 26]["classification"] == "safe-to-seal"
    assert by_id["attempt_" + "E" * 26]["classification"] == "provider-outcome-needed"
    assert "journal-transmission-marker-without-redemption" in by_id["attempt_" + "E" * 26]["anomalies"]
    assert by_id["attempt_" + "F" * 26]["classification"] == "closed-unused"
    assert result.result["redeemed_without_report"] == ["attempt_" + "A" * 26]
    assert result.result["unreadable_attempts"][0]["status"] == 404
    unlinked = result.result["unsettled_journal_effects_without_supplied_attempt"]
    assert [effect["intent_id"] for effect in unlinked] == [INTENT_C]
    assert {"kind": "work-item", "id": "42"} in result.work_refs
    assert {"kind": "effect-intent", "id": f"{INTENT_A}@1"} in result.work_refs
    assert {"kind": "incident", "id": "i-1"} in result.work_refs
    assert result.status == "attention"
    assert any(u.startswith("owner-has-no-attempt-list-operation") for u in result.unknowns)


def test_without_a_journal_an_unredeemed_attempt_is_never_called_safe():
    entry = classify(attempt("D", "accepted"), None)
    assert entry["classification"] == "provider-outcome-needed"


def test_invalid_attempt_ids_are_refused(journal):
    result = uncertain_effects(["attempt_bad"], source_for(), journal_dir=journal)
    assert result.status == "error" and result.result["invalid_attempt_ids"] == ["attempt_bad"]


def test_attempts_export_file_source(tmp_path):
    path = tmp_path / "attempts.jsonl"
    path.write_text("\n".join(json.dumps(a) for a in (attempt("A", "redeemed"), attempt("B", "accepted"))))
    status, body = file_attempt_source(path)("attempt_" + "B" * 26)
    assert status == 200 and body["state"] == "accepted"
    assert file_attempt_source(path)("attempt_" + "Z" * 26)[0] == 404


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    _git("checkout", "-q", "-b", "vuoro-effect/x", cwd=work)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "effect", cwd=work)
    head = _git("rev-parse", "HEAD", cwd=work)
    bare = tmp_path / "remote.git"
    _git("clone", "-q", "--bare", str(work), str(bare))
    _git("bundle", "create", str(tmp_path / "original.bundle"), "vuoro-effect/x", cwd=work)
    return bare, head, tmp_path / "original.bundle"


def test_push_outcome_is_read_from_the_remote_without_writing(remote, tmp_path):
    bare, head, _ = remote
    refs_before = _git("for-each-ref", cwd=bare)
    applied = attempt("A", "redeemed", operation="push_branch")
    applied["authorization"]["target"]["commit_sha"] = head
    result = provider_outcome_check("attempt_" + "A" * 26, source_for(applied), journal_dir=None, remote=str(bare))
    assert result.result["provider_outcome"] == "applied"
    assert result.result["next_step"].startswith("applied at the provider")
    assert _git("for-each-ref", cwd=bare) == refs_before

    absent = attempt("A", "redeemed", operation="push_branch", branch="vuoro-effect/missing")
    result = provider_outcome_check("attempt_" + "A" * 26, source_for(absent), journal_dir=None, remote=str(bare))
    assert result.result["provider_outcome"] == "not-observed"
    assert "replay never re-grants" in result.result["next_step"]

    unredeemed = attempt("A", "accepted", operation="push_branch", branch="vuoro-effect/missing")
    result = provider_outcome_check("attempt_" + "A" * 26, source_for(unredeemed), journal_dir=None, remote=str(bare))
    assert "attempt-seal-unused-v1" in result.result["next_step"]

    conflict = attempt("A", "redeemed", operation="push_branch")
    result = provider_outcome_check("attempt_" + "A" * 26, source_for(conflict), journal_dir=None, remote=str(bare))
    assert result.result["provider_outcome"] == "conflict"


def test_pull_request_outcome_and_signed_artifact(remote, tmp_path):
    bare, head, bundle = remote
    root = tmp_path / "artifacts-root"
    (root / "artifacts" / "a").mkdir(parents=True)
    (root / "artifacts" / "a" / "original.bundle").write_bytes(bundle.read_bytes())
    journal = make_journal(tmp_path / "journal2", [{
        "intent_id": INTENT_A, "revision": 1, "spent": ["branch-create", "pr-create"],
        "events": [("LOCAL_READY", {"commit_oid": head, "artifact_relative_path": "artifacts/a/original.bundle",
                                    "artifact_sha256": _sha(bundle.read_bytes())}),
                   ("BRANCH_TRANSMISSION_POSSIBLE", {}), ("PR_TRANSMISSION_POSSIBLE", {})]}])
    redeemed = attempt("A", "redeemed")
    redeemed["authorization"]["target"]["commit_sha"] = head
    requests = []

    def http_get(url, headers):
        requests.append(url)
        return 200, [{"number": 7, "html_url": "https://github.com/bayleafwalker/vuoro/pull/7", "state": "open",
                      "merged_at": None, "head": {"sha": head}, "base": {"ref": "main"}}]

    result = provider_outcome_check("attempt_" + "A" * 26, source_for(redeemed), journal_dir=journal,
                                    remote=str(bare), artifact_root=root, http_get=http_get)
    body = result.result
    assert body["provider_outcome"] == "applied"
    assert body["observed"]["pull_request"]["number"] == 7
    assert "attempt-report-applied-v1" in body["next_step"]
    assert body["signed_artifact"]["state"] == "verified"
    assert requests == ["https://api.github.com/repos/bayleafwalker/vuoro/pulls?"
                        "head=bayleafwalker%3Avuoro-effect%2Fx&state=all&per_page=10"]
    assert result.status == "attention"  # applied, but the applier has not reported yet

    reported = attempt("A", "redeemed", report={"commit_sha": head, "pr_url": "https://github.com/bayleafwalker/vuoro/pull/7"})
    reported["authorization"]["target"]["commit_sha"] = head
    result = provider_outcome_check("attempt_" + "A" * 26, source_for(reported), journal_dir=journal,
                                    remote=str(bare), http_get=http_get)
    assert result.status == "ok"

    result = provider_outcome_check("attempt_" + "A" * 26, source_for(redeemed), journal_dir=journal,
                                    remote=str(bare), http_get=lambda url, headers: (200, []))
    assert result.result["provider_outcome"] == "partially-applied"
    result = provider_outcome_check("attempt_" + "A" * 26, source_for(redeemed), journal_dir=journal,
                                    remote=str(bare), http_get=lambda url, headers: (502, None))
    assert result.result["provider_outcome"] == "unknown" and result.status == "error"
