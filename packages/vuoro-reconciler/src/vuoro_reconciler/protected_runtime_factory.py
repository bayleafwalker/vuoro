"""Fixed installed protected composition; never selects caller credentials."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path

from .execution_journal import ExecutionJournal, install_journal, rebind_empty_journal
from .protected_basis_source import NativeBasisSource
from .protected_copy_codec import _hash, _need
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


def _operation_state_check(profile_id, state, expected_profile):
    from .protected_provider_registry import _owned, _state_paths

    _need(state.profile == expected_profile)
    directory, lock, active_name = _state_paths(profile_id)
    directory_info, lock_info = os.fstat(state.directory), os.fstat(state.lock)
    named_directory, named_lock = os.lstat(directory), os.lstat(lock)
    for info, mode, is_directory in (
        (directory_info, 0o700, True),
        (lock_info, 0o600, False),
        (named_directory, 0o700, True),
        (named_lock, 0o600, False),
    ):
        _owned(info, mode, is_directory)
    _need((directory_info.st_dev, directory_info.st_ino) == (named_directory.st_dev, named_directory.st_ino))
    _need((lock_info.st_dev, lock_info.st_ino) == (named_lock.st_dev, named_lock.st_ino))
    try:
        os.stat(active_name, dir_fd=state.directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _need(False)


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

    def journal_identities(self):
        """Pin the journal directory and children before handing off to SQLite."""
        from .execution_journal import DB_NAME, LOCK_NAME, _identity, _owned

        self.check()
        directory_fd = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        descriptors = []
        try:
            _owned(os.fstat(directory_fd), 0o700, True)
            identities = [_identity(os.fstat(directory_fd))]
            for name in (LOCK_NAME, DB_NAME):
                fd = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory_fd,
                )
                descriptors.append(fd)
                info = os.fstat(fd)
                _owned(info, 0o600)
                named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                _owned(named, 0o600)
                _need(_identity(info) == _identity(named))
                identities.append(_identity(info))
            self.check()
            return tuple(identities)
        finally:
            for fd in descriptors:
                os.close(fd)
            os.close(directory_fd)

    def check_journal_identities(self, expected):
        from .execution_journal import DB_NAME, LOCK_NAME, _identity, _owned

        self.check()
        directory_fd = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            infos = [os.fstat(directory_fd)]
            for name in (LOCK_NAME, DB_NAME):
                infos.append(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
            for info, identity, mode, is_directory in zip(
                infos, expected, (0o700, 0o600, 0o600), (True, False, False), strict=True
            ):
                _owned(info, mode, is_directory)
                _need(_identity(info) == identity)
        finally:
            os.close(directory_fd)

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


@_safe
def rebind_empty_profile_journal(
    profile_id,
    *,
    expected_old_registry_sha256,
    expected_profile_sha256,
    expected_host_id,
    operation_id,
):
    """Explicitly rebind an empty installed journal after an admitted registry update."""
    _need(type(profile_id) is str and _ID.fullmatch(profile_id))
    snapshot = _snapshot(profile_id)
    _need(snapshot.profile.profile_sha256 == expected_profile_sha256)
    _need(snapshot.host_id == expected_host_id)
    admit_installation(snapshot.profile)
    runtime = load_runtime_profile(profile_id)
    _need(runtime.profile_sha256 == snapshot.profile.data["runtime_profile_sha256"])
    provider = _provider(profile_id)
    state = provider._state
    locked = False
    try:
        try:
            fcntl.flock(state.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            _need(False)
        _operation_state_check(profile_id, state, snapshot.profile)
        _need(_snapshot(profile_id) == snapshot)
        _need(load_runtime_profile(profile_id) == runtime)
        location = _journal_directory(profile_id)
        try:
            _separate_storage(location.directory, snapshot.profile)
            location.check()
            journal_identities = location.journal_identities()

            def check_pinned_state():
                _operation_state_check(profile_id, state, snapshot.profile)
                _need(_snapshot(profile_id) == snapshot)
                _need(load_runtime_profile(profile_id) == runtime)
                location.check_journal_identities(journal_identities)

            receipt = rebind_empty_journal(
                location.directory,
                host_id=snapshot.host_id,
                profile_id=profile_id,
                expected_old_registry_sha256=expected_old_registry_sha256,
                new_registry_utf8=snapshot.registry_utf8,
                operation_id=operation_id,
                expected_physical_identities=journal_identities,
                identity_check=check_pinned_state,
            )
            _need(_snapshot(profile_id) == snapshot)
            _need(load_runtime_profile(profile_id) == runtime)
            location.check_journal_identities(journal_identities)
            return {
                "profile_id": profile_id,
                "host_id": snapshot.host_id,
                "profile_sha256": snapshot.profile.profile_sha256,
                **receipt,
            }
        finally:
            location.close()
    finally:
        if locked:
            fcntl.flock(state.lock, fcntl.LOCK_UN)
        provider.close()


@_safe
def read_profile_journal_rebind_receipt(
    profile_id, *, operation_id, expected_profile_sha256, expected_host_id
):
    """Read one committed transition receipt without retrying or changing state."""
    _need(type(profile_id) is str and _ID.fullmatch(profile_id))
    snapshot = _snapshot(profile_id)
    _need(snapshot.profile.profile_sha256 == expected_profile_sha256)
    _need(snapshot.host_id == expected_host_id)
    admit_installation(snapshot.profile)
    runtime = load_runtime_profile(profile_id)
    _need(runtime.profile_sha256 == snapshot.profile.data["runtime_profile_sha256"])
    provider = _provider(profile_id)
    state = provider._state
    locked = False
    try:
        try:
            fcntl.flock(state.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            _need(False)
        _operation_state_check(profile_id, state, snapshot.profile)
        _need(_snapshot(profile_id) == snapshot)
        location = _journal_directory(profile_id)
        try:
            location.check()
            journal = ExecutionJournal(
                location.directory,
                host_id=snapshot.host_id,
                registry_utf8=snapshot.registry_utf8,
            )
            try:
                receipt = journal.registry_rebind_receipt(operation_id)
            finally:
                journal.close()
            _need(_snapshot(profile_id) == snapshot)
            _need(load_runtime_profile(profile_id) == runtime)
            _operation_state_check(profile_id, state, snapshot.profile)
            location.check()
            return receipt
        finally:
            location.close()
    finally:
        if locked:
            fcntl.flock(state.lock, fcntl.LOCK_UN)
        provider.close()
