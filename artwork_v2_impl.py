# -*- coding: utf-8 -*-
"""Ultra Stalker v2 external artwork pipeline.

Global VOD/Series artwork policy:
  one flat HDD master store -> TMDB canonical receiver-sized master -> provider rescue ONLY
  for a component TMDB genuinely does not have. Existing TMDB masters are immutable.
All screens consume the same poster/backdrop files from the same HDD store.

The module owns identity-to-artwork resolution and persistent image files.
Metadata enrichment remains compatible with the existing details UI, while
image downloading/caching no longer depends on the legacy artwork pipeline.
"""
from __future__ import absolute_import
import json
import os
import re
import difflib
import hashlib
import threading
import time
import urllib.request
import urllib.parse
import shutil
from collections import OrderedDict
try:
    from PIL import Image as _PILImage, ImageOps as _PILImageOps, ImageFilter as _PILImageFilter
except Exception:
    _PILImage = None
    _PILImageOps = None
    _PILImageFilter = None

from .tmdb import TMDBClient, _query_variants, _score, _year, _country_hint, _latin_heavy, _norm
from .fanart import fetch_artwork as _fanart_fetch, FanartError
from .storage import load_api_keys as _load_api_keys
from .title_clean import tmdb_search_title, tmdb_search_aliases, catalogue_title
from .persistent_cache import ROOT, POSTERS, BACKDROPS, INDEX, LOOKUP, LOGS, GENERATED, content_cache_key, hdd_ready, hdd_read_ready, ensure_persistent_dirs, persistent_write_gate
from .netsec import build_safe_https_media_opener
from .log import redact as _redact_log_value
from .core.image_budget import image_budgeted
from .core.executor import LazyThreadPoolExecutor
from .log import diagnostic_failure
from .media_library import item_paths as _library_paths, load as _library_load, save as _library_save, ensure_adaptive as _library_adaptive, load_alias as _library_load_alias, save_alias as _library_save_alias, metadata_complete as _library_complete, item_write_lock as _library_item_lock

# Test78 fixed flat artwork store. No per-title directories are created.
ART_ROOT = ROOT
POSTER_ROOT = POSTERS
BACKDROP_ROOT = BACKDROPS
MANIFEST_ROOT = INDEX
V3_ROOT = ROOT
V3_MOVIE_ROOT = POSTERS
V3_TV_ROOT = POSTERS
BACKDROP_PENDING_ROOT = INDEX
MANUAL_RESCUE_ROOT = ROOT
LEGACY_CONTENT_ART_ROOTS = (os.path.join(ROOT, "generated", "content_art"), os.path.join(GENERATED, "content_art"))
ART_LOG_ROOT = LOGS
ART_LOG_PATH = os.path.join(ART_LOG_ROOT, "artwork.log")
ART_DIRS = (POSTERS, BACKDROPS, INDEX, ART_LOG_ROOT)
# Directories are created lazily by the central persistent write gate.
# Importing this module must remain completely free of /media/hdd I/O.

_SCHEMA = 12
_UA = "Ultra Stalker/10 TMDB-Artwork-Safe194"
_LOCK_GUARD = threading.RLock()
_RESOLVE_LOCKS_GUARD = threading.RLock()
_LOCKS = {}
_RESOLVE_LOCKS = {}
# R81: tiny RAM bridge for equivalent provider-row identities (for example
# ``id:123`` vs ``movie_id:123`` returned by category vs native search).
# It stores only already-local canonical snapshots and never owns image bytes.
_FAST_BRIDGE_RAM = OrderedDict()
_FAST_BRIDGE_LOCK = threading.RLock()
_FAST_BRIDGE_WRITE_PENDING = set()
_FAST_BRIDGE_LIMIT = 512
# First-visible artwork lanes are deliberately independent.  Receiver tests
# showed that a shared two-worker pool can still serialize poster/backdrop when
# metadata or cache work occupies one lane.  Keep one dedicated worker per
# visible component family, using the established local-cache behaviour where each
# image request owns its own Deferred.  Title-logo already has its own UI lane.
_FIRST_PAINT_POSTER_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-firstpaint-poster")
_FIRST_PAINT_BACKDROP_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-firstpaint-backdrop")

class _RefLockHandle(object):
    def __init__(self,pool,guard,key):
        self.pool=pool;self.guard=guard;self.key=key;self.lock=None
    def __enter__(self):
        with self.guard:
            entry=self.pool.get(self.key)
            if entry is None:
                entry=[threading.RLock(),0];self.pool[self.key]=entry
            entry[1]+=1;self.lock=entry[0]
        self.lock.acquire();return self.lock
    def __exit__(self,exc_type,exc,tb):
        try:self.lock.release()
        finally:
            with self.guard:
                entry=self.pool.get(self.key)
                if entry is not None:
                    entry[1]-=1
                    if entry[1]<=0:self.pool.pop(self.key,None)
        return False

def _resolve_dedup_lock(media_type,item):
    mt="tv" if str(media_type or "").lower() in ("series","tv","episode") else "movie"
    raw="%s|%s|%s"%(mt,_norm(_title(item)),str(_year_hint(item) or "")[:4])
    key=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
    return _RefLockHandle(_RESOLVE_LOCKS,_RESOLVE_LOCKS_GUARD,key)

def _write_ok(target):
    try:
        return bool(persistent_write_gate(target))
    except Exception:
        return False

def _write_require(target):
    if not _write_ok(target):
        raise OSError("persistent artwork cache is not write-ready")
    return True

_BACKDROP_RECOVERY_ACTIVE = set()
_BACKDROP_RECOVERY_LOCK = threading.RLock()
_BACKDROP_RECOVERY_QUEUE = None
_BACKDROP_RECOVERY_THREAD = None
_BACKDROP_RECOVERY_STOP = threading.Event()
BACKDROP_MIN_WIDTH = 1280
BACKDROP_TARGET_WIDTH = 1920
BACKDROP_TARGET_HEIGHT = 1080
# Final cache: one compact canonical poster master is enough for every Ultra Stalker
# view. 500px width covers the largest real poster presentation while avoiding
# multi-megabyte TMDB originals and expensive cold-grid downloads.
POSTER_TARGET_WIDTH = 342
POSTER_TARGET_HEIGHT = 513

def _pending_backdrop_path(media_type, tmdb_id):
    mt = "tv" if str(media_type or "").lower() == "tv" else "movie"
    return os.path.join(INDEX, "pending_backdrop_%s_%s.json" % (mt, int(tmdb_id)))

def _mark_backdrop_state(media_type, tmdb_id, state, **extra):
    if not hdd_ready():
        return False
    try:
        current = _load_canonical(media_type, tmdb_id)
        current["backdrop_state"] = str(state or "pending")
        current.update(extra or {})
        _save_canonical(media_type, tmdb_id, current)
        path = _pending_backdrop_path(media_type, tmdb_id)
        if state == "real":
            try:
                if os.path.exists(path) and _write_ok(path): os.unlink(path)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
        else:
            payload={"media_type":"tv" if str(media_type or "").lower()=="tv" else "movie", "tmdb_id":int(tmdb_id), "state":state, "updated_at":int(time.time())}
            payload.update(extra or {})
            _atomic_json(path,payload)
    except Exception as exc:
        _art_log("backdrop state failed %s/%s %s: %s" % (media_type,tmdb_id,state,exc))

def _ranked_backdrops(rows, lang="ar", limit=8):
    rows=[r for r in (rows or []) if isinstance(r,dict) and r.get("file_path")]
    rows=[r for r in rows if int(r.get("width") or 0) >= int(r.get("height") or 1)]
    def rank(r):
        iso=str(r.get("iso_639_1") or "").lower()
        # Language-neutral landscape art is preferred because it usually has no text.
        lang_bonus=4 if not iso else (3 if iso==lang else (2 if iso=="en" else 0))
        width=int(r.get("width") or 0); height=int(r.get("height") or 0)
        pixels=min(width*height, 16000000)
        return (lang_bonus, pixels, float(r.get("vote_average") or 0), int(r.get("vote_count") or 0))
    rows.sort(key=rank, reverse=True)
    out=[];seen=set()
    for row in rows:
        fp=str(row.get("file_path") or "")
        if not fp or fp in seen: continue
        seen.add(fp);out.append(row)
        if len(out)>=max(1,int(limit or 8)):break
    return out



def _all_tmdb_images(client, media_type, tmdb_id):
    """Fetch the complete TMDB image gallery without a language filter.

    TMDB only filters `/images` when a language is supplied. Backdrops are
    overwhelmingly language-neutral, so the V4 engine deliberately requests
    the unfiltered gallery and applies language preference locally. This avoids
    accidentally hiding valid landscape art for Arabic/foreign catalogues.
    """
    try:
        payload = client._get("/%s/%s/images" % (media_type, int(tmdb_id)), {})
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        _art_log("V4 images lookup failed %s/%s: %s" % (media_type, tmdb_id, exc))
        return {}


def _candidate_file_paths(rows, lang="ar", limit=12):
    return [str((r or {}).get("file_path") or "") for r in _ranked_backdrops(rows, lang, limit) if str((r or {}).get("file_path") or "")]


def _download_backdrop_safe(url, target, timeout=9.0, max_width=1920, max_height=1080):
    """Stream one receiver-sized TMDB JPEG straight to HDD.

    No Pillow, no RGB expansion, no resize, no background decode.  The only
    validation is JPEG magic + SOF dimensions parsed in pure Python.  This is
    intentionally conservative because repeated native backdrop decoding was
    correlated with watchdog/Enigma2 restarts without a Python crash log.
    """
    if _valid_backdrop(target):
        return target
    if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(target)):
        return None
    with _lock(target):
        if _valid_backdrop(target):
            return target
        temp = target + ".safe194.%d.%d" % (os.getpid(), threading.get_ident())
        try:
            parts = urllib.parse.urlsplit(str(url or ""))
            if parts.scheme.lower() != "https" or (parts.hostname or "").lower() != "image.tmdb.org":
                _art_log("SAFE194 blocked non-TMDB backdrop URL: %s" % url); return None
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA,
                "Accept": "image/jpeg",
                "Connection": "close",
            })
            opener = build_safe_https_media_opener(allowed_hosts=("image.tmdb.org","assets.fanart.tv"))
            total=0; head=b""
            with opener.open(req, timeout=max(5.0, min(float(timeout or 9.0), 12.0))) as response:
                status=int(getattr(response,"status",200) or 200)
                ctype=str(response.headers.get("Content-Type") or "").lower()
                if status < 200 or status >= 300: raise IOError("HTTP %s"%status)
                declared=int(response.headers.get("Content-Length") or 0)
                if declared and declared > 8*1024*1024: raise IOError("safe backdrop too large %s"%declared)
                _write_require(temp)
                with open(temp,"wb") as fh:
                    while True:
                        chunk=response.read(64*1024)
                        if not chunk: break
                        if not head: head=chunk[:16]
                        total += len(chunk)
                        if total > 8*1024*1024: raise IOError("safe backdrop exceeds 8MB")
                        fh.write(chunk)
                    fh.flush(); os.fsync(fh.fileno())
            if total <= 1024: raise IOError("safe backdrop too small %s"%total)
            if not head.startswith(b"\xff\xd8\xff"):
                raise IOError("safe backdrop is not JPEG content-type=%s head=%r"%(ctype,head[:8]))
            w,h=_jpeg_dimensions_no_decode(temp)
            if w < 1200 or h < 600 or w < h:
                raise IOError("safe backdrop invalid dimensions %sx%s"%(w,h))
            if int(max_width or 0) > 0 and w > int(max_width):
                raise IOError("safe backdrop width %s exceeds %s"%(w,max_width))
            if int(max_height or 0) > 0 and h > int(max_height):
                raise IOError("safe backdrop height %s exceeds %s"%(h,max_height))
            _write_require(target);os.replace(temp,target);_fsync_parent(target)
            try:
                old_marker=target+".e2backdrop"
                if os.path.exists(old_marker) and _write_ok(old_marker): os.unlink(old_marker)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
            _art_log("SAFE194 OK %s -> %s (%d bytes %sx%s direct JPEG)"%(url,target,total,w,h))
            return target
        except Exception as exc:
            _art_log("SAFE194 FAIL %s -> %s : %s"%(url,target,exc))
            try:
                if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
            return None


def _download_landscape_path(file_path, target, source_label="gallery"):
    """Receiver-sized TMDB backdrop fetch without runtime image decoding.

    Performance Lab 19 policy: never request TMDB ``original`` artwork during
    browsing/cache hydration. Full-HD Enigma2 screens do not benefit from
    decoding a larger source, so the canonical backdrop is fetched directly at
    w1280 and streamed to HDD. This preserves the existing safe JPEG path while
    avoiding oversized network transfers and transient/native decode pressure.
    """
    fp=str(file_path or "")
    if not fp:return None, None, None
    attempts=(("w1280",9.0,1920,1080),)
    for size,tout,mw,mh in attempts:
        url=TMDBClient.image_url(fp,size)
        if not url:continue
        local=_download_backdrop_safe(url,target,tout,max_width=mw,max_height=mh)
        if local:
            return local,url,"%s:%s"%(source_label,size)
    return None,None,None


def _tv_episode_still_paths(client, tmdb_id, details, limit=10):
    """Use TMDB episode stills when a TV title genuinely has no series backdrop."""
    seasons=[]
    for row in ((details or {}).get("seasons") or []):
        if not isinstance(row,dict):continue
        try:num=int(row.get("season_number"))
        except Exception:continue
        if num < 0:continue
        seasons.append((0 if num==1 else 1, num, int(row.get("episode_count") or 0)))
    seasons.sort()
    out=[]
    for _prio,num,_count in seasons[:3]:
        try:
            payload=client._get("/tv/%s/season/%s"%(int(tmdb_id),num), {"language":"en-US"})
        except Exception as exc:
            _art_log("V4 season still lookup failed tv/%s season=%s: %s"%(tmdb_id,num,exc));continue
        for ep in (payload.get("episodes") or []):
            if not isinstance(ep,dict):continue
            fp=str(ep.get("still_path") or "")
            if fp and fp not in out:out.append(fp)
            if len(out)>=limit:return out
    return out


def _movie_collection_backdrops(client, details, lang="ar", limit=8):
    coll=(details or {}).get("belongs_to_collection")
    if not isinstance(coll,dict) or not coll.get("id"):return []
    try:
        payload=client._get("/collection/%s/images"%int(coll.get("id")), {})
    except Exception as exc:
        _art_log("V4 collection images failed collection/%s: %s"%(coll.get("id"),exc));return []
    return _candidate_file_paths(payload.get("backdrops"),lang,limit)


