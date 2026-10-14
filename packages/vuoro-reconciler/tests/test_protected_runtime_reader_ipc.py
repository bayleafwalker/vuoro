"""Real private socket/memfd falsifiers; no installed credential authority."""

import array
import asyncio
from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import socket
import sys
import time
import threading
from types import SimpleNamespace

import pytest

from vuoro_reconciler import protected_runtime_reader_ipc as module
from vuoro_reconciler.protected_copy_codec import _canonical
from vuoro_reconciler.protected_runtime_credentials import _sealed


@dataclass(frozen=True)
class Runtime:
    profile_id: str = "disposable"
    profile_sha256: str = "sha256:" + "a" * 64


def provider_pins():
    return dict(
        registry_sha256="sha256:" + "b" * 64, profile_sha256="sha256:" + "c" * 64
    )


def receipt(runtime, kind="native_read", **changes):
    value = {
        "schema": "vuoro-private-reader-materials/v1",
        "profile_id": runtime.profile_id,
        "runtime_profile_sha256": runtime.profile_sha256,
        "provider_registry_sha256": provider_pins()["registry_sha256"],
        "provider_profile_sha256": provider_pins()["profile_sha256"],
        "kind": kind,
        "count": len(module._FIELDS[kind]),
    }
    value.update(changes)
    return _canonical(value)


def rights(packet):
    result = []
    for level, control, data in packet[1]:
        if level == socket.SOL_SOCKET and control == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(data[: len(data) - len(data) % values.itemsize])
            result.extend(values)
    return result


def assert_closed(descriptors):
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def packet(raw, descriptors):
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        control = (
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", descriptors))]
            if descriptors
            else []
        )
        child.sendmsg([raw], control)
        return parent.recvmsg(
            2049,
            socket.CMSG_SPACE(4 * array.array("i").itemsize),
            socket.MSG_CMSG_CLOEXEC,
        )
    finally:
        parent.close()
        child.close()


@pytest.mark.parametrize("kind", ["broker_read", "native_read"])
def test_real_private_packet_material_order_seals_and_received_descriptor_cleanup(kind):
    runtime = Runtime()
    materials = tuple(
        ("fixture-" + str(i)).encode() for i in range(len(module._FIELDS[kind]))
    )
    descriptors = [_sealed(raw) for raw in materials]
    try:
        received = packet(receipt(runtime, kind), descriptors)
        delivered = rights(received)
        assert (
            module._decode_packet(
                received, runtime, kind, SimpleNamespace(**provider_pins())
            )
            == materials
        )
        assert_closed(delivered)
        for fd in descriptors:
            assert os.fstat(fd).st_size > 0
    finally:
        module._close(descriptors)


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "profile",
        "digest",
        "provider-registry",
        "provider-profile",
        "kind",
        "count",
        "bool-count",
        "extra",
        "noncanonical",
        "empty",
        "large",
        "missing-fd",
        "extra-fd",
        "truncated-controls",
    ],
)
def test_malformed_private_packet_closes_all_delivered_rights(fault):
    runtime = Runtime()
    raw = receipt(runtime)
    descriptors = [_sealed(b"fixture") for _ in range(2)]
    if fault == "schema":
        raw = receipt(runtime, schema="wrong")
    elif fault == "profile":
        raw = receipt(runtime, profile_id="foreign")
    elif fault == "digest":
        raw = receipt(runtime, runtime_profile_sha256="sha256:" + "b" * 64)
    elif fault == "provider-registry":
        raw = receipt(runtime, provider_registry_sha256="sha256:" + "d" * 64)
    elif fault == "provider-profile":
        raw = receipt(runtime, provider_profile_sha256="sha256:" + "d" * 64)
    elif fault == "kind":
        raw = receipt(runtime, kind="broker_read")
    elif fault == "count":
        raw = receipt(runtime, count=3)
    elif fault == "bool-count":
        raw = receipt(runtime, count=True)
    elif fault == "extra":
        raw = receipt(runtime, credential_selector="forged")
    elif fault == "noncanonical":
        raw = raw + b" "
    elif fault == "empty":
        raw = b""
    elif fault == "large":
        raw = b"x" * 4096
    elif fault == "missing-fd":
        os.close(descriptors.pop())
    elif fault in {"extra-fd", "truncated-controls"}:
        descriptors.extend(
            _sealed(b"extra") for _ in range(1 if fault == "extra-fd" else 8)
        )
    try:
        received = packet(raw, descriptors)
        delivered = rights(received)
        with pytest.raises((ValueError, OSError)):
            module._decode_packet(
                received, runtime, "native_read", SimpleNamespace(**provider_pins())
            )
        assert_closed(delivered)
    finally:
        module._close(descriptors)


