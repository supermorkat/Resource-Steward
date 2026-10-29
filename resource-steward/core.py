"""Read-only resource measurements and approximate Arena snapshot accounting."""
from pathlib import Path
import heapq
import os
import shutil
import stat

MIB = 1024 * 1024
EXCLUDED_DIRS = frozenset({
    '.arena', '.cache', '.local', '.mypy_cache', '.next', '.nox', '.npm', '.nuxt',
    '.output', '.parcel-cache', '.playwright-cli', '.pytest_cache', '.ruff_cache',
    '.svelte-kit', '.tox', '.turbo', '.venv', '.vite', '__pycache__', 'build',
    'coverage', 'dist', 'node_modules', 'out', 'target',
})
SECRET_NAMES = frozenset({'.git-credentials', '.netrc'})


def _candidates(root):
    root = Path(root).resolve()
    for base, dirs, names in os.walk(root, followlinks=False):
        kept = []
        for name in dirs:
            path = Path(base) / name
            if name not in EXCLUDED_DIRS and not path.is_symlink():
                kept.append(name)
        dirs[:] = kept
        for name in names:
            path = Path(base) / name
            rel = path.relative_to(root)
            if name in SECRET_NAMES or (len(rel.parts) >= 2 and rel.parts[-2] == '.git' and name in {'config', 'credentials'}):
                continue
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    continue
            except (OSError, ValueError):
                continue
            yield {'path': rel.as_posix(), 'bytes': info.st_size, 'mtime_ns': info.st_mtime_ns}


def audit(root):
    total = count = 0
    largest = []
    for item in _candidates(root):
        total += item['bytes']
        count += 1
        key = (item['bytes'], count, item)
        if len(largest) < 10:
            heapq.heappush(largest, key)
        elif key[:2] > largest[0][:2]:
            heapq.heapreplace(largest, key)
    return {'candidate_bytes': total, 'candidate_files': count,
            'largest': [entry for _size, _index, entry in sorted(largest, reverse=True)],
            'estimate_only': True}


def eligible_files(root, min_bytes):
    return sorted((entry for entry in _candidates(root) if entry['bytes'] >= min_bytes),
                  key=lambda entry: entry['path'])


def _read_number(path):
    try:
        raw = Path(path).read_text().strip()
        return None if raw == 'max' else int(raw)
    except (OSError, ValueError):
        return None


def health(cgroup, meminfo, root):
    cgroup, meminfo = Path(cgroup), Path(meminfo)
    limit = _read_number(cgroup / 'memory.max')
    current = _read_number(cgroup / 'memory.current')
    system_available = None
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith('MemAvailable:'):
                system_available = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    remaining = [x for x in (system_available, limit - current if limit is not None and current is not None else None) if x is not None]
    events = {}
    try:
        events = {key: int(value) for key, value in (line.split() for line in (cgroup / 'memory.events').read_text().splitlines())}
    except (OSError, ValueError):
        pass
    return {'limit_bytes': limit, 'current_bytes': current,
            'headroom_bytes': max(0, min(remaining)) if remaining else None,
            'disk_free_bytes': shutil.disk_usage(root).free, 'events': events}


def evaluate(report):
    a, h = report['audit'], report['health']
    size, count = a['candidate_bytes'], a['candidate_files']
    head = h.get('headroom_bytes')
    free = h.get('disk_free_bytes')
    stop = []
    warn = []
    if size >= 100_000_000: stop.append('workspace_candidates >= 100 MB')
    elif size >= 80_000_000: warn.append('workspace_candidates >= 80 MB')
    if count >= 9000: stop.append('workspace_files >= 9000')
    elif count >= 7000: warn.append('workspace_files >= 7000')
    if head is not None and head < 384 * MIB: stop.append('headroom < 384 MiB')
    elif head is not None and head < 512 * MIB: warn.append('headroom < 512 MiB')
    if free is not None and free < 2 * 1024**3: warn.append('disk_free < 2 GiB')
    if head is None: stop.append('memory_headroom_unavailable')
    return {'level': 'stop' if stop else ('warn' if warn else 'pass'), 'reasons': stop + warn}
