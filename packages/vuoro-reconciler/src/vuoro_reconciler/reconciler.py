"""Orchestration: poll -> checkout -> apply -> sign -> push -> open PR -> report.

Every step that could touch a real repository is behind `IntentSource` or
`ProviderClient`; this module holds no credential and makes no network call
of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import shutil
import tempfile

from .git_ops import (
    CheckoutFailed,
    DiffDoesNotApply,
    checkout_at,
    commit_signed,
    refuse_if_protected,
    try_apply_diff,
)
from .intents import EffectIntent, IntentSource
from .provider import ProviderClient, PullRequest
from .signing import SigningKey

__all__ = [
    "DiffDoesNotApply",
    "Outcome",
    "Reconciler",
    "ReconcilerConfig",
    "RepositoryNotAllowlisted",
]


class RepositoryNotAllowlisted(Exception):
    """The intent names a repository this reconciler will not touch."""


@dataclass(frozen=True)
class ReconcilerConfig:
    """What this reconciler is allowed to touch.

    `repository_allowlist` is checked before anything else -- before even
    a clone is attempted -- so an intent for an unlisted repository never
    reaches git or the provider at all. `protected_branches` is the second,
    independent guard against ever pushing a default/protected branch (the
    first is that `ProviderClient` has no merge or protected-push method to
    begin with).
    """

    repository_allowlist: frozenset[str]
    protected_branches: frozenset[str] = frozenset({"main", "master"})
    branch_prefix: str = "vuoro-effect"

    def branch_for(self, intent: EffectIntent) -> str:
        return f"{self.branch_prefix}/{intent.intent_id}"


@dataclass(frozen=True)
class Outcome:
    intent_id: str
    state: str  # "applied" | "failed"
    commit_sha: str | None = None
    pr_url: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Reconciler:
    intent_source: IntentSource
    provider: ProviderClient
    signing_key: SigningKey
    config: ReconcilerConfig
    _workdir_root: str | None = field(default=None, repr=False)

    async def run_once(self) -> list[Outcome]:
        """Poll once and reconcile every accepted intent. Returns one
        `Outcome` per intent, in poll order; never raises for an individual
        intent's failure (that is reported and recorded, not propagated)."""

        intents = await self.intent_source.poll_accepted()
        return [await self._process(intent) for intent in intents]

    async def _process(self, intent: EffectIntent) -> Outcome:
        if intent.repository not in self.config.repository_allowlist:
            return await self._fail(intent, "repository-not-allowlisted")

        branch = self.config.branch_for(intent)
        try:
            refuse_if_protected(branch, self.config.protected_branches)
        except ValueError:  # pragma: no cover - branch_for never produces one
            return await self._fail(intent, "branch-is-protected")

        workdir = tempfile.mkdtemp(prefix="vuoro-reconciler-", dir=self._workdir_root)
        try:
            try:
                checkout_at(self.provider.clone_url(intent.repository), intent.base_commit, workdir)
            except CheckoutFailed as error:
                return await self._fail(intent, f"checkout-failed: {error}")

            try:
                try_apply_diff(workdir, intent.unified_diff)
            except DiffDoesNotApply as error:
                return await self._fail(intent, f"diff-does-not-apply: {error}")

            commit_sha = commit_signed(
                workdir,
                title=intent.title,
                rationale=intent.rationale,
                run_id=intent.run_id,
                intent_id=intent.intent_id,
                key=self.signing_key,
            )
            await self.provider.push_branch(intent.repository, branch, local_path=workdir)
            result = await self.provider.open_pull_request(
                PullRequest(
                    repository=intent.repository,
                    branch=branch,
                    base_branch=self.provider.default_branch(intent.repository),
                    title=intent.title,
                    body=intent.rationale,
                )
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        await self.intent_source.report_applied(
            intent.intent_id, commit_sha=commit_sha, pr_url=result.url
        )
        return Outcome(intent.intent_id, "applied", commit_sha=commit_sha, pr_url=result.url)

    async def _fail(self, intent: EffectIntent, reason: str) -> Outcome:
        await self.intent_source.report_failed(intent.intent_id, reason=reason)
        return Outcome(intent.intent_id, "failed", reason=reason)
