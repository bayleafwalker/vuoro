"""Fixed installed runtime composition metadata; this module grants nothing."""

from dataclasses import dataclass
from functools import wraps
import os
from pathlib import Path
import pwd
import re
import ssl
import stat

from .protected_copy_codec import (
    _canonical,
    _closed,
    _digest,
    _hash,
    _namespace_bytes,
    _need,
    _read,
)
from .protected_read_transport import ReadoutProfile

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_MAX = 65536


class RuntimeRefused(ValueError):
    """A fixed diagnostic without credential data or upstream exception state."""


def _safe(function):
    @wraps(function)
    def run(*args, **kwargs):
        failed = False
        try:
            return function(*args, **kwargs)
        except Exception:
            failed = True
        if failed:
            raise RuntimeRefused("protected-runtime-invalid")

    return run


def _absolute(value):
    _need(type(value) is str and 0 < len(value) <= 4096)
    _need(not any(ord(char) < 32 or ord(char) == 127 for char in value))
    path = Path(value)
    _need(path.is_absolute() and ".." not in path.parts and str(path) == value)
    return path


def _ancestry(path):
    states = []
    for component in reversed((path, *path.parents)):
        info = os.lstat(component)
        _need(not stat.S_ISLNK(info.st_mode))
        states.append((str(component), info.st_dev, info.st_ino))
    return tuple(states)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _owned_file(path, maximum, *, private, read=True, admit_local=False):
    """Read through a joined descriptor; callers choose only frozen selectors."""
    path = _absolute(path)
    ancestry = _ancestry(path)
    before = os.lstat(path)
    _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
    _need(before.st_uid == os.getuid())
    _need(stat.S_IMODE(before.st_mode) in ({0o600} if private else {0o600, 0o644}))
    _need(0 < before.st_size <= maximum)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _need(_identity(os.fstat(descriptor)) == _identity(before))
        if admit_local:
            from .protected_provider_registry import _filesystem, _LOCAL_FILESYSTEMS

            _need(_filesystem(descriptor) in _LOCAL_FILESYSTEMS)
        raw = os.read(descriptor, maximum + 1) if read else None
        if read:
            _need(len(raw) == before.st_size and os.read(descriptor, 1) == b"")
        _need(_identity(os.fstat(descriptor)) == _identity(before))
        _need(
            _ancestry(path) == ancestry
            and _identity(os.lstat(path)) == _identity(before)
        )
        return raw
    finally:
        os.close(descriptor)


def _directory(value, *, private):
    path = _absolute(value)
    _ancestry(path)
    info = os.lstat(path)
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid())
    _need(stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022)
    return path


def _read_profile(value, namespace, repository):
    result = ReadoutProfile(
        namespace,
        repository,
        value["https_origin"],
        value["connect_ip"],
        tuple(value["ca_der_sha256"]),
    )
    _need(type(value["ca_der_sha256"]) is list)
    raw = _owned_file(value["server_ca_path"], 262144, private=False)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=raw.decode("ascii"))
    actual = tuple(
        sorted(_hash(cert) for cert in context.get_ca_certs(binary_form=True))
    )
    _need(actual == tuple(sorted(result.ca_der_sha256)))
    return result


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeProfile:
    profile_id: str
    profile_utf8: bytes

    @_safe
    def __post_init__(self):
        _need(type(self.profile_id) is str and _ID.fullmatch(self.profile_id))
        value = _read(self.profile_utf8, _MAX)
        _closed(
            value,
            "schema profile_id effect_repository namespace broker_read native_read local_base signer",
        )
        _need(value["schema"] == "vuoro-protected-runtime-profile/v1")
        _need(value["profile_id"] == self.profile_id)
        namespace = _canonical(value["namespace"])
        _namespace_bytes(namespace)
        broker, native = value["broker_read"], value["native_read"]
        _closed(
            broker,
            "https_origin connect_ip ca_der_sha256 server_ca_path client_certificate_path private_key_path session_path",
        )
        _closed(
            native,
            "https_origin connect_ip ca_der_sha256 server_ca_path credential_ref",
        )
        for item in (broker, native):
            _read_profile(item, namespace, value["effect_repository"])
        _owned_file(
            broker["client_certificate_path"], 262144, private=False, read=False
        )
        _owned_file(broker["private_key_path"], 262144, private=True, read=False)
        _owned_file(broker["session_path"], 65536, private=True, read=False)
        reference = native["credential_ref"]
        _need(type(reference) is str and reference.startswith("file:"))
        _owned_file(reference[5:], 16384, private=True, read=False)
        local, signer = value["local_base"], value["signer"]
        _closed(local, "kind git_directory")
        _need(local["kind"] == "local-git-objects/v1")
        _directory(local["git_directory"], private=False)
        _closed(signer, "kind gnupg_home")
        _need(signer["kind"] == "local-openpgp-agent/v1")
        _directory(signer["gnupg_home"], private=True)

    @property
    def data(self):
        return _read(self.profile_utf8, _MAX)

    @property
    def profile_sha256(self):
        return _hash(self.profile_utf8)

    @property
    def namespace_utf8(self):
        return _canonical(self.data["namespace"])

    @_safe
    def read_profile(self, kind):
        _need(kind in {"broker_read", "native_read"})
        value = self.data
        return _read_profile(
            value[kind], self.namespace_utf8, value["effect_repository"]
        )


def _root():
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/vuoro-reconciler"


def _provider_profile(profile_id):
    from .protected_provider_registry import load_installed_profile

    return load_installed_profile(profile_id)


@_safe
def load_runtime_profile(profile_id):
    """Load fixed installed bytes bound by the selected provider profile."""
    _need(type(profile_id) is str and _ID.fullmatch(profile_id))
    provider = _provider_profile(profile_id)
    expected = provider.data["runtime_profile_sha256"]
    _digest(expected)
    path = _root() / "runtime-profiles" / (profile_id + ".json")
    initial_parent = os.lstat(path.parent)
    initial_ancestry = _ancestry(path.parent)
    _directory(str(path.parent), private=True)
    descriptor = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        parent = os.fstat(descriptor)
        _need(_identity(parent) == _identity(initial_parent))
        _need(_ancestry(path.parent) == initial_ancestry)
        _directory(str(path.parent), private=True)
        _need(_identity(parent) == _identity(os.lstat(path.parent)))
        ancestry = _ancestry(path.parent)
        before = os.lstat(path)
        raw = _owned_file(str(path), _MAX, private=True, admit_local=True)
        _need(_hash(raw) == expected)
        result = RuntimeProfile(profile_id, raw)
        _need(result.data["effect_repository"] == provider.data["effect_repository"])
        _need(_provider_profile(profile_id) == provider)
        _directory(str(path.parent), private=True)
        _need(_identity(os.fstat(descriptor)) == _identity(parent))
        _need(_identity(os.lstat(path.parent)) == _identity(parent))
        _need(_ancestry(path.parent) == ancestry)
        _need(_identity(os.lstat(path)) == _identity(before))
        return result
    finally:
        os.close(descriptor)
