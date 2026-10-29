#!/usr/bin/env python3
"""Resource Steward: local monitoring, supervised commands, and Drive handoff queue."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

from core import MIB, audit, eligible_files, evaluate, health
from state import Store


def default_cgroup():
    try:
        path = Path('/proc/self/cgroup').read_text().strip().splitlines()[0].split(':', 2)[-1]
        return Path('/sys/fs/cgroup') / path.lstrip('/')
    except (OSError, IndexError):
        return Path('/sys/fs/cgroup/user')


def report(args):
    root = args.root.resolve()
    h = health(args.cgroup, args.meminfo, root)
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
                    new_alerts = store.set_alerts(alert_keys)
                    if new_alerts or new_queue or args.once:
                        emit({'status': 'scanned', 'policy': r['policy'],
                              'workspace_bytes': r['audit']['candidate_bytes'],
                              'new_alerts': new_alerts, 'new_queue': new_queue})
                except (OSError, ValueError, sqlite3.Error) as exc:
                    emit({'status': 'scan_error', 'error': type(exc).__name__, 'detail': str(exc)[:160]})
                    if args.once: return 5
                if args.once: return 0
                time.sleep(args.interval)


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
    child = subprocess.Popen(cmd, start_new_session=True)
    emit({'status': 'started', 'pid': child.pid})
    while True:
        code = child.poll()
        if code is not None:
            emit({'status': 'exited', 'pid': child.pid, 'exit_code': code})
            return code if code >= 0 else 128 - code
        h = health(args.cgroup, args.meminfo, args.root)
        limit, current = h['limit_bytes'], h['current_bytes']
        head = h['headroom_bytes']
        if (limit is not None and current is not None and current >= limit * 0.80) or (head is not None and head < 256 * MIB):
            emit({'status': 'guard_stop', 'pid': child.pid, 'headroom_bytes': head,
                  'reason': 'near configured RAM safety boundary'})
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            # A parent can exit while its descendants ignore TERM and keep
            # stdout/stderr pipes open. Clean only the process group we created.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if child.poll() is None:
                child.wait()
            return 124
        time.sleep(.25)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local resource protection; no native Arena hook or unattended Drive credentials')
    parser.add_argument('--root', type=Path, default=Path('/home/user'))
    parser.add_argument('--cgroup', type=Path, default=default_cgroup())
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
        if args.interval < 0.2: parser.error('--interval must be >= 0.2 seconds')
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
