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
import re
import shutil
import tempfile

from .acceptance import AutoAcceptConfig, apply_auto_accept, scope_admits
from .diff_policy import (
    DiffPolicy,
    DiffPolicyViolation,
    check_changes,
    check_patch_is_text,
    check_patch_text,
    check_staged_content,
    is_git_control_path,
    is_unsupported_path,
    patch_paths,
    staged_changes,
)
from .git_ops import (
    CheckoutFailed,
    DiffDoesNotApply,
    checkout_at,
    commit_signed,
    ensure_commit,
    refuse_if_protected,
    same_change,
    stage_all,
    try_apply_diff,
)
from .intents import Acceptor, EffectIntent, IntentSource, OperatorAcceptor, PolicyAcceptor
from .provider import (
    BranchAlreadyExists,
    ProviderClient,
    ProviderCredentialRejected,
    PullRequest,
    PullRequestAlreadyExists,
    PullRequestResult,
)
from .signing import SigningKey, verify_commit

__all__ = [
    "DiffDoesNotApply",
    "Outcome",
    "Reconciler",
    "ReconcilerConfig",
    "RepositoryNotAllowlisted",
]


_log = logging.getLogger(__name__)

#: An intent id becomes part of a branch name and a push refspec, so only
#: plain ref-safe characters: no '.', ':', '/', '~', '^', '-' prefix, ...
_INTENT_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,127}")


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
    #: Consecutive `provider-lookup-failed` deferrals of one intent (counted
    #: per `Reconciler` instance, so a restart resets it) after which the
    #: intent is recorded `failed: provider-lookup-failed`. Callers should
    #: still alert on repeated `deferred` outcomes across restarts.
    max_lookup_deferrals: int = 5

    def branch_for(self, intent: EffectIntent) -> str:
        return f"{self.branch_prefix}/{intent.intent_id}"

    def diff_policy_for(self, repository: str) -> DiffPolicy:
        return self.diff_policies.get(repository, _EMPTY_DIFF_POLICY)


