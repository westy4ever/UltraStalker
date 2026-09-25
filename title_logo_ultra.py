# -*- coding: utf-8 -*-
"""Unified Ultra Stalker title-logo service.

One bounded background implementation serves every title-logo presentation.
TMDb is source #1, Fanart.tv is source #2, and every network hit is written
through title_logo_runtime at display-safe size.  Screens keep their historical
widget geometry; this module only owns identity/source/cache/presentation.

Home deliberately does not call this service.
"""
from __future__ import absolute_import

import os
import re
import threading

from .title_logo_runtime import (
    fetch_prepare_title_logo_candidates,
    normalize_title_logo_url,
    prepare_title_logo_file,
    title_logo_policy_languages,
    title_logo_policy_metadata_known,
    title_logo_provider,
    ultra_title_logo_cache_path,
    ultra_title_logo_fallback_path,
    valid_ultra_title_logo,
)
from .storage import load_settings, load_api_keys
from .tmdb import TMDBClient
from .fanart import fetch_artwork as _fanart_fetch
from .artwork_v2 import ArtworkV2, identity_cache_compatible
from .ui_artwork_helpers import _strip_portal_artwork
from .title_clean import catalogue_title
from .persistent_cache import GENERATED

MASTER_CANVAS = (900, 210)
# Point 2 speed fix: serialize only the SAME title identity.  The previous single
# process-wide lock made the newly focused title wait behind a stale 3-7 second
# network request from the title the user had already left.
# Fixed-size lock stripes avoid both the old process-wide bottleneck and an
# ever-growing per-title lock table on large catalogues. Same identity hashes to
# the same stripe; unrelated titles normally resolve in parallel.
_SERVICE_LOCKS = tuple(threading.Lock() for _ in range(32))

def _service_lock(media_type,item,row,visible_title=""):
    ident=ultra_title_logo_identity_id(item,row)
    if ident not in (None,""):
        key=(str(_media_type(media_type)),str(ident))
    else:
        key=(str(_media_type(media_type)),"title:"+str(visible_title or "").strip().lower())
    return _SERVICE_LOCKS[hash(key) % len(_SERVICE_LOCKS)]


def _cancelled(ev):
    try:
        return bool(ev is not None and ev.is_set())
    except Exception:
        return False


def _media_type(media_type):
    return "series" if str(media_type or "").lower() in ("series", "tv", "episode") else "vod"


def _tmdb_media_type(media_type):
    return "tv" if _media_type(media_type) == "series" else "movie"


def _merge_nonempty(dst, src):
    out=dict(dst or {})
    if isinstance(src,dict):
        for key,value in src.items():
            if value not in (None,"",[],{}):out[key]=value
    return out


def _visible_title(item,row,visible_title=""):
    value=str(visible_title or "").strip()
    if value:return value
    row=row if isinstance(row,dict) else {}
    item=item if isinstance(item,dict) else {}
    for value in (
        row.get("original_title"),row.get("title"),row.get("name"),
        item.get("_series_title"),item.get("name"),item.get("title"),
    ):
        text=str(value or "").strip()
        if text:
            try:return catalogue_title(text) or text
            except Exception:return text
    return ""





def _logo_language_order(item,row=None,visible_title=""):
    """Identity-owned Stage-5 policy; visible title is intentionally ignored."""
    return title_logo_policy_languages(item,row)


def _pgv2_legacy_logo_cache_path(media_type,tmdb_id,lang):
    """Read-only bridge to the proven pre-unified PGV2 cache."""
    try:
        mt="tv" if _media_type(media_type)=="series" else "movie"
        return os.path.join(GENERATED,"pgv2_title_logos","%s_%s_%s_900x125_v1.png"%(mt,int(tmdb_id),str(lang or "en")))
    except Exception:
        return ""


