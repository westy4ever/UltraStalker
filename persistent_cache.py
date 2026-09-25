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
import tempfile
import time
import threading
from .securefs import secure_private_dir
from .identity import content_digest

CACHE_SCHEMA = 8
LOG = logging.getLogger("UltraStalker.PersistentCache")
PLUGIN_DIRNAME = "UltraStalker"
# Final layout keeps only canonical media identity long-term. View-specific artwork
# and URL-keyed source staging live in an HDD-backed Enigma2-process session.
# This avoids tmpfs/RAM pressure while preventing those derivatives from becoming
# a permanent multi-GB library. A new Enigma2 PID naturally starts a new session.
PERFLAB_SESSION_MODE = True
SESSION_CACHE_ROOT = os.path.join("/media/hdd", PLUGIN_DIRNAME, ".sessions", "enigma2-%d" % os.getpid())
VOLATILE_CACHE_ROOT = SESSION_CACHE_ROOT
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


def _fsync_parent(path):
    try:
        fd=os.open(os.path.dirname(os.path.abspath(path)) or ".",os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except OSError as exc:
        LOG.debug('parent fsync failed for %s: %s', path, exc)


# Legacy root-hint is never read or written in the fresh architecture.

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

def _managed_cache_location(path):
    absolute=os.path.abspath(str(path or ""))
    root=os.path.abspath(ROOT)
    volatile=os.path.abspath(VOLATILE_CACHE_ROOT)
    if absolute == root or absolute.startswith(root + os.sep):
        return "persistent"
    if absolute == volatile or absolute.startswith(volatile + os.sep):
        return "volatile"
    return ""

def ensure_persistent_dirs(*paths):
    """Create managed cache directories without leaking UI derivatives to HDD.

    Canonical metadata/posters/backdrops live below ``ROOT`` and keep the old
    fail-closed HDD safety. Reproducible presentation derivatives live below
    ``VOLATILE_CACHE_ROOT`` and may be recreated locally from canonical art.
    """
    targets=list(paths or (ROOT,))
    kinds=[_managed_cache_location(path) for path in targets]
    if not all(kinds):
        return False
    if "persistent" in kinds and not hdd_ready():
        return False
    try:
        for path in targets:
            absolute=os.path.abspath(str(path or ""))
            if _managed_cache_location(absolute)=="volatile":
                secure_private_dir(absolute)
            else:
                os.makedirs(absolute, mode=0o700, exist_ok=True)
        return True
    except Exception:
        return False


def persistent_write_gate(*targets):
    """Fail closed for canonical HDD writes and safely allow session derivatives.

    This keeps every existing caller unchanged while separating long-lived media
    identity from disposable view-specific presentation files.
    """
    rows=list(targets or (ROOT,))
    kinds=[_managed_cache_location(target) for target in rows]
    if not all(kinds):
        return False
    if "persistent" in kinds and not hdd_ready():
        return False
    try:
        dirs=[]
        for target,kind in zip(rows,kinds):
            absolute=os.path.abspath(str(target or ""))
            root_for_kind=os.path.abspath(ROOT if kind=="persistent" else VOLATILE_CACHE_ROOT)
            if absolute == root_for_kind or os.path.isdir(absolute):
                directory=absolute
            else:
                base=os.path.basename(absolute)
                directory=os.path.dirname(absolute) if os.path.splitext(base)[1] else absolute
            if directory and directory not in dirs:dirs.append(directory)
        for directory in dirs:
            if _managed_cache_location(directory)=="volatile":secure_private_dir(directory)
            else:os.makedirs(directory,mode=0o700,exist_ok=True)
        if "persistent" in kinds and not _write_mount_ready_uncached():
            return False
        for target,kind in zip(rows,kinds):
            if kind=="volatile":
                parent=target if os.path.isdir(target) else (os.path.dirname(os.path.abspath(target)) or VOLATILE_CACHE_ROOT)
                if not os.access(parent,os.W_OK):return False
        return True
    except Exception:
        return False


ROOT = _select_root()
# Test78 restores the original fixed HDD layout under one UltraStalker root.
# No movie-/tv-/content-specific directories are created.  Every view resolves
# artwork from these same flat buckets, so a poster/backdrop downloaded once is
# immediately reusable by Grid, Cinematic, Details, Player, Favorites and Search.
POSTERS = os.path.join(ROOT, "posters")
BACKDROPS = os.path.join(ROOT, "backdrops")
GENERATED = os.path.join(VOLATILE_CACHE_ROOT, "generated")
SOURCE_POSTERS = os.path.join(VOLATILE_CACHE_ROOT, "source_posters")
SOURCE_BACKDROPS = os.path.join(VOLATILE_CACHE_ROOT, "source_backdrops")
INDEX = os.path.join(ROOT, "index")
QUALITY = os.path.join(ROOT, "quality")
HOME = os.path.join(ROOT, "hero")
LIVE = os.path.join(ROOT, "live")
LOGS = os.path.join(ROOT, "logs")
# Explicit BLUE Cache Artwork owns a tiny durable namespace separate from
# session-only presentation derivatives.  Canonical posters/backdrops remain
# the source of truth; only user-requested readiness/final fast assets live here.
BLUE_CACHE = os.path.join(ROOT, "blue_cache")
# R171: final title-adaptive chrome is durable artwork-adjacent cache, not session state.
# It is intentionally separate from GENERATED so Enigma2 restarts never force a rebuild.
ADAPTIVE = os.path.join(ROOT, "adaptive")
TITLE_LOGOS = os.path.join(ROOT, "title_logo")

# Compatibility aliases for modules that historically imported these names.
# LIBRARY/LOOKUP are metadata/index only now; artwork never lives below them.
LIBRARY = INDEX
LOOKUP = INDEX
DERIVED = GENERATED
PORTAL_ART = POSTERS

ALL_DIRS = (ROOT, POSTERS, BACKDROPS, GENERATED, INDEX, QUALITY, HOME, LIVE, LOGS, BLUE_CACHE, ADAPTIVE, TITLE_LOGOS)


_SESSION_DIR_RE = re.compile(r"^enigma2-(\d+)$")


def cleanup_stale_session_caches():
    """Remove only stale Ultra Stalker Enigma2 session derivatives.

    PERFLAB10 lifecycle rule:
      * canonical posters/backdrops/index/BLUE data are never touched here;
      * the current process session is always protected;
      * only sibling ``.sessions/enigma2-<pid>`` directories are candidates;
      * cleanup is intended to run from a background startup worker, never from
        navigation/UI hot paths.

    Returns a small result dictionary so callers can log/diagnose the cleanup
    without walking the trees a second time merely to calculate sizes.
    """
    result = {"ready": False, "removed": 0, "skipped_live": 0, "errors": 0}
    if not PERFLAB_SESSION_MODE:
        return result
    if not _write_mount_ready_uncached():
        return result

    sessions_root = os.path.abspath(os.path.join(ROOT, ".sessions"))
    current = os.path.abspath(SESSION_CACHE_ROOT)
    try:
        # Fail closed if constants ever drift outside the dedicated PerfLab root.
        if os.path.dirname(current) != sessions_root:
            LOG.warning("session cleanup refused: current root escaped .sessions: %s", current)
            return result
        if not os.path.isdir(sessions_root):
            result["ready"] = True
            return result

        import shutil
        result["ready"] = True
        for entry in os.scandir(sessions_root):
            try:
                match = _SESSION_DIR_RE.match(entry.name or "")
                if not match or not entry.is_dir(follow_symlinks=False):
                    continue
                path = os.path.abspath(entry.path)
                if path == current:
                    continue
                if os.path.dirname(path) != sessions_root or os.path.ismount(path):
                    continue

                # Be conservative if another live Enigma2 process somehow exists.
                try:
                    pid = int(match.group(1))
                    proc_cmd = "/proc/%d/cmdline" % pid
                    if os.path.isfile(proc_cmd):
                        with open(proc_cmd, "rb") as h:
                            cmd = h.read(512).replace(b"\\x00", b" ").lower()
                        if b"enigma2" in cmd:
                            result["skipped_live"] += 1
                            continue
                except Exception:
                    pass

                shutil.rmtree(path)
                result["removed"] += 1
            except Exception as exc:
                result["errors"] += 1
                LOG.warning("stale session cleanup failed for %s: %s", getattr(entry, "path", "?"), exc)
    except Exception as exc:
        result["errors"] += 1
        LOG.warning("stale session cleanup scan failed: %s", exc)
    return result

# Beta54 is the one-time clean break from every pre-global-library HDD layout.
# The marker deliberately lives under /etc, outside the HDD tree being deleted,
# so reinstalling/upgrading later never wipes the new persistent library again.
FRESH_RESET_MARKER = "/etc/enigma2/ultrastalker/.final_hdd_global_v1_done"
FRESH_RESET_IN_PROGRESS = "/etc/enigma2/ultrastalker/.final_hdd_global_v1_resetting"
# Beta59: the HDD carries its own non-destructive schema marker.  Losing /etc
# during an image reflash must never make a valid global library look "old" and
# trigger another destructive reset.
HDD_LIBRARY_SCHEMA_MARKER = os.path.join(ROOT, ".global_library_schema_v1")

LEGACY_ARTWORK_V5_MARKER = os.path.join(ROOT, ".tmdb_master_store_v5")

def _retire_legacy_artwork_system_once():
    """Adopt the legacy artwork layout non-destructively.

    V7.5.5 policy: no automatic migration/maintenance path may delete persistent
    HDD artwork. Readers may ignore retired duplicates, but the files stay until
    the user explicitly chooses a cache-clear action.
    """
    if not _mounted_hdd_uncached() or os.path.isfile(LEGACY_ARTWORK_V5_MARKER):
        return True
    try:
        _write_reset_state(LEGACY_ARTWORK_V5_MARKER, "tmdb-master-store-v5-preserved")
        return True
    except Exception as exc:
        LOG.warning("legacy artwork preservation marker failed: %s", exc)
        return False

# Final cache reset remains non-destructive: no automatic schema path is allowed to
# target historical user artwork/cache locations.
LEGACY_HDD_ROOTS = ()

def _fresh_reset_done():
    try:
        if os.path.isfile(FRESH_RESET_MARKER):return True
        # A valid HDD-side schema marker is authoritative after an image reflash.
        # Recreate the disposable /etc marker, but never delete the library.
        if _mounted_hdd_uncached() and os.path.isfile(HDD_LIBRARY_SCHEMA_MARKER):
            try:_write_reset_state(FRESH_RESET_MARKER,"global-tmdb-library-v4")
            except Exception:pass
            return True
    except Exception:
        pass
    return False

def _write_reset_state(path,text):
    try:
        directory=os.path.dirname(path);os.makedirs(directory,mode=0o700,exist_ok=True)
        temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
        with open(temp,"w",encoding="utf-8") as h:h.write(text+"\n");h.flush();os.fsync(h.fileno())
        try:os.chmod(temp,0o600)
        except OSError:pass
        os.replace(temp,path);_fsync_parent(path)
        _save_canonical_metadata_aliases(profile,media_type,item,payload)
        return True
    except Exception as exc:
        LOG.warning("fresh reset state write failed %s: %s",path,exc);return False

def _safe_remove_tree(path):
    absolute=os.path.abspath(str(path or ""))
    if absolute not in LEGACY_HDD_ROOTS:return False
    if absolute.startswith("/media/hdd/") and not _mounted_hdd_uncached():return False
    if not os.path.lexists(absolute):return True
    try:
        if os.path.islink(absolute) or os.path.isfile(absolute):os.unlink(absolute)
        else:
            import shutil as _fresh_shutil
            _fresh_shutil.rmtree(absolute)
        return not os.path.lexists(absolute)
    except Exception as exc:
        LOG.warning("fresh reset delete failed for %s: %s",absolute,exc);return False

def ensure_fresh_hdd_reset_once():
    """Ensure the fixed HDD layout without deleting any existing artwork.

    Test78 deliberately retires the old destructive schema reset. Existing
    /media/hdd/UltraStalker content is preserved; old per-title directories can
    be adopted lazily by the readers while every new write uses the fixed flat
    posters/backdrops/generated/index buckets.
    """
    if not _mounted_hdd_uncached():
        return False
    try:
        os.makedirs(ROOT, mode=0o700, exist_ok=True)
        for path in (POSTERS, BACKDROPS, GENERATED, INDEX, QUALITY, HOME, LIVE, LOGS, BLUE_CACHE, ADAPTIVE, TITLE_LOGOS):
            os.makedirs(path, mode=0o700, exist_ok=True)
        if not os.path.isfile(HDD_LIBRARY_SCHEMA_MARKER):
            _write_reset_state(HDD_LIBRARY_SCHEMA_MARKER, "fixed-flat-artwork-v1")
        if not os.path.isfile(FRESH_RESET_MARKER):
            _write_reset_state(FRESH_RESET_MARKER, "fixed-flat-artwork-v1")
        _retire_legacy_artwork_system_once()
        try:
            if os.path.isfile(FRESH_RESET_IN_PROGRESS):
                os.unlink(FRESH_RESET_IN_PROGRESS);_fsync_parent(FRESH_RESET_IN_PROGRESS)
        except Exception:
            pass
        return True
    except Exception as exc:
        LOG.warning("fixed HDD layout initialization failed: %s", exc)
        return False

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
        if not ensure_fresh_hdd_reset_once():
            return False
        if not hdd_ready() or not ensure_persistent_dirs(*ALL_DIRS):
            return False
        confirmed=_mount_identity_uncached()
        if confirmed is None or confirmed != current:
            _INIT_DONE=False;_INIT_MOUNT_ID=None
            return False
        try:
            # Clean-start policy: never import or probe historical UltraStalker
            # artwork/cache trees.  The mounted HDD root is authoritative.
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
            "index": INDEX,
            "home": HOME,
            "live": LIVE,
            "quality": QUALITY,
            "adaptive": ADAPTIVE,
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
    return os.path.join(LOOKUP, "detail_" + content_cache_key(profile, media_type, item) + ".json")

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
    return os.path.join(LOOKUP,"detail_alias_"+str(key)+".json")

def _library_item_record_path(media_type,tmdb_id):
    mt=_canonical_media_type(media_type)
    try:tid=str(int(tmdb_id))
    except Exception:return ""
    return os.path.join(INDEX,"%s_%s.json"%(mt,tid))

def _load_library_item_record(media_type,tmdb_id):
    path=_library_item_record_path(media_type,tmdb_id)
    if not path or not hdd_read_ready():return {}
    try:
        with open(path,"r",encoding="utf-8") as h:data=json.load(h)
        return data if isinstance(data,dict) else {}
    except Exception as exc:
        LOG.debug("global library metadata read failed %s: %s",path,exc);return {}

def _detail_tmdb_snapshot_path(media_type,tmdb_id):
    mt=_canonical_media_type(media_type)
    try:tid=str(int(tmdb_id))
    except Exception:return ""
    return os.path.join(LOOKUP,"detail_tmdb_%s_%s.json"%(mt,tid))

def load_detail_snapshot_by_tmdb(media_type,tmdb_id):
    data=_load_library_item_record(media_type,tmdb_id)
    if data:return data
    return {}


def _detail_imdb_snapshot_path(imdb_id):
    imdb=str(imdb_id or "").strip().lower()
    if not re.match(r"^tt\d{5,12}$",imdb):return ""
    return os.path.join(LOOKUP,"detail_imdb_%s.json"%imdb)

def load_detail_snapshot_by_imdb(imdb_id):
    path=_detail_imdb_snapshot_path(imdb_id)
    if not path or not hdd_read_ready():return {}
    try:
        with open(path,"r",encoding="utf-8") as h:pointer=json.load(h)
        if not isinstance(pointer,dict) or not pointer.get("tmdb_id"):return {}
        return _load_library_item_record(pointer.get("media_type"),pointer.get("tmdb_id"))
    except (OSError,ValueError,TypeError):return {}

def load_shared_detail_snapshot(profile, media_type, item):
    item=item if isinstance(item,dict) else {};mt=_canonical_media_type(media_type)
    tmdb_id=item.get("tmdb_id") or item.get("tmdbid")
    if tmdb_id not in (None,""):
        snap=load_detail_snapshot_by_tmdb(mt,tmdb_id)
        if snap:return snap
    imdb_id=item.get("imdb_id") or item.get("imdb")
    if imdb_id:
        snap=load_detail_snapshot_by_imdb(imdb_id)
        if snap:return snap
    return load_detail_snapshot(profile or {},mt,item)

def _save_detail_imdb_snapshot(payload):
    if not isinstance(payload,dict) or not payload.get("tmdb_id"):return
    imdb=str(payload.get("imdb_id") or "").strip().lower();path=_detail_imdb_snapshot_path(imdb)
    if not path:return
    pointer={"cache_schema":CACHE_SCHEMA,"tmdb_id":int(payload.get("tmdb_id")),"media_type":_canonical_media_type(payload.get("media_type")),"updated_at":int(time.time())}
    temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
    try:
        if not persistent_write_gate(temp):return
        with open(temp,"w",encoding="utf-8") as h:json.dump(pointer,h,separators=(",",":"));h.flush();os.fsync(h.fileno())
        if not persistent_write_gate(path):return
        os.replace(temp,path);_fsync_parent(path)
    except Exception as exc:LOG.debug("IMDb pointer save failed: %s",exc)

def _save_detail_tmdb_snapshot(payload):
    # Global TMDB metadata lives only in LIBRARY; there is no duplicate TMDB JSON in LOOKUP.
    return

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
    for key in ("identity_catalogue_title","title","name","original_title","original_name"):
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
        except Exception as exc:LOG.debug("snapshot year comparison failed: %s",exc)
    return True


def _load_global_media_title_alias(media_type, item):
    """Portal-independent title/year lookup into the one TMDB media library.

    ``media_library`` already owns ambiguity-safe aliases written by ArtworkV2.
    Import lazily to avoid the persistent_cache <-> media_library module cycle.
    The returned row carries ``identity_pointer_verified`` and the exact provider
    catalogue title that created the alias, so localized TMDB titles cannot make
    M3U/Xtream reject a record previously learned from a Portal.
    """
    if not hdd_read_ready():return {}
    item=item if isinstance(item,dict) else {}
    try:
        from .media_library import load_alias as _global_alias_load
    except Exception:
        return {}
    year=_canonical_metadata_year(item,{})
    titles=[]
    for key in ("_raw_name","name","title","display_name","movie_name","series_name","original_name","original_title"):
        value=str(item.get(key) or "").strip()
        if value and value not in titles:titles.append(value)
    for title in titles:
        try:
            row=_global_alias_load(media_type,title,year) or {}
            if row and _snapshot_identity_compatible(item,row):
                return row
        except Exception as exc:
            LOG.debug("global media title alias read failed: %s",exc)
    return {}

def _load_canonical_metadata_alias(profile, media_type, item):
    if not hdd_read_ready():return {}
    for key in _canonical_metadata_alias_keys(profile,media_type,item,None):
        try:
            with open(_canonical_metadata_alias_path(key),"r",encoding="utf-8") as h:pointer=json.load(h)
            if not isinstance(pointer,dict) or not pointer.get("tmdb_id"):continue
            data=_load_library_item_record(pointer.get("media_type") or media_type,pointer.get("tmdb_id"))
            if data and _snapshot_identity_compatible(item,data):return data
        except (OSError,ValueError,TypeError):continue
    return {}

def _save_canonical_metadata_aliases(profile, media_type, item, payload):
    if not isinstance(payload,dict) or not payload.get("tmdb_id") or not payload.get("identity_verified"):return
    pointer={"cache_schema":CACHE_SCHEMA,"tmdb_id":int(payload.get("tmdb_id")),"media_type":_canonical_media_type(payload.get("media_type") or media_type),"updated_at":int(time.time())}
    for key in _canonical_metadata_alias_keys(profile,media_type,item,payload):
        path=_canonical_metadata_alias_path(key);temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
        try:
            if not persistent_write_gate(temp):continue
            with open(temp,"w",encoding="utf-8") as h:json.dump(pointer,h,separators=(",",":"));h.flush();os.fsync(h.fileno())
            if not persistent_write_gate(path):continue
            os.replace(temp,path);_fsync_parent(path)
        except Exception as exc:LOG.debug("canonical pointer save failed: %s",exc)

def load_detail_snapshot(profile, media_type, item):
    if not hdd_read_ready():return {}
    # First ask the one global title/year alias shared by Portal, Xtream and M3U.
    # It is ambiguity-safe and preserves the provider title that proved identity.
    global_alias=_load_global_media_title_alias(media_type,item)
    if global_alias:return global_alias
    alias_row=_load_canonical_metadata_alias(profile,media_type,item)
    if alias_row:return alias_row
    path=detail_snapshot_path(profile,media_type,item)
    try:
        with open(path,"r",encoding="utf-8") as h:pointer=json.load(h)
        if isinstance(pointer,dict) and pointer.get("tmdb_id"):
            global_row=_load_library_item_record(pointer.get("media_type") or media_type,pointer.get("tmdb_id"))
            if global_row and _snapshot_identity_compatible(item,global_row):return global_row
        if isinstance(pointer,dict):return pointer
    except Exception as exc:LOG.debug("detail pointer read failed %s: %s",path,exc)
    return {}


def save_detail_snapshot(profile, media_type, item, data):
    if not isinstance(data,dict) or not data or not hdd_ready():return False
    payload=dict(data);payload["cache_schema"]=CACHE_SCHEMA;payload["updated_at"]=int(time.time())
    tmdb_id=payload.get("tmdb_id");mt=_canonical_media_type(payload.get("media_type") or media_type)
    if tmdb_id:
        # One writer owns global metadata. Import lazily to avoid a module cycle.
        try:
            from .media_library import save as _global_media_save
            if not _global_media_save(mt,tmdb_id,payload):return False
        except Exception as exc:
            LOG.debug("global library metadata save failed: %s",exc);return False
        pointer={"cache_schema":CACHE_SCHEMA,"tmdb_id":int(tmdb_id),"media_type":mt,"title":payload.get("title"),"year":payload.get("year"),"updated_at":int(time.time())}
    else:
        # No verified TMDB identity means there is no global record to point at.
        return False
    path=detail_snapshot_path(profile,media_type,item);temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
    try:
        if not persistent_write_gate(temp):return False
        with open(temp,"w",encoding="utf-8") as h:
            json.dump(pointer,h,ensure_ascii=False,separators=(",",":"));h.flush();os.fsync(h.fileno())
        if not persistent_write_gate(path):return False
        os.replace(temp,path);_fsync_parent(path)
        _save_canonical_metadata_aliases(profile,media_type,item,payload)
        return True
    except Exception as exc:
        LOG.debug("detail pointer save failed: %s",exc);return False
