"""Recover a fully built room held back by the conservative 2 GiB estimate.

Only an already completed preparation with the exact oversize failure is allowed.
Rechecks sealed-runtime input hashes without Blender or API requests. Keeps its
original case identity and records the actual size as an explicit case exception.
"""
import argparse
import fcntl
import os
from pathlib import Path
import sys
import threading

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nonrect_fast_v1 import content_policy, derive
from nonrect_fast_v1.pipeline import (GIB, CaseLease, Limits, atomic_write, canonical_sha,
                                     inventory_files, publish_ready, read, safe_output, sha)
from nonrect_fast_v1.run import Stages, command


def storage_accepts(physical_free_gib, prepared_bytes, other_ready_bytes, queue_gib):
    if physical_free_gib < 30 or prepared_bytes > 4 * GIB or other_ready_bytes + prepared_bytes > queue_gib * GIB:
        raise ValueError('Verified preparation exceeds the reviewed storage bounds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--case-id', required=True)
    args = parser.parse_args()
    import shutil
    output = safe_output(args.output_root)
    derive.verify(args.runtime)
    plan = read(output / 'plan.json')
    task = next(row for row in plan['tasks'] if row['case_id'] == args.case_id)
    with (output / 'runner.lock').open('a+') as runner_lock:
        fcntl.flock(runner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lease = CaseLease(output / 'rooms' / task['case_id'], {'campaign': plan['identity'], 'task': task})
        try:
            root = lease.root
            old = read(root / 'state.json')
            if old.get('error') != 'Prepared case exceeds its reserved size; no ready marker published':
                raise ValueError('Only the verified oversize preparation state can be recovered here')
            if (root / 'ready.json').exists() or (root / 'consuming.json').exists():
                raise ValueError('Case is already owned by a consumer')
            files = inventory_files(root)
            size = sum(row[0] for row in files.values())
            other = sum(read(path)['prepared_bytes'] for path in (output / 'rooms').glob('*/ready.json'))
            storage_accepts(shutil.disk_usage(output).free / GIB, size, other, plan['limits']['ready_gib'])
            env = dict(os.environ)
            env[content_policy.ENV] = plan['identity']['content_fingerprint_validation']
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            stages = Stages(args.runtime.resolve(), env, Limits(**plan['limits']),
                            avoid_conflicts_with=Path(plan['identity']['conflict_guard_root']) if plan['identity'].get('conflict_guard_root') else None)
            stages.conflict_check(root)
            command(stages.uniform_args(root), root / 'recovery_input_check.json', env, threading.Event(),
                    timeout=300, work=root, conflict_check=lambda: stages.conflict_check(root))
            checked = read(root / 'recovery_input_check.json')
            if checked != read(root / 'input_check.json') or [r['case_id'] for r in checked['cases']] != [task['case_id']]:
                raise ValueError('Prepared input identity differs on independent reload')
            atomic_write(root / 'input_receipt.json', checked)
            atomic_write(root / 'preparation_recovery.json', {'previous_state': old, 'prepared_bytes': size,
                         'exception': 'Existing complete case admitted after actual disk usage review; no extra Blender work',
                         'original_case_identity_changed': False, 'recovery_source_sha256': sha(Path(__file__)),
                         'input_check_sha256': sha(root / 'recovery_input_check.json')})
            atomic_write(root / 'preparation_timing.json', {'real_blender': True, 'total_prepare_seconds': None,
                         'timing_limitation': 'Original timing receipt was not published after the size guard; see stage logs',
                         'recovered_from_verified_complete_artifacts': True})
            marker = publish_ready(lease)
            atomic_write(root / 'state.json', {'case_id': task['case_id'], 'status': 'ready',
                         'identity_sha256': canonical_sha(lease.identity), 'prepared_bytes': marker['prepared_bytes'],
                         'actual_size_exception_receipt': 'preparation_recovery.json'})
            print({'case_id': task['case_id'], 'ready': True, 'actual_gib': size / GIB})
        finally:
            lease.close()


if __name__ == '__main__':
    main()
