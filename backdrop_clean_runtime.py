# -*- coding: utf-8 -*-
"""Ultra Stalker official TMDb primary-backdrop upgrader (R93).

This module never resolves a title by name.  It operates only on the already
verified TMDb id stored by the existing R90 artwork manifest, asks TMDb for that id's primary ``backdrop_path`` from the title Details
response, and atomically replaces only that title's canonical backdrop master.
The image gallery is fallback-only when the Details response has no backdrop.

The UI/presentation layers remain responsible for rebuilding their own derived
surfaces when the canonical source signature changes.
"""
from __future__ import absolute_import

import os
import threading

CLEAN_BACKDROP_SELECTOR_VERSION = 3
TARGET_RATIO = 16.0 / 9.0


def _lang(value):
    return str(value or "en").split("-")[0].lower()


def rank_clean_backdrops(rows, preferred_lang="en", limit=16):
    valid=[]
    for row in (rows or []):
        if not isinstance(row,dict) or not row.get("file_path"):
            continue
        try:
            w=int(row.get("width") or 0); h=int(row.get("height") or 0)
        except Exception:
            w=h=0
        if h <= 0 or w < h:
            continue
        valid.append(row)
    lang=_lang(preferred_lang)
    def key(row):
        iso=str(row.get("iso_639_1") or "").strip().lower()
        neutral=1 if not iso else 0
        local=1 if iso and iso==lang else 0
        english=1 if iso=="en" else 0
        try:
            w=int(row.get("width") or 0); h=int(row.get("height") or 0)
        except Exception:
            w=h=0
        try: votes=int(row.get("vote_count") or 0)
        except Exception: votes=0
        try: avg=float(row.get("vote_average") or 0.0)
        except Exception: avg=0.0
        ratio=(float(w)/float(h)) if h else 0.0
        ratio_score=max(-2.0,1.0-abs(ratio-TARGET_RATIO))
        pixels=min(max(0,w*h),16000000)
        # Textless/neutral is decisive.  Ratio and quality only order within the
        # same language class, avoiding a large localized title-card beating a
        # clean neutral backdrop.
        return (neutral,local,english,ratio_score,votes,avg,pixels)
    valid.sort(key=key,reverse=True)
    out=[];seen=set()
    for row in valid:
        fp=str(row.get("file_path") or "")
        if not fp or fp in seen:
            continue
        seen.add(fp);out.append(row)
        if len(out)>=max(1,int(limit or 16)):
            break
    return out


def _cancelled(cancel_event):
    return bool(cancel_event is not None and cancel_event.is_set())


def status(profile, media_type, item):
    try:
        from .artwork_v2 import load_manifest, _valid_backdrop
        data=load_manifest(profile,media_type,item) or {}
        backdrop=str(data.get("backdrop_local") or "")
        return {
            "data":data,
            "tmdb_id":data.get("tmdb_id"),
            "ready":bool(int(data.get("clean_backdrop_selector_version") or 0)>=CLEAN_BACKDROP_SELECTOR_VERSION and _valid_backdrop(backdrop)),
            "backdrop_local":backdrop,
        }
    except Exception:
        return {"data":{},"tmdb_id":None,"ready":False,"backdrop_local":""}


