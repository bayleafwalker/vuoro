"""Fixed installed local adapter selection and refusal boundary."""

import asyncio
from types import SimpleNamespace
import pytest
from vuoro_reconciler import protected_runtime_local as module
import test_protected_runtime_local_child as local_fixtures
import test_protected_runtime_registry as runtime_fixtures


def test_sign_input_is_closed_before_any_child():
    authority = object.__new__(module.InstalledLocalAuthority)
    for value in (b"", "text", b"x" * 65537, bytearray(b"commit")):
        with pytest.raises(ValueError):
            asyncio.run(authority.sign(value))


def test_base_exact_profile_and_oid_before_any_path_access():
    authority = object.__new__(module.InstalledLocalAuthority)
    authority.profile = SimpleNamespace(signing=object())
    for profile, oid in (
        (object(), "a" * 40),
        (authority.profile.signing, "a" * 39),
        (authority.profile.signing, "A" * 40),
        (authority.profile.signing, True),
    ):
        with pytest.raises(ValueError):
            asyncio.run(authority.acquire_base(profile, oid, "/missing/unregistered"))


@pytest.fixture
def installed_local(tmp_path, monkeypatch, request):
    """Disposable pwd-root only; no production installation or credential read."""
    import hashlib
    import importlib
    import os
    from pathlib import Path
    import sys
    from types import SimpleNamespace
    import vuoro_reconciler
    from vuoro_reconciler import protected_runtime_registry as runtime_registry
    from test_protected_runtime_registry import value as value_fixture

    source = os.environ.get("Q4_PROVIDER_TEST_SOURCE_DIR")
    if not source:
        pytest.skip("unmerged provider fixture source required")
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    provider_source = Path(source)
    monkeypatch.setattr(
        vuoro_reconciler, "__path__", [*vuoro_reconciler.__path__, str(provider_source)]
    )
    monkeypatch.syspath_prepend(str(provider_source.parents[1] / "tests"))
    registry = importlib.import_module("vuoro_reconciler.protected_provider_registry")
    lifecycle = importlib.import_module("vuoro_reconciler.protected_provider")
    helpers = importlib.import_module("test_protected_provider_registry")
    keys = request.getfixturevalue("keys")
    certificates = request.getfixturevalue("certificates")
    data = value_fixture.__wrapped__(tmp_path, certificates)
    registered = helpers.data(tmp_path, certificates, keys)
    data["profile_id"] = "fixture"
    data["effect_repository"] = registered["effect_repository"]
    data["signer"]["gnupg_home"] = str(keys[0]["home"])
    runtime_source = Path(runtime_registry.__file__).parent

    def pins():
        result = {}
        for directory in (provider_source, runtime_source):
            for path in sorted(directory.rglob("*.py")):
                name = str(path.relative_to(directory))
                digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                result[name] = digest
        return result

    monkeypatch.setattr(registry, "_source_pins", pins)
    registered["module_sha256"] = pins()
    registered["runtime_profile_sha256"] = registry._hash(registry._canonical(data))
    path = helpers.install_test_registry(tmp_path, monkeypatch, registered)
    directory = path.parent / "runtime-profiles"
    directory.mkdir(mode=0o700)
    runtime_path = directory / "fixture.json"
    runtime_path.write_bytes(registry._canonical(data))
    runtime_path.chmod(0o600)
    home = path.parents[3]
    original = lifecycle._create_owned_process
    processes = []
    # Installed production has one module directory. This explicit fixture
    # combines the two unmerged public source trees while checking every byte.
    bootstrap = (
        "import asyncio,hashlib,os,pathlib,pwd,sys,types;"
        f"sys.path.insert(0,{str(runtime_source.parent)!r});"
        "import vuoro_reconciler;"
        f"vuoro_reconciler.__path__=[{str(runtime_source)!r},{str(provider_source)!r}];"
        "from vuoro_reconciler import protected_provider_registry as registry;"
        f"pwd.getpwuid=lambda uid:types.SimpleNamespace(pw_dir={str(home)!r});\n"
        "def pins():\n"
        " result={}\n"
        f" for directory in [{str(provider_source)!r},{str(runtime_source)!r}]:\n"
        "  directory=pathlib.Path(directory)\n"
        "  for path in sorted(directory.rglob('*.py')):\n"
        "   name=str(path.relative_to(directory));digest='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()\n"
        "   # Runtime tree is first in the actual package import path.\n"
        "   result[name]=digest\n"
        " return result\n"
        "registry._source_pins=pins\n"
    )

    async def spawn(*args, **kwargs):
        if "--state" in args:
            script = (
                bootstrap
                + "from vuoro_reconciler import protected_provider_child as child;sys.exit(child.main())"
            )
            process = await original(
                sys.executable, "-I", "-c", script, "--state", *args[-4:], **kwargs
            )
        elif "vuoro_reconciler.protected_runtime_local_child" in args:
            script = (
                bootstrap
                + "from vuoro_reconciler import protected_runtime_local_child as child;\n"
                + result.injection
                + "\nasyncio.run(child.main())"
            )
            process = await original(
                sys.executable, "-I", "-c", script, *args[-3:], **kwargs
            )
        else:
            process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(lifecycle, "_create_owned_process", spawn)
    authority = module.InstalledLocalAuthority("fixture")
    result = SimpleNamespace(
        authority=authority,
        runtime_path=runtime_path,
        registry_path=path,
        processes=processes,
        runtime_data=data,
        lifecycle=lifecycle,
        registry=registry,
        keys=keys,
        injection="",
    )
    yield result
    result.authority.close()


