"""Remote-mode authenticated transport checks for the EC-00 ingress fixture.

Runs the signed-identity cases of authenticated_transport_checks.py against a
deployed fixture (serve_fixture.py) through an ingress, plus probes of what the
ingress does to repeated headers, HTTP versions, grpc-web and header spelling.

  keygen  writes a fresh disposable Ed25519 keypair: the public half goes to the
          fixture (ConfigMap / mounted file), the private half stays here (0600).
  run     signs assertions with that private key and exercises the target.

Transport rules: https:// URLs and TLS gRPC, verified against the system trust
store (SSL_CERT_FILE, the interpreter default, or the host bundle); there is no option to skip
verification. --connect-to pins the TCP destination (e.g. the Gateway address)
while SNI, Host/:authority and certificate name checks keep the URL hostname.
Plain http:// and insecure gRPC are accepted only with --allow-plaintext-loopback
and only for loopback hosts (local container validation).

Owner-row checks use the fixture's read-only /_ec00/ readback (row counts and a
digest of every owner table, server-side identity-context records, and received
headers with assertions redacted), so absence of effect is still asserted
remotely. Dropped relative to the loopback suite: revocation after commit (it
needs an owner-store write the fixture does not expose).

Exit status is 1 when any judged case differs from its expectation: wrong
outcome, or owner state changed when it should not have (or did not when it
should). Each failure carries a kind: "security" (a request that should be
refused was accepted or changed owner state, a repeated identity header that
reached the fixture was not refused, or identity crossed requests) or
"expectation" (for example a signed publication refused because the ingress
rewrote x-request-id). Probes of ingress behaviour (versions, header spelling,
grpc-web) are never judged. A repeated identity/correlation header that the
ingress collapsed to one value and that was then accepted is raised as a
top-level ER-010 warning.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
import socket
import ssl
import statistics
import struct
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import grpc
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pyqwest import HTTPVersion, SyncClient, SyncHTTPTransport

from authenticated_owner import FOREIGN, WORKSPACE, fixture_principal, sign_fixture_assertion
from candidate_owner_http import v1_pb2, v1_pb2_grpc

PUBLISH_PATH = '/' + v1_pb2.DESCRIPTOR.services_by_name['OwnerOperations'].full_name + '/Publish'

ARMS = ('http-v2', 'connect-json', 'connect-binary', 'grpc')
PROBE = 'x-ec00-probe'
LOOPBACK = {'localhost'}
SCHEMA = 'ec00-ingress-observation/v1'


def is_loopback(host: str) -> bool:
    if host in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_TAG = re.compile(r'<redacted sha256:([0-9a-f]{16}) len:(\d+)(?: commas:(\d+))?>')


def token_digest(value: str) -> str:
    """The sha256 prefix serve_fixture puts in a received assertion's redaction tag."""
    return hashlib.sha256(value.encode('latin1')).hexdigest()[:16]


def parse_tag(received: str):
    """(digest, comma count) of a redaction tag; comma count is None for older fixtures."""
    match = _TAG.fullmatch(received)
    if not match:
        return None, received.count(',')
    return match.group(1), None if match.group(3) is None else int(match.group(3))


def system_ca_path() -> Path:
    paths = ssl.get_default_verify_paths()
    # Interpreters built outside the host (uv-managed Python on NixOS) report an
    # OpenSSL default that does not exist there; fall back to the host bundle.
    for candidate in (os.environ.get('SSL_CERT_FILE'), os.environ.get('NIX_SSL_CERT_FILE'),
                      paths.cafile, paths.openssl_cafile, '/etc/ssl/certs/ca-certificates.crt'):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit('system trust store bundle not found (set SSL_CERT_FILE)')


def system_ca_bundle() -> bytes:
    return system_ca_path().read_bytes()


def percentile_summary(samples):
    samples = sorted(samples)
    if not samples:
        return {'n': 0}
    p95 = statistics.quantiles(samples, n=20, method='inclusive')[18] if len(samples) > 1 else samples[0]
    return {'n': len(samples), 'p50_ms': round(statistics.median(samples) * 1000, 2),
            'p95_ms': round(p95 * 1000, 2), 'min_ms': round(samples[0] * 1000, 2),
            'max_ms': round(samples[-1] * 1000, 2)}


