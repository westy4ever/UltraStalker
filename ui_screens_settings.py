"""Settings screens extracted from ui.py without changing class behavior."""

from Screens.Screen import Screen
from Screens.InputBox import InputBox
from Components.Input import Input
from enigma import gFont
from skin import parseColor
from .ui_async import AsyncScreenMixin
from .ui_image_loader import ImageLoaderMixin
from .ui_transition import TransitionMixin
from .ui_fixed_adaptive import fixed_settings_rows, fixed_value_color, fixed_settings_chrome, cleanup_legacy_application_outputs
from .ui_helpers import content_refresh_feedback, content_refresh_failure
from .storage import load_server_library, import_server_library_profiles
from .localization import set_plugin_language, language_name
from .language_catalog import interface_language_choices, description_language_choices, description_language_name, default_description_for_interface
from .category_visibility import category_id as _visibility_category_id, hidden_ids as _profile_hidden_ids
import queue as _queue
import time as _time
from .log import memory_snapshot as _mem34
import shutil as _shutil

BROWSER_SKIN = ""


# Settings message-card authority.  The approved About card is the single
# visual master for passive informational text throughout Settings and any
# Settings-owned popup reused by Browser/Portal List.  Long copy is paged
# inside the same card instead of growing a giant one-off rectangle.
_SETTINGS_NOTICE_MASTER_W = 560
_SETTINGS_NOTICE_MASTER_H = 260
_SETTINGS_NOTICE_INNER_W = _SETTINGS_NOTICE_MASTER_W - 72
_SETTINGS_NOTICE_FONT = 20
_SETTINGS_NOTICE_MAX_LINES = 6

def _settings_notice_wrap_line(line, font_size=_SETTINGS_NOTICE_FONT, max_px=_SETTINGS_NOTICE_INNER_W):
    text=str(line or "")
    if not text:
        return [""]
    words=text.split(" ")
    out=[]; current=""
    for word in words:
        candidate=word if not current else current+" "+word
        try: fits=_settings_text_width_px(candidate,font_size)<=max_px
        except Exception: fits=len(candidate)<=48
        if fits:
            current=candidate
            continue
        if current:
            out.append(current);current=""
        # Very long paths/tokens still stay inside the master card.  Split only
        # when a token cannot fit by itself; normal prose remains word-wrapped.
        token=str(word or "")
        while token:
            take=len(token)
            while take>1:
                part=token[:take]
                try: part_fits=_settings_text_width_px(part,font_size)<=max_px
                except Exception: part_fits=len(part)<=48
                if part_fits: break
                take-=1
            part=token[:max(1,take)]
            out.append(part)
            token=token[len(part):]
        current=""
    if current or not out: out.append(current)
    return out

def _settings_notice_pages(message, font_size=_SETTINGS_NOTICE_FONT, max_px=_SETTINGS_NOTICE_INNER_W, max_lines=_SETTINGS_NOTICE_MAX_LINES):
    wrapped=[]
    for raw in str(message or "").splitlines() or [""]:
        wrapped.extend(_settings_notice_wrap_line(raw,font_size,max_px))
    # Trim useless page-edge blank lines while preserving intentional paragraph
    # spacing inside a page.
    pages=[]
    step=max(1,int(max_lines or 1))
    for i in range(0,len(wrapped),step):
        chunk=list(wrapped[i:i+step])
        while chunk and chunk[0]=="": chunk.pop(0)
        while chunk and chunk[-1]=="": chunk.pop()
        pages.append("\n".join(chunk) if chunk else "")
    return pages or [""]

def _settings_notice_title(base,index,total):
    title=str(base or "Information")
    return ("%s  •  %d/%d"%(title,int(index)+1,int(total))) if int(total or 0)>1 else title

SETTINGS_HOME_GRID_SKIN = """<screen name="NovaSettingsScreen" position="center,center" size="1920,1080" backgroundColor="#02070b" flags="wfNoBorder">
 <widget name="ambient_bg" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
 <widget name="hero_backdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
 <widget name="list" position="50,20" size="430,1040" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
 <widget name="context_panel" position="1120,600" size="730,360" alphatest="blend" scale="1" transparent="1" zPosition="10"/>
 <widget name="context_title" position="1160,618" size="650,40" font="Regular;29" foregroundColor="#ffffff" transparent="1" zPosition="11" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="context_subtitle" position="1160,658" size="650,28" font="Regular;17" foregroundColor="#91b7ca" transparent="1" zPosition="11" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="context_message" position="1160,700" size="650,150" font="Regular;20" foregroundColor="#f4f8fb" noWrap="0" transparent="1" zPosition="11" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="context_list" position="1160,700" size="650,220" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="12"/>
 <widget name="context_hint" position="1160,925" size="650,28" font="Regular;16" halign="center" valign="center" foregroundColor="#a9c1d0" transparent="1" zPosition="12" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="description" position="650,920" size="1210,92" font="Regular;25" halign="right" valign="center" foregroundColor="#ffffff" noWrap="0" shadowColor="#000000" shadowOffset="2,2" transparent="1" zPosition="9"/>
 <widget name="status" position="540,1028" size="1320,34" font="Regular;18" halign="right" valign="center" foregroundColor="#b7d9eb" shadowColor="#000000" shadowOffset="1,1" transparent="1" zPosition="9"/>
</screen>"""

def configure_settings_screens(**deps):
    globals().update(deps)
    if "BROWSER_SKIN" in deps:
        AdvancedSettingsScreen.skin = deps["BROWSER_SKIN"]
        scaler=deps.get("font_scale_skin")
        NovaSettingsScreen.skin = scaler(SETTINGS_HOME_GRID_SKIN) if callable(scaler) else SETTINGS_HOME_GRID_SKIN
        # Apply the same global readability setting to settings-owned popup skins.
        if callable(scaler):
            for cls in (SettingsGlassChoiceScreen,SettingsGlassNoticeScreen,SettingsWebCleanerReadyScreen,SettingsGlassInputScreen,ServerLibraryScreen):
                try:
                    raw=getattr(cls,"skin","")
                    if isinstance(raw,str) and raw:cls.skin=scaler(raw)
                except Exception:
                    pass

def _settings_popup_key(source,suffix):
    try: stamp=os.path.getmtime(source) if source and os.path.isfile(source) else 0
    except Exception: stamp=0
    return hashlib.sha1((str(source or "")+"|"+str(stamp)+"|"+str(suffix or "settings-popup")).encode("utf-8","ignore")).hexdigest()[:16]


def _settings_category_backdrop():
    """Return the shared bundled background used across the approved pages."""
    fallback = asset("category_palestine_static_1920x1080.jpg")
    return fallback if fallback and os.path.isfile(fallback) else ""


