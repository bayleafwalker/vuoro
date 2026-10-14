"""``vuoro-workspace migrate|check`` operator commands.

``migrate`` runs DDL and is meant for the migration role only; ``check`` is
SELECT-only and exits 2 when the schema cannot serve this release.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Sequence

from .migrations import DEFAULT_SCHEMA, SchemaMigrationError, compatibility, migrate_schema

DSN_ENV = "VUORO_WORKSPACE_DSN"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vuoro-workspace", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("migrate", "apply pending migrations (migration role)"),
                            ("check", "read-only compatibility check")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--dsn", default=os.environ.get(DSN_ENV), help=f"PostgreSQL DSN (default ${DSN_ENV})")
        cmd.add_argument("--schema", default=DEFAULT_SCHEMA)
        if name == "check":
            cmd.add_argument("--runtime-role", default=None, help="expected runtime role (default: current role)")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error(f"--dsn or ${DSN_ENV} is required")
    import psycopg

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
