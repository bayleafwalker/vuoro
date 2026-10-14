"""Installed closed credential child. No generic URL, command, or credential API."""

from __future__ import annotations
import asyncio
import fcntl
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import tempfile
from urllib.parse import quote, urlencode

from . import protected_git as git
from .protected_copy_codec import (
    _canonical,
    _closed,
    _read,
    _hash,
    _integer,
    _need,
    _read_json,
    _pairs,
    _number,
    _tree,
)
from .protected_provider import (
    _INPUT_MAX,
    _OUTPUT_MAX,
    _b64,
    _validate_input,
    _spawn,
    _cleanup_owned,
    _environment,
)
from .protected_provider_registry import (
    _decode,
    load_installed_profile,
    _join_cgroup,
    _admit_current_cgroup,
    _validate_active,
    _state_paths,
    _active_record,
    _owned,
    _path_state,
    _filesystem,
    _LOCAL_FILESYSTEMS,
)
from .protected_read_transport import _HEADER_NAME, _TOKEN, _origin

_HTTP_SECONDS = 15.0
_TOTAL_SECONDS = 60.0
# Linux UAPI linux/fcntl.h; standalone CPython3.12 omits these names.
_ADD_SEALS = 1033
_GET_SEALS = 1034
_SEALS = 1 | 2 | 4 | 8


def _context(profile):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["http/1.1"])
    for root in profile["ca_der_b64"]:
        context.load_verify_locations(
            cadata=ssl.DER_cert_to_PEM_cert(_decode(root, 32768))
        )
    _need(
        sorted(context.get_ca_certs(binary_form=True))
        == sorted(_decode(v, 32768) for v in profile["ca_der_b64"])
    )
    return context


