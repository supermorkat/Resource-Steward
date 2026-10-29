import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from state import Store


class StoreTests(unittest.TestCase):
    def test_queue_deduplicates_and_reopens_changed_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'part.bin'; p.write_bytes(b'123')
            with Store(Path(d) / 'state.sqlite') as store:
                stat = p.stat()
                store.queue(p, stat.st_size, stat.st_mtime_ns)
                store.queue(p, stat.st_size, stat.st_mtime_ns)
                self.assertEqual(len(store.pending()), 1)
                with self.assertRaises(ValueError): store.record_verified(p, '', 3, None)
                with self.assertRaises(ValueError): store.record_verified(p, 'real-drive-id', 4, None)
                store.record_verified(p, 'real-drive-id', 3, 'abcd')
                self.assertEqual(store.pending(), [])
                p.write_bytes(b'123456')
                stat = p.stat()
                store.queue(p, stat.st_size, stat.st_mtime_ns)
                self.assertEqual(store.pending()[0]['size'], 6)
                self.assertIsNone(store.pending()[0]['drive_id'])

    def test_changed_or_deleted_source_cannot_be_verified(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d) / 'part'; p.write_text('hi')
            with Store(Path(d)/'state.sqlite') as store:
                s=p.stat(); store.queue(p,s.st_size,s.st_mtime_ns)
                p.write_text('changed')
                self.assertEqual(store.pending(), [])
                with self.assertRaises(ValueError): store.record_verified(p,'id',2,None)

    def test_alerts_are_deduplicated_and_events_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(Path(d) / 'state.sqlite') as store:
                self.assertEqual(store.set_alerts({'workspace_high'}), ['workspace_high'])
                self.assertEqual(store.set_alerts({'workspace_high'}), [])
                store.set_alerts(set())
                self.assertEqual(store.set_alerts({'workspace_high'}), ['workspace_high'])
                for n in range(230): store.event('sample', str(n))
                self.assertLessEqual(store.event_count(), 200)

    def test_alert_recovery_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(Path(d) / 'state.sqlite') as store:
                self.assertEqual(store.update_alerts({'ram'}), {'new': ['ram'], 'cleared': []})
                self.assertEqual(store.update_alerts({'ram'}), {'new': [], 'cleared': []})
                self.assertEqual(store.update_alerts({'disk'}), {'new': ['disk'], 'cleared': ['ram']})
                self.assertEqual(store.update_alerts({'disk'}), {'new': [], 'cleared': []})
                events = [tuple(row) for row in store.db.execute('SELECT kind, details FROM events ORDER BY id')]
                self.assertEqual(events, [('alert', 'ram'), ('alert_cleared', 'ram'), ('alert', 'disk')])

if __name__ == '__main__': unittest.main()
