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


def _authored() -> dict:
    return dict(record_kind="observation", summary="offline", created_at="2026-10-10T00:00:00Z", record_id="native-record-1", basis_revision="release-1")


def test_same_authored_retry_and_conflict_do_not_change_history(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    first = log.append(**_authored())
    before = log.path.read_bytes()
    assert RecoveryLog(tmp_path, "incident-1").append(**_authored()) == first
    assert log.path.read_bytes() == before
    with pytest.raises(ValueError, match="conflicts"):
        log.append(**(_authored() | {"basis_revision": "different-release"}))
    assert log.path.read_bytes() == before
    assert len(log.export()) == 1


def test_namespace_and_private_storage(tmp_path: Path) -> None:
    for incident in ("..", "/outside", "../outside", "one/two", "", "a" * 129):
        with pytest.raises(ValueError, match="namespace"):
            RecoveryLog(tmp_path, incident)
    log = RecoveryLog(tmp_path, "incident-1").begin()
    assert log.path.stat().st_mode & 0o777 == 0o600
    assert log.path.parent.stat().st_mode & 0o777 == 0o700
    original = tmp_path / "outside"
    original.write_text("preserve me")
    log.path.unlink()
    log.path.symlink_to(original)
    with pytest.raises(OSError):
        log.begin()
    assert original.read_text() == "preserve me"


@pytest.mark.parametrize("fault", ["foreign-incident", "partial-tail", "invalid-kind"])
def test_corrupt_history_refuses_append_and_export_without_rewrite(tmp_path: Path, fault: str) -> None:
    import json
    log = RecoveryLog(tmp_path, "incident-1").begin()
    entry = log.append(**_authored()).to_json()
    if fault == "foreign-incident":
        entry["incident_id"] = "another-incident"
    if fault == "invalid-kind":
        entry["record_kind"] = "accepted-decision"
    data = json.dumps(entry) + ("" if fault == "partial-tail" else "\n")
    log.path.write_text(data)
    before = log.path.read_bytes()
    with pytest.raises(ValueError):
        log.export()
    with pytest.raises(ValueError):
        log.append(**(_authored() | {"record_id": "second"}))
    assert log.path.read_bytes() == before


def test_two_processes_retry_one_record(tmp_path: Path) -> None:
    import multiprocessing
    log = RecoveryLog(tmp_path, "incident-1").begin()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    def append() -> None:
        barrier.wait(timeout=5)
        RecoveryLog(tmp_path, "incident-1").append(**_authored())
    children = [context.Process(target=append) for _ in range(2)]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=10)
        assert child.exitcode == 0
    assert len(log.export()) == 1


@pytest.mark.parametrize("store_kind", ["hardlink", "fifo", "directory"])
def test_nonprivate_regular_stores_refused_without_modifying_target(tmp_path: Path, store_kind: str) -> None:
    import os
    log = RecoveryLog(tmp_path, "incident-1").begin()
    log.path.unlink()
    original = tmp_path / "outside"
    original.write_text("original history")
    original.chmod(0o644)
    if store_kind == "hardlink":
        os.link(original, log.path)
    elif store_kind == "fifo":
        os.mkfifo(log.path)
    else:
        log.path.mkdir()
    for operation in (log.begin, log.export, lambda: log.append(**_authored())):
        with pytest.raises((ValueError, OSError)):
            operation()
    assert original.read_text() == "original history"
    assert original.stat().st_mode & 0o777 == 0o644


def test_symlinked_parent_namespace_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        RecoveryLog(alias / "nested", "incident-1").begin()
    assert not list(target.iterdir())


@pytest.mark.parametrize("data", ['\n', '{"record_id":"a","record_id":"b"}\n', '[]\n'])
def test_uninterpretable_rows_are_not_skipped_or_repaired(tmp_path: Path, data: str) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    log.path.write_text(data)
    for operation in (log.export, lambda: log.append(**_authored())):
        with pytest.raises(ValueError, match="preserve"):
            operation()
    assert log.path.read_text() == data


