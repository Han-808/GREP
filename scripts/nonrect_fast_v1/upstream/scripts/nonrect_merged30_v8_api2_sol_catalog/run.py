#!/usr/bin/env python3
"""Pinned 30-layout/126-room sealed v8 API2 Sol runner.

Room jobs own isolated preparation and evaluation directories. Twelve outer
jobs each invoke the sealed evaluator with one inner worker: global cap 12,
not twelve workers per model. No implicit paid room retries or source cleanup.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import warnings
import importlib.util
import re

ROOT = Path('/Users/han_mohan/Desktop/Layout_DDD')
HERE = Path(__file__).resolve().parent
SOURCE = ROOT/'Support/artifacts/outputs/non_rectangular_generation/pi_default_matched_merged_success_v1'
RELEASE = ROOT/'Support/artifacts/releases/model_floorplan_unified_polygon_refactor_v8_20260918'
RELEASE_SHA = 'caf95546afeaa220d77adb0c380abef462084bf4f07c004b1c2d32f51024a7ff'
DEFAULT_OUTPUT = ROOT/'Support/outputs/nonrect_merged30_v8_api2_sol_catalog_20260925'
JUDGE_MODEL = 'gpt-5.6-sol'
UPSTREAM_MODEL = 'azure_openai/gpt-5.6-sol'
BLENDER = Path('/Applications/Blender.app/Contents/MacOS/Blender')
LOCK = threading.Lock()
STOP = threading.Event()
HALT = threading.Event()  # Drain existing calls; do not dispatch new rooms/stages.
API_CIRCUIT = threading.Event()  # Only upstream faults close the local relay.
CHILDREN: set[subprocess.Popen] = set()


class LowDisk(OSError):
    """No new paid attempt may start until storage is available."""


class LowMemory(OSError):
    """No new Blender process may start while usable RAM is too low."""


class PreparationLimiter:
    """Honor 12 as a ceiling while reserving RAM for each Blender worker."""

    RESERVE_GIB = 8
    ESTIMATED_WORKER_GIB = 4

    def __init__(self, limit):
        self.limit = limit
        self.active = 0
        self.peak = 0
        self.condition = threading.Condition()

    def available_gib(self):
        result = subprocess.run(['memory_pressure', '-Q'], capture_output=True,
            text=True, check=True, timeout=15)
        matched = re.search(r'System-wide memory free percentage:\s*(\d+)%', result.stdout)
        if not matched:
            raise LowMemory('Cannot read current macOS free memory percentage')
        total_gib = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') / 1024**3
        return total_gib * int(matched.group(1)) / 100

    def __enter__(self):
        no_worker_since = time.monotonic()
        with self.condition:
            while True:
                require_dispatch()
                if self.active < self.limit:
                    free_gib = self.available_gib()
                    if free_gib >= self.RESERVE_GIB + (self.active+1)*self.ESTIMATED_WORKER_GIB:
                        self.active += 1
                        self.peak = max(self.peak, self.active)
                        return self
                if self.active:
                    no_worker_since = time.monotonic()
                elif time.monotonic()-no_worker_since > 300:
                    raise LowMemory('Free memory remained below the Blender reserve for five minutes')
                self.condition.wait(timeout=15)

    def __exit__(self, exc_type, exc, traceback):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()


class DispatchHalted(Exception):
    """A different room failed: retain inputs, do not begin another stage."""


def require_dispatch():
    if STOP.is_set():
        raise InterruptedError('Runner interrupted')
    if HALT.is_set():
        raise DispatchHalted('Campaign dispatch halted; existing work drains')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def inventory():
    manifest = read(SOURCE/'merged_manifest.json')
    if manifest['success_scenes'] != 30 or manifest['success_rooms'] != 126:
        raise ValueError('Merged cohort identity/count changed')
    tasks, pins = [], {str(SOURCE/'merged_manifest.json'): sha(SOURCE/'merged_manifest.json')}
    for entry in manifest['entries']:
        model, scene = entry['model'], entry['scene_id']
        if model not in {'sol', 'kimi', 'hy4'} or entry['relative_directory'] != model+'/'+scene:
            raise ValueError('Unexpected cohort entry')
        directory = SOURCE/entry['relative_directory']
        receipt = read(directory/'merge_receipt.json')
        pins[str(directory/'merge_receipt.json')] = sha(directory/'merge_receipt.json')
        for rel, expected in receipt['files'].items():
            path = directory/rel
            if not path.resolve().is_relative_to(directory.resolve()) or sha(path) != expected:
                raise ValueError('Merged input differs from merge receipt: '+str(path))
            pins[str(path)] = expected
        generated = read(directory/'generated_scene.json')
        preflight = read(directory/'evaluation_preflight.json')
        rooms = generated['room_order']
        if (len(rooms) != entry['room_count'] or len(rooms) != len(set(rooms))
                or len(generated['rooms']) != len(rooms) or rooms != preflight['room_order']
                or preflight['should_run_room_evaluation'] is not True
                or preflight['count_compliance']['factor'] != 1.0
                or preflight['program_mapping']['coverage_compliance']['factor'] != 1.0):
            raise ValueError('Room/preflight contract differs from reviewed cohort')
        for room in rooms:
            tasks.append({'case_id': f'nr.merged30.{model}.{scene}.{room}', 'model': model,
                'scene': scene, 'room': room, 'generation_effort': entry['reasoning_effort']})
    if len(tasks) != 126 or len({r['case_id'] for r in tasks}) != 126:
        raise ValueError('Missing or duplicated rooms')
    for model in ('sol', 'kimi', 'hy4'):
        if sum(t['model'] == model for t in tasks) != 42:
            raise ValueError('Unexpected model room count')
    return tasks, pins


def verify():
    if sha(RELEASE/'release_manifest.json') != RELEASE_SHA:
        raise ValueError('Wrong sealed evaluator manifest')
    for rel, expected in read(RELEASE/'release_manifest.json')['files'].items():
        if sha(RELEASE/rel) != expected:
            raise ValueError('Sealed evaluator changed: '+rel)
    for path, expected in read(HERE/'dependency_pins.json').items():
        if sha(path) != expected:
            raise ValueError('Runner/materializer dependency changed: '+path)
    for path in (BLENDER,):
        if not path.is_file():
            raise FileNotFoundError(path)
    tasks, pins = inventory()
    plans_path = HERE/'offline_plans.json'
    if plans_path.exists():
        plans = read(plans_path)
        if (plans.get('passed') is not True or plans.get('input_pins') != pins
                or {r['case_id'] for r in plans['tasks']} != {t['case_id'] for t in tasks}):
            raise ValueError('Offline full-room planning receipt does not match inputs')
    return tasks, pins


def clean_environment():
    env = dict(os.environ)
    for name in list(env):
        if name.startswith(('JUDGE_', 'VLM_', 'LITELLM_')) or name in {
            'PYTHONPATH', 'PYTHONHOME', 'API2_APP_CREDENTIAL',
            'API2_DIRECT_GATE_RECEIPT', 'API2_DIRECT_GATE_SHA256'}:
            env.pop(name)
    env.update(PYTHONDONTWRITEBYTECODE='1', TOKENIZERS_PARALLELISM='false')
    return env


def request_credential():
    credential = os.environ.get('API2_APP_CREDENTIAL', '')
    if not credential:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            credential = getpass.getpass('API2 APP_ID:APP_KEY (hidden): ')
    credential = credential.split('?', 1)[0].strip()
    if ':' not in credential or any(c.isspace() for c in credential):
        raise ValueError('Expected API2 APP_ID:APP_KEY; credential was not saved')
    return credential


def verify_direct_gate_receipt(path_text, expected_sha, *, now=None):
    """Use only the fresh, exact-route, two-modality receipt from our launcher."""
    path = Path(path_text)
    outputs = ROOT/'Support/outputs'
    if (not path.is_absolute() or path.name != 'diagnostic.json'
            or not path.parent.name.startswith('nonrect_api2_sol_catalog_gate_')
            or path.is_symlink() or path.parent.is_symlink()
            or not path.resolve().is_relative_to(outputs.resolve())):
        raise ValueError('Direct Sol gate receipt path is outside the reviewed output scope')
    if sha(path) != expected_sha:
        raise ValueError('Direct Sol gate receipt hash differs from launcher')
    record = read(path)
    if (record.get('schema_version') != 'api2_sol_full_run_gate_v1'
            or record.get('route') != UPSTREAM_MODEL
            or record.get('gateway') != 'http://llm-api.model-eval.woa.com/v1'
            or record.get('status') != 'passed' or record.get('retries') != 0):
        raise ValueError('Direct Sol gate identity or status differs')
    checked_at = datetime.fromisoformat(record['completed_at'])
    if checked_at.tzinfo is None:
        raise ValueError('Direct Sol gate timestamp has no timezone')
    age = (now or datetime.now(timezone.utc)) - checked_at
    if age.total_seconds() < -30 or age.total_seconds() > 300:
        raise ValueError('Direct Sol gate is stale')
    for stage in ('text', 'vision'):
        item = record.get(stage) or {}
        if (item.get('stage') != stage or item.get('status') != 'passed'
                or item.get('http_status') != 200 or item.get('attempts') != 1
                or item.get('model_sent') != UPSTREAM_MODEL
                or item.get('reasoning_effort') != 'xhigh'
                or item.get('max_completion_tokens') != 4096
                or item.get('response_is_sol') is not True
                or item.get('finish_reason') != 'stop'
                or item.get('expected_reply_received') is not True):
            raise ValueError('Direct Sol gate failed '+stage+' qualification')
    return record


def terminate(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass


def command(argv, log, env, timeout=43200):
    if STOP.is_set():
        raise InterruptedError('Runner interrupted')
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as stream:
        with LOCK:
            if STOP.is_set():
                raise InterruptedError('Runner interrupted')
            process = subprocess.Popen([str(x) for x in argv], cwd=ROOT, env=env,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            CHILDREN.add(process)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            terminate(process)
            raise
        finally:
            with LOCK:
                CHILDREN.discard(process)
    if code:
        if STOP.is_set():
            raise InterruptedError('Subprocess cancelled by user')
        raise RuntimeError('Subprocess failed; see '+str(log))


def uniform_args(work):
    return [sys.executable, '-B', '-I', RELEASE/'scripts/run_uniform_model_evaluation.py',
        '--mode', 'non-rect', '--dataset-root', work/'dataset', '--release-manifest',
        RELEASE/'release_manifest.json', '--max-workers', '1', '--blender-bin', BLENDER]


def require_space(root):
    if shutil.disk_usage(root).free < 30*1024**3:
        raise LowDisk('Less than 30 GiB free; no new room dispatched')


def archive_path(path, work):
    """Keep an interrupted attempt on the same volume for later inspection."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink():
        raise ValueError('Refusing to move symlink: '+str(path))
    attempts = work/'interrupted_artifacts'
    attempts.mkdir(exist_ok=True)
    destination = attempts/path.name
    index = 1
    while destination.exists() or destination.is_symlink():
        destination = attempts/f'{path.name}.{index:03d}'
        index += 1
    path.rename(destination)
    return destination


