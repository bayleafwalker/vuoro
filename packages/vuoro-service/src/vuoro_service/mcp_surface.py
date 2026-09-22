"""E1: read-only public MCP surface, static bearer (agentops#2465).

Scope, per `docs/plans/2026-09-20-vuoro-at-the-edge.md` section 9 and
agentops#2465: exactly two MCP tools, both read-bucket per the boundary in
section 3 -- ``list_ready_work`` and ``describe_work``. No claim, no
record, no propose tool lives here; those are E2/E3 and are a different
module, added only after a separate item authorizes them.

This module is deliberately self-contained and additive: it does not import
or modify ``app.py`` / ``composition.py`` (the protocol-v1 shell), and
nothing in the rest of the package imports it either. That is what makes
the acceptance line "deleting the connector fully removes public
reachability with no other code change required" true at the source level
too -- the whole public surface is this one file plus its adapter
injection point, and removing the process that serves it (or simply never
mounting it) leaves the rest of vuoro-service untouched.

Auth model (agentops#2470, decided): a single static bearer token, with the
grant shaped as if it were an OAuth scope set (`docs/plans/
2026-09-20-vuoro-at-the-edge.md` section 5, "EffectGrant as scope"). There
is exactly one scope defined today, ``vuoro:work.read`` -- both tools sit in
the same read bucket, so one token gates the whole surface. No
``vuoro:effect.apply`` scope exists anywhere in this module, and none should
be added without a re-decision (section 5, "leaving that scope undefined is
a design decision, not an omission").

Protocol compliance essentials implemented here (edge doc section 4):
one HTTPS POST endpoint (``/mcp``), ``405`` on GET and DELETE, ``Origin``
validation, header-to-body agreement checks on ``MCP-Protocol-Version``,
``Mcp-Method`` and ``Mcp-Name`` rejecting mismatches with JSON-RPC error
``-32020``, ``server/discover`` implemented, ``resultType`` on every
result, ``ttlMs``/``cacheScope`` on every list and read result, deterministic
tool ordering (``TOOL_ORDER``, a fixed tuple -- never derived from dict
iteration or sorted at request time), and stateless handlers throughout
(no session state; a leased handle has no place on this read-only surface).

**Dual-era protocol support, flagged unverified.** Anthropic clients were
reported (as of the source document, mid-2026) to still open sessions with
the legacy ``initialize`` method, while ``2026-07-28`` is described as the
current sessionless revision. This module accepts both: ``initialize`` /
``notifications/initialized`` for legacy clients, and direct
``server/discover`` / ``tools/call`` with no prior handshake for clients on
the newer revision. The exact set of legacy protocol-version strings still
in the wild, and the claim that Anthropic clients still open with
``initialize`` as of mid-2026, is a third-party claim carried over from the
edge doc and is **not independently verified by this implementation** --
re-check the live client matrix before ever dropping ``initialize`` support.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import logging
from typing import Any, Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from vuoro_service import __version__
from vuoro_service.metrics import RequestMetrics
from vuoro_service.rate_limit import RateLimitExceededError, RateLimiter, rate_limit_key


LOGGER = logging.getLogger(__name__)

MCP_PATH = "/mcp"

#: The one scope this surface knows about. Both tools are "read" bucket
#: (edge doc section 3); there is no coordinate/record/propose scope here,
#: and no effect-apply scope anywhere in this file (TS-16).
SCOPE_WORK_READ = "vuoro:work.read"

#: Legacy protocol-version strings a client may still open a session with,
#: via `initialize`. Sourced from the edge doc; UNVERIFIED third-party claim
#: (see module docstring) -- treat this set as dated, not authoritative.
LEGACY_PROTOCOL_VERSIONS: frozenset[str] = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18"}
)
#: The current, sessionless revision this module targets.
CURRENT_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = LEGACY_PROTOCOL_VERSIONS | {
    CURRENT_PROTOCOL_VERSION
}

#: Deterministic tool ordering for prompt-cache hit rates (edge doc section
#: 4). A fixed tuple, not a dict-iteration or sort -- reordering this line
#: is a compatibility change, not a refactor.
TOOL_ORDER: tuple[str, ...] = ("list_ready_work", "describe_work")

_LIST_READY_WORK_TTL_MS = 15_000
_DESCRIBE_WORK_TTL_MS = 15_000

# JSON-RPC 2.0 reserved and MCP-specific error codes.
_JSONRPC_PARSE_ERROR = -32700
_JSONRPC_INVALID_REQUEST = -32600
_JSONRPC_METHOD_NOT_FOUND = -32601
_JSONRPC_INVALID_PARAMS = -32602
_JSONRPC_INTERNAL_ERROR = -32603
_JSONRPC_UNAUTHORIZED = -32001
_JSONRPC_ORIGIN_NOT_ALLOWED = -32002
#: Header-to-body agreement mismatch, per the edge doc's compliance list.
_JSONRPC_HEADER_BODY_MISMATCH = -32020


# ---------------------------------------------------------------------------
# Work-release adapter protocol
#
# vuoro-service does not own domain state (AGENTS.md: "Domain state machines
# ... remain in their owner repositories"). This surface is generic against
# whatever injects a `ReadyWorkSource`; with none configured it serves an
# always-empty, always-not-found source rather than failing to start, so the
# protocol shell is independently testable.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkReleaseSummary:
    work_id: str
    title: str
    repo_id: str
    priority: int
    tier: str | None = None


@dataclass(frozen=True)
class WorkReleaseDetail:
    work_id: str
    title: str
    repo_id: str
    description: str
    acceptance: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    prior_attempts: tuple[str, ...] = ()


class ReadyWorkSource(Protocol):
    def list_ready_work(self) -> Sequence[WorkReleaseSummary]: ...

    def describe_work(self, work_id: str) -> WorkReleaseDetail | None: ...


class _EmptyWorkSource:
    """Safe default: no adapter wired yet, so no domain state to leak."""

    def list_ready_work(self) -> Sequence[WorkReleaseSummary]:
        return ()

    def describe_work(self, work_id: str) -> WorkReleaseDetail | None:
        return None


def _summary_payload(item: WorkReleaseSummary) -> dict[str, Any]:
    return {
        "work_id": item.work_id,
        "title": item.title,
        "repo_id": item.repo_id,
        "priority": item.priority,
        "tier": item.tier,
    }


def _detail_payload(item: WorkReleaseDetail) -> dict[str, Any]:
    return {
        "work_id": item.work_id,
        "title": item.title,
        "repo_id": item.repo_id,
        "description": item.description,
        "acceptance": list(item.acceptance),
        "provenance": list(item.provenance),
        "prior_attempts": list(item.prior_attempts),
    }


# ---------------------------------------------------------------------------
# Tool definitions
#
# Description text carries durability/state claims explicitly, per the edge
# doc: "the model reads the description", not only the schema.
# ---------------------------------------------------------------------------

_TOOL_DEFS: dict[str, dict[str, Any]] = {
    "list_ready_work": {
        "name": "list_ready_work",
        "description": (
            "Lists WorkReleases whose dependencies are satisfied, as of the "
            "moment of this call. Read-only: calling this tool creates no "
            "state, claims nothing, and holds no lease. The result is a "
            "live snapshot, not a reservation -- a WorkRelease listed here "
            "can stop being ready before you act on it, and nothing about "
            "having listed it grants any priority or right to it. Cache the "
            "result for at most `ttlMs` milliseconds (see the result "
            "envelope) and re-call rather than trust an older answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of WorkReleases to return.",
                }
            },
            "additionalProperties": False,
        },
    },
    "describe_work": {
        "name": "describe_work",
        "description": (
            "Returns acceptance criteria, provenance edges and prior-attempt "
            "history for one WorkRelease, identified by `work_id`. "
            "Read-only: calling this tool creates no state, claims nothing, "
            "and holds no lease -- it does not mark the WorkRelease as seen, "
            "in progress, or reserved for you. The answer reflects the "
            "record at read time; cache it for at most `ttlMs` milliseconds "
            "and re-call before acting on it if time has passed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The WorkRelease id to describe.",
                }
            },
            "required": ["work_id"],
            "additionalProperties": False,
        },
    },
}


def _tool_list_payload() -> list[dict[str, Any]]:
    return [_TOOL_DEFS[name] for name in TOOL_ORDER]


# ---------------------------------------------------------------------------
# JSON-RPC envelope helpers
# ---------------------------------------------------------------------------


def _rpc_result(
    *, id_: Any, result: dict[str, Any]
) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(
    *, id_: Any, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "error": {"code": code, "message": message},
    }


def _with_envelope(
    result: dict[str, Any],
    *,
    result_type: str,
    ttl_ms: int | None = None,
    cache_scope: str | None = None,
) -> dict[str, Any]:
    payload = dict(result)
    payload["resultType"] = result_type
    if ttl_ms is not None:
        payload["ttlMs"] = ttl_ms
    if cache_scope is not None:
        payload["cacheScope"] = cache_scope
    return payload


# ---------------------------------------------------------------------------
# Bearer identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BearerGrant:
    scopes: frozenset[str]


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    return token


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_mcp_app(
    *,
    tokens: Mapping[str, BearerGrant],
    work_source: ReadyWorkSource | None = None,
    allowed_origins: frozenset[str] = frozenset(),
    rate_limiter: RateLimiter | None = None,
    metrics: RequestMetrics | None = None,
) -> FastAPI:
    """Build the standalone E1 MCP surface as its own ASGI app.

    Deliberately not merged into `vuoro_service.app.create_app`'s
    protocol-v1 shell: this surface has different auth (static bearer, not
    gateway-signed identity), a different wire protocol (JSON-RPC/MCP, not
    the invocation envelope), and a different exposure story (public,
    behind a connector the operator registers and can delete). Composing it
    as a second ASGI app -- mounted at an internal path or run as a second
    process, at the operator's discretion -- keeps deleting the connector a
    complete removal: nothing in `app.py` or `composition.py` references
    this module.
    """

    work_source = work_source or _EmptyWorkSource()
    metrics = metrics or RequestMetrics()
    app = FastAPI(title="Vuoro MCP read surface", version=__version__)
    app.state.metrics = metrics

    def _resolve_grant(request: Request) -> BearerGrant | None:
        token = _extract_bearer_token(request)
        if token is None:
            return None
        return tokens.get(token)

    def _origin_allowed(request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            # Non-browser, server-to-server MCP clients typically send no
            # Origin header at all; nothing to validate in that case.
            return True
        return origin in allowed_origins

    def _dispatch_rate_limit_key(request: Request) -> str:
        token = _extract_bearer_token(request)
        client_ip = request.client.host if request.client is not None else None
        return rate_limit_key(token=token, client_ip=client_ip)

    def _header_body_mismatches(
        request: Request, body: dict[str, Any]
    ) -> str | None:
        """Return an error message iff a header disagrees with its body
        counterpart when BOTH are present. Absence on either side is not a
        mismatch -- dual-era clients legitimately omit these headers."""

        method = body.get("method") if isinstance(body, dict) else None
        params = body.get("params") if isinstance(body, dict) else None
        params = params if isinstance(params, dict) else {}

        header_protocol_version = request.headers.get("mcp-protocol-version")
        body_protocol_version = params.get("protocolVersion")
        if (
            header_protocol_version is not None
            and isinstance(body_protocol_version, str)
            and header_protocol_version != body_protocol_version
        ):
            return "MCP-Protocol-Version header disagrees with request body"

        header_method = request.headers.get("mcp-method")
        if (
            header_method is not None
            and isinstance(method, str)
            and header_method != method
        ):
            return "Mcp-Method header disagrees with request body"

        header_name = request.headers.get("mcp-name")
        body_name = params.get("name")
        if (
            header_name is not None
            and isinstance(body_name, str)
            and header_name != body_name
        ):
            return "Mcp-Name header disagrees with request body"

        return None

    def _call_tool(name: str, arguments: Any) -> tuple[dict[str, Any], int]:
        """Returns (result_envelope, ttl_ms)."""

        arguments = arguments if isinstance(arguments, dict) else {}
        if name == "list_ready_work":
            limit = arguments.get("limit")
            items = list(work_source.list_ready_work())
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                items = items[:limit]
            structured = {"work_releases": [_summary_payload(item) for item in items]}
            return (
                {
                    "content": [
                        {"type": "text", "text": json.dumps(structured)}
                    ],
                    "structuredContent": structured,
                    "isError": False,
                },
                _LIST_READY_WORK_TTL_MS,
            )
        if name == "describe_work":
            work_id = arguments.get("work_id")
            if not isinstance(work_id, str) or not work_id:
                raise _InvalidParams("describe_work requires a non-empty work_id")
            detail = work_source.describe_work(work_id)
            if detail is None:
                structured = {"work_id": work_id, "found": False}
                return (
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": f"no WorkRelease found for work_id={work_id!r}",
                            }
                        ],
                        "structuredContent": structured,
                        "isError": True,
                    },
                    _DESCRIBE_WORK_TTL_MS,
                )
            structured = _detail_payload(detail)
            return (
                {
                    "content": [
                        {"type": "text", "text": json.dumps(structured)}
                    ],
                    "structuredContent": structured,
                    "isError": False,
                },
                _DESCRIBE_WORK_TTL_MS,
            )
        raise _UnknownTool(name)

    class _InvalidParams(ValueError):
        pass

    class _UnknownTool(ValueError):
        def __init__(self, name: str) -> None:
            super().__init__(f"unknown tool: {name}")
            self.name = name

    @app.post(MCP_PATH, include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        stop_timer = metrics.start_timer()
        response = await _handle(request)
        stop_timer(response.status_code >= 500)
        return response

    async def _handle(request: Request) -> Response:
        if not _origin_allowed(request):
            return JSONResponse(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_ORIGIN_NOT_ALLOWED,
                    message="Origin is not allowed",
                ),
                status_code=403,
            )

        if rate_limiter is not None:
            try:
                rate_limiter.check(_dispatch_rate_limit_key(request))
            except RateLimitExceededError:
                return JSONResponse(
                    _rpc_error(
                        id_=None,
                        code=_JSONRPC_INTERNAL_ERROR,
                        message="rate limit exceeded; slow down and retry later",
                    ),
                    status_code=429,
                )

        grant = _resolve_grant(request)
        if grant is None or SCOPE_WORK_READ not in grant.scopes:
            return JSONResponse(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_UNAUTHORIZED,
                    message="a valid bearer token with vuoro:work.read is required",
                ),
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        raw_body = await request.body()
        try:
            body = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            return JSONResponse(
                _rpc_error(id_=None, code=_JSONRPC_PARSE_ERROR, message="invalid JSON"),
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_INVALID_REQUEST,
                    message="request body must be a JSON-RPC object",
                ),
                status_code=400,
            )

        request_id = body.get("id")
        method = body.get("method")
        if not isinstance(method, str) or not method:
            return JSONResponse(
                _rpc_error(
                    id_=request_id,
                    code=_JSONRPC_INVALID_REQUEST,
                    message="method is required",
                ),
                status_code=400,
            )

        mismatch = _header_body_mismatches(request, body)
        if mismatch is not None:
            return JSONResponse(
                _rpc_error(
                    id_=request_id,
                    code=_JSONRPC_HEADER_BODY_MISMATCH,
                    message=mismatch,
                ),
                status_code=400,
            )

        is_notification = method.startswith("notifications/")
        params = body.get("params")
        params = params if isinstance(params, dict) else {}

        if method == "initialize":
            client_version = params.get("protocolVersion")
            negotiated = (
                client_version
                if isinstance(client_version, str)
                and client_version in SUPPORTED_PROTOCOL_VERSIONS
                else CURRENT_PROTOCOL_VERSION
            )
            result = _with_envelope(
                {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "vuoro-mcp-read-surface",
                        "version": __version__,
                    },
                },
                result_type="initialize-result",
                ttl_ms=0,
                cache_scope="none",
            )
            return JSONResponse(_rpc_result(id_=request_id, result=result))

        if method == "notifications/initialized":
            # A JSON-RPC notification: no response body per spec.
            return Response(status_code=202)

        if method == "server/discover":
            result = _with_envelope(
                {
                    "protocolVersion": CURRENT_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "vuoro-mcp-read-surface",
                        "version": __version__,
                    },
                    "tools": _tool_list_payload(),
                },
                result_type="discover-result",
                ttl_ms=0,
                cache_scope="none",
            )
            return JSONResponse(_rpc_result(id_=request_id, result=result))

        if method == "tools/list":
            result = _with_envelope(
                {"tools": _tool_list_payload()},
                result_type="tools-list-result",
                ttl_ms=0,
                cache_scope="none",
            )
            return JSONResponse(_rpc_result(id_=request_id, result=result))

        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in _TOOL_DEFS:
                return JSONResponse(
                    _rpc_error(
                        id_=request_id,
                        code=_JSONRPC_INVALID_PARAMS,
                        message="unknown or missing tool name",
                    ),
                    status_code=400,
                )
            try:
                tool_result, ttl_ms = _call_tool(name, params.get("arguments"))
            except _InvalidParams as error:
                return JSONResponse(
                    _rpc_error(
                        id_=request_id,
                        code=_JSONRPC_INVALID_PARAMS,
                        message=str(error),
                    ),
                    status_code=400,
                )
            except Exception:
                LOGGER.exception(
                    "MCP tool handler failed", extra={"tool": name}
                )
                return JSONResponse(
                    _rpc_error(
                        id_=request_id,
                        code=_JSONRPC_INTERNAL_ERROR,
                        message="tool handler failed",
                    ),
                    status_code=500,
                )
            result = _with_envelope(
                tool_result,
                result_type="tool-call-result",
                ttl_ms=ttl_ms,
                cache_scope="private",
            )
            return JSONResponse(_rpc_result(id_=request_id, result=result))

        if is_notification:
            return Response(status_code=202)

        return JSONResponse(
            _rpc_error(
                id_=request_id,
                code=_JSONRPC_METHOD_NOT_FOUND,
                message=f"unknown method: {method}",
            ),
            status_code=404,
        )

    @app.get(MCP_PATH, include_in_schema=False)
    async def mcp_get_not_allowed() -> Response:
        return JSONResponse(
            _rpc_error(
                id_=None,
                code=_JSONRPC_INVALID_REQUEST,
                message="GET is not supported on the MCP endpoint",
            ),
            status_code=405,
            headers={"Allow": "POST"},
        )

    @app.delete(MCP_PATH, include_in_schema=False)
    async def mcp_delete_not_allowed() -> Response:
        return JSONResponse(
            _rpc_error(
                id_=None,
                code=_JSONRPC_INVALID_REQUEST,
                message="DELETE is not supported on the MCP endpoint",
            ),
            status_code=405,
            headers={"Allow": "POST"},
        )

    return app
