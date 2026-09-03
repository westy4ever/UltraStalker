# -*- coding: utf-8 -*-
"""Small cancellable worker pool used by Enigma2 screens.

Us31 hardening keeps explicit track of every active task so plugin shutdown
can cancel work deterministically instead of leaving daemon-style operations
running against state that is being restored or torn down.
"""
import inspect
import itertools
import threading
import time
from .executor import LazyThreadPoolExecutor

from ..log import get_logger

LOG = get_logger()


class TaskHandle:
    def __init__(self, task_id, key=None):
        self.task_id = task_id
        self.key = key
        self.cancel_event = threading.Event()
        self.future = None
        self._cancel_lock = threading.RLock()
        self._cancel_callbacks = []

    def add_cancel_callback(self, callback):
        if not callable(callback):
            return
        run_now = False
        with self._cancel_lock:
            if self.cancel_event.is_set():
                run_now = True
            elif callback not in self._cancel_callbacks:
                self._cancel_callbacks.append(callback)
        if run_now:
            try:
                callback()
            except Exception as exc:
                LOG.debug("Late task cancel callback failed task=%s: %s", self.task_id, exc)

    def cancel(self):
        self.cancel_event.set()
        if self.future is not None:
            self.future.cancel()
        with self._cancel_lock:
            callbacks = list(self._cancel_callbacks)
            self._cancel_callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                LOG.debug("Task cancel callback failed task=%s: %s", self.task_id, exc)

    def clear_cancel_callbacks(self):
        with self._cancel_lock:
            self._cancel_callbacks = []

    def cancelled(self):
        return self.cancel_event.is_set()

    def wait(self, seconds):
        return self.cancel_event.wait(seconds)


class TaskManager:
    def __init__(self, workers=3):
        self._workers = max(1, int(workers or 3))
        self._executor = LazyThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="ultrastalker-core")
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active = {}          # keyed de-duplication
        self._handles = {}         # all active task_id -> handle
        self._shutdown = False
        self._thread_local = threading.local()

    def submit(self, func, callback_queue, ok=None, fail=None, key=None):
        with self._lock:
            if self._shutdown:
                return None
            if key and key in self._active:
                return self._active[key]
            handle = TaskHandle(next(self._ids), key)
            if key:
                self._active[key] = handle
            self._handles[handle.task_id] = handle

        def run():
            self._thread_local.task_id = handle.task_id
            try:
                if handle.cancelled():
                    return
                try:
                    wants_token = len(inspect.signature(func).parameters) > 0
                except Exception:
                    wants_token = False
                value = func(handle) if wants_token else func()
                if not handle.cancelled():
                    callback_queue.put((ok, value, False, handle.task_id))
            except Exception as exc:
                if not handle.cancelled():
                    callback_queue.put((fail, exc, True, handle.task_id))
            finally:
                try: self._thread_local.task_id = None
                except Exception: pass
                handle.clear_cancel_callbacks()
                with self._condition:
                    if key and self._active.get(key) is handle:
                        self._active.pop(key, None)
                    self._handles.pop(handle.task_id, None)
                    self._condition.notify_all()

        def cancelled_before_start(future):
            # concurrent.futures never invokes ``run`` when a queued Future is
            # cancelled before a worker picks it up. Without this callback the
            # handle would remain forever in _handles/_active, making backup
            # wait_idle() time out and slowly leaking task bookkeeping during
            # rapid page flips.
            if not future.cancelled():
                return
            handle.clear_cancel_callbacks()
            with self._condition:
                if key and self._active.get(key) is handle:
                    self._active.pop(key, None)
                self._handles.pop(handle.task_id, None)
                self._condition.notify_all()
        try:
            handle.future = self._executor.submit(run)
            handle.future.add_done_callback(cancelled_before_start)
        except Exception:
            with self._condition:
                if key and self._active.get(key) is handle:
                    self._active.pop(key, None)
                self._handles.pop(handle.task_id, None)
                self._condition.notify_all()
            raise
        return handle

    def current_task_id(self):
        return getattr(self._thread_local, "task_id", None)

    def cancel_all(self, exclude_task_id=None):
        with self._lock:
            handles = [handle for task_id, handle in self._handles.items() if task_id != exclude_task_id]
        for handle in handles:
            handle.cancel()
        return len(handles)

    def wait_idle(self, timeout=5.0, exclude_task_id=None):
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        with self._condition:
            while any(task_id != exclude_task_id for task_id in self._handles):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(0.2, remaining))
            return True

    def active_count(self):
        with self._lock:
            return len(self._handles)

    def shutdown(self, wait=False):
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancel()
        try:
            self._executor.shutdown(wait=bool(wait), cancel_futures=True)
        except TypeError:  # Python versions without cancel_futures.
            self._executor.shutdown(wait=bool(wait))


TASKS = TaskManager(4)
