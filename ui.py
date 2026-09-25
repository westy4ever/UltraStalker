# -*- coding: utf-8 -*-
import os
import queue
import threading
import weakref
import hashlib
import html
import urllib.parse
import urllib.request
import urllib.error
import time
import json

# R47: keep the proven R46 cold-start cyclic-GC guard, without profiler overhead.
import math
import re
import unicodedata
import io
import gc
# PERF46: Cold-start cyclic-GC guard. The R44 profiler proved that the ~255 ms
# pause was not module work: the same generation-2 collection simply moved from
# Downloads to Storage when Downloads became lazy.  Disable only *automatic*
# cyclic GC while ui.py is being constructed. Normal CPython ref-counting stays
# active. At ui_ready we do a cheap generation-0 flush and restore the receiver's
# original GC enabled/disabled state. Thresholds and long-term GC policy are not
# changed.
_IMPORT46_GC_WAS_ENABLED = False
try:
    _IMPORT46_GC_WAS_ENABLED = bool(gc.isenabled())
    if _IMPORT46_GC_WAS_ENABLED:
        gc.disable()
except Exception:
    pass

import tempfile
import shutil
try:
    import colorsys
except Exception:
    from . import compat_colorsys as colorsys
from collections import OrderedDict
from concurrent.futures import as_completed, ThreadPoolExecutor
from .core.executor import LazyThreadPoolExecutor, PriorityLazyExecutor
from .core.shared_executors import VISIBLE_ARTWORK_EXECUTOR, VISIBLE_PROVIDER_ARTWORK_EXECUTOR, CATALOGUE_EXECUTOR, METADATA_EXECUTOR, CACHE_IO_EXECUTOR
from .core.image_budget import image_work

try:
    from PIL import Image as _PILImage, ImageOps as _PILImageOps, ImageFilter as _PILImageFilter, ImageChops as _PILImageChops, ImageDraw
except Exception:
    _PILImage = None
    _PILImageOps = None
    _PILImageFilter = None
    _PILImageChops = None
    ImageDraw = None

from Screens.Screen import Screen
from skin import parseColor
from Screens.MessageBox import MessageBox
from Screens.InputBox import InputBox
from Components.Input import Input
from Screens.ChoiceBox import ChoiceBox
try:
    from Screens.VirtualKeyBoard import VirtualKeyBoard
except Exception:
    VirtualKeyBoard = None
try:
    from Screens.AudioSelection import AudioSelection
except Exception:
    AudioSelection = None
try:
    from Screens.SubtitleDisplay import SubtitleDisplay
except Exception:
    SubtitleDisplay = None
from Components.ActionMap import ActionMap
from Components.Label import Label
try:
    from Components.ScrollLabel import ScrollLabel
except Exception:
    ScrollLabel = Label
from Components.ProgressBar import ProgressBar
from Components.MenuList import MenuList
from Components.Pixmap import Pixmap
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
try:
    from Components.MultiContent import MultiContentEntryPixmapAlphaBlend
except Exception:
    MultiContentEntryPixmapAlphaBlend = MultiContentEntryPixmapAlphaTest
from enigma import (
    eTimer, eListboxPythonMultiContent, gFont, getDesktop, eLabel,
    RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_HALIGN_CENTER, RT_VALIGN_CENTER, loadPNG, ePicLoad, ePoint, eSize, eServiceReference, eServiceCenter, iServiceInformation
)

from . import _
from .client import StalkerClient
from .core.tasks import TASKS
# R112 startup/import cleanup: diagnostics, maintenance and backup are explicit
# utility actions, not first-paint dependencies.  Keep their public callables
# stable for the extracted screens, but import the heavy modules only on use.
def diagnostic_snapshot(*args, **kwargs):
    from .core.diagnostics import snapshot as _real
    return _real(*args, **kwargs)

def export_diagnostics(*args, **kwargs):
    from .core.diagnostics import export as _real
    return _real(*args, **kwargs)

def export_support_bundle(*args, **kwargs):
    from .core.diagnostics import export_support_bundle as _real
    return _real(*args, **kwargs)

def persistent_cache_stats(*args, **kwargs):
    from .core.maintenance import cache_stats as _real
    return _real(*args, **kwargs)

def prune_persistent_cache(*args, **kwargs):
    from .core.maintenance import prune_cache as _real
    return _real(*args, **kwargs)

def runtime_health(*args, **kwargs):
    from .core.maintenance import runtime_health as _real
    return _real(*args, **kwargs)

from .core.generated_cache import maintain_generated_cache, note_generated_use
from .core.runtime_log import breadcrumb as runtime_breadcrumb
from .core.session import PortalSession

def create_backup(*args, **kwargs):
    from .core.backup import create_backup as _real
    return _real(*args, **kwargs)

def restore_backup(*args, **kwargs):
    from .core.backup import restore_backup as _real
    return _real(*args, **kwargs)

def list_backups(*args, **kwargs):
    from .core.backup import list_backups as _real
    return _real(*args, **kwargs)

def inspect_backup(*args, **kwargs):
    from .core.backup import inspect_backup as _real
    return _real(*args, **kwargs)

from .core.portal_security import requires_http_consent, HTTP_WARNING, http_warning_for, mark_http_consent
from .ui_parts.catalog import clean_display_text as _clean_display_text, clean_live_channel_name as _clean_live_channel_name
from .ui_helpers import (
    ACCENT_NAMES,
    _search_key,
    _accent_for,
    _two_line_title,
    _letter_placeholder,
    _lift_dynamic_accent,
    _mix_rgb,
)
from .ui_artwork_helpers import (
    _image_url,
    _first_art_value,
    _backdrop_candidates,
    _backdrop_url,
    _strip_portal_artwork,
    _verified_external_art,
    _normalize_provider_image_url,
    _fit_live_picon_canvas,
    _fit_live_row_picon_canvas,
)
from .ui_dynamic_palette import (
    _DYNAMIC_PALETTE_CACHE,
    _DYNAMIC_PALETTE_CACHE_LOCK,
    _DYNAMIC_PALETTE_CACHE_LIMIT,
    _dynamic_palette,
)
from .title_clean import clean_title as _clean_catalog_title, display_title as _catalogue_title
from .ui_parts.settings import backup_choices, secret_backup_warning
# Recording and bouquet integration are used only after the user enters media
# flows or explicitly exports receiver integration.  Deferring these two large
# modules keeps them off the cold UI-graph import without changing their APIs.
def prepare_portal_recording(*args, **kwargs):
    from .core.recording import prepare_portal_recording as _real
    return _real(*args, **kwargs)

def install_recording_timer(*args, **kwargs):
    from .core.recording import install_recording_timer as _real
    return _real(*args, **kwargs)

def event_times(*args, **kwargs):
    from .core.recording import event_times as _real
    return _real(*args, **kwargs)

def export_live_integration(*args, **kwargs):
    from .core.bouquets import export_live_integration as _real
    return _real(*args, **kwargs)

def export_live_category_bouquet(*args, **kwargs):
    from .core.bouquets import export_live_category_bouquet as _real
    return _real(*args, **kwargs)

def export_receiver_items(*args, **kwargs):
    from .core.bouquets import export_receiver_items as _real
    return _real(*args, **kwargs)

def export_series_category_bouquets(*args, **kwargs):
    from .core.bouquets import export_series_category_bouquets as _real
    return _real(*args, **kwargs)

def unexport_live_integration(*args, **kwargs):
    from .core.bouquets import unexport_live_integration as _real
    return _real(*args, **kwargs)

def reload_bouquets(*args, **kwargs):
    from .core.bouquets import reload_bouquets as _real
    return _real(*args, **kwargs)

def start_proxy_server(*args, **kwargs):
    from .core.bouquets import start_proxy_server as _real
    return _real(*args, **kwargs)
from .core.parental import (
    is_unlocked as parental_is_unlocked, unlock as parental_unlock,
    lock_now as parental_lock_now, remaining_minutes as parental_remaining_minutes,
    verify_pin as parental_verify_pin, hash_pin as parental_hash_pin,
    pin_is_default as parental_pin_is_default, lockout_remaining as parental_lockout_remaining,
)
from .version import PLUGIN_VERSION, BUILD_NAME
from .receiver_video import capture_aspect_mode, restore_aspect_mode

# PERF38: the native player is one of the largest modules in the plugin and is
# irrelevant until playback actually starts.  Keep the historical dependency
# name injected into every screen, but resolve the real class only when
# Session.open()/openWithCallback() calls it.  This does not touch player code
# or playback behavior; it only moves the import off the plugin cold-start path.
def UltraStalkerPlayer(session, *args, **kwargs):
    from .services.player import UltraStalkerPlayer as _RealUltraStalkerPlayer
    return _RealUltraStalkerPlayer(session, *args, **kwargs)

def force_session_silence(*args, **kwargs):
    from .services.player import force_session_silence as _real_force_session_silence
    return _real_force_session_silence(*args, **kwargs)

# PERF45: Downloads are optional and must not participate in plugin cold start.
# Importing downloads.py was the point where a full generation-2 GC pause was
# being triggered (~255 ms on the test receiver).  Keep the historical globals
# injected into Details/Series, but resolve the real module only on first use.
class _LazyDownloadsFacade(object):
    def _manager(self):
        from .downloads import MANAGER as _MANAGER
        return _MANAGER
    def snapshot(self): return self._manager().snapshot()
    def add(self, job, resolver): return self._manager().add(job, resolver)
    def cancel(self, jid): return self._manager().cancel(jid)
    def retry(self, jid, resolver=None): return self._manager().retry(jid, resolver)
    def shutdown(self, wait=False, timeout=2.5): return self._manager().shutdown(wait=wait, timeout=timeout)

DOWNLOADS = _LazyDownloadsFacade()

def movie_job(*args, **kwargs):
    from .downloads import movie_job as _movie_job
    return _movie_job(*args, **kwargs)

def episode_job(*args, **kwargs):
    from .downloads import episode_job as _episode_job
    return _episode_job(*args, **kwargs)

from .tmdb import TMDBClient, TMDBError
from .artwork_v2 import ArtworkV2, load_manifest as load_artwork_v2_manifest, load_fast_local_poster as load_artwork_v2_fast_local_poster, load_manual_rescue_art, canonical_art_paths, identity_cache_compatible
from .persistent_cache import load_detail_snapshot, load_shared_detail_snapshot, load_detail_snapshot_by_tmdb, load_detail_snapshot_by_imdb, save_detail_snapshot, hdd_read_ready, canonical_external_digest, content_cache_key
# PERF41: IMDb support is optional metadata enrichment and should not make every
# plugin cold start parse the full IMDb/AWS adapter.  Preserve the historical
# dependency name injected into Details, but resolve the real implementation
# only on the first actual IMDb client construction.
def IMDbClient(*args, **kwargs):
    from .imdb import IMDbClient as _RealIMDbClient
    return _RealIMDbClient(*args, **kwargs)

from .log import get_logger, optional_failure, diagnostic_failure, redact as _redact_log_value
from .netsec import SafeMediaRedirectHandler, validate_remote_media_url, build_safe_media_opener, provider_urlopen
from .ultra import (premium_title, quality_badges, load_ui_state, save_ui_state,
    one_line, epg_summary, engine_memory_count, load_content_quality, load_content_qualities, normalize_quality, remember_content_quality)
from .storage import (load_profiles, save_profiles, disable_profiles, enable_profile, load_disabled_profiles, replace_profile, duplicate_profile, reorder_profile, permanently_delete_profile, purge_profile_data, load_theme, save_theme, THEMES, IMPORT_FILE, load_server_library, import_server_library_profiles,
    load_favorites, toggle_favorite, is_favorite, load_content_states, load_recently_played, load_continue_watching, add_recently_played, load_playback_progress, mark_watched, remove_from_history, clear_history, history_revision, load_settings, save_settings, save_tmdb_credential, update_api_keys, consume_recovery_notices)



class UltraSearchVirtualKeyBoard(VirtualKeyBoard if VirtualKeyBoard is not None else Screen):
    """OpenBH VKB with a real Arabic first view for Arabic locales.

    OpenBH builds Arabic by extending the English key list; stock setLocale()
    resets shiftLevel to 0, which is still English. Selecting Arabic therefore
    looks as if nothing happened. We select the first appended Arabic level.
    """
    def setLocale(self):
        if VirtualKeyBoard is None:return
        VirtualKeyBoard.setLocale(self)
        try:
            if str(getattr(self,"lang","")).lower().startswith("ar_") and len(getattr(self,"keyList",[]) or [])>2:
                self.shiftLevel=2
        except Exception as exc:
            diagnostic_failure("ui.failsoft.keyboard_locale",exc)

def _configured_playback_engine(settings=None, fallback=4097):
    """Return the single playback engine selected in Settings.

    Smart/per-title engine memory must never override the explicit user setting.
    """
    cfg=settings if isinstance(settings,dict) else load_settings()
    try:
        engine=int(cfg.get("service_type",fallback))
    except (TypeError,ValueError):
        try: engine=int(fallback)
        except (TypeError,ValueError): engine=4097
    return engine if engine in (1,4097,5001,5002,8193) else 4097



