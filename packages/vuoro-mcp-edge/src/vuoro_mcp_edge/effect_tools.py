"""Toolset builder owned by E3 (agentops#2467): propose_effect and get_effect -- bucket "propose" (vuoro:effect.propose -> effect:propose). Diff-shaped intents only; no code path here may execute an effect.

`propose_effect` accepts a unified diff, validates it structurally (no
binary patch, no mode change, no symlink, no submodule, no path outside the
repository, no rename/delete outside the repository's path allowlist, no CI
workflow or SOPS-matched path unless the allowlist names it, and it must
look like a diff at all -- an imperative payload is refused), binds it to
the caller's E2 run handle, and records it through `IntentStore`. It never
applies anything: there is no import here of a git client, a process
runner or a provider credential, and no route to one. Applying the diff
and opening a PR is `packages/vuoro-reconciler`'s job, running wherever the
trusted-service-boundary design places it -- not here, and not from here.

`get_effect` reports an intent's current state
(proposed/accepted/rejected/applied/failed), and the trusted-side acceptor
once there is one, to the caller that proposed it. Nothing here moves an
intent out of `proposed` (TS-16): acceptance is an operator or an opt-in
policy on the trusted side (`vuoro_reconciler.acceptance`).

The intent store's durable home is with its lifecycle owner. Per
`vuoro-cloud/13-REPO-OWNERSHIP-AND-CHANGE-MATRIX.md` ("Hosted action
state" -> owner actionq) and the trusted-service boundary design memo
("ActionQ owns execution lifecycle and accepted action outcomes"), that
owner is ActionQ, reached the same way `ShellWorkSource` reaches sprintctl:
through the runtime shell's invoke API, with no credential held here.
ActionQ's served catalog does not yet publish that operation (see
`13-REPO-OWNERSHIP-AND-CHANGE-MATRIX.md`'s actionq row: "connector-safe
claim and lease operations... idempotent outcome reporting" are still
required changes, not shipped ones), so -- exactly like `UnavailableRunRegistry`
before E2 -- `build_toolset` composes `UnavailableIntentStore` today and
`propose_effect`/`get_effect` fail closed with `effects-unavailable` until a
durable store is wired in at composition. `InMemoryIntentStore` is the
reference behaviour this module's own tests use; a real `ShellIntentStore`
lands with ActionQ's operation, the same way E2 wires its `RunRegistry`.

Only the owning work item edits this module; see
docs/plans/2026-09-26-e2-e3-shared-contract.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol
import fnmatch
import json
import re
import secrets

from .idempotency import (
    IDEMPOTENCY_KEY_SCHEMA,
    InMemoryIdempotencyLedger,
    StoredResult,
    replay_or_conflict,
    request_digest,
    require_key,
)
from .runs import RunBinding, binding_for
from .toolsets import ToolFailure, ToolSet, ToolSpec, ToolsetContext, WRITE_ANNOTATIONS

if TYPE_CHECKING:
    from .runs import RunRegistry
    from .work_source import ForwardedIdentity

__all__ = [
    "ENV_REPOSITORY_POLICY",
    "INTENT_ID",
    "EffectIntent",
    "InMemoryIntentStore",
    "IntentStore",
    "RepositoryEffectPolicy",
    "UnavailableIntentStore",
    "build_toolset",
]

#: `effect_` + 26 Crockford base32 characters, mirroring `runs.RUN_ID`.
INTENT_ID = re.compile(r"^effect_[0-9A-HJKMNP-TV-Z]{26}$")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Section 7 size caps.
_TITLE_MAX = 200
_RATIONALE_MAX = 4000
_DIFF_MAX_BYTES = 256 * 1024

_BASE_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")

#: CI workflow paths are always refused unless the repository's allowlist
#: names them. `fnmatch` patterns: `*` matches across `/`, so a single `*`
#: already behaves like a recursive glob for these purposes.
_CI_WORKFLOW_PATTERNS: tuple[str, ...] = (
    ".github/workflows/*",
    ".forgejo/workflows/*",
    ".gitea/workflows/*",
)

#: Final path components git reads as checkout configuration (see
#: `_is_git_control_path`); never admissible, whatever the allowlist says.
_GIT_INFO_FILES = frozenset({"attributes", "exclude", "sparse-checkout"})


def _is_git_control_path(path: str) -> bool:
    """`.gitattributes`, `.gitmodules`, `.gitignore`, `.mailmap`, any other
    `.git*` name (but not the inert `.gitkeep`), or an `info/attributes`-style
    path. A proposer-supplied `.gitattributes` could re-label binary content
    as text or name a filter driver the reconciler's `git add` would run."""

    parts = path.lower().split("/")
    name = parts[-1]
    if name.startswith(".git") and name != ".gitkeep":
        return True
    if name == ".mailmap":
        return True
    return len(parts) >= 2 and parts[-2] == "info" and name in _GIT_INFO_FILES

