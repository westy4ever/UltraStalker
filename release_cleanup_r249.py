# -*- coding: utf-8 -*-
"""R249 one-time HDD layout cleanup.

Goals:
  * preserve posters, metadata/index, subtitles, picons/live assets, hero/user data;
  * remove only legacy backdrop masters/derivatives and start the final Q60
    backdrop cache from one top-level ``backdrops`` directory;
  * migrate title logos into one unversioned ``title_logo`` directory, then
    retire the old versioned title-logo trees.

The backdrop detach phase is intentionally fast (same-filesystem os.replace)
so it can run before UI artwork starts. Large deletion and title-logo migration
run off the UI thread.
"""
from __future__ import absolute_import

import os
import re
import shutil
import threading
import time

from .log import get_logger
from .persistent_cache import (
    ROOT, BACKDROPS, SOURCE_BACKDROPS, GENERATED, BLUE_CACHE, INDEX,
    TITLE_LOGOS, ensure_persistent_dirs, hdd_ready,
)

LOG = get_logger()
BACKDROP_TAG = "r249-clean-backdrop-layout-v1"
BACKDROP_MARKER = os.path.join(INDEX, ".%s.done" % BACKDROP_TAG)
TITLE_TAG = "r249-flat-title-logo-layout-v1"
TITLE_MARKER = os.path.join(INDEX, ".%s.done" % TITLE_TAG)

# Explicit top-level backdrop experiment/history roots only. User/library data
# is deliberately absent from this list.
_LEGACY_BACKDROP_ROOT_NAMES = (
    "cinematic_original_v196",
    "cinematic_w1280_test_v240",
    "cinematic_fullhd_q90_v243",
    "cinematic_fullhd_q85_v244",
    "cinematic_fullhd_q75_v245",
    "cinematic_fullhd_q75_v246",
    "cinematic_fullhd_q70_v247",
    "cinematic_fullhd_q60_v248",
)
_GENERATED_PREFIXES = (
    "cinematic_", "backdrop_grid_", "backdrop_grid2_",
    "us220_detail_", "us221_detail_", "us222_detail_",
    "us223_detail_", "us224_detail_", "us225_detail_",
)
_GENERATED_DIRS = (
    "cinematic",
    "cinematic_details_overview_v6532",
    "backdrop_grid_hud_v3",
    "backdrop_grid_hud_v4",
    "backdrop_grid2_hud_v1",
)
_BLUE_DIRS = (
    "cinematic_ready", "cinematic_fast",
    "backdrop_grid_ready", "backdrop_grid_fast",
    "three_view_fast_v151",
)


def _safe_remove(path):
    try:
        if os.path.islink(path) or os.path.isfile(path):
            os.unlink(path); return 1
        if os.path.isdir(path):
            shutil.rmtree(path); return 1
    except Exception as exc:
        LOG.warning("R249 cleanup could not remove %s: %s", path, exc)
    return 0


def _detach_dir(path, trash, recreate=False):
    path=os.path.abspath(str(path or ""))
    if not path:return 0
    parent=os.path.dirname(path)
    if parent and not os.path.isdir(parent):os.makedirs(parent,mode=0o700,exist_ok=True)
    if os.path.isdir(path):
        detached=os.path.join(parent,".%s.%s.%d.%d"%(
            os.path.basename(path),BACKDROP_TAG,os.getpid(),int(time.time()*1000)))
        try:
            os.replace(path,detached);trash.append(detached)
        except Exception:
            # Fail safe: never follow symlinks or widen deletion outside the exact path.
            _safe_remove(path)
    if recreate:os.makedirs(path,mode=0o700,exist_ok=True)
    return 1


