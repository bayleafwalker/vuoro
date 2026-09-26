"""The reconciler's own diff policy: it does not trust the edge's validation.

The edge (`vuoro_mcp_edge.effect_tools.validate_diff`) refuses a disallowed
diff at proposal time, but the reconciler is the thing that acts, so it
re-derives what the diff touches from git itself and checks that against
its own, independently configured policy before anything is committed:

- `patch_paths` asks `git apply --numstat` which paths a patch names, used
  by auto-accept to evaluate a policy's `path_globs` before any checkout;
- `staged_changes` reads the *result* of applying the patch to a clean
  checkout (`git diff --cached --raw --numstat --no-renames`), so no patch
  grammar trick can hide a touched path, a mode or a binary blob from it;
- `check_changes` refuses what section 7 refuses: binary content, mode
  changes (including a new file created executable), symlinks, submodules,
  unsafe paths, deletes outside the repository's path allowlist, and CI
  workflow or protected paths the allowlist does not name.

With `--no-renames` a rename is seen as a delete of the old path plus an
add of the new one, so the old path must be on the allowlist exactly as a
delete must be.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import fnmatch
import os
import subprocess
import tempfile

__all__ = [
    "DiffPolicy",
    "DiffPolicyViolation",
    "StagedChange",
    "check_changes",
    "patch_paths",
    "staged_changes",
]

#: Same fixed patterns the edge uses; `fnmatch`'s `*` spans `/`.
CI_WORKFLOW_PATTERNS: tuple[str, ...] = (".github/workflows/*", ".forgejo/workflows/*")

_REGULAR = "100644"
_EXECUTABLE = "100755"
_SYMLINK = "120000"
_GITLINK = "160000"
_ABSENT = "000000"


class DiffPolicyViolation(Exception):
    """The diff touches something this reconciler's policy refuses."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class DiffPolicy:
    """What the reconciler allows for one repository. The empty default is
    fail-closed: no deletes/renames, no CI-workflow or protected path."""

    path_allowlist: frozenset[str] = frozenset()
    protected_path_patterns: frozenset[str] = frozenset()

    def is_allowlisted(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.path_allowlist)

    def is_protected(self, path: str) -> bool:
        patterns = (*CI_WORKFLOW_PATTERNS, *self.protected_path_patterns)
        return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


@dataclass(frozen=True)
class StagedChange:
    path: str
    status: str  # "A" | "D" | "M" | "T"
    old_mode: str
    new_mode: str
    binary: bool


def _git(*args: str, cwd: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True)


def patch_paths(unified_diff: str) -> tuple[str, ...]:
    """Every path git would write for `unified_diff`, as git parses it.

    Runs `git apply --numstat` outside any repository (it only parses; it
    applies nothing). Raises `DiffPolicyViolation("diff-unparseable")` if
    git rejects the patch.
    """

    with tempfile.TemporaryDirectory(prefix="vuoro-patch-") as scratch:
        patch = os.path.join(scratch, "intent.patch")
        with open(patch, "w", encoding="utf-8") as handle:
            handle.write(unified_diff)
        result = _git("apply", "--numstat", "-z", patch, cwd=scratch)
    if result.returncode != 0:
        raise DiffPolicyViolation("diff-unparseable", "git apply could not parse the patch")
    paths = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        _added, _deleted, path = record.split(b"\t", 2)
        paths.append(path.decode("utf-8", "surrogateescape"))
    return tuple(paths)


def staged_changes(repo_path: str) -> tuple[StagedChange, ...]:
    """What the index of `repo_path` changes relative to HEAD."""

    raw = _git("diff", "--cached", "--raw", "-z", "--no-renames", cwd=repo_path)
    numstat = _git("diff", "--cached", "--numstat", "-z", "--no-renames", cwd=repo_path)
    if raw.returncode != 0 or numstat.returncode != 0:
        raise DiffPolicyViolation("diff-unreadable", "git diff --cached failed")

    binary: dict[str, bool] = {}
    for record in numstat.stdout.split(b"\0"):
        if not record:
            continue
        added, deleted, path = record.split(b"\t", 2)
        binary[path.decode("utf-8", "surrogateescape")] = added == b"-" or deleted == b"-"

    tokens = raw.stdout.split(b"\0")
    changes = []
    index = 0
    while index < len(tokens) and tokens[index]:
        meta = tokens[index].decode()
        path = tokens[index + 1].decode("utf-8", "surrogateescape")
        index += 2
        old_mode, new_mode, _old_sha, _new_sha, status = meta.lstrip(":").split(" ")
        changes.append(
            StagedChange(
                path=path,
                status=status,
                old_mode=old_mode,
                new_mode=new_mode,
                binary=binary.get(path, False),
            )
        )
    return tuple(changes)


def _is_path_safe(path: str) -> bool:
    if not path or path.startswith("/") or "\x00" in path:
        return False
    parts = path.split("/")
    return not any(part in ("", "..", ".git") for part in parts)


def check_changes(changes: Iterable[StagedChange], policy: DiffPolicy) -> None:
    """Raise `DiffPolicyViolation` for the first refused change."""

    changes = tuple(changes)
    if not changes:
        raise DiffPolicyViolation("empty-diff", "the diff changes nothing")
    for change in changes:
        path = change.path
        if not _is_path_safe(path):
            raise DiffPolicyViolation("path-outside-repository", path)
        if change.binary:
            raise DiffPolicyViolation("binary-patch-refused", path)
        modes = {change.old_mode, change.new_mode}
        if _SYMLINK in modes:
            raise DiffPolicyViolation("symlink-refused", path)
        if _GITLINK in modes:
            raise DiffPolicyViolation("submodule-refused", path)
        if change.status == "A" and change.new_mode != _REGULAR:
            raise DiffPolicyViolation("mode-change-refused", path)
        if change.status in ("M", "T") and change.old_mode != change.new_mode:
            raise DiffPolicyViolation("mode-change-refused", path)
        if change.status not in ("A", "D", "M"):
            raise DiffPolicyViolation("diff-not-supported", f"{change.status} {path}")
        if change.status == "D" and not policy.is_allowlisted(path):
            raise DiffPolicyViolation("path-not-allowlisted", path)
        if policy.is_protected(path) and not policy.is_allowlisted(path):
            raise DiffPolicyViolation("protected-path-refused", path)
        if change.new_mode not in (_REGULAR, _EXECUTABLE, _ABSENT):  # pragma: no cover
            raise DiffPolicyViolation("mode-change-refused", path)
