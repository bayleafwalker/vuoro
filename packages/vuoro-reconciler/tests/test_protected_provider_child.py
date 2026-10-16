"""Actual TLS, smart Git receive-pack, framing and child failure oracles."""

import asyncio
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
import pytest
from vuoro_reconciler import protected_provider_child as module
from vuoro_reconciler.protected_provider import _validate_input
from test_protected_copy_codec import canonical
from test_protected_git import GIT, run
import test_protected_git as git_fixtures

from test_protected_provider import make_case, request_input
import test_protected_provider as provider_fixtures

from test_protected_read_transport import command
import test_protected_read_transport as tls_fixtures


keys = git_fixtures.keys
certificates = tls_fixtures.certificates
provider_case = provider_fixtures.provider_case


TOKEN = "disposable.scoped-child-token"


@pytest.fixture(scope="session")
def server_certificates(certificates):
    command(
        "req",
        "-new",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-nodes",
        "-keyout",
        str(certificates / "forgejo.key"),
        "-out",
        str(certificates / "forgejo.csr"),
        "-subj",
        "/CN=forgejo.invalid",
    )
    (certificates / "forgejo.ext").write_text(
        "basicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:forgejo.invalid,DNS:localhost,IP:127.0.0.1\n"
    )
    command(
        "x509",
        "-req",
        "-in",
        str(certificates / "forgejo.csr"),
        "-CA",
        str(certificates / "ca.pem"),
        "-CAkey",
        str(certificates / "ca.key"),
        "-CAcreateserial",
        "-out",
        str(certificates / "forgejo.pem"),
        "-days",
        "1",
        "-extfile",
        str(certificates / "forgejo.ext"),
    )
    return certificates


def wire(body=b"{}", status=200, *, headers=None, chunked=False):
    headers = headers or {}
    head = f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n".encode()
    head += (
        b"Transfer-Encoding: chunked\r\n"
        if chunked
        else f"Content-Length: {len(body)}\r\n".encode()
    )
    head += (
        b"".join(
            k.encode() + b": " + str(v).encode() + b"\r\n" for k, v in headers.items()
        )
        + b"Connection: close\r\n\r\n"
    )
    return head + (
        f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n" if chunked else body
    )


class TLS:
    def __init__(self, certificates, handler=None):
        self.requests = []
        self.handler = handler
        self.peers = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_fixed()

            def do_POST(self):
                self.handle_fixed()

            def handle_fixed(self):
                try:
                    if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                        chunks = []
                        while True:
                            size = int(self.rfile.readline().strip(), 16)
                            if not size:
                                self.rfile.readline()
                                break
                            chunks.append(self.rfile.read(size))
                            assert self.rfile.read(2) == b"\r\n"
                        body = b"".join(chunks)
                    else:
                        body = self.rfile.read(
                            int(self.headers.get("Content-Length", "0"))
                        )
                    outer.requests.append(
                        (self.command, self.path, dict(self.headers), body)
                    )
                    payload = outer.handler(self, body) if outer.handler else wire()
                    if isinstance(payload, list):
                        for item, delay in payload:
                            self.connection.sendall(item)
                            time.sleep(delay)
                    else:
                        self.connection.sendall(payload)
                    self.close_connection = True
                except (OSError, ValueError):
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(
            certificates / "forgejo.pem", certificates / "forgejo.key"
        )
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def origin(self):
        return "https://forgejo.invalid:" + str(self.server.server_port)


def profile_for(server, certificates):
    import base64

    return {
        "https_origin": server.origin,
        "numeric_ip": "127.0.0.1",
        "ca_der_b64": [
            base64.b64encode(
                ssl.PEM_cert_to_DER_cert((certificates / "ca.pem").read_text())
            ).decode()
        ],
    }


def call_http(server, certificates, *, timeout=2, maximum=2 * 1024 * 1024):
    async def call():
        return await module._http(
            profile_for(server, certificates),
            TOKEN,
            "GET",
            "/fixed",
            b"",
            maximum,
            asyncio.get_running_loop().time() + timeout,
        )

    return asyncio.run(call())


