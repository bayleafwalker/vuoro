"""Contained fixed local object/signature operations, with no private output errors."""

import asyncio
import fcntl
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from .protected_copy_codec import _canonical, _closed, _need, _read
from .protected_runtime_local import _SCHEMA, _INPUT_MAX
from .protected_runtime_registry import (
    load_runtime_profile,
    _directory,
    _ancestry,
    _identity,
)

_PACK_MAX = 256 * 1024 * 1024
_SEALS = 15


def _environment(home):
    return {
        "PATH": "/run/current-system/sw/bin:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
        "GIT_CONFIG_KEY_2": "protocol.allow",
        "GIT_CONFIG_VALUE_2": "never",
        "GIT_CONFIG_KEY_3": "core.fsmonitor",
        "GIT_CONFIG_VALUE_3": "false",
    }


def _git_directory(path, descriptor=None):
    path = _directory(str(path), private=False)
    ancestry = _ancestry(path)
    info = os.lstat(path)
    if descriptor is not None:
        held = os.fstat(descriptor)
        _need(stat.S_ISDIR(held.st_mode) and held.st_uid == os.getuid())
        _need((held.st_dev, held.st_ino) == (info.st_dev, info.st_ino))
    protected = None
    if descriptor is not None:
        for ancestor in (path, *path.parents):
            candidate = os.lstat(ancestor)
            if (
                candidate.st_uid == os.getuid()
                and stat.S_IMODE(candidate.st_mode) == 0o700
            ):
                protected = (
                    str(ancestor),
                    candidate.st_dev,
                    candidate.st_ino,
                    candidate.st_mode,
                )
                break
        _need(protected is not None)
    for name in (
        "commondir",
        "shallow",
        "info/grafts",
        "objects/info/alternates",
        "objects/info/http-alternates",
    ):
        _need(not os.path.lexists(path / name))
    objects = path / "objects"
    _directory(str(objects), private=False)
    object_info = os.lstat(objects)
    object_state = (
        _ancestry(objects),
        object_info.st_dev,
        object_info.st_ino,
        object_info.st_mode,
        object_info.st_uid,
    )
    return (
        path,
        ancestry,
        (info.st_dev, info.st_ino, info.st_mode, info.st_uid),
        protected,
        object_state,
    )


def _check_directory(snapshot, descriptor=None):
    path, ancestry, identity, protected, object_state = snapshot
    current = _git_directory(path, descriptor)
    _need(current == snapshot and _ancestry(path) == ancestry)
    current_info = os.lstat(path)
    _need(
        identity
        == (
            current_info.st_dev,
            current_info.st_ino,
            current_info.st_mode,
            current_info.st_uid,
        )
    )


def _configuration(path):
    named = path / "config"
    before = os.lstat(named)
    _need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_uid == os.getuid()
    )
    _need(not before.st_mode & 0o022 and before.st_size <= 65536)
    descriptor = os.open(named, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _need(_identity(os.fstat(descriptor)) == _identity(before))
        raw = os.read(descriptor, 65537)
        _need(len(raw) == before.st_size and os.read(descriptor, 1) == b"")
        _need(
            _identity(os.fstat(descriptor))
            == _identity(before)
            == _identity(os.lstat(named))
        )
        return raw, _identity(before)
    finally:
        os.close(descriptor)


async def _command(argv, env, data=b"", maximum=65536, *, pack=False, pass_fds=()):
    from . import protected_provider as lifecycle

    process = await lifecycle._spawn(
        *argv,
        env=env,
        stdin=lifecycle.subprocess.PIPE,
        stdout=lifecycle.subprocess.PIPE,
        stderr=lifecycle.subprocess.DEVNULL,
        pass_fds=pass_fds,
    )
    tasks = []
    descriptor = None
    succeeded = False
    try:
        if pack:
            descriptor = os.memfd_create(
                "vuoro-reachable-objects", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
            )
            os.fchmod(descriptor, 0o600)

        async def write():
            try:
                if type(data) is int:
                    os.lseek(data, 0, os.SEEK_SET)
                    while chunk := os.read(data, 65536):
                        process.stdin.write(chunk)
                        await process.stdin.drain()
                else:
                    process.stdin.write(data)
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                # A command can reject its arguments before consuming stdin.
                # Preserve its exit status so the caller sees the fixed refusal
                # rather than a pipe race that masks the actual result.
                pass
            finally:
                process.stdin.close()

        async def read():
            chunks, size = [], 0
            while chunk := await process.stdout.read(65536):
                size += len(chunk)
                _need(size <= maximum)
                if pack:
                    position = 0
                    while position < len(chunk):
                        count = os.write(descriptor, chunk[position:])
                        _need(count > 0)
                        position += count
                else:
                    chunks.append(chunk)
            if pack:
                _need(size >= 12)
                os.lseek(descriptor, 0, os.SEEK_SET)
                _need(os.read(descriptor, 4) == b"PACK")
                os.lseek(descriptor, 0, os.SEEK_SET)
                fcntl.fcntl(descriptor, 1033, _SEALS)
                _need(fcntl.fcntl(descriptor, 1034) & _SEALS == _SEALS)
                return descriptor
            return b"".join(chunks)

        tasks = [
            asyncio.create_task(write()),
            asyncio.create_task(read()),
            asyncio.create_task(process.wait()),
        ]
        result = await asyncio.gather(*tasks)
        _need(process.returncode == 0)
        returned = result[1]
        succeeded = True
    finally:
        try:
            await lifecycle._cleanup_owned(process, tasks)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        if not succeeded and descriptor is not None:
            os.close(descriptor)
    return returned


async def _base(provider, runtime, oid, target_fd):
    _need(type(oid) is str and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid))
    _need(target_fd >= 3)
    target = Path(os.readlink("/proc/self/fd/" + str(target_fd)))
    target_snapshot = _git_directory(target, target_fd)
    config = _configuration(target)
    mirror = Path(runtime.data["local_base"]["git_directory"])
    mirror_snapshot = _git_directory(mirror)
    git = provider.data["git_path"]
    with tempfile.TemporaryDirectory(prefix="vuoro-local-objects-") as temporary:
        scratch = Path(temporary)
        home, template, synthetic = (
            scratch / name for name in ("home", "template", "synthetic")
        )
        home.mkdir(mode=0o700)
        template.mkdir(mode=0o700)
        env = _environment(home)
        await _command(
            [
                git,
                "init",
                "--bare",
                "--quiet",
                "--object-format=" + ("sha1" if len(oid) == 40 else "sha256"),
                "--template=" + str(template),
                str(synthetic),
            ],
            env,
        )
        borrowed = dict(env, GIT_OBJECT_DIRECTORY=str(mirror / "objects"))
        pack = await _command(
            [
                git,
                "-C",
                str(synthetic),
                "-c",
                "pack.threads=1",
                "pack-objects",
                "--stdout",
                "--revs",
                "--no-reuse-delta",
                "--no-reuse-object",
            ],
            borrowed,
            (oid + "\n").encode("ascii"),
            _PACK_MAX,
            pack=True,
        )
        try:
            _check_directory(mirror_snapshot)
            _check_directory(target_snapshot, target_fd)
            _need(_configuration(target) == config)
            await _command(
                [
                    git,
                    "--git-dir=/proc/self/fd/" + str(target_fd),
                    "index-pack",
                    "--strict",
                    "--stdin",
                ],
                env,
                pack,
                pass_fds=(target_fd,),
            )
            _check_directory(target_snapshot, target_fd)
            _need(_configuration(target) == config)
        finally:
            os.close(pack)
    return _canonical({"schema": "vuoro-protected-local-base-result/v1", "oid": oid})


