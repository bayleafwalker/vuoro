"""Closed scoped child protocol and parent lifetime falsifiers."""

import asyncio
from dataclasses import replace
import gc
import json
import os
from pathlib import Path
import sys
import time
import pytest
from vuoro_reconciler import protected_provider as module
from vuoro_reconciler.execution_journal import JournalEvent
from vuoro_reconciler.protected_provider_registry import (
    ProviderRefused,
    load_installed_profile,
)
from test_protected_copy_codec import canonical, sha
from test_protected_git import case
import test_protected_git as git_fixtures

from test_protected_provider_registry import data, install_test_registry
import test_protected_read_transport as tls_fixtures


_REAL_STATE_RPC = module._InstalledOperationState._rpc
_REAL_OWNED_PROCESS = module._create_owned_process

keys = git_fixtures.keys
certificates = tls_fixtures.certificates


def make_case(tmp_path, keys, certificates, monkeypatch, origin=None):
    from vuoro_reconciler import protected_provider_child as child

    # In-process wire tests use disposable credential DATA; separate actual
    # launcher tests qualify the kernel containment boundary.
    monkeypatch.setattr(child, "_admit_current_cgroup", lambda profile: None)
    monkeypatch.setattr(child, "_validate_active", lambda profile: None)

    async def state_data_only(self, group, action):
        return None

    # Protocol/transport fixtures isolate the state layer; dedicated actual
    # state-helper tests qualify the durable lock and active lifecycle.
    monkeypatch.setattr(module._InstalledOperationState, "_rpc", state_data_only)
    value = case(tmp_path, keys)
    registered = data(tmp_path, certificates, keys, artifact_root=value.directory)
    if origin is not None:
        registered["https_origin"] = origin
    path = install_test_registry(tmp_path, monkeypatch, registered)
    profile = load_installed_profile("fixture")
    binding = json.loads(value.record.binding_utf8)
    binding["registry_sha256"] = profile.registry_sha256
    binding["provider_profile_sha256"] = profile.profile_sha256
    raw = canonical(binding)
    # These are explicit test DATA. The old journal is not rewritten or treated
    # as a grant; future executor alone reads the installed authority chain.
    value.record = replace(
        value.record, binding_utf8=raw, events=(JournalEvent(0, "BOUND", raw),)
    )
    artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
    value.record = replace(
        value.record,
        events=(
            *value.record.events,
            JournalEvent(1, "LOCAL_READY", artifact.local_ready_utf8),
        ),
    )
    value.provider_profile = profile
    value.artifact = artifact
    value.registry_path = path
    return value


@pytest.fixture
def provider_case(tmp_path, keys, certificates, monkeypatch):
    value = make_case(tmp_path, keys, certificates, monkeypatch)
    yield value
    value.journal.close()


def request_input(value, operation):
    profile = value.provider_profile
    binding = json.loads(value.record.binding_utf8)
    ready = value.record.events[1].payload_utf8
    request = module._request(
        value.record, value.effect, binding, json.loads(ready), operation
    )
    return canonical(
        {
            "schema": "vuoro-protected-provider-child-request/v1",
            "operation": operation,
            "profile_id": profile.profile_id,
            "registry_sha256": profile.registry_sha256,
            "profile_sha256": profile.profile_sha256,
            "request_utf8_b64": module._b64(request),
            "local_ready_utf8_b64": module._b64(ready),
            "protected_copy_utf8_b64": module._b64(value.record.protected_copy_utf8),
            "binding_utf8_b64": module._b64(value.record.binding_utf8),
        }
    )


@pytest.mark.parametrize(
    "operation",
    ["branch-lookup", "branch-create", "pr-lookup", "pr-create", "capability-check"],
)
def test_closed_request_exacttuple_and_operation_specific_result(
    provider_case, operation
):
    value = provider_case
    raw = request_input(value, operation)
    incoming, record, effect, request, local = module._validate_input(
        raw, value.provider_profile
    )
    status = (
        "ABSENT"
        if operation.endswith("-lookup")
        else "CAPABILITY_PRESENT"
        if operation == "capability-check"
        else "TRANSMISSION_POSSIBLE"
    )
    result = canonical(
        {
            "schema": "vuoro-protected-provider-child-result/v1",
            "operation": operation,
            "effect_key": record.effect_key,
            "binding_sha256": sha(record.binding_utf8),
            "request_sha256": sha(canonical(request)),
            "status": status,
            "observation_utf8_b64": None,
        }
    )
    decoded = module._result(result, incoming, record, request)
    assert decoded.status == status and decoded.observation_utf8 is None
    assert str(value.directory) not in repr(decoded)
    changed = json.loads(result)
    changed["status"] = (
        "OBSERVED" if operation == "pr-create" else "TRANSMISSION_POSSIBLE"
    )
    if changed["status"] == status:
        changed["status"] = "CAPABILITY_PRESENT"
    with pytest.raises(ValueError):
        module._result(canonical(changed), incoming, record, request)


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "url",
        "profile",
        "registry",
        "noncanonical",
        "operation",
        "revision-bool",
        "local-oid",
        "request-oid",
        "request-repository",
        "request-base",
        "request-title",
        "request-body",
        "copy-scope",
        "oversize",
    ],
)
def test_input_refuses_unjoined_or_selector_data(provider_case, kind):
    value = provider_case
    raw = request_input(value, "pr-create")
    incoming = json.loads(raw)
    if kind in ("unknown", "url"):
        incoming[kind] = "https://attacker.invalid"
    elif kind == "profile":
        incoming["profile_sha256"] = "sha256:" + "0" * 64
    elif kind == "registry":
        incoming["registry_sha256"] = "sha256:" + "0" * 64
    elif kind == "operation":
        incoming["operation"] = "merge"
    elif kind == "revision-bool":
        binding = json.loads(value.record.binding_utf8)
        binding["intent_revision"] = True
        incoming["binding_utf8_b64"] = module._b64(canonical(binding))
    elif kind == "local-oid":
        local = json.loads(value.artifact.local_ready_utf8)
        local["commit_oid"] = "0" * 40
        incoming["local_ready_utf8_b64"] = module._b64(canonical(local))
    elif kind.startswith("request-"):
        request = json.loads(
            __import__("base64").b64decode(incoming["request_utf8_b64"])
        )
        field = {
            "request-oid": "commit_oid",
            "request-repository": "repository",
            "request-base": "base_branch",
            "request-title": "title",
            "request-body": "body_utf8_b64",
        }[kind]
        request[field] = (
            "0" * 40
            if field == "commit_oid"
            else "eA=="
            if field == "body_utf8_b64"
            else "other"
        )
        incoming["request_utf8_b64"] = module._b64(canonical(request))
    elif kind == "copy-scope":
        copied = json.loads(value.record.protected_copy_utf8)
        copied["locator"]["namespace"]["repository"] = "other"
        incoming["protected_copy_utf8_b64"] = module._b64(canonical(copied))
    elif kind == "oversize":
        incoming["protected_copy_utf8_b64"] = "A" * (12 * 1024 * 1024)
    raw = canonical(incoming) + (b"\n" if kind == "noncanonical" else b"")
    with pytest.raises(ValueError):
        module._validate_input(raw, value.provider_profile)


