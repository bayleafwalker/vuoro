"""Fixed scoped public-child provider operations; results are DATA only."""

from __future__ import annotations
import asyncio
import contextvars
import ctypes
import fcntl
import base64
from dataclasses import dataclass
import os
import pwd
from pathlib import Path
import signal
import subprocess
import sys
import platform
import time
import uuid
import threading

from . import execution_journal as journal
from . import protected_git as git
from .protected_copy_codec import (
    _canonical,
    _closed,
    _hash,
    _need,
    _read,
    decode_protected_copy,
)
from .protected_provider_registry import (
    ProviderRefused,
    InstalledProviderProfile,
    _decode,
    load_installed_profile,
    _validate_cgroup_root,
    _path_state,
    _owned,
    _state_paths,
    _check_state_install,
)

_INPUT_FIELDS = "schema operation profile_id registry_sha256 profile_sha256 request_utf8_b64 local_ready_utf8_b64 protected_copy_utf8_b64 binding_utf8_b64"
_RESULT_FIELDS = "schema operation effect_key binding_sha256 request_sha256 status observation_utf8_b64"
_OPERATIONS = {
    "branch-lookup",
    "branch-create",
    "pr-lookup",
    "pr-create",
    "capability-check",
}
_TOTAL = 60.0
_INPUT_MAX = 12 * 1024 * 1024
_OUTPUT_MAX = 65536
_CLEANUP_RESERVE = 2.0
_DEADLINE = contextvars.ContextVar("protected_provider_deadline", default=None)
_GROUP = contextvars.ContextVar("protected_provider_cgroup", default=None)


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _capability_request(record, binding):
    return _canonical(
        {
            "schema": "vuoro-protected-provider-capability-request/v1",
            "effect_key": record.effect_key,
            "binding_sha256": _hash(record.binding_utf8),
            "repository": binding["publication_target"]["repository"],
            "provider_profile_sha256": binding["provider_profile_sha256"],
        }
    )


def _request(record, effect, binding, local, operation):
    if operation == "capability-check":
        return _capability_request(record, binding)
    common = {
        "effect_key": record.effect_key,
        "binding_sha256": _hash(record.binding_utf8),
        "repository": effect.repository,
        "branch": "vuoro-protected/" + record.effect_key,
        "commit_oid": local["commit_oid"],
        "provider_profile_sha256": binding["provider_profile_sha256"],
    }
    if operation.startswith("branch-"):
        return _canonical(
            {
                "schema": "vuoro-protected-branch-request/v1",
                **common,
                "base_parent_oid": local["base_parent_oid"],
            }
        )
    params = _read(effect.parameters_utf8)
    public = _canonical(journal._public_binding(record, binding, local, effect))
    body = (
        params["rationale"]
        + "\n\n<!-- vuoro-protected-publication/v1 "
        + _b64(public)
        + " -->\n"
    ).encode("utf8")
    return _canonical(
        {
            "schema": "vuoro-protected-pr-request/v1",
            **common,
            "head_repository": binding["publication_target"]["head_repository"],
            "base_branch": binding["publication_target"]["base_branch"],
            "title": params["title"],
            "body_utf8_b64": _b64(body),
            "body_sha256": _hash(body),
        }
    )