@pytest.mark.parametrize(
    "fault", ["unsealed", "mode", "empty", "large", "inheritable", "regular-file"]
)
def test_untrusted_received_descriptor_is_refused_and_every_descriptor_closed(
    tmp_path, fault
):
    runtime = Runtime()
    descriptors = [_sealed(b"fixture"), _sealed(b"fixture")]
    try:
        if fault == "unsealed":
            os.close(descriptors[1])
            descriptors[1] = os.memfd_create(
                "unsealed", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
            )
            os.fchmod(descriptors[1], 0o600)
            os.write(descriptors[1], b"fixture")
        elif fault == "mode":
            os.fchmod(descriptors[1], 0o644)
        elif fault == "empty":
            os.close(descriptors[1])
            descriptors[1] = _sealed(b"")
        elif fault == "large":
            os.close(descriptors[1])
            descriptors[1] = _sealed(b"x" * 16385)
        elif fault == "regular-file":
            path = tmp_path / "private"
            path.write_bytes(b"fixture")
            path.chmod(0o600)
            os.close(descriptors[1])
            descriptors[1] = os.open(path, os.O_RDONLY)
        received = packet(receipt(runtime), descriptors)
        delivered = rights(received)
        if fault == "inheritable":
            os.set_inheritable(delivered[1], True)
        with pytest.raises((ValueError, OSError)):
            module._decode_packet(
                received, runtime, "native_read", SimpleNamespace(**provider_pins())
            )
        assert_closed(delivered)
    finally:
        module._close(descriptors)


def test_receive_task_cancel_at_successful_descriptor_delivery_closes_packet(
    monkeypatch,
):
    runtime = Runtime()
    descriptors = [_sealed(b"fixture"), _sealed(b"fixture")]
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.setblocking(False)
    delivered = []

    async def workflow():
        loop = asyncio.get_running_loop()
        task = None

        class CancelOnDelivery(asyncio.Future):
            def set_result(self, value):
                delivered.extend(rights(value))
                super().set_result(value)
                task.cancel()

        monkeypatch.setattr(loop, "create_future", lambda: CancelOnDelivery(loop=loop))
        task = asyncio.create_task(module._receive(parent))
        child.sendmsg(
            [receipt(runtime)],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", descriptors))],
        )
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(workflow())
        assert len(delivered) == 2
        assert_closed(delivered)
    finally:
        module._close(delivered)
        module._close(descriptors)
        parent.close()
        child.close()


def test_cancel_before_packet_removes_reader_and_leaves_no_received_fds():
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.setblocking(False)

    async def workflow():
        task = asyncio.create_task(module._receive(parent))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not asyncio.get_running_loop().remove_reader(parent.fileno())

    try:
        asyncio.run(workflow())
    finally:
        parent.close()
        child.close()


@pytest.fixture
def lifecycle(monkeypatch):
    """Optional unmerged public source is a fixture path, never runtime input."""
    try:
        return importlib.import_module("vuoro_reconciler.protected_provider")
    except ModuleNotFoundError:
        directory = os.environ.get("Q4_PROVIDER_TEST_SOURCE_DIR")
        if not directory:
            pytest.skip("provider lifecycle source not merged/configured")
        import vuoro_reconciler

        monkeypatch.setattr(
            vuoro_reconciler, "__path__", [*vuoro_reconciler.__path__, directory]
        )
        return importlib.import_module("vuoro_reconciler.protected_provider")


@pytest.fixture
def group_root():
    path = os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT")
    if not path:
        pytest.skip("actual delegated cgroup fixture not configured")
    root = Path(path)
    assert root.is_dir()
    return root


