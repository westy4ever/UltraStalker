# -*- coding: utf-8 -*-
"""Artwork and visual preparation helpers for Ultra Stalker Player.

Stage-1 extraction only: no artwork policy, cache lookup or rendering logic is
changed here; the original functions are kept byte-for-byte at function level.
"""
from __future__ import absolute_import, print_function

try:
    import colorsys
except Exception:
    from .. import compat_colorsys as colorsys

import hashlib
import os
import re
import stat
import threading
from collections import OrderedDict

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter
except Exception:
    _PILImage = None
    _ImageDraw = None
    _ImageFilter = None

from ..core.image_budget import image_budgeted
from ..securefs import secure_private_dir
from ..persistent_cache import ROOT as PERSISTENT_CACHE_ROOT, GENERATED as PERSISTENT_GENERATED_DIR
from ..storage import load_settings
from ..ui_artwork_helpers import _fit_live_picon_canvas
from ..log import optional_failure
from .player_visuals import _PROGRESS_FRAME_EXECUTOR, _PROGRESS_FRAME_PENDING, _PROGRESS_FRAME_LOCK

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(PLUGIN_DIR, "assets_fhd")
PLAYER_ASSET_DIR = os.path.join(ASSET_DIR, "player")
PLAYER_FALLBACK_INFOBAR_DIR = os.path.join(PLAYER_ASSET_DIR, "fallback_infobar")

# Stage-2: bounded in-memory artwork memoization. Persistent files remain the
# source of truth; these caches only avoid reopening/re-analysing the same
# unchanged source image repeatedly during one Player session.
_ARTWORK_MEMO_LIMIT = 48
_ARTWORK_MEMO_LOCK = threading.RLock()
_FALLBACK_FRAMES_MEMO = None
_ADAPTIVE_PLAYER_MEMO = OrderedDict()
_ADAPTIVE_INFO_MEMO = OrderedDict()
_LIVE_PICON_MEMO = OrderedDict()
_POSTER_RESOLUTION_MEMO = OrderedDict()

def _source_signature(path, min_size=1):
    try:
        source = str(path or "")
        if not source:
            return None
        st = os.stat(source)
        if not stat.S_ISREG(st.st_mode) or int(st.st_size) < int(min_size):
            return None
        stamp = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)))
        return (source, stamp, int(st.st_size))
    except Exception:
        return None

def _memo_get(cache, key):
    with _ARTWORK_MEMO_LOCK:
        value = cache.get(key)
        if value is None:
            return None
        try:
            cache.move_to_end(key)
        except Exception:
            pass
        return value

def _memo_put(cache, key, value):
    with _ARTWORK_MEMO_LOCK:
        cache[key] = value
        try:
            cache.move_to_end(key)
        except Exception:
            pass
        while len(cache) > _ARTWORK_MEMO_LIMIT:
            try:
                cache.popitem(last=False)
            except Exception:
                break
    return value

def _fallback_player_frames():
    """Bundled receiver-safe neutral glass.

    This is intentionally independent of poster/picon/HDD state.  It is the
    first-line player chrome so a late/missing adaptive source can never leave
    naked text and controls floating over video.
    """
    global _FALLBACK_FRAMES_MEMO
    with _ARTWORK_MEMO_LOCK:
        if _FALLBACK_FRAMES_MEMO is not None:
            return dict(_FALLBACK_FRAMES_MEMO)
    names=("main","poster_halo","poster","live_picon","track","keybar",
           "chip_quality","chip_video","chip_audio_codec",
           "chip_stream","chip_audio","chip_subtitles","chip_engine")
    out={}
    for name in names:
        path=os.path.join(PLAYER_FALLBACK_INFOBAR_DIR,"%s.png"%name)
        if os.path.isfile(path):
            out[name]=path
    out["accent"]="#4aa2d6"
    out["accent_soft"]="#9bd9f5"
    out["accent_neon"]="#69c9f4"
    with _ARTWORK_MEMO_LOCK:
        _FALLBACK_FRAMES_MEMO=dict(out)
    return dict(out)

