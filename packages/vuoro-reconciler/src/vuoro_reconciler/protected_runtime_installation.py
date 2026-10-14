"""Constructor admission for a fixed installed runtime; never installs or grants."""

from dataclasses import dataclass
import errno
import hashlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys

from . import protected_git
from .protected_copy_codec import _hash, _need


class InstallationRefused(ValueError):
    """Fixed diagnostic with no imported paths or upstream diagnostics."""


@dataclass(frozen=True, slots=True, repr=False)
class InstallationAdmission:
    profile_id: str
    registry_sha256: str
    profile_sha256: str
    package_root: str
    interpreter_library_paths: tuple[str, ...]
    git_helper_path: str
    # The library/helper paths have immutable ancestry. Their transitive
    # contents are not claimed to be covered by the registered binary hashes.


def _state(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _immutable(info):
    # No other trusted UID is registered in the current profile schema.
    # Root ownership is sufficient; UID-owned chmod(0555) is not immutable.
    _need(info.st_uid == 0)
    if not stat.S_ISLNK(info.st_mode):
        _need(not info.st_mode & 0o022)


def _chain(path):
    path = Path(path)
    _need(path.is_absolute() and ".." not in path.parts)
    current = Path("/")
    queue = list(path.parts[1:])
    states = {}
    links = 0
    while True:
        info = os.lstat(current)
        _immutable(info)
        link = os.readlink(current) if stat.S_ISLNK(info.st_mode) else None
        state = (_state(info), link)
        if str(current) in states:
            _need(states[str(current)] == state)
        states[str(current)] = state
        if link is not None:
            links += 1
            _need(links <= 40)
            target = Path(link)
            current = Path("/") if target.is_absolute() else current.parent
            queue = (
                list(target.parts[1:] if target.is_absolute() else target.parts) + queue
            )
        elif not queue:
            break
        else:
            _need(stat.S_ISDIR(info.st_mode))
            part = queue.pop(0)
            if part == "..":
                current = current.parent
            elif part != ".":
                current /= part
    _need(current == path.resolve(strict=True))
    return current, states


def _recheck(states):
    for path, expected in states.items():
        info = os.lstat(path)
        _immutable(info)
        link = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
        _need((_state(info), link) == expected)


def _executable_privileges(descriptor, info):
    # Linux executable metadata must not add child authority absent in this
    # admitted non-root parent. Unsupported/unreadable xattrs are not absence.
    _need(sys.platform == "linux")
    _need(not info.st_mode & (stat.S_ISUID | stat.S_ISGID))
    try:
        capability = os.getxattr(descriptor, "security.capability")
    except OSError as error:
        _need(error.errno == errno.ENODATA)
    else:
        _need(capability == b"")


def _file(path, expected=None, *, executable=False):
    resolved, states = _chain(path)
    before = os.lstat(resolved)
    _need(stat.S_ISREG(before.st_mode))
    if executable:
        _need(before.st_mode & 0o111)
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _need(_state(os.fstat(descriptor)) == _state(before))
        if executable:
            _executable_privileges(descriptor, os.fstat(descriptor))
        digest = hashlib.sha256()
        while raw := os.read(descriptor, 65536):
            digest.update(raw)
        _need(_state(os.fstat(descriptor)) == _state(before))
        if executable:
            _executable_privileges(descriptor, os.fstat(descriptor))
        if expected is not None:
            _need("sha256:" + digest.hexdigest() == expected)
        _recheck(states)
        return resolved, states
    finally:
        os.close(descriptor)


def _directory(path):
    resolved, states = _chain(path)
    before = os.lstat(resolved)
    _need(stat.S_ISDIR(before.st_mode))
    descriptor = os.open(
        resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        _need(_state(os.fstat(descriptor)) == _state(before))
        _recheck(states)
        return resolved, states
    finally:
        os.close(descriptor)


def _unprivileged():
    _need(os.getuid() != 0 and os.geteuid() == os.getuid())
    _need(os.getgid() == os.getegid())
    # A non-root UID with ownership/DAC capabilities can still mutate a
    # nominally root-owned closure. Fail closed on all effective/permitted/
    # inheritable/ambient capability sets; no host grant is inferred from UID.
    fields = {}
    with open("/proc/self/status", encoding="ascii") as status:
        for line in status:
            if line.startswith(("CapEff:", "CapPrm:", "CapInh:", "CapAmb:")):
                key, value = line.split()
                fields[key] = int(value, 16)
    _need(set(fields) == {"CapEff:", "CapPrm:", "CapInh:", "CapAmb:"})
    _need(not any(fields.values()))


def _isolated():
    return sys.flags.isolated == 1


def _package():
    import vuoro_reconciler

    paths = list(vuoro_reconciler.__path__)
    _need(len(paths) == 1)
    return Path(paths[0])


def _loaded_origins():
    result = []
    for name, module in tuple(sys.modules.items()):
        if name == "vuoro_reconciler" or name.startswith("vuoro_reconciler."):
            _need(module is not None)
            filename = getattr(module, "__file__", None)
            spec = getattr(module, "__spec__", None)
            _need(type(filename) is str and spec is not None)
            _need(type(spec.origin) is str)
            _need(type(spec.loader) is importlib.machinery.SourceFileLoader)
            _need(
                Path(filename).resolve(strict=True)
                == Path(spec.origin).resolve(strict=True)
            )
            cached = getattr(module, "__cached__", None)
            _need(
                cached is None or cached == importlib.util.cache_from_source(filename)
            )
            result.append(filename)
    return result


def _source_only_package(root):
    """Source-only cold -I installation; no alternate bytecode cache locator.

    Root-owned immutable ancestry and a nonprivileged interpreter mean it
    cannot create caches in this tree. Installation must use --no-compile.
    Standard-library caches outside this package retain the explicitly weaker
    immutable-origin evidence, not the package's approved source SHA proof.
    """
    _need(sys.pycache_prefix is None)
    census = {}
    forbidden = (".pyc", ".pyo", ".zip", ".so", ".pyd", ".dll", ".dylib")
    binary = (b"\x7fELF", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    for path in sorted(root.rglob("*")):
        _need(not path.name.lower().endswith(forbidden))
        if path.is_dir():
            resolved, _ = _directory(path)
        else:
            resolved, _ = _file(path)
            with resolved.open("rb") as source:
                magic = source.read(4)
            _need(magic not in binary and not magic.startswith(b"MZ"))
            _need(magic != importlib.util.MAGIC_NUMBER)
        _need(resolved.is_relative_to(root))
        census[str(path.relative_to(root))] = _state(os.lstat(path))
    return census


def _library_paths():
    return tuple(sys.path)


def _interpreter_modules():
    # Include already loaded Python/native extension module files, not only
    # the search directories. This is immutable-origin evidence; ELF loader
    # dependency content is not asserted to share their SHA or the Python SHA.
    paths = set()
    for name, imported in tuple(sys.modules.items()):
        if name == "vuoro_reconciler" or name.startswith("vuoro_reconciler."):
            continue
        filename = getattr(imported, "__file__", None)
        if filename is not None:
            _need(type(filename) is str and Path(filename).is_absolute())
            paths.add(filename)
    return tuple(sorted(paths))


def _helper(git):
    # Ask the already hash-checked immutable Git for its compiled helper root.
    # No ambient GIT_EXEC_PATH, HOME config or caller executable is propagated.
    result = subprocess.run(
        [str(git), "--exec-path"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={
            "PATH": os.defpath,
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        timeout=3,
        check=False,
    )
    _need(result.returncode == 0 and 0 < len(result.stdout) <= 4096)
    _need(result.stdout.endswith(b"\n") and result.stdout.count(b"\n") == 1)
    return Path(result.stdout[:-1].decode("utf8"))


def admit_installation(provider):
    """Fixed registered fields only; constructor work, outside read deadlines.

    The trusted factory rechecks installed registry/profile admission. This
    function verifies the current interpreter/import closure and binary pins;
    it cannot turn a caller-supplied profile into execution authority.
    """
    try:
        _unprivileged()
        _need(_isolated())
        data = provider.data
        _need(_hash(provider.profile_utf8) == provider.profile_sha256)
        states = []
        python, pins = _file(
            data["python_path"], data["python_sha256"], executable=True
        )
        states.append(pins)
        actual_python, pins = _file(
            sys.executable, data["python_sha256"], executable=True
        )
        states.append(pins)
        _need(actual_python == python)
        _, pins = _file(data["credctl_path"], data["credctl_sha256"], executable=True)
        states.append(pins)
        root, pins = _directory(_package())
        states.append(pins)
        code_census = _source_only_package(root)
        expected = data["module_sha256"]
        _need(type(expected) is dict and expected)
        observed = {}
        for source in sorted(root.rglob("*.py")):
            name = str(source.relative_to(root))
            _need(name in expected)
            resolved, pins = _file(source, expected[name])
            _need(resolved.is_relative_to(root))
            states.append(pins)
            observed[name] = expected[name]
        _need(observed == expected)
        for origin in _loaded_origins():
            path, pins = _file(origin)
            _need(path.is_relative_to(root) and path.suffix == ".py")
            _need(str(path.relative_to(root)) in expected)
            states.append(pins)
        libraries = []
        for path in _library_paths():
            _need(type(path) is str and path)
            item = Path(path)
            if not item.exists():
                # Python includes an optional standard-library zip path. Its
                # immutable parent prevents the execution UID creating it.
                _need(item.suffix == ".zip")
                _, pins = _directory(item.parent)
                libraries.append(str(item))
            elif item.is_dir():
                resolved, pins = _directory(item)
                libraries.append(str(resolved))
            else:
                resolved, pins = _file(item)
                libraries.append(str(resolved))
            states.append(pins)
        for path in _interpreter_modules():
            resolved, pins = _file(path)
            states.append(pins)
            libraries.append(str(resolved))
        binaries = {}
        for field, frozen in (("git", protected_git._GIT), ("gpg", protected_git._GPG)):
            registered, pins = _file(
                data[field + "_path"], data[field + "_sha256"], executable=True
            )
            states.append(pins)
            _need(frozen is not None)
            loaded, pins = _file(frozen, data[field + "_sha256"], executable=True)
            states.append(pins)
            _need(registered == loaded)
            binaries[field] = registered
        helper, pins = _directory(_helper(binaries["git"]))
        states.append(pins)
        for pins in states:
            _recheck(pins)
        _need(
            {str(path.relative_to(root)) for path in root.rglob("*.py")}
            == set(observed)
        )
        _need(_source_only_package(root) == code_census)
        return InstallationAdmission(
            provider.profile_id,
            provider.registry_sha256,
            provider.profile_sha256,
            str(root),
            tuple(libraries),
            str(helper),
        )
    except Exception:
        pass
    raise InstallationRefused("protected-runtime-installation-invalid")
