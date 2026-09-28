"""Migration exclusivity, paid execution guards, and success-only cleanup."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from nonrect_fast_v1 import handoff_execution as h
from nonrect_fast_v1.pipeline import Limits, atomic_write, sha


def options(**kwargs):
    return SimpleNamespace(**dict(workers=3, worker_memory_gib=20,
        desktop_memory_reserve_gib=6, evaluation_growth_gib=6, **kwargs))


def test_partition_rejects_duplicate_overlap_and_missing():
    h.check_partition(['a','b','c'], ['a'], ['b'], ['c'])
    for ready, missing, success in [(['a','a'], ['b'], ['c']), (['a'], ['a'], ['c']), (['a'], [], ['c'])]:
        with pytest.raises(ValueError):
            h.check_partition(['a','b','c'], ready, missing, success)


def test_effective_evaluation_budget_preserves_plan():
    plan = {'limits': asdict(Limits())}
    before = dict(plan['limits'])
    limits = h.effective_limits(plan, options())
    assert limits.evaluation_growth_gib == 6
    assert limits.evaluation_workers == limits.preparation_workers == 3
    assert limits.evaluation_memory_gib == 20 and limits.minimum_free_gib == 30
    assert plan['limits'] == before


def test_twelve_worker_override_preserves_resource_guards():
    plan = {'limits': asdict(Limits())}
    before = dict(plan['limits'])
    args = options()
    args.workers = 12
    limits = h.effective_limits(plan, args)
    assert limits.evaluation_workers == 12
    assert limits.evaluation_memory_gib == 20
    assert limits.reserve_memory_gib == 8
    assert limits.minimum_free_gib == 30
    assert plan['limits'] == before


@pytest.mark.parametrize('field,value', [('workers',13), ('workers',0), ('worker_memory_gib',0),
    ('desktop_memory_reserve_gib',0), ('evaluation_growth_gib',2), ('worker_memory_gib',float('nan'))])
def test_unsupported_overrides_rejected(field,value):
    args=options();setattr(args,field,value)
    with pytest.raises(ValueError):h.effective_limits({'limits':asdict(Limits())},args)


def migration_case(tmp_path, monkeypatch):
    old=tmp_path/'old';work=tmp_path/'new';source='nr.merged30.sol.example'
    monkeypatch.setattr(h,'OLD',old)
    atomic_write(work/'identity.json', {'task':{'source_case_id':source}})
    atomic_write(old/'rooms'/source/'state.json', {'status':'cancelled'})
    record={'old':{'cancelled':{source:{'state_sha256':sha(old/'rooms'/source/'state.json')}}}}
    return old/'rooms'/source,work,record


def test_cancelled_exception_requires_unchanged_state(tmp_path,monkeypatch):
    old,work,record=migration_case(tmp_path,monkeypatch)
    h.check_migration_case(work,record)
    atomic_write(old/'state.json',{'status':'complete'})
    with pytest.raises(h.ExternalConflict):h.check_migration_case(work,record)


def test_cancelled_exception_rejects_new_artifacts(tmp_path,monkeypatch):
    old,work,record=migration_case(tmp_path,monkeypatch)
    (old/'evaluation').mkdir()
    with pytest.raises(h.ExternalConflict):h.check_migration_case(work,record)


def test_unclaimed_guard_remains_enabled(tmp_path,monkeypatch):
    old,work,record=migration_case(tmp_path,monkeypatch)
    record['old']['cancelled']={}
    with pytest.raises(h.ExternalConflict):h.check_migration_case(work,record)


def test_old_runner_lock_excludes_second_owner(tmp_path):
    p=tmp_path/'runner.lock';p.touch()
    with h.lock_file(p,existing=True):
        with pytest.raises(BlockingIOError):
            with h.lock_file(p,existing=True):pass


def test_live_evaluation_receives_rss_watchdog_and_six_gib_budget(tmp_path,monkeypatch):
    stages=object.__new__(h.MigratedStages)
    stages.env={};stages.limits=h.effective_limits({'limits':asdict(Limits())},options())
    stages.reserve_gib=6;stages.rss_gib=20
    calls=[]
    stages.conflict_check=lambda work:calls.append('conflict')
    class Guard:
        def __init__(self,work,reserve,rss):assert reserve==6 and rss==20
        def __call__(self):calls.append('memory');raise MemoryError('pressure')
    monkeypatch.setattr(h,'RunningMemoryGuard',Guard)
    def execute(*args,**kwargs):
        assert kwargs['growth_gib']==6
        kwargs['conflict_check']()
        pytest.fail('Memory guard failed to stop execution')
    monkeypatch.setattr(h.command_executor,'command',execute)
    with pytest.raises(MemoryError):
        stages.cmd(['unused'],tmp_path/'log',threading.Event(),tmp_path,stage='evaluate')
    assert calls==['conflict','memory']


def test_failed_evaluation_is_not_cleaned(tmp_path,monkeypatch):
    pipeline=object.__new__(h.SuccessCleanupPipeline);pipeline.output=tmp_path
    monkeypatch.setattr(h.Pipeline,'save',lambda *args,**kwargs:None)
    monkeypatch.setattr(h,'cleanup_terminal',lambda *args,**kwargs:pytest.fail('Failure inputs must be kept'))
    pipeline.save([{'status':'failed_eval','case_id':'case'}])


def test_no_memory_gate_admits_twelve_with_low_memory_and_high_swap():
    limits = Limits(evaluation_workers=12)
    observation = {'available_memory_gib': 0, 'swapout_mib_per_second': 10000,
                   'free_disk_gib': 1000, 'load_per_cpu': 0}
    governor = h.MemoryUngatedGovernor(limits, lambda: dict(observation))
    assert all(governor.acquire('evaluate') for _ in range(12))
    assert governor.last == observation  # No fabricated available-memory value.


@pytest.mark.parametrize('field,value,reason', [('free_disk_gib', 0, 'disk'), ('load_per_cpu', 10, 'cpu')])
def test_no_memory_gate_preserves_other_admission_checks(field, value, reason):
    observation = {'available_memory_gib': 0, 'swapout_mib_per_second': 10000,
                   'free_disk_gib': 1000, 'load_per_cpu': 0}
    observation[field] = value
    governor = h.MemoryUngatedGovernor(Limits(), lambda: observation)
    assert governor.acquire('evaluate') is None
    assert governor.reason == reason


def test_no_memory_gate_skips_watchdog_but_checks_ownership(tmp_path, monkeypatch):
    stages = object.__new__(h.MigratedStages)
    stages.env = {}; stages.limits = Limits(); stages.memory_gates = False
    calls = []
    stages.conflict_check = lambda work: calls.append('conflict')
    monkeypatch.setattr(h, 'RunningMemoryGuard', lambda *a: pytest.fail('Memory gate still active'))
    monkeypatch.setattr(h.command_executor, 'command', lambda *a, **kw: kw['conflict_check']())
    stages.cmd(['unused'], tmp_path/'log', threading.Event(), tmp_path, stage='evaluate')
    assert calls == ['conflict']


def test_resource_opt_out_admits_twelve_without_fabricating_observations():
    observation = {'available_memory_gib': 0, 'free_disk_gib': 0,
                   'cpu_idle_percent': 0, 'swapout_mib_per_second': 10000}
    governor = h.ResourceUngatedGovernor(Limits(), lambda: dict(observation))
    assert len({governor.acquire('evaluate') for _ in range(12)}) == 12
    assert governor.last == observation and governor.reason is None
    for token in list(governor.tokens):
        governor.release(token)
    assert not governor.tokens


def test_resource_opt_out_keeps_ownership_and_disables_runtime_disk_check(tmp_path, monkeypatch):
    stages = object.__new__(h.MigratedStages)
    stages.env = {}; stages.limits = Limits()
    stages.memory_gates = stages.disk_guard = False
    calls = []
    stages.conflict_check = lambda work: calls.append('conflict')
    monkeypatch.setattr(h, 'RunningMemoryGuard', lambda *a: pytest.fail('Memory gate still active'))
    def execute(*args, **kwargs):
        assert kwargs['disk_guard'] is False
        kwargs['conflict_check']()
    monkeypatch.setattr(h.command_executor, 'command', execute)
    stages.cmd(['unused'], tmp_path/'log', threading.Event(), tmp_path, stage='evaluate')
    assert calls == ['conflict']


def test_executor_disk_opt_out_keeps_real_subprocess_exit_and_stop(tmp_path, monkeypatch):
    executor = h.command_executor
    monkeypatch.setattr(executor, 'disk_bytes', lambda *a: pytest.fail('Disk growth scan still active'))
    monkeypatch.setattr(executor.shutil, 'disk_usage', lambda *a: pytest.fail('Disk floor still active'))
    executor.command([sys.executable, '-c', 'import time; time.sleep(0.6)'], tmp_path/'child.log',
                     None, threading.Event(), work=tmp_path, disk_guard=False)
    stop = threading.Event(); stop.set()
    with pytest.raises(InterruptedError):
        executor.command(['must-not-start'], tmp_path/'stopped.log', None, stop, disk_guard=False)


def test_twelve_prepared_cases_enter_evaluation_concurrently(tmp_path):
    from nonrect_fast_v1.pipeline import publish_ready
    from dataclasses import replace
    tasks = [{'case_id': 'prepared-' + str(i)} for i in range(12)]
    identity = {'test': 'resource-opt-out'}
    for task in tasks:
        lease = h.CaseLease(tmp_path/'rooms'/task['case_id'], {'campaign': identity, 'task': task})
        try:
            (lease.root/'dataset').mkdir()
            atomic_write(lease.root/'input_receipt.json', {'case_id': task['case_id']})
            publish_ready(lease)
            atomic_write(lease.root/'state.json', {'status': 'ready'})
        finally:
            lease.close()
    barrier = threading.Barrier(12)
    def evaluate(task, work, stop):
        barrier.wait(timeout=10)
        atomic_write(work/'final/report.json', {'mock': True})
        return {'status': 'complete', 'report': 'final/report.json',
                'report_sha256': sha(work/'final/report.json')}
    settings = replace(Limits(), evaluation_workers=12, poll_seconds=.01)
    pipeline = h.Pipeline(tmp_path, identity, settings,
        lambda *a: pytest.fail('Unexpected re-preparation'), evaluate)
    pipeline.governor = h.ResourceUngatedGovernor(settings, lambda: {'free_disk_gib': 0})
    result = pipeline.execute(tasks)
    assert result['peaks']['evaluate'] == 12
    assert len(result['results']) == 12
    assert all(row['status'] == 'complete' for row in result['results'])


def test_failure_checkpoint_does_not_claim_launched_or_store_secret(tmp_path,monkeypatch):
    monkeypatch.setattr(h,'CONTROL',tmp_path)
    with pytest.raises(ValueError):
        with h.record_failure():raise ValueError('secret-example-must-not-be-recorded')
    result=h.read(tmp_path/'status.json')
    assert result['status']=='blocked_or_failed'
    assert 'secret-example' not in (tmp_path/'status.json').read_text()
