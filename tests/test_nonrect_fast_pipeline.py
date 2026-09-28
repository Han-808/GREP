"""Behavioral contracts for the isolated fast runtime and bounded pipeline."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nonrect_fast_v1 import content_policy, derive
from nonrect_fast_v1.pipeline import (CaseBusy, CaseLease, Governor, IdentityMismatch,
    Limits, Pipeline, atomic_write, canonical_sha, cleanup_terminal, publish_ready,
    read, safe_output, sha, validate_ready)


def healthy():
    return {'available_memory_gib': 48, 'free_disk_gib': 200,
            'load_per_cpu': .2, 'swapout_mib_per_second': 0}


def limits(**kwargs):
    return replace(Limits(), startup_seconds=.01, poll_seconds=.005,
                   backpressure_timeout=.025, **kwargs)


def prepare(task, work, stop):
    (work / 'dataset').mkdir(exist_ok=True)
    (work / 'dataset/mesh.ply').write_text('actual geometry')
    atomic_write(work / 'input_receipt.json', {'case_id': task['case_id']})


def evaluate(task, work, stop):
    assert (work / 'ready.json').is_file()
    assert (work / 'consuming.json').is_file()
    report = work / 'final/report.json'
    atomic_write(report, {'all_metric_facts': [1, 2, 3]})
    atomic_write(work / 'final/replay.json', {'status': 'passed', 'report_sha256': sha(report)})
    return {'status': 'complete', 'report': 'final/report.json', 'report_sha256': sha(report),
            'scoring_replay_receipt': 'final/replay.json'}


def tasks(n):
    return [{'case_id': 'case-' + str(i), 'index': i} for i in range(n)]


def test_strict_default_and_no_fake_content_hashes(monkeypatch):
    monkeypatch.delenv(content_policy.ENV, raising=False)
    assert content_policy.mode() == 'strict'
    record = {'content_fingerprint_validation': content_policy.receipt()}
    with pytest.raises(ValueError):
        content_policy.validate_record(record)
    monkeypatch.setenv(content_policy.ENV, 'off')
    content_policy.validate_record(record)
    assert record['content_fingerprint_validation']['status'] == 'skipped_by_policy'
    for field in content_policy.HASH_FIELDS:
        with pytest.raises(ValueError):
            content_policy.validate_record({**record, field: '0' * 64})
    with pytest.raises(ValueError):
        content_policy.validate_record({})
    assert not content_policy.inspection_accepted({'status': 'passed'})
    assert content_policy.inspection_accepted({'status': 'passed_structural_checks', **record})
    monkeypatch.setenv(content_policy.ENV, 'typo')
    with pytest.raises(ValueError):
        content_policy.mode()


@pytest.mark.parametrize('stage', ['prepare', 'evaluate'])
def test_two_stages_overlap_failure_isolated_and_refilled(tmp_path, stage):
    evaluation_started = threading.Event()
    later_prepared = threading.Event()

    def prep(task, work, stop):
        if task['index'] == 1:
            assert evaluation_started.wait(2)
            later_prepared.set()
            if stage == 'prepare':
                raise RuntimeError('one bad room')
        prepare(task, work, stop)

    def ev(task, work, stop):
        evaluation_started.set()
        assert later_prepared.wait(2), 'preparation was blocked behind evaluation'
        if task['index'] == 1 and stage == 'evaluate':
            raise RuntimeError('one bad room')
        return evaluate(task, work, stop)

    result = Pipeline(tmp_path, {'version': 'test'}, limits(evaluation_workers=2), prep, ev,
                      probe=healthy).execute(tasks(5))
    assert len(result['results']) == 5
    assert sum(r['status'] == 'complete' for r in result['results']) == 4
    assert sum(r['status'] == 'failed_' + ('prepare' if stage == 'prepare' else 'eval') for r in result['results']) == 1
    assert result['peaks']['prepare'] == 1 and result['peaks']['evaluate'] <= 2


def test_buffer_is_bounded_and_resumes_without_reprepare(tmp_path):
    settings = limits(ready_capacity=2)
    result = Pipeline(tmp_path, {'v': 1}, settings, prepare, evaluate,
                      prepare_only=True, probe=healthy).execute(tasks(5))
    assert len(result['ready']) == 2 and len(result['waiting']) == 3
    assert not result['results']
    prepared = []
    def prep(task, work, stop):
        prepared.append(task['index'])
        prepare(task, work, stop)
    result = Pipeline(tmp_path, {'v': 1}, settings, prep, evaluate, probe=healthy).execute(tasks(5))
    assert prepared == [2, 3, 4]
    assert len(result['results']) == 5
    assert not list(tmp_path.rglob('ready.json'))
    assert not list(tmp_path.rglob('consuming.json'))


@pytest.mark.parametrize('field,value,reason', [
    ('available_memory_gib', 7, 'memory'), ('free_disk_gib', 31, 'disk'),
    ('load_per_cpu', 4, 'cpu'), ('swapout_mib_per_second', 100, 'swap'),
    ('swapout_mib_per_second', None, 'swap')])
def test_shared_host_backpressure_starts_no_work(tmp_path, field, value, reason):
    def no_work(*args):
        pytest.fail('admitted under resource pressure')
    result = Pipeline(tmp_path, {}, limits(), no_work, no_work,
                      probe=lambda: {**healthy(), field: value}).execute(tasks(2))
    assert result['backpressure_reason'] == reason and len(result['waiting']) == 2


def test_ram_reserves_only_startup_and_next_worker():
    now = [0]
    governor = Governor(limits(), lambda: {**healthy(), 'available_memory_gib': 13}, clock=lambda: now[0])
    a = governor.acquire('prepare')
    assert a
    assert governor.acquire('prepare') is None
    now[0] = 20
    # The observed 13 GiB already accounts for the established worker. Requiring
    # 8 + (active+1)*4 would incorrectly deny this admission.
    assert governor.acquire('prepare')


def test_actual_cpu_usage_controls_admission_instead_of_old_load_average():
    governor = Governor(limits(), lambda: {**healthy(), 'load_per_cpu': 5, 'cpu_idle_percent': 60})
    assert governor.acquire('prepare')
    assert governor.acquire('prepare')
    assert governor.acquire('prepare')
    assert governor.acquire('prepare') is None  # startup CPU reservations
    governor = Governor(limits(), lambda: {**healthy(), 'cpu_idle_percent': 5})
    assert governor.acquire('prepare') is None and governor.reason == 'cpu'


def test_full_prepare_keeps_waiting_through_transient_host_pressure(tmp_path):
    calls = []
    def probe():
        calls.append(1)
        return {**healthy(), 'available_memory_gib': 1 if len(calls) in {2, 3} else 48}
    result = Pipeline(tmp_path, {}, limits(ready_capacity=8, ready_gib=16), prepare, evaluate,
                      prepare_only=True, probe=probe).execute(tasks(5))
    assert len(result['ready']) == 5 and not result['waiting']


def test_disk_reservation_is_shared_between_both_pools():
    governor = Governor(limits(), lambda: {**healthy(), 'free_disk_gib': 33})
    token = governor.acquire('prepare')
    assert token and governor.acquire('evaluate') is None
    governor.release(token)
    assert governor.acquire('evaluate')


def test_case_exclusive_lock_identity_and_ready_mutation(tmp_path):
    identity = {'campaign': {'v': 1}, 'task': tasks(1)[0]}
    lease = CaseLease(tmp_path / 'case', identity)
    with pytest.raises(CaseBusy):
        CaseLease(lease.root, identity)
    with pytest.raises(ValueError):
        publish_ready(lease)
    prepare(tasks(1)[0], lease.root, threading.Event())
    marker = publish_ready(lease)
    assert marker == validate_ready(lease)
    (lease.root / 'dataset/mesh.ply').write_text('drift')
    with pytest.raises(IdentityMismatch):
        validate_ready(lease)
    lease.close()
    with pytest.raises(IdentityMismatch):
        CaseLease(tmp_path / 'case', {'campaign': {'v': 2}, 'task': tasks(1)[0]})


def test_lock_conflict_does_not_modify_owner(tmp_path):
    task = tasks(1)[0]
    lease = CaseLease(tmp_path / 'rooms' / task['case_id'], {'campaign': {}, 'task': task})
    prepare(task, lease.root, threading.Event())
    publish_ready(lease)
    before = (lease.root / 'ready.json').read_bytes()
    result = Pipeline(tmp_path, {}, limits(), prepare, evaluate, probe=healthy).execute([task])
    assert result['results'][0]['status'] == 'lock_conflict'
    assert (lease.root / 'ready.json').read_bytes() == before
    lease.close()


def test_cancel_preparation_leaves_recoverable_atomic_ready(tmp_path):
    stop = threading.Event()
    def prep(task, work, stopping):
        prepare(task, work, stopping)
        stopping.set()
    result = Pipeline(tmp_path, {}, limits(), prep, evaluate, stop=stop, probe=healthy).execute(tasks(2))
    assert result['status'] == 'interrupted'
    assert len(result['ready']) == 1
    result = Pipeline(tmp_path, {}, limits(), prepare, evaluate, probe=healthy).execute(tasks(2))
    assert len(result['results']) == 2


def test_interrupted_paid_consumer_never_automatically_retries(tmp_path):
    task = tasks(1)[0]
    lease = CaseLease(tmp_path / 'rooms' / task['case_id'], {'campaign': {}, 'task': task})
    prepare(task, lease.root, threading.Event())
    publish_ready(lease)
    atomic_write(lease.root / 'consuming.json', {'paid_attempt_possible': True})
    lease.close()
    def forbidden(*args):
        pytest.fail('implicit paid retry')
    result = Pipeline(tmp_path, {}, limits(), forbidden, forbidden, probe=healthy).execute([task])
    assert result['results'][0]['status'] == 'interrupted_evaluation_needs_review'
    assert (tmp_path / 'rooms/case-0/ready.json').exists()


def test_cleanup_protects_ready_and_consuming_retains_complete_report(tmp_path):
    task = tasks(1)[0]
    lease = CaseLease(tmp_path / 'rooms' / task['case_id'], {'campaign': {}, 'task': task})
    prepare(task, lease.root, threading.Event())
    publish_ready(lease)
    atomic_write(lease.root / 'state.json', {'status': 'failed_eval'})
    assert cleanup_terminal(lease, enabled=True) == []
    (lease.root / 'ready.json').unlink()
    atomic_write(lease.root / 'consuming.json', {})
    assert cleanup_terminal(lease, enabled=True) == []
    lease.close()
    # Use a different case to verify ordinary successful cleanup.
    result = Pipeline(tmp_path, {}, limits(), prepare, evaluate, cleanup=True, probe=healthy).execute(tasks(2)[1:])
    state = result['results'][0]
    work = tmp_path / 'rooms/case-1'
    assert not (work / 'dataset').exists()
    assert sha(work / state['report']) == state['report_sha256']
    assert read(work / state['report']) == {'all_metric_facts': [1, 2, 3]}


def test_bad_identity_or_historical_directory_cannot_be_adopted(tmp_path):
    root = tmp_path / 'rooms/case-0'
    root.mkdir(parents=True)
    (root / 'old_report.json').write_text('{}')
    result = Pipeline(tmp_path, {}, limits(), prepare, evaluate, probe=healthy).execute(tasks(1))
    assert result['results'][0]['status'] == 'identity_rejected'
    assert (root / 'old_report.json').exists()
    with pytest.raises(ValueError):
        safe_output('/Users/han_mohan/Desktop/Layout_DDD/Support/outputs/new_name')


def test_success_resume_verifies_hash_without_preparation(tmp_path):
    Pipeline(tmp_path, {}, limits(), prepare, evaluate, cleanup=True, probe=healthy).execute(tasks(1))
    result = Pipeline(tmp_path, {}, limits(), lambda *a: pytest.fail('reprepared'),
                      lambda *a: pytest.fail('reevaluated'), probe=healthy).execute(tasks(1))
    assert result['results'][0]['status'] == 'complete'
    (tmp_path / 'rooms/case-0/final/report.json').write_text('corrupted')
    result = Pipeline(tmp_path, {}, limits(), prepare, evaluate, probe=healthy).execute(tasks(1))
    assert result['results'][0]['status'] == 'identity_rejected'


def test_scientific_and_transport_files_remain_byte_identical():
    # The derivation allowlist excludes every scoring, weight, camera, fallback,
    # coverage and transport file. This test also checks original pinned bytes.
    runtime = ROOT / 'Support/nonrect_fast_v1/runtime'
    if not runtime.is_dir():
        pytest.skip('Build pinned local runtime first')
    manifest = derive.verify(runtime)
    assert set(manifest['edits']) == set(derive.TRANSFORMS) | {
        'scripts/nonrect_merged30_v8_api2_sol_catalog/' + name
        for name in ['build_case.py', 'materialize_room.py', 'verify_materialized.py', 'nonrect_evidence_worker.py']}
    for rel, original in manifest['original_files'].items():
        if rel not in manifest['edits']:
            assert sha(runtime / rel) == original, rel
    assert manifest['parent_evaluator_manifest_sha256'] == 'caf95546afeaa220d77adb0c380abef462084bf4f07c004b1c2d32f51024a7ff'


def test_twelve_evaluation_slots_refill_while_peers_wait(tmp_path):
    first_wave = threading.Barrier(12)
    replacement = threading.Event()
    def ev(task, work, stop):
        if task['index'] < 12:
            first_wave.wait(5)
        if task['index'] == 0:
            raise RuntimeError('room-local evaluator failure')
        if task['index'] == 12:
            replacement.set()
        assert replacement.wait(5)
        return evaluate(task, work, stop)
    result = Pipeline(tmp_path, {}, limits(evaluation_workers=12), prepare, ev, probe=healthy).execute(tasks(14))
    assert result['peaks']['evaluate'] == 12 and result['peaks']['prepare'] == 1
    assert len(result['results']) == 14
    assert sum(r['status'] == 'complete' for r in result['results']) == 13


def test_recovered_ready_can_drain_with_only_evaluation_memory(tmp_path):
    Pipeline(tmp_path, {}, limits(), prepare, evaluate, prepare_only=True, probe=healthy).execute(tasks(1))
    def no_preparation(*args):
        pytest.fail('should consume recovered ready without a preparation reservation')
    result = Pipeline(tmp_path, {}, limits(), no_preparation, evaluate,
                      probe=lambda: {**healthy(), 'available_memory_gib': 11}).execute(tasks(1))
    assert result['results'][0]['status'] == 'complete'


def test_crash_after_terminal_state_retires_markers_on_resume(tmp_path):
    Pipeline(tmp_path, {}, limits(), prepare, evaluate, probe=healthy).execute(tasks(1))
    work = tmp_path / 'rooms/case-0'
    atomic_write(work / 'ready.json', {'stale_after_durable_completion': True})
    atomic_write(work / 'consuming.json', {})
    Pipeline(tmp_path, {}, limits(), lambda *a: pytest.fail('reprepare'),
             lambda *a: pytest.fail('reevaluate'), cleanup=True, probe=healthy).execute(tasks(1))
    assert not (work / 'ready.json').exists() and not (work / 'consuming.json').exists()
    assert not (work / 'dataset').exists() and (work / 'final/report.json').exists()


def test_success_cleanup_requires_independent_score_replay(tmp_path):
    lease = CaseLease(tmp_path / 'room', {'campaign': {}, 'task': tasks(1)[0]})
    prepare(tasks(1)[0], lease.root, threading.Event())
    report = lease.root / 'report.json'
    atomic_write(report, {})
    atomic_write(lease.root / 'state.json', {'status': 'complete', 'report': 'report.json', 'report_sha256': sha(report),
                                         'scoring_replay_receipt': 'proof.json'})
    atomic_write(lease.root / 'proof.json', {'status': 'failed', 'report_sha256': sha(report)})
    with pytest.raises(IdentityMismatch):
        cleanup_terminal(lease, enabled=True)
    assert (lease.root / 'dataset/mesh.ply').exists()
    lease.close()


def test_symlink_case_parent_cannot_write_into_main_or_sibling(tmp_path):
    sibling = tmp_path / 'sibling'
    sibling.mkdir()
    (tmp_path / 'rooms').symlink_to(sibling, target_is_directory=True)
    result = Pipeline(tmp_path, {}, limits(), prepare, evaluate, probe=healthy).execute(tasks(1))
    assert result['results'][0]['status'] == 'identity_rejected'
    assert not list(sibling.iterdir())


def test_cancel_during_evaluation_preserves_inputs_and_marks_paid_review(tmp_path):
    stop = threading.Event()
    def ev(task, work, stopping):
        stopping.set()
        raise InterruptedError('owned child stopped')
    result = Pipeline(tmp_path, {}, limits(), prepare, ev, stop=stop, cleanup=True, probe=healthy).execute(tasks(1))
    assert result['results'][0]['status'] == 'interrupted_evaluation_needs_review'
    work = tmp_path / 'rooms/case-0'
    assert (work / 'dataset/mesh.ply').exists()
    assert (work / 'ready.json').exists() and (work / 'consuming.json').exists()


def test_external_campaign_conflict_refuses_our_preparation(tmp_path):
    from nonrect_fast_v1.run import Stages
    from nonrect_fast_v1.pipeline import ExternalConflict
    work = tmp_path / 'work'
    work.mkdir()
    atomic_write(work / 'identity.json', {'task': {'source_case_id': 'original'}})
    stages = object.__new__(Stages)
    stages.avoid_conflicts_with = tmp_path / 'old_campaign'
    stages.conflict_check(work)
    (stages.avoid_conflicts_with / 'rooms/original').mkdir(parents=True)
    with pytest.raises(ExternalConflict):
        stages.conflict_check(work)


def test_conflict_during_owned_child_cancels_only_that_process(tmp_path):
    from nonrect_fast_v1.run import command
    from nonrect_fast_v1.pipeline import ExternalConflict
    import os
    calls = []
    def check():
        calls.append(1)
        if len(calls) > 1:
            raise ExternalConflict('old queue claimed this case')
    start = time.monotonic()
    with pytest.raises(ExternalConflict):
        command([sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path / 'child.log',
                dict(os.environ), threading.Event(), timeout=10, conflict_check=check)
    assert time.monotonic() - start < 5 and len(calls) == 2


def test_explicit_capacity_override_changes_only_concurrency():
    from nonrect_fast_v1.resume_capacity import capacity_override
    from dataclasses import asdict
    prior = Limits(preparation_workers=3, ready_capacity=126, ready_gib=80)
    updated = capacity_override(prior, 6, 18)
    assert {key for key in asdict(prior) if asdict(prior)[key] != asdict(updated)[key]} == {'preparation_workers'}
    assert updated.preparation_workers == 6 and prior.preparation_workers == 3
    with pytest.raises(ValueError):
        capacity_override(prior, 7, 18)
    with pytest.raises(ValueError):
        capacity_override(prior, 6, 12)


def test_desktop_guard_rejects_high_estimate_with_low_physical_headroom():
    from nonrect_fast_v1.resume_capacity import desktop_headroom
    assert desktop_headroom(healthy(), 7, 1, 4, 4)['desktop_memory_veto'] == 'physical_headroom'
    assert desktop_headroom(healthy(), 20, 4, 4, 4)['desktop_memory_veto'] == 'kernel_pressure'
    safe = desktop_headroom(healthy(), 12, 1, 4, 4)
    assert safe['available_memory_gib'] == healthy()['available_memory_gib']
    assert 'desktop_memory_veto' not in safe


def test_oversize_recovery_preserves_global_disk_and_queue_bounds():
    from nonrect_fast_v1.adopt_verified_preparation import storage_accepts
    from nonrect_fast_v1.pipeline import GIB
    storage_accepts(60, 2.4 * GIB, 40 * GIB, 80)
    for free, size, used in [(29, 2.4, 40), (60, 4.1, 40), (60, 2.4, 79)]:
        with pytest.raises(ValueError):
            storage_accepts(free, size * GIB, used * GIB, 80)


@pytest.mark.parametrize('physical,pressure,rss', [(5, 1, 3), (20, 4, 3), (20, 1, 9)])
def test_running_desktop_guard_stops_only_when_memory_bound_crossed(physical, pressure, rss):
    from nonrect_fast_v1.resume_capacity import validate_running_memory
    with pytest.raises(MemoryError):
        validate_running_memory({'physical_free_gib': physical, 'kernel_memory_pressure_level': pressure}, rss, 8, 6)
    validate_running_memory({'physical_free_gib': 20, 'kernel_memory_pressure_level': 1}, 3, 8, 6)


def test_reclaimable_memory_uses_disjoint_kernel_counters_without_anon_credit():
    from nonrect_fast_v1.resume_capacity import desktop_headroom, validate_running_memory
    observation = {**healthy(), 'available_memory_gib': 40, 'inactive_gib': 25, 'speculative_gib': 4}
    result = desktop_headroom(observation, 1, 1, 6, 8, file_backed_gib=20)
    assert result['physical_free_gib'] == 1
    assert result['reclaimable_headroom_gib'] == 21
    assert result['available_memory_gib'] == 21
    assert 'desktop_memory_veto' not in result
    validate_running_memory(result, 4, 8, 6)
    # Pressure percentages must not grant credit to anonymous/compressed pages.
    blocked = desktop_headroom(observation, 1, 1, 6, 8, file_backed_gib=8)
    assert blocked['available_memory_gib'] == 0
    with pytest.raises(MemoryError):
        validate_running_memory({**result, 'kernel_memory_pressure_level': 2}, 4, 8, 6)


def test_shutdown_does_not_signal_an_already_exited_group():
    from nonrect_fast_v1.command_executor import terminate_owned_group
    import signal
    process = type('Process', (), {'pid': 123, 'wait': lambda *a, **kw: -15})()
    responses = iter([[123, 124], [], [], []])
    sent = []
    result = terminate_owned_group(process, members=lambda _: next(responses), send=lambda group, sig: sent.append(sig))
    assert sent == [signal.SIGTERM]
    assert result['confirmed_exited']


def test_shutdown_permission_error_remains_diagnostic_not_original_cause():
    from nonrect_fast_v1.command_executor import terminate_owned_group
    process = type('Process', (), {'pid': 123, 'wait': lambda *a, **kw: -15})()
    responses = iter([[123], [], [], []])
    def denied(*args):
        raise PermissionError(1, 'already gone')
    result = terminate_owned_group(process, members=lambda _: next(responses), send=denied)
    assert result['confirmed_exited'] and result['errors'][0]['type'] == 'PermissionError'


def test_exited_zombie_group_is_not_a_live_resource_owner(monkeypatch):
    from nonrect_fast_v1 import command_executor
    result = type('Result', (), {'stdout': ' 11 10 Z\n 12 10 S\n 13 99 R\n'})()
    monkeypatch.setattr(command_executor.subprocess, 'run', lambda *a, **kw: result)
    assert command_executor.group_members(10) == [12]


def test_user_memory_exception_is_restricted_to_the_sole_last_room():
    from nonrect_fast_v1.retry_last_room import CASE, scope
    plan = {'tasks': [{'case_id': CASE}, {'case_id': 'other'}]}
    assert scope(plan, ['other'])['case_id'] == CASE
    with pytest.raises(ValueError):
        scope(plan, [])
    with pytest.raises(ValueError):
        scope({'tasks': [{'case_id': 'other'}]}, [])
