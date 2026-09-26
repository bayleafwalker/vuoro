"""The vuoro MCP protocol server: read tools over gateway assertions.

`vuoro-service mcp-serve` runs this app.  It is a hand-written JSON-RPC MCP
server on FastAPI (no MCP SDK) with one stateless endpoint, ``POST /mcp``,
answering plain JSON.  There is no SSE stream, no ``Mcp-Session-Id`` and no
per-client state; any instance can serve any request.

Auth: the vuoro.cloud gateway owns OAuth and the connector challenge.  It
forwards each request with a short-lived ``X-Vuoro-Identity`` assertion
(Ed25519 JWT, ``aud`` ``vuoro-service``, 30 s lifetime) and ``X-Request-Id``.
This server verifies that assertion with the runtime shell's own verifier
and trust configuration (`vuoro_service.gateway_identity`), and a request
without a valid one gets HTTP 401 with JSON-RPC error ``-32001``.  The
process holds no credential: upstream calls forward the same assertion to
the runtime shell on localhost, which verifies it again.

Tools are table-driven: `TOOL_SCOPES` classifies every callable tool into a
scope bucket, and `SCOPE_AUTHORITIES` names the authority each bucket
requires.  A tool without a row cannot be listed or called.

JSON-RPC errors travel as HTTP 200 with an error body; tool failures
(including bad arguments and unknown tools) are tool results with
``isError: true``.  HTTP status carries only transport-level refusals: 401
(assertion), 400 (unsupported ``MCP-Protocol-Version``), 413 (body over
64 KiB), 415 (not JSON), 405 (GET/DELETE), 202 (notifications and responses).

Protocol handling (edge plan section 4): ``405`` on GET and DELETE,
header-to-body agreement on ``MCP-Protocol-Version``, ``Mcp-Method`` and
``Mcp-Name`` (mismatch is ``-32020``), ``server/discover``, ``resultType:
"complete"`` on every result, ``ttlMs``/``cacheScope`` (``"private"``) on list
and read results, and a fixed
tool order.  Both protocol eras work: legacy clients open with
``initialize`` / ``notifications/initialized``; clients on the 2026-07-28
revision call ``server/discover`` / ``tools/call`` with no handshake.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from vuoro_service.identity import Identity, IdentityResolutionError

from .errors import WorkSourceUnavailable, client_error, is_not_found
from .toolsets import BUCKET_AUTHORITIES, ToolFailure, ToolSet, merge_toolsets
from .work_source import ForwardedIdentity, ShellWorkSource

__all__ = [
    "CURRENT_PROTOCOL_VERSION",
    "LEGACY_PROTOCOL_VERSIONS",
    "MCP_PATH",
    "SCOPE_AUTHORITIES",
    "TOOL_ORDER",
    "TOOL_SCOPES",
    "create_edge_app",
]

LOGGER = logging.getLogger(__name__)

try:
    __version__ = version("vuoro-mcp-edge")
except PackageNotFoundError:  # pragma: no cover - source checkout without metadata
    __version__ = "0+unknown"

MCP_PATH = "/mcp"
SERVER_NAME = "vuoro"

#: Legacy protocol versions a client may open a session with via
#: `initialize`.  The client matrix is third-party and dated July 2026;
#: re-check it before dropping `initialize` support.
LEGACY_PROTOCOL_VERSIONS: frozenset[str] = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
)
#: The current, sessionless revision.
CURRENT_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = LEGACY_PROTOCOL_VERSIONS | {
    CURRENT_PROTOCOL_VERSION
}

#: Every callable tool and its scope bucket (edge plan section 3: read,
#: coordinate, record, propose).  A new tool is one row here plus its
#: definition and handler.
TOOL_SCOPES: dict[str, str] = {
    "list_ready_work": "read",
    "describe_work": "read",
}
#: The assertion authority each bucket requires.
SCOPE_AUTHORITIES: dict[str, str] = {
    "read": "work:read",
}

#: Deterministic tool order for prompt-cache hit rates.  A fixed tuple:
#: reordering it is a compatibility change, not a refactor.
TOOL_ORDER: tuple[str, ...] = ("list_ready_work", "describe_work")

LIST_LIMIT_MAX = 50
_TOOL_TTL_MS = 15_000

_JSONRPC_PARSE_ERROR = -32700
_JSONRPC_INVALID_REQUEST = -32600
_JSONRPC_METHOD_NOT_FOUND = -32601
_JSONRPC_UNAUTHORIZED = -32001
_JSONRPC_HEADER_BODY_MISMATCH = -32020


# ---------------------------------------------------------------------------
# Tool definitions.  The description carries the state claims explicitly,
# because the model reads the description, not only the schema.
# ---------------------------------------------------------------------------

_READ_ONLY_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_TOOL_DEFS: dict[str, dict[str, Any]] = {
    "list_ready_work": {
        "name": "list_ready_work",
        "title": "List ready work",
        "description": (
            "Lists the workspace's ready work items: status pending and not "
            "blocked by any unresolved dependency. Active, blocked and done "
            "items are not included. Order is the tracker's next-work order "
            "(priority 1 highest, unset last, then creation order). Each item "
            "has exactly work_id, title, priority (1-9 or null), status "
            "(always pending here), blocked (always false here) and "
            "updated_at; the result also carries as_of, the time of the read. "
            "Read-only: calling this creates no state, claims nothing and "
            "holds no lease. The answer is a live snapshot, not a "
            "reservation; an item listed here can stop being ready before you "
            "act on it. Cache it for at most ttlMs milliseconds. A failure is "
            "reported as a tool error, never as an empty list: an empty list "
            "means there is no ready work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": LIST_LIMIT_MAX,
                    "default": LIST_LIMIT_MAX,
                    "description": "Maximum number of items to return.",
                }
            },
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    "describe_work": {
        "name": "describe_work",
        "title": "Describe a work item",
        "description": (
            "Returns one work item by its integer work_id, in any status: "
            "title, priority, status, blocked, blocked_by (the work_ids of "
            "unresolved blockers), resolution, created_at and updated_at. "
            "Read-only: it does not mark the item as seen, in progress or "
            "reserved for you. The answer reflects the record at read time; "
            "cache it for at most ttlMs milliseconds. An unknown work_id is a "
            "tool error with code item-not-found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The work item id, as listed by list_ready_work.",
                }
            },
            "required": ["work_id"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
}

#: The request body cap, checked before parsing.
MAX_BODY_BYTES = 64 * 1024


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _rpc_result(*, id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(*, id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _with_envelope(
    result: dict[str, Any],
    *,
    ttl_ms: int | None = None,
    cache_scope: str | None = None,
) -> dict[str, Any]:
    # 2026-07-28 resultType is the result's completion kind, not its method:
    # "complete" | "input_required" | "task". Clients reject anything else
    # ("Unsupported result type"), and list results need cacheScope
    # "public" | "private".
    payload = dict(result)
    payload["resultType"] = "complete"
    if ttl_ms is not None:
        payload["ttlMs"] = ttl_ms
    if cache_scope is not None:
        payload["cacheScope"] = cache_scope
    return payload


def _tool_success(structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(structured)}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_error(error: dict[str, str]) -> dict[str, Any]:
    """A tool error.  Every string in `error` is written in this package."""

    return {
        "content": [{"type": "text", "text": f"{error['code']}: {error['message']}"}],
        "structuredContent": {"error": error},
        "isError": True,
    }


class _ToolFailure(Exception):
    def __init__(self, code: str, message: str, **extra: str) -> None:
        super().__init__(f"{code}: {message}")
        self.error = {"code": code, "message": message, **extra}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_json_content_type(value: str | None) -> bool:
    if not value:
        return False
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


IdentityVerifier = Callable[[Request], Identity | Awaitable[Identity]]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_edge_app(
    *,
    identity_resolver: IdentityVerifier,
    work_source: ShellWorkSource,
    toolsets: Sequence[ToolSet] = (),
) -> FastAPI:
    """Build the MCP protocol server.

    `identity_resolver` is the runtime shell's gateway assertion verifier
    (`GatewayAssertionIdentityResolver`); `work_source` reads the
    public-work contract through the shell.  Neither holds a credential.
    `toolsets` add write-class tools after the built-in read tools (see
    `toolsets.py`); a name collision is a startup error.
    """

    tool_order, toolset_specs = merge_toolsets(TOOL_ORDER, toolsets)

    def _tool_bucket(name: str) -> str | None:
        if name in toolset_specs:
            return toolset_specs[name].bucket
        return TOOL_SCOPES.get(name)

    def _bucket_authority(bucket: str) -> str | None:
        return SCOPE_AUTHORITIES.get(bucket) or BUCKET_AUTHORITIES.get(bucket)

    def _app_callable_tools() -> list[str]:
        return [
            name
            for name in tool_order
            if name in toolset_specs or (name in TOOL_SCOPES and name in _TOOL_DEFS)
        ]

    def _app_tool_list_payload() -> list[dict[str, Any]]:
        return [
            dict(toolset_specs[name].definition) if name in toolset_specs else _TOOL_DEFS[name]
            for name in _app_callable_tools()
        ]

    def _app_allowed_authorities() -> frozenset[str]:
        buckets = {_tool_bucket(name) for name in _app_callable_tools()}
        return frozenset(
            authority
            for authority in (_bucket_authority(bucket) for bucket in buckets if bucket)
            if authority
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await work_source.aclose()

    app = FastAPI(
        title="Vuoro MCP read surface",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    async def _verify(request: Request) -> tuple[Identity, str, str] | None:
        assertion = request.headers.get("x-vuoro-identity")
        request_id = request.headers.get("x-request-id")
        if not assertion or not request_id:
            return None
        # The gateway assertion's request_id claim covers the transport
        # request here; the shell's verifier checks it against this state.
        request.state.vuoro_invocation_request_id = request_id
        try:
            identity = identity_resolver(request)
            if inspect.isawaitable(identity):
                identity = await identity
        except IdentityResolutionError:
            return None
        if not isinstance(identity, Identity):
            return None
        # The gateway mints MCP assertions carrying only what this surface's
        # scope table can use.  A broader assertion was minted for something
        # else and is refused, not narrowed.
        if not identity.authorities or not identity.authorities <= _app_allowed_authorities():
            return None
        return identity, assertion, request_id

    def _header_body_mismatch(request: Request, body: dict[str, Any]) -> str | None:
        """A header that disagrees with its body counterpart when BOTH are
        present.  Absence on either side is not a mismatch: dual-era clients
        legitimately omit these headers."""

        method = body.get("method")
        params = body.get("params")
        params = params if isinstance(params, dict) else {}
        header_version = request.headers.get("mcp-protocol-version")
        body_version = params.get("protocolVersion")
        if (
            header_version is not None
            and isinstance(body_version, str)
            and header_version != body_version
        ):
            return "MCP-Protocol-Version header disagrees with request body"
        header_method = request.headers.get("mcp-method")
        if header_method is not None and isinstance(method, str) and header_method != method:
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

    def _forwarded(identity: Identity, assertion: str, request_id: str) -> ForwardedIdentity:
        repo_ids = sorted(identity.repo_ids)
        if not repo_ids:
            raise _ToolFailure(
                "workspace-unbound", "the caller's assertion names no repository"
            )
        if len(repo_ids) > 1:
            raise _ToolFailure(
                "workspace-ambiguous",
                "the caller's assertion names more than one repository",
            )
        return ForwardedIdentity(
            assertion=assertion,
            request_id=request_id,
            repo_id=repo_ids[0],
            identity=identity,
        )

    def _invalid_params(message: str) -> _ToolFailure:
        return _ToolFailure("invalid-params", message)

    def _list_arguments(arguments: dict[str, Any]) -> int:
        if set(arguments) - {"limit"}:
            raise _invalid_params("list_ready_work accepts only limit")
        limit = arguments.get("limit", LIST_LIMIT_MAX)
        if not _is_int(limit) or not 1 <= limit <= LIST_LIMIT_MAX:
            raise _invalid_params(f"limit must be an integer from 1 to {LIST_LIMIT_MAX}")
        return limit

    def _describe_arguments(arguments: dict[str, Any]) -> int:
        if set(arguments) - {"work_id"}:
            raise _invalid_params("describe_work accepts only work_id")
        work_id = arguments.get("work_id")
        if not _is_int(work_id) or work_id < 1:
            raise _invalid_params("work_id must be an integer >= 1")
        return work_id

    async def _list_ready_work(limit: int, forwarded: ForwardedIdentity) -> dict[str, Any]:
        listing = await work_source.list_work(forwarded)
        # sprintctl's own ready rule: pending and not blocked.
        ready = [
            item
            for item in listing["items"]
            if item["status"] == "pending" and item["blocked"] is False
        ]
        return {
            "authority": listing["authority"],
            "as_of": listing["as_of"],
            "items": ready[:limit],
        }

    async def _describe_work(work_id: int, forwarded: ForwardedIdentity) -> dict[str, Any]:
        return await work_source.describe_work(forwarded, work_id)

    tools = {
        "list_ready_work": (_list_arguments, _list_ready_work),
        "describe_work": (_describe_arguments, _describe_work),
    }

    async def _call_tool(
        params: dict[str, Any], identity: Identity, assertion: str, request_id: str
    ) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or name not in _app_callable_tools():
            raise _ToolFailure("unknown-tool", "no callable tool has that name")
        if name not in tools and name not in toolset_specs:
            raise _ToolFailure("unknown-tool", "no callable tool has that name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _invalid_params("arguments must be an object")
        if name in toolset_specs:
            spec = toolset_specs[name]
            try:
                parsed = spec.parse(arguments)
            except ToolFailure as failure:
                raise _ToolFailure(failure.code, failure.message) from failure
            run = spec.run
        else:
            parse, run = tools[name]
            parsed = parse(arguments)
        scope = _tool_bucket(name) or ""
        authority = _bucket_authority(scope)
        if authority is None or authority not in identity.authorities:
            raise _ToolFailure(
                "authority-required",
                f"the caller's assertion lacks the {scope} scope authority",
            )
        forwarded = _forwarded(identity, assertion, request_id)
        try:
            structured = await run(parsed, forwarded)
        except ToolFailure as failure:
            raise _ToolFailure(failure.code, failure.message) from failure
        except WorkSourceUnavailable as error:
            failure = client_error(error)
            if is_not_found(error):
                # An ordinary not-found answer from the contract, not the
                # work source failing to answer: never "work source failed".
                LOGGER.info(
                    "work item not found",
                    extra={
                        "tool": name,
                        "code": failure["code"],
                        "request_id": request_id,
                    },
                )
            else:
                LOGGER.warning(
                    "work source failed",
                    extra={
                        "tool": name,
                        "code": error.code,
                        "upstream": error.upstream,
                        "detail": error.message,
                        "request_id": request_id,
                    },
                )
            raise _ToolFailure(**failure) from error
        return _tool_success(structured)

    def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status_code)

    def _rpc_fail(id_: Any, code: int, message: str) -> JSONResponse:
        # JSON-RPC errors travel as HTTP 200 with an error body.
        return _json(_rpc_error(id_=id_, code=code, message=message))

    @app.post(MCP_PATH, include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        verified = await _verify(request)
        if verified is None:
            return _json(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_UNAUTHORIZED,
                    message="a valid gateway identity assertion is required",
                ),
                401,
            )
        identity, assertion, request_id = verified

        header_version = request.headers.get("mcp-protocol-version")
        if header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return _json(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_INVALID_REQUEST,
                    message="unsupported MCP-Protocol-Version",
                ),
                400,
            )
        if not _is_json_content_type(request.headers.get("content-type")):
            return _json(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_INVALID_REQUEST,
                    message="Content-Type must be application/json",
                ),
                415,
            )
        declared_length = request.headers.get("content-length")
        if declared_length is not None and (
            not declared_length.isdigit() or int(declared_length) > MAX_BODY_BYTES
        ):
            return _json(
                _rpc_error(id_=None, code=_JSONRPC_INVALID_REQUEST, message="request too large"),
                413,
            )
        raw_body = b""
        async for chunk in request.stream():
            raw_body += chunk
            if len(raw_body) > MAX_BODY_BYTES:
                return _json(
                    _rpc_error(
                        id_=None, code=_JSONRPC_INVALID_REQUEST, message="request too large"
                    ),
                    413,
                )
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _rpc_fail(None, _JSONRPC_PARSE_ERROR, "invalid JSON")
        if isinstance(body, list):
            return _rpc_fail(None, _JSONRPC_INVALID_REQUEST, "batch requests are not supported")
        if not isinstance(body, dict):
            return _rpc_fail(None, _JSONRPC_INVALID_REQUEST, "request must be a JSON-RPC object")
        rpc_id = body.get("id")
        if body.get("jsonrpc") != "2.0":
            return _rpc_fail(rpc_id, _JSONRPC_INVALID_REQUEST, 'jsonrpc must be "2.0"')
        method = body.get("method")
        if method is None and ("result" in body or "error" in body):
            # A client's response to a server request.  This server sends
            # none, so there is nothing to correlate; acknowledge it.
            return Response(status_code=202)
        if not isinstance(method, str) or not method:
            return _rpc_fail(rpc_id, _JSONRPC_INVALID_REQUEST, "method is required")
        if "id" not in body:
            # A notification (including notifications/initialized): no body,
            # and nothing is executed on its behalf.
            return Response(status_code=202)
        if rpc_id is None or isinstance(rpc_id, bool) or not isinstance(rpc_id, (str, int)):
            return _rpc_fail(None, _JSONRPC_INVALID_REQUEST, "id must be a string or integer")
        mismatch = _header_body_mismatch(request, body)
        if mismatch is not None:
            return _rpc_fail(rpc_id, _JSONRPC_HEADER_BODY_MISMATCH, mismatch)
        params = body.get("params")
        params = params if isinstance(params, dict) else {}
        server_info = {"name": SERVER_NAME, "version": __version__}
        capabilities = {"tools": {"listChanged": False}}

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
                    "capabilities": capabilities,
                    "serverInfo": server_info,
                },
                ttl_ms=0,
                cache_scope="private",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method == "ping":
            return _json(_rpc_result(id_=rpc_id, result={}))

        if method == "server/discover":
            result = _with_envelope(
                {
                    "protocolVersion": CURRENT_PROTOCOL_VERSION,
                    "supportedVersions": sorted(SUPPORTED_PROTOCOL_VERSIONS),
                    "capabilities": capabilities,
                    "serverInfo": server_info,
                    "tools": _app_tool_list_payload(),
                },
                ttl_ms=0,
                cache_scope="private",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method == "tools/list":
            result = _with_envelope(
                {"tools": _app_tool_list_payload()},
                ttl_ms=0,
                cache_scope="private",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method == "tools/call":
            try:
                tool_result = await _call_tool(params, identity, assertion, request_id)
                ttl_ms = _TOOL_TTL_MS
            except _ToolFailure as failure:
                tool_result = _tool_error(failure.error)
                ttl_ms = 0
            except Exception:
                LOGGER.exception("tool handler failed", extra={"tool": params.get("name")})
                tool_result = _tool_error(
                    {"code": "internal-error", "message": "the tool handler failed"}
                )
                ttl_ms = 0
            result = _with_envelope(
                tool_result,
                ttl_ms=ttl_ms,
                cache_scope="private",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        return _rpc_fail(rpc_id, _JSONRPC_METHOD_NOT_FOUND, "method not found")

    @app.get(MCP_PATH, include_in_schema=False)
    async def mcp_get_not_allowed() -> Response:
        return JSONResponse(
            _rpc_error(
                id_=None,
                code=_JSONRPC_INVALID_REQUEST,
                message="GET is not supported: this server answers POST with JSON only",
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
                message="DELETE is not supported: this server keeps no sessions",
            ),
            status_code=405,
            headers={"Allow": "POST"},
        )

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "live"}

    return app
