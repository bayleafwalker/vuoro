"""`proposed -> accepted`: the trusted-side decision to act on an intent.

TS-16 stays unamended: a cloud caller never applies an effect, it only
queues one. Acceptance therefore happens here, on the trusted side, by one
of two separately authenticated actors -- never the proposing principal and
never through any edge/MCP tool:

- **An operator**, interactively (`vuoro-reconciler accept <intent_id>`, see
  `cli.py`), recorded as `OperatorAcceptor(subject)`. The default.
- **An opt-in auto-accept policy**, recorded as
  `PolicyAcceptor(policy_id, version, scope, config_digest)`. Off by
  default. `AutoAcceptConfig` is loaded only from a file path passed
  explicitly to its constructor -- never from the environment and never
  from anything a cloud caller can reach -- and the reconciler evaluates it
  asynchronously, in `Reconciler.run_once`, not inside `propose_effect`.

Config file shape (JSON)::

    {"version": 3,
     "policies": [{"id": "docs-only", "enabled": true,
                   "workspace_id": "01K...", "repository": "repo-a",
                   "effect_kinds": ["diff"], "path_globs": ["docs/*"]}]}

A missing file, an empty file, or no enabled policy means auto-accept is
off. `workspace_id`, a non-empty `effect_kinds`, and at least one of
`repository` or `path_globs` are required, so a policy can never match
"everything in a workspace"; a policy without them is a config error, and
the whole file then fails to load (closed).

Before acting on a `PolicyAcceptor`, the reconciler checks it against the
config as it is *now* (`AutoAcceptConfig.stale_reason`): the policy must
still exist and be enabled, and the recorded version, config digest and
scope must match. An acceptance recorded under an older or different
config is refused, not honoured.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import fnmatch
import hashlib
import json
import logging
import os

from .diff_policy import DiffPolicyViolation, patch_paths
from .intents import EffectIntent, IntentSource, OperatorAcceptor, PolicyAcceptor

_log = logging.getLogger(__name__)

__all__ = [
    "AcceptanceRefused",
    "AutoAcceptConfig",
    "AutoAcceptPolicy",
    "OperatorAcceptance",
    "apply_auto_accept",
    "scope_admits",
]


class AcceptanceRefused(Exception):
    """An acceptance or rejection was refused; nothing was transitioned."""


@dataclass(frozen=True)
class AutoAcceptPolicy:
    id: str
    enabled: bool
    workspace_id: str
    effect_kinds: frozenset[str]
    repository: str | None = None
    path_globs: tuple[str, ...] | None = None

    def scope(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "repository": self.repository,
            "effect_kinds": sorted(self.effect_kinds),
            "path_globs": list(self.path_globs) if self.path_globs is not None else None,
        }

    def matches(self, intent: EffectIntent) -> bool:
        if not self.enabled:
            return False
        if intent.workspace_id != self.workspace_id:
            return False
        if self.repository is not None and intent.repository != self.repository:
            return False
        if intent.effect_kind not in self.effect_kinds:
            return False
        if self.path_globs is not None:
            try:
                paths = patch_paths(intent.unified_diff)
            except DiffPolicyViolation:
                return False
            if not paths or not all(self.covers(path) for path in paths):
                return False
        return True

    def covers(self, path: str) -> bool:
        """True if `path` is inside this policy's `path_globs` (or it has none)."""

        if self.path_globs is None:
            return True
        return any(fnmatch.fnmatch(path, glob) for glob in self.path_globs)


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{where} must be a list of non-empty strings")
    return value


def _parse_policy(entry: Any, index: int) -> AutoAcceptPolicy:
    where = f"policies[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be an object")
    allowed = {"id", "enabled", "workspace_id", "repository", "effect_kinds", "path_globs"}
    unknown = set(entry) - allowed
    if unknown:
        raise ValueError(f"{where} has unknown keys {sorted(unknown)}")
    policy_id = entry.get("id")
    if not isinstance(policy_id, str) or not policy_id:
        raise ValueError(f"{where}.id must be a non-empty string")
    enabled = entry.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError(f"{where}.enabled must be true or false")
    workspace_id = entry.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError(f"{where}.workspace_id must be a non-empty string")
    repository = entry.get("repository")
    if repository is not None and (not isinstance(repository, str) or not repository):
        raise ValueError(f"{where}.repository must be a non-empty string when given")
    effect_kinds = _str_list(entry.get("effect_kinds"), f"{where}.effect_kinds")
    if not effect_kinds:
        raise ValueError(f"{where}.effect_kinds must name at least one kind")
    path_globs = entry.get("path_globs")
    if path_globs is not None:
        path_globs = tuple(_str_list(path_globs, f"{where}.path_globs"))
        if not path_globs:
            raise ValueError(f"{where}.path_globs must be omitted or non-empty")
    if repository is None and path_globs is None:
        raise ValueError(f"{where} must name a repository or path_globs (or both)")
    return AutoAcceptPolicy(
        id=policy_id,
        enabled=enabled,
        workspace_id=workspace_id,
        effect_kinds=frozenset(effect_kinds),
        repository=repository,
        path_globs=path_globs,
    )


