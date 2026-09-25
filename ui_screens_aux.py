"""Auxiliary UI screens extracted from ui.py without changing behavior."""

from . import _
import hashlib
import os

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer

# PERF45: the Downloads screen is imported during normal UI startup, but the
# download engine itself is only needed when this screen is actually opened.
# Keep a tiny facade here so importing auxiliary screens stays side-effect free.
class _LazyDownloadsFacade(object):
    def _manager(self):
        from .downloads import MANAGER as _MANAGER
        return _MANAGER
    def snapshot(self): return self._manager().snapshot()
    def cancel(self, jid): return self._manager().cancel(jid)
    def retry(self, jid, resolver=None): return self._manager().retry(jid, resolver)

DOWNLOADS = _LazyDownloadsFacade()
from .log import optional_failure
from .ui_parts.catalog import clean_display_text as _clean_display_text
from .ui_dynamic_chrome import _build_aux_adaptive_chrome

IconMenuList = None
_information_sections = None

def configure_aux_screens(icon_menu_list, information_sections):
    global IconMenuList, _information_sections
    IconMenuList = icon_menu_list
    _information_sections = information_sections

class AdaptiveInformationScreen(Screen):
    skin = """<screen name="AdaptiveInformationScreen" position="0,0" size="1920,1080" backgroundColor="#02060b" flags="wfNoBorder">
        <widget name="frame" position="300,120" size="1320,820" alphatest="blend" zPosition="1" />
        <widget name="header" position="365,155" size="1180,52" font="Regular;36" foregroundColor="#ffffff" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="title" position="365,205" size="1180,36" font="Regular;22" foregroundColor="#9ec9dc" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>

        <widget name="overview_bg" position="350,255" size="1220,300" alphatest="blend" zPosition="2" />
        <widget name="overview_title" position="375,270" size="1170,34" font="Regular;19" foregroundColor="#9fd8ed" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="overview" position="385,310" size="1150,225" font="Regular;23" foregroundColor="#f3f5f7" transparent="1" zPosition="6"  shadowColor="#000000" shadowOffset="1,1"/>

        <widget name="cast_bg" position="350,575" size="560,150" alphatest="blend" zPosition="2" />
        <widget name="cast_title" position="370,588" size="520,30" font="Regular;18" halign="center" valign="center" foregroundColor="#9fd8ed" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="cast" position="375,620" size="510,90" font="Regular;20" halign="center" valign="center" foregroundColor="#f3f5f7" transparent="1" zPosition="6"  shadowColor="#000000" shadowOffset="1,1"/>

        <widget name="director_bg" position="930,575" size="280,150" alphatest="blend" zPosition="2" />
        <widget name="director_title" position="945,588" size="250,30" font="Regular;18" halign="center" valign="center" foregroundColor="#9fd8ed" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="director" position="945,620" size="250,90" font="Regular;20" halign="center" valign="center" foregroundColor="#f3f5f7" transparent="1" zPosition="6"  shadowColor="#000000" shadowOffset="1,1"/>

        <widget name="writer_bg" position="1230,575" size="340,150" alphatest="blend" zPosition="2" />
        <widget name="writer_title" position="1245,588" size="310,30" font="Regular;18" halign="center" valign="center" foregroundColor="#9fd8ed" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="writer" position="1245,620" size="310,90" font="Regular;20" halign="center" valign="center" foregroundColor="#f3f5f7" transparent="1" zPosition="6"  shadowColor="#000000" shadowOffset="1,1"/>

        <ePixmap position="350,785" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_red.png" alphatest="blend" zPosition="3" />
        <widget name="red" position="350,785" size="310,58" font="Regular;23" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="scroll_hint" position="700,795" size="650,38" font="Regular;19" foregroundColor="#9fb0c0" transparent="1" zPosition="5"  shadowColor="#000000" shadowOffset="1,1"/>
    </screen>"""
    def __init__(self,session,title,text,source_path="",info_item=None,info_parent=None):
        Screen.__init__(self,session); self._source_path=str(source_path or "")
        self._sections=_information_sections(info_item,info_parent,text)
        self["frame"]=Pixmap(); self["header"]=Label(_("INFORMATION"))
        self["title"]=Label(_clean_display_text(title,120))
        for n in ("overview_bg","cast_bg","director_bg","writer_bg"): self[n]=Pixmap()
        self["overview_title"]=Label(_("OVERVIEW"))
        self["overview"]=Label(_clean_display_text(self._sections.get("overview"),1200))
        self["cast_title"]=Label(_("Cast"))
        self["cast"]=Label(_clean_display_text(self._sections.get("cast"),350))
        self["director_title"]=Label(_("Director"))
        self["director"]=Label(_clean_display_text(self._sections.get("director"),160))
        self["writer_title"]=Label(_("Writer"))
        self["writer"]=Label(_clean_display_text(self._sections.get("writer"),200))
        self["red"]=Label(_("Close"))
        self["scroll_hint"]=Label(_("UP / DOWN  Scroll"))
        self["actions"]=ActionMap(["OkCancelActions","ColorActions","DirectionActions"],{"cancel":self.close,"red":self.close,"ok":self.close},-1)
        self.onLayoutFinish.append(self._finish_layout)
    def _finish_layout(self):
        self._apply_chrome()
        # Labels are used deliberately here instead of ScrollLabel: OpenBH can
        # leave an empty ScrollLabel instance on some skins even when text is set.
        for n in ("overview","cast","director","writer"):
            try:self[n].show()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
    def _apply_chrome(self):
        key=hashlib.sha1((self._source_path+"|info175").encode("utf-8","ignore")).hexdigest()[:16]
        chrome=_build_aux_adaptive_chrome(self._source_path,key)
        mapping=(("frame","info_panel"),("overview_bg","info_overview"),("cast_bg","info_cast"),("director_bg","info_director"),("writer_bg","info_writer"))
        for name,asset_key in mapping:
            path=chrome.get(asset_key)
            try:
                if path and os.path.isfile(path) and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path); self[name].show()
            except Exception as exc:optional_failure("ui.adaptive_info_chrome",exc)


