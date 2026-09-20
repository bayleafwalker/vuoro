"""The self-hosted Managed Agents poller (agentops#2469).

Polls a Managed Agents work queue on a host you control, and executes the
matched custom tool locally against the internal MCP server -- the
`http.internal:8000/mcp`-style pattern from the edge doc's §2. No public
listener is opened anywhere in this module; the only outbound calls are (a)
to the queue client (Anthropic's Managed Agents/Messages API, over the
open internet, outbound-only) and (b) to the internal MCP server on the
same host or private network.

Designed for the four constraints (edge doc §2):

1. **Sessions expire while waiting.** Every dispatch is one fast internal
   MCP round trip; nothing here blocks on model output or human approval.
2. **Scheduled tasks may start without connectors.** `verify_tools_available`
   runs before every dispatch and every poll cycle skips (not crashes) when
   the internal MCP server -- this poller's own dependency, not a vendor
   connector -- is unreachable or missing an expected tool. Every operation
   is safe to call twice: idempotency is enforced at two layers, the
   task-id ledger here and the lease/claim semantics underneath.
3. **Both protocol eras.** Delegated to `mcp_server.py`/`mcp_client.py`.
4. **Runs on a host you control.** No code here binds a public port; see
   `deploy/poller/README.md` for the network posture this module assumes.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import time
from typing import Any, Protocol

from vuoro_worker.custom_tools import CustomTool
from vuoro_worker.mcp_client import MCPClient, MCPClientError


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    tool_name: str
    arguments: Mapping[str, Any]


class QueueClient(Protocol):
    """The poller's view of Managed Agents' work queue. Implementations are
    outward-facing (they hold the vendor bearer credential and talk to the
    real API) and are deliberately not exercised in this repository's test
    suite -- `FakeQueueClient` below stands in for them."""

    def poll(self) -> ScheduledTask | None: ...

    def submit_result(self, task_id: str, result: Mapping[str, Any] | None, *, error: str | None = None) -> None: ...


class FakeQueueClient:
    """An in-memory `QueueClient` for tests and local dry runs. Tasks are
    consumed in the order given; `submitted` records every result for
    assertions."""

    def __init__(self, tasks: list[ScheduledTask] | None = None) -> None:
        self._tasks = list(tasks or [])
        self.submitted: list[tuple[str, Mapping[str, Any] | None, str | None]] = []

    def enqueue(self, task: ScheduledTask) -> None:
        self._tasks.append(task)

    def poll(self) -> ScheduledTask | None:
        if not self._tasks:
            return None
        return self._tasks.pop(0)

    def submit_result(self, task_id: str, result: Mapping[str, Any] | None, *, error: str | None = None) -> None:
        self.submitted.append((task_id, result, error))


@dataclass(frozen=True)
class PollOutcome:
    status: str  # "empty" | "skipped_unavailable" | "executed" | "idempotent_replay" | "error"
    task_id: str | None = None
    detail: str | None = None


class Poller:
    """One poll/dispatch cycle plus the run loop. `custom_tools` is keyed
    by name once at construction so dispatch is O(1) and cannot silently
    fall through to a wrong tool."""

    def __init__(
        self,
        *,
        queue_client: QueueClient,
        mcp_client: MCPClient,
        custom_tools: tuple[CustomTool, ...],
        idempotency_ledger_size: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue = queue_client
        self._mcp = mcp_client
        self._tools = {tool.name: tool for tool in custom_tools}
        self._ledger: "OrderedDict[str, Mapping[str, Any]]" = OrderedDict()
        self._ledger_size = idempotency_ledger_size
        self._clock = clock

    def verify_tools_available(self) -> bool:
        """Preflight check (constraint 2): confirm the internal MCP server
        answers and offers every tool this poller was built to dispatch.
        Never raises -- callers get a bool and act on it."""
        try:
            available = {descriptor.name for descriptor in self._mcp.discover()}
        except MCPClientError as error:
            LOGGER.warning("internal MCP server unavailable: %s", error)
            return False
        missing = set(self._tools) - available
        if missing:
            LOGGER.warning("internal MCP server is missing tools: %s", sorted(missing))
            return False
        return True

    def _remember(self, task_id: str, result: Mapping[str, Any]) -> None:
        self._ledger[task_id] = result
        while len(self._ledger) > self._ledger_size:
            self._ledger.popitem(last=False)

    def run_once(self) -> PollOutcome:
        task = self._queue.poll()
        if task is None:
            return PollOutcome(status="empty")

        if task.task_id in self._ledger:
            # A retried/duplicated scheduled task (constraint 2): report the
            # cached result rather than re-executing a non-idempotent-by-
            # itself operation a second time.
            cached = self._ledger[task.task_id]
            self._queue.submit_result(task.task_id, cached)
            return PollOutcome(status="idempotent_replay", task_id=task.task_id)

        if not self.verify_tools_available():
            # Do not ack, do not fail permanently -- the next poll cycle
            # tries again once connectors/the internal server are back.
            return PollOutcome(status="skipped_unavailable", task_id=task.task_id)

        tool = self._tools.get(task.tool_name)
        if tool is None:
            message = f"no such custom tool: {task.tool_name!r}"
            self._queue.submit_result(task.task_id, None, error=message)
            return PollOutcome(status="error", task_id=task.task_id, detail=message)

        try:
            result = tool.handler(task.arguments)
        except MCPClientError as error:
            self._queue.submit_result(task.task_id, None, error=str(error))
            return PollOutcome(status="error", task_id=task.task_id, detail=str(error))

        self._remember(task.task_id, result)
        self._queue.submit_result(task.task_id, result)
        return PollOutcome(status="executed", task_id=task.task_id)

    def run_forever(
        self,
        *,
        poll_interval_seconds: float,
        should_continue: Callable[[], bool] = lambda: True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        while should_continue():
            outcome = self.run_once()
            if outcome.status == "empty":
                sleep(poll_interval_seconds)
