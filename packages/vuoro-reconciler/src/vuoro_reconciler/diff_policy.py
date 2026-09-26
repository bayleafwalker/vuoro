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
- binary content is refused three times over, because a `GIT binary patch`
  literal carries no NUL byte (it is base85) and a base-commit attribute
  such as `*.md diff` makes git's own binary detection say "text":
  `check_patch_text` refuses a NUL byte or any binary-patch line
  (`GIT binary patch`, `literal `, `delta `, `Binary files `) before
  applying; `check_patch_is_text` refuses any hunk `git apply --numstat`
  itself reports as binary, whatever the repository's attributes; and
  `check_staged_content` reads every staged blob after applying and
  refuses NUL, C0/C1 control characters other than tab and LF, DEL, or
  invalid UTF-8 -- whatever the attributes say;
- `check_changes` refuses what section 7 refuses: binary content, mode
  changes (including a new file created executable), symlinks, submodules,
  unsafe paths, deletes outside the repository's path allowlist, and CI
  workflow or protected paths the allowlist does not name -- plus any git
  control file (`.gitattributes`, `.gitmodules`, `.mailmap`, `.gitignore`,
  any other `.git*` name, `info/attributes`-style paths), which no
  allowlist can admit: a proposer-supplied `.gitattributes` could
  otherwise re-label binary content as text or name a filter driver.

With `--no-renames` a rename is seen as a delete of the old path plus an
add of the new one, so the old path must be on the allowlist exactly as a
delete must be.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import fnmatch
import os
import re
import subprocess
import tempfile

from .gitenv import run_git

__all__ = [
    "DiffPolicy",
    "DiffPolicyViolation",
    "StagedChange",
    "check_changes",
    "check_patch_is_text",
    "check_patch_text",
    "check_staged_content",
    "is_git_control_path",
    "is_unsupported_path",
    "patch_paths",
    "staged_changes",
]

#: Same fixed patterns the edge uses; `fnmatch`'s `*` spans `/`.
CI_WORKFLOW_PATTERNS: tuple[str, ...] = (
    ".github/workflows/*",
    ".forgejo/workflows/*",
    ".gitea/workflows/*",
)

#: Final path components git reads as configuration of the checkout.
_GIT_INFO_FILES = frozenset({"attributes", "exclude", "sparse-checkout"})


def is_git_control_path(path: str) -> bool:
    """True for a file git itself reads as configuration: `.gitattributes`,
    `.gitmodules`, `.gitignore`, `.mailmap`, any other `.git*` name (but
    not the inert `.gitkeep`), or an `info/attributes`-style path."""

    parts = path.lower().split("/")
    name = parts[-1]
    if name.startswith(".git") and name != ".gitkeep":
        return True
    if name == ".mailmap":
        return True
    return len(parts) >= 2 and parts[-2] == "info" and name in _GIT_INFO_FILES

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
    #: The staged blob's full object id (from `--raw --no-abbrev`); the
    #: content scan reads by this id, never by path.
    new_sha: str = ""


