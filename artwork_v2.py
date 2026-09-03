# -*- coding: utf-8 -*-
"""Ultra Stalker v2 external artwork pipeline.

Single-source policy for VOD/Series artwork:
  HDD canonical cache -> TMDB -> local placeholder in the UI.
Portal artwork is intentionally never inspected here.

The module owns identity-to-artwork resolution and persistent image files.
Metadata enrichment remains compatible with the existing details UI, while
image downloading/caching no longer depends on the legacy artwork pipeline.
"""
from __future__ import absolute_import
import json
import os
import re
import hashlib
import threading
import time
import urllib.request
import urllib.parse
import shutil
try:
    from PIL import Image as _PILImage, ImageOps as _PILImageOps, ImageFilter as _PILImageFilter
except Exception:
    _PILImage = None
    _PILImageOps = None
    _PILImageFilter = None

from .tmdb import TMDBClient, _query_variants, _score, _year, _country_hint, _latin_heavy, _norm
from .title_clean import tmdb_search_title, tmdb_search_aliases, catalogue_title
from .persistent_cache import ROOT, content_cache_key, hdd_ready, hdd_read_ready, ensure_persistent_dirs, persistent_write_gate
from .netsec import build_safe_https_media_opener
from .log import redact as _redact_log_value
from .log import diagnostic_failure

ART_ROOT = os.path.join(ROOT, "tmdb_artwork_v2")
POSTER_ROOT = os.path.join(ART_ROOT, "posters")
BACKDROP_ROOT = os.path.join(ART_ROOT, "backdrops")
MANIFEST_ROOT = os.path.join(ART_ROOT, "manifests")
# Artwork Cache V3: one canonical on-disk home per verified TMDB identity.
# Item manifests remain as a fast identity index, but image bytes are no longer
# tied to a translated title, portal item id, URL size, or one UI screen.
V3_ROOT = os.path.join(ROOT, "tmdb_artwork_v3")
V3_MOVIE_ROOT = os.path.join(V3_ROOT, "movie")
V3_TV_ROOT = os.path.join(V3_ROOT, "tv")
BACKDROP_PENDING_ROOT = os.path.join(V3_ROOT, "pending_backdrops")
ART_LOG_ROOT = os.path.join(ROOT, "logs")
ART_LOG_PATH = os.path.join(ART_LOG_ROOT, "artwork.log")
ART_DIRS = (ART_ROOT, POSTER_ROOT, BACKDROP_ROOT, MANIFEST_ROOT, V3_ROOT, V3_MOVIE_ROOT, V3_TV_ROOT, BACKDROP_PENDING_ROOT, ART_LOG_ROOT)
# Directories are created lazily by the central persistent write gate.
# Importing this module must remain completely free of /media/hdd I/O.

_SCHEMA = 12
_UA = "Ultra Stalker/10 TMDB-Artwork-Safe194"
_LOCKS = {}
_LOCK_GUARD = threading.RLock()

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

def _pending_backdrop_path(media_type, tmdb_id):
    mt = "tv" if str(media_type or "").lower() == "tv" else "movie"
    return os.path.join(BACKDROP_PENDING_ROOT, "%s_%s.json" % (mt, int(tmdb_id)))

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
            opener = build_safe_https_media_opener(allowed_hosts=("image.tmdb.org",))
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
            _write_require(target);os.replace(temp,target)
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
    """Safe high-quality backdrop ladder without runtime image decoding.

    Prefer TMDB original only when the JPEG itself is receiver-safe Full-HD
    (<=1920x1080).  Larger originals are discarded without decoding and we fall
    back to w1280.  The file is streamed to HDD and later displayed directly by
    Enigma2, so Pillow never expands the landscape frame in Python memory.
    """
    fp=str(file_path or "")
    if not fp:return None, None, None
    attempts=(("original",11.0,1920,1080),("w1280",9.0,1920,1080))
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
        _write_require(target);os.replace(temp,target)
        return target if _valid(target) else None
    except Exception as exc:
        _art_log("V4 synthetic fallback failed %s: %s"%(poster_path,exc))
        try:
            if os.path.exists(temp) and _write_ok(temp):os.unlink(temp)
        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
        return None