def _adopt_cached_logo_source(media_type,tmdb_id,lang,canvas_size):
    """Worker-only bridge from any proven Ultra/PGV2 logo canvas.

    PGV2 historically owns a 900x125 cache while Cinematic/Backdrop/Details
    request other canvases.  Reuse that already-proven transparent logo locally
    before touching the network, then write the normal shared master/variant.
    """
    try:
        target=ultra_title_logo_cache_path(media_type,tmdb_id,lang,canvas_size)
        if valid_ultra_title_logo(target):return target
        master=ultra_title_logo_cache_path(media_type,tmdb_id,lang,MASTER_CANVAS)
        if valid_ultra_title_logo(master):return _variant_from_master(master,target,canvas_size)
        # Stage 5 intentionally ignores pre-policy PGV2 caches: older builds could
        # write an Arabic/other-language image into an ``en`` lane.  Only the new
        # versioned Ultra cache is safe to cross-adopt.
        sources=[
            ultra_title_logo_cache_path(media_type,tmdb_id,lang,(900,125)),
            ultra_title_logo_cache_path(media_type,tmdb_id,lang,(420,144)),
            ultra_title_logo_cache_path(media_type,tmdb_id,lang,(432,210)),
        ]
        for source in sources:
            if not valid_ultra_title_logo(source):continue
            if os.path.abspath(str(source))==os.path.abspath(str(master)):
                break
            mx,my=_variant_margins(MASTER_CANVAS)
            prepared=prepare_title_logo_file(source,master,canvas_size=MASTER_CANVAS,margin_x=mx,margin_y=my)
            if valid_ultra_title_logo(prepared):
                return _variant_from_master(master,target,canvas_size)
    except Exception:
        pass
    return ""


def _fill_logo_policy_metadata(client,media_type,tmdb_id,fresh):
    """Fill only identity metadata required by the logo-language policy."""
    if client is None or not tmdb_id or not isinstance(fresh,dict):return fresh
    if fresh.get("original_language") and (fresh.get("origin_country") or fresh.get("countries")):
        return fresh
    try:
        mt=_tmdb_media_type(media_type)
        details=client._get("/%s/%s"%(mt,int(tmdb_id)),{"language":"en-US"}) or {}
        if isinstance(details,dict):
            for key in ("original_language","origin_country","production_countries","original_title","original_name"):
                value=details.get(key)
                if value in (None,"",[],{}):continue
                if key=="production_countries":
                    if not fresh.get("countries"):fresh["countries"]=value
                elif not fresh.get(key):
                    fresh[key]=value
    except Exception:
        pass
    return fresh


def ultra_title_logo_identity_id(item,row=None):
    """Return the strongest already-known TMDb id for title-logo ownership.

    Runtime verified/locked ids deliberately outrank the raw provider/cache
    ``tmdb_id``.  Player handoff can therefore never regress to an older
    provider id after Details/Backdrop Grid already verified the title.
    """
    item=item if isinstance(item,dict) else {}
    row=row if isinstance(row,dict) else {}
    for obj,key in ((item,"_player_title_logo_tmdb_id"),(row,"_player_title_logo_tmdb_id"),
                    (item,"_locked_tmdb_id"),(row,"_locked_tmdb_id"),
                    (row,"tmdb_id"),(item,"tmdb_id")):
        value=obj.get(key)
        if value not in (None,""):
            return value
    return None

def _variant_margins(canvas_size):
    try:w,h=int(canvas_size[0]),int(canvas_size[1])
    except Exception:w,h=432,210
    if (w,h)==(1220,72):return (24,4)
    if (w,h)==(900,125):return (40,13)
    if (w,h)==(420,144):return (16,12)
    if (w,h)==(432,210):return (28,24)
    return (max(12,int(w*0.045)),max(6,int(h*0.08)))


def _variant_from_master(master,target,canvas_size):
    if valid_ultra_title_logo(target):return target
    if not valid_ultra_title_logo(master):return ""
    if os.path.abspath(str(master))==os.path.abspath(str(target)):return master
    mx,my=_variant_margins(canvas_size)
    try:
        return prepare_title_logo_file(master,target,canvas_size=canvas_size,margin_x=mx,margin_y=my)
    except Exception:
        return ""


