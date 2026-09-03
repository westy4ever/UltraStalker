# -*- coding: utf-8 -*-
"""Persistent, HDD-first cache layout for Ultra Stalker.

Artwork/metadata work is intentionally kept outside the plugin directory so it
survives package upgrades and Enigma2 restarts.  Receiver configuration and
playback history remain under /etc/enigma2; only reproducible cache data lives
here.
"""
from __future__ import absolute_import
import json
import hashlib
import re
import os
import logging
import shutil
import tempfile
import time
import threading
from .securefs import secure_private_dir
from .identity import content_digest

CACHE_SCHEMA = 7
LOG = logging.getLogger("UltraStalker.PersistentCache")
PLUGIN_DIRNAME = "UltraStalker"
_CANONICAL_ALIAS_MEMORY = {}
_CANONICAL_ALIAS_ORDER = []
_CANONICAL_ALIAS_LIMIT = 384
_CANONICAL_ALIAS_LOCK = threading.RLock()

def _canonical_alias_mem_get(key):
    with _CANONICAL_ALIAS_LOCK:
        value=_CANONICAL_ALIAS_MEMORY.get(key)
        if not isinstance(value,dict):
            return None
        try:_CANONICAL_ALIAS_ORDER.remove(key)
        except ValueError:pass
        _CANONICAL_ALIAS_ORDER.append(key)
        return dict(value)

def _canonical_alias_mem_put(key,payload):
    if not key or not isinstance(payload,dict):
        return
    with _CANONICAL_ALIAS_LOCK:
        _CANONICAL_ALIAS_MEMORY[key]=dict(payload)
        try:_CANONICAL_ALIAS_ORDER.remove(key)
        except ValueError:pass
        _CANONICAL_ALIAS_ORDER.append(key)
        while len(_CANONICAL_ALIAS_ORDER)>_CANONICAL_ALIAS_LIMIT:
            old=_CANONICAL_ALIAS_ORDER.pop(0)
            _CANONICAL_ALIAS_MEMORY.pop(old,None)


ROOT_HINT_FILE = "/etc/enigma2/ultrastalker/persistent_root.txt"