class Reply:
    def __init__(self, status, version, headers, body, trailers, elapsed):
        self.status, self.version, self.headers = status, version, headers
        self.body, self.trailers, self.elapsed = body, trailers, elapsed

    def header(self, name):
        return [v for k, v in self.headers if k.lower() == name]


class Target:
    def __init__(self, args):
        self.plaintext = args.allow_plaintext_loopback
        self.http_url = args.http_url.rstrip('/')
        self.connect_url = args.connect_url.rstrip('/')
        self.grpc_target = args.grpc_target
        self.connect_to = args.connect_to
        for url in (self.http_url, self.connect_url):
            self._check_url(url)
        grpc_host = self.grpc_target.rsplit(':', 1)[0].strip('[]')
        self.grpc_host = grpc_host
        self.grpc_port = int(self.grpc_target.rsplit(':', 1)[1])
        self.grpc_plaintext = self.plaintext and is_loopback(grpc_host)
        if self.plaintext and not self.grpc_plaintext:
            raise SystemExit('--allow-plaintext-loopback requires a loopback gRPC target')
        hosts = {urlsplit(u).hostname for u in (self.http_url, self.connect_url)} | {grpc_host}
        self.dns_overrides = {h: [self.connect_to] for h in hosts} if self.connect_to else None
        self._clients = {}
        self.channel = self._channel()
        self.stub = v1_pb2_grpc.OwnerOperationsStub(self.channel)

    def _check_url(self, url):
        parts = urlsplit(url)
        if parts.scheme == 'https':
            return
        if parts.scheme == 'http' and self.plaintext and is_loopback(parts.hostname or ''):
            return
        raise SystemExit(f'{url}: https required (plain http only with --allow-plaintext-loopback to loopback)')

    @property
    def grpc_web_url(self):
        scheme = 'http' if self.grpc_plaintext else 'https'
        host = f'[{self.grpc_host}]' if ':' in self.grpc_host else self.grpc_host
        return f'{scheme}://{host}:{self.grpc_port}'

    def _channel(self):
        if self.grpc_plaintext:
            return grpc.insecure_channel(self.grpc_target)
        credentials = grpc.ssl_channel_credentials(root_certificates=system_ca_bundle())
        if not self.connect_to:
            return grpc.secure_channel(self.grpc_target, credentials)
        # Dial the pinned address; verify the certificate for, and send SNI and
        # :authority as, the configured hostname. Verification is not skipped.
        return grpc.secure_channel(f'{self.connect_to}:{self.grpc_port}', credentials, options=[
            ('grpc.ssl_target_name_override', self.grpc_host), ('grpc.default_authority', self.grpc_host)])

    def client(self, version: str | None = None) -> SyncClient:
        if version not in self._clients:
            http_version = {None: None, 'HTTP/1.1': HTTPVersion.HTTP1, 'HTTP/2': HTTPVersion.HTTP2}[version]
            transport = SyncHTTPTransport(tls_include_system_certs=True, http_version=http_version,
                dns_overrides=self.dns_overrides, follow_redirects=False, timeout=15, enable_otel=False)
            self._clients[version] = SyncClient(transport)
        return self._clients[version]

    def send(self, method, url, headers=(), body=None, *, version=None) -> Reply:
        started = time.perf_counter()
        with self.client(version).stream(method, url, headers=list(headers), content=body, timeout=15) as reply:
            content = b''.join(bytes(chunk) for chunk in reply.content)
            elapsed = time.perf_counter() - started
            observed = next((label for label, value in (('HTTP/1.1', HTTPVersion.HTTP1), ('HTTP/2', HTTPVersion.HTTP2),
                             ('HTTP/3', HTTPVersion.HTTP3)) if reply.http_version == value), str(reply.http_version))
            return Reply(reply.status, observed, list(reply.headers.items()), content,
                         list(reply.trailers.items()), elapsed)

    # Read-only fixture readback.
    def readback(self, path):
        reply = self.send('GET', self.http_url + path)
        if reply.status != 200:
            raise RuntimeError(f'readback {path}: HTTP {reply.status}')
        return json.loads(reply.body)

    def state(self):
        return self.readback('/_ec00/state')

    def wire(self, probe):
        return self.readback('/_ec00/wire/' + probe)['received']

    def observations(self, request_ids):
        return self.readback('/_ec00/observations?' + urlencode([('request_id', r) for r in request_ids]))


def classify_status(status):
    return {200: 'ok', 401: 'identity', 403: 'authority', 409: 'owner'}.get(status, f'http-{status}')