class AutoAcceptConfig:
    """The trusted-side auto-accept policy set, read from `path` once.

    `path=None`, a missing file or an empty file all mean off. A present
    but malformed file raises `ValueError`: an operator's config mistake
    fails loudly rather than silently accepting (or silently not).
    """

    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self.path = Path(path) if path is not None else None
        self.version: int | None = None
        self.policies: tuple[AutoAcceptPolicy, ...] = ()
        self.digest: str | None = None
        if self.path is None or not self.path.exists():
            return
        raw = self.path.read_bytes()
        if not raw.strip():
            return
        self.digest = hashlib.sha256(raw).hexdigest()
        try:
            parsed = json.loads(raw)
        except ValueError as error:
            raise ValueError(f"{self.path}: auto-accept config is not valid JSON") from error
        if not isinstance(parsed, dict) or set(parsed) - {"version", "policies"}:
            raise ValueError(f"{self.path}: expected an object with version and policies only")
        version = parsed.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError(f"{self.path}: version must be a positive integer")
        entries = parsed.get("policies", [])
        if not isinstance(entries, list):
            raise ValueError(f"{self.path}: policies must be a list")
        policies = tuple(_parse_policy(entry, index) for index, entry in enumerate(entries))
        ids = [policy.id for policy in policies]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.path}: policy ids must be unique")
        self.version = version
        self.policies = policies

    @property
    def active(self) -> bool:
        return any(policy.enabled for policy in self.policies)

    def policy(self, policy_id: str) -> AutoAcceptPolicy | None:
        return next((policy for policy in self.policies if policy.id == policy_id), None)

    def stale_reason(self, acceptor: PolicyAcceptor) -> str | None:
        """Why `acceptor` no longer holds under this config, or `None`."""

        policy = self.policy(acceptor.policy_id)
        if policy is None:
            return "policy-not-found"
        if not policy.enabled:
            return "policy-disabled"
        if acceptor.version != self.version:
            return "policy-version-mismatch"
        if acceptor.config_digest != self.digest:
            return "policy-config-digest-mismatch"
        if dict(acceptor.scope) != policy.scope():
            return "policy-scope-mismatch"
        return None

    def evaluate(self, intent: EffectIntent) -> PolicyAcceptor | None:
        """The acceptor of the first enabled policy that matches `intent`,
        or `None` (leave it for an operator)."""

        if not self.active or self.version is None or self.digest is None:
            return None
        for policy in self.policies:
            if policy.matches(intent):
                return PolicyAcceptor(
                    policy_id=policy.id,
                    version=self.version,
                    scope=policy.scope(),
                    config_digest=self.digest,
                )
        return None


async def apply_auto_accept(
    intent_source: IntentSource, config: AutoAcceptConfig | None
) -> list[tuple[str, PolicyAcceptor]]:
    """Accept every proposed intent a policy matches. Returns what was
    accepted. With no config, or no enabled policy, does nothing at all --
    not even a poll.

    Never raises: a poll that fails, or one intent whose evaluation or
    `accept` fails (e.g. a lost compare-and-set race with an operator), is
    logged and skipped, and the rest of the cycle carries on."""

    if config is None or not config.active:
        return []
    try:
        proposed = await intent_source.poll_proposed()
    except Exception:
        _log.exception("auto-accept: poll_proposed failed; skipping auto-accept this cycle")
        return []
    accepted = []
    for intent in proposed:
        try:
            acceptor = config.evaluate(intent)
            if acceptor is None:
                continue
            await intent_source.accept(intent.intent_id, acceptor)
        except Exception:
            _log.exception("auto-accept: intent %s could not be accepted; skipped", intent.intent_id)
            continue
        accepted.append((intent.intent_id, acceptor))
    return accepted


@dataclass(frozen=True)
class OperatorAcceptance:
    """Interactive acceptance by an operator on the trusted side."""

    intent_source: IntentSource

    async def pending(self, intent_id: str) -> EffectIntent:
        """The proposed intent `intent_id`, or `AcceptanceRefused`."""

        for intent in await self.intent_source.poll_proposed():
            if intent.intent_id == intent_id:
                return intent
        raise AcceptanceRefused(f"no proposed intent {intent_id!r}")

    async def accept_interactive(self, intent_id: str, operator_subject: str) -> OperatorAcceptor:
        intent = await self.pending(intent_id)
        _require_subject(operator_subject)
        if operator_subject == intent.proposer_principal:
            raise AcceptanceRefused("the proposing principal cannot accept its own intent")
        acceptor = OperatorAcceptor(subject=operator_subject)
        await self.intent_source.accept(intent_id, acceptor)
        return acceptor

    async def reject_interactive(
        self, intent_id: str, operator_subject: str, reason: str
    ) -> OperatorAcceptor:
        await self.pending(intent_id)
        _require_subject(operator_subject)
        if not reason.strip():
            raise AcceptanceRefused("a rejection needs a reason")
        acceptor = OperatorAcceptor(subject=operator_subject)
        await self.intent_source.reject(intent_id, acceptor, reason)
        return acceptor


def _require_subject(subject: str) -> None:
    if not isinstance(subject, str) or not subject.strip():
        raise AcceptanceRefused("an operator subject is required")


def scope_admits(scope: Mapping[str, Any], intent: EffectIntent, paths: Sequence[str]) -> bool:
    """Re-check a recorded `PolicyAcceptor.scope` against an intent and the
    paths it actually touched (the reconciler does this after applying, so
    a policy never authorises more than its scope)."""

    if scope.get("workspace_id") != intent.workspace_id:
        return False
    repository = scope.get("repository")
    if repository is not None and repository != intent.repository:
        return False
    if intent.effect_kind not in (scope.get("effect_kinds") or ()):
        return False
    globs = scope.get("path_globs")
    if globs is not None:
        return all(any(fnmatch.fnmatch(path, glob) for glob in globs) for path in paths)
    return True
