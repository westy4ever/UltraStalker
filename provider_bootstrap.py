# -*- coding: utf-8 -*-
"""Retired provider/server artwork bootstrap compatibility shim.

R269 permanently removes provider/server artwork as a no-TMDb fallback.
The module remains only so older call sites/upgrades can safely request cleanup
of a legacy provider_bootstrap tree without reviving that artwork authority.
"""
from __future__ import absolute_import

import os
import shutil
import threading
import time

from .log import optional_failure
from .persistent_cache import ROOT

BOOTSTRAP_ROOT = os.path.join(ROOT, "provider_bootstrap")
ACTIVE_ROOT = os.path.join(BOOTSTRAP_ROOT, "active")

_CLEAN_LOCK = threading.Lock()
_CLEAN_PENDING = False


def bootstrap_enabled():
    """Provider/server artwork fallback is permanently disabled."""
    return False


def invalidate_credential_state():
    return None


def load(profile, media_type, item):
    return {}


def save_local(profile, media_type, item, poster=None, backdrop=None):
    return {}


def ensure_provider_art(profile, media_type, item, client=None, downloader=None,
                        need_poster=True, need_backdrop=False, cancel_event=None):
    # Compatibility no-op. Never invokes provider transport/downloader.
    return {}


def _safe_delete(path):
    try:
        path=os.path.realpath(str(path or ""))
        root=os.path.realpath(str(ROOT or ""))
        if not path or not root or path==root or not path.startswith(root+os.sep):
            return False
        if os.path.islink(path) or os.path.ismount(path):
            return False
        if os.path.isdir(path):
            shutil.rmtree(path)
        return not os.path.exists(path)
    except Exception as exc:
        optional_failure("provider_bootstrap.retired_cleanup",exc)
        return False


def _schedule_cleanup():
    global _CLEAN_PENDING
    with _CLEAN_LOCK:
        if _CLEAN_PENDING:
            return False
        _CLEAN_PENDING=True
    def worker():
        global _CLEAN_PENDING
        try:
            _safe_delete(BOOTSTRAP_ROOT)
        finally:
            with _CLEAN_LOCK:
                _CLEAN_PENDING=False
    try:
        t=threading.Thread(target=worker,name="ultrastalker-provider-bootstrap-retired")
        t.daemon=True;t.start()
        return True
    except Exception as exc:
        with _CLEAN_LOCK:
            _CLEAN_PENDING=False
        optional_failure("provider_bootstrap.retired_submit",exc)
        return False


def detach_and_purge_async(reason="provider-bootstrap-retired"):
    existed=bool(os.path.isdir(BOOTSTRAP_ROOT) and not os.path.islink(BOOTSTRAP_ROOT))
    scheduled=_schedule_cleanup() if existed else False
    return {"detached": existed, "cleanup_scheduled": scheduled,
            "reason": str(reason or "provider-bootstrap-retired"),
            "bootstrap_enabled": False}


def reconcile_tmdb_state(force=False):
    """Purge legacy provider bootstrap data; never enable fallback again."""
    return detach_and_purge_async("provider-bootstrap-retired")
