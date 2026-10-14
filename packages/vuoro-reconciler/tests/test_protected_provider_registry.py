"""Installed local metadata admission, without production installation."""

import base64
import os
from pathlib import Path
import pwd
import shutil
import ssl
import sys
from types import SimpleNamespace
import pytest
from vuoro_reconciler import protected_provider_registry as module
from vuoro_reconciler.protected_provider_registry import ProviderRefused
from test_protected_copy_codec import canonical, decode, sha
from test_protected_git import profile
import test_protected_git as git_fixtures

import test_protected_read_transport as tls_fixtures


keys = git_fixtures.keys
certificates = tls_fixtures.certificates


def data(tmp_path, certificates, keys, *, artifact_root=None):
    root = artifact_root or tmp_path / "artifacts-root"
    if not root.exists():
        root.mkdir(mode=0o700)
    signing = profile(decode(), keys[0])

    def b64(value):
        return base64.b64encode(value).decode("ascii")

    result = {
        "schema": "vuoro-protected-provider-profile/v1",
        "effect_repository": decode().repository,
        "broker_repository_id": "forgejo:owner/repository",
        "owner": "owner",
        "name": "repository",
        "repository_id": 8,
        "base_branch": "main",
        "https_origin": "https://localhost:443",
        "numeric_ip": "127.0.0.1",
        "ca_der_b64": [
            b64(ssl.PEM_cert_to_DER_cert((certificates / "ca.pem").read_text()))
        ],
        "artifact_root": str(root),
        "signing_profile_utf8_b64": b64(signing.signing_profile_utf8),
        "public_key_utf8_b64": b64(signing.public_key_utf8),
        "module_sha256": module._source_pins(),
        "runtime_profile_sha256": "sha256:" + "9" * 64,
        "cgroup_root": os.environ.get(
            "Q4_PROVIDER_CGROUP_FIXTURE_ROOT", "/sys/fs/cgroup/q4-missing-delegation"
        ),
    }
    for name, path in {
        "credctl": "/run/current-system/sw/bin/credctl",
        "python": sys.executable,
        "git": shutil.which("git"),
        "gpg": shutil.which("gpg"),
    }.items():
        result[name + "_path"] = path
        result[name + "_sha256"] = sha(Path(path).read_bytes())
    return result


def install_test_registry(tmp_path, monkeypatch, value):
    home = tmp_path / "pwd-home"
    directory = home / ".local/state/vuoro-reconciler"
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    state = directory / "operation-state"
    state.mkdir(mode=0o700)
    lock = state / "fixture.lock"
    lock.touch(mode=0o600)
    path = directory / "installed-registry.json"
    path.write_bytes(
        canonical(
            {
                "schema": "vuoro-protected-provider-registry/v1",
                "host_id": module._host_id(),
                "profiles": {"fixture": value},
            }
        )
    )
    path.chmod(0o600)
    monkeypatch.setattr(
        module.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(home))
    )
    # Sandbox unit runs emulate admission; escalated real-state qualification
    # uses the measured btrfs host filesystem without this emulation.
    if os.environ.get("Q4_PROVIDER_REAL_STATE_FS") != "1":
        # Actual /tmp is overlay/tmpfs in this test environment. Emulate admitted
        # local statfs only for fixtures, never expose a production selector.
        real_fs = module._filesystem
        monkeypatch.setattr(
            module,
            "_filesystem",
            lambda fd: real_fs(fd)
            if os.readlink("/proc/self/fd/" + str(fd)).startswith("/sys/fs/cgroup/")
            else "ext4",
        )
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        monkeypatch.setattr(module, "_validate_cgroup_root", lambda root: None)
    return path


def test_registry_ignores_home_xdg_and_refuses_uninstalled(tmp_path, monkeypatch):
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert (
        module._registry_path()
        == Path(real_home) / ".local/state/vuoro-reconciler/installed-registry.json"
    )
    monkeypatch.setattr(
        module.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(tmp_path))
    )
    with pytest.raises(ProviderRefused) as error:
        module.load_installed_profile("fixture")
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert not (tmp_path / ".local").exists()


