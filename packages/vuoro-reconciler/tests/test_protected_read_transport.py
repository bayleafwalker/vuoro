"""Actual disposable loopback mTLS, framing, total-deadline and closure tests."""

import asyncio
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess

import pytest

from vuoro_reconciler import protected_read_transport as module
from vuoro_reconciler.protected_read_transport import (
    PrivateReadTransport,
    ProtectedReadRefused,
    ReadCredentials,
    ReadoutProfile,
)
from test_protected_copy_codec import FIXTURE, canonical, decode, sha

TOKEN = "disposable.test+/token=="
NAMESPACE = json.loads(FIXTURE.read_bytes())["locator"]["namespace"]
ACCEPTANCE_ID = decode().acceptance.acceptance_id
REPOSITORY = decode().repository


def command(*args):
    result = subprocess.run(
        ["openssl", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "OPENSSL_CONF": "/dev/null"},
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="session")
def certificates(tmp_path_factory):
    assert shutil.which("openssl"), "disposable TLS qualification requires openssl"
    root = tmp_path_factory.mktemp("private-read-certificates")
    for name in ("ca", "wrong-ca"):
        command(
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-keyout",
            str(root / (name + ".key")),
            "-out",
            str(root / (name + ".pem")),
            "-days",
            "1",
            "-subj",
            "/CN=Disposable " + name,
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        )
    for name, eku in (("server", "serverAuth"), ("client", "clientAuth")):
        command(
            "req",
            "-new",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-keyout",
            str(root / (name + ".key")),
            "-out",
            str(root / (name + ".csr")),
            "-subj",
            "/CN=localhost" if name == "server" else "/CN=Disposable client",
        )
        ext = root / (name + ".ext")
        ext.write_text(
            "basicConstraints=CA:FALSE\nextendedKeyUsage="
            + eku
            + (
                "\nsubjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1\n"
                if name == "server"
                else "\n"
            )
        )
        command(
            "x509",
            "-req",
            "-in",
            str(root / (name + ".csr")),
            "-CA",
            str(root / "ca.pem"),
            "-CAkey",
            str(root / "ca.key"),
            "-CAcreateserial",
            "-out",
            str(root / (name + ".pem")),
            "-days",
            "1",
            "-extfile",
            str(ext),
        )
    return root


def client_context(certificates, *, ca="ca", client=True):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(certificates / (ca + ".pem"))
    if client:
        context.load_cert_chain(
            certificates / "client.pem", certificates / "client.key"
        )
    context.set_alpn_protocols(["http/1.1"])
    return context


def server_context(certificates):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(certificates / "server.pem", certificates / "server.key")
    context.load_verify_locations(certificates / "ca.pem")
    context.verify_mode = ssl.CERT_REQUIRED
    context.set_alpn_protocols(["http/1.1"])
    return context


def ca_hash(certificates, name="ca"):
    return sha(ssl.PEM_cert_to_DER_cert((certificates / (name + ".pem")).read_text()))


def profile(port=443, *, certificates, **changes):
    values = dict(
        namespace_utf8=canonical(NAMESPACE),
        repository=REPOSITORY,
        https_origin=f"https://localhost:{port}",
        connect_ip="127.0.0.1",
        ca_der_sha256=(ca_hash(certificates),),
    )
    values.update(changes)
    return ReadoutProfile(**values)


class Credentials:
    def __init__(self, context, *, delay=0, token=TOKEN, fail=False):
        self.context, self.delay, self.token, self.fail = context, delay, token, fail
        self.calls = []

    async def acquire(self, value):
        self.calls.append(value)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("secret-looking-upstream-error-" + self.token)
        return ReadCredentials(self.token, self.context)


