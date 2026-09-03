# -*- coding: utf-8 -*-
"""Runtime-light player lifecycle barrier helpers.

This module intentionally has no Enigma2 imports so lifecycle invalidation can
be regression-tested on normal CPython.  It only mutates terminal async state;
visible playback/UI behavior remains owned by player.py.
"""
from __future__ import absolute_import

import queue


def begin_async_cleanup(player):
    """Invalidate asynchronous player work and release queued results.

    Returns True only for the first cleanup call.  Repeated calls are harmless.
    Workers may still finish blocked network calls, but their generation is stale
    and their cancellation event is set before result queues are drained.
    """
    if getattr(player, "_lifecycle_cleanup_done", False):
        return False

    player._lifecycle_cleanup_done = True
    player._closing_playback = True
    player._online_subtitle_generation = int(getattr(player, "_online_subtitle_generation", 0) or 0) + 1
    player._zap_switch_generation = int(getattr(player, "_zap_switch_generation", 0) or 0) + 1
    player._online_subtitle_searching = False
    player._zap_switch_inflight = False
    player._zap_switch_started_at = 0.0
    player._recovery_inflight = False

    for event_name in ("_online_subtitle_cancel", "_recovery_cancel"):
        event = getattr(player, event_name, None)
        if event is not None:
            try:
                event.set()
            except Exception:
                pass

    for queue_name in ("_online_subtitle_queue", "_recovery_queue", "_zap_switch_queue"):
        result_queue = getattr(player, queue_name, None)
        if result_queue is None:
            continue
        try:
            while True:
                result_queue.get_nowait()
        except queue.Empty:
            pass
        except Exception:
            pass

    return True