@image_budgeted
def _adaptive_player_frames(source_path):
    """Create player chrome with the exact floating-glass language used by Categories/Portal rows.

    Geometry stays identical to us226. Only the material changes: dark calm glass,
    restrained poster-derived tint, soft internal glow, polished inner rim and top sheen.
    """
    if _PILImage is None or _ImageDraw is None:
        return {}
    try:
        signature=_source_signature(source_path,1)
        if signature is None:
            return {}
        memo_key=(signature,"player-glass-v1")
        memo=_memo_get(_ADAPTIVE_PLAYER_MEMO,memo_key)
        if memo is not None:
            return dict(memo)
        source_path=signature[0]
        with _PILImage.open(source_path) as im:
            im = im.convert("RGB")
            im.thumbnail((96, 96))
            pixels = [p for p in list(im.getdata()) if 28 < sum(p) / 3.0 < 230]
            if not pixels:
                return {}
            pixels.sort(key=lambda p: (max(p)-min(p)) + sum(p)/12.0, reverse=True)
            sample = pixels[:max(8, len(pixels)//8)]
            rgb = tuple(int(sum(px[i] for px in sample)/len(sample)) for i in range(3))
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
        key = hashlib.sha1((source_path + str(os.path.getmtime(source_path)) + str(accent) + "glass245-player-exactfit-titlelogo-episode-marker").encode("utf-8", "ignore")).hexdigest()[:16]
        out = {}

        specs = {
            # name: (size, radius, selected_like, fill_alpha, edge_alpha, glow_alpha)
            "main": ((1800,200), 26, True, 226, 238, 196),
            # Separate halo canvas. This is intentionally larger than the poster frame
            # so Enigma2 cannot clip the outer neon at the artwork boundary.
            "poster_halo": ((310,415), 28, True, 0, 255, 255),
            "poster": ((270,395), 22, True, 0, 255, 255),
            "live_picon": ((240,152), 18, True, 118, 245, 188),
            "keybar": ((1540,58), 22, True, 232, 228, 178),
            "chip_quality": ((150,42), 15, False, 232, 226, 168),
            "chip_video": ((130,42), 15, False, 232, 226, 168),
            "chip_audio_codec": ((120,42), 15, False, 232, 226, 168),
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

                    # Keep this layer as chrome only. The real poster is now always the
                    # bottom-most layer, while every halo/rim layer renders above it.
                    # This preserves the exact premium frame but lets the overlays hide
                    # any rectangular poster corners that used to peek outside the rim.
                    d=_ImageDraw.Draw(img)
                    d.rounded_rectangle((29,19,280,395),radius=18,outline=hot+(255,),width=3)
                    d.rounded_rectangle((30,20,279,394),radius=16,outline=white_hot+(255,),width=1)
                elif name == "poster":
                    # Foreground poster chrome. The actual poster sits 10 px inside this
                    # 270x395 overlay. Paint only the four outside corner wedges above
                    # the poster, then redraw the exact existing rim. This does NOT crop
                    # or resize the artwork; it simply makes the chrome the final visible
                    # edge so no poster corners can protrude past the rounded frame.
                    hot = mix(accent, (255,255,255), .72)
                    white_hot = mix(accent, (255,255,255), .94)
                    glass_corner = mix(base, accent, .13)
                    poster_rect=(10,10,w-11,hh-11)
                    mask=_PILImage.new("L",size,0)
                    md=_ImageDraw.Draw(mask)
                    md.rectangle(poster_rect,fill=255)
                    md.rounded_rectangle(poster_rect,radius=18,fill=0)
                    corner_layer=_PILImage.new("RGBA",size,glass_corner+(246,))
                    img.alpha_composite(_PILImage.composite(corner_layer,_PILImage.new("RGBA",size,(0,0,0,0)),mask))
                    d=_ImageDraw.Draw(img)
                    d.rounded_rectangle((4,4,w-5,hh-5),radius=22,outline=accent+(255,),width=3)
                    d.rounded_rectangle((6,6,w-7,hh-7),radius=20,outline=hot+(255,),width=2)
                    d.rounded_rectangle((8,8,w-9,hh-9),radius=18,outline=white_hot+(220,),width=1)
                else:
                    fill_mix = 0.13 if name == "main" else (0.115 if name == "keybar" else 0.105)
                    fill = mix(base, accent, fill_mix)
                    # Fill the ENTIRE widget canvas. Older builds started at (2,2),
                    # leaving a transparent video-colored gutter between the adaptive
                    # material and its glass rim. The user wants one continuous glass
                    # surface: transparency must come only from the material alpha,
                    # never from an accidental empty strip around the frame.
                    outer_box=(0,0,w-1,hh-1)
                    edge_width=(3 if selected else 2)
                    d.rounded_rectangle(outer_box, radius=radius, fill=fill + (fill_alpha,))
                    # Exact-fit outer closure for the main Player panel and lower keybar.
                    # These two widgets must read as one complete adaptive surface with no
                    # apparent empty strip between the glass body and the outer rim.
                    if name in ("main","keybar"):
                        d.rounded_rectangle(outer_box, radius=radius,
                                            outline=accent + (min(255, edge_alpha + 12),), width=1)
                        d.rounded_rectangle((1,1,w-2,hh-2), radius=max(1,radius-1),
                                            outline=accent + (edge_alpha,), width=edge_width)
                        inner_inset=4
                    else:
                        d.rounded_rectangle((1,1,w-2,hh-2), radius=max(1,radius-1),
                                            outline=accent + (edge_alpha,), width=edge_width)
                        inner_inset=5
                    inner = mix(accent2, (255,255,255), .34)
                    d.rounded_rectangle((inner_inset,inner_inset,w-1-inner_inset,hh-1-inner_inset), radius=max(8,radius-inner_inset),
                                        outline=inner + ((126 if selected else 92),), width=1)

                    # Polished upper reflection copied from the approved category glass treatment.
                    sheen = _PILImage.new("RGBA", size, (0,0,0,0))
                    sd = _ImageDraw.Draw(sheen)
                    hi = mix(accent, (255,255,255), .54)
                    top = max(18, int(hh*0.43))
                    for yy in range(6, top, 5):
                        t = (yy-6.0)/max(1.0, top-6.0)
                        a = max(0, int((38 if selected else 25) * (1.0-t)**1.65))
                        sd.rounded_rectangle((10, yy, w-11, min(hh-8, yy+7)),
                                             radius=max(6,radius-8), fill=hi + (a,))
                    if _ImageFilter is not None:
                        sheen = sheen.filter(_ImageFilter.GaussianBlur(radius=4 if selected else 3))
                    img = _PILImage.alpha_composite(img, sheen)
                    # No vertical separators in the keybar. It is intentionally one
                    # uninterrupted adaptive glass dock; the four Cinematic buttons
                    # provide all visual grouping by themselves.

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
        out["accent"] = "#%02x%02x%02x" % accent
        out["accent_soft"] = "#%02x%02x%02x" % mix(accent, (255,255,255), .54)
        out["accent_neon"] = "#%02x%02x%02x" % mix(accent, (255,255,255), .32)
        _memo_put(_ADAPTIVE_PLAYER_MEMO,memo_key,dict(out))
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


def _asset(name):
    return os.path.join(ASSET_DIR, name)



def _letter_logo(name, media_type):
    if media_type == "vod":
        return _asset("grid_placeholder_movie_921.png")
    if media_type in ("series", "episode"):
        return _asset("grid_placeholder_series_921.png")
    return _asset("us168_live_placeholder_220x132.png")



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


@image_budgeted
def _adaptive_info_frames(source_path):
    if _PILImage is None or _ImageDraw is None:
        return {}
    try:
        signature=_source_signature(source_path,1)
        if signature is None:return {}
        memo_key=(signature,"info-glass-v1")
        memo=_memo_get(_ADAPTIVE_INFO_MEMO,memo_key)
        if memo is not None:return dict(memo)
        source_path=signature[0]
        with _PILImage.open(source_path) as im:
            im=im.convert("RGB");im.thumbnail((72,96))
            pixels=[p for p in im.getdata() if 25<sum(p)/3.0<235]
        if not pixels:return {}
        pixels.sort(key=lambda p:(max(p)-min(p))+sum(p)/14.0,reverse=True)
        sample=pixels[:max(8,len(pixels)//8)]
        rgb=tuple(int(sum(p[i] for p in sample)/len(sample)) for i in range(3))
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
        _memo_put(_ADAPTIVE_INFO_MEMO,memo_key,dict(out))
        return out
    except Exception as exc:
        optional_failure("player.info_glass",exc);return {}

def _cached_poster(item, name, media_type):
    is_live=str(media_type or "").lower() in ("itv","live")
    data=item if isinstance(item,dict) else {}
    # Only memoize a real resolved file. Placeholders are intentionally never
    # memoized because a Live picon can arrive asynchronously a moment later.
    relevant=(
        str(data.get("_player_picon") or ""), str(data.get("_player_poster") or ""),
        str(data.get("_player_picon_url") or ""), str(data.get("stream_icon") or ""),
        str(data.get("picon") or ""), str(data.get("logo") or ""),
        str(data.get("poster") or ""), str(data.get("poster_url") or ""),
        str(data.get("cover") or ""), str(data.get("cover_url") or ""),
        str(data.get("icon") or ""), str(data.get("image") or ""), str(data.get("img") or ""),
    )
    memo_key=(str(media_type or "").lower(),str(name or ""),relevant)
    cached=_memo_get(_POSTER_RESOLUTION_MEMO,memo_key)
    if cached and _source_signature(cached,101) is not None:
        return cached

    explicit_key="_player_picon" if is_live else "_player_poster"
    direct = str(data.get(explicit_key) or data.get("_player_poster") or "").strip()
    if direct and _source_signature(direct,1) is not None:
        return _memo_put(_POSTER_RESOLUTION_MEMO,memo_key,direct)
    value = _image_value(data)
    if value and _source_signature(value,1) is not None:
        return _memo_put(_POSTER_RESOLUTION_MEMO,memo_key,value)
    # Prefer the absolute picon URL attached by the browser; its digest is the
    # same persistent HDD cache key used by the live grid.
    if is_live:
        # Provider/browser channel art is not consistent about the field name.
        # Reuse the already-cached HDD picon regardless of which native key supplied it.
        picon_url=str(data.get("_player_picon_url") or data.get("stream_icon") or data.get("picon") or data.get("logo") or data.get("icon") or data.get("image") or data.get("img") or "").strip()
        if picon_url:value=picon_url
    if value.startswith(("http://", "https://")):
        digest = hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()
        roots=[IMAGE_CACHE_DIR]
        if is_live:
            roots.insert(0,os.path.join(PERSISTENT_CACHE_ROOT,"live_picons"))
        for root in roots:
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                candidate = os.path.join(root, digest + ext)
                if _source_signature(candidate,101) is not None:
                    return _memo_put(_POSTER_RESOLUTION_MEMO,memo_key,candidate)
    # Live must never show a generated initial/letter. Until a real picon is
    # available use the neutral live asset; do not memoize it.
    if is_live:
        return _asset("us168_live_placeholder_220x132.png")
    return _letter_logo(name, media_type)

@image_budgeted
def _prepare_player_poster_fill(source_path, size=(242,330)):
    """Player reuses the exact HDD master poster path; no player-only poster copy."""
    return source_path

@image_budgeted
def _prepare_live_picon(source_path, size=(220,132)):
    """Aspect-fit Live picon on an exact transparent 220x132 Enigma-style canvas."""
    signature=_source_signature(source_path,101)
    if signature is None:return source_path
    try:tw,th=max(1,int(size[0])),max(1,int(size[1]))
    except Exception:tw,th=220,132
    memo_key=(signature,tw,th)
    cached=_memo_get(_LIVE_PICON_MEMO,memo_key)
    if cached and _source_signature(cached,101) is not None:return cached
    prepared=_fit_live_picon_canvas(signature[0],PERSISTENT_GENERATED_DIR,(tw,th)) or signature[0]
    if prepared and _source_signature(prepared,101) is not None:
        _memo_put(_LIVE_PICON_MEMO,memo_key,prepared)
    return prepared

def _player_title_logo_source(item, name, media_type):
    """Return the current title's Cinematic authority, never a Player lookup.

    Player is presentation-only.  It first honors an explicit current-screen
    handoff, then the unified Ultra Cinematic 420x144 cache for the same locked
    identity.  It never selects a 1220x72 cache entry independently.
    """
    item=item if isinstance(item,dict) else {}
    try:
        for key in ("_player_title_logo_cinematic_source",):
            candidate=str(item.get(key) or "")
            if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>256:return candidate
        from ..title_logo_ultra import ultra_title_logo_cached
        return ultra_title_logo_cached(media_type,item,item,(420,144),name)
    except Exception as exc:
        optional_failure("player.title_logo_source",exc);return ""

def _prepare_player_title_logo_cache_path(source_path, canvas_size=(1220,78)):
    try:
        source=str(source_path or "")
        if not source or not os.path.isfile(source) or os.path.getsize(source)<=256:return ""
        st=os.stat(source)
        stamp="%s|%s"%(int(getattr(st,"st_mtime_ns",int(st.st_mtime*1000000000))),int(st.st_size))
        try:
            cw,ch=int(canvas_size[0]),int(canvas_size[1])
        except Exception:
            cw,ch=1220,78
        digest=hashlib.sha1((source+"|"+stamp+"|player-title-logo-%sx%s-v3"%(cw,ch)).encode("utf-8","ignore")).hexdigest()[:20]
        return os.path.join(PERSISTENT_GENERATED_DIR,"player_title_logos","%s.png"%digest)
    except Exception:return ""

@image_budgeted
def _prepare_player_title_logo(source_path, canvas_size=(1220,78)):
    """Create a pristine persistent Player presentation from the HDD master.

    Transparent margins are trimmed first, aspect ratio is preserved exactly,
    and LANCZOS is used only for down-scaling. The master is never modified.
    """
    if _PILImage is None:return ""
    try:
        source=str(source_path or "")
        if not source or not os.path.isfile(source) or os.path.getsize(source)<=256:return ""
        cw,ch=int(canvas_size[0]),int(canvas_size[1])
        target=_prepare_player_title_logo_cache_path(source,(cw,ch))
        if not target:return ""
        cache=os.path.dirname(target)
        secure_private_dir(cache)
        if os.path.isfile(target) and os.path.getsize(target)>256:return target
        with _PILImage.open(source) as src:
            im=src.convert("RGBA")
            try:
                bbox=im.getchannel("A").getbbox()
                if bbox:im=im.crop(bbox)
            except Exception:pass
            if im.width<2 or im.height<2:return ""
            max_w,max_h=max(1,cw-24),max(1,ch-4)
            scale=min(float(max_w)/float(im.width),float(max_h)/float(im.height),1.0)
            nw=max(1,int(round(im.width*scale)));nh=max(1,int(round(im.height*scale)))
            if (nw,nh)!=im.size:
                res=getattr(getattr(_PILImage,"Resampling",_PILImage),"LANCZOS",1)
                im=im.resize((nw,nh),res)
            canvas=_PILImage.new("RGBA",(cw,ch),(0,0,0,0))
            canvas.alpha_composite(im,((cw-nw)//2,max(0,(ch-nh)//2)))
            tmp=target+".tmp.%s"%os.getpid()
            canvas.save(tmp,"PNG",compress_level=1);os.replace(tmp,target)
        return target if os.path.isfile(target) and os.path.getsize(target)>256 else ""
    except Exception as exc:
        optional_failure("player.title_logo_prepare",exc);return ""

def _apply_player_font_scale(xml):
    try:mode=str(load_settings().get("font_scale") or "normal").lower()
    except Exception:mode="normal"
    factor={"normal":1.0,"large":1.12,"larger":1.22,"xlarge":1.32}.get(mode,1.0)
    if factor<=1.0:return xml
    def _font(m):
        base=int(m.group(2))
        if base<12:return m.group(0)
        size=max(base,min(base+10,int(round(base*factor))))
        return 'font="%s;%d"'%(m.group(1),size)
    return re.sub(r'font="([^;]+);(\d+)"',_font,xml)
