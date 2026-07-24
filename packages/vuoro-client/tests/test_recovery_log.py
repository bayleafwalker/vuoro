from __future__ import annotations

from pathlib import Path

import pytest

from vuoro_client.recovery import RecoveryLog


def test_begin_is_idempotent(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    log.begin()
    assert log.path.exists()


def test_append_and_export_round_trip(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    log.append(
        record_kind="observation",
        summary="service unreachable",
        created_at="2026-07-24T00:00:00Z",
        basis_revision="rev-1",
    )
    log.append(
        record_kind="requested-command",
        summary="ask ops to roll back deploy X",
        created_at="2026-07-24T00:05:00Z",
        requested_command={"command_type": "rollback", "params": {"deploy": "X"}},
    )
    exported = log.export()
    assert len(exported) == 2
    assert exported[0]["record_kind"] == "observation"
    assert exported[1]["requested_command"] == {
        "command_type": "rollback",
        "params": {"deploy": "X"},
    }


def test_requested_command_kind_requires_payload(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    with pytest.raises(ValueError, match="requested_command is required"):
        log.append(
            record_kind="requested-command",
            summary="missing payload",
            created_at="2026-07-24T00:00:00Z",
        )


def test_observation_kind_forbids_requested_command_payload(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    with pytest.raises(ValueError, match="must be omitted"):
        log.append(
            record_kind="observation",
            summary="should not carry a command",
            created_at="2026-07-24T00:00:00Z",
            requested_command={"command_type": "noop", "params": {}},
        )


def test_restart_resumes_the_same_namespace_without_loss_or_duplication(
    tmp_path: Path,
) -> None:
    first = RecoveryLog(tmp_path, "incident-1").begin()
    entry = first.append(
        record_kind="observation",
        summary="before restart",
        created_at="2026-07-24T00:00:00Z",
    )

    # Simulate a process restart: a fresh RecoveryLog reopens the same
    # incident namespace on disk.
    resumed = RecoveryLog(tmp_path, "incident-1").begin()
    resumed.append(
        record_kind="observation",
        summary="after restart",
        created_at="2026-07-24T00:10:00Z",
    )

    records = list(resumed.records())
    assert len(records) == 2
    assert records[0].record_id == entry.record_id
    assert records[0].summary == "before restart"
    assert records[1].summary == "after restart"


def test_records_before_begin_raises(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1")
    with pytest.raises(FileNotFoundError):
        list(log.records())


def test_export_is_the_only_surface_no_apply_method_exists(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    public_names = {name for name in dir(log) if not name.startswith("_")}
    assert public_names == {"begin", "append", "records", "export", "path", "root", "incident_id"}