@pytest.mark.parametrize("drift", ["hostname", "roots"])
def test_actual_tls_trust_drift_refuses_before_http(server_certificates, drift):
    with TLS(server_certificates, lambda h, b: wire(b"{}")) as server:
        data = profile_for(server, server_certificates)
        if drift == "hostname":
            data["https_origin"] = server.origin.replace(
                "forgejo.invalid", "other.invalid"
            )
        else:
            data["ca_der_b64"] = []

        async def call():
            return await module._http(
                data,
                TOKEN,
                "GET",
                "/fixed",
                b"",
                262144,
                asyncio.get_running_loop().time() + 2,
            )

        with pytest.raises((ValueError, ssl.SSLError)):
            asyncio.run(call())
        assert server.requests == []


def test_actual_tls_large_single_chunk_streams_without_dns_or_proxy(
    server_certificates, monkeypatch
):
    body = canonical({"value": "x" * 200000})
    with TLS(server_certificates, lambda h, b: wire(body, chunked=True)) as server:

        def no_dns(*args, **kwargs):
            pytest.fail("resolver used for numeric connection")

        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        status, headers, returned = call_http(server, server_certificates)
        assert status == 200 and returned == body and len(server.requests) == 1
        assert server.requests[0][2]["Authorization"] == "token " + TOKEN


@pytest.mark.parametrize(
    "kind",
    [
        "both",
        "extensions",
        "trailers",
        "duplicate",
        "encoding",
        "extra",
        "truncated",
        "oversize",
        "chunks",
        "redirect",
        "auth",
    ],
)
def test_actual_tls_framing_and_unknown_status_refusals(server_certificates, kind):
    prefix = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
    cases = {
        "both": prefix
        + b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n",
        "extensions": prefix
        + b"Transfer-Encoding: chunked\r\n\r\n2;foo=x\r\n{}\r\n0\r\n\r\n",
        "trailers": prefix
        + b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\nX: y\r\n\r\n",
        "duplicate": prefix + b"Content-Length: 2\r\ncontent-length: 2\r\n\r\n{}",
        "encoding": prefix + b"Content-Length: 2\r\nContent-Encoding: gzip\r\n\r\n{}",
        "extra": wire() + b"x",
        "truncated": prefix + b"Content-Length: 4\r\n\r\n{}",
        "oversize": prefix + b"Transfer-Encoding: chunked\r\n\r\n200001\r\n",
        "chunks": prefix
        + b"Transfer-Encoding: chunked\r\n\r\n"
        + b"1\r\nx\r\n" * 513
        + b"0\r\n\r\n",
        "redirect": wire(status=302, headers={"Location": "https://attacker.invalid"}),
        "auth": wire(status=401, headers={"WWW-Authenticate": 'Basic realm="fixture"'}),
    }
    with TLS(server_certificates, lambda h, b: cases[kind]) as server:
        if kind in ("redirect", "auth"):
            status, _, body = call_http(server, server_certificates)
            assert status in (302, 401) and body is None
        else:
            with pytest.raises((ValueError, asyncio.IncompleteReadError)):
                call_http(server, server_certificates)
        assert len(server.requests) == 1


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_actual_tls_byte_drip_has_hard_deadline(server_certificates, phase):
    response = wire()
    parts = (
        [(response[:10], 0.4), (response[10:], 0)]
        if phase == "headers"
        else [(response[:-1], 0.4), (response[-1:], 0)]
    )
    with TLS(server_certificates, lambda h, b: parts) as server:
        before = time.monotonic()
        with pytest.raises(TimeoutError):
            call_http(server, server_certificates, timeout=0.15)
        assert time.monotonic() - before < 0.35


def pr_row(request, data, *, state="open", merged=False):
    repo = {"id": 8, "full_name": "owner/repository"}
    return {
        "id": 27,
        "number": 42,
        "head": {
            "repo_id": 8,
            "repo": repo,
            "ref": request["branch"],
            "sha": request["commit_oid"],
        },
        "base": {"repo_id": 8, "repo": repo, "ref": "main", "sha": "a" * 40},
        "title": request["title"],
        "body": __import__("base64").b64decode(request["body_utf8_b64"]).decode(),
        "html_url": data["https_origin"] + "/owner/repository/pulls/42",
        "state": state,
        "merged": merged,
    }


