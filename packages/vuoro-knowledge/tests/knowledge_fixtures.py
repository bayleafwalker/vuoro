from __future__ import annotations

# Not named conftest.py: sibling packages import their own ``conftest`` by
# module name, and a second one on sys.path would shadow it in a workspace run.

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def estate(tmp_path: Path) -> dict[str, Path]:
    """The Kctl case: July plan, September register, October note, as separate roots."""

    target = tmp_path / "estate"
    shutil.copytree(FIXTURES / "estate", target)
    return {name: target / name for name in ("vuoro", "kctl", "agentops")}


@pytest.fixture
def write(tmp_path: Path):
    def _write(relative: str, text: str) -> Path:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    return _write