def _validate_input(raw, profile):
    _need(type(profile) is InstalledProviderProfile)
    value = _read(raw, _INPUT_MAX)
    _closed(value, _INPUT_FIELDS)
    _need(
        value["schema"] == "vuoro-protected-provider-child-request/v1"
        and value["operation"] in _OPERATIONS
    )
    _need(
        value["profile_id"] == profile.profile_id
        and value["registry_sha256"] == profile.registry_sha256
        and value["profile_sha256"] == profile.profile_sha256
    )
    copy = _decode(value["protected_copy_utf8_b64"], 8 * 1024 * 1024)
    copied = _read(copy, 8 * 1024 * 1024)
    effect = decode_protected_copy(
        copy,
        namespace_utf8=_canonical(copied["locator"]["namespace"]),
        acceptance_id=copied["acceptance"]["acceptance_id"],
        repository=profile.data["effect_repository"],
    )
    bound = _decode(value["binding_utf8_b64"], 16384)
    ready = _decode(value["local_ready_utf8_b64"], 16384)
    record = journal.JournalRecord(
        journal.effect_key(effect),
        bound,
        copy,
        (
            journal.JournalEvent(0, "BOUND", bound),
            journal.JournalEvent(1, "LOCAL_READY", ready),
        ),
        (),
        None,
    )
    binding, _ = git._record(effect, record, profile.signing)
    _need(
        binding["registry_sha256"] == profile.registry_sha256
        and binding["provider_profile_sha256"] == profile.profile_sha256
    )
    _need(binding["publication_target"]["base_branch"] == profile.data["base_branch"])
    local = journal._payload(record, "LOCAL_READY", ready, {}, effect, binding)
    request = _decode(
        value["request_utf8_b64"],
        65536 if value["operation"].startswith("pr-") else 16384,
    )
    _need(request == _request(record, effect, binding, local, value["operation"]))
    if value["operation"] != "capability-check":
        kind = (
            "PR_TRANSMISSION_POSSIBLE"
            if value["operation"].startswith("pr-")
            else "BRANCH_TRANSMISSION_POSSIBLE"
        )
        journal._payload(
            record,
            kind,
            request,
            {"LOCAL_READY": local, "BRANCH_OBSERVED": {}},
            effect,
            binding,
        )
    return value, record, effect, _read(request, 65536), local


@dataclass(frozen=True, slots=True, repr=False)
class ProviderResult:
    operation: str
    effect_key: str
    binding_sha256: str
    request_sha256: str
    status: str
    observation_utf8: bytes | None


def _result(raw, value, record, request):
    response = _read(raw, _OUTPUT_MAX)
    _closed(response, _RESULT_FIELDS)
    _need(response["schema"] == "vuoro-protected-provider-child-result/v1")
    for key, expected in {
        "operation": value["operation"],
        "effect_key": record.effect_key,
        "binding_sha256": _hash(record.binding_utf8),
        "request_sha256": _hash(_canonical(request)),
    }.items():
        _need(response[key] == expected)
    allowed = (
        {"ABSENT", "OBSERVED", "UNKNOWN", "CONFLICT", "REFUSED"}
        if value["operation"].endswith("-lookup")
        else {"CAPABILITY_PRESENT", "UNKNOWN", "REFUSED"}
        if value["operation"] == "capability-check"
        else {"TRANSMISSION_POSSIBLE", "UNKNOWN", "CONFLICT", "REFUSED"}
    )
    _need(response["status"] in allowed)
    observation = None
    if response["status"] == "OBSERVED":
        observation = _decode(response["observation_utf8_b64"], 65536)
        kind = "PR_OBSERVED" if value["operation"] == "pr-lookup" else "BRANCH_OBSERVED"
        binding = _read(record.binding_utf8)
        ready = _read(record.events[1].payload_utf8)
        effect = decode_protected_copy(
            record.protected_copy_utf8,
            namespace_utf8=_canonical(binding["namespace"]),
            acceptance_id=binding["acceptance_id"],
            repository=request["repository"],
        )
        marker = (
            "PR_TRANSMISSION_POSSIBLE"
            if kind == "PR_OBSERVED"
            else "BRANCH_TRANSMISSION_POSSIBLE"
        )
        journal._payload(
            record,
            kind,
            observation,
            {"LOCAL_READY": ready, marker: request},
            effect,
            binding,
        )
    else:
        _need(response["observation_utf8_b64"] is None)
    return ProviderResult(
        response["operation"],
        response["effect_key"],
        response["binding_sha256"],
        response["request_sha256"],
        response["status"],
        observation,
    )


