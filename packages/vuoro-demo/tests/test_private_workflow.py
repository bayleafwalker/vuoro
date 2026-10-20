import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import pytest
from vuoro_demo.private_workflow import main

SOURCE = (
    Path(__file__).resolve().parents[3]
    / "docs/fixtures/private-workflow-maintenance.json"
)
MODEL = os.environ.get("VUORO_PRIVATE_TEST_MODEL")
PG = os.environ.get("VUORO_DEMO_TEST_PG_BIN")


def arguments(root, receipt):
    return [
        "--model-endpoint",
        "http://127.0.0.1:11531",
        "--model",
        MODEL or "explicit-model",
        "--model-digest",
        "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0",
        "--source",
        str(SOURCE),
        "--state-dir",
        str(root),
        "--receipt",
        str(receipt),
        "--pg-bin",
        PG or "/nonexistent/explicit-tools",
    ]


def test_previous_receipt_is_never_overwritten(tmp_path):
    receipt = tmp_path / "proof.json"
    receipt.write_text("historical proof")
    assert main(arguments(tmp_path / "state", receipt)) == 1
    assert receipt.read_text() == "historical proof"
    assert not (tmp_path / "state").exists()


@pytest.mark.skipif(
    not MODEL or not PG, reason="explicit owned local model and PG16 tools required"
)
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_cancellation_after_durable_dispatch_stops_owned_services(tmp_path, signum):
    root = tmp_path / "state"
    receipt = tmp_path / "proof.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vuoro_demo.private_workflow",
            *arguments(root, receipt),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 90
        while not (root / "workflow-progress.json").exists():
            assert process.poll() is None, "workflow exited before durable dispatch"
            if time.monotonic() > deadline:
                pytest.fail("durable dispatch timeout")
            time.sleep(0.05)
        pg_pid = int((root / "postgres/postmaster.pid").read_text().splitlines()[0])
        os.kill(pg_pid, 0)
        process.send_signal(signum)
        assert process.wait(40) == 1
        report = json.loads(receipt.read_text())
        assert (
            report["status"] == "incomplete" and report["fixture_cleanup"] == "complete"
        )
        assert report["interruption"] == signal.Signals(signum).name
        assert not (root / "postgres/postmaster.pid").exists()
        with pytest.raises(ProcessLookupError):
            os.kill(pg_pid, 0)
        assert "result" not in report
        for child_pid in json.loads((root / "sovite/owned-processes.json").read_text())[
            "pids"
        ]:
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(40)


def test_unrelated_c0_source_refused_before_model_contact(tmp_path, monkeypatch):
    import vuoro_demo.private_workflow as workflow

    evaluation = pytest.importorskip("sovite.local_evaluation")
    monkeypatch.setattr(workflow, "version", lambda name: workflow.VERSIONS[name])
    monkeypatch.setattr(
        evaluation, "local_model", lambda *_: pytest.fail("contacted model")
    )
    source = tmp_path / "unrelated.json"
    source.write_text(
        json.dumps({"classification": "C0", "synthetic": True, "snippets": []})
    )
    args = arguments(tmp_path / "state", tmp_path / "proof.json")
    args[args.index("--source") + 1] = str(source)
    assert main(args) == 1
    report = json.loads((tmp_path / "proof.json").read_text())
    assert report["failure_type"] == "VerificationRefused"
    assert not (tmp_path / "state").exists()
