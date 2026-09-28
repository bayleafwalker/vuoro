"""Toolset builder owned by E2 (agentops#2466, E2b agentops#2520): claim_work, heartbeat, report_outcome -- bucket "coordinate" (vuoro:work.claim -> work:claim), on an exclusive, durable, owner-backed lease.

Only the owning work item edits this module; see
docs/plans/2026-09-26-e2-e3-shared-contract.md section 6.

Design notes (for reviewers):

* The lease owner is sprintctl (0.10.0, remote schema 18): its
  `work.lease.acquire-v1`, `work.lease.heartbeat-v1` and
  `work.lease.report-outcome-v1` operations, reached through the runtime
  shell's invoke API with the caller's own forwarded assertion -- the same
  client, upstream and edge-proof signer the record bucket uses
  (`SprintctlRecordStore.shell_client`).  The edge holds no credential, no
  DSN and no lease state.
* The outcome tool is `report_outcome`, not `complete_work`: the contract's
  agentops#2540 amendment renames it ("the edge tool becomes
  `report_outcome`"), and sprintctl 0.10.0 keeps `work.lease.complete-v1`
  only as a deprecated alias of `work.lease.report-outcome-v1` (same input,
  result and ledger tool, `report_outcome`).  The worker reports; the owner
  settles.
* The edge never schedules, expires or retries anything (TS-1).  Expiry is
  evaluated by the owner, against its own clock, when someone calls.  Each
  tool makes exactly one owner call after resolving the run; a refusal is
  returned to the caller, never retried here.
* Every tool first resolves the caller's `run_id` through `context.runs`,
  exactly as the record tools do, so a run that is not the caller's whole
  binding (principal, workspace, repository, OAuth client and grant) is
  `run-not-found` before the lease owner is asked.  The owner checks the
  binding again and holds the lease by that binding.
* Idempotency lives with the owner (sprintctl's `work_idempotency_ledger`,
  keyed (repo, workspace, principal, tool, key)).  `claim_work` uses it for
  conflicts only: a same-key, same-arguments retry is re-evaluated by the
  owner (`resumed: true`), never replayed from a stored answer -- that is
  how a restarted worker resumes its own lease.  The edge therefore keeps no
  ledger and forwards the caller's arguments unchanged (the owner's digest
  covers the raw arguments, so the edge must not add defaults).
  `heartbeat` takes no key: sprintctl's input schema forbids one.
* `ttl_seconds` is no longer an argument (agentops#2540): the TTL is the
  authority's configuration and every lease advertises
  `heartbeat_interval_seconds`.  The edge refuses `ttl_seconds` with
  `invalid-arguments` before calling anyone, and treats a lease without
  `heartbeat_interval_seconds` as an owner older than 0.10.0.
* Owner refusals pass through unchanged (code and message): `lease-held`,
  `claim-superseded`, `lease-expired`, `lease-ended`, `lease-not-found`,
  `work-not-found`, `work-settled`, `work-blocked`, `work-not-active`,
  `work-awaiting-verification`, `verification-unsupported`,
  `maintenance-active`, `verification-unsatisfied`, `run-not-found`,
  `idempotency-conflict`, `invalid-arguments`.  sprintctl attaches
  `details: {claim_id, current_generation, reported_generation}` to
  `claim-superseded`, but vuoro-service's `OperationRejectedError` carries
  no details, so the generations reach the caller only in the message (and
  in `work.lease.read-v1`).
* `build_toolset` returns `None` unless `context.runs` is a durable store
  that exposes the runtime-shell client (`shell_client`): with the
  unavailable placeholder or the in-memory reference registry there is no
  durable lease owner, and the bucket lists nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .idempotency import IDEMPOTENCY_KEY_SCHEMA, require_key
from .record_tools import RECORD_OWNER_INCOMPATIBLE, RecordShellClient
from .runs import RUN_ID, RunRegistry, UnavailableRunRegistry, binding_for
from .toolsets import WRITE_ANNOTATIONS, ToolFailure, ToolSet, ToolsetContext, ToolSpec
from .work_source import ForwardedIdentity

__all__ = [
    "CLAIM_OWNER_INCOMPATIBLE",
    "LEASE_ID",
    "OPERATION_LEASE_ACQUIRE",
    "OPERATION_LEASE_HEARTBEAT",
    "OPERATION_LEASE_REPORT_OUTCOME",
    "REQUIRED_SPRINTCTL",
    "SprintctlLeaseOwner",
    "build_toolset",
]

OPERATION_LEASE_ACQUIRE = "work.lease.acquire-v1"
OPERATION_LEASE_HEARTBEAT = "work.lease.heartbeat-v1"
OPERATION_LEASE_REPORT_OUTCOME = "work.lease.report-outcome-v1"

#: The owner release whose lease contract these tools are built against
#: (agentops#2540: authority TTL, heartbeat_interval_seconds,
#: claim-superseded, report-outcome-v1).
REQUIRED_SPRINTCTL = "0.10.0"
CLAIM_OWNER_INCOMPATIBLE = "claim-owner-incompatible"
_CLAIM_SHELL_UNAVAILABLE = "claim-shell-unavailable"
_CLAIM_SHELL_REJECTED = "claim-shell-rejected"

#: `lease_` + 26 Crockford base32 characters, as sprintctl mints it.
LEASE_ID = re.compile(r"^lease_[0-9A-HJKMNP-TV-Z]{26}$")

_SUMMARY_MAX = 4000
_CHECKS_MAX = 64
_CHECK_NAME_MAX = 128
_CHECK_REF_MAX = 512


class SprintctlLeaseOwner:
    """sprintctl's `work.lease.*` operations, through the runtime shell.

    Holds no lease state: every answer is the owner's, evaluated when called.
    """

    def __init__(self, client: RecordShellClient) -> None:
        self._client = client

    async def _invoke(
        self, operation: str, arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        try:
            result = await self._client.invoke(operation, arguments, forwarded)
        except ToolFailure as failure:
            raise _as_claim_failure(operation, failure) from failure
        if not isinstance(result, dict):
            raise ToolFailure(_CLAIM_SHELL_UNAVAILABLE, f"{operation} returned a malformed result")
        return result

    async def acquire(
        self, arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        result = await self._invoke(OPERATION_LEASE_ACQUIRE, arguments, forwarded)
        _require_lease(result.get("lease"), OPERATION_LEASE_ACQUIRE)
        return result

    async def heartbeat(
        self, arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        result = await self._invoke(OPERATION_LEASE_HEARTBEAT, arguments, forwarded)
        _require_lease(result.get("lease"), OPERATION_LEASE_HEARTBEAT)
        return result

    async def report_outcome(
        self, arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        result = await self._invoke(OPERATION_LEASE_REPORT_OUTCOME, arguments, forwarded)
        if not isinstance(result.get("report"), dict):
            raise ToolFailure(
                _CLAIM_SHELL_UNAVAILABLE, f"{OPERATION_LEASE_REPORT_OUTCOME} returned no report"
            )
        if "settlement_effect" not in result:
            raise ToolFailure(
                CLAIM_OWNER_INCOMPATIBLE,
                f"{OPERATION_LEASE_REPORT_OUTCOME} returned no settlement_effect; the "
                f"claim tools need sprintctl {REQUIRED_SPRINTCTL} or later",
            )
        return result


def _as_claim_failure(operation: str, failure: ToolFailure) -> ToolFailure:
    """Rename the shared shell client's own failures for this bucket.

    Owner refusals (every other code) pass through unchanged.
    """

    if failure.code == RECORD_OWNER_INCOMPATIBLE:
        return ToolFailure(
            CLAIM_OWNER_INCOMPATIBLE,
            f"the runtime's work adapter does not provide {operation}; the "
            f"claim tools need sprintctl {REQUIRED_SPRINTCTL} or later",
        )
    if failure.code == "record-shell-unavailable":
        return ToolFailure(_CLAIM_SHELL_UNAVAILABLE, failure.message)
    if failure.code == "record-shell-rejected":
        return ToolFailure(_CLAIM_SHELL_REJECTED, failure.message)
    return failure


def _require_lease(lease: Any, operation: str) -> None:
    if not isinstance(lease, dict) or not isinstance(lease.get("lease_id"), str):
        raise ToolFailure(_CLAIM_SHELL_UNAVAILABLE, f"{operation} returned no lease")
    interval = lease.get("heartbeat_interval_seconds")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
        # Every lease advertises it from sprintctl 0.10.0 (agentops#2540);
        # a caller must never have to guess how often to heartbeat.
        raise ToolFailure(
            CLAIM_OWNER_INCOMPATIBLE,
            f"{operation} returned a lease without heartbeat_interval_seconds; the "
            f"claim tools need sprintctl {REQUIRED_SPRINTCTL} or later",
        )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_RUN_ID_INPUT: dict[str, Any] = {
    "type": "string",
    "pattern": RUN_ID.pattern,
    "description": "Your own run_id from register_run; the lease is held by this run.",
}
_LEASE_ID_INPUT: dict[str, Any] = {
    "type": "string",
    "pattern": LEASE_ID.pattern,
    "description": "The lease_id claim_work returned (a claim's claim_id is its lease_id).",
}
_CLAIM_KEY_SCHEMA: dict[str, Any] = {
    **IDEMPOTENCY_KEY_SCHEMA,
    "description": (
        "Caller-chosen key, 8-128 characters of A-Z a-z 0-9 . _ : -. Retrying "
        "with the same key and the same arguments is re-evaluated by the "
        "lease owner (resumed: true) -- this is how a restarted worker "
        "resumes its own lease -- not replayed from a stored answer. The "
        "same key with different arguments is refused with "
        "idempotency-conflict."
    ),
}

_CLAIM_WORK_DEFINITION: dict[str, Any] = {
    "name": "claim_work",
    "title": "Claim a work item under a lease",
    "description": (
        "Takes an exclusive lease on one work item (work_id from "
        "list_ready_work/describe_work) for your run. The lease owner sets "
        "the TTL; the answer's lease carries lease_id, generation, "
        "expires_at and heartbeat_interval_seconds -- call heartbeat at "
        "least that often or the lease goes stale. Nothing runs in the "
        "background: expiry is evaluated when someone calls, and a stale "
        "lease may be taken over by another claimant (took_over names the "
        "lease it replaced). A live lease held by someone else is refused "
        "with lease-held. Retrying with the same idempotency_key and "
        "arguments resumes your own lease (resumed: true); a lease that was "
        "taken over is claim-superseded. Other refusals: work-blocked, "
        "work-not-active, work-settled, work-awaiting-verification, "
        "verification-unsupported, maintenance-active, work-not-found. "
        "ttl_seconds is not an argument. Mutating."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "integer",
                "minimum": 1,
                "description": "The work item's integer work_id.",
            },
            "run_id": _RUN_ID_INPUT,
            "idempotency_key": _CLAIM_KEY_SCHEMA,
        },
        "required": ["item_id", "run_id", "idempotency_key"],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}

_HEARTBEAT_DEFINITION: dict[str, Any] = {
    "name": "heartbeat",
    "title": "Heartbeat a lease",
    "description": (
        "Refreshes your live lease and returns it with its new expires_at. "
        "Call it every heartbeat_interval_seconds while you work. A lease "
        "that went stale cannot be refreshed (lease-expired); one taken over "
        "is claim-superseded; one already settled or released is "
        "lease-ended; an item moved off active is work-not-active; a "
        "lease_id that is not yours is lease-not-found. Takes no "
        "idempotency_key: refreshing has no effect a retry could double."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"lease_id": _LEASE_ID_INPUT, "run_id": _RUN_ID_INPUT},
        "required": ["lease_id", "run_id"],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}

_CHECK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": _CHECK_NAME_MAX},
        "status": {"enum": ["passed", "failed"]},
        "ref": {"type": "string", "maxLength": _CHECK_REF_MAX},
    },
    "required": ["name", "status"],
    "additionalProperties": False,
}

_REPORT_OUTCOME_DEFINITION: dict[str, Any] = {
    "name": "report_outcome",
    "title": "Report a leased item's outcome",
    "description": (
        "Reports the outcome of the work you hold a lease on. You report; "
        "the lease owner settles. A succeeded report settles the item only "
        "if it meets the item's verification profile (checked needs at "
        "least one check, all passed, with every required check present); "
        "settlement_effect says what happened: settled, lease-released (a "
        "failed outcome, which releases the lease and leaves the item "
        "active), awaiting-verification, or none. A refused report is kept "
        "as evidence and the item does not change: claim-superseded (the "
        "lease was taken over), lease-expired (stale, nobody took it: "
        "re-present your claim_work to reactivate, then report again), "
        "lease-ended, work-not-active, work-blocked, "
        "verification-unsatisfied. Idempotent: the same idempotency_key "
        "with the same arguments returns the same answer; different "
        "arguments are idempotency-conflict. Mutating."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lease_id": _LEASE_ID_INPUT,
            "run_id": _RUN_ID_INPUT,
            "outcome": {"enum": ["succeeded", "failed"]},
            "summary": {"type": "string", "maxLength": _SUMMARY_MAX},
            "payload": {
                "type": "object",
                "description": "Structured result (e.g. a rate_limit_event for a failed outcome).",
            },
            "checks": {
                "type": "array",
                "items": _CHECK_INPUT_SCHEMA,
                "maxItems": _CHECKS_MAX,
                "description": "Checks you ran, by name, with passed/failed and an optional ref.",
            },
            "idempotency_key": IDEMPOTENCY_KEY_SCHEMA,
        },
        "required": ["lease_id", "run_id", "outcome", "idempotency_key"],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}


# ---------------------------------------------------------------------------
# Argument parsing.  Returns exactly what the owner is sent: the caller's own
# arguments, validated, with nothing added (the owner's request digest covers
# the raw arguments).
# ---------------------------------------------------------------------------


def _refuse_unexpected(tool: str, arguments: Mapping[str, Any], allowed: set[str]) -> None:
    unexpected = set(arguments) - allowed
    if unexpected:
        raise ToolFailure(
            "invalid-arguments", f"{tool} does not accept: {', '.join(sorted(unexpected))}"
        )


def _require_run_id(arguments: Mapping[str, Any]) -> str:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ToolFailure("invalid-arguments", "run_id must be a run_<ULID> handle")
    return run_id


def _require_lease_id(arguments: Mapping[str, Any]) -> str:
    lease_id = arguments.get("lease_id")
    if not isinstance(lease_id, str) or not LEASE_ID.fullmatch(lease_id):
        raise ToolFailure("invalid-arguments", "lease_id must be a lease_<ULID> handle")
    return lease_id


def _parse_claim_work(arguments: dict[str, Any]) -> dict[str, Any]:
    if "ttl_seconds" in arguments:
        raise ToolFailure(
            "invalid-arguments",
            "ttl_seconds is not an argument: the lease owner sets the TTL and "
            "advertises heartbeat_interval_seconds on the lease",
        )
    _refuse_unexpected("claim_work", arguments, {"item_id", "run_id", "idempotency_key"})
    item_id = arguments.get("item_id")
    if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1:
        raise ToolFailure("invalid-arguments", "item_id must be an integer >= 1")
    return {
        "item_id": item_id,
        "run_id": _require_run_id(arguments),
        "idempotency_key": require_key(arguments),
    }


def _parse_heartbeat(arguments: dict[str, Any]) -> dict[str, Any]:
    if "idempotency_key" in arguments:
        raise ToolFailure(
            "invalid-arguments",
            "heartbeat takes no idempotency_key: refreshing a lease is idempotent",
        )
    _refuse_unexpected("heartbeat", arguments, {"lease_id", "run_id"})
    return {"lease_id": _require_lease_id(arguments), "run_id": _require_run_id(arguments)}


def _parse_check(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolFailure("invalid-arguments", "each check must be an object")
    if set(value) - {"name", "status", "ref"}:
        raise ToolFailure("invalid-arguments", "a check has only name, status and ref")
    name = value.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= _CHECK_NAME_MAX:
        raise ToolFailure(
            "invalid-arguments", f"a check name must be 1-{_CHECK_NAME_MAX} characters"
        )
    if value.get("status") not in ("passed", "failed"):
        raise ToolFailure("invalid-arguments", "a check status must be 'passed' or 'failed'")
    if "ref" in value and (
        not isinstance(value["ref"], str) or len(value["ref"]) > _CHECK_REF_MAX
    ):
        raise ToolFailure(
            "invalid-arguments", f"a check ref must be a string of at most {_CHECK_REF_MAX}"
        )
    return value


def _parse_report_outcome(arguments: dict[str, Any]) -> dict[str, Any]:
    _refuse_unexpected(
        "report_outcome",
        arguments,
        {"lease_id", "run_id", "outcome", "summary", "payload", "checks", "idempotency_key"},
    )
    outcome = arguments.get("outcome")
    if outcome not in ("succeeded", "failed"):
        raise ToolFailure("invalid-arguments", "outcome must be 'succeeded' or 'failed'")
    parsed: dict[str, Any] = {
        "lease_id": _require_lease_id(arguments),
        "run_id": _require_run_id(arguments),
        "outcome": outcome,
    }
    if "summary" in arguments:
        summary = arguments["summary"]
        if not isinstance(summary, str) or len(summary) > _SUMMARY_MAX:
            raise ToolFailure(
                "invalid-arguments", f"summary must be a string of at most {_SUMMARY_MAX}"
            )
        parsed["summary"] = summary
    if "payload" in arguments:
        if not isinstance(arguments["payload"], dict):
            raise ToolFailure("invalid-arguments", "payload must be an object")
        parsed["payload"] = arguments["payload"]
    if "checks" in arguments:
        checks = arguments["checks"]
        if not isinstance(checks, list) or len(checks) > _CHECKS_MAX:
            raise ToolFailure(
                "invalid-arguments", f"checks must be a list of at most {_CHECKS_MAX}"
            )
        parsed["checks"] = [_parse_check(check) for check in checks]
    parsed["idempotency_key"] = require_key(arguments)
    return parsed


# ---------------------------------------------------------------------------
# build_toolset
# ---------------------------------------------------------------------------


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    runs: RunRegistry = context.runs
    if isinstance(runs, UnavailableRunRegistry):
        return None
    client = getattr(runs, "shell_client", None)
    if not isinstance(client, RecordShellClient):
        # No durable lease owner reachable (e.g. the in-memory reference
        # registry): list nothing rather than tools that fail every call.
        return None
    owner = SprintctlLeaseOwner(client)

    async def _resolve(parsed: dict[str, Any], forwarded: ForwardedIdentity) -> None:
        await runs.resolve(parsed["run_id"], binding_for(forwarded), forwarded=forwarded)

    async def _run_claim_work(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        await _resolve(parsed, forwarded)
        return await owner.acquire(parsed, forwarded)

    async def _run_heartbeat(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        await _resolve(parsed, forwarded)
        return await owner.heartbeat(parsed, forwarded)

    async def _run_report_outcome(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        await _resolve(parsed, forwarded)
        return await owner.report_outcome(parsed, forwarded)

    return ToolSet(
        name="coordinate",
        tools=(
            ToolSpec(
                name="claim_work",
                bucket="coordinate",
                definition=_CLAIM_WORK_DEFINITION,
                parse=_parse_claim_work,
                run=_run_claim_work,
            ),
            ToolSpec(
                name="heartbeat",
                bucket="coordinate",
                definition=_HEARTBEAT_DEFINITION,
                parse=_parse_heartbeat,
                run=_run_heartbeat,
            ),
            ToolSpec(
                name="report_outcome",
                bucket="coordinate",
                definition=_REPORT_OUTCOME_DEFINITION,
                parse=_parse_report_outcome,
                run=_run_report_outcome,
            ),
        ),
    )
