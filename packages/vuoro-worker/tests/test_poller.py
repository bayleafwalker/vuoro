from __future__ import annotations

import httpx
import pytest

from vuoro_service.identity import Identity

from vuoro_worker.custom_tools import build_custom_tools
from vuoro_worker.internal_tools import InternalToolServer, StaticWorkSource, WorkItemDetail
from vuoro_worker.mcp_client import MCPClient, MCPClientError, ToolDescriptor
from vuoro_worker.mcp_server import create_mcp_app
from vuoro_worker.poller import FakeQueueClient, Poller, ScheduledTask


TOKEN = "test-bearer-token"


@pytest.fixture()
def live_mcp_client(run_app) -> MCPClient:
    server = InternalToolServer(
        identities={TOKEN: Identity(actor="worker-a", environment="test", repo_ids=frozenset({"*"}))},
        work_source=StaticWorkSource(
            [WorkItemDetail(subject="item-1", title="Do the thing", acceptance=("it works",))]
        ),
    )
    app = create_mcp_app(server)
    base_url = run_app(app)
    client = MCPClient(base_url=base_url, token=TOKEN)
    yield client
    client.close()


def _unreachable_client() -> MCPClient:
    def raising_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return MCPClient(
        base_url="http://internal-mcp", token=TOKEN, transport=httpx.MockTransport(raising_transport)
    )


def test_verify_tools_available_true_when_server_is_up(live_mcp_client):
    tools = build_custom_tools(live_mcp_client)
    poller = Poller(queue_client=FakeQueueClient(), mcp_client=live_mcp_client, custom_tools=tools)
    assert poller.verify_tools_available() is True


def test_verify_tools_available_false_when_server_is_unreachable(live_mcp_client):
    tools = build_custom_tools(live_mcp_client)
    poller = Poller(queue_client=FakeQueueClient(), mcp_client=_unreachable_client(), custom_tools=tools)
    assert poller.verify_tools_available() is False


def test_run_once_empty_queue_returns_empty(live_mcp_client):
    tools = build_custom_tools(live_mcp_client)
    poller = Poller(queue_client=FakeQueueClient(), mcp_client=live_mcp_client, custom_tools=tools)
    outcome = poller.run_once()
    assert outcome.status == "empty"


def test_run_once_executes_a_claim_work_task(live_mcp_client):
    tools = build_custom_tools(live_mcp_client)
    queue = FakeQueueClient(
        [ScheduledTask(task_id="task-1", tool_name="claim_work", arguments={"subject": "item-1"})]
    )
    poller = Poller(queue_client=queue, mcp_client=live_mcp_client, custom_tools=tools)
    outcome = poller.run_once()
    assert outcome.status == "executed"
    assert queue.submitted[0][0] == "task-1"
    assert queue.submitted[0][1]["content"]["subject"] == "item-1"


def test_run_once_is_idempotent_for_a_repeated_task_id(live_mcp_client):
    tools = build_custom_tools(live_mcp_client)
    queue = FakeQueueClient(
        [
            ScheduledTask(task_id="task-1", tool_name="claim_work", arguments={"subject": "item-1"}),
            ScheduledTask(task_id="task-1", tool_name="claim_work", arguments={"subject": "item-1"}),
        ]
    )
    poller = Poller(queue_client=queue, mcp_client=live_mcp_client, custom_tools=tools)
    first = poller.run_once()
    second = poller.run_once()
    assert first.status == "executed"
    assert second.status == "idempotent_replay"
    # The replayed result is the cached lease, not a second, distinct claim
    # rejected as a conflict -- the whole point of the ledger.
    assert queue.submitted[0][1]["content"]["lease_id"] == queue.submitted[1][1]["content"]["lease_id"]


def test_run_once_skips_dispatch_when_tools_are_unavailable():
    queue = FakeQueueClient(
        [ScheduledTask(task_id="task-1", tool_name="claim_work", arguments={"subject": "item-1"})]
    )
    # No live server to discover tools from, so build_custom_tools cannot run;
    # exercise the poller's preflight path directly with an empty tool set,
    # which is what "connectors not loaded yet" looks like from the inside.
    poller = Poller(queue_client=queue, mcp_client=_unreachable_client(), custom_tools=())
    outcome = poller.run_once()
    assert outcome.status == "skipped_unavailable"
    assert queue.submitted == []


def test_build_custom_tools_raises_when_a_tool_is_missing():
    class _StubClient:
        def discover(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor(name="list_ready_work", description="stub"),)

        def call_tool(self, name, **params):  # pragma: no cover - not exercised
            raise AssertionError

    with pytest.raises(MCPClientError):
        build_custom_tools(_StubClient())
