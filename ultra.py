# -*- coding: utf-8 -*-
"""Ultra Premium UX helpers kept independent from Enigma2 UI classes."""
from __future__ import absolute_import
import hashlib, json, os, re, tempfile, time, urllib.parse, unicodedata, threading

CONFIG_DIR = "/etc/enigma2/ultrastalker"
ENGINE_FILE = os.path.join(CONFIG_DIR, "smart_engines.json")
TITLE_ENGINE_FILE = os.path.join(CONFIG_DIR, "title_engines.json")
UI_STATE_FILE = os.path.join(CONFIG_DIR, "ui_state.json")
from .persistent_cache import (
    QUALITY as PERSISTENT_QUALITY_DIR,
    hdd_read_ready,
    persistent_write_gate,
)
from .identity import content_digest
QUALITY_FILE = os.path.join(PERSISTENT_QUALITY_DIR, "quality_cache.json")
LEGACY_QUALITY_FILE = os.path.join(CONFIG_DIR, "quality_cache.json")
_STATE_LOCK = threading.RLock()

TECH_TOKENS = {
    "4k", "uhd", "fhd", "hd", "sd", "hevc", "h265", "h264", "avc", "mpeg2", "mpeg4",
    "dolby", "audio", "aac", "ac3", "eac3", "ar", "en", "fr", "tr", "do", "vip", "raw",
    "1080p", "720p", "2160p", "hdr", "hdr10", "arabic", "english"
}


def _read(path, default):
    # QUALITY_FILE lives on /media/hdd.  Treat it as nonexistent unless the
    # mount is freshly verified; this makes any shadow directory on rootfs
    # invisible to the quality engine.  Config files under /etc keep their
    # normal bounded best-effort behaviour.
    if os.path.abspath(str(path or "")) == os.path.abspath(QUALITY_FILE):
        try:
            if not hdd_read_ready(force=True):
                return default
        except Exception:
            return default
    try:
        with open(path, "r", encoding="utf-8") as h:
            data = json.load(h)
        return data
    except Exception:
        return default


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except OSError:
        pass

def _write(path, data):
    directory = os.path.dirname(path)
    tmp = None
    is_quality = os.path.abspath(str(path or "")) == os.path.abspath(QUALITY_FILE)
    try:
        # HDD-backed quality state must pass a fresh mutation gate before any
        # mkdir/temp creation and again immediately before the atomic commit.
        if is_quality and not persistent_write_gate(path):
            return False
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if is_quality and not persistent_write_gate(path):
            return False
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path)+".", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            json.dump(data, h, ensure_ascii=False, indent=2, sort_keys=True)
            h.flush(); os.fsync(h.fileno())
        try: os.chmod(tmp, 0o600)
        except OSError: pass
        if is_quality and not persistent_write_gate(tmp, path):
            return False
        os.replace(tmp, path); tmp = None
        _fsync_dir(directory)
        return True
    except Exception:
        return False
    finally:
        if tmp:
            try:
                if (not is_quality) or persistent_write_gate(tmp):
                    os.unlink(tmp)
            except OSError:
                pass


def one_line(value, max_chars=180):
    raw = unicodedata.normalize("NFKC", str(value or ""))
    chars=[]
    for ch in raw:
        cat=unicodedata.category(ch)
        if ch.isspace(): chars.append(" ")
        elif cat in ("Cc","Cf","Cs","Co","Cn"): continue
        else: chars.append(ch)
    text=re.sub(r"\s+", " ", "".join(chars)).strip(" -|•")
    if max_chars and len(text)>max_chars:
        text=text[:max_chars-1].rstrip()+"…"
    return text


def premium_title(value, enabled=True):
    """Return the UI title; Clean Titles OFF always preserves the raw label."""
    text=one_line(value)
    if not text:
        return text
    if not enabled:
        return text
    try:
        from .title_clean import display_title
        cleaned=display_title(text, enabled=True)
        return one_line(cleaned) or text
    except Exception:
        return text

