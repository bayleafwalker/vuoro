"""Toolset builder owned by E2 (agentops#2466): register_run, append_evidence, write_session_note -- bucket "record" (vuoro:evidence.record -> work:evidence).

Returns `None` (no tools) until its work item lands.  Only the owning work
item edits this module; see docs/plans/2026-09-26-e2-e3-shared-contract.md.
"""

from __future__ import annotations

from .toolsets import ToolSet, ToolsetContext

__all__ = ["build_toolset"]


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    return None
