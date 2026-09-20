"""Internal-only MCP-over-HTTP server, bound to a loopback/internal address
by deployment config (`deploy/poller/`), never to a public listener.

Speaks both protocol eras (§2's third constraint): a legacy `initialize`
handshake for older clients, and the 2026-07-28 SEP-2567 sessionless style
for everything else -- tool calls carry their own handle (`lease_id`) as an
ordinary argument, with no session object on either side. `server/discover`
returns the eight tools in `internal_tools.TOOL_ORDER` (deterministic, for
prompt-cache hit rates) with durability text in each description.

This process is the "internal MCP server" the poller wraps as custom tools
(edge doc §2: "wrapping an internal MCP server as custom tools against
http://mcp.internal:8000/mcp"). It is deliberately not the public E1-E4
surface -- no gateway-signed assertions, no internet exposure -- and it
must never be deployed with a publicly routable listener (see
`deploy/poller/README.md`).
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from vuoro_worker.internal_tools import InternalToolServer, ToolError, ToolCallResult


LOGGER = logging.getLogger(__name__)

_PROTOCOL_VERSIONS = ("2026-07-28", "2025-06-18")  # SEP-2567 era, then legacy


def _rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _rpc_result(request_id: Any, result: Mapping[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": dict(result)})


def _tool_call_json(result: ToolCallResult) -> dict[str, Any]:
    body: dict[str, Any] = {"resultType": result.result_type, "content": dict(result.payload)}
    if result.ttl_ms is not None:
        body["ttlMs"] = result.ttl_ms
    if result.cache_scope is not None:
        body["cacheScope"] = result.cache_scope
    return body


def create_mcp_app(
    server: InternalToolServer,
    *,
    allowed_origins: frozenset[str] = frozenset(),
) -> FastAPI:
    """`allowed_origins` empty means: refuse any request that carries an
    `Origin` header at all. That is the correct default for a host-local
    server -- same-process and same-host callers (the poller's own MCP
    client) never send one; a browser-origin request always does."""

    app = FastAPI(title="vuoro internal MCP", docs_url=None, redoc_url=None)

    @app.get("/mcp")
    @app.delete("/mcp")
    async def _method_not_allowed() -> JSONResponse:
        return JSONResponse({"error": "method_not_allowed"}, status_code=405)

    @app.get("/health/metrics")
    async def health_metrics() -> JSONResponse:
        snapshot = server.metrics.snapshot()
        return JSONResponse(
            {
                "request_count": snapshot.request_count,
                "error_count": snapshot.error_count,
                "error_rate": snapshot.error_rate,
                "p50_latency_ms": snapshot.p50_latency_ms,
                "p95_latency_ms": snapshot.p95_latency_ms,
            }
        )

    @app.post("/mcp")
    async def mcp_endpoint(request: Request) -> JSONResponse:
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return _rpc_error(None, -32700, "invalid JSON body")
        if not isinstance(body, dict):
            return _rpc_error(None, -32600, "invalid request")

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        if not isinstance(method, str) or not method:
            return _rpc_error(request_id, -32600, "invalid request")

        # Header-to-body agreement (§4's compliance list): a header naming a
        # different method than the body is a client bug worth surfacing
        # distinctly, not silently taking the body's word for it.
        header_method = request.headers.get("mcp-method")
        header_name = request.headers.get("mcp-name")
        if header_method is not None and header_method != method:
            return _rpc_error(request_id, -32020, "Mcp-Method header disagrees with body method")
        if header_name is not None and header_name != method:
            return _rpc_error(request_id, -32020, "Mcp-Name header disagrees with body method")
        protocol_version = request.headers.get("mcp-protocol-version")
        if protocol_version is not None and protocol_version not in _PROTOCOL_VERSIONS:
            return _rpc_error(request_id, -32020, "unsupported MCP-Protocol-Version")

        if method == "initialize":
            # Legacy-era handshake. No session is created or required by
            # anything downstream (SEP-2567: handles are ordinary args), so
            # this exists purely so pre-2026-07-28 clients get the reply
            # shape they expect before they start calling tools.
            return _rpc_result(
                request_id,
                {
                    "protocolVersion": _PROTOCOL_VERSIONS[1],
                    "serverInfo": {"name": "vuoro-internal-mcp", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )

        if method == "server/discover":
            tools = [
                {"name": name, "description": server.tool_description(name)}
                for name in server.tool_names()
            ]
            return _rpc_result(request_id, {"tools": tools})

        if method not in server.tool_names():
            return _rpc_error(request_id, -32601, f"unknown method {method!r}")

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _rpc_error(request_id, -32001, "a bearer token is required")

        handler = getattr(server, method)
        try:
            result = handler(token, **params)
        except ToolError as error:
            return _rpc_error(request_id, -32010, f"{error.code}: {error.message}")
        except TypeError as error:
            return _rpc_error(request_id, -32602, f"invalid params: {error}")
        return _rpc_result(request_id, _tool_call_json(result))

    return app
