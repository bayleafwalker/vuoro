"""Explicit clean source-owner proof; immutable released composition is separate."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not all(os.environ.get(key) for key in (
    "VUORO_DELEGATION_OWNER_SOURCE", "VUORO_DELEGATION_OWNER_COMMIT", "SPRINTCTL_TEST_PG_URL")),
    reason="explicit clean delegation owner source/commit and guarded disposable PG database required")
def test_real_owner_digest_projection_and_cached_context_revocation():
    root = Path(__file__).resolve().parents[4]
    completed = subprocess.run([sys.executable, "-I", str(root / "scripts/qualify_run_delegation_owner.py"),
        "--owner-source", os.environ["VUORO_DELEGATION_OWNER_SOURCE"],
        "--owner-commit", os.environ["VUORO_DELEGATION_OWNER_COMMIT"]], capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "cached-context revocation conformance passed" in completed.stdout
