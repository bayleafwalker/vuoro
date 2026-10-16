"""Bounded private material IPC; installed metadata selects the fixed child."""

import array
import asyncio
import fcntl
import os
import socket
import stat
import sys
import threading

from .protected_copy_codec import _canonical, _closed, _need, _read
from .protected_runtime_reader_child import _FIELDS

_TOTAL = 15.0
_CLEANUP = 2.0
_F_GET_SEALS = 1034
_SEALS = 15
_DESCRIPTOR_NOTE = "private-reader-descriptor-close-uncertain"


class _DescriptorCloseUncertain(ValueError):
    def __init__(self):
        super().__init__(_DESCRIPTOR_NOTE)


def _descriptor_failure(primary):
    if primary is None:
        raise _DescriptorCloseUncertain() from None
    primary.add_note(_DESCRIPTOR_NOTE)


def _close(descriptors):
    uncertain = False
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            uncertain = True
    return uncertain


def _discard_packet(packet):
    uncertain = False
    for level, control, data in packet[1]:
        if level == socket.SOL_SOCKET and control == socket.SCM_RIGHTS:
            rights = array.array("i")
            rights.frombytes(data[: len(data) - len(data) % rights.itemsize])
            uncertain = _close(rights) or uncertain
    return uncertain


async def _receive(channel):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    transferred = False
    discard_uncertain = False

    def ready():
        nonlocal discard_uncertain
        try:
            packet = channel.recvmsg(
                2049,
                socket.CMSG_SPACE(4 * array.array("i").itemsize),
                socket.MSG_CMSG_CLOEXEC,
            )
        except BlockingIOError:
            return
        except BaseException as error:
            if not future.done():
                future.set_exception(error)
        else:
            if future.done():
                discard_uncertain = _discard_packet(packet) or discard_uncertain
            else:
                future.set_result(packet)
        loop.remove_reader(channel.fileno())

    loop.add_reader(channel.fileno(), ready)
    try:
        packet = await future
        transferred = True
        return packet
    finally:
        primary = sys.exc_info()[1]
        loop.remove_reader(channel.fileno())
        if not transferred and future.done() and not future.cancelled():
            if future.exception() is None:
                discard_uncertain = _discard_packet(future.result()) or discard_uncertain
        if discard_uncertain:
            _descriptor_failure(primary)


