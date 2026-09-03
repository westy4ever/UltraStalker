# -*- coding: utf-8 -*-
"""Online subtitle timing/runtime helpers for UltraStalkerPlayer.

Extracted from player.py without changing method names or behaviour.
"""
import json
import os, threading

from Screens.MessageBox import MessageBox
from ..log import optional_failure
from .subtitles_online import parse_srt


class OnlineSubtitleRuntimeMixin(object):
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
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            optional_failure("player.subtitle_sync_load",exc)

    def _save_subtitle_sync(self):
        path=self._subtitle_sync_file()
        if not path:return
        try:
            temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
            with open(temp,"w",encoding="utf-8") as h:
                json.dump({"offset_ms":int(self._online_subtitle_offset_ms),"scale":float(self._online_subtitle_scale)},h,separators=(",",":"))
                h.flush();os.fsync(h.fileno())
            try:os.chmod(temp,0o600)
            except OSError:pass
            os.replace(temp,path)
        except Exception as exc:
            try:
                if "temp" in locals() and os.path.exists(temp):os.unlink(temp)
            except OSError:pass
            optional_failure("player.subtitle_sync_save",exc)

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
        except Exception as exc:
            optional_failure("player.subtitle_seek_position",exc)
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
        except Exception as exc:optional_failure("player.subtitle_timer_restart",exc)