def response(body=b"{}", *, status=b"HTTP/1.1 200 OK", headers=None):
    fields = (
        [
            b"Content-Length: " + str(len(body)).encode(),
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
        if headers is None
        else headers
    )
    return status + b"\r\n" + b"\r\n".join(fields) + b"\r\n\r\n" + body


class Loopback:
    """Server only in tests; requires actual disposable client certificate."""

    def __init__(self, certificates, responder=None, *, tls=True, host="127.0.0.1"):
        self.context = server_context(certificates) if tls else None
        self.host = host
        self.responder = responder
        self.requests = []
        self.peers_closed = []
        self.tasks = set()

    async def __aenter__(self):
        self.server = await asyncio.start_server(
            self.handle, self.host, 0, ssl=self.context
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        peer_closed = False
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.split(b"\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in lines
                if line.lower().startswith(b"content-length:")
            )
            body = await reader.readexactly(length)
            tls = writer.get_extra_info("ssl_object")
            self.requests.append(
                (header, body, None if tls is None else tls.getpeercert())
            )
            if self.responder is None:
                writer.write(response())
                await writer.drain()
            else:
                await self.responder(reader, writer)
            if not writer.is_closing():
                writer.close()
            try:
                await writer.wait_closed()
                peer_closed = True
            except (OSError, ssl.SSLError):
                peer_closed = True
        except (OSError, ssl.SSLError, asyncio.IncompleteReadError):
            peer_closed = True
        finally:
            writer.transport.abort()
            self.peers_closed.append(peer_closed)
            self.tasks.discard(task)


def test_exact_requests_fresh_credentials_numeric_tls_and_proxy_ignored(
    certificates, monkeypatch
):
    async def run():
        context = client_context(certificates)
        credentials = Credentials(context)
        trap_hits = []

        async def trap(reader, writer):
            trap_hits.append(await reader.read(100))
            writer.close()

        trap_server = await asyncio.start_server(trap, "127.0.0.1", 0)
        try:
            port = trap_server.sockets[0].getsockname()[1]
            for name in (
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "ALL_PROXY",
                "https_proxy",
                "http_proxy",
                "all_proxy",
            ):
                monkeypatch.setenv(name, f"http://127.0.0.1:{port}")
            async with Loopback(certificates) as server:
                chosen = profile(server.port, certificates=certificates)
                transport = PrivateReadTransport(chosen, credentials)

                def resolver(*args, **kwargs):
                    raise AssertionError("numeric transport must not use DNS resolver")

                monkeypatch.setattr(socket, "getaddrinfo", resolver)
                assert await transport.list_accepted() == b"{}"
                assert await transport.read_copy(ACCEPTANCE_ID) == b"{}"
                assert credentials.calls == [chosen, chosen]
                assert len(server.requests) == 2 and all(
                    row[2] for row in server.requests
                )
                assert all(
                    b"X-Vuoro-Client-Protocol:" not in row[0]
                    for row in server.requests
                )
                head, body, _ = server.requests[0]
                assert head.startswith(
                    b"POST /v1/protected-effects/list-accepted HTTP/1.1\r\n"
                )
                assert b"Authorization: Bearer " + TOKEN.encode() + b"\r\n" in head
                assert json.loads(body) == {
                    "schema": "cred-broker-protected-effect-list-accepted-request/v1",
                    "namespace": NAMESPACE,
                    "cursor": None,
                    "limit": 64,
                }
                assert json.loads(server.requests[1][1]) == {
                    "schema": "cred-broker-protected-effect-read-copy-request/v1",
                    "namespace": NAMESPACE,
                    "acceptance_id": ACCEPTANCE_ID,
                }
                assert not trap_hits
                with pytest.raises(AttributeError):
                    transport.profile = chosen
        finally:
            trap_server.close()
            await trap_server.wait_closed()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"https_origin": "http://localhost"},
        {"https_origin": "https://user:secret@localhost"},
        {"https_origin": "https://localhost/"},
        {"https_origin": "https://localhost?secret=x"},
        {"https_origin": "https://localhost#fragment"},
        {"https_origin": "https://localhost?"},
        {"https_origin": "https://localhost#"},
        {"https_origin": "https://local\r\nhost"},
        {"https_origin": "https://localhost:0"},
        {"https_origin": "https://localhost:65536"},
        {"https_origin": "https://localhost:"},
        {"https_origin": "https://élève.example"},
        {"connect_ip": "localhost"},
        {"connect_ip": "127.0.0.1\n"},
        {"ca_der_sha256": ()},
        {"ca_der_sha256": ["sha256:" + "0" * 64]},
        {"namespace_utf8": b'{"unknown":1}'},
        {"repository": ""},
    ],
)
def test_profile_closed_namespace_origin_numeric_ip_and_ca_types(certificates, kwargs):
    with pytest.raises(ProtectedReadRefused, match="^protected-read-refused$") as error:
        profile(certificates=certificates, **kwargs)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert "secret" not in str(error.value)


def test_ipv6_profile_authority_and_ca_unique_hashes(certificates):
    value = profile(
        certificates=certificates, https_origin="https://[::1]:8443", connect_ip="::1"
    )
    assert module._origin(value.https_origin) == ("::1", 8443, "[::1]:8443")
    assert ipaddress.ip_address(value.connect_ip).version == 6
    with pytest.raises(ProtectedReadRefused):
        profile(
            certificates=certificates,
            ca_der_sha256=(ca_hash(certificates), ca_hash(certificates)),
        )


