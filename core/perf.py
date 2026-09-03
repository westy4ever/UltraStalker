# -*- coding: utf-8 -*-
"""Tiny, privacy-safe runtime performance counters.

Only operation names, durations and counts are retained. No portal URLs, MACs,
queries, tokens or titles are recorded.
"""
import threading
import time
from collections import defaultdict, deque


class PerformanceMetrics:
    def __init__(self, samples_per_metric=64):
        self._limit = max(8, int(samples_per_metric or 64))
        self._lock = threading.RLock()
        self._samples = defaultdict(lambda: deque(maxlen=self._limit))
        self._counts = defaultdict(int)

    def record(self, name, elapsed_ms):
        key = str(name or "unknown")[:80]
        try:
            value = max(0.0, float(elapsed_ms))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._counts[key] += 1
            self._samples[key].append(value)

    def increment(self, name, amount=1):
        key = str(name or "unknown")[:80]
        with self._lock:
            self._counts[key] += max(0, int(amount or 0))

    def timer(self, name):
        return _PerfTimer(self, name)

    def snapshot(self):
        with self._lock:
            result = {}
            keys = set(self._counts) | set(self._samples)
            for key in sorted(keys):
                values = list(self._samples.get(key, ()))
                row = {"count": int(self._counts.get(key, 0))}
                if values:
                    row.update({
                        "avg_ms": round(sum(values) / float(len(values)), 2),
                        "max_ms": round(max(values), 2),
                        "last_ms": round(values[-1], 2),
                        "samples": len(values),
                    })
                result[key] = row
            return result


class _PerfTimer:
    def __init__(self, metrics, name):
        self.metrics = metrics
        self.name = name
        self.started = None

    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.started is not None:
            self.metrics.record(self.name, (time.monotonic() - self.started) * 1000.0)
        return False


PERF = PerformanceMetrics()