def _decode_packet(packet, runtime, kind, provider):
    raw, controls, flags, address = packet
    descriptors = []
    try:
        valid_controls = True
        for level, control, data in controls:
            if level != socket.SOL_SOCKET or control != socket.SCM_RIGHTS:
                valid_controls = False
                continue
            rights = array.array("i")
            size = len(data) - len(data) % rights.itemsize
            rights.frombytes(data[:size])
            descriptors.extend(rights)
            if size != len(data):
                valid_controls = False
        _need(valid_controls and len(controls) == 1)
        _need(not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
        value = _read(raw, 2048)
        _closed(
            value,
            "schema profile_id runtime_profile_sha256 provider_registry_sha256 provider_profile_sha256 kind count",
        )
        expected = {
            "schema": "vuoro-private-reader-materials/v1",
            "profile_id": runtime.profile_id,
            "runtime_profile_sha256": runtime.profile_sha256,
            "provider_registry_sha256": provider.registry_sha256,
            "provider_profile_sha256": provider.profile_sha256,
            "kind": kind,
            "count": len(_FIELDS[kind]),
        }
        _need(value == expected and type(value["count"]) is int)
        _need(len(descriptors) == len(_FIELDS[kind]))
        materials = []
        for descriptor, (_, maximum, _) in zip(descriptors, _FIELDS[kind], strict=True):
            info = os.fstat(descriptor)
            _need(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and info.st_nlink == 0
                and stat.S_IMODE(info.st_mode) == 0o600
            )
            _need(not os.get_inheritable(descriptor))
            _need(fcntl.fcntl(descriptor, _F_GET_SEALS) & _SEALS == _SEALS)
            _need(0 < info.st_size <= maximum)
            os.lseek(descriptor, 0, os.SEEK_SET)
            raw = os.read(descriptor, maximum + 1)
            _need(len(raw) == info.st_size and os.read(descriptor, 1) == b"")
            materials.append(raw)
        return tuple(materials)
    finally:
        primary = sys.exc_info()[1]
        if _close(descriptors):
            _descriptor_failure(primary)


async def acquire_materials(runtime, kind, provider, state):
    """Contain and terminate blocking acquisition before returning material."""
    from . import protected_provider as lifecycle

    _need(kind in _FIELDS)
    deadline = asyncio.get_running_loop().time() + _TOTAL
    deadline_token = lifecycle._DEADLINE.set(deadline)
    group = process = parent = child = None
    group_token = None
    caller = (os.getpid(), threading.get_ident(), asyncio.current_task())
    try:
        group = lifecycle._OperationGroup(provider)
        group_token = lifecycle._GROUP.set(group)
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.setblocking(False)
        async with asyncio.timeout_at(deadline - _CLEANUP):
            await state.begin(group)
            process = await lifecycle._spawn(
                provider.data["python_path"],
                "-I",
                "-m",
                "vuoro_reconciler.protected_runtime_reader_child",
                str(group.procs_fd),
                str(child.fileno()),
                env=lifecycle._environment(),
                pass_fds=(group.procs_fd, child.fileno()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            child.close()
            child = None
            request = _canonical(
                {
                    "schema": "vuoro-private-reader-request/v1",
                    "profile_id": runtime.profile_id,
                    "kind": kind,
                    "runtime_profile_sha256": runtime.profile_sha256,
                    "provider_registry_sha256": provider.registry_sha256,
                    "provider_profile_sha256": provider.profile_sha256,
                }
            )
            process.stdin.write(request)
            await process.stdin.drain()
            process.stdin.close()
            packet = await _receive(parent)
            materials = _decode_packet(packet, runtime, kind, provider)
            await process.wait()
            _need(process.returncode == 0)
            return materials
    finally:
        primary = sys.exc_info()[1]
        descriptor_uncertain = isinstance(primary, _DescriptorCloseUncertain) or (
            primary is not None
            and _DESCRIPTOR_NOTE in getattr(primary, "__notes__", ())
        )
        cleanup_error = None
        state_error = False
        try:
            if process is not None:
                try:
                    lifecycle._kill_tree(process)
                finally:
                    try:
                        process._transport.close()
                    finally:
                        # Reaping owns a separate task so further caller
                        # cancellation cannot abandon this exact child.
                        reaper = asyncio.create_task(process.wait())
                        interrupted = None
                        while True:
                            try:
                                await asyncio.shield(reaper)
                                break
                            except asyncio.CancelledError as error:
                                if reaper.cancelled():
                                    raise
                                if interrupted is None:
                                    interrupted = error
                        if interrupted is not None and not isinstance(
                            primary, asyncio.CancelledError
                        ):
                            raise interrupted
                _need(process._transport.closed)
            elif group is not None:
                group.kill()
        except BaseException as error:
            cleanup_error = error
        finally:
            for channel in (parent, child):
                if channel is not None:
                    try:
                        channel.close()
                    except BaseException as error:
                        if cleanup_error is None:
                            cleanup_error = error
            try:
                if (
                    cleanup_error is None
                    and not descriptor_uncertain
                    and group is not None
                    and state.owner == caller
                    and state.admitted
                ):
                    await state.finalize(group)
            except BaseException as error:
                cleanup_error = error
                state_error = True
            finally:
                if state.owner == caller:
                    try:
                        state.release()
                    except BaseException as error:
                        if cleanup_error is None:
                            cleanup_error = error
                            state_error = True
            try:
                if group is not None:
                    group.close()
            except BaseException as error:
                state_error = False
                if cleanup_error is None:
                    cleanup_error = error
            finally:
                if group_token is not None:
                    lifecycle._GROUP.reset(group_token)
                lifecycle._DEADLINE.reset(deadline_token)
        if cleanup_error is not None:
            cancellation = (
                primary
                if isinstance(primary, asyncio.CancelledError)
                else cleanup_error
                if isinstance(cleanup_error, asyncio.CancelledError)
                else None
            )
            if cancellation is not None:
                cancellation.add_note(
                    "private-reader-state-clear-uncertain"
                    if state_error
                    else "private-reader-containment-uncertain"
                )
                # Work cancellation resumes after this finally block;
                # first cancellation during cleanup is raised explicitly.
                if cancellation is not primary:
                    raise cancellation
            else:
                raise cleanup_error
