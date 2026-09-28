"""Bounded retries of identical API2 requests, with a durable metadata ledger."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
import time
import urllib.error


RETRY_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_RETRIES = 5
POLICY = {
    'schema_version': 'api2_request_retry_v1',
    'max_retries': MAX_RETRIES,
    'max_attempts': MAX_RETRIES + 1,
    'retry_gap_seconds': 30,
    'retry_http_statuses': sorted(RETRY_HTTP_STATUSES),
    'retry_network_errors': True,
    'retry_invalid_response': False,
    'retry_room_automatically': False,
    'relay_total_timeout': 'existing upstream timeout minus 5 seconds',
}


def now():
    return datetime.now(timezone.utc).isoformat()


class AttemptLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def __call__(self, record):
        line = json.dumps({'timestamp': now(), **record}, allow_nan=False) + '\n'
        with self.lock:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())


class BufferedResponse(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def is_retryable(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRY_HTTP_STATUSES
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return not isinstance(exc.reason, ssl.SSLError)
    return isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead))


class RetryingOpener:
    """One retry owner for a logical request, including failures while reading.

    For the loopback relay, attempts share the existing request timeout. This
    returns before the evaluator's socket deadline, preventing its own retry
    from overlapping a still-running upstream request. Direct probes have no
    outer socket and retain their original timeout for each attempt.
    """

    def __init__(self, opener, ledger, redact, credential, *, stop=None,
                 shared_timeout=True, wait=None, clock=time.monotonic, max_retries=None):
        self.opener = opener
        self.ledger = ledger
        self.redact = redact
        self.credential = credential
        self.stop = stop if stop is not None else threading.Event()
        self.shared_timeout = shared_timeout
        self.wait = wait if wait is not None else self.stop.wait
        self.clock = clock
        self.max_retries = POLICY['max_retries'] if max_retries is None else max_retries
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ValueError('max_retries must be a nonnegative integer')
        self.logical_calls = []
        self.calls_lock = threading.Lock()

    def open(self, request, *, timeout):
        logical_id = secrets.token_hex(12)
        wire = request.data or b''
        payload = json.loads(wire) if wire else {}
        images = sum(
            part.get('type') == 'image_url'
            for message in payload.get('messages', [])
            if isinstance(message.get('content'), list)
            for part in message['content'] if isinstance(part, dict)
        )
        metadata = {
            'logical_request_id': logical_id,
            'method': request.get_method(),
            'operation': 'chat_completion' if wire else 'model_catalog',
            'request_sha256': hashlib.sha256(wire).hexdigest(),
            'request_bytes': len(wire),
            'model': payload.get('model'),
            'reasoning_effort': payload.get('reasoning_effort'),
            'max_completion_tokens': payload.get('max_completion_tokens'),
            'image_count': images,
        }
        audit = {'logical_request_id': logical_id, 'attempts': 0}
        with self.calls_lock:
            self.logical_calls.append(audit)
        deadline = self.clock() + max(1, timeout - 5) if self.shared_timeout else None
        for attempt in range(1, self.max_retries + 2):
            if self.stop.is_set():
                self.ledger({**metadata, 'attempt': attempt,
                             'event': 'request_cancelled_before_send'})
                raise InterruptedError('Campaign stopped before upstream attempt')
            attempt_timeout = max(.001, deadline - self.clock()) if deadline else timeout
            started = self.clock()
            self.ledger({**metadata, 'attempt': attempt, 'event': 'attempt_started',
                         'timeout_seconds': attempt_timeout})
            audit['attempts'] = attempt
            status = None
            try:
                with self.opener.open(request, timeout=attempt_timeout) as response:
                    status = response.status
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    headers = response.headers
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError('API response exceeded size limit')
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    status = exc.code
                    try:
                        raw_error = exc.read(16384)
                    except (OSError, http.client.HTTPException):
                        raw_error = b'Could not read HTTP error body'
                    finally:
                        exc.close()
                    detail = raw_error.decode(errors='replace')
                    # Downstream diagnostics must still be able to read it.
                    outgoing = urllib.error.HTTPError(request.full_url, exc.code,
                        exc.reason, exc.headers, io.BytesIO(raw_error))
                else:
                    detail, outgoing = str(exc), exc
                retryable = is_retryable(exc)
                can_retry = retryable and attempt <= self.max_retries
                if deadline is not None:
                    can_retry = can_retry and deadline - self.clock() > POLICY['retry_gap_seconds'] + 1
                self.ledger({**metadata, 'attempt': attempt, 'event': 'attempt_failed',
                    'http_status': status, 'error_type': type(exc).__name__,
                    'error': self.redact(detail, self.credential),
                    'elapsed_seconds': round(self.clock() - started, 3),
                    'retryable': retryable, 'retry_scheduled': can_retry,
                    'tokens_usage': None,
                    'retry_gap_seconds': POLICY['retry_gap_seconds'] if can_retry else None})
                if not can_retry:
                    raise outgoing from None
                print(f"API {status or type(exc).__name__}: retry {attempt}/{self.max_retries} "
                      f"in {POLICY['retry_gap_seconds']}s "
                      f"(request {logical_id})", flush=True)
                if self.wait(POLICY['retry_gap_seconds']) or self.stop.is_set():
                    self.ledger({**metadata, 'event': 'request_cancelled_during_retry_wait',
                                 'attempt': attempt})
                    raise InterruptedError('Campaign stopped during retry wait') from None
                continue
            usage = None
            try:
                data = json.loads(body)
                if isinstance(data, dict) and isinstance(data.get('usage'), dict):
                    usage = {key: value for key, value in data['usage'].items()
                             if key in {'prompt_tokens', 'completion_tokens', 'total_tokens'}
                             and isinstance(value, (int, float)) and not isinstance(value, bool)}
            except (ValueError, UnicodeDecodeError):
                pass  # The sealed consumer owns response/schema validation.
            self.ledger({**metadata, 'attempt': attempt, 'event': 'attempt_response',
                'http_status': status, 'elapsed_seconds': round(self.clock() - started, 3),
                'response_bytes': len(body), 'tokens_usage': usage})
            return BufferedResponse(body, status, headers)
        raise AssertionError('Unreachable retry state')