def _resolve_backdrop_v4(client, language, media_type, tmdb_id, details, images, poster_local, target, cancel_event=None):
    """One deterministic backdrop ladder for every Movies/Series details screen."""
    def cancelled():return bool(cancel_event is not None and cancel_event.is_set())
    lang=str(language or "ar-EG").split("-")[0].lower()
    attempted=[]
    # 1) Complete unfiltered series/movie gallery.
    for fp in _candidate_file_paths((images or {}).get("backdrops"),lang,14):
        if cancelled():return None,None,None,None
        if fp in attempted:continue
        attempted.append(fp)
        local,url,src=_download_landscape_path(fp,target,"gallery")
        if local:return local,fp,url,src
        if len(attempted)>=8:break
    # 2) TMDB's official default backdrop, even if gallery ranking omitted it.
    default=str((details or {}).get("backdrop_path") or "")
    if default and default not in attempted and not cancelled():
        local,url,src=_download_landscape_path(default,target,"default")
        if local:return local,default,url,src
    # 3) TV shows: episode stills are genuine TMDB landscape artwork and are far
    # better than a blank screen when a new series has no series-level backdrop.
    if media_type=="tv" and not cancelled():
        for fp in _tv_episode_still_paths(client,tmdb_id,details,10):
            if cancelled():return None,None,None,None
            url=TMDBClient.image_url(fp,"w1280")
            if url:
                local=_download_backdrop_safe(url,target,9.0)
                if local:return local,fp,url,"episode_still:w1280"
    # 4) Movies: collection art is still TMDB artwork and often exists when the
    # individual entry is new/obscure.
    if media_type=="movie" and not cancelled():
        for fp in _movie_collection_backdrops(client,details,lang,8):
            if cancelled():return None,None,None,None
            local,url,src=_download_landscape_path(fp,target,"collection")
            if local:return local,fp,url,src
    # 5) No artwork synthesis here. The UI may use a lightweight poster-derived
    # ambient fallback, but the artwork engine itself owns only real TMDB JPEGs.
    return None,None,None,None

def _ensure_backdrop_recovery_worker():
    global _BACKDROP_RECOVERY_QUEUE, _BACKDROP_RECOVERY_THREAD
    with _BACKDROP_RECOVERY_LOCK:
        if _BACKDROP_RECOVERY_QUEUE is None:
            import queue as _queue
            _BACKDROP_RECOVERY_QUEUE=_queue.Queue(maxsize=64)
        if _BACKDROP_RECOVERY_THREAD is not None and _BACKDROP_RECOVERY_THREAD.is_alive():
            return
        _BACKDROP_RECOVERY_STOP.clear()
        def runner():
            while not _BACKDROP_RECOVERY_STOP.is_set():
                try:
                    job=_BACKDROP_RECOVERY_QUEUE.get(timeout=0.8)
                except Exception:
                    continue
                if job is None: break
                if not job: continue
                credential,language,mt,tid=job
                key=(mt,int(tid))
                try:
                    paths=_canonical_paths(mt,tid);target=paths["backdrop"]
                    if _valid_backdrop(target):
                        _mark_backdrop_state(mt,tid,"real"); continue
                    client=TMDBClient(credential,language or "ar-EG",8)
                    details={}
                    try: details=client._get("/%s/%s"%key,{"language":language or "ar-EG"})
                    except Exception as exc:_art_log("safe recovery details %s/%s: %s"%(mt,tid,exc))
                    images=_all_tmdb_images(client,mt,tid)
                    canonical=_load_canonical(mt,tid); poster=canonical.get("poster_local")
                    resolved,fp,url,source=_resolve_backdrop_v4(client,language,mt,tid,details,images,poster,target,None)
                    if resolved and source != "poster_synthetic" and _valid_backdrop(target):
                        data=_load_canonical(mt,tid);data.update({"backdrop_path":fp,"backdrop_url":url,"backdrop_local":target,"backdrop_source":source,"backdrop_state":"real"});_save_canonical(mt,tid,data)
                        _mark_backdrop_state(mt,tid,"real",backdrop_source=source)
                        _art_log("SAFE recovery OK %s/%s source=%s"%(mt,tid,source))
                    else:
                        _mark_backdrop_state(mt,tid,"pending",next_retry_at=int(time.time())+180)
                        _art_log("SAFE recovery pending %s/%s"%(mt,tid))
                except Exception as exc:
                    _art_log("SAFE recovery error %s/%s: %s"%(mt,tid,exc))
                finally:
                    with _BACKDROP_RECOVERY_LOCK:_BACKDROP_RECOVERY_ACTIVE.discard(key)
        _BACKDROP_RECOVERY_THREAD=threading.Thread(target=runner,name="UltraBackdropSafeQueue")
        _BACKDROP_RECOVERY_THREAD.daemon=True;_BACKDROP_RECOVERY_THREAD.start()