keys = local_fixtures.keys
objects = local_fixtures.objects
certificates = runtime_fixtures.certificates


def test_actual_installed_local_base_state_and_child_closure(installed_local, objects):
    import os
    from pathlib import Path

    value = installed_local
    git, mirror, target, oid, unrelated = objects
    # Rewrite only fixture metadata before constructing a new pinned holder.
    value.authority.close()
    value.runtime_data["local_base"]["git_directory"] = str(mirror)
    raw = value.registry._canonical(value.runtime_data)
    value.runtime_path.write_bytes(raw)
    registry_data = value.registry._read(value.registry_path.read_bytes(), 65536)
    registry_data["profiles"]["fixture"]["runtime_profile_sha256"] = (
        value.registry._hash(raw)
    )
    value.registry_path.write_bytes(value.registry._canonical(registry_data))
    value.authority = module.InstalledLocalAuthority("fixture")
    asyncio.run(
        value.authority.acquire_base(value.authority.profile.signing, oid, target)
    )
    assert not value.authority._state.admitted and value.authority._state.owner is None
    assert not (value.registry_path.parent / "operation-state/fixture.active").exists()
    assert value.processes
    for process in value.processes:
        assert (
            process._process.returncode is not None and process._provider_pidfd is None
        )
        assert (
            process._transport.closed and not Path("/proc/" + str(process.pid)).exists()
        )
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
    from test_protected_runtime_local_child import run

    assert run(git, target, "cat-file", "-t", oid).stdout == b"commit\n"
    assert run(git, target, "cat-file", "-e", unrelated, check=False).returncode != 0
    value.authority.close()


def test_actual_installed_signature_has_private_state_and_complete_cleanup(
    installed_local,
):
    value = installed_local
    signature = asyncio.run(value.authority.sign(b"fixture-only unsigned content\n"))
    assert signature.startswith(b"-----BEGIN PGP SIGNATURE-----\n")
    assert value.authority._state.owner is None and not value.authority._state.admitted
    assert not (value.registry_path.parent / "operation-state/fixture.active").exists()
    assert all(
        process._process.returncode is not None and process._transport.closed
        for process in value.processes
    )


def stall(value, marker):
    value.injection = (
        "async def stall(*args):\n"
        " descendant=os.fork()\n"
        " if descendant==0:\n"
        "  os.setsid()\n"
        f"  pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "  __import__('time').sleep(30)\n"
        "  os._exit(0)\n"
        " await asyncio.sleep(30)\n"
        "child._sign=stall\n"
    )


@pytest.mark.parametrize(
    "mode", ["timeout", "cancel", "repeated-cancel", "failed-close"]
)
def test_actual_local_containment_failure_paths(
    installed_local, tmp_path, monkeypatch, mode
):
    import gc
    import os
    from pathlib import Path
    import time

    value = installed_local
    marker = tmp_path / "disposable-descendant.pid"
    stall(value, marker)
    original_create = value.lifecycle._create_owned_process
    reap_entered = asyncio.Event()
    originals = []

    class FailedClose:
        def __init__(self, original):
            self.original = original

        def close(self):
            raise OSError("disposable local close failure")

        def __getattr__(self, name):
            return getattr(self.original, name)

    async def create(*args, **kwargs):
        process = await original_create(*args, **kwargs)
        if "vuoro_reconciler.protected_runtime_local_child" in args:
            if mode == "failed-close":
                original = process._process.stdin
                originals.append((process, original))
                process._process.stdin = FailedClose(original)
            if mode == "repeated-cancel":
                wait = process.wait
                count = 0

                async def delayed():
                    nonlocal count
                    count += 1
                    if count > 1:
                        reap_entered.set()
                        await asyncio.sleep(0.08)
                    return await wait()

                monkeypatch.setattr(process, "wait", delayed)
        return process

    monkeypatch.setattr(value.lifecycle, "_create_owned_process", create)
    if mode == "timeout":
        monkeypatch.setattr(module, "_TOTAL", 1.1)
        monkeypatch.setattr(module, "_CLEANUP", 0.3)

    async def workflow():
        started = time.monotonic()
        task = asyncio.create_task(value.authority.sign(b"fixture unsigned content"))
        for _ in range(400):
            if marker.exists():
                break
            await asyncio.sleep(0.005)
        assert marker.exists()
        descendant = int(marker.read_text())
        if mode == "timeout":
            with pytest.raises(module.RuntimeRefused):
                await task
        else:
            task.cancel()
            if mode == "repeated-cancel":
                await asyncio.wait_for(reap_entered.wait(), 2)
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert time.monotonic() - started < 3
        assert value.authority._state.owner is None
        active = value.registry_path.parent / "operation-state/fixture.active"
        if mode == "failed-close":
            assert active.exists() and value.authority._state.admitted
            assert originals and not originals[0][0]._transport.closed
        else:
            assert not active.exists() and not value.authority._state.admitted
        for process in value.processes:
            assert process._process.returncode is not None
            assert process._provider_pidfd is None
            assert not Path("/proc/" + str(process.pid)).exists()
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
        # Descendants may be momentarily visible as init-owned zombies, but no
        # credential-bearing process survives the cgroup's populated=0 proof.
        if Path("/proc/" + str(descendant) + "/stat").exists():
            assert (
                Path("/proc/" + str(descendant) + "/stat").read_text().split()[2] == "Z"
            )
        for process, original in originals:
            process._process.stdin = original
            process._transport.close()  # Explicit disposable fixture repair.
        await asyncio.sleep(0)
        gc.collect()

    asyncio.run(workflow())
    gc.collect()


