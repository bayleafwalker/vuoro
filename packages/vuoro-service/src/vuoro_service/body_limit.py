"""Bounded request bodies for the invocation route.

The invocation envelope is parsed in memory, so its size is bounded before
parsing: a declared ``Content-Length`` over the limit is refused without
reading, and an undeclared (chunked) body is read only up to the limit. The
accepted body is replayed unchanged, so body-bound edge proofs still verify.
Only ``POST /api/invoke/v1`` is affected.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

INVOKE_PATH = "/api/invoke/v1"
#: Default cap: a Phase 1 knowledge upload (8 MiB as base64, ~10.7 MiB) plus
#: envelope headroom.
DEFAULT_INVOKE_BODY_LIMIT_BYTES = 12 * 1024 * 1024

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_REJECTION = {
    "schema_version": "invocation-result/v1",
    "request_id": "invalid-request",
    "operation": "invalid-operation",
    "catalog_revision": "redacted",
    "status": "rejected",
    "result": None,
    "error": {"code": "request-too-large", "message": "invocation body exceeds the service limit"},
}


class InvokeBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, limit_bytes: int = DEFAULT_INVOKE_BODY_LIMIT_BYTES) -> None:
        if not isinstance(limit_bytes, int) or isinstance(limit_bytes, bool) or limit_bytes < 1:
            raise ValueError("invoke body limit must be a positive integer")
        self.app = app
        self.limit = limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") != INVOKE_PATH or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > self.limit:
                    await _reject(send)
                    return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                await _reject(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def _reject(send: Send) -> None:
    payload = json.dumps(_REJECTION, separators=(",", ":")).encode()
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
    })
    await send({"type": "http.response.body", "body": payload})