def _schedule_backdrop_recovery(credential, language, media_type, tmdb_id):
    """Queue at most one real-backdrop recovery at a time for the whole plugin."""
    if not credential or not tmdb_id:return
    mt="tv" if str(media_type or "").lower()=="tv" else "movie"; key=(mt,int(tmdb_id))
    with _BACKDROP_RECOVERY_LOCK:
        if key in _BACKDROP_RECOVERY_ACTIVE:return
        _BACKDROP_RECOVERY_ACTIVE.add(key)
    _mark_backdrop_state(mt,int(tmdb_id),"pending")
    _ensure_backdrop_recovery_worker()
    try:_BACKDROP_RECOVERY_QUEUE.put_nowait((credential,language,mt,int(tmdb_id)))
    except Exception:
        with _BACKDROP_RECOVERY_LOCK:_BACKDROP_RECOVERY_ACTIVE.discard(key)


def shutdown_artwork_workers(wait=False, timeout=2.5):
    global _BACKDROP_RECOVERY_THREAD
    _BACKDROP_RECOVERY_STOP.set(); q=_BACKDROP_RECOVERY_QUEUE
    if q is not None:
        try:q.put_nowait(None)
        except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
    thread=_BACKDROP_RECOVERY_THREAD
    if wait and thread is not None and thread.is_alive():thread.join(max(0.0,float(timeout or 0.0)))
    with _BACKDROP_RECOVERY_LOCK:_BACKDROP_RECOVERY_ACTIVE.clear()
    if thread is None or not thread.is_alive():_BACKDROP_RECOVERY_THREAD=None;return True
    return False

def _lock(key):
    with _LOCK_GUARD:
        obj = _LOCKS.get(key)
        if obj is None:
            obj = threading.RLock(); _LOCKS[key] = obj
        return obj


def _valid(path, min_bytes=1024):
    try:
        if path and os.path.abspath(str(path)).startswith(os.path.abspath(ROOT) + os.sep) and not hdd_read_ready(force=True):
            return False
        if not path or not os.path.isfile(path) or os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as h: head = h.read(16)
        return bool(head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))
    except Exception:
        return False


def _canonical_dir(media_type, tmdb_id, create=False):
    mt = "tv" if str(media_type or "").lower() == "tv" else "movie"
    root = V3_TV_ROOT if mt == "tv" else V3_MOVIE_ROOT
    path = os.path.join(root, str(int(tmdb_id)))
    if create:
        ensure_persistent_dirs(root, path)
    return path


def _canonical_paths(media_type, tmdb_id):
    base = _canonical_dir(media_type, tmdb_id)
    return {
        "dir": base,
        "poster": os.path.join(base, "poster.jpg"),
        "backdrop": os.path.join(base, "backdrop.jpg"),
        "manifest": os.path.join(base, "manifest.json"),
    }


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
        _write_require(target);os.replace(temp, target)
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
    if not tmdb_id or not hdd_read_ready(force=True):
        return {}
    return dict(_canonical_paths(media_type, tmdb_id))


def _load_canonical(media_type, tmdb_id):
    if not tmdb_id or not hdd_read_ready(force=True): return {}
    paths = _canonical_paths(media_type, tmdb_id)
    data = {}
    # us184 decoder repair: older builds could save a WebP/PNG TMDB payload
    # Safe194: canonical backdrops are direct TMDB JPEGs only.  Never invoke
    # Pillow merely to repair an old backdrop during a hot-path manifest read.
    try:
        bp=paths.get("backdrop")
        if bp and os.path.isfile(bp) and not _valid_backdrop(bp):
            try:
                if _write_ok(bp): os.unlink(bp)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
            try:
                marker=bp+".e2backdrop"
                if os.path.exists(marker) and _write_ok(marker): os.unlink(marker)
            except Exception as exc: diagnostic_failure("artwork.failsoft", exc)
            data["backdrop_local"]=None
            data["backdrop_state"]="pending"
            _art_log("SAFE194 dropped legacy/unsafe canonical backdrop %s/%s"%(media_type,tmdb_id))
    except Exception as exc:
        _art_log("SAFE194 canonical backdrop validation failed %s/%s: %s"%(media_type,tmdb_id,exc))
    if _valid_backdrop(paths["backdrop"]): data["backdrop_local"] = paths["backdrop"]
    elif data.get("backdrop_local") and not _valid(data.get("backdrop_local")): data["backdrop_local"] = None
    if data.get("backdrop_fallback_local") and not _valid(data.get("backdrop_fallback_local")):
        data["backdrop_fallback_local"] = None
    data["tmdb_id"] = int(tmdb_id)
    data["media_type"] = "tv" if str(media_type or "").lower() == "tv" else "movie"
    return data


