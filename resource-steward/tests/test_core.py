import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import MIB, audit, eligible_files, health, evaluate, runtime_guard


class AuditTests(unittest.TestCase):
    def test_sparse_110mb_is_critical_without_allocating_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            big = root / 'data-عربي.bin'
            with big.open('wb') as f: f.truncate(110_000_000)
            (root / 'node_modules').mkdir()
            (root / 'node_modules' / 'ignored').write_bytes(b'x' * 500)
            (root / 'repo' / '.git').mkdir(parents=True)
            (root / 'repo' / '.git' / 'config').write_text('secret')
            (root / 'link').symlink_to(big)
            a = audit(root)
            self.assertEqual(a['candidate_bytes'], 110_000_000)
            self.assertEqual(a['candidate_files'], 1)
            self.assertEqual(evaluate({'audit': a, 'health': {'headroom_bytes': 600 * 2**20}})['level'], 'stop')
            self.assertEqual([x['path'] for x in eligible_files(root, 25_000_000)], ['data-عربي.bin'])

    def test_disappearing_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'keep').write_text('ok')
            self.assertEqual(audit(root)['candidate_files'], 1)

    def test_unknown_memory_headroom_refuses_heavy_run(self):
        r = {'audit': {'candidate_bytes':0,'candidate_files':0},
             'health': {'headroom_bytes':None, 'disk_free_bytes':3*2**30}}
        self.assertEqual(evaluate(r)['level'], 'stop')

    def test_headroom_uses_smaller_of_cgroup_and_host(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cg = root / 'cg'; cg.mkdir()
            (cg / 'memory.max').write_text(str(2 * 2**30))
            (cg / 'memory.current').write_text(str(1536 * 2**20))
            (cg / 'memory.events').write_text('oom_kill 0\n')
            mem = root / 'meminfo'
            mem.write_text('MemAvailable: 700000 kB\nSwapTotal: 0 kB\n')
            h = health(cg, mem, root)
            self.assertEqual(h['headroom_bytes'], 512 * 2**20)
            h['disk_free_bytes'] = 3 * 2**30  # isolate RAM policy from /tmp tmpfs fixture disk
            policy = evaluate({'health': h, 'audit': {'candidate_bytes': 0, 'candidate_files': 0}})
            self.assertEqual(policy['level'], 'warn')
            self.assertIn('cgroup_usage >= 70%', policy['reasons'])
            (cg / 'memory.current').write_text(str(1750 * 2**20))
            h = health(cg, mem, root)
            self.assertEqual(evaluate({'health': h, 'audit': {'candidate_bytes': 0, 'candidate_files': 0}})['level'], 'stop')

    def test_parent_cgroup_can_be_the_binding_limit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            parent = root / 'groups'; parent.mkdir()
            child = parent / 'child'; child.mkdir()
            (parent / 'memory.max').write_text(str(1200 * MIB))
            (parent / 'memory.current').write_text(str(900 * MIB))
            (child / 'memory.max').write_text('max')
            (child / 'memory.current').write_text(str(100 * MIB))
            mem = root / 'meminfo'; mem.write_text('MemAvailable: 3000000 kB\n')
            h = health(child, mem, root, cgroup_root=parent)
            self.assertEqual(h['headroom_bytes'], 300 * MIB)
            self.assertEqual(h['memory_usage_ratio'], 0.75)
            self.assertEqual(h['cgroups'][0]['limit_kind'], 'unlimited')
            self.assertEqual(len(h['cgroups']), 2)
            self.assertEqual(h['memory_telemetry_errors'], [])
            self.assertEqual(evaluate({'health': h, 'audit': {'candidate_bytes': 0, 'candidate_files': 0}})['level'], 'stop')

    def test_missing_or_invalid_cgroup_metrics_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cg = root / 'cg'; cg.mkdir()
            mem = root / 'meminfo'; mem.write_text('MemAvailable: 3000000 kB\n')
            (cg / 'memory.current').write_text(str(100 * MIB))
            for value in (None, 'invalid', '-1'):
                with self.subTest(value=value):
                    maximum = cg / 'memory.max'
                    if value is None:
                        maximum.unlink(missing_ok=True)
                    else:
                        maximum.write_text(value)
                    h = health(cg, mem, root)
                    self.assertIsNone(h['headroom_bytes'])
                    self.assertTrue(any(x.startswith('cgroup_limit_unavailable:') for x in h['memory_telemetry_errors']))
            (cg / 'memory.max').write_text(str(900 * MIB))
            (cg / 'memory.current').unlink()
            h = health(cg, mem, root)
            self.assertIsNone(h['headroom_bytes'])
            self.assertTrue(any(x.startswith('cgroup_current_unavailable:') for x in h['memory_telemetry_errors']))

    def test_missing_host_memory_and_wrong_cgroup_boundary_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cg = root / 'cg'; cg.mkdir()
            (cg / 'memory.max').write_text(str(900 * MIB))
            (cg / 'memory.current').write_text(str(100 * MIB))
            mem = root / 'meminfo'; mem.write_text('MemAvailable: malformed kB\n')
            h = health(cg, mem, root)
            self.assertIsNone(h['headroom_bytes'])
            self.assertIn('mem_available_unavailable', h['memory_telemetry_errors'])
            mem.write_text('MemAvailable: 3000000 kB\n')
            h = health(cg, mem, root, cgroup_root=root / 'other')
            self.assertIsNone(h['headroom_bytes'])
            self.assertIn('cgroup_outside_root', h['memory_telemetry_errors'])

    def test_unlimited_cgroup_uses_host_memory_when_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cg = root / 'cg'; cg.mkdir()
            (cg / 'memory.max').write_text('max')
            mem = root / 'meminfo'; mem.write_text('MemAvailable: 1048576 kB\n')
            h = health(cg, mem, root)
            self.assertEqual(h['headroom_bytes'], 1024 * MIB)
            self.assertIsNone(h['memory_usage_ratio'])
            self.assertEqual(h['memory_telemetry_errors'], [])

    def test_disk_error_and_low_disk_block_preflight(self):
        audit_report = {'candidate_bytes': 0, 'candidate_files': 0}
        for free, level, reason in ((None, 'stop', 'disk_free_unavailable'),
                                    (512 * MIB - 1, 'stop', 'disk_free < 512 MiB'),
                                    (512 * MIB, 'warn', 'disk_free < 2 GiB'),
                                    (2 * 1024 * MIB, 'pass', None)):
            with self.subTest(free=free):
                h = {'headroom_bytes': 1024 * MIB, 'disk_free_bytes': free}
                policy = evaluate({'health': h, 'audit': audit_report})
                self.assertEqual(policy['level'], level)
                if reason:
                    self.assertIn(reason, policy['reasons'])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cg = root / 'cg'; cg.mkdir()
            (cg / 'memory.max').write_text('max')
            mem = root / 'meminfo'; mem.write_text('MemAvailable: 3000000 kB\n')
            with patch('core.shutil.disk_usage', side_effect=OSError('inaccessible')):
                h = health(cg, mem, root)
            self.assertIsNone(h['disk_free_bytes'])
            self.assertIn('disk_free_unavailable', evaluate({'health': h, 'audit': audit_report})['reasons'])

    def test_cgroup_ratio_rejects_preflight_before_starting(self):
        a = {'candidate_bytes': 0, 'candidate_files': 0}
        h = {'headroom_bytes': 4 * 1024 * MIB, 'disk_free_bytes': 4 * 1024 * MIB,
             'memory_usage_ratio': 0.8}
        self.assertEqual(evaluate({'health': h, 'audit': a})['level'], 'stop')
        self.assertIn('cgroup_usage >= 80%', evaluate({'health': h, 'audit': a})['reasons'])
        h['memory_usage_ratio'] = 0.7
        self.assertEqual(evaluate({'health': h, 'audit': a})['level'], 'warn')

    def test_runtime_guard_classifies_limits_and_missing_metrics(self):
        good = {'headroom_bytes': 1000 * MIB, 'disk_free_bytes': 4 * 1024 * MIB,
                'memory_usage_ratio': 0.5}
        self.assertIsNone(runtime_guard(good))
        cases = [({'memory_usage_ratio': 0.8}, 'guard_stop', 'cgroup_usage >= 80%'),
                 ({'headroom_bytes': 256 * MIB - 1}, 'guard_stop', 'headroom < 256 MiB'),
                 ({'disk_free_bytes': 512 * MIB - 1}, 'guard_stop', 'disk_free < 512 MiB'),
                 ({'headroom_bytes': None}, 'monitoring_error', 'memory_headroom_unavailable'),
                 ({'disk_free_bytes': None}, 'monitoring_error', 'disk_free_unavailable')]
        for change, status, reason in cases:
            with self.subTest(change=change):
                result = runtime_guard({**good, **change})
                self.assertEqual(result['status'], status)
                self.assertIn(reason, result['reasons'])

if __name__ == '__main__': unittest.main()
