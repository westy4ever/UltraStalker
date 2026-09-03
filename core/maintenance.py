# -*- coding: utf-8 -*-
"""Bounded cache and runtime-health maintenance helpers.

This module deliberately has no Enigma2 imports so it can be exercised by CI
and support tooling on a normal Python host.
"""
from __future__ import absolute_import
import os
import shutil
import threading
import time

from .. import persistent_cache


def _iter_files(root):
    # Maintenance is intentionally conservative: if the real HDD disappears
    # at any point, stop scanning rather than falling through to a shadow
    # /media/hdd directory on receiver flash.
    if not persistent_cache.hdd_read_ready(force=True):
        return
    if not root or not os.path.isdir(root):
        return
    for base, _dirs, files in os.walk(root):
        if not persistent_cache.hdd_read_ready(force=True):
            return
        for name in files:
            if not persistent_cache.hdd_read_ready(force=True):
                return
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if os.path.isfile(path):
                yield path, st


def cache_stats():
    rows = {}
    total_files = 0
    total_bytes = 0
    if not persistent_cache.hdd_read_ready(force=True):
        empty={name:{"files":0,"bytes":0,"oldest":0,"newest":0} for name in ("posters","backdrops","generated","tmdb","portal_art","quality","index")}
        return {
            "root": persistent_cache.ROOT, "persistent": True,
            "files": 0, "bytes": 0, "categories": empty, "disk": {},
        }
    for name, root in (
        ("posters", persistent_cache.POSTERS),
        ("backdrops", persistent_cache.BACKDROPS),
        ("generated", persistent_cache.GENERATED),
        ("tmdb", persistent_cache.TMDB_META),
        ("portal_art", persistent_cache.PORTAL_ART),
        ("quality", persistent_cache.QUALITY),
        ("index", persistent_cache.INDEX),
    ):
        files = 0
        size = 0
        oldest = 0
        newest = 0
        for _path, st in _iter_files(root) or ():
            files += 1
            size += int(st.st_size)
            stamp = int(st.st_mtime)
            oldest = stamp if not oldest else min(oldest, stamp)
            newest = max(newest, stamp)
        rows[name] = {"files": files, "bytes": size, "oldest": oldest, "newest": newest}
        total_files += files
        total_bytes += size
    try:
        if not persistent_cache.hdd_read_ready(force=True):
            raise OSError("persistent HDD unavailable")
        usage = shutil.disk_usage(persistent_cache.ROOT)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except Exception:
        disk = {}
    return {
        "root": persistent_cache.ROOT,
        "persistent": persistent_cache.ROOT.startswith("/media/hdd/"),
        "files": total_files,
        "bytes": total_bytes,
        "categories": rows,
        "disk": disk,
    }


def prune_cache(max_bytes, expired_age_seconds=None):
    """LRU-prune reproducible cache data to ``max_bytes``.

    Detail/index metadata participates too; user configuration/history is never
    touched. Returns exact before/after counters for diagnostics/UI.
    """
    max_bytes = max(0, int(max_bytes or 0))
    if not persistent_cache.hdd_read_ready(force=True):
        return {"before_files":0,"before_bytes":0,"removed_files":0,"removed_bytes":0,"after_bytes":0,"limit_bytes":max_bytes}
    now = time.time()
    entries = []
    total = 0
    for root in persistent_cache.ALL_DIRS[1:]:
        for path, st in _iter_files(root) or ():
            # Keep migration/manifest controls outside child dirs; everything
            # here is reproducible data and therefore safe to evict.
            total += int(st.st_size)
            entries.append((float(st.st_mtime), float(st.st_mtime), int(st.st_size), path))
    before_files = len(entries)
    before_bytes = total
    removed_files = 0
    removed_bytes = 0
    entries.sort(key=lambda row: (row[0], row[1]))
    for _atime, mtime, size, path in entries:
        expired = bool(expired_age_seconds is not None and now - mtime > int(expired_age_seconds))
        if not expired and total <= max_bytes:
            continue
        try:
            if not persistent_cache.persistent_write_gate(path):
                break
            os.unlink(path)
            total -= size
            removed_files += 1
            removed_bytes += size
        except OSError:
            pass
    return {
        "before_files": before_files,
        "before_bytes": before_bytes,
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "after_bytes": max(0, total),
        "limit_bytes": max_bytes,
    }


def runtime_health():
    """Privacy-safe process health useful for endurance tests/support bundles."""
    result = {"threads": threading.active_count()}
    try:
        result["loadavg"] = list(os.getloadavg())
    except Exception:
        pass
    try:
        # Linux receivers expose cheap process counters here; no psutil needed.
        with open('/proc/self/status', 'r', encoding='utf-8', errors='replace') as h:
            for line in h:
                if line.startswith(('VmRSS:', 'VmSize:', 'Threads:', 'FDSize:')):
                    key, value = line.split(':', 1)
                    result[key.lower()] = value.strip()
    except Exception:
        pass
    try:
        result['open_fds'] = len(os.listdir('/proc/self/fd'))
    except Exception:
        pass
    return result
