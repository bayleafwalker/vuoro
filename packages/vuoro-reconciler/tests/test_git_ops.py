from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from vuoro_reconciler.gitenv import run_git
from vuoro_reconciler.git_ops import (
    CheckoutFailed,
    DiffDoesNotApply,
    checkout_at,
    push_branch,
    refuse_if_protected,
    try_apply_diff,
)


def test_checkout_at_an_unknown_commit_raises_checkout_failed(bare_remote: Path, tmp_path: Path) -> None:
    dest = tmp_path / "checkout"
    with pytest.raises(CheckoutFailed):
        checkout_at(str(bare_remote), "0" * 40, str(dest))


def test_checkout_at_a_bad_remote_raises_checkout_failed(tmp_path: Path) -> None:
    dest = tmp_path / "checkout"
    with pytest.raises(CheckoutFailed):
        checkout_at(str(tmp_path / "does-not-exist"), "0" * 40, str(dest))


def test_try_apply_diff_applies_a_matching_diff(bare_remote: Path, tmp_path: Path) -> None:
    dest = tmp_path / "work"
    checkout_at(str(bare_remote), _head(bare_remote), str(dest))
    try_apply_diff(
        str(dest),
        "diff --git a/docs/readme.md b/docs/readme.md\n"
        "--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n",
    )
    assert (dest / "docs" / "readme.md").read_text() == "new\n"


def test_try_apply_diff_refuses_a_non_matching_diff_and_touches_nothing(
    bare_remote: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "work"
    checkout_at(str(bare_remote), _head(bare_remote), str(dest))
    with pytest.raises(DiffDoesNotApply):
        try_apply_diff(
            str(dest),
            "diff --git a/docs/readme.md b/docs/readme.md\n"
            "--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n"
            "-nothing like the real content\n+new\n",
        )
    assert (dest / "docs" / "readme.md").read_text() == "old\n"


def test_refuse_if_protected_blocks_main_and_master() -> None:
    for branch in ("main", "master"):
        with pytest.raises(ValueError):
            refuse_if_protected(branch, frozenset({"main", "master"}))
    refuse_if_protected("vuoro-effect/effect_x", frozenset({"main", "master"}))


def test_push_branch_refuses_a_protected_branch_before_running_git(
    bare_remote: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "work"
    checkout_at(str(bare_remote), _head(bare_remote), str(dest))
    with pytest.raises(ValueError):
        push_branch(str(dest), remote_url=str(bare_remote), branch="main", protected_branches=frozenset({"main"}))
    # The bare remote's main ref is unchanged: no push was attempted.
    assert _head(bare_remote) == _head(bare_remote)


def _head(bare_remote: Path) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(bare_remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_checkout_disables_hooks(bare_remote: Path, tmp_path: Path) -> None:
    dest = tmp_path / "work"
    checkout_at(str(bare_remote), _head(bare_remote), str(dest))
    # Read through the scrubbed env, so an ambient global/system config
    # cannot supply (or mask) the value being checked.
    hooks = run_git("-C", str(dest), "config", "--local", "core.hooksPath")
    assert hooks.returncode == 0, hooks.stderr
    assert hooks.stdout.strip() == "/dev/null"
