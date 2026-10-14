"""Installation refusal proofs and explicit mocked immutable metadata fixtures."""

import os
from pathlib import Path
import stat
from types import SimpleNamespace
from types import ModuleType
from importlib.machinery import ModuleSpec
import io
import errno
import importlib.machinery
import importlib.util
import py_compile

import pytest

from vuoro_reconciler import protected_runtime_installation as module
from vuoro_reconciler.protected_copy_codec import _canonical, _hash

_REAL_PACKAGE = module._package
_REAL_ORIGINS = module._loaded_origins


def test_actual_mutable_uid_owned_file_refuses_even_without_write_mode(tmp_path):
    path = tmp_path / "source.py"
    path.write_bytes(b"pass\n")
    path.chmod(0o444)
    assert os.lstat(path).st_uid == os.getuid() != 0
    with pytest.raises(ValueError):
        module._immutable(os.lstat(path))


def test_actual_mutable_project_ancestry_refuses(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    with pytest.raises(ValueError):
        module._directory(directory)


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("mode", [0o666, 0o775, 0o777])
def test_root_metadata_group_world_write_is_not_immutable(kind, mode):
    value = SimpleNamespace(
        st_uid=0, st_mode=(stat.S_IFREG if kind == "file" else stat.S_IFDIR) | mode
    )
    with pytest.raises(ValueError):
        module._immutable(value)


def test_root_metadata_nonwritable_file_and_symlink_admission():
    module._immutable(SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644))
    module._immutable(SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777))


@pytest.fixture
def installed(monkeypatch, tmp_path):
    # These real scratch files are NOT immutable installations. Only the
    # ownership/ancestry policy is mocked; real reads, SHA joins, descriptors,
    # source maps and final identity/state checks remain under test.
    monkeypatch.setattr(module, "_immutable", lambda info: None)
    monkeypatch.setattr(module, "_unprivileged", lambda: None)
    monkeypatch.setattr(module, "_isolated", lambda: True)
    root = tmp_path / "package"
    root.mkdir()
    (root / "__init__.py").write_bytes(b"# fixture package\n")
    (root / "child.py").write_bytes(b"# fixture child\n")
    binaries = {}
    for name in ("python", "git", "gpg", "credctl"):
        path = tmp_path / name
        path.write_bytes(("fixture-" + name).encode())
        path.chmod(0o555)
        binaries[name] = path
    library = tmp_path / "library"
    library.mkdir()
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    monkeypatch.setattr(module, "_package", lambda: root)
    monkeypatch.setattr(
        module,
        "_loaded_origins",
        lambda: [str(root / "__init__.py"), str(root / "child.py")],
    )
    monkeypatch.setattr(
        module, "_library_paths", lambda: [str(library), str(library / "python.zip")]
    )
    monkeypatch.setattr(module, "_helper", lambda git: helpers)
    monkeypatch.setattr(module, "_interpreter_modules", lambda: ())
    monkeypatch.setattr(module.sys, "executable", str(binaries["python"]))
    monkeypatch.setattr(module.protected_git, "_GIT", str(binaries["git"]))
    monkeypatch.setattr(module.protected_git, "_GPG", str(binaries["gpg"]))
    data = {
        name + suffix: value
        for name, path in binaries.items()
        for suffix, value in (
            ("_path", str(path)),
            ("_sha256", _hash(path.read_bytes())),
        )
    }
    data["module_sha256"] = {p.name: _hash(p.read_bytes()) for p in root.glob("*.py")}
    raw = _canonical(data)
    provider = SimpleNamespace(
        profile_id="fixture",
        registry_sha256="sha256:" + "a" * 64,
        profile_sha256=_hash(raw),
        profile_utf8=raw,
        data=data,
    )
    return provider, root, binaries, helpers


