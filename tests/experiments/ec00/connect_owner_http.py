"""Disposable direct Python Connect projection of the existing owner adapter."""
import asyncio
from pathlib import Path
import socket
from types import SimpleNamespace
import sys
import threading
import uvicorn

from lifecycle_slice_checks import HERE, grpc
from candidate_owner_http import _Server, _GrpcOwner
from owner_oracle import Conflict, ResponseLost
sys.path.insert(0, str(HERE / 'generated/connect-python'))
from ec00_connect.oa_v1_connect import OwnerOperationsASGIApplication
from connectrpc.code import Code
from connectrpc.errors import ConnectError

METHODS = {
    'publish': 'Publish', 'revise': 'Revise', 'withdraw_dependent': 'WithdrawDependent',
    'report_performed': 'ReportPerformed', 'verify': 'Verify', 'decide_artifact': 'DecideArtifact',
    'resolve': 'Resolve', 'acknowledge_continuation': 'AcknowledgeContinuation',
    'read_request': 'ReadRequest', 'read_dependants': 'ReadDependants', 'read_artifact': 'ReadArtifact',
    'list_requests': 'ListRequests', 'watch': 'Watch',
    'list_requests_v2': 'ListRequestsV2', 'watch_v2': 'WatchV2',
}


class _Context:
    def __init__(self, context):
        self.context = context

    def invocation_metadata(self):
        key = self.context.request_headers.get('idempotency-key', '')
        return [('idempotency-key', key)]

    def abort(self, status, message):
        code = Code.__members__[status.name.replace('CANCELLED', 'CANCELED')]
        raise ConnectError(code, message)


class DirectConnectServer:
    """One Python process, no gRPC forwarding hop and no additional owner store."""
    def __init__(self, path, principal_id='requester-a', fault_plan=None, *,
                 owner_factory=_Server, middleware=None, address=('127.0.0.1', 0)):
        self.owner = owner_factory(path, principal_id, fault_plan)
        projection = _GrpcOwner(SimpleNamespace(httpd=self.owner))
        def method(rpc):
            async def invoke(service, request, context):
                return await asyncio.to_thread(call, request, context)
            def call(request, context):
                try:
                    return getattr(projection, rpc)(request, _Context(context))
                except Conflict as exc:
                    raise ConnectError(Code.ABORTED, str(exc)) from exc
                except ResponseLost as exc:
                    raise ConnectError(Code.UNAVAILABLE, 'test owner response unavailable') from exc
                except ValueError as exc:
                    raise ConnectError(Code.INVALID_ARGUMENT, str(exc)) from exc
            return invoke
        service = type('DirectOwnerProjection', (), {name: method(rpc) for name, rpc in METHODS.items()})()
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(address)
        self.socket.listen(64)
        application = OwnerOperationsASGIApplication(service)
        if middleware is not None:
            application = middleware(application)
        config = uvicorn.Config(application, host='127.0.0.1',
                                log_level='critical', access_log=False, lifespan='off')
        self.httpd = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.httpd.run, kwargs={'sockets': [self.socket]}, daemon=True)

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.socket.getsockname()[1]}'

    def __enter__(self):
        import time
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.httpd.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('direct Connect ASGI startup failed')
            time.sleep(0.01)
        return self

    def __exit__(self, *args):
        self.httpd.should_exit = True
        self.thread.join(timeout=5)
        self.socket.close()
        self.owner.server_close()
        if self.thread.is_alive():
            raise RuntimeError('direct Connect ASGI server did not stop')
