"""Fixed installed protected composition; never selects caller credentials."""

from contextlib import ExitStack
import os
from pathlib import Path

from .execution_journal import ExecutionJournal, install_journal
from .protected_basis_source import NativeBasisSource
from .protected_copy_codec import _need
from .protected_executor import ProtectedExecutor
from .protected_git import ProtectedGit
from .protected_ledger_source import ProtectedLedgerSource
from .protected_read_transport import PrivateReadTransport
from .protected_runtime_credentials import InstalledReadCredentials
from .protected_runtime_installation import admit_installation
from .protected_runtime_registry import (
    _ID,
    _ancestry,
    _directory,
    _root,
    _safe,
    load_runtime_profile,
)


def _snapshot(profile_id):
    from .protected_provider_registry import load_installed_registry_snapshot

    return load_installed_registry_snapshot(profile_id)


def _provider(profile_id):
    from .protected_provider import ProtectedProvider

    return ProtectedProvider(profile_id)


def _local(profile_id):
    from .protected_runtime_local import InstalledLocalAuthority

    return InstalledLocalAuthority(profile_id)


def _journal_directory(profile_id):
    _need(type(profile_id) is str and _ID.fullmatch(profile_id))
    return _JournalLocation(_root() / "profiles" / profile_id / "journal")


def _directory_state(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


class _JournalLocation:
    def __init__(self, directory):
        self.directory = directory
        parent = directory.parent
        self.descriptor = None
        self.private = (_root(), parent.parent, parent)
        # Owner-installed parents are mandatory, including for explicit journal
        # installation. No runtime or installer repairs an existing layout.
        self.ancestry = _ancestry(parent)
        self.states = tuple(_directory_state(os.lstat(path)) for path in self.private)
        for path in self.private:
            _directory(str(path), private=True)
        try:
            self.descriptor = os.open(
                parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            from .protected_provider_registry import _filesystem, _LOCAL_FILESYSTEMS

            _need(_filesystem(self.descriptor) in _LOCAL_FILESYSTEMS)
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        _need(self.descriptor is not None)
        for path, expected in zip(self.private, self.states, strict=True):
            _directory(str(path), private=True)
            _need(_directory_state(os.lstat(path)) == expected)
        _need(_ancestry(self.directory.parent) == self.ancestry)
        _need(_directory_state(os.fstat(self.descriptor)) == self.states[-1])

    def close(self):
        if self.descriptor is not None:
            descriptor, self.descriptor = self.descriptor, None
            os.close(descriptor)


def _separate_storage(directory, provider):
    journal = Path(directory).resolve()
    artifacts = Path(provider.data["artifact_root"]).resolve()
    _need(
        not journal.is_relative_to(artifacts) and not artifacts.is_relative_to(journal)
    )


class InstalledRuntime:
    """Own the fixed composition's resources until its context is closed."""

    def __init__(self, executor, resources, journal, states, location):
        self._executor = executor
        self._resources = resources
        self._journal = journal
        self._states = states
        self._pid = os.getpid()
        self._closed = False
        self._close_uncertain = False
        self._location = location

    @property
    @_safe
    def executor(self):
        _need(
            not self._closed and not self._close_uncertain and os.getpid() == self._pid
        )
        self._location.check()
        return self._executor

    @_safe
    def __enter__(self):
        _need(
            not self._closed and not self._close_uncertain and os.getpid() == self._pid
        )
        try:
            self._location.check()
        except BaseException:
            # A failed with-expression does not invoke __exit__.
            try:
                self.close()
            except BaseException:
                pass
            raise
        return self

    def __exit__(self, kind, primary, traceback):
        try:
            self.close()
        except BaseException:
            if primary is None:
                raise
            primary.add_note("protected-runtime-resource-close-uncertain")
        return False

    @_safe
    def close(self):
        _need(not self._close_uncertain)
        if not self._closed:
            # Check before consuming any ExitStack callbacks: an in-flight
            # cycle or fork cannot partially close the owning composition.
            _need(os.getpid() == self._pid and self._journal._active is None)
            _need(all(state.owner is None for state in self._states))
            configuration_error = None
            try:
                self._location.check()
            except Exception as error:
                configuration_error = error
            try:
                self._resources.close()
            except BaseException:
                self._close_uncertain = True
                raise
            self._closed = True
            if configuration_error is not None:
                raise configuration_error


@_safe
def open_installed_runtime(profile_id):
    """Profile ID is the sole selector; opening never installs or migrates."""
    _need(type(profile_id) is str and _ID.fullmatch(profile_id))
    snapshot = _snapshot(profile_id)
    admit_installation(snapshot.profile)
    runtime = load_runtime_profile(profile_id)
    _need(
        runtime.data["effect_repository"] == snapshot.profile.data["effect_repository"]
    )
    _need(runtime.profile_sha256 == snapshot.profile.data["runtime_profile_sha256"])
    with ExitStack() as resources:
        location = _journal_directory(profile_id)
        resources.callback(location.close)
        _separate_storage(location.directory, snapshot.profile)
        location.check()
        journal = ExecutionJournal(
            location.directory,
            host_id=snapshot.host_id,
            registry_utf8=snapshot.registry_utf8,
        )
        resources.callback(journal.close)
        broker = InstalledReadCredentials(profile_id, "broker_read")
        resources.callback(broker.close)
        native = InstalledReadCredentials(profile_id, "native_read")
        resources.callback(native.close)
        local = _local(profile_id)
        resources.callback(local.close)
        provider = _provider(profile_id)
        resources.callback(provider.close)
        _need(provider.profile == snapshot.profile)
        broker_profile = runtime.read_profile("broker_read")
        native_profile = runtime.read_profile("native_read")
        ledger = ProtectedLedgerSource(
            broker_profile, PrivateReadTransport(broker_profile, broker)
        )
        basis = NativeBasisSource(native_profile, native)
        git = ProtectedGit(
            snapshot.profile.signing,
            snapshot.profile.data["artifact_root"],
            local,
            local,
        )
        executor = ProtectedExecutor(ledger, basis, journal, git, provider)
        # Join all constructors back to the original checked registry snapshot.
        # No credentials, signing or remote effects have been acquired here.
        _need(_snapshot(profile_id) == snapshot)
        _need(load_runtime_profile(profile_id) == runtime)
        location.check()
        states = (broker._state, native._state, local._state, provider._state)
        return InstalledRuntime(
            executor, resources.pop_all(), journal, states, location
        )


@_safe
def install_profile_journal(profile_id):
    """Exclusive explicit journal creation from final installed registry bytes."""
    _need(type(profile_id) is str and _ID.fullmatch(profile_id))
    snapshot = _snapshot(profile_id)
    admit_installation(snapshot.profile)
    runtime = load_runtime_profile(profile_id)
    _need(runtime.profile_sha256 == snapshot.profile.data["runtime_profile_sha256"])
    _need(_snapshot(profile_id) == snapshot)
    location = _journal_directory(profile_id)
    try:
        _separate_storage(location.directory, snapshot.profile)
        location.check()
        install_journal(
            location.directory,
            host_id=snapshot.host_id,
            registry_utf8=snapshot.registry_utf8,
        )
        # Failed joins leave the exclusive installation for owner inspection.
        _need(_snapshot(profile_id) == snapshot)
        _need(load_runtime_profile(profile_id) == runtime)
        location.check()
    finally:
        location.close()
