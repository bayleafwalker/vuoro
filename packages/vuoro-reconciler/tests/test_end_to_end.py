"""E3 (agentops#2467) acceptance: a diff-shaped intent becomes a signed
commit whose signature verifies against the reconciler's key and whose
trailers name the run and intent; a non-applying diff fails without a
commit. (An imperative/malformed intent is refused at `propose_effect`
itself -- see `vuoro_mcp_edge`'s own
`test_effect_tools.py::test_propose_effect_refuses_each_structural_case`
and `::test_propose_effect_rejects_out_of_shape_arguments`; this package
never receives one, by construction of the shared contract, and does not
depend on the edge package to prove it independently here.)"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler.git_ops import trailer
from vuoro_reconciler.intents import EffectIntent, OperatorAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.signing import verify_commit

from conftest import base_commit_of

MODIFY_DIFF = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "index 1111111..2222222 100644\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)

NON_APPLYING_DIFF = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "index 1111111..2222222 100644\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-this content is not present in the file\n"
    "+new\n"
)


OPERATOR = OperatorAcceptor(subject="operator:alice")


def _intent(bare_remote: Path, *, unified_diff: str, intent_id: str = "effect_test0001") -> EffectIntent:
    return EffectIntent(
        intent_id=intent_id,
        run_id="run_test0001",
        repository="repo-a",
        base_commit=base_commit_of(bare_remote),
        title="Fix the typo",
        rationale="A short rationale.",
        unified_diff=unified_diff,
        workspace_id="ws-1",
        proposer_principal="cloud:proposer:0",
        acceptor=OPERATOR,
    )


def test_a_diff_shaped_intent_becomes_a_signed_commit_with_trailers(
    bare_remote: Path, reconciler_signing_key
) -> None:
    intent = _intent(bare_remote, unified_diff=MODIFY_DIFF)
    intent_source = FakeIntentSource(accepted=[intent])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    reconciler = Reconciler(
        intent_source=intent_source,
        provider=provider,
        signing_key=reconciler_signing_key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
    )

    outcomes = asyncio.run(reconciler.run_once())

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.state == "applied"
    assert outcome.commit_sha
    assert intent_source.applied == [
        {
            "intent_id": intent.intent_id,
            "commit_sha": outcome.commit_sha,
            "pr_url": outcome.pr_url,
            "acceptor": OPERATOR,
        }
    ]
    assert intent_source.failed == []

    # The pushed branch, in the bare "forge", carries the signed commit.
    assert provider.pushed_branches == [("repo-a", "vuoro-effect/effect_test0001")]
    clone = str(bare_remote)
    assert verify_commit(clone, outcome.commit_sha, reconciler_signing_key)

    message = subprocess.run(
        ["git", "--git-dir", clone, "log", "-1", "--format=%B", outcome.commit_sha],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert trailer(intent.run_id, intent.intent_id, OPERATOR) in message
    assert "Vuoro-Accepted-By: operator:operator:alice" in message

    # The PR was opened against the provider's default branch, never main itself.
    assert len(provider.pull_requests) == 1
    pr = provider.pull_requests[0]
    assert pr.branch == "vuoro-effect/effect_test0001"
    assert pr.base_branch == "main"


def test_a_signature_forged_against_the_wrong_key_does_not_verify(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path
) -> None:
    """Negative control for the positive assertion above: a real, different
    key must NOT verify, so "verifies against the reconciler key" is
    actually being tested and not vacuously true."""

    intent = _intent(bare_remote, unified_diff=MODIFY_DIFF)
    reconciler = Reconciler(
        intent_source=FakeIntentSource(accepted=[intent]),
        provider=FakeProviderClient(repositories={"repo-a": bare_remote}),
        signing_key=reconciler_signing_key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
    )
    outcomes = asyncio.run(reconciler.run_once())
    commit_sha = outcomes[0].commit_sha

    other_gnupghome = tmp_path / "other-gnupghome"
    other_gnupghome.mkdir(mode=0o700)
    from vuoro_reconciler.signing import SigningKey

    unrelated_key = SigningKey(
        key_format="openpgp",
        signing_key="0" * 16,
        committer_name="Someone Else",
        committer_email="else@vuoro.test",
        env={"GNUPGHOME": str(other_gnupghome)},
    )
    assert verify_commit(str(bare_remote), commit_sha, unrelated_key) is False


def test_a_non_applying_diff_fails_without_a_commit(bare_remote: Path, reconciler_signing_key) -> None:
    before = subprocess.run(
        ["git", "--git-dir", str(bare_remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    intent = _intent(bare_remote, unified_diff=NON_APPLYING_DIFF)
    intent_source = FakeIntentSource(accepted=[intent])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    reconciler = Reconciler(
        intent_source=intent_source,
        provider=provider,
        signing_key=reconciler_signing_key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
    )

    outcomes = asyncio.run(reconciler.run_once())

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.state == "failed"
    assert outcome.commit_sha is None
    assert outcome.reason is not None and outcome.reason.startswith("diff-does-not-apply")
    assert intent_source.failed == [{"intent_id": intent.intent_id, "reason": outcome.reason}]
    assert intent_source.applied == []
    assert provider.pushed_branches == []
    assert provider.pull_requests == []

    after = subprocess.run(
        ["git", "--git-dir", str(bare_remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert after == before  # nothing landed in the "forge"


def test_a_repository_outside_the_allowlist_is_refused_before_any_clone(
    bare_remote: Path, reconciler_signing_key
) -> None:
    intent = _intent(bare_remote, unified_diff=MODIFY_DIFF)
    intent_source = FakeIntentSource(accepted=[intent])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    reconciler = Reconciler(
        intent_source=intent_source,
        provider=provider,
        signing_key=reconciler_signing_key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"some-other-repo"})),
    )

    outcomes = asyncio.run(reconciler.run_once())

    assert outcomes[0].state == "failed"
    assert outcomes[0].reason == "repository-not-allowlisted"
    assert intent_source.failed == [
        {"intent_id": intent.intent_id, "reason": "repository-not-allowlisted"}
    ]
    assert provider.pushed_branches == []
    assert provider.pull_requests == []
