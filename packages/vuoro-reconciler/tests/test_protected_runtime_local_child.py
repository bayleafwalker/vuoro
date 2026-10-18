"""Actual bounded local Git object closure; no installed or signing authority."""

import asyncio
import importlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import pytest
from vuoro_reconciler import protected_runtime_local_child as child


@pytest.fixture
def lifecycle(monkeypatch):
    import vuoro_reconciler

    directory = os.environ.get("Q4_PROVIDER_TEST_SOURCE_DIR")
    if directory:
        monkeypatch.setattr(
            vuoro_reconciler, "__path__", [*vuoro_reconciler.__path__, directory]
        )
    return importlib.import_module("vuoro_reconciler.protected_provider")


def run(git, repo, *args, data=None, check=True):
    env = child._environment(repo.parent / "empty-home")
    env.update(
        GIT_AUTHOR_NAME="Disposable",
        GIT_AUTHOR_EMAIL="disposable@example.invalid",
        GIT_COMMITTER_NAME="Disposable",
        GIT_COMMITTER_EMAIL="disposable@example.invalid",
        GIT_AUTHOR_DATE="2026-10-14T00:00:00+00:00",
        GIT_COMMITTER_DATE="2026-10-14T00:00:00+00:00",
    )
    return subprocess.run(
        [git, "-C", str(repo), *args],
        input=data,
        env=env,
        capture_output=True,
        check=check,
        timeout=10,
    )


@pytest.fixture(params=["sha1", "sha256"])
def objects(tmp_path, request):
    git = shutil.which("git")
    assert git
    mirror, target, template = (
        tmp_path / name
        for name in ("object-mirror", "object-target", "object-template")
    )
    template.mkdir(mode=0o700)
    for repo in (mirror, target):
        repo.mkdir(mode=0o700)
        run(
            git,
            repo,
            "init",
            "--bare",
            "--quiet",
            "--object-format=" + request.param,
            "--template=" + str(template),
        )
    blob = run(
        git, mirror, "hash-object", "-w", "--stdin", data=b"approved content\n"
    ).stdout.strip()
    tree = run(
        git, mirror, "mktree", data=b"100644 blob " + blob + b"\tapproved.txt\n"
    ).stdout.strip()
    ancestor = run(
        git, mirror, "commit-tree", tree.decode(), data=b"ancestor\n"
    ).stdout.strip()
    oid = (
        run(
            git,
            mirror,
            "commit-tree",
            tree.decode(),
            "-p",
            ancestor.decode(),
            data=b"approved base\n",
        )
        .stdout.strip()
        .decode()
    )
    unrelated = (
        run(
            git,
            mirror,
            "hash-object",
            "-w",
            "--stdin",
            data=b"unrelated content\n" * 65536,
        )
        .stdout.strip()
        .decode()
    )
    return git, mirror, target, oid, unrelated


def test_actual_only_reachable_closure_ignores_mirror_configuration(objects, lifecycle):
    git, mirror, target, oid, unrelated = objects
    (mirror / "config").write_bytes(b"malformed mirror config must not be read\n")
    before = (target / "config").read_bytes()
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = asyncio.run(
            child._base(
                SimpleNamespace(data={"git_path": git}),
                SimpleNamespace(data={"local_base": {"git_directory": str(mirror)}}),
                oid,
                descriptor,
            )
        )
    finally:
        os.close(descriptor)
    assert result == child._canonical(
        {"schema": "vuoro-protected-local-base-result/v1", "oid": oid}
    )
    assert (target / "config").read_bytes() == before
    assert run(git, target, "cat-file", "-t", oid).stdout == b"commit\n"
    assert run(git, target, "cat-file", "-e", unrelated, check=False).returncode != 0
    assert (
        len(
            run(
                git,
                target,
                "cat-file",
                "--batch-all-objects",
                "--batch-check=%(objectname)",
            ).stdout.splitlines()
        )
        == 4
    )
    run(git, target, "fsck", "--strict", "--no-reflogs", oid)


