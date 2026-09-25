# -*- coding: utf-8 -*-
"""One-time R95 backdrop-only cache reset.

Detaches only canonical/source backdrop files and backdrop-derived presentation
surfaces. Posters, title logos, metadata, ratings, favourites, Home hero and
other persistent content remain untouched.
"""
from __future__ import absolute_import

import json
import os
import shutil
import threading
import time

from .log import get_logger
from .persistent_cache import (
    BACKDROPS, SOURCE_BACKDROPS, GENERATED, BLUE_CACHE, INDEX,
    ensure_persistent_dirs, hdd_ready,
)

LOG = get_logger()
RESET_TAG = "r95-official-primary-backdrop-reset-v1"
MARKER = os.path.join(INDEX, ".%s.done" % RESET_TAG)

_GENERATED_PREFIXES = (
    "cinematic_",
    "backdrop_grid_",
    "backdrop_grid2_",
    "us220_detail_",
    "us221_detail_",
    "us222_detail_",
    "us223_detail_",
    "us224_detail_",
    "us225_detail_",
)
_GENERATED_DIRS = (
    "cinematic",
    "cinematic_details_overview_v6532",
    "backdrop_grid_hud_v3",
    "backdrop_grid_hud_v4",
    "backdrop_grid2_hud_v1",
)
_BLUE_DIRS = (
    "cinematic_ready",
    "cinematic_fast",
    "backdrop_grid_ready",
    "backdrop_grid_fast",
    "three_view_fast_v151",
)
_VISUAL_BACKDROP_KEYS = (
    "backdrop", "backdrop_present", "backdrop_present_source_fp",
    "manual_rescue_backdrop_locked", "backdrop_fast", "backdrop_source",
)


def _safe_remove(path):
    try:
        if os.path.islink(path) or os.path.isfile(path):
            os.unlink(path); return 1
        if os.path.isdir(path):
            shutil.rmtree(path); return 1
    except Exception as exc:
        LOG.warning("R95 backdrop reset could not remove %s: %s", path, exc)
    return 0


def _detach_dir(path, trash):
    """Atomically detach a cache directory and recreate an empty replacement."""
    path=os.path.abspath(str(path or ""))
    if not path:return (0, "")
    parent=os.path.dirname(path)
    if parent and not os.path.isdir(parent):os.makedirs(parent,mode=0o700,exist_ok=True)
    detached=""
    if os.path.isdir(path):
        detached=os.path.join(parent, ".%s.%s.%d.%d" % (
            os.path.basename(path), RESET_TAG, os.getpid(), int(time.time()*1000)))
        try:
            os.replace(path, detached)
        except Exception:
            # Cross-device should not happen within one parent, but fail safe.
            detached=""
            shutil.rmtree(path)
    os.makedirs(path,mode=0o700,exist_ok=True)
    if detached:trash.append(detached)
    return (1, detached)


def _scrub_visual_bundles():
    """Drop only backdrop-derived fields from per-content presentation manifests."""
    folder=os.path.join(GENERATED,"visual_bundles")
    changed=0
    if not os.path.isdir(folder):return 0
    for name in os.listdir(folder):
        if not name.endswith(".json"):continue
        path=os.path.join(folder,name)
        try:
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            if not isinstance(data,dict):continue
            dirty=False
            for key in _VISUAL_BACKDROP_KEYS:
                if key in data:
                    data.pop(key,None);dirty=True
            if not dirty:continue
            tmp=path+".r95tmp.%d"%os.getpid()
            with open(tmp,"w",encoding="utf-8") as h:
                json.dump(data,h,ensure_ascii=False,separators=(",",":"))
            os.replace(tmp,path);changed+=1
        except Exception as exc:
            LOG.warning("R95 visual bundle scrub failed %s: %s",path,exc)
    return changed


def _purge_derived():
    removed=0
    try:
        if os.path.isdir(GENERATED):
            for name in os.listdir(GENERATED):
                if name in _GENERATED_DIRS or name.startswith(_GENERATED_PREFIXES):
                    removed += _safe_remove(os.path.join(GENERATED,name))
        removed += _scrub_visual_bundles()
    except Exception as exc:
        LOG.warning("R95 generated backdrop reset failed: %s",exc)
    try:
        for name in _BLUE_DIRS:
            removed += _safe_remove(os.path.join(BLUE_CACHE,name))
    except Exception as exc:
        LOG.warning("R95 BLUE backdrop reset failed: %s",exc)
    return removed


def _background_delete(paths):
    for path in list(paths or []):
        _safe_remove(path)


def reset_once():
    """Detach all old backdrop masters once, preserving every non-backdrop cache."""
    result={"ready":False,"reset":False,"detached":0,"derived":0,"marker":MARKER}
    try:
        if os.path.isfile(MARKER):
            result.update({"ready":True,"already":True});return result
        if not hdd_ready(force=True):
            result["reason"]="hdd_not_ready";return result
        ensure_persistent_dirs(INDEX,BACKDROPS)
        trash=[]
        result["detached"] += _detach_dir(BACKDROPS,trash)[0]
        # SOURCE_BACKDROPS is volatile but must be detached too or a stale source
        # can immediately repopulate the freshly-empty canonical store.
        try:
            result["detached"] += _detach_dir(SOURCE_BACKDROPS,trash)[0]
        except Exception as exc:
            LOG.warning("R95 source backdrop detach failed: %s",exc)
        result["derived"]=_purge_derived()

        tmp=MARKER+".tmp.%d"%os.getpid()
        with open(tmp,"w",encoding="ascii") as h:
            h.write(RESET_TAG+"\n")
        os.replace(tmp,MARKER)
        result.update({"ready":True,"reset":True})

        if trash:
            t=threading.Thread(target=_background_delete,args=(trash,),name="UltraBackdropResetCleanup")
            t.daemon=True;t.start()
        LOG.info("R95 backdrop-only reset complete: detached=%s derived=%s",result["detached"],result["derived"])
        return result
    except Exception as exc:
        result["reason"]="error";result["error"]=str(exc)
        LOG.warning("R95 backdrop-only reset failed: %s",exc)
        return result
