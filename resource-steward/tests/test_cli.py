import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'steward.py'
MIB = 1024 * 1024


class CliTests(unittest.TestCase):
    def test_installed_launcher_exposes_cli(self):
        # A checkout contains install.sh; test it in an isolated home. Installed
        # copies omit install.sh and can test their own sibling launcher instead.
        project_or_home = SCRIPT.parents[1]
        installer = project_or_home / 'install.sh'
        if installer.is_file():
            home = self.root / 'installed-home'
            env = dict(os.environ, RESOURCE_STEWARD_HOME=str(home))
            result = subprocess.run(['sh', str(installer)], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            home = project_or_home
        launcher = home / 'bin' / 'resource-steward'
        p = subprocess.run([str(launcher), '--help'], capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('preflight', p.stdout)

    def test_installer_preserves_state_and_guidance_is_idempotent(self):
        installer = SCRIPT.parents[1] / 'install.sh'
        if not installer.is_file():
            self.skipTest('installer is not included in the installed copy')
        home = self.root / 'installed-home'
        (home / 'resource-steward').mkdir(parents=True)
        state = home / 'resource-steward' / 'state.sqlite'
        state.write_bytes(b'preserve existing state')
        guide = home / 'AGENTS.md'
        guide.write_text('Existing instructions\n', encoding='utf-8')
        env = dict(os.environ, RESOURCE_STEWARD_HOME=str(home))
        for _ in range(2):
            result = subprocess.run(['sh', str(installer), '--enable-agent-guide'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.read_bytes(), b'preserve existing state')
        contents = guide.read_text(encoding='utf-8')
        self.assertTrue(contents.startswith('Existing instructions\n'))
        self.assertEqual(contents.count('<!-- Resource Steward local workflow -->'), 1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cg = self.root / 'cg'; self.cg.mkdir()
        (self.cg / 'memory.max').write_text(str(800 * MIB))
        (self.cg / 'memory.current').write_text(str(100 * MIB))
        (self.cg / 'memory.events').write_text('oom_kill 0\n')
        self.mem = self.root / 'meminfo'
        self.mem.write_text('MemAvailable: 1000000 kB\n')
        self.state = self.root / 'state.sqlite'
        self.args = [sys.executable, str(SCRIPT), '--root', str(self.root), '--cgroup', str(self.cg), '--meminfo', str(self.mem), '--state', str(self.state)]

    def test_status_and_daemon_queue_sparse_110mb_without_upload(self):
        big = self.root / 'demo.bin'
        with big.open('wb') as f: f.truncate(110_000_000)
        p = subprocess.run(self.args + ['daemon','--once'],capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
        q = subprocess.run(self.args + ['queue'],capture_output=True,text=True,timeout=5)
        self.assertEqual(q.returncode,0,q.stderr)
        self.assertEqual(json.loads(q.stdout)[0]['size'],110_000_000)
        s = subprocess.run(self.args + ['status'],capture_output=True,text=True,timeout=5)
        self.assertEqual(json.loads(s.stdout)['policy']['level'],'stop')
        bad = subprocess.run(self.args + ['record-upload','--path',str(big),'--drive-id','some-id','--verified-bytes','1'],capture_output=True,text=True,timeout=5)
        self.assertNotEqual(bad.returncode,0)

    def test_record_upload_labels_operator_attestation_without_cloud_check(self):
        # A matching local size is not proof that a remote Drive file exists.
        artifact = self.root / 'sample.bin'
        with artifact.open('wb') as f: f.truncate(25_000_000)
        scan = subprocess.run(self.args + ['daemon', '--once'],
                              capture_output=True, text=True, timeout=5)
        self.assertEqual(scan.returncode, 0, scan.stderr)
        record = subprocess.run(self.args + ['record-upload', '--path', str(artifact),
                                             '--drive-id', 'NOT_A_REAL_DRIVE_ID',
                                             '--verified-bytes', '25000000'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(record.returncode, 0, record.stderr)
        result = json.loads(record.stdout)
        self.assertEqual(result['status'], 'verified')  # existing public status
        self.assertEqual(result['verification_source'], 'operator')
        self.assertIs(result['remote_verified_by_tool'], False)
        help_result = subprocess.run([sys.executable, str(SCRIPT), 'record-upload', '--help'],
                                     capture_output=True, text=True, timeout=5)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn('does not contact Drive', help_result.stdout)

    def test_corrupt_state_fails_with_structured_error(self):
        self.state.write_bytes(b'not a sqlite database')
        result = subprocess.run(self.args + ['queue'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stdout)['status'], 'storage_error')
        self.assertNotIn('Traceback', result.stderr)

    def test_daemon_queues_dedicated_excluded_staging_area(self):
        stage = self.root / '.cache' / 'resource-steward'
        stage.mkdir(parents=True)
        part = stage / 'part.bin'
        with part.open('wb') as f: f.truncate(110_000_000)
        run = subprocess.run(self.args + ['daemon','--once'],capture_output=True,text=True,timeout=5)
        self.assertEqual(run.returncode,0,run.stderr)
        out = subprocess.run(self.args + ['queue'],capture_output=True,text=True,timeout=5)
        self.assertEqual([x['path'] for x in json.loads(out.stdout)], [str(part)])
        status = subprocess.run(self.args + ['status'],capture_output=True,text=True,timeout=5)
        self.assertLess(json.loads(status.stdout)['audit']['candidate_bytes'], 100_000_000)

    def test_run_normal_exit_and_guard_only_its_child(self):
        ok = subprocess.run(self.args+['run','--',sys.executable,'-c','print("ok")'],capture_output=True,text=True,timeout=5)
        self.assertEqual(ok.returncode,0,ok.stderr)
        mark=self.root / 'ready'
        child=[sys.executable,'-c',f'from pathlib import Path; Path({str(mark)!r}).write_text("ready"); import time; time.sleep(8)']
        parent=subprocess.Popen(self.args+['run','--',*child],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+3
            while not mark.exists() and time.monotonic()<deadline:time.sleep(.03)
            self.assertTrue(mark.exists())
            (self.cg / 'memory.current').write_text(str(700 * MIB))
            out,err=parent.communicate(timeout=5)
            self.assertEqual(parent.returncode,124,(out,err))
            self.assertIn('guard_stop',out)
        finally:
            if parent.poll() is None: parent.kill(); parent.wait()

    def test_guard_does_not_leave_ignoring_grandchild_running(self):
        pidfile=self.root/'grandchild.pid'
        grandchild='import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(8)'
        parent_code=(f'import subprocess,time; from pathlib import Path; '
                     f'p=subprocess.Popen([{sys.executable!r},"-c",{grandchild!r}]); '
                     f'Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(8)')
        runner=subprocess.Popen(self.args+['run','--',sys.executable,'-c',parent_code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        child_pid=None
        try:
            deadline=time.monotonic()+3
            while not pidfile.exists() and time.monotonic()<deadline: time.sleep(.03)
            self.assertTrue(pidfile.exists())
            child_pid=int(pidfile.read_text())
            (self.cg/'memory.current').write_text(str(700*MIB))
            runner.communicate(timeout=6)
            self.assertEqual(runner.returncode,124)
            time.sleep(.1)
            try:
                state=Path(f'/proc/{child_pid}/stat').read_text().split()[2]
            except FileNotFoundError:
                state='gone'
            self.assertIn(state, ('gone','Z'), f'grandchild {child_pid} still running: {state}')
        finally:
            if runner.poll() is None: runner.kill(); runner.wait()
            if child_pid:
                try: os.kill(child_pid,signal.SIGKILL)
                except ProcessLookupError: pass
            runner.stdout.close(); runner.stderr.close()

    def test_second_daemon_exits_if_lock_taken(self):
        first=subprocess.Popen(self.args+['daemon','--interval','0.3'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+3
            lock=self.state.with_suffix('.lock')
            while not lock.exists() and time.monotonic()<deadline: time.sleep(.03)
            self.assertTrue(lock.exists())
            second=subprocess.run(self.args+['daemon','--once'],capture_output=True,text=True,timeout=4)
            self.assertEqual(second.returncode,4,(second.stdout,second.stderr))
        finally:
            if first.poll() is None: first.terminate()
            first.wait(timeout=4)
            first.stderr.close()

    def test_daemon_reports_new_and_cleared_alerts(self):
        (self.cg / 'memory.current').write_text(str(700 * MIB))
        first = subprocess.run(self.args + ['daemon', '--once'], capture_output=True, text=True, timeout=5)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_scan = [json.loads(line) for line in first.stdout.splitlines()][-1]
        self.assertIn('headroom < 384 MiB', first_scan['new_alerts'])
        self.assertIn('cgroup_usage >= 80%', first_scan['new_alerts'])
        (self.cg / 'memory.current').write_text(str(100 * MIB))
        second = subprocess.run(self.args + ['daemon', '--once'], capture_output=True, text=True, timeout=5)
        self.assertEqual(second.returncode, 0, second.stderr)
        second_scan = [json.loads(line) for line in second.stdout.splitlines()][-1]
        self.assertIn('headroom < 384 MiB', second_scan['cleared_alerts'])
        self.assertIn('cgroup_usage >= 80%', second_scan['cleared_alerts'])
        third = subprocess.run(self.args + ['daemon', '--once'], capture_output=True, text=True, timeout=5)
        self.assertEqual([json.loads(line) for line in third.stdout.splitlines()][-1]['cleared_alerts'], [])

    def test_preflight_rejects_unknown_disk(self):
        missing = self.root / 'missing-root'
        args = [sys.executable, str(SCRIPT), '--root', str(missing), '--cgroup', str(self.cg),
                '--meminfo', str(self.mem), 'preflight']
        run = subprocess.run(args, capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn('disk_free_unavailable', json.loads(run.stdout)['policy']['reasons'])

    def test_run_stops_when_memory_telemetry_disappears(self):
        pidfile = self.root / 'telemetry-child.pid'
        code = (f'from pathlib import Path; import os,time; '
                f'Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)')
        runner = subprocess.Popen(self.args + ['run', '--', sys.executable, '-c', code],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child_pid = None
        try:
            deadline = time.monotonic() + 4
            while not pidfile.exists() and time.monotonic() < deadline: time.sleep(.03)
            self.assertTrue(pidfile.exists())
            child_pid = int(pidfile.read_text())
            (self.cg / 'memory.current').unlink()
            out, err = runner.communicate(timeout=8)
            self.assertEqual(runner.returncode, 125, (out, err))
            lines = [json.loads(line) for line in out.splitlines()]
            error = next(line for line in lines if line['status'] == 'monitoring_error')
            self.assertIn('memory_headroom_unavailable', error['reasons'])
            try:
                state = Path(f'/proc/{child_pid}/stat').read_text().split()[2]
            except FileNotFoundError:
                state = 'gone'
            self.assertIn(state, ('gone', 'Z'))
        finally:
            if runner.poll() is None: runner.kill(); runner.wait()
            if child_pid:
                try: os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError: pass
            runner.stdout.close(); runner.stderr.close()

    def test_sigterm_is_forwarded_and_task_does_not_outlive_guard(self):
        pidfile = self.root / 'signal-child.pid'
        received = self.root / 'signal-received'
        code = '\n'.join([
            'from pathlib import Path', 'import os, signal, sys, time',
            'def stop(signum, frame):',
            f'    Path({str(received)!r}).write_text("received")',
            '    sys.exit(0)',
            'signal.signal(signal.SIGTERM, stop)',
            f'Path({str(pidfile)!r}).write_text(str(os.getpid()))',
            'time.sleep(30)',
        ])
        runner = subprocess.Popen(self.args + ['run', '--', sys.executable, '-c', code],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child_pid = None
        try:
            deadline = time.monotonic() + 4
            while not pidfile.exists() and time.monotonic() < deadline: time.sleep(.03)
            self.assertTrue(pidfile.exists())
            child_pid = int(pidfile.read_text())
            runner.send_signal(signal.SIGTERM)
            out, err = runner.communicate(timeout=8)
            self.assertEqual(runner.returncode, 128 + signal.SIGTERM, (out, err))
            self.assertIn('interrupted', out)
            self.assertTrue(received.exists(), 'child did not receive SIGTERM')
            try:
                state = Path(f'/proc/{child_pid}/stat').read_text().split()[2]
            except FileNotFoundError:
                state = 'gone'
            self.assertIn(state, ('gone', 'Z'))
        finally:
            if runner.poll() is None: runner.kill(); runner.wait()
            if child_pid:
                try: os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError: pass
            runner.stdout.close(); runner.stderr.close()

    def test_guard_uses_sigkill_only_after_grace_period(self):
        pidfile = self.root / 'ignoring-child.pid'
        code = '\n'.join([
            'from pathlib import Path', 'import os, signal, time',
            'signal.signal(signal.SIGTERM, signal.SIG_IGN)',
            f'Path({str(pidfile)!r}).write_text(str(os.getpid()))',
            'time.sleep(30)',
        ])
        runner = subprocess.Popen(self.args + ['run', '--', sys.executable, '-c', code],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child_pid = None
        try:
            deadline = time.monotonic() + 4
            while not pidfile.exists() and time.monotonic() < deadline: time.sleep(.03)
            self.assertTrue(pidfile.exists())
            child_pid = int(pidfile.read_text())
            started = time.monotonic()
            (self.cg / 'memory.current').write_text(str(700 * MIB))
            out, err = runner.communicate(timeout=8)
            self.assertEqual(runner.returncode, 124, (out, err))
            self.assertGreaterEqual(time.monotonic() - started, 2.5)
            try:
                state = Path(f'/proc/{child_pid}/stat').read_text().split()[2]
            except FileNotFoundError:
                state = 'gone'
            self.assertIn(state, ('gone', 'Z'))
        finally:
            if runner.poll() is None: runner.kill(); runner.wait()
            if child_pid:
                try: os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError: pass
            runner.stdout.close(); runner.stderr.close()

    def test_descendants_receive_a_grace_period_before_sigkill(self):
        ready = self.root / 'grandchild-ready'
        finished = self.root / 'grandchild-finished'
        grandchild = '\n'.join([
            'from pathlib import Path', 'import signal, sys, time',
            'def stop(signum, frame):',
            '    time.sleep(0.5)',
            f'    Path({str(finished)!r}).write_text("finished")',
            '    sys.exit(0)',
            'signal.signal(signal.SIGTERM, stop)',
            f'Path({str(ready)!r}).write_text("ready")',
            'time.sleep(30)',
        ])
        parent_code = (f'import subprocess,time; '
                       f'subprocess.Popen([{sys.executable!r}, "-c", {grandchild!r}]); time.sleep(30)')
        runner = subprocess.Popen(self.args + ['run', '--', sys.executable, '-c', parent_code],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 4
            while not ready.exists() and time.monotonic() < deadline: time.sleep(.03)
            self.assertTrue(ready.exists())
            (self.cg / 'memory.current').write_text(str(700 * MIB))
            out, err = runner.communicate(timeout=8)
            self.assertEqual(runner.returncode, 124, (out, err))
            self.assertTrue(finished.exists(), 'descendant was killed before its grace period')
        finally:
            if runner.poll() is None: runner.kill(); runner.wait()
            runner.stdout.close(); runner.stderr.close()

    def test_background_descendants_are_not_left_after_leader_exits(self):
        ready = self.root / 'background-ready'
        cleaned = self.root / 'background-cleaned'
        grandchild = '\n'.join([
            'from pathlib import Path', 'import signal, sys, time',
            'def stop(signum, frame):',
            f'    Path({str(cleaned)!r}).write_text("stopped")',
            '    sys.exit(0)',
            'signal.signal(signal.SIGTERM, stop)',
            f'Path({str(ready)!r}).write_text("ready")',
            'time.sleep(30)',
        ])
        parent_code = '\n'.join([
            'from pathlib import Path', 'import subprocess, time',
            f'subprocess.Popen([{sys.executable!r}, "-c", {grandchild!r}])',
            'deadline = time.monotonic() + 4',
            f'while not Path({str(ready)!r}).exists() and time.monotonic() < deadline:',
            '    time.sleep(0.03)',
        ])
        run = subprocess.run(self.args + ['run', '--', sys.executable, '-c', parent_code],
                             capture_output=True, text=True, timeout=8)
        self.assertEqual(run.returncode, 0, (run.stdout, run.stderr))
        self.assertTrue(cleaned.exists(), 'background child was left without supervision')

    def test_invalid_interval_and_missing_command_are_reported(self):
        for interval in ('nan', 'inf', '0.1'):
            run = subprocess.run(self.args + ['daemon', '--interval', interval], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 2)
            self.assertIn('finite number', run.stderr)
        run = subprocess.run(self.args + ['run', '--', '/definitely/nonexistent/command'],
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 127)
        self.assertIn('command_not_found', run.stdout)

if __name__ == '__main__': unittest.main()