def private_child_script(mode, pid_path):
    # Explicit test child joins the real owned cgroup BEFORE fixture material
    # or any descendant. It performs no registry/auth/network operation.
    return f"""import array,fcntl,json,os,socket,sys,time
def publish_pid():
 with open({str(pid_path)+'.pending'!r},"w") as output: output.write(str(os.getpid()))
 os.replace({str(pid_path)+'.pending'!r},{str(pid_path)!r})
os.write(int(sys.argv[1]),b"0");os.close(int(sys.argv[1]))
channel=socket.socket(fileno=int(sys.argv[2]))
request=json.loads(sys.stdin.buffer.read())
if {mode!r} in ("stall","descendant"):
 child=os.fork()
 if child==0:
  os.setsid()
  publish_pid()
  time.sleep(30)
 else: time.sleep(30)
else:
 fds=[]
 for raw in [b"fixture-ca",b"fixture-token"]:
  fd=os.memfd_create("private-fixture",os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.fchmod(fd,0o600);os.write(fd,raw);fcntl.fcntl(fd,1033,15);fds.append(fd)
 packet={{"schema":"vuoro-private-reader-materials/v1","profile_id":request["profile_id"],"runtime_profile_sha256":request["runtime_profile_sha256"],"provider_registry_sha256":request["provider_registry_sha256"],"provider_profile_sha256":request["provider_profile_sha256"],"kind":request["kind"],"count":2}}
 raw=json.dumps(packet,sort_keys=True,separators=(",",":")).encode()
 channel.sendmsg([raw],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array("i",fds))])
 for fd in fds: os.close(fd)
 channel.close()
 if {mode!r}=="packet-stall":
  publish_pid()
  time.sleep(30)
"""


class FixtureState:
    """Explicit DATA state fixture; no installed admission or durable state claim."""

    def __init__(self):
        self.owner = None
        self.admitted = False
        self.events = []
        self.group = None

    async def begin(self, group):
        assert self.owner is None
        self.owner = (os.getpid(), threading.get_ident(), asyncio.current_task())
        self.events.append("begin")
        self.group = group
        self.admitted = True

    async def finalize(self, group):
        assert self.admitted and group is self.group and group.clean
        assert self.owner == (
            os.getpid(),
            threading.get_ident(),
            asyncio.current_task(),
        )
        for process in group.processes:
            assert process.returncode is not None and process._provider_pidfd is None
            assert process._transport.closed
        assert (group.root / group.name).is_dir()
        self.events.append("finalize")
        self.admitted = False

    def release(self):
        assert self.owner == (
            os.getpid(),
            threading.get_ident(),
            asyncio.current_task(),
        )
        self.events.append("release")
        self.owner = None

    def close(self):
        assert self.owner is None
        self.events.append("close")


def closed_state(provider, uncertain=False):
    state = provider.state
    assert state.owner is None
    assert state.events == (
        ["begin", "release"] if uncertain else ["begin", "finalize", "release"]
    )
    state.close()
    assert state.events[-1] == "close"


def composition(monkeypatch, lifecycle, group_root, tmp_path, mode):
    original = lifecycle._spawn
    state = FixtureState()
    pid_path = tmp_path / "descendant.pid"
    seen = []

    async def spawn(*args, **kwargs):
        assert state.admitted and state.events == ["begin"]
        assert state.owner == (
            os.getpid(),
            threading.get_ident(),
            asyncio.current_task(),
        )
        # Intercepts fixed module execution only for a controlled fixture;
        # actual spawn/pidfd/operation cgroup/cleanup are unchanged.
        assert args[1:4] == (
            "-I",
            "-m",
            "vuoro_reconciler.protected_runtime_reader_child",
        )
        process = await original(
            sys.executable,
            "-I",
            "-c",
            private_child_script(mode, pid_path),
            *args[4:],
            **kwargs,
        )
        seen.append(process)
        return process

    monkeypatch.setattr(lifecycle, "_spawn", spawn)
    provider = SimpleNamespace(
        data={"cgroup_root": str(group_root), "python_path": sys.executable},
        state=state,
        **provider_pins(),
    )
    return provider, pid_path, seen


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_actual_delegated_group_private_packet_success_and_cleanup(
    monkeypatch, lifecycle, group_root, tmp_path
):
    provider, _, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "success"
    )
    assert asyncio.run(
        module.acquire_materials(Runtime(), "native_read", provider, provider.state)
    ) == (b"fixture-ca", b"fixture-token")
    assert len(seen) == 1 and seen[0].returncode == 0
    assert not (group_root / seen[0]._provider_cgroup.name).exists()
    closed_state(provider)


