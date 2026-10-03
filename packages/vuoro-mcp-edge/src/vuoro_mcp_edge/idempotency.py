"""Idempotency for write-class tools (E2 and E3 share these rules).

Every write-class tool takes a required `idempotency_key` argument.  The
ledger is keyed by (workspace, principal, tool, key) and stores the digest
of the request's other arguments together with the first result:

- same key, same digest  -> the stored result, and no second effect;
- same key, other digest -> `ToolFailure("idempotency-conflict", ...)`.

The digest is sha256 over canonical JSON (sorted keys, no whitespace, UTF-8)
of the arguments without `idempotency_key`, prefixed by the tool name, so the
same key reused on another tool never collides.

The principal is part of the key (contract section 5, amended 2026-09-26;
protocol changed by agentops#2520): two principals in one workspace never
share a key space, so one cannot replay another's stored result or probe
which keys exist.

Durable ledgers live with the record owner that performs the write (E2: the
run/evidence owner; E2b: the lease owner; E3: the intent store), not in the
edge.  `InMemoryIdempotencyLedger` is the reference behaviour for tests, and
every implementation passes the same behaviour test
(tests/test_ledger_behaviour.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
import copy
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
    "LedgerEntry",
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


@dataclass(frozen=True)
class LedgerEntry:
    """Canonical begin/complete value; completed reads are fresh snapshots.

    A fresh begin has no result and replayed=False. A begin after completion
    returns the first result with replayed=True. Repository scope belongs to
    the provider binding, outside this workspace/principal/tool/key tuple.
    """

    workspace_id: str
    principal_id: str
    tool: str
    key: str
    request_digest: str
    result: Mapping[str, Any] | None
    replayed: bool


class IdempotencyLedger(Protocol):
    async def begin(self, workspace_id: str, principal_id: str, tool: str,
                    key: str, request_digest: str) -> LedgerEntry: ...

    async def complete(self, entry: LedgerEntry, result: Mapping[str, Any]) -> LedgerEntry: ...

    async def lookup(
        self, workspace_id: str, principal_id: str, tool: str, key: str
    ) -> StoredResult | None:
        """The stored result for this principal's key, if any."""

    async def store(
        self, workspace_id: str, principal_id: str, tool: str, key: str, stored: StoredResult
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
        self._rows: dict[tuple[str, str, str, str], StoredResult] = {}
        self._pending: dict[tuple[str, str, str, str], LedgerEntry] = {}

    async def begin(self, workspace_id: str, principal_id: str, tool: str,
                    key: str, request_digest: str) -> LedgerEntry:
        scope = (workspace_id, principal_id, tool, key)
        row = self._rows.get(scope)
        if row is not None:
            replay_or_conflict(row, request_digest)
            return LedgerEntry(*scope, request_digest, copy.deepcopy(row.result), True)
        pending = self._pending.get(scope)
        if pending is not None:
            if pending.request_digest != request_digest:
                replay_or_conflict(StoredResult(pending.request_digest, {}), request_digest)
            # This test reference has no surrounding database transaction to
            # wait for. Never grant another claimant an incomplete entry.
            raise ToolFailure("idempotency-in-progress", "this logical operation is incomplete")
        entry = LedgerEntry(*scope, request_digest, None, False)
        self._pending[scope] = entry
        return entry

    def _complete(self, entry: LedgerEntry, stored: StoredResult) -> LedgerEntry:
        scope = (entry.workspace_id, entry.principal_id, entry.tool, entry.key)
        if entry.replayed or self._pending.get(scope) != entry or stored.digest != entry.request_digest:
            raise ValueError("no incomplete entry was begun for this completion")
        self._rows[scope] = stored
        del self._pending[scope]
        return LedgerEntry(*scope, entry.request_digest, copy.deepcopy(stored.result), False)

    async def complete(self, entry: LedgerEntry, result: Mapping[str, Any]) -> LedgerEntry:
        return self._complete(entry, StoredResult(entry.request_digest, copy.deepcopy(result)))


    async def lookup(
        self, workspace_id: str, principal_id: str, tool: str, key: str
    ) -> StoredResult | None:
        return self._rows.get((workspace_id, principal_id, tool, key))

    async def store(
        self, workspace_id: str, principal_id: str, tool: str, key: str, stored: StoredResult
    ) -> StoredResult:
        # Thin compatibility adapter: store still returns the first row even
        # for a conflicting racer; its caller maps that row to the wire refusal.
        previous = await self.lookup(workspace_id, principal_id, tool, key)
        if previous is not None:
            return previous
        entry = await self.begin(workspace_id, principal_id, tool, key, stored.digest)
        self._complete(entry, stored)
        return stored