def _adaptive_poster_rgb(source_path):
    """Return a TV-visible accent sampled from the poster."""
    if not source_path or not os.path.isfile(source_path) or _PILImage is None:
        return (56, 180, 255)
    try:
        with _PILImage.open(source_path) as src:
            im=src.convert("RGB")
            sample=im.resize((24,32),getattr(getattr(_PILImage,"Resampling",_PILImage),"BILINEAR",2))
        scored=[]
        for r,g,b in sample.getdata():
            mx=max(r,g,b);mn=min(r,g,b);sat=mx-mn;lum=(r*3+g*6+b)//10
            if lum < 28 or lum > 242 or sat < 18:
                continue
            scored.append((sat*(90+min(lum,190)),r,g,b))
        if scored:
            scored.sort(reverse=True)
            top=scored[:max(8,min(40,len(scored)//3 or 8))]
            r=sum(x[1] for x in top)//len(top);g=sum(x[2] for x in top)//len(top);b=sum(x[3] for x in top)//len(top)
        else:
            r,g,b=(56,180,255)
        mx=max(r,g,b)
        if mx < 150:
            boost=150.0/max(1,mx);r=min(255,int(r*boost));g=min(255,int(g*boost));b=min(255,int(b*boost))
        return (r,g,b)
    except Exception as exc:
        optional_failure("ui.grid_adaptive_colour",exc)
        return (56,180,255)



def _grid_fit_card_title(text, max_width=226):
    """Fit a movie/series title into the footer without clipping.

    Uses Enigma2's own font metrics. Short titles keep the current 18px size;
    longer titles are balanced over at most two lines and shrink only as needed.
    """
    value=" ".join(str(text or "").replace("\n"," ").split()).strip()
    if not value:return "",18
    cache_key=(value,int(max_width))
    cached=_GRID_TITLE_FIT_CACHE.get(cache_key)
    if cached is not None:
        try:_GRID_TITLE_FIT_CACHE.move_to_end(cache_key)
        except Exception as exc:diagnostic_failure("ui.failsoft.203",exc)
        return cached
    words=value.split()
    for font_size in (18,17,16,15,14,13,12):
        try:
            if _settings_text_width_px(value,font_size)<=max_width:
                result=(value,font_size);_GRID_TITLE_FIT_CACHE[cache_key]=result;_GRID_TITLE_FIT_CACHE.move_to_end(cache_key)
                while len(_GRID_TITLE_FIT_CACHE)>_GRID_TITLE_FIT_CACHE_LIMIT:_GRID_TITLE_FIT_CACHE.popitem(last=False)
                return result
        except Exception as exc:
            diagnostic_failure("ui.failsoft.title_fit",exc)
        if len(words)>1:
            best=None
            for cut in range(1,len(words)):
                a=" ".join(words[:cut]);b=" ".join(words[cut:])
                try:
                    wa=_settings_text_width_px(a,font_size);wb=_settings_text_width_px(b,font_size)
                except Exception:
                    wa=len(a)*font_size*0.56;wb=len(b)*font_size*0.56
                score=max(wa,wb)
                if best is None or score<best[0]:best=(score,a,b)
            if best and best[0]<=max_width:
                result=(best[1]+"\n"+best[2],font_size);_GRID_TITLE_FIT_CACHE[cache_key]=result;_GRID_TITLE_FIT_CACHE.move_to_end(cache_key)
                while len(_GRID_TITLE_FIT_CACHE)>_GRID_TITLE_FIT_CACHE_LIMIT:_GRID_TITLE_FIT_CACHE.popitem(last=False)
                return result
    # Pathological titles: preserve the full title at the minimum readable size.
    # Enigma2 will wrap naturally if needed rather than silently truncating it.
    result=(value,12);_GRID_TITLE_FIT_CACHE[cache_key]=result;_GRID_TITLE_FIT_CACHE.move_to_end(cache_key)
    while len(_GRID_TITLE_FIT_CACHE)>_GRID_TITLE_FIT_CACHE_LIMIT:_GRID_TITLE_FIT_CACHE.popitem(last=False)
    return result

def _grid_card_chrome_from_poster(source_path, selected=False):
    """Build the adaptive full card frame + separate footer from the reference design."""
    if not source_path or not os.path.isfile(source_path) or _PILImage is None or ImageDraw is None:
        return None
    try:stamp=str(int(os.path.getmtime(source_path)))
    except Exception:stamp="0"
    variant="selected-clean" if selected else "normal"
    key=hashlib.sha1((source_path+"|"+stamp+"|us-card-full-v8-small-206x382|"+variant).encode("utf-8","ignore")).hexdigest()
    target=os.path.join(THUMB_CACHE_DIR,"cardfull_%s_206x382_v8_%s.png"%(key,variant))
    if os.path.isfile(target) and os.path.getsize(target)>100:return target
    try:
        primary,_secondary=_dynamic_palette(source_path)
        r,g,b=_lift_dynamic_accent(primary,0.48,0.46)
        canvas=_PILImage.new("RGBA",(206,382),(0,0,0,0))
        # 3D adaptive information tray.  The poster ends exactly at footer_top;
        # the tray is a separate bevel/glass block like the Portal/Movie cards.
        # Overlap the last two poster pixels so there is never a black seam
        # between the poster and the 3D information tray.
        footer_top=308
        draw=ImageDraw.Draw(canvas)
        for yy in range(footer_top,379):
            t=float(yy-footer_top)/69.0
            # brighter upper lip -> deep lower body -> subtle reflected bottom edge
            level=(0.58-0.31*t) if t < 0.76 else (0.29+0.13*(t-0.76)/0.24)
            rr=int(max(4,min(255,r*level)));gg=int(max(6,min(255,g*level)));bb=int(max(8,min(255,b*level)))
            draw.line((3,yy,202,yy),fill=(rr,gg,bb,246))
        # Seamless poster-to-info transition.  Do not draw a bright horizontal
        # top lip here: on the focused card it reads as an unwanted line running
        # through the inside of the adaptive frame.  Keep only subtle side/bottom
        # depth so the information tray still has volume without a separator.
        mid=(min(255,r+28),min(255,g+28),min(255,b+28),118)
        draw.line((7,377,198,377),fill=(r//3,g//3,b//3,190),width=2)
        draw.line((5,footer_top+2,5,374),fill=mid,width=1)
        draw.line((200,footer_top+2,200,374),fill=(max(3,r//5),max(3,g//5),max(3,b//5),185),width=1)
        # The normal card keeps its adaptive frame.  The focused card deliberately
        # omits this layer because the separate 266x398 selection laser already
        # supplies the visible focus edge; drawing both creates the unwanted
        # second/inner line seen inside the laser.
        if not selected:
            laser=_PILImage.new("RGBA",canvas.size,(0,0,0,0));ld=ImageDraw.Draw(laser)
            ld.rounded_rectangle((2,2,203,379),radius=15,outline=(r,g,b,128),width=3)
            if _PILImageFilter is not None:
                laser=laser.filter(_PILImageFilter.GaussianBlur(3))
            canvas=_PILImage.alpha_composite(canvas,laser);draw=ImageDraw.Draw(canvas)
            draw.rounded_rectangle((1,1,204,380),radius=15,outline=(min(255,r+18),min(255,g+18),min(255,b+18),236),width=2)
        _persistent_write_require(target);canvas.save(target,"PNG",optimize=True)
        return target
    except Exception as exc:
        optional_failure("ui.grid_card_chrome",exc);return None


def _grid_card_chrome_cached(source_path, selected=False):
    """Return an already-built card chrome path without any Pillow/palette work."""
    if not source_path or not os.path.isfile(source_path):
        return None
    try:stamp=str(int(os.path.getmtime(source_path)))
    except Exception:stamp="0"
    variant="selected-clean" if selected else "normal"
    key=hashlib.sha1((source_path+"|"+stamp+"|us-card-full-v8-small-206x382|"+variant).encode("utf-8","ignore")).hexdigest()
    target=os.path.join(THUMB_CACHE_DIR,"cardfull_%s_206x382_v8_%s.png"%(key,variant))
    return target if _valid_cache_file(target,ttl=0) else None


def _grid_selection_asset_from_poster(source_path):
    """Build a thick adaptive laser focus around the complete card."""
    if not source_path or not os.path.isfile(source_path) or _PILImage is None or ImageDraw is None:
        return None
    try:stamp=str(int(os.path.getmtime(source_path)))
    except Exception:stamp="0"
    # Test72 restores the thick TV-visible selector, but keeps it fully outside
    # the 206x382 card so the poster itself never shrinks and no inner frame is
    # drawn over the artwork.
    key=hashlib.sha1((source_path+"|"+stamp+"|us-card-select-v12-thickoutside-222x398").encode("utf-8","ignore")).hexdigest()
    cached=_GRID_ACCENT_CACHE.get(key)
    if cached and os.path.isfile(cached):
        try:_GRID_ACCENT_CACHE.move_to_end(key)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        return cached
    target=os.path.join(THUMB_CACHE_DIR,"selcard_%s_222x398_v12.png"%key)
    if os.path.isfile(target) and os.path.getsize(target)>100:
        _GRID_ACCENT_CACHE[key]=target;return target
    try:
        primary,_secondary=_dynamic_palette(source_path)
        r,g,b=_lift_dynamic_accent(primary,0.60,0.58)
        canvas=_PILImage.new("RGBA",(222,398),(0,0,0,0))

        # Thick outer halo to hide the tiny square card/poster edge remnants the
        # user highlighted, while staying outside the card footprint.
        glow=_PILImage.new("RGBA",canvas.size,(0,0,0,0));gd=ImageDraw.Draw(glow)
        gd.rounded_rectangle((7,7,214,390),radius=18,outline=(r,g,b,248),width=11)
        if _PILImageFilter is not None:
            glow=glow.filter(_PILImageFilter.GaussianBlur(7))
        canvas=_PILImage.alpha_composite(canvas,glow)

        # Secondary luminous band adds the strong thick look from the earlier
        # build without introducing an inner rail across the poster/footer join.
        glow2=_PILImage.new("RGBA",canvas.size,(0,0,0,0));g2=ImageDraw.Draw(glow2)
        g2.rounded_rectangle((8,8,213,389),radius=18,outline=(min(255,r+42),min(255,g+42),min(255,b+42),236),width=7)
        if _PILImageFilter is not None:
            glow2=glow2.filter(_PILImageFilter.GaussianBlur(3))
        canvas=_PILImage.alpha_composite(canvas,glow2)

        d=ImageDraw.Draw(canvas)
        core=(min(255,r+92),min(255,g+92),min(255,b+92),255)
        d.rounded_rectangle((8,8,213,389),radius=18,outline=core,width=4)

        _persistent_write_require(target);canvas.save(target,"PNG",optimize=True)
        _GRID_ACCENT_CACHE[key]=target
        try:_GRID_ACCENT_CACHE.move_to_end(key)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        while len(_GRID_ACCENT_CACHE)>_GRID_ACCENT_CACHE_LIMIT:
            try:_GRID_ACCENT_CACHE.popitem(last=False)
            except Exception:break
        return target
    except Exception as exc:
        optional_failure("ui.grid_adaptive_selector",exc);return None


def _grid_page_mood_from_poster(source_path):
    """Visible full-screen adaptive atmosphere from the selected HDD poster only.

    Test66 keeps one cache key shared with GridArtworkMixin and raises the light
    field slightly so the page itself remains visibly adaptive instead of looking
    permanently black while only the top HUD frames change colour.
    """
    if not source_path or not os.path.isfile(source_path) or _PILImage is None:
        return None
    try:
        st=os.stat(source_path);stamp="%s|%s"%(int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size))
    except Exception:stamp="0|0"
    key=hashlib.sha1((source_path+"|"+stamp+"|grid-page-mood-v8-stable").encode("utf-8","ignore")).hexdigest()
    target=os.path.join(THUMB_CACHE_DIR,"gridmood_%s_960x540_v8_stable.jpg"%key)
    if _valid_cache_file(target,ttl=0):return target
    if not _persistent_write_ok(THUMB_CACHE_DIR):return None
    temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        primary,secondary=_dynamic_palette(source_path)
        def _lift(rgb,floor=82):
            vals=[max(0,min(255,int(x))) for x in rgb[:3]]
            peak=max(vals) if vals else 0
            if peak<floor:
                scale=float(floor)/float(max(1,peak))
                vals=[min(255,int(v*scale)) for v in vals]
            return tuple(vals)
        primary=_lift(primary,86);secondary=_lift(secondary,72)
        w,h=960,540;base=(5,10,18)
        lw,lh=120,68;small=_PILImage.new("RGB",(lw,lh),base);px=small.load()
        pr,pg,pb=primary;sr,sg,sb=secondary
        for y in range(lh):
            ny=y/float(max(1,lh-1))
            for x in range(lw):
                nx=x/float(max(1,lw-1));dx=(nx-0.50)/0.66;dy=(ny-0.50)/0.64
                radial=max(0.0,1.0-(dx*dx+dy*dy));edge=max(0.0,1.0-abs(nx-0.5)*2.0)
                a=0.60*radial*(1.00-0.20*ny);bmix=0.22*edge*(1.0-0.24*ny)
                rr=int(base[0]*(1-a-bmix)+pr*a+sr*bmix);gg=int(base[1]*(1-a-bmix)+pg*a+sg*bmix);bb=int(base[2]*(1-a-bmix)+pb*a+sb*bmix)
                px[x,y]=(max(0,min(255,rr)),max(0,min(255,gg)),max(0,min(255,bb)))
        resampling=getattr(getattr(_PILImage,"Resampling",_PILImage),"BICUBIC",3);img=small.resize((w,h),resampling)
        if _PILImageFilter is not None:img=img.filter(_PILImageFilter.GaussianBlur(10))
        _persistent_write_require(temp);img.save(temp,"JPEG",quality=88,optimize=False,progressive=False)
        _persistent_write_require(target);os.replace(temp,target)
        return target if _valid_cache_file(target,ttl=0) else None
    except Exception as exc:
        optional_failure("ui.grid_page_mood",exc)
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as cleanup_exc:optional_failure("ui.grid_page_mood_cleanup",cleanup_exc)
        return None


def _looks_like_m3u_url(value):
    low=str(value or "").strip().lower()
    if not low:return False
    return (".m3u" in low or "type=m3u" in low or "output=m3u" in low or
            ("get.php" in low and ("username=" in low or "password=" in low)))

def _probe_m3u_url(value, timeout=8, cancel_event=None):
    """Return True when a URL actually serves an M3U playlist.

    Only a small prefix is read.  This is intentionally independent of the M3U
    adapter so adding a source does not pull the adapter into plugin startup.
    """
    url=str(value or "").strip()
    if not url:return False
    low=url.lower()
    if low.startswith("m3u://"):url="http://"+url[6:]
    elif low.startswith("m3u8://"):url="http://"+url[7:]
    if not url.lower().startswith(("http://","https://")):
        url="https://"+url
    req=urllib.request.Request(url,headers={
        "User-Agent":"UltraStalker/10",
        "Accept":"audio/x-mpegurl,application/vnd.apple.mpegurl,text/plain,*/*",
        "Range":"bytes=0-131071",
    })
    with provider_urlopen(req,timeout=max(3,int(timeout or 8))) as response:
        raw=response.read(131072)
    if cancel_event is not None and cancel_event.is_set():return False
    text=raw.decode("utf-8","replace").lstrip("\ufeff\r\n\t ")
    return text.startswith("#EXTM3U") or "#EXTINF" in text[:131072]

def _client_from_profile(profile, timeout=None):
    profile = profile if isinstance(profile, dict) else {}
    source_type=str(profile.get("source_type") or ("m3u" if _looks_like_m3u_url(profile.get("portal")) else "stalker")).lower()
    if source_type=="m3u":
        from .m3u_adapter import M3UClient
        return M3UClient(profile.get("portal"),profile.get("mac") or "M3U",
                         timeout=timeout if timeout is not None else load_settings().get("timeout",10))
    return StalkerClient(
        profile.get("portal"), profile.get("mac"),
        timeout=timeout if timeout is not None else load_settings().get("timeout", 10),
        allow_http_fallback=profile.get("allow_http_fallback", False),
        tls_mode=profile.get("tls_mode", "auto"),
        device_profile=profile.get("device_profile", "auto"),
        allow_tls_fallback=profile.get("tls_fallback_accepted", False),
        http_fallback_accepted=profile.get("http_fallback_accepted", False),
    )

def _validate_profile_client(profile):
    """Validate portal/MAC construction without leaking a keep-alive transport."""
    probe = _client_from_profile(profile)
    try:
        return True
    finally:
        try: probe.close()
        except Exception as exc: optional_failure("ui", exc)

def _friendly_portal_error(exc):
    """Convert low-level transport/protocol failures into safe setup guidance."""
    raw = str(exc or "").strip()
    low = raw.casefold()
    if any(token in low for token in ("timed out", "timeout")):
        return _("Connection timed out. Check the portal address, internet connection, and server availability.")
    if any(token in low for token in ("certificate", "ssl", "tls")):
        return _("TLS certificate validation failed. Verify the portal URL or adjust TLS mode later from Portal Manager.")
    if any(token in low for token in ("401", "403", "unauthor", "forbidden", "authentication", "auth failed")):
        return _("The portal rejected authentication. Check the MAC address and whether the subscription is active.")
    if any(token in low for token in ("name or service not known", "temporary failure in name resolution", "dns")):
        return _("The portal hostname could not be resolved. Check DNS/network settings and the portal address.")
    if any(token in low for token in ("connection refused", "network is unreachable", "no route to host")):
        return _("The portal server could not be reached. Check the address, network, and whether the server is online.")
    if "mac" in low and any(token in low for token in ("invalid", "format", "address")):
        return _("The MAC address is invalid. Use a format such as 00:1A:79:XX:XX:XX.")
    return _("Portal connection failed: %s") % (raw[:220] or _("Unknown error"))

def _new_isolated_source_client(profile, timeout=6):
    """Create a brand-new source client for background cache work.

    PortalSession is intentionally not used because it is a singleton and would
    share sockets/state with the foreground Live browser.
    """
    profile=dict(profile or {})
    portal=str(profile.get("portal") or "")
    source_type=str(profile.get("source_type") or "").strip().lower()
    low=portal.lower()
    if not source_type:
        source_type="m3u" if (".m3u" in low or "type=m3u" in low or "output=m3u" in low or
                              ("get.php" in low and ("username=" in low or "password=" in low))) else "stalker"
    if source_type=="m3u":
        from .m3u_adapter import M3UClient
        return M3UClient(portal,profile.get("mac") or "M3U",timeout=timeout)
    return StalkerClient(
        portal,profile.get("mac") or "",timeout=timeout,
        allow_http_fallback=profile.get("allow_http_fallback",False),
        tls_mode=profile.get("tls_mode","auto"),
        device_profile=profile.get("device_profile","auto"),
        allow_tls_fallback=profile.get("tls_fallback_accepted",False),
        http_fallback_accepted=profile.get("http_fallback_accepted",False),
    )


ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets_fhd")
_ACTIVE_THEME_CACHE = None
_ACTIVE_THEME_LOCK = threading.RLock()

def _active_theme():
    """Resolve the configured theme lazily; importing ui.py performs no config I/O."""
    global _ACTIVE_THEME_CACHE
    if _ACTIVE_THEME_CACHE in THEMES:
        return _ACTIVE_THEME_CACHE
    with _ACTIVE_THEME_LOCK:
        if _ACTIVE_THEME_CACHE in THEMES:
            return _ACTIVE_THEME_CACHE
        try:
            value = load_theme(readonly=True)
        except Exception:
            value = "nova_fhd"
        _ACTIVE_THEME_CACHE = value if value in THEMES else "nova_fhd"
        return _ACTIVE_THEME_CACHE
from .persistent_cache import ROOT as PERSISTENT_CACHE_ROOT, INDEX as PERSISTENT_LIBRARY_DIR, POSTERS as PERSISTENT_ART_DIR, BACKDROPS as BACKDROP_CACHE_DIR, GENERATED as PERSISTENT_GENERATED_DIR, ADAPTIVE as PERSISTENT_ADAPTIVE_DIR, SOURCE_POSTERS as SOURCE_POSTER_CACHE_DIR, SOURCE_BACKDROPS as SOURCE_BACKDROP_CACHE_DIR, HOME as PERSISTENT_HOME_DIR, LIVE as PERSISTENT_LIVE_DIR, hdd_ready, hdd_read_ready, ensure_persistent_dirs, persistent_write_gate, initialize_persistent_cache_once
from .media_library import load as load_global_media, save as save_global_media, item_paths as global_media_paths

def _select_artwork_cache_root():
    # Central HDD-first persistent cache selected once by persistent_cache.py.
    return PERSISTENT_CACHE_ROOT

ARTWORK_CACHE_ROOT = _select_artwork_cache_root()
IMAGE_CACHE_DIR = PERSISTENT_ART_DIR
THUMB_CACHE_DIR = PERSISTENT_GENERATED_DIR
CATEGORY_ADAPTIVE_TMP_DIR = os.path.join(PERSISTENT_GENERATED_DIR, "category_adaptive")
HOME_RUNTIME_DIR = os.path.join(PERSISTENT_GENERATED_DIR, "home_runtime")
HOME_SINGLE_DIR = PERSISTENT_HOME_DIR
VISUAL_BUNDLE_DIR = os.path.join(PERSISTENT_GENERATED_DIR, "visual_bundles")
# R171 durable final adaptive cache. Only final title chrome + its small pointer JSON live here.
ADAPTIVE_CHROME_DIR = os.path.join(PERSISTENT_ADAPTIVE_DIR, "chrome")
ADAPTIVE_BUNDLE_DIR = os.path.join(PERSISTENT_ADAPTIVE_DIR, "bundles")


def _visual_bundle_legacy_path(profile, media_type, item):
    # Beta54: no legacy visual-bundle identity. Derived UI state is keyed only
    # by the verified global TMDB identity when available.
    return _visual_bundle_path(profile,media_type,item,None)


def _tmdb_identity_for_item(profile,media_type,item,snapshot=None):
    """Return only an independently compatible external identity for originals.

    Test65 hardens the visual path as well as metadata: a stale/search-derived
    TMDB id must never select another title's global artwork/derived bundle.
    """
    snap=snapshot if isinstance(snapshot,dict) else {}
    row=item if isinstance(item,dict) else {}

    def candidate(source, provider_row=False):
        if not isinstance(source,dict):return (None,None)
        tid=source.get("tmdb_id") or source.get("tmdbid")
        mt=source.get("media_type") or media_type
        if not tid:return (None,None)
        if not provider_row:
            try:
                if not identity_cache_compatible(row,source):return (None,None)
            except Exception:return (None,None)
        try:return ("tv" if str(mt).lower() in ("tv","series") else "movie",int(tid))
        except Exception:return (None,None)

    mt,tid=candidate(snap,False)
    if tid:return mt,tid
    # ``item`` here is the raw provider catalogue row on Details/Grid callers.
    # A structured provider id is independent evidence and may anchor originals.
    mt,tid=candidate(row,True)
    if tid:return mt,tid
    try:
        local=load_detail_snapshot(profile or {},media_type,row) or {}
        mt,tid=candidate(local,False)
        if tid:return mt,tid
    except Exception:pass
    return (None,None)


def _visual_bundle_path(profile, media_type, item, snapshot=None):
    """Per-content derived presentation path.

    Originals may still deduplicate globally by verified TMDB identity, but UI
    derivatives must never be shared solely because several catalogue rows once
    pointed at the same (possibly poisoned) external id.  The stable provider
    content key also lets non-TMDB/manual BLUE artwork own Adaptive Details.
    """
    try:key=str(content_cache_key(profile or {},media_type,item or {}) or "")
    except Exception:key=""
    if not key:return ""
    return os.path.join(VISUAL_BUNDLE_DIR,"content_%s.json"%key)


def _persistent_adaptive_bundle_path(profile, media_type, item):
    try:key=str(content_cache_key(profile or {},media_type,item or {}) or "")
    except Exception:key=""
    if not key:return ""
    return os.path.join(ADAPTIVE_BUNDLE_DIR,"content_%s.json"%key)


def _load_persistent_adaptive_bundle(profile,media_type,item):
    path=_persistent_adaptive_bundle_path(profile,media_type,item)
    if not path or not hdd_read_ready():return {}
    try:
        with open(path,"r",encoding="utf-8") as fh:data=json.load(fh)
        if not isinstance(data,dict):return {}
        chrome=data.get("chrome") if isinstance(data.get("chrome"),dict) else {}
        chrome={k:v for k,v in chrome.items() if v and os.path.isfile(str(v))}
        if not chrome:return {}
        return {"chrome":chrome,"adaptive_source_fp":str(data.get("adaptive_source_fp") or "")}
    except Exception:
        return {}


def _save_persistent_adaptive_bundle(profile,media_type,item,chrome,source_fp):
    if not isinstance(chrome,dict) or not chrome or not source_fp:return False
    path=_persistent_adaptive_bundle_path(profile,media_type,item)
    if not path:return False
    try:
        if not _persistent_write_ok(ADAPTIVE_BUNDLE_DIR):return False
        os.makedirs(ADAPTIVE_BUNDLE_DIR,mode=0o700,exist_ok=True)
        clean={k:str(v) for k,v in chrome.items() if v and os.path.isfile(str(v))}
        if not clean:return False
        payload={"schema":171,"chrome":clean,"adaptive_source_fp":str(source_fp),"updated_at":int(time.time())}
        temp=path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        _persistent_write_require(temp)
        with open(temp,"w",encoding="utf-8") as fh:
            json.dump(payload,fh,ensure_ascii=False,separators=(",",":"));fh.flush();os.fsync(fh.fileno())
        _persistent_write_require(path);os.replace(temp,path)
        return True
    except Exception as exc:
        optional_failure("ui.persistent_adaptive_save",exc)
        try:
            if 'temp' in locals() and os.path.exists(temp):os.unlink(temp)
        except Exception:pass
        return False


def _content_art_paths(profile, media_type, item):
    """Compatibility wrapper onto the ONE global TMDB library.

    No per-portal artwork directory exists anymore.
    """
    mt,tid=_tmdb_identity_for_item(profile,media_type,item,None)
    if not tid:return {}
    paths=global_media_paths(mt,tid)
    return {"dir":paths["dir"],"poster":paths["poster"],"backdrop":paths["backdrop"],"manifest":paths["metadata"]}


def _load_content_manifest(profile,media_type,item):
    mt,tid=_tmdb_identity_for_item(profile,media_type,item,None)
    if not tid:return {}
    row=load_global_media(mt,tid) or {}
    if row.get("poster_local"):row["poster"]=row.get("poster_local")
    if row.get("backdrop_local"):row["backdrop"]=row.get("backdrop_local")
    return row


def _write_content_manifest(profile,media_type,item,updates,source="visual_bundle"):
    # Original artwork/metadata may only be written by media_library/ArtworkV2.
    # This compatibility hook persists metadata fields only when a verified TMDB
    # identity already exists; derived chrome is never copied into library/.
    mt,tid=_tmdb_identity_for_item(profile,media_type,item,updates if isinstance(updates,dict) else None)
    if not tid:return False
    safe={k:v for k,v in (updates or {}).items() if k not in ("grid_thumb","grid_card","grid_selection","grid_mood","detail_poster","backdrop_present","theme","accent","edge","chrome","poster","backdrop")}
    return save_global_media(mt,tid,safe) if safe else True


def _promote_content_art(profile,media_type,item,poster=None,backdrop=None,source="tmdb-library"):
    # Beta54 hard rule: never duplicate/copy original poster/backdrop outside the
    # global TMDB library. Callers receive the already-owned library paths only.
    row=_load_content_manifest(profile,media_type,item)
    out={}
    if row.get("poster_local"):out["poster"]=row.get("poster_local")
    if row.get("backdrop_local"):out["backdrop"]=row.get("backdrop_local")
    return out


def _load_content_art(profile,media_type,item):
    return _load_content_manifest(profile,media_type,item)



def _visual_source_fingerprint(path):
    """Cheap binding for reproducible derivatives to their global source."""
    try:
        path=os.path.realpath(str(path or ""))
        if not path or not os.path.isfile(path):return ""
        st=os.stat(path)
        return hashlib.sha1((path+"|%s|%s"%(int(st.st_mtime_ns),int(st.st_size))).encode("utf-8","ignore")).hexdigest()
    except Exception:return ""

def _validate_visual_bundle(data):
    if not isinstance(data,dict):return {}
    data=dict(data)
    for key in ("poster","backdrop","detail_poster","backdrop_present","theme","accent","edge","grid_thumb","grid_card","grid_selection","grid_mood"):
        value=str(data.get(key) or "")
        if value and not os.path.isfile(value):data[key]=""
    chrome=data.get("chrome")
    if isinstance(chrome,dict):data["chrome"]={k:v for k,v in chrome.items() if v and os.path.isfile(str(v))}
    else:data["chrome"]={}
    return data

def _load_visual_bundle(profile, media_type, item, snapshot=None):
    """Return verified originals plus per-content reproducible UI derivatives.

    Test65 isolates every presentation bundle by stable provider content key and
    binds each derivative to the exact original file that produced it.  A stale
    TMDB pointer can therefore never make unrelated titles share a poster,
    backdrop presentation, gradient or adaptive chrome.
    """
    if not hdd_read_ready():return {}
    mt,tid=_tmdb_identity_for_item(profile,media_type,item,snapshot)
    global_row=load_global_media(mt,tid) if tid else {}
    data={}
    if global_row.get("poster_local"):data["poster"]=global_row.get("poster_local")
    if global_row.get("backdrop_local"):data["backdrop"]=global_row.get("backdrop_local")
    path=_visual_bundle_path(profile,media_type,item,snapshot)
    if path:
        try:
            with open(path,"r",encoding="utf-8") as fh:derived=_validate_visual_bundle(json.load(fh))
            if isinstance(derived,dict):data.update(derived)
        except Exception as exc:optional_failure("ui.visual_bundle_load",exc)
    # R171: durable final adaptive overlays session presentation state. The
    # source fingerprint check below still invalidates it if the poster changes.
    try:
        persistent_adaptive=_load_persistent_adaptive_bundle(profile,media_type,item)
        if persistent_adaptive:data.update(persistent_adaptive)
    except Exception as exc:optional_failure("ui.persistent_adaptive_load",exc)
    # Reassert verified originals, then explicit BLUE per-content locks.
    if global_row.get("poster_local"):data["poster"]=global_row.get("poster_local")
    if global_row.get("backdrop_local"):data["backdrop"]=global_row.get("backdrop_local")
    try:
        manual=load_manual_rescue_art(profile,media_type,item) or {}
        mp=str(manual.get("poster_local") or "");mb=str(manual.get("backdrop_local") or "")
        # Provider/BLUE rescue is missing-only.  Never let an older fallback
        # shadow a verified TMDB master that already exists in the global store.
        if mp and os.path.isfile(mp) and not (data.get("poster") and os.path.isfile(str(data.get("poster")))):
            data["poster"]=mp;data["manual_rescue_poster_locked"]=True
        if mb and os.path.isfile(mb) and not (data.get("backdrop") and os.path.isfile(str(data.get("backdrop")))):
            data["backdrop"]=mb;data["manual_rescue_backdrop_locked"]=True
        if manual.get("manual_rescue_source"):data["manual_rescue_source"]=manual.get("manual_rescue_source")
    except Exception as exc:optional_failure("ui.manual_rescue_bundle_load",exc)

    poster_fp=_visual_source_fingerprint(data.get("poster") or global_row.get("poster_local"))
    backdrop_fp=_visual_source_fingerprint(data.get("backdrop") or global_row.get("backdrop_local"))

    # Poster-derived presentation is valid only for the exact poster bytes.
    if data.get("detail_poster") and (not poster_fp or str(data.get("detail_poster_source_fp") or "")!=poster_fp):
        data["detail_poster"]="";data.pop("detail_poster_source_fp",None)

    # Integrated landscape must follow synthetic->real backdrop upgrades and can
    # never survive a source change in place at manual_rescue_art/backdrop.jpg.
    if data.get("backdrop_present") and (not backdrop_fp or str(data.get("backdrop_present_source_fp") or "")!=backdrop_fp):
        data["backdrop_present"]="";data.pop("backdrop_present_source_fp",None)

    # Adaptive chrome/gradient are poster-derived.  Fail closed when the source
    # poster changed, which also kills old Test61-64 shared-TMDB contamination.
    adaptive_keys=("theme","accent","edge")
    has_adaptive=bool(any(data.get(k) for k in adaptive_keys) or (isinstance(data.get("chrome"),dict) and data.get("chrome")))
    if has_adaptive and (not poster_fp or str(data.get("adaptive_source_fp") or "")!=poster_fp):
        for k in adaptive_keys:data[k]=""
        data["chrome"]={};data.pop("adaptive_source_fp",None)

    # Test66: every Grid derivative is poster-derived too.  Test61-65 did not
    # bind grid_thumb/card/selection/mood to the source bytes, so one poisoned
    # English poster could keep reappearing after the original identity was fixed.
    grid_keys=("grid_thumb","grid_card","grid_selection","grid_mood")
    has_grid=any(data.get(k) for k in grid_keys)
    if has_grid and (not poster_fp or str(data.get("grid_source_fp") or "")!=poster_fp):
        for k in grid_keys:data[k]=""
        data.pop("grid_source_fp",None)
    return data


def _save_visual_bundle(profile, media_type, item, payload, snapshot=None):
    if not isinstance(payload,dict) or not payload:return False
    path=_visual_bundle_path(profile,media_type,item,snapshot or payload)
    if not path:return False
    # Only reproducible presentation derivatives live here.  Test65 adds source
    # fingerprints so in-place BLUE artwork upgrades invalidate stale UI assets.
    allowed=("detail_poster","backdrop_present","grid_thumb","grid_card","grid_selection","grid_mood","theme","accent","edge","chrome","detail_poster_source_fp","backdrop_present_source_fp","adaptive_source_fp","grid_source_fp")
    clean={k:v for k,v in payload.items() if k in allowed and v not in (None,"",{},[])}

    mt,tid=_tmdb_identity_for_item(profile,media_type,item,snapshot or payload)
    global_row=load_global_media(mt,tid) if tid else {}
    manual={}
    try:manual=load_manual_rescue_art(profile,media_type,item) or {}
    except Exception:manual={}
    poster_source=str(payload.get("poster") or manual.get("poster_local") or global_row.get("poster_local") or "")
    backdrop_source=str(payload.get("backdrop") or manual.get("backdrop_local") or global_row.get("backdrop_local") or "")
    poster_fp=_visual_source_fingerprint(poster_source)
    backdrop_fp=_visual_source_fingerprint(backdrop_source)

    if payload.get("detail_poster") and poster_fp:clean["detail_poster_source_fp"]=poster_fp
    if payload.get("backdrop_present") and backdrop_fp:clean["backdrop_present_source_fp"]=backdrop_fp
    if (payload.get("theme") or payload.get("accent") or payload.get("edge") or payload.get("chrome")) and poster_fp:
        clean["adaptive_source_fp"]=poster_fp
    # R171: final Details-family chrome is a durable part of the title package.
    # Save it independently from the session visual bundle so restart/power-off
    # never forces the same poster palette to be generated again.
    if payload.get("chrome") and poster_fp:
        if not _save_persistent_adaptive_bundle(profile,media_type,item,payload.get("chrome"),poster_fp):
            return False
    if any(payload.get(k) for k in ("grid_thumb","grid_card","grid_selection","grid_mood")) and poster_fp:
        clean["grid_source_fp"]=poster_fp
    if not clean:return True
    try:
        if not _persistent_write_ok(VISUAL_BUNDLE_DIR):return False
        os.makedirs(VISUAL_BUNDLE_DIR,mode=0o700,exist_ok=True)
        old={}
        try:
            if os.path.isfile(path):
                with open(path,"r",encoding="utf-8") as fh:old=_validate_visual_bundle(json.load(fh))
        except Exception:old={}
        merged=dict(old);merged.update(clean);merged["schema"]=66;merged["updated_at"]=int(time.time())
        temp=path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        _persistent_write_require(temp)
        with open(temp,"w",encoding="utf-8") as fh:
            json.dump(merged,fh,ensure_ascii=False,separators=(",",":"));fh.flush();os.fsync(fh.fileno())
        _persistent_write_require(path);os.replace(temp,path);return True
    except Exception as exc:
        optional_failure("ui.visual_bundle_save",exc);return False


def _persistent_write_ok(target):
    """Central UI gate for every mutation under Ultra Stalker's HDD cache."""
    try:
        absolute=os.path.abspath(str(target or ""))
        root=os.path.abspath(PERSISTENT_CACHE_ROOT)
        if absolute == root or absolute.startswith(root + os.sep):
            return bool(persistent_write_gate(absolute))
        return True
    except Exception as exc:
        optional_failure("ui.persistent_write_gate", exc)
        return False

def _persistent_write_require(target):
    if not _persistent_write_ok(target):
        raise OSError("persistent cache is not write-ready")
    return True

RECENT_SEARCH_FILE = "/etc/enigma2/ultrastalker/recent_searches.json"
_RECENT_SEARCH_LOCK = threading.RLock()
_HOME_HERO_LOCK = threading.RLock()
_HOME_VISUAL_BUILD_LOCK = threading.RLock()

def _fsync_parent_dir(path):
    directory = os.path.dirname(os.path.abspath(str(path or ""))) or "."
    try:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
WIZARD_DONE_FILE = "/etc/enigma2/ultrastalker/.wizard_done"
HOME_HERO_FILE = os.path.join(HOME_SINGLE_DIR, "hero.json")
HOME_HERO_TTL = 0
HOME_HERO_SCHEMA = 303
HERO_SINGLE_SLOT_MIGRATION = "/etc/enigma2/ultrastalker/.single_hero_slot_v303"
_LEGACY_HERO_CLEANED = False


def _prepare_single_home_hero(source_path,target_path,size=(1920,1080)):
    # Exact release Home-Hero visual contract, adapted only to the current single slot.
    try: os.makedirs(os.path.dirname(target_path) or HOME_SINGLE_DIR, exist_ok=True)
    except Exception: return None
    if _PILImage is None or not source_path or not os.path.isfile(source_path):return None
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        tw,th=map(int,size);rs=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui.silent_guard",exc)
            image=image.convert("RGB")
            if float(image.width)/max(1.0,float(image.height))<1.35:return None
            scale=max(float(tw)/max(1,image.width),float(th)/max(1,image.height))
            nw=max(tw,int(round(image.width*scale)));nh=max(th,int(round(image.height*scale)))
            image=image.resize((nw,nh),rs)
            left=max(0,(nw-tw)//2);top=max(0,(nh-th)//2)
            image=image.crop((left,top,left+tw,top+th))
            grade=_PILImage.new("RGBA",(tw,th),(2,7,12,38))
            image=_PILImage.alpha_composite(image.convert("RGBA"),grade).convert("RGB")
            scrim=_PILImage.new("RGBA",(tw,th),(0,0,0,0));sd=ImageDraw.Draw(scrim) if ImageDraw is not None else None
            if sd is not None:
                for x in range(0,min(tw,1040),16):
                    q=x/1040.0;a=int(118*((1.0-q)**1.55))
                    sd.rectangle((x,0,min(tw,x+16),470),fill=(1,6,10,a))
            if _PILImageFilter is not None:
                try:scrim=scrim.filter(_PILImageFilter.GaussianBlur(radius=22))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
            image=_PILImage.alpha_composite(image.convert("RGBA"),scrim).convert("RGB")
            vm=_PILImage.new("L",(1,th));vvals=[];fade_start=330.0;fade_end=610.0
            for y in range(th):
                if y<=fade_start:a=255
                elif y>=fade_end:a=0
                else:
                    t=(y-fade_start)/(fade_end-fade_start);smooth=t*t*(3.0-2.0*t);a=int(255*(1.0-smooth))
                vvals.append(max(0,min(255,a)))
            vm.putdata(vvals);vm=vm.resize((tw,th))
            hm=_PILImage.new("L",(tw,1));hvals=[]
            for x in range(tw):
                a=255
                if x<70:
                    t=x/70.0;a=int(224+31*t)
                elif x>1870:
                    t=max(0.0,(tw-1-x)/49.0);a=int(224+31*t)
                hvals.append(max(0,min(255,a)))
            hm.putdata(hvals);hm=hm.resize((tw,th))
            alpha=_PILImageChops.multiply(hm,vm) if _PILImageChops is not None else vm
            if _PILImageFilter is not None:
                try:alpha=alpha.filter(_PILImageFilter.GaussianBlur(radius=12))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
            rgba=image.convert("RGBA");rgba.putalpha(alpha);rgba.save(temp,"PNG",optimize=False)
        os.replace(temp,target_path)
        return target_path if os.path.isfile(target_path) and os.path.getsize(target_path)>100 else None
    except Exception as exc:
        optional_failure("ui.single_home_hero_prepare",exc)
        try:
            if os.path.exists(temp):os.unlink(temp)
        except OSError:pass
        return None

def _cleanup_legacy_home_hero_storage():
    """One-time cleanup of every old/automatic Home-Hero store.

    After migration, ROOT/hero is single-slot only: one hero image plus hero.json.
    Canonical movie/series poster and backdrop caches are never removed.
    """
    global _LEGACY_HERO_CLEANED
    if _LEGACY_HERO_CLEANED:return True
    try:
        if not hdd_ready(force=True):return False
        if not os.path.isfile(HERO_SINGLE_SLOT_MIGRATION):
            for legacy_dir in (os.path.join(PERSISTENT_CACHE_ROOT,"home"),HOME_RUNTIME_DIR,HOME_SINGLE_DIR):
                try:
                    if legacy_dir and os.path.isdir(legacy_dir):shutil.rmtree(legacy_dir)
                except Exception as exc:optional_failure("ui.hero_legacy_dir_cleanup",exc)
            try:
                if BACKDROP_CACHE_DIR and os.path.isdir(BACKDROP_CACHE_DIR):
                    for name in os.listdir(BACKDROP_CACHE_DIR):
                        if "homehero" not in name.lower():continue
                        path=os.path.join(BACKDROP_CACHE_DIR,name)
                        try:
                            if os.path.isfile(path) or os.path.islink(path):os.unlink(path)
                        except OSError:pass
            except Exception as exc:optional_failure("ui.hero_legacy_tag_cleanup",exc)
            os.makedirs(os.path.dirname(HERO_SINGLE_SLOT_MIGRATION),mode=0o700,exist_ok=True)
            fd,tmp=tempfile.mkstemp(prefix=".hero-migrate.",dir=os.path.dirname(HERO_SINGLE_SLOT_MIGRATION))
            try:
                with os.fdopen(fd,"w",encoding="ascii") as fh:fh.write("single-slot-v303\n")
                os.replace(tmp,HERO_SINGLE_SLOT_MIGRATION)
            finally:
                if os.path.exists(tmp):
                    try:os.unlink(tmp)
                    except OSError:pass
        os.makedirs(HOME_SINGLE_DIR,mode=0o700,exist_ok=True)
        _LEGACY_HERO_CLEANED=True
        return True
    except Exception as exc:
        optional_failure("ui.hero_legacy_cleanup",exc);return False

_PLUGIN_LAUNCH_SERIAL = 0
_PLUGIN_LAUNCH_TOKEN = "%d-%d" % (os.getpid(), int(time.time() * 1000))
_PLUGIN_ORIGINAL_SERVICE = None
_PLUGIN_ORIGINAL_ASPECT_RATIO = None
_PLUGIN_SERVICE_CAPTURED = False
_RUNTIME_LOG_LOCK = threading.RLock()

_RUNTIME_LOG_MAX = 1024 * 1024

def _runtime_endurance_log(event, **fields):
    runtime_breadcrumb(event, **fields)

def begin_plugin_launch(session=None):
    global _PLUGIN_LAUNCH_SERIAL, _PLUGIN_LAUNCH_TOKEN, _PLUGIN_ORIGINAL_SERVICE, _PLUGIN_ORIGINAL_ASPECT_RATIO, _PLUGIN_SERVICE_CAPTURED
    _PLUGIN_LAUNCH_SERIAL += 1
    # Capture once at the true plugin boundary, before Splash/Wizard/PortalList
    # can change navigation state. This covers both normal and first-run flows.
    _PLUGIN_ORIGINAL_SERVICE = None
    _PLUGIN_ORIGINAL_ASPECT_RATIO = capture_aspect_mode()
    _PLUGIN_SERVICE_CAPTURED = False
    if session is not None:
        try:
            _PLUGIN_ORIGINAL_SERVICE = session.nav.getCurrentlyPlayingServiceReference()
            _PLUGIN_SERVICE_CAPTURED = True
        except Exception as exc:
            optional_failure("ui.launch_service_snapshot", exc)
    _PLUGIN_LAUNCH_TOKEN = "%d-%d-%d" % (os.getpid(), int(time.time() * 1000), _PLUGIN_LAUNCH_SERIAL)
    # First launch action: eradicate legacy automatic/multi-Hero storage before
    # any screen can read it.  This is a one-time in-process migration.
    try:_cleanup_legacy_home_hero_storage()
    except Exception as exc:optional_failure("ui.hero_legacy_cleanup_launch",exc)
    # Grid position belongs to one plugin session only.  Never carry page/index
    # across a full close/reopen in the same Enigma2 process.  This also avoids
    # rebuilding several old portal pages during the splash on the next launch.
    try: _GRID_NAV_STATE.clear()
    except Exception as exc: optional_failure("ui.silent_guard",exc)
    try: _CATEGORY_NAV_STATE.clear()
    except Exception as exc: optional_failure("ui.silent_guard",exc)
    # Session navigation is already reset in RAM above. Do not rewrite ui_state
    # for every configured portal on every plugin launch; with dozens of portals
    # that was pure synchronous HDD I/O before the first screen even appeared.
    try:
        _cfg=load_settings();_updates={}
        if int(_cfg.get("persistent_cache_mb",20480) or 10240)==8192:_updates["persistent_cache_mb"]=20480
        if str(_cfg.get("resume_behavior") or "ask").lower()=="ask":_updates["resume_behavior"]="always"
        if _updates:save_settings(_updates)
    except Exception as exc:
        optional_failure("ui.launch_default_migration",exc)
    return _PLUGIN_LAUNCH_TOKEN
def current_plugin_launch():
    return _PLUGIN_LAUNCH_TOKEN

def _plugin_original_service_string():
    try:
        ref=_PLUGIN_ORIGINAL_SERVICE
        return ref.toString() if ref is not None else ""
    except Exception:
        return ""

def restore_plugin_service(session):
    """Return the receiver to the service and AV mode captured at plugin entry.

    Only the outer Ultra Stalker boundary owns this restoration.  Captured state
    is consumed before touching Enigma2 so repeated safety-net calls are harmless.
    """
    global _PLUGIN_ORIGINAL_SERVICE, _PLUGIN_ORIGINAL_ASPECT_RATIO, _PLUGIN_SERVICE_CAPTURED
    if not _PLUGIN_SERVICE_CAPTURED:
        return False

    return_ref=_PLUGIN_ORIGINAL_SERVICE
    return_aspect=_PLUGIN_ORIGINAL_ASPECT_RATIO
    _PLUGIN_SERVICE_CAPTURED=False
    _PLUGIN_ORIGINAL_SERVICE=None
    _PLUGIN_ORIGINAL_ASPECT_RATIO=None

    if session is None or return_ref is None:
        return False
    nav=getattr(session,"nav",None)
    if nav is None:
        return False

    try:
        force_session_silence(session,"",force=False,stop_native=True)
    except Exception as exc:
        optional_failure("ui.exit_silence",exc)

    try:
        nav.playService(return_ref)
    except Exception as exc:
        optional_failure("ui.restore_original_service",exc)
        return False

    restore_aspect_mode(return_aspect)
    return True

IMAGE_CACHE_TTL = None
ARTWORK_FAILURE_TTL = 30 * 60
LOG = get_logger()

_ARTWORK_PATH_CACHE = {}
_ARTWORK_PATH_CACHE_LOCK = threading.RLock()
_ARTWORK_PATH_CACHE_LIMIT = 256

def _optimized_artwork_url(url, backdrop=False):
    """Request receiver-sized TMDB artwork instead of multi-megabyte originals."""
    value=str(url or "").strip()
    low=value.lower()
    if "image.tmdb.org/t/p/" in low:
        size="w1280" if backdrop else "w500"
        try:
            parts=value.split("/t/p/",1)
            tail=parts[1].split("/",1)
            if len(tail)==2:return parts[0]+"/t/p/"+size+"/"+tail[1]
        except Exception as exc:optional_failure("ui.silent_guard",exc)
    return value

def _cache_artwork_path(digest,path):
    if not digest or not path:return
    with _ARTWORK_PATH_CACHE_LOCK:
        if len(_ARTWORK_PATH_CACHE)>=_ARTWORK_PATH_CACHE_LIMIT:
            # Drop the oldest insertion on Python 3.7+ dicts. Paths are only a
            # hint; filesystem validation remains authoritative.
            try:_ARTWORK_PATH_CACHE.pop(next(iter(_ARTWORK_PATH_CACHE)))
            except Exception:_ARTWORK_PATH_CACHE.clear()
        _ARTWORK_PATH_CACHE[digest]=path

def _cached_artwork_path(digest):
    with _ARTWORK_PATH_CACHE_LOCK:path=_ARTWORK_PATH_CACHE.get(digest)
    if path and _valid_cache_file(path):return path
    if path:
        with _ARTWORK_PATH_CACHE_LOCK:_ARTWORK_PATH_CACHE.pop(digest,None)
    return None

class _SafeImageRedirectHandler(SafeMediaRedirectHandler):
    """Backward-compatible artwork redirect policy backed by netsec."""
    pass


def _trusted_image_origins(profile):
    try:
        portal = str((profile or {}).get("portal", "") or "").strip()
        parts = urllib.parse.urlsplit(portal)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
        if scheme not in ("http", "https") or not host:
            return ()
        port = parts.port or (443 if scheme == "https" else 80)
        display = "[%s]" % host if ":" in host and not host.startswith("[") else host
        return ("%s://%s:%d" % (scheme, display, port),)
    except Exception:
        return ()


def _safe_image_opener(url, profile):
    trusted = _trusted_image_origins(profile)
    validate_remote_media_url(url, trusted_private_origins=trusted)
    return build_safe_media_opener(trusted_private_origins=trusted)


def _safe_image_headers(url, profile, client, item=None):
    """Return safe artwork headers without leaking portal credentials.

    Xtream/M3U artwork often lives on a sibling CDN and expects the same browser
    identity used by player_api/get.php.  M3UClient._request_headers contains no
    username/password, so it is safe to reuse cross-host.  Stalker token/MAC
    headers remain restricted to the exact portal origin.
    """
    generic = {"User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) MAG250 stbapp", "Accept": "image/*,*/*;q=0.8"}
    portal_parts = urllib.parse.urlsplit((profile or {}).get("portal", ""))
    # Send only a credential-free provider origin as Referer. A number of IPTV
    # logo CDNs reject hotlinked stream_icon/tvg-logo requests without it.
    try:
        if portal_parts.scheme and portal_parts.hostname:
            host=portal_parts.hostname
            if portal_parts.port:host+="%s%d"%(":",portal_parts.port)
            generic["Referer"]="%s://%s/"%(portal_parts.scheme,host)
    except Exception as exc:
        diagnostic_failure("ui.failsoft.image_referer",exc)
    # For Xtream/M3U provider art, reuse the adapter's browser identity even
    # when stream_icon points at a CDN hostname. No credentials are included.
    if isinstance(item,dict) and item.get("_xtream") and client is not None and hasattr(client,"_request_headers"):
        try:
            headers=dict(client._request_headers(browser=True,range_prefix=False) or {})
            headers["Accept"]="image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
            headers.pop("Range",None)
            if generic.get("Referer"):headers.setdefault("Referer",generic.get("Referer"))
            return headers
        except Exception as exc:
            diagnostic_failure("ui.failsoft.provider_image_headers",exc)
    image_parts = urllib.parse.urlsplit(url)
    def origin(parts):
        scheme=(parts.scheme or "").lower(); host=(parts.hostname or "").lower()
        port=parts.port or (443 if scheme == "https" else 80 if scheme == "http" else None)
        return (scheme,host,port)
    if client is not None and portal_parts.hostname and origin(image_parts) == origin(portal_parts):
        # Stalker carries MAG/token headers. M3U/Xtream uses its normal player
        # HTTP identity; calling _headers() unconditionally used to crash the
        # picon download path because M3UClient does not implement it.
        if hasattr(client,"_headers"):
            try:return client._headers()
            except Exception as exc:diagnostic_failure("ui.failsoft.816",exc)
        if hasattr(client,"_request_headers"):
            try:
                headers=dict(client._request_headers(browser=True,range_prefix=False) or {})
                headers["Accept"]="image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                headers.pop("Range",None)
                if generic.get("Referer"):headers.setdefault("Referer",generic.get("Referer"))
                return headers
            except Exception as exc:diagnostic_failure("ui.failsoft.824",exc)
    return generic

def _live_restart_trace(event, **state):
    """Lightweight Live trace. Never changes downloader/render behavior."""
    try:
        root="/tmp/UltraStalker"
        os.makedirs(root,0o700,exist_ok=True)
        try:os.chmod(root,0o700)
        except OSError:pass
        payload={"ts":time.strftime("%Y-%m-%d %H:%M:%S"),"event":str(event),
                 "thread":threading.current_thread().name,"tid":threading.get_ident()}
        payload.update({str(k):_redact_log_value(v) for k,v in state.items()})
        line=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        path=os.path.join(root,"live_restart_trace.log")
        with open(path,"a") as h:
            h.write(line+"\n");h.flush()
        try:os.chmod(path,0o600)
        except OSError:pass
        # Tiny atomic last-state file survives a normal Enigma2 restart.
        final=os.path.join(root,"live_restart_last.json")
        tmp="%s.tmp.%d.%d"%(final,os.getpid(),threading.get_ident())
        with open(tmp,"w") as h:
            json.dump(payload,h,ensure_ascii=False,sort_keys=True,default=str);h.flush();os.fsync(h.fileno())
        try:os.chmod(tmp,0o600)
        except OSError:pass
        os.replace(tmp,final)
    except Exception as exc:
        diagnostic_failure("ui.failsoft.image_meta_write",exc)


# Beta60: one derived-image executor plus a process-wide Pillow budget.
# Different UI subsystems may request image work independently, but only one
# heavy derived-image job is allowed to execute at a time on the receiver.
_IMAGE_WORK_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-image-work")
_IMAGE_EXECUTOR = _IMAGE_WORK_EXECUTOR
_ADAPTIVE_EXECUTOR = _IMAGE_WORK_EXECUTOR
_BACKDROP_PRESENT_EXECUTOR = _IMAGE_WORK_EXECUTOR
# Test67: visible provider posters are first-paint UI assets, not rescue.
# Keep them off the single Pillow lane so a 14-card page does not serialize
# network waits behind thumbnail/chrome work. BLUE/TMDB rescue remains capped
# by the separate two-worker global hydration executor below.
_VISIBLE_PROVIDER_POSTER_EXECUTOR = VISIBLE_PROVIDER_ARTWORK_EXECUTOR
# Page mood must not starve behind poster thumbnail/card generation.
_GRID_MOOD_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-grid-mood")
# Beta54: one global TMDB hydration lane for poster, backdrop and metadata.
# Two workers is the hard ceiling across Grid/Details background hydration.
_GLOBAL_HYDRATION_EXECUTOR = PriorityLazyExecutor(max_workers=2,max_pending=40,thread_name_prefix="ultrastalker-hydrate")
_DETAIL_PREFETCH_EXECUTOR = _GLOBAL_HYDRATION_EXECUTOR
_DETAIL_LOGO_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-details-logo")
_FAST_POSTER_EXECUTOR = _GLOBAL_HYDRATION_EXECUTOR
_POSTER_RESCUE_EXECUTOR = _GLOBAL_HYDRATION_EXECUTOR
_QUALITY_PREFETCH_EXECUTOR = METADATA_EXECUTOR
_GRID_EPG_EXECUTOR = METADATA_EXECUTOR
_PGV2_HERO_EXECUTOR = PriorityLazyExecutor(max_workers=2,max_pending=4,thread_name_prefix="ultrastalker-pgv2-hero")
_PGV2_MATERIAL_EXECUTOR = PriorityLazyExecutor(max_workers=1,max_pending=2,thread_name_prefix="ultrastalker-pgv2-material")
_GRID_ACCENT_EXECUTOR = _IMAGE_WORK_EXECUTOR
_POSTER_THUMB_EXECUTOR = _IMAGE_WORK_EXECUTOR
_CATEGORY_PREFETCH_EXECUTOR = CATALOGUE_EXECUTOR
# beta58: series hierarchy network warming must never share the page/category lane.
# A slow get_series_info request previously blocked adjacent-page preparation and
# could make Portal navigation feel slow after browsing M3U Series.
_SERIES_HIERARCHY_PREFETCH_EXECUTOR = CATALOGUE_EXECUTOR
_PICON_CACHE_EXECUTOR = CACHE_IO_EXECUTOR
# Test69: cache pruning must never compete with first-paint/image work.
_MAINTENANCE_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-maintenance")
_CATEGORY_CACHE = OrderedDict()
_CATEGORY_CACHE_LOCK = threading.RLock()
_CATEGORY_CACHE_TTL = 300.0
_CATEGORY_CACHE_LIMIT = 384
_CATEGORY_LAST_GOOD_DIR = os.path.join(PERSISTENT_GENERATED_DIR, "category_last_good")

# R268: portal-specific Home cold-start work is completed while the existing
# Splash is visible.  The warmed PortalSession is the same singleton Home later
# receives, category rows are hydrated into the normal RAM cache, and large Home
# pixmaps are decoded once into a tiny boot cache so Home can bind them without
# another visible HDD/decode beat.
_SPLASH_HOME_WARM_LOCK = threading.RLock()
_SPLASH_HOME_WARM = OrderedDict()
_SPLASH_HOME_WARM_LIMIT = 8
_HOME_BOOT_PIXMAP_LOCK = threading.RLock()
_HOME_BOOT_PIXMAPS = OrderedDict()
_HOME_BOOT_PIXMAP_LIMIT = 16

def _splash_profile_key(profile):
    profile=profile if isinstance(profile,dict) else {}
    return (str(profile.get("portal") or "").rstrip("/").lower(),
            str(profile.get("mac") or "").upper(),
            str(profile.get("source_type") or "stalker").lower())

def _remember_splash_home_warm(profile, account_info=None, category_ready=None, recent_art=None, recent_progress=None):
    key=_splash_profile_key(profile)
    state={"launch":current_plugin_launch(),"stamp":time.monotonic(),
           "account_info":dict(account_info or {}) if isinstance(account_info,dict) else {},
           "category_ready":tuple(category_ready or ()),
           "recent_art":dict(recent_art or {}) if isinstance(recent_art,dict) else {},
           "recent_progress":dict(recent_progress or {}) if isinstance(recent_progress,dict) else {}}
    with _SPLASH_HOME_WARM_LOCK:
        _SPLASH_HOME_WARM[key]=state
        try:_SPLASH_HOME_WARM.move_to_end(key)
        except Exception:pass
        while len(_SPLASH_HOME_WARM)>_SPLASH_HOME_WARM_LIMIT:
            try:_SPLASH_HOME_WARM.popitem(last=False)
            except Exception:break
    return state

def splash_home_warm_state(profile):
    key=_splash_profile_key(profile)
    with _SPLASH_HOME_WARM_LOCK:
        state=_SPLASH_HOME_WARM.get(key)
        if not isinstance(state,dict) or state.get("launch")!=current_plugin_launch():return {}
        try:_SPLASH_HOME_WARM.move_to_end(key)
        except Exception:pass
        return dict(state)

def splash_home_is_warm(profile):
    return bool(splash_home_warm_state(profile))

def splash_home_account_info(profile):
    state=splash_home_warm_state(profile)
    info=state.get("account_info") if isinstance(state,dict) else None
    return dict(info) if isinstance(info,dict) else {}

def splash_home_recent_assets(profile):
    state=splash_home_warm_state(profile)
    art=state.get("recent_art") if isinstance(state,dict) else None
    progress=state.get("recent_progress") if isinstance(state,dict) else None
    return (dict(art or {}) if isinstance(art,dict) else {},
            dict(progress or {}) if isinstance(progress,dict) else {})

def _boot_pixmap_put(path,pix):
    if not path or pix is None:return
    path=os.path.realpath(str(path))
    with _HOME_BOOT_PIXMAP_LOCK:
        _HOME_BOOT_PIXMAPS[path]=pix
        try:_HOME_BOOT_PIXMAPS.move_to_end(path)
        except Exception:pass
        while len(_HOME_BOOT_PIXMAPS)>_HOME_BOOT_PIXMAP_LIMIT:
            try:_HOME_BOOT_PIXMAPS.popitem(last=False)
            except Exception:break

def home_boot_pixmap(path):
    if not path:return None
    path=os.path.realpath(str(path))
    with _HOME_BOOT_PIXMAP_LOCK:
        pix=_HOME_BOOT_PIXMAPS.get(path)
        if pix is not None:
            try:_HOME_BOOT_PIXMAPS.move_to_end(path)
            except Exception:pass
        return pix

def _page_cache_file(path):
    """Read a local artwork file once so setPixmapFromFile never waits on HDD."""
    try:
        if not path or not os.path.isfile(path):return False
        with open(path,"rb") as fh:
            while fh.read(256*1024):pass
        return True
    except Exception:return False

def _prime_home_boot_artwork():
    hero={}
    try:
        if HOME_HERO_FILE and os.path.isfile(HOME_HERO_FILE):
            with open(HOME_HERO_FILE,"r",encoding="utf-8") as fh:state=json.load(fh)
            if isinstance(state,dict) and int(state.get("schema") or 0)==HOME_HERO_SCHEMA and isinstance(state.get("hero"),dict):
                hero=dict(state.get("hero") or {})
    except Exception as exc:optional_failure("ui.splash_home_hero_read",exc)
    if not hero:return {}
    title_logo=str(hero.get("title_logo_local") or "")
    if not title_logo:
        try:
            from .title_logo_ultra import ultra_title_logo_cached
            media=str(hero.get("media_type") or "vod")
            title=str(hero.get("title") or "")
            title_logo=ultra_title_logo_cached(media,hero,hero,(432,210),title) or ""
            if title_logo:hero["title_logo_local"]=title_logo
        except Exception as exc:optional_failure("ui.splash_home_logo_cache",exc)
    candidates=[]
    for key in ("prepared","display_backdrop_local","backdrop_local","display_poster_local","poster_local"):
        value=str(hero.get(key) or "")
        if value and value not in candidates:candidates.append(value)
    if title_logo and title_logo not in candidates:candidates.append(title_logo)
    for path in candidates:
        _page_cache_file(path)
    return hero

def _persist_splash_home_logo(hero,path,resolved=None):
    if not (isinstance(hero,dict) and path and HOME_HERO_FILE):return False
    resolved=resolved if isinstance(resolved,dict) else {}
    try:
        with _HOME_HERO_LOCK:
            with open(HOME_HERO_FILE,"r",encoding="utf-8") as fh:state=json.load(fh)
            current=state.get("hero") if isinstance(state,dict) and isinstance(state.get("hero"),dict) else None
            if not current:return False
            current["title_logo_local"]=str(path)
            for key in ("original_language","origin_country","countries","production_countries","country_hint"):
                value=resolved.get(key)
                if value not in (None,"",[],{}):current[key]=value
            folder=os.path.dirname(HOME_HERO_FILE) or "/tmp"
            fd,tmp=tempfile.mkstemp(prefix=".splash-hero-logo.",suffix=".tmp",dir=folder)
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as fh:
                    json.dump(state,fh,ensure_ascii=False,separators=(",",":"));fh.flush();os.fsync(fh.fileno())
                os.chmod(tmp,0o600);os.replace(tmp,HOME_HERO_FILE)
            finally:
                if os.path.exists(tmp):
                    try:os.unlink(tmp)
                    except OSError:pass
        return True
    except Exception as exc:
        optional_failure("ui.splash_home_logo_persist",exc);return False

def splash_prime_home_pixmaps_gui():
    """Decode the already page-cached Home PNGs while Splash owns the GUI.

    Called only from the Enigma2 main thread by HomeWarmupSplashScreen. This
    keeps receiver-specific loadPNG work out of background threads.
    """
    hero=_prime_home_boot_artwork()
    if not isinstance(hero,dict) or not hero:return 0
    candidates=[]
    for key in ("prepared","display_backdrop_local","backdrop_local","title_logo_local"):
        value=str(hero.get(key) or "")
        if value and value not in candidates:candidates.append(value)
    decoded=0
    for name in ("home_live.png","home_movies.png","home_series.png","home_catchup.png","home_favorites.png","home_search.png","home_settings.png"):
        try:
            path=asset(name)
            if path and path not in candidates:candidates.append(path)
        except Exception:pass
    for path in candidates:
        try:
            if not str(path).lower().endswith(".png") or not os.path.isfile(path):continue
            pix=loadPNG(str(path))
            if pix is not None:
                _boot_pixmap_put(path,pix);decoded+=1
        except Exception as exc:optional_failure("ui.splash_home_pixmap",exc)
    return decoded

def _splash_recent_series_identity(item,mtype):
    item=item if isinstance(item,dict) else {}
    mt=str(mtype or "").lower()
    keys=("_series_tmdb_id","series_tmdb_id","tv_tmdb_id") + (("_locked_tmdb_id","tmdb_id") if mt=="series" else ())
    for key in keys:
        value=item.get(key)
        if value not in (None,""):return "tmdb:%s"%str(value).strip()
    pkeys=("_series_id","series_id","series_uid","parent_id","series") if mt=="episode" else ("series_id","id","series_uid","_series_id")
    for key in pkeys:
        value=item.get(key)
        if value not in (None,""):return "provider:%s"%str(value).strip()
    raw=item.get("_series_title") or item.get("series_title") or item.get("series_name") or (item.get("name") if mt!="episode" else "") or item.get("title") or ""
    title=re.sub(r"[^\w\u0600-\u06ff]+"," ",str(raw or "").casefold(),flags=re.UNICODE)
    title=re.sub(r"\s+"," ",title).strip()
    return "title:%s"%title if title else ""

def _splash_recent_rows(profile):
    portal=str((profile or {}).get("portal") or "").rstrip("/").lower();mac=str((profile or {}).get("mac") or "").upper()
    groups=[[],[],[]];seen_series=set()
    try:recent=load_recently_played() or []
    except Exception:recent=[]
    for entry in recent:
        if not isinstance(entry,dict):continue
        if str(entry.get("portal") or "").rstrip("/").lower()!=portal:continue
        if entry.get("mac") and str(entry.get("mac") or "").upper()!=mac:continue
        item=entry.get("item") if isinstance(entry.get("item"),dict) else entry
        mtype=str(entry.get("media_type") or item.get("_saved_media_type") or "").lower()
        group=0 if mtype in ("itv","live") else (1 if mtype=="vod" else (2 if mtype in ("series","episode") else -1))
        if group<0:continue
        if group==2:
            identity=_splash_recent_series_identity(item,mtype)
            if identity and identity in seen_series:continue
            if identity:seen_series.add(identity)
        if len(groups[group])<3:groups[group].append((entry,item,mtype))
        if all(len(rows)>=3 for rows in groups):break
    for rows in groups:
        while len(rows)<3:rows.append(None)
    return groups[0]+groups[1]+groups[2]

def _splash_prepare_recent_assets(profile):
    art={};progress={};rows=_splash_recent_rows(profile)
    for i,packed in enumerate(rows):
        if not packed:continue
        entry,item,mtype=packed;group=max(0,min(2,i//3))
        size=(220,132) if group==0 else (190,272)
        ph=("us168_live_placeholder_220x132.png","grid_placeholder_movie_921.png","grid_placeholder_series_921.png")[group]
        try:
            cached=_home_cached_art(item,profile,size,ph,mtype)
            if group==0 and cached and os.path.isfile(str(cached)) and os.path.basename(str(cached))!=os.path.basename(str(asset(ph))):
                fitted=_fit_live_picon_canvas(str(cached),PERSISTENT_GENERATED_DIR,(220,132))
                art[i]=str(fitted or cached)
            elif cached:
                art[i]=str(cached)
            if art.get(i):_page_cache_file(art[i])
        except Exception as exc:optional_failure("ui.splash_recent_art",exc)
        if group in (1,2):
            try:
                pos=max(0,int(entry.get("_position") or 0));dur=max(0,int(entry.get("_duration") or 0));completed=bool(entry.get("_completed"))
                pct=min(100,int(pos*100.0/dur)) if pos and dur else (100 if completed else 0)
                if pct>0:
                    from .ui_cinematic_global import _cinematic_row_progress_frame
                    path=_cinematic_row_progress_frame("#5fc49a",pct,width=277) or ""
                    if path:
                        progress[i]=str(path);_page_cache_file(path)
            except Exception as exc:optional_failure("ui.splash_recent_progress",exc)
    return art,progress

def splash_warm_home_profile(profile, progress=None):
    """Complete selected-portal Home work before Home is ever painted.

    `progress` is a worker-safe callback accepting (percent, status).  Network
    category calls use the shared PortalSession singleton, so Home inherits the
    already-authorized client instead of opening another connection.
    """
    profile=dict(profile or {})
    ready=[];account={}
    def report(pct,text):
        if callable(progress):
            try:progress(int(pct),str(text or ""))
            except Exception:pass
    hero=_prime_home_boot_artwork()
    # If the pinned Home title logo is missing, resolve it here while Splash is
    # still visible. Home therefore never starts a title-logo network job or
    # swaps text -> logo after the viewer has already arrived.
    if isinstance(hero,dict) and hero:
        try:
            from .title_logo_runtime import valid_ultra_title_logo
            from .title_logo_ultra import ultra_title_logo_cached, resolve_ultra_title_logo
            media=str(hero.get("media_type") or "vod");title=str(hero.get("title") or "")
            logo=str(hero.get("title_logo_local") or "")
            if not valid_ultra_title_logo(logo):
                logo=ultra_title_logo_cached(media,hero,hero,(432,210),title) or ""
            resolved={}
            if not valid_ultra_title_logo(logo):
                logo,resolved=resolve_ultra_title_logo(profile,media,hero,hero,(432,210),title,settings=(load_settings() or {}))
                logo=str(logo or "")
            if logo and valid_ultra_title_logo(logo):
                _persist_splash_home_logo(hero,logo,resolved);_page_cache_file(logo)
        except Exception as exc:optional_failure("ui.splash_home_logo_resolve",exc)
    report(25,_('Preparing Home artwork'))
    session=PortalSession(profile,timeout=(load_settings() or {}).get("timeout",10))
    client=session.client
    source_type=str(profile.get("source_type") or ("m3u" if _looks_like_m3u_url(profile.get("portal")) else "stalker")).lower()
    media_steps=(("itv",50,_('Preparing Live categories')),("vod",75,_('Preparing Movie categories')),("series",75,_('Preparing Series categories')))
    for media,pct,label in media_steps:
        report(pct,label)
        rows=None
        try:rows=_category_cache_get(profile,media)
        except Exception as exc:optional_failure("ui.splash_category_cache_%s"%media,exc)
        # M3U catalogues may be multi-minute downloads. Preserve the established
        # lazy M3U contract: hydrate last-good rows here, never parse a huge
        # playlist merely to satisfy startup warmup.
        if rows is None and source_type!="m3u":
            try:
                rows=client.genres(media)
                if isinstance(rows,list) and rows:_category_cache_put(profile,media,rows)
            except Exception as exc:optional_failure("ui.splash_category_%s"%media,exc)
        if isinstance(rows,list) and rows:ready.append(media)
    report(75,_('Preparing recent cards'))
    recent_art,recent_progress=_splash_prepare_recent_assets(profile)
    report(75,_('Preparing account'))
    if source_type!="m3u":
        try:
            info=client.account_info()
            if isinstance(info,dict):account=dict(info)
        except Exception as exc:optional_failure("ui.splash_account",exc)
    if account:
        try:
            expiry=account.get("phone") or account.get("end_date") or account.get("expire_billing_date") or ""
            state=account.get("status") or account.get("account_status") or "CONNECTED"
            profile["expiry"]=str(expiry or "");profile["account_state"]=str(state or "")
            if expiry:profile["state"]="%s / %s"%(str(state),str(expiry))
            profiles=load_profiles();target=_splash_profile_key(profile)
            found=False
            for row in profiles:
                if _splash_profile_key(row)==target:
                    row.update({"expiry":profile.get("expiry",""),"account_state":profile.get("account_state",""),"state":profile.get("state","")});found=True;break
            if not found:profiles.append(dict(profile))
            save_profiles(profiles)
        except Exception as exc:optional_failure("ui.splash_account_persist",exc)
    _remember_splash_home_warm(profile,account_info=account,category_ready=ready,recent_art=recent_art,recent_progress=recent_progress)
    report(100,_('Ready'))
    return {"category_ready":tuple(ready),"account_info":account}

def _category_cache_key(profile, media_type):
    return (str((profile or {}).get("portal") or "").rstrip("/").lower(), str((profile or {}).get("mac") or "").upper(), str(media_type or "").lower())

def _category_cache_disk_path(profile, media_type):
    key=_category_cache_key(profile,media_type)
    digest=hashlib.sha1(("|".join(key)).encode("utf-8","ignore")).hexdigest()
    return os.path.join(_CATEGORY_LAST_GOOD_DIR,"%s.json"%digest)

def _category_cache_get(profile, media_type):
    """Return the last successful category catalogue immediately.

    RAM remains the hot five-minute layer.  The HDD copy is deliberately
    last-good rather than expiring data: Portal categories are tiny routing
    records, and showing the last known-good list is much better than exposing
    an empty backdrop because one Stalker loader request hiccupped.  The
    Categories screen refreshes stale HDD data quietly after first paint.
    """
    key = _category_cache_key(profile, media_type)
    now = time.monotonic()
    with _CATEGORY_CACHE_LOCK:
        row = _CATEGORY_CACHE.get(key)
        if row:
            stamp,data=row
            try:_CATEGORY_CACHE.move_to_end(key)
            except Exception as exc:optional_failure("ui.category_cache_lru",exc)
            # Keep the row even after the hot TTL; callers may paint it while a
            # silent refresh happens.  The age helper decides refresh policy.
            return [dict(x) if isinstance(x,dict) else x for x in data]
    path=_category_cache_disk_path(profile,media_type)
    try:
        if hdd_read_ready() and os.path.isfile(path):
            with open(path,"r",encoding="utf-8") as fh:payload=json.load(fh)
            data=payload.get("rows") if isinstance(payload,dict) else None
            if isinstance(data,list) and data:
                clean=[dict(x) if isinstance(x,dict) else x for x in data]
                # Disk rows are stale-safe first paint. Give them an old RAM
                # stamp so the screen knows to refresh quietly.
                with _CATEGORY_CACHE_LOCK:
                    _CATEGORY_CACHE[key]=(now-_CATEGORY_CACHE_TTL-1.0,clean)
                    try:_CATEGORY_CACHE.move_to_end(key)
                    except Exception as exc:optional_failure("ui.category_cache_lru",exc)
                return [dict(x) if isinstance(x,dict) else x for x in clean]
    except Exception as exc:optional_failure("ui.category_last_good_read",exc)
    return None

def _category_cache_age(profile, media_type):
    key=_category_cache_key(profile,media_type)
    with _CATEGORY_CACHE_LOCK:
        row=_CATEGORY_CACHE.get(key)
        if row:
            try:return max(0.0,time.monotonic()-float(row[0]))
            except Exception:return _CATEGORY_CACHE_TTL+1.0
    return _CATEGORY_CACHE_TTL+1.0

def _category_cache_write_disk(profile,media_type,clean):
    path=_category_cache_disk_path(profile,media_type)
    try:
        if not _persistent_write_ok(_CATEGORY_LAST_GOOD_DIR):return
        os.makedirs(_CATEGORY_LAST_GOOD_DIR,mode=0o700,exist_ok=True)
        temp=path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        _persistent_write_require(temp)
        with open(temp,"w",encoding="utf-8") as fh:
            json.dump({"schema":1,"updated_at":int(time.time()),"rows":clean},fh,ensure_ascii=False,separators=(",",":"));fh.flush();os.fsync(fh.fileno())
        _persistent_write_require(path);os.replace(temp,path)
    except Exception as exc:optional_failure("ui.category_last_good_write",exc)

def _category_cache_put(profile, media_type, rows):
    if not isinstance(rows, list) or not rows:return
    key = _category_cache_key(profile, media_type)
    clean = [dict(x) if isinstance(x, dict) else x for x in rows]
    with _CATEGORY_CACHE_LOCK:
        _CATEGORY_CACHE[key]=(time.monotonic(),clean)
        try:_CATEGORY_CACHE.move_to_end(key)
        except Exception as exc:optional_failure("ui.category_cache_lru",exc)
        while len(_CATEGORY_CACHE)>_CATEGORY_CACHE_LIMIT:
            try:_CATEGORY_CACHE.popitem(last=False)
            except Exception:break
    # Never make category first-paint wait for fsync. One low-priority worker
    # persists only successful catalogues.
    try:_CATEGORY_PREFETCH_EXECUTOR.submit(_category_cache_write_disk,dict(profile or {}),str(media_type or ""),clean)
    except Exception as exc:optional_failure("ui.category_last_good_submit",exc)

def _invalidate_source_navigation_cache(profile):
    """Drop only navigation/catalogue first-paint caches for one selected source."""
    removed=0
    for media_type in ("itv","vod","series"):
        key=_category_cache_key(profile,media_type)
        with _CATEGORY_CACHE_LOCK:
            if _CATEGORY_CACHE.pop(key,None) is not None:removed+=1
        path=_category_cache_disk_path(profile,media_type)
        try:
            if os.path.isfile(path):os.remove(path);removed+=1
        except OSError as exc:optional_failure("ui.category_refresh_cache_remove",exc)
    return removed



def _available_memory_mb():
    """Return MemAvailable in MiB on Linux receivers, or None when unavailable."""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    parts=line.split()
                    if len(parts)>=2:
                        return max(0, int(parts[1]) // 1024)
    except Exception as exc:
        optional_failure("ui.silent_guard",exc)
    return None


def _memory_pressure(limit_mb=112):
    available=_available_memory_mb()
    return available is not None and available < int(limit_mb)
_GLOBAL_GRID_PIXMAP_CACHE = OrderedDict()
_GLOBAL_GRID_PIXMAP_CACHE_LOCK = threading.Lock()
# Keep this conservative on low-memory boxes, but allow a small session-hot cache
# on healthier receivers so returning to a grid does not immediately re-decode
# the same posters from HDD.
_GLOBAL_GRID_PIXMAP_CACHE_LIMIT = 2
_GRID_TITLE_FIT_CACHE = OrderedDict()
_GRID_TITLE_FIT_CACHE_LIMIT = 384

_GRID_ACCENT_CACHE = OrderedDict()
_GRID_ACCENT_CACHE_LIMIT = 192

# One poster drives card chrome, selection laser, page mood and Details.  Keep
# the extracted palette in RAM for the whole Enigma2 session so those surfaces
# do not reopen/quantize the same JPEG four times during one focus change.

# Session navigation memory. Keys include portal/media/category so returning from
# details, playback or a recreated grid lands on the exact previous item.
class _BoundedNavState(OrderedDict):
    def __init__(self,limit):
        OrderedDict.__init__(self);self.limit=max(16,int(limit))
    def __setitem__(self,key,value):
        OrderedDict.__setitem__(self,key,value)
        try:self.move_to_end(key)
        except Exception as exc:optional_failure("ui.nav_state_lru",exc)
        while len(self)>self.limit:
            try:self.popitem(last=False)
            except Exception:break
    def get(self,key,default=None):
        value=OrderedDict.get(self,key,default)
        if key in self:
            try:self.move_to_end(key)
            except Exception as exc:optional_failure("ui.nav_state_lru",exc)
        return value

_GRID_NAV_STATE = _BoundedNavState(512)
_CATEGORY_NAV_STATE = _BoundedNavState(256)
_ARTWORK_FAILURES = {}
_ARTWORK_FAILURE_LOCK = threading.Lock()
_ARTWORK_FAILURE_LIMIT = 1024
_ARTWORK_FILE_LOCK_GUARD = threading.Lock()
_ARTWORK_FILE_LOCKS = weakref.WeakValueDictionary()
_THUMB_BUILDING = set()
_THUMB_BUILDING_LOCK = threading.Lock()
_THUMB_BUILDING_LIMIT = 96

def _artwork_file_lock(digest):
    key = str(digest or "")
    with _ARTWORK_FILE_LOCK_GUARD:
        lock = _ARTWORK_FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ARTWORK_FILE_LOCKS[key] = lock
        return lock


def _artwork_target_lock(path):
    return _artwork_file_lock(hashlib.sha1(os.path.abspath(str(path or "")).encode("utf-8","ignore")).hexdigest())

def shutdown_ui_workers(wait=False):
    """Stop module-level artwork/EPG executors during Enigma2 GUI shutdown."""
    _shutdown_executors=[]
    for executor in (_IMAGE_EXECUTOR, _ADAPTIVE_EXECUTOR, _BACKDROP_PRESENT_EXECUTOR, _DETAIL_LOGO_EXECUTOR, _GRID_MOOD_EXECUTOR, _GLOBAL_HYDRATION_EXECUTOR, _PGV2_HERO_EXECUTOR, _PGV2_MATERIAL_EXECUTOR, _GRID_ACCENT_EXECUTOR, _POSTER_THUMB_EXECUTOR, _MAINTENANCE_EXECUTOR):
        if executor not in _shutdown_executors:_shutdown_executors.append(executor)
    for executor in _shutdown_executors:
        try: executor.shutdown(wait=bool(wait), cancel_futures=True)
        except TypeError: executor.shutdown(wait=bool(wait))
        except Exception as exc: optional_failure("ui", exc)




def _friendly_error(value):
    text = one_line(value, 220)
    low = text.casefold()
    if "non-json" in low: return _("Content temporarily unavailable. Try again.")
    if "timed out" in low or "timeout" in low: return _("Connection timed out. Check the portal address, internet connection, and server availability.")
    if "http 401" in low or "unauthor" in low: return _("The portal rejected authentication. Check the MAC address and whether the subscription is active.")
    if "security consent" in low or "fallback is not approved" in low or "certificate verification" in low:
        return _("TLS certificate validation failed. Verify the portal URL or adjust TLS mode later from Portal Manager.")
    if "connection" in low: return _("The portal server could not be reached. Check the address, network, and whether the server is online.")
    return text or _("Content temporarily unavailable. Try again.")







def _load_recent_searches():
    try:
        if os.path.getsize(RECENT_SEARCH_FILE) > 64 * 1024:
            return []
        with open(RECENT_SEARCH_FILE, "r", encoding="utf-8") as h:
            data = json.load(h)
        return [str(x) for x in data if str(x).strip()][:100] if isinstance(data, list) else []
    except Exception:
        return []

def _save_recent_search(term):
    term = str(term or "").strip()
    if not term: return
    with _RECENT_SEARCH_LOCK:
        items = [x for x in _load_recent_searches() if x.lower() != term.lower()]
        items.insert(0, term); items = items[:100]
        tmp = None
        try:
            directory=os.path.dirname(RECENT_SEARCH_FILE);os.makedirs(directory, mode=0o700, exist_ok=True)
            fd,tmp=tempfile.mkstemp(prefix="recent_searches.",suffix=".tmp",dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as h:
                json.dump(items, h, ensure_ascii=False, indent=2)
                h.flush();os.fsync(h.fileno())
            try: os.chmod(tmp, 0o600)
            except OSError: pass
            os.replace(tmp, RECENT_SEARCH_FILE);tmp=None
        except Exception as exc:
            optional_failure("ui.recent_search", exc)
        finally:
            if tmp:
                try: os.unlink(tmp)
                except OSError: pass
# Persistent image/thumbnail directories are created lazily by the central
# write gate.  Importing ui.py must never touch or wake /media/hdd.

def asset(name):
    themed = os.path.join(ASSET_DIR, "themes", _active_theme(), name)
    return themed if os.path.exists(themed) else os.path.join(ASSET_DIR, name)

_ICON_PIXMAP_CACHE = {}
_ICON_PIXMAP_CACHE_LIMIT = 16

def cached_png(path):
    if not path:
        return None
    cached = _ICON_PIXMAP_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        cached = loadPNG(path)
        if cached is not None:
            if len(_ICON_PIXMAP_CACHE) >= _ICON_PIXMAP_CACHE_LIMIT:
                # Static UI icons are cheap to reload; bounding this cache keeps
                # long browsing sessions from retaining every pixmap forever.
                _ICON_PIXMAP_CACHE.clear()
            _ICON_PIXMAP_CACHE[path] = cached
        return cached
    except Exception:
        return None

def _release_pixmap_widget(screen, name):
    """Drop the native gPixmap owned by one widget immediately."""
    try:
        widget=screen[name]
        if widget.instance is not None:
            widget.instance.setPixmap(None)
        try:widget.hide()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
    except Exception as exc:
        optional_failure("ui.silent_guard",exc)


def _native_image_pressure_relief(force=False):
    """Bound process-wide native image retention on constrained STBs.

    dmesg from the target receiver shows Nexus_Image triggering OOM while
    Enigma2 owns ~600 MiB RSS. Clearing Python dictionaries alone is not enough;
    these caches contain native pixmap references, so release them at screen
    lifecycle boundaries and whenever MemAvailable is low.
    """
    try:
        low=_memory_pressure(180)
    except Exception:
        low=False
    if not (force or low):
        return False
    try:
        with _GLOBAL_GRID_PIXMAP_CACHE_LOCK:
            _GLOBAL_GRID_PIXMAP_CACHE.clear()
    except Exception as exc:optional_failure("ui.silent_guard",exc)
    try:_ICON_PIXMAP_CACHE.clear()
    except Exception as exc:optional_failure("ui.silent_guard",exc)
    try:_GRID_ACCENT_CACHE.clear()
    except Exception as exc:optional_failure("ui.silent_guard",exc)
    try:
        with _DYNAMIC_PALETTE_CACHE_LOCK:
            if low:
                while len(_DYNAMIC_PALETTE_CACHE)>64:
                    _DYNAMIC_PALETTE_CACHE.popitem(last=False)
    except Exception as exc:optional_failure("ui.silent_guard",exc)
    try:gc.collect()
    except Exception as exc:optional_failure("ui.silent_guard",exc)
    return True


def _cleanup_cache_dir(cache_dir, max_files=None, max_bytes=None):
    try:
        entries = []
        total = 0
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            entries.append((stat.st_mtime, stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        entries.sort()
        file_limited = max_files not in (None, 0)
        byte_limited = max_bytes not in (None, 0)
        while entries and ((file_limited and len(entries) > int(max_files)) or (byte_limited and total > int(max_bytes))):
            _atime, _mtime, size, path = entries.pop(0)
            try:
                if not _persistent_write_ok(path):
                    break
                os.unlink(path)
                total -= size
            except OSError:
                pass
    except Exception as exc:
        optional_failure("ui", exc)
def cleanup_image_cache(max_files=None, max_bytes=None):
    if PERSISTENT_CACHE_ROOT.startswith("/media/hdd/") and not hdd_ready():
        return
    cfg = load_settings()
    if max_bytes is None:
        try: max_bytes = int(cfg.get("image_cache_mb",80)) * 1024 * 1024
        except Exception: max_bytes = 80 * 1024 * 1024
    # IMAGE_CACHE_DIR is the persistent last-resort portal-artwork store.
    # Do not accidentally treat it as a 128 MB transient cache.
    persistent_media = PERSISTENT_CACHE_ROOT.startswith("/media/hdd/")
    art_bytes = max_bytes
    if persistent_media and max_bytes:
        try: art_bytes = max(max_bytes, int(cfg.get("persistent_cache_mb",20480)) * 1024 * 1024)
        except Exception: art_bytes = max(max_bytes, 8192 * 1024 * 1024)
    requested_files=max_files
    if max_files is None:
        # Persistent HDD/USB artwork is byte-budgeted only. A hidden file-count
        # ceiling causes old posters/backdrops to disappear long before the
        # configured multi-GB budget is reached. Small fallback storage keeps a
        # conservative count limit.
        max_files = None if persistent_media else max(240, min(4000, int(art_bytes // (180 * 1024))))
    _cleanup_cache_dir(IMAGE_CACHE_DIR, max_files, art_bytes)
    # URL-keyed provider/TMDB source bytes are only staging. They must never
    # become a second persistent poster/backdrop library beside TMDB identity.
    source_bytes = 0 if max_bytes == 0 else max(16 * 1024 * 1024, int(max_bytes * 0.50))
    source_files = 0 if requested_files == 0 else max(160,min(2400,int(max(1,source_bytes)//(96*1024))))
    _cleanup_cache_dir(SOURCE_POSTER_CACHE_DIR, source_files, source_bytes)
    _cleanup_cache_dir(SOURCE_BACKDROP_CACHE_DIR, max(80,source_files//2) if source_files else 0, source_bytes)
    # release Persistent presentation vault:
    # On a real HDD, GENERATED contains the decoder-ready Cinematic backdrop,
    # title-logo/adaptive presentation assets and other Stable-Focus results.
    # These are part of the user's persistent library, not disposable thumbs.
    # Automatic maintenance must therefore never evict them merely because a
    # newer folder was cached.  Only an explicit Clear Cache request
    # (max_files=0,max_bytes=0) is allowed to remove this vault.
    explicit_clear = (requested_files == 0 and max_bytes == 0)
    generated_persistent = False
    try:
        _gen=os.path.abspath(PERSISTENT_GENERATED_DIR);_root=os.path.abspath(PERSISTENT_CACHE_ROOT)
        generated_persistent=bool(_gen==_root or _gen.startswith(_root+os.sep))
    except Exception:
        generated_persistent=False
    if (not generated_persistent) or explicit_clear:
        # PerfLab: view-specific derivatives are session/volatile assets. Keep
        # them under the normal image-cache budget instead of the multi-GB HDD
        # persistent budget used for canonical poster/backdrop originals.
        thumb_bytes = 0 if max_bytes == 0 else max(24 * 1024 * 1024, int(max_bytes * 0.75))
        if requested_files == 0:
            thumb_files=0
        else:
            thumb_files=max(400,min(6000,int(max(1,thumb_bytes)//(72*1024))))
        _cleanup_cache_dir(THUMB_CACHE_DIR, thumb_files, thumb_bytes)


def _thumb_path(digest, size):
    return os.path.join(THUMB_CACHE_DIR, "%s_%dx%d.jpg" % (digest, int(size[0]), int(size[1])))


def _valid_cache_file(path, ttl=IMAGE_CACHE_TTL):
    """Validate cached artwork while keeping the HDD hot path write-light.

    Persistent artwork is size/LRU managed rather than age-expired.  A cache
    hit should also be essentially read-only: touching atime on every rendered
    poster/backdrop wakes the disk and creates metadata I/O, so access time is
    refreshed at most once every ten minutes.
    """
    try:
        if path and os.path.abspath(str(path)).startswith(os.path.abspath(PERSISTENT_CACHE_ROOT) + os.sep) and not hdd_read_ready():
            return False
        stat = os.stat(path)
        if stat.st_size <= 100:
            return False
        now = time.time()
        if ttl not in (None, 0, False):
            age = max(0, now - stat.st_mtime)
            if age > float(ttl):
                return False
        # Generated presentation files are reproducible, but navigation can use
        # them heavily. Keep a write-light in-memory last-use ledger so the
        # background cache guard can evict stale assets instead of hot ones.
        try:
            abs_path=os.path.abspath(str(path))
            generated_root=os.path.abspath(PERSISTENT_GENERATED_DIR)
            if abs_path.startswith(generated_root+os.sep):
                note_generated_use(abs_path)
        except Exception:
            pass
        return True
    except OSError:
        return False


from .ui_dynamic_chrome import (
    configure_dynamic_chrome as _configure_dynamic_chrome,
    _build_dynamic_details_gradient,
    _build_dynamic_poster_accent,
    _build_dynamic_details_chrome,
    _build_aux_adaptive_chrome,
    _build_home_adaptive_focus,
    _build_category_adaptive_chrome,
    _build_category_extended_backdrop,
    _build_dynamic_settings_episode_rows,
    _build_poster_adaptive_chrome_clean,
    _build_live_adaptive_chrome_211,
    _build_home_mood_assets,
)
_configure_dynamic_chrome(
    THUMB_CACHE_DIR,
    _persistent_write_ok,
    _persistent_write_require,
    _valid_cache_file,
    CATEGORY_ADAPTIVE_TMP_DIR,
    ADAPTIVE_CHROME_DIR,
)



def _find_original_artwork(digest, landscape=False):
    """Resolve provider artwork from the one fixed HDD poster/backdrop store."""
    cache_key=("b:" if landscape else "p:")+str(digest)
    cached=_cached_artwork_path(cache_key) or (None if landscape else _cached_artwork_path(str(digest)))
    if cached:return cached
    base=SOURCE_BACKDROP_CACHE_DIR if landscape else SOURCE_POSTER_CACHE_DIR
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path=os.path.join(base,digest+ext)
        if _valid_cache_file(path):
            _cache_artwork_path(("b:" if landscape else "p:")+str(digest),path);return path
    # Lazy, non-destructive adoption of the old flat provider-art folder.
    legacy=os.path.join(PERSISTENT_CACHE_ROOT,"live")
    if os.path.abspath(legacy)!=os.path.abspath(base):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            source=os.path.join(legacy,digest+ext)
            if not _valid_cache_file(source):continue
            target=os.path.join(base,digest+ext)
            try:
                if _persistent_write_ok(target):
                    ensure_persistent_dirs(base)
                    try:os.replace(source,target)
                    except Exception:
                        import shutil as _us_shutil
                        _us_shutil.copy2(source,target)
                    if _valid_cache_file(target):source=target
            except Exception:pass
            _cache_artwork_path(("b:" if landscape else "p:")+str(digest),source);return source
    return None


def _build_thumbnail(source_path, target_path, size):
    if _PILImage is None:
        return None
    if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(target_path)):
        return None
    temp = target_path + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:
                image = _PILImageOps.exif_transpose(image)
            except Exception as exc:
                optional_failure("ui", exc)
            image = image.convert("RGB")
            resampling = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", 1)
            image.thumbnail((int(size[0]), int(size[1])), resampling)
            canvas = _PILImage.new("RGB", (int(size[0]), int(size[1])), (4, 8, 13))
            canvas.paste(image, ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2))
            if not _persistent_write_ok(temp): return None
            _persistent_write_require(temp);canvas.save(temp, "JPEG", quality=84, optimize=False, progressive=False)
        if not _persistent_write_ok(target_path): return None
        os.replace(temp, target_path)
        return target_path if _valid_cache_file(target_path) else None
    except Exception:
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):
                os.unlink(temp)
        except Exception as exc:
            optional_failure("ui", exc)
        return None


def _build_cover_thumbnail(source_path,target_path,size):
    """Sharp edge-to-edge poster renderer for the premium poster grid.

    The output is an exact-size PNG, not a JPEG.  It uses a true cover crop,
    mild post-resize sharpening and a top-corner alpha mask so the poster can
    touch the card frame without square pixels protruding through the rounded
    outer corners.
    """
    if _PILImage is None:return None
    if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(target_path)):return None
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui",exc)
            image=image.convert("RGB")
            tw,th=int(size[0]),int(size[1]);sw,sh=image.size
            if sw<=0 or sh<=0:raise ValueError("invalid poster dimensions")
            scale=max(float(tw)/sw,float(th)/sh)
            nw=max(tw,int(round(sw*scale)));nh=max(th,int(round(sh*scale)))
            resampling=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            image=image.resize((nw,nh),resampling)
            left=max(0,(nw-tw)//2);top=max(0,(nh-th)//2)
            image=image.crop((left,top,left+tw,top+th))
            # Restore micro-contrast lost by the downscale.  Keep it deliberately
            # light; oversharpening poster text produces halos on a TV panel.
            if _PILImageFilter is not None:
                try:image=image.filter(_PILImageFilter.UnsharpMask(radius=0.65,percent=115,threshold=2))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
            rgba=image.convert("RGBA")
            if ImageDraw is not None:
                mask=_PILImage.new("L",(tw,th),0);md=ImageDraw.Draw(mask);radius=16
                # Keep the image inside the 2px adaptive outer stroke.  The
                # transparent pixels are occupied by the chrome overlay, so this
                # is a frame edge, not a visible black gap.
                md.rounded_rectangle((2,2,tw-3,th-1),radius=radius,fill=255)
                # The poster/footer seam stays square and reaches the footer.
                md.rectangle((2,radius+2,tw-3,th-1),fill=255)
                rgba.putalpha(mask)
            _persistent_write_require(temp);rgba.save(temp,"PNG",compress_level=3,optimize=False)
        if not _persistent_write_ok(target_path):return None
        os.replace(temp,target_path)
        return target_path if _valid_cache_file(target_path,ttl=0) else None
    except Exception:
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as exc:optional_failure("ui.cover_thumb_cleanup",exc)
        return None


def _queue_thumbnail_build(source_path, target_path, size):
    if _PILImage is None or _valid_cache_file(target_path):
        return
    key = (target_path, int(size[0]), int(size[1]))
    with _THUMB_BUILDING_LOCK:
        if key in _THUMB_BUILDING:
            return
        # Derived thumbs are opportunistic. Refuse to grow an unbounded executor
        # backlog during frantic page flipping; the original artwork remains
        # usable and a later request can rebuild the thumb when pressure drops.
        if len(_THUMB_BUILDING) >= _THUMB_BUILDING_LIMIT:
            return
        _THUMB_BUILDING.add(key)
    def worker():
        try:
            _build_thumbnail(source_path, target_path, size)
        finally:
            with _THUMB_BUILDING_LOCK:
                _THUMB_BUILDING.discard(key)
    try:
        _MAINTENANCE_EXECUTOR.submit(worker)
    except Exception:
        with _THUMB_BUILDING_LOCK:
            _THUMB_BUILDING.discard(key)


def _artwork_attempt_allowed(url):
    now = time.time()
    with _ARTWORK_FAILURE_LOCK:
        state = _ARTWORK_FAILURES.get(url)
        if not state:
            return True
        attempts, retry_after = state
        if now >= retry_after:
            _ARTWORK_FAILURES.pop(url, None)
            return True
        return attempts < 2


def _record_artwork_failure(url):
    with _ARTWORK_FAILURE_LOCK:
        now=time.time()
        # Failed artwork URLs are diagnostic/backoff state, not a permanent
        # catalogue index. Bound the map so a long session through many dead
        # poster URLs cannot grow receiver RAM forever.
        if len(_ARTWORK_FAILURES) >= _ARTWORK_FAILURE_LIMIT:
            expired=[key for key,state in list(_ARTWORK_FAILURES.items()) if state and now >= state[1]]
            for key in expired[:256]:_ARTWORK_FAILURES.pop(key,None)
            while len(_ARTWORK_FAILURES) >= _ARTWORK_FAILURE_LIMIT:
                try:_ARTWORK_FAILURES.pop(next(iter(_ARTWORK_FAILURES)))
                except Exception:break
        attempts, _retry_after = _ARTWORK_FAILURES.get(url, (0, 0))
        attempts += 1
        # One automatic retry is permitted. After the second failure, cool down
        # the URL so page redraws do not hammer a dead artwork endpoint.
        retry_after = now + (2 if attempts < 2 else ARTWORK_FAILURE_TTL)
        _ARTWORK_FAILURES[url] = (attempts, retry_after)


def _clear_artwork_failure(url):
    with _ARTWORK_FAILURE_LOCK:
        _ARTWORK_FAILURES.pop(url, None)


_STARTUP_CACHE_INIT_LOCK = threading.RLock()
_STARTUP_CACHE_INIT_SCHEDULED = False
_STARTUP_CACHE_MAINT_DONE = False

def _schedule_startup_cache_maintenance():
    """Run cache initialization/maintenance once, with failure-safe retries.

    The scheduled flag means only that a worker is currently in flight. A
    separate completion flag prevents successful/recent maintenance from being
    repeated, while any mount race or failure becomes retryable on a later
    screen launch.
    """
    global _STARTUP_CACHE_INIT_SCHEDULED,_STARTUP_CACHE_MAINT_DONE
    with _STARTUP_CACHE_INIT_LOCK:
        if _STARTUP_CACHE_MAINT_DONE or _STARTUP_CACHE_INIT_SCHEDULED:
            return
        _STARTUP_CACHE_INIT_SCHEDULED=True
    def worker():
        global _STARTUP_CACHE_INIT_SCHEDULED,_STARTUP_CACHE_MAINT_DONE
        completed=False
        try:
            # Test69 Lean runtime: cache maintenance is background housekeeping,
            # never part of plugin first paint.  Give the user three minutes of
            # uncontended UI/network time before any full HDD walk can begin.
            time.sleep(180.0)
            if not initialize_persistent_cache_once():
                return
            marker=os.path.join(PERSISTENT_CACHE_ROOT,".maintenance_stamp")
            if not hdd_read_ready(force=True):
                return
            try:age=max(0.0,time.time()-os.path.getmtime(marker))
            except OSError:age=10**9
            if age < 6*60*60:
                completed=True
                return
            # Generated presentation assets are reproducible and now have their
            # own bounded guard. Posters/backdrops/library/index are deliberately
            # outside this maintenance path. The walk runs only in this delayed
            # background worker, never while the user is key-repeating through UI.
            try:
                maintain_generated_cache()
            except Exception as exc:
                optional_failure("generated-cache-maintenance",exc)
            temp=marker+".tmp.%d"%os.getpid()
            # R62: marker names are dot-files with no conventional extension.
            # persistent_write_gate() quite reasonably treats extensionless
            # targets as directories, so gating the marker path itself created
            # an empty `.maintenance_stamp/` directory.  The following
            # os.replace(temp, marker) then failed with EISDIR and startup cache
            # maintenance retried forever.  Gate the parent directory instead;
            # this is the actual write target we need to validate.
            marker_dir=os.path.dirname(marker) or PERSISTENT_CACHE_ROOT
            _persistent_write_require(marker_dir)
            # Clean up only the exact empty directory left by the old bug.
            # Never recurse and never delete a non-empty path.
            if os.path.isdir(marker):
                try:os.rmdir(marker)
                except OSError:
                    return
            with open(temp,"w",encoding="ascii") as handle:
                handle.write(str(int(time.time())))
                handle.flush();os.fsync(handle.fileno())
            _persistent_write_require(marker_dir)
            os.replace(temp,marker)
            try:os.chmod(marker,0o600)
            except OSError:pass
            completed=True
        except Exception as exc:
            optional_failure("cache-prune-startup",exc)
        finally:
            with _STARTUP_CACHE_INIT_LOCK:
                _STARTUP_CACHE_INIT_SCHEDULED=False
                if completed:
                    _STARTUP_CACHE_MAINT_DONE=True
    try:
        _MAINTENANCE_EXECUTOR.submit(worker)
    except Exception as exc:
        with _STARTUP_CACHE_INIT_LOCK:
            _STARTUP_CACHE_INIT_SCHEDULED=False
        optional_failure("cache-prune-schedule",exc)












def _portal_snapshot(item, media_type, poster_local=None, backdrop_local=None):
    """Build a trusted local snapshot from the portal payload itself.

    No fuzzy external identity is introduced here. This lets a complete Stalker
    row become a true HDD hot path even when the portal does not expose a TMDB
    or IMDb id. A later direct-ID TMDB enrichment may safely replace it.
    """
    item=item if isinstance(item,dict) else {}
    def split_names(value, limit):
        if isinstance(value,(list,tuple)):
            return [str(x).strip() for x in value[:limit] if str(x).strip()]
        text=str(value or "").strip()
        if not text:return []
        return [x.strip() for x in re.split(r"[,;/|]",text) if x.strip()][:limit]
    genres=split_names(item.get("genre") or item.get("genres") or item.get("category_name"),6)
    countries=split_names(item.get("country") or item.get("country_code") or item.get("countries"),3)
    cast=split_names(item.get("actors") or item.get("cast") or item.get("actor"),8)
    directors=split_names(item.get("director") or item.get("directors"),4)
    writers=split_names(item.get("writer") or item.get("writers") or item.get("creator"),5)
    title=str(item.get("original_name") or item.get("original_title") or item.get("name") or item.get("title") or "").strip()
    year=str(item.get("year") or item.get("release_year") or "").strip()
    overview=str(item.get("description") or item.get("descr") or item.get("plot") or "").strip()
    tmdb_id=item.get("tmdb_id") or item.get("tmdbid")
    imdb_id=item.get("imdb_id") or item.get("imdb")
    overlay={
        "year":year,"country":countries[0] if countries else "","genre":" / ".join(genres[:3]),
        "description":overview,"actors":", ".join(cast[:6]),"director":", ".join(directors[:3]),
        "writer":", ".join(writers[:4]),"number_of_episodes":item.get("number_of_episodes") or item.get("episodes_count"),
        "number_of_seasons":item.get("number_of_seasons") or item.get("seasons_count"),
    }
    is_provider=bool(item.get("_xtream"))
    return {
        "matched":True,"identity_verified":True,"identity_source":("provider_payload" if is_provider else "portal_payload"),"identity_evidence":4,
        "source":("XTREAM_PROVIDER" if is_provider else "PORTAL"),"media_type":str(media_type or ""),"confidence":1.0,"title":title,"year":year,
        "overview":overview,"genres":genres,"countries":countries,"cast":cast,"directors":directors,"writers":writers,
        "number_of_episodes":item.get("number_of_episodes") or item.get("episodes_count"),
        "number_of_seasons":item.get("number_of_seasons") or item.get("seasons_count"),
        "runtime":item.get("time") or item.get("duration") or item.get("length"),
        "rating":item.get("rating") or item.get("rating_imdb") or item.get("imdb_rating"),
        "tmdb_id":tmdb_id,"imdb_id":imdb_id,"poster_local":poster_local,"backdrop_local":backdrop_local,
        "item_overlay":{k:v for k,v in overlay.items() if v not in (None,"",[],{})},
    }

class _ArtworkCancelled(Exception):
    pass



def _source_art_url(value, profile, item=None):
    value=_first_art_value(value)
    if not value:return None
    value=str(value).strip()
    if value.startswith("//"):
        base=str((item or {}).get("_art_base") or (profile or {}).get("portal") or "http://")
        scheme=urllib.parse.urlsplit(base).scheme or "http"
        return scheme+":"+value
    if value.startswith(("http://","https://")):
        return _normalize_provider_image_url(value)
    base=str((item or {}).get("_art_base") or (profile or {}).get("portal") or "")
    return _normalize_provider_image_url(urllib.parse.urljoin(base.rstrip("/")+"/",value.lstrip("/")))

def _portal_art_url(value, profile):
    value=_first_art_value(value)
    if not value:return None
    value=str(value).strip()
    portal=str((profile or {}).get("portal") or "")
    if value.startswith("//"):
        scheme=urllib.parse.urlsplit(portal or "http://").scheme or "http"
        return scheme+":"+value
    if value.startswith(("http://","https://")):
        return value
    return urllib.parse.urljoin(portal.rstrip("/")+"/",value.lstrip("/"))



def _cached_portal_artwork(value, profile, landscape=False):
    """Return the original cached portal artwork, never a UI derivative."""
    try:
        url=_portal_art_url(value,profile)
        if not url:return None
        url=_optimized_artwork_url(url,bool(landscape))
        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
        path=_find_original_artwork(digest,bool(landscape))
        if not path:return None
        if landscape and _PILImage is not None:
            try:
                with _PILImage.open(path) as im:
                    if float(im.width)/float(max(1,im.height))<1.25:return None
            except Exception:return None
        return path
    except Exception:
        return None


def _live_picon_diag(message):
    # Picon diagnostics are useful when explicitly enabled, but synchronous
    # trace-file writes do not belong on the normal Live rendering path.
    try:
        if not LOG.isEnabledFor(10):
            return
    except Exception:
        return
    try:
        root="/tmp/UltraStalker"
        try:os.makedirs(root,0o700,exist_ok=True)
        except TypeError:
            try:os.makedirs(root,0o700)
            except OSError:pass
        safe_message=_redact_log_value(message)
        line="%s %s\n"%(time.strftime("%Y-%m-%d %H:%M:%S"),safe_message)
        path=os.path.join(root,"live_picon_diag.log")
        with open(path,"a") as handle:handle.write(line)
        try:os.chmod(root,0o700)
        except OSError:pass
        try:os.chmod(path,0o600)
        except OSError:pass
        try:LOG.info("LIVE_PICON_DIAG %s",safe_message)
        except Exception as exc:diagnostic_failure("ui.failsoft.1533",exc)
    except Exception as exc:
        diagnostic_failure("ui.failsoft.live_picon_diag",exc)

def _cached_live_picon_path(value, profile, item=None):
    """Return an already cached Live picon without touching network state."""
    try:
        url=_source_art_url(value,profile,item)
        if not url:return None
        url=_normalize_provider_image_url(url)
        if not url:return None
        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
        root=os.path.join(ARTWORK_CACHE_ROOT,"live_picons")
        for ext in (".png",".jpg",".webp"):
            path=os.path.join(root,digest+ext)
            try:
                if os.path.isfile(path) and os.path.getsize(path)>256:return path
            except OSError:
                pass
    except Exception as exc:
        diagnostic_failure("ui.failsoft.live_picon_cache",exc)
    return None



def _live_picon_failed_queue_path(profile):
    """Persistent retry queue. One tiny JSON file per provider/profile."""
    try:
        ident="|".join([
            str((profile or {}).get("portal") or ""),
            str((profile or {}).get("url") or ""),
            str((profile or {}).get("username") or ""),
            str((profile or {}).get("mac") or ""),
        ])
        key=hashlib.sha1(ident.encode("utf-8","ignore")).hexdigest()[:16]
        root=os.path.join(ARTWORK_CACHE_ROOT,"live_picons")
        if not os.path.isdir(root):
            os.makedirs(root,exist_ok=True)
        return os.path.join(root,"failed_%s.json"%key)
    except Exception:
        return ""

def _load_live_picon_failed_queue(profile):
    path=_live_picon_failed_queue_path(profile)
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path,"r",encoding="utf-8") as fh:
            data=json.load(fh)
        rows=[]
        for row in (data if isinstance(data,list) else []):
            if not isinstance(row,dict): continue
            resolved=str(row.get("url") or "")
            item=row.get("item") if isinstance(row.get("item"),dict) else {}
            if resolved: rows.append((item,resolved))
        return rows
    except Exception as exc:
        optional_failure("ui.live_picon_failed_queue_load",exc)
        return []

def _save_live_picon_failed_queue(profile,rows):
    path=_live_picon_failed_queue_path(profile)
    if not path:return
    try:
        if not rows:
            try: os.unlink(path)
            except OSError: pass
            return
        tmp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
        payload=[{"url":resolved,"item":item if isinstance(item,dict) else {}} for item,resolved in rows]
        with open(tmp,"w",encoding="utf-8") as fh:
            json.dump(payload,fh,ensure_ascii=False,separators=(",",":"))
            fh.flush()
            try: os.fsync(fh.fileno())
            except Exception as exc:diagnostic_failure("ui.failsoft.1607",exc)
        try: os.chmod(tmp,0o600)
        except OSError: pass
        os.replace(tmp,path)
    except Exception as exc:
        optional_failure("ui.live_picon_failed_queue_save",exc)

def _download_public_live_picon(value, profile, item=None, timeout=4.0):
    """Download one public Live picon with standard urllib HTTPS only.

    This intentionally does not accept/use a Stalker or Xtream client. The source
    client is needed to enumerate channels, not to fetch a public CDN image.
    """
    try:
        url=_source_art_url(value,profile,item)
        if not url:return None
        url=_normalize_provider_image_url(url)
        if not url:return None
        parts=urllib.parse.urlsplit(url)
        if parts.scheme not in ("http","https") or not parts.hostname:return None

        existing=_cached_live_picon_path(url,profile,item)
        if existing:return existing

        root=os.path.join(ARTWORK_CACHE_ROOT,"live_picons")
        try:os.makedirs(root,0o700,exist_ok=True)
        except TypeError:
            try:os.makedirs(root,0o700)
            except OSError:pass

        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
        host=parts.hostname
        if parts.port:host+=":%d"%parts.port
        image_profile={"portal":"%s://%s/"%(parts.scheme,host)}
        headers=_safe_image_headers(url,image_profile,None,None)
        req=urllib.request.Request(url,headers=headers)
        # Public/CDN picons are untrusted media. Use the DNS-pinned opener and
        # validate every redirect. The configured portal host is trusted only
        # for RFC1918/ULA deployments; loopback/link-local remain blocked.
        trusted_origins=_trusted_image_origins(profile)
        opener=build_safe_media_opener(trusted_private_origins=trusted_origins)
        tmp=os.path.join(root,digest+".bulk.%d.%d"%(os.getpid(),threading.get_ident()))
        total=0;head=b""
        try:
            with opener.open(req,timeout=max(2.0,min(float(timeout or 4.0),6.0))) as response,open(tmp,"wb") as handle:
                while True:
                    chunk=response.read(64*1024)
                    if not chunk:break
                    if not head:head=chunk[:16]
                    total+=len(chunk)
                    if total>2*1024*1024:raise ValueError("live picon exceeds 2 MB")
                    handle.write(chunk)
            if total<=128:raise ValueError("empty live picon")
            if head.startswith(b"\x89PNG\r\n\x1a\n"):ext=".png"
            elif head.startswith(b"\xff\xd8\xff"):ext=".jpg"
            elif head[:4]==b"RIFF" and head[8:12]==b"WEBP":ext=".webp"
            else:ext=""
            if not ext:
                if _PILImage is None:raise ValueError("unsupported live picon format")
                target=os.path.join(root,digest+".png")
                with _PILImage.open(tmp) as im:
                    im.seek(0)
                    im=im.convert("RGBA" if "A" in im.getbands() else "RGB")
                    im.save(target,"PNG",optimize=False)
                try:os.unlink(tmp)
                except OSError:pass
            else:
                target=os.path.join(root,digest+ext)
                os.replace(tmp,target)
            return target if os.path.isfile(target) and os.path.getsize(target)>256 else None
        except Exception as exc:
            try:
                if os.path.exists(tmp):os.unlink(tmp)
            except OSError:pass
            _live_picon_diag("PUBLIC_FAIL channel=%r url=%r error=%r"%(str((item or {}).get("name") or ""),url,exc))
            return None
    except Exception as exc:
        _live_picon_diag("PUBLIC_SETUP_FAIL channel=%r raw=%r error=%r"%(str((item or {}).get("name") or ""),value,exc))
        return None


def _download_live_portal_temp_picon(value, profile, client, item=None, timeout=5.5):
    """Download a Live picon as disposable Portal-style session artwork.

    M3U/Xtream Live logos intentionally bypass the persistent artwork/HDD
    pipeline, but still use the shared hardened media opener. The configured
    portal remains the only credential-bearing trust origin; external artwork
    hosts receive only credential-free browser identity headers.
    """
    try:
        url=_source_art_url(value,profile,item)
        _live_picon_diag("DOWNLOAD_START channel=%r raw=%r resolved=%r"%(str((item or {}).get("name") or ""),value,url))
        if not url:
            _live_picon_diag("DOWNLOAD_NO_URL channel=%r raw=%r"%(str((item or {}).get("name") or ""),value))
            return None
        url=_normalize_provider_image_url(url)
        if not url:return None
        parts=urllib.parse.urlsplit(url)
        if parts.scheme not in ("http","https") or not parts.hostname:return None
        portal_item={"logo":url,"logo_url":url,"picon":url,"stream_icon":url}
        temp_root=os.path.join(ARTWORK_CACHE_ROOT,"live_picons")
        try:os.makedirs(temp_root,0o700,exist_ok=True)
        except TypeError:
            try:os.makedirs(temp_root,0o700)
            except OSError:pass
        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
        # Persistent Live picon cache: one successful HTTPS download is reused across entries/restarts.
        existing=_cached_live_picon_path(value,profile,item)
        if existing:return existing
        # Keep the configured portal as the credential trust origin. Never
        # promote an artwork/CDN host to portal trust merely because it supplied
        # the image URL; Stalker MAC/token headers must remain same-origin only.
        headers=_safe_image_headers(url,profile,client,portal_item)
        req=urllib.request.Request(url,headers=headers)
        # Validate and DNS-pin every destination, including redirects. This
        # blocks receiver-side SSRF/DNS rebinding while still allowing the
        # configured portal host when it legitimately lives on a private LAN.
        opener=_safe_image_opener(url,profile)
        tmp=os.path.join(temp_root,digest+".download.%d.%d"%(os.getpid(),threading.get_ident()))
        total=0;head=b"";ctype=""
        try:
            with opener.open(req,timeout=max(2.0,min(float(timeout or 5.5),7.0))) as response,open(tmp,"wb") as handle:
                ctype=(response.headers.get("Content-Type") or "").lower()
                try:status=getattr(response,"status",None) or response.getcode()
                except Exception:status=None
                _live_picon_diag("HTTP channel=%r status=%r ctype=%r url=%r"%(str((item or {}).get("name") or ""),status,ctype,url))
                while True:
                    chunk=response.read(64*1024)
                    if not chunk:break
                    if not head:head=chunk[:16]
                    total+=len(chunk)
                    if total>2*1024*1024:raise ValueError("live picon exceeds 2 MB")
                    handle.write(chunk)
            if total<=128:raise ValueError("empty live picon")
            if head.startswith(b"\x89PNG\r\n\x1a\n"):ext=".png"
            elif head.startswith(b"\xff\xd8\xff"):ext=".jpg"
            elif head[:4]==b"RIFF" and head[8:12]==b"WEBP":ext=".webp"
            else:ext=""
            if not ext:
                if _PILImage is None:raise ValueError("unsupported live picon format")
                target=os.path.join(temp_root,digest+".png")
                with _PILImage.open(tmp) as im:
                    im.seek(0);im=im.convert("RGBA" if "A" in im.getbands() else "RGB");im.save(target,"PNG",optimize=False)
                try:os.unlink(tmp)
                except OSError:pass
            else:
                target=os.path.join(temp_root,digest+ext)
                os.replace(tmp,target)
            _live_picon_diag("DOWNLOAD_OK channel=%r path=%r bytes=%r exists=%r"%(str((item or {}).get("name") or ""),target,total,os.path.isfile(target)))
            try:LOG.info("Live picon TEMP portal success channel=%r url=%r path=%s bytes=%s ctype=%r",str((item or {}).get("name") or ""),url,target,total,ctype)
            except Exception as exc:diagnostic_failure("ui.failsoft.1757",exc)
            return target
        except Exception:
            try:
                if os.path.exists(tmp):os.unlink(tmp)
            except OSError:pass
            raise
    except Exception as exc:
        _live_picon_diag("DOWNLOAD_FAIL channel=%r raw=%r error=%r"%(str((item or {}).get("name") or ""),value,exc))
        try:LOG.exception("Live picon TEMP portal failed channel=%r value=%r error=%s",str((item or {}).get("name") or ""),value,exc)
        except Exception as exc:diagnostic_failure("ui.failsoft.1767",exc)
        return None

def _download_portal_artwork(value, profile, client, landscape=False, timeout=3.5, cancel_event=None, item=None, trace_cb=None, force_attempt=False):
    """HDD-first download for artwork supplied directly by the Stalker portal.

    This path deliberately runs before TMDB enrichment. It shares the same
    persistent digest store as the grid/details loaders, so a prefetched image
    becomes an immediate local hit everywhere else in the plugin.
    """
    if cancel_event is not None and cancel_event.is_set():return None
    raw_value=value
    url=_source_art_url(value,profile,item)
    if not url:
        if callable(trace_cb):
            try:trace_cb("ARTWORK_SKIP reason=source_url raw=%r"%(str(raw_value or "")[:220],))
            except Exception:pass
        return None
    url=_optimized_artwork_url(url,bool(landscape))
    if not url:
        if callable(trace_cb):
            try:trace_cb("ARTWORK_SKIP reason=optimized_url raw=%r"%(str(raw_value or "")[:220],))
            except Exception:pass
        return None
    digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
    with _artwork_file_lock(digest):
        existing=_find_original_artwork(digest,bool(landscape))
        if existing:
            if not landscape or _PILImage is None:return existing
            try:
                with _PILImage.open(existing) as im:
                    if float(im.width)/float(max(1,im.height))>=1.25:return existing
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        if not force_attempt and not _artwork_attempt_allowed(url):
            if callable(trace_cb):
                try:trace_cb("ARTWORK_SKIP reason=backoff url=%r"%(url,))
                except Exception:pass
            return None
        temp=None
        try:
            store_dir=SOURCE_BACKDROP_CACHE_DIR if landscape else SOURCE_POSTER_CACHE_DIR
            if not _persistent_write_ok(store_dir): return None
            if cancel_event is not None and cancel_event.is_set():raise _ArtworkCancelled()
            headers=_safe_image_headers(url,profile,client,item)
            req=urllib.request.Request(url,headers=headers)
            temp=os.path.join(store_dir,"%s.download.%d.%d"%(digest,os.getpid(),threading.get_ident()))
            total=0;head=b"";ctype="";transport="safe"
            _persistent_write_require(temp)
            # Provider artwork URLs are provider-controlled resources. Xtream/M3U
            # artwork in particular may legitimately live on a private/sibling CDN
            # that the strict generic remote-media opener rejects before any HTTP
            # request is made. Try the strict transport first, then the provider
            # transport that is already used for user-configured Xtream/M3U URLs.
            # Credentials are still protected by _safe_image_headers() and the
            # ProviderRedirectHandler strips credential-like headers cross-origin.
            try:
                opener=_safe_image_opener(url,profile)
                response_ctx=opener.open(req,timeout=max(2.0,min(float(timeout or 3.5),6.5)))
            except Exception as first_exc:
                transport="provider"
                if callable(trace_cb):
                    try: trace_cb("ARTWORK_OPEN_RETRY url=%r first=%r"%(url,str(first_exc)[:180]))
                    except Exception: pass
                response_ctx=provider_urlopen(req,timeout=max(2.0,min(float(timeout or 3.5),6.5)))
            with response_ctx as response, open(temp,"wb") as handle:
                ctype=(response.headers.get("Content-Type") or "").lower()
                if callable(trace_cb):
                    try: trace_cb("ARTWORK_HTTP url=%r transport=%s status=%r ctype=%r final=%r"%(url,transport,getattr(response,"status",None),ctype,getattr(response,"geturl",lambda:url)()))
                    except Exception: pass
                while True:
                    if cancel_event is not None and cancel_event.is_set():raise _ArtworkCancelled()
                    chunk=response.read(64*1024)
                    if not chunk:break
                    if not head:head=chunk[:16]
                    total+=len(chunk)
                    if total>5*1024*1024:raise ValueError("portal artwork exceeds 5 MB")
                    handle.write(chunk)
                handle.flush()
            if total<=256:raise ValueError("empty portal artwork")
            if head.startswith(b"\x89PNG\r\n\x1a\n"):ext=".png"
            elif head.startswith(b"\xff\xd8\xff"):ext=".jpg"
            elif head[:4]==b"RIFF" and head[8:12]==b"WEBP":ext=".webp"
            elif head[:6] in (b"GIF87a",b"GIF89a"):ext=".gif"
            elif head[:2]==b"BM":ext=".bmp"
            elif "png" in ctype:ext=".png"
            elif "jpeg" in ctype or "jpg" in ctype:ext=".jpg"
            elif "webp" in ctype:ext=".webp"
            elif "gif" in ctype:ext=".gif"
            elif "bmp" in ctype:ext=".bmp"
            else:ext=""
            # Normalize formats that Enigma2/ePicLoad may reject, and accept
            # image/octet-stream responses after actual image validation.
            if ext in (".gif",".bmp") or not ext:
                if _PILImage is None:raise ValueError("unsupported provider artwork")
                target=os.path.join(store_dir,digest+".png")
                if not _persistent_write_ok(target): return None
                with _PILImage.open(temp) as im:
                    im.seek(0)
                    im=im.convert("RGBA" if "A" in im.getbands() else "RGB")
                    _persistent_write_require(target);im.save(target,"PNG",optimize=False)
                os.unlink(temp);temp=None
            else:
                target=os.path.join(store_dir,digest+ext)
                if not _persistent_write_ok(target): return None
                _persistent_write_require(target);os.replace(temp,target);temp=None
            _cache_artwork_path(("b:" if landscape else "p:")+str(digest),target)
            if landscape and _PILImage is not None:
                try:
                    with _PILImage.open(target) as im:
                        if float(im.width)/float(max(1,im.height))<1.25:return None
                except Exception:return None
            _clear_artwork_failure(url)
            if callable(trace_cb):
                try: trace_cb("ARTWORK_OK url=%r path=%r bytes=%d ctype=%r"%(url,target,total,ctype))
                except Exception: pass
            return target
        except _ArtworkCancelled:
            return None
        except Exception as exc:
            if callable(trace_cb):
                try: trace_cb("ARTWORK_FAIL url=%r error=%r"%(url,str(exc)[:240]))
                except Exception: pass
            _record_artwork_failure(url)
            return None
        finally:
            if temp:
                try:
                    if _persistent_write_ok(temp): os.unlink(temp)
                except OSError:pass


def _build_ambient_backdrop(source_path, target_path, size=(1920,1080)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    """Poster fallback without showing a giant blurred poster.

    Collapse artwork to a tiny colour field, then upscale/blur it so only the
    palette survives. This provides atmosphere but no recognisable enlarged
    faces/text from the poster.
    """
    if _PILImage is None or not source_path or not os.path.isfile(source_path):return None
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui",exc)
            image=image.convert("RGB").resize((8,5),getattr(getattr(_PILImage,"Resampling",_PILImage),"BOX",4))
            resampling=getattr(getattr(_PILImage,"Resampling",_PILImage),"BICUBIC",3)
            image=image.resize((int(size[0]),int(size[1])),resampling)
            if _PILImageFilter is not None:image=image.filter(_PILImageFilter.GaussianBlur(68))
            # Deep navy filmic grade; preserve only broad poster palette.
            shade=_PILImage.new("RGBA",size,(2,7,12,112))
            image=_PILImage.alpha_composite(image.convert("RGBA"),shade).convert("RGB")
            _persistent_write_require(temp);image.save(temp,"JPEG",quality=88,optimize=False,progressive=False)
        _persistent_write_require(target_path);os.replace(temp,target_path)
        return target_path if os.path.isfile(target_path) and os.path.getsize(target_path)>100 else None
    except Exception:
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as exc:optional_failure("ui",exc)
        return None















def _compose_information_text(item=None, fallback="", parent=None):
    """Return a robust information payload for the Yellow-key overlay.

    Prefer real portal/TMDB metadata from the selected item, then the parent
    series/movie, then the already-rendered synopsis as a final fallback.
    """
    def _pick(obj, keys):
        if not isinstance(obj, dict):
            return ""
        for key in keys:
            value = obj.get(key)
            if value not in (None, "", [], {}):
                if isinstance(value, (list, tuple)):
                    value = ", ".join([str(v) for v in value if v not in (None, "")])
                elif isinstance(value, dict):
                    value = ", ".join([str(v) for v in value.values() if v not in (None, "")])
                text = str(value).strip()
                if text:
                    return text
        return ""

    description_keys = (
        "overview", "description", "plot", "plot_outline", "synopsis",
        "descr", "info", "storyline", "summary", "comment"
    )
    description = _pick(item, description_keys) or _pick(parent, description_keys) or str(fallback or "").strip()
    lines=[]
    if description:
        lines.append(description)

    meta_sources=[item if isinstance(item,dict) else {}, parent if isinstance(parent,dict) else {}]
    meta_specs=(
        (_("Year"), ("year","release_year","first_air_date","release_date")),
        (_("Genre"), ("genres","genre","category")),
        (_("Country"), ("country","origin_country","production_country")),
        (_("Rating"), ("vote_average","rating","imdb_rating")),
        (_("Director"), ("director","directors")),
        (_("Cast"), ("cast","actors","actor")),
    )
    used=set()
    meta_lines=[]
    for label,keys in meta_specs:
        value=""
        for src in meta_sources:
            value=_pick(src,keys)
            if value:
                break
        if value and value not in used:
            used.add(value)
            meta_lines.append("%s: %s"%(label,value))
    if meta_lines:
        if lines:
            lines.append("")
        lines.extend(meta_lines)
    return "\n".join(lines).strip() or _("No programme description")



def _information_sections(item=None, parent=None, fallback=""):
    """Return Overview/Cast/Director/Writer text from portal + TMDB payloads."""
    def _as_text(value):
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, dict):
            # TMDB/IMDb sometimes nests credits under dictionaries.
            vals=[]
            for v in value.values():
                t=_as_text(v)
                if t and t not in vals: vals.append(t)
            return ", ".join(vals)
        if isinstance(value, (list, tuple, set)):
            vals=[]
            for v in value:
                if isinstance(v, dict):
                    v=v.get("name") or v.get("title") or v.get("original_name") or ""
                t=str(v or "").strip()
                if t and t not in vals: vals.append(t)
            return ", ".join(vals)
        return str(value).strip()

    def _pick(keys):
        sources=[]
        for src in (item, parent):
            if isinstance(src, dict):
                sources.append(src)
                imdb=src.get("imdb")
                if isinstance(imdb, dict): sources.append(imdb)
        for src in sources:
            for key in keys:
                t=_as_text(src.get(key))
                if t: return t
        return ""

    overview=_pick(("overview","description","plot","plot_outline","synopsis","descr","storyline","summary","comment")) or str(fallback or "").strip()
    cast=_pick(("cast","actors","actor"))
    director=_pick(("directors","director"))
    writer=_pick(("writers","writer","creator","creators"))
    return {
        "overview": overview or "No description available.",
        "cast": cast or "N/A",
        "director": director or "N/A",
        "writer": writer or "N/A",
    }


from .ui_icon_menu import (
    configure_icon_menu as _configure_icon_menu,
    IconMenuList,
)
_configure_icon_menu(
    ASSET_DIR=ASSET_DIR,
    MenuList=MenuList,
    MultiContentEntryPixmapAlphaTest=MultiContentEntryPixmapAlphaTest,
    MultiContentEntryPixmapAlphaBlend=MultiContentEntryPixmapAlphaBlend,
    MultiContentEntryText=MultiContentEntryText,
    RT_HALIGN_CENTER=RT_HALIGN_CENTER,
    RT_HALIGN_LEFT=RT_HALIGN_LEFT,
    RT_HALIGN_RIGHT=RT_HALIGN_RIGHT,
    RT_VALIGN_CENTER=RT_VALIGN_CENTER,
    _clean_display_text=_clean_display_text,
    asset=asset,
    cached_png=cached_png,
    eListboxPythonMultiContent=eListboxPythonMultiContent,
    gFont=gFont,
    optional_failure=optional_failure,
    os=os,
)

from .ui_screens_aux import (
    configure_aux_screens as _configure_aux_screens,
    AdaptiveInformationScreen,
    DownloadsManagerScreen,
)
_configure_aux_screens(IconMenuList, _information_sections)










def _build_cinematic_backdrop(source_path, target_path, size=(1920,1080)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return None
    temp = target_path + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:image = _PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui",exc)
            image = image.convert("RGB")
            source_ratio=float(image.width)/max(1.0,float(image.height))
            tw, th = int(size[0]), int(size[1])
            resampling = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", 1)
            scale = max(float(tw) / max(1, image.width), float(th) / max(1, image.height))
            nw, nh = max(tw, int(image.width * scale)), max(th, int(image.height * scale))
            image = image.resize((nw, nh), resampling)
            left=max(0,(nw-tw)//2); top=max(0,(nh-th)//2)
            image=image.crop((left,top,left+tw,top+th))
            # Only true landscape artwork is accepted as a cinematic backdrop.
            # Portrait images are handled by the neutral ambient fallback.
            if source_ratio < 1.28:
                return None
            if _PILImageFilter is not None:
                try:image=image.filter(_PILImageFilter.UnsharpMask(radius=1.0,percent=110,threshold=3))
                except Exception as exc:optional_failure("ui",exc)
            shade=_PILImage.new("RGBA",(tw,th),(1,5,9,18))
            image=_PILImage.alpha_composite(image.convert("RGBA"),shade).convert("RGB")
            _persistent_write_require(temp);image.save(temp,"JPEG",quality=91,optimize=False,progressive=False)
        _persistent_write_require(target_path);os.replace(temp,target_path)
        return target_path if os.path.isfile(target_path) and os.path.getsize(target_path)>100 else None
    except Exception:
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as exc:optional_failure("ui",exc)
        return None



def _build_cinematic_edge_overlay(source_path, target_path, size=(1620,910)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    """Create a lightweight adaptive veil around a raw TMDB backdrop.

    The backdrop itself is never decoded by Pillow.  This PNG contains only
    adaptive colour/alpha gradients and sits above the Enigma2-decoded JPEG,
    hiding hard picture borders while preserving a fully sharp centre.
    """
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return None
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        primary,secondary=_dynamic_palette(source_path)
        tw,th=map(int,size)
        out=_PILImage.new("RGBA",(tw,th),(0,0,0,0))
        px=out.load()
        # Match the dark adaptive page rather than black.  The left side needs
        # the longest blend because the poster/details chrome lives there.
        base=_mix_rgb((3,8,13),primary,0.24)
        base2=_mix_rgb((3,8,13),secondary,0.15)
        for y in range(th):
            by=0.0
            if y < 36: by=(36-y)/36.0*0.32
            if y > 500: by=max(by,min(1.0,(y-500)/260.0))
            for x in range(tw):
                lx=max(0.0,1.0-x/290.0)
                rx=max(0.0,(x-(tw-145))/145.0)
                edge=max(lx,rx,by)
                if edge<=0.001: continue
                # Smooth alpha: centre remains untouched; edges dissolve fully.
                edge=edge*edge*(3.0-2.0*edge)
                a=int(min(238,edge*226))
                t=min(1.0,max(0.0,y/float(max(1,th-1))))
                col=_mix_rgb(base,base2,t*0.45)
                px[x,y]=(col[0],col[1],col[2],a)
        if _PILImageFilter is not None:
            try: out=out.filter(_PILImageFilter.GaussianBlur(radius=10))
            except Exception as exc: optional_failure("ui.silent_guard",exc)
        _persistent_write_require(temp);out.save(temp,"PNG",optimize=False)
        _persistent_write_require(target_path);os.replace(temp,target_path)
        return target_path if os.path.isfile(target_path) and os.path.getsize(target_path)>100 else None
    except Exception as exc:
        optional_failure("ui.cinematic_edge_overlay",exc)
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp): os.unlink(temp)
        except Exception as exc: optional_failure("ui.silent_guard",exc)
        return None


def _build_integrated_backdrop(source_path, target_path, size=(1620,620)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    """Build a frameless details backdrop using real alpha feathering.

    The centre stays fully opaque and sharp. The edges become progressively
    transparent, exposing the actual dynamic background underneath rather than
    a guessed flat colour. This keeps the two-layer design while hiding seams.
    """
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return None
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui",exc)
            image=image.convert("RGB")
            if float(image.width)/max(1.0,float(image.height)) < 1.20:return None
            tw,th=int(size[0]),int(size[1])
            resampling=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            scale=max(float(tw)/max(1,image.width),float(th)/max(1,image.height))
            nw=max(1,int(round(image.width*scale)));nh=max(1,int(round(image.height*scale)))
            image=image.resize((nw,nh),resampling)
            left=max(0,(nw-tw)//2);top=max(0,int((nh-th)*0.12))
            image=image.crop((left,top,left+tw,top+th))

            left_fade=max(165,int(tw*.14));right_fade=max(260,int(tw*.20))
            top_fade=max(34,int(th*.065));bottom_fade=max(155,int(th*.28))
            hvals=[]
            for x in range(tw):
                a=255
                if x<left_fade:
                    q=x/float(max(1,left_fade-1));a=int(255*(q**1.65))
                elif x>=tw-right_fade:
                    q=(tw-1-x)/float(max(1,right_fade-1));a=int(255*(max(0.0,q)**1.55))
                hvals.append(max(0,min(255,a)))
            vvals=[]
            for y in range(th):
                a=255
                if y<top_fade:
                    q=y/float(max(1,top_fade-1));a=int(255*(q**1.45))
                elif y>=th-bottom_fade:
                    q=(th-1-y)/float(max(1,bottom_fade-1));a=int(255*(max(0.0,q)**1.55))
                vvals.append(max(0,min(255,a)))
            hmask=_PILImage.new("L",(tw,1));hmask.putdata(hvals);hmask=hmask.resize((tw,th))
            vmask=_PILImage.new("L",(1,th));vmask.putdata(vvals);vmask=vmask.resize((tw,th))
            alpha=_PILImageChops.multiply(hmask,vmask) if _PILImageChops is not None else _PILImage.composite(hmask,vmask,hmask)

            # Blur only the area that is already fading. No loss of centre detail.
            if _PILImageFilter is not None and _PILImageChops is not None:
                try:
                    blurred=image.filter(_PILImageFilter.GaussianBlur(radius=5.5))
                    blurmask=_PILImageChops.invert(alpha).point(lambda p:int(p*.78))
                    image=_PILImage.composite(blurred,image,blurmask)
                except Exception as exc:optional_failure("ui.details_edge_blur",exc)

            rgba=image.convert("RGBA");rgba.putalpha(alpha)
            _persistent_write_require(temp);rgba.save(temp,"PNG",optimize=False)
        _persistent_write_require(target_path);os.replace(temp,target_path)
        return target_path if os.path.isfile(target_path) and os.path.getsize(target_path)>100 else None
    except Exception:
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as exc:optional_failure("ui",exc)
        return None


def _serialized_artwork_builder(func):
    def wrapped(source_path, target_path, *args, **kwargs):
        with _artwork_target_lock(target_path):
            with image_work(getattr(func,"__name__","artwork")):
                return func(source_path, target_path, *args, **kwargs)
    wrapped.__name__ = getattr(func, "__name__", "artwork_builder")
    return wrapped

# Multiple UI paths may prefetch/build the same derived artwork concurrently.
# Serialize by final target and keep unique temp files so no thread can unlink
# another thread's in-progress output.
_build_thumbnail = _serialized_artwork_builder(_build_thumbnail)
_build_ambient_backdrop = _serialized_artwork_builder(_build_ambient_backdrop)
_build_cinematic_backdrop = _serialized_artwork_builder(_build_cinematic_backdrop)
_build_integrated_backdrop = _serialized_artwork_builder(_build_integrated_backdrop)

# Home artwork generation is memory-heavy on Enigma2 receivers.  Serialize all
# Home-derived Pillow work so a hero, ambient, glass and focus render can never
# allocate large RGBA buffers at the same time.
# R64: legacy Hero-dependent application-chrome writers are permanently disabled.
# Poster/content adaptive builders remain untouched elsewhere.
def _build_home_adaptive_focus_safe(source_path, cache_key):
    return {}

def _build_home_mood_assets_safe(source_path, cache_key):
    return {}

_build_home_adaptive_focus = _build_home_adaptive_focus_safe
_build_home_mood_assets = _build_home_mood_assets_safe

def _home_cached_art(item, profile, size, placeholder, media_type=""):
    """Return already-persisted Home card art without network or image work.

    Home is a resume surface, so previously displayed artwork must be visible on
    first paint.  This helper deliberately performs HDD/RAM lookups only: no
    provider request, no TMDB resolve and no thumbnail generation.
    """
    if not isinstance(item,dict):
        return asset(placeholder)
    mtype=str(media_type or item.get("_saved_media_type") or "").lower()
    try:
        # Continue/Recent must display the exact canonical poster pinned by the
        # player for the last episode.  This path is persisted in history and is
        # the same parent-series poster used by the player InfoBar/info overlay.
        if mtype=="episode":
            pinned=str(item.get("_player_poster") or item.get("_adaptive_source_local") or "").strip()
            if pinned and os.path.isfile(pinned) and os.path.getsize(pinned)>100:
                digest=hashlib.sha1(pinned.encode("utf-8","ignore")).hexdigest()
                thumb=_thumb_path(digest,size)
                if _valid_cache_file(thumb):return thumb
                return pinned
        if mtype in ("itv","live"):
            value=_image_url(item)
            if not value:return asset(placeholder)
            url=_source_art_url(value,profile or {},item) or value
            url=_normalize_provider_image_url(url) or url
            source=_cached_live_picon_path(url,profile or {},item)
            # Home9 is a strict network-cache consumer: return the cached source only.
            # The Home screen performs any 220x132 aspect-safe local fit on its
            # shared image worker, never on the GUI thread and never via network.
            if source and os.path.isfile(source):return source
            return asset(placeholder)
        media="series" if mtype in ("series","episode") else "vod"
        art_item=dict(item)
        if mtype=="episode":
            parent_id=art_item.get("_series_id") or art_item.get("series_id") or art_item.get("series") or art_item.get("parent_id")
            parent_title=art_item.get("_series_title")
            if parent_id not in (None,""):art_item["series_id"]=parent_id
            if parent_title:art_item["name"]=parent_title;art_item["title"]=parent_title
        snap=load_artwork_v2_manifest(profile,media,art_item) or {}
        poster=_verified_external_art(snap,"poster")
        if not poster:
            snap=load_detail_snapshot(profile,media,art_item) or {}
            poster=_verified_external_art(snap,"poster")
        if poster and os.path.isfile(poster):
            digest=hashlib.sha1(poster.encode("utf-8","ignore")).hexdigest()
            thumb=_thumb_path(digest,size)
            if _valid_cache_file(thumb):return thumb
            return poster
    except Exception as exc:
        optional_failure("ui.home_cached_art",exc)
    return asset(placeholder)

def _home_prepare_art(item, profile, client, size, placeholder, media_type=""):
    """Prepare home-card art. Portal art is allowed only for Live TV."""
    if not isinstance(item,dict):
        return asset(placeholder)
    mtype=str(media_type or item.get("_saved_media_type") or "").lower()
    # If playback already pinned the canonical parent-series poster into the
    # history item, Home must reuse it verbatim instead of resolving episode art.
    if mtype=="episode":
        pinned=str(item.get("_player_poster") or item.get("_adaptive_source_local") or "").strip()
        if pinned and os.path.isfile(pinned) and os.path.getsize(pinned)>100:
            try:
                digest=hashlib.sha1(pinned.encode("utf-8","ignore")).hexdigest();thumb=_thumb_path(digest,size)
                return _build_thumbnail(pinned,thumb,size) or pinned
            except Exception:
                return pinned
    if mtype in ("itv","live"):
        value=_image_url(item)
        if not value:return asset(placeholder)
        try:
            # HOME-ONLY fix: reuse the exact persistent Live Picon cache.
            # Do not invoke/change the Live screen downloader, retry queue or player.
            url=_source_art_url(value,profile or {},item) or value
            url=_normalize_provider_image_url(url) or url
            source=_cached_live_picon_path(url,profile or {},item)
            if source and os.path.isfile(source):
                fitted=_fit_live_picon_canvas(source,PERSISTENT_GENERATED_DIR,(220,132))
                return fitted or source
            # Cache miss: preserve the existing Home behaviour as fallback only.
            source=_download_portal_artwork(value,profile,client,False,timeout=3.5,item=item)
            if source:
                fitted=_fit_live_picon_canvas(source,PERSISTENT_GENERATED_DIR,(220,132))
                return fitted or source
        except Exception as exc:
            optional_failure("ui.home_recent_live_picon",exc)
        return asset(placeholder)
    media="series" if mtype in ("series","episode") else "vod"
    # History rows for episodes store the parent as _series_id/_series_title.
    # Artwork manifests are keyed as SERIES, so never ask the series cache to
    # resolve using the episode id (that can return artwork from another title).
    art_item=dict(item)
    if mtype=="episode":
        parent_id=art_item.get("_series_id") or art_item.get("series_id") or art_item.get("series") or art_item.get("parent_id")
        parent_title=art_item.get("_series_title")
        if parent_id not in (None,""): art_item["series_id"]=parent_id
        if parent_title: art_item["name"]=parent_title; art_item["title"]=parent_title
    clean=(dict(art_item) if isinstance(art_item,dict) and art_item.get("_xtream") else _strip_portal_artwork(art_item))
    try:
        # Grid/details already persist the canonical TMDB poster in Artwork V2.
        # Reuse that exact HDD file first so Continue/Recent never performs a
        # fresh identity search for an item we have already seen.
        snap=load_artwork_v2_manifest(profile,media,art_item)
        poster=_verified_external_art(snap,"poster")
        if not poster:
            snap=load_detail_snapshot(profile,media,art_item)
            poster=_verified_external_art(snap,"poster")
        if poster:
            digest=hashlib.sha1(poster.encode("utf-8","ignore")).hexdigest();thumb=_thumb_path(digest,size)
            return _build_thumbnail(poster,thumb,size) or poster
    except Exception as exc:
        optional_failure("ui.silent_guard",exc)
    # First visit: resolve external identity/art directly. Never inspect portal image fields.
    try:
        cfg=load_settings();credential=str(cfg.get("tmdb_credential") or "").strip()
        if not (cfg.get("tmdb_enabled",True) and credential):return asset(placeholder)
        data=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(4,max(2,int(cfg.get("timeout",10) or 10)))).resolve(profile,media,clean,full=False)
        if not (isinstance(data,dict) and data.get("matched") and data.get("identity_verified")):return asset(placeholder)
        poster=str(data.get("poster_local") or "")
        if not (poster and os.path.isfile(poster)):return asset(placeholder)
        save_detail_snapshot(profile,media,art_item,data)
        digest=hashlib.sha1(poster.encode("utf-8","ignore")).hexdigest();thumb=_thumb_path(digest,size)
        return _build_thumbnail(poster,thumb,size) or poster
    except Exception:
        return asset(placeholder)


def _player_payload(item, profile=None, fallback=None, media_type=None):
    """Attach cached artwork and stable logical resume metadata to the player.

    Resume is keyed by the portal item identity in SQLite, not the temporary
    create_link URL. This means expiring Stalker URLs cannot make an episode
    forget where playback stopped.
    """
    data = {}
    if isinstance(fallback, dict):
        data.update(fallback)
    if isinstance(item, dict):
        data.update(item)
    if media_type == "episode" and isinstance(fallback, dict):
        # Keep the parent-series title for artwork, subtitles and episode identity.
        if not data.get("_series_title"):
            data["_series_title"]=str(fallback.get("name") or fallback.get("title") or fallback.get("_raw_name") or "")
    # Receiver service handoff: pin the service captured at the true
    # plugin boundary AFTER merging item/fallback payloads. Old history or
    # zap rows may carry a stale/blank private key; they must never override the
    # current session return service.
    data["_return_service_ref_string"]=_plugin_original_service_string()
    if isinstance(profile, dict):
        data["_portal"] = str(profile.get("portal") or "")
        data["_mac"] = str(profile.get("mac") or "")
        data["_server_name"] = str(profile.get("name") or "")
        data["_source_type"] = str(profile.get("source_type") or data.get("_source_type") or "")
        data["_allow_http_fallback"] = bool(profile.get("allow_http_fallback",False))
        data["_tls_mode"] = str(profile.get("tls_mode") or "auto")
        data["_device_profile"] = str(profile.get("device_profile") or "auto")
        data["_tls_fallback_accepted"] = bool(profile.get("tls_fallback_accepted",False))
        data["_http_fallback_accepted"] = bool(profile.get("http_fallback_accepted",False))
    if media_type in ("itv", "live") and isinstance(item, dict):
        # us199: carry the real portal picon into the live player.  Resolve
        # the same URL/digest used by the live grid so the 220x132 InfoBar card
        # never falls back to a generated channel letter when the picon is
        # already on HDD.
        try:
            raw_picon=_image_url(item)
            abs_picon=_source_art_url(raw_picon,profile or {},item) if raw_picon else None
            if abs_picon:
                # Live grid/player share the normalized ORIGINAL URL and ROOT/live_picons.
                abs_picon=_normalize_provider_image_url(abs_picon) or abs_picon
                local=_cached_live_picon_path(abs_picon,profile or {},item)
                if local and os.path.isfile(local):data["_player_picon"]=local
                data["_player_picon_url"]=abs_picon
        except Exception as exc:optional_failure("ui.player_picon_payload",exc)

    if media_type in ("vod", "series", "episode", "catchup") and isinstance(item, dict):
        # Keep the exact portal item used by the browser as the history key.
        # The merged player payload may contain a parent-series id from fallback.
        history_item = dict(item)
        if media_type == "episode" and isinstance(fallback, dict):
            series_title = fallback.get("name") or fallback.get("title")
            if series_title: history_item["_series_title"] = str(series_title)
            series_id=fallback.get("series_id") or fallback.get("id") or fallback.get("series_uid")
            if series_id not in (None,"") and history_item.get("series_id") in (None,""):
                history_item["_series_id"] = series_id
            # Keep the actual parent-series identity solely for runtime quality
            # learning. It is not used as the episode history/resume key.
            data["_quality_parent_item"] = dict(fallback)
        data["_history_item"] = history_item
        try:
            progress = load_playback_progress(profile or {}, media_type, item) or {}
            position = max(0, int(progress.get("position") or 0))
            duration = max(0, int(progress.get("duration") or 0))
            completed = bool(progress.get("completed"))
            sane_bounds = (not duration) or (position <= duration + (5 * 90000))
            # native-style bookmark policy: only reject the trivial first/last
            # ten seconds. A stale auto-completed flag must not hide a perfectly
            # valid mid-title bookmark.
            sane_start = position >= (10 * 90000)
            sane_end = (not duration) or (duration-position > 10*90000)
            if sane_start and sane_bounds and sane_end:
                data["_resume_position"] = position
                data["_resume_duration"] = duration
        except Exception as exc:
            optional_failure("ui", exc)
    # Player title-logo handoff is authority-only.  The Player never selects a
    # Player-sized cache independently; it consumes the current screen or the
    # same 420x144 Cinematic Ultra authority for the locked identity.
    try:
        logo_owner=(fallback if media_type=="episode" and isinstance(fallback,dict) else item) if isinstance(item,dict) else (fallback or {})
        if not isinstance(logo_owner,dict):logo_owner={}
        from .title_logo_ultra import ultra_title_logo_cached, ultra_title_logo_identity_id
        # Home Recent stores a compact history row. Rehydrate only already-known
        # identity/policy metadata from the persistent HDD snapshot before the
        # Player asks for its title-logo authority. This is HDD-only and avoids
        # the historical first-open blank / second-open hit.
        try:
            logo_media="series" if media_type in ("series","episode") else (media_type or "vod")
            logo_snap={}
            if media_type=="episode":
                series_ref={}
                if isinstance(fallback,dict):
                    series_ref=dict(fallback)
                elif isinstance(logo_owner,dict):
                    series_ref=dict(logo_owner)
                    sid=series_ref.get("_series_id") or series_ref.get("series_id") or series_ref.get("series") or series_ref.get("parent_id")
                    if sid not in (None,""):series_ref["series_id"]=sid
                    stitle=series_ref.get("_series_title") or series_ref.get("series_title") or series_ref.get("series_name")
                    if stitle:series_ref["name"]=stitle;series_ref["title"]=stitle
                if series_ref:
                    logo_snap=load_artwork_v2_manifest(profile or {},"series",series_ref) or load_detail_snapshot(profile or {},"series",series_ref) or {}
            if not logo_snap:
                logo_snap=load_artwork_v2_manifest(profile or {},logo_media,logo_owner) or load_detail_snapshot(profile or {},logo_media,logo_owner) or {}
            if isinstance(logo_snap,dict):
                snap_tmdb=logo_snap.get("tmdb_id")
                if snap_tmdb not in (None,""):
                    logo_owner.setdefault("tmdb_id",snap_tmdb);logo_owner.setdefault("_locked_tmdb_id",snap_tmdb)
                for key in ("original_language","origin_country","countries","production_countries","original_title","original_name","logo_url","logo_language"):
                    value=logo_snap.get(key)
                    if value not in (None,"",[],{}) and logo_owner.get(key) in (None,"",[],{}):logo_owner[key]=value
        except Exception as exc:optional_failure("ui.player_title_logo_recent_hdd",exc)
        logo_title=str(logo_owner.get("name") or logo_owner.get("title") or data.get("_series_title") or data.get("name") or data.get("title") or "")
        logo_id=ultra_title_logo_identity_id(logo_owner,logo_owner)
        authority=""
        for candidate in (logo_owner.get("_player_title_logo_cinematic_source"),):
            try:
                candidate=str(candidate or "")
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>256:authority=candidate;break
            except Exception:pass
        if not authority:
            authority=ultra_title_logo_cached(media_type,logo_owner,logo_owner,(420,144),logo_title)
        if authority:
            data["_player_title_logo_cinematic_source"]=authority
            data["_player_title_logo_title"]=logo_title
            history=data.get("_history_item")
            if isinstance(history,dict):history["_player_title_logo_cinematic_source"]=authority
        if logo_id not in (None,""):
            data["_player_title_logo_tmdb_id"]=logo_id
            data["_locked_tmdb_id"]=logo_id
            history=data.get("_history_item")
            if isinstance(history,dict):
                history["_player_title_logo_tmdb_id"]=logo_id;history["_locked_tmdb_id"]=logo_id
    except Exception as exc:optional_failure("ui.player_title_logo_handoff",exc)

    # R37 player poster identity lock.  Artwork slots and history rows can live
    # longer than the item that first painted them, so a local poster is trusted
    # only inside the same portal-scoped logical content identity.  Episodes use
    # their parent-series identity because the Player intentionally shows the
    # series poster.
    expected_poster_identity=""
    try:
        from .persistent_cache import content_cache_key
        identity_media="series" if media_type=="episode" else (media_type or "vod")
        identity_item=(fallback if media_type=="episode" and isinstance(fallback,dict) else item)
        if isinstance(identity_item,dict):
            expected_poster_identity=str(content_cache_key(profile or {},identity_media,identity_item) or "")
        handed_identity=str(data.get("_player_poster_identity") or "")
        if expected_poster_identity and handed_identity and handed_identity!=expected_poster_identity:
            for _k in ("_player_poster","_ultra_poster_source","_adaptive_source_local","_cin_provider_poster_local"):
                data.pop(_k,None)
            data.pop("_player_poster_identity",None)
    except Exception as exc:
        optional_failure("ui.player_poster_identity_prepare",exc)

    # First honor an already-verified local poster handed off by the current
    # Grid/Cinematic/Details screen. Playback must not start a parallel artwork
    # identity when the exact poster is already resident on HDD.
    try:
        for local_key in ("_player_poster","_ultra_poster_source","_adaptive_source_local","_cin_provider_poster_local","poster_local"):
            candidate=str(data.get(local_key) or "")
            if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>100:
                data["_player_poster"]=candidate;break
        for local_key in ("_backdrop_source_local","_cin_provider_backdrop_local"):
            candidate=str(data.get(local_key) or "")
            if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>100:
                data["_backdrop_source_local"]=candidate;break
    except Exception as exc:
        optional_failure("ui.player_local_handoff",exc)

    # Then consult the canonical external HDD snapshot. Episodes prefer the
    # parent-series poster; a valid current-screen handoff always wins.
    try:
        art_item = dict(item) if isinstance(item,dict) else dict(data)
        if media_type=="episode":
            parent_id=art_item.get("_series_id") or art_item.get("series_id") or art_item.get("series") or art_item.get("parent_id")
            parent_title=art_item.get("_series_title")
            if parent_id not in (None,""): art_item["series_id"]=parent_id
            if parent_title: art_item["name"]=parent_title; art_item["title"]=parent_title
        if media_type=="episode":
            snap=None
            if isinstance(fallback,dict):
                snap=load_artwork_v2_manifest(profile or {},"series",fallback)
                if not isinstance(snap,dict) or not snap.get("poster_local"):
                    snap=load_detail_snapshot(profile or {},"series",fallback)
            if not isinstance(snap,dict) or not snap.get("poster_local"):
                snap=load_artwork_v2_manifest(profile or {},"series",art_item)
            if not isinstance(snap,dict) or not snap.get("poster_local"):
                snap=load_detail_snapshot(profile or {},"series",art_item)
            if not isinstance(snap,dict) or not snap.get("poster_local"):
                snap=load_artwork_v2_manifest(profile or {},"episode",art_item)
            if not isinstance(snap,dict) or not snap.get("poster_local"):
                snap=load_detail_snapshot(profile or {},"episode",art_item)
        else:
            snap=load_artwork_v2_manifest(profile or {},media_type or "vod",art_item)
            if not isinstance(snap,dict) or not snap.get("poster_local"):
                snap=load_detail_snapshot(profile or {},media_type or "vod",art_item)

        # release player poster persistence bridge.  The browser/grid may carry
        # only the locked external identity while the item-keyed pointer was
        # created by an earlier screen/portal representation.  Fall back to the
        # global persistent TMDb/IMDb library before allowing Player to use a
        # generated letter logo.  HDD reads only: no TMDb/provider request here.
        if not isinstance(snap,dict) or not snap.get("poster_local"):
            lookup_media="series" if media_type in ("series","episode") else (media_type or "vod")
            locked_tmdb=(art_item.get("_locked_tmdb_id") or art_item.get("tmdb_id") or art_item.get("tmdbid") or data.get("_locked_tmdb_id") or data.get("tmdb_id") or data.get("tmdbid"))
            locked_imdb=(art_item.get("imdb_id") or art_item.get("imdb") or data.get("imdb_id") or data.get("imdb"))
            if locked_tmdb not in (None,""):
                try:snap=load_detail_snapshot_by_tmdb(lookup_media,locked_tmdb) or {}
                except Exception as exc:optional_failure("ui.player_tmdb_poster_lookup",exc);snap={}
            if (not isinstance(snap,dict) or not snap.get("poster_local")) and locked_imdb:
                try:snap=load_detail_snapshot_by_imdb(locked_imdb) or {}
                except Exception as exc:optional_failure("ui.player_imdb_poster_lookup",exc);snap={}
            if not isinstance(snap,dict) or not snap.get("poster_local"):
                try:snap=load_shared_detail_snapshot(profile or {},lookup_media,art_item) or {}
                except Exception as exc:optional_failure("ui.player_shared_poster_lookup",exc);snap={}
        if isinstance(snap,dict):
            # Exact item-keyed persistent artwork is sufficient for Player visual
            # handoff. Opening Details is never a prerequisite for a movie poster.
            candidate=str(snap.get("poster_local") or "")
            # R37: an exact item/TMDb snapshot has stronger identity than any
            # recycled on-screen slot.  When it exists it always replaces the
            # handed poster instead of allowing stale pixels to win forever.
            if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>100:
                data["_player_poster"]=candidate
                if expected_poster_identity:data["_player_poster_identity"]=expected_poster_identity
            final_poster=str(data.get("_player_poster") or "")
            if final_poster and os.path.isfile(final_poster):
                if expected_poster_identity:data["_player_poster_identity"]=expected_poster_identity
                history=data.get("_history_item")
                if isinstance(history,dict):
                    history["_player_poster"]=final_poster;history["_adaptive_source_local"]=final_poster
                    if expected_poster_identity:history["_player_poster_identity"]=expected_poster_identity
            backdrop=str(snap.get("display_backdrop_local") or snap.get("backdrop_local") or "")
            current_backdrop=str(data.get("_backdrop_source_local") or "")
            if (not (current_backdrop and os.path.isfile(current_backdrop))) and backdrop and os.path.isfile(backdrop) and os.path.getsize(backdrop)>100:
                data["_backdrop_source_local"]=backdrop
                history=data.get("_history_item")
                if isinstance(history,dict):history["_backdrop_source_local"]=backdrop
    except Exception as exc:
        optional_failure("ui.player_external_poster",exc)

    # Last HDD-only bridge for provider-backed content. This fixes the old dead
    # fallback code that sat after `return data` and therefore never executed.
    # Resolve the browser's exact URL digest, but never perform network I/O here.
    try:
        current=str(data.get("_player_poster") or "")
        if not (current and os.path.isfile(current) and os.path.getsize(current)>100):
            raw=str(data.get("_cin_provider_poster_url") or data.get("_visible_provider_art_url") or (_image_url(item) if isinstance(item,dict) else "") or "")
            value=_source_art_url(raw,profile or {},item if isinstance(item,dict) else data) if raw else None
            if value:
                value=_optimized_artwork_url(value,False)
                data["_player_poster_url"]=value
                digest=hashlib.sha1(value.encode("utf-8","ignore")).hexdigest()
                candidate=_find_original_artwork(digest)
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>100:
                    data["_player_poster"]=candidate
                    if expected_poster_identity:data["_player_poster_identity"]=expected_poster_identity
                    history=data.get("_history_item")
                    if isinstance(history,dict):
                        history["_player_poster"]=candidate;history["_adaptive_source_local"]=candidate
                        if expected_poster_identity:history["_player_poster_identity"]=expected_poster_identity
    except Exception as exc:
        optional_failure("ui.player_cached_provider_poster",exc)
    return data




from .ui_skin_templates import (
    MAIN_SKIN, BROWSER_SKIN, POSTER_GRID_SKIN, POSTER_GRID_V2_SKIN, LIVE_GRID_SKIN, DETAIL_SKIN,
    SERIES_EPISODES_SKIN, HOME_SKIN, DIAGNOSTICS_SKIN, WIZARD_SKIN, CINEMATIC_GLOBAL_SKIN, BACKDROP_GRID_SKIN, BACKDROP_GRID2_SKIN, SEARCH_SKIN,
)

_NEUTRAL_UTILITY_GLASS_CACHE = None
def _neutral_utility_glass():
    """Static shipped utility glass. No runtime image generation."""
    global _NEUTRAL_UTILITY_GLASS_CACHE
    if isinstance(_NEUTRAL_UTILITY_GLASS_CACHE,dict):
        return dict(_NEUTRAL_UTILITY_GLASS_CACHE)
    out={
        "settings_row":asset("fixed_master_r63/utility_row.png"),
        "settings_selected":asset("fixed_master_r63/utility_row_selected.png"),
        "utility_row":asset("fixed_master_r63/utility_row.png"),
        "utility_selected":asset("fixed_master_r63/utility_row_selected.png"),
        "side":asset("us_utility_side_card_660.png"),
    }
    out["row"]=out["utility_row"]
    out["row_selected"]=out["utility_selected"]
    out["info"]=out["side"]
    _NEUTRAL_UTILITY_GLASS_CACHE=dict(out)
    return out











def _apply_theme_palette(xml):
    """Lightweight premium palettes without extra GPU-heavy overlays."""
    theme=str(_active_theme() or "nova_fhd")
    if theme == "oled_black":
        mapping={"#03070d":"#000000","#050b13":"#020202","#07111d":"#050505","#071522":"#060606","#29b7ff":"#48c8ff","#6dd4ff":"#8bdcff"}
    elif theme == "midnight_purple":
        mapping={"#03070d":"#080513","#050b13":"#0b0718","#07111d":"#100a21","#071522":"#120b27","#29b7ff":"#9c6cff","#6dd4ff":"#bb9cff","#73cbed":"#a98cff"}
    else:return xml
    for src,dst in mapping.items():xml=xml.replace(src,dst)
    return xml


def _normalize_colored_pill_labels(xml):
    """Center every red/green/yellow/blue footer label on its actual pill.

    Several legacy skins kept the colored background at one geometry while the
    Label widget used a narrower, shifted rectangle.  ``halign=center`` then
    centered the text inside the *wrong* box, which is why labels drifted left
    or right on receiver even though the XML claimed to be centered.

    The authoritative geometry is the colored ePixmap immediately before the
    matching Label.  Mirror that exact rectangle for the label and center on
    both axes.  This keeps one rule across Portal, grids, details, episodes,
    diagnostics and wizard screens instead of accumulating per-screen offsets.
    """
    colors=("red","green","yellow","blue")
    seq=re.compile(
        r'(?P<pix><ePixmap\b(?=[^>]*pixmap="[^"]*(?P<color>red|green|yellow|blue)[^"]*")[^>]*/>)'
        r'(?P<gap>\s*)'
        r'(?P<label><widget\s+name="(?P=color)"[^>]*/>)',
        re.I,
    )

    def _center(match):
        pix=match.group("pix")
        label=match.group("label")
        pm=re.search(r'position="(\d+),(\d+)"',pix)
        sm=re.search(r'size="(\d+),(\d+)"',pix)
        if not (pm and sm):
            return match.group(0)
        x,y=pm.group(1),pm.group(2)
        w,h=sm.group(1),sm.group(2)
        if re.search(r'position="[^"]*"',label):
            label=re.sub(r'position="[^"]*"','position="%s,%s"'%(x,y),label,count=1)
        else:
            label=label[:-2]+' position="%s,%s"/>'%(x,y)
        if re.search(r'size="[^"]*"',label):
            label=re.sub(r'size="[^"]*"','size="%s,%s"'%(w,h),label,count=1)
        else:
            label=label[:-2]+' size="%s,%s"/>'%(w,h)
        if 'halign=' in label:
            label=re.sub(r'halign="[^"]*"','halign="center"',label,count=1)
        else:
            label=label[:-2]+' halign="center"/>'
        if 'valign=' in label:
            label=re.sub(r'valign="[^"]*"','valign="center"',label,count=1)
        else:
            label=label[:-2]+' valign="center"/>'
        fm=re.search(r'font="([^;]+);(\d+)"',label)
        if fm:
            new_size=max(18,int(fm.group(2))-2)
            label=re.sub(r'font="([^;]+);\d+"',lambda m:'font="%s;%d"'%(m.group(1),new_size),label,count=1)
        return pix+match.group("gap")+label

    # Run twice defensively in case a theme transform inserted harmless spacing.
    xml=seq.sub(_center,xml)
    xml=seq.sub(_center,xml)
    return xml

def _bind_internal_skin_assets(xml):
    """Resolve packaged asset paths for internal screens at receiver runtime.

    Keep Home and Select Portal untouched: those two screens use their own
    approved dynamic layers.  The internal browser/grid/live/details/episodes
    screens still contain /usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd placeholders, and if they are not
    resolved Enigma2 silently skips their dark-navy backgrounds and static
    chrome.
    """
    return xml.replace("/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd", ASSET_DIR.rstrip("/"))

for _skin_name in (
    "BROWSER_SKIN", "POSTER_GRID_SKIN", "LIVE_GRID_SKIN",
    "DETAIL_SKIN", "SERIES_EPISODES_SKIN",
    "DIAGNOSTICS_SKIN", "WIZARD_SKIN",
):
    globals()[_skin_name] = _normalize_colored_pill_labels(globals()[_skin_name])
    globals()[_skin_name] = _bind_internal_skin_assets(globals()[_skin_name])

def _scale_skin(xml):
    try:
        desktop = getDesktop(0).size()
        width, height = int(desktop.width()), int(desktop.height())
    except Exception:
        return xml
    if width == 1920 and height == 1080:
        return xml
    sx, sy = width / 1920.0, height / 1080.0
    def pair(match):
        return '%s="%d,%d"' % (match.group(1), round(int(match.group(2))*sx), round(int(match.group(3))*sy))
    xml = re.sub(r'(position|size)="(\d+),(\d+)"', pair, xml)
    # Preserve 1px compatibility widgets. Only real text receives desktop scaling.
    def desktop_font(match):
        base=int(match.group(2))
        if base < 12:return match.group(0)
        return 'font="%s;%d"' % (match.group(1), max(12, round(base*sy)))
    xml = re.sub(r'font="([^;]+);(\d+)"', desktop_font, xml)
    return xml

_FONT_SCALE_FACTORS={"normal":1.0,"large":1.12,"larger":1.22,"xlarge":1.32}
def _apply_user_font_scale(xml):
    """Apply the global readability preference to real text only.

    Geometry is deliberately untouched; growth is capped at +10 px so provider
    titles still fit their authored boxes and existing adaptive fitters remain
    the final authority for unusually long strings.
    """
    try:mode=str(load_settings().get("font_scale") or "normal").lower()
    except Exception:mode="normal"
    factor=float(_FONT_SCALE_FACTORS.get(mode,1.0))
    if factor <= 1.0:return xml
    def repl_font(match):
        base=int(match.group(2))
        if base < 12:return match.group(0)
        size=max(base,min(base+10,int(round(base*factor))))
        return 'font="%s;%d"' % (match.group(1),size)
    return re.sub(r'font="([^;]+);(\d+)"',repl_font,xml)

def _prepare_runtime_skin(xml):
    return _apply_user_font_scale(_scale_skin(xml))

MAIN_SKIN = _prepare_runtime_skin(MAIN_SKIN)
BROWSER_SKIN = _prepare_runtime_skin(BROWSER_SKIN)
POSTER_GRID_SKIN = _prepare_runtime_skin(POSTER_GRID_SKIN)
LIVE_GRID_SKIN = _prepare_runtime_skin(LIVE_GRID_SKIN)
DETAIL_SKIN = _prepare_runtime_skin(DETAIL_SKIN)
SERIES_EPISODES_SKIN = _prepare_runtime_skin(SERIES_EPISODES_SKIN)
HOME_SKIN = _prepare_runtime_skin(HOME_SKIN)
CINEMATIC_GLOBAL_SKIN = _prepare_runtime_skin(CINEMATIC_GLOBAL_SKIN)
DIAGNOSTICS_SKIN = _prepare_runtime_skin(DIAGNOSTICS_SKIN)
def DiagnosticsScreen(session, *args, **kwargs):
    # Diagnostics pulls database/endurance/backup state; none is needed for the
    # normal portal/home path. Session.open accepts this callable factory just
    # like the existing lazy Player factory.
    from .ui_screens_diagnostics import DiagnosticsScreen as _RealDiagnosticsScreen
    # The callable factory is not the Screen instance Enigma2 renders.  Bind the
    # already scaled/prepared runtime skin to the extracted real class as well,
    # otherwise its module-level `skin = ""` produces a fully populated black
    # screen.
    try:_RealDiagnosticsScreen.skin = DIAGNOSTICS_SKIN
    except Exception:pass
    return _RealDiagnosticsScreen(session, *args, **kwargs)
DiagnosticsScreen.skin = DIAGNOSTICS_SKIN
WIZARD_SKIN = _prepare_runtime_skin(WIZARD_SKIN)


from .ui_transition import TransitionMixin



from .ui_async import AsyncScreenMixin







from .ui_image_loader import (
    configure_image_loader as _configure_image_loader,
    ImageLoaderMixin,
)
_configure_image_loader(
    LOG,
    SOURCE_POSTER_CACHE_DIR,
    _ArtworkCancelled,
    _IMAGE_EXECUTOR,
    _artwork_attempt_allowed,
    _artwork_file_lock,
    _build_thumbnail,
    _cache_artwork_path,
    _clear_artwork_failure,
    _find_original_artwork,
    _image_url,
    _optimized_artwork_url,
    _persistent_write_ok,
    _persistent_write_require,
    _queue_thumbnail_build,
    _record_artwork_failure,
    _safe_image_headers,
    _safe_image_opener,
    _thumb_path,
    _valid_cache_file,
    asset,
)

from .ui_screens_portallist import (
    configure_portal_list as _configure_portal_list,
    PortalListScreen,
)




from .ui_screens_home import (
    configure_home_screen as _configure_home_screen,
    PortalHomeScreen,
)



from .ui_screens_browser import (
    configure_browser_screen as _configure_browser_screen,
    PortalBrowserScreen,
)



_WIZARD_SCREEN_CLASS = None
def _ensure_wizard_screen():
    global _WIZARD_SCREEN_CLASS
    if _WIZARD_SCREEN_CLASS is None:
        from .ui_screens_wizard import configure_wizard_screen as _configure_wizard_screen_lazy, FirstRunWizardScreen as _RealFirstRunWizardScreen
        _configure_wizard_screen_lazy(
            PortalHomeScreen,
            PortalListScreen,
            WIZARD_DONE_FILE,
            WIZARD_SKIN,
            _client_from_profile,
            _friendly_portal_error,
            _looks_like_m3u_url,
            _probe_m3u_url,
            _validate_profile_client,
        )
        _WIZARD_SCREEN_CLASS = _RealFirstRunWizardScreen
    return _WIZARD_SCREEN_CLASS

def FirstRunWizardScreen(session, *args, **kwargs):
    return _ensure_wizard_screen()(session, *args, **kwargs)





def _settings_popup_source():
    """R64: Settings/Portal popups use the frozen application source only."""
    try:
        from .ui_fixed_adaptive import fixed_adaptive_source
        src=str(fixed_adaptive_source() or "")
        if src and os.path.isfile(src) and os.path.getsize(src)>4096:
            return src
    except Exception as exc:
        optional_failure("ui.settings_fixed_popup_source",exc)
    fallback=asset("category_palestine_static_1920x1080.jpg")
    return fallback if fallback and os.path.isfile(fallback) else None

def _settings_popup_backdrop(source_path):
    """Return a cached blurred/dimmed copy of the current Home hero for Settings overlays.

    This is deliberately disk-only: opening a Settings popup must never trigger a
    network request or rebuild the Home hero.
    """
    if not source_path or not os.path.isfile(source_path) or _PILImage is None:
        return source_path
    if not _persistent_write_ok(THUMB_CACHE_DIR):
        return source_path
    try:
        try: stamp=os.path.getmtime(source_path)
        except Exception: stamp=0
        sig=hashlib.sha1((str(source_path)+"|"+str(stamp)+"|settings-popup-blur-v1").encode("utf-8","ignore")).hexdigest()[:18]
        target=os.path.join(THUMB_CACHE_DIR,"settings_popup_blur_%s.jpg"%sig)
        if _valid_cache_file(target):
            return target
        temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui.settings_popup_exif",exc)
            image=image.convert("RGB")
            tw,th=1920,1080
            scale=max(float(tw)/max(1,image.width),float(th)/max(1,image.height))
            nw=max(tw,int(round(image.width*scale)));nh=max(th,int(round(image.height*scale)))
            res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            image=image.resize((nw,nh),res)
            left=max(0,(nw-tw)//2);top=max(0,(nh-th)//2)
            image=image.crop((left,top,left+tw,top+th))
            if _PILImageFilter is not None:
                image=image.filter(_PILImageFilter.GaussianBlur(radius=15))
            # Keep the hero recognisable while pushing it behind the modal card.
            shade=_PILImage.new("RGBA",(tw,th),(1,5,10,118))
            image=_PILImage.alpha_composite(image.convert("RGBA"),shade).convert("RGB")
            _persistent_write_require(temp);image.save(temp,"JPEG",quality=90,optimize=True)
        _persistent_write_require(target);os.replace(temp,target)
        return target
    except Exception as exc:
        optional_failure("ui.settings_popup_backdrop",exc)
        return source_path

def _build_settings_popup_chrome(source_path, cache_key):
    """Adaptive 3D glass for Settings dialogs.

    Text dialogs use medium-width cards with visible depth. Numeric pickers stay
    compact. The material itself is neutral navy/black with the current adaptive
    accent blended into the body, rim and highlight.
    """
    if not _persistent_write_ok(THUMB_CACHE_DIR):return {}
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):return {}
    try:
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.58,0.54)
        accent2=_lift_dynamic_accent(secondary,0.50,0.42)
        base=(3,10,17)
        specs={
            "panel":((1140,720),30,"panel"),
            "panel_560":((560,720),30,"panel"),
            "panel_900":((900,720),30,"panel"),
            "panel_980":((980,720),30,"panel"),
            "row":((980,64),18,"row"),
            "row_360":((360,64),18,"row"),
            "row_760":((760,64),18,"row"),
            "row_820":((820,64),18,"row"),
            "selected":((980,64),18,"selected"),
            "selected_360":((360,64),18,"selected"),
            "selected_760":((760,64),18,"selected"),
            "selected_820":((820,64),18,"selected"),
            "inner":((980,420),24,"inner"),
            "button":((300,58),18,"button"),
            "input":((920,70),18,"input"),
        }
        out={}
        for name,(size,radius,kind) in specs.items():
            target=os.path.join(THUMB_CACHE_DIR,"settingsglass_v5_%s_%s.png"%(str(cache_key),name))
            if _valid_cache_file(target):
                out[name]=target;continue
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            w,h=size

            img=_PILImage.new("RGBA",size,(0,0,0,0))

            # 1) depth shadow
            shadow=_PILImage.new("RGBA",size,(0,0,0,0))
            sd=ImageDraw.Draw(shadow)
            sd.rounded_rectangle((8,10,w-9,h-4),radius=radius,fill=(0,0,0,120))
            if _PILImageFilter is not None:
                shadow=shadow.filter(_PILImageFilter.GaussianBlur(radius=7))
            img=_PILImage.alpha_composite(img,shadow)

            # 2) adaptive body
            d=ImageDraw.Draw(img)
            if kind=="panel":
                tint=0.12; alpha=238; edge=136
            elif kind=="selected":
                tint=0.25; alpha=248; edge=236
            elif kind in ("row","input","button"):
                tint=0.14; alpha=245; edge=146
            else:
                tint=0.11; alpha=238; edge=126
            fill=_mix_rgb(base,accent,tint)
            d.rounded_rectangle((3,3,w-4,h-4),radius=radius,fill=fill+(alpha,),outline=accent+(edge,),width=(2 if kind=="selected" else 1))

            # 3) inner bevel and top glass sheen
            inner=_mix_rgb(accent2,(255,255,255),0.36)
            d.rounded_rectangle((8,8,w-9,h-9),radius=max(8,radius-6),outline=inner+(72 if kind=="selected" else 44,),width=1)

            sheen=_PILImage.new("RGBA",size,(0,0,0,0))
            sh=ImageDraw.Draw(sheen)
            hi=_mix_rgb(accent,(255,255,255),0.58)
            top=max(18,int(h*0.46))
            for yy in range(7,top,4):
                q=(yy-7.0)/max(1.0,top-7.0)
                a=max(0,int((42 if kind=="selected" else 25)*(1.0-q)**1.7))
                sh.rounded_rectangle((12,yy,w-13,min(h-10,yy+7)),radius=max(6,radius-8),fill=hi+(a,))
            if _PILImageFilter is not None:
                sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=3))
            img=_PILImage.alpha_composite(img,sheen)

            # selected glow outside the rim
            if kind=="selected":
                glow=_PILImage.new("RGBA",size,(0,0,0,0))
                gd=ImageDraw.Draw(glow)
                gd.rounded_rectangle((4,4,w-5,h-5),radius=radius,outline=accent+(118,),width=3)
                if _PILImageFilter is not None:
                    glow=glow.filter(_PILImageFilter.GaussianBlur(radius=4))
                img=_PILImage.alpha_composite(img,glow)

            _persistent_write_require(temp);img.save(temp,"PNG");_persistent_write_require(target);os.replace(temp,target)
            out[name]=target
        out["info"]=out.get("inner")
        return out
    except Exception as exc:
        optional_failure("ui.settings_glass_v5",exc);return {}


def _settings_text_width_px(text, font_size=24):
    """Measure with Enigma2's own Regular font renderer, not character counts."""
    value=str(text or "")
    try:
        probe=eLabel()
        probe.setFont(gFont("Regular",int(font_size)))
        probe.setText(value)
        size=probe.calculateSize()
        return max(0,int(size.width()))
    except Exception as exc:
        optional_failure("ui.settings_text_measure",exc)
        # Conservative only as a last-resort fallback.
        return max(0,int(len(value)*font_size*0.56))

def _build_settings_sized_chrome(source_path, cache_key, panel_w, panel_h, row_w, row_h):
    """Build exact-size adaptive glass. Enigma2 clips pixmaps; it does not scale them."""
    if not _persistent_write_ok(THUMB_CACHE_DIR):return {}
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):return {}
    try:
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.58,0.54)
        accent2=_lift_dynamic_accent(secondary,0.50,0.42)
        base=(3,10,17)
        dims={
            "panel":(int(panel_w),int(panel_h),30,"panel"),
            "row":(int(row_w),int(row_h-8),18,"row"),
            "selected":(int(row_w),int(row_h-8),18,"selected"),
        }
        out={}
        for name,(w,h,radius,kind) in dims.items():
            sig="%s_%dx%d_%s"%(str(cache_key),w,h,name)
            target=os.path.join(THUMB_CACHE_DIR,"settingsfit_v1_"+sig+".png")
            if _valid_cache_file(target):
                out[name]=target;continue

            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            img=_PILImage.new("RGBA",(w,h),(0,0,0,0))

            shadow=_PILImage.new("RGBA",(w,h),(0,0,0,0))
            sd=ImageDraw.Draw(shadow)
            sd.rounded_rectangle((8,9,w-9,h-4),radius=radius,fill=(0,0,0,118))
            if _PILImageFilter is not None:
                shadow=shadow.filter(_PILImageFilter.GaussianBlur(radius=6))
            img=_PILImage.alpha_composite(img,shadow)

            d=ImageDraw.Draw(img)
            if kind=="selected":
                tint=0.25;alpha=248;edge=236;bw=2
            elif kind=="row":
                tint=0.14;alpha=245;edge=146;bw=1
            else:
                tint=0.12;alpha=238;edge=136;bw=1
            fill=_mix_rgb(base,accent,tint)
            d.rounded_rectangle((3,3,w-4,h-4),radius=radius,fill=fill+(alpha,),outline=accent+(edge,),width=bw)

            inner=_mix_rgb(accent2,(255,255,255),0.36)
            d.rounded_rectangle((8,8,w-9,h-9),radius=max(8,radius-6),outline=inner+(72 if kind=="selected" else 44,),width=1)

            sheen=_PILImage.new("RGBA",(w,h),(0,0,0,0))
            sh=ImageDraw.Draw(sheen)
            hi=_mix_rgb(accent,(255,255,255),0.58)
            top=max(18,int(h*0.46))
            for yy in range(7,top,4):
                q=(yy-7.0)/max(1.0,top-7.0)
                a=max(0,int((42 if kind=="selected" else 25)*(1.0-q)**1.7))
                sh.rounded_rectangle((12,yy,w-13,min(h-10,yy+7)),radius=max(6,radius-8),fill=hi+(a,))
            if _PILImageFilter is not None:
                sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=3))
            img=_PILImage.alpha_composite(img,sheen)

            if kind=="selected":
                glow=_PILImage.new("RGBA",(w,h),(0,0,0,0))
                gd=ImageDraw.Draw(glow)
                gd.rounded_rectangle((4,4,w-5,h-5),radius=radius,outline=accent+(118,),width=3)
                if _PILImageFilter is not None:
                    glow=glow.filter(_PILImageFilter.GaussianBlur(radius=4))
                img=_PILImage.alpha_composite(img,glow)

            _persistent_write_require(temp);img.save(temp,"PNG")
            _persistent_write_require(target);os.replace(temp,target)
            out[name]=target
        return out
    except Exception as exc:
        optional_failure("ui.settings_sized_chrome",exc);return {}

_SETTINGS_SCREEN_CLASSES = None
def _ensure_settings_screens():
    global _SETTINGS_SCREEN_CLASSES
    if _SETTINGS_SCREEN_CLASSES is None:
        from . import ui_screens_settings as _settings_mod
        _settings_mod.configure_settings_screens(
            BROWSER_SKIN=BROWSER_SKIN,
            font_scale_skin=_apply_user_font_scale,
            ActionMap=ActionMap,
            BUILD_NAME=BUILD_NAME,
            DiagnosticsScreen=DiagnosticsScreen,
            HOME_HERO_FILE=HOME_HERO_FILE,
            IMAGE_CACHE_DIR=IMAGE_CACHE_DIR,
            IconMenuList=IconMenuList,
            Label=Label,
            PLUGIN_VERSION=PLUGIN_VERSION,
            Pixmap=Pixmap,
            PortalSession=PortalSession,
            THEMES=THEMES,
            TMDBClient=TMDBClient,
            WIZARD_DONE_FILE=WIZARD_DONE_FILE,
            _=_,
            _build_settings_popup_chrome=_build_settings_popup_chrome,
            _build_settings_sized_chrome=_build_settings_sized_chrome,
            _neutral_utility_glass=_neutral_utility_glass,
            _settings_popup_source=_settings_popup_source,
            _settings_popup_backdrop=_settings_popup_backdrop,
            _settings_text_width_px=_settings_text_width_px,
            _invalidate_source_navigation_cache=_invalidate_source_navigation_cache,
            asset=asset,
            backup_choices=backup_choices,
            cleanup_image_cache=cleanup_image_cache,
            create_backup=create_backup,
            ePoint=ePoint,
            eSize=eSize,
            export_support_bundle=export_support_bundle,
            getDesktop=getDesktop,
            hashlib=hashlib,
            hdd_read_ready=hdd_read_ready,
            inspect_backup=inspect_backup,
            json=json,
            list_backups=list_backups,
            load_settings=load_settings,
            load_profiles=load_profiles,
            _load_content_art=_load_content_art,
            _load_visual_bundle=_load_visual_bundle,
            load_theme=load_theme,
            optional_failure=optional_failure,
            os=os,
            parental_hash_pin=parental_hash_pin,
            parental_is_unlocked=parental_is_unlocked,
            parental_lock_now=parental_lock_now,
            parental_pin_is_default=parental_pin_is_default,
            parental_remaining_minutes=parental_remaining_minutes,
            prune_persistent_cache=prune_persistent_cache,
            restore_backup=restore_backup,
            save_settings=save_settings,
            save_theme=save_theme,
            save_tmdb_credential=save_tmdb_credential,
            secret_backup_warning=secret_backup_warning,
            update_api_keys=update_api_keys,
        )
        _SETTINGS_SCREEN_CLASSES = (
            _settings_mod.SettingsGlassChoiceScreen,
            _settings_mod.SettingsGlassNoticeScreen,
            _settings_mod.SettingsGlassInputScreen,
            _settings_mod.AdvancedSettingsScreen,
            _settings_mod.NovaSettingsScreen,
        )
    return _SETTINGS_SCREEN_CLASSES

def SettingsGlassChoiceScreen(session, *args, **kwargs):
    return _ensure_settings_screens()[0](session, *args, **kwargs)

def SettingsGlassNoticeScreen(session, *args, **kwargs):
    return _ensure_settings_screens()[1](session, *args, **kwargs)

def SettingsGlassInputScreen(session, *args, **kwargs):
    return _ensure_settings_screens()[2](session, *args, **kwargs)

def AdvancedSettingsScreen(session, *args, **kwargs):
    return _ensure_settings_screens()[3](session, *args, **kwargs)

def NovaSettingsScreen(session, *args, **kwargs):
    return _ensure_settings_screens()[4](session, *args, **kwargs)









from .ui_grid_artwork import (
    configure_grid_artwork as _configure_grid_artwork,
    GridArtworkMixin,
)

from .ui_grid_base import (
    configure_grid_base as _configure_grid_base,
    PremiumGridBase,
)



from .ui_grid_screens import (
    configure_grid_screens as _configure_grid_screens,
    PremiumPosterGridScreen,
    PremiumPosterGridV2Screen,
    PremiumLiveGridScreen,
)
from .ui_cinematic_global import (
    configure_cinematic_global as _configure_cinematic_global,
    PremiumGlobalCinematicScreen,
)
from .ui_backdrop_grid import (
    configure_backdrop_grid as _configure_backdrop_grid,
    PremiumBackdropGridScreen,
)
from .ui_backdrop_grid2 import (
    configure_backdrop_grid2 as _configure_backdrop_grid2,
    PremiumBackdropGrid2Screen,
)


from .ui_screens_search import (
    configure_search_screen as _configure_search_screen,
    PortalGlobalSearchScreen,
)




def _detail_metadata_complete(data):
    if not isinstance(data,dict):return False
    overlay=data.get("item_overlay") if isinstance(data.get("item_overlay"),dict) else {}
    desc=str(data.get("overview") or overlay.get("description") or "").strip()
    actors=overlay.get("actors") or data.get("cast") or []
    director=overlay.get("director") or data.get("directors") or []
    return bool(desc and (actors or director))

def _detail_cover_artwork(source_path, size=(360,560)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    """Return a cached center-cropped poster that completely fills the target card."""
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return source_path
    try:
        stamp="%s:%s:%sx%s"%(source_path,os.path.getmtime(source_path),int(size[0]),int(size[1]))
        key=hashlib.sha1(stamp.encode("utf-8","ignore")).hexdigest()[:18]
        target=os.path.join(THUMB_CACHE_DIR,"detailcover_%s_%sx%s.png"%(key,int(size[0]),int(size[1])))
        if _valid_cache_file(target):
            return target
        temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui.silent_guard",exc)
            image=image.convert("RGB")
            tw,th=int(size[0]),int(size[1])
            scale=max(float(tw)/max(1,image.width),float(th)/max(1,image.height))
            nw=max(tw,int(round(image.width*scale)));nh=max(th,int(round(image.height*scale)))
            res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            image=image.resize((nw,nh),res)
            left=max(0,(nw-tw)//2);top=max(0,(nh-th)//2)
            image=image.crop((left,top,left+tw,top+th)).convert("RGBA")
            # Clip the artwork to the same rounded geometry as its adaptive card.
            if _ImageDraw is not None:
                mask=_PILImage.new("L",(tw,th),0);md=_ImageDraw.Draw(mask)
                md.rounded_rectangle((0,0,tw-1,th-1),radius=18,fill=255)
                image.putalpha(mask)
            # Receiver CPUs are slow at zlib compression. This derivative is a
            # local first-paint cache, so favor decode/write speed over a few KB.
            _persistent_write_require(temp);image.save(temp,"PNG",compress_level=0)
        _persistent_write_require(target);os.replace(temp,target)
        return target
    except Exception as exc:
        optional_failure("ui.detail_cover",exc)
        return source_path

from .ui_screens_details import (
    configure_details_screen as _configure_details_screen,
    ContentDetailsScreen,
)



from .ui_screens_series import (
    configure_series_screen as _configure_series_screen,
    SeriesEpisodesScreen,
)



_configure_details_screen(
    DETAIL_SKIN=DETAIL_SKIN,
    ActionMap=ActionMap,
    AdaptiveInformationScreen=AdaptiveInformationScreen,
    ArtworkV2=ArtworkV2,
    ChoiceBox=ChoiceBox,
    DOWNLOADS=DOWNLOADS,
    DownloadsManagerScreen=DownloadsManagerScreen,
    IMDbClient=IMDbClient,
    Label=Label,
    Pixmap=Pixmap,
    RT_HALIGN_LEFT=RT_HALIGN_LEFT,
    RT_HALIGN_RIGHT=RT_HALIGN_RIGHT,
    RT_HALIGN_CENTER=RT_HALIGN_CENTER,
    SeriesEpisodesScreen=SeriesEpisodesScreen,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    HOME_HERO_FILE=HOME_HERO_FILE,
    HOME_HERO_SCHEMA=HOME_HERO_SCHEMA,
    HOME_RUNTIME_DIR=HOME_RUNTIME_DIR,
    _HOME_HERO_LOCK=_HOME_HERO_LOCK,
    _prepare_single_home_hero=_prepare_single_home_hero,
    _cleanup_legacy_home_hero_storage=_cleanup_legacy_home_hero_storage,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _ADAPTIVE_EXECUTOR=_ADAPTIVE_EXECUTOR,
    _BACKDROP_PRESENT_EXECUTOR=_BACKDROP_PRESENT_EXECUTOR,
    _DETAIL_PREFETCH_EXECUTOR=_DETAIL_PREFETCH_EXECUTOR,
    _DETAIL_LOGO_EXECUTOR=_DETAIL_LOGO_EXECUTOR,
    _IMAGE_EXECUTOR=_IMAGE_EXECUTOR,
    _backdrop_url=_backdrop_url,
    _build_dynamic_details_chrome=_build_dynamic_details_chrome,
    _build_dynamic_details_gradient=_build_dynamic_details_gradient,
    _build_dynamic_poster_accent=_build_dynamic_poster_accent,
    _build_integrated_backdrop=_build_integrated_backdrop,
    _build_home_adaptive_focus=_build_home_adaptive_focus,
    _build_home_mood_assets=_build_home_mood_assets,
    _catalogue_title=_catalogue_title,
    _clean_display_text=_clean_display_text,
    _compose_information_text=_compose_information_text,
    _configured_playback_engine=_configured_playback_engine,
    _detail_cover_artwork=_detail_cover_artwork,
    _detail_metadata_complete=_detail_metadata_complete,
    _download_portal_artwork=_download_portal_artwork,
    _image_url=_image_url,
    _load_visual_bundle=_load_visual_bundle,
    _native_image_pressure_relief=_native_image_pressure_relief,
    _player_payload=_player_payload,
    _release_pixmap_widget=_release_pixmap_widget,
    _runtime_endurance_log=_runtime_endurance_log,
    _save_visual_bundle=_save_visual_bundle,
    _source_art_url=_source_art_url,
    _valid_cache_file=_valid_cache_file,
    _verified_external_art=_verified_external_art,
    asset=asset,
    eLabel=eLabel,
    ePoint=ePoint,
    eSize=eSize,
    eTimer=eTimer,
    gFont=gFont,
    hashlib=hashlib,
    current_plugin_launch=current_plugin_launch,
    identity_cache_compatible=identity_cache_compatible,
    is_favorite=is_favorite,
    load_artwork_v2_manifest=load_artwork_v2_manifest,
    load_artwork_v2_fast_local_poster=load_artwork_v2_fast_local_poster,
    load_content_quality=load_content_quality,
    load_detail_snapshot=load_detail_snapshot,
    load_detail_snapshot_by_tmdb=load_detail_snapshot_by_tmdb,
    load_playback_progress=load_playback_progress,
    load_settings=load_settings,
    hdd_read_ready=hdd_read_ready,
    load_shared_detail_snapshot=load_shared_detail_snapshot,
    math=math,
    movie_job=movie_job,
    optional_failure=optional_failure,
    os=os,
    premium_title=premium_title,
    parseColor=parseColor,
    quality_badges=quality_badges,
    queue=queue,
    re=re,
    remember_content_quality=remember_content_quality,
    save_detail_snapshot=save_detail_snapshot,
    threading=threading,
    toggle_favorite=toggle_favorite,
)



_configure_search_screen(
    SEARCH_SKIN=_prepare_runtime_skin(SEARCH_SKIN),
    HOME_HERO_FILE=HOME_HERO_FILE,
    ActionMap=ActionMap,
    ContentDetailsScreen=ContentDetailsScreen,
    IconMenuList=IconMenuList,
    InputBox=InputBox,
    Label=Label,
    MessageBox=MessageBox,
    Pixmap=Pixmap,
    PortalSession=PortalSession,
    ThreadPoolExecutor=ThreadPoolExecutor,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _client_from_profile=_client_from_profile,
    _configured_playback_engine=_configured_playback_engine,
    _friendly_error=_friendly_error,
    _load_recent_searches=_load_recent_searches,
    _neutral_utility_glass=_neutral_utility_glass,
    _build_category_extended_backdrop=_build_category_extended_backdrop,
    _build_dynamic_settings_episode_rows=_build_dynamic_settings_episode_rows,
    _DETAIL_PREFETCH_EXECUTOR=_DETAIL_PREFETCH_EXECUTOR,
    _build_dynamic_details_chrome=_build_dynamic_details_chrome,
    _build_dynamic_details_gradient=_build_dynamic_details_gradient,
    _build_integrated_backdrop=_build_integrated_backdrop,
    _detail_cover_artwork=_detail_cover_artwork,
    _download_portal_artwork=_download_portal_artwork,
    _load_visual_bundle=_load_visual_bundle,
    _save_visual_bundle=_save_visual_bundle,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    _player_payload=_player_payload,
    _save_recent_search=_save_recent_search,
    _strip_portal_artwork=_strip_portal_artwork,
    as_completed=as_completed,
    asset=asset,
    hashlib=hashlib,
    json=json,
    ePoint=ePoint,
    eSize=eSize,
    eTimer=eTimer,
    getDesktop=getDesktop,
    identity_cache_compatible=identity_cache_compatible,
    load_profiles=load_profiles,
    load_settings=load_settings,
    one_line=one_line,
    optional_failure=optional_failure,
    os=os,
    parseColor=parseColor,
    provider_urlopen=provider_urlopen,
    premium_title=premium_title,
    quality_badges=quality_badges,
    queue=queue,
)



_configure_grid_screens(
    POSTER_GRID_SKIN=POSTER_GRID_SKIN,
    POSTER_GRID_V2_SKIN=POSTER_GRID_V2_SKIN,
    LIVE_GRID_SKIN=LIVE_GRID_SKIN,
    LOG=LOG,
    Label=Label,
    Pixmap=Pixmap,
    ProgressBar=ProgressBar,
    TASKS=TASKS,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _GRID_ACCENT_EXECUTOR=_GRID_ACCENT_EXECUTOR,
    _GRID_NAV_STATE=_GRID_NAV_STATE,
    _PGV2_HERO_EXECUTOR=_PGV2_HERO_EXECUTOR,
    _PGV2_MATERIAL_EXECUTOR=_PGV2_MATERIAL_EXECUTOR,
    _build_live_adaptive_chrome_211=_build_live_adaptive_chrome_211,
    _fit_live_row_picon_canvas=_fit_live_row_picon_canvas,
    PERSISTENT_GENERATED_DIR=PERSISTENT_GENERATED_DIR,
    _clean_display_text=_clean_display_text,
    _clean_live_channel_name=_clean_live_channel_name,
    _configured_playback_engine=_configured_playback_engine,
    _find_original_artwork=_find_original_artwork,
    _image_url=_image_url,
    _live_picon_diag=_live_picon_diag,
    _optimized_artwork_url=_optimized_artwork_url,
    _player_payload=_player_payload,
    _thumb_path=_thumb_path,
    _valid_cache_file=_valid_cache_file,
    add_recently_played=add_recently_played,
    asset=asset,
    eServiceReference=eServiceReference,
    eTimer=eTimer,
    force_session_silence=force_session_silence,
    gFont=gFont,
    hashlib=hashlib,
    iServiceInformation=iServiceInformation,
    load_settings=load_settings,
    optional_failure=optional_failure,
    os=os,
    parseColor=parseColor,
    quality_badges=quality_badges,
    queue=queue,
    time=time,
)


_configure_cinematic_global(
    CINEMATIC_GLOBAL_SKIN=CINEMATIC_GLOBAL_SKIN,
    ActionMap=ActionMap,
    Label=Label,
    Pixmap=Pixmap,
    eTimer=eTimer,
    asset=asset,
    quality_badges=quality_badges,
    load_content_states=load_content_states,
    load_content_qualities=load_content_qualities,
    _strip_portal_artwork=_strip_portal_artwork,
    _image_url=_image_url,
    _backdrop_url=_backdrop_url,
    _download_portal_artwork=_download_portal_artwork,
    _FAST_POSTER_EXECUTOR=_FAST_POSTER_EXECUTOR,
    _GRID_ACCENT_EXECUTOR=_GRID_ACCENT_EXECUTOR,
    _build_dynamic_details_chrome=_build_dynamic_details_chrome,
    _build_dynamic_details_gradient=_build_dynamic_details_gradient,
    _build_dynamic_poster_accent=_build_dynamic_poster_accent,
    _build_integrated_backdrop=_build_integrated_backdrop,
    _detail_cover_artwork=_detail_cover_artwork,
    _load_visual_bundle=_load_visual_bundle,
    _save_visual_bundle=_save_visual_bundle,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    _release_pixmap_widget=_release_pixmap_widget,
    _native_image_pressure_relief=_native_image_pressure_relief,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _configured_playback_engine=_configured_playback_engine,
    _player_payload=_player_payload,
)



_configure_backdrop_grid(
    BACKDROP_GRID_SKIN=BACKDROP_GRID_SKIN,
    ActionMap=ActionMap,
    Label=Label,
    Pixmap=Pixmap,
    asset=asset,
    cached_png=cached_png,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    _load_visual_bundle=_load_visual_bundle,
    _build_dynamic_details_chrome=_build_dynamic_details_chrome,
    _build_thumbnail=_build_thumbnail,
    _build_cover_thumbnail=_build_cover_thumbnail,
    _grid_card_chrome_from_poster=_grid_card_chrome_from_poster,
    _grid_selection_asset_from_poster=_grid_selection_asset_from_poster,
    _native_image_pressure_relief=_native_image_pressure_relief,
    _download_portal_artwork=_download_portal_artwork,
)

_configure_backdrop_grid2(BACKDROP_GRID2_SKIN=BACKDROP_GRID2_SKIN)


_configure_grid_base(
    ActionMap=ActionMap,
    ArtworkV2=ArtworkV2,
    ChoiceBox=ChoiceBox,
    ContentDetailsScreen=ContentDetailsScreen,
    InputBox=InputBox,
    Label=Label,
    MessageBox=MessageBox,
    OrderedDict=OrderedDict,
    Pixmap=Pixmap,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    ThreadPoolExecutor=ThreadPoolExecutor,
    UltraSearchVirtualKeyBoard=UltraSearchVirtualKeyBoard,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _ArtworkCancelled=_ArtworkCancelled,
    _ADAPTIVE_EXECUTOR=_ADAPTIVE_EXECUTOR,
    _CATEGORY_PREFETCH_EXECUTOR=_CATEGORY_PREFETCH_EXECUTOR,
    _SERIES_HIERARCHY_PREFETCH_EXECUTOR=_SERIES_HIERARCHY_PREFETCH_EXECUTOR,
    _FAST_POSTER_EXECUTOR=_FAST_POSTER_EXECUTOR,
    _DETAIL_PREFETCH_EXECUTOR=_DETAIL_PREFETCH_EXECUTOR,
    _GRID_EPG_EXECUTOR=_GRID_EPG_EXECUTOR,
    _GRID_NAV_STATE=_GRID_NAV_STATE,
    _POSTER_RESCUE_EXECUTOR=_POSTER_RESCUE_EXECUTOR,
    _available_memory_mb=_available_memory_mb,
    _build_cover_thumbnail=_build_cover_thumbnail,
    _build_integrated_backdrop=_build_integrated_backdrop,
    _build_poster_adaptive_chrome_clean=_build_poster_adaptive_chrome_clean,
    _build_thumbnail=_build_thumbnail,
    _catalogue_title=_catalogue_title,
    _clean_display_text=_clean_display_text,
    _clean_live_channel_name=_clean_live_channel_name,
    _clear_artwork_failure=_clear_artwork_failure,
    HOME_HERO_FILE=HOME_HERO_FILE,
    HOME_HERO_SCHEMA=HOME_HERO_SCHEMA,
    _HOME_HERO_LOCK=_HOME_HERO_LOCK,
    _prepare_single_home_hero=_prepare_single_home_hero,
    _build_home_adaptive_focus=_build_home_adaptive_focus,
    _build_home_mood_assets=_build_home_mood_assets,
    _IMAGE_EXECUTOR=_IMAGE_EXECUTOR,
    _configured_playback_engine=_configured_playback_engine,
    _download_portal_artwork=_download_portal_artwork,
    _friendly_error=_friendly_error,
    _grid_card_chrome_cached=_grid_card_chrome_cached,
    _grid_card_chrome_from_poster=_grid_card_chrome_from_poster,
    _grid_fit_card_title=_grid_fit_card_title,
    _grid_page_mood_from_poster=_grid_page_mood_from_poster,
    _grid_selection_asset_from_poster=_grid_selection_asset_from_poster,
    _image_url=_image_url,
    _live_restart_trace=_live_restart_trace,
    _load_content_art=_load_content_art,
    _load_recent_searches=_load_recent_searches,
    _save_recent_search=_save_recent_search,
    _load_visual_bundle=_load_visual_bundle,
    _memory_pressure=_memory_pressure,
    _player_payload=_player_payload,
    _portal_snapshot=_portal_snapshot,
    _runtime_endurance_log=_runtime_endurance_log,
    _save_visual_bundle=_save_visual_bundle,
    _strip_portal_artwork=_strip_portal_artwork,
    _valid_cache_file=_valid_cache_file,
    add_recently_played=add_recently_played,
    as_completed=as_completed,
    asset=asset,
    canonical_art_paths=canonical_art_paths,
    current_plugin_launch=current_plugin_launch,
    ePoint=ePoint,
    eSize=eSize,
    eTimer=eTimer,
    epg_summary=epg_summary,
    gFont=gFont,
    getDesktop=getDesktop,
    hashlib=hashlib,
    identity_cache_compatible=identity_cache_compatible,
    load_artwork_v2_manifest=load_artwork_v2_manifest,
    load_artwork_v2_fast_local_poster=load_artwork_v2_fast_local_poster,
    load_content_qualities=load_content_qualities,
    load_content_states=load_content_states,
    load_detail_snapshot=load_detail_snapshot,
    load_detail_snapshot_by_imdb=load_detail_snapshot_by_imdb,
    load_detail_snapshot_by_tmdb=load_detail_snapshot_by_tmdb,
    load_settings=load_settings,
    hdd_read_ready=hdd_read_ready,
    load_shared_detail_snapshot=load_shared_detail_snapshot,
    mark_watched=mark_watched,
    normalize_quality=normalize_quality,
    one_line=one_line,
    optional_failure=optional_failure,
    os=os,
    premium_title=premium_title,
    quality_badges=quality_badges,
    queue=queue,
    re=re,
    threading=threading,
    time=time,
    toggle_favorite=toggle_favorite,
)



_configure_grid_artwork(
    LOG=LOG,
    ArtworkV2=ArtworkV2,
    OrderedDict=OrderedDict,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    _GLOBAL_GRID_PIXMAP_CACHE=_GLOBAL_GRID_PIXMAP_CACHE,
    _GLOBAL_GRID_PIXMAP_CACHE_LIMIT=_GLOBAL_GRID_PIXMAP_CACHE_LIMIT,
    _GLOBAL_GRID_PIXMAP_CACHE_LOCK=_GLOBAL_GRID_PIXMAP_CACHE_LOCK,
    _GRID_ACCENT_CACHE=_GRID_ACCENT_CACHE,
    _GRID_ACCENT_EXECUTOR=_GRID_ACCENT_EXECUTOR,
    _GRID_MOOD_EXECUTOR=_GRID_MOOD_EXECUTOR,
    _IMAGE_EXECUTOR=_IMAGE_EXECUTOR,
    _VISIBLE_PROVIDER_POSTER_EXECUTOR=_VISIBLE_PROVIDER_POSTER_EXECUTOR,
    _POSTER_THUMB_EXECUTOR=_POSTER_THUMB_EXECUTOR,
    _PILImage=_PILImage,
    _artwork_attempt_allowed=_artwork_attempt_allowed,
    _artwork_file_lock=_artwork_file_lock,
    _available_memory_mb=_available_memory_mb,
    _build_cover_thumbnail=_build_cover_thumbnail,
    _build_thumbnail=_build_thumbnail,
    _cached_live_picon_path=_cached_live_picon_path,
    _clear_artwork_failure=_clear_artwork_failure,
    _download_live_portal_temp_picon=_download_live_portal_temp_picon,
    _download_portal_artwork=_download_portal_artwork,
    _find_original_artwork=_find_original_artwork,
    _grid_card_chrome_cached=_grid_card_chrome_cached,
    _grid_card_chrome_from_poster=_grid_card_chrome_from_poster,
    _grid_page_mood_from_poster=_grid_page_mood_from_poster,
    _grid_selection_asset_from_poster=_grid_selection_asset_from_poster,
    _image_url=_image_url,
    _live_restart_trace=_live_restart_trace,
    _load_visual_bundle=_load_visual_bundle,
    _native_image_pressure_relief=_native_image_pressure_relief,
    _normalize_provider_image_url=_normalize_provider_image_url,
    _optimized_artwork_url=_optimized_artwork_url,
    _persistent_write_ok=_persistent_write_ok,
    _queue_thumbnail_build=_queue_thumbnail_build,
    _record_artwork_failure=_record_artwork_failure,
    _release_pixmap_widget=_release_pixmap_widget,
    _save_visual_bundle=_save_visual_bundle,
    _thumb_path=_thumb_path,
    _valid_cache_file=_valid_cache_file,
    asset=asset,
    cached_png=cached_png,
    ePicLoad=ePicLoad,
    hashlib=hashlib,
    load_artwork_v2_manifest=load_artwork_v2_manifest,
    load_detail_snapshot=load_detail_snapshot,
    load_shared_detail_snapshot=load_shared_detail_snapshot,
    optional_failure=optional_failure,
    os=os,
    queue=queue,
    threading=threading,
    urllib=urllib,
)



_configure_browser_screen(
    BROWSER_SKIN=BROWSER_SKIN,
    ActionMap=ActionMap,
    ChoiceBox=ChoiceBox,
    ContentDetailsScreen=ContentDetailsScreen,
    HOME_HERO_FILE=HOME_HERO_FILE,
    IMAGE_CACHE_DIR=IMAGE_CACHE_DIR,
    PERSISTENT_ARTWORK_DIRS=(PERSISTENT_ART_DIR,BACKDROP_CACHE_DIR,PERSISTENT_GENERATED_DIR,PERSISTENT_HOME_DIR,PERSISTENT_LIVE_DIR),
    IconMenuList=IconMenuList,
    Input=Input,
    InputBox=InputBox,
    Label=Label,
    MessageBox=MessageBox,
    NovaSettingsScreen=NovaSettingsScreen,
    Pixmap=Pixmap,
    PortalGlobalSearchScreen=PortalGlobalSearchScreen,
    PortalSession=PortalSession,
    PremiumLiveGridScreen=PremiumLiveGridScreen,
    PremiumPosterGridScreen=PremiumPosterGridScreen,
    PremiumPosterGridV2Screen=PremiumPosterGridV2Screen,
    PremiumGlobalCinematicScreen=PremiumGlobalCinematicScreen,
    PremiumBackdropGridScreen=PremiumBackdropGridScreen,
    PremiumBackdropGrid2Screen=PremiumBackdropGrid2Screen,
    SettingsGlassNoticeScreen=SettingsGlassNoticeScreen,
    ThreadPoolExecutor=ThreadPoolExecutor,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _IMAGE_EXECUTOR=_IMAGE_EXECUTOR,
    _GRID_EPG_EXECUTOR=_GRID_EPG_EXECUTOR,
    _PICON_CACHE_EXECUTOR=_PICON_CACHE_EXECUTOR,
    _CATEGORY_PREFETCH_EXECUTOR=_CATEGORY_PREFETCH_EXECUTOR,
    _accent_for=_accent_for,
    _build_category_adaptive_chrome=_build_category_adaptive_chrome,
    _build_category_extended_backdrop=_build_category_extended_backdrop,
    _build_cover_thumbnail=_build_cover_thumbnail,
    _grid_card_chrome_from_poster=_grid_card_chrome_from_poster,
    _grid_selection_asset_from_poster=_grid_selection_asset_from_poster,
    _load_visual_bundle=_load_visual_bundle,
    _save_visual_bundle=_save_visual_bundle,
    _cached_live_picon_path=_cached_live_picon_path,
    _fit_live_picon_canvas=_fit_live_picon_canvas,
    PERSISTENT_GENERATED_DIR=PERSISTENT_GENERATED_DIR,
    _category_cache_get=_category_cache_get,
    _category_cache_put=_category_cache_put,
    _category_cache_age=_category_cache_age,
    _CATEGORY_CACHE_TTL=_CATEGORY_CACHE_TTL,
    _clean_display_text=_clean_display_text,
    _clean_live_channel_name=_clean_live_channel_name,
    _configured_playback_engine=_configured_playback_engine,
    _download_portal_artwork=_download_portal_artwork,
    _download_public_live_picon=_download_public_live_picon,
    _download_live_portal_temp_picon=_download_live_portal_temp_picon,
    _friendly_error=_friendly_error,
    _image_url=_image_url,
    _letter_placeholder=_letter_placeholder,
    _live_restart_trace=_live_restart_trace,
    _load_live_picon_failed_queue=_load_live_picon_failed_queue,
    _load_recent_searches=_load_recent_searches,
    _native_image_pressure_relief=_native_image_pressure_relief,
    _neutral_utility_glass=_neutral_utility_glass,
    _new_isolated_source_client=_new_isolated_source_client,
    _normalize_provider_image_url=_normalize_provider_image_url,
    _player_payload=_player_payload,
    _release_pixmap_widget=_release_pixmap_widget,
    _save_live_picon_failed_queue=_save_live_picon_failed_queue,
    _save_recent_search=_save_recent_search,
    _search_key=_search_key,
    _source_art_url=_source_art_url,
    _strip_portal_artwork=_strip_portal_artwork,
    _settings_text_width_px=_settings_text_width_px,
    add_recently_played=add_recently_played,
    as_completed=as_completed,
    asset=asset,
    canonical_art_paths=canonical_art_paths,
    cleanup_image_cache=cleanup_image_cache,
    clear_history=clear_history,
    ePoint=ePoint,
    eSize=eSize,
    eTimer=eTimer,
    epg_summary=epg_summary,
    event_times=event_times,
    export_live_category_bouquet=export_live_category_bouquet,
    export_receiver_items=export_receiver_items,
    export_series_category_bouquets=export_series_category_bouquets,
    reload_bouquets=reload_bouquets,
    force_session_silence=force_session_silence,
    getDesktop=getDesktop,
    hashlib=hashlib,
    hdd_read_ready=hdd_read_ready,
    install_recording_timer=install_recording_timer,
    json=json,
    loadPNG=loadPNG,
    load_content_states=load_content_states,
    load_continue_watching=load_continue_watching,
    load_detail_snapshot=load_detail_snapshot,
    load_favorites=load_favorites,
    load_settings=load_settings,
    mark_watched=mark_watched,
    optional_failure=optional_failure,
    os=os,
    parental_hash_pin=parental_hash_pin,
    parental_is_unlocked=parental_is_unlocked,
    parental_pin_is_default=parental_pin_is_default,
    parental_verify_pin=parental_verify_pin,
    parseColor=parseColor,
    premium_title=premium_title,
    prepare_portal_recording=prepare_portal_recording,
    prune_persistent_cache=prune_persistent_cache,
    quality_badges=quality_badges,
    queue=queue,
    remove_from_history=remove_from_history,
    save_settings=save_settings,
    save_theme=save_theme,
    time=time,
    urllib=urllib,
)



_configure_portal_list(
    MAIN_SKIN=MAIN_SKIN,
    font_scale_skin=_apply_user_font_scale,
    _plugin_original_service_getter=lambda: _PLUGIN_ORIGINAL_SERVICE,
    _plugin_service_captured_getter=lambda: _PLUGIN_SERVICE_CAPTURED,
    DiagnosticsScreen=DiagnosticsScreen,
    SettingsGlassChoiceScreen=SettingsGlassChoiceScreen,
    SettingsGlassNoticeScreen=SettingsGlassNoticeScreen,
    SettingsGlassInputScreen=SettingsGlassInputScreen,
    HOME_HERO_FILE=HOME_HERO_FILE,
    HTTP_WARNING=HTTP_WARNING,
    http_warning_for=http_warning_for,
    IconMenuList=IconMenuList,
    PortalHomeScreen=PortalHomeScreen,
    PortalSession=PortalSession,
    StalkerClient=StalkerClient,
    THEMES=THEMES,
    _=_,
    _active_theme=_active_theme,
    _build_category_adaptive_chrome=_build_category_adaptive_chrome,
    _client_from_profile=_client_from_profile,
    _friendly_portal_error=_friendly_portal_error,
    _looks_like_m3u_url=_looks_like_m3u_url,
    _probe_m3u_url=_probe_m3u_url,
    _validate_profile_client=_validate_profile_client,
    asset=asset,
    consume_recovery_notices=consume_recovery_notices,
    create_backup=create_backup,
    inspect_backup=inspect_backup,
    list_backups=list_backups,
    restore_backup=restore_backup,
    disable_profiles=disable_profiles,
    duplicate_profile=duplicate_profile,
    enable_profile=enable_profile,
    export_live_integration=export_live_integration,
    load_disabled_profiles=load_disabled_profiles,
    load_profiles=load_profiles,
    load_server_library=load_server_library,
    import_server_library_profiles=import_server_library_profiles,
    load_settings=load_settings,
    mark_http_consent=mark_http_consent,
    optional_failure=optional_failure,
    permanently_delete_profile=permanently_delete_profile,
    premium_title=premium_title,
    purge_profile_data=purge_profile_data,
    queue=queue,
    reload_bouquets=reload_bouquets,
    reorder_profile=reorder_profile,
    replace_profile=replace_profile,
    requires_http_consent=requires_http_consent,
    restore_plugin_service=restore_plugin_service,
    save_profiles=save_profiles,
    save_settings=save_settings,
    save_theme=save_theme,
    save_ui_state=save_ui_state,
    start_proxy_server=start_proxy_server,
    unexport_live_integration=unexport_live_integration,
)



_configure_home_screen(
    HOME_SKIN=HOME_SKIN,
    BACKDROP_CACHE_DIR=BACKDROP_CACHE_DIR,
    HOME_HERO_FILE=HOME_HERO_FILE,
    HOME_HERO_SCHEMA=HOME_HERO_SCHEMA,
    HOME_HERO_TTL=HOME_HERO_TTL,
    HOME_RUNTIME_DIR=HOME_RUNTIME_DIR,
    IMAGE_CACHE_DIR=IMAGE_CACHE_DIR,
    THUMB_CACHE_DIR=THUMB_CACHE_DIR,
    LOG=LOG,
    PortalSession=PortalSession,
    TMDBClient=TMDBClient,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _CATEGORY_PREFETCH_EXECUTOR=_CATEGORY_PREFETCH_EXECUTOR,
    _HOME_HERO_LOCK=_HOME_HERO_LOCK,
    _IMAGE_EXECUTOR=_GLOBAL_HYDRATION_EXECUTOR,
    PERSISTENT_GENERATED_DIR=PERSISTENT_GENERATED_DIR,
    _fit_live_picon_canvas=_fit_live_picon_canvas,
    _PILImage=_PILImage,
    _build_home_adaptive_focus=_build_home_adaptive_focus,
    _build_home_mood_assets=_build_home_mood_assets,
    _cleanup_legacy_home_hero_storage=_cleanup_legacy_home_hero_storage,
    _category_cache_get=_category_cache_get,
    _category_cache_put=_category_cache_put,
    _invalidate_source_navigation_cache=_invalidate_source_navigation_cache,
    _clean_display_text=_clean_display_text,
    _configured_playback_engine=_configured_playback_engine,
    _client_from_profile=_client_from_profile,
    _fsync_parent_dir=_fsync_parent_dir,
    _home_prepare_art=_home_prepare_art,
    _home_cached_art=_home_cached_art,
    _player_payload=_player_payload,
    _valid_cache_file=_valid_cache_file,
    add_recently_played=add_recently_played,
    asset=asset,
    current_plugin_launch=current_plugin_launch,
    load_playback_progress=load_playback_progress,
    load_profiles=load_profiles,
    load_recently_played=load_recently_played,
    history_revision=history_revision,
    load_settings=load_settings,
    load_ui_state=load_ui_state,
    optional_failure=optional_failure,
    premium_title=premium_title,
    save_profiles=save_profiles,
    save_ui_state=save_ui_state,
    home_boot_pixmap=home_boot_pixmap,
    splash_home_is_warm=splash_home_is_warm,
    splash_home_account_info=splash_home_account_info,
    splash_home_recent_assets=splash_home_recent_assets,
    ContentDetailsScreen=ContentDetailsScreen,
    NovaSettingsScreen=NovaSettingsScreen,
    PortalBrowserScreen=PortalBrowserScreen,
    PortalGlobalSearchScreen=PortalGlobalSearchScreen,
)











from .ui_screens_splash import (
    configure_splash_screen as _configure_splash_screen,
    SplashScreen,
)
_configure_splash_screen(
    FirstRunWizardScreen,
    PortalListScreen,
    WIZARD_DONE_FILE,
    _scale_skin,
    _schedule_startup_cache_maintenance,
    asset,
    restore_plugin_service,
)





_configure_series_screen(
    SERIES_EPISODES_SKIN=SERIES_EPISODES_SKIN,
    ActionMap=ActionMap,
    AdaptiveInformationScreen=AdaptiveInformationScreen,
    ChoiceBox=ChoiceBox,
    DOWNLOADS=DOWNLOADS,
    DownloadsManagerScreen=DownloadsManagerScreen,
    IconMenuList=IconMenuList,
    Label=Label,
    Pixmap=Pixmap,
    UltraStalkerPlayer=UltraStalkerPlayer,
    _clean_display_text=_clean_display_text,
    _compose_information_text=_compose_information_text,
    _configured_playback_engine=_configured_playback_engine,
    _player_payload=_player_payload,
    asset=asset,
    episode_job=episode_job,
    load_content_quality=load_content_quality,
    load_content_qualities=load_content_qualities,
    load_content_states=load_content_states,
    load_settings=load_settings,
    mark_watched=mark_watched,
    normalize_quality=normalize_quality,
    optional_failure=optional_failure,
    re=re,
    remember_content_quality=remember_content_quality,
)



# R47: finish the cold-start GC guard quietly. Flush generation 0 only, then
# restore the receiver's original automatic-GC state. No thresholds are changed.
try:
    if _IMPORT46_GC_WAS_ENABLED:
        try:
            gc.collect(0)
        finally:
            gc.enable()
except Exception:
    try:
        if _IMPORT46_GC_WAS_ENABLED:
            gc.enable()
    except Exception:
        pass

