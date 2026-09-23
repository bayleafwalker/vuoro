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

Protocol handling (edge plan section 4): ``405`` on GET and DELETE,
header-to-body agreement on ``MCP-Protocol-Version``, ``Mcp-Method`` and
``Mcp-Name`` (mismatch is ``-32020``), ``server/discover``, ``resultType`` on
every result, ``ttlMs``/``cacheScope`` on list and read results, and a fixed
tool order.  Both protocol eras work: legacy clients open with
``initialize`` / ``notifications/initialized``; clients on the 2026-07-28
revision call ``server/discover`` / ``tools/call`` with no handshake.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from vuoro_service.identity import Identity, IdentityResolutionError

from .errors import WorkSourceUnavailable
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
    {"2024-11-05", "2025-03-26", "2025-06-18"}
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
_JSONRPC_INVALID_PARAMS = -32602
_JSONRPC_UNAUTHORIZED = -32001
_JSONRPC_HEADER_BODY_MISMATCH = -32020


# ---------------------------------------------------------------------------
# Tool definitions.  The description carries the state claims explicitly,
# because the model reads the description, not only the schema.
# ---------------------------------------------------------------------------

_TOOL_DEFS: dict[str, dict[str, Any]] = {
    "list_ready_work": {
        "name": "list_ready_work",
        "description": (
            "Lists the workspace's open work items that are not blocked by an "
            "unresolved dependency, in the tracker's next-work order (priority "
            "first, unset last, then creation order). Each item has work_id, "
            "title, priority, status, blocked (always false here) and "
            "updated_at. Read-only: calling this creates no state, claims "
            "nothing and holds no lease. The answer is a live snapshot, not a "
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
    },
    "describe_work": {
        "name": "describe_work",
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
    },
}


def _callable_tools() -> list[str]:
    return [name for name in TOOL_ORDER if name in TOOL_SCOPES and name in _TOOL_DEFS]


def _tool_list_payload() -> list[dict[str, Any]]:
    return [_TOOL_DEFS[name] for name in _callable_tools()]


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


def _tool_success(structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(structured)}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"{code}: {message}"}],
        "structuredContent": {"error": {"code": code, "message": message}},
        "isError": True,
    }


class _InvalidParams(ValueError):
    pass


class _ToolFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


