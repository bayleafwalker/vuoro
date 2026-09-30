"""M2-3 (agentops#2580): crash recovery and concurrency.

Each case kills the reconciler at a named point (a `BaseException`, which
`run_once` does not catch -- the process just stops, like a SIGKILL), then
starts a fresh `Reconciler` against the same forge and intent store. The
expected outcome is always one branch `vuoro-effect/<intent_id>`, at most
one PR, and one terminal intent state. The provider marker (branch + open
PR) is the recovery key; the reconciler keeps no ledger of its own.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler.intents import Acceptor, EffectIntent, OperatorAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig

from conftest import base_commit_of

OPERATOR = OperatorAcceptor(subject="ops:alice")
BRANCH = "vuoro-effect/effect_rec0001"

MODIFY_DIFF = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "index 1111111..2222222 100644\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


class SimulatedCrash(BaseException):
    """The process died here. Not an `Exception`: nothing in the reconciler
    gets to handle it, record it, or clean up the forge."""


def _intent(
    bare_remote: Path, *, intent_id: str = "effect_rec0001", repository: str = "repo-a"
) -> EffectIntent:
    return EffectIntent(
        intent_id=intent_id,
        run_id="run_rec0001",
        repository=repository,
        base_commit=base_commit_of(bare_remote),
        title="Fix the typo",
        rationale="A short rationale.",
        unified_diff=MODIFY_DIFF,
        workspace_id="ws-1",
        proposer_principal="cloud:proposer:0",
        acceptor=OPERATOR,
    )


def _reconciler(source, provider, key, repositories=("repo-a",)) -> Reconciler:
    return Reconciler(
        intent_source=source,
        provider=provider,
        signing_key=key,
        config=ReconcilerConfig(repository_allowlist=frozenset(repositories)),
    )


def _crash_at(point: str):
    async def crash(repository: str, branch: str) -> None:
        raise SimulatedCrash(point)

    return crash


def _assert_recovered(source: FakeIntentSource, provider: FakeProviderClient, outcomes) -> None:
    assert [(o.intent_id, o.state) for o in outcomes] == [("effect_rec0001", "applied")], outcomes
    assert provider.pushed_branches == [("repo-a", BRANCH)]
    assert len(provider.pull_requests) == 1
    assert source.states == {"effect_rec0001": "applied"}
    assert len(source.applied) == 1 and source.failed == []
    assert source.applied[0]["commit_sha"] == provider.branch_tip("repo-a", BRANCH)
    assert source.applied[0]["pr_url"] == outcomes[0].pr_url


def test_crash_after_push_before_pr_restart_opens_exactly_one_pr(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote}, hooks={"after_push": _crash_at("after_push")}
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    pushed_tip = provider.branch_tip("repo-a", BRANCH)
    assert pushed_tip is not None and provider.pull_requests == []
    assert source.states == {"effect_rec0001": "accepted"}

    provider.hooks.clear()
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    _assert_recovered(source, provider, outcomes)
    # The branch the first run pushed is the one reported; nothing re-pushed.
    assert outcomes[0].commit_sha == pushed_tip


def test_crash_after_pr_opened_before_applied_restart_reuses_the_pr(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote},
        hooks={"after_open_pull_request": _crash_at("after_open_pull_request")},
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert len(provider.pull_requests) == 1 and source.applied == []

    provider.hooks.clear()
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    _assert_recovered(source, provider, outcomes)
    assert outcomes[0].pr_url.endswith("/pulls/1")


class _DiesOnFirstReport(FakeIntentSource):
    """The provider calls all returned; the process died before the
    `IntentSource` write landed."""

    crashes_left: int = 1

    async def report_applied(
        self, intent_id: str, *, commit_sha: str, pr_url: str, acceptor: Acceptor
    ) -> None:
        if self.crashes_left:
            self.crashes_left -= 1
            raise SimulatedCrash("before report_applied")
        await super().report_applied(intent_id, commit_sha=commit_sha, pr_url=pr_url, acceptor=acceptor)


def test_crash_before_success_recorded_restart_records_applied_once(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = _DiesOnFirstReport(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    with pytest.raises(SimulatedCrash):
        asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert len(provider.pull_requests) == 1
    assert source.states == {"effect_rec0001": "accepted"} and source.applied == []

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    _assert_recovered(source, provider, outcomes)


def test_concurrent_consumers_produce_one_branch_and_one_pr(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    barrier = asyncio.Barrier(2) if hasattr(asyncio, "Barrier") else None
    assert barrier is not None, "asyncio.Barrier requires Python 3.11+"

    async def both_ready_to_push(repository: str, branch: str) -> None:
        # Both consumers have looked the branch up and found nothing.
        await barrier.wait()

    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote}, hooks={"before_push": both_ready_to_push}
    )

    async def race():
        first = _reconciler(source, provider, reconciler_signing_key)
        second = _reconciler(source, provider, reconciler_signing_key)
        return await asyncio.gather(first.run_once(), second.run_once())

    first, second = asyncio.run(race())

    states = sorted(outcome[0].state for outcome in (first, second))
    assert states == ["applied", "duplicate"], (first, second)
    loser = next(outcome[0] for outcome in (first, second) if outcome[0].state == "duplicate")
    assert loser.reason == "concurrent-consumer"
    assert provider.pushed_branches == [("repo-a", BRANCH)]
    assert len(provider.pull_requests) == 1
    # The loser records nothing: one applied, no failure.
    assert source.states == {"effect_rec0001": "applied"}
    assert len(source.applied) == 1 and source.failed == []


def test_credential_revoked_mid_run_fails_cleanly_and_others_continue(
    bare_remote: Path, tmp_path: Path, reconciler_signing_key
) -> None:
    other_remote = tmp_path / "other.git"
    shutil.copytree(bare_remote, other_remote)
    main_before = base_commit_of(bare_remote)
    revoked = _intent(bare_remote)
    unaffected = _intent(other_remote, intent_id="effect_rec0002", repository="repo-b")
    source = FakeIntentSource(accepted=[revoked, unaffected])

    async def revoke_repo_a(repository: str, branch: str) -> None:
        if repository == "repo-a":
            provider.revoked.add("repo-a")

    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote, "repo-b": other_remote}, hooks={"after_push": revoke_repo_a}
    )
    outcomes = asyncio.run(
        _reconciler(source, provider, reconciler_signing_key, repositories=("repo-a", "repo-b")).run_once()
    )

    assert [(o.intent_id, o.state, o.reason) for o in outcomes] == [
        ("effect_rec0001", "failed", "provider-credential-rejected"),
        ("effect_rec0002", "applied", None),
    ]
    assert source.states == {"effect_rec0001": "failed", "effect_rec0002": "applied"}
    assert source.failed == [{"intent_id": "effect_rec0001", "reason": "provider-credential-rejected"}]
    # No PR for the revoked repository, and its default branch is untouched.
    assert [pr.repository for pr in provider.pull_requests] == ["repo-b"]
    assert base_commit_of(bare_remote) == main_before
    # The reason is a code: nothing from the provider's error leaks.
    assert "token" not in json.dumps(source.failed)
