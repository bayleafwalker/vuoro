"""Required migration proof must fail rather than skip if psycopg is missing."""
import os
from pathlib import Path
import subprocess
import sys


def test_required_proof_refuses_missing_driver():
    target = Path(__file__).resolve().parents[1] / 'packages/vuoro-service/tests/conformance/test_schema30_owner_migration.py'
    code = '''
import runpy, sys, types
pytest = types.ModuleType("pytest")
def skip(*args, **kwargs):
    raise RuntimeError("unexpected optional skip")
pytest.importorskip = skip
sys.modules["pytest"] = pytest
runpy.run_path(sys.argv[1])
'''
    result = subprocess.run([sys.executable, '-S', '-c', code, str(target)],
                            env={**os.environ, 'VUORO_SCHEMA30_PROOF': '1'},
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert "No module named 'psycopg'" in result.stderr
    assert 'unexpected optional skip' not in result.stderr
