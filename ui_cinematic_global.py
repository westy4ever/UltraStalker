# -*- coding: utf-8 -*-
"""Global-library-only Cinematic presentation for Movies and Series.

Hard rule: this screen never calls TMDB/network resolver.  It is a renderer over
PremiumGridBase catalogue/navigation plus the one persistent Global TMDB Library.
"""
from __future__ import absolute_import
from . import _
from .age_rating import display_certification
from Screens.MessageBox import MessageBox
from .localization import current_language
import hashlib, json, os, queue, time, threading, shutil, re, tempfile, unicodedata, weakref, logging, math
from collections import deque
from .core.executor import LazyThreadPoolExecutor
from .core.shared_executors import CATALOGUE_EXECUTOR, METADATA_EXECUTOR
try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter
except Exception:
    _PILImage=None;_ImageDraw=None;_ImageFilter=None
try:
    from enigma import gFont, ePoint, eSize
except Exception:
    gFont=None; ePoint=None; eSize=None
try:
    from skin import parseColor as _cin_parse_color
except Exception:
    _cin_parse_color=None

# Fast backdrop transport is Ultra-owned and independent of presentation logic.
# It keeps Enigma2's proven Twisted reactor/Agent network engine while the
# selection, cache policy and presentation remain Ultra Stalker code.
from .backdrop_transport import FastBackdropTransport

from .ui_grid_base import PremiumGridBase, _page_backdrop_fetch_original_q60
from .ui_async import AsyncScreenMixin
from .artwork_v2 import load_manifest, ArtworkV2, save_manual_rescue_art, canonical_art_paths, _download as _artwork_download
from .tmdb import TMDBClient
from .fanart import fetch_artwork as _fanart_fetch
from .storage import load_settings, load_api_keys
from .persistent_cache import ROOT, BACKDROPS, GENERATED, INDEX, BLUE_CACHE, TITLE_LOGOS, ensure_persistent_dirs, load_detail_snapshot, load_detail_snapshot_by_tmdb, hdd_read_ready, hdd_ready
from .media_library import readiness as _library_readiness, metadata_complete as _metadata_complete, load as _media_library_load, save as _media_library_save, ensure_adaptive as _media_library_ensure_adaptive
from .core.image_budget import image_budgeted
from .ui_dynamic_chrome import _build_category_extended_backdrop, _build_dynamic_settings_episode_rows, _cached_dynamic_settings_episode_rows, _build_dynamic_details_chrome, canonical_dynamic_details_key, canonical_dynamic_rows_key, _dynamic_palette, _lift_dynamic_accent, _mix_rgb
from .ui_fixed_adaptive import fixed_settings_rows, fixed_exact_surface
from .log import optional_failure
from .title_clean import display_title as _catalogue_title
from .ui_icon_menu import IconMenuList
from .ui_settings_inline_choice import SettingsInlineChoiceOverlay
from .ui_parts.catalog import strip_arabic_tashkeel as _shared_strip_arabic_tashkeel
_CIN_TITLE_LOGO_API = None
def _cin_title_logo_api():
    global _CIN_TITLE_LOGO_API
    if _CIN_TITLE_LOGO_API is None:
        from .title_logo_ultra import resolve_ultra_title_logo, ultra_title_logo_cached
        _CIN_TITLE_LOGO_API = (resolve_ultra_title_logo, ultra_title_logo_cached)
    return _CIN_TITLE_LOGO_API

def resolve_ultra_title_logo(*args, **kwargs):
    return _cin_title_logo_api()[0](*args, **kwargs)
def ultra_title_logo_cached(*args, **kwargs):
    return _cin_title_logo_api()[1](*args, **kwargs)
from .services.player_visuals import _adaptive_player_frames, _progress_neon_frame

CINEMATIC_GLOBAL_SKIN=""
_CIN_HYBRID_EXECUTOR=LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-cinematic-hybrid")
_CIN_PAGE_EXECUTOR=CATALOGUE_EXECUTOR
_FAST_BACKDROP_META_EXECUTOR=LazyThreadPoolExecutor(max_workers=2, thread_name_prefix="ultrastalker-fast-backdrop-meta")
_HQ_BACKDROP_EXECUTOR=LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-hq-backdrop")


def _age_rating_display(value):
    return display_certification(value)
# Feature gate retained for receiver A/B safety.
_FAST_BACKDROP_ENABLED=True
# R196 legacy raw ORIGINAL cache is kept untouched for rollback/reference.
_R196_ORIGINAL_ROOT=os.path.join(ROOT,"cinematic_original_v196")
# R242 isolated storage experiment: first paint still uses the exact TMDb ORIGINAL
# bytes, but HDD persistence stores only a decoder-friendly Full-HD JPEG.  The raw
# ORIGINAL lives in /tmp for the active session and is removed by the existing
# fast-backdrop cleanup, so the old R196 folder is never mutated by this test.
_R242_FULLHD_ROOT=BACKDROPS
_R242_FULLHD_EXECUTOR=LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-r242-fullhd")
_R242_FULLHD_LOCK=threading.RLock()
_R242_FULLHD_PENDING=set()
# Issue 10: Cinematic/BG1/BG2 share one decoder-ready backdrop authority.
# The canonical master lives in ArtworkV2; this executor only prepares the one
# presentation JPEG in the background so switching view modes never requires
# another Cache Artwork pass.
_SHARED_CIN_BACKDROP_EXECUTOR=LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-shared-backdrop")
_SHARED_CIN_BACKDROP_LOCK=threading.RLock()
_SHARED_CIN_BACKDROP_RAM={}
_SHARED_CIN_BACKDROP_PENDING=set()
# R151 root authority: one persistent decoder-ready cache shared by Cinematic,
# Backdrop Grid 1 and Backdrop Grid 2.  Unlike GENERATED, BLUE_CACHE survives
# Enigma2 restarts, so a completed Cache Artwork pass stays genuinely hot.
_THREE_VIEW_FAST_ROOT=os.path.join(BLUE_CACHE,"three_view_fast_v151")
_THREE_VIEW_FAST_SCHEMA=1
_THREE_VIEW_FAST_CHROME_RAM={}

def configure_cinematic_global(**deps):
    globals().update(deps)
    if "CINEMATIC_GLOBAL_SKIN" in deps:
        PremiumGlobalCinematicScreen.skin=deps["CINEMATIC_GLOBAL_SKIN"]


def _strip_arabic_tashkeel(value):
    """Use the exact Details description display policy."""
    return _shared_strip_arabic_tashkeel(value)

_CIN_ARAB_CAST_COUNTRY_TOKENS={"EG","LB","SA","AE","KW","QA","BH","OM","JO","SY","IQ","PS","YE","MA","DZ","TN","LY","SD"}

def _cin_person_name_is_latin(value):
    letters=[]
    for ch in str(value or ""):
        try:
            if unicodedata.category(ch).startswith("L"):letters.append(ch)
        except Exception:pass
    if not letters:return False
    for ch in letters:
        try:
            if "LATIN" not in unicodedata.name(ch,""):return False
        except Exception:return False
    return True

def _cin_is_arabic_work(row, item, visible_title=""):
    data={}
    if isinstance(item,dict):data.update(item)
    if isinstance(row,dict):data.update(row)
    if re.search(r"[\u0600-\u06ff]",str(visible_title or "")):return True
    for key in ("original_language","language","lang","audio_language"):
        raw=str(data.get(key) or "").strip().lower().replace("_","-")
        if raw=="ar" or raw.startswith("ar-"):return True
    country=data.get("origin_country") or data.get("country_code") or data.get("country") or data.get("production_countries") or ""
    values=list(country) if isinstance(country,(list,tuple,set)) else [country]
    aliases=("egypt","lebanon","saudi","emirates","kuwait","qatar","bahrain","oman","jordan","syria","iraq","palestine","yemen","morocco","algeria","tunisia","libya","sudan")
    for value in values:
        if isinstance(value,dict):value=value.get("iso_3166_1") or value.get("code") or value.get("name") or ""
        raw=str(value or "").strip();upper=raw.upper();low=raw.casefold()
        if upper in _CIN_ARAB_CAST_COUNTRY_TOKENS or any(x in low for x in aliases):return True
    return False

def _hex_rgb(value, fallback=(202,145,65)):
    value=str(value or "").strip().lstrip("#")
    if len(value)==6:
        try:return tuple(int(value[i:i+2],16) for i in (0,2,4))
        except Exception:pass
    return fallback

