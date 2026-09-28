"""agentops#2520 (E2b): claim_work, heartbeat, report_outcome -- the coordinate
bucket's tools over sprintctl's durable `work.lease.*` operations.

Two kinds of test:

* request/answer tests against canned shell responses (`_FakeShell`), which
  pin exactly what the edge sends and that owner refusals pass through;
* behaviour tests against `_LeaseOwner`, a small in-test stand-in for
  sprintctl 0.10.0's lease semantics (authority TTL, stale takeover,
  claim-superseded, resume re-evaluated by the owner, retained refusals).
  sprintctl proves the real operations against PostgreSQL on its side; this
  stand-in only lets the edge's own paths run end to end.

`TestLeaseContract` runs lease.py's contract cases (expiry with heartbeat,
a dead superseded id, a holder mismatch, a replayed completion on a dead
lease) against both the in-memory `LeaseStore` and the edge tools, as the
shared contract section 6 requires.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import httpx
import pytest
from edge_support import (
    ISSUER,
    SUBJECT,
    FakeShell,
    assertion,
    call,
    edge_client,
    identity_headers,
)
from vuoro_mcp_edge.claim_tools import (
    CLAIM_OWNER_INCOMPATIBLE,
    OPERATION_LEASE_ACQUIRE,
    OPERATION_LEASE_HEARTBEAT,
    OPERATION_LEASE_REPORT_OUTCOME,
    build_toolset,
)
from vuoro_mcp_edge.record_tools import SprintctlRecordStore
from vuoro_mcp_edge.runs import InMemoryRunRegistry, UnavailableRunRegistry
from vuoro_mcp_edge.server import MCP_PATH
from vuoro_mcp_edge.toolsets import ToolFailure, ToolsetContext, ToolSpec
from vuoro_mcp_edge.work_source import ForwardedIdentity, ShellWorkSource
from vuoro_service.identity import Identity
from vuoro_service.lease import (
    LeaseConflictError,
    LeaseHolderMismatchError,
    LeaseNotCurrentError,
    LeaseStore,
)

REQUEST_ID = "01K33333333333333333333333"
REPO_ID = "repo-a"
PRINCIPAL_ID = "vuoro-cloud-control:github:123:0"
OTHER_PRINCIPAL_ID = "vuoro-cloud-control:github:456:0"
WORKSPACE_ID = "01K11111111111111111111111"
RUN_ID = "run_" + "0" * 24 + "AA"
OTHER_RUN_ID = "run_" + "0" * 24 + "BB"
LEASE_ID = "lease_" + "0" * 24 + "AA"
OTHER_LEASE_ID = "lease_" + "0" * 24 + "BB"

RESOLVE = "work.run.resolve-v1"

#: Every owner refusal the contract says the edge passes through unchanged
#: (section 6, with the agentops#2540 renames and additions).
PASS_THROUGH_CODES = (
    "lease-held",
    "claim-superseded",
    "lease-expired",
    "lease-ended",
    "lease-not-found",
    "work-not-found",
    "work-settled",
    "work-blocked",
    "work-not-active",
    "work-awaiting-verification",
    "verification-unsupported",
    "maintenance-active",
    "verification-unsatisfied",
    "run-not-found",
    "idempotency-conflict",
    "invalid-arguments",
)


def _forwarded(
    *,
    principal_id: str = PRINCIPAL_ID,
    client_id: str | None = None,
    grant_id: str | None = None,
) -> ForwardedIdentity:
    identity = Identity(
        actor="github:123",
        environment="vuoro-dev",
        authorities=frozenset({"work:claim"}),
        repo_ids=frozenset({REPO_ID}),
        workspace_id=WORKSPACE_ID,
        principal_id=principal_id,
        client_id=client_id,
        grant_id=grant_id,
    )
    return ForwardedIdentity(
        assertion=f"assert:{principal_id}",
        request_id=REQUEST_ID,
        repo_id=REPO_ID,
        identity=identity,
    )


def _envelope(operation: str, status: str, result: Any, error: Any, http: int) -> httpx.Response:
    return httpx.Response(
        http,
        json={
            "schema_version": "invocation-result/v1",
            "request_id": REQUEST_ID,
            "operation": operation,
            "catalog_revision": "rev-1",
            "status": status,
            "result": result,
            "error": error,
        },
    )


def _accepted(operation: str, result: Any) -> httpx.Response:
    return _envelope(operation, "accepted", result, None, 200)


def _rejected(
    operation: str, code: str, message: str = "rejected", status: int = 409
) -> httpx.Response:
    return _envelope(operation, "rejected", None, {"code": code, "message": message}, status)


def _resolved(principal_id: str = PRINCIPAL_ID, **extra: Any) -> httpx.Response:
    return _accepted(
        RESOLVE,
        {
            "run_id": RUN_ID,
            "principal_id": principal_id,
            "workspace_id": WORKSPACE_ID,
            "client_id": None,
            "grant_id": None,
            **extra,
        },
    )


def _lease(**overrides: Any) -> dict[str, Any]:
    lease = {
        "lease_id": LEASE_ID,
        "generation": 1,
        "item_id": 7,
        "run_id": RUN_ID,
        "principal_id": PRINCIPAL_ID,
        "workspace_id": WORKSPACE_ID,
        "state": "active",
        "ttl_seconds": 600,
        "heartbeat_interval_seconds": 120,
        "acquired_at": "2026-09-28T10:00:00Z",
        "heartbeat_at": "2026-09-28T10:00:00Z",
        "expires_at": "2026-09-28T10:10:00Z",
        "ended_at": None,
        "end_reason": None,
        "takeover_of": None,
        "superseded_by": None,
        "verification": {"profile": "checked", "requirements": ["checks"], "required_checks": []},
    }
    lease.update(overrides)
    return lease


def _claimed(**lease_overrides: Any) -> httpx.Response:
    return _accepted(
        OPERATION_LEASE_ACQUIRE,
        {"repo_id": REPO_ID, "lease": _lease(**lease_overrides), "resumed": False, "took_over": None},
    )


class _FakeShell:
    """A minimal ``/api/invoke/v1`` handler: canned responses, in order."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/invoke/v1"
        self.requests.append(json.loads(request.content))
        if not self._responses:
            raise AssertionError("unexpected extra request")
        return self._responses.pop(0)

    @property
    def operations(self) -> list[str]:
        return [sent["operation"] for sent in self.requests]