def verify_materialized(task, work, env):
    command([sys.executable, '-B', '-I', HERE/'verify_materialized.py',
        '--materialized', work/'materialized', '--generation-root', SOURCE,
        '--model', task['model'], '--scene', task['scene'], '--room', task['room']],
        work/'materialized_recheck.log', env, 600)


def adopt_report(task, work):
    report_path = work/'evaluation/cases'/task['case_id']/'evaluation_report.json'
    if not report_path.is_file():
        return None
    report = read(report_path)
    if report.get('case_id', task['case_id']) != task['case_id']:
        raise ValueError('Previous report belongs to another case')
    summary = report['judgement_coverage_summary']
    if not isinstance(summary['eligible'], bool):
        raise ValueError('Previous report has invalid eligibility')
    if summary.get('status') == 'infrastructure_failure' or summary.get('infrastructure_failure_metrics'):
        return None
    return {**task, 'status': 'complete' if summary['eligible'] else 'not_score_eligible',
        'score': summary['score'], 'coverage': summary['judgement_coverage_fraction'],
        'report': str(report_path), 'report_sha256': sha(report_path), 'work': str(work),
        'recovered_from_previous_attempt': True}


def prepare(task, work, env):
    require_dispatch()
    require_space(work)
    receipt = work/'input_receipt.json'
    if receipt.exists():
        command(uniform_args(work), work/'input_recheck.json', env)
        if read(receipt) != read(work/'input_recheck.json'):
            raise ValueError('Prepared input drift; refusing reuse')
        return
    if (work/'dataset').exists():
        archive_path(work/'dataset', work)
    if (work/'initial_render').exists():
        archive_path(work/'initial_render', work)
    if (work/'initial_render_normalized_scene.json').exists():
        archive_path(work/'initial_render_normalized_scene.json', work)
    if (work/'.materialized.building').exists():
        archive_path(work/'.materialized.building', work)
    if (work/'materialized').exists():
        verify_materialized(task, work, env)
    else:
        command([sys.executable, '-B', '-I', HERE/'materialize_room.py', '--generation-root', SOURCE,
            '--model', task['model'], '--scene', task['scene'], '--room', task['room'],
            '--dest', work/'materialized', '--timeout-seconds', '1800'], work/'materialize.log', env, 3600)
    require_dispatch()
    command([sys.executable, '-B', '-I', HERE/'build_case.py', '--room-dir', work/'materialized',
        '--dataset-root', work/'dataset', '--case-id', task['case_id'], '--dataset-id', 'nonrect_merged30_v8_api2_sol',
        '--render-dir', work/'initial_render', '--evidence-worker', HERE/'nonrect_evidence_worker.py'],
        work/'build_case.log', env, 3600)
    command(uniform_args(work), work/'input_check.json', env)
    checked = read(work/'input_check.json')
    if len(checked['cases']) != 1 or checked['cases'][0]['case_id'] != task['case_id']:
        raise ValueError('Prepared case differs from task')
    write(receipt, checked)