def test_actual_profile_executable_module_ca_signing_pins(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    result = module.load_installed_profile("fixture")
    assert result.registry_sha256 == sha(
        path.read_bytes()
    ) and result.profile_sha256 == sha(canonical(value))
    assert result.signing.public_key_utf8 == keys[0]["public"]
    assert repr(result).startswith("<") and str(tmp_path) not in repr(result)


@pytest.mark.parametrize(
    "kind",
    [
        "host",
        "unknown-key",
        "noncanonical",
        "boolean-id",
        "mapping",
        "base",
        "ca",
        "signer",
        "module",
        "executable",
        "mode",
        "hardlink",
        "symlink",
        "ancestry",
        "networkfs",
        "tmpfs",
        "overlay",
        "replace",
        "runtime-hash",
    ],
)
def test_registry_admission_falsifiers(tmp_path, monkeypatch, certificates, keys, kind):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    import json

    registry = json.loads(path.read_bytes())
    selected = registry["profiles"]["fixture"]
    if kind == "host":
        registry["host_id"] = "sha256:" + "0" * 64
    elif kind == "unknown-key":
        registry["repair"] = True
    elif kind == "boolean-id":
        selected["repository_id"] = True
    elif kind == "mapping":
        selected["broker_repository_id"] = "forgejo:other/repo"
    elif kind == "base":
        selected["base_branch"] = "../main"
    elif kind == "ca":
        selected["ca_der_b64"] = ["eA=="]
    elif kind == "signer":
        selected["public_key_utf8_b64"] = base64.b64encode(keys[1]["public"]).decode()
    elif kind == "runtime-hash":
        selected["runtime_profile_sha256"] = "bad-runtime-hash"
    elif kind == "module":
        selected["module_sha256"]["protected_git.py"] = "sha256:" + "0" * 64
    elif kind == "executable":
        selected["git_sha256"] = "sha256:" + "0" * 64
    path.write_bytes(canonical(registry))
    if kind == "noncanonical":
        path.write_bytes(path.read_bytes() + b"\n")
    elif kind == "mode":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, path.with_name("other"))
    elif kind == "symlink":
        saved = path.with_name("saved")
        path.rename(saved)
        path.symlink_to(saved)
    elif kind == "ancestry":
        parent = path.parent
        saved = parent.with_name("saved")
        parent.rename(saved)
        parent.symlink_to(saved)
    elif kind in ("networkfs", "tmpfs", "overlay"):
        monkeypatch.setattr(
            module,
            "_filesystem",
            lambda fd: {
                "networkfs": "nfs",
                "tmpfs": "tmpfs",
                "overlay": "overlay",
            }[kind],
        )
    elif kind == "replace":
        real = module._validate_profile

        def swap(profile):
            real(profile)
            new = path.with_name("new")
            new.write_bytes(path.read_bytes())
            new.chmod(0o600)
            os.replace(new, path)

        monkeypatch.setattr(module, "_validate_profile", swap)
    with pytest.raises(ProviderRefused):
        module.load_installed_profile("fixture")


def test_real_statfs_reports_known_linux_magic(tmp_path):
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert type(module._filesystem(descriptor)) is str and bool(
            module._filesystem(descriptor)
        )
    finally:
        os.close(descriptor)