def _store(handler: Callable[[httpx.Request], httpx.Response]) -> SprintctlRecordStore:
    return SprintctlRecordStore(
        base_url="http://127.0.0.1:8080", timeout=5.0, transport=httpx.MockTransport(handler)
    )


def _context(runs: Any) -> ToolsetContext:
    return ToolsetContext(
        env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=runs
    )


def _specs(handler: Callable[[httpx.Request], httpx.Response]) -> dict[str, ToolSpec]:
    toolset = build_toolset(_context(_store(handler)))
    assert toolset is not None
    return {spec.name: spec for spec in toolset.tools}


def _call(
    specs: dict[str, ToolSpec], tool: str, arguments: dict[str, Any], forwarded: ForwardedIdentity
) -> dict[str, Any]:
    spec = specs[tool]
    return asyncio.run(spec.run(spec.parse(arguments), forwarded))


def _refused(
    specs: dict[str, ToolSpec], tool: str, arguments: dict[str, Any], forwarded: ForwardedIdentity
) -> ToolFailure:
    with pytest.raises(ToolFailure) as excinfo:
        _call(specs, tool, arguments, forwarded)
    return excinfo.value


CLAIM_ARGS = {"item_id": 7, "run_id": RUN_ID, "idempotency_key": "claim-key-0001"}
HEARTBEAT_ARGS = {"lease_id": LEASE_ID, "run_id": RUN_ID}
REPORT_ARGS = {
    "lease_id": LEASE_ID,
    "run_id": RUN_ID,
    "outcome": "succeeded",
    "idempotency_key": "report-key-0001",
}


# ---------------------------------------------------------------------------
# The toolset gate
# ---------------------------------------------------------------------------


class TestBuildToolsetGate:
    def test_lists_nothing_with_the_unavailable_placeholder(self) -> None:
        assert build_toolset(_context(UnavailableRunRegistry())) is None

    def test_lists_nothing_without_a_durable_lease_owner(self) -> None:
        # The in-memory reference registry has no runtime shell behind it.
        assert build_toolset(_context(InMemoryRunRegistry())) is None

    def test_lists_three_coordinate_tools_over_the_durable_store(self) -> None:
        toolset = build_toolset(_context(_store(_FakeShell())))
        assert toolset is not None
        assert [spec.name for spec in toolset.tools] == ["claim_work", "heartbeat", "report_outcome"]
        assert all(spec.bucket == "coordinate" for spec in toolset.tools)

    def test_definitions_state_the_contract(self) -> None:
        specs = _specs(_FakeShell())
        claim = specs["claim_work"].definition["inputSchema"]
        assert claim["required"] == ["item_id", "run_id", "idempotency_key"]
        assert "ttl_seconds" not in claim["properties"]
        assert claim["additionalProperties"] is False
        heartbeat = specs["heartbeat"].definition["inputSchema"]
        assert "idempotency_key" not in heartbeat["properties"]
        report = specs["report_outcome"].definition["inputSchema"]
        assert report["required"] == ["lease_id", "run_id", "outcome", "idempotency_key"]
        assert "heartbeat_interval_seconds" in specs["claim_work"].definition["description"]


# ---------------------------------------------------------------------------
# claim_work
# ---------------------------------------------------------------------------