def run_room(task, output, env, preparation_slots, prepare_only=False):
    work = output/'rooms'/task['case_id']
    work.mkdir(parents=True, exist_ok=True)
    state_path = work/'state.json'
    old = read(state_path) if state_path.exists() else {}
    if old.get('status') in {'complete', 'not_score_eligible'}:
        if sha(Path(old['report'])) != old['report_sha256']:
            raise ValueError('Completed report hash changed')
        coverage = read(old['report'])['judgement_coverage_summary']
        if coverage.get('status') != 'infrastructure_failure' and not coverage.get('infrastructure_failure_metrics'):
            return old
    if (work/'evaluation').exists():
        recovered = adopt_report(task, work)
        if recovered is not None:
            write(state_path, recovered)
            return recovered
        # User authorized taking over this interrupted run. Preserve every
        # partial paid attempt before rerunning it; never overwrite it.
        if old and not (work/'interrupted_artifacts/previous_state.json').exists():
            write(work/'interrupted_artifacts/previous_state.json', old)
        archive_path(work/'evaluation', work)
    phase = 'prepare'
    try:
        write(state_path, {**task, 'status': 'waiting_preparation_capacity', 'work': str(work)})
        with preparation_slots:
            write(state_path, {**task, 'status': 'preparing', 'work': str(work)})
            prepare(task, work, env)
        if prepare_only:
            state = {**task, 'status': 'prepared', 'work': str(work)}
        else:
            require_dispatch()
            require_space(work)
            phase = 'evaluate'
            write(state_path, {**task, 'status': 'evaluating', 'work': str(work)})
            command([*uniform_args(work), '--input-manifest', work/'input_receipt.json',
                '--output-root', work/'evaluation', '--run'], work/'evaluate.log', env)
            report_path = work/'evaluation/cases'/task['case_id']/'evaluation_report.json'
            report = read(report_path)
            summary = report['judgement_coverage_summary']
            if summary.get('status') == 'infrastructure_failure' or summary.get('infrastructure_failure_metrics'):
                status = 'infrastructure_failure'
                HALT.set()
            else:
                status = 'complete' if summary['eligible'] else 'not_score_eligible'
            state = {**task, 'status': status,
                'score': summary['score'], 'coverage': summary['judgement_coverage_fraction'],
                'report': str(report_path), 'report_sha256': sha(report_path), 'work': str(work)}
        write(state_path, state)
        return state
    except Exception as exc:
        if isinstance(exc, InterruptedError) or STOP.is_set():
            status = 'cancelled'
        elif isinstance(exc, DispatchHalted):
            status = 'paused_dispatch'
        else:
            status = ('blocked_disk' if isinstance(exc, LowDisk) else
                'blocked_memory' if isinstance(exc, LowMemory) else
                'infrastructure_failure' if phase == 'evaluate' and API_CIRCUIT.is_set()
                else 'failed_prepare' if phase == 'prepare' else 'failed_eval')
            # A single materialization failure belongs to that room. Global
            # resource or evaluator faults halt admission; the API relay only
            # closes when the upstream itself fails.
            if status != 'failed_prepare':
                HALT.set()
        state = {**task, 'status': status,
            'phase': phase, 'error_type': type(exc).__name__,
            'work': str(work), 'automatic_paid_retry': False}
        write(state_path, state)
        return state


