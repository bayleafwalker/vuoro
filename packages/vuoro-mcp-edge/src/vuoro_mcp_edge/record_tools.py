"""Toolset builder owned by E2 (agentops#2466): register_run, append_evidence, write_session_note -- bucket "record" (vuoro:evidence.record -> work:evidence).

Returns `None` (no tools) until its work item lands.  Only the owning work
item edits this module; see docs/plans/2026-09-26-e2-e3-shared-contract.md.

Design notes (for reviewers; see the E2 final report for the full reasoning):

* The edge holds no credential and no DSN (unchanged).  `SprintctlRecordStore`
  below reaches sprintctl's `work.run.*` / `work.evidence.*` /
  `work.session-note.*` operations through the runtime shell's invoke API,
  the caller's own forwarded assertion, and nothing else -- the same design
  `ShellWorkSource` (work_source.py) uses, reimplemented as a small,
  independent client (`RecordShellClient`) because `work_source.py` is
  frozen/shared and does not expose these operations.
* `SprintctlRecordStore` implements `runs.RunRegistry` exactly as amended
  on 2026-09-26 (shared contract section 3): `register()` and `resolve()`
  take the caller's `forwarded` assertion, without which sprintctl cannot be
  reached at all, and `register()` takes the RunManifest fields the run
  record holds.  Any toolset calling `context.runs` through the protocol
  (E3's `propose_effect` included) calls this store the same way.
* Every sprintctl operation used here is new in sprintctl 0.8.0.  Against an
  older work adapter the shell answers `unknown-operation`; this module
  reports that as `record-owner-incompatible`, never as a generic failure.
* The evidence chain's hash math (`entry_digest`/`link`) is computed here,
  from `vuoro_evidence.core.chain`, never reimplemented -- "E2 must not fork
  a second evidence chain".  Only `chain_seq`/`chain_prev_digest` are needed
  from that computation, so the `EvidenceItem` objects built for it carry
  placeholder `kind`/`ref`/`collector`/`validity` (never read by
  `entry_digest`/`link`, never returned to a caller); the real validity,
  claims and provenance the caller supplied are sent to sprintctl as-is.
* `build_toolset` returns `None` when `context.runs` is the shared
  `UnavailableRunRegistry` placeholder (as `tests/test_toolsets.py`'s
  `test_default_composition_ships_no_write_tools` requires), and builds the
  three tools only once composition.py's one line has wired in a durable
  registry.
* Continuation (agentops#2525, shared contract section 4): `register_run`
  may name a `predecessor_run_id`, and `read_predecessor_context` (bucket
  "read") returns that predecessor's session notes and evidence to the
  successor.  The successor must carry `work:read` and share the
  predecessor's workspace and repository; its principal, client and grant
  may differ.  Continuation transfers context, not authority: the
  successor's own run resolves only to the successor's binding, and the
  predecessor's run never resolves to it.  A store advertises both when
  it sets `supports_continuation` (the in-memory reference), or -- for
  `SprintctlRecordStore` -- when the runtime shell's catalog advertises
  `work.run.predecessor-context-v1` (sprintctl 0.11.0, schema 20), which
  ships together with `work.run.register-v1`'s `predecessor_run_id`.
  Against an older work adapter neither is advertised: capability
  detection, not a version flip, so an older sprintctl keeps working.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx
from vuoro_evidence.core.chain import link
from vuoro_evidence.core.model import EvidenceItem, ValidityBasis, ValidityWindow
from vuoro_evidence.run import ObservedProfile, RunManifest

from .idempotency import IDEMPOTENCY_KEY_SCHEMA, require_key
from .runs import RUN_ID, RunBinding, UnavailableRunRegistry, binding_for
from .toolsets import ToolFailure, ToolSet, ToolSpec, ToolsetContext, WRITE_ANNOTATIONS
from .work_source import ForwardedIdentity

__all__ = ["SprintctlRecordStore", "build_run_registry", "build_toolset"]

_ENV_UPSTREAM_URL = "VUORO_MCP_UPSTREAM_URL"
_ENV_UPSTREAM_TIMEOUT = "VUORO_MCP_UPSTREAM_TIMEOUT_SECONDS"
_DEFAULT_UPSTREAM_URL = "http://127.0.0.1:8080"
_DEFAULT_UPSTREAM_TIMEOUT = 5.0

_PROTOCOL_HEADER = "X-Vuoro-Client-Protocol"
_CLIENT_PROTOCOL = "1"

OPERATION_RUN_REGISTER = "work.run.register-v1"
OPERATION_RUN_RESOLVE = "work.run.resolve-v1"
OPERATION_EVIDENCE_TAIL = "work.evidence.tail-v1"
OPERATION_EVIDENCE_APPEND = "work.evidence.append-v1"
OPERATION_SESSION_NOTE_WRITE = "work.session-note.write-v1"
#: agentops#2525 (sprintctl 0.11.0, schema 20).  Its presence in the shell's
#: catalog is the continuation capability: the same release makes
#: `work.run.register-v1` accept `predecessor_run_id`.
OPERATION_RUN_PREDECESSOR_CONTEXT = "work.run.predecessor-context-v1"
#: The owner's next-page cursors (sprintctl#114), passed through as-is.
_CONTEXT_CURSORS = ("next_after_note_id", "next_after_chain_seq")
#: The paging arguments read_predecessor_context accepts and forwards.
_CONTEXT_PAGING = ("limit", "after_note_id", "after_chain_seq")
CONTEXT_MAX_LIMIT = 500
#: sprintctl#114's page size when the caller sends no limit.
CONTEXT_DEFAULT_LIMIT = 100
_CATALOG_PATH = "/api/catalog/v1"
#: A catalog read is a capability probe on the listing path (tools/list,
#: server/discover, initialize), so it gets its own short bound rather than
#: the upstream call timeout: a shell that hangs during warmup must not
#: stall a connector's initialize.
CATALOG_TIMEOUT_SECONDS = 1.0
#: After a failed catalog read, answer "not available" without asking again
#: for this long: one probe per few seconds, not one per request or per tool.
CATALOG_RETRY_SECONDS = 3.0

_NOT_YOURS = "no run with that id belongs to the caller"

#: The shell's code for an operation its active catalog lacks: here, a work
#: adapter older than the sprintctl release that ships the record bucket.
_UNKNOWN_OPERATION = "unknown-operation"
RECORD_OWNER_INCOMPATIBLE = "record-owner-incompatible"
REQUIRED_SPRINTCTL = "0.8.0"

#: The authority a successor must carry to name a predecessor and to read
#: its context (agentops#253 R4 decision 3).
CONTINUATION_AUTHORITY = "work:read"

_READ_ONLY_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

#: sprintctl's refusal of an append whose chain link is no longer the tail.
CHAIN_CONFLICT = "evidence-chain-conflict"
#: How many times append_evidence re-reads the tail and relinks after a
#: concurrent append took the slot it computed.
CHAIN_ATTEMPTS = 3

#: A placeholder validity window for the internal-only EvidenceItem objects
#: `_placeholder_item` builds: never read by entry_digest/link and never
#: returned to any caller.
_PLACEHOLDER_VALIDITY = ValidityWindow(
    basis=ValidityBasis.INDEFINITE, valid_from=datetime(1970, 1, 1, tzinfo=timezone.utc)
)

def _placeholder_item(
    *, item_id: str, digest: str, chain_seq: int | None, chain_prev_digest: str | None
) -> EvidenceItem:
    """An `EvidenceItem` carrying only the fields `core.chain.entry_digest`
    and `link` actually read (`item_id`, `digest`, `chain_seq`,
    `chain_prev_digest`); see their implementations in
    `vuoro_evidence.core.chain`.  The rest are placeholders: never inspected
    by those two functions, never returned to a caller.  Used so chaining
    calls the real, canonical chain math without this module reconstructing
    the full `ValidityWindow`/`Claim` objects a wire-format item was built
    from, twice, for values that never leave this function.
    """

    return EvidenceItem(
        item_id=item_id,
        kind="_",
        ref="_",
        digest=digest,
        collector="_",
        validity=_PLACEHOLDER_VALIDITY,
        chain_seq=chain_seq,
        chain_prev_digest=chain_prev_digest,
    )


def _mint_item_id(run_id: str, idempotency_key: str) -> str:
    """A stable id for this (run_id, idempotency_key) append.

    Deterministic, not random: a retry of the same idempotency_key must
    resend the same item_id, or its request digest (which includes item_id;
    see append_evidence's arguments) would differ from the one stored under
    that key and a clean replay would come back as a spurious
    idempotency-conflict instead. Uniqueness relies on idempotency_key's own
    per-(workspace, principal, tool) uniqueness at the ledger; it need not
    be secret.
    """

    digest = hashlib.sha256(f"{run_id}:{idempotency_key}".encode()).hexdigest()
    return "evi_" + digest[:32]


def _page_is_consistent(
    page: Mapping[str, int],
    entries: list[Any],
    key: str,
    cursor: Any,
    after_name: str,
) -> bool:
    """Whether one list of a predecessor-context page is what sprintctl#114
    defines (`sprintctl.pg.predecessor_context`): at most `limit` entries
    (default `CONTEXT_DEFAULT_LIMIT`), each with `key` strictly greater than
    the cursor sent (`after_*`, or none when absent -- sprintctl reads
    `> -1`) and strictly ascending; and a non-null next cursor only on a
    full page, equal to the last entry's `key` (sprintctl sets
    `next_after_* = entries[-1][key]` when more rows remain).  Anything else
    is a broken owner: the edge fails closed rather than return a page that
    could skip, repeat or silently truncate rows."""

    limit = page.get("limit", CONTEXT_DEFAULT_LIMIT)
    if len(entries) > limit:
        return False
    previous = page.get(after_name, -1)
    for entry in entries:
        value = entry.get(key) if isinstance(entry, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value <= previous:
            return False
        previous = value
    if cursor is None:
        return True
    return len(entries) == limit and cursor == previous


class RecordShellClient:
    """A minimal `POST /api/invoke/v1` client for the record bucket's own
    operations: no credential, no response cache, the caller's own forwarded
    assertion on every call -- the same design as `ShellWorkSource`
    (work_source.py), which this module cannot use directly because it does
    not expose `work.run.*`/`work.evidence.*`/`work.session-note.*` and is
    frozen/shared (see the module docstring).  Unlike `ShellWorkSource`, this
    client does not cache the catalog or retry on `stale-catalog`: every
    record-bucket operation is `idempotency="not-allowed"` at the envelope
    level (sprintctl.vuoro_adapter's E2 contracts) and manages its own
    idempotency internally, so a bare retry is always safe to leave to the
    caller rather than build in here.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            auth=auth,
            headers={_PROTOCOL_HEADER: _CLIENT_PROTOCOL},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def advertised_operations(
        self, *, timeout: float = CATALOG_TIMEOUT_SECONDS
    ) -> frozenset[str] | None:
        """The operation names the runtime shell's catalog advertises, or
        None when the catalog cannot be read within `timeout` seconds.  The
        catalog is public and caller-independent, so no forwarded assertion
        is sent."""

        try:
            # wait_for bounds the whole exchange, whatever the transport
            # enforces; the httpx timeout also releases the connection.
            response = await asyncio.wait_for(
                self._client.get(_CATALOG_PATH, timeout=timeout), timeout
            )
            body = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            return None
        if not isinstance(body, dict) or not isinstance(body.get("operations"), list):
            return None
        return frozenset(
            operation["name"]
            for operation in body["operations"]
            if isinstance(operation, dict) and isinstance(operation.get("name"), str)
        )

    async def invoke(
        self, operation: str, arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> Any:
        envelope = {
            "schema_version": "invocation/v1",
            "request_id": forwarded.request_id,
            "operation": operation,
            "arguments": arguments,
            "catalog_revision": None,
            "basis_revision": None,
            "idempotency_key": None,
            "repo_id": forwarded.repo_id,
        }
        try:
            response = await self._client.post(
                "/api/invoke/v1", json=envelope, headers=forwarded.headers()
            )
        except httpx.HTTPError as exc:
            raise ToolFailure(
                "record-shell-unavailable", f"{operation} failed: {type(exc).__name__}"
            ) from exc
        return _unwrap(operation, response)


def _unwrap(operation: str, response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError as exc:
        raise ToolFailure(
            "record-shell-unavailable", f"{operation} returned a non-JSON body"
        ) from exc
    if not isinstance(body, dict):
        raise ToolFailure(
            "record-shell-unavailable",
            f"{operation} returned {type(body).__name__}, expected an object",
        )
    if response.status_code >= 400 or body.get("status") != "accepted":
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if code == _UNKNOWN_OPERATION:
            raise ToolFailure(
                RECORD_OWNER_INCOMPATIBLE,
                f"the runtime's work adapter does not provide {operation}; the "
                f"record tools need sprintctl {REQUIRED_SPRINTCTL} or later",
            )
        raise ToolFailure(
            code if isinstance(code, str) and code else "record-shell-rejected",
            message if isinstance(message, str) and message else f"{operation} was not accepted",
        )
    if body.get("operation") not in (None, operation):
        raise ToolFailure(
            "record-shell-unavailable", f"{operation} response names a different operation"
        )
    return body.get("result")


def _expect_mapping(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolFailure("record-shell-unavailable", f"{operation} returned a malformed result")
    return value


class SprintctlRecordStore:
    """Reaches sprintctl's record-bucket operations through the runtime
    shell.  Implements `vuoro_mcp_edge.runs.RunRegistry` (as amended
    2026-09-26), plus the evidence/session-note methods that protocol has no
    room for.
    """

    #: Not a static yes: whether the owner records predecessors and reads
    #: their context back depends on the work adapter the runtime shell
    #: loaded, so it is detected (`continuation_available`).
    supports_continuation = False

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._client = RecordShellClient(
            base_url=base_url, timeout=timeout, transport=transport, auth=auth
        )
        self._continuation: bool | None = None
        self._retry_at = 0.0
        #: The one in-flight catalog read (a task), with the event loop it runs on.
        self._probe: tuple[asyncio.AbstractEventLoop, asyncio.Task[bool]] | None = None
        self.catalog_timeout = CATALOG_TIMEOUT_SECONDS
        self.catalog_retry = CATALOG_RETRY_SECONDS
        self.clock: Callable[[], float] = time.monotonic

    async def continuation_available(self) -> bool:
        """Whether the runtime shell's catalog advertises
        `work.run.predecessor-context-v1` (and so `predecessor_run_id` on
        `work.run.register-v1`).

        * A catalog that was read decides for the process: the edge and the
          work adapter ship in one image, so the adapter cannot change under
          a running edge.
        * Until then there is at most one catalog read in flight per process
          (concurrent callers await the same read), bounded by
          `catalog_timeout` (1 s), not the upstream call timeout.
        * A read that fails or times out answers False, and for
          `catalog_retry` seconds (3 s) every caller gets False without a
          read.  So one request -- a tools/list asking for two definitions,
          or a register_run call after its describe -- costs at most one
          bounded read, and a shell that was not ready yet does not switch
          continuation off for good.
        """

        if self._continuation is not None:
            return self._continuation
        loop = asyncio.get_running_loop()
        if self._probe is None or self._probe[0] is not loop or self._probe[1].done():
            if self.clock() < self._retry_at:
                return False
            # The read runs as its own task and every caller -- the one that
            # started it included -- awaits it shielded, so a caller that is
            # cancelled (a client disconnect) never cancels the read, and a
            # healthy shell is never mistaken for a failed one.
            self._probe = (loop, loop.create_task(self._read_catalog()))
        return await asyncio.shield(self._probe[1])

    async def _read_catalog(self) -> bool:
        operations = await self._client.advertised_operations(timeout=self.catalog_timeout)
        if operations is None:
            self._retry_at = self.clock() + self.catalog_retry
            return False
        self._continuation = OPERATION_RUN_PREDECESSOR_CONTEXT in operations
        return self._continuation

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def shell_client(self) -> RecordShellClient:
        """The runtime-shell client this store calls through.

        `claim_tools` reaches the lease operations over this same client (the
        same upstream, the same edge-proof signer), so the coordinate bucket
        holds no second connection and no credential of its own.
        """

        return self._client

    async def register(
        self,
        binding: RunBinding,
        *,
        idempotency_key: str,
        forwarded: ForwardedIdentity,
        manifest: Mapping[str, Any],
        predecessor_run_id: str | None = None,
    ) -> str:
        if predecessor_run_id is not None and not await self.continuation_available():
            # Never dropped silently: a run registered without the link it
            # asked for would read as a fresh run.
            raise ToolFailure(
                RECORD_OWNER_INCOMPATIBLE,
                "the runtime's work adapter cannot record a run's predecessor",
            )
        arguments = {
            "harness_id": manifest["harness_id"],
            "harness_build": manifest["harness_build"],
            "model_id": manifest["model_id"],
            "recipe_id": manifest["recipe_id"],
            "observed_profile": manifest["observed_profile"],
            "idempotency_key": idempotency_key,
        }
        if predecessor_run_id is not None:
            # Only when set: a request without a predecessor keeps the digest
            # sprintctl stored for it before continuation existed.
            arguments["predecessor_run_id"] = predecessor_run_id
        result = await self._client.invoke(OPERATION_RUN_REGISTER, arguments, forwarded)
        run = _expect_mapping(result, OPERATION_RUN_REGISTER).get("run")
        run = _expect_mapping(run, OPERATION_RUN_REGISTER)
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ToolFailure(
                "record-shell-unavailable", f"{OPERATION_RUN_REGISTER} returned an invalid run_id"
            )
        # The run record IS a RunManifest (agentops#2466 / the E2/E3 shared
        # contract): construct the real object from sprintctl's response --
        # never a second, invented shape -- both to reuse its own validation
        # and so a future caller of this store can use it directly.
        RunManifest(
            run_id=run_id,
            harness_id=run["harness_id"],
            harness_build=run["harness_build"],
            model_id=run["model_id"],
            recipe_id=run["recipe_id"],
            observed_profile=ObservedProfile(
                instruction_digest=run["observed_profile"]["instruction_digest"],
                skill_digests=tuple(
                    (pair["skill_id"], pair["digest"])
                    for pair in run["observed_profile"]["skill_digests"]
                ),
            ),
            grant_ids=tuple(run["grant_ids"]),
            claim_ids=tuple(run["claim_ids"]),
        )
        return run_id

    async def resolve(
        self, run_id: str, caller: RunBinding, *, forwarded: ForwardedIdentity
    ) -> RunBinding:
        if not RUN_ID.fullmatch(run_id or ""):
            raise ToolFailure("run-not-found", _NOT_YOURS)
        result = await self._client.invoke(OPERATION_RUN_RESOLVE, {"run_id": run_id}, forwarded)
        body = _expect_mapping(result, OPERATION_RUN_RESOLVE)
        # The owner echoes the run's full binding; client and grant are
        # absent (None) only for a run minted without an OAuth grant.
        binding = RunBinding(
            principal_id=body.get("principal_id"),
            workspace_id=body.get("workspace_id"),
            repo_id=caller.repo_id,
            client_id=body.get("client_id"),
            grant_id=body.get("grant_id"),
        )
        if binding != caller:
            # sprintctl already scopes by the caller's own identity, so this
            # should never trip; it is the same fail-closed posture
            # InMemoryRunRegistry.resolve takes rather than trusting the
            # remote answer unconditionally.
            raise ToolFailure("run-not-found", _NOT_YOURS)
        return binding

    async def read_predecessor_context(
        self,
        run_id: str,
        *,
        forwarded: ForwardedIdentity,
        page: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """One page of the predecessor's notes and evidence, read by
        sprintctl through the caller's own run (it answers run-not-found for
        any other binding, as `work.run.resolve-v1` does).  `page` carries
        the caller's `limit` / `after_note_id` / `after_chain_seq`; sprintctl
        bounds every page (sprintctl#114: default 100, at most 500 per list)
        and returns the next cursor of each list, null when exhausted."""

        if not await self.continuation_available():
            raise ToolFailure(
                RECORD_OWNER_INCOMPATIBLE,
                "the runtime's work adapter cannot read a predecessor's notes and evidence",
            )
        result = await self._client.invoke(
            OPERATION_RUN_PREDECESSOR_CONTEXT, {"run_id": run_id, **(page or {})}, forwarded
        )
        body = _expect_mapping(result, OPERATION_RUN_PREDECESSOR_CONTEXT)
        predecessor = body.get("predecessor_run_id")
        notes = body.get("session_notes")
        evidence = body.get("evidence")
        cursors = {name: body.get(name) for name in _CONTEXT_CURSORS}
        if (
            body.get("run_id") != run_id
            or not (predecessor is None or (isinstance(predecessor, str) and RUN_ID.fullmatch(predecessor)))
            or not isinstance(notes, list)
            or not isinstance(evidence, list)
            or not all(isinstance(entry, dict) for entry in (*notes, *evidence))
            or not all(
                cursor is None or (isinstance(cursor, int) and not isinstance(cursor, bool) and cursor >= 0)
                for cursor in cursors.values()
            )
            or not _page_is_consistent(page or {}, notes, "note_id", cursors["next_after_note_id"], "after_note_id")
            or not _page_is_consistent(
                page or {}, evidence, "chain_seq", cursors["next_after_chain_seq"], "after_chain_seq"
            )
        ):
            raise ToolFailure(
                "record-shell-unavailable",
                f"{OPERATION_RUN_PREDECESSOR_CONTEXT} returned a malformed result",
            )
        return {
            "predecessor_run_id": predecessor, "session_notes": notes, "evidence": evidence,
            **cursors,
        }

    async def evidence_tail(
        self, run_id: str, *, forwarded: ForwardedIdentity
    ) -> dict[str, Any] | None:
        result = await self._client.invoke(
            OPERATION_EVIDENCE_TAIL, {"run_id": run_id}, forwarded
        )
        item = _expect_mapping(result, OPERATION_EVIDENCE_TAIL).get("item")
        if item is None:
            return None
        return _expect_mapping(item, OPERATION_EVIDENCE_TAIL)

    async def append_evidence(
        self,
        run_id: str,
        *,
        forwarded: ForwardedIdentity,
        idempotency_key: str,
        item_id: str,
        kind: str,
        ref: str,
        digest: str,
        collector: str,
        validity: Mapping[str, Any],
        claims: list[Any],
        provenance: Mapping[str, Any],
        chain_seq: int,
        chain_prev_digest: str | None,
    ) -> dict[str, Any]:
        arguments = {
            "run_id": run_id,
            "item_id": item_id,
            "kind": kind,
            "ref": ref,
            "digest": digest,
            "collector": collector,
            "validity": validity,
            "claims": claims,
            "provenance": provenance,
            "chain_seq": chain_seq,
            "chain_prev_digest": chain_prev_digest,
            "idempotency_key": idempotency_key,
        }
        result = await self._client.invoke(OPERATION_EVIDENCE_APPEND, arguments, forwarded)
        return _expect_mapping(
            _expect_mapping(result, OPERATION_EVIDENCE_APPEND).get("item"),
            OPERATION_EVIDENCE_APPEND,
        )

    async def write_session_note(
        self, run_id: str, *, forwarded: ForwardedIdentity, note: str, idempotency_key: str
    ) -> dict[str, Any]:
        arguments = {"run_id": run_id, "note": note, "idempotency_key": idempotency_key}
        result = await self._client.invoke(OPERATION_SESSION_NOTE_WRITE, arguments, forwarded)
        return _expect_mapping(result, OPERATION_SESSION_NOTE_WRITE)


def build_run_registry(
    env: Mapping[str, str], *, auth: httpx.Auth | None = None
) -> SprintctlRecordStore:
    """The durable RunRegistry composition.py's one line constructs.

    Reads the same upstream URL/timeout environment variables as
    `ShellWorkSource` (composition.py's `ENV_UPSTREAM_URL`/
    `ENV_UPSTREAM_TIMEOUT`): the same runtime shell, reached the same way.
    composition.py already validates these before constructing
    `ShellWorkSource`; a malformed value here (unreachable in that startup
    path) falls back to `ShellWorkSource`'s own defaults rather than raising,
    since this function has no other caller to raise to.
    """

    base_url = (env.get(_ENV_UPSTREAM_URL) or _DEFAULT_UPSTREAM_URL).strip() or (
        _DEFAULT_UPSTREAM_URL
    )
    try:
        timeout = float(env.get(_ENV_UPSTREAM_TIMEOUT, "").strip() or _DEFAULT_UPSTREAM_TIMEOUT)
    except ValueError:
        timeout = _DEFAULT_UPSTREAM_TIMEOUT
    if not 0 < timeout <= 30:
        timeout = _DEFAULT_UPSTREAM_TIMEOUT
    return SprintctlRecordStore(base_url=base_url, timeout=timeout, auth=auth)


# ---------------------------------------------------------------------------
# register_run
# ---------------------------------------------------------------------------

_OBSERVED_PROFILE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "instruction_digest": {
            "type": "string",
            "minLength": 1,
            "description": "Digest of the instruction text this run actually saw.",
        },
        "skill_digests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "minLength": 1},
                    "digest": {"type": "string", "minLength": 1},
                },
                "required": ["skill_id", "digest"],
                "additionalProperties": False,
            },
            "default": [],
            "description": "(skill id, digest) pairs, in the order observed.",
        },
    },
    "required": ["instruction_digest", "skill_digests"],
    "additionalProperties": False,
}