def test_refused_concurrent_state_does_not_touch_other_task_owner(
    monkeypatch, lifecycle, group_root, tmp_path
):
    provider, _, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "success"
    )

    async def workflow():
        owner = (os.getpid(), threading.get_ident(), asyncio.current_task())
        provider.state.owner = owner
        provider.state.admitted = True
        provider.state.events = ["other-operation-active"]
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        with pytest.raises(AssertionError):
            await task
        assert seen == []
        assert provider.state.owner == owner and provider.state.admitted
        assert provider.state.events == ["other-operation-active"]
        provider.state.owner = None
        provider.state.close()

    asyncio.run(workflow())


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_actual_descendant_setsid_owned_and_reaped_before_return(
    monkeypatch, lifecycle, group_root, tmp_path, mode
):
    provider, pid_path, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "descendant"
    )
    monkeypatch.setattr(module, "_TOTAL", 2.0)
    monkeypatch.setattr(module, "_CLEANUP", 1.0)

    async def workflow():
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        if mode == "cancel":
            for _ in range(400):
                if pid_path.exists():
                    break
                await asyncio.sleep(0.005)
            assert pid_path.exists()
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if mode == "cancel" else TimeoutError
        ) as caught:
            await task
        assert not getattr(caught.value, "__notes__", ())
        assert len(seen) == 1 and seen[0].returncode is not None
        assert pid_path.exists() and not alive(int(pid_path.read_text()))

    asyncio.run(workflow())
    assert not (group_root / seen[0]._provider_cgroup.name).exists()
    assert lifecycle._GROUP.get() is lifecycle._DEADLINE.get() is None
    closed_state(provider)


def test_caller_cancellation_with_failed_containment_is_explicit(
    monkeypatch, lifecycle, group_root, tmp_path
):
    provider, pid_path, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "descendant"
    )
    original_group = lifecycle._OperationGroup
    held = []

    class FailingGroup(original_group):
        def __init__(self, profile):
            super().__init__(profile)
            held.append((self, os.dup(self.kill_fd), os.dup(self.events_fd)))

        def kill(self):
            raise ValueError("fixture-containment-failure")

    monkeypatch.setattr(lifecycle, "_OperationGroup", FailingGroup)

    async def workflow():
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        for _ in range(400):
            if pid_path.exists():
                break
            await asyncio.sleep(0.005)
        assert pid_path.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert getattr(caught.value, "__notes__", ()) == [
            "private-reader-containment-uncertain"
        ]
        assert seen[0].returncode is not None
        assert alive(int(pid_path.read_text()))
        assert (group_root / held[0][0].name).is_dir()

    try:
        asyncio.run(workflow())
        closed_state(provider, uncertain=True)
    finally:
        # Only this disposable operation is repaired by fixture teardown. Runtime
        # must preserve uncertainty and never pretend that the child is gone.
        for group, kill_fd, events_fd in held:
            try:
                assert os.write(kill_fd, b"1") == 1
                limit = time.monotonic() + 2
                while True:
                    os.lseek(events_fd, 0, os.SEEK_SET)
                    raw = os.read(events_fd, 8192)
                    if b"populated 0\n" in raw:
                        break
                    assert time.monotonic() < limit
                    time.sleep(0.005)
                os.rmdir(group_root / group.name)
            finally:
                os.close(kill_fd)
                os.close(events_fd)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_material_packet_closed_before_waiting_for_child_exit(
    monkeypatch, lifecycle, group_root, tmp_path, mode
):
    provider, pid_path, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "packet-stall"
    )
    monkeypatch.setattr(module, "_TOTAL", 2.0)
    monkeypatch.setattr(module, "_CLEANUP", 1.0)
    decode = module._decode_packet
    delivered = []

    def decoded(packet, *args):
        delivered.extend(rights(packet))
        return decode(packet, *args)

    monkeypatch.setattr(module, "_decode_packet", decoded)

    async def workflow():
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        for _ in range(400):
            if delivered and pid_path.exists():
                break
            await asyncio.sleep(0.005)
        assert len(delivered) == 2 and pid_path.exists()
        assert_closed(delivered)
        if mode == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if mode == "cancel" else TimeoutError
        ):
            await task
        assert seen[0].returncode is not None

    asyncio.run(workflow())
    assert not (group_root / seen[0]._provider_cgroup.name).exists()
    closed_state(provider)


