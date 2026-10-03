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
             "result": {"intent": {"intent_id": "intent_00000000000000000000000000", "state": "proposed"}}})
    async def scenario():
        async with httpx.AsyncClient(base_url="http://shell", transport=httpx.MockTransport(handler)) as client:
            store = ShellIntentStore(client)
            binding = RunBinding("p", "w", "repo")
            forwarded = ForwardedIdentity(assertion="caller-assertion", request_id="req", repo_id="repo")
            intent = EffectIntent("effect_local", "run", binding, "repo", "a" * 40, "title", "why", "diff", item_id=23)
            return await store.create("w", "p", "propose_effect", "key", StoredResult("digest", {}), intent, forwarded=forwarded)
    result = asyncio.run(scenario())
    assert result.result == {"intent_id": "intent_00000000000000000000000000", "state": "proposed"}
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


@pytest.mark.parametrize("owner_code", ["effect-not-found", "run-not-found", "forbidden", "not-yours", "grant-mismatch", "invalid-arguments"])
@pytest.mark.parametrize("operation", ["work.effect.get-v1", "work.run.resolve-v1"])
def test_shell_foreign_and_unknown_reads_have_one_error(owner_code, operation):
    class Store(ShellIntentStore):
        async def invoke(self, name, arguments, forwarded):
            if name == operation:
                raise ToolFailure(owner_code, "owner detail must not escape")
            return {"intent": {"run_id": "foreign-run"}}
    async def scenario():
        with pytest.raises(ToolFailure) as error:
            await Store(None).get("intent_" + "0" * 26, RunBinding("p", "w", "repo"), forwarded=object())
        assert (error.value.code, error.value.message) == ("effect-not-found", "no effect intent with that id belongs to the caller")
    asyncio.run(scenario())


def test_shell_malformed_id_has_same_private_error_without_owner_call():
    class Store(ShellIntentStore):
        async def invoke(self, *args):
            pytest.fail("malformed id contacted owner")
    async def scenario():
        with pytest.raises(ToolFailure) as error:
            await Store(None).get("malformed", RunBinding("p", "w", "repo"), forwarded=object())
        assert (error.value.code, error.value.message) == ("effect-not-found", "no effect intent with that id belongs to the caller")
    asyncio.run(scenario())


@pytest.mark.parametrize("names,available", [([], False), (["work.effect.propose-v1"], False),
    (["work.effect.propose-v1", "work.effect.get-v1"], True)])
def test_owner_catalog_advertisement_is_transport_bound(names, available):
    async def scenario():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"operations": [{"name": name} for name in names]}))
        async with httpx.AsyncClient(base_url="http://shell", transport=transport) as client:
            assert await ShellIntentStore(client).available() is available
    asyncio.run(scenario())


def test_nonascii_digest_matches_owner_contract_golden_vector():
    intent = EffectIntent("effect_local", "run", RunBinding("p", "w", "repo"), "repo", "a" * 40,
                          "Muutos 漢字", "", "diff café\n", item_id=2147483647)
    assert intent.canonical_intent_digest == "bb41fc3fb92a7849ed058c46ca1420ba379c581f57795f18e0dc0e3447f1bc02"
