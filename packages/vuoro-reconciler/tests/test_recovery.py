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
import subprocess
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler.git_ops import checkout_at, commit_signed, trailer
from vuoro_reconciler.intents import Acceptor, EffectIntent, OperatorAcceptor
from vuoro_reconciler.provider import PullRequest
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


# -- review follow-ups: only the reconciler's own work is ever adopted ---------------------


def _push_lookalike(bare_remote: Path, tmp_path: Path, key, *, signed: bool) -> str:
    """Push a same-tree, same-parent commit to BRANCH that is not this
    intent's reconciler commit: unsigned with the exact same message, or
    signed by the reconciler key but carrying another intent's trailers."""

    dest = tmp_path / "lookalike"
    checkout_at(str(bare_remote), base_commit_of(bare_remote), str(dest))
    (dest / "docs" / "readme.md").write_text("new\n")
    if signed:
        commit_signed(
            str(dest),
            title="Fix the typo",
            rationale="A short rationale.",
            run_id="run_other",
            intent_id="effect_other",
            acceptor=OPERATOR,
            key=key,
        )
    else:
        message = (
            f"Fix the typo\n\nA short rationale.\n\n{trailer('run_rec0001', 'effect_rec0001', OPERATOR)}\n"
        )
        git = ["git", "-C", str(dest), "-c", "user.name=mallory", "-c", "user.email=m@example.test"]
        subprocess.run([*git, "-c", "commit.gpgsign=false", "commit", "-qam", message], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "push", "-q", str(bare_remote), f"HEAD:refs/heads/{BRANCH}"], check=True
    )
    return subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("signed", [False, True], ids=["unsigned-same-message", "signed-other-intent"])
def test_a_foreign_same_tree_branch_is_never_adopted(
    bare_remote: Path, tmp_path: Path, reconciler_signing_key, signed: bool
) -> None:
    _push_lookalike(bare_remote, tmp_path, reconciler_signing_key, signed=signed)
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert [(o.state, o.reason) for o in outcomes] == [("failed", "branch-exists-with-different-change")]
    assert provider.pushed_branches == [] and provider.pull_requests == []
    assert source.applied == []


def test_a_pr_to_the_wrong_base_is_never_adopted(bare_remote: Path, reconciler_signing_key) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote}, hooks={"after_push": _crash_at("after_push")}
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    provider.hooks.clear()
    provider.add_pull_request(
        PullRequest("repo-a", BRANCH, "some-other-base", "evil", "evil"),
        head_sha=provider.branch_tip("repo-a", BRANCH),
    )

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert [(o.state, o.reason) for o in outcomes] == [("failed", "pull-request-mismatch")]
    assert len(provider.pull_requests) == 1 and source.applied == []


def _crash_after_pr(bare_remote: Path, key) -> tuple[FakeIntentSource, FakeProviderClient]:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote},
        hooks={"after_open_pull_request": _crash_at("after_open_pull_request")},
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(_reconciler(source, provider, key).run_once())
    provider.hooks.clear()
    return source, provider


def test_a_pr_merged_before_restart_is_reported_applied(bare_remote: Path, reconciler_signing_key) -> None:
    source, provider = _crash_after_pr(bare_remote, reconciler_signing_key)
    provider.pr_states[1] = "merged"

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    _assert_recovered(source, provider, outcomes)
    assert outcomes[0].pr_url.endswith("/pulls/1")


def test_a_merged_pr_whose_branch_was_deleted_is_not_pushed_again(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source, provider = _crash_after_pr(bare_remote, reconciler_signing_key)
    tip = provider.branch_tip("repo-a", BRANCH)
    git_dir = ["git", "--git-dir", str(bare_remote)]
    subprocess.run([*git_dir, "update-ref", "refs/heads/main", tip], check=True)
    subprocess.run([*git_dir, "update-ref", "-d", f"refs/heads/{BRANCH}"], check=True)
    provider.pr_states[1] = "merged"

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert [(o.state, o.commit_sha) for o in outcomes] == [("applied", tip)]
    assert provider.pushed_branches == [("repo-a", BRANCH)]  # the first run's push only
    assert provider.branch_tip("repo-a", BRANCH) is None
    assert len(provider.pull_requests) == 1 and len(source.applied) == 1


def test_a_pr_closed_unmerged_fails_and_opens_no_new_pr(bare_remote: Path, reconciler_signing_key) -> None:
    source, provider = _crash_after_pr(bare_remote, reconciler_signing_key)
    provider.pr_states[1] = "closed"

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert [(o.state, o.reason) for o in outcomes] == [("failed", "pull-request-closed")]
    assert len(provider.pull_requests) == 1 and source.applied == []


def test_a_late_second_consumer_after_the_push_reports_once(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    late_outcomes = []

    async def second_consumer_runs_now(repository: str, branch: str) -> None:
        # B starts after A's push and before A's PR, and runs to completion.
        provider.hooks.clear()
        late_outcomes.extend(await _reconciler(source, provider, reconciler_signing_key).run_once())

    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote}, hooks={"after_push": second_consumer_runs_now}
    )
    first = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert [(o.state, o.reason) for o in late_outcomes] == [("applied", None)]
    assert [(o.state, o.reason) for o in first] == [("duplicate", "concurrent-consumer")]
    assert provider.pushed_branches == [("repo-a", BRANCH)]
    assert len(provider.pull_requests) == 1
    assert len(source.applied) == 1 and source.failed == []


def test_a_transient_lookup_failure_leaves_the_intent_for_the_next_poll(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote}, fail_lookups=True)

    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert [(o.state, o.reason) for o in outcomes] == [("deferred", "provider-lookup-failed")]
    assert source.states == {"effect_rec0001": "accepted"} and source.failed == []
    assert provider.pushed_branches == []

    provider.fail_lookups = False
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    _assert_recovered(source, provider, outcomes)
