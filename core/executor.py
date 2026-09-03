# -*- coding: utf-8 -*-
import threading
from concurrent.futures import ThreadPoolExecutor

class LazyThreadPoolExecutor:
    """Create worker threads only on first submit and shut them down centrally."""
    def __init__(self, max_workers=1, thread_name_prefix="ultrastalker"):
        self.max_workers=max(1,int(max_workers or 1)); self.thread_name_prefix=str(thread_name_prefix or "ultrastalker")
        self._lock=threading.RLock(); self._executor=None; self._shutdown=False
    def _ensure(self):
        with self._lock:
            if self._shutdown: raise RuntimeError("executor is shut down")
            if self._executor is None:
                self._executor=ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
            return self._executor
    def submit(self, fn, *args, **kwargs): return self._ensure().submit(fn,*args,**kwargs)
    def shutdown(self, wait=False, cancel_futures=True):
        with self._lock:
            self._shutdown=True; ex=self._executor; self._executor=None
        if ex is None: return
        try: ex.shutdown(wait=bool(wait), cancel_futures=bool(cancel_futures))
        except TypeError: ex.shutdown(wait=bool(wait))
    @property
    def started(self):
        with self._lock: return self._executor is not None