@image_budgeted
def _build_cinematic_poster_fallback(poster_path, target):
    """Last-resort visible background when TMDB has no landscape artwork at all.

    This is intentionally recognisable artwork, unlike the old 8x5 colour smear.
    It remains marked synthetic in the manifest and can never block a future
    real TMDB backdrop/still from replacing it.
    """
    if _PILImage is None or not _valid(poster_path):return None
    temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        with _PILImage.open(poster_path) as im:
            try:
                if _PILImageOps is not None: im=_PILImageOps.exif_transpose(im)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
            im=im.convert("RGB")
            tw,th=1920,1080
            res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            # Large softly blurred cover layer.
            scale=max(float(tw)/max(1,im.width),float(th)/max(1,im.height))
            bg=im.resize((max(1,int(im.width*scale)),max(1,int(im.height*scale))),res)
            left=max(0,(bg.width-tw)//2); top=max(0,(bg.height-th)//2)
            bg=bg.crop((left,top,left+tw,top+th))
            if _PILImageFilter is not None:bg=bg.filter(_PILImageFilter.GaussianBlur(18))
            bg=bg.convert("RGBA")
            bg=_PILImage.alpha_composite(bg,_PILImage.new("RGBA",(tw,th),(2,7,12,105)))
            # Ghosted sharp poster on the right half gives the eye real artwork.
            fg=im.copy(); fg.thumbnail((610,980),res)
            layer=_PILImage.new("RGBA",(tw,th),(0,0,0,0)); x=tw-fg.width-130; y=max(20,(th-fg.height)//2)
            rgba=fg.convert("RGBA"); rgba.putalpha(78)
            layer.alpha_composite(rgba,(x,y))
            out=_PILImage.alpha_composite(bg,layer).convert("RGB")
            _write_require(temp);out.save(temp,"JPEG",quality=91,optimize=False,progressive=False)
        _write_require(target);os.replace(temp,target);_fsync_parent(target)
        return target if _valid(target) else None
    except Exception as exc:
        _art_log("V4 synthetic fallback failed %s: %s"%(poster_path,exc))
        try:
            if os.path.exists(temp) and _write_ok(temp):os.unlink(temp)
        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
        return None


def _resolve_backdrop_v4(client, language, media_type, tmdb_id, details, images, poster_local, target, cancel_event=None):
    """TMDb primary backdrop first; gallery only when Details has no primary."""
    def cancelled():return bool(cancel_event is not None and cancel_event.is_set())
    lang=str(language or "ar-EG").split("-")[0].lower()
    default=str((details or {}).get("backdrop_path") or "").strip()
    if default and not cancelled():
        local,url,src=_download_landscape_path(default,target,"tmdb_primary")
        if local:return local,default,url,"tmdb_primary:w1280"
        # A temporary download failure must never silently switch the artwork.
        return None,None,None,None
    # Strict fallback only for titles where TMDb Details has no primary path.
    attempted=[]
    for fp in _candidate_file_paths((images or {}).get("backdrops"),lang,14):
        if cancelled():return None,None,None,None
        if fp in attempted:continue
        attempted.append(fp)
        local,url,src=_download_landscape_path(fp,target,"gallery_fallback")
        if local:return local,fp,url,"tmdb_gallery_fallback:w1280"
        if len(attempted)>=8:break
    return None,None,None,None

def _ensure_backdrop_recovery_worker():
    # Beta56: retired. Backdrop recovery is attempted inline by ArtworkV2
    # while already inside the bounded global hydration lane.
    return False

def _schedule_backdrop_recovery(credential, language, media_type, tmdb_id):
    # Keep the title pending so a later selected/visible/Blue hydration can retry.
    # Never create a hidden third TMDB network worker.
    return False

def shutdown_artwork_workers(wait=False, timeout=2.5):
    for _executor in (_FIRST_PAINT_POSTER_EXECUTOR, _FIRST_PAINT_BACKDROP_EXECUTOR):
        try:
            _executor.shutdown(wait=bool(wait),cancel_futures=True)
        except Exception:
            pass
    return True

def _lock(key):
    return _RefLockHandle(_LOCKS,_LOCK_GUARD,str(key))


def _valid(path, min_bytes=1024):
    try:
        if path and os.path.abspath(str(path)).startswith(os.path.abspath(ROOT) + os.sep) and not hdd_read_ready():
            return False
        if not path or not os.path.isfile(path) or os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as h: head = h.read(16)
        return bool(head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))
    except Exception:
        return False


def _canonical_dir(media_type, tmdb_id, create=False):
    # Compatibility helper only. Canonical artwork is flat under POSTERS/BACKDROPS.
    if create:ensure_persistent_dirs(POSTERS,BACKDROPS,INDEX)
    return ROOT


def _canonical_paths(media_type, tmdb_id):
    return _library_paths(media_type, tmdb_id)


def _copy_art_once(source, target):
    if _valid(target): return target
    if not _valid(source) or not hdd_ready(): return None
    try:
        if not ensure_persistent_dirs(os.path.dirname(target)): return None
        temp = target + ".migrate.%d.%d" % (os.getpid(), threading.get_ident())
        try:
            _write_require(temp)
            os.link(source, temp)
        except Exception:
            shutil.copy2(source, temp)
        _write_require(target);os.replace(temp, target);_fsync_parent(target)
        return target if _valid(target) else None
    except Exception as exc:
        _art_log("canonical promote failed %s -> %s : %s" % (source, target, exc))
        try:
            if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
        return None


def canonical_art_paths(media_type, tmdb_id):
    """Return mounted-HDD V3 paths for a verified TMDB identity.

    Returning no paths while /media/hdd is absent prevents callers from treating
    a rootfs shadow directory as persistent artwork.
    """
    if not tmdb_id or not hdd_read_ready():
        return {}
    return dict(_canonical_paths(media_type, tmdb_id))


def _load_canonical(media_type, tmdb_id):
    if not tmdb_id or not hdd_read_ready():return {}
    data=_library_load(media_type,tmdb_id) or {}
    paths=_canonical_paths(media_type,tmdb_id)
    try:
        bp=paths.get("backdrop")
        if bp and os.path.isfile(bp) and not _valid_backdrop(bp):
            if _write_ok(bp):os.unlink(bp)
            data["backdrop_local"]=None;data["backdrop_state"]="pending"
    except Exception as exc:_art_log("canonical backdrop validation failed %s/%s: %s"%(media_type,tmdb_id,exc))
    if paths.get("poster") and not _valid(paths["poster"]):data["poster_local"]=None
    return data


def _save_canonical(media_type, tmdb_id, data):
    return _library_save(media_type,tmdb_id,data or {})


def _promote_to_canonical(media_type, tmdb_id, data):
    if not tmdb_id: return dict(data or {})
    with _library_item_lock(media_type,tmdb_id):
        result = dict(data or {})
        paths = _canonical_paths(media_type, tmdb_id)
        p = result.get("poster_local")
        b = result.get("backdrop_local")
        cp = _copy_art_once(p, paths["poster"]) if _valid(p) else (paths["poster"] if _valid(paths["poster"]) else None)
        cb = _copy_art_once(b, paths["backdrop"]) if _valid_backdrop(b) else (paths["backdrop"] if _valid_backdrop(paths["backdrop"]) else None)
        if cp: result["poster_local"] = cp
        if cb: result["backdrop_local"] = cb
        _save_canonical(media_type, tmdb_id, result)
        return result



def _fsync_parent(path):
    try:
        fd=os.open(os.path.dirname(path) or ".",os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except Exception as exc:diagnostic_failure("artwork.fsync_parent",exc)

def _atomic_json(path, payload):
    if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(path)):
        return False
    temp = path + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
    try:
        _write_require(temp)
        with open(temp, "w", encoding="utf-8") as h:
            json.dump(payload, h, ensure_ascii=False, separators=(",", ":"))
            h.flush(); os.fsync(h.fileno())
        try: os.chmod(temp, 0o600)
        except OSError: pass
        _write_require(path);os.replace(temp, path);_fsync_parent(path)
        return True
    except Exception as exc:
        try:
            if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
        except OSError: pass
        try: _art_log("atomic json write failed %s: %s" % (path, exc))
        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
        return False


def _write_marker(path):
    """Race-safe private marker write used by artwork normalization."""
    temp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    try:
        _write_require(temp)
        with open(temp, "w", encoding="ascii") as h:
            h.write("1\n"); h.flush(); os.fsync(h.fileno())
        try: os.chmod(temp, 0o600)
        except OSError: pass
        _write_require(path); os.replace(temp, path);_fsync_parent(path)
        return True
    except Exception as exc:
        try:
            if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
        except OSError: pass
        try: _art_log("artwork marker write failed %s: %s" % (path, exc))
        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
        return False


def _manifest_path(profile, media_type, item):
    return os.path.join(MANIFEST_ROOT, content_cache_key(profile or {}, media_type, item or {}) + ".json")


def _manual_rescue_paths(profile, media_type, item):
    """Stable BLUE rescue paths in the same fixed flat HDD artwork store."""
    try:key=content_cache_key(profile or {},media_type,item or {})
    except Exception:key=""
    if not key:return {}
    return {
        "dir":ROOT,
        "poster":os.path.join(POSTERS,"fallback_%s.jpg"%key),
        "backdrop":os.path.join(BACKDROPS,"fallback_%s.jpg"%key),
        "manifest":os.path.join(INDEX,"fallback_%s.json"%key),
        "legacy_dir":"",
    }

def _adopt_legacy_manual_paths(paths):
    # Test110 clean break: the retired manual/content-art trees are never read or
    # promoted. Missing provider art is written directly into the global flat store.
    return paths


def _manual_owner_fingerprint(profile, media_type, item):
    """Provider/content ownership proof that never depends on TMDB/IMDb discovery."""
    row=item if isinstance(item,dict) else {}
    prof=profile if isinstance(profile,dict) else {}
    ids=[]
    for key in ("series_id","movie_id","stream_id","video_id","id","ch_id","cmd","command","url"):
        value=str(row.get(key) or "").strip()
        if value:ids.append([key,value[:320]])
    title=str(row.get("_raw_name") or row.get("name") or row.get("title") or row.get("series_name") or row.get("movie_name") or "").strip()
    title=re.sub(r"\s+"," ",title).casefold()[:320]
    year=str(row.get("year") or row.get("release_year") or row.get("release_date") or row.get("first_air_date") or "")[:10]
    raw=json.dumps([
        str(prof.get("portal") or "").rstrip("/").casefold(),
        str(prof.get("mac") or "").upper(),str(media_type or "").lower(),ids,title,year,
    ],ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()


def _manual_meta(paths):
    if not paths:return {}
    try:
        with open(paths.get("manifest"),"r",encoding="utf-8") as h:meta=json.load(h)
        return meta if isinstance(meta,dict) else {}
    except Exception:return {}


def _manual_old_source_safe(source):
    source=str(source or "").lower()
    return bool(source.startswith("provider_") or source.startswith("legacy_") or source.startswith("xtream_"))


def _manual_component_sources(meta):
    meta=meta if isinstance(meta,dict) else {}
    source=str(meta.get("source") or "blue")
    if int(meta.get("schema") or 0)>=66:
        return str(meta.get("poster_source") or source),str(meta.get("backdrop_source") or source)
    return source,source


def load_manual_rescue_art(profile, media_type, item):
    """Return BLUE-owned originals only when their per-item ownership is trustworthy.

    Test61-65 manifests had one shared ``source`` field.  A later synthetic
    backdrop could overwrite that field and erase the provenance of the poster,
    while older TMDB rescue bugs could persist a wrong title's artwork forever.
    Test66 therefore fails closed on old TMDB/synthetic manifests.  Provider and
    legacy-HDD entries remain readable; an explicit BLUE run can rebuild anything
    else under schema 66 with component-level provenance.
    """
    if not hdd_read_ready():return {}
    paths=_adopt_legacy_manual_paths(_manual_rescue_paths(profile,media_type,item))
    if not paths:return {}
    meta=_manual_meta(paths);schema=int(meta.get("schema") or 0)
    owner=_manual_owner_fingerprint(profile,media_type,item)
    if schema>=66:
        saved_owner=str(meta.get("owner_fp") or "")
        if not saved_owner or saved_owner!=owner:return {}
    elif meta and not _manual_old_source_safe(meta.get("source")):
        # Old tmdb_pair/poster_companion vaults are exactly where the cross-title
        # English poster/backdrop poisoning lived. Ignore them until BLUE repairs.
        return {}
    elif not meta:
        return {}
    poster_source,backdrop_source=_manual_component_sources(meta)
    out={}
    try:
        if _valid(paths.get("poster")):out["poster_local"]=paths.get("poster")
        # Hard visual rule: a synthetic/poster-companion image is never a real
        # cinematic backdrop. Old cached companions are ignored immediately.
        temporary_backdrop=bool(str(backdrop_source).startswith("poster_companion_"))
        if (not temporary_backdrop) and _valid_backdrop(paths.get("backdrop"),min_width=min(BACKDROP_MIN_WIDTH,1200)):
            out["backdrop_local"]=paths.get("backdrop")
        if out:
            out["manual_rescue_source"]=str(meta.get("source") or "blue")
            out["manual_rescue_poster_source"]=poster_source
            out["manual_rescue_backdrop_source"]=backdrop_source
            out["manual_rescue_backdrop_temporary"]=bool(str(backdrop_source).startswith("poster_companion_"))
            out["manual_rescue_updated_at"]=int(meta.get("updated_at") or 0)
            out["manual_rescue_backdrop_probe_at"]=int(meta.get("backdrop_probe_at") or 0)
            out["manual_rescue_backdrop_probe_sig"]=str(meta.get("backdrop_probe_sig") or "")
    except Exception as exc:_art_log("manual rescue load failed: %s"%exc)
    return out


def _manual_rescue_merge(profile, media_type, item, data):
    """Fill only absent canonical components from the manual/provider rescue vault.

    TMDB canonical files are authoritative. A BLUE/provider fallback must never
    shadow a real TMDB poster/backdrop that already exists in the shared store.
    """
    out=dict(data or {})
    rescue=load_manual_rescue_art(profile,media_type,item)
    p=str(rescue.get("poster_local") or "");b=str(rescue.get("backdrop_local") or "")
    canonical_p=str(out.get("poster_local") or "")
    canonical_b=str(out.get("backdrop_local") or "")
    if p and _valid(p) and not _valid(canonical_p):
        out["poster_local"]=p;out["manual_rescue_poster_locked"]=True
    if b and _valid_backdrop(b,min_width=min(BACKDROP_MIN_WIDTH,1200)) and not _valid_backdrop(canonical_b,min_width=min(BACKDROP_MIN_WIDTH,1200)):
        out["backdrop_local"]=b;out["manual_rescue_backdrop_locked"]=True
    for key in ("manual_rescue_source","manual_rescue_poster_source","manual_rescue_backdrop_source","manual_rescue_backdrop_temporary"):
        if key in rescue:out[key]=rescue.get(key)
    if rescue.get("manual_rescue_updated_at"):
        try:out["updated_at"]=max(int(out.get("updated_at") or 0),int(rescue.get("manual_rescue_updated_at") or 0))
        except Exception:pass
    return out


def _manual_safe_copy(source,target,landscape=False):
    """Normalize one source into the BLUE vault, never overwriting a hit."""
    source=str(source or "");target=str(target or "")
    if not source or not target or not _valid(source):return ""
    if _valid(target):return target
    try:
        if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(target)):return ""
        ok=False
        if landscape:
            try:ok=bool(_normalize_backdrop_jpeg(source,target))
            except Exception:ok=False
        else:
            try:ok=bool(_normalize_poster_jpeg(source,target))
            except Exception:ok=False
        if not ok:
            try:
                if os.path.exists(target) and _write_ok(target):os.unlink(target)
            except Exception:pass
            try:
                with open(source,"rb") as h:magic=h.read(2)
                safe_jpeg=bool(magic==b"\xff\xd8")
                if safe_jpeg and landscape:
                    w,h=_jpeg_dimensions_no_decode(source);safe_jpeg=bool(w>=1200 and h>=600 and w>=h)
                if safe_jpeg:
                    temp=target+".copy.%d.%d"%(os.getpid(),threading.get_ident())
                    _write_require(temp);shutil.copy2(source,temp);_write_require(target);os.replace(temp,target);_fsync_parent(target)
                    ok=_valid_backdrop(target,min_width=1200) if landscape else _valid(target)
            except Exception:ok=False
        return target if ok and (_valid_backdrop(target,min_width=1200) if landscape else _valid(target)) else ""
    except Exception as exc:
        _art_log("manual rescue copy failed %s -> %s: %s"%(source,target,exc));return ""


def _write_manual_meta(profile,media_type,item,paths,poster_source,backdrop_source,old=None,source="blue"):
    old=old if isinstance(old,dict) else {}
    owner=_manual_owner_fingerprint(profile,media_type,item)
    payload={
        "schema":66,"owner_fp":owner,
        "owner_title":str((item or {}).get("name") or (item or {}).get("title") or "")[:180],
        "source":str(source or "blue"),"updated_at":int(time.time()),
    }
    if _valid(paths.get("poster")):
        payload["poster_local"]=paths.get("poster");payload["poster_source"]=str(poster_source or source or "blue")
    if _valid_backdrop(paths.get("backdrop"),min_width=1200):
        payload["backdrop_local"]=paths.get("backdrop");payload["backdrop_source"]=str(backdrop_source or source or "blue")
        payload["temporary_backdrop"]=bool(str(payload["backdrop_source"]).startswith("poster_companion_"))
    for key in ("backdrop_probe_at","backdrop_probe_sig"):
        if old.get(key) not in (None,""):payload[key]=old.get(key)
    _atomic_json(paths.get("manifest"),payload)
    return payload


def save_manual_rescue_art(profile, media_type, item, poster=None, backdrop=None, source="blue"):
    """Persist only missing artwork during explicit BLUE Check Artwork.

    Schema 66 records poster and backdrop provenance separately. Saving a
    synthetic companion can no longer erase the fact that the poster came from
    the provider, and a later real backdrop upgrade changes only backdrop_source.
    """
    paths=_adopt_legacy_manual_paths(_manual_rescue_paths(profile,media_type,item))
    if not paths:return {}
    old=_manual_meta(paths);schema=int(old.get("schema") or 0)
    if schema>=66 and str(old.get("owner_fp") or "")!=_manual_owner_fingerprint(profile,media_type,item):
        return {}
    old_ps,old_bs=_manual_component_sources(old)
    existing_p=paths.get("poster") if _valid(paths.get("poster")) else ""
    existing_b=paths.get("backdrop") if _valid_backdrop(paths.get("backdrop"),min_width=1200) else ""
    wrote_p=False;wrote_b=False
    if poster and not existing_p:
        existing_p=_manual_safe_copy(poster,paths.get("poster"),False);wrote_p=bool(existing_p)
    if backdrop and not existing_b:
        existing_b=_manual_safe_copy(backdrop,paths.get("backdrop"),True);wrote_b=bool(existing_b)
    if not (existing_p or existing_b):return {}
    ps=(str(source or "blue") if wrote_p else old_ps)
    bs=(str(source or "blue") if wrote_b else old_bs)
    if schema<66 and _manual_old_source_safe(old.get("source")):
        ps=ps or str(old.get("source") or "legacy_blue");bs=bs or str(old.get("source") or "legacy_blue")
    summary=ps if ps and ps==bs else (bs if bs and not ps else (ps if ps and not bs else "mixed_blue66"))
    try:_write_manual_meta(profile,media_type,item,paths,ps,bs,old,summary or source)
    except Exception as exc:_art_log("manual rescue manifest failed: %s"%exc)
    return load_manual_rescue_art(profile,media_type,item)


def blue_quarantine_unsafe_manual_rescue(profile, media_type, item):
    """BLUE-only cleanup of Test61-65 vaults whose provenance cannot be trusted."""
    paths=_manual_rescue_paths(profile,media_type,item)
    if not paths or not hdd_ready():return False
    meta=_manual_meta(paths)
    if not meta:return False
    schema=int(meta.get("schema") or 0)
    owner=_manual_owner_fingerprint(profile,media_type,item)
    unsafe=False
    if schema>=66:
        unsafe=bool(str(meta.get("owner_fp") or "")!=owner)
    else:
        unsafe=not _manual_old_source_safe(meta.get("source"))
    if not unsafe:
        # Upgrade old provider/legacy provenance in place without touching bytes.
        if schema<66:
            ps,bs=_manual_component_sources(meta)
            try:_write_manual_meta(profile,media_type,item,paths,ps,bs,meta,str(meta.get("source") or "legacy_blue"))
            except Exception:pass
        return False
    changed=False
    for key in ("poster","backdrop","manifest"):
        path=paths.get(key)
        try:
            if path and os.path.isfile(path) and _write_ok(path):os.unlink(path);changed=True
        except Exception:pass
    for base in (paths.get("poster"),paths.get("backdrop")):
        for suffix in (".e2backdrop",".e2jpeg"):
            try:
                marker=str(base or "")+suffix
                if marker and os.path.isfile(marker) and _write_ok(marker):os.unlink(marker)
            except Exception:pass
    return changed


def blue_repair_legacy_unpaired_backdrop(profile, media_type, item):
    """Explicit-BLUE migration guard for Test61/62 detached TMDB backdrops."""
    paths=_manual_rescue_paths(profile,media_type,item)
    if not paths or not hdd_ready():return False
    meta=_manual_meta(paths)
    if not meta:return False
    source=str(meta.get("backdrop_source") or meta.get("source") or "")
    unsafe={"tmdb_blue_detached","tmdb_retry_blue_detached","tmdb_backdrop_blue_detached"}
    if source not in unsafe:return False
    b=paths.get("backdrop")
    if not _valid_backdrop(b,min_width=min(BACKDROP_MIN_WIDTH,1200)):return False
    try:
        if os.path.isfile(b) and _write_ok(b):os.unlink(b)
        ps,_=_manual_component_sources(meta)
        _write_manual_meta(profile,media_type,item,paths,ps,"",meta,"blue66_pair_repair_pending")
        return True
    except Exception as exc:
        _art_log("BLUE pair repair failed: %s"%exc);return False


def save_manual_poster_companion_backdrop(profile, media_type, item, poster=None, source="poster_companion_blue"):
    """Build a same-title temporary landscape companion from the locked poster."""
    paths=_adopt_legacy_manual_paths(_manual_rescue_paths(profile,media_type,item))
    if not paths:return {}
    current=load_manual_rescue_art(profile,media_type,item) or {}
    if _valid_backdrop(current.get("backdrop_local"),min_width=min(BACKDROP_MIN_WIDTH,1200)):
        return current
    p=str(poster or current.get("poster_local") or "")
    if not _valid(p) or not hdd_ready() or not ensure_persistent_dirs(paths.get("dir")):return current
    temp=os.path.join(paths.get("dir"),"poster_companion.tmp.jpg")
    try:
        built=_build_cinematic_poster_fallback(p,temp)
        if not _valid_backdrop(built,min_width=1200):return current
        return save_manual_rescue_art(profile,media_type,item,backdrop=built,source=source) or current
    finally:
        try:
            if os.path.isfile(temp) and _write_ok(temp):os.unlink(temp)
        except Exception:pass


def manual_rescue_backdrop_is_temporary(profile, media_type, item):
    paths=_manual_rescue_paths(profile,media_type,item)
    if not paths:return False
    meta=_manual_meta(paths);_,bs=_manual_component_sources(meta)
    return bool(str(bs or "").startswith("poster_companion_"))


def manual_rescue_backdrop_upgrade_due(profile, media_type, item, evidence_sig="", min_interval=21600):
    """A temporary backdrop is display-complete; re-probe only on new evidence/TTL."""
    if not manual_rescue_backdrop_is_temporary(profile,media_type,item):return False
    meta=_manual_meta(_manual_rescue_paths(profile,media_type,item))
    evidence_sig=str(evidence_sig or "")
    previous=str(meta.get("backdrop_probe_sig") or "")
    if evidence_sig and evidence_sig!=previous:return True
    try:last=int(meta.get("backdrop_probe_at") or meta.get("updated_at") or 0)
    except Exception:last=0
    return bool(not last or (int(time.time())-last)>=max(900,int(min_interval or 21600)))


def mark_manual_temporary_backdrop_probe(profile, media_type, item, evidence_sig=""):
    if not manual_rescue_backdrop_is_temporary(profile,media_type,item):return False
    paths=_manual_rescue_paths(profile,media_type,item);meta=_manual_meta(paths)
    if not paths or not meta:return False
    try:
        meta=dict(meta);meta["backdrop_probe_at"]=int(time.time());meta["backdrop_probe_sig"]=str(evidence_sig or "");meta["updated_at"]=int(time.time())
        _atomic_json(paths.get("manifest"),meta);return True
    except Exception:return False


def replace_manual_temporary_backdrop(profile, media_type, item, backdrop, source="blue_real_backdrop"):
    """BLUE-only promotion: replace a synthetic companion with verified real art."""
    if not manual_rescue_backdrop_is_temporary(profile,media_type,item):
        return load_manual_rescue_art(profile,media_type,item)
    backdrop=str(backdrop or "")
    if not _valid_backdrop(backdrop,min_width=min(BACKDROP_MIN_WIDTH,1200)):
        return load_manual_rescue_art(profile,media_type,item)
    paths=_manual_rescue_paths(profile,media_type,item)
    if not paths or not hdd_ready():return load_manual_rescue_art(profile,media_type,item)
    target=paths.get("backdrop")
    try:
        if os.path.isfile(target) and _write_ok(target):os.unlink(target)
        for marker in (target+".e2backdrop",target+".e2jpeg"):
            try:
                if os.path.isfile(marker) and _write_ok(marker):os.unlink(marker)
            except Exception:pass
        out=save_manual_rescue_art(profile,media_type,item,backdrop=backdrop,source=source)
        return out
    except Exception as exc:
        _art_log("temporary backdrop promotion failed: %s"%exc)
        return load_manual_rescue_art(profile,media_type,item)


def adopt_legacy_manual_rescue(profile, media_type, item, need_poster=True, need_backdrop=True):
    # Test110: legacy rescue storage is retired and ignored.
    return load_manual_rescue_art(profile,media_type,item)


def identity_cache_compatible(item, data):
    """Fail closed when a cached external identity does not independently match the catalogue item.

    A discovered TMDB/IMDb id is never allowed to validate itself.  Search-derived
    metadata must still agree with the provider title (and year when both exist).
    Structured ids that were already present on the provider item remain authoritative.
    """
    item=item if isinstance(item,dict) else {}
    data=data if isinstance(data,dict) else {}
    if not data or not data.get("tmdb_id"):
        return True

    # Independent provider ids are allowed to prove identity. Search may also
    # attach one internal marker after its own conservative title/year/media
    # check. The marker is presentation/cache-only and never changes playback.
    provider_tmdb=""
    for key in ("tmdb_id","tmdbid","tmdb","_provider_tmdb_id","_ultra_search_verified_tmdb_id"):
        value=str(item.get(key) or "").strip()
        if re.fullmatch(r"\d{1,12}",value):provider_tmdb=value;break
    cached_tmdb=str(data.get("tmdb_id") or "").strip()
    if provider_tmdb and cached_tmdb and provider_tmdb==cached_tmdb:
        return True
    provider_imdb=""
    for key in ("imdb_id","imdb","_provider_imdb_id"):
        value=str(item.get(key) or "").strip().lower()
        m=re.search(r"tt\d{5,12}",value)
        if m:provider_imdb=m.group(0);break
    cached_imdb=str(data.get("imdb_id") or "").strip().lower()
    if provider_imdb and cached_imdb and provider_imdb==cached_imdb:
        return True

    current_year=_year_hint(item)
    cached_year=None
    try:
        raw_year=str(data.get("year") or data.get("release_date") or data.get("first_air_date") or "")
        m=re.search(r"(?:19|20)\d{2}",raw_year);cached_year=int(m.group(0)) if m else None
    except Exception:
        cached_year=None
    if current_year and cached_year and abs(int(current_year)-int(cached_year))>=2:
        return False

    # Use only the provider's primary title fields for trust.  Search aliases are
    # useful for discovery, not for proving that a discovered identity is correct.
    current=[]
    for key in ("_raw_name","name","title","display_name","movie_name","series_name","original_name","original_title"):
        value=str(item.get(key) or "").strip()
        if not value:continue
        n=_norm(catalogue_title(value))
        if n and n not in current:current.append(n)
    stored=[]
    for key in ("identity_catalogue_title","title","original_title","name","original_name"):
        value=str(data.get(key) or "").strip()
        if not value:continue
        n=_norm(catalogue_title(value))
        if n and n not in stored:stored.append(n)
    if not current or not stored:
        return False

    def strong_title(a,b):
        if not a or not b:return False
        if a==b:return True
        at=[x for x in a.split() if len(x)>1];bt=[x for x in b.split() if len(x)>1]
        if not at or not bt:return False
        aset=set(at);bset=set(bt);common=aset & bset
        # Substring acceptance is intentionally strict: editorial suffixes are
        # okay, unrelated same-year shows are not.
        if min(len(a),len(b))>=8 and (a in b or b in a):
            return float(len(common))/float(max(1,min(len(aset),len(bset))))>=0.80
        token_ratio=float(len(common))/float(max(1,max(len(aset),len(bset))))
        char_ratio=difflib.SequenceMatcher(None,a,b).ratio()
        return bool(token_ratio>=0.75 and char_ratio>=0.78)

    title_ok=any(strong_title(c,st) for c in current for st in stored)
    if not title_ok:
        return False
    # A year match strengthens a title but never substitutes for one.
    return True

def _fast_bridge_ram_get(key):
    key=str(key or "")
    if not key:return {}
    with _FAST_BRIDGE_LOCK:
        row=_FAST_BRIDGE_RAM.get(key)
        if not isinstance(row,dict):return {}
        _FAST_BRIDGE_RAM.move_to_end(key)
        return dict(row)


def _fast_bridge_ram_put(key,row):
    key=str(key or "")
    if not key or not isinstance(row,dict) or not row:return
    with _FAST_BRIDGE_LOCK:
        _FAST_BRIDGE_RAM[key]=dict(row)
        _FAST_BRIDGE_RAM.move_to_end(key)
        while len(_FAST_BRIDGE_RAM)>_FAST_BRIDGE_LIMIT:
            _FAST_BRIDGE_RAM.popitem(last=False)


def _fast_pointer_snapshot(item,pointer,media_type,source="pointer"):
    """Validate one tiny item pointer and expose only already-local canonical art."""
    if not isinstance(pointer,dict):return {}
    try:
        tmdb_id=pointer.get("tmdb_id")
        if not tmdb_id or not identity_cache_compatible(item,pointer):return {}
        mt=str(pointer.get("media_type") or _media_type(media_type))
        paths=_canonical_paths(mt,tmdb_id)
        poster=str(paths.get("poster") or "")
        backdrop=str(paths.get("backdrop") or "")
        if not (poster and os.path.isfile(poster) and os.path.getsize(poster)>256):poster=""
        if not (backdrop and os.path.isfile(backdrop) and os.path.getsize(backdrop)>1024):backdrop=""
        if not poster and not backdrop:return {}
        row={
            "schema":pointer.get("schema"),"tmdb_id":int(tmdb_id),"media_type":mt,
            "title":pointer.get("title") or _title(item),"year":pointer.get("year"),
            "poster_local":poster,"backdrop_local":backdrop,
            "identity_catalogue_title":str(pointer.get("title") or _title(item) or ""),
            "identity_pointer_verified":True,
        }
        if source and source!="pointer":row["_fast_bridge_source"]=str(source)
        return row
    except Exception:return {}


def _fast_pointer_candidates(profile,media_type,item):
    """Equivalent strong-ID pointer files, bounded and title-verified on read.

    Native provider/category/search endpoints sometimes return the same title with
    different field names (``id`` vs ``movie_id`` / ``series_id``).  The stable
    content key intentionally chooses one strong field, so probe the tiny set of
    equivalent strong-ID encodings before doing any title alias work.
    """
    item=item if isinstance(item,dict) else {}
    mt=str(media_type or "").lower()
    current=_manifest_path(profile,media_type,item)
    out=[];seen={current}
    keys=("movie_id","id","video_id") if mt=="vod" else (("series_id","id","series_uid") if mt=="series" else ())
    values=[]
    for key in keys:
        value=item.get(key)
        # Field spelling is part of stable_content_identity, so id=123 and
        # movie_id=123 intentionally remain two candidate pointer keys.
        if value not in (None,""):values.append((key,value))
    # Exact fields already carried by the row.
    for chosen,value in values:
        row=dict(item)
        for key in keys:row.pop(key,None)
        row[chosen]=value
        try:path=_manifest_path(profile,media_type,row)
        except Exception:continue
        if path not in seen:seen.add(path);out.append(path)
    # Safe mirror of an authoritative generic id into the media-specific id (or
    # vice versa). A pointer is still accepted only after title/year compatibility,
    # so equal numeric namespaces cannot poison an unrelated title.
    generic=item.get("id")
    specific=item.get("movie_id") if mt=="vod" else (item.get("series_id") if mt=="series" else None)
    mirrors=[]
    if generic not in (None,"") and specific in (None,""):
        mirrors.append(("movie_id" if mt=="vod" else "series_id",generic))
    if specific not in (None,"") and generic in (None,""):
        mirrors.append(("id",specific))
    for chosen,value in mirrors:
        row=dict(item)
        for key in keys:row.pop(key,None)
        row[chosen]=value
        try:path=_manifest_path(profile,media_type,row)
        except Exception:continue
        if path not in seen:seen.add(path);out.append(path)
    return out[:4]


def _persist_fast_bridge_pointer_async(profile,media_type,item,snapshot):
    """Backfill the current tiny item pointer without delaying first paint."""
    if not isinstance(snapshot,dict) or not snapshot.get("tmdb_id"):return
    try:path=_manifest_path(profile,media_type,item)
    except Exception:return
    with _FAST_BRIDGE_LOCK:
        if path in _FAST_BRIDGE_WRITE_PENDING:return
        _FAST_BRIDGE_WRITE_PENDING.add(path)
    payload={
        "schema":53,"tmdb_id":int(snapshot.get("tmdb_id")),
        "media_type":str(snapshot.get("media_type") or _media_type(media_type)),
        "title":_title(item) or snapshot.get("title") or "",
        "year":_year_hint(item) or snapshot.get("year"),"updated_at":int(time.time()),
    }
    def worker():
        try:
            if hdd_ready():
                _atomic_json(path,payload)
                try:_library_save_alias(payload["media_type"],payload.get("title"),payload.get("year"),payload.get("tmdb_id"))
                except Exception:pass
        except Exception as exc:
            try:_art_log("R81 cache bridge pointer write failed: %s"%exc)
            except Exception:pass
        finally:
            with _FAST_BRIDGE_LOCK:_FAST_BRIDGE_WRITE_PENDING.discard(path)
    try:
        t=threading.Thread(target=worker,name="ultrastalker-cache-bridge")
        t.daemon=True;t.start()
    except Exception:
        with _FAST_BRIDGE_LOCK:_FAST_BRIDGE_WRITE_PENDING.discard(path)


def load_fast_local_poster(profile, media_type, item):
    """Return already-local canonical art without TMDb/network/Pillow work.

    R81 keeps the original direct per-item pointer as the fastest path, then adds
    a bounded HDD-only bridge for equivalent provider identities and the exact
    global title/year alias.  This is what makes cached art remain instant when
    the same movie/series arrives through Search, another portal view, or Xtream
    with a different ID field spelling.  No directory scan is performed.
    """
    if not hdd_read_ready():return {}
    try:current_key=content_cache_key(profile or {},media_type,item or {})
    except Exception:current_key=""
    cached=_fast_bridge_ram_get(current_key)
    if cached:return cached

    # 1) Exact current pointer.
    path=_manifest_path(profile,media_type,item)
    try:
        with open(path,"r",encoding="utf-8") as h:pointer=json.load(h)
        row=_fast_pointer_snapshot(item,pointer,media_type,"pointer")
        if row:
            _fast_bridge_ram_put(current_key,row);return row
    except Exception:pass

    # 2) Same provider item encoded with an equivalent strong-ID field.
    for alt in _fast_pointer_candidates(profile,media_type,item):
        try:
            with open(alt,"r",encoding="utf-8") as h:pointer=json.load(h)
        except Exception:continue
        row=_fast_pointer_snapshot(item,pointer,media_type,"identity")
        if row:
            _fast_bridge_ram_put(current_key,row)
            _persist_fast_bridge_pointer_async(profile,media_type,item,row)
            return row

    # 3) Exact global title/year alias. This is a single deterministic JSON
    # lookup, not a scan, and allows Stalker/Xtream/cross-portal rows to reuse the
    # one canonical TMDB master already on HDD.
    try:
        mt=_media_type(media_type);title=_title(item);year=_year_hint(item)
        alias=_library_load_alias(mt,title,year) or {}
        if alias and identity_cache_compatible(item,alias):
            tmdb_id=alias.get("tmdb_id")
            paths=_canonical_paths(alias.get("media_type") or mt,tmdb_id) if tmdb_id else {}
            poster=str(alias.get("poster_local") or paths.get("poster") or "")
            backdrop=str(alias.get("backdrop_local") or paths.get("backdrop") or "")
            if not (poster and os.path.isfile(poster) and os.path.getsize(poster)>256):poster=""
            if not (backdrop and os.path.isfile(backdrop) and os.path.getsize(backdrop)>1024):backdrop=""
            if poster or backdrop:
                row=dict(alias);row.update({"poster_local":poster,"backdrop_local":backdrop,"identity_pointer_verified":True,"_fast_bridge_source":"alias"})
                _fast_bridge_ram_put(current_key,row)
                _persist_fast_bridge_pointer_async(profile,media_type,item,row)
                return row
    except Exception as exc:
        try:_art_log("R81 exact cache bridge failed: %s"%exc)
        except Exception:pass
    # R269: no provider/server artwork fallback. Canonical TMDb/manual cache is
    # the only artwork authority exposed by the fast bridge.
    return {}


def load_manifest(profile, media_type, item):
    """Load canonical artwork, then fill only absent originals from BLUE vault."""
    if not hdd_read_ready():return {}
    path=_manifest_path(profile,media_type,item)
    data={}
    try:
        with open(path,"r",encoding="utf-8") as h:data=json.load(h)
        if not isinstance(data,dict):data={}
    except Exception:data={}
    tmdb_id=data.get("tmdb_id");mt=str(data.get("media_type") or _media_type(media_type))
    if tmdb_id and identity_cache_compatible(item,data):
        canonical=_load_canonical(mt,tmdb_id)
        if canonical:
            # release identity handoff: the per-item pointer already proved that
            # this canonical TMDB record belongs to the catalogue row. Preserve
            # that catalogue title on the returned canonical snapshot so Details
            # does not reject the same record a second time merely because TMDB's
            # localized/original title differs (Generation War / Unsere Mütter...,
            # Black Spot / Zone Blanche, etc.). Artwork bytes stay canonical once.
            canonical=dict(canonical)
            canonical["identity_catalogue_title"]=str(data.get("title") or _title(item) or "")
            canonical["identity_pointer_verified"]=True
            return _manual_rescue_merge(profile,media_type,item,canonical)
    # Cross-portal/global title alias. Same title/year skips TMDB search entirely.
    try:
        title=_title(item);year=_year_hint(item)
        alias=_library_load_alias(mt,title,year) or {}
        if alias and identity_cache_compatible(item,alias):return _manual_rescue_merge(profile,media_type,item,alias)
    except Exception as exc:_art_log("global alias lookup failed: %s"%exc)
    out=_manual_rescue_merge(profile,media_type,item,data)
    # R269: provider/server bootstrap is retired. Never fill missing canonical
    # components from portal artwork here.
    return out


def save_manifest(profile, media_type, item, data):
    payload=dict(data or {});tmdb_id=payload.get("tmdb_id");mt=str(payload.get("media_type") or _media_type(media_type))
    if tmdb_id:
        payload=_promote_to_canonical(mt,tmdb_id,payload)
        # Per-portal/item file is an identity pointer only.  Metadata/artwork live once.
        # The per-item pointer must be keyed by the provider/catalogue identity,
        # not by TMDB's localized title. With ar-EG, payload["title"] can be
        # Arabic while the Xtream row is English; identity_cache_compatible()
        # then rejects our own freshly-written pointer and BLUE reports the same
        # artwork as missing on the next state() read. Keep canonical metadata in
        # the global TMDB record, but keep this pointer aligned with the row that
        # owns it.
        pointer_title=_title(item) or payload.get("title") or payload.get("identity_title") or ""
        pointer_year=_year_hint(item) or payload.get("year")
        pointer={"schema":53,"tmdb_id":int(tmdb_id),"media_type":mt,"title":pointer_title,"year":pointer_year,"updated_at":int(time.time())}
        ok=_atomic_json(_manifest_path(profile,media_type,item),pointer)
        try:_library_save_alias(mt,pointer.get("title"),pointer.get("year"),tmdb_id)
        except Exception as exc:_art_log("global alias save failed: %s"%exc)
        return ok
    return False


def _media_type(media_type):
    return "tv" if str(media_type or "").lower() in ("series", "tv", "episode") else "movie"


def _title(item):
    item = item if isinstance(item, dict) else {}
    for key in ("original_title", "original_name", "name", "title", "movie_name"):
        value = str(item.get(key) or "").strip()
        if value: return value
    return ""


# Last-mile aliases for Arabic catalogues whose TMDB records are indexed under
# a Latin/English title.  These are search hints only: the returned TMDB row is
# still scored/validated before its id can become canonical.  No artwork is
# sourced from the portal or from these aliases.
_KNOWN_TITLE_ALIASES = {
    "القصة الكاملة": ("The Full Story", "Al-Qissa Al-Kamila", "Al Qissa Al Kamila"),
    "السفارة 87": ("Al Sefara 87", "Al Safara 87", "Embassy 87"),
    "سكرة الحب": ("Sakrat Al Hob", "Sakret Al Hob"),
    "قلب مفتوح": ("Qalb Maftooh", "Qalb Maftuh"),
    "ابن الشركة": ("Ebn Al Sherka", "Ibn Al Sherka"),
}

def _fallback_aliases(item):
    """Return conservative extra title queries without changing identity policy."""
    item=item if isinstance(item,dict) else {}
    candidates=[]
    for key in ("name","title","display_name","series_name","movie_name","original_name","original_title"):
        value=str(item.get(key) or "").strip()
        if value:candidates.append(value)
    base=[]
    for value in candidates:
        variants=_query_variants(value) or [value]
        for variant in variants:
            n=_norm(variant)
            if n and n not in base:base.append(n)
    out=[]
    for n in base:
        for key,aliases in _KNOWN_TITLE_ALIASES.items():
            if n == _norm(key):
                for alias in aliases:
                    if alias not in out:out.append(alias)
    return out

def _trace_identity(stage, item, **fields):
    """Tiny receiver-side trace for the stubborn last-mile catalogue rows."""
    try:
        payload={"ts":int(time.time()),"stage":_redact_log_value(stage),"title":_redact_log_value(_title(item))}
        for key,value in fields.items():
            payload[str(key)[:48]]=value if isinstance(value,(bool,int,float)) or value is None else _redact_log_value(value)
        path="/tmp/UltraStalker_artwork_trace.log"
        with open(path,"a",encoding="utf-8") as h:
            h.write(json.dumps(payload,ensure_ascii=False,separators=(",",":"))+"\n")
        try:os.chmod(path,0o600)
        except OSError:pass
    except Exception as exc:
        diagnostic_failure("artwork.failsoft", exc)
def _year_hint(item):
    item = item if isinstance(item, dict) else {}
    for key in ("year", "release_year", "released", "release_date", "first_air_date"):
        text = str(item.get(key) or "")
        m = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        if m:
            try: return int(m.group(1))
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
    m = re.search(r"\b(19\d{2}|20\d{2})\b", _title(item))
    return int(m.group(1)) if m else None


def _numeric_tmdb_id(item):
    item = item if isinstance(item, dict) else {}
    for key in ("_locked_tmdb_id", "tmdb_id", "tmdbid", "tmdb"):
        text = str(item.get(key) or "").strip()
        # Xtream may place IMDb tt1234567 in tmdb_id. Never strip its digits.
        if re.fullmatch(r"\d{1,12}", text):
            try:return int(text)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
    return None


def _imdb_id(item):
    item = item if isinstance(item, dict) else {}
    for key in ("imdb_id", "imdb", "imdbid", "tmdb_id", "tmdb"):
        text = str(item.get(key) or "").strip()
        m = re.search(r"tt\d{5,12}", text, re.I)
        if m:return m.group(0).lower()
    return ""


def _best_image(rows, poster=False, lang="ar"):
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("file_path")]
    if poster:
        rows = [r for r in rows if int(r.get("height") or 0) > int(r.get("width") or 0)]
        def rank(r):
            iso = str(r.get("iso_639_1") or "").lower()
            lang_bonus = 4 if iso == lang else (3 if iso == "en" else (2 if not iso else 0))
            return (lang_bonus, int(r.get("width") or 0), float(r.get("vote_average") or 0), int(r.get("vote_count") or 0))
    else:
        rows = [r for r in rows if int(r.get("width") or 0) >= int(r.get("height") or 1)]
        def rank(r):
            return (int(r.get("width") or 0), float(r.get("vote_average") or 0), int(r.get("vote_count") or 0))
    rows.sort(key=rank, reverse=True)
    return rows[0] if rows else None


