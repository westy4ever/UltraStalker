# -*- coding: utf-8 -*-
"""Disk-capacity guardrails for long-running downloads."""
from __future__ import absolute_import

import os
import shutil

DEFAULT_RESERVE_MB = 1024
MIN_RESERVE_MB = 256
MAX_RESERVE_MB = 8192
CHECK_INTERVAL_BYTES = 4 * 1024 * 1024


def reserve_bytes(settings=None):
    value = DEFAULT_RESERVE_MB
    if isinstance(settings, dict):
        try:
            value = int(settings.get("download_reserve_mb", DEFAULT_RESERVE_MB))
        except (TypeError, ValueError):
            value = DEFAULT_RESERVE_MB
    value = max(MIN_RESERVE_MB, min(MAX_RESERVE_MB, value))
    return value * 1024 * 1024


def free_bytes(path):
    target = os.path.abspath(path or ".")
    while not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    return int(shutil.disk_usage(target).free)


def ensure_capacity(path, incoming_bytes=0, reserve=0):
    """Raise before a write could consume the receiver's safety reserve."""
    reserve = max(0, int(reserve or 0))
    incoming = max(0, int(incoming_bytes or 0))
    free = free_bytes(path)
    required = reserve + incoming
    if free < required:
        raise RuntimeError(
            "Insufficient disk space: %.1f MB free; %.1f MB required including safety reserve"
            % (free / 1048576.0, required / 1048576.0)
        )
    return free
