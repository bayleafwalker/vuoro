from __future__ import annotations

import json
from pathlib import Path

import pytest

from vuoro_client import __version__
from vuoro_client.cli import build_parser, main


def test_parser_is_transport_generic() -> None:
    parser = build_parser()
    assert parser.prog == "vuoro"
    assert "schema-described operations" in parser.description


def test_version_is_available_without_service_imports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--version"])
    assert capsys.readouterr().out.strip() == f"vuoro {__version__}"


def test_recovery_begin_observe_export_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VUORO_RECOVERY_ROOT", str(tmp_path))
    assert main(["recovery", "begin", "--incident", "inc-1"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "recovery",
                "observe",
                "--incident",
                "inc-1",
                "--summary",
                "service unreachable",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "recovery",
                "request-command",
                "--incident",
                "inc-1",
                "--summary",
                "ask for rollback",
                "--command",
                json.dumps({"command_type": "rollback", "params": {}}),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["recovery", "export", "--incident", "inc-1"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert len(exported) == 2
    assert exported[1]["requested_command"] == {"command_type": "rollback", "params": {}}