@pytest.mark.parametrize(
    "kind",
    [
        "absent",
        "open",
        "merged",
        "closed",
        "duplicate",
        "head-id",
        "nested-id",
        "full-name",
        "oid",
        "body",
        "title",
        "base",
        "bool-number",
        "null",
        "missing-count",
    ],
)
def test_exact_all_state_pr_join_cardinality_and_full_body_actual_tls(
    tmp_path, keys, server_certificates, monkeypatch, kind
):
    holder = {}

    def handler(h, b):
        if "?" not in h.path:
            return wire(canonical({"id": 8, "full_name": "owner/repository"}))
        request = holder["request"]
        row = pr_row(
            request,
            holder["profile"],
            state="closed" if kind in ("merged", "closed") else "open",
            merged=kind == "merged",
        )
        if kind == "head-id":
            row["head"]["repo_id"] = 9
        elif kind == "nested-id":
            row["head"]["repo"] = {"id": 9, "full_name": "owner/repository"}
        elif kind == "full-name":
            row["base"]["repo"] = {"id": 8, "full_name": "fork/repository"}
        elif kind == "oid":
            row["head"]["sha"] = "0" * 40
        elif kind == "body":
            row["body"] += "\n"
        elif kind == "title":
            row["title"] += " "
        elif kind == "base":
            row["base"]["ref"] = "other"
        elif kind == "bool-number":
            row["number"] = True
        rows = (
            []
            if kind == "absent"
            else [row, row]
            if kind == "duplicate"
            else [None]
            if kind == "null"
            else [row]
        )
        return wire(
            canonical(rows),
            headers={} if kind == "missing-count" else {"X-Total-Count": len(rows)},
            chunked=True,
        )

    with TLS(server_certificates, handler) as server:
        value = make_case(
            tmp_path, keys, server_certificates, monkeypatch, server.origin
        )
        try:
            raw = request_input(value, "pr-lookup")
            _, _, _, request, _ = _validate_input(raw, value.provider_profile)
            holder.update(request=request, profile=value.provider_profile.data)
            monkeypatch.setenv("FJ_TOKEN", TOKEN)
            if kind in ("absent", "open", "merged", "closed", "duplicate"):
                result = json.loads(asyncio.run(module._execute(raw)))
                assert (
                    result["status"]
                    == {
                        "absent": "ABSENT",
                        "open": "OBSERVED",
                        "merged": "OBSERVED",
                        "closed": "OBSERVED",
                        "duplicate": "CONFLICT",
                    }[kind]
                )
            else:
                with pytest.raises(ValueError):
                    asyncio.run(module._execute(raw))
            assert len(server.requests) == 2
            assert (
                "state=all" in server.requests[-1][1]
                and "limit=2" in server.requests[-1][1]
            )
        finally:
            value.journal.close()


def test_capability_write_probe_and_repo_identity_gate_before_target(
    tmp_path, keys, server_certificates, monkeypatch
):
    with TLS(
        server_certificates,
        lambda h, b: wire(canonical({"id": 9, "full_name": "owner/repository"})),
    ) as server:
        value = make_case(
            tmp_path, keys, server_certificates, monkeypatch, server.origin
        )
        try:
            monkeypatch.setenv("FJ_TOKEN", TOKEN)
            with pytest.raises(ValueError):
                asyncio.run(module._execute(request_input(value, "pr-create")))
            assert len(server.requests) == 1
        finally:
            value.journal.close()


def test_full_bundle_mutation_refuses_before_credential_send(
    provider_case, monkeypatch
):
    value = provider_case
    (value.directory / value.artifact.bundle_relative_path).write_bytes(
        b"changed-original"
    )
    monkeypatch.setenv("FJ_TOKEN", TOKEN)

    async def forbidden(*args, **kwargs):
        pytest.fail("wire send before original verifier")

    monkeypatch.setattr(module, "_http", forbidden)
    with pytest.raises(ValueError):
        asyncio.run(module._execute(request_input(value, "branch-create")))


