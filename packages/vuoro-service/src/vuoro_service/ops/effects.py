"""Uncertain effect attempts: owner facts joined to the local execution journal.

Sources, all read-only:

* ``work.effect.attempt-get-v1`` -- the owner's verified attempt snapshot.
  There is no owner list operation, so the attempt ids come from the caller
  (the reconciler's durable dispatch tracking, or an incident record).
* the reconciler's installed execution journal, opened ``mode=ro``.  It is
  local publication history (DATA), never an execution grant.
* the provider: ``git ls-remote`` for the branch head and the GitHub REST
  pull-request listing.  Nothing is fetched into, pushed to or opened on the
  provider, and no attempt is redeemed, sealed or reported.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .result import DiagnosticResult
from .runtime import tls_context

ATTEMPT_ID = re.compile(r"^attempt_[0-9A-HJKMNP-TV-Z]{26}$")
JOURNAL_DB = "journal.sqlite3"
JOURNAL_TABLES = {"journal_metadata", "effect_bindings", "journal_events", "publication_spent", "applied_outcomes"}
JOURNAL_OPERATION = {"push_branch": "branch-create", "open_pull_request": "pr-create"}
TRANSMISSION_EVENT = {"branch-create": "BRANCH_TRANSMISSION_POSSIBLE", "pr-create": "PR_TRANSMISSION_POSSIBLE"}
OBSERVED_EVENT = {"branch-create": "BRANCH_OBSERVED", "pr-create": "PR_OBSERVED"}
TERMINAL = {"APPLIED", "REFUSED", "CONFLICT"}

# attempt_id -> (http status, attempt-get result or error body)
AttemptSource = Callable[[str], tuple[int, Any]]


class JournalUnreadable(ValueError):
    """The journal is absent, not v1, or fails its own row digests."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class JournalEffect:
    effect_key: str
    intent_id: str | None
    intent_revision: Any
    namespace: str | None
    publication_target: dict[str, Any] | None
    events: tuple[str, ...]
    spent: tuple[str, ...]
    status: str
    local_ready: dict[str, Any] | None
    observations: dict[str, dict[str, Any]]
    outcome: dict[str, Any] | None

    def summary(self) -> dict[str, Any]:
        return {
            "effect_key": self.effect_key,
            "intent_id": self.intent_id,
            "intent_revision": self.intent_revision,
            "status": self.status,
            "events": list(self.events),
            "spent": list(self.spent),
            "local_commit_oid": (self.local_ready or {}).get("commit_oid"),
            "artifact_relative_path": (self.local_ready or {}).get("artifact_relative_path"),
            "observations": self.observations,
            "outcome": self.outcome,
        }


def read_journal(directory: Path) -> dict[str, JournalEffect]:
    """Read every journal effect without taking a write lock or repairing."""
    database = directory / JOURNAL_DB
    if not database.is_file() or database.is_symlink():
        raise JournalUnreadable("journal database is absent")
    uri = "file:" + urllib.parse.quote(str(database.resolve())) + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as error:
        raise JournalUnreadable("journal database cannot be opened read-only") from error
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise JournalUnreadable("journal is not component version 1")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != JOURNAL_TABLES:
            raise JournalUnreadable("journal catalog does not match version 1")
        connection.execute("BEGIN")  # one consistent snapshot under SQLite's shared lock
        bindings = connection.execute(
            "SELECT effect_key, binding_utf8, binding_sha256 FROM effect_bindings ORDER BY effect_key"
        ).fetchall()
        events: dict[str, list[tuple[int, str, bytes]]] = {}
        for key, seq, kind, payload, digest in connection.execute(
            "SELECT effect_key, seq, event_type, payload_utf8, payload_sha256 FROM journal_events ORDER BY effect_key, seq"
        ):
            if _sha(payload) != digest:
                raise JournalUnreadable("journal event digest mismatch")
            events.setdefault(key, []).append((seq, kind, payload))
        spent: dict[str, list[str]] = {}
        for key, operation, request, digest in connection.execute(
            "SELECT effect_key, operation, request_utf8, request_sha256 FROM publication_spent ORDER BY effect_key, operation"
        ):
            if _sha(request) != digest:
                raise JournalUnreadable("journal spent-marker digest mismatch")
            spent.setdefault(key, []).append(operation)
        outcomes = {
            key: (raw, digest)
            for key, raw, digest in connection.execute(
                "SELECT effect_key, outcome_utf8, outcome_sha256 FROM applied_outcomes"
            )
        }
        connection.execute("COMMIT")
    except sqlite3.Error as error:
        raise JournalUnreadable("journal read failed") from error
    finally:
        connection.close()

    effects: dict[str, JournalEffect] = {}
    for key, binding_raw, binding_digest in bindings:
        if _sha(binding_raw) != binding_digest:
            raise JournalUnreadable("journal binding digest mismatch")
        binding = json.loads(binding_raw)
        chain = events.get(key, [])
        if [seq for seq, _, _ in chain] != list(range(len(chain))) or not chain or chain[0][1] != "BOUND":
            raise JournalUnreadable("journal event chain is not contiguous from BOUND")
        payloads = {kind: json.loads(payload) for _, kind, payload in chain}
        kinds = tuple(kind for _, kind, _ in chain)
        last = kinds[-1]
        status = last if last in TERMINAL else ("UNSETTLED" if spent.get(key) else last)
        observations = {}
        for kind in ("BRANCH_OBSERVED", "PR_OBSERVED"):
            if kind in payloads:
                observation = payloads[kind]
                observations[kind] = {
                    field: observation.get(field)
                    for field in ("branch", "commit_oid", "pr_number", "pr_url", "state")
                    if field in observation
                }
        outcome = None
        if key in outcomes:
            raw, digest = outcomes[key]
            if _sha(raw) != digest:
                raise JournalUnreadable("journal outcome digest mismatch")
            value = json.loads(raw)
            outcome = {field: value.get(field) for field in ("commit_oid", "pr_number", "pr_url")}
        effects[key] = JournalEffect(
            effect_key=key,
            intent_id=binding.get("intent_id"),
            intent_revision=binding.get("intent_revision"),
            namespace=binding.get("namespace"),
            publication_target=binding.get("publication_target"),
            events=kinds,
            spent=tuple(spent.get(key, ())),
            status=status,
            local_ready=payloads.get("LOCAL_READY"),
            observations=observations,
            outcome=outcome,
        )
    return effects


