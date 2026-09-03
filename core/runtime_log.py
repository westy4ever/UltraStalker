# -*- coding: utf-8 -*-
"""Privacy-safe, non-blocking runtime breadcrumbs for silent Enigma2 restarts."""
from __future__ import absolute_import
import json
import os
import queue
import threading
import time

from .. import persistent_cache
from .maintenance import runtime_health
from .endurance import ENDURANCE
from ..log import redact as _redact_log_value

_MAX_BYTES=1024*1024
_QUEUE=queue.Queue(maxsize=256)
_STOP=threading.Event()
_THREAD=None
_THREAD_LOCK=threading.RLock()
_FINALIZED=False
_PERSIST_TO_HDD=False
_IDLE=threading.Event();_IDLE.set()
_TMP_LOG_DIR="/tmp/ultrastalker"
_TMP_LOG_PATH=os.path.join(_TMP_LOG_DIR,"runtime.log")
_UI_BASELINE={}
_UI_BASELINE_LOCK=threading.RLock()
_ENDURANCE_SAMPLE_INTERVAL_S=60.0
_LAST_ENDURANCE_SAMPLE=0.0

def _health_number(value):
    try:
        text=str(value or "").strip().split()[0]
        return int(text)
    except (TypeError,ValueError,IndexError):
        return 0

def _annotate_ui_guard(row):
    """Annotate suspicious process growth; never mutate runtime behavior."""
    try:
        if not str(row.get("event") or "").startswith("ui_"):
            return
        health=row.get("health") if isinstance(row.get("health"),dict) else {}
        current={
            "rss_kb":_health_number(health.get("vmrss")),
            "threads":_health_number(health.get("threads") or health.get("Threads")),
            "fds":_health_number(health.get("open_fds")),
        }
        with _UI_BASELINE_LOCK:
            if not _UI_BASELINE:
                _UI_BASELINE.update(current)
                row["guard_baseline"]=True
                return
            warnings=[]
            rss_delta=current["rss_kb"]-int(_UI_BASELINE.get("rss_kb") or 0)
            thread_delta=current["threads"]-int(_UI_BASELINE.get("threads") or 0)
            fd_delta=current["fds"]-int(_UI_BASELINE.get("fds") or 0)
            if current["rss_kb"] and rss_delta > 96*1024:
                warnings.append("rss+%dMB"%int(rss_delta/1024))
            if current["threads"] and thread_delta > 8:
                warnings.append("threads+%d"%thread_delta)
            if current["fds"] and fd_delta > 32:
                warnings.append("fds+%d"%fd_delta)
            if warnings:
                row["guard"]=",".join(warnings)[:80]
    except Exception:
        pass


def _safe_row(event, fields):
    row={"ts":int(time.time()),"event":str(event or "runtime")[:48]}
    for key,value in fields.items():
        if isinstance(value,(bool,int,float)) or value is None:row[str(key)[:32]]=value
        else:row[str(key)[:32]]=_redact_log_value(value)[:80]
    return row


def configure_runtime_log(persistent=False):
    """Select breadcrumb durability after runtime settings are available.

    Normal operation keeps breadcrumbs in /tmp so playback events never wake a
    sleeping HDD. Diagnostic mode may opt into the persistent HDD log.
    """
    global _PERSIST_TO_HDD
    _PERSIST_TO_HDD=bool(persistent)


