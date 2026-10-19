"""Disposable candidate identity adapters using the unchanged service verifier.

Keys and identities belong only to the qualification fixture. This does not mint
production credentials or choose a production OA authority model.
"""
from concurrent import futures
from contextvars import ContextVar
import json
from pathlib import Path
import sys
import threading

from candidate_owner_http import _Server, _GrpcOwner, grpc, v1_pb2_grpc
from owner_oracle import Conflict, OwnerOracle
from versioned_counter_http import _VersionedHandler

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'packages/vuoro-service/src'))
from fastapi import Request
from vuoro_service.gateway_identity import GatewayAssertionIdentityResolver
from vuoro_service.identity import Identity, IdentityResolutionError

CURRENT: ContextVar[Identity | None] = ContextVar('ec00_verified_identity', default=None)
READ_RPCS = frozenset({'ReadRequest', 'ReadDependants', 'ReadArtifact',
                       'ListRequests', 'Watch', 'ListRequestsV2', 'WatchV2'})
WRITE_RPCS = frozenset({'Publish', 'Revise', 'WithdrawDependent', 'ReportPerformed',
                        'Verify', 'DecideArtifact', 'Resolve', 'AcknowledgeContinuation'})


class AuthorityRefused(PermissionError):
    pass


class IdentityPolicy:
    def __init__(self, verifier: GatewayAssertionIdentityResolver):
        self.verifier = verifier

    def resolve(self, headers, *, read: bool) -> Identity:
        # Preserve repeated headers: the production verifier rejects ambiguity.
        pairs = [(str(k).lower().encode('ascii'), str(v).encode('latin1')) for k, v in headers]
        request = Request({'type': 'http', 'method': 'POST', 'path': '/',
                           'query_string': b'', 'headers': pairs})
        request.state.vuoro_invocation_request_id = request.headers.get('x-request-id')
        identity = self.verifier(request)
        required = 'fixture:owner-read' if read else 'fixture:owner-write'
        if required not in identity.authorities:
            raise AuthorityRefused('fixture authority required')
        return identity

    def rpc(self, headers, method: str) -> Identity:
        if method not in READ_RPCS | WRITE_RPCS:
            raise AuthorityRefused('unlisted fixture operation')
        return self.resolve(headers, read=method in READ_RPCS)


class AuthenticatedOwner(_Server):
    def connect_owner(self):
        identity = CURRENT.get()
        if identity is None or identity.principal_id is None:
            raise IdentityResolutionError('verified fixture identity required')
        owner = OwnerOracle.connect_as(self.db_path, identity.principal_id)
        # The same connection supplies the owner's later transaction and
        # active-membership checks. Never translate a signed actor to a role.
        if owner.workspace_id != identity.workspace_id:
            owner.close()
            raise Conflict('verified workspace differs from owner membership')
        return owner


class AuthenticatedHandler(_VersionedHandler):
    def _authenticated(self, handler, *, read=False):
        try:
            identity = self.server.policy.resolve(self.headers.items(), read=read)
        except (IdentityResolutionError, AuthorityRefused) as exc:
            status = 403 if isinstance(exc, AuthorityRefused) else 401
            # Set before _VersionedHandler._json: no version dispatch occurred.
            self._counter_v2 = False
            self._json(status, {'error': 'permission-denied' if status == 403 else 'identity-required'})
            return
        token = CURRENT.set(identity)
        try:
            handler(self)
        finally:
            CURRENT.reset(token)

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
            try:
                identity = self.policy.rpc(context.invocation_metadata(), details.method.rsplit('/', 1)[-1])
            except (IdentityResolutionError, AuthorityRefused) as exc:
                code = grpc.StatusCode.PERMISSION_DENIED if isinstance(exc, AuthorityRefused) else grpc.StatusCode.UNAUTHENTICATED
                context.abort(code, 'fixture identity refused')
            token = CURRENT.set(identity)
            try:
                return handler.unary_unary(request, context)
            finally:
                CURRENT.reset(token)

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
        try:
            identity = self.policy.rpc(headers, scope['path'].rsplit('/', 1)[-1])
        except (IdentityResolutionError, AuthorityRefused) as exc:
            status = 403 if isinstance(exc, AuthorityRefused) else 401
            body = json.dumps({'code': 'permission_denied' if status == 403 else 'unauthenticated',
                               'message': 'fixture identity refused'}).encode()
            await send({'type': 'http.response.start', 'status': status,
                        'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]})
            await send({'type': 'http.response.body', 'body': body})
            return
        token = CURRENT.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT.reset(token)


class AuthenticatedCandidateServer:
    """Real HTTP and native gRPC listeners; one owner DB and request-local identity."""
    def __init__(self, path, policy, fault_plan=None):
        self.httpd = AuthenticatedOwner(path, '_never-used_', fault_plan)
        self.httpd.policy = policy
        self.httpd.RequestHandlerClass = AuthenticatedHandler
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4),
                                      interceptors=[IdentityInterceptor(policy)])
        v1_pb2_grpc.add_OwnerOperationsServicer_to_server(_GrpcOwner(self), self.grpc_server)
        self.grpc_port = self.grpc_server.add_insecure_port('127.0.0.1:0')
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