def quality_badges(value):
    text=one_line(value).upper().replace("×","X")
    out=[]
    # Portal labels are wildly inconsistent: 1080P50, 1080I, FHD50,
    # 1920x1080, HEVC-1080 and friends should all map to the same tier.
    tests=((r"(?<!\d)(?:4K|UHD|2160(?:P|I)?(?:24|25|30|50|60)?|3840\s*X\s*2160)(?!\d)","4K"),
           (r"(?<!\d)(?:FULL[ ._-]?HD|FHD(?:24|25|30|50|60)?|1080(?:P|I)?(?:24|25|30|50|60)?|1920\s*X\s*1080)(?!\d)","FHD"),
           (r"(?<!\d)(?:HD|720(?:P|I)?(?:24|25|30|50|60)?|1280\s*X\s*720)(?!\d)","HD"),
           (r"(?<!\d)(?:SD|576(?:P|I)?(?:25|50)?|480(?:P|I)?(?:24|30|60)?|720\s*X\s*(?:576|480))(?!\d)","SD"),
           (r"\b(?:H[ ._-]?265|HEVC|X265)\b","H.265"),(r"\b(?:H[ ._-]?264|AVC|X264)\b","H.264"),
           (r"\b(?:BLU[ ._-]?RAY|BDRIP|BRRIP)\b","BLURAY"),(r"\bHDRIP\b","HDRIP"),
           (r"\b(?:MULTI[ ._-]?AUDIO|DUAL[ ._-]?AUDIO)\b","MULTI"),
           (r"\bDOLBY\b","DOLBY"),(r"\bHDR10?\b","HDR"))
    for pattern,label in tests:
        if re.search(pattern,text) and label not in out: out.append(label)
    return " • ".join(out[:3])


def portal_key(profile):
    portal=str((profile or {}).get("portal") or "").rstrip("/").lower()
    mac=str((profile or {}).get("mac") or "").upper()
    return hashlib.sha1((portal+"|"+mac).encode("utf-8","ignore")).hexdigest()


def load_ui_state(profile):
    all_state=_read(UI_STATE_FILE,{})
    value=all_state.get(portal_key(profile),{}) if isinstance(all_state,dict) else {}
    return value if isinstance(value,dict) else {}


def save_ui_state(profile, **updates):
    with _STATE_LOCK:
        all_state=_read(UI_STATE_FILE,{})
        if not isinstance(all_state,dict): all_state={}
        key=portal_key(profile); current=all_state.get(key,{})
        if not isinstance(current,dict): current={}
        current.update(updates); current["updated_at"]=int(time.time())
        all_state[key]=current
        return _write(UI_STATE_FILE,all_state)


def _stream_family(url):
    parsed=urllib.parse.urlsplit(str(url or ""))
    path=(parsed.path or "").lower()
    ext=os.path.splitext(path)[-1].lstrip(".")
    if ext in ("m3u8","mpd","ts","mp4","mkv","avi","mov","webm"): return ext
    return parsed.scheme.lower() or "stream"


def engine_key(profile, media_type, url):
    return "%s:%s:%s" % (portal_key(profile), str(media_type or "itv").lower(), _stream_family(url))



def _title_identity(item):
    item=item if isinstance(item,dict) else {}
    for key in ("id","movie_id","series_id","episode_id","cmd","name","title"):
        value=item.get(key)
        if value not in (None,""):
            return str(value)[:240]
    return "unknown"



def _quality_identity(profile, media_type, item):
    return content_digest(profile, media_type, item, algorithm="sha1")

def _quality_identity_us110(profile, media_type, item):
    if str(media_type or "").lower() != "episode":return ""
    return content_digest(profile, media_type, item, algorithm="sha1", legacy_episode="us110")

def _quality_identity_legacy(profile, media_type, item):
    item=item if isinstance(item,dict) else {}
    raw="%s|%s|%s"%(portal_key(profile or {}),str(media_type or "").lower(),_title_identity(item))
    return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()

def _quality_key_candidates(profile, media_type, item):
    """Return current + historical quality keys in lookup priority order."""
    keys=[_quality_identity(profile,media_type,item)]
    us110=_quality_identity_us110(profile,media_type,item)
    if us110:keys.append(us110)
    keys.append(_quality_identity_legacy(profile,media_type,item))
    out=[]
    for key in keys:
        if key and key not in out:out.append(key)
    return out

