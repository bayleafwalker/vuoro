from __future__ import annotations

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