class TestClaimWork:
    def test_resolves_the_run_then_acquires_with_the_callers_arguments(self) -> None:
        shell = _FakeShell(_resolved(), _claimed())
        result = _call(_specs(shell), "claim_work", CLAIM_ARGS, _forwarded())
        assert shell.operations == [RESOLVE, OPERATION_LEASE_ACQUIRE]
        assert shell.requests[0]["arguments"] == {"run_id": RUN_ID}
        # Exactly the caller's arguments: the owner's digest covers them raw.
        assert shell.requests[1]["arguments"] == CLAIM_ARGS
        assert shell.requests[1]["idempotency_key"] is None  # envelope-level: not-allowed
        assert result["lease"]["lease_id"] == LEASE_ID
        assert result["lease"]["heartbeat_interval_seconds"] == 120
        assert result["resumed"] is False

    def test_ttl_seconds_is_refused_before_anyone_is_called(self) -> None:
        shell = _FakeShell()
        failure = _refused(
            _specs(shell), "claim_work", {**CLAIM_ARGS, "ttl_seconds": 300}, _forwarded()
        )
        assert failure.code == "invalid-arguments"
        assert "ttl_seconds" in failure.message
        assert shell.requests == []

    @pytest.mark.parametrize(
        "arguments",
        [
            {"run_id": RUN_ID, "idempotency_key": "claim-key-0001"},
            {**CLAIM_ARGS, "item_id": 0},
            {**CLAIM_ARGS, "item_id": True},
            {**CLAIM_ARGS, "item_id": "7"},
            {**CLAIM_ARGS, "run_id": "not-a-run"},
            {"item_id": 7, "run_id": RUN_ID},
            {**CLAIM_ARGS, "idempotency_key": "short"},
            {**CLAIM_ARGS, "reason": "x"},
        ],
    )
    def test_bad_arguments_are_refused_locally(self, arguments: dict[str, Any]) -> None:
        shell = _FakeShell()
        assert _refused(_specs(shell), "claim_work", arguments, _forwarded()).code == (
            "invalid-arguments"
        )
        assert shell.requests == []

    def test_a_run_of_another_principal_is_refused_before_the_lease_owner(self) -> None:
        shell = _FakeShell(_resolved(principal_id=OTHER_PRINCIPAL_ID))
        failure = _refused(_specs(shell), "claim_work", CLAIM_ARGS, _forwarded())
        assert failure.code == "run-not-found"
        assert shell.operations == [RESOLVE]

    def test_a_run_of_another_grant_is_refused_before_the_lease_owner(self) -> None:
        # The run was minted under grant-1; the caller presents grant-2.
        shell = _FakeShell(_resolved(client_id="claude-connector", grant_id="grant-1"))
        failure = _refused(
            _specs(shell),
            "claim_work",
            CLAIM_ARGS,
            _forwarded(client_id="claude-connector", grant_id="grant-2"),
        )
        assert failure.code == "run-not-found"
        assert shell.operations == [RESOLVE]

    def test_a_lease_without_a_heartbeat_interval_is_an_older_owner(self) -> None:
        lease = _lease()
        del lease["heartbeat_interval_seconds"]
        shell = _FakeShell(
            _resolved(),
            _accepted(
                OPERATION_LEASE_ACQUIRE,
                {"repo_id": REPO_ID, "lease": lease, "resumed": False, "took_over": None},
            ),
        )
        failure = _refused(_specs(shell), "claim_work", CLAIM_ARGS, _forwarded())
        assert failure.code == CLAIM_OWNER_INCOMPATIBLE
        assert "sprintctl 0.10.0" in failure.message


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_refreshes_through_the_owner(self) -> None:
        shell = _FakeShell(
            _resolved(),
            _accepted(OPERATION_LEASE_HEARTBEAT, {"repo_id": REPO_ID, "lease": _lease()}),
        )
        result = _call(_specs(shell), "heartbeat", HEARTBEAT_ARGS, _forwarded())
        assert shell.operations == [RESOLVE, OPERATION_LEASE_HEARTBEAT]
        assert shell.requests[1]["arguments"] == HEARTBEAT_ARGS
        assert result["lease"]["heartbeat_interval_seconds"] == 120

    def test_takes_no_idempotency_key(self) -> None:
        shell = _FakeShell()
        failure = _refused(
            _specs(shell),
            "heartbeat",
            {**HEARTBEAT_ARGS, "idempotency_key": "beat-key-0001"},
            _forwarded(),
        )
        assert failure.code == "invalid-arguments"
        assert shell.requests == []

    @pytest.mark.parametrize("lease_id", ["lease-1", "lease_" + "0" * 25, 42, None])
    def test_a_malformed_lease_id_is_refused_locally(self, lease_id: Any) -> None:
        shell = _FakeShell()
        failure = _refused(
            _specs(shell), "heartbeat", {**HEARTBEAT_ARGS, "lease_id": lease_id}, _forwarded()
        )
        assert failure.code == "invalid-arguments"
        assert shell.requests == []

    def test_an_expired_lease_is_refused_once_and_never_retried(self) -> None:
        shell = _FakeShell(
            _resolved(),
            _rejected(OPERATION_LEASE_HEARTBEAT, "lease-expired", "lease went stale"),
        )
        failure = _refused(_specs(shell), "heartbeat", HEARTBEAT_ARGS, _forwarded())
        assert (failure.code, failure.message) == ("lease-expired", "lease went stale")
        assert shell.operations == [RESOLVE, OPERATION_LEASE_HEARTBEAT]


# ---------------------------------------------------------------------------
# report_outcome
# ---------------------------------------------------------------------------


def _report_result(**overrides: Any) -> dict[str, Any]:
    report = {
        "report_id": "outcome_" + "0" * 26,
        "item_id": 7,
        "lease_id": LEASE_ID,
        "run_id": RUN_ID,
        "principal_id": PRINCIPAL_ID,
        "outcome": "succeeded",
        "summary": "",
        "payload": {},
        "checks": [{"name": "pytest", "status": "passed"}],
        "payload_digest": "0" * 64,
        "disposition": "settled",
        "reason_code": None,
        "verification_profile": "checked",
        "decision_id": 1,
        "created_at": "2026-09-28T10:05:00Z",
    }
    report.update(overrides)
    return {
        "repo_id": REPO_ID,
        "report": report,
        "settled": report["disposition"] == "settled",
        "settlement_effect": "settled",
    }


