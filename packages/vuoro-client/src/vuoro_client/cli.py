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

    def format_help(self) -> str:
        if not self._subparsers or not self._subparsers._group_actions:
            return super().format_help()
        commands = self._subparsers._group_actions[0]
        rows = [(action.dest, action.help or "") for action in commands._choices_actions]
        nested = self.prog.endswith(" recovery")
        if nested:
            usage = "usage: vuoro-client recovery [--help] <command> ..."
            purpose = "Start here: local, incident-scoped recovery records."
            groups = [("Daily use", rows)]
            pointer = "Run 'vuoro-client recovery <command> --help' for command options."
        else:
            usage = "usage: vuoro-client [--version] <command> ..."
            purpose = self.description
            groups = [("Start here", [("", "Use recovery for local, incident-scoped records.")]),
                      ("Maintain", rows)]
            pointer = "Run 'vuoro-client <command> --help' for command options."
        output = [usage, "", purpose]
        for heading, rows in groups:
            output.extend(["", heading + ":"])
            width = max((len(name) for name, _ in rows), default=0)
            output.extend(f"  {name:<{width}}  {text}" if name else f"  {text}"
                          for name, text in rows)
        output.extend(["", "Examples:",
                       "  vuoro-client recovery begin --incident INC-123",
                       "  vuoro-client recovery export --incident INC-123",
                       "", pointer, ""])
        return "\n".join(output)

def _default_recovery_root() -> Path:
    override = os.environ.get("VUORO_RECOVERY_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".vuoro" / "recovery"


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="vuoro-client",
        description="Export and maintain local Vuoro incident recovery records.",
        epilog="Examples:\n  vuoro-client recovery begin --incident INC-123\n  vuoro-client recovery export --incident INC-123",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", parser_class=_Parser)

    recovery = commands.add_parser(
        "recovery",
        help="Open or export local incident records",
        description="Start here — recovery is a local, export-only escape hatch.",
    )
    recovery.prog = "vuoro-client recovery"
    recovery_commands = recovery.add_subparsers(dest="recovery_command", parser_class=_Parser)

    begin = recovery_commands.add_parser(
        "begin", help="Open or resume a recovery incident"
    )
    begin.add_argument("--incident", required=True)

    observe = recovery_commands.add_parser(
        "observe", help="Record an incident observation"
    )
    observe.add_argument("--incident", required=True)
    observe.add_argument("--summary", required=True)
    observe.add_argument("--basis-revision", default=None)

    request_command = recovery_commands.add_parser(
        "request-command",
        help="Record a command request (never applied automatically)",
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
        "export", help="Export every incident record as JSON"
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
