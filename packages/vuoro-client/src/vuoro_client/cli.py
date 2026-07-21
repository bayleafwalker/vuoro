"""Minimal client entrypoint for the repository bootstrap."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from vuoro_client import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vuoro",
        description="Invoke schema-described operations on a Vuoro service.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