class SettingsGlassChoiceScreen(Screen):
    skin = """<screen name="SettingsGlassChoiceScreen" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
      <widget name="background" position="0,0" size="1,1" alphatest="blend" scale="1" transparent="1" zPosition="0"/>
      <widget name="panel" position="680,650" size="1040,300" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
      <widget name="header_icon" position="0,0" size="1,1" alphatest="blend" scale="1" transparent="1" zPosition="4"/>
      <widget name="title" position="730,665" size="940,42" font="Regular;30" foregroundColor="#ffffff" transparent="1" zPosition="5" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="subtitle" position="730,704" size="940,26" font="Regular;17" foregroundColor="#91b7ca" transparent="1" zPosition="5" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="list" position="730,742" size="940,160" transparent="1" zPosition="6"/>
      <widget name="hint" position="730,910" size="940,30" font="Regular;17" halign="center" valign="center" foregroundColor="#a9c1d0" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
    </screen>"""
    def __init__(self,session,title,choices,selection=0,subtitle="Choose an option"):
        Screen.__init__(self,session)
        self._choices=[(_(str(c[0])),c[1]) if isinstance(c,(tuple,list)) and len(c)>1 else c for c in list(choices or [])]
        self._initial_selection=int(selection or 0)
        self._choice_chrome={}
        self["background"]=Pixmap();self["panel"]=Pixmap();self["header_icon"]=Pixmap()
        self["title"]=Label(_(str(title or "Settings")));self["subtitle"]=Label(_(str(subtitle or "")))
        self["hint"]=Label(_("OK  Select   •   BACK  Cancel"))
        self["list"]=IconMenuList([],width=980,item_height=76,icon_size=0,primary_font=24,secondary_font=17,row_style="settings_dialog")
        self["actions"]=ActionMap(["OkCancelActions","DirectionActions"],{
            "cancel":lambda:self.close(None),"ok":self._accept,
            "up":lambda:self["list"].wrap_up(),"down":lambda:self["list"].wrap_down(),
            "left":lambda:self["list"].page_left(),"right":lambda:self["list"].page_right(),
        },-1)
        self.onLayoutFinish.append(self._finish)

    def _geom(self,name,x,y,w,h):
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self[name].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                inst.resize(eSize(int(round(w*sx)),int(round(h*sy))))
        except Exception as exc:optional_failure("ui.settings_choice_geom",exc)

    def _finish(self):
        count=max(1,len(self._choices))
        labels=[str(c[0]) if isinstance(c,(tuple,list)) and c else str(c) for c in self._choices]
        numeric_like=bool(labels) and all(
            len(x)<=16 and (
                not any(ch.isalpha() for ch in x)
                or x.upper().endswith(("MB","GB","MS","S","%"))
            ) for x in labels
        )
        label_px=max([_settings_text_width_px(x,23) for x in labels] or [120])
        title_px=_settings_text_width_px(self["title"].getText(),30)
        subtitle_px=_settings_text_width_px(self["subtitle"].getText(),17)
        row_pad=52 if numeric_like else 70
        list_w=max(250,min(1000,label_px+row_pad))
        row_h=58
        visible=min(4,count)
        list_h=max(row_h,visible*row_h)

        # Right-side context panel: the full-screen layer is transparent, so
        # the Settings rail and the untouched hero stay visible behind it.
        region_x,region_y,region_w,region_h=560,590,1290,390
        heading_w=max(title_px,subtitle_px)+76
        panel_w=max(420,min(1120,max(list_w+72,heading_w)))
        panel_h=max(230,min(region_h,118+list_h+48))
        panel_x=region_x+max(0,(region_w-panel_w)//2)
        panel_y=max(560,1000-panel_h)
        list_x=panel_x+(panel_w-list_w)//2
        list_y=panel_y+82
        list_h=min(list_h,max(row_h,panel_h-132))

        self._geom("background",0,0,1,1)
        self._geom("panel",panel_x,panel_y,panel_w,panel_h)
        self._geom("header_icon",0,0,1,1)
        self._geom("title",panel_x+38,panel_y+16,panel_w-76,38)
        self._geom("subtitle",panel_x+38,panel_y+52,panel_w-76,24)
        self._geom("list",list_x,list_y,list_w,list_h)
        self._geom("hint",panel_x+42,panel_y+panel_h-38,panel_w-84,28)
        try:
            self["background"].hide();self["header_icon"].hide()
        except Exception as exc:optional_failure("ui.settings_choice_context_hide",exc)

        chrome=fixed_settings_chrome(panel_w,panel_h,list_w,max(40,row_h-8))
        self._choice_chrome=chrome
        panel=chrome.get("panel")
        if panel and os.path.isfile(panel):
            self["panel"].instance.setPixmapFromFile(panel);self["panel"].show()
        try:
            if self["list"].instance is not None:
                self["list"].instance.setSelectionEnable(0)
                self["list"].instance.setTransparent(1)
                self["list"].instance.setScrollbarMode(2)
            self["list"].row_width=list_w
            self["list"].set_layout(row_h,0,23,16,row_style="settings_dialog")
        except Exception as exc:optional_failure("ui.settings_choice_context_list",exc)
        selection=max(0,min(self._initial_selection,len(self._choices)-1)) if self._choices else 0
        row_asset=chrome.get("row")
        selected_asset=chrome.get("selected")
        self._choice_row_asset=row_asset; self._choice_selected_asset=selected_asset
        rows=[]
        for i,c in enumerate(self._choices):
            label=labels[i]
            details={"selected":i==selection,"row_asset":row_asset,"row_selected_asset":selected_asset,"meta":"","settings_compact":numeric_like}
            rows.append((label,None,c,details))
        self["list"].set_icon_rows(rows)
        try:self["list"].moveToIndex(selection)
        except Exception as exc:optional_failure("ui.settings_choice_index",exc)
        self._last_idx=selection;self["list"].onSelectionChanged.append(self._selection_changed)

    def _selection_changed(self):
        try:idx=self["list"].getSelectedIndex()
        except Exception:return
        old=getattr(self,"_last_idx",idx)
        if old==idx:return
        self._last_idx=idx
        chrome=getattr(self,"_choice_chrome",{}) or {}
        for j in set((old,idx)):
            if 0<=j<len(self._choices):
                c=self._choices[j]
                label=str(c[0]) if isinstance(c,(tuple,list)) and c else str(c)
                details={"selected":j==idx,"row_asset":getattr(self,"_choice_row_asset",chrome.get("row")),"row_selected_asset":getattr(self,"_choice_selected_asset",chrome.get("selected")),"meta":"","settings_compact":False}
                self["list"].update_icon_row(j,(label,None,c,details))
        try:self["list"].l.invalidate()
        except Exception as exc:optional_failure("ui.settings_choice_refresh",exc)

    def _accept(self):
        try:idx=self["list"].getSelectedIndex()
        except Exception:idx=-1
        self.close(self._choices[idx] if 0<=idx<len(self._choices) else None)


class SettingsGlassNoticeScreen(Screen):
    skin = """<screen name="SettingsGlassNoticeScreen" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
      <widget name="background" position="0,0" size="1,1" alphatest="blend" transparent="1" zPosition="0"/>
      <widget name="panel" position="720,680" size="1000,260" alphatest="blend" transparent="1" zPosition="2"/>
      <widget name="header_icon" position="0,0" size="1,1" alphatest="blend" scale="1" transparent="1" zPosition="4"/>
      <widget name="title" position="760,695" size="920,42" font="Regular;30" halign="left" foregroundColor="#ffffff" transparent="1" zPosition="5" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="info_bg" position="0,0" size="1,1" alphatest="blend" transparent="1" zPosition="3"/>
      <widget name="message" position="770,745" size="900,110" font="Regular;22" halign="left" valign="top" noWrap="0" foregroundColor="#f4f8fb" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="button1" position="900,865" size="280,52" alphatest="blend" transparent="1" zPosition="3"/>
      <widget name="button2" position="0,0" size="1,1" alphatest="blend" transparent="1" zPosition="3"/>
      <widget name="yes" position="900,865" size="280,52" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="no" position="0,0" size="1,1" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
    </screen>"""
    def __init__(self,session,title,message,yesno=False):
        Screen.__init__(self,session);self._yesno=bool(yesno);self._answer=True
        self._notice_title_base=_(str(title or "Information"))
        self._notice_message=_(str(message or ""))
        self._notice_pages=_settings_notice_pages(self._notice_message) if not self._yesno else [self._notice_message]
        self._notice_page_idx=0
        self["background"]=Pixmap();self["panel"]=Pixmap();self["header_icon"]=Pixmap();self["info_bg"]=Pixmap();self["button1"]=Pixmap();self["button2"]=Pixmap()
        self["title"]=Label(self._notice_title_base);self["message"]=Label(self._notice_pages[0])
        self["yes"]=Label(_("Yes") if self._yesno else "");self["no"]=Label(_("No") if self._yesno else "")
        self["actions"]=ActionMap(["OkCancelActions","DirectionActions"],{
            "cancel":lambda:self.close(False if self._yesno else None),
            "ok":lambda:self.close(self._answer if self._yesno else None),
            "left":lambda:self._choose_answer(True) if self._yesno else self._notice_page(-1),
            "right":lambda:self._choose_answer(False) if self._yesno else self._notice_page(1),
            "up":lambda:self._notice_page(-1) if not self._yesno else None,
            "down":lambda:self._notice_page(1) if not self._yesno else None,
        },-1)
        self.onLayoutFinish.append(self._finish)

    def _notice_page(self,step):
        if self._yesno or len(self._notice_pages)<=1:return
        self._notice_page_idx=(self._notice_page_idx+int(step or 0))%len(self._notice_pages)
        self["title"].setText(_settings_notice_title(self._notice_title_base,self._notice_page_idx,len(self._notice_pages)))
        self["message"].setText(self._notice_pages[self._notice_page_idx])

    def _geom(self,name,x,y,w,h):
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self[name].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                inst.resize(eSize(max(1,int(round(w*sx))),max(1,int(round(h*sy)))))
        except Exception as exc:optional_failure("ui.settings_notice_geom",exc)

    def _choose_answer(self,answer):
        if not self._yesno:return
        self._answer=bool(answer);self._paint_buttons()

    def _paint_buttons(self):
        chrome=getattr(self,"_notice_chrome",{}) or {}
        normal=chrome.get("row");selected=chrome.get("selected") or normal
        try:
            p1=selected if (not self._yesno or self._answer) else normal
            if p1 and os.path.isfile(p1):self["button1"].instance.setPixmapFromFile(p1);self["button1"].show()
            if self._yesno:
                p2=selected if not self._answer else normal
                if p2 and os.path.isfile(p2):self["button2"].instance.setPixmapFromFile(p2);self["button2"].show()
        except Exception as exc:optional_failure("ui.settings_notice_buttons",exc)

    def _finish(self):
        title=str(self["title"].getText() or "")
        message=str(self["message"].getText() or "")

        # Issue 6: About is the one passive-message master.  Do not resize the
        # glass around each sentence; wrap/page the sentence inside the approved
        # 560x260 card instead.  Confirmations retain room for their controls.
        if self._yesno:
            raw_lines=message.splitlines() or [""]
            title_px=_settings_text_width_px(title,30)
            max_line_px=max([_settings_text_width_px(line,22) for line in raw_lines] or [280])
            panel_w=max(700,min(900,max(title_px+105,min(max_line_px+120,900))))
            message_w=max(420,panel_w-96)
            wrapped=0
            for line in raw_lines:
                px=max(1,_settings_text_width_px(line,22))
                wrapped+=max(1,int((px+message_w-1)//message_w))
            msg_font=22 if wrapped<=5 else (19 if wrapped<=9 else 17)
            line_h=30 if msg_font>=22 else (26 if msg_font>=19 else 23)
            footer_h=76
            message_h=max(70,min(330,wrapped*line_h+16))
            panel_h=max(220,min(470,82+message_h+footer_h))
            available=max(70,panel_h-82-footer_h)
            message_h=min(message_h,available)
        else:
            panel_w=_SETTINGS_NOTICE_MASTER_W
            panel_h=_SETTINGS_NOTICE_MASTER_H
            message_w=_SETTINGS_NOTICE_INNER_W
            msg_font=_SETTINGS_NOTICE_FONT
            footer_h=20
            message_h=174
            self["title"].setText(_settings_notice_title(self._notice_title_base,self._notice_page_idx,len(self._notice_pages)))
            self["message"].setText(self._notice_pages[self._notice_page_idx])
        panel_x=1850-panel_w
        panel_y=max(500,998-panel_h)

        self._geom("background",0,0,1,1)
        self._geom("panel",panel_x,panel_y,panel_w,panel_h)
        self._geom("header_icon",0,0,1,1)
        self._geom("title",panel_x+36,panel_y+16,panel_w-72,42)
        self._geom("message",panel_x+36,panel_y+66,panel_w-72,message_h)
        self._geom("info_bg",0,0,1,1)
        try:
            from enigma import gFont
            if self["message"].instance is not None:self["message"].instance.setFont(gFont("Regular",msg_font))
        except Exception as exc:optional_failure("ui.settings_notice_context_font",exc)

        if self._yesno:
            by=panel_y+panel_h-62
            gap=28;bw=260;total=bw*2+gap;bx=panel_x+(panel_w-total)//2
            self._geom("button1",bx,by,bw,50);self._geom("yes",bx,by,bw,50)
            self._geom("button2",bx+bw+gap,by,bw,50);self._geom("no",bx+bw+gap,by,bw,50)
        else:
            # Informational notices are text-only.  The removed Close button
            # lane is returned to the message so long status text does not clip.
            for name in ("button1","button2","yes","no"):
                self._geom(name,0,0,1,1)

        try:
            self["background"].hide();self["header_icon"].hide();self["info_bg"].hide()
        except Exception as exc:optional_failure("ui.settings_notice_context_hide",exc)

        chrome=fixed_settings_chrome(panel_w,panel_h,270,50)
        self._notice_chrome=chrome
        try:
            panel=chrome.get("panel")
            if panel and os.path.isfile(panel):self["panel"].instance.setPixmapFromFile(panel);self["panel"].show()
        except Exception as exc:optional_failure("ui.settings_notice_context_panel",exc)
        if self._yesno:
            self._paint_buttons()
        else:
            for name in ("button1","button2","yes","no"):
                try:self[name].hide()
                except Exception as exc:optional_failure("ui.settings_notice_hide",exc)

class SettingsWebCleanerReadyScreen(Screen):
    """Short-URL pairing screen with optional one-scan QR bootstrap."""
    skin = """<screen name="SettingsWebCleanerReadyScreen" position="0,0" size="1920,1080" backgroundColor="#02060b" flags="wfNoBorder">
      <widget name="background" position="0,0" size="1920,1080" alphatest="off" scale="1" transparent="0" zPosition="0"/>
      <widget name="panel" position="390,180" size="1140,720" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
      <widget name="title" position="460,214" size="1000,56" font="Regular;34" foregroundColor="#ffffff" transparent="1" zPosition="5" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="info_bg" position="470,305" size="980,425" alphatest="blend" scale="1" transparent="1" zPosition="3"/>
      <widget name="lead" position="515,345" size="525,55" font="Regular;22" foregroundColor="#edf5f8" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="url" position="515,410" size="525,55" font="Regular;25" foregroundColor="#7ed8ff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="code_title" position="515,500" size="525,38" font="Regular;20" foregroundColor="#a8c5d4" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="code" position="515,540" size="525,75" font="Regular;44" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="note" position="515,625" size="525,70" font="Regular;18" foregroundColor="#a8c5d4" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="qr" position="1090,350" size="300,300" alphatest="blend" scale="1" transparent="1" zPosition="6"/>
      <widget name="scan" position="1060,665" size="360,34" font="Regular;18" halign="center" foregroundColor="#a8c5d4" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="button" position="810,790" size="300,58" alphatest="blend" transparent="1" zPosition="3"/>
      <widget name="close" position="810,790" size="300,58" font="Regular;21" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
    </screen>"""
    def __init__(self,session,url,pair_code="",qr_path="",access_mode="protected"):
        Screen.__init__(self,session)
        self._access_mode="protected" if str(access_mode or "").lower()=="protected" else "easy"
        self._qr_path=str(qr_path or "") if self._access_mode=="protected" else ""
        self["background"]=Pixmap();self["panel"]=Pixmap();self["info_bg"]=Pixmap();self["qr"]=Pixmap();self["button"]=Pixmap()
        self["title"]=Label(_("Web Cleaner Ready"))
        self["lead"]=Label(_("Open this address on the same local network:"))
        self["url"]=Label(str(url or ""))
        if self._access_mode=="protected":
            self["code_title"]=Label(_("PAIRING CODE"))
            self["code"]=Label(str(pair_code or "------"))
            self["note"]=Label(_("Enter the code once. Your browser keeps the secure session until Web Cleaner is stopped."))
            self["scan"]=Label(_("Scan QR for instant access") if self._qr_path else _("QR unavailable • use the 6-digit code"))
        else:
            self["code_title"]=Label(_("EASY ACCESS"))
            self["code"]=Label(_("NO PIN"))
            self["note"]=Label(_("Direct access is limited to devices on the local network. Use Stop Service when finished."))
            self["scan"]=Label(_("PIN / QR protection can be enabled from Settings"))
        self["close"]=Label(_("Close"))
        self["actions"]=ActionMap(["OkCancelActions"],{"cancel":self.close,"ok":self.close},-1)
        self.onLayoutFinish.append(self._finish)

    def _finish(self):
        source=_settings_popup_source()
        try:
            popup_bg=_settings_popup_backdrop(source)
            if popup_bg and os.path.isfile(popup_bg):self["background"].instance.setPixmapFromFile(popup_bg);self["background"].show()
        except Exception as exc:optional_failure("ui.webcleaner_ready_bg",exc)
        try:
            outer=fixed_settings_chrome(1140,720,300,58)
            inner=fixed_settings_chrome(980,425,300,58)
            mapping=(("panel",outer.get("panel")),("info_bg",inner.get("panel")),("button",outer.get("selected")))
            for name,path in mapping:
                if path and os.path.isfile(path):self[name].instance.setPixmapFromFile(path);self[name].show()
        except Exception as exc:optional_failure("ui.webcleaner_ready_chrome",exc)
        try:
            if self._qr_path and os.path.isfile(self._qr_path):
                self["qr"].instance.setPixmapFromFile(self._qr_path);self["qr"].show()
            else:self["qr"].hide()
        except Exception as exc:
            optional_failure("ui.webcleaner_ready_qr",exc)
            try:self["qr"].hide()
            except Exception:pass


class SettingsGlassInputScreen(InputBox):
    TEXT = Input.TEXT
    PIN = Input.PIN
    NUMBER = Input.NUMBER
    skin = """<screen name="SettingsGlassInputScreen" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
      <widget name="background" position="0,0" size="1,1" alphatest="blend" transparent="1" zPosition="0"/>
      <widget name="panel" position="700,690" size="1040,250" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
      <widget name="header_icon" position="0,0" size="1,1" alphatest="blend" scale="1" transparent="1" zPosition="4"/>
      <widget name="text" position="750,710" size="940,48" font="Regular;28" foregroundColor="#ffffff" transparent="1" zPosition="5" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="input_bg" position="750,785" size="940,64" alphatest="blend" scale="1" transparent="1" zPosition="3"/>
      <widget name="input" position="775,794" size="890,46" font="Regular;23" foregroundColor="#ffffff" backgroundColor="#00000000" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
      <widget name="hint" position="750,880" size="940,30" font="Regular;17" halign="center" valign="center" foregroundColor="#a9c1d0" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
    </screen>"""
    def __init__(self,session,title="",windowTitle=None,useableChars=None,**kwargs):
        safe_title = str(_(str(title or "")) or str(title or ""))
        safe_window_title = str(windowTitle or safe_title or "Ultra Stalker")
        InputBox.__init__(self,session,title=safe_title,windowTitle=safe_window_title,useableChars=useableChars,**kwargs)
        self["background"]=Pixmap();self["panel"]=Pixmap();self["header_icon"]=Pixmap();self["input_bg"]=Pixmap()
        self["hint"]=Label(_("OK  Save   •   BACK  Cancel"))
        self.onLayoutFinish.append(self._glass_finish)

    def _geom(self,name,x,y,w,h):
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self[name].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                inst.resize(eSize(max(1,int(round(w*sx))),max(1,int(round(h*sy)))))
        except Exception as exc:optional_failure("ui.settings_input_geom",exc)

    def _glass_finish(self):
        title=str(self["text"].getText() or "")
        title_px=_settings_text_width_px(title,28)
        region_x,region_y,region_w,region_h=560,620,1290,350
        panel_w=max(700,min(1120,title_px+170))
        input_w=panel_w-100;panel_h=270
        panel_x=region_x+max(0,(region_w-panel_w)//2)
        panel_y=max(560,1000-panel_h)
        self._geom("background",0,0,1,1)
        self._geom("panel",panel_x,panel_y,panel_w,panel_h)
        self._geom("header_icon",0,0,1,1)
        self._geom("text",panel_x+44,panel_y+22,panel_w-88,46)
        self._geom("input_bg",panel_x+50,panel_y+92,input_w,66)
        self._geom("input",panel_x+74,panel_y+102,input_w-48,46)
        self._geom("hint",panel_x+50,panel_y+panel_h-48,input_w,30)
        try:
            self["background"].hide();self["header_icon"].hide()
        except Exception as exc:optional_failure("ui.settings_input_context_hide",exc)
        chrome=fixed_settings_chrome(panel_w,panel_h,input_w,64)
        for name,keyname in (("panel","panel"),("input_bg","row")):
            path=chrome.get(keyname)
            try:
                if path and os.path.isfile(path):
                    self[name].instance.setPixmapFromFile(path);self[name].show()
            except Exception as exc:optional_failure("ui.settings_input_context_chrome",exc)


class ServerLibraryScreen(Screen):
    """Passive local Portal/Xtream catalog. Parsing happens only when this screen opens."""
    skin = """<screen name="ServerLibraryScreen" position="center,center" size="1920,1080" backgroundColor="#02070b" flags="wfNoBorder">
     <widget name="ambient_bg" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
     <widget name="hero_backdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
     <widget name="list" position="50,20" size="620,1040" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
     <widget name="status" position="720,1015" size="1140,34" font="Regular;18" halign="right" valign="center" foregroundColor="#b7d9eb" shadowColor="#000000" shadowOffset="1,1" transparent="1" zPosition="9"/>
    </screen>"""
    ROW_W = 620
    ROW_H = 80

    def __init__(self, session, kind="portal"):
        Screen.__init__(self, session)
        self.kind = "xtream" if str(kind or "").lower() == "xtream" else "portal"
        self._rows = list(load_server_library(self.kind))
        self._selected = set()
        self._visual_index = -1
        self._rebuilding = False
        self._row_asset = asset("series_floating_row.png")
        self._row_selected_asset = asset("series_floating_row_selected.png")
        self._value_color = int("74d8ff", 16)
        self["ambient_bg"] = Pixmap(); self["hero_backdrop"] = Pixmap()
        self["list"] = IconMenuList([], width=self.ROW_W, item_height=self.ROW_H, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self["status"] = Label("")
        self["list"].onSelectionChanged.append(self._selection_changed)
        self["actions"] = ActionMap(["OkCancelActions","ColorActions","DirectionActions"], {
            "cancel": self.close,
            "ok": self._toggle,
            "green": self._import_selected,
            "red": self._clear_selected,
            "yellow": self._select_all,
            "up": lambda:self["list"].wrap_up(),
            "down": lambda:self["list"].wrap_down(),
            "left": lambda:self["list"].page_left(),
            "right": lambda:self["list"].page_right(),
        }, -1)
        self.onLayoutFinish.append(self._layout_ready)
        self.onClose.append(self._cleanup)
        self._render(0)

    def _cleanup(self):
        try:
            callbacks=self["list"].onSelectionChanged
            if self._selection_changed in callbacks: callbacks.remove(self._selection_changed)
        except Exception as exc: optional_failure("ui.server_library_cleanup",exc)

    def _layout_ready(self):
        prepared=""; ambient=""
        try:
            with open(HOME_HERO_FILE,"r",encoding="utf-8") as h:
                state=json.load(h)
            hero=state.get("hero") if isinstance(state,dict) and isinstance(state.get("hero"),dict) else {}
            prepared=_settings_category_backdrop(); ambient=""
        except Exception: pass
        if not (prepared and os.path.isfile(prepared)): prepared=asset("category_palestine_static_1920x1080.jpg")
        try:
            if ambient and os.path.isfile(ambient): self["ambient_bg"].instance.setPixmapFromFile(ambient); self["ambient_bg"].show()
            else: self["ambient_bg"].hide()
        except Exception as exc: optional_failure("ui.server_library_ambient",exc)
        try:
            if prepared and os.path.isfile(prepared): self["hero_backdrop"].instance.setPixmapFromFile(prepared); self["hero_backdrop"].show()
        except Exception as exc: optional_failure("ui.server_library_backdrop",exc)
        self._update_status()

    def _index(self):
        try:return int(self["list"].getSelectedIndex() or 0)
        except Exception:return 0

    def _icon(self, picked=False):
        base="multi_search.png" if picked else ("channel_list.png" if self.kind=="xtream" else "web_access.png")
        compact=asset("settings_icons_40/"+base)
        return compact if compact and os.path.isfile(compact) else asset("settings_icons/"+base)

    def _name(self, profile, idx):
        name=str((profile or {}).get("name") or "").strip()
        if name:return name
        return ((_("Xtream Server %d") if self.kind=="xtream" else _("Portal Server %d"))%(idx+1))

    def _value(self, profile, picked):
        marker="SELECTED • " if picked else ""
        if self.kind=="portal": value=str((profile or {}).get("mac") or "")
        else:
            raw=str((profile or {}).get("portal") or "")
            try:value=str(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(raw).hostname or "XTREAM")
            except Exception:value="XTREAM"
        return (marker+value)[:38]

    def _row(self, idx, current=False):
        profile=self._rows[idx]; picked=idx in self._selected
        meta={"selected":bool(current),"value":self._value(profile,picked),"row_asset":self._row_asset,"row_selected_asset":self._row_selected_asset,"value_color":self._value_color}
        return (self._name(profile,idx),self._icon(picked),profile,meta)

    def _render(self, selected=0):
        selected=max(0,min(int(selected or 0),max(0,len(self._rows)-1))) if self._rows else 0
        rows=[self._row(i,i==selected) for i in range(len(self._rows))]
        self._rebuilding=True
        try:
            self["list"].set_icon_rows(rows)
            if rows:self["list"].moveToIndex(selected)
        finally:self._rebuilding=False
        self._visual_index=selected; self._update_status()

    def _selection_changed(self):
        if self._rebuilding:return
        idx=self._index(); old=self._visual_index
        if idx==old:return
        self._visual_index=idx
        for j in set((old,idx)):
            if 0<=j<len(self._rows): self["list"].update_icon_row(j,self._row(j,j==idx))
        self._update_status()

    def _toggle(self):
        idx=self._index()
        if not (0<=idx<len(self._rows)):return
        if idx in self._selected:self._selected.remove(idx)
        else:self._selected.add(idx)
        self["list"].update_icon_row(idx,self._row(idx,True)); self._update_status()

    def _clear_selected(self):
        if not self._selected:return
        old=list(self._selected); self._selected.clear()
        for idx in old:
            if 0<=idx<len(self._rows):self["list"].update_icon_row(idx,self._row(idx,idx==self._index()))
        self._update_status()

    def _select_all(self):
        self._selected=set(range(len(self._rows))); self._render(self._index())

    def _import_selected(self):
        if not self._selected:
            self["status"].setText(_("Nothing selected • OK Toggle • GREEN Import Selected • BACK Settings")); return
        chosen=[self._rows[i] for i in sorted(self._selected) if 0<=i<len(self._rows)]
        try: result=import_server_library_profiles(chosen) or {}
        except Exception as exc:
            optional_failure("ui.server_library_import",exc); self["status"].setText(_("Import failed")); return
        added=int(result.get("added",0) or 0); dup=int(result.get("duplicates",0) or 0)
        self._selected.clear(); self._render(self._index())
        self["status"].setText(_("Import complete • %d added • %d already existed • no server checks were run")%(added,dup))

    def _update_status(self):
        title=_("Xtream Library") if self.kind=="xtream" else _("Portal Library")
        if not self._rows:
            self["status"].setText(_("%s • Library is empty • BACK Settings")%title); return
        self["status"].setText(_("%s • %d entries • %d selected • OK Toggle • GREEN Import • YELLOW Select all • RED Clear • BACK Settings")%(title,len(self._rows),len(self._selected)))


class AdvancedSettingsScreen(Screen, TransitionMixin):
    """Low-frequency tuning options kept separate from everyday settings."""
    skin = BROWSER_SKIN

    def __init__(self, session, profile=None):
        Screen.__init__(self, session)
        self.profile = profile or {}
        self.entries = []
        self["page_adaptive_bg"] = Pixmap()
        self["brand_header"] = Pixmap()
        self["title"] = Label(_("Advanced Settings"))
        self["counter"] = Label(_("Expert controls"))
        self["section"] = Label(_("Performance, filtering and integration"))
        self["list"] = IconMenuList([], width=1090, item_height=84, icon_size=48, primary_font=27, secondary_font=18, row_style="settings_portal")
        self["preview"] = Pixmap(); self["accent_frame"] = Pixmap()
        self["info_card1"] = Pixmap(); self["info_card2"] = Pixmap(); self["info_card3"] = Pixmap()
        self["info_icon1"] = Pixmap(); self["info_icon2"] = Pixmap(); self["info_icon3"] = Pixmap()
        self["info_title"] = Label(_("Advanced Settings"))
        self["info"] = Label(_("Change only the options you understand. Defaults are safe for most receivers."))
        self["status"] = Label(_("Changes are saved immediately"))
        self["red"] = Label(_("Back")); self["green"] = Label(_("Change"))
        self["yellow"] = Label(""); self["blue"] = Label(_("Select"))
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "cancel": self.close, "red": self.close, "ok": self.select, "green": self.select,
            "blue": self.select, "yellow": lambda: None,
            "up": lambda: self["list"].wrap_up(), "down": lambda: self["list"].wrap_down(),
            "left": lambda: self["list"].page_left(), "right": lambda: self["list"].page_right(),
        }, -1)
        self["list"].onSelectionChanged.append(self._selection_changed)
        self.onLayoutFinish.append(self._finish_layout)
        self.refresh()

    def _finish_layout(self):
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self["list"].instance
            if inst is not None:
                inst.move(ePoint(int(round(24*sx)),int(round(18*sy))))
                inst.resize(eSize(int(round(928*sx)),int(round(1026*sy))))
                inst.setSelectionEnable(0)
                inst.setTransparent(1)
                inst.setScrollbarMode(2)
            self["list"].row_width=928
            self["list"].set_layout(54,38,22,15,"settings_portal")
        except Exception as exc:optional_failure("ui.advanced_list_layout",exc)

        try:self["page_adaptive_bg"].hide()
        except Exception as exc:optional_failure("ui.advanced_bg_hide",exc)
        for _n in ("brand_header","title","section","counter","top_divider"):
            try:self[_n].hide()
            except Exception as exc:optional_failure("ui.advanced_header_hide",exc)

        def _geom(name,x,y,w,h):
            try:
                desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
                inst=self[name].instance
                if inst is not None:
                    inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                    inst.resize(eSize(int(round(w*sx)),int(round(h*sy))))
            except Exception as exc:optional_failure("ui.advanced_geom",exc)

        # Keep the large Settings icon centred, then build one clean vertical
        # information stack under it. All three cards share identical geometry.
        _geom("preview",1260,132,360,260)
        _geom("accent_frame",0,0,1,1)

        for name,y in (("info_card1",438),("info_card2",532),("info_card3",626)):
            _geom(name,1110,y,660,78)
        for name,y in (("info_icon1",453),("info_icon2",547),("info_icon3",641)):
            _geom(name,1134,y,48,48)
        for name,y in (("info_title",438),("info",532),("status",626)):
            _geom(name,1200,y,540,78)

        try:
            chrome=_neutral_utility_glass()
            card=chrome.get("side") or chrome.get("info")
            if card and os.path.isfile(card):
                for n in ("info_card1","info_card2","info_card3"):
                    self[n].instance.setPixmapFromFile(card);self[n].show()

            # Same three semantic icons used by the main Settings screen.
            right_icons=(
                ("info_icon1","us89_folder_yellow_42.png"),
                ("info_icon2","settings_icons_40/web_access.png"),
                ("info_icon3","settings_icons_40/progress_safe.png"),
            )
            for n,filename in right_icons:
                self[n].instance.setPixmapFromFile(asset(filename));self[n].show()

            self["preview"].instance.setPixmapFromFile(asset("us81_section_settings_530x382.png"));self["preview"].show()
            self["accent_frame"].hide()
        except Exception as exc:optional_failure("ui.advanced_glass_layout",exc)

        try:
            idx=self["list"].getSelectedIndex()
        except Exception:idx=0
        try:self._render_advanced_rows(idx)
        except Exception as exc:optional_failure("ui.advanced_rows_layout",exc)


    @staticmethod
    def _safe_clear_dir_contents(path):
        removed_files=0
        removed_bytes=0
        try:
            if not path or not os.path.isdir(path):
                return removed_files,removed_bytes
            for name in os.listdir(path):
                target=os.path.join(path,name)
                try:
                    if os.path.islink(target) or os.path.isfile(target):
                        try:removed_bytes+=int(os.path.getsize(target) or 0)
                        except Exception:pass
                        os.unlink(target);removed_files+=1
                    elif os.path.isdir(target):
                        for base,_dirs,files in os.walk(target):
                            for fn in files:
                                fp=os.path.join(base,fn)
                                try:removed_bytes+=int(os.path.getsize(fp) or 0);removed_files+=1
                                except Exception:pass
                        _shutil.rmtree(target)
                except Exception as exc:
                    optional_failure("ui.cache_clear_item",exc)
        except Exception as exc:
            optional_failure("ui.cache_clear_dir",exc)
        return removed_files,removed_bytes

    def _clear_temporary_cache(self):
        removed_files=0;removed_bytes=0
        # /tmp only. Never follow this action onto /media/hdd.
        for path in ("/tmp/UltraStalker","/tmp/ultrastalker","/tmp/UltraStalker-subtitles"):
            f,b=self._safe_clear_dir_contents(path);removed_files+=f;removed_bytes+=b
        self.refresh()
        self["status"].setText(_("Temporary cache cleared • %d files • %.1f MB. Persistent HDD artwork kept.")%(removed_files,removed_bytes/(1024.0*1024.0)))

    def _confirm_clear_persistent_artwork(self,choice):
        if choice != "delete":
            self["status"].setText(_("Persistent artwork was not changed"))
            return
        if not hdd_read_ready(force=True):
            self["status"].setText(_("Persistent HDD is not available; nothing was deleted"))
            return
        removed_files=0;removed_bytes=0
        # Artwork-only clear. Metadata/index, settings, portals and history are
        # deliberately outside these roots and survive this action.
        for path in PERSISTENT_ARTWORK_DIRS:
            f,b=self._safe_clear_dir_contents(path);removed_files+=f;removed_bytes+=b
        try:
            state_path=self._global_artwork_state_path()
            if state_path and os.path.isfile(state_path):os.remove(state_path)
        except Exception as exc:optional_failure("ui.global_artwork_state_clear",exc)
        self._global_artwork_last={}
        self.refresh()
        self["status"].setText(_("Persistent artwork cleared • %d files • %.1f MB. Settings and metadata kept.")%(removed_files,removed_bytes/(1024.0*1024.0)))

    def refresh(self):
        cfg = load_settings()
        self.entries = [
            (_("Show Channel Numbers"), "show_channel_numbers", _("ON") if cfg.get("show_channel_numbers", True) else _("OFF"), _("Display channel numbers in Live TV lists.")),
            (_("Hide Empty Categories"), "hide_empty_categories", _("ON") if cfg.get("hide_empty_categories", True) else _("OFF"), _("Hide categories detected as empty after loading.")),
            (_("Hide Adult Categories"), "hide_adult", _("ON") if cfg.get("hide_adult", True) else _("OFF"), _("Hide categories matching adult keywords when Parental Lock is disabled.")),
            (_("Bouquet Proxy Port"), "proxy_port", str(cfg.get("proxy_port", 17999)), _("Local loopback port used by dynamic bouquet playback and XMLTV integration.")),
            (_("EPG Refresh Budget"), "epg_refresh_budget", _("%ss") % cfg.get("epg_refresh_budget", 45), _("Maximum background XMLTV refresh time budget.")),
            (_("Search Scan Pages"), "search_max_pages", str(cfg.get("search_max_pages", 250)), _("Maximum catalogue pages scanned when native portal search is unavailable.")),
            (_("Search Time Budget"), "search_time_budget", _("%ss") % cfg.get("search_time_budget", 12), _("Maximum time spent searching one portal before partial results are returned.")),
            (_("Image Cache Limit"), "image_cache_mb", _("%d MB") % cfg.get("image_cache_mb", 128), _("Maximum persistent artwork cache size.")),
            (_("Diagnostic Logging"), "diagnostic_logging", _("ON") if cfg.get("diagnostic_logging", False) else _("OFF"), _("Enable verbose rotating diagnostic logs after plugin restart.")),
        ]
        self._render_advanced_rows(getattr(self,"_advanced_last_idx",0))
        self["counter"].setText(_("%d options") % len(self.entries))
        self._selection_changed()

    @staticmethod
    def _advanced_row_icon():
        compact=asset("settings_icons_40/advanced.png")
        return compact if compact and os.path.isfile(compact) else asset("settings_icons/advanced.png")

    def _render_advanced_rows(self,selected=0):
        chrome=_neutral_utility_glass()
        rows=[]
        selected=max(0,min(int(selected or 0),max(0,len(self.entries)-1)))
        for i,(title,action,value,_desc) in enumerate(self.entries):
            details={
                "selected":i==selected,
                "value":value,
                "row_asset":chrome.get("settings_row"),
                "row_selected_asset":chrome.get("settings_selected"),
            }
            rows.append((title,self._advanced_row_icon(),action,details))
        self["list"].row_width=928
        self["list"].set_layout(54,38,22,15,"settings_portal")
        self["list"].set_icon_rows(rows)
        try:self["list"].moveToIndex(selected)
        except Exception as exc:optional_failure("ui.advanced_row_index",exc)
        self._advanced_last_idx=selected

    def _selection_changed(self):
        try:idx=self["list"].getSelectedIndex()
        except Exception:return
        if not (0<=idx<len(self.entries)):return
        old=getattr(self,"_advanced_last_idx",idx)
        self._advanced_last_idx=idx
        if old!=idx:
            try:
                chrome=_neutral_utility_glass()
                for j in set((old,idx)):
                    if 0<=j<len(self.entries):
                        title,action,value,_desc=self.entries[j]
                        details={
                            "selected":j==idx,
                            "value":value,
                            "row_asset":chrome.get("settings_row"),
                            "row_selected_asset":chrome.get("settings_selected"),
                        }
                        self["list"].update_icon_row(j,(title,self._advanced_row_icon(),action,details))
                self["list"].l.invalidate()
            except Exception as exc:optional_failure("ui.advanced_selection_glass",exc)
        title,_action,value,desc=self.entries[idx]
        self["info_title"].setText(title)
        self["info"].setText(str(desc)[:52])
        self["status"].setText(_("Current: %s")%str(value)[:32])


    def select(self):
        try: idx=self["list"].getSelectedIndex()
        except Exception: return
        if not (0 <= idx < len(self.entries)): return
        action=self.entries[idx][1]
        cfg=load_settings()
        if action in ("show_channel_numbers", "hide_empty_categories", "hide_adult", "diagnostic_logging"):
            save_settings({action: not bool(cfg.get(action, False))})
            self.refresh(); self["status"].setText(_("Setting updated"))
        elif action == "proxy_port":
            self.session.openWithCallback(self._save_proxy_port, SettingsGlassInputScreen, title=_("Bouquet proxy port (1024-65535)"), text=str(cfg.get("proxy_port",17999)), maxSize=5)
        elif action == "epg_refresh_budget":
            self.session.openWithCallback(lambda c:self._save_choice("epg_refresh_budget",c), SettingsGlassChoiceScreen,_("EPG refresh budget"),[(_("%d seconds")%x,x) for x in (15,30,45,60,90,120,180)])
        elif action == "search_max_pages":
            self.session.openWithCallback(lambda c:self._save_choice("search_max_pages",c), SettingsGlassChoiceScreen,_("Maximum search scan pages"),[(str(x),x) for x in (25,50,100,150,250)])
        elif action == "search_time_budget":
            self.session.openWithCallback(lambda c:self._save_choice("search_time_budget",c), SettingsGlassChoiceScreen,_("Search time budget per portal"),[(_("%d seconds")%x,x) for x in (5,8,12,15,20,30)])
        elif action == "image_cache_mb":
            self.session.openWithCallback(lambda c:self._save_choice("image_cache_mb",c), SettingsGlassChoiceScreen,_("Image cache limit"),[(_("%d MB")%x,x) for x in (40,80,128,180,256,512,1024)])

    def _save_choice(self, key, choice):
        if not choice: return
        save_settings({key:int(choice[1])}); self.refresh(); self["status"].setText(_("Setting saved"))

    def _save_proxy_port(self, value):
        if value is None: return
        try:
            port=int(str(value).strip())
            if not 1024 <= port <= 65535: raise ValueError()
            save_settings({"proxy_port":port}); self.refresh(); self["status"].setText(_("Proxy port saved. Restart the plugin before exporting bouquets again."))
        except Exception:
            self.session.open(SettingsGlassNoticeScreen,_("Invalid port"),_("Enter a port number between 1024 and 65535."),False)

    def reset_wizard(self):
        try:
            if os.path.exists(WIZARD_DONE_FILE): os.unlink(WIZARD_DONE_FILE)
            save_settings({"first_run_wizard":True})
            self["status"].setText(_("First-run wizard will start on the next plugin launch."))
        except Exception as exc:
            self["status"].setText(_("Unable to reset wizard: %s") % exc)


class NovaSettingsScreen(Screen, AsyncScreenMixin, ImageLoaderMixin, TransitionMixin):
    """Full-HD settings surface; sub-pickers intentionally use native Enigma2 dialogs."""
    skin = SETTINGS_HOME_GRID_SKIN
    SETTINGS_ROW_W = 430
    SETTINGS_ROW_H = 80
    SETTINGS_VISIBLE_ROWS = 13

    def __init__(self, session, profile, client=None):
        Screen.__init__(self, session)
        self._async_init(); self.onClose.append(self._stop_async); self.onClose.append(self._image_stop)
        self.profile = profile or {}
        self.client = client or PortalSession(self.profile, timeout=load_settings().get("timeout", 10)).client
        self.entries = []
        self._global_artwork_running=False
        self._global_artwork_progress=_queue.Queue()
        self._global_artwork_last={}
        # Main Settings deliberately reuses the approved floating episode-row
        # grammar: one narrow rail below the Ultra Stalker logo, leaving the hero
        # artwork unobstructed on the right.
        self["title"] = Label(_("Settings"))
        self["counter"] = Label("")
        self["section"] = Label("")
        self["list"] = IconMenuList([], width=self.SETTINGS_ROW_W, item_height=self.SETTINGS_ROW_H, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self["preview"] = Pixmap(); self["accent_frame"] = Pixmap()
        self["info_title"] = Label(_("Portal Settings"))
        self["info"] = Label(_("Choose an option to configure playback, lists and privacy."))
        self["status"] = Label(_("ARROWS Navigate  •  OK Select  •  BACK Home"))
        self["description"] = Label("")
        self["red"] = Label(_("Back")); self["green"] = Label(_("Change"))
        self["yellow"] = Label(_("Diagnostics")); self["blue"] = Label(_("Select"))
        self["ambient_bg"] = Pixmap(); self["hero_backdrop"] = Pixmap()
        # Inline Right Context Panel lives on this same Settings screen.  It is
        # hidden until OK is pressed, so opening a setting never creates a new
        # full-screen Screen and can never replace the hero with a black layer.
        self["context_panel"] = Pixmap()
        self["context_title"] = Label("")
        self["context_subtitle"] = Label("")
        self["context_message"] = Label("")
        self["context_hint"] = Label("")
        self["context_list"] = IconMenuList([], width=650, item_height=58, icon_size=0, primary_font=22, secondary_font=16, row_style="settings_dialog")
        self._context_active = False
        self._context_mode = None
        self._context_callback = None
        self._context_choices = []
        self._context_last_idx = 0
        self._context_row_asset = None
        self._context_selected_asset = None
        self._settings_row_asset = asset("series_floating_row.png")
        self._settings_row_selected_asset = asset("series_floating_row_selected.png")
        self._settings_value_color = int("74d8ff",16)
        try:
            _fixed=fixed_settings_rows() or {}
            _n=str(_fixed.get("normal") or "");_s=str(_fixed.get("selected") or "")
            if _n and os.path.isfile(_n):self._settings_row_asset=_n
            if _s and os.path.isfile(_s):self._settings_row_selected_asset=_s
            self._settings_value_color=int(_fixed.get("value_color") or fixed_value_color(self._settings_value_color))
        except Exception as exc:optional_failure("ui.settings_fixed_init",exc)
        self._settings_visual_index = -1
        self._settings_rebuilding = False
        self._settings_section = None
        self._settings_root_last_idx = 0
        self._settings_section_last_idx = {}
        # Preserve the proven 5.0.2 lifecycle and action plumbing.
        self._image_init("preview", (1,1), self.profile, self.client)
        self["list"].onSelectionChanged.append(self._selection_changed)
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions","UltraStalkerMenuActions", "DirectionActions"], {
            "cancel": self._settings_cancel, "red": self._settings_cancel,
            "ok": self._settings_ok, "blue": self._settings_ok, "green": self._settings_ok,
            "yellow": self._settings_diagnostics, "menu": self._settings_diagnostics,
            "up": lambda: self._settings_move("up"), "down": lambda: self._settings_move("down"),
            "left": lambda: self._settings_move("left"), "right": lambda: self._settings_move("right"),
        }, -1)
        self.onLayoutFinish.append(self._layout_ready)
        self.onClose.append(self._mem34_settings_close)
        self.refresh()
        _mem34("settings_init_done")

    def _mem34_settings_close(self):
        _mem34("settings_close", section=str(getattr(self,"_settings_section","") or "root"))

    def _context_geom(self,name,x,y,w,h):
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self[name].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                inst.resize(eSize(max(1,int(round(w*sx))),max(1,int(round(h*sy)))))
        except Exception as exc:optional_failure("ui.settings_inline_context_geom",exc)

    def _context_hide_widgets(self):
        for name in ("context_panel","context_title","context_subtitle","context_message","context_list","context_hint"):
            try:self[name].hide()
            except Exception as exc:optional_failure("ui.settings_inline_context_hide",exc)

    def _settings_cancel(self):
        if getattr(self,"_context_active",False):
            self._context_close(None, cancelled=True)
            return
        if getattr(self,"_settings_section",None):
            try:self._settings_section_last_idx[self._settings_section]=int(self["list"].getSelectedIndex() or 0)
            except Exception:pass
            self._settings_section=None
            self._settings_last_idx=int(getattr(self,"_settings_root_last_idx",0) or 0)
            self.refresh()
            return
        self.close()

    def _settings_ok(self):
        if getattr(self,"_context_active",False):
            if self._context_mode=="choice":self._context_accept()
            else:self._context_close(None, cancelled=False)
        else:
            self.select()

    def _settings_diagnostics(self):
        if getattr(self,"_context_active",False):return
        self.open_diagnostics()

    def _settings_move(self,direction):
        if getattr(self,"_context_active",False) and self._context_mode=="notice":
            if direction in ("up","left"):self._context_notice_page(-1)
            elif direction in ("down","right"):self._context_notice_page(1)
            return
        target=self["context_list"] if getattr(self,"_context_active",False) and self._context_mode=="choice" else self["list"]
        try:
            if direction=="up":target.wrap_up()
            elif direction=="down":target.wrap_down()
            elif direction=="left":target.page_left()
            elif direction=="right":target.page_right()
        except Exception as exc:optional_failure("ui.settings_inline_context_move",exc)

    def _context_compact_episode_assets(self,card_w):
        """Resize the approved Settings episode-row glass horizontally only.

        The left Settings rail remains the visual source of truth.  Context
        choices reuse those exact normal/selected assets, with a 9-slice style
        horizontal resize so the rounded ends and adaptive glow keep their shape.
        """
        card_w=max(300,min(560,int(card_w or 426)))
        normal=str(getattr(self,"_settings_row_asset","") or "")
        selected=str(getattr(self,"_settings_row_selected_asset","") or "")
        if not (normal and selected and os.path.isfile(normal) and os.path.isfile(selected)):
            chrome=fixed_settings_rows() or {}
            normal=str(chrome.get("normal") or normal);selected=str(chrome.get("selected") or selected)
        if card_w==426:
            return normal,selected
        out=[]
        for src,kind in ((normal,"normal"),(selected,"selected")):
            if not (src and os.path.isfile(src)):
                out.append(src);continue
            try:
                stamp=int(os.path.getmtime(src))
                sig=hashlib.sha1((src+"|"+str(stamp)+"|"+str(card_w)+"|floating-choice-v1").encode("utf-8","ignore")).hexdigest()[:16]
                dst=os.path.join(os.path.dirname(src),"dyn_settings_context_%s_%s_%d.png"%(sig,kind,card_w))
                if not (os.path.isfile(dst) and os.path.getsize(dst)>64):
                    from PIL import Image as _SettingsImage
                    im=_SettingsImage.open(src).convert("RGBA")
                    sw,sh=im.size
                    target_h=72
                    if sh!=target_h:
                        resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                        im=im.resize((sw,target_h),resample);sw,sh=im.size
                    cap=max(36,min(72,sw//4,(card_w-16)//2))
                    resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                    if card_w>cap*2+8 and sw>cap*2+8:
                        canvas=_SettingsImage.new("RGBA",(card_w,target_h),(0,0,0,0))
                        left=im.crop((0,0,cap,target_h));right=im.crop((sw-cap,0,sw,target_h))
                        center=im.crop((cap,0,sw-cap,target_h)).resize((card_w-cap*2,target_h),resample)
                        canvas.paste(left,(0,0));canvas.paste(center,(cap,0));canvas.paste(right,(card_w-cap,0))
                    else:
                        canvas=im.resize((card_w,target_h),resample)
                    tmp=dst+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG");os.replace(tmp,dst)
                out.append(dst)
            except Exception as exc:
                optional_failure("ui.settings_context_episode_resize",exc);out.append(src)
        return (out+[None,None])[:2]

    def _context_prepare_floating_choices(self,title,subtitle,labels,count,show_text=False):
        # No enclosing panel.  The options themselves float over the untouched
        # backdrop exactly like the left Settings rows, only compact in width.
        label_px=max([_settings_text_width_px(x,22) for x in labels] or [220])
        card_w=max(300,min(560,label_px+58))
        list_w=card_w+8
        row_h=80
        visible=max(1,min(5,int(count or 1)))
        region_top,region_h=590,400
        list_h=visible*row_h
        list_x=1850-list_w
        list_y=region_top+max(0,(region_h-list_h)//2)
        if show_text:
            # Confirmations may need a short explanatory line.  Keep it floating
            # too; there is still no enclosing rectangle behind title/message.
            heading_w=min(740,max(420,_settings_text_width_px(str(subtitle or title or ""),17)+24))
            heading_x=1850-heading_w
            self._context_geom("context_title",heading_x,max(600,list_y-92),heading_w,36)
            self._context_geom("context_subtitle",heading_x,max(638,list_y-54),heading_w,52)
            list_y=max(list_y,748)
        self._context_geom("context_list",list_x,list_y,list_w,list_h)
        self._context_geom("context_panel",0,0,1,1)
        self._context_geom("context_message",0,0,1,1)
        self._context_geom("context_hint",0,0,1,1)
        self._context_row_asset,self._context_selected_asset=self._context_compact_episode_assets(card_w)
        try:
            if self["context_list"].instance is not None:
                self["context_list"].instance.setSelectionEnable(0)
                self["context_list"].instance.setTransparent(1)
                try:self["context_list"].instance.setScrollbarMode(2)
                except Exception:pass
            self["context_list"].row_width=list_w
            self["context_list"].set_layout(row_h,0,22,16,row_style="settings_dialog")
        except Exception as exc:optional_failure("ui.settings_floating_context_list_layout",exc)
        return list_w,row_h

    def _context_prepare_chrome(self,title,subtitle,labels,count):
        # Retained for About/Info notices. Choice lists use floating rows instead.
        label_px=max([_settings_text_width_px(x,22) for x in labels] or [220])
        title_px=_settings_text_width_px(title,29)
        subtitle_px=_settings_text_width_px(subtitle,17)
        visible=max(1,min(5,int(count or 1)))
        row_h=56
        panel_w=max(520,min(760,max(label_px+92,title_px+96,subtitle_px+96)))
        panel_h=max(230,min(390,112+visible*row_h+46))
        panel_x=1850-panel_w
        # R70: align the notice/context card above the footer/status lane.
        panel_y=max(560,998-panel_h)
        inner_x=panel_x+36;inner_w=panel_w-72
        list_y=panel_y+88;list_h=min(visible*row_h,max(row_h,panel_h-138))
        self._context_geom("context_panel",panel_x,panel_y,panel_w,panel_h)
        self._context_geom("context_title",inner_x,panel_y+16,inner_w,38)
        self._context_geom("context_subtitle",inner_x,panel_y+52,inner_w,28)
        # Notice cards keep the exact same adaptive panel geometry, but the old
        # footer hint consumed space the About text actually needs.  Reclaim that
        # lane for the message instead of enlarging or moving the panel.
        self._context_geom("context_message",inner_x,panel_y+68,inner_w,max(84,panel_h-88))
        self._context_geom("context_list",inner_x,list_y,inner_w,list_h)
        self._context_geom("context_hint",0,0,1,1)
        chrome=fixed_settings_chrome(panel_w,panel_h,inner_w,max(40,row_h-8)) or {}
        self._context_row_asset=chrome.get("row")
        self._context_selected_asset=chrome.get("selected")
        try:
            panel=chrome.get("panel")
            if panel and os.path.isfile(panel):self["context_panel"].instance.setPixmapFromFile(panel)
        except Exception as exc:optional_failure("ui.settings_inline_context_panel",exc)
        try:
            if self["context_list"].instance is not None:
                self["context_list"].instance.setSelectionEnable(0)
                self["context_list"].instance.setTransparent(1)
            self["context_list"].row_width=inner_w
            self["context_list"].set_layout(row_h,0,22,16,row_style="settings_dialog")
        except Exception as exc:optional_failure("ui.settings_inline_context_list_layout",exc)
        return inner_w,row_h

    def _context_open_choice(self,callback,title,choices,selection=0,subtitle="Choose an option",show_text=False):
        self._context_hide_widgets()
        try:self["description"].hide()
        except Exception:pass
        self._context_active=True;self._context_mode="choice"
        self._context_callback=callback;self._context_choices=list(choices or [])
        labels=[_(str(c[0])) if isinstance(c,(tuple,list)) and c else _(str(c)) for c in self._context_choices]
        title_text=_(str(title or "Settings")); subtitle_text=_(str(subtitle or ""))
        self._context_prepare_floating_choices(title_text,subtitle_text,labels,len(labels),show_text=show_text)
        self["context_title"].setText(title_text);self["context_subtitle"].setText(subtitle_text)
        self["context_hint"].setText("")
        selection=max(0,min(int(selection or 0),len(self._context_choices)-1)) if self._context_choices else 0
        rows=[]
        for i,c in enumerate(self._context_choices):
            details={"selected":i==selection,"row_asset":self._context_row_asset,"row_selected_asset":self._context_selected_asset,"meta":""}
            rows.append((labels[i],None,c,details))
        self["context_list"].set_icon_rows(rows)
        try:self["context_list"].moveToIndex(selection)
        except Exception as exc:optional_failure("ui.settings_inline_context_index",exc)
        self._context_last_idx=selection
        try:
            if self._context_selection_changed not in self["context_list"].onSelectionChanged:
                self["context_list"].onSelectionChanged.append(self._context_selection_changed)
        except Exception as exc:optional_failure("ui.settings_inline_context_hook",exc)
        try:self["context_list"].show()
        except Exception as exc:optional_failure("ui.settings_inline_context_show",exc)
        if show_text:
            for name in ("context_title","context_subtitle"):
                try:self[name].show()
                except Exception as exc:optional_failure("ui.settings_inline_context_text_show",exc)
        try:self["context_panel"].hide();self["context_message"].hide();self["context_hint"].hide()
        except Exception:pass

    def _context_selection_changed(self):
        if not getattr(self,"_context_active",False) or self._context_mode!="choice":return
        try:idx=self["context_list"].getSelectedIndex()
        except Exception:return
        old=getattr(self,"_context_last_idx",idx)
        if idx==old:return
        self._context_last_idx=idx
        for j in set((old,idx)):
            if 0<=j<len(self._context_choices):
                c=self._context_choices[j]
                label=str(c[0]) if isinstance(c,(tuple,list)) and c else str(c)
                details={"selected":j==idx,"row_asset":self._context_row_asset,"row_selected_asset":self._context_selected_asset,"meta":""}
                self["context_list"].update_icon_row(j,(label,None,c,details))
        try:self["context_list"].l.invalidate()
        except Exception as exc:optional_failure("ui.settings_inline_context_refresh",exc)

    def _context_open_notice(self,title,message,callback=None,yesno=False):
        if yesno:
            # Confirmations keep explicit Yes/No choices; only passive notices
            # become text-only adaptive cards.
            self._context_open_choice(callback,str(title or _("Confirm")),[(_("Yes"),True),(_("No"),False)],0,str(message or ""),show_text=True)
            return
        self._context_hide_widgets()
        try:self["description"].hide()
        except Exception:pass
        self._context_active=True;self._context_mode="notice"
        self._context_callback=callback;self._context_choices=[]
        message=_(str(message or ""))
        title_text=_(str(title or "Information"))

        # Issue 6: the approved About card is the sole passive message master.
        # Refresh/Update/Backup/etc. never grow the panel; long copy is paged
        # inside the same 560x260 glass.
        panel_w=_SETTINGS_NOTICE_MASTER_W
        panel_h=_SETTINGS_NOTICE_MASTER_H
        inner_w=_SETTINGS_NOTICE_INNER_W
        msg_font=_SETTINGS_NOTICE_FONT
        message_h=174
        panel_x=1850-panel_w
        panel_y=max(500,998-panel_h)
        self._context_notice_title_base=title_text
        self._context_notice_pages=_settings_notice_pages(message,msg_font,inner_w,_SETTINGS_NOTICE_MAX_LINES)
        self._context_notice_page_idx=0

        self._context_geom("context_panel",panel_x,panel_y,panel_w,panel_h)
        self._context_geom("context_title",panel_x+36,panel_y+16,inner_w,40)
        self._context_geom("context_subtitle",0,0,1,1)
        self._context_geom("context_message",panel_x+36,panel_y+68,inner_w,message_h)
        self._context_geom("context_list",0,0,1,1)
        self._context_geom("context_hint",0,0,1,1)
        chrome=fixed_settings_chrome(panel_w,panel_h,inner_w,48) or {}
        try:
            panel=chrome.get("panel")
            if panel and os.path.isfile(panel):self["context_panel"].instance.setPixmapFromFile(panel)
        except Exception as exc:optional_failure("ui.settings_inline_notice_panel",exc)
        try:
            if self["context_message"].instance is not None:self["context_message"].instance.setFont(gFont("Regular",msg_font))
        except Exception as exc:optional_failure("ui.settings_inline_notice_font",exc)

        self["context_title"].setText(_settings_notice_title(title_text,0,len(self._context_notice_pages)));self["context_subtitle"].setText("")
        self["context_message"].setText(self._context_notice_pages[0]);self["context_hint"].setText("")
        for name in ("context_panel","context_title","context_message"):
            try:self[name].show()
            except Exception as exc:optional_failure("ui.settings_inline_notice_show",exc)
        try:self["context_subtitle"].hide();self["context_list"].hide();self["context_hint"].hide()
        except Exception:pass

    def _context_notice_page(self,step):
        if not getattr(self,"_context_active",False) or getattr(self,"_context_mode",None)!="notice":return
        pages=list(getattr(self,"_context_notice_pages",[]) or [])
        if len(pages)<=1:return
        idx=(int(getattr(self,"_context_notice_page_idx",0) or 0)+int(step or 0))%len(pages)
        self._context_notice_page_idx=idx
        title=str(getattr(self,"_context_notice_title_base","") or "Information")
        self["context_title"].setText(_settings_notice_title(title,idx,len(pages)))
        self["context_message"].setText(pages[idx])

    def _context_accept(self):
        try:idx=self["context_list"].getSelectedIndex()
        except Exception:idx=-1
        value=self._context_choices[idx] if 0<=idx<len(self._context_choices) else None
        self._context_close(value,cancelled=False)

    def _context_close(self,value=None,cancelled=False):
        callback=getattr(self,"_context_callback",None)
        mode=getattr(self,"_context_mode",None)
        self._context_active=False;self._context_mode=None;self._context_callback=None;self._context_choices=[]
        self._context_notice_pages=[];self._context_notice_page_idx=0;self._context_notice_title_base=""
        self._context_hide_widgets()
        try:
            self["description"].show()
            self._selection_changed()
        except Exception:pass
        if callback is not None:
            try:callback(None if cancelled else value)
            except Exception as exc:optional_failure("ui.settings_inline_context_callback",exc)

    def _save_toggle_choice(self,action,key,choice):
        if not choice:return
        value=bool(choice[1]);cfg=load_settings()
        if action=="parental":
            if value and parental_pin_is_default(cfg):
                # Enabling parental control with the factory PIN used to be a dead end:
                # Settings refused the toggle and left the user on OFF.  Turn that into
                # one atomic flow: collect a real PIN, save it securely, then enable.
                self.session.openWithCallback(
                    self._save_parental_pin_and_enable,
                    SettingsGlassInputScreen,
                    title=_("Set parental PIN to enable lock (4-8 digits)"),
                    text="",
                    maxSize=8,
                    type=Input.PIN,
                )
                return
            save_settings({"parental_lock":value})
            # Never carry a previously unlocked parental session across an ON/OFF
            # state change.  The next protected category must reflect the new state.
            parental_lock_now()
            self.refresh()
            self["status"].setText(_("Parental lock enabled") if value else _("Parental lock disabled"))
            return
        save_settings({key:value});self.refresh();self["status"].setText(_("Setting updated"))

    def _settings_hero_visuals(self):
        """Return the bundled Settings background without reading Home hero state.

        Settings keeps its adaptive glass look, but the source artwork is now
        deterministic and local.  Opening Settings therefore never waits on or
        parses Home hero metadata.
        """
        prepared=_settings_category_backdrop()
        if not (prepared and os.path.isfile(prepared)):
            prepared=asset("category_palestine_static_1920x1080.jpg")
        menu=asset("home129_menu_fallback.png")
        focus_menu=asset("home129_menu_focus.png")
        return prepared,"",menu,focus_menu

    def _apply_settings_list_materials(self):
        try:cleanup_legacy_application_outputs()
        except Exception as exc:optional_failure("ui.fixed_adaptive_cleanup_settings",exc)
        prepared,ambient,_menu,_focus=self._settings_hero_visuals()
        try:
            if ambient:
                self["ambient_bg"].instance.setPixmapFromFile(ambient);self["ambient_bg"].show()
            else:self["ambient_bg"].hide()
        except Exception as exc:optional_failure("ui.settings_list_ambient",exc)
        try:
            if prepared and os.path.isfile(prepared):
                self["hero_backdrop"].instance.setPixmapFromFile(prepared);self["hero_backdrop"].show()
        except Exception as exc:optional_failure("ui.settings_list_backdrop",exc)

        # R63: one persistent utility-row pair copied from the approved frozen
        # application adaptive.  Settings only binds ready-made files; entering
        # and leaving the screen never rebuilds palette/chrome.
        try:
            chrome=fixed_settings_rows() or {}
            normal=str(chrome.get("normal") or "")
            selected=str(chrome.get("selected") or "")
            if normal and os.path.isfile(normal):self._settings_row_asset=normal
            if selected and os.path.isfile(selected):self._settings_row_selected_asset=selected
            self._settings_value_color=int(chrome.get("value_color") or fixed_value_color(self._settings_value_color))
            _accent=parseColor("#%06x" % (int(self._settings_value_color)&0xFFFFFF))
            for _name,_size in (("context_subtitle",19),("context_hint",18),("status",19)):
                try:
                    _inst=self[_name].instance
                    if _inst is not None:
                        _inst.setForegroundColor(_accent);_inst.setFont(gFont("Regular",_size))
                except Exception:pass
        except Exception as exc:optional_failure("ui.settings_episode_material",exc)

        try:
            if self["list"].instance is not None:self["list"].instance.setSelectionEnable(0)
        except Exception as exc:optional_failure("ui.settings_episode_selection",exc)

    def _grid_set_visible(self,slot,visible):
        for kind in ("grid_bg","grid_icon","grid_title","grid_value"):
            try:
                (self["%s%d"%(kind,slot)].show() if visible else self["%s%d"%(kind,slot)].hide())
            except Exception as exc:optional_failure("ui.settings_grid_visibility",exc)

    def _grid_text_px(self,text,size):
        text=str(text or "")
        try:
            return int(_settings_text_width_px(text,int(size)))
        except Exception:
            # Conservative fallback for images where the native Enigma2 font
            # measurement helper is unavailable during early layout.
            return int(len(text)*max(1,int(size))*0.58)

    def _grid_title_size(self,text):
        lines=str(text or "").split("\n") or [""]
        limit=max(80,int(self.GRID_TITLE_W)-10)
        for size in (18,17,16,15,14,13,12):
            if max([self._grid_text_px(line,size) for line in lines] or [0])<=limit:
                return size
        return 11

    def _grid_title_display(self,text):
        text=" ".join(str(text or "").split())
        if not text or " " not in text:
            return text
        # Keep short labels on one line. Longer labels are split at the point
        # that produces the most balanced *rendered* line widths, not merely
        # the closest character count.
        if self._grid_text_px(text,18)<=max(80,int(self.GRID_TITLE_W)-10):
            return text
        words=text.split(" ")
        best=None
        for cut in range(1,len(words)):
            a=" ".join(words[:cut]);b=" ".join(words[cut:])
            aw=self._grid_text_px(a,16);bw=self._grid_text_px(b,16)
            score=max(aw,bw)*10+abs(aw-bw)
            if best is None or score<best[0]:best=(score,a,b)
        return (best[1]+"\n"+best[2]) if best else text

    def _grid_value_size(self,text):
        limit=max(70,int(self.GRID_VALUE_W)-8)
        for size in (14,13,12,11,10):
            if self._grid_text_px(str(text or ""),size)<=limit:
                return size
        return 10

    def _grid_apply_title_font(self,slot,text):
        try:
            from enigma import gFont
            inst=self["grid_title%d"%slot].instance
            if inst is not None:inst.setFont(gFont("Regular",self._grid_title_size(text)))
            vinst=self["grid_value%d"%slot].instance
            if vinst is not None:vinst.setFont(gFont("Regular",self._grid_value_size(self["grid_value%d"%slot].getText())))
        except Exception as exc:optional_failure("ui.settings_grid_font",exc)

    def _grid_icon_path(self,title,action):
        name=(getattr(self,"_settings_icons",{}) or {}).get(str(action or ""),"settings_icons/advanced.png")
        base=os.path.basename(str(name or ""))
        compact=asset("settings_icons_40/"+base) if base else ""
        if compact and os.path.isfile(compact):return compact
        path=asset(name)
        return path if path and os.path.isfile(path) else asset("home_settings.png")

    def _render_grid_page(self,page=None):
        if page is None:
            try:idx=int(self["list"].getSelectedIndex() or 0)
            except Exception:idx=0
            page=idx//self.GRID_PAGE_SIZE
        page=max(0,int(page or 0));base=page*self.GRID_PAGE_SIZE
        for slot in range(self.GRID_PAGE_SIZE):
            absolute=base+slot
            if 0<=absolute<len(self.entries):
                title,_action,value,_desc=self.entries[absolute]
                self["grid_title%d"%slot].setText(self._grid_title_display(title))
                self["grid_value%d"%slot].setText(str(value))
                self._grid_apply_title_font(slot,self["grid_title%d"%slot].getText())
                self._grid_set_visible(slot,True)
                try:
                    icon=self._grid_icon_path(title,_action)
                    if icon and os.path.isfile(icon):
                        self["grid_icon%d"%slot].instance.setPixmapFromFile(icon);self["grid_icon%d"%slot].show()
                except Exception as exc:optional_failure("ui.settings_grid_icon_%d"%slot,exc)
            else:
                self["grid_title%d"%slot].setText("");self["grid_value%d"%slot].setText("")
                self._grid_set_visible(slot,False)
        self._grid_page=page

    def _grid_focus_index(self,idx):
        if not self.entries:return
        idx=max(0,min(int(idx or 0),len(self.entries)-1))
        page=idx//self.GRID_PAGE_SIZE
        if page!=getattr(self,"_grid_page",-1):self._render_grid_page(page)
        local=idx%self.GRID_PAGE_SIZE;row=local//self.GRID_COLS;col=local%self.GRID_COLS
        try:
            desk=getDesktop(0).size();sx=float(desk.width())/1920.0;sy=float(desk.height())/1080.0
            inst=self["grid_focus"].instance
            if inst is not None:
                inst.move(ePoint(int(round(self.GRID_X[col]*sx)),int(round(self.GRID_Y[row]*sy))))
                inst.resize(eSize(int(round(self.GRID_CARD_W*sx)),int(round(self.GRID_CARD_H*sy))))
            self["grid_focus"].show()
        except Exception as exc:optional_failure("ui.settings_grid_focus",exc)

    def _grid_move(self,dx,dy):
        total=len(self.entries)
        if total<=0:return
        try:idx=int(self["list"].getSelectedIndex() or 0)
        except Exception:idx=0
        page=idx//self.GRID_PAGE_SIZE;local=idx%self.GRID_PAGE_SIZE
        row=local//self.GRID_COLS;col=local%self.GRID_COLS
        target=idx
        if dx:
            # Horizontal navigation behaves exactly like a Home row: move only
            # inside the visible row and stop cleanly at its edge.  Page changes
            # are vertical, so LEFT/RIGHT never make a surprising jump.
            ncol=col+(1 if dx>0 else -1)
            if 0<=ncol<self.GRID_COLS:
                cand=page*self.GRID_PAGE_SIZE+row*self.GRID_COLS+ncol
                if cand<total and cand<(page+1)*self.GRID_PAGE_SIZE:target=cand
        elif dy:
            if dy>0:
                if row==0:
                    cand=page*self.GRID_PAGE_SIZE+self.GRID_COLS+col
                    if cand<min(total,(page+1)*self.GRID_PAGE_SIZE):target=cand
                    else:
                        cand=(page+1)*self.GRID_PAGE_SIZE+col
                        if cand<total:target=cand
                else:
                    next_base=(page+1)*self.GRID_PAGE_SIZE
                    if next_base<total:
                        last_col=max(0,min(self.GRID_COLS-1,total-next_base-1))
                        target=next_base+min(col,last_col)
            else:
                if row==1:
                    target=page*self.GRID_PAGE_SIZE+col
                elif page>0:
                    cand=(page-1)*self.GRID_PAGE_SIZE+self.GRID_COLS+col
                    if cand<total:target=cand
                    else:
                        cand=min(total-1,page*self.GRID_PAGE_SIZE-1)
                        if cand>=0:target=cand
        if target!=idx:
            try:self["list"].moveToIndex(target)
            except Exception as exc:optional_failure("ui.settings_grid_move",exc)

    def _layout_ready(self):
        _mem34("settings_layout_begin", section=str(getattr(self,"_settings_section","") or "root"))
        # Backdrop stays untouched; only the left episode-style Settings rail is
        # rendered over it. No right column and no visible scrollbar.
        self._apply_settings_list_materials()
        self._render_settings_rows(getattr(self,"_settings_last_idx",0))
        self._selection_changed()
        self._context_hide_widgets()
        _mem34("settings_layout_done", section=str(getattr(self,"_settings_section","") or "root"), rows=len(getattr(self,"entries",[]) or []))

    def _drain_jobs(self):
        AsyncScreenMixin._drain_jobs(self)
        if not self._screen_closed:
            self._drain_image_jobs()
            self._drain_global_artwork_progress()

    def _global_artwork_state_path(self):
        try:return os.path.join(os.path.dirname(str(IMAGE_CACHE_DIR).rstrip("/")),"global_artwork_preload.json")
        except Exception:return ""

    def _global_artwork_source_key(self):
        profile=dict(self.profile or {})
        raw="|".join((
            str(profile.get("portal") or profile.get("url") or "").rstrip("/").lower(),
            str(profile.get("mac") or "").upper(),
            str(profile.get("source_type") or profile.get("type") or "portal").lower(),
            str(profile.get("username") or profile.get("user") or profile.get("login") or "").strip().lower(),
        ))
        return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()[:20]

    def _global_artwork_source_label(self):
        profile=dict(self.profile or {})
        return str(profile.get("name") or profile.get("portal") or profile.get("url") or "Current source")

    def _load_global_artwork_state(self):
        path=self._global_artwork_state_path()
        if not path or not os.path.isfile(path):return {}
        try:
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            if not isinstance(data,dict):return {}
            sources=data.get("sources") if isinstance(data.get("sources"),dict) else {}
            state=sources.get(self._global_artwork_source_key()) if sources else None
            return dict(state) if isinstance(state,dict) else {}
        except Exception:return {}

    def _save_global_artwork_state(self,data):
        path=self._global_artwork_state_path()
        if not path:return
        try:
            old={}
            try:
                if os.path.isfile(path):
                    with open(path,"r",encoding="utf-8") as h:old=json.load(h)
            except Exception:old={}
            if not isinstance(old,dict) or not isinstance(old.get("sources"),dict):old={"schema":2,"sources":{}}
            old["schema"]=2;old.setdefault("sources",{})
            clean=dict(data or {});clean["source_key"]=self._global_artwork_source_key();clean["source_label"]=self._global_artwork_source_label()
            old["sources"][self._global_artwork_source_key()]=clean
            temp=path+".tmp.%d"%os.getpid()
            os.makedirs(os.path.dirname(path),exist_ok=True)
            with open(temp,"w",encoding="utf-8") as h:
                json.dump(old,h,ensure_ascii=False,sort_keys=True)
                h.flush();os.fsync(h.fileno())
            os.replace(temp,path)
        except Exception as exc:optional_failure("ui.global_artwork_state",exc)

    def _global_artwork_value(self):
        if self._global_artwork_running:return _("RUNNING")
        state=self._global_artwork_last or self._load_global_artwork_state()
        if not isinstance(state,dict) or not state.get("total"):return _("START")
        total=int(state.get("total",0) or 0);ready=int(state.get("ready",0) or 0);failed=int(state.get("failed",0) or 0)
        return ((_("%d/%d READY")%(ready,total))+((_(" • %d MISS")%failed) if failed else ""))

    def _drain_global_artwork_progress(self):
        latest=None
        while True:
            try:latest=self._global_artwork_progress.get_nowait()
            except _queue.Empty:break
            except Exception:break
        if not latest:return
        kind=latest[0] if isinstance(latest,(tuple,list)) and latest else ""
        if kind=="scan":
            _k,label,media,category_done,category_total,found=latest
            self["status"].setText(_("Current Artwork • %s • %s categories %d/%d • %d titles counted")%(
                str(label),str(media),int(category_done or 0),int(category_total or 0),int(found or 0)))
        elif kind=="scan_request":
            _k,label,media,scope,page,found=latest
            self["status"].setText(_("Current Artwork • %s • %s • reading %s page %d • %d titles counted")%(
                str(label),str(media),str(scope),int(page or 1),int(found or 0)))
        elif kind=="scan_catalog":
            _k,label,media,page,pages,found,expected=latest
            if int(pages or 0)>0:
                self["status"].setText(_("Current Artwork • %s • %s catalog pages %d/%d • %d/%d titles counted")%(
                    str(label),str(media),int(page or 1),int(pages or 1),int(found or 0),max(int(found or 0),int(expected or 0))))
            else:
                self["status"].setText(_("Current Artwork • %s • %s catalog page %d • %d titles counted")%(
                    str(label),str(media),int(page or 1),int(found or 0)))
        elif kind=="scan_category":
            _k,label,media,category_done,category_total,page,pages,found=latest
            suffix=(_(" • page %d/%d")%(int(page or 1),int(pages or 1))) if int(pages or 0)>0 else (_(" • page %d")%int(page or 1))
            self["status"].setText(_("Current Artwork • %s • %s categories %d/%d%s • %d titles counted")%(
                str(label),str(media),int(category_done or 0),int(category_total or 0),suffix,int(found or 0)))
        elif kind=="inventory_progress":
            _k,checked,total,ready,pending=latest
            self["status"].setText(_("Current Artwork • HDD check %d/%d • Ready %d • Need work %d")%(
                int(checked),int(total),int(ready),int(pending)))
        elif kind=="inventory":
            _k,total,ready,pending=latest
            self["status"].setText(_("Current Artwork • Total %d • Ready on HDD %d • Need work %d")%(
                int(total),int(ready),int(pending)))
        elif kind=="progress":
            _k,done,total,already,downloaded,local_built,failed=latest
            remaining=max(0,int(total)-int(done))
            self["status"].setText(_("Current Artwork • %d/%d processed • Already ready %d • Downloaded %d • Local build %d • Remaining %d • Unavailable %d")%(
                int(done),int(total),int(already),int(downloaded),int(local_built),remaining,int(failed)))

    @staticmethod
    def _global_profile_key(profile):
        profile=profile if isinstance(profile,dict) else {}
        return (str(profile.get("portal") or "").rstrip("/").lower(),str(profile.get("mac") or "").upper(),str(profile.get("source_type") or "").lower())

    @staticmethod
    def _global_category_id(row):
        row=row if isinstance(row,dict) else {}
        return str(row.get("id") or row.get("genre_id") or row.get("category_id") or row.get("name") or row.get("title") or "*")

    def _global_artwork_host(self,profile,client,media_type,progress_cb=None):
        # Generic Poster/Grid artwork host only.  Deliberately independent from
        # Generic Poster/Grid artwork host: preload never depends on a presentation-specific renderer.
        from .ui_grid_base import PremiumGridBase
        host=object.__new__(PremiumGridBase)
        host.profile=dict(profile or {});host.client=client;host.media_type=str(media_type)
        host._grid_settings=load_settings();host._grid_image_size=(250,310);host._poster_cover_mode=True
        host._folder_artwork_heavy_finalize=False;host._folder_artwork_worker_limit=1;host._screen_closed=False
        host._folder_artwork_progress=_queue.Queue();host._folder_artwork_cache_done=0;host._folder_artwork_cache_total=0
        host._folder_artwork_progress_offset=0;host._folder_artwork_progress_total_override=0
        host._folder_artwork_progress_callback=progress_cb
        return host

    def _global_artwork_item_ready(self,profile,media_type,item):
        # Poster Grid / content-owned originals are the readiness source.  HDD only:
        # no manifests/network/TMDB are touched by this inventory check.
        def valid(path):
            try:return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path))>256)
            except Exception:return False
        try:
            owned=_load_content_art(profile,media_type,item) or {}
        except Exception:
            owned={}
        poster=str((owned or {}).get("poster") or "");backdrop=str((owned or {}).get("backdrop") or "")
        if valid(poster) and valid(backdrop):return True
        try:
            visual=_load_visual_bundle(profile,media_type,item,snapshot={}) or {}
        except Exception:
            visual={}
        if not valid(poster):poster=str((visual or {}).get("poster") or "")
        if not valid(backdrop):backdrop=str((visual or {}).get("backdrop") or "")
        return bool(valid(poster) and valid(backdrop))

    def _global_artwork_collect(self,cancel_event):
        from .persistent_cache import content_cache_key
        # Current source only.  Fast path asks the provider for category="*" and
        # walks native pages, which is the same catalogue API the old Poster Grid
        # already trusts.  This avoids serially opening hundreds of categories
        # (e.g. 443) before the first counter can move.  Category-by-category is
        # retained only as a compatibility fallback for panels that reject "*".
        profile=dict(self.profile or {})
        if not profile:return []
        client=self.client;label=self._global_artwork_source_label()
        groups=[];seen=set();found=0;cfg=load_settings();max_pages=max(25,min(int(cfg.get("search_max_pages",250) or 250),500))

        def add_rows(media,rows,bucket):
            nonlocal found
            added=0
            for item in rows if isinstance(rows,list) else []:
                if not isinstance(item,dict):continue
                try:key=str(content_cache_key(profile,media,item) or "")
                except Exception:key="%s|%s|%s"%(self._global_profile_key(profile),media,str(item.get("id") or item.get("movie_id") or item.get("series_id") or item.get("name") or item.get("title") or ""))
                global_key=(media,key)
                if global_key in seen:continue
                seen.add(global_key);bucket.append(dict(item));found+=1;added+=1
            return added

        def scan_pages(media,gid,bucket,scope,category_done=0,category_total=0):
            page=1;previous_signature=None;added_total=0
            for _ in range(max_pages):
                if cancel_event is not None and cancel_event.is_set():return added_total
                self._global_artwork_progress.put(("scan_request",label,(_("Movies") if media=="vod" else _("Series")),scope,page,len(bucket)))
                try:payload=client.ordered_page(media,gid,page,cancel_event=cancel_event) or {}
                except Exception as exc:
                    optional_failure("ui.global_artwork_catalog_page",exc);break
                rows=[x for x in (payload.get("items") or []) if isinstance(x,dict)] if isinstance(payload,dict) else []
                if not rows:break
                signature=tuple(str(x.get("id") or x.get("movie_id") or x.get("series_id") or x.get("name") or x.get("title") or "") for x in rows[:4])
                if page>1 and signature and signature==previous_signature:break
                previous_signature=signature
                added=add_rows(media,rows,bucket);added_total+=added
                total=max(0,int(payload.get("total") or 0)) if isinstance(payload,dict) else 0
                page_size=max(1,int(payload.get("page_size") or len(rows) or 1)) if isinstance(payload,dict) else max(1,len(rows))
                pages=((total+page_size-1)//page_size) if total else 0
                if category_total:
                    self._global_artwork_progress.put(("scan_category",label,(_("Movies") if media=="vod" else _("Series")),category_done,category_total,page,pages,len(bucket)))
                else:
                    self._global_artwork_progress.put(("scan_catalog",label,(_("Movies") if media=="vod" else _("Series")),page,pages,len(bucket),total))
                if total and page*page_size>=total:break
                if not total and len(rows)<page_size:break
                page+=1
            return added_total

        for media in ("vod","series"):
            if cancel_event is not None and cancel_event.is_set():return []
            bucket=[]
            hidden=_profile_hidden_ids(cfg,profile,media)
            # Wildcard catalogues are safe only when nothing is hidden. When the
            # user hides folders, enumerate visible category IDs so hidden content
            # is never fetched by the background artwork worker.
            wildcard_added=scan_pages(media,"*",bucket,"catalog") if not hidden else 0
            if wildcard_added<=0:
                try:categories=client.genres(media,cancel_event=cancel_event) or []
                except Exception as exc:
                    optional_failure("ui.global_artwork_genres",exc);categories=[]
                category_ids=[]
                for row in categories if isinstance(categories,list) else []:
                    if isinstance(row,dict):
                        gid=self._global_category_id(row)
                        if gid and gid not in hidden and gid not in category_ids:category_ids.append(gid)
                category_total=len(category_ids)
                for cidx,gid in enumerate(category_ids,1):
                    if cancel_event is not None and cancel_event.is_set():return []
                    self._global_artwork_progress.put(("scan",label,(_("Movies") if media=="vod" else _("Series")),cidx-1,category_total,len(bucket)))
                    scan_pages(media,gid,bucket,"category",cidx,category_total)
            if bucket:groups.append((dict(profile),client,media,bucket))
        return groups

    def _global_artwork_worker(self,cancel_event):
        groups=self._global_artwork_collect(cancel_event)
        if cancel_event is not None and cancel_event.is_set():return {"cancelled":True}
        total=sum(len(rows) for _p,_c,_m,rows in groups)
        ready=0;checked=0;pending_groups=[]
        for profile,client,media,rows in groups:
            host=self._global_artwork_host(profile,client,media)
            pending=[]
            for item in rows:
                if cancel_event is not None and cancel_event.is_set():return {"cancelled":True}
                checked+=1
                if self._global_artwork_item_ready(profile,media,item):ready+=1
                else:pending.append(item)
                if checked==1 or checked==total or checked%5==0:
                    self._global_artwork_progress.put(("inventory_progress",checked,total,ready,checked-ready))
            if pending:pending_groups.append((profile,client,media,pending))
        self._global_artwork_progress.put(("inventory",total,ready,total-ready))
        if total<=0:return {"total":0,"ready":0,"already":0,"downloaded":0,"local_built":0,"failed":0}
        if not pending_groups:return {"total":total,"ready":total,"already":total,"downloaded":0,"local_built":0,"failed":0,"nothing_missing":True}
        processed=0;downloaded=0;local_built=0;failed=0
        for profile,client,media,rows in pending_groups:
            if cancel_event is not None and cancel_event.is_set():return {"cancelled":True}
            base_processed=processed;base_downloaded=downloaded;base_local=local_built;base_failed=failed
            def pcb(done,_group_total,stats,bp=base_processed,bd=base_downloaded,bl=base_local,bf=base_failed):
                self._global_artwork_progress.put(("progress",ready+bp+int(done or 0),total,ready,bd+int(stats.get("downloaded",0) or 0),bl+int(stats.get("hot",0) or 0),bf+int(stats.get("failed",0) or 0)))
            host=self._global_artwork_host(profile,client,media,pcb)
            result=host._cache_folder_artwork_worker(rows,cancel_event) or {}
            processed+=int(result.get("done",0) or 0);downloaded+=int(result.get("downloaded",0) or 0);local_built+=int(result.get("hot",0) or 0);failed+=int(result.get("failed",0) or 0)
            self._global_artwork_progress.put(("progress",ready+processed,total,ready,downloaded,local_built,failed))
        final_ready=max(0,total-failed)
        return {"total":total,"ready":final_ready,"already":ready,"downloaded":downloaded,"local_built":local_built,"failed":failed}

    def _start_global_artwork_preload(self):
        # Always count the CURRENT source first.  This deliberately does not trust
        # an old total blindly: a Portal/Xtream catalogue may have gained titles.
        # The count/HDD check is cheap compared with artwork download, and matching
        # canonical files prepared from another source are reused immediately.
        if self._global_artwork_running or self._busy:
            self["status"].setText(_("Current Artwork preload is already running"));return
        self._global_artwork_running=True;self.refresh()
        self["status"].setText(_("Current Artwork • opening current source catalog..."))
        def ok(result):
            self._global_artwork_running=False;result=result or {}
            if result.get("cancelled"):
                self["status"].setText(_("Current Artwork preload cancelled"));self.refresh();return
            total=int(result.get("total",0) or 0);ready=int(result.get("ready",0) or 0);already=int(result.get("already",0) or 0);downloaded=int(result.get("downloaded",0) or 0);local_built=int(result.get("local_built",0) or 0);failed=int(result.get("failed",0) or 0)
            state={"schema":2,"completed_at":int(_time.time()),"total":total,"ready":ready,"already":already,"downloaded":downloaded,"local_built":local_built,"failed":failed}
            self._global_artwork_last=state;self._save_global_artwork_state(state);self.refresh()
            if result.get("nothing_missing") or (total and ready==total and downloaded==0 and local_built==0 and failed==0):
                self["status"].setText(_("Current Artwork already ready • checked %d/%d • everything is on HDD • 0 remaining")%(ready,total))
            elif total and ready==total:
                self["status"].setText(_("Current Artwork ready • %d/%d on HDD • %d already ready • %d downloaded • %d local builds • 0 remaining")%(ready,total,already,downloaded,local_built))
            else:
                self["status"].setText(_("Current Artwork finished • %d/%d ready • %d downloaded • %d local builds • %d unavailable")%(ready,total,downloaded,local_built,failed))
        def fail(error):
            self._global_artwork_running=False;self.refresh();self["status"].setText(_("Current Artwork failed: %s")%str(error or _("Unknown error")))
        self._run_async(lambda handle:self._global_artwork_worker(handle.cancel_event),ok,fail)

    def _refresh_current_content(self):
        """Refresh only the selected source's catalogue/index data."""
        self["status"].setText(_("Refreshing current source content…"))
        profile=dict(self.profile or {})
        client=self.client
        def work(handle):
            method=getattr(client,"refresh_content",None)
            if not callable(method):
                raise RuntimeError(_("This source does not support content refresh."))
            result=method(cancel_event=handle.cancel_event)
            if handle.cancelled():
                raise RuntimeError(_("Content refresh cancelled"))
            try:
                _invalidate_source_navigation_cache(profile)
            except Exception as exc:
                optional_failure("ui.content_refresh_navigation_cache",exc)
            return result or {}
        def done(result):
            message=content_refresh_feedback(result, _)
            self.refresh()
            self["status"].setText(message)
            self._context_open_notice(_("Refresh Content"),message)
        def failed(exc):
            message=content_refresh_failure(exc, _)
            self["status"].setText(message)
            self._context_open_notice(_("Refresh Content"),message)
        self._run_async(work,done,failed)

    def refresh(self):
        cfg = load_settings()

        # Settings is now a compact ten-section navigator.  Everyday users see
        # the ten clear groups first; pressing OK swaps this same left rail to
        # the group's options.  No extra Screen is created and the adaptive
        # background/materials stay resident.
        sections = [
            ("playback", _("Playback"), _("Playback engine, resume and watch-progress behaviour."), "settings_icons/playback.png"),
            ("live", _("Live & EPG"), _("Live list, preview, EPG and catch-up controls."), "settings_icons/epg.png"),
            ("media", _("Movies & Series"), _("Views, artwork and catalogue presentation."), "settings_icons/artwork.png"),
            ("search", _("Search & Metadata"), _("Search scope, TMDB and external artwork/subtitle sources."), "settings_icons/multi_search.png"),
            ("language_metadata", _("Language & Metadata"), _("Interface language and movie/series description language."), "settings_icons/language.png"),
            ("parental", _("Parental Control"), _("PIN, sensitive categories and temporary unlock."), "settings_icons/parental.png"),
            ("appearance", _("Appearance"), _("Font size, menu entry and visual theme."), "settings_icons/theme.png"),
            ("storage", _("Storage & Maintenance"), _("Refresh, caches, artwork storage and backups."), "settings_icons/cache.png"),
            ("portal", _("Portal / Network / Advanced"), _("Portal timeout, proxy and low-frequency tuning."), "settings_icons/advanced.png"),
            ("tools", _("Tools & About"), _("Diagnostics, cleaner, updates and build information."), "settings_icons/diagnostics.png"),
        ]

        by_section = {
            "playback": [
                (_("Playback Engine"), "service", str(cfg.get("service_type",4097)), _("Choose the Enigma2 playback service used for streams.")),
                (_("Resume Behavior"), "resume_behavior", _({"ask":"ASK","always":"ALWAYS","start":"FROM START"}.get(str(cfg.get("resume_behavior","ask")),"ASK")), _("Ask, always resume, or always start from the beginning.")),
                (_("Crash-Safe Progress"), "crash_safe_progress", _("ON") if cfg.get("crash_safe_progress",True) else _("OFF"), _("Save VOD/episode progress frequently and on pause/engine changes.")),
                (_("Progress Save Interval"), "progress_save_seconds", _("%ss")%cfg.get("progress_save_seconds",10), _("Crash-safe bookmark interval for movies and episodes.")),
                (_("Next Episode Countdown"), "next_episode_countdown", _("%ss")%cfg.get("next_episode_countdown",10), _("Choose the cancelable autoplay countdown after an episode ends.")),
                (_("Auto Remove Completed"), "auto_remove_completed", _("ON") if cfg.get("auto_remove_completed",True) else _("OFF"), _("Automatically remove titles from Continue Watching after the completion threshold.")),
                (_("Completion Threshold"), "completion_threshold", _("%s%%")%cfg.get("completion_threshold",93), _("Percentage at which a title is considered completed and removed from Continue Watching.")),
                (_("Completion Remaining Time"), "completion_remaining_seconds", _("%ss")%cfg.get("completion_remaining_seconds",180), _("Also mark a title completed when this little time remains.")),
            ],
            "live": [
                (_("Channel List"), "channel_list_mode", _({"epg":"EPG","compact":"COMPACT","large":"LARGE"}.get(str(cfg.get("channel_list_mode","epg")),"EPG")), _("Choose EPG, compact or large live-channel list density.")),
                (_("Show Channel Numbers"), "show_channel_numbers", _("ON") if cfg.get("show_channel_numbers",True) else _("OFF"), _("Display channel numbers in Live TV lists.")),
                (_("Live Preview"), "preview", _("ON") if cfg.get("live_preview",False) else _("OFF"), _("Enable portal live-preview behavior where supported.")),
                (_("EPG Window"), "epg", _("%sh") % cfg.get("epg_hours",4), _("How much EPG data to request for live channels.")),
                (_("Catch-up History"), "catchup_hours", _("%sh") % cfg.get("catchup_hours",72), _("How far back archive EPG should be collected.")),
                (_("EPG Refresh Budget"), "epg_refresh_budget", _("%ss") % cfg.get("epg_refresh_budget",45), _("Maximum background XMLTV refresh time budget.")),
            ],
            "media": [
                (_("Poster Loading"), "images", _("ON") if cfg.get("load_images",True) else _("OFF"), _("Enable or disable remote poster and logo artwork.")),
                (_("Movies View"), "movies_view_mode", _("CINEMATIC") if cfg.get("movies_view_mode","cinematic")=="cinematic" else (_("BACKDROP GRID 2") if cfg.get("movies_view_mode","cinematic")=="backdrop2" else (_("BACKDROP GRID") if cfg.get("movies_view_mode","cinematic")=="backdrop" else (_("POSTER GRID") if cfg.get("movies_view_mode","cinematic")=="poster_legacy" else _("POSTER GRID V2")))), _("Choose Poster Grid, Cinematic, Backdrop Grid, or Backdrop Grid 2 for Movies.")),
                (_("Series View"), "series_view_mode", _("CINEMATIC") if cfg.get("series_view_mode","cinematic")=="cinematic" else (_("BACKDROP GRID 2") if cfg.get("series_view_mode","cinematic")=="backdrop2" else (_("BACKDROP GRID") if cfg.get("series_view_mode","cinematic")=="backdrop" else (_("POSTER GRID") if cfg.get("series_view_mode","cinematic")=="poster_legacy" else _("POSTER GRID V2")))), _("Choose Poster Grid, Cinematic, Backdrop Grid, or Backdrop Grid 2 for Series.")),
                (_("Watched Badges"), "show_watched", _("ON") if cfg.get("show_watched",True) else _("OFF"), _("Show WATCHED state on movie and series cards.")),
                (_("Clean Display Names"), "clean_titles", _("ON") if cfg.get("clean_titles",True) else _("OFF"), _("ON shows the cleaned Ultra Stalker names across Movies, Series, Live and Player screens. OFF shows the provider names as received; TMDB matching and artwork lookup stay unchanged.")),
                (_("Quality Badges"), "quality_badges", _("ON") if cfg.get("show_quality_badges",True) else _("OFF"), _("Show 4K/HD and similar quality metadata when detected.")),
                (_("Preload Current Artwork"), "global_artwork_preload", self._global_artwork_value(), _("Prepare Movies/Series artwork for the current Portal/Xtream source; shared HDD-ready items are skipped.")),
            ],
            "search": [
                (_("Multi-Portal Search"), "multi_portal_search", _("ON") if cfg.get("multi_portal_search",True) else _("OFF"), _("Global Search checks every enabled portal, not only the current one.")),
                (_("Search Limit / Portal"), "content_page_size", str(cfg.get("content_page_size",50)), _("Maximum Global Search results returned from each portal.")),
                (_("Search Scan Pages"), "search_max_pages", str(cfg.get("search_max_pages",250)), _("Maximum catalogue pages scanned when native portal search is unavailable.")),
                (_("Search Time Budget"), "search_time_budget", _("%ss") % cfg.get("search_time_budget",12), _("Maximum time spent searching one portal before partial results are returned.")),
                (_("TMDB Metadata"), "tmdb_enabled", _("ON") if cfg.get("tmdb_enabled",True) else _("OFF"), _("Use TMDB to enrich movie/series metadata and artwork.")),
                (_("TMDB API Key / Token"), "tmdb_credential", (_("FILE ••••") if cfg.get("tmdb_credential") else _("NOT SET")), _("Stored privately in api_keys.conf; never copied into settings.json.")),
                (_("TMDB Connection Test"), "tmdb_test", _("TEST"), _("Validate the saved TMDB credential against the official API.")),
                (_("Fanart.tv API Key"), "fanart_api_key", (_("FILE ••••") if cfg.get("fanart_api_key") else _("NOT SET")), _("Optional second-source artwork fallback used only when TMDB leaves a poster or backdrop missing.")),
                (_("Online Subtitles • SubDL"), "subdl_api_key", (_("FILE ••••") if cfg.get("subdl_api_key") else _("NOT SET")), _("SubDL API key for online movie and episode subtitles.")),
                (_("Online Subtitles • SubSource"), "subsource_api_key", (_("FILE ••••") if cfg.get("subsource_api_key") else _("NOT SET")), _("SubSource API key for online movie and episode subtitles.")),
            ],
            "language_metadata": [
                (_("Interface Language"), "plugin_language", language_name(cfg.get("plugin_language","en")), _("Choose the language used by Ultra Stalker menus and controls. Portal, M3U and Xtream content is never translated.")),
                (_("TMDb Information Language"), "description_language", _(description_language_name(cfg.get("description_language","ar-en"))), _("Choose movie and series TMDb information language independently from the interface. Missing fields fall back to English; Clean Names is separate.")),
            ],
            "parental": [
                (_("Parental Lock"), "parental", _("ON") if cfg.get("parental_lock",False) else _("OFF"), _("Protect configured/adult content categories.")),
                (_("Parental Mode"), "parental_mode", _("PIN") if cfg.get("parental_mode","pin")=="pin" else _("HIDE"), _("PIN-protect sensitive categories or hide them completely.")),
                (_("Parental Unlock"), "parental_session", _("%d min") % cfg.get("parental_session_minutes",30), _("Keep a correct PIN unlocked for this session duration.")),
                (_("Parental PIN"), "pin", _("CHANGE"), _("Set a 4–8 digit parental-control PIN.")),
                (_("Adult Keywords"), "adult_keywords", _("%d words") % len(cfg.get("adult_keywords",[])), _("Comma-separated words used to detect sensitive categories.")),
                (_("Hide Adult Categories"), "hide_adult", _("ON") if cfg.get("hide_adult",True) else _("OFF"), _("Hide categories matching adult keywords when Parental Lock is disabled.")),
                (_("Lock Parental Session"), "parental_lock_now", (_("UNLOCKED %d min")%parental_remaining_minutes()) if parental_is_unlocked() else _("LOCKED"), _("Immediately clear the temporary parental unlock.")),
            ],
            "appearance": [
                (_("Font Size"), "font_scale", _({"normal":"Normal","large":"Large","larger":"Larger","xlarge":"Extra Large"}.get(str(cfg.get("font_scale","normal")),"Normal")), _("Choose the text size used across Ultra Stalker. Restart Enigma2 to apply it everywhere.")),
                (_("Show in Main Menu"), "show_main_menu", _("ON") if cfg.get("show_main_menu",True) else _("OFF"), _("Show Ultra Stalker directly in the Enigma2 Main Menu.")),
                (_("Visual Theme"), "theme", load_theme().replace("_"," ").title(), _("Choose Nova FHD, OLED Black, or Midnight Purple. Restart is required.")),
            ],
            "storage": [
                (_("Refresh Content"), "refresh_content", _("REFRESH"), _("Reload fresh Portal, Xtream or M3U catalogue data for the currently selected source without deleting persistent artwork.")),
                (_("Temporary Cache"), "clear_temp_cache", _("CLEAR"), _("Clear only temporary Ultra Stalker files under /tmp. Persistent HDD artwork is kept.")),
                (_("Persistent Artwork"), "clear_persistent_artwork", _("PROTECTED"), _("Delete saved posters, backdrops, generated artwork, Home artwork and Live picons from the HDD. Confirmation is required.")),
                (_("Image Cache Limit"), "image_cache_mb", _("%d MB") % cfg.get("image_cache_mb",128), _("Maximum persistent artwork cache size.")),
                (_("Download Disk Reserve"), "download_reserve_mb", _("%d MB") % cfg.get("download_reserve_mb",1024), _("Keep this much disk space free while downloads are running.")),
                (_("Backup & Restore"), "backup_restore", _("OPEN"), _("Backup or restore portals, settings, favorites, history and resume state.")),
            ],
            "portal": [
                (_("Portal Timeout"), "timeout", _("%ss") % cfg.get("timeout",10), _("Network timeout for portal API calls.")),
                (_("Bouquet Proxy Port"), "proxy_port", str(cfg.get("proxy_port",17999)), _("Local loopback port used by dynamic bouquet playback and XMLTV integration.")),
                (_("Hide Empty Categories"), "hide_empty_categories", _("ON") if cfg.get("hide_empty_categories",True) else _("OFF"), _("Hide categories detected as empty after loading.")),
                (_("Diagnostic Logging"), "diagnostic_logging", _("ON") if cfg.get("diagnostic_logging",False) else _("OFF"), _("Enable verbose rotating diagnostic logs after plugin restart.")),
            ],
            "tools": [
                (_("Support Bundle"), "support_bundle", _("EXPORT"), _("Create a redacted diagnostics ZIP for troubleshooting.")),
                (_("Web Cleaner Access"), "web_cleaner_access", _("EASY") if cfg.get("web_cleaner_access","easy")=="easy" else _("PIN / QR"), _("Choose how devices on your network open Web Cleaner")),
                (_("Web Cleaner"), "web_cleaner", self._web_cleaner_state(), _("Start/stop the Premium Web Cleaner and Deep Check service on port 7725.")),
                (_("Online Update"), "online_update", _("CHECK"), _("Check the official Ultra Stalker release channel and install a verified update safely.")),
                (_("About"), "about", PLUGIN_VERSION, _("Version, build identity and runtime information.")),
                (_("Diagnostics"), "diagnostics", _("OPEN"), _("View package, database, cache and portal diagnostic information.")),
            ],
        }

        self._settings_sections = sections
        self._settings_entries_by_section = by_section
        self._settings_icons = {
            "section:playback":"settings_icons/playback.png", "section:live":"settings_icons/epg.png",
            "section:media":"settings_icons/artwork.png", "section:search":"settings_icons/multi_search.png",
            "section:language_metadata":"settings_icons/language.png",
            "section:parental":"settings_icons/parental.png", "section:appearance":"settings_icons/theme.png",
            "section:storage":"settings_icons/cache.png", "section:portal":"settings_icons/advanced.png",
            "section:tools":"settings_icons/diagnostics.png",
            "service":"settings_icons/playback.png", "plugin_language":"settings_icons/language.png",
            "font_scale":"settings_icons/advanced.png", "timeout":"settings_icons/timeout.png",
            "refresh_content":"settings_icons/cache.png", "epg":"settings_icons/epg.png",
            "catchup_hours":"settings_icons/catchup.png", "images":"settings_icons/artwork.png",
            "movies_view_mode":"settings_icons/channel_list.png", "series_view_mode":"settings_icons/channel_list.png",
            "clear_temp_cache":"settings_icons/cache.png", "clear_persistent_artwork":"settings_icons/cache.png",
            "download_reserve_mb":"settings_icons/disk.png", "image_cache_mb":"settings_icons/cache.png",
            "global_artwork_preload":"settings_icons/prefetch.png", "show_watched":"settings_icons/watched.png",
            "preview":"settings_icons/preview.png", "parental":"settings_icons/parental.png",
            "parental_mode":"settings_icons/parental_mode.png", "parental_session":"settings_icons/session.png",
            "pin":"settings_icons/pin.png", "adult_keywords":"settings_icons/keywords.png",
            "parental_lock_now":"settings_icons/lock.png", "hide_adult":"settings_icons/parental.png",
            "clean_titles":"settings_icons/clean.png", "quality_badges":"settings_icons/quality.png",
            "channel_list_mode":"settings_icons/channel_list.png", "show_channel_numbers":"settings_icons/channel_list.png",
            "resume_behavior":"settings_icons/resume.png", "crash_safe_progress":"settings_icons/progress_safe.png",
            "progress_save_seconds":"settings_icons/interval.png", "next_episode_countdown":"settings_icons/next_episode.png",
            "auto_remove_completed":"settings_icons/auto_remove.png", "completion_threshold":"settings_icons/threshold.png",
            "completion_remaining_seconds":"settings_icons/remaining.png", "multi_portal_search":"settings_icons/multi_search.png",
            "content_page_size":"settings_icons/search_limit.png", "search_max_pages":"settings_icons/search_limit.png",
            "search_time_budget":"settings_icons/search_limit.png", "tmdb_enabled":"settings_icons/tmdb.png",
            "tmdb_credential":"settings_icons/tmdb.png", "description_language":"settings_icons/tmdb.png",
            "tmdb_test":"settings_icons/tmdb.png", "fanart_api_key":"settings_icons/artwork.png",
            "subdl_api_key":"settings_icons/subtitles.png", "subsource_api_key":"settings_icons/subtitles.png", "backup_restore":"settings_icons/backup.png",
            "theme":"settings_icons/theme.png", "support_bundle":"settings_icons/support.png",
            "web_cleaner_access":"settings_icons/web_access.png", "web_cleaner":"settings_icons/web_cleaner.png",
            "online_update":"settings_icons/recovery.png", "about":"settings_icons/about.png",
            "diagnostics":"settings_icons/diagnostics.png", "proxy_port":"settings_icons/web_access.png",
            "epg_refresh_budget":"settings_icons/epg.png", "hide_empty_categories":"settings_icons/channel_list.png",
            "diagnostic_logging":"settings_icons/diagnostics.png", "show_main_menu":"settings_icons/advanced.png",
        }

        section_key=getattr(self,"_settings_section",None)
        if section_key and section_key in by_section:
            self.entries=list(by_section[section_key])
            selected=int(getattr(self,"_settings_section_last_idx",{}).get(section_key,0) or 0)
        else:
            self._settings_section=None
            self.entries=[(title,"section:"+key,_('%d options')%len(by_section.get(key,[])),desc) for key,title,desc,_icon in sections]
            selected=int(getattr(self,"_settings_root_last_idx",0) or 0)
        self._render_settings_rows(selected)
        self._selection_changed()

    @staticmethod
    def _safe_clear_dir_contents(path):
        removed_files=0
        removed_bytes=0
        try:
            if not path or not os.path.isdir(path):
                return removed_files,removed_bytes
            for name in os.listdir(path):
                target=os.path.join(path,name)
                try:
                    if os.path.islink(target) or os.path.isfile(target):
                        try:removed_bytes+=int(os.path.getsize(target) or 0)
                        except Exception:pass
                        os.unlink(target);removed_files+=1
                    elif os.path.isdir(target):
                        for base,_dirs,files in os.walk(target):
                            for fn in files:
                                fp=os.path.join(base,fn)
                                try:removed_bytes+=int(os.path.getsize(fp) or 0);removed_files+=1
                                except Exception:pass
                        _shutil.rmtree(target)
                except Exception as exc:
                    optional_failure("ui.cache_clear_item",exc)
        except Exception as exc:
            optional_failure("ui.cache_clear_dir",exc)
        return removed_files,removed_bytes

    def _clear_temporary_cache(self):
        removed_files=0;removed_bytes=0
        # /tmp only. Never follow this action onto /media/hdd.
        for path in ("/tmp/UltraStalker","/tmp/ultrastalker","/tmp/UltraStalker-subtitles"):
            f,b=self._safe_clear_dir_contents(path);removed_files+=f;removed_bytes+=b
        self.refresh()
        self["status"].setText(_("Temporary cache cleared • %d files • %.1f MB. Persistent HDD artwork kept.")%(removed_files,removed_bytes/(1024.0*1024.0)))

    def _confirm_clear_persistent_artwork(self,choice):
        if choice != "delete":
            self["status"].setText(_("Persistent artwork was not changed"))
            return
        if not hdd_read_ready(force=True):
            self["status"].setText(_("Persistent HDD is not available; nothing was deleted"))
            return
        removed_files=0;removed_bytes=0
        # Artwork-only clear. Metadata/index, settings, portals and history are
        # deliberately outside these roots and survive this action.
        for path in PERSISTENT_ARTWORK_DIRS:
            f,b=self._safe_clear_dir_contents(path);removed_files+=f;removed_bytes+=b
        try:
            state_path=self._global_artwork_state_path()
            if state_path and os.path.isfile(state_path):os.remove(state_path)
        except Exception as exc:optional_failure("ui.global_artwork_state_clear",exc)
        self._global_artwork_last={}
        self.refresh()
        self["status"].setText(_("Persistent artwork cleared • %d files • %.1f MB. Settings and metadata kept.")%(removed_files,removed_bytes/(1024.0*1024.0)))

    def _position_settings_list(self):
        """Bottom-anchor the complete Settings rail while preserving row geometry.

        The Settings navigator contains a compact fixed set of rows.  Keeping the
        rail pinned near the top left a large dead area below it, so position the
        whole row stack 20 logical pixels above the bottom edge.  Row height,
        horizontal position, artwork and inter-row spacing stay unchanged.
        """
        try:
            count=max(1,min(int(len(getattr(self,"entries",[]) or [])),int(self.SETTINGS_VISIBLE_ROWS)))
            logical_h=count*int(self.SETTINGS_ROW_H)
            logical_y=max(20,1080-20-logical_h)
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self["list"].instance
            if inst is not None:
                inst.move(ePoint(int(round(50*sx)),int(round(logical_y*sy))))
                inst.resize(eSize(max(1,int(round(self.SETTINGS_ROW_W*sx))),max(1,int(round(logical_h*sy)))))
        except Exception as exc:
            optional_failure("ui.settings_bottom_anchor",exc)

    def _render_settings_rows(self, selected=None):
        self._position_settings_list()
        if selected is None:
            try:selected=self["list"].getSelectedIndex()
            except Exception:selected=0
        selected=max(0,min(int(selected or 0),max(0,len(self.entries)-1))) if self.entries else 0
        rows=[]
        for idx,(title,action,value,_desc) in enumerate(self.entries):
            meta={
                "selected":bool(idx==selected),
                "value":str(value),
                "row_asset":self._settings_row_asset,
                "row_selected_asset":self._settings_row_selected_asset,
                "value_color":self._settings_value_color,
                "utility_accent_strong":True,
            }
            rows.append((title,self._grid_icon_path(title,action),action,meta))
        self._settings_rebuilding=True
        try:
            self["list"].set_icon_rows(rows)
            if self.entries:self["list"].moveToIndex(selected)
        finally:
            self._settings_rebuilding=False
        self._settings_visual_index=selected
        self._settings_last_idx=selected

    def _setting_description_layout(self,text):
        text=" ".join(str(text or "").split())
        if not text:return "",25
        width=1180
        words=text.split(" ")
        for size in (25,24,23,22,21,20,19,18):
            if _settings_text_width_px(text,size)<=width:
                return text,size
            if len(words)>1:
                best=None
                for cut in range(1,len(words)):
                    a=" ".join(words[:cut]);b=" ".join(words[cut:])
                    aw=_settings_text_width_px(a,size);bw=_settings_text_width_px(b,size)
                    if aw<=width and bw<=width:
                        score=max(aw,bw)*10+abs(aw-bw)
                        if best is None or score<best[0]:best=(score,a,b)
                if best is not None:
                    return best[1]+"\n"+best[2],size
        cut=max(1,len(words)//2)
        return " ".join(words[:cut])+"\n"+" ".join(words[cut:]),18

    def _set_setting_description(self,text):
        rendered,size=self._setting_description_layout(text)
        try:self["description"].setText(rendered)
        except Exception:return
        try:
            inst=self["description"].instance
            if inst is not None:inst.setFont(gFont("Regular",int(size)))
        except Exception as exc:optional_failure("ui.settings_description_font",exc)
        if not getattr(self,"_context_active",False):
            try:self["description"].show()
            except Exception:pass

    def _selection_changed(self):
        if getattr(self,"_settings_rebuilding",False):return
        try:idx=int(self["list"].getSelectedIndex() or 0)
        except Exception:return
        if not (0<=idx<len(self.entries)):return
        if idx!=getattr(self,"_settings_visual_index",-1):
            self._render_settings_rows(idx)
        self._settings_last_idx=idx
        if getattr(self,"_settings_section",None):
            self._settings_section_last_idx[self._settings_section]=idx
            back_label=_("Back")
        else:
            self._settings_root_last_idx=idx
            back_label=_("Back")
        title,_action,value,desc=self.entries[idx]
        self["info_title"].setText(title);self["info"].setText(str(desc)[:120])
        self._set_setting_description(desc)
        # Keep the existing footer hints/controls untouched; the new white guide
        # lives in its own two-line lane immediately above them.
        self["status"].setText((_("Current: %s") % str(value)[:28]) + "   •   " + _("ARROWS Navigate  •  OK Select  •  BACK Home"))

    def select(self):
        try: idx=self["list"].getSelectedIndex()
        except Exception: return
        if not (0 <= idx < len(self.entries)): return
        action=self.entries[idx][1]
        if str(action).startswith("section:"):
            self._settings_root_last_idx=idx
            self._settings_section=str(action).split(":",1)[1]
            self._settings_last_idx=int(self._settings_section_last_idx.get(self._settings_section,0) or 0)
            self.refresh()
            return
        cfg=load_settings()
        if action == "service":
            choices=[("DVB / native (1)",1),("IPTV / GStreamer (4097)",4097),("GstPlayer (5001)",5001),("ExtePlayer3 (5002)",5002),("ServiceApp / DreamOS (8193)",8193)]
            current=int(cfg.get("service_type",4097) or 4097)
            selection=next((i for i,c in enumerate(choices) if c[1]==current),0)
            self._context_open_choice(lambda c:self._save_choice("service_type",c),_("Playback engine"),choices,selection,_("Choose the Enigma2 playback service"))
        elif action in ("movies_view_mode","series_view_mode"):
            current=str(cfg.get(action) or "cinematic").lower();_view_values=["poster_legacy","poster","cinematic","backdrop","backdrop2"];selection=_view_values.index(current) if current in _view_values else 0
            title=_("Movies View") if action=="movies_view_mode" else _("Series View")
            self._context_open_choice(lambda c,k=action:self._save_choice(k,c),title,[(_("Poster Grid"),"poster_legacy"),(_("Poster Grid V2"),"poster"),(_("Cinematic"),"cinematic"),(_("Backdrop Grid"),"backdrop"),(_("Backdrop Grid 2"),"backdrop2")],selection,_("Both views read the same single Global TMDB Library record"))
        elif action == "timeout":
            self._context_open_choice(lambda c:self._save_choice("timeout",c),_("Portal timeout"),[(_("%d seconds")%x,x) for x in (5,8,10,15,20,30)])
        elif action == "proxy_port":
            self.session.openWithCallback(self._save_proxy_port,SettingsGlassInputScreen,title=_("Bouquet proxy port (1024-65535)"),text=str(cfg.get("proxy_port",17999)),maxSize=5)
        elif action == "refresh_content":
            self._refresh_current_content()
        elif action == "resume_behavior":
            self._context_open_choice(lambda c:self._save_choice("resume_behavior",c),_("Resume behavior"),[(_("Ask every time"),"ask"),(_("Always resume"),"always"),(_("Always start from beginning"),"start")])
        elif action == "progress_save_seconds":
            self._context_open_choice(lambda c:self._save_choice("progress_save_seconds",c),_("Progress save interval"),[(_("%d seconds")%x,x) for x in (3,5,10,15,30)])
        elif action == "next_episode_countdown":
            self._context_open_choice(lambda c:self._save_choice("next_episode_countdown",c),_("Next episode countdown"),[(_("%d seconds")%x,x) for x in (5,10,15,20,30)])
        elif action == "completion_threshold":
            self._context_open_choice(lambda c:self._save_choice("completion_threshold",c),_("Completion threshold"),[(_("%d%%")%x,x) for x in (85,90,93,95,97)])
        elif action == "completion_remaining_seconds":
            self._context_open_choice(lambda c:self._save_choice("completion_remaining_seconds",c),_("Completion remaining time"),[(_("%d seconds")%x,x) for x in (60,120,180,240,300)])
        elif action == "epg":
            self._context_open_choice(lambda c:self._save_choice("epg_hours",c),_("EPG window"),[(_("%d hours")%x,x) for x in (2,4,6,8,12,24)])
        elif action == "catchup_hours":
            self._context_open_choice(lambda c:self._save_choice("catchup_hours",c),_("Catch-up history"),[(_("%d hours")%x,x) for x in (24,48,72,120,168,336,720)])
        elif action == "epg_refresh_budget":
            self._context_open_choice(lambda c:self._save_choice("epg_refresh_budget",c),_("EPG refresh budget"),[(_("%d seconds")%x,x) for x in (15,30,45,60,90,120,180)])
        elif action in ("images","preview","parental","clean_titles","quality_badges","multi_portal_search","diagnostic_logging","show_watched","tmdb_enabled","crash_safe_progress","auto_remove_completed","show_main_menu","show_channel_numbers","hide_empty_categories","hide_adult"):
            key={"images":"load_images","preview":"live_preview","parental":"parental_lock","quality_badges":"show_quality_badges"}.get(action,action)
            current=bool(cfg.get(key,True))
            title=self.entries[idx][0]
            self._context_open_choice(lambda c,a=action,k=key:self._save_toggle_choice(a,k,c),title,[(_("ON"),True),(_("OFF"),False)],0 if current else 1,_("Choose ON or OFF"))
        elif action == "clear_temp_cache":
            self._clear_temporary_cache()
        elif action == "clear_persistent_artwork":
            self._context_open_choice(
                self._confirm_clear_persistent_artwork,
                _("Delete Persistent Artwork?"),
                [(_("CANCEL"),"cancel"),(_("DELETE ARTWORK"),"delete")],
                0,
                _("This removes saved HDD artwork only. Settings, portals, watch history and metadata indexes are kept.")
            )
        elif action == "global_artwork_preload":
            self._start_global_artwork_preload()
        elif action == "image_cache_mb":
            self._context_open_choice(lambda c:self._save_choice("image_cache_mb",c),_("Image cache limit"),[(_("%d MB")%x,x) for x in (40,80,120,180,256,512,1024)])
        elif action == "download_reserve_mb":
            self._context_open_choice(lambda c:self._save_choice("download_reserve_mb",c),_("Download disk safety reserve"),[(_("%d MB")%x,x) for x in (256,512,1024,2048,4096,8192)])
        elif action == "content_page_size":
            self._context_open_choice(lambda c:self._save_choice("content_page_size",c),_("Search results per portal"),[(str(x),x) for x in (20,30,50,80,120,200)])
        elif action == "search_max_pages":
            self._context_open_choice(lambda c:self._save_choice("search_max_pages",c),_("Maximum search scan pages"),[(str(x),x) for x in (25,50,100,150,250)])
        elif action == "search_time_budget":
            self._context_open_choice(lambda c:self._save_choice("search_time_budget",c),_("Search time budget per portal"),[(_("%d seconds")%x,x) for x in (5,8,12,15,20,30)])
        elif action == "parental_mode":
            self._context_open_choice(lambda c:self._save_choice("parental_mode",c),_("Parental mode"),[(_("PIN protect"),"pin"),(_("Hide sensitive categories"),"hide")])
        elif action == "parental_session":
            self._context_open_choice(lambda c:self._save_choice("parental_session_minutes",c),_("Parental unlock duration"),[(_("One action"),0),(_("15 minutes"),15),(_("30 minutes"),30),(_("60 minutes"),60),(_("120 minutes"),120)])
        elif action == "adult_keywords":
            self.session.openWithCallback(self._save_adult_keywords,SettingsGlassInputScreen,title=_("Adult keywords (comma separated)"),text=", ".join(cfg.get("adult_keywords",[])),maxSize=240)
        elif action == "parental_lock_now":
            parental_lock_now();self.refresh();self["status"].setText(_("Parental session locked"))
        elif action == "pin":
            self.session.openWithCallback(self._save_pin, SettingsGlassInputScreen, title=_("New parental PIN (4-8 digits)"), text="", maxSize=8, type=Input.PIN)
        elif action == "channel_list_mode":
            self._context_open_choice(lambda c:self._save_choice("channel_list_mode",c),_("Channel list layout"),[(_("Professional EPG"),"epg"),(_("Compact"),"compact"),(_("Large"),"large")])
        elif action == "api_keys_file":
            self._context_open_notice(_("API Keys"),_("Put long credentials in:\n/etc/enigma2/ultrastalker/api_keys.conf\n\nTMDB_API_KEY=...\nTMDB_READ_TOKEN=...\nIMDB_API_KEY=...\nIMDB_API_ENDPOINT=... (generic mode)\nSUBDL_API_KEY=...\nSUBSOURCE_API_KEY=...\nFANART_API_KEY=...\n\nOfficial IMDb/AWS Data Exchange:\nIMDB_ACCESS_KEY_ID=...\nIMDB_SECRET_ACCESS_KEY=...\nIMDB_SESSION_TOKEN=... (optional)\nIMDB_REGION=us-east-1\nIMDB_DATASET_ID=...\nIMDB_REVISION_ID=...\nIMDB_ASSET_ID=..."))
        elif action == "tmdb_credential":
            self.session.openWithCallback(self._save_tmdb_credential, SettingsGlassInputScreen, title=_("TMDB API key or Read Access Token"), text=str(cfg.get("tmdb_credential") or ""), maxSize=512)
        elif action == "plugin_language":
            current=str(cfg.get("plugin_language") or "en")
            choices=interface_language_choices()
            selection=next((i for i,x in enumerate(choices) if x[1]==current),0)
            self._context_open_choice(self._plugin_language_selected,_("Interface Language"),choices,selection,_("Choose the language used by Ultra Stalker menus and controls. Portal, M3U and Xtream content is never translated."))
        elif action == "font_scale":
            current=str(cfg.get("font_scale") or "normal")
            choices=[(_("Normal"),"normal"),(_("Large"),"large"),(_("Larger"),"larger"),(_("Extra Large"),"xlarge")]
            selection=next((i for i,x in enumerate(choices) if x[1]==current),0)
            self._context_open_choice(self._font_scale_selected,_("Font Size"),choices,selection,_("Choose the text size used across Ultra Stalker. Restart Enigma2 to apply it everywhere."))
        elif action == "description_language":
            current=str(cfg.get("description_language") or "ar-en")
            choices=[(_(label),code) for code,label in description_language_choices()]
            selection=next((i for i,x in enumerate(choices) if x[1]==current),0)
            self._context_open_choice(lambda c:self._save_choice("description_language",c),_("TMDb Information Language"),choices,selection,_("This controls movie and series TMDb information. The selected language is requested first and missing fields fall back to English. Clean Names and artwork/title-logo language are separate."))
        elif action == "fanart_api_key":
            self.session.openWithCallback(self._save_fanart_api_key, SettingsGlassInputScreen, title=_("Fanart.tv API key"), text=str(cfg.get("fanart_api_key") or ""), maxSize=512)
        elif action == "subdl_api_key":
            self.session.openWithCallback(self._save_subdl_api_key, SettingsGlassInputScreen, title=_("SubDL API key"), text=str(cfg.get("subdl_api_key") or ""), maxSize=512)
        elif action == "subsource_api_key":
            self.session.openWithCallback(self._save_subsource_api_key, SettingsGlassInputScreen, title=_("SubSource API key"), text=str(cfg.get("subsource_api_key") or ""), maxSize=512)
        elif action == "tmdb_test":
            credential=str(cfg.get("tmdb_credential") or "").strip()
            if not credential:
                self._context_open_notice(_("TMDB"),_("Add a TMDB API key or Read Access Token first."))
            else:
                self["status"].setText(_("Testing TMDB connection..."))
                def _ok(value):
                    self._context_open_notice(_("TMDB"),_("TMDB connection successful."))
                    self["status"].setText(_("TMDB ready"))
                self._run_async(lambda: TMDBClient(credential,cfg.get("tmdb_language","ar-EG"),cfg.get("timeout",10)).test(), _ok, lambda e:self._context_open_notice(_("TMDB"),_("TMDB test failed: %s")%e))
        elif action == "tmdb_credits":
            self._context_open_notice(_("TMDB Metadata"),_("This product uses the TMDB API but is not endorsed or certified by TMDB.\n\nTMDB data/images are used only when you configure your own credential."))
        elif action == "backup_restore":
            self._open_backup_restore()
        elif action == "support_bundle":
            self._export_support_bundle()
        elif action == "portal_library":
            self.session.open(ServerLibraryScreen,"portal")
        elif action == "xtream_library":
            self.session.open(ServerLibraryScreen,"xtream")
        elif action == "web_cleaner_access":
            current=str(cfg.get("web_cleaner_access") or "easy")
            selection=1 if current=="protected" else 0
            self._context_open_choice(self._web_cleaner_access_selected,_("Web Cleaner Access"),[(_("Easy - LAN direct"),"easy"),(_("Protected - PIN / QR"),"protected")],selection,_("Choose how devices on your network open Web Cleaner"))
        elif action == "web_cleaner":
            mode=_("Easy LAN access") if cfg.get("web_cleaner_access","easy")=="easy" else _("PIN / QR protected")
            self._context_open_choice(self._web_cleaner_action,_("Web Cleaner"),[(_("Start / Show Link"),"start"),(_("Stop Service"),"stop")],0,_("Premium Cleaner + Deep Check • %s")%mode)
        elif action == "theme":
            choices=[(_("Nova FHD"),"nova_fhd"),(_("OLED Black"),"oled_black"),(_("Midnight Purple"),"midnight_purple")]
            current=load_theme(); selection=THEMES.index(current) if current in THEMES else 0
            self._context_open_choice(self._theme_selected_settings,_("Choose visual theme"),choices,selection,_("Select the visual style"))
        elif action == "online_update":
            self["status"].setText(_("Checking the official Ultra Stalker update channel..."))
            def _update_checked(info):
                info=dict(info or {})
                if not info.get("ok"):
                    if not info.get("configured"):
                        self._context_open_notice(_("Online Update"),_("The updater engine is ready, but the official release URL has not been linked to this build yet."))
                    else:
                        self._context_open_notice(_("Online Update"),_("Could not check for updates right now. Your installed version is unchanged."))
                    self["status"].setText(_("Update check finished safely"));return
                if not info.get("available"):
                    self._context_open_notice(_("Ultra Stalker is up to date"),_("You already have the latest official version: V%s")%str(info.get("current") or PLUGIN_VERSION))
                    self["status"].setText(_("Ultra Stalker is up to date"));return
                try:
                    from .updater import show_update_notice
                    show_update_notice(self.session,info)
                    self["status"].setText(_("Update available: V%s")%str(info.get("version") or ""))
                except Exception as exc:
                    optional_failure("ui.online_update_notice",exc)
                    self._context_open_notice(_("Online Update"),_("Update V%s is available, but the premium update screen could not open.")%str(info.get("version") or ""))
            def _update_failed(error):
                optional_failure("ui.online_update_check",error)
                self._context_open_notice(_("Online Update"),_("Could not check for updates right now. Your installed version is unchanged."))
                self["status"].setText(_("Update check failed safely"))
            try:
                from .updater import check_for_update
                self._run_async(lambda:check_for_update(timeout=10),_update_checked,_update_failed)
            except Exception as exc:
                _update_failed(exc)
        elif action == "about":
            self._context_open_notice(_("Ultra Stalker Information"),(_("Version: %s\nBuild: %s\nPython support: 3.12 / 3.13 / 3.14 / 3.15\nInterface: Full HD • Adaptive 3D Glass UI")%(PLUGIN_VERSION,BUILD_NAME)) + "\nAhmed L-HadarY")
        elif action == "diagnostics":
            self.open_diagnostics()

    def _save_proxy_port(self,value):
        if value is None:return
        try:
            port=int(str(value).strip())
            if not 1024 <= port <= 65535:raise ValueError()
            save_settings({"proxy_port":port});self.refresh();self["status"].setText(_("Proxy port saved. Restart the plugin before exporting bouquets again."))
        except Exception:
            self._context_open_notice(_("Invalid port"),_("Enter a port number between 1024 and 65535."))

    def _web_cleaner_state(self):
        try:
            from .webcleaner import status
            info=status()
            if not info.get("running"):return _("STOPPED")
            return _("RUNNING EASY :7725") if info.get("access_mode")=="easy" else _("RUNNING PIN :7725")
        except Exception:
            return _("STOPPED")

    def _web_cleaner_access_selected(self, choice):
        if not choice:return
        mode="protected" if choice[1]=="protected" else "easy"
        save_settings({"web_cleaner_access":mode})
        stopped=False
        try:
            from . import webcleaner
            if webcleaner.status().get("running"):
                webcleaner.stop();stopped=True
        except Exception as exc:optional_failure("ui.webcleaner_access_stop",exc)
        self.refresh()
        self["status"].setText(_("Web Cleaner access: %s%s")%(_("PIN / QR") if mode=="protected" else _("EASY"),_(" • service stopped; start again") if stopped else ""))

    def _web_cleaner_action(self, choice):
        if not choice: return
        try:
            from . import webcleaner
            if choice[1] == "stop":
                webcleaner.stop(); self.refresh(); self["status"].setText(_("Web Cleaner stopped"))
                self._context_open_notice(_("Web Cleaner"),_("Service stopped. Port 7725 is closed."))
                return
            info=webcleaner.start(); self.refresh(); self["status"].setText(_("Web Cleaner running on port 7725"))
            self.session.open(SettingsWebCleanerReadyScreen,info.get("url"),info.get("pair_code"),info.get("qr_path"),info.get("access_mode"))
        except Exception as exc:
            self._context_open_notice(_("Web Cleaner"),_("Could not start Web Cleaner:\n%s") % exc)

    def _open_glass_choice(self,callback,title,rows,selection=0,subtitle="Choose an option"):
        self._context_open_choice(callback,title,rows,selection,subtitle)

    def _open_glass_notice(self,title,message,callback=None,yesno=False):
        self._context_open_notice(title,message,callback,yesno)

    def _theme_selected_settings(self, choice):
        if not choice: return
        try:
            save_theme(choice[1]); self.refresh()
            self._context_open_notice(_("Visual Theme"),_("Theme saved. Restart Enigma2 to apply: %s") % choice[0])
        except Exception as exc:
            self._context_open_notice(_("Visual Theme"),_("Theme save failed: %s") % exc)

    def _plugin_language_selected(self, choice):
        if not choice:
            return
        code=str(choice[1] or "en")
        # Interface changes establish the matching TMDb Information Language as
        # the new default. The user can still override Information Language
        # independently afterwards.  AR + EN remains a manual special preset.
        description_code=default_description_for_interface(code)
        save_settings({"plugin_language":code,"description_language":description_code})
        set_plugin_language(code)
        # Refresh this screen immediately; other screens pick the language up
        # the next time they are opened. Layout geometry is intentionally unchanged.
        try:
            self["title"].setText(_("Settings"))
            self["info_title"].setText(_("Portal Settings"))
            self["info"].setText(_("Choose an option to configure playback, lists and privacy."))
            self["red"].setText(_("Back")); self["green"].setText(_("Change"))
            self["yellow"].setText(_("Diagnostics")); self["blue"].setText(_("Select"))
        except Exception:
            pass
        self.refresh()
        self["status"].setText(_("Language saved"))

    def _font_scale_selected(self,choice):
        if not choice:return
        save_settings({"font_scale":choice[1]});self.refresh()
        self["status"].setText(_("Font size saved. Restart Enigma2 to apply it everywhere."))
        self._context_open_notice(_("Font Size"),_("Font size saved. Restart Enigma2 to apply it everywhere."))

    def _save_choice(self,key,choice):
        if choice:
            save_settings({key:choice[1]}); self.refresh(); self["status"].setText(_("Setting saved"))

    def _save_pin(self,value):
        value=str(value or "")
        if value.isdigit() and 4 <= len(value) <= 8:
            save_settings(parental_hash_pin(value))
            parental_lock_now()
            self.refresh()
            self["status"].setText(_("Parental PIN saved securely"))
        elif value:
            self._context_open_notice(_("Parental PIN"),_("PIN must contain 4-8 digits"))

    def _save_parental_pin_and_enable(self,value):
        value=str(value or "")
        if not value:
            self.refresh()
            self["status"].setText(_("Parental lock remains disabled"))
            return
        if not (value.isdigit() and 4 <= len(value) <= 8):
            self.refresh()
            self._context_open_notice(_("Parental PIN"),_("PIN must contain 4-8 digits. Parental Lock was not enabled."))
            return
        payload=parental_hash_pin(value)
        payload["parental_lock"]=True
        save_settings(payload)
        parental_lock_now()
        self.refresh()
        self["status"].setText(_("Parental PIN saved • Parental lock enabled"))

    def _save_adult_keywords(self,value):
        if value is None:return
        words=[]
        for part in str(value).split(","):
            word=part.strip().casefold()
            if word and word not in words:words.append(word[:40])
        save_settings({"adult_keywords":words});self.refresh();self["status"].setText(_("Adult keywords saved"))

    def _open_backup_restore(self):
        rows=[((_("Create authenticated backup (this receiver)") if action=="backup_all" else _("Restore backup")), action) for label, action in backup_choices()]
        self._context_open_choice(self._backup_action,_('Backup & Restore'),rows,0,_('Choose a backup or restore action'))

    def _backup_action(self,choice):
        if not choice:return
        action=choice[1]
        if action=='backup_all':
            self._create_backup_now()
        elif action=='restore':
            backups=list_backups()
            if not backups:
                self._context_open_notice(_('Backup & Restore'),_('No backups found on HDD.\n\nExpected folder: /media/hdd/UltraStalker/Backup/'))
                return
            rows=[]
            for path in backups:
                try:
                    manifest=inspect_backup(path)
                    kind='FULL' if manifest.get('contains_secrets') else 'LEGACY'
                    label=os.path.basename(path)+'  •  '+str(manifest.get('plugin_version') or 'unknown')+'  •  '+kind
                except Exception:
                    label=os.path.basename(path)+'  •  INVALID'
                rows.append((label,path))
            self._context_open_choice(self._restore_selected,_('Choose backup to restore'),rows,0,_('Available Ultra Stalker backups'))

    def _create_backup_now(self):
        self['status'].setText(_('Creating complete HDD backup…'))
        def work(): return create_backup()
        def done(result):
            self['status'].setText(_('Backup: %s')%result['path'])
            self._context_open_notice(_('Backup Created'),_('Complete backup created on HDD:\n%s\n\nPortals, settings and user API credentials are included. Artwork cache is not copied.')%result['path'])
        def failed(exc): self._context_open_notice(_('Backup Failed'),_('Backup failed: %s')%exc)
        self._run_async(work,done,failed)

    def _restore_selected(self,choice):
        if not choice:return
        self._restore_path=choice[1]
        try:inspect_backup(self._restore_path)
        except Exception as exc:self._context_open_notice(_('Invalid Backup'),_('Invalid backup: %s')%exc);return
        self._context_open_notice(_('Confirm Restore'),_('Restore this backup completely?\n\nThe selected backup will be restored directly. No additional backup copy will be created.'),self._restore_confirmed,True)

    def _restore_confirmed(self,answer):
        if not answer:return
        path=self._restore_path
        self['status'].setText(_('Validating and restoring backup…'))
        def work(handle):
            return restore_backup(path,restore_secrets=True,cancel_event=handle.cancel_event)
        def done(result):
            self.refresh()
            self._context_open_notice(_('Restore Complete'),_('Restore complete. Restart Enigma2/plugin before continuing.\n\nRestored: %s')%(', '.join(result.get('restored',[]))))
        def failed(exc): self._context_open_notice(_('Restore Failed'),_('Restore failed: %s')%exc)
        self._run_async(work,done,failed)

    def _export_support_bundle(self):
        profile=dict(self.profile or {})
        def work(): return export_support_bundle('/tmp/ultrastalker-support.zip',profile)
        def done(path): self["status"].setText(_("Support bundle: %s")%path);self._context_open_notice(_("Support Bundle"),_("Redacted support bundle created:\n%s")%path)
        def failed(exc): self._context_open_notice(_("Support Bundle"),_("Support export failed: %s")%exc)
        self._run_async(work,done,failed)

    def _save_tmdb_credential(self, value):
        value=str(value or "").strip()
        try:
            save_tmdb_credential(value)
            self.refresh()
            self["status"].setText(_("TMDB credential saved privately in api_keys.conf") if value else _("TMDB credential cleared"))
        except Exception as exc:
            self._context_open_notice(_("TMDB"),_("TMDB credential save failed: %s")%exc)

    def _save_fanart_api_key(self, value):
        value=str(value or "").strip()[:4096]
        try:
            update_api_keys({"FANART_API_KEY":value})
            self.refresh()
            self["status"].setText(_("Fanart.tv API key saved privately in api_keys.conf") if value else _("Fanart.tv API key cleared"))
        except Exception as exc:
            self._context_open_notice(_("Fanart.tv"),_("Fanart.tv API key save failed: %s")%exc)

    def _save_subdl_api_key(self, value):
        value=str(value or "").strip()[:4096]
        try:
            update_api_keys({"SUBDL_API_KEY":value})
            self.refresh()
            self["status"].setText(_("SubDL API key saved privately in api_keys.conf") if value else _("SubDL API key cleared"))
        except Exception as exc:
            self._context_open_notice(_("Online Subtitles"),_("SubDL API key save failed: %s")%exc)

    def _save_subsource_api_key(self, value):
        value=str(value or "").strip()[:4096]
        try:
            update_api_keys({"SUBSOURCE_API_KEY":value})
            self.refresh()
            self["status"].setText(_("SubSource API key saved privately in api_keys.conf") if value else _("SubSource API key cleared"))
        except Exception as exc:
            self._context_open_notice(_("Online Subtitles"),_("SubSource API key save failed: %s")%exc)

    def open_diagnostics(self):
        self.session.open(DiagnosticsScreen,self.profile)