@dataclass(frozen=True)
class Outcome:
    intent_id: str
    #: "applied" | "failed" | "duplicate" | "deferred". "duplicate" means
    #: another consumer created the intent's branch or PR first; nothing was
    #: recorded, and that consumer (or, if it dies, the next run) finishes
    #: the intent. "deferred" (reason `provider-lookup-failed`) means a
    #: forge lookup failed transiently; nothing was recorded, so the intent
    #: stays accepted and the next poll retries it.
    state: str
    commit_sha: str | None = None
    pr_url: str | None = None
    reason: str | None = None
    acceptor: Acceptor | None = None


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Unrecorded(Exception):
    """End this intent's attempt without writing to `IntentSource`."""

    def __init__(self, state: str, reason: str, cause: str = "-") -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason
        #: The underlying exception's type name only (never its message).
        self.cause = cause


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
    _deferrals: dict[str, int] = field(default_factory=dict, repr=False, compare=False)

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
            except _Unrecorded as unrecorded:
                outcomes.append(await self._unrecorded(intent, unrecorded))
            except Exception as error:
                # One intent's unexpected failure never stops the others.
                outcomes.append(await self._fail(intent, f"internal-error: {type(error).__name__}"))
            if outcomes[-1].state != "deferred":
                self._deferrals.pop(intent.intent_id, None)
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
        if not _INTENT_ID.fullmatch(intent.intent_id or ""):
            raise _Refused("invalid-intent-id")
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
            try:
                result = await self._publish(intent, branch, default_branch, commit_sha, workdir)
            except ProviderCredentialRejected:
                raise _Refused("provider-credential-rejected") from None
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        commit_sha, pr = result

        try:
            await self.intent_source.report_applied(
                intent.intent_id, commit_sha=commit_sha, pr_url=pr.url, acceptor=acceptor
            )
        except Exception:
            # The branch and PR exist; a re-run finds them and reports again.
            _log.exception("report_applied failed for intent %s", intent.intent_id)
        return Outcome(intent.intent_id, "applied", commit_sha=commit_sha, pr_url=pr.url, acceptor=acceptor)

    async def _publish(
        self, intent: EffectIntent, branch: str, default_branch: str, commit_sha: str, workdir: str
    ) -> tuple[str, PullRequestResult]:
        """Push the branch and open its PR, resuming whatever an interrupted
        run left behind. The forge's branch and PR are the recovery key, but
        only once verified as this intent's own: a branch is reused only if
        its tip is signed by the reconciler's key and records the same
        change (tree, parents, message and trailers); a PR only if its base
        is the default branch and its head is that commit. A merged PR is
        success; a PR closed unmerged is a failure, never a reason to open
        another. Raises `_Unrecorded("duplicate", ...)` when another
        consumer created the branch or PR during this run."""

        repository = intent.repository
        existing = await self._lookup(self.provider.find_branch(repository, branch))
        pr = await self._lookup(self.provider.find_pull_request(repository, branch))

        if pr is not None and pr.state == "closed":
            # Rejected by a human: never re-open, whatever the branch holds.
            raise _Refused("pull-request-closed")
        if existing is not None:
            # An earlier run pushed this branch (crash after push, lost report).
            # If a PR from it was squash- or rebase-merged while the branch
            # was kept, this branch commit (not the squash commit) is what is
            # recorded as applied.
            self._require_own_commit(workdir, f"refs/heads/{branch}", existing, commit_sha)
            commit_sha = existing
        elif pr is not None and pr.state == "merged" and pr.head_sha:
            # Merged, then the branch was deleted. After a squash or rebase
            # merge the head is not on the default branch; the PR head ref
            # still reaches it.
            head_ref = pr.head_ref or f"refs/heads/{default_branch}"
            self._require_own_commit(workdir, head_ref, pr.head_sha, commit_sha)
            commit_sha = pr.head_sha

        if pr is not None:
            if pr.state not in ("open", "merged"):
                raise _Refused("pull-request-mismatch")
            if pr.base_branch != default_branch or pr.head_sha != commit_sha:
                raise _Refused("pull-request-mismatch")
            return commit_sha, pr

        if existing is None:
            try:
                await self.provider.push_branch(repository, branch, local_path=workdir)
            except BranchAlreadyExists:
                raise _Unrecorded("duplicate", "concurrent-consumer") from None
            except ProviderCredentialRejected:
                raise
            except Exception:
                raise _Refused("push-failed") from None
            # Someone may have opened a PR from our branch since we pushed:
            # they report it, not us.
            if await self._lookup(self.provider.find_pull_request(repository, branch)) is not None:
                raise _Unrecorded("duplicate", "concurrent-consumer")

        request = PullRequest(
            repository=repository,
            branch=branch,
            base_branch=default_branch,
            title=intent.title,
            body=intent.rationale,
        )
        try:
            opened = await self.provider.open_pull_request(request)
        except PullRequestAlreadyExists:
            # Another consumer opened it between our lookup and our open.
            raise _Unrecorded("duplicate", "concurrent-consumer") from None
        except ProviderCredentialRejected:
            raise
        except Exception:
            raise _Refused("pull-request-failed") from None
        return commit_sha, opened

    async def _lookup(self, call):
        """Await a forge lookup; a non-credential failure defers the intent
        (unrecorded, retried on the next poll) instead of failing it."""

        try:
            return await call
        except ProviderCredentialRejected:
            raise
        except Exception as error:
            raise _Unrecorded("deferred", "provider-lookup-failed", type(error).__name__) from None

    def _require_own_commit(self, workdir: str, ref: str, sha: str, candidate: str) -> None:
        """`sha` must be the reconciler's own signed commit of this very
        change; anything else (unsigned, foreign-signed, another intent's
        trailers) is never adopted as this intent's result."""

        if not (
            ensure_commit(workdir, sha, ref=ref)
            and verify_commit(workdir, sha, self.signing_key)
            and same_change(workdir, sha, candidate)
        ):
            raise _Refused("branch-exists-with-different-change")

    def _prepare_commit(self, intent: EffectIntent, acceptor: Acceptor, workdir: str) -> str:
        """Validate, checkout, apply, re-validate and sign; the commit exists
        only in `workdir` until something pushes it."""

        # Before anything is applied: no NUL byte, no binary hunk (by line
        # and by git's own parse -- git apply cannot be told to refuse
        # binary itself), and no path git would read as configuration (a
        # .gitattributes could re-label binary content or name a filter
        # driver for the `git add` below).
        try:
            check_patch_text(intent.unified_diff)
            check_patch_is_text(intent.unified_diff)
            for path in patch_paths(intent.unified_diff):
                if is_unsupported_path(path):
                    raise DiffPolicyViolation("unsupported-path", path)
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
            check_staged_content(workdir, changes)
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

    async def _unrecorded(self, intent: EffectIntent, unrecorded: _Unrecorded) -> Outcome:
        if unrecorded.state == "deferred":
            count = self._deferrals.get(intent.intent_id, 0) + 1
            self._deferrals[intent.intent_id] = count
            _log.warning(
                "deferring intent %s (%s, %s, attempt %d/%d)",
                intent.intent_id,
                unrecorded.reason,
                unrecorded.cause,
                count,
                self.config.max_lookup_deferrals,
            )
            if count >= self.config.max_lookup_deferrals:
                self._deferrals.pop(intent.intent_id, None)
                return await self._fail(intent, unrecorded.reason)
        return Outcome(intent.intent_id, unrecorded.state, reason=unrecorded.reason, acceptor=intent.acceptor)

    async def _fail(self, intent: EffectIntent, reason: str) -> Outcome:
        try:
            await self.intent_source.report_failed(intent.intent_id, reason=reason)
        except Exception:
            _log.exception("report_failed failed for intent %s (%s)", intent.intent_id, reason)
        return Outcome(intent.intent_id, "failed", reason=reason, acceptor=intent.acceptor)
