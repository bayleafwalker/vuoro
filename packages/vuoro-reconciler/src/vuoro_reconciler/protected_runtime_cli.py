"""Fixed protected entrypoint: python -I -m vuoro_reconciler.protected_runtime_cli."""

import argparse
import asyncio
from dataclasses import asdict
import json
import re
import sys

from .protected_copy_codec import _need, _read
from .protected_executor import ExecutionResult
from .protected_effects import AcceptedPageData
from .protected_runtime_factory import open_installed_runtime, install_profile_journal
from .protected_runtime_registry import _ID


def _profile(value):
    if not _ID.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid installed profile ID")
    return value


def _acceptance(value):
    if not re.fullmatch(r"iac_[0-9a-f]{32}", value):
        raise argparse.ArgumentTypeError("invalid acceptance ID")
    return value


def _limit(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "limit must be an integer from 1 to 64"
        ) from None
    if not 1 <= number <= 64:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 64")
    return number


def _cursor(value):
    if not 0 < len(value) <= 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise argparse.ArgumentTypeError("invalid accepted-page cursor")
    return value


def _parser():
    parser = argparse.ArgumentParser(prog="vuoro-protected")
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser(
        "execute", help="reconcile an approved original effect"
    )
    execute.add_argument("profile_id", type=_profile)
    execute.add_argument("acceptance_id", type=_acceptance)
    listing = commands.add_parser("list-accepted", help="read accepted effect metadata")
    listing.add_argument("profile_id", type=_profile)
    listing.add_argument("--cursor", type=_cursor)
    listing.add_argument("--limit", type=_limit, default=64)
    install = commands.add_parser(
        "install-journal", help="exclusively install the bound journal"
    )
    install.add_argument("profile_id", type=_profile)
    return parser


def _emit(value):
    # Escape every non-ASCII character as well as JSON controls. Proposer DATA
    # and public URLs cannot insert terminal controls or bidi formatting.
    sys.stdout.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()


async def _run(arguments):
    with open_installed_runtime(arguments.profile_id) as runtime:
        if arguments.command == "list-accepted":
            page = await runtime.executor.ledger.list_accepted(
                arguments.cursor, arguments.limit
            )
            _need(type(page) is AcceptedPageData)
            value = {
                "schema": "vuoro-protected-accepted-page/v1",
                "profile_id": arguments.profile_id,
                "namespace": _read(page.namespace_utf8, 4096),
                "items": [asdict(item) for item in page.items],
                "next_cursor": page.next_cursor,
            }
            code = 0
        else:
            result = await runtime.executor.run(arguments.acceptance_id)
            _need(type(result) is ExecutionResult)
            _need(
                result.status
                in {"APPLIED", "REFUSED", "CONFLICT", "DEFERRED", "UNSETTLED"}
            )
            value = {
                "schema": "vuoro-protected-execution-result/v1",
                "profile_id": arguments.profile_id,
                "status": result.status,
                "reason": result.reason,
                "effect_key": result.effect_key,
                "outcome": _read(result.outcome_utf8, 65536)
                if result.outcome_utf8 is not None
                else None,
            }
            code = 0 if result.status == "APPLIED" else 2
    _emit(value)
    return code


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "install-journal":
            install_profile_journal(arguments.profile_id)
            _emit(
                {
                    "schema": "vuoro-protected-journal-installation/v1",
                    "profile_id": arguments.profile_id,
                    "status": "INSTALLED",
                }
            )
            return 0
        return asyncio.run(_run(arguments))
    except (Exception, KeyboardInterrupt):
        # This says nothing about whether a historical effect was published.
        # The durable journal and a fresh observation retain that distinction.
        _emit(
            {
                "schema": "vuoro-protected-runtime-unavailable/v1",
                "status": "UNAVAILABLE",
                "reason": "protected-runtime-unavailable",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
