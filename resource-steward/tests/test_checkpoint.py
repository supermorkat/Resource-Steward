import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'checkpointed_task.py'


class CheckpointTests(unittest.TestCase):
    def test_resume_after_interruption_without_repeating_completed_steps(self):
        if not EXAMPLE.is_file():
            self.skipTest('example not included in installed copy')
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / 'work'
            base = [sys.executable, str(EXAMPLE), '--workdir', str(work), '--steps', '5']
            first = subprocess.run(base + ['--interrupt-after', '2'], capture_output=True, text=True)
            self.assertEqual(first.returncode, 75)
            self.assertEqual(json.loads((work / 'progress.json').read_text())['completed'], 2)
            mtime = (work / 'step-0000.txt').stat().st_mtime_ns
            second = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((work / 'step-0000.txt').stat().st_mtime_ns, mtime)
            self.assertEqual(json.loads((work / 'progress.json').read_text())['completed'], 5)
            self.assertEqual(len(list(work.glob('step-*.txt'))), 5)
            mismatch = subprocess.run(base[:-1] + ['6'], capture_output=True, text=True)
            self.assertEqual(mismatch.returncode, 2)
            self.assertIn('incompatible checkpoint', mismatch.stderr)


if __name__ == '__main__': unittest.main()