@contextmanager
def proxy_environment(output, credential):
    gate_path = os.environ.get('API2_DIRECT_GATE_RECEIPT', '')
    gate_sha = os.environ.get('API2_DIRECT_GATE_SHA256', '')
    if not gate_path or not gate_sha:
        raise ValueError('This runner requires a fresh direct text/image gate; use the launcher')
    gate = verify_direct_gate_receipt(gate_path, gate_sha)
    env = clean_environment()
    env['MERGED30_PROXY_KEY'] = secrets.token_hex(32)
    spec = importlib.util.spec_from_file_location('api2_sol_relay', HERE/'relay.py')
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    failure_path = output/('circuit-breaker-'+secrets.token_hex(6)+'.json')

    def record_failure(failure):
        HALT.set()
        with LOCK:
            if not failure_path.exists():
                write(failure_path, {**failure, 'checked_at': datetime.now(timezone.utc).isoformat()})
            evaluators = [process for process in CHILDREN
                if str(RELEASE/'scripts/run_uniform_model_evaluation.py') in
                    [str(arg) for arg in process.args] and '--run' in [str(arg) for arg in process.args]]
        # The upstream circuit is open. Stop owned evaluator processes now so
        # they cannot produce a cascade of local 503-based invalid reports.
        for process in evaluators:
            terminate(process)

    HALT.clear()
    API_CIRCUIT.clear()
    # Fixed gateway, fixed model, no automatic model failover or network retry.
    with relay.serve(credential, env['MERGED30_PROXY_KEY'], API_CIRCUIT, record_failure) as endpoint:
        env.update(JUDGE_ENDPOINT=endpoint, JUDGE_MODEL=JUDGE_MODEL, JUDGE_API_KEY_ENV='MERGED30_PROXY_KEY')
        # Upstream credential stays in this process, not evaluator children.
        env.pop('STANDARD_API_CREDENTIAL', None)
        env.pop('API2_APP_CREDENTIAL', None)
        receipt = output/('preflight-'+secrets.token_hex(6)+'.json')
        write(receipt, {'schema_version': 'api2_sol_direct_gate_adoption_v1',
            'method': 'authenticated_direct_text_and_image_probes',
            'status': 'passed', 'source_receipt': gate_path,
            'source_receipt_sha256': gate_sha,
            'checked_at': gate['checked_at'], 'adopted_at': datetime.now(timezone.utc).isoformat(),
            'judge_model': JUDGE_MODEL, 'upstream_model': UPSTREAM_MODEL,
            'room_jobs_started_at_adoption': 0,
            'checks': [{key: gate[stage].get(key) for key in
                ('stage', 'status', 'http_status', 'response_model', 'finish_reason')}
                for stage in ('text', 'vision')]})
        print('Fresh direct Sol text/image gate adopted: '+str(receipt), flush=True)
        yield env


