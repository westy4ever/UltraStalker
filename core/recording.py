# -*- coding: utf-8 -*-
"""Native Enigma2 recording-timer integration for Stalker Live TV.

Us31 hardening deliberately separates network URL resolution from Enigma2
RecordTimer mutation.  The periodic manager never performs portal I/O on the
GUI/eTimer thread; workers only resolve fresh URLs and the next eTimer tick
applies them on the Enigma2 thread.
"""
from __future__ import absolute_import

import datetime
import hashlib
import json
import os
import queue
import threading
import time

from ..client import StalkerClient
from ..storage import CONFIG_DIR, load_settings, load_json_file, _fsync_parent_dir
from ..log import get_logger

LOG = get_logger()

JOB_FILE = os.path.join(CONFIG_DIR, "recording_jobs.json")
_REFRESH_AHEAD = 180
_MANAGER = None
_LOCK = threading.RLock()


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _timestamp(value):
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"):
        try:
            return int(time.mktime(datetime.datetime.strptime(text[:19], fmt).timetuple()))
        except (TypeError, ValueError):
            pass
    return 0


def event_times(program):
    row = program if isinstance(program, dict) else {}
    start = _timestamp(row.get("start_timestamp") or row.get("start") or row.get("time") or row.get("utc"))
    stop = _timestamp(row.get("stop_timestamp") or row.get("stop") or row.get("end") or row.get("time_to"))
    duration = _int(row.get("duration") or row.get("length"), 0)
    if duration > 24 * 3600 and start:
        duration = max(0, duration - start)
    if not stop and start and duration:
        stop = start + max(60, duration)
    if stop and start and stop <= start:
        stop = start + max(60, duration or 3600)
    return start, stop


def _profile_client(profile):
    cfg = load_settings()
    profile = profile if isinstance(profile, dict) else {}
    return StalkerClient(
        profile.get("portal"), profile.get("mac"), timeout=cfg.get("timeout", 10),
        allow_http_fallback=profile.get("allow_http_fallback", False),
        tls_mode=profile.get("tls_mode", "auto"),
        device_profile=profile.get("device_profile", "auto"),
        allow_tls_fallback=profile.get("tls_fallback_accepted", False),
        http_fallback_accepted=profile.get("http_fallback_accepted", False),
    )


def _close_client(client):
    if client is None:
        return
    try:
        client.close()
    except Exception as exc:
        LOG.debug("Recording client close failed: %s", exc)


def _job_id(profile, channel, start):
    raw = "%s|%s|%s|%s" % (
        str((profile or {}).get("portal") or "").rstrip("/").lower(),
        str((profile or {}).get("mac") or "").upper(),
        str((channel or {}).get("id") or (channel or {}).get("ch_id") or (channel or {}).get("cmd") or ""),
        int(start or 0),
    )
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def _load_jobs():
    return load_json_file(JOB_FILE, list, [])


def _save_jobs(rows):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    temp = JOB_FILE + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush(); os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, JOB_FILE)
        _fsync_parent_dir(JOB_FILE)
    finally:
        try:
            if os.path.exists(temp):
                os.unlink(temp)
        except OSError:
            pass


def prepare_portal_recording(profile, channel, program, service_type=4097, padding_before=0, padding_after=0):
    """Resolve a fresh URL and return a serializable timer payload.

    This function performs network I/O and must be run on a worker thread by UI
    callers.  ``install_recording_timer`` remains the UI-thread operation.
    """
    profile = dict(profile or {})
    channel = dict(channel or {})
    program = dict(program or {})
    start, stop = event_times(program)
    if not start:
        raise ValueError("EPG programme has no usable start time")
    if not stop:
        stop = start + 3600
    now = int(time.time())
    if stop <= now:
        raise ValueError("This programme has already ended")
    try:
        padding_before = max(0, min(30, int(padding_before or 0))) * 60
        padding_after = max(0, min(60, int(padding_after or 0))) * 60
    except (TypeError, ValueError):
        padding_before = padding_after = 0
    begin = max(now + 2, start - padding_before)
    end = max(begin + 60, stop + padding_after)
    title = str(program.get("name") or program.get("title") or program.get("descr") or channel.get("name") or "Stalker recording")[:180]
    client = _profile_client(profile)
    try:
        url = client.create_link(channel, "itv")
    finally:
        _close_client(client)
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Portal returned an empty recording URL")
    job_id = _job_id(profile, channel, start)
    return {
        "id": job_id, "profile": profile, "channel": channel, "program": program,
        "event_start": int(start), "event_end": int(stop), "begin": int(begin), "end": int(end),
        "title": title, "service_type": int(service_type or 4097), "url": url.strip(),
        "created_at": int(time.time()), "last_refresh": int(time.time()),
    }


