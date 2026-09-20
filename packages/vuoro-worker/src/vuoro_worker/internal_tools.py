"""The eight-tool internal surface (agentops#2469), host-local only.

Implements docs/plans/2026-09-20-vuoro-at-the-edge.md §4's tool bucketing
(read/coordinate/record/propose) as an in-process Python object the poller
and `mcp_server.py` both call. Every integrity guarantee -- lease expiry,
rate limiting, request metrics, identity resolution -- is the E0 primitive
already landed in `vuoro_service`/`vuoro_evidence` (agentops#2464); nothing
here re-implements them. This module is the wiring, not a second
implementation.

Boundary (TS-16): `propose_effect` records an `EffectIntent`-shaped entry
and returns its id. It never executes anything -- there is deliberately no
effect-apply path here, matching the public surface's own bucket rule
(§3: "every tool on the MCP surface must be classifiable as read,
coordinate, record or propose").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import time
import uuid
from typing import Any, Protocol

from vuoro_evidence.core.model import (
    Claim,
    ClaimType,
    EvidenceItem,
    ValidityBasis,
    ValidityWindow,
)
from vuoro_evidence.core.set_builder import EvidenceSetBuilder, verify_evidence_set
from vuoro_service.identity import Identity, IdentityResolutionError, StaticBearerIdentityResolver
from vuoro_service.lease import Lease, LeaseError, LeaseStore
from vuoro_service.metrics import RequestMetrics
from vuoro_service.rate_limit import RateLimitExceededError, RateLimiter, rate_limit_key


class ToolError(Exception):
    """A typed tool-level rejection. Carries an MCP-shaped error code so
    `mcp_server.py` can project it without guessing at HTTP/JSON-RPC status
    from a bare exception message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class WorkItemSummary:
    subject: str
    title: str
    ready: bool = True


@dataclass(frozen=True)
class WorkItemDetail:
    subject: str
    title: str
    acceptance: tuple[str, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)
    prior_attempts: tuple[str, ...] = ()


class WorkSource(Protocol):
    """Pluggable read of ready work. This surface fronts a catalog it does
    not own -- same posture as `vuoro-client`'s transport-only boundary."""

    def list_ready(self) -> Sequence[WorkItemSummary]: ...

    def describe(self, subject: str) -> WorkItemDetail: ...


class StaticWorkSource:
    """A fixed, in-memory `WorkSource` for tests and for hosts where the
    real catalog adapter is composed by the caller instead."""

    def __init__(self, items: Sequence[WorkItemDetail]) -> None:
        self._by_subject = {item.subject: item for item in items}

    def list_ready(self) -> Sequence[WorkItemSummary]:
        return tuple(
            WorkItemSummary(subject=item.subject, title=item.title)
            for item in self._by_subject.values()
        )

    def describe(self, subject: str) -> WorkItemDetail:
        try:
            return self._by_subject[subject]
        except KeyError as error:
            raise ToolError("resource_not_found", f"no such work item: {subject!r}") from error


@dataclass(frozen=True)
class ToolCallResult:
    """The envelope §4's "what compliance actually costs" asks every list
    and read result to carry: a `result_type` tag, and (for cacheable
    reads) `ttl_ms`/`cache_scope`."""

    result_type: str
    payload: Mapping[str, Any]
    ttl_ms: int | None = None
    cache_scope: str | None = None


#: Deterministic tool ordering for prompt-cache hit rates (§4's compliance
#: list). This is the one place the eight names are enumerated; every other
#: consumer (mcp_server discovery, custom_tools schema) iterates this.
TOOL_ORDER: tuple[str, ...] = (
    "append_evidence",
    "claim_work",
    "complete_work",
    "describe_work",
    "heartbeat",
    "list_ready_work",
    "propose_effect",
    "write_session_note",
)