def ultra_title_logo_cached(media_type,item,row=None,canvas_size=(432,210),visible_title=""):
    """HDD-only lookup.  Never decodes or touches network."""
    item=item if isinstance(item,dict) else {}
    row=row if isinstance(row,dict) else {}
    visible=_visible_title(item,row,visible_title)
    tmdb_id=ultra_title_logo_identity_id(item,row)
    policy_known=title_logo_policy_metadata_known(item,row)
    try:
        if tmdb_id and policy_known:
            for lang in _logo_language_order(item,row,visible):
                path=ultra_title_logo_cache_path(media_type,tmdb_id,lang,canvas_size)
                if valid_ultra_title_logo(path):return path
            # R230: foreign fallback has its own cache lane so it can never
            # masquerade as English on later views/focuses.
            path=ultra_title_logo_cache_path(media_type,tmdb_id,"any",canvas_size)
            if valid_ultra_title_logo(path):return path
    except Exception:pass
    direct=normalize_title_logo_url(row.get("logo_url") or item.get("logo_url"))
    direct_lang=str(row.get("logo_language") or item.get("logo_language") or "").lower().split("-")[0].strip()
    allowed=_logo_language_order(item,row,visible)
    if policy_known and direct and direct_lang in allowed:
        path=ultra_title_logo_fallback_path(media_type,visible+"|"+direct,direct_lang,canvas_size)
        if valid_ultra_title_logo(path):return path
    return ""

def _resolve_identity(profile,media_type,item,row,cancel_event,cfg):
    fresh=dict(row or {})
    src=dict(item or {}) if isinstance(item,dict) else {}
    verified=ultra_title_logo_identity_id(src,fresh)
    if verified not in (None,""):
        fresh["tmdb_id"]=verified
        return fresh
    credential=str(cfg.get("tmdb_credential") or "").strip()
    if not cfg.get("tmdb_enabled",True) or not credential or _cancelled(cancel_event):return fresh
    resolver=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(7,max(3,int(cfg.get("timeout",10) or 10))))
    try:
        quick=resolver.resolve_backdrop_only(profile,_media_type(media_type),src,cancel_event=cancel_event) or {}
        if isinstance(quick,dict) and quick.get("tmdb_id"):
            try:compatible=identity_cache_compatible(src,quick)
            except Exception:compatible=True
            if compatible:return _merge_nonempty(fresh,quick)
    except Exception:pass
    if _cancelled(cancel_event):return fresh
    try:clean=dict(src) if src.get("_xtream") else _strip_portal_artwork(src)
    except Exception:clean=dict(src)
    for full in (False,True):
        if _cancelled(cancel_event):break
        try:
            found=resolver.resolve(profile,_media_type(media_type),clean,full=full,cancel_event=cancel_event) or {}
            if isinstance(found,dict) and found.get("tmdb_id"):
                try:compatible=identity_cache_compatible(src,found)
                except Exception:compatible=True
                if compatible:return _merge_nonempty(fresh,found)
        except Exception:pass
    return fresh


