import json
from pathlib import Path
import stat
import os
import subprocess
import sys
from contextlib import contextmanager

import pytest

from vuoro_demo.cli import main
from vuoro_demo.scenario import Caller


@pytest.mark.parametrize("endpoint", ["https://production.example", "http://localhost:80",
    "http://127.0.0.1:80/remote", "http://user:secret@127.0.0.1:80",
    "http://127.0.0.1:80?token=value", "http://[::1]:80"])
def test_nonfixture_endpoint_refused_before_transport(endpoint):
    with pytest.raises(ValueError, match="owned loopback"):
        Caller(endpoint, "fixture", [])


def test_existing_receipt_refuses_before_fixture_start(tmp_path, monkeypatch):
    path = tmp_path / "proof.json"
    path.write_text("preserve historical proof")
    monkeypatch.setattr("vuoro_demo.cli.fixture", lambda *_: pytest.fail("started fixture"))
    assert main(["demo", "--receipt", str(path)]) == 1
    assert path.read_text() == "preserve historical proof"


def test_symlink_receipt_refuses_before_fixture_start(tmp_path, monkeypatch):
    historical = tmp_path / "historical.json"
    historical.write_text("preserve")
    path = tmp_path / "proof.json"
    path.symlink_to(historical)
    monkeypatch.setattr("vuoro_demo.cli.fixture", lambda *_: pytest.fail("started fixture"))
    assert main(["demo", "--receipt", str(path)]) == 1
    assert historical.read_text() == "preserve"


def test_unavailable_pg_tools_report_incomplete_without_external_fallback(tmp_path):
    receipt = tmp_path / "proof.json"
    assert main(["demo", "--receipt", str(receipt), "--pg-bin", str(tmp_path)]) == 1
    report = json.loads(receipt.read_text())
    assert report["status"] == "incomplete"
    assert report["failure_type"] == "ValueError"
    assert not report["steps"]
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600


def test_uncertain_cleanup_retains_private_scratch_and_never_passes(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    root.mkdir()
    (root / "diagnostic").write_text("preserve")
    monkeypatch.setattr("vuoro_demo.cli.tempfile.mkdtemp", lambda **_: str(root))

    @contextmanager
    def failed_fixture(*_, state):
        state["cleanup"] = "failed"
        raise RuntimeError("owned fixture shutdown uncertain")
        yield  # Make the failure occur during context entry.

    monkeypatch.setattr("vuoro_demo.cli.fixture", failed_fixture)
    receipt = tmp_path / "receipt.json"
    assert main(["demo", "--receipt", str(receipt)]) == 1
    assert root.is_dir() and (root / "diagnostic").read_text() == "preserve"
    report = json.loads(receipt.read_text())
    assert report["status"] == "incomplete" and report["scratch_retained"] == str(root)


@pytest.mark.parametrize("option", ["--pg-url", "--endpoint", "--profile", "--mode"])
def test_no_live_backend_input(option, tmp_path):
    with pytest.raises(SystemExit):
        main(["demo", "--receipt", str(tmp_path / "proof.json"), option, "live"])
    assert not (tmp_path / "proof.json").exists()


@pytest.mark.skipif(not os.environ.get("VUORO_DEMO_TEST_PG_BIN"), reason="explicit owned PG16 integration tools required")
@pytest.mark.parametrize("falsifier", [None, "--omit-required-verification", "--omit-required-check"])
def test_installed_checkoutless_owner_protocol(falsifier, tmp_path):
    receipt = tmp_path / "receipt.json"
    arguments = [str(Path(sys.executable).parent / "vuoro"), "demo", "--receipt", str(receipt),
        "--pg-bin", os.environ["VUORO_DEMO_TEST_PG_BIN"]]
    if falsifier:
        arguments.append(falsifier)
    environment = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
    run = subprocess.run(arguments, cwd=tmp_path, env=environment, capture_output=True, timeout=120)
    report = json.loads(receipt.read_text())
    if falsifier:
        assert run.returncode != 0 and report["status"] == "incomplete"
        expected = "verification-unsatisfied" if falsifier.endswith("check") else "effect-verification-required"
        assert any(step.get("refusal") == expected for step in report["steps"])
        assert not any(step.get("result", {}).get("settlement_effect") == "settled" for step in report["steps"])
        assert "result" not in report
    else:
        assert run.returncode == 0 and report["status"] == "passed"
        source = report["result"]["source"]
        decision = report["result"]["decision"]
        assert source["commit"] == source["causal_basis"]["commit_sha"]
        assert decision["release_digest"] == source["release"]["release_digest"]
        assert decision["evidence_digests"] == [report["result"]["outcome_payload_digest"]]
        assert source["verification_receipt"] == source["accepted_intent"]["acceptance"]["verification"]["receipt"]
        assert any(step.get("signal") == "SIGKILL" for step in report["steps"])
        assert any(step.get("refusal") == "claim-superseded" for step in report["steps"])
        assert report["cleanup"] == "owned shell and PostgreSQL stopped"
