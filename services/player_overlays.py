# -*- coding: utf-8 -*-
"""Player overlay, subtitle-choice and live-zap screens.

Player overlay and chooser screens split from the main native player module.
Their public behavior remains owned by the Ultra Stalker player contract.
"""
from __future__ import absolute_import, print_function

try:
    import colorsys
except Exception:
    from .. import compat_colorsys as colorsys

import hashlib
import os
import queue
import re
import time
import unicodedata

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter
except Exception:
    _PILImage = None
    _ImageDraw = None
    _ImageFilter = None

from .. import _
from ..core.image_budget import image_budgeted
from ..securefs import secure_private_dir
from ..persistent_cache import GENERATED as PERSISTENT_GENERATED_DIR
from ..log import optional_failure
from .player_core import _PLAYER_BG_EXECUTOR
from .player_artwork import _apply_player_font_scale, _asset, _adaptive_info_frames

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
try:
    from Components.MultiContent import MultiContentEntryPixmapAlphaBlend
except Exception:
    MultiContentEntryPixmapAlphaBlend = MultiContentEntryPixmapAlphaTest
from Components.Pixmap import Pixmap
from Components.ServiceEventTracker import ServiceEventTracker
from Screens.Screen import Screen
from enigma import eTimer, iPlayableService, gFont, eListboxPythonMultiContent, RT_HALIGN_LEFT, RT_VALIGN_CENTER, loadPNG

ONLINE_SUBTITLE_SKIN = """
<screen name="UltraStalkerOnlineSubtitleOverlay" position="0,0" size="1920,1080" flags="wfNoBorder">
    <widget name="text" position="170,805" size="1580,150" font="Regular;38" halign="center" valign="bottom"
        foregroundColor="#ffffff" shadowColor="#000000" shadowOffset="3,3" transparent="1" zPosition="1"/>
</screen>
"""
ONLINE_SUBTITLE_SKIN=_apply_player_font_scale(ONLINE_SUBTITLE_SKIN)

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


@image_budgeted
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
        self["title"]=Label(_(str(title or "Subtitles")))
        self["hint"]=Label(_("OK  Select   •   BACK  Close   •   UP / DOWN  Navigate"))
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
        self["title"]=Label(str(name or _("Media information"))[:105]);self["meta"]=Label(str(meta or "")[:160])
        desc=self.item.get("description") or self.item.get("descr") or self.item.get("plot") or self.item.get("overview") or _("No additional description is available.")
        self._pages=self._make_pages(desc);self._page=0;self["description"]=Label(self._pages[0]);self["page"]=Label("")
        cast=self.item.get("actors") or self.item.get("cast") or self.item.get("actor") or "";director=self.item.get("director") or self.item.get("directors") or "";writer=self.item.get("writer") or self.item.get("writers") or self.item.get("creator") or ""
        self["cast_label"]=Label(_("Cast") if cast else "");self["cast"]=Label(str(cast)[:135]);self["director_label"]=Label(_("Director") if director else "");self["director"]=Label(str(director)[:68]);self["writer_label"]=Label(_("Writer") if writer else "");self["writer"]=Label(str(writer)[:68]);self["hint"]=Label(_("UP / DOWN  Read description     •     YELLOW / OK / BACK  Close"))
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

def _clean_live_name(value, enabled=True):
    raw=str(value or "").strip()
    if not enabled:
        return raw or "Channel"
    text=raw
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

@image_budgeted
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

