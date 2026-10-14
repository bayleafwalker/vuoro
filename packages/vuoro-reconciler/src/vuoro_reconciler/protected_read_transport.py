"""Bounded private read delivery; profiles and returned bytes are DATA only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import wraps
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit

from .protected_copy_codec import (
    COPY_MAX,
    LIST_MAX,
    _canonical,
    _cursor,
    _digest,
    _hash,
    _id,
    _namespace_bytes,
    _need,
    _text,
)

_TOTAL_TIMEOUT_SECONDS = 15.0
_LIST_PATH = "/v1/protected-effects/list-accepted"
_COPY_PATH = "/v1/protected-effects/read-copy"
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class ProtectedReadRefused(ValueError):
    """Fixed metadata only; no upstream body, credential or exception chain."""


def _fixed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        failed = False
        try:
            return function(*args, **kwargs)
        except Exception:
            failed = True
        if failed:
            raise ProtectedReadRefused("protected-read-refused")

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
            raise ProtectedReadRefused("protected-read-refused")

    return checked


def _origin(value):
    _text(value, 2048)
    _need(value.isascii() and not any(ord(c) <= 32 or ord(c) == 127 for c in value))
    _need("?" not in value and "#" not in value)
    origin = urlsplit(value)
    _need(
        origin.scheme == "https"
        and origin.hostname
        and not origin.path
        and not origin.query
        and not origin.fragment
    )
    _need(
        origin.username is None and origin.password is None and "@" not in origin.netloc
    )
    hostname = origin.hostname
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        _need(
            len(hostname) <= 253
            and all(_HOST_LABEL.fullmatch(label) for label in hostname.split("."))
        )
    else:
        _need("%" not in hostname)
        if address.version == 6:
            _need(origin.netloc.startswith("["))
    port = origin.port or 443
    _need(1 <= port <= 65535 and origin.port != 0)
    # Reconstruct authority to reject an empty port, stray colon or URL syntax.
    host = "[" + hostname + "]" if ":" in hostname else hostname
    authority = host + (":" + str(origin.port) if origin.port is not None else "")
    _need(origin.netloc.lower() == authority.lower())
    return hostname, port, authority


@dataclass(frozen=True, slots=True, repr=False)
class ReadoutProfile:
    namespace_utf8: bytes
    repository: str
    https_origin: str
    connect_ip: str
    ca_der_sha256: tuple[str, ...]

    @_fixed
    def __post_init__(self):
        _namespace_bytes(self.namespace_utf8)
        _text(self.repository, 200)
        _origin(self.https_origin)
        _text(self.connect_ip, 64)
        _need("%" not in self.connect_ip)
        ipaddress.ip_address(self.connect_ip)
        _need(type(self.ca_der_sha256) is tuple and 1 <= len(self.ca_der_sha256) <= 8)
        for digest in self.ca_der_sha256:
            _digest(digest)
        _need(len(set(self.ca_der_sha256)) == len(self.ca_der_sha256))


@dataclass(frozen=True, slots=True, repr=False)
class ReadCredentials:
    """Transient private provider result; never returned through ledger APIs."""

    bearer_ascii: str
    ssl_context: ssl.SSLContext


def _credentials(value, profile):
    _need(type(value) is ReadCredentials)
    _text(value.bearer_ascii, 16384)
    _need(_TOKEN.fullmatch(value.bearer_ascii))
    context = value.ssl_context
    _need(isinstance(context, ssl.SSLContext))
    _need(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True)
    _need(context.minimum_version >= ssl.TLSVersion.TLSv1_3)
    trusted = context.get_ca_certs(binary_form=True)
    _need(len(trusted) == len(profile.ca_der_sha256))
    _need({_hash(der) for der in trusted} == set(profile.ca_der_sha256))
    return value


async def _headers(reader):
    total = 0

    async def line():
        nonlocal total
        raw = await reader.readuntil(b"\r\n")
        _need(len(raw) <= 8192)
        total += len(raw)
        _need(total <= 65536)
        _need(not any(c < 32 or c == 127 for c in raw[:-2]))
        return raw[:-2]

    status = await line()
    _need(re.fullmatch(rb"HTTP/1\.[01] 200(?: [\x20-\x7e]*)?", status))
    fields = {}
    for _ in range(65):
        raw = await line()
        if raw == b"":
            break
        _need(len(fields) < 64 and not raw.startswith((b" ", b"\t")) and b":" in raw)
        name, value = raw.split(b":", 1)
        _need(_HEADER_NAME.fullmatch(name))
        name = name.lower()
        _need(name not in fields)
        fields[name] = value.strip(b" ").lower()
    else:
        _need(False)
    _need(b"transfer-encoding" not in fields and b"content-encoding" not in fields)
    length = fields.get(b"content-length", b"")
    _need(re.fullmatch(rb"0|[1-9][0-9]*", length) and len(length) <= 10)
    _need(
        fields.get(b"content-type")
        in (
            b"application/json",
            b"application/json; charset=utf-8",
            b"application/json;charset=utf-8",
        )
    )
    _need(fields.get(b"cache-control") == b"no-store")
    return int(length)


class PrivateReadTransport:
    """Two fixed read operations; no generic URL/header/path or mutation API."""

    @_fixed
    def __init__(self, profile, credential_provider):
        _need(
            type(profile) is ReadoutProfile
            and callable(getattr(credential_provider, "acquire", None))
        )
        profile.__post_init__()
        self._profile = profile
        self._credential_provider = credential_provider

    @property
    def profile(self):
        return self._profile

    @_async_fixed
    async def list_accepted(self, cursor=None, limit=64):
        namespace = _namespace_bytes(self.profile.namespace_utf8)
        _need(type(limit) is int and 1 <= limit <= 64)
        if cursor is not None:
            _cursor(cursor, namespace)
        request = {
            "schema": "cred-broker-protected-effect-list-accepted-request/v1",
            "namespace": namespace,
            "cursor": cursor,
            "limit": limit,
        }
        return await self._exchange(_LIST_PATH, _canonical(request), LIST_MAX)

    @_async_fixed
    async def read_copy(self, acceptance_id):
        _id(acceptance_id, "iac_")
        request = {
            "schema": "cred-broker-protected-effect-read-copy-request/v1",
            "namespace": _namespace_bytes(self.profile.namespace_utf8),
            "acceptance_id": acceptance_id,
        }
        return await self._exchange(_COPY_PATH, _canonical(request), COPY_MAX)

    async def _exchange(self, path, request_utf8, maximum):
        _need(path in (_LIST_PATH, _COPY_PATH) and len(request_utf8) <= 65536)
        writer = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _TOTAL_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                credential = _credentials(
                    await self._credential_provider.acquire(self.profile), self.profile
                )
                _need(loop.time() < deadline)
                hostname, port, authority = _origin(self.profile.https_origin)
                address = ipaddress.ip_address(self.profile.connect_ip)
                reader, writer = await asyncio.open_connection(
                    host=self.profile.connect_ip,
                    port=port,
                    family=socket.AF_INET if address.version == 4 else socket.AF_INET6,
                    flags=socket.AI_NUMERICHOST,
                    ssl=credential.ssl_context,
                    server_hostname=hostname,
                    ssl_handshake_timeout=max(0.001, deadline - loop.time()),
                    limit=8192,
                )
                tls = writer.get_extra_info("ssl_object")
                _need(
                    tls is not None
                    and tls.version() == "TLSv1.3"
                    and tls.selected_alpn_protocol() in (None, "http/1.1")
                )
                head = (
                    f"POST {path} HTTP/1.1\r\nHost: {authority}\r\nAuthorization: Bearer {credential.bearer_ascii}\r\nContent-Type: application/json\r\nContent-Length: {len(request_utf8)}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(head + request_utf8)
                await writer.drain()
                length = await _headers(reader)
                _need(0 < length <= maximum)
                chunks = []
                remaining = length
                while remaining:
                    chunk = await reader.read(min(65536, remaining))
                    _need(bool(chunk))
                    chunks.append(chunk)
                    remaining -= len(chunk)
                _need(await reader.read(1) == b"")
                body = b"".join(chunks)
                _need(loop.time() < deadline)
                return body
        finally:
            if writer is not None:
                writer.transport.abort()
