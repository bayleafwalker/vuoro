"""Durable original Git artifacts, never approval or publication authority.

Base acquisition and signing are private injected dependencies. No request can
choose a URL, key, environment, or executable. A future trusted executor must
obtain fresh protected delivery/currentness before invoking this local library.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from functools import wraps
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import tempfile
import time

from .diff_policy import DiffPolicy, StagedChange, check_changes, check_patch_text
from .execution_journal import JournalEvent, JournalRecord, SpentRecord, effect_key
from .protected_copy_codec import (
    _assert_effect_data,
    _canonical,
    _closed,
    _digest,
    _hash,
    _need,
    _read,
    _text,
    decode_protected_copy,
)

_TOTAL_SECONDS = 60.0
_BUNDLE_MAX = 256 * 1024 * 1024
_OUTPUT_MAX = 1024 * 1024
_COMMIT_MAX = 65536
_PUBLIC_KEY_MAX = 65536
_GIT = shutil.which("git")
_GPG = shutil.which("gpg")
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_PROFILE_FIELDS = "schema repository signing_fingerprint public_key_sha256 author_name author_email committer_name committer_email path_allowlist protected_path_patterns"
_BINDING_FIELDS = "schema namespace intent_id intent_revision canonical_hash legacy_content_digest acceptance_id acceptance_revision preparation_id receipt_id accepted_at signature_sha256 challenge_sha256 frozen_reference_set_sha256 registry_sha256 provider_profile_sha256 signing_profile_sha256 publication_target signer_fingerprint public_key_sha256"
_LOCAL_FIELDS = "schema commit_oid tree_oid base_parent_oid message_utf8_b64 message_sha256 signer_fingerprint public_key_sha256 artifact_relative_path artifact_sha256"


class ProtectedGitRefused(ValueError):
    """Fixed metadata only, without proposal, paths, keys or upstream errors."""


def _fixed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        failed = False
        try:
            return function(*args, **kwargs)
        except Exception:
            failed = True
        if failed:
            raise ProtectedGitRefused("protected-git-refused")

    return checked


def _async_fixed(function):
    @wraps(function)
    async def checked(*args, **kwargs):
        failed = False
        try:
            return await function(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        if failed:
            raise ProtectedGitRefused("protected-git-refused")

    return checked


def _identity(info):
    return info.st_dev, info.st_ino


def _owned(info, mode, directory=False):
    _need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == mode)
    _need(
        stat.S_ISDIR(info.st_mode)
        if directory
        else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    )


def _path(path):
    _need(isinstance(path, (str, Path)))
    original = Path(path)
    _need(".." not in original.parts)
    path = Path(os.path.abspath(original))
    for part in reversed((path, *path.parents)):
        _need(not stat.S_ISLNK(os.lstat(part).st_mode))
    return path


def _name(value):
    _text(value, 200)
    _need(not any(ord(c) < 32 or ord(c) == 127 or c in "<>" for c in value))


def _email(value):
    _text(value, 254)
    _need(value.isascii() and value.count("@") == 1)
    _need(not any(ord(c) <= 32 or ord(c) == 127 or c in "<>" for c in value))
    _need(all(value.split("@")))


def _branch(value):
    _text(value, 200)
    _need(
        value not in ("HEAD", "@")
        and not value.startswith(("/", "-"))
        and not value.endswith(("/", "."))
    )
    _need(not any(ord(c) <= 32 or ord(c) == 127 or c in "~^:?*[\\" for c in value))
    _need(".." not in value and "@{" not in value and "//" not in value)
    _need(
        all(not p.startswith(".") and not p.endswith(".lock") for p in value.split("/"))
    )


@dataclass(frozen=True, slots=True, repr=False)
class GitArtifactProfile:
    signing_profile_utf8: bytes
    public_key_utf8: bytes

    @_fixed
    def __post_init__(self):
        value = _read(self.signing_profile_utf8, 16384)
        _closed(value, _PROFILE_FIELDS)
        _need(value["schema"] == "vuoro-protected-git-signing-profile/v1")
        _text(value["repository"], 200)
        _need(
            type(value["signing_fingerprint"]) is str
            and _OID.fullmatch(value["signing_fingerprint"])
        )
        _need(
            type(self.public_key_utf8) is bytes
            and 0 < len(self.public_key_utf8) <= _PUBLIC_KEY_MAX
        )
        _need(value["public_key_sha256"] == _hash(self.public_key_utf8))
        for key in ("author_name", "committer_name"):
            _name(value[key])
        for key in ("author_email", "committer_email"):
            _email(value[key])
        for key in ("path_allowlist", "protected_path_patterns"):
            patterns = value[key]
            _need(type(patterns) is list and len(patterns) <= 64)
            _need(patterns == sorted(set(patterns)))
            for pattern in patterns:
                _text(pattern, 200)
                _need(not any(ord(c) < 32 or ord(c) == 127 for c in pattern))

    @property
    def signing_profile_sha256(self):
        return _hash(self.signing_profile_utf8)


@dataclass(frozen=True, slots=True, repr=False)
class GitArtifact:
    local_ready_utf8: bytes
    bundle_relative_path: str


class _Runner:
    def __init__(self, scratch, deadline):
        self.scratch, self.deadline = Path(scratch), deadline
        self.home = self.scratch / "home"
        self.home.mkdir(mode=0o700)
        self.template = self.scratch / "template"
        self.template.mkdir(mode=0o700)

    def env(self, gnupg=None):
        result = {
            "PATH": os.defpath,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_LITERAL_PATHSPECS": "1",
        }
        if gnupg is not None:
            result["GNUPGHOME"] = str(gnupg)
        return result

    async def run(
        self,
        executable,
        args,
        *,
        cwd=None,
        data=None,
        output_max=_OUTPUT_MAX,
        gnupg=None,
        watched_file=None,
    ):
        _need(
            executable is not None and asyncio.get_running_loop().time() < self.deadline
        )
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                executable,
                *args,
                cwd=cwd,
                env=self.env(gnupg),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Cancellation during pipe setup must not abandon a spawned child.
            process = await creation
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process._transport.close()
            await process.wait()
            await asyncio.sleep(0)
            raise

        async def collect(stream, maximum):
            chunks, size = [], 0
            while chunk := await stream.read(65536):
                size += len(chunk)
                _need(size <= maximum)
                chunks.append(chunk)
            return b"".join(chunks)

        async def feed():
            try:
                if data is not None:
                    process.stdin.write(data)
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        async def monitor():
            while process.returncode is None:
                if watched_file is not None and watched_file.exists():
                    _need(watched_file.stat().st_size <= _BUNDLE_MAX)
                await asyncio.sleep(0.01)

        tasks = [
            asyncio.create_task(collect(process.stdout, output_max)),
            asyncio.create_task(collect(process.stderr, _OUTPUT_MAX)),
            asyncio.create_task(feed()),
            asyncio.create_task(process.wait()),
            asyncio.create_task(monitor()),
        ]
        try:
            results = await asyncio.gather(*tasks)
            _need(process.returncode == 0)
            return results[0], results[1]
        finally:
            # The entire subprocess group includes Git's verifier/helper children.
            if process.returncode is None or any(not task.done() for task in tasks):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            # A paused full output pipe can keep wait() pending after SIGKILL.
            # Close all owned pipes before reaping, including inherited pipes.
            process._transport.close()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await process.wait()
            await asyncio.sleep(0)

    async def git(self, repo, *args, **kwargs):
        options = (
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "core.autocrlf=false",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "-c",
            "protocol.allow=never",
        )
        return await self.run(_GIT, (*options, "-C", str(repo), *args), **kwargs)

    async def initialize(self, object_format):
        repo = self.scratch / "repo"
        await self.run(
            _GIT,
            (
                "init",
                "--quiet",
                "--object-format=" + object_format,
                "--template=" + str(self.template),
                str(repo),
            ),
        )
        return repo


def _record(effect, record, profile):
    _assert_effect_data(effect)
    _need(type(record) is JournalRecord and record.effect_key == effect_key(effect))
    _need(type(record.events) is tuple and type(record.spent) is tuple)
    for event in record.events:
        _need(type(event) is JournalEvent and type(event.seq) is int and event.seq >= 0)
        _need(type(event.event_type) is str and type(event.payload_utf8) is bytes)
    for spent in record.spent:
        _need(type(spent) is SpentRecord and type(spent.request_utf8) is bytes)
    decoded = decode_protected_copy(
        record.protected_copy_utf8,
        namespace_utf8=effect.namespace_utf8,
        acceptance_id=effect.acceptance.acceptance_id,
        repository=effect.repository,
    )
    _need(decoded == effect)
    binding = _read(record.binding_utf8, 16384)
    _closed(binding, _BINDING_FIELDS)
    registration = _read(profile.signing_profile_utf8, 16384)
    _need(registration["repository"] == effect.repository)
    for field in ("registry_sha256", "provider_profile_sha256"):
        _digest(binding[field])
    target = binding["publication_target"]
    _closed(target, "schema repository head_repository base_branch")
    _need(
        target["schema"] == "vuoro-protected-publication-target/v1"
        and target["repository"] == target["head_repository"] == effect.repository
    )
    _branch(target["base_branch"])
    locator = _read(effect.locator_utf8)
    expected = {
        "schema": "vuoro-protected-journal-binding/v1",
        "namespace": locator["namespace"],
        "intent_id": locator["intent_id"],
        "intent_revision": locator["intent_revision"],
        "canonical_hash": effect.canonical_hash,
        "legacy_content_digest": effect.legacy_content_digest,
        "acceptance_id": effect.acceptance.acceptance_id,
        "acceptance_revision": effect.acceptance_revision,
        "preparation_id": effect.acceptance.preparation_id,
        "receipt_id": effect.receipt.receipt_id,
        "accepted_at": effect.acceptance.accepted_at,
        "signature_sha256": effect.acceptance.signature_sha256,
        "challenge_sha256": effect.acceptance.challenge_sha256,
        "frozen_reference_set_sha256": _hash(
            _canonical(_read(effect.canonical_utf8)["evidence_refs"])
        ),
        "registry_sha256": binding["registry_sha256"],
        "provider_profile_sha256": binding["provider_profile_sha256"],
        "signing_profile_sha256": profile.signing_profile_sha256,
        "publication_target": target,
        "signer_fingerprint": registration["signing_fingerprint"],
        "public_key_sha256": registration["public_key_sha256"],
    }
    _need(record.binding_utf8 == _canonical(expected))
    _need(
        record.events
        and record.events[0].seq == 0
        and record.events[0].event_type == "BOUND"
        and record.events[0].payload_utf8 == record.binding_utf8
    )
    _need([event.seq for event in record.events] == list(range(len(record.events))))
    _need(len({event.event_type for event in record.events}) == len(record.events))
    return binding, registration


def _message(effect, record, binding):
    public = {
        "schema": "vuoro-protected-commit-binding/v1",
        "effect_key": record.effect_key,
        "binding_sha256": _hash(record.binding_utf8),
        "locator_utf8_b64": base64.b64encode(effect.locator_utf8).decode("ascii"),
        "canonical_hash": binding["canonical_hash"],
        "legacy_content_digest": binding["legacy_content_digest"],
        "acceptance_id": binding["acceptance_id"],
        "acceptance_revision": binding["acceptance_revision"],
        "receipt_id": binding["receipt_id"],
        "source_run_utf8_b64": base64.b64encode(
            effect.source_run.encode("utf8")
        ).decode("ascii"),
    }
    parameters = _read(effect.parameters_utf8)
    return (
        parameters["title"].encode("utf8")
        + b"\n\n"
        + parameters["rationale"].encode("utf8")
        + b"\n\nVuoro-Protected-Binding: "
        + base64.b64encode(_canonical(public))
        + b"\n"
    )


def _local(effect, record, registration, commit_oid, tree_oid, message, bundle_hash):
    value = {
        "schema": "vuoro-protected-local-ready/v1",
        "commit_oid": commit_oid,
        "tree_oid": tree_oid,
        "base_parent_oid": _read(effect.parameters_utf8)["base_commit"],
        "message_utf8_b64": base64.b64encode(message).decode("ascii"),
        "message_sha256": _hash(message),
        "signer_fingerprint": registration["signing_fingerprint"],
        "public_key_sha256": registration["public_key_sha256"],
        "artifact_relative_path": "artifacts/" + record.effect_key + "/original.bundle",
        "artifact_sha256": bundle_hash,
    }
    raw = _canonical(value)
    _need(len(raw) <= 16384)
    return raw


async def _policy(runner, repo, base, candidate, registration):
    raw, _ = await runner.git(
        repo,
        "diff",
        "--raw",
        "-z",
        "--no-renames",
        "--no-abbrev",
        "--no-ext-diff",
        "--no-textconv",
        base,
        candidate,
        "--",
    )
    tokens = raw.split(b"\0")
    changes, blobs = [], []
    for index in range(0, len(tokens) - 1, 2):
        _need(index + 1 < len(tokens) and tokens[index].startswith(b":"))
        old_mode, new_mode, old_oid, new_oid, change = tokens[index][1:].split(b" ")
        path = tokens[index + 1].decode("utf8")
        changes.append(
            StagedChange(
                path,
                change.decode(),
                old_mode.decode(),
                new_mode.decode(),
                False,
                new_oid.decode(),
            )
        )
        for oid, mode in ((old_oid, old_mode), (new_oid, new_mode)):
            if mode != b"000000":
                _need(_OID.fullmatch(oid.decode()))
                blobs.append(oid.decode())
    _need(tokens[-1] == b"")
    check_changes(
        changes,
        DiffPolicy(
            frozenset(registration["path_allowlist"]),
            frozenset(registration["protected_path_patterns"]),
        ),
    )
    for oid in set(blobs):
        kind, _ = await runner.git(repo, "cat-file", "-t", oid)
        _need(kind == b"blob\n")
        size, _ = await runner.git(repo, "cat-file", "-s", oid)
        _need(size.strip().isdigit() and int(size) <= _BUNDLE_MAX)
        content, _ = await runner.git(
            repo, "cat-file", "blob", oid, output_max=_BUNDLE_MAX
        )
        text = content.decode("utf8")
        for index, char in enumerate(text):
            if char == "\r" and text[index + 1 : index + 2] == "\n":
                continue
            _need(
                not (
                    (ord(char) < 32 and char not in "\t\n") or 0x7F <= ord(char) <= 0x9F
                )
            )


async def _tree(runner, repo, base, effect, registration):
    parameters = _read(effect.parameters_utf8)
    patch = parameters["unified_diff"]
    check_patch_text(patch)
    info = repo / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_bytes(
        b"* -text -ident -filter -eol -working-tree-encoding\n"
    )
    await runner.git(repo, "checkout", "--detach", "--force", base, "--")
    await runner.git(
        repo,
        "apply",
        "--check",
        "--index",
        "--whitespace=nowarn",
        "-",
        data=patch.encode("utf8"),
    )
    await runner.git(
        repo, "apply", "--index", "--whitespace=nowarn", "-", data=patch.encode("utf8")
    )
    tree, _ = await runner.git(repo, "write-tree")
    tree = tree.strip().decode("ascii")
    _need(_OID.fullmatch(tree) and len(tree) == len(base))
    # A temporary unsigned commit lets the pure policy read exact tree changes.
    temporary = (
        b"tree "
        + tree.encode()
        + b"\nparent "
        + base.encode()
        + b"\nauthor Check <check@example.invalid> 1 +0000\ncommitter Check <check@example.invalid> 1 +0000\n\npolicy\n"
    )
    candidate, _ = await runner.git(
        repo, "hash-object", "-t", "commit", "-w", "--stdin", data=temporary
    )
    await _policy(runner, repo, base, candidate.strip().decode(), registration)
    return tree


async def _verifier(runner, profile):
    gnupg = runner.scratch / "verify-keys"
    gnupg.mkdir(mode=0o700)
    shown, _ = await runner.run(
        _GPG,
        (
            "--batch",
            "--no-options",
            "--homedir",
            str(gnupg),
            "--with-colons",
            "--import-options",
            "show-only",
            "--import",
        ),
        data=profile.public_key_utf8,
        gnupg=gnupg,
    )
    _need(not any(line.startswith((b"sec:", b"ssb:")) for line in shown.splitlines()))
    registration = _read(profile.signing_profile_utf8, 16384)
    fingerprints = [
        line.split(b":")[9].decode().lower()
        for line in shown.splitlines()
        if line.startswith(b"fpr:")
    ]
    _need(registration["signing_fingerprint"] in fingerprints)
    await runner.run(
        _GPG,
        ("--batch", "--no-options", "--homedir", str(gnupg), "--import"),
        data=profile.public_key_utf8,
        gnupg=gnupg,
    )
    return gnupg


def _unsigned(tree, parent, registration, message):
    timestamp = str(int(time.time())).encode("ascii")
    _need(int(timestamp) <= 2**53 - 1)

    def identity(prefix):
        return (
            registration[prefix + "_name"].encode("utf8")
            + b" <"
            + registration[prefix + "_email"].encode("ascii")
            + b"> "
            + timestamp
            + b" +0000"
        )

    return (
        b"tree "
        + tree.encode()
        + b"\nparent "
        + parent.encode()
        + b"\nauthor "
        + identity("author")
        + b"\ncommitter "
        + identity("committer")
        + b"\n\n"
        + message
    )


def _signed(unsigned, signature, width):
    _need(type(signature) is bytes and 0 < len(signature) <= 16384)
    _need(
        signature.startswith(b"-----BEGIN PGP SIGNATURE-----\n")
        and signature.endswith(b"-----END PGP SIGNATURE-----\n")
    )
    _need(all(32 <= c <= 126 or c == 10 for c in signature))
    lines = signature[:-1].split(b"\n")
    header = (b"gpgsig" if width == 40 else b"gpgsig-sha256") + b" " + lines[0] + b"\n"
    header += b"".join(b" " + line + b"\n" for line in lines[1:])
    head, message = unsigned.split(b"\n\n", 1)
    return head + b"\n" + header + b"\n" + message


async def _verify_commit(
    runner, repo, oid, effect, record, profile, binding, registration, gnupg
):
    _need(
        _OID.fullmatch(oid)
        and len(oid) == len(_read(effect.parameters_utf8)["base_commit"])
    )
    raw, _ = await runner.git(repo, "cat-file", "commit", oid, output_max=_COMMIT_MAX)
    header, message = raw.split(b"\n\n", 1)
    rows = []
    for line in header.split(b"\n"):
        if line.startswith(b" "):
            _need(rows and rows[-1][0] in (b"gpgsig", b"gpgsig-sha256"))
            rows[-1][1] += b"\n" + line[1:]
        else:
            key, value = line.split(b" ", 1)
            rows.append([key, value])
    expected_signature = b"gpgsig" if len(oid) == 40 else b"gpgsig-sha256"
    _need(
        [r[0] for r in rows]
        == [b"tree", b"parent", b"author", b"committer", expected_signature]
    )
    tree, parent = rows[0][1].decode("ascii"), rows[1][1].decode("ascii")
    _need(
        _OID.fullmatch(tree)
        and len(tree) == len(oid)
        and parent == _read(effect.parameters_utf8)["base_commit"]
    )
    timestamps = []
    for prefix, row in (("author", rows[2][1]), ("committer", rows[3][1])):
        expected = (
            registration[prefix + "_name"].encode("utf8")
            + b" <"
            + registration[prefix + "_email"].encode("ascii")
            + b"> "
        )
        _need(row.startswith(expected))
        suffix = row[len(expected) :]
        _need(re.fullmatch(rb"[0-9]+ \+0000", suffix))
        timestamp = int(suffix.split(b" ")[0])
        _need(timestamp <= 2**53 - 1)
        timestamps.append(timestamp)
    _need(
        timestamps[0] == timestamps[1] and message == _message(effect, record, binding)
    )
    _, status = await runner.git(
        repo,
        "-c",
        "gpg.format=openpgp",
        "-c",
        "gpg.program=" + _GPG,
        "verify-commit",
        "--raw",
        oid,
        gnupg=gnupg,
    )
    valid = [
        line.split()[2].decode("ascii").lower()
        for line in status.splitlines()
        if line.startswith(b"[GNUPG:] VALIDSIG ")
    ]
    _need(valid == [registration["signing_fingerprint"]])
    # Reconstructing the approved result defeats a correctly signed wrong tree.
    _need(await _tree(runner, repo, parent, effect, registration) == tree)
    return tree, message


class ProtectedGit:
    @_fixed
    def __init__(self, profile, artifact_root, base_provider, signer):
        _need(
            type(profile) is GitArtifactProfile
            and callable(getattr(base_provider, "acquire_base", None))
            and callable(getattr(signer, "sign", None))
        )
        profile.__post_init__()
        self.profile, self.base_provider, self.signer = profile, base_provider, signer
        self.artifact_root = _path(artifact_root)
        self._root_identity = _identity(os.lstat(self.artifact_root))
        _owned(os.lstat(self.artifact_root), 0o700, True)

    def _root(self):
        _path(self.artifact_root)
        current = os.lstat(self.artifact_root)
        _owned(current, 0o700, True)
        _need(_identity(current) == self._root_identity)

    def _open_directory(self, parent_fd, name, *, create=False):
        if create:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        _owned(before, 0o700, True)
        fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            _need(_identity(before) == _identity(after) == _identity(os.fstat(fd)))
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _artifact_directory(self, key, *, create=False):
        self._root()
        root_fd = os.open(
            self.artifact_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        artifacts_fd = effect_fd = None
        try:
            _need(_identity(os.fstat(root_fd)) == self._root_identity)
            try:
                artifacts_fd = self._open_directory(root_fd, "artifacts")
            except FileNotFoundError:
                _need(create)
                artifacts_fd = self._open_directory(root_fd, "artifacts", create=True)
            try:
                effect_fd = self._open_directory(artifacts_fd, key)
                existed = True
            except FileNotFoundError:
                _need(create)
                effect_fd = self._open_directory(artifacts_fd, key, create=True)
                existed = False
            self._root()
            return root_fd, artifacts_fd, effect_fd, existed
        except BaseException:
            for fd in (effect_fd, artifacts_fd, root_fd):
                if fd is not None:
                    os.close(fd)
            raise

    def _check_artifact_directory(self, key, fds):
        self._root()
        for fd, path in zip(
            fds,
            (
                self.artifact_root,
                self.artifact_root / "artifacts",
                self.artifact_root / "artifacts" / key,
            ),
        ):
            current = os.lstat(path)
            _owned(current, 0o700, True)
            _owned(os.fstat(fd), 0o700, True)
            _need(_identity(current) == _identity(os.fstat(fd)))

    async def _restore(
        self,
        runner,
        effect,
        record,
        binding,
        registration,
        fds,
        local=None,
        observed_oid=None,
    ):
        self._check_artifact_directory(record.effect_key, fds)
        before = os.stat("original.bundle", dir_fd=fds[-1], follow_symlinks=False)
        _owned(before, 0o600)
        _need(0 < before.st_size <= _BUNDLE_MAX)
        fd = os.open(
            "original.bundle",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=fds[-1],
        )
        try:
            _need(_identity(before) == _identity(os.fstat(fd)))
            copied = runner.scratch / "restore.bundle"
            digest = hashlib.sha256()
            with copied.open("xb") as output:
                remaining = before.st_size
                while remaining:
                    chunk = os.read(fd, min(65536, remaining))
                    _need(bool(chunk))
                    digest.update(chunk)
                    output.write(chunk)
                    remaining -= len(chunk)
                _need(os.read(fd, 1) == b"")
            bundle_hash = "sha256:" + digest.hexdigest()
            if local is not None:
                _need(bundle_hash == local["artifact_sha256"])
            _need(
                _identity(
                    os.stat("original.bundle", dir_fd=fds[-1], follow_symlinks=False)
                )
                == _identity(before)
            )
            _need(os.fstat(fd).st_size == before.st_size)
            # Exactly one advertised ref and no prerequisite object lines.
            with copied.open("rb") as handle:
                header = handle.readline(256)
                _need(header in (b"# v2 git bundle\n", b"# v3 git bundle\n"))
                advertised = []
                while True:
                    line = handle.readline(512)
                    _need(line and len(line) <= 511)
                    if line == b"\n":
                        break
                    if line.startswith(b"@object-format="):
                        continue
                    _need(not line.startswith(b"-"))
                    advertised.append(line.rstrip(b"\n"))
            _need(len(advertised) == 1)
            oid, ref = advertised[0].decode("ascii").split(" ")
            _need(
                _OID.fullmatch(oid)
                and ref == "refs/vuoro-protected/" + record.effect_key
            )
            if observed_oid is not None:
                _need(observed_oid == oid)
            if local is not None:
                _need(oid == local["commit_oid"])
            repo = await runner.initialize("sha1" if len(oid) == 40 else "sha256")
            await runner.git(repo, "bundle", "verify", str(copied))
            await runner.git(repo, "bundle", "unbundle", str(copied))
            await runner.git(repo, "fsck", "--strict", "--no-reflogs", oid)
            gnupg = await _verifier(runner, self.profile)
            tree, message = await _verify_commit(
                runner,
                repo,
                oid,
                effect,
                record,
                self.profile,
                binding,
                registration,
                gnupg,
            )
            raw = _local(effect, record, registration, oid, tree, message, bundle_hash)
            if local is not None:
                _need(raw == _canonical(local))
            self._check_artifact_directory(record.effect_key, fds)
            after = os.stat("original.bundle", dir_fd=fds[-1], follow_symlinks=False)
            _owned(after, 0o600)
            _need(
                _identity(after) == _identity(before)
                and after.st_size == before.st_size
                and after.st_mtime_ns == before.st_mtime_ns
                and after.st_ctime_ns == before.st_ctime_ns
            )
            # A completed artifact may have survived an uncertain previous install.
            # Harden it and every containing entry before returning LOCAL_READY data.
            os.fsync(fd)
            for directory_fd in reversed(fds):
                os.fsync(directory_fd)
            self._check_artifact_directory(record.effect_key, fds)
            return GitArtifact(raw, _read(raw)["artifact_relative_path"])
        finally:
            os.close(fd)

    @_async_fixed
    async def prepare_original(self, effect, record):
        deadline = asyncio.get_running_loop().time() + _TOTAL_SECONDS
        binding, registration = _record(effect, record, self.profile)
        _need(
            len(record.events) == 1 and not record.spent and record.outcome_utf8 is None
        )
        parent = _read(effect.parameters_utf8)["base_commit"]
        message = _message(effect, record, binding)
        _local(
            effect,
            record,
            registration,
            "0" * len(parent),
            "0" * len(parent),
            message,
            "sha256:" + "0" * 64,
        )
        _need(asyncio.get_running_loop().time() < deadline)
        fds = ()
        try:
            async with asyncio.timeout_at(deadline):
                root_fd, artifacts_fd, effect_fd, existed = self._artifact_directory(
                    record.effect_key, create=True
                )
                fds = (root_fd, artifacts_fd, effect_fd)
                with tempfile.TemporaryDirectory(
                    prefix="vuoro-protected-git-"
                ) as scratch:
                    runner = _Runner(scratch, deadline)
                    if existed:
                        return await self._restore(
                            runner, effect, record, binding, registration, fds
                        )
                    repo = await runner.initialize(
                        "sha1" if len(parent) == 40 else "sha256"
                    )
                    git_dir = repo / ".git"
                    config = (git_dir / "config").read_bytes()
                    await self.base_provider.acquire_base(self.profile, parent, git_dir)
                    _need(
                        asyncio.get_running_loop().time() < deadline
                        and (git_dir / "config").read_bytes() == config
                    )
                    _need(
                        not any(
                            (git_dir / name).exists()
                            for name in (
                                "shallow",
                                "commondir",
                                "config.worktree",
                                "info/grafts",
                                "objects/info/alternates",
                                "objects/info/http-alternates",
                            )
                        )
                    )
                    kind, _ = await runner.git(repo, "cat-file", "-t", parent)
                    _need(kind == b"commit\n")
                    await runner.git(repo, "fsck", "--strict", "--no-reflogs", parent)
                    gnupg = await _verifier(runner, self.profile)
                    tree = await _tree(runner, repo, parent, effect, registration)
                    unsigned = _unsigned(tree, parent, registration, message)
                    signature = await self.signer.sign(unsigned)
                    _need(asyncio.get_running_loop().time() < deadline)
                    signed = _signed(unsigned, signature, len(parent))
                    oid, _ = await runner.git(
                        repo,
                        "hash-object",
                        "-t",
                        "commit",
                        "-w",
                        "--stdin",
                        data=signed,
                    )
                    oid = oid.strip().decode("ascii")
                    verified_tree, _ = await _verify_commit(
                        runner,
                        repo,
                        oid,
                        effect,
                        record,
                        self.profile,
                        binding,
                        registration,
                        gnupg,
                    )
                    _need(verified_tree == tree)
                    ref = "refs/vuoro-protected/" + record.effect_key
                    await runner.git(repo, "update-ref", ref, oid)
                    temporary = Path(scratch) / "candidate.bundle"
                    await runner.git(
                        repo,
                        "bundle",
                        "create",
                        str(temporary),
                        ref,
                        watched_file=temporary,
                    )
                    _need(0 < temporary.stat().st_size <= _BUNDLE_MAX)
                    self._check_artifact_directory(record.effect_key, fds)
                    # A private complete temporary is linked atomically, never renamed over an existing original.
                    stage_fd = os.open(
                        "pending.bundle",
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_NOFOLLOW
                        | os.O_CLOEXEC,
                        0o600,
                        dir_fd=effect_fd,
                    )
                    digest = hashlib.sha256()
                    try:
                        with temporary.open("rb") as source:
                            while chunk := source.read(65536):
                                digest.update(chunk)
                                view = memoryview(chunk)
                                while view:
                                    count = os.write(stage_fd, view)
                                    _need(count > 0)
                                    view = view[count:]
                        os.fsync(stage_fd)
                        staged = os.fstat(stage_fd)
                        _owned(staged, 0o600)
                        pending = os.stat(
                            "pending.bundle", dir_fd=effect_fd, follow_symlinks=False
                        )
                        _need(_identity(pending) == _identity(staged))
                        os.link(
                            "pending.bundle",
                            "original.bundle",
                            src_dir_fd=effect_fd,
                            dst_dir_fd=effect_fd,
                            follow_symlinks=False,
                        )
                        os.unlink("pending.bundle", dir_fd=effect_fd)
                        installed = os.stat(
                            "original.bundle", dir_fd=effect_fd, follow_symlinks=False
                        )
                        _owned(installed, 0o600)
                        _need(_identity(installed) == _identity(staged))
                        os.lseek(stage_fd, 0, os.SEEK_SET)
                        installed_digest = hashlib.sha256()
                        while chunk := os.read(stage_fd, 65536):
                            installed_digest.update(chunk)
                            _need(asyncio.get_running_loop().time() < deadline)
                        _need(installed_digest.digest() == digest.digest())
                        os.fsync(stage_fd)
                        os.fsync(effect_fd)
                        os.fsync(artifacts_fd)
                        os.fsync(root_fd)
                        final = os.stat(
                            "original.bundle", dir_fd=effect_fd, follow_symlinks=False
                        )
                        _owned(final, 0o600)
                        _need(
                            _identity(final) == _identity(installed)
                            and final.st_size == installed.st_size
                            and final.st_mtime_ns == installed.st_mtime_ns
                            and final.st_ctime_ns == installed.st_ctime_ns
                        )
                    finally:
                        os.close(stage_fd)
                    self._check_artifact_directory(record.effect_key, fds)
                    raw = _local(
                        effect,
                        record,
                        registration,
                        oid,
                        tree,
                        message,
                        "sha256:" + digest.hexdigest(),
                    )
                    _need(asyncio.get_running_loop().time() < deadline)
                    return GitArtifact(raw, _read(raw)["artifact_relative_path"])
        finally:
            for fd in reversed(fds):
                os.close(fd)

    @_async_fixed
    async def verify_original(self, effect, record, observed_oid=None):
        deadline = asyncio.get_running_loop().time() + _TOTAL_SECONDS
        binding, registration = _record(effect, record, self.profile)
        ready = [event for event in record.events if event.event_type == "LOCAL_READY"]
        _need(len(ready) == 1 and ready[0].seq == 1)
        local = _read(ready[0].payload_utf8, 16384)
        _closed(local, _LOCAL_FIELDS)
        _need(
            local["artifact_relative_path"]
            == "artifacts/" + record.effect_key + "/original.bundle"
        )
        if observed_oid is not None:
            _need(observed_oid == local["commit_oid"])
        _need(asyncio.get_running_loop().time() < deadline)
        fds = ()
        try:
            async with asyncio.timeout_at(deadline):
                root_fd, artifacts_fd, effect_fd, _ = self._artifact_directory(
                    record.effect_key
                )
                fds = (root_fd, artifacts_fd, effect_fd)
                with tempfile.TemporaryDirectory(
                    prefix="vuoro-protected-restore-"
                ) as scratch:
                    return await self._restore(
                        _Runner(scratch, deadline),
                        effect,
                        record,
                        binding,
                        registration,
                        fds,
                        local,
                        observed_oid,
                    )
        finally:
            for fd in reversed(fds):
                os.close(fd)