class TestReportOutcome:
    def test_reports_through_report_outcome_v1_with_only_what_was_sent(self) -> None:
        shell = _FakeShell(
            _resolved(), _accepted(OPERATION_LEASE_REPORT_OUTCOME, _report_result())
        )
        arguments = {**REPORT_ARGS, "checks": [{"name": "pytest", "status": "passed"}]}
        result = _call(_specs(shell), "report_outcome", arguments, _forwarded())
        assert shell.operations == [RESOLVE, "work.lease.report-outcome-v1"]
        # No summary/payload defaults added: the owner's digest covers the raw arguments.
        assert shell.requests[1]["arguments"] == arguments
        assert result["settlement_effect"] == "settled"
        assert result["settled"] is True

    def test_a_failed_outcome_with_a_payload(self) -> None:
        shell = _FakeShell(
            _resolved(),
            _accepted(
                OPERATION_LEASE_REPORT_OUTCOME,
                {
                    **_report_result(outcome="failed", disposition="recorded"),
                    "settlement_effect": "lease-released",
                },
            ),
        )
        arguments = {
            **REPORT_ARGS,
            "outcome": "failed",
            "summary": "rate limited",
            "payload": {"rate_limit_event": {"retry_after_seconds": 60}},
        }
        result = _call(_specs(shell), "report_outcome", arguments, _forwarded())
        assert shell.requests[1]["arguments"] == arguments
        assert result["settlement_effect"] == "lease-released"

    @pytest.mark.parametrize(
        "arguments",
        [
            {**REPORT_ARGS, "outcome": "done"},
            {**REPORT_ARGS, "summary": "x" * 4001},
            {**REPORT_ARGS, "payload": [1]},
            {**REPORT_ARGS, "checks": [{"name": "pytest", "status": "ok"}]},
            {**REPORT_ARGS, "checks": [{"name": "", "status": "passed"}]},
            {**REPORT_ARGS, "checks": [{"name": "a", "status": "passed", "extra": 1}]},
            {**REPORT_ARGS, "checks": [{"name": "a", "status": "passed"}] * 65},
            {k: v for k, v in REPORT_ARGS.items() if k != "idempotency_key"},
            {**REPORT_ARGS, "settle": True},
        ],
    )
    def test_bad_arguments_are_refused_locally(self, arguments: dict[str, Any]) -> None:
        shell = _FakeShell()
        assert _refused(_specs(shell), "report_outcome", arguments, _forwarded()).code == (
            "invalid-arguments"
        )
        assert shell.requests == []

    def test_a_result_without_settlement_effect_is_an_older_owner(self) -> None:
        result = _report_result()
        del result["settlement_effect"]
        shell = _FakeShell(_resolved(), _accepted(OPERATION_LEASE_REPORT_OUTCOME, result))
        assert _refused(_specs(shell), "report_outcome", REPORT_ARGS, _forwarded()).code == (
            CLAIM_OWNER_INCOMPATIBLE
        )


# ---------------------------------------------------------------------------
# Pass-through and owner compatibility
# ---------------------------------------------------------------------------


_TOOL_CALLS = (
    ("claim_work", CLAIM_ARGS, OPERATION_LEASE_ACQUIRE),
    ("heartbeat", HEARTBEAT_ARGS, OPERATION_LEASE_HEARTBEAT),
    ("report_outcome", REPORT_ARGS, OPERATION_LEASE_REPORT_OUTCOME),
)