@pytest.mark.parametrize("close_failure", [False, True])
def test_cancelled_after_proved_containment_with_state_clear_failure(
    monkeypatch, lifecycle, group_root, tmp_path, close_failure
):
    provider, pid_path, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "descendant"
    )

    async def finalize(group):
        assert group.clean
        assert all(process.returncode is not None for process in group.processes)
        provider.state.events.append("finalize-failed")
        raise ValueError("fixture-state-clear-failed")

    monkeypatch.setattr(provider.state, "finalize", finalize)
    if close_failure:
        original_group = lifecycle._OperationGroup

        class FailedCloseGroup(original_group):
            def close(self):
                super().close()
                raise ValueError("fixture-group-close-refused")

        monkeypatch.setattr(lifecycle, "_OperationGroup", FailedCloseGroup)

    async def workflow():
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        for _ in range(400):
            if pid_path.exists():
                break
            await asyncio.sleep(0.005)
        assert pid_path.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.__notes__ == [
            "private-reader-containment-uncertain"
            if close_failure
            else "private-reader-state-clear-uncertain"
        ]
        assert not alive(int(pid_path.read_text()))
        assert provider.state.owner is None and provider.state.admitted
        assert provider.state.events == ["begin", "finalize-failed", "release"]
        provider.state.close()

    asyncio.run(workflow())
    assert not (group_root / seen[0]._provider_cgroup.name).exists()


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_real_owned_pipe_close_refusal_still_reaps_and_withholds_state_clear(
    monkeypatch, lifecycle, group_root, tmp_path, mode
):
    provider, pid_path, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "packet-stall"
    )
    monkeypatch.setattr(module, "_TOTAL", 2.0)
    monkeypatch.setattr(module, "_CLEANUP", 1.0)
    originals = []
    finalized = []
    real_finalize = provider.state.finalize

    async def finalize(group):
        finalized.append(True)
        await real_finalize(group)

    monkeypatch.setattr(provider.state, "finalize", finalize)

    class BadClose:
        def __init__(self, pipe):
            self.pipe = pipe

        def close(self):
            raise OSError("fixture-real-owned-pipe-close-refused")

        def __getattr__(self, name):
            return getattr(self.pipe, name)

    async def workflow():
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        for _ in range(400):
            if pid_path.exists() and seen:
                break
            await asyncio.sleep(0.005)
        assert pid_path.exists() and len(seen) == 1
        process = seen[0]
        originals.append((process, process._process.stdin))
        # Real Popen pipe ownership is used. Its close is faulted after request
        # delivery; no source, credential, or production authority is mocked.
        process._process.stdin = BadClose(process._process.stdin)
        if mode == "cancel":
            task.cancel("original-caller-cancellation")
        with pytest.raises(
            asyncio.CancelledError if mode == "cancel" else OSError
        ) as caught:
            await task
        if mode == "cancel":
            assert caught.value.args == ("original-caller-cancellation",)
            assert "private-reader-containment-uncertain" in caught.value.__notes__
        assert process._process.returncode is not None
        assert not Path(f"/proc/{process.pid}").exists()
        assert not process._transport.closed
        assert finalized == []
        assert provider.state.admitted and provider.state.owner is None
        assert provider.state.events == ["begin", "release"]
        provider.state.close()

    try:
        asyncio.run(workflow())
    finally:
        for process, stdin in originals:
            process._process.stdin = stdin
            stdin.close()
            process._process.wait(timeout=2)
            process._transport.close()