def _background_backdrop_cleanup(trash):
    for path in list(trash or []):_safe_remove(path)
    try:
        if os.path.isdir(GENERATED):
            for name in os.listdir(GENERATED):
                if name in _GENERATED_DIRS or name.startswith(_GENERATED_PREFIXES):
                    _safe_remove(os.path.join(GENERATED,name))
    except Exception as exc:LOG.warning("R249 generated backdrop cleanup failed: %s",exc)
    try:
        for name in _BLUE_DIRS:_safe_remove(os.path.join(BLUE_CACHE,name))
    except Exception as exc:LOG.warning("R249 BLUE backdrop cleanup failed: %s",exc)
    # Retired Cinematic package trees from pre-flat-cache builds. Only their
    # cinematic children are removed; old library metadata itself is preserved.
    try:
        old_derived=os.path.join(ROOT,"derived")
        for name in ("cinematic_hybrid","cinematic_offline","cinematic"):
            _safe_remove(os.path.join(old_derived,name))
    except Exception as exc:LOG.warning("R249 derived cinematic cleanup failed: %s",exc)
    try:
        old_library=os.path.join(ROOT,"library")
        if os.path.isdir(old_library):
            for entry in os.scandir(old_library):
                if entry.is_dir(follow_symlinks=False):
                    _safe_remove(os.path.join(entry.path,"cinematic"))
    except Exception as exc:LOG.warning("R249 legacy library cinematic cleanup failed: %s",exc)


def prepare_backdrops_once():
    """Atomically retire all legacy backdrop storage and create one clean root."""
    result={"ready":False,"reset":False,"marker":BACKDROP_MARKER,"detached":0}
    try:
        if os.path.isfile(BACKDROP_MARKER):
            result.update({"ready":True,"already":True});return result
        if not hdd_ready(force=True):
            result["reason"]="hdd_not_ready";return result
        ensure_persistent_dirs(INDEX)
        trash=[]
        # The old canonical w1280 backdrop tree is intentionally reset too. Posters,
        # metadata/index, title logos, hero, live/picons and subtitles are untouched.
        result["detached"] += _detach_dir(BACKDROPS,trash,recreate=True)
        try:result["detached"] += _detach_dir(SOURCE_BACKDROPS,trash,recreate=True)
        except Exception as exc:LOG.warning("R249 source-backdrop detach failed: %s",exc)
        legacy_names=set(_LEGACY_BACKDROP_ROOT_NAMES)
        try:
            for name in os.listdir(ROOT):
                if re.match(r"^cinematic_(?:original|fullhd_q\d+|w1280).*_v?\d*$",str(name or ""),re.I):
                    legacy_names.add(name)
                elif re.match(r"^cinematic_fullhd_q\d+_v\d+$",str(name or ""),re.I):
                    legacy_names.add(name)
        except Exception:pass
        for name in sorted(legacy_names):
            path=os.path.join(ROOT,name)
            if os.path.abspath(path)==os.path.abspath(BACKDROPS):continue
            if os.path.exists(path):result["detached"] += _detach_dir(path,trash,recreate=False)
        tmp=BACKDROP_MARKER+".tmp.%d"%os.getpid()
        with open(tmp,"w",encoding="ascii") as h:h.write(BACKDROP_TAG+"\n")
        os.replace(tmp,BACKDROP_MARKER)
        result.update({"ready":True,"reset":True})
        if trash:
            t=threading.Thread(target=_background_backdrop_cleanup,args=(trash,),name="UltraR249BackdropCleanup")
            t.daemon=True;t.start()
        else:
            # Still clear presentation derivatives, but never on the UI thread.
            t=threading.Thread(target=_background_backdrop_cleanup,args=([],),name="UltraR249BackdropCleanup")
            t.daemon=True;t.start()
        LOG.info("R249 clean backdrop layout prepared: detached=%s root=%s",result["detached"],BACKDROPS)
        return result
    except Exception as exc:
        result["reason"]="error";result["error"]=str(exc)
        LOG.warning("R249 clean backdrop layout failed: %s",exc)
        return result


def _valid_logo(path):
    try:return bool(path and os.path.isfile(path) and os.path.getsize(path)>256)
    except Exception:return False