class Runner:
    def __init__(self, target: Target, key: Ed25519PrivateKey, samples: int):
        self.target, self.key, self.samples = target, key, samples
        self.run_id = uuid.uuid4().hex[:12]
        self._rid = 9007199254740993 + secrets.randbelow(10 ** 12) * 1000
        self.cases, self.failures, self.latency, self.ingress = [], [], {}, {}
        self.er010_warnings = []
        self._rid_lock = threading.Lock()

    def rid(self):
        with self._rid_lock:  # called from the concurrency workers
            self._rid += 1
            return str(self._rid)

    def sign(self, subject='alice', *, rid, claims=None, key=None):
        return sign_fixture_assertion(key or self.key, subject, request_id=rid, claims=claims)

    # One publication on one arm; returns (outcome, returned request id, probe, reply-ish).
    def publish(self, arm, headers, *, rid, subject='alice', key='publish', objective='ingress fixture',
                extra=()):
        probe = uuid.uuid4().hex
        pairs = [*(headers.items() if isinstance(headers, dict) else headers), ('idempotency-key', key),
                 (PROBE, probe), *extra]
        dependants = ['work-' + subject]
        if arm == 'grpc':
            request = v1_pb2.PublishRequest(request_id=rid, objective=objective, dependent_ids=dependants)
            started = time.perf_counter()
            try:
                result, call = self.target.stub.Publish.with_call(request, metadata=pairs, timeout=15)
                return 'ok', result.request_id, probe, time.perf_counter() - started, 'HTTP/2'
            except grpc.RpcError as exc:
                outcome = {grpc.StatusCode.UNAUTHENTICATED: 'identity', grpc.StatusCode.PERMISSION_DENIED: 'authority',
                           grpc.StatusCode.ABORTED: 'owner'}.get(exc.code(), f'grpc-{exc.code().name}')
                return outcome, None, probe, time.perf_counter() - started, 'HTTP/2'
        if arm == 'http-v2':
            url = self.target.http_url + '/v2/requests'
            body = json.dumps({'requestId': rid, 'objective': objective, 'dependants': dependants}).encode()
            pairs.append(('content-type', 'application/json'))
        else:
            url = self.target.connect_url + PUBLISH_PATH
            pairs.append(('connect-protocol-version', '1'))
            if arm == 'connect-json':
                body = json.dumps({'requestId': rid, 'objective': objective, 'dependentIds': dependants}).encode()
                pairs.append(('content-type', 'application/json'))
            else:
                body = v1_pb2.PublishRequest(request_id=rid, objective=objective,
                                             dependent_ids=dependants).SerializeToString()
                pairs.append(('content-type', 'application/proto'))
        reply = self.target.send('POST', url, pairs, body)
        outcome = classify_status(reply.status)
        returned = None
        if outcome == 'ok':
            returned = (v1_pb2.MutationResult.FromString(reply.body).request_id if arm == 'connect-binary'
                        else json.loads(reply.body)['requestId'])
        return outcome, returned, probe, reply.elapsed, reply.version

    def record(self, arm, case, expected, outcome, *, before=None, after=None, effect_expected=False, **extra):
        unchanged = None if before is None else before['sha256'] == after['sha256']
        ok = outcome == expected and (unchanged is None or unchanged != effect_expected)
        entry = dict(arm=arm, case=case, expected=expected, observed=outcome, owner_state_unchanged=unchanged,
                     passed=ok, **extra)
        if not ok:
            refused_case = expected != 'ok'
            entry['failure_kind'] = 'security' if refused_case and (outcome == 'ok' or unchanged is False) else 'expectation'
        self.cases.append(entry)
        if not ok:
            self.failures.append(entry)
        return entry

    def run_arm(self, arm):
        t = self.target
        rid = self.rid()
        key = f'{self.run_id}-{arm}-publish'
        s0 = t.state()
        headers = self.sign(rid=rid)
        outcome, returned, probe, _, version = self.publish(arm, headers, rid=rid, key=key)
        s1 = t.state()
        received = t.wire(probe)
        self.record(arm, 'signed-publication', 'ok', outcome, before=s0, after=s1, effect_expected=True,
                    returned_request_id_matches=returned == rid, client_http_version=version,
                    correlation=correlation_summary(headers, received))
        outcome, *_ = self.publish(arm, headers, rid=rid, key=key)
        self.record(arm, 'assertion-replay', 'identity', outcome, before=s1, after=t.state())
        outcome, returned, *_ = self.publish(arm, self.sign(rid=rid), rid=rid, key=key)
        self.record(arm, 'fresh-assertion-retry', 'ok', outcome, before=s1, after=t.state(),
                    returned_request_id_matches=returned == rid)
        outcome, *_ = self.publish(arm, self.sign(rid=rid), rid=rid, key=key, objective='changed')
        self.record(arm, 'changed-body-same-key', 'owner', outcome, before=s1, after=t.state())

        fresh = self.rid()
        now = int(time.time())
        mutations = [('audience', {'aud': 'different'}), ('issuer', {'iss': 'different'}),
                     ('workspace', {'workspace_id': FOREIGN}), ('repository', {'repo_ids': ['foreign']}),
                     ('expired', {'exp': now - 5, 'iat': now - 30, 'nbf': now - 30}),
                     ('future', {'iat': now + 60, 'nbf': now + 60, 'exp': now + 90}),
                     ('actor-disagrees', {'actor': 'agent:bob'}), ('epoch', {'principal_epoch': -1})]
        bad = [('missing', {}), ('unsigned-actor', {'x-actor': 'agent:alice', 'x-workspace-id': WORKSPACE}),
               ('signature', self.sign(rid=fresh, key=Ed25519PrivateKey.generate())),
               ('correlation', {**self.sign(rid=fresh), 'x-request-id': 'different'}),
               ('envelope', self.sign(rid=self.rid()))]
        bad.extend((name, self.sign(rid=fresh, claims=change)) for name, change in mutations)
        for name, bad_headers in bad:
            before = t.state()
            outcome, _, probe, _, _ = self.publish(arm, bad_headers, rid=fresh, key=f'{self.run_id}-{arm}-{name}')
            extra = {}
            if name == 'missing':
                extra['ingress_injected'] = [k for k, v in headers_of(t.wire(probe)) if k.lower() == 'x-request-id']
            self.record(arm, name, 'identity', outcome, before=before, after=t.state(), **extra)
        before = t.state()
        outcome, *_ = self.publish(arm, self.sign(rid=fresh, claims={'authorities': ['fixture:owner-read']}),
                                   rid=fresh, key=f'{self.run_id}-{arm}-authority')
        self.record(arm, 'insufficient-authority', 'authority', outcome, before=before, after=t.state())
        for subject in ('revoked', 'foreign', 'observer'):
            before = t.state()
            outcome, *_ = self.publish(arm, self.sign(subject, rid=fresh), rid=fresh, subject=subject,
                                       key=f'{self.run_id}-{arm}-{subject}')
            self.record(arm, f'owner-refuses-{subject}', 'owner', outcome, before=before, after=t.state())
        before = t.state()
        outcome, *_ = self.publish(arm, {**self.sign(rid=fresh), 'x-actor': 'agent:bob'}, rid=fresh, subject='bob',
                                   key=f'{self.run_id}-{arm}-display-actor')
        self.record(arm, 'display-actor-cannot-take-link', 'owner', outcome, before=before, after=t.state())
        self.repeated_headers(arm)
        self.concurrency(arm)
        self.measure(arm)

    def repeated_headers(self, arm):
        t = self.target
        for name, mode in (('x-vuoro-identity', 'identical'), ('x-vuoro-identity', 'different'),
                           ('x-request-id', 'identical'), ('x-request-id', 'different')):
            rid = self.rid()
            headers = self.sign(rid=rid)
            second = headers[name] if mode == 'identical' else (
                self.sign('bob', rid=rid)['x-vuoro-identity'] if name == 'x-vuoro-identity' else 'different')
            pairs = [*headers.items(), (name, second)]
            before = t.state()
            outcome, _, probe, _, version = self.publish(arm, pairs, rid=rid, key=f'{self.run_id}-{arm}-dup-{name}-{mode}')
            after = t.state()
            received = [v for k, v in headers_of(t.wire(probe)) if k.lower() == name]
            sent = [headers[name], second]
            arrived, commas = classify_arrival(name, sent, received)
            entry = dict(arm=arm, case=f'repeated-{name}-{mode}', observed=outcome, client_http_version=version,
                         received_count=len(received), received_comma_counts=commas, arrived_as=arrived,
                         owner_state_unchanged=before['sha256'] == after['sha256'])
            # Invariant only where the fixture itself saw the repetition.
            entry['passed'] = (outcome == 'identity' and entry['owner_state_unchanged']) if len(received) >= 2 else None
            if entry['passed'] is False:
                entry['failure_kind'] = 'security'
            if len(received) < 2 and outcome == 'ok':
                warning = dict(arm=arm, case=entry['case'], arrived_as=arrived,
                               message=(f'ER-010: the ingress delivered a repeated {name} as {arrived} and the '
                                        'request was accepted; the fixture could not see the ambiguity'))
                entry['er010_warning'] = warning['message']
                self.er010_warnings.append(warning)
            self.cases.append(entry)
            if entry['passed'] is False:
                self.failures.append(entry)

    def concurrency(self, arm):
        t = self.target
        before = t.state()

        def call(index):
            subject = 'alice' if index % 2 == 0 else 'bob'
            rid = self.rid()
            outcome, returned, *_ = self.publish(arm, self.sign(subject, rid=rid), rid=rid, subject=subject,
                                                 key=f'{self.run_id}-{arm}-shared-{index // 2}')
            return outcome, returned, rid, subject

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(16)))
        after = t.state()
        expected = {rid: fixture_principal(subject) for _, _, rid, subject in results}
        observed = t.observations(list(expected))
        problems = [r for r in results if r[0] != 'ok' or r[1] != r[2]]
        problems += [r for r in observed['request'] if not (r['entry_clear'] and r['exit_clear'])
                     or expected.get(r['request_id']) != r['principal']]
        problems += [r for r in observed['owner'] if expected.get(r['request_id']) != r['principal']]
        coverage = {r['request_id'] for r in observed['owner']} == set(expected)
        entry = dict(arm=arm, case='concurrent-16x8-no-identity-carry-over',
                     passed=not problems and coverage and after['tables']['request'] == before['tables']['request'] + 16,
                     problems=problems[:5], owner_records=len(observed['owner']))
        if not entry['passed']:
            entry['failure_kind'] = 'security'
        self.cases.append(entry)
        if not entry['passed']:
            self.failures.append(entry)

    def measure(self, arm):
        ok_samples, refusal_samples = [], []
        self.publish(arm, {}, rid=self.rid(), key=f'{self.run_id}-{arm}-warmup')  # warm-up, not counted
        for index in range(self.samples):
            rid = self.rid()
            outcome, _, _, elapsed, _ = self.publish(arm, self.sign(rid=rid), rid=rid,
                                                     key=f'{self.run_id}-{arm}-latency-{index}')
            if outcome == 'ok':
                ok_samples.append(elapsed)
            outcome, _, _, elapsed, _ = self.publish(arm, {}, rid=self.rid(), key=f'{self.run_id}-{arm}-refusal-{index}')
            if outcome == 'identity':
                refusal_samples.append(elapsed)
        self.latency[arm] = {'signed_publication': percentile_summary(ok_samples),
                             'missing_identity_refusal': percentile_summary(refusal_samples)}

    # Ingress-level observations (no pass/fail).
    def probe_versions(self):
        t, out = self.target, {}
        for label, url, body, headers in (
                ('http', t.http_url + '/v2/requests', b'{}', [('content-type', 'application/json')]),
                ('connect', t.connect_url + PUBLISH_PATH, b'{}', [('content-type', 'application/json'),
                                                                   ('connect-protocol-version', '1')])):
            for version in (None, 'HTTP/1.1', 'HTTP/2'):
                probe = uuid.uuid4().hex
                try:
                    reply = t.send('POST', url, [*headers, (PROBE, probe)], body, version=version)
                    upstream = [e['http_version'] for e in t.wire(probe)]
                    out[f'{label}:{version or "alpn-default"}'] = dict(status=reply.status, client=reply.version,
                                                                       fixture_received=upstream)
                except Exception as exc:  # recorded, not judged
                    out[f'{label}:{version or "alpn-default"}'] = dict(error=type(exc).__name__, detail=str(exc)[:300])
        self.ingress['http_versions'] = out

    def probe_header_spelling(self):
        t = self.target
        probe = uuid.uuid4().hex
        lines = [f'X-EC00-Probe: {probe}', 'X-EC00-Mixed-Case: Value-Case', 'x-ec00-padded:   padded value   ',
                 'x_ec00_underscore: underscore', 'X-EC00-Dup: first', 'x-ec00-dup: second', 'X-EC00-Comma: a, b',
                 'Content-Type: application/json']
        try:
            status = raw_http1(t, t.http_url, '/v2/requests', lines, b'{}')
            received = headers_of(t.wire(probe))
            self.ingress['http1_header_spelling'] = dict(
                status=status, sent=lines[:-1],
                received=[[k, v] for k, v in received if k.lower().startswith(('x-ec00', 'x_ec00'))],
                added_by_path=sorted({k.lower() for k, _ in received} - {l.split(':', 1)[0].lower() for l in lines}
                                     - {'host', 'content-length', 'connection'}))
        except Exception as exc:
            self.ingress['http1_header_spelling'] = dict(error=type(exc).__name__, detail=str(exc)[:300])
        probe = uuid.uuid4().hex
        try:
            reply = t.send('POST', t.connect_url + PUBLISH_PATH,
                           [(PROBE, probe), ('X-EC00-Mixed-Case', 'Value-Case'), ('x-ec00-dup', 'first'),
                            ('x-ec00-dup', 'second'), ('content-type', 'application/json'),
                            ('connect-protocol-version', '1')], b'{}', version=None)
            received = headers_of(t.wire(probe))
            self.ingress['connect_header_spelling'] = dict(
                status=reply.status, client=reply.version,
                received=[[k, v] for k, v in received if k.lower().startswith('x-ec00')],
                fixture_http_version=[e['http_version'] for e in t.wire(probe)])
        except Exception as exc:
            self.ingress['connect_header_spelling'] = dict(error=type(exc).__name__, detail=str(exc)[:300])

    def probe_grpc_web(self):
        t, out = self.target, {}
        for version in ('HTTP/1.1', 'HTTP/2'):
            rid = self.rid()
            message = v1_pb2.PublishRequest(request_id=rid, objective='grpc-web probe',
                                            dependent_ids=['work-alice']).SerializeToString()
            probe = uuid.uuid4().hex
            before = t.state()
            try:
                reply = t.send('POST', t.grpc_web_url + PUBLISH_PATH,
                               [*self.sign(rid=rid).items(), ('idempotency-key', f'{self.run_id}-grpcweb-{version}'),
                                (PROBE, probe), ('content-type', 'application/grpc-web+proto'), ('x-grpc-web', '1'),
                                ('accept', 'application/grpc-web+proto')],
                               b'\x00' + struct.pack('>I', len(message)) + message, version=version)
                out[version] = dict(status=reply.status, client=reply.version,
                                    content_type=reply.header('content-type'), grpc_status=reply.header('grpc-status')
                                    or [v for k, v in reply.trailers if k.lower() == 'grpc-status'],
                                    reached_fixture=bool(t.wire(probe)),
                                    owner_state_changed=t.state()['sha256'] != before['sha256'])
            except Exception as exc:
                out[version] = dict(error=type(exc).__name__, detail=str(exc)[:300],
                                    reached_fixture=bool(t.wire(probe)))
        self.ingress['grpc_web_on_grpc_host'] = out