_PREDECESSOR_RUN_ID_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": RUN_ID.pattern,
    "description": (
        "The run this one continues. Needs work:read, and the predecessor must "
        "be in your workspace and repository; it may belong to another "
        "principal, client or grant. Continuation transfers context, not "
        "authority."
    ),
}

_REGISTER_RUN_DEFINITION: dict[str, Any] = {
    "name": "register_run",
    "title": "Register a run",
    "description": (
        "Mints a run handle (run_id) bound to your identity: principal, "
        "workspace and the one repository your assertion authorizes. "
        "harness_id, harness_build, model_id, recipe_id and observed_profile "
        "compose the run's manifest -- what produced it -- addressable "
        "later by run_id from append_evidence and write_session_note. "
        "Idempotent: the same idempotency_key with the same arguments "
        "returns the same run_id and mints nothing new; the same key with "
        "different arguments is refused with idempotency-conflict. "
        "Mutating: creates a run record."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "harness_id": {
                "type": "string",
                "minLength": 1,
                "description": "Which harness/CLI produced this run (e.g. claude-code).",
            },
            "harness_build": {
                "type": "string",
                "minLength": 1,
                "description": "Which build of the harness (version, commit, image tag).",
            },
            "model_id": {"type": "string", "minLength": 1},
            "recipe_id": {
                "type": "string",
                "minLength": 1,
                "description": "The recipe/recipe revision identifier this run followed.",
            },
            "observed_profile": _OBSERVED_PROFILE_INPUT_SCHEMA,
            "idempotency_key": IDEMPOTENCY_KEY_SCHEMA,
        },
        "required": [
            "harness_id", "harness_build", "model_id", "recipe_id",
            "observed_profile", "idempotency_key",
        ],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}


