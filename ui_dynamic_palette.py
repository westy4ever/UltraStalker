"""Dynamic artwork palette extraction shared by UltraStalker UI chrome builders."""

import colorsys
import os
import threading
from collections import OrderedDict

try:
    from PIL import Image as _PILImage, ImageOps as _PILImageOps
except Exception:
    _PILImage = None
    _PILImageOps = None

from .log import optional_failure

_DYNAMIC_PALETTE_CACHE = OrderedDict()
_DYNAMIC_PALETTE_CACHE_LOCK = threading.RLock()
_DYNAMIC_PALETTE_CACHE_LIMIT = 256

def _dynamic_palette(source_path):
    """Extract and session-cache two safe accent colours from artwork.

    One poster can feed card chrome, selection laser, page mood and Details.
    Cache by path + mtime + size so all of those surfaces reuse one extraction
    without risking stale colours when the poster file changes.
    """
    if _PILImage is None or not source_path or not os.path.isfile(source_path):
        return ((8, 34, 52), (5, 19, 31))
    cache_key=None
    try:
        st=os.stat(source_path);cache_key=(str(source_path),int(getattr(st,"st_mtime_ns",int(st.st_mtime*1000000000))),int(st.st_size))
        with _DYNAMIC_PALETTE_CACHE_LOCK:
            cached=_DYNAMIC_PALETTE_CACHE.get(cache_key)
            if cached is not None:
                _DYNAMIC_PALETTE_CACHE.move_to_end(cache_key);return cached
    except Exception:
        cache_key=None
    try:
        with _PILImage.open(source_path) as image:
            try:image=_PILImageOps.exif_transpose(image)
            except Exception as exc:optional_failure("ui.dynamic_palette",exc)
            image=image.convert("RGB")
            resampling=getattr(getattr(_PILImage,"Resampling",_PILImage),"BOX",4)
            image.thumbnail((72,48),resampling)
            # Quantization prevents a single bright face/logo pixel from
            # becoming the theme. Dominant mid-tone colours win instead.
            q=image.quantize(colors=12,method=getattr(_PILImage,"MEDIANCUT",0)).convert("RGB")
            colors=q.getcolors(maxcolors=4096) or []
        ranked=[]
        for count,rgb in colors:
            r,g,b=[int(v) for v in rgb]
            mx=max(r,g,b);mn=min(r,g,b);sat=(mx-mn)/float(max(1,mx));lum=(r*0.2126+g*0.7152+b*0.0722)
            if lum<18 or lum>238:continue
            score=float(count)*(0.55+sat*1.8)*(0.55+min(lum,180.0)/180.0)
            ranked.append((score,(r,g,b)))
        ranked.sort(reverse=True,key=lambda x:x[0])
        if not ranked:return ((8,34,52),(5,19,31))
        first=ranked[0][1];second=None
        for _score,c in ranked[1:]:
            distance=sum((c[i]-first[i])**2 for i in range(3))**0.5
            if distance>=52:
                second=c;break
        second=second or ranked[min(1,len(ranked)-1)][1]
        def dark(rgb,light=0.16):
            r,g,b=[v/255.0 for v in rgb];h,l,sv=colorsys.rgb_to_hls(r,g,b)
            # Keep the source hue but force a premium dark/saturated surface.
            sv=max(0.42,min(0.82,sv));l=max(0.075,min(light,0.19))
            rr,gg,bb=colorsys.hls_to_rgb(h,l,sv)
            return (int(rr*255),int(gg*255),int(bb*255))
        result=(dark(first,0.17),dark(second,0.115))
        if cache_key is not None:
            try:
                with _DYNAMIC_PALETTE_CACHE_LOCK:
                    _DYNAMIC_PALETTE_CACHE[cache_key]=result;_DYNAMIC_PALETTE_CACHE.move_to_end(cache_key)
                    while len(_DYNAMIC_PALETTE_CACHE)>_DYNAMIC_PALETTE_CACHE_LIMIT:_DYNAMIC_PALETTE_CACHE.popitem(last=False)
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        return result
    except Exception as exc:
        optional_failure("ui.dynamic_palette",exc)
        return ((8,34,52),(5,19,31))