def test_closed_child_cli_missing_registry_and_no_private_output(tmp_path):
    # Installed module is absent/unactivated in this qualification environment.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys;sys.path.insert(0,"
            + repr(str(Path(module.__file__).parents[1]))
            + ");from vuoro_reconciler.protected_provider_child import main;sys.exit(main())",
        ],
        input=canonical(
            {"profile_id": "not-installed", "url": "https://attacker.invalid"}
        ),
        capture_output=True,
        timeout=5,
        env={
            "PATH": os.defpath,
            "HOME": str(tmp_path),
            "FJ_TOKEN": "disposable-secret",
        },
    )
    assert result.returncode == 1 and result.stdout == b"" and result.stderr == b""


class SmartGit:
    def __init__(self, root):
        self.root = root
        self.bad_auth = False
        self.git_requests = []

    def __call__(self, h, body):
        if h.path == "/api/v1/repos/owner/repository":
            return wire(canonical({"id": 8, "full_name": "owner/repository"}))
        self.git_requests.append((h.command, h.path))
        if h.headers.get("Authorization") != "token " + TOKEN:
            return wire(status=401)
        if self.bad_auth:
            return wire(
                status=401, headers={"WWW-Authenticate": 'Basic realm="fixture"'}
            )
        path, _, query = h.path.partition("?")
        env = {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "REQUEST_METHOD": h.command,
            "CONTENT_TYPE": h.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": "disposable",
            "REMOTE_ADDR": "127.0.0.1",
        }
        process = subprocess.run(
            [GIT, "http-backend"], input=body, capture_output=True, env=env, timeout=5
        )
        assert process.returncode == 0
        headers, payload = process.stdout.split(b"\r\n\r\n", 1)
        status = 200
        kept = []
        for line in headers.split(b"\r\n"):
            if line.lower().startswith(b"status:"):
                status = int(line.split()[1])
            elif not line.lower().startswith(b"content-length:"):
                kept.append(line)
        return (
            f"HTTP/1.1 {status} Git\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n".encode()
            + b"\r\n".join(kept)
            + b"\r\n\r\n"
            + payload
        )


def test_actual_smart_git_empty_lease_creates_once_then_never_force_updates(
    tmp_path, keys, server_certificates, monkeypatch
):
    serverroot = tmp_path / "server"
    (serverroot / "owner").mkdir(parents=True)
    bare = serverroot / "owner/repository.git"
    run(GIT, "init", "--bare", "--quiet", bare)
    run(GIT, "-C", bare, "config", "http.receivepack", "true")
    backend = SmartGit(serverroot)
    with TLS(server_certificates, backend) as server:
        casepath = tmp_path / "case"
        casepath.mkdir()
        value = make_case(
            casepath, keys, server_certificates, monkeypatch, server.origin
        )
        try:
            # Seed only approved base. Original never exists before protected push.
            run(
                GIT,
                "-C",
                value.repo,
                "push",
                str(bare),
                value.base + ":refs/heads/main",
            )
            raw = request_input(value, "branch-create")
            ready = json.loads(value.artifact.local_ready_utf8)
            monkeypatch.setenv("FJ_TOKEN", TOKEN)
            monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
            result = json.loads(asyncio.run(module._execute(raw)))
            assert (
                result["status"] == "TRANSMISSION_POSSIBLE"
                and result["observation_utf8_b64"] is None
            )
            branch = "refs/heads/vuoro-protected/" + value.record.effect_key
            assert (
                run(GIT, "-C", bare, "rev-parse", branch).strip().decode()
                == ready["commit_oid"]
            )
            # Force the remote to older base; protected attempt must never FF it.
            run(GIT, "-C", bare, "update-ref", branch, value.base)
            monkeypatch.setenv("FJ_TOKEN", TOKEN)
            assert (
                json.loads(asyncio.run(module._execute(raw)))["status"]
                == "TRANSMISSION_POSSIBLE"
            )
            assert (
                run(GIT, "-C", bare, "rev-parse", branch).strip().decode() == value.base
            )
            assert len([q for q in backend.git_requests if q[0] == "POST"]) == 1
            backend.bad_auth = True
            before = len(backend.git_requests)
            monkeypatch.setenv("FJ_TOKEN", TOKEN)
            asyncio.run(module._execute(raw))
            assert len(backend.git_requests) == before + 1  # 401 never auth retry
            gc.collect()
        finally:
            value.journal.close()


