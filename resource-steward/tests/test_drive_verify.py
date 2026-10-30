import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_verify import verify_drive_binary
from state import Store
import steward


class FakeResponse:
    def __init__(self, payload):
        self.payload = io.BytesIO(json.dumps(payload).encode())

    def read(self, n=-1):
        return self.payload.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class DriveVerifyTests(unittest.TestCase):
    def test_provider_checksum_and_size_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'binary'; p.write_bytes(b'synthetic test')
            digest = hashlib.md5(p.read_bytes()).hexdigest()
            metadata = {'id': 'test-id', 'size': str(p.stat().st_size),
                        'md5Checksum': digest, 'trashed': False}
            with patch('drive_verify._open', return_value=FakeResponse(metadata)) as open_url:
                result = verify_drive_binary(p, 'test-id', 'dummy-token')
            self.assertEqual(result['size'], p.stat().st_size)
            self.assertEqual(result['md5'], digest)
            self.assertIn('www.googleapis.com/drive/v3/files/test-id', open_url.call_args.args[0].full_url)

    def test_missing_or_mismatched_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'binary'; p.write_bytes(b'original')
            for metadata in ({'id': 'test-id', 'size': '8', 'md5Checksum': '0' * 32},
                             {'id': 'test-id', 'size': '8'},
                             {'id': 'other', 'size': '8', 'md5Checksum': '0' * 32},
                             {'id': 'test-id', 'size': '8', 'md5Checksum': '0' * 32, 'trashed': True}):
                with self.subTest(metadata=metadata), patch('drive_verify._open', return_value=FakeResponse(metadata)):
                    with self.assertRaises(ValueError): verify_drive_binary(p, 'test-id', 'dummy-token')
            with patch('drive_verify._open', return_value=FakeResponse(['not', 'metadata'])):
                with self.assertRaisesRegex(ValueError, 'valid size'):
                    verify_drive_binary(p, 'test-id', 'dummy-token')
            with self.assertRaises(ValueError): verify_drive_binary(p, '../bad', 'dummy-token')
            with self.assertRaises(ValueError): verify_drive_binary(p, 'test-id', '')

    def test_cli_records_provider_check_but_manual_entry_stays_operator(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = root / 'file'; p.write_bytes(b'synthetic')
            db = root / 'state.sqlite'
            with Store(db) as store:
                stat = p.stat(); store.queue(p, stat.st_size, stat.st_mtime_ns)
            metadata = {'id': 'test-id', 'size': str(p.stat().st_size),
                        'md5Checksum': hashlib.md5(p.read_bytes()).hexdigest()}
            stdout = io.StringIO()
            args = ['--root', str(root), '--state', str(db), 'verify-drive-upload',
                    '--path', str(p), '--drive-id', 'test-id']
            with patch.dict(os.environ, {'RESOURCE_STEWARD_DRIVE_TOKEN': 'dummy-token'}), \
                 patch('drive_verify._open', return_value=FakeResponse(metadata)), \
                 contextlib.redirect_stdout(stdout):
                code = steward.main(args)
            self.assertEqual(code, 0, stdout.getvalue())
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['verification_source'], 'remote_checksum_checked')
            self.assertIs(result['remote_verified_by_tool'], True)
            with Store(db) as store:
                row = store.db.execute('SELECT verification_source,checksum FROM queue').fetchone()
                self.assertEqual(row['verification_source'], 'remote_checksum_checked')
                self.assertEqual(row['checksum'], metadata['md5Checksum'])

    def test_mismatch_leaves_queue_pending(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = root / 'file'; p.write_bytes(b'synthetic')
            db = root / 'state.sqlite'
            with Store(db) as store:
                stat = p.stat(); store.queue(p, stat.st_size, stat.st_mtime_ns)
            bad = {'id': 'test-id', 'size': str(p.stat().st_size), 'md5Checksum': '0'*32}
            stdout = io.StringIO()
            args = ['--root', str(root), '--state', str(db), 'verify-drive-upload',
                    '--path', str(p), '--drive-id', 'test-id']
            with patch.dict(os.environ, {'RESOURCE_STEWARD_DRIVE_TOKEN': 'dummy-token'}), \
                 patch('drive_verify._open', return_value=FakeResponse(bad)), \
                 contextlib.redirect_stdout(stdout):
                code = steward.main(args)
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'error')
            with Store(db) as store:
                self.assertEqual(len(store.pending()), 1)


if __name__ == '__main__': unittest.main()
