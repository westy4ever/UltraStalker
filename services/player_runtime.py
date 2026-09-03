# -*- coding: utf-8 -*-
"""Conservative /proc helpers for player-owned external processes."""
from __future__ import absolute_import
import os
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

_EXTERNAL_PLAYER_NAMES = ("exteplayer3", "gstplayer", "serviceapp", "ffmpeg")



def _proc_cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as handle:
            return handle.read(16384).replace(b"\x00", b" ").decode("utf-8", "ignore")
    except Exception:
        return ""

def _all_external_player_pids():
    result=[]
    try:entries=os.listdir("/proc")
    except Exception:return result
    for entry in entries:
        if not entry.isdigit():continue
        pid=int(entry)
        if pid==os.getpid():continue
        low=_proc_cmdline(pid).lower()
        if low and any(name in low for name in _EXTERNAL_PLAYER_NAMES):result.append(pid)
    return result

def _matching_external_player_pids(streamurl=""):
    """Return only external-player processes that plausibly own this stream.

    Enigma2 ServiceApp spawns a separate exteplayer3/gstplayer process. Some
    images leave that process alive after Navigation.stopService(), so decoder
    audio keeps running behind the plugin. We match conservatively by executable
    name and stream URL host/path token; native Enigma2 playback returns no PIDs.
    """
    result=[]
    url=str(streamurl or "").strip()
    host=""; path_token=""
    try:
        parsed=urlparse(url); host=(parsed.hostname or "").lower(); path_token=os.path.basename(parsed.path or "")
    except Exception:
        pass
    try: entries=os.listdir("/proc")
    except Exception: return result
    for entry in entries:
        if not entry.isdigit(): continue
        pid=int(entry)
        if pid==os.getpid(): continue
        cmd=_proc_cmdline(pid)
        low=cmd.lower()
        if not cmd or not any(name in low for name in _EXTERNAL_PLAYER_NAMES): continue
        if url and url in cmd:
            result.append(pid); continue
        if host and host in low and (not path_token or path_token.lower() in low):
            result.append(pid)
    return result

def _proc_ppid(pid):
    try:
        with open("/proc/%d/stat" % int(pid), "r") as handle:
            parts=handle.read().split()
        return int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        return 0

def _descendant_pids(root_pids):
    roots=set(int(x) for x in (root_pids or ()) if str(x).isdigit())
    if not roots:return set()
    try:pids=[int(x) for x in os.listdir("/proc") if x.isdigit()]
    except Exception:return set()
    children={}
    for pid in pids:
        parent=_proc_ppid(pid)
        if parent:children.setdefault(parent,set()).add(pid)
    found=set(); stack=list(roots)
    while stack:
        parent=stack.pop()
        for child in children.get(parent,()):
            if child not in found and child not in roots:
                found.add(child);stack.append(child)
    return found