def test_explicit_metadata_fixture_admits_exact_pins_not_production(installed):
    provider, root, _, helpers = installed
    result = module.admit_installation(provider)
    assert result.package_root == str(root)
    assert result.git_helper_path == str(helpers)
    assert len(result.interpreter_library_paths) == 2
    assert result.registry_sha256 == provider.registry_sha256
    assert "fixture" not in repr(result)


@pytest.mark.parametrize(
    "fault",
    [
        "not-isolated",
        "interpreter",
        "mixed-origin",
        "extra-source",
        "missing-source",
        "source-digest",
        "git-global",
        "gpg-global",
        "helper",
        "profile-digest",
    ],
)
def test_refuses_drift_and_mixed_imported_origins(
    installed, monkeypatch, tmp_path, fault
):
    provider, root, binaries, _ = installed
    if fault == "not-isolated":
        monkeypatch.setattr(module, "_isolated", lambda: False)
    elif fault == "interpreter":
        path = tmp_path / "other-python"
        path.write_bytes(binaries["python"].read_bytes())
        path.chmod(0o555)
        monkeypatch.setattr(module.sys, "executable", str(path))
    elif fault == "mixed-origin":
        path = tmp_path / "mixed.py"
        path.write_bytes(b"# different imported source\n")
        monkeypatch.setattr(module, "_loaded_origins", lambda: [str(path)])
    elif fault == "extra-source":
        (root / "unregistered.py").write_bytes(b"pass\n")
    elif fault == "missing-source":
        (root / "child.py").unlink()
    elif fault == "source-digest":
        (root / "child.py").write_bytes(b"# tampered\n")
    elif fault in ("git-global", "gpg-global"):
        name = fault.split("-")[0]
        path = tmp_path / ("other-" + name)
        path.write_bytes(binaries[name].read_bytes())
        path.chmod(0o555)
        monkeypatch.setattr(module.protected_git, "_" + name.upper(), str(path))
    elif fault == "helper":
        monkeypatch.setattr(module, "_helper", lambda git: tmp_path / "missing")
    else:
        provider.profile_sha256 = "sha256:" + "b" * 64
    with pytest.raises(
        module.InstallationRefused, match="^protected-runtime-installation-invalid$"
    ):
        module.admit_installation(provider)


def test_final_join_refuses_source_replacement_during_helper_inspection(
    installed, monkeypatch
):
    provider, root, _, helpers = installed

    def helper(git):
        path = root / "child.py"
        path.unlink()
        path.write_bytes(b"# fixture child\n")
        return helpers

    monkeypatch.setattr(module, "_helper", helper)
    with pytest.raises(module.InstallationRefused):
        module.admit_installation(provider)


def test_symlink_joins_refuse_replacement_during_descriptor_read(
    installed, monkeypatch, tmp_path
):
    _, _, binaries, _ = installed
    link = tmp_path / "fixed-python"
    link.symlink_to(binaries["python"])
    original = module.os.read
    changed = []

    def read(fd, count):
        raw = original(fd, count)
        if not changed:
            changed.append(True)
            link.unlink()
            link.symlink_to(binaries["git"])
        return raw

    monkeypatch.setattr(module.os, "read", read)
    with pytest.raises(ValueError):
        module._file(link)


def test_fixed_helper_inspection_scrubs_environment(monkeypatch, tmp_path):
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=(str(tmp_path) + "\n").encode())

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setenv("GIT_EXEC_PATH", "/caller-selected")
    assert module._helper(Path("/fixed/git")) == tmp_path
    argv, arguments = seen[0]
    assert argv == ["/fixed/git", "--exec-path"]
    assert "GIT_EXEC_PATH" not in arguments["env"]
    assert arguments["timeout"] == 3


def test_privileged_execution_refuses(monkeypatch):
    monkeypatch.setattr(module.os, "getuid", lambda: 0)
    with pytest.raises(ValueError):
        module._unprivileged()


