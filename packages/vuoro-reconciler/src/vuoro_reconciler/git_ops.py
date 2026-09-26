"""Local git operations: clone, apply, commit, push. No provider credential
and no network call of its own -- every remote push goes through
`ProviderClient`, which owns whatever authentication the real forge needs.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
import subprocess
import tempfile

from .intents import Acceptor
from .signing import SigningKey, configure_signing

__all__ = [
    "CheckoutFailed",
    "DiffDoesNotApply",
    "checkout_at",
    "commit_signed",
    "push_branch",
    "refuse_if_protected",
    "remote_branch_tip",
    "same_change",
    "stage_all",
    "trailer",
    "try_apply_diff",
]


class CheckoutFailed(Exception):
    """`base_commit` could not be checked out (unknown, or the clone failed)."""


class DiffDoesNotApply(Exception):
    """The diff was refused by `git apply --check`; nothing was committed."""


def _run(
    *args: str, cwd: str | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=full_env, capture_output=True, text=True
    )


def checkout_at(clone_url: str, base_commit: str, dest: str) -> None:
    """Clone `clone_url` into `dest` and detach HEAD at `base_commit`.

    Raises `CheckoutFailed` for a clone failure or an unknown `base_commit`;
    either way, `dest` is left as git's own failed-clone state, never a
    checkout at the wrong commit. The clone's `core.hooksPath` is
    `/dev/null`, so no hook (the repository's, or one configured globally)
    runs on checkout or commit.
    """

    cloned = _run("clone", "-c", "core.hooksPath=/dev/null", "--no-local", clone_url, dest)
    if cloned.returncode != 0:
        raise CheckoutFailed(f"git clone failed: {cloned.stderr.strip()}")
    checked_out = _run("-C", dest, "checkout", "--detach", base_commit)
    if checked_out.returncode != 0:
        raise CheckoutFailed(f"base_commit {base_commit!r} could not be checked out")


def try_apply_diff(repo_path: str, unified_diff: str) -> None:
    """Apply `unified_diff` to `repo_path`'s working tree.

    Checks with `git apply --check` first and never runs `git apply` (let
    alone commits) unless the check passes: a non-applying diff fails
    without leaving anything applied or committed.
    """

    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as handle:
        handle.write(unified_diff)
        patch_path = handle.name
    try:
        checked = _run("-C", repo_path, "apply", "--check", patch_path)
        if checked.returncode != 0:
            raise DiffDoesNotApply(checked.stderr.strip() or "git apply --check failed")
        applied = _run("-C", repo_path, "apply", patch_path)
        if applied.returncode != 0:
            # A second, independent check: --check and the real apply can
            # disagree (e.g. a race on the working tree), so this path is
            # reachable and covered, not merely defensive.
            raise DiffDoesNotApply(applied.stderr.strip() or "git apply failed")
    finally:
        os.unlink(patch_path)


def trailer(run_id: str, intent_id: str, acceptor: Acceptor | None = None) -> str:
    lines = [f"Vuoro-Run: {run_id}", f"Vuoro-Intent: {intent_id}"]
    if acceptor is not None:
        lines.append(f"Vuoro-Accepted-By: {acceptor.trailer_value()}")
    return "\n".join(lines)


def stage_all(repo_path: str) -> None:
    """Stage the whole working tree, so the index is exactly what a commit
    would record (and what `diff_policy.staged_changes` inspects)."""

    added = _run("-C", repo_path, "add", "-A")
    if added.returncode != 0:  # pragma: no cover - defensive
        raise RuntimeError(f"git add failed: {added.stderr.strip()}")


def commit_signed(
    repo_path: str,
    *,
    title: str,
    rationale: str,
    run_id: str,
    intent_id: str,
    acceptor: Acceptor,
    key: SigningKey,
) -> str:
    """Stage everything, commit signed by `key` with the run/intent/acceptor
    trailers,
    and return the new commit's sha. Raises if the signature cannot be
    produced (e.g. a missing key): no half-signed commit is left behind
    (git itself refuses to create the commit when `-S` fails)."""

    configure_signing(repo_path, key)
    add = _run("-C", repo_path, "add", "-A", env=key.env)
    if add.returncode != 0:  # pragma: no cover - defensive
        raise RuntimeError(f"git add failed: {add.stderr.strip()}")
    message = f"{title}\n\n{rationale}\n\n{trailer(run_id, intent_id, acceptor)}\n"
    committed = _run(
        "-C", repo_path, "commit", "--no-verify", "-S", "-m", message, env=key.env
    )
    if committed.returncode != 0:
        raise RuntimeError(f"signed commit failed: {committed.stderr.strip()}")
    sha = _run("-C", repo_path, "rev-parse", "HEAD", env=key.env)
    return sha.stdout.strip()


def remote_branch_tip(repo_path: str, branch: str) -> str | None:
    """The commit `branch` pointed at on the clone's origin when it was
    cloned, or `None` if it did not exist there."""

    tip = _run("-C", repo_path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{commit}}")
    return tip.stdout.strip() if tip.returncode == 0 else None


def same_change(repo_path: str, existing: str, candidate: str) -> bool:
    """True if `existing` records the same change as `candidate`: the same
    tree on the same parent(s). A re-run's freshly signed commit differs
    from the first run's only in its timestamp and signature."""

    def shape(ref: str) -> tuple[str, str]:
        tree = _run("-C", repo_path, "rev-parse", f"{ref}^{{tree}}").stdout.strip()
        parents = _run("-C", repo_path, "rev-list", "--parents", "-n", "1", ref).stdout.split()[1:]
        return tree, " ".join(parents)

    return shape(existing) == shape(candidate)


def refuse_if_protected(branch: str, protected_branches: frozenset[str]) -> None:
    """Raise before any push is attempted if `branch` is protected."""

    if branch in protected_branches:
        raise ValueError(f"refusing to push protected branch {branch!r}")


def push_branch(
    repo_path: str, *, remote_url: str, branch: str, protected_branches: frozenset[str]
) -> None:
    """Push `repo_path`'s HEAD to `branch` on `remote_url`.

    A reusable helper for a `ProviderClient` implementation that pushes with
    plain git (e.g. over an authenticated URL, or -- as in this package's
    tests -- to a local bare repository standing in for a forge). Refuses
    structurally, before running any git command, if `branch` is one of
    `protected_branches`.
    """

    refuse_if_protected(branch, protected_branches)
    pushed = _run(
        "-C", repo_path, "push", remote_url, f"HEAD:refs/heads/{branch}"
    )
    if pushed.returncode != 0:
        raise RuntimeError(f"git push failed: {pushed.stderr.strip()}")
