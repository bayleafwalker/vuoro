from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[3]


def _load_dispatch_config() -> dict:
    return json.loads(
        (ROOT / "vuoro.dispatch.json").read_text(encoding="utf-8")
    )


def test_hybrid_is_enabled() -> None:
    config = _load_dispatch_config()
    assert config["hybrid"]["enabled"] is True


def test_boundaries_command_builds_wheels_before_testing() -> None:
    config = _load_dispatch_config()
    cmd = config["hybrid"]["commands"]["vuoro.boundaries"]
    assert isinstance(cmd, str)
    assert "uv build --package vuoro-client" in cmd
    assert "uv build --package vuoro-service" in cmd
    client_pos = cmd.index("uv build --package vuoro-client")
    service_pos = cmd.index("uv build --package vuoro-service")
    pytest_pos = cmd.index("pytest")
    assert client_pos < pytest_pos, "vuoro-client build must precede pytest"
    assert service_pos < pytest_pos, "vuoro-service build must precede pytest"


def test_protected_paths_guard_authority_and_packaging() -> None:
    config = _load_dispatch_config()
    paths = config["hybrid"]["protected_paths"]
    assert "deploy/**" in paths
    assert "packages/vuoro-client/src/**" in paths
    assert "vuoro.dispatch.json" in paths
