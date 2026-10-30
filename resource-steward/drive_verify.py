"""Opt-in verification of a binary Drive file using provider metadata.

This module does not upload files or manage OAuth credentials. The caller passes
an access token from an environment variable; it is never written to SQLite.
"""
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the bearer token to a redirected host."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _open(request):
    return build_opener(_NoRedirect()).open(request, timeout=30)


_DRIVE_ID = re.compile(r'[A-Za-z0-9_-]{1,256}\Z')


def verify_drive_binary(path, drive_id, token):
    """Check Drive's reported size/MD5 against a stable local binary file.

    MD5 is used for accidental-integrity checking, not adversarial authenticity.
    Google Workspace-native documents do not provide binary MD5 and are rejected.
    """
    if not _DRIVE_ID.fullmatch(drive_id):
        raise ValueError('invalid Drive file ID')
    if not token or not token.strip():
        raise ValueError('RESOURCE_STEWARD_DRIVE_TOKEN is required')
    path = Path(path)
    if path.is_symlink():
        raise ValueError('source must not be a symlink')
    before = path.stat()
    md5 = hashlib.md5()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            md5.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('source changed while hashing')

    url = ('https://www.googleapis.com/drive/v3/files/' + quote(drive_id, safe='') +
           '?fields=id,size,md5Checksum,mimeType,trashed&supportsAllDrives=true')
    request = Request(url, headers={'Authorization': 'Bearer ' + token,
                                    'Accept': 'application/json'})
    try:
        with _open(request) as response:
            # A bounded response prevents an unexpected metadata body exhausting memory.
            body = response.read(65537)
    except HTTPError as exc:
        raise ValueError(f'Drive metadata request failed (HTTP {exc.code})') from exc
    except URLError as exc:
        raise ValueError('Drive metadata request failed (network error)') from exc
    if len(body) > 65536:
        raise ValueError('Drive metadata response too large')
    try:
        remote = json.loads(body)
        if not isinstance(remote, dict):
            raise ValueError('invalid metadata object')
        raw_size = remote['size']
        if isinstance(raw_size, bool) or not str(raw_size).isdigit():
            raise ValueError('invalid size')
        size = int(raw_size)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError('Drive metadata missing a valid size') from exc
    if remote.get('trashed') or remote.get('id') != drive_id:
        raise ValueError('Drive file ID mismatch or file is trashed')
    checksum = remote.get('md5Checksum')
    if not isinstance(checksum, str) or not re.fullmatch('[0-9a-fA-F]{32}', checksum):
        raise ValueError('Drive binary MD5 checksum unavailable')
    if size != before.st_size or checksum.lower() != md5.hexdigest():
        raise ValueError('Drive size or MD5 checksum differs from local file')
    return {'size': size, 'md5': md5.hexdigest(), 'local_mtime_ns': before.st_mtime_ns}
