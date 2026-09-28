#!/usr/bin/env python3
"""Diagnostic only: one API2 catalog GET, then at most one text and image POST.

Credentials are entered in a terminal, used in memory, and never saved.
Catalog failure does not suppress the independent, single historical-route POST.
No evaluation, proxy, automatic retry, or route fallback is started.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import getpass
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.request
import warnings

from diagnose_feedback_api2 import ROOT, RejectRedirect, error_summary, redact

BASE = 'http://llm-api.model-eval.woa.com/v1'
HISTORICAL_MODEL = 'api_azure_openai_gpt-5.6-sol'
CATALOG_AZURE_MODEL = 'azure_openai/gpt-5.6-sol'
MAX_CATALOG = 2*1024*1024
MAX_RESPONSE = 2*1024*1024


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirect())


def model_is_sol(value):
    return bool(re.search(r'gpt[-_]5[._-]6[-_]sol(?=$|[/_.-])', str(value), re.I))


def read_response(handle, limit):
    raw = handle.read(limit+1)
    if len(raw) > limit:
        raise ValueError('Response exceeded diagnostic size limit')
    return json.loads(raw)


def catalog(credential, transport):
    result = {'attempts': 1, 'status': 'unverified', 'http_status': None,
              'sol_model_ids': [], 'total_model_entries': None}
    request = urllib.request.Request(BASE+'/models',
        headers={'Authorization': 'Bearer '+credential})
    try:
        with transport.open(request, timeout=30) as response:
            data = read_response(response, MAX_CATALOG)
            result['http_status'] = response.status
        if not isinstance(data.get('data'), list):
            result['status'] = 'unrecognized_catalog'
        else:
            result['total_model_entries'] = len(data['data'])
            result['sol_model_ids'] = sorted({row['id'] for row in data['data']
                if isinstance(row, dict) and isinstance(row.get('id'), str)
                and model_is_sol(row['id'])})
            result['status'] = 'catalog_read'
    except urllib.error.HTTPError as exc:
        result.update(status='http_error', http_status=exc.code,
                      error=error_summary(exc.read(16384), credential))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result.update(status='catalog_error', error_type=type(exc).__name__)
    return result


def red_png_data_url():
    from PIL import Image
    image = Image.new('RGB', (128, 128), (255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()


def post(credential, transport, model, *, vision):
    stage = 'vision' if vision else 'text'
    content = ([{'type': 'text', 'text': 'What is the solid color in this image? Reply exactly RED.'},
                {'type': 'image_url', 'image_url': {'url': red_png_data_url(), 'detail': 'high'}}]
               if vision else 'Reply exactly OK.')
    payload = {'model': model, 'messages': [{'role': 'user', 'content': content}],
               'max_completion_tokens': 4096, 'reasoning_effort': 'xhigh', 'stream': False}
    request = urllib.request.Request(BASE+'/chat/completions', method='POST',
        data=json.dumps(payload).encode(), headers={
            'Authorization': 'Bearer '+credential, 'Content-Type': 'application/json'})
    result = {'stage': stage, 'attempts': 1, 'http_status': None,
              'status': 'unverified', 'model_sent': model, 'max_completion_tokens': 4096,
              'reasoning_effort': 'xhigh'}
    try:
        with transport.open(request, timeout=180) as response:
            data = read_response(response, MAX_RESPONSE)
            result['http_status'] = response.status
        if not isinstance(data.get('choices'), list) or not data['choices']:
            result['status'] = 'unrecognized_response'
            return result
        choice = data['choices'][0]
        content = (choice.get('message') or {}).get('content')
        expected = 'RED' if vision else 'OK'
        observed_model = str(data.get('model') or '')
        result.update(response_model=redact(observed_model, credential),
                      response_is_sol=model_is_sol(observed_model),
                      finish_reason=choice.get('finish_reason'),
                      expected_reply_received=isinstance(content, str)
                        and content.strip().upper() == expected)
        result['status'] = ('passed' if result['response_is_sol']
            and result['finish_reason'] == 'stop' and result['expected_reply_received']
            else 'response_unverified')
    except urllib.error.HTTPError as exc:
        result.update(status='http_error', http_status=exc.code,
                      error=error_summary(exc.read(16384), credential))
    except (OSError, ValueError, TypeError, AttributeError, IndexError) as exc:
        result.update(status='request_error', error_type=type(exc).__name__)
    return result


def diagnose(credential, transport, *, model_override=None):
    result = {'schema_version': 'api2_gpt56_sol_correspondence_v1',
        'diagnostic_only': True, 'experiment_started': False, 'retries': 0,
        'gateway': BASE, 'checked_at': datetime.now(timezone.utc).isoformat()}
    result['catalog'] = catalog(credential, transport)
    ids = result['catalog']['sol_model_ids']
    # Prefer the authenticated catalog's single exact route. Multiple routes
    # are ambiguous; use the known historical alias and never scan all routes.
    model = model_override or (ids[0] if len(ids) == 1 else HISTORICAL_MODEL)
    result['model_selection'] = ('explicit_route' if model_override else
        'unique_catalog_route' if len(ids) == 1 else 'historical_route')
    result['model'] = model
    result['text'] = post(credential, transport, model, vision=False)
    if result['text']['status'] == 'passed':
        result['vision'] = post(credential, transport, model, vision=True)
    else:
        result['vision'] = {'status': 'skipped_after_text_failure', 'attempts': 0}
    result['status'] = ('text_and_vision_passed' if result['text']['status'] == 'passed'
                        and result['vision']['status'] == 'passed'
                        else 'not_qualified')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='One GET plus up to two POSTs')
    parser.add_argument('--model-id', choices=(HISTORICAL_MODEL, CATALOG_AZURE_MODEL),
        help='Probe one explicit Sol route; --run required')
    args = parser.parse_args(argv)
    if args.model_id and not args.run:
        parser.error('--model-id requires --run')
    if not args.run:
        print(json.dumps({'status': 'offline_ready', 'gateway': BASE,
                          'historical_model': HISTORICAL_MODEL, 'api_calls': 0}))
        return 0
    if not sys.stdin.isatty():
        raise ValueError('Run in an interactive terminal for hidden credential input')
    print('Diagnostic only: one catalog GET, one Sol text POST, then one image POST if text passes. No retries.', flush=True)
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        credential = getpass.getpass('API2 APP_ID:APP_KEY (hidden): ').strip().split('?', 1)[0]
    app_id, sep, app_key = credential.partition(':')
    if not sep or not app_id or not app_key or any(c.isspace() for c in credential):
        raise ValueError('Invalid APP_ID:APP_KEY format')
    result = diagnose(credential, opener(), model_override=args.model_id)
    del credential, app_id, app_key
    os.umask(0o077)
    target = Path(tempfile.mkdtemp(prefix='nonrect_api2_sol_correspondence_',
                                   dir=ROOT/'Support/outputs'))/'diagnostic.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('Diagnostic: '+str(target))
    return 0 if result['status'] == 'text_and_vision_passed' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted; no evaluation started.', file=sys.stderr)
        raise SystemExit(130)
