"""Wire authority remains with the authenticated owner, never acceptor arguments."""
import asyncio
import pytest
from vuoro_reconciler.intents import OperatorAcceptor
from vuoro_reconciler.sprintctl_source import SprintctlIntentSource


def test_missing_accepted_discovery_fails_loudly_not_empty():
    calls = []
    async def invoke(operation, arguments):
        calls.append(operation)
        raise RuntimeError("unknown-operation")
    source = SprintctlIntentSource(invoke, workspace_id="ws", principal_id="operator")
    with pytest.raises(RuntimeError, match="unknown-operation"):
        asyncio.run(source.poll_accepted())
    assert calls == ["work.effect.list-accepted-v1"]


def test_acceptor_never_sent_and_compare_and_set_binding_forwarded():
    calls = []
    async def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {"intent": {"acceptance": {"acceptor_principal": "operator"}}}
    source = SprintctlIntentSource(invoke, workspace_id="ws", principal_id="operator")
    asyncio.run(source.accept("intent_x", OperatorAcceptor("operator"), revision=3,
                              canonical_intent_digest="a" * 64))
    assert calls == [("work.effect.accept-v1", {"intent_id": "intent_x", "revision": 3,
                                               "canonical_intent_digest": "a" * 64})]
    with pytest.raises(ValueError):
        asyncio.run(source.accept("intent_x", OperatorAcceptor("spoofed"), revision=3,
                                  canonical_intent_digest="a" * 64))
    assert len(calls) == 1


def test_report_applied_preserves_acceptance_digest_and_revision():
    calls = []
    async def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {}
    source = SprintctlIntentSource(invoke, workspace_id="ws", principal_id="operator")
    asyncio.run(source.report_applied("intent_x", commit_sha="b" * 40,
        pr_url="https://forge.example/pull/1", acceptor=OperatorAcceptor("operator"),
        revision=7, canonical_intent_digest="c" * 64))
    operation, arguments = calls[0]
    assert operation == "work.effect.mark-applied-v1"
    assert arguments == {"intent_id": "intent_x", "revision": 7, "canonical_intent_digest": "c" * 64,
                         "commit_sha": "b" * 40, "pr_url": "https://forge.example/pull/1"}
    assert not any("acceptor" in key for key in arguments)


def test_nonascii_digest_matches_owner_contract_golden_vector():
    from vuoro_reconciler.intents import EffectIntent, canonical_digest
    intent = EffectIntent("intent_local", "run", "repo", "a" * 40, "Muutos 漢字", "",
                          "diff café\n", "w", "p", item_id=2147483647)
    assert canonical_digest(intent) == "bb41fc3fb92a7849ed058c46ca1420ba379c581f57795f18e0dc0e3447f1bc02"
