"""Minimal client entrypoint for the repository bootstrap."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
from difflib import get_close_matches
import json
import os
from pathlib import Path
import re

from vuoro_client import __version__
from vuoro_client.recovery import RecoveryLog


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        match = re.search(r"invalid choice: '([^']+)'", message)
        if match:
            typo = match.group(1)
            for action in self._actions:
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict):
                    suggestion = get_close_matches(typo, choices, n=1, cutoff=0.6)
                    if suggestion:
                        message += f"\ndid you mean {suggestion[0]}?"
                        break
        super().error(message)


def _default_recovery_root() -> Path:
    override = os.environ.get("VUORO_RECOVERY_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".vuoro" / "recovery"


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="vuoro",
        description="Export and maintain local Vuoro incident recovery records.",
        epilog="Examples:\n  vuoro recovery --help\n  vuoro recovery export --incident INC-123",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", parser_class=_Parser)

    recovery = commands.add_parser(
        "recovery",
        help="Bootstrap/disaster-recovery escape hatch: export-only, incident-scoped",
    )
    recovery_commands = recovery.add_subparsers(dest="recovery_command", parser_class=_Parser)

    begin = recovery_commands.add_parser(
        "begin", help="Open (or resume) an incident-scoped recovery namespace"
    )
    begin.add_argument("--incident", required=True)

    observe = recovery_commands.add_parser(
        "observe", help="Append an observation to a recovery incident"
    )
    observe.add_argument("--incident", required=True)
    observe.add_argument("--summary", required=True)
    observe.add_argument("--basis-revision", default=None)

    request_command = recovery_commands.add_parser(
        "request-command",
        help="Append a requested command (never applied automatically) to a recovery incident",
    )
    request_command.add_argument("--incident", required=True)
    request_command.add_argument("--summary", required=True)
    request_command.add_argument("--basis-revision", default=None)
    request_command.add_argument(
        "--command",
        dest="command_payload",
        required=True,
        help="JSON object: the requested command_type and params",
    )

    export = recovery_commands.add_parser(
        "export", help="Render every record in a recovery incident as JSON"
    )
    export.add_argument("--incident", required=True)

    return parser


def _recovery_log(args: argparse.Namespace) -> RecoveryLog:
    return RecoveryLog(_default_recovery_root(), args.incident)


def _run_recovery(args: argparse.Namespace) -> int:
    if args.recovery_command == "begin":
        log = _recovery_log(args).begin()
        print(f"recovery incident {args.incident!r} open at {log.path}")
        return 0
    if args.recovery_command == "observe":
        log = _recovery_log(args)
        entry = log.append(
            record_kind="observation",
            summary=args.summary,
            created_at=datetime.now(timezone.utc).isoformat(),
            basis_revision=args.basis_revision,
        )
        print(json.dumps(entry.to_json(), sort_keys=True))
        return 0
    if args.recovery_command == "request-command":
        log = _recovery_log(args)
        entry = log.append(
            record_kind="requested-command",
            summary=args.summary,
            created_at=datetime.now(timezone.utc).isoformat(),
            basis_revision=args.basis_revision,
            requested_command=json.loads(args.command_payload),
        )
        print(json.dumps(entry.to_json(), sort_keys=True))
        return 0
    if args.recovery_command == "export":
        log = _recovery_log(args)
        print(json.dumps(log.export(), sort_keys=True))
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "recovery":
        if args.recovery_command is None:
            parser.parse_args(["recovery", "--help"])
            return 0
        return _run_recovery(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