def _save_canonical(media_type, tmdb_id, data):
    if not tmdb_id: return False
    paths = _canonical_paths(media_type, tmdb_id)
    payload = dict(data or {})
    payload.update({"schema": 3, "tmdb_id": int(tmdb_id), "media_type": "tv" if str(media_type or "").lower() == "tv" else "movie", "updated_at": int(time.time())})
    # Canonical manifests always point at canonical files, never ephemeral URL-hash paths.
    payload["poster_local"] = paths["poster"] if _valid(paths["poster"]) else None
    payload["backdrop_local"] = paths["backdrop"] if _valid_backdrop(paths["backdrop"]) else None
    return _atomic_json(paths["manifest"], payload)


def _promote_to_canonical(media_type, tmdb_id, data):
    if not tmdb_id: return dict(data or {})
    result = dict(data or {})
    paths = _canonical_paths(media_type, tmdb_id)
    p = result.get("poster_local")
    b = result.get("backdrop_local")
    cp = _copy_art_once(p, paths["poster"]) if _valid(p) else (paths["poster"] if _valid(paths["poster"]) else None)
    cb = _copy_art_once(b, paths["backdrop"]) if _valid(b) else (paths["backdrop"] if _valid(paths["backdrop"]) else None)
    if cp: result["poster_local"] = cp
    if cb: result["backdrop_local"] = cb
    _save_canonical(media_type, tmdb_id, result)
    return result


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
        _write_require(path);os.replace(temp, path)
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
        _write_require(path); os.replace(temp, path)
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


def identity_cache_compatible(item, data):
    """Reject cached TMDB identities that clearly belong to another title/year."""
    item=item if isinstance(item,dict) else {}
    data=data if isinstance(data,dict) else {}
    if not data or not data.get("tmdb_id"):
        return True

    current_year=_year_hint(item)
    cached_year=None
    try:
        raw_year=str(data.get("year") or "")
        cached_year=int(raw_year[:4]) if re.match(r"^(?:19|20)\\d{2}",raw_year) else None
    except Exception:
        cached_year=None
    if current_year and cached_year and abs(int(current_year)-int(cached_year))>=2:
        return False

    current=[]
    current_primary=[]
    for key in ("_raw_name","name","title","display_name","movie_name","series_name","original_name","original_title"):
        value=str(item.get(key) or "").strip()
        if not value:
            continue
        cleaned=catalogue_title(value)
        cn=_norm(cleaned)
        if cn and cn not in current_primary:
            current_primary.append(cn)
        for q in (tmdb_search_aliases(cleaned) or [cleaned]):
            q_clean=catalogue_title(q)
            n=_norm(q_clean)
            if n and n not in current:
                current.append(n)

    stored=[]
    stored_raw=[]
    for key in ("title","original_title","name","original_name"):
        raw=str(data.get(key) or "").strip()
        n=_norm(raw)
        if n and n not in stored_raw:
            stored_raw.append(n)
        cleaned=catalogue_title(raw)
        n2=_norm(cleaned)
        if n2 and n2 not in stored:
            stored.append(n2)

    generic=frozenset(("pure","movie","movies","film","films","vod","series","tv"))
    meaningful_current=[x for x in current_primary if x not in generic]
    if meaningful_current and stored_raw and all(x in generic for x in stored_raw):
        return False

    if not current or not stored:
        return not meaningful_current

    for c in current:
        for s in stored:
            if c==s:
                return True
            shorter=min(len(c),len(s))
            if shorter>=7 and (c in s or s in c):
                ct=set(c.split());st=set(s.split())
                overlap=float(len(ct & st))/float(max(1,min(len(ct),len(st))))
                if overlap>=0.66:
                    return True
    return False