def _parse_observed_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolFailure("invalid-arguments", "observed_profile must be an object")
    instruction_digest = value.get("instruction_digest")
    if not isinstance(instruction_digest, str) or not instruction_digest:
        raise ToolFailure(
            "invalid-arguments", "observed_profile.instruction_digest must be a non-empty string"
        )
    raw_digests = value.get("skill_digests", [])
    if not isinstance(raw_digests, list):
        raise ToolFailure("invalid-arguments", "observed_profile.skill_digests must be an array")
    skill_digests: list[dict[str, str]] = []
    for pair in raw_digests:
        if (
            not isinstance(pair, dict)
            or not isinstance(pair.get("skill_id"), str) or not pair["skill_id"]
            or not isinstance(pair.get("digest"), str) or not pair["digest"]
        ):
            raise ToolFailure(
                "invalid-arguments",
                "observed_profile.skill_digests entries need non-empty skill_id and digest",
            )
        skill_digests.append({"skill_id": pair["skill_id"], "digest": pair["digest"]})
    return {"instruction_digest": instruction_digest, "skill_digests": skill_digests}


def _require_str(arguments: Mapping[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value:
        raise ToolFailure("invalid-arguments", f"{field} must be a non-empty string")
    return value


def _register_run_definition(*, continuation: bool) -> dict[str, Any]:
    if not continuation:
        return _REGISTER_RUN_DEFINITION
    definition = dict(_REGISTER_RUN_DEFINITION)
    definition["description"] = definition["description"] + (
        " predecessor_run_id (optional) links this run to the run it "
        "continues, whose session notes and evidence read_predecessor_context "
        "then returns; it grants nothing the predecessor could do. An "
        "ineligible predecessor is a tool error with code "
        "predecessor-not-eligible."
    )
    schema = dict(definition["inputSchema"])
    schema["properties"] = {
        **schema["properties"], "predecessor_run_id": _PREDECESSOR_RUN_ID_SCHEMA,
    }
    definition["inputSchema"] = schema
    return definition


def _parse_register_run(
    arguments: dict[str, Any], *, continuation: bool = False
) -> dict[str, Any]:
    parsed = {
        field: _require_str(arguments, field)
        for field in ("harness_id", "harness_build", "model_id", "recipe_id")
    }
    parsed["observed_profile"] = _parse_observed_profile(arguments.get("observed_profile"))
    parsed["idempotency_key"] = require_key(arguments)
    accepted = {
        "harness_id", "harness_build", "model_id", "recipe_id",
        "observed_profile", "idempotency_key",
    }
    if continuation:
        accepted.add("predecessor_run_id")
    unexpected = set(arguments) - accepted
    if unexpected:
        raise ToolFailure(
            "invalid-arguments", f"register_run does not accept: {', '.join(sorted(unexpected))}"
        )
    predecessor_run_id = arguments.get("predecessor_run_id")
    if predecessor_run_id is not None and (
        not isinstance(predecessor_run_id, str) or not RUN_ID.fullmatch(predecessor_run_id)
    ):
        raise ToolFailure("invalid-arguments", "predecessor_run_id must be a run_<ULID> handle")
    parsed["predecessor_run_id"] = predecessor_run_id
    return parsed


# ---------------------------------------------------------------------------
# append_evidence
# ---------------------------------------------------------------------------

_VALIDITY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "basis": {"enum": ["indefinite", "bounded", "until_inputs_change"]},
        "valid_from": {"type": "string", "minLength": 1},
        "valid_until": {"type": ["string", "null"], "default": None},
        "component_digests": {"type": "object", "default": {}},
    },
    "required": ["basis", "valid_from"],
    "additionalProperties": False,
}