def _environment():
    home = pwd.getpwuid(os.getuid()).pw_dir
    return {
        "PATH": "/run/current-system/sw/bin:/usr/bin:/bin",
        "HOME": home,
        "XDG_CONFIG_HOME": home + "/.config",
        "LANG": "C",
        "LC_ALL": "C",
    }


class _OperationGroup:
    def __init__(self, profile):
        self.root = Path(profile.data["cgroup_root"])
        self.root_state = _validate_cgroup_root(self.root)
        self.root_fd = os.open(
            self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        self.name = "operation-" + uuid.uuid4().hex
        self.descriptors = []
        self.clean = False
        self.processes = []
        self.deadline = _DEADLINE.get() or time.monotonic() + _TOTAL
        try:
            _need(_path_state(self.root) == self.root_state)
            os.mkdir(self.name, 0o700, dir_fd=self.root_fd)
            self.directory = os.open(
                self.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=self.root_fd,
            )
            self.descriptors.append(self.directory)
            for name, flags in (
                ("cgroup.procs", os.O_WRONLY),
                ("cgroup.kill", os.O_WRONLY),
                ("cgroup.events", os.O_RDONLY),
            ):
                self.descriptors.append(
                    os.open(
                        name,
                        flags | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=self.directory,
                    )
                )
            self.procs_fd, self.kill_fd, self.events_fd = self.descriptors[1:]
            self.identity = (
                os.fstat(self.directory).st_dev,
                os.fstat(self.directory).st_ino,
            )
            self.check()
        except BaseException:
            for descriptor in reversed(self.descriptors):
                os.close(descriptor)
            os.close(self.root_fd)
            raise

    def check(self):
        _need(_path_state(self.root) == self.root_state)
        root = os.fstat(self.root_fd)
        _need(
            (root.st_dev, root.st_ino)
            == (os.lstat(self.root).st_dev, os.lstat(self.root).st_ino)
        )
        current = os.stat(self.name, dir_fd=self.root_fd, follow_symlinks=False)
        _owned(current, 0o700, True)
        _need((current.st_dev, current.st_ino) == self.identity)
        _need(
            (os.fstat(self.directory).st_dev, os.fstat(self.directory).st_ino)
            == self.identity
        )

    def kill(self):
        self.clean = False
        layout_valid = True
        try:
            self.check()
        except Exception:
            layout_valid = False
        # The already validated held control FD owns this exact kernel group,
        # even if its directory is renamed. Kill before reporting path drift.
        _need(os.write(self.kill_fd, b"1") == 1)
        limit = min(self.deadline, time.monotonic() + _CLEANUP_RESERVE)
        while True:
            os.lseek(self.events_fd, 0, os.SEEK_SET)
            raw = os.read(self.events_fd, 8193)
            _need(len(raw) <= 8192)
            fields = {}
            for line in raw.decode("ascii").splitlines():
                key, value = line.split()
                _need(key not in fields)
                fields[key] = value
            _need(fields.get("populated") in ("0", "1"))
            if fields["populated"] == "0":
                break
            _need(time.monotonic() < limit)
            time.sleep(0.001)
        self.clean = True
        _need(layout_valid)
        self.check()

    def close(self):
        try:
            # Failed cgroup.kill/populated proof preserves the operation group
            # and refuses. It is an activation blocker, never cleanup success.
            _need(self.clean)
            self.check()
            os.rmdir(self.name, dir_fd=self.root_fd)
        finally:
            for descriptor in reversed(self.descriptors):
                os.close(descriptor)
            os.close(self.root_fd)


def _kill_tree(process):
    group = getattr(process, "_provider_cgroup", None)
    descriptor = getattr(process, "_provider_pidfd", None)
    try:
        if group is not None:
            group.kill()
    finally:
        # Before the launcher has joined, only this stable PID can be owned.
        # There is deliberately no PID/group reuse or snapshot fallback.
        if descriptor is not None:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(descriptor)
        process._provider_pidfd = None


class _SignalAction(ctypes.Structure):
    # Linux/glibc x86_64 sigaction layout, verified against installed headers.
    _fields_ = [
        ("handler", ctypes.c_void_p),
        ("mask", ctypes.c_ulong * 16),
        ("flags", ctypes.c_int),
        ("restorer", ctypes.c_void_p),
    ]


def _normal_child_signals():
    _need(platform.system() == "Linux" and platform.machine() == "x86_64")
    action = _SignalAction()
    library = ctypes.CDLL(None, use_errno=True)
    _need(library.sigaction(signal.SIGCHLD, None, ctypes.byref(action)) == 0)
    # Default handling leaves each child unreaped until its owning Popen waits.
    _need(action.handler is None and not action.flags & 2)
    _need(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL)


class _WriteProtocol(asyncio.streams.FlowControlMixin):
    def __init__(self, loop):
        super().__init__(loop=loop)
        self.closed = loop.create_future()

    def connection_lost(self, error):
        super().connection_lost(error)
        if not self.closed.done():
            if error is None:
                self.closed.set_result(None)
            else:
                self.closed.set_exception(error)
                self.closed.exception()

    def _get_close_waiter(self, stream):
        return self.closed


class _PipeOwner:
    def __init__(self, process):
        self.process = process
        self.transports = []
        self.closed = False

    def close(self):
        pending = sys.exception()
        self.closed = False
        failure = None
        pipes = tuple(
            pipe
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr)
            if pipe is not None
        )
        for resource in (*self.transports, *pipes):
            try:
                resource.close()
            except BaseException as error:
                if failure is None:
                    failure = error
        complete = True
        for transport in self.transports:
            try:
                complete = transport.is_closing() and complete
            except BaseException as error:
                complete = False
                if failure is None:
                    failure = error
        for pipe in pipes:
            try:
                complete = pipe.closed and complete
            except BaseException as error:
                complete = False
                if failure is None:
                    failure = error
        self.closed = failure is None and complete
        if failure is not None or not complete:
            if isinstance(pending, asyncio.CancelledError):
                pending.add_note("protected-provider-pipe-cleanup-uncertain")
                return
            if failure is not None:
                raise failure
            raise ProviderRefused("protected-provider-pipe-cleanup-uncertain")