def _fsync_parent(path):
    try:
        fd=os.open(os.path.dirname(os.path.abspath(path)) or ".",os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except OSError as exc:
        LOG.debug('parent fsync failed for %s: %s', path, exc)


def _read_root_hint():
    try:
        with open(ROOT_HINT_FILE, "r", encoding="utf-8") as h:
            value = h.read().strip()
        if value.startswith("/media/hdd/"):
            return value
    except (OSError, UnicodeError) as exc:
        LOG.debug('persistent root hint read failed: %s', exc)
    return ""

def _write_root_hint(root):
    try:
        if not str(root or "").startswith("/media/hdd/"):
            return
        directory=os.path.dirname(ROOT_HINT_FILE)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        tmp="%s.tmp.%d.%d"%(ROOT_HINT_FILE,os.getpid(),threading.get_ident())
        with open(tmp,"w",encoding="utf-8") as h:
            h.write(str(root).strip()+"\n"); h.flush(); os.fsync(h.fileno())
        try: os.chmod(tmp,0o600)
        except OSError: pass
        os.replace(tmp,ROOT_HINT_FILE); _fsync_parent(ROOT_HINT_FILE)
    except OSError as exc:
        LOG.warning('persistent root hint write failed: %s', exc)


def _writable_mount(path):
    try:
        return os.path.ismount(path) and os.access(path, os.W_OK)
    except Exception:
        return False


def _write_test(root):
    try:
        if os.path.abspath(root).startswith("/tmp/"):
            secure_private_dir(root)
        else:
            os.makedirs(root, mode=0o700, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write-", dir=root)
        os.close(fd)
        os.unlink(probe)
        return True
    except Exception:
        return False


def _select_root():
    """Return Ultra Stalker's one and only persistent cache root.

    us181 deliberately removes every artwork/cache fallback outside the HDD.
    If /media/hdd is not mounted yet we still keep the canonical path stable,
    but writers must fail closed until the real mount is available.  This means
    a cold boot can never create a second cache under /etc, /tmp or /media/usb
    and make previously downloaded artwork appear to have vanished.
    """
    return os.path.join("/media/hdd", PLUGIN_DIRNAME)


_HDD_STATE_LOCK = threading.RLock()
_HDD_MOUNT_STATE = {"value": False, "checked": 0.0}
_HDD_WRITE_STATE = {"value": False, "checked": 0.0}
_HDD_MOUNT_TTL = 5.0
_HDD_WRITE_TTL = 30.0

def _mounted_hdd_uncached():
    """Check that /media/hdd resolves to storage outside the receiver rootfs.

    This check is read-only. It deliberately performs no mkdir/open/write probe,
    so cached artwork can be read immediately without waking a sleeping disk just
    to prove writeability.
    """
    try:
        raw = "/media/hdd"
        real = os.path.realpath(raw)
        if _writable_mount(raw) or _writable_mount(real):
            return True
        if not os.path.isdir(real):
            return False
        try:
            return os.stat(real).st_dev != os.stat("/").st_dev
        except Exception:
            return False
    except Exception:
        return False

def hdd_read_ready(force=False):
    """Return True when /media/hdd is a genuine mounted filesystem.

    Results are cached briefly because this function sits on artwork hot paths.
    It never creates files or directories.
    """
    now = time.monotonic()
    with _HDD_STATE_LOCK:
        if not force and (now - float(_HDD_MOUNT_STATE["checked"] or 0.0)) < _HDD_MOUNT_TTL:
            return bool(_HDD_MOUNT_STATE["value"])
        value = bool(_mounted_hdd_uncached())
        _HDD_MOUNT_STATE.update(value=value, checked=now)
        if not value:
            _HDD_WRITE_STATE.update(value=False, checked=now)
        return value

def _write_mount_ready_uncached():
    """Cheap, uncached safety gate used immediately before persistent writes.

    It performs no mkdir/open probe.  The purpose is to invalidate a cached True
    as soon as /media/hdd disappears, is replaced by rootfs, or becomes read-only.
    The heavier write probe may still be cached after this gate succeeds.
    """
    try:
        if not _mounted_hdd_uncached():
            return False
        real = os.path.realpath("/media/hdd")
        if not os.access(real, os.W_OK):
            return False
        try:
            flags = int(getattr(os.statvfs(real), "f_flag", 0) or 0)
            readonly = int(getattr(os, "ST_RDONLY", 1) or 1)
            if flags & readonly:
                return False
        except (OSError, AttributeError, TypeError, ValueError):
            # os.access + genuine-mount validation are still a useful gate on
            # images whose Python build does not expose statvfs flags reliably.
            pass
        return True
    except Exception:
        return False


def hdd_ready(force=False):
    """Return True only when the persistent HDD cache is safe for writes.

    Every call first performs a cheap *uncached* mount/writability check.  This
    closes the stale-TTL window after HDD unmount without restoring the old
    expensive write-probe-on-every-hot-path behaviour.
    """
    now = time.monotonic()
    with _HDD_STATE_LOCK:
        if not _write_mount_ready_uncached():
            _HDD_MOUNT_STATE.update(value=False, checked=now)
            _HDD_WRITE_STATE.update(value=False, checked=now)
            return False
        _HDD_MOUNT_STATE.update(value=True, checked=now)
        if not force and (now - float(_HDD_WRITE_STATE["checked"] or 0.0)) < _HDD_WRITE_TTL:
            return bool(_HDD_WRITE_STATE["value"])
        value = bool(_write_test(ROOT))
        _HDD_WRITE_STATE.update(value=value, checked=now)
        return value

def ensure_persistent_dirs(*paths):
    """Create cache directories only after the real HDD mount is write-ready."""
    if not hdd_ready():
        return False
    try:
        for path in paths or (ROOT,):
            absolute = os.path.abspath(str(path or ""))
            if not (absolute == ROOT or absolute.startswith(ROOT + os.sep)):
                return False
            os.makedirs(absolute, mode=0o700, exist_ok=True)
        return True
    except Exception:
        return False


def persistent_write_gate(*targets):
    """Fresh fail-closed gate immediately before any persistent filesystem mutation.

    ``targets`` may be files or directories under ROOT.  The function always
    performs the uncached mount/writability validation via ``hdd_ready()`` and
    creates only the required parent directories.  Callers should invoke it
    both before a long operation and again immediately before atomic replace,
    unlink or other final mutation.
    """
    if not hdd_ready():
        return False
    try:
        dirs=[]
        for target in targets or (ROOT,):
            absolute=os.path.abspath(str(target or ""))
            if not (absolute == ROOT or absolute.startswith(ROOT + os.sep)):
                return False
            # Existing directories and ROOT itself are accepted directly; file
            # targets use their parent.  For a not-yet-created path, an extension
            # is a conservative signal that it is a file target.
            if absolute == ROOT or os.path.isdir(absolute):
                directory=absolute
            else:
                base=os.path.basename(absolute)
                directory=os.path.dirname(absolute) if os.path.splitext(base)[1] else absolute
            if directory and directory not in dirs: dirs.append(directory)
        for directory in dirs:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        # Revalidate after mkdir as well; mount state can change between calls.
        return bool(_write_mount_ready_uncached())
    except Exception:
        return False


ROOT = _select_root()
POSTERS = os.path.join(ROOT, "posters")
BACKDROPS = os.path.join(ROOT, "backdrops")
GENERATED = os.path.join(ROOT, "generated")
TMDB_META = os.path.join(ROOT, "metadata", "tmdb")
PORTAL_ART = os.path.join(ROOT, "artwork")
QUALITY = os.path.join(ROOT, "quality")
INDEX = os.path.join(ROOT, "index")

ALL_DIRS = (ROOT, POSTERS, BACKDROPS, GENERATED, TMDB_META, PORTAL_ART, QUALITY, INDEX)

_INIT_LOCK = threading.RLock()
_INIT_DONE = False
_INIT_MOUNT_ID = None

def _mount_identity_uncached():
    """Return a generation-sensitive identity for the current /media/hdd mount.

    Linux mountinfo exposes a mount ID that changes on remount, while the
    major:minor device pair catches disk swaps. The stat fallback keeps older
    receiver images usable if mountinfo is unavailable.
    """
    try:
        if not _mounted_hdd_uncached():
            return None
        target=os.path.realpath("/media/hdd")
        try:
            with open("/proc/self/mountinfo","r",encoding="utf-8",errors="replace") as h:
                for line in h:
                    parts=line.rstrip("\n").split(" ")
                    if len(parts) < 6:
                        continue
                    mount_point=parts[4].replace("\\040"," ")
                    if os.path.realpath(mount_point) == target:
                        return ("mountinfo",parts[0],parts[2],target)
        except OSError:
            pass
        st=os.stat(target)
        return ("stat",int(st.st_dev),int(st.st_ino),target)
    except Exception:
        return None

def initialize_persistent_cache_once():
    """Initialize the persistent cache once per actual HDD mount generation.

    A remount or disk swap invalidates the previous initialization marker so
    directories, migration state and the manifest are refreshed safely. Failed
    attempts remain retryable for late-mounted storage.
    """
    global _INIT_DONE,_INIT_MOUNT_ID
    with _INIT_LOCK:
        current=_mount_identity_uncached()
        if current is None:
            _INIT_DONE=False;_INIT_MOUNT_ID=None
            return False
        if _INIT_DONE and _INIT_MOUNT_ID == current:
            return True
        if _INIT_MOUNT_ID != current:
            _INIT_DONE=False
        if not hdd_ready() or not ensure_persistent_dirs(*ALL_DIRS):
            return False
        confirmed=_mount_identity_uncached()
        if confirmed is None or confirmed != current:
            _INIT_DONE=False;_INIT_MOUNT_ID=None
            return False
        try:
            migrate_legacy()
            if not write_manifest():
                return False
            final_id=_mount_identity_uncached()
            if final_id is None or final_id != confirmed:
                _INIT_DONE=False;_INIT_MOUNT_ID=None
                return False
            _INIT_MOUNT_ID=final_id
            _INIT_DONE=True
            return True
        except Exception as exc:
            LOG.debug("persistent cache initialization failed: %s", exc)
            _INIT_DONE=False
            return False


def persistent_root_ready():
    return hdd_ready()

def _copy_tree_files(src, dst):
    """Best-effort one-time migration; never delete the old cache automatically."""
    try:
        if not os.path.isdir(src):
            return 0
        count = 0
        for name in os.listdir(src):
            source = os.path.join(src, name)
            target = os.path.join(dst, name)
            if not os.path.isfile(source) or os.path.exists(target):
                continue
            try:
                if not persistent_write_gate(target):
                    break
                shutil.copy2(source, target)
                count += 1
            except Exception as exc:
                LOG.debug("legacy cache migration skipped %s -> %s: %s", source, target, exc)
        return count
    except Exception as exc:
        LOG.debug("legacy cache migration scan failed for %s: %s", src, exc)
        return 0


def migrate_legacy():
    if not hdd_ready() or not ensure_persistent_dirs(*ALL_DIRS):
        return 0
    marker = os.path.join(ROOT, ".migration-v1")
    if os.path.exists(marker):
        return
    migrated = 0
    # Us91-101 artwork caches.
    for old in (
        "/media/hdd/.cache/ultrastalker/images",
        "/media/usb/.cache/ultrastalker/images",
        "/etc/enigma2/ultrastalker/artwork-cache/images",
    ):
        migrated += _copy_tree_files(old, PORTAL_ART)
    for old in (
        "/media/hdd/.cache/ultrastalker/thumbs",
        "/media/usb/.cache/ultrastalker/thumbs",
        "/etc/enigma2/ultrastalker/artwork-cache/thumbs",
    ):
        migrated += _copy_tree_files(old, GENERATED)
    for old in (
        "/media/hdd/.cache/ultrastalker/tmdb",
        "/media/usb/.cache/ultrastalker/tmdb",
        "/etc/enigma2/ultrastalker/tmdb-images",
    ):
        # Legacy TMDB cache mixed posters/backdrops; keep them in portal-art so
        # existing hash lookups can still find/copy them if needed.
        migrated += _copy_tree_files(old, PORTAL_ART)
    migrated += _copy_tree_files("/etc/enigma2/ultrastalker/tmdb-cache", TMDB_META)
    try:
        if not persistent_write_gate(marker): return migrated
        with open(marker, "w", encoding="utf-8") as h:
            h.write("%d\n" % int(time.time()))
    except Exception as exc:
        LOG.debug("persistent cache migration marker write failed: %s", exc)
    return migrated


def write_manifest():
    if not hdd_ready() or not ensure_persistent_dirs(ROOT):
        return False
    path = os.path.join(ROOT, "cache_manifest.json")
    payload = {
        "schema": CACHE_SCHEMA,
        "root": ROOT,
        "persistent": ROOT.startswith("/media/hdd/"),
        "updated_at": int(time.time()),
        "layout": {
            "posters": POSTERS,
            "backdrops": BACKDROPS,
            "generated": GENERATED,
            "tmdb": TMDB_META,
            "portal_art": PORTAL_ART,
            "quality": QUALITY,
        },
    }
    temp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    try:
        if not persistent_write_gate(temp): return False
        with open(temp, "w", encoding="utf-8") as h:
            json.dump(payload, h, ensure_ascii=False, indent=2, sort_keys=True)
            h.flush(); os.fsync(h.fileno())
        try: os.chmod(temp, 0o600)
        except OSError as exc: LOG.debug("manifest chmod failed for %s: %s", temp, exc)
        if not persistent_write_gate(path): return False
        os.replace(temp, path); _fsync_parent(path)
        return True
    except Exception as exc:
        LOG.debug("persistent cache manifest write failed: %s", exc)
        try:
            if os.path.exists(temp) and persistent_write_gate(temp): os.unlink(temp)
        except OSError as cleanup_exc:
            LOG.debug("manifest temp cleanup failed for %s: %s", temp, cleanup_exc)
        return False



def _safe_text(value):
    try:
        return str(value or "").strip()
    except Exception:
        return ""

def _canonical_media_type(media_type):
    mt=str(media_type or "").strip().lower()
    if mt in ("movie","movies","vod"):return "movie"
    if mt in ("tv","show","shows","series"):return "tv"
    return mt or "content"

def canonical_external_identity(media_type, item=None, snapshot=None):
    """Portal-independent identity for globally deduplicated visual assets."""
    item=item if isinstance(item,dict) else {}
    snap=snapshot if isinstance(snapshot,dict) else {}
    mt=_canonical_media_type(snap.get("media_type") or media_type)
    tmdb=snap.get("tmdb_id") or item.get("tmdb_id") or item.get("tmdbid")
    if tmdb not in (None,""):
        try:tmdb=str(int(tmdb))
        except Exception:tmdb=str(tmdb).strip()
        if tmdb:return "%s:tmdb:%s"%(mt,tmdb)
    imdb=snap.get("imdb_id") or item.get("imdb_id") or item.get("imdb")
    imdb=str(imdb or "").strip().lower()
    if imdb and re.match(r"^tt\d{5,12}$",imdb):
        return "%s:imdb:%s"%(mt,imdb)
    verified=bool(snap.get("identity_verified")) and str(snap.get("identity_source") or "") not in ("","portal_payload")
    if verified:
        title=str(snap.get("original_title") or snap.get("original_name") or snap.get("title") or item.get("original_title") or item.get("original_name") or item.get("title") or item.get("name") or "").strip().casefold()
        year=str(snap.get("year") or item.get("year") or item.get("release_year") or item.get("first_air_date") or "")[:4]
        if title:
            raw=json.dumps([mt,title,year],ensure_ascii=False,separators=(",",":"))
            return "%s:verified:%s"%(mt,hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest())
    return ""

def canonical_external_digest(media_type, item=None, snapshot=None):
    identity=canonical_external_identity(media_type,item,snapshot)
    return hashlib.sha1(identity.encode("utf-8","ignore")).hexdigest() if identity else ""

def content_cache_key(profile, media_type, item):
    """Shared stable content key for HDD-first detail snapshots.

    Uses the same logical identity as resume/history and quality caching, so a
    regenerated stream command or translated display title cannot split one
    title into several cache entries.
    """
    return content_digest(profile, media_type, item, algorithm="sha1")

def detail_snapshot_path(profile, media_type, item):
    return os.path.join(INDEX, "detail_" + content_cache_key(profile, media_type, item) + ".json")

def _canonical_metadata_title(value):
    text=str(value or "").strip()
    if not text:return ""
    previous=None
    while text and text!=previous:
        previous=text
        text=re.sub(r"^\s*(?:TOP|NEW|MOVIE|MOVIES|FILM|FILMS|BLURAY|WEB[- .]?DL|4K|UHD|FHD|FULL\s*HD|HD|SD)\s*[-:|•]+\s*","",text,flags=re.I)
        text=re.sub(r"^\s*[\[\(\{][^\]\)\}]{1,24}[\]\)\}]\s*[-:|•]*\s*","",text)
    text=re.sub(r"\s*[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]\s*$","",text)
    text=re.sub(r"\s*(?:[-:|•]\s*)?(?:2160p|1080p|720p|4k|uhd|fhd|full\s*hd|hd|sd|hevc|x26[45]|h\.26[45])\s*$","",text,flags=re.I)
    text=re.sub(r"\s*[\[\(](?:4k|uhd|fhd|full\s*hd|hd|sd|2160p|1080p|720p|ar|arabic|en|eng|english)[\]\)]\s*$","",text,flags=re.I)
    # "Pure" is catalogue/provider branding on affected portals. Strip it only
    # when another meaningful title remains; a real movie literally named Pure
    # still keeps its identity.
    if re.search(r"\bpure\s*$",text,flags=re.I):
        without=re.sub(r"(?:[-:|•]\s*)?\bpure\s*$","",text,flags=re.I).strip(" -:|•._")
        if len(re.sub(r"\W+","",without,flags=re.UNICODE))>=2:
            text=without
    return re.sub(r"\s+"," ",text).strip(" -:|•._").casefold()

def _canonical_metadata_year(item=None, snapshot=None):
    item=item if isinstance(item,dict) else {}
    snap=snapshot if isinstance(snapshot,dict) else {}
    for value in (snap.get("year"),item.get("year"),item.get("release_year"),item.get("release_date"),item.get("releasedate"),item.get("first_air_date")):
        m=re.search(r"(19|20)\d{2}",str(value or ""))
        if m:return m.group(0)
    return ""

def _canonical_metadata_alias_keys(profile, media_type, item=None, snapshot=None):
    item=item if isinstance(item,dict) else {}
    snap=snapshot if isinstance(snapshot,dict) else {}
    mt=_canonical_media_type(snap.get("media_type") or media_type)
    portal=str((profile or {}).get("portal") or "").rstrip("/").casefold()
    mac=str((profile or {}).get("mac") or "").strip().upper()
    year=_canonical_metadata_year(item,snap)
    titles=[]
    for value in (item.get("name"),item.get("title"),item.get("original_name"),item.get("original_title"),
                  snap.get("title"),snap.get("name"),snap.get("original_title"),snap.get("original_name")):
        title=_canonical_metadata_title(value)
        if title and title not in titles:titles.append(title)
    keys=[]
    for title in titles:
        # Portal-local alias remains first for perfect backward compatibility.
        raw_local=json.dumps([portal,mac,mt,title,year],ensure_ascii=False,separators=(",",":"))
        local_key=hashlib.sha1(raw_local.encode("utf-8","ignore")).hexdigest()
        if local_key not in keys:keys.append(local_key)
        # Global alias is written/read only for snapshots that were externally
        # verified before save. It lets another portal reuse the same identity,
        # metadata and canonical artwork without another full enrichment pass.
        raw_global=json.dumps(["global",mt,title,year],ensure_ascii=False,separators=(",",":"))
        global_key=hashlib.sha1(raw_global.encode("utf-8","ignore")).hexdigest()
        if global_key not in keys:keys.append(global_key)
    return keys

def _canonical_metadata_alias_path(key):
    return os.path.join(INDEX,"detail_alias_"+str(key)+".json")

def _detail_tmdb_snapshot_path(media_type,tmdb_id):
    mt=_canonical_media_type(media_type)
    try:tid=str(int(tmdb_id))
    except Exception:return ""
    return os.path.join(INDEX,"detail_tmdb_%s_%s.json"%(mt,tid))

def load_detail_snapshot_by_tmdb(media_type,tmdb_id):
    path=_detail_tmdb_snapshot_path(media_type,tmdb_id)
    if not path or not hdd_read_ready():return {}
    try:
        with open(path,"r",encoding="utf-8") as h:data=json.load(h)
        if isinstance(data,dict) and int(data.get("cache_schema") or 0)>=CACHE_SCHEMA and _snapshot_metadata_complete(data):
            return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOG.debug('TMDB detail snapshot read failed for %s: %s', path, exc)
    return {}

def _detail_imdb_snapshot_path(imdb_id):
    imdb=str(imdb_id or "").strip().lower()
    if not re.match(r"^tt\d{5,12}$",imdb):return ""
    return os.path.join(INDEX,"detail_imdb_%s.json"%imdb)

def load_detail_snapshot_by_imdb(imdb_id):
    path=_detail_imdb_snapshot_path(imdb_id)
    if not path or not hdd_read_ready():return {}
    try:
        with open(path,"r",encoding="utf-8") as h:data=json.load(h)
        if isinstance(data,dict) and int(data.get("cache_schema") or 0)>=CACHE_SCHEMA and _snapshot_metadata_complete(data):
            return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOG.debug('IMDb detail snapshot read failed for %s: %s', path, exc)
    return {}


def load_shared_detail_snapshot(profile, media_type, item):
    """Cross-source metadata/artwork lookup for Stalker and M3U/Xtream.

    Priority is explicit TMDB/IMDb identity, then the existing verified global
    title/year alias. No portal URL or MAC is required for a global cache hit.
    """
    item=item if isinstance(item,dict) else {}
    mt=_canonical_media_type(media_type)
    tmdb_id=item.get("tmdb_id") or item.get("tmdbid")
    if tmdb_id not in (None,""):
        snap=load_detail_snapshot_by_tmdb(mt,tmdb_id)
        if snap:return snap
    imdb_id=item.get("imdb_id") or item.get("imdb")
    if imdb_id:
        snap=load_detail_snapshot_by_imdb(imdb_id)
        if snap:return snap

    # Existing canonical alias loader already tries local + global aliases.
    # Supplying an empty source profile deliberately makes the global alias the
    # reusable cross-provider identity while retaining title/year safeguards.
    try:
        snap=_load_canonical_metadata_alias({},mt,item)
        if isinstance(snap,dict) and snap.get("matched") and snap.get("identity_verified"):
            return snap
    except Exception as exc:
        LOG.debug('shared canonical metadata lookup failed: %s', exc)
    return load_detail_snapshot(profile or {},mt,item)

def _save_detail_imdb_snapshot(payload):
    if not isinstance(payload,dict) or not _snapshot_metadata_complete(payload):return
    imdb=str(payload.get("imdb_id") or "").strip().lower()
    if not (payload.get("matched") and payload.get("identity_verified") and imdb):return
    path=_detail_imdb_snapshot_path(imdb)
    if not path:return
    temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
    try:
        if not persistent_write_gate(temp):return
        with open(temp,"w",encoding="utf-8") as h:
            json.dump(payload,h,ensure_ascii=False,separators=(",",":"));h.flush();os.fsync(h.fileno())
        try: os.chmod(temp,0o600)
        except OSError as exc: LOG.debug("snapshot temp chmod failed for %s: %s", temp, exc)
        if not persistent_write_gate(path):return
        os.replace(temp,path);_fsync_parent(path)
    except (OSError, TypeError, ValueError) as exc:
        LOG.warning('IMDb detail snapshot write failed for %s: %s', path, exc)
        try:
            if os.path.exists(temp):os.unlink(temp)
        except OSError as cleanup_exc:LOG.debug('IMDb snapshot temp cleanup failed: %s', cleanup_exc)

def _save_detail_tmdb_snapshot(payload):
    if not isinstance(payload,dict) or not _snapshot_metadata_complete(payload):return
    if not (payload.get("matched") and payload.get("identity_verified") and payload.get("tmdb_id")):return
    path=_detail_tmdb_snapshot_path(payload.get("media_type"),payload.get("tmdb_id"))
    if not path:return
    temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
    try:
        if not persistent_write_gate(temp):return
        with open(temp,"w",encoding="utf-8") as h:
            json.dump(payload,h,ensure_ascii=False,separators=(",",":"));h.flush();os.fsync(h.fileno())
        try: os.chmod(temp,0o600)
        except OSError as exc: LOG.debug("snapshot temp chmod failed for %s: %s", temp, exc)
        if not persistent_write_gate(path):return
        os.replace(temp,path);_fsync_parent(path)
    except (OSError, TypeError, ValueError) as exc:
        LOG.warning('TMDB detail snapshot write failed for %s: %s', path, exc)
        try:
            if os.path.exists(temp):os.unlink(temp)
        except OSError as cleanup_exc:LOG.debug('TMDB snapshot temp cleanup failed: %s', cleanup_exc)

def _snapshot_metadata_complete(data):
    if not isinstance(data,dict):return False
    desc=str(data.get("overview") or (data.get("item_overlay") or {}).get("description") or "").strip()
    overlay=data.get("item_overlay") if isinstance(data.get("item_overlay"),dict) else {}
    actors=overlay.get("actors") or data.get("cast") or []
    director=overlay.get("director") or data.get("directors") or []
    # A full movie/series metadata hit needs synopsis plus at least one credit.
    return bool(desc and (actors or director))

def _snapshot_identity_compatible(item, data):
    """Fail closed when persisted metadata clearly belongs to another title."""
    item=item if isinstance(item,dict) else {}
    data=data if isinstance(data,dict) else {}
    if not data:return False
    if not data.get("tmdb_id") and not data.get("imdb_id"):
        return True

    current=[]
    for key in ("_raw_name","name","title","display_name","movie_name","series_name","original_name","original_title"):
        title=_canonical_metadata_title(item.get(key))
        if title and title not in current:current.append(title)

    stored=[]
    for key in ("title","name","original_title","original_name"):
        title=_canonical_metadata_title(data.get(key))
        if title and title not in stored:stored.append(title)

    generic=frozenset(("pure","movie","movies","film","films","vod","series","tv"))
    meaningful=[x for x in current if x not in generic]
    if meaningful and stored and all(x in generic for x in stored):
        return False
    if meaningful and stored:
        for c in meaningful:
            for d in stored:
                if c==d:return True
                shorter=min(len(c),len(d))
                if shorter>=7 and (c in d or d in c):
                    ct=set(c.split());dt=set(d.split())
                    overlap=float(len(ct & dt))/float(max(1,min(len(ct),len(dt))))
                    if overlap>=0.66:return True
        return False

    current_year=_canonical_metadata_year(item,{})
    stored_year=_canonical_metadata_year({},data)
    if current_year and stored_year:
        try:
            if abs(int(current_year)-int(stored_year))>=2:return False
        except Exception:pass
    return True

def _load_canonical_metadata_alias(profile, media_type, item):
    if not hdd_read_ready():return {}
    alias_keys=_canonical_metadata_alias_keys(profile,media_type,item,None)
    memory_key="|".join(alias_keys)
    memo=_canonical_alias_mem_get(memory_key)
    if isinstance(memo,dict) and _snapshot_identity_compatible(item,memo):
        return memo
    for key in alias_keys:
        try:
            with open(_canonical_metadata_alias_path(key),"r",encoding="utf-8") as h:data=json.load(h)
            if not isinstance(data,dict):continue
            if int(data.get("cache_schema") or 0)<CACHE_SCHEMA:continue
            if not (data.get("matched") and data.get("identity_verified")):continue
            if str(data.get("identity_source") or "") in ("","portal_payload"):continue
            if not _snapshot_metadata_complete(data):continue
            if not _snapshot_identity_compatible(item,data):continue
            _canonical_alias_mem_put(memory_key,data)
            return data
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            LOG.debug('canonical metadata alias read failed for %s: %s', key, exc)
            continue
    return {}

def _save_canonical_metadata_aliases(profile, media_type, item, payload):
    if not isinstance(payload,dict) or not payload.get("matched") or not payload.get("identity_verified"):return
    if str(payload.get("identity_source") or "") in ("","portal_payload"):return
    keys=_canonical_metadata_alias_keys(profile,media_type,item,payload)
    _canonical_alias_mem_put("|".join(keys),payload)
    for key in keys:
        path=_canonical_metadata_alias_path(key);temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
        try:
            if not persistent_write_gate(temp):continue
            with open(temp,"w",encoding="utf-8") as h:
                json.dump(payload,h,ensure_ascii=False,separators=(",",":"));h.flush();os.fsync(h.fileno())
            try: os.chmod(temp,0o600)
            except OSError as exc: LOG.debug("canonical alias temp chmod failed for %s: %s", temp, exc)
            if not persistent_write_gate(path):continue
            os.replace(temp,path);_fsync_parent(path)
        except (OSError, TypeError, ValueError) as exc:
            LOG.warning('canonical metadata alias write failed for %s: %s', path, exc)
            try:
                if os.path.exists(temp):os.unlink(temp)
            except OSError as cleanup_exc:LOG.debug('canonical alias temp cleanup failed: %s', cleanup_exc)

def load_detail_snapshot(profile, media_type, item):
    path = detail_snapshot_path(profile, media_type, item)
    if not hdd_read_ready():
        return {}
    exact={}
    try:
        with open(path,"r",encoding="utf-8") as h:data=json.load(h)
        if isinstance(data,dict) and int(data.get("cache_schema") or 0)>=CACHE_SCHEMA:
            if not (data.get("matched") and not data.get("identity_verified")):
                compatible=_snapshot_identity_compatible(item,data)
                if compatible:
                    exact=data
                    source=str(data.get("identity_source") or "")
                    if data.get("matched") and data.get("identity_verified") and source not in ("","portal_payload"):
                        if _snapshot_metadata_complete(data):
                            try:_save_canonical_metadata_aliases(profile,media_type,item,data)
                            except Exception as exc:LOG.debug('canonical alias refresh failed: %s', exc)
                            try:_save_detail_tmdb_snapshot(data)
                            except Exception as exc:LOG.debug('TMDB snapshot refresh failed: %s', exc)
                            try:_save_detail_imdb_snapshot(data)
                            except Exception as exc:LOG.debug('IMDb snapshot refresh failed: %s', exc)
                            return data
                else:
                    LOG.info("Rejected stale detail identity for current catalogue item")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOG.debug('detail snapshot read failed for %s: %s', path, exc)
        exact={}
    canonical=_load_canonical_metadata_alias(profile,media_type,item)
    return canonical or exact

def save_detail_snapshot(profile, media_type, item, data):
    if not isinstance(data, dict) or not data:
        return False
    if not hdd_ready() or not ensure_persistent_dirs(INDEX):
        return False
    path = detail_snapshot_path(profile, media_type, item)
    payload = dict(data)
    payload["cache_schema"] = CACHE_SCHEMA
    payload["updated_at"] = int(time.time())
    temp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    try:
        if not persistent_write_gate(temp): return False
        with open(temp, "w", encoding="utf-8") as h:
            json.dump(payload, h, ensure_ascii=False, separators=(",",":"))
            h.flush(); os.fsync(h.fileno())
        try: os.chmod(temp,0o600)
        except OSError as exc: LOG.debug("detail snapshot temp chmod failed for %s: %s", temp, exc)
        if not persistent_write_gate(path): return False
        os.replace(temp, path); _fsync_parent(path)
        if _snapshot_metadata_complete(payload):
            try:_save_canonical_metadata_aliases(profile,media_type,item,payload)
            except Exception as exc:LOG.debug('canonical alias save failed: %s', exc)
            try:_save_detail_tmdb_snapshot(payload)
            except Exception as exc:LOG.debug('TMDB snapshot save failed: %s', exc)
            try:_save_detail_imdb_snapshot(payload)
            except Exception as exc:LOG.debug('IMDb snapshot save failed: %s', exc)
        return True
    except Exception as exc:
        LOG.debug("detail snapshot write failed: %s", exc)
        try:
            if os.path.exists(temp) and persistent_write_gate(temp): os.unlink(temp)
        except OSError as cleanup_exc:
            LOG.debug('detail snapshot temp cleanup failed: %s', cleanup_exc)
        return False

