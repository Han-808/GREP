"""One explicit user-authorized retry without the added memory watchdog.

Restricted to the sole outstanding HY4 room. Other ready cases are read-only;
scientific/input validation, locks, disk reserve and existing stage deadlines
remain. No admission CPU/RAM gate, RSS cap or swap/pressure-triggered stop is used.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import signal
import sys
import threading
import uuid

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nonrect_fast_v1 import command_executor, content_policy, derive, run
from nonrect_fast_v1.pipeline import (CaseLease, Limits, atomic_write, canonical_sha,
                                     publish_ready, read, safe_output, sha, validate_ready)

CASE = 'nr.fastv1.hy4.scene_011838.room_000'


def scope(plan, ready_ids):
    tasks = {task['case_id']: task for task in plan['tasks']}
    if CASE not in tasks or set(tasks) - set(ready_ids) not in ({CASE}, set()):
        raise ValueError('This exception applies only to the sole remaining HY4 room')
    return tasks[CASE]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--runtime', type=Path, default=run.DEFAULT_RUNTIME)
    parser.add_argument('--disable-memory-watchdog', action='store_true', required=True)
    args = parser.parse_args(argv)
    output = safe_output(args.output_root)
    derive.verify(args.runtime)
    plan = read(output / 'plan.json')
    ready_ids = [p.parent.name for p in (output / 'rooms').glob('*/ready.json')]
    task = scope(plan, ready_ids)
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *unused: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    with (output / 'runner.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lease = CaseLease(output / 'rooms' / CASE, {'campaign': plan['identity'], 'task': task})
        old_command = run.command
        started = datetime.now(timezone.utc).isoformat()
        record_path = output / 'execution_overrides' / (uuid.uuid4().hex + '.json')
        record = {'schema_version': 'nonrect_single_case_memory_watchdog_exception_v1',
                  'started_at': started, 'case_id': CASE, 'status': 'starting',
                  'authorization': 'User explicitly requested no added memory protection and retry this one case',
                  'concurrent_preparation_limit': 1, 'memory_admission': False,
                  'memory_pressure_stop': False, 'rss_cap': False, 'swap_stop': False,
                  'cpu_admission': False, 'minimum_free_disk_gib': 30,
                  'per_case_disk_growth_gib': 6, 'paid_evaluation_enabled': False,
                  'original_plan_sha256': sha(output / 'plan.json'),
                  'retry_source_sha256': sha(Path(__file__)),
                  'command_executor_sha256': sha(Path(command_executor.__file__))}
        try:
            if (lease.root / 'ready.json').exists():
                validate_ready(lease)
                print(json.dumps({'status': 'already_ready', 'case_id': CASE}))
                return 0
            if (lease.root / 'consuming.json').exists():
                raise ValueError('Case is owned by a consumer')
            atomic_write(record_path, record)
            env = run.load('nonrect_single_retry_base', args.runtime / 'scripts/nonrect_merged30_v8_api2_sol_catalog/run.py').clean_environment()
            for key in list(env):
                if any(token in key.upper() for token in ('CREDENTIAL', 'API_KEY', 'ACCESS_TOKEN')):
                    env.pop(key)
            env[content_policy.ENV] = plan['identity']['content_fingerprint_validation']
            settings = replace(Limits(**plan['limits']), preparation_workers=1, preparation_growth_gib=6)
            stages = run.Stages(args.runtime.resolve(), env, settings,
                                avoid_conflicts_with=Path(plan['identity']['conflict_guard_root']) if plan['identity'].get('conflict_guard_root') else None)
            # Deliberately no RunningMemoryGuard wrapper on this explicit retry.
            run.command = command_executor.command
            summary = read(output / 'queue_summary.json')
            summary.update(status='running_single_case_user_override', active=[{'case_id': CASE, 'stage': 'prepare'}],
                           execution_override=str(record_path), memory_watchdog_enabled=False)
            atomic_write(output / 'queue_summary.json', summary)
            atomic_write(lease.root / 'state.json', {'case_id': CASE, 'status': 'preparing',
                         'identity_sha256': canonical_sha(lease.identity), 'execution_override': str(record_path)})
            print(json.dumps({'status': 'preparing', 'case_id': CASE, 'memory_watchdog_enabled': False}), flush=True)
            stages.prepare(task, lease.root, stop)
            marker = publish_ready(lease)
            atomic_write(lease.root / 'state.json', {'case_id': CASE, 'status': 'ready',
                         'identity_sha256': canonical_sha(lease.identity), 'prepared_bytes': marker['prepared_bytes'],
                         'execution_override': str(record_path)})
            record['status'] = 'ready'
            return 0
        except BaseException as exc:
            record.update(status='failed_prepare', error_type=type(exc).__name__, error=str(exc))
            atomic_write(lease.root / 'state.json', {'case_id': CASE, 'status': 'failed_prepare',
                         'identity_sha256': canonical_sha(lease.identity), 'error_type': type(exc).__name__,
                         'error': str(exc), 'execution_override': str(record_path)})
            raise
        finally:
            run.command = old_command
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            atomic_write(record_path, record)
            summary = read(output / 'queue_summary.json')
            ready_ids = [p.parent.name for p in (output / 'rooms').glob('*/ready.json')]
            summary.update(active=[], ready=ready_ids, waiting=[],
                           all_preparation_complete=len(ready_ids) == len(plan['tasks']),
                           status='prepared_buffer_ready' if len(ready_ids) == len(plan['tasks']) else 'finished_with_failures',
                           results=[] if record['status'] == 'ready' else [read(lease.root / 'state.json')])
            atomic_write(output / 'queue_summary.json', summary)
            lease.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            print(json.dumps({'status': record['status'], 'ready_count': len(ready_ids)}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
