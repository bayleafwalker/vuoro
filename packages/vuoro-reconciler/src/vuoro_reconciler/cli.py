"""`vuoro-reconciler accept|reject <intent_id>`: an operator's decision.

This is the default `proposed -> accepted` path (TS-16): an operator on the
trusted side reads the intent and its diff and records
`{kind: "operator", subject}`. It is never reachable from the edge/MCP
surface, and the proposing principal cannot accept its own intent.

`--intent-source module:factory` names a trusted-side callable returning an
`IntentSource`. Trust assumption: acceptance authority is whoever holds
that source's credentials on the trusted side. `--operator` is a
*self-asserted* subject -- recorded for attribution and compared against
`proposer_principal` (same principal namespace), not authenticated here.

Everything shown comes from the proposer, so it is displayed with every
non-printable character escaped visibly (`\x1b`, `\r`, `\u202e`, ...):
ESC/CR sequences or bidi overrides cannot hide or reorder diff lines on
the operator's terminal.
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

__all__ = ["main", "visible"]


def visible(text: str) -> str:
    """`text` with every non-printable character -- C0/C1 controls (ESC,
    CR, ...), DEL, bidi overrides/isolates (U+202A-202E, U+2066-2069) and
    other format or separator characters -- shown as an escape. Newline and
    tab are kept: they cannot overwrite or reorder what is already shown."""

    out = []
    for char in text:
        if char in "\n\t" or char.isprintable():
            out.append(char)
        elif ord(char) < 0x100:
            out.append(f"\\x{ord(char):02x}")
        elif ord(char) < 0x10000:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(f"\\U{ord(char):08x}")
    return "".join(out)


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
    v = visible
    # The title is one line: a newline in it is escaped too.
    title = v(intent.title).replace("\n", "\\n")
    out.write(
        f"intent:     {v(intent.intent_id)}\n"
        f"run:        {v(intent.run_id)}\n"
        f"workspace:  {v(intent.workspace_id)}\n"
        f"repository: {v(intent.repository)} @ {v(intent.base_commit)}\n"
        f"proposer:   {v(intent.proposer_principal)}\n"
        f"kind:       {v(intent.effect_kind)}\n"
        f"title:      {title}\n\n"
        f"{v(intent.rationale)}\n\n"
        f"{v(intent.unified_diff)}"
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
        stdout.write(f"refused: {visible(str(refused))}\n")
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
        stdout.write(f"refused: {visible(str(refused))}\n")
        return 1
    stdout.write(f"{arguments.command}ed by {visible(acceptor.trailer_value())}\n")
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