@pytest.mark.parametrize(
    "operation,args",
    [
        ("list_accepted", {"limit": True}),
        ("list_accepted", {"limit": 0}),
        ("list_accepted", {"limit": 65}),
        ("list_accepted", {"cursor": "bad="}),
        ("list_accepted", {"cursor": "!"}),
        ("read_copy", {"acceptance_id": "caller-label"}),
        ("read_copy", {"acceptance_id": "iac_" + "F" * 32}),
    ],
)
def test_request_invalid_before_credentials_and_network(certificates, operation, args):
    async def run():
        credentials = Credentials(client_context(certificates))
        transport = PrivateReadTransport(
            profile(certificates=certificates), credentials
        )
        with pytest.raises(ProtectedReadRefused):
            await getattr(transport, operation)(**args)
        assert credentials.calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        "wrong-ca",
        "wrong-host",
        "no-client",
        "extra-ca",
        "unverified",
        "tls12",
        "bad-token",
        "provider-error",
    ],
)
def test_actual_authentication_or_pins_fail_without_data_leak(certificates, kind):
    async def run():
        async with Loopback(certificates) as server:
            context = client_context(
                certificates,
                ca="wrong-ca" if kind == "wrong-ca" else "ca",
                client=kind != "no-client",
            )
            chosen = profile(
                server.port,
                certificates=certificates,
                https_origin=f"https://wrong.example:{server.port}"
                if kind == "wrong-host"
                else f"https://localhost:{server.port}",
                ca_der_sha256=(ca_hash(certificates, "wrong-ca"),)
                if kind == "wrong-ca"
                else (ca_hash(certificates),),
            )
            if kind == "extra-ca":
                context.load_verify_locations(certificates / "wrong-ca.pem")
            elif kind == "unverified":
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            elif kind == "tls12":
                context.minimum_version = ssl.TLSVersion.TLSv1_2
            credentials = Credentials(
                context,
                token=TOKEN + "\r\nInjected: credential"
                if kind == "bad-token"
                else TOKEN,
                fail=kind == "provider-error",
            )
            transport = PrivateReadTransport(chosen, credentials)
            with pytest.raises(
                ProtectedReadRefused, match="^protected-read-refused$"
            ) as error:
                await transport.read_copy(ACCEPTANCE_ID)
            assert error.value.__cause__ is None and error.value.__context__ is None
            assert TOKEN not in str(error.value) and "secret-looking" not in repr(
                error.value
            )
            assert server.requests == []

    asyncio.run(run())


BAD_RESPONSES = [
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"content-type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b"X-Name: a",
            b"x-name: b",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 02",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response(
        headers=[
            b"Content-Length: +2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b"Transfer-Encoding: identity",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b"Content-Encoding: identity",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b" Folded: invalid",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b"X-Name: a\x00b",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b"X-Name: a\tb",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json; charset=ascii",
            b"Cache-Control: no-store",
        ]
    ),
    response(headers=[b"Content-Length: 2", b"Content-Type: application/json"]),
    response(headers=[b"Content-Type: application/json", b"Cache-Control: no-store"]),
    response(
        headers=[
            b"Content-Length: 0",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response(
        headers=[
            b"Content-Length: 3",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response() + b"HTTP/1.1 200 OK\r\n\r\n",
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
            b"X-Long: " + b"a" * 8192,
        ]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
        + [f"X-{i}: x".encode() for i in range(64)]
    ),
    response(
        headers=[
            b"Content-Length: 2",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
        + [f"X-{i}: ".encode() + b"a" * 7000 for i in range(10)]
    ),
    response(
        headers=[
            b"Content-Length: 131073",
            b"Content-Type: application/json",
            b"Cache-Control: no-store",
        ]
    ),
    response(status=b"HTTP/2 200 OK"),
    response(status=b"HTTP/1.1 401 secret-looking-error"),
]


@pytest.mark.parametrize("wire", BAD_RESPONSES)
def test_actual_bad_http_framing_fixed_refusal(certificates, wire):
    async def run():
        async def emit(reader, writer):
            writer.write(wire)
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(
                ProtectedReadRefused, match="^protected-read-refused$"
            ) as error:
                await transport.list_accepted()
            assert error.value.__cause__ is None and error.value.__context__ is None
            assert TOKEN not in str(error.value)

    asyncio.run(run())


def test_declared_length_eof_and_budgets_exact_boundary(certificates):
    async def run():
        body = b"x" * (128 * 1024)

        async def emit(reader, writer):
            writer.write(response(body))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            assert await transport.list_accepted() == body

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["header", "body", "eof", "credential"])
def test_actual_byte_drip_and_acquisition_share_hard_total_deadline(
    certificates, monkeypatch, stage
):
    monkeypatch.setattr(module, "_TOTAL_TIMEOUT_SECONDS", 0.25)

    async def run():
        async def drip(reader, writer):
            if stage == "header":
                data = response()
                for byte in data:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(0.04)
            elif stage == "body":
                writer.write(response(b"0123456789")[:-10])
                await writer.drain()
                for byte in b"0123456789":
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(0.04)
            elif stage == "eof":
                writer.write(response())
                await writer.drain()
                await reader.read()
            else:
                writer.write(response())
                await writer.drain()

        async with Loopback(certificates, drip) as server:
            credentials = Credentials(
                client_context(certificates),
                delay=0.5 if stage == "credential" else 0.05,
            )
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates), credentials
            )
            start = asyncio.get_running_loop().time()
            with pytest.raises(ProtectedReadRefused):
                await transport.list_accepted()
            elapsed = asyncio.get_running_loop().time() - start
            assert 0.20 <= elapsed < 0.65
            if stage == "credential":
                assert server.requests == []
            else:
                # The client has aborted; the server's next read sees EOF/reset.
                for _ in range(100):
                    if server.peers_closed:
                        break
                    await asyncio.sleep(0.005)
                assert server.peers_closed == [True]

    asyncio.run(run())