def _unsigned(descriptor):
    _need(descriptor >= 3)
    info = os.fstat(descriptor)
    _need(
        stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 0
    )
    _need(stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= _INPUT_MAX)
    _need(fcntl.fcntl(descriptor, 1034) & _SEALS == _SEALS)
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw = os.read(descriptor, _INPUT_MAX + 1)
    _need(len(raw) == info.st_size and os.read(descriptor, 1) == b"")
    return raw


async def _sign(provider, runtime, descriptor):
    unsigned = _unsigned(descriptor)
    home = _directory(runtime.data["signer"]["gnupg_home"], private=True)
    info = os.lstat(home)
    ancestry, identity = _ancestry(home), (info.st_dev, info.st_ino)
    signing = _read(provider.signing.signing_profile_utf8, 16384)
    with tempfile.TemporaryDirectory(prefix="vuoro-local-sign-home-") as temporary:
        env = _environment(Path(temporary))
        env["GNUPGHOME"] = str(home)
        result = await _command(
            [
                provider.data["gpg_path"],
                "--no-options",
                "--no-autostart",
                "--no-auto-key-retrieve",
                "--batch",
                "--no-tty",
                "--pinentry-mode",
                "error",
                "--local-user",
                signing["signing_fingerprint"] + "!",
                "--detach-sign",
                "--armor",
                "--output",
                "-",
            ],
            env,
            unsigned,
        )
    _directory(str(home), private=True)
    after = os.lstat(home)
    _need(_ancestry(home) == ancestry and (after.st_dev, after.st_ino) == identity)
    return result


async def main():
    _need(len(sys.argv) == 4)
    group_fd, input_fd, target_fd = (int(value) for value in sys.argv[1:])
    request = _read(sys.stdin.buffer.read(2049), 2048)
    _closed(
        request,
        "schema profile_id runtime_profile_sha256 provider_registry_sha256 provider_profile_sha256 action base_oid",
    )
    _need(request["schema"] == _SCHEMA and request["action"] in {"base", "sign"})
    from .protected_provider_registry import (
        load_installed_profile,
        _join_cgroup,
        _validate_active,
    )

    provider = load_installed_profile(request["profile_id"])
    _need(
        provider.registry_sha256 == request["provider_registry_sha256"]
        and provider.profile_sha256 == request["provider_profile_sha256"]
    )
    _join_cgroup(group_fd, provider.data)
    _validate_active(provider)
    runtime = load_runtime_profile(request["profile_id"])
    _need(runtime.profile_sha256 == request["runtime_profile_sha256"])
    async with asyncio.timeout(53.0):
        if request["action"] == "base":
            _need(input_fd == -1)
            result = await _base(provider, runtime, request["base_oid"], target_fd)
        else:
            _need(target_fd == -1 and request["base_oid"] is None)
            result = await _sign(provider, runtime, input_fd)
    _need(
        load_runtime_profile(runtime.profile_id) == runtime
        and load_installed_profile(provider.profile_id) == provider
    )
    sys.stdout.buffer.write(result)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException:
        os._exit(1)
