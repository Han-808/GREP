#!/usr/bin/env python3
"""Independent Nonrect derived-v8 runner; default is offline planning only.

--prepare-only fills a bounded ready queue. --mock-evaluate loads complete inputs
without API calls. Only --run enables the existing API2 Sol transport and fresh
text/image gate. Never point this runner at a historical experiment directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import importlib.util
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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nonrect_fast_v1 import content_policy, derive
from nonrect_fast_v1.pipeline import GIB, ExternalConflict, Limits, Pipeline, atomic_write, canonical_sha, read, safe_output, sha

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
DEFAULT_RUNTIME = WORKSPACE / "Support/nonrect_fast_v1/runtime"
BLENDER = Path('/Applications/Blender.app/Contents/MacOS/Blender')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def disk_bytes(path):
    return sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file() and not p.is_symlink())


def command(argv, log, env, stop, *, timeout=43200, work=None, growth_gib=2.0, conflict_check=None):
    """Only owns this newly spawned process group; cancellation never scans PIDs."""
    if stop.is_set():
        raise InterruptedError("Cancelled before subprocess admission")
    if conflict_check:
        conflict_check()
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    baseline = disk_bytes(work) if work else 0
    with log.open('w') as stream:
        process = subprocess.Popen([str(x) for x in argv], cwd=WORKSPACE, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            last_disk_check = 0.0
            while process.poll() is None:
                if stop.wait(0.5):
                    raise InterruptedError("Cancelled owned subprocess")
                if conflict_check:
                    conflict_check()
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Subprocess exceeded stage deadline")
                if work and time.monotonic() - last_disk_check >= 2:
                    last_disk_check = time.monotonic()
                    if shutil.disk_usage(work).free < 30 * GIB:
                        raise OSError("Disk reserve reached; stopping owned worker")
                    if disk_bytes(work) - baseline > growth_gib * GIB:
                        raise OSError("Stage growth reservation exceeded; stopping owned worker")
            if process.returncode:
                raise RuntimeError("Subprocess failed: " + str(log))
            if conflict_check:
                conflict_check()
        except BaseException:
            # Even if the immediate process exited, its Blender descendants can
            # still hold files. The process group belongs exclusively to us.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


class Stages:
    def __init__(self, runtime, env, limits, *, mock=False, avoid_conflicts_with=None):
        self.runtime, self.env, self.limits, self.mock = runtime, env, limits, mock
        self.catalog = runtime / 'scripts/nonrect_merged30_v8_api2_sol_catalog'
        self.base = load('nonrect_fast_inventory_base', self.catalog / 'run.py')
        self.release = runtime / 'evaluator'
        self.avoid_conflicts_with = avoid_conflicts_with

    def conflict_check(self, work):
        if self.avoid_conflicts_with is None:
            return
        source_case = read(work / 'identity.json')['task']['source_case_id']
        external = self.avoid_conflicts_with / 'rooms' / source_case
        if external.exists() or external.is_symlink():
            raise ExternalConflict('Existing campaign now owns source case: ' + source_case)

    def uniform_args(self, work):
        return [sys.executable, '-B', '-I', self.release / 'scripts/run_uniform_model_evaluation.py',
                '--mode', 'non-rect', '--dataset-root', work / 'dataset', '--release-manifest',
                self.release / 'release_manifest.json', '--max-workers', '1', '--blender-bin', BLENDER]

    def cmd(self, argv, log, stop, work, *, stage='prepare', timeout=3600):
        command(argv, log, self.env, stop, timeout=timeout, work=work,
                growth_gib=self.limits.preparation_growth_gib if stage == 'prepare' else self.limits.evaluation_growth_gib,
                conflict_check=lambda: self.conflict_check(work))

    def prepare(self, task, work, stop):
        started = time.monotonic()
        self.conflict_check(work)
        # Recover a complete materialization with the original file checks.
        # Partial artifacts in this owned case can be regenerated. No ready
        # artifact reaches this method and no external output is touched.
        if (work / 'ready.json').exists() or (work / 'consuming.json').exists():
            raise ValueError("Preparation cannot replace queued/in-use artifacts")
        for name in ('dataset', 'initial_render', '.materialized.building'):
            path = work / name
            if path.is_symlink() or any(p.is_symlink() for p in path.rglob('*')):
                raise ValueError("Cannot recover symlink artifacts")
            if path.exists():
                shutil.rmtree(path)
        if (work / 'materialized').exists():
            self.cmd([sys.executable, '-B', '-I', self.catalog / 'verify_materialized.py',
                      '--materialized', work / 'materialized', '--generation-root', self.base.SOURCE,
                      '--model', task['model'], '--scene', task['scene'], '--room', task['room']],
                     work / 'materialized_recheck.log', stop, work)
        else:
            self.cmd([sys.executable, '-B', '-I', self.catalog / 'materialize_room.py',
                      '--generation-root', self.base.SOURCE, '--model', task['model'],
                      '--scene', task['scene'], '--room', task['room'], '--dest', work / 'materialized',
                      '--timeout-seconds', '1800'], work / 'materialize.log', stop, work)
        materialized_seconds = time.monotonic() - started
        self.cmd([sys.executable, '-B', '-I', self.catalog / 'build_case.py', '--room-dir', work / 'materialized',
                  '--dataset-root', work / 'dataset', '--case-id', task['case_id'], '--dataset-id', derive.VERSION,
                  '--render-dir', work / 'initial_render', '--evidence-worker', self.catalog / 'nonrect_evidence_worker.py'],
                 work / 'build_case.log', stop, work)
        self.cmd(self.uniform_args(work), work / 'input_check.json', stop, work)
        if disk_bytes(work) > self.limits.preparation_growth_gib * GIB:
            raise OSError('Prepared case exceeds its reserved size; no ready marker published')
        checked = read(work / 'input_check.json')
        if len(checked['cases']) != 1 or checked['cases'][0]['case_id'] != task['case_id']:
            raise ValueError("Prepared input differs from task")
        atomic_write(work / 'input_receipt.json', checked)
        atomic_write(work / 'preparation_timing.json', {'materialize_seconds': materialized_seconds,
                     'total_prepare_seconds': time.monotonic() - started, 'real_blender': True})
        self.conflict_check(work)

    def evaluate(self, task, work, stop):
        self.cmd(self.uniform_args(work), work / 'consumer_check.json', stop, work, stage='evaluate')
        if read(work / 'consumer_check.json') != read(work / 'input_receipt.json'):
            raise ValueError("Prepared input changed before consumption")
        if self.mock:
            report = {'schema_version': 'nonrect_mock_load_receipt_v1', 'case_id': task['case_id'],
                      'real_blender_preparation': True, 'real_api_qualified': False, 'mock_evaluation': True,
                      'checked_inputs': read(work / 'consumer_check.json'),
                      'content_fingerprint_validation': self.env[content_policy.ENV]}
            status = 'mock_evaluated'
        else:
            old_eval = work / 'evaluation'
            if old_eval.exists():
                # Explicit --retry-failed only. Keep the prior receipt with its
                # own identity; no automatic room retry occurs in the scheduler.
                prior = work / ('prior_evaluation_' + secrets.token_hex(5))
                old_eval.rename(prior)
            self.cmd([*self.uniform_args(work), '--input-manifest', work / 'input_receipt.json',
                      '--output-root', old_eval, '--run'], work / 'evaluate.log', stop, work,
                     stage='evaluate', timeout=43200)
            report = read(old_eval / 'cases' / task['case_id'] / 'evaluation_report.json')
            case_state = read(old_eval / 'cases' / task['case_id'] / 'case_run_manifest.json')
            if case_state.get('status') != 'complete':
                raise ValueError('Evaluator writer has not completed its full report')
            if report.get('case_id', task['case_id']) != task['case_id']:
                raise ValueError("Evaluation report belongs to another case")
            coverage = report['judgement_coverage_summary']
            status = ('infrastructure_failure' if coverage.get('status') == 'infrastructure_failure' or coverage.get('infrastructure_failure_metrics')
                      else 'complete' if coverage['eligible'] else 'not_score_eligible')
        # The full original report stays intact; an execution provenance field
        # records the reduced guarantee and separately derived identity.
        report['execution_validation'] = {
            'identity': read(work / 'identity.json'),
            'content_fingerprint_validation': content_policy.receipt() if self.env[content_policy.ENV] == 'off' else {'mode': 'strict'},
            'historical_success_relabelled': False}
        destination = work / 'final/evaluation_report.json'
        atomic_write(destination, report)
        result = {'status': status, 'report': str(destination.relative_to(work)), 'report_sha256': sha(destination),
                'real_api_qualified': not self.mock, 'automatic_paid_room_retry': False}
        if not self.mock and status in {'complete', 'not_score_eligible'}:
            self.cmd([sys.executable, '-B', '-I', HERE / 'replay_report.py', '--release', self.release,
                      '--report', destination], work / 'final/scoring_replay.json', stop, work, stage='evaluate')
            proof = read(work / 'final/scoring_replay.json')
            if proof['status'] != 'passed' or proof['report_sha256'] != result['report_sha256']:
                raise ValueError('Full retained report failed independent score replay')
            result['scoring_replay_receipt'] = 'final/scoring_replay.json'
        return result


@contextmanager
def live_environment(runtime, base, output, stop):
    sys.path.insert(0, str(runtime / 'scripts'))
    from nonrect30_api2_sol_retry_transport import AttemptLedger, RetryingOpener, POLICY
    import nonrect30_isolated_relay as isolated
    relay = load('nonrect_fast_relay', runtime / 'scripts/nonrect_merged30_v8_api2_sol_catalog/relay.py')
    gate = load('nonrect_fast_gate', runtime / 'scripts/launch_nonrect30_api2_sol_catalog_after_probe.py')
    credential = base.request_credential()
    session = output / 'api_sessions' / secrets.token_hex(8)
    session.mkdir(parents=True)
    ledger = AttemptLedger(session / 'attempts.jsonl')
    policy = {**POLICY, 'max_retries': 20, 'max_attempts': 21}
    transport = RetryingOpener(gate.opener(), ledger, relay.redaction.redact, credential,
                              shared_timeout=False, max_retries=policy['max_retries'])
    diagnostic = gate.diagnostic(credential, transport)
    diagnostic['retry_policy'] = policy
    atomic_write(session / 'preflight.json', diagnostic)
    if diagnostic['status'] != 'passed':
        raise RuntimeError("Fresh API2 Sol text/image gate failed; no rooms dispatched")
    env = base.clean_environment()
    env.pop('STANDARD_API_CREDENTIAL', None)
    env['MERGED30_PROXY_KEY'] = secrets.token_hex(32)
    transport = RetryingOpener(gate.opener(), ledger, relay.redaction.redact, credential,
                              stop=stop, max_retries=policy['max_retries'])
    def failed(failure):
        ledger({'event': 'isolated_request_failure', 'scope': 'request',
                'http_status': failure.get('http_status'), 'error_type': failure.get('error_type')})
    with isolated.serve(relay, credential, env['MERGED30_PROXY_KEY'], stop, failed, opener=transport) as endpoint:
        env.update(JUDGE_ENDPOINT=endpoint, JUDGE_MODEL=base.JUDGE_MODEL, JUDGE_API_KEY_ENV='MERGED30_PROXY_KEY')
        yield env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--content-fingerprint-validation', choices=('strict', 'off'), default='strict')
    parser.add_argument('--case-id', action='append', default=[])
    parser.add_argument('--preparation-workers', type=int, default=1)
    parser.add_argument('--evaluation-workers', type=int, default=12)
    parser.add_argument('--ready-capacity', type=int, default=2)
    parser.add_argument('--ready-gib', type=float, default=4)
    parser.add_argument('--backpressure-timeout', type=float, default=300)
    parser.add_argument('--cleanup-terminal', action='store_true')
    parser.add_argument('--retry-failed', action='store_true', help='Explicitly retry prior failed/interrupted evaluation; may repeat paid requests')
    parser.add_argument('--evaluation-mode', choices=('api', 'mock'), default='api',
                        help='Identity of the intended consumer; mock receipts can never be adopted by an API run')
    parser.add_argument('--avoid-conflicts-with', type=Path,
                        help='Read-only old output root; cancel only our child if that campaign creates the same source case')
    parser.add_argument('--only-unclaimed', action='store_true',
                        help='Prepare the full remaining backlog, from the far end of the old queue; requires --avoid-conflicts-with')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--prepare-only', action='store_true')
    action.add_argument('--mock-evaluate', action='store_true')
    action.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    evaluation_mode = 'mock' if args.mock_evaluate else args.evaluation_mode
    if args.run and evaluation_mode != 'api':
        raise ValueError('--run requires the API consumer identity')
    output = safe_output(args.output_root)
    runtime = args.runtime.resolve()
    if output.is_relative_to(runtime) or runtime.is_relative_to(output):
        raise ValueError("Output and derived runtime must be disjoint")
    manifest = derive.verify(runtime)
    limits = Limits(preparation_workers=args.preparation_workers, evaluation_workers=args.evaluation_workers,
                    ready_capacity=args.ready_capacity, ready_gib=args.ready_gib, backpressure_timeout=args.backpressure_timeout)
    base = load('nonrect_fast_plan_base', runtime / 'scripts/nonrect_merged30_v8_api2_sol_catalog/run.py')
    tasks, pins = base.inventory()
    tasks = [{**t, 'source_case_id': t['case_id'], 'case_id': t['case_id'].replace('nr.merged30.', 'nr.fastv1.')} for t in tasks]
    if args.only_unclaimed:
        if args.avoid_conflicts_with is None or args.case_id:
            raise ValueError('--only-unclaimed requires a conflict root and no explicit case selection')
        if (output / 'plan.json').exists():
            saved = read(output / 'plan.json')['tasks']
            known = {t['case_id']: t for t in tasks}
            if any(known.get(t['case_id']) != t for t in saved):
                raise ValueError('Saved backlog differs from current source inventory')
            tasks = saved
        else:
            tasks = [t for t in reversed(tasks) if not (args.avoid_conflicts_with / 'rooms' / t['source_case_id']).exists()]
    if args.case_id:
        index = {key: task for task in tasks for key in (task['case_id'], task['source_case_id'])}
        tasks = [index[key] for key in args.case_id if key in index]
        if len(tasks) != len(set(args.case_id)):
            raise ValueError("Unknown or duplicated case selection")
    code_pins = {name: sha(HERE / name) for name in ('run.py', 'pipeline.py', 'derive.py', 'content_policy.py', 'replay_report.py', '__init__.py')}
    identity = {'schema_version': derive.VERSION, 'runtime_tree_sha256': manifest['source_tree_sha256'],
                'runner_source_sha256': canonical_sha(code_pins), 'input_pins': pins,
                'content_fingerprint_validation': args.content_fingerprint_validation,
                'evaluation_mode': evaluation_mode,
                'conflict_guard_root': str(args.avoid_conflicts_with.resolve()) if args.avoid_conflicts_with else None,
                'judge': base.JUDGE_MODEL, 'route': base.UPSTREAM_MODEL,
                'reasoning_effort': 'xhigh', 'parent_evaluator_manifest_sha256': base.RELEASE_SHA}
    plan = {'identity': identity, 'tasks': tasks, 'limits': asdict(limits)}
    print(json.dumps({'status': 'offline_verified', 'tasks': len(tasks), 'output': str(output),
                      'validation': args.content_fingerprint_validation, 'runtime_tree_sha256': manifest['source_tree_sha256']}), flush=True)
    if not (args.prepare_only or args.mock_evaluate or args.run):
        return 0
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'runner.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output / 'plan.json').exists() and read(output / 'plan.json') != plan:
            raise ValueError("Immutable plan/selection/limits changed; use a new output")
        atomic_write(output / 'plan.json', plan)
        stop = threading.Event()
        def cancel(*unused):
            stop.set()
        previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            def execute(env):
                env[content_policy.ENV] = args.content_fingerprint_validation
                # Both stages and every Blender descendant inherit the explicit
                # mode; runtime and prepared records independently validate it.
                stages = Stages(runtime, env, limits, mock=args.mock_evaluate,
                                avoid_conflicts_with=args.avoid_conflicts_with.resolve() if args.avoid_conflicts_with else None)
                pipeline = Pipeline(output, identity, limits, stages.prepare, stages.evaluate,
                                    stop=stop, prepare_only=args.prepare_only, cleanup=args.cleanup_terminal,
                                    retry_failed=args.retry_failed)
                result = pipeline.execute(tasks)
                print(json.dumps({'status': result['status'], 'results': len(result['results']),
                                  'ready': result['ready'], 'waiting': result['waiting'],
                                  'backpressure_reason': result['backpressure_reason']}), flush=True)
                return 0 if result['status'] in {'finished', 'prepared_buffer_ready'} and all(r['status'] in {'complete', 'not_score_eligible', 'mock_evaluated'} for r in result['results']) else 2
            if args.run:
                with live_environment(runtime, base, output, stop) as env:
                    return execute(env)
            env = base.clean_environment()
            # No model credential enters preparation or mock subprocesses.
            for key in list(env):
                if any(token in key.upper() for token in ('CREDENTIAL', 'API_KEY', 'ACCESS_TOKEN')):
                    env.pop(key)
            return execute(env)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
