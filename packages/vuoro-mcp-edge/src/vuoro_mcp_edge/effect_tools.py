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
(proposed/accepted/rejected/applied/failed) to the caller that proposed it.

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
_CI_WORKFLOW_PATTERNS: tuple[str, ...] = (".github/workflows/*", ".forgejo/workflows/*")

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
# ---------------------------------------------------------------------------


#: Fallback path source when a block has no `---`/`+++`/rename lines: the
#: symmetric case only (`a/<path> b/<path>` with the same path both sides).
_HEADER_RE = re.compile(r"^diff --git a/(?P<path>.+) b/(?P=path)$")


@dataclass
class _FileChange:
    old_path: str | None = None
    new_path: str | None = None
    is_rename: bool = False
    is_binary: bool = False
    is_mode_change: bool = False
    is_symlink: bool = False
    is_submodule: bool = False


def _is_path_safe(path: str) -> bool:
    if not path or path.startswith("/") or "\x00" in path:
        return False
    parts = path.split("/")
    return not any(part in ("", "..") for part in parts)


def _mode_marks(token: str, change: _FileChange) -> None:
    if "120000" in token:
        change.is_symlink = True
    if "160000" in token:
        change.is_submodule = True


def _split_diff_blocks(diff_text: str) -> list[list[str]]:
    lines = diff_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
    if not starts:
        return []
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        blocks.append(lines[start:end])
    return blocks


def _parse_block(block: list[str]) -> _FileChange:
    change = _FileChange()
    for line in block:
        if line.startswith("old mode ") or line.startswith("new mode "):
            change.is_mode_change = True
            _mode_marks(line, change)
        elif line.startswith("new file mode "):
            _mode_marks(line, change)
        elif line.startswith("deleted file mode "):
            _mode_marks(line, change)
        elif line.startswith("index "):
            _mode_marks(line, change)
        elif line.startswith("rename from "):
            change.is_rename = True
            change.old_path = line[len("rename from ") :]
        elif line.startswith("rename to "):
            change.is_rename = True
            change.new_path = line[len("rename to ") :]
        elif line.startswith("copy from "):
            change.is_rename = True
            change.old_path = line[len("copy from ") :]
        elif line.startswith("copy to "):
            change.is_rename = True
            change.new_path = line[len("copy to ") :]
        elif line.startswith("--- "):
            value = line[4:]
            if value != "/dev/null" and change.old_path is None:
                change.old_path = value[2:] if value.startswith("a/") else value
        elif line.startswith("+++ "):
            value = line[4:]
            if value != "/dev/null" and change.new_path is None:
                change.new_path = value[2:] if value.startswith("b/") else value
        elif line.startswith("Binary files ") and line.rstrip().endswith("differ"):
            change.is_binary = True
        elif line.strip() == "GIT binary patch":
            change.is_binary = True
        elif line.startswith("+Subproject commit ") or line.startswith("-Subproject commit "):
            change.is_submodule = True
    if change.old_path is None and change.new_path is None and not change.is_rename:
        # A pure mode change or a binary diff carries no `---`/`+++` lines at
        # all (git omits them for both); the header is the only place left
        # to find the path. Only the symmetric a/<path> b/<path> case is
        # trusted here -- an asymmetric header without rename/copy lines is
        # already unusual enough to refuse rather than guess.
        header = _HEADER_RE.match(block[0]) if block else None
        if header is not None:
            change.old_path = change.new_path = header.group("path")
    return change


def validate_diff(unified_diff: str, *, policy: RepositoryEffectPolicy) -> None:
    """Raise `ToolFailure` on any refusal in section 7's list. Returns
    normally for a diff-shaped intent this policy allows."""

    blocks = _split_diff_blocks(unified_diff)
    if not blocks:
        raise ToolFailure(
            "diff-not-supported",
            "the payload does not contain a unified diff (no 'diff --git' header)",
        )
    for block in blocks:
        change = _parse_block(block)
        touched = tuple(p for p in (change.old_path, change.new_path) if p is not None)
        if not touched:
            raise ToolFailure("diff-not-supported", "a diff section names no path")
        for path in touched:
            if not _is_path_safe(path):
                raise ToolFailure(
                    "path-outside-repository",
                    f"path {path!r} is outside the repository",
                )
        if change.is_binary:
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