@image_budgeted
def _build_cinematic_backdrop(source,target,accent_hex):
    """Build the Cinematic presentation backdrop from the one global HDD image."""
    if _PILImage is None or not source or not os.path.isfile(source):return ""
    if os.path.isfile(target) and os.path.getsize(target)>1024:return target
    if not ensure_persistent_dirs(os.path.dirname(target)):return ""
    temp=target+".tmp.%d"%os.getpid();accent=_hex_rgb(accent_hex,(38,28,18))
    try:
        tw,th=1920,1080;res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
        with _PILImage.open(source) as src:
            src=src.convert("RGB")
            if src.width<16 or src.height<16:return ""
            scale=max(float(tw)/src.width,float(th)/src.height);nw=max(tw,int(round(src.width*scale)));nh=max(th,int(round(src.height*scale)))
            src=src.resize((nw,nh),res);left=max(0,min(nw-tw,int((nw-tw)*0.58)));top=max(0,(nh-th)//2);src=src.crop((left,top,left+tw,top+th)).convert("RGBA")
        src=_PILImage.alpha_composite(src,_PILImage.new("RGBA",(tw,th),(accent[0]//5,accent[1]//5,accent[2]//5,24)))
        # Premium left rail veil: strong enough to keep the floating rows
        # readable, but feathered so the backdrop never exposes a hard edge.
        left_alpha=[]
        for x in range(780):
            t=float(x)/779.0;left_alpha.append(max(0,min(255,int(236*((1.0-t)**1.85)))))
        la=_PILImage.new("L",(780,1));la.putdata(left_alpha);la=la.resize((780,th))
        veil=_PILImage.new("RGBA",(tw,th),(2,6,10,0));veil.putalpha(_PILImage.new("L",(tw,th),0));veil.alpha_composite(_PILImage.merge("RGBA",(_PILImage.new("L",(780,th),2),_PILImage.new("L",(780,th),6),_PILImage.new("L",(780,th),10),la)),(0,0))
        src=_PILImage.alpha_composite(src,veil)
        # Beta86: match the approved older composition.  The backdrop is strong
        # above the details area, then genuinely fades OUT into a dark adaptive
        # page plane.  Do not merely darken a full-screen wallpaper; the lower
        # area must read as UI background carrying a restrained current-title hue.
        art_alpha=[]
        fade_start,fade_end=470.0,900.0
        for y in range(th):
            if y<=fade_start:a=255
            elif y>=fade_end:a=0
            else:
                tt=(float(y)-fade_start)/(fade_end-fade_start);smooth=tt*tt*(3.0-2.0*tt);a=int(255*(1.0-smooth))
            art_alpha.append(max(0,min(255,a)))
        aa=_PILImage.new("L",(1,th));aa.putdata(art_alpha);aa=aa.resize((tw,th))
        if _ImageFilter is not None:
            try:aa=aa.filter(_ImageFilter.GaussianBlur(radius=9))
            except Exception:pass
        src.putalpha(aa)
        # Beta89: dark adaptive footer, visibly tied to the selected title while still cinematic.
        mood=(max(5,int(accent[0]*0.18)),max(7,int(accent[1]*0.18)),max(10,int(accent[2]*0.18)),255)
        base=_PILImage.new("RGBA",(tw,th),mood)
        # A very subtle vertical lift keeps the footer premium rather than flat.
        md=_ImageDraw.Draw(base) if _ImageDraw is not None else None
        if md is not None:
            lower=(max(4,int(accent[0]*0.12)),max(6,int(accent[1]*0.12)),max(9,int(accent[2]*0.12)),255)
            md.rectangle((0,760,tw,th),fill=lower)
            for yy in range(760,th,12):
                t=(yy-760)/float(max(1,th-760));a=max(0.0,1.0-t)
                col=(max(lower[0],int(accent[0]*(0.16+0.06*a))),
                     max(lower[1],int(accent[1]*(0.16+0.06*a))),
                     max(lower[2],int(accent[2]*(0.16+0.06*a))),255)
                md.rectangle((0,yy,tw,min(th,yy+12)),fill=col)
        src=_PILImage.alpha_composite(base,src)
        src.save(temp,"PNG",compress_level=3);os.replace(temp,target);return target
    except Exception as exc:
        optional_failure("cinematic.backdrop",exc)
        try:
            if os.path.exists(temp):os.unlink(temp)
        except Exception:pass
        return ""


def _shared_cinematic_backdrop_safe_key(key):
    value=str(key or "").strip()
    if not value:return ""
    value=re.sub(r"[^A-Za-z0-9._-]+","_",value)[:160]
    return value or hashlib.sha1(str(key).encode("utf-8","ignore")).hexdigest()[:24]

def _shared_cinematic_backdrop_source_sig(source):
    source=str(source or "")
    try:
        real=os.path.realpath(source);st=os.stat(real)
        return "%s:%s:%s"%(real,int(getattr(st,"st_mtime_ns",int(st.st_mtime*1000000000))),int(st.st_size))
    except Exception:
        return source

def _shared_cinematic_backdrop_identity(source,key="",row=None):
    """One identity for Cinematic + BG1 + BG2, derived from the same master file.

    R149 could choose TMDb identity on a hydrated screen and a source-path identity
    on a lightweight page snapshot.  The same cached ArtworkV2 backdrop therefore
    mapped to two presentation files and one view could wait/rebuild needlessly.
    R150 makes the canonical local master path the first and screen-independent
    authority.  A canonical movie_123/tv_123 filename stays human-readable; every
    other master uses a stable realpath hash.  Metadata richness can no longer
    change the presentation key.
    """
    source=str(source or "").strip();row=row if isinstance(row,dict) else {}
    if source:
        base=os.path.basename(source)
        match=re.search(r"(?:^|[^a-z])(tv|movie)[_-](\d+)(?:[^0-9]|$)",base,re.I)
        if match:
            return "%s-%s"%(match.group(1).lower(),match.group(2))
        try:real=os.path.realpath(source)
        except Exception:real=source
        return "src-"+hashlib.sha1(str(real).encode("utf-8","ignore")).hexdigest()[:24]
    # Defensive no-source fallback only. Once a master exists, metadata must not
    # participate in the display identity.
    tid=row.get("tmdb_id")
    if tid:
        try:
            mt=str(row.get("media_type") or "").lower()
            kind="tv" if mt in ("tv","series","show") else "movie"
            return "%s-%s"%(kind,int(tid))
        except Exception:pass
    raw=str(key or "").strip()
    match=re.match(r"^(tv|movie)[_-](\d+)$",raw,re.I)
    if match:return "%s-%s"%(match.group(1).lower(),match.group(2))
    return "key-"+hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()[:24] if raw else ""

def _shared_cinematic_backdrop_legacy_sig(source,key,row=None):
    source=str(source or "")
    try:
        st=os.stat(source);src_sig="%s:%s:%s"%(source,int(st.st_mtime),int(st.st_size))
    except Exception:
        src_sig=source
    row=row if isinstance(row,dict) else {}
    fingerprint=str(row.get("adaptive_fingerprint") or "")
    raw="shared-cinematic-v1|%s|%s|%s"%(str(key or ""),src_sig,fingerprint)
    return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()

def _shared_cinematic_backdrop_sig(source,key="",row=None):
    # The presentation content is owned by the canonical backdrop master, not by
    # a screen-local provider key or metadata/adaptive hydration state.  Once the
    # master changes its mtime/size the signature changes and the shared surface
    # is rebuilt exactly once.
    raw="shared-cinematic-v2|%s"%_shared_cinematic_backdrop_source_sig(source)
    return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()

def _shared_cinematic_backdrop_path(source,key="",row=None):
    identity=_shared_cinematic_backdrop_identity(source,key,row)
    safe=_shared_cinematic_backdrop_safe_key(identity)
    if not safe:return ""
    # Persistent fast authority for all three presentation screens.
    return os.path.join(_THREE_VIEW_FAST_ROOT,"%s_backdrop.jpg"%safe)

def _shared_cinematic_backdrop_legacy_path(source,key,row=None):
    safe=_shared_cinematic_backdrop_safe_key(key)
    return os.path.join(GENERATED,"cinematic_%s_backdrop_fast.jpg"%safe) if safe else ""

def _shared_cinematic_backdrop_accent(source,key,row=None):
    """Resolve the exact accent policy used by Cinematic, without a Screen."""
    row=row if isinstance(row,dict) else {}
    adaptive_source=str(row.get("poster_local") or "")
    if not (adaptive_source and os.path.isfile(adaptive_source)):
        adaptive_source=str(source or "")
    fingerprint=str(row.get("adaptive_fingerprint") or "")
    settings_key=canonical_dynamic_rows_key(adaptive_source,selected_rim_only=True,cinematic_premium=True) or "hy136_rows_%s_%s"%(str(key or ""),hashlib.sha1((adaptive_source+fingerprint).encode("utf-8","ignore")).hexdigest()[:14])
    settings=_build_dynamic_settings_episode_rows(adaptive_source,settings_key,selected_rim_only=True,cinematic_premium=True) or {}
    value_color=settings.get("value_color")
    if isinstance(value_color,(tuple,list)) and len(value_color)>=3:
        return "#%02x%02x%02x"%(int(value_color[0]),int(value_color[1]),int(value_color[2]))
    return str(row.get("adaptive_accent") or row.get("adaptive_primary") or "#c99141")

def _shared_cinematic_backdrop_ram_get(identity,sig):
    try:
        with _SHARED_CIN_BACKDROP_LOCK:
            cached=_SHARED_CIN_BACKDROP_RAM.get(str(identity or "")) or {}
            path=str(cached.get("path") or "")
            if cached.get("sig")==sig and path and os.path.isfile(path) and os.path.getsize(path)>4096:
                return path
    except Exception:pass
    return ""

def _shared_cinematic_backdrop_ram_put(identity,sig,path):
    try:
        with _SHARED_CIN_BACKDROP_LOCK:
            _SHARED_CIN_BACKDROP_RAM[str(identity or "")]={"sig":str(sig or ""),"path":str(path or "")}
            if len(_SHARED_CIN_BACKDROP_RAM)>512:
                # Dict insertion order is enough here; this cache is only a tiny
                # process-local accelerator and HDD remains authoritative.
                for old in list(_SHARED_CIN_BACKDROP_RAM)[:128]:_SHARED_CIN_BACKDROP_RAM.pop(old,None)
    except Exception:pass

def _three_view_fast_source_sig(source):
    return _shared_cinematic_backdrop_source_sig(source)


def _three_view_fast_chrome_paths(source,key="",row=None):
    row=row if isinstance(row,dict) else {}
    identity=_shared_cinematic_backdrop_identity(source or row.get("poster_local"),key,row)
    safe=_shared_cinematic_backdrop_safe_key(identity)
    if not safe:return {}
    prefix=os.path.join(_THREE_VIEW_FAST_ROOT,safe)
    return {
        "row_normal":prefix+"_row_normal.png",
        "row_selected":prefix+"_row_selected.png",
        "panel_detail":prefix+"_panel_detail.png",
        "overview_detail":prefix+"_overview_detail.png",
        "cast_detail":prefix+"_cast_detail.png",
        "quality":prefix+"_quality.png",
        "year":prefix+"_year.png",
        "runtime":prefix+"_runtime.png",
        "country":prefix+"_country.png",
        "genre_detail":prefix+"_genre_detail.png",
        "bg_genre":prefix+"_bg_genre.png",
        "bg1_overview":prefix+"_bg1_overview.png",
        "bg2_overview":prefix+"_bg2_overview.png",
        "sig":prefix+"_chrome.sig",
    }


def _three_view_fast_chrome_sig(source,key="",row=None):
    row=row if isinstance(row,dict) else {}
    adaptive=str(row.get("poster_local") or "")
    if not (adaptive and os.path.isfile(adaptive)):adaptive=str(source or "")
    if not adaptive:return ""
    raw="three-view-fast-chrome-v151-overview-inset|%s"%_three_view_fast_source_sig(adaptive)
    return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()


def _three_view_fast_valid(path,minimum=256):
    try:return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path))>int(minimum))
    except Exception:return False


def _three_view_fast_copy(source,target):
    if not _three_view_fast_valid(source):return ""
    if _three_view_fast_valid(target):return target
    if not ensure_persistent_dirs(os.path.dirname(target)):return ""
    temp=target+".tmp.%s.%s"%(os.getpid(),threading.get_ident())
    try:
        try:os.link(source,temp)
        except Exception:shutil.copy2(source,temp)
        os.replace(temp,target)
        return target if _three_view_fast_valid(target) else ""
    except Exception as exc:
        optional_failure("three_view_fast.copy",exc);return ""
    finally:
        try:
            if os.path.exists(temp):os.unlink(temp)
        except Exception:pass


def _three_view_fast_nine_slice(source,target,size):
    if _PILImage is None or not _three_view_fast_valid(source):return ""
    tw,th=int(size[0]),int(size[1])
    temp=target+".tmp.%s.%s"%(os.getpid(),threading.get_ident())
    try:
        with _PILImage.open(source) as im:
            im=im.convert("RGBA");sw,sh=im.size
            cx=max(8,min(34,sw//5,tw//5));cy=max(7,min(20,sh//4,th//4))
            left=right=cx;top=bottom=cy
            canvas=_PILImage.new("RGBA",(tw,th),(0,0,0,0))
            sx=(0,left,sw-right,sw);sy=(0,top,sh-bottom,sh)
            dx=(0,left,tw-right,tw);dy=(0,top,th-bottom,th)
            res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            for yi in range(3):
                for xi in range(3):
                    tile=im.crop((sx[xi],sy[yi],sx[xi+1],sy[yi+1]))
                    out_size=(dx[xi+1]-dx[xi],dy[yi+1]-dy[yi])
                    if tile.size!=out_size:tile=tile.resize(out_size,res)
                    canvas.alpha_composite(tile,(dx[xi],dy[yi]))
            if not ensure_persistent_dirs(os.path.dirname(target)):return ""
            canvas.save(temp,"PNG",compress_level=1);os.replace(temp,target)
        return target if _three_view_fast_valid(target) else ""
    except Exception as exc:
        optional_failure("three_view_fast.nine_slice",exc);return ""
    finally:
        try:
            if os.path.exists(temp):os.unlink(temp)
        except Exception:pass


def _three_view_fast_inset_overview(path):
    """Inset only the dark overview body while preserving the native outer rim.

    The operation is pixel-for-pixel at the final target size.  It never resizes
    the overview asset, which keeps the approved Full-HD corners/edge glow sharp.
    """
    try:
        path=str(path or "")
        if _PILImage is None or _ImageDraw is None or not _three_view_fast_valid(path):return ""
        with _PILImage.open(path) as src:
            src=src.convert("RGBA");w,h=src.size
            if w<80 or h<60:return path
            scale=4
            mw,mh=w*scale,h*scale
            mask=_PILImage.new("L",(mw,mh),0);md=_ImageDraw.Draw(mask)
            # Keep the original rim/glow at the edge.  The dark body begins one
            # visual step further inside, leaving a narrow transparent breathing ring.
            outer_cut=(7*scale,7*scale,(w-8)*scale,(h-8)*scale)
            md.rectangle((0,0,mw,mh),fill=255)
            md.rounded_rectangle(outer_cut,radius=15*scale,fill=0)
            inner=(11*scale,10*scale,(w-12)*scale,(h-11)*scale)
            md.rounded_rectangle(inner,radius=12*scale,fill=255)
            res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            mask=mask.resize((w,h),res)
            alpha=src.getchannel("A")
            try:
                from PIL import ImageChops as _ImageChops
                alpha=_ImageChops.multiply(alpha,mask)
            except Exception:
                # Fallback is still native-size; no runtime scaling is introduced.
                alpha=mask
            src.putalpha(alpha)
            temp=path+".inset.tmp.%s.%s"%(os.getpid(),threading.get_ident())
            src.save(temp,"PNG",compress_level=1);os.replace(temp,path)
        return path if _three_view_fast_valid(path) else ""
    except Exception as exc:
        optional_failure("three_view_fast.overview_inset",exc);return ""


def _prepare_three_view_fast_chrome(source,key="",row=None,build=False):
    """R259: retired durable Three-View fast package.

    Cinematic/BG1/BG2 no longer create a second backdrop/chrome vault.  Their
    HUD/adaptive layers keep their normal authorities, while the image itself is
    always read from the single canonical BACKDROPS folder.
    """
    return {}

def _prepare_shared_cinematic_backdrop(source,key,row=None,build=True,accent_hint=""):
    """R259 single-backdrop authority.

    Never build/copy/recompress a view-specific backdrop.  The already prepared
    backdrop file itself is the one display authority for Cinematic, BG1 and BG2.
    In the normal focus lane this is the R249/R251 Full-HD Q60 file under
    /media/hdd/UltraStalker/backdrops.
    """
    source=str(source or "").strip()
    try:
        return source if source and os.path.isfile(source) and os.path.getsize(source)>1024 else ""
    except Exception:
        return ""

def _schedule_shared_cinematic_backdrop(source,key="",row=None,accent_hint=""):
    """R259: no background duplicate is scheduled; reuse the one HDD backdrop."""
    return _prepare_shared_cinematic_backdrop(source,key,row,build=False,accent_hint=accent_hint)


@image_budgeted
def _build_overview_glass(target, accent_hex):
    """Adaptive transparent overview glass derived only from saved adaptive color."""
    if _PILImage is None or _ImageDraw is None:return ""
    if os.path.isfile(target) and os.path.getsize(target)>256:return target
    if not ensure_persistent_dirs(os.path.dirname(target)):return ""
    temp=target+".tmp.%d"%os.getpid();accent=_hex_rgb(accent_hex)
    try:
        w,h=1240,230
        im=_PILImage.new("RGBA",(w,h),(0,0,0,0));d=_ImageDraw.Draw(im)
        # Medium-density adaptive glass: backdrop remains visible through it.
        fill=(max(2,accent[0]//12),max(3,accent[1]//12),max(4,accent[2]//12),132)
        rim=(accent[0],accent[1],accent[2],185)
        d.rounded_rectangle((2,2,w-3,h-3),radius=20,fill=fill,outline=rim,width=2)
        d.rounded_rectangle((5,5,w-6,h-6),radius=17,outline=(255,255,255,34),width=1)
        # Top sheen and bottom mood give depth without turning it into a black slab.
        d.rounded_rectangle((10,7,w-11,48),radius=14,fill=(255,255,255,12))
        d.rounded_rectangle((10,h-58,w-11,h-11),radius=14,fill=(0,0,0,18))
        im.save(temp,"PNG",compress_level=3);os.replace(temp,target);return target
    except Exception as exc:
        optional_failure("cinematic.overview_glass",exc)
        try:
            if os.path.exists(temp):os.unlink(temp)
        except Exception:pass
        return ""


@image_budgeted
def _build_cinematic_exact_chrome(source_path, root, cache_key, accent_override=None):
    """Same adaptive Details material, rendered at Cinematic's exact widget sizes.

    This avoids clipping/scaling the Native Details 1283x142 overview and 815x40
    genre assets into smaller Cinematic widgets, which produced visibly broken
    right edges and jagged rounded corners on the receiver.
    """
    if _PILImage is None or _ImageDraw is None or not source_path or not os.path.isfile(source_path):
        return {}
    try:
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.48,0.46)
        override=str(accent_override or "").strip().lstrip("#")
        if len(override)==6:
            try:accent=tuple(int(override[i:i+2],16) for i in (0,2,4))
            except Exception:pass
        accent2=tuple(min(255,int(v*0.78+255*0.22)) for v in accent) if accent_override else _lift_dynamic_accent(secondary,0.42,0.38)
        base=(3,12,20)
        specs={
            "panel":((1325,370),30,"panel"),
            "overview":((965,220),22,"overview"),
            "quality":((150,50),22,"pill"),
            "year":((130,50),22,"pill"),
            "runtime":((150,50),22,"pill"),
            "country":((160,50),22,"pill"),
            "genre":((335,50),22,"pill"),
        }
        result={}
        ensure_persistent_dirs(root)
        for name,(size,radius,kind) in specs.items():
            target=os.path.join(root,"cin89_exact_%s_%s.png"%(cache_key,name))
            if os.path.isfile(target) and os.path.getsize(target)>256:
                result[name]=target;continue
            temp=target+".tmp.%d"%os.getpid();w,h=size
            out=_PILImage.new("RGBA",size,(0,0,0,0))
            glow=_PILImage.new("RGBA",size,(0,0,0,0));gd=_ImageDraw.Draw(glow)
            gd.rounded_rectangle((6,6,w-7,h-7),radius=radius,outline=accent+((92 if kind in ("panel","overview") else 78),),width=3)
            if _ImageFilter is not None:
                try:glow=glow.filter(_ImageFilter.GaussianBlur(radius=7 if kind in ("panel","overview") else 5))
                except Exception:pass
            out=_PILImage.alpha_composite(out,glow);d=_ImageDraw.Draw(out)
            fill=_mix_rgb(base,accent,0.10 if kind in ("panel","overview") else 0.14)
            if kind=="panel":fa,ba=62,174
            elif kind=="overview":fa,ba=82,188
            else:fa,ba=98,196
            if kind=="overview":
                d.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=None,outline=accent+(ba,),width=1)
                d.rounded_rectangle((10,9,w-11,h-10),radius=max(7,radius-7),fill=fill+(fa,))
            else:
                d.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=fill+(fa,),outline=accent+(ba,),width=1)
            inner=_mix_rgb(accent2,(255,255,255),0.46)
            if kind=="overview":
                d.rounded_rectangle((11,10,w-12,h-11),radius=max(6,radius-8),outline=inner+(22,),width=1)
            else:
                d.rounded_rectangle((7,7,w-8,h-8),radius=max(5,radius-6),outline=inner+(22,),width=1)
            sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=_ImageDraw.Draw(sheen)
            if kind=="overview":
                sd.rounded_rectangle((15,12,w-16,max(20,h//2)),radius=max(5,radius-10),fill=inner+(17,))
            else:
                sd.rounded_rectangle((12,7,w-13,max(14,h//2)),radius=max(5,radius-8),fill=inner+((17 if kind=="panel" else 13),))
            if _ImageFilter is not None:
                try:sheen=sheen.filter(_ImageFilter.GaussianBlur(radius=4 if kind in ("panel","overview") else 3))
                except Exception:pass
            out=_PILImage.alpha_composite(out,sheen)
            out.save(temp,"PNG",compress_level=3);os.replace(temp,target);result[name]=target
        return result
    except Exception as exc:
        optional_failure("cinematic.exact_chrome",exc);return {}


_COUNTRY_NAMES = {
    "EG":"مصر","LB":"لبنان","SA":"السعودية","AE":"الإمارات","KW":"الكويت","QA":"قطر","BH":"البحرين","OM":"عُمان","JO":"الأردن","SY":"سوريا","IQ":"العراق","PS":"فلسطين","YE":"اليمن","MA":"المغرب","DZ":"الجزائر","TN":"تونس","LY":"ليبيا","SD":"السودان","US":"USA","GB":"UK","FR":"France","DE":"Germany","IT":"Italy","ES":"Spain","TR":"Turkey","IN":"India","KR":"Korea","JP":"Japan","CN":"China","CA":"Canada","MX":"Mexico","BR":"Brazil","AR":"Argentina","RU":"Russia","AU":"Australia"
}
_COUNTRY_CODES_EN = {
    "EG":"EG","LB":"LB","SA":"KSA","AE":"UAE","KW":"KW","QA":"QA","BH":"BH","OM":"OM","JO":"JO","SY":"SY","IQ":"IQ","PS":"PS","YE":"YE","MA":"MA","DZ":"DZ","TN":"TN","LY":"LY","SD":"SD",
    "US":"USA","GB":"UK","FR":"FR","DE":"DE","IT":"IT","ES":"ES","TR":"TR","IN":"IN","KR":"KR","JP":"JP","CN":"CN","CA":"CA","MX":"MX","BR":"BR","AR":"AR","RU":"RU","AU":"AU"
}

def _country_code(value):
    if isinstance(value,(list,tuple,set)):
        for row in value:
            code=_country_code(row)
            if code:return code
        return ""
    if isinstance(value,dict):
        for key in ("iso_3166_1","country_code","code","name"):
            code=_country_code(value.get(key))
            if code:return code
        return ""
    raw=str(value or "").strip()
    if not raw:return ""
    upper=raw.upper().strip()
    aliases={
        "EGYPT":"EG","ARAB REPUBLIC OF EGYPT":"EG","مصر":"EG","جمهورية مصر العربية":"EG",
        "LEBANON":"LB","لبنان":"LB","SAUDI ARABIA":"SA","السعودية":"SA",
        "UNITED ARAB EMIRATES":"AE","UAE":"AE","الإمارات":"AE",
        "UNITED STATES":"US","UNITED STATES OF AMERICA":"US","USA":"US",
        "UNITED KINGDOM":"GB","GREAT BRITAIN":"GB","UK":"GB",
        "TURKEY":"TR","TÜRKIYE":"TR","TURKIYE":"TR",
        "SOUTH KOREA":"KR","REPUBLIC OF KOREA":"KR","KOREA":"KR",
        "NORTH KOREA":"KP","JAPAN":"JP","CHINA":"CN","INDIA":"IN",
        "FRANCE":"FR","GERMANY":"DE","ITALY":"IT","SPAIN":"ES",
        "CANADA":"CA","MEXICO":"MX","BRAZIL":"BR","ARGENTINA":"AR",
        "RUSSIA":"RU","RUSSIAN FEDERATION":"RU","AUSTRALIA":"AU",
        "KUWAIT":"KW","QATAR":"QA","BAHRAIN":"BH","OMAN":"OM",
        "JORDAN":"JO","SYRIA":"SY","IRAQ":"IQ","PALESTINE":"PS",
        "MOROCCO":"MA","ALGERIA":"DZ","TUNISIA":"TN","LIBYA":"LY","SUDAN":"SD"
    }
    if upper in aliases:return aliases[upper]
    for code,name in _COUNTRY_NAMES.items():
        if upper==str(name).upper():return code
    if len(upper)==2 and upper.isalpha():return upper
    return ""

def _normalized_country(value):
    code=_country_code(value);raw=str(value or "").strip()
    if current_language()=="en":
        return _COUNTRY_CODES_EN.get(code,code or (raw[:12] if raw else ""))
    return _COUNTRY_NAMES.get(code,raw[:12] if raw else "")

def _quality_asset_name(value):
    import re
    text=str(value or "").upper()
    if re.search(r"\b(?:4K|UHD|2160P?)\b",text):return "us65_quality_4k_122x34.png"
    if re.search(r"\b(?:FULL[ ._-]?HD|FHD|1080P?)\b",text):return "us65_quality_fullhd_122x34.png"
    if re.search(r"\b(?:HD|720P?)\b",text):return "us65_quality_hd_122x34.png"
    if re.search(r"\b(?:SD|576P?|480P?)\b",text):return "us93_quality_sd_122x34.png"
    return None



@image_budgeted
def _cinematic_row_progress_frame(accent_hex, value, width=300):
    """Exact Player laser grammar rendered at the rail's native width.

    Enigma2 MultiContent clips pixmaps instead of scaling them. The Player asset
    is 1350px wide, so using it inside a 300px rail slot made 7% and 60% look
    almost the same. This keeps the Player drawing algorithm but renders it at
    the row's real width, making beam length mathematically match resume %.
    """
    if _PILImage is None or _ImageDraw is None:return ""
    try:
        value=max(0,min(100,int(value)))
        hx=str(accent_hex or "#55b9ff").lstrip("#")
        if len(hx)!=6:hx="55b9ff"
        accent=tuple(int(hx[i:i+2],16) for i in (0,2,4))
        root=os.path.join(GENERATED,"cinematic_row_progress6560")
        if not ensure_persistent_dirs(root):return ""
        w=max(80,min(600,int(width or 300)));h=34
        target=os.path.join(root,"%s_%03d_%03d.png"%(hx,value,w))
        if os.path.isfile(target) and os.path.getsize(target)>128:return target
        img=_PILImage.new("RGBA",(w,h),(0,0,0,0))
        if value<=0:img.save(target,"PNG");return target
        # release: keep a real end-cap safety zone inside the native pixmap.
        # Older cached 100% frames ended too close to the right edge, so Enigma2's
        # MultiContent clipping visually shaved the flare/dot.  The percentage stays
        # exactly where it is; only the watched beam gets 24px of breathing room.
        end=max(2,min(w-24,int(round((w-28)*value/100.0))));y=h//2
        bloom=_PILImage.new("RGBA",(w,h),(0,0,0,0));bd=_ImageDraw.Draw(bloom)
        bd.line((2,y,end,y),fill=accent+(205,),width=11)
        if _ImageFilter is not None:bloom=bloom.filter(_ImageFilter.GaussianBlur(radius=6))
        img=_PILImage.alpha_composite(img,bloom)
        halo=_PILImage.new("RGBA",(w,h),(0,0,0,0));hd=_ImageDraw.Draw(halo)
        hd.line((2,y,end,y),fill=accent+(245,),width=7)
        if _ImageFilter is not None:halo=halo.filter(_ImageFilter.GaussianBlur(radius=2.8))
        img=_PILImage.alpha_composite(img,halo);d=_ImageDraw.Draw(img)
        hot=tuple(min(255,int(v*.48+255*.52)) for v in accent);core=tuple(min(255,int(v*.20+255*.80)) for v in accent)
        d.line((2,y,end,y),fill=hot+(255,),width=4);d.line((2,y,end,y),fill=core+(255,),width=2)
        flare=_PILImage.new("RGBA",(w,h),(0,0,0,0));fd=_ImageDraw.Draw(flare)
        fd.line((max(2,end-28),y,end+min(16,w-1-end),y),fill=core+(250,),width=5)
        fd.line((end,max(1,y-9),end,min(h-2,y+9)),fill=core+(210,),width=2)
        fd.ellipse((end-5,y-5,end+5,y+5),fill=(255,255,255,250))
        if _ImageFilter is not None:flare=flare.filter(_ImageFilter.GaussianBlur(radius=4.0))
        img=_PILImage.alpha_composite(img,flare);d=_ImageDraw.Draw(img);d.ellipse((end-2,y-2,end+2,y+2),fill=(255,255,255,255))
        temp=target+".tmp.%d"%os.getpid();img.save(temp,"PNG");os.replace(temp,target);return target
    except Exception as exc:
        optional_failure("cinematic.row_progress_frame",exc);return ""

class PremiumGlobalCinematicScreen(PremiumGridBase):
    """Cinematic list presentation over the exact same Global TMDB record."""

    # Cinematic is a vertical Settings/Episodes list, not a poster grid.  The
    # inherited PremiumGridBase constructor calls self._grid_art_init(), so an
    # override here prevents the hidden 1x1 GridArtwork ePicLoad from ever being
    # constructed.  This is intentionally done before PremiumGridBase.__init__
    # runs; stopping it later is too late because OpenBH has already allocated
    # the native decoder/surface.
    def _grid_art_init(self, slot_names, image_size, profile, client):
        self._grid_slot_names=[]
        self._grid_image_size=(0,0)
        self._grid_profile=profile
        self._grid_client=client
        self._grid_generation=0
        self._grid_download_jobs=queue.Queue()
        self._grid_decode_queue=[]
        self._grid_decode_busy=False
        self._grid_decode_active=None
        self._grid_closed=False
        self._grid_decode_cache={}
        self._grid_decode_cache_limit=0
        self._grid_waiting={}
        self._grid_queued=set()
        self._grid_slot_paths={}
        self._grid_palette_sources={}
        self._grid_accent_jobs=queue.Queue()
        self._grid_accent_pending=set()
        self._grid_download_inflight={}
        self._grid_download_futures={}
        self._grid_poster_thumb_pending=set()
        self._grid_download_lock=threading.Lock()
        self._grid_picload=None
        self._grid_pic_connection=None

    def _grid_art_layout_ready(self):
        # No hidden GridArtwork decoder exists in Cinematic.
        return

    def _grid_layout_ready(self):
        # Preserve PremiumGridBase catalogue/navigation startup while skipping
        # its selector/grid-art setup.  This is the only page bootstrap the
        # Cinematic screen needs.
        try:self._update_grid_clock()
        except Exception:pass
        self.load_page(self._restore_page,self._restore_index)

    def _grid_hidden_release(self):
        self._grid_images_suspended=True

    def _cin_release_surfaces_for_child(self):
        """Release only rendered/native Cinematic surfaces before a child opens.

        Canonical poster/backdrop files stay on HDD and their paths are retained.
        This is a gAccel surface-budget handoff, not an artwork/cache eviction.
        Releasing here happens *before* Details is instantiated, which gives the
        receiver room for the child's 1620x620/backdrop/glass allocations.
        """
        saved={}
        try:
            for base in ("cin_backdrop",):
                active=self._cin_active_pixmap.get(base,base)
                path=str(self._cin_pixmap_paths.get(active) or "")
                if path and os.path.isfile(path):saved[base]=path
        except Exception as exc:optional_failure("cinematic.surface_budget_capture",exc)
        # Preserve lightweight chrome paths too, but drop their native gPixmaps.
        try:
            for name,path in dict(getattr(self,"_cin_widget_paths",{}) or {}).items():
                if path and os.path.isfile(str(path)):saved["widget:"+str(name)]=str(path)
        except Exception as exc:optional_failure("cinematic.surface_budget_capture_chrome",exc)
        self._cin_suspended_visual_paths=saved

        # The large backdrop/panel surfaces are the important wins. Small labels
        # remain untouched; only native image surfaces are released.
        heavy=(
            "cin_backdrop","cin_backdrop_stage",
            "panel_bg","overview_bg","quality_pill_bg","year_pill_bg",
            "runtime_pill_bg","country_pill_bg","genre_pill_bg",
            "quality_logo","runtime_icon","country_flag"
        )
        for name in heavy:
            try:_release_pixmap_widget(self,name)
            except Exception as exc:optional_failure("cinematic.surface_budget_release",exc)
        self._cin_pixmap_paths.clear();self._cin_widget_paths.clear()
        self._cin_active_pixmap={"cin_backdrop":"cin_backdrop"}
        try:_native_image_pressure_relief(force=True)
        except Exception as exc:optional_failure("cinematic.surface_budget_relief",exc)

    def _cin_restore_surfaces_after_child(self):
        """HDD-only rebind of the exact visuals released for the child."""
        saved=dict(getattr(self,"_cin_suspended_visual_paths",{}) or {})
        if not saved:return
        try:
            for base in ("cin_backdrop",):
                path=str(saved.get(base) or "")
                if path and os.path.isfile(path) and self[base].instance is not None:
                    self[base].instance.setPixmapFromFile(path);self[base].show()
                    self._cin_pixmap_paths[base]=path;self._cin_active_pixmap[base]=base
            for key,path in saved.items():
                if not str(key).startswith("widget:"):continue
                name=str(key)[7:]
                if not path or not os.path.isfile(path):continue
                try:
                    if self[name].instance is not None:
                        self[name].instance.setPixmapFromFile(path);self[name].show();self._cin_widget_paths[name]=path
                except Exception as exc:optional_failure("cinematic.surface_budget_restore_widget",exc)
        except Exception as exc:optional_failure("cinematic.surface_budget_restore",exc)
        self._cin_suspended_visual_paths={}

    def _cin_force_cached_visual_rebind(self):
        """Rebind only already-cached cinematic pixmaps after Player returns.

        OpenBH can keep the Python path markers while dropping the native gPixmap
        surfaces during video playback.  Clear + bind the exact same HDD files;
        never rebuild artwork, hit TMDb, or change the selected item.
        """
        try:
            for base in ("cin_backdrop",):
                active=self._cin_active_pixmap.get(base,base)
                path=str(self._cin_pixmap_paths.get(active) or "")
                if path and os.path.isfile(path) and self[active].instance is not None:
                    try:self[active].instance.setPixmap(None)
                    except Exception:pass
                    self[active].instance.setPixmapFromFile(path);self[active].show()
            for name,path in dict(getattr(self,"_cin_widget_paths",{}) or {}).items():
                path=str(path or "")
                if not path or not os.path.isfile(path):continue
                try:
                    if self[name].instance is not None:
                        self[name].instance.setPixmap(None)
                        self[name].instance.setPixmapFromFile(path);self[name].show()
                except Exception as exc:optional_failure("cinematic.player_return_rebind_widget",exc)
            logo=str(getattr(self,"_cin_logo_path","") or "")
            if logo and os.path.isfile(logo) and self["title_logo"].instance is not None:
                try:self["title_logo"].instance.setPixmap(None)
                except Exception:pass
                self["title_logo"].instance.setPixmapFromFile(logo);self["title_logo"].show()
        except Exception as exc:optional_failure("cinematic.player_return_rebind",exc)

    def _pause_grid_background_for_child(self):
        # Keep the accepted Fast-Navigation cancellations, then free only native
        # rendered surfaces. No poster/backdrop file is deleted or re-downloaded.
        try:PremiumGridBase._pause_grid_background_for_child(self)
        except Exception as exc:optional_failure("cinematic.child_pause_base",exc)
        self._cin_cancel_focus(enqueue=False);self._cin_cancel_background()
        self._cin_release_surfaces_for_child()

    def _prepare_grid_page(self,target,cancel_event=None):
        """Catalogue-only page preparation for Cinematic.

        PremiumGridBase prepares poster-grid thumbnails/card chrome while it
        builds a page.  On this screen image_size is intentionally 1x1, which
        created gridposter_*_1x1 files and native gSurfaces even though no grid
        exists.  Keep only catalogue/state/text preparation here; all visual
        ownership belongs to the explicit Cinematic pipeline.
        """
        data=self._grid_page_data(target,cancel_event)
        valid=[x for x in data if isinstance(x,dict)] if isinstance(data,list) else []
        if self.media_type in ("vod","series"):
            # Preserve only the direct provider artwork URLs in private fields
            # before the structural Stalker artwork strip. Cinematic may hydrate
            # the *focused* title from these URLs, but the catalogue itself stays
            # clean and no page-wide image engine is re-enabled.
            originals=[dict(x) for x in valid]
            poster_counts={};backdrop_counts={}
            for src in originals:
                try:
                    pv=str(_image_url(src) or "").strip();bv=str(_backdrop_url(src) or "").strip()
                    if pv:poster_counts[pv]=poster_counts.get(pv,0)+1
                    if bv:backdrop_counts[bv]=backdrop_counts.get(bv,0)+1
                except Exception:pass
            rebuilt=[]
            for src in originals:
                try:
                    pv=str(_image_url(src) or "").strip();bv=str(_backdrop_url(src) or "").strip()
                    clean=dict(src) if src.get("_xtream") else _strip_portal_artwork(src)
                    # Repeated page-wide artwork is almost always a provider
                    # placeholder. Do not promote it into a title identity.
                    if pv and poster_counts.get(pv,0)<3:clean["_cin_provider_poster_url"]=pv
                    if bv and backdrop_counts.get(bv,0)<4:clean["_cin_provider_backdrop_url"]=bv
                    rebuilt.append(clean)
                except Exception:
                    rebuilt.append(dict(src))
            valid=rebuilt
        try:states=load_content_states(self.profile,self.media_type,valid) if valid else []
        except Exception:states=[{} for _ in valid]
        try:qualities=load_content_qualities(self.profile,self.media_type,valid) if valid and self.media_type in ("vod","series") else ["" for _ in valid]
        except Exception:qualities=["" for _ in valid]
        cfg=self._grid_settings or {};state_rows=[];titles=[];fitted=[];meta=[]
        for pos,item in enumerate(valid):
            state=states[pos] if pos<len(states) else {};quality=qualities[pos] if pos<len(qualities) else ""
            row=dict(state or {});row["quality"]=quality or "";state_rows.append(row)
            clean_titles=bool(cfg.get("clean_titles",True))
            raw=item.get("_raw_name") or item.get("name") or item.get("title") or item.get("id") or "Item"
            title=(_catalogue_title(raw) if clean_titles else str(raw or "").strip());titles.append(title);fitted.append((title,None))
            bits=[]
            try:
                explicit=self._explicit_item_quality(item);badges=explicit or str(row.get("quality") or "") or quality_badges(raw)
                if badges:bits.append(badges)
            except Exception:pass
            if item.get("year"):bits.append(str(item.get("year"))[:4])
            if row.get("favorite"):bits.append("★")
            if row.get("completed") and cfg.get("show_watched",True):bits.append("WATCHED")
            elif row.get("position") and row.get("duration"):
                try:bits.append("%d%%"%min(99,int(row["position"]*100.0/row["duration"])))
                except Exception:pass
            meta.append("  •  ".join(bits)[:28])
        return {"items":valid,"states":state_rows,"titles":titles,"fitted":fitted,"meta":meta,"art":[{} for _ in valid]}

    # Grid-only background engines must never wake inside Cinematic.  Focused
    # artwork/cache policy is owned exclusively by the _cin_* pipeline below.
    def _poll_visible_poster_cache(self):return
    def _start_visible_poster_watch(self):return
    def _start_progressive_poster_prefetch(self):return
    def _prefetch_visible_page_details(self,*args,**kwargs):return
    def _prefetch_selected_details(self):return
    def _prefetch_selected_series_hierarchy(self):return
    def _warm_idle_grid_page(self):return
    skin=CINEMATIC_GLOBAL_SKIN
    columns=1;page_size=12;image_size=(1,1);placeholder="grid_placeholder_movie_921.png";selection_asset="portal_selection_clear.png"
    card_positions=[(50,130+i*73) for i in range(12)]

    def _cin_init_authority_state(self):
        """Single selected-title runtime state for Cinematic, BG1 and BG2."""
        self._cin_record_key="";self._cin_visual_key="";self._cin_jobs=queue.Queue();self._cin_pending=set();self._cin_last_record={};self._cin_row_refresh_cursor=0;self._cin_row_complete=set();self._cin_page_records={};self._cin_committed_adaptive_key="";self._settings_row_asset=asset("series_floating_row.png");self._settings_row_selected_asset=self._settings_row_asset;self._cin_progress_accent="#69c9f4"
        # Stage 9: selected-title adaptive work is generation-owned.  Navigation
        # invalidates the previous generation immediately, but keeps its already
        # committed surfaces visible until the current title has a COMPLETE pair
        # of row + details chrome surfaces ready to atomically replace them.
        self._cin_adaptive_generation=0;self._cin_adaptive_target_sig="";self._cin_adaptive_cancel=None;self._cin_visual_future=None
        # R224 Stage 3: visible adaptive material has explicit selection ownership.
        # Navigation may never keep another title's palette on screen. The fixed
        # green master is the neutral bridge while the new title settles.
        self._cin_display_adaptive_owner_sig="";self._cin_nav_adaptive_pending_sig=""
        self._cin_fixed_progress_accent="#69c9f4"
        self._cin_package_schema=5
        self._cin_focus_cancel=None;self._cin_focus_future=None;self._cin_focus_sig=""
        self._cin_fast_info_cancel=None;self._cin_fast_info_future=None;self._cin_fast_info_sig=""
        self._cin_background_cancel=None;self._cin_background_future=None
        self._cin_missed=deque();self._cin_missed_keys=set();self._cin_last_focus_item=None
        self._cin_last_nav_at=time.monotonic();self._cin_hidden=False
        self._cin_active_pixmap={"cin_backdrop":"cin_backdrop"};self._cin_pixmap_paths={};self._cin_widget_paths={};self._cin_suspended_visual_paths={}
        self._cin_shared_backdrop_retry=0
        self._cin_page_packages={};self._cin_page_backdrops={};self._cin_nav_alias_ram={}
        self._cin_logo_pending=set();self._cin_logo_path="";self._cin_logo_cancel=None;self._cin_logo_active_sig=""
        self._fast_bd_enabled=bool(_FAST_BACKDROP_ENABLED)
        self._fast_bd_req_id=0;self._fast_bd_deferred=None;self._fast_bd_path="";self._fast_bd_sig="";self._fast_bd_started_at=0.0;self._fast_bd_inflight=False
        self._fast_bd_meta_pending=set();self._fast_bd_meta_latest_sig="";self._fast_bd_fast_cache={};self._fast_bd_fast_order=deque();self._fast_bd_fast_limit=12;self._fast_bd_locked_sig="";self._fast_bd_locked_path=""
        self._fast_bd_transport=None
        if self._fast_bd_enabled:
            try:self._fast_bd_transport=FastBackdropTransport()
            except Exception as exc:optional_failure("cinematic.fast_backdrop_transport",exc)
        self._folder_artwork_worker_window=1
        self._cin_cache_exclusive=False
        self._folder_artwork_fast_art_only=False

    def _cin_init_authority_widgets(self):
        """Single HUD/backdrop/logo/timer lifecycle used by all three views."""
        self["cin_backdrop"]=Pixmap();self["cin_backdrop_stage"]=Pixmap();self["title_logo"]=Pixmap();self["artwork_status_bg"]=Pixmap()
        for name in ("panel_bg","overview_bg","cast_card_bg","quality_pill_bg","year_pill_bg","runtime_pill_bg","country_pill_bg","genre_pill_bg","quality_logo","runtime_icon","country_flag"):
            self[name]=Pixmap()
        for name in ("name","quality_text","year_text","runtime_text","country_text","genre_text","age_rating_text","cast_text","description"):
            self[name]=Label("")
        self["cin_list"]=IconMenuList([], width=430, item_height=73, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self._settings_inline_menu=SettingsInlineChoiceOverlay(self,"settings_menu_list","settings_menu_actions",asset,_,optional_failure)
        self._cin_list_icon=asset("settings_icons/artwork.png")
        self._cin_poll=eTimer();self._cin_poll_conn=None;self._cin_poll_interval=600
        try:self._cin_poll_conn=self._cin_poll.timeout.connect(self._cin_poll_hdd)
        except Exception:self._cin_poll.callback.append(self._cin_poll_hdd)
        self._cin_nav_backdrop_timer=eTimer();self._cin_nav_backdrop_conn=None
        try:self._cin_nav_backdrop_conn=self._cin_nav_backdrop_timer.timeout.connect(self._cin_apply_debounced_backdrop)
        except Exception:self._cin_nav_backdrop_timer.callback.append(self._cin_apply_debounced_backdrop)
        self._cin_nav_adaptive_timer=eTimer();self._cin_nav_adaptive_conn=None
        try:self._cin_nav_adaptive_conn=self._cin_nav_adaptive_timer.timeout.connect(self._cin_apply_nav_adaptive_baseline)
        except Exception:self._cin_nav_adaptive_timer.callback.append(self._cin_apply_nav_adaptive_baseline)
        self._cin_fast_info_timer=eTimer();self._cin_fast_info_conn=None
        try:self._cin_fast_info_conn=self._cin_fast_info_timer.timeout.connect(self._cin_apply_fast_info)
        except Exception:self._cin_fast_info_timer.callback.append(self._cin_apply_fast_info)
        self.onLayoutFinish.append(self._cin_layout_ready);self.onClose.append(self._cin_stop)
        try:self.onHide.append(self._cin_hidden_start)
        except Exception:pass
        self["section"].setText("")
        self["blue"].setText(_("Cache Artwork"))

    def _cin_install_actions(self,left,right,up,down):
        """Same action surface; only the navigator functions differ for poster rails."""
        self["actions"] = ActionMap(["OkCancelActions","ColorActions","DirectionActions","MenuActions","UltraStalkerMenuActions","InfoActions"], {
            "cancel":self.close,"red":self.open_folder_search,"ok":self.open_selected,
            "left":left,"right":right,"up":up,"down":down,
            "green":self.toggle_selected_favorite,"yellow":self.cycle_folder_sort,
            "blue":self.cache_folder_artwork,"menu":self.open_menu,"info":self.show_information},-1)

    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        self._cin_record_key="";self._cin_visual_key="";self._cin_jobs=queue.Queue();self._cin_pending=set();self._cin_last_record={};self._cin_row_refresh_cursor=0;self._cin_row_complete=set();self._cin_page_records={};self._cin_committed_adaptive_key=""
        self._cin_adaptive_generation=0;self._cin_adaptive_target_sig="";self._cin_adaptive_cancel=None;self._cin_visual_future=None
        # R224 Stage 3: visible adaptive material is owned by one selected title.
        self._cin_display_adaptive_owner_sig="";self._cin_nav_adaptive_pending_sig=""
        self._cin_fixed_progress_accent="#69c9f4"
        # R214 Stage 1: Cinematic cold paint starts from the exact bundled fixed-green
        # application material.  This is a static packaged asset lookup only; no
        # palette/Pillow/network work is allowed before the first title settles.
        _fixed_rows=fixed_settings_rows() or {}
        self._settings_row_asset=str(_fixed_rows.get("normal") or asset("series_floating_row.png"))
        self._settings_row_selected_asset=str(_fixed_rows.get("selected") or self._settings_row_asset)
        self._cin_fixed_row_asset=self._settings_row_asset
        self._cin_fixed_row_selected_asset=self._settings_row_selected_asset
        self._cin_progress_accent="#69c9f4"
        # Beta103 hybrid policy: only the focused title may hydrate while browsing.
        # Everything completed is persisted as one immutable HDD package and reused forever.
        self._cin_package_schema=5
        self._cin_focus_cancel=None;self._cin_focus_future=None;self._cin_focus_sig=""
        self._cin_fast_info_cancel=None;self._cin_fast_info_future=None;self._cin_fast_info_sig=""
        self._cin_background_cancel=None;self._cin_background_future=None
        self._cin_missed=deque();self._cin_missed_keys=set();self._cin_last_focus_item=None
        self._cin_last_nav_at=time.monotonic();self._cin_hidden=False
        # Stage 7: page navigation has a virtual destination independent of the
        # currently painted page. Remote key-repeat can therefore advance several
        # pages while one provider read is still pending, without replaying every
        # intermediate page afterwards.
        self._cin_page_nav_target=0
        self._cin_page_nav_row=0
        self._cin_page_nav_generation=0
        # One native full-screen backdrop widget, exactly like Backdrop Grid.
        # The old two-widget flip could invalidate neighbouring HUD gPixmaps on
        # some OpenBH/Broadcom receivers.  Keep the stage allocated only for skin
        # compatibility, but never use it for navigation swaps.
        self._cin_active_pixmap={"cin_backdrop":"cin_backdrop"};self._cin_pixmap_paths={};self._cin_widget_paths={};self._cin_suspended_visual_paths={}
        self._cin_shared_backdrop_retry=0
        # release: page-local trusted package/backdrop index. Navigation must never
        # open package JSON or scan the HDD after an arrow press. The tiny package
        # files are indexed once when the page is painted; only the selected JPEG
        # is decoded on demand.
        self._cin_page_packages={};self._cin_page_backdrops={}
        # R171 hot-page visual index: cached final adaptive + title-logo paths are
        # discovered once while the page is painted. Arrow navigation then stays
        # RAM-only and can swap these tiny already-decoded-file authorities
        # immediately, instead of waiting for the 850ms stable-focus lane.
        # release: provider-key aliases point straight at the FINAL prepared Cinematic backdrop.
        self._cin_nav_alias_ram={}
        self._cin_logo_pending=set();self._cin_logo_path="";self._cin_logo_cancel=None;self._cin_logo_active_sig=""
        self._cin_logo_executor=LazyThreadPoolExecutor(2,"ultrastalker-cinematic-logo")
        # Clean-room fast backdrop lane: same proven Twisted timing class as
        # R208 while selection, cache and presentation remain Ultra-owned.
        self._fast_bd_enabled=bool(_FAST_BACKDROP_ENABLED)
        self._fast_bd_req_id=0;self._fast_bd_deferred=None;self._fast_bd_path="";self._fast_bd_sig="";self._fast_bd_started_at=0.0;self._fast_bd_inflight=False
        self._fast_bd_meta_pending=set();self._fast_bd_meta_latest_sig="";self._fast_bd_fast_cache={};self._fast_bd_fast_order=deque();self._fast_bd_fast_limit=12;self._fast_bd_locked_sig="";self._fast_bd_locked_path=""
        self._fast_bd_transport=None
        if self._fast_bd_enabled:
            try:self._fast_bd_transport=FastBackdropTransport()
            except Exception as exc:optional_failure("cinematic.fast_backdrop_transport",exc)
        self._folder_artwork_worker_window=1
        self._cin_cache_exclusive=False
        self._folder_artwork_fast_art_only=False
        PremiumGridBase.__init__(self,session,profile,client,media_type,genre,category_title)
        # Beta67: the right side is the native Details information cluster itself.
        # Catalogue/navigation/HDD hydration remain owned by PremiumGridBase.
        self["cin_backdrop"]=Pixmap()
        self["cin_backdrop_stage"]=Pixmap()
        self["title_logo"]=Pixmap()
        self["artwork_status_bg"]=Pixmap()
        for name in ("panel_bg","overview_bg","cast_card_bg","quality_pill_bg","year_pill_bg","runtime_pill_bg","country_pill_bg","genre_pill_bg","quality_logo","runtime_icon","country_flag"):
            self[name]=Pixmap()
        for name in ("name","quality_text","year_text","runtime_text","country_text","genre_text","age_rating_text","cast_text","description"):
            self[name]=Label("")
        # R199: exact R81 Cinematic rail. Native list rows own selection; no per-key geometry relayout.
        self["cin_list"]=IconMenuList([], width=430, item_height=73, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self._settings_inline_menu=SettingsInlineChoiceOverlay(self,"settings_menu_list","settings_menu_actions",asset,_,optional_failure)
        self._cin_list_icon=asset("settings_icons/artwork.png")
        self._cin_poll=eTimer();self._cin_poll_conn=None;self._cin_poll_interval=600
        try:self._cin_poll_conn=self._cin_poll.timeout.connect(self._cin_poll_hdd)
        except Exception:self._cin_poll.callback.append(self._cin_poll_hdd)
        # Warm Cinematic libraries can have a decoder-ready 1080p backdrop for
        # every row. Loading one on every key-repeat can overwhelm native image
        # surfaces. Move the highlight immediately, but bind the cached backdrop
        # only after navigation has been quiet briefly.
        self._cin_nav_backdrop_timer=eTimer();self._cin_nav_backdrop_conn=None
        try:self._cin_nav_backdrop_conn=self._cin_nav_backdrop_timer.timeout.connect(self._cin_apply_debounced_backdrop)
        except Exception:self._cin_nav_backdrop_timer.callback.append(self._cin_apply_debounced_backdrop)
        self._cin_nav_adaptive_timer=eTimer();self._cin_nav_adaptive_conn=None
        try:self._cin_nav_adaptive_conn=self._cin_nav_adaptive_timer.timeout.connect(self._cin_apply_nav_adaptive_baseline)
        except Exception:self._cin_nav_adaptive_timer.callback.append(self._cin_apply_nav_adaptive_baseline)
        self._cin_fast_info_timer=eTimer();self._cin_fast_info_conn=None
        try:self._cin_fast_info_conn=self._cin_fast_info_timer.timeout.connect(self._cin_apply_fast_info)
        except Exception:self._cin_fast_info_timer.callback.append(self._cin_apply_fast_info)
        self._cin_page_nav_timer=eTimer();self._cin_page_nav_conn=None
        try:self._cin_page_nav_conn=self._cin_page_nav_timer.timeout.connect(self._cin_commit_page_nav)
        except Exception:self._cin_page_nav_timer.callback.append(self._cin_commit_page_nav)
        self.onLayoutFinish.append(self._cin_layout_ready);self.onClose.append(self._cin_stop)
        try:self.onHide.append(self._cin_hidden_start)
        except Exception:pass
        self["section"].setText("")
        self["blue"].setText(_("Cache Artwork"))
        # Exact SeriesEpisodes direction grammar. PremiumGridBase's grid left/right
        # means adjacent card; Cinematic is a vertical episode-style list, so
        # LEFT/RIGHT must page while UP/DOWN move one row with global wrap.
        self["actions"] = ActionMap(["OkCancelActions","ColorActions","DirectionActions","MenuActions","UltraStalkerMenuActions","InfoActions"], {
            "cancel":self.close,"red":self.open_folder_search,"ok":self.open_selected,
            "left":self._cin_page_left,"right":self._cin_page_right,
            "up":self._cin_wrap_up,"down":self._cin_wrap_down,
            "green":self.toggle_selected_favorite,"yellow":self.cycle_folder_sort,
            "blue":self.cache_folder_artwork,"menu":self.open_menu,"info":self.show_information},-1)

    @staticmethod
    def _hq_backdrop_file_path(value):
        """Normalize a TMDb image URL/file_path to the gallery file_path form."""
        text=str(value or "").strip()
        if not text:return ""
        if text.startswith("/"):return text
        m=re.search(r"image\.tmdb\.org/t/p/(?:original|w\d+|h\d+)(/[^?#]+)",text,re.I)
        return m.group(1) if m else ""

    def _hq_backdrop_identity(self,item,row):
        """Return only an already-verified/current TMDb identity for manual HQ replacement."""
        item=item if isinstance(item,dict) else {};row=row if isinstance(row,dict) else {}
        mt="tv" if str(self.media_type or "").lower() in ("series","tv") else "movie"
        for value in (row.get("tmdb_id"),item.get("tmdb_id"),item.get("_ultra_search_verified_tmdb_id"),item.get("_locked_tmdb_id")):
            try:
                text=str(value or "").strip()
                if re.fullmatch(r"\d{1,12}",text):return mt,int(text)
            except Exception:pass
        try:
            hot=load_manifest(self.profile,self.media_type,item) or {}
            text=str(hot.get("tmdb_id") or "").strip()
            if re.fullmatch(r"\d{1,12}",text):
                hmt="tv" if str(hot.get("media_type") or mt).lower() in ("series","tv") else "movie"
                return hmt,int(text)
        except Exception as exc:optional_failure("cinematic.hq_backdrop_identity",exc)
        return mt,None

    def _replace_backdrop_hq(self):
        """Manual, one-title HQ Gallery replacement using the existing canonical Q60 authority."""
        if self.media_type not in ("vod","series") or not self.grid_items:return
        try:
            active=getattr(self,"_hq_backdrop_future",None)
            if active is not None and hasattr(active,"done") and not active.done():
                try:self["status"].setText(_("Backdrop replacement is already running"))
                except Exception:pass
                return
        except Exception:pass
        index=int(self.index);item=dict(self.grid_items[index] or {})
        row=dict((getattr(self,"_cin_page_records",{}) or {}).get(index,{}) or {})
        sig=self._page_visual_key(self.grid_items[index])
        mt,tmdb_id=self._hq_backdrop_identity(item,row)
        if not tmdb_id:
            try:self.session.open(MessageBox,_("TMDb identity is not ready for this title"),MessageBox.TYPE_INFO,timeout=4)
            except Exception:pass
            return
        settings=(getattr(self,"_grid_settings",None) or load_settings() or {})
        credential=str(settings.get("tmdb_credential") or "").strip()
        language=str(settings.get("tmdb_language") or "en-US").strip() or "en-US"
        timeout=min(12,max(5,int(settings.get("timeout",7) or 7)))
        if not credential:
            try:self.session.open(MessageBox,_("Add a TMDB API key or Read Access Token first."),MessageBox.TYPE_INFO,timeout=4)
            except Exception:pass
            return
        current_fp=self._hq_backdrop_file_path(row.get("manual_hq_backdrop_path") or row.get("backdrop_path") or row.get("backdrop_url") or "")
        try:self["status"].setText(_("Searching for a higher-quality backdrop…"))
        except Exception:pass

        def worker():
            client=TMDBClient(credential,language,timeout)
            payload=client._get("/%s/%s/images"%(mt,int(tmdb_id)),{}) or {}
            rows=[x for x in (payload.get("backdrops") or []) if isinstance(x,dict) and x.get("file_path")]
            lang=str(language or "en").split("-")[0].lower()
            valid=[];current=None
            for candidate in rows:
                fp=str(candidate.get("file_path") or "").strip()
                try:w=int(candidate.get("width") or 0);h=int(candidate.get("height") or 0)
                except Exception:w=h=0
                if not fp or w<h or w<1280 or h<600:continue
                iso=str(candidate.get("iso_639_1") or "").lower()
                lang_bonus=3 if not iso else (2 if iso==lang else (1 if iso=="en" else 0))
                score=(w*h,float(candidate.get("vote_average") or 0.0),int(candidate.get("vote_count") or 0),lang_bonus)
                packed=(score,candidate,w,h,fp)
                if fp==current_fp:current=packed
                else:valid.append(packed)
            if not valid:return {"ok":False,"reason":"no_candidate","sig":sig}
            valid.sort(key=lambda x:x[0],reverse=True)
            chosen=None
            if current is not None:
                current_score=current[0]
                for packed in valid:
                    # "HQ" means genuinely better gallery evidence. Equal pixel
                    # count may still win only when TMDb voting is stronger.
                    if packed[0]>current_score:
                        chosen=packed;break
            else:
                chosen=valid[0]
            if chosen is None:return {"ok":False,"reason":"no_better","sig":sig}
            _score,candidate,w,h,fp=chosen
            url=str(TMDBClient.image_url(fp,"original") or "")
            paths=canonical_art_paths(mt,tmdb_id) or {}
            target=str(paths.get("backdrop") or os.path.join(BACKDROPS,"%s_%s.jpg"%(mt,int(tmdb_id))))
            built=_page_backdrop_fetch_original_q60(url,target,timeout=timeout)
            if not (built and os.path.isfile(built) and os.path.getsize(built)>4096):
                return {"ok":False,"reason":"download","sig":sig}
            metadata=_media_library_load(mt,tmdb_id) or {}
            metadata.update({
                "backdrop_local":built,"backdrop_path":fp,"backdrop_url":url,
                "backdrop_source":"manual_hq_gallery:q60","clean_backdrop_selector_version":3,
                "manual_hq_backdrop_locked":True,"manual_hq_backdrop_path":fp,
                "manual_hq_backdrop_url":url,"manual_hq_backdrop_width":int(w),
                "manual_hq_backdrop_height":int(h),"manual_hq_backdrop_vote":float(candidate.get("vote_average") or 0.0),
                "manual_hq_backdrop_updated_at":int(time.time()),"identity_verified":True,
            })
            try:metadata=_media_library_ensure_adaptive(metadata) or metadata
            except Exception:pass
            _media_library_save(mt,tmdb_id,metadata)
            return {"ok":True,"sig":sig,"index":index,"path":built,"file_path":fp,"url":url,"width":w,"height":h,"tmdb_id":tmdb_id,"media_type":mt}

        def completed(future):
            try:result=future.result()
            except Exception as exc:result={"ok":False,"reason":"error","error":str(exc),"sig":sig}
            if getattr(self,"_screen_closed",False):return
            try:self._jobs.put((self._replace_backdrop_hq_done,result,False))
            except Exception:pass
        try:
            future=_HQ_BACKDROP_EXECUTOR.submit(worker,_task_key="cin-hq-backdrop:%x"%id(self),_replace_task_key=True)
            self._hq_backdrop_future=future
            future.add_done_callback(completed)
            try:self._async_set_poll_interval(120)
            except Exception:pass
        except Exception as exc:
            optional_failure("cinematic.hq_backdrop_submit",exc)
            try:self.session.open(MessageBox,_("Backdrop replacement failed"),MessageBox.TYPE_ERROR,timeout=4)
            except Exception:pass

    def _replace_backdrop_hq_done(self,result):
        self._hq_backdrop_future=None
        result=result if isinstance(result,dict) else {}
        if not result.get("ok"):
            message=_("No higher-quality backdrop found") if str(result.get("reason") or "") in ("no_candidate","no_better") else _("Backdrop replacement failed")
            try:self["status"].setText(message)
            except Exception:pass
            try:self.session.open(MessageBox,message,MessageBox.TYPE_INFO,timeout=4)
            except Exception:pass
            return
        path=str(result.get("path") or "")
        sig=str(result.get("sig") or "")
        try:
            if sig and sig==self._fast_bd_current_sig() and self.grid_items:
                idx=int(self.index);row=self._cin_page_records.get(idx,{}) or {}
                row.update({
                    "backdrop_local":path,"backdrop_path":result.get("file_path"),"backdrop_url":result.get("url"),
                    "backdrop_source":"manual_hq_gallery:q60","clean_backdrop_selector_version":3,
                    "manual_hq_backdrop_locked":True,"manual_hq_backdrop_path":result.get("file_path"),
                    "manual_hq_backdrop_url":result.get("url"),"manual_hq_backdrop_width":result.get("width"),
                    "manual_hq_backdrop_height":result.get("height"),"tmdb_id":result.get("tmdb_id"),
                })
                self._cin_page_records[idx]=row
                self._cin_page_backdrops[idx]=path
                self._fast_bd_locked_sig="";self._fast_bd_locked_path=""
                self._fast_bd_bind_local(path,sig,source="manual_hq_gallery_q60")
            try:self["status"].setText(_("HQ backdrop applied"))
            except Exception:pass
        except Exception as exc:
            optional_failure("cinematic.hq_backdrop_apply",exc)
            try:self.session.open(MessageBox,_("Backdrop replacement failed"),MessageBox.TYPE_ERROR,timeout=4)
            except Exception:pass

    def _menu_selected(self,choice):
        if not choice:return
        try:
            if choice[1]=="replace_backdrop_hq":
                self._replace_backdrop_hq();return
        except Exception:pass
        return PremiumGridBase._menu_selected(self,choice)

    def open_menu(self):
        choices=[(_("Refresh page"),"refresh"),(_("First page"),"first"),(_("Toggle favorite"),"favorite")]
        if self.media_type in ("vod","series") and self.grid_items:
            choices.append((_("Replace Backdrop (HQ)"),"replace_backdrop_hq"))
            choices.append((_("Set as Home Hero"),"set_home_hero"))
        if self.media_type=="vod" and self.grid_items:
            state=self._grid_item_state.get(id(self.grid_items[self.index]),{})
            choices.append((_("Mark unwatched") if state.get("completed") else _("Mark watched"),"unwatch" if state.get("completed") else "watch"))
        # R168 menu-light pass: same actions and anchor, but pure floating text.
        # No glass/adaptive row generation or pixmap bind is allowed on MENU open.
        # Normal rows are bright white; selection is the shared green + larger font.
        self._settings_inline_menu.show(choices,selection=0,right=1850,region_top=240,region_h=400,on_accept=self._menu_selected,min_card_w=220,max_card_w=460,padding=44,anchor_bottom=670,visual_style="text_only",row_h=58,max_visible=6)

    def _cin_sharp_details_overview(self, source):
        """Use the same nine-slice sharpening geometry as Details: 1318x170."""
        try:
            source=str(source or "")
            if not source or not os.path.isfile(source):return ""
            tw,th=1318,170
            with _PILImage.open(source) as probe:sw,sh=probe.size
            if (sw,sh)==(tw,th):return source
            # Use the exact same persistent cache key/path as Native Details,
            # not a Cinematic copy. If Details already refined this glass, this is
            # literally the same file; otherwise we generate the same nine-slice
            # once into the Details cache namespace.
            root=os.path.join(GENERATED,"details_sharp_v6513")
            if not os.path.isdir(root):os.makedirs(root,mode=0o700)
            stamp="%s|%s|%s|%s|%s"%(source,os.path.getmtime(source),"overview_detail",tw,th)
            out=os.path.join(root,hashlib.sha1(stamp.encode("utf-8","ignore")).hexdigest()[:24]+".png")
            if os.path.isfile(out) and os.path.getsize(out)>256:return out
            with _PILImage.open(source) as im:
                im=im.convert("RGBA");sw,sh=im.size
                cx=max(10,min(38,sw//5,tw//5));cy=max(8,min(22,sh//4,th//4))
                left=right=cx;top=bottom=cy
                canvas=_PILImage.new("RGBA",(tw,th),(0,0,0,0))
                sx=(0,left,sw-right,sw);sy=(0,top,sh-bottom,sh)
                dx=(0,left,tw-right,tw);dy=(0,top,th-bottom,th)
                res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
                for yi in range(3):
                    for xi in range(3):
                        tile=im.crop((sx[xi],sy[yi],sx[xi+1],sy[yi+1]))
                        size=(dx[xi+1]-dx[xi],dy[yi+1]-dy[yi])
                        if tile.size!=size:tile=tile.resize(size,res)
                        canvas.alpha_composite(tile,(dx[xi],dy[yi]))
                temp=out+".tmp.%s"%os.getpid();canvas.save(temp,"PNG",optimize=True);os.replace(temp,out)
            return out
        except Exception as exc:
            optional_failure("cinematic.details_overview_sharp",exc);return str(source or "")

    def _cin_apply_details_overview(self,item,row):
        """Adopt Native Details chrome only as a complete panel/overview pair.

        A partially-written/legacy bundle must never replace the already-visible
        Details chrome with a neutral fallback for one event-loop turn.  Holding the
        current pair eliminates the visible overview blink while a title-specific
        adaptive bundle finishes on HDD.
        """
        try:
            bundle=_load_visual_bundle(self.profile,self.media_type,item,row) or {}
            chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
            panel=str(chrome.get("panel_detail") or "") if isinstance(chrome,dict) else ""
            overview=str(chrome.get("overview_detail") or "") if isinstance(chrome,dict) else ""
            if panel and overview and os.path.isfile(panel) and os.path.isfile(overview):
                self._apply_native_detail_chrome(chrome)
        except Exception as exc:optional_failure("cinematic.details_cluster_apply",exc)

    # ------------------------------------------------------------------
    # R209 FAST FIRST PAINT + POST-PAINT CINEMATIC/ADAPTIVE
    # ------------------------------------------------------------------
    def _fast_bd_current_sig(self):
        try:
            if not self.grid_items:return ""
            return self._page_visual_key(self.grid_items[self.index])
        except Exception:return ""

    def _fast_bd_cancel(self,clear_file=False):
        deferred=getattr(self,"_fast_bd_deferred",None)
        if deferred is not None:
            try:
                if not getattr(deferred,"called",False):deferred.cancel()
            except Exception:pass
        self._fast_bd_deferred=None;self._fast_bd_inflight=False
        if clear_file:
            path=str(getattr(self,"_fast_bd_path","") or "");self._fast_bd_path=""
            try:
                if path and path.startswith("/tmp/") and os.path.isfile(path):os.unlink(path)
            except Exception:pass

    @staticmethod
    def _fast_bd_original_cache_path(url):
        """Persistent R242 target for the exact Clean Official TMDb backdrop.

        The request URL intentionally remains /original/.  Only the stored form
        changes: one Full-HD JPEG keyed by that exact original URL.
        """
        url=str(url or "").strip()
        if not (url.startswith("https://image.tmdb.org/t/p/original/") or url.startswith("http://image.tmdb.org/t/p/original/")):
            return ""
        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()[:32]
        return os.path.join(_R242_FULLHD_ROOT,digest+".jpg")

    @staticmethod
    def _fast_bd_build_fullhd(raw_path,destination):
        """Recompress one downloaded ORIGINAL to at-most 1920x1080 JPEG q60.

        No crop, no artwork re-selection, no presentation overlay.  Aspect ratio is
        preserved and smaller originals are never upscaled.  This runs only in the
        dedicated R242 worker after native first paint has already won the screen.
        """
        raw_path=str(raw_path or "").strip();destination=str(destination or "").strip()
        if _PILImage is None or not raw_path or not destination:return ""
        if not (os.path.isfile(raw_path) and os.path.getsize(raw_path)>1024):return ""
        if not ensure_persistent_dirs(os.path.dirname(destination)):return ""
        temp=destination+".tmp.%s.%s"%(os.getpid(),threading.get_ident())
        try:
            with _PILImage.open(raw_path) as image:
                image=image.convert("RGB");w,h=image.size
                if w<16 or h<16:return ""
                scale=min(1.0,1920.0/float(w),1080.0/float(h))
                if scale<0.999:
                    res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
                    image=image.resize((max(1,int(round(w*scale))),max(1,int(round(h*scale)))),res)
                image.save(temp,"JPEG",quality=60,subsampling=2,optimize=False,progressive=False)
            if not (os.path.isfile(temp) and os.path.getsize(temp)>4096):return ""
            os.replace(temp,destination)
            return destination
        except Exception as exc:
            optional_failure("cinematic.r242_fullhd_build",exc);return ""
        finally:
            try:
                if os.path.exists(temp):os.unlink(temp)
            except Exception:pass

    def _fast_bd_schedule_fullhd_store(self,raw_path,destination):
        """Persist after first paint and wait for remote navigation to go quiet."""
        raw_path=str(raw_path or "").strip();destination=str(destination or "").strip()
        if not raw_path or not destination:return
        if not (os.path.isfile(raw_path) and os.path.getsize(raw_path)>1024):return
        with _R242_FULLHD_LOCK:
            if destination in _R242_FULLHD_PENDING:return
            _R242_FULLHD_PENDING.add(destination)
        owner=weakref.ref(self)
        def worker():
            try:
                # Never compete with arrow-repeat.  The ORIGINAL has already been
                # handed to Enigma2, so storage work can afford to wait.
                deadline=time.monotonic()+4.0
                while time.monotonic()<deadline:
                    screen=owner()
                    if screen is None:break
                    try:
                        quiet=(time.monotonic()-float(getattr(screen,"_cin_last_nav_at",0.0) or 0.0))>=0.85
                    except Exception:quiet=True
                    if quiet:break
                    time.sleep(0.20)
                self._fast_bd_build_fullhd(raw_path,destination)
            except Exception as exc:optional_failure("cinematic.r242_fullhd_store",exc)
            finally:
                with _R242_FULLHD_LOCK:_R242_FULLHD_PENDING.discard(destination)
        try:_R242_FULLHD_EXECUTOR.submit(worker)
        except Exception:
            with _R242_FULLHD_LOCK:_R242_FULLHD_PENDING.discard(destination)

    @staticmethod
    def _fast_bd_local_full_quality(path):
        """Accept only a genuinely full-screen local source for direct paint.

        ArtworkV2 intentionally stores w1280 masters. R196 must not stretch those
        to 1920 anymore. A file in the dedicated ORIGINAL cache is trusted even
        when a rare TMDb original is itself narrower than 1920; otherwise inspect
        only the image header and require near-full-HD width.
        """
        path=str(path or "").strip()
        try:
            if not (path and os.path.isfile(path) and os.path.getsize(path)>1024):return False
            root=os.path.abspath(_R242_FULLHD_ROOT)+os.sep
            absolute=os.path.abspath(path)
            # R249 shares one top-level backdrops folder with ArtworkV2's legacy
            # canonical w1280 lane. Trust only this Q60 worker's URL-hash files
            # without dimensions; movie_123/tv_123 canonical files must still pass
            # the >=1800px full-screen gate or Cinematic will fetch ORIGINAL.
            if absolute.startswith(root) and re.match(r"^[0-9a-f]{32}\.jpg$",os.path.basename(absolute),re.I):return True
            if _PILImage is None:return False
            with _PILImage.open(path) as image:
                w,h=image.size
            return int(w)>=1800 and int(h)>=700
        except Exception:return False

    @classmethod
    def _fast_bd_clean_local_from_row(cls,row):
        """Return a sealed Clean Official local only when it is full quality."""
        row=row if isinstance(row,dict) else {}
        try:
            if int(row.get("clean_backdrop_selector_version") or 0)<3:return ""
        except Exception:return ""
        path=str(row.get("backdrop_local") or "").strip()
        return path if cls._fast_bd_local_full_quality(path) else ""

    @staticmethod
    def _fast_bd_url_from_row(row):
        """Return only the sealed Clean Official TMDb ORIGINAL URL.

        Never paint a stored w1280 URL in Cinematic. If the manifest contains a
        TMDb file_path, rebuild the URL as ORIGINAL; otherwise rewrite a sealed
        image.tmdb.org URL in-place.
        """
        row=row if isinstance(row,dict) else {}
        try:
            if int(row.get("clean_backdrop_selector_version") or 0)<3:return ""
        except Exception:return ""
        fp=str((row.get("manual_hq_backdrop_path") if row.get("manual_hq_backdrop_locked") else "") or row.get("backdrop_path") or "").strip()
        if fp.startswith("/"):
            try:return str(TMDBClient.image_url(fp,"original") or "")
            except Exception:return ""
        if "image.tmdb.org/t/p/" in fp:
            fp=re.sub(r"(/t/p/)(?:original|w\d+|h\d+)(/)",r"\1original\2",fp,count=1,flags=re.I)
            if fp.startswith(("http://","https://")):return fp
        url_keys=("manual_hq_backdrop_url","backdrop_url","_backdrop_url","tmdb_backdrop_url") if row.get("manual_hq_backdrop_locked") else ("backdrop_url","_backdrop_url","tmdb_backdrop_url")
        for key in url_keys:
            value=str(row.get(key) or "").strip()
            if "image.tmdb.org/t/p/" not in value:continue
            value=re.sub(r"(/t/p/)(?:original|w\d+|h\d+)(/)",r"\1original\2",value,count=1,flags=re.I)
            if value.startswith(("http://","https://")):return value
        return ""

    def _fast_bd_cached_original(self,url):
        try:
            path=self._fast_bd_original_cache_path(url)
            if path and hdd_read_ready() and os.path.isfile(path) and os.path.getsize(path)>1024:return path
        except Exception:pass
        return ""

    def _fast_bd_focus_stable(self,quiet_window=0.80):
        """True only after remote navigation has been quiet long enough.

        R197: adaptive/HDD colour work is forbidden during key-repeat.  The rail,
        title logo and fast backdrop may still move immediately, but the current
        adaptive remains held until focus settles.
        """
        try:
            return (time.monotonic()-float(getattr(self,"_cin_last_nav_at",0.0) or 0.0))>=float(quiet_window)
        except Exception:
            return False

    def _fast_bd_bind_local(self,path,sig,req_id=None,source="local"):
        """Direct native bind. No shared cache, Pillow, manifest or adaptive work."""
        try:
            if not self._fast_bd_enabled or self._screen_closed:return False
            if sig!=self._fast_bd_current_sig():return False
            if req_id is not None and int(req_id)!=int(self._fast_bd_req_id):return False
            path=str(path or "")
            if not (path and os.path.isfile(path) and os.path.getsize(path)>1024):return False
            inst=self["cin_backdrop"].instance
            if inst is None:return False
            old=str(getattr(self,"_fast_bd_path","") or "")
            # Do exactly what the diagnostic is meant to test: hand the completed
            # image straight to Enigma2's native Pixmap with no Ultra derivative.
            inst.setPixmapFromFile(path);self["cin_backdrop"].show()
            self._cin_pixmap_paths["cin_backdrop"]=path;self._fast_bd_path=path;self._fast_bd_sig=sig
            # R195 single backdrop authority: the first CLEAN direct paint for this
            # focus owns the screen until focus changes. Metadata/title-logo lanes
            # are never allowed to swap in a second image for the same title.
            self._fast_bd_locked_sig=sig;self._fast_bd_locked_path=path
            elapsed=int((time.monotonic()-float(getattr(self,"_fast_bd_started_at",0.0) or time.monotonic()))*1000)
            logging.getLogger("UltraStalker").info("FAST_BACKDROP PAINT sig=%s source=%s elapsed_ms=%d path=%s",sig,source,elapsed,path)
            # R199: direct backdrop is image-only. Adaptive/metadata belong to stable-focus Details authority.
            try:self._fast_bd_fast_store(sig,backdrop=path)
            except Exception as exc:optional_failure("cinematic.r199_fast_store_backdrop",exc)
            # Keep the proven R197 single-backdrop handoff into Native Details.
            # Details may crop/present these exact bytes, but must not choose a
            # competing landscape after Cinematic has already established one.
            try:
                current=self.grid_items[self.index] if self.grid_items else {}
                if isinstance(current,dict):
                    current["_backdrop_source_local"]=path
                    current["_cin_provider_backdrop_local"]=path
                    current["_details_clean_backdrop_source"]=path
                    current["_details_clean_backdrop_locked"]=True
            except Exception as exc:optional_failure("cinematic.r199_details_backdrop_handoff",exc)
            # Direct /tmp backdrops use one bounded per-title RAM/LRU cache.
            # Never delete the previous title here; revisit must be an atomic warm hit.
            return True
        except Exception as exc:
            optional_failure("cinematic.fast_bd_bind",exc);return False

    def _fast_bd_fast_store(self,sig,backdrop=None):
        if not sig:return
        cache=getattr(self,"_fast_bd_fast_cache",None)
        order=getattr(self,"_fast_bd_fast_order",None)
        if not isinstance(cache,dict) or order is None:return
        entry=cache.get(sig) if isinstance(cache.get(sig),dict) else {}
        if backdrop and os.path.isfile(str(backdrop)):entry["backdrop"]=str(backdrop)
        cache[sig]=entry
        try:
            while sig in order:order.remove(sig)
            order.append(sig)
            limit=max(4,int(getattr(self,"_fast_bd_fast_limit",12) or 12))
            while len(order)>limit:
                old=order.popleft();old_entry=cache.pop(old,None) or {}
                old_path=str(old_entry.get("backdrop") or "")
                if old_path.startswith("/tmp/us_r243_original_") and old_path!=str(getattr(self,"_fast_bd_path","") or ""):
                    try:
                        if os.path.isfile(old_path):os.unlink(old_path)
                    except Exception:pass
        except Exception:pass

    def _fast_bd_fast_cleanup(self):
        try:
            for entry in list((getattr(self,"_fast_bd_fast_cache",{}) or {}).values()):
                path=str((entry or {}).get("backdrop") or "")
                if path.startswith(("/tmp/us_r188_fast_bd_","/tmp/us_r243_original_")):
                    try:
                        if os.path.isfile(path):os.unlink(path)
                    except Exception:pass
        finally:
            self._fast_bd_fast_cache={};self._fast_bd_fast_order=deque()

    def _fast_bd_try_fast_warm(self,item,row=None):
        """Restore only the already-won clean backdrop for a revisited title."""
        try:
            sig=self._page_visual_key(item);entry=(getattr(self,"_fast_bd_fast_cache",{}) or {}).get(sig) or {}
            backdrop=str(entry.get("backdrop") or "")
            if not (backdrop and os.path.isfile(backdrop)):return False
            self._fast_bd_cancel(clear_file=False);self._fast_bd_req_id+=1;self._fast_bd_started_at=time.monotonic()
            if not self._fast_bd_bind_local(backdrop,sig,req_id=self._fast_bd_req_id,source="r199-warm"):return False
            return True
        except Exception as exc:optional_failure("cinematic.r199_warm",exc);return False

    def _fast_bd_body_ready(self,body,req_id,sig,url):
        if not self._fast_bd_enabled:return None
        if int(req_id)!=int(self._fast_bd_req_id) or sig!=self._fast_bd_current_sig():return None
        self._fast_bd_inflight=False
        destination=self._fast_bd_original_cache_path(url)
        local_path=""
        try:
            # R243 fix: preserve a decoder-recognizable image extension for the temporary
            # ORIGINAL first-paint file.  R242 used .img, which some Enigma2 builds
            # refuse to decode via setPixmapFromFile even when the bytes are valid.
            # The exact downloaded ORIGINAL bytes still go
            # straight to Enigma2 from /tmp.  HDD compression starts only after
            # that bind, so network and visible first-paint behavior stay intact.
            tail=str(url or "").split("?",1)[0].lower()
            suffix=".png" if tail.endswith(".png") else ".jpg"
            fd,local_path=tempfile.mkstemp(prefix="us_r243_original_",suffix=suffix,dir="/tmp")
            with os.fdopen(fd,"wb") as handle:handle.write(body)
            painted=self._fast_bd_bind_local(local_path,sig,req_id=req_id,source="twisted-original-first-paint")
            if painted and destination and hdd_ready():
                self._fast_bd_schedule_fullhd_store(local_path,destination)
            if local_path!=getattr(self,"_fast_bd_path","") and local_path.startswith("/tmp/") and os.path.isfile(local_path):os.unlink(local_path)
        except Exception as exc:
            optional_failure("cinematic.fast_backdrop_body",exc)
            try:
                if local_path and local_path.startswith("/tmp/") and os.path.isfile(local_path):os.unlink(local_path)
            except Exception:pass
        return None

    def _fast_bd_http_status(self,code,req_id,sig,url):
        if int(req_id)==int(self._fast_bd_req_id):self._fast_bd_inflight=False
        return None

    def _fast_bd_download_error(self,failure,req_id,sig,url):
        if int(req_id)==int(self._fast_bd_req_id):
            self._fast_bd_inflight=False;self._fast_bd_deferred=None
        try:optional_failure("cinematic.fast_backdrop_download",failure)
        except Exception:pass
        return None

    def _fast_bd_start_url(self,url,sig):
        if not self._fast_bd_enabled or not url or sig!=self._fast_bd_current_sig():return False
        cached=self._fast_bd_cached_original(url)
        if cached:
            self._fast_bd_cancel(clear_file=False);self._fast_bd_req_id+=1;self._fast_bd_started_at=time.monotonic()
            return self._fast_bd_bind_local(cached,sig,req_id=self._fast_bd_req_id,source="local-cache")
        transport=getattr(self,"_fast_bd_transport",None)
        if transport is None:return False
        self._fast_bd_cancel(clear_file=False)
        self._fast_bd_req_id+=1;req_id=int(self._fast_bd_req_id);self._fast_bd_sig=sig;self._fast_bd_started_at=time.monotonic();self._fast_bd_inflight=True
        try:
            deferred=transport.fetch(
                url,
                lambda:self._fast_bd_enabled and int(req_id)==int(self._fast_bd_req_id) and sig==self._fast_bd_current_sig(),
                lambda body:self._fast_bd_body_ready(body,req_id,sig,url),
                lambda code:self._fast_bd_http_status(code,req_id,sig,url),
            )
            self._fast_bd_deferred=deferred
            deferred.addErrback(self._fast_bd_download_error,req_id,sig,url)
            return True
        except Exception as exc:
            self._fast_bd_deferred=None;self._fast_bd_inflight=False
            optional_failure("cinematic.fast_backdrop_start",exc);return False

    def _fast_bd_meta_fallback(self,item,row,sig):
        """Resolve ONLY the Clean Official TMDb primary backdrop for first paint.

        If a verified TMDb id is already known, skip title search completely.
        Otherwise do the same tiny search needed to establish identity, then ask
        the title Details endpoint once. TMDb primary is the visible authority;
        Gallery is fallback-only when Details has no primary backdrop.
        """
        if sig==str(getattr(self,"_fast_bd_meta_latest_sig","") or ""):return
        self._fast_bd_meta_latest_sig=sig;self._fast_bd_meta_pending.add(sig)
        item_copy=dict(item or {});row_copy=dict(row or {});media_type=self.media_type
        settings=(getattr(self,"_grid_settings",None) or load_settings() or {})
        credential=str(settings.get("tmdb_credential") or "").strip()
        language=str(settings.get("tmdb_language") or "en-US").strip() or "en-US"
        timeout=min(7,max(3,int(settings.get("timeout",7) or 7)))
        raw_title=self._rail_title(item_copy,row_copy) or str(row_copy.get("title") or row_copy.get("name") or item_copy.get("name") or item_copy.get("title") or "")
        title=_catalogue_title(raw_title) or str(raw_title or "").strip()
        year=None
        for value in (row_copy.get("year"),item_copy.get("year"),row_copy.get("release_date"),item_copy.get("release_date"),row_copy.get("first_air_date"),item_copy.get("first_air_date")):
            m=re.search(r"(?:19|20)\d{2}",str(value or ""))
            if m:
                year=int(m.group(0));break
        def worker():
            found=dict(row_copy)
            def stale():
                if sig==self._fast_bd_current_sig():return False
                self._fast_bd_meta_pending.discard(sig)
                if sig==str(getattr(self,"_fast_bd_meta_latest_sig","") or ""):self._fast_bd_meta_latest_sig=""
                return True
            if stale():return
            try:
                if not credential:raise RuntimeError("TMDb credential unavailable")
                client=TMDBClient(credential,language,timeout)
                mt="tv" if str(media_type or "").lower() in ("series","tv") else "movie"
                tid=None
                for value in (row_copy.get("tmdb_id"),item_copy.get("tmdb_id"),item_copy.get("_ultra_search_verified_tmdb_id")):
                    try:
                        if value not in (None,""):tid=int(value);break
                    except Exception:pass
                if not tid:
                    if not title:raise RuntimeError("title unavailable")
                    params={"query":title,"language":language,"include_adult":"false","page":1}
                    if year:params["first_air_date_year" if mt=="tv" else "year"]=year
                    payload=client._get("/search/%s"%mt,params) or {}
                    results=payload.get("results") if isinstance(payload,dict) else []
                    for candidate in (results or [])[:8]:
                        if isinstance(candidate,dict) and candidate.get("id"):
                            tid=int(candidate.get("id"));break
                    if stale():return
                if not tid:raise RuntimeError("verified TMDb id unavailable")
                # Match backdrop_clean_runtime exactly: Details primary is the
                # official authority; neutral gallery is fallback-only.
                details=client._get("/%s/%s"%(mt,tid),{"append_to_response":"images","include_image_language":"null,en,%s"%str(language or "en").split("-")[0]}) or {}
                # R260 official-artwork authority: the verified title's Details
                # primary backdrop is the visible authority. Gallery is fallback
                # only when TMDb provides no primary path at all.
                fp=str((details or {}).get("backdrop_path") or "").strip()
                choice="tmdb_primary" if fp else "";iso=""
                images=details.get("images") if isinstance(details.get("images"),dict) else {}
                if not fp:
                    try:
                        from .backdrop_clean_runtime import rank_clean_backdrops
                        ranked=rank_clean_backdrops(images.get("backdrops") or [],language,16)
                    except Exception:
                        ranked=[]
                    if ranked:
                        pick=ranked[0];fp=str(pick.get("file_path") or "");iso=str(pick.get("iso_639_1") or "");choice="gallery_fallback"
                if fp:
                    found["tmdb_id"]=tid;found["media_type"]=mt
                    found["backdrop_path"]=fp;found["backdrop_url"]=TMDBClient.image_url(fp,"original")
                    found["clean_backdrop_selector_version"]=3
                    found["clean_backdrop_choice"]=choice;found["clean_backdrop_iso"]=iso
                    found["identity_verified"]=True
            except Exception as exc:optional_failure("cinematic.r193_clean_identity",exc)
            if stale():return
            try:self._cin_jobs.put({"kind":"fast_backdrop_meta","sig":sig,"row":found})
            except Exception:pass
        try:
            getattr(self,"_cin_fast_meta_executor",_FAST_BACKDROP_META_EXECUTOR).submit(worker,_task_key="cin-r193-meta:%x"%id(self),_replace_task_key=True);self._cin_kick_poll()
        except Exception as exc:
            self._fast_bd_meta_pending.discard(sig)
            if sig==str(getattr(self,"_fast_bd_meta_latest_sig","") or ""):self._fast_bd_meta_latest_sig=""
            optional_failure("cinematic.r193_clean_meta_submit",exc)

    def _fast_bd_schedule(self,item,row,resolved=None):
        if not self._fast_bd_enabled or self._screen_closed:return
        sig=self._page_visual_key(item)
        if not sig or sig!=self._fast_bd_current_sig():return
        # Warm titles never re-enter Raw -> Instant -> Final.
        if self._fast_bd_try_fast_warm(item,row):return
        # R195: once one clean direct backdrop has actually painted for this focus,
        # metadata/title-logo/canonical rows may hydrate but can never swap it.
        if str(getattr(self,"_fast_bd_locked_sig","") or "")==sig:
            locked=str(getattr(self,"_fast_bd_locked_path","") or "")
            if locked and os.path.isfile(locked):return
        merged=dict(row or {})
        if isinstance(resolved,dict):
            for k,v in resolved.items():
                if v not in (None,"",[],{}):merged[k]=v
        # Already-sealed canonical clean artwork is safe to display immediately.
        local=self._fast_bd_clean_local_from_row(merged)
        if local:
            if str(getattr(self,"_fast_bd_sig","") or "")==sig and str(getattr(self,"_fast_bd_path","") or "")==local:return
            self._fast_bd_started_at=time.monotonic();self._fast_bd_req_id+=1
            self._fast_bd_bind_local(local,sig,req_id=self._fast_bd_req_id,source="clean-canonical");return
        # A network URL is trusted only when the Clean Official selector sealed it.
        url=self._fast_bd_url_from_row(merged)
        if url:
            if str(getattr(self,"_fast_bd_sig","") or "")==sig:
                if bool(getattr(self,"_fast_bd_inflight",False)):return
                current_path=str(getattr(self,"_fast_bd_path","") or "")
                if current_path and os.path.isfile(current_path):return
            self._fast_bd_start_url(url,sig);return
        # Never first-paint an unsealed provider/title-card backdrop. Resolve the
        # verified TMDb neutral/textless artwork directly instead.
        self._fast_bd_meta_fallback(item,merged,sig)

    # R81 title-logo authority: fixed 420x144 slot above the lightweight rail.
    # It resolves only after stable focus; arrow navigation never touches it.
    def _cin_title_logo_signature(self,item,row):
        """Current-item request key + PGV2 shared-cache lookup, HDD only.

        The old Cinematic path refused to schedule logo discovery until tmdb_id
        was already hydrated.  Poster Grid V2 does not have that dependency: the
        worker discovers canonical identity itself.  Keep the request key bound
        to the selected catalogue item so it survives that hydration step.
        """
        try:
            row=row if isinstance(row,dict) else {}
            title=self._rail_title(item,row) or str(row.get("title") or row.get("name") or "")
            key=str(self._page_visual_key(item) or "")
            if not key:
                basis="%s|%s"%(self.media_type,str(title or "").strip().casefold())
                key=hashlib.sha1(basis.encode("utf-8","ignore")).hexdigest()
            cached=ultra_title_logo_cached(self.media_type,item,row,(420,144),title) or ""
            return ("%s:item:%s"%(self.media_type,key),str(cached or ""))
        except Exception:return ("","")

    def _cin_title_logo_display_target(self,source):
        """Return the Cinematic fitted-logo cache path without decoding artwork."""
        try:
            source=str(source or "")
            if not source or not os.path.isfile(source) or os.path.getsize(source)<=256:return ""
            st=os.stat(source)
            stamp="%s|%s"%(int(getattr(st,"st_mtime_ns",int(st.st_mtime*1000000000))),int(st.st_size))
            digest=hashlib.sha1((source+"|"+stamp+"|cinematic-title-logo-420x144-v1").encode("utf-8","ignore")).hexdigest()[:20]
            return os.path.join(THUMB_CACHE_DIR,"title_logos","cinematic_%s_420x144_v1.png"%digest)
        except Exception:return ""

    def _cin_prepare_title_logo_display(self,source):
        """Build the fitted 420x144 Cinematic logo on the background worker only."""
        try:
            source=str(source or "");target=self._cin_title_logo_display_target(source)
            if _PILImage is None or not target:return ""
            if os.path.isfile(target) and os.path.getsize(target)>256:return target
            os.makedirs(os.path.dirname(target),mode=0o700,exist_ok=True)
            with _PILImage.open(source) as src:
                im=src.convert("RGBA")
                try:
                    bbox=im.getchannel("A").getbbox()
                    if bbox:im=im.crop(bbox)
                except Exception:pass
                if im.width<2 or im.height<2:return ""
                max_w,max_h=404,132
                scale=min(float(max_w)/float(im.width),float(max_h)/float(im.height),1.35)
                nw=max(1,int(round(im.width*scale)));nh=max(1,int(round(im.height*scale)))
                if (nw,nh)!=im.size:
                    res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
                    im=im.resize((nw,nh),res)
                canvas=_PILImage.new("RGBA",(420,144),(0,0,0,0))
                canvas.alpha_composite(im,((420-nw)//2,max(0,(144-nh)//2)))
                tmp=target+".tmp.%s"%os.getpid()
                canvas.save(tmp,"PNG",compress_level=1);os.replace(tmp,target)
            return target if os.path.isfile(target) and os.path.getsize(target)>256 else ""
        except Exception as exc:
            optional_failure("cinematic.title_logo_display_prepare",exc);return ""

    def _cin_show_title_logo(self,item,row):
        """Hold last-good while pending; hide it only when CURRENT is logo-less."""
        sig,path=self._cin_title_logo_signature(item,row)
        try:
            if sig and sig!=str(getattr(self,"_cin_logo_active_sig","") or ""):
                old=getattr(self,"_cin_logo_cancel",None)
                if old is not None:old.set()
                self._cin_logo_cancel=threading.Event();self._cin_logo_active_sig=sig
            if path and os.path.isfile(path) and os.path.getsize(path)>256 and self["title_logo"].instance is not None:
                if self._cin_logo_path!=path:self["title_logo"].instance.setPixmapFromFile(path);self._cin_logo_path=path
                self["title_logo"].show();return

            from .ui_grid_screens import _pgv2_logo_seal_state_detached,_pgv2_title_logo_probe_missing_detached
            seal_state,_seal_path,_seal=_pgv2_logo_seal_state_detached(self.profile,self.media_type,item,row)
            probe_missing=(_pgv2_title_logo_probe_missing_detached(item,row) if seal_state!="logo" else False)
            if seal_state=="missing" or probe_missing:
                # Cinematic already has the selected name in its rail. A confirmed
                # logo-less title therefore hides stale art instead of adding text.
                self["title_logo"].hide();self._cin_logo_path=""
                if seal_state=="missing":return
            else:
                held=str(getattr(self,"_cin_logo_path","") or "")
                if held and os.path.isfile(held) and os.path.getsize(held)>256:self["title_logo"].show()
                else:self["title_logo"].hide();self._cin_logo_path=""
        except Exception:pass
        if not sig or sig in self._cin_logo_pending:return
        if getattr(self,"_cin_logo_cancel",None) is None:
            self._cin_logo_cancel=threading.Event();self._cin_logo_active_sig=sig
        ev=self._cin_logo_cancel
        self._cin_logo_pending.add(sig);item_copy=dict(item or {});row_copy=dict(row or {})
        def worker():
            result="";logo_state="pending";resolved_row=dict(row_copy or {})
            try:
                from .ui_grid_screens import (
                    _pgv2_bridge_logo_from_seal_detached,_pgv2_authoritative_logo_row_detached,
                    _pgv2_resolve_title_logo_detached,_pgv2_write_logo_seal_detached
                )
                state,bridged,_seal=_pgv2_bridge_logo_from_seal_detached(
                    self.profile,self.media_type,item_copy,resolved_row,(420,144),cancel_event=ev
                )
                if ev is not None and ev.is_set():return
                if state=="missing":logo_state="missing"
                elif state=="logo" and bridged:
                    result=bridged;logo_state="logo"
                else:
                    settings=(getattr(self,"_grid_settings",None) or load_settings() or {})
                    resolved_row=_pgv2_authoritative_logo_row_detached(
                        self.profile,self.media_type,item_copy,resolved_row,cancel_event=ev,settings=settings
                    )
                    if ev is not None and ev.is_set():return
                    result,resolved_row=_pgv2_resolve_title_logo_detached(
                        self.profile,self.media_type,item_copy,resolved_row,cancel_event=ev,settings=settings,canvas_size=(420,144)
                    )
                    if ev is not None and ev.is_set():return
                    logo_state="logo" if result and os.path.isfile(result) else "missing"
                    _pgv2_write_logo_seal_detached(self.profile,self.media_type,item_copy,resolved_row,result)
            except Exception as exc:optional_failure("cinematic.title_logo_prepare",exc)
            try:self._cin_jobs.put({"kind":"title_logo","sig":sig,"path":result,"missing":(logo_state=="missing")})
            except Exception:pass
        try:
            ex=getattr(self,"_cin_logo_executor",None) or getattr(self,"_cin_hybrid_executor",_CIN_HYBRID_EXECUTOR)
            future=ex.submit(worker,_task_key="cin-logo:%x"%id(self),_replace_task_key=True)
            def _logo_done(done,_sig=sig):
                if done.cancelled():self._cin_logo_pending.discard(_sig)
            future.add_done_callback(_logo_done);self._cin_kick_poll()
        except Exception:self._cin_logo_pending.discard(sig)

    def _cin_nav_total_pages(self):
        total=int(self._active_total() or 0)
        return max(1,(total+self.page_size-1)//self.page_size) if total else max(1,int(self.page or 1))

    def _cin_prefetch_catalogue_pages(self):
        """Focus-driven runtime: never warm neighbouring catalogue pages."""
        return
        if getattr(self,"_cin_page_warm_running",False):return
        total=int(self._active_total() or 0)
        if total<=self.page_size:return
        total_pages=max(1,(total+self.page_size-1)//self.page_size)
        current=max(1,min(int(self.page or 1),total_pages))
        targets=[]
        for p in (current+1,current-1):
            if 1<=p<=total_pages and p!=current and p not in targets:
                targets.append(p)
        if not targets:return
        self._cin_page_warm_running=True
        def worker():
            try:
                for target in targets:
                    if getattr(self,"_screen_closed",False):break
                    with self._prepared_page_lock:
                        if target in self._prepared_page_cache:continue
                    try:
                        payload=self._prepare_grid_page(target,None)
                        if payload:self._cache_prepared_page(target,payload)
                    except Exception as exc:optional_failure("cinematic.catalogue_page_warm",exc)
            finally:self._cin_page_warm_running=False
        try:getattr(self,"_cin_page_executor",_CIN_PAGE_EXECUTOR).submit(worker)
        except Exception as exc:self._cin_page_warm_running=False;optional_failure("cinematic.catalogue_page_warm_submit",exc)

    def _apply_prepared_page(self,target,payload,restore_index=None):
        pending=int(getattr(self,"_cin_page_nav_target",0) or 0)
        # Key-repeat may keep moving inside the ONE pending page while provider
        # I/O is in flight.  Apply the newest pending row, never the stale row
        # captured when load_page() first started.
        if pending and int(target or 0)==pending:
            restore_index=max(0,int(getattr(self,"_cin_page_nav_row",0) or 0))
        if pending>0 and int(target or 0)!=pending:
            # A newer key burst already chose another destination. Preserve this
            # completed catalogue payload in RAM, but never flash it on screen.
            try:self._cache_prepared_page(int(target),payload)
            except Exception as exc:optional_failure("cinematic.stale_page_cache",exc)
            return False
        ok=PremiumGridBase._apply_prepared_page(self,target,payload,restore_index)
        if ok:
            if int(getattr(self,"_cin_page_nav_target",0) or 0)==int(target or 0):
                self._cin_page_nav_target=0;self._cin_page_nav_row=0
            try:self._cin_prefetch_catalogue_pages()
            except Exception as exc:optional_failure("cinematic.catalogue_page_warm_start",exc)
        return ok

    def _cin_virtual_page(self):
        target=int(getattr(self,"_cin_page_nav_target",0) or 0)
        return target if target>0 else max(1,int(self.page or 1))

    def _cin_schedule_page_target(self,target,row,delay_ms=110):
        """Queue one virtual page destination without disturbing last-good visuals.

        The painted page remains authoritative until the final catalogue payload is
        ready. Every key press updates the virtual destination; one short one-shot
        timer collapses provider work, not key events.
        """
        last=self._cin_nav_total_pages()
        target=max(1,min(int(last),int(target or 1)))
        self._cin_page_nav_target=target
        self._cin_page_nav_row=max(0,int(row or 0))
        self._cin_page_nav_generation=int(getattr(self,"_cin_page_nav_generation",0) or 0)+1
        try:
            self._cin_page_nav_timer.stop();self._cin_page_nav_timer.start(max(1,int(delay_ms)),True)
        except Exception:
            self._cin_commit_page_nav()

    def _cin_queue_page_delta(self,delta,row,wrap=False,delay_ms=110):
        if not self.grid_items:return
        last=self._cin_nav_total_pages();base=self._cin_virtual_page();step=int(delta or 0)
        target=base+step
        if wrap:
            if target<1:target=last
            elif target>last:target=1
        else:
            target=max(1,min(last,target))
        if target==base:
            # Preserve the legacy edge grammar when there is nowhere to page.
            if not int(getattr(self,"_cin_page_nav_target",0) or 0):
                if step<0:self.index=0;self._update_selection()
                elif step>0:self.index=max(0,len(self.grid_items)-1);self._update_selection()
            return
        self._cin_schedule_page_target(target,row,delay_ms)

    def _cin_commit_page_nav(self):
        target=int(getattr(self,"_cin_page_nav_target",0) or 0)
        if target<=0:return
        row=max(0,int(getattr(self,"_cin_page_nav_row",0) or 0))
        if target==int(self.page or 1):
            self._cin_page_nav_target=0;self._cin_page_nav_row=0
            return
        # load_page already generation-guards/cancels older provider reads. Keep
        # our virtual target alive until _apply_prepared_page confirms that this
        # exact destination actually painted.
        self.load_page(target,row)
        # A fully painted previous Cinematic page is a valid hold surface. Page I/O
        # is background work and must not replace it with a six-second Loading text.
        try:
            if getattr(self,"grid_items",None):self["status"].setText("")
        except Exception:pass

    def _cin_wrap_up(self):
        # Continuous item navigation crosses one page boundary at a time, but
        # the whole current folder is circular: global item #1 UP -> final item.
        if not self.grid_items:return
        if int(getattr(self,"_cin_page_nav_target",0) or 0):
            self._cin_page_nav_row=max(0,int(getattr(self,"_cin_page_nav_row",0) or 0)-1)
            return
        total=max(0,int(self._active_total() or 0))
        page_size=max(1,int(self.page_size or 1))
        current_global=(max(1,int(self.page or 1))-1)*page_size+max(0,int(self.index or 0))
        if total>0 and current_global<=0:
            last_page=max(1,(total+page_size-1)//page_size)
            last_row=max(0,(total-1)%page_size)
            self._cin_schedule_page_target(last_page,last_row,1)
            return
        if self.index>0:
            self.index-=1;self._update_selection();return
        self._cin_queue_page_delta(-1,page_size-1,wrap=True,delay_ms=1)

    def _cin_wrap_down(self):
        # Same one-page-at-a-time law in the opposite direction.  At the actual
        # final folder item, DOWN wraps straight to global item #1.
        if not self.grid_items:return
        if int(getattr(self,"_cin_page_nav_target",0) or 0):
            self._cin_page_nav_row=min(max(0,int(self.page_size or 1)-1),int(getattr(self,"_cin_page_nav_row",0) or 0)+1)
            return
        total=max(0,int(self._active_total() or 0))
        page_size=max(1,int(self.page_size or 1))
        current_global=(max(1,int(self.page or 1))-1)*page_size+max(0,int(self.index or 0))
        if total>0 and current_global>=total-1:
            self._cin_schedule_page_target(1,0,1)
            return
        if self.index<len(self.grid_items)-1:
            self.index+=1;self._update_selection();return
        self._cin_queue_page_delta(1,0,wrap=True,delay_ms=1)

    def _cin_page_left(self):
        # LEFT/RIGHT are explicit page keys. Keep the current row and collapse a
        # rapid key burst to its final page target.
        if not self.grid_items:return
        self._cin_queue_page_delta(-1,max(0,int(self.index or 0)),wrap=False)

    def _cin_page_right(self):
        if not self.grid_items:return
        self._cin_queue_page_delta(1,max(0,int(self.index or 0)),wrap=False)

    @staticmethod
    def _cin_final_chrome_ready(chrome):
        """True only for one complete Details-family adaptive snapshot.

        Cinematic never swaps a partial title palette.  The previous good adaptive
        remains visible until every surface needed by the right-side information
        cluster is physically present on HDD.
        """
        if not isinstance(chrome,dict):return False
        required=("panel_detail","overview_detail","cast_detail","quality","year","runtime","country","genre_detail")
        for key in required:
            path=str(chrome.get(key) or "")
            try:
                if not path or not os.path.isfile(path) or os.path.getsize(path)<=128:return False
            except Exception:return False
        return True

    def _cin_load_final_adaptive(self,item,row):
        try:
            bundle=_load_visual_bundle(self.profile,self.media_type,item,row) or {}
            chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
            return dict(chrome) if self._cin_final_chrome_ready(chrome) else {}
        except Exception as exc:
            optional_failure("cinematic.final_adaptive_load",exc);return {}

    def _cin_ensure_final_adaptive(self,item,row,cancel_event=None,build=False):
        """Load or build the ONE persistent title adaptive bundle.

        This is deliberately independent from the shared full-HD backdrop cache.
        A title visited normally is persisted exactly like a BLUE Cache Artwork
        title; BLUE simply pre-builds the same files for the whole folder.
        """
        ready=self._cin_load_final_adaptive(item,row)
        if ready or not build:return ready
        if cancel_event is not None and cancel_event.is_set():return {}
        row=dict(row or {})
        poster=str(row.get("poster_local") or "")
        if not (poster and os.path.isfile(poster)):return {}
        try:
            key=canonical_dynamic_details_key(poster) or ("r170_final_%s"%hashlib.sha1(poster.encode("utf-8","ignore")).hexdigest()[:20])
            chrome=_build_dynamic_details_chrome(poster,key) or {}
            if cancel_event is not None and cancel_event.is_set():return {}
            if not self._cin_final_chrome_ready(chrome):return {}
            if not _save_visual_bundle(self.profile,self.media_type,item,{"poster":poster,"chrome":chrome},snapshot=row):return {}
            return dict(chrome)
        except Exception as exc:
            optional_failure("cinematic.final_adaptive_build",exc);return {}

    def _cin_fixed_master_chrome(self):
        """Exact bundled fixed-green Cinematic first-paint chrome.

        The files are already shipped at the final widget geometries, so this
        path performs no image generation and never depends on the selected title.
        It is the cold-start baseline only; once a complete title adaptive is
        ready, hold-last-good logic owns all subsequent navigation handoffs.
        """
        specs={
            "panel_detail":(1410,360,"cin_panel_detail"),
            "overview_detail":(1318,170,"cin_overview_detail"),
            "cast_detail":(1318,50,"cin_cast_detail"),
            "quality":(150,50,"cin_quality"),
            "year":(130,50,"cin_year"),
            "runtime":(150,50,"cin_runtime"),
            "country":(160,50,"cin_country"),
            "genre_detail":(680,50,"cin_genre_detail"),
        }
        out={}
        try:
            for key,(w,h,tag) in specs.items():
                path=str(fixed_exact_surface(w,h,False,tag) or "")
                if not (path and os.path.isfile(path) and os.path.getsize(path)>128):
                    return {}
                out[key]=path
        except Exception as exc:
            optional_failure("cinematic.fixed_green_master",exc);return {}
        return out

    def _cin_ensure_description_shell(self, force_rebind=False):
        """Hold last-good adaptive overview; R81 shell is cold-start fallback only."""
        try:
            held=str((getattr(self,"_cin_widget_paths",{}) or {}).get("overview_bg") or "")
            if held and os.path.isfile(held):
                if force_rebind:
                    try:
                        inst=self["overview_bg"].instance
                        if inst is not None:inst.setPixmapFromFile(held)
                    except Exception as exc:optional_failure("cinematic.description_rebind",exc)
                try:self["overview_bg"].show()
                except Exception:pass
                try:self["description"].show()
                except Exception:pass
                return True
            path=str((getattr(self,"_cin_last_chrome",{}) or {}).get("overview_detail") or "")
            if not (path and os.path.isfile(path)):
                master=self._cin_fixed_master_chrome() or {}
                path=str(master.get("overview_detail") or "")
            ok=self._set_native_pixmap("overview_bg",path) if path and os.path.isfile(path) else False
            try:self["description"].show()
            except Exception:pass
            return bool(ok)
        except Exception as exc:
            optional_failure("cinematic.description_shell",exc);return False

    def _cin_layout_ready(self):
        try:self["cin_backdrop_stage"].hide()
        except Exception:pass
        try:self["title_logo"].hide()
        except Exception:pass
        try:
            self._set_native_pixmap("artwork_status_bg","us66_neutral_pill_genre.png")
            self["artwork_status_bg"].hide()
        except Exception:pass
        # R214 Stage 1: delete the old neutral first-paint path from Cinematic.
        # The exact bundled fixed-green material is the one immutable cold shell,
        # matching Home/utility chrome.  It stays resident until a COMPLETE title
        # adaptive replaces it; navigation never re-applies a fallback in between.
        _master=self._cin_fixed_master_chrome()
        if _master:
            self._cin_fixed_master_cache=dict(_master)
            self._cin_display_adaptive_owner_sig=""
            self._apply_native_detail_chrome(_master)
        else:
            # Package-corruption safety only. Normal shipped builds always have
            # the complete fixed master, so this bridge is not part of first paint.
            self._apply_native_detail_chrome({})
        # Cinematic paints selection entirely inside the Settings/Episodes row
        # renderer. Disable Enigma2's native listbox selection layer, otherwise
        # some OpenBH skins inject the red rectangular slab seen on receiver.
        try:
            if self["cin_list"].instance is not None:self["cin_list"].instance.setSelectionEnable(0)
        except Exception as exc:optional_failure("cinematic.native_selection",exc)
        # Beta75: drain completed HDD visual jobs quickly enough that a ready
        # backdrop does not sit in the queue for half a second after navigation.
        # Re-run the exact Details season/runtime fit only after Enigma has
        # instantiated the label and its real 102px width is available.
        self._cin_fit_runtime_like_details()
        try:self._cin_set_poll_interval(120 if self._cin_poll_has_pending_work() else 0,force=True)
        except Exception as exc:optional_failure("cinematic.poll_start",exc)

    def _set_native_pixmap(self,widget,path):
        try:
            if path and not os.path.isabs(str(path)):path=asset(path)
            path=str(path or "")
            if path and os.path.isfile(path) and self[widget].instance is not None:
                if self._cin_widget_paths.get(widget)==path:
                    self[widget].show();return True
                self[widget].instance.setPixmapFromFile(path);self._cin_widget_paths[widget]=path;self[widget].show();return True
            self._cin_widget_paths.pop(widget,None);self[widget].hide()
        except Exception as exc:optional_failure("cinematic.native_pixmap",exc)
        return False

    def _cin_buffered_handoff(self,base_name,stage_name,path,expected_sig=""):
        """Commit one decoder-ready backdrop without ever blanking last-good.

        Stage 5 keeps the proven single native full-screen surface used by
        Cinematic/BG1/BG2, but removes the destructive ``setPixmap(None)`` that
        ran *before* the replacement bind.  If a decoder/driver rejects the new
        JPEG, the already-visible backdrop therefore remains on screen.

        ``expected_sig`` is the selected catalogue identity at schedule time.
        Async/cache completions are allowed to paint only while that identity is
        still current; a stale title can never replace the user's current image.
        """
        try:
            path=str(path or "");expected_sig=str(expected_sig or "")
            if expected_sig and expected_sig!=self._fast_bd_current_sig():return False
            if not path or not os.path.isfile(path):return False
            target=self[base_name]
            if target.instance is None:return False
            if self._cin_pixmap_paths.get(base_name)==path:
                if expected_sig and expected_sig!=self._fast_bd_current_sig():return False
                target.show();return True
            # Never clear the committed surface first. setPixmapFromFile replaces
            # it only when Enigma accepts the new decoder-ready file, preserving
            # last-good on missing/corrupt/stale candidates.
            target.instance.setPixmapFromFile(path)
            if expected_sig and expected_sig!=self._fast_bd_current_sig():return False
            self._cin_pixmap_paths[base_name]=path
            self._cin_active_pixmap[base_name]=base_name
            target.show()
            try:
                stage=self[stage_name]
                stage.hide()
                if stage.instance is not None:stage.instance.setPixmap(None)
                self._cin_pixmap_paths.pop(stage_name,None)
            except Exception:pass
            return True
        except Exception as exc:optional_failure("cinematic.single_backdrop_handoff",exc);return False

    def _cin_reassert_info_hud(self):
        """Rebind small Cinematic chrome after a heavy backdrop surface swap.

        Some receiver drivers invalidate adjacent native pixmaps when the 1080p
        backdrop double-buffer flips.  Our logical path cache survives, so a
        normal show() is insufficient.  Rebind the already-selected local chrome
        files without any network, TMDB or adaptive rebuild work.
        """
        names=("panel_bg","overview_bg","cast_card_bg","quality_pill_bg",
               "year_pill_bg","runtime_pill_bg","country_pill_bg","genre_pill_bg")
        for name in names:
            try:
                path=str((getattr(self,"_cin_widget_paths",{}) or {}).get(name) or "")
                if not (path and os.path.isfile(path)):
                    continue
                inst=self[name].instance
                if inst is None:
                    continue
                inst.setPixmapFromFile(path)
                self[name].show()
            except Exception as exc:
                optional_failure("cinematic.reassert_hud.%s"%name,exc)
        try:self._cin_ensure_description_shell(force_rebind=True)
        except Exception as exc:optional_failure("cinematic.reassert_description",exc)

    def _apply_native_detail_chrome(self,chrome):
        """Fixed-green cold shell + atomic hold-last-good title adaptive.

        A visible chrome set is never cleared, hidden, or partially replaced on
        navigation.  Cold open starts from the complete fixed-green set.  After
        that, only another complete eight-surface title adaptive may replace the
        currently visible set.
        """
        incoming=chrome if isinstance(chrome,dict) else {}
        required=("panel_detail","overview_detail","cast_detail","quality","year","runtime","country","genre_detail")
        incoming_complete=all(str(incoming.get(k) or "") and os.path.isfile(str(incoming.get(k) or "")) for k in required)

        previous=dict(getattr(self,"_cin_last_chrome",{}) or {})
        previous_complete=all(str(previous.get(k) or "") and os.path.isfile(str(previous.get(k) or "")) for k in required)

        if incoming_complete:
            active={k:str(incoming.get(k) or "") for k in required}
            self._cin_last_chrome=dict(active)
        elif previous_complete:
            active=previous
        else:
            # Only possible before first layout paint or with a damaged package.
            # Prefer the fixed master as a complete set instead of mixing/hiding.
            master=self._cin_fixed_master_chrome() or {}
            if all(str(master.get(k) or "") and os.path.isfile(str(master.get(k) or "")) for k in required):
                active=master;self._cin_last_chrome=dict(master)
            else:
                active=previous

        mapping={
            "panel_bg":"panel_detail",
            "overview_bg":"overview_detail",
            "cast_card_bg":"cast_detail",
            "quality_pill_bg":"quality",
            "year_pill_bg":"year",
            "runtime_pill_bg":"runtime",
            "country_pill_bg":"country",
            "genre_pill_bg":"genre_detail",
        }
        for widget,key in mapping.items():
            path=str(active.get(key) or "")
            if path and os.path.isfile(path):
                if key=="overview_detail":
                    path=self._cin_sharp_details_overview(path) or path
                self._set_native_pixmap(widget,path)
                continue
            # Never blank a resident widget.  A missing replacement is simply
            # ignored until the complete next chrome set exists.
            held=str((getattr(self,"_cin_widget_paths",{}) or {}).get(widget) or "")
            if held and os.path.isfile(held):
                try:self[widget].show()
                except Exception:pass


    @staticmethod
    def _cin_adaptive_rows_ready(rows):
        rows=rows if isinstance(rows,dict) else {}
        normal=str(rows.get("normal") or rows.get("row_normal") or "")
        selected=str(rows.get("selected") or rows.get("row_selected") or "")
        return bool(normal and selected and os.path.isfile(normal) and os.path.isfile(selected))

    @staticmethod
    def _cin_adaptive_chrome_ready(chrome):
        chrome=chrome if isinstance(chrome,dict) else {}
        required=("panel_detail","overview_detail","cast_detail","quality","year","runtime","country","genre_detail")
        return all(str(chrome.get(key) or "") and os.path.isfile(str(chrome.get(key) or "")) for key in required)

    def _cin_refresh_rail_material_only(self,allow_progress_build=True):
        """Rebind visible Cinematic rows without rebuilding/indexing the page.

        The old path called ``_render_grid`` after every adaptive colour change.
        That rescanned page packages/HDD metadata and then recursively ran a full
        selection update on the GUI thread.  Per-entry invalidation is enough:
        names, progress and selection state are already resident in RAM.
        """
        try:
            limit=min(int(self.page_size or 0),len(self.grid_items or []))
            for pos in range(limit):
                row=(getattr(self,"_cin_page_records",{}) or {}).get(pos,{}) or {}
                self["cin_list"].update_icon_row(pos,self._cin_list_row(pos,selected=(pos==self.index),record=row,allow_progress_build=allow_progress_build))
            if limit:
                self["cin_list"].moveToIndex(max(0,min(int(self.index),limit-1)))
                self._cin_last_list_index=int(self.index)
            return True
        except Exception as exc:
            optional_failure("cinematic.atomic_rows_refresh",exc);return False

    def _cin_commit_adaptive_presentation(self,item,row,rows,chrome,accent=""):
        """Commit rail material + Details chrome as one selected-title state.

        A title never paints only the overview/description chrome and later the
        left rail (or vice versa).  Until BOTH complete surface sets exist, the
        previously committed adaptive state stays visible.  The commit itself is
        GUI-only and performs no page scan, metadata lookup, image analysis or
        network work.
        """
        if self._screen_closed or not self.grid_items:return False
        if not self._cin_adaptive_rows_ready(rows) or not self._cin_adaptive_chrome_ready(chrome):return False
        try:
            current=self.grid_items[self.index]
            if self._page_visual_key(current)!=self._page_visual_key(item):return False
        except Exception:return False
        normal=str(rows.get("normal") or rows.get("row_normal") or "")
        selected=str(rows.get("selected") or rows.get("row_selected") or "")
        key=self._record_key(row,item)
        try:
            changed=bool(normal!=self._settings_row_asset or selected!=self._settings_row_selected_asset)
            self._settings_row_asset=normal;self._settings_row_selected_asset=selected
            if re.match(r"^#[0-9a-fA-F]{6}$",str(accent or "")):
                # Keep the laser colour authority, but do not force a full page
                # rebuild.  Existing laser pixmaps stay resident until a row is
                # naturally invalidated; adaptive row surfaces still swap now.
                self._cin_progress_accent=str(accent).lower()
            if changed:self._cin_refresh_rail_material_only()
            self._apply_native_detail_chrome(chrome)
            self._cin_committed_adaptive_key=str(key or "")
            try:self._cin_display_adaptive_owner_sig=str(self._page_visual_key(item) or "")
            except Exception:self._cin_display_adaptive_owner_sig=""
            return True
        except Exception as exc:
            optional_failure("cinematic.atomic_adaptive_commit",exc);return False


    def _cin_apply_nav_adaptive_baseline(self):
        """R225: navigation never inserts the fixed-green master between titles.

        Kept as a compatibility callback because existing Enigma2 timer wiring may
        still reference it during screen teardown/resume.  The cold-start fixed
        master is applied by _cin_layout_ready(); normal title-to-title navigation
        is strict hold-last-good until the next complete adaptive commits.
        """
        return False


    def _grid_shown_resume(self):
        self._cin_hidden=False
        self._cin_cancel_background()
        # Child screens stop Cinematic's private heartbeat completely.  Resume
        # only when this screen is visible again, using the adaptive cadence.
        try:self._cin_set_poll_interval(120 if self._cin_poll_has_pending_work() else 0,force=True)
        except Exception:pass
        if getattr(self,"_screen_closed",False) or getattr(self,"_grid_closed",False):return
        self._grid_images_suspended=False
        # Restore the exact local visual first so BACK never waits on network or
        # artwork hydration. Selection repaint can then refresh text/chrome only.
        try:self._cin_restore_surfaces_after_child()
        except Exception as exc:optional_failure("cinematic.resume_surface_restore",exc)
        if self.grid_items:
            try:self._update_selection();self._apply_debounced_grid_focus()
            except Exception as exc:optional_failure("cinematic.resume",exc)

    def _cin_hidden_start(self):
        """Child screens are a quiet period, not an automatic cache worker.

        Older builds started hidden folder hydration here while Details was
        allocating its own surfaces. That competed for CPU/HDD and contributed to
        gAccel pressure. Explicit BLUE Cache Artwork remains the only folder-wide
        artwork job.
        """
        if self._screen_closed:return
        self._cin_hidden=True
        try:self._cin_poll.stop()
        except Exception:pass
        try:self._cin_fast_info_timer.stop()
        except Exception:pass
        self._cin_cancel_fast_info();self._cin_cancel_focus(enqueue=False);self._cin_cancel_background()
        try:
            if self._cin_adaptive_cancel is not None:self._cin_adaptive_cancel.set()
        except Exception:pass
        try:
            if self._cin_visual_future is not None and not self._cin_visual_future.done():self._cin_visual_future.cancel()
        except Exception:pass
        self._cin_visual_future=None
        try:
            if self._cin_logo_cancel is not None:self._cin_logo_cancel.set()
        except Exception:pass
        # Hidden child screens cancel the in-flight logo but keep the dedicated
        # executor alive; BACK must be able to resolve the same title again.
        self._cin_logo_cancel=None;self._cin_logo_active_sig=""

    def _cin_cancel_background(self):
        ev=getattr(self,"_cin_background_cancel",None)
        if ev is not None:
            try:ev.set()
            except Exception:pass
        future=getattr(self,"_cin_background_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        self._cin_background_cancel=None
        self._cin_background_future=None

    def _cin_cancel_fast_info(self):
        ev=getattr(self,"_cin_fast_info_cancel",None)
        if ev is not None:
            try:ev.set()
            except Exception:pass
        future=getattr(self,"_cin_fast_info_future",None)
        if future is not None:
            try:
                if not future.done():future.cancel()
            except Exception:pass
        self._cin_fast_info_cancel=None;self._cin_fast_info_future=None;self._cin_fast_info_sig=""

    def _cin_arm_fast_info(self):
        """Selected-title metadata lane, deliberately separate from artwork focus."""
        if self._screen_closed or getattr(self,"_cin_cache_exclusive",False) or not self.grid_items:return
        self._cin_cancel_fast_info()
        try:self._cin_fast_info_timer.stop();self._cin_fast_info_timer.start(160,True)
        except Exception:self._cin_apply_fast_info()

    def _cin_apply_fast_info(self):
        if self._screen_closed or getattr(self,"_cin_cache_exclusive",False) or not self.grid_items:return
        item=self.grid_items[self.index]
        row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index),{}) or self._record(item) or {}
        # Cached Details data paints immediately. Missing fields keep the previous
        # title's text until the new authoritative snapshot arrives.
        try:self._cin_apply_record(item,row,allow_build=False,hold_missing=True)
        except TypeError:self._cin_apply_record(item,row,allow_build=False)
        except Exception as exc:optional_failure("cinematic.fast_info_paint",exc)
        try:
            from .details_authority import metadata_language_ready
            if metadata_language_ready(row,(self._grid_settings or {})):return
        except Exception:
            if row.get("_details_authority_ready"):return
        # Do not require a pre-existing TMDb id here. The metadata-only Details
        # Authority lane can resolve the selected catalogue identity itself, so
        # titles such as Grand Hotel populate outside Details without an enter/exit.
        sig=self._page_visual_key(item)
        if not sig:return
        ev=threading.Event();self._cin_fast_info_cancel=ev;self._cin_fast_info_sig=sig
        item_copy=dict(item);row_copy=dict(row);profile=self.profile;media_type=self.media_type;settings=dict(self._grid_settings or {});provider_client=self.client
        def worker():
            try:
                from .details_authority import resolve_metadata_fast
                resolved=resolve_metadata_fast(profile,media_type,item_copy,current=row_copy,cancel_event=ev,settings=settings,provider_client=provider_client) or row_copy
                if not ev.is_set():self._cin_jobs.put({"kind":"fast_info","sig":sig,"row":resolved})
            except Exception as exc:optional_failure("cinematic.fast_info_worker",exc)
        try:
            self._cin_fast_info_future=getattr(self,"_cin_fast_meta_executor",_FAST_BACKDROP_META_EXECUTOR).submit(worker,_task_key="cin-info:%x"%id(self),_replace_task_key=True)
            self._cin_kick_poll()
        except Exception as exc:
            self._cin_fast_info_cancel=None;self._cin_fast_info_future=None;self._cin_fast_info_sig=""
            optional_failure("cinematic.fast_info_submit",exc)

    def _cin_cancel_focus(self,enqueue=True):
        ev=getattr(self,"_cin_focus_cancel",None)
        if ev is not None:
            try:ev.set()
            except Exception:pass
        future=getattr(self,"_cin_focus_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        if enqueue and isinstance(getattr(self,"_cin_last_focus_item",None),dict):
            self._cin_enqueue_missed(self._cin_last_focus_item)
        self._cin_focus_cancel=None;self._cin_focus_future=None;self._cin_focus_sig=""

    def _cin_stop(self):
        try:self._cin_page_nav_timer.stop()
        except Exception:pass
        self._cin_page_nav_target=0;self._cin_page_nav_row=0
        try:self._fast_bd_cancel(clear_file=False)
        except Exception:pass
        try:self._fast_bd_fast_cleanup()
        except Exception:pass
        self._fast_bd_meta_pending.clear()
        try:self._cin_poll.stop()
        except Exception:pass
        try:self._cin_nav_backdrop_timer.stop()
        except Exception:pass
        try:self._cin_nav_adaptive_timer.stop()
        except Exception:pass
        try:self._cin_fast_info_timer.stop()
        except Exception:pass
        self._cin_cancel_fast_info();self._cin_cancel_focus(enqueue=False);self._cin_cancel_background()
        try:
            if self._cin_logo_cancel is not None:self._cin_logo_cancel.set()
        except Exception:pass
        try:
            ex=getattr(self,"_cin_logo_executor",None)
            if ex is not None:ex.shutdown(wait=False,cancel_futures=True)
        except Exception:pass
        for name in ("cin_backdrop","cin_backdrop_stage","title_logo"):
            try:
                self[name].hide()
                if self[name].instance is not None:self[name].instance.setPixmap(None)
            except Exception:pass
        self._cin_pixmap_paths.clear();self._cin_widget_paths.clear()
        self._cin_pending.clear();self._cin_missed.clear();self._cin_missed_keys.clear();self._cin_logo_pending.clear();self._cin_logo_path=""

    def _prefetch_visible_page_details(self, priority_index=None, max_items=None):
        # Hybrid Cinematic owns hydration itself. PremiumGridBase page warming is
        # deliberately disabled here so no hidden gridposter_1x1 or neighbour
        # TMDB jobs run while the user is navigating.
        return

    def _cin_safe_key(self,key):
        value=str(key or "").strip()
        if not value:return ""
        value=re.sub(r"[^A-Za-z0-9._-]+","_",value)[:160]
        return value or hashlib.sha1(str(key).encode("utf-8","ignore")).hexdigest()[:24]

    def _cin_package_root(self,key):
        # One fixed generated directory. Key belongs in filenames, never folders.
        return GENERATED if self._cin_safe_key(key) else ""

    def _cin_package_path(self,key):
        safe=self._cin_safe_key(key)
        return os.path.join(GENERATED,"cinematic_%s_package.json"%safe) if safe else ""

    def _cin_legacy_roots(self,key):
        safe=str(key or "")
        if not safe:return []
        old_derived=os.path.join(ROOT,"derived")
        old_library=os.path.join(ROOT,"library")
        roots=[
            os.path.join(old_derived,"cinematic_hybrid",safe),
            os.path.join(old_derived,"cinematic_offline",safe),
            os.path.join(old_derived,"cinematic",safe),
        ]
        # Test74-76 may have written package folders under library/movie-id/cinematic.
        if safe.startswith(("movie-","tv-")):
            roots.append(os.path.join(old_library,safe,"cinematic"))
        else:
            roots.append(os.path.join(old_library,"local-"+safe,"cinematic"))
        return roots

    def _cin_store_asset(self,source,root,role,key=""):
        source=str(source or "")
        if not source or not os.path.isfile(source):return ""
        safe=self._cin_safe_key(key) or hashlib.sha1(source.encode("utf-8","ignore")).hexdigest()[:18]
        try:
            # Builders already write into the one fixed GENERATED folder. Keep
            # that exact file instead of duplicating it under another name.
            if os.path.commonpath((os.path.abspath(source),os.path.abspath(GENERATED)))==os.path.abspath(GENERATED):
                return source
        except Exception:pass
        ext=os.path.splitext(source)[1].lower() or ".png"
        if ext not in (".png",".jpg",".jpeg",".webp"):ext=".png"
        target=os.path.join(GENERATED,"cinematic_%s_%s%s"%(safe,role,ext))
        try:
            if not ensure_persistent_dirs(GENERATED):return ""
            if os.path.isfile(target):
                try:
                    if os.path.getsize(target)==os.path.getsize(source) and os.path.getsize(target)>0:return target
                except Exception:pass
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            try:
                if os.path.exists(temp):os.unlink(temp)
                os.link(source,temp)
            except Exception:
                shutil.copy2(source,temp)
            os.replace(temp,target)
            return target if os.path.isfile(target) and os.path.getsize(target)>0 else ""
        except Exception as exc:
            optional_failure("cinematic.flat_store_asset",exc);return ""

    def _cin_nav_alias_path(self,item):
        try:
            visual=self._page_visual_key(item);safe=self._cin_safe_key(visual)
            return os.path.join(GENERATED,"cinematic_nav_%s.json"%safe) if safe else ""
        except Exception:return ""

    def _cin_publish_nav_alias(self,item,row,package):
        """Persist a tiny provider-key -> final prepared backdrop pointer."""
        if not isinstance(package,dict) or not package:return False
        try:
            backdrop=str(package.get("backdrop_fast") or package.get("backdrop") or "")
            if not backdrop or not os.path.isfile(backdrop):return False
            visual=self._page_visual_key(item);path=self._cin_nav_alias_path(item)
            if not visual or not path:return False
            payload={"schema":1,"visual_key":visual,"package_key":str(package.get("key") or self._record_key(row if isinstance(row,dict) else {},item) or ""),"backdrop":backdrop}
            tmp=path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            with open(tmp,"w",encoding="utf-8") as h:json.dump(payload,h,separators=(",",":"),ensure_ascii=False)
            os.replace(tmp,path);self._cin_nav_alias_ram[visual]=dict(payload);return True
        except Exception as exc:
            optional_failure("cinematic.nav_alias_write",exc)
            try:
                if 'tmp' in locals() and os.path.exists(tmp):os.unlink(tmp)
            except Exception:pass
            return False

    def _cin_load_nav_alias(self,item):
        """RAM/tiny-JSON only. No metadata, adaptive, Pillow or network work."""
        try:
            visual=self._page_visual_key(item)
            if not visual:return {}
            cached=(getattr(self,"_cin_nav_alias_ram",{}) or {}).get(visual) or {}
            bd=str(cached.get("backdrop") or "") if isinstance(cached,dict) else ""
            if bd and os.path.isfile(bd):return cached
            path=self._cin_nav_alias_path(item)
            if not path or not os.path.isfile(path):return {}
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            if not isinstance(data,dict) or int(data.get("schema") or 0)!=1:return {}
            bd=str(data.get("backdrop") or "")
            if not bd or not os.path.isfile(bd):return {}
            self._cin_nav_alias_ram[visual]=data;return data
        except Exception:return {}

    def _cin_normalize_legacy_package(self,data):
        if not isinstance(data,dict):return {}
        rows=data.get("settings_rows") if isinstance(data.get("settings_rows"),dict) else {}
        chrome=data.get("chrome") if isinstance(data.get("chrome"),dict) else {}
        out=dict(data)
        if not out.get("row_normal"):out["row_normal"]=rows.get("normal")
        if not out.get("row_selected"):out["row_selected"]=rows.get("selected")
        for name in ("panel","overview","quality","year","runtime","country","genre"):
            if not out.get(name):out[name]=chrome.get(name)
        return out

    def _cin_adopt_legacy_package(self,item,row):
        """Adopt only old catalogue row material into the current package.

        R149 deliberately refuses legacy backdrop/chrome surfaces here. Native
        Details is the sole information-chrome authority and the shared prepared
        Cinematic JPEG is the sole full-screen backdrop authority. Keeping only
        row assets preserves cheap cache reuse without reviving old visual paths.
        """
        key=self._record_key(row,item);root=self._cin_package_root(key)
        if not key or not root:return {}
        for legacy_root in self._cin_legacy_roots(key):
            for path in (os.path.join(legacy_root,"package.json"),os.path.join(legacy_root,"cinematic_package.json")):
                if not os.path.isfile(path):continue
                try:
                    with open(path,"r",encoding="utf-8") as h:data=self._cin_normalize_legacy_package(json.load(h))
                except Exception:
                    continue
                normal=str(data.get("row_normal") or "");selected=str(data.get("row_selected") or "")
                if not (normal and selected and os.path.isfile(normal) and os.path.isfile(selected)):continue
                adopted_normal=self._cin_store_asset(normal,root,"row_normal",key)
                adopted_selected=self._cin_store_asset(selected,root,"row_selected",key)
                if not (adopted_normal and adopted_selected):continue
                payload={
                    "schema":self._cin_package_schema,"complete":True,"key":key,
                    "signature":self._cin_package_signature(item,row),
                    "accent":str(data.get("accent") or row.get("adaptive_accent") or row.get("adaptive_primary") or "#c99141"),
                    "created_at":int(data.get("created_at") or data.get("completed_at") or time.time()),
                    "row_normal":adopted_normal,"row_selected":adopted_selected,
                    "backdrop_source_sig":self._cin_file_sig(row.get("backdrop_local")),
                    "clean_backdrop_selector_version":int(row.get("clean_backdrop_selector_version") or 0),
                }
                if self._cin_write_package(self._cin_package_path(key),payload):return payload
        return {}

    def _cin_file_sig(self,path):
        try:
            if path and os.path.isfile(str(path)):
                st=os.stat(str(path));return "%s:%s:%s"%(str(path),int(st.st_mtime),int(st.st_size))
        except Exception:pass
        return ""

    def _cin_package_signature(self,item,row):
        row=row if isinstance(row,dict) else {}
        raw="|".join((str(self._cin_package_schema),self._record_key(row,item),self._cin_file_sig(row.get("backdrop_local")),str(row.get("adaptive_fingerprint") or "")))
        return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()

    def _cin_ensure_fast_backdrop(self,item,row,package):
        """Finalize Cinematic to one decoder-friendly JPEG presentation surface.

        The full-HD composited PNG is a build intermediate only.  Once the FAST
        JPEG has been created and validated, delete only that generated PNG and
        keep the canonical source backdrop untouched.  This preserves first-paint
        speed without retaining a second multi-megabyte backdrop per title.
        """
        if not isinstance(package,dict) or not package:return ""
        key=self._record_key(row if isinstance(row,dict) else {},item)
        safe=self._cin_safe_key(key)
        if not safe:return ""
        target=os.path.join(GENERATED,"cinematic_%s_backdrop_fast.jpg"%safe)

        def drop_build_intermediate(final_path):
            source=str(package.get("backdrop") or "")
            if not source or source==final_path:return
            try:
                generated=os.path.abspath(GENERATED)
                source_abs=os.path.abspath(source)
                name=os.path.basename(source_abs)
                expected="cinematic_%s_backdrop_"%safe
                # Surgical guard: never delete canonical /backdrops artwork or
                # any unrelated presentation asset.
                if os.path.commonpath((source_abs,generated))!=generated:return
                if not (name.startswith(expected) and name.lower().endswith(".png")):return
                if os.path.isfile(source_abs):os.unlink(source_abs)
                if not os.path.exists(source_abs):package["backdrop"]=""
            except Exception as exc:
                optional_failure("cinematic.fast_backdrop_cleanup",exc)

        fast=str(package.get("backdrop_fast") or "")
        if fast and os.path.isfile(fast) and os.path.getsize(fast)>4096:
            drop_build_intermediate(fast)
            return fast
        source=str(package.get("backdrop") or "")
        if not source or not os.path.isfile(source) or _PILImage is None:return ""
        temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        try:
            if os.path.isfile(target) and os.path.getsize(target)>4096:
                package["backdrop_fast"]=target
                drop_build_intermediate(target)
                return target
            if not ensure_persistent_dirs(os.path.dirname(target)):return ""
            with _PILImage.open(source) as im:
                im=im.convert("RGB")
                # No optimize/progressive pass: cache generation should spend as
                # little CPU as possible.  90 is visually transparent at 1080p
                # for this already-composited presentation surface.
                im.save(temp,"JPEG",quality=90,subsampling=2,optimize=False,progressive=False)
            os.replace(temp,target)
            if not (os.path.isfile(target) and os.path.getsize(target)>4096):return ""
            package["backdrop_fast"]=target
            drop_build_intermediate(target)
            return target
        except Exception as exc:
            optional_failure("cinematic.fast_backdrop_encode",exc)
            try:
                if os.path.exists(temp):os.unlink(temp)
            except Exception:pass
            return ""

    def _cin_load_canonical_package_fast(self,item,row):
        """Read only the canonical package JSON; never migrate, resolve or build.

        This is the navigation-safe index path used while painting a catalogue
        page.  It intentionally performs no legacy adoption and no artwork work.
        """
        row=row if isinstance(row,dict) else {}
        key=self._record_key(row,item);path=self._cin_package_path(key)
        if not path or not os.path.isfile(path):return {}
        try:
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            if not isinstance(data,dict) or int(data.get("schema") or 0)!=self._cin_package_schema or not data.get("complete"):return {}
            if int(row.get("clean_backdrop_selector_version") or 0)>=3:
                current_sig=self._cin_file_sig(row.get("backdrop_local"))
                if current_sig and str(data.get("backdrop_source_sig") or "")!=current_sig:return {}
            # R149 package is row metadata only. ArtworkV2 backdrop_local is
            # the canonical source/master; the shared prepared Cinematic JPEG is
            # the sole full-screen display authority for Cinematic/BG1/BG2.
            return data
        except Exception as exc:
            optional_failure("cinematic.fast_package_index",exc);return {}

    def _cin_instant_cached_backdrop(self,item,row):
        """Bind only the shared prepared presentation for the CURRENT title.

        Missing/new artwork never clears the committed backdrop.  The selection
        signature is checked both before lookup and at commit so a late cache hit
        from a skipped title cannot leak into Cinematic, BG1 or BG2.
        """
        try:
            row=row if isinstance(row,dict) else {}
            expected_sig=self._page_visual_key(item)
            if not expected_sig or expected_sig!=self._fast_bd_current_sig():return False
            path=str((getattr(self,"_cin_page_backdrops",{}) or {}).get(int(self.index)) or "")
            if not (path and os.path.isfile(path)):
                source=str(row.get("backdrop_local") or "")
                key=self._record_key(row,item)
                path=_prepare_shared_cinematic_backdrop(source,key,row,build=False) if source else ""
                if path and os.path.isfile(path) and expected_sig==self._fast_bd_current_sig():
                    self._cin_page_backdrops[int(self.index)]=path
            if expected_sig!=self._fast_bd_current_sig():return False
            if not path or not os.path.isfile(path):return False
            if not self._cin_buffered_handoff("cin_backdrop","cin_backdrop_stage",path,expected_sig=expected_sig):return False
            self._cin_visual_key=self._record_key(row,item)
            self._cin_nav_backdrop_ready_key=self._cin_backdrop_ready_token(item,row,{},path)
            self._cin_shared_backdrop_retry=0
            return True
        except Exception as exc:
            optional_failure("cinematic.shared_cached_backdrop",exc);return False

    def _cin_cached_backdrop_delay_ms(self):
        """R202: cached backdrops settle at 300 ms; cold titles keep 550 ms.

        Cache detection is the same bounded R200 lookup: page-local prepared
        presentation, the direct warm cache, a clean local row backdrop, or the
        persistent ORIGINAL cache.  Nothing is built here.  Arrow navigation
        only chooses the quiet-window; actual decode/paint remains deferred.
        """
        try:
            path=str((getattr(self,"_cin_page_backdrops",{}) or {}).get(int(self.index)) or "")
            if getattr(self,"_fast_bd_enabled",False) and self.grid_items:
                item=self.grid_items[self.index];sig=self._page_visual_key(item)
                entry=(getattr(self,"_fast_bd_fast_cache",{}) or {}).get(sig) or {}
                direct=str(entry.get("backdrop") or "")
                row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index),{}) or {}
                if not (direct and os.path.isfile(direct)):
                    direct=self._fast_bd_clean_local_from_row(row)
                if not (direct and os.path.isfile(direct)):
                    url=self._fast_bd_url_from_row(row)
                    direct=self._fast_bd_cached_original(url) if url else ""
                if direct and os.path.isfile(direct):path=direct
            low=path.lower()
            if path and (low.endswith(".jpg") or low.endswith(".jpeg") or low.endswith(".webp")):
                return 300
        except Exception:
            pass
        return 550

    def _cin_apply_debounced_backdrop(self):
        """R202: one R81-style gate, 300 ms cached / 550 ms uncached.

        Every arrow press restarts the one-shot timer. No backdrop decode or
        network work happens until the final focused title wins its own quiet
        window. Cached titles feel faster; cold titles retain the conservative
        550 ms gate that kept rapid navigation smooth.
        """
        if self._screen_closed or getattr(self,"_cin_hidden",False) or getattr(self,"_cin_cache_exclusive",False):return
        if not self.grid_items:return
        try:
            target=max(0.06,float(self._cin_cached_backdrop_delay_ms())/1000.0)
            quiet=max(0.0,time.monotonic()-float(getattr(self,"_cin_last_nav_at",0.0) or 0.0))
            if quiet < target:
                self._cin_nav_backdrop_timer.stop();self._cin_nav_backdrop_timer.start(max(60,int((target-quiet)*1000)),True)
                return
            item=self.grid_items[self.index]
            row=self._cin_page_records.get(self.index,{}) or {}
            if getattr(self,"_fast_bd_enabled",False):
                # Same R196/R199 quality/clean-image authority, now allowed to
                # start only after R81's navigation quiet gate has actually won.
                self._fast_bd_schedule(item,row)
                return
            self._cin_instant_cached_backdrop(item,row)
        except Exception as exc:optional_failure("cinematic.r200_r81_nav_backdrop",exc)

    def _cin_load_package(self,item,row):
        row=row if isinstance(row,dict) else {}
        key=self._record_key(row,item);path=self._cin_package_path(key)
        def valid(data):
            # Trusted Local Cache Path: a completed canonical package is the
            # authority for presentation. Metadata timestamps/fingerprints may
            # change independently and must never force a cached 5-6GB library
            # back through Pillow/TMDB processing during navigation.
            if not isinstance(data,dict) or int(data.get("schema") or 0)!=self._cin_package_schema or not data.get("complete"):return False
            if int(row.get("clean_backdrop_selector_version") or 0)>=3:
                current_sig=self._cin_file_sig(row.get("backdrop_local"))
                if current_sig and str(data.get("backdrop_source_sig") or "")!=current_sig:return False
            # R149 packages own catalogue row material only. Backdrop display
            # belongs to the shared presentation cache and information chrome
            # belongs to Native Details, so neither can participate in package
            # validity or silently become a competing visual authority.
            essential=("row_normal","row_selected")
            for field in essential:
                fp=str(data.get(field) or "")
                if not fp or not os.path.isfile(fp):return False
            return True
        try:
            if path and os.path.isfile(path):
                with open(path,"r",encoding="utf-8") as h:data=json.load(h)
                if valid(data):return data
            # Canonical miss does NOT mean artwork miss. Try the already-saved
            # pre-V7 package before any resolver/build path is allowed to run.
            adopted=self._cin_adopt_legacy_package(item,row)
            if valid(adopted):return adopted
            return {}
        except Exception as exc:optional_failure("cinematic.canonical_package_load",exc);return {}

    def _cin_write_package(self,path,data):
        temp=""
        try:
            if not ensure_persistent_dirs(os.path.dirname(path)):return False
            temp=path+".tmp.%d"%os.getpid()
            with open(temp,"w",encoding="utf-8") as h:
                json.dump(data,h,ensure_ascii=False,separators=(",",":"))  # derived cinematic cache metadata
            os.replace(temp,path);return True
        except Exception as exc:
            optional_failure("cinematic.hybrid_package_write",exc)
            try:
                if temp and os.path.exists(temp):os.unlink(temp)
            except Exception:pass
            return False

    def _cin_build_package(self,item,row,cancel_event=None):
        """Build only Cinematic catalogue-row material.

        R149 removes panel/overview chrome from the Cinematic package entirely.
        Native Details owns those surfaces; this package may only cache the left
        catalogue rows and their accent. The full-screen backdrop is separately
        owned by the shared Cinematic/BG presentation cache.
        """
        def cancelled():return bool(cancel_event is not None and cancel_event.is_set())
        row=dict(row or {});key=self._record_key(row,item)
        if not key:return {}
        existing=self._cin_load_package(item,row)
        if existing:return existing
        backdrop=str(row.get("backdrop_local") or "")
        if not (backdrop and os.path.isfile(backdrop)):return {}
        root=self._cin_package_root(key);ensure_persistent_dirs(root)
        poster=str(row.get("poster_local") or "")
        adaptive_source=poster if poster and os.path.isfile(poster) else backdrop
        fingerprint=str(row.get("adaptive_fingerprint") or "")
        if cancelled():return {}
        settings_key=canonical_dynamic_rows_key(adaptive_source,selected_rim_only=True,cinematic_premium=True) or "hy149_rows_%s_%s"%(key,hashlib.sha1((adaptive_source+fingerprint).encode("utf-8","ignore")).hexdigest()[:14])
        settings=_build_dynamic_settings_episode_rows(adaptive_source,settings_key,selected_rim_only=True,cinematic_premium=True) or {}
        normal=str(settings.get("normal") or "");selected=str(settings.get("selected") or "")
        if not (normal and selected and os.path.isfile(normal) and os.path.isfile(selected)):return {}
        value_color=settings.get("value_color")
        if isinstance(value_color,(tuple,list)) and len(value_color)>=3:
            accent="#%02x%02x%02x"%(int(value_color[0]),int(value_color[1]),int(value_color[2]))
        else:accent=str(row.get("adaptive_accent") or row.get("adaptive_primary") or "#c99141")
        if cancelled():return {}
        stored_normal=self._cin_store_asset(normal,root,"row_normal",key)
        stored_selected=self._cin_store_asset(selected,root,"row_selected",key)
        if not (stored_normal and stored_selected):return {}
        package={
            "schema":self._cin_package_schema,"complete":True,"key":key,
            "signature":self._cin_package_signature(item,row),"accent":accent,
            "created_at":int(time.time()),"row_normal":stored_normal,
            "row_selected":stored_selected,
            "backdrop_source_sig":self._cin_file_sig(backdrop),
            "clean_backdrop_selector_version":int(row.get("clean_backdrop_selector_version") or 0),
        }
        if not self._cin_write_package(self._cin_package_path(key),package):return {}
        return package

    def _cin_backdrop_ready_token(self,item,row,package=None,path=""):
        package=package if isinstance(package,dict) else {}
        key=str(package.get("key") or self._record_key(row if isinstance(row,dict) else {},item) or "")
        sig=str(package.get("backdrop_source_sig") or "")
        if not sig:
            source=str((row or {}).get("backdrop_local") or path or "") if isinstance(row,dict) else str(path or "")
            sig=self._cin_file_sig(source) if source else ""
        return key+"|"+sig if sig else key

    def _cin_apply_package(self,item,row,package=None):
        package=package if isinstance(package,dict) and package else self._cin_load_package(item,row)
        if not package:return False
        normal=str(package.get("row_normal") or "");selected=str(package.get("row_selected") or "")
        acc=str(package.get("accent") or "")
        package_key=str(package.get("key") or self._record_key(row,item))
        # Backdrop and Adaptive are independent layers. If navigation already
        # painted this title's final cached backdrop, focus/package hydration is
        # forbidden from decoding or replacing it again.
        if not getattr(self,"_fast_bd_enabled",False) and str(getattr(self,"_cin_nav_backdrop_ready_key","") or "") != package_key:
            bd=str(package.get("backdrop_fast") or package.get("backdrop") or "")
            expected_sig=self._page_visual_key(item)
            if bd and os.path.isfile(bd) and expected_sig==self._fast_bd_current_sig():
                if self._cin_buffered_handoff("cin_backdrop","cin_backdrop_stage",bd,expected_sig=expected_sig):
                    self._cin_nav_backdrop_ready_key=package_key
        # R199 strict authority split: R81 package owns only the left-row material.
        # The information box can paint adaptive chrome only from the exact
        # persisted Native Details visual bundle for this title.
        details_chrome={}
        try:
            bundle=_load_visual_bundle(self.profile,self.media_type,item,row) or {}
            details_chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
        except Exception as exc:optional_failure("cinematic.r199_details_chrome",exc)
        adaptive_committed=self._cin_commit_adaptive_presentation(
            item,row,{"normal":normal,"selected":selected},details_chrome,acc
        )
        # Publish one authoritative local visual identity to Details + Player.
        # These are private runtime handoff fields, not provider metadata.
        try:
            poster=str((row or {}).get("poster_local") or "");backdrop=str((row or {}).get("backdrop_local") or "")
            if poster and os.path.isfile(poster):
                if isinstance(item,dict):
                    item["_cin_provider_poster_local"]=poster
                    item["_ultra_palette_source"]=poster
                    item["_ultra_poster_source"]=poster
                    item["_player_poster"]=poster
                    item["_adaptive_source_local"]=poster
                try:self._grid_palette_sources[self.index]=poster
                except Exception:pass
            if backdrop and os.path.isfile(backdrop) and isinstance(item,dict):
                item["_cin_provider_backdrop_local"]=backdrop
                item["_backdrop_source_local"]=backdrop
        except Exception as exc:optional_failure("cinematic.visual_handoff",exc)
        self._cin_visual_key=str(package.get("key") or self._record_key(row,item))
        return bool(adaptive_committed)

    def _cin_resolve_and_package(self,item,cancel_event=None):
        """Consume Details Authority metadata; Cinematic owns presentation only.

        R92 performs one targeted clean-backdrop check here, inside the existing
        stable-focus worker.  It uses the already-verified TMDb id only and never
        runs on arrow navigation or the Enigma2 UI thread.
        """
        if cancel_event is not None and cancel_event.is_set():return ({}, {})
        row=self._record(item) or {}
        def valid_file(value):
            try:return bool(value and os.path.isfile(str(value)) and os.path.getsize(str(value))>100)
            except Exception:return False
        backdrop=str(row.get("backdrop_local") or "")
        try:
            from .details_authority import resolve_canonical
            authoritative=resolve_canonical(self.profile,self.media_type,item,cancel_event=cancel_event,settings=(self._grid_settings or {}),provider_client=self.client,provider_downloader=_download_portal_artwork) or {}
            if authoritative:
                merged=dict(row)
                if authoritative.get("_replace_localized_metadata"):
                    for _meta_key in ('overview','overview_language','description','plot','descr','genres','genre','cast','actors','actor','crew','directors','director','writers','writer','runtime','duration','time','length','number_of_seasons','seasons_count','season_count','number_of_episodes','episodes_count','episode_count','release_date','first_air_date','year','release_year','releaseDate','countries','country','country_code','production_country','production_countries','origin_country','rating','vote_count','original_language','certification','age_rating'):
                        merged.pop(_meta_key,None)
                for key,value in authoritative.items():
                    if value not in (None,"",[],{}):merged[key]=value
                row=merged
        except Exception as exc:optional_failure("cinematic.details_authority",exc)
        if not valid_file(row.get("backdrop_local")) and valid_file(backdrop):row["backdrop_local"]=backdrop
        if cancel_event is not None and cancel_event.is_set():return (row,{})
        try:
            from .backdrop_clean_runtime import upgrade as _upgrade_clean_backdrop
            clean=_upgrade_clean_backdrop(self.profile,self.media_type,item,settings=(self._grid_settings or {}),cancel_event=cancel_event) or {}
            fresh=clean.get("data") if isinstance(clean,dict) else {}
            if isinstance(fresh,dict) and fresh:
                # backdrop_clean_runtime is a visual repair lane.  Never let its
                # manifest snapshot overwrite the raw Details-Authority metadata
                # that was selected/fallback-resolved just above.
                visual_keys=(
                    "tmdb_id","media_type","poster_local","backdrop_local",
                    "poster_path","backdrop_path","poster_url","backdrop_url","_backdrop_url","tmdb_backdrop_url",
                    "adaptive_primary","adaptive_secondary","adaptive_dark","adaptive_accent","adaptive_fingerprint",
                    "clean_backdrop_choice","clean_backdrop_iso","clean_backdrop_selector_version",
                    "backdrop_source","backdrop_state","identity_verified","identity_pointer_verified",
                )
                merged=dict(row)
                for key in visual_keys:
                    value=fresh.get(key)
                    if value not in (None,"",[],{}):merged[key]=value
                row=merged
        except Exception as exc:optional_failure("cinematic.clean_backdrop_upgrade",exc)
        if cancel_event is not None and cancel_event.is_set():return (row,{})
        # R193 radical split: stable focus owns metadata/canonical persistence only.
        # Visible backdrop and adaptive chrome are already complete through the
        # direct-clean + HDD adaptive-colour lane. Never build shared backdrops, row
        # PNGs or final adaptive bundles here. Those were the measured 8-10s tail.
        package=self._cin_load_package(item,row) or {}
        return (row,package)

    def _cin_enqueue_missed(self,item):
        # Focus-driven law: skipped titles are never recovered in background.
        return

    def _cin_schedule_focus(self,item):
        if self._folder_artwork_cache_running:return
        sig=self._page_visual_key(item)
        if not sig:return
        if self._cin_focus_sig==sig and self._cin_focus_future is not None and not self._cin_focus_future.done():return
        self._cin_cancel_background();self._cin_cancel_focus(enqueue=False)
        ev=threading.Event();self._cin_focus_cancel=ev;self._cin_focus_sig=sig
        def worker():
            try:
                row,package=self._cin_resolve_and_package(dict(item),ev)
                if not ev.is_set():self._cin_jobs.put({"kind":"focus","sig":sig,"row":row,"package":package})
            except Exception as exc:optional_failure("cinematic.hybrid_focus",exc)
        self._cin_focus_future=getattr(self,"_cin_hybrid_executor",_CIN_HYBRID_EXECUTOR).submit(worker);self._cin_kick_poll()

    def _cin_start_hidden_folder_cache(self,rows):
        self._cin_cancel_background()
        if not self._cin_hidden or self._folder_artwork_cache_running:return
        items=[dict(x) for x in (rows or []) if isinstance(x,dict)]
        if not items:return
        ev=threading.Event();self._cin_background_cancel=ev
        def worker():
            done=0
            for item in items:
                if ev.is_set() or not self._cin_hidden:break
                try:
                    row=self._record(item) or {}
                    if self._cin_load_package(item,row):continue
                    self._cin_resolve_and_package(item,ev);done+=1
                except Exception as exc:optional_failure("cinematic.hidden_fill",exc)
                if ev.is_set():break
            try:self._cin_jobs.put({"kind":"hidden_done","done":done})
            except Exception:pass
        self._cin_background_future=getattr(self,"_cin_hybrid_executor",_CIN_HYBRID_EXECUTOR).submit(worker);self._cin_kick_poll()

    @staticmethod
    def _blue_title_logo_cache_path(media_type,tmdb_id,logo_lang="en"):
        try:
            from .title_logo_runtime import ultra_title_logo_cache_path
            return ultra_title_logo_cache_path(media_type,tmdb_id,logo_lang,(420,144))
        except Exception:return ""

    def _cache_title_logo_for_blue(self,item,row,cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():return ""
        try:
            # Use Poster Grid V2's exact receiver-proven identity gate and logo
            # resolver.  Only the Cinematic presentation canvas stays different.
            from .ui_grid_screens import _pgv2_authoritative_logo_row_detached,_pgv2_resolve_title_logo_detached
            settings=(getattr(self,"_grid_settings",None) or load_settings() or {})
            fresh=_pgv2_authoritative_logo_row_detached(
                self.profile,self.media_type,dict(item or {}),dict(row or {}),
                cancel_event=cancel_event,settings=settings
            )
            if cancel_event is not None and cancel_event.is_set():return ""
            path,_resolved=_pgv2_resolve_title_logo_detached(
                self.profile,self.media_type,dict(item or {}),fresh,
                cancel_event=cancel_event,settings=settings,canvas_size=(420,144)
            )
            return path
        except Exception as exc:
            optional_failure("cinematic.blue_title_logo",exc);return ""

    def _details_bundle_ready(self,item,row):
        try:
            bundle=_load_visual_bundle(self.profile,self.media_type,item,row) or {}
            chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
            return bool(
                bundle.get("detail_poster") and os.path.isfile(str(bundle.get("detail_poster") or "")) and
                bundle.get("backdrop_present") and os.path.isfile(str(bundle.get("backdrop_present") or "")) and
                bundle.get("theme") and os.path.isfile(str(bundle.get("theme") or "")) and
                chrome.get("panel_detail") and chrome.get("overview_detail")
            )
        except Exception:
            return False

    def _build_details_bundle_for_blue_cache(self,item,row,cin_package=None,cancel_event=None):
        """Build Details presentation only during explicit BLUE Cache Artwork.

        Browsing and opening Details never generate adaptive assets.  The same
        canonical poster/backdrop already cached outside are used once here, on
        user request, then Details becomes HDD-only.
        """
        if cancel_event is not None and cancel_event.is_set():return False
        row=dict(row or {})
        poster=str(row.get("poster_local") or "");backdrop=str(row.get("backdrop_local") or "")
        if not (poster and os.path.isfile(poster) and backdrop and os.path.isfile(backdrop)):return False
        try:self._cache_title_logo_for_blue(item,row,cancel_event)
        except Exception as exc:optional_failure("cinematic.blue_title_logo_prepare",exc)
        if self._details_bundle_ready(item,row):return True
        try:
            stamp=str(os.path.getmtime(poster))
            digest=hashlib.sha1((poster+stamp+"|us125-details").encode("utf-8","ignore")).hexdigest()[:20]
            chrome=_build_dynamic_details_chrome(poster,canonical_dynamic_details_key(poster) or ("us125_"+digest)) or {}
            try:
                exact_accent=str((cin_package or {}).get("accent") or row.get("adaptive_accent") or row.get("adaptive_primary") or "").strip()
                if re.match(r"^#[0-9a-fA-F]{6}$",exact_accent):chrome["accent_color"]=exact_accent.lower()
            except Exception:pass
            if cancel_event is not None and cancel_event.is_set():return False
            theme=_build_dynamic_details_gradient(poster,os.path.join(THUMB_CACHE_DIR,"us125_%s_bg.jpg"%digest),(1920,1080)) or ""
            # Details poster focus is now a runtime 2px adaptive trace sampled
            # from the already-cached glass color.  Do not build/carry any
            # poster-frame surface; it was extra CPU/HDD work and wrong geometry.
            detail_poster=_detail_cover_artwork(poster,(352,552)) or ""
            if cancel_event is not None and cancel_event.is_set():return False
            try:bd_stamp=str(os.path.getmtime(backdrop))
            except Exception:bd_stamp="0"
            bd_digest=hashlib.sha1((backdrop+"|"+bd_stamp+"|us198-present").encode("utf-8","ignore")).hexdigest()[:24]
            bd_target=os.path.join(THUMB_CACHE_DIR,"us220_detail_%s_1620x620.png"%bd_digest)
            backdrop_present=_build_integrated_backdrop(backdrop,bd_target,(1620,620)) or ""
            payload={
                "poster":poster,"detail_poster":detail_poster,"backdrop_present":backdrop_present,
                "theme":theme,"chrome":chrome,
            }
            return bool(_save_visual_bundle(self.profile,self.media_type,item,payload,snapshot=row))
        except Exception as exc:
            optional_failure("cinematic.blue_details_bundle",exc);return False

    def _cin_blue_lock_path(self,item):
        """Persistent completed-vault marker for explicit BLUE Cache Artwork.

        The marker is intentionally independent of process/session/version state.
        It records only physical HDD assets that were already completed once.
        Restarting Enigma2 or reopening a folder therefore cannot make a finished
        title expensive again.  A title is repaired only when one of those files
        is genuinely missing/corrupt.
        """
        try:
            safe=self._cin_safe_key(self._page_visual_key(item))
            return os.path.join(BLUE_CACHE,"cinematic_ready","cinematic_blue_ready_%s.json"%safe) if safe else ""
        except Exception:return ""

    def _cin_blue_fast_path(self,item,row=None):
        """One durable decoder-ready backdrop for explicit BLUE Cache Artwork.

        Normal browsing keeps presentation derivatives session-only.  BLUE is an
        explicit user request, so its final JPEG may survive Enigma2 restarts, but
        the multi-megabyte PNG build intermediate never does.
        """
        try:
            row=row if isinstance(row,dict) else {}
            key=self._record_key(row,item) or self._page_visual_key(item)
            safe=self._cin_safe_key(key)
            return os.path.join(BLUE_CACHE,"cinematic_fast","cinematic_%s_backdrop_fast.jpg"%safe) if safe else ""
        except Exception:return ""

    def _cin_promote_blue_fast(self,item,row,package):
        source=str((package or {}).get("backdrop_fast") or "") if isinstance(package,dict) else ""
        if not source or not os.path.isfile(source) or os.path.getsize(source)<=4096:return ""
        target=self._cin_blue_fast_path(item,row)
        if not target:return ""
        try:
            if os.path.isfile(target) and os.path.getsize(target)>4096:return target
            if not ensure_persistent_dirs(os.path.dirname(target)):return ""
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            try:
                if os.path.exists(temp):os.unlink(temp)
                os.link(source,temp)
            except Exception:
                shutil.copy2(source,temp)
            os.replace(temp,target)
            return target if os.path.isfile(target) and os.path.getsize(target)>4096 else ""
        except Exception as exc:
            optional_failure("cinematic.blue_fast_promote",exc)
            try:
                if 'temp' in locals() and os.path.exists(temp):os.unlink(temp)
            except OSError:pass
            return ""

    @staticmethod
    def _cin_blue_lock_files_valid(files):
        if not isinstance(files,(list,tuple)) or not files:return False
        for path in files:
            try:
                path=str(path or "")
                if not path or not os.path.isfile(path) or os.path.getsize(path)<=256:return False
            except Exception:return False
        return True

    def _cin_read_blue_lock(self,item):
        path=self._cin_blue_lock_path(item)
        if not path or not os.path.isfile(path):return {}
        try:
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            # Schema 1 is the older presentation-heavy vault. Schema 2 is the
            # release artwork-only seal. Both stay valid so upgrades never make
            # previously cached titles expensive again.
            if not isinstance(data,dict) or int(data.get("schema") or 0) not in (1,2,3,4,5):return {}
            if not self._cin_blue_lock_files_valid(data.get("files")):return {}
            return data
        except Exception:return {}

    def _cin_write_blue_lock(self,item,row,poster=None,backdrop=None,package=None):
        """Persist BLUE completion only after the final Cinematic backdrop exists.

        V7.0.38 keeps Details Authority as the only metadata resolver, then lets
        BLUE build the already-resolved Cinematic presentation once, sequentially,
        while the user explicitly waits for Cache Artwork.  This moves the costly
        first-focus backdrop conversion into BLUE without reintroducing any
        independent Cinematic metadata search.  Older schema-1/2 locks stay
        readable, but a V7.0.38 BLUE pass upgrades them to schema 3.
        """
        try:
            p=str(poster or (row or {}).get("poster_local") or "")
            b=str(backdrop or (row or {}).get("backdrop_local") or "")
            package=dict(package or {}) if isinstance(package,dict) else {}
            final_bd=""
            if b and os.path.isfile(b):
                final_bd=_prepare_shared_cinematic_backdrop(b,self._record_key(row,item),row,build=True,accent_hint=str(package.get("accent") or row.get("adaptive_accent") or row.get("adaptive_primary") or "")) or ""
                _prepare_three_view_fast_chrome(b,self._record_key(row,item),row,build=True)
            adaptive=self._cin_load_final_adaptive(item,row)
            adaptive_files=[str(adaptive.get(k) or "") for k in ("panel_detail","overview_detail","cast_detail","quality","year","runtime","country","genre_detail")] if adaptive else []
            required=[p,b,final_bd]+adaptive_files
            if not adaptive or not self._cin_blue_lock_files_valid(required):return False
            path=self._cin_blue_lock_path(item)
            if not path or not ensure_persistent_dirs(os.path.dirname(path)):return False
            payload={
                "schema":5,
                "visual_key":self._page_visual_key(item),
                "files":required,
                "backdrop_fast":final_bd,
                "tmdb_id":str((row or {}).get("tmdb_id") or ""),
                "cinematic_key":str(package.get("key") or ""),
                "backdrop_source_sig":self._cin_file_sig(b),
                "clean_backdrop_selector_version":int((row or {}).get("clean_backdrop_selector_version") or 0),
                "sealed_at":int(time.time()),
            }
            temp=path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            with open(temp,"w",encoding="utf-8") as h:
                json.dump(payload,h,ensure_ascii=False,separators=(",",":"))  # rebuildable readiness seal
            os.replace(temp,path);return True
        except Exception as exc:
            optional_failure("cinematic.blue_ready_lock_write",exc)
            try:
                if 'temp' in locals() and os.path.exists(temp):os.unlink(temp)
            except Exception:pass
            return False

    def _folder_artwork_ready_hook(self,item,data):
        """Incremental BLUE readiness: fill only physically missing components.

        R171 never restarts a completed title.  Existing poster/backdrop masters,
        canonical metadata, persistent final adaptive, shared fast backdrop,
        three-view chrome and title-logo cache are reused independently.  Only a
        component that is genuinely absent is built/resolved.
        """
        row=dict(data or {}) if isinstance(data,dict) else {}
        poster=str(row.get("poster_local") or "")
        backdrop=str(row.get("backdrop_local") or "")
        try:
            from .artwork_v2 import _valid_backdrop
            art_ready=bool(poster and os.path.isfile(poster) and os.path.getsize(poster)>512 and _valid_backdrop(backdrop))
        except Exception:
            art_ready=False
        if not art_ready:return False

        # Completed BLUE seal: disk-only verification, zero metadata/artwork work.
        try:
            lock=self._cin_read_blue_lock(item) or {}
            fast=str(lock.get("backdrop_fast") or "")
            adaptive=self._cin_load_final_adaptive(item,row)
            if adaptive and fast and os.path.isfile(fast) and os.path.getsize(fast)>4096:
                if int(row.get("clean_backdrop_selector_version") or 0)>=3:
                    current_sig=self._cin_file_sig(backdrop)
                    if current_sig and str(lock.get("backdrop_source_sig") or "")==current_sig:return True
                else:return True
        except Exception:pass

        try:
            from .details_authority import load_canonical, metadata_language_ready, resolve_canonical
            canonical=load_canonical(self.profile,self.media_type,item) or {}
            if not metadata_language_ready(canonical,(self._grid_settings or {})):
                canonical=resolve_canonical(
                    self.profile,self.media_type,item,cancel_event=None,
                    settings=(self._grid_settings or {}),provider_client=self.client,provider_downloader=_download_portal_artwork
                ) or canonical
            if not metadata_language_ready(canonical,(self._grid_settings or {})):return False
            merged=dict(row);merged.update(canonical)
            # Never let metadata overwrite physically known canonical artwork.
            if poster and os.path.isfile(poster):merged["poster_local"]=poster
            if backdrop and os.path.isfile(backdrop):merged["backdrop_local"]=backdrop
            key=self._record_key(merged,item)

            # Shared decoder-ready backdrop: lookup first, build only on a true miss.
            final_bd=_prepare_shared_cinematic_backdrop(backdrop,key,merged,build=False)
            if not final_bd:
                final_bd=_prepare_shared_cinematic_backdrop(
                    backdrop,key,merged,build=True,
                    accent_hint=str(merged.get("adaptive_accent") or merged.get("adaptive_primary") or "")
                )
            if not (final_bd and os.path.isfile(final_bd) and os.path.getsize(final_bd)>256):return False

            # BG1/BG2/Cinematic shared chrome is independent and missing-only too.
            shared=_prepare_three_view_fast_chrome(backdrop,key,merged,build=False) or {}
            if not shared:
                shared=_prepare_three_view_fast_chrome(backdrop,key,merged,build=True) or {}

            # Final Details-family adaptive is now persistent across restarts.
            adaptive=self._cin_load_final_adaptive(item,merged)
            if not adaptive:
                adaptive=self._cin_ensure_final_adaptive(item,merged,cancel_event=None,build=True)
            if not adaptive:return False

            # Title Logo is also missing-only. Absence is allowed for titles that
            # genuinely have no usable logo, but a cached one is never re-resolved.
            try:
                visible=self._rail_title(item,merged) or str(merged.get("title") or merged.get("name") or "")
                logo=ultra_title_logo_cached(self.media_type,item,merged,(420,144),visible)
                if not logo:self._cache_title_logo_for_blue(item,merged,None)
            except Exception as exc:optional_failure("cinematic.r171_blue_logo_missing_only",exc)

            # Seal using already-hot components. _cin_write_blue_lock only validates
            # the same persistent files and cannot re-download poster/backdrop.
            self._cin_write_blue_lock(item,merged,poster,backdrop,package={"accent":str(merged.get("adaptive_accent") or merged.get("adaptive_primary") or "")})
            return True
        except Exception as exc:
            optional_failure("cinematic.r171_blue_incremental",exc)
        return False

    def _folder_artwork_finalize_hook(self,item,poster,backdrop,payload,data,cancel_event=None):
        """After artwork rescue, preload the same canonical Details record."""
        if cancel_event is not None and cancel_event.is_set():return False
        row=dict(data or {}) if isinstance(data,dict) else {}
        if poster and os.path.isfile(str(poster)):row["poster_local"]=str(poster)
        if backdrop and os.path.isfile(str(backdrop)):row["backdrop_local"]=str(backdrop)
        # Reuse the exact readiness path so both already-cached and newly rescued
        # titles obey one contract: BLUE completes only after Details metadata is
        # persistent. The Cinematic package is built only after Details metadata is canonical; no independent metadata lookup is allowed.
        return bool(self._folder_artwork_ready_hook(item,row))

    def _cin_enter_cache_exclusive(self):
        """Give explicit BLUE Cache Artwork exclusive ownership of the screen.

        While a large folder vault is running, Cinematic must not spend CPU/HDD
        time on focus artwork, title logos, cast, page hydration or periodic
        visual work. The async callback/progress channel remains alive so the BLUE
        counter can move, but every unrelated screen engine is silent.
        """
        self._cin_cache_exclusive=True
        self._folder_artwork_fast_art_only=True
        self._cin_cancel_fast_info();self._cin_cancel_focus(enqueue=False);self._cin_cancel_background()
        for timer_name in ("_grid_focus_timer","_series_prefetch_timer","_detail_prefetch_timer","_epg_timer","_cin_poll","_cin_fast_info_timer"):
            try:getattr(self,timer_name).stop()
            except Exception:pass
        # Discard any stale visual jobs queued before BLUE took ownership.
        for qname in ("_cin_jobs","_art_prefetch_jobs","_quality_prefetch_jobs","_epg_jobs"):
            q=getattr(self,qname,None)
            if q is None:continue
            try:
                while True:q.get_nowait()
            except Exception:pass

    def _cin_leave_cache_exclusive(self):
        self._folder_artwork_fast_art_only=False
        self._cin_cache_exclusive=False
        if self._screen_closed:return
        # Cinematic poll is normally only a queue-delivery heartbeat. Restore it
        # after BLUE exits, then refresh exactly the currently focused title once.
        try:self._cin_set_poll_interval(120,force=True)
        except Exception:pass
        try:
            if self.grid_items:
                self._grid_focus_timer.stop();self._grid_focus_timer.start(120,True)
        except Exception:pass

    def _drain_jobs(self):
        if getattr(self,"_cin_cache_exclusive",False):
            # Cache-exclusive GUI pump: only the async completion queue and BLUE
            # progress queue are serviced. No poster/adaptive/EPG/cast/page drains.
            if time.monotonic() < float(getattr(self,"_nav_burst_until",0.0) or 0.0):
                return
            AsyncScreenMixin._drain_jobs(self)
            if not self._screen_closed:
                try:self._drain_folder_artwork_progress()
                except Exception as exc:optional_failure("cinematic.blue_progress_only",exc)
            return
        return PremiumGridBase._drain_jobs(self)

    def _cin_blue_preflight_ready(self,item):
        """Read-only full-title BLUE readiness check. Never repairs/builds."""
        try:
            row=self._record(item) or {}
            poster=str(row.get("poster_local") or "");backdrop=str(row.get("backdrop_local") or "")
            if not (poster and os.path.isfile(poster) and os.path.getsize(poster)>512):return False
            try:
                from .artwork_v2 import _valid_backdrop
                if not _valid_backdrop(backdrop):return False
            except Exception:return False
            adaptive=self._cin_load_final_adaptive(item,row)
            if not adaptive:return False
            key=self._record_key(row,item)
            shared=_prepare_shared_cinematic_backdrop(backdrop,key,row,build=False)
            if not (shared and os.path.isfile(shared) and os.path.getsize(shared)>4096):return False
            # BLUE also prepares the small shared BG1/BG2/Cinematic row/info shell.
            # build=False is lookup-only and therefore safe in preflight.
            three=_prepare_three_view_fast_chrome(backdrop,key,row,build=False) or {}
            if not three:return False
            try:
                from .details_authority import load_canonical, metadata_language_ready
                canonical=load_canonical(self.profile,self.media_type,item) or {}
                if not metadata_language_ready(canonical,(self._grid_settings or {})):return False
            except Exception:return False
            return True
        except Exception as exc:
            optional_failure("cinematic.blue_preflight",exc);return False

    def _cache_folder_artwork_worker(self, rows, cancel_event=None):
        # R171 incremental preflight: the visible heavy counter contains ONLY
        # titles that still need some component. Fully-ready titles are verified
        # with lookup-only HDD checks and never enter the artwork/metadata/adaptive
        # worker again. A 68-title folder with 2 incomplete titles therefore runs
        # 0/2 -> 2/2, not 0/68 -> 68/68.
        self._folder_artwork_fast_art_only=True
        all_rows=[dict(x) for x in (rows or []) if isinstance(x,dict)]
        todo=[];hot=0
        for item in all_rows:
            if cancel_event is not None and cancel_event.is_set():break
            if self._cin_blue_preflight_ready(item):hot+=1
            else:todo.append(item)
        if not todo:
            return {"total":len(all_rows),"done":len(all_rows),"work_total":0,"downloaded":0,"hot":hot,"failed":0,
                    "poster_missing":0,"backdrop_missing":0,"poster_recovered":0,"backdrop_recovered":0,
                    "poster_failed":0,"backdrop_failed":0,"backdrop_upgrade_candidates":0,"backdrop_upgraded":0,
                    "recovered":0,"presentation_total":len(all_rows),"presentation_ready":len(all_rows),
                    "preflight_ready":hot}
        result=PremiumGridBase._cache_folder_artwork_worker(self,todo,cancel_event) or {}
        # Keep final folder status truthful while the progress counter represented
        # only actual work. Base result counts apply to todo; total is the folder.
        result=dict(result);result["total"]=len(all_rows);result["work_total"]=len(todo);result["preflight_ready"]=hot
        result["hot"]=int(result.get("hot",0) or 0)+hot
        try:
            result["presentation_total"]=len(all_rows)
            result["presentation_ready"]=hot+max(0,len(todo)-int(result.get("failed",0) or 0))
        except Exception:pass
        return result

    def _apply_folder_artwork_result(self,result):
        try:return PremiumGridBase._apply_folder_artwork_result(self,result)
        finally:self._cin_leave_cache_exclusive()

    def cache_folder_artwork(self):
        """Use the shared current-page, backdrop-only Q60 BLUE action."""
        return PremiumGridBase.cache_folder_artwork(self)

    def _item_fallback_title(self,item):
        if not isinstance(item,dict):return ""
        clean=bool((self._grid_settings or {}).get("clean_titles",True))
        raw=item.get("_raw_name") or item.get("name") or item.get("title") or item.get("series_name") or ""
        return str(raw or "").strip()

    def _display_rail_title(self,value):
        raw=str(value or "").strip()
        if not raw:return ""
        return (_catalogue_title(raw) or raw) if bool((self._grid_settings or {}).get("clean_titles",True)) else raw

    def _item_fallback_year(self,item):
        if not isinstance(item,dict):return ""
        for k in ("year","release_date","first_air_date","added"):
            v=str(item.get(k) or "").strip()
            if len(v)>=4 and v[:4].isdigit():return v[:4]
        return ""

    def _rail_title(self,item,row=None):
        """Always show a clean catalogue title on the left rail, aligned left.

        The visible rail never prefers a localized title merely because TMDB returned
        one.  It first cleans the provider/catalogue name so routing prefixes, quality
        tags and decorative junk cannot leak into either display or identity matching.
        No network work is allowed here.
        """
        row=row if isinstance(row,dict) else {}
        provider=self._display_rail_title(self._item_fallback_title(item))
        original=self._display_rail_title(row.get("original_title") or "")
        saved=self._display_rail_title(row.get("title") or row.get("name") or "")
        return provider or original or saved

    def _quality(self,item):
        try:
            row=self._grid_item_state.get(id(item),{}) or {};q=str(row.get("quality") or "").strip()
            if q:return q
        except Exception:pass
        try:
            badges=str(quality_badges(self._item_fallback_title(item)) or "").strip()
            if badges:return badges.split(" • ")[0]
        except Exception:pass
        name=self._item_fallback_title(item).upper()
        if "4K" in name or "2160" in name:return "4K"
        if "FHD" in name or "1080" in name:return "FHD"
        if "HD" in name or "720" in name:return "HD"
        return "AUTO"

    def _record(self,item):
        """Return the same final HDD state used by Native Details.

        Cinematic never resolves metadata itself.  It merges the global artwork
        manifest with the Details pointer/global snapshot so outside and inside
        cannot disagree about overview, seasons or credits.  Both readers are
        HDD-only and ultimately point at the same TMDB library identity.
        """
        try:
            art=load_manifest(self.profile,self.media_type,item) or {}
            detail=load_detail_snapshot(self.profile,self.media_type,item) or {}
            if art.get("tmdb_id"):
                direct=load_detail_snapshot_by_tmdb(art.get("media_type") or self.media_type,art.get("tmdb_id")) or {}
                if direct:
                    merged=dict(detail);merged.update(direct);detail=merged
            # Native Details owns every information field once its verified raw
            # TMDb snapshot is ready.  Clear artwork/provider metadata first so a
            # field that TMDb genuinely lacks cannot resurrect from an older
            # manifest; Clean Names/title identity remains completely separate.
            row=dict(art)
            if detail.get("_details_authority_ready"):
                for _meta_key in ('overview','overview_language','description','plot','descr','genres','genre','cast','actors','actor','crew','directors','director','writers','writer','runtime','duration','time','length','number_of_seasons','seasons_count','season_count','number_of_episodes','episodes_count','episode_count','release_date','first_air_date','year','release_year','releaseDate','countries','country','country_code','production_country','production_countries','origin_country','rating','vote_count','original_language','certification','age_rating'):
                    row.pop(_meta_key,None)
            row.update(detail)
            for _visual_key in ("poster_local","backdrop_local","poster_url","backdrop_url",
                                "poster_source","backdrop_source","backdrop_state",
                                "clean_backdrop_selector_version","manual_rescue_poster_locked",
                                "manual_rescue_backdrop_locked","provider_bootstrap"):
                _visual_value=art.get(_visual_key)
                if _visual_value not in (None,"",[],{}):row[_visual_key]=_visual_value
            # Focused provider artwork is a verified local handoff, not a second
            # metadata authority. Keep it visible for this screen lifetime and
            # let Details/Player reuse the same exact HDD files.
            if isinstance(item,dict):
                pp=str(item.get("_cin_provider_poster_local") or item.get("_ultra_poster_source") or item.get("_player_poster") or "")
                pb=str(item.get("_cin_provider_backdrop_local") or item.get("_backdrop_source_local") or "")
                if pp and os.path.isfile(pp):row["poster_local"]=pp
                if pb and os.path.isfile(pb):row["backdrop_local"]=pb
            # Preserve the newest timestamp so the HDD poll notices metadata
            # completed by Native Details while Cinematic remains open.
            try:row["updated_at"]=max(int(detail.get("updated_at") or 0),int(art.get("updated_at") or 0))
            except Exception:pass
            return row
        except Exception as exc:
            optional_failure("cinematic.global_record",exc);return {}

    def _record_key(self,row,item):
        tid=row.get("tmdb_id") if isinstance(row,dict) else None
        if tid:return "%s-%s"%("tv" if self.media_type=="series" else "movie",tid)
        return self._page_visual_key(item)

    def _format_runtime(self,value):
        try:
            m=int(float(value or 0));return "%dh %02dm"%(m//60,m%60) if m>=60 else ("%d min"%m if m else "")
        except Exception:return ""

    def _cin_list_row(self, pos, selected=False, record=None, allow_progress_build=True):
        item=self.grid_items[pos]
        record=record if isinstance(record,dict) else self._record(item)
        title=self._rail_title(item,record)
        # Cinematic rail deliberately reserves the entire second line for watched
        # progress. Quality/year already exist in the full Details cluster.
        # Keep the key-repeat path RAM-only: the old implementation recalculated
        # quality/year and re-probed/generated the laser PNG every time the
        # selected row changed, even though neither value is rendered here.
        value=""
        state=self._grid_item_state.get(id(item),{}) or {}
        progress_pct=0
        if self.media_type=="vod":
            try:
                play_pos=float(state.get("position") or 0);dur=float(state.get("duration") or 0)
                if int(state.get("completed") or 0):progress_pct=100
                elif play_pos>0 and dur>0:progress_pct=max(1,min(99,int(round((play_pos*100.0)/dur))))
            except Exception:progress_pct=0
        accent=str(getattr(self,"_cin_progress_accent","#69c9f4") or "#69c9f4")
        progress_neon=""
        if progress_pct>0:
            try:
                frame_cache=getattr(self,"_cin_progress_frame_ram",None)
                if not isinstance(frame_cache,dict):
                    frame_cache={};self._cin_progress_frame_ram=frame_cache
                frame_key=(accent,int(progress_pct),300)
                progress_neon=str(frame_cache.get(frame_key) or "")
                if not progress_neon and allow_progress_build:
                    progress_neon=_cinematic_row_progress_frame(accent,progress_pct) or ""
                    if progress_neon:frame_cache[frame_key]=progress_neon
            except Exception as exc:optional_failure("cinematic.row_progress_neon",exc)
        cache=getattr(self,"_cin_hot_row_cache",None)
        if not isinstance(cache,dict):
            cache={};self._cin_hot_row_cache=cache
        cache_key=(id(item),bool(selected),title,int(progress_pct),accent,progress_neon,
                   str(self._settings_row_asset or ""),str(self._settings_row_selected_asset or ""))
        cached=cache.get(cache_key)
        if cached is not None:return cached
        meta={
            "selected":bool(selected),
            "value":value,
            "row_asset":self._settings_row_asset,
            "row_selected_asset":self._settings_row_selected_asset,
            "value_color":0xFFFFFF,
            "watch_progress":progress_pct,
            "watch_progress_neon":progress_neon,
            "watch_progress_full_line":True,
            # Beta81: text-only polish on the stable Beta77 row renderer.
            # Remove the catalogue icon so names start from the left edge,
            # and enable adaptive title sizing without touching row assets.
            "hide_icon":True,
            "adaptive_title":True,
            "compact_row":True,
        }
        row=(title,self._cin_list_icon,item,meta);cache[cache_key]=row;return row

    def _cin_index_current_page(self):
        """Build the one page-local backdrop/record/package index used by all views."""
        self._cin_hot_row_cache={}
        self._cin_row_complete.clear();self._cin_page_records={};self._cin_page_packages={};self._cin_page_backdrops={}
        for pos in range(min(self.page_size,len(self.grid_items))):
            item=self.grid_items[pos]
            try:
                prepared=(getattr(self,"_prepared_art_for_render",[]) or [])
                ram_art=prepared[pos] if pos<len(prepared) and isinstance(prepared[pos],dict) else {}
                ram_bd=str(ram_art.get("backdrop") or "")
                if ram_bd:self._cin_page_backdrops[pos]=ram_bd
            except Exception:pass
            try:
                alias={} if pos in self._cin_page_backdrops else (self._cin_load_nav_alias(item) or {});abd=str(alias.get("backdrop") or "")
                if abd and os.path.isfile(abd):self._cin_page_backdrops[pos]=abd
                elif pos not in self._cin_page_backdrops:
                    blue=self._cin_read_blue_lock(item) or {};blue_bd=str(blue.get("backdrop_fast") or "")
                    if blue_bd and os.path.isfile(blue_bd) and os.path.getsize(blue_bd)>4096:self._cin_page_backdrops[pos]=blue_bd
            except Exception as exc:optional_failure("cinematic.page_nav_alias",exc)
            try:self._cin_page_records[pos]=self._record(item) or {}
            except Exception:self._cin_page_records[pos]={}
            try:
                pkg=self._cin_load_canonical_package_fast(item,self._cin_page_records.get(pos,{}) or {}) or {}
                if pkg:
                    self._cin_page_packages[pos]=pkg
                    bd=str(pkg.get("backdrop_fast") or pkg.get("backdrop") or "")
                    if bd and os.path.isfile(bd):
                        self._cin_page_backdrops[pos]=bd
                        self._cin_publish_nav_alias(item,self._cin_page_records.get(pos,{}) or {},pkg)
            except Exception as exc:optional_failure("cinematic.page_backdrop_index",exc)

    def _render_grid(self):
        self._cin_hot_row_cache={}
        # Beta86: keep the same Settings/Episodes row grammar in a compact 73px
        # slot and show 12 rows, starting directly beneath the Ultra Stalker header.
        # release additionally indexes the tiny canonical package JSON for every
        # visible row here.  Arrow navigation therefore does zero HDD lookup and
        # can bind the already-cached decoder-ready backdrop immediately.
        self._cin_row_complete.clear();self._cin_page_records={};self._cin_page_packages={};self._cin_page_backdrops={}
        rows=[]
        for pos in range(min(self.page_size,len(self.grid_items))):
            _item=self.grid_items[pos]
            # R75: first consume the page-prepared local backdrop pointer already
            # in RAM.  No HDD stat/decode happens on arrow navigation.  The final
            # file validation + native decode is deferred until the 300 ms cached / 550 ms cold quiet window.
            try:
                _prepared=(getattr(self,"_prepared_art_for_render",[]) or [])
                _ram_art=_prepared[pos] if pos < len(_prepared) and isinstance(_prepared[pos],dict) else {}
                _ram_bd=str(_ram_art.get("backdrop") or "")
                if _ram_bd:self._cin_page_backdrops[pos]=_ram_bd
            except Exception:pass
            # R54: Backdrop navigation index is independent of metadata/adaptive hydration.
            # Prefer the tiny nav alias, but BLUE Cache Artwork is itself a durable
            # decoder-ready authority. Older/repaired libraries can have a valid
            # BLUE backdrop_fast without a nav alias/canonical package pointer;
            # index that JPEG once during page paint so arrow navigation stays RAM-only.
            try:
                _alias={} if pos in self._cin_page_backdrops else (self._cin_load_nav_alias(_item) or {});_abd=str(_alias.get("backdrop") or "")
                if _abd and os.path.isfile(_abd):
                    self._cin_page_backdrops[pos]=_abd
                elif pos not in self._cin_page_backdrops:
                    _blue=self._cin_read_blue_lock(_item) or {};_blue_bd=str(_blue.get("backdrop_fast") or "")
                    if _blue_bd and os.path.isfile(_blue_bd) and os.path.getsize(_blue_bd)>4096:
                        self._cin_page_backdrops[pos]=_blue_bd
            except Exception as exc:optional_failure("cinematic.page_nav_alias",exc)
            try:self._cin_page_records[pos]=self._record(_item) or {}
            except Exception:self._cin_page_records[pos]={}
            try:
                _pkg=self._cin_load_canonical_package_fast(_item,self._cin_page_records.get(pos,{}) or {}) or {}
                if _pkg:
                    self._cin_page_packages[pos]=_pkg
                    _bd=str(_pkg.get("backdrop_fast") or _pkg.get("backdrop") or "")
                    if _bd and os.path.isfile(_bd):
                        self._cin_page_backdrops[pos]=_bd
                        self._cin_publish_nav_alias(_item,self._cin_page_records.get(pos,{}) or {},_pkg)
            except Exception as exc:optional_failure("cinematic.page_backdrop_index",exc)
            rows.append(self._cin_list_row(pos,selected=(pos==self.index),record=self._cin_page_records.get(pos)))
        try:
            self["cin_list"].set_icon_rows(rows)
            if rows:self["cin_list"].moveToIndex(max(0,min(self.index,len(rows)-1)))
        except Exception as exc:optional_failure("cinematic.settings_list_render",exc)
        try:
            _jpg=sum(1 for _p in self._cin_page_backdrops.values() if str(_p or "").lower().endswith((".jpg",".jpeg")))
            logging.getLogger("UltraStalker").info("PERF54 cinematic_backdrop_index page=%s rows=%d ready=%d jpeg=%d",int(getattr(self,"page",1) or 1),len(rows),len(self._cin_page_backdrops),_jpg)
        except Exception:pass
        self._update_selection()

    def _cin_after_navigation(self,item):
        """The single selected-title navigation gate used by Cinematic/BG1/BG2."""
        self._cin_last_nav_at=time.monotonic()
        self._cin_cancel_fast_info();self._cin_cancel_focus(enqueue=False);self._cin_cancel_background();self._cin_last_focus_item=dict(item)
        self._cin_arm_fast_info()
        if not getattr(self,"_cin_cache_exclusive",False):
            try:
                delay=self._cin_cached_backdrop_delay_ms()
                self._cin_nav_backdrop_timer.stop();self._cin_nav_backdrop_timer.start(int(delay),True)
            except Exception as exc:optional_failure("cinematic.nav_backdrop_schedule",exc)
        if not getattr(self,"_cin_cache_exclusive",False):
            try:
                self._nav_burst_until=time.monotonic()+0.64;self._grid_focus_timer.stop();self._grid_focus_timer.start(650,True)
                if self.media_type=="series":self._series_prefetch_timer.stop();self._series_prefetch_timer.start(900,True)
            except Exception:self._apply_debounced_grid_focus()
        self._update_page_counter()

    def _cin_set_adaptive_target(self,item):
        """Make the current selection the sole owner of future adaptive work.

        A running Pillow job cannot be force-killed safely on Enigma2, so the
        generation/event pair makes it cooperative: queued work is cancelled, a
        running stale job is prevented from starting its next expensive phase,
        and any result that still arrives is rejected before GUI commit.  The
        last committed adaptive is deliberately NOT cleared here.
        """
        try:sig=str(self._page_visual_key(item) or "")
        except Exception:sig=""
        if sig==str(getattr(self,"_cin_adaptive_target_sig","") or ""):
            return int(getattr(self,"_cin_adaptive_generation",0) or 0)
        self._cin_adaptive_generation=int(getattr(self,"_cin_adaptive_generation",0) or 0)+1
        self._cin_adaptive_target_sig=sig
        old_event=getattr(self,"_cin_adaptive_cancel",None)
        if old_event is not None:
            try:old_event.set()
            except Exception:pass
        old_future=getattr(self,"_cin_visual_future",None)
        if old_future is not None and not old_future.done():
            try:old_future.cancel()
            except Exception:pass
        self._cin_adaptive_cancel=threading.Event()
        self._cin_visual_future=None
        # Pending keys belong to obsolete generations.  Clearing them lets a
        # repeated/colliding provider key for the new selection schedule at once.
        try:self._cin_pending.clear()
        except Exception:pass
        return int(self._cin_adaptive_generation)

    def _update_selection(self):
        if not self.grid_items:return
        self.index=max(0,min(self.index,len(self.grid_items)-1));item=self.grid_items[self.index]
        self._cin_set_adaptive_target(item)
        self._cin_last_nav_at=time.monotonic()
        # First touch only the two rail rows involved in this key event. Their
        # tuples are RAM-cached by _cin_list_row, so the remote path performs no
        # metadata/HDD/Pillow work before moveToIndex().
        try:
            previous=getattr(self,"_cin_last_list_index",-1)
            previous=int(previous if previous is not None else -1)
            if 0<=previous<len(self.grid_items) and previous!=self.index:
                self["cin_list"].update_icon_row(previous,self._cin_list_row(previous,selected=False,record=self._cin_page_records.get(previous,{}),allow_progress_build=False))
            self["cin_list"].update_icon_row(self.index,self._cin_list_row(self.index,selected=True,record=self._cin_page_records.get(self.index,{}),allow_progress_build=False))
            self["cin_list"].moveToIndex(self.index)
            self._cin_last_list_index=int(self.index)
        except Exception as exc:optional_failure("cinematic.settings_list_selection",exc)
        self._remember_grid_state()
        # R225 Stage 3 correction: HOLD LAST GOOD adaptive during navigation.
        # The fixed-green master is cold-start/failure fallback only, never an
        # obligatory bridge between title A and title B.  Stable focus later
        # atomically swaps directly from A's complete adaptive to B's complete
        # adaptive; skipped titles perform zero adaptive repaint/build work.
        try:
            self._cin_nav_adaptive_pending_sig=str(self._page_visual_key(item) or "")
            self._cin_nav_adaptive_timer.stop()
        except Exception as exc:optional_failure("cinematic.nav_adaptive_hold",exc)
        # Navigation path is deliberately UI-only. Never enqueue skipped titles,
        # reprioritize artwork workers, scan HDD manifests or start network work.
        self._cin_cancel_focus(enqueue=False);self._cin_cancel_background();self._cin_last_focus_item=dict(item)
        # Arrow/key-repeat path must stay UI-only. Keep the previously committed
        # information visible and let the 160 ms fast-info timer paint the new
        # cached record after the key burst settles. _cin_arm_fast_info() owns the
        # one required fast-info cancellation; avoid cancelling the same future twice.
        self._cin_arm_fast_info()
        # Warm-cache safety: never decode a full-HD backdrop on every remote
        # repeat event. Restart one short one-shot timer instead; the highlight
        # and list movement stay immediate while the final cached backdrop binds
        # after the user releases the arrow key.
        if not getattr(self,"_cin_cache_exclusive",False):
            try:
                # R53: keep zero decoder work during key-repeat. Decoder-ready
                # cached JPEGs bind after a short quiet-window; PNG/fallback
                # surfaces retain the conservative delay.
                _bd_delay=self._cin_cached_backdrop_delay_ms()
                self._cin_nav_backdrop_timer.stop();self._cin_nav_backdrop_timer.start(int(_bd_delay),True)
            except Exception as exc:optional_failure("cinematic.nav_backdrop_schedule",exc)
        if not getattr(self,"_cin_cache_exclusive",False):
            try:
                self._nav_burst_until=time.monotonic()+0.64;self._grid_focus_timer.stop();self._grid_focus_timer.start(650,True)
                if self.media_type=="series":self._series_prefetch_timer.stop();self._series_prefetch_timer.start(900,True)
            except Exception:self._apply_debounced_grid_focus()
        self._update_page_counter()

    def open_selected(self):
        """Movies play directly; Series keep the existing Details/Episodes route."""
        if self._busy or not self.grid_items:return
        if self.media_type!="vod":
            return PremiumGridBase.open_selected(self)
        item=self.grid_items[self.index]
        command=item.get("cmd") or item.get("command") or item.get("url")
        if not command:
            try:self["status"].setText(_("No stream command"))
            except Exception:pass
            return
        try:self["status"].setText(_("Creating stream link..."))
        except Exception:pass
        selected_page=int(self.page or 1);selected_index=int(self.index or 0)
        def ok(url):
            url=url if isinstance(url,str) else ""
            name=str(item.get("name") or item.get("title") or "Ultra Stalker stream")
            try:
                engine=_configured_playback_engine()
                play_item=dict(item) if isinstance(item,dict) else item
                if isinstance(play_item,dict):
                    try:
                        # R111 speed: page records were already loaded while painting the
                        # rail. Avoid rescanning manifest/detail JSON on every PLAY press.
                        row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index),{}) or {}
                        locked=(row.get("_locked_tmdb_id") or row.get("tmdb_id") or play_item.get("_locked_tmdb_id") or play_item.get("tmdb_id")) if isinstance(row,dict) else (play_item.get("_locked_tmdb_id") or play_item.get("tmdb_id"))
                        if locked in (None,""):
                            fallback=self._record(item) or {}
                            locked=(fallback.get("_locked_tmdb_id") or fallback.get("tmdb_id")) if isinstance(fallback,dict) else None
                        if locked not in (None,""):
                            play_item["_locked_tmdb_id"]=locked;play_item["tmdb_id"]=locked
                        authority=str(getattr(self,"_cin_logo_path","") or "")
                        if authority and os.path.isfile(authority) and os.path.getsize(authority)>256:
                            play_item["_player_title_logo_cinematic_source"]=authority
                    except Exception as exc:optional_failure("cinematic.direct_player_logo_handoff",exc)
                    try:
                        visible_poster=self._visible_player_poster_path(item,self.index)
                        if visible_poster:
                            play_item["_player_poster"]=visible_poster
                            play_item["_ultra_poster_source"]=visible_poster
                            play_item["_adaptive_source_local"]=visible_poster
                            _poster_key=self._page_visual_key(item) if isinstance(item,dict) else ""
                            if _poster_key:play_item["_player_poster_identity"]=_poster_key
                    except Exception as exc:optional_failure("cinematic.direct_player_poster_handoff",exc)
                payload=_player_payload(play_item,self.profile,media_type="vod")
                payload["_player_client_ref"]=self.client
                def closed(result=None):
                    try:self["status"].setText("")
                    except Exception:pass
                    # Player persists resume position. Reload only lightweight content state
                    # and repaint the same rail; no Details screen/artwork cycle is involved.
                    try:
                        states=load_content_states(self.profile,self.media_type,self.grid_items) if self.grid_items else []
                        qualities=load_content_qualities(self.profile,self.media_type,self.grid_items) if self.grid_items else []
                        self._grid_item_state={}
                        for it,st,q in zip(self.grid_items,states,qualities):
                            rr=dict(st or {});rr["quality"]=q or "";self._grid_item_state[id(it)]=rr
                    except Exception as exc:optional_failure("cinematic.direct_play_state_reload",exc)
                    if self.page!=selected_page or not self.grid_items:
                        self.load_page(selected_page,selected_index);return
                    self.index=max(0,min(selected_index,len(self.grid_items)-1))
                    try:self["status"].setText("")
                    except Exception:pass
                    try:self._render_grid()
                    except Exception as exc:optional_failure("cinematic.direct_play_repaint",exc)
                    try:self._update_selection()
                    except Exception:pass
                    # Player can invalidate native adaptive/backdrop surfaces while
                    # our cached path markers remain unchanged. Rebind the exact
                    # same cached files once; no image work or network refresh.
                    try:self._cin_force_cached_visual_rebind()
                    except Exception as exc:optional_failure("cinematic.direct_play_cached_rebind",exc)
                try:self["status"].setText("")
                except Exception:pass
                self.session.openWithCallback(closed,UltraStalkerPlayer,str(url).strip(),name,"vod",engine,payload)
            except Exception as exc:
                try:self["status"].setText(_("Player failed: %s")%exc)
                except Exception:pass
        self._run_async(lambda handle:self.client.create_link(item,"vod",cancel_event=handle.cancel_event),ok,lambda e:ok(""))

    def _cin_fast_backdrop_handoff(self,item,row):
        return self._cin_instant_cached_backdrop(item,row if isinstance(row,dict) else {})

    def _apply_debounced_grid_focus(self):
        if self._screen_closed or not self.grid_items:return
        # Exact R81 stable-focus law: skipped titles do zero work.  Re-arm until
        # navigation has been quiet for a full 600 ms window.
        try:
            quiet=max(0.0,time.monotonic()-float(getattr(self,"_cin_last_nav_at",0.0) or 0.0))
            if quiet < 0.60:
                self._grid_focus_timer.stop();self._grid_focus_timer.start(max(60,int((0.60-quiet)*1000)),True)
                return
        except Exception:
            return
        item=self.grid_items[self.index]
        row=self._cin_page_records.get(self.index,{}) or self._record(item) or {}
        self._cin_page_records[self.index]=row

        # --- R81 LEFT RAIL AUTHORITY ---
        # Apply the canonical R81 package rows immediately after stable focus,
        # regardless of whether Details metadata has resolved yet.  This restores
        # the exact R81 row/adaptive cadence: neutral/previous material while
        # moving, then the focused title's real poster-derived rows after pause.
        package=(getattr(self,"_cin_page_packages",{}) or {}).get(int(self.index)) or self._cin_load_package(item,row)
        adaptive_committed=False
        if package:
            try:self._cin_page_packages[int(self.index)]=dict(package)
            except Exception:pass
            adaptive_committed=bool(self._cin_apply_package(item,row,package))
        if not adaptive_committed:
            # Missing either half of the title presentation means HOLD the last
            # committed state and prepare BOTH halves off-thread.  Never let the
            # description chrome and rail colours arrive as two visible phases.
            try:self._cin_schedule_visuals(item,row)
            except Exception as exc:optional_failure("cinematic.r200_atomic_visual_schedule",exc)

        # Backdrop is an independent modern lane.  Calling it here is harmless
        # for an already-painted/cache hit and is the cold-title fallback after
        # the same R81 stable-focus window.
        if getattr(self,"_fast_bd_enabled",False):
            try:self._fast_bd_schedule(item,row)
            except Exception as exc:optional_failure("cinematic.r200_backdrop_focus",exc)

        # --- DETAILS INFORMATION AUTHORITY ---
        # The information box never gates the R81 rows.  It paints only the same
        # canonical Details record used by the real Details screen.
        try:
            from .details_authority import metadata_language_ready
            details_ready=bool(row.get("_details_authority_ready") and metadata_language_ready(row,(self._grid_settings or {})))
        except Exception:
            details_ready=bool(row.get("_details_authority_ready"))

        # Paint the information cluster immediately from the already-indexed HDD
        # record.  This is presentation-only and does not mark the record ready.
        # The authoritative focus resolve below will repaint the same widgets when
        # richer/correct-language Details metadata becomes available.
        self._cin_apply_record(item,row,allow_build=False,hold_missing=not details_ready)
        if details_ready:
            # Details chrome is loaded from the title's persisted visual bundle;
            # _cin_apply_package above already prefers that exact bundle and does
            # not manufacture a second information-box authority.
            return

        # Cold/incomplete title: resolve the same Details Authority once.
        self._cin_schedule_focus(item)

    def _cin_set_poll_interval(self,interval_ms,force=False):
        if self._screen_closed:return
        try:
            requested=int(interval_ms or 0)
            if requested<=0:
                self._cin_poll.stop();self._cin_poll_interval=0;return
            interval=max(80,requested)
            if not force and int(getattr(self,"_cin_poll_interval",0) or 0)==interval:return
            self._cin_poll.stop();self._cin_poll_interval=interval;self._cin_poll.start(interval,False)
        except Exception as exc:optional_failure("cinematic.poll_interval",exc)

    def _cin_poll_has_pending_work(self):
        if self._screen_closed:return False
        try:
            if not self._cin_jobs.empty():return True
        except Exception:pass
        # Stage 9: adaptive visual work is a first-class delivery source.  The
        # old poller ignored it, allowing a completed current-title adaptive to
        # remain stranded in _cin_jobs until some unrelated later event.
        try:
            if getattr(self,"_cin_pending",None):return True
        except Exception:pass
        visual_future=getattr(self,"_cin_visual_future",None)
        try:
            if visual_future is not None and not visual_future.done():return True
        except Exception:return True
        for future in (getattr(self,"_cin_fast_info_future",None),getattr(self,"_cin_focus_future",None),getattr(self,"_cin_background_future",None)):
            try:
                if future is not None and not future.done():return True
            except Exception:return True
        if getattr(self,"_cin_logo_pending",None):return True
        # Backdrop Grid 1/2 reuse this poller for their page-local adaptive HUD
        # and selector workers.  Keep delivery alive only while those jobs exist.
        if getattr(self,"_bd_hud_pending",None):return True
        if getattr(self,"_grid_accent_pending",None):return True
        try:
            if hasattr(self,"_bd_hud_jobs") and not self._bd_hud_jobs.empty():return True
        except Exception:pass
        return bool(self._async_poll_has_pending_work())

    def _cin_kick_poll(self):
        try:self._cin_set_poll_interval(120)
        except Exception:pass

    def _cin_poll_hdd(self):
        # Queue delivery only. No HDD scans, visible-row refresh, missed-title
        # recovery, prefetch or speculative work is permitted in the poll loop.
        if self._screen_closed:return
        self._cin_drain_jobs()
        self._cin_set_poll_interval(120 if self._cin_poll_has_pending_work() else 0)

    def _cin_refresh_one_visible_row(self):
        """Refresh one visible Settings-style row per HDD poll."""
        if not self.grid_items:return
        count=min(self.page_size,len(self.grid_items));pos=None
        for _ in range(count):
            candidate=int(self._cin_row_refresh_cursor or 0)%count;self._cin_row_refresh_cursor=candidate+1
            if candidate not in self._cin_row_complete:pos=candidate;break
        if pos is None:return
        try:
            item=self.grid_items[pos];row=self._record(item) or {}
            self["cin_list"].update_icon_row(pos,self._cin_list_row(pos,selected=(pos==self.index),record=row))
            if _library_readiness(row).get("complete"):self._cin_row_complete.add(pos)
        except Exception as exc:optional_failure("cinematic.row_refresh",exc)

    def _cin_fit_single_line_font(self, widget_name, text, max_size=42, min_size=9):
        """Conservative one-line fitter for very long titles/genres on OpenBH.

        OpenBH's calculateSize() can under-estimate mixed/Arabic glyph runs, so
        use a stronger width estimate first and only allow the native probe to
        reduce the font further.  The goal is simple: never let text escape its
        pill/title box, even for absurdly long provider/TMDB strings.
        """
        value=" ".join(str(text or "").replace("\n"," ").split()).strip()
        try:
            widget=self[widget_name];inst=widget.instance
            if inst is None:return
            width=max(70,int(inst.size().width())-36)
            try:
                if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            except Exception as exc:optional_failure("cinematic.title_nowrap",exc)
            units=0.0;has_ar=False;has_latin=False
            for ch in value:
                code=ord(ch)
                if 0x0600<=code<=0x06ff:
                    has_ar=True;units+=0.86
                elif "A"<=ch<="Z":
                    has_latin=True;units+=0.78
                elif "a"<=ch<="z":
                    has_latin=True;units+=0.66
                elif ch.isdigit():units+=0.64
                elif ch.isspace():units+=0.38
                elif ch in ".,:;!?\'`-_/()[]{}":units+=0.42
                else:units+=0.72
            safety=1.34 if has_ar and has_latin else (1.28 if has_ar else 1.22)
            units=max(1.0,units*safety)
            estimated=int(float(width)/units)
            chosen=max(int(min_size),min(int(max_size),estimated))
            for size in range(chosen,int(min_size)-1,-1):
                try:
                    probe=eLabel();probe.setFont(gFont("Regular",size));probe.setText(value)
                    native=int(probe.calculateSize().width())
                except Exception:native=0
                # Keep a 10% OpenBH shaping safety margin.  A zero probe is not
                # treated as proof that the text fits; the conservative estimate
                # above remains authoritative.
                if native>0 and int(native*1.10)>width:
                    chosen=max(int(min_size),size-1);continue
                chosen=size;break
            inst.setFont(gFont("Regular",chosen))
        except Exception as exc:optional_failure("cinematic.title_adaptive_font",exc)

    def _cin_fit_compact_text(self, widget_name, text=None, max_size=22, min_size=8, padding=6):
        """Keep every compact Cinematic metadata value on one adaptive line.

        Short values retain the generous native size. Longer/localized values
        shrink only enough to stay inside the existing pill. This changes text
        rendering only; geometry, metadata and provider/TMDB values are untouched.
        """
        try:
            widget=self[widget_name];inst=widget.instance
            if inst is None:return
            value=" ".join(str(widget.getText() if text is None else text or "").replace("\n"," ").split()).strip()
            if not value:return
            try:
                if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            except Exception:pass
            width=max(24,int(inst.size().width())-int(padding))
            units=0.0;has_ar=False
            for ch in value:
                code=ord(ch)
                if 0x0600<=code<=0x06ff:
                    has_ar=True;units+=0.80
                elif "A"<=ch<="Z":units+=0.72
                elif "a"<=ch<="z":units+=0.59
                elif ch.isdigit():units+=0.58
                elif ch.isspace():units+=0.34
                elif ch in ".,:;!?\'`-_/()[]{}•":units+=0.36
                else:units+=0.68
            safety=1.17 if has_ar else 1.10
            estimated=max(int(min_size),min(int(max_size),int(float(width)/max(1.0,units*safety))))
            chosen=int(min_size)
            for size in range(int(max_size),int(min_size)-1,-1):
                if size>estimated+2:continue
                try:
                    probe=eLabel();probe.setFont(gFont("Regular",size));probe.setText(value)
                    native=int(probe.calculateSize().width())
                except Exception:native=0
                painted=max(native,int(units*size*safety)) if native>0 else int(units*size*safety)
                if painted<=width:
                    chosen=size;break
            inst.setFont(gFont("Regular",chosen))
        except Exception as exc:optional_failure("cinematic.compact_adaptive_font",exc)

    def _cin_fit_runtime_like_details(self):
        """Use the native Details compact-text fitter itself for seasons/runtime.

        This deliberately reuses the exact Details implementation instead of a
        Cinematic copy, so receiver font metrics and adaptive sizing stay identical.
        """
        try:
            from .ui_screens_details import ContentDetailsScreen
            ContentDetailsScreen._fit_compact_text(self,"runtime_text",max_size=24,min_size=18,padding=4)
        except Exception as exc:
            optional_failure("cinematic.runtime_details_fit",exc)

    def _cin_fit_description_font(self):
        """Exact Native Details description fitting principle for the larger Cinematic box."""
        try:
            value=str(self["description"].getText() or "").strip();widget=self["description"]
            if widget.instance is None:return
            width=max(200,int(widget.instance.size().width()));height=max(40,int(widget.instance.size().height()))
            is_ar=bool(re.search(r"[\u0600-\u06ff]",value));factor=0.62 if is_ar else 0.55
            chosen=16
            for size in range(26,15,-1):
                chars_per_line=max(12,int(width/max(1.0,size*factor)))
                weighted=len(value)+value.count("\n")*chars_per_line
                lines=max(1,int(math.ceil(float(weighted)/float(chars_per_line))))
                if lines*int(size*1.34)<=height:
                    chosen=size;break
            widget.instance.setFont(gFont("Regular",chosen))
        except Exception as exc:optional_failure("cinematic.description_adaptive_font",exc)

    def _cin_apply_record(self,item,row,allow_build=False,hold_missing=False):
        """Paint Details metadata without ever blanking a field mid-navigation.

        ``hold_missing`` is used by the arrow/fast-info lane.  Any value already
        visible remains resident until the new title supplies that field.  Once
        the authoritative Details snapshot is ready, normal painting may clear a
        field that TMDb genuinely does not provide.
        """
        row=dict(row or {});new_key=self._record_key(row,item);identity_changed=bool(new_key and new_key!=self._cin_record_key)
        self._cin_last_record=row;self._cin_record_key=new_key
        if identity_changed:
            # Poster/backdrop hold-last-good is owned by their own lanes.
            pass

        def _set_text(name,value,fit=None):
            text=str(value or "")
            if text or not hold_missing:
                try:
                    widget=self[name]
                    current=str(widget.getText() or "")
                    if current==text:return False
                    widget.setText(text)
                except Exception:return False
                if fit:
                    try:fit(text)
                    except Exception:pass
                return True
            return False

        title=self._rail_title(item,row) or str(row.get("title") or row.get("name") or "")
        display_title=title[:120]
        self._cin_show_title_logo(item,row)
        # Adaptive colour is committed only by _cin_commit_adaptive_presentation.
        # Metadata repaint must never recolour the description box independently.
        _set_text("name",display_title,lambda text:self._cin_fit_single_line_font("name",text,38,9))
        try:self["cin_list"].update_icon_row(self.index,self._cin_list_row(self.index,selected=True,record=row))
        except Exception:pass

        genres=row.get("genres") or row.get("genre") or []
        if isinstance(genres,str):genres=[genres]
        genre_names=[]
        for value in genres[:3]:
            if isinstance(value,dict):value=value.get("name") or ""
            value=str(value or "").strip()
            if value:genre_names.append(value)
        genre_value=" / ".join(genre_names)[:78]
        _set_text("genre_text",genre_value,lambda text:self._cin_fit_single_line_font("genre_text",text,25,12))
        age_value=_age_rating_display(row.get("certification") or row.get("age_rating"))
        _set_text("age_rating_text",age_value)

        cast=row.get("cast") or row.get("actors") or []
        if isinstance(cast,str):cast=[x.strip() for x in re.split(r"[,،|]",cast) if x.strip()]
        names=[]
        for actor in cast[:8]:
            if isinstance(actor,dict):actor=actor.get("name") or actor.get("original_name") or ""
            actor=str(actor or "").strip()
            if actor and actor not in names:names.append(actor)
            if len(names)>=6:break
        cast_value=", ".join(names)[:220]
        _set_text("cast_text",cast_value,lambda text:self._cin_fit_single_line_font("cast_text",text,18,11))

        details_overview="";details_overview_ready=False
        try:
            from .details_authority import details_overview
            details_overview,details_overview_ready=details_overview(self.profile,self.media_type,item,row)
        except Exception as exc:optional_failure("cinematic.details_overview_authority",exc)
        overview=str(details_overview if details_overview_ready else (row.get("overview") or "")).strip()
        overview_value=_strip_arabic_tashkeel(overview[:1800])
        if _set_text("description",overview_value):
            try:self._cin_fit_description_font()
            except Exception:pass

        # Quality icons are evidence-only.  The catalogue/provider title may say
        # HD/SD, but that is not proof of the actual stream.  Only the persisted
        # runtime quality learned by the player may paint a quality icon; when no
        # learned value exists the HUD says AUTO and no stale icon is retained.
        try:
            _quality_state=(getattr(self,"_grid_item_state",{}) or {}).get(id(item),{}) or {}
            q=str(_quality_state.get("quality") or "").strip()
        except Exception:
            q=""
        q=q or _("AUTO")
        year=str(row.get("year") or "")
        if self.media_type=="series":
            seasons=row.get("number_of_seasons") or 0
            try:seasons=int(seasons or 0)
            except Exception:seasons=0
            runtime=((_("%d season")%seasons) if seasons==1 else ((_("%d seasons")%seasons) if seasons>1 else ""))
        else:
            runtime_value=row.get("runtime")
            try:runtime_value=int(float(runtime_value or 0))
            except Exception:runtime_value=0
            runtime=((_("%s min")%runtime_value) if runtime_value else "")

        _set_text("quality_text",q[:10],lambda text:self._cin_fit_compact_text("quality_text",text,max_size=20,min_size=9,padding=8))
        _set_text("year_text",year[:8],lambda text:self._cin_fit_compact_text("year_text",text,max_size=22,min_size=10,padding=4))
        if runtime or not hold_missing:
            _set_text("runtime_text",runtime[:14])
            if self.media_type=="series":
                try:self._cin_fit_runtime_like_details()
                except Exception:pass
            else:
                try:self._cin_fit_compact_text("runtime_text",runtime[:14],max_size=24,min_size=15,padding=2)
                except Exception:pass

        quality_asset=_quality_asset_name(q) if str(q or "").upper()!=str(_("AUTO")).upper() else None
        if quality_asset:
            self._set_native_pixmap("quality_logo",quality_asset)
            try:self["quality_text"].setText("")
            except Exception:pass
        else:
            # AUTO/unknown must never inherit the previous title's SD/HD badge.
            try:self["quality_logo"].hide()
            except Exception:pass
            try:
                if self["quality_text"].getText()!=str(q or _("AUTO"))[:10]:
                    self["quality_text"].setText(str(q or _("AUTO"))[:10])
            except Exception:pass

        runtime_shell=str((getattr(self,"_cin_last_chrome",{}) or {}).get("runtime") or "")
        if not (runtime_shell and os.path.isfile(runtime_shell)):
            runtime_shell=str((self._cin_fixed_master_chrome() or {}).get("runtime") or "")
        if runtime:
            if runtime_shell:self._set_native_pixmap("runtime_pill_bg",runtime_shell)
            self._set_native_pixmap("runtime_icon","us166_details_folder_yellow_32.png" if self.media_type=="series" else "us173_movie_runtime_32_icononly.png")
        elif not hold_missing:
            try:self["runtime_icon"].hide()
            except Exception:pass
            if runtime_shell:self._set_native_pixmap("runtime_pill_bg",runtime_shell)

        countries=row.get("countries") or row.get("country") or []
        raw_country=countries[0] if isinstance(countries,(list,tuple)) and countries else countries
        code=_country_code(raw_country);country_value=_normalized_country(raw_country)
        if country_value or not hold_missing:
            _set_text("country_text",country_value,lambda text:self._cin_fit_compact_text("country_text",text,max_size=20,min_size=8,padding=6))
            self._set_native_pixmap("country_flag","flags_iso/%s.png"%code.lower() if code else None)
        if allow_build and row:self._cin_schedule_visuals(item,row)

    def _cin_schedule_visuals(self,item,row):
        """Prepare adaptive material for the latest settled Cinematic title only.

        Stage 9 fixes two independent stalls from the old lane:
        1) visual work was not part of the poll heartbeat, so a completed result
           could sit in ``_cin_jobs`` until some unrelated later action woke it;
        2) queued/running work had no selection generation, so an old title could
           occupy the single image lane while the user had already moved on.

        Work is now generation-owned and cooperatively cancellable between the
        row and Details-chrome phases.  Last-good adaptive surfaces remain on the
        GUI until the current generation has both complete halves ready.
        """
        row=dict(row or {})
        key=self._record_key(row,item)
        if not key:return
        generation=int(self._cin_set_adaptive_target(item) or 0)
        target_sig=str(getattr(self,"_cin_adaptive_target_sig","") or "")
        if not target_sig:return
        token="%d|%s"%(generation,key)
        if token in self._cin_pending:return
        self._cin_pending.add(token)
        index=int(self.index)
        backdrop=str(row.get("backdrop_local") or "")
        accent=str(row.get("adaptive_accent") or row.get("adaptive_primary") or "#c99141")
        fingerprint=str(row.get("adaptive_fingerprint") or "")
        source=str(row.get("poster_local") or "")
        if not (source and os.path.isfile(source)):source=backdrop
        if not fingerprint and source and os.path.isfile(source):
            try:
                _st=os.stat(source);fingerprint="%s:%s"%(int(_st.st_mtime),int(_st.st_size))
            except Exception:fingerprint=""
        source_sig="%s|%s"%(source,fingerprint)
        cancel_event=getattr(self,"_cin_adaptive_cancel",None)
        if cancel_event is None:
            cancel_event=threading.Event();self._cin_adaptive_cancel=cancel_event
        owner_ref=weakref.ref(self)
        item_snapshot=dict(item or {}) if isinstance(item,dict) else item
        profile=self.profile;media_type=self.media_type

        def stale():
            owner=owner_ref()
            if owner is None or cancel_event.is_set():return True
            try:
                if owner._screen_closed:return True
                if int(getattr(owner,"_cin_adaptive_generation",0) or 0)!=generation:return True
                if str(getattr(owner,"_cin_adaptive_target_sig","") or "")!=target_sig:return True
            except Exception:return True
            return False

        def chrome_ready(chrome):
            chrome=chrome if isinstance(chrome,dict) else {}
            required=("panel_detail","overview_detail","cast_detail","quality","year","runtime","country","genre_detail")
            try:return all(str(chrome.get(name) or "") and os.path.isfile(str(chrome.get(name) or "")) for name in required)
            except Exception:return False

        def worker():
            if stale():return False
            result={"kind":"adaptive_visual","key":key,"pending_token":token,"index":index,"row":row,
                    "backdrop":"","chrome":{},"settings_rows":{},"adaptive_generation":generation,
                    "target_sig":target_sig,"source_sig":source_sig}
            try:
                root=os.path.join(GENERATED,"cinematic",key);ensure_persistent_dirs(root)
                if stale():return False
                # Reuse persisted Details-family chrome before manufacturing any
                # new Pillow surfaces. This is the cheapest and most consistent
                # path when Details/BLUE already completed this title.
                try:
                    bundle=_load_visual_bundle(profile,media_type,item_snapshot,row) or {}
                    cached_chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
                    if chrome_ready(cached_chrome):result["chrome"]=dict(cached_chrome)
                except Exception as exc:optional_failure("cinematic.adaptive_bundle_fast_load",exc)
                if stale():return False
                if source and os.path.isfile(source):
                    settings_key=canonical_dynamic_rows_key(source,selected_rim_only=True,cinematic_premium=True) or "cin89_reference_rows_%s_%s"%(key,hashlib.sha1((source+fingerprint).encode("utf-8","ignore")).hexdigest()[:14])
                    result["settings_rows"]=_build_dynamic_settings_episode_rows(source,settings_key,selected_rim_only=True,cinematic_premium=True) or {}
                    # Navigation during row generation invalidates this title
                    # before the much heavier Details-chrome phase can begin.
                    if stale():return False
                    if not chrome_ready(result.get("chrome")):
                        chrome_key=canonical_dynamic_details_key(source) or "cin534_focus_%s_%s"%(key,hashlib.sha1((source+fingerprint).encode("utf-8","ignore")).hexdigest()[:14])
                        result["chrome"]=_build_dynamic_details_chrome(source,chrome_key) or {}
                if stale():return False
            except Exception as exc:
                optional_failure("cinematic.visual_worker",exc)
                if stale():return False
            self._cin_jobs.put(result)
            return True

        try:
            future=getattr(self,"_cin_visual_executor",_GRID_ACCENT_EXECUTOR).submit(
                worker,_task_key="cin-visual:%x"%id(self),_replace_task_key=True
            )
            self._cin_visual_future=future
            def completed(done,pending_token=token,ref=owner_ref):
                owner=ref()
                if owner is None:return
                queued=False
                try:queued=(not done.cancelled()) and bool(done.result())
                except Exception:queued=False
                if not queued:
                    try:owner._cin_pending.discard(pending_token)
                    except Exception:pass
                try:
                    if getattr(owner,"_cin_visual_future",None) is done:owner._cin_visual_future=None
                except Exception:pass
            future.add_done_callback(completed)
            # Critical Stage-9 fix: keep the GUI delivery heartbeat alive for
            # this visual future; previously ready adaptive results could wait in
            # the queue for many subsequent titles before anything woke polling.
            self._cin_kick_poll()
        except Exception as exc:
            self._cin_pending.discard(token);self._cin_visual_future=None
            optional_failure("cinematic.visual_submit",exc)

    def _cin_drain_jobs(self):
        _budget=2
        while _budget>0:
            try:r=self._cin_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            _budget-=1
            kind=str(r.get("kind") or "")
            if kind=="hidden_done":continue
            if kind=="fast_backdrop_meta":
                sig=str(r.get("sig") or "");self._fast_bd_meta_pending.discard(sig)
                if sig==str(getattr(self,"_fast_bd_meta_latest_sig","") or ""):self._fast_bd_meta_latest_sig=""
                if self._screen_closed or not self.grid_items or sig!=self._fast_bd_current_sig():continue
                current=self.grid_items[self.index];canonical=self._cin_page_records.get(int(self.index),{}) or self._record(current) or {}
                resolved=r.get("row") if isinstance(r.get("row"),dict) else {}
                # Direct TMDb helper is permitted to contribute CLEAN BACKDROP identity only.
                # Descriptive metadata is deliberately discarded; Details Authority owns it.
                keys=("tmdb_id","media_type","backdrop_local","backdrop_url","_backdrop_url","tmdb_backdrop_url","backdrop_path","clean_backdrop_choice","clean_backdrop_iso","clean_backdrop_selector_version","identity_verified")
                candidate=dict(canonical)
                for key in keys:
                    value=resolved.get(key)
                    if value not in (None,"",[],{}):candidate[key]=value
                try:self._fast_bd_schedule(current,candidate)
                except Exception as exc:optional_failure("cinematic.r199_direct_backdrop_meta",exc)
                continue
            if kind=="title_logo":
                sig=str(r.get("sig") or "");self._cin_logo_pending.discard(sig)
                if self._screen_closed or not self.grid_items:continue
                current=self.grid_items[self.index];row=self._record(current) or {};current_sig,_unused=self._cin_title_logo_signature(current,row)
                if sig!=current_sig:continue
                path=str(r.get("path") or "")
                try:
                    if path and os.path.isfile(path) and self["title_logo"].instance is not None:
                        self["title_logo"].instance.setPixmapFromFile(path);self._cin_logo_path=path;self["title_logo"].show()
                    elif bool(r.get("missing")):
                        # Current item is confirmed logo-less. Cinematic has its name
                        # in the list already, so retire the stale logo with no text.
                        self["title_logo"].hide();self._cin_logo_path=""
                    else:
                        held=str(getattr(self,"_cin_logo_path","") or "")
                        if held and os.path.isfile(held) and os.path.getsize(held)>256:self["title_logo"].show()
                        else:self["title_logo"].hide();self._cin_logo_path=""
                except Exception as exc:optional_failure("cinematic.title_logo_apply",exc)
                continue
            if kind=="fast_info":
                self._cin_fast_info_future=None;self._cin_fast_info_cancel=None;self._cin_fast_info_sig=""
                if self._screen_closed or not self.grid_items:continue
                current=self.grid_items[self.index];sig=self._page_visual_key(current)
                if str(r.get("sig") or "")!=sig:continue
                row=r.get("row") if isinstance(r.get("row"),dict) else {}
                if row:
                    try:self._cin_page_records[int(self.index)]=dict(row)
                    except Exception:pass
                    try:
                        from .details_authority import metadata_language_ready
                        ready=metadata_language_ready(row,(self._grid_settings or {}))
                    except Exception:ready=bool(row.get("_details_authority_ready"))
                    try:self._cin_apply_record(current,row,allow_build=False,hold_missing=not ready)
                    except TypeError:self._cin_apply_record(current,row,allow_build=False)
                    except Exception as exc:optional_failure("cinematic.fast_info_apply",exc)
                continue
            if kind=="focus":
                if self._screen_closed or not self.grid_items:continue
                current=self.grid_items[self.index];sig=self._page_visual_key(current)
                if str(r.get("sig") or "")!=sig:continue
                row=r.get("row") if isinstance(r.get("row"),dict) else (self._record(current) or {})
                package=r.get("package") if isinstance(r.get("package"),dict) else {}
                try:self._cin_page_records[int(self.index)]=dict(row)
                except Exception:pass
                try:
                    poster=str(row.get("poster_local") or "");backdrop=str(row.get("backdrop_local") or "")
                    if poster and os.path.isfile(poster):
                        current["_cin_provider_poster_local"]=poster;current["_ultra_palette_source"]=poster;current["_ultra_poster_source"]=poster;current["_player_poster"]=poster;current["_adaptive_source_local"]=poster;self._grid_palette_sources[self.index]=poster
                    if backdrop and os.path.isfile(backdrop):current["_cin_provider_backdrop_local"]=backdrop;current["_backdrop_source_local"]=backdrop
                except Exception as exc:optional_failure("cinematic.focus_handoff",exc)
                try:
                    from .details_authority import metadata_language_ready
                    _focus_info_ready=metadata_language_ready(row,(self._grid_settings or {}))
                except Exception:_focus_info_ready=bool(row.get("_details_authority_ready"))
                self._cin_apply_record(current,row,allow_build=False,hold_missing=not _focus_info_ready)
                adaptive_committed=False
                if package:
                    try:self._cin_page_packages[int(self.index)]=dict(package)
                    except Exception:pass
                    adaptive_committed=bool(self._cin_apply_package(current,row,package))
                if not adaptive_committed:
                    try:self._cin_schedule_visuals(current,row)
                    except Exception as exc:optional_failure("cinematic.atomic_focus_adaptive_schedule",exc)
                if getattr(self,"_fast_bd_enabled",False):
                    try:self._fast_bd_schedule(current,row)
                    except Exception as exc:optional_failure("cinematic.r199_focus_backdrop_rebind",exc)
                continue
            if kind=="adaptive_visual":
                pending_token=str(r.get("pending_token") or "")
                if pending_token:self._cin_pending.discard(pending_token)
                if self._screen_closed or not self.grid_items:continue
                generation=int(r.get("adaptive_generation") or -1)
                target_sig=str(r.get("target_sig") or "")
                if generation!=int(getattr(self,"_cin_adaptive_generation",0) or 0):continue
                if target_sig!=str(getattr(self,"_cin_adaptive_target_sig","") or ""):continue
                item=self.grid_items[self.index]
                try:current_sig=str(self._page_visual_key(item) or "")
                except Exception:current_sig=""
                if not target_sig or target_sig!=current_sig:continue
                row=self._cin_page_records.get(int(self.index),{}) or self._record(item) or {}
                key=self._record_key(row,item)
                if str(r.get("key") or "")!=key:continue
                # Canonical artwork can be corrected while the same title stays
                # selected.  Never commit adaptive material sampled from the old
                # provisional poster/backdrop; reschedule against the new source.
                current_source=str((row or {}).get("poster_local") or "")
                if not (current_source and os.path.isfile(current_source)):
                    current_source=str((row or {}).get("backdrop_local") or "")
                current_fp=str((row or {}).get("adaptive_fingerprint") or "")
                if not current_fp and current_source and os.path.isfile(current_source):
                    try:
                        _st=os.stat(current_source);current_fp="%s:%s"%(int(_st.st_mtime),int(_st.st_size))
                    except Exception:current_fp=""
                current_source_sig="%s|%s"%(current_source,current_fp)
                if str(r.get("source_sig") or "")!=current_source_sig:
                    try:self._cin_schedule_visuals(item,row)
                    except Exception as exc:optional_failure("cinematic.adaptive_source_refresh",exc)
                    continue
                settings_rows=r.get("settings_rows") if isinstance(r.get("settings_rows"),dict) else {}
                chrome=r.get("chrome") if isinstance(r.get("chrome"),dict) else {}
                if chrome:
                    try:
                        poster=str((row or {}).get("poster_local") or "")
                        if poster and os.path.isfile(poster):_save_visual_bundle(self.profile,self.media_type,item,{"poster":poster,"chrome":chrome},snapshot=row)
                    except Exception as exc:optional_failure("cinematic.adaptive_bundle_repair_save",exc)
                try:
                    committed=self._cin_commit_adaptive_presentation(item,row,settings_rows,chrome,str((row or {}).get("adaptive_accent") or (row or {}).get("adaptive_primary") or ""))
                    if committed:self._cin_apply_record(item,row,allow_build=False,hold_missing=True)
                except Exception as exc:optional_failure("cinematic.atomic_visual_apply",exc)
                continue
            self._cin_pending.discard(str(r.get("key") or ""))
            if self._screen_closed or not self.grid_items:continue
            item=self.grid_items[self.index];row=self._record(item);key=self._record_key(row,item)
            if str(r.get("key") or "")!=key:continue
            settings_rows=r.get("settings_rows") if isinstance(r.get("settings_rows"),dict) else {}
            chrome=r.get("chrome") if isinstance(r.get("chrome"),dict) else {}
            if chrome:
                try:
                    poster=str((row or {}).get("poster_local") or "")
                    if poster and os.path.isfile(poster):_save_visual_bundle(self.profile,self.media_type,item,{"poster":poster,"chrome":chrome},snapshot=row)
                except Exception as exc:optional_failure("cinematic.adaptive_bundle_repair_save",exc)
            try:
                committed=self._cin_commit_adaptive_presentation(item,row,settings_rows,chrome,str((row or {}).get("adaptive_accent") or (row or {}).get("adaptive_primary") or ""))
                if committed:self._cin_apply_record(item,row,allow_build=False,hold_missing=True)
            except Exception as exc:optional_failure("cinematic.atomic_visual_apply",exc)

