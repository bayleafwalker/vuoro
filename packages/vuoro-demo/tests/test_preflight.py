"""Prerequisite diagnosis must not acquire a database or fixture authority."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from vuoro_demo.cli import main
from vuoro_demo import preflight


@pytest.fixture
def tools(tmp_path, monkeypatch):
    for name, text in {"git": "git version 2.43.0",
            "initdb": "initdb (PostgreSQL) 16.10",
            "pg_ctl": "pg_ctl (PostgreSQL) 16.10"}.items():
        path = tmp_path / name
        # Reject any command other than a version probe, including initdb -D.
        path.write_text(f'#!/bin/sh\n[ "$#" = 1 ] && [ "$1" = --version ] || exit 42\nprintf "%s\\n" "{text}"\n')
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(preflight, "version", lambda name: preflight.QUALIFIED_VERSIONS[name])
    return tmp_path


def test_doctor_checks_versions_without_creating_or_starting_fixture(tools, monkeypatch, capsys):
    def forbidden(*_, **__):
        pytest.fail("doctor tried to acquire fixture state")
    monkeypatch.setattr("vuoro_demo.cli.fixture", forbidden)
    monkeypatch.setattr("vuoro_demo.cli.tempfile.mkdtemp", forbidden)
    monkeypatch.setattr("vuoro_demo.cli.os.open", forbidden)
    assert main(["doctor", "--pg-bin", str(tools), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "vuoro-demo-preflight/v1"
    assert report["status"] == "ready"
    assert {row["name"] for row in report["checks"]} == {
        "python", "runtime-user", "vuoro-client", "vuoro-service", "sprintctl",
        "git", "initdb", "pg_ctl", "scratch-directory"}
    assert {row["name"]: row["version"] for row in report["checks"] if "version" in row} == {
        "git": "2.43.0", "initdb": "16.10", "pg_ctl": "16.10"}


def test_explicit_missing_pg_directory_never_falls_back_to_path(tools):
    report = preflight.diagnose(tools / "missing")
    blocked = {row["name"] for row in report["checks"] if row["status"] == "blocked"}
    assert report["status"] == "blocked" and blocked == {"initdb", "pg_ctl"}


def test_mixed_pg_majors_fail_in_doctor_and_real_fixture_gate(tools):
    (tools / "pg_ctl").write_text('#!/bin/sh\necho "pg_ctl (PostgreSQL) 17.2"\n')
    report = preflight.diagnose(tools)
    assert report["status"] == "blocked"
    assert next(row for row in report["checks"] if row["name"] == "pg_ctl")["status"] == "blocked"
    with pytest.raises(ValueError, match="PostgreSQL 16"):
        preflight.postgres_tools(tools)


def test_relative_pg_directory_resolves_before_fixture_changes_working_directory(tools, monkeypatch):
    monkeypatch.chdir(tools.parent)
    resolved = preflight.postgres_tools(Path(tools.name))
    assert resolved == {name: str(tools / name) for name in ("initdb", "pg_ctl")}


def test_root_is_reported_and_refused_before_database_start(tools, monkeypatch):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    report = preflight.diagnose(tools)
    row = next(row for row in report["checks"] if row["name"] == "runtime-user")
    assert report["status"] == row["status"] == "blocked"
    assert "non-root" in row["detail"]
    with pytest.raises(ValueError, match="non-root"):
        preflight.postgres_tools(tools)


def test_dependency_skew_is_reported_without_importing_owner(tools, monkeypatch):
    monkeypatch.setattr(preflight, "version", lambda _: "0.0.0")
    report = preflight.diagnose(tools)
    assert report["status"] == "blocked"
    assert {row["name"] for row in report["checks"] if row["status"] == "blocked"} == set(preflight.QUALIFIED_VERSIONS)


@pytest.mark.parametrize("failure", [OSError("private diagnostic"),
    subprocess.TimeoutExpired("private diagnostic", 5),
    subprocess.CalledProcessError(1, "private diagnostic", stderr="private diagnostic")])
def test_failed_or_timed_out_tools_are_bounded_and_do_not_echo_diagnostics(tools, monkeypatch, failure):
    def fail(*_, **__):
        raise failure
    monkeypatch.setattr(preflight.subprocess, "run", fail)
    report = preflight.diagnose(tools)
    assert report["status"] == "blocked"
    assert "private diagnostic" not in json.dumps(report)


def test_doctor_can_diagnose_missing_dependencies_in_a_stdlib_only_process(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    env = {"PYTHONPATH": str(source), "PATH": "", "LANG": "C"}
    run = subprocess.run([sys.executable, "-S", "-m", "vuoro_demo.cli", "doctor", "--json"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert run.returncode == 1 and not run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "blocked"
    distributions = [row for row in report["checks"] if row["name"] in preflight.QUALIFIED_VERSIONS]
    assert len(distributions) == 3 and all(row["installed"] is None for row in distributions)
    assert not list(tmp_path.iterdir())


def test_plain_text_doctor_explains_blockers(tools, monkeypatch, capsys):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    assert main(["doctor", "--pg-bin", str(tools)]) == 1
    text = capsys.readouterr().out
    assert "blocked: runtime-user: Run as a non-root" in text
    assert "Prerequisites only" in text