def load_manifest(profile, media_type, item):
    # Never inspect a shadow /media/hdd directory on receiver rootfs.
    if not hdd_read_ready(force=True):
        return {}
    path = _manifest_path(profile, media_type, item)
    try:
        with open(path, "r", encoding="utf-8") as h: data = json.load(h)
        if not isinstance(data, dict): return {}
    except Exception:
        return {}
    # Never erase a previously decoded image merely because matcher/schema code
    # changed.  Persisted bytes are sticky; identity can be refreshed separately.
    p = str(data.get("poster_local") or "")
    b = str(data.get("backdrop_local") or "")
    if p and not _valid(p): data["poster_local"] = None
    if b and not _valid(b): data["backdrop_local"] = None
    tmdb_id = data.get("tmdb_id")
    mt = str(data.get("media_type") or _media_type(media_type))
    if tmdb_id and not identity_cache_compatible(item,data):
        data["poster_local"]=None
        data["backdrop_local"]=None
        data["tmdb_id"]=None
        data["matched"]=False
        data["identity_verified"]=False
        data["_identity_stale"]=True
        data["_needs_metadata_refresh"]=True
        return data
    if tmdb_id:
        canonical = _load_canonical(mt, tmdb_id)
        # One-time migration from every historical item-specific path into V3.
        # Also probe the old deterministic v2 targets because some us builds
        # saved URL metadata after accidentally clearing *_local from manifests.
        legacy_poster = os.path.join(POSTER_ROOT, "%s_%s.jpg" % (mt, tmdb_id))
        legacy_backdrop = os.path.join(BACKDROP_ROOT, "%s_%s.jpg" % (mt, tmdb_id))
        if not data.get("poster_local") and _valid(legacy_poster): data["poster_local"] = legacy_poster
        if not data.get("backdrop_local") and _valid(legacy_backdrop): data["backdrop_local"] = legacy_backdrop
        if not canonical.get("poster_local") and data.get("poster_local"):
            canonical = _promote_to_canonical(mt, tmdb_id, data)
        if not canonical.get("backdrop_local") and data.get("backdrop_local"):
            canonical = _promote_to_canonical(mt, tmdb_id, data)
        for key, value in canonical.items():
            if value not in (None, ""): data[key] = value
    # Older schemas keep their safe local art and verified id.  Mark metadata for
    # refresh rather than quarantining/hiding artwork from the UI.
    if int(data.get("schema") or 0) != _SCHEMA:
        data["_needs_metadata_refresh"] = True
    return data