class _OwnedProcess:
    def __init__(self, process, descriptor, group):
        self._process = process
        self.pid = process.pid
        self._provider_pidfd = descriptor
        self._provider_cgroup = group
        self._transport = _PipeOwner(process)
        self.stdin = self.stdout = self.stderr = None

    @property
    def returncode(self):
        return self._process.poll()

    async def wait(self):
        while self._process.poll() is None:
            await asyncio.sleep(0.005)
        return self._process.returncode


async def _cleanup_owned(process, tasks=(), primary=None):
    pending = primary if primary is not None else sys.exception()
    failure = None
    try:
        _kill_tree(process)
    except BaseException as error:
        failure = error
    try:
        process._transport.close()
    except BaseException as error:
        if failure is None:
            failure = error
    for task in tasks:
        if not task.done():
            task.cancel()

    async def drain_and_reap():
        problem = None
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException as error:
            problem = error
        finally:
            try:
                await process.wait()
            except BaseException as error:
                if problem is None:
                    problem = error
            finally:
                await asyncio.sleep(0)
        return problem

    completion = asyncio.create_task(drain_and_reap())
    interrupted = None
    while True:
        try:
            problem = await asyncio.shield(completion)
            break
        except asyncio.CancelledError as error:
            if interrupted is None:
                interrupted = error
    if failure is None:
        failure = problem
    if interrupted is not None and not isinstance(pending, asyncio.CancelledError):
        if failure is not None:
            interrupted.add_note("protected-provider-child-cleanup-uncertain")
        raise interrupted
    if failure is not None:
        if pending is not None:
            pending.add_note("protected-provider-child-cleanup-uncertain")
        else:
            raise failure


