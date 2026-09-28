"""Audited 97+12 handoff: prepare separately, then consume in place in two batches.

Never modifies the sealed runtime, original campaign, or existing immutable plan.
Only --run can request model calls. The old runner lock is held throughout every
operation, and cancelled-case exceptions require unchanged migration evidence.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import threading
import uuid

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nonrect_fast_v1 import command_executor, content_policy, derive, run
from nonrect_fast_v1.pipeline import (CaseLease, ExternalConflict, Governor, HostProbe, Limits, Pipeline, TERMINAL,
    atomic_write, canonical_sha, cleanup_terminal, read, report_path, safe_output, sha, validate_ready)
from nonrect_fast_v1.resume_capacity import DesktopHostProbe, RunningMemoryGuard

BASE = run.WORKSPACE / 'Support/nonrect_fast_v1'
EXISTING = BASE / 'prepared_remaining_api_20260925'
ADDITIONAL = BASE / 'prepared_missing12_api_20260925'
CONTROL = BASE / 'handoff_remaining12_20260925'
OLD = Path('/Users/han_mohan/Desktop/Layout_DDD/Support/outputs/nonrect_merged30_v8_api2_sol_catalog_20260925')
HANDOFF = Path('/Users/han_mohan/Desktop/Layout_DDD/Support/artifacts/analysis/nonrect_fast_remaining12_handoff_20260925.md')
MISSING = tuple('nr.merged30.sol.' + x for x in (
    'scene_011634.room_003', 'scene_011687.room_000', 'scene_011760.room_000',
    'scene_011809.room_000', 'scene_011838.room_000', 'scene_011923.room_000',
    'scene_011923.room_001', 'scene_011923.room_002', 'scene_011923.room_003',
    'scene_011923.room_004', 'scene_012121.room_000', 'scene_012121.room_001'))
PINNED = ('run.py', 'pipeline.py', 'derive.py', 'content_policy.py', 'replay_report.py', '__init__.py')


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def lock_file(path, *, existing=False):
    path = Path(path)
    flags = os.O_RDONLY if existing else os.O_CREAT | os.O_RDWR
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r' if existing else 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def immutable(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError('Immutable handoff record differs: ' + str(path))
    else:
        atomic_write(path, value)


@contextmanager
def record_failure():
    # Enter only while owning the control lock. Never persist exception text
    # from credential input or transport code.
    try:
        yield
    except BaseException as exc:
        atomic_write(CONTROL / 'status.json', {'status': 'blocked_or_failed',
            'error_type': type(exc).__name__, 'updated_at': now(),
            'automatic_paid_retry': False})
        raise


def check_partition(all_ids, prepared, missing, successes):
    groups = [list(prepared), list(missing), list(successes)]
    if any(len(g) != len(set(g)) for g in groups):
        raise ValueError('Duplicated source case')
    if any(set(groups[i]) & set(groups[j]) for i in range(3) for j in range(i)):
        raise ValueError('Prepared, migrated and historical successful cases overlap')
    if set().union(*map(set, groups)) != set(all_ids):
        raise ValueError('Source cases are missing or unexpected')


def verified_plan(root, runtime_manifest, pins):
    plan = read(root / 'plan.json')
    identity = plan['identity']
    code = {name: sha(run.HERE / name) for name in PINNED}
    if (identity['runner_source_sha256'] != canonical_sha(code)
            or identity['runtime_tree_sha256'] != runtime_manifest['source_tree_sha256']
            or identity['input_pins'] != pins
            or identity['evaluation_mode'] != 'api'):
        raise ValueError('Saved runner/runtime/input identity mismatch')
    return plan


def old_snapshot():
    cancelled, successes = {}, {}
    for statepath in sorted((OLD / 'rooms').glob('*/state.json')):
        state = read(statepath)
        case = statepath.parent.name
        if state['case_id'] != case:
            raise ValueError('Old case identity mismatch')
        row = {'state_sha256': sha(statepath), 'status': state['status']}
        if state['status'] == 'complete':
            report = Path(state['report'])
            if not report.resolve().is_relative_to(statepath.parent.resolve()) or sha(report) != state['report_sha256']:
                raise ValueError('Historical success report changed')
            row.update(report=str(report), report_sha256=state['report_sha256'])
            successes[case] = row
        elif state['status'] == 'cancelled':
            if any((statepath.parent / n).exists() for n in ('dataset', 'materialized', 'evaluation', 'initial_render')):
                raise ValueError('Cancelled case still has live/uncleaned artifacts')
            cancelled[case] = row
        else:
            raise ValueError('Unexpected old campaign state')
    if set(cancelled) != set(MISSING) or len(successes) != 17:
        raise ValueError('Old campaign does not match the authorized 17+12 handoff')
    return {'cancelled': cancelled, 'successes': successes, 'plan_sha256': sha(OLD / 'plan.json')}


def check_migration_case(work, migration):
    source = read(work / 'identity.json')['task']['source_case_id']
    old = OLD / 'rooms' / source
    row = migration['old']['cancelled'].get(source)
    if row is None:
        if old.exists() or old.is_symlink():
            raise ExternalConflict('Old campaign now owns unclaimed case: ' + source)
    elif (old.is_symlink() or sha(old / 'state.json') != row['state_sha256']
          or read(old / 'state.json')['status'] != 'cancelled'
          or any((old / n).exists() for n in ('dataset', 'materialized', 'evaluation', 'initial_render'))):
        raise ExternalConflict('Migrated cancelled case changed: ' + source)


class MemoryUngatedGovernor(Governor):
    """Explicit user opt-out of memory/swap admission; observations stay factual."""
    def acquire(self, stage):
        limits = self.limits
        disk = limits.preparation_growth_gib if stage == 'prepare' else limits.evaluation_growth_gib
        try:
            observation = self.last = self.probe()
        except Exception as exc:
            self.reason = 'host_probe_unavailable:' + type(exc).__name__
            return None
        cpu_startup = sum(limits.startup_cpu_percent for token in self.tokens.values()
                          if self.clock() - token['start'] < limits.startup_seconds)
        checks = {
            'disk': observation['free_disk_gib'] >= limits.minimum_free_gib
                    + sum(token['disk'] for token in self.tokens.values()) + disk,
            'cpu': ((observation['cpu_idle_percent'] is not None
                     and observation['cpu_idle_percent'] >= limits.minimum_cpu_idle_percent
                         + cpu_startup + limits.startup_cpu_percent)
                    if 'cpu_idle_percent' in observation
                    else observation['load_per_cpu'] <= limits.max_load_per_cpu),
        }
        self.reason = next((key for key, okay in checks.items() if not okay), None)
        if self.reason:
            return None
        token = uuid.uuid4().hex
        self.tokens[token] = {'start': self.clock(), 'memory': 0, 'disk': disk, 'stage': stage}
        return token


class ResourceUngatedGovernor(Governor):
    """Explicit user-owned capacity; host observations do not gate admission."""
    def acquire(self, stage):
        try:
            self.last = self.probe()
        except Exception as exc:
            self.last = {'observation_error_type': type(exc).__name__}
        self.reason = None
        token = uuid.uuid4().hex
        self.tokens[token] = {'start': self.clock(), 'memory': 0, 'disk': 0, 'stage': stage}
        return token


class MigratedStages(run.Stages):
    def __init__(self, *args, migration, reserve_gib, rss_gib, memory_gates=True, disk_guard=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.migration, self.reserve_gib, self.rss_gib = migration, reserve_gib, rss_gib
        self.memory_gates = memory_gates
        self.disk_guard = disk_guard

    def conflict_check(self, work):
        check_migration_case(work, self.migration)

    def cmd(self, argv, log, stop, work, *, stage='prepare', timeout=3600):
        monitor = (RunningMemoryGuard(work, self.reserve_gib, self.rss_gib)
                   if getattr(self, 'memory_gates', True) else None)
        def check():
            self.conflict_check(work)
            if monitor is not None:
                monitor()
        command_executor.command(argv, log, self.env, stop, timeout=timeout, work=work,
            growth_gib=self.limits.preparation_growth_gib if stage == 'prepare' else self.limits.evaluation_growth_gib,
            conflict_check=check, disk_guard=getattr(self, 'disk_guard', True))


class SuccessCleanupPipeline(Pipeline):
    """Keep failed inputs for review; clean successes only after replay proof."""
    def save(self, results, *args, **kwargs):
        super().save(results, *args, **kwargs)
        for state in results:
            if state['status'] not in {'complete', 'not_score_eligible'}:
                continue
            root = self.output / 'rooms' / state['case_id']
            if (root / 'cleanup.json').exists():
                continue
            lease = CaseLease(root, read(root / 'identity.json'))
            try:
                cleanup_terminal(lease, enabled=True)
            finally:
                lease.close()


def effective_limits(plan, args):
    if not 1 <= args.workers <= 12:
        raise ValueError('Worker ceiling must be 1..12; resource guards still apply')
    if not 4 <= args.desktop_memory_reserve_gib <= 16 or not 8 <= args.worker_memory_gib <= 20:
        raise ValueError('Desktop reserve must be 4..16 GiB and per-case RSS reservation 8..20 GiB')
    if not 6 <= args.evaluation_growth_gib <= 12:
        raise ValueError('Evaluation growth reservation must be 6..12 GiB')
    return replace(Limits(**plan['limits']), preparation_workers=args.workers,
        evaluation_workers=args.workers, preparation_growth_gib=6,
        evaluation_growth_gib=args.evaluation_growth_gib,
        preparation_memory_gib=args.worker_memory_gib, evaluation_memory_gib=args.worker_memory_gib,
        reserve_memory_gib=max(8, args.desktop_memory_reserve_gib), ready_gib=max(80, plan['limits']['ready_gib']),
        backpressure_timeout=900)


def create_handoff(manifest, inventory, pins):
    old = old_snapshot()
    prior = verified_plan(EXISTING, manifest, pins)
    check_partition([t['case_id'] for t in inventory], [t['source_case_id'] for t in prior['tasks']], MISSING, old['successes'])
    migration_path = CONTROL / 'migration.json'
    migration = {'schema_version': 'nonrect_explicit_cancelled_migration_v1',
        'handoff_path': str(HANDOFF), 'handoff_sha256': sha(HANDOFF),
        'authorization': 'User confirmed execute handoff in preparation task on 2026-09-25',
        'old_root': str(OLD), 'old': old, 'existing_plan_sha256': sha(EXISTING / 'plan.json'),
        'old_runner_and_cleanup_locks_acquired': True,
        'mapping': [{'source_case_id': c, 'case_id': c.replace('nr.merged30.', 'nr.fastv1.'),
                     'destination': str(ADDITIONAL)} for c in MISSING],
        'historical_success_relabelled': False, 'automatic_paid_retry': False}
    immutable(migration_path, migration)
    tasks = [{**t, 'source_case_id': t['case_id'], 'case_id': t['case_id'].replace('nr.merged30.', 'nr.fastv1.')}
             for c in MISSING for t in inventory if t['case_id'] == c]
    identity = {**prior['identity'], 'explicit_migration_sha256': sha(migration_path)}
    settings = replace(Limits(**prior['limits']), preparation_workers=3, evaluation_workers=3,
                       preparation_growth_gib=6, evaluation_growth_gib=6, ready_capacity=12, ready_gib=80)
    additional = {'identity': identity, 'tasks': tasks, 'limits': asdict(settings)}
    immutable(ADDITIONAL / 'plan.json', additional)
    evaluation = {'schema_version': 'nonrect_two_batch_evaluation_manifest_v1',
        'source_count': 109, 'historical_success_count': 17, 'copy_prepared_data': False,
        'migration_sha256': sha(migration_path),
        'batches': [{'output_root': str(r), 'plan_sha256': sha(r / 'plan.json'),
                    'source_case_ids': [t['source_case_id'] for t in read(r / 'plan.json')['tasks']]}
                   for r in (EXISTING, ADDITIONAL)]}
    immutable(CONTROL / 'evaluation_manifest.json', evaluation)
    return migration


def ready_preflight(root, plan, *, allow_terminal=False):
    ready = terminal = 0
    for task in plan['tasks']:
        lease = CaseLease(root / 'rooms' / task['case_id'], {'campaign': plan['identity'], 'task': task})
        try:
            state = read(lease.root / 'state.json')
            if state.get('identity_sha256') != canonical_sha(lease.identity):
                raise ValueError('State identity mismatch')
            if allow_terminal and state['status'] in {'complete', 'not_score_eligible'}:
                report = report_path(lease.root, state['report'])
                if sha(report) != state['report_sha256']:
                    raise ValueError('Terminal report changed')
                replay = read(report_path(lease.root, state['scoring_replay_receipt']))
                if replay.get('status') != 'passed' or replay.get('report_sha256') != state['report_sha256']:
                    raise ValueError('Terminal scoring replay proof changed')
                terminal += 1
            else:
                if state['status'] != 'ready' or (lease.root / 'consuming.json').exists():
                    raise ValueError('Case is not ready; explicit review needed: ' + task['case_id'])
                validate_ready(lease)
                ready += 1
        finally:
            lease.close()
    return {'ready': ready, 'terminal': terminal}


def audit_override(args, plans):
    label = uuid.uuid4().hex
    source_root = CONTROL / 'execution_override_sources' / label
    sources = {}
    for name in (*PINNED, 'handoff_execution.py', 'resume_capacity.py', 'command_executor.py'):
        src = run.HERE / name
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / name).write_bytes(src.read_bytes())
        sources[name] = sha(src)
    path = CONTROL / 'execution_overrides' / (label + '.json')
    atomic_write(path, {'schema_version': 'nonrect_handoff_execution_override_v1', 'created_at': now(),
        'mode': 'evaluate' if args.run else 'prepare', 'source_sha256': sources,
        'plans': [{'root': str(r), 'plan_sha256': sha(r / 'plan.json'),
                   'original_limits': p['limits'], 'effective_limits': asdict(effective_limits(p, args))} for r,p in plans],
        'maximum_workers': args.workers,
        'memory_gates_enabled': not args.no_memory_gate,
        'memory_gate_authorization': 'User explicitly requested no memory thresholds on 2026-09-25' if args.no_memory_gate else None,
        'running_rss_cap_gib': None if args.no_memory_gate else args.worker_memory_gib,
        'desktop_reserve_gib': None if args.no_memory_gate else args.desktop_memory_reserve_gib,
        'kernel_pressure_must_equal': None if args.no_memory_gate else 1,
        'sustained_swap_stop_mib_per_second': None if args.no_memory_gate else 64,
        'resource_gates_enabled': not args.no_resource_gates,
        'cpu_admission_enabled': not args.no_resource_gates,
        'disk_admission_enabled': not args.no_resource_gates,
        'disk_runtime_guard_enabled': not args.no_resource_gates,
        'resource_gate_authorization': 'User explicitly confirmed disabling CPU/memory/disk admission and runtime thresholds on 2026-09-25' if args.no_resource_gates else None,
        'minimum_free_disk_gib': None if args.no_resource_gates else 30,
        'evaluation_growth_limit_gib': None if args.no_resource_gates else args.evaluation_growth_gib,
        'preparation_evaluation_overlap': False, 'cleanup_successes_only': True,
        'model_requests_enabled': args.run, 'score_or_protocol_changed': False,
        'old_runner_and_cleanup_locks_held': True})
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ('initialize', 'prepare', 'preflight', 'run'):
        mode.add_argument('--' + name, action='store_true')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--worker-memory-gib', type=float, default=20)
    parser.add_argument('--desktop-memory-reserve-gib', type=float, default=6)
    parser.add_argument('--evaluation-growth-gib', type=float, default=6)
    parser.add_argument('--no-memory-gate', action='store_true',
                        help='Explicitly disable runner memory/RSS/pressure/swap gates; OS limits still apply')
    parser.add_argument('--no-resource-gates', action='store_true',
                        help='Disable runner CPU/memory/swap/disk thresholds; preserve locks, stop, deadlines and OS errors')
    args = parser.parse_args(argv)
    if args.no_resource_gates:
        args.no_memory_gate = True
    for root in (CONTROL, ADDITIONAL):
        safe_output(root).mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *unused: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with ExitStack() as stack:
            stack.enter_context(lock_file(CONTROL / 'runner.lock'))
            stack.enter_context(record_failure())
            stack.enter_context(lock_file(OLD / 'runner.lock', existing=True))
            stack.enter_context(lock_file(OLD / 'cleanup.lock', existing=True))
            for root in (EXISTING, ADDITIONAL):
                stack.enter_context(lock_file(root / 'runner.lock'))
            manifest = derive.verify(run.DEFAULT_RUNTIME)
            base = run.load('nonrect_handoff_inventory', run.DEFAULT_RUNTIME / 'scripts/nonrect_merged30_v8_api2_sol_catalog/run.py')
            inventory, pins = base.inventory()
            migration = create_handoff(manifest, inventory, pins)
            plans = [(root, verified_plan(root, manifest, pins)) for root in (EXISTING, ADDITIONAL)]
            for _, plan in plans:
                effective_limits(plan, args)
            if args.initialize:
                status = {'status': 'initialized', 'existing': ready_preflight(*plans[0]), 'additional_count': 12}
            elif args.preflight:
                status = {'status': 'offline_preflight_passed', 'batches': [ready_preflight(r,p,allow_terminal=True) for r,p in plans],
                          'credential_in_environment': bool(os.environ.get('API2_APP_CREDENTIAL')), 'model_requests_made': False,
                          'maximum_workers': args.workers, 'resource_gates_enabled': not args.no_resource_gates,
                          'memory_gates_enabled': not args.no_memory_gate}
            else:
                if args.run:
                    for root, plan in plans:
                        ready_preflight(root, plan, allow_terminal=True)
                override = audit_override(args, plans)
                atomic_write(CONTROL / 'status.json', {'status': 'awaiting_credential_and_api_gate' if args.run else 'preparing',
                             'started_at': now(), 'execution_override': str(override), 'pid': os.getpid()})
                if args.run:
                    env = stack.enter_context(run.live_environment(run.DEFAULT_RUNTIME, base, CONTROL, stop))
                else:
                    env = base.clean_environment()
                    for key in list(env):
                        if any(t in key.upper() for t in ('CREDENTIAL', 'API_KEY', 'ACCESS_TOKEN')):
                            env.pop(key)
                results = []
                for root, plan in (plans if args.run else plans[1:]):
                    if stop.is_set():
                        break
                    settings = effective_limits(plan, args)
                    env[content_policy.ENV] = plan['identity']['content_fingerprint_validation']
                    stages = MigratedStages(run.DEFAULT_RUNTIME, env, settings, migration=migration,
                                            reserve_gib=args.desktop_memory_reserve_gib, rss_gib=args.worker_memory_gib,
                                            memory_gates=not args.no_memory_gate, disk_guard=not args.no_resource_gates)
                    def no_preparation(*unused):
                        raise ValueError('Evaluation cannot regenerate missing inputs')
                    pipeline = SuccessCleanupPipeline(root, plan['identity'], settings,
                        no_preparation if args.run else stages.prepare, stages.evaluate,
                        probe=DesktopHostProbe(root, args.desktop_memory_reserve_gib, args.worker_memory_gib),
                        stop=stop, prepare_only=not args.run, cleanup=False, retry_failed=False)
                    if args.no_resource_gates:
                        pipeline.governor = ResourceUngatedGovernor(settings, HostProbe(root))
                    elif args.no_memory_gate:
                        pipeline.governor = MemoryUngatedGovernor(settings, HostProbe(root))
                    atomic_write(CONTROL / 'status.json', {'status': 'evaluating' if args.run else 'preparing',
                        'active_batch': str(root), 'execution_override': str(override), 'pid': os.getpid(), 'updated_at': now()})
                    result = pipeline.execute(plan['tasks'])
                    results.append({'root': str(root), 'status': result['status'], 'ready': len(result['ready']),
                                    'results': len(result['results']), 'waiting': len(result['waiting'])})
                    if args.run and result['status'] != 'finished':
                        break
                success = (not stop.is_set() and
                    (len(results) == 2 and all(r['status'] == 'finished' for r in results) if args.run else
                     len(results) == 1 and results[0]['ready'] == 12 and not results[0]['waiting'] and not results[0]['results']))
                status = {'status': ('evaluation_complete' if args.run else 'additional_prepared') if success else 'needs_review_or_resource_wait',
                          'batches': results, 'execution_override': str(override), 'updated_at': now()}
            atomic_write(CONTROL / 'status.json', status)
            print(json.dumps(status), flush=True)
            return 0 if status['status'] in {'initialized', 'offline_preflight_passed', 'additional_prepared', 'evaluation_complete'} else 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
