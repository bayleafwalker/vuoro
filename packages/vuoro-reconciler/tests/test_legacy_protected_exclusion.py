"""Protected effects never enter legacy signing, publication or public writes."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from vuoro_reconciler import acceptance, reconciler as module
from vuoro_reconciler.diff_policy import DiffPolicy
from vuoro_reconciler.intents import EffectIntent, PolicyAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from fakes import FakeIntentSource, FakeProviderClient
from test_acceptance import _intent
from test_protected_preflight import owner, source as owner_source


class SourceSpy:
    def __init__(self, intents=(), proposed=()):
        self.intents, self.proposed = list(intents), list(proposed)
        self.writes, self.preflights = [], []

    async def poll_accepted(self):
        return self.intents

    async def poll_proposed(self):
        return self.proposed

    async def preflight(self, intent):
        self.preflights.append(intent)

    async def accept(self, *args, **kwargs):
        self.writes.append(("accept", args, kwargs))

    async def report_applied(self, *args, **kwargs):
        self.writes.append(("applied", args, kwargs))

    async def report_failed(self, *args, **kwargs):
        self.writes.append(("failed", args, kwargs))


class ProviderSpy:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError("excluded intent touched legacy provider")


def candidate(proof=None, *, repository="repo-a", no_acceptor=True, released=True):
    return EffectIntent(
        "intent_protected",
        "run_protected",
        repository,
        "a" * 40,
        "Title",
        "Rationale",
        "not even a valid diff",
        "workspace",
        "proposer",
        acceptance=None if no_acceptor else {"verification": proof},
        release_digest="b" * 64 if released else None,
    )


def runtime(source, provider, *, registered=(), policy=None):
    return Reconciler(
        source,
        provider,
        object(),
        ReconcilerConfig(
            repository_allowlist=frozenset({"repo-a"}),
            protected_repositories=frozenset(registered),
            max_lookup_deferral_seconds=0,
        ),
        auto_accept=policy,
        clock=lambda: 10**6,
    )


@pytest.mark.parametrize("proof", [False, 0, "", {}, [], {"malformed": "proof"}])
@pytest.mark.parametrize("registered", [True, False])
def test_nonnull_malformed_verification_excluded_before_any_legacy_effect(
    monkeypatch, proof, registered
):
    intent = candidate(proof, no_acceptor=False)
    src, provider = SourceSpy([intent]), ProviderSpy()
    value = runtime(src, provider, registered={"repo-a"} if registered else ())
    touched = []

    def git_trap(*args, **kwargs):
        touched.append(True)
        raise AssertionError("excluded intent reached Git")

    monkeypatch.setattr(module, "checkout_at", git_trap)
    value._deferrals[intent.intent_id] = 0
    for _ in range(2):
        (result,) = asyncio.run(value.run_once())
        assert (
            result.state == "excluded"
            and result.reason == "protected-executor-required"
        )
        assert (
            result.commit_sha is None
            and result.pr_url is None
            and result.acceptor is None
        )
    assert not touched and not provider.calls and not src.writes and not src.preflights
    assert not value._deferrals


@pytest.mark.parametrize(
    "metadata", ["missing", "null", "stripped-release", "no-acceptor"]
)
def test_registered_repository_excluded_even_when_all_protected_metadata_stripped(
    metadata,
):
    intent = candidate(
        None,
        no_acceptor=metadata in {"missing", "no-acceptor"},
        released=metadata != "stripped-release",
    )
    src, provider = SourceSpy([intent]), ProviderSpy()
    (result,) = asyncio.run(runtime(src, provider, registered={"repo-a"}).run_once())
    assert (
        result.state == "excluded"
        and not src.writes
        and not src.preflights
        and not provider.calls
    )


def test_actual_valid_owner_receipt_excluded_before_native_preflight_or_reporting(
    bare_remote,
):
    state = owner(bare_remote)
    native = owner_source(state)
    provider = ProviderSpy()
    (result,) = asyncio.run(runtime(native, provider).run_once())
    assert result.state == "excluded" and not provider.calls
    assert state.calls == [("work.effect.list-accepted-v1", {})]
    assert state.row["state"] == "accepted"


@pytest.mark.parametrize("proof", [False, {}, "not-a-proof"])
def test_auto_accept_skips_nonnull_verification_before_policy_evaluation(proof):
    intent = candidate(proof, no_acceptor=False)
    src = SourceSpy(proposed=[intent])
    evaluated = []
    policy = SimpleNamespace(
        active=True, evaluate=lambda intent: evaluated.append(intent)
    )
    assert asyncio.run(acceptance.apply_auto_accept(src, policy)) == []
    assert not evaluated and not src.writes


def test_auto_accept_registered_stripped_intent_never_evaluates_or_accepts():
    intent = candidate(None, released=False)
    src, provider = SourceSpy(proposed=[intent]), ProviderSpy()
    evaluated = []
    policy = SimpleNamespace(
        active=True, evaluate=lambda intent: evaluated.append(intent)
    )
    assert (
        asyncio.run(
            runtime(src, provider, registered={"repo-a"}, policy=policy).run_once()
        )
        == []
    )
    assert not evaluated and not src.writes and not provider.calls


def test_explicit_verification_null_remains_eligible_for_ordinary_auto_accept():
    intent = candidate(None, no_acceptor=False)
    src = SourceSpy(proposed=[intent])
    acceptor = PolicyAcceptor("ordinary", 1, {}, "c" * 64)
    evaluated = []

    def evaluate(intent):
        evaluated.append(intent)
        return acceptor

    policy = SimpleNamespace(active=True, evaluate=evaluate)
    assert asyncio.run(acceptance.apply_auto_accept(src, policy)) == [
        (intent.intent_id, acceptor)
    ]
    assert evaluated == [intent] and src.writes[0][0] == "accept"


@pytest.mark.parametrize("verification", ["absent", "null"])
def test_unregistered_ordinary_release_bound_still_signs_and_publishes(
    bare_remote, reconciler_signing_key, verification
):
    from vuoro_reconciler.intents import OperatorAcceptor

    intent = replace(
        _intent(bare_remote, acceptor=OperatorAcceptor("operator")),
        release_digest="b" * 64,
    )
    src = FakeIntentSource(accepted=[intent])
    if verification == "null":
        accepted = src.records[intent.intent_id]
        src.records[intent.intent_id] = replace(
            accepted, acceptance={**accepted.acceptance, "verification": None}
        )

    async def preflight(intent):
        pass

    src.preflight = preflight
    provider = FakeProviderClient({"repo-a": bare_remote})
    value = Reconciler(
        src,
        provider,
        reconciler_signing_key,
        ReconcilerConfig(
            frozenset({"repo-a"}),
            diff_policies={"repo-a": DiffPolicy(frozenset({"docs/*"}))},
        ),
    )
    (result,) = asyncio.run(value.run_once())
    assert (
        result.state == "applied"
        and len(provider.pushed_branches) == len(provider.pull_requests) == 1
    )
    assert len(src.applied) == 1 and not src.failed


@pytest.mark.parametrize(
    "invalid", [set(), [], ("repo-a",), frozenset({""}), frozenset({1})]
)
def test_protected_registration_requires_immutable_repository_set(invalid):
    with pytest.raises(ValueError, match="^protected-repositories-invalid$"):
        ReconcilerConfig(frozenset(), protected_repositories=invalid)


def test_protected_registration_cannot_be_mutated():
    config = ReconcilerConfig(frozenset(), protected_repositories=frozenset({"repo-a"}))
    with pytest.raises(FrozenInstanceError):
        config.protected_repositories = frozenset()