@image_budgeted
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
    """One in-player Live browser with two in-place modes.

    R66 keeps the proven drawer and simply swaps its row model:
      channels --BACK/EXIT--> categories --OK--> channels
    No new screen is opened for category browsing, and playback keeps running.
    """
    skin=LIVE_ZAP_SKIN
    VISIBLE=15

    def __init__(self,session,channels,current_index=0,title="Live Channels",picon_path=None,
                 page_loader=None,page_size=10,total=0,categories=None,current_category_id="",
                 categories_loader=None,category_page_loader=None,clean_titles=True):
        Screen.__init__(self,session)
        self.channels=list(channels or [])
        self.total=max(len(self.channels),int(total or 0))
        if self.total and len(self.channels)<self.total:self.channels.extend([None]*(self.total-len(self.channels)))
        self.cursor=max(0,min(int(current_index or 0),max(0,len(self.channels)-1))) if self.channels else 0
        self.offset=max(0,min(self.cursor,max(0,len(self.channels)-self.VISIBLE)))
        self._channel_cursor=self.cursor;self._channel_offset=self.offset
        self.current_category_id=str(current_category_id or "")
        self.current_category_title=str((_("Live Channels") if title == "Live Channels" else title) or _("Live TV"))
        self.clean_titles=bool(clean_titles)
        self.title_text=_clean_live_name(self.current_category_title,self.clean_titles)
        self.picon_path=_fit_zap_picon(picon_path)
        self.page_loader=page_loader if callable(page_loader) else None
        self.page_size=max(1,int(page_size or 10))
        self.categories=[dict(x) for x in (categories or []) if isinstance(x,dict)]
        self.categories_loader=categories_loader if callable(categories_loader) else None
        self.category_page_loader=category_page_loader if callable(category_page_loader) else None
        self.category_cursor=0;self.category_offset=0
        self.mode="channels"

        self.jobs=queue.Queue();self.loading=False;self._pending_pages=set();self._page_attempts={};self._page_retry_after={};self._render_cache={}
        self._page_futures={};self._channel_generation=0;self._last_move_direction=1
        self.nav_jobs=queue.Queue();self._nav_future=None;self._nav_pending=False;self._nav_generation=0

        self["panel"]=Pixmap();self["folder_bg"]=Pixmap();self["channel_bg"]=Pixmap();self["picon"]=Pixmap()
        self["footer_count_bg"]=Pixmap();self["footer_page_bg"]=Pixmap();self["footer_play_bg"]=Pixmap();self["footer_close_bg"]=Pixmap()
        self["title"]=Label(self.title_text);self["subtitle"]=Label("")
        self["counter"]=Label("");self["footer_page"]=Label(_("◀▶  Page"));self["footer_play"]=Label(_("OK  Play"));self["footer_close"]=Label("BACK  " + _("Categories"))
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

    @staticmethod
    def _category_id(item):
        if not isinstance(item,dict):return ""
        return str(item.get("id") or item.get("genre_id") or item.get("category_id") or item.get("name") or item.get("title") or "").strip()

    @staticmethod
    def _category_name(item):
        if not isinstance(item,dict):return ""
        value=str(item.get("title") or item.get("name") or item.get("category_name") or item.get("genre_name") or item.get("label") or "").strip()
        try:value=unicodedata.normalize("NFKC",value)
        except Exception:pass
        return value or UltraStalkerLiveZapList._category_id(item) or _("LIVE")

    def _fit_folder_title_font(self):
        """Fit the complete drawer heading without clipping long provider names."""
        try:
            value=str(self.title_text or _("Live TV"))
            units=sum(1.28 if ord(ch)>0x2ff else (0.55 if ch in " ilI1|.,:'" else 1.0) for ch in value)
            size=int(max(14,min(24,round(24.0*min(1.0,20.5/max(1.0,units))))))
            if self["title"].instance is not None:self["title"].instance.setFont(gFont("Regular",size))
        except Exception as exc:
            optional_failure("player.zap_folder_font_fit",exc)

    def _set_mode_labels(self):
        if self.mode=="categories":
            self.title_text=_("Categories")
            self["footer_play"].setText("OK  " + _("Open"));self["footer_close"].setText(_("BACK  Close"))
        else:
            self.title_text=_clean_live_name(self.current_category_title or _("Live TV"),self.clean_titles)
            self["footer_play"].setText(_("OK  Play"));self["footer_close"].setText("BACK  " + _("Categories"))
        self["title"].setText(self.title_text)
        self._fit_folder_title_font()

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
        self._set_mode_labels();self._render(force=True);self._load_all()

    def _ensure_timer(self):
        try:
            if not self.timer.isActive():self.timer.start(120,False)
        except Exception:
            try:self.timer.start(120,False)
            except Exception:pass

    def _load_page_for_index(self,index,urgent=False):
        """Load a missing drawer page without ever making a transient miss permanent.

        The channel drawer is a navigation surface, not a catalogue crawler.  A
        temporary empty/failed provider page must therefore remain retryable,
        while the page under the cursor wins over speculative neighbour warm-up.
        """
        if self.mode!="channels" or self.page_loader is None or not self.total:return
        page=max(1,int(index)//self.page_size+1)
        base=(page-1)*self.page_size
        end=min(len(self.channels),base+self.page_size)
        if base<end and all(isinstance(x,dict) for x in self.channels[base:end]):return
        pending=getattr(self,"_pending_pages",set())
        if page in pending:return
        now=time.monotonic()
        retry_after=getattr(self,"_page_retry_after",{})
        ready_at=float(retry_after.get(page,0.0) or 0.0)
        # Even an urgent cursor request respects a tiny anti-hammer window.
        if now<ready_at and (not urgent or ready_at-now>0.20):return
        attempts=getattr(self,"_page_attempts",{})
        attempts[page]=int(attempts.get(page,0) or 0)+1;self._page_attempts=attempts
        pending.add(page);self._pending_pages=pending
        page_loader=self.page_loader;jobs=self.jobs;generation=self._channel_generation
        def worker():
            rows=[];error=None
            try:
                try:rows=page_loader(page)
                except TypeError:rows=page_loader(page,None)
                except Exception as exc:error=exc;rows=[]
                jobs.put((page,[dict(x) for x in (rows or []) if isinstance(x,dict)],generation,error))
            finally:
                jobs.put((-page,[],generation,None))
        future=None
        try:
            # Duplicate suppression keeps rapid remote repeats from queueing the
            # same provider page more than once.
            future=_PLAYER_BG_EXECUTOR.submit(worker,_task_key="live-drawer-page:%s:%s"%(generation,page))
            self._page_futures[page]=future;self._ensure_timer()
        except Exception:
            pending.discard(page);self._page_futures.pop(page,None)
            if future is not None:
                try:future.cancel()
                except Exception:pass

    def _window_pages(self,start,end):
        if end<start:return []
        first=max(1,int(start)//self.page_size+1);last=max(first,int(end)//self.page_size+1)
        return list(range(first,last+1))

    def _warm_visible_pages(self,include_adjacent=True):
        if self.mode!="channels" or self.page_loader is None or not self.channels:return
        total=len(self.channels);last=max(0,total-1)
        # Selected page first, then the rest of the visible window.  Neighbour
        # pages follow the last navigation direction so speculative work can
        # never sit in front of the row the viewer is actually moving onto.
        selected=max(1,int(self.cursor)//self.page_size+1)
        current=self._window_pages(self.offset,min(last,self.offset+self.VISIBLE-1))
        forward_start=self.offset+self.VISIBLE
        backward_start=max(0,self.offset-self.VISIBLE)
        forward=self._window_pages(forward_start,min(last,forward_start+self.VISIBLE-1)) if forward_start<total else []
        backward=self._window_pages(backward_start,min(last,backward_start+self.VISIBLE-1)) if self.offset>0 else []
        ordered=[selected]+[p for p in current if p!=selected]
        if include_adjacent:
            neighbours=(forward+backward) if int(getattr(self,"_last_move_direction",1) or 1)>0 else (backward+forward)
            ordered.extend(neighbours)
        seen=set()
        for page in ordered:
            if page in seen:continue
            seen.add(page)
            self._load_page_for_index((page-1)*self.page_size,urgent=(page==selected))

    def _load_all(self):
        self._warm_visible_pages(include_adjacent=True)

    def _cancel_channel_page_workers(self):
        self._channel_generation+=1
        for future in list(getattr(self,"_page_futures",{}).values()):
            try:future.cancel()
            except Exception:pass
        self._page_futures.clear();self._pending_pages.clear();self._page_attempts.clear();self._page_retry_after.clear()

    def _request_categories(self):
        if self.categories or not callable(self.categories_loader) or self._nav_pending:return
        self._nav_generation+=1;generation=self._nav_generation;self._nav_pending=True
        loader=self.categories_loader;jobs=self.nav_jobs
        def worker():
            try:rows=[dict(x) for x in (loader() or []) if isinstance(x,dict)];jobs.put(("categories",generation,rows,None))
            except Exception as exc:jobs.put(("categories",generation,[],exc))
        try:self._nav_future=_PLAYER_BG_EXECUTOR.submit(worker);self._ensure_timer()
        except Exception as exc:
            self._nav_pending=False;optional_failure("player.zap_categories_worker",exc)

    def _make_category_page_loader(self,category_id):
        loader=self.category_page_loader;cid=str(category_id or "")
        if not callable(loader):return None
        def page_loader(page):
            data=loader(cid,page)
            if isinstance(data,dict):return [dict(x) for x in (data.get("items") or []) if isinstance(x,dict)]
            return [dict(x) for x in (data or []) if isinstance(x,dict)]
        return page_loader

    def _request_category_first_page(self,category):
        if not isinstance(category,dict) or not callable(self.category_page_loader):return
        cid=self._category_id(category);title=self._category_name(category)
        if not cid:return
        self._cancel_channel_page_workers()
        self._nav_generation+=1;generation=self._nav_generation;self._nav_pending=True
        self.current_category_id=cid;self.current_category_title=title;self.mode="channels"
        self.channels=[];self.total=0;self.cursor=0;self.offset=0;self._channel_cursor=0;self._channel_offset=0
        self.page_loader=None;self._render_cache.clear();self._set_mode_labels();self._render(force=True)
        loader=self.category_page_loader;jobs=self.nav_jobs
        def worker():
            try:
                data=loader(cid,1)
                if isinstance(data,dict):
                    rows=[dict(x) for x in (data.get("items") or []) if isinstance(x,dict)]
                    meta={"items":rows,"page":int(data.get("page") or 1),"page_size":int(data.get("page_size") or len(rows) or 1),"total":int(data.get("total") or len(rows))}
                else:
                    rows=[dict(x) for x in (data or []) if isinstance(x,dict)]
                    meta={"items":rows,"page":1,"page_size":max(1,len(rows)),"total":len(rows)}
                jobs.put(("category_page",generation,cid,title,meta,None))
            except Exception as exc:jobs.put(("category_page",generation,cid,title,{},exc))
        try:self._nav_future=_PLAYER_BG_EXECUTOR.submit(worker);self._ensure_timer()
        except Exception as exc:
            self._nav_pending=False;optional_failure("player.zap_category_worker",exc);self._render(force=True)

    def _drain_nav(self):
        changed=False
        while True:
            try:job=self.nav_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            tag=job[0] if job else ""
            generation=job[1] if len(job)>1 else -1
            if generation!=self._nav_generation:continue
            self._nav_pending=False;self._nav_future=None
            if tag=="categories":
                rows=job[2] if len(job)>2 else [];error=job[3] if len(job)>3 else None
                if error:optional_failure("player.zap_categories_load",error)
                self.categories=[dict(x) for x in (rows or []) if isinstance(x,dict)]
                self._select_current_category();changed=True
            elif tag=="category_page":
                cid=job[2] if len(job)>2 else "";title=job[3] if len(job)>3 else "Live Channels"
                meta=job[4] if len(job)>4 and isinstance(job[4],dict) else {};error=job[5] if len(job)>5 else None
                if error:
                    optional_failure("player.zap_category_page_load",error);self.channels=[];self.total=0;self.page_loader=None;changed=True;continue
                rows=[dict(x) for x in (meta.get("items") or []) if isinstance(x,dict)]
                try:page_size=max(1,int(meta.get("page_size") or len(rows) or 1))
                except Exception:page_size=max(1,len(rows) or 1)
                try:total=max(len(rows),int(meta.get("total") or len(rows)))
                except Exception:total=len(rows)
                self.page_size=page_size;self.total=total;self.channels=[None]*total
                for i,row in enumerate(rows):
                    if i<len(self.channels):self.channels[i]=row
                self.page_loader=self._make_category_page_loader(cid)
                self.current_category_id=str(cid);self.current_category_title=str(title or _("Live TV"))
                self.cursor=0;self.offset=0;self._channel_cursor=0;self._channel_offset=0
                self._render_cache.clear();self._set_mode_labels();changed=True
                self._warm_visible_pages(include_adjacent=True)
        return changed

    def _drain(self):
        nav_changed=self._drain_nav();changed_visible=False
        while True:
            try:job=self.jobs.get_nowait()
            except queue.Empty:break
            if not isinstance(job,(tuple,list)) or len(job)<2:continue
            page,rows=job[:2];generation=job[2] if len(job)>2 else self._channel_generation
            error=job[3] if len(job)>3 else None
            if generation!=self._channel_generation:continue
            if page<0:
                try:self._pending_pages.discard(-page);self._page_futures.pop(-page,None)
                except Exception as exc:optional_failure("player.silent_guard",exc)
                continue
            if page==0:continue
            base=(page-1)*self.page_size
            if rows:
                try:
                    self._page_attempts.pop(page,None);self._page_retry_after.pop(page,None)
                except Exception as exc:optional_failure("player.silent_guard",exc)
            else:
                # A transient provider miss is never frozen into the drawer.
                # Back off gently, then let visible/cursor demand retry it.
                try:
                    attempts=max(1,int(self._page_attempts.get(page,1) or 1))
                    self._page_retry_after[page]=time.monotonic()+min(2.0,0.45*(2**min(2,attempts-1)))
                except Exception:pass
                if error:optional_failure("player.zap_page_load",error)
            for i,row in enumerate(rows):
                pos=base+i
                if 0<=pos<len(self.channels):
                    self.channels[pos]=row
                    if self.mode=="channels" and self.offset<=pos<self.offset+self.VISIBLE:changed_visible=True
        if nav_changed:self._render(force=True)
        elif changed_visible:self._render(force=False)
        if self.mode=="channels" and not getattr(self,"_pending_pages",set()):self._warm_visible_pages(include_adjacent=True)
        if not getattr(self,"_pending_pages",set()) and not self._nav_pending:
            try:self.timer.stop()
            except Exception as exc:optional_failure("player.zap_timer_idle_stop",exc)

    def _name(self,item,index):
        # Missing rows are transient lazy-page state, not real channels.  Keep
        # the drawer visually quiet while the selected page is filled.
        if not isinstance(item,dict):return "…"
        return _clean_live_name(item.get("name") or item.get("title") or (_("Channel %d")%(index+1)),self.clean_titles)

    @staticmethod
    def _row_font_size(value):
        """Adaptive category-row font: preserve the complete name, never ellipsize."""
        text=str(value or "")
        units=sum(1.62 if ord(ch)>0x2ff else (0.56 if ch in " ilI1|.,:'" else 1.0) for ch in text)
        if units<=25:return 22
        if units<=31:return 20
        if units<=38:return 18
        if units<=47:return 16
        if units<=58:return 14
        if units<=70:return 12
        return 10

    def _render(self,force=False):
        is_categories=(self.mode=="categories")
        rows=self.categories if is_categories else self.channels
        total=len(rows)
        item=rows[self.cursor] if 0<=self.cursor<total else None
        if is_categories:
            subtitle=self._category_name(item) if isinstance(item,dict) else (_("Loading categories...") if self._nav_pending else _("No Live categories"))
        else:
            subtitle=self._name(item,self.cursor)[:64] if total else (_("Loading channels...") if self._nav_pending else _("No channels"))
        counter="%d / %d"%(self.cursor+1,total) if total else "0 / 0"
        cache=self._render_cache
        if force or cache.get("subtitle")!=subtitle:
            self["subtitle"].setText(subtitle)
            try:
                units=sum(1.65 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in subtitle)
                size=19 if units<=27 else (17 if units<=34 else (15 if units<=43 else (13 if units<=54 else 11)))
                if self["subtitle"].instance is not None:self["subtitle"].instance.setFont(gFont("Regular",size))
            except Exception as exc:optional_failure("player.zap_header_font",exc)
            cache["subtitle"]=subtitle
        if force or cache.get("counter")!=counter:
            self["counter"].setText(counter);cache["counter"]=counter
        normal=(getattr(self,"assets",{}) or {}).get("normal");selected=(getattr(self,"assets",{}) or {}).get("selected")
        for row in range(self.VISIBLE):
            idx=self.offset+row
            if 0<=idx<total:
                if is_categories:
                    label=self._category_name(rows[idx])
                    display=("▶  "+label) if idx==self.cursor and label else label
                    row_font=self._row_font_size(display)
                else:
                    label=self._name(rows[idx],idx)[:45]
                    display=("▶  "+label) if idx==self.cursor and label else label
                    row_font=22
                number=str(idx+1)
            else:
                display="";number="";row_font=22
            bg=selected if idx==self.cursor and idx<total else normal
            state=(self.mode,idx,display,number,bg,row_font)
            if not force and cache.get(("row",row))==state:continue
            self["row%d"%row].setText(display);self["num%d"%row].setText(number)
            try:
                if self["row%d"%row].instance is not None:self["row%d"%row].instance.setFont(gFont("Regular",row_font))
            except Exception as exc:optional_failure("player.zap_row_font",exc)
            try:
                if bg and os.path.isfile(bg):self["bg%d"%row].instance.setPixmapFromFile(bg);self["bg%d"%row].show()
                else:self["bg%d"%row].hide()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            cache[("row",row)]=state

    def _move(self,delta):
        rows=self.categories if self.mode=="categories" else self.channels
        if not rows:return
        total=len(rows);old=self.cursor
        self._last_move_direction=1 if int(delta)>0 else -1
        self.cursor=(self.cursor+1)%total if int(delta)>0 else (self.cursor-1)%total
        if self.cursor==0 and old==total-1:self.offset=0
        elif self.cursor==total-1 and old==0:self.offset=max(0,total-self.VISIBLE)
        elif self.cursor<self.offset:self.offset=self.cursor
        elif self.cursor>=self.offset+self.VISIBLE:self.offset=max(0,min(self.cursor-self.VISIBLE+1,total-self.VISIBLE))
        if self.mode=="channels":
            self._channel_cursor=self.cursor;self._channel_offset=self.offset
            self._load_page_for_index(self.cursor,urgent=True);self._warm_visible_pages(include_adjacent=True)
        else:
            self.category_cursor=self.cursor;self.category_offset=self.offset
        # Cache-aware repaint: within the same window only the old/new selected
        # rows plus header/counter change.  Do not rebind all 15 row pixmaps on
        # every remote repeat.
        self._render(force=False)

    def _page_shift(self,direction):
        rows=self.categories if self.mode=="categories" else self.channels
        if not rows:return
        total=len(rows);row=max(0,self.cursor-self.offset);max_offset=max(0,total-self.VISIBLE)
        if int(direction)>0:
            if self.offset>=max_offset:self.cursor=total-1;self.offset=max_offset
            else:self.offset=min(max_offset,self.offset+self.VISIBLE);self.cursor=min(total-1,self.offset+row)
        else:
            if self.offset<=0:self.offset=0;self.cursor=0
            else:self.offset=max(0,self.offset-self.VISIBLE);self.cursor=min(total-1,self.offset+row)
        if self.mode=="channels":
            self._last_move_direction=1 if int(direction)>0 else -1
            self._channel_cursor=self.cursor;self._channel_offset=self.offset
            self._load_page_for_index(self.cursor,urgent=True);self._warm_visible_pages(include_adjacent=True)
        else:
            self.category_cursor=self.cursor;self.category_offset=self.offset
        self._render(force=False)

    def page_up(self):self._page_shift(-1)
    def page_down(self):self._page_shift(1)

    def _select_current_category(self):
        if not self.categories:
            self.cursor=0;self.offset=0;return
        target=str(self.current_category_id or "")
        found=None
        for i,row in enumerate(self.categories):
            if target and self._category_id(row)==target:found=i;break
        if found is None:found=max(0,min(int(self.category_cursor or 0),len(self.categories)-1))
        self.cursor=found;self.offset=max(0,min(found,max(0,len(self.categories)-self.VISIBLE)))
        self.category_cursor=self.cursor;self.category_offset=self.offset

    def _show_categories(self):
        if self.mode=="categories":return
        self._channel_cursor=self.cursor;self._channel_offset=self.offset
        # Any category-page request becomes stale the instant BACK returns to the
        # category model. Existing channel page cache itself stays warm.
        if self._nav_pending:self._nav_generation+=1;self._nav_pending=False
        self.mode="categories";self._select_current_category();self._render_cache.clear();self._set_mode_labels()
        if not self.categories:self._request_categories()
        self._render(force=True)

    def _enter_category(self,category):
        if not isinstance(category,dict):return
        cid=self._category_id(category);title=self._category_name(category)
        if not cid:return
        if cid==str(self.current_category_id or "") and self.channels:
            self.mode="channels";self.current_category_title=title or self.current_category_title
            self.cursor=max(0,min(self._channel_cursor,max(0,len(self.channels)-1)))
            self.offset=max(0,min(self._channel_offset,max(0,len(self.channels)-self.VISIBLE)))
            self._render_cache.clear();self._set_mode_labels();self._render(force=True);self._warm_visible_pages(include_adjacent=True);return
        self._request_category_first_page(category)

    def keyOK(self):
        if self.mode=="categories":
            if 0<=self.cursor<len(self.categories):self._enter_category(self.categories[self.cursor])
            return
        if 0<=self.cursor<len(self.channels) and isinstance(self.channels[self.cursor],dict):
            self._channel_cursor=self.cursor;self._channel_offset=self.offset
            self.close({
                "type":"channel","index":int(self.cursor),"item":dict(self.channels[self.cursor]),
                "category_id":str(self.current_category_id or ""),"category_title":str(self.current_category_title or _("Live TV")),
                "channels":list(self.channels),"page_size":int(self.page_size or 1),"total":int(self.total or len(self.channels)),
                "categories":[dict(x) for x in (self.categories or []) if isinstance(x,dict)],
            })

    def _cancel(self):
        if self.mode=="channels":self._show_categories()
        else:self.close(None)

    def _cleanup(self):
        try:self.timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        future=getattr(self,"_nav_future",None)
        if future is not None:
            try:future.cancel()
            except Exception:pass
        for future in list(getattr(self,"_page_futures",{}).values()):
            try:future.cancel()
            except Exception as exc:optional_failure("player.zap_page_future_cancel",exc)
        try:self._page_futures.clear();self._pending_pages.clear()
        except Exception:pass
        try:
            if self.timer_conn is not None:self.timer_conn.disconnect()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            if self._drain in self.timer.callback:self.timer.callback.remove(self._drain)
        except Exception as exc:optional_failure("player.zap_timer_callback_remove",exc)


class UltraInfobarVisibility(object):
    """Ultra Stalker InfoBar visibility policy.

    The Screen itself remains the rendering authority.  This controller owns
    only three pieces of state: whether the bar is visible, how many callers
    are holding it open, and the one-shot hide timer.
    """

    HIDE_DELAY_MS = 6000

    def __init__(self):
        self._us_infobar_visible = True
        self._us_infobar_hold_depth = 0
        self._us_infobar_skip_toggle_once = False
        self._us_infobar_tracker = ServiceEventTracker(
            screen=self,
            eventmap={iPlayableService.evStart: self.serviceStarted},
        )
        self.hideTimer = eTimer()
        try:
            self.hideTimer_conn = self.hideTimer.timeout.connect(self.doTimerHide)
        except Exception:
            self.hideTimer.callback.append(self.doTimerHide)
        self.onShow.append(self._us_infobar_on_show)
        self.onHide.append(self._us_infobar_on_hide)
        self.startHideTimer()

    def _us_infobar_on_show(self):
        self._us_infobar_visible = True
        self.startHideTimer()

    def _us_infobar_on_hide(self):
        self._us_infobar_visible = False
        try:
            self.hideTimer.stop()
        except Exception:
            pass

    def infobarVisible(self):
        return bool(self._us_infobar_visible)

    def serviceStarted(self):
        # A new service should surface the playback controls just like a fresh
        # player entry, but only while this Screen is the active dialog.
        if getattr(self, "execing", False):
            self.doShow()

    def _us_infobar_modal_hold_active(self):
        """Return True while a Player-owned modal overlay must keep the InfoBar visible.

        The explicit hold depth remains the primary authority.  The subtitle
        flags are also checked directly so a queued Enigma2 hide event cannot
        win a race between stopping the timer and painting the inline picker.
        """
        if int(getattr(self, "_us_infobar_hold_depth", 0) or 0) > 0:
            return True
        if bool(getattr(self, "_subtitle_inline_locked", False)):
            return True
        if bool(getattr(self, "_subtitle_native_selector_hold", False)):
            return True
        if bool(getattr(self, "_server_search_locked", False)):
            return True
        if bool(getattr(self, "_server_search_mode_active", False)):
            return True
        try:
            overlay = getattr(self, "_subtitle_inline_overlay", None)
            if overlay is not None and bool(getattr(overlay, "active", False)):
                return True
            search_overlay = getattr(self, "_server_search_inline_overlay", None)
            if search_overlay is not None and bool(getattr(search_overlay, "active", False)):
                return True
        except Exception:
            pass
        return False

    def startHideTimer(self):
        try:
            self.hideTimer.stop()
        except Exception:
            pass
        if self._us_infobar_visible and not self._us_infobar_modal_hold_active():
            try:
                self.hideTimer.start(self.HIDE_DELAY_MS, True)
            except Exception:
                pass
        self._us_infobar_skip_toggle_once = False

    def doShow(self):
        try:
            self.hideTimer.stop()
        except Exception:
            pass
        self._us_infobar_visible = True
        self.show()
        self.startHideTimer()

    def doTimerHide(self):
        try:
            self.hideTimer.stop()
        except Exception:
            pass
        # Subtitle selection is modal inside the Player.  Never let an already
        # queued one-shot timer make the picker invisible while it still owns
        # navigation/OK.
        if self._us_infobar_modal_hold_active():
            self._us_infobar_visible = True
            try: Screen.show(self)
            except Exception: pass
            return
        if self._us_infobar_visible:
            self._us_infobar_visible = False
            self.hide()

    def _toggle_infobar(self):
        if self._us_infobar_skip_toggle_once:
            self._us_infobar_skip_toggle_once = False
            return
        # Do not allow a second InfoBar action map to hide the Player while a
        # subtitle picker is consuming OK/arrow keys.
        if self._us_infobar_modal_hold_active():
            try:self.hideTimer.stop()
            except Exception:pass
            self._us_infobar_visible = True
            try:Screen.show(self)
            except Exception:pass
            return
        if self._us_infobar_visible:
            try:
                self.hideTimer.stop()
            except Exception:
                pass
            self._us_infobar_visible = False
            self.hide()
            return
        self.doShow()

    def OkPressed(self):
        self._toggle_infobar()

    def lockShow(self):
        self._us_infobar_hold_depth += 1
        try:
            self.hideTimer.stop()
        except Exception:
            pass
        if getattr(self, "execing", False) and not self._us_infobar_visible:
            self._us_infobar_visible = True
            self.show()

    def unlockShow(self):
        if self._us_infobar_hold_depth > 0:
            self._us_infobar_hold_depth -= 1
        if getattr(self, "execing", False) and self._us_infobar_visible:
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
        title = str(item.get("name") or item.get("title") or item.get("episode_name") or _("Next Episode"))
        number = item.get("episode") or item.get("number") or item.get("episode_id") or ""
        self["title"] = Label(_("Play Next Episode"))
        self["episode"] = Label((_("Episode %s  •  %s") % (number, title))[:70] if number else title[:70])
        self["countdown"] = Label("")
        self["hint"] = Label(_("OK Play now   •   BACK Cancel   •   YELLOW Disable autoplay"))
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
        self["countdown"].setText(_("Next episode starts in %d seconds") % self.remaining)

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
