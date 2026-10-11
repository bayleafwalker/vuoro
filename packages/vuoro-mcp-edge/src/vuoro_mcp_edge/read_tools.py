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
HEX = re.compile(r"^[0-9a-f]{64}$")
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
MAX_PAGE = 64

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
              "idempotentHint": True, "openWorldHint": False}

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
        "owner reference set and is refused when its snapshot becomes stale."
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
            or any(type(checkpoint.get(k)) is not str or SHA.fullmatch(checkpoint[k]) is None
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

    async def invoke(self, operation: str, args: dict[str, Any], caller: ForwardedIdentity,
                     result_key: str, expected_schema: str) -> dict[str, Any]:
        try:
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

    async def preview(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        return await owner.invoke(ACCEPTANCE_SNAPSHOT, args, caller, "snapshot",
                                  "sprintctl-effect-acceptance-snapshot/v1")

    async def delta(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        return await owner.invoke(CONTINUATION_DELTA, args, caller, "delta",
                                  "sprintctl-work-continuation-delta/v1")

    return ToolSet("owner-read-projections", (
        build_explanation_spec(context.work_source),
        ToolSpec("preview_acceptance", "effect-read", _PREVIEW, _parse_preview, preview,
                 describe=describe_preview,
                 required_authorities=frozenset({"work:read", "work.effect.get"})),
        ToolSpec("read_work_delta", "read", _DELTA, _parse_delta, delta,
                 describe=describe_delta, required_authorities=frozenset({"work:read"})),
    ))
