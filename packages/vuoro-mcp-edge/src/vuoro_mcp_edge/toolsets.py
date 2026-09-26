"""Toolsets: how write-class tools join the edge without editing `server.py`.

The two read tools are built into `server.py`.  E2 (record and coordinate:
runs, evidence, session notes, claims) and E3 (propose: effect intents) add
their tools as `ToolSet`s, each built in its own module
(`record_tools`, `claim_tools`, `effect_tools`) and passed to
`create_edge_app(toolsets=...)`.  The server merges them after the built-in
tools, in the order given, and refuses a toolset that would shadow a tool
name or name a bucket it has no authority for.

A toolset tool is dispatched exactly like a built-in one: the caller's
assertion must carry the bucket's authority, the result is wrapped in the
2026-07-28 envelope (`resultType: "complete"`), and a `ToolFailure` raised by
the handler becomes a tool error result, never a JSON-RPC error.

See docs/plans/2026-09-26-e2-e3-shared-contract.md for the ownership split.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runs import RunRegistry
    from .work_source import ForwardedIdentity, ShellWorkSource

__all__ = [
    "BUCKET_AUTHORITIES",
    "ToolFailure",
    "ToolSet",
    "ToolSpec",
    "ToolsetContext",
    "WRITE_ANNOTATIONS",
]

#: Scope bucket -> the gateway assertion authority it requires.  The gateway
#: mints these from OAuth scopes (vuoro-cloud `oauth_scopes.SCOPE_TO_AUTHORITIES`):
#: vuoro:work.read -> work:read, vuoro:evidence.record -> work:evidence,
#: vuoro:work.claim -> work:claim, vuoro:effect.propose -> effect:propose.
#: There is no "apply" bucket, by construction: vuoro:effect.apply is never
#: granted and no edge tool may execute an effect.
BUCKET_AUTHORITIES: dict[str, str] = {
    "read": "work:read",
    "record": "work:evidence",
    "coordinate": "work:claim",
    "propose": "effect:propose",
}

#: MCP tool annotations for a write-class tool that is safe to retry with the
#: same idempotency key.
WRITE_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class ToolFailure(Exception):
    """A tool-level failure: reported to the caller as `isError: true`.

    `code` is a stable machine-readable string (e.g. `run-not-found`,
    `idempotency-conflict`, `repository-mismatch`); `message` is local text,
    never upstream detail.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


Parse = Callable[[dict[str, Any]], Any]
Run = Callable[[Any, "ForwardedIdentity"], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    """One callable tool: its MCP definition, its bucket and its handler.

    `parse` validates `arguments` and returns the parsed value (raise
    `ToolFailure("invalid-arguments", ...)` on bad input); `run` performs the
    call and returns the tool's structured content.
    """

    name: str
    bucket: str
    definition: Mapping[str, Any]
    parse: Parse
    run: Run

    def __post_init__(self) -> None:
        if self.definition.get("name") != self.name:
            raise ValueError(f"tool {self.name}: definition name must match")
        if self.bucket not in BUCKET_AUTHORITIES:
            raise ValueError(f"tool {self.name}: unknown bucket {self.bucket!r}")


@dataclass(frozen=True)
class ToolSet:
    """A named group of tools owned by one work item (E2 or E3)."""

    name: str
    tools: tuple[ToolSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ToolsetContext:
    """What a toolset builder may use.  Holds no credential: the edge has none.

    `env` is the process environment (builders read their own settings from
    it, prefixed with their toolset name); `work_source` is the runtime shell
    client; `runs` resolves E2 run handles (an unavailable registry until E2
    ships one, so E3 code paths refuse cleanly rather than guess).
    """

    env: Mapping[str, str]
    work_source: ShellWorkSource
    runs: RunRegistry


def merge_toolsets(
    builtin_order: Sequence[str], toolsets: Sequence[ToolSet]
) -> tuple[tuple[str, ...], dict[str, ToolSpec]]:
    """The final tool order and the toolset tools by name.

    Built-in tools come first, then each toolset's tools in the given order.
    A name collision (with a built-in or another toolset) is a startup error.
    """

    order = list(builtin_order)
    specs: dict[str, ToolSpec] = {}
    for toolset in toolsets:
        for spec in toolset.tools:
            if spec.name in order:
                raise ValueError(
                    f"toolset {toolset.name}: tool {spec.name} is already registered"
                )
            order.append(spec.name)
            specs[spec.name] = spec
    return tuple(order), specs
