"""The one behaviour test every idempotency ledger passes (contract section 5).

The shared `IdempotencyLedger` protocol is keyed by (workspace, principal,
tool, key) (agentops#2520, backlog H1-9).  Each ledger lives with its record
owner, and each must behave exactly like `InMemoryIdempotencyLedger`.  This
module runs the same checks against every implementation the edge has:

- `InMemoryIdempotencyLedger`, the reference;
- the effect intent store's ledger path (`InMemoryIntentStore.lookup` /
  `create`), which writes the intent only when its row wins.

A durable ledger joins by adding a factory to `LEDGERS`.  The lease owner's
ledger (sprintctl `work_idempotency_ledger`) is not this protocol: its key
also carries the repo, (repo, workspace, principal, tool, key).  It is proved
on the sprintctl side against real PostgreSQL; the edge's claim toolset adds
its client-side view here when it lands.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from vuoro_mcp_edge.effect_tools import EffectIntent, InMemoryIntentStore
from vuoro_mcp_edge.idempotency import (
    IdempotencyLedger,
    InMemoryIdempotencyLedger,
    StoredResult,
    replay_or_conflict,
    request_digest,
)
from vuoro_mcp_edge.runs import RunBinding
from vuoro_mcp_edge.toolsets import ToolFailure


class _IntentStoreLedger:
    """The intent store seen through the ledger protocol: `store` is a
    `create` carrying a fresh intent, which must exist only if it won."""

    def __init__(self) -> None:
        self.intents = InMemoryIntentStore()
        self._counter = 0

    async def lookup(self, workspace_id: str, principal_id: str, tool: str, key: str):
        return await self.intents.lookup(workspace_id, principal_id, tool, key)

    async def store(self, workspace_id: str, principal_id: str, tool: str, key: str, stored):
        self._counter += 1
        intent = EffectIntent(
            intent_id=f"effect_{self._counter:026d}",
            run_id="run_" + "0" * 26,
            binding=RunBinding(principal_id=principal_id, workspace_id=workspace_id, repo_id="r"),
            repository="r",
            base_commit="0" * 40,
            title="t",
            rationale="r",
            unified_diff="",
        )
        winner = await self.intents.create(workspace_id, principal_id, tool, key, stored, intent)
        assert (intent.intent_id in self.intents._intents) == (winner is stored)
        return winner


LEDGERS: dict[str, Callable[[], IdempotencyLedger]] = {
    "in-memory": InMemoryIdempotencyLedger,
    "intent-store": _IntentStoreLedger,
}


def _row(value: Any, tool: str = "t") -> StoredResult:
    return StoredResult(request_digest(tool, {"value": value}), {"value": value})


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(params=sorted(LEDGERS))
def ledger(request) -> IdempotencyLedger:
    return LEDGERS[request.param]()


def test_an_unknown_key_has_nothing_stored(ledger) -> None:
    assert _run(ledger.lookup("w", "p", "t", "key-0001")) is None


def test_the_first_write_wins_and_a_racer_gets_it_back(ledger) -> None:
    async def scenario():
        first = await ledger.store("w", "p", "t", "key-0001", _row(1))
        racer = await ledger.store("w", "p", "t", "key-0001", _row(2))
        return first, racer, await ledger.lookup("w", "p", "t", "key-0001")

    first, racer, found = _run(scenario())
    assert racer == first == found == _row(1)


def test_same_digest_replays_and_another_digest_conflicts(ledger) -> None:
    _run(ledger.store("w", "p", "t", "key-0001", _row(1)))
    found = _run(ledger.lookup("w", "p", "t", "key-0001"))
    assert replay_or_conflict(found, _row(1).digest) == {"value": 1}
    with pytest.raises(ToolFailure) as conflict:
        replay_or_conflict(found, _row(2).digest)
    assert conflict.value.code == "idempotency-conflict"


@pytest.mark.parametrize(
    "other",
    [("w2", "p", "t"), ("w", "p2", "t"), ("w", "p", "t2")],
    ids=["another-workspace", "another-principal", "another-tool"],
)
def test_workspace_principal_and_tool_each_scope_the_key(ledger, other) -> None:
    """Another principal in the same workspace neither sees nor collides with
    the first principal's key (the section 5 amendment)."""

    async def scenario():
        await ledger.store("w", "p", "t", "key-0001", _row(1))
        seen = await ledger.lookup(*other, "key-0001")
        own = await ledger.store(*other, "key-0001", _row(2))
        return seen, own

    seen, own = _run(scenario())
    assert seen is None
    assert own == _row(2)
    assert _run(ledger.lookup("w", "p", "t", "key-0001")) == _row(1)


def test_eight_gathered_writers_of_one_key_agree_on_one_winner(ledger) -> None:
    """First write wins across eight `store` calls on one event loop.

    This is not a race test for the in-memory ledgers: neither awaits between
    its lookup and its insert, so `gather` runs them one after another.  It
    exercises an interleaving only for a ledger that yields inside `store`,
    and says nothing about writers in separate processes, which a durable
    ledger proves against its own database.  Winners are compared by value,
    so a ledger that rebuilds the row it read back passes.
    """

    async def scenario():
        return await asyncio.gather(
            *(ledger.store("w", "p", "t", "key-race-1", _row(i)) for i in range(8))
        )

    winners = _run(scenario())
    assert winners[0] in [_row(i) for i in range(8)]
    assert all(w == winners[0] for w in winners)
    assert _run(ledger.lookup("w", "p", "t", "key-race-1")) == winners[0]


def test_parts_that_would_collide_if_joined_stay_distinct(ledger) -> None:
    """Guards a durable ledger that concatenates its key parts."""

    async def scenario():
        await ledger.store("w|x", "p", "t", "key-0001", _row(1))
        return await ledger.lookup("w", "x|p", "t", "key-0001")

    assert _run(scenario()) is None
