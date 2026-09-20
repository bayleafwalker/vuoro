"""Entrypoints for the two processes this package runs on a homelab host:
the internal MCP server, and the poller that wraps it as custom tools.

Both are meant to be started by the deployment config in
`deploy/poller/` (systemd units or docker-compose), bound to a loopback or
internal-only address -- never a publicly routable one. Neither entrypoint
here opens a listener on `0.0.0.0` by default.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import os

from vuoro_service.identity import Identity

from vuoro_worker import __version__
from vuoro_worker.internal_tools import InternalToolServer, StaticWorkSource
from vuoro_worker.mcp_client import MCPClient
from vuoro_worker.custom_tools import build_custom_tools
from vuoro_worker.managed_agents import HTTPQueueClient
from vuoro_worker.poller import Poller


def _identities_from_env() -> dict[str, Identity]:
    """`VUORO_POLLER_TOKEN` names the single static-bearer credential this
    host-local server accepts, matching the static-bearer auth mode decided
    for E1 (agentops#2470) rather than inventing a second auth model for
    the internal path. Read once at process start; never logged."""
    token = os.environ.get("VUORO_POLLER_TOKEN")
    if not token:
        raise SystemExit("VUORO_POLLER_TOKEN must be set")
    actor = os.environ.get("VUORO_POLLER_ACTOR", "vuoro-poller")
    return {
        token: Identity(
            actor=actor,
            environment=os.environ.get("VUORO_POLLER_ENVIRONMENT", "homelab"),
            authorities=frozenset({"poller"}),
            repo_ids=frozenset({"*"}),
        )
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vuoro-worker")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser(
        "serve-internal-mcp", help="Run the internal MCP server (loopback by default)"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    poll = commands.add_parser("poll", help="Run the Managed Agents poller loop")
    poll.add_argument("--internal-mcp-url", default="http://127.0.0.1:8765")
    poll.add_argument("--poll-interval-seconds", type=float, default=5.0)

    return parser


def _run_serve_internal_mcp(host: str, port: int) -> int:
    import uvicorn

    from vuoro_worker.mcp_server import create_mcp_app

    server = InternalToolServer(identities=_identities_from_env(), work_source=StaticWorkSource(()))
    app = create_mcp_app(server)
    if host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger(__name__).warning(
            "serve-internal-mcp is binding to %s, not loopback -- confirm this "
            "address is not publicly routable before running it (see "
            "deploy/poller/README.md)",
            host,
        )
    uvicorn.run(app, host=host, port=port)
    return 0


def _run_poll(internal_mcp_url: str, poll_interval_seconds: float) -> int:
    token = os.environ.get("VUORO_POLLER_TOKEN")
    if not token:
        raise SystemExit("VUORO_POLLER_TOKEN must be set")
    managed_agents_base_url = os.environ.get("VUORO_MANAGED_AGENTS_BASE_URL")
    managed_agents_token = os.environ.get("VUORO_MANAGED_AGENTS_TOKEN")
    managed_agents_agent_id = os.environ.get("VUORO_MANAGED_AGENTS_AGENT_ID")
    if not (managed_agents_base_url and managed_agents_token and managed_agents_agent_id):
        raise SystemExit(
            "VUORO_MANAGED_AGENTS_BASE_URL, VUORO_MANAGED_AGENTS_TOKEN and "
            "VUORO_MANAGED_AGENTS_AGENT_ID must be set -- registering the agent "
            "itself is an operator, vendor-side step this package does not do"
        )
    mcp_client = MCPClient(base_url=internal_mcp_url, token=token)
    custom_tools = build_custom_tools(mcp_client)
    queue_client = HTTPQueueClient(
        base_url=managed_agents_base_url,
        token=managed_agents_token,
        agent_id=managed_agents_agent_id,
    )
    poller = Poller(queue_client=queue_client, mcp_client=mcp_client, custom_tools=custom_tools)
    poller.run_forever(poll_interval_seconds=poll_interval_seconds)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve-internal-mcp":
        return _run_serve_internal_mcp(args.host, args.port)
    if args.command == "poll":
        return _run_poll(args.internal_mcp_url, args.poll_interval_seconds)
    parser.print_help()
    return 0
