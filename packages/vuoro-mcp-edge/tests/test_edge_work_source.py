"""The catalog check under concurrency: loaded is free, in-flight is shared."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from edge_support import REQUEST_ID, accepted, list_record, list_result
from vuoro_mcp_edge.errors import WorkSourceUnavailable
from vuoro_mcp_edge.work_source import ForwardedIdentity, ShellWorkSource

IDENTITY = ForwardedIdentity(assertion="a.b.c", request_id=REQUEST_ID, repo_id="repo-a")
CATALOG = {
    "revision": "rev-1",
    "operations": [{"name": "work.public.list-v1"}, {"name": "work.public.item-v1"}],
}


class _GatedShell:
    """Catalog responses wait on a gate, so calls overlap deterministically."""

    def __init__(self, *catalog_responses: httpx.Response) -> None:
        self.gate = asyncio.Event()
        self.catalog_responses = list(catalog_responses)
        self.catalog_calls = 0
        self.invoke_calls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/catalog/v1":
            self.catalog_calls += 1
            await self.gate.wait()
            return self.catalog_responses.pop(0)
        self.invoke_calls += 1
        return accepted(list_result(list_record(1)))


def _source(shell: _GatedShell) -> ShellWorkSource:
    return ShellWorkSource(transport=httpx.MockTransport(shell))


def test_concurrent_callers_share_one_catalog_fetch() -> None:
    async def scenario() -> None:
        shell = _GatedShell(httpx.Response(200, json=CATALOG))
        source = _source(shell)
        calls = [asyncio.create_task(source.list_work(IDENTITY)) for _ in range(5)]
        await asyncio.sleep(0)
        shell.gate.set()
        results = await asyncio.gather(*calls)
        assert all(len(result["items"]) == 1 for result in results)
        assert shell.catalog_calls == 1
        assert shell.invoke_calls == 5
        await source.aclose()

    asyncio.run(scenario())


def test_a_failed_fetch_fails_every_waiter_at_once_and_the_next_call_refetches() -> None:
    async def scenario() -> None:
        shell = _GatedShell(httpx.Response(503), httpx.Response(200, json=CATALOG))
        source = _source(shell)
        calls = [asyncio.create_task(source.list_work(IDENTITY)) for _ in range(4)]
        await asyncio.sleep(0)
        shell.gate.set()
        outcomes = await asyncio.gather(*calls, return_exceptions=True)
        assert all(isinstance(o, WorkSourceUnavailable) for o in outcomes)
        assert {o.code for o in outcomes} == {"catalog-unavailable"}
        # One fetch for all four waiters: they did not queue up and retry.
        assert shell.catalog_calls == 1
        assert shell.invoke_calls == 0
        result = await source.list_work(IDENTITY)
        assert len(result["items"]) == 1
        assert shell.catalog_calls == 2
        await source.aclose()

    asyncio.run(scenario())


def test_a_loaded_catalog_is_not_fetched_again() -> None:
    async def scenario() -> None:
        shell = _GatedShell(httpx.Response(200, json=CATALOG))
        shell.gate.set()
        source = _source(shell)
        for _ in range(3):
            await source.list_work(IDENTITY)
        assert shell.catalog_calls == 1
        await source.aclose()

    asyncio.run(scenario())


def test_one_cancelled_caller_does_not_cancel_the_shared_fetch() -> None:
    async def scenario() -> None:
        shell = _GatedShell(httpx.Response(200, json=CATALOG))
        source = _source(shell)
        first = asyncio.create_task(source.list_work(IDENTITY))
        second = asyncio.create_task(source.list_work(IDENTITY))
        await asyncio.sleep(0)
        first.cancel()
        shell.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert len((await second)["items"]) == 1
        assert shell.catalog_calls == 1
        await source.aclose()

    asyncio.run(scenario())