def _ranked_posters(rows, lang="ar", limit=4):
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("file_path")]
    rows = [r for r in rows if int(r.get("height") or 0) > int(r.get("width") or 0)]
    def rank(r):
        iso = str(r.get("iso_639_1") or "").lower()
        lang_bonus = 5 if iso == lang else (4 if iso == "en" else (3 if not iso else 1))
        # Test110 master-art policy: resolution is primary. Language remains a
        # tie-breaker only; the HDD master should be the highest-quality TMDB art.
        width = int(r.get("width") or 0); height = int(r.get("height") or 0)
        return (width * height, width, lang_bonus, float(r.get("vote_average") or 0), int(r.get("vote_count") or 0))
    rows.sort(key=rank, reverse=True)
    out=[]; seen=set()
    for row in rows:
        fp=str(row.get("file_path") or "")
        if not fp or fp in seen: continue
        seen.add(fp); out.append(row)
        if len(out) >= max(1, int(limit or 4)): break
    return out


def _art_log(message):
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), _redact_log_value(message))
    # HDD log is allowed only on a genuine mounted/write-ready disk. /tmp stays
    # as the non-persistent diagnostic fallback and never shadows the cache.
    paths = ["/tmp/UltraStalker_artwork.log"]
    if hdd_ready() and ensure_persistent_dirs(ART_LOG_ROOT):
        paths.insert(0, ART_LOG_PATH)
    for path in paths:
        try:
            parent=os.path.dirname(path)
            if parent and path.startswith("/tmp/"): os.makedirs(parent, mode=0o700, exist_ok=True)
            with open(path, "a", encoding="utf-8") as h:
                h.write(line)
            try: os.chmod(path, 0o600)
            except OSError: pass
        except Exception as exc:
            diagnostic_failure("artwork.failsoft", exc)