def _journal_for(attempt: Mapping[str, Any], journal: Mapping[str, JournalEffect]) -> JournalEffect | None:
    authorization = attempt.get("authorization") or {}
    matches = [
        effect for effect in journal.values()
        if effect.intent_id == authorization.get("intent_id")
        and str(effect.intent_revision) == str(authorization.get("intent_revision"))
    ]
    return matches[0] if len(matches) == 1 else None


def _attempt_facts(attempt: Mapping[str, Any]) -> dict[str, Any]:
    authorization = attempt.get("authorization") or {}
    target = authorization.get("target") or {}
    events = attempt.get("events") or []
    report = next((event for event in events if event.get("event_kind") == "application_report_received"), None)
    return {
        "attempt_id": authorization.get("attempt_id"),
        "state": attempt.get("state"),
        "intent_id": authorization.get("intent_id"),
        "intent_revision": authorization.get("intent_revision"),
        "work_item_id": authorization.get("work_item_id"),
        "provider_operation": target.get("operation"),
        "repository": target.get("repository"),
        "branch": target.get("branch"),
        "commit_sha": target.get("commit_sha"),
        "base_branch": target.get("base_branch"),
        "principal_id": authorization.get("principal_id"),
        "events": [event.get("event_kind") for event in events],
        "report": None if report is None else report.get("payload"),
    }


def classify(attempt: Mapping[str, Any], journal: Mapping[str, JournalEffect] | None) -> dict[str, Any]:
    """Classify one attempt from owner facts and (optional) journal markers.

    ``reported``: redeemed and an application report is recorded.
    ``safe-to-seal``: never redeemed and the journal shows no transmission
    marker for this intent and provider operation.
    ``provider-outcome-needed``: everything that only the provider can settle,
    including an unredeemed attempt without journal evidence.
    ``closed-unused``: already sealed without redemption; nothing to do.
    """
    facts = _attempt_facts(attempt)
    linked = _journal_for(attempt, journal) if journal is not None else None
    operation = JOURNAL_OPERATION.get(facts["provider_operation"] or "")
    marker = bool(linked and operation in linked.spent)
    anomalies: list[str] = []
    reasons: list[str] = []
    state = facts["state"]
    if state == "sealed_unused":
        classification = "closed-unused"
        reasons.append("authorization sealed without redemption")
        if marker:
            anomalies.append("journal-transmission-marker-on-sealed-attempt")
    elif state == "redeemed" and facts["report"] is not None:
        classification = "reported"
        reasons.append("redeemed and application report recorded (an applier claim, not provider proof)")
    elif state == "redeemed":
        classification = "provider-outcome-needed"
        reasons.append("redeemed without an application report; replay never re-grants dispatch")
        if facts["provider_operation"] == "push_branch":
            reasons.append("push authorizations take no application report; only the provider settles them")
    elif state == "accepted":
        if journal is None:
            classification = "provider-outcome-needed"
            reasons.append("unredeemed, but no journal was supplied to exclude an in-flight transmission")
        elif marker:
            classification = "provider-outcome-needed"
            anomalies.append("journal-transmission-marker-without-redemption")
            reasons.append("journal records a possible transmission for an unredeemed authorization")
        else:
            classification = "safe-to-seal"
            reasons.append("never redeemed and no journal transmission marker")
    else:
        classification = "provider-outcome-needed"
        anomalies.append(f"unknown-attempt-state:{state}")
    if linked is not None and linked.local_ready and facts["commit_sha"]:
        if linked.local_ready.get("commit_oid") != facts["commit_sha"]:
            anomalies.append("journal-local-commit-differs-from-authorized-commit")
    return {
        **facts,
        "classification": classification,
        "reasons": reasons,
        "anomalies": anomalies,
        "journal": None if linked is None else linked.summary(),
    }


