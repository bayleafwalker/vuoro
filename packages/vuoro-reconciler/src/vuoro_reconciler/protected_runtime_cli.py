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
from .protected_runtime_factory import (
    install_profile_journal,
    open_installed_runtime,
    rebind_empty_profile_journal,
    read_profile_journal_rebind_receipt,
)
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


def _sha256(value):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("invalid SHA-256 digest")
    return value


def _operation_id(value):
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise argparse.ArgumentTypeError("invalid operation ID")
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
    rebind = commands.add_parser(
        "rebind-empty-journal",
        help="explicitly bind a verified empty journal to the installed registry",
    )
    rebind.add_argument("profile_id", type=_profile)
    rebind.add_argument("--expected-old-registry-sha256", required=True, type=_sha256)
    rebind.add_argument("--expected-profile-sha256", required=True, type=_sha256)
    rebind.add_argument("--expected-host-id", required=True, type=_sha256)
    rebind.add_argument("--operation-id", required=True, type=_operation_id)
    receipt = commands.add_parser(
        "journal-rebind-receipt", help="read a committed empty-journal registry transition receipt"
    )
    receipt.add_argument("profile_id", type=_profile)
    receipt.add_argument("operation_id", type=_operation_id)
    receipt.add_argument("--expected-profile-sha256", required=True, type=_sha256)
    receipt.add_argument("--expected-host-id", required=True, type=_sha256)
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
        if arguments.command == "rebind-empty-journal":
            receipt = rebind_empty_profile_journal(
                arguments.profile_id,
                expected_old_registry_sha256=arguments.expected_old_registry_sha256,
                expected_profile_sha256=arguments.expected_profile_sha256,
                expected_host_id=arguments.expected_host_id,
                operation_id=arguments.operation_id,
            )
            _emit(
                {
                    "schema": "vuoro-protected-journal-registry-rebind/v1",
                    "status": "REBOUND",
                    **receipt,
                }
            )
            return 0
        if arguments.command == "journal-rebind-receipt":
            value = read_profile_journal_rebind_receipt(
                arguments.profile_id,
                operation_id=arguments.operation_id,
                expected_profile_sha256=arguments.expected_profile_sha256,
                expected_host_id=arguments.expected_host_id,
            )
            if value is None:
                _emit(
                    {
                        "schema": "vuoro-protected-journal-registry-rebind-receipt/v1",
                        "status": "NOT_FOUND",
                        "profile_id": arguments.profile_id,
                        "operation_id": arguments.operation_id,
                    }
                )
                return 2
            _emit(
                {
                    "schema": "vuoro-protected-journal-registry-rebind-receipt/v1",
                    "status": "COMMITTED",
                    **value,
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
