"""Run handles: the binding E2 mints and E3 (and E2's own tools) consume.

A run handle (`run_id`) is minted server-side by E2's `register_run` and is
bound to the caller that minted it: principal, workspace and exactly one
repository (plus, once the gateway asserts them, OAuth client and grant).
Every later call that presents a `run_id` must come from the same binding:
a valid token for a different principal, workspace or repository is refused,
never treated as a new owner.

E2 owns the durable `RunRegistry` implementation (on the evidence/run-record
owner, not in the edge: the edge holds no credential).  Until it lands,
`UnavailableRunRegistry` refuses every lookup with `runs-unavailable`, so E3
code paths fail closed instead of inventing a binding.  `InMemoryRunRegistry`
is the reference behaviour both E2 and E3 test against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
import re
import secrets

from .toolsets import ToolFailure

if TYPE_CHECKING:
    from .work_source import ForwardedIdentity

__all__ = [
    "RUN_ID",
    "InMemoryRunRegistry",
    "RunBinding",
    "RunRegistry",
    "UnavailableRunRegistry",
    "binding_for",
]

#: `run_` + 26 Crockford base32 characters (a ULID body).
RUN_ID = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")


@dataclass(frozen=True)
class RunBinding:
    """Who a run belongs to.  Two bindings match only if every field matches."""

    principal_id: str
    workspace_id: str
    repo_id: str
    client_id: str | None = None
    grant_id: str | None = None


def binding_for(forwarded: ForwardedIdentity) -> RunBinding:
    """The binding of the caller behind `forwarded`.

    Refuses a caller without a stable principal or workspace: such a caller
    cannot own a run.
    """

    identity = forwarded.identity
    if identity is None or not identity.principal_id or not identity.workspace_id:
        raise ToolFailure(
            "run-binding-unavailable",
            "the caller's assertion carries no principal or workspace to bind a run to",
        )
    return RunBinding(
        principal_id=identity.principal_id,
        workspace_id=identity.workspace_id,
        repo_id=forwarded.repo_id,
    )


class RunRegistry(Protocol):
    """Resolves and mints run handles.  Implementations must be durable."""

    async def register(self, binding: RunBinding, *, idempotency_key: str) -> str:
        """Mint a run for `binding`; the same key for the same binding returns
        the same `run_id`."""

    async def resolve(self, run_id: str, caller: RunBinding) -> RunBinding:
        """The run's binding if `caller` matches it.

        Raises `ToolFailure("run-not-found", ...)` for an unknown, malformed
        or expired id AND for a run bound to a different principal,
        workspace, repository, client or grant: one code and one message for
        both, so a caller cannot probe which run ids exist.
        """


class UnavailableRunRegistry:
    """The registry until E2 ships one: every call fails closed."""

    async def register(self, binding: RunBinding, *, idempotency_key: str) -> str:
        raise ToolFailure("runs-unavailable", "run handles are not available yet")

    async def resolve(self, run_id: str, caller: RunBinding) -> RunBinding:
        raise ToolFailure("runs-unavailable", "run handles are not available yet")


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_NOT_YOURS = "no run with that id belongs to the caller"


class InMemoryRunRegistry:
    """Reference behaviour for tests.  Not durable; never deploy it."""

    def __init__(self) -> None:
        self._runs: dict[str, RunBinding] = {}
        self._by_key: dict[tuple[RunBinding, str], str] = {}

    async def register(self, binding: RunBinding, *, idempotency_key: str) -> str:
        existing = self._by_key.get((binding, idempotency_key))
        if existing is not None:
            return existing
        run_id = "run_" + "".join(secrets.choice(_CROCKFORD) for _ in range(26))
        self._runs[run_id] = binding
        self._by_key[(binding, idempotency_key)] = run_id
        return run_id

    async def resolve(self, run_id: str, caller: RunBinding) -> RunBinding:
        owner = self._runs.get(run_id) if RUN_ID.fullmatch(run_id or "") else None
        if owner is None or owner != caller:
            raise ToolFailure("run-not-found", _NOT_YOURS)
        return owner
