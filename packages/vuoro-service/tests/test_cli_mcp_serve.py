"""`vuoro-service mcp-serve` and the separation of the shell from the MCP server."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import vuoro_service
from vuoro_service import cli

SRC = Path(vuoro_service.__file__).parent


def test_mcp_serve_defaults_to_port_8081_on_loopback() -> None:
    args = cli.build_parser().parse_args(["mcp-serve"])
    assert (args.command, args.host, args.port) == ("mcp-serve", "127.0.0.1", 8081)


def test_mcp_serve_accepts_host_and_port() -> None:
    args = cli.build_parser().parse_args(["mcp-serve", "--host", "0.0.0.0", "--port", "9000"])
    assert (args.host, args.port) == ("0.0.0.0", 9000)


def test_mcp_serve_runs_the_edge_factory(monkeypatch) -> None:
    import importlib.util

    import uvicorn

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert cli.main(["mcp-serve", "--port", "8081"]) == 0
    ((args, kwargs),) = calls
    assert args == ("vuoro_mcp_edge.composition:create_app_from_environment",)
    assert kwargs == {"factory": True, "host": "127.0.0.1", "port": 8081}


def test_the_factory_string_resolves_to_the_edge_factory() -> None:
    """What uvicorn will import at run time, imported the same way."""

    from uvicorn.importer import import_from_string

    factory = import_from_string(cli.MCP_APP_FACTORY)
    from vuoro_mcp_edge.composition import create_app_from_environment

    assert factory is create_app_from_environment
    assert callable(factory)


def test_mcp_serve_without_the_edge_package_exits_2(monkeypatch, capsys) -> None:
    import importlib.util

    import uvicorn

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not start"))
    assert cli.main(["mcp-serve"]) == 2
    assert "vuoro-mcp-edge" in capsys.readouterr().err


def test_importing_the_cli_does_not_import_the_edge() -> None:
    probe = (
        "import sys, vuoro_service.cli, vuoro_service.composition, vuoro_service.app; "
        "print(any(m.startswith('vuoro_mcp_edge') for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "False"


@pytest.mark.parametrize("module", ["app.py", "composition.py"])
def test_the_runtime_shell_never_references_the_mcp_surface(module) -> None:
    source = (SRC / module).read_text(encoding="utf-8")
    hits = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(source.splitlines(), start=1)
        if re.search("mcp", line, re.IGNORECASE)
    ]
    assert hits == [], f"{module} mentions mcp: {hits}"
