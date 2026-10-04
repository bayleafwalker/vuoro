"""The one behaviour test every idempotency ledger passes (contract section 5).

The shared `IdempotencyLedger` protocol is keyed by (workspace, principal,
tool, key) (agentops#2520, backlog H1-9).  Each ledger lives with its record
owner, and each must behave exactly like `InMemoryIdempotencyLedger`.  This
module runs the same checks against every implementation the edge has:

- `InMemoryIdempotencyLedger`, the reference;
- the effect intent store's ledger path (`InMemoryIntentStore.lookup` /
  `create`), which writes the intent only when its row wins.

A durable ledger joins through a repository-scoped factory. The PostgreSQL
binding uses the pinned sprintctl owner ledger begin/complete path. Repository
scope is fixed by the binding, while this shared protocol retains the four
caller key parts. Configuring the disposable PostgreSQL URL makes this binding
mandatory; it is not silently skipped on a missing driver or owner.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from vuoro_mcp_edge.effect_tools import EffectIntent, InMemoryIntentStore
from vuoro_mcp_edge.idempotency import (
    IdempotencyLedger,
    InMemoryIdempotencyLedger,
    StoredResult,
    LedgerEntry,
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

    async def begin(self, workspace_id, principal_id, tool, key, request_digest):
        return await self.intents.begin(workspace_id, principal_id, tool, key, request_digest)

    async def complete(self, entry, result):
        self._counter += 1
        intent = EffectIntent(
            intent_id=f"effect_{self._counter:026d}",
            run_id="run_" + "0" * 26,
            binding=RunBinding(principal_id=entry.principal_id, workspace_id=entry.workspace_id, repo_id="r"),
            repository="r", base_commit="0" * 40, title="t", rationale="r", unified_diff="",
        )
        return await self.intents.complete(entry, result, intent)

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


LEDGER_PROVIDERS = sorted(LEDGERS) + (["sprintctl-pg"] if os.environ.get("VUORO_AUTHORITY_TEST_PG_URL") else [])


@pytest.fixture(params=LEDGER_PROVIDERS)
def ledger(request) -> Iterator[IdempotencyLedger]:
    if request.param == "sprintctl-pg":
        from authority_pg_binding import PgLedgerBinding
        binding = PgLedgerBinding(os.environ["VUORO_AUTHORITY_TEST_PG_URL"])
        try:
            yield binding
        finally:
            binding.close()
    else:
        yield LEDGERS[request.param]()


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



@pytest.mark.parametrize("factory", LEDGERS.values(), ids=LEDGERS.keys())
def test_begin_complete_is_the_normative_ledger_entry(factory):
    async def scenario():
        ledger = factory()
        entry = await ledger.begin("w", "p", "tool", "key-0001", _row(1).digest)
        assert entry == LedgerEntry("w", "p", "tool", "key-0001", _row(1).digest, None, False)
        assert await ledger.lookup("w", "p", "tool", "key-0001") is None
        completed = await ledger.complete(entry, {"value": 1})
        assert completed.result == {"value": 1} and completed.replayed is False
        replay = await ledger.begin("w", "p", "tool", "key-0001", _row(1).digest)
        assert replay.result == {"value": 1} and replay.replayed is True
        with pytest.raises(ToolFailure) as error:
            await ledger.begin("w", "p", "tool", "key-0001", _row(2).digest)
        assert error.value.code == "idempotency-conflict"
        with pytest.raises(ValueError):
            await ledger.complete(replay, {"value": 2})
    _run(scenario())



@pytest.mark.skipif(not os.environ.get("VUORO_AUTHORITY_TEST_PG_URL"), reason="disposable owner not configured")
def test_owner_concurrent_transactions_and_reconnect_keep_first_result():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from authority_pg_binding import PgLedgerBinding
    bindings = [PgLedgerBinding(os.environ["VUORO_AUTHORITY_TEST_PG_URL"]) for _ in range(4)]
    repo = bindings[0].owner_store.repo_id
    barrier = Barrier(4, timeout=10)
    try:
        for binding in bindings:
            binding.owner_store.repo_id = repo
            original = binding.owner.begin
            def begin(*args, _original=original):
                barrier.wait()
                return _original(*args)
            binding.owner.begin = begin
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_run, binding.store("w", "p", "t", "race-0001", _row(i)))
                       for i, binding in enumerate(bindings)]
            results = [future.result(timeout=15) for future in futures]
        assert results[0] in [_row(i) for i in range(4)]
        assert all(result == results[0] for result in results)
        for binding in bindings:
            binding.close()
        bindings = []
        fresh = PgLedgerBinding(os.environ["VUORO_AUTHORITY_TEST_PG_URL"])
        try:
            fresh.owner_store.repo_id = repo
            assert _run(fresh.lookup("w", "p", "t", "race-0001")) == results[0]
        finally:
            fresh.close()
    finally:
        for binding in bindings:
            binding.close()



@pytest.mark.parametrize("factory", LEDGERS.values(), ids=LEDGERS.keys())
def test_begin_never_grants_a_second_incomplete_claim_or_mutable_replay(factory):
    async def scenario():
        ledger = factory()
        entry = await ledger.begin("w", "p", "t", "key-0001", _row(1).digest)
        with pytest.raises(ToolFailure) as error:
            await ledger.begin("w", "p", "t", "key-0001", _row(1).digest)
        assert error.value.code == "idempotency-in-progress"
        with pytest.raises(ToolFailure) as conflict:
            await ledger.begin("w", "p", "t", "key-0001", _row(2).digest)
        assert conflict.value.code == "idempotency-conflict"
        assert await ledger.lookup("w", "p", "t", "key-0001") is None
        result = {"nested": {"value": 1}}
        await ledger.complete(entry, result)
        result["nested"]["value"] = 2
        replay = await ledger.begin("w", "p", "t", "key-0001", _row(1).digest)
        assert replay.result == {"nested": {"value": 1}}
        replay.result["nested"]["value"] = 3
        assert (await ledger.begin("w", "p", "t", "key-0001", _row(1).digest)).result == {"nested": {"value": 1}}
    _run(scenario())


@pytest.mark.parametrize("factory", LEDGERS.values(), ids=LEDGERS.keys())
@pytest.mark.parametrize("other", [("w2", "p", "t"), ("w", "p2", "t"), ("w", "p", "t2")])
def test_canonical_begin_scopes_pending_and_completed_keys(factory, other):
    async def scenario():
        ledger = factory()
        first = await ledger.begin("w", "p", "t", "key-0001", _row(1).digest)
        independent = await ledger.begin(*other, "key-0001", _row(2).digest)
        await ledger.complete(independent, {"value": 2})
        await ledger.complete(first, {"value": 1})
        assert (await ledger.begin("w", "p", "t", "key-0001", _row(1).digest)).result == {"value": 1}
        assert (await ledger.begin(*other, "key-0001", _row(2).digest)).result == {"value": 2}
    _run(scenario())


def test_intent_canonical_completion_refusals_leave_no_ledger_result_or_orphan():
    from dataclasses import replace

    async def scenario():
        store = InMemoryIntentStore()
        entry = await store.begin("w", "p", "t", "key-0001", _row(1).digest)
        intent = EffectIntent(
            intent_id="effect_" + "0" * 26, run_id="run_" + "0" * 26,
            binding=RunBinding(principal_id="p", workspace_id="w", repo_id="r"),
            repository="r", base_commit="0" * 40, title="t", rationale="r", unified_diff="",
        )
        for binding in [replace(intent.binding, principal_id="other"), replace(intent.binding, workspace_id="other")]:
            with pytest.raises(ValueError, match="^intent binding differs from the begun ledger entry$"):
                await store.complete(entry, {"value": 1}, replace(intent, binding=binding))
            assert await store.lookup("w", "p", "t", "key-0001") is None
            assert store._intents == {}
        with pytest.raises(ValueError, match="^no incomplete entry was begun for this completion$"):
            await store.complete(replace(entry, request_digest=_row(2).digest), {"value": 2}, intent)
        assert store._intents == {}
        store.seed(intent)
        with pytest.raises(ValueError, match="^intent ID is already present$"):
            await store.complete(entry, {"value": 1}, intent)
        assert await store.lookup("w", "p", "t", "key-0001") is None
        new_intent = replace(intent, intent_id="effect_" + "1" * 26)
        completed = await store.complete(entry, {"value": 1}, new_intent)
        assert completed.result == {"value": 1}
        replay = await store.begin("w", "p", "t", "key-0001", _row(1).digest)
        with pytest.raises(ValueError, match="^no incomplete entry was begun for this completion$"):
            await store.complete(replay, {"value": 1}, replace(intent, intent_id="effect_" + "2" * 26))
        assert store._intents == {intent.intent_id: intent, new_intent.intent_id: new_intent}
        assert (await store.lookup("w", "p", "t", "key-0001")).result == {"value": 1}
    _run(scenario())



@pytest.mark.skipif(not os.environ.get("VUORO_AUTHORITY_TEST_PG_URL"), reason="disposable owner not configured")
def test_owner_canonical_entry_rollback_and_snapshot_are_durable():
    from authority_pg_binding import PgLedgerBinding
    binding = PgLedgerBinding(os.environ["VUORO_AUTHORITY_TEST_PG_URL"])
    class Abort(Exception):
        pass
    try:
        with pytest.raises(Abort):
            with binding.owner_store.conn.transaction():
                entry = binding.owner.begin("w", "p", "t", "rollback-0001", _row(1).digest)
                assert vars(entry) == vars(LedgerEntry("w", "p", "t", "rollback-0001", _row(1).digest, None, False))
                binding.owner.complete(entry, {"nested": {"value": 1}})
                raise Abort()
        assert _run(binding.lookup("w", "p", "t", "rollback-0001")) is None
        with binding.owner_store.conn.transaction():
            entry = binding.owner.begin("w", "p", "t", "rollback-0001", _row(2).digest)
            assert entry.result is None and entry.replayed is False
            binding.owner.complete(entry, {"nested": {"value": 2}})
        with binding.owner_store.conn.transaction():
            replay = binding.owner.begin("w", "p", "t", "rollback-0001", _row(2).digest)
            assert replay.replayed is True
            replay.result["nested"]["value"] = 3
        with binding.owner_store.conn.transaction():
            assert binding.owner.begin("w", "p", "t", "rollback-0001", _row(2).digest).result == {"nested": {"value": 2}}
    finally:
        binding.close()