def test_actual_incomplete_tls_handshake_total_deadline_and_peer_closed(
    certificates, monkeypatch
):
    monkeypatch.setattr(module, "_TOTAL_TIMEOUT_SECONDS", 0.20)

    async def run():
        closed = asyncio.Event()
        tasks = set()

        async def raw(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                while await reader.read(65536):
                    pass
                closed.set()
            finally:
                writer.transport.abort()
                tasks.discard(task)

        server = await asyncio.start_server(raw, "127.0.0.1", 0)
        try:
            chosen = profile(
                server.sockets[0].getsockname()[1], certificates=certificates
            )
            transport = PrivateReadTransport(
                chosen, Credentials(client_context(certificates))
            )
            start = asyncio.get_running_loop().time()
            with pytest.raises(ProtectedReadRefused):
                await transport.list_accepted()
            assert asyncio.get_running_loop().time() - start < 0.65
            await asyncio.wait_for(closed.wait(), 0.5)
        finally:
            server.close()
            await server.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*list(tasks), return_exceptions=True)

    asyncio.run(run())


def test_success_refusal_and_external_cancellation_abort_actual_socket(certificates):
    async def run():
        async def stalled(reader, writer):
            await reader.read()

        async with Loopback(certificates, stalled) as server:
            credentials = Credentials(client_context(certificates))
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates), credentials
            )
            task = asyncio.create_task(transport.list_accepted())
            for _ in range(100):
                if server.requests:
                    break
                await asyncio.sleep(0.005)
            assert server.requests
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(100):
                if server.peers_closed:
                    break
                await asyncio.sleep(0.005)
            assert server.peers_closed == [True]

    asyncio.run(run())


def test_redirect_destination_gets_no_request_or_credentials(certificates):
    async def run():
        async with Loopback(certificates) as destination:

            async def redirect(reader, writer):
                writer.write(
                    response(
                        status=b"HTTP/1.1 302 Found",
                        headers=[
                            b"Content-Length: 2",
                            b"Content-Type: application/json",
                            b"Cache-Control: no-store",
                            f"Location: https://localhost:{destination.port}/".encode(),
                        ],
                    )
                )
                await writer.drain()

            async with Loopback(certificates, redirect) as server:
                transport = PrivateReadTransport(
                    profile(server.port, certificates=certificates),
                    Credentials(client_context(certificates)),
                )
                with pytest.raises(ProtectedReadRefused):
                    await transport.read_copy(ACCEPTANCE_ID)
                assert destination.requests == []

    asyncio.run(run())


