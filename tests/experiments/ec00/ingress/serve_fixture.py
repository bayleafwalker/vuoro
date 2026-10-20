"""Disposable EC-00 ingress workload: the authenticated candidate fixture on fixed ports.

Only a fixture PUBLIC key, supplied at runtime as a mounted file, is trusted. No
signing key exists in the image or the pod; assertions are minted by the
remote-mode client (remote_checks.py), which holds the private key.

Listeners (all bound directly, no relay):
  EC00_HTTP_PORT    (8080) HTTP v2 JSON owner surface + read-only /_ec00/ readback
  EC00_CONNECT_PORT (8081) Python Connect (JSON and binary), HTTP/1.1 (uvicorn)
  EC00_GRPC_PORT    (8082) native gRPC (h2c)

The owner store is a fresh SQLite file under EC00_STATE_DIR; an existing store is
refused so every deployment starts from the seeded fixture principals.

The /_ec00/ readback is read-only and unauthenticated by design: it exposes only
fixture rows (as counts and a digest), server-side identity-context records, and
the headers each transport received with identity assertions redacted to a hash
prefix. It exists so absence-of-effect and header-handling checks can run through
an ingress without database access. It is a qualification aid, not a product API.
"""
from collections import OrderedDict
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from authenticated_owner import (AuthenticatedCandidateServer, AuthenticatedHandler, AuthenticatedOwner,
    IdentityMiddleware, OWNER_TABLES, fixture_policy, owner_snapshot, seed_fixture_owner)
from connect_owner_http import DirectConnectServer

PROBE_HEADER = 'x-ec00-probe'
REDACTED = frozenset({'x-vuoro-identity', 'authorization', 'proxy-authorization', 'cookie'})
_WIRE_PATH = re.compile(r'/_ec00/wire/([A-Za-z0-9._-]{1,128})')


def redact(name: str, value: str) -> str:
    if name.lower() not in REDACTED:
        return value
    digest = hashlib.sha256(value.encode('latin1', 'replace')).hexdigest()[:16]
    # The comma count keeps a proxy-joined pair of assertions detectable.
    return f'<redacted sha256:{digest} len:{len(value)} commas:{value.count(",")}>'


class WireLog:
    """Bounded per-probe record of the headers each transport received."""
    def __init__(self, limit=4096):
        self.limit = limit
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, list] = OrderedDict()

    def record(self, transport, headers, http_version):
        probes = [v for k, v in headers if k.lower() == PROBE_HEADER]
        key = probes[0] if len(probes) == 1 else None
        if key is None:
            return
        entry = dict(transport=transport, http_version=http_version,
                     headers=[[k, redact(k, v)] for k, v in headers])
        with self._lock:
            self._entries.setdefault(key, []).append(entry)
            self._entries.move_to_end(key)
            while len(self._entries) > self.limit:
                self._entries.popitem(last=False)

    def get(self, probe):
        with self._lock:
            return list(self._entries.get(probe, ()))


def state_summary(path):
    rows = owner_snapshot(path)
    canonical = json.dumps(rows, sort_keys=True, separators=(',', ':'), default=str).encode()
    return dict(tables={t: len(rows[t]) for t in OWNER_TABLES},
                sha256=hashlib.sha256(canonical).hexdigest())


class ReadbackHandler(AuthenticatedHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if not parsed.path.startswith('/_ec00/'):
            return super().do_GET()
        self._counter_v2 = False
        server = self.server
        if parsed.path == '/_ec00/healthz':
            return self._json(200, {'ready': True})
        if parsed.path == '/_ec00/state':
            return self._json(200, state_summary(server.db_path))
        match = _WIRE_PATH.fullmatch(parsed.path)
        if match:
            return self._json(200, {'probe': match.group(1), 'received': server.wire_log.get(match.group(1))})
        if parsed.path == '/_ec00/observations':
            wanted = set(parse_qs(parsed.query).get('request_id', []))
            policy = server.policy
            pick = lambda rows: [r for r in rows if not wanted or r['request_id'] in wanted]
            return self._json(200, {'request': pick(list(policy.observations)),
                                    'owner': pick(list(policy.owner_observations))})
        return self._json(404, {'error': 'unknown readback'})


def load_public_key(path: Path) -> Ed25519PublicKey:
    data = path.read_bytes()
    if b'PRIVATE KEY' in data:
        raise SystemExit('refusing a private key: mount only the fixture public key')
    key = load_pem_public_key(data)
    if not isinstance(key, Ed25519PublicKey):
        raise SystemExit('fixture public key must be Ed25519')
    return key


def main():
    host = os.environ.get('EC00_BIND', '0.0.0.0')
    ports = {name: int(os.environ.get(f'EC00_{name.upper()}_PORT', default))
             for name, default in (('http', 8080), ('connect', 8081), ('grpc', 8082))}
    state = Path(os.environ.get('EC00_STATE_DIR', '/state'))
    path = state / 'owner.sqlite'
    if os.environ.get('EC00_RESET_STATE') == '1':
        # Deployment mode: the store is disposable, so a container restart in the
        # same pod (whose emptyDir survives) starts over instead of crash-looping.
        for stale in state.glob('owner.sqlite*'):
            stale.unlink()
            print(json.dumps({'reset_state': stale.name}), flush=True)
    if path.exists():
        raise SystemExit('fresh disposable owner database required; restore must be explicit')
    public_key = load_public_key(Path(os.environ.get('EC00_PUBLIC_KEY_FILE', '/fixture/public.pem')))
    seed_fixture_owner(path)
    policy = fixture_policy(public_key)
    wire_log = WireLog()
    policy.wire_recorder = wire_log.record

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    native = AuthenticatedCandidateServer(path, policy, host=host, http_port=ports['http'],
                                          grpc_port=ports['grpc'])
    native.httpd.RequestHandlerClass = ReadbackHandler
    native.httpd.wire_log = wire_log
    connect = DirectConnectServer(path, owner_factory=AuthenticatedOwner,
                                  middleware=lambda app: IdentityMiddleware(app, policy),
                                  address=(host, ports['connect']))
    with ExitStack() as stack:
        stack.enter_context(native)
        stack.enter_context(connect)
        print(json.dumps({'ready': True, 'ports': ports, 'identity': 'fixture-public-key-only',
                          'store': 'disposable-sqlite'}), flush=True)
        stop.wait()


if __name__ == '__main__':
    main()
