"""Caller-bound transport for the released owner's portable P1 projection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from operator_projection import portable_acceptance as portable
from operator_projection import reconstruction as p1

from .errors import WorkSourceUnavailable
from .toolsets import ToolFailure, ToolSpec
from .work_source import ForwardedIdentity, ShellWorkSource

_DESCRIPTION = {
    "name": "explain_acceptance",
    "title": "Explain recorded acceptance links",
    "description": (
        "Read-only explanation of one intent's recorded artifact, verification, "
        "acceptance and effect links, with missing links and unknown currentness. "
        "Requires work and effect-read authorities. Authorizes no effect."
    ),
    "inputSchema": {
        "type": "object", "properties": {
            "intent_id": {"type": "string", "minLength": 1, "maxLength": 128,
                          "pattern": "^[A-Za-z0-9_.:-]+$"},
        }, "required": ["intent_id"], "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": False},
}


def build_spec(source: ShellWorkSource) -> ToolSpec:
    def parse(args: dict[str, Any]) -> dict[str, Any]:
        if (set(args) != {"intent_id"} or type(args.get("intent_id")) is not str
                or not portable.ID.fullmatch(args["intent_id"])):
            raise ToolFailure("invalid-arguments", "give one bounded intent id")
        return args

    async def describe():
        if not callable(getattr(source, "_ensure_catalog", None)):
            return None
        try:
            await asyncio.wait_for(source._ensure_catalog(), timeout=1.0)
        except (WorkSourceUnavailable, asyncio.TimeoutError):
            return None
        return _DESCRIPTION if p1.READ_OPS <= source._read_operations else None

    async def explain(args: dict[str, Any], caller: ForwardedIdentity) -> dict[str, Any]:
        # Availability is not a grant: each invocation still forwards the caller
        # and is independently checked by the existing shell/owner authority.
        if await describe() is None:
            raise ToolFailure("owner-read-unavailable", "required owner reads unavailable")
        capture = {"schema": p1.CAPTURE_SCHEMA, "repo_id": caller.repo_id,
                   "intent_id": args["intent_id"], "source_mode": "live-owner-reads",
                   "catalog_revision": source._catalog_revision, "results": {}}

        async def read(operation: str, arguments: dict[str, Any]):
            row = {"arguments": arguments,
                   "observed_at": datetime.now(timezone.utc).isoformat()}
            try:
                value = await source._invoke(operation, arguments, caller, require_read=True)
            except WorkSourceUnavailable:
                row.update(status="unavailable", reason="owner read refused or unavailable")
                value = None
            else:
                row.update(status="observed", value=value)
            capture["results"][operation] = row
            return value

        effect = await read(p1.EFFECT, {"intent_id": args["intent_id"]})
        if effect is not None:
            intent = effect.get("intent") if type(effect) is dict else None
            item_id = intent.get("item_id") if type(intent) is dict else None
            if (type(effect) is not dict or effect.get("repo_id") != caller.repo_id
                    or type(intent) is not dict or intent.get("intent_id") != args["intent_id"]
                    or type(item_id) is not int or not 1 <= item_id <= portable.OWNER_BIGINT_MAX):
                raise ToolFailure("owner-response-invalid", "inconsistent owner read")
            for operation in (p1.RELEASE, p1.DECISIONS, p1.LEASES):
                await read(operation, {"item_id": item_id})
        try:
            report = p1.reconstruct(capture, live=True)
            presentation = portable.present(report)
            text = portable.render_text(report)
        except (p1.ReconstructionError, KeyError, TypeError, ValueError, OverflowError):
            raise ToolFailure("owner-response-invalid", "inconsistent owner read") from None
        return {"schema": "vuoro-acceptance-explanation/v1",
                "presentation": presentation, "text": text}

    return ToolSpec("explain_acceptance", "effect-read", _DESCRIPTION, parse,
                    explain, describe=describe,
                    required_authorities=frozenset({"work:read"}))