def _quality_row_with_key(data, profile, media_type, item):
    for key in _quality_key_candidates(profile,media_type,item):
        row=data.get(key,{})
        if row:return row,key
    return {},""

def _quality_rows(data, profile, media_type, item):
    return _quality_row_with_key(data,profile,media_type,item)[0]

def normalize_quality(value):
    badges=quality_badges(value)
    first=(badges.split(" • ",1)[0] if badges else "")
    return first if first in ("4K","FHD","HD","SD") else ""


def _ensure_quality_cache_migrated():
    # Beta56+: fresh global-HDD architecture never imports legacy quality state.
    return True

def load_content_quality(profile, media_type, item):
    """Read learned quality and promote any discoverable historical key.

    Us110 episode quality keys contained the visible title.  If the title is
    unchanged on the first Us111 read, copy that row to the new structural
    key immediately.  Future title translations then keep resolving through
    the structural key.  An already-changed title cannot be reverse-hashed
    from the old SHA1-only cache, so that rare case correctly falls through to
    explicit metadata/parent-series/runtime detection instead of guessing.
    """
    _ensure_quality_cache_migrated()
    data=_read(QUALITY_FILE,{})
    if not isinstance(data,dict): return ""
    row,found_key=_quality_row_with_key(data,profile,media_type,item)
    value=(row or {}).get("quality","") if isinstance(row,dict) else ""
    quality=normalize_quality(value)
    current_key=_quality_identity(profile,media_type,item)
    if quality and found_key and found_key!=current_key:
        with _STATE_LOCK:
            latest=_read(QUALITY_FILE,{})
            if not isinstance(latest,dict):latest={}
            if not latest.get(current_key):
                promoted=dict(row)
                promoted["quality"]=quality
                promoted["migrated_at"]=int(time.time())
                latest[current_key]=promoted
                if len(latest)>1200:
                    ordered=sorted(latest.items(),key=lambda kv:int((kv[1] or {}).get("updated_at",0)),reverse=True)[:900]
                    latest=dict(ordered)
                _write(QUALITY_FILE,latest)
    return quality

def load_content_qualities(profile, media_type, items):
    """Load a whole page of quality values with one JSON read."""
    _ensure_quality_cache_migrated()
    data=_read(QUALITY_FILE,{})
    if not isinstance(data,dict): data={}
    out=[]
    for item in list(items or []):
        row=_quality_rows(data,profile,media_type,item)
        value=(row or {}).get("quality","") if isinstance(row,dict) else ""
        out.append(normalize_quality(value))
    return out

def remember_content_quality(profile, media_type, item, quality, width=0, height=0):
    _ensure_quality_cache_migrated()
    quality=normalize_quality(quality)
    if not quality:return False
    with _STATE_LOCK:
        data=_read(QUALITY_FILE,{})
        if not isinstance(data,dict):data={}
        key=_quality_identity(profile,media_type,item)
        data[key]={"quality":quality,"width":int(width or 0),"height":int(height or 0),"updated_at":int(time.time())}
        if len(data)>1200:
            ordered=sorted(data.items(),key=lambda kv:int((kv[1] or {}).get("updated_at",0)),reverse=True)[:900]
            data=dict(ordered)
        return _write(QUALITY_FILE,data)

def title_engine_key(profile, media_type, item):
    return content_digest(profile, media_type, item, algorithm="sha1")

def preferred_title_engine(profile, media_type, item, default=4097):
    """Per-title engine memory is disabled; Settings owns the engine."""
    try:value=int(default)
    except Exception:value=4097
    return value if value in (1,4097,5001,5002,8193) else 4097


