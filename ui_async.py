"""Async screen lifecycle mixin extracted from ui.py without changing behavior."""

import queue

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
        self._poll.start(90, False)
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
            self._poll.start(90,False)
        except Exception as exc:optional_failure("ui.async_resume_poll",exc)

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
                        self["status"].setText("UI error: %s" % exc)
                    except Exception as exc:
                        optional_failure("ui", exc)
            elif is_error:
                try:
                    self["status"].setText(str(value))
                except Exception as exc:
                    optional_failure("ui", exc)
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