@pytest.mark.parametrize(
    "name",
    [
        "commondir",
        "shallow",
        "info/grafts",
        "objects/info/alternates",
        "objects/info/http-alternates",
    ],
)
def test_mirror_indirection_refuses_before_git(objects, lifecycle, monkeypatch, name):
    git, mirror, target, oid, unrelated = objects
    (mirror / name).parent.mkdir(exist_ok=True)
    (mirror / name).write_bytes(b"/unregistered/path\n")

    async def forbidden(*args, **kwargs):
        raise AssertionError("Git must not run")

    monkeypatch.setattr(child, "_command", forbidden)
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            asyncio.run(
                child._base(
                    SimpleNamespace(data={"git_path": git}),
                    SimpleNamespace(
                        data={"local_base": {"git_directory": str(mirror)}}
                    ),
                    oid,
                    descriptor,
                )
            )
    finally:
        os.close(descriptor)


def test_actual_missing_base_refuses_without_target_objects(objects, lifecycle):
    git, mirror, target, oid, unrelated = objects
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            asyncio.run(
                child._base(
                    SimpleNamespace(data={"git_path": git}),
                    SimpleNamespace(
                        data={"local_base": {"git_directory": str(mirror)}}
                    ),
                    "0" * len(oid),
                    descriptor,
                )
            )
    finally:
        os.close(descriptor)
    assert (
        run(
            git,
            target,
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname)",
        ).stdout
        == b""
    )


@pytest.mark.parametrize("drift", ["config", "replacement", "mode"])
def test_actual_target_drift_refuses_before_import(
    objects, lifecycle, monkeypatch, drift
):
    git, mirror, target, oid, unrelated = objects
    original = child._command
    calls = []

    async def command(argv, *args, **kwargs):
        result = await original(argv, *args, **kwargs)
        calls.append(argv)
        if "pack-objects" in argv:
            if drift == "config":
                (target / "config").write_bytes(
                    (target / "config").read_bytes()
                    + b"\n[core]\n\tfsmonitor = /untrusted-helper\n"
                )
            elif drift == "mode":
                target.chmod(0o755)
            else:
                target.rename(target.with_name("old-target"))
                target.mkdir(mode=0o700)
                run(
                    git,
                    target,
                    "init",
                    "--bare",
                    "--quiet",
                    "--object-format=" + ("sha1" if len(oid) == 40 else "sha256"),
                )
        return result

    monkeypatch.setattr(child, "_command", command)
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            asyncio.run(
                child._base(
                    SimpleNamespace(data={"git_path": git}),
                    SimpleNamespace(
                        data={"local_base": {"git_directory": str(mirror)}}
                    ),
                    oid,
                    descriptor,
                )
            )
    finally:
        os.close(descriptor)
    assert not any("index-pack" in call for call in calls)
    assert (
        run(
            git,
            target,
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname)",
        ).stdout
        == b""
    )


def test_actual_pack_budget_refuses_before_import(objects, lifecycle, monkeypatch):
    git, mirror, target, oid, unrelated = objects
    monkeypatch.setattr(child, "_PACK_MAX", 16)
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            asyncio.run(
                child._base(
                    SimpleNamespace(data={"git_path": git}),
                    SimpleNamespace(
                        data={"local_base": {"git_directory": str(mirror)}}
                    ),
                    oid,
                    descriptor,
                )
            )
    finally:
        os.close(descriptor)
    assert (
        run(
            git,
            target,
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname)",
        ).stdout
        == b""
    )


@pytest.fixture(scope="session")
def keys(tmp_path_factory):
    # Root explicitly permits ephemeral fixture-only software authority here.
    from test_protected_git import keys as fixture

    yield from fixture.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize("foreign", [False, True])
