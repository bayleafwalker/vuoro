"""Explicit installed local provider metadata; never an execution grant."""

from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass
import ipaddress
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
    _integer,
    _need,
    _read,
    _text,
)
from .protected_git import GitArtifactProfile, _branch
from .protected_read_transport import _origin

_PROFILE_FIELDS = (
    "schema effect_repository broker_repository_id owner name repository_id base_branch "
    "https_origin numeric_ip ca_der_b64 artifact_root signing_profile_utf8_b64 "
    "public_key_utf8_b64 credctl_path credctl_sha256 python_path python_sha256 "
    "git_path git_sha256 gpg_path gpg_sha256 module_sha256 cgroup_root runtime_profile_sha256"
)
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_LOCAL_FILESYSTEMS = {"btrfs", "ext4", "xfs"}


class ProviderRefused(ValueError):
    """Fixed diagnostic, containing no credential or upstream content."""


def _decode(value, maximum):
    _need(type(value) is str and len(value) <= ((maximum + 2) // 3) * 4)
    raw = base64.b64decode(value, validate=True)
    _need(0 < len(raw) <= maximum and base64.b64encode(raw).decode("ascii") == value)
    return raw


def _filesystem(fd):
    # Linux statfs starts with a native long filesystem magic. Oversized storage
    # avoids depending on the remainder of this platform's structure layout.
    storage = ctypes.create_string_buffer(512)
    library = ctypes.CDLL(None, use_errno=True)
    _need(library.fstatfs(ctypes.c_int(fd), ctypes.byref(storage)) == 0)
    magic = ctypes.c_long.from_buffer(storage).value & 0xFFFFFFFF
    info = Path("/proc/self/fdinfo/" + str(fd)).read_text()
    _need(len(info) <= 16384)
    matches = re.findall(r"^mnt_id:\s*([0-9]+)$", info, re.MULTILINE)
    _need(len(matches) == 1)
    mounted = Path("/proc/self/mountinfo").read_text()
    _need(len(mounted) <= 2 * 1024 * 1024)
    types = []
    for line in mounted.splitlines():
        left, separator, right = line.partition(" - ")
        if separator and left.split()[0] == matches[0]:
            types.append(right.split()[0])
    _need(len(types) == 1)
    expected = {"btrfs": 0x9123683E, "ext4": 0xEF53, "xfs": 0x58465342}
    if types[0] in expected:
        _need(magic == expected[types[0]])
    return types[0]


def _path_state(path):
    path = Path(path)
    _need(path.is_absolute() and ".." not in path.parts)
    states = []
    for part in reversed((path, *path.parents)):
        info = os.lstat(part)
        _need(not stat.S_ISLNK(info.st_mode))
        states.append((str(part), info.st_dev, info.st_ino))
    return tuple(states)


def _owned(info, mode, directory=False):
    _need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == mode)
    _need(
        stat.S_ISDIR(info.st_mode)
        if directory
        else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    )


def _registry_path():
    return (
        Path(pwd.getpwuid(os.getuid()).pw_dir)
        / ".local/state/vuoro-reconciler/installed-registry.json"
    )


def _host_id():
    return _hash(Path("/etc/machine-id").read_bytes())


def _source_pins():
    directory = Path(__file__).resolve().parent
    return {
        str(path.relative_to(directory)): _hash(path.read_bytes())
        for path in sorted(directory.rglob("*.py"))
    }


def _check_executable(path, expected):
    _need(
        type(path) is str and Path(path).is_absolute() and ".." not in Path(path).parts
    )
    _digest(expected)
    resolved = Path(path).resolve(strict=True)
    before = resolved.stat()
    _need(stat.S_ISREG(before.st_mode) and before.st_mode & 0o111)
    _need(not before.st_mode & 0o022)
    with resolved.open("rb") as source:
        opened = os.fstat(source.fileno())
        _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino))
        _need(_hash(source.read()) == expected)
        after = os.fstat(source.fileno())
    _need(
        (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    )
    _need(resolved == Path(path).resolve(strict=True))


@dataclass(frozen=True, slots=True, repr=False)
class InstalledProviderProfile:
    profile_id: str
    registry_sha256: str
    profile_sha256: str
    profile_utf8: bytes

    @property
    def data(self):
        return _read(self.profile_utf8, 65536)

    @property
    def signing(self):
        value = self.data
        return GitArtifactProfile(
            _decode(value["signing_profile_utf8_b64"], 16384),
            _decode(value["public_key_utf8_b64"], 65536),
        )


@dataclass(frozen=True, slots=True, repr=False)
class InstalledRegistrySnapshot:
    profile: InstalledProviderProfile
    registry_utf8: bytes
    host_id: str


def load_installed_registry_snapshot(profile_id):
    """Return profile and whole registry DATA from one admitted descriptor."""
    return _load_installed_snapshot(profile_id)


def load_installed_profile(profile_id):
    return _load_installed_snapshot(profile_id).profile


def _validate_cgroup_root(path):
    path = Path(path)
    _need(path.is_relative_to("/sys/fs/cgroup") and path != Path("/sys/fs/cgroup"))
    before = _path_state(path)
    _owned(os.lstat(path), 0o700, True)
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        _need(_filesystem(descriptor) == "cgroup2")
        _need((path / "cgroup.type").read_bytes() == b"domain\n")
        _need((path / "cgroup.procs").read_bytes() == b"")
        for name in ("cgroup.procs", "cgroup.kill"):
            info = os.lstat(path / name)
            _need(
                info.st_uid == os.getuid()
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
            )
            opened = os.open(
                name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor
            )
            try:
                _need(
                    (info.st_dev, info.st_ino)
                    == (os.fstat(opened).st_dev, os.fstat(opened).st_ino)
                )
            finally:
                os.close(opened)
        _need(_path_state(path) == before)
        return before
    finally:
        os.close(descriptor)


def _join_cgroup(descriptor, profile):
    _need(type(descriptor) is int and descriptor >= 3)
    path = Path(os.readlink("/proc/self/fd/" + str(descriptor)))
    root = Path(profile["cgroup_root"])
    _validate_cgroup_root(root)
    _need(path.name == "cgroup.procs" and path.parent.parent == root)
    _need(re.fullmatch(r"operation-[0-9a-f]{32}", path.parent.name))
    _owned(os.lstat(path.parent), 0o700, True)
    _need(_filesystem(descriptor) == "cgroup2")
    info = os.fstat(descriptor)
    named = os.lstat(path)
    _need(
        info.st_uid == os.getuid()
        and stat.S_ISREG(info.st_mode)
        and (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino)
    )
    _need(os.write(descriptor, b"0") == 1)
    os.close(descriptor)
    _admit_current_cgroup(profile)


def _admit_current_cgroup(profile):
    raw = Path("/proc/self/cgroup").read_text()
    _need(len(raw) <= 8192 and len(raw.splitlines()) == 1 and raw.startswith("0::/"))
    current = Path("/sys/fs/cgroup") / raw.strip()[3:].lstrip("/")
    root = Path(profile["cgroup_root"])
    _need(
        current.parent == root and re.fullmatch(r"operation-[0-9a-f]{32}", current.name)
    )
    _owned(os.lstat(current), 0o700, True)
    _need(_path_state(current))


def _validate_profile(value):
    _closed(value, _PROFILE_FIELDS)
    _digest(value["runtime_profile_sha256"])
    _validate_cgroup_root(value["cgroup_root"])
    _need(value["schema"] == "vuoro-protected-provider-profile/v1")
    _text(value["effect_repository"], 200)
    for field in ("owner", "name"):
        _need(type(value[field]) is str and _NAME.fullmatch(value[field]))
    _need(
        value["broker_repository_id"]
        == "forgejo:" + value["owner"] + "/" + value["name"]
    )
    _integer(value["repository_id"])
    _branch(value["base_branch"])
    _origin(value["https_origin"])
    _need(
        type(value["numeric_ip"]) is str
        and str(ipaddress.ip_address(value["numeric_ip"])) == value["numeric_ip"]
    )
    roots = value["ca_der_b64"]
    _need(type(roots) is list and 1 <= len(roots) <= 8 and roots == sorted(set(roots)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for root in roots:
        context.load_verify_locations(
            cadata=ssl.DER_cert_to_PEM_cert(_decode(root, 32768))
        )
    _need(
        sorted(context.get_ca_certs(binary_form=True))
        == sorted(_decode(v, 32768) for v in roots)
    )
    _path_state(value["artifact_root"])
    _owned(os.lstat(value["artifact_root"]), 0o700, True)
    descriptor = os.open(
        value["artifact_root"],
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        _need(_filesystem(descriptor) in _LOCAL_FILESYSTEMS)
    finally:
        os.close(descriptor)
    signing = GitArtifactProfile(
        _decode(value["signing_profile_utf8_b64"], 16384),
        _decode(value["public_key_utf8_b64"], 65536),
    )
    _need(
        _read(signing.signing_profile_utf8)["repository"] == value["effect_repository"]
    )
    _need(value["credctl_path"] == "/run/current-system/sw/bin/credctl")
    for field in ("credctl", "python", "git", "gpg"):
        _check_executable(value[field + "_path"], value[field + "_sha256"])
    _need(
        type(value["module_sha256"]) is dict
        and value["module_sha256"] == _source_pins()
    )


def _load_installed_snapshot(profile_id):
    """Refuse absent/drifted installation; ignore all environment path selectors."""
    failed = False
    try:
        _need(type(profile_id) is str and _PROFILE_ID.fullmatch(profile_id))
        # Lifecycle admission requires Linux pidfds before any credential mint.
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
        path = _registry_path()
        ancestry = _path_state(path)
        _owned(os.lstat(path.parent), 0o700, True)
        before = os.lstat(path)
        _owned(before, 0o600)
        _need(0 < before.st_size <= 65536)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(descriptor)
            _owned(opened, 0o600)
            _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino))
            _need(_filesystem(descriptor) in _LOCAL_FILESYSTEMS)
            raw = os.read(descriptor, 65537)
            _need(len(raw) == before.st_size and os.read(descriptor, 1) == b"")
            value = _read(raw, 65536)
            _closed(value, "schema host_id profiles")
            _need(
                value["schema"] == "vuoro-protected-provider-registry/v1"
                and value["host_id"] == _host_id()
            )
            profiles = value["profiles"]
            _need(type(profiles) is dict and 1 <= len(profiles) <= 16)
            hashes, logical, physical = set(), set(), set()
            for key, registered in profiles.items():
                _need(_PROFILE_ID.fullmatch(key))
                _closed(registered, _PROFILE_FIELDS)
                digest = _hash(_canonical(registered))
                _text(registered["effect_repository"], 200)
                _text(registered["broker_repository_id"], 200)
                hostname, port, _ = _origin(registered["https_origin"])
                try:
                    hostname = str(ipaddress.ip_address(hostname))
                except ValueError:
                    pass
                target = (hostname, port, registered["broker_repository_id"])
                _need(
                    digest not in hashes
                    and registered["effect_repository"] not in logical
                    and target not in physical
                )
                hashes.add(digest)
                logical.add(registered["effect_repository"])
                physical.add(target)
            _need(profile_id in profiles)
            selected = profiles[profile_id]
            for registered in profiles.values():
                _validate_profile(registered)
            _check_state_install(profile_id)
            _validate_state_store(profiles, _hash(raw))
            after = os.fstat(descriptor)
            _need(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                == (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
            )
            _need(_path_state(path) == ancestry)
            named = os.lstat(path)
            held = os.fstat(descriptor)
            _owned(named, 0o600)
            _owned(held, 0o600)
            _need(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                == (
                    held.st_dev,
                    held.st_ino,
                    held.st_size,
                    held.st_mtime_ns,
                    held.st_ctime_ns,
                )
                == (
                    named.st_dev,
                    named.st_ino,
                    named.st_size,
                    named.st_mtime_ns,
                    named.st_ctime_ns,
                )
            )
            _owned(os.lstat(path.parent), 0o700, True)
            profile = InstalledProviderProfile(
                profile_id,
                _hash(raw),
                _hash(_canonical(selected)),
                _canonical(selected),
            )
            return InstalledRegistrySnapshot(profile, raw, value["host_id"])
        finally:
            os.close(descriptor)
    except Exception:
        failed = True
    if failed:
        raise ProviderRefused("protected-provider-registry-invalid")


def _state_paths(profile_id):
    _need(type(profile_id) is str and _PROFILE_ID.fullmatch(profile_id))
    directory = _registry_path().parent / "operation-state"
    return directory, directory / (profile_id + ".lock"), profile_id + ".active"


def _check_state_install(profile_id):
    directory, lock, _ = _state_paths(profile_id)
    before = _path_state(lock)
    directory_info = os.lstat(directory)
    lock_info = os.lstat(lock)
    _owned(directory_info, 0o700, True)
    _owned(lock_info, 0o600)
    opened = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _owned(os.fstat(opened), 0o600)
        _need(_filesystem(opened) in _LOCAL_FILESYSTEMS)
        _need(
            (os.fstat(opened).st_dev, os.fstat(opened).st_ino)
            == (os.lstat(lock).st_dev, os.lstat(lock).st_ino)
        )
        _need(_path_state(lock) == before)
        _owned(os.lstat(directory), 0o700, True)
        _owned(os.lstat(lock), 0o600)
        return (
            before,
            (directory_info.st_dev, directory_info.st_ino),
            (lock_info.st_dev, lock_info.st_ino),
        )
    finally:
        os.close(opened)


def _active_record(profile, operation_id):
    _need(type(operation_id) is str and re.fullmatch(r"[0-9a-f]{32}", operation_id))
    return _canonical(
        {
            "schema": "vuoro-protected-operation-active/v1",
            "profile_id": profile.profile_id,
            "registry_sha256": profile.registry_sha256,
            "profile_sha256": profile.profile_sha256,
            "operation_id": operation_id,
        }
    )


def _validate_active(profile):
    _admit_current_cgroup(profile.data)
    line = Path("/proc/self/cgroup").read_text().strip()
    operation_id = line.rsplit("/operation-", 1)[1]
    directory, _, name = _state_paths(profile.profile_id)
    before = _path_state(directory / name)
    _owned(os.lstat(directory), 0o700, True)
    descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        _owned(info, 0o600)
        _need((info.st_dev, info.st_ino) == before[-1][1:])
        _need(0 < info.st_size <= 4096)
        _need(_filesystem(descriptor) in _LOCAL_FILESYSTEMS)
        raw = os.read(descriptor, 4097)
        _need(
            raw == _active_record(profile, operation_id)
            and os.read(descriptor, 1) == b""
        )
        after = os.fstat(descriptor)
        _need(
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        )
        _need(_path_state(directory / name) == before)
        _owned(os.lstat(directory), 0o700, True)
        current = os.lstat(directory / name)
        _owned(current, 0o600)
        _need((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))
    finally:
        os.close(descriptor)


def _metadata_state(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _bounded_state_names(descriptor):
    names = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            _need(len(names) < 64)
            names.append(entry.name)
    return tuple(sorted(names))


def _validate_state_store(profiles, registry_sha256):
    directory, _, _ = _state_paths(next(iter(profiles)))
    before = _path_state(directory)
    _owned(os.lstat(directory), 0o700, True)
    descriptor = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        _owned(os.fstat(descriptor), 0o700, True)
        _need(
            (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) == before[-1][1:]
        )
        _need(_filesystem(descriptor) in _LOCAL_FILESYSTEMS)
        directory_state = _metadata_state(os.fstat(descriptor))
        names = _bounded_state_names(descriptor)
        entry_states = {}
        for name in names:
            _need(not name.endswith(".quarantine"))
            if name.endswith(".lock"):
                # A retired, well-formed owned lock has no active authority.
                _need(_PROFILE_ID.fullmatch(name[:-5]))
                active = False
            else:
                _need(name.endswith(".active") and name[:-7] in profiles)
                active = True
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _owned(info, 0o600)
            entry_states[name] = _metadata_state(info)
            opened = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor
            )
            try:
                current = os.fstat(opened)
                _owned(current, 0o600)
                _need((info.st_dev, info.st_ino) == (current.st_dev, current.st_ino))
                if active:
                    _need(0 < current.st_size <= 4096)
                    raw = os.read(opened, 4097)
                    value = _read(raw, 4096)
                    _closed(
                        value,
                        "schema profile_id registry_sha256 profile_sha256 operation_id",
                    )
                    profile_id = name[:-7]
                    profile_utf8 = _canonical(profiles[profile_id])
                    profile = InstalledProviderProfile(
                        profile_id, registry_sha256, _hash(profile_utf8), profile_utf8
                    )
                    _need(
                        raw == _active_record(profile, value["operation_id"])
                        and os.read(opened, 1) == b""
                    )
                after = os.fstat(opened)
                path = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                _owned(path, 0o600)
                _need(
                    (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    )
                    == (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    == (
                        path.st_dev,
                        path.st_ino,
                        path.st_size,
                        path.st_mtime_ns,
                        path.st_ctime_ns,
                    )
                )
            finally:
                os.close(opened)
        _need(_bounded_state_names(descriptor) == names)
        for name, admitted in entry_states.items():
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _owned(current, 0o600)
            _need(_metadata_state(current) == admitted)
        _need(
            directory_state
            == _metadata_state(os.fstat(descriptor))
            == _metadata_state(os.lstat(directory))
        )
        _need(_path_state(directory) == before)
        _owned(os.lstat(directory), 0o700, True)
        _owned(os.fstat(descriptor), 0o700, True)
        _need(
            (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)
            == (os.lstat(directory).st_dev, os.lstat(directory).st_ino)
        )
    finally:
        os.close(descriptor)