_CLAIM_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claim_type": {
            "enum": [
                "target_stale", "precondition_refused", "capability_unavailable",
                "evidence_persisted", "effect_completed", "effect_failed",
                "effect_uncertain", "effect_not_invoked", "observation",
            ]
        },
        "subject": {"type": "string", "minLength": 1},
        "grant_id": {"type": ["string", "null"], "default": None},
        "freshness": {
            "type": ["object", "null"],
            "properties": {
                "scope": {"type": "string", "minLength": 1},
                "position": {"type": "integer"},
            },
            "required": ["scope", "position"],
            "additionalProperties": False,
            "default": None,
        },
        "confirms": {"type": ["boolean", "null"], "default": None},
        "detail": {"type": "object", "default": {}},
    },
    "required": ["claim_type", "subject"],
    "additionalProperties": False,
}

_APPEND_EVIDENCE_DEFINITION: dict[str, Any] = {
    "name": "append_evidence",
    "title": "Append evidence to a run",
    "description": (
        "Appends one evidence item to run_id's hash-chained evidence "
        "history (a run from register_run; an unknown or someone else's "
        "run_id is a tool error with code run-not-found). kind is your own "
        "label for what this evidence is; ref and digest are where it lives "
        "and its content digest; collector names what produced it; validity "
        "states how long it holds. The chain link is computed here, not by "
        "you: call this once per item and it is appended at the run's "
        "current tail. If other appends to the same run keep taking the "
        "tail, it re-reads and relinks a bounded number of times, then "
        "fails with evidence-chain-conflict; retrying with the same "
        "idempotency_key is safe. Idempotent: the same idempotency_key with the "
        "same arguments replays the same append with no second item; the "
        "same key with different arguments is refused with "
        "idempotency-conflict. Mutating: appends an immutable evidence item."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "pattern": RUN_ID.pattern,
                "description": "A run_id from register_run.",
            },
            "kind": {"type": "string", "minLength": 1},
            "ref": {"type": "string", "minLength": 1},
            "digest": {"type": "string", "minLength": 1},
            "collector": {"type": "string", "minLength": 1},
            "validity": _VALIDITY_INPUT_SCHEMA,
            "claims": {"type": "array", "items": _CLAIM_INPUT_SCHEMA, "default": []},
            "provenance": {"type": "object", "default": {}},
            "idempotency_key": IDEMPOTENCY_KEY_SCHEMA,
        },
        "required": [
            "run_id", "kind", "ref", "digest", "collector", "validity", "idempotency_key",
        ],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}


