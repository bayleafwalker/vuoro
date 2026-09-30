"""`ProviderClient`: the only way this package reaches a forge.

Deliberately narrow: there is no method here that merges anything, pushes a
protected or default branch, or fetches anything outside `repository`. A
real implementation (Forgejo, GitHub) restricts itself to a repository
allowlist on top of this; the reconciler itself also enforces the
allowlist before ever constructing a `ProviderClient` call (see
`reconciler.ReconcilerConfig.repository_allowlist`), so both layers must
agree before an intent reaches a real forge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BranchAlreadyExists(Exception):
    """`push_branch` found the branch already there: another consumer (or an
    earlier, interrupted run) created it first. Nothing was pushed."""


class PullRequestAlreadyExists(Exception):
    """`open_pull_request` found an open PR from the branch already (a forge
    refuses a second one, e.g. GitHub's 422). Nothing was opened."""


class ProviderCredentialRejected(Exception):
    """The forge refused the reconciler's credential (revoked, expired or
    lacking scope). Raised by any `ProviderClient` call, so the reconciler
    can record one stable reason instead of a step-specific one."""


@dataclass(frozen=True)
class PullRequest:
    repository: str
    branch: str
    base_branch: str
    title: str
    body: str


@dataclass(frozen=True)
class PullRequestResult:
    url: str
    number: int | None = None
    #: The PR's base and head as the forge reports them. The reconciler
    #: reuses a found PR only when its base is the default branch and its
    #: head is the verified commit on `vuoro-effect/<intent_id>`.
    base_branch: str | None = None
    head_sha: str | None = None
    #: "open" | "merged" | "closed" (closed without merging).
    state: str = "open"
    #: A full ref, fetchable from `clone_url`, that still reaches `head_sha`
    #: after the branch is gone (GitHub `refs/pull/<n>/head`); used to verify
    #: a squash- or rebase-merged PR whose branch was deleted.
    head_ref: str | None = None


class ProviderClient(Protocol):
    """One repository's forge access, scoped to what reconciliation needs.

    No method here can merge, delete a ref, or touch a branch other than
    the one just pushed: that is the structural half of "never merges,
    never pushes a protected branch" (the other half is
    `Reconciler`/`ReconcilerConfig.protected_branches`, checked before this
    is ever called).
    """

    def clone_url(self, repository: str) -> str:
        """Where to clone `repository` from (a local path or remote URL)."""

    def default_branch(self, repository: str) -> str:
        """`repository`'s protected default branch; a pull request's base."""

    async def find_branch(self, repository: str, branch: str) -> str | None:
        """The commit `branch` points at on the forge now, or `None` if it
        does not exist. With `find_pull_request`, this is the recovery key
        after a crash: `vuoro-effect/<intent_id>` is deterministic, so a
        restarted reconciler finds what an interrupted run left behind
        without any ledger of its own."""

    async def find_pull_request(self, repository: str, branch: str) -> PullRequestResult | None:
        """The most recent PR from `branch` in any state (open, merged or
        closed), or `None` if there has never been one. A merged PR is found
        even after its branch was deleted.

        Only PRs whose head repository is `repository` itself: a fork's PR
        from a branch of the same name is never returned (the reconciler
        would otherwise mistake it for its own)."""

    async def push_branch(self, repository: str, branch: str, *, local_path: str) -> None:
        """Create `branch` at `local_path`'s current HEAD.

        Must be atomic and create-only: if `branch` already exists, raise
        `BranchAlreadyExists` and push nothing (never a fast-forward, never
        a force). Of two concurrent consumers exactly one creates the
        branch. A plain `git push` does NOT give this (it fast-forwards an
        existing branch); use a ref-create call that refuses an existing
        ref (e.g. GitHub `POST /repos/{owner}/{repo}/git/refs`, which
        answers 422) or `git push --force-with-lease=refs/heads/<branch>:`
        (empty expected value: the ref must not exist).

        Implementations must refuse a `branch` equal to `default_branch` or
        any other protected ref; the reconciler never asks for one, but a
        provider client does not trust that either.
        """

    async def open_pull_request(self, request: PullRequest) -> PullRequestResult:
        """Open a PR from `request.branch` into `request.base_branch`.

        The reconciler looks for a PR first (`find_pull_request`); if an
        open one appeared in between, raise `PullRequestAlreadyExists`
        rather than opening a second."""
