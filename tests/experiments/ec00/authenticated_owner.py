"""Disposable candidate identity adapters using the unchanged service verifier.

Keys and identities belong only to the qualification fixture. This does not mint
production credentials or choose a production OA authority model.
"""
from concurrent import futures
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import sys
import threading
from urllib.parse import unquote, urlsplit

from candidate_owner_http import _Server, _GrpcOwner, grpc, v1_pb2, v1_pb2_grpc
from google.protobuf import json_format, message_factory
from owner_oracle import Conflict, OwnerOracle
from versioned_counter_http import _VersionedHandler

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'packages/vuoro-service/src'))
from fastapi import Request
from vuoro_service.gateway_identity import GatewayAssertionIdentityResolver
from vuoro_service.identity import Identity, IdentityResolutionError

CURRENT: ContextVar[Identity | None] = ContextVar('ec00_verified_identity', default=None)
SCOPE: ContextVar['RequestScope | None'] = ContextVar('ec00_request_scope', default=None)
READ_RPCS = frozenset({'ReadRequest', 'ReadDependants', 'ReadArtifact',
                       'ListRequests', 'Watch', 'ListRequestsV2', 'WatchV2'})
WRITE_RPCS = frozenset({'Publish', 'Revise', 'WithdrawDependent', 'ReportPerformed',
                        'Verify', 'DecideArtifact', 'Resolve', 'AcknowledgeContinuation'})


_SERVICE = v1_pb2.DESCRIPTOR.services_by_name['OwnerOperations']
_REQUEST_PATH = re.compile(r'/v[12]/requests/([^/]+)')
_BODY_LIMIT = 1024 * 1024


class AuthorityRefused(PermissionError):
    pass


@dataclass(frozen=True)
class RequestScope:
    request_id: str | None
    policy: 'IdentityPolicy'


def message_request_id(message) -> str | None:
    """The OA request id carried in a parsed candidate message, if it has one.

    The candidate OA protocol has no separate invocation envelope, so the parsed
    message's own ``request_id`` is the body field the signed claim must cover.
    Messages without one (list/watch pages) yield ``None`` and are refused.
    """
    if message is None or 'request_id' not in message.DESCRIPTOR.fields_by_name:
        return None
    return message.request_id or None


def rpc_request_id(method: str, body: bytes, content_type: str) -> str | None:
    """Parse a Connect unary body into the method's input message (JSON or binary)."""
    descriptor = _SERVICE.methods_by_name.get(method)
    if descriptor is None:
        return None
    message = message_factory.GetMessageClass(descriptor.input_type)()
    try:
        if content_type.split(';', 1)[0].strip().lower() in {'application/json', 'application/connect+json'}:
            json_format.Parse(body or b'{}', message, ignore_unknown_fields=False)
        else:
            message.ParseFromString(body)
    except Exception:
        return None
    return message_request_id(message)


def http_request_id(path: str, body: bytes) -> str | None:
    """The request id in an HTTP v2 JSON body, else the one in the resource path."""
    if body:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get('requestId'), str):
            return parsed['requestId'] or None
    match = _REQUEST_PATH.match(urlsplit(path).path)
    return unquote(match.group(1)) if match else None


class IdentityPolicy:
    """Fixture policy around the unchanged service verifier.

    It also keeps an in-process record of the identity context each request
    observes on the server side: the context at entry (before this request bound
    its identity), at owner use, and after reset. Tests read these records to
    check that no request inherits or leaks another request's identity.
    """
    def __init__(self, verifier: GatewayAssertionIdentityResolver):
        self.verifier = verifier
        self._lock = threading.Lock()
        self.resolutions = 0
        self.observations: list[dict] = []
        self.owner_observations: list[dict] = []

    def resolve(self, headers, *, read: bool, envelope_request_id: str | None) -> Identity:
        with self._lock:
            self.resolutions += 1
        # Preserve repeated headers: the production verifier rejects ambiguity.
        pairs = [(str(k).lower().encode('ascii'), str(v).encode('latin1')) for k, v in headers]
        request = Request({'type': 'http', 'method': 'POST', 'path': '/',
                           'query_string': b'', 'headers': pairs})
        # As in production (vuoro_service.app), the claim must cover the request
        # id parsed from the body, not only the transport header.
        request.state.vuoro_invocation_request_id = envelope_request_id
        identity = self.verifier(request)
        required = 'fixture:owner-read' if read else 'fixture:owner-write'
        if required not in identity.authorities:
            raise AuthorityRefused('fixture authority required')
        return identity

    def rpc(self, headers, method: str, envelope_request_id: str | None) -> Identity:
        if method not in READ_RPCS | WRITE_RPCS:
            raise AuthorityRefused('unlisted fixture operation')
        return self.resolve(headers, read=method in READ_RPCS, envelope_request_id=envelope_request_id)

    @contextmanager
    def bound(self, identity: Identity, request_id: str | None, transport: str):
        entry = (CURRENT.get(), SCOPE.get())
        identity_token = CURRENT.set(identity)
        scope_token = SCOPE.set(RequestScope(request_id, self))
        try:
            yield
        finally:
            SCOPE.reset(scope_token)
            CURRENT.reset(identity_token)
            record = dict(transport=transport, request_id=request_id, principal=identity.principal_id,
                          entry_clear=entry == (None, None),
                          exit_clear=(CURRENT.get(), SCOPE.get()) == (None, None),
                          thread=threading.get_ident())
            with self._lock:
                self.observations.append(record)

    def observe_owner(self, request_id: str | None, principal: str | None):
        with self._lock:
            self.owner_observations.append(dict(request_id=request_id, principal=principal))


