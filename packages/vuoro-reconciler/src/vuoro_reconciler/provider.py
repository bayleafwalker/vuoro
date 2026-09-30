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
        does not exist. With `find_open_pull_request`, this is the recovery
        key after a crash: `vuoro-effect/<intent_id>` is deterministic, so a
        restarted reconciler finds what an interrupted run left behind
        without any ledger of its own."""

    async def find_open_pull_request(
        self, repository: str, branch: str
    ) -> PullRequestResult | None:
        """The open PR from `branch`, or `None` if there is none."""

    async def push_branch(self, repository: str, branch: str, *, local_path: str) -> None:
        """Create `branch` at `local_path`'s current HEAD.

        Create-only: if `branch` already exists, raise `BranchAlreadyExists`
        and push nothing (never a fast-forward, never a force). Of two
        concurrent consumers exactly one creates the branch.

        Implementations must refuse a `branch` equal to `default_branch` or
        any other protected ref; the reconciler never asks for one, but a
        provider client does not trust that either.
        """

    async def open_pull_request(self, request: PullRequest) -> PullRequestResult:
        """Open a PR from `request.branch` into `request.base_branch`.

        The reconciler looks for an open PR first (`find_open_pull_request`);
        if one appeared in between, raise `PullRequestAlreadyExists` (or
        return the existing one) rather than opening a second."""