def test_fixed_credctl_scoped_args_scrubbed_env_one_call_and_stderr_drop(
    provider_case, monkeypatch, tmp_path
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    intercepted = []
    real = module._create_owned_process
    shim = tmp_path / "fake-broker.py"
    shim.write_text(
        "import sys,json,base64,hashlib\nv=json.load(sys.stdin)\nr=base64.b64decode(v['request_utf8_b64'])\nq=json.loads(r)\nh=lambda b:'sha256:'+hashlib.sha256(b).hexdigest()\nsys.stderr.write('private-disposable-error-and-token')\njson.dump(dict(schema='vuoro-protected-provider-child-result/v1',operation=v['operation'],effect_key=q['effect_key'],binding_sha256=q['binding_sha256'],request_sha256=h(r),status='CAPABILITY_PRESENT',observation_utf8_b64=None),sys.stdout,separators=(',',':'),sort_keys=True)\n"
    )

    async def spawn(*args, **kwargs):
        intercepted.append((args, kwargs["env"]))
        if module._IDENTITY_PROBE in args:
            # Fixture emulates the installed isolated module inventory; no
            # credential acquisition happens in this subprocess.
            code = (
                "import sys;sys.stdout.buffer.write("
                + repr(canonical(value.provider_profile.data["module_sha256"]))
                + ")"
            )
            code = (
                "import os;os.write("
                + args[-1]
                + ",b'0');os.close("
                + args[-1]
                + ");"
                + code
            )
            return await real(sys.executable, "-I", "-c", code, **kwargs)
        join = (
            "import os,runpy;os.write("
            + args[-1]
            + ",b'0');os.close("
            + args[-1]
            + ");runpy.run_path("
            + repr(str(shim))
            + ",run_name='__main__')"
        )
        return await real(sys.executable, "-c", join, **kwargs)

    monkeypatch.setattr(module, "_create_owned_process", spawn)
    monkeypatch.setenv("FJ_TOKEN", "ambient-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://attacker.invalid")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    provider = module.ProtectedProvider("fixture")
    result = asyncio.run(provider.fresh_capability(value.record))
    assert result.status == "CAPABILITY_PRESENT" and len(intercepted) == 2
    args, env = intercepted[1]
    assert module._IDENTITY_PROBE in intercepted[0][0]
    assert args[:5] == (
        value.provider_profile.data["python_path"],
        "-I",
        "-m",
        "vuoro_reconciler.protected_provider_child",
        "--launch",
    )
    assert args[-1].isdecimal()
    assert (
        "FJ_TOKEN" not in env
        and "HTTPS_PROXY" not in env
        and "GIT_CONFIG_COUNT" not in env
    )
    assert env["HOME"] != os.environ.get("HOME", "impossible-ambient") or env[
        "HOME"
    ] == str(value.registry_path.parents[3])
    gc.collect()


@pytest.mark.parametrize(
    "kind", ["timeout", "cancel", "overflow", "invalid-output", "new-session"]
)
def test_parent_kills_child_group_and_fixed_failures(
    provider_case, monkeypatch, tmp_path, kind
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    real = module._create_owned_process
    pidfile = tmp_path / "child.pid"
    shim = tmp_path / "bad-broker.py"
    shim.write_text(
        "import os,sys,time\npid=os.fork()\nif pid==0:\n"
        + (" os.setsid()\n" if kind == "new-session" else "")
        + f" time.sleep(30)\nelse:\n open({str(pidfile)!r},'w').write(str(pid))\n"
        + (
            " os.write(1,b'x'*(2000000))\n"
            if kind == "overflow"
            else " os.write(1,b'private-not-json')\n"
            if kind == "invalid-output"
            else ""
        )
        + (" time.sleep(30)\n" if kind != "invalid-output" else "")
    )

    async def spawn(*args, **kwargs):
        if module._IDENTITY_PROBE in args:
            code = (
                "import sys;sys.stdout.buffer.write("
                + repr(canonical(value.provider_profile.data["module_sha256"]))
                + ")"
            )
            code = (
                "import os;os.write("
                + args[-1]
                + ",b'0');os.close("
                + args[-1]
                + ");"
                + code
            )
            return await real(sys.executable, "-I", "-c", code, **kwargs)
        join = (
            "import os,runpy;os.write("
            + args[-1]
            + ",b'0');os.close("
            + args[-1]
            + ");runpy.run_path("
            + repr(str(shim))
            + ",run_name='__main__')"
        )
        return await real(sys.executable, "-c", join, **kwargs)

    monkeypatch.setattr(module, "_create_owned_process", spawn)
    monkeypatch.setattr(module, "_TOTAL", 0.6)
    monkeypatch.setattr(module, "_CLEANUP_RESERVE", 0.3)
    provider = module.ProtectedProvider("fixture")

    async def workflow():
        task = asyncio.create_task(provider.fresh_capability(value.record))
        if kind == "cancel":
            for _ in range(100):
                if pidfile.exists():
                    break
                await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ProviderRefused) as error:
                await task
            assert error.value.__context__ is None and error.value.__cause__ is None
        for _ in range(100):
            try:
                state = (
                    Path("/proc/" + pidfile.read_text() + "/stat")
                    .read_text()
                    .split()[2]
                )
            except FileNotFoundError:
                break
            if state == "Z":
                break
            await asyncio.sleep(0.005)
        else:
            pytest.fail("private child still alive")

    asyncio.run(workflow())
    gc.collect()


def test_current_registry_drift_refuses_before_minting(provider_case, monkeypatch):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    from test_protected_provider_child import child_driver

    value = provider_case
    provider = module.ProtectedProvider("fixture")
    value.registry_path.write_bytes(value.registry_path.read_bytes() + b"\n")
    real = module._create_owned_process
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        if "--launch" in args:
            return await real(
                sys.executable,
                "-I",
                "-c",
                child_driver(value),
                "--launch",
                args[-1],
                **kwargs,
            )
        assert module._IDENTITY_PROBE in args
        source = str(Path(module.__file__).parents[1])
        probe = "import sys;sys.path.insert(0," + repr(source) + ");" + args[3]
        return await real(*args[:3], probe, *args[4:], **kwargs)

    monkeypatch.setattr(module, "_create_owned_process", spawn)
    with pytest.raises(ProviderRefused):
        asyncio.run(provider.fresh_capability(value.record))
    assert len(calls) == 2  # Fresh child refuses before any broker exec.


def test_cancellation_during_actual_spawn_still_captures_kills_and_reaps(
    tmp_path, monkeypatch
):
    real = module._create_owned_process
    pidfile = tmp_path / "pid"

    async def delayed(*args, **kwargs):
        process = await real(*args, **kwargs)
        pidfile.write_text(str(process.pid))
        await asyncio.sleep(0.15)
        return process

    monkeypatch.setattr(module, "_create_owned_process", delayed)

    async def workflow():
        task = asyncio.create_task(
            module._spawn(
                sys.executable,
                "-c",
                "import time;time.sleep(30)",
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        for _ in range(100):
            if pidfile.exists():
                break
            await asyncio.sleep(0.005)
        assert pidfile.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not Path("/proc/" + pidfile.read_text()).exists()

    asyncio.run(workflow())
    gc.collect()


def test_installed_isolated_child_identity_drift_refuses_before_mint(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    real = module._create_owned_process
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        assert module._IDENTITY_PROBE in args, "credential mint after mismatched child"
        return await real(sys.executable, "-I", "-c", "print('{}',end='')", **kwargs)

    monkeypatch.setattr(module, "_create_owned_process", spawn)
    provider = module.ProtectedProvider("fixture")
    with pytest.raises(ProviderRefused):
        asyncio.run(provider.fresh_capability(value.record))
    assert len(calls) == 1 and calls[0][0] == value.provider_profile.data["python_path"]


def test_actual_cgroup_kill_contains_thread_forks_new_sessions_and_reparenting(
    provider_case, monkeypatch, tmp_path
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    descendants = tmp_path / "descendants"
    real = module._create_owned_process

    async def delayed(*args, **kwargs):
        process = await real(*args, **kwargs)
        await asyncio.sleep(0.15)  # Root exits before Process capture.
        return process

    monkeypatch.setattr(module, "_create_owned_process", delayed)
    group = module._OperationGroup(value.provider_profile)
    code = (
        "import os,time,threading\nos.write("
        + str(group.procs_fd)
        + ",b'0');os.close("
        + str(group.procs_fd)
        + ")\n"
        "first=os.fork()\nif first==0:\n os.setsid()\n"
        " def forks():\n  for _ in range(200):\n   pid=os.fork()\n   if pid==0:\n    os.setsid();time.sleep(30);os._exit(0)\n"
        f"   with open({str(descendants)!r},'a') as out:out.write(str(pid)+'\\n')\n   time.sleep(.01)\n"
        " thread=threading.Thread(target=forks);thread.start();thread.join();time.sleep(30)\nelse:\n os._exit(0)\n"
    )

    async def workflow():
        token = module._GROUP.set(group)
        try:
            process = await module._spawn(
                sys.executable,
                "-c",
                code,
                pass_fds=(group.procs_fd,),
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.returncode == 0  # Reparented new sessions remain alive.
            assert descendants.exists()
            module._kill_tree(process)
            process._transport.close()
            await process.wait()
            await asyncio.sleep(0.02)
            assert group.clean
            for pid in descendants.read_text().splitlines():
                try:
                    state = Path("/proc/" + pid + "/stat").read_text().split()[2]
                except FileNotFoundError:
                    continue
                assert state == "Z", "cgroup-owned orphan survived cleanup"
        finally:
            module._GROUP.reset(token)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        if not group.clean:
            group.kill()
        group.close()


def test_no_pidfd_or_group_ownership_never_signals_unrelated_process():
    from types import SimpleNamespace

    async def workflow():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time;time.sleep(30)", start_new_session=True
        )
        try:
            module._kill_tree(
                SimpleNamespace(
                    pid=process.pid, _provider_pidfd=None, _provider_cgroup=None
                )
            )
            assert process.returncode is None
        finally:
            process.kill()
            await process.wait()

    asyncio.run(workflow())
    gc.collect()


def test_actual_cgroup_cleanup_failure_is_reported_and_preserves_quarantine(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    group = module._OperationGroup(provider_case.provider_profile)
    real = os.write

    def fail(fd, data):
        if fd == group.kill_fd:
            raise OSError("disposable synthetic cgroup failure")
        return real(fd, data)

    monkeypatch.setattr(module.os, "write", fail)
    with pytest.raises(OSError):
        group.kill()
    assert not group.clean and (group.root / group.name).exists()
    monkeypatch.setattr(module.os, "write", real)
    group.kill()
    group.close()


def test_actual_pidfd_failure_kills_owned_child_before_refusal(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    group = module._OperationGroup(provider_case.provider_profile)
    real_popen = module.subprocess.Popen
    captured = []

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        captured.append(process)
        return process

    def fail_pidfd(pid):
        raise OSError("disposable pidfd exhaustion")

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module.os, "pidfd_open", fail_pidfd)

    async def workflow():
        token = module._GROUP.set(group)
        try:
            with pytest.raises(OSError):
                await module._spawn(
                    sys.executable,
                    "-c",
                    "import time;time.sleep(30)",
                    start_new_session=True,
                    stdin=module.subprocess.PIPE,
                    stdout=module.subprocess.PIPE,
                    stderr=module.subprocess.PIPE,
                )
            assert group.clean and len(captured) == 1
            assert captured[0].returncode is not None
            assert not Path("/proc/" + str(captured[0].pid)).exists()
            assert all(
                pipe.closed
                for pipe in (captured[0].stdin, captured[0].stdout, captured[0].stderr)
            )
        finally:
            module._GROUP.reset(token)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        if not group.clean:
            group.kill()
        group.close()


@pytest.mark.parametrize("phase", ["read", "write"])
def test_actual_pipe_adaptation_failure_reaps_and_closes_owned_child(
    provider_case, monkeypatch, phase
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    group = module._OperationGroup(provider_case.provider_profile)
    real_popen = module.subprocess.Popen
    captured = []

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        captured.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", popen)

    async def workflow():
        loop = asyncio.get_running_loop()

        async def failed(*args, **kwargs):
            raise OSError("disposable pipe adaptation failure")

        monkeypatch.setattr(loop, "connect_" + phase + "_pipe", failed)
        token = module._GROUP.set(group)
        try:
            with pytest.raises(OSError):
                await module._spawn(
                    sys.executable,
                    "-c",
                    "import time;time.sleep(30)",
                    start_new_session=True,
                    stdin=module.subprocess.PIPE,
                    stdout=module.subprocess.PIPE,
                    stderr=module.subprocess.PIPE,
                )
            assert group.clean and len(captured) == 1
            assert captured[0].returncode is not None
            assert not Path("/proc/" + str(captured[0].pid)).exists()
            assert all(
                pipe.closed
                for pipe in (captured[0].stdin, captured[0].stdout, captured[0].stderr)
            )
            gc.collect()
            await asyncio.sleep(0)
        finally:
            module._GROUP.reset(token)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        if not group.clean:
            group.kill()
        group.close()


def actual_state_wire(value, monkeypatch, *, injected=""):
    from test_protected_provider_child import child_driver

    real = _REAL_OWNED_PROCESS
    calls = []

    async def spawn(*args, **kwargs):
        if "--state" not in args:
            return await real(*args, **kwargs)
        calls.append(args)
        driver = child_driver(value)
        if injected:
            driver = driver.replace(
                "sys.exit(child.main())", injected + ";sys.exit(child.main())"
            )
        return await real(
            sys.executable, "-I", "-c", driver, "--state", *args[-4:], **kwargs
        )

    monkeypatch.setattr(module._InstalledOperationState, "_rpc", _REAL_STATE_RPC)
    monkeypatch.setattr(module, "_create_owned_process", spawn)
    return calls


def test_actual_durable_active_begin_clear_and_exclusive_holder(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    calls = actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import (
        _state_paths,
        _active_record,
    )

    directory, lock, name = _state_paths("fixture")

    async def workflow():
        try:
            await state.begin(group)
            assert state.admitted and state.locked
            assert (directory / name).read_bytes() == _active_record(
                value.provider_profile, group.name.removeprefix("operation-")
            )
            other = os.open(lock, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    module.fcntl.flock(
                        other, module.fcntl.LOCK_EX | module.fcntl.LOCK_NB
                    )
            finally:
                os.close(other)
            with pytest.raises(ValueError):
                await state.begin(group)  # Same-thread async reuse is refused.
            group.kill()
            await state.finalize(group)
            assert not state.admitted and not (directory / name).exists()
        finally:
            state.release()

    try:
        asyncio.run(workflow())
        assert len(calls) == 2
        gc.collect()
    finally:
        if not group.clean:
            group.kill()
        group.close()
        state.close()


@pytest.mark.parametrize("existing", ["active", "partial", "symlink", "quarantine"])
def test_actual_existing_state_blocks_new_admission(
    provider_case, monkeypatch, existing
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import (
        _state_paths,
        _active_record,
    )

    directory, _, name = _state_paths("fixture")
    path = directory / ("fixture.quarantine" if existing == "quarantine" else name)
    if existing == "symlink":
        path.symlink_to(directory / "missing")
    else:
        path.write_bytes(
            _active_record(value.provider_profile, "f" * 32)
            if existing == "active"
            else b"partial"
        )
        path.chmod(0o600)

    async def workflow():
        try:
            with pytest.raises(ValueError):
                await state.begin(group)
            assert not state.admitted
            assert path.is_symlink() or path.exists()
        finally:
            state.release()

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.kill()
        group.close()
        state.close()


def test_actual_begin_fsync_failure_keeps_active_and_fresh_holder_refuses(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    real = module._create_owned_process
    actual_state_wire(
        value,
        monkeypatch,
        injected="os.fsync=lambda fd:(_ for _ in ()).throw(OSError('fixture fsync'))",
    )
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, _, name = _state_paths("fixture")

    async def failed_begin():
        try:
            with pytest.raises(ValueError):
                await state.begin(group)
            assert not state.admitted and (directory / name).exists()
        finally:
            state.release()

    try:
        asyncio.run(failed_begin())
    finally:
        group.kill()
        group.close()
        state.close()
    # Fresh process helpers and a fresh parent holder cannot adopt a no-ack file.
    monkeypatch.setattr(module, "_create_owned_process", real)
    actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)

    async def restart():
        try:
            with pytest.raises(ValueError):
                await state.begin(group)
        finally:
            state.release()

    try:
        asyncio.run(restart())
        gc.collect()
    finally:
        group.kill()
        group.close()
        state.close()


@pytest.mark.parametrize("drift", ["lock-replace", "lock-mode", "directory-replace"])
def test_state_holder_precheck_to_open_race_refuses(provider_case, monkeypatch, drift):
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, lock, _ = _state_paths("fixture")
    original = module._check_state_install

    def replace(profile_id):
        result = original(profile_id)
        if drift == "lock-replace":
            lock.rename(directory / "old-lock")
            lock.touch(mode=0o600)
        elif drift == "lock-mode":
            lock.chmod(0o666)
        else:
            directory.rename(directory.with_name("old-operation-state"))
            directory.mkdir(mode=0o700)
            lock.touch(mode=0o600)
        return result

    monkeypatch.setattr(module, "_check_state_install", replace)
    candidate = None
    try:
        with pytest.raises(ValueError):
            candidate = module._InstalledOperationState(provider_case.provider_profile)
    finally:
        if candidate is not None:
            candidate.close()


def test_actual_state_begin_cancel_after_fsync_before_ack_preserves_active(
    provider_case, monkeypatch, tmp_path
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    marker = tmp_path / "after-state-fsync"
    injection = (
        "original_state=child._state_helper;"
        "child._state_helper=lambda *fds:(original_state(*fds),"
        f"__import__('pathlib').Path({str(marker)!r}).write_text('done'),__import__('time').sleep(30))"
    )
    actual_state_wire(value, monkeypatch, injected=injection)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, _, name = _state_paths("fixture")

    async def begin():
        try:
            await state.begin(group)
        finally:
            state.release()

    async def workflow():
        task = asyncio.create_task(begin())
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(0.005)
        assert marker.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not state.admitted and (directory / name).exists()
        assert state.owner is None
        gc.collect()
        await asyncio.sleep(0)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.kill()
        group.close()
        state.close()


def test_actual_finalize_kernel_nonempty_refuses_despite_heap_claim(
    provider_case, monkeypatch, tmp_path
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    marker = tmp_path / "joined-child"
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, _, name = _state_paths("fixture")

    async def workflow():
        process = None
        try:
            await state.begin(group)
            process = module.subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    f"import os,time;os.write({group.procs_fd},b'0');"
                    f"open({str(marker)!r},'w').write('joined');time.sleep(30)",
                ],
                pass_fds=(group.procs_fd,),
                start_new_session=True,
            )
            for _ in range(200):
                if marker.exists():
                    break
                await asyncio.sleep(0.005)
            assert marker.exists()
            group.clean = True  # Corrupt heap DATA cannot replace kernel proof.
            with pytest.raises(ProviderRefused, match="state-clear-uncertain"):
                await state.finalize(group)
            assert (directory / name).exists() and process.poll() is None
        finally:
            group.kill()
            if process is not None:
                process.wait(timeout=5)
            await state.finalize(group)
            state.release()

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.close()
        state.close()


def test_actual_unlink_then_directory_fsync_failure_is_clear_uncertainty(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, _, name = _state_paths("fixture")

    async def workflow():
        try:
            await state.begin(group)
            group.kill()
            actual_state_wire(
                value,
                monkeypatch,
                injected="os.fsync=lambda fd:(_ for _ in ()).throw(OSError('fixture fsync'))",
            )
            with pytest.raises(ProviderRefused, match="state-clear-uncertain"):
                await state.finalize(group)
            assert state.admitted and not (directory / name).exists()
            assert group.clean  # No credential processes can revive after clear.
        finally:
            state.release()

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.close()
        state.close()


@pytest.mark.parametrize("alias", ["identical", "non-target-pin"])
def test_actual_registered_alias_cannot_bypass_active_uncertainty(
    provider_case, monkeypatch, alias
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    registry = json.loads(value.registry_path.read_bytes())
    alternate = dict(registry["profiles"]["fixture"])
    if alias == "non-target-pin":
        alternate["runtime_profile_sha256"] = "sha256:" + "8" * 64
    registry["profiles"]["alias"] = alternate
    value.registry_path.write_bytes(canonical(registry))
    state_directory = value.registry_path.parent / "operation-state"
    (state_directory / "alias.lock").touch(mode=0o600)
    actual_state_wire(value, monkeypatch)
    first = second = first_group = second_group = None

    async def workflow():
        nonlocal first, second, first_group, second_group
        try:
            with pytest.raises(ProviderRefused):
                # The measured pre-fix source admitted B despite A's durable,
                # deliberately unfinalized active record.
                first_profile = load_installed_profile("fixture")
                first = module._InstalledOperationState(first_profile)
                first_group = module._OperationGroup(first_profile)
                await first.begin(first_group)
                first_group.kill()
                first.release()
                second_profile = load_installed_profile("alias")
                second = module._InstalledOperationState(second_profile)
                second_group = module._OperationGroup(second_profile)
                await second.begin(second_group)
        finally:
            for holder, group in ((first, first_group), (second, second_group)):
                if group is not None:
                    group.kill()
                    group.close()
                if holder is not None:
                    if holder.owner is not None:
                        holder.release()
                    holder.close()

    asyncio.run(workflow())


def test_actual_separate_process_lock_contention_is_bounded_without_active_write(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    calls = actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, lock, name = _state_paths("fixture")
    locker = module.subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,sys,time,fcntl;fd=os.open(sys.argv[1],os.O_RDWR);"
            "fcntl.flock(fd,fcntl.LOCK_EX);print('locked',flush=True);time.sleep(30)",
            str(lock),
        ],
        stdout=module.subprocess.PIPE,
        stderr=module.subprocess.DEVNULL,
        env={"PATH": os.defpath},
        start_new_session=True,
    )
    assert locker.stdout.readline() == b"locked\n"
    monkeypatch.setattr(module, "_CLEANUP_RESERVE", 0.1)

    async def workflow():
        token = module._DEADLINE.set(time.monotonic() + 0.3)
        try:
            started = time.monotonic()
            with pytest.raises(ValueError):
                await state.begin(group)
            assert time.monotonic() - started < 0.8
            assert not state.admitted and not (directory / name).exists()
            assert calls == [] and locker.poll() is None
        finally:
            state.release()
            module._DEADLINE.reset(token)

    try:
        asyncio.run(workflow())
    finally:
        locker.kill()
        locker.wait(timeout=5)
        locker.stdout.close()
        group.kill()
        group.close()
        state.close()


def test_actual_popen_exec_failure_has_no_pipe_or_process_leak(provider_case):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    group = module._OperationGroup(provider_case.provider_profile)

    async def workflow():
        token = module._GROUP.set(group)
        before = set(os.listdir("/proc/self/fd"))
        try:
            with pytest.raises(FileNotFoundError):
                await module._spawn(
                    "/tmp/q4-intentionally-absent-provider-executable",
                    stdin=module.subprocess.PIPE,
                    stdout=module.subprocess.PIPE,
                    stderr=module.subprocess.PIPE,
                    start_new_session=True,
                )
            await asyncio.sleep(0)
            gc.collect()
            assert group.clean
            assert set(os.listdir("/proc/self/fd")) == before
        finally:
            module._GROUP.reset(token)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.close()


@pytest.mark.parametrize("mode", ["ignored", "no-cld-wait"])
def test_actual_nondefault_sigchld_refuses_before_child_creation(mode):
    source = str(Path(module.__file__).parents[1])
    code = (
        "import sys,signal,ctypes,asyncio;sys.path.insert(0," + repr(source) + ");"
        "from vuoro_reconciler import protected_provider as p;"
        + (
            "signal.signal(signal.SIGCHLD,signal.SIG_IGN);"
            if mode == "ignored"
            else "a=p._SignalAction();lib=ctypes.CDLL(None);assert lib.sigaction(signal.SIGCHLD,None,ctypes.byref(a))==0;"
            "a.flags|=2;assert lib.sigaction(signal.SIGCHLD,ctypes.byref(a),None)==0;"
        )
        + "\ndef forbidden(*a,**k):raise AssertionError('child was created')\n"
        "p.subprocess.Popen=forbidden\n"
        "try:asyncio.run(p._spawn('/fixed-unused'))\n"
        "except ValueError:print('refused')\n"
        "else:raise AssertionError('nondefault SIGCHLD accepted')\n"
    )
    result = module.subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        timeout=5,
        env={"PATH": os.defpath},
    )
    assert (
        result.returncode == 0
        and result.stdout == b"refused\n"
        and result.stderr == b""
    )


@pytest.mark.parametrize(
    "failed", ["stdin", "stdout", "stderr", "transport0", "transport1", "transport2"]
)
def test_actual_close_failure_attempts_all_retains_active_until_explicit_cleanup(
    provider_case, monkeypatch, failed
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    actual_state_wire(value, monkeypatch)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, _, active = _state_paths("fixture")
    attempted = []

    class CloseResource:
        def __init__(self, name, original):
            self.name, self.original = name, original

        def close(self):
            attempted.append(self.name)
            if self.name == failed:
                raise OSError("disposable close failure")
            return self.original.close()

        def __getattr__(self, name):
            return getattr(self.original, name)

    async def workflow():
        token = module._GROUP.set(group)
        process = None
        originals = transports = None
        try:
            await state.begin(group)
            process = await module._spawn(
                sys.executable,
                "-c",
                f"import os,time;os.write({group.procs_fd},b'0');time.sleep(30)",
                pass_fds=(group.procs_fd,),
                start_new_session=True,
                stdin=module.subprocess.PIPE,
                stdout=module.subprocess.PIPE,
                stderr=module.subprocess.PIPE,
            )
            originals = tuple(
                getattr(process._process, name)
                for name in ("stdin", "stdout", "stderr")
            )
            transports = tuple(process._transport.transports)
            assert len(transports) == 3
            for name, original in zip(("stdin", "stdout", "stderr"), originals):
                setattr(process._process, name, CloseResource(name, original))
            process._transport.transports = [
                CloseResource("transport" + str(index), original)
                for index, original in enumerate(transports)
            ]
            module._kill_tree(process)
            await process.wait()
            with pytest.raises(OSError):
                process._transport.close()
            assert set(attempted) == {
                "stdin",
                "stdout",
                "stderr",
                "transport0",
                "transport1",
                "transport2",
            }
            assert not process._transport.closed
            await asyncio.sleep(0)
            assert process.returncode is not None
            assert not Path("/proc/" + str(process.pid)).exists()
            with pytest.raises(ValueError):
                await state.finalize(group)
            assert (directory / active).exists() and state.admitted
        finally:
            if process is not None:
                # Explicit fixture repair closes the injected failed resource;
                # product admission never converts the failed flag into proof.
                if originals is not None:
                    for name, original in zip(("stdin", "stdout", "stderr"), originals):
                        setattr(process._process, name, original)
                if transports is not None:
                    process._transport.transports = list(transports)
                process._transport.close()
                await process.wait()
                await asyncio.sleep(0)
            if not group.clean:
                group.kill()
            if state.admitted:
                await state.finalize(group)
            state.release()
            module._GROUP.reset(token)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.close()
        state.close()


def test_actual_caller_cancel_survives_injected_pipe_close_error(tmp_path, monkeypatch):
    entered = tmp_path / "child-started"
    failed_pipe = None

    async def workflow():
        nonlocal failed_pipe
        process = await module._spawn(
            sys.executable,
            "-c",
            f"import pathlib,time;pathlib.Path({str(entered)!r}).write_text('started');time.sleep(30)",
            start_new_session=True,
            stdin=module.subprocess.PIPE,
            stdout=module.subprocess.PIPE,
            stderr=module.subprocess.PIPE,
        )
        failed_pipe = process._process.stdin

        class FailedClose:
            @property
            def closed(self):
                return failed_pipe.closed

            def close(self):
                raise OSError("disposable close failure")

        process._process.stdin = FailedClose()
        task = asyncio.create_task(module._capture(process, b""))
        for _ in range(100):
            if entered.exists():
                break
            await asyncio.sleep(0.005)
        assert entered.exists()
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError) as refusal:
                await task
            assert "protected-provider-pipe-cleanup-uncertain" in getattr(
                refusal.value, "__notes__", []
            )
            assert not process._transport.closed and process.returncode is not None
            assert not Path("/proc/" + str(process.pid)).exists()
            assert process._process.stdout.closed and process._process.stderr.closed
        finally:
            process._process.stdin = failed_pipe
            process._transport.close()
            await process.wait()
            await asyncio.sleep(0)

    asyncio.run(workflow())
    gc.collect()


@pytest.mark.parametrize("phase", ["adaptation", "capture", "spawn-cancel"])
@pytest.mark.parametrize("pipe", ["stdin", "stdout", "stderr"])
def test_actual_cleanup_seams_reap_despite_persistent_pipe_close_error(
    monkeypatch, phase, pipe
):
    captured = []
    original_init = module._PipeOwner.__init__
    original_factory = module._create_owned_process

    class FailedClose:
        def __init__(self, original):
            self.original = original

        def close(self):
            raise OSError("disposable persistent close failure")

        def __getattr__(self, name):
            return getattr(self.original, name)

    def capture(owner, process):
        original_init(owner, process)
        original = getattr(process, pipe)
        captured.append((owner, process, original))
        if phase == "adaptation":
            setattr(process, pipe, FailedClose(original))

    monkeypatch.setattr(module._PipeOwner, "__init__", capture)

    async def workflow():
        loop = asyncio.get_running_loop()
        args = (sys.executable, "-c", "import time;time.sleep(30)")
        kwargs = dict(
            start_new_session=True,
            stdin=module.subprocess.PIPE,
            stdout=module.subprocess.PIPE,
            stderr=module.subprocess.PIPE,
        )
        if phase == "adaptation":

            async def fail(*args, **kwargs):
                raise OSError("primary adaptation failure")

            monkeypatch.setattr(loop, "connect_read_pipe", fail)
            with pytest.raises(OSError, match="primary adaptation failure"):
                await module._create_owned_process(*args, **kwargs)
        elif phase == "capture":
            process = await module._spawn(*args, **kwargs)
            setattr(process._process, pipe, FailedClose(captured[0][2]))
            task = asyncio.create_task(module._capture(process, b""))
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:

            async def delayed(*args, **kwargs):
                process = await original_factory(*args, **kwargs)
                setattr(process._process, pipe, FailedClose(captured[0][2]))
                await asyncio.sleep(0.08)
                return process

            monkeypatch.setattr(module, "_create_owned_process", delayed)
            task = asyncio.create_task(module._spawn(*args, **kwargs))
            for _ in range(100):
                if captured:
                    break
                await asyncio.sleep(0.005)
            assert captured
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        owner, raw_process, original = captured[0]
        assert raw_process.returncode is not None  # Do not poll away the bug.
        assert not Path("/proc/" + str(raw_process.pid)).exists()
        with pytest.raises(ChildProcessError):
            os.waitpid(raw_process.pid, os.WNOHANG)
        assert not owner.closed
        setattr(raw_process, pipe, original)
        owner.close()
        assert owner.closed
        await asyncio.sleep(0)
        gc.collect()

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        for owner, raw_process, original in captured:
            setattr(raw_process, pipe, original)
            if raw_process.poll() is None:
                raw_process.kill()
            raw_process.wait(timeout=5)
            owner.close()


def test_actual_repeated_cancel_drains_owned_reaper(monkeypatch):
    async def workflow():
        process = await module._spawn(
            sys.executable,
            "-c",
            "import time;time.sleep(30)",
            start_new_session=True,
            stdin=module.subprocess.PIPE,
            stdout=module.subprocess.PIPE,
            stderr=module.subprocess.PIPE,
        )
        entered = asyncio.Event()
        original_wait = process.wait
        waits = 0

        async def delayed_wait():
            nonlocal waits
            waits += 1
            if waits > 1:
                entered.set()
                await asyncio.sleep(0.08)
            return await original_wait()

        monkeypatch.setattr(process, "wait", delayed_wait)
        task = asyncio.create_task(module._capture(process, b""))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process._process.returncode is not None
        assert not Path("/proc/" + str(process.pid)).exists()
        assert process._transport.closed
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
        await asyncio.sleep(0)

    asyncio.run(workflow())
    gc.collect()


def test_actual_state_helper_pipe_failure_retains_durable_active(
    provider_case, monkeypatch
):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    value = provider_case
    actual_state_wire(value, monkeypatch)
    captured = []
    original_factory = module._create_owned_process

    class FailedClose:
        def __init__(self, original):
            self.original = original

        def close(self):
            raise OSError("disposable state helper close failure")

        def __getattr__(self, name):
            return getattr(self.original, name)

    async def capture(*args, **kwargs):
        owner = await original_factory(*args, **kwargs)
        original = owner._process.stdin
        captured.append((owner._transport, owner._process, original))
        owner._process.stdin = FailedClose(original)
        return owner

    monkeypatch.setattr(module, "_create_owned_process", capture)
    state = module._InstalledOperationState(value.provider_profile)
    group = module._OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import _state_paths

    directory, _, name = _state_paths("fixture")

    async def workflow():
        try:
            with pytest.raises(module.ProviderRefused):
                await state.begin(group)
            assert not state.admitted and (directory / name).exists()
            for owner, process, original in captured:
                assert process.returncode is not None
                assert not Path("/proc/" + str(process.pid)).exists()
                assert not owner.closed
                process.stdin = original
                owner.close()
            with pytest.raises(ValueError):
                await state.finalize(group)
        finally:
            state.release()
            group.kill()
        await asyncio.sleep(0)

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        group.close()
        state.close()


@pytest.mark.parametrize("failed", ["stdin", "stdout", "stderr"])
def test_actual_pidfd_and_pipe_close_failure_preserves_allocation_error(
    monkeypatch, failed
):
    captured, attempted = [], []
    original_init = module._PipeOwner.__init__
    allocation = OSError("disposable original pidfd failure")

    class CloseProbe:
        def __init__(self, name, original):
            self.name, self.original = name, original

        def close(self):
            attempted.append(self.name)
            if self.name == failed:
                raise OSError("disposable secondary close failure")
            self.original.close()

        def __getattr__(self, name):
            return getattr(self.original, name)

    def capture(owner, process):
        original_init(owner, process)
        originals = tuple(
            getattr(process, name) for name in ("stdin", "stdout", "stderr")
        )
        captured.append((owner, process, originals))
        for name, original in zip(("stdin", "stdout", "stderr"), originals):
            setattr(process, name, CloseProbe(name, original))

    def fail_pidfd(pid):
        raise allocation

    monkeypatch.setattr(module._PipeOwner, "__init__", capture)
    monkeypatch.setattr(module.os, "pidfd_open", fail_pidfd)

    async def workflow():
        with pytest.raises(OSError) as refusal:
            await module._create_owned_process(
                sys.executable,
                "-c",
                "import time;time.sleep(30)",
                stdin=module.subprocess.PIPE,
                stdout=module.subprocess.PIPE,
                stderr=module.subprocess.PIPE,
                start_new_session=True,
            )
        assert refusal.value is allocation and refusal.value.__suppress_context__
        assert getattr(refusal.value, "__notes__", []) == [
            "protected-provider-child-cleanup-uncertain"
        ]
        owner, process, originals = captured[0]
        assert attempted == ["stdin", "stdout", "stderr"]
        assert process.returncode is not None and not owner.closed
        assert not Path("/proc/" + str(process.pid)).exists()
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
        for name, original in zip(("stdin", "stdout", "stderr"), originals):
            setattr(process, name, original)
        owner.close()  # Explicit disposable fixture repair only.
        assert owner.closed
        await asyncio.sleep(0)

    asyncio.run(workflow())
    gc.collect()