def _copy_logo(source,target):
    if _valid_logo(target):return True
    if not _valid_logo(source):return False
    try:
        parent=os.path.dirname(target)
        if parent and not os.path.isdir(parent):os.makedirs(parent,mode=0o700,exist_ok=True)
        tmp=target+".r249.%d.%d"%(os.getpid(),threading.get_ident())
        try:os.link(source,tmp)
        except Exception:shutil.copy2(source,tmp)
        os.replace(tmp,target)
        return _valid_logo(target)
    except Exception as exc:
        LOG.warning("R249 title-logo promote failed %s -> %s: %s",source,target,exc)
        try:
            if 'tmp' in locals() and os.path.exists(tmp):os.unlink(tmp)
        except Exception:pass
        return False


def _legacy_title_roots():
    rows=[]
    # Current pre-R249 canonical root comes first so it always wins collisions.
    preferred=os.path.join(ROOT,"title_logos")
    if os.path.isdir(preferred):rows.append(preferred)
    try:
        for name in os.listdir(ROOT):
            if not re.match(r"^title_logos?_v\d+$",str(name or ""),re.I):continue
            path=os.path.join(ROOT,name)
            if os.path.isdir(path) and path not in rows:rows.append(path)
    except Exception:pass
    return rows


def _iter_logo_files(root):
    # Within title_logos, newest ultra_vN lanes are preferred. Unversioned files
    # are included too. The destination itself is never traversed.
    if not root or not os.path.isdir(root):return []
    out=[]
    for dirpath,dirnames,filenames in os.walk(root):
        for name in filenames:
            if not str(name).lower().endswith(".png"):continue
            path=os.path.join(dirpath,name)
            rel=os.path.relpath(path,root)
            parts=rel.split(os.sep)
            version=0
            if parts and re.match(r"^ultra_v\d+$",parts[0],re.I):
                try:version=int(re.findall(r"\d+",parts[0])[-1])
                except Exception:version=0
                parts=parts[1:]
            if not parts:continue
            fallback=(parts[0].lower()=="fallback")
            basename=parts[-1]
            dest=os.path.join(TITLE_LOGOS,"fallback",basename) if fallback else os.path.join(TITLE_LOGOS,basename)
            out.append((version,path,dest))
    out.sort(key=lambda row:(-int(row[0]),str(row[1])))
    return out


def migrate_title_logos_once():
    result={"ready":False,"migrated":0,"skipped":0,"removed_roots":0,"marker":TITLE_MARKER}
    try:
        if os.path.isfile(TITLE_MARKER):
            result.update({"ready":True,"already":True});return result
        if not hdd_ready(force=True):
            result["reason"]="hdd_not_ready";return result
        ensure_persistent_dirs(INDEX,TITLE_LOGOS,os.path.join(TITLE_LOGOS,"fallback"))
        roots=_legacy_title_roots()
        for root in roots:
            for _version,source,target in _iter_logo_files(root):
                if _valid_logo(target):result["skipped"]+=1;continue
                if _copy_logo(source,target):result["migrated"]+=1
        # Only after promotion has completed do we retire old title-logo roots.
        for root in roots:
            result["removed_roots"] += _safe_remove(root)
        tmp=TITLE_MARKER+".tmp.%d"%os.getpid()
        with open(tmp,"w",encoding="ascii") as h:h.write(TITLE_TAG+"\n")
        os.replace(tmp,TITLE_MARKER)
        result["ready"]=True
        LOG.info("R249 title-logo migration complete: migrated=%s skipped=%s removed_roots=%s root=%s",
                 result["migrated"],result["skipped"],result["removed_roots"],TITLE_LOGOS)
        return result
    except Exception as exc:
        result["reason"]="error";result["error"]=str(exc)
        LOG.warning("R249 title-logo migration failed: %s",exc)
        return result


def schedule_title_logo_migration():
    """Run potentially-large logo migration off the Enigma2/UI thread."""
    try:
        if os.path.isfile(TITLE_MARKER):return None
    except Exception:pass
    t=threading.Thread(target=migrate_title_logos_once,name="UltraR249TitleLogoMigration")
    t.daemon=True;t.start();return t