def test_actual_existing_agent_detached_signature_exact_fingerprint(
    tmp_path, lifecycle, keys, foreign
):
    key = keys[int(foreign)]
    fingerprint = key["signing"]
    provider = SimpleNamespace(
        data={"gpg_path": shutil.which("gpg")},
        signing=SimpleNamespace(
            signing_profile_utf8=child._canonical({"signing_fingerprint": fingerprint})
        ),
    )
    runtime = SimpleNamespace(data={"signer": {"gnupg_home": str(key["home"])}})
    from vuoro_reconciler.protected_runtime_credentials import _sealed

    unsigned = (
        b"tree "
        + b"a" * 40
        + b"\nparent "
        + b"b" * 40
        + b"\nauthor Fixture <fixture@example.invalid> 1791936000 +0000\ncommitter Fixture <fixture@example.invalid> 1791936000 +0000\n\nfixture only\n"
    )
    descriptor = _sealed(unsigned)
    try:
        signature = asyncio.run(child._sign(provider, runtime, descriptor))
    finally:
        os.close(descriptor)
    assert signature.startswith(b"-----BEGIN PGP SIGNATURE-----\n")
    signed_file, input_file = (
        tmp_path / "public-signature.asc",
        tmp_path / "public-unsigned-commit",
    )
    signed_file.write_bytes(signature)
    input_file.write_bytes(unsigned)
    verified = subprocess.run(
        [
            shutil.which("gpg"),
            "--no-options",
            "--no-autostart",
            "--batch",
            "--status-fd",
            "1",
            "--verify",
            str(signed_file),
            str(input_file),
        ],
        env={"PATH": os.defpath, "GNUPGHOME": str(key["home"])},
        capture_output=True,
        timeout=10,
    )
    assert (
        verified.returncode == 0
        and (b"[GNUPG:] VALIDSIG " + fingerprint.upper().encode()) in verified.stdout
    )


def test_actual_missing_agent_refuses_without_starting_one(tmp_path, lifecycle):
    from vuoro_reconciler.protected_runtime_credentials import _sealed

    home = tmp_path / "empty-signer"
    home.mkdir(mode=0o700)
    provider = SimpleNamespace(
        data={"gpg_path": shutil.which("gpg")},
        signing=SimpleNamespace(
            signing_profile_utf8=child._canonical({"signing_fingerprint": "a" * 40})
        ),
    )
    runtime = SimpleNamespace(data={"signer": {"gnupg_home": str(home)}})
    descriptor = _sealed(b"unsigned disposable fixture")
    try:
        with pytest.raises(ValueError):
            asyncio.run(child._sign(provider, runtime, descriptor))
    finally:
        os.close(descriptor)
    assert not any(path.name.startswith("S.gpg-agent") for path in home.iterdir())
    assert not (home / "private-keys-v1.d").exists()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("foreign", [False, True])
def test_actual_frozen_git_callback_integration(
    tmp_path, lifecycle, keys, object_format, foreign
):
    from test_protected_git import case
    from vuoro_reconciler.protected_runtime_credentials import _sealed

    value = case(tmp_path, keys, object_format=object_format)
    registered = SimpleNamespace(
        data={"git_path": shutil.which("git"), "gpg_path": shutil.which("gpg")},
        signing=value.profile,
    )
    runtime = SimpleNamespace(
        data={
            "local_base": {"git_directory": str(value.repo / ".git")},
            "signer": {"gnupg_home": str(keys[int(foreign)]["home"])},
        }
    )
    calls = []

    class Local:
        async def acquire_base(self, profile, oid, target):
            initial_mode = stat.S_IMODE(target.stat().st_mode)
            assert profile == value.profile
            fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
            try:
                await child._base(registered, runtime, oid, fd)
            finally:
                os.close(fd)
            calls.append(
                ("base", initial_mode, stat.S_IMODE(target.stat().st_mode))
            )

        async def sign(self, unsigned):
            calls.append(("sign", len(unsigned)))
            fd = _sealed(unsigned)
            signer = registered
            if foreign:
                signer = SimpleNamespace(
                    data=registered.data,
                    signing=SimpleNamespace(
                        signing_profile_utf8=child._canonical(
                            {"signing_fingerprint": keys[1]["signing"]}
                        )
                    ),
                )
            try:
                return await child._sign(signer, runtime, fd)
            finally:
                os.close(fd)

    local = Local()
    value.git.base_provider = local
    value.git.signer = local
    try:
        if foreign:
            with pytest.raises(ValueError):
                asyncio.run(value.git.prepare_original(value.effect, value.record))
        else:
            artifact = asyncio.run(
                value.git.prepare_original(value.effect, value.record)
            )
            assert len(child._read(artifact.local_ready_utf8, 16384)["commit_oid"]) == (
                40 if object_format == "sha1" else 64
            )
            from test_protected_git import remember

            remember(value, artifact)

            async def forbidden(*args):
                raise AssertionError("recovery must reuse the original signed OID")

            local.acquire_base = forbidden
            local.sign = forbidden
            recovered = asyncio.run(
                value.git.verify_original(
                    value.effect,
                    value.record,
                    child._read(artifact.local_ready_utf8, 16384)["commit_oid"],
                )
            )
            assert recovered.local_ready_utf8 == artifact.local_ready_utf8
        assert [call[0] for call in calls] == ["base", "sign"]
        assert calls[0][1] == calls[0][2]
    finally:
        value.journal.close()