_NOT_YOURS = "no effect intent with that id belongs to the caller"

_READ_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

#: A builder reads its own settings from `context.env`, prefixed like this.
ENV_REPOSITORY_POLICY = "VUORO_MCP_EFFECT_REPOSITORY_POLICY"


# ---------------------------------------------------------------------------
# Repository policy: what a repository's diff may touch.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryEffectPolicy:
    """What `propose_effect` allows for one repository.

    `path_allowlist` is checked for every rename or delete (both sides of a
    rename) and is also what exempts a CI-workflow or SOPS-matched path from
    the blanket refusal below. Patterns are `fnmatch` patterns against the
    repository-relative path; a bare `*` already spans `/`.

    An unconfigured repository gets the empty default: no renames or
    deletes are allowed, and no CI-workflow or SOPS-matched path is ever
    exempt. This is deliberately fail-closed, the same posture
    `UnavailableRunRegistry` and `UnavailableIntentStore` take: an operator
    opts a repository in, rather than the edge guessing what is safe.
    """

    path_allowlist: frozenset[str] = frozenset()
    #: Additional protected patterns for this repository (its SOPS-matched
    #: paths), on top of the fixed CI-workflow patterns above.
    protected_path_patterns: frozenset[str] = frozenset()

    def is_allowlisted(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.path_allowlist)

    def is_protected(self, path: str) -> bool:
        patterns = (*_CI_WORKFLOW_PATTERNS, *self.protected_path_patterns)
        return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


_EMPTY_POLICY = RepositoryEffectPolicy()


