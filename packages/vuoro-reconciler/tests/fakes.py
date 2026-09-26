"""Fake IntentSource and ProviderClient: local git repos, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vuoro_reconciler.git_ops import push_branch
from vuoro_reconciler.intents import EffectIntent
from vuoro_reconciler.provider import PullRequest, PullRequestResult


@dataclass
class FakeIntentSource:
    intents: list[EffectIntent]
    applied: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    async def poll_accepted(self) -> list[EffectIntent]:
        pending, self.intents = self.intents, []
        return pending

    async def report_applied(self, intent_id: str, *, commit_sha: str, pr_url: str) -> None:
        self.applied.append({"intent_id": intent_id, "commit_sha": commit_sha, "pr_url": pr_url})

    async def report_failed(self, intent_id: str, *, reason: str) -> None:
        self.failed.append({"intent_id": intent_id, "reason": reason})


@dataclass
class FakeProviderClient:
    """Backed by real local bare git repositories: `repositories` maps a
    repository id to the path of its bare remote."""

    repositories: dict[str, Path]
    protected_branches: frozenset[str] = frozenset({"main"})
    pull_requests: list[PullRequest] = field(default_factory=list)
    pushed_branches: list[tuple[str, str]] = field(default_factory=list)

    def clone_url(self, repository: str) -> str:
        return str(self.repositories[repository])

    def default_branch(self, repository: str) -> str:
        return "main"

    async def push_branch(self, repository: str, branch: str, *, local_path: str) -> None:
        push_branch(
            local_path,
            remote_url=str(self.repositories[repository]),
            branch=branch,
            protected_branches=self.protected_branches,
        )
        self.pushed_branches.append((repository, branch))

    async def open_pull_request(self, request: PullRequest) -> PullRequestResult:
        self.pull_requests.append(request)
        return PullRequestResult(
            url=f"file://{self.repositories[request.repository]}/pulls/{len(self.pull_requests)}",
            number=len(self.pull_requests),
        )
