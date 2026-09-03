# -*- coding: utf-8 -*-
"""Native Ultra Stalker player.

The screen lifecycle, InfoBar mixins, service-reference creation, engine
switching and resume behaviour are adapted from the user-supplied reference implementation
1.42 player. All paths, classes, settings and state remain independent inside
UltraStalker.
"""
from __future__ import absolute_import, print_function

import hashlib
import json
import gc
import ctypes
import os
import signal
import time
import queue
import re
import threading
import unicodedata

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter, ImageEnhance as _ImageEnhance
except Exception:
    _PILImage = None; _ImageDraw = None; _ImageFilter = None

from ..securefs import secure_private_dir
from ..persistent_cache import ROOT as PERSISTENT_CACHE_ROOT, GENERATED as PERSISTENT_GENERATED_DIR, persistent_write_gate
from ..ultra import (
    remember_content_quality,
)
from ..client import StalkerClient
from ..storage import add_recently_played, touch_recently_played, load_settings, save_settings
from ..title_clean import catalogue_title as _catalogue_title
from .subtitles_online_golden57 import cached_subtitle, search_arabic, download_candidate, parse_srt
from ..core.recovery import runtime_interruption_action, smart_recovery_allowed
from ..core.runtime_log import breadcrumb as runtime_breadcrumb
from ..log import get_logger, optional_failure
from ..core.executor import LazyThreadPoolExecutor
from .player_runtime import _matching_external_player_pids, _descendant_pids, _all_external_player_pids

try:
    from urllib.parse import unquote, urlparse
except ImportError:
    from urllib import unquote
    from urlparse import urlparse

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
try:
    from Components.MultiContent import MultiContentEntryPixmapAlphaBlend
except Exception:
    MultiContentEntryPixmapAlphaBlend = MultiContentEntryPixmapAlphaTest
from Components.ProgressBar import ProgressBar
try:
    from Components.ScrollLabel import ScrollLabel
except Exception:
    ScrollLabel = Label
from Components.Pixmap import MultiPixmap, Pixmap
from Components.ServiceEventTracker import ServiceEventTracker
try:
    from Components.ServiceEventTracker import InfoBarBase
except Exception:
    class InfoBarBase(object):
        def __init__(self, *args, **kwargs):
            pass
from enigma import ePicLoad, ePoint, eSize, getDesktop, eServiceReference, eTimer, iPlayableService, iServiceInformation, gFont, eListboxPythonMultiContent, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_HALIGN_CENTER, RT_VALIGN_CENTER, loadPNG
from skin import parseColor
try:
    from enigma import eDVBVolumecontrol
except Exception:
    eDVBVolumecontrol = None
from Screens.MessageBox import MessageBox
try:
    from Screens.SubtitleDisplay import SubtitleDisplay
except Exception:
    SubtitleDisplay = None
try:
    from Screens.ChoiceBox import ChoiceBox
except Exception:
    ChoiceBox = None
from Screens.Screen import Screen
from Tools.BoundFunction import boundFunction

LOG = get_logger()
_PROGRESS_FRAME_EXECUTOR=LazyThreadPoolExecutor(max_workers=1,thread_name_prefix="ultrastalker-progress")
_PROGRESS_FRAME_PENDING=set()
_PROGRESS_FRAME_LOCK=threading.RLock()

def _process_rss_kb():
    try:
        with open("/proc/self/status","r") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts=line.split()
                    return int(parts[1]) if len(parts)>1 else 0
    except Exception as exc:
        optional_failure("player.silent_guard",exc)
    return 0

def _malloc_trim():
    try:
        libc=ctypes.CDLL(None)
        trim=getattr(libc,"malloc_trim",None)
        if trim is not None:
            trim.argtypes=[ctypes.c_size_t];trim.restype=ctypes.c_int
            trim(0)
    except Exception as exc:
        optional_failure("player.silent_guard",exc)


def force_session_silence(session, streamurl="", force=False, stop_native=True, exclude_pids=None, owned_pids=None):
    """Best-effort silence restricted to this stream and explicitly owned PIDs."""
    nav=getattr(session,"nav",None)
    if stop_native and nav is not None:
        try: nav.stopService()
        except Exception as exc: optional_failure("player.force_nav_stop",exc)
        try:
            pnav=getattr(nav,"pnav",None)
            stop=getattr(pnav,"stopService",None)
            if callable(stop): stop()
        except Exception as exc: optional_failure("player.force_pnav_stop",exc)
    sig=signal.SIGKILL if force else signal.SIGTERM
    excluded=set(int(x) for x in (exclude_pids or ()) if str(x).isdigit())
    owned=set(int(x) for x in (owned_pids or ()) if str(x).isdigit())
    targets=set(_matching_external_player_pids(streamurl))|owned|_descendant_pids(owned)
    for pid in targets:
        if int(pid) in excluded:continue
        try:os.kill(pid,sig)
        except OSError:pass
        except Exception as exc:optional_failure("player.force_external_stop",exc)
    return True


def _optional_infobar(name):
    try:
        module = __import__("Screens.InfoBarGenerics", fromlist=[name])
        return getattr(module, name)
    except Exception as exc:
        optional_failure("InfoBar.%s" % name, exc)
        return type("Missing%s" % name, (object,), {"__init__": lambda self, *args, **kwargs: None})


InfoBarAudioSelection = _optional_infobar("InfoBarAudioSelection")
InfoBarMoviePlayerSummarySupport = _optional_infobar("InfoBarMoviePlayerSummarySupport")
InfoBarNotifications = _optional_infobar("InfoBarNotifications")
InfoBarSeek = _optional_infobar("InfoBarSeek")
InfoBarSubtitleSupport = _optional_infobar("InfoBarSubtitleSupport")
InfoBarSummarySupport = _optional_infobar("InfoBarSummarySupport")

try:
    from enigma import eAVSwitch
except Exception:
    try:
        from enigma import eAVControl as eAVSwitch
    except Exception:
        eAVSwitch = None

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(PLUGIN_DIR, "assets_fhd")
PLAYER_ASSET_DIR = os.path.join(ASSET_DIR, "player")
PLAYER_FALLBACK_INFOBAR_DIR = os.path.join(PLAYER_ASSET_DIR, "fallback_infobar")

def _fallback_player_frames():
    """Bundled receiver-safe neutral glass.

    This is intentionally independent of poster/picon/HDD state.  It is the
    first-line player chrome so a late/missing adaptive source can never leave
    naked text and controls floating over video.
    """
    names=("main","poster_halo","poster","live_picon","track","keybar",
           "chip_quality","chip_video","chip_audio_codec","chip_bitrate",
           "chip_stream","chip_audio","chip_subtitles","chip_engine")
    out={}
    for name in names:
        path=os.path.join(PLAYER_FALLBACK_INFOBAR_DIR,"%s.png"%name)
        if os.path.isfile(path):
            out[name]=path
    out["glow"]=""
    out["accent"]="#4aa2d6"
    out["accent_soft"]="#9bd9f5"
    out["accent_neon"]="#69c9f4"
    return out