def _load_repository_policies(env: Mapping[str, str]) -> dict[str, RepositoryEffectPolicy]:
    """Parse `ENV_REPOSITORY_POLICY`: `{repo_id: {path_allowlist: [...],
    protected_path_patterns: [...]}}`. Absent or blank means no repository is
    configured, so every repository gets the fail-closed default."""

    raw = env.get(ENV_REPOSITORY_POLICY, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{ENV_REPOSITORY_POLICY} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{ENV_REPOSITORY_POLICY} must be a JSON object")
    policies: dict[str, RepositoryEffectPolicy] = {}
    for repo_id, entry in parsed.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{ENV_REPOSITORY_POLICY}[{repo_id!r}] must be an object")
        allowlist = entry.get("path_allowlist", [])
        protected = entry.get("protected_path_patterns", [])
        if not isinstance(allowlist, list) or not all(isinstance(p, str) for p in allowlist):
            raise ValueError(f"{ENV_REPOSITORY_POLICY}[{repo_id!r}].path_allowlist must be a list of strings")
        if not isinstance(protected, list) or not all(isinstance(p, str) for p in protected):
            raise ValueError(
                f"{ENV_REPOSITORY_POLICY}[{repo_id!r}].protected_path_patterns must be a list of strings"
            )
        policies[repo_id] = RepositoryEffectPolicy(
            path_allowlist=frozenset(allowlist), protected_path_patterns=frozenset(protected)
        )
    return policies


def _policy_for(repository: str, policies: Mapping[str, RepositoryEffectPolicy]) -> RepositoryEffectPolicy:
    return policies.get(repository, _EMPTY_POLICY)


# ---------------------------------------------------------------------------
# Diff validation: structural refusals only. Whether the diff applies to
# base_commit is the reconciler's job, on a real checkout it has and the
# edge does not (section 7).
#
# The parser is a strict grammar, not a scan for the first header: every
# line must be a `diff --git` header, a recognised extended header, a
# `---`/`+++` pair, a hunk header, or one of the lines that hunk header
# counted. Anything else -- a preamble before the first header, a second
# old-style `---`/`+++` hunk trailing a valid block, a stray line -- is
# refused, because `git apply` would read it as more patch, touching paths
# this validation never saw. (The reconciler independently re-derives
# what was touched from the applied result; this is the edge's half.)
# ---------------------------------------------------------------------------

#: `[0-9]`, never `\d`: in a `str` pattern `\d` also matches non-ASCII digits.
_HUNK_RE = re.compile(r"^@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: .*)?$")
_MODE_RE = re.compile(r"^(old mode|new mode|deleted file mode|new file mode) ([0-9]{6})$")
_INDEX_RE = re.compile(r"^index [0-9a-f]{4,64}\.\.[0-9a-f]{4,64}(?: ([0-9]{6}))?$")
_SIMILARITY_RE = re.compile(r"^(?:dis)?similarity index [0-9]{1,3}%$")
_PATH_LINE_RE = re.compile(r"^(rename from|rename to|copy from|copy to) (.+)$")

_REGULAR_MODE = "100644"
_EXECUTABLE_MODE = "100755"
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"


@dataclass
class _FileChange:
    header: str = ""
    old_path: str | None = None
    new_path: str | None = None
    is_rename: bool = False
    is_binary: bool = False
    is_mode_change: bool = False
    is_symlink: bool = False
    is_submodule: bool = False


def _unsupported(message: str) -> ToolFailure:
    return ToolFailure("diff-not-supported", message)


def _is_path_safe(path: str) -> bool:
    if not path or path.startswith("/") or "\x00" in path:
        return False
    parts = path.split("/")
    return not any(part in ("", ".", "..", ".git") for part in parts)


def _mark_mode(mode: str, change: _FileChange) -> None:
    if mode == _SYMLINK_MODE:
        change.is_symlink = True
    elif mode == _GITLINK_MODE:
        change.is_submodule = True


def _side_path(value: str, prefix: str) -> str | None:
    """`a/<path>` / `b/<path>` -> `<path>`; `/dev/null` -> None. Quoted,
    tab-suffixed (traditional timestamp) or unprefixed names are refused
    rather than guessed at: git would strip or unquote them differently."""

    if value == "/dev/null":
        return None
    if not value.startswith(prefix) or "\t" in value or value.startswith('"'):
        raise _unsupported(f"unsupported file name line {value!r}")
    return value[len(prefix) :]


def _parse_hunks(lines: list[str], index: int, change: _FileChange) -> int:
    """Consume one or more hunks starting at `lines[index]`; return the
    index of the first line after them."""

    if index >= len(lines) or not lines[index].startswith("@@"):
        raise _unsupported("a ---/+++ pair must be followed by a hunk")
    while index < len(lines) and lines[index].startswith("@@"):
        match = _HUNK_RE.match(lines[index])
        if match is None:
            raise _unsupported(f"malformed hunk header {lines[index]!r}")
        old_left = int(match.group(2) if match.group(2) is not None else 1)
        new_left = int(match.group(4) if match.group(4) is not None else 1)
        index += 1
        while old_left > 0 or new_left > 0:
            if index >= len(lines):
                raise _unsupported("a hunk ends before its line counts are met")
            line = lines[index]
            if line == "" or line.startswith(" "):
                old_left -= 1
                new_left -= 1
            elif line.startswith("-"):
                old_left -= 1
                if line.startswith("-Subproject commit "):
                    change.is_submodule = True
            elif line.startswith("+"):
                new_left -= 1
                if line.startswith("+Subproject commit "):
                    change.is_submodule = True
            elif not line.startswith("\\"):
                raise _unsupported(f"unexpected line inside a hunk: {line[:80]!r}")
            if old_left < 0 or new_left < 0:
                raise _unsupported("a hunk has more lines than its header counts")
            index += 1
        if index < len(lines) and lines[index].startswith("\\"):
            index += 1  # "\ No newline at end of file" after the last line
    return index


def _parse_file(lines: list[str], index: int) -> tuple[_FileChange, int]:
    change = _FileChange(header=lines[index])
    index += 1
    header_paths: dict[str, str] = {}

    # Extended header lines, in any order, until something else.
    while index < len(lines):
        line = lines[index]
        mode = _MODE_RE.match(line)
        path_line = _PATH_LINE_RE.match(line)
        index_line = _INDEX_RE.match(line)
        if mode is not None:
            kind, value = mode.groups()
            _mark_mode(value, change)
            if kind in ("old mode", "new mode"):
                change.is_mode_change = True
            elif kind == "new file mode" and value not in (_REGULAR_MODE, _SYMLINK_MODE, _GITLINK_MODE):
                # A file created executable (100755) is a mode change too.
                change.is_mode_change = True
        elif index_line is not None:
            if index_line.group(1) is not None:
                _mark_mode(index_line.group(1), change)
        elif _SIMILARITY_RE.match(line):
            pass
        elif path_line is not None:
            kind, value = path_line.groups()
            if kind in header_paths:
                raise _unsupported(f"duplicate {kind!r} line")
            header_paths[kind] = value
            change.is_rename = True
        else:
            break
        index += 1

    if index < len(lines) and (
        lines[index].startswith("Binary files ") or lines[index] == "GIT binary patch"
    ):
        change.is_binary = True
        raise ToolFailure("binary-patch-refused", "binary patches are not accepted")

    old_from_lines = new_from_lines = None
    has_file_lines = False
    if index < len(lines) and lines[index].startswith("--- "):
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise _unsupported("a '---' line must be followed by a '+++' line")
        old_from_lines = _side_path(lines[index][4:], "a/")
        new_from_lines = _side_path(lines[index + 1][4:], "b/")
        has_file_lines = True
        index = _parse_hunks(lines, index + 2, change)

    old_named = header_paths.get("rename from", header_paths.get("copy from"))
    new_named = header_paths.get("rename to", header_paths.get("copy to"))
    if change.is_rename and (old_named is None or new_named is None):
        raise _unsupported("a rename or copy must name both sides")
    if has_file_lines and change.is_rename and (old_from_lines, new_from_lines) != (old_named, new_named):
        raise _unsupported("rename/copy lines disagree with the ---/+++ lines")

    if has_file_lines:
        change.old_path, change.new_path = old_from_lines, new_from_lines
    elif change.is_rename:
        change.old_path, change.new_path = old_named, new_named
    else:
        # A pure mode change carries no ---/+++ lines: only the symmetric
        # `a/<path> b/<path>` header can name the path.
        body = change.header[len("diff --git ") :]
        half, remainder = divmod(len(body) - 1, 2)
        if remainder or body[half] != " " or not body.startswith("a/"):
            raise _unsupported("a diff section names no path")
        left, right = body[:half], body[half + 1 :]
        if left[2:] != right[2:] or not right.startswith("b/"):
            raise _unsupported("a diff section names no path")
        change.old_path = change.new_path = left[2:]

    left_path = change.old_path if change.old_path is not None else change.new_path
    right_path = change.new_path if change.new_path is not None else change.old_path
    if left_path is None or change.header != f"diff --git a/{left_path} b/{right_path}":
        raise _unsupported("the diff --git header disagrees with the paths below it")
    return change, index


def _parse_diff(diff_text: str) -> list[_FileChange]:
    lines = diff_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not any(line.startswith("diff --git ") for line in lines):
        raise _unsupported("the payload does not contain a unified diff (no 'diff --git' header)")
    changes = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("diff --git "):
            raise _unsupported(f"unrecognised line outside a diff section: {lines[index][:80]!r}")
        change, index = _parse_file(lines, index)
        changes.append(change)
    return changes


def validate_diff(unified_diff: str, *, policy: RepositoryEffectPolicy) -> None:
    """Raise `ToolFailure` on any refusal in section 7's list. Returns
    normally for a diff-shaped intent this policy allows."""

    if "\x00" in unified_diff:
        raise ToolFailure("binary-patch-refused", "the diff contains a NUL byte")
    for change in _parse_diff(unified_diff):
        touched = tuple(p for p in (change.old_path, change.new_path) if p is not None)
        if not touched:
            raise ToolFailure("diff-not-supported", "a diff section names no path")
        for path in touched:
            if not path.isprintable():
                # Control, bidi-override or other format characters in a
                # path could rewrite or reorder what an operator sees.
                raise ToolFailure(
                    "path-not-printable",
                    "a path contains a control or formatting character",
                )
            if not _is_path_safe(path):
                raise ToolFailure(
                    "path-outside-repository",
                    f"path {path!r} is outside the repository",
                )
            if _is_git_control_path(path):
                raise ToolFailure(
                    "git-control-file-refused",
                    f"path {path!r} is a git control file (.gitattributes, .gitmodules, ...)",
                )
        if change.is_binary:  # pragma: no cover - _parse_file raises first
            raise ToolFailure("binary-patch-refused", "binary patches are not accepted")
        if change.is_mode_change:
            raise ToolFailure("mode-change-refused", "file-mode changes are not accepted")
        if change.is_symlink:
            raise ToolFailure("symlink-refused", "symlinks are not accepted")
        if change.is_submodule:
            raise ToolFailure("submodule-refused", "submodule changes are not accepted")
        is_delete = change.old_path is not None and change.new_path is None
        if change.is_rename or is_delete:
            if not all(policy.is_allowlisted(path) for path in touched):
                raise ToolFailure(
                    "path-not-allowlisted",
                    "renames and deletes are restricted to the repository's path allowlist",
                )
        for path in touched:
            if policy.is_protected(path) and not policy.is_allowlisted(path):
                raise ToolFailure(
                    "protected-path-refused",
                    f"path {path!r} is a CI workflow or protected path not on the allowlist",
                )


# ---------------------------------------------------------------------------
# The intent record and its store.
# ---------------------------------------------------------------------------

#: Every state an intent can be in (only the last three are terminal).
_INTENT_STATES = frozenset({"proposed", "accepted", "rejected", "applied", "failed"})


@dataclass(frozen=True)
class EffectIntent:
    intent_id: str
    run_id: str
    binding: RunBinding
    repository: str
    base_commit: str
    title: str
    rationale: str
    unified_diff: str
    state: str = "proposed"
    #: Who moved it `proposed -> accepted` (or rejected it), as the trusted
    #: side recorded it: `{kind: "operator", subject}` or `{kind: "policy",
    #: policy_id, version, scope, config_digest}`. Never set by the edge.
    acceptor: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.state not in _INTENT_STATES:
            raise ValueError(f"unknown effect intent state {self.state!r}")


class IntentStore(Protocol):
    """Where proposed effect intents live. Durable implementations live with
    the lifecycle owner (ActionQ), not in the edge -- see the module
    docstring. Shares the idempotency ledger's `lookup` shape (section 5).

    There is deliberately no transition method: `proposed -> accepted` is
    the trusted side's (an operator or an opt-in policy, never the
    proposer), so nothing the edge holds can accept an intent."""

    async def lookup(self, workspace_id: str, tool: str, key: str) -> StoredResult | None: ...

    async def create(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult, intent: EffectIntent
    ) -> StoredResult:
        """Atomically record the ledger row for (workspace, tool, key) and
        `intent` -- or, if a row already exists, record neither and return
        that row. First write wins (section 5): a racing duplicate never
        leaves an orphan `proposed` intent behind."""

    async def get(self, intent_id: str, caller: RunBinding) -> EffectIntent:
        """The intent if it belongs to `caller`.

        Raises `ToolFailure("effect-not-found", ...)` for an unknown,
        malformed id AND for one bound to a different caller: one code and
        message for both, so a caller cannot probe which ids exist (the
        same posture as `RunRegistry.resolve`).
        """


class UnavailableIntentStore:
    """The store until ActionQ's intent-lifecycle operation lands: every
    call fails closed, exactly as `UnavailableRunRegistry` does for runs."""

    async def lookup(self, workspace_id: str, tool: str, key: str) -> StoredResult | None:
        raise ToolFailure("effects-unavailable", "the effect intent store is not available yet")

    async def create(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult, intent: EffectIntent
    ) -> StoredResult:
        raise ToolFailure("effects-unavailable", "the effect intent store is not available yet")

    async def get(self, intent_id: str, caller: RunBinding) -> EffectIntent:
        raise ToolFailure("effects-unavailable", "the effect intent store is not available yet")


class InMemoryIntentStore:
    """Reference behaviour for tests. Not durable; never deploy it.

    Composes `InMemoryIdempotencyLedger` for the shared idempotency shape,
    so it is provably equivalent to the ledger's own reference behaviour.
    """

    def __init__(self) -> None:
        self._ledger = InMemoryIdempotencyLedger()
        self._intents: dict[str, EffectIntent] = {}

    async def lookup(self, workspace_id: str, tool: str, key: str) -> StoredResult | None:
        return await self._ledger.lookup(workspace_id, tool, key)

    async def create(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult, intent: EffectIntent
    ) -> StoredResult:
        # The ledger's store never suspends, so the row and the intent are
        # written together on one event loop: the intent exists only if
        # this call's row is the one that won.
        winner = await self._ledger.store(workspace_id, tool, key, stored)
        if winner is stored:
            self._intents[intent.intent_id] = intent
        return winner

    def seed(self, intent: EffectIntent) -> None:
        """Test-only: place an intent directly (e.g. one bound elsewhere)."""

        self._intents[intent.intent_id] = intent

    async def get(self, intent_id: str, caller: RunBinding) -> EffectIntent:
        intent = self._intents.get(intent_id) if INTENT_ID.fullmatch(intent_id or "") else None
        if intent is None or intent.binding != caller:
            raise ToolFailure("effect-not-found", _NOT_YOURS)
        return intent

    def set_state(
        self, intent_id: str, state: str, acceptor: Mapping[str, Any] | None = None
    ) -> None:
        """Test-only: simulate the reconciler reporting a settlement back.

        A real store's transition path is out of the edge's reach entirely
        (section 7: "no code path from the edge to any executor"); this
        exists so `get_effect` tests can prove every reported state without
        one."""

        self._intents[intent_id] = replace(self._intents[intent_id], state=state, acceptor=acceptor)


def _new_intent_id() -> str:
    return "effect_" + "".join(secrets.choice(_CROCKFORD) for _ in range(26))


# ---------------------------------------------------------------------------
# Tool definitions.
# ---------------------------------------------------------------------------

_PROPOSE_DEFINITION: dict[str, Any] = {
    "name": "propose_effect",
    "title": "Propose a repository effect",
    "description": (
        "Proposes a diff-shaped change to one repository: a unified diff "
        "applied against base_commit, with a title and rationale. This "
        "tool never applies anything and never touches the repository "
        "itself -- it records a proposal and returns {intent_id, state: "
        "'proposed'}. Only a trusted-side operator or policy -- never this "
        "caller and never any tool here -- can accept it; a trusted "
        "reconciler then applies the diff, signs the commit and opens a PR; "
        "call get_effect with intent_id to see the outcome. Refused "
        "before any of that: binary patches, file-mode changes, symlinks, "
        "submodules, paths outside the repository, renames or deletes "
        "outside the repository's path allowlist, CI workflow paths and "
        "protected (e.g. SOPS-matched) paths unless the allowlist names "
        "them, git control files (.gitattributes, .gitmodules, .mailmap, "
        ".gitignore, ...) always, NUL bytes, control or bidi-override "
        "characters in title or rationale, and any payload that is not a "
        "unified diff. run_id must "
        "come from register_run and must be bound to the same caller and "
        "repository."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run handle from register_run (E2).",
            },
            "repository": {
                "type": "string",
                "maxLength": 200,
                "description": "The repository this effect targets; must match the caller's bound repository.",
            },
            "base_commit": {
                "type": "string",
                "pattern": _BASE_COMMIT.pattern,
                "description": "The 40-hex commit the diff applies to.",
            },
            "title": {"type": "string", "minLength": 1, "maxLength": _TITLE_MAX},
            "rationale": {"type": "string", "minLength": 1, "maxLength": _RATIONALE_MAX},
            "unified_diff": {
                "type": "string",
                "minLength": 1,
                "description": f"A unified diff, UTF-8, at most {_DIFF_MAX_BYTES} bytes.",
            },
            "idempotency_key": IDEMPOTENCY_KEY_SCHEMA,
        },
        "required": [
            "run_id",
            "repository",
            "base_commit",
            "title",
            "rationale",
            "unified_diff",
            "idempotency_key",
        ],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}

