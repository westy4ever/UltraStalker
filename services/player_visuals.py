# -*- coding: utf-8 -*-
"""Player visual/artwork helpers.

Extracted from services.player to keep rendering/cache concerns separate from
the playback lifecycle. This module intentionally contains no Screen/player
state transitions.
"""
from __future__ import absolute_import, print_function

import hashlib
import os
import threading

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter, ImageEnhance as _ImageEnhance
except Exception:
    _PILImage = None
    _ImageDraw = None
    _ImageFilter = None
    _ImageEnhance = None

from ..securefs import secure_private_dir
from ..persistent_cache import ROOT as PERSISTENT_CACHE_ROOT, GENERATED as PERSISTENT_GENERATED_DIR
from ..log import optional_failure
from ..core.executor import LazyThreadPoolExecutor

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(PLUGIN_DIR, "assets_fhd")
PLAYER_ASSET_DIR = os.path.join(ASSET_DIR, "player")
PLAYER_FALLBACK_INFOBAR_DIR = os.path.join(PLAYER_ASSET_DIR, "fallback_infobar")

_PROGRESS_FRAME_EXECUTOR = LazyThreadPoolExecutor(max_workers=1, thread_name_prefix="ultrastalker-progress")
_PROGRESS_FRAME_PENDING = set()
_PROGRESS_FRAME_LOCK = threading.RLock()

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
def _asset(name):
    return os.path.join(ASSET_DIR, name)


def _player_asset(name):
    return os.path.join(PLAYER_ASSET_DIR, name)


NEXT_EPISODE_AUTOPLAY_SESSION = True









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