def test_actual_https_race_two_distinct_signed_originals_has_one_ref_winner(
    tmp_path, keys, server_certificates, monkeypatch
):
    from dataclasses import replace
    import tempfile
    from vuoro_reconciler import protected_provider as parent

    serverroot = tmp_path / "server"
    (serverroot / "owner").mkdir(parents=True)
    bare = serverroot / "owner/repository.git"
    run(GIT, "init", "--bare", "--quiet", bare)
    run(GIT, "-C", bare, "config", "http.receivepack", "true")
    backend = SmartGit(serverroot)
    with TLS(server_certificates, backend) as server:
        casepath = tmp_path / "case"
        casepath.mkdir()
        value = make_case(
            casepath, keys, server_certificates, monkeypatch, server.origin
        )
        try:
            run(
                GIT,
                "-C",
                value.repo,
                "push",
                str(bare),
                value.base + ":refs/heads/main",
            )
            otherroot = tmp_path / "other-original"
            otherroot.mkdir(mode=0o700)
            other = module.git.ProtectedGit(
                value.profile, otherroot, value.base_provider, value.signer
            )
            bound = replace(value.record, events=(value.record.events[0],))
            original_time = module.git.time.time
            monkeypatch.setattr(module.git.time, "time", lambda: original_time() + 10)
            alternative = asyncio.run(other.prepare_original(value.effect, bound))
            alternative_record = replace(
                bound,
                events=(
                    *bound.events,
                    module.git.JournalEvent(
                        1, "LOCAL_READY", alternative.local_ready_utf8
                    ),
                ),
            )
            first = json.loads(value.artifact.local_ready_utf8)
            second = json.loads(alternative.local_ready_utf8)
            assert (
                first["commit_oid"] != second["commit_oid"]
                and first["tree_oid"] == second["tree_oid"]
            )

            async def race():
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 10
                with (
                    tempfile.TemporaryDirectory() as one,
                    tempfile.TemporaryDirectory() as two,
                ):
                    runners = []
                    requests = []
                    for artifact, record, scratch in (
                        (value.git, value.record, one),
                        (other, alternative_record, two),
                    ):
                        runner = module.git._Runner(scratch, deadline)
                        binding, registration = module.git._record(
                            value.effect, record, value.profile
                        )
                        local = json.loads(record.events[1].payload_utf8)
                        a, b, c, _ = artifact._artifact_directory(record.effect_key)
                        try:
                            await artifact._restore(
                                runner,
                                value.effect,
                                record,
                                binding,
                                registration,
                                (a, b, c),
                                local,
                            )
                        finally:
                            for descriptor in (c, b, a):
                                os.close(descriptor)
                        runners.append(runner)
                        requests.append(
                            json.loads(
                                parent._request(
                                    record,
                                    value.effect,
                                    binding,
                                    local,
                                    "branch-create",
                                )
                            )
                        )
                    await asyncio.gather(
                        *(
                            module._push(
                                value.provider_profile.data,
                                TOKEN,
                                runner,
                                request,
                                deadline,
                            )
                            for runner, request in zip(runners, requests)
                        )
                    )

            asyncio.run(race())
            winner = (
                run(
                    GIT,
                    "-C",
                    bare,
                    "rev-parse",
                    "refs/heads/vuoro-protected/" + value.record.effect_key,
                )
                .strip()
                .decode()
            )
            assert winner in (first["commit_oid"], second["commit_oid"])
            assert len([r for r in backend.git_requests if r[0] == "POST"]) in (1, 2)
            assert len(backend.git_requests) <= 4
        finally:
            value.journal.close()


