"""Explicit, audited preparation-capacity override for an existing plan.

Task/runtime/case identities and the original plan are immutable. This command
records a separate execution override and applies only a preparation concurrency
ceiling. Resource gates and all scientific/evaluation settings remain unchanged.
Only preparation is supported; this command can never send model requests.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nonrect_fast_v1 import run
from nonrect_fast_v1 import command_executor
from nonrect_fast_v1.pipeline import GIB, HostProbe, Pipeline, atomic_write, read, safe_output, sha


def desktop_headroom(observation, physical_free_gib, pressure_level, reserve_gib, worker_gib,
                     *, file_backed_gib=None):
    # XNU documents fully reclaimable memory as file-backed + free + purgeable
    # (+ secluded). Use only file-backed + vm_stat's non-speculative free pages.
    # vm_stat subtracts speculative pages from its displayed free count; those
    # pages are already in file-backed. Never add inactive or speculative again.
    # https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md
    headroom = physical_free_gib + (file_backed_gib or 0)
    result = {**observation, 'physical_free_gib': physical_free_gib,
              'kernel_memory_pressure_level': pressure_level,
              'desktop_physical_reserve_gib': reserve_gib}
    if file_backed_gib is not None:
        result.update(file_backed_gib=file_backed_gib, reclaimable_headroom_gib=headroom,
                      memory_accounting='darwin_non_speculative_free_plus_file_backed_v1',
                      usable_memory_estimate_gib=observation['available_memory_gib'])
        result['available_memory_gib'] = min(observation['available_memory_gib'], headroom)
    if pressure_level != 1 or headroom < reserve_gib + worker_gib:
        result['usable_memory_estimate_gib'] = observation['available_memory_gib']
        result['available_memory_gib'] = 0  # explicit resource veto, not a memory measurement
        result['desktop_memory_veto'] = 'kernel_pressure' if pressure_level != 1 else 'physical_headroom'
    return result


class DesktopHostProbe(HostProbe):
    def __init__(self, root, reserve_gib, worker_gib):
        super().__init__(root)
        self.reserve_gib, self.worker_gib = reserve_gib, worker_gib

    def __call__(self):
        observation = super().__call__()
        vm = subprocess.run(['vm_stat'], capture_output=True, text=True, check=True, timeout=15).stdout
        page = re.search(r'page size of (\d+) bytes', vm)
        free = re.search(r'Pages free:\s*(\d+)', vm)
        file_backed = re.search(r'File-backed pages:\s*(\d+)', vm)
        if not page or not free or not file_backed:
            raise RuntimeError('Physical memory observation unavailable')
        level = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'],
                               capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        return desktop_headroom(observation, int(page.group(1)) * int(free.group(1)) / GIB,
                                int(level), self.reserve_gib, self.worker_gib,
                                file_backed_gib=int(page.group(1)) * int(file_backed.group(1)) / GIB)


def validate_running_memory(observation, owned_rss_gib, max_rss_gib, reserve_gib):
    if observation['kernel_memory_pressure_level'] != 1:
        raise MemoryError('Desktop guard stopped our worker: kernel memory pressure')
    if observation.get('reclaimable_headroom_gib', observation['physical_free_gib']) < reserve_gib:
        raise MemoryError('Desktop guard stopped our worker: physical desktop reserve')
    if owned_rss_gib > max_rss_gib:
        raise MemoryError('Desktop guard stopped our worker: per-case resident memory limit')


class RunningMemoryGuard:
    def __init__(self, work, reserve_gib, max_rss_gib):
        self.work = Path(work).resolve()
        self.probe = DesktopHostProbe(work, reserve_gib, max_rss_gib)
        self.reserve_gib, self.max_rss_gib = reserve_gib, max_rss_gib
        self.last_check = 0.0
        self.high_swap_samples = 0

    def __call__(self):
        if time.monotonic() - self.last_check < 2:
            return
        self.last_check = time.monotonic()
        observation = self.probe()
        rows = subprocess.run(['/bin/ps', '-axo', 'rss=,command='], capture_output=True,
                              text=True, check=True, timeout=15).stdout.splitlines()
        rss_kib = 0
        for row in rows:
            fields = row.strip().split(None, 1)
            if len(fields) == 2 and str(self.work) in fields[1] and not fields[1].startswith('/bin/ps '):
                rss_kib += int(fields[0])
        rss_gib = rss_kib / 1024**2
        # Persist only aggregate resource facts; never store process command lines.
        atomic_write(self.work / 'memory_guard.json', {'host': observation, 'owned_rss_gib': rss_gib,
                     'max_owned_rss_gib': self.max_rss_gib, 'physical_reserve_gib': self.reserve_gib})
        validate_running_memory(observation, rss_gib, self.max_rss_gib, self.reserve_gib)
        swap = observation.get('swapout_mib_per_second')
        self.high_swap_samples = self.high_swap_samples + 1 if swap is not None and swap > 64 else 0
        if self.high_swap_samples >= 2:
            raise MemoryError('Desktop guard stopped our worker: sustained swap writes')


def capacity_override(limits, workers, cpu_count):
    # The current user's safe-concurrency authorization is bounded to six
    # preparation workers on an 18-core host; it does not unlock 12+12 dispatch.
    if not 1 <= workers <= min(6, max(1, cpu_count // 3)):
        raise ValueError('Preparation ceiling must be <= 6 and <= CPU count / 3')
    return replace(limits, preparation_workers=workers)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=run.DEFAULT_RUNTIME)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--preparation-workers', required=True, type=int)
    parser.add_argument('--desktop-memory-reserve-gib', type=float, default=6.0,
                        help='Physical free RAM retained in addition to the next preparation worker estimate')
    parser.add_argument('--worker-memory-gib', type=float, default=8.0,
                        help='Conservative incremental reservation and running per-case RSS cap')
    parser.add_argument('--preparation-growth-gib', type=float,
                        help='Audited per-room disk growth/size reservation; defaults to the saved plan')
    args = parser.parse_args(argv)
    output = safe_output(args.output_root)
    plan = read(output / 'plan.json')
    if plan['identity']['evaluation_mode'] != 'api':
        raise ValueError('This preparation continuation requires the API-target ready identity')
    requested = capacity_override(run.Limits(**plan['limits']), args.preparation_workers, os.cpu_count() or 1)
    requested = replace(requested, preparation_memory_gib=args.worker_memory_gib)
    if args.preparation_growth_gib is not None:
        if not 2 <= args.preparation_growth_gib <= 6:
            raise ValueError('Reviewed per-room disk reservation must be 2..6 GiB')
        requested = replace(requested, preparation_growth_gib=args.preparation_growth_gib)
    if args.desktop_memory_reserve_gib < 4:
        raise ValueError('Keep at least 4 GiB physical free RAM for desktop apps')
    original_class = run.Pipeline
    original_command = run.command
    original_stages = run.Stages

    class RecoveryStages(original_stages):
        def __init__(self, runtime, env, limits, **kwargs):
            if asdict(limits) != plan['limits']:
                raise ValueError('Stage override requires the original saved limits')
            super().__init__(runtime, env, requested, **kwargs)

    def guarded_command(*values, **kwargs):
        work = kwargs.get('work')
        prior_check = kwargs.get('conflict_check')
        if work is not None:
            monitor = RunningMemoryGuard(work, args.desktop_memory_reserve_gib, args.worker_memory_gib)
            def check():
                if prior_check:
                    prior_check()
                monitor()
            kwargs['conflict_check'] = check
        return command_executor.command(*values, **kwargs)

    class RecordedCapacityPipeline(Pipeline):
        def __init__(self, root, identity, limits, *callbacks, **kwargs):
            if not kwargs.get('prepare_only') or identity != plan['identity'] or asdict(limits) != plan['limits']:
                raise ValueError('Capacity override cannot change the task or evaluator identity')
            # run.main holds runner.lock and has independently reverified its
            # source pins, saved plan and selected input identity at this point.
            record = {'schema_version': 'nonrect_preparation_capacity_override_v1',
                      'created_at': datetime.now(timezone.utc).isoformat(),
                      'authorization': 'User requested safe preparation capacity, then reduction after desktop memory warning',
                      'original_plan_sha256': sha(output / 'plan.json'),
                      'planned_limits': plan['limits'], 'effective_limits': asdict(requested),
                      'override_source_sha256': sha(Path(__file__)),
                      'command_executor_sha256': sha(Path(command_executor.__file__)),
                      'desktop_physical_reserve_gib': args.desktop_memory_reserve_gib,
                      'running_owned_case_rss_cap_gib': args.worker_memory_gib,
                      'kernel_memory_pressure_must_equal': 1,
                      'headroom_accounting': 'darwin_non_speculative_free_plus_file_backed_v1',
                      'source_case_identity_changed': False, 'model_requests_enabled': False}
            atomic_write(output / 'execution_overrides' / (uuid.uuid4().hex + '.json'), record)
            kwargs['probe'] = DesktopHostProbe(root, args.desktop_memory_reserve_gib, requested.preparation_memory_gib)
            super().__init__(root, identity, requested, *callbacks, **kwargs)

    run.Pipeline = RecordedCapacityPipeline
    run.command = guarded_command
    run.Stages = RecoveryStages
    arguments = ['--runtime', str(args.runtime), '--output-root', str(output),
                 '--content-fingerprint-validation', plan['identity']['content_fingerprint_validation'],
                 '--preparation-workers', str(plan['limits']['preparation_workers']),
                 '--evaluation-workers', str(plan['limits']['evaluation_workers']),
                 '--ready-capacity', str(plan['limits']['ready_capacity']),
                 '--ready-gib', str(plan['limits']['ready_gib']),
                 '--backpressure-timeout', str(plan['limits']['backpressure_timeout']), '--prepare-only']
    conflict_root = plan['identity'].get('conflict_guard_root')
    if conflict_root:
        arguments += ['--avoid-conflicts-with', conflict_root, '--only-unclaimed']
    else:
        for task in plan['tasks']:
            arguments += ['--case-id', task['case_id']]
    try:
        return run.main(arguments)
    finally:
        run.Pipeline = original_class
        run.command = original_command
        run.Stages = original_stages


if __name__ == '__main__':
    raise SystemExit(main())
