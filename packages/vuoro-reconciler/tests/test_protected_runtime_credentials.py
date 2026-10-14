"""Installed fresh reader identity and actual TLS credential boundary checks."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
import ssl

import pytest

from vuoro_reconciler import protected_runtime_credentials as module
from vuoro_reconciler.protected_read_transport import (
    ProtectedReadRefused,
    _read_wire,
    _COPY_PATH,
)
from vuoro_reconciler.protected_runtime_registry import RuntimeProfile
from vuoro_reconciler.protected_runtime_registry import _owned_file
from test_protected_runtime_registry import value as value_fixture
from test_protected_runtime_registry import certificates as certificate_fixture
from test_protected_read_transport import Loopback, TOKEN, canonical


@pytest.fixture(scope="session")
def certificates(tmp_path_factory):
    return certificate_fixture.__wrapped__(tmp_path_factory)


@pytest.fixture
def value(tmp_path, certificates):
    return value_fixture.__wrapped__(tmp_path, certificates)


@pytest.fixture
def runtime(value, monkeypatch):
    broker = value["broker_read"]
    session = {
        "session_id": "ses_disposable",
        "token": TOKEN,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "subject_id": "disposable-reader",
        "host_id": "disposable-host",
    }
    Path(broker["session_path"]).write_text(json.dumps(session))
    Path(value["native_read"]["credential_ref"][5:]).write_text(TOKEN + "\n")
    result = RuntimeProfile("disposable", canonical(value))
    monkeypatch.setattr(module, "load_runtime_profile", lambda selected: result)
    monkeypatch.setattr(
        module, "_installed_provider", lambda selected: SimpleNamespace(data={})
    )

    monkeypatch.setattr(
        module, "_installed_state", lambda provider: SimpleNamespace(close=lambda: None)
    )

    async def materials(runtime, kind, provider, state):
        # Fixture-only file acquisition; actual child/cgroup IPC has separate
        # tests. There is no production path for injecting this callable.
        from vuoro_reconciler.protected_runtime_reader_child import _FIELDS

        module._need(module.load_runtime_profile(runtime.profile_id) == runtime)
        material = []
        for field, maximum, private in _FIELDS[kind]:
            path = runtime.data[kind][field]
            if field == "credential_ref":
                path = path[5:]
            material.append(_owned_file(path, maximum, private=private))
        module._need(module.load_runtime_profile(runtime.profile_id) == runtime)
        return tuple(material)

    monkeypatch.setattr(module, "_acquire_materials", materials)
    return result


def test_sealed_memfd_has_private_mode_and_cannot_change_or_leak_to_exec():
    descriptor = module._sealed(b"disposable test material")
    try:
        assert os.fstat(descriptor).st_mode & 0o777 == 0o600
        assert not os.get_inheritable(descriptor)
        assert (
            fcntl.fcntl(descriptor, module._F_GET_SEALS) & module._SEALS
            == module._SEALS
        )
        with pytest.raises(PermissionError):
            os.write(descriptor, b"changed")
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("kind", ["broker_read", "native_read"])
def test_acquire_is_fresh_and_context_contains_only_exact_roots(runtime, kind):
    reader = module.InstalledReadCredentials("disposable", kind)
    profile = runtime.read_profile(kind)
    first = asyncio.run(reader.acquire(profile))
    second = asyncio.run(reader.acquire(profile))
    assert first.bearer_ascii == second.bearer_ascii == TOKEN
    assert first.ssl_context is not second.ssl_context
    assert first.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert len(first.ssl_context.get_ca_certs(binary_form=True)) == 1
    assert TOKEN not in repr(first)


def test_real_loopback_mutual_tls_accepts_installed_broker_identity(
    runtime, certificates
):
    async def run():
        async with Loopback(certificates) as server:
            value = copy.deepcopy(runtime.data)
            value["broker_read"]["https_origin"] = f"https://localhost:{server.port}"
            chosen = RuntimeProfile("disposable", canonical(value))
            old = module.load_runtime_profile
            module.load_runtime_profile = lambda selected: chosen
            try:
                reader = module.InstalledReadCredentials("disposable", "broker_read")
                assert (
                    await _read_wire(
                        chosen.read_profile("broker_read"), reader, _COPY_PATH, b"{}"
                    )
                    == b"{}"
                )
                assert len(server.requests) == 1
                assert server.requests[0][2] is not None
            finally:
                module.load_runtime_profile = old

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation", ["expired", "naive", "delegated", "duplicate", "token-control"]
)
def test_native_session_refuses_without_secret_exception_state(runtime, mutation):
    path = Path(runtime.data["broker_read"]["session_path"])
    session = json.loads(path.read_bytes())
    if mutation == "expired":
        session["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif mutation == "naive":
        session["expires_at"] = "2099-01-01T00:00:00"
    elif mutation == "delegated":
        session["parent_session_id"] = "ses_parent"
    elif mutation == "token-control":
        session["token"] = TOKEN + "\r\n"
    raw = json.dumps(session)
    if mutation == "duplicate":
        raw = raw[:-1] + ',"token":"different"}'
    path.write_text(raw)
    reader = module.InstalledReadCredentials("disposable", "broker_read")
    with pytest.raises(ProtectedReadRefused) as caught:
        asyncio.run(reader.acquire(runtime.read_profile("broker_read")))
    assert str(caught.value) == "protected-read-refused"
    assert caught.value.__context__ is caught.value.__cause__ is None


def test_runtime_drift_refuses_before_private_key_context_or_wire(runtime, monkeypatch):
    reader = module.InstalledReadCredentials("disposable", "broker_read")
    altered = copy.deepcopy(runtime.data)
    altered["namespace"]["repo_id"] = "other"
    changed = RuntimeProfile("disposable", canonical(altered))
    monkeypatch.setattr(module, "load_runtime_profile", lambda selected: changed)
    monkeypatch.setattr(
        module, "_context", lambda *args, **kw: pytest.fail("must not load private key")
    )
    with pytest.raises(ProtectedReadRefused):
        asyncio.run(reader.acquire(runtime.read_profile("broker_read")))


def test_native_credential_strips_exactly_one_newline_and_reloads(runtime):
    reader = module.InstalledReadCredentials("disposable", "native_read")
    profile = runtime.read_profile("native_read")
    path = Path(runtime.data["native_read"]["credential_ref"][5:])
    path.write_text("new.disposable-token\n")
    assert asyncio.run(reader.acquire(profile)).bearer_ascii == "new.disposable-token"
    path.write_text(TOKEN + "\n\n")
    with pytest.raises(ProtectedReadRefused):
        asyncio.run(reader.acquire(profile))


def test_sealed_key_descriptors_closed_when_openssl_refuses(runtime, monkeypatch):
    seen = []
    original = module._sealed

    def remember(raw):
        fd = original(raw)
        seen.append(fd)
        return fd

    monkeypatch.setattr(module, "_sealed", remember)
    Path(runtime.data["broker_read"]["private_key_path"]).write_text(
        "invalid private key fixture"
    )
    reader = module.InstalledReadCredentials("disposable", "broker_read")
    with pytest.raises(ProtectedReadRefused):
        asyncio.run(reader.acquire(runtime.read_profile("broker_read")))
    assert len(seen) == 2
    for fd in seen:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_certificate_close_error_still_attempts_private_key_close(certificates, monkeypatch):
    originals = module._sealed, os.close
    held=[]
    attempted=[]
    def sealed(raw):
        descriptor=originals[0](raw)
        held.append(descriptor)
        return descriptor
    def close(descriptor):
        attempted.append(descriptor)
        if held and descriptor == held[0]:
            raise OSError('disposable first memfd close fault')
        return originals[1](descriptor)
    monkeypatch.setattr(module,'_sealed',sealed)
    monkeypatch.setattr(os,'close',close)
    materials=((certificates/'ca.pem').read_bytes(),b'fixture-session',(certificates/'client.pem').read_bytes(),(certificates/'client.key').read_bytes())
    try:
        with pytest.raises(OSError):
            module._context(materials,client=True)
        assert len(held)==2
        assert attempted == held
        assert os.fstat(held[0])
        with pytest.raises(OSError):
            os.fstat(held[1])
    finally:
        for descriptor in held:
            try:
                originals[1](descriptor)
            except OSError:
                pass