def test_real_loaded_package_origin_outside_admitted_root_refuses(
    installed, monkeypatch, tmp_path
):
    provider, _, _, _ = installed
    root = _REAL_PACKAGE()
    monkeypatch.setattr(module, "_package", _REAL_PACKAGE)
    monkeypatch.setattr(module, "_loaded_origins", _REAL_ORIGINS)
    # This test isolates the origin check. The mutable pytest checkout has
    # real bytecode caches and is not a source-only installation; separate
    # cached-bytecode tests qualify that required refusal.
    monkeypatch.setattr(module, "_source_only_package", lambda selected: {})
    # Earlier IPC fixtures deliberately loaded the unmerged provider overlay.
    # Remove only those fixture imports for this baseline; admission would
    # correctly reject them as mixed roots if left present.
    for name, loaded in tuple(module.sys.modules.items()):
        if name.startswith("vuoro_reconciler."):
            origin = getattr(loaded, "__file__", None)
            if origin and not Path(origin).resolve().is_relative_to(root.resolve()):
                monkeypatch.delitem(module.sys.modules, name)
    provider.data["module_sha256"] = {
        str(path.relative_to(root)): _hash(path.read_bytes())
        for path in root.rglob("*.py")
    }
    provider.profile_utf8 = _canonical(provider.data)
    provider.profile_sha256 = _hash(provider.profile_utf8)
    # Baseline real currently imported package closure passes under the
    # explicitly mocked immutable-metadata fixture.
    module.admit_installation(provider)
    path = tmp_path / "foreign_import.py"
    path.write_bytes(b"# actual mixed source origin\n")
    name = "vuoro_reconciler.fixture_foreign_import"
    foreign = ModuleType(name)
    foreign.__file__ = str(path)
    foreign.__spec__ = ModuleSpec(
        name,
        loader=importlib.machinery.SourceFileLoader(name, str(path)),
        origin=str(path),
    )
    monkeypatch.setitem(module.sys.modules, name, foreign)
    with pytest.raises(module.InstallationRefused):
        module.admit_installation(provider)


def test_real_multiple_package_paths_refuse(monkeypatch, tmp_path):
    import vuoro_reconciler

    monkeypatch.setattr(
        vuoro_reconciler, "__path__", [*vuoro_reconciler.__path__, str(tmp_path)]
    )
    with pytest.raises(ValueError):
        _REAL_PACKAGE()


@pytest.mark.parametrize("capability", ["CapEff", "CapPrm", "CapInh", "CapAmb"])
def test_nonroot_capability_metadata_refuses(capability, monkeypatch):
    raw = "".join(
        key + ":\t" + ("1" if key == capability else "0") + "\n"
        for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")
    )
    monkeypatch.setattr(
        module, "open", lambda *args, **kwargs: io.StringIO(raw), raising=False
    )
    with pytest.raises(ValueError):
        module._unprivileged()


def test_loaded_interpreter_extension_collects_actual_origin_and_refuses_mutability(
    monkeypatch, tmp_path
):
    path = tmp_path / "fixture-native.so"
    path.write_bytes(b"fixture data, never loaded as native code")
    imported = ModuleType("fixture_untrusted_native")
    imported.__file__ = str(path)
    monkeypatch.setitem(module.sys.modules, imported.__name__, imported)
    assert str(path) in module._interpreter_modules()
    with pytest.raises(ValueError):
        module._file(path)


def test_fixed_refusal_retains_no_sensitive_exception_context(installed, monkeypatch):
    provider, _, _, _ = installed
    sentinel = "sensitive-private-locator-and-credential-sentinel"

    def fail():
        raise OSError(sentinel)

    monkeypatch.setattr(module, "_package", fail)
    with pytest.raises(module.InstallationRefused) as caught:
        module.admit_installation(provider)
    assert str(caught.value) == "protected-runtime-installation-invalid"
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert sentinel not in repr(caught.value)


