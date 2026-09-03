# -*- coding: utf-8 -*-
"""Playback/service lifecycle helpers extracted from player.py without behavior changes."""
from __future__ import absolute_import, print_function

import gc
import os
import signal
import time

from enigma import eServiceReference

from ..storage import add_recently_played, load_settings
from ..core.runtime_log import breadcrumb as runtime_breadcrumb
from ..log import optional_failure
from .player_runtime import _matching_external_player_pids, _descendant_pids
from .player_process import malloc_trim as _malloc_trim, force_session_silence


class PlayerServiceLifecycleMixin(object):
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