def child_driver(value, launch=False):
    # Test-only process harness emulates installed identity/local durable FS.
    # Product CLI exposes no source, home, credential, or filesystem selector.
    from test_protected_provider_registry import credctl_admission_bootstrap

    source = str(Path(module.__file__).parents[1])
    home = str(value.registry_path.parents[3])
    bootstrap = (
        "import sys,types,os;sys.path.insert(0," + repr(source) + ");"
        "from vuoro_reconciler import protected_provider_registry as registry;"
        "registry.pwd.getpwuid=lambda uid:types.SimpleNamespace(pw_dir="
        + repr(home)
        + ");"
        + (
            ""
            if os.environ.get("Q4_PROVIDER_REAL_STATE_FS") == "1"
            else "realfs=registry._filesystem;registry._filesystem=lambda fd:realfs(fd) if os.readlink('/proc/self/fd/'+str(fd)).startswith('/sys/fs/cgroup/') else 'ext4';"
        )
        + credctl_admission_bootstrap(value.registry_path.parents[4])
        + "from vuoro_reconciler import protected_provider_child as child;"
    )
    if launch:
        # Inspect the fixed packaged broker command, then substitute only the
        # test credential injection. Actual broker activation is not performed.
        bootstrap += (
            "realexec=os.execve;"
            "def_unused=0;\n"
            "def fixture_exec(path,args,env):\n"
            " import fcntl,errno\n"
            " assert fcntl.fcntl(0,1034)==15\n"
            " try:os.write(0,b'x')\n"
            " except OSError as error:assert error.errno==errno.EPERM\n"
            " else:raise AssertionError('unsealed stdin')\n"
            " assert path=='/run/current-system/sw/bin/credctl' and args[1]=='exec'\n"
            " assert set(env)=={'PATH','HOME','XDG_CONFIG_HOME','LANG','LC_ALL'}\n"
            " assert args[2] in ('repo.read','repo.write') and args[3:6]==['--repository','forgejo:owner/repository','--']\n"
            " assert args[-3:]==['-I','-m','vuoro_reconciler.protected_provider_child']\n"
            " env=dict(env,FJ_TOKEN=" + repr(TOKEN) + ")\n"
            " realexec(sys.executable,[sys.executable,'-I','-c',"
            + repr(child_driver(value))
            + "],env)\n"
            "os.execve=fixture_exec\n"
        )
    return bootstrap + "sys.exit(child.main())"


def run_child(value, raw, timeout=10):
    if not os.environ.get("Q4_PROVIDER_CGROUP_FIXTURE_ROOT"):
        pytest.skip("actual delegated cgroup fixture required")
    from vuoro_reconciler.protected_provider import _OperationGroup

    group = _OperationGroup(value.provider_profile)
    from vuoro_reconciler.protected_provider_registry import (
        _state_paths,
        _active_record,
    )

    directory, _, active = _state_paths(value.provider_profile.profile_id)
    active_path = directory / active
    active_path.write_bytes(
        _active_record(value.provider_profile, group.name.removeprefix("operation-"))
    )
    active_path.chmod(0o600)
    with active_path.open("rb") as stream:
        os.fsync(stream.fileno())
    state_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(state_fd)
    os.close(state_fd)
    try:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                child_driver(value, launch=True),
                "--launch",
                str(group.procs_fd),
            ],
            input=raw,
            capture_output=True,
            timeout=timeout,
            pass_fds=(group.procs_fd,),
            env={
                "PATH": os.defpath,
                "HOME": "/ambient/untrusted",
                "HTTPS_PROXY": "http://127.0.0.1:1",
            },
        )
    finally:
        group.kill()
        group.close()
        active_path.unlink()


@pytest.mark.parametrize("operation", ["capability-check", "pr-create"])
def test_actual_closed_public_child_stdin_tls_and_once_only_ack(
    tmp_path, keys, server_certificates, monkeypatch, operation
):
    holder = {}

    def handler(h, body):
        if h.path == "/api/v1/repos/owner/repository":
            return wire(canonical({"id": 8, "full_name": "owner/repository"}))
        assert operation == "pr-create" and h.command == "POST"
        supplied = json.loads(body)
        request = holder["request"]
        assert set(supplied) == {"head", "base", "title", "body"}
        assert (
            supplied["title"] == request["title"]
            and supplied["body"]
            == __import__("base64").b64decode(request["body_utf8_b64"]).decode()
        )
        return wire(canonical(pr_row(request, holder["data"])), 201, chunked=True)

    with TLS(server_certificates, handler) as server:
        value = make_case(
            tmp_path, keys, server_certificates, monkeypatch, server.origin
        )
        try:
            raw = request_input(value, operation)
            _, _, _, request, _ = _validate_input(raw, value.provider_profile)
            holder.update(request=request, data=value.provider_profile.data)
            result = run_child(value, raw)
            assert result.returncode == 0 and result.stderr == b""
            response = json.loads(result.stdout)
            assert response["status"] == (
                "CAPABILITY_PRESENT"
                if operation == "capability-check"
                else "TRANSMISSION_POSSIBLE"
            )
            assert response["observation_utf8_b64"] is None
            assert len(server.requests) == (1 if operation == "capability-check" else 2)
            assert (
                TOKEN.encode() not in result.stdout
                and str(value.directory).encode() not in result.stdout
            )
        finally:
            value.journal.close()


