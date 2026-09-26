"""Toolset builder owned by E3 (agentops#2467): propose_effect and effect status -- bucket "propose" (vuoro:effect.propose -> effect:propose). Diff-shaped intents only; no code path here may execute an effect.

Returns `None` (no tools) until its work item lands.  Only the owning work
item edits this module; see docs/plans/2026-09-26-e2-e3-shared-contract.md.
"""

from __future__ import annotations

from .toolsets import ToolSet, ToolsetContext

__all__ = ["build_toolset"]


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    return None
