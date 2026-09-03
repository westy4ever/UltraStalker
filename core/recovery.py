# -*- coding: utf-8 -*-
"""Pure playback-recovery decisions, kept separate for regression testing."""

RUNTIME_MEDIA = ("itv", "live", "catchup")
SMART_MEDIA = ("itv", "live", "vod", "series", "episode", "catchup")


def runtime_interruption_action(media_type, smart_recovery, restored=False, failed=False, inflight=False, reconnects=0):
    if restored or failed or inflight:
        return "ignore"
    if str(media_type or "").lower() not in RUNTIME_MEDIA:
        return "ignore"
    if not smart_recovery:
        return "ignore"
    return "same_engine" if int(reconnects or 0) < 1 else "engine_fallback"


def smart_recovery_allowed(media_type, smart_recovery, inflight, attempts, max_retries, has_identity, has_item):
    if not smart_recovery or inflight:
        return False
    if str(media_type or "").lower() not in SMART_MEDIA:
        return False
    if int(attempts or 0) >= max(0, int(max_retries or 0)):
        return False
    return bool(has_identity and has_item)
