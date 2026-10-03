"""Edge forwards only caller authority and owner-supported proposal fields."""
import asyncio
import json
import httpx
import pytest
from vuoro_mcp_edge.effect_tools import ShellIntentStore, EffectIntent
from vuoro_mcp_edge.idempotency import StoredResult
from vuoro_mcp_edge.runs import RunBinding
from vuoro_mcp_edge.work_source import ForwardedIdentity
from vuoro_mcp_edge.toolsets import ToolFailure


def test_shell_create_forwards_caller_and_atomic_owner_key():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "accepted", "operation": "work.effect.propose-v1",
             "result": {"intent": {"intent_id": "intent_owner", "state": "proposed"}}})
    async def scenario():
        async with httpx.AsyncClient(base_url="http://shell", transport=httpx.MockTransport(handler)) as client:
            store = ShellIntentStore(client)
            binding = RunBinding("p", "w", "repo")
            forwarded = ForwardedIdentity(assertion="caller-assertion", request_id="req", repo_id="repo")
            intent = EffectIntent("effect_local", "run", binding, "repo", "a" * 40, "title", "why", "diff", item_id=23)
            return await store.create("w", "p", "propose_effect", "key", StoredResult("digest", {}), intent, forwarded=forwarded)
    result = asyncio.run(scenario())
    assert result.result == {"intent_id": "intent_owner", "state": "proposed"}
    (request,) = requests
    assert request.headers["X-Vuoro-Identity"] == "caller-assertion"
    body = json.loads(request.content)
    assert body["request_id"] == "req" and body["repo_id"] == "repo"
    assert body["arguments"]["item_id"] == 23 and body["arguments"]["idempotency_key"] == "key"
    assert not {"proposer", "acceptor", "canonical_intent_digest", "state"} & body["arguments"].keys()


def test_shell_store_refuses_missing_forwarded_assertion_before_network():
    def handler(request):
        pytest.fail("missing caller must never contact the owner")
    async def scenario():
        async with httpx.AsyncClient(base_url="http://shell", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ToolFailure) as error:
                await ShellIntentStore(client).invoke("work.effect.get-v1", {"intent_id": "x"}, None)
            assert error.value.code == "effects-unavailable"
    asyncio.run(scenario())
