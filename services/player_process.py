# -*- coding: utf-8 -*-
"""Process and memory helpers for the UltraStalker player."""
from __future__ import absolute_import

import ctypes
import os
import signal

from ..log import optional_failure
from .player_runtime import _matching_external_player_pids, _descendant_pids


def process_rss_kb():
    try:
        with open("/proc/self/status", "r") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return int(parts[1]) if len(parts) > 1 else 0
    except Exception as exc:
        optional_failure("player.silent_guard", exc)
    return 0


def malloc_trim():
    try:
        libc = ctypes.CDLL(None)
        trim = getattr(libc, "malloc_trim", None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
    except Exception as exc:
        optional_failure("player.silent_guard", exc)


def force_session_silence(session, streamurl="", force=False, stop_native=True, exclude_pids=None, owned_pids=None):
    """Best-effort silence restricted to this stream and explicitly owned PIDs."""
    nav = getattr(session, "nav", None)
    if stop_native and nav is not None:
        try:
            nav.stopService()
        except Exception as exc:
            optional_failure("player.force_nav_stop", exc)
        try:
            pnav = getattr(nav, "pnav", None)
            stop = getattr(pnav, "stopService", None)
            if callable(stop):
                stop()
        except Exception as exc:
            optional_failure("player.force_pnav_stop", exc)
    sig = signal.SIGKILL if force else signal.SIGTERM
    excluded = set(int(x) for x in (exclude_pids or ()) if str(x).isdigit())
    owned = set(int(x) for x in (owned_pids or ()) if str(x).isdigit())
    targets = set(_matching_external_player_pids(streamurl)) | owned | _descendant_pids(owned)
    for pid in targets:
        if int(pid) in excluded:
            continue
        try:
            os.kill(pid, sig)
        except OSError:
            pass
        except Exception as exc:
            optional_failure("player.force_external_stop", exc)
    return True
