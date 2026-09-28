"""Bounded preparation/evaluation pipeline with durable case ownership.

The lock spans preparing, ready and consuming. Disk admission reserves only
future growth; RAM admission subtracts startup reservations, not the memory of
workers already represented in the host observation. No global halt on a room
failure, and no automatic paid room retries.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
import fcntl
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

GIB = 1024 ** 3
TERMINAL = {"complete", "not_score_eligible", "mock_evaluated"}
PROTECTED_MAIN = Path("/Users/han_mohan/Desktop/Layout_DDD")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def safe_output(path):
    raw = Path(path).absolute()
    path = raw.resolve()
    if raw != path:
        raise ValueError("Output may not have symlink components")
    main = PROTECTED_MAIN.resolve()
    if path.is_relative_to(main) or main.is_relative_to(path):
        raise ValueError("The active main repository is read-only for this runner")
    return path


class CaseBusy(RuntimeError):
    pass


class IdentityMismatch(ValueError):
    pass


class ExternalConflict(RuntimeError):
    """The read-only historical queue began owning this source case."""


class CaseLease:
    def __init__(self, root, identity):
        self.root = safe_output(root)
        if self.root.is_symlink():
            raise ValueError("Symlink case directory")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "case.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity_path = self.root / "identity.json"
            if identity_path.exists():
                if identity_path.is_symlink() or read(identity_path) != identity:
                    raise IdentityMismatch("Case identity is immutable")
            else:
                # Never adopt unidentified historical artifacts as fast results.
                if any(p.name != 'case.lock' for p in self.root.iterdir()):
                    raise IdentityMismatch("Nonempty case has no fast-run identity")
                atomic_write(identity_path, identity)
            self.identity = identity
        except BlockingIOError:
            self.stream.close()
            raise CaseBusy(str(root)) from None
        except BaseException:
            self.stream.close()
            raise

    def close(self):
        self.stream.close()


def inventory_files(root):
    result = {}
    for folder in ("dataset", "materialized", "initial_render"):
        base = Path(root) / folder
        if base.is_symlink():
            raise ValueError("Prepared artifact may not be a symlink")
        for path in base.rglob("*"):
            if path.is_symlink():
                raise ValueError("Prepared artifact may not contain a symlink")
            if path.is_file():
                stat = path.stat()
                result[str(path.relative_to(root))] = [stat.st_size, stat.st_mtime_ns]
    return result


def publish_ready(lease):
    root = lease.root
    if not (root / "input_receipt.json").is_file() or not (root / "dataset").is_dir():
        raise ValueError("Incomplete preparation cannot become ready")
    files = inventory_files(root)
    marker = {"schema_version": "nonrect_atomic_ready_v1", "identity": lease.identity,
              "input_receipt_sha256": sha(root / "input_receipt.json"), "files": files,
              "prepared_bytes": sum(value[0] for value in files.values())}
    atomic_write(root / "ready.json", marker)
    return marker


def validate_ready(lease):
    marker = read(lease.root / "ready.json")
    if (marker.get("schema_version") != "nonrect_atomic_ready_v1"
            or marker.get("identity") != lease.identity
            or marker.get("input_receipt_sha256") != sha(lease.root / "input_receipt.json")
            or marker.get("files") != inventory_files(lease.root)):
        raise IdentityMismatch("Prepared artifacts or identity changed after ready publication")
    return marker


@dataclass(frozen=True)
class Limits:
    preparation_workers: int = 1
    evaluation_workers: int = 12
    ready_capacity: int = 2
    ready_gib: float = 4.0
    minimum_free_gib: float = 30.0
    preparation_growth_gib: float = 2.0
    evaluation_growth_gib: float = 2.0
    reserve_memory_gib: float = 8.0
    preparation_memory_gib: float = 4.0
    evaluation_memory_gib: float = 2.0
    startup_seconds: float = 15.0
    max_load_per_cpu: float = 1.25
    minimum_cpu_idle_percent: float = 15.0
    startup_cpu_percent: float = 12.0
    max_swapout_mib_per_second: float = 16.0
    backpressure_timeout: float = 300.0
    poll_seconds: float = 1.0

    def __post_init__(self):
        if not 1 <= self.preparation_workers <= 12 or not 1 <= self.evaluation_workers <= 12:
            raise ValueError("Preparation and evaluation worker counts must be 1..12")
        if self.ready_capacity < 1 or self.ready_gib < self.preparation_growth_gib:
            raise ValueError("Ready capacity must fit at least one preparation reservation")
        if self.minimum_free_gib < 30:
            raise ValueError("Keep at least 30 GiB host disk headroom")
        for name, value in asdict(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Resource limits must be finite and positive: " + name)


class HostProbe:
    def __init__(self, root):
        self.root = Path(root)
        self.previous = None
        self.cached = None
        self.previous_cpu = None

    def cpu_idle_percent(self):
        # Darwin host CPU ticks: user, system, idle, nice. No psutil dependency
        # and no process scanning; rates use consecutive host observations.
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        library.mach_host_self.restype = ctypes.c_uint
        ticks = (ctypes.c_uint * 4)()
        count = ctypes.c_uint(4)
        if library.host_statistics(library.mach_host_self(), 3, ctypes.byref(ticks), ctypes.byref(count)) != 0:
            raise OSError('Cannot read actual host CPU usage')
        current = tuple(ticks)
        previous, self.previous_cpu = self.previous_cpu, current
        if previous is None:
            return None
        delta = [(a - b) % 2**32 for a, b in zip(current, previous)]
        return 100 * delta[2] / sum(delta) if sum(delta) else None

    def __call__(self):
        if self.cached is not None and time.monotonic() - self.cached['observed_at_monotonic'] < 1:
            return self.cached
        pressure = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, check=True, timeout=15).stdout
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True, timeout=15).stdout
        percent = re.search(r"System-wide memory free percentage:\s*(\d+)%", pressure)
        page = re.search(r"page size of (\d+) bytes", vm)
        swap = re.search(r"Swapouts:\s*(\d+)", vm)
        if not percent or not page or not swap:
            raise RuntimeError("Host memory/swap observation unavailable; admission paused")
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        current = (time.monotonic(), int(swap.group(1)) * int(page.group(1)))
        # First observation only warms the swap-rate probe; no worker is admitted.
        swap_rate = None if self.previous is None else max(0, current[1] - self.previous[1]) / max(0.001, current[0] - self.previous[0]) / 1024**2
        self.previous = current
        self.cached = {"available_memory_gib": total / GIB * int(percent.group(1)) / 100,
                "free_disk_gib": shutil.disk_usage(self.root).free / GIB,
                "load_per_cpu": os.getloadavg()[0] / (os.cpu_count() or 1),
                "cpu_idle_percent": self.cpu_idle_percent(),
                "swapout_mib_per_second": swap_rate,
                "memory_estimate": "memory_pressure usable percentage; not physical free RAM",
                "observed_at_monotonic": current[0]}
        return self.cached


class Governor:
    def __init__(self, limits, probe, clock=time.monotonic):
        self.limits, self.probe, self.clock = limits, probe, clock
        self.tokens = {}
        self.last = {}
        self.reason = None

    def acquire(self, stage):
        limits = self.limits
        memory = limits.preparation_memory_gib if stage == "prepare" else limits.evaluation_memory_gib
        disk = limits.preparation_growth_gib if stage == "prepare" else limits.evaluation_growth_gib
        try:
            observation = self.last = self.probe()
        except Exception as exc:
            self.reason = "host_probe_unavailable:" + type(exc).__name__
            return None
        startup = sum(t["memory"] for t in self.tokens.values() if self.clock() - t["start"] < limits.startup_seconds)
        cpu_startup = sum(limits.startup_cpu_percent for t in self.tokens.values() if self.clock() - t['start'] < limits.startup_seconds)
        reserved_disk = sum(t["disk"] for t in self.tokens.values())
        # Host observation already includes established workers. Reserve just the
        # incremental worker plus allocations still in their startup window.
        checks = {
            "memory": observation["available_memory_gib"] >= limits.reserve_memory_gib + startup + memory,
            "disk": observation["free_disk_gib"] >= limits.minimum_free_gib + reserved_disk + disk,
            "cpu": ((observation['cpu_idle_percent'] is not None
                     and observation['cpu_idle_percent'] >= limits.minimum_cpu_idle_percent + cpu_startup + limits.startup_cpu_percent)
                    if 'cpu_idle_percent' in observation else observation["load_per_cpu"] <= limits.max_load_per_cpu),
            "swap": observation["swapout_mib_per_second"] is not None and observation["swapout_mib_per_second"] <= limits.max_swapout_mib_per_second,
        }
        self.reason = next((key for key, okay in checks.items() if not okay), None)
        if self.reason:
            return None
        token = uuid.uuid4().hex
        self.tokens[token] = {"start": self.clock(), "memory": memory, "disk": disk, "stage": stage}
        return token

    def release(self, token):
        del self.tokens[token]


def cleanup_terminal(lease, *, enabled):
    """Only after durable terminal state; queued/consuming inputs stay protected.

    A success keeps the complete evaluation report, input receipt, identity,
    source/version pins and lifecycle receipts, sufficient for score reaggregation
    and deterministic re-preparation from the retained external source. It does
    not claim that a paid model response can be regenerated identically.
    """
    root = lease.root
    state = read(root / "state.json")
    if not enabled or (root / "ready.json").exists() or (root / "consuming.json").exists():
        return []
    if state['status'] not in TERMINAL | {"failed_eval", "infrastructure_failure"}:
        return []
    if state['status'] in TERMINAL and sha(report_path(root, state['report'])) != state['report_sha256']:
        raise IdentityMismatch("Report must be verified before cleanup")
    if state['status'] in {'complete', 'not_score_eligible'}:
        proof = read(report_path(root, state['scoring_replay_receipt']))
        if proof.get('status') != 'passed' or proof.get('report_sha256') != state['report_sha256']:
            raise IdentityMismatch('Independent scoring replay is required before cleanup')
    removed = []
    for name in ("materialized", ".materialized.building", "dataset", "initial_render", "evaluation"):
        path = root / name
        if path.is_symlink() or any(p.is_symlink() for p in path.rglob('*')):
            raise ValueError("Cleanup refuses symlink artifacts")
        if path.exists():
            shutil.rmtree(path)
            removed.append(name)
    atomic_write(root / "cleanup.json", {"removed": removed, "state": state['status'], "complete_report_retained": state['status'] in TERMINAL})
    return removed


def report_path(root, relative):
    path = Path(relative)
    candidate = Path(root) / path
    if path.is_absolute() or '..' in path.parts or candidate.is_symlink() or not candidate.resolve().is_relative_to(Path(root).resolve()):
        raise IdentityMismatch('Report path escapes owned case')
    return candidate


class Pipeline:
    def __init__(self, output, identity, limits, prepare, evaluate, *, probe=None, stop=None,
                 prepare_only=False, cleanup=False, retry_failed=False):
        self.output = safe_output(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.identity, self.limits = identity, limits
        self.prepare, self.evaluate = prepare, evaluate
        self.stop = stop if stop is not None else threading.Event()
        self.prepare_only, self.cleanup, self.retry_failed = prepare_only, cleanup, retry_failed
        self.governor = Governor(limits, probe or HostProbe(self.output))
        self.events = []

    def event(self, case, event):
        self.events.append({"case_id": case, "event": event, "monotonic": time.monotonic(),
                            **({'admission_host': self.governor.last} if event.endswith('_started') else {})})

    def state(self, lease, status, **extra):
        state = {"case_id": lease.identity['task']['case_id'], "identity_sha256": canonical_sha(lease.identity),
                 "status": status, **extra}
        atomic_write(lease.root / "state.json", state)
        return state

    def execute(self, tasks):
        if len({t['case_id'] for t in tasks}) != len(tasks):
            raise ValueError("Duplicate case ID")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", t['case_id']) or t['case_id'] in {'.', '..'} for t in tasks):
            raise ValueError("Invalid case ID")
        waiting, ready, results = deque(tasks), deque(), []
        active, leases = {}, {}
        failed_since = None
        peak_prepare = peak_eval = peak_ready = 0
        pools = {"prepare": ThreadPoolExecutor(self.limits.preparation_workers),
                 "evaluate": ThreadPoolExecutor(self.limits.evaluation_workers)}

        def finish(lease, state):
            results.append(state)
            leases.pop(state['case_id'], None)
            lease.close()

        def claim(task):
            case = task['case_id']
            lease = CaseLease(self.output / 'rooms' / case, {"campaign": self.identity, "task": task})
            leases[case] = lease
            old = read(lease.root / 'state.json') if (lease.root / 'state.json').exists() else {}
            if old.get('status') in TERMINAL:
                if old.get('identity_sha256') != canonical_sha(lease.identity) or sha(report_path(lease.root, old['report'])) != old['report_sha256']:
                    raise IdentityMismatch("Terminal report identity changed")
                # Recover a crash between the durable terminal report/state and
                # retiring ready/consuming markers, without repeating evaluation.
                (lease.root / 'consuming.json').unlink(missing_ok=True)
                (lease.root / 'ready.json').unlink(missing_ok=True)
                cleanup_terminal(lease, enabled=self.cleanup)
                finish(lease, old)
                return None
            if (lease.root / 'consuming.json').exists():
                # Process death could follow a paid request. Do not silently retry.
                if not self.retry_failed:
                    finish(lease, self.state(lease, 'interrupted_evaluation_needs_review', automatic_paid_retry=False))
                    return None
                (lease.root / 'consuming.json').unlink()
            if old.get('status') in {'failed_eval', 'infrastructure_failure', 'interrupted_evaluation_needs_review'} and not self.retry_failed:
                finish(lease, old)
                return None
            return lease

        try:
            while waiting or ready or active:
                completed = [future for future in active if future.done()]
                for future in completed:
                    stage, task, lease, token = active.pop(future)
                    self.governor.release(token)
                    case = task['case_id']
                    try:
                        value = future.result()
                        if stage == 'prepare':
                            marker = publish_ready(lease)
                            self.state(lease, 'ready', prepared_bytes=marker['prepared_bytes'])
                            ready.append((task, lease, marker['prepared_bytes']))
                            self.event(case, 'ready')
                        else:
                            if value.get('status') not in TERMINAL | {'infrastructure_failure', 'failed_eval'}:
                                raise ValueError("Evaluator returned invalid terminal state")
                            self.state(lease, **value)
                            (lease.root / 'consuming.json').unlink(missing_ok=True)
                            (lease.root / 'ready.json').unlink(missing_ok=True)
                            cleanup_terminal(lease, enabled=self.cleanup)
                            finish(lease, read(lease.root / 'state.json'))
                            self.event(case, 'evaluation_finished')
                    except Exception as exc:
                        status = ('blocked_external_conflict' if isinstance(exc, ExternalConflict)
                                  else 'cancelled_prepare' if self.stop.is_set() and stage == 'prepare'
                                  else 'interrupted_evaluation_needs_review' if self.stop.is_set()
                                  else 'failed_' + ('prepare' if stage == 'prepare' else 'eval'))
                        state = self.state(lease, status, error_type=type(exc).__name__, error=str(exc)[:500], automatic_paid_retry=False)
                        if stage == 'evaluate' and not self.stop.is_set():
                            (lease.root / 'consuming.json').unlink(missing_ok=True)
                            (lease.root / 'ready.json').unlink(missing_ok=True)
                            cleanup_terminal(lease, enabled=self.cleanup)
                        finish(lease, state)
                        self.event(case, status)
                if self.stop.is_set():
                    if active:
                        wait(active, timeout=self.limits.poll_seconds, return_when=FIRST_COMPLETED)
                        continue
                    break
                n_prepare = sum(row[0] == 'prepare' for row in active.values())
                n_eval = sum(row[0] == 'evaluate' for row in active.values())
                progressed = bool(completed)
                # Consumers get first claim to resources: draining ready releases
                # disk and avoids a full buffer starving evaluation.
                if ready and not self.prepare_only and n_eval < self.limits.evaluation_workers:
                    token = self.governor.acquire('evaluate')
                    if token:
                        task, lease, size = ready.popleft()
                        try:
                            validate_ready(lease)
                            atomic_write(lease.root / 'consuming.json', {'identity': lease.identity, 'ready_sha256': sha(lease.root / 'ready.json')})
                            self.state(lease, 'evaluating')
                            future = pools['evaluate'].submit(self.evaluate, task, lease.root, self.stop)
                            active[future] = ('evaluate', task, lease, token)
                            self.event(task['case_id'], 'evaluation_started')
                            n_eval += 1
                            progressed = True
                        except Exception as exc:
                            self.governor.release(token)
                            finish(lease, self.state(lease, 'invalid_ready', error_type=type(exc).__name__))
                ready_bytes = sum(row[2] for row in ready)
                can_prepare = (waiting and n_prepare < self.limits.preparation_workers
                               and len(ready) + n_prepare < self.limits.ready_capacity
                               and ready_bytes / GIB + (n_prepare + 1) * self.limits.preparation_growth_gib <= self.limits.ready_gib)
                if can_prepare:
                    # Durable ready and terminal states can be recovered without
                    # admitting another preparation worker or reserving its RAM.
                    candidate = self.output / 'rooms' / waiting[0]['case_id']
                    recover = (candidate / 'ready.json').is_file() or ((candidate / 'state.json').is_file()
                              and read(candidate / 'state.json').get('status') in TERMINAL)
                    token = None if recover else self.governor.acquire('prepare')
                    if token or recover:
                        task = waiting.popleft()
                        lease = None
                        try:
                            lease = claim(task)
                            if lease is None:
                                if token:
                                    self.governor.release(token)
                            elif (lease.root / 'ready.json').exists():
                                marker = validate_ready(lease)
                                ready.append((task, lease, marker['prepared_bytes']))
                                if token:
                                    self.governor.release(token)
                                self.event(task['case_id'], 'ready_recovered')
                            else:
                                if token is None:
                                    raise IdentityMismatch('Recovery state disappeared')
                                self.state(lease, 'preparing')
                                future = pools['prepare'].submit(self.prepare, task, lease.root, self.stop)
                                active[future] = ('prepare', task, lease, token)
                                self.event(task['case_id'], 'preparation_started')
                                n_prepare += 1
                            progressed = True
                        except Exception as exc:
                            if token:
                                self.governor.release(token)
                            owned = leases.pop(task['case_id'], None)
                            if owned:
                                owned.close()
                            results.append({'case_id': task['case_id'], 'status': 'lock_conflict' if isinstance(exc, CaseBusy) else 'identity_rejected', 'error_type': type(exc).__name__})
                            progressed = True
                peak_prepare, peak_eval, peak_ready = max(peak_prepare, n_prepare), max(peak_eval, n_eval), max(peak_ready, len(ready))
                if progressed:
                    failed_since = None
                elif not active:
                    # Preparation-only must stop at the durable buffer limit;
                    # it never turns the bounded queue into an all-room cache.
                    if self.prepare_only and ready and not can_prepare:
                        break
                    failed_since = failed_since or time.monotonic()
                    if time.monotonic() - failed_since >= self.limits.backpressure_timeout:
                        break
                self.save(results, waiting, ready, active, peak_prepare, peak_eval, peak_ready)
                if not progressed:
                    if active:
                        wait(active, timeout=self.limits.poll_seconds, return_when=FIRST_COMPLETED)
                    else:
                        self.stop.wait(self.limits.poll_seconds)
        finally:
            for pool in pools.values():
                pool.shutdown(wait=True, cancel_futures=True)
            for lease in leases.values():
                lease.close()
            self.save(results, waiting, ready, active, peak_prepare, peak_eval, peak_ready, final=True)
        return read(self.output / 'queue_summary.json')

    def save(self, results, waiting, ready, active, peak_prepare, peak_eval, peak_ready, final=False):
        atomic_write(self.output / 'queue_summary.json', {
            'identity': self.identity, 'limits': asdict(self.limits), 'results': results,
            'waiting': [task['case_id'] for task in waiting], 'ready': [task['case_id'] for task, _, _ in ready],
            'active': [{'stage': stage, 'case_id': task['case_id']} for stage, task, _, _ in active.values()],
            'peaks': {'prepare': peak_prepare, 'evaluate': peak_eval, 'ready': peak_ready},
            'host': self.governor.last, 'backpressure_reason': self.governor.reason,
            'status': ('interrupted' if self.stop.is_set()
                       else 'prepared_buffer_ready' if final and self.prepare_only and ready and not active
                       else 'paused_with_pending' if final and (waiting or ready or active)
                       else 'finished_with_failures' if final and any(r['status'] not in TERMINAL for r in results)
                       else 'finished' if final else 'running'),
            'events': self.events})