def _service_reference(url, title, service_type):
    from enigma import eServiceReference
    ref = eServiceReference(int(service_type or 4097), 0, str(url))
    try:
        ref.setName(str(title or "Stalker recording"))
    except Exception:
        pass
    try:
        from ServiceReference import ServiceReference
        return ServiceReference(ref)
    except Exception:
        return ref


def install_recording_timer(session, prepared):
    data = dict(prepared or {})
    if not session or not getattr(session, "nav", None) or not getattr(session.nav, "RecordTimer", None):
        raise RuntimeError("Enigma2 RecordTimer is not available")
    from RecordTimer import RecordTimerEntry, AFTEREVENT

    marker = "[UltraStalker:%s]" % data.get("id")
    description = "%s Dynamic portal URL refresh" % marker
    service_ref = _service_reference(data.get("url"), data.get("title"), data.get("service_type", 4097))
    args = (service_ref, int(data.get("begin")), int(data.get("end")), str(data.get("title") or "Stalker recording"), description, 0)
    try:
        timer = RecordTimerEntry(*args, disabled=False, justplay=False, afterEvent=AFTEREVENT.AUTO, dirname=None, tags=["UltraStalker"])
    except TypeError:
        timer = RecordTimerEntry(*args)
        try: timer.justplay = False
        except Exception: pass
    result = session.nav.RecordTimer.record(timer)
    if result:
        raise RuntimeError("Recording timer conflicts with an existing timer")
    data.pop("url", None)
    data["marker"] = marker
    with _LOCK:
        jobs = [row for row in _load_jobs() if isinstance(row, dict) and row.get("id") != data.get("id")]
        jobs.append(data)
        _save_jobs(jobs[-250:])
    return timer


def _timer_description(timer):
    try:
        return str(getattr(timer, "description", "") or "")
    except Exception:
        return ""


def _find_timer(record_timer, marker):
    for attr in ("timer_list", "processed_timers"):
        for timer in getattr(record_timer, attr, []) or []:
            if marker and marker in _timer_description(timer):
                return timer
    return None


def _resolve_job_url(job, cancel_event=None, client_sink=None, client_release=None):
    client = _profile_client(job.get("profile"))
    if client_sink is not None:
        client_sink(client)
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("recording refresh cancelled")
        url = client.create_link(job.get("channel") or {}, "itv", cancel_event=cancel_event)
        if not url:
            raise RuntimeError("Portal returned an empty refreshed recording URL")
        return str(url).strip()
    finally:
        _close_client(client)
        if client_release is not None:
            try: client_release(client)
            except Exception as exc: LOG.debug("Recording client untrack failed: %s", exc)


def _persist_timer_reference(session, job, timer, url):
    """Apply a fresh URL on Enigma2's thread and *prove* it was persisted."""
    record_timer = session.nav.RecordTimer
    old_ref = getattr(timer, "service_ref", None)
    ref = _service_reference(url, job.get("title"), job.get("service_type", 4097))
    timer.service_ref = ref
    first_error = None
    persisted = False
    try:
        changed = getattr(record_timer, "timeChanged", None)
        if callable(changed):
            changed(timer)
            persisted = True
    except Exception as exc:
        first_error = exc
    if not persisted:
        try:
            saver = getattr(record_timer, "saveTimer", None)
            if not callable(saver):
                raise RuntimeError("RecordTimer exposes neither working timeChanged nor saveTimer")
            saver()
            persisted = True
        except Exception as save_exc:
            timer.service_ref = old_ref
            message = "Recording timer refresh could not be persisted: %s / %s" % (first_error or "timeChanged unavailable", save_exc)
            LOG.warning(message)
            raise RuntimeError(message)
    job = dict(job)
    job["last_refresh"] = int(time.time())
    job.pop("last_error", None); job.pop("last_attempt", None)
    return job