@pytest.mark.parametrize("kind", ["registry", "runtime", "code"])
def test_fresh_child_pins_refuse_before_local_authority(
    installed_local, monkeypatch, tmp_path, kind
):
    value = installed_local
    marker = tmp_path / "authority-contacted"
    value.injection = (
        "original_sign=child._sign\n"
        "async def tracked(*args):\n"
        f" pathlib.Path({str(marker)!r}).write_text('contacted')\n"
        " return await original_sign(*args)\n"
        "child._sign=tracked\n"
    )
    if kind == "runtime":
        data = value.registry._read(value.runtime_path.read_bytes(), 65536)
        data["signer"]["gnupg_home"] = data["local_base"]["git_directory"]
        value.runtime_path.write_bytes(value.registry._canonical(data))
    else:
        data = value.registry._read(value.registry_path.read_bytes(), 65536)
        selected = data["profiles"]["fixture"]
        if kind == "registry":
            selected["runtime_profile_sha256"] = "sha256:" + "8" * 64
        else:
            selected["module_sha256"]["protected_runtime_local_child.py"] = (
                "sha256:" + "8" * 64
            )
        value.registry_path.write_bytes(value.registry._canonical(data))
    with pytest.raises(module.RuntimeRefused) as caught:
        asyncio.run(value.authority.sign(b"disposable unsigned content"))
    assert not marker.exists()
    assert caught.value.__context__ is caught.value.__cause__ is None


@pytest.mark.parametrize("later_close_error", [False, True])
def test_actual_finalize_cancellation_preserved_after_success(
    installed_local, monkeypatch, later_close_error
):
    import os
    from pathlib import Path

    value = installed_local

    async def workflow():
        entered = asyncio.Event()

        async def finalize(group):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(value.authority._state, "finalize", finalize)
        if later_close_error:
            close = value.lifecycle._OperationGroup.close

            def fail_after_close(group):
                close(group)
                if entered.is_set():
                    raise OSError("disposable later group close failure")

            monkeypatch.setattr(
                value.lifecycle._OperationGroup, "close", fail_after_close
            )
        task = asyncio.create_task(
            value.authority.sign(b"disposable finalize cancellation fixture")
        )
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("first-finalize-cancellation")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert task.cancelled() and caught.value.args == (
            "first-finalize-cancellation",
        )
        assert "protected-local-authority-cleanup-uncertain" in getattr(
            caught.value, "__notes__", []
        )
        assert (value.registry_path.parent / "operation-state/fixture.active").exists()
        assert value.authority._state.owner is None and value.authority._state.admitted
        for process in value.processes:
            assert process._process.returncode is not None and process._transport.closed
            assert (
                process._provider_pidfd is None
                and not Path("/proc/" + str(process.pid)).exists()
            )
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)

    asyncio.run(workflow())


def test_actual_first_work_cancel_survives_later_finalize_cancel(
    installed_local, tmp_path, monkeypatch
):
    value = installed_local
    marker = tmp_path / "disposable-work-cancel.pid"
    stall(value, marker)

    async def workflow():
        entered = asyncio.Event()

        async def finalize(group):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(value.authority._state, "finalize", finalize)
        task = asyncio.create_task(
            value.authority.sign(b"disposable first work cancellation fixture")
        )
        for _ in range(400):
            if marker.exists():
                break
            await asyncio.sleep(0.005)
        assert marker.exists()
        task.cancel("first-work-cancellation")
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("later-finalize-cancellation")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert task.cancelled() and caught.value.args == ("first-work-cancellation",)
        assert "protected-local-authority-cleanup-uncertain" in getattr(
            caught.value, "__notes__", []
        )
        assert (value.registry_path.parent / "operation-state/fixture.active").exists()
        assert value.authority._state.owner is None and value.authority._state.admitted
        assert all(
            process._process.returncode is not None and process._transport.closed
            for process in value.processes
        )

    asyncio.run(workflow())
