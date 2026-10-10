import json
from pathlib import Path
import stat
import os
import subprocess
import sys
import signal
import time
from importlib.metadata import PackageNotFoundError
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


def test_missing_metadata_is_handled_with_private_incomplete_receipt(tmp_path, monkeypatch):
    def missing(_name):
        raise PackageNotFoundError("broken fixture installation")
    monkeypatch.setattr("vuoro_demo.cli.version", missing)
    original = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    receipt = tmp_path / "receipt.json"
    assert main(["demo", "--receipt", str(receipt)]) == 1
    report = json.loads(receipt.read_text())
    assert report["status"] == "incomplete" and report["failure_type"] == "PackageNotFoundError"
    assert report["versions"] == {} and not report["steps"]
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert {s: signal.getsignal(s) for s in original} == original


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_normal_signal_restores_prior_handlers_and_records_incomplete(tmp_path, monkeypatch, signum):
    original = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    @contextmanager
    def interrupted_fixture(*_, state):
        os.kill(os.getpid(), signum)
        try:
            yield "http://127.0.0.1:1234", {}
        finally:
            state["cleanup"] = "complete"

    monkeypatch.setattr("vuoro_demo.cli.fixture", interrupted_fixture)
    receipt = tmp_path / "receipt.json"
    assert main(["demo", "--receipt", str(receipt)]) == 1
    report = json.loads(receipt.read_text())
    assert report["status"] == "incomplete" and report["interruption"] == signum.name
    assert report["fixture_cleanup"] == "complete"
    assert {s: signal.getsignal(s) for s in original} == original


def test_signal_during_synchronous_scratch_cleanup_records_incomplete(tmp_path, monkeypatch):
    @contextmanager
    def finished_fixture(*_, state):
        yield "http://127.0.0.1:1234", {}
        state["cleanup"] = "complete"

    async def finished_scenario(*_, **__):
        return {"fixture": "test-only"}

    import shutil
    remove = shutil.rmtree

    def interrupted_cleanup(root):
        os.kill(os.getpid(), signal.SIGTERM)
        remove(root)

    monkeypatch.setattr("vuoro_demo.cli.fixture", finished_fixture)
    monkeypatch.setattr("vuoro_demo.cli.scenario", finished_scenario)
    monkeypatch.setattr("vuoro_demo.cli.shutil.rmtree", interrupted_cleanup)
    receipt = tmp_path / "receipt.json"
    assert main(["demo", "--receipt", str(receipt)]) == 1
    report = json.loads(receipt.read_text())
    assert report["status"] == "incomplete" and report["interruption"] == "SIGTERM"
    assert report["fixture_cleanup"] == "complete" and "scratch_retained" not in report


@pytest.mark.parametrize("option", ["--pg-url", "--endpoint", "--profile", "--mode"])
def test_no_live_backend_input(option, tmp_path):
    with pytest.raises(SystemExit):
        main(["demo", "--receipt", str(tmp_path / "proof.json"), option, "live"])
    assert not (tmp_path / "proof.json").exists()


@pytest.mark.skipif(not os.environ.get("VUORO_DEMO_TEST_PG_BIN"), reason="explicit owned PG16 integration tools required")
@pytest.mark.parametrize("falsifier", [None, "--omit-required-verification", "--omit-required-check", "--wrong-artifact-digest"])
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
        expected = {"--omit-required-check": "verification-unsatisfied",
            "--omit-required-verification": "effect-verification-required",
            "--wrong-artifact-digest": "effect-verification-refused"}[falsifier]
        assert any(step.get("refusal") == expected for step in report["steps"])
        assert not any(step.get("result", {}).get("settlement_effect") == "settled" for step in report["steps"])
        assert "result" not in report
        if falsifier == "--wrong-artifact-digest":
            decisions = [s["result"]["decisions"] for s in report["steps"]
                if s.get("operation") == "work.read.item-decisions" and "result" in s]
            ready = [s["result"]["ready_items"] for s in report["steps"]
                if s.get("operation") == "work.read.next-work" and "result" in s]
            assert decisions and all(not rows for rows in decisions)
            assert ready and all(not any(row["title"] == "Y depends on X" for row in rows) for rows in ready)
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


@pytest.mark.skipif(not os.environ.get("VUORO_DEMO_TEST_PG_BIN"), reason="explicit owned PG16 interruption tools required")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_installed_normal_interruption_stops_actual_owned_postgres(signum, tmp_path):
    receipt = tmp_path / "receipt.json"
    process = subprocess.Popen([str(Path(sys.executable).parent / "vuoro"), "demo",
        "--receipt", str(receipt), "--pg-bin", os.environ["VUORO_DEMO_TEST_PG_BIN"]],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ})
    owned_root = None
    pg_pid = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for marker in Path("/tmp").glob("vuoro-demo-*/owner.json"):
                try:
                    owner = json.loads(marker.read_text())
                except (OSError, ValueError):
                    continue
                if owner.get("pid") == process.pid and owner.get("receipt") == str(receipt):
                    owned_root = marker.parent
                    pid_file = owned_root / "postgres" / "postmaster.pid"
                    if owner.get("phase") == "running" and pid_file.exists():
                        pg_pid = int(pid_file.read_text().splitlines()[0])
                        os.kill(pg_pid, 0)  # Actual owned PG must be alive before interruption.
                    break
            if pg_pid is not None:
                break
            assert process.poll() is None, "demo exited before owned PG startup"
            time.sleep(.05)
        assert pg_pid is not None, "owned PostgreSQL did not start"
        # Reach the native consumer, rather than merely interrupting initdb.
        time.sleep(2)
        assert process.poll() is None
        process.send_signal(signum)
        process.communicate(timeout=30)
        assert process.returncode == 1
        report = json.loads(receipt.read_text())
        assert report["status"] == "incomplete" and report["interruption"] == signum.name
        assert report["fixture_cleanup"] == "complete"
        assert "scratch_retained" not in report and not owned_root.exists()
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
        with pytest.raises(ProcessLookupError):
            os.kill(pg_pid, 0)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=30)
        # If the oracle fails, stop only the marker-qualified cluster we own.
        if owned_root is not None and (owned_root / "postgres").exists():
            subprocess.run([str(Path(os.environ["VUORO_DEMO_TEST_PG_BIN"]) / "pg_ctl"),
                "-D", str(owned_root / "postgres"), "-m", "immediate", "-w", "stop"],
                capture_output=True, timeout=30, check=False)
