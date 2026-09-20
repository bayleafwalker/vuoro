"""HTTP client for the internal MCP server (`mcp_server.py`).

Used by `custom_tools.py` to fulfil each Managed Agents custom tool call by
making one MCP JSON-RPC request against the internal, host-local endpoint --
mirroring the edge doc's own example (§2: "wrapping an internal MCP server
as custom tools against http://mcp.internal:8000/mcp"). The transport is
injectable so tests run against an in-process ASGI app with no socket at
all (`httpx.ASGITransport`), and the poller's own preflight check
(`verify_tools_available`) uses the same client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
import itertools

import httpx


class MCPClientError(Exception):
    """The internal MCP endpoint returned a JSON-RPC error, or was
    unreachable. Distinguishing the two matters to the poller's preflight
    check: `unreachable` means "skip this cycle and retry later" (§2's
    second constraint -- do not act on missing tools), while a JSON-RPC
    error from a live server means the call itself was rejected."""

    def __init__(self, message: str, *, code: int | None = None, unreachable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.unreachable = unreachable


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str


_ids = itertools.count(1)


class MCPClient:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8765",
        token: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=base_url, transport=transport, timeout=timeout_seconds
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _rpc(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        request_id = next(_ids)
        headers = {"mcp-protocol-version": "2026-07-28", "mcp-method": method}
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        try:
            response = self._client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})},
                headers=headers,
            )
        except httpx.HTTPError as error:
            raise MCPClientError(f"internal MCP endpoint unreachable: {error}", unreachable=True) from error
        try:
            body = response.json()
        except ValueError as error:
            raise MCPClientError("internal MCP endpoint returned a non-JSON response", unreachable=True) from error
        if "error" in body:
            raise MCPClientError(body["error"].get("message", "tool call rejected"), code=body["error"].get("code"))
        return body.get("result", {})

    def discover(self) -> tuple[ToolDescriptor, ...]:
        result = self._rpc("server/discover")
        return tuple(ToolDescriptor(name=t["name"], description=t["description"]) for t in result.get("tools", []))

    def call_tool(self, name: str, **params: Any) -> Mapping[str, Any]:
        return self._rpc(name, params)
