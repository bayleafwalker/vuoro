"""Thin provider observations. No provider verdict is an acceptance or grant.

Only documented wire fields are interpreted. Collector observations are named
separately; absent build/session/artifact/rubric/instruction data stays unknown.
The caller retains the original payload at ref. This module does not persist,
verify webhook signatures, invoke a transport or mint work/run identities.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

UNKNOWN = "unknown"
SCHEMA = "vuoro-provider-observation/v1"
COLLECTOR = "vuoro-provider-observation/v1"
OBSERVED_FIELDS = frozenset({"provider_build", "session_reference", "artifact_digest",
                             "rubric_or_check_revision", "observed_instruction_digest"})
DIGEST_FIELDS = frozenset({"artifact_digest", "observed_instruction_digest"})
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _text(value):
    return type(value) is str and bool(value.strip())


def normalize(provider, event, payload, *, delivery_id, observed=None):
    """Normalize a supplied payload; unverified context cannot assert assurance."""
    if type(payload) is not dict or not _text(delivery_id):
        raise ValueError("payload object and external delivery ID required")
    payload_digest = "sha256:" + hashlib.sha256(canonical(payload)).hexdigest()
    fields = dict.fromkeys(OBSERVED_FIELDS, UNKNOWN)
    observed = {} if observed is None else observed
    if type(observed) is not dict or observed.keys() - OBSERVED_FIELDS:
        raise ValueError("unsupported collector observation")
    for key, value in observed.items():
        if not _text(value) or (key in DIGEST_FIELDS and value != UNKNOWN and not SHA256.fullmatch(value)):
            raise ValueError("invalid collector observation")
        fields[key] = value
    repository = UNKNOWN
    commit_reference = UNKNOWN
    event_reference = UNKNOWN
    explanation = UNKNOWN
    if provider == "anthropic-managed-agents" and event == "span.outcome_evaluation_end":
        if (payload.get("type") != event or not _text(payload.get("id"))
                or not _text(payload.get("result")) or not _text(payload.get("outcome_id"))):
            raise ValueError("malformed outcome event")
        event_reference = payload["id"]
        verdict = payload["result"]  # unknown future verdicts remain evidence, never success
        explanation = payload.get("explanation", UNKNOWN)
        status = "evaluation-ended"
        provider_refs = {"outcome_id": payload["outcome_id"],
                         "evaluation_start_id": payload.get("outcome_evaluation_start_id", UNKNOWN)}
    elif provider == "github" and event in {"check_run", "workflow_run", "pull_request"}:
        repo = payload.get("repository")
        value = payload.get(event)
        if (type(repo) is not dict or not _text(repo.get("full_name"))
                or type(value) is not dict or type(value.get("id")) is not int or value["id"] <= 0
                or not _text(payload.get("action"))):
            raise ValueError("malformed GitHub event")
        repository = repo["full_name"]
        event_reference = str(value["id"])
        status = payload["action"]
        verdict = (value.get("conclusion") or UNKNOWN) if event != "pull_request" else UNKNOWN
        head = value.get("head", {}) if event == "pull_request" else value
        if type(head) is not dict:
            raise ValueError("malformed GitHub head")
        commit_reference = head.get("sha" if event == "pull_request" else "head_sha", UNKNOWN)
        if commit_reference != UNKNOWN and (not _text(commit_reference) or not GIT_SHA.fullmatch(commit_reference)):
            raise ValueError("invalid commit reference")
        provider_refs = {"run_attempt": value.get("run_attempt", UNKNOWN),
                         "workflow_id": value.get("workflow_id", UNKNOWN),
                         "check_name": value.get("name", UNKNOWN)}
    else:
        raise ValueError("unsupported provider event")
    # Identity is independent of verdict/content. Changed payload under one
    # delivery keeps its owner retry key and changes the bound content digest.
    identity = {"provider": provider, "repository": repository, "event": event, "delivery_id": delivery_id}
    return {"schema": SCHEMA, **identity, **fields,
            "event_reference": event_reference, "provider_references": provider_refs,
            "commit_reference": commit_reference, "status": status, "verdict": verdict,
            "explanation": explanation, "payload_digest": payload_digest,
            "assurance": "unverified-supplied-payload", "collector_observations": sorted(observed),
            "idempotency_key": "provider-observation:" + hashlib.sha256(canonical(identity)).hexdigest()}


def evidence_item_draft(observation, *, ref, collected_at):
    """Existing append-owner fields. Run/grant, chain tail and replay are owner concerns."""
    if (type(observation) is not dict or observation.get("schema") != SCHEMA
            or observation.get("assurance") != "unverified-supplied-payload"
            or not _text(ref) or not isinstance(collected_at, datetime)
            or collected_at.tzinfo is None or collected_at.utcoffset() is None):
        raise ValueError("observation, original payload reference and zoned collection time required")
    digest = "sha256:" + hashlib.sha256(canonical(observation)).hexdigest()
    return {"item_id": observation["idempotency_key"], "kind": "provider-observation",
            "ref": ref, "digest": digest, "collector": COLLECTOR,
            "idempotency_key": observation["idempotency_key"],
            "validity": {"basis": "indefinite", "valid_from": collected_at.isoformat(),
                         "valid_until": None, "component_digests": {}},
            "claims": [{"claim_type": "observation", "subject": observation["idempotency_key"],
                        "grant_id": None, "freshness": None, "confirms": None,
                        "detail": observation}],
            "provenance": {"payload_digest": observation["payload_digest"],
                           "assurance": "unverified-supplied-payload"}}


def normalize_github_check_response(payload, *, repository, capture_id, observed=None):
    """A supplied REST GET response, explicitly distinct from webhook delivery.

Repository is captured request scope, not inferred from an external URL. The
capture ID names one immutable response capture, not the recurring check ID.
"""
    if (type(payload) is not dict or not _text(repository)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or any(part in {".", ".."} for part in repository.split("/"))
            or payload.get("status") not in {"queued", "in_progress", "completed"}):
        raise ValueError("check response and captured repository scope required")
    # Reuse documented check fields without claiming a webhook was received.
    result = normalize("github", "check_run", {
        "action": payload["status"], "repository": {"full_name": repository},
        "check_run": payload,
    }, delivery_id=capture_id, observed=observed)
    result.update(event="check_run_response", capture_source="supplied-rest-get-response",
                  repository_source="captured-request-scope",
                  payload_digest="sha256:" + hashlib.sha256(canonical(payload)).hexdigest())
    identity = {k: result[k] for k in ("provider", "repository", "event", "delivery_id")}
    result["idempotency_key"] = "provider-observation:" + hashlib.sha256(canonical(identity)).hexdigest()
    return result
