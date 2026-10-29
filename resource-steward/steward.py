#!/usr/bin/env python3
"""Resource Steward: local monitoring, supervised commands, and Drive handoff queue."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

from core import audit, eligible_files, evaluate, health, runtime_guard
from state import Store


def default_cgroup():
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            fields = line.split(':', 2)
            if len(fields) == 3 and fields[0] == '0' and fields[1] == '':
                return Path('/sys/fs/cgroup') / fields[2].lstrip('/')
    except OSError:
        pass
    return Path('/sys/fs/cgroup/user')


def report(args):
    root = args.root.resolve()
    h = health(args.cgroup, args.meminfo, root, args.cgroup_root)
    a = audit(root)
    return {'health': h, 'audit': a, 'policy': evaluate({'health': h, 'audit': a})}


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def daemon(args):
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock_path = args.state.with_suffix('.lock')
    with lock_path.open('a+b') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit({'status': 'already_running', 'lock': str(lock_path)})
            return 4
        with Store(args.state) as store:
            emit({'status': 'monitor_started', 'pid': os.getpid(), 'interval_s': args.interval})
            while True:
                try:
                    r = report(args)
                    new_queue = 0
                    # Include our dedicated non-snapshotted staging area; never
                    # traverse the user's entire .cache or dependencies.
                    for base in (args.root, args.root / '.cache' / 'resource-steward'):
                        if not base.is_dir():
                            continue
                        for item in eligible_files(base, 25_000_000):
                            if store.queue(base.resolve() / item['path'], item['bytes'], item['mtime_ns']):
                                new_queue += 1
                    alert_keys = set(r['policy']['reasons'])
                    if r['health']['events'].get('oom_kill', 0):
                        alert_keys.add('oom_kill recorded in cgroup')
                    changes = store.update_alerts(alert_keys)
                    if changes['new'] or changes['cleared'] or new_queue or args.once:
                        emit({'status': 'scanned', 'policy': r['policy'],
                              'workspace_bytes': r['audit']['candidate_bytes'],
                              'new_alerts': changes['new'],
                              'cleared_alerts': changes['cleared'], 'new_queue': new_queue})
                except (OSError, ValueError, sqlite3.Error) as exc:
                    emit({'status': 'scan_error', 'error': type(exc).__name__, 'detail': str(exc)[:160]})
                    if args.once: return 5
                if args.once: return 0
                time.sleep(args.interval)


def _group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal_group(pgid, signum):
    try:
        os.killpg(pgid, signum)
        return True
    except ProcessLookupError:
        return False


def stop_process_group(child, first_signal=signal.SIGTERM, grace_seconds=3.0):
    """Stop only the session created for this child, allowing its group to exit."""
    pgid = child.pid
    if not _signal_group(pgid, first_signal):
        child.poll()
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        child.poll()  # Reap the direct child, so it does not keep the group alive.
        if not _group_exists(pgid):
            return
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    _signal_group(pgid, signal.SIGKILL)
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def supervise(args):
    r = report(args)
    if r['policy']['level'] == 'stop':
        emit({'status': 'preflight_stop', 'reasons': r['policy']['reasons']})
        return 3
    cmd = args.command
    if cmd and cmd[0] == '--': cmd = cmd[1:]
    if not cmd:
        emit({'status': 'error', 'detail': 'provide a command after --'})
        return 2

    pending_signal = None
    child = None
    cleaned = False
    previous_handlers = {}

    def handle_signal(signum, _frame):
        nonlocal pending_signal
        if pending_signal is None:
            pending_signal = signum

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous_handlers[signum] = signal.signal(signum, handle_signal)
        try:
            child = subprocess.Popen(cmd, start_new_session=True)
        except FileNotFoundError:
            emit({'status': 'error', 'detail': 'command_not_found'})
            return 127
        except OSError as exc:
            emit({'status': 'error', 'detail': 'cannot_start_command', 'error': type(exc).__name__})
            return 126
        emit({'status': 'started', 'pid': child.pid})
        while True:
            if pending_signal is not None:
                emit({'status': 'interrupted', 'pid': child.pid, 'signal': pending_signal})
                stop_process_group(child, first_signal=pending_signal)
                cleaned = True
                return 128 + pending_signal
            code = child.poll()
            if code is not None:
                # Do not leave same-group descendants running without supervision.
                if _group_exists(child.pid):
                    stop_process_group(child)
                cleaned = True
                emit({'status': 'exited', 'pid': child.pid, 'exit_code': code})
                return code if code >= 0 else 128 - code
            h = health(args.cgroup, args.meminfo, args.root, args.cgroup_root)
            guard = runtime_guard(h)
            if guard:
                emit({'status': guard['status'], 'pid': child.pid, 'reasons': guard['reasons'],
                      'headroom_bytes': h['headroom_bytes'], 'disk_free_bytes': h['disk_free_bytes'],
                      'memory_usage_ratio': h['memory_usage_ratio'],
                      'memory_telemetry_errors': h['memory_telemetry_errors']})
                stop_process_group(child)
                cleaned = True
                return 124 if guard['status'] == 'guard_stop' else 125
            time.sleep(.25)
    except Exception as exc:
        # A failed monitor must not silently leave its supervised task running.
        if child is not None:
            try:
                stop_process_group(child)
                cleaned = True
            except OSError:
                pass
        emit({'status': 'monitoring_error', 'error': type(exc).__name__, 'detail': str(exc)[:160]})
        return 125
    finally:
        if child is not None and not cleaned:
            try:
                stop_process_group(child)
            except OSError:
                pass
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local resource protection; no native Arena hook or unattended Drive credentials')
    parser.add_argument('--root', type=Path, default=Path('/home/user'))
    parser.add_argument('--cgroup', type=Path, default=default_cgroup())
    parser.add_argument('--cgroup-root', type=Path, help='visible cgroup ancestor boundary (default: system mount or custom leaf)')
    parser.add_argument('--meminfo', type=Path, default=Path('/proc/meminfo'))
    parser.add_argument('--state', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('status','preflight','audit','queue'): sub.add_parser(name)
    daemon_p = sub.add_parser('daemon')
    daemon_p.add_argument('--interval', type=float, default=15.0)
    daemon_p.add_argument('--once', action='store_true')
    run_p = sub.add_parser('run')
    run_p.add_argument('command', nargs=argparse.REMAINDER)
    record_p = sub.add_parser('record-upload')
    record_p.add_argument('--path', type=Path, required=True)
    record_p.add_argument('--drive-id', required=True)
    record_p.add_argument('--verified-bytes', required=True, type=int)
    record_p.add_argument('--checksum')
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    args.state = args.state or args.root / 'resource-steward' / 'state.sqlite'
    if args.action == 'audit': emit(audit(args.root)); return 0
    if args.action in ('status','preflight'):
        r = report(args); emit(r)
        return 3 if args.action == 'preflight' and r['policy']['level'] == 'stop' else 0
    if args.action == 'daemon':
        if not math.isfinite(args.interval) or args.interval < 0.2:
            parser.error('--interval must be a finite number >= 0.2 seconds')
        return daemon(args)
    if args.action == 'run': return supervise(args)
    with Store(args.state) as store:
        if args.action == 'queue': emit(store.pending()); return 0
        if args.action == 'record-upload':
            try:
                result = store.record_verified(args.path, args.drive_id, args.verified_bytes, args.checksum)
            except ValueError as exc:
                emit({'status':'error','detail':str(exc)})
                return 2
            emit(result)
            return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
