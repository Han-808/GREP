"""Request-isolated relay; payload and route contracts come from the pinned relay."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.error
import urllib.request


@contextmanager
def serve(protocol, credential, local_key, halt, record_failure, *, opener=None):
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), protocol.redaction.RejectRedirect())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log authorization headers, payloads or raw exchanges.

        def reply(self, status, body):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def error(self, status, message):
            self.reply(status, json.dumps({'error': {'message': message}}).encode())

        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer '+local_key:
                return self.error(401, 'Invalid loopback credential')
            if self.path != '/v1/chat/completions':
                return self.error(404, 'Unsupported endpoint')
            if halt.is_set():
                return self.error(503, 'Campaign circuit breaker open; no upstream request made')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= protocol.MAX_BODY:
                    raise ValueError('Request body size outside allowed range')
                original = json.loads(self.rfile.read(length))
                payload = protocol.adapt(original)
                timeout = 180 if self.headers.get('X-Evaluation-Preflight') == '1' else 3000
                if original.get('max_tokens') == 64 and payload.get('max_completion_tokens') == 4096:
                    timeout = 240
            except Exception:
                return self.error(400, 'Invalid frozen GPT-5.6 Sol request')
            request = urllib.request.Request(protocol.UPSTREAM, method='POST', data=json.dumps(payload).encode(),
                headers={'Authorization': 'Bearer '+credential, 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=timeout) as response:
                    body = response.read(protocol.MAX_BODY+1)
                    if len(body) > protocol.MAX_BODY:
                        raise ValueError('Upstream response exceeds size limit')
                    data = json.loads(body)
                    if not data.get('choices'):
                        raise ValueError('Upstream did not return a Chat Completions response')
                    if not protocol.is_sol(data.get('model', '')):
                        raise ValueError('Response model does not identify the requested GPT-5.6 Sol route')
                    status = response.status
            except Exception as exc:
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else 502
                detail = exc.read(16384).decode(errors='replace') if isinstance(exc, urllib.error.HTTPError) else str(exc)
                detail = protocol.redaction.redact(protocol.redaction.redact(detail, credential), local_key)
                record_failure({'status': 'request_failed', 'http_status': status, 'error_type': type(exc).__name__,
                    'error': detail, 'upstream_model': 'azure_openai/gpt-5.6-sol', 'automatic_upstream_retry': False})
                return self.error(status, detail)
            self.reply(status, body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
