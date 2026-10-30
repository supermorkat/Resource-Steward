import json
import sqlite3
import threading
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
                recorded = store.record_verified(p, 'real-drive-id', 3, 'abcd')
                self.assertEqual(recorded['status'], 'verified')  # legacy stored status
                self.assertEqual(recorded['verification_source'], 'operator')
                self.assertIs(recorded['remote_verified_by_tool'], False)
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

    def test_legacy_database_is_versioned_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / 'state.sqlite'
            with sqlite3.connect(db_path) as db:
                # Schema written by releases before PRAGMA user_version existed.
                db.executescript("""
                    CREATE TABLE queue (path TEXT PRIMARY KEY, size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL, status TEXT NOT NULL, drive_id TEXT,
                        verified_bytes INTEGER, checksum TEXT, updated REAL NOT NULL);
                    CREATE TABLE events (id INTEGER PRIMARY KEY, created REAL NOT NULL,
                        kind TEXT NOT NULL, details TEXT NOT NULL);
                    CREATE TABLE active_alerts (key TEXT PRIMARY KEY);
                    INSERT INTO queue VALUES ('/old/file', 3, 123, 'verified', 'id', 3, NULL, 0);
                    INSERT INTO active_alerts VALUES ('disk');
                """)
            with Store(db_path) as store:
                self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 2)
                row = store.db.execute('SELECT drive_id,verification_source FROM queue').fetchone()
                self.assertEqual((row['drive_id'], row['verification_source']), ('id', 'operator'))
                self.assertEqual(store.db.execute('SELECT key FROM active_alerts').fetchone()[0], 'disk')
            with Store(db_path) as store:
                self.assertEqual(store.db.execute('SELECT count(*) FROM queue').fetchone()[0], 1)

    def test_v1_database_is_upgraded_to_v2_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / 'state.sqlite'
            with sqlite3.connect(db_path) as db:
                db.executescript("""
                    CREATE TABLE queue (path TEXT PRIMARY KEY, size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL, status TEXT NOT NULL, drive_id TEXT,
                        verified_bytes INTEGER, checksum TEXT, updated REAL NOT NULL);
                    CREATE TABLE events (id INTEGER PRIMARY KEY, created REAL NOT NULL,
                        kind TEXT NOT NULL, details TEXT NOT NULL);
                    CREATE TABLE active_alerts (key TEXT PRIMARY KEY);
                    INSERT INTO queue VALUES ('/old/file',3,123,'verified','id',3,NULL,0);
                    PRAGMA user_version=1;
                """)
            with Store(db_path) as store:
                self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 2)
                row = store.db.execute('SELECT status,verification_source FROM queue').fetchone()
                self.assertEqual(tuple(row), ('verified', 'operator'))

    def test_unknown_or_incomplete_schema_fails_without_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            future = Path(d) / 'future.sqlite'
            with sqlite3.connect(future) as db:
                db.execute('PRAGMA user_version = 99')
                db.execute('CREATE TABLE important (value TEXT)')
            with self.assertRaisesRegex(Exception, 'newer than supported'):
                Store(future)
            with sqlite3.connect(future) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 99)
                self.assertEqual(db.execute('SELECT count(*) FROM important').fetchone()[0], 0)
            partial = Path(d) / 'partial.sqlite'
            with sqlite3.connect(partial) as db:
                db.execute('CREATE TABLE queue (path TEXT)')
            with self.assertRaisesRegex(sqlite3.DatabaseError, 'incomplete'):
                Store(partial)
            with sqlite3.connect(partial) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(queue)')], ['path'])

    def test_alert_transition_rolls_back_on_journal_failure(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(Path(d) / 'state.sqlite') as store:
                store.db.execute("""CREATE TRIGGER fail_alert BEFORE INSERT ON events
                                  WHEN NEW.kind='alert' BEGIN SELECT RAISE(ABORT, 'journal failed'); END""")
                with self.assertRaises(sqlite3.DatabaseError):
                    store.update_alerts({'disk'})
                self.assertEqual(store.db.execute('SELECT count(*) FROM active_alerts').fetchone()[0], 0)
                self.assertEqual(store.event_count(), 0)
                store.db.execute('DROP TRIGGER fail_alert')
                self.assertEqual(store.update_alerts({'disk'})['new'], ['disk'])

    def test_stale_scan_cannot_overwrite_a_concurrent_requeue(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sample'; p.write_bytes(b'old')
            state = Path(d) / 'state.sqlite'
            with Store(state) as scanner, Store(state) as updater:
                old = p.stat()
                scanner.queue(p, old.st_size, old.st_mtime_ns)
                original_stat = Path.stat
                changed = False

                def requeue_during_scan(path, *args, **kwargs):
                    nonlocal changed
                    if path == p and not changed:
                        changed = True
                        p.write_bytes(b'new content')
                        new = original_stat(p)
                        updater.queue(p, new.st_size, new.st_mtime_ns)
                    return original_stat(path, *args, **kwargs)

                with patch('state.Path.stat', requeue_during_scan):
                    self.assertEqual(scanner.pending(), [])  # old row became invalid
                self.assertEqual(updater.pending()[0]['size'], len(b'new content'))
                self.assertEqual(updater.pending()[0]['status'], 'pending')

    def test_concurrent_stores_record_an_alert_once(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / 'state.sqlite'
            with Store(db_path):
                pass
            barrier = threading.Barrier(4)
            results = []
            errors = []

            def worker():
                try:
                    with Store(db_path) as store:
                        barrier.wait(timeout=5)
                        results.append(store.update_alerts({'ram'})['new'])
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=8)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(results.count(['ram']), 1)
            with Store(db_path) as store:
                self.assertEqual(store.event_count(), 1)
                self.assertEqual(store.db.execute('SELECT count(*) FROM active_alerts').fetchone()[0], 1)

if __name__ == '__main__': unittest.main()