def _refresh_job(session, job, timer):
    """Synchronous compatibility helper used by explicit/manual refreshes/tests."""
    url = _resolve_job_url(job)
    return _persist_timer_reference(session, job, timer, url)


def _prune_and_due(session, now=None):
    """UI-thread/local-only snapshot. No portal I/O occurs here."""
    now = int(now or time.time())
    record_timer = session.nav.RecordTimer
    with _LOCK:
        jobs = list(_load_jobs())
    kept = []
    due = []
    changed = False
    for raw in jobs:
        if not isinstance(raw, dict):
            changed = True; continue
        job = dict(raw)
        if int(job.get("end") or 0) < now - 3600:
            changed = True; continue
        marker = job.get("marker") or ("[UltraStalker:%s]" % job.get("id"))
        timer = _find_timer(record_timer, marker)
        if timer is None:
            if int(job.get("begin") or 0) > now:
                changed = True; continue
            kept.append(job); continue
        begin = int(job.get("begin") or 0)
        last_refresh = int(job.get("last_refresh") or 0)
        last_attempt = int(job.get("last_attempt") or 0)
        if 0 <= begin - now <= _REFRESH_AHEAD and now - max(last_refresh, last_attempt) >= 30:
            due.append((dict(job), marker))
        kept.append(job)
    return kept, due, changed


def _replace_job(rows, replacement, append_missing=True):
    rid = str((replacement or {}).get("id") or "")
    out = []
    found = False
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == rid:
            out.append(dict(replacement)); found = True
        else:
            out.append(row)
    if not found and replacement and append_missing:
        out.append(dict(replacement))
    return out


def _job_snapshot_matches(current, original):
    if not isinstance(current, dict) or not isinstance(original, dict):
        return False
    if str(current.get("id") or "") != str(original.get("id") or ""):
        return False
    if not _same_profile(current.get("profile") or {}, original.get("profile") or {}):
        return False
    for key in ("begin", "end", "created_at", "marker"):
        if str(current.get(key) or "") != str(original.get(key) or ""):
            return False
    cur_ch = current.get("channel") or {}; old_ch = original.get("channel") or {}
    for key in ("id", "ch_id", "cmd", "command", "url"):
        if str(cur_ch.get(key) or "") != str(old_ch.get(key) or ""):
            return False
    return True


def _replace_existing_job(rows, original, replacement):
    rid = str((replacement or {}).get("id") or "")
    out=[]; applied=False
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == rid:
            if _job_snapshot_matches(row, original):
                out.append(dict(replacement)); applied=True
            else:
                out.append(row)
        else:
            out.append(row)
    return out, applied


def refresh_recording_jobs(session):
    """Explicit synchronous refresh API.

    Kept for callers/tests, but the periodic ``_RecordingManager`` below does
    not use it, so timer callbacks never block on portal network I/O.
    """
    if not session or not getattr(session, "nav", None) or not getattr(session.nav, "RecordTimer", None):
        return {"refreshed": 0, "remaining": 0}
    now = int(time.time())
    kept, due, changed = _prune_and_due(session, now)
    refreshed = 0
    by_id = {str(row.get("id") or ""): dict(row) for row in kept if isinstance(row, dict)}
    for job, marker in due:
        timer = _find_timer(session.nav.RecordTimer, marker)
        if timer is None:
            continue
        try:
            updated = _refresh_job(session, job, timer)
            by_id[str(job.get("id") or "")] = updated
            refreshed += 1; changed = True
        except Exception as exc:
            failed = dict(job); failed["last_error"] = str(exc)[:240]; failed["last_attempt"] = now
            by_id[str(job.get("id") or "")] = failed; changed = True
    final = []
    for row in kept:
        if isinstance(row, dict): final.append(by_id.get(str(row.get("id") or ""), row))
        else: final.append(row)
    if changed:
        with _LOCK: _save_jobs(final)
    return {"refreshed": refreshed, "remaining": len(final)}