async def _create_owned_process(*args, **kwargs):
    _normal_child_signals()
    process = subprocess.Popen(args, **kwargs)
    owner = _OwnedProcess(process, None, _GROUP.get())
    if owner._provider_cgroup is not None:
        owner._provider_cgroup.processes.append(owner)
    try:
        owner._provider_pidfd = os.pidfd_open(process.pid)
    except ProcessLookupError:
        # A normal unreaped exited child is still exclusively owned by Popen.
        process.wait()
    except BaseException as primary:
        # Normal SIGCHLD admission means this exact unreaped child cannot have
        # reused its PID. No arbitrary PID/group lookup is performed.
        try:
            process.kill()
            process.wait()
            if owner._provider_cgroup is not None:
                owner._provider_cgroup.kill()
        finally:
            try:
                owner._transport.close()
            except BaseException:
                primary.add_note("protected-provider-child-cleanup-uncertain")
        raise primary from None
    try:
        loop = asyncio.get_running_loop()
        for name in ("stdout", "stderr"):
            pipe = getattr(process, name)
            if pipe is not None:
                reader = asyncio.StreamReader(limit=65536, loop=loop)
                transport, _ = await loop.connect_read_pipe(
                    lambda reader=reader: asyncio.StreamReaderProtocol(
                        reader, loop=loop
                    ),
                    pipe,
                )
                owner._transport.transports.append(transport)
                setattr(owner, name, reader)
        if process.stdin is not None:
            protocol = _WriteProtocol(loop)
            transport, _ = await loop.connect_write_pipe(
                lambda: protocol, process.stdin
            )
            owner._transport.transports.append(transport)
            owner.stdin = asyncio.StreamWriter(transport, protocol, None, loop)
        return owner
    except BaseException:
        await _cleanup_owned(owner)
        raise


async def _spawn(*args, **kwargs):
    group = _GROUP.get()
    if group is not None:
        group.clean = False
    pending = asyncio.create_task(_create_owned_process(*args, **kwargs))
    cancelled = None
    while True:
        try:
            process = await asyncio.shield(pending)
            break
        except asyncio.CancelledError as error:
            if cancelled is None:
                cancelled = error
        except BaseException:
            try:
                if group is not None:
                    group.kill()
            finally:
                if cancelled is not None:
                    cancelled.add_note("protected-provider-child-cleanup-uncertain")
                    raise cancelled
            raise
    if cancelled is not None:
        await _cleanup_owned(process, primary=cancelled)
        raise cancelled
    return process


_IDENTITY_PROBE = (
    "import os,sys;fd=int(sys.argv[1]);os.write(fd,b'0');os.close(fd);"
    "from vuoro_reconciler.protected_provider_registry import _source_pins;"
    "from vuoro_reconciler.protected_copy_codec import _canonical;"
    "sys.stdout.buffer.write(_canonical(_source_pins()))"
)


async def _capture(process, raw):
    async def read():
        chunks, size = [], 0
        while chunk := await process.stdout.read(65536):
            size += len(chunk)
            _need(size <= _OUTPUT_MAX)
            chunks.append(chunk)
        return b"".join(chunks)

    async def write():
        try:
            process.stdin.write(raw)
            await process.stdin.drain()
        finally:
            process.stdin.close()

    tasks = [
        asyncio.create_task(read()),
        asyncio.create_task(write()),
        asyncio.create_task(process.wait()),
    ]
    try:
        output = await asyncio.gather(*tasks)
        _need(process.returncode == 0)
        return output[0]
    finally:
        await _cleanup_owned(process, tasks)