_TERMINAL_STATES = frozenset({"proposed", "accepted", "rejected", "applied", "failed"})


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

    def __post_init__(self) -> None:
        if self.state not in _TERMINAL_STATES:
            raise ValueError(f"unknown effect intent state {self.state!r}")


class IntentStore(Protocol):
    """Where proposed effect intents live. Durable implementations live with
    the lifecycle owner (ActionQ), not in the edge -- see the module
    docstring. Shares the idempotency ledger's method shapes (section 5) so
    one object plays both roles for its owner."""

    async def lookup(self, workspace_id: str, tool: str, key: str) -> StoredResult | None: ...

    async def store(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult
    ) -> StoredResult: ...

    async def create(self, intent: EffectIntent) -> None:
        """Persist a newly proposed intent. `intent.intent_id` is unique."""

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

    async def store(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult
    ) -> StoredResult:
        raise ToolFailure("effects-unavailable", "the effect intent store is not available yet")

    async def create(self, intent: EffectIntent) -> None:
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

    async def store(
        self, workspace_id: str, tool: str, key: str, stored: StoredResult
    ) -> StoredResult:
        return await self._ledger.store(workspace_id, tool, key, stored)

    async def create(self, intent: EffectIntent) -> None:
        self._intents[intent.intent_id] = intent

    async def get(self, intent_id: str, caller: RunBinding) -> EffectIntent:
        intent = self._intents.get(intent_id) if INTENT_ID.fullmatch(intent_id or "") else None
        if intent is None or intent.binding != caller:
            raise ToolFailure("effect-not-found", _NOT_YOURS)
        return intent

    def set_state(self, intent_id: str, state: str) -> None:
        """Test-only: simulate the reconciler reporting a settlement back.

        A real store's transition path is out of the edge's reach entirely
        (section 7: "no code path from the edge to any executor"); this
        exists so `get_effect` tests can prove every reported state without
        one."""

        self._intents[intent_id] = replace(self._intents[intent_id], state=state)


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
        "'proposed'}. A trusted reconciler, not this tool and not this "
        "caller, later applies the diff, signs the commit and opens a PR; "
        "call get_effect with intent_id to see the outcome. Refused "
        "before any of that: binary patches, file-mode changes, symlinks, "
        "submodules, paths outside the repository, renames or deletes "
        "outside the repository's path allowlist, CI workflow paths and "
        "protected (e.g. SOPS-matched) paths unless the allowlist names "
        "them, and any payload that is not a unified diff. run_id must "
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
        "rejected, applied or failed. Read-only: it does not change the "
        "intent. An unknown intent_id, or one bound to a different caller, "
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
    rationale = _require_str(arguments, "rationale")
    if len(rationale) > _RATIONALE_MAX:
        raise ToolFailure("invalid-arguments", f"rationale must be at most {_RATIONALE_MAX} characters")
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
        await intent_store.create(intent)
        result = {"intent_id": intent.intent_id, "state": intent.state}
        winner = await intent_store.store(
            binding.workspace_id, "propose_effect", parsed["idempotency_key"], StoredResult(digest, result)
        )
        return dict(winner.result)

    async def get_effect(parsed: dict[str, Any], forwarded: "ForwardedIdentity") -> dict[str, Any]:
        binding = binding_for(forwarded)
        intent = await intent_store.get(parsed["intent_id"], binding)
        return {"intent_id": intent.intent_id, "state": intent.state}

    return ToolSet(
        name="effect",
        tools=(
            ToolSpec("propose_effect", "propose", _PROPOSE_DEFINITION, _parse_propose, propose_effect),
            ToolSpec("get_effect", "propose", _GET_DEFINITION, _parse_get, get_effect),
        ),
    )


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    return _build(
        intent_store=UnavailableIntentStore(),
        runs=context.runs,
        repository_policies=_load_repository_policies(context.env),
    )