def test_invalid_authored_data_cannot_start_a_partial_append(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    before = log.path.read_bytes()
    with pytest.raises(ValueError):
        log.append(**_authored(), detail={"measurement": float("nan")})
    with pytest.raises(ValueError):
        log.append(**_authored(), detail=[])
    with pytest.raises(ValueError):
        log.append(**_authored(), detail={"nested": {1: "integer key", "1": "string key"}})
    assert log.path.read_bytes() == before


def test_retry_requires_exact_json_types_not_python_numeric_equality(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    log.append(**_authored(), detail={"measurement": 1})
    before = log.path.read_bytes()
    for value in (True, 1.0):
        with pytest.raises(ValueError, match="conflicts"):
            log.append(**_authored(), detail={"measurement": value})
    assert log.path.read_bytes() == before


def test_historical_duplicate_ids_are_not_implicitly_deduplicated(tmp_path: Path) -> None:
    log = RecoveryLog(tmp_path, "incident-1").begin()
    log.append(**_authored())
    row = log.path.read_bytes()
    log.path.write_bytes(row + row)
    for operation in (log.export, lambda: log.append(**_authored())):
        with pytest.raises(ValueError, match="duplicate"):
            operation()
    assert log.path.read_bytes() == row + row


def test_crash_after_fsync_before_reply_retries_original_record(tmp_path: Path) -> None:
    import multiprocessing
    import os
    log = RecoveryLog(tmp_path, "incident-1").begin()
    def interrupted() -> None:
        real_fsync = os.fsync
        def fsync_then_crash(descriptor):
            real_fsync(descriptor)
            os._exit(73)
        os.fsync = fsync_then_crash
        RecoveryLog(tmp_path, "incident-1").append(**_authored())
    child = multiprocessing.get_context("fork").Process(target=interrupted)
    child.start()
    child.join(timeout=10)
    assert child.exitcode == 73
    before = log.path.read_bytes()
    returned = RecoveryLog(tmp_path, "incident-1").append(**_authored())
    assert returned.record_id == "native-record-1"
    assert log.path.read_bytes() == before
    assert len(log.export()) == 1


def test_crash_during_write_preserves_incomplete_tail_and_refuses_retry(tmp_path: Path) -> None:
    import multiprocessing
    import os
    log = RecoveryLog(tmp_path, "incident-1").begin()
    def interrupted() -> None:
        real_fdopen = os.fdopen
        class PartialWriter:
            def __init__(self, descriptor, *args, **kwargs):
                self.handle = real_fdopen(descriptor, *args, **kwargs)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def __getattr__(self, name):
                return getattr(self.handle, name)
            def __iter__(self):
                return iter(self.handle)
            def write(self, data):
                self.handle.write(data[:len(data)//2])
                self.handle.flush()
                os.fsync(self.handle.fileno())
                os._exit(74)
        os.fdopen = PartialWriter
        RecoveryLog(tmp_path, "incident-1").append(**_authored())
    child = multiprocessing.get_context("fork").Process(target=interrupted)
    child.start()
    child.join(timeout=10)
    assert child.exitcode == 74
    before = log.path.read_bytes()
    assert before and not before.endswith(b"\n")
    for operation in (log.export, lambda: log.append(**_authored())):
        with pytest.raises(ValueError, match="incomplete"):
            operation()
    assert log.path.read_bytes() == before


def test_unavailable_posix_locking_does_not_break_client_imports(tmp_path: Path, monkeypatch) -> None:
    import builtins
    import importlib
    import vuoro_client
    import vuoro_client.recovery as recovery
    actual_import = builtins.__import__
    def no_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("simulated non-POSIX platform")
        return actual_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_fcntl)
    importlib.reload(recovery)
    importlib.reload(vuoro_client)
    with pytest.raises(RuntimeError, match="POSIX"):
        recovery.RecoveryLog(tmp_path, "incident-1").begin()
    assert not (tmp_path / "incident-1").exists()
