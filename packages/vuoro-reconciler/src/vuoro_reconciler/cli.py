"""`vuoro-reconciler accept|reject <intent_id>`: an operator's decision.

This is the default `proposed -> accepted` path (TS-16): an operator on the
trusted side reads the intent and its diff and records
`{kind: "operator", subject}`. It is never reachable from the edge/MCP
surface, and the proposing principal cannot accept its own intent.

`--intent-source module:factory` names a trusted-side callable returning an
`IntentSource`; whoever can run this CLI with that source's credentials is
the separately authenticated actor. `--operator` is that actor's subject,
in the same principal namespace as the intent's `proposer_principal`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO
import argparse
import asyncio
import importlib
import sys

from .acceptance import AcceptanceRefused, OperatorAcceptance
from .intents import EffectIntent, IntentSource

__all__ = ["main"]


def _load_source(spec: str) -> IntentSource:
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise SystemExit("--intent-source must be module:factory")
    factory = getattr(importlib.import_module(module_name), attribute)
    return factory()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vuoro-reconciler")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("accept", "reject"):
        command = commands.add_parser(name, help=f"{name} a proposed effect intent")
        command.add_argument("intent_id")
        command.add_argument("--operator", required=True, help="the operator's subject")
        command.add_argument("--intent-source", help="module:factory returning an IntentSource")
        command.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
        if name == "reject":
            command.add_argument("--reason", required=True)
    return parser


def _show(intent: EffectIntent, out: TextIO) -> None:
    out.write(
        f"intent:     {intent.intent_id}\n"
        f"run:        {intent.run_id}\n"
        f"workspace:  {intent.workspace_id}\n"
        f"repository: {intent.repository} @ {intent.base_commit}\n"
        f"proposer:   {intent.proposer_principal}\n"
        f"kind:       {intent.effect_kind}\n"
        f"title:      {intent.title}\n\n"
        f"{intent.rationale}\n\n"
        f"{intent.unified_diff}"
    )
    if not intent.unified_diff.endswith("\n"):
        out.write("\n")


async def _run(
    arguments: argparse.Namespace, source: IntentSource, stdin: TextIO, stdout: TextIO
) -> int:
    acceptance = OperatorAcceptance(source)
    try:
        intent = await acceptance.pending(arguments.intent_id)
    except AcceptanceRefused as refused:
        stdout.write(f"refused: {refused}\n")
        return 1
    _show(intent, stdout)
    if not arguments.yes:
        stdout.write(f"\n{arguments.command} this intent? [y/N] ")
        stdout.flush()
        if stdin.readline().strip().lower() not in ("y", "yes"):
            stdout.write("nothing recorded\n")
            return 1
    try:
        if arguments.command == "accept":
            acceptor = await acceptance.accept_interactive(intent.intent_id, arguments.operator)
        else:
            acceptor = await acceptance.reject_interactive(
                intent.intent_id, arguments.operator, arguments.reason
            )
    except AcceptanceRefused as refused:
        stdout.write(f"refused: {refused}\n")
        return 1
    stdout.write(f"{arguments.command}ed by {acceptor.trailer_value()}\n")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    intent_source: IntentSource | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    if intent_source is None:
        if not arguments.intent_source:
            raise SystemExit("--intent-source is required")
        intent_source = _load_source(arguments.intent_source)
    return asyncio.run(
        _run(arguments, intent_source, stdin or sys.stdin, stdout or sys.stdout)
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
