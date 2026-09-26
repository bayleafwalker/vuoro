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

    async def push_branch(self, repository: str, branch: str, *, local_path: str) -> None:
        """Push `local_path`'s current HEAD to a new branch named `branch`.

        Implementations must refuse a `branch` equal to `default_branch` or
        any other protected ref; the reconciler never asks for one, but a
        provider client does not trust that either.
        """

    async def open_pull_request(self, request: PullRequest) -> PullRequestResult:
        """Open a PR from `request.branch` into `request.base_branch`.

        Idempotent per branch: if a PR from `request.branch` is already
        open, return it rather than opening a second one (a re-run after a
        lost `report_applied` asks again)."""