def served_attempt_source(reader: Any, repo_id: str | None) -> AttemptSource:
    def get(attempt_id: str) -> tuple[int, Any]:
        status, body = reader.read("work.effect.attempt-get-v1", {"attempt_id": attempt_id}, repo_id=repo_id)
        if status == 200 and isinstance(body, dict) and body.get("status") == "accepted":
            return 200, body["result"]
        return status, body

    return get


def file_attempt_source(path: Path) -> AttemptSource:
    """Exported attempt-get results: a JSON list, or one object per line."""
    text = path.read_text(encoding="utf-8").strip()
    documents = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    by_id = {(doc.get("authorization") or {}).get("attempt_id"): doc for doc in documents}

    def get(attempt_id: str) -> tuple[int, Any]:
        if attempt_id in by_id:
            return 200, by_id[attempt_id]
        return 404, {"error": {"code": "attempt-not-in-export"}}

    return get


def uncertain_effects(
    attempt_ids: Iterable[str],
    source: AttemptSource,
    *,
    journal_dir: Path | None,
    work_refs: list[dict[str, str]] | None = None,
) -> DiagnosticResult:
    unknowns = ["owner-has-no-attempt-list-operation:enumeration-limited-to-supplied-attempt-ids"]
    ids = list(dict.fromkeys(attempt_ids))
    bad = [value for value in ids if not ATTEMPT_ID.fullmatch(value)]
    if bad:
        return DiagnosticResult("uncertain-effects", "1", "error",
                                {"invalid_attempt_ids": bad}, unknowns=unknowns)
    journal: dict[str, JournalEffect] | None = None
    journal_state: dict[str, Any] = {"supplied": journal_dir is not None}
    if journal_dir is not None:
        try:
            journal = read_journal(journal_dir)
            journal_state.update(state="read", effects=len(journal))
        except JournalUnreadable as error:
            journal_state.update(state="unreadable", reason=str(error))
            unknowns.append("execution-journal-unreadable")
    else:
        unknowns.append("execution-journal-not-supplied")

    attempts: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    refs = list(work_refs or [])
    for attempt_id in ids:
        status, body = source(attempt_id)
        refs.append({"kind": "effect-attempt", "id": attempt_id})
        if status != 200 or not isinstance(body, dict):
            error = body.get("error") if isinstance(body, dict) else body
            unreadable.append({"attempt_id": attempt_id, "status": status, "error": error})
            continue
        entry = classify(body, journal)
        if entry["intent_id"]:
            refs.append({"kind": "effect-intent", "id": f"{entry['intent_id']}@{entry['intent_revision']}"})
        if entry["work_item_id"]:
            refs.append({"kind": "work-item", "id": str(entry["work_item_id"])})
        attempts.append(entry)

    linked_keys = {entry["journal"]["effect_key"] for entry in attempts if entry["journal"]}
    unlinked = [
        effect.summary() for key, effect in sorted((journal or {}).items())
        if key not in linked_keys and effect.status == "UNSETTLED"
    ]
    if unreadable:
        unknowns.append("some-attempts-unreadable")
    counts: dict[str, int] = {}
    for entry in attempts:
        counts[entry["classification"]] = counts.get(entry["classification"], 0) + 1
    uncertain = [entry for entry in attempts if entry["state"] == "redeemed" and entry["report"] is None]
    needs_attention = bool(
        counts.get("provider-outcome-needed") or counts.get("safe-to-seal") or unlinked or unreadable
        or any(entry["anomalies"] for entry in attempts)
    )
    result = {
        "journal": journal_state,
        "counts": dict(sorted(counts.items())),
        "redeemed_without_report": [entry["attempt_id"] for entry in uncertain],
        "attempts": attempts,
        "unreadable_attempts": unreadable,
        "unsettled_journal_effects_without_supplied_attempt": unlinked,
    }
    return DiagnosticResult("uncertain-effects", "1", "attention" if needs_attention else "ok",
                            result, work_refs=refs, unknowns=unknowns)


