"""The sprintctl public-work contract (v1), as this edge consumes it.

Reference: sprintctl ``docs/reference/work-public-contract.md`` (sprintctl
main 9f92190).  Two operations, one envelope shape, two exact record shapes.

Validation here is strict in both directions: a record whose key set is not
exactly the contract's field set is refused, whether a key is missing or an
extra one appeared.  An upstream that starts leaking ``description`` (or any
other never-emit field) fails the tool call instead of reaching the client;
widening what the edge emits is a deliberate edit to the constants below and
to a test, never a side effect of the upstream changing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .errors import WorkSourceUnavailable

__all__ = [
    "AUTHORITY",
    "ITEM_FIELDS",
    "LIST_ITEM_FIELDS",
    "OPERATION_ITEM",
    "OPERATION_LIST",
    "REQUIRED_OPERATIONS",
    "STATUSES",
    "TITLE_MAX_LENGTH",
    "validate_item_result",
    "validate_list_result",
]

LOGGER = logging.getLogger(__name__)

#: Registered operation names.  The adapter kit forbids ``/`` in a name, so
#: the design's ``work.public.list/v1`` registers as ``work.public.list-v1``.
OPERATION_LIST = "work.public.list-v1"
OPERATION_ITEM = "work.public.item-v1"
REQUIRED_OPERATIONS: tuple[str, ...] = (OPERATION_LIST, OPERATION_ITEM)

AUTHORITY = "sprintctl"
TITLE_MAX_LENGTH = 160
STATUSES: frozenset[str] = frozenset({"pending", "active", "done", "blocked"})

#: Exact list-record key set.
LIST_ITEM_FIELDS: frozenset[str] = frozenset(
    {"work_id", "title", "priority", "status", "blocked", "updated_at"}
)
#: Exact item-record key set: the list record plus three detail fields.
ITEM_FIELDS: frozenset[str] = LIST_ITEM_FIELDS | {
    "created_at",
    "resolution",
    "blocked_by",
}

_LIST_ENVELOPE_FIELDS = frozenset({"authority", "as_of", "state", "items"})
_ITEM_ENVELOPE_FIELDS = frozenset({"authority", "as_of", "state", "item"})


def _invalid(operation: str, message: str) -> WorkSourceUnavailable:
    return WorkSourceUnavailable("invalid-response", f"{operation}: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], operation: str, what: str
) -> None:
    keys = set(value)
    extra = keys - expected
    missing = expected - keys
    if extra or missing:
        # Key names go to the log, not to the client: an extra key is exactly
        # the thing that must not cross this boundary, and neither its value
        # nor a hint of it belongs in a tool result.
        LOGGER.error(
            "upstream %s %s key set violates the public-work contract",
            operation,
            what,
            extra={"extra_keys": sorted(extra), "missing_keys": sorted(missing)},
        )
        raise WorkSourceUnavailable(
            "contract-violation",
            f"{operation}: upstream {what} does not match the public-work "
            f"contract ({len(extra)} undeclared key(s), {len(missing)} missing)",
        )


def _check_envelope(
    result: Any, expected: frozenset[str], operation: str
) -> Mapping[str, Any]:
    if not isinstance(result, dict):
        raise _invalid(operation, f"result is {type(result).__name__}, expected an object")
    _exact_keys(result, expected, operation, "envelope")
    if result["authority"] != AUTHORITY:
        raise _invalid(operation, "result authority is not sprintctl")
    if result["state"] != "ok":
        # The contract says sprintctl never emits a non-ok payload; an
        # unavailable authority arrives as a rejection.  If one arrives
        # anyway it is an error, never an empty answer.
        raise WorkSourceUnavailable(
            "authority-unavailable", f"{operation}: work authority state is not ok"
        )
    if not isinstance(result["as_of"], str) or not result["as_of"]:
        raise _invalid(operation, "as_of is not a timestamp string")
    return result


def _check_list_fields(record: Mapping[str, Any], operation: str) -> None:
    work_id = record["work_id"]
    if not _is_int(work_id) or work_id < 1:
        raise _invalid(operation, "work_id is not a positive integer")
    title = record["title"]
    if not isinstance(title, str) or not title or len(title) > TITLE_MAX_LENGTH:
        raise _invalid(operation, "title is not a string of 1..160 characters")
    priority = record["priority"]
    if priority is not None and not _is_int(priority):
        raise _invalid(operation, "priority is not an integer or null")
    if record["status"] not in STATUSES:
        raise _invalid(operation, "status is outside the contract vocabulary")
    if not isinstance(record["blocked"], bool):
        raise _invalid(operation, "blocked is not a boolean")
    if not isinstance(record["updated_at"], str):
        raise _invalid(operation, "updated_at is not a string")


def _check_list_record(record: Any, operation: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise _invalid(operation, "list record is not an object")
    _exact_keys(record, LIST_ITEM_FIELDS, operation, "record")
    _check_list_fields(record, operation)
    return record


def _check_item_record(record: Any, operation: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise _invalid(operation, "item record is not an object")
    _exact_keys(record, ITEM_FIELDS, operation, "record")
    _check_list_fields(record, operation)
    if not isinstance(record["created_at"], str):
        raise _invalid(operation, "created_at is not a string")
    resolution = record["resolution"]
    if resolution is not None and not isinstance(resolution, str):
        raise _invalid(operation, "resolution is not a string or null")
    blocked_by = record["blocked_by"]
    if not isinstance(blocked_by, list) or not all(
        _is_int(value) and value >= 1 for value in blocked_by
    ):
        raise _invalid(operation, "blocked_by is not an array of work ids")
    return record


def validate_list_result(result: Any) -> dict[str, Any]:
    """Return ``{authority, as_of, items}`` with every record validated."""

    envelope = _check_envelope(result, _LIST_ENVELOPE_FIELDS, OPERATION_LIST)
    items = envelope["items"]
    if not isinstance(items, list):
        raise _invalid(OPERATION_LIST, "items is not an array")
    return {
        "authority": envelope["authority"],
        "as_of": envelope["as_of"],
        "items": [_check_list_record(record, OPERATION_LIST) for record in items],
    }


def validate_item_result(result: Any) -> dict[str, Any]:
    """Return ``{authority, as_of, item}`` with the record validated."""

    envelope = _check_envelope(result, _ITEM_ENVELOPE_FIELDS, OPERATION_ITEM)
    return {
        "authority": envelope["authority"],
        "as_of": envelope["as_of"],
        "item": _check_item_record(envelope["item"], OPERATION_ITEM),
    }