def test_repeated_cancel_drains_owned_reaper_before_return(
    monkeypatch, lifecycle, group_root, tmp_path
):
    provider, pid_path, seen = composition(
        monkeypatch, lifecycle, group_root, tmp_path, "packet-stall"
    )

    async def workflow():
        task = asyncio.create_task(
            module.acquire_materials(Runtime(), "native_read", provider, provider.state)
        )
        for _ in range(400):
            if pid_path.exists() and seen:
                break
            await asyncio.sleep(0.005)
        assert pid_path.exists()
        process = seen[0]
        original_wait = process.wait
        entered = asyncio.Event()
        release = asyncio.Event()

        async def controlled_wait():
            # Explicit timing seam around the real Popen wait; the child itself,
            # stable pidfd, kernel group and underlying reap remain actual.
            entered.set()
            await release.wait()
            return await original_wait()

        monkeypatch.setattr(process, "wait", controlled_wait)
        task.cancel("first-cancel")
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel("second-cancel")
        await asyncio.sleep(0.02)
        try:
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("first-cancel",)
        assert process._process.returncode is not None
        assert not Path(f"/proc/{process.pid}").exists()

    try:
        asyncio.run(workflow())
    finally:
        for process in seen:
            process._process.wait(timeout=2)
            process._transport.close()


@pytest.mark.parametrize("kind", ["broker_read", "native_read"])
@pytest.mark.parametrize(
    "drift", [None, "registry_sha256", "profile_sha256", "inactive"]
)
def test_actual_child_framing_seals_and_join_before_private_reads(
    monkeypatch, tmp_path, kind, drift
):
    import io
    from vuoro_reconciler import protected_runtime_reader_child as child_module

    material = {}
    expected = []
    for index, (field, _, private) in enumerate(module._FIELDS[kind]):
        path = tmp_path / field
        raw = ("disposable-material-" + str(index)).encode()
        if field == "session_path":
            raw = _canonical(
                {
                    "session_id": "disposable",
                    "token": "iac_" + "1" * 32,
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "subject_id": "disposable",
                    "host_id": "disposable",
                }
            )
        path.write_bytes(raw)
        path.chmod(0o600 if private else 0o644)
        material[field] = ("file:" if field == "credential_ref" else "") + str(path)
        expected.append(raw)
    runtime = SimpleNamespace(
        profile_id="disposable",
        profile_sha256=Runtime().profile_sha256,
        data={kind: material},
    )
    provider = SimpleNamespace(
        data={"fixture": "no installed authority"}, **provider_pins()
    )
    if drift not in (None, "inactive"):
        setattr(provider, drift, "sha256:" + "d" * 64)
    joined = []
    group_fd = _sealed(b"fixture group locator only")

    def join(fd, data):
        assert fd == group_fd and data == provider.data
        joined.append(True)
        os.close(fd)

    def active(selected):
        assert selected is provider and joined
        if drift == "inactive":
            raise ValueError("fixture-inactive")

    fake = SimpleNamespace(
        load_installed_profile=lambda selected: provider,
        _join_cgroup=join,
        _validate_active=active,
    )
    monkeypatch.setitem(
        sys.modules, "vuoro_reconciler.protected_provider_registry", fake
    )
    monkeypatch.setattr(child_module, "load_runtime_profile", lambda selected: runtime)
    original = child_module._owned_file
    reads = []

    def read(*args, **kwargs):
        assert joined
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(child_module, "_owned_file", read)
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    child_fd = child.detach()
    monkeypatch.setattr(sys, "argv", ["private-reader", str(group_fd), str(child_fd)])
    request = _canonical(
        {
            "schema": "vuoro-private-reader-request/v1",
            "profile_id": runtime.profile_id,
            "kind": kind,
            "runtime_profile_sha256": runtime.profile_sha256,
            "provider_registry_sha256": provider_pins()["registry_sha256"],
            "provider_profile_sha256": provider_pins()["profile_sha256"],
        }
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(request)))
    completed = False
    try:
        if drift is not None:
            with pytest.raises(ValueError):
                child_module.main()
            assert joined == ([True] if drift == "inactive" else [])
            assert reads == []
            return
        child_module.main()
        completed = True
        received = parent.recvmsg(
            2049,
            socket.CMSG_SPACE(4 * array.array("i").itemsize),
            socket.MSG_CMSG_CLOEXEC,
        )
        delivered = rights(received)
        assert module._decode_packet(
            received, runtime, kind, SimpleNamespace(**provider_pins())
        ) == tuple(expected)
        assert_closed(delivered)
        assert joined == [True]
    finally:
        parent.close()
        if not joined:
            module._close([group_fd])
        if not completed:
            module._close([child_fd])