def _parse_validity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolFailure("invalid-arguments", "validity must be an object")
    basis = value.get("basis")
    if basis not in ("indefinite", "bounded", "until_inputs_change"):
        raise ToolFailure("invalid-arguments", "validity.basis is outside the contract vocabulary")
    valid_from = value.get("valid_from")
    if not isinstance(valid_from, str) or not valid_from:
        raise ToolFailure("invalid-arguments", "validity.valid_from must be a non-empty string")
    valid_until = value.get("valid_until")
    if valid_until is not None and not isinstance(valid_until, str):
        raise ToolFailure("invalid-arguments", "validity.valid_until must be a string or null")
    component_digests = value.get("component_digests", {})
    if not isinstance(component_digests, dict):
        raise ToolFailure("invalid-arguments", "validity.component_digests must be an object")
    return {
        "basis": basis,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "component_digests": component_digests,
    }


def _parse_claim(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolFailure("invalid-arguments", "each claim must be an object")
    claim_type = value.get("claim_type")
    if not isinstance(claim_type, str) or not claim_type:
        raise ToolFailure("invalid-arguments", "claim.claim_type must be a non-empty string")
    subject = value.get("subject")
    if not isinstance(subject, str) or not subject:
        raise ToolFailure("invalid-arguments", "claim.subject must be a non-empty string")
    freshness = value.get("freshness")
    if freshness is not None:
        if (
            not isinstance(freshness, dict)
            or not isinstance(freshness.get("scope"), str) or not freshness["scope"]
            or not isinstance(freshness.get("position"), int)
            or isinstance(freshness.get("position"), bool)
        ):
            raise ToolFailure(
                "invalid-arguments", "claim.freshness must be null or {scope, position}"
            )
        freshness = {"scope": freshness["scope"], "position": freshness["position"]}
    confirms = value.get("confirms")
    if confirms is not None and not isinstance(confirms, bool):
        raise ToolFailure("invalid-arguments", "claim.confirms must be a boolean or null")
    grant_id = value.get("grant_id")
    if grant_id is not None and not isinstance(grant_id, str):
        raise ToolFailure("invalid-arguments", "claim.grant_id must be a string or null")
    detail = value.get("detail", {})
    if not isinstance(detail, dict):
        raise ToolFailure("invalid-arguments", "claim.detail must be an object")
    return {
        "claim_type": claim_type,
        "subject": subject,
        "grant_id": grant_id,
        "freshness": freshness,
        "confirms": confirms,
        "detail": detail,
    }


def _parse_append_evidence(arguments: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "run_id", "kind", "ref", "digest", "collector", "validity", "claims",
        "provenance", "idempotency_key",
    }
    unexpected = set(arguments) - expected
    if unexpected:
        raise ToolFailure(
            "invalid-arguments", f"append_evidence does not accept: {', '.join(sorted(unexpected))}"
        )
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ToolFailure("invalid-arguments", "run_id must be a run_<ULID> handle")
    parsed = {
        "run_id": run_id,
        "kind": _require_str(arguments, "kind"),
        "ref": _require_str(arguments, "ref"),
        "digest": _require_str(arguments, "digest"),
        "collector": _require_str(arguments, "collector"),
        "validity": _parse_validity(arguments.get("validity")),
        "idempotency_key": require_key(arguments),
    }
    raw_claims = arguments.get("claims", [])
    if not isinstance(raw_claims, list):
        raise ToolFailure("invalid-arguments", "claims must be an array")
    parsed["claims"] = [_parse_claim(claim) for claim in raw_claims]
    provenance = arguments.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ToolFailure("invalid-arguments", "provenance must be an object")
    parsed["provenance"] = provenance
    return parsed


# ---------------------------------------------------------------------------
# write_session_note
# ---------------------------------------------------------------------------

_WRITE_SESSION_NOTE_DEFINITION: dict[str, Any] = {
    "name": "write_session_note",
    "title": "Write a session note",
    "description": (
        "Records one free-text note against run_id, unchained (not part of "
        "the evidence chain). An unknown or someone else's run_id is a tool "
        "error with code run-not-found. Idempotent: the same "
        "idempotency_key with the same note replays the same note_id with "
        "no second note; the same key with a different note is refused "
        "with idempotency-conflict. Mutating: creates a note."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "pattern": RUN_ID.pattern,
                "description": "A run_id from register_run.",
            },
            "note": {"type": "string", "minLength": 1},
            "idempotency_key": IDEMPOTENCY_KEY_SCHEMA,
        },
        "required": ["run_id", "note", "idempotency_key"],
        "additionalProperties": False,
    },
    "annotations": WRITE_ANNOTATIONS,
}


