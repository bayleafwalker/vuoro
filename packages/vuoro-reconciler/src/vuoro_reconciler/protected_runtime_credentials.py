"""Fresh installed private-reader credentials, retained only in process memory."""

import fcntl
import os
import ssl
import sys
from datetime import UTC, datetime

from .protected_copy_codec import _closed, _need, _read_json, _text
from .protected_read_transport import (
    ReadCredentials,
    ReadoutProfile,
    _credentials,
    _async_fixed,
)
from .protected_runtime_registry import load_runtime_profile

# Linux UAPI include/uapi/linux/fcntl.h: Python builds need not expose these
# names even when the kernel supports sealing. Admission is Linux-only and
# the resulting seal state is independently checked before use.
_F_ADD_SEALS, _F_GET_SEALS = 1033, 1034
_SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008


def _sealed(raw):
    _need(sys.platform == "linux")
    descriptor = os.memfd_create(
        "vuoro-private-reader", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    )
    try:
        os.fchmod(descriptor, 0o600)
        position = 0
        while position < len(raw):
            count = os.write(descriptor, raw[position:])
            _need(count > 0)
            position += count
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, _F_ADD_SEALS, _SEALS)
        _need(fcntl.fcntl(descriptor, _F_GET_SEALS) & _SEALS == _SEALS)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _context(materials, *, client):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    roots = materials[0]
    context.load_verify_locations(cadata=roots.decode("ascii"))
    context.set_alpn_protocols(["http/1.1"])
    if client:
        certificate = key = None
        try:
            certificate = _sealed(materials[2])
            key = _sealed(materials[3])

            def no_password():
                raise ValueError("protected-reader-encrypted-key-refused")

            context.load_cert_chain(
                "/proc/self/fd/" + str(certificate),
                "/proc/self/fd/" + str(key),
                password=no_password,
            )
        finally:
            close_error = None
            for descriptor in (certificate, key):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except BaseException as error:
                        if close_error is None:
                            close_error = error
            if close_error is not None:
                raise close_error
    return context


def _session(raw):
    session = _read_json(raw, 65536)
    # Host-native session files have exactly these five fields. A delegated
    # session's parent_session_id is refused rather than treated as authority.
    _closed(session, "session_id token expires_at subject_id host_id")
    for field in ("session_id", "subject_id", "host_id"):
        _text(session[field], 512)
    expiry = session["expires_at"]
    _need(type(expiry) is str and 0 < len(expiry) <= 128)
    deadline = datetime.fromisoformat(expiry)
    _need(deadline.tzinfo is not None and deadline.utcoffset() is not None)
    _need(deadline > datetime.now(UTC))
    return session["token"]


def _installed_provider(profile_id):
    from .protected_provider_registry import load_installed_profile

    return load_installed_profile(profile_id)


def _installed_state(provider):
    from .protected_provider import _InstalledOperationState

    return _InstalledOperationState(provider)


async def _acquire_materials(runtime, kind, provider, state):
    from .protected_runtime_reader_ipc import acquire_materials

    return await acquire_materials(runtime, kind, provider, state)


class InstalledReadCredentials:
    """Only an installed profile selects credential files or TLS identities."""

    def __init__(self, profile_id, kind):
        _need(kind in {"broker_read", "native_read"})
        self._runtime = load_runtime_profile(profile_id)
        self._provider = _installed_provider(profile_id)
        self._kind = kind
        self._profile = self._runtime.read_profile(kind)
        self._state = _installed_state(self._provider)

    def close(self):
        self._state.close()

    @_async_fixed
    async def acquire(self, profile):
        _need(type(profile) is ReadoutProfile and profile == self._profile)
        materials = await _acquire_materials(
            self._runtime, self._kind, self._provider, self._state
        )
        _need(type(materials) is tuple and all(type(raw) is bytes for raw in materials))
        _need(len(materials) == (4 if self._kind == "broker_read" else 2))
        if self._kind == "broker_read":
            token = _session(materials[1])
        else:
            raw = materials[1]
            # Match the existing native file credential contract exactly.
            if raw.endswith(b"\n"):
                raw = raw[:-1]
            token = raw.decode("utf8")
        context = _context(materials, client=self._kind == "broker_read")
        credential = _credentials(ReadCredentials(token, context), profile)
        return credential
