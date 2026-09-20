"""A real loopback-bound test server for the internal MCP app.

httpx's `ASGITransport` only implements the async transport interface, and
`mcp_client.MCPClient` is deliberately a synchronous client (the poller's
own dispatch loop is synchronous, matching its fast/non-interactive
constraint -- see `poller.py`). Rather than giving the client two code
paths, tests run the real FastAPI app on a loopback socket in a background
thread, which also happens to exercise the exact network posture this
package is meant to have (bound to 127.0.0.1, never 0.0.0.0).
"""

from __future__ import annotations

from collections.abc import Iterator
import socket
import threading

import pytest
import uvicorn
from fastapi import FastAPI


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def run_app():
    """Yields a function `run_app(app) -> base_url` that starts `app` on a
    loopback socket and tears it down at test end."""
    servers: list[uvicorn.Server] = []
    threads: list[threading.Thread] = []

    def _run(app: FastAPI) -> str:
        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
        import time

        deadline = time.monotonic() + 5.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        return f"http://127.0.0.1:{port}"

    yield _run

    for server in servers:
        server.should_exit = True
    for thread in threads:
        thread.join(timeout=5.0)