def _parse_write_session_note(arguments: dict[str, Any]) -> dict[str, Any]:
    unexpected = set(arguments) - {"run_id", "note", "idempotency_key"}
    if unexpected:
        raise ToolFailure(
            "invalid-arguments",
            f"write_session_note does not accept: {', '.join(sorted(unexpected))}",
        )
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ToolFailure("invalid-arguments", "run_id must be a run_<ULID> handle")
    return {
        "run_id": run_id,
        "note": _require_str(arguments, "note"),
        "idempotency_key": require_key(arguments),
    }


# ---------------------------------------------------------------------------
# read_predecessor_context
# ---------------------------------------------------------------------------

_READ_PREDECESSOR_CONTEXT_DEFINITION: dict[str, Any] = {
    "name": "read_predecessor_context",
    "title": "Read a predecessor run's context",
    "description": (
        "Returns the session notes and evidence of the run that run_id "
        "continues (its predecessor_run_id from register_run), so a "
        "successor -- possibly another harness, principal or grant -- picks "
        "up where it stopped. run_id is your own run; an unknown or someone "
        "else's run_id is a tool error with code run-not-found. A run with "
        "no predecessor returns predecessor_run_id null and empty lists. "
        "Results are paged: each call returns at most limit notes and limit "
        "evidence items (default 100, at most 500); when next_after_note_id "
        "or next_after_chain_seq is not null, call again passing it as "
        "after_note_id / after_chain_seq for the rest of that list. "
        "Read-only: it grants nothing the predecessor could do, and the "
        "predecessor's run_id stays unusable for your writes."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "pattern": RUN_ID.pattern,
                "description": "Your own run_id, registered with predecessor_run_id.",
            },
            "limit": {
                "type": "integer", "minimum": 1, "maximum": CONTEXT_MAX_LIMIT,
                "description": "At most this many notes and this many evidence items (default 100).",
            },
            "after_note_id": {
                "type": "integer", "minimum": 0,
                "description": "next_after_note_id from the previous page.",
            },
            "after_chain_seq": {
                "type": "integer", "minimum": 0,
                "description": "next_after_chain_seq from the previous page.",
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
    "annotations": _READ_ONLY_ANNOTATIONS,
}


def _parse_read_predecessor_context(arguments: dict[str, Any]) -> dict[str, Any]:
    unexpected = set(arguments) - {"run_id", *_CONTEXT_PAGING}
    if unexpected:
        raise ToolFailure(
            "invalid-arguments",
            f"read_predecessor_context does not accept: {', '.join(sorted(unexpected))}",
        )
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ToolFailure("invalid-arguments", "run_id must be a run_<ULID> handle")
    page: dict[str, int] = {}
    for name in _CONTEXT_PAGING:
        value = arguments.get(name)
        if value is None:
            continue
        low, high = (1, CONTEXT_MAX_LIMIT) if name == "limit" else (0, None)
        if (
            isinstance(value, bool) or not isinstance(value, int)
            or value < low or (high is not None and value > high)
        ):
            bound = f"between {low} and {high}" if high is not None else f"at least {low}"
            raise ToolFailure("invalid-arguments", f"{name} must be an integer {bound}")
        page[name] = value
    return {"run_id": run_id, "page": page}


def _require_continuation_authority(forwarded: ForwardedIdentity) -> None:
    identity = forwarded.identity
    if identity is None or CONTINUATION_AUTHORITY not in identity.authorities:
        raise ToolFailure(
            "authority-required",
            f"continuing a run needs the {CONTINUATION_AUTHORITY} authority",
        )


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    store = context.runs
    if isinstance(store, UnavailableRunRegistry):
        # No durable registry wired in (e.g. a bare ToolsetContext built for
        # a test, or an environment that has not composed one yet): this
        # bucket has nothing it could serve durably, so it advertises
        # nothing rather than tools that would fail every call.
        return None
    # Static for a store that always serves continuation (the in-memory
    # reference); detected per request for one that may or may not, by what
    # its owner advertises (`SprintctlRecordStore.continuation_available`).
    static_continuation = bool(getattr(store, "supports_continuation", False))
    detect = None if static_continuation else getattr(store, "continuation_available", None)
    continuation = static_continuation or detect is not None

    async def _run_register_run(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        binding = binding_for(forwarded)
        if parsed["predecessor_run_id"] is not None and detect is not None and not await detect():
            # Listed without predecessor_run_id right now: refused exactly as
            # a store without continuation refuses it (_parse_register_run).
            raise ToolFailure(
                "invalid-arguments", "register_run does not accept: predecessor_run_id"
            )
        if parsed["predecessor_run_id"] is not None:
            _require_continuation_authority(forwarded)
        run_id = await store.register(
            binding,
            idempotency_key=parsed["idempotency_key"],
            forwarded=forwarded,
            manifest={
                "harness_id": parsed["harness_id"],
                "harness_build": parsed["harness_build"],
                "model_id": parsed["model_id"],
                "recipe_id": parsed["recipe_id"],
                "observed_profile": parsed["observed_profile"],
            },
            predecessor_run_id=parsed["predecessor_run_id"],
        )
        return {"run_id": run_id}

    async def _run_append_evidence(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        binding = binding_for(forwarded)
        await store.resolve(parsed["run_id"], binding, forwarded=forwarded)
        item_id = _mint_item_id(parsed["run_id"], parsed["idempotency_key"])
        next_item = _placeholder_item(
            item_id=item_id, digest=parsed["digest"], chain_seq=None, chain_prev_digest=None
        )
        # The tail read and the append are separate calls, so a concurrent
        # append can take the slot linked here; sprintctl refuses the stale
        # link and this relinks against the new tail, a bounded number of
        # times.
        for attempt in range(1, CHAIN_ATTEMPTS + 1):
            tail = await store.evidence_tail(parsed["run_id"], forwarded=forwarded)
            if tail is None:
                linked = link((), next_item)
            else:
                tail_item = _placeholder_item(
                    item_id=tail["item_id"],
                    digest=tail["digest"],
                    chain_seq=tail["chain_seq"],
                    chain_prev_digest=tail["chain_prev_digest"],
                )
                linked = link((tail_item,), next_item)
            try:
                return await store.append_evidence(
                    parsed["run_id"],
                    forwarded=forwarded,
                    idempotency_key=parsed["idempotency_key"],
                    item_id=item_id,
                    kind=parsed["kind"],
                    ref=parsed["ref"],
                    digest=parsed["digest"],
                    collector=parsed["collector"],
                    validity=parsed["validity"],
                    claims=parsed["claims"],
                    provenance=parsed["provenance"],
                    chain_seq=linked.chain_seq,
                    chain_prev_digest=linked.chain_prev_digest,
                )
            except ToolFailure as failure:
                if failure.code != CHAIN_CONFLICT or attempt == CHAIN_ATTEMPTS:
                    raise
        raise AssertionError("unreachable")

    async def _run_write_session_note(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        binding = binding_for(forwarded)
        await store.resolve(parsed["run_id"], binding, forwarded=forwarded)
        return await store.write_session_note(
            parsed["run_id"],
            forwarded=forwarded,
            note=parsed["note"],
            idempotency_key=parsed["idempotency_key"],
        )

    async def _run_read_predecessor_context(
        parsed: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        # The bucket already demands work:read; checked again here so the
        # rule holds wherever this handler is reached from.
        _require_continuation_authority(forwarded)
        binding = binding_for(forwarded)
        # The caller's own run, by its exact binding: the successor reads
        # through its own handle, never by presenting the predecessor's.
        await store.resolve(parsed["run_id"], binding, forwarded=forwarded)
        continued = await store.read_predecessor_context(
            parsed["run_id"], forwarded=forwarded, page=parsed["page"]
        )
        return {
            "run_id": parsed["run_id"],
            "predecessor_run_id": continued.get("predecessor_run_id"),
            "session_notes": list(continued.get("session_notes") or []),
            "evidence": list(continued.get("evidence") or []),
            # Null when the list is exhausted (always, for an unpaged store).
            "next_after_note_id": continued.get("next_after_note_id"),
            "next_after_chain_seq": continued.get("next_after_chain_seq"),
        }

    register_definition = _register_run_definition(continuation=continuation)
    describe_register = None
    describe_read = None
    if detect is not None:

        async def describe_register() -> Mapping[str, Any]:
            return register_definition if await detect() else _REGISTER_RUN_DEFINITION

        async def describe_read() -> Mapping[str, Any] | None:
            return _READ_PREDECESSOR_CONTEXT_DEFINITION if await detect() else None

    continuation_tools: tuple[ToolSpec, ...] = ()
    if continuation:
        continuation_tools = (
            ToolSpec(
                name="read_predecessor_context",
                bucket="read",
                definition=_READ_PREDECESSOR_CONTEXT_DEFINITION,
                parse=_parse_read_predecessor_context,
                run=_run_read_predecessor_context,
                describe=describe_read,
            ),
        )

    return ToolSet(
        name="record",
        tools=(
            ToolSpec(
                name="register_run",
                bucket="record",
                definition=register_definition,
                parse=lambda arguments: _parse_register_run(
                    arguments, continuation=continuation
                ),
                run=_run_register_run,
                describe=describe_register,
            ),
            ToolSpec(
                name="append_evidence",
                bucket="record",
                definition=_APPEND_EVIDENCE_DEFINITION,
                parse=_parse_append_evidence,
                run=_run_append_evidence,
            ),
            ToolSpec(
                name="write_session_note",
                bucket="record",
                definition=_WRITE_SESSION_NOTE_DEFINITION,
                parse=_parse_write_session_note,
                run=_run_write_session_note,
            ),
            *continuation_tools,
        ),
    )
