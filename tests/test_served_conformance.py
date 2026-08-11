from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

from vuoro_client import parse_bootstrap_manifest, parse_discovery
from vuoro_client.profile import load_profile


ROOT = Path(__file__).parents[1]


def test_cloud_served_fixtures_match_vuoro_contracts() -> None:
    fixture = ROOT / "tests" / "fixtures" / "served"
    discovery = parse_discovery(json.loads((fixture / "discovery.json").read_text()))
    manifest = parse_bootstrap_manifest(json.loads((fixture / "bootstrap-manifest.json").read_text()))
    profile = load_profile(fixture / "client-profile.json")
    assert discovery.environment_id == manifest.environment_id == profile.expected_environment
    assert manifest.packages.vuoro_client == "0.1.0"


def test_credential_free_served_conformance_gate() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_served_conformance.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "served conformance: PASS" in result.stdout