IdentityVerifier = Callable[[Request], Identity | Awaitable[Identity]]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_edge_app(
    *,
    identity_resolver: IdentityVerifier,
    work_source: ShellWorkSource,
) -> FastAPI:
    """Build the MCP protocol server.

    `identity_resolver` is the runtime shell's gateway assertion verifier
    (`GatewayAssertionIdentityResolver`); `work_source` reads the
    public-work contract through the shell.  Neither holds a credential.
    """

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
            assertion=assertion, request_id=request_id, repo_id=repo_ids[0]
        )

    async def _list_ready_work(
        arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        unknown = set(arguments) - {"limit"}
        if unknown:
            raise _InvalidParams("list_ready_work accepts only limit")
        limit = arguments.get("limit", LIST_LIMIT_MAX)
        if not _is_int(limit) or not 1 <= limit <= LIST_LIMIT_MAX:
            raise _InvalidParams(f"limit must be an integer from 1 to {LIST_LIMIT_MAX}")
        listing = await work_source.list_work(forwarded)
        ready = [item for item in listing["items"] if item["blocked"] is False]
        return {
            "authority": listing["authority"],
            "as_of": listing["as_of"],
            "items": ready[:limit],
        }

    async def _describe_work(
        arguments: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        unknown = set(arguments) - {"work_id"}
        if unknown:
            raise _InvalidParams("describe_work accepts only work_id")
        work_id = arguments.get("work_id")
        if not _is_int(work_id) or work_id < 1:
            raise _InvalidParams("work_id must be an integer >= 1")
        return await work_source.describe_work(forwarded, work_id)

    handlers = {
        "list_ready_work": _list_ready_work,
        "describe_work": _describe_work,
    }

    async def _call_tool(
        name: str,
        arguments: Any,
        identity: Identity,
        assertion: str,
        request_id: str,
    ) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _InvalidParams("arguments must be an object")
        scope = TOOL_SCOPES[name]
        authority = SCOPE_AUTHORITIES.get(scope)
        if authority is None or authority not in identity.authorities:
            raise _ToolFailure(
                "authority-required",
                f"the caller's assertion lacks the {scope} scope authority",
            )
        forwarded = _forwarded(identity, assertion, request_id)
        try:
            structured = await handlers[name](arguments, forwarded)
        except WorkSourceUnavailable as error:
            raise _ToolFailure(error.code, error.message) from error
        return _tool_success(structured)

    def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status_code)

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

        raw_body = await request.body()
        try:
            body = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            return _json(
                _rpc_error(id_=None, code=_JSONRPC_PARSE_ERROR, message="invalid JSON"), 400
            )
        if not isinstance(body, dict):
            return _json(
                _rpc_error(
                    id_=None,
                    code=_JSONRPC_INVALID_REQUEST,
                    message="request body must be a JSON-RPC object",
                ),
                400,
            )
        rpc_id = body.get("id")
        method = body.get("method")
        if not isinstance(method, str) or not method:
            return _json(
                _rpc_error(
                    id_=rpc_id, code=_JSONRPC_INVALID_REQUEST, message="method is required"
                ),
                400,
            )
        mismatch = _header_body_mismatch(request, body)
        if mismatch is not None:
            return _json(
                _rpc_error(id_=rpc_id, code=_JSONRPC_HEADER_BODY_MISMATCH, message=mismatch),
                400,
            )
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
                result_type="initialize-result",
                ttl_ms=0,
                cache_scope="none",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method == "server/discover":
            result = _with_envelope(
                {
                    "protocolVersion": CURRENT_PROTOCOL_VERSION,
                    "supportedVersions": sorted(SUPPORTED_PROTOCOL_VERSIONS),
                    "capabilities": capabilities,
                    "serverInfo": server_info,
                    "tools": _tool_list_payload(),
                },
                result_type="discover-result",
                ttl_ms=0,
                cache_scope="none",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method == "tools/list":
            result = _with_envelope(
                {"tools": _tool_list_payload()},
                result_type="tools-list-result",
                ttl_ms=0,
                cache_scope="none",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in _callable_tools():
                return _json(
                    _rpc_error(
                        id_=rpc_id,
                        code=_JSONRPC_INVALID_PARAMS,
                        message="unknown or missing tool name",
                    ),
                    400,
                )
            try:
                tool_result = await _call_tool(
                    name, params.get("arguments"), identity, assertion, request_id
                )
                ttl_ms = _TOOL_TTL_MS
            except _InvalidParams as error:
                return _json(
                    _rpc_error(id_=rpc_id, code=_JSONRPC_INVALID_PARAMS, message=str(error)),
                    400,
                )
            except _ToolFailure as failure:
                LOGGER.warning(
                    "tool call failed",
                    extra={"tool": name, "code": failure.code, "request_id": request_id},
                )
                tool_result = _tool_error(failure.code, failure.message)
                ttl_ms = 0
            except Exception:
                LOGGER.exception("tool handler failed", extra={"tool": name})
                tool_result = _tool_error("internal-error", "the tool handler failed")
                ttl_ms = 0
            result = _with_envelope(
                tool_result,
                result_type="tool-call-result",
                ttl_ms=ttl_ms,
                cache_scope="private",
            )
            return _json(_rpc_result(id_=rpc_id, result=result))

        if method.startswith("notifications/"):
            # Includes notifications/initialized: no response body.
            return Response(status_code=202)

        return _json(
            _rpc_error(
                id_=rpc_id, code=_JSONRPC_METHOD_NOT_FOUND, message=f"unknown method: {method}"
            ),
            404,
        )

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