def _write_row(row):
    try:
        try:row["health"]=runtime_health()
        except Exception:pass
        _annotate_ui_guard(row)
        payload=(json.dumps(row,separators=(",",":"),sort_keys=True)+"\n").encode("utf-8","replace")
        if _PERSIST_TO_HDD:
            log_dir=os.path.join(persistent_cache.ROOT,"logs")
            path=os.path.join(log_dir,"runtime.log")
            if not persistent_cache.persistent_write_gate(path):
                return
            try:os.chmod(log_dir,0o700)
            except OSError:pass
            if os.path.isfile(path) and os.path.getsize(path)>_MAX_BYTES:
                if not persistent_cache.hdd_read_ready(force=True):
                    return
                with open(path,"rb") as src:
                    src.seek(max(0,os.path.getsize(path)-(_MAX_BYTES//2)));tail=src.read()
                cut=tail.find(b"\n")
                if cut>=0:tail=tail[cut+1:]
                if not persistent_cache.persistent_write_gate(path):
                    return
                with open(path,"wb") as dst:
                    dst.write(tail);dst.flush();os.fsync(dst.fileno())
            if not persistent_cache.persistent_write_gate(path):
                return
            with open(path,"ab") as handle:
                handle.write(payload);handle.flush();os.fsync(handle.fileno())
            try:os.chmod(path,0o600)
            except OSError:pass
            return

        # Default path is volatile and deliberately independent of /media/hdd.
        os.makedirs(_TMP_LOG_DIR,mode=0o700,exist_ok=True)
        path=_TMP_LOG_PATH
        if os.path.isfile(path) and os.path.getsize(path)>_MAX_BYTES:
            with open(path,"rb") as src:
                src.seek(max(0,os.path.getsize(path)-(_MAX_BYTES//2)));tail=src.read()
            cut=tail.find(b"\n")
            if cut>=0:tail=tail[cut+1:]
            with open(path,"wb") as dst:dst.write(tail)
        with open(path,"ab") as handle:handle.write(payload)
        try:os.chmod(path,0o600)
        except OSError:pass
    except Exception:pass




def _endurance_sample_row(label="periodic"):
    """Build one privacy-safe periodic soak sample for the runtime log."""
    report=ENDURANCE.observe(label)
    current=report.get("current") or {}
    delta=report.get("delta") or {}
    peak=report.get("peak") or {}
    return _safe_row("soak_resource_sample",{
        "samples":int(report.get("samples") or 0),
        "uptime_s":int(report.get("uptime_s") or 0),
        "healthy":bool(report.get("healthy",True)),
        "rss_kb":int(current.get("rss_kb") or 0),
        "fds":int(current.get("fds") or 0),
        "threads":int(current.get("threads") or 0),
        "rss_delta_kb":int(delta.get("rss_kb") or 0),
        "fd_delta":int(delta.get("fds") or 0),
        "thread_delta":int(delta.get("threads") or 0),
        "rss_peak_kb":int(peak.get("rss_kb") or 0),
        "warnings":",".join(report.get("warnings") or ())[:80],
    })

def _maybe_write_endurance_sample(now=None, force=False):
    """Write at most one soak sample per interval; called only by log worker."""
    global _LAST_ENDURANCE_SAMPLE
    stamp=time.monotonic() if now is None else float(now)
    if not force and _LAST_ENDURANCE_SAMPLE and stamp-_LAST_ENDURANCE_SAMPLE < _ENDURANCE_SAMPLE_INTERVAL_S:
        return False
    _LAST_ENDURANCE_SAMPLE=stamp
    _write_row(_endurance_sample_row("periodic"))
    return True

def _worker():
    while not _STOP.is_set() or not _QUEUE.empty():
        try:
            _maybe_write_endurance_sample()
        except Exception:
            pass
        try:row=_QUEUE.get(timeout=0.25)
        except queue.Empty:continue
        if row is None:
            if _QUEUE.empty():_IDLE.set()
            continue
        try:_write_row(row)
        finally:
            if _QUEUE.empty():_IDLE.set()


def _ensure_worker():
    global _THREAD
    with _THREAD_LOCK:
        if _FINALIZED:return False
        if _THREAD is not None and _THREAD.is_alive():return True
        _STOP.clear();_THREAD=threading.Thread(target=_worker,name="ultrastalker-runtime-log",daemon=True);_THREAD.start();return True


def breadcrumb(event, **fields):
    """Queue one tiny breadcrumb; never perform HDD/proc I/O on the caller."""
    try:
        if not _ensure_worker():return
        row=_safe_row(event,fields);_IDLE.clear()
        try:
            _QUEUE.put_nowait(row);_IDLE.clear()
        except queue.Full:
            try:_QUEUE.get_nowait()
            except queue.Empty:pass
            try:
                _QUEUE.put_nowait(row);_IDLE.clear()
            except queue.Full:pass
    except Exception:pass


def diagnostic_breadcrumb(event, **fields):
    """Queue a UI diagnostic breadcrumb only when diagnostic_logging is enabled."""
    try:
        from ..storage import load_settings
        if not bool((load_settings() or {}).get("diagnostic_logging", False)):
            return False
        breadcrumb(event, **fields)
        return True
    except Exception:
        return False

def flush_runtime_log(timeout=1.0):
    return _IDLE.wait(max(0.0,float(timeout or 0)))


def shutdown_runtime_log(wait=False):
    global _FINALIZED
    _FINALIZED=True;_STOP.set()
    try:_QUEUE.put_nowait(None)
    except queue.Full:pass
    thread=_THREAD
    if wait and thread is not None:
        try:thread.join(timeout=1.0)
        except Exception:pass
