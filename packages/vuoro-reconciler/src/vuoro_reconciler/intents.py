"""The intent this package consumes, who accepted it, and where it comes from.

Deliberately independent of `vuoro_mcp_edge.effect_tools.EffectIntent`: this
package is location-independent (it may not even ship from the same repo
checkout as the edge in a later placement) and takes its intents through
`IntentSource`, never by importing the edge's internals.

Lifecycle (TS-16, unamended): a cloud caller only ever *proposes*. The
`proposed -> accepted` transition belongs to a separately authenticated
trusted-side actor -- an operator at the `vuoro-reconciler accept` CLI, or
an opt-in auto-accept policy loaded from a trusted-side config file (see
`acceptance.py`) -- and never to the proposing principal. Only an
`accepted` intent that carries its `Acceptor` is ever reconciled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

__all__ = [
    "Acceptor",
    "EffectIntent",
    "IntentSource",
    "OperatorAcceptor",
    "PolicyAcceptor",
]


@dataclass(frozen=True)
class OperatorAcceptor:
    """An operator accepted (or rejected) the intent interactively.

    `subject` is the operator's authenticated trusted-side identity, in the
    same principal namespace as `EffectIntent.proposer_principal` (so the
    "never the proposer" comparison is meaningful)."""

    subject: str
    kind: Literal["operator"] = "operator"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "subject": self.subject}

    def trailer_value(self) -> str:
        return f"operator:{self.subject}"


@dataclass(frozen=True)
class PolicyAcceptor:
    """An auto-accept policy accepted the intent.

    `scope` is the matching policy's own scope (workspace, repository,
    effect kinds, path globs) as it stood when it matched; `config_digest`
    is the sha256 of the config file it was loaded from, so the acceptance
    is traceable to one exact trusted-side config revision."""

    policy_id: str
    version: int
    scope: Mapping[str, Any]
    config_digest: str
    kind: Literal["policy"] = "policy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "policy_id": self.policy_id,
            "version": self.version,
            "scope": dict(self.scope),
            "config_digest": self.config_digest,
        }

    def trailer_value(self) -> str:
        return f"policy:{self.policy_id}@{self.version}"


Acceptor = OperatorAcceptor | PolicyAcceptor


@dataclass(frozen=True)
class EffectIntent:
    """A diff-shaped intent, as an `IntentSource` reports it.

    Mirrors the shape `propose_effect` records (shared contract section 7),
    but is this package's own type: an `IntentSource` implementation is
    responsible for translating whatever its backend stores into this.

    `acceptor` is `None` for a `proposed` intent and must be set for an
    `accepted` one; the reconciler refuses an intent without it.
    """

    intent_id: str
    run_id: str
    repository: str
    base_commit: str
    title: str
    rationale: str
    unified_diff: str
    workspace_id: str
    proposer_principal: str
    effect_kind: str = "diff"
    acceptor: Acceptor | None = field(default=None)


class IntentSource(Protocol):
    """Where intents come from, how they are accepted, and where their
    outcome goes. Implementations live on the trusted side only; nothing on
    the public MCP surface holds one."""

    async def poll_proposed(self) -> list[EffectIntent]:
        """Intents still awaiting acceptance (state `proposed`)."""

    async def poll_accepted(self) -> list[EffectIntent]:
        """Intents ready to reconcile. An empty list means none are ready --
        never an error; a source that cannot answer raises instead."""

    async def accept(self, intent_id: str, acceptor: Acceptor) -> None:
        """`proposed -> accepted`, recording `acceptor`. Compare-and-set: an
        intent no longer in `proposed` is not transitioned (raise)."""

    async def reject(self, intent_id: str, acceptor: Acceptor, reason: str) -> None:
        """`proposed -> rejected`, recording who rejected it and why."""

    async def report_applied(
        self, intent_id: str, *, commit_sha: str, pr_url: str, acceptor: Acceptor
    ) -> None:
        """The intent was applied: `commit_sha` is the signed commit, `pr_url`
        the pull request opened for it, `acceptor` who authorised it."""

    async def report_failed(self, intent_id: str, *, reason: str) -> None:
        """The intent could not be reconciled. `reason` is a stable,
        machine-readable code (e.g. `diff-does-not-apply`,
        `repository-not-allowlisted`), never upstream/log detail."""