@pytest.mark.parametrize("suffix", [".pyc", ".pyo", ".so", ".zip", ".dll"])
def test_source_only_package_refuses_cached_or_native_or_zip_code(installed, suffix):
    provider, root, _, _ = installed
    (root / ("unregistered-code" + suffix)).write_bytes(b"fixture code container")
    with pytest.raises(module.InstallationRefused) as caught:
        module.admit_installation(provider)
    assert caught.value.__context__ is caught.value.__cause__ is None


def test_actual_timestamp_size_bytecode_substitution_refused(installed):
    provider, root, _, _ = installed
    path = root / "child.py"
    approved = b"VALUE = 'approved'\n"
    poisoned = b"VALUE = 'tampered'\n"
    assert len(approved) == len(poisoned)
    path.write_bytes(poisoned)
    stamp = path.stat().st_mtime_ns
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(approved)
    os.utime(path, ns=(stamp, stamp))
    provider.data["module_sha256"]["child.py"] = _hash(approved)
    provider.profile_utf8 = _canonical(provider.data)
    provider.profile_sha256 = _hash(provider.profile_utf8)
    # A .py origin plus approved source bytes still executes substituted cache.
    loader = importlib.machinery.SourceFileLoader("cache_discriminator", str(path))
    imported = ModuleType("cache_discriminator")
    imported.__file__ = str(path)
    loader.exec_module(imported)
    assert imported.VALUE == "tampered" and imported.__file__ == str(path)
    assert _hash(path.read_bytes()) == provider.data["module_sha256"]["child.py"]
    with pytest.raises(module.InstallationRefused):
        module.admit_installation(provider)


def test_alternate_cache_prefix_refuses(installed, monkeypatch, tmp_path):
    provider, _, _, _ = installed
    monkeypatch.setattr(module.sys, "pycache_prefix", str(tmp_path / "caller-cache"))
    with pytest.raises(module.InstallationRefused):
        module.admit_installation(provider)


def test_code_container_inserted_during_final_inspection_refuses(
    installed, monkeypatch
):
    provider, root, _, helpers = installed

    def helper(git):
        (root / "late.pyc").write_bytes(b"late code cache")
        return helpers

    monkeypatch.setattr(module, "_helper", helper)
    with pytest.raises(module.InstallationRefused):
        module.admit_installation(provider)


def test_binary_code_magic_under_data_filename_refuses(installed):
    provider, root, _, _ = installed
    (root / "data.bin").write_bytes(b"\x7fELFfixture-only")
    with pytest.raises(module.InstallationRefused):
        module.admit_installation(provider)


@pytest.mark.parametrize("privilege", [stat.S_ISUID, stat.S_ISGID])
def test_actual_registered_executable_privilege_mode_refuses(installed, privilege):
    provider, _, binaries, _ = installed
    # Real mode bits on the otherwise hash-matching executable fixture.
    binaries["git"].chmod(0o555 | privilege)
    with pytest.raises(module.InstallationRefused) as caught:
        module.admit_installation(provider)
    assert caught.value.__context__ is caught.value.__cause__ is None


@pytest.mark.parametrize("result", [b"file-capability", errno.EACCES, errno.EOPNOTSUPP])
def test_registered_executable_capability_or_unreadable_metadata_refuses(
    installed, monkeypatch, result
):
    provider, _, _, _ = installed

    def get(descriptor, name):
        assert isinstance(descriptor, int) and name == "security.capability"
        if isinstance(result, int):
            raise OSError(result, "sensitive capability metadata sentinel")
        return result

    # Explicit xattr fixture: no privileged security.capability installation.
    monkeypatch.setattr(module.os, "getxattr", get)
    with pytest.raises(module.InstallationRefused) as caught:
        module.admit_installation(provider)
    assert caught.value.__context__ is caught.value.__cause__ is None


def test_actual_missing_security_capability_is_normal(installed):
    _, _, binaries, _ = installed
    descriptor = os.open(binaries["python"], os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            os.getxattr(descriptor, "security.capability")
        assert caught.value.errno == errno.ENODATA
        module._executable_privileges(descriptor, os.fstat(descriptor))
    finally:
        os.close(descriptor)
