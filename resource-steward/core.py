"""Read-only resource measurements and approximate Arena snapshot accounting."""
from pathlib import Path
import heapq
import os
import shutil
import stat

MIB = 1024 * 1024
GIB = 1024 * MIB
DISK_STOP_BYTES = 512 * MIB
DISK_WARN_BYTES = 2 * GIB
RUN_HEADROOM_STOP_BYTES = 256 * MIB
RUN_CGROUP_USAGE_STOP = 0.80

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
        number = int(Path(path).read_text().strip())
        return number if number >= 0 else None
    except (OSError, ValueError):
        return None


def _read_limit(path):
    """Distinguish an unlimited cgroup from an unreadable or invalid limit."""
    try:
        raw = Path(path).read_text().strip()
        if raw == 'max':
            return 'unlimited', None
        value = int(raw)
        if value >= 0:
            return 'limited', value
    except (OSError, ValueError):
        pass
    return 'unknown', None


def _cgroup_ancestors(cgroup, cgroup_root):
    """Return visible ancestors, never traversing beyond the selected boundary."""
    cgroup = Path(cgroup).resolve()
    system_root = Path('/sys/fs/cgroup').resolve()
    if cgroup_root is None:
        # Custom fixtures/paths inspect only their leaf unless a boundary is given.
        boundary = system_root if cgroup == system_root or system_root in cgroup.parents else cgroup
    else:
        boundary = Path(cgroup_root).resolve()
    if cgroup != boundary and boundary not in cgroup.parents:
        return [], ['cgroup_outside_root']
    paths = [cgroup]
    while paths[-1] != boundary:
        paths.append(paths[-1].parent)
    return paths, []


def health(cgroup, meminfo, root, cgroup_root=None):
    cgroup, meminfo = Path(cgroup).resolve(), Path(meminfo)
    groups, errors = _cgroup_ancestors(cgroup, cgroup_root)
    measurements, remaining, ratios = [], [], []

    for group in groups:
        limit_file = group / 'memory.max'
        # The actual cgroup-v2 mount root has no memory.max on some kernels.
        if group == Path('/sys/fs/cgroup').resolve() and not limit_file.exists():
            continue
        kind, limit = _read_limit(limit_file)
        current = _read_number(group / 'memory.current')
        measurements.append({'path': str(group), 'limit_kind': kind,
                             'limit_bytes': limit, 'current_bytes': current})
        if kind == 'unknown':
            errors.append('cgroup_limit_unavailable:' + str(group))
        elif kind == 'limited':
            if current is None:
                errors.append('cgroup_current_unavailable:' + str(group))
            else:
                remaining.append(max(0, limit - current))
                ratios.append(current / limit if limit else 1.0)

    host_available = None
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith('MemAvailable:'):
                amount = int(line.split()[1])
                if amount >= 0:
                    host_available = amount * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    if host_available is None:
        errors.append('mem_available_unavailable')
    else:
        remaining.append(host_available)

    leaf = next((entry for entry in measurements if entry['path'] == str(cgroup)), {})
    events = {}
    try:
        events = {key: int(value) for key, value in (line.split() for line in (cgroup / 'memory.events').read_text().splitlines())}
    except (OSError, ValueError):
        pass
    try:
        disk_free = shutil.disk_usage(root).free
    except OSError:
        disk_free = None

    return {'limit_bytes': leaf.get('limit_bytes'), 'current_bytes': leaf.get('current_bytes'),
            'headroom_bytes': min(remaining) if remaining and not errors else None,
            'host_available_bytes': host_available,
            'memory_usage_ratio': max(ratios) if ratios and not errors else None,
            'memory_telemetry_errors': errors, 'cgroups': measurements,
            'disk_free_bytes': disk_free, 'events': events}


def evaluate(report):
    a, h = report['audit'], report['health']
    size, count = a['candidate_bytes'], a['candidate_files']
    head = h.get('headroom_bytes')
    free = h.get('disk_free_bytes')
    ratio = h.get('memory_usage_ratio')
    stop = []
    warn = []
    if size >= 100_000_000: stop.append('workspace_candidates >= 100 MB')
    elif size >= 80_000_000: warn.append('workspace_candidates >= 80 MB')
    if count >= 9000: stop.append('workspace_files >= 9000')
    elif count >= 7000: warn.append('workspace_files >= 7000')
    if head is not None and head < 384 * MIB: stop.append('headroom < 384 MiB')
    elif head is not None and head < 512 * MIB: warn.append('headroom < 512 MiB')
    if head is None: stop.append('memory_headroom_unavailable')
    if ratio is not None and ratio >= RUN_CGROUP_USAGE_STOP: stop.append('cgroup_usage >= 80%')
    elif ratio is not None and ratio >= 0.70: warn.append('cgroup_usage >= 70%')
    if free is None: stop.append('disk_free_unavailable')
    elif free < DISK_STOP_BYTES: stop.append('disk_free < 512 MiB')
    elif free < DISK_WARN_BYTES: warn.append('disk_free < 2 GiB')
    return {'level': 'stop' if stop else ('warn' if warn else 'pass'), 'reasons': stop + warn}


def runtime_guard(h):
    """Classify runtime resource boundaries, separately from preflight policy."""
    head, free = h.get('headroom_bytes'), h.get('disk_free_bytes')
    missing = []
    if head is None: missing.append('memory_headroom_unavailable')
    if free is None: missing.append('disk_free_unavailable')
    if missing:
        return {'status': 'monitoring_error', 'reasons': missing}
    reasons = []
    ratio = h.get('memory_usage_ratio')
    if ratio is not None and ratio >= RUN_CGROUP_USAGE_STOP:
        reasons.append('cgroup_usage >= 80%')
    if head < RUN_HEADROOM_STOP_BYTES:
        reasons.append('headroom < 256 MiB')
    if free < DISK_STOP_BYTES:
        reasons.append('disk_free < 512 MiB')
    return {'status': 'guard_stop', 'reasons': reasons} if reasons else None
