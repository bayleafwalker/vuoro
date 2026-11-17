"""``vuoro-workspace migrate|check`` operator commands.

``migrate`` runs DDL and is meant for the migration role only; ``check`` is
SELECT-only and exits 2 when the schema cannot serve this release.
"""

from __future__ import annotations

import argparse
import dataclasses
from difflib import get_close_matches
import json
import os
import re
import sys
import textwrap
from typing import Sequence

DSN_ENV = "VUORO_WORKSPACE_DSN"


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
        if self._subparsers is None or not self._subparsers._group_actions:
            return super().format_help()
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        formatter.add_text(self.description)
        formatter.start_section("Start here")
        formatter.add_text("Check the schema before serving; migrate with the migration role.")
        formatter.end_section()
        groups = {"Daily use": [], "Inspect": [], "Change": [], "Maintain": [], "Plumbing": [], "Other": []}
        commands = self._subparsers._group_actions[0]
        category = {"check": "Inspect", "migrate": "Maintain"}
        helps = {action.dest: action.help or "" for action in commands._choices_actions}
        for name in commands.choices:
            groups[category.get(name, "Other")].append((name, helps.get(name, "")))
        for group, rows in groups.items():
            if not rows:
                continue
            formatter.start_section(group)
            formatter.add_text("\n".join(f"{name:<8} {purpose}" for name, purpose in rows))
            formatter.end_section()
        formatter.start_section("Examples")
        formatter.add_text(textwrap.dedent("\n".join(self.epilog.splitlines()[1:])).strip())
        formatter.end_section()
        formatter.add_text(f"Run '{self.prog} <command> --help' for command options.")
        return formatter.format_help()


def main(argv: Sequence[str] | None = None) -> int:
    from . import __version__

    parser = _Parser(prog="vuoro-workspace", description="Manage workspace schema compatibility.",
                     epilog="Examples:\n  vuoro-workspace check\n  vuoro-workspace migrate",
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    for name, help_text in (("migrate", "Apply pending migrations (migration role)"),
                            ("check", "Check schema compatibility (read-only)")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--dsn", default=os.environ.get(DSN_ENV), help=f"PostgreSQL DSN (default ${DSN_ENV})")
        cmd.add_argument("--schema", default=None, help="database schema (default: knowledge)")
        if name == "check":
            cmd.add_argument("--runtime-role", default=None, help="expected runtime role (default: current role)")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if not args.dsn:
        parser.error(f"--dsn or ${DSN_ENV} is required")
    import psycopg
    from .migrations import DEFAULT_SCHEMA, SchemaMigrationError, compatibility, migrate_schema
    if args.schema is None:
        args.schema = DEFAULT_SCHEMA

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        try:
            if args.command == "migrate":
                result = migrate_schema(conn, args.schema)
                print(json.dumps(dataclasses.asdict(result), sort_keys=True))
                return 0
            report = compatibility(conn, args.schema, args.runtime_role)
        except SchemaMigrationError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0 if report.compatible else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