def save_summary(tasks, output, max_workers, results=None, campaign_status=None,
                 preparation_peak=0, preparation_limit=None):
    observed = {row['case_id']: row for row in (results or [])}
    rows = []
    for task in tasks:
        path = output/'rooms'/task['case_id']/'state.json'
        row = observed.get(task['case_id'])
        if row is None:
            row = read(path) if path.exists() else {**task, 'status': 'not_started'}
        rows.append(row)
    counts = {key: sum(r['status'] == key for r in rows) for key in sorted({r['status'] for r in rows})}
    write(output/'summary.json', {'planned_rooms': len(tasks),
        'finished_jobs': sum(r['status'] in {'complete', 'prepared', 'not_score_eligible', 'infrastructure_failure', 'failed_prepare', 'failed_eval', 'blocked_disk', 'blocked_memory', 'cancelled', 'paused_dispatch', 'needs_review_existing_evaluation'} for r in rows),
        'counts': counts, 'results': rows, 'campaign_status': campaign_status or 'running',
        'evaluator_manifest_sha256': RELEASE_SHA, 'global_max_workers': max_workers,
        'max_preparation_workers': preparation_limit or max_workers,
        'observed_preparation_peak': preparation_peak,
        'room_inner_max_workers': 1, 'automatic_paid_retry': False,
        'judge': JUDGE_MODEL, 'upstream_model': UPSTREAM_MODEL})


