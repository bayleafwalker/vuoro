"""Fake IntentSource and ProviderClient: local git repos, no network."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from vuoro_reconciler.git_ops import push_branch
from vuoro_reconciler.intents import Acceptor, EffectIntent
from vuoro_reconciler.provider import PullRequest, PullRequestResult


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
    repository id to the path of its bare remote."""

    repositories: dict[str, Path]
    protected_branches: frozenset[str] = frozenset({"main"})
    pull_requests: list[PullRequest] = field(default_factory=list)
    pushed_branches: list[tuple[str, str]] = field(default_factory=list)
    clones: list[str] = field(default_factory=list)
    fail_push_for: frozenset[str] = frozenset()
    default: str = "main"

    def clone_url(self, repository: str) -> str:
        self.clones.append(repository)
        return str(self.repositories[repository])

    def default_branch(self, repository: str) -> str:
        return self.default

    async def push_branch(self, repository: str, branch: str, *, local_path: str) -> None:
        if branch in self.fail_push_for:
            raise RuntimeError("simulated push failure: https://token@forge.example/")
        push_branch(
            local_path,
            remote_url=str(self.repositories[repository]),
            branch=branch,
            protected_branches=self.protected_branches,
        )
        self.pushed_branches.append((repository, branch))

    async def open_pull_request(self, request: PullRequest) -> PullRequestResult:
        for number, existing in enumerate(self.pull_requests, start=1):
            if (existing.repository, existing.branch) == (request.repository, request.branch):
                return self._result(request.repository, number)
        self.pull_requests.append(request)
        return self._result(request.repository, len(self.pull_requests))

    def _result(self, repository: str, number: int) -> PullRequestResult:
        return PullRequestResult(url=f"file://{self.repositories[repository]}/pulls/{number}", number=number)
