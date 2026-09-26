"""TS-16 acceptance: the cloud caller only proposes; `proposed -> accepted`
is an operator (CLI) or an opt-in, file-configured policy on the trusted
side, never the proposer; the reconciler acts only on an intent that
carries its acceptor, and re-validates the diff itself."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler import cli
from vuoro_reconciler.acceptance import AcceptanceRefused, AutoAcceptConfig, OperatorAcceptance
from vuoro_reconciler.diff_policy import DiffPolicy
from vuoro_reconciler.intents import EffectIntent, OperatorAcceptor, PolicyAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig

from conftest import base_commit_of

WORKSPACE = "ws-1"
PROPOSER = "vuoro-cloud-control:01K44444444444444444444444:0"
OPERATOR = OperatorAcceptor(subject="ops:alice")

MODIFY_DIFF = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)

#: A valid `diff --git` block followed by an old-style hunk that git apply
#: also applies: it creates a CI workflow the edge's first-header-only
#: parser never saw.
TRAILING_HUNK_DIFF = MODIFY_DIFF + (
    "--- a/.github/workflows/ci.yml\n"
    "+++ b/.github/workflows/ci.yml\n"
    "@@ -0,0 +1 @@\n"
    "+on: push\n"
)

EXECUTABLE_DIFF = (
    "diff --git a/docs/run.sh b/docs/run.sh\n"
    "new file mode 100755\n"
    "--- /dev/null\n"
    "+++ b/docs/run.sh\n"
    "@@ -0,0 +1 @@\n"
    "+echo hi\n"
)


def _intent(
    bare_remote: Path,
    *,
    intent_id: str = "effect_acc0001",
    unified_diff: str = MODIFY_DIFF,
    workspace_id: str = WORKSPACE,
    repository: str = "repo-a",
    acceptor=None,
) -> EffectIntent:
    return EffectIntent(
        intent_id=intent_id,
        run_id="run_acc0001",
        repository=repository,
        base_commit=base_commit_of(bare_remote),
        title="Fix the typo",
        rationale="A short rationale.",
        unified_diff=unified_diff,
        workspace_id=workspace_id,
        proposer_principal=PROPOSER,
        acceptor=acceptor,
    )


def _reconciler(source, provider, key, *, auto_accept=None, **config) -> Reconciler:
    return Reconciler(
        intent_source=source,
        provider=provider,
        signing_key=key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"}), **config),
        auto_accept=auto_accept,
    )


def _write_config(tmp_path: Path, policies: list[dict], version: int = 7) -> Path:
    path = tmp_path / "auto-accept.json"
    path.write_text(json.dumps({"version": version, "policies": policies}))
    return path


def _docs_policy(**overrides) -> dict:
    policy = {
        "id": "docs-only",
        "enabled": True,
        "workspace_id": WORKSPACE,
        "repository": "repo-a",
        "effect_kinds": ["diff"],
        "path_globs": ["docs/*"],
    }
    policy.update(overrides)
    return policy


def _commit_message(bare_remote: Path, sha: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(bare_remote), "log", "-1", "--format=%B", sha],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# -- default: nothing is accepted, nothing is executed ------------------------------


@pytest.mark.parametrize("config", ["none", "missing-file", "empty-file", "all-disabled"])
def test_default_config_does_no_clone_push_or_pr_and_the_intent_stays_proposed(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path, config: str
) -> None:
    auto_accept = {
        "none": lambda: None,
        "missing-file": lambda: AutoAcceptConfig(tmp_path / "absent.json"),
        "empty-file": lambda: AutoAcceptConfig(_touch(tmp_path / "empty.json")),
        "all-disabled": lambda: AutoAcceptConfig(
            _write_config(tmp_path, [_docs_policy(enabled=False)])
        ),
    }[config]()
    intent = _intent(bare_remote)
    source = FakeIntentSource(proposed=[intent])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})

    outcomes = asyncio.run(
        _reconciler(source, provider, reconciler_signing_key, auto_accept=auto_accept).run_once()
    )

    assert outcomes == []
    assert source.states[intent.intent_id] == "proposed"
    assert provider.clones == []
    assert provider.pushed_branches == []
    assert provider.pull_requests == []
    assert source.applied == [] and source.failed == []


def _touch(path: Path) -> Path:
    path.write_text("")
    return path


# -- the operator path -----------------------------------------------------------------


def test_the_proposer_cannot_accept_its_own_intent(bare_remote: Path) -> None:
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    with pytest.raises(AcceptanceRefused):
        asyncio.run(OperatorAcceptance(source).accept_interactive("effect_acc0001", PROPOSER))
    assert source.states["effect_acc0001"] == "proposed"


def test_the_reconciler_refuses_an_intent_its_proposer_accepted(
    bare_remote: Path, reconciler_signing_key
) -> None:
    intent = _intent(bare_remote, acceptor=OperatorAcceptor(subject=PROPOSER))
    source = FakeIntentSource(accepted=[intent])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert outcomes[0].reason == "acceptor-is-proposer"
    assert provider.clones == [] and provider.pushed_branches == []


def test_the_reconciler_refuses_an_accepted_intent_with_no_acceptor(
    bare_remote: Path, reconciler_signing_key
) -> None:
    intent = _intent(bare_remote, acceptor=None)
    source = FakeIntentSource(accepted=[intent])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert [o.state for o in outcomes] == ["failed"]
    assert outcomes[0].reason == "no-acceptor"
    assert source.failed == [{"intent_id": intent.intent_id, "reason": "no-acceptor"}]
    assert provider.clones == [] and provider.pushed_branches == [] and provider.pull_requests == []


def test_cli_accept_shows_the_diff_and_records_the_operator(bare_remote: Path) -> None:
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    out = io.StringIO()
    code = cli.main(
        ["accept", "effect_acc0001", "--operator", "ops:alice"],
        intent_source=source,
        stdin=io.StringIO("y\n"),
        stdout=out,
    )
    assert code == 0
    assert MODIFY_DIFF in out.getvalue()
    assert PROPOSER in out.getvalue()
    assert source.states["effect_acc0001"] == "accepted"
    assert source.records["effect_acc0001"].acceptor == OperatorAcceptor(subject="ops:alice")


def test_cli_accept_without_confirmation_records_nothing(bare_remote: Path) -> None:
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    code = cli.main(
        ["accept", "effect_acc0001", "--operator", "ops:alice"],
        intent_source=source,
        stdin=io.StringIO("\n"),
        stdout=io.StringIO(),
    )
    assert code == 1
    assert source.states["effect_acc0001"] == "proposed"


def test_cli_accept_as_the_proposer_is_refused(bare_remote: Path) -> None:
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    out = io.StringIO()
    code = cli.main(
        ["accept", "effect_acc0001", "--operator", PROPOSER, "--yes"],
        intent_source=source,
        stdout=out,
    )
    assert code == 1
    assert "refused" in out.getvalue()
    assert source.states["effect_acc0001"] == "proposed"


def test_cli_reject_records_the_operator_and_reason(bare_remote: Path) -> None:
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    code = cli.main(
        ["reject", "effect_acc0001", "--operator", "ops:alice", "--reason", "not now", "--yes"],
        intent_source=source,
        stdout=io.StringIO(),
    )
    assert code == 0
    assert source.states["effect_acc0001"] == "rejected"
    assert source.rejected == [
        {"intent_id": "effect_acc0001", "acceptor": OperatorAcceptor(subject="ops:alice"), "reason": "not now"}
    ]


# -- the opt-in policy path ------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"workspace_id": "some-other-workspace"},
        {"repository": "some-other-repo"},
        {"effect_kinds": ["something-else"]},
        {"path_globs": ["src/*"]},
    ],
)
def test_a_policy_scoped_elsewhere_does_not_execute(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path, overrides: dict
) -> None:
    config = AutoAcceptConfig(_write_config(tmp_path, [_docs_policy(**overrides)]))
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    outcomes = asyncio.run(
        _reconciler(source, provider, reconciler_signing_key, auto_accept=config).run_once()
    )
    assert outcomes == []
    assert source.states["effect_acc0001"] == "proposed"
    assert provider.clones == [] and provider.pushed_branches == []


def test_a_matching_policy_records_its_id_and_version_in_the_report_and_trailer(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path
) -> None:
    path = _write_config(tmp_path, [_docs_policy()], version=7)
    config = AutoAcceptConfig(path)
    source = FakeIntentSource(proposed=[_intent(bare_remote)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})

    outcomes = asyncio.run(
        _reconciler(source, provider, reconciler_signing_key, auto_accept=config).run_once()
    )

    assert [o.state for o in outcomes] == ["applied"], outcomes
    acceptor = source.applied[0]["acceptor"]
    assert isinstance(acceptor, PolicyAcceptor)
    assert (acceptor.policy_id, acceptor.version) == ("docs-only", 7)
    assert acceptor.scope["workspace_id"] == WORKSPACE
    assert acceptor.scope["repository"] == "repo-a"
    assert acceptor.config_digest == config.digest and len(config.digest) == 64
    message = _commit_message(bare_remote, outcomes[0].commit_sha)
    assert "Vuoro-Accepted-By: policy:docs-only@7" in message
    assert "Vuoro-Intent: effect_acc0001" in message


def test_policy_path_globs_are_rechecked_against_what_the_diff_actually_touched(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path
) -> None:
    """A current, valid policy acceptor whose scope does not cover the
    applied change is refused after applying, whoever recorded it."""

    config = AutoAcceptConfig(_write_config(tmp_path, [_docs_policy(id="src-only", path_globs=["src/*"])]))
    policy = config.policy("src-only")
    acceptor = PolicyAcceptor("src-only", config.version, policy.scope(), config.digest)
    source = FakeIntentSource(accepted=[_intent(bare_remote, acceptor=acceptor)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    outcomes = asyncio.run(
        _reconciler(source, provider, reconciler_signing_key, auto_accept=config).run_once()
    )
    assert outcomes[0].reason == "outside-policy-scope"
    assert provider.pushed_branches == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"version": 1, "policies": [{"id": "x", "enabled": "yes"}]}),
        json.dumps({"version": 1, "policies": [_docs_policy(effect_kinds=[])]}),
        json.dumps({"version": 1, "policies": [_docs_policy(extra=1)]}),
        json.dumps({"version": 0, "policies": []}),
        json.dumps({"version": 1, "policies": [_docs_policy(), _docs_policy()]}),
    ],
)
def test_a_malformed_auto_accept_config_fails_loudly(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "auto-accept.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        AutoAcceptConfig(path)


# -- the reconciler's own diff policy ---------------------------------------------------


@pytest.mark.parametrize(
    ("diff", "code"),
    [
        (TRAILING_HUNK_DIFF, "protected-path-refused"),
        (EXECUTABLE_DIFF, "mode-change-refused"),
    ],
)
def test_the_reconciler_revalidates_the_diff_itself(
    bare_remote: Path, reconciler_signing_key, diff: str, code: str
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote, unified_diff=diff, acceptor=OPERATOR)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert outcomes[0].reason == f"diff-policy-refused: {code}"
    assert provider.pushed_branches == [] and provider.pull_requests == []


def test_the_reconciler_diff_policy_is_configurable_per_repository(
    bare_remote: Path, reconciler_signing_key
) -> None:
    source = FakeIntentSource(accepted=[_intent(bare_remote, unified_diff=TRAILING_HUNK_DIFF, acceptor=OPERATOR)])
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    policies = {"repo-a": DiffPolicy(path_allowlist=frozenset({".github/workflows/*"}))}
    outcomes = asyncio.run(
        _reconciler(source, provider, reconciler_signing_key, diff_policies=policies).run_once()
    )
    assert outcomes[0].state == "applied", outcomes


# -- per-intent failure isolation and idempotent re-runs ----------------------------------


def test_a_push_failure_fails_that_intent_and_the_others_continue(
    bare_remote: Path, reconciler_signing_key
) -> None:
    first = _intent(bare_remote, intent_id="effect_acc0001", acceptor=OPERATOR)
    second = _intent(bare_remote, intent_id="effect_acc0002", acceptor=OPERATOR)
    source = FakeIntentSource(accepted=[first, second])
    provider = FakeProviderClient(
        repositories={"repo-a": bare_remote}, fail_push_for=frozenset({"vuoro-effect/effect_acc0001"})
    )
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert [(o.intent_id, o.state, o.reason) for o in outcomes] == [
        ("effect_acc0001", "failed", "push-failed"),
        ("effect_acc0002", "applied", None),
    ]
    # The reason is a code: nothing from the provider's exception leaks.
    assert "token" not in json.dumps(source.failed)


def test_a_rerun_after_a_lost_report_is_success_not_a_non_fast_forward(
    bare_remote: Path, reconciler_signing_key
) -> None:
    intent = _intent(bare_remote, acceptor=OPERATOR)
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    first = asyncio.run(
        _reconciler(FakeIntentSource(accepted=[intent]), provider, reconciler_signing_key).run_once()
    )
    assert first[0].state == "applied"

    # The source never recorded `applied` and hands the intent out again.
    source = FakeIntentSource(accepted=[intent])
    second = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())

    assert second[0].state == "applied", second
    assert second[0].commit_sha == first[0].commit_sha
    assert provider.pushed_branches == [("repo-a", "vuoro-effect/effect_acc0001")]
    assert len(provider.pull_requests) == 1


def test_an_existing_branch_with_a_different_change_is_refused(
    bare_remote: Path, reconciler_signing_key
) -> None:
    intent = _intent(bare_remote, acceptor=OPERATOR)
    provider = FakeProviderClient(repositories={"repo-a": bare_remote})
    asyncio.run(_reconciler(FakeIntentSource(accepted=[intent]), provider, reconciler_signing_key).run_once())

    changed = _intent(
        bare_remote, acceptor=OPERATOR, unified_diff=MODIFY_DIFF.replace("+new", "+newer")
    )
    source = FakeIntentSource(accepted=[changed])
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert outcomes[0].reason == "branch-exists-with-different-change"


def test_the_default_branch_is_always_protected(bare_remote: Path, reconciler_signing_key) -> None:
    intent = _intent(bare_remote, acceptor=OPERATOR)
    provider = FakeProviderClient(repositories={"repo-a": bare_remote}, default="vuoro-effect/effect_acc0001")
    source = FakeIntentSource(accepted=[intent])
    outcomes = asyncio.run(_reconciler(source, provider, reconciler_signing_key).run_once())
    assert outcomes[0].reason == "branch-is-protected"
    assert provider.clones == [] and provider.pushed_branches == []
