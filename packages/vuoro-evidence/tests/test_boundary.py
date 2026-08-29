"""The step-7.4 criterion, checked mechanically: zero profile/adapter/host-class
knowledge in the core reducer and decision path.

Rewritten 2026-08-29 from the recovered bytecode's intent (see ../RECOVERY.md);
this is not the original source.
"""
from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "vuoro_evidence" / "core"

# Every term below names a host, a wire profile, an adapter or a carrier. The
# core reduces claims; if it can name where a claim came from, the boundary the
# package exists to hold has already been crossed.
FORBIDDEN = (
    "hostproto", "outctl", "command_capture", "command-capture",
    "mcp", "a2a", "debugpy", "delve", "chromium", "browser", "playwright",
    "receipt", "spool", "manifest", "adapter", "profile", "surface", "capture",
)


def _core_modules() -> list[Path]:
    modules = sorted(p for p in CORE.glob("*.py"))
    assert modules, f"no core modules found under {CORE}"
    return modules


def test_core_has_no_host_or_profile_vocabulary() -> None:
    offences = []
    for path in _core_modules():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            lowered = line.lower()
            for term in FORBIDDEN:
                if term in lowered:
                    offences.append(f"{path.name}:{lineno}: {term!r} in {line.strip()!r}")
    assert not offences, "host/profile vocabulary leaked into core:\n" + "\n".join(offences)


def test_core_does_not_import_ingress() -> None:
    offences = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level>0 is a relative import; module is None for `from . import x`
                names = [node.module or ""] + [a.name for a in node.names]
            else:
                continue
            for name in names:
                if "ingress" in name:
                    offences.append(f"{path.name}:{node.lineno}: imports {name!r}")
    assert not offences, "core imports ingress:\n" + "\n".join(offences)
