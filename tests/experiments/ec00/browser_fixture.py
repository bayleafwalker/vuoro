"""Credential-free loopback browser origin and CORS proxy for candidate comparison."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import httpx


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


class BrowserOrigin:
    def __init__(self, bundle):
        class Handler(_Quiet):
            def do_GET(self):
                data = bundle if self.path == '/client.js' else b'<script src="/client.js"></script>'
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript' if self.path == '/client.js' else 'text/html')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.httpd.server_port}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=5)


class CorsProxy(BrowserOrigin):
    """Same explicit-origin policy for every arm; records actual forwarded bytes."""
    def __init__(self, upstream, origin):
        self.wire = []
        wire = self.wire
        class Handler(_Quiet):
            def reply(self, status, content=b'', content_type='application/json'):
                self.send_response(status)
                if self.headers.get('Origin') == origin:
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                    self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS')
                    self.send_header('Access-Control-Allow-Headers', 'content-type, idempotency-key, connect-protocol-version, connect-timeout-ms')
                    self.send_header('Access-Control-Expose-Headers', 'content-type')
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                try:
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_OPTIONS(self):
                allowed = self.headers.get('Origin') == origin
                wire.append({'method': 'OPTIONS', 'origin': self.headers.get('Origin'), 'allowed': allowed})
                self.reply(204 if allowed else 403)

            def forward(self):
                if self.headers.get('Origin') != origin:
                    self.reply(403)
                    return
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() in ('content-type', 'idempotency-key', 'connect-protocol-version', 'connect-timeout-ms')}
                headers['accept-encoding'] = 'identity'
                try:
                    with httpx.Client(timeout=10) as client:
                        response = client.request(self.command, upstream + self.path, content=body, headers=headers)
                except httpx.HTTPError as exc:
                    wire.append({'method': self.command, 'path': self.path, 'status': 0,
                                 'request': body, 'error': type(exc).__name__})
                    self.close_connection = True
                    return
                wire.append({'method': self.command, 'path': self.path, 'content_type': headers.get('Content-Type', headers.get('content-type')),
                             'request': body, 'status': response.status_code, 'response': response.content})
                self.reply(response.status_code, response.content, response.headers.get('content-type', 'application/octet-stream'))
            do_POST = forward
            do_GET = forward
            do_PATCH = forward
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
