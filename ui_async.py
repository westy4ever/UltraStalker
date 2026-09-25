"""Async screen lifecycle mixin extracted from ui.py without changing behavior."""

import queue

from . import _

from enigma import eTimer

from .core.tasks import TASKS
from .log import optional_failure

class AsyncScreenMixin:
    def _async_init(self):
        self._jobs = queue.Queue()
        self._busy = False
        self._screen_closed = False
        self._poll = eTimer()
        self._poll_connection = None
        try:
            self._poll_connection = self._poll.timeout.connect(self._drain_jobs)
        except Exception:
            try:
                self._poll.callback.append(self._drain_jobs)
            except Exception as exc:
                optional_failure("ui", exc)
        # Adaptive GUI delivery heartbeat: fast only while work is actually
        # pending, relaxed while the screen is idle.  This keeps completed
        # worker results responsive without waking Enigma2 ~8 times/sec forever.
        self._async_poll_interval = 600
        self._poll.start(self._async_poll_interval, False)
        try:
            self.onHide.append(self._pause_async_poll)
            self.onShow.append(self._resume_async_poll)
        except Exception as exc:optional_failure("ui.async_visibility_hooks",exc)

    def _pause_async_poll(self):
        if self._screen_closed:return
        try:self._poll.stop()
        except Exception:pass

    def _resume_async_poll(self):
        if self._screen_closed:return
        try:
            self._drain_jobs()
            self._async_set_poll_interval(120 if self._async_poll_has_pending_work() else 600)
        except Exception as exc:optional_failure("ui.async_resume_poll",exc)

    def _async_set_poll_interval(self, interval_ms):
        """Keep the repeating UI heartbeat alive at the requested cadence.

        PerfLab5: child screens stop ``_poll`` on hide.  On return the desired
        interval is often unchanged (usually 600 ms), so the old equality guard
        returned without restarting the stopped eTimer.  Background poster jobs
        then completed normally but their result queues were never drained until
        the whole Grid screen was reopened.
        """
        if self._screen_closed:return
        try:
            interval=max(80,int(interval_ms or 600))
            same=int(getattr(self,"_async_poll_interval",0) or 0)==interval
            active=False
            try:active=bool(self._poll.isActive())
            except Exception:active=False
            if same and active:return
            try:self._poll.stop()
            except Exception:pass
            self._async_poll_interval=interval
            self._poll.start(interval,False)
        except Exception as exc:optional_failure("ui.async_poll_interval",exc)

    @staticmethod
    def _async_future_pending(value):
        try:return value is not None and hasattr(value,"done") and not value.done()
        except Exception:return False

    def _async_poll_has_pending_work(self):
        """Cheap RAM-only pending-work probe; never touches HDD or network."""
        if self._screen_closed:return False
        if bool(getattr(self,"_busy",False)):return True
        active=getattr(self,"_active_async_handle",None)
        if active is not None:
            try:
                fut=getattr(active,"future",None)
                if fut is None or not fut.done():return True
            except Exception:return True
        for handle in list(getattr(self,"_task_handles",[]) or []):
            try:
                fut=getattr(handle,"future",None)
                if not handle.cancelled() and (fut is None or not fut.done()):return True
            except Exception:continue
        # Screen-local delivery queues used by Grid, Search, Details, Home, Live
        # and Cinematic. Queue.empty() is only a memory check here.
        for name in (
            "_jobs","_art_prefetch_jobs","_quality_prefetch_jobs","_poster_hud_jobs",
            "_epg_jobs","_grid_download_jobs","_grid_accent_jobs","_grid_mood_jobs",
            "_image_jobs","_poster_jobs","_search_partial_jobs","_backdrop_jobs",
            "_adaptive_jobs","_title_logo_jobs","_tmdb_jobs","_home_jobs",
            "_portal_progress_jobs","_series_jobs","_preview_jobs","_live_chrome_jobs",
            "_pgv2_hero_jobs","_pgv2_material_jobs","_page_prefetch_future_jobs"):
            q=getattr(self,name,None)
            if q is None:continue
            try:
                if not q.empty():return True
            except Exception:continue
        # Detached visual/network futures may still be computing while their
        # result queue is empty; keep the fast cadence until they settle.
        for name in (
            "_image_future","_epg_future","_live_picon_cache_future",
            "_pgv2_hero_future","_pgv2_material_future","_preview_handle",
            "_hero_pin_future","_cin_focus_future","_cin_background_future"):
            if self._async_future_pending(getattr(self,name,None)):return True
        for name in ("_page_prefetch_futures","_poster_futures","_recent_futures","_details_worker_futures"):
            values=getattr(self,name,None)
            if not values:continue
            try:
                if any(self._async_future_pending(value) for value in list(values)):return True
            except Exception:pass
        futures=getattr(self,"_grid_download_futures",None)
        if isinstance(futures,dict):
            try:
                if any(self._async_future_pending(value) for value in list(futures.values())):return True
            except Exception:pass
        return False

    def _run_async(self, func, ok, fail=None):
        if self._busy or self._screen_closed:
            return False
        self._busy = True

        try:
            handle = TASKS.submit(func, self._jobs, ok=ok, fail=fail)
            if handle is None:
                self._busy = False
                return False
            # Task cancellation is request-scoped through TaskHandle.cancel_event.
            # Closing every socket on the shared StalkerClient can abort unrelated
            # Details/EPG/Grid work and was a source of page-flip instability.
            if not hasattr(self, "_task_handles"):
                self._task_handles = []
            # Cancelled workers intentionally do not enqueue a UI callback.
            # Prune those handles here so repeated page flips cannot grow a
            # screen-local list for its whole lifetime.
            self._task_handles=[old for old in self._task_handles
                if not old.cancelled() and not (old.future is not None and old.future.done())]
            self._task_handles.append(handle)
            self._active_async_handle = handle
            # A new request must not wait for the relaxed idle heartbeat.
            self._async_set_poll_interval(120)
        except Exception as exc:
            self._busy = False
            self._jobs.put((fail, exc, True))
            return False
        return handle

    def _cancel_active_async(self):
        """Cancel the screen's current request without closing the screen."""
        active=getattr(self,"_active_async_handle",None)
        if active is not None:
            try: active.cancel()
            except Exception as exc: optional_failure("ui.async_cancel",exc)
        for handle in list(getattr(self,"_task_handles",[]) or []):
            try: handle.cancel()
            except Exception as exc: optional_failure("ui.async_cancel_all",exc)
        self._task_handles=[]
        self._active_async_handle=None
        self._busy=False

    def _drain_jobs(self):
        if self._screen_closed:
            return
        while True:
            try:
                job = self._jobs.get_nowait()
                callback, value, is_error = job[:3]
                task_id = job[3] if len(job) > 3 else None
            except queue.Empty:
                break
            is_active_result = task_id is None
            if task_id is not None and hasattr(self,"_task_handles"):
                self._task_handles=[handle for handle in self._task_handles if handle.task_id != task_id]
                active=getattr(self,"_active_async_handle",None)
                if active is not None and active.task_id == task_id:
                    self._active_async_handle = None
                    is_active_result = True
            # A cancelled/obsolete worker may already have queued a callback before
            # the newest page request was submitted. Never let that stale result
            # clear the busy flag belonging to the newer active task.
            if is_active_result:
                self._busy = False
            elif task_id is not None:
                # A superseded task may have finished just before cancellation.
                # Its callback belongs to the previous request and must never
                # mutate this screen after a newer request became active.
                continue
            if callback:
                try:
                    callback(value)
                except Exception as exc:
                    try:
                        self["status"].setText(_("UI error: %s") % exc)
                    except Exception as exc:
                        optional_failure("ui", exc)
            elif is_error:
                try:
                    self["status"].setText(str(value))
                except Exception as exc:
                    optional_failure("ui", exc)
        # Stay responsive while any worker/delivery queue is active; otherwise
        # back off to five checks per three seconds. Hidden screens stop entirely.
        self._async_set_poll_interval(120 if self._async_poll_has_pending_work() else 600)

    def _stop_async(self):
        self._screen_closed = True
        self._busy = False
        for handle in getattr(self, "_task_handles", []):
            try: handle.cancel()
            except Exception as exc: optional_failure("ui",exc)
        self._task_handles = []
        self._active_async_handle = None
        # onHide/onShow are screen-owned callback lists, but explicitly removing
        # the bound methods breaks lifecycle reference chains immediately on
        # images that keep a closed Screen alive for one more event-loop turn.
        for hook_name, callback in (("onHide", self._pause_async_poll), ("onShow", self._resume_async_poll)):
            try:
                hooks=getattr(self, hook_name, None)
                if hooks is not None and callback in hooks: hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.async_visibility_unhook", exc)
        try:
            self._poll.stop()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._poll_connection is not None:
                self._poll_connection.disconnect()
        except Exception as exc:
            optional_failure("ui", exc)
        self._poll_connection = None
        try:
            if self._drain_jobs in self._poll.callback:
                self._poll.callback.remove(self._drain_jobs)
        except Exception as exc:
            optional_failure("ui", exc)
        # Discard callbacks/results already queued by workers that won the race
        # with cancellation. They are obsolete once the Screen is closed.
        try:
            while True: self._jobs.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:
            optional_failure("ui.async_queue_drain", exc)

