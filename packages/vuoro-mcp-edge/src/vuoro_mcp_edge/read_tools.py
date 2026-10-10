"""Bounded owner-read projections for exact-artifact review and continuation.

These reads are advisory. They keep Sprintctl as the sole Release, Decision,
checkpoint and effect-intent owner; neither snapshot digest is a write token.
The raw owner records may contain private prose and URLs, so only fields
selected below may cross the MCP boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .errors import WorkSourceUnavailable
from .toolsets import ToolFailure, ToolSet, ToolSpec, ToolsetContext
from .work_source import ForwardedIdentity, ShellWorkSource

EFFECT = "work.effect.get-v1"
ITEM = "work.read.item"
RELEASE = "work.read.release"
DECISIONS = "work.read.item-decisions"
LEASES = "work.lease.read-v1"
NEXT = "work.read.next-work-explain"
PREVIEW_OPS = frozenset({EFFECT, ITEM, RELEASE, DECISIONS, LEASES})
DELTA_OPS = frozenset({ITEM, RELEASE, DECISIONS, NEXT})
HEX = re.compile(r"^[0-9a-f]{64}$")
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
EDIT_REVISION = re.compile(r"^item:[0-9a-fA-F-]{36}@description:v[0-9]+@sha256:[0-9a-f]{64}$")
RELEASE_REVISION = re.compile(r"^item:[0-9a-fA-F-]{36}@description:v[0-9]+@sha256:[0-9a-f]{64}@revise:[0-9]+$")
CHECKPOINT_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
DECISION_KINDS = frozenset({"accept", "reject", "withdraw", "supersede", "revise"})
INTENT_CONTENT = ("item_id", "repository", "base_commit", "title", "rationale", "unified_diff")
MAX_DECISIONS = 20
MAX_REFS = 100
MAX_OBLIGATIONS = 64
MAX_CHECKS = 64

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
              "idempotentHint": True, "openWorldHint": False}

_PREVIEW = {
    "name": "preview_acceptance", "title": "Preview exact-artifact acceptance evidence",
    "description": (
        "Read-only advisory view of one exact effect intent revision and digest. "
        "It names current owner Release obligations and separately observed "
        "evidence; no obligation is marked passed, and this snapshot cannot "
        "authorize acceptance. Owner state may change during or after the "
        "read sequence. Requires separately granted work and effect-read access."
    ),
    "inputSchema": {"type": "object", "properties": {
        "intent_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "revision": {"type": "integer", "minimum": 1},
        "canonical_intent_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }, "required": ["intent_id", "revision", "canonical_intent_digest"],
        "additionalProperties": False},
    "annotations": _READ_ONLY,
}

_DELTA = {
    "name": "read_work_delta", "title": "Read work changes for a successor",
    "description": (
        "Read-only, currently authorized owner facts for an ordinary work "
        "identifier. A supplied baseline is comparison data, not proof or a "
        "capability. Shows changed work revision, Decision, Release and "
        "reference count, plus an owner-derived unacknowledged checkpoint "
        "when available. Source content and revision remain unknown without "
        "a pinned source manifest. Does not claim, settle or suggest action."
    ),
    "inputSchema": {"type": "object", "properties": {
        "work_link": {"type": "object", "properties": {
            "kind": {"const": "work"}, "id": {"type": "integer", "minimum": 1}},
            "required": ["kind", "id"], "additionalProperties": False},
        "baseline": {"type": "object", "properties": {
            "item_revision": {"type": "string"},
            "terminal_decision_id": {"type": ["integer", "null"]},
            "latest_decision_id": {"type": ["integer", "null"]},
            "release_digest": {"type": ["string", "null"]},
            "reference_count": {"type": "integer", "minimum": 0, "maximum": MAX_REFS},
        }, "additionalProperties": False},
    }, "required": ["work_link"], "additionalProperties": False},
    "annotations": _READ_ONLY,
}


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _bad_owner() -> ToolFailure:
    return ToolFailure("owner-response-invalid", "the work owner returned an inconsistent read")


def _object(value: Any, *, repo_id: str) -> dict[str, Any]:
    if type(value) is not dict or value.get("repo_id") != repo_id:
        raise _bad_owner()
    return value


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _digest(value: Any) -> bool:
    return type(value) is str and HEX.fullmatch(value) is not None


def _safe_text(value: Any, *, limit: int = 200) -> bool:
    return type(value) is str and 0 < len(value) <= limit and value.isprintable()


def _parse_preview(args: dict[str, Any]) -> dict[str, Any]:
    if (set(args) != {"intent_id", "revision", "canonical_intent_digest"}
            or not _safe_text(args.get("intent_id"), limit=128)
            or not _positive(args.get("revision"))
            or not _digest(args.get("canonical_intent_digest"))):
        raise ToolFailure("invalid-arguments", "give an exact intent id, revision and digest")
    return args


def _parse_delta(args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"work_link", "baseline"}:
        raise ToolFailure("invalid-arguments", "unknown read_work_delta argument")
    link = args.get("work_link")
    if type(link) is not dict or set(link) != {"kind", "id"} or link.get("kind") != "work" or not _positive(link.get("id")):
        raise ToolFailure("invalid-arguments", "work_link must be an ordinary work id")
    baseline = args.get("baseline")
    if baseline is not None:
        if type(baseline) is not dict or set(baseline) - {"item_revision", "terminal_decision_id", "latest_decision_id", "release_digest", "reference_count"}:
            raise ToolFailure("invalid-arguments", "invalid baseline")
        if "item_revision" in baseline and (type(baseline["item_revision"]) is not str
                or EDIT_REVISION.fullmatch(baseline["item_revision"]) is None):
            raise ToolFailure("invalid-arguments", "invalid baseline item revision")
        for field in ("terminal_decision_id", "latest_decision_id"):
            decision = baseline.get(field)
            if field in baseline and decision is not None and not _positive(decision):
                raise ToolFailure("invalid-arguments", "invalid baseline Decision id")
        release = baseline.get("release_digest")
        if "release_digest" in baseline and release is not None and not _digest(release):
            raise ToolFailure("invalid-arguments", "invalid baseline Release digest")
        refs = baseline.get("reference_count")
        if "reference_count" in baseline and (type(refs) is not int or not 0 <= refs <= MAX_REFS):
            raise ToolFailure("invalid-arguments", "invalid baseline reference count")
    return {"work_id": link["id"], "baseline": baseline}


class _OwnerReads:
    def __init__(self, work_source: ShellWorkSource) -> None:
        self.source = work_source
        self._advertised: frozenset[str] | None = None
        self._retry_at = 0.0

    async def available(self, operations: frozenset[str]) -> bool:
        # The catalog is caller-independent. Never cache a negative result
        # forever during runtime startup, and bound tools/list latency.
        if self._advertised is None and time.monotonic() >= self._retry_at:
            try:
                response = await asyncio.wait_for(
                    self.source._client.get("/api/catalog/v1", timeout=1.0), 1.0)
                body = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
                body = None
            if type(body) is dict and type(body.get("operations")) is list:
                self._advertised = frozenset(row["name"] for row in body["operations"]
                    if type(row) is dict and type(row.get("name")) is str)
            else:
                self._retry_at = time.monotonic() + 3.0
        return self._advertised is not None and operations <= self._advertised

    async def read(self, operation: str, args: dict[str, Any], caller: ForwardedIdentity,
                   *, optional_missing: bool = False,
                   trace: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        try:
            value = await self.source._invoke(operation, args, caller)
        except WorkSourceUnavailable as error:
            if optional_missing and error.code == "release-not-found":
                if trace is not None:
                    trace.append({"operation": operation, "status": "missing",
                                  "received_at": datetime.now(timezone.utc).isoformat()})
                return None
            if error.code in {"item-not-found", "effect-not-found", "work-not-found"}:
                raise ToolFailure("work-not-found", "no authorized owner record was found") from error
            if error.code == "authority-required":
                raise ToolFailure("authority-required", "the current caller lacks an owner read authority") from error
            # Never forward owner error messages, which may contain private
            # descriptions, URLs or a credential-bearing upstream detail.
            raise ToolFailure("owner-read-unavailable", "the work owner could not supply this read") from error
        if operation == NEXT:
            # This owner aggregate is already scoped by the invocation's
            # repo_id, but its published result schema has no repo_id field.
            if (type(value) is not dict or type(value.get("sprint")) is not dict
                    or type(value["sprint"].get("id")) is not int
                    or value["sprint"]["id"] != args["sprint_id"]):
                raise _bad_owner()
        else:
            value = _object(value, repo_id=caller.repo_id)
        if trace is not None:
            entry: dict[str, Any] = {"operation": operation, "status": "observed",
                                     "received_at": datetime.now(timezone.utc).isoformat()}
            if operation == EFFECT and type(value.get("intent")) is dict:
                effect = value["intent"]
                if _positive(effect.get("revision")):
                    entry["effect_revision"] = effect["revision"]
                if _digest(effect.get("canonical_intent_digest")):
                    entry["effect_digest"] = effect["canonical_intent_digest"]
            elif operation == ITEM and type(value.get("item")) is dict:
                revision = value["item"].get("edit_revision")
                if type(revision) is str and EDIT_REVISION.fullmatch(revision):
                    entry["item_edit_revision"] = revision
            elif operation == RELEASE and type(value.get("release")) is dict:
                release = value["release"]
                revision = release.get("item_revision")
                if type(revision) is str and RELEASE_REVISION.fullmatch(revision):
                    entry["release_item_revision"] = revision
                if _digest(release.get("release_digest")):
                    entry["release_digest"] = release["release_digest"]
            elif operation == DECISIONS and type(value.get("decisions")) is list:
                rows = value["decisions"]
                if rows and type(rows[-1]) is dict and _positive(rows[-1].get("id")):
                    entry["latest_decision_id"] = rows[-1]["id"]
            trace.append(entry)
        return value


def _item(value: dict[str, Any], work_id: int) -> dict[str, Any]:
    item = value.get("item")
    revision = item.get("edit_revision") if type(item) is dict else None
    if (type(item) is not dict or type(item.get("id")) is not int or item["id"] != work_id
            or type(revision) is not str or EDIT_REVISION.fullmatch(revision) is None):
        raise _bad_owner()
    return item


def _release(value: dict[str, Any] | None, work_id: int) -> dict[str, Any] | None:
    if value is None:
        return None
    release = value.get("release")
    if (type(release) is not dict or type(release.get("work_item_id")) is not int
            or release["work_item_id"] != work_id
            or not _digest(release.get("release_digest"))
            or type(release.get("item_revision")) is not str
            or RELEASE_REVISION.fullmatch(release["item_revision"]) is None):
        raise _bad_owner()
    return release


def _decisions(value: dict[str, Any], work_id: int) -> dict[str, Any]:
    if type(value.get("item_id")) is not int or value["item_id"] != work_id or type(value.get("decisions")) is not list:
        raise _bad_owner()
    return value


def _decision_rows(value: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows = value["decisions"]
    selected = []
    for row in rows[-MAX_DECISIONS:]:
        if type(row) is not dict or not _positive(row.get("id")):
            raise _bad_owner()
        kind = row.get("kind")
        if type(kind) is not str or kind not in DECISION_KINDS:
            raise _bad_owner()
        digests = row.get("evidence_digests") or []
        if type(digests) is not list or len(digests) > MAX_OBLIGATIONS or any(not _digest(d) for d in digests):
            raise _bad_owner()
        selected.append({"id": row["id"], "kind": kind,
                         "release_digest": row.get("release_digest") if _digest(row.get("release_digest")) else None,
                         "evidence_digests": digests})
    return selected, len(rows)


def _reference_count(item_value: dict[str, Any]) -> int:
    # A Release freezes older refs; current item refs are the continuation
    # input. The Release digest remains a separate frozen checkpoint.
    refs = item_value.get("refs")
    if type(refs) is not list or len(refs) > MAX_REFS:
        raise ToolFailure("owner-result-too-large", "the reference set exceeds this read contract")
    for ref in refs:
        if type(ref) is not dict or any(type(ref.get(key)) is not str for key in ("ref_type", "url")):
            raise _bad_owner()
    return len(refs)


def _checkpoint_time(value: Any) -> str | None:
    if type(value) is not str or CHECKPOINT_TIME.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _verify_intent_content(intent: dict[str, Any]) -> None:
    """Check the frozen Sprintctl digest domain, not an acceptance verdict."""
    if (not _positive(intent.get("item_id"))
            or any(type(intent.get(key)) is not str for key in INTENT_CONTENT if key != "item_id")):
        raise _bad_owner()
    try:
        content = {"schema": "sprintctl-effect-intent/v1",
                   **{key: intent[key] for key in INTENT_CONTENT}}
        computed = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise _bad_owner() from None
    if computed != intent.get("canonical_intent_digest"):
        raise _bad_owner()


def build_toolset(context: ToolsetContext) -> ToolSet:
    owner = _OwnerReads(context.work_source)

    async def describe_preview():
        return _PREVIEW if await owner.available(PREVIEW_OPS) else None

    async def describe_delta():
        return _DELTA if await owner.available(DELTA_OPS) else None

    async def preview(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        read_started_at = datetime.now(timezone.utc).isoformat()
        trace: list[dict[str, Any]] = []
        effect_value = await owner.read(EFFECT, {"intent_id": args["intent_id"]}, caller, trace=trace)
        intent = effect_value.get("intent") if effect_value else None
        if (type(intent) is not dict or intent.get("intent_id") != args["intent_id"]
                or not _positive(intent.get("item_id")) or not _positive(intent.get("revision"))
                or not _digest(intent.get("canonical_intent_digest"))):
            raise _bad_owner()
        _verify_intent_content(intent)
        work_id = intent["item_id"]
        item_value = await owner.read(ITEM, {"item_id": work_id}, caller, trace=trace)
        item = _item(item_value, work_id)
        release_value = await owner.read(RELEASE, {"item_id": work_id}, caller,
                                         optional_missing=True, trace=trace)
        release = _release(release_value, work_id)
        decision_value = _decisions(await owner.read(DECISIONS, {"item_id": work_id}, caller,
                                                     trace=trace), work_id)
        lease_value = await owner.read(LEASES, {"item_id": work_id}, caller, trace=trace)
        if lease_value.get("item_id") != work_id:
            raise _bad_owner()
        # A second exact owner read catches common mid-read moves. This is
        # still a read sequence, never an atomic transaction.
        effect_after = await owner.read(EFFECT, {"intent_id": args["intent_id"]}, caller,
                                        trace=trace)
        if type(effect_after.get("intent")) is not dict:
            raise _bad_owner()
        _verify_intent_content(effect_after["intent"])
        item_after = _item(await owner.read(ITEM, {"item_id": work_id}, caller,
                                            trace=trace), work_id)
        release_after = _release(await owner.read(RELEASE, {"item_id": work_id}, caller,
                                                   optional_missing=True, trace=trace), work_id)
        decision_after = _decisions(await owner.read(DECISIONS, {"item_id": work_id}, caller,
                                                     trace=trace), work_id)
        lease_after = await owner.read(LEASES, {"item_id": work_id}, caller, trace=trace)
        owner_basis = {"intent_id": intent["intent_id"], "revision": intent["revision"],
                       "canonical_intent_digest": intent["canonical_intent_digest"],
                       "item_id": work_id, "item_revision": item["edit_revision"],
                       "release_digest": release["release_digest"] if release else None}
        stable = (effect_after == effect_value and item_after == item
                  and release_after == release and decision_after == decision_value
                  and lease_after == lease_value)
        expected = {k: args[k] for k in ("intent_id", "revision", "canonical_intent_digest")}
        matches = all(owner_basis[k] == expected[k] for k in expected)
        release_revision_current = bool(release and release["item_revision"].startswith(item["edit_revision"] + "@revise:"))
        release_bound = bool(release and intent.get("release_digest") == release["release_digest"])
        contract = release.get("acceptance_contract") if release else None
        if contract is not None and type(contract) is not dict:
            raise _bad_owner()
        obligations = contract.get("evidence_obligations", []) if contract else []
        if (type(obligations) is not list or len(obligations) > MAX_OBLIGATIONS
                or any(not _safe_text(label) for label in obligations)):
            raise _bad_owner()
        rows, total_decisions = _decision_rows(decision_value)
        acceptance = intent.get("acceptance")
        if acceptance is not None and type(acceptance) is not dict:
            raise _bad_owner()
        verification = acceptance.get("verification") if acceptance else None
        if verification is not None and type(verification) is not dict:
            raise _bad_owner()
        receipt = verification.get("receipt") if verification else None
        checks = receipt.get("checks") if type(receipt) is dict else None
        if checks is not None and (type(checks) is not list or len(checks) > MAX_CHECKS):
            raise _bad_owner()
        check_revisions = []
        for index, check in enumerate(checks or [], 1):
            if type(check) is not dict or not _safe_text(check.get("name")) or type(check.get("revision")) is not str or SHA.fullmatch(check["revision"]) is None:
                raise _bad_owner()
            check_revisions.append({"index": index, "revision": check["revision"],
                                    "owner_reported_status": check.get("status") if check.get("status") in ("passed", "failed") else "unknown"})
        observed_digests = sorted({digest for row in rows for digest in row["evidence_digests"]})
        evidence_digest = verification.get("evidence_digest") if verification else None
        if evidence_digest is not None:
            if type(evidence_digest) is not str or SHA.fullmatch(evidence_digest) is None:
                raise _bad_owner()
            observed_digests.append(evidence_digest)
        report = {"schema": "vuoro-acceptance-preview/v1", "authority": "sprintctl-owner-reads",
                  "authorizes_acceptance": False, "snapshot_is_permit": False,
                  "snapshot_domain": "redacted-owner-projection/v1",
                  "intent_content_integrity": "matches-frozen-owner-digest-domain",
                  "consistency": "non-atomic-owner-read-sequence",
                  "basis_status": "unstable" if not stable else "historical" if not matches or (release and (not release_revision_current or not release_bound)) else "matching" if release else "unknown",
                  "requested_basis": expected, "owner_basis": owner_basis,
                  "release_binding": "matching" if release_bound else "missing" if not release else "unknown-or-different",
                  "release_item_revision": "matching" if release_revision_current else "missing" if not release else "stale",
                  "obligations": [{"label": label, "evidence_mapping": "unknown"} for label in obligations],
                  "owner_referenced_evidence_digests": sorted(set(observed_digests)),
                  "evidence_integrity": "unknown; owner Decision references and protected receipt were read, evidence bytes were not",
                  "evidence_reference_scope": "last-20-decisions" if total_decisions > len(rows) else "all-item-decisions",
                  "protected_check_revisions": check_revisions,
                  "decisions": rows, "decision_count": total_decisions,
                  "decision_rows_truncated": total_decisions > len(rows),
                  "lease_verification_record": "present" if lease_value.get("verification") is not None else "missing",
                  "read_started_at": read_started_at,
                  "read_completed_at": datetime.now(timezone.utc).isoformat(),
                  "sources": trace}
        report["snapshot_id"] = _sha({k: v for k, v in report.items() if k not in {"sources", "snapshot_id", "read_started_at", "read_completed_at"}})
        return report

    async def delta(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        read_started_at = datetime.now(timezone.utc).isoformat()
        trace: list[dict[str, Any]] = []
        work_id = args["work_id"]
        item_value = await owner.read(ITEM, {"item_id": work_id}, caller, trace=trace)
        item = _item(item_value, work_id)
        decisions = _decisions(await owner.read(DECISIONS, {"item_id": work_id}, caller,
                                                trace=trace), work_id)
        release = _release(await owner.read(RELEASE, {"item_id": work_id}, caller,
                                            optional_missing=True, trace=trace), work_id)
        sprint_id = item.get("sprint_id")
        if not _positive(sprint_id):
            raise _bad_owner()
        explain = await owner.read(NEXT, {"sprint_id": sprint_id}, caller, trace=trace)
        checkpoints = explain.get("checkpointed_unacked") if explain else None
        if type(checkpoints) is not list:
            raise _bad_owner()
        selected = [row for row in checkpoints if type(row) is dict and row.get("item_id") == work_id]
        if len(selected) > 1:
            raise _bad_owner()
        checkpoint = None
        if selected:
            row = selected[0]
            if not _positive(row.get("checkpoint_note_id")):
                raise _bad_owner()
            checkpoint = {"note_id": row["checkpoint_note_id"], "created_at": _checkpoint_time(row.get("created_at")),
                          "release_digest": row.get("release_digest") if _digest(row.get("release_digest")) else None,
                          "git_sha": row.get("sha") if type(row.get("sha")) is str and re.fullmatch(r"[0-9a-f]{40}", row["sha"]) else None,
                          "state": "owner-derived-unacknowledged", "source_content": "not-disclosed"}
        rows, decision_count = _decision_rows(decisions)
        current = {"item_revision": item["edit_revision"],
                   "terminal_decision_id": decisions.get("terminal_decision_id"),
                   "latest_decision_id": rows[-1]["id"] if rows else None,
                   "release_digest": release["release_digest"] if release else None,
                   "reference_count": _reference_count(item_value)}
        if current["terminal_decision_id"] is not None and not _positive(current["terminal_decision_id"]):
            raise _bad_owner()
        baseline = args["baseline"]
        comparison = {key: "unknown" if baseline is None or key not in baseline
                      else "changed" if baseline[key] != current[key] else "unchanged"
                      for key in ("item_revision", "terminal_decision_id", "latest_decision_id", "release_digest")}
        # Equal counts cannot prove equal refs; private URLs and labels stay
        # opaque until an owner-minted safe reference identifier exists.
        comparison["reference_count"] = (
            "changed" if baseline is not None and "reference_count" in baseline
            and baseline["reference_count"] != current["reference_count"] else "unknown"
        )
        # Reread item and Decisions: a moving owner cannot become a stable
        # "no change" answer simply because one read preceded a Decision.
        item_after = _item(await owner.read(ITEM, {"item_id": work_id}, caller,
                                            trace=trace), work_id)
        decisions_after = _decisions(await owner.read(DECISIONS, {"item_id": work_id}, caller,
                                                      trace=trace), work_id)
        release_after = _release(await owner.read(RELEASE, {"item_id": work_id}, caller,
                                                  optional_missing=True, trace=trace), work_id)
        explain_after = await owner.read(NEXT, {"sprint_id": sprint_id}, caller, trace=trace)
        stable = (item_after == item and decisions_after == decisions and release_after == release
                  and explain_after == explain)
        if not stable:
            comparison = {key: "unknown" for key in comparison}
            if checkpoint is not None:
                checkpoint["state"] = "provisional-owner-read"
        report = {"schema": "vuoro-work-delta/v1", "authority": "sprintctl-owner-reads",
                  "authorizes_action": False, "consistency": "non-atomic-owner-read-sequence",
                  "snapshot_domain": "redacted-owner-projection/v1",
                  "read_state": "stable-sequence" if stable else "changed-during-read",
                  "observed_facts_freshness": "read-sequence-stable-not-currentness-proof" if stable else "provisional",
                  "work_link": {"kind": "work", "id": work_id},
                  "work": {"title": item.get("title") if _safe_text(item.get("title")) else None,
                           "status": item.get("status") if item.get("status") in ("pending", "active", "blocked", "done") else "unknown",
                           "revision": current["item_revision"],
                           "intention_detail": "not-disclosed; title and revision only"},
                  "current": current, "baseline_provenance": "caller-supplied" if baseline is not None else "absent",
                  "release_item_revision": "unknown" if not stable else "matching" if release and release["item_revision"].startswith(item["edit_revision"] + "@revise:") else "missing" if not release else "stale",
                  "latest_decision": {key: rows[-1][key] for key in ("id", "kind", "release_digest")} if rows else None,
                  "decision_count": decision_count,
                  "decision_rows_truncated": decision_count > len(rows),
                  "changed": comparison,
                  "source_revisions": "unknown; no pinned source manifest in owner refs",
                  "source_content": "not-read", "reference_state": "owner-ref-count-observed" if current["reference_count"] else "none-recorded",
                  "unacknowledged_checkpoint": checkpoint,
                  "read_started_at": read_started_at,
                  "read_completed_at": datetime.now(timezone.utc).isoformat(),
                  "sources": trace}
        report["snapshot_id"] = _sha({k: v for k, v in report.items() if k not in {"sources", "snapshot_id", "read_started_at", "read_completed_at"}})
        return report

    return ToolSet("owner-read-projections", (
        ToolSpec("preview_acceptance", "effect-read", _PREVIEW, _parse_preview, preview,
                 describe=describe_preview, required_authorities=frozenset({"work:read"})),
        ToolSpec("read_work_delta", "read", _DELTA, _parse_delta, delta,
                 describe=describe_delta),
    ))
