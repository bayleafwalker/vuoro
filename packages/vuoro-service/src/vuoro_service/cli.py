"""Multi-command service entrypoint without automatic migration behavior."""

from __future__ import annotations

import argparse
import json
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

    commands.add_parser(
        "check-compatibility",
        help="Check configured domain schemas without changing them",
    )

    migrate = commands.add_parser(
        "migrate",
        help="Run an explicitly selected deployment migration entrypoint",
    )
    migrate.add_argument("--domain", required=True)

    admin = commands.add_parser(
        "admin",
        help="Run an explicitly authorized operational task",
    )
    admin.add_argument("action")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "vuoro_service.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
        )
        return 0
    if args.command == "check-compatibility":
        print(json.dumps({"compatible": False, "reason": "no adapters registered"}))
        return 3
    if args.command in {"migrate", "admin"}:
        parser.error(
            f"{args.command} is not available until an owning adapter is registered"
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
