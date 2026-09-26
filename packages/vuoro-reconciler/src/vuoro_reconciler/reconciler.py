"""Orchestration: auto-accept -> poll accepted -> checkout -> apply ->
re-validate -> sign -> push -> open PR -> report.

Every step that could touch a real repository is behind `IntentSource` or
`ProviderClient`; this module holds no credential and makes no network call
of its own.

Only an `accepted` intent that carries its `Acceptor` is ever reconciled
(TS-16: the cloud caller only proposes). Acceptance is the operator's CLI
or an opt-in `AutoAcceptConfig` passed to this reconciler explicitly; with
none, `run_once` never accepts anything itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import shutil
import tempfile

from .acceptance import AutoAcceptConfig, apply_auto_accept, scope_admits
from .diff_policy import (
    DiffPolicy,
    DiffPolicyViolation,
    check_changes,
    check_patch_text,
    is_git_control_path,
    patch_paths,
    staged_changes,
)
from .git_ops import (
    CheckoutFailed,
    DiffDoesNotApply,
    checkout_at,
    commit_signed,
    refuse_if_protected,
    remote_branch_tip,
    same_change,
    stage_all,
    try_apply_diff,
)
from .intents import Acceptor, EffectIntent, IntentSource, OperatorAcceptor, PolicyAcceptor
from .provider import ProviderClient, PullRequest
from .signing import SigningKey

__all__ = [
    "DiffDoesNotApply",
    "Outcome",
    "Reconciler",
    "ReconcilerConfig",
    "RepositoryNotAllowlisted",
]


_log = logging.getLogger(__name__)


class RepositoryNotAllowlisted(Exception):
    """The intent names a repository this reconciler will not touch."""


_EMPTY_DIFF_POLICY = DiffPolicy()


@dataclass(frozen=True)
class ReconcilerConfig:
    """What this reconciler is allowed to touch.

    `repository_allowlist` is checked before anything else -- before even
    a clone is attempted -- so an intent for an unlisted repository never
    reaches git or the provider at all. `protected_branches` (plus each
    repository's default branch, always) is the second, independent guard
    against ever pushing a default/protected branch (the first is that
    `ProviderClient` has no merge or protected-push method to begin with).

    `diff_policies` is the reconciler's own per-repository diff policy; an
    unlisted repository gets the fail-closed empty one. It is applied to
    the result of applying the diff, whatever the edge already checked.
    """

    repository_allowlist: frozenset[str]
    protected_branches: frozenset[str] = frozenset({"main", "master"})
    branch_prefix: str = "vuoro-effect"
    diff_policies: Mapping[str, DiffPolicy] = field(default_factory=dict)

    def branch_for(self, intent: EffectIntent) -> str:
        return f"{self.branch_prefix}/{intent.intent_id}"

    def diff_policy_for(self, repository: str) -> DiffPolicy:
        return self.diff_policies.get(repository, _EMPTY_DIFF_POLICY)


@dataclass(frozen=True)
class Outcome:
    intent_id: str
    state: str  # "applied" | "failed"
    commit_sha: str | None = None
    pr_url: str | None = None
    reason: str | None = None
    acceptor: Acceptor | None = None


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Reconciler:
    intent_source: IntentSource
    provider: ProviderClient
    signing_key: SigningKey
    config: ReconcilerConfig
    #: Opt-in auto-accept. `None` (the default) means off: `run_once` then
    #: only ever acts on intents an operator already accepted.
    auto_accept: AutoAcceptConfig | None = None
    _workdir_root: str | None = field(default=None, repr=False)

    async def run_once(self) -> list[Outcome]:
        """Apply auto-accept (if configured) to proposed intents, then poll
        once and reconcile every accepted intent. Returns one `Outcome` per
        accepted intent, in poll order; never raises for an individual
        intent's failure (that is reported and recorded, not propagated),
        nor for auto-accept or reporting failures (logged, and the cycle
        continues). Only a failing `poll_accepted` -- no work to do at
        all -- propagates."""

        await apply_auto_accept(self.intent_source, self.auto_accept)
        intents = await self.intent_source.poll_accepted()
        outcomes = []
        for intent in intents:
            try:
                outcomes.append(await self._process(intent))
            except _Refused as refused:
                outcomes.append(await self._fail(intent, refused.reason))
            except Exception as error:
                # One intent's unexpected failure never stops the others.
                outcomes.append(await self._fail(intent, f"internal-error: {type(error).__name__}"))
        return outcomes

    async def _process(self, intent: EffectIntent) -> Outcome:
        acceptor = intent.acceptor
        if acceptor is None:
            raise _Refused("no-acceptor")
        if isinstance(acceptor, OperatorAcceptor) and acceptor.subject == intent.proposer_principal:
            raise _Refused("acceptor-is-proposer")
        if isinstance(acceptor, PolicyAcceptor):
            # Honour a policy acceptance only under the config as it is now.
            if self.auto_accept is None:
                raise _Refused("policy-acceptor-stale: auto-accept-not-configured")
            stale = self.auto_accept.stale_reason(acceptor)
            if stale is not None:
                raise _Refused(f"policy-acceptor-stale: {stale}")
            if not scope_admits(acceptor.scope, intent, ()):
                raise _Refused("outside-policy-scope")
        if intent.effect_kind != "diff":
            raise _Refused("effect-kind-not-supported")
        if intent.repository not in self.config.repository_allowlist:
            raise _Refused("repository-not-allowlisted")

        branch = self.config.branch_for(intent)
        default_branch = self.provider.default_branch(intent.repository)
        try:
            refuse_if_protected(branch, self.config.protected_branches | {default_branch})
        except ValueError:
            raise _Refused("branch-is-protected") from None

        workdir = tempfile.mkdtemp(prefix="vuoro-reconciler-", dir=self._workdir_root)
        try:
            commit_sha = self._prepare_commit(intent, acceptor, workdir)
            existing = remote_branch_tip(workdir, branch)
            if existing is not None:
                # A re-run (e.g. report_applied was lost): the same change is
                # already on the branch, so it is not pushed again.
                if not same_change(workdir, existing, commit_sha):
                    raise _Refused("branch-exists-with-different-change")
                commit_sha = existing
            else:
                try:
                    await self.provider.push_branch(intent.repository, branch, local_path=workdir)
                except Exception:
                    raise _Refused("push-failed") from None
            try:
                result = await self.provider.open_pull_request(
                    PullRequest(
                        repository=intent.repository,
                        branch=branch,
                        base_branch=default_branch,
                        title=intent.title,
                        body=intent.rationale,
                    )
                )
            except Exception:
                raise _Refused("pull-request-failed") from None
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        try:
            await self.intent_source.report_applied(
                intent.intent_id, commit_sha=commit_sha, pr_url=result.url, acceptor=acceptor
            )
        except Exception:
            # The branch and PR exist; a re-run finds them and reports again.
            _log.exception("report_applied failed for intent %s", intent.intent_id)
        return Outcome(
            intent.intent_id, "applied", commit_sha=commit_sha, pr_url=result.url, acceptor=acceptor
        )

    def _prepare_commit(self, intent: EffectIntent, acceptor: Acceptor, workdir: str) -> str:
        """Validate, checkout, apply, re-validate and sign; the commit exists
        only in `workdir` until something pushes it."""

        # Before anything is applied: no NUL bytes, and no path git would
        # read as configuration (a .gitattributes could re-label binary
        # content or name a filter driver for the `git add` below).
        try:
            check_patch_text(intent.unified_diff)
            for path in patch_paths(intent.unified_diff):
                if is_git_control_path(path):
                    raise DiffPolicyViolation("git-control-file-refused", path)
        except DiffPolicyViolation as violation:
            raise _Refused(f"diff-policy-refused: {violation.code}") from None

        try:
            checkout_at(self.provider.clone_url(intent.repository), intent.base_commit, workdir)
        except CheckoutFailed as error:
            raise _Refused(f"checkout-failed: {error}") from None
        try:
            try_apply_diff(workdir, intent.unified_diff)
        except DiffDoesNotApply as error:
            raise _Refused(f"diff-does-not-apply: {error}") from None

        stage_all(workdir)
        try:
            changes = staged_changes(workdir)
            check_changes(changes, self.config.diff_policy_for(intent.repository))
        except DiffPolicyViolation as violation:
            raise _Refused(f"diff-policy-refused: {violation.code}") from None
        if isinstance(acceptor, PolicyAcceptor) and not scope_admits(
            acceptor.scope, intent, [change.path for change in changes]
        ):
            raise _Refused("outside-policy-scope")

        try:
            return commit_signed(
                workdir,
                title=intent.title,
                rationale=intent.rationale,
                run_id=intent.run_id,
                intent_id=intent.intent_id,
                acceptor=acceptor,
                key=self.signing_key,
            )
        except Exception as error:
            raise _Refused(f"commit-failed: {error}") from None

    async def _fail(self, intent: EffectIntent, reason: str) -> Outcome:
        try:
            await self.intent_source.report_failed(intent.intent_id, reason=reason)
        except Exception:
            _log.exception("report_failed failed for intent %s (%s)", intent.intent_id, reason)
        return Outcome(intent.intent_id, "failed", reason=reason, acceptor=intent.acceptor)
