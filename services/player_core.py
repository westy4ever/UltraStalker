# -*- coding: utf-8 -*-
"""Shared runtime helpers for the native Ultra Stalker player.

Shared non-visual runtime helpers used by the native Ultra Stalker player.
The module preserves the player contract while keeping process/runtime helpers isolated.
"""
from __future__ import absolute_import, print_function

import ctypes
import os
import re
import signal

from .. import _
from ..core.executor import LazyThreadPoolExecutor
from ..storage import load_settings
from ..log import optional_failure
from .player_runtime import _matching_external_player_pids, _descendant_pids
from .player_visuals import _PROGRESS_FRAME_EXECUTOR

try:
    from urllib.parse import unquote, urlparse
except ImportError:
    from urllib import unquote
    from urlparse import urlparse

# All non-GUI player I/O is bounded by one shared executor.
_PLAYER_BG_EXECUTOR = LazyThreadPoolExecutor(max_workers=2, thread_name_prefix="ultrastalker-player-bg")

def _process_rss_kb():
    try:
        with open("/proc/self/status","r") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts=line.split()
                    return int(parts[1]) if len(parts)>1 else 0
    except Exception as exc:
        optional_failure("player.silent_guard",exc)
    return 0

def _malloc_trim():
    try:
        libc=ctypes.CDLL(None)
        trim=getattr(libc,"malloc_trim",None)
        if trim is not None:
            trim.argtypes=[ctypes.c_size_t];trim.restype=ctypes.c_int
            trim(0)
    except Exception as exc:
        optional_failure("player.silent_guard",exc)


def force_session_silence(session, streamurl="", force=False, stop_native=True, exclude_pids=None, owned_pids=None):
    """Best-effort silence restricted to this stream and explicitly owned PIDs."""
    nav=getattr(session,"nav",None)
    if stop_native and nav is not None:
        try: nav.stopService()
        except Exception as exc: optional_failure("player.force_nav_stop",exc)
        try:
            pnav=getattr(nav,"pnav",None)
            stop=getattr(pnav,"stopService",None)
            if callable(stop): stop()
        except Exception as exc: optional_failure("player.force_pnav_stop",exc)
    sig=signal.SIGKILL if force else signal.SIGTERM
    excluded=set(int(x) for x in (exclude_pids or ()) if str(x).isdigit())
    owned=set(int(x) for x in (owned_pids or ()) if str(x).isdigit())
    targets=set(_matching_external_player_pids(streamurl))|owned|_descendant_pids(owned)
    for pid in targets:
        if int(pid) in excluded:continue
        try:os.kill(pid,sig)
        except OSError:pass
        except Exception as exc:optional_failure("player.force_external_stop",exc)
    return True


def _optional_infobar(name):
    try:
        module = __import__("Screens.InfoBarGenerics", fromlist=[name])
        return getattr(module, name)
    except Exception as exc:
        optional_failure("InfoBar.%s" % name, exc)
        return type("Missing%s" % name, (object,), {"__init__": lambda self, *args, **kwargs: None})


InfoBarAudioSelection = _optional_infobar("InfoBarAudioSelection")
InfoBarMoviePlayerSummarySupport = _optional_infobar("InfoBarMoviePlayerSummarySupport")
InfoBarNotifications = _optional_infobar("InfoBarNotifications")
InfoBarSeek = _optional_infobar("InfoBarSeek")
InfoBarSubtitleSupport = _optional_infobar("InfoBarSubtitleSupport")
InfoBarSummarySupport = _optional_infobar("InfoBarSummarySupport")

try:
    from enigma import eAVSwitch
except Exception:
    try:
        from enigma import eAVControl as eAVSwitch
    except Exception:
        eAVSwitch = None

def shutdown_player_workers(wait=False):
    for executor,name in ((_PLAYER_BG_EXECUTOR,"player.bg_shutdown"),(_PROGRESS_FRAME_EXECUTOR,"player.progress_shutdown")):
        try:executor.shutdown(wait=bool(wait),cancel_futures=True)
        except Exception as exc:optional_failure(name,exc)

ENGINE_NAMES = {
    1: "Native",
    4097: "GStreamer",
    5001: "GstPlayer",
    5002: "ExtePlayer3",
    8193: "ServiceApp",
}


def _engine_label(value):
    try:
        value = int(value)
    except Exception:
        return str(value)
    return "%s %s" % (ENGINE_NAMES.get(value, "Engine"), value)


def _extension(url):
    try:
        path = unquote(urlparse(str(url)).path)
        ext = os.path.splitext(path)[-1].lower()
        if ext in (".ts", ".m3u8", ".mp4", ".mkv", ".avi", ".mpd", ".mov", ".webm"):
            return ext.lstrip(".").upper()
    except Exception as exc:
        optional_failure("player", exc)
    return "STREAM"


def _quality(item, name):
    item=item if isinstance(item,dict) else {}
    fields=("quality","video_quality","resolution","video_resolution","height","width","format","stream_quality",
            "name","title","cmd","command","url","stream_url","filename")
    text=" ".join(str(item.get(k) or "") for k in fields) + " " + str(name or "")
    up=text.upper()
    if re.search(r"\b(?:4K|UHD|2160P?|3840(?:X2160)?)\b",up): return "UHD 2160p"
    if re.search(r"\b(?:FULL[ ._-]?HD|FHD|1080P?|1920(?:X1080)?)\b",up): return "FHD 1080p"
    if re.search(r"\b(?:HD|720P?|1280(?:X720)?)\b",up): return "HD 720p"
    if re.search(r"\b(?:SD|576P?|480P?|720X576|720X480)\b",up): return "SD"
    return "AUTO"

def _available_engines(preferred, url):
    try:
        preferred = int(preferred)
    except Exception:
        preferred = 4097

    available = [1, 4097]
    if os.path.exists("/usr/bin/gstplayer"):
        available.append(5001)
    if os.path.exists("/usr/bin/exteplayer3"):
        available.append(5002)
    # Service type 8193 is not universal. Only advertise it when ServiceApp
    # (or a DreamOS-style service implementation) is actually present.
    serviceapp = any(os.path.exists(path) for path in (
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/Extensions/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp/serviceapp.so",
    ))
    if serviceapp:
        available.append(8193)

    low = str(url or "").lower()
    if ".m3u8" in low or "hls" in low:
        if preferred == 1:
            preferred = 4097
        order = [4097, 5001, 5002, 8193, 1]
    elif low.startswith(("rtsp://", "rtmp://")):
        order = [4097, 5002, 5001, 8193, 1]
    else:
        order = [4097, 5002, 5001, 8193, 1]

    result = []
    for value in [preferred] + order:
        if value in available and value not in result:
            result.append(value)
    return result or [4097]