def classify_arrival(name, sent, received):
    """How a header sent twice (``sent``) arrived at the fixture (``received``).

    Returns (arrived_as, per-value comma counts). Assertions come back only as
    redaction tags, so they are matched by digest, and the tag's comma count
    flags a join that matches neither known join spelling.
    """
    joins = [f'{sent[0]}, {sent[1]}', f'{sent[0]},{sent[1]}']
    if name == 'x-vuoro-identity':
        parsed = [parse_tag(v) for v in received]
        keys, commas = [d for d, _ in parsed], [c for _, c in parsed]
        sent_keys, join_keys = [token_digest(v) for v in sent], [token_digest(v) for v in joins]
    else:
        keys, commas, sent_keys, join_keys = received, [v.count(',') for v in received], sent, joins
    if len(received) >= 2:
        return 'repeated', commas
    if not received:
        return 'none', commas
    if keys[0] in join_keys or (keys[0] not in sent_keys and (commas[0] or 0) > 0):
        return 'joined', commas
    return 'single:' + ('first' if keys[0] == sent_keys[0] else
                        'second' if keys[0] == sent_keys[1] else 'other'), commas


def headers_of(entries):
    return [tuple(pair) for entry in entries for pair in entry['headers']]


def correlation_summary(sent, received):
    got = [v for k, v in headers_of(received) if k.lower() == 'x-request-id']
    return dict(sent=sent['x-request-id'], received=got, preserved=got == [sent['x-request-id']],
                fixture_http_version=[e['http_version'] for e in received])