#: Tool description text, written to satisfy §4's "document durability in
#: the tool description" rule -- the model reading these must be able to
#: reason about lease lifetime without inspecting the schema.
def _tool_description(name: str, *, lease_ttl_seconds: float) -> str:
    ttl_minutes = lease_ttl_seconds / 60.0
    descriptions = {
        "list_ready_work": "List WorkReleases whose dependencies are satisfied. Read-only, cacheable.",
        "describe_work": "Read one WorkRelease's acceptance criteria, provenance and prior attempts. Read-only, cacheable.",
        "claim_work": (
            f"Claim a WorkRelease under a lease. The returned lease_id expires after "
            f"{ttl_minutes:g} minutes without a heartbeat; call heartbeat before it lapses "
            f"or the claim is released for another holder. Safe to call twice with the "
            f"same holder while the lease is still live -- the prior lease is returned."
        ),
        "heartbeat": "Extend a held lease's deadline. Fails if the lease has expired or was reclaimed.",
        "append_evidence": "Append one evidence item to the lease's chained evidence set. Returns its chain position.",
        "write_session_note": "Record a session note against the lease's evidence chain.",
        "propose_effect": (
            "Record a proposed EffectIntent for the homelab reconciler to review. This "
            "never executes anything -- it only queues a described change."
        ),
        "complete_work": "Release the lease as successfully completed and report whether its evidence chain verifies.",
    }
    return descriptions[name]


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class InternalToolServer:
    """Fronts E0's primitives with the eight-tool surface. One instance is
    long-lived per poller process; it is not per-request state."""

    def __init__(
        self,
        *,
        identities: Mapping[str, Identity],
        work_source: WorkSource,
        lease_ttl_seconds: float = 1800.0,
        rate_limit_capacity: int = 30,
        rate_limit_refill_per_second: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._resolver = StaticBearerIdentityResolver(identities)
        self._work_source = work_source
        self._lease_ttl_seconds = lease_ttl_seconds
        self._wall_clock = wall_clock
        self._leases = LeaseStore(clock=clock)
        self._rate_limiter = RateLimiter(
            capacity=rate_limit_capacity,
            refill_per_second=rate_limit_refill_per_second,
            clock=clock,
        )
        self.metrics = RequestMetrics(clock=clock)
        self._evidence: dict[str, EvidenceSetBuilder] = {}
        self._effect_intents: dict[str, Mapping[str, Any]] = {}
        # Idempotency mirror for claim_work only (§4: "safe to call twice
        # with the same holder"). LeaseStore itself has no such replay path
        # by design (a second claim from anyone, including the same holder,
        # is a conflict) -- this wraps that contract rather than loosening
        # it, using only `LeaseStore.is_expired`, which is already public.
        self._last_claim: dict[str, Lease] = {}

    def tool_names(self) -> tuple[str, ...]:
        return TOOL_ORDER

    def tool_description(self, name: str) -> str:
        return _tool_description(name, lease_ttl_seconds=self._lease_ttl_seconds)

    # -- identity / rate limit -------------------------------------------------

    def _resolve(self, token: str) -> Identity:
        try:
            return self._resolver(_BearerRequest(token))
        except IdentityResolutionError as error:
            raise ToolError("identity_required", str(error)) from error

    def _throttle(self, token: str) -> None:
        key = rate_limit_key(token=token, client_ip="poller-local")
        try:
            self._rate_limiter.check(key)
        except RateLimitExceededError as error:
            raise ToolError(
                "rate_limit_exceeded", str(error)
            ) from error

    def _call(self, token: str, fn: Callable[[Identity], ToolCallResult]) -> ToolCallResult:
        stop = self.metrics.start_timer()
        try:
            identity = self._resolve(token)
            self._throttle(token)
            result = fn(identity)
        except ToolError:
            stop(True)
            raise
        except Exception:
            stop(True)
            raise
        stop(False)
        return result

    # -- read --------------------------------------------------------------

    def list_ready_work(self, token: str) -> ToolCallResult:
        return self._call(token, lambda _identity: ToolCallResult(
            result_type="work_list",
            payload={"items": [item.__dict__ for item in self._work_source.list_ready()]},
            ttl_ms=5_000,
            cache_scope="per-token",
        ))

    def describe_work(self, token: str, *, subject: str) -> ToolCallResult:
        def run(_identity: Identity) -> ToolCallResult:
            detail = self._work_source.describe(subject)
            return ToolCallResult(
                result_type="work_detail",
                payload={
                    "subject": detail.subject,
                    "title": detail.title,
                    "acceptance": list(detail.acceptance),
                    "provenance": dict(detail.provenance),
                    "prior_attempts": list(detail.prior_attempts),
                },
                ttl_ms=5_000,
                cache_scope="per-token",
            )
        return self._call(token, run)

    # -- coordinate ----------------------------------------------------------

    def claim_work(self, token: str, *, subject: str) -> ToolCallResult:
        def run(identity: Identity) -> ToolCallResult:
            try:
                lease = self._leases.claim(
                    subject, identity.actor, ttl_seconds=self._lease_ttl_seconds
                )
            except LeaseError as error:
                mirrored = self._last_claim.get(subject)
                if (
                    mirrored is not None
                    and mirrored.holder == identity.actor
                    and self._leases.is_expired(subject) is False
                ):
                    lease = mirrored
                else:
                    raise ToolError("lease_conflict", str(error)) from error
            self._last_claim[subject] = lease
            self._evidence.setdefault(lease.lease_id, EvidenceSetBuilder(lease.lease_id))
            return ToolCallResult(
                result_type="lease",
                payload={
                    "lease_id": lease.lease_id,
                    "subject": lease.subject,
                    "deadline": lease.issued_at + lease.ttl_seconds,
                    "run_manifest_id": None,  # agentops#2479 not landed; see design note
                },
            )
        return self._call(token, run)

    def heartbeat(self, token: str, *, lease_id: str) -> ToolCallResult:
        def run(identity: Identity) -> ToolCallResult:
            try:
                lease = self._leases.heartbeat(lease_id, identity.actor)
            except LeaseError as error:
                raise ToolError("lease_not_current", str(error)) from error
            return ToolCallResult(
                result_type="lease",
                payload={"lease_id": lease.lease_id, "deadline": lease.last_heartbeat_at + lease.ttl_seconds},
            )
        return self._call(token, run)

    def complete_work(self, token: str, *, lease_id: str) -> ToolCallResult:
        def run(identity: Identity) -> ToolCallResult:
            try:
                self._leases.complete(lease_id, identity.actor)
            except LeaseError as error:
                raise ToolError("lease_not_current", str(error)) from error
            builder = self._evidence.get(lease_id)
            chain_ok = True
            if builder is not None:
                verification = verify_evidence_set(builder.build())
                chain_ok = bool(verification)
            return ToolCallResult(
                result_type="completion",
                payload={"lease_id": lease_id, "accepted": True, "evidence_chain_ok": chain_ok},
            )
        return self._call(token, run)

    # -- record ----------------------------------------------------------------

    def append_evidence(
        self, token: str, *, lease_id: str, kind: str, ref: str, claim_type: str | None = None
    ) -> ToolCallResult:
        def run(identity: Identity) -> ToolCallResult:
            builder = self._evidence.setdefault(lease_id, EvidenceSetBuilder(lease_id))
            payload = {"kind": kind, "ref": ref, "collector": identity.actor}
            claims: tuple[Claim, ...] = ()
            if claim_type is not None:
                claims = (Claim(claim_type=ClaimType(claim_type), subject=lease_id),)
            item = EvidenceItem(
                item_id=str(uuid.uuid4()),
                kind=kind,
                ref=ref,
                digest=_digest(payload),
                collector=identity.actor,
                validity=ValidityWindow(basis=ValidityBasis.INDEFINITE, valid_from=self._wall_clock()),
                claims=claims,
            )
            chained = builder.add(item)
            return ToolCallResult(
                result_type="chain_position",
                payload={"item_id": chained.item_id, "chain_seq": chained.chain_seq},
            )
        return self._call(token, run)

    def write_session_note(self, token: str, *, lease_id: str, note: str) -> ToolCallResult:
        return self.append_evidence(token, lease_id=lease_id, kind="session_note", ref=note)

    # -- propose -----------------------------------------------------------

    def propose_effect(
        self, token: str, *, lease_id: str, description: Mapping[str, Any]
    ) -> ToolCallResult:
        def run(_identity: Identity) -> ToolCallResult:
            effect_id = str(uuid.uuid4())
            record = {
                "effect_intent_id": effect_id,
                "lease_id": lease_id,
                "description": dict(description),
                "status": "queued_for_homelab_reconciler",
            }
            self._effect_intents[effect_id] = record
            # No effect scope exists on this surface (TS-16): this stores a
            # description for the homelab reconciler to pick up and act on
            # under its own credentials; it never runs anything itself.
            return ToolCallResult(result_type="effect_intent", payload=dict(record))
        return self._call(token, run)


class _BearerRequest:
    """Minimal request-like shim: `StaticBearerIdentityResolver` only reads
    `request.headers.get("authorization")`, so this avoids constructing a
    real `fastapi.Request` for an in-process call."""

    def __init__(self, token: str) -> None:
        self.headers = {"authorization": f"Bearer {token}"}