def resolve_ultra_title_logo(profile,media_type,item,row=None,canvas_size=(432,210),visible_title="",cancel_event=None,settings=None):
    """Return ``(prepared_path, resolved_row)`` from the shared Ultra pipeline.

    The resolver now carries the proven PGV2 discovery policy so Cinematic,
    Backdrop Grid 1/2, Details and Player see the same title-logo authority:
    canonical TMDb identity, identity-based language order, neutral-logo
    fallback, several TMDb candidates, Fanart fallback, and cross-view cache
    adoption.  Network/decode work remains serialized for weak receivers.
    """
    item=item if isinstance(item,dict) else {}
    fresh=dict(row or {}) if isinstance(row,dict) else {}
    visible=_visible_title(item,fresh,visible_title)
    cached=ultra_title_logo_cached(media_type,item,fresh,canvas_size,visible)
    if cached:return cached,fresh
    if _cancelled(cancel_event):return "",fresh
    cfg=dict(settings or load_settings() or {})
    # Logos are tiny w300 assets.  Do not let one dead provider hold the current
    # focus for the generic 7 second metadata timeout.
    timeout=3

    with _service_lock(media_type,item,fresh,visible):
        cached=ultra_title_logo_cached(media_type,item,fresh,canvas_size,visible)
        if cached:return cached,fresh
        if _cancelled(cancel_event):return "",fresh

        # Stage 5: never cache an unclassified direct logo before canonical
        # identity/origin metadata is known.  That old shortcut is how a dubbed
        # Arabic catalogue title could poison the English cache lane.
        fresh=_resolve_identity(profile,media_type,item,fresh,cancel_event,cfg)
        if _cancelled(cancel_event):return "",fresh
        tmdb_id=fresh.get("tmdb_id")
        if not tmdb_id:return "",fresh

        credential=str(cfg.get("tmdb_credential") or "").strip()
        mt=_tmdb_media_type(media_type)
        client=TMDBClient(credential,str(cfg.get("tmdb_language") or "en-US"),timeout) if cfg.get("tmdb_enabled",True) and credential else None
        fresh=_fill_logo_policy_metadata(client,media_type,tmdb_id,fresh)
        languages=_logo_language_order(item,fresh,visible)

        # ArtworkV2 may already carry a logo from its generic artwork lane.  Use
        # it only when TMDb explicitly labelled that image with one of the
        # languages allowed for this work.  Unknown/wrong-language direct URLs
        # are ignored and the strict gallery lookup below takes over.
        direct=normalize_title_logo_url(fresh.get("logo_url") or item.get("logo_url"))
        direct_lang=str(fresh.get("logo_language") or item.get("logo_language") or "").lower().split("-")[0].strip()
        if direct and direct_lang in languages and (fresh.get("identity_verified") or fresh.get("identity_pointer_verified")):
            master=ultra_title_logo_cache_path(media_type,tmdb_id,direct_lang,MASTER_CANVAS)
            target=ultra_title_logo_cache_path(media_type,tmdb_id,direct_lang,canvas_size)
            if not valid_ultra_title_logo(master):
                fetch_prepare_title_logo_candidates([direct],master,canvas_size=MASTER_CANVAS,timeout=timeout)
            path=_variant_from_master(master,target,canvas_size)
            if path:return path,fresh

        # First consume any successful logo another view already discovered.
        for logo_lang in languages:
            path=_adopt_cached_logo_source(media_type,tmdb_id,logo_lang,canvas_size)
            if path:return path,fresh
        # R230 cross-view adoption for a previously discovered foreign fallback.
        path=_adopt_cached_logo_source(media_type,tmdb_id,"any",canvas_size)
        if path:return path,fresh
        if _cancelled(cancel_event):return "",fresh

        try:
            keys=load_api_keys() or {}
            project=str(keys.get("FANART_API_KEY") or "").strip()
            personal=str(keys.get("FANART_CLIENT_KEY") or "").strip()
        except Exception:
            project="";personal=""

        direct=normalize_title_logo_url(fresh.get("logo_url") or item.get("logo_url"))
        direct_provider=title_logo_provider(direct)
        neutral_tmdb_urls=[]

        for logo_lang in languages:
            if _cancelled(cancel_event):return "",fresh
            master=ultra_title_logo_cache_path(media_type,tmdb_id,logo_lang,MASTER_CANVAS)
            target=ultra_title_logo_cache_path(media_type,tmdb_id,logo_lang,canvas_size)
            path=_variant_from_master(master,target,canvas_size)
            if path:return path,fresh

            tmdb_urls=[];fanart_urls=[];other_urls=[]
            if direct and direct_lang==logo_lang:
                if direct_provider=="tmdb":tmdb_urls.append(direct)
                elif direct_provider=="fanart":fanart_urls.append(direct)
                else:other_urls.append(direct)

            # Stage 5 is strict: only the requested explicit language may enter
            # this cache lane.  No Arabic/other/null logo can poison English.
            if client is not None and not _cancelled(cancel_event):
                try:
                    include=("%s,null"%logo_lang)
                    payload=client._get("/%s/%s/images"%(mt,int(tmdb_id)),{"include_image_language":include}) or {}
                    logos=payload.get("logos") if isinstance(payload,dict) else []
                    exact=[]
                    for logo in (logos or []):
                        if not isinstance(logo,dict):continue
                        fp=str(logo.get("file_path") or "").strip()
                        if not fp:continue
                        raw_iso=logo.get("iso_639_1")
                        iso=str(raw_iso or "").lower().strip()
                        url=TMDBClient.image_url(fp,"original")
                        if raw_iso in (None,""):
                            if url and url not in neutral_tmdb_urls:neutral_tmdb_urls.append(url)
                            continue
                        if iso!=logo_lang:continue
                        if url and url not in exact:exact.append(url)
                    for url in exact[:4]:
                        if url not in tmdb_urls:tmdb_urls.append(url)
                except Exception:
                    pass
            if _cancelled(cancel_event):return "",fresh
            path=fetch_prepare_title_logo_candidates(tmdb_urls,master,canvas_size=MASTER_CANVAS,timeout=timeout)

            if not path and not _cancelled(cancel_event) and (project or personal):
                try:
                    tvdb_id=str(fresh.get("tvdb_id") or fresh.get("thetvdb_id") or fresh.get("tvdb") or "").strip()
                    if mt=="tv" and not tvdb_id and client is not None:
                        try:
                            ext=client._get("/tv/%s/external_ids"%int(tmdb_id),{}) or {}
                            tvdb_id=str(ext.get("tvdb_id") or "").strip()
                        except Exception:tvdb_id=""
                    fa=_fanart_fetch(project,mt,tmdb_id=tmdb_id,tvdb_id=tvdb_id,timeout=timeout,personal_key=personal,logo_lang=logo_lang) or {}
                    url=normalize_title_logo_url(fa.get("title_logo_url"))
                    if url and url not in fanart_urls:fanart_urls.append(url)
                except Exception:
                    pass
                path=fetch_prepare_title_logo_candidates(fanart_urls,master,canvas_size=MASTER_CANVAS,timeout=timeout)

            if not path and other_urls and not _cancelled(cancel_event):
                path=fetch_prepare_title_logo_candidates(other_urls,master,canvas_size=MASTER_CANVAS,timeout=timeout)
            if valid_ultra_title_logo(master):
                return _variant_from_master(master,target,canvas_size),fresh

        # Expanded discovery fallback: many legitimate TMDb/Fanart clearlogos are
        # explicitly language-neutral (TMDb iso_639_1=null, Fanart lang=00).  They
        # are safe after every allowed explicit-language lane has failed because
        # they carry no conflicting language tag.  Wrong explicit languages remain
        # rejected.  Store the neutral hit in the first policy lane so every view
        # reuses the same prepared cache on the next focus.
        neutral_lang=(languages[0] if languages else "en")
        master=ultra_title_logo_cache_path(media_type,tmdb_id,neutral_lang,MASTER_CANVAS)
        target=ultra_title_logo_cache_path(media_type,tmdb_id,neutral_lang,canvas_size)
        neutral_urls=list(neutral_tmdb_urls[:6])
        if direct and not direct_lang and direct not in neutral_urls:
            neutral_urls.insert(0,direct)
        if not neutral_urls and client is not None and not _cancelled(cancel_event):
            try:
                payload=client._get("/%s/%s/images"%(mt,int(tmdb_id)),{"include_image_language":"null"}) or {}
                logos=payload.get("logos") if isinstance(payload,dict) else []
                ranked=[]
                for logo in (logos or []):
                    if not isinstance(logo,dict):continue
                    fp=str(logo.get("file_path") or "").strip()
                    iso=logo.get("iso_639_1")
                    if not fp or iso not in (None,""):continue
                    try:vote=float(logo.get("vote_average") or 0.0)
                    except Exception:vote=0.0
                    try:width=int(logo.get("width") or 0);height=int(logo.get("height") or 0)
                    except Exception:width=height=0
                    url=TMDBClient.image_url(fp,"original")
                    if url:ranked.append((vote,width*height,url))
                ranked.sort(reverse=True)
                for _vote,_area,url in ranked[:6]:
                    if url not in neutral_urls:neutral_urls.append(url)
            except Exception:
                pass
        path=fetch_prepare_title_logo_candidates(neutral_urls,master,canvas_size=MASTER_CANVAS,timeout=timeout)
        if not path and not _cancelled(cancel_event) and (project or personal):
            try:
                tvdb_id=str(fresh.get("tvdb_id") or fresh.get("thetvdb_id") or fresh.get("tvdb") or "").strip()
                if mt=="tv" and not tvdb_id and client is not None:
                    try:
                        ext=client._get("/tv/%s/external_ids"%int(tmdb_id),{}) or {}
                        tvdb_id=str(ext.get("tvdb_id") or "").strip()
                    except Exception:tvdb_id=""
                fa=_fanart_fetch(project,mt,tmdb_id=tmdb_id,tvdb_id=tvdb_id,timeout=timeout,personal_key=personal,logo_lang="00") or {}
                url=normalize_title_logo_url(fa.get("title_logo_url"))
                if url:path=fetch_prepare_title_logo_candidates([url],master,canvas_size=MASTER_CANVAS,timeout=timeout)
            except Exception:
                pass
        if valid_ultra_title_logo(master):
            return _variant_from_master(master,target,canvas_size),fresh

        # R230 last resort: if Arabic/English + neutral all failed, use the best
        # available EXPLICIT logo in any language.  Keep it in a dedicated
        # ``any`` lane so it never contaminates the English/Arabic cache.
        any_lang="any"
        master=ultra_title_logo_cache_path(media_type,tmdb_id,any_lang,MASTER_CANVAS)
        target=ultra_title_logo_cache_path(media_type,tmdb_id,any_lang,canvas_size)
        path=_variant_from_master(master,target,canvas_size)
        if path:return path,fresh
        any_urls=[]
        if direct and direct_lang and direct_lang not in languages:
            any_urls.append(direct)
        if client is not None and not _cancelled(cancel_event):
            try:
                payload=client._get("/%s/%s/images"%(mt,int(tmdb_id)),{}) or {}
                logos=payload.get("logos") if isinstance(payload,dict) else []
                ranked=[]
                for logo in (logos or []):
                    if not isinstance(logo,dict):continue
                    fp=str(logo.get("file_path") or "").strip()
                    iso=str(logo.get("iso_639_1") or "").lower().split("-")[0].strip()
                    if not fp or not iso or iso in languages:continue
                    try:vote=float(logo.get("vote_average") or 0.0)
                    except Exception:vote=0.0
                    try:width=int(logo.get("width") or 0);height=int(logo.get("height") or 0)
                    except Exception:width=height=0
                    try:votes=int(logo.get("vote_count") or 0)
                    except Exception:votes=0
                    url=TMDBClient.image_url(fp,"original")
                    if url:ranked.append((vote,votes,width*height,width,url))
                ranked.sort(reverse=True)
                for _vote,_votes,_area,_width,url in ranked[:8]:
                    if url not in any_urls:any_urls.append(url)
            except Exception:
                pass
        path=fetch_prepare_title_logo_candidates(any_urls,master,canvas_size=MASTER_CANVAS,timeout=timeout)
        if not path and not _cancelled(cancel_event) and (project or personal):
            try:
                tvdb_id=str(fresh.get("tvdb_id") or fresh.get("thetvdb_id") or fresh.get("tvdb") or "").strip()
                if mt=="tv" and not tvdb_id and client is not None:
                    try:
                        ext=client._get("/tv/%s/external_ids"%int(tmdb_id),{}) or {}
                        tvdb_id=str(ext.get("tvdb_id") or "").strip()
                    except Exception:tvdb_id=""
                fa=_fanart_fetch(project,mt,tmdb_id=tmdb_id,tvdb_id=tvdb_id,timeout=timeout,personal_key=personal,logo_lang="*") or {}
                url=normalize_title_logo_url(fa.get("title_logo_url"))
                if url:path=fetch_prepare_title_logo_candidates([url],master,canvas_size=MASTER_CANVAS,timeout=timeout)
            except Exception:
                pass
        if valid_ultra_title_logo(master):
            return _variant_from_master(master,target,canvas_size),fresh
        return "",fresh

