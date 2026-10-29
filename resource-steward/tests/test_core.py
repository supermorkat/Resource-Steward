import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import audit, eligible_files, health, evaluate


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
            self.assertEqual(evaluate({'health': h, 'audit': {'candidate_bytes': 0, 'candidate_files': 0}})['level'], 'pass')
            (cg / 'memory.current').write_text(str(1750 * 2**20))
            h = health(cg, mem, root)
            self.assertEqual(evaluate({'health': h, 'audit': {'candidate_bytes': 0, 'candidate_files': 0}})['level'], 'stop')

if __name__ == '__main__': unittest.main()