class DownloadsManagerScreen(Screen):
    skin = """<screen name="DownloadsManagerScreen" position="0,0" size="1920,1080" backgroundColor="#02060b" flags="wfNoBorder">
        <widget name="frame" position="320,140" size="1280,800" alphatest="blend" zPosition="1" />
        <widget name="inner" position="370,250" size="1180,590" alphatest="blend" zPosition="2" />
        <widget name="title" position="390,185" size="1120,54" font="Regular;38" foregroundColor="#ffffff" transparent="1" zPosition="4"  shadowColor="#000000" shadowOffset="1,1"/>
        <widget name="list" position="415,275" size="1090,500" scrollbarMode="showNever" transparent="1" zPosition="5" />
        <ePixmap position="390,850" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_red.png" alphatest="blend" zPosition="3"/><widget name="red" position="390,850" size="310,58" font="Regular;23" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
        <ePixmap position="760,850" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_yellow.png" alphatest="blend" zPosition="3"/><widget name="yellow" position="760,850" size="310,58" font="Regular;23" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
        <ePixmap position="1130,850" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_green.png" alphatest="blend" zPosition="3"/><widget name="green" position="1130,850" size="310,58" font="Regular;23" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
    </screen>"""
    def __init__(self,session,source_path=""):
        Screen.__init__(self,session); self.setTitle(_("Downloads")); self._source_path=str(source_path or ""); self._chrome={}
        self["frame"]=Pixmap(); self["inner"]=Pixmap(); self["title"]=Label(_("Downloads"))
        self["list"]=IconMenuList([],width=1090,item_height=80,icon_size=40,primary_font=23,secondary_font=17,row_style="download_glass")
        self["red"]=Label(_("Close")); self["yellow"]=Label(_("Cancel selected")); self["green"]=Label(_("Retry failed"))
        self["actions"]=ActionMap(["OkCancelActions","ColorActions","DirectionActions"],{"cancel":self.close,"red":self.close,"yellow":self.cancel_selected,"green":self.retry_selected,"up":lambda:self["list"].wrap_up(),"down":lambda:self["list"].wrap_down(),"left":lambda:self["list"].page_left(),"right":lambda:self["list"].page_right()},-1)
        self._rows=[]; self._rendering=False; self._timer=eTimer(); self._timer.callback.append(self.refresh); self["list"].onSelectionChanged.append(self._selection_changed); self.onLayoutFinish.append(self._start); self.onClose.append(self._stop)
    def _apply_chrome(self):
        key=hashlib.sha1((self._source_path+"|downloads174").encode("utf-8","ignore")).hexdigest()[:16]
        self._chrome=_build_aux_adaptive_chrome(self._source_path,key)
        for name,key_name in (("frame","download_panel"),("inner","download_inner")):
            try:
                path=self._chrome.get(key_name)
                if path and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
            except Exception as exc:optional_failure("ui.download_panel_chrome",exc)
    def _start(self):
        self._apply_chrome()
        try:
            if self["list"].instance is not None:
                self["list"].instance.setSelectionEnable(0)
        except Exception as exc:optional_failure("ui.download_selection_disable",exc)
        self.refresh();self._timer.start(1000,False)
    def _stop(self):
        try:self._timer.stop()
        except Exception as exc:optional_failure("ui.download_timer_stop",exc)
        try:
            if self.refresh in self._timer.callback:self._timer.callback.remove(self.refresh)
        except Exception as exc:optional_failure("ui.download_timer_callback",exc)
        try:
            callbacks=self["list"].onSelectionChanged
            if self._selection_changed in callbacks:callbacks.remove(self._selection_changed)
        except Exception as exc:optional_failure("ui.download_selection_callback",exc)
    def _selection_changed(self):
        if not self._rendering:self._render_rows()
    def _render_rows(self):
        if self._rendering:return
        self._rendering=True
        selected=self["list"].getSelectedIndex() if self._rows else 0
        rows=[]
        for i,row in enumerate(self._rows):
            done=int(row.get("downloaded") or 0); total=int(row.get("total") or 0); pct=int(done*100.0/total) if total else 0
            status=_(str(row.get("status") or "queued").upper()); size="%.1f MB"%(done/(1024.0*1024.0)); progress=("%d%%"%pct) if total else size
            meta={"selected":bool(i==selected),"status":status,"progress":progress,"row_asset":self._chrome.get("download_row"),"row_selected_asset":self._chrome.get("download_row_selected")}
            rows.append((_clean_display_text(row.get("title") or _("Download"),100),"",row,meta))
        if not rows:
            rows=[(_("No downloads yet"),"",None,{"selected":True,"status":"","progress":"","row_asset":self._chrome.get("download_row"),"row_selected_asset":self._chrome.get("download_row_selected")})]
        try:
            self["list"].set_icon_rows(rows)
            if self._rows:
                try:self["list"].moveToIndex(min(selected,len(self._rows)-1))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
        finally:
            self._rendering=False
    def refresh(self):
        selected=self["list"].getSelectedIndex() if self._rows else 0
        self._rows=DOWNLOADS.snapshot(); self._render_rows()
        if self._rows:
            try:self["list"].moveToIndex(min(selected,len(self._rows)-1))
            except Exception as exc:optional_failure("ui.silent_guard",exc)
    def _selected_job(self):
        idx=self["list"].getSelectedIndex(); return self._rows[idx] if 0<=idx<len(self._rows) else None
    def cancel_selected(self):
        row=self._selected_job()
        if row:DOWNLOADS.cancel(row.get("id"));self.refresh()
    def retry_selected(self):
        row=self._selected_job()
        if row and row.get("status") in ("failed","cancelled","paused"):
            self.session.open(MessageBox,_("Re-open the movie/episode and choose Download again to refresh its portal link."),MessageBox.TYPE_INFO,timeout=7)


