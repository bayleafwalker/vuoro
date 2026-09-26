"""The intent this package consumes, and where it comes from.

Deliberately independent of `vuoro_mcp_edge.effect_tools.EffectIntent`: this
package is location-independent (it may not even ship from the same repo
checkout as the edge in a later placement) and takes its intents through
`IntentSource`, never by importing the edge's internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EffectIntent:
    """A diff-shaped intent accepted for reconciliation.

    Mirrors the shape `propose_effect` records (shared contract section 7),
    but is this package's own type: an `IntentSource` implementation is
    responsible for translating whatever its backend stores into this.
    """

    intent_id: str
    run_id: str
    repository: str
    base_commit: str
    title: str
    rationale: str
    unified_diff: str


class IntentSource(Protocol):
    """Where accepted intents come from, and where their outcome goes."""

    async def poll_accepted(self) -> list[EffectIntent]:
        """Intents ready to reconcile. An empty list means none are ready --
        never an error; a source that cannot answer raises instead."""

    async def report_applied(self, intent_id: str, *, commit_sha: str, pr_url: str) -> None:
        """The intent was applied: `commit_sha` is the signed commit, `pr_url`
        the pull request opened for it."""

    async def report_failed(self, intent_id: str, *, reason: str) -> None:
        """The intent could not be reconciled. `reason` is a stable,
        machine-readable code (e.g. `diff-does-not-apply`,
        `repository-not-allowlisted`), never upstream/log detail."""
