"""Provider-neutral agent directory and inbox contract (contract v1).

Spec: docs/architecture/agent-directory-and-inbox.md and the
docs/architecture/agent-*-v1.schema.json files. Methods map 1:1 to the ten
served operations. Each takes a caller context (the credential's workspace and
principal, and the outer repo_id of the call) plus the operation arguments, and
returns the result dict or raises ContractRefusal(code) with the contract's own
kebab-case error code. Bindings normalize owner errors here; scenarios only
speak contract codes.

Result shapes the spec leaves open are fixed here so bindings can normalize:
  register  -> {"instance_id", "declaration_revision", "replayed"}
  observe   -> {"instance_id", "fact", "observed_at", "valid_until", "source", "replayed"}
  retire    -> {"instance_id", "retired_at", "replayed"}
  list      -> {"entries": [agent-directory-entry/v1, ...]}
  describe  -> an agent-directory-entry/v1 (argument: principal_id)
  send      -> the owner stamps plus "replayed"
  read      -> {"messages": [stamped message, ...], "next_after_seq"}
  status    -> {"message": stamped message, "receipts": [receipt, ...]}
  receipt / cancel -> an agent-message-receipt/v1 plus "replayed"

The test hooks (clock, restart, item and run creation, concurrent sends) are
test-only and never form part of the published operation API.
"""
import random
from dataclasses import dataclass
from typing import Protocol

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_rng = random.Random()


def ulid() -> str:
    return "".join(_rng.choice(_CROCKFORD) for _ in range(26))


def new_instance_id() -> str:
    return "ins_" + ulid()


def new_message_id() -> str:
    return "msg_" + ulid()


# Provider capabilities (InboxProvider.capabilities).
CONCURRENT_COMMIT_ORDER = "concurrent-commit-order"
RESTART = "restart"
CLOCK = "clock"


@dataclass(frozen=True)
class Ctx:
    """Caller context: workspace and principal come from the credential."""
    workspace_id: str
    repo_id: str
    principal_id: str


class ContractRefusal(Exception):
    def __init__(self, code: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


@dataclass
class ReversedSends:
    """Outcome of two sends whose transactions started A then B.

    commit_order lists the message_ids in the order they actually committed.
    first_poll is a read-v1 result (after_seq=0, limit=100) taken by the
    recipient between the two commits; second_poll resumes from
    first_poll["next_after_seq"] after both committed.
    """
    commit_order: list[str]
    first_poll: dict
    second_poll: dict


class InboxProvider(Protocol):
    capabilities: frozenset[str]

    # work.agent.*
    def register(self, ctx: Ctx, *, instance_id: str, declaration: dict, idempotency_key: str) -> dict: ...
    def observe(self, ctx: Ctx, *, instance_id: str, fact: str, valid_for_seconds: int,
                idempotency_key: str, channel: str | None = None, run_id: str | None = None) -> dict: ...
    def retire(self, ctx: Ctx, *, instance_id: str, reason: str, idempotency_key: str) -> dict: ...
    def list(self, ctx: Ctx) -> dict: ...
    def describe(self, ctx: Ctx, *, principal_id: str) -> dict: ...

    # work.message.*
    def send(self, ctx: Ctx, *, message: dict) -> dict: ...
    def read(self, ctx: Ctx, *, after_seq: int, limit: int, instance_id: str | None = None) -> dict: ...
    def status(self, ctx: Ctx, *, message_id: str) -> dict: ...
    def receipt(self, ctx: Ctx, *, message_id: str, instance_id: str, kind: str, **fields) -> dict: ...
    def cancel(self, ctx: Ctx, *, message_id: str, reason: str) -> dict: ...

    # Test hooks. Owner records the tests may name (a work item, a registered run).
    def new_item(self, ctx: Ctx) -> int: ...
    def new_run(self, ctx: Ctx) -> str: ...
    # CLOCK: advance the owner's clock (every later stamp moves forward).
    def advance_clock(self, seconds: float) -> None: ...
    # RESTART: drop all in-process state and reconnect to the durable store.
    def restart(self) -> None: ...
    # CONCURRENT_COMMIT_ORDER: two sends, A started first, B committed first
    # when the backend permits it; a backend that serializes sends within a
    # repository lets A commit first. A reader polls between the commits.
    def send_concurrently_reversed(self, sender: Ctx, message_a: dict, message_b: dict,
                                   reader: Ctx) -> ReversedSends: ...
