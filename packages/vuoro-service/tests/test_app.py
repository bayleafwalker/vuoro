from __future__ import annotations

from vuoro_service.app import create_app
from vuoro_service.cli import build_parser, main


def test_bootstrap_exposes_only_operational_probes() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/health/live" in paths
    assert "/health/ready" in paths
    assert "/api/meta/v1/handshake" in paths
    assert "/api/catalog/v1" in paths
    assert "/api/invoke/v1" in paths


def test_service_commands_are_process_scoped(capsys) -> None:
    parser = build_parser()
    assert {"serve", "check-compatibility", "migrate", "admin"} <= set(
        parser._subparsers._group_actions[0].choices
    )
    assert main(["check-compatibility"]) == 3
    assert '"compatible": false' in capsys.readouterr().out
