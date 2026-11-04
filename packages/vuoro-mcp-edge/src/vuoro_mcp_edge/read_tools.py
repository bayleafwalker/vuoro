"""Expose Sprintctl's bounded, atomic owner-read operations to MCP callers.

The edge validates the caller's exact request and maps the owner's response.
It does not assemble snapshots from separate reads or decide acceptance.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from .acceptance_explanation import build_spec as build_explanation_spec
from .errors import WorkSourceUnavailable
from .toolsets import ToolFailure, ToolSet, ToolSpec, ToolsetContext
from .work_source import ForwardedIdentity, ShellWorkSource

ACCEPTANCE_SNAPSHOT = "work.effect.acceptance-snapshot-v1"
CONTINUATION_DELTA = "work.run.continuation-delta-v1"
EFFECT_PREVIEW = "work.effect.preview-v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
MAX_PAGE = 64

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
              "idempotentHint": True, "openWorldHint": False}

_EFFECT_PREVIEW = {
    "name": "preview_effect", "title": "Preview declared effect",
    "description": "Read a redacted static declaration for one intent. This does not authorize acceptance or execution.",
    "inputSchema": {"type": "object", "properties": {
        "intent_id": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,128}$"}},
        "required": ["intent_id"], "additionalProperties": False},
    "annotations": _READ_ONLY,
}


def _parse_effect_preview(args: dict[str, Any]) -> dict[str, Any]:
    if (set(args) != {"intent_id"} or type(args.get("intent_id")) is not str
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", args["intent_id"]) is None):
        raise ToolFailure("invalid-arguments", "give one explicit intent id")
    return {"intent_id": args["intent_id"], "disclose_paths": False}


def _effect_projection(value: dict[str, Any], intent_id: str) -> dict[str, Any]:
    def obj(v: Any, keys: str) -> bool:
        return type(v) is dict and set(v) == set(keys.split())

    def digest(v: Any) -> bool:
        return type(v) is str and HEX.fullmatch(v) is not None

    def identifier(v: Any) -> bool:
        return type(v) is str and re.fullmatch(r"intent_[0-9A-HJKMNP-TV-Z]{26}", v) is not None

    try:
        b, c, d, r = (value[k] for k in ("basis", "declared_changes", "duplicate_relation", "receipt_source"))
        valid = (obj(value, "schema basis unified_diff_sha256 declared_changes resources protected_policy external_consequences duplicate_relation receipt_source authorization")
            and obj(b, "intent_id revision canonical_intent_digest state item_id repository base_commit release_digest")
            and identifier(b["intent_id"]) and b["intent_id"] == intent_id
            and _positive(b["revision"]) and _positive(b["item_id"])
            and digest(b["canonical_intent_digest"]) and (b["release_digest"] is None or digest(b["release_digest"]))
            and b["state"] in ("proposed", "accepted", "rejected", "applied")
            and type(b["repository"]) is str and 1 <= len(b["repository"]) <= 200
            and "\x00" not in b["repository"]
            and not any(0xD800 <= ord(ch) <= 0xDFFF for ch in b["repository"])
            and type(b["base_commit"]) is str and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", b["base_commit"]) is not None
            and digest(value["unified_diff_sha256"])
            and obj(c, "status path_count paths redacted truncated")
            and c["status"] in ("parsed", "unsupported", "unavailable")
            and ((c["status"] == "parsed" and type(c["path_count"]) is int and 0 <= c["path_count"] <= 1000000)
                 or (c["status"] != "parsed" and c["path_count"] is None))
            and c["paths"] == [] and c["redacted"] is True and c["truncated"] is False
            and value["resources"] == {"status": "not-declared"}
            and value["protected_policy"] == {"status": "unknown"}
            and value["external_consequences"] == {"status": "unknown"}
            and obj(d, "status candidate_intent_ids complete scope")
            and d["status"] in ("observed", "unknown") and type(d["complete"]) is bool
            and d["scope"] == "authorized proposed intents, same repository/item/release/content"
            and type(d["candidate_intent_ids"]) is list and len(d["candidate_intent_ids"]) <= 200
            and all(identifier(x) for x in d["candidate_intent_ids"])
            and (d["status"] != "unknown" or (not d["candidate_intent_ids"] and not d["complete"]))
            and obj(r, "operation acceptance application") and r["operation"] == "work.effect.get-v1"
            and r["acceptance"] in ("observed", "missing") and r["application"] in ("observed", "missing")
            and value["authorization"] == "none; owner acceptance and execution checks remain required")
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ToolFailure("owner-response-invalid", "the work owner returned an inconsistent read")
    # The owner may include other intent IDs for a caller with list authority.
    # This narrow tool is not an enumeration surface.
    return {**{k: v for k, v in value.items() if k != "duplicate_relation"},
            "basis": {k: v for k, v in b.items() if k != "repository"}}

_PREVIEW = {
    "name": "preview_acceptance", "title": "Preview exact-artifact acceptance evidence",
    "description": (
        "Read one atomic, bounded Sprintctl owner snapshot for an exact effect "
        "intent revision, canonical intent digest and supplied diff artifact "
        "digest. Receipt and evidence comparisons are descriptive; source bytes, "
        "lease settlement and acceptance authorization remain unknown or false."
    ),
    "inputSchema": {"type": "object", "properties": {
        "intent_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "revision": {"type": "integer", "minimum": 1},
        "canonical_intent_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "artifact_sha256": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "verification_ref": {"type": "object", "properties": {
            "run_id": {"type": "string", "pattern": RUN_ID.pattern},
            "item_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            "required": ["run_id", "item_id"], "additionalProperties": False},
    }, "required": ["intent_id", "revision", "canonical_intent_digest", "artifact_sha256"],
        "additionalProperties": False},
    "annotations": _READ_ONLY,
}

_CHECKPOINT_FIELDS = ("repo_id", "item_id", "work_edit_revision", "status_revision",
                      "current_release_digest", "current_decision_digest",
                      "current_reference_set_digest")
_DELTA = {
    "name": "read_work_delta", "title": "Read a successor run's work delta",
    "description": (
        "Read bounded owner facts for an explicitly named, currently owned run "
        "and work item. Checkpoints are caller comparison data; source bytes and "
        "external Git correspondence are unknown. A cursor only pages the current "
        "owner reference set and is refused when its snapshot becomes stale. "
        "Reference URL and label values marked incomplete are excerpts; do not use "
        "an incomplete URL as a link. Owner truncation markers and digests are "
        "returned unchanged."
    ),
    "inputSchema": {"type": "object", "properties": {
        "run_id": {"type": "string", "pattern": RUN_ID.pattern},
        "item_id": {"type": "integer", "minimum": 1},
        "checkpoint": {"type": "object", "properties": {
            "repo_id": {"type": "string", "minLength": 1},
            "item_id": {"type": "integer", "minimum": 1},
            "work_edit_revision": {"type": "string", "minLength": 1},
            "status_revision": {"type": "string", "minLength": 1},
            "current_release_digest": {"type": ["string", "null"]},
            "current_decision_digest": {"anyOf": [{"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}, {"type": "null"}]},
            "current_reference_set_digest": {"anyOf": [{"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}, {"type": "null"}]},
        }, "required": list(_CHECKPOINT_FIELDS), "additionalProperties": False},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE},
        "cursor": {"type": "object", "properties": {
            "expected_snapshot_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "checkpoint_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "last_reference_id": {"type": "integer", "minimum": 1}},
            "required": ["expected_snapshot_digest", "checkpoint_digest", "last_reference_id"],
            "additionalProperties": False},
        "after_note_id": {"type": "integer", "minimum": 0},
        "after_chain_seq": {"type": "integer", "minimum": 0},
    }, "required": ["run_id", "item_id"], "additionalProperties": False},
    "annotations": _READ_ONLY,
}


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _safe_text(value: Any, limit: int = 128) -> bool:
    return type(value) is str and 0 < len(value) <= limit and value.isprintable()


def _parse_preview(args: dict[str, Any]) -> dict[str, Any]:
    if (set(args) - {"intent_id", "revision", "canonical_intent_digest", "artifact_sha256", "verification_ref"}
            or not _safe_text(args.get("intent_id")) or not _positive(args.get("revision"))
            or type(args.get("canonical_intent_digest")) is not str
            or HEX.fullmatch(args["canonical_intent_digest"]) is None
            or type(args.get("artifact_sha256")) is not str
            or SHA.fullmatch(args["artifact_sha256"]) is None):
        raise ToolFailure("invalid-arguments", "give an exact intent revision, digest and diff artifact digest")
    ref = args.get("verification_ref")
    if ref is not None and (type(ref) is not dict or set(ref) != {"run_id", "item_id"}
            or type(ref.get("run_id")) is not str or RUN_ID.fullmatch(ref["run_id"]) is None
            or not _safe_text(ref.get("item_id"))):
        raise ToolFailure("invalid-arguments", "verification_ref must name a caller-owned run and evidence item")
    return args


def _parse_delta(args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"run_id", "item_id", "checkpoint", "limit", "cursor", "after_note_id", "after_chain_seq"}
    if (set(args) - allowed or type(args.get("run_id")) is not str
            or RUN_ID.fullmatch(args["run_id"]) is None or not _positive(args.get("item_id"))):
        raise ToolFailure("invalid-arguments", "give an exact run id and explicit work item id")
    limit = args.get("limit", 32)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ToolFailure("invalid-arguments", "limit must be between 1 and 64")
    checkpoint = args.get("checkpoint")
    if checkpoint is not None and (type(checkpoint) is not dict or set(checkpoint) != set(_CHECKPOINT_FIELDS)
            or not _safe_text(checkpoint.get("repo_id")) or checkpoint.get("item_id") != args["item_id"]
            or not _safe_text(checkpoint.get("work_edit_revision"))
            or not _safe_text(checkpoint.get("status_revision"))
            or (checkpoint.get("current_release_digest") is not None
                and (type(checkpoint["current_release_digest"]) is not str
                     or HEX.fullmatch(checkpoint["current_release_digest"]) is None))
            or any(checkpoint.get(k) is not None and (type(checkpoint[k]) is not str
                   or SHA.fullmatch(checkpoint[k]) is None)
                   for k in ("current_decision_digest", "current_reference_set_digest"))):
        raise ToolFailure("invalid-arguments", "checkpoint must use the complete owner checkpoint schema")
    cursor = args.get("cursor")
    if cursor is not None and (type(cursor) is not dict or set(cursor) != {
            "expected_snapshot_digest", "checkpoint_digest", "last_reference_id"}
            or any(type(cursor.get(k)) is not str or SHA.fullmatch(cursor[k]) is None
                   for k in ("expected_snapshot_digest", "checkpoint_digest"))
            or not _positive(cursor.get("last_reference_id"))):
        raise ToolFailure("invalid-arguments", "cursor must bind an owner snapshot, checkpoint and last reference")
    for key in ("after_note_id", "after_chain_seq"):
        value = args.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ToolFailure("invalid-arguments", f"{key} must be a non-negative integer")
    return {**args, "limit": limit}


class _OwnerReads:
    def __init__(self, source: ShellWorkSource) -> None:
        self.source = source
        self._advertised: frozenset[str] | None = None
        self._retry_at = 0.0

    async def available(self, operation: str) -> bool:
        if self._advertised is None and time.monotonic() >= self._retry_at:
            try:
                response = await asyncio.wait_for(self.source._client.get("/api/catalog/v1", timeout=1.0), 1.0)
                body = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
                body = None
            if type(body) is dict and type(body.get("operations")) is list:
                self._advertised = frozenset(row["name"] for row in body["operations"]
                    if type(row) is dict and type(row.get("name")) is str)
            else:
                self._retry_at = time.monotonic() + 3.0
        return self._advertised is not None and operation in self._advertised

    async def available_read(self, operation: str) -> bool:
        try:
            await asyncio.wait_for(self.source._ensure_catalog(), 1.0)
        except (WorkSourceUnavailable, asyncio.TimeoutError):
            return False
        return operation in self.source._read_operations

    async def invoke(self, operation: str, args: dict[str, Any], caller: ForwardedIdentity,
                     result_key: str, expected_schema: str, *, require_read: bool = False) -> dict[str, Any]:
        try:
            if require_read:
                value = await self.source._invoke(operation, args, caller, require_read=True)
            else:
                value = await self.source._invoke(operation, args, caller)
        except WorkSourceUnavailable as error:
            if error.code == "authority-required":
                raise ToolFailure("authority-required", "the current caller lacks an owner read authority") from error
            if error.code in {"item-not-found", "effect-not-found", "run-not-found"}:
                raise ToolFailure("work-not-found", "no authorized owner record was found") from error
            if error.code == "stale-cursor":
                raise ToolFailure("stale-cursor", "owner facts changed; restart this bounded read") from error
            raise ToolFailure("owner-read-unavailable", "the work owner could not supply this read") from error
        if (type(value) is not dict or value.get("repo_id") != caller.repo_id
                or (require_read and set(value) != {"repo_id", result_key})
                or type(value.get(result_key)) is not dict
                or value[result_key].get("schema") != expected_schema):
            raise ToolFailure("owner-response-invalid", "the work owner returned an inconsistent read")
        return value[result_key]


def build_toolset(context: ToolsetContext) -> ToolSet:
    owner = _OwnerReads(context.work_source)

    async def describe_preview():
        return _PREVIEW if await owner.available(ACCEPTANCE_SNAPSHOT) else None

    async def describe_delta():
        return _DELTA if await owner.available(CONTINUATION_DELTA) else None

    async def describe_effect_preview():
        return _EFFECT_PREVIEW if await owner.available_read(EFFECT_PREVIEW) else None

    async def effect_preview(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        value = await owner.invoke(EFFECT_PREVIEW, args, caller, "preview",
                                   "sprintctl-declared-effect-preview/v1", require_read=True)
        return _effect_projection(value, args["intent_id"])

    async def preview(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        return await owner.invoke(ACCEPTANCE_SNAPSHOT, args, caller, "snapshot",
                                  "sprintctl-effect-acceptance-snapshot/v1")

    async def delta(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        return await owner.invoke(CONTINUATION_DELTA, args, caller, "delta",
                                  "sprintctl-work-continuation-delta/v1")

    return ToolSet("owner-read-projections", (
        build_explanation_spec(context.work_source),
        ToolSpec("preview_effect", "effect-read", _EFFECT_PREVIEW, _parse_effect_preview, effect_preview,
                 describe=describe_effect_preview,
                 required_authorities=frozenset({"work:read", "work.effect.get"})),
        ToolSpec("preview_acceptance", "effect-read", _PREVIEW, _parse_preview, preview,
                 describe=describe_preview,
                 required_authorities=frozenset({"work:read", "work.effect.get"})),
        ToolSpec("read_work_delta", "read", _DELTA, _parse_delta, delta,
                 describe=describe_delta, required_authorities=frozenset({"work:read"})),
    ))
