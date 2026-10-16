"""Runtime composition pin and private-file boundary falsifiers."""

import copy
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from vuoro_reconciler import protected_runtime_registry as module
from test_protected_read_transport import ca_hash, NAMESPACE, REPOSITORY, canonical
from test_protected_read_transport import certificates as certificate_fixture


@pytest.fixture(scope="session")
def certificates(tmp_path_factory):
    return certificate_fixture.__wrapped__(tmp_path_factory)


@pytest.fixture
def value(tmp_path, certificates):
    fields = {}
    for name, source in [
        ("ca", "ca.pem"),
        ("certificate", "client.pem"),
        ("key", "client.key"),
    ]:
        path = tmp_path / name
        path.write_bytes((certificates / source).read_bytes())
        path.chmod(0o600)
        fields[name] = str(path)
    for name in ("session", "native-token"):
        path = tmp_path / name
        path.write_text("disposable fixture only")
        path.chmod(0o600)
        fields[name] = str(path)
    for name in ("mirror", "signer"):
        path = tmp_path / name
        path.mkdir(mode=0o700)
        fields[name] = str(path)
    common = {
        "https_origin": "https://localhost:44443",
        "connect_ip": "127.0.0.1",
        "ca_der_sha256": [ca_hash(certificates)],
        "server_ca_path": fields["ca"],
    }
    return {
        "schema": "vuoro-protected-runtime-profile/v1",
        "profile_id": "disposable",
        "effect_repository": REPOSITORY,
        "namespace": NAMESPACE,
        "broker_read": {
            **common,
            "client_certificate_path": fields["certificate"],
            "private_key_path": fields["key"],
            "session_path": fields["session"],
        },
        "native_read": {**common, "credential_ref": "file:" + fields["native-token"]},
        "local_base": {
            "kind": "local-git-objects/v1",
            "git_directory": fields["mirror"],
        },
        "signer": {"kind": "local-openpgp-agent/v1", "gnupg_home": fields["signer"]},
    }


def construct(value):
    return module.RuntimeProfile("disposable", canonical(value))


def test_frozen_metadata_and_exact_profile_scopes(value):
    result = construct(value)
    assert result.data == value
    for kind in ("broker_read", "native_read"):
        profile = result.read_profile(kind)
        assert profile.namespace_utf8 == canonical(NAMESPACE)
        assert profile.repository == REPOSITORY
    detached = result.data
    detached["namespace"]["repo_id"] = "changed"
    assert result.data == value
    with pytest.raises(FrozenInstanceError):
        result.profile_id = "changed"
    assert REPOSITORY not in repr(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "id",
        "namespace",
        "http",
        "dns",
        "ca",
        "duplicate-ca",
        "native-selector",
        "base-kind",
        "signer-kind",
        "path-traversal",
    ],
)
def test_closed_profile_refusals_have_no_private_diagnostics(value, mutation):
    value = copy.deepcopy(value)
    if mutation == "extra":
        value["caller_factory"] = "private-looking-module"
    elif mutation == "id":
        value["profile_id"] = "other"
    elif mutation == "namespace":
        value["namespace"]["extra"] = "forged"
    elif mutation == "http":
        value["native_read"]["https_origin"] = "http://localhost"
    elif mutation == "dns":
        value["native_read"]["connect_ip"] = "localhost"
    elif mutation == "ca":
        value["broker_read"]["ca_der_sha256"] = ["sha256:" + "0" * 64]
    elif mutation == "duplicate-ca":
        value["native_read"]["ca_der_sha256"] *= 2
    elif mutation == "native-selector":
        value["native_read"]["credential_ref"] = "env:SECRET"
    elif mutation == "base-kind":
        value["local_base"]["kind"] = "caller-command"
    elif mutation == "signer-kind":
        value["signer"]["kind"] = "caller-command"
    elif mutation == "path-traversal":
        value["signer"]["gnupg_home"] += "/../signer"
    with pytest.raises(module.RuntimeRefused) as caught:
        construct(value)
    assert str(caught.value) == "protected-runtime-invalid"
    assert caught.value.__context__ is caught.value.__cause__ is None


@pytest.mark.parametrize("mutation", ["mode", "hardlink", "symlink", "empty", "large"])
def test_private_session_file_boundary(value, tmp_path, mutation):
    path = Path(value["broker_read"]["session_path"])
    if mutation == "mode":
        path.chmod(0o644)
    elif mutation == "hardlink":
        os.link(path, tmp_path / "linked")
    elif mutation == "symlink":
        moved = tmp_path / "moved"
        path.rename(moved)
        path.symlink_to(moved)
    elif mutation == "empty":
        path.write_bytes(b"")
    elif mutation == "large":
        path.write_bytes(b"x" * 65537)
    with pytest.raises(module.RuntimeRefused):
        construct(value)


