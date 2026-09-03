# -*- coding: utf-8 -*-
"""Subtitle style helpers extracted from player.py without UI behaviour changes."""
from __future__ import absolute_import

import hashlib
import os

from ..securefs import secure_private_dir
from ..persistent_cache import GENERATED as PERSISTENT_GENERATED_DIR
from ..log import optional_failure

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter
except Exception:
    _PILImage = None
    _ImageDraw = None
    _ImageFilter = None

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

