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


def test_verify_tools_available_false_when_the_server_is_up_but_a_tool_is_missing(live_mcp_client):
    """The reachable-but-incomplete branch of the preflight, distinct from
    the unreachable one above. A coordinator silent-pass audit (agentops#2469)
    found that deleting this branch left the suite green: `verify_tools_available`
    was only ever exercised with a fully-stocked server or none at all, so a
    server answering with a partial tool set would have been dispatched
    against. That is the "only one of two placements" failure class."""

    class _PartialTool:
        name = "a_tool_the_server_does_not_offer"

        @staticmethod
        def handler(arguments):  # pragma: no cover - never dispatched
            raise AssertionError("must not be dispatched")

    poller = Poller(
        queue_client=FakeQueueClient(),
        mcp_client=live_mcp_client,
        custom_tools=(_PartialTool(),),
    )
    assert poller.verify_tools_available() is False


def test_run_once_reports_error_for_an_unknown_custom_tool(live_mcp_client):
    """A queued task naming a tool this poller was not built with must be
    reported back as an error, not silently acked or crashed on. Added by the
    coordinator audit: disabling the `tool is None` guard left the suite green
    while turning this case into an AttributeError inside the dispatch."""
    tools = build_custom_tools(live_mcp_client)
    queue = FakeQueueClient(
        [ScheduledTask(task_id="task-9", tool_name="not_a_wrapped_tool", arguments={})]
    )
    poller = Poller(queue_client=queue, mcp_client=live_mcp_client, custom_tools=tools)
    outcome = poller.run_once()
    assert outcome.status == "error"
    assert "not_a_wrapped_tool" in (outcome.detail or "")
    assert queue.submitted[0][0] == "task-9"
    assert queue.submitted[0][1] is None
    assert "not_a_wrapped_tool" in queue.submitted[0][2]