def test_registry_parent_same_inode_chmod_during_admission_refuses(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    original = module._validate_profile

    def drift(profile):
        original(profile)
        path.parent.chmod(0o755)

    monkeypatch.setattr(module, "_validate_profile", drift)
    with pytest.raises(ProviderRefused):
        module.load_installed_profile("fixture")


def test_whole_registry_snapshot_same_descriptor_profile_and_host(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    snapshot = module.load_installed_registry_snapshot("fixture")
    assert snapshot.registry_utf8 == path.read_bytes()
    assert snapshot.profile == module.load_installed_profile("fixture")
    assert snapshot.profile.registry_sha256 == sha(snapshot.registry_utf8)
    assert snapshot.profile.profile_utf8 == canonical(value)
    assert snapshot.host_id == module._host_id()
    assert (
        __import__("json").loads(snapshot.registry_utf8)["host_id"] == snapshot.host_id
    )
    assert repr(snapshot).startswith("<")  # No automatic metadata repr dumping.


def test_whole_registry_snapshot_same_inode_redigest_drift_refuses(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    original = module._validate_profile
    before = path.stat().st_ino

    def drift(profile):
        original(profile)
        raw = path.read_bytes()
        host = module._host_id().encode()
        changed = host[:-1] + (b"1" if host[-1:] != b"1" else b"2")
        path.write_bytes(raw.replace(host, changed))
        assert path.stat().st_ino == before and len(path.read_bytes()) == len(raw)

    monkeypatch.setattr(module, "_validate_profile", drift)
    with pytest.raises(ProviderRefused):
        module.load_installed_registry_snapshot("fixture")


@pytest.mark.parametrize(
    "entry", ["partial", "unknown-active", "quarantine", "unknown-file", "too-many"]
)
def test_store_invalid_or_unknown_active_blocks_global_admission(
    tmp_path, monkeypatch, certificates, keys, entry
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    directory = path.parent / "operation-state"
    if entry == "too-many":
        for index in range(65):
            (directory / ("old" + str(index) + ".lock")).touch(mode=0o600)
    else:
        name = {
            "partial": "fixture.active",
            "unknown-active": "retired.active",
            "quarantine": "retired.quarantine",
            "unknown-file": "unrecognized",
        }[entry]
        (directory / name).write_bytes(b"partial")
        (directory / name).chmod(0o600)
    with pytest.raises(ProviderRefused):
        module.load_installed_registry_snapshot("fixture")


def test_store_harmless_retired_lock_is_admitted_without_repair(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    old = path.parent / "operation-state/retired.lock"
    old.touch(mode=0o600)
    before = old.stat().st_ino
    assert module.load_installed_profile("fixture").profile_utf8 == canonical(value)
    assert old.stat().st_ino == before


def test_old_registry_active_blocks_non_target_pin_rotation(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    previous = module.load_installed_profile("fixture")
    active = path.parent / "operation-state/fixture.active"
    active.write_bytes(module._active_record(previous, "a" * 32))
    active.chmod(0o600)
    registry = __import__("json").loads(path.read_bytes())
    registry["profiles"]["fixture"]["runtime_profile_sha256"] = "sha256:" + "8" * 64
    path.write_bytes(canonical(registry))
    with pytest.raises(ProviderRefused):
        module.load_installed_registry_snapshot("fixture")
    assert active.read_bytes() == module._active_record(previous, "a" * 32)


def test_current_independent_target_active_may_coexist(
    tmp_path, monkeypatch, certificates, keys
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    other = dict(value)
    other.update(
        effect_repository="forgejo:owner/other",
        broker_repository_id="forgejo:owner/other",
        name="other",
        repository_id=9,
    )
    signing = __import__("json").loads(
        base64.b64decode(value["signing_profile_utf8_b64"])
    )
    signing["repository"] = other["effect_repository"]
    other["signing_profile_utf8_b64"] = base64.b64encode(canonical(signing)).decode()
    registry = __import__("json").loads(path.read_bytes())
    registry["profiles"]["other"] = other
    path.write_bytes(canonical(registry))
    (path.parent / "operation-state/other.lock").touch(mode=0o600)
    registered = module.load_installed_profile("other")
    active = path.parent / "operation-state/other.active"
    active.write_bytes(module._active_record(registered, "b" * 32))
    active.chmod(0o600)
    assert module.load_installed_profile("fixture").registry_sha256 == sha(
        path.read_bytes()
    )
    assert active.read_bytes() == module._active_record(registered, "b" * 32)


@pytest.mark.parametrize(
    "first,second",
    [
        ("https://localhost:443", "https://LOCALHOST:443"),
        ("https://localhost", "https://localhost:443"),
        ("https://[::1]:443", "https://[0:0:0:0:0:0:0:1]"),
    ],
)
def test_normalized_physical_origin_alias_refuses_with_exact_profile_bytes(
    tmp_path, monkeypatch, certificates, keys, first, second
):
    value = data(tmp_path, certificates, keys)
    value["https_origin"] = first
    path = install_test_registry(tmp_path, monkeypatch, value)
    registry = __import__("json").loads(path.read_bytes())
    other = dict(
        value, effect_repository="other-logical-repository", https_origin=second
    )
    signing = __import__("json").loads(
        base64.b64decode(other["signing_profile_utf8_b64"])
    )
    signing["repository"] = other["effect_repository"]
    other["signing_profile_utf8_b64"] = base64.b64encode(canonical(signing)).decode()
    registry["profiles"]["other"] = other
    path.write_bytes(canonical(registry))
    (path.parent / "operation-state/other.lock").touch(mode=0o600)
    raw = path.read_bytes()
    with pytest.raises(ProviderRefused):
        module.load_installed_registry_snapshot("fixture")
    assert path.read_bytes() == raw  # No profile byte rewriting at admission.


@pytest.mark.parametrize("drift", ["mode", "content", "replace"])
def test_registry_final_ancestry_bracket_named_drift_refuses(
    tmp_path, monkeypatch, certificates, keys, drift
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    original = module._path_state
    visits = []

    def bracket(candidate):
        answer = original(candidate)
        if Path(candidate) == path:
            visits.append(candidate)
            if len(visits) == 2:
                if drift == "mode":
                    path.chmod(0o644)
                elif drift == "content":
                    raw = path.read_bytes()
                    old = module._host_id().encode()
                    new = old[:-1] + (b"1" if old[-1:] != b"1" else b"2")
                    path.write_bytes(raw.replace(old, new))
                else:
                    old = path.with_name("old-registry")
                    path.rename(old)
                    path.write_bytes(old.read_bytes())
                    path.chmod(0o600)
        return answer

    monkeypatch.setattr(module, "_path_state", bracket)
    with pytest.raises(ProviderRefused):
        module.load_installed_registry_snapshot("fixture")


@pytest.mark.parametrize("drift", ["insert", "delete", "rename", "body", "mode"])
def test_state_store_final_census_and_entry_state_drift_refuses(
    tmp_path, monkeypatch, certificates, keys, drift
):
    value = data(tmp_path, certificates, keys)
    path = install_test_registry(tmp_path, monkeypatch, value)
    snapshot = module.load_installed_profile("fixture")
    directory = path.parent / "operation-state"
    old = directory / "retired.lock"
    old.touch(mode=0o600)
    active = directory / "fixture.active"
    active.write_bytes(module._active_record(snapshot, "a" * 32))
    active.chmod(0o600)
    original = module._bounded_state_names
    calls = []

    def census(descriptor):
        names = original(descriptor)
        calls.append(names)
        if len(calls) == 1:
            if drift == "insert":
                (directory / "other.quarantine").touch(mode=0o600)
            elif drift == "delete":
                old.unlink()
            elif drift == "rename":
                old.rename(directory / "different.lock")
        elif len(calls) == 2:
            if drift == "body":
                active.write_bytes(module._active_record(snapshot, "b" * 32))
            elif drift == "mode":
                active.chmod(0o644)
        return names

    monkeypatch.setattr(module, "_bounded_state_names", census)
    with pytest.raises(ProviderRefused):
        module.load_installed_registry_snapshot("fixture")