def test_actual_ipv6_numeric_connect_and_bracketed_host(certificates, monkeypatch):
    async def run():
        async with Loopback(certificates, host="::1") as server:
            chosen = profile(
                server.port,
                certificates=certificates,
                https_origin=f"https://[::1]:{server.port}",
                connect_ip="::1",
            )
            transport = PrivateReadTransport(
                chosen, Credentials(client_context(certificates))
            )

            def forbidden(*args, **kwargs):
                raise AssertionError("IPv6 literal must bypass DNS")

            monkeypatch.setattr(socket, "getaddrinfo", forbidden)
            assert await transport.list_accepted() == b"{}"
            assert f"Host: [::1]:{server.port}\r\n".encode() in server.requests[0][0]

    asyncio.run(run())


def test_copy_specific_eight_mib_wire_limit_and_success_eof_peer_closure(certificates):
    async def run():
        body = b"x" * (8 * 1024 * 1024)

        async def emit(reader, writer):
            writer.write(response(body))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            assert len(await transport.read_copy(ACCEPTANCE_ID)) == len(body)
            for _ in range(100):
                if server.peers_closed:
                    break
                await asyncio.sleep(0.005)
            assert server.peers_closed == [True]

        async def oversized(reader, writer):
            writer.write(
                response(
                    headers=[
                        b"Content-Length: 8388609",
                        b"Content-Type: application/json",
                        b"Cache-Control: no-store",
                    ]
                )
            )
            await writer.drain()

        async with Loopback(certificates, oversized) as server:
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await transport.read_copy(ACCEPTANCE_ID)
            for _ in range(100):
                if server.peers_closed:
                    break
                await asyncio.sleep(0.005)
            assert server.peers_closed == [True]

    asyncio.run(run())


def test_actual_negotiated_http2_alpn_is_refused(certificates):
    async def run():
        async with Loopback(certificates) as server:
            server.context.set_alpn_protocols(["h2"])
            context = client_context(certificates)
            context.set_alpn_protocols(["h2"])
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates), Credentials(context)
            )
            with pytest.raises(ProtectedReadRefused):
                await transport.list_accepted()
            assert server.requests == []

    asyncio.run(run())


def test_combined_credential_headers_body_deadline_never_restarts_between_phases(
    certificates, monkeypatch
):
    monkeypatch.setattr(module, "_TOTAL_TIMEOUT_SECONDS", 0.25)

    async def run():
        closed = asyncio.Event()

        async def split(reader, writer):
            await asyncio.sleep(0.09)
            writer.write(response()[:-2])
            await writer.drain()
            # Each phase by itself is below the total budget. Together they exceed it.
            await asyncio.sleep(0.09)
            writer.write(b"{}")
            try:
                await writer.drain()
                await reader.read()
            except OSError:
                pass
            closed.set()

        async with Loopback(certificates, split) as server:
            credentials = Credentials(client_context(certificates), delay=0.10)
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates), credentials
            )
            start = asyncio.get_running_loop().time()
            with pytest.raises(ProtectedReadRefused):
                await transport.list_accepted()
            elapsed = asyncio.get_running_loop().time() - start
            assert 0.20 <= elapsed < 0.65
            await asyncio.wait_for(closed.wait(), 0.5)
            assert len(server.requests) == 1

    asyncio.run(run())


def test_actual_framing_refusal_aborts_peer_waiting_for_eof(certificates):
    async def run():
        closed = asyncio.Event()

        async def malformed(reader, writer):
            writer.write(
                response(
                    headers=[
                        b"Content-Length: 2",
                        b"Content-Length: 2",
                        b"Content-Type: application/json",
                        b"Cache-Control: no-store",
                    ]
                )
            )
            await writer.drain()
            try:
                assert await reader.read(1) == b""
            except OSError:
                pass
            closed.set()

        async with Loopback(certificates, malformed) as server:
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await transport.list_accepted()
            await asyncio.wait_for(closed.wait(), 0.5)

    asyncio.run(run())


def test_actual_tls12_only_server_is_refused_before_request(certificates):
    async def run():
        async with Loopback(certificates) as server:
            server.context.minimum_version = ssl.TLSVersion.TLSv1_2
            server.context.maximum_version = ssl.TLSVersion.TLSv1_2
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await transport.list_accepted()
            assert server.requests == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "operation,args",
    [("list_accepted", {}), ("read_copy", {"acceptance_id": ACCEPTANCE_ID})],
)
def test_d1_no_store_mandatory_even_after_distinct_native_wire_policy(
    certificates, operation, args
):
    async def run():
        async def emit(reader, writer):
            writer.write(
                response(
                    headers=[b"Content-Length: 2", b"Content-Type: application/json"]
                )
            )
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            transport = PrivateReadTransport(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await getattr(transport, operation)(**args)

    asyncio.run(run())