def _same_profile(left, right):
    return (
        str((left or {}).get("portal") or "").rstrip("/").lower() == str((right or {}).get("portal") or "").rstrip("/").lower()
        and str((left or {}).get("mac") or "").upper() == str((right or {}).get("mac") or "").upper()
    )


def purge_profile_jobs(profile, session=None, remove_timers=False):
    removed = 0; timer_removed = 0
    with _LOCK:
        jobs = _load_jobs(); kept = []
        record_timer = getattr(getattr(session, "nav", None), "RecordTimer", None) if session else None
        for row in jobs:
            if not isinstance(row, dict) or not _same_profile(row.get("profile") or {}, profile):
                kept.append(row); continue
            removed += 1
            if remove_timers and record_timer is not None:
                marker = row.get("marker") or ("[UltraStalker:%s]" % row.get("id"))
                timer = _find_timer(record_timer, marker)
                if timer is not None:
                    try:
                        remover = getattr(record_timer, "removeEntry", None) or getattr(record_timer, "remove", None)
                        if remover:
                            remover(timer); timer_removed += 1
                    except Exception as exc:
                        LOG.warning("Could not remove Stalker recording timer: %s", exc)
        if removed: _save_jobs(kept)
    return {"jobs_removed": removed, "timers_removed": timer_removed}


def reset_recording_integrations(session=None, remove_timers=True):
    """Remove every plugin-owned recording job and, when possible, its timer.

    Used after state restore/uninstall-style reconciliation so stale jobs from the
    pre-restore portal set can never reappear or tune old credentials.
    """
    record_timer = getattr(getattr(session, "nav", None), "RecordTimer", None) if session else None
    with _LOCK:
        jobs = [dict(row) for row in _load_jobs() if isinstance(row, dict)]
        timers_removed = 0
        if remove_timers and record_timer is not None:
            seen=set()
            for attr in ("timer_list", "processed_timers"):
                for timer in list(getattr(record_timer, attr, []) or []):
                    marker = _timer_description(timer)
                    tags = getattr(timer, "tags", []) or []
                    if "[UltraStalker:" not in marker and "UltraStalker" not in tags:
                        continue
                    if id(timer) in seen: continue
                    seen.add(id(timer))
                    try:
                        remover = getattr(record_timer, "removeEntry", None) or getattr(record_timer, "remove", None)
                        if remover:
                            remover(timer); timers_removed += 1
                    except Exception as exc:
                        LOG.warning("Could not remove stale Stalker recording timer: %s", exc)
        try:
            os.unlink(JOB_FILE)
            _fsync_parent_dir(JOB_FILE)
        except OSError:
            if jobs:
                _save_jobs([])
        return {"jobs_removed": len(jobs), "timers_removed": timers_removed}


