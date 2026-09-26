"""Idempotency for write-class tools (E2 and E3 share these rules).

Every write-class tool takes a required `idempotency_key` argument.  The
ledger is keyed by (workspace, tool, key) and stores the digest of the
request's other arguments together with the first result:

- same key, same digest  -> the stored result, and no second effect;
- same key, other digest -> `ToolFailure("idempotency-conflict", ...)`.

The digest is sha256 over canonical JSON (sorted keys, no whitespace, UTF-8)
of the arguments without `idempotency_key`, prefixed by the tool name, so the
same key reused on another tool never collides.

Durable ledgers live with the record owner that performs the write (E2: the
run/evidence owner; E3: the intent store), not in the edge.
`InMemoryIdempotencyLedger` is the reference behaviour for tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
import hashlib
import json
import re

from .toolsets import ToolFailure

__all__ = [
    "IDEMPOTENCY_KEY",
    "IDEMPOTENCY_KEY_SCHEMA",
    "IdempotencyLedger",
    "InMemoryIdempotencyLedger",
    "StoredResult",
    "replay_or_conflict",
    "request_digest",
    "require_key",
]

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

#: Drop into a write tool's `inputSchema.properties` (and `required`).
IDEMPOTENCY_KEY_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": IDEMPOTENCY_KEY.pattern,
    "description": (
        "Caller-chosen key, 8-128 characters of A-Z a-z 0-9 . _ : -. Retrying "
        "with the same key and the same arguments returns the first result "
        "and has no second effect; the same key with different arguments is "
        "refused with idempotency-conflict."
    ),
}


def require_key(arguments: Mapping[str, Any]) -> str:
    """The validated `idempotency_key`, or `ToolFailure("invalid-arguments")`."""

    key = arguments.get("idempotency_key")
    if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key):
        raise ToolFailure(
            "invalid-arguments",
            "idempotency_key is required: 8-128 characters of A-Z a-z 0-9 . _ : -",
        )
    return key


def request_digest(tool: str, arguments: Mapping[str, Any]) -> str:
    """sha256 of the tool name and canonical JSON of the non-key arguments."""

    body = {name: value for name, value in arguments.items() if name != "idempotency_key"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{tool}\n{canonical}".encode()).hexdigest()


@dataclass(frozen=True)
class StoredResult:
    digest: str
    result: Mapping[str, Any]


class IdempotencyLedger(Protocol):
    async def lookup(self, workspace_id: str, tool: str, key: str) -> StoredResult | None:
        """The stored result for this key, if any."""

    async def store(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult
    ) -> StoredResult:
        """Record the first result atomically; if another writer won the race,
        return theirs (the caller must then compare digests)."""


def replay_or_conflict(stored: StoredResult | None, digest: str) -> Mapping[str, Any] | None:
    """The result to replay, `None` to proceed, or an idempotency conflict."""

    if stored is None:
        return None
    if stored.digest != digest:
        raise ToolFailure(
            "idempotency-conflict",
            "this idempotency_key was already used with different arguments",
        )
    return stored.result


class InMemoryIdempotencyLedger:
    """Reference behaviour for tests.  Not durable; never deploy it."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], StoredResult] = {}

    async def lookup(self, workspace_id: str, tool: str, key: str) -> StoredResult | None:
        return self._rows.get((workspace_id, tool, key))

    async def store(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult
    ) -> StoredResult:
        return self._rows.setdefault((workspace_id, tool, key), stored)