_GET_DEFINITION: dict[str, Any] = {
    "name": "get_effect",
    "title": "Get an effect intent's state",
    "description": (
        "Reports one effect intent's current state: proposed, accepted, "
        "rejected, applied or failed, plus who accepted or rejected it "
        "(acceptor) once the trusted side has. Read-only: it does not "
        "change the intent. An unknown intent_id, or one bound to a different caller, "
        "is a tool error with code effect-not-found (one code for both, so "
        "a caller cannot probe which ids exist)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"intent_id": {"type": "string"}},
        "required": ["intent_id"],
        "additionalProperties": False,
    },
    "annotations": _READ_ANNOTATIONS,
}


def _require_str(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ToolFailure("invalid-arguments", f"{name} is required and must be a non-empty string")
    return value


def _refuse_unprintable(name: str, value: str, *, allowed: str) -> None:
    """Refuse control characters (ESC, CR, other C0/C1), bidi overrides and
    isolates (U+202A-202E, U+2066-2069) and every other non-printable
    character: this text is shown to an operator deciding whether to
    accept, and must not be able to rewrite or reorder what they see."""

    for char in value:
        if char not in allowed and not char.isprintable():
            raise ToolFailure(
                "invalid-arguments",
                f"{name} contains a control or formatting character (U+{ord(char):04X})",
            )


def _parse_propose(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "run_id",
        "repository",
        "base_commit",
        "title",
        "rationale",
        "unified_diff",
        "idempotency_key",
    }
    if set(arguments) != allowed:
        unexpected = sorted(set(arguments) - allowed)
        missing = sorted(allowed - set(arguments))
        raise ToolFailure(
            "invalid-arguments",
            f"propose_effect requires exactly {sorted(allowed)}; "
            f"unexpected {unexpected}, missing {missing}",
        )
    run_id = _require_str(arguments, "run_id")
    repository = _require_str(arguments, "repository")
    if not _REPOSITORY.fullmatch(repository):
        raise ToolFailure("invalid-arguments", "repository is not a valid repository identifier")
    base_commit = _require_str(arguments, "base_commit")
    if not _BASE_COMMIT.fullmatch(base_commit):
        raise ToolFailure("invalid-arguments", "base_commit must be 40 hex characters")
    title = _require_str(arguments, "title")
    if len(title) > _TITLE_MAX:
        raise ToolFailure("invalid-arguments", f"title must be at most {_TITLE_MAX} characters")
    _refuse_unprintable("title", title, allowed="")
    rationale = _require_str(arguments, "rationale")
    if len(rationale) > _RATIONALE_MAX:
        raise ToolFailure("invalid-arguments", f"rationale must be at most {_RATIONALE_MAX} characters")
    _refuse_unprintable("rationale", rationale, allowed="\n\t")
    unified_diff = _require_str(arguments, "unified_diff")
    if len(unified_diff.encode("utf-8")) > _DIFF_MAX_BYTES:
        raise ToolFailure("invalid-arguments", f"unified_diff must be at most {_DIFF_MAX_BYTES} bytes")
    key = require_key(arguments)
    return {
        "run_id": run_id,
        "repository": repository,
        "base_commit": base_commit,
        "title": title,
        "rationale": rationale,
        "unified_diff": unified_diff,
        "idempotency_key": key,
    }


def _parse_get(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) != {"intent_id"}:
        raise ToolFailure("invalid-arguments", "get_effect accepts exactly intent_id")
    return {"intent_id": _require_str(arguments, "intent_id")}


def _build(
    *,
    intent_store: IntentStore,
    runs: "RunRegistry",
    repository_policies: Mapping[str, RepositoryEffectPolicy],
) -> ToolSet:
    async def propose_effect(parsed: dict[str, Any], forwarded: "ForwardedIdentity") -> dict[str, Any]:
        binding = binding_for(forwarded)
        if parsed["repository"] != binding.repo_id:
            raise ToolFailure(
                "repository-mismatch",
                "repository does not match the caller's bound repository",
            )
        digest = request_digest("propose_effect", parsed)
        stored = await intent_store.lookup(binding.workspace_id, "propose_effect", parsed["idempotency_key"])
        replay = replay_or_conflict(stored, digest)
        if replay is not None:
            return dict(replay)

        # Bind to the caller's E2 run: a mismatched or unknown run_id fails
        # here with run-not-found (or runs-unavailable before E2 lands).
        await runs.resolve(parsed["run_id"], binding)

        policy = _policy_for(parsed["repository"], repository_policies)
        validate_diff(parsed["unified_diff"], policy=policy)

        intent = EffectIntent(
            intent_id=_new_intent_id(),
            run_id=parsed["run_id"],
            binding=binding,
            repository=parsed["repository"],
            base_commit=parsed["base_commit"],
            title=parsed["title"],
            rationale=parsed["rationale"],
            unified_diff=parsed["unified_diff"],
        )
        result = {"intent_id": intent.intent_id, "state": intent.state}
        winner = await intent_store.create(
            binding.workspace_id,
            "propose_effect",
            parsed["idempotency_key"],
            StoredResult(digest, result),
            intent,
        )
        # A racing writer with the same key won: replay its result, or
        # refuse if it was for different arguments.
        return dict(replay_or_conflict(winner, digest) or winner.result)

    async def get_effect(parsed: dict[str, Any], forwarded: "ForwardedIdentity") -> dict[str, Any]:
        binding = binding_for(forwarded)
        intent = await intent_store.get(parsed["intent_id"], binding)
        reported: dict[str, Any] = {"intent_id": intent.intent_id, "state": intent.state}
        if intent.acceptor is not None:
            reported["acceptor"] = dict(intent.acceptor)
        return reported

    return ToolSet(
        name="effect",
        tools=(
            ToolSpec("propose_effect", "propose", _PROPOSE_DEFINITION, _parse_propose, propose_effect),
            ToolSpec("get_effect", "propose", _GET_DEFINITION, _parse_get, get_effect),
        ),
    )


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    """The propose bucket as composed in production.

    Reads only `ENV_REPOSITORY_POLICY` from `context.env`. Any auto-accept
    setting (e.g. `VUORO_MCP_EFFECT_AUTO_ACCEPT`) is ignored by
    construction: auto-accept is a trusted-side config file read by the
    reconciler (`vuoro_reconciler.acceptance`), never an edge setting."""

    return _build(
        intent_store=UnavailableIntentStore(),
        runs=context.runs,
        repository_policies=_load_repository_policies(context.env),
    )