def _adaptive_player_frames(source_path):
    """Create player chrome with the exact floating-glass language used by Categories/Portal rows.

    Geometry stays identical to us226. Only the material changes: dark calm glass,
    restrained poster-derived tint, soft internal glow, polished inner rim and top sheen.
    """
    if _PILImage is None or _ImageDraw is None:
        return {}
    try:
        if not source_path or not os.path.isfile(source_path):
            return {}
        with _PILImage.open(source_path) as im:
            im = im.convert("RGB")
            im.thumbnail((96, 96))
            pixels = [p for p in list(im.getdata()) if 28 < sum(p) / 3.0 < 230]
            if not pixels:
                return {}
            pixels.sort(key=lambda p: (max(p)-min(p)) + sum(p)/12.0, reverse=True)
            sample = pixels[:max(8, len(pixels)//8)]
            rgb = tuple(int(sum(px[i] for px in sample)/len(sample)) for i in range(3))
        import colorsys
        rr, gg, bb = [v/255.0 for v in rgb]
        h, l, sat = colorsys.rgb_to_hls(rr, gg, bb)
        sat = max(.38, min(.66, sat))
        l = max(.38, min(.54, l))
        rr, gg, bb = colorsys.hls_to_rgb(h, l, sat)
        accent = (int(rr*255), int(gg*255), int(bb*255))
        # Secondary accent is a slightly softer/lighter cousin, same idea as category glass.
        accent2 = tuple(min(255, int(v*0.78 + 255*0.22)) for v in accent)
        base = (4, 12, 18)

        def mix(a, b, t):
            t = max(0.0, min(1.0, float(t)))
            return tuple(int(a[i]*(1.0-t) + b[i]*t) for i in range(3))

        cache = os.path.join(PERSISTENT_GENERATED_DIR,"player_glass241")
        secure_private_dir(cache)
        key = hashlib.sha1((source_path + str(os.path.getmtime(source_path)) + str(accent) + "glass241").encode("utf-8", "ignore")).hexdigest()[:16]
        out = {}

        specs = {
            # name: (size, radius, selected_like, fill_alpha, edge_alpha, glow_alpha)
            "main": ((1800,200), 26, True, 226, 238, 196),
            # Separate halo canvas. This is intentionally larger than the poster frame
            # so Enigma2 cannot clip the outer neon at the artwork boundary.
            "poster_halo": ((310,415), 28, True, 0, 255, 255),
            "poster": ((270,395), 22, True, 0, 255, 255),
            "live_picon": ((240,152), 18, True, 118, 245, 188),
            "keybar": ((1600,58), 22, True, 232, 228, 178),
            "chip_quality": ((150,42), 15, False, 232, 226, 168),
            "chip_video": ((130,42), 15, False, 232, 226, 168),
            "chip_audio_codec": ((120,42), 15, False, 232, 226, 168),
            "chip_bitrate": ((190,42), 15, False, 232, 226, 168),
            "chip_stream": ((150,42), 15, False, 232, 226, 168),
            "chip_audio": ((150,42), 15, False, 232, 226, 168),
            "chip_subtitles": ((170,42), 15, False, 232, 226, 168),
            "chip_engine": ((210,42), 15, False, 232, 226, 168),
        }

        for name, (size, radius, selected, fill_alpha, edge_alpha, glow_alpha) in specs.items():
            target = os.path.join(cache, "%s_%s.png" % (key, name))
            if not os.path.isfile(target):
                w, hh = size
                img = _PILImage.new("RGBA", size, (0,0,0,0))

                d = _ImageDraw.Draw(img)
                if name == "poster_halo":
                    # us242: renderer-safe poster neon. OpenBH on this box visibly
                    # drops/weakens soft semi-transparent Gaussian bloom. Use stepped,
                    # near-opaque adaptive energy bands baked into the SAME pixmap as
                    # the poster so the effect survives framebuffer composition.
                    poster_box = (30, 20, 279, 394)
                    hot = mix(accent, (255,255,255), .62)
                    white_hot = mix(accent, (255,255,255), .92)
                    outer = mix(accent, (255,255,255), .28)

                    # Draw outside-in. These are deliberately opaque enough to survive
                    # Enigma2 alphatest/blend on OpenBH while still reading as neon.
                    bands = (
                        (15, outer, 120, 8),
                        (11, accent, 170, 7),
                        (8,  hot,    215, 6),
                        (5,  hot,    245, 5),
                        (3,  white_hot, 255, 3),
                    )
                    for expand, col, alpha, width in bands:
                        box=(poster_box[0]-expand,poster_box[1]-expand,poster_box[2]+expand,poster_box[3]+expand)
                        d.rounded_rectangle(box, radius=18+expand//2, outline=col+(alpha,), width=width)

                    # No external flare ticks: on the receiver these rendered as four
                    # white crop marks around the poster. Keep only the continuous rim.

                    # Bake poster into the same RGBA pixmap. This is already proven to
                    # render on the receiver; only the soft bloom was disappearing.
                    try:
                        prepared = _prepare_player_poster_fill(source_path, (250,375))
                        artwork_path = prepared if prepared and os.path.isfile(prepared) else source_path
                        with _PILImage.open(artwork_path) as art:
                            art = art.convert("RGBA")
                            if art.size != (250,375):
                                res = getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
                                art = art.resize((250,375), res)
                            img.alpha_composite(art, (30,20))
                        d=_ImageDraw.Draw(img)
                        # Hard hot rim at the exact poster edge, plus one brighter corner
                        # pass to give the premium "energized frame" look.
                        d.rounded_rectangle((29,19,280,395),radius=18,outline=hot+(255,),width=3)
                        d.rounded_rectangle((30,20,279,394),radius=16,outline=white_hot+(255,),width=1)
                    except Exception as exc:
                        optional_failure("player.poster_composite", exc)
                elif name == "poster":
                    # The foreground frame is now deliberately clean.  All bloom is on
                    # poster_halo behind it, so nothing can be clipped by this 270x395 widget.
                    hot = mix(accent, (255,255,255), .72)
                    white_hot = mix(accent, (255,255,255), .94)
                    d.rounded_rectangle((4,4,w-5,hh-5),radius=22,outline=accent+(255,),width=3)
                    d.rounded_rectangle((6,6,w-7,hh-7),radius=20,outline=hot+(255,),width=2)
                    d.rounded_rectangle((8,8,w-9,hh-9),radius=18,outline=white_hot+(220,),width=1)
                else:
                    fill_mix = 0.13 if name == "main" else (0.115 if name == "keybar" else 0.105)
                    fill = mix(base, accent, fill_mix)
                    d.rounded_rectangle((2,2,w-3,hh-3), radius=radius,
                                        fill=fill + (fill_alpha,),
                                        outline=accent + (edge_alpha,), width=(3 if selected else 2))
                    inner = mix(accent2, (255,255,255), .34)
                    d.rounded_rectangle((7,7,w-8,hh-8), radius=max(8,radius-6),
                                        outline=inner + ((126 if selected else 92),), width=1)

                    # Polished upper reflection copied from the approved category glass treatment.
                    sheen = _PILImage.new("RGBA", size, (0,0,0,0))
                    sd = _ImageDraw.Draw(sheen)
                    hi = mix(accent, (255,255,255), .54)
                    top = max(18, int(hh*0.43))
                    for yy in range(8, top, 5):
                        t = (yy-8.0)/max(1.0, top-8.0)
                        a = max(0, int((38 if selected else 25) * (1.0-t)**1.65))
                        sd.rounded_rectangle((13, yy, w-14, min(hh-11, yy+7)),
                                             radius=max(6,radius-9), fill=hi + (a,))
                    if _ImageFilter is not None:
                        sheen = sheen.filter(_ImageFilter.GaussianBlur(radius=4 if selected else 3))
                    img = _PILImage.alpha_composite(img, sheen)
                    if name == "keybar":
                        kd = _ImageDraw.Draw(img)
                        sep = mix(accent, (255,255,255), .30)
                        # Subtle dividers create a real control dock rather than a long empty strip.
                        for xx in (235, 485, 755, 1045, 1345):
                            kd.line((xx,13,xx,hh-13), fill=sep + (72,), width=1)

                img.save(target, "PNG")
            out[name] = target

        # Neutral remaining track. The watched segment itself is rendered by a dedicated
        # neon pixmap so it can look like a laser/glow rather than a flat ProgressBar.
        track_target = os.path.join(cache, "%s_track.png" % key)
        if not os.path.isfile(track_target):
            w, hh = 1350, 8
            track = _PILImage.new("RGBA", (w,hh), (0,0,0,0))
            td = _ImageDraw.Draw(track)
            td.rounded_rectangle((0,2,w-1,hh-3), radius=2, fill=(7,11,15,222))
            td.rounded_rectangle((0,2,w-1,hh-3), radius=2, outline=mix(accent,(255,255,255),.20)+(105,), width=1)
            track.save(track_target, "PNG")
        out["track"] = track_target
        out["glow"] = ""
        out["accent"] = "#%02x%02x%02x" % accent
        out["accent_soft"] = "#%02x%02x%02x" % mix(accent, (255,255,255), .54)
        out["accent_neon"] = "#%02x%02x%02x" % mix(accent, (255,255,255), .32)
        return out
    except Exception as exc:
        optional_failure("player.adaptive_frames", exc)
        return {}

def _progress_neon_frame(accent_hex, value):
    """Render one cached laser-like watched-progress frame (0..100).

    The beam itself communicates progress; there is no detached dot/target. A compact
    white-hot flare at the leading edge is part of the beam, matching the supplied reference.
    """
    if _PILImage is None or _ImageDraw is None:
        return ""
    try:
        value=max(0,min(100,int(value)))
        hx=str(accent_hex or "#55b9ff").lstrip("#")
        if len(hx)!=6: hx="55b9ff"
        accent=tuple(int(hx[i:i+2],16) for i in (0,2,4))
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"progress_neon239")
        secure_private_dir(cache)
        target=os.path.join(cache,"%s_%03d.png"%(hx,value))
        if os.path.isfile(target): return target
        w,h=1350,34
        img=_PILImage.new("RGBA",(w,h),(0,0,0,0))
        if value<=0:
            img.save(target,"PNG"); return target
        end=max(2,min(w-2,int(round((w-4)*value/100.0))))
        y=h//2
        # Broad atmospheric bloom.
        bloom=_PILImage.new("RGBA",(w,h),(0,0,0,0)); bd=_ImageDraw.Draw(bloom)
        bd.line((2,y,end,y),fill=accent+(205,),width=11)
        if _ImageFilter is not None: bloom=bloom.filter(_ImageFilter.GaussianBlur(radius=6))
        img=_PILImage.alpha_composite(img,bloom)
        # Tighter neon halo.
        halo=_PILImage.new("RGBA",(w,h),(0,0,0,0)); hd=_ImageDraw.Draw(halo)
        hd.line((2,y,end,y),fill=accent+(245,),width=7)
        if _ImageFilter is not None: halo=halo.filter(_ImageFilter.GaussianBlur(radius=2.8))
        img=_PILImage.alpha_composite(img,halo)
        d=_ImageDraw.Draw(img)
        hot=tuple(min(255,int(v*.48+255*.52)) for v in accent)
        core=tuple(min(255,int(v*.20+255*.80)) for v in accent)
        # Bright continuous core: the same premium laser language as the reference.
        d.line((2,y,end,y),fill=hot+(255,),width=4)
        d.line((2,y,end,y),fill=core+(255,),width=2)
        # Leading flare, not a separate point: a short comet bloom integrated into the beam.
        flare=_PILImage.new("RGBA",(w,h),(0,0,0,0)); fd=_ImageDraw.Draw(flare)
        fd.line((max(2,end-28),y,end+min(16,w-1-end),y),fill=core+(250,),width=5)
        fd.line((end,max(1,y-9),end,min(h-2,y+9)),fill=core+(210,),width=2)
        fd.ellipse((end-5,y-5,end+5,y+5),fill=(255,255,255,250))
        if _ImageFilter is not None: flare=flare.filter(_ImageFilter.GaussianBlur(radius=4.0))
        img=_PILImage.alpha_composite(img,flare)
        # Re-sharpen a tiny white-hot center within the flare.
        d=_ImageDraw.Draw(img); d.ellipse((end-2,y-2,end+2,y+2),fill=(255,255,255,255))
        img.save(target,"PNG")
        return target
    except Exception as exc:
        optional_failure("player.progress_neon",exc); return ""


def _schedule_progress_neon_frame(accent_hex,value):
    """Generate missing progress PNGs off Enigma2's GUI thread."""
    try:
        value=max(0,min(100,int(value)));hx=str(accent_hex or "#55b9ff").lstrip("#");key=(hx,value)
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"progress_neon239");path=os.path.join(cache,"%s_%03d.png"%(hx,value))
        if os.path.isfile(path):return path
        with _PROGRESS_FRAME_LOCK:
            if key in _PROGRESS_FRAME_PENDING:return ""
            _PROGRESS_FRAME_PENDING.add(key)
        def work():
            try:return _progress_neon_frame("#"+hx,value)
            finally:
                with _PROGRESS_FRAME_LOCK:_PROGRESS_FRAME_PENDING.discard(key)
        _PROGRESS_FRAME_EXECUTOR.submit(work)
    except Exception as exc:optional_failure("player.progress_schedule",exc)
    return ""

def shutdown_player_workers(wait=False):
    try:_PROGRESS_FRAME_EXECUTOR.shutdown(wait=bool(wait),cancel_futures=True)
    except Exception as exc:optional_failure("player.progress_shutdown",exc)

PLAYER_PROGRESS_X = 370
PLAYER_PROGRESS_Y = 858
PLAYER_PROGRESS_W = 1350
PLAYER_CRYSTAL_SIZE = 44
PLAYER_CRYSTAL_Y = PLAYER_PROGRESS_Y - ((PLAYER_CRYSTAL_SIZE - 8) // 2)

ASPECT_RATIOS = {
    0: "4:3 Letterbox",
    1: "4:3 PanScan",
    2: "16:9",
    3: "16:9 Always",
    4: "16:10 Letterbox",
    5: "16:10 PanScan",
    6: "16:9 Letterbox",
}


def _asset(name):
    return os.path.join(ASSET_DIR, name)


def _player_asset(name):
    return os.path.join(PLAYER_ASSET_DIR, name)


NEXT_EPISODE_AUTOPLAY_SESSION = True

ENGINE_NAMES = {
    1: "Native",
    4097: "GStreamer",
    5001: "GstPlayer",
    5002: "ExtePlayer3",
    8193: "ServiceApp",
}


def _engine_label(value):
    try:
        value = int(value)
    except Exception:
        return str(value)
    return "%s %s" % (ENGINE_NAMES.get(value, "Engine"), value)


def _extension(url):
    try:
        path = unquote(urlparse(str(url)).path)
        ext = os.path.splitext(path)[-1].lower()
        if ext in (".ts", ".m3u8", ".mp4", ".mkv", ".avi", ".mpd", ".mov", ".webm"):
            return ext.lstrip(".").upper()
    except Exception as exc:
        optional_failure("player", exc)
    return "STREAM"


def _quality(item, name):
    item=item if isinstance(item,dict) else {}
    fields=("quality","video_quality","resolution","video_resolution","height","width","format","stream_quality",
            "name","title","cmd","command","url","stream_url","filename")
    text=" ".join(str(item.get(k) or "") for k in fields) + " " + str(name or "")
    up=text.upper()
    if re.search(r"\b(?:4K|UHD|2160P?|3840(?:X2160)?)\b",up): return "UHD 2160p"
    if re.search(r"\b(?:FULL[ ._-]?HD|FHD|1080P?|1920(?:X1080)?)\b",up): return "FHD 1080p"
    if re.search(r"\b(?:HD|720P?|1280(?:X720)?)\b",up): return "HD 720p"
    if re.search(r"\b(?:SD|576P?|480P?|720X576|720X480)\b",up): return "SD"
    return "AUTO"


def _letter_logo(name, media_type):
    if media_type == "vod":
        return _asset("placeholder_movie_x.png")
    if media_type in ("series", "episode"):
        return _asset("placeholder_series_x.png")
    text = str(name or "S").strip().upper()
    char = text[:1] if text else "S"
    if not (char.isalpha() or char.isdigit()):
        char = "S"
    candidate = _asset(os.path.join("list_icons", "logo_letter_%s.png" % char))
    if os.path.exists(candidate):
        return candidate
    return _asset("placeholder_live_x.png")



IMAGE_CACHE_DIR = os.path.join(PERSISTENT_GENERATED_DIR,"player_images")
try:
    secure_private_dir(IMAGE_CACHE_DIR)
except OSError:
    pass


def _image_value(item):
    if not isinstance(item, dict):
        return ""
    # VOD/Series player artwork must be the canonical external poster prepared
    # by the catalogue/details pipeline. Portal cover/screenshot fields are
    # deliberately ignored so the InfoBar can never resurrect low-quality art.
    media_type=str(item.get("_media_type") or item.get("media_type") or "").lower()
    keys=("_player_poster",) if media_type in ("vod","movie","series","episode") else ("_player_poster","logo","poster","poster_url","cover","cover_url","image","img")
    for key in keys:
        value=item.get(key)
        if value is not None and str(value).strip() and str(value).strip().lower() not in ("null","none"):
            return str(value).strip()
    return ""


def _adaptive_info_frames(source_path):
    if _PILImage is None or _ImageDraw is None or not source_path or not os.path.isfile(source_path):
        return {}
    try:
        with _PILImage.open(source_path) as im:
            im=im.convert("RGB");im.thumbnail((72,96))
            pixels=[p for p in im.getdata() if 25<sum(p)/3.0<235]
        if not pixels:return {}
        pixels.sort(key=lambda p:(max(p)-min(p))+sum(p)/14.0,reverse=True)
        sample=pixels[:max(8,len(pixels)//8)]
        rgb=tuple(int(sum(p[i] for p in sample)/len(sample)) for i in range(3))
        import colorsys
        rr,gg,bb=[x/255.0 for x in rgb];h,l,s=colorsys.rgb_to_hls(rr,gg,bb)
        s=max(.38,min(.68,s));l=max(.36,min(.52,l));rr,gg,bb=colorsys.hls_to_rgb(h,l,s)
        accent=(int(rr*255),int(gg*255),int(bb*255))
        def mix(a,b,t):return tuple(int(a[i]*(1-t)+b[i]*t) for i in range(3))
        base=(5,10,16);hot=mix(accent,(255,255,255),.62)
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"player_info_glass")
        secure_private_dir(cache)
        key=hashlib.sha1((source_path+str(os.path.getmtime(source_path))+"|info-glass-v2").encode("utf-8","ignore")).hexdigest()[:18]
        specs={
            "main":((1500,690),28,222,3),
            "poster":((280,400),24,75,4),
            "desc":((1080,270),22,220,2),
            "cast":((520,120),20,218,2),
            "director":((260,120),20,218,2),
            "writer":((260,120),20,218,2),
        }
        out={}
        for name,(size,radius,alpha,width) in specs.items():
            target=os.path.join(cache,"%s_%s.png"%(key,name));out[name]=target
            if os.path.isfile(target):continue
            w,hh=size;img=_PILImage.new("RGBA",size,(0,0,0,0));d=_ImageDraw.Draw(img)
            if name=="poster":
                for expand,a,wid in ((8,70,7),(5,125,5),(2,235,3)):
                    d.rounded_rectangle((expand,expand,w-1-expand,hh-1-expand),radius=max(12,radius-expand//2),outline=hot+(a,),width=wid)
            else:
                fill=mix(base,accent,.105 if name=="main" else .13)
                d.rounded_rectangle((2,2,w-3,hh-3),radius=radius,fill=fill+(alpha,),outline=accent+(238,),width=width)
                d.rounded_rectangle((7,7,w-8,hh-8),radius=max(10,radius-6),outline=hot+(105,),width=1)
                # restrained top sheen, no white arcs/rails.
                sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=_ImageDraw.Draw(sheen)
                sd.rounded_rectangle((12,8,w-13,max(18,int(hh*.32))),radius=max(8,radius-8),fill=hot+(20,))
                if _ImageFilter is not None:sheen=sheen.filter(_ImageFilter.GaussianBlur(4))
                img=_PILImage.alpha_composite(img,sheen)
            img.save(target,"PNG")
        return out
    except Exception as exc:
        optional_failure("player.info_glass",exc);return {}

def _cached_poster(item, name, media_type):
    is_live=str(media_type or "").lower() in ("itv","live")
    explicit_key="_player_picon" if is_live else "_player_poster"
    direct = str((item or {}).get(explicit_key) or (item or {}).get("_player_poster") or "").strip()
    if direct and os.path.isfile(direct):
        return direct
    value = _image_value(item)
    if value and os.path.isfile(value):
        return value
    # Prefer the absolute picon URL attached by the browser; its digest is the
    # same persistent HDD cache key used by the live grid.
    if is_live:
        picon_url=str((item or {}).get("_player_picon_url") or "").strip()
        if picon_url:value=picon_url
    if value.startswith(("http://", "https://")):
        digest = hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()
        roots=[IMAGE_CACHE_DIR]
        if is_live:
            roots.insert(0,os.path.join(PERSISTENT_CACHE_ROOT,"live_picons"))
        for root in roots:
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                candidate = os.path.join(root, digest + ext)
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 100:
                    return candidate
    # Live must never show a generated initial/letter.  Until a real picon is
    # available use the neutral live asset; the card remains exactly 220x132.
    if is_live:
        return _asset("placeholder_live_x.png")
    return _letter_logo(name, media_type)


def _meta_text(item, media_type):
    item = item or {}
    values = []
    year = item.get("year") or item.get("release_date") or item.get("created")
    if year:
        values.append(str(year)[:10])
    genre = item.get("genre") or item.get("genres_str") or item.get("category_name")
    if genre:
        values.append(str(genre).replace(";", " / ")[:38])
    duration = item.get("time") or item.get("duration") or item.get("length")
    if duration:
        values.append(str(duration)[:14])
    rating = item.get("rating") or item.get("age") or item.get("age_rating")
    if rating:
        values.append("Rating %s" % str(rating)[:8])
    if not values:
        values.append({"itv": "Live TV", "live": "Live TV", "vod": "Movie", "series": "Series", "episode": "Episode", "catchup": "Catch-up"}.get(media_type, "Ultra Stalker stream"))
    return "   •   ".join(values)[:92]


def _next_meta(item, media_type):
    item = item or {}
    value = item.get("next_time") or item.get("next_start") or item.get("end_time") or item.get("time_to")
    if value:
        return str(value)[:34]
    if media_type in ("vod", "series", "episode", "catchup"):
        return "Resume • Audio • Subtitles"
    return "EPG information when available"


def _available_engines(preferred, url):
    try:
        preferred = int(preferred)
    except Exception:
        preferred = 4097

    available = [1, 4097]
    if os.path.exists("/usr/bin/gstplayer"):
        available.append(5001)
    if os.path.exists("/usr/bin/exteplayer3"):
        available.append(5002)
    # Service type 8193 is not universal. Only advertise it when ServiceApp
    # (or a DreamOS-style service implementation) is actually present.
    serviceapp = any(os.path.exists(path) for path in (
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/Extensions/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp/serviceapp.so",
    ))
    if serviceapp:
        available.append(8193)

    low = str(url or "").lower()
    if ".m3u8" in low or "hls" in low:
        if preferred == 1:
            preferred = 4097
        order = [4097, 5001, 5002, 8193, 1]
    elif low.startswith(("rtsp://", "rtmp://")):
        order = [4097, 5002, 5001, 8193, 1]
    else:
        order = [4097, 5002, 5001, 8193, 1]

    result = []
    for value in [preferred] + order:
        if value in available and value not in result:
            result.append(value)
    return result or [4097]



def _prepare_player_poster_fill(source_path, size=(242,330)):
    """Center-crop a poster to the exact InfoBar card size, with no empty bands."""
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return source_path
    try:
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"player_posters");secure_private_dir(cache)
        stamp="%s:%s:%sx%s"%(source_path,os.path.getmtime(source_path),int(size[0]),int(size[1]))
        key=hashlib.sha1(stamp.encode("utf-8","ignore")).hexdigest()[:18]
        target=os.path.join(cache,"%s.png"%key)
        if os.path.isfile(target) and os.path.getsize(target)>100:
            return target
        temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        with _PILImage.open(source_path) as image:
            image=image.convert("RGB")
            tw,th=int(size[0]),int(size[1])
            scale=max(float(tw)/max(1,image.width),float(th)/max(1,image.height))
            nw=max(tw,int(round(image.width*scale)));nh=max(th,int(round(image.height*scale)))
            res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            image=image.resize((nw,nh),res)
            left=max(0,(nw-tw)//2);top=max(0,(nh-th)//2)
            image=image.crop((left,top,left+tw,top+th)).convert("RGBA")
            if _ImageDraw is not None:
                mask=_PILImage.new("L",(tw,th),0);md=_ImageDraw.Draw(mask)
                md.rounded_rectangle((0,0,tw-1,th-1),radius=18,fill=255)
                image.putalpha(mask)
            image.save(temp,"PNG")
        os.replace(temp,target)
        return target
    except Exception as exc:
        optional_failure("player.poster_fill",exc)
        return source_path

def _prepare_live_picon(source_path, size=(220,132)):
    """Fit and gently illuminate a channel picon without crop or distortion."""
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return source_path
    try:
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"player_picons_lit_v2");secure_private_dir(cache)
        stamp="%s:%s:%sx%s|lit-v2"%(source_path,os.path.getmtime(source_path),int(size[0]),int(size[1]))
        key=hashlib.sha1(stamp.encode("utf-8","ignore")).hexdigest()[:18]
        target=os.path.join(cache,"%s.png"%key)
        if os.path.isfile(target) and os.path.getsize(target)>100:return target
        with _PILImage.open(source_path) as image:
            image=image.convert("RGBA")
            tw,th=int(size[0]),int(size[1]);res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
            image.thumbnail((tw,th),res)

            # Lift the logo itself instead of brightening the whole glass card.
            try:
                rgb=image.convert("RGB")
                rgb=_ImageEnhance.Brightness(rgb).enhance(1.18)
                rgb=_ImageEnhance.Contrast(rgb).enhance(1.08)
                rgb=_ImageEnhance.Color(rgb).enhance(1.12)
                rgb=_ImageEnhance.Sharpness(rgb).enhance(1.08)
                lit=rgb.convert("RGBA");lit.putalpha(image.getchannel("A"));image=lit
            except Exception as exc:optional_failure("player.live_picon_enhance",exc)

            canvas=_PILImage.new("RGBA",(tw,th),(0,0,0,0))
            x=(tw-image.width)//2;y=(th-image.height)//2

            # Soft white halo derived from the picon alpha, kept behind the logo.
            try:
                alpha=image.getchannel("A")
                glow_alpha=alpha.filter(_ImageFilter.GaussianBlur(radius=7))
                glow_alpha=glow_alpha.point(lambda v:min(110,int(v*0.46)))
                glow=_PILImage.new("RGBA",image.size,(210,235,255,0));glow.putalpha(glow_alpha)
                canvas.alpha_composite(glow,(x,y))
            except Exception as exc:optional_failure("player.live_picon_glow",exc)

            canvas.alpha_composite(image,(x,y))
            canvas.save(target,"PNG")
        return target
    except Exception as exc:
        optional_failure("player.live_picon",exc);return source_path

PLAYER_SKIN = """
<screen name="UltraStalkerPlayer" position="0,0" size="1920,1080" backgroundColor="#ff000000" flags="wfNoBorder">
    <widget name="resume_mask" position="0,0" size="1920,1080" font="Regular;1" foregroundColor="#000000" backgroundColor="#000000" transparent="0" zPosition="100" />

    <widget name="adaptive_main" position="60,760" size="1800,200" alphatest="blend" transparent="1" zPosition="5" />

    <!-- VOD/episode poster: separate oversized neon layer fixes Enigma2 clipping. -->
    <widget name="poster_neon_halo" position="30,565" size="310,415" alphatest="blend" transparent="1" zPosition="9" />
    <widget name="logo" position="60,585" size="250,375" alphatest="blend" scale="1" backgroundColor="#07111d" transparent="0" zPosition="8" />
    <widget name="adaptive_poster" position="50,575" size="270,395" alphatest="blend" transparent="1" zPosition="7" />
    <!-- Live-only 220x132 picon card, centered and aspect-safe. -->
    <!-- Adaptive frame stays behind the actual picon. The picon is the lit foreground subject. -->
    <widget name="adaptive_live_picon" position="97,796" size="240,152" alphatest="blend" transparent="1" zPosition="9" />
    <widget name="live_picon" position="107,806" size="220,132" alphatest="blend" scale="1" transparent="1" zPosition="12" />

    <widget name="channel" position="350,770" size="1010,46" font="Regular;29" halign="left" valign="center" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6" />
    <widget name="category" position="1480,776" size="120,32" font="Regular;18" foregroundColor="#ffffff" backgroundColor="#132633" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="quality" position="1615,776" size="110,32" font="Regular;18" foregroundColor="#d8e8ef" backgroundColor="#132633" halign="center" valign="center" transparent="1" zPosition="6" />

    <widget name="skip_hint" position="370,818" size="265,28" font="Regular;16" foregroundColor="#7fe8ff" backgroundColor="#000000" transparent="1" zPosition="8" />
    <widget name="now" position="650,818" size="525,28" font="Regular;18" foregroundColor="#d4dde3" backgroundColor="#000000" halign="center" transparent="1" zPosition="6" />
    <widget name="connection" position="1190,818" size="530,28" font="Regular;16" foregroundColor="#e0eef2" backgroundColor="#000000" halign="right" transparent="1" zPosition="7" />

    <widget source="session.CurrentService" render="Progress" position="370,858" size="1350,8" borderWidth="0" foregroundColor="#203542" backgroundColor="#203542" zPosition="5">
        <convert type="ServicePosition">Position</convert>
    </widget>
    <widget name="adaptive_progress_track" position="370,858" size="1350,8" alphatest="blend" transparent="1" zPosition="6" />
    <widget name="watched_progress_glow" position="0,0" size="1,1" borderWidth="0" foregroundColor="#000000" backgroundColor="#000000" transparent="1" zPosition="1" />
    <widget name="watched_progress" position="0,0" size="1,1" borderWidth="0" foregroundColor="#000000" backgroundColor="#000000" transparent="1" zPosition="1" />
    <widget name="progress_neon" position="370,845" size="1350,34" alphatest="blend" transparent="1" zPosition="9" />
    <widget name="progress_crystal" position="0,0" size="1,1" pixmap="%(blank)s" alphatest="blend" zPosition="1" />
    <widget source="session.CurrentService" render="Label" position="370,869" size="170,24" font="Regular;16" foregroundColor="#d8e0e5" backgroundColor="#000000" transparent="1" zPosition="6">
        <convert type="ServicePosition">Position,ShowHours</convert>
    </widget>
    <widget source="session.CurrentService" render="Label" position="1550,869" size="170,24" font="Regular;16" foregroundColor="#d8e0e5" backgroundColor="#000000" halign="right" transparent="1" zPosition="6">
        <convert type="ServicePosition">Length,ShowHours</convert>
    </widget>

    <!-- Adaptive bordered technical cards. -->
    <widget name="chip_video_quality" position="370,902" size="150,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="video_quality" position="370,902" size="150,42" font="Regular;17" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="chip_video_codec" position="530,902" size="130,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="video_codec" position="530,902" size="130,42" font="Regular;17" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="chip_audio_codec" position="670,902" size="120,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="audio_codec" position="670,902" size="120,42" font="Regular;17" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="chip_bitrate" position="800,902" size="190,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="bitrate" position="800,902" size="190,42" font="Regular;16" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />

    <widget name="chip_stream" position="1040,902" size="150,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="extension" position="1040,902" size="150,42" font="Regular;17" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="chip_audio" position="1200,902" size="150,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="audio_tag" position="1200,902" size="150,42" font="Regular;17" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="chip_subtitles" position="1360,902" size="170,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="subtitle_tag" position="1360,902" size="170,42" font="Regular;17" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="chip_engine" position="1540,902" size="210,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="engine" position="1540,902" size="210,42" font="Regular;16" foregroundColor="#d5e2e8" halign="center" valign="center" transparent="1" zPosition="6" />

    <!-- Runtime compatibility widgets. -->
    <widget name="brand" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="section" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="state" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="statusicon" position="0,0" size="1,1" alphatest="blend" pixmaps="%(play)s,%(pause)s,%(stop)s,%(ff)s,%(rw)s,%(slow)s,%(blank)s" />
    <widget name="speed" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="format_tag" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="transport_hint" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="media_meta" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="next_header" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="next" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="next_meta" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="description" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="online_subtitle" position="170,805" size="1580,150" font="Regular;38" halign="center" valign="bottom" foregroundColor="#ffffff" shadowColor="#000000" shadowOffset="3,3" transparent="1" zPosition="4" />

    <!-- Original slim player key bar: compact remote dots + labels, as in the approved reference. -->
    <widget name="adaptive_keybar" position="160,978" size="1600,58" alphatest="blend" transparent="1" zPosition="5" />
    <widget source="global.CurrentTime" render="Label" position="185,982" size="125,33" font="Regular;25" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6">
        <convert type="ClockToText">Format:%%H:%%M</convert>
    </widget>
    <widget source="global.CurrentTime" render="Label" position="185,1010" size="205,19" font="Regular;14" foregroundColor="#d7dee2" backgroundColor="#000000" transparent="1" zPosition="6">
        <convert type="ClockToText">Format:%%a %%d %%b %%Y</convert>
    </widget>
    <ePixmap pixmap="%(redkey)s" position="420,990" size="32,32" alphatest="blend" zPosition="6" />
    <widget name="key_red" position="460,991" size="145,30" font="Regular;20" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6" />
    <ePixmap pixmap="%(greenkey)s" position="645,990" size="32,32" alphatest="blend" zPosition="6" />
    <widget name="key_green" position="685,991" size="190,30" font="Regular;20" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6" />
    <ePixmap pixmap="%(yellowkey)s" position="895,990" size="32,32" alphatest="blend" zPosition="6" />
    <widget name="key_yellow" position="935,991" size="220,30" font="Regular;20" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6" />
    <ePixmap pixmap="%(bluekey)s" position="1185,990" size="32,32" alphatest="blend" zPosition="6" />
    <widget name="key_blue" position="1225,991" size="205,30" font="Regular;20" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6" />
    <widget name="hint" position="0,0" size="1,1" font="Regular;1" transparent="1" zPosition="1"/>
</screen>
""" % {
    "mainpanel": _player_asset("us90_player_main_clean.png"),
    "posterframe": _player_asset("us8_player_poster_9_6_4_night.png"),
    "keybar": _player_asset("us8_player_keys_9_6_4_opaque_night.png"),
    "cinplay": _player_asset("cinematic_play.png"),
    "redkey": _player_asset("key_red_dot.png"),
    "greenkey": _player_asset("key_green_dot.png"),
    "yellowkey": _player_asset("key_yellow_dot.png"),
    "bluekey": _player_asset("key_blue_dot.png"),
    "actionred": _asset("us15_action_red.png"),
    "actiongreen": _asset("us15_action_green.png"),
    "actionyellow": _asset("us15_action_yellow.png"),
    "actionblue": _asset("us15_action_blue.png"),
    "play": _player_asset("state_play.png"),
    "pause": _player_asset("state_pause.png"),
    "stop": _player_asset("state_stop.png"),
    "ff": _player_asset("state_ff.png"),
    "rw": _player_asset("state_rw.png"),
    "slow": _player_asset("state_slow.png"),
    "blank": _player_asset("state_blank.png"),
    "crystal": _player_asset("progress_crystal.png"),
}



ONLINE_SUBTITLE_SKIN = """
<screen name="UltraStalkerOnlineSubtitleOverlay" position="0,0" size="1920,1080" flags="wfNoBorder">
    <widget name="text" position="170,805" size="1580,150" font="Regular;38" halign="center" valign="bottom"
        foregroundColor="#ffffff" shadowColor="#000000" shadowOffset="3,3" transparent="1" zPosition="1"/>
</screen>
"""

class OnlineSubtitleOverlay(Screen):
    skin=ONLINE_SUBTITLE_SKIN
    def __init__(self,session):
        Screen.__init__(self,session)
        self["text"]=Label("")
    def set_text(self,value):
        try:self["text"].setText(str(value or ""))
        except Exception:pass


SUBTITLE_STYLE_COLORS = (
    ("White", "#FFFFFF"), ("Cream", "#FFF1C1"), ("Yellow", "#FFD84D"),
    ("Gold", "#FFB300"), ("Orange", "#FF9A4D"), ("Lime", "#D9FF57"),
    ("Green", "#69F0AE"), ("Mint", "#64FFD8"), ("Cyan", "#5DEBFF"),
    ("Sky Blue", "#63B8FF"), ("Blue", "#7D93FF"), ("Lavender", "#C3A6FF"),
    ("Purple", "#DA9BFF"), ("Pink", "#FF95D8"), ("Coral", "#FF958C"),
)
SUBTITLE_POSITION_PRESETS = (
    ("Very High", 180), ("High", 120), ("Upper", 60), ("Default", 0),
    ("Lower", -60), ("Low", -120), ("Very Low", -180),
)
def _subtitle_color_name(value):
    value=str(value or "#FFFFFF").upper()
    for name,hex_value in SUBTITLE_STYLE_COLORS:
        if hex_value.upper()==value:return name
    return "White"
def _subtitle_position_name(value):
    try:value=int(value or 0)
    except Exception:value=0
    for name,offset in SUBTITLE_POSITION_PRESETS:
        if int(offset)==value:return name
    return ("%+d px"%value) if value else "Default"


def _subtitle_glass_assets(source_path, width=1120, row_width=1010, row_height=72):
    """Downloads-style adaptive glass for subtitle picker screens."""
    if _PILImage is None or _ImageDraw is None:
        return {}
    try:
        source=str(source_path or "")
        if source and os.path.isfile(source):
            with _PILImage.open(source) as im:
                im=im.convert("RGB"); im.thumbnail((72,96))
                pixels=[p for p in im.getdata() if 24 < sum(p)/3.0 < 238]
            if pixels:
                pixels.sort(key=lambda p:(max(p)-min(p))+sum(p)/16.0, reverse=True)
                sample=pixels[:max(8,len(pixels)//8)]
                accent=tuple(int(sum(p[i] for p in sample)/len(sample)) for i in range(3))
            else:
                accent=(78,146,196)
        else:
            accent=(78,146,196)

        import colorsys
        rr,gg,bb=[v/255.0 for v in accent]
        h,l,s=colorsys.rgb_to_hls(rr,gg,bb)
        s=max(.38,min(.70,s)); l=max(.34,min(.50,l))
        rr,gg,bb=colorsys.hls_to_rgb(h,l,s)
        accent=(int(rr*255),int(gg*255),int(bb*255))
        hot=tuple(min(255,int(v*.30+255*.70)) for v in accent)
        base=(6,10,15)

        stamp="0"
        try: stamp=str(os.path.getmtime(source)) if source else "0"
        except Exception: pass
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"subtitle_glass")
        secure_private_dir(cache)
        key=hashlib.sha1((source+"|"+stamp+"|subtitle-glass-v1|%s|%s|%s"%(width,row_width,row_height)).encode("utf-8","ignore")).hexdigest()[:20]

        def mix(a,b,t):
            return tuple(int(a[i]*(1.0-t)+b[i]*t) for i in range(3))

        specs={
            "frame":(1280,800,30,238),
            "inner":(1180,590,26,238),
            "row":(row_width,row_height,22,244),
            "row_selected":(row_width,row_height,22,244),
        }
        out={}
        for name,(w,hh,radius,alpha) in specs.items():
            target=os.path.join(cache,"%s_%s.png"%(key,name))
            out[name]=target
            if os.path.isfile(target):
                continue
            img=_PILImage.new("RGBA",(w,hh),(0,0,0,0))
            d=_ImageDraw.Draw(img)
            kind=("selected" if name=="row_selected" else ("row" if name=="row" else ("panel" if name=="frame" else "inner")))
            glow=_PILImage.new("RGBA",(w,hh),(0,0,0,0))
            gd=_ImageDraw.Draw(glow)
            ga=118 if kind=="selected" else (58 if kind in ("panel","inner") else 44)
            gw=4 if kind=="selected" else 2
            gd.rounded_rectangle((5,5,w-6,hh-6),radius=radius,outline=accent+(ga,),width=gw)
            if _ImageFilter is not None:
                glow=glow.filter(_ImageFilter.GaussianBlur(7 if kind in ("panel","selected") else 4))
            img=_PILImage.alpha_composite(img,glow)
            d=_ImageDraw.Draw(img)
            mix_value=.085 if kind=="panel" else (.105 if kind=="inner" else (.12 if kind=="row" else .20))
            fill=mix(base,accent,mix_value)
            fill_alpha=238 if kind in ("panel","inner") else 244
            border=236 if kind=="selected" else (150 if kind=="row" else 190)
            bw=2 if kind=="selected" else 1
            d.rounded_rectangle((2,2,w-3,hh-3),radius=radius,fill=fill+(fill_alpha,),outline=accent+(border,),width=bw)
            inner=mix(hot,(255,255,255),.34)
            d.rounded_rectangle((7,7,w-8,hh-8),radius=max(8,radius-6),outline=inner+((86 if kind=="selected" else 50),),width=1)
            sheen=_PILImage.new("RGBA",(w,hh),(0,0,0,0));sd=_ImageDraw.Draw(sheen)
            hi=mix(accent,(255,255,255),.46)
            top=max(24,int(hh*.45))
            for yy in range(8,top,6):
                t=(yy-8.0)/max(1.0,top-8.0)
                a=max(0,int((24 if kind in ("row","selected") else 14)*(1.0-t)**1.5))
                sd.rounded_rectangle((12,yy,w-13,min(hh-12,yy+8)),radius=max(6,radius-8),fill=hi+(a,))
            if _ImageFilter is not None:sheen=sheen.filter(_ImageFilter.GaussianBlur(4))
            img=_PILImage.alpha_composite(img,sheen)
            img.save(target,"PNG")
        return out
    except Exception as exc:
        optional_failure("player.subtitle_glass_assets",exc)
        return {}


class SubtitleGlassList(MenuList):
    def __init__(self, choices=None, width=1010, item_height=72):
        self.row_width=int(width)
        self.row_height=int(item_height)
        self._rows=list(choices or [])
        self._row_asset=""
        self._row_selected_asset=""
        self._rendering=False
        MenuList.__init__(self,[],enableWrapAround=True,content=eListboxPythonMultiContent)
        # Per-row adaptive fonts: each title selects the largest size that fits.
        for idx,size in enumerate((25,23,21,19,17)):
            self.l.setFont(idx,gFont("Regular",size))
        self.l.setItemHeight(self.row_height)

    def _font_index(self,text):
        n=len(str(text or ""))
        if n<=30:return 0
        if n<=44:return 1
        if n<=62:return 2
        if n<=82:return 3
        return 4

    def set_assets(self,row_asset,row_selected_asset):
        self._row_asset=str(row_asset or "")
        self._row_selected_asset=str(row_selected_asset or "")

    def set_choices(self,choices,selected=0):
        self._rows=list(choices or [])
        self._render(selected)

    def _render(self,selected=None):
        if self._rendering:return
        self._rendering=True
        if selected is None:
            selected=self.getSelectedIndex() if self._rows else 0
        rows=[]
        for idx,choice in enumerate(self._rows):
            label=str(choice[0] if isinstance(choice,(tuple,list)) and choice else choice or "")
            payload=choice
            row=[payload]
            frame_path=self._row_selected_asset if idx==selected else self._row_asset
            try:
                png=loadPNG(frame_path) if frame_path and os.path.isfile(frame_path) else None
            except Exception:
                png=None
            if png is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(0,5),size=(self.row_width,66),png=png))
            font_idx=self._font_index(label)
            row.append(MultiContentEntryText(
                pos=(28,7),size=(self.row_width-56,58),
                font=font_idx,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=label
            ))
            rows.append(row)
        try:
            self.setList(rows)
            if rows:
                try:self.moveToIndex(max(0,min(int(selected),len(rows)-1)))
                except Exception:pass
        finally:
            self._rendering=False

    def selection_changed(self):
        if not self._rendering:self._render(self.getSelectedIndex())

    def selected_choice(self):
        idx=self.getSelectedIndex()
        return self._rows[idx] if 0<=idx<len(self._rows) else None


SUBTITLE_GLASS_SKIN = """
<screen name="SubtitleGlassChoiceScreen" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
    <widget name="frame" position="320,140" size="1280,800" alphatest="blend" transparent="1" zPosition="1"/>
    <widget name="inner" position="370,250" size="1180,590" alphatest="blend" transparent="1" zPosition="2"/>
    <widget name="title" position="390,185" size="1120,54" font="Regular;38" foregroundColor="#ffffff" transparent="1" zPosition="4"/>
    <widget name="list" position="415,268" size="1090,552" scrollbarMode="showNever" transparent="1" zPosition="5"/>
    <widget name="hint" position="390,860" size="1120,34" font="Regular;18" halign="center" foregroundColor="#a9c5d3" transparent="1" zPosition="5"/>
</screen>
"""

class SubtitleGlassChoiceScreen(Screen):
    skin=SUBTITLE_GLASS_SKIN
    def __init__(self,session,title,choices,source_path="",selected=0):
        Screen.__init__(self,session)
        self._choices=list(choices or [])
        self._source_path=str(source_path or "")
        self._selected=max(0,int(selected or 0))
        self["frame"]=Pixmap();self["inner"]=Pixmap()
        self["title"]=Label(str(title or "Subtitles"))
        self["hint"]=Label("OK  Select   •   BACK  Close   •   UP / DOWN  Navigate")
        self["list"]=SubtitleGlassList(self._choices,width=1090,item_height=76)
        self["actions"]=ActionMap(
            ["OkCancelActions","DirectionActions"],
            {"ok":self.accept,"cancel":self.cancel_close,"up":self.up,"down":self.down,
             "left":self.page_up,"right":self.page_down},
            -2,
        )
        self["list"].onSelectionChanged.append(self._selection_changed)
        self.onLayoutFinish.append(self._layout_ready)

    def _layout_ready(self):
        assets=_subtitle_glass_assets(self._source_path,1280,1090,66)
        for name,key in (("frame","frame"),("inner","inner")):
            try:
                path=assets.get(key)
                if path and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
            except Exception as exc:optional_failure("player.subtitle_glass_panel",exc)
        self["list"].set_assets(assets.get("row"),assets.get("row_selected"))
        try:
            if self["list"].instance is not None:self["list"].instance.setSelectionEnable(0)
        except Exception:pass
        self["list"].set_choices(self._choices,self._selected)

    def _selection_changed(self):
        try:self["list"].selection_changed()
        except Exception as exc:optional_failure("player.subtitle_glass_selection",exc)

    def up(self):
        try:self["list"].up()
        except Exception:pass
    def down(self):
        try:self["list"].down()
        except Exception:pass
    def page_up(self):
        try:self["list"].pageUp()
        except Exception:pass
    def page_down(self):
        try:self["list"].pageDown()
        except Exception:pass

    def accept(self):
        choice=self["list"].selected_choice()
        if choice is not None:self.close(choice)

    def cancel_close(self):
        self.close(None)

PLAYER_INFO_SKIN = """
<screen name="PlayerInformationOverlay" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
 <ePixmap pixmap="%(scrim)s" position="0,0" size="1920,1080" alphatest="blend" zPosition="1"/>
 <widget name="adaptive_main" position="210,175" size="1500,690" alphatest="blend" transparent="1" zPosition="2"/>
 <widget name="adaptive_poster" position="260,225" size="280,400" alphatest="blend" transparent="1" zPosition="3"/>
 <widget name="poster" position="275,240" size="250,370" alphatest="blend" scale="1" zPosition="4"/>
 <widget name="title" position="575,220" size="1035,60" font="Regular;38" foregroundColor="#ffffff" transparent="1" zPosition="5"/>
 <widget name="meta" position="575,286" size="1035,34" font="Regular;19" foregroundColor="#9bd9f5" transparent="1" zPosition="5"/>

 <widget name="adaptive_desc" position="555,335" size="1080,270" alphatest="blend" transparent="1" zPosition="3"/>
 <widget name="description" position="585,365" size="1020,205" font="Regular;25" foregroundColor="#f7f9fb" transparent="1" valign="top" zPosition="5"/>
 <widget name="page" position="1500,570" size="100,25" font="Regular;16" halign="right" foregroundColor="#9bd9f5" transparent="1" zPosition="5"/>

 <widget name="adaptive_cast" position="555,630" size="520,120" alphatest="blend" transparent="1" zPosition="3"/>
 <widget name="cast_label" position="580,648" size="120,28" font="Regular;18" foregroundColor="#9bd9f5" transparent="1" zPosition="5"/>
 <widget name="cast" position="580,678" size="470,55" font="Regular;19" foregroundColor="#ffffff" transparent="1" zPosition="5"/>

 <widget name="adaptive_director" position="1090,630" size="260,120" alphatest="blend" transparent="1" zPosition="3"/>
 <widget name="director_label" position="1110,648" size="220,28" font="Regular;18" halign="center" foregroundColor="#9bd9f5" transparent="1" zPosition="5"/>
 <widget name="director" position="1110,680" size="220,50" font="Regular;18" halign="center" foregroundColor="#ffffff" transparent="1" zPosition="5"/>

 <widget name="adaptive_writer" position="1365,630" size="260,120" alphatest="blend" transparent="1" zPosition="3"/>
 <widget name="writer_label" position="1385,648" size="220,28" font="Regular;18" halign="center" foregroundColor="#9bd9f5" transparent="1" zPosition="5"/>
 <widget name="writer" position="1385,680" size="220,50" font="Regular;18" halign="center" foregroundColor="#ffffff" transparent="1" zPosition="5"/>

 <widget name="hint" position="575,790" size="1035,34" font="Regular;17" halign="center" foregroundColor="#a9c5d3" transparent="1" zPosition="5"/>
</screen>
""" % {"scrim":_asset("us20_player_info_scrim.png")}

class PlayerInformationOverlay(Screen):
    skin=PLAYER_INFO_SKIN
    def __init__(self,session,name,item,media_type,meta,poster_path=None):
        Screen.__init__(self,session);self.item=item if isinstance(item,dict) else {}
        self["poster"]=Pixmap();self["adaptive_main"]=Pixmap();self["adaptive_poster"]=Pixmap();self["adaptive_desc"]=Pixmap();self["adaptive_cast"]=Pixmap();self["adaptive_director"]=Pixmap();self["adaptive_writer"]=Pixmap()
        self["title"]=Label(str(name or "Media information")[:105]);self["meta"]=Label(str(meta or "")[:160])
        desc=self.item.get("description") or self.item.get("descr") or self.item.get("plot") or self.item.get("overview") or "No additional description is available."
        self._pages=self._make_pages(desc);self._page=0;self["description"]=Label(self._pages[0]);self["page"]=Label("")
        cast=self.item.get("actors") or self.item.get("cast") or self.item.get("actor") or "";director=self.item.get("director") or self.item.get("directors") or "";writer=self.item.get("writer") or self.item.get("writers") or self.item.get("creator") or ""
        self["cast_label"]=Label("Cast" if cast else "");self["cast"]=Label(str(cast)[:135]);self["director_label"]=Label("Director" if director else "");self["director"]=Label(str(director)[:68]);self["writer_label"]=Label("Writer" if writer else "");self["writer"]=Label(str(writer)[:68]);self["hint"]=Label("UP / DOWN  Read description     •     YELLOW / OK / BACK  Close")
        self._poster_path=poster_path;self.onLayoutFinish.append(self._layout_ready);self._sync_page()
        self["actions"]=ActionMap(["OkCancelActions","ColorActions","DirectionActions"],{"cancel":self.close,"ok":self.close,"yellow":self.close,"up":self.page_up,"down":self.page_down},-1000)
    @staticmethod
    def _make_pages(text,limit=470):
        words=str(text or "").replace("\\n"," ").split();pages=[];buf=[];count=0
        for word in words:
            extra=len(word)+(1 if buf else 0)
            if buf and count+extra>limit:pages.append(" ".join(buf));buf=[word];count=len(word)
            else:buf.append(word);count+=extra
        if buf:pages.append(" ".join(buf))
        return pages or [str(text or "")]
    def _sync_page(self):
        try:self["description"].setText(self._pages[self._page]);self["page"].setText(("%d / %d"%(self._page+1,len(self._pages))) if len(self._pages)>1 else "")
        except Exception as exc:optional_failure("player",exc)
    def _layout_ready(self):
        try:
            if self._poster_path and os.path.isfile(self._poster_path):
                self["poster"].instance.setPixmapFromFile(self._poster_path);self["poster"].show()
                frames=_adaptive_info_frames(self._poster_path)
                for widget_name,key in (("adaptive_main","main"),("adaptive_poster","poster"),("adaptive_desc","desc"),("adaptive_cast","cast"),("adaptive_director","director"),("adaptive_writer","writer")):
                    path=frames.get(key)
                    widget=self[widget_name]
                    if path and os.path.isfile(path) and widget.instance is not None:
                        widget.instance.setPixmapFromFile(path);widget.show()
        except Exception as exc:optional_failure("player.info_layout",exc)
    def page_up(self):
        if self._pages:self._page=(self._page-1)%len(self._pages);self._sync_page()
    def page_down(self):
        if self._pages:self._page=(self._page+1)%len(self._pages);self._sync_page()



LIVE_ZAP_SKIN = """
<screen name="UltraStalkerLiveZapList" position="0,0" size="590,1080" backgroundColor="#02070b" flags="wfNoBorder">
    <widget name="panel" position="0,0" size="590,1080" zPosition="-6" alphatest="blend" />
    <widget name="folder_bg" position="204,18" size="358,46" zPosition="3" alphatest="blend" />
    <widget name="channel_bg" position="204,72" size="358,38" zPosition="3" alphatest="blend" />
    <widget name="picon" position="28,18" size="166,100" zPosition="7" alphatest="blend" />
    <widget name="title" position="218,20" size="330,42" font="Regular;24" foregroundColor="#ffffff" transparent="1" zPosition="8" valign="center" />
    <widget name="subtitle" position="218,73" size="330,36" font="Regular;19" foregroundColor="#e8f6fb" transparent="1" zPosition="8" valign="center" />
    <widget name="bg0" position="18,143" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num0" position="24,143" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row0" position="102,143" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg1" position="18,199" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num1" position="24,199" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row1" position="102,199" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg2" position="18,255" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num2" position="24,255" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row2" position="102,255" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg3" position="18,311" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num3" position="24,311" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row3" position="102,311" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg4" position="18,367" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num4" position="24,367" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row4" position="102,367" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg5" position="18,423" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num5" position="24,423" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row5" position="102,423" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg6" position="18,479" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num6" position="24,479" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row6" position="102,479" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg7" position="18,535" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num7" position="24,535" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row7" position="102,535" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg8" position="18,591" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num8" position="24,591" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row8" position="102,591" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg9" position="18,647" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num9" position="24,647" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row9" position="102,647" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg10" position="18,703" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num10" position="24,703" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row10" position="102,703" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg11" position="18,759" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num11" position="24,759" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row11" position="102,759" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg12" position="18,815" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num12" position="24,815" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row12" position="102,815" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg13" position="18,871" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num13" position="24,871" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row13" position="102,871" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="bg14" position="18,927" size="554,52" zPosition="2" alphatest="blend" />
    <widget name="num14" position="24,927" size="44,52" font="Regular;18" halign="center" valign="center" foregroundColor="#9fc7d9" transparent="1" zPosition="7" />
    <widget name="row14" position="102,927" size="446,52" font="Regular;22" halign="left" valign="center" foregroundColor="#eef8fc" transparent="1" zPosition="7" />
    <widget name="footer_count_bg" position="18,1000" size="118,42" zPosition="3" alphatest="blend" />
    <widget name="footer_page_bg" position="142,1000" size="126,42" zPosition="3" alphatest="blend" />
    <widget name="footer_play_bg" position="274,1000" size="126,42" zPosition="3" alphatest="blend" />
    <widget name="footer_close_bg" position="406,1000" size="166,42" zPosition="3" alphatest="blend" />
    <widget name="counter" position="22,1001" size="110,40" font="Regular;15" halign="center" valign="center" foregroundColor="#eaf8ff" transparent="1" zPosition="8" />
    <widget name="footer_page" position="146,1001" size="118,40" font="Regular;15" halign="center" valign="center" foregroundColor="#eaf8ff" transparent="1" zPosition="8" />
    <widget name="footer_play" position="278,1001" size="118,40" font="Regular;15" halign="center" valign="center" foregroundColor="#eaf8ff" transparent="1" zPosition="8" />
    <widget name="footer_close" position="410,1001" size="158,40" font="Regular;15" halign="center" valign="center" foregroundColor="#eaf8ff" transparent="1" zPosition="8" />
</screen>
"""

def _clean_live_name(value):
    text=str(value or "").strip()
    try:text=unicodedata.normalize("NFKC",text)
    except Exception as exc:optional_failure("player.optional_guard",exc)
    text=re.sub(r"^\s*#+\s*","",text);text=re.sub(r"\s*#+\s*$","",text)
    text=re.sub(r"^\s*[A-Z]{2,3}\s*[:|•/_-]\s*","",text)
    text=re.sub(r"(?i)\(\s*(?:EVENT\s*ONLY|BACKUP|TEST|VIP)\s*\)"," ",text)
    tokens=("3840P","2160P","1440P","1080P","720P","576P","480P","FULL HD","FULLHD","FHD","ULTRA HD","ULTRAHD","UHD","HDR10+","HDR10","HDR","DOLBY VISION","DOLBYVISION","HEVC","H.265","H265","H.264","H264","60FPS","50FPS","30FPS","8K","4K","RAW")
    for _ in range(5):
        old=text
        for token in sorted(tokens,key=len,reverse=True):
            text=re.sub(r"(?i)(?<![A-Z0-9])"+re.escape(token)+r"(?![A-Z0-9])"," ",text)
        text=re.sub(r"\s*[\[\]{}<>|•·▶►◀◄]+\s*"," ",text)
        text=re.sub(r"\s*[:;,_/\\]+\s*"," ",text)
        text=re.sub(r"\s{2,}"," ",text).strip(" -:|•·_")
        if text==old:break
    return text or str(value or "Channel").strip()

def _zap_palette(path):
    if _PILImage is None or not path or not os.path.isfile(str(path)):
        return (52,148,202),(104,210,255)
    try:
        with _PILImage.open(path) as im:
            im=im.convert("RGB"); im.thumbnail((80,80))
            pixels=list(im.getdata())
        useful=[c for c in pixels if max(c)>42 and (max(c)-min(c))>12]
        if not useful:
            return (52,148,202),(104,210,255)
        useful.sort(key=lambda c:(max(c)-min(c))*1.6+sum(c)/3.0,reverse=True)
        top=useful[:max(8,len(useful)//5)]
        accent=tuple(int(sum(c[i] for c in top)/len(top)) for i in range(3))
        hot=tuple(min(255,int(v*.68+255*.32)) for v in accent)
        return accent,hot
    except Exception:
        return (52,148,202),(104,210,255)

def _zap_assets(picon_path):
    cache=os.path.join(PERSISTENT_GENERATED_DIR,"live_zap_seriesglass_1041")
    try:secure_private_dir(cache)
    except Exception:return {}
    try:
        stamp=str(picon_path or "neutral")
        if picon_path and os.path.isfile(str(picon_path)):
            stamp+="|%s"%os.path.getmtime(str(picon_path))
        key=hashlib.sha1((stamp+"|series-glass-1041").encode("utf-8","ignore")).hexdigest()[:16]
        panel=os.path.join(cache,key+"_panel.png")
        folder=os.path.join(cache,key+"_folder.png")
        channel=os.path.join(cache,key+"_channel.png")
        normal=os.path.join(cache,key+"_row.png")
        selected=os.path.join(cache,key+"_selected.png")
        footer_count=os.path.join(cache,key+"_footer_count.png")
        footer_page=os.path.join(cache,key+"_footer_page.png")
        footer_play=os.path.join(cache,key+"_footer_play.png")
        footer_close=os.path.join(cache,key+"_footer_close.png")
        if all(os.path.isfile(x) and os.path.getsize(x)>150 for x in (panel,folder,channel,normal,selected,footer_count,footer_page,footer_play,footer_close)):
            return {"panel":panel,"folder":folder,"channel":channel,"normal":normal,"selected":selected,
                    "footer_count":footer_count,"footer_page":footer_page,"footer_play":footer_play,"footer_close":footer_close}
        if _PILImage is None or _ImageDraw is None:return {}
        accent,_hot=_zap_palette(picon_path)
        base=(4,12,18)
        def mix(a,b,t):
            return tuple(int(a[i]*(1.0-t)+b[i]*t) for i in range(3))

        # Full drawer: same quiet translucent adaptive glass as details panels.
        p=_PILImage.new("RGBA",(590,1080),(0,0,0,0));pd=_ImageDraw.Draw(p)
        pfill=mix((3,10,16),accent,0.08)
        pd.rounded_rectangle((2,2,587,1077),radius=28,fill=pfill+(76,),outline=accent+(158,),width=1)
        inner=mix(accent,(255,255,255),0.40)
        pd.rounded_rectangle((8,8,581,1071),radius=22,outline=inner+(22,),width=1)
        p.save(panel,"PNG")

        # Two compact adaptive glass boxes: folder identity and selected channel.
        for target,size,radius,fill_mix,alpha,border in (
            (folder,(358,46),16,0.14,152,224),
            (channel,(358,38),14,0.18,166,238),
        ):
            w,h=size
            g=_PILImage.new("RGBA",size,(0,0,0,0));gd=_ImageDraw.Draw(g)
            fill=mix((4,12,18),accent,fill_mix)
            gd.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=fill+(alpha,),outline=accent+(border,),width=2)
            gd.rounded_rectangle((7,7,w-8,h-8),radius=max(7,radius-6),outline=inner+(78,),width=1)
            sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=_ImageDraw.Draw(sheen)
            sd.rounded_rectangle((12,6,w-13,max(10,int(h*.48))),radius=max(6,radius-8),fill=inner+(18,))
            if _ImageFilter is not None:
                try:sheen=sheen.filter(_ImageFilter.GaussianBlur(radius=3))
                except Exception as exc:optional_failure("player.optional_guard",exc)
            g=_PILImage.alpha_composite(g,sheen);g.save(target,"PNG")

        # Footer controls are the *same material* as the folder/channel glass:
        # identical tint strength, alpha, bright adaptive rim and top sheen.
        for target,size,radius in (
            (footer_count,(118,42),14),(footer_page,(126,42),14),
            (footer_play,(126,42),14),(footer_close,(166,42),14),
        ):
            w,h=size
            g=_PILImage.new("RGBA",size,(0,0,0,0));gd=_ImageDraw.Draw(g)
            fill=mix((4,12,18),accent,0.14)
            gd.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=fill+(152,),outline=accent+(224,),width=2)
            gd.rounded_rectangle((7,7,w-8,h-8),radius=max(7,radius-6),outline=inner+(78,),width=1)
            sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=_ImageDraw.Draw(sheen)
            sd.rounded_rectangle((12,6,w-13,max(10,int(h*.48))),radius=max(6,radius-8),fill=inner+(18,))
            if _ImageFilter is not None:
                try:sheen=sheen.filter(_ImageFilter.GaussianBlur(radius=3))
                except Exception as exc:optional_failure("player.optional_guard",exc)
            g=_PILImage.alpha_composite(g,sheen)
            g.save(target,"PNG")

        for target,is_selected in ((normal,False),(selected,True)):
            r=_PILImage.new("RGBA",(554,52),(0,0,0,0));rd=_ImageDraw.Draw(r)
            if is_selected:
                fill=mix((4,13,18),accent,0.18);alpha=126;border=232;width=2
            else:
                fill=mix((4,12,18),accent,0.10);alpha=108;border=130;width=1
            rd.rounded_rectangle((2,2,551,49),radius=17,fill=fill+(alpha,),outline=accent+(border,),width=width)
            # Same series-row rule: single floating card, no inner duplicate rim.
            sheen=_PILImage.new("RGBA",(554,52),(0,0,0,0));sd=_ImageDraw.Draw(sheen)
            sd.rounded_rectangle((12,7,541,25),radius=9,fill=inner+(15 if is_selected else 11,))
            if _ImageFilter is not None:
                try:sheen=sheen.filter(_ImageFilter.GaussianBlur(radius=3))
                except Exception as exc:optional_failure("player.optional_guard",exc)
            r=_PILImage.alpha_composite(r,sheen)
            r.save(target,"PNG")
        return {"panel":panel,"folder":folder,"channel":channel,"normal":normal,"selected":selected,
                "footer_count":footer_count,"footer_page":footer_page,"footer_play":footer_play,"footer_close":footer_close}
    except Exception as exc:
        optional_failure("player.zap_assets",exc)
        return {}

def _fit_zap_picon(path):
    if _PILImage is None or not path or not os.path.isfile(str(path)):
        return path
    try:
        cache=os.path.join(PERSISTENT_GENERATED_DIR,"live_zap_glass")
        secure_private_dir(cache)
        stamp="%s|%s|162x96"%(path,os.path.getmtime(path))
        target=os.path.join(cache,hashlib.sha1(stamp.encode("utf-8","ignore")).hexdigest()[:16]+"_picon.png")
        if os.path.isfile(target) and os.path.getsize(target)>100:return target
        with _PILImage.open(path) as im:
            im=im.convert("RGBA")
            im.thumbnail((154,88),getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1))
            canvas=_PILImage.new("RGBA",(162,96),(0,0,0,0))
            x=(162-im.width)//2;y=(96-im.height)//2
            canvas.alpha_composite(im,(x,y))
            canvas.save(target,"PNG")
        return target
    except Exception:
        return path

class UltraStalkerLiveZapList(Screen):
    skin=LIVE_ZAP_SKIN
    VISIBLE=15

    def __init__(self,session,channels,current_index=0,title="Live Channels",picon_path=None,page_loader=None,page_size=10,total=0):
        Screen.__init__(self,session)
        self.channels=list(channels or [])
        self.total=max(len(self.channels),int(total or 0))
        if self.total and len(self.channels)<self.total:self.channels.extend([None]*(self.total-len(self.channels)))
        self.cursor=max(0,min(int(current_index or 0),max(0,len(self.channels)-1))) if self.channels else 0
        self.offset=max(0,min(self.cursor,max(0,len(self.channels)-self.VISIBLE)))
        self.title_text=_clean_live_name(str(title or "Live Channels"))
        self.picon_path=_fit_zap_picon(picon_path)
        self.page_loader=page_loader if callable(page_loader) else None
        self.page_size=max(1,int(page_size or 10))
        self.jobs=queue.Queue();self.loading=False;self._pending_pages=set();self._page_attempts={};self._render_cache={}
        self["panel"]=Pixmap();self["folder_bg"]=Pixmap();self["channel_bg"]=Pixmap();self["picon"]=Pixmap()
        self["footer_count_bg"]=Pixmap();self["footer_page_bg"]=Pixmap();self["footer_play_bg"]=Pixmap();self["footer_close_bg"]=Pixmap()
        self["title"]=Label(self.title_text);self["subtitle"]=Label("")
        self["counter"]=Label("");self["footer_page"]=Label("◀▶  Page");self["footer_play"]=Label("OK  Play");self["footer_close"]=Label("BACK  Close")
        for i in range(self.VISIBLE):
            self["bg%d"%i]=Pixmap();self["num%d"%i]=Label("");self["row%d"%i]=Label("")
        self["actions"]=ActionMap(["OkCancelActions","DirectionActions"],{
            "ok":self.keyOK,"cancel":self._cancel,"up":lambda:self._move(-1),"down":lambda:self._move(1),
            "left":self.page_up,"right":self.page_down,
        },-1000)
        self.timer=eTimer();self.timer_conn=None
        try:self.timer_conn=self.timer.timeout.connect(self._drain)
        except Exception:self.timer.callback.append(self._drain)
        self.onLayoutFinish.append(self._ready);self.onClose.append(self._cleanup)

    def _fit_folder_title_font(self):
        """Fit the complete Live folder name inside the fixed Mini List header."""
        try:
            value=str(self.title_text or "Live Channels")
            # Weighted width estimate: Arabic glyphs are visually wider than
            # narrow Latin punctuation, so raw len() produced oversized headers.
            units=sum(1.28 if ord(ch)>0x2ff else (0.55 if ch in " ilI1|.,:'" else 1.0) for ch in value)
            # Header is 330 px wide. Keep short names premium/large, then scale
            # continuously down to a readable floor for long mixed Arabic/Latin.
            size=int(max(14,min(24,round(24.0*min(1.0,20.5/max(1.0,units))))))
            if self["title"].instance is not None:
                self["title"].instance.setFont(gFont("Regular",size))
        except Exception as exc:
            optional_failure("player.zap_folder_font_fit",exc)

    def _ready(self):
        self._fit_folder_title_font()
        self.assets=_zap_assets(self.picon_path)
        try:
            p=self.assets.get("panel")
            if p:self["panel"].instance.setPixmapFromFile(p);self["panel"].show()
            folder=self.assets.get("folder");channel=self.assets.get("channel")
            if folder:self["folder_bg"].instance.setPixmapFromFile(folder);self["folder_bg"].show()
            if channel:self["channel_bg"].instance.setPixmapFromFile(channel);self["channel_bg"].show()
            for key,name in (("footer_count","footer_count_bg"),("footer_page","footer_page_bg"),
                             ("footer_play","footer_play_bg"),("footer_close","footer_close_bg")):
                path=self.assets.get(key)
                if path and os.path.isfile(path):
                    self[name].instance.setPixmapFromFile(path);self[name].show()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            if self.picon_path and os.path.isfile(self.picon_path):
                self["picon"].instance.setPixmapFromFile(self.picon_path);self["picon"].show()
            else:self["picon"].hide()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        self._render(force=True);self._load_all()
        try:self.timer.start(220,False)
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _load_page_for_index(self,index):
        """Load only the page needed by the visible cursor.

        The old drawer fetched every folder page immediately on open and repainted
        all 15 rows every 100ms as pages arrived. That looked like animation and
        competed with playback. Navigation is now local and page fetches are lazy.
        """
        if self.page_loader is None or not self.total:return
        page=max(1,int(index)//self.page_size+1)
        base=(page-1)*self.page_size
        end=min(len(self.channels),base+self.page_size)
        if base<end and all(isinstance(x,dict) for x in self.channels[base:end]):return
        pending=getattr(self,"_pending_pages",set())
        if page in pending:return
        attempts=getattr(self,"_page_attempts",{})
        if int(attempts.get(page,0) or 0)>=2:return
        attempts[page]=int(attempts.get(page,0) or 0)+1;self._page_attempts=attempts
        pending.add(page);self._pending_pages=pending
        def worker():
            try:
                try:rows=self.page_loader(page)
                except TypeError:rows=self.page_loader(page,None)
                except Exception:rows=[]
                self.jobs.put((page,[dict(x) for x in (rows or []) if isinstance(x,dict)]))
            finally:
                self.jobs.put((-page,[]))
        try:threading.Thread(target=worker,name="UltraZapPage%d"%page,daemon=True).start()
        except Exception:pending.discard(page)

    def _warm_visible_pages(self,include_adjacent=True):
        """Prefetch every portal page needed by the 15 visible drawer rows.

        The drawer viewport can span two portal pages (12-item portal page vs
        15 visible rows).  Loading only the cursor page left lower rows stuck on
        "Loading channel..." until the cursor physically reached them.
        """
        if self.page_loader is None or not self.channels:return
        total=len(self.channels)
        windows=[(self.offset,min(total-1,self.offset+self.VISIBLE-1))]
        if include_adjacent:
            if self.offset>0:
                prev=max(0,self.offset-self.VISIBLE);windows.append((prev,min(total-1,prev+self.VISIBLE-1)))
            nxt=self.offset+self.VISIBLE
            if nxt<total:windows.append((nxt,min(total-1,nxt+self.VISIBLE-1))
            )
        pages=set()
        for start,end in windows:
            if end<start:continue
            first=max(1,start//self.page_size+1);last=max(first,end//self.page_size+1)
            pages.update(range(first,last+1))
        for page in sorted(pages):
            self._load_page_for_index((page-1)*self.page_size)

    def _load_all(self):
        self._warm_visible_pages(include_adjacent=True)

    def _drain(self):
        changed_visible=False
        while True:
            try:page,rows=self.jobs.get_nowait()
            except queue.Empty:break
            if page<0:
                try:self._pending_pages.discard(-page)
                except Exception as exc:optional_failure("player.silent_guard",exc)
                continue
            if page==0:continue
            base=(page-1)*self.page_size
            if rows:
                try:self._page_attempts[page]=99
                except Exception as exc:optional_failure("player.silent_guard",exc)
            for i,row in enumerate(rows):
                pos=base+i
                if 0<=pos<len(self.channels):
                    self.channels[pos]=row
                    if self.offset<=pos<self.offset+self.VISIBLE:changed_visible=True
        if changed_visible:self._render(force=True)
        # Keep the whole current viewport warm even when portal pages arrive out of order.
        if not getattr(self,"_pending_pages",set()):
            self._warm_visible_pages(include_adjacent=True)

    def _name(self,item,index):
        if not isinstance(item,dict):return "Loading channel %d..."%(index+1)
        return _clean_live_name(item.get("name") or item.get("title") or ("Channel %d"%(index+1)))


    def _render(self,force=False):
        total=len(self.channels)
        item=self.channels[self.cursor] if 0<=self.cursor<total else None
        subtitle=self._name(item,self.cursor)[:64] if total else "No channels"
        counter="%d / %d"%(self.cursor+1,total) if total else "0 / 0"
        cache=self._render_cache
        if force or cache.get("subtitle")!=subtitle:
            self["subtitle"].setText(subtitle)
            try:
                units=sum(1.65 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in subtitle)
                size=19 if units<=27 else (17 if units<=34 else (15 if units<=43 else 13))
                if self["subtitle"].instance is not None:self["subtitle"].instance.setFont(gFont("Regular",size))
            except Exception as exc:optional_failure("player.zap_header_font",exc)
            cache["subtitle"]=subtitle
        if force or cache.get("counter")!=counter:
            self["counter"].setText(counter);cache["counter"]=counter
        normal=(getattr(self,"assets",{}) or {}).get("normal");selected=(getattr(self,"assets",{}) or {}).get("selected")
        for row in range(self.VISIBLE):
            idx=self.offset+row
            label=self._name(self.channels[idx],idx)[:45] if 0<=idx<total else ""
            display=("▶  "+label) if idx==self.cursor and label else label
            number=str(idx+1) if 0<=idx<total else ""
            bg=selected if idx==self.cursor else normal
            state=(idx,display,number,bg)
            if not force and cache.get(("row",row))==state:continue
            self["row%d"%row].setText(display)
            self["num%d"%row].setText(number)
            try:
                if bg and os.path.isfile(bg):
                    self["bg%d"%row].instance.setPixmapFromFile(bg);self["bg%d"%row].show()
                else:self["bg%d"%row].hide()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            cache[("row",row)]=state

    def _move(self,delta):
        if not self.channels:return
        total=len(self.channels)
        old=self.cursor
        if int(delta)>0:
            self.cursor=(self.cursor+1)%total
        else:
            self.cursor=(self.cursor-1)%total
        if self.cursor==0 and old==total-1:
            self.offset=0
        elif self.cursor==total-1 and old==0:
            self.offset=max(0,total-self.VISIBLE)
        elif self.cursor<self.offset:
            self.offset=self.cursor
        elif self.cursor>=self.offset+self.VISIBLE:
            self.offset=max(0,min(self.cursor-self.VISIBLE+1,total-self.VISIBLE))
        self._warm_visible_pages(include_adjacent=True)
        self._render(force=True)

    def _page_shift(self,direction):
        if not self.channels:return
        total=len(self.channels)
        row=max(0,self.cursor-self.offset)
        max_offset=max(0,total-self.VISIBLE)
        if int(direction)>0:
            if self.offset>=max_offset:
                # Final page: RIGHT means the actual final channel.
                self.cursor=total-1
                self.offset=max_offset
            else:
                self.offset=min(max_offset,self.offset+self.VISIBLE)
                self.cursor=min(total-1,self.offset+row)
        else:
            if self.offset<=0:
                # First page: LEFT from any row means channel 1.
                self.offset=0;self.cursor=0
            else:
                self.offset=max(0,self.offset-self.VISIBLE)
                self.cursor=min(total-1,self.offset+row)
        self._warm_visible_pages(include_adjacent=True)
        self._render(force=True)

    def page_up(self):
        self._page_shift(-1)

    def page_down(self):
        self._page_shift(1)

    def keyOK(self):
        if 0<=self.cursor<len(self.channels) and isinstance(self.channels[self.cursor],dict):
            self.close((self.cursor,self.channels[self.cursor]))

    def _cancel(self):
        self.close(None)

    def _cleanup(self):
        try:self.timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            if self.timer_conn is not None:self.timer_conn.disconnect()
        except Exception as exc:optional_failure("player.optional_guard",exc)


class IPTVInfoBarShowHide(object):
    """The same proven show/hide lifecycle used by the reference implementation players."""

    STATE_HIDDEN = 0
    STATE_HIDING = 1
    STATE_SHOWING = 2
    STATE_SHOWN = 3
    skipToggleShow = False

    def __init__(self):
        self.__showhide_tracker = ServiceEventTracker(
            screen=self,
            eventmap={iPlayableService.evStart: self.serviceStarted},
        )
        self.__state = self.STATE_SHOWN
        self.__locked = 0
        self.hideTimer = eTimer()
        try:
            self.hideTimer_conn = self.hideTimer.timeout.connect(self.doTimerHide)
        except Exception:
            self.hideTimer.callback.append(self.doTimerHide)
        self.hideTimer.start(6000, True)
        self.onShow.append(self.__onShow)
        self.onHide.append(self.__onHide)

    def OkPressed(self):
        self.toggleShow()

    def __onShow(self):
        self.__state = self.STATE_SHOWN
        self.startHideTimer()

    def __onHide(self):
        self.__state = self.STATE_HIDDEN

    def serviceStarted(self):
        if getattr(self, "execing", False):
            self.doShow()

    def startHideTimer(self):
        if self.__state == self.STATE_SHOWN and not self.__locked:
            self.hideTimer.stop()
            self.hideTimer.start(6000, True)
        self.skipToggleShow = False

    def doShow(self):
        self.hideTimer.stop()
        self.show()
        self.startHideTimer()

    def doTimerHide(self):
        self.hideTimer.stop()
        if self.__state == self.STATE_SHOWN:
            self.hide()

    def toggleShow(self):
        if self.skipToggleShow:
            self.skipToggleShow = False
            return
        if self.__state == self.STATE_HIDDEN:
            self.show()
            self.hideTimer.stop()
        else:
            self.hide()
            self.startHideTimer()

    def lockShow(self):
        self.__locked += 1
        if getattr(self, "execing", False):
            self.show()
            self.hideTimer.stop()

    def unlockShow(self):
        self.__locked = max(0, self.__locked - 1)
        if getattr(self, "execing", False):
            self.startHideTimer()



NEXT_EPISODE_SKIN = """
<screen name="UltraStalkerNextEpisodePrompt" position="center,center" size="760,250" backgroundColor="#071522" flags="wfNoBorder">
    <eLabel position="0,0" size="760,250" backgroundColor="#071522" />
    <eLabel position="0,0" size="760,5" backgroundColor="#29b7ff" />
    <widget name="title" position="50,32" size="660,45" font="Regular;29" halign="center" foregroundColor="#ffffff" transparent="1" />
    <widget name="episode" position="55,88" size="650,42" font="Regular;22" halign="center" foregroundColor="#b9d7e8" transparent="1" />
    <widget name="countdown" position="55,143" size="650,34" font="Regular;20" halign="center" foregroundColor="#6ed7ff" transparent="1" />
    <widget name="hint" position="55,196" size="650,28" font="Regular;17" halign="center" foregroundColor="#91a9b8" transparent="1" />
</screen>
"""


class UltraStalkerNextEpisodePrompt(Screen):
    """Small cancelable 10-second autoplay prompt shown after a natural episode EOF."""

    skin = NEXT_EPISODE_SKIN

    def __init__(self, session, next_item, seconds=10):
        Screen.__init__(self, session)
        self.remaining = max(1, int(seconds or 10))
        item = next_item if isinstance(next_item, dict) else {}
        title = str(item.get("name") or item.get("title") or item.get("episode_name") or "Next Episode")
        number = item.get("episode") or item.get("number") or item.get("episode_id") or ""
        self["title"] = Label("Play Next Episode")
        self["episode"] = Label(("Episode %s  •  %s" % (number, title))[:70] if number else title[:70])
        self["countdown"] = Label("")
        self["hint"] = Label("OK Play now   •   BACK Cancel   •   YELLOW Disable autoplay")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok": self._accept, "green": self._accept,
            "cancel": self._cancel, "red": self._cancel,
            "yellow": self._disable_session,
        }, -1000)
        self.timer = eTimer()
        self.timer_conn = None
        try:
            self.timer_conn = self.timer.timeout.connect(self._tick)
        except Exception:
            self.timer.callback.append(self._tick)
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._cleanup_timer)

    def _start(self):
        self._render()
        try: self.timer.start(1000, False)
        except Exception as exc: optional_failure("player.optional_guard", exc)

    def _render(self):
        self["countdown"].setText("Next episode starts in %d seconds" % self.remaining)

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self._accept()
            return
        self._render()

    def _accept(self):
        self._cleanup_timer()
        self.close(True)

    def _cancel(self):
        self._cleanup_timer()
        self.close(False)

    def _disable_session(self):
        self._cleanup_timer()
        self.close("session_off")

    def _cleanup_timer(self):
        try: self.timer.stop()
        except Exception as exc: optional_failure("player.optional_guard", exc)
        try:
            if self.timer_conn is not None: self.timer_conn.disconnect()
        except Exception as exc: optional_failure("player.optional_guard", exc)
        try:
            if self._tick in self.timer.callback:self.timer.callback.remove(self._tick)
        except Exception as exc:optional_failure("player.optional_guard",exc)


class UltraStalkerPlayer(
    InfoBarBase,
    IPTVInfoBarShowHide,
    InfoBarAudioSelection,
    InfoBarSeek,
    InfoBarNotifications,
    InfoBarSummarySupport,
    InfoBarSubtitleSupport,
    InfoBarMoviePlayerSummarySupport,
    Screen,
):
    """Full-screen live/VOD player with reference implementation-style InfoBar behaviour."""

    skin = PLAYER_SKIN
    ALLOW_SUSPEND = True

    def __init__(self, session, streamurl, name, media_type="itv", servicetype=4097, item=None, reuse_current=False):
        Screen.__init__(self, session)
        self.session = session
        self.streamurl = str(streamurl or "").strip()
        self.media_type = str(media_type or "itv")
        _raw_name = str(name or "Ultra Stalker stream")
        self.name = _clean_live_name(_raw_name) if self.media_type in ("itv","live") else (_catalogue_title(_raw_name) or _raw_name)
        self.item = item if isinstance(item, dict) else {}
        self._return_service_ref_string=str(self.item.get("_return_service_ref_string") or "")
        self._service_handoff_done=False
        self.reuse_current = bool(reuse_current and self.media_type in ("itv","live"))
        _zap_snapshot=list(self.item.get("_live_folder_channels") or []) if self.media_type in ("itv","live") else []
        self._zap_page_loader=self.item.get("_live_page_loader") if callable(self.item.get("_live_page_loader")) else None
        try:self._zap_page_size=max(1,int(self.item.get("_live_page_size") or len(_zap_snapshot) or 10))
        except Exception:self._zap_page_size=max(1,len(_zap_snapshot) or 10)
        try:self._zap_total=max(len(_zap_snapshot),int(self.item.get("_live_folder_total") or 0))
        except Exception:self._zap_total=len(_zap_snapshot)
        try:self._zap_index=max(0,int(self.item.get("_live_absolute_index") or 0))
        except Exception:self._zap_index=0
        try:_zap_page=max(1,int(self.item.get("_live_folder_page") or (self._zap_index//self._zap_page_size+1)))
        except Exception:_zap_page=max(1,self._zap_index//self._zap_page_size+1)
        if self.media_type in ("itv","live") and self._zap_total:
            self._zap_channels=[None]*self._zap_total
            _zap_base=max(0,(_zap_page-1)*self._zap_page_size)
            for _i,_row in enumerate(_zap_snapshot):
                _pos=_zap_base+_i
                if _pos<len(self._zap_channels) and isinstance(_row,dict):self._zap_channels[_pos]=_row
        else:
            self._zap_channels=_zap_snapshot
        self._zap_title=str(self.item.get("_live_folder_title") or "Live Channels")
        self._zap_last_ok=0.0
        self._zap_open=False
        self._zap_infobar_armed=False
        for mixin in (
            InfoBarBase,
            IPTVInfoBarShowHide,
            InfoBarAudioSelection,
            InfoBarSeek,
            InfoBarNotifications,
            InfoBarSummarySupport,
            InfoBarSubtitleSupport,
            InfoBarMoviePlayerSummarySupport,
        ):
            try:
                mixin.__init__(self)
            except Exception as exc:
                LOG.warning("Player mixin init failed (%s): %s", mixin, exc)

        cfg = load_settings()
        try:configured_engine=int(cfg.get("service_type",servicetype))
        except Exception:configured_engine=int(servicetype or 4097)
        if configured_engine not in (1,4097,5001,5002,8193):configured_engine=4097
        self.engines = [configured_engine]
        self.engine_index = 0
        self.servicetype = configured_engine
        self._engine_locked_to_settings = True
        self.reference = None
        self._owned_reference_string = ""
        self.started = False
        self.start_attempt = 0.0
        self.failed = False
        self.restored = False
        self.resume_prompted = False
        self._resume_target = 0
        self._resume_verify_attempts = 0
        self._resume_grace_until = 0.0
        self._watch_started_at = 0.0
        self._history_save_min_seconds = 10
        self._last_manual_seek_at = 0.0
        self._early_eof_pending = False
        self._startup_guard_until = 0.0
        self._startup_guard_checks = 0
        self._startup_guard_last_position = -1
        self._startup_guard_reason = ""
        self._last_progress_position = 0
        self._last_progress_duration = 0
        self._hard_stop_attempts = 0
        self._hard_stop_pending_result = None
        self._hard_stop_closing = False
        self._closing_playback = False
        self._owned_external_pids = set()
        self._external_player_baseline = set(_all_external_player_pids())
        self._resume_seek_count = 0
        self._resume_verify_cycles = 0
        self._resume_prepared = False
        self._resume_prompt_open = False
        self._native_resume_started = False
        self._resume_audio_guard = False
        self._resume_was_muted = None
        self._resume_started_at = 0.0
        self._observed_quality = ""
        self._observed_width = 0
        self._observed_height = 0
        self._video_guard_misses = 0
        self._video_guard_last_engine = None
        self._engine_video_confirmed = False
        self._next_episode_prompted = False
        self._subtitle_user_override = False
        self._subtitle_disable_attempts = 0
        self._recovery_attempts = 0
        self._recovery_inflight = False
        self._recovery_queue = queue.Queue()
        self._recovery_cancel = threading.Event()
        self._recovery_thread = None
        self._recovery_client_lock = threading.RLock()
        self._recovery_client = None
        self._runtime_reconnects = 0
        self._last_network_activity = 0.0
        self._event_suppress_until = 0.0
        self._play_generation = 0
        self._memory_fuse_level = 0
        self._memory_fuse_last_rss = 0
        self._memory_fuse_emergency = False
        self._zap_switch_generation = 0
        self._zap_switch_queue = queue.Queue()
        self._zap_switch_inflight = False
        self._zap_switch_started_at = 0.0
        self.ar_id_player = -1
        runtime_breadcrumb("player_open",media_type=self.media_type,engine=int(self.servicetype or 0))

        self["resume_mask"] = Label("")
        self["brand"] = Label("Ultra Stalker")
        self["section"] = Label("Live TV / Movies / Series")
        self["connection"] = Label("PREPARING STREAM")
        self["logo"] = Pixmap();self["live_picon"] = Pixmap();self["adaptive_main"] = Pixmap();self["poster_neon_halo"] = Pixmap();self["adaptive_poster"] = Pixmap();self["adaptive_live_picon"] = Pixmap()
        self["adaptive_progress_track"] = Pixmap(); self["adaptive_keybar"] = Pixmap(); self["watched_progress_glow"] = ProgressBar(); self["watched_progress_glow"].setRange((0,100)); self["watched_progress_glow"].setValue(0); self["watched_progress"] = ProgressBar(); self["watched_progress"].setRange((0,100)); self["watched_progress"].setValue(0)
        self["chip_video_quality"] = Pixmap(); self["chip_video_codec"] = Pixmap(); self["chip_audio_codec"] = Pixmap(); self["chip_bitrate"] = Pixmap()
        self["chip_stream"] = Pixmap(); self["chip_audio"] = Pixmap(); self["chip_subtitles"] = Pixmap(); self["chip_engine"] = Pixmap()
        self["channel"] = Label(self.name[:120])
        self["state"] = Label("PLAY")
        self["speed"] = Label("")
        self["statusicon"] = MultiPixmap()
        self["now"] = Label(self._now_text())
        self["next_header"] = Label("NEXT")
        self["next"] = Label(self._next_text())
        self["next_meta"] = Label(_next_meta(self.item, self.media_type))
        description = self.item.get("description") or self.item.get("descr") or self.item.get("plot") or ""
        self["description"] = Label(str(description)[:92])
        self["category"] = Label(self._category_label())
        self["engine"] = Label(_engine_label(self.servicetype))
        self["extension"] = Label("STREAM")
        self["quality"] = Label("AUTO")
        self["media_meta"] = Label(_meta_text(self.item, self.media_type)[:12])
        self["video_quality"] = Label(_quality(self.item, self.name))
        self["video_codec"] = Label("VIDEO")
        self["audio_codec"] = Label("AUDIO")
        self["bitrate"] = Label("Bitrate --")
        self["skip_hint"] = Label("")
        self["progress_crystal"] = Pixmap(); self["progress_neon"] = Pixmap(); self._adaptive_accent_neon = "#55b9ff"; self._progress_neon_value = -1
        self._bitrate_last_rx = None
        self._bitrate_last_ts = None
        self._bitrate_smoothed_bps = None
        self._bitrate_source = ""
        self["audio_tag"] = Label("AUDIO")
        self["subtitle_tag"] = Label("SUBTITLES")
        self["online_subtitle"] = Label("")
        self._online_subtitle_cues = []
        self._online_subtitle_active = False
        self._online_subtitle_index = 0
        self._online_subtitle_path = ""
        self._online_subtitle_searching = False
        self._online_subtitle_queue = queue.Queue()
        self._online_subtitle_generation = 0
        self._online_subtitle_cancel = threading.Event()
        self._online_subtitle_offset_ms = 0
        self._online_subtitle_scale = 1.0
        self._online_subtitle_overlay = None
        self._online_subtitle_display = None
        self._online_subtitle_current_text = ""
        _sub_cfg=load_settings()
        self._subtitle_style={
            "color":str(_sub_cfg.get("subtitle_color") or "#FFFFFF"),
            "background":bool(_sub_cfg.get("subtitle_background",False)),
            "position":max(-260,min(260,int(_sub_cfg.get("subtitle_position",0) or 0))),
        }
        self._media_info_open = False
        self["format_tag"] = Label(_meta_text(self.item, self.media_type)[13:35] or "STREAM INFO")
        self["transport_hint"] = Label("<< Rewind   >> Forward   0 Restart")
        self["key_red"] = Label("Exit")
        self["key_green"] = Label("Aspect Ratio")
        self["key_yellow"] = Label("Subtitles")
        self["key_blue"] = Label("Engine Locked")
        self["hint"] = Label("")

        try:
            self["statusicon"].setPixmapNum(6)
        except Exception as exc:
            optional_failure("player", exc)
        self["player_actions"] = ActionMap(
            ["UltraStalkerPlayerActions", "OkCancelActions", "ColorActions", "InfobarActions", "NumberActions"],
            {
                "cancel": self.back,
                "stop": self.back,
                "red": self.back,
                "ok": self.OKButton,
                "info": self.show_media_info,
                "blue": self.toggleStreamType,
                "tv": self.toggleStreamType,
                "green": self.nextAR,
                "yellow": self.subtitleSelection,
                "0": self.restartStream,
                # Numeric trick-play: keep the receiver's familiar 10-second
                # jumps, but never route 3 to the old Skip Credits feature.
                "1": self.seekBack10,
                "3": self.seekForward10,
            },
            -2,
        )

        # us132: native-style decisive EXIT lifecycle.  OpenBH InfoBar
        # mixins can bind OkCancelActions too, so keep a dedicated, very-high
        # priority exit map whose only job is to reach our hard-stop path.
        # The normal player map remains responsible for all other keys.
        self["hard_exit_actions"] = ActionMap(
            ["UltraStalkerPlayerActions", "OkCancelActions"],
            {"cancel": self.back, "stop": self.back},
            -10000,
        )

        # OE-A/OpenBH InfoBar mixins also bind Yellow. Keep our Nova information
        # overlay on a dedicated higher-priority ColorActions map so the legacy
        # "Information (n)" popup can never steal the key.
        self["nova_yellow_actions"] = ActionMap(["ColorActions"], {"yellow": self.subtitleSelection}, -1000)
        self["online_subtitle_actions"] = ActionMap(
            ["InfobarSubtitleSelectionActions"],
            {"subtitleSelection": self.subtitleSelection},
            -1200,
        )
        self["subtitle_volume_watch_actions"] = ActionMap(
            ["VolumeActions"],
            {"volumeUp":self._subtitle_volume_osd_event,
             "volumeDown":self._subtitle_volume_osd_event,
             "volumeMute":self._subtitle_volume_osd_event},
            -1100,
        )

        eventmap = {iPlayableService.evStart: self._service_started}
        _ev_updated=getattr(iPlayableService,"evUpdatedInfo",None)
        if _ev_updated is not None:eventmap[_ev_updated]=self._native_resume_updated
        for event_name, callback in (("evTuneFailed", self._service_failed), ("evEOF", self._service_eof)):
            event_value = getattr(iPlayableService, event_name, None)
            if event_value is not None:
                eventmap[event_value] = callback
        self.__event_tracker = ServiceEventTracker(screen=self, eventmap=eventmap)
        try:
            # Always paint receiver-safe neutral glass first. Adaptive chrome
            # then replaces it on the same layout pass when artwork is ready.
            self.onLayoutFinish.append(self._apply_adaptive_player_chrome)
            self.onLayoutFinish.append(self._init_online_subtitle_display)
        except Exception as exc:optional_failure("player.optional_guard",exc)

        self.startup_timer = eTimer()
        try:
            self.startup_timer_conn = self.startup_timer.timeout.connect(self._startup_timeout)
        except Exception:
            self.startup_timer.callback.append(self._startup_timeout)


        # Proven native-style resume timer: wait briefly after evStart, then
        # issue exactly one absolute seekTo() against the live seek interface.
        self.resume_timer = eTimer()
        self.resume_timer_conn = None
        try:
            self.resume_timer_conn = self.resume_timer.timeout.connect(self._reference_resume)
        except Exception:
            self.resume_timer.callback.append(self._reference_resume)

        self.resume_verify_timer = eTimer()
        try:
            self.resume_verify_timer_conn = self.resume_verify_timer.timeout.connect(self._verify_resume_position)
        except Exception:
            self.resume_verify_timer.callback.append(self._verify_resume_position)


        self.subtitle_default_timer = eTimer()
        try:
            self.subtitle_default_timer_conn = self.subtitle_default_timer.timeout.connect(self._enforce_default_subtitles_off)
        except Exception:
            self.subtitle_default_timer.callback.append(self._enforce_default_subtitles_off)

        self.online_subtitle_timer = eTimer()
        try:
            self.online_subtitle_timer_conn = self.online_subtitle_timer.timeout.connect(self._online_subtitle_tick)
        except Exception:
            self.online_subtitle_timer.callback.append(self._online_subtitle_tick)
        self.online_subtitle_result_timer = eTimer()
        try:
            self.online_subtitle_result_timer_conn = self.online_subtitle_result_timer.timeout.connect(self._drain_online_subtitle_result)
        except Exception:
            self.online_subtitle_result_timer.callback.append(self._drain_online_subtitle_result)

        self.subtitle_volume_restore_timer=eTimer();self.subtitle_volume_restore_timer_conn=None
        try:self.subtitle_volume_restore_timer_conn=self.subtitle_volume_restore_timer.timeout.connect(self._restore_subtitle_after_volume_osd)
        except Exception:self.subtitle_volume_restore_timer.callback.append(self._restore_subtitle_after_volume_osd)

        self.progress_timer = eTimer()
        try:
            self.progress_timer_conn = self.progress_timer.timeout.connect(self._periodic_progress_save)
        except Exception:
            self.progress_timer.callback.append(self._periodic_progress_save)

        self.crystal_timer = eTimer()
        try:
            self.crystal_timer_conn = self.crystal_timer.timeout.connect(self._update_progress_crystal)
        except Exception:
            self.crystal_timer.callback.append(self._update_progress_crystal)

        self.hard_stop_timer = eTimer()
        try:
            self.hard_stop_timer_conn = self.hard_stop_timer.timeout.connect(self._verify_hard_stop)
        except Exception:
            self.hard_stop_timer.callback.append(self._verify_hard_stop)

        # Some ServiceApp/ExtePlayer builds emit a transient EOF one or two
        # seconds after evStart while the same service keeps playing. Do not
        # immediately restart the movie; verify actual playback first.
        self.eof_guard_timer = eTimer()
        try:
            self.eof_guard_timer_conn = self.eof_guard_timer.timeout.connect(self._verify_early_eof)
        except Exception:
            self.eof_guard_timer.callback.append(self._verify_early_eof)

        self.recovery_timer = eTimer()
        try:
            self.recovery_timer_conn = self.recovery_timer.timeout.connect(self._drain_recovery_result)
        except Exception:
            self.recovery_timer.callback.append(self._drain_recovery_result)

        self.zap_switch_timer = eTimer()
        try:
            self.zap_switch_timer_conn = self.zap_switch_timer.timeout.connect(self._drain_zap_switch_result)
        except Exception:
            self.zap_switch_timer.callback.append(self._drain_zap_switch_result)

        # A stream must stay healthy for a while before retry budgets are reset.
        # This prevents a flapping channel from creating an endless reconnect loop.
        self.stable_timer = eTimer()
        try:
            self.stable_timer_conn = self.stable_timer.timeout.connect(self._mark_stream_stable)
        except Exception:
            self.stable_timer.callback.append(self._mark_stream_stable)

        # reference implementation-style aspect-preserving poster decoder.
        self.PicLoad = ePicLoad()
        self.PicLoad_conn = None
        self._poster_path = None
        try:
            self.PicLoad.PictureData.get().append(self._decode_poster)
        except Exception:
            try:
                self.PicLoad_conn = self.PicLoad.PictureData.connect(self._decode_poster)
            except Exception:
                self.PicLoad_conn = None

        # Poll actual service data after playback starts. This affects labels only;
        # the proven playback and MAG workflow remains untouched.
        self.stream_info_timer = eTimer()
        self.stream_info_timer_conn = None
        try:
            self.stream_info_timer_conn = self.stream_info_timer.timeout.connect(self._update_stream_info)
        except Exception:
            self.stream_info_timer.callback.append(self._update_stream_info)

        # Last-resort memory fuse. This does not restart playback. Its job is to
        # shed optional UI memory long before Broadcom's OOM killer reaches the
        # ~600 MB RSS failure zone observed on the target receiver.
        self.memory_fuse_timer=eTimer();self.memory_fuse_timer_conn=None
        try:
            self.memory_fuse_timer_conn=self.memory_fuse_timer.timeout.connect(self._memory_fuse_tick)
        except Exception:
            self.memory_fuse_timer.callback.append(self._memory_fuse_tick)

        try:
            self.onPlayStateChanged.append(self._play_state_changed)
        except Exception as exc:
            optional_failure("player", exc)
        self.onLayoutFinish.append(self._layout_ready)
        try:self.onShow.append(self._restore_player_chrome_after_show)
        except Exception as exc:optional_failure("player.chrome_show_hook",exc)
        self.onFirstExecBegin.append(self._begin_playback)
        self.onClose.append(self._cleanup)

    def _layout_ready(self):
        try:
            if not self._resume_prepared:
                self["resume_mask"].hide()
        except Exception as exc:
            optional_failure("player.resume_mask_layout", exc)
        artwork=_cached_poster(self.item, self.name, self.media_type)
        if self.media_type in ("itv","live"):
            self._load_live_picon(artwork)
        else:
            self._load_poster(artwork)
        self._update_stream_info()


    def _load_live_picon(self, path):
        prepared=_prepare_live_picon(path,(220,132))
        try:
            self["logo"].hide();self["poster_neon_halo"].hide();self["adaptive_poster"].hide()
            if prepared and os.path.isfile(prepared) and self["live_picon"].instance is not None:
                self["live_picon"].instance.setPixmapFromFile(prepared);self["live_picon"].show()
        except Exception as exc:optional_failure("player.live_picon_apply",exc)

    def _resume_bookmark(self):
        try:
            position = max(0, int(self.item.get("_resume_position") or 0))
            duration = max(0, int(self.item.get("_resume_duration") or 0))
        except Exception:
            position = 0; duration = 0
        # Ignore only trivial starts and bookmarks that meet the same configured
        # completion policy used by history/Continue Watching.  Older builds had
        # a hard-coded four-minute tail here, so a perfectly valid bookmark could
        # be stored yet silently refused on the next open.
        if position < (10 * 90000):
            return 0
        if duration and (position >= duration or max(0,duration-position) <= 10*90000):
            return 0
        return position

    def _begin_playback(self):
        """Start playback first, then resume from evStart like the proven native player.

        Resume persistence is already stable-ID based in Ultra Stalker.  The only
        part borrowed here is the working playback lifecycle: playService(), wait
        for evStart, pause 900 ms, obtain the current seek interface and seekTo().
        """
        self._resume_target = 0
        self._resume_seek_count = 0
        self._resume_verify_attempts = 0
        self._resume_verify_cycles = 0
        self._resume_prepared = False
        self._resume_prompt_open = False
        self._native_resume_started = False
        behavior = str(load_settings().get("resume_behavior", "always") or "always").lower()
        self.resume_prompted = bool(behavior == "start")
        if self.reuse_current:
            try:
                current=self.session.nav.getCurrentlyPlayingServiceReference()
                if current is not None:
                    self.reference=current
                    try:self._owned_reference_string=current.toString()
                    except Exception:self._owned_reference_string=""
                    self.started=True;self.failed=False;self._watch_started_at=time.time()
                    self.start_attempt=time.time();self._last_network_activity=time.monotonic()
                    self._runtime_reconnects=0;self._recovery_attempts=0
                    self["connection"].setText("PLAYING  •  %s" % _engine_label(self.servicetype))
                    runtime_breadcrumb("player_adopt_current",media_type=self.media_type,engine=int(self.servicetype or 0))
                    try:self.stable_timer.stop();self.stable_timer.start(30000,True)
                    except Exception as exc:optional_failure("player.optional_guard",exc)
                    try:self.stream_info_timer.start(700,True)
                    except Exception as exc:optional_failure("player.optional_guard",exc)
                    return
            except Exception as exc:
                optional_failure("player.adopt_current",exc)
        self.playStream(self.servicetype, self.streamurl)

    def _resume_before_start_answer(self, answer, bookmark):
        self._resume_prompt_open = False
        self.resume_prompted = True
        if answer:
            self._prepare_instant_resume(bookmark)
        self.playStream(self.servicetype, self.streamurl)

    def _prepare_instant_resume(self, position):
        self._resume_target = max(0, int(position or 0))
        if not self._resume_target:
            return
        self._resume_prepared = True
        self._resume_started_at = time.time()
        self._resume_verify_attempts = 0
        self._resume_seek_count = 0
        self._resume_verify_cycles = 0
        self._resume_grace_until = time.time() + 16.0
        # Keep playback lifecycle native. We wait only for seekability and then
        # issue one absolute bookmark seek, matching the proven reference flow.
        self["connection"].setText("RESUMING  •  preparing bookmark")

    def _set_resume_audio_guard(self, enabled):
        if eDVBVolumecontrol is None:
            return
        try:
            volume = eDVBVolumecontrol.getInstance()
            if volume is None:
                return
            if enabled:
                if not self._resume_audio_guard:
                    try:
                        self._resume_was_muted = bool(volume.isMuted())
                    except Exception:
                        self._resume_was_muted = False
                    if not self._resume_was_muted:
                        volume.setMuted(True)
                    self._resume_audio_guard = True
            elif self._resume_audio_guard:
                if self._resume_was_muted is False:
                    volume.setMuted(False)
                self._resume_audio_guard = False
        except Exception as exc:
            optional_failure("player.resume_audio_guard", exc)

    def _release_resume_shield(self):
        self._resume_prepared = False
        try:
            self["resume_mask"].hide()
        except Exception as exc:
            optional_failure("player.resume_mask_hide", exc)
        self._set_resume_audio_guard(False)

    def _connect_picload(self):
        self.PicLoad_conn = None
        try:
            self.PicLoad.PictureData.get().append(self._decode_poster)
        except Exception:
            try:
                self.PicLoad_conn = self.PicLoad.PictureData.connect(self._decode_poster)
            except Exception:
                self.PicLoad_conn = None

    def _load_poster(self, path):
        """Fill the poster card exactly, using a high-quality center crop."""
        prepared=_prepare_player_poster_fill(path,(250,375))
        self._poster_path = prepared or path
        try:self["live_picon"].hide();self["adaptive_live_picon"].hide()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            if prepared and os.path.isfile(prepared) and self["logo"].instance is not None:
                self["logo"].instance.setPixmapFromFile(prepared)
                self["logo"].show()
                return
        except Exception as exc:
            optional_failure("player.poster_direct",exc)
        try:
            self.PicLoad.setPara([242, 330, 1, 1, 0, 1, "FF000000"])
            result = self.PicLoad.startDecode(self._poster_path)
            if result:
                try:
                    callbacks=self.PicLoad.PictureData.get()
                    if self._decode_poster in callbacks:callbacks.remove(self._decode_poster)
                except Exception as exc:optional_failure("player.optional_guard",exc)
                try:
                    if self.PicLoad_conn is not None:self.PicLoad_conn.disconnect()
                except Exception as exc:optional_failure("player.optional_guard",exc)
                self.PicLoad = ePicLoad()
                self._connect_picload()
                self.PicLoad.setPara([242, 330, 1, 1, 0, 1, "FF000000"])
                self.PicLoad.startDecode(self._poster_path)
        except Exception:
            try:
                self["logo"].instance.setPixmapFromFile(self._poster_path)
                self["logo"].show()
            except Exception as exc:
                optional_failure("player", exc)
    def _decode_poster(self, pic_info=None):
        try:
            ptr = self.PicLoad.getData()
            if ptr is not None and self["logo"].instance:
                self["logo"].instance.setPixmap(ptr)
                self["logo"].show()
        except Exception:
            try:
                if self._poster_path:
                    self["logo"].instance.setPixmapFromFile(self._poster_path)
                    self["logo"].show()
            except Exception as exc:
                optional_failure("player", exc)
    @staticmethod
    def _read_proc_number(paths, base=10):
        for pathname in paths:
            try:
                if os.path.exists(pathname):
                    value = open(pathname, "r").read().strip()
                    if value:
                        return int(value, base)
            except Exception as exc:
                optional_failure("player", exc)
        return None

    @staticmethod
    def _info_number(info, name, default=None):
        try:
            key = getattr(iServiceInformation, name)
            value = info.getInfo(key)
            if value == -2:
                raw = info.getInfoString(key)
                return int(str(raw).strip())
            if value is not None and value >= 0:
                return int(value)
        except Exception as exc:
            optional_failure("player", exc)
        return default

    @staticmethod
    def _video_codec_text(info):
        for pathname in ("/proc/stb/vmpeg/0/codec", "/proc/stb/vmpeg/0/vcodec"):
            try:
                if os.path.exists(pathname):
                    raw = open(pathname, "r").read().strip()
                    if raw:
                        return raw.upper().replace("HEVC", "H.265").replace("AVC", "H.264")[:12]
            except Exception as exc:
                optional_failure("player", exc)
        value = UltraStalkerPlayer._info_number(info, "sVideoType", None)
        return {
            0: "MPEG2", 1: "H.264", 2: "H.263", 3: "VC-1",
            4: "MPEG4", 5: "VC-1", 6: "MPEG1", 7: "H.265",
            8: "VP8", 9: "VP9", 10: "XVID", 12: "AVS",
            13: "AVS2", 16: "AV1",
        }.get(value, "VIDEO")

    @staticmethod
    def _audio_codec_text(service, info):
        try:
            tracks = service.audioTracks()
            if tracks and tracks.getNumberOfTracks() > 0:
                current = tracks.getCurrentTrack()
                if current < 0:
                    current = 0
                desc = str(tracks.getTrackInfo(current).getDescription() or "").strip()
                if desc:
                    token = desc.split()[0].upper()
                    aliases = {"AC-3": "AC3", "AC3+": "E-AC3", "HE-AAC": "AAC"}
                    return aliases.get(token, token)[:12]
        except Exception as exc:
            optional_failure("player", exc)
        try:
            key = getattr(iServiceInformation, "sAudioType")
            raw = info.getInfoString(key)
            if raw:
                return str(raw).upper()[:12]
        except Exception as exc:
            optional_failure("player", exc)
        return "AUDIO"

    @staticmethod
    def _network_rx_total():
        """Return receive bytes for active physical interfaces.

        This is used only when Enigma2 and the decoder expose no service
        bitrate. During playback the stream overwhelmingly dominates receive
        traffic, giving a useful live measured rate instead of a fake constant.
        """
        total=0
        try:
            root="/sys/class/net"
            for name in os.listdir(root):
                if name=="lo" or name.startswith(("sit","ip6tnl","tun","tap","docker","veth")):continue
                oper=os.path.join(root,name,"operstate")
                try:
                    state=open(oper,"r").read().strip()
                    if state not in ("up","unknown"):continue
                except Exception as exc:optional_failure("player",exc)
                path=os.path.join(root,name,"statistics","rx_bytes")
                try:total+=int(open(path,"r").read().strip() or 0)
                except Exception as exc:optional_failure("player",exc)
        except Exception as exc:optional_failure("player",exc)
        return total or None

    def _measured_network_bitrate(self):
        now=time.monotonic();rx=self._network_rx_total()
        previous_rx=self._bitrate_last_rx;previous_ts=self._bitrate_last_ts
        self._bitrate_last_rx=rx;self._bitrate_last_ts=now
        if rx is None or previous_rx is None or previous_ts is None:return None
        elapsed=max(0.25,now-previous_ts);delta=rx-previous_rx
        if delta<=0:return None
        self._last_network_activity = now
        bps=(float(delta)*8.0)/elapsed
        # Reject obvious unrelated bursts or counters that wrapped.
        if bps<32000 or bps>250000000:return None
        if self._bitrate_smoothed_bps is None:self._bitrate_smoothed_bps=bps
        else:self._bitrate_smoothed_bps=(0.62*self._bitrate_smoothed_bps)+(0.38*bps)
        return self._bitrate_smoothed_bps

    def _bitrate_text(self,info):
        bits_per_second=None;approximate=False
        proc_value=self._read_proc_number(("/proc/stb/vmpeg/0/bitrate","/proc/stb/vmpeg/0/stream_bitrate"),10)
        if proc_value and proc_value>0:
            # A few Broadcom drivers report Kbit/s, while most report bit/s.
            bits_per_second=(proc_value*1000) if proc_value<100000 else proc_value
            self._bitrate_source="decoder"
        if not bits_per_second:
            transfer=self._info_number(info,"sTransferBPS",None)
            if transfer and transfer>0:
                bits_per_second=transfer*8;self._bitrate_source="service"
        if not bits_per_second:
            bits_per_second=self._measured_network_bitrate();approximate=bool(bits_per_second);self._bitrate_source="network" if bits_per_second else ""
        if not bits_per_second:return "Bitrate measuring..."
        prefix="Bitrate ~" if approximate else "Bitrate "
        if bits_per_second>=1000000:return prefix+"%.2f Mbps"%(float(bits_per_second)/1000000.0)
        return prefix+"%d Kbps"%int(bits_per_second/1000.0)

    def _update_stream_info(self):
        if getattr(self, "restored", False):
            return
        try:
            service = self.session.nav.getCurrentService()
            info = service and service.info()
            if info:
                width = self._read_proc_number(("/proc/stb/vmpeg/0/xres",), 16)
                height = self._read_proc_number(("/proc/stb/vmpeg/0/yres",), 16)
                if not width:
                    width = self._info_number(info, "sVideoWidth", 0) or 0
                if not height:
                    height = self._info_number(info, "sVideoHeight", 0) or 0
                if height >= 2160 or width >= 3840:
                    quality = "UHD 2160p"
                elif height >= 1080:
                    quality = "FHD 1080p"
                elif height >= 720:
                    quality = "HD 720p"
                elif height > 0:
                    quality = "SD %sp" % height
                else:
                    quality = _quality(self.item, self.name)
                self["video_quality"].setText(quality[:15])
                self._observed_quality = quality
                self._observed_width = int(width or 0)
                self._observed_height = int(height or 0)
                if (width or height) and not self._engine_video_confirmed:
                    self._engine_video_confirmed=True;self._video_guard_misses=0
                # Deterministic playback: stream-info probes are informational only.
                # They must never restart/refresh the active service.
                # Persist the decoder-observed resolution for the catalogue
                # details screen. This is deliberately written only when the
                # receiver reports a real width/height, never from AUTO guesses.
                if (width or height) and self.media_type in ("vod","series","episode"):
                    try:
                        profile={"portal":self.item.get("_portal", ""),"mac":self.item.get("_mac", "")}
                        remember_content_quality(profile,self.media_type,self.item,quality,width,height)
                        # Episode playback should also teach the parent series
                        # when the payload carries a stable series id/title.
                        if self.media_type=="episode":
                            parent=self.item.get("_quality_parent_item") if isinstance(self.item,dict) else None
                            if isinstance(parent,dict) and parent:
                                remember_content_quality(profile,"series",parent,quality,width,height)
                    except Exception as exc:optional_failure("player.quality_cache_write",exc)
                self["video_codec"].setText(self._video_codec_text(info))
                self["audio_codec"].setText(self._audio_codec_text(service, info))
                self["bitrate"].setText(self._bitrate_text(info))
        except Exception as exc:
            optional_failure("player", exc)
        # Live continuity is driven by real Enigma2 EOF/tune-failed events.
        # Do not synthesize a stall from a timestamp that is not backed by actual
        # socket receive counters; that old watchdog could restart healthy Live TV.
        try:
            self.stream_info_timer.stop()
            self.stream_info_timer.start(1000, True)
        except Exception as exc:
            optional_failure("player", exc)
    def _memory_fuse_release_visuals(self, aggressive=False):
        # Playback service is deliberately left untouched. Only optional native
        # graphics/decoder helpers are released.
        # Never shed the core InfoBar glass.  With large portal libraries RSS can
        # already be above the old 430 MB threshold when Player opens; the old
        # fuse therefore stripped adaptive_main/keybar/chips a few seconds later
        # and left naked labels over video.  Only artwork/decorative layers are
        # disposable while playback continues.
        for name in (
            "logo","live_picon","poster_neon_halo","adaptive_poster",
            "adaptive_live_picon","progress_crystal","progress_neon"
        ):
            try:
                widget=self[name]
                if widget.instance is not None:widget.instance.setPixmap(None)
                try:widget.hide()
                except Exception as exc:optional_failure("player.silent_guard",exc)
            except Exception as exc:optional_failure("player.silent_guard",exc)
        if aggressive:
            try:self.PicLoad.PictureData.get().remove(self._decode_poster)
            except Exception as exc:optional_failure("player.silent_guard",exc)
            try:self._poster_path=None
            except Exception as exc:optional_failure("player.silent_guard",exc)
            try:self.stream_info_timer.stop()
            except Exception as exc:optional_failure("player.silent_guard",exc)
        try:
            with _PROGRESS_FRAME_LOCK:_PROGRESS_FRAME_PENDING.clear()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        try:gc.collect()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        _malloc_trim()

    def _memory_fuse_tick(self):
        if self.restored or self._closing_playback:
            try:self.memory_fuse_timer.stop()
            except Exception as exc:optional_failure("player.silent_guard",exc)
            return
        rss=_process_rss_kb();self._memory_fuse_last_rss=rss
        if not rss:return
        # 430 MB: shed decorative native pixmaps and Python garbage.
        if rss >= 430*1024 and self._memory_fuse_level < 1:
            self._memory_fuse_level=1
            runtime_breadcrumb("player_memory_fuse",level=1,rss_kb=int(rss))
            self._memory_fuse_release_visuals(False)
        # 510 MB: stop all non-essential player image polling/decoding.
        if rss >= 510*1024 and self._memory_fuse_level < 2:
            self._memory_fuse_level=2
            runtime_breadcrumb("player_memory_fuse",level=2,rss_kb=int(rss))
            self._memory_fuse_release_visuals(True)
            try:self["connection"].setText("MEMORY PROTECTION  •  playback preserved")
            except Exception as exc:optional_failure("player.silent_guard",exc)
        # 555 MB: protect Enigma2 itself. Better to leave the current Player
        # cleanly than let the kernel kill the entire GUI around 600 MB.
        if rss >= 555*1024 and not self._memory_fuse_emergency:
            self._memory_fuse_emergency=True
            runtime_breadcrumb("player_memory_fuse",level=3,rss_kb=int(rss))
            try:self["connection"].setText("MEMORY SAFETY EXIT")
            except Exception as exc:optional_failure("player.silent_guard",exc)
            try:self._save_history_progress(force=True)
            except Exception as exc:optional_failure("player.silent_guard",exc)
            self._close_player(save_progress=False)

    def _restore_player_chrome_after_show(self):
        """Re-bind the exact glass pixmaps after every InfoBar show.

        Enigma2 can drop native pixmap surfaces while a Screen is hidden even
        though the Python widgets survive. Rebinding cached files is cheap and
        avoids regenerating adaptive artwork on every OK press.
        """
        if getattr(self,"restored",False) or getattr(self,"_closing_playback",False):return
        # Live picons can arrive asynchronously after playback starts. The old
        # InfoBar kept the neutral blue frames created at init forever, while the
        # later-opened Mini List already saw the real picon palette. Refresh from
        # the now-cached picon before rebinding the InfoBar. Generated frames are
        # persistent/cached, so subsequent OK presses do not redo image work.
        if self.media_type in ("itv","live"):
            try:
                palette_item=self.item
                if 0<=int(getattr(self,"_zap_index",0))<len(getattr(self,"_zap_channels",[]) or []):
                    row=(getattr(self,"_zap_channels",[]) or [])[int(self._zap_index)]
                    if isinstance(row,dict):palette_item=row
                live_art=_cached_poster(palette_item,self.name,self.media_type)
                self._load_live_picon(live_art)
                self._apply_adaptive_player_chrome()
            except Exception as exc:optional_failure("player.live_infobar_adaptive_refresh",exc)
        frames=dict(getattr(self,"_player_chrome_frames",{}) or _fallback_player_frames())
        mapping=(("main","adaptive_main"),("track","adaptive_progress_track"),("keybar","adaptive_keybar"),
                 ("chip_quality","chip_video_quality"),("chip_video","chip_video_codec"),
                 ("chip_audio_codec","chip_audio_codec"),("chip_bitrate","chip_bitrate"),
                 ("chip_stream","chip_stream"),("chip_audio","chip_audio"),
                 ("chip_subtitles","chip_subtitles"),("chip_engine","chip_engine"))
        for key,name in mapping:
            try:
                path=frames.get(key)
                if path and os.path.isfile(path) and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
            except Exception as exc:optional_failure("player.chrome_reshow",exc)
        # Artwork-specific chrome is restored without touching playback.
        try:
            if self.media_type in ("itv","live"):
                p=frames.get("live_picon")
                if p and os.path.isfile(p) and self["adaptive_live_picon"].instance is not None:
                    self["adaptive_live_picon"].instance.setPixmapFromFile(p);self["adaptive_live_picon"].show()
            else:
                p=frames.get("poster_halo")
                if p and os.path.isfile(p) and self["poster_neon_halo"].instance is not None:
                    self["poster_neon_halo"].instance.setPixmapFromFile(p);self["poster_neon_halo"].show()
        except Exception as exc:optional_failure("player.chrome_reshow_art",exc)

    def _apply_adaptive_player_chrome(self):
        try:
            palette_item=self.item
            if self.media_type in ("itv","live"):
                try:
                    if 0<=int(getattr(self,"_zap_index",0))<len(getattr(self,"_zap_channels",[]) or []):
                        row=(getattr(self,"_zap_channels",[]) or [])[int(self._zap_index)]
                        if isinstance(row,dict):palette_item=row
                except Exception:pass
            source=_cached_poster(palette_item,self.name,self.media_type)
            fallback=_fallback_player_frames()
            adaptive=_adaptive_player_frames(source)
            # Per-frame merge is deliberate.  A partially generated adaptive
            # set must never remove an otherwise valid neutral glass layer.
            frames=dict(fallback)
            if isinstance(adaptive,dict):
                for key,value in adaptive.items():
                    if value:
                        frames[key]=value
            self._player_chrome_frames=dict(frames)
            for key,name in (("main","adaptive_main"),("poster_halo","poster_neon_halo"),("poster","adaptive_poster"),("live_picon","adaptive_live_picon"),("track","adaptive_progress_track"),("keybar","adaptive_keybar"),
                             ("chip_quality","chip_video_quality"),("chip_video","chip_video_codec"),
                             ("chip_audio_codec","chip_audio_codec"),("chip_bitrate","chip_bitrate"),
                             ("chip_stream","chip_stream"),("chip_audio","chip_audio"),
                             ("chip_subtitles","chip_subtitles"),("chip_engine","chip_engine")):
                path=frames.get(key)
                if path and os.path.isfile(path) and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
                else:
                    # Only poster-specific decoration may disappear. Core glass
                    # should always exist from the bundled fallback assets.
                    if key in ("poster_halo","poster","live_picon"):
                        self[name].hide()
            try:
                if self.media_type in ("itv","live"):
                    self["poster_neon_halo"].hide(); self["adaptive_poster"].hide();self["adaptive_live_picon"].show()
                else:
                    # The VOD/episode poster and halo are now one composite pixmap.
                    # Hide the legacy sibling poster/frame widgets so Enigma2 cannot cover
                    # or flatten the external neon bloom.
                    self["adaptive_live_picon"].hide()
                    self["adaptive_poster"].hide()
                    self["logo"].hide()
                    self["poster_neon_halo"].show()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            accent=frames.get("accent") or "#b14b45"
            accent_soft=frames.get("accent_soft") or accent
            self._adaptive_accent_neon=frames.get("accent_neon") or accent_soft
            self._progress_neon_value=-1
            try:
                color=parseColor(accent)
                # Two-layer neon beam: a wide translucent aura plus a narrow white-hot
                # adaptive core.  No separate target/dot at the current position.
                hexrgb = accent.lstrip("#")
                self["watched_progress_glow"].instance.setForegroundColor(parseColor("#66" + hexrgb))
                self["watched_progress"].instance.setForegroundColor(parseColor(accent_soft))
                # Status text follows the poster palette instead of fixed cyan.
                self["connection"].instance.setForegroundColor(parseColor(accent_soft))
                self["quality"].instance.setForegroundColor(parseColor(accent_soft))
            except Exception as exc: optional_failure("player.adaptive_colors",exc)
            glow=frames.get("glow")
            if glow and os.path.isfile(glow) and self["progress_crystal"].instance is not None:
                self["progress_crystal"].instance.setPixmapFromFile(glow)
        except Exception as exc:optional_failure("player.adaptive_apply",exc)

    def _now_text(self):
        # The compact InfoBar is for playback state, not a duplicate channel name.
        # For Live, center text is reserved for real EPG only.
        if self.media_type in ("vod", "series", "episode", "catchup"):
            return ""
        text = self.item.get("now") or self.item.get("program") or self.item.get("epg_title") or ""
        text = str(text or "").strip()
        if not text:
            return ""
        try:
            if _clean_live_name(text).casefold() == _clean_live_name(self.name).casefold():
                return ""
        except Exception:
            if text.casefold() == str(self.name or "").casefold():
                return ""
        return text[:62]

    def _next_text(self):
        text = self.item.get("next") or self.item.get("next_program")
        if not text:
            if self.media_type in ("vod", "series", "episode", "catchup"):
                text = "Seek, audio tracks, subtitles and resume are available when supported by the stream"
            else:
                text = "Press BLUE to switch playback engine if the channel does not start"
        return str(text)[:56]

    def _init_online_subtitle_overlay(self):
        return

    def _init_online_subtitle_display(self):
        if self._online_subtitle_display is not None or SubtitleDisplay is None:
            return
        try:
            maker=getattr(self.session,"instantiateDialog",None)
            if callable(maker):
                self._online_subtitle_display=maker(SubtitleDisplay)
                self._online_subtitle_display.hideScreen()
                self._apply_subtitle_style_native()
        except Exception as exc:
            optional_failure("player.native_online_subtitle_init",exc)
            self._online_subtitle_display=None

    def _player_infobar_shown(self):
        return

    def _player_infobar_hidden(self):
        return

    def _set_online_subtitle_text(self,text):
        text=str(text or "")
        self._online_subtitle_current_text=text
        display=self._online_subtitle_display
        if display is None:
            # Safe fallback only; normal OpenBH path uses SubtitleDisplay.
            try:self["online_subtitle"].setText(text)
            except Exception:pass
            return
        try:
            if text:
                display.showSubtitles(text)
                self._apply_subtitle_style_native()
            else:
                display.hideSubtitles()
        except Exception as exc:
            optional_failure("player.native_online_subtitle_text",exc)

    def _subtitle_volume_osd_event(self):
        if self._online_subtitle_active and self._online_subtitle_current_text:
            try:self._set_online_subtitle_text(self._online_subtitle_current_text)
            except Exception:pass
            try:self.subtitle_volume_restore_timer.stop();self.subtitle_volume_restore_timer.start(80,True)
            except Exception:pass
        # Let the receiver's normal VolumeActions continue unchanged.
        return 0

    def _restore_subtitle_after_volume_osd(self):
        if not self._online_subtitle_active or not self._online_subtitle_current_text:return
        try:
            self._set_online_subtitle_text(self._online_subtitle_current_text)
            display=self._online_subtitle_display
            if display is not None:
                try:display.show()
                except Exception:pass
        except Exception as exc:optional_failure("player.subtitle_volume_restore",exc)

    def subtitleSelection(self,preselect_action=None):
        """One subtitle entry point: embedded tracks or exact online Arabic."""
        self._subtitle_user_override = True
        try:
            self.subtitle_default_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        if ChoiceBox is None:
            return self._open_native_subtitles()
        choices=[
            ("Embedded subtitles", "embedded"),
            ("Arabic Online • SubDL", "online_ar"),
            ("Subtitle Color • %s"%_subtitle_color_name(self._subtitle_style.get("color")), "style_color"),
            ("Subtitle Background • %s"%("Black" if self._subtitle_style.get("background") else "Off"), "style_background"),
            ("Subtitle Position • %s"%_subtitle_position_name(self._subtitle_style.get("position")), "style_position"),
        ]
        if self._online_subtitle_active:
            choices += [
                ("Auto Sync • lightweight", "auto_sync"),
                ("Subtitle delay -0.5 sec", "delay_minus"),
                ("Subtitle delay +0.5 sec", "delay_plus"),
                ("Reset subtitle sync", "sync_reset"),
            ]
        choices.append(("Disable subtitles", "disable"))
        try:
            source=str(getattr(self,"_poster_path","") or _cached_poster(self.item,self.name,self.media_type) or "")
            selected=0
            if preselect_action:
                for idx,row in enumerate(choices):
                    if isinstance(row,(tuple,list)) and len(row)>1 and row[1]==preselect_action:
                        selected=idx;break
            self.session.openWithCallback(
                self._subtitle_menu_selected,
                SubtitleGlassChoiceScreen,
                title="Ultra Stalker • Subtitles",
                choices=choices,
                source_path=source,
                selected=selected,
            )
            return 1
        except Exception as exc:
            optional_failure("player.subtitle_menu",exc)
            return self._open_native_subtitles()

    def _open_native_subtitles(self):
        self._disable_online_subtitles()
        try:
            return InfoBarSubtitleSupport.subtitleSelection(self)
        except Exception as exc:
            optional_failure("player.native_subtitleSelection", exc)
            return 0

    def _subtitle_menu_selected(self, choice=None):
        if not choice:
            return
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if action=="embedded":
            self._open_native_subtitles()
        elif action=="disable":
            self._disable_online_subtitles()
            self._disable_native_subtitles()
            try:self["connection"].setText("PLAYING  •  SUBTITLES OFF")
            except Exception:pass
        elif action=="online_ar":
            self._start_online_arabic_subtitles()
        elif action=="style_color":
            self._open_subtitle_color_menu()
        elif action=="style_background":
            self._open_subtitle_background_menu()
        elif action=="style_position":
            self._open_subtitle_position_menu()
        elif action=="delay_minus":
            self._adjust_subtitle_delay(-500)
            self.subtitleSelection("delay_minus")
        elif action=="delay_plus":
            self._adjust_subtitle_delay(500)
            self.subtitleSelection("delay_plus")
        elif action=="sync_reset":
            self._online_subtitle_offset_ms=0;self._online_subtitle_scale=1.0;self._save_subtitle_sync()
            self.subtitleSelection("sync_reset")
        elif action=="auto_sync":
            self._auto_sync_subtitle()

    def _subtitle_picker_source(self):
        return str(getattr(self,"_poster_path","") or _cached_poster(self.item,self.name,self.media_type) or "")

    def _persist_subtitle_style(self):
        try:
            cfg=load_settings()
            cfg["subtitle_color"]=str(self._subtitle_style.get("color") or "#FFFFFF")
            cfg["subtitle_background"]=bool(self._subtitle_style.get("background",False))
            cfg["subtitle_position"]=max(-260,min(260,int(self._subtitle_style.get("position",0) or 0)))
            save_settings(cfg)
        except Exception as exc:optional_failure("player.subtitle_style_save",exc)

    def _apply_subtitle_style_native(self):
        """Style the stock OpenBH SubtitleDisplay in-place.

        No replacement Screen, no alternate subtitle lifecycle. The receiver still
        owns show/hide/timing; Ultra only changes the existing label presentation.
        """
        display=self._online_subtitle_display
        if display is None:return
        try:
            label=display["subtitles"]
            inst=getattr(label,"instance",None)
            if inst is None:return
            inst.setForegroundColor(parseColor(str(self._subtitle_style.get("color") or "#FFFFFF")))
            background=bool(self._subtitle_style.get("background",False))
            try:inst.setBackgroundColor(parseColor("#000000"))
            except Exception:pass
            try:inst.setTransparent(0 if background else 1)
            except Exception:pass

            # Native showSubtitles has already measured the cue. Reposition that
            # same label, without replacing SubtitleDisplay or its timers.
            try:
                size=label.getSize()
                width=max(100,int(size[0])+36);height=max(48,int(size[1])+16)
                desktop=getDesktop(0).size()
                offset=max(-260,min(260,int(self._subtitle_style.get("position",0) or 0)))
                x=max(0,(desktop.width()-width)//2)
                y=max(20,min(desktop.height()-height-18,desktop.height()-height-32-offset))
                inst.resize(eSize(width,height));inst.move(ePoint(x,y))
            except Exception as exc:optional_failure("player.subtitle_style_position",exc)
        except Exception as exc:
            optional_failure("player.subtitle_style_native",exc)

    def _refresh_online_subtitle_style(self):
        self._persist_subtitle_style()
        if self._online_subtitle_active and self._online_subtitle_current_text:
            # Re-render the currently visible cue through the proven native path.
            self._set_online_subtitle_text(self._online_subtitle_current_text)
        else:
            self._apply_subtitle_style_native()

    def _open_subtitle_color_menu(self):
        current=str(self._subtitle_style.get("color") or "#FFFFFF").upper()
        choices=[(("✓ " if value.upper()==current else "")+name,value) for name,value in SUBTITLE_STYLE_COLORS]
        self.session.openWithCallback(self._subtitle_color_selected,SubtitleGlassChoiceScreen,
            title="Subtitle Color",choices=choices,source_path=self._subtitle_picker_source())

    def _subtitle_color_selected(self,choice=None):
        if not choice:return
        value=str(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice or "").upper()
        valid={v.upper() for _n,v in SUBTITLE_STYLE_COLORS}
        if value not in valid:return
        self._subtitle_style["color"]=value;self._refresh_online_subtitle_style()
        self.subtitleSelection("style_color")

    def _open_subtitle_background_menu(self):
        enabled=bool(self._subtitle_style.get("background",False))
        choices=[(("✓ " if not enabled else "")+"Off • text only",False),
                 (("✓ " if enabled else "")+"Black • behind subtitle text",True)]
        self.session.openWithCallback(self._subtitle_background_selected,SubtitleGlassChoiceScreen,
            title="Subtitle Background",choices=choices,source_path=self._subtitle_picker_source())

    def _subtitle_background_selected(self,choice=None):
        if not choice:return
        self._subtitle_style["background"]=bool(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else False)
        self._refresh_online_subtitle_style()
        self.subtitleSelection("style_background")

    def _open_subtitle_position_menu(self):
        current=int(self._subtitle_style.get("position",0) or 0)
        choices=[(("✓ " if int(offset)==current else "")+name,int(offset)) for name,offset in SUBTITLE_POSITION_PRESETS]
        self.session.openWithCallback(self._subtitle_position_selected,SubtitleGlassChoiceScreen,
            title="Subtitle Position",choices=choices,source_path=self._subtitle_picker_source())

    def _subtitle_position_selected(self,choice=None):
        if not choice:return
        try:value=int(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else 0)
        except Exception:return
        self._subtitle_style["position"]=max(-260,min(260,value));self._refresh_online_subtitle_style()
        self.subtitleSelection("style_position")

    def _disable_native_subtitles(self):
        try:
            service=self.session.nav.getCurrentService()
            subtitle=service.subtitle() if service else None
            if subtitle is not None:
                window=getattr(self,"subtitle_window",None)
                instance=getattr(window,"instance",None) if window is not None else None
                if instance is not None:
                    subtitle.disableSubtitles(instance)
                else:
                    try:subtitle.disableSubtitles()
                    except TypeError:pass
            window=getattr(self,"subtitle_window",None)
            if window is not None:window.hide()
        except Exception as exc:
            optional_failure("player.disable_native_subtitles",exc)

    def _disable_online_subtitles(self):
        self._online_subtitle_active=False
        self._online_subtitle_searching=False
        self._online_subtitle_generation += 1
        try:self._online_subtitle_cancel.set()
        except Exception:pass
        self._online_subtitle_cues=[]
        self._online_subtitle_index=0
        self._online_subtitle_path=""
        try:self.online_subtitle_timer.stop()
        except Exception:pass
        try:self.online_subtitle_result_timer.stop()
        except Exception:pass
        try:
            while True:self._online_subtitle_queue.get_nowait()
        except Exception:pass
        try:self._set_online_subtitle_text("")
        except Exception:pass

    def _start_online_arabic_subtitles(self):
        if self.media_type not in ("vod","series","episode"):
            try:self.session.open(MessageBox,"Online subtitles are available for Movies and Series.",MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass
            return
        if self._online_subtitle_searching:return
        cfg=load_settings();api_key=str(cfg.get("subdl_api_key") or "").strip()
        if not api_key:
            try:self.session.open(MessageBox,"SubDL API key is not configured.",MessageBox.TYPE_ERROR,timeout=6)
            except Exception:pass
            return
        self._disable_native_subtitles()
        self._online_subtitle_searching=True
        self._online_subtitle_generation += 1
        generation=self._online_subtitle_generation
        try:self._online_subtitle_cancel.clear()
        except Exception:pass
        try:
            self["connection"].setText("SEARCHING  •  SUBDL ARABIC")
            self.show()
        except Exception:pass
        item=dict(self.item);media_type=self.media_type;name=self.name
        def worker():
            try:
                candidates,ident=search_arabic(api_key,item,media_type,name)
                if self._online_subtitle_cancel.is_set() or generation!=self._online_subtitle_generation:return
                self._online_subtitle_queue.put(("choices",(candidates,ident,generation)))
            except Exception as exc:
                if self._online_subtitle_cancel.is_set() or generation!=self._online_subtitle_generation:return
                self._online_subtitle_queue.put(("error",(str(exc),generation)))
        try:
            threading.Thread(target=worker,name="UltraStalker-SubDL",daemon=True).start()
            self.online_subtitle_result_timer.start(250,False)
        except Exception as exc:
            self._online_subtitle_searching=False
            try:self.session.open(MessageBox,"Subtitle search failed: %s"%exc,MessageBox.TYPE_ERROR,timeout=7)
            except Exception:pass

    def _drain_online_subtitle_result(self):
        if self.restored or self._closing_playback:
            try:self.online_subtitle_result_timer.stop()
            except Exception:pass
            return
        try:kind,payload=self._online_subtitle_queue.get_nowait()
        except queue.Empty:return
        if kind=="choices" and isinstance(payload,tuple) and len(payload)==3:
            candidates,ident,generation=payload
            if generation!=self._online_subtitle_generation:return
            payload=(candidates,ident)
        elif kind=="error" and isinstance(payload,tuple) and len(payload)==2:
            message,generation=payload
            if generation!=self._online_subtitle_generation:return
            payload=message
        elif kind=="downloaded" and isinstance(payload,tuple) and len(payload)==2:
            result,generation=payload
            if generation!=self._online_subtitle_generation:return
            payload=result
        self._online_subtitle_searching=False
        try:self.online_subtitle_result_timer.stop()
        except Exception:pass
        if kind=="downloaded" and isinstance(payload,dict):
            try:
                self._activate_online_subtitle(str(payload.get("path") or ""),str(payload.get("release") or "Arabic"))
                return
            except Exception as exc:
                payload="Could not load subtitle: %s"%exc
        if kind=="choices":
            candidates,ident=payload
            self._online_subtitle_candidates=(candidates,ident)
            choices=[]
            for idx,c in enumerate(candidates):
                label=str(c.get("name") or "Arabic subtitle")
                if c.get("cached_path"):label="★ "+label
                choices.append((label[:110],idx))
            try:
                source=str(getattr(self,"_poster_path","") or _cached_poster(self.item,self.name,self.media_type) or "")
                self.session.openWithCallback(
                    self._online_subtitle_choice_selected,
                    SubtitleGlassChoiceScreen,
                    title="Arabic subtitles • %d found"%len(choices),
                    choices=choices,
                    source_path=source,
                )
                return
            except Exception as exc:
                payload="Could not open subtitle results: %s"%exc
        try:
            self["connection"].setText("PLAYING  •  NO ARABIC SUBTITLE")
            self.session.open(MessageBox,str(payload or "No Arabic subtitle found"),MessageBox.TYPE_INFO,timeout=8)
        except Exception:pass

    def _online_subtitle_choice_selected(self,choice=None):
        if not choice:return
        try:idx=int(choice[1])
        except Exception:return
        try:
            candidates,ident=self._online_subtitle_candidates
            candidate=candidates[idx]
        except Exception:return
        self._online_subtitle_searching=True
        self._online_subtitle_generation += 1
        generation=self._online_subtitle_generation
        try:self._online_subtitle_cancel.clear()
        except Exception:pass
        try:
            self["connection"].setText("DOWNLOADING  •  ARABIC SUBTITLE")
            self.show()
        except Exception:pass
        def worker():
            try:
                result=download_candidate(candidate,ident)
                if self._online_subtitle_cancel.is_set() or generation!=self._online_subtitle_generation:return
                self._online_subtitle_queue.put(("downloaded",(result,generation)))
            except Exception as exc:
                if self._online_subtitle_cancel.is_set() or generation!=self._online_subtitle_generation:return
                self._online_subtitle_queue.put(("error",(str(exc),generation)))
        threading.Thread(target=worker,name="UltraStalker-SubDL-Download",daemon=True).start()
        self.online_subtitle_result_timer.start(250,False)

    def _subtitle_sync_file(self):
        path=str(self._online_subtitle_path or "")
        return (path+".sync.json") if path else ""

    def _load_subtitle_sync(self):
        self._online_subtitle_offset_ms=0;self._online_subtitle_scale=1.0
        path=self._subtitle_sync_file()
        if not path:return
        try:
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            self._online_subtitle_offset_ms=max(-300000,min(300000,int(data.get("offset_ms") or 0)))
            scale=float(data.get("scale") or 1.0)
            self._online_subtitle_scale=scale if 0.94<=scale<=1.06 else 1.0
        except Exception:pass

    def _save_subtitle_sync(self):
        path=self._subtitle_sync_file()
        if not path:return
        try:
            temp=path+".tmp"
            with open(temp,"w",encoding="utf-8") as h:
                json.dump({"offset_ms":int(self._online_subtitle_offset_ms),"scale":float(self._online_subtitle_scale)},h,separators=(",",":"))
                h.flush();os.fsync(h.fileno())
            os.replace(temp,path)
        except Exception as exc:optional_failure("player.subtitle_sync_save",exc)

    def _adjust_subtitle_delay(self,delta_ms):
        if not self._online_subtitle_active:return
        self._online_subtitle_offset_ms=max(-300000,min(300000,int(self._online_subtitle_offset_ms)+int(delta_ms)))
        self._save_subtitle_sync()
        try:
            self.session.open(MessageBox,"Subtitle delay: %+.1f sec"%(self._online_subtitle_offset_ms/1000.0),MessageBox.TYPE_INFO,timeout=2)
        except Exception:pass

    def _auto_sync_subtitle(self):
        """Safe lightweight drift correction without an always-running AI model.

        Detect only the two common 23.976<->25fps timing drifts when evidence is
        strong. Constant offsets stay user-adjustable in 0.5s steps.
        """
        if not self._online_subtitle_active or not self._online_subtitle_cues:return
        try:
            service=self.session.nav.getCurrentService();seek=service.seek() if service else None
            length=seek.getLength() if seek else None
            duration_ms=int(length[1] or 0)//90 if length and not length[0] else 0
        except Exception:duration_ms=0
        last_ms=int(self._online_subtitle_cues[-1][1] or 0)
        if duration_ms<=20*60*1000 or last_ms<=15*60*1000:
            try:self.session.open(MessageBox,"Auto Sync needs a stable movie duration. Use delay +/- for this subtitle.",MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass
            return
        ratio=float(duration_ms)/float(last_ms)
        candidates=(25.0/23.976,23.976/25.0)
        best=min(candidates,key=lambda x:abs(x-ratio))
        # Very conservative: only apply when measured ratio is close to a known
        # PAL/cinema drift and subtitle coverage reaches most of the movie.
        if abs(best-ratio)<=0.008 and last_ms>=int(duration_ms*0.88):
            self._online_subtitle_scale=float(best)
            self._save_subtitle_sync()
            try:self.session.open(MessageBox,"Auto Sync applied • timing scale %.5f"%best,MessageBox.TYPE_INFO,timeout=4)
            except Exception:pass
        else:
            try:self.session.open(MessageBox,"No safe automatic drift detected. Use subtitle delay +/- for this release.",MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass

    def _activate_online_subtitle(self,path,release_name="Arabic"):
        cues=parse_srt(path)
        if not cues:
            raise RuntimeError("subtitle file contains no readable cues")
        self._disable_native_subtitles()
        self._online_subtitle_path=path
        self._online_subtitle_cues=cues
        self._online_subtitle_index=0
        self._load_subtitle_sync()
        self._online_subtitle_active=True
        self._subtitle_user_override=True
        try:self["online_subtitle"].hide()
        except Exception:pass
        self._set_online_subtitle_text("")
        self.online_subtitle_timer.start(80,True)
        try:self["connection"].setText("PLAYING  •  ARABIC SUBTITLES")
        except Exception:pass
        return True

    def _online_subtitle_tick(self):
        if not self._online_subtitle_active or self.restored or self._closing_playback:
            try:self["online_subtitle"].setText("")
            except Exception:pass
            return
        try:
            service=self.session.nav.getCurrentService()
            seek=service.seek() if service else None
            pos=seek.getPlayPosition() if seek else None
            if not pos or pos[0]:
                return
            ms=max(0,int(pos[1] or 0)//90)
            scale=float(self._online_subtitle_scale or 1.0)
            timeline_ms=max(0,int((ms-int(self._online_subtitle_offset_ms or 0))/scale))
        except Exception:
            return
        ms=timeline_ms
        cues=self._online_subtitle_cues
        idx=max(0,min(int(self._online_subtitle_index or 0),max(0,len(cues)-1)))
        while idx<len(cues) and cues[idx][1] < ms:
            idx+=1
        while idx>0 and cues[idx-1][0] > ms:
            idx-=1
        self._online_subtitle_index=idx
        text=""
        if idx<len(cues):
            start,end,body=cues[idx]
            if start<=ms<=end:
                text=body
        try:self._set_online_subtitle_text(text)
        except Exception as exc:optional_failure("player.online_subtitle_tick",exc)
        # Wake near the next cue boundary instead of polling the decoder forever
        # every 180/300ms. This keeps subtitle timing responsive without making
        # Enigma2's main loop do constant seek() calls.
        try:
            wait=700
            if idx<len(cues):
                start,end,_body=cues[idx]
                boundary=end if start<=ms<=end else start
                wait=max(90,min(700,int(abs(boundary-ms))))
            self.online_subtitle_timer.start(wait,True)
        except Exception:pass

    def _enforce_default_subtitles_off(self):
        """Block cached/automatic subtitles until the user explicitly opens the selector."""
        if self.restored or self._subtitle_user_override:
            return
        try:
            # A truthy sentinel prevents InfoBarSubtitleSupport.__updatedInfo from
            # auto-enabling getCachedSubtitle() again on the next metadata event.
            self.selected_subtitle = (0, 0, 0, 0)
            service = self.session.nav.getCurrentService()
            subtitle = service.subtitle() if service else None
            if subtitle is not None:
                window = getattr(self, "subtitle_window", None)
                instance = getattr(window, "instance", None) if window is not None else None
                if instance is not None:
                    subtitle.disableSubtitles(instance)
                else:
                    try:
                        subtitle.disableSubtitles()
                    except TypeError:
                        pass
            window = getattr(self, "subtitle_window", None)
            if window is not None:
                window.hide()
        except Exception as exc:
            optional_failure("player.default_subtitles_off", exc)
        self._subtitle_disable_attempts += 1
        if self._subtitle_disable_attempts < 4 and not self._subtitle_user_override:
            try:
                self.subtitle_default_timer.start(900, True)
            except Exception as exc:
                optional_failure("player", exc)

    def _schedule_startup_guard(self, reason):
        """Debounce stale EOF/tune-failed events from ServiceApp/ExtePlayer.

        VOD and episodes sometimes decode a second of video and then emit a stale
        startup event for the previous engine/service. Never restart immediately;
        verify the active reference and advancing play position first.
        """
        if self.media_type not in ("vod", "series", "episode", "catchup"):
            return False
        self._early_eof_pending = True
        self._startup_guard_reason = str(reason or "startup interruption")
        self._startup_guard_checks = 0
        try:
            self.eof_guard_timer.stop()
            self.eof_guard_timer.start(1100, True)
            return True
        except Exception as exc:
            optional_failure("player", exc)
            return False

    def _category_label(self):
        return {
            "itv": "LIVE",
            "live": "LIVE",
            "vod": "MOVIE",
            "series": "SERIES",
            "episode": "EPISODE",
            "catchup": "CATCH-UP",
        }.get(self.media_type, self.media_type.upper()[:10])

    def playStream(self, servicetype, streamurl):
        runtime_breadcrumb("player_start_request",media_type=self.media_type,engine=int(servicetype or 0))
        if self.restored or self._closing_playback:
            return
        if not streamurl:
            self._final_failure("Empty stream URL")
            return

        try:
            service_value = int(servicetype)
        except Exception:
            service_value = 4097
        self.servicetype = service_value
        self._play_generation += 1
        self.started = False
        self.failed = False
        self._early_eof_pending = False
        self._startup_guard_checks = 0
        self._startup_guard_last_position = -1
        self._startup_guard_reason = ""
        self._startup_guard_until = time.time() + (8.0 if self.media_type in ("vod", "series", "episode", "catchup") else 0.0)
        self._video_guard_misses = 0
        self._video_guard_last_engine = int(service_value)
        self._engine_video_confirmed = False
        self._subtitle_user_override = False
        self._subtitle_disable_attempts = 0
        try:
            self.subtitle_default_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        try:
            self.eof_guard_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        self.start_attempt = time.time()
        self["engine"].setText(_engine_label(self.servicetype))
        self["connection"].setText("CONNECTING  •  %s" % _engine_label(self.servicetype))
        self["extension"].setText("STREAM")

        try:
            self.reference = eServiceReference(self.servicetype, 0, str(streamurl))
            self.reference.setName(self.name)
            try:self._owned_reference_string=self.reference.toString()
            except Exception:self._owned_reference_string=""
            # Match the proven reference implementation lifecycle: replace the current service
            # directly from inside the player screen. Calling stopService first
            # can emit an EOF for the old service and falsely trigger fallback.
            result = self.session.nav.playService(self.reference)
            try: self._owned_external_pids.update(set(_matching_external_player_pids(self.streamurl))-set(self._external_player_baseline))
            except Exception as exc: optional_failure("player.capture_external",exc)
            if result is False:
                raise RuntimeError("service engine rejected the stream")
            self.startup_timer.stop()
            self.startup_timer.start(12000, True)
        except Exception as exc:
            self._try_next_engine("engine %s rejected stream: %s" % (self.servicetype, exc))

    def _service_started(self):
        if self.restored or self._closing_playback:
            return
        already_started = bool(self.started)
        self.started = True
        if not already_started or not self._watch_started_at:
            self._watch_started_at = time.time()
        try:
            self.startup_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        self["connection"].setText("PLAYING  •  %s" % _engine_label(self.servicetype))
        runtime_breadcrumb("player_started",media_type=self.media_type,engine=int(self.servicetype or 0))
        try:
            self._owned_external_pids.update(set(_all_external_player_pids())-set(self._external_player_baseline))
        except Exception as exc:optional_failure("player.capture_external_started",exc)
        self._last_network_activity = time.monotonic()
        try:
            self.memory_fuse_timer.stop();self.memory_fuse_timer.start(5000,False)
        except Exception as exc:optional_failure("player.silent_guard",exc)
        try:
            self.stable_timer.stop(); self.stable_timer.start(30000, True)
        except Exception as exc:
            optional_failure("player", exc)
        if not already_started:
            try:
                self.subtitle_default_timer.stop(); self.subtitle_default_timer.start(450, True)
            except Exception as exc:
                optional_failure("player", exc)
        self["state"].setText("PLAYING")
        # Last Viewed is intentionally separate from Resume. Touch recency as
        # soon as Enigma2 confirms real playback, while preserving any existing
        # resume point. Progress itself is still saved only after the 45s gate.
        if self.media_type in ("vod","series","episode","catchup"):
            try:
                profile={"portal":self.item.get("_portal", ""),"mac":self.item.get("_mac", "")}
                history_item=self.item.get("_history_item") if isinstance(self.item.get("_history_item"),dict) else self.item
                touch_recently_played(profile,self.media_type,history_item)
            except Exception as exc:optional_failure("player",exc)
        self._update_stream_info()
        try:
            self["statusicon"].setPixmapNum(0)
        except Exception as exc:
            optional_failure("player", exc)
        if self.media_type in ("vod", "series", "episode", "catchup"):
            try:
                cfg=load_settings()
                interval=(int(cfg.get("progress_save_seconds",10)) if cfg.get("crash_safe_progress",True) else 10)*1000
                self.progress_timer.stop(); self.progress_timer.start(interval, False)
                self.crystal_timer.stop(); self.crystal_timer.start(500, False)
            except Exception as exc:
                optional_failure("player", exc)
            if not self.resume_prompted:
                self.resume_prompted=True

    def _suppress_service_events(self, seconds=1.25):
        self._event_suppress_until=max(float(getattr(self,"_event_suppress_until",0.0) or 0.0),time.time()+max(0.0,float(seconds or 0.0)))

    def _event_matches_current_reference(self):
        # stopService() intentionally emits EOF/TuneFailed on several OE-A images.
        # Ignore those stale events briefly; the startup watchdog still catches a
        # genuinely failed replacement service.
        if time.time() < float(getattr(self,"_event_suppress_until",0.0) or 0.0):
            return False
        try:
            current = self.session.nav.getCurrentlyPlayingServiceReference()
            if current is None or self.reference is None:
                return True
            return current.toString() == self.reference.toString()
        except Exception:
            return True

    def _service_failed(self):
        if self.restored or self._closing_playback:
            return
        if not self._event_matches_current_reference():
            return
        elapsed = time.time() - self.start_attempt if self.start_attempt else 999
        if self.media_type in ("vod", "series", "episode", "catchup") and elapsed < 8.0:
            self["connection"].setText("PLAYING  •  verifying startup" if self.started else "CONNECTING  •  verifying startup")
            self._schedule_startup_guard("transient tune failed")
            return
        if self.started and self.media_type in ("itv", "live"):
            if self._handle_runtime_interruption("tune failed during playback"):
                return
        self._try_next_engine("tune failed")

    def _service_eof(self):
        if self.restored or self._closing_playback:
            return
        if not self._event_matches_current_reference():
            return
        if self._resume_target and time.time() < self._resume_grace_until:
            try:
                self.eof_guard_timer.stop(); self.eof_guard_timer.start(1800, True)
            except Exception as exc:
                optional_failure("player", exc)
            return
        elapsed = time.time() - self.start_attempt if self.start_attempt else 999
        if self.media_type in ("vod", "series", "episode", "catchup") and elapsed < 8.0:
            self["connection"].setText("PLAYING  •  stabilizing stream" if self.started else "CONNECTING  •  stabilizing stream")
            self._schedule_startup_guard("transient early EOF")
            return
        if not self.started:
            self._try_next_engine("stream ended before playback started")
            return
        if self.media_type in ("itv", "live"):
            if self._handle_runtime_interruption("stream ended during playback"):
                return
        if self.media_type in ("vod", "series", "episode", "catchup"):
            # Natural EOF is different from the user pressing BACK. Freeze the
            # periodic writer so it cannot overwrite the completed state while
            # the 10-second Next Episode prompt is on screen.
            try: self.progress_timer.stop()
            except Exception as exc: optional_failure("player", exc)
            self._save_history_progress(force=True, completed_override=True)
            next_item = self.item.get("_next_episode_item") if self.media_type == "episode" else None
            global NEXT_EPISODE_AUTOPLAY_SESSION
            if isinstance(next_item, dict) and next_item and not self._next_episode_prompted and NEXT_EPISODE_AUTOPLAY_SESSION:
                self._next_episode_prompted = True
                try:
                    self.session.openWithCallback(self._next_episode_answer, UltraStalkerNextEpisodePrompt, next_item, int(load_settings().get("next_episode_countdown",10)))
                    return
                except Exception as exc:
                    optional_failure("player.next_episode_prompt", exc)
            self._close_player(save_progress=False)

    def _mark_stream_stable(self):
        if self.restored or not self.started:
            return
        self._runtime_reconnects = 0
        self._recovery_attempts = 0

    def _handle_runtime_interruption(self, reason):
        if self.restored or self._closing_playback:return False
        try:self.stable_timer.stop()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        # Never tear down/recreate a decoder automatically. Broadcom native
        # allocations were observed accumulating across repeated Stop/Play cycles.
        self.started=False
        self["connection"].setText("STREAM INTERRUPTED  •  press 0 to restart")
        runtime_breadcrumb("player_interruption_no_restart",media_type=self.media_type,reason=str(reason or ""))
        return True


    def _verify_early_eof(self):
        if not self._early_eof_pending or self.restored or self.failed:
            return
        now = time.time()
        try:
            service = self.session.nav.getCurrentService()
            seekable = service.seek() if service else None
            pos = seekable.getPlayPosition() if seekable else None
            position = abs(int(pos[1])) if pos and not pos[0] else -1
        except Exception:
            position = -1
        ref_ok = self._event_matches_current_reference()
        previous = self._startup_guard_last_position
        self._startup_guard_last_position = position
        self._startup_guard_checks += 1
        # Advancing decoded position is definitive proof that playback survived
        # the stale event; keep the current engine and do not restart.
        if ref_ok and position >= 0 and previous >= 0 and position > previous + 9000:
            self._early_eof_pending = False
            self["connection"].setText("PLAYING  •  %s" % _engine_label(self.servicetype))
            return
        if ref_ok and self.started and position > 45000:
            self._early_eof_pending = False
            self["connection"].setText("PLAYING  •  %s" % _engine_label(self.servicetype))
            return
        # Never bounce engines during the protected startup window merely because
        # one probe cannot read a seek position yet. External players often expose
        # seek() a few seconds after video begins.
        if ref_ok and now < self._startup_guard_until and self._startup_guard_checks < 7:
            try:
                self.eof_guard_timer.start(1000, True)
            except Exception as exc:
                optional_failure("player", exc)
            return
        self._early_eof_pending = False
        self._try_next_engine(self._startup_guard_reason or "stream stopped during startup verification")

    def _startup_timeout(self):
        if self.restored or self._closing_playback:
            return
        if not self.started:
            self._try_next_engine("startup timeout")

    def _try_next_engine(self, reason):
        if self.restored or self._closing_playback or self.failed:return
        try:self.startup_timer.stop()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        # Engine and service are deterministic. Startup failure is surfaced; it
        # is never converted into a hidden Stop/Play/URL-refresh loop.
        self._final_failure(reason)


    def _begin_smart_recovery(self, reason):
        # Disabled by design on this receiver: refreshing a link previously
        # recreated the native decoder and contributed to unbounded RSS growth.
        return False


    def _cancel_smart_recovery(self):
        self._recovery_inflight = False
        cancel_event = getattr(self, "_recovery_cancel", None)
        if cancel_event is not None:
            try: cancel_event.set()
            except Exception as exc: optional_failure("player.recovery_cancel", exc)
        lock = getattr(self, "_recovery_client_lock", None)
        client = None
        if lock is not None:
            try:
                with lock:
                    client = getattr(self, "_recovery_client", None)
            except Exception as exc:
                optional_failure("player.recovery_cancel", exc)
        if client is not None:
            try: client.cancel_pending_requests()
            except Exception as exc: optional_failure("player.recovery_cancel", exc)

    def _drain_recovery_result(self):
        try:self.recovery_timer.stop()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        self._recovery_inflight=False
        while True:
            try:self._recovery_queue.get_nowait()
            except queue.Empty:break


    def _final_failure(self, reason):
        if self.failed:
            return
        self.failed = True
        self["connection"].setText("PLAYBACK FAILED")
        text = "The stream could not start with the configured Enigma2 engine.\n\n%s\n\nTried: %s" % (
            reason,
            ", ".join(str(x) for x in self.engines),
        )
        try:
            self.session.openWithCallback(lambda answer=None: self.back(), MessageBox, text, MessageBox.TYPE_ERROR, timeout=10)
        except Exception:
            self.back()

    def restartStream(self):
        self._save_history_progress()
        self.playStream(self.servicetype, self.streamurl)

    def toggleStreamType(self):
        try:
            configured=int(load_settings().get("service_type",self.servicetype))
        except Exception:
            configured=self.servicetype
        self["connection"].setText("ENGINE LOCKED  •  %s  •  change it in Settings" % _engine_label(configured))
        try:self.doShow()
        except Exception as exc:optional_failure("player.optional_guard",exc)


    def _remember_manual_title_engine(self, chosen):
        # Engine selection is globally locked to Settings. Kept as a no-op for
        # compatibility with older callbacks that may still reference it.
        return

    def _engine_choice_answer(self, answer):
        try: chosen=int(load_settings().get("service_type",self.servicetype))
        except (TypeError,ValueError): chosen=int(self.servicetype or 4097)
        if chosen not in (1,4097,5001,5002,8193): chosen=4097
        self.engines=[chosen];self.engine_index=0;self.servicetype=chosen
        self["connection"].setText("ENGINE LOCKED  •  %s  •  change it in Settings" % _engine_label(chosen))
        self.playStream(chosen,self.streamurl)
        try:self.doShow()
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _infobar_is_shown(self):
        try:
            return getattr(self,"_IPTVInfoBarShowHide__state",IPTVInfoBarShowHide.STATE_HIDDEN)==IPTVInfoBarShowHide.STATE_SHOWN
        except Exception:
            return False

    def OKButton(self):
        if self.media_type in ("itv","live") and self._zap_channels:
            # First OK is always reserved for the InfoBar. Only a subsequent OK
            # while that InfoBar is actually visible opens the channel drawer.
            if (not self._zap_infobar_armed) or (not self._infobar_is_shown()):
                self._zap_infobar_armed=True
                try:self.doShow()
                except Exception:IPTVInfoBarShowHide.OkPressed(self)
                return
            self._zap_infobar_armed=False
            self._openZapList()
            return
        IPTVInfoBarShowHide.OkPressed(self)

    def _openZapList(self):
        if self._zap_open or self.media_type not in ("itv","live") or not self._zap_channels:
            return
        self._zap_open=True
        try:self.hideTimer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        channels=list(self._zap_channels)
        # Resolve the drawer palette from the actual current channel row, not the
        # stale player bootstrap item. This also replaces the generic monitor
        # placeholder with the cached channel picon whenever it is available.
        current_row=None
        try:
            if 0<=int(self._zap_index)<len(channels) and isinstance(channels[int(self._zap_index)],dict):
                current_row=channels[int(self._zap_index)]
        except Exception:
            current_row=None
        picon=_cached_poster(current_row or self.item,self.name,self.media_type)
        if self._zap_total>len(channels):channels.extend([None]*(self._zap_total-len(channels)))
        try:
            self.session.openWithCallback(
                self._onZapSelected, UltraStalkerLiveZapList, channels, self._zap_index,
                self._zap_title, picon, self._zap_page_loader, self._zap_page_size, self._zap_total
            )
        except Exception as exc:
            self._zap_open=False
            optional_failure("player.open_zap_list",exc)
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)

    def _onZapSelected(self,result=None):
        self._zap_open=False
        self._zap_infobar_armed=False
        if not result:
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        try:index,item=result
        except Exception:return
        self._switchChannel(index,item)

    def _switchChannel(self,index,item):
        if not isinstance(item,dict):
            return
        client=self.item.get("_live_client_ref")
        if client is None:
            self["connection"].setText("CHANNEL SWITCH UNAVAILABLE")
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        if self._zap_switch_inflight:
            self["connection"].setText("CHANNEL SWITCH IN PROGRESS...")
            return
        self._zap_switch_generation += 1
        generation=self._zap_switch_generation
        self._zap_switch_inflight=True
        self._zap_switch_started_at=time.monotonic()
        self["connection"].setText("SWITCHING CHANNEL...")
        def worker():
            try:
                url=client.create_link(item,"itv")
                self._zap_switch_queue.put((generation,index,dict(item),str(url or ""),None))
            except Exception as exc:
                self._zap_switch_queue.put((generation,index,dict(item),"",exc))
        try:
            threading.Thread(target=worker,name="UltraLiveZapResolve",daemon=True).start()
            self.zap_switch_timer.start(120,False)
        except Exception as exc:
            self._zap_switch_inflight=False
            optional_failure("player.zap_worker_start",exc)
            self["connection"].setText("CHANNEL SWITCH FAILED")

    def _drain_zap_switch_result(self):
        if self.restored or self._closing_playback:
            try:self.zap_switch_timer.stop()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        try:
            generation,index,item,url,error=self._zap_switch_queue.get_nowait()
        except queue.Empty:
            # A pathological portal/library call must not leave channel switching
            # permanently locked. Invalidate the generation; a late worker result
            # will be ignored safely.
            try:timeout=max(8.0,float(load_settings().get("timeout",10) or 10)+5.0)
            except Exception:timeout=15.0
            if self._zap_switch_inflight and self._zap_switch_started_at and time.monotonic()-self._zap_switch_started_at>timeout:
                self._zap_switch_generation+=1;self._zap_switch_inflight=False;self._zap_switch_started_at=0.0
                try:self.zap_switch_timer.stop()
                except Exception as exc:optional_failure("player.optional_guard",exc)
                self["connection"].setText("CHANNEL SWITCH TIMED OUT")
                try:self.doShow()
                except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        if generation != self._zap_switch_generation:
            return
        self._zap_switch_inflight=False
        self._zap_switch_started_at=0.0
        try:self.zap_switch_timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        if error or not url:
            self["connection"].setText("CHANNEL SWITCH FAILED")
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return

        old_url=self.streamurl
        self._suppress_service_events(1.25)
        try:force_session_silence(self.session,old_url,force=True,stop_native=True,exclude_pids=self._external_player_baseline,owned_pids=self._owned_external_pids)
        except Exception as exc:optional_failure("player.zap_stop_owned",exc)
        self._owned_external_pids.clear()

        self._zap_index=max(0,int(index))
        self.item.update(item)
        self.name=_clean_live_name(str(item.get("name") or item.get("title") or "Live channel"))
        self.streamurl=url
        self["channel"].setText(self.name[:120])
        try:self["now"].setText(self._now_text());self["next"].setText(self._next_text());self["category"].setText(self._category_label())
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            engine=int(load_settings().get("service_type",self.servicetype))
        except (TypeError,ValueError):
            engine=int(self.servicetype or 4097)
        if engine not in (1,4097,5001,5002,8193):engine=4097
        self.engines=[engine];self.engine_index=0;self.servicetype=engine
        self.playStream(engine,url)
        try:self.doShow()
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _cancel_pending_resume_for_manual_seek(self):
        """A viewer seek permanently cancels any pending automatic Resume seek."""
        self._resume_target=0
        self._resume_grace_until=0.0
        self.resume_prompted=True
        try:self.resume_timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:self.resume_verify_timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:self._release_resume_shield()
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _play_state_changed(self, state):
        try:
            value = state[3] or ""
        except Exception:
            value = ""
        if self.media_type in ("vod","series","episode","catchup"):
            if value.startswith(">>") or value.startswith("<<") or value.startswith("/"):
                # Do not let a delayed Resume seek wake up after the viewer
                # manually rewinds/forwards.  Also avoid saving the transient
                # end-of-file sentinel some ServiceApp builds report during
                # trick-play state changes.
                self._cancel_pending_resume_for_manual_seek()
            elif value in ("||", ">"):
                self._save_history_progress(force=True)
        speed = ""
        icon = 6
        if value == ">":
            icon = 0
        elif value == "||":
            icon = 1
        elif value == "END":
            icon = 2
        elif value.startswith(">>"):
            icon = 3
            parts = value.split()
            speed = parts[1] if len(parts) > 1 else value[2:]
        elif value.startswith("<<"):
            icon = 4
            parts = value.split()
            speed = parts[1] if len(parts) > 1 else value[2:]
        elif value.startswith("/"):
            icon = 5
            speed = value
        label = value
        if value == ">":
            label = "PLAYING"
        elif value == "||":
            label = "PAUSED"
            if load_settings().get("crash_safe_progress",True):
                self._save_history_progress(force=True)
        elif value == "END":
            label = "STOPPED"
        elif value.startswith(">>"):
            label = "FORWARD"
        elif value.startswith("<<"):
            label = "REWIND"
        elif value.startswith("/"):
            label = "SLOW"
        self["state"].setText(label)
        self["speed"].setText(speed)
        try:
            self["statusicon"].setPixmapNum(icon)
        except Exception as exc:
            optional_failure("player", exc)
    def _schedule_resume_probe(self):
        attempt=max(0,int(self._resume_verify_attempts or 0))
        delay=min(1200,50*(2**min(attempt,4)))
        self.resume_verify_timer.stop(); self.resume_verify_timer.start(delay,True)

    def _perform_resume_seek(self):
        """Apply the saved bookmark once, but only after the decoder is truly seekable.

        ServiceApp/ExtePlayer3 can expose service.seek() at evStart before
        seekTo() is actually honoured.  We therefore require a valid length and
        play position, plus a short decoder-settle window, before issuing the
        single absolute seek.
        """
        if not self._resume_target or self.restored or self._closing_playback:return
        if self._resume_seek_count>0:return
        try:
            # Give ServiceApp a moment after evStart to publish a usable
            # iSeekableService. Readiness probes do not count as seek attempts.
            if self._watch_started_at and (time.time()-self._watch_started_at)<0.55:
                self._resume_verify_attempts+=1;self._schedule_resume_probe();return
            service=self.session.nav.getCurrentService();seekable=service.seek() if service else None
            if seekable is None:
                self._resume_verify_attempts+=1
                if self._resume_verify_attempts<14:self._schedule_resume_probe()
                else:self._resume_target=0;self._release_resume_shield()
                return
            native_probe=getattr(self,"isCurrentlySeekable",None)
            if callable(native_probe):
                try:
                    state=native_probe()
                    if isinstance(state,(tuple,list)):
                        state=(not state[0]) and (len(state)<2 or bool(state[1]))
                    if not bool(state):
                        self._resume_verify_attempts+=1;self._schedule_resume_probe();return
                except Exception as exc:optional_failure("player.optional_guard",exc)
            length=seekable.getLength();position=seekable.getPlayPosition()
            length_ok=bool(length and not length[0] and int(length[1] or 0)>30*90000)
            pos_ok=bool(position and not position[0] and int(position[1] or 0)>=0)
            if not (length_ok and pos_ok):
                self._resume_verify_attempts+=1
                if self._resume_verify_attempts<14:self._schedule_resume_probe()
                else:self._resume_target=0;self._release_resume_shield();self["connection"].setText("PLAYING  •  resume unavailable")
                return
            target=min(int(self._resume_target),max(0,int(length[1])-10*90000))
            result=seekable.seekTo(target)
            # Enigma2 implementations commonly return None/0 on success.  What
            # matters is that the command was issued after verified readiness.
            self._resume_seek_count=1
            self._last_progress_position=target
            self["connection"].setText("RESUMING  •  %d:%02d"%(target//90000//60,target//90000%60))
            self.resume_verify_timer.stop();self.resume_verify_timer.start(450,True)
        except Exception as exc:
            optional_failure("player.resume_seek",exc)
            self._resume_verify_attempts+=1
            if self._resume_seek_count==0 and self._resume_verify_attempts<14:self._schedule_resume_probe()
            else:
                self._resume_target=0;self._release_resume_shield()

    def _verify_resume_position(self):
        if not self._resume_target or self.restored or self._closing_playback:return
        if self._resume_seek_count==0:
            self._perform_resume_seek();return
        try:
            service=self.session.nav.getCurrentService();seekable=service.seek() if service else None
            pos=seekable.getPlayPosition() if seekable else None
            current=max(0,int(pos[1])) if pos and not pos[0] else 0
        except Exception as exc:
            optional_failure("player.resume_verify",exc);current=0
        tolerance=7*90000
        if current and abs(int(current)-int(self._resume_target))<=tolerance:
            self._last_progress_position=current
            self._resume_target=0;self._resume_grace_until=0.0
            self._release_resume_shield();self["connection"].setText("PLAYING  •  %s"%_engine_label(self.servicetype));return
        self._resume_verify_cycles+=1
        if self._resume_verify_cycles<7:
            self.resume_verify_timer.stop();self.resume_verify_timer.start(350,True);return
        # One actual automatic seek only. If the engine ignored it, fail cleanly
        # rather than issuing a late retry that could override a manual seek.
        self._resume_target=0;self._resume_grace_until=0.0;self._release_resume_shield()
        self["connection"].setText("PLAYING  •  resume unavailable")

    def _native_resume_updated(self):
        """Resume through the native CueSheet-style flow.

        evUpdatedInfo is the trigger; once the current service exposes a seek
        interface, read the stored absolute PTS bookmark and issue one seekTo().
        No delayed retry/verification loop is allowed to move playback again.
        """
        if self.restored or self._closing_playback or self.media_type not in ("vod","series","episode","catchup"):
            return
        if getattr(self,"_native_resume_started",False):
            return
        bookmark=self._resume_bookmark()
        if not bookmark:
            self._native_resume_started=True
            return
        try:
            service=self.session.nav.getCurrentService()
            if service is None:return
            seekable=service.seek()
            if seekable is None:return
            length=seekable.getLength() or (None,0)
            total=abs(int(length[1] or 0)) if len(length)>1 else 0
            if bookmark <= 900000:return
            if total and bookmark >= total-900000:
                self._native_resume_started=True
                return
            # Mark immediately before issuing the one and only automatic seek.
            self._native_resume_started=True
            self.resume_prompted=True
            seekable.seekTo(int(bookmark))
            self._last_progress_position=int(bookmark)
            self["connection"].setText("PLAYING  •  RESUMED %d:%02d:%02d" % (bookmark//90000//3600,(bookmark//90000%3600)//60,bookmark//90000%60))
        except Exception as exc:
            optional_failure("player.native_resume",exc)

    def _reference_resume(self):
        """Apply the saved stable-ID bookmark using the proven native flow."""
        if self.restored or self._closing_playback or self.resume_prompted:
            return
        self.resume_prompted = True
        bookmark = self._resume_bookmark()
        if not bookmark:
            return
        try:
            service = self.session.nav.getCurrentService()
            seekable = service.seek() if service else None
            if seekable is None:
                return
            length_result = seekable.getLength()
            length = abs(int(length_result[1])) if length_result and not length_result[0] else 0
        except Exception as exc:
            optional_failure("player.reference_resume_probe", exc)
            return
        if length and bookmark >= length - (10 * 90000):
            return
        behavior = str(load_settings().get("resume_behavior", "always") or "always").lower()
        if behavior == "start":
            return
        if behavior == "ask":
            seconds = int(bookmark // 90000)
            message = "Resume playback from %d:%02d:%02d?" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)
            try:
                self.session.openWithCallback(lambda answer: self._reference_resume_answer(answer, bookmark), MessageBox, message, MessageBox.TYPE_YESNO, timeout=10)
                return
            except Exception as exc:
                optional_failure("player.reference_resume_prompt", exc)
        self._reference_resume_answer(True, bookmark)

    def _reference_resume_answer(self, answer, position):
        if not answer or self.restored or self._closing_playback:
            return
        try:
            service = self.session.nav.getCurrentService()
            seekable = service.seek() if service else None
            if seekable is not None:
                seekable.seekTo(int(position))
                self._last_progress_position = int(position)
                self._resume_seek_count = 1
                self["connection"].setText("PLAYING  •  RESUMED %d:%02d" % (int(position)//90000//60, int(position)//90000%60))
        except Exception as exc:
            optional_failure("player.reference_resume_seek", exc)

    def _periodic_progress_save(self):
        if self.restored or self.media_type not in ("vod", "series", "episode", "catchup"):
            return
        self._save_history_progress()

    def _update_progress_crystal(self):
        if self.restored or self.media_type not in ("vod","series","episode","catchup"):
            try:
                self["progress_crystal"].hide(); self["progress_neon"].hide(); self["skip_hint"].setText(""); self["watched_progress"].setValue(0); self["watched_progress_glow"].setValue(0)
            except Exception as exc:
                optional_failure("player.crystal_hide", exc)
            return
        position,duration=self._service_progress_snapshot()
        if duration <= 0:
            self._update_skip_hint(position, duration)
            return
        ratio=max(0.0,min(1.0,float(position)/float(duration)))
        try:
            value=max(0,min(100,int(round(ratio*100.0))))
            self["watched_progress_glow"].setValue(0)
            self["watched_progress"].setValue(0)
            # Only swap the pixmap when the integer percentage changes. This keeps the
            # one-second timer cheap on the receiver while preserving smooth visual progress.
            if value != self._progress_neon_value:
                hx=str(self._adaptive_accent_neon or "#55b9ff").lstrip("#")
                path=os.path.join(os.path.join(PERSISTENT_GENERATED_DIR,"progress_neon239"),"%s_%03d.png"%(hx,value))
                if os.path.isfile(path) and self["progress_neon"].instance is not None:
                    self["progress_neon"].instance.setPixmapFromFile(path);self["progress_neon"].show();self._progress_neon_value=value
                else:
                    _schedule_progress_neon_frame(self._adaptive_accent_neon,value)
        except Exception as exc:optional_failure("player.watched_progress",exc)
        try:self["progress_crystal"].hide()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        self._update_skip_hint(position, duration)

    def _update_skip_hint(self, position, duration):
        # 1/3 are pure relative seek. Skip Intro/Credits no longer exist.
        try:self["skip_hint"].setText("")
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _seek_relative_seconds(self, seconds):
        """Native Enigma2 relative seek only: 1=-10s, 3=+10s."""
        seconds = int(seconds or 0)
        if not seconds:
            return False
        # Debounce key-repeat bursts. ServiceApp/exteplayer3 can stall when several
        # relative seeks are queued before the previous demux seek has settled.
        now=time.monotonic()
        if now-float(getattr(self,"_last_manual_seek_at",0.0) or 0.0) < 0.18:
            return False
        self._last_manual_seek_at=now
        self._cancel_pending_resume_for_manual_seek()
        pts = abs(int(seconds * 90000))
        try:
            service=self.session.nav.getCurrentService()
            seekable=service.seek() if service else None
            if seekable is None:return False
            check=getattr(seekable,"isCurrentlySeekable",None)
            if callable(check):
                try:
                    if not check():return False
                except Exception as exc:optional_failure("player.optional_guard",exc)
            native = getattr(self, "doSeekRelative", None)
            if callable(native):
                native(int(seconds * 90000))
            else:
                seekable.seekRelative(1 if seconds > 0 else -1, pts)
            try:
                self["skip_hint"].setText("+10s" if seconds > 0 else "-10s")
            except Exception as exc:
                optional_failure("player.optional_guard", exc)
            return True
        except Exception as exc:
            optional_failure("player.native_seek", exc)
            return False

    def seekBack10(self):
        return self._seek_relative_seconds(-10)

    def seekForward10(self):
        return self._seek_relative_seconds(10)

    def nextAR(self):
        self.ar_id_player += 1
        if self.ar_id_player > 6:
            self.ar_id_player = 0
        try:
            if eAVSwitch is not None:
                eAVSwitch.getInstance().setAspectRatio(self.ar_id_player)
            message = ASPECT_RATIOS.get(self.ar_id_player, "Auto")
        except Exception:
            message = "Aspect-ratio change failed"
        try:
            self.session.open(MessageBox, message, MessageBox.TYPE_INFO, timeout=1)
        except Exception as exc:
            optional_failure("player", exc)
    def show_media_info(self):
        meta="%s   •   %s   •   %s   •   %s   •   %s"%(self._category_label(),self["video_quality"].getText(),self["video_codec"].getText(),self["audio_codec"].getText(),self["bitrate"].getText())
        self._media_info_open=True
        try:
            if self._online_subtitle_overlay is not None:self._online_subtitle_overlay.hide()
        except Exception:pass
        try:
            self._set_infobar_visuals(False)
        except Exception as exc:optional_failure("player",exc)
        try:self.session.openWithCallback(self._media_info_closed,PlayerInformationOverlay,self.name,self.item,self.media_type,meta,self._poster_path)
        except Exception as exc:
            LOG.warning("Nova media information overlay failed: %s", exc)
            try:self["connection"].setText("MEDIA INFO UNAVAILABLE")
            except Exception as exc:optional_failure("player",exc)
    def _media_info_closed(self,*args):
        self._media_info_open=False
        try:self.doShow()
        except Exception:
            try:self.show()
            except Exception as exc:optional_failure("player",exc)

    def _service_progress_snapshot(self, raw=False):
        """Read one sane 90kHz PTS snapshot from the active Enigma2 service."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service.seek() if service else None
            pos_result = seek.getPlayPosition() if seek else None
            len_result = seek.getLength() if seek else None
            position = int(pos_result[1]) if pos_result and not pos_result[0] else 0
            duration = int(len_result[1]) if len_result and not len_result[0] else 0
            # Negative values are error/sentinel values on several OE-A builds;
            # never abs() them into a fake positive resume timestamp.
            position = max(0, position)
            duration = max(0, duration)
            if duration and position > duration + (5 * 90000):
                position = 0
            return position, duration
        except Exception as exc:
            try:
                if load_settings().get("diagnostic_logging",False): optional_failure("player.progress_snapshot",exc)
            except Exception as exc:
                optional_failure("player.optional_guard", exc)
            return 0, 0

    def _save_history_progress(self, force=False, completed_override=None):
        if self.media_type not in ("vod","series","episode","catchup"):
            return
        try:
            if not self.started or not self._watch_started_at:
                return
            elapsed = time.time() - float(self._watch_started_at)
            had_resume = int(self.item.get("_resume_position") or 0) >= (10 * 90000)
            # A resumed title must be allowed to update its bookmark even when
            # the viewer watches only a few seconds before pressing BACK.
            if not force and not had_resume and elapsed < float(self._history_save_min_seconds):
                return
        except Exception:
            if not force:
                return

        position, duration = self._service_progress_snapshot()
        if position > 0:
            self._last_progress_position = position
        if duration > 0:
            self._last_progress_duration = duration
        position = position or int(self._last_progress_position or 0)
        duration = duration or int(self._last_progress_duration or 0)
        try:
            existing=max(0,int(self.item.get("_resume_position") or 0))
            elapsed=max(0.0,time.time()-float(self._watch_started_at or time.time()))
            if duration and existing >= 45*90000 and elapsed < 75.0 and position >= int(duration*0.95) and existing < int(duration*0.85):
                position=existing
        except Exception as exc: optional_failure("player.progress_eof_guard",exc)
        minimum = int(self._history_save_min_seconds * 90000)
        if position < minimum and completed_override is not True:
            return
        if duration and position > duration + (5 * 90000):
            return
        try:
            profile={"portal":self.item.get("_portal", ""),"mac":self.item.get("_mac", "")}
            history_item=self.item.get("_history_item") if isinstance(self.item.get("_history_item"),dict) else self.item
            cfg=load_settings()
            threshold=max(80,min(99,int(cfg.get("completion_threshold",93))))/100.0
            remaining_limit=max(30,min(900,int(cfg.get("completion_remaining_seconds",180))))*90000
            completed_by_position=bool(duration and (position>=int(duration*threshold) or max(0,duration-position)<=remaining_limit))
            completed = bool(completed_override) if completed_override is not None else bool(cfg.get("auto_remove_completed",True) and completed_by_position)
            add_recently_played(profile,self.media_type,history_item,position,duration,completed,force=bool(force))
        except Exception as exc:
            optional_failure("player", exc)

    def _current_service_is_owned(self):
        try:
            current=self.session.nav.getCurrentlyPlayingServiceReference()
            if current is None:return False
            current_text=current.toString()
            owned=str(getattr(self,"_owned_reference_string","") or "")
            if owned:return current_text==owned
            ref=getattr(self,"reference",None)
            return bool(ref is not None and current_text==ref.toString())
        except Exception:return False

    def _stop_owned_service(self, force_external=False):
        """Hard-stop only the native/external playback owned by this screen."""
        self._cancel_smart_recovery();self._closing_playback=True
        for timer_name in ("startup_timer","resume_verify_timer","subtitle_default_timer","progress_timer","crystal_timer","eof_guard_timer","recovery_timer","zap_switch_timer","stable_timer","stream_info_timer","memory_fuse_timer"):
            timer=getattr(self,timer_name,None)
            if timer is not None:
                try:timer.stop()
                except Exception as exc:optional_failure("player.stop_timer",exc)
        # Unified hard stop: leaving a plugin player always stops the active
        # Enigma2 service first. ServiceApp can wrap/change the reference, so an
        # ownership-string comparison is not sufficient to guarantee silence.
        try:
            nav=getattr(self.session,"nav",None)
            if nav is not None:
                nav.stopService()
        except Exception as exc:optional_failure("player.stopService",exc)
        try:force_session_silence(self.session,self.streamurl,force=bool(force_external),stop_native=True,exclude_pids=self._external_player_baseline,owned_pids=self._owned_external_pids)
        except Exception as exc:optional_failure("player.hard_stop",exc)
        sig=signal.SIGKILL if force_external else signal.SIGTERM
        targets=set(self._owned_external_pids)
        try:targets.update(set(_matching_external_player_pids(self.streamurl))-set(self._external_player_baseline))
        except Exception as exc:optional_failure("player.external_match_stop",exc)
        for pid in list(targets):
            try:os.kill(int(pid),sig)
            except OSError:self._owned_external_pids.discard(pid)
            except Exception as exc:optional_failure("player.owned_external_stop",exc)
        return True

    def _service_still_owned(self):
        """True only while this exact native reference or matching player PID lives."""
        try:
            if self._current_service_is_owned():return True
        except Exception as exc:
            optional_failure("player.hard_stop_probe",exc)
        try:
            alive=[]
            for pid in list(self._owned_external_pids):
                if os.path.exists("/proc/%d"%int(pid)):alive.append(pid)
                else:self._owned_external_pids.discard(pid)
            matched=set(_matching_external_player_pids(self.streamurl))-set(self._external_player_baseline)
            descendants=_descendant_pids(self._owned_external_pids)-set(self._external_player_baseline)
            return bool(alive or matched or descendants)
        except Exception as exc:
            optional_failure("player.hard_stop_process_probe",exc)
            return True

    def _xstreamity_service_handoff(self, restore_service=True):
        """XStreamity-style player exit: stop IPTV, restore pre-plugin service.

        The important part is that Enigma2 owns the transition. We do not keep
        retrying/respawning/killing the decoder from Python. A normal service
        replacement lets ServiceApp and Broadcom release the old playback path.
        """
        self._closing_playback=True
        self._cancel_smart_recovery()
        for timer_name in ("startup_timer","resume_timer","resume_verify_timer","subtitle_default_timer","progress_timer","crystal_timer","hard_stop_timer","eof_guard_timer","recovery_timer","zap_switch_timer","stable_timer","stream_info_timer","memory_fuse_timer","hideTimer"):
            timer=getattr(self,timer_name,None)
            if timer is not None:
                try:timer.stop()
                except Exception as exc:optional_failure("player.silent_guard",exc)
        nav=getattr(self.session,"nav",None)
        if nav is None:return False
        try:nav.stopService()
        except Exception as exc:optional_failure("player.xst_stop_service",exc)
        restored=False
        if restore_service and self._return_service_ref_string:
            try:
                nav.playService(eServiceReference(self._return_service_ref_string))
                restored=True
            except Exception as exc:
                optional_failure("player.xst_restore_service",exc)
        self._service_handoff_done=True
        try:gc.collect();_malloc_trim()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        return restored

    def _begin_hard_stop_close(self, result):
        # Compatibility entry point used by the rest of Ultra Stalker. Normal
        # EXIT now follows XStreamity's deterministic stop/restore/close flow.
        self._hard_stop_pending_result=result
        self._hard_stop_closing=True
        skip_restore=bool(isinstance(result,dict) and result.get("next_episode"))
        restored=self._xstreamity_service_handoff(restore_service=not skip_restore)
        if isinstance(result,dict):result["service_restored"]=bool(restored)
        self._finalize_player_close()

    def _verify_hard_stop(self):
        # Kept only for compatibility with an already-armed timer. No retry loop.
        if self._hard_stop_closing:self._finalize_player_close()

    def _finalize_player_close(self):
        runtime_breadcrumb("player_close",media_type=self.media_type,engine=int(self.servicetype or 0),handoff=True)
        result=self._hard_stop_pending_result or {"engine":self.servicetype,"started":self.started,"failed":self.failed}
        self._hard_stop_pending_result=None;self._hard_stop_closing=False;self.reference=None
        self.close(result)

    def _close_player(self, extra=None, save_progress=True):
        if self.restored or self._hard_stop_closing:return
        self._closing_playback=True
        if save_progress and self.media_type in ("vod","series","episode","catchup"):
            try:self._save_history_progress(force=True)
            except Exception as exc:optional_failure("player.close_save",exc)
        result={"engine":self.servicetype,"started":self.started,"failed":self.failed}
        if self._observed_quality:
            result["quality"]=self._observed_quality;result["video_width"]=int(self._observed_width or 0);result["video_height"]=int(self._observed_height or 0)
        if isinstance(extra,dict):result.update(extra)
        self.restored=True
        self._suppress_service_events(2.5)
        self._begin_hard_stop_close(result)

    def _next_episode_answer(self, answer):
        global NEXT_EPISODE_AUTOPLAY_SESSION
        next_item = self.item.get("_next_episode_item") if isinstance(self.item, dict) else None
        if answer == "session_off":
            NEXT_EPISODE_AUTOPLAY_SESSION = False
            self._close_player(save_progress=False)
        elif answer and isinstance(next_item, dict) and next_item:
            self._close_player({"next_episode": dict(next_item)}, save_progress=False)
        else:
            self._close_player(save_progress=False)

    def back(self):
        self._close_player(save_progress=True)

    def _cleanup(self):
        runtime_breadcrumb("player_cleanup",media_type=self.media_type,engine=int(self.servicetype or 0))
        # Never leave global receiver audio muted if the screen closes while a
        # resume probe is still in progress.
        try: self._release_resume_shield()
        except Exception as exc: optional_failure("player.resume_cleanup", exc)
        # Final idempotent safety net.  Repeat the complete native + pnav +
        # external stop sequence in case the screen was closed through an
        # unexpected Enigma2 path rather than back().
        if not getattr(self,"_service_handoff_done",False):
            try:
                force_session_silence(self.session, self.streamurl, force=True, stop_native=True, exclude_pids=self._external_player_baseline, owned_pids=self._owned_external_pids)
            except Exception as exc:
                optional_failure("player.cleanup_hard_stop", exc)
        for timer_name in ("startup_timer", "resume_timer", "resume_verify_timer", "subtitle_default_timer", "online_subtitle_timer", "online_subtitle_result_timer", "subtitle_volume_restore_timer", "progress_timer", "crystal_timer", "hard_stop_timer", "eof_guard_timer", "recovery_timer", "zap_switch_timer", "stable_timer", "stream_info_timer", "memory_fuse_timer", "hideTimer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception as exc:
                    optional_failure("player", exc)
        for conn_name in ("startup_timer_conn", "resume_timer_conn", "resume_verify_timer_conn", "subtitle_default_timer_conn", "online_subtitle_timer_conn", "online_subtitle_result_timer_conn", "subtitle_volume_restore_timer_conn", "progress_timer_conn", "crystal_timer_conn", "hard_stop_timer_conn", "eof_guard_timer_conn", "recovery_timer_conn", "zap_switch_timer_conn", "stable_timer_conn", "stream_info_timer_conn", "memory_fuse_timer_conn", "PicLoad_conn", "hideTimer_conn"):
            conn = getattr(self, conn_name, None)
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception as exc:
                    optional_failure("player", exc)
        callback_map=(
            ("startup_timer",self._startup_timeout),("resume_timer",self._reference_resume),("resume_verify_timer",self._verify_resume_position),
            ("subtitle_default_timer",self._enforce_default_subtitles_off),
            ("online_subtitle_timer",self._online_subtitle_tick),("online_subtitle_result_timer",self._drain_online_subtitle_result),
            ("subtitle_volume_restore_timer",self._restore_subtitle_after_volume_osd),
            ("progress_timer",self._periodic_progress_save),
            ("crystal_timer",self._update_progress_crystal),("hard_stop_timer",self._verify_hard_stop),
            ("eof_guard_timer",self._verify_early_eof),("recovery_timer",self._drain_recovery_result),("zap_switch_timer",self._drain_zap_switch_result),
            ("stable_timer",self._mark_stream_stable),("stream_info_timer",self._update_stream_info),
            ("hideTimer",self.doTimerHide),
        )
        for timer_name,callback in callback_map:
            timer=getattr(self,timer_name,None)
            if timer is None:continue
            try:
                if callback in timer.callback:timer.callback.remove(callback)
            except Exception as exc:optional_failure("player.timer_callback_remove",exc)
        try:
            callbacks=self.PicLoad.PictureData.get()
            if self._decode_poster in callbacks:callbacks.remove(self._decode_poster)
        except Exception as exc:optional_failure("player.picload_callback_remove",exc)
        try:
            if self._play_state_changed in self.onPlayStateChanged:self.onPlayStateChanged.remove(self._play_state_changed)
        except Exception as exc:optional_failure("player.playstate_callback_remove",exc)
        try:
            if self._online_subtitle_display is not None:
                self._online_subtitle_display.hideScreen()
                deleter=getattr(self.session,"deleteDialog",None)
                if callable(deleter):deleter(self._online_subtitle_display)
                self._online_subtitle_display=None
        except Exception as exc:optional_failure("player.native_online_subtitle_cleanup",exc)
        # Release decoded poster/picon buffers promptly on constrained receivers.
        for widget_name in ("logo","live_picon","adaptive_main","adaptive_poster","adaptive_live_picon","progress_neon"):
            try:
                widget=self[widget_name]
                if widget.instance is not None:widget.instance.setPixmap(None)
            except Exception as exc:optional_failure("player.optional_guard",exc)