async def _http(profile, token, method, path, body, maximum, deadline):
    writer = None
    try:
        async with asyncio.timeout_at(deadline):
            hostname, port, authority = _origin(profile["https_origin"])
            address = ipaddress.ip_address(profile["numeric_ip"])
            reader, writer = await asyncio.open_connection(
                host=str(address),
                port=port,
                family=socket.AF_INET if address.version == 4 else socket.AF_INET6,
                flags=socket.AI_NUMERICHOST,
                ssl=_context(profile),
                server_hostname=hostname,
                ssl_handshake_timeout=max(
                    0.001, deadline - asyncio.get_running_loop().time()
                ),
                limit=8192,
            )
            tls = writer.get_extra_info("ssl_object")
            _need(
                tls.version() == "TLSv1.3"
                and tls.selected_alpn_protocol() in (None, "http/1.1")
            )
            head = (
                f"{method} {path} HTTP/1.1\r\nHost: {authority}\r\nAuthorization: token {token}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(head + body)
            await writer.drain()
            total = 0

            async def line():
                nonlocal total
                raw = await reader.readuntil(b"\r\n")
                total += len(raw)
                _need(
                    len(raw) <= 8192
                    and total <= 65536
                    and not any(c < 32 or c == 127 for c in raw[:-2])
                )
                return raw[:-2]

            first = await line()
            _need(re.fullmatch(rb"HTTP/1\.[01] [1-5][0-9]{2}(?: [\x20-\x7e]*)?", first))
            status = int(first.split(b" ")[1])
            headers = {}
            for _ in range(65):
                raw = await line()
                if raw == b"":
                    break
                _need(
                    len(headers) < 64
                    and not raw.startswith((b" ", b"\t"))
                    and b":" in raw
                )
                name, value = raw.split(b":", 1)
                _need(_HEADER_NAME.fullmatch(name) and name.lower() not in headers)
                headers[name.lower()] = value.strip(b" ")
            else:
                _need(False)
            if status not in (200, 201, 404):
                return status, headers, None
            _need(b"content-encoding" not in headers)
            _need(
                headers.get(b"content-type", b"").lower()
                in (
                    b"application/json",
                    b"application/json; charset=utf-8",
                    b"application/json;charset=utf-8",
                )
            )
            length, transfer = (
                headers.get(b"content-length"),
                headers.get(b"transfer-encoding"),
            )
            _need((length is None) != (transfer is None))
            chunks, used = [], 0

            async def consume(amount):
                nonlocal used
                _need(amount <= maximum - used)
                remaining = amount
                while remaining:
                    chunk = await reader.read(min(65536, remaining))
                    _need(bool(chunk))
                    chunks.append(chunk)
                    used += len(chunk)
                    remaining -= len(chunk)

            if length is not None:
                _need(len(length) <= 10 and re.fullmatch(rb"0|[1-9][0-9]*", length))
                await consume(int(length))
            else:
                _need(transfer.lower() == b"chunked")
                for _ in range(513):
                    raw = await reader.readuntil(b"\r\n")
                    _need(re.fullmatch(rb"[0-9a-fA-F]{1,16}\r\n", raw))
                    size = int(raw[:-2], 16)
                    if size == 0:
                        _need(await reader.readexactly(2) == b"\r\n")
                        break
                    _need(_ < 512)
                    await consume(size)
                    _need(await reader.readexactly(2) == b"\r\n")
                else:
                    _need(False)
            _need(await reader.read(1) == b"")
            return status, headers, b"".join(chunks)
    finally:
        if writer is not None:
            writer.transport.abort()


def _repo(profile, raw):
    value = _read_json(raw, 256 * 1024)
    _need(type(value) is dict)
    _integer(value.get("id"))
    _need(
        value["id"] == profile["repository_id"]
        and value.get("full_name") == profile["owner"] + "/" + profile["name"]
    )


def _pr(profile, request, raw):
    _need(type(raw) is dict)
    _integer(raw.get("number"))
    _integer(raw.get("id"))
    number = raw["number"]
    for part in ("head", "base"):
        data = raw.get(part)
        _need(type(data) is dict and type(data.get("repo")) is dict)
        _integer(data.get("repo_id"))
        _integer(data["repo"].get("id"))
        _need(data["repo_id"] == data["repo"]["id"] == profile["repository_id"])
        _need(data["repo"].get("full_name") == profile["owner"] + "/" + profile["name"])
    _need(
        raw["head"].get("ref") == request["branch"]
        and raw["head"].get("sha") == request["commit_oid"]
    )
    _need(raw["base"].get("ref") == profile["base_branch"] == request["base_branch"])
    _need(
        raw.get("title") == request["title"]
        and type(raw.get("body")) is str
        and raw["body"].encode("utf8") == _decode(request["body_utf8_b64"], 32768)
    )
    expected_url = (
        profile["https_origin"]
        + "/"
        + profile["owner"]
        + "/"
        + profile["name"]
        + "/pulls/"
        + str(number)
    )
    _need(raw.get("html_url") == expected_url and type(raw.get("merged")) is bool)
    state = raw.get("state")
    _need(state in ("open", "closed") and not (state == "open" and raw["merged"]))
    return number, expected_url, "merged" if raw["merged"] else state


def _observation(request, operation, *, pr=None):
    if operation == "branch-lookup":
        names = "effect_key binding_sha256 repository branch commit_oid".split()
        result = {
            "schema": "vuoro-protected-branch-observation/v1",
            **{key: request[key] for key in names},
            "request_sha256": _hash(_canonical(request)),
        }
    else:
        names = "effect_key binding_sha256 repository head_repository branch commit_oid base_branch title body_utf8_b64 body_sha256".split()
        result = {
            "schema": "vuoro-protected-pr-observation/v1",
            **{key: request[key] for key in names},
            "request_sha256": _hash(_canonical(request)),
            "pr_number": pr[0],
            "pr_url": pr[1],
            "state": pr[2],
        }
    return _canonical(result)


class _Never:
    async def acquire_base(self, *args):
        _need(False)

    async def sign(self, *args):
        _need(False)


async def _push(profile, token, runner, request, deadline):
    repo = runner.scratch / "repo"
    ca = runner.scratch / "public-roots.pem"
    ca.write_text(
        "".join(
            ssl.DER_cert_to_PEM_cert(_decode(root, 32768))
            for root in profile["ca_der_b64"]
        )
    )
    home = runner.scratch / "git-push-home"
    home.mkdir(mode=0o700)
    hostname, port, _ = _origin(profile["https_origin"])
    numeric = profile["numeric_ip"]
    if ":" in numeric:
        numeric = "[" + numeric + "]"
    settings = [
        ("http.extraHeader", "Authorization: token " + token),
        ("http.proxy", ""),
        ("http.followRedirects", "false"),
        ("http.sslVerify", "true"),
        ("http.sslVersion", "tlsv1.3"),
        ("http.sslCAInfo", str(ca)),
        ("http.curloptResolve", f"{hostname}:{port}:{numeric}"),
        ("credential.helper", ""),
        ("core.hooksPath", "/dev/null"),
        ("core.fsmonitor", "false"),
        ("http.emptyAuth", "false"),
        ("http.maxRequests", "1"),
    ]
    env = runner.env()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    branch = "refs/heads/" + request["branch"]
    remote = (
        profile["https_origin"]
        + "/"
        + profile["owner"]
        + "/"
        + profile["name"]
        + ".git"
    )
    process = await _spawn(
        profile["git_path"],
        "-C",
        str(repo),
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "push",
        "--no-verify",
        "--porcelain",
        "--force-with-lease=" + branch + ":",
        remote,
        request["commit_oid"] + ":" + branch,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def drain(stream):
        used = 0
        while chunk := await stream.read(65536):
            used += len(chunk)
            _need(used <= 1024 * 1024)

    tasks = [
        asyncio.create_task(drain(process.stdout)),
        asyncio.create_task(drain(process.stderr)),
        asyncio.create_task(process.wait()),
    ]
    try:
        async with asyncio.timeout_at(deadline):
            await asyncio.gather(*tasks)
        # Even Git exit0 can mean already up-to-date; it never proves adoption.
    finally:
        await _cleanup_owned(process, tasks)


async def _perform(
    profile, token, value, record, effect, request, local, runner, deadline
):
    operation = value["operation"]
    data = profile.data
    prefix = "/api/v1/repos/" + data["owner"] + "/" + data["name"]
    http_deadline = min(deadline, asyncio.get_running_loop().time() + _HTTP_SECONDS)
    status, _, raw = await _http(
        data, token, "GET", prefix, b"", 256 * 1024, http_deadline
    )
    if status != 200:
        return "UNKNOWN", None
    _repo(data, raw)
    if operation == "capability-check":
        return "CAPABILITY_PRESENT", None
    if operation == "branch-create":
        await _push(data, token, runner, request, deadline)
        return "TRANSMISSION_POSSIBLE", None
    if operation == "pr-create":
        body = _canonical(
            {
                "head": request["branch"],
                "base": request["base_branch"],
                "title": request["title"],
                "body": _decode(request["body_utf8_b64"], 32768).decode("utf8"),
            }
        )
        _need(len(body) <= 65536)
        status, _, raw = await _http(
            data, token, "POST", prefix + "/pulls", body, 256 * 1024, http_deadline
        )
        if status == 201:
            _pr(data, request, _read_json(raw, 256 * 1024))
            return "TRANSMISSION_POSSIBLE", None
        return "UNKNOWN", None
    if operation == "branch-lookup":
        path = prefix + "/branches/" + quote(request["branch"], safe="")
        status, _, raw = await _http(
            data, token, "GET", path, b"", 256 * 1024, http_deadline
        )
        if status == 404:
            return "ABSENT", None
        if status != 200:
            return "UNKNOWN", None
        branch = _read_json(raw, 256 * 1024)
        _need(
            type(branch) is dict
            and branch.get("name") == request["branch"]
            and type(branch.get("commit")) is dict
        )
        if branch["commit"].get("id") != request["commit_oid"]:
            return "CONFLICT", None
        return "OBSERVED", _observation(request, operation)
    path = (
        prefix
        + "/pulls?"
        + urlencode(
            {
                "state": "all",
                "head": request["branch"],
                "base": request["base_branch"],
                "limit": 2,
                "page": 1,
            }
        )
    )
    status, headers, raw = await _http(
        data, token, "GET", path, b"", 2 * 1024 * 1024, http_deadline
    )
    if status != 200:
        return "UNKNOWN", None
    count = headers.get(b"x-total-count", b"")
    _need(re.fullmatch(rb"0|[1-9][0-9]*", count) and len(count) <= 16)
    _need(type(raw) is bytes and 0 < len(raw) <= 2 * 1024 * 1024)
    rows = json.loads(
        raw.decode("utf8"),
        object_pairs_hook=_pairs,
        parse_float=_number,
        parse_constant=_number,
    )
    _tree(rows)
    _need(type(rows) is list)
    if int(count) >= 2 or len(rows) >= 2:
        return "CONFLICT", None
    _need(int(count) == len(rows))
    if not rows:
        return "ABSENT", None
    pr = _pr(data, request, rows[0])
    return "OBSERVED", _observation(request, operation, pr=pr)


async def _execute(raw):
    value0 = _read_json(raw, _INPUT_MAX)
    _need(type(value0) is dict)
    profile = load_installed_profile(value0.get("profile_id"))
    value, record, effect, request, local = _validate_input(raw, profile)
    token = os.environ.pop("FJ_TOKEN", None)
    _need(type(token) is str and 0 < len(token) <= 16384 and _TOKEN.fullmatch(token))
    data = profile.data
    _admit_current_cgroup(data)
    _validate_active(profile)
    git._GIT, git._GPG = data["git_path"], data["gpg_path"]
    deadline = asyncio.get_running_loop().time() + _TOTAL_SECONDS
    observation = None
    status = "UNKNOWN"
    async with asyncio.timeout_at(deadline):
        with tempfile.TemporaryDirectory(prefix="vuoro-protected-provider-") as scratch:
            runner = git._Runner(scratch, deadline)
            artifact = git.ProtectedGit(
                profile.signing, data["artifact_root"], _Never(), _Never()
            )
            binding, registration = git._record(effect, record, profile.signing)
            root, artifacts, directory, _ = artifact._artifact_directory(
                record.effect_key
            )
            descriptors = (root, artifacts, directory)
            try:
                await artifact._restore(
                    runner, effect, record, binding, registration, descriptors, local
                )
                status, observation = await _perform(
                    profile,
                    token,
                    value,
                    record,
                    effect,
                    request,
                    local,
                    runner,
                    deadline,
                )
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
    result = _canonical(
        {
            "schema": "vuoro-protected-provider-child-result/v1",
            "operation": value["operation"],
            "effect_key": record.effect_key,
            "binding_sha256": _hash(record.binding_utf8),
            "request_sha256": _hash(_canonical(request)),
            "status": status,
            "observation_utf8_b64": None if observation is None else _b64(observation),
        }
    )
    _need(len(result) <= _OUTPUT_MAX)
    return result


def _launch(descriptor):
    _need("FJ_TOKEN" not in os.environ)
    raw = sys.stdin.buffer.read(_INPUT_MAX + 1)
    _need(0 < len(raw) <= _INPUT_MAX)
    initial = _read_json(raw, _INPUT_MAX)
    profile = load_installed_profile(initial.get("profile_id"))
    value, _, _, _, _ = _validate_input(raw, profile)
    _join_cgroup(descriptor, profile.data)
    _validate_active(profile)
    # Restore the already consumed closed request as sealed RAM stdin, never
    # message/diff argv or a persistent file. Broker child inherits stdin.
    memory = os.memfd_create(
        "vuoro-provider-request", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    )
    try:
        remaining = memoryview(raw)
        while remaining:
            written = os.write(memory, remaining)
            _need(written > 0)
            remaining = remaining[written:]
        os.lseek(memory, 0, os.SEEK_SET)
        fcntl.fcntl(memory, _ADD_SEALS, _SEALS)
        _need(fcntl.fcntl(memory, _GET_SEALS) == _SEALS)
        os.dup2(memory, 0, inheritable=True)
    finally:
        os.close(memory)
    data = profile.data
    capability = "repo.read" if value["operation"].endswith("-lookup") else "repo.write"
    command = [
        data["credctl_path"],
        "exec",
        capability,
        "--repository",
        data["broker_repository_id"],
        "--",
        data["python_path"],
        "-I",
        "-m",
        "vuoro_reconciler.protected_provider_child",
    ]
    os.execve(data["credctl_path"], command, _environment())


def _state_helper(procs_fd, directory_fd, lock_fd, events_fd):
    _need("FJ_TOKEN" not in os.environ)
    raw = sys.stdin.buffer.read(4097)
    value = _read(raw, 4096)
    _closed(
        value, "schema action profile_id registry_sha256 profile_sha256 operation_id"
    )
    _need(
        value["schema"] == "vuoro-protected-operation-state-request/v1"
        and value["action"] in ("begin", "finalize")
    )
    profile = load_installed_profile(value["profile_id"])
    _need(
        value["registry_sha256"] == profile.registry_sha256
        and value["profile_sha256"] == profile.profile_sha256
    )
    expected = _active_record(profile, value["operation_id"])
    _join_cgroup(procs_fd, profile.data)
    directory, lock, name = _state_paths(profile.profile_id)
    before = _path_state(directory)
    _need(os.readlink("/proc/self/fd/" + str(directory_fd)) == str(directory))
    _need(os.readlink("/proc/self/fd/" + str(lock_fd)) == str(lock))
    _owned(os.fstat(directory_fd), 0o700, True)
    _owned(os.fstat(lock_fd), 0o600)
    _need(_filesystem(directory_fd) in _LOCAL_FILESYSTEMS)
    for descriptor, path in ((directory_fd, directory), (lock_fd, lock)):
        current = os.lstat(path)
        _need(
            (current.st_dev, current.st_ino)
            == (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)
        )
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    main_events = (
        profile.data["cgroup_root"]
        + "/operation-"
        + value["operation_id"]
        + "/cgroup.events"
    )
    _need(os.readlink("/proc/self/fd/" + str(events_fd)) == main_events)
    info = os.lstat(main_events)
    _need(
        (info.st_dev, info.st_ino)
        == (os.fstat(events_fd).st_dev, os.fstat(events_fd).st_ino)
    )
    os.lseek(events_fd, 0, os.SEEK_SET)
    events = os.read(events_fd, 8193)
    _need(len(events) <= 8192)
    fields = {}
    for line in events.decode("ascii").splitlines():
        key, item = line.split()
        _need(key not in fields)
        fields[key] = item
    _need(fields.get("populated") == "0")
    try:
        os.stat(
            profile.profile_id + ".quarantine",
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    else:
        _need(False)
    if value["action"] == "begin":
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            info = os.fstat(descriptor)
            _owned(info, 0o600)
            remaining = memoryview(expected)
            while remaining:
                count = os.write(descriptor, remaining)
                _need(count > 0)
                remaining = remaining[count:]
            os.fsync(descriptor)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            _need((info.st_dev, info.st_ino) == (current.st_dev, current.st_ino))
        finally:
            os.close(descriptor)
    else:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd
        )
        try:
            info = os.fstat(descriptor)
            _owned(info, 0o600)
            _need(0 < info.st_size <= 4096)
            _need(
                os.read(descriptor, 4097) == expected and os.read(descriptor, 1) == b""
            )
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            _need(
                (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
                == (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                )
            )
            _need(_path_state(directory) == before)
            os.unlink(name, dir_fd=directory_fd)
        finally:
            os.close(descriptor)
    os.fsync(directory_fd)
    _need(_path_state(directory) == before)
    _owned(os.fstat(directory_fd), 0o700, True)
    _owned(os.lstat(directory), 0o700, True)
    _owned(os.fstat(lock_fd), 0o600)
    _owned(os.lstat(lock), 0o600)
    sys.stdout.buffer.write(raw)


def main():
    # A failed parse/registry/credential operation has no trusted correlation
    # envelope. Emit nothing; parent translates it to its finite UNKNOWN.
    try:
        if len(sys.argv) == 6 and sys.argv[1] == "--state":
            _need(
                all(re.fullmatch(r"[1-9][0-9]{0,9}", field) for field in sys.argv[2:])
            )
            _state_helper(*(int(field) for field in sys.argv[2:]))
            return 0
        if len(sys.argv) == 3 and sys.argv[1] == "--launch":
            _need(re.fullmatch(r"[1-9][0-9]{0,9}", sys.argv[2]))
            _launch(int(sys.argv[2]))
            _need(False)
        _need(len(sys.argv) == 1)
        raw = sys.stdin.buffer.read(_INPUT_MAX + 1)
        _need(0 < len(raw) <= _INPUT_MAX)
        result = asyncio.run(_execute(raw))
    except Exception:
        return 1
    sys.stdout.buffer.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
