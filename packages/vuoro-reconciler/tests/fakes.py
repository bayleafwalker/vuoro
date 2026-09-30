"""Fake IntentSource and ProviderClient: local git repos, no network."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
import subprocess
from typing import Any

from vuoro_reconciler.git_ops import push_branch
from vuoro_reconciler.intents import Acceptor, EffectIntent
from vuoro_reconciler.provider import (
    BranchAlreadyExists,
    ProviderCredentialRejected,
    PullRequest,
    PullRequestAlreadyExists,
    PullRequestResult,
)


@dataclass
class FakeIntentSource:
    """A trusted-side intent store: `proposed` intents await acceptance,
    `accepted` ones (each carrying its acceptor, or deliberately not) await
    reconciliation. Transitions are compare-and-set on the state, like a
    real one."""

    proposed: list[EffectIntent] = field(default_factory=list)
    accepted: list[EffectIntent] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    records: dict[str, EffectIntent] = field(default_factory=dict)
    applied: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    polls_proposed: int = 0

    def __post_init__(self) -> None:
        for intent in self.proposed:
            self.records[intent.intent_id] = intent
            self.states[intent.intent_id] = "proposed"
        for intent in self.accepted:
            self.records[intent.intent_id] = intent
            self.states[intent.intent_id] = "accepted"

    def _in(self, state: str) -> list[EffectIntent]:
        return [self.records[i] for i, s in self.states.items() if s == state]

    async def poll_proposed(self) -> list[EffectIntent]:
        self.polls_proposed += 1
        return self._in("proposed")

    async def poll_accepted(self) -> list[EffectIntent]:
        return self._in("accepted")

    async def accept(self, intent_id: str, acceptor: Acceptor) -> None:
        if self.states.get(intent_id) != "proposed":
            raise RuntimeError(f"{intent_id} is not proposed")
        self.records[intent_id] = replace(self.records[intent_id], acceptor=acceptor)
        self.states[intent_id] = "accepted"

    async def reject(self, intent_id: str, acceptor: Acceptor, reason: str) -> None:
        if self.states.get(intent_id) != "proposed":
            raise RuntimeError(f"{intent_id} is not proposed")
        self.states[intent_id] = "rejected"
        self.rejected.append({"intent_id": intent_id, "acceptor": acceptor, "reason": reason})

    async def report_applied(
        self, intent_id: str, *, commit_sha: str, pr_url: str, acceptor: Acceptor
    ) -> None:
        self.states[intent_id] = "applied"
        self.applied.append(
            {"intent_id": intent_id, "commit_sha": commit_sha, "pr_url": pr_url, "acceptor": acceptor}
        )

    async def report_failed(self, intent_id: str, *, reason: str) -> None:
        self.states[intent_id] = "failed"
        self.failed.append({"intent_id": intent_id, "reason": reason})


@dataclass
class FakeProviderClient:
    """Backed by real local bare git repositories: `repositories` maps a
    repository id to the path of its bare remote.

    Behaves like a real forge where it matters for recovery: a push only
    creates a branch (`BranchAlreadyExists` otherwise), and a second PR from
    the same branch is refused (`PullRequestAlreadyExists`, like GitHub's
    422). `hooks` run at named points ("before_push", "after_push",
    "after_open_pull_request") so a test can interleave two consumers or
    kill the process mid-step; `revoked` repositories refuse every forge
    call with `ProviderCredentialRejected`."""

    repositories: dict[str, Path]
    protected_branches: frozenset[str] = frozenset({"main"})
    pull_requests: list[PullRequest] = field(default_factory=list)
    pushed_branches: list[tuple[str, str]] = field(default_factory=list)
    clones: list[str] = field(default_factory=list)
    fail_push_for: frozenset[str] = frozenset()
    default: str = "main"
    hooks: dict[str, Callable[[str, str], Awaitable[None]]] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)

    def clone_url(self, repository: str) -> str:
        self.clones.append(repository)
        return str(self.repositories[repository])

    def default_branch(self, repository: str) -> str:
        return self.default

    def _authorize(self, repository: str) -> None:
        if repository in self.revoked:
            raise ProviderCredentialRejected("401 Bad credentials: https://token@forge.example/")

    async def _hook(self, point: str, repository: str, branch: str) -> None:
        hook = self.hooks.get(point)
        if hook is not None:
            await hook(repository, branch)

    def branch_tip(self, repository: str, branch: str) -> str | None:
        result = subprocess.run(
            ["git", "--git-dir", str(self.repositories[repository]), "rev-parse", "--verify",
             "--quiet", f"refs/heads/{branch}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    async def find_branch(self, repository: str, branch: str) -> str | None:
        self._authorize(repository)
        return self.branch_tip(repository, branch)

    async def find_open_pull_request(self, repository: str, branch: str) -> PullRequestResult | None:
        self._authorize(repository)
        for number, existing in enumerate(self.pull_requests, start=1):
            if (existing.repository, existing.branch) == (repository, branch):
                return self._result(repository, number)
        return None

    async def push_branch(self, repository: str, branch: str, *, local_path: str) -> None:
        await self._hook("before_push", repository, branch)
        self._authorize(repository)
        if branch in self.fail_push_for:
            raise RuntimeError("simulated push failure: https://token@forge.example/")
        if self.branch_tip(repository, branch) is not None:
            raise BranchAlreadyExists(branch)
        push_branch(
            local_path,
            remote_url=str(self.repositories[repository]),
            branch=branch,
            protected_branches=self.protected_branches,
        )
        self.pushed_branches.append((repository, branch))
        await self._hook("after_push", repository, branch)

    async def open_pull_request(self, request: PullRequest) -> PullRequestResult:
        self._authorize(request.repository)
        if await self.find_open_pull_request(request.repository, request.branch) is not None:
            raise PullRequestAlreadyExists(request.branch)
        self.pull_requests.append(request)
        result = self._result(request.repository, len(self.pull_requests))
        await self._hook("after_open_pull_request", request.repository, request.branch)
        return result

    def _result(self, repository: str, number: int) -> PullRequestResult:
        return PullRequestResult(url=f"file://{self.repositories[repository]}/pulls/{number}", number=number)
