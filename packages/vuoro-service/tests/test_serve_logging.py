"""Sub-build 1c: confirm the ``serve`` entrypoint does not enable request-body
or credential-bearing logging.

``uvicorn.run``'s default access-log formatter only ever emits
``client_addr``, the request line (method + path + protocol), and the status
code — never headers or the request body, so a transient credential value
(which only ever travels in the JSON body) cannot reach it. This test proves
the CLI's ``serve`` command does not override that default with a custom
``log_config`` or ``access_log`` setting that could start logging bodies, and
proves no deployment asset configures logging either.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vuoro_service.cli as cli_module


CLI_SOURCE_PATH = Path(cli_module.__file__)
REPO_ROOT = Path(__file__).parents[3]


def _find_uvicorn_run_call(tree: ast.AST) -> ast.Call:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
        ):
            return node
    raise AssertionError("expected to find a uvicorn.run(...) call in cli.py")


def test_serve_uses_only_host_port_and_factory_kwargs() -> None:
    """No ``log_config``/``access_log`` override is present, so uvicorn's
    default access-log format (method/path/status only, never the body or
    headers) is what actually governs this deployment."""

    tree = ast.parse(CLI_SOURCE_PATH.read_text(encoding="utf-8"))
    call = _find_uvicorn_run_call(tree)
    keyword_names = {keyword.arg for keyword in call.keywords}
    assert keyword_names <= {"host", "port", "factory"}
    assert "log_config" not in keyword_names
    assert "access_log" not in keyword_names


def test_no_checked_in_deployment_asset_configures_logging() -> None:
    """The kustomize/compose deployment surface does not set any env var or
    flag that could turn on uvicorn body/header logging; if it ever does,
    this test should be extended to assert the new configuration is safe."""

    deploy_dir = REPO_ROOT / "deploy"
    assert deploy_dir.is_dir()
    hits = []
    for path in deploy_dir.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        for needle in ("log_config", "access_log", "uvicorn_log"):
            if needle in lowered:
                hits.append((path, needle))
    assert hits == []