def raw_http1(target, base, path, header_lines, body):
    """Send exact HTTP/1.1 header bytes (TLS verified with the system store unless loopback plain)."""
    parts = urlsplit(base)
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    address = (target.connect_to or parts.hostname, port)
    sock = socket.create_connection(address, timeout=15)
    if parts.scheme == 'https':
        context = ssl.create_default_context(cafile=str(system_ca_path()))
        context.set_alpn_protocols(['http/1.1'])
        sock = context.wrap_socket(sock, server_hostname=parts.hostname)
    request = '\r\n'.join([f'POST {path} HTTP/1.1', f'Host: {parts.netloc}', *header_lines,
                           f'Content-Length: {len(body)}', 'Connection: close', '', '']).encode('latin1') + body
    with sock:
        sock.sendall(request)
        data = b''
        while chunk := sock.recv(65536):
            data += chunk
    return int(data.split(b' ', 2)[1])


def keygen(args):
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Ed25519PrivateKey.generate()
    private = directory / 'fixture-private.pem'
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    public = directory / 'fixture-public.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo))
    print(json.dumps({'public_key': str(public), 'private_key': str(private),
                      'note': 'mount only the public key; delete the private key after the run'}))


def run(args):
    target = Target(args)
    key = serialization.load_pem_private_key(Path(args.private_key).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit('fixture private key must be Ed25519')
    runner = Runner(target, key, args.samples)
    health = target.readback('/_ec00/healthz')
    for arm in args.arms:
        runner.run_arm(arm)
    runner.probe_versions()
    runner.probe_header_spelling()
    runner.probe_grpc_web()
    report = dict(schema_version=SCHEMA, run_id=runner.run_id,
                  target=dict(http_url=target.http_url, connect_url=target.connect_url,
                              grpc_target=target.grpc_target, connect_to=target.connect_to,
                              plaintext_loopback=target.plaintext),
                  fixture_health=health, arms=list(args.arms),
                  dropped_relative_to_loopback=['revocation-after-commit (needs an owner-store write)'],
                  latency_note=('client-side wall time per request on a reused connection, first request '
                                'excluded; no cross-target comparison is implied'),
                  cases=runner.cases, latency=runner.latency, ingress=runner.ingress,
                  er010_warnings=runner.er010_warnings,
                  failure_kinds={kind: sum(1 for f in runner.failures if f.get('failure_kind') == kind)
                                 for kind in ('security', 'expectation')},
                  invariant_failures=runner.failures)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(text + '\n')
    passed = sum(1 for c in runner.cases if c.get('passed'))
    print(json.dumps({'cases': len(runner.cases), 'passed': passed, 'observations_only':
                      sum(1 for c in runner.cases if c.get('passed') is None),
                      'invariant_failures': len(runner.failures), 'er010_warnings': len(runner.er010_warnings),
                      'latency': runner.latency}, indent=2))
    return 1 if runner.failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    k = sub.add_parser('keygen', help='write a disposable fixture keypair')
    k.add_argument('--directory', required=True)
    r = sub.add_parser('run', help='run the remote-mode checks')
    r.add_argument('--http-url', default=os.environ.get('EC00_HTTP_URL'), required='EC00_HTTP_URL' not in os.environ)
    r.add_argument('--connect-url', default=os.environ.get('EC00_CONNECT_URL'),
                   required='EC00_CONNECT_URL' not in os.environ)
    r.add_argument('--grpc-target', default=os.environ.get('EC00_GRPC_TARGET'),
                   required='EC00_GRPC_TARGET' not in os.environ)
    r.add_argument('--connect-to', default=os.environ.get('EC00_CONNECT_TO'),
                   help='TCP address to dial instead of resolving the hostnames (e.g. the Gateway IP)')
    r.add_argument('--private-key', default=os.environ.get('EC00_PRIVATE_KEY'),
                   required='EC00_PRIVATE_KEY' not in os.environ)
    r.add_argument('--allow-plaintext-loopback', action='store_true')
    r.add_argument('--samples', type=int, default=20)
    r.add_argument('--arms', nargs='+', choices=ARMS, default=list(ARMS))
    r.add_argument('--report')
    args = parser.parse_args(argv)
    if args.command == 'keygen':
        keygen(args)
        return 0
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