def remember_title_engine(profile, media_type, item, engine):
    try:engine=int(engine)
    except Exception:return False
    if engine not in (1,4097,5001,5002,8193):return False
    with _STATE_LOCK:
        data=_read(TITLE_ENGINE_FILE,{})
        if not isinstance(data,dict):data={}
        data[title_engine_key(profile,media_type,item)]={"engine":engine,"updated_at":int(time.time())}
        # Bound per-title overrides so years of browsing cannot grow this file forever.
        if len(data)>500:
            ordered=sorted(data.items(), key=lambda kv:int((kv[1] or {}).get("updated_at",0)), reverse=True)[:400]
            data=dict(ordered)
        return _write(TITLE_ENGINE_FILE,data)

def forget_title_engine(profile, media_type, item):
    with _STATE_LOCK:
        data=_read(TITLE_ENGINE_FILE,{})
        if not isinstance(data,dict): return False
        key=title_engine_key(profile,media_type,item)
        if key not in data: return True
        data.pop(key,None)
        return _write(TITLE_ENGINE_FILE,data)

def title_engine_memory_count():
    data=_read(TITLE_ENGINE_FILE,{})
    return len(data) if isinstance(data,dict) else 0

def preferred_engine(profile, media_type, url, default=4097):
    """The Settings-selected engine is authoritative.

    Historical Smart Engine memory is intentionally ignored. Ultra Stalker no
    longer changes the service type behind the viewer's back.
    """
    try:value=int(default)
    except Exception:value=4097
    return value if value in (1,4097,5001,5002,8193) else 4097



def remember_engine(profile, media_type, url, engine):
    try: engine=int(engine)
    except Exception: return False
    if engine not in (1,4097,5001,5002,8193): return False
    with _STATE_LOCK:
        data=_read(ENGINE_FILE,{})
        if not isinstance(data,dict): data={}
        data[engine_key(profile,media_type,url)]={"engine":engine,"updated_at":int(time.time())}
        # Bound state forever. Humans create enough unbounded caches already.
        if len(data)>120:
            ordered=sorted(data.items(), key=lambda kv:int((kv[1] or {}).get("updated_at",0)), reverse=True)[:100]
            data=dict(ordered)
        return _write(ENGINE_FILE,data)


def engine_memory_count():
    data=_read(ENGINE_FILE,{})
    return len(data) if isinstance(data,dict) else 0


def epg_title(row):
    if not isinstance(row,dict): return ""
    return one_line(row.get("name") or row.get("title") or row.get("descr") or row.get("description"), 100)


def _timestamp(value):
    try:
        if isinstance(value,(int,float)): return int(value)
        text=str(value or "").strip()
        if text.isdigit(): return int(text)
        # Most MAG portals use YYYY-MM-DD HH:MM:SS.
        import datetime
        for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S","%H:%M"):
            try:
                parsed=datetime.datetime.strptime(text[:19],fmt)
                if fmt=="%H:%M":
                    now=datetime.datetime.now(); parsed=parsed.replace(year=now.year,month=now.month,day=now.day)
                return int(time.mktime(parsed.timetuple()))
            except Exception: pass
    except Exception: pass
    return 0


def epg_summary(rows, now=None):
    now=int(now or time.time()); events=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict): continue
        start=_timestamp(row.get("start_timestamp") or row.get("start") or row.get("time"))
        stop=_timestamp(row.get("stop_timestamp") or row.get("stop") or row.get("end") or row.get("time_to"))
        if stop and start and stop<start: stop=start+stop
        events.append((start,stop,row))
    events.sort(key=lambda x:x[0] or 0)
    current=None; nxt=None
    for idx,event in enumerate(events):
        start,stop,row=event
        if start and stop and start<=now<stop:
            current=event; nxt=events[idx+1] if idx+1<len(events) else None; break
    if current is None and events:
        future=[e for e in events if not e[0] or e[0]>=now]
        current=events[0] if not future else future[0]
        pos=events.index(current); nxt=events[pos+1] if pos+1<len(events) else None
    percent=0
    if current and current[0] and current[1] and current[1]>current[0]:
        percent=max(0,min(100,int((now-current[0])*100.0/(current[1]-current[0]))))
    return {
        "now": epg_title(current[2]) if current else "",
        "next": epg_title(nxt[2]) if nxt else "",
        "percent": percent,
    }