class TestOwnerRefusalsPassThrough:
    @pytest.mark.parametrize("code", PASS_THROUGH_CODES)
    @pytest.mark.parametrize(("tool", "arguments", "operation"), _TOOL_CALLS)
    def test_code_and_message_are_unchanged(
        self, tool: str, arguments: dict[str, Any], operation: str, code: str
    ) -> None:
        message = f"owner says {code}"
        shell = _FakeShell(_resolved(), _rejected(operation, code, message))
        failure = _refused(_specs(shell), tool, arguments, _forwarded())
        assert (failure.code, failure.message) == (code, message)

    def test_claim_superseded_keeps_the_generations_in_the_message(self) -> None:
        # vuoro-service's OperationRejectedError carries no details, so the
        # owner's {claim_id, current_generation, reported_generation} reach
        # the caller in the message only.
        message = (
            f"lease {LEASE_ID} was taken over (claim {OTHER_LEASE_ID}, "
            "current generation 2, reported generation 1)"
        )
        shell = _FakeShell(
            _resolved(), _rejected(OPERATION_LEASE_REPORT_OUTCOME, "claim-superseded", message)
        )
        failure = _refused(_specs(shell), "report_outcome", REPORT_ARGS, _forwarded())
        assert failure.code == "claim-superseded"
        assert "current generation 2" in failure.message

    @pytest.mark.parametrize(("tool", "arguments", "operation"), _TOOL_CALLS)
    def test_an_owner_without_the_lease_operations_is_a_typed_error(
        self, tool: str, arguments: dict[str, Any], operation: str
    ) -> None:
        shell = _FakeShell(
            _resolved(),
            _rejected(operation, "unknown-operation", "not in the active catalog", status=404),
        )
        failure = _refused(_specs(shell), tool, arguments, _forwarded())
        assert failure.code == CLAIM_OWNER_INCOMPATIBLE
        assert operation in failure.message
        assert "sprintctl 0.10.0" in failure.message

    def test_an_unreachable_shell_is_a_claim_shell_failure(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = json.loads(request.content)
            if body["operation"] == RESOLVE:
                return _resolved()
            raise httpx.ConnectError("boom")

        failure = _refused(_specs(handler), "claim_work", CLAIM_ARGS, _forwarded())
        assert failure.code == "claim-shell-unavailable"
        assert calls["n"] == 2  # never retried by the edge


# ---------------------------------------------------------------------------
# A stand-in for sprintctl 0.10.0's lease owner
# ---------------------------------------------------------------------------


class _LeaseOwner:
    """The subset of sprintctl 0.10.0's `work.lease.*` behaviour the edge
    relies on, evaluated only when called, on a manual clock.

    The caller is identified by the forwarded assertion (``assert:<principal>``);
    a run belongs to the principal that `add_run` gave it.
    """

    TTL = 600
    INTERVAL = 120

    def __init__(self) -> None:
        self.now = 0.0
        self.runs: dict[str, str] = {}
        self.leases: dict[str, dict[str, Any]] = {}
        self.current: dict[int, str] = {}
        self.ledger: dict[tuple[str, str, str], tuple[str, Any]] = {}
        self.reports: list[dict[str, Any]] = []
        self.item_refusals: dict[int, str] = {}
        self.calls: list[str] = []
        self._seq = 0

    def add_run(self, run_id: str, principal_id: str) -> None:
        self.runs[run_id] = principal_id

    def _mint(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:026d}"

    def _stale(self, lease: dict[str, Any]) -> bool:
        return lease["heartbeat_at"] + self.TTL <= self.now

    def _view(self, lease: dict[str, Any]) -> dict[str, Any]:
        return {
            "lease_id": lease["lease_id"],
            "generation": lease["generation"],
            "item_id": lease["item_id"],
            "run_id": lease["run_id"],
            "principal_id": lease["principal_id"],
            "workspace_id": WORKSPACE_ID,
            "state": lease["state"],
            "ttl_seconds": self.TTL,
            "heartbeat_interval_seconds": self.INTERVAL,
            "heartbeat_at": lease["heartbeat_at"],
            "expires_at": lease["heartbeat_at"] + self.TTL,
            "takeover_of": lease["takeover_of"],
            "superseded_by": lease["superseded_by"],
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        operation, arguments = body["operation"], body["arguments"]
        self.calls.append(operation)
        principal = next(
            v.removeprefix("assert:") for v in request.headers.values() if v.startswith("assert:")
        )
        try:
            if self.runs.get(arguments.get("run_id")) != principal:
                raise _Refusal("run-not-found", "no run with that id belongs to the caller", 404)
            handler = {
                RESOLVE: self._resolve,
                OPERATION_LEASE_ACQUIRE: self._acquire,
                OPERATION_LEASE_HEARTBEAT: self._heartbeat,
                OPERATION_LEASE_REPORT_OUTCOME: self._report,
            }[operation]
            return _accepted(operation, handler(arguments, principal))
        except _Refusal as refusal:
            return _rejected(operation, refusal.code, refusal.message, refusal.status)

    def _resolve(self, arguments: dict[str, Any], principal: str) -> dict[str, Any]:
        return {
            "run_id": arguments["run_id"],
            "principal_id": principal,
            "workspace_id": WORKSPACE_ID,
            "client_id": None,
            "grant_id": None,
        }

    def _superseded(self, lease: dict[str, Any]) -> _Refusal:
        successor = self.leases[lease["superseded_by"]]
        return _Refusal(
            "claim-superseded",
            f"lease {lease['lease_id']} was taken over (claim {successor['lease_id']}, "
            f"current generation {successor['generation']}, "
            f"reported generation {lease['generation']})",
        )

    def _acquire(self, arguments: dict[str, Any], principal: str) -> dict[str, Any]:
        if "ttl_seconds" in arguments:
            raise _Refusal("invalid-arguments", "ttl_seconds is not an argument", 422)
        item_id = arguments["item_id"]
        digest = json.dumps({"item_id": item_id, "run_id": arguments["run_id"]}, sort_keys=True)
        row = (principal, "claim_work", arguments["idempotency_key"])
        if row in self.ledger:
            stored_digest, lease_id = self.ledger[row]
            if stored_digest != digest:
                raise _Refusal("idempotency-conflict", "key reused with other arguments")
            # A replay is re-evaluated, never answered from the ledger.
            lease = self.leases[lease_id]
            if lease["state"] == "superseded":
                raise self._superseded(lease)
            if lease["state"] == "active":
                # Fresh: refreshed.  Stale and nobody took it: reactivated in
                # place, same id and generation (agentops#2540 semantics 5).
                lease["heartbeat_at"] = self.now
            return {"repo_id": REPO_ID, "lease": self._view(lease), "resumed": True, "took_over": None}
        if item_id in self.item_refusals:
            raise _Refusal(self.item_refusals[item_id], "the item cannot be leased")
        took_over = None
        current_id = self.current.get(item_id)
        if current_id is not None:
            current = self.leases[current_id]
            if not self._stale(current):
                raise _Refusal("lease-held", f"item #{item_id} is leased")
            took_over = current_id
        generation = 1 + sum(1 for lease in self.leases.values() if lease["item_id"] == item_id)
        lease = {
            "lease_id": self._mint("lease"),
            "generation": generation,
            "item_id": item_id,
            "run_id": arguments["run_id"],
            "principal_id": principal,
            "state": "active",
            "heartbeat_at": self.now,
            "takeover_of": took_over,
            "superseded_by": None,
        }
        self.leases[lease["lease_id"]] = lease
        if took_over is not None:
            self.leases[took_over]["state"] = "superseded"
            self.leases[took_over]["superseded_by"] = lease["lease_id"]
        self.current[item_id] = lease["lease_id"]
        self.ledger[row] = (digest, lease["lease_id"])
        return {"repo_id": REPO_ID, "lease": self._view(lease), "resumed": False, "took_over": took_over}

    def _own_lease(self, arguments: dict[str, Any], principal: str) -> dict[str, Any]:
        lease = self.leases.get(arguments["lease_id"])
        if lease is None or lease["principal_id"] != principal or lease["run_id"] != arguments["run_id"]:
            raise _Refusal("lease-not-found", "no such lease for the caller", 404)
        return lease

    def _dead(self, lease: dict[str, Any]) -> _Refusal | None:
        if lease["state"] == "superseded":
            return self._superseded(lease)
        if lease["state"] in ("settled", "released"):
            return _Refusal("lease-ended", "the lease was already settled or released")
        if self._stale(lease):
            return _Refusal("lease-expired", "the lease went stale")
        return None

    def _heartbeat(self, arguments: dict[str, Any], principal: str) -> dict[str, Any]:
        lease = self._own_lease(arguments, principal)
        refusal = self._dead(lease)
        if refusal is not None:
            raise refusal
        lease["heartbeat_at"] = self.now
        return {"repo_id": REPO_ID, "lease": self._view(lease)}

    def _report(self, arguments: dict[str, Any], principal: str) -> dict[str, Any]:
        lease = self._own_lease(arguments, principal)  # a stranger stores nothing
        body = {k: v for k, v in arguments.items() if k != "idempotency_key"}
        digest = json.dumps(body, sort_keys=True)
        row = (principal, "report_outcome", arguments["idempotency_key"])
        if row in self.ledger:
            stored_digest, answer = self.ledger[row]
            if stored_digest != digest:
                raise _Refusal("idempotency-conflict", "key reused with other arguments")
            if isinstance(answer, _Refusal):
                raise answer
            return answer
        refusal = self._dead(lease)
        report = {"lease_id": lease["lease_id"], "outcome": arguments["outcome"]}
        if refusal is not None:
            # Committed first, refused second: kept as evidence.
            self.reports.append({**report, "disposition": "rejected", "reason_code": refusal.code})
            self.ledger[row] = (digest, refusal)
            raise refusal
        settled = arguments["outcome"] == "succeeded"
        lease["state"] = "settled" if settled else "released"
        self.current.pop(lease["item_id"], None)
        report["disposition"] = "settled" if settled else "recorded"
        self.reports.append(report)
        answer = {
            "repo_id": REPO_ID,
            "report": report,
            "settled": settled,
            "settlement_effect": "settled" if settled else "lease-released",
        }
        self.ledger[row] = (digest, answer)
        return answer


class _Refusal(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _owner_specs() -> tuple[_LeaseOwner, dict[str, ToolSpec]]:
    owner = _LeaseOwner()
    owner.add_run(RUN_ID, PRINCIPAL_ID)
    owner.add_run(OTHER_RUN_ID, OTHER_PRINCIPAL_ID)
    return owner, _specs(owner)


A = _forwarded()
B = _forwarded(principal_id=OTHER_PRINCIPAL_ID)
B_CLAIM = {"item_id": 7, "run_id": OTHER_RUN_ID, "idempotency_key": "claim-key-0002"}


class TestAgainstTheLeaseOwner:
    def test_a_retried_claim_is_re_evaluated_by_the_owner_not_replayed(self) -> None:
        owner, specs = _owner_specs()
        first = _call(specs, "claim_work", CLAIM_ARGS, A)
        owner.now = 500  # nearly stale
        again = _call(specs, "claim_work", CLAIM_ARGS, A)
        # Both reached the owner; the retry is the same lease, refreshed.
        assert owner.calls.count(OPERATION_LEASE_ACQUIRE) == 2
        assert again["resumed"] is True
        assert again["lease"]["lease_id"] == first["lease"]["lease_id"]
        assert again["lease"]["heartbeat_at"] == 500

    def test_a_retry_after_going_stale_reactivates_the_same_lease(self) -> None:
        owner, specs = _owner_specs()
        first = _call(specs, "claim_work", CLAIM_ARGS, A)
        owner.now = 700
        assert _refused(specs, "heartbeat", {"lease_id": first["lease"]["lease_id"], "run_id": RUN_ID}, A).code == "lease-expired"
        again = _call(specs, "claim_work", CLAIM_ARGS, A)
        assert again["resumed"] is True
        assert (again["lease"]["lease_id"], again["lease"]["generation"]) == (
            first["lease"]["lease_id"],
            first["lease"]["generation"],
        )

    def test_the_same_key_with_another_item_is_an_idempotency_conflict(self) -> None:
        _, specs = _owner_specs()
        _call(specs, "claim_work", CLAIM_ARGS, A)
        failure = _refused(specs, "claim_work", {**CLAIM_ARGS, "item_id": 8}, A)
        assert failure.code == "idempotency-conflict"

    def test_a_live_lease_is_held_against_another_principal(self) -> None:
        owner, specs = _owner_specs()
        _call(specs, "claim_work", CLAIM_ARGS, A)
        owner.now = 599
        assert _refused(specs, "claim_work", B_CLAIM, B).code == "lease-held"

    def test_takeover_supersedes_and_the_former_holder_gets_claim_superseded(self) -> None:
        owner, specs = _owner_specs()
        a = _call(specs, "claim_work", CLAIM_ARGS, A)["lease"]
        owner.now = 700
        b = _call(specs, "claim_work", B_CLAIM, B)
        assert b["took_over"] == a["lease_id"]
        assert b["lease"]["generation"] == 2
        a_beat = {"lease_id": a["lease_id"], "run_id": RUN_ID}
        assert _refused(specs, "heartbeat", a_beat, A).code == "claim-superseded"
        # Re-presenting the claim that was taken over is claim-superseded too.
        assert _refused(specs, "claim_work", CLAIM_ARGS, A).code == "claim-superseded"
        late = _refused(
            specs, "report_outcome",
            {**a_beat, "outcome": "succeeded", "idempotency_key": "report-key-late1"}, A,
        )
        assert late.code == "claim-superseded"
        assert "current generation 2, reported generation 1" in late.message
        # The late report was kept as evidence, and B still holds the item.
        assert owner.reports[-1]["reason_code"] == "claim-superseded"
        assert owner.current[7] == b["lease"]["lease_id"]

    def test_a_stranger_presenting_a_lease_id_gets_lease_not_found(self) -> None:
        owner, specs = _owner_specs()
        a = _call(specs, "claim_work", CLAIM_ARGS, A)["lease"]
        refused = _refused(
            specs, "report_outcome",
            {"lease_id": a["lease_id"], "run_id": OTHER_RUN_ID, "outcome": "succeeded",
             "idempotency_key": "report-key-0002"},
            B,
        )
        assert refused.code == "lease-not-found"
        assert owner.reports == []  # a stranger's report stores nothing

    def test_a_run_of_another_principal_never_reaches_the_lease_operation(self) -> None:
        owner, specs = _owner_specs()
        failure = _refused(specs, "claim_work", {**CLAIM_ARGS, "run_id": OTHER_RUN_ID}, A)
        assert failure.code == "run-not-found"
        assert owner.calls == [RESOLVE]

    def test_a_retried_report_returns_the_same_answer(self) -> None:
        owner, specs = _owner_specs()
        a = _call(specs, "claim_work", CLAIM_ARGS, A)["lease"]
        report = {**REPORT_ARGS, "lease_id": a["lease_id"]}
        first = _call(specs, "report_outcome", report, A)
        again = _call(specs, "report_outcome", report, A)
        assert first == again
        assert len(owner.reports) == 1
        changed = _refused(specs, "report_outcome", {**report, "outcome": "failed"}, A)
        assert changed.code == "idempotency-conflict"

    @pytest.mark.parametrize(
        "code",
        ["work-blocked", "work-not-active", "work-awaiting-verification",
         "verification-unsupported", "maintenance-active"],
    )
    def test_an_item_the_owner_will_not_lease(self, code: str) -> None:
        owner, specs = _owner_specs()
        owner.item_refusals[7] = code
        assert _refused(specs, "claim_work", CLAIM_ARGS, A).code == code

    def test_ttl_seconds_never_reaches_the_owner(self) -> None:
        owner, specs = _owner_specs()
        assert _refused(specs, "claim_work", {**CLAIM_ARGS, "ttl_seconds": 30}, A).code == (
            "invalid-arguments"
        )
        assert owner.calls == []


# ---------------------------------------------------------------------------
# lease.py's contract cases, over both implementations (contract section 6)
# ---------------------------------------------------------------------------

#: The contract's error mapping: which owner codes stand for each lease.py
#: refusal.  lease.py gives one answer for a dead lease; the owner tells the
#: verified holder why, and answers lease-not-found to anyone else.
_DURABLE_CODES: dict[type[Exception], frozenset[str]] = {
    LeaseConflictError: frozenset({"lease-held"}),
    LeaseHolderMismatchError: frozenset({"lease-not-found"}),
    LeaseNotCurrentError: frozenset(
        {"claim-superseded", "lease-expired", "lease-ended", "lease-not-found"}
    ),
}


class _InMemory:
    name = "in-memory"

    def __init__(self) -> None:
        self.now = 0.0
        self.store = LeaseStore(clock=lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def claim(self, subject: int, holder: str) -> str:
        return self.store.claim(str(subject), holder, ttl_seconds=_LeaseOwner.TTL).lease_id

    def heartbeat(self, lease_id: str, holder: str) -> None:
        self.store.heartbeat(lease_id, holder)

    def complete(self, lease_id: str, holder: str) -> None:
        self.store.complete(lease_id, holder, result={"by": holder})

    def retained(self, subject: int) -> int:
        return len(self.store.retained_outcomes(str(subject)))

    @contextmanager
    def refused(self, kind: type[Exception]) -> Iterator[None]:
        with pytest.raises(kind):
            yield


class _Durable:
    """The edge tools over `_LeaseOwner`."""

    name = "durable"
    _RUNS: ClassVar[dict[str, tuple[str, str]]] = {
        "a": (RUN_ID, PRINCIPAL_ID),
        "b": (OTHER_RUN_ID, OTHER_PRINCIPAL_ID),
    }

    def __init__(self) -> None:
        self.owner = _LeaseOwner()
        for run_id, principal in self._RUNS.values():
            self.owner.add_run(run_id, principal)
        self.specs = _specs(self.owner)
        self._keys = 0

    def _key(self, prefix: str) -> str:
        self._keys += 1
        return f"{prefix}-key-{self._keys:04d}"

    def _as(self, holder: str) -> tuple[str, ForwardedIdentity]:
        run_id, principal = self._RUNS[holder]
        return run_id, _forwarded(principal_id=principal)

    def advance(self, seconds: float) -> None:
        self.owner.now += seconds

    def claim(self, subject: int, holder: str) -> str:
        run_id, forwarded = self._as(holder)
        arguments = {"item_id": subject, "run_id": run_id, "idempotency_key": self._key("claim")}
        return _call(self.specs, "claim_work", arguments, forwarded)["lease"]["lease_id"]

    def heartbeat(self, lease_id: str, holder: str) -> None:
        run_id, forwarded = self._as(holder)
        _call(self.specs, "heartbeat", {"lease_id": lease_id, "run_id": run_id}, forwarded)

    def complete(self, lease_id: str, holder: str) -> None:
        run_id, forwarded = self._as(holder)
        arguments = {
            "lease_id": lease_id, "run_id": run_id, "outcome": "succeeded",
            "idempotency_key": self._key("report"),
        }
        _call(self.specs, "report_outcome", arguments, forwarded)

    def retained(self, subject: int) -> int:
        return sum(1 for report in self.owner.reports if report["disposition"] == "rejected")

    @contextmanager
    def refused(self, kind: type[Exception]) -> Iterator[None]:
        with pytest.raises(ToolFailure) as excinfo:
            yield
        assert excinfo.value.code in _DURABLE_CODES[kind], excinfo.value.code


@pytest.fixture(params=[_InMemory, _Durable], ids=["in-memory", "durable"])
def leases(request: pytest.FixtureRequest) -> Any:
    return request.param()


class TestLeaseContract:
    """The expiry boundary differs by one tick between the two (lease.py:
    ``now > hb + ttl``; the owner: ``hb + ttl <= now``), so no case probes
    the exact instant."""

    def test_expiry_with_heartbeat(self, leases: Any) -> None:
        lease_id = leases.claim(7, "a")
        leases.advance(400)
        leases.heartbeat(lease_id, "a")
        leases.advance(400)  # 800 s since the claim, 400 since the heartbeat
        leases.heartbeat(lease_id, "a")
        with leases.refused(LeaseConflictError):
            leases.claim(7, "b")
        leases.advance(700)
        with leases.refused(LeaseNotCurrentError):
            leases.heartbeat(lease_id, "a")

    def test_a_superseded_lease_id_is_permanently_dead(self, leases: Any) -> None:
        old = leases.claim(7, "a")
        leases.advance(700)
        new = leases.claim(7, "b")
        assert new != old
        with leases.refused(LeaseNotCurrentError):
            leases.heartbeat(old, "a")
        with leases.refused(LeaseNotCurrentError):
            leases.complete(old, "a")
        leases.heartbeat(new, "b")
        leases.complete(new, "b")

    def test_a_holder_mismatch_is_refused_and_leaves_the_lease_alive(self, leases: Any) -> None:
        lease_id = leases.claim(7, "a")
        with leases.refused(LeaseHolderMismatchError):
            leases.heartbeat(lease_id, "b")
        with leases.refused(LeaseHolderMismatchError):
            leases.complete(lease_id, "b")
        leases.heartbeat(lease_id, "a")
        leases.complete(lease_id, "a")

    def test_a_replayed_completion_on_a_dead_lease_fails_and_is_retained(
        self, leases: Any
    ) -> None:
        lease_id = leases.claim(7, "a")
        leases.advance(700)
        leases.claim(7, "b")
        with leases.refused(LeaseNotCurrentError):
            leases.complete(lease_id, "a")
        # INV-L1: the former holder's late result is kept, and settles nothing.
        assert leases.retained(7) == 1

    def test_a_second_completion_of_a_settled_lease_fails(self, leases: Any) -> None:
        lease_id = leases.claim(7, "a")
        leases.complete(lease_id, "a")
        with leases.refused(LeaseNotCurrentError):
            leases.complete(lease_id, "a")
        with leases.refused(LeaseNotCurrentError):
            leases.heartbeat(lease_id, "a")


# ---------------------------------------------------------------------------
# Through the MCP server: the bucket's authority and the result envelope
# ---------------------------------------------------------------------------


#: The principal the gateway assertions in edge_support resolve to.
EDGE_PRINCIPAL_ID = f"{ISSUER}:{SUBJECT}:0"


def _edge_owner() -> _LeaseOwner:
    owner = _LeaseOwner()
    owner.add_run(RUN_ID, EDGE_PRINCIPAL_ID)
    return owner


class _AssertionAsPrincipal(httpx.MockTransport):
    """Rewrites the forwarded JWT into ``assert:<principal>`` for `_LeaseOwner`
    (the stand-in does not verify assertions; the edge already did)."""

    def __init__(self, owner: _LeaseOwner) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            request.headers["X-Vuoro-Identity"] = f"assert:{EDGE_PRINCIPAL_ID}"
            return owner(request)

        super().__init__(handler)


def test_claim_work_through_the_server_needs_work_claim(keys) -> None:
    owner = _edge_owner()
    store = SprintctlRecordStore(
        base_url="http://127.0.0.1:8080", timeout=5.0, transport=_AssertionAsPrincipal(owner)
    )
    toolset = build_toolset(_context(store))
    client = edge_client(keys[0], FakeShell(), toolsets=(toolset,))

    # An authority no registered tool uses is refused at the door.
    evidence_only = client.post(
        MCP_PATH,
        headers=identity_headers(assertion(keys[1], authorities=["work:read", "work:evidence"])),
        json=call("claim_work", CLAIM_ARGS),
    )
    assert evidence_only.status_code == 401
    # work:read alone reaches the server but not the coordinate bucket.
    read_only = client.post(
        MCP_PATH,
        headers=identity_headers(assertion(keys[1], authorities=["work:read"])),
        json=call("claim_work", CLAIM_ARGS),
    ).json()["result"]
    assert read_only["structuredContent"]["error"]["code"] == "authority-required"
    assert owner.calls == []

    granted = client.post(
        MCP_PATH,
        headers=identity_headers(assertion(keys[1], authorities=["work:read", "work:claim"])),
        json=call("claim_work", CLAIM_ARGS),
    ).json()["result"]
    assert granted["resultType"] == "complete"
    assert granted["isError"] is False
    assert granted["structuredContent"]["lease"]["heartbeat_interval_seconds"] == 120
    assert owner.calls == [RESOLVE, OPERATION_LEASE_ACQUIRE]

    ttl = client.post(
        MCP_PATH,
        headers=identity_headers(assertion(keys[1], authorities=["work:read", "work:claim"])),
        json=call("claim_work", {**CLAIM_ARGS, "ttl_seconds": 60}),
    ).json()["result"]
    assert ttl["isError"] is True
    assert ttl["structuredContent"]["error"]["code"] == "invalid-arguments"
