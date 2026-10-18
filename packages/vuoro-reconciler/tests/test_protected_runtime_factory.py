"""Composition ownership and refusal gates; no installed authority fixture."""

from types import SimpleNamespace
import os

import pytest

from vuoro_reconciler import protected_runtime_factory as module
from vuoro_reconciler import protected_provider_registry
from vuoro_reconciler.protected_runtime_registry import RuntimeRefused


def composition(monkeypatch, fail=None, drift=None):
    events = []
    digest = "sha256:" + "a" * 64
    profile = SimpleNamespace(
        data={
            "effect_repository": "fixture",
            "runtime_profile_sha256": digest,
            "artifact_root": "/fixture-authority-not-accessed",
        },
        signing=object(),
    )
    snapshot = SimpleNamespace(
        profile=profile, host_id="fixture-host", registry_utf8=b'{"fixture":true}'
    )
    runtime = SimpleNamespace(
        data={"effect_repository": "fixture"},
        profile_sha256=digest,
        read_profile=lambda kind: kind,
    )
    snapshots = [snapshot, snapshot]
    runtimes = [runtime, runtime]
    if drift == "registry":
        snapshots[-1] = SimpleNamespace(profile=profile, host_id="changed")
    elif drift == "runtime":
        runtimes[-1] = object()
    monkeypatch.setattr(module, "_snapshot", lambda identifier: snapshots.pop(0))
    monkeypatch.setattr(
        module, "load_runtime_profile", lambda identifier: runtimes.pop(0)
    )

    def admit(value):
        events.append("admit")
        assert value is profile
        if fail == "admit":
            raise OSError("sensitive fixture path must not escape")

    monkeypatch.setattr(module, "admit_installation", admit)
    monkeypatch.setattr(
        module,
        "_journal_directory",
        lambda identifier: SimpleNamespace(
            directory="/fixture-journal", check=lambda: None, close=lambda: None
        ),
    )

    def construct(name):
        def create(*args, **kwargs):
            events.append("create:" + name)
            if fail == name:
                raise OSError("sensitive fixture constructor must not escape")
            result = SimpleNamespace(close=lambda: events.append("close:" + name))
            result._active = None
            result._state = SimpleNamespace(owner=None)
            if name == "provider":
                result.profile = profile
            return result

        return create

    monkeypatch.setattr(module, "ExecutionJournal", construct("journal"))
    monkeypatch.setattr(
        module, "InstalledReadCredentials", lambda identifier, kind: construct(kind)()
    )
    monkeypatch.setattr(module, "_local", construct("local"))
    monkeypatch.setattr(module, "_provider", construct("provider"))
    for name in (
        "PrivateReadTransport",
        "ProtectedLedgerSource",
        "NativeBasisSource",
        "ProtectedGit",
        "ProtectedExecutor",
    ):
        monkeypatch.setattr(module, name, construct(name))
    return events, snapshot, runtime


@pytest.mark.parametrize(
    "fail",
    [
        "admit",
        "journal",
        "broker_read",
        "native_read",
        "local",
        "provider",
        "ProtectedExecutor",
    ],
)
def test_constructor_failure_closes_every_acquired_resource(monkeypatch, fail):
    events, _, _ = composition(monkeypatch, fail=fail)
    with pytest.raises(RuntimeRefused) as caught:
        module.open_installed_runtime("fixture")
    assert str(caught.value) == "protected-runtime-invalid"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    created = [event[7:] for event in events if event.startswith("create:")]
    owned = [
        name
        for name in created
        if name in {"journal", "broker_read", "native_read", "local", "provider"}
        and name != fail
    ]
    assert [event[6:] for event in events if event.startswith("close:")] == owned[::-1]
    assert events[0] == "admit"


@pytest.mark.parametrize("drift", ["registry", "runtime"])
def test_final_pin_join_failure_closes_all_resources(monkeypatch, drift):
    events, _, _ = composition(monkeypatch, drift=drift)
    with pytest.raises(RuntimeRefused):
        module.open_installed_runtime("fixture")
    assert [event for event in events if event.startswith("close:")] == [
        "close:provider",
        "close:local",
        "close:native_read",
        "close:broker_read",
        "close:journal",
    ]