class _TMDBOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        parts = urllib.parse.urlsplit(target)
        if parts.scheme.lower() != "https" or (parts.hostname or "").lower() != "image.tmdb.org":
            raise urllib.error.HTTPError(target, code, "TMDB CDN redirect blocked", headers, fp)
        return super(_TMDBOnlyRedirectHandler, self).redirect_request(req, fp, code, msg, headers, target)

@image_budgeted
def _normalize_poster_jpeg(source_path, target_path):
    """Write a baseline RGB JPEG that Enigma2/ePicLoad can decode reliably.

    TMDB CDN can serve JPEG/PNG/WebP depending on the underlying asset and
    content negotiation.  Older Enigma2 image loaders are much less tolerant
    than Pillow, especially when a WebP/PNG payload is stored with a .jpg
    suffix.  Posters are therefore normalized once at cache-write time.
    """
    if _PILImage is None:
        return False
    temp = target_path + ".norm.%d.%d" % (os.getpid(), threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:
                if _PILImageOps is not None:
                    image = _PILImageOps.exif_transpose(image)
            except Exception as exc:
                diagnostic_failure("artwork.failsoft", exc)
            image = image.convert("RGB")
            # Final cache: the HDD master is canonical by CONTENT, not by source
            # resolution. Ultra Stalker never needs a poster wider than this
            # receiver-sized master; each view scales it in RAM / session cache.
            # Keeping one 342px JPEG matches the dense grids with headroom and reduces first-page network,
            # disk footprint and decode cost without creating per-view masters.
            resampling = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", 1)
            try:
                image.thumbnail((POSTER_TARGET_WIDTH, POSTER_TARGET_HEIGHT), resampling)
            except Exception as exc:
                diagnostic_failure("artwork.failsoft", exc)
            _write_require(temp);image.save(temp, "JPEG", quality=90, optimize=False, progressive=False, subsampling=2)
        _write_require(target_path);os.replace(temp, target_path);_fsync_parent(target_path)
        return _valid(target_path)
    except Exception as exc:
        _art_log("poster normalize FAIL %s -> %s : %s" % (source_path, target_path, exc))
        try:
            if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
        except Exception as exc:
            diagnostic_failure("artwork.failsoft", exc)
        return False


def _image_dimensions(path):
    if _PILImage is None or not _valid(path):
        return (0, 0)
    try:
        with _PILImage.open(path) as image:
            return (int(image.width or 0), int(image.height or 0))
    except Exception:
        return (0, 0)


def _jpeg_dimensions_no_decode(path):
    """Read JPEG SOF dimensions without invoking Pillow/libjpeg.

    Safe Backdrop Mode deliberately avoids native image decoding in worker
    threads.  A malformed/huge JPEG must never be enough to restart Enigma2
    just because we wanted to validate its width.
    """
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"\xff\xd8":
                return (0, 0)
            while True:
                b = fh.read(1)
                if not b:
                    return (0, 0)
                if b != b"\xff":
                    continue
                while b == b"\xff":
                    b = fh.read(1)
                    if not b:
                        return (0, 0)
                marker = b[0]
                if marker in (0xD8, 0xD9):
                    continue
                raw = fh.read(2)
                if len(raw) != 2:
                    return (0, 0)
                seglen = int.from_bytes(raw, "big")
                if seglen < 2:
                    return (0, 0)
                if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
                    body = fh.read(5)
                    if len(body) != 5:
                        return (0, 0)
                    height = int.from_bytes(body[1:3], "big")
                    width = int.from_bytes(body[3:5], "big")
                    return (width, height)
                fh.seek(seglen - 2, 1)
    except Exception:
        return (0, 0)


def _valid_backdrop(path, min_width=BACKDROP_MIN_WIDTH):
    if not _valid(path):
        return False
    w, h = _jpeg_dimensions_no_decode(path)
    return bool(w >= int(min_width or BACKDROP_MIN_WIDTH) and h >= 600 and w >= h)


# Fast first-paint staging lives only under /tmp.  It is never authoritative
# cache and never replaces the canonical HDD store.  Its sole job is to make
# downloaded bytes visible immediately, through the same immediate temporary VOD
# artwork files; persistence/normalisation follows afterwards in the same lane.
_FIRST_PAINT_STAGE_ROOT = "/tmp/ultrastalker_firstpaint"
_FIRST_PAINT_STAGE_GUARD = threading.RLock()
_FIRST_PAINT_STAGE_LAST_PRUNE = [0.0]

def _first_paint_stage_path(url, kind):
    raw=(str(kind or "art")+"|"+str(url or "")).encode("utf-8","ignore")
    digest=hashlib.sha1(raw).hexdigest()[:24]
    ext=".png" if str(kind or "").lower()=="logo" else ".jpg"
    return os.path.join(_FIRST_PAINT_STAGE_ROOT, "%s_%s%s"%(str(kind or "art"),digest,ext))

def _prune_first_paint_stage(limit=36, max_age=7200):
    now=time.time()
    with _FIRST_PAINT_STAGE_GUARD:
        if now-float(_FIRST_PAINT_STAGE_LAST_PRUNE[0] or 0)<60:return
        _FIRST_PAINT_STAGE_LAST_PRUNE[0]=now
    try:
        if not os.path.isdir(_FIRST_PAINT_STAGE_ROOT):return
        rows=[]
        for name in os.listdir(_FIRST_PAINT_STAGE_ROOT):
            path=os.path.join(_FIRST_PAINT_STAGE_ROOT,name)
            try:
                st=os.stat(path)
                if not os.path.isfile(path):continue
                if now-st.st_mtime>float(max_age):
                    try:os.unlink(path)
                    except Exception:pass
                    continue
                rows.append((st.st_mtime,path))
            except Exception:pass
        rows.sort(reverse=True)
        for _mtime,path in rows[int(limit):]:
            try:os.unlink(path)
            except Exception:pass
    except Exception:pass

def _download_first_paint_stage(url, kind, timeout=4.0, max_bytes=None):
    """Download one visible TMDB image with minimum receiver-side work.

    No HDD gate, fsync, Pillow conversion, manifest write or adaptive work is
    allowed here.  We validate only the media envelope required for safe native
    display.  The canonical path is populated afterwards from this same file.
    """
    url=str(url or "").strip();kind=str(kind or "art").lower()
    if not url:return None
    try:
        parts=urllib.parse.urlsplit(url)
        if parts.scheme.lower()!="https" or (parts.hostname or "").lower()!="image.tmdb.org":return None
    except Exception:return None
    try:os.makedirs(_FIRST_PAINT_STAGE_ROOT,mode=0o700,exist_ok=True)
    except Exception:return None
    _prune_first_paint_stage()
    target=_first_paint_stage_path(url,kind)
    if kind=="backdrop":
        if _valid_backdrop(target):return target
    elif _valid(target):
        return target
    tmp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    cap=int(max_bytes or (8*1024*1024 if kind=="backdrop" else 5*1024*1024))
    try:
        req=urllib.request.Request(url,headers={
            "User-Agent":_UA,
            "Accept":"image/jpeg,image/png,image/webp,image/*;q=0.9,*/*;q=0.1",
        })
        opener=build_safe_https_media_opener(allowed_hosts=("image.tmdb.org",))
        total=0;head=b""
        with opener.open(req,timeout=max(2.5,min(float(timeout or 4.0),8.0))) as response:
            status=int(getattr(response,"status",200) or 200)
            if status<200 or status>=300:return None
            declared=int(response.headers.get("Content-Length") or 0)
            if declared and declared>cap:return None
            with open(tmp,"wb") as handle:
                while True:
                    chunk=response.read(96*1024)
                    if not chunk:break
                    if not head:head=chunk[:16]
                    total+=len(chunk)
                    if total>cap:raise IOError("first-paint image exceeds cap")
                    handle.write(chunk)
        if total<=1024:return None
        if not (head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or (head[:4]==b"RIFF" and head[8:12]==b"WEBP")):
            return None
        os.replace(tmp,target)
        if kind=="backdrop":
            return target if _valid_backdrop(target) else None
        return target if _valid(target) else None
    except Exception as exc:
        _art_log("first-paint stage FAIL %s %s: %s"%(kind,url,exc));return None
    finally:
        try:
            if os.path.exists(tmp):os.unlink(tmp)
        except Exception:pass

def _persist_staged_poster(stage, target):
    if _valid(target):return target
    if not stage or not _valid(stage) or not hdd_ready():return None
    try:
        if _normalize_poster_jpeg(stage,target):
            _write_marker(target+".e2jpeg")
            return target
    except Exception as exc:_art_log("first-paint poster persist failed: %s"%exc)
    return None

def _persist_staged_backdrop(stage, target):
    if _valid_backdrop(target):return target
    if not stage or not _valid_backdrop(stage) or not hdd_ready():return None
    try:
        if not ensure_persistent_dirs(os.path.dirname(target)):return None
        with _lock(target):
            if _valid_backdrop(target):return target
            temp=target+".stage.%d.%d"%(os.getpid(),threading.get_ident())
            shutil.copyfile(stage,temp)
            _write_require(target);os.replace(temp,target);_fsync_parent(target)
            return target if _valid_backdrop(target) else None
    except Exception as exc:
        _art_log("first-paint backdrop persist failed: %s"%exc)
        try:
            if 'temp' in locals() and os.path.exists(temp) and _write_ok(temp):os.unlink(temp)
        except Exception:pass
        return None


@image_budgeted
def _normalize_backdrop_jpeg(source_path, target_path):
    """Write a decoder-safe landscape JPEG with bounded decode memory.

    For JPEG sources Pillow's ``draft`` asks libjpeg to decode near Full-HD
    instead of materialising a 4K/8K RGB frame first. Other formats still use
    thumbnail after open. The final file is always baseline RGB JPEG.
    """
    if _PILImage is None:
        return False
    temp = target_path + ".backdropnorm.%d.%d" % (os.getpid(), threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:
                if str(getattr(image, "format", "") or "").upper() in ("JPEG", "JPG"):
                    image.draft("RGB", (BACKDROP_TARGET_WIDTH, BACKDROP_TARGET_HEIGHT))
            except Exception as exc:
                diagnostic_failure("artwork.failsoft", exc)
            try:
                if _PILImageOps is not None:
                    image = _PILImageOps.exif_transpose(image)
            except Exception as exc:
                diagnostic_failure("artwork.failsoft", exc)
            if int(getattr(image,"width",0) or 0) < int(getattr(image,"height",0) or 0):
                return False
            if image.mode != "RGB":
                image = image.convert("RGB")
            resampling = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", 1)
            try:
                image.thumbnail((BACKDROP_TARGET_WIDTH, BACKDROP_TARGET_HEIGHT), resampling)
            except Exception as exc:
                diagnostic_failure("artwork.failsoft", exc)
            _write_require(temp);image.save(temp, "JPEG", quality=95, optimize=False, progressive=False, subsampling=0)
        _write_require(target_path);os.replace(temp, target_path);_fsync_parent(target_path)
        return _valid_backdrop(target_path, min_width=min(BACKDROP_MIN_WIDTH, 1200))
    except Exception as exc:
        _art_log("backdrop normalize FAIL %s -> %s : %s" % (source_path, target_path, exc))
        try:
            if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
        except Exception as exc:
            diagnostic_failure("artwork.failsoft", exc)
        return False


def _download(url, target, timeout=4.5, normalize_poster=False, normalize_backdrop=False):
    """Stream TMDB artwork directly to HDD, then normalize from disk.

    Never buffers a whole original backdrop in Python RAM. This is important on
    Enigma2 where a compressed 15 MB JPEG can expand to tens of MB during decode.
    """
    marker = target + (".e2backdrop" if normalize_backdrop else (".e2jpeg" if normalize_poster else ""))
    # A path under /media/hdd is not cache unless /media/hdd is a genuine mount.
    # This check must happen before the early cached-file fast path.
    if not hdd_read_ready(force=True):
        return None
    if _valid(target):
        if (normalize_backdrop and _valid_backdrop(target) and os.path.isfile(marker)) or (normalize_poster and os.path.isfile(marker)) or (not normalize_poster and not normalize_backdrop):
            return target
        # Normalization and marker creation are writes, even on a cache hit.
        if not hdd_ready():
            return None
        if (normalize_backdrop and _normalize_backdrop_jpeg(target, target)) or (normalize_poster and _normalize_poster_jpeg(target, target)):
            _write_marker(marker)
            return target
    if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(target)):
        return None
    with _lock(target):
        # Recheck at the write boundary; the HDD can disappear while waiting
        # for another artwork worker holding this per-target lock.
        if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(target)):
            return None
        if _valid(target):
            if (normalize_backdrop and _valid_backdrop(target) and os.path.isfile(marker)) or (normalize_poster and os.path.isfile(marker)) or (not normalize_poster and not normalize_backdrop):
                return target
            if (normalize_backdrop and _normalize_backdrop_jpeg(target, target)) or (normalize_poster and _normalize_poster_jpeg(target, target)):
                _write_marker(marker)
                return target
        temp = target + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        try:
            parts = urllib.parse.urlsplit(str(url or ""))
            host=(parts.hostname or "").lower()
            if parts.scheme.lower() != "https" or host not in ("image.tmdb.org","assets.fanart.tv"):
                _art_log("blocked non-approved artwork URL: %s" % url); return None
            req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.9,*/*;q=0.1", "Connection":"close"})
            opener = build_safe_https_media_opener(allowed_hosts=("image.tmdb.org","assets.fanart.tv"))
            last = None
            max_bytes = 24 * 1024 * 1024 if normalize_backdrop else 10 * 1024 * 1024
            for attempt in range(2):
                total=0; head=b""
                try:
                    with opener.open(req, timeout=max(4.0, min(float(timeout or 4.5), 15.0))) as response:
                        status = int(getattr(response, "status", 200) or 200)
                        ctype = str(response.headers.get("Content-Type") or "").lower()
                        if status < 200 or status >= 300: raise IOError("HTTP %s" % status)
                        declared=int(response.headers.get("Content-Length") or 0)
                        if declared and declared > max_bytes: raise IOError("image too large %s" % declared)
                        _write_require(temp)
                        with open(temp, "wb") as h:
                            while True:
                                chunk=response.read(64*1024)
                                if not chunk: break
                                if not head: head=chunk[:16]
                                total += len(chunk)
                                if total > max_bytes: raise IOError("image exceeds limit %s" % max_bytes)
                                h.write(chunk)
                            h.flush(); os.fsync(h.fileno())
                    if total <= 1024: raise IOError("invalid image size %s" % total)
                    if not (head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
                        raise IOError("unexpected content-type=%s head=%r" % (ctype, head[:8]))
                    if (normalize_poster or normalize_backdrop) and _PILImage is not None:
                        ok = _normalize_backdrop_jpeg(temp, target) if normalize_backdrop else _normalize_poster_jpeg(temp, target)
                        if not ok: raise IOError("artwork JPEG normalization failed")
                        _write_marker(marker)
                        try:
                            if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
                        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
                    else:
                        _write_require(target);os.replace(temp, target);_fsync_parent(target)
                    if _valid(target):
                        _art_log("OK %s -> %s (%d streamed bytes%s)" % (url, target, total, ", normalized backdrop" if normalize_backdrop else (", normalized poster" if normalize_poster else "")))
                        return target
                    raise IOError("written file failed validation")
                except Exception as exc:
                    last=exc
                    try:
                        if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
                    except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
                    if attempt == 0: time.sleep(0.18)
            _art_log("FAIL %s -> %s : %s" % (url, target, last)); return None
        except Exception as exc:
            _art_log("FAIL %s -> %s : %s" % (url, target, exc)); return None
        finally:
            try:
                if os.path.exists(temp) and _write_ok(temp): os.unlink(temp)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)




_GENERIC_SEARCH_QUERIES = frozenset(("pure","movie","movies","film","films","vod","series","tv"))



def _cache_global_cast_profiles(media_kind, tmdb_id, credit_rows, timeout=5.0):
    """V7.0.12: cast is text-only. Never download or persist actor portraits."""
    return []

def _safe_catalogue_query(value):
    q=catalogue_title(value)
    q=str(q or "").strip()
    nq=_norm(q)
    if not nq or nq in _GENERIC_SEARCH_QUERIES:
        return ""
    if len(re.sub(r"\W+","",nq,flags=re.UNICODE)) < 2:
        return ""
    return q

def _stable_adaptive_from_art(data):
    return _library_adaptive(data or {})


class ArtworkV2(object):
    def __init__(self, credential, language="ar-EG", timeout=4):
        self.language = str(language or "ar-EG")
        self.lang = self.language.split("-")[0].lower()
        self.timeout = max(2, min(6, int(timeout or 4)))
        self.tmdb = TMDBClient(credential, self.language, self.timeout)
        self.trace_cb = None

    def _blue_trace(self, message):
        cb=getattr(self,"trace_cb",None)
        if callable(cb):
            try: cb(str(message))
            except Exception: pass

    def _resolve_identity(self, media_type, item, hot=None):
        """Resolve one stable TMDB identity without touching artwork.

        us119 keeps the working us118 poster/backdrop downloader intact and
        replaces only name matching.  Stalker catalogues are messy: display names
        can carry Arabic translations, routing tags, ``(قريبا)``, an incorrect
        catalogue year, or a Latin/original title in a secondary field.  Identity
        resolution therefore uses every useful title field, tries original/Latin
        candidates first for Asian/Turkish catalogues, searches with and without
        year, and only then falls back across movie/tv namespaces.
        """
        mt = _media_type(media_type)
        alt_mt = "movie" if mt == "tv" else "tv"
        item = item if isinstance(item, dict) else {}
        hot = hot if isinstance(hot, dict) else {}

        # Only internally locked ids or an already verified hot manifest are
        # trusted without another lookup. Provider/Xtream tmdb_id fields are
        # untrusted hints because some panels return stale/package-level ids.
        locked=_numeric_tmdb_id({"_locked_tmdb_id":item.get("_locked_tmdb_id")})
        search_verified=_numeric_tmdb_id({"_locked_tmdb_id":item.get("_ultra_search_verified_tmdb_id")})
        if locked and search_verified and int(locked)==int(search_verified):
            # Search only stamps this marker after a verified, media/year-compatible
            # HDD identity match. Trust that scoped proof instead of spending a
            # network round-trip revalidating the same TMDb id on every OK.
            return mt,int(locked),1.0,"search_verified_tmdb_id",None
        if locked:
            # A locked id can originate from an older/stale item manifest.  BLUE
            # artwork repair must not blindly preserve a poisoned identity forever.
            # Validate the id against the current catalogue title/year before trust.
            current_title=catalogue_title(item.get("_raw_name") or _title(item))
            if current_title:
                try:
                    candidate=self.tmdb._get("/%s/%s"%(mt,int(locked)),{"language":self.language}) or {}
                    candidate_name=str(candidate.get("name") or candidate.get("title") or "")
                    candidate_original=str(candidate.get("original_name") or candidate.get("original_title") or "")
                    qn=_norm(current_title)
                    names=[_norm(candidate_name),_norm(candidate_original)]
                    names=[x for x in names if x]
                    wanted_year=_year_hint(item)
                    result_year=_year(candidate.get("first_air_date") if mt=="tv" else candidate.get("release_date"))
                    year_ok=bool(not wanted_year or not result_year or abs(int(wanted_year)-int(result_year))<=1)
                    score=_score(current_title,candidate,mt,wanted_year,mt,_country_hint(item,_title(item)),str(item.get("description") or item.get("plot") or ""))
                    exact=bool(qn and qn in names)
                    if exact or (score>=0.84 and year_ok):
                        _trace_identity("locked_tmdb_verified",item,tmdb_id=locked,result=candidate_name,score=round(score,3))
                        return mt,int(locked),1.0,"locked_tmdb_id",candidate
                    _trace_identity("locked_tmdb_rejected",item,tmdb_id=locked,result=candidate_name,score=round(score,3))
                    self._blue_trace("IDENTITY_LOCK_REJECT title=%r locked_tmdb_id=%s result=%r score=%.3f"%(current_title,locked,candidate_name,score))
                except Exception as exc:
                    _art_log("locked TMDB id validation failed id=%r: %s"%(locked,exc))
                    self._blue_trace("IDENTITY_LOCK_ERROR title=%r locked_tmdb_id=%s error=%r"%(current_title,locked,str(exc)[:160]))
            else:
                return mt,int(locked),1.0,"locked_tmdb_id",None

        hot_direct=_numeric_tmdb_id(hot)
        if hot_direct and hot.get("identity_verified") and identity_cache_compatible(item,hot):
            return str(hot.get("media_type") or mt),int(hot_direct),1.0,"verified_hot_tmdb_id",None

        provider_direct=None
        for _k in ("tmdb_id","tmdbid","tmdb"):
            _v=str(item.get(_k) or "").strip()
            if re.fullmatch(r"\d{1,12}",_v):
                try:
                    provider_direct=int(_v)
                    break
                except Exception as exc:
                    diagnostic_failure("artwork.provider_tmdb_parse",exc)

        if provider_direct:
            # Ultra identity rule: a numeric TMDB id supplied by the provider's
            # authoritative info row is used directly. Do not reject it with a
            # second local scoring layer before asking TMDB for details.
            self._blue_trace("IDENTITY_PROVIDER_DIRECT title=%r tmdb_id=%s"%(catalogue_title(item.get("_raw_name") or _title(item)),provider_direct))
            return mt,int(provider_direct),1.0,"provider_tmdb_direct",None

        iid = _imdb_id(item) or _imdb_id(hot)
        if iid:
            try:
                found = self.tmdb._get("/find/%s" % iid, {"external_source": "imdb_id", "language": self.language})
                preferred = (found.get("tv_results") if mt == "tv" else found.get("movie_results")) or []
                alternate = (found.get("movie_results") if mt == "tv" else found.get("tv_results")) or []
                rows = preferred or alternate
                if rows and rows[0].get("id"):
                    resolved_mt = mt if preferred else alt_mt
                    return resolved_mt, int(rows[0]["id"]), 1.0, "imdb_id", rows[0]
            except Exception as exc:
                _art_log("IMDb->TMDB identity lookup failed %s: %s" % (iid, exc))

        # Ultra title fallback: if there is no numeric id, search the
        # requested namespace with the catalogue title. Use year first, then one
        # no-year retry, and accept TMDB's first result. The older score-heavy
        # resolver remains below only as a final safety net if TMDB returns none.
        simple_title=catalogue_title(item.get("_provider_name") or item.get("name") or item.get("title") or _title(item))
        simple_year=_year_hint(item)
        if simple_title:
            for use_year in ((True,False) if simple_year else (False,)):
                try:
                    params={"query":simple_title,"language":self.language,"include_adult":"false"}
                    if use_year and simple_year:
                        params["first_air_date_year" if mt=="tv" else "year"]=simple_year
                    payload=self.tmdb._get("/search/%s"%mt,params) or {}
                    rows=payload.get("results") or []
                    if rows and isinstance(rows[0],dict) and rows[0].get("id"):
                        rid=int(rows[0]["id"]); rname=str(rows[0].get("name") or rows[0].get("title") or "")
                        self._blue_trace("IDENTITY_FIRST_RESULT title=%r year=%r use_year=%s tmdb_id=%s result=%r"%(simple_title,simple_year,use_year,rid,rname))
                        return mt,rid,0.96,"ultra_first_result",rows[0]
                except Exception as exc:
                    _art_log("Ultra TMDB title search failed type=%s q=%r year=%r: %s"%(mt,simple_title,simple_year if use_year else None,exc))

        wanted_year = _year_hint(item)
        desc = str(item.get("description") or item.get("descr") or item.get("overview") or item.get("plot") or "")
        country = _country_hint(item, _title(item))

        # Build queries from all portal identity fields. Original/English/Latin
        # names are deliberately inserted before the display name so Korean,
        # Turkish, Indian and mixed-script rows do not depend on Arabic search.
        fields = (
            "_provider_name", "original_name", "original_title", "o_name", "orig_name",
            "english_name", "english_title", "eng_name", "eng_title",
            "movie_name", "series_name", "display_name", "title", "name",
        )
        sources=[]
        for key in fields:
            value=str(item.get(key) or "").strip()
            if value and value not in sources: sources.append(value)
        raw=_title(item)
        if raw and raw not in sources: sources.append(raw)
        if not sources:
            return mt, None, 0.0, "none", None

        queries=[]
        search_sources=[]
        for source in sources:
            cleaned=catalogue_title(source)
            if cleaned and cleaned not in search_sources:
                search_sources.append(cleaned)
        raw_clean=catalogue_title(item.get("_raw_name") or raw)
        if raw_clean and raw_clean not in search_sources:
            search_sources.insert(0,raw_clean)

        for source in search_sources:
            for search_clean in tmdb_search_aliases(source):
                nq=_norm(search_clean)
                # Never search weak branding-only/generic queries.
                if not nq or nq in ("pure","movie","movies","film","films"):
                    continue
                if len(re.sub(r"\W+","",nq,flags=re.UNICODE))<2:
                    continue
                if all(nq!=_norm(x) for x in queries):
                    queries.append(search_clean)
            for q in _query_variants(source):
                q=catalogue_title(q)
                nq=_norm(q)
                if not nq or nq in ("pure","movie","movies","film","films"):
                    continue
                if len(re.sub(r"\W+","",nq,flags=re.UNICODE))<2:
                    continue
                if all(_norm(x)!=nq for x in queries):
                    queries.append(q)
        queries=queries[:8]
        if not queries:
            return mt, None, 0.0, "none", None

        # Foreign catalogues should try a Latin/original candidate first when one
        # exists. This mirrors the mature Stalker-client behaviour we wanted.
        foreign = country in ("KR","TR","IN","MY","ID","TH","JP","CN","PH","SG")
        if foreign:
            queries.sort(key=lambda q: (0 if _latin_heavy(q) else 1, len(q)))

        candidates=[]
        def add_rows(rows, candidate_mt, q):
            for row in (rows or [])[:12]:
                if not isinstance(row,dict) or not row.get("id"): continue
                # Do not globally dedupe a TMDB id before all query variants have
                # seen it.  A noisy portal form such as
                # 'السفارة 87 (قريبا) - 3' may encounter the correct row first,
                # but only the later clean query 'السفارة 87' gives it the score
                # it deserves. Keep both scores and let the best one win.
                score=_score(q,row,candidate_mt,wanted_year,mt,country,desc)
                candidates.append((score,row,candidate_mt,q))

        def do_search(candidate_mt, query, use_year, language):
            params={"query":query,"language":language,"include_adult":"false"}
            if use_year and wanted_year:
                params["first_air_date_year" if candidate_mt=="tv" else "year"]=wanted_year
            try:
                payload=self.tmdb._get("/search/%s"%candidate_mt,params)
                add_rows(payload.get("results") or [],candidate_mt,query)
                return True
            except Exception as exc:
                _art_log("TMDB identity search failed type=%s q=%r year=%r: %s"%(candidate_mt,query,wanted_year if use_year else None,exc))
                return False

        # Requested namespace first. Year is a hint, never a gate: many portals
        # label upcoming/imported titles with the catalogue year rather than TMDB's
        # first-air/release year, so every query also gets one no-year attempt.
        for q in queries:
            lang="en-US" if (_latin_heavy(q) and foreign) else self.language
            if wanted_year: do_search(mt,q,True,lang)
            best=max([x[0] for x in candidates if x[2]==mt] or [0.0])
            if best < 0.94: do_search(mt,q,False,lang)
            best=max([x[0] for x in candidates if x[2]==mt] or [0.0])
            if best >= 0.94: break

        requested_best=max([x[0] for x in candidates if x[2]==mt] or [0.0])
        if requested_best < (0.88 if country else 0.72):
            for q in queries[:5]:
                lang="en-US" if (_latin_heavy(q) and foreign) else self.language
                if wanted_year: do_search(alt_mt,q,True,lang)
                alt_best=max([x[0] for x in candidates if x[2]==alt_mt] or [0.0])
                if alt_best < 0.92: do_search(alt_mt,q,False,lang)
                alt_best=max([x[0] for x in candidates if x[2]==alt_mt] or [0.0])
                if alt_best >= 0.92: break

        # Final multi-search is deliberately bounded to two best query variants.
        if not candidates or max(x[0] for x in candidates)<0.62:
            for q in queries[:2]:
                try:
                    lang="en-US" if (_latin_heavy(q) and foreign) else self.language
                    payload=self.tmdb._get("/search/multi",{"query":q,"language":lang,"include_adult":"false"})
                    for row in payload.get("results") or []:
                        rmt=str(row.get("media_type") or "")
                        if rmt in ("movie","tv"): add_rows([row],rmt,q)
                except Exception as exc:
                    _art_log("TMDB multi identity search failed q=%r: %s"%(q,exc))

        if not candidates:
            # Last conservative rescue for Latin catalogue titles: ask TMDB in
            # en-US without a year and accept ONLY an exact localized/original
            # title.  This recovers well-known titles when locale/year metadata
            # prevented the normal ranked search, without introducing fuzzy IDs.
            for q in queries[:5]:
                if not _latin_heavy(q):
                    continue
                try:
                    payload=self.tmdb._get("/search/%s"%mt,{"query":q,"language":"en-US","include_adult":"false"}) or {}
                    qn=_norm(q)
                    for row in (payload.get("results") or [])[:12]:
                        if not isinstance(row,dict) or not row.get("id"):
                            continue
                        names=(_norm(row.get("name") or row.get("title") or ""),_norm(row.get("original_name") or row.get("original_title") or ""))
                        if qn and qn in names:
                            self._blue_trace("IDENTITY_EN_EXACT title=%r query=%r tmdb_id=%s result=%r"%(raw,q,row.get("id"),row.get("name") or row.get("title") or ""))
                            return mt,int(row.get("id")),1.0,"en_exact_rescue",row
                except Exception as exc:
                    _art_log("TMDB en exact rescue failed type=%s q=%r: %s"%(mt,q,exc))
            _art_log("identity miss title=%r queries=%r year=%r country=%r"%(raw,queries,wanted_year,country))
            _trace_identity("identity_miss",item,queries=queries,year=wanted_year,country=country)
            return mt,None,0.0,"none",None
        candidates.sort(key=lambda x:x[0],reverse=True)
        best_score,best,best_mt,best_query=candidates[0]
        result_year=_year(best.get("first_air_date") if best_mt=="tv" else best.get("release_date"))
        year_close=bool(wanted_year and result_year and abs(int(wanted_year)-int(result_year))<=1)
        exact=bool(_norm(best_query) and _norm(best_query) in (_norm(best.get("name") or best.get("title") or ""),_norm(best.get("original_name") or best.get("original_title") or "")))
        threshold=0.58 if (year_close or exact) else 0.68
        if best_mt!=mt: threshold=max(threshold,0.84)
        # Numeric Arabic catalogue titles are collision-prone (87, 75, K1...).
        # A candidate must preserve the meaningful number.  Also reject a purely
        # foreign-script candidate for an Arabic query unless the portal itself
        # identifies the title as a foreign catalogue.
        qnums=re.findall(r"\d+", _norm(best_query or ""))
        candidate_text=str(best.get("name") or best.get("title") or "")+" "+str(best.get("original_name") or best.get("original_title") or "")
        cnums=re.findall(r"\d+", _norm(candidate_text))
        if qnums and qnums != cnums and not exact:
            _art_log("identity numeric mismatch title=%r q=%r result=%r"%(raw,best_query,best.get("name") or best.get("title")))
            return mt,None,float(best_score),"none",None
        query_ar=bool(re.search(r"[\u0600-\u06ff]",str(best_query or "")))
        result_ar=bool(re.search(r"[\u0600-\u06ff]",candidate_text))
        foreign_catalogue=country in ("KR","TR","IN","MY","ID","TH","JP","CN","PH","SG")
        if query_ar and not result_ar and not foreign_catalogue and not exact:
            _art_log("identity script mismatch title=%r q=%r result=%r"%(raw,best_query,best.get("name") or best.get("title")))
            return mt,None,float(best_score),"none",None
        if best_score < threshold:
            _art_log("identity low-confidence title=%r q=%r result=%r score=%.3f threshold=%.3f"%(raw,best_query,best.get("name") or best.get("title"),best_score,threshold))
            return mt,None,float(best_score),"none",None
        _art_log("identity OK title=%r q=%r -> %s/%s %r score=%.3f"%(raw,best_query,best_mt,best.get("id"),best.get("name") or best.get("title"),best_score))
        _trace_identity("identity_ok",item,query=best_query,tmdb_id=best.get("id"),tmdb_type=best_mt,result=best.get("name") or best.get("title"),score=round(best_score,3))
        return best_mt,int(best["id"]),float(best_score),"search",best

    def _resolve_poster_rescue(self, media_type, item):
        """Balanced identity rescue for catalogue rows missed by normal matching.

        Prefer cleaned exact/localized titles, but do not make portal year/editorial
        noise a hard gate.  Numeric titles are protected against collisions: if the
        query contains a meaningful number, a fuzzy candidate must contain the same
        number.  A successful rescue becomes the canonical TMDB identity so poster,
        backdrop and details stay in sync.
        """
        mt=_media_type(media_type)
        item=item if isinstance(item,dict) else {}
        wanted_year=_year_hint(item)
        sources=[]
        for key in ("_provider_name","original_name","original_title","english_name","english_title","o_name","movie_name","series_name","display_name","title","name"):
            val=str(item.get(key) or "").strip()
            if val and val not in sources:sources.append(val)
        raw=_title(item)
        if raw and raw not in sources:sources.append(raw)
        queries=[]
        def add_query(value):
            q=_safe_catalogue_query(value)
            nq=_norm(q)
            if q and nq and all(nq!=_norm(x) for x in queries):
                queries.append(q)

        raw_clean=_safe_catalogue_query(item.get("_raw_name") or raw)
        if raw_clean:
            for search_clean in tmdb_search_aliases(raw_clean):
                add_query(search_clean)

        # Every rescue source, including _provider_name, is sanitized first.
        for source in sources:
            source_clean=_safe_catalogue_query(source)
            if not source_clean:
                continue
            for q in _query_variants(source_clean):
                add_query(q)

        # Keep localized/English aliases, but never let a generic provider label
        # become an identity query.
        _aliases=_fallback_aliases(item)
        if _aliases:
            merged=[]
            if queries:
                merged.append(queries[0])
            for q in _aliases + queries[1:]:
                q=_safe_catalogue_query(q)
                if q and all(_norm(q)!=_norm(x) for x in merged):
                    merged.append(q)
            queries=merged

        for source in list(sources):
            q=re.sub(r"\([^)]*\)"," ",source)
            q=re.sub(r"\[[^]]*\]"," ",q)
            q=re.sub(r"\s+[-|:]\s*\d{1,2}\s*$","",q)
            q=re.sub(r"\s+"," ",q).strip(" -_|:.")
            add_query(q)

        # Network-boundary guard. Future fallbacks cannot reintroduce Pure.
        queries=[q for q in queries if _safe_catalogue_query(q)]
        queries=queries[:10]
        ranked=[]
        for q in queries:
            params={"query":q,"language":self.language,"include_adult":"false"}
            payloads=[]
            if wanted_year:
                with_year=dict(params);with_year["first_air_date_year" if mt=="tv" else "year"]=wanted_year
                try:payloads.append(self.tmdb._get("/search/%s"%mt,with_year))
                except Exception as exc:_art_log("verified rescue search failed type=%s q=%r year=%r: %s"%(mt,q,wanted_year,exc))
            try:payloads.append(self.tmdb._get("/search/%s"%mt,params))
            except Exception as exc:_art_log("verified rescue search failed type=%s q=%r: %s"%(mt,q,exc))
            for payload in payloads:
                for row in (payload.get("results") or [])[:12]:
                    if not isinstance(row,dict) or not row.get("id"):continue
                    qn=_norm(q)
                    names=[_norm(row.get("name") or row.get("title") or ""),_norm(row.get("original_name") or row.get("original_title") or "")]
                    names=[x for x in names if x]
                    exact=bool(qn and qn in names)
                    score=_score(q,row,mt,wanted_year,mt,"","")
                    result_year=_year(row.get("first_air_date") if mt=="tv" else row.get("release_date"))
                    # The portal year is frequently a catalogue year rather than
                    # TMDB first-air year.  Treat it as ranking evidence only.
                    year_close=bool(not wanted_year or not result_year or abs(int(wanted_year)-int(result_year))<=2)
                    qnums=re.findall(r"\d+",_norm(q))
                    result_name=str(row.get("name") or row.get("title") or "")
                    original_name=str(row.get("original_name") or row.get("original_title") or "")
                    rnums=re.findall(r"\d+",_norm(result_name + " " + original_name))
                    # If the catalogue title contains a number (87, K1, 75...), a
                    # fuzzy candidate without that same number is unsafe.  Exact
                    # localized/original titles are the only exception.
                    nums_ok=bool(not qnums or exact or qnums==rnums)
                    qtokens=set(_norm(q).split())
                    ntokens=set(_norm(result_name).split())
                    otokens=set(_norm(original_name).split())
                    overlap=max(
                        float(len(qtokens & ntokens))/float(max(1,len(qtokens | ntokens))),
                        float(len(qtokens & otokens))/float(max(1,len(qtokens | otokens)))
                    )
                    # Us123: broaden rescue enough to recover real Arabic portal
                    # titles while keeping numeric collisions out. Exact matches are
                    # always preferred; fuzzy matches need both a healthy score and
                    # token overlap. A year mismatch simply raises the bar slightly.
                    threshold=0.80 if year_close else 0.86
                    q_ar=bool(re.search(r"[\u0600-\u06ff]",str(q or "")))
                    result_ar=bool(re.search(r"[\u0600-\u06ff]",result_name+" "+original_name))
                    country=_country_hint(item, raw)
                    foreign_catalogue=country in ("KR","TR","IN","MY","ID","TH","JP","CN","PH","SG")
                    script_ok=bool((not q_ar) or result_ar or foreign_catalogue or exact)
                    trusted=bool(nums_ok and script_ok and (exact or (score>=threshold and overlap>=0.50)))
                    if exact:score=max(score,0.99)
                    ranked.append((score,row,q,trusted,exact,result_year))
            if ranked and any(x[3] and x[0]>=0.98 for x in ranked):break
        if not ranked:return mt,None,0.0,None,False
        ranked.sort(key=lambda x:x[0],reverse=True)
        score,row,q,trusted,exact,result_year=ranked[0]
        if not trusted:
            _art_log("verified rescue rejected title=%r q=%r result=%r score=%.3f year=%r"%(raw,q,row.get("name") or row.get("title"),score,result_year))
            return mt,None,float(score),None,False
        _art_log("verified rescue OK title=%r q=%r -> %s/%s %r score=%.3f"%(raw,q,mt,row.get("id"),row.get("name") or row.get("title"),score))
        return mt,int(row.get("id")),float(score),row,True

    @staticmethod
    def _full_metadata_ready(data):
        if not isinstance(data,dict):return False
        try:return bool(_library_complete(data))
        except Exception:return False

    def resolve_backdrop_only(self, profile, media_type, item, cancel_event=None):
        """Resolve and persist only a real landscape backdrop.

        This path is deliberately isolated from poster and metadata painting.
        It is used by the details screen when the normal metadata resolver misses
        a difficult title but the provider/original/IMDb identity still gives us
        enough evidence to fetch a safe TMDB backdrop.
        """
        if cancel_event is not None and cancel_event.is_set():
            return {}
        item=item if isinstance(item,dict) else {}
        mt=_media_type(media_type)
        wanted_year=_year_hint(item)
        title_sources=[]
        for key in ("original_title","original_name","o_name","english_name","_raw_name","name","title"):
            value=str(item.get(key) or "").strip()
            cleaned=_safe_catalogue_query(value)
            if cleaned and all(_norm(cleaned)!=_norm(x) for x in title_sources):
                title_sources.append(cleaned)

        def cancelled():
            return bool(cancel_event is not None and cancel_event.is_set())

        def row_year(row):
            return _year((row or {}).get("first_air_date") if mt=="tv" else (row or {}).get("release_date"))

        def row_names(row):
            return [str((row or {}).get(k) or "") for k in ("name","title","original_name","original_title") if str((row or {}).get(k) or "").strip()]

        def strong_match(row):
            if not isinstance(row,dict) or not row.get("id"):
                return False,0.0
            names=[_norm(x) for x in row_names(row) if _norm(x)]
            exact=False
            best=0.0
            for query in title_sources:
                qn=_norm(query)
                if qn and qn in names:
                    exact=True
                try:
                    best=max(best,float(_score(query,row,mt,wanted_year,mt,_country_hint(item,_title(item)),
                                               str(item.get("description") or item.get("plot") or ""))))
                except Exception as exc:
                    diagnostic_failure("artwork.backdrop_only_score",exc)
            ry=row_year(row)
            year_ok=bool(not wanted_year or not ry or abs(int(wanted_year)-int(ry))<=1)
            return bool(year_ok and (exact or best>=0.90)),best

        tmdb_id=None
        seed=None
        identity_source=""

        # 1) Trusted internal lock from an already verified cache.
        #
        # Issue 4: this lock is written only by Ultra Stalker after canonical
        # identity has already been proved.  Re-scoring the current provider
        # display title against TMDB here made exact recovery fail for titles
        # whose catalogue name contains a subtitle/translation (for example
        # ``BLACK TRICK: The Lawyer Who Controls Justice`` while TMDB stores a
        # shorter canonical name).  That failure was invisible while the old
        # backdrop file still existed, then surfaced after HDD cleanup/moves:
        # Cache Artwork could no longer rebuild the missing master.
        #
        # A locked id therefore means exactly what its name says: use that
        # numeric identity directly.  The direct details request still proves
        # the TMDB record exists, but title-language/display-name differences
        # are no longer allowed to detach a previously verified identity.
        locked=str(item.get("_locked_tmdb_id") or "").strip()
        locked_type=str(item.get("_locked_tmdb_type") or "").strip().lower()
        if re.fullmatch(r"\d{1,12}",locked) and (not locked_type or _media_type(locked_type)==mt):
            try:
                candidate=self.tmdb._get("/%s/%s"%(mt,int(locked)),{"language":self.language}) or {}
                if isinstance(candidate,dict) and candidate.get("id"):
                    tmdb_id=int(locked);seed=candidate;identity_source="locked_tmdb"
            except Exception as exc:
                _art_log("backdrop-only locked id failed %s/%s: %s"%(mt,locked,exc))

        # 1b) A compatible persisted item pointer is the same verified identity
        # evidence on a cold path.  Reuse it before IMDb/provider/title search so
        # deleting or moving only the artwork bytes never makes identity recovery
        # weaker than it was before the cleanup.
        if not tmdb_id and not cancelled():
            try:
                cached=load_manifest(profile,media_type,item) or {}
                cached_id=str(cached.get("tmdb_id") or "").strip()
                cached_mt=_media_type(cached.get("media_type") or media_type)
                if re.fullmatch(r"\d{1,12}",cached_id) and cached_mt==mt and identity_cache_compatible(item,cached):
                    tmdb_id=int(cached_id);seed=cached;identity_source="verified_manifest"
            except Exception as exc:
                _art_log("backdrop-only manifest identity failed: %s"%exc)

        # 2) IMDb is exact identity evidence when available.
        if not tmdb_id and not cancelled():
            iid=_imdb_id(item)
            if iid:
                try:
                    found=self.tmdb._get("/find/%s"%iid,{"external_source":"imdb_id"}) or {}
                    rows=(found.get("tv_results") if mt=="tv" else found.get("movie_results")) or []
                    for row in rows:
                        if isinstance(row,dict) and row.get("id"):
                            ok,_=strong_match(row)
                            if ok or not title_sources:
                                tmdb_id=int(row.get("id"));seed=row;identity_source="imdb_find";break
                except Exception as exc:
                    _art_log("backdrop-only IMDb find failed %s: %s"%(iid,exc))

        # 3) Provider tmdb_id is only a hint and must validate against title/year.
        if not tmdb_id and not cancelled():
            for key in ("tmdb_id","tmdbid","tmdb"):
                raw=str(item.get(key) or "").strip()
                if not re.fullmatch(r"\d{1,12}",raw):
                    continue
                try:
                    row=self.tmdb._get("/%s/%s"%(mt,int(raw)),{"language":self.language}) or {}
                    ok,_=strong_match(row)
                    if ok:
                        tmdb_id=int(raw);seed=row;identity_source="provider_tmdb_verified";break
                except Exception as exc:
                    _art_log("backdrop-only provider id failed %s/%s: %s"%(mt,raw,exc))

        # 4) release First Valid Artwork Wins.
        # Do not make localized display names prove each other. Search every
        # available title/alias across the useful TMDB languages and accept the
        # first sane year-compatible result that actually exposes artwork.
        # Once a usable hit is found, stop immediately and use its numeric TMDB id.
        if not tmdb_id and not cancelled():
            queries=[]
            for source in title_sources:
                for q in (tmdb_search_aliases(source) or [source]):
                    q=_safe_catalogue_query(q)
                    if q and all(_norm(q)!=_norm(x) for x in queries):
                        queries.append(q)
                for q in _query_variants(source):
                    q=_safe_catalogue_query(q)
                    if q and all(_norm(q)!=_norm(x) for x in queries):
                        queries.append(q)
            # Keep the configured language first, then explicit English/German/Arabic
            # rescue.  This is identity/artwork discovery only; it does not alter the
            # title language shown by Ultra Stalker.
            search_languages=[]
            for lang in (self.language,"en-US","de-DE","ar-EG"):
                lang=str(lang or "").strip() or "en-US"
                if lang not in search_languages:
                    search_languages.append(lang)
            for query in queries[:12]:
                if cancelled():
                    break
                for search_language in search_languages:
                    if cancelled():
                        break
                    params={"query":query,"language":search_language,"include_adult":"false","page":1}
                    if wanted_year:
                        params["first_air_date_year" if mt=="tv" else "year"]=wanted_year
                    try:
                        payload=self.tmdb._get("/search/%s"%mt,params) or {}
                    except Exception as exc:
                        _art_log("backdrop-only first-valid search failed %r lang=%s: %s"%(query,search_language,exc))
                        continue
                    for row in (payload.get("results") or [])[:8]:
                        if not isinstance(row,dict) or not row.get("id"):
                            continue
                        ry=row_year(row)
                        if wanted_year and ry:
                            try:
                                if abs(int(wanted_year)-int(ry))>1:
                                    continue
                            except Exception:
                                pass
                        # First usable artwork result wins. Backdrop is preferred, but
                        # poster also proves this is a real visual candidate; the exact
                        # details call below can still supply the backdrop gallery.
                        if not (row.get("backdrop_path") or row.get("poster_path")):
                            continue
                        seed=row
                        tmdb_id=int(row.get("id"))
                        identity_source="first_valid:%s"%search_language
                        self._blue_trace("BACKDROP_FIRST_VALID title=%r query=%r lang=%s tmdb_id=%s backdrop=%r poster=%r"%(
                            _title(item),query,search_language,tmdb_id,row.get("backdrop_path"),row.get("poster_path")))
                        break
                    if tmdb_id:
                        break
                if tmdb_id:
                    break

        if not tmdb_id or cancelled():
            return {}

        canonical=_load_canonical(mt,tmdb_id)
        existing=str(canonical.get("backdrop_local") or "")
        if _valid_backdrop(existing):
            return {
                "matched":True,"identity_verified":True,"identity_pointer_verified":True,
                "identity_catalogue_title":(_safe_catalogue_query(_title(item)) or _title(item)),
                "media_type":mt,"tmdb_id":tmdb_id,
                "backdrop_local":existing,"backdrop_path":canonical.get("backdrop_path"),
                "backdrop_url":canonical.get("backdrop_url"),"backdrop_source":canonical.get("backdrop_source") or "canonical",
                "identity_source":"backdrop_only:%s"%identity_source,
            }

        if not hdd_ready() or cancelled():
            return {}

        details={}
        try:
            details=self.tmdb._get("/%s/%s"%(mt,tmdb_id),{
                "language":self.language,
                "append_to_response":"images",
                "include_image_language":"%s,en,null"%self.lang,
            }) or {}
        except Exception as exc:
            _art_log("backdrop-only details failed %s/%s: %s"%(mt,tmdb_id,exc))

        images=details.get("images") if isinstance(details.get("images"),dict) else {}
        # Issue 9 last-mile recovery: a non-empty images payload is not proof
        # that it contains the landscape gallery.  If the language-filtered
        # appended response has no ranked backdrops, pull the complete TMDB
        # `/images` set before declaring this verified title backdrop-less.
        # This is especially important for Arabic/foreign originals whose
        # backdrop row may carry a language tag different from the current
        # metadata request.
        if not str(details.get("backdrop_path") or "").strip() and not _ranked_backdrops((images or {}).get("backdrops"),self.lang,1):
            unfiltered=_all_tmdb_images(self.tmdb,mt,tmdb_id)
            if isinstance(unfiltered,dict) and unfiltered.get("backdrops"):
                images=dict(images or {})
                images["backdrops"]=unfiltered.get("backdrops") or []

        target=_canonical_paths(mt,tmdb_id)["backdrop"]
        resolved,fp,url,source=_resolve_backdrop_v4(
            self.tmdb,self.language,mt,tmdb_id,details,images,None,target,cancel_event
        )
        if not resolved or not _valid_backdrop(resolved):
            _mark_backdrop_state(mt,tmdb_id,"pending",next_retry_at=int(time.time())+120)
            _schedule_backdrop_recovery(self.tmdb.credential,self.language,mt,tmdb_id)
            return {}

        data=_load_canonical(mt,tmdb_id)
        data.update({
            "media_type":mt,"tmdb_id":tmdb_id,
            "backdrop_path":fp,"backdrop_url":url,"backdrop_local":resolved,
            "backdrop_source":source or "gallery","backdrop_state":"real",
        })
        _save_canonical(mt,tmdb_id,data)
        _mark_backdrop_state(mt,tmdb_id,"real",backdrop_source=source or "gallery",retry_count=0)
        return {
            "matched":True,"identity_verified":True,"identity_pointer_verified":True,
            "identity_catalogue_title":(_safe_catalogue_query(_title(item)) or _title(item)),
            "media_type":mt,"tmdb_id":tmdb_id,
            "backdrop_local":resolved,"backdrop_path":fp,"backdrop_url":url,
            "backdrop_source":source or "gallery",
            "identity_source":"backdrop_only:%s"%identity_source,
        }

    def _resolve_unlocked(self, profile, media_type, item, full=False, cancel_event=None, component_callback=None):
        def _emit_component(kind, payload):
            if not callable(component_callback):
                return
            if cancel_event is not None and cancel_event.is_set():
                return
            try:
                component_callback(str(kind), dict(payload or {}))
            except Exception as exc:
                _art_log("component callback failed %s: %s" % (kind, exc))
        # Explicit BLUE detached probes carry a strictly verified locked TMDB id.
        # Do not let an older title alias/pointer short-circuit that exact rescue.
        blue_exact=bool(isinstance(item,dict) and item.get("_blue_detached_exact") and item.get("_locked_tmdb_id"))
        hot = {} if blue_exact else load_manifest(profile, media_type, item)
        if cancel_event is not None and cancel_event.is_set(): return hot

        # us181 HDD-only canonical fast path.  If this title has ever been
        # resolved before, reopen its canonical files directly from /media/hdd
        # before doing identity work, metadata calls or URL-hash lookups.
        # This is the path that makes a cold boot/restart feel instant.
        try:
            hid = int(hot.get("tmdb_id") or 0)
        except Exception:
            hid = 0
        if hid:
            hmt = str(hot.get("media_type") or _media_type(media_type))
            canonical = _load_canonical(hmt, hid)
            if canonical.get("poster_local"):
                hot["poster_local"] = canonical.get("poster_local")
            if canonical.get("backdrop_local"):
                hot["backdrop_local"] = canonical.get("backdrop_local")
            for _k,_v in canonical.items():
                if _v not in (None, ""):
                    hot[_k] = _v
            poster_ready=bool(hot.get("poster_local"))
            backdrop_ready=bool(hot.get("backdrop_local"))
            if full and hot.get("manual_rescue_poster_locked") and not hot.get("poster_candidate_unavailable"):
                poster_ready=False
            if full and hot.get("manual_rescue_backdrop_locked") and not hot.get("backdrop_candidate_unavailable"):
                backdrop_ready=False
            if poster_ready and (not full or (backdrop_ready and self._full_metadata_ready(hot) and bool(hot.get("title_logo_probe_v2_checked")))):
                return hot

        # True hot path for already-linked item manifests. A provider/manual
        # fallback is display-safe but does not block a full TMDB upgrade pass.
        poster_ready=bool(hot.get("poster_local"))
        backdrop_ready=bool(hot.get("backdrop_local"))
        if full and hot.get("manual_rescue_poster_locked") and not hot.get("poster_candidate_unavailable"):
            poster_ready=False
        if full and hot.get("manual_rescue_backdrop_locked") and not hot.get("backdrop_candidate_unavailable"):
            backdrop_ready=False
        if hot.get("tmdb_id") and poster_ready and (not full or (backdrop_ready and self._full_metadata_ready(hot) and bool(hot.get("title_logo_probe_v2_checked")))):
            return hot
        mt, tmdb_id, confidence, identity_source, seed = self._resolve_identity(media_type, item, hot)
        rescue_verified=False
        if not tmdb_id:
            rmt,rid,rconf,rseed,rtrusted=self._resolve_poster_rescue(media_type,item)
            if rid and rtrusted:
                mt,tmdb_id,confidence,seed=rmt,rid,rconf,rseed
                identity_source="verified_rescue";rescue_verified=True
        if not tmdb_id:
            # Never regress a previously decoded image just because a later
            # identity pass cannot reproduce the match.  Keep sticky local art.
            return {
                "matched": False, "confidence": confidence, "source": "TMDB",
                "poster_local": hot.get("poster_local"),
                "backdrop_local": hot.get("backdrop_local"),
                "tmdb_id": hot.get("tmdb_id"),
                "tmdb_lookup_state": "not_found",
                "poster_candidate_unavailable": True,
                "backdrop_candidate_unavailable": True,
            }
        if cancel_event is not None and cancel_event.is_set(): return hot

        # Canonical V3 hot path: once identity is known, every portal/screen uses
        # the same files. This bypasses URL-hash caches and prevents re-downloads.
        canonical = _load_canonical(mt, tmdb_id)
        if canonical.get("poster_local"):
            hot["poster_local"] = canonical.get("poster_local")
        if canonical.get("backdrop_local"):
            hot["backdrop_local"] = canonical.get("backdrop_local")
        for _k,_v in canonical.items():
            if _v not in (None,"") and hot.get(_k) in (None,""):
                hot[_k]=_v

        # R182 point-2 first-visible pass: once a verified identity already owns
        # canonical HDD artwork, publish each existing component immediately.
        # Do this before metadata repair/gallery probing so Details never waits on
        # a network request to rediscover bytes that are already on disk.
        if full:
            _hot_identity={
                "matched":True,"identity_verified":True,"source":"TMDB",
                "identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,
                "confidence":round(confidence,3),
                "logo_url":str(hot.get("logo_url") or ""),
                "logo_language":str(hot.get("logo_language") or ""),
                "title_logo_checked":bool(hot.get("title_logo_probe_v2_checked") or hot.get("title_logo_checked")),
                "title_logo_probe_checked":bool(hot.get("title_logo_probe_v2_checked") or hot.get("title_logo_probe_checked")),
                "title_logo_probe_v2_checked":bool(hot.get("title_logo_probe_v2_checked")),
            }
            _emit_component("identity",_hot_identity)
        if _valid(hot.get("poster_local")):
            _emit_component("poster",{
                "matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,
                "media_type":mt,"tmdb_id":tmdb_id,"poster_local":hot.get("poster_local"),
                "poster_url":hot.get("poster_url"),"poster_path":hot.get("poster_path"),
            })
        if _valid_backdrop(hot.get("backdrop_local")):
            _emit_component("backdrop",{
                "matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,
                "media_type":mt,"tmdb_id":tmdb_id,"backdrop_local":hot.get("backdrop_local"),
                "backdrop_url":hot.get("backdrop_url"),"backdrop_path":hot.get("backdrop_path"),
                "backdrop_source":hot.get("backdrop_source"),
            })

        # Revalidation bridge for us124-and-older weak identities.  Keep a good
        # decoded file when the freshly resolved id is unchanged; otherwise the
        # stale/wrong artwork is deliberately detached from this content item.
        previous_id = hot.get("_previous_tmdb_id")
        try: same_revalidated = bool(previous_id and int(previous_id) == int(tmdb_id))
        except Exception: same_revalidated = False
        if same_revalidated:
            qp=hot.get("_quarantined_poster_local"); qb=hot.get("_quarantined_backdrop_local")
            if _valid(qp): hot["poster_local"]=qp; hot["poster_url"]=hot.get("_quarantined_poster_url")
            if _valid_backdrop(qb): hot["backdrop_local"]=qb; hot["backdrop_url"]=hot.get("_quarantined_backdrop_url")
        for _k in ("_needs_revalidate","_previous_tmdb_id","_quarantined_poster_local","_quarantined_backdrop_local","_quarantined_poster_url","_quarantined_backdrop_url"):
            hot.pop(_k,None)

        # Resolve paths. Canonical cached files win before network. For new art,
        # /images is used to prefer the highest-resolution backdrop; the default
        # details path remains a reliable fallback when gallery lookup is slow.
        seed = seed if isinstance(seed, dict) else {}
        # R260: a missing local file must not resurrect an old manifest path
        # that may have been gallery-selected by an earlier build. Current TMDb
        # seed/details paths are allowed; a valid warm local file remains locked.
        poster_path = seed.get("poster_path")
        backdrop_path = seed.get("backdrop_path") if full else None

        # Receiver-first paint: verified TMDB search rows already contain the
        # default poster/backdrop paths.  Start both visible image lanes NOW,
        # before the heavier details payload.  Each lane owns its own executor
        # so poster can never queue ahead of backdrop (or vice versa).  Network
        # bytes land in a small /tmp staging file, are published immediately,
        # then promoted to the canonical HDD cache from those SAME bytes.
        canonical_paths = _canonical_paths(mt, tmdb_id)
        poster_target = canonical_paths["poster"]
        backdrop_target = canonical_paths["backdrop"]
        poster_local = hot.get("poster_local") if _valid(hot.get("poster_local")) else None
        backdrop_local = hot.get("backdrop_local") if _valid_backdrop(hot.get("backdrop_local")) else None
        early_poster_future=None;early_backdrop_future=None

        if full and not backdrop_local and backdrop_path and not (cancel_event is not None and cancel_event.is_set()):
            _early_backdrop_url=TMDBClient.image_url(backdrop_path,"w1280")
            if _early_backdrop_url:
                def _early_backdrop_lane(_url=_early_backdrop_url,_path=backdrop_path,_target=backdrop_target):
                    stage=_download_first_paint_stage(_url,"backdrop",min(4.0,max(2.8,self.timeout)),max_bytes=8*1024*1024)
                    if stage and not (cancel_event is not None and cancel_event.is_set()):
                        # Paint the downloaded bytes BEFORE fsync/cache/index work.
                        _emit_component("backdrop",{
                            "matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,
                            "media_type":mt,"tmdb_id":tmdb_id,"backdrop_local":stage,"backdrop_url":_url,
                            "backdrop_path":_path,"backdrop_source":"default:w1280","first_paint_stage":True,
                        })
                    canonical=_persist_staged_backdrop(stage,_target) if stage else None
                    if canonical:
                        _mark_backdrop_state(mt,tmdb_id,"real",backdrop_source="default:w1280",retry_count=0)
                    return canonical,_path,_url,"default:w1280"
                try:
                    early_backdrop_future=_FIRST_PAINT_BACKDROP_EXECUTOR.submit(_early_backdrop_lane,_task_key="backdrop:%s:%s"%(mt,tmdb_id))
                except Exception as exc:_art_log("early backdrop submit failed %s/%s: %s"%(mt,tmdb_id,exc))

        if full and not poster_local and poster_path and not (cancel_event is not None and cancel_event.is_set()):
            _early_poster_url=TMDBClient.image_url(poster_path,"w342")
            if _early_poster_url:
                def _early_poster_lane(_url=_early_poster_url,_path=poster_path,_target=poster_target):
                    stage=_download_first_paint_stage(_url,"poster",min(4.0,max(2.8,self.timeout)),max_bytes=5*1024*1024)
                    # Poster is normalised before publication so Details does not
                    # flash a raw/stretched source and replace it two seconds later.
                    canonical=_persist_staged_poster(stage,_target) if stage else None
                    if canonical and not (cancel_event is not None and cancel_event.is_set()):
                        _emit_component("poster",{
                            "matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,
                            "media_type":mt,"tmdb_id":tmdb_id,"poster_local":canonical,"poster_url":_url,
                            "poster_path":_path,"first_paint_stage":False,
                        })
                    return canonical,_url,_path
                try:
                    early_poster_future=_FIRST_PAINT_POSTER_EXECUTOR.submit(_early_poster_lane,_task_key="poster:%s:%s"%(mt,tmdb_id))
                except Exception as exc:_art_log("early poster submit failed %s/%s: %s"%(mt,tmdb_id,exc))

        base_details = {}
        # V4: one details call owns paths + seasons/collection + credits.  This
        # avoids racing a path-only request against a later metadata request and
        # gives the backdrop ladder everything it needs on the first open.
        if full or (not poster_path and not hot.get("poster_local")):
            try:
                params={"language": self.language}
                # Poster-only hydration stays deliberately lean: the normal
                # details response already carries poster_path. Logo galleries,
                # credits and external IDs are stable-focus/full-detail work.
                if full:
                    params["append_to_response"]="images,credits,external_ids"
                    _langs=[]
                    for _lang in (self.lang,"en","null"):
                        if _lang and _lang not in _langs:_langs.append(_lang)
                    params["include_image_language"]=",".join(_langs)
                base_details = self.tmdb._get("/%s/%s" % (mt, tmdb_id), params)
            except Exception as exc:
                _art_log("V4 TMDB details lookup failed %s/%s: %s" % (mt, tmdb_id, exc))
                base_details = {}
            if not poster_local:
                _official_poster=str(base_details.get("poster_path") or "").strip()
                if _official_poster: poster_path=_official_poster
            if full and not backdrop_local:
                _official_backdrop=str(base_details.get("backdrop_path") or "").strip()
                if _official_backdrop: backdrop_path=_official_backdrop

        images = {}
        if isinstance(base_details.get("images"),dict):
            images = base_details.get("images") or {}
        # release: wire Ultra's title-logo source into the resolver that Details
        # actually uses.  The logo comes from the SAME appended images payload;
        # there is no separate /images lookup for title-logo selection.
        logo_path = ""
        logo_language = str(hot.get("logo_language") or "")
        logo_url = str(hot.get("logo_url") or "")
        title_logo_probe_complete = bool(hot.get("title_logo_probe_v2_checked"))
        if full:
            logos = images.get("logos") if isinstance(images,dict) else []
            if logos:
                try:
                    _logo_rows=[x for x in logos if isinstance(x,dict) and x.get("file_path")]
                    _rank={self.lang:0,"en":1,"":2,"null":2}
                    _logo_rows.sort(key=lambda x:(_rank.get(str(x.get("iso_639_1") or "").lower(),3),-float(x.get("vote_average") or 0)))
                    _logo_row=_logo_rows[0] if _logo_rows else {}
                    logo_path = str(_logo_row.get("file_path") or "")
                    logo_language = str(_logo_row.get("iso_639_1") or "").lower()
                except Exception:
                    logo_path = ""
            if logo_path:
                logo_url = TMDBClient.image_url(logo_path, "w300") or logo_url
            # Poster-only hydration never seals title-logo discovery state.
            title_logo_probe_complete = bool(isinstance(base_details, dict) and "images" in base_details)
        if full:
            _identity_partial={
                "matched":True,"identity_verified":True,"source":"TMDB",
                "identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,
                "confidence":round(confidence,3),"logo_url":logo_url,"logo_language":logo_language,
                "title_logo_checked":title_logo_probe_complete,
                "title_logo_probe_checked":title_logo_probe_complete,
                "title_logo_probe_v2_checked":title_logo_probe_complete,
            }
            if isinstance(base_details,dict):
                _identity_partial["title"]=base_details.get("name") if mt=="tv" else base_details.get("title")
                _identity_partial["original_title"]=base_details.get("original_name") if mt=="tv" else base_details.get("original_title")
            _emit_component("identity",_identity_partial)

        if full and not hot.get("backdrop_local") and not backdrop_path:
            # R260: gallery is fallback-only when the verified Details payload
            # genuinely has no primary backdrop. It never replaces a primary.
            back_candidates = _ranked_backdrops((images or {}).get("backdrops"), self.lang, 14)
            back = back_candidates[0] if back_candidates else None
            if not back:
                unfiltered = _all_tmdb_images(self.tmdb, mt, tmdb_id)
                unfiltered_backdrops = (unfiltered or {}).get("backdrops") if isinstance(unfiltered,dict) else []
                if unfiltered_backdrops:
                    images = dict(images or {})
                    images["backdrops"] = unfiltered_backdrops
                    back_candidates = _ranked_backdrops(unfiltered_backdrops, self.lang, 14)
                    back = back_candidates[0] if back_candidates else None
            if back and back.get("file_path"):
                backdrop_path = back.get("file_path")
        if not poster_path and not hot.get("poster_local"):
            if not images:
                images = _all_tmdb_images(self.tmdb, mt, tmdb_id)
            post = _best_image(images.get("posters"), True, self.lang)
            poster_path = (post or {}).get("file_path") if post else None
        poster_url = TMDBClient.image_url(poster_path, "w342") if poster_path else None
        # Performance Lab 19: receiver-sized TMDB artwork only; never request original.
        backdrop_url = TMDBClient.image_url(backdrop_path, "w1280") if backdrop_path else None
        _art_log("resolve %s/%s source=%s poster=%s backdrop=%s" % (mt, tmdb_id, identity_source, bool(poster_url), bool(backdrop_url)))
        if poster_local:
            _emit_component("poster", {"matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,"poster_local":poster_local,"poster_url":poster_url,"poster_path":poster_path})
        if backdrop_local:
            _emit_component("backdrop", {"matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,"backdrop_local":backdrop_local,"backdrop_url":backdrop_url,"backdrop_path":backdrop_path,"backdrop_source":backdrop_source})

        # Canonical artwork still belongs only on the mounted HDD.  The only
        # exception is the bounded /tmp first-paint staging above: it is a short-
        # lived display handoff, never authoritative cache.  If HDD is late we
        # can still paint that staging file, but persistence waits for the disk.
        disk_ok = hdd_ready()
        if not disk_ok:
            result = dict(hot)
            result.update({
                "matched": True, "identity_verified": True, "source": "TMDB",
                "media_type": mt, "tmdb_id": tmdb_id, "confidence": round(confidence, 3),
                "poster_path": poster_path or hot.get("poster_path"),
                "backdrop_path": backdrop_path or hot.get("backdrop_path"),
                "poster_url": poster_url or hot.get("poster_url"),
                "poster_local": poster_local,
                "backdrop_url": backdrop_url or hot.get("backdrop_url"),
                "backdrop_local": backdrop_local,
                "logo_url": logo_url, "logo_language": logo_language, "title_logo_checked": title_logo_probe_complete, "title_logo_probe_checked": title_logo_probe_complete, "title_logo_probe_v2_checked": title_logo_probe_complete,
                "backdrop_state": "real" if backdrop_local else ("pending" if full else str(hot.get("backdrop_state") or "unknown")),
                "artwork_storage": "hdd_wait",
            })
            return result

        # R173 Parallel First Paint: poster and backdrop are independent visual
        # components. Once verified identity + the single TMDB details payload
        # exist, never make the landscape lane wait for poster download/rescue.
        # Both lanes write straight to the SAME persistent canonical HDD targets.
        def _poster_first_paint_lane():
            local=poster_local
            out_url=poster_url
            out_path=poster_path
            lane_images=images
            if out_url and not local and not (cancel_event is not None and cancel_event.is_set()):
                local=_download(out_url,poster_target,min(4.2,max(3.2,self.timeout)),normalize_poster=True)
            if not local and not out_path and not (cancel_event is not None and cancel_event.is_set()):
                gallery=_all_tmdb_images(self.tmdb,mt,tmdb_id)
                if gallery:lane_images=gallery
                candidates=_ranked_posters((lane_images or {}).get("posters"),self.lang,8)
                attempted=set([str(out_path or "")])
                for n,row in enumerate(candidates):
                    if cancel_event is not None and cancel_event.is_set():break
                    fp=str((row or {}).get("file_path") or "")
                    if not fp or fp in attempted:continue
                    attempted.add(fp)
                    alt_url=TMDBClient.image_url(fp,"w342")
                    if not alt_url:continue
                    got=_download(alt_url,poster_target,min(4.5,max(3.2,self.timeout)),normalize_poster=True)
                    if got:
                        local=got;out_url=alt_url;out_path=fp
                        _art_log("poster rescue selected %s/%s candidate=%s size=w342 lang=%s"%(mt,tmdb_id,n+1,str((row or {}).get("iso_639_1") or "null")))
                        break
            return local,out_url,out_path

        backdrop_fallback_local=hot.get("backdrop_fallback_local") if _valid(hot.get("backdrop_fallback_local")) else None
        backdrop_source=str(hot.get("backdrop_source") or "")
        def _backdrop_first_paint_lane():
            local=backdrop_local
            out_path=backdrop_path
            out_url=backdrop_url
            out_source=backdrop_source
            if full and not local and not (cancel_event is not None and cancel_event.is_set()):
                # First visible landscape uses TMDB's already-known default path.
                # It is one bounded download straight to the canonical HDD file.
                # The heavier gallery/episode/collection ladder is fallback only,
                # never a prerequisite for painting the screen.
                if out_url:
                    try:
                        direct=_download_backdrop_safe(out_url,backdrop_target,min(4.5,max(3.2,self.timeout)))
                    except Exception:
                        direct=None
                    if direct:
                        local=direct
                        out_source=out_source or "default:w1280"
                        _mark_backdrop_state(mt,tmdb_id,"real",backdrop_source=out_source,retry_count=0)
                if not local and not out_path and not (cancel_event is not None and cancel_event.is_set()):
                    lane_images=images
                    if not lane_images:
                        lane_images=_all_tmdb_images(self.tmdb,mt,tmdb_id)
                    resolved,resolved_fp,resolved_url,resolved_source=_resolve_backdrop_v4(
                        self.tmdb,self.language,mt,tmdb_id,base_details,lane_images,
                        None,backdrop_target,cancel_event
                    )
                    if resolved:
                        local=resolved
                        out_path=resolved_fp or out_path
                        out_url=resolved_url or out_url
                        out_source=resolved_source or "tmdb_gallery_fallback:w1280"
                        _mark_backdrop_state(mt,tmdb_id,"real",backdrop_source=out_source,retry_count=0)
                if not local:
                    _mark_backdrop_state(mt,tmdb_id,"pending",next_retry_at=int(time.time())+60)
                    _schedule_backdrop_recovery(self.tmdb.credential,self.language,mt,tmdb_id)
            return local,out_path,out_url,out_source

        poster_future=early_poster_future;backdrop_future=early_backdrop_future
        if full and not poster_local and poster_future is None and not (cancel_event is not None and cancel_event.is_set()):
            try:poster_future=_FIRST_PAINT_POSTER_EXECUTOR.submit(_poster_first_paint_lane,_task_key="poster:%s:%s"%(mt,tmdb_id))
            except Exception as exc:_art_log("parallel poster submit failed %s/%s: %s"%(mt,tmdb_id,exc))
        if full and not backdrop_local and backdrop_future is None and not (cancel_event is not None and cancel_event.is_set()):
            try:backdrop_future=_FIRST_PAINT_BACKDROP_EXECUTOR.submit(_backdrop_first_paint_lane,_task_key="backdrop:%s:%s"%(mt,tmdb_id))
            except Exception as exc:_art_log("parallel backdrop submit failed %s/%s: %s"%(mt,tmdb_id,exc))

        # R181: futures may still be joined below for the final canonical record,
        # but the UI is notified by EACH lane independently the instant it lands.
        if poster_future is not None and poster_future is not early_poster_future:
            def _poster_done(_future):
                try:
                    _local,_url,_path=_future.result()
                    if _local:_emit_component("poster",{"matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,"poster_local":_local,"poster_url":_url,"poster_path":_path})
                except Exception as exc:_art_log("poster first-paint callback failed %s/%s: %s"%(mt,tmdb_id,exc))
            try:poster_future.add_done_callback(_poster_done)
            except Exception:pass
        if backdrop_future is not None and backdrop_future is not early_backdrop_future:
            def _backdrop_done(_future):
                try:
                    _local,_path,_url,_source=_future.result()
                    if _local:_emit_component("backdrop",{"matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,"backdrop_local":_local,"backdrop_url":_url,"backdrop_path":_path,"backdrop_source":_source})
                except Exception as exc:_art_log("backdrop first-paint callback failed %s/%s: %s"%(mt,tmdb_id,exc))
            try:backdrop_future.add_done_callback(_backdrop_done)
            except Exception:pass

        if poster_future is not None:
            try:poster_local,poster_url,poster_path=poster_future.result()
            except Exception as exc:_art_log("parallel poster lane failed %s/%s: %s"%(mt,tmdb_id,exc))
        elif not poster_local:
            poster_local,poster_url,poster_path=_poster_first_paint_lane()
            if poster_local:_emit_component("poster",{"matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,"poster_local":poster_local,"poster_url":poster_url,"poster_path":poster_path})

        if backdrop_future is not None:
            try:backdrop_local,backdrop_path,backdrop_url,backdrop_source=backdrop_future.result()
            except Exception as exc:_art_log("parallel backdrop lane failed %s/%s: %s"%(mt,tmdb_id,exc))
        elif full and not backdrop_local:
            backdrop_local,backdrop_path,backdrop_url,backdrop_source=_backdrop_first_paint_lane()
            if backdrop_local:_emit_component("backdrop",{"matched":True,"identity_verified":True,"source":"TMDB","identity_source":identity_source,"media_type":mt,"tmdb_id":tmdb_id,"backdrop_local":backdrop_local,"backdrop_url":backdrop_url,"backdrop_path":backdrop_path,"backdrop_source":backdrop_source})

        # R260: poster/backdrop bytes are TMDb-only. Fanart.tv remains eligible
        # for a missing title logo, but it may not replace official poster/backdrop
        # artwork or become a second landscape authority.
        fanart_source = ""
        # release: Fanart.tv also fills a missing title logo, but only after
        # TMDB has genuinely returned no same-language logo.  This stays inside
        # ArtworkV2's existing worker path; no UI-thread network work is added.
        _visible_logo_title = str(catalogue_title(seed.get("name") or seed.get("title") or seed.get("original_name") or seed.get("original_title") or hot.get("title") or hot.get("name") or "") or "")
        _wanted_logo_lang = "ar" if re.search(r"[\u0600-\u06ff]", _visible_logo_title) else "en"
        if full and (not poster_local or not backdrop_local or not logo_url) and not (cancel_event is not None and cancel_event.is_set()):
            try:
                _keys=_load_api_keys() or {}
                fanart_project_key=str(_keys.get("FANART_API_KEY") or "").strip()
                fanart_personal_key=str(_keys.get("FANART_CLIENT_KEY") or "").strip()
            except Exception:
                fanart_project_key="";fanart_personal_key=""
            self._blue_trace("FANART_KEYS media=%s tmdb_id=%s project_present=%s personal_present=%s"%(mt,tmdb_id,bool(fanart_project_key),bool(fanart_personal_key)))
            if fanart_project_key or fanart_personal_key:
                tvdb_id=""
                if mt=="tv":
                    ext=(base_details.get("external_ids") if isinstance(base_details,dict) and isinstance(base_details.get("external_ids"),dict) else {}) or {}
                    tvdb_id=str(ext.get("tvdb_id") or "").strip()
                    self._blue_trace("FANART_TVDB initial tmdb_id=%s tvdb_id=%r"%(tmdb_id,tvdb_id))
                    if not tvdb_id:
                        try:
                            # TMDB exposes TV external IDs as a dedicated endpoint.
                            # Do not rely on append_to_response here; some responses
                            # omit external_ids even when the dedicated endpoint has it.
                            ext_details=self.tmdb._get("/tv/%s/external_ids"%tmdb_id,{}) or {}
                            tvdb_id=str(ext_details.get("tvdb_id") or "").strip()
                            self._blue_trace("FANART_TVDB lookup tmdb_id=%s tvdb_id=%r"%(tmdb_id,tvdb_id))
                        except Exception as exc:
                            _art_log("fanart tvdb id lookup failed tv/%s: %s"%(tmdb_id,exc))
                            self._blue_trace("FANART_TVDB_ERROR tmdb_id=%s error=%r"%(tmdb_id,str(exc)[:160]))
                try:
                    fa=_fanart_fetch(fanart_project_key,mt,tmdb_id=tmdb_id,tvdb_id=tvdb_id,timeout=max(4.0,self.timeout),personal_key=fanart_personal_key,trace_cb=getattr(self,"trace_cb",None),logo_lang=_wanted_logo_lang) or {}
                    self._blue_trace("FANART_FETCH media=%s tmdb_id=%s tvdb_id=%r poster_url=%s backdrop_url=%s title_logo_url=%s logo_lang=%s"%(mt,tmdb_id,tvdb_id,bool(fa.get("poster_url")),bool(fa.get("backdrop_url")),bool(fa.get("title_logo_url")),_wanted_logo_lang))
                    if not logo_url and fa.get("title_logo_url"):
                        logo_url=str(fa.get("title_logo_url") or "").strip()
                        if logo_url:
                            fanart_source="fanart.tv"
                    # Poster/backdrop may use Fanart only when TMDb Details has
                    # no official primary path at all. It can never replace a
                    # TMDb primary or rotate an already-selected official image.
                    _tmdb_primary_poster=str((base_details or {}).get("poster_path") or "").strip()
                    _tmdb_primary_backdrop=str((base_details or {}).get("backdrop_path") or "").strip()
                    if not _tmdb_primary_poster and not poster_local and fa.get("poster_url"):
                        got=_download(str(fa.get("poster_url")),poster_target,min(6.0,max(4.0,self.timeout)),normalize_poster=True)
                        self._blue_trace("FANART_POSTER_FALLBACK tmdb_id=%s success=%s"%(tmdb_id,bool(got and _valid(got))))
                        if got:
                            poster_local=got;fanart_source="fanart.tv"
                    if full and not _tmdb_primary_backdrop and not backdrop_local and fa.get("backdrop_url"):
                        got=_download(str(fa.get("backdrop_url")),backdrop_target,min(7.0,max(4.5,self.timeout)),normalize_backdrop=True)
                        self._blue_trace("FANART_BACKDROP_FALLBACK tmdb_id=%s success=%s"%(tmdb_id,bool(got and _valid(got))))
                        if got and _valid_backdrop(got):
                            backdrop_local=got;backdrop_url=str(fa.get("backdrop_url"));backdrop_source="fanart.tv";fanart_source="fanart.tv"
                            _mark_backdrop_state(mt,tmdb_id,"real",backdrop_source="fanart.tv",retry_count=0)
                except Exception as exc:
                    _art_log("fanart fallback failed %s/%s: %s"%(mt,tmdb_id,exc))
                    self._blue_trace("FANART_ERROR media=%s tmdb_id=%s error=%r"%(mt,tmdb_id,str(exc)[:180]))

        # R60: poster-only hydration already knows the canonical TMDB identity and
        # usually has either the search seed or the lightweight details response.
        # Publish YEAR + rating from that data immediately instead of discarding
        # it and forcing Poster Grid to wait for a second metadata lookup/focus.
        # This adds no network request: it only reuses data already fetched while
        # resolving/downloading the visible poster.
        _card_meta_seed = base_details if isinstance(base_details,dict) and base_details else (seed if isinstance(seed,dict) else {})
        _card_date = (_card_meta_seed.get("first_air_date") if mt == "tv" else _card_meta_seed.get("release_date")) or hot.get("release_date") or ""
        _card_year = _year(_card_date) or _year(hot.get("year"))
        _card_rating = _card_meta_seed.get("vote_average")
        if _card_rating in (None, ""):
            _card_rating = hot.get("rating") or hot.get("vote_average")
        _card_vote_count = _card_meta_seed.get("vote_count")
        if _card_vote_count in (None, ""):
            _card_vote_count = hot.get("vote_count")

        result = dict(hot)
        result.update({
            "matched": True, "identity_verified": True, "source": "TMDB", "identity_source": identity_source,
            "identity_evidence": 4 if identity_source in ("tmdb_id", "imdb_id") else (3 if rescue_verified else 2),
            "media_type": mt, "tmdb_id": tmdb_id, "confidence": round(confidence, 3),
            "identity_title": str(seed.get("name") or seed.get("title") or seed.get("original_name") or seed.get("original_title") or ""),
            "poster_path": poster_path or hot.get("poster_path"),
            "backdrop_path": backdrop_path or hot.get("backdrop_path"),
            "poster_url": poster_url or hot.get("poster_url"), "poster_local": poster_local,
            "backdrop_url": backdrop_url or hot.get("backdrop_url"), "backdrop_local": backdrop_local,
            "logo_url": logo_url, "title_logo_checked": title_logo_probe_complete, "title_logo_probe_checked": title_logo_probe_complete, "title_logo_probe_v2_checked": title_logo_probe_complete,
            "backdrop_fallback_local": backdrop_fallback_local,
            "backdrop_source": backdrop_source,
            "secondary_artwork_source": fanart_source,
            "backdrop_state": "real" if backdrop_local else str(hot.get("backdrop_state") or ("pending" if full else "unknown")),
            "year": _card_year or hot.get("year"),
            "release_date": _card_date or hot.get("release_date") or "",
            "rating": _card_rating,
            "vote_count": _card_vote_count,
        })
        if full and not (cancel_event is not None and cancel_event.is_set()):
            details = base_details if isinstance(base_details,dict) and base_details else {}
            if not details:
                try:
                    details = self.tmdb._get("/%s/%s" % (mt, tmdb_id), {"language": self.language, "append_to_response": "credits,external_ids"})
                except Exception:
                    details = {}
            if details:
                date = details.get("first_air_date") if mt == "tv" else details.get("release_date")
                title = details.get("name") if mt == "tv" else details.get("title")
                original_title = details.get("original_name") if mt == "tv" else details.get("original_title")
                credits = details.get("credits") if isinstance(details.get("credits"), dict) else {}
                all_cast_rows=[x for x in (credits.get("cast") or []) if isinstance(x,dict) and x.get("name")]
                cast_rows=all_cast_rows[:6]
                cast = [str(x.get("name")) for x in cast_rows]
                # release: names remain essential metadata, but cast portraits are
                # permanently disabled. No profile_path/local portrait data is created.
                cast_profiles=[]
                directors=[]; writers=[]
                for p in (credits.get("crew") or []):
                    if not isinstance(p, dict) or not p.get("name"): continue
                    name=str(p.get("name")); job=str(p.get("job") or ""); dept=str(p.get("department") or "")
                    if job == "Director" and name not in directors: directors.append(name)
                    if (job in ("Writer","Screenplay","Story","Teleplay","Original Story","Novel") or dept == "Writing") and name not in writers: writers.append(name)
                if mt == "tv":
                    for p in (details.get("created_by") or []):
                        if isinstance(p, dict) and p.get("name") and str(p.get("name")) not in writers: writers.insert(0, str(p.get("name")))
                ext = details.get("external_ids") if isinstance(details.get("external_ids"), dict) else {}
                countries=[]
                for row in (details.get("production_countries") or details.get("origin_country") or []):
                    value=(row.get("iso_3166_1") or row.get("name")) if isinstance(row,dict) else row
                    if value and str(value) not in countries: countries.append(str(value))
                result.update({
                    "title": str(title or _title(item)), "original_title": str(original_title or ""),
                    "overview": str(details.get("overview") or ""), "year": _year(date), "release_date": date or "",
                    "rating": details.get("vote_average"), "vote_count": details.get("vote_count"),
                    "genres": [str(x.get("name")) for x in (details.get("genres") or []) if isinstance(x,dict) and x.get("name")],
                    "runtime": details.get("runtime") or ((details.get("episode_run_time") or [0])[0] if isinstance(details.get("episode_run_time"),list) and details.get("episode_run_time") else 0),
                    "number_of_seasons": details.get("number_of_seasons") or 0, "number_of_episodes": details.get("number_of_episodes") or 0,
                    "cast": cast, "cast_total": len(all_cast_rows), "directors": directors[:3], "writers": writers[:4], "countries": countries[:3],
                    "imdb_id": str(ext.get("imdb_id") or _imdb_id(item) or ""),
                    "overview_unavailable": not bool(str(details.get("overview") or "").strip()),
                    "credits_unavailable": not bool(cast or directors or writers),
                    "runtime_unavailable": not bool(details.get("runtime") or (details.get("episode_run_time") or [])),
                    "country_unavailable": not bool(countries),
                    "year_unavailable": not bool(_year(date)),
                    # Distinguish a true TMDB absence from a transient image download failure.
                    "poster_candidate_unavailable": not bool(details.get("poster_path") or poster_path or (images.get("posters") if isinstance(images,dict) else [])),
                    "backdrop_candidate_unavailable": not bool(details.get("backdrop_path") or backdrop_path or (images.get("backdrops") if isinstance(images,dict) else [])),
                    "_metadata_complete": True,
                })
        if full:
            result=_stable_adaptive_from_art(result)
        # Test110: provider rescue may run only when TMDB genuinely has no candidate.
        # A download failure for an existing candidate remains TMDB-owned and is retried.
        result["poster_candidate_unavailable"] = bool(result.get("poster_candidate_unavailable", not bool(poster_path or ((images or {}).get("posters") if isinstance(images,dict) else []))))
        result["backdrop_candidate_unavailable"] = bool(result.get("backdrop_candidate_unavailable", not bool(backdrop_path or ((images or {}).get("backdrops") if isinstance(images,dict) else []))))
        result["tmdb_lookup_state"] = "resolved"
        save_manifest(profile, media_type, item, result)
        return result

    def resolve(self, profile, media_type, item, full=False, cancel_event=None, component_callback=None):
        """Globally deduplicated hydration for one logical title.

        The first portal/card owns the TMDB request; concurrent duplicates wait
        on the same title lock and then hit the completed global HDD record.
        """
        lock=_resolve_dedup_lock(media_type,item)
        with lock:
            blue_exact=bool(isinstance(item,dict) and item.get("_blue_detached_exact") and item.get("_locked_tmdb_id"))
            if cancel_event is not None and cancel_event.is_set():
                return {} if blue_exact else load_manifest(profile,media_type,item)
            if blue_exact:
                return self._resolve_unlocked(profile,media_type,item,full=full,cancel_event=cancel_event,component_callback=component_callback)
            hot=load_manifest(profile,media_type,item) or {}
            if hot.get("tmdb_id") and hot.get("poster_local") and (not full or (hot.get("backdrop_local") and self._full_metadata_ready(hot) and bool(hot.get("title_logo_probe_v2_checked")))):
                return hot
            return self._resolve_unlocked(profile,media_type,item,full=full,cancel_event=cancel_event,component_callback=component_callback)