@pytest.mark.parametrize("drift", ["mode", "inode", "symlink"])
def test_actual_existing_signer_directory_replacement_refuses(
    tmp_path, lifecycle, keys, monkeypatch, drift
):
    from vuoro_reconciler.protected_runtime_credentials import _sealed

    key = keys[0]
    home = key["home"]
    previous = home.with_name(home.name + "-held")
    registered = SimpleNamespace(
        data={"gpg_path": shutil.which("gpg")},
        signing=SimpleNamespace(
            signing_profile_utf8=child._canonical(
                {"signing_fingerprint": key["signing"]}
            )
        ),
    )
    runtime = SimpleNamespace(data={"signer": {"gnupg_home": str(home)}})
    command = child._command

    async def mutate(*args, **kwargs):
        result = await command(*args, **kwargs)
        if drift == "mode":
            home.chmod(0o755)
        else:
            home.rename(previous)
            if drift == "inode":
                home.mkdir(mode=0o700)
            else:
                home.symlink_to(previous, target_is_directory=True)
        return result

    monkeypatch.setattr(child, "_command", mutate)
    descriptor = _sealed(b"unsigned fixture only")
    try:
        with pytest.raises(ValueError):
            asyncio.run(child._sign(registered, runtime, descriptor))
    finally:
        os.close(descriptor)
        if drift == "mode":
            home.chmod(0o700)
        elif previous.exists():
            if home.is_symlink():
                home.unlink()
            else:
                home.rmdir()
            previous.rename(home)


@pytest.mark.parametrize("kind", ["unsealed", "oversize", "named", "mode"])
def test_unsigned_input_requires_exact_sealed_anonymous_fd(tmp_path, kind):
    from vuoro_reconciler.protected_runtime_credentials import _sealed
    import fcntl

    if kind == "named":
        path = tmp_path / "named-public-input"
        path.write_bytes(b"unsigned fixture")
        path.chmod(0o600)
        descriptor = os.open(path, os.O_RDONLY)
    elif kind == "unsealed":
        descriptor = os.memfd_create(
            "unsealed-disposable", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
        )
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, b"unsigned fixture")
        assert fcntl.fcntl(descriptor, 1034) == 0
    else:
        descriptor = _sealed(b"x" * (65537 if kind == "oversize" else 1))
        if kind == "mode":
            os.fchmod(descriptor, 0o644)
    try:
        with pytest.raises(ValueError):
            child._unsigned(descriptor)
    finally:
        os.close(descriptor)


def test_actual_truncated_pack_refuses_and_closes_pack_fd(
    objects, lifecycle, monkeypatch
):
    git, mirror, target, oid, unrelated = objects
    original = child._command
    descriptors = []

    async def truncated(argv, *args, **kwargs):
        if "pack-objects" in argv:
            from vuoro_reconciler.protected_runtime_credentials import _sealed

            descriptor = _sealed(b"PACK\x00\x00\x00\x02\x00\x00\x00\x01broken")
            descriptors.append(descriptor)
            return descriptor
        return await original(argv, *args, **kwargs)

    monkeypatch.setattr(child, "_command", truncated)
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            asyncio.run(
                child._base(
                    SimpleNamespace(data={"git_path": git}),
                    SimpleNamespace(
                        data={"local_base": {"git_directory": str(mirror)}}
                    ),
                    oid,
                    descriptor,
                )
            )
    finally:
        os.close(descriptor)
    assert descriptors
    for transferred in descriptors:
        with pytest.raises(OSError):
            os.fstat(transferred)
    assert (
        run(
            git,
            target,
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname)",
        ).stdout
        == b""
    )


