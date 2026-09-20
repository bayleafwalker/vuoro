"""Wrap the internal MCP server's eight tools as Managed Agents custom
tools (§2: self-hosted sandbox workers register tool functions with the
Managed Agents SDK; each function here fulfils its call by making one MCP
request to the internal, host-local endpoint via `MCPClient`).

Every wrapped tool is fast and non-interactive (§2's first constraint --
sessions expire while waiting, so nothing here blocks on anything but the
internal MCP round trip) and takes ordinary JSON arguments only; long work
is represented by returning a `lease_id`/`chain_seq` handle for the model to
poll or extend via `heartbeat`, never by the tool call itself running long.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vuoro_worker.internal_tools import TOOL_ORDER
from vuoro_worker.mcp_client import MCPClient, MCPClientError


@dataclass(frozen=True)
class CustomTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[[Mapping[str, Any]], Mapping[str, Any]]


#: JSON-schema `input_schema` per tool, hand-written to match
#: `internal_tools.InternalToolServer`'s keyword parameters exactly (minus
#: `token`, which the poller supplies out of band -- see
#: `poller.py::Poller._dispatch`, never as a model-visible argument).
_INPUT_SCHEMAS: Mapping[str, Mapping[str, Any]] = {
    "list_ready_work": {"type": "object", "properties": {}, "additionalProperties": False},
    "describe_work": {
        "type": "object",
        "properties": {"subject": {"type": "string"}},
        "required": ["subject"],
        "additionalProperties": False,
    },
    "claim_work": {
        "type": "object",
        "properties": {"subject": {"type": "string"}},
        "required": ["subject"],
        "additionalProperties": False,
    },
    "heartbeat": {
        "type": "object",
        "properties": {"lease_id": {"type": "string"}},
        "required": ["lease_id"],
        "additionalProperties": False,
    },
    "append_evidence": {
        "type": "object",
        "properties": {
            "lease_id": {"type": "string"},
            "kind": {"type": "string"},
            "ref": {"type": "string"},
            "claim_type": {"type": "string"},
        },
        "required": ["lease_id", "kind", "ref"],
        "additionalProperties": False,
    },
    "write_session_note": {
        "type": "object",
        "properties": {"lease_id": {"type": "string"}, "note": {"type": "string"}},
        "required": ["lease_id", "note"],
        "additionalProperties": False,
    },
    "propose_effect": {
        "type": "object",
        "properties": {
            "lease_id": {"type": "string"},
            "description": {"type": "object"},
        },
        "required": ["lease_id", "description"],
        "additionalProperties": False,
    },
    "complete_work": {
        "type": "object",
        "properties": {"lease_id": {"type": "string"}},
        "required": ["lease_id"],
        "additionalProperties": False,
    },
}


def build_custom_tools(client: MCPClient) -> tuple[CustomTool, ...]:
    """One `CustomTool` per entry in `TOOL_ORDER`, in that fixed order.
    Descriptions come from the live server (`discover`), not a second copy
    hand-maintained here -- if the internal server is unreachable this
    raises `MCPClientError`, which the poller's preflight check surfaces as
    "skip this cycle", per §2's second constraint."""
    descriptors = {tool.name: tool.description for tool in client.discover()}
    missing = [name for name in TOOL_ORDER if name not in descriptors]
    if missing:
        raise MCPClientError(f"internal MCP server is missing tools: {missing}")

    def make_handler(name: str) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
        def handler(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            return client.call_tool(name, **arguments)

        return handler

    return tuple(
        CustomTool(
            name=name,
            description=descriptors[name],
            input_schema=_INPUT_SCHEMAS[name],
            handler=make_handler(name),
        )
        for name in TOOL_ORDER
    )
