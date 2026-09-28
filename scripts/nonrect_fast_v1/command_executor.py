"""Owned-process execution with original failure preserved during shutdown."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from .pipeline import GIB, atomic_write
from .run import WORKSPACE, disk_bytes


def group_members(group):
    result = subprocess.run(['ps', '-axo', 'pid=,pgid=,stat='], capture_output=True,
                            text=True, check=True, timeout=15)
    return [int(fields[0]) for row in result.stdout.splitlines()
            if len(fields := row.split()) == 3 and int(fields[1]) == group
            and not fields[2].startswith('Z')]


def terminate_owned_group(process, *, members=group_members, send=os.killpg):
    receipt = {'group': process.pid, 'signals': [], 'errors': []}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        live = members(process.pid)
        if not live:
            break
        try:
            send(process.pid, sig)
            receipt['signals'].append(sig.name)
        except ProcessLookupError:
            pass
        except OSError as exc:
            receipt['errors'].append({'signal': sig.name, 'type': type(exc).__name__, 'errno': exc.errno})
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        # The parent can exit before Blender completes normal SIGTERM cleanup.
        # Allow that owned group to drain before considering SIGKILL.
        deadline = time.monotonic() + 2
        while members(process.pid) and time.monotonic() < deadline:
            time.sleep(.1)
    receipt['remaining_pids'] = members(process.pid)
    receipt['confirmed_exited'] = not receipt['remaining_pids']
    return receipt


def command(argv, log, env, stop, *, timeout=43200, work=None, growth_gib=2.0, conflict_check=None,
            disk_guard=True):
    if stop.is_set():
        raise InterruptedError('Cancelled before subprocess admission')
    if conflict_check:
        conflict_check()
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    baseline = disk_bytes(work) if work and disk_guard else 0
    with log.open('w') as stream:
        process = subprocess.Popen([str(x) for x in argv], cwd=WORKSPACE, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            last_disk_check = 0.0
            while process.poll() is None:
                if stop.wait(.5):
                    raise InterruptedError('Cancelled owned subprocess')
                if conflict_check:
                    conflict_check()
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Subprocess exceeded stage deadline')
                if work and disk_guard and time.monotonic() - last_disk_check >= 2:
                    last_disk_check = time.monotonic()
                    if shutil.disk_usage(work).free < 30 * GIB:
                        raise OSError('Disk reserve reached; stopping owned worker')
                    if disk_bytes(work) - baseline > growth_gib * GIB:
                        raise OSError('Stage growth reservation exceeded; stopping owned worker')
            if process.returncode:
                raise RuntimeError('Subprocess failed: ' + str(log))
            if conflict_check:
                conflict_check()
        except BaseException as cause:
            try:
                shutdown = terminate_owned_group(process)
            except Exception as exc:
                shutdown = {'confirmed_exited': False, 'error_type': type(exc).__name__}
            if not shutdown['confirmed_exited']:
                # Admission cannot continue if ownership cleanup is uncertain.
                stop.set()
            atomic_write(log.with_suffix('.failure.json'), {'error_type': type(cause).__name__,
                         'error': str(cause), 'shutdown': shutdown})
            raise