def test_actual_ambient_git_helper_proxy_and_hook_never_execute(
    objects, lifecycle, tmp_path, monkeypatch
):
    git, mirror, target, oid, unrelated = objects
    marker = tmp_path / "ambient-executed"
    evil = tmp_path / "ambient-helper"
    evil.write_text("#!/bin/sh\nprintf executed > " + str(marker) + "\nexit 1\n")
    evil.chmod(0o700)
    for name, value in {
        "GIT_SSH_COMMAND": str(evil),
        "GIT_CONFIG_GLOBAL": str(evil),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": str(evil),
        "GIT_CONFIG_PARAMETERS": "'alias.pack-objects=!" + str(evil) + "'",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "HOME": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    hooks = mirror / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").symlink_to(evil)
    (mirror / "config").write_text(
        "[core]\n\tfsmonitor = "
        + str(evil)
        + "\n[credential]\n\thelper = "
        + str(evil)
        + "\n"
    )
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        asyncio.run(
            child._base(
                SimpleNamespace(data={"git_path": git}),
                SimpleNamespace(data={"local_base": {"git_directory": str(mirror)}}),
                oid,
                descriptor,
            )
        )
    finally:
        os.close(descriptor)
    assert not marker.exists()


@pytest.fixture
def short_gnupg_home():
    # GnuPG creates several UNIX sockets under GNUPGHOME. Keep the path short
    # enough for S.gpg-agent.browser even when pytest's temp path is very long.
    with tempfile.TemporaryDirectory(prefix="v-gpg-") as directory:
        home = Path(directory)
        yield home


def test_actual_locked_fixture_signer_refuses_without_unlock_or_agent_creation(
    short_gnupg_home, lifecycle
):
    from vuoro_reconciler.protected_runtime_credentials import _sealed

    home = short_gnupg_home
    env = {"PATH": os.defpath, "GNUPGHOME": str(home), "LANG": "C"}
    gpg, gpgconf, agent = (
        shutil.which(name) for name in ("gpg", "gpgconf", "gpg-agent")
    )
    assert gpg and gpgconf and agent
    # Public known software-fixture passphrase, never a production PIN or key.
    create = subprocess.run(
        [
            gpg,
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "disposable-public-fixture-passphrase",
            "--quick-generate-key",
            "Locked Fixture <locked@example.invalid>",
            "ed25519",
            "sign",
            "0",
        ],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert create.returncode == 0
    listing = subprocess.run(
        [gpg, "--batch", "--with-colons", "--list-secret-keys"],
        env=env,
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    fingerprint = next(
        line.split(b":")[9].decode().lower()
        for line in listing.splitlines()
        if line.startswith(b"fpr:")
    )
    subprocess.run(
        [gpgconf, "--homedir", str(home), "--kill", "gpg-agent"],
        env=env,
        capture_output=True,
        check=True,
        timeout=10,
    )
    # Explicit fixture-only restart clears its passphrase cache. Runtime still
    # has --no-autostart and never creates or unlocks any signing authority.
    descriptor = _sealed(b"unsigned disposable fixture")
    try:
        started = subprocess.run(
            [gpgconf, "--homedir", str(home), "--launch", "gpg-agent"],
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert started.returncode == 0
        with pytest.raises(ValueError):
            asyncio.run(
                child._sign(
                    SimpleNamespace(
                        data={"gpg_path": gpg},
                        signing=SimpleNamespace(
                            signing_profile_utf8=child._canonical(
                                {"signing_fingerprint": fingerprint}
                            )
                        ),
                    ),
                    SimpleNamespace(data={"signer": {"gnupg_home": str(home)}}),
                    descriptor,
                )
            )
    finally:
        os.close(descriptor)
        subprocess.run(
            [gpgconf, "--homedir", str(home), "--kill", "gpg-agent"],
            env=env,
            capture_output=True,
            timeout=10,
        )