class AuthenticatedOwner(_Server):
    def connect_owner(self):
        identity = CURRENT.get()
        if identity is None or identity.principal_id is None:
            raise IdentityResolutionError('verified fixture identity required')
        scope = SCOPE.get()
        if scope is not None:
            scope.policy.observe_owner(scope.request_id, identity.principal_id)
        owner = OwnerOracle.connect_as(self.db_path, identity.principal_id)
        # The same connection supplies the owner's later transaction and
        # active-membership checks. Never translate a signed actor to a role.
        if owner.workspace_id != identity.workspace_id:
            owner.close()
            raise Conflict('verified workspace differs from owner membership')
        return owner


class AuthenticatedHandler(_VersionedHandler):
    def _buffered_body(self) -> bytes | None:
        try:
            size = int(self.headers.get('Content-Length', '0') or '0')
        except ValueError:
            return None
        if size < 0 or size > _BODY_LIMIT:
            return None
        return self.rfile.read(size) if size else b''

    def _authenticated(self, handler, *, read=False):
        body = self._buffered_body()
        try:
            envelope = None if body is None else http_request_id(self.path, body)
            identity = self.server.policy.resolve(self.headers.items(), read=read,
                                                  envelope_request_id=envelope)
        except (IdentityResolutionError, AuthorityRefused) as exc:
            status = 403 if isinstance(exc, AuthorityRefused) else 401
            # Set before _VersionedHandler._json: no version dispatch occurred.
            self._counter_v2 = False
            self._json(status, {'error': 'permission-denied' if status == 403 else 'identity-required'})
            return
        # The handler re-reads the body it was authorized against; the original
        # stream is restored for the next request on a keep-alive connection.
        stream, self.rfile = self.rfile, io.BytesIO(body)
        try:
            with self.server.policy.bound(identity, envelope, 'http'):
                handler(self)
        finally:
            self.rfile = stream

    def do_POST(self):
        self._authenticated(_VersionedHandler.do_POST)

    def do_PATCH(self):
        self._authenticated(_VersionedHandler.do_PATCH)

    def do_GET(self):
        self._authenticated(_VersionedHandler.do_GET, read=True)


class IdentityInterceptor(grpc.ServerInterceptor):
    def __init__(self, policy):
        self.policy = policy

    def intercept_service(self, continuation, details):
        handler = continuation(details)
        if handler is None:
            return None
        if handler.unary_unary is None:
            raise ValueError('streaming is outside this unary qualification')

        def invoke(request, context):
            envelope = message_request_id(request)
            try:
                identity = self.policy.rpc(context.invocation_metadata(), details.method.rsplit('/', 1)[-1], envelope)
            except (IdentityResolutionError, AuthorityRefused) as exc:
                code = grpc.StatusCode.PERMISSION_DENIED if isinstance(exc, AuthorityRefused) else grpc.StatusCode.UNAUTHENTICATED
                context.abort(code, 'fixture identity refused')
            with self.policy.bound(identity, envelope, 'grpc'):
                return handler.unary_unary(request, context)

        return grpc.unary_unary_rpc_method_handler(invoke,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer)


class IdentityMiddleware:
    def __init__(self, app, policy):
        self.app, self.policy = app, policy

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = [(k.decode('ascii'), v.decode('latin1')) for k, v in scope['headers']]
        body, size = [], 0
        while True:
            message = await receive()
            if message['type'] != 'http.request':
                break
            size += len(message.get('body', b''))
            if size > _BODY_LIMIT:
                body = None
                break
            body.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        raw = None if body is None else b''.join(body)
        method = scope['path'].rsplit('/', 1)[-1]
        content_type = next((v for k, v in headers if k.lower() == 'content-type'), '')
        envelope = None if raw is None else rpc_request_id(method, raw, content_type)
        try:
            identity = self.policy.rpc(headers, method, envelope)
        except (IdentityResolutionError, AuthorityRefused) as exc:
            status = 403 if isinstance(exc, AuthorityRefused) else 401
            body = json.dumps({'code': 'permission_denied' if status == 403 else 'unauthenticated',
                               'message': 'fixture identity refused'}).encode()
            await send({'type': 'http.response.start', 'status': status,
                        'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]})
            await send({'type': 'http.response.body', 'body': body})
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': raw, 'more_body': False}
            return await receive()

        with self.policy.bound(identity, envelope, 'connect-python'):
            await self.app(scope, replay, send)


class AuthenticatedCandidateServer:
    """Real HTTP and native gRPC listeners; one owner DB and request-local identity."""
    def __init__(self, path, policy, fault_plan=None, *, host='127.0.0.1', http_port=0, grpc_port=0):
        self.httpd = AuthenticatedOwner(path, '_never-used_', fault_plan, address=(host, http_port))
        self.httpd.policy = policy
        self.httpd.RequestHandlerClass = AuthenticatedHandler
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4),
                                      interceptors=[IdentityInterceptor(policy)])
        v1_pb2_grpc.add_OwnerOperationsServicer_to_server(_GrpcOwner(self), self.grpc_server)
        self.grpc_port = self.grpc_server.add_insecure_port(f'{host}:{grpc_port}')
        if not self.grpc_port:
            raise RuntimeError('cannot bind authenticated fixture')

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.httpd.server_port}'

    @property
    def grpc_target(self):
        return f'127.0.0.1:{self.grpc_port}'

    def __enter__(self):
        self.thread.start()
        self.grpc_server.start()
        return self

    def __exit__(self, *args):
        self.grpc_server.stop(grace=0).wait(timeout=5)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
