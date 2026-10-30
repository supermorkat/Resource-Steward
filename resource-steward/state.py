"""Small transactional local queue and event journal. No cloud credentials."""
from pathlib import Path
import sqlite3
import time


SCHEMA_VERSION = 1
EXPECTED_COLUMNS = {
    'queue': {'path', 'size', 'mtime_ns', 'status', 'drive_id',
              'verified_bytes', 'checksum', 'updated'},
    'events': {'id', 'created', 'kind', 'details'},
    'active_alerts': {'key'},
}


class UnsupportedSchemaVersion(RuntimeError):
    """The database was created by a newer version of Resource Steward."""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize_schema()
        except Exception:
            self.db.close()
            raise

    def _initialize_schema(self):
        # Explicit transaction makes legacy adoption atomic across connections.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise UnsupportedSchemaVersion(
                    f'state schema {version} is newer than supported {SCHEMA_VERSION}')
            if version == 0:
                tables = {row[0] for row in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables and not EXPECTED_COLUMNS.keys() <= tables:
                    raise sqlite3.DatabaseError('incomplete or unknown legacy state schema')
                self.db.execute('CREATE TABLE IF NOT EXISTS queue (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, status TEXT NOT NULL, drive_id TEXT, verified_bytes INTEGER, checksum TEXT, updated REAL NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS active_alerts (key TEXT PRIMARY KEY)')
            for table, expected in EXPECTED_COLUMNS.items():
                actual = {row['name'] for row in self.db.execute(f'PRAGMA table_info({table})')}
                if not expected <= actual:
                    raise sqlite3.DatabaseError(f'unsupported state schema: {table}')
            if version == 0:
                self.db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, _typ, _value, _tb):
        self.close()

    def queue(self, path, size, mtime_ns):
        path = str(Path(path).absolute())
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.db.execute('SELECT * FROM queue WHERE path=?', (path,)).fetchone()
            if existing and existing['size'] == size and existing['mtime_ns'] == mtime_ns and existing['status'] in ('pending','verified'):
                return False
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
                    self.db.execute("UPDATE queue SET status='stale' WHERE path=? AND status='pending' AND size=? AND mtime_ns=?",
                                    (row['path'], row['size'], row['mtime_ns']))
                continue
            valid.append(dict(row))
        return valid

    def record_verified(self, path, drive_id, verified_bytes, checksum=None):
        """Record an operator's remote-upload attestation; never contact Drive."""
        if not drive_id or not str(drive_id).strip() or len(str(drive_id)) > 256:
            raise ValueError('non-empty operator-supplied Drive file ID required')
        path = str(Path(path).absolute())
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
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
            self.db.execute("UPDATE queue SET status='verified', drive_id=?, verified_bytes=?, checksum=?, updated=? WHERE path=?",
                            (str(drive_id), verified_bytes, checksum, time.time(), path))
        # Keep the legacy status for existing databases/consumers. These fields
        # clarify that the tool has not independently verified the remote file.
        return {'path': path, 'drive_file_id': str(drive_id), 'bytes': verified_bytes,
                'status': 'verified', 'verification_source': 'operator',
                'remote_verified_by_tool': False}

    def event(self, kind, details):
        with self.db:
            self.db.execute('INSERT INTO events (created,kind,details) VALUES (?,?,?)', (time.time(), str(kind)[:80], str(details)[:512]))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 200)')

    def event_count(self):
        return self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def update_alerts(self, keys):
        """Record alert transitions once, including recovery, in a bounded journal."""
        keys = set(keys)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            before = {row[0] for row in self.db.execute('SELECT key FROM active_alerts')}
            new = sorted(keys - before)
            cleared = sorted(before - keys)
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