def execute(tasks, output, env, max_workers, prepare_only, preparation_workers):
    STOP.clear()
    HALT.clear()
    API_CIRCUIT.clear()
    slots = PreparationLimiter(preparation_workers)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    results, futures = [], {}
    remaining = iter(tasks)

    def fill():
        while len(futures) < max_workers and not HALT.is_set() and not STOP.is_set():
            task = next(remaining, None)
            if task is None:
                break
            futures[executor.submit(run_room, task, output, env, slots, prepare_only)] = task

    try:
        fill()
        save_summary(tasks, output, max_workers, results, preparation_peak=slots.peak,
            preparation_limit=preparation_workers)
        while futures:
            done, _ = wait(futures, timeout=20, return_when=FIRST_COMPLETED)
            if not done:
                save_summary(tasks, output, max_workers, results,
                    preparation_peak=slots.peak, preparation_limit=preparation_workers)
                continue
            for future in done:
                task = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    HALT.set()
                    result = {**task, 'status': 'failed', 'error_type': type(exc).__name__, 'phase': 'runner_validation'}
                    # Preserve any earlier complete receipt for investigation.
                    write(output/'rooms'/task['case_id']/'runner_error.json', result)
                results.append(result)
                if result['status'] not in {'complete', 'prepared', 'not_score_eligible', 'failed_prepare'}:
                    HALT.set()
                print(f"[{len(results)}/{len(tasks)}] {result['case_id']} {result['status']}", flush=True)
            save_summary(tasks, output, max_workers, results, preparation_peak=slots.peak,
                preparation_limit=preparation_workers)
            fill()
    except BaseException:
        STOP.set()
        with LOCK:
            children = list(CHILDREN)
        for child in children:
            terminate(child)
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        # Collect every admitted job after interruption, before final summary.
        for future, task in futures.items():
            if not future.cancelled() and future.done():
                try:
                    results.append(future.result())
                except BaseException:
                    results.append({**task, 'status': 'cancelled' if STOP.is_set() else 'failed'})
        status = ('interrupted' if STOP.is_set() else 'halted' if HALT.is_set()
            else 'finished_with_failures' if any(r['status'] == 'failed_prepare' for r in results)
            else 'finished')
        save_summary(tasks, output, max_workers, results, status, preparation_peak=slots.peak,
            preparation_limit=preparation_workers)
    return 0 if len(results) == len(tasks) and all(r['status'] in {'complete', 'prepared'} for r in results) else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--run', action='store_true')
    action.add_argument('--probe', action='store_true', help='Text + vision only; zero room jobs')
    action.add_argument('--prepare-only', action='store_true', help='Blender preparation only; no model calls')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--max-workers', type=int, default=12)
    parser.add_argument('--max-preparation-workers', type=int, default=12)
    parser.add_argument('--case-id', help='Preparation-only diagnostic; full --run always evaluates all 126 rooms')
    args = parser.parse_args(argv)
    if not 1 <= args.max_workers <= 12 or not 1 <= args.max_preparation_workers <= args.max_workers or (args.case_id and not args.prepare_only):
        parser.error('Workers and preparation workers must be 1..12; case selection is preparation-only')
    os.umask(0o077)
    tasks, input_pins = verify()
    print(json.dumps({'scenes': 30, 'rooms': len(tasks), 'judge': JUDGE_MODEL, 'api2_route': UPSTREAM_MODEL,
        'max_workers': args.max_workers, 'release_manifest_sha256': RELEASE_SHA}), flush=True)
    if not args.run and not args.prepare_only and not args.probe:
        return 0
    if args.run and not (HERE/'offline_plans.json').is_file():
        raise ValueError('Full 126-room offline materialization planning is required')
    output = args.output_root.resolve()
    if (output.is_relative_to(SOURCE) or SOURCE.is_relative_to(output)
            or output.is_relative_to(RELEASE) or RELEASE.is_relative_to(output)):
        raise ValueError('Output overlaps source or sealed evaluator')
    credential = request_credential() if args.run or args.probe else None
    output.mkdir(parents=True, exist_ok=True)
    with (output/'runner.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = {'schema_version': 'nonrect_merged30_v8_api2_sol_catalog_v1', 'tasks': tasks, 'input_pins': input_pins,
            'release_manifest_sha256': RELEASE_SHA, 'runner_pins_sha256': sha(HERE/'dependency_pins.json'),
            'max_workers': args.max_workers, 'initial_preparation_workers': args.max_preparation_workers,
            'judge': JUDGE_MODEL, 'api2_route': UPSTREAM_MODEL, 'reasoning_effort': 'xhigh',
            'minimum_free_gib': 30, 'preflight_policy': 'fresh_direct_gate_then_native_case_preflight_v1'}
        if (output/'plan.json').exists():
            existing = read(output/'plan.json')
            if existing != plan:
                raise ValueError('Existing plan differs; refusing to mix API2 routes')
        else:
            write(output/'plan.json', plan)
        if args.case_id:
            tasks = [t for t in tasks if t['case_id'] == args.case_id]
            if not tasks:
                raise ValueError('Unknown preparation case ID')
        if args.prepare_only:
            return execute(tasks, output, clean_environment(), args.max_workers, True, args.max_preparation_workers)
        save_summary(tasks, output, args.max_workers, campaign_status='preflight',
            preparation_limit=args.max_preparation_workers)
        try:
            with proxy_environment(output, credential) as env:
                if args.probe:
                    save_summary(tasks, output, args.max_workers, campaign_status='probe_passed_no_evaluation',
                        preparation_limit=args.max_preparation_workers)
                    return 0
                return execute(tasks, output, env, args.max_workers, False, args.max_preparation_workers)
        except KeyboardInterrupt:
            # execute() already reconciles in-flight jobs before propagating.
            if read(output/'summary.json')['campaign_status'] == 'preflight':
                save_summary(tasks, output, args.max_workers, campaign_status='preflight_interrupted',
                    preparation_limit=args.max_preparation_workers)
            raise
        except Exception:
            if read(output/'summary.json')['campaign_status'] == 'preflight':
                save_summary(tasks, output, args.max_workers, campaign_status='preflight_failed',
                    preparation_limit=args.max_preparation_workers)
            raise


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted; artifacts retained. No automatic paid retries.', file=sys.stderr)
        raise SystemExit(130)
