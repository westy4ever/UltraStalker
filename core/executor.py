# -*- coding: utf-8 -*-
import itertools
import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor

class LazyThreadPoolExecutor:
    """Create worker threads only on first submit and shut them down centrally."""
    def __init__(self, max_workers=1, thread_name_prefix="ultrastalker"):
        self.max_workers=max(1,int(max_workers or 1)); self.thread_name_prefix=str(thread_name_prefix or "ultrastalker")
        self._lock=threading.RLock(); self._executor=None; self._shutdown=False; self._tasks={}
    def _ensure(self):
        with self._lock:
            if self._shutdown: raise RuntimeError("executor is shut down")
            if self._executor is None:
                self._executor=ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
            return self._executor
    def submit(self, fn, *args, **kwargs):
        """Submit work, with opt-in keyed duplicate/stale-job control.

        ``_task_key`` returns the already pending/running Future instead of
        queueing identical work. ``_replace_task_key`` cancels an older queued
        Future with the same key and queues the newest request. Running work is
        never force-killed; callers that replace focus jobs already provide
        their own cancellation token and therefore exit cooperatively.
        """
        task_key=kwargs.pop("_task_key",None)
        replace=bool(kwargs.pop("_replace_task_key",False))
        if task_key not in (None,""):
            with self._lock:existing=self._tasks.get(task_key)
            if existing is not None and not existing.done():
                if not replace:return existing
                try:existing.cancel()
                except Exception:pass
        future=self._ensure().submit(fn,*args,**kwargs)
        if task_key not in (None,""):
            with self._lock:self._tasks[task_key]=future
            def _forget(done,key=task_key):
                with self._lock:
                    if self._tasks.get(key) is done:self._tasks.pop(key,None)
            future.add_done_callback(_forget)
        return future
    def shutdown(self, wait=False, cancel_futures=True):
        with self._lock:
            self._shutdown=True; ex=self._executor; self._executor=None; self._tasks.clear()
        if ex is None: return
        try: ex.shutdown(wait=bool(wait), cancel_futures=bool(cancel_futures))
        except TypeError: ex.shutdown(wait=bool(wait))
    @property
    def started(self):
        with self._lock: return self._executor is not None