def _git(*args: str, cwd: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return run_git(*args, cwd=cwd, text=False, input=input)


#: Lines that only ever appear in a binary patch, outside any hunk (inside a
#: hunk every line starts with ' ', '+', '-' or '\\').
_BINARY_PATCH_LINES: tuple[str, ...] = ("GIT binary patch", "literal ", "delta ", "Binary files ")


def check_patch_text(unified_diff: str) -> None:
    """Refuse a NUL byte anywhere in the patch text, and any binary-patch
    line: a `GIT binary patch` literal is base85 and carries no NUL, so the
    NUL check alone would let binary (or control-character) content in."""

    if "\x00" in unified_diff:
        raise DiffPolicyViolation("binary-patch-refused", "the patch contains a NUL byte")
    for line in unified_diff.splitlines():
        if line.startswith(_BINARY_PATCH_LINES):
            raise DiffPolicyViolation("binary-patch-refused", f"binary patch line {line[:40]!r}")


def _numstat(unified_diff: str) -> list[tuple[bytes, bytes, str]]:
    """`git apply --numstat -z` rows for `unified_diff`, parsed outside any
    repository (it only parses; it applies nothing)."""

    with tempfile.TemporaryDirectory(prefix="vuoro-patch-") as scratch:
        patch = os.path.join(scratch, "intent.patch")
        with open(patch, "w", encoding="utf-8") as handle:
            handle.write(unified_diff)
        result = _git("apply", "--numstat", "-z", patch, cwd=scratch)
    if result.returncode != 0:
        raise DiffPolicyViolation("diff-unparseable", "git apply could not parse the patch")
    rows = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        added, deleted, path = record.split(b"\t", 2)
        rows.append((added, deleted, path.decode("utf-8", "surrogateescape")))
    return rows


def check_patch_is_text(unified_diff: str) -> None:
    """Refuse any hunk git's own patch parser reports as binary (`-` in
    `--numstat`). git apply has no switch to turn binary support off, so
    this is how the reconciler applies without it: a patch with a binary
    hunk never reaches `git apply`. The patch is parsed outside the
    checkout, so no attribute in the repository can re-label it."""

    for added, deleted, path in _numstat(unified_diff):
        if added == b"-" or deleted == b"-":
            raise DiffPolicyViolation("binary-patch-refused", path)


def patch_paths(unified_diff: str) -> tuple[str, ...]:
    """Every path `unified_diff` touches, as git parses it: what
    `git apply --numstat` would write, plus every rename/copy source (which
    `--numstat` omits).

    Runs `git apply --numstat` outside any repository (it only parses; it
    applies nothing). Raises `DiffPolicyViolation("diff-unparseable")` if
    git rejects the patch.
    """

    paths = [path for _added, _deleted, path in _numstat(unified_diff)]
    # Sources are taken from every such line, wherever it appears: an extra
    # path only makes a policy's path_globs harder to satisfy, never easier.
    for line in unified_diff.splitlines():
        for prefix in ("rename from ", "copy from "):
            if line.startswith(prefix):
                paths.append(line[len(prefix) :])
    return tuple(dict.fromkeys(paths))


def staged_changes(repo_path: str) -> tuple[StagedChange, ...]:
    """What the index of `repo_path` changes relative to HEAD."""

    raw = _git("diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev", cwd=repo_path)
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
        old_mode, new_mode, _old_sha, new_sha, status = meta.lstrip(":").split(" ")
        changes.append(
            StagedChange(
                path=path,
                status=status,
                old_mode=old_mode,
                new_mode=new_mode,
                binary=binary.get(path, False),
                new_sha=new_sha,
            )
        )
    return tuple(changes)


_OBJECT_ID = re.compile(rb"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _read_blobs(repo_path: str, object_ids: list[str]) -> dict[str, bytes]:
    """The content of every blob in `object_ids`, read by object id through
    one `git cat-file --batch` call. Any object that is missing, not a
    blob, or not reported exactly as asked is a refusal -- never skipped."""

    if not object_ids:
        return {}
    request = "".join(f"{oid}\n" for oid in object_ids).encode("ascii")
    result = _git("cat-file", "--batch", cwd=repo_path, input=request)
    if result.returncode != 0:
        raise DiffPolicyViolation("diff-unreadable", "git cat-file --batch failed")
    out = result.stdout
    blobs: dict[str, bytes] = {}
    offset = 0
    for oid in object_ids:
        newline = out.find(b"\n", offset)
        if newline < 0:
            raise DiffPolicyViolation("diff-unreadable", f"truncated batch output at {oid}")
        header = out[offset:newline].split(b" ")
        offset = newline + 1
        if len(header) != 3 or header[0].decode("ascii", "replace") != oid or header[1] != b"blob":
            raise DiffPolicyViolation("blob-missing", f"{oid}: {b' '.join(header)[:80]!r}")
        if not header[2].isdigit():
            raise DiffPolicyViolation("diff-unreadable", f"{oid}: bad size")
        size = int(header[2])
        if offset + size + 1 > len(out) or out[offset + size : offset + size + 1] != b"\n":
            raise DiffPolicyViolation("diff-unreadable", f"{oid}: truncated content")
        blobs[oid] = out[offset : offset + size]
        offset += size + 1
    if offset != len(out):
        raise DiffPolicyViolation("diff-unreadable", "unexpected trailing batch output")
    return blobs


def _check_text(path: str, data: bytes) -> None:
    if b"\0" in data:
        raise DiffPolicyViolation("binary-content-refused", path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DiffPolicyViolation("non-utf8-content-refused", path) from None
    for index, char in enumerate(text):
        code = ord(char)
        if char == "\r" and text[index + 1 : index + 2] == "\n":
            continue  # CRLF line ending: allowed; a lone CR is not
        if (code < 0x20 and char not in "\t\n") or 0x7F <= code <= 0x9F:
            raise DiffPolicyViolation("control-character-content-refused", f"{path}: U+{code:04X}")


def check_staged_content(repo_path: str, changes: Iterable[StagedChange]) -> None:
    """Read every added or modified blob and refuse NUL (binary), C0/C1
    control characters other than tab, LF and the CR of a CRLF, DEL, or
    invalid UTF-8 -- whatever `.gitattributes` in the base commit says
    about the file. This is the last word on content: it looks at the bytes
    that would be committed, not at any patch or attribute.

    Blobs are read by the staged object id from `--raw`, never by path: a
    path such as `0:docs/readme.md` would otherwise be read as the revision
    `:0:docs/readme.md` (stage 0 of another file) and scan the wrong blob."""

    wanted: list[tuple[StagedChange, str]] = []
    for change in changes:
        if change.status == "D":
            continue
        if not _OBJECT_ID.match(change.new_sha.encode("ascii", "replace")) or not change.new_sha.strip("0"):
            raise DiffPolicyViolation("blob-missing", f"{change.path}: no staged object id")
        wanted.append((change, change.new_sha))
    blobs = _read_blobs(repo_path, list(dict.fromkeys(oid for _change, oid in wanted)))
    for change, oid in wanted:
        _check_text(change.path, blobs[oid])


def is_unsupported_path(path: str) -> bool:
    """True for a path git could read as something else: any ':' (revision
    or pathspec syntax -- `:0:<path>` is stage 0 of another file,
    `:(glob)*` is pathspec magic) or a leading '-' (an option)."""

    return ":" in path or path.startswith("-")


def _is_path_safe(path: str) -> bool:
    if not path or path.startswith("/") or "\x00" in path:
        return False
    if not path.isprintable():
        return False
    parts = path.split("/")
    return not any(part in ("", ".", "..", ".git") for part in parts)


def check_changes(changes: Iterable[StagedChange], policy: DiffPolicy) -> None:
    """Raise `DiffPolicyViolation` for the first refused change."""

    changes = tuple(changes)
    if not changes:
        raise DiffPolicyViolation("empty-diff", "the diff changes nothing")
    for change in changes:
        path = change.path
        if is_unsupported_path(path):
            raise DiffPolicyViolation("unsupported-path", path)
        if not _is_path_safe(path):
            raise DiffPolicyViolation("path-outside-repository", path)
        if is_git_control_path(path):
            raise DiffPolicyViolation("git-control-file-refused", path)
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
