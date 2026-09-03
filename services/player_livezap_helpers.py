# -*- coding: utf-8 -*-
"""Live-Zap presentation helpers extracted from player.py.

Kept behavior-identical to the original player implementation; this module
contains no Screen lifecycle, key handling, or skin geometry.
"""
from __future__ import absolute_import, print_function

import hashlib
import os
import re
import unicodedata

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter
except Exception:
    _PILImage = None
    _ImageDraw = None
    _ImageFilter = None

from ..persistent_cache import GENERATED as PERSISTENT_GENERATED_DIR
from ..securefs import secure_private_dir
from ..log import optional_failure

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