async def _identity(profile, group):
    data = profile.data
    process = await _spawn(
        data["python_path"],
        "-I",
        "-c",
        _IDENTITY_PROBE,
        str(group.procs_fd),
        pass_fds=(group.procs_fd,),
        env=_environment(),
        start_new_session=True,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _need(await _capture(process, b"") == _canonical(data["module_sha256"]))


class _InstalledOperationState:
    """Fixed admitted local handles; construction is outside operation timeout."""

    def __init__(self, profile):
        _need(type(profile) is InstalledProviderProfile)
        admitted = _check_state_install(profile.profile_id)
        self.profile = profile
        directory, lock, _ = _state_paths(profile.profile_id)
        self.directory = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            self.lock = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                _need(_path_state(lock) == admitted[0])
                for descriptor, path, identity, mode, is_directory in (
                    (self.directory, directory, admitted[1], 0o700, True),
                    (self.lock, lock, admitted[2], 0o600, False),
                ):
                    info = os.fstat(descriptor)
                    current = os.lstat(path)
                    _owned(info, mode, is_directory)
                    _owned(current, mode, is_directory)
                    _need(
                        (info.st_dev, info.st_ino)
                        == identity
                        == (current.st_dev, current.st_ino)
                    )
                _need(_path_state(lock) == admitted[0])
            except BaseException:
                os.close(self.lock)
                raise
        except BaseException:
            os.close(self.directory)
            raise
        self.pid = os.getpid()
        self.owner = None
        self.locked = False
        self.admitted = False
        self.closed = False

    def _caller(self):
        _need(os.getpid() == self.pid and not self.closed)
        return (os.getpid(), threading.get_ident(), asyncio.current_task())

    async def begin(self, group):
        caller = self._caller()
        _need(self.owner is None)
        self.owner = caller
        self.admitted = False
        deadline = _DEADLINE.get() or group.deadline
        while True:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.locked = True
                break
            except BlockingIOError:
                _need(time.monotonic() < deadline - _CLEANUP_RESERVE)
                await asyncio.sleep(0.005)
        await self._rpc(group, "begin")
        self.admitted = True

    async def finalize(self, group):
        _need(self.owner == self._caller() and self.locked and group.clean)
        await asyncio.sleep(0)
        for process in group.processes:
            _need(
                process.returncode is not None
                and process._provider_pidfd is None
                and process._transport.closed
            )
        await self._rpc(group, "finalize")
        self.admitted = False

    async def _rpc(self, group, action):
        _need(self.owner == self._caller() and self.locked)
        helper = _OperationGroup(self.profile)
        token = _GROUP.set(helper)
        cancelled = False
        try:
            raw = _canonical(
                {
                    "schema": "vuoro-protected-operation-state-request/v1",
                    "action": action,
                    "profile_id": self.profile.profile_id,
                    "registry_sha256": self.profile.registry_sha256,
                    "profile_sha256": self.profile.profile_sha256,
                    "operation_id": group.name.removeprefix("operation-"),
                }
            )
            async with asyncio.timeout_at(group.deadline):
                process = await _spawn(
                    self.profile.data["python_path"],
                    "-I",
                    "-m",
                    "vuoro_reconciler.protected_provider_child",
                    "--state",
                    str(helper.procs_fd),
                    str(self.directory),
                    str(self.lock),
                    str(group.events_fd),
                    pass_fds=(
                        helper.procs_fd,
                        self.directory,
                        self.lock,
                        group.events_fd,
                    ),
                    env=_environment(),
                    start_new_session=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                output = await _capture(process, raw)
                _need(output == raw)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            reason = (
                "protected-provider-active-create-uncertain"
                if action == "begin"
                else "protected-provider-state-clear-uncertain"
            )
            raise ProviderRefused(reason) from None
        finally:
            try:
                try:
                    try:
                        if not helper.clean:
                            helper.kill()
                    finally:
                        helper.close()
                except Exception:
                    if not cancelled:
                        raise ProviderRefused(
                            "protected-provider-state-helper-cleanup-uncertain"
                        ) from None
            finally:
                _GROUP.reset(token)

    def release(self):
        _need(self.owner == self._caller())
        try:
            if self.locked:
                fcntl.flock(self.lock, fcntl.LOCK_UN)
        finally:
            self.locked = False
            self.owner = None

    def close(self):
        _need(self.owner is None and os.getpid() == self.pid)
        if not self.closed:
            self.closed = True
            try:
                os.close(self.lock)
            finally:
                os.close(self.directory)


class ProtectedProvider:
    def __init__(self, profile_id):
        self.profile = load_installed_profile(profile_id)
        self._state = _InstalledOperationState(self.profile)

    def close(self):
        self._state.close()

    async def _call(self, record, operation, verified_original=None):
        failed = False
        containment_failed = False
        state_failed = False
        result = None
        deadline = asyncio.get_running_loop().time() + _TOTAL
        token = _DEADLINE.set(deadline)
        group_token = None
        group = None
        try:
            async with asyncio.timeout_at(deadline - _CLEANUP_RESERVE):
                profile = self.profile
                # Fresh installed pins are checked in the contained launcher
                # before credential acquisition; parent disk IO cannot outrun
                # the asynchronous operation deadline.
                _need(type(record) is journal.JournalRecord)
                binding = _read(record.binding_utf8, 16384)
                ready = [
                    e.payload_utf8
                    for e in record.events
                    if e.event_type == "LOCAL_READY"
                ]
                _need(len(ready) == 1)
                local = _read(ready[0], 16384)
                effect = decode_protected_copy(
                    record.protected_copy_utf8,
                    namespace_utf8=_canonical(binding["namespace"]),
                    acceptance_id=binding["acceptance_id"],
                    repository=profile.data["effect_repository"],
                )
                git._record(effect, record, profile.signing)
                if operation == "branch-create":
                    _need(
                        type(verified_original) is git.GitArtifact
                        and verified_original.local_ready_utf8 == ready[0]
                        and verified_original.bundle_relative_path
                        == local["artifact_relative_path"]
                    )
                request = _request(record, effect, binding, local, operation)
                raw = _canonical(
                    {
                        "schema": "vuoro-protected-provider-child-request/v1",
                        "operation": operation,
                        "profile_id": profile.profile_id,
                        "registry_sha256": profile.registry_sha256,
                        "profile_sha256": profile.profile_sha256,
                        "request_utf8_b64": _b64(request),
                        "local_ready_utf8_b64": _b64(ready[0]),
                        "protected_copy_utf8_b64": _b64(record.protected_copy_utf8),
                        "binding_utf8_b64": _b64(record.binding_utf8),
                    }
                )
                value, reconstructed, _, requested, _ = _validate_input(raw, profile)
                data = profile.data
                group = _OperationGroup(profile)
                group_token = _GROUP.set(group)
                await _identity(profile, group)
                await self._state.begin(group)
                process = await _spawn(
                    data["python_path"],
                    "-I",
                    "-m",
                    "vuoro_reconciler.protected_provider_child",
                    "--launch",
                    str(group.procs_fd),
                    pass_fds=(group.procs_fd,),
                    env=_environment(),
                    start_new_session=True,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )

                output = await _capture(process, raw)
                result = _result(output, value, reconstructed, requested)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            try:
                if group is not None:
                    try:
                        if self._state.admitted and group.clean:
                            await self._state.finalize(group)
                    except Exception:
                        state_failed = True
                    finally:
                        try:
                            group.close()
                        except Exception:
                            containment_failed = True
            finally:
                if self._state.owner == (
                    os.getpid(),
                    threading.get_ident(),
                    asyncio.current_task(),
                ):
                    self._state.release()
                if group_token is not None:
                    _GROUP.reset(group_token)
                _DEADLINE.reset(token)
        if containment_failed:
            raise ProviderRefused("protected-provider-containment-uncertain")
        if state_failed:
            raise ProviderRefused("protected-provider-state-clear-uncertain")
        if failed:
            raise ProviderRefused("protected-provider-operation-unknown")
        return result

    async def lookup_branch(self, record):
        return await self._call(record, "branch-lookup")

    async def lookup_pr(self, record):
        return await self._call(record, "pr-lookup")

    async def create_branch_once(self, record, verified_original):
        return await self._call(record, "branch-create", verified_original)

    async def create_pr_once(self, record):
        return await self._call(record, "pr-create")

    async def fresh_capability(self, record):
        return await self._call(record, "capability-check")
