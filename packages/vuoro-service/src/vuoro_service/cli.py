"""Multi-command service entrypoint without automatic migration behavior."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from vuoro_service import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vuoro-service")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="Run the reusable HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    mcp_serve = commands.add_parser(
        "mcp-serve",
        help="Run the MCP protocol server (requires the vuoro-mcp-edge package)",
    )
    mcp_serve.add_argument("--host", default="127.0.0.1")
    mcp_serve.add_argument("--port", type=int, default=8081)

    return parser


#: The MCP server's uvicorn factory.  Named by string and imported by uvicorn
#: at run time, never by this module: vuoro-mcp-edge depends on vuoro-service,
#: so vuoro-service must not depend on or import it at import time.
MCP_APP_FACTORY = "vuoro_mcp_edge.composition:create_app_from_environment"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "vuoro_service.composition:create_composed_app",
            factory=True,
            host=args.host,
            port=args.port,
        )
        return 0
    if args.command == "mcp-serve":
        import importlib.util

        if importlib.util.find_spec("vuoro_mcp_edge") is None:
            print(
                "vuoro-service mcp-serve: the vuoro-mcp-edge package is not installed",
                file=sys.stderr,
            )
            return 2
        import uvicorn

        uvicorn.run(
            MCP_APP_FACTORY,
            factory=True,
            host=args.host,
            port=args.port,
        )
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