def save_manifest(profile, media_type, item, data):
    payload = dict(data or {})
    tmdb_id = payload.get("tmdb_id")
    mt = str(payload.get("media_type") or _media_type(media_type))
    if tmdb_id:
        payload = _promote_to_canonical(mt, tmdb_id, payload)
    payload["schema"] = _SCHEMA
    payload["updated_at"] = int(time.time())
    payload.pop("_needs_metadata_refresh", None)
    return _atomic_json(_manifest_path(profile, media_type, item), payload)

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
        # Prefer enough resolution for the FHD poster frame, but don't reward
        # giant originals so much that a low-vote scan beats a clean poster.
        width = min(1200, int(r.get("width") or 0))
        return (lang_bonus, width, float(r.get("vote_average") or 0), int(r.get("vote_count") or 0))
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
            # Poster UI never exceeds ~400 px wide; 600 keeps excellent quality
            # while cutting decoder/memory cost on Vu+ receivers.
            if image.width > 600:
                ratio = 600.0 / float(max(1, image.width))
                h = max(1, int(round(image.height * ratio)))
                resampling = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", 1)
                image = image.resize((600, h), resampling)
            _write_require(temp);image.save(temp, "JPEG", quality=92, optimize=False, progressive=False)
        _write_require(target_path);os.replace(temp, target_path)
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
            _write_require(temp);image.save(temp, "JPEG", quality=92, optimize=False, progressive=False, subsampling=1)
        _write_require(target_path);os.replace(temp, target_path)
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
            if parts.scheme.lower() != "https" or (parts.hostname or "").lower() != "image.tmdb.org":
                _art_log("blocked non-TMDB artwork URL: %s" % url); return None
            req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.9,*/*;q=0.1", "Connection":"close"})
            opener = build_safe_https_media_opener(allowed_hosts=("image.tmdb.org",))
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
                        _write_require(target);os.replace(temp, target)
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

def _safe_catalogue_query(value):
    q=catalogue_title(value)
    q=str(q or "").strip()
    nq=_norm(q)
    if not nq or nq in _GENERIC_SEARCH_QUERIES:
        return ""
    if len(re.sub(r"\W+","",nq,flags=re.UNICODE)) < 2:
        return ""
    return q

class ArtworkV2(object):
    def __init__(self, credential, language="ar-EG", timeout=4):
        self.language = str(language or "ar-EG")
        self.lang = self.language.split("-")[0].lower()
        self.timeout = max(2, min(6, int(timeout or 4)))
        self.tmdb = TMDBClient(credential, self.language, self.timeout)

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
        if locked:
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
            try:
                candidate=self.tmdb._get("/%s/%s"%(mt,provider_direct),{"language":self.language}) or {}
                candidate_name=str(candidate.get("name") or candidate.get("title") or "")
                candidate_original=str(candidate.get("original_name") or candidate.get("original_title") or "")
                current_title=catalogue_title(item.get("_raw_name") or _title(item))
                qn=_norm(current_title)
                names=[_norm(candidate_name),_norm(candidate_original)]
                names=[x for x in names if x]
                exact=bool(qn and qn in names)
                wanted_year=_year_hint(item)
                result_year=_year(candidate.get("first_air_date") if mt=="tv" else candidate.get("release_date"))
                year_ok=bool(not wanted_year or not result_year or abs(int(wanted_year)-int(result_year))<=1)
                score=_score(current_title,candidate,mt,wanted_year,mt,_country_hint(item,_title(item)),
                             str(item.get("description") or item.get("plot") or ""))
                q_ar=bool(re.search(r"[\u0600-\u06ff]",current_title))
                result_ar=bool(re.search(r"[\u0600-\u06ff]",candidate_name+" "+candidate_original))
                script_ok=bool((not q_ar) or result_ar or exact)
                trusted=bool(exact or (score>=0.84 and year_ok and script_ok))
                if trusted:
                    _trace_identity("provider_tmdb_verified",item,tmdb_id=provider_direct,result=candidate_name,score=round(score,3))
                    return mt,int(provider_direct),max(0.90,float(score)),"provider_tmdb_verified",candidate
                _trace_identity("provider_tmdb_rejected",item,tmdb_id=provider_direct,result=candidate_name,score=round(score,3))
            except Exception as exc:
                _art_log("provider TMDB id validation failed id=%r: %s"%(provider_direct,exc))

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
        if not isinstance(data,dict):
            return False
        if data.get("_metadata_complete"):
            return True
        overview=str(data.get("overview") or "").strip()
        credits=bool(data.get("cast") or data.get("directors") or data.get("writers"))
        return bool(overview and credits)

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
        locked=str(item.get("_locked_tmdb_id") or "").strip()
        if re.fullmatch(r"\d{1,12}",locked):
            try:
                candidate=self.tmdb._get("/%s/%s"%(mt,int(locked)),{"language":self.language}) or {}
                ok,_=strong_match(candidate)
                if ok or not title_sources:
                    tmdb_id=int(locked);seed=candidate;identity_source="locked_tmdb"
            except Exception as exc:
                _art_log("backdrop-only locked id failed %s/%s: %s"%(mt,locked,exc))

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

        # 4) Strict title search. Exact localized/original title wins; otherwise
        # require a very high score plus year agreement.
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
            for query in queries[:10]:
                if cancelled():break
                params={"query":query,"language":self.language,"include_adult":"false","page":1}
                if wanted_year:
                    params["first_air_date_year" if mt=="tv" else "year"]=wanted_year
                try:
                    payload=self.tmdb._get("/search/%s"%mt,params) or {}
                except Exception as exc:
                    _art_log("backdrop-only search failed %r: %s"%(query,exc));continue
                ranked=[]
                for row in (payload.get("results") or [])[:8]:
                    ok,score=strong_match(row)
                    if ok:
                        ranked.append((score,row))
                if ranked:
                    ranked.sort(key=lambda x:x[0],reverse=True)
                    seed=ranked[0][1]
                    tmdb_id=int(seed.get("id"))
                    identity_source="strict_title_search"
                    break

        if not tmdb_id or cancelled():
            return {}

        canonical=_load_canonical(mt,tmdb_id)
        existing=str(canonical.get("backdrop_local") or "")
        if _valid_backdrop(existing):
            return {
                "matched":True,"identity_verified":True,"media_type":mt,"tmdb_id":tmdb_id,
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
        if not images:
            images=_all_tmdb_images(self.tmdb,mt,tmdb_id)

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
            "matched":True,"identity_verified":True,"media_type":mt,"tmdb_id":tmdb_id,
            "backdrop_local":resolved,"backdrop_path":fp,"backdrop_url":url,
            "backdrop_source":source or "gallery",
            "identity_source":"backdrop_only:%s"%identity_source,
        }

    def resolve(self, profile, media_type, item, full=False, cancel_event=None):
        hot = load_manifest(profile, media_type, item)
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
            if hot.get("poster_local") and (hot.get("backdrop_local") or not full) and (not full or self._full_metadata_ready(hot)):
                return hot

        # True hot path for already-linked item manifests.
        if hot.get("tmdb_id") and hot.get("poster_local") and (hot.get("backdrop_local") or not full) and (not full or self._full_metadata_ready(hot)):
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
            }
        if cancel_event is not None and cancel_event.is_set(): return hot

        # Canonical V3 hot path: once identity is known, every portal/screen uses
        # the same files. This bypasses URL-hash caches and prevents re-downloads.
        canonical = _load_canonical(mt, tmdb_id)
        if canonical.get("poster_local"):
            hot["poster_local"] = canonical.get("poster_local")
        if canonical.get("backdrop_local"):
            hot["backdrop_local"] = canonical.get("backdrop_local")
        for _k in ("poster_url", "backdrop_url", "poster_path", "backdrop_path", "title", "overview", "year", "rating", "cast", "directors", "writers", "imdb_id", "_metadata_complete"):
            if canonical.get(_k) not in (None, "") and hot.get(_k) in (None, ""):
                hot[_k] = canonical.get(_k)

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
        poster_path = seed.get("poster_path") or hot.get("poster_path")
        backdrop_path = (seed.get("backdrop_path") or hot.get("backdrop_path")) if full else None
        base_details = {}
        # V4: one details call owns paths + seasons/collection + credits.  This
        # avoids racing a path-only request against a later metadata request and
        # gives the backdrop ladder everything it needs on the first open.
        if full or (not poster_path and not hot.get("poster_local")):
            try:
                params={"language": self.language}
                params["append_to_response"]="images" + (",credits,external_ids" if full else "")
                params["include_image_language"]="%s,en,null"%self.lang
                base_details = self.tmdb._get("/%s/%s" % (mt, tmdb_id), params)
            except Exception as exc:
                _art_log("V4 TMDB details lookup failed %s/%s: %s" % (mt, tmdb_id, exc))
                base_details = {}
            poster_path = poster_path or base_details.get("poster_path")
            if full: backdrop_path = backdrop_path or base_details.get("backdrop_path")

        images = {}
        if isinstance(base_details.get("images"),dict):
            images = base_details.get("images") or {}
        if full and not hot.get("backdrop_local"):
            # Prefer appended images from the details round-trip; only fall back
            # to a second unfiltered /images call if that payload is empty.
            if not images:
                images = _all_tmdb_images(self.tmdb, mt, tmdb_id)
            back_candidates = _ranked_backdrops(images.get("backdrops"), self.lang, 14)
            back = back_candidates[0] if back_candidates else None
            if back and back.get("file_path"):
                backdrop_path = back.get("file_path")
        if not poster_path and not hot.get("poster_local"):
            if not images:
                images = _all_tmdb_images(self.tmdb, mt, tmdb_id)
            post = _best_image(images.get("posters"), True, self.lang)
            poster_path = (post or {}).get("file_path") if post else None
        poster_url = TMDBClient.image_url(poster_path, "w500") if poster_path else None
        # us196 high-quality safe ladder resolves original Full-HD first, then w1280.
        backdrop_url = TMDBClient.image_url(backdrop_path, "original") if backdrop_path else None
        _art_log("resolve %s/%s source=%s poster=%s backdrop=%s" % (mt, tmdb_id, identity_source, bool(poster_url), bool(backdrop_url)))
        canonical_paths = _canonical_paths(mt, tmdb_id)
        poster_target = canonical_paths["poster"]
        backdrop_target = canonical_paths["backdrop"]
        poster_local = hot.get("poster_local") if _valid(hot.get("poster_local")) else None
        backdrop_local = hot.get("backdrop_local") if _valid_backdrop(hot.get("backdrop_local")) else None

        # Never download artwork anywhere except the mounted HDD.  If the disk
        # is late during boot, return what is already known and let a later screen
        # retry after /media/hdd is mounted.  No /tmp, /etc or USB shadow cache.
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
                "backdrop_state": "real" if backdrop_local else ("pending" if full else str(hot.get("backdrop_state") or "unknown")),
                "artwork_storage": "hdd_wait",
            })
            return result

        if poster_url and not poster_local and not (cancel_event is not None and cancel_event.is_set()):
            # First try the canonical details poster. w500 is fast and receiver-safe.
            poster_local = _download(poster_url, poster_target, min(4.2, max(3.2,self.timeout)), normalize_poster=True)

        # Strong poster rescue for verified TMDB identities. Always use the
        # UNFILTERED gallery so Arabic/Asian/English titles can fall through to
        # localized -> English -> language-neutral -> original posters.
        if not poster_local and not (cancel_event is not None and cancel_event.is_set()):
            gallery = _all_tmdb_images(self.tmdb, mt, tmdb_id)
            if gallery:
                images = gallery
            candidates = _ranked_posters((images or {}).get("posters"), self.lang, 8)
            attempted = set([str(poster_path or "")])
            for n, row in enumerate(candidates):
                if cancel_event is not None and cancel_event.is_set(): break
                fp = str((row or {}).get("file_path") or "")
                if not fp or fp in attempted: continue
                attempted.add(fp)
                # Some CDN edges occasionally fail one size while another works.
                # Try a high-quality receiver-sized image, then the universal w500.
                for size in ("w780","w500"):
                    if cancel_event is not None and cancel_event.is_set(): break
                    alt_url = TMDBClient.image_url(fp, size)
                    if not alt_url: continue
                    local = _download(alt_url, poster_target, min(4.5, max(3.2,self.timeout)), normalize_poster=True)
                    if local:
                        poster_local = local
                        poster_url = alt_url
                        poster_path = fp
                        _art_log("poster rescue selected %s/%s candidate=%s size=%s lang=%s" % (mt, tmdb_id, n + 1, size, str((row or {}).get("iso_639_1") or "null")))
                        break
                if poster_local:
                    break

        backdrop_fallback_local = hot.get("backdrop_fallback_local") if _valid(hot.get("backdrop_fallback_local")) else None
        backdrop_source = str(hot.get("backdrop_source") or "")
        if full and not backdrop_local and not (cancel_event is not None and cancel_event.is_set()):
            if not images:
                images = _all_tmdb_images(self.tmdb, mt, tmdb_id)
            resolved, resolved_fp, resolved_url, resolved_source = _resolve_backdrop_v4(
                self.tmdb, self.language, mt, tmdb_id, base_details, images,
                poster_local, backdrop_target, cancel_event
            )
            if resolved and resolved_source == "poster_synthetic":
                backdrop_fallback_local = resolved
                backdrop_source = resolved_source
                _mark_backdrop_state(mt,tmdb_id,"pending",fallback="poster_synthetic",next_retry_at=int(time.time())+120)
                _schedule_backdrop_recovery(self.tmdb.credential,self.language,mt,tmdb_id)
            elif resolved:
                backdrop_local = resolved
                backdrop_path = resolved_fp or backdrop_path
                backdrop_url = resolved_url or backdrop_url
                backdrop_source = resolved_source or "gallery"
                _mark_backdrop_state(mt,tmdb_id,"real",backdrop_source=backdrop_source,retry_count=0)
            else:
                _mark_backdrop_state(mt,tmdb_id,"pending",next_retry_at=int(time.time())+60)
                _schedule_backdrop_recovery(self.tmdb.credential,self.language,mt,tmdb_id)

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
            "backdrop_fallback_local": backdrop_fallback_local,
            "backdrop_source": backdrop_source,
            "backdrop_state": "real" if backdrop_local else str(hot.get("backdrop_state") or ("pending" if full else "unknown")),
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
                cast = [str(x.get("name")) for x in (credits.get("cast") or [])[:6] if isinstance(x, dict) and x.get("name")]
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
                    "cast": cast, "directors": directors[:3], "writers": writers[:4], "countries": countries[:3],
                    "imdb_id": str(ext.get("imdb_id") or _imdb_id(item) or ""),
                    "_metadata_complete": True,
                })
        save_manifest(profile, media_type, item, result)
        return result
