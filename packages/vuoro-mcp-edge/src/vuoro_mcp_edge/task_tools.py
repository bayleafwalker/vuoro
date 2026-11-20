"""Task creation through the workspace's durable work owner."""

from __future__ import annotations

import re
from typing import Any

from .idempotency import IDEMPOTENCY_KEY_SCHEMA, require_key
from .record_tools import SprintctlRecordStore
from .toolsets import ToolFailure, ToolSet, ToolSpec, ToolsetContext, WRITE_ANNOTATIONS
from .work_source import ForwardedIdentity

OPERATION = "work.item.create-v1"
_TEXT_FIELDS = ("track_name", "title", "description")
_DEFINITION = {
    "name": "create_task",
    "title": "Create a task",
    "description": "Create a task in an existing sprint in the connected repository. Reuse the same idempotency key when retrying.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "sprint_id": {"type": "integer", "minimum": 1},
            **{name: {"type": "string", "minLength": 1} for name in _TEXT_FIELDS},
            "require_review": {
                "type": "boolean", "description": "Require independent browser acceptance of the outcome.",
            },
            "priority": {"type": "integer", "minimum": 1, "maximum": 9},
            "idempotency_key": IDEMPOTENCY_KEY_SCHEMA,
        },
        "required": ["sprint_id", "track_name", "title", "idempotency_key"],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}


def _parse(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) - _DEFINITION["inputSchema"]["properties"].keys():
        raise ToolFailure("invalid-arguments", "Unexpected task field")
    sprint_id = arguments.get("sprint_id")
    if type(sprint_id) is not int or not 1 <= sprint_id <= 2**63 - 1:
        raise ToolFailure("invalid-arguments", "sprint_id must be a positive integer")
    for field in _TEXT_FIELDS:
        if field == "description" and field not in arguments:
            continue
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ToolFailure("invalid-arguments", f"{field} must contain text without NUL bytes")
    if "priority" in arguments:
        value = arguments["priority"]
        if type(value) is not int or not 1 <= value <= 9:
            raise ToolFailure("invalid-arguments", "priority must be an integer between 1 and 9")
    if "require_review" in arguments and type(arguments["require_review"]) is not bool:
        raise ToolFailure("invalid-arguments", "require_review must be a boolean")
    require_key(arguments)
    return arguments


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    store = context.runs
    if not isinstance(store, SprintctlRecordStore):
        return None

    async def describe() -> dict[str, Any] | None:
        return _DEFINITION if await store.operation_available(OPERATION) else None

    async def run(arguments: dict[str, Any], forwarded: ForwardedIdentity) -> dict[str, Any]:
        result = await store.shell_client.invoke(
            OPERATION, arguments, forwarded, idempotency_key=arguments["idempotency_key"]
        )
        item = result.get("item") if isinstance(result, dict) else None
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] < 1:
            raise ToolFailure("owner-response-invalid", "The work owner returned no task ID")
        task = {"item_id": item["id"]}
        if arguments.get("require_review"):
            digest = result.get("review_release_digest")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ToolFailure("owner-response-invalid", "The work owner returned no review release")
            task["review_release_digest"] = digest
        return task

    return ToolSet(
        "task",
        (ToolSpec("create_task", "task-create", _DEFINITION, _parse, run, describe),),
    )
