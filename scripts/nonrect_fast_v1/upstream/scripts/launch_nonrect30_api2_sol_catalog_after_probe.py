#!/usr/bin/env python3
"""Probe the exact API2 Sol route, then exec the already-pinned 30-scene runner.

The user enters APP_ID:APP_KEY once in a terminal. No evaluation process is
started unless both a direct text request and direct image request succeed.
Credentials are passed only in the replacement runner's environment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings

from probe_api2_gpt56_sol_correspondence import (
    BASE, CATALOG_AZURE_MODEL, catalog, opener, post,
)

ROOT = Path('/Users/han_mohan/Desktop/Layout_DDD')
PYTHON = ROOT/'.venv/bin/python'
RUNNER = ROOT/'scripts/nonrect_merged30_v8_api2_sol_catalog/run.py'
OUTPUT = ROOT/'Support/outputs/nonrect_merged30_v8_api2_sol_catalog_20260925'


def diagnostic(credential, transport):
    record = {'schema_version': 'api2_sol_full_run_gate_v1',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'diagnostic_only': True, 'experiment_started': False,
        'route': CATALOG_AZURE_MODEL, 'gateway': BASE, 'retries': 0}
    record['catalog'] = catalog(credential, transport)
    record['text'] = post(credential, transport, CATALOG_AZURE_MODEL, vision=False)
    record['vision'] = (post(credential, transport, CATALOG_AZURE_MODEL, vision=True)
        if record['text']['status'] == 'passed' else {'status': 'skipped_after_text_failure', 'attempts': 0})
    record['status'] = ('passed' if record['text']['status'] == 'passed'
        and record['vision']['status'] == 'passed' else 'failed')
    record['completed_at'] = datetime.now(timezone.utc).isoformat()
    return record


def main():
    if not sys.stdin.isatty():
        raise ValueError('Interactive terminal required for hidden API2 credential')
    offline = subprocess.run([str(PYTHON), '-B', str(RUNNER)], cwd=ROOT,
        capture_output=True, text=True, timeout=180)
    if offline.returncode:
        print('Frozen evaluator or 126-room input validation failed; no API calls made.', file=sys.stderr)
        print(offline.stderr[-1200:], file=sys.stderr)
        return 2
    overview = json.loads(offline.stdout)
    if (overview.get('scenes'), overview.get('rooms'), overview.get('judge'),
            overview.get('api2_route')) != (30, 126, 'gpt-5.6-sol', CATALOG_AZURE_MODEL):
        raise ValueError('Offline cohort or Judge route identity differs')
    print('Offline cohort verified: 30 model×scene outputs, 126 rooms, Sealed v8.', flush=True)
    print('Next: one authenticated catalog GET, then one text and one image POST on the catalog Azure Sol route. No retries.', flush=True)
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        credential = getpass.getpass('API2 APP_ID:APP_KEY (hidden): ').strip().split('?', 1)[0]
    app_id, separator, app_key = credential.partition(':')
    if not separator or not app_id or not app_key or any(char.isspace() for char in credential):
        raise ValueError('Invalid APP_ID:APP_KEY format')
    result = diagnostic(credential, opener())
    os.umask(0o077)
    target = Path(tempfile.mkdtemp(prefix='nonrect_api2_sol_catalog_gate_',
                                   dir=ROOT/'Support/outputs'))/'diagnostic.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({
        'probe_status': result['status'],
        'catalog_status': result['catalog']['status'],
        'catalog_http_status': result['catalog'].get('http_status'),
        'text_status': result['text']['status'],
        'text_http_status': result['text'].get('http_status'),
        'vision_status': result['vision']['status'],
        'vision_http_status': result['vision'].get('http_status'),
        'diagnostic_path': str(target),
        'experiment_started': False,
    }, ensure_ascii=False), flush=True)
    if result['status'] != 'passed':
        print('Probe failed; evaluation was not launched.', flush=True)
        return 2
    print('Direct Sol text and image probes passed. Starting the pinned full runner...', flush=True)
    env = dict(os.environ)
    env['API2_APP_CREDENTIAL'] = credential
    env['API2_DIRECT_GATE_RECEIPT'] = str(target.resolve())
    env['API2_DIRECT_GATE_SHA256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    argv = [str(PYTHON), '-B', str(RUNNER), '--run', '--max-workers', '12',
        '--max-preparation-workers', '12', '--output-root', str(OUTPUT)]
    os.execve(str(PYTHON), argv, env)
    raise AssertionError('os.execve unexpectedly returned')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted before or during probe; no new evaluation dispatched.', file=sys.stderr)
        raise SystemExit(130)
