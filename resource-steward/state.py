"""Small transactional local queue and event journal. No cloud credentials."""
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS queue (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, status TEXT NOT NULL, drive_id TEXT, verified_bytes INTEGER, checksum TEXT, updated REAL NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS active_alerts (key TEXT PRIMARY KEY)')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, _typ, _value, _tb):
        self.close()

    def queue(self, path, size, mtime_ns):
        path = str(Path(path).absolute())
        existing = self.db.execute('SELECT * FROM queue WHERE path=?', (path,)).fetchone()
        if existing and existing['size'] == size and existing['mtime_ns'] == mtime_ns and existing['status'] in ('pending','verified'):
            return False
        with self.db:
            self.db.execute('INSERT INTO queue (path,size,mtime_ns,status,drive_id,verified_bytes,checksum,updated) VALUES (?,?,?,\'pending\',NULL,NULL,NULL,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,status=\'pending\',drive_id=NULL,verified_bytes=NULL,checksum=NULL,updated=excluded.updated',
                            (path,size,mtime_ns,time.time()))
        return True

    def pending(self):
        rows = self.db.execute("SELECT * FROM queue WHERE status='pending' ORDER BY updated").fetchall()
        valid = []
        for row in rows:
            path = Path(row['path'])
            try:
                stat = path.stat()
                if path.is_symlink() or stat.st_size != row['size'] or stat.st_mtime_ns != row['mtime_ns']:
                    raise ValueError('source changed')
            except (OSError, ValueError):
                with self.db:
                    self.db.execute("UPDATE queue SET status='stale' WHERE path=?", (row['path'],))
                continue
            valid.append(dict(row))
        return valid

    def record_verified(self, path, drive_id, verified_bytes, checksum=None):
        if not drive_id or not str(drive_id).strip() or len(str(drive_id)) > 256:
            raise ValueError('real Drive file ID required')
        path = str(Path(path).absolute())
        row = self.db.execute('SELECT * FROM queue WHERE path=?', (path,)).fetchone()
        if not row or row['status'] != 'pending':
            raise ValueError('no pending artifact at this path')
        source = Path(path)
        try:
            stat = source.stat()
        except OSError as exc:
            raise ValueError('source missing') from exc
        if source.is_symlink() or (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime_ns']):
            raise ValueError('source changed since queueing')
        if row['size'] != verified_bytes:
            raise ValueError('remote size differs from queued local size')
        with self.db:
            self.db.execute("UPDATE queue SET status='verified', drive_id=?, verified_bytes=?, checksum=?, updated=? WHERE path=?",
                            (str(drive_id), verified_bytes, checksum, time.time(), path))
        return {'path': path, 'drive_file_id': str(drive_id), 'bytes': verified_bytes, 'status': 'verified'}

    def event(self, kind, details):
        with self.db:
            self.db.execute('INSERT INTO events (created,kind,details) VALUES (?,?,?)', (time.time(), str(kind)[:80], str(details)[:512]))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 200)')

    def event_count(self):
        return self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def update_alerts(self, keys):
        """Record alert transitions once, including recovery, in a bounded journal."""
        keys = set(keys)
        before = {row[0] for row in self.db.execute('SELECT key FROM active_alerts')}
        new = sorted(keys - before)
        cleared = sorted(before - keys)
        with self.db:
            for key in cleared:
                self.db.execute('DELETE FROM active_alerts WHERE key=?', (key,))
                self.db.execute('INSERT INTO events (created,kind,details) VALUES (?,?,?)',
                                (time.time(), 'alert_cleared', key[:512]))
            for key in new:
                self.db.execute('INSERT INTO active_alerts (key) VALUES (?)', (key,))
                self.db.execute('INSERT INTO events (created,kind,details) VALUES (?,?,?)',
                                (time.time(), 'alert', key[:512]))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 200)')
        return {'new': new, 'cleared': cleared}

    def set_alerts(self, keys):
        # Preserve the existing API for callers that only need newly raised alerts.
        return self.update_alerts(keys)['new']