def test_successful_context_owns_and_closes_once_without_any_execution(monkeypatch):
    events, _, _ = composition(monkeypatch)
    runtime = module.open_installed_runtime("fixture")
    assert not any(event.startswith("close:") for event in events)
    with runtime as entered:
        assert entered is runtime and entered.executor is not None
    runtime.close()
    assert len([event for event in events if event.startswith("close:")]) == 5
    with pytest.raises(Exception):
        runtime.__enter__()


@pytest.mark.parametrize("identifier", ["../fixture", "/tmp/fixture", "", None])
def test_invalid_selector_refuses_before_loading_or_installing(monkeypatch, identifier):
    calls = []
    monkeypatch.setattr(module, "_snapshot", lambda value: calls.append(value))
    for entry in (module.open_installed_runtime, module.install_profile_journal):
        with pytest.raises(RuntimeRefused):
            entry(identifier)
    assert calls == []


def test_explicit_install_uses_exact_snapshot_and_is_not_runtime_open(monkeypatch):
    events, snapshot, _ = composition(monkeypatch)
    installed = []
    monkeypatch.setattr(
        module, "install_journal", lambda path, **data: installed.append((path, data))
    )
    # Installer brackets its write with three independent snapshot loads.
    monkeypatch.setattr(module, "_snapshot", lambda identifier: snapshot)
    module.install_profile_journal("fixture")
    assert installed == [
        (
            "/fixture-journal",
            {"host_id": snapshot.host_id, "registry_utf8": snapshot.registry_utf8},
        )
    ]
    assert events == ["admit"]


def rebind_composition(monkeypatch, tmp_path, *, active=False):
    digest = "sha256:" + "a" * 64
    host = "sha256:" + "b" * 64
    old = "sha256:" + "c" * 64
    registry = b'{"schema":"registry/v1"}'
    profile = SimpleNamespace(
        profile_sha256=digest,
        registry_sha256=module._hash(registry),
        data={"runtime_profile_sha256": "sha256:" + "d" * 64, "artifact_root": str(tmp_path / "artifacts")},
    )
    (tmp_path / "artifacts").mkdir()
    snapshot = SimpleNamespace(
        profile=profile, host_id=host, registry_utf8=registry
    )
    runtime = SimpleNamespace(profile_sha256=profile.data["runtime_profile_sha256"])
    state_dir = tmp_path / "operation-state"
    state_dir.mkdir()
    state_dir.chmod(0o700)
    lock_path = state_dir / "fixture.lock"
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o600)
    active_path = state_dir / "fixture.active"
    if active:
        active_path.write_text("active")
    events = []
    monkeypatch.setattr(module, "_snapshot", lambda profile_id: snapshot)
    monkeypatch.setattr(module, "load_runtime_profile", lambda profile_id: runtime)
    monkeypatch.setattr(module, "admit_installation", lambda value: events.append("admit"))
    monkeypatch.setattr(
        protected_provider_registry,
        "_state_paths",
        lambda profile_id: (state_dir, lock_path, "fixture.active"),
    )

    class Provider:
        def __init__(self):
            self._state = SimpleNamespace(
                profile=profile,
                directory=os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY),
                lock=os.open(lock_path, os.O_RDWR),
            )

        def close(self):
            events.append("provider-close")
            os.close(self._state.lock)
            os.close(self._state.directory)

    monkeypatch.setattr(module, "_provider", lambda profile_id: Provider())
    location = SimpleNamespace(
        directory=tmp_path / "journal",
        check=lambda: events.append("location-check"),
        journal_identities=lambda: (1, 2, 3),
        check_journal_identities=lambda expected: events.append("journal-identities-check"),
        close=lambda: events.append("location-close"),
    )
    monkeypatch.setattr(module, "_journal_directory", lambda profile_id: location)
    monkeypatch.setattr(module, "_separate_storage", lambda *args: None)
    return snapshot, runtime, location, events, old