class PriorityLazyExecutor:
    """Bounded, non-blocking priority executor for global TMDB hydration.

    Selected/visible jobs must never block the Enigma2 GUI because Blue preload
    filled the queue.  Lower priority numbers win.  A high-priority job may
    evict one queued lower-priority job; running jobs are never interrupted.
    """
    _STOP_PRIORITY=10**9
    def __init__(self,max_workers=2,max_pending=16,thread_name_prefix="ultrastalker-priority"):
        self.max_workers=max(1,int(max_workers or 1));self.max_pending=max(self.max_workers,int(max_pending or 1))
        self.thread_name_prefix=str(thread_name_prefix or "ultrastalker-priority")
        self._queue=queue.PriorityQueue(maxsize=self.max_pending)
        self._seq=itertools.count();self._lock=threading.RLock();self._threads=[];self._shutdown=False;self._tasks={}
    def _ensure(self):
        with self._lock:
            if self._shutdown:raise RuntimeError("executor is shut down")
            while len(self._threads)<self.max_workers:
                idx=len(self._threads)+1
                t=threading.Thread(target=self._worker,name="%s_%d"%(self.thread_name_prefix,idx),daemon=True)
                t.start();self._threads.append(t)
    def _worker(self):
        while True:
            item=self._queue.get()
            try:
                priority,seq,future,fn,args,kwargs,task_key=item
                if fn is None:return
                if future is not None and future.set_running_or_notify_cancel():
                    try:future.set_result(fn(*args,**kwargs))
                    except BaseException as exc:future.set_exception(exc)
                if task_key not in (None,""):
                    with self._lock:
                        if self._tasks.get(task_key) is future:self._tasks.pop(task_key,None)
            finally:self._queue.task_done()
    def _evict_lower_priority(self,incoming_priority):
        """Evict one worst queued job when selected/visible work needs room."""
        q=self._queue
        try:
            with q.mutex:
                if not q.queue:return False
                worst_index=None;worst_key=None
                for idx,item in enumerate(q.queue):
                    if not item or item[3] is None:continue
                    key=(int(item[0]),int(item[1]))
                    if worst_key is None or key>worst_key:worst_key=key;worst_index=idx
                if worst_index is None or worst_key[0] <= int(incoming_priority):return False
                item=q.queue.pop(worst_index)
                import heapq;heapq.heapify(q.queue)
                q.unfinished_tasks=max(0,q.unfinished_tasks-1)
                try:
                    if item[2] is not None:item[2].cancel()
                except Exception:pass
                if len(item)>=7 and item[6] not in (None,""):
                    with self._lock:
                        if self._tasks.get(item[6]) is item[2]:self._tasks.pop(item[6],None)
                q.not_full.notify()
                return True
        except Exception:return False
    def submit(self,fn,*args,**kwargs):
        priority=int(kwargs.pop("priority",1) or 0)
        # GUI/background navigation callers default to non-blocking. Explicit
        # Blue orchestration may opt into a short blocking wait from its own worker.
        block=bool(kwargs.pop("_queue_block",False))
        timeout=kwargs.pop("_queue_timeout",None)
        task_key=kwargs.pop("_task_key",None)
        if task_key not in (None,""):
            with self._lock:existing=self._tasks.get(task_key)
            if existing is not None and not existing.done():
                self.reprioritize(task_key,priority);return existing
        self._ensure();future=Future()
        if task_key not in (None,""):
            with self._lock:self._tasks[task_key]=future
        item=(priority,next(self._seq),future,fn,args,kwargs,task_key)
        try:self._queue.put(item,block=False)
        except queue.Full:
            if priority<=1 and self._evict_lower_priority(priority):
                try:self._queue.put(item,block=False)
                except queue.Full:future.set_exception(RuntimeError("hydration queue is full"))
            elif block:
                try:self._queue.put(item,block=True,timeout=(0.75 if timeout is None else timeout))
                except queue.Full:future.set_exception(RuntimeError("hydration queue is full"))
            else:future.set_exception(RuntimeError("hydration queue is full"))
        if future.done() and task_key not in (None,""):
            with self._lock:
                if self._tasks.get(task_key) is future:self._tasks.pop(task_key,None)
        return future
    def reprioritize(self,task_key,new_priority=0):
        """Promote an already queued task without duplicating its work."""
        if task_key in (None,""):return False
        q=self._queue
        try:
            with q.mutex:
                for idx,item in enumerate(q.queue):
                    if len(item)>=7 and item[6]==task_key and item[3] is not None:
                        if int(item[0])<=int(new_priority):return True
                        q.queue[idx]=(int(new_priority),item[1],item[2],item[3],item[4],item[5],item[6])
                        import heapq;heapq.heapify(q.queue);q.not_empty.notify()
                        return True
        except Exception:return False
        return False

    def shutdown(self,wait=False,cancel_futures=True):
        with self._lock:
            self._shutdown=True;threads=list(self._threads);self._threads=[]
        if cancel_futures:
            try:
                while True:
                    item=self._queue.get_nowait()
                    try:
                        if item and item[2] is not None:item[2].cancel()
                    except Exception:pass
                    self._queue.task_done()
            except queue.Empty:pass
            with self._lock:self._tasks={k:f for k,f in self._tasks.items() if not f.cancelled() and not f.done()}
        # PriorityQueue requires comparable sentinels; never enqueue bare None.
        for _ in threads:
            stop=(self._STOP_PRIORITY,next(self._seq),None,None,(),{},None)
            try:self._queue.put_nowait(stop)
            except queue.Full:
                try:self._queue.put(stop,timeout=0.25)
                except Exception:pass
        if wait:
            for t in threads:
                try:t.join(timeout=2.0)
                except Exception:pass
    @property
    def started(self):
        with self._lock:return bool(self._threads)
    @property
    def pending(self):
        try:return int(self._queue.qsize())
        except Exception:return 0