def test_actual_child_fixed_fifteen_second_header_deadline_no_retry(
    tmp_path, keys, server_certificates, monkeypatch
):
    with TLS(
        server_certificates, lambda h, b: [(b"HTTP/1.1 2", 17), (b"00 OK\r\n\r\n", 0)]
    ) as server:
        value = make_case(
            tmp_path, keys, server_certificates, monkeypatch, server.origin
        )
        try:
            started = time.monotonic()
            result = run_child(
                value, request_input(value, "capability-check"), timeout=18
            )
            elapsed = time.monotonic() - started
            assert (
                15 <= elapsed < 17
                and result.returncode == 1
                and result.stdout == result.stderr == b""
            )
            assert len(server.requests) == 1
        finally:
            value.journal.close()


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_actual_child_push_cleanup_close_failure_still_reaps(
    tmp_path, monkeypatch, server_certificates, pipe
):
    from types import SimpleNamespace
    from vuoro_reconciler import protected_provider as parent

    captured = []
    original_init = parent._PipeOwner.__init__

    class FailedClose:
        def __init__(self, original):
            self.original = original

        def close(self):
            raise OSError("disposable persistent push close failure")

        def __getattr__(self, name):
            return getattr(self.original, name)

    def capture(owner, process):
        original_init(owner, process)
        original = getattr(process, pipe)
        captured.append((owner, process, original))

    monkeypatch.setattr(parent._PipeOwner, "__init__", capture)

    async def spawn(*args, **kwargs):
        # A real process isolates this cleanup seam; separate HTTPS smart Git
        # fixtures qualify the unchanged wire/create-only command itself.
        process = await parent._spawn(
            sys.executable, "-c", "import time;time.sleep(30)", **kwargs
        )
        setattr(process._process, pipe, FailedClose(captured[0][2]))
        return process

    monkeypatch.setattr(module, "_spawn", spawn)
    runner = SimpleNamespace(scratch=tmp_path, env=lambda: {"PATH": os.defpath})
    profile = {
        "https_origin": "https://forgejo.invalid:443",
        "numeric_ip": "127.0.0.1",
        "owner": "owner",
        "name": "repository",
        "git_path": GIT,
        "ca_der_b64": [
            __import__("base64")
            .b64encode(
                ssl.PEM_cert_to_DER_cert((server_certificates / "ca.pem").read_text())
            )
            .decode()
        ],
    }

    async def workflow():
        task = asyncio.create_task(
            module._push(
                profile,
                TOKEN,
                runner,
                {"branch": "protected-original", "commit_oid": "a" * 40},
                asyncio.get_running_loop().time() + 5,
            )
        )
        for _ in range(100):
            if captured:
                break
            await asyncio.sleep(0.005)
        assert captured
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner, raw_process, original = captured[0]
        assert raw_process.returncode is not None and not owner.closed
        assert not Path("/proc/" + str(raw_process.pid)).exists()
        with pytest.raises(ChildProcessError):
            os.waitpid(raw_process.pid, os.WNOHANG)
        setattr(raw_process, pipe, original)
        owner.close()
        await asyncio.sleep(0)
        gc.collect()

    try:
        asyncio.run(workflow())
        gc.collect()
    finally:
        for owner, raw_process, original in captured:
            setattr(raw_process, pipe, original)
            if raw_process.poll() is None:
                raw_process.kill()
            raw_process.wait(timeout=5)
            owner.close()