def test_empty_journal_rebind_factory_binds_current_profile_under_locks(monkeypatch, tmp_path):
    snapshot, runtime, location, events, old = rebind_composition(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        module,
        "rebind_empty_journal",
        lambda directory, **kwargs: calls.append((directory, kwargs))
        or {"operation_id": kwargs["operation_id"], "old_registry_sha256": old, "new_registry_sha256": snapshot.profile.registry_sha256},
    )
    result = module.rebind_empty_profile_journal(
        "fixture",
        expected_old_registry_sha256=old,
        expected_profile_sha256=snapshot.profile.profile_sha256,
        expected_host_id=snapshot.host_id,
        operation_id="a" * 32,
    )
    assert result["operation_id"] == "a" * 32
    assert calls[0][0] == location.directory
    assert {key: value for key, value in calls[0][1].items() if key != "identity_check"} == {
        "host_id": snapshot.host_id,
        "profile_id": "fixture",
        "expected_old_registry_sha256": old,
        "new_registry_utf8": snapshot.registry_utf8,
        "operation_id": "a" * 32,
        "expected_physical_identities": (1, 2, 3),
    }
    assert callable(calls[0][1]["identity_check"])
    assert events.count("provider-close") == 1 and events.count("location-close") == 1


@pytest.mark.parametrize("drift", ["profile", "host"])
def test_empty_journal_rebind_factory_refuses_stale_profile_or_host_before_lock(
    monkeypatch, tmp_path, drift
):
    snapshot, _, _, _, _ = rebind_composition(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_provider", lambda *args: pytest.fail("lock opened"))
    kwargs = {
        "expected_old_registry_sha256": "sha256:" + "c" * 64,
        "expected_profile_sha256": "sha256:" + "0" * 64 if drift == "profile" else snapshot.profile.profile_sha256,
        "expected_host_id": "sha256:" + "0" * 64 if drift == "host" else snapshot.host_id,
        "operation_id": "a" * 32,
    }
    with pytest.raises(RuntimeRefused):
        module.rebind_empty_profile_journal("fixture", **kwargs)


def test_empty_journal_rebind_factory_refuses_active_marker_before_mutation(
    monkeypatch, tmp_path
):
    snapshot, _, _, _, _ = rebind_composition(monkeypatch, tmp_path, active=True)
    monkeypatch.setattr(
        module, "rebind_empty_journal", lambda *args, **kwargs: pytest.fail("journal changed")
    )
    with pytest.raises(RuntimeRefused):
        module.rebind_empty_profile_journal(
            "fixture",
            expected_old_registry_sha256="sha256:" + "c" * 64,
            expected_profile_sha256=snapshot.profile.profile_sha256,
            expected_host_id=snapshot.host_id,
            operation_id="a" * 32,
        )


def test_registry_rebind_receipt_factory_joins_current_admission_and_journal(
    monkeypatch, tmp_path
):
    snapshot, _, location, events, _ = rebind_composition(monkeypatch, tmp_path)
    receipt = {
        "operation_id": "a" * 32,
        "host_id": snapshot.host_id,
        "profile_id": "fixture",
        "old_registry_sha256": "sha256:" + "c" * 64,
        "new_registry_sha256": snapshot.profile.registry_sha256,
    }

    class Journal:
        def __init__(self, directory, *, host_id, registry_utf8):
            assert directory == location.directory
            assert host_id == snapshot.host_id
            assert registry_utf8 == snapshot.registry_utf8

        def registry_rebind_receipt(self, operation_id):
            assert operation_id == "a" * 32
            return receipt

        def close(self):
            events.append("journal-close")

    monkeypatch.setattr(module, "ExecutionJournal", Journal)
    observed = module.read_profile_journal_rebind_receipt(
        "fixture",
        operation_id="a" * 32,
        expected_profile_sha256=snapshot.profile.profile_sha256,
        expected_host_id=snapshot.host_id,
    )
    assert observed == receipt
    assert events.count("journal-close") == 1 and events.count("provider-close") == 1


def test_registry_rebind_receipt_factory_rechecks_journal_path_after_read(
    monkeypatch, tmp_path
):
    snapshot, _, location, events, _ = rebind_composition(monkeypatch, tmp_path)
    checks = 0

    def changing_path():
        nonlocal checks
        checks += 1
        events.append("location-check")
        if checks == 2:
            raise OSError("fixture path replaced")

    location.check = changing_path

    class Journal:
        def __init__(self, *args, **kwargs):
            pass

        def registry_rebind_receipt(self, operation_id):
            return {"operation_id": operation_id}

        def close(self):
            events.append("journal-close")

    monkeypatch.setattr(module, "ExecutionJournal", Journal)
    with pytest.raises(RuntimeRefused):
        module.read_profile_journal_rebind_receipt(
            "fixture",
            operation_id="a" * 32,
            expected_profile_sha256=snapshot.profile.profile_sha256,
            expected_host_id=snapshot.host_id,
        )
    assert events.count("journal-close") == 1
    assert events.count("provider-close") == 1


def test_registry_rebind_receipt_factory_refuses_marker_created_during_read(
    monkeypatch, tmp_path
):
    snapshot, _, location, events, _ = rebind_composition(monkeypatch, tmp_path)
    active_path = tmp_path / "operation-state" / "fixture.active"

    class Journal:
        def __init__(self, *args, **kwargs):
            pass

        def registry_rebind_receipt(self, operation_id):
            active_path.write_text("active")
            return {"operation_id": operation_id}

        def close(self):
            events.append("journal-close")

    monkeypatch.setattr(module, "ExecutionJournal", Journal)
    with pytest.raises(RuntimeRefused):
        module.read_profile_journal_rebind_receipt(
            "fixture",
            operation_id="a" * 32,
            expected_profile_sha256=snapshot.profile.profile_sha256,
            expected_host_id=snapshot.host_id,
        )
    assert events.count("journal-close") == 1
    assert events.count("provider-close") == 1


@pytest.mark.parametrize("active", ["journal", "state", "fork"])
def test_close_refuses_before_consuming_any_resource_when_not_idle(monkeypatch, active):
    events, _, _ = composition(monkeypatch)
    runtime = module.open_installed_runtime("fixture")
    if active == "journal":
        runtime._journal._active = object()
    elif active == "state":
        runtime._states[0].owner = object()
    else:
        runtime._pid = -1
    with pytest.raises(RuntimeRefused):
        runtime.close()
    assert not any(event.startswith("close:") for event in events)
    runtime._journal._active = None
    runtime._states[0].owner = None
    runtime._pid = module.os.getpid()
    runtime.close()
    assert len([event for event in events if event.startswith("close:")]) == 5


@pytest.mark.parametrize(
    "artifact_root", ["/fixture-journal", "/", "/fixture-journal/nested"]
)
def test_overlapping_storage_refuses_before_resource_open_or_install(
    monkeypatch, artifact_root
):
    events, snapshot, _ = composition(monkeypatch)
    snapshot.profile.data["artifact_root"] = artifact_root
    installed = []
    monkeypatch.setattr(
        module, "install_journal", lambda *args, **kwargs: installed.append(True)
    )
    for entry in (module.open_installed_runtime, module.install_profile_journal):
        # Reload the fixture's stable snapshot/runtime for each constructor.
        events, snapshot, _ = composition(monkeypatch)
        snapshot.profile.data["artifact_root"] = artifact_root
        with pytest.raises(RuntimeRefused):
            entry("fixture")
        assert not any(event.startswith("create:") for event in events)
    assert installed == []


def real_storage_fixture(monkeypatch, tmp_path):
    import sys

    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    (root / "profiles").mkdir(mode=0o700)
    parent = root / "profiles" / "fixture"
    parent.mkdir(mode=0o700)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    digest = "sha256:" + "a" * 64
    profile = SimpleNamespace(
        data={"artifact_root": str(artifacts), "runtime_profile_sha256": digest}
    )
    snapshot = SimpleNamespace(
        profile=profile, host_id="fixture", registry_utf8=b'{"fixture":true}'
    )
    runtime = SimpleNamespace(profile_sha256=digest)
    monkeypatch.setattr(module, "_root", lambda: root)
    monkeypatch.setattr(module, "_snapshot", lambda value: snapshot)
    monkeypatch.setattr(module, "admit_installation", lambda value: None)
    monkeypatch.setattr(module, "load_runtime_profile", lambda value: runtime)
    # This fixture qualifies actual paths/modes/descriptor joins and SQLite,
    # not installed authority or the separate filesystem admission predicate.
    monkeypatch.setitem(
        sys.modules,
        "vuoro_reconciler.protected_provider_registry",
        SimpleNamespace(_filesystem=lambda descriptor: 123, _LOCAL_FILESYSTEMS={123}),
    )
    return parent, snapshot


@pytest.mark.parametrize("drift", ["mode", "replacement700", "replacement755"])
def test_actual_install_refuses_private_parent_drift_through_write(
    monkeypatch, tmp_path, drift
):
    parent, _ = real_storage_fixture(monkeypatch, tmp_path)
    original = module.install_journal

    def changed(*args, **kwargs):
        if drift == "mode":
            parent.chmod(0o755)
        else:
            parent.rename(parent.with_name("retired"))
            parent.mkdir(mode=0o700 if drift == "replacement700" else 0o755)
        original(*args, **kwargs)

    monkeypatch.setattr(module, "install_journal", changed)
    with pytest.raises(RuntimeRefused) as caught:
        module.install_profile_journal("fixture")
    assert caught.value.__context__ is None


def test_actual_install_normal_directory_entry_changes_do_not_refuse(
    monkeypatch, tmp_path
):
    parent, snapshot = real_storage_fixture(monkeypatch, tmp_path)
    module.install_profile_journal("fixture")
    journal = module.ExecutionJournal(
        parent / "journal",
        host_id=snapshot.host_id,
        registry_utf8=snapshot.registry_utf8,
    )
    journal.close()


def test_actual_journal_close_failure_is_permanent_runtime_uncertainty(
    monkeypatch, tmp_path
):
    from contextlib import ExitStack

    parent, snapshot = real_storage_fixture(monkeypatch, tmp_path)
    location = module._journal_directory("fixture")
    module.install_journal(
        location.directory,
        host_id=snapshot.host_id,
        registry_utf8=snapshot.registry_utf8,
    )
    journal = module.ExecutionJournal(
        location.directory,
        host_id=snapshot.host_id,
        registry_utf8=snapshot.registry_utf8,
    )
    descriptors = list(journal._fds)
    stack = ExitStack()
    stack.callback(location.close)
    stack.callback(journal.close)
    runtime = module.InstalledRuntime(object(), stack, journal, (), location)
    real_close = module.os.close

    def failed(descriptor):
        if descriptor == descriptors[0]:
            raise OSError("fixture descriptor close uncertainty")
        real_close(descriptor)

    monkeypatch.setattr(module.os, "close", failed)
    try:
        for _ in range(2):
            with pytest.raises(RuntimeRefused):
                runtime.close()
        assert runtime._close_uncertain and not runtime._closed
        with pytest.raises(RuntimeRefused):
            _ = runtime.executor
        for descriptor in descriptors:
            module.os.fstat(descriptor)
    finally:
        for descriptor in descriptors:
            real_close(descriptor)
        journal._fds = []


@pytest.mark.parametrize("operation", ["enter", "close"])
def test_location_drift_releases_owned_resources_without_success(
    monkeypatch, operation
):
    events, _, _ = composition(monkeypatch)
    runtime = module.open_installed_runtime("fixture")

    def changed():
        raise OSError("fixture private parent location changed")

    runtime._location.check = changed
    with pytest.raises(RuntimeRefused) as caught:
        runtime.__enter__() if operation == "enter" else runtime.close()
    assert caught.value.__context__ is None
    assert runtime._closed
    assert len([event for event in events if event.startswith("close:")]) == 5


def test_initial_private_directory_admission_joins_original_inode(
    monkeypatch, tmp_path
):
    parent, _ = real_storage_fixture(monkeypatch, tmp_path)
    original = module._directory
    swapped = []

    def replaced(value, *, private):
        result = original(value, private=private)
        if value == str(parent) and not swapped:
            parent.rename(parent.with_name("original"))
            parent.mkdir(mode=0o700)
            swapped.append(True)
        return result

    monkeypatch.setattr(module, "_directory", replaced)
    with pytest.raises(Exception):
        module._journal_directory("fixture")
    assert swapped == [True]
