"""Fixed installed local object/signing adapters; metadata is DATA only."""

import asyncio
import os
import re
import threading
from functools import wraps

from .protected_copy_codec import _canonical, _need
from .protected_runtime_credentials import _sealed
from .protected_runtime_registry import (
    RuntimeRefused,
    load_runtime_profile,
    _directory,
    _identity,
    _safe,
)

_TOTAL = 55.0
_CLEANUP = 2.0
_INPUT_MAX = 65536
_SCHEMA = "vuoro-protected-local-authority-request/v1"


def _safe_async(function):
    @wraps(function)
    async def run(*args, **kwargs):
        failed = False
        try:
            return await function(*args, **kwargs)
        except Exception:
            failed = True
        if failed:
            raise RuntimeRefused("protected-local-authority-refused")

    return run


class InstalledLocalAuthority:
    """Own one fixed installed profile and its explicitly installed state."""

    @_safe
    def __init__(self, profile_id):
        from .protected_provider_registry import load_installed_profile
        from .protected_provider import _InstalledOperationState

        self.profile = load_installed_profile(profile_id)
        self.runtime = load_runtime_profile(profile_id)
        _need(
            self.runtime.profile_sha256 == self.profile.data["runtime_profile_sha256"]
        )
        self._state = _InstalledOperationState(self.profile)
        self._pid = os.getpid()
        self._closed = False

    @_safe
    def close(self):
        _need(os.getpid() == self._pid)
        if not self._closed:
            self._state.close()
            self._closed = True

    @_safe_async
    async def acquire_base(self, profile, full_oid, target_git_directory):
        _need(profile == self.profile.signing)
        _need(
            type(full_oid) is str
            and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", full_oid)
        )
        target = _directory(str(target_git_directory), private=False)
        before = os.lstat(target)
        descriptor = os.open(
            target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            _need(_identity(os.fstat(descriptor)) == _identity(before))
            result = await self._call("base", full_oid, -1, descriptor)
            _need(
                result
                == _canonical(
                    {"schema": "vuoro-protected-local-base-result/v1", "oid": full_oid}
                )
            )
            _directory(str(target), private=False)
            _need(
                (before.st_dev, before.st_ino)
                == (os.lstat(target).st_dev, os.lstat(target).st_ino)
            )
        finally:
            os.close(descriptor)

    @_safe_async
    async def sign(self, unsigned_commit_bytes):
        _need(
            type(unsigned_commit_bytes) is bytes
            and 0 < len(unsigned_commit_bytes) <= _INPUT_MAX
        )
        descriptor = _sealed(unsigned_commit_bytes)
        try:
            result = await self._call("sign", None, descriptor, -1)
            _need(0 < len(result) <= _INPUT_MAX)
            _need(
                result.startswith(b"-----BEGIN PGP SIGNATURE-----\n")
                and result.rstrip().endswith(b"-----END PGP SIGNATURE-----")
            )
            return result
        finally:
            os.close(descriptor)

    async def _call(self, action, oid, input_fd, target_fd):
        from . import protected_provider as lifecycle

        _need(not self._closed and os.getpid() == self._pid)
        deadline = asyncio.get_running_loop().time() + _TOTAL
        token = lifecycle._DEADLINE.set(deadline)
        group = None
        group_token = None
        caller = (os.getpid(), threading.get_ident(), asyncio.current_task())
        primary = cleanup_error = cancelled = None
        try:
            group = lifecycle._OperationGroup(self.profile)
            group_token = lifecycle._GROUP.set(group)
            async with asyncio.timeout_at(deadline - _CLEANUP):
                await self._state.begin(group)
                process = await lifecycle._spawn(
                    self.profile.data["python_path"],
                    "-I",
                    "-m",
                    "vuoro_reconciler.protected_runtime_local_child",
                    str(group.procs_fd),
                    str(input_fd),
                    str(target_fd),
                    pass_fds=tuple(
                        fd for fd in (group.procs_fd, input_fd, target_fd) if fd >= 3
                    ),
                    env=lifecycle._environment(),
                    start_new_session=True,
                    stdin=lifecycle.subprocess.PIPE,
                    stdout=lifecycle.subprocess.PIPE,
                    stderr=lifecycle.subprocess.DEVNULL,
                )
                request = _canonical(
                    {
                        "schema": _SCHEMA,
                        "profile_id": self.runtime.profile_id,
                        "runtime_profile_sha256": self.runtime.profile_sha256,
                        "provider_registry_sha256": self.profile.registry_sha256,
                        "provider_profile_sha256": self.profile.profile_sha256,
                        "action": action,
                        "base_oid": oid,
                    }
                )
                result = await lifecycle._capture(process, request)
        except BaseException as error:
            primary = error
            if isinstance(error, asyncio.CancelledError):
                cancelled = error
        finally:
            try:
                if group is not None:
                    try:
                        if not group.clean:
                            group.kill()
                        if self._state.owner == caller and self._state.admitted:
                            await self._state.finalize(group)
                    except BaseException as error:
                        if (
                            isinstance(error, asyncio.CancelledError)
                            and cancelled is None
                        ):
                            cancelled = error
                        if cleanup_error is None:
                            cleanup_error = error
                    finally:
                        try:
                            group.close()
                        except BaseException as error:
                            if (
                                isinstance(error, asyncio.CancelledError)
                                and cancelled is None
                            ):
                                cancelled = error
                            if cleanup_error is None:
                                cleanup_error = error
            finally:
                try:
                    if self._state.owner == caller:
                        self._state.release()
                except BaseException as error:
                    if isinstance(error, asyncio.CancelledError) and cancelled is None:
                        cancelled = error
                    if cleanup_error is None:
                        cleanup_error = error
                finally:
                    if group_token is not None:
                        lifecycle._GROUP.reset(group_token)
                    lifecycle._DEADLINE.reset(token)
        if cancelled is not None:
            if cleanup_error is not None:
                cancelled.add_note("protected-local-authority-cleanup-uncertain")
            raise cancelled
        if primary is not None or cleanup_error is not None:
            raise RuntimeRefused("protected-local-authority-refused")
        return result
