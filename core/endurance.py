# -*- coding: utf-8 -*-
"""Lightweight receiver endurance/resource guard.

The guard is intentionally process-local and privacy-safe: it records only
resource counters (RSS, virtual memory, file descriptors and threads).  It has
no Enigma2 imports, performs no network I/O and never records portal/user data.
"""
from __future__ import absolute_import

import os
import threading
import time


_DEFAULT_THRESHOLDS = {
    "rss_kb": 96 * 1024,   # suspicious growth from baseline: +96 MiB
    "fds": 32,             # suspicious descriptor growth
    "threads": 8,          # suspicious thread growth
}


def process_resources():
    """Return cheap Linux process counters suitable for Enigma2 receivers."""
    result = {
        "monotonic_s": round(time.monotonic(), 3),
        "threads": threading.active_count(),
    }
    try:
        with open('/proc/self/status', 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if line.startswith('VmRSS:'):
                    result['rss_kb'] = int(line.split()[1])
                elif line.startswith('VmSize:'):
                    result['vmsize_kb'] = int(line.split()[1])
                elif line.startswith('Threads:'):
                    result['threads'] = int(line.split()[1])
    except Exception:
        pass
    try:
        result['fds'] = len(os.listdir('/proc/self/fd'))
    except Exception:
        pass
    return result


class EnduranceMonitor(object):
    """Track baseline/high-water resource use and flag suspicious growth."""
    def __init__(self, sampler=None, thresholds=None):
        self._sampler = sampler or process_resources
        self._thresholds = dict(_DEFAULT_THRESHOLDS)
        if thresholds:
            self._thresholds.update(thresholds)
        self._lock = threading.RLock()
        self._baseline = None
        self._last = None
        self._peak = {}
        self._samples = 0
        self._started = time.time()

    @staticmethod
    def _counter(row, key):
        try:
            return max(0, int((row or {}).get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def reset(self):
        with self._lock:
            self._baseline = None
            self._last = None
            self._peak = {}
            self._samples = 0
            self._started = time.time()
        return self.observe('baseline')

    def observe(self, label='sample'):
        current = dict(self._sampler() or {})
        safe_label = str(label or 'sample')[:48]
        with self._lock:
            if self._baseline is None:
                self._baseline = dict(current)
            self._last = dict(current)
            self._samples += 1
            for key in ('rss_kb', 'vmsize_kb', 'fds', 'threads'):
                value = self._counter(current, key)
                if value:
                    self._peak[key] = max(self._counter(self._peak, key), value)
            return self._report_locked(safe_label)

    def _report_locked(self, label):
        baseline = self._baseline or {}
        current = self._last or {}
        delta = {}
        warnings = []
        for key in ('rss_kb', 'fds', 'threads'):
            start = self._counter(baseline, key)
            now = self._counter(current, key)
            change = now - start if start and now else 0
            delta[key] = change
            threshold = int(self._thresholds.get(key, 0) or 0)
            if threshold and change > threshold:
                warnings.append('%s+%s' % (key, change))
        return {
            'label': label,
            'samples': self._samples,
            'uptime_s': max(0, int(time.time() - self._started)),
            'baseline': {k: self._counter(baseline, k) for k in ('rss_kb', 'vmsize_kb', 'fds', 'threads')},
            'current': {k: self._counter(current, k) for k in ('rss_kb', 'vmsize_kb', 'fds', 'threads')},
            'peak': {k: self._counter(self._peak, k) for k in ('rss_kb', 'vmsize_kb', 'fds', 'threads')},
            'delta': delta,
            'warnings': warnings,
            'healthy': not warnings,
        }

    def snapshot(self, label='diagnostics'):
        return self.observe(label)


ENDURANCE = EnduranceMonitor()
ENDURANCE.observe('startup')
