"""Explicit API2 GPT-5.6 Sol transport. No route fallback or upstream retries.

The sealed evaluator remains unchanged. Translate its legacy max_tokens field
to GPT-5's max_completion_tokens and retain each role's exact budget, except
the recognizable 64-token native connectivity probe (4096 including reasoning).
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import threading
import urllib.error
import urllib.request

spec = importlib.util.spec_from_file_location('relay_redaction', Path(__file__).resolve().parents[1]/'diagnose_feedback_api2.py')
redaction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redaction)
UPSTREAM = 'http://llm-api.model-eval.woa.com/v1/chat/completions'
MAX_BODY = 128*1024*1024


def adapt(payload):
    if payload.get('model') != 'gpt-5.6-sol':
        raise ValueError('Only the frozen gpt-5.6-sol alias is allowed')
    result = dict(payload)
    result['model'] = 'azure_openai/gpt-5.6-sol'
    result['reasoning_effort'] = 'xhigh'
    if result.get('stream'):
        raise ValueError('This evaluator uses non-streaming requests only')
    messages = result.get('messages', [])
    native_probe = (len(messages) == 2
        and messages[0] == {'role': 'system', 'content': 'Return one short JSON object only.'}
        and isinstance(messages[1].get('content'), list)
        and messages[1]['content'][0] == {'type': 'text', 'text': 'Confirm that this image is visible. Return exactly {"ok":true}.'}
        and result.get('max_tokens') == 64)
    if 'max_tokens' in result:
        if 'max_completion_tokens' in result:
            raise ValueError('Conflicting completion budgets')
        result['max_completion_tokens'] = 4096 if native_probe else result.pop('max_tokens')
        result.pop('max_tokens', None)
    # These were explicitly dropped by the former API2 proxy as well.
    result.pop('temperature', None)
    result.pop('output_config', None)
    return result


def is_sol(value):
    return bool(re.search(r'gpt[-_]5[._-]6[-_]sol(?=$|[/_.-])', str(value), re.I))


@contextmanager
def serve(credential, local_key, halt, record_failure, *, opener=None):
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), redaction.RejectRedirect())

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
                if not 0 < length <= MAX_BODY:
                    raise ValueError('Request body size outside allowed range')
                original = json.loads(self.rfile.read(length))
                payload = adapt(original)
                timeout = 180 if self.headers.get('X-Evaluation-Preflight') == '1' else 3000
                if original.get('max_tokens') == 64 and payload.get('max_completion_tokens') == 4096:
                    timeout = 240
            except Exception:
                return self.error(400, 'Invalid frozen GPT-5.6 Sol request')
            request = urllib.request.Request(UPSTREAM, method='POST', data=json.dumps(payload).encode(),
                headers={'Authorization': 'Bearer '+credential, 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=timeout) as response:
                    body = response.read(MAX_BODY+1)
                    if len(body) > MAX_BODY:
                        raise ValueError('Upstream response exceeds size limit')
                    data = json.loads(body)
                    if not data.get('choices'):
                        raise ValueError('Upstream did not return a Chat Completions response')
                    if not is_sol(data.get('model', '')):
                        raise ValueError('Response model does not identify the requested GPT-5.6 Sol route')
                    status = response.status
            except Exception as exc:
                halt.set()  # Every later request fails locally, not another paid call.
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else 502
                detail = exc.read(16384).decode(errors='replace') if isinstance(exc, urllib.error.HTTPError) else str(exc)
                detail = redaction.redact(redaction.redact(detail, credential), local_key)
                record_failure({'status': 'open', 'http_status': status, 'error_type': type(exc).__name__,
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