def installed(value, tmp_path, monkeypatch):
    root = tmp_path / "installed"
    directory = root / "runtime-profiles"
    directory.mkdir(parents=True, mode=0o700)
    raw = canonical(value)
    file = directory / "disposable.json"
    file.write_bytes(raw)
    file.chmod(0o600)
    provider = SimpleNamespace(
        data={
            "effect_repository": REPOSITORY,
            "runtime_profile_sha256": module._hash(raw),
        }
    )
    monkeypatch.setattr(module, "_root", lambda: root)
    monkeypatch.setattr(module, "_provider_profile", lambda selected: provider)
    # Fixture-only filesystem admission, never an installed caller override.
    fake = SimpleNamespace(_filesystem=lambda fd: "ext4", _LOCAL_FILESYSTEMS={"ext4"})
    monkeypatch.setitem(
        sys.modules, "vuoro_reconciler.protected_provider_registry", fake
    )
    return file, provider


def test_installed_exact_digest_and_environment_paths_ignored(
    value, tmp_path, monkeypatch
):
    installed(value, tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", "/wrong-home")
    monkeypatch.setenv("XDG_STATE_HOME", "/wrong-state")
    result = module.load_runtime_profile("disposable")
    assert result.data == value


@pytest.mark.parametrize(
    "mutation",
    [
        "digest",
        "repository",
        "noncanonical",
        "missing",
        "private-parent",
        "profile-replaced",
    ],
)
def test_installed_binding_refuses_before_runtime_or_credentials(
    value, tmp_path, monkeypatch, mutation
):
    path, provider = installed(value, tmp_path, monkeypatch)
    if mutation == "digest":
        provider.data["runtime_profile_sha256"] = "sha256:" + "0" * 64
    elif mutation == "repository":
        provider.data["effect_repository"] = "other"
    elif mutation == "noncanonical":
        raw = json.dumps(value, indent=2).encode()
        path.write_bytes(raw)
        provider.data["runtime_profile_sha256"] = module._hash(raw)
    elif mutation == "missing":
        path.unlink()
    elif mutation == "private-parent":
        path.parent.chmod(0o755)
    elif mutation == "profile-replaced":
        calls = []

        def replacement(selected):
            calls.append(selected)
            return (
                provider
                if len(calls) == 1
                else SimpleNamespace(data={"different": True})
            )

        monkeypatch.setattr(module, "_provider_profile", replacement)
    with pytest.raises(module.RuntimeRefused):
        module.load_runtime_profile("disposable")


def test_file_inode_replacement_during_open_refuses(value, monkeypatch):
    target = Path(value["broker_read"]["server_ca_path"])
    original = os.open
    replaced = False

    def swap(path, flags, *args, **kwargs):
        nonlocal replaced
        if Path(path) == target and not replaced:
            replaced = True
            copy_path = target.with_name("replacement")
            copy_path.write_bytes(target.read_bytes())
            copy_path.chmod(0o600)
            copy_path.replace(target)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", swap)
    with pytest.raises(module.RuntimeRefused):
        construct(value)


@pytest.mark.parametrize("mode", [0o700, 0o755])
def test_parent_replacement_after_private_precheck_refuses(
    value, tmp_path, monkeypatch, mode
):
    file, provider = installed(value, tmp_path, monkeypatch)
    original = module._directory
    swapped = False

    def replace(value, *, private):
        nonlocal swapped
        result = original(value, private=private)
        if Path(value) == file.parent and not swapped:
            swapped = True
            moved = file.parent.with_name("old-profile-parent")
            file.parent.rename(moved)
            file.parent.mkdir(mode=mode)
            file.write_bytes((moved / file.name).read_bytes())
            file.chmod(0o600)
        return result

    monkeypatch.setattr(module, "_directory", replace)
    with pytest.raises(module.RuntimeRefused):
        module.load_runtime_profile("disposable")


def test_profile_replacement_during_construction_refuses(value, tmp_path, monkeypatch):
    file, provider = installed(value, tmp_path, monkeypatch)
    original = module.RuntimeProfile

    def replace(*args):
        result = original(*args)
        replacement = file.with_name("new-profile.json")
        replacement.write_bytes(file.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(file)
        return result

    monkeypatch.setattr(module, "RuntimeProfile", replace)
    with pytest.raises(module.RuntimeRefused):
        module.load_runtime_profile("disposable")