def upgrade(profile, media_type, item, settings=None, cancel_event=None):
    """Upgrade one already-identified title to TMDb's primary title-page backdrop.

    Returns a small result dict.  No title search, provider search, Pillow decode,
    or UI work occurs here.  A failure leaves the old canonical backdrop intact.
    """
    if _cancelled(cancel_event):
        return {"attempted":False,"cancelled":True}
    settings=dict(settings or {})
    credential=str(settings.get("tmdb_credential") or "").strip()
    if not settings.get("tmdb_enabled",True) or not credential:
        return {"attempted":False,"reason":"tmdb_disabled"}
    language=str(settings.get("tmdb_language") or "ar-EG")
    try: timeout=min(9,max(4,int(settings.get("timeout",10) or 10)))
    except Exception: timeout=7

    # Import the implementation only inside the background worker.  This keeps
    # Home/menu startup identical to R90 and avoids waking the heavy resolver on
    # navigation merely to check a marker.
    try:
        from . import artwork_v2_impl as av
        from .tmdb import TMDBClient
    except Exception as exc:
        return {"attempted":False,"reason":"import","error":str(exc)}

    try:
        data=av.load_manifest(profile,media_type,item) or {}
        tmdb_id=data.get("tmdb_id")
        mt=str(data.get("media_type") or av._media_type(media_type))
        old_local=str(data.get("backdrop_local") or "")
        if not tmdb_id:
            return {"attempted":False,"reason":"no_verified_tmdb_id"}
        if int(data.get("clean_backdrop_selector_version") or 0)>=CLEAN_BACKDROP_SELECTOR_VERSION and av._valid_backdrop(old_local):
            return {"attempted":False,"ready":True,"changed":False,"data":data}
        if _cancelled(cancel_event):
            return {"attempted":False,"cancelled":True}

        client=TMDBClient(credential,language,timeout)
        # R93: the canonical page backdrop is the authority.  Do not search or
        # score dozens of gallery stills when TMDb already exposes the image it
        # selected for the title page itself.  Omit a language parameter here so
        # TMDb returns its normal primary backdrop choice.
        details=client._get("/%s/%s"%(mt,tmdb_id),{}) or {}
        file_path=str(details.get("backdrop_path") or "").strip()
        iso=""
        choice_source="tmdb_primary"
        if not file_path:
            # Fallback only for the rare title whose Details payload has no
            # primary backdrop.  Keep R92's safe neutral gallery ordering here.
            images=client._get("/%s/%s/images"%(mt,tmdb_id),{}) or {}
            ranked=rank_clean_backdrops(images.get("backdrops") or [],language,16)
            if ranked:
                choice=ranked[0]
                file_path=str(choice.get("file_path") or "")
                iso=str(choice.get("iso_639_1") or "")
                choice_source="gallery_fallback"
        if not file_path:
            # Nothing usable on TMDb. Seal the marker only when the current
            # canonical backdrop is physically valid, avoiding repeated network
            # work without blanking a title that already has artwork.
            if av._valid_backdrop(old_local):
                canonical=av._load_canonical(mt,tmdb_id) or dict(data)
                canonical["clean_backdrop_selector_version"]=CLEAN_BACKDROP_SELECTOR_VERSION
                canonical["clean_backdrop_choice"]="existing_no_tmdb_primary"
                av._save_canonical(mt,tmdb_id,canonical)
                canonical=av.load_manifest(profile,media_type,item) or canonical
                return {"attempted":True,"ready":True,"changed":False,"data":canonical,"reason":"no_tmdb_primary"}
            return {"attempted":True,"ready":False,"changed":False,"reason":"no_tmdb_primary"}
        canonical=av._load_canonical(mt,tmdb_id) or dict(data)
        old_fp=str(canonical.get("backdrop_path") or data.get("backdrop_path") or "")
        paths=av._canonical_paths(mt,tmdb_id)
        target=str(paths.get("backdrop") or old_local or "")
        if not target:
            return {"attempted":True,"ready":False,"changed":False,"reason":"no_target"}

        # Same TMDb image: only seal the selector marker.  No byte rewrite and no
        # presentation invalidation is needed.
        if file_path and file_path==old_fp and av._valid_backdrop(target):
            canonical.update({
                "backdrop_local":target,
                "clean_backdrop_selector_version":CLEAN_BACKDROP_SELECTOR_VERSION,
                "clean_backdrop_choice":choice_source,
                "clean_backdrop_iso":iso,
            })
            av._save_canonical(mt,tmdb_id,canonical)
            fresh=av.load_manifest(profile,media_type,item) or canonical
            return {"attempted":True,"ready":True,"changed":False,"data":fresh,"file_path":file_path}

        if _cancelled(cancel_event):
            return {"attempted":False,"cancelled":True}
        url=TMDBClient.image_url(file_path,"w1280")
        if not url:
            return {"attempted":True,"ready":False,"changed":False,"reason":"no_url"}
        temp=target+".official93.%d.%d.jpg"%(os.getpid(),threading.get_ident())
        try:
            if os.path.exists(temp): os.unlink(temp)
        except Exception:
            pass
        got=av._download_backdrop_safe(url,temp,timeout,max_width=1920,max_height=1080)
        if not got or not av._valid_backdrop(temp):
            try:
                if os.path.exists(temp): os.unlink(temp)
            except Exception:
                pass
            return {"attempted":True,"ready":bool(av._valid_backdrop(old_local)),"changed":False,"reason":"download_failed"}
        if _cancelled(cancel_event):
            try:
                if os.path.exists(temp): os.unlink(temp)
            except Exception:
                pass
            return {"attempted":False,"cancelled":True}

        # Atomic swap: the old visible backdrop survives until the complete new
        # JPEG has been validated.  Replace only this title's canonical master.
        av._write_require(target)
        os.replace(temp,target)
        av._fsync_parent(target)
        canonical.update({
            "backdrop_local":target,
            "backdrop_path":file_path,
            "backdrop_url":url,
            "backdrop_source":"tmdb_primary:w1280" if choice_source=="tmdb_primary" else "tmdb_gallery_fallback:w1280",
            "backdrop_state":"real",
            "clean_backdrop_selector_version":CLEAN_BACKDROP_SELECTOR_VERSION,
            "clean_backdrop_choice":choice_source,
            "clean_backdrop_iso":iso,
        })
        av._save_canonical(mt,tmdb_id,canonical)
        fresh=av.load_manifest(profile,media_type,item) or canonical
        return {
            "attempted":True,"ready":True,"changed":True,"data":fresh,
            "tmdb_id":tmdb_id,"media_type":mt,"file_path":file_path,"backdrop_local":target,
        }
    except Exception as exc:
        return {"attempted":True,"ready":False,"changed":False,"error":str(exc)}
