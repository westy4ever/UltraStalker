# -*- coding: utf-8 -*-
"""Single heavy-image budget for receiver stability.

All Pillow-heavy generation paths share one semaphore so TMDB hydration,
Home, Grid, Details and Player cannot decode/generate large images in parallel.
Network concurrency remains controlled separately by the global hydration queue.
"""
import threading
from contextlib import contextmanager

_IMAGE_WORK_SEMAPHORE = threading.BoundedSemaphore(1)
_IMAGE_WORK_LOCAL = threading.local()

@contextmanager
def image_work(label=""):
    # Re-entrant for the same worker thread: a high-level image builder may call
    # a lower-level normalized/derived builder while it already owns the budget.
    depth=int(getattr(_IMAGE_WORK_LOCAL,"depth",0) or 0)
    acquired=False
    if depth<=0:
        _IMAGE_WORK_SEMAPHORE.acquire();acquired=True
    _IMAGE_WORK_LOCAL.depth=depth+1
    try:
        yield
    finally:
        _IMAGE_WORK_LOCAL.depth=max(0,int(getattr(_IMAGE_WORK_LOCAL,"depth",1) or 1)-1)
        if acquired:_IMAGE_WORK_SEMAPHORE.release()

def image_budgeted(func):
    def wrapped(*args, **kwargs):
        with image_work(getattr(func, "__name__", "image")):
            return func(*args, **kwargs)
    wrapped.__name__ = getattr(func, "__name__", "image_budgeted")
    wrapped.__doc__ = getattr(func, "__doc__", None)
    return wrapped