class _RecordingManager(object):
    """Async resolver + UI-thread apply pump driven by one Enigma2 eTimer."""
    def __init__(self, session):
        self.session = session
        self.timer = None; self.conn = None
        self._results = queue.Queue()
        self._worker = None
        self._cancel = threading.Event()
        self._clients_lock = threading.RLock(); self._clients = set()
        self._stopped = False
        try:
            from enigma import eTimer
            self.timer = eTimer()
            try: self.conn = self.timer.timeout.connect(self.tick)
            except Exception: self.timer.callback.append(self.tick)
            self.timer.start(1000, False)
        except Exception as exc:
            LOG.warning("Recording manager timer unavailable: %s", exc)
            self.timer = None

    def _track_client(self, client):
        with self._clients_lock:
            self._clients.add(client)

    def _untrack_client(self, client):
        with self._clients_lock:
            self._clients.discard(client)

    def _cancel_clients(self):
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try: client.cancel_pending_requests()
            except Exception as exc: LOG.debug("Recording client cancellation failed: %s", exc)

    def _worker_run(self, due):
        try:
            for job, marker in due:
                if self._cancel.is_set(): break
                try:
                    url = _resolve_job_url(job, self._cancel, self._track_client, self._untrack_client)
                    self._results.put((True, job, marker, url))
                except Exception as exc:
                    self._results.put((False, job, marker, str(exc)))
        finally:
            self._results.put((None, None, None, None))

    def _start_worker(self, due):
        if not due or self._stopped: return
        if self._worker is not None and self._worker.is_alive(): return
        self._cancel.clear()
        self._worker = threading.Thread(target=self._worker_run, args=(list(due),), name="stalker-recording-refresh", daemon=True)
        self._worker.start()

    def _drain_results(self):
        if not self.session or not getattr(self.session, "nav", None) or not getattr(self.session.nav, "RecordTimer", None):
            return
        updates = []
        while True:
            try: success, job, marker, value = self._results.get_nowait()
            except queue.Empty: break
            if success is None: continue
            current = dict(job or {})
            timer = _find_timer(self.session.nav.RecordTimer, marker)
            if timer is None:
                current["last_error"] = "Enigma2 timer disappeared before URL refresh could be applied"
                current["last_attempt"] = int(time.time())
            elif success:
                try:
                    current = _persist_timer_reference(self.session, current, timer, value)
                except Exception as exc:
                    current["last_error"] = str(exc)[:240]; current["last_attempt"] = int(time.time())
                    LOG.warning("Recording URL apply failed job=%s: %s", current.get("id"), exc)
            else:
                current["last_error"] = str(value)[:240]; current["last_attempt"] = int(time.time())
                LOG.warning("Recording URL resolution failed job=%s: %s", current.get("id"), value)
            updates.append((dict(job or {}), current))
        if updates:
            with _LOCK:
                rows = _load_jobs(); changed = False
                for original, update in updates:
                    rows, applied = _replace_existing_job(rows, original, update)
                    if applied: changed = True
                    else: LOG.info("Dropped stale recording refresh result job=%s", update.get("id"))
                if changed: _save_jobs(rows[-250:])

    def tick(self):
        if self._stopped: return
        try:
            self._drain_results()
            kept, due, changed = _prune_and_due(self.session)
            if changed:
                with _LOCK: _save_jobs(kept)
            self._start_worker(due)
        except Exception as exc:
            LOG.exception("Recording manager tick failed: %s", exc)

    def stop(self, wait=False, timeout=8.0):
        self._stopped = True; self._cancel.set(); self._cancel_clients()
        if self.timer is not None:
            try: self.timer.stop()
            except Exception as exc: LOG.debug("Recording timer stop failed: %s", exc)
        if self.conn is not None:
            try: self.conn.disconnect()
            except Exception as exc: LOG.debug("Recording timer disconnect failed: %s", exc)
        if self.timer is not None:
            try:
                if self.tick in self.timer.callback:self.timer.callback.remove(self.tick)
            except Exception as exc: LOG.debug("Recording timer callback cleanup failed: %s", exc)
        worker = self._worker
        if wait and worker is not None and worker.is_alive():
            worker.join(max(0.0, float(timeout or 0.0)))
        return not (worker is not None and worker.is_alive())


def start_recording_manager(session, initial_tick=True):
    global _MANAGER
    if _MANAGER is not None:
        _MANAGER.session = session
        return _MANAGER
    _MANAGER = _RecordingManager(session)
    # SESSIONSTART may request one immediate local pass. UI launch paths can
    # skip it so no recording-job file scan delays the first Splash frame.
    if initial_tick:
        try: _MANAGER.tick()
        except Exception as exc: LOG.warning("Initial recording manager pass failed: %s", exc)
    return _MANAGER


def stop_recording_manager(wait=False):
    """Stop manager, cancel in-flight network work, and return its session.

    A synchronous caller (backup/restore shutdown path) gets a hard failure if
    the worker is still alive after the timeout; the global manager reference
    is retained in that case so callers can retry instead of losing ownership.
    """
    global _MANAGER
    manager = _MANAGER
    if manager is None:
        return None
    session = manager.session
    try:
        stopped = manager.stop(wait=wait)
    except Exception:
        if wait:
            raise
        LOG.exception("Recording manager stop failed")
        return session
    if wait and not stopped:
        raise RuntimeError("recording manager worker did not stop before timeout")
    if _MANAGER is manager:
        _MANAGER = None
    return session