@pytest.mark.parametrize('close_failure',[False,True])
def test_first_finalize_cancel_preserves_uncertainty_note(monkeypatch,lifecycle,group_root,tmp_path,close_failure):
    provider, _, seen = composition(monkeypatch,lifecycle,group_root,tmp_path,'success')
    async def run():
        entered=asyncio.Event()
        async def finalize(group):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(provider.state,'finalize',finalize)
        if close_failure:
            original=lifecycle._OperationGroup.close
            def close(group):
                original(group)
                raise OSError('disposable later group close fault')
            monkeypatch.setattr(lifecycle._OperationGroup,'close',close)
        task=asyncio.create_task(module.acquire_materials(Runtime(),'native_read',provider,provider.state))
        await asyncio.wait_for(entered.wait(),10)
        task.cancel('first-finalize-cancel')
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ('first-finalize-cancel',)
        assert getattr(caught.value,'__notes__',[]) == [
            'private-reader-containment-uncertain' if close_failure
            else 'private-reader-state-clear-uncertain'
        ]
        assert provider.state.owner is None and provider.state.admitted
        assert all(process.returncode is not None and process._transport.closed for process in seen)
    asyncio.run(run())


def test_received_memfd_close_error_refuses_private_materials(monkeypatch):
    runtime=Runtime()
    sent=[_sealed(raw) for raw in (b'fixture-ca',b'fixture-private-token')]
    received=packet(receipt(runtime),sent)
    delivered=rights(received)
    original=os.close
    attempted=[]
    def close(descriptor):
        attempted.append(descriptor)
        if descriptor==delivered[0]:
            raise OSError('disposable received descriptor close fault')
        return original(descriptor)
    monkeypatch.setattr(os,'close',close)
    try:
        with pytest.raises(module._DescriptorCloseUncertain, match=module._DESCRIPTOR_NOTE):
            module._decode_packet(received,runtime,'native_read',SimpleNamespace(**provider_pins()))
        assert attempted == delivered
        assert os.fstat(delivered[0])
        with pytest.raises(OSError):
            os.fstat(delivered[1])
    finally:
        for descriptor in (*sent,delivered[0]):
            original(descriptor)


def test_channel_close_error_preserves_cancel_and_attempts_owner_cleanup(monkeypatch,lifecycle,group_root,tmp_path):
    provider,pid_path,seen=composition(monkeypatch,lifecycle,group_root,tmp_path,'descendant')
    original=module.socket.socketpair
    held=[]
    class FailedClose:
        def __init__(self,channel):
            self.channel=channel
        def __getattr__(self,name):
            return getattr(self.channel,name)
        def close(self):
            raise OSError('disposable parent channel close failure')
    def pair(*args):
        parent,child=original(*args)
        if len(args) > 1 and args[1] == module.socket.SOCK_SEQPACKET:
            held.append(parent)
            return FailedClose(parent),child
        return parent,child
    monkeypatch.setattr(module.socket,'socketpair',pair)
    async def run():
        task=asyncio.create_task(module.acquire_materials(Runtime(),'native_read',provider,provider.state))
        for _ in range(400):
            if pid_path.exists():
                break
            await asyncio.sleep(.005)
        assert pid_path.exists()
        task.cancel('original-channel-close-cancel')
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ('original-channel-close-cancel',)
        assert caught.value.__notes__ == ['private-reader-containment-uncertain']
        assert task.cancelled()
        assert provider.state.owner is None and provider.state.admitted
        for descriptor in provider.state.group.descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        assert all(process.returncode is not None and process._transport.closed for process in seen)
    try:
        asyncio.run(run())
    finally:
        for channel in held:
            channel.close()