# ---------------------------------------------------------------------------
# provider outcome (read-only)
# ---------------------------------------------------------------------------

# url -> (status, json)
HttpGet = Callable[[str, Mapping[str, str]], tuple[int, Any]]


def _http_get(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=20, context=tls_context()) as response:  # noqa: S310 - fixed https API base
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, None


def github_repository(repository: str | None) -> str | None:
    if not repository:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return repository
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", repository)
    return match.group(1) if match else None


def ls_remote_head(remote: str, branch: str, *, timeout: float = 30.0) -> tuple[str | None, str | None]:
    """The branch head on ``remote`` via ``git ls-remote``; (oid, error)."""
    git = shutil.which("git")
    if git is None:
        return None, "git-not-installed"
    env = {"GIT_TERMINAL_PROMPT": "0", "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/")}
    try:
        completed = subprocess.run(
            [git, "ls-remote", "--refs", remote, f"refs/heads/{branch}"],
            capture_output=True, text=True, timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        return None, "ls-remote-timeout"
    if completed.returncode != 0:
        return None, f"ls-remote-failed:{completed.stderr.strip()[-200:]}"
    for line in completed.stdout.splitlines():
        oid, _, ref = line.partition("\t")
        if ref == f"refs/heads/{branch}":
            return oid, None
    return "", None  # empty string: definitively absent


def _artifact_check(effect: JournalEffect | None, artifact_root: Path | None) -> dict[str, Any]:
    if effect is None or not effect.local_ready:
        return {"state": "no-journal-artifact"}
    local = effect.local_ready
    if artifact_root is None:
        return {"state": "artifact-root-not-supplied", "commit_oid": local.get("commit_oid"),
                "artifact_sha256": local.get("artifact_sha256")}
    bundle = artifact_root / local["artifact_relative_path"]
    try:
        raw = bundle.read_bytes()
    except OSError:
        return {"state": "artifact-missing", "path": str(bundle)}
    digest_ok = _sha(raw) == local.get("artifact_sha256") or hashlib.sha256(raw).hexdigest() == local.get("artifact_sha256")
    heads: list[str] = []
    git = shutil.which("git")
    if git is not None:
        listed = subprocess.run([git, "bundle", "list-heads", str(bundle)], capture_output=True,
                                text=True, timeout=30, check=False)
        heads = [line.split()[0] for line in listed.stdout.splitlines() if line.strip()]
    return {
        "state": "verified" if digest_ok and local.get("commit_oid") in heads else "mismatch",
        "path": str(bundle),
        "artifact_sha256_matches_journal": digest_ok,
        "bundle_contains_commit": local.get("commit_oid") in heads,
        "commit_oid": local.get("commit_oid"),
    }


def provider_outcome_check(
    attempt_id: str,
    source: AttemptSource,
    *,
    journal_dir: Path | None,
    remote: str | None = None,
    artifact_root: Path | None = None,
    github_api: str = "https://api.github.com",
    github_token: str | None = None,
    http_get: HttpGet | None = None,
    ls_remote: Callable[[str, str], tuple[str | None, str | None]] = ls_remote_head,
    work_refs: list[dict[str, str]] | None = None,
) -> DiagnosticResult:
    unknowns: list[str] = []
    refs = list(work_refs or []) + [{"kind": "effect-attempt", "id": attempt_id}]
    if not ATTEMPT_ID.fullmatch(attempt_id):
        return DiagnosticResult("provider-outcome-check", "1", "error", {"invalid_attempt_id": attempt_id})
    status, body = source(attempt_id)
    if status != 200 or not isinstance(body, dict):
        return DiagnosticResult("provider-outcome-check", "1", "error",
                                {"attempt_id": attempt_id, "status": status, "error": body},
                                work_refs=refs, unknowns=["attempt-unreadable"])
    journal = None
    if journal_dir is not None:
        try:
            journal = read_journal(journal_dir)
        except JournalUnreadable:
            unknowns.append("execution-journal-unreadable")
    else:
        unknowns.append("execution-journal-not-supplied")
    entry = classify(body, journal)
    refs.append({"kind": "effect-intent", "id": f"{entry['intent_id']}@{entry['intent_revision']}"})
    linked = _journal_for(body, journal) if journal is not None else None
    expected = entry["commit_sha"]
    branch = entry["branch"]
    repo = github_repository(entry["repository"])
    remote = remote or (f"https://github.com/{repo}.git" if repo else None)

    observed: dict[str, Any] = {"remote": remote}
    if remote is None or not branch:
        unknowns.append("provider-remote-unresolvable")
        branch_state = "unknown"
    else:
        head, error = ls_remote(remote, branch)
        observed["branch_head"] = head or None
        if error:
            observed["branch_error"] = error
            unknowns.append("branch-lookup-failed")
            branch_state = "unknown"
        else:
            branch_state = "absent" if head == "" else ("matches" if head == expected else "differs")
    observed["branch_state"] = branch_state

    pr_state = None
    if entry["provider_operation"] == "open_pull_request":
        if repo is None:
            pr_state = "unknown"
            unknowns.append("pull-request-provider-unsupported")
        else:
            owner = repo.split("/")[0]
            query = urllib.parse.urlencode({"head": f"{owner}:{branch}", "state": "all", "per_page": "10"})
            headers = {"Authorization": f"Bearer {github_token}"} if github_token else {}
            code, pulls = (http_get or _http_get)(f"{github_api}/repos/{repo}/pulls?{query}", headers)
            if code != 200 or not isinstance(pulls, list):
                pr_state = "unknown"
                observed["pull_request_error"] = code
                unknowns.append("pull-request-lookup-failed")
            elif not pulls:
                pr_state = "absent"
            else:
                pull = max(pulls, key=lambda item: item.get("number") or 0)
                head_sha = (pull.get("head") or {}).get("sha")
                observed["pull_request"] = {
                    "number": pull.get("number"),
                    "url": pull.get("html_url"),
                    "state": "merged" if pull.get("merged_at") else pull.get("state"),
                    "head_sha": head_sha,
                    "base_branch": (pull.get("base") or {}).get("ref"),
                }
                base_ok = entry["base_branch"] in (None, observed["pull_request"]["base_branch"])
                pr_state = "matches" if head_sha == expected and base_ok else "differs"
        observed["pull_request_state"] = pr_state

    decisive = pr_state if entry["provider_operation"] == "open_pull_request" else branch_state
    outcome = {"matches": "applied", "absent": "not-observed", "differs": "conflict"}.get(decisive or "", "unknown")
    if outcome == "not-observed" and entry["provider_operation"] == "open_pull_request" and branch_state == "matches":
        outcome = "partially-applied"

    report = entry["report"]
    if report and outcome == "applied":
        pull = observed.get("pull_request") or {}
        if report.get("commit_sha") != expected or (pull and report.get("pr_url") != pull.get("url")):
            entry["anomalies"].append("application-report-differs-from-provider")
    next_step = _next_step(entry, outcome)
    result = {
        "attempt": {key: entry[key] for key in (
            "attempt_id", "state", "classification", "provider_operation", "repository", "branch",
            "commit_sha", "base_branch", "report", "anomalies")},
        "provider_outcome": outcome,
        "observed": observed,
        "signed_artifact": _artifact_check(linked, artifact_root),
        "journal": None if linked is None else linked.summary(),
        "next_step": next_step,
        "dispatch": "none: this check never redeems, re-dispatches or reports",
    }
    status_value = "error" if outcome == "unknown" else (
        "ok" if outcome == "applied" and report is not None and not entry["anomalies"] else "attention")
    return DiagnosticResult("provider-outcome-check", "1", status_value, result, work_refs=refs, unknowns=unknowns)


def _next_step(entry: Mapping[str, Any], outcome: str) -> str:
    state, operation = entry["state"], entry["provider_operation"]
    if outcome == "unknown":
        return "provider outcome could not be established; retry the read-only check"
    if outcome == "conflict":
        return "provider shows a different commit: investigate before any report; do not re-dispatch"
    if state == "accepted":
        if outcome == "not-observed":
            return "unredeemed and not observed at the provider: eligible for attempt-seal-unused-v1 by its owner principal"
        return "observed at the provider without redemption: investigate the out-of-band actor"
    if state == "redeemed":
        if outcome == "applied" and operation == "open_pull_request" and entry["report"] is None:
            return "applied: the applier may record attempt-report-applied-v1 with the observed commit and PR URL"
        if outcome == "applied":
            return "applied at the provider; no owner action is pending for this attempt"
        return ("redeemed but not applied: the authorization is consumed and replay never re-grants "
                "dispatch; a new intent revision is required for another attempt")
    return "no action"
