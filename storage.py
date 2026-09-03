# -*- coding: utf-8 -*-
import copy
import hashlib
from .netsec import validate_http_url
import json
import os
import re
import threading
import time
import urllib.parse

from .log import get_logger
LOG = get_logger()

CONFIG_DIR = "/etc/enigma2/ultrastalker"
CONFIG_FILE = os.path.join(CONFIG_DIR, "profiles.json")
IMPORT_FILE = os.path.join(CONFIG_DIR, "portals.txt")
DISABLED_FILE = os.path.join(CONFIG_DIR, "disabled_profiles.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
API_KEYS_FILE = os.path.join(CONFIG_DIR, "api_keys.conf")
RECOVERY_NOTICE_FILE = os.path.join(CONFIG_DIR, "recovery_notices.log")
THEMES = ("nova_fhd", "oled_black", "midnight_purple")
DEFAULT_SETTINGS = {"theme": "nova_fhd", "service_type": 4097, "timeout": 10, "load_images": True, "epg_hours": 4, "catchup_hours": 72, "search_max_pages": 250, "image_cache_mb": 128, "persistent_cache_mb": 20480, "content_page_size": 50, "parental_lock": False, "hide_adult": True, "diagnostic_logging": False, "first_run_wizard": True, "show_live": True, "show_movies": True, "show_series": True, "show_catchup": True, "live_preview": False, "parental_pin": "0000", "parental_pin_hash": "", "parental_pin_salt": "", "hidden_categories": {"itv": [], "vod": [], "series": []}, "protected_categories": {"itv": [], "vod": [], "series": []}, "adult_keywords": ["adult", "xxx", "18+", "porn"],
    "clean_titles": True, "show_quality_badges": True,
    "show_channel_numbers": True, "channel_list_mode": "epg", "remember_location": False,
    "smart_engine": False, "hide_empty_categories": True, "pinned_categories": {"itv": [], "vod": [], "series": []},
    "empty_categories": {"itv": [], "vod": [], "series": []}, "animations": "subtle", "prefetch_images": True,
    "show_watched": True, "parental_mode": "pin", "parental_session_minutes": 30, "download_reserve_mb": 1024,
    "smart_recovery": True, "stream_retry_count": 1, "multi_portal_search": True,
    "tmdb_enabled": True, "tmdb_credential": "", "tmdb_language": "ar-EG",
    "proxy_port": 17999, "epg_refresh_budget": 45, "search_time_budget": 12,
    "resume_behavior": "always", "crash_safe_progress": True, "progress_save_seconds": 10,
    "next_episode_countdown": 10, "auto_remove_completed": True, "completion_threshold": 93, "completion_remaining_seconds": 180,
    "per_title_engine": True}

MAC_RE = re.compile(r"^\s*([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{12})\s*$")
MAC_ANY_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
URL_RE = re.compile(r"(https?://[^\s|;,\]\[<>{}\"']+)", re.I)
BARE_PORTAL_RE = re.compile(
    r"(?<![@\w])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}(?::\d{1,5})?(?:/[^\s|;,\]\[<>{}\"']*)?|(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?:/[^\s|;,\]\[<>{}\"']*)?)",
    re.I,
)

_SETTINGS_CACHE_LOCK = threading.RLock()
_SETTINGS_CACHE = None
_SETTINGS_CACHE_SIGNATURE = None
_API_KEYS_CACHE = None
_API_KEYS_SIGNATURE = None
STATE_IO_LOCK = threading.RLock()


def _fsync_parent_dir(path):
    """Persist an atomic rename itself, not only the file contents."""
    directory = os.path.dirname(os.path.abspath(str(path or ""))) or "."
    try:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        LOG.debug('parent directory fsync failed for %s: %s', directory, exc)


def _file_signature(path):
    try:
        stat = os.stat(path)
        return (getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1000000000)), stat.st_size)
    except OSError:
        return None


def _invalidate_settings_cache():
    global _SETTINGS_CACHE, _SETTINGS_CACHE_SIGNATURE
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE = None
        _SETTINGS_CACHE_SIGNATURE = None


def _invalidate_api_keys_cache():
    global _API_KEYS_CACHE, _API_KEYS_SIGNATURE
    with _SETTINGS_CACHE_LOCK:
        _API_KEYS_CACHE = None
        _API_KEYS_SIGNATURE = None
    _invalidate_settings_cache()


def _record_recovery_notice(path, quarantined):
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        with open(RECOVERY_NOTICE_FILE, "a", encoding="utf-8") as handle:
            handle.write("Recovered malformed %s as %s\n" % (os.path.basename(path), os.path.basename(quarantined)))
        os.chmod(RECOVERY_NOTICE_FILE, 0o600)
    except OSError as exc:
        LOG.warning('could not record recovery notice: %s', exc)


def consume_recovery_notices():
    try:
        with open(RECOVERY_NOTICE_FILE, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle.readlines() if line.strip()]
        os.unlink(RECOVERY_NOTICE_FILE)
        return lines[-8:]
    except OSError:
        return []


def _quarantine_corrupt_file(path):
    """Move malformed persisted data aside so a later save cannot destroy it."""
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path + ".corrupt-" + stamp
    suffix = 1
    while os.path.exists(target):
        target = path + ".corrupt-%s-%d" % (stamp, suffix)
        suffix += 1
    try:
        os.replace(path, target)
        _fsync_parent_dir(target)
        try: os.chmod(target, 0o600)
        except OSError as exc: LOG.debug('chmod failed for quarantined file %s: %s', target, exc)
        _record_recovery_notice(path, target)
        return target
    except OSError:
        return None


def load_json_file(path, expected_type, default):
    """Load bounded JSON and quarantine malformed/wrong-shaped files instead of hiding damage."""
    try:
        if os.path.getsize(path) > 8 * 1024 * 1024:
            _quarantine_corrupt_file(path)
            LOG.warning("Oversized state file quarantined: %s", os.path.basename(path))
            return default.copy() if isinstance(default, (dict, list)) else default
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        return default.copy() if isinstance(default, (dict, list)) else default
    except (ValueError, TypeError):
        _quarantine_corrupt_file(path)
        return default.copy() if isinstance(default, (dict, list)) else default
    if not isinstance(data, expected_type):
        _quarantine_corrupt_file(path)
        return default.copy() if isinstance(default, (dict, list)) else default
    return data



def ensure_files():
    with STATE_IO_LOCK:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        try:
            os.chmod(CONFIG_DIR, 0o700)
        except OSError as exc:
            LOG.debug('config directory chmod failed: %s', exc)
        if not os.path.exists(IMPORT_FILE):
            with open(IMPORT_FILE, "w", encoding="utf-8") as handle:
                handle.write("# Ultra Stalker import file\n")
                handle.write("# URL then one or more MAC addresses, or URL|MAC\n")
                handle.flush(); os.fsync(handle.fileno())
            try:
                os.chmod(IMPORT_FILE, 0o600)
            except OSError as exc:
                LOG.debug('import file chmod failed: %s', exc)
        if not os.path.exists(API_KEYS_FILE):
            try:
                with open(API_KEYS_FILE, "w", encoding="utf-8") as handle:
                    handle.write("# Ultra Stalker API credentials\n")
                    handle.write("TMDB_API_KEY=\nTMDB_READ_TOKEN=\nIMDB_API_KEY=\nIMDB_API_ENDPOINT=\nSUBDL_API_KEY=\n")
                    handle.flush(); os.fsync(handle.fileno())
                os.chmod(API_KEYS_FILE, 0o600)
            except OSError as exc:
                LOG.warning('API keys file initialization failed: %s', exc)

        else:
            # Forward-compatible credential migration: existing installations
            # keep their current secrets, while newly introduced key names are
            # appended only when missing.
            try:
                with open(API_KEYS_FILE, "r", encoding="utf-8", errors="replace") as handle:
                    current_lines = handle.readlines()
                existing = set()
                for raw in current_lines:
                    stripped = raw.strip()
                    if stripped and not stripped.startswith(("#", ";")) and "=" in stripped:
                        existing.add(stripped.split("=", 1)[0].strip().upper())
                missing = [k for k in ("TMDB_API_KEY","TMDB_READ_TOKEN","IMDB_API_KEY","IMDB_API_ENDPOINT","SUBDL_API_KEY") if k not in existing]
                if missing:
                    temp = API_KEYS_FILE + ".migrate.%d.%d" % (os.getpid(), threading.get_ident())
                    with open(temp, "w", encoding="utf-8") as handle:
                        handle.writelines(current_lines)
                        if current_lines and current_lines[-1].strip():
                            handle.write("\n")
                        for key in missing:
                            handle.write("%s=\n" % key)
                        handle.flush(); os.fsync(handle.fileno())
                    os.chmod(temp, 0o600)
                    os.replace(temp, API_KEYS_FILE)
                    _fsync_parent_dir(API_KEYS_FILE)
                    _invalidate_api_keys_cache()
            except OSError as exc:
                LOG.warning('API keys migration failed: %s', exc)

def _looks_like_m3u_profile_url(value):
    low=str(value or "").strip().lower()
    return (".m3u" in low or "type=m3u" in low or "output=m3u" in low or
            ("get.php" in low and ("username=" in low or "password=" in low)))

def _synthetic_m3u_mac(value):
    # Internal compatibility key only; never sent to the playlist provider.
    h=hashlib.sha1(str(value or "").encode("utf-8","ignore")).hexdigest()[:10]
    raw="02"+h
    return ":".join(raw[i:i+2] for i in range(0,12,2)).upper()

def _normalize_profile(profile, default_source="saved"):
    if not isinstance(profile, dict):
        return None
    portal = str(profile.get("portal") or "").strip().rstrip(".,;")
    source_type=str(profile.get("source_type") or ("m3u" if _looks_like_m3u_profile_url(portal) else "stalker")).strip().lower()
    if source_type=="m3u":
        low_portal=portal.lower()
        if low_portal.startswith("m3u://"):portal="http://"+portal[6:]
        elif low_portal.startswith("m3u8://"):portal="http://"+portal[7:]
    mac = _synthetic_m3u_mac(portal) if source_type=="m3u" else _canonical_mac(profile.get("mac"))
    had_explicit_scheme = portal.lower().startswith(("http://", "https://"))
    if not portal or not MAC_RE.match(mac):
        return None
    if not had_explicit_scheme:
        portal = "https://" + portal
    if not URL_RE.match(portal):
        return None
    try:
        validate_http_url(portal)
    except Exception:
        return None
    result = {
        "portal": portal,
        "mac": mac,
        "state": str(profile.get("state") or "Not checked")[:160],
        "source": str(profile.get("source") or default_source)[:40],
        "source_type": source_type if source_type in ("stalker","m3u") else "stalker",
        "health": str(profile.get("health") or "unknown")[:16],
        "name": str(profile.get("name") or "")[:120],
        "account_state": str(profile.get("account_state") or "")[:80],
        "expiry": str(profile.get("expiry") or "")[:80],
        # HARDENED BUILD: strip legacy insecure transport approvals whenever
        # profiles are normalized.
        "allow_http_fallback": False,
        "http_fallback_accepted": False,
        "tls_fallback_accepted": False,
        "tls_mode": "strict",
        "device_profile": str(profile.get("device_profile") or "auto").strip().lower(),
        "explicit_http_accepted": bool(profile.get("explicit_http_accepted", False)) if portal.lower().startswith("http://") else False,
    }
    if result["device_profile"] not in ("auto", "mag250", "mag254", "mag256"):
        result["device_profile"] = "auto"
    try:
        latency = int(profile.get("latency_ms", 0) or 0)
    except (TypeError, ValueError):
        latency = 0
    if latency > 0:
        result["latency_ms"] = min(latency, 120000)
    for field, limit in (("account_state", 80), ("expiry", 80), ("last_error", 240)):
        value = str(profile.get(field) or "").strip()
        if value:
            result[field] = value[:limit]
    try:
        last_success = int(profile.get("last_success", 0) or 0)
    except (TypeError, ValueError):
        last_success = 0
    if last_success > 0:
        result["last_success"] = last_success
    name = str(profile.get("name") or "").strip()
    if name:
        result["name"] = name[:80]
    return result


def _read_json_profiles():
    return load_json_file(CONFIG_FILE, list, [])


def _canonical_mac(value):
    raw = re.sub(r"[^0-9A-Fa-f]", "", str(value or ""))
    if len(raw) != 12:
        return ""
    return ":".join(raw[i:i+2] for i in range(0, 12, 2)).upper()


def _extract_macs(text):
    result = []
    seen = set()
    for match in MAC_ANY_RE.finditer(str(text or "")):
        mac = _canonical_mac(match.group(1))
        if mac and mac not in seen:
            seen.add(mac); result.append(mac)
    return result


def _clean_portal_candidate(value):
    value = str(value or "").strip().strip("\"'<>[]{}()")
    value = value.rstrip(".,;|)")
    # Provider notes often leave a slash-less host or an endpoint followed by
    # punctuation. Preserve useful MAG paths (/c/, /stalker_portal/, etc.).
    return value


def _extract_portal(text):
    line = str(text or "").strip()
    match = URL_RE.search(line)
    if match:
        return _clean_portal_candidate(match.group(1))
    # Accept labelled or plain schemeless hosts/IPs. _normalize_profile() will
    # add HTTPS first and retain the existing controlled HTTP fallback policy.
    match = BARE_PORTAL_RE.search(line)
    if match:
        return _clean_portal_candidate(match.group(1))
    return ""


def _import_paths():
    paths = [IMPORT_FILE]
    # Keep reading the former runtime directory as a compatibility bridge.
    # Build its name from neutral fragments so old branding never becomes UI.
    legacy_tag = "stalker" + "portal" + "obh"
    legacy_dir = os.path.join("/etc/enigma2", legacy_tag)
    for filename in ("portals.txt", "portal.txt"):
        candidate = os.path.join(legacy_dir, filename)
        if candidate not in paths:
            paths.append(candidate)
    # A few historical builds used portal.txt in the current directory.
    current_singular = os.path.join(CONFIG_DIR, "portal.txt")
    if current_singular not in paths:
        paths.append(current_singular)
    return paths


def _add_profile(result, seen, portal, mac):
    normalized = _normalize_profile({"portal": portal, "mac": _canonical_mac(mac), "source": "text", "source_type": "stalker"}, "text")
    if not normalized:
        return
    key = (normalized["portal"].rstrip("/").lower(), normalized["mac"])
    if key in seen:
        return
    seen.add(key)
    result.append(normalized)


def _add_m3u_profile(result, seen, portal):
    """Add one M3U URL as a complete text-import profile.

    M3U playlists do not have a MAG MAC line. _normalize_profile() creates the
    deterministic internal compatibility MAC used by the rest of Ultra Stalker;
    it is never sent to the IPTV provider.
    """
    normalized = _normalize_profile({
        "portal": portal,
        "mac": "",
        "source": "text",
        "source_type": "m3u",
        "state": "M3U • READY",
        "account_state": "M3U PLAYLIST",
        "health": "unknown",
    }, "text")
    if not normalized:
        return
    key = (normalized["portal"].rstrip("/").lower(), normalized["mac"])
    if key in seen:
        return
    seen.add(key)
    result.append(normalized)


def _xtream_value(line, key):
    """Read common labelled Xtream fields without being fussy about separators."""
    text=str(line or "").strip()
    match=re.search(r"(?i)(?:^|[|;,\s])%s\s*[:=]\s*([^|;,\s]+)" % re.escape(key), text)
    return str(match.group(1) or "").strip().strip("\"'") if match else ""


def _xtream_playlist_url(base, username, password):
    """Turn Server + Username + Password into the M3U URL used by M3UClient."""
    base=_clean_portal_candidate(base).rstrip("/")
    if not base or not username or not password:
        return ""
    if not base.lower().startswith(("http://","https://")):
        base="https://"+base
    try:
        parsed=urllib.parse.urlsplit(base)
        if not parsed.netloc:
            return ""
        root=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,parsed.path.rstrip("/"),"",""))
        query=urllib.parse.urlencode({"username":str(username),"password":str(password),"type":"m3u_plus","output":"ts"})
        return root.rstrip("/")+"/get.php?"+query
    except Exception:
        return ""


def _parse_import_lines(lines, result, seen):
    current_portal = None
    pending_macs = []
    xtream_base = ""
    xtream_user = ""
    xtream_pass = ""

    def commit_xtream():
        nonlocal xtream_base, xtream_user, xtream_pass
        if xtream_base and xtream_user and xtream_pass:
            url=_xtream_playlist_url(xtream_base,xtream_user,xtream_pass)
            if url:
                _add_m3u_profile(result,seen,url)
            xtream_base=xtream_user=xtream_pass=""
            return True
        return False

    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            commit_xtream()
            current_portal = None
            pending_macs = []
            continue
        if line.startswith(("#", ";", "//")):
            continue

        # Xtream providers commonly publish credentials either on one line or
        # as Server/URL + Username + Password on adjacent lines. Accept both.
        labelled_user=_xtream_value(line,"username") or _xtream_value(line,"user")
        labelled_pass=_xtream_value(line,"password") or _xtream_value(line,"pass")
        labelled_server=_xtream_value(line,"server") or _xtream_value(line,"host")
        portal = _extract_portal(line)
        macs = _extract_macs(line)
        if labelled_server:
            xtream_base=labelled_server
        elif portal and (labelled_user or labelled_pass):
            xtream_base=portal
        if labelled_user: xtream_user=labelled_user
        if labelled_pass: xtream_pass=labelled_pass
        if commit_xtream():
            current_portal=None;pending_macs=[];continue
        if (labelled_user or labelled_pass or labelled_server) and not macs:
            continue

        if portal:
            if _looks_like_m3u_profile_url(portal):
                _add_m3u_profile(result, seen, portal)
                current_portal = None
                pending_macs = []
                continue
            # A plain URL followed by Username/Password is also a standard
            # Xtream block. Keep it pending; a MAC still wins for Stalker.
            xtream_base=portal
            current_portal = portal
            if macs:
                xtream_base=""
                for mac in macs:
                    _add_profile(result, seen, current_portal, mac)
                pending_macs = []
            elif pending_macs:
                xtream_base=""
                for mac in pending_macs:
                    _add_profile(result, seen, current_portal, mac)
                pending_macs = []
            continue
        if macs:
            xtream_base=xtream_user=xtream_pass=""
            if current_portal:
                for mac in macs:
                    _add_profile(result, seen, current_portal, mac)
            else:
                for mac in macs:
                    if mac not in pending_macs:
                        pending_macs.append(mac)
    commit_xtream()
    return result

def parse_import_file():
    """Parse portal imports permissively and isolate malformed lines.

    Supported forms include URL|MAC, labelled fields, URL followed by one or
    more MAC lines, MAC-before-URL blocks, standalone M3U/get.php playlist URLs,
    schemeless domains/IPs, quoted values, comments and mixed separators. One
    bad line never invalidates the rest of the file. Compatibility paths from
    older builds are merged too.
    """
    ensure_files()
    result, seen = [], set()
    for path in _import_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
                _parse_import_lines(handle, result, seen)
        except OSError:
            continue
    return result


def load_profiles():
    """Load active portal profiles without resurrecting deleted text imports.

    Profiles whose source is ``text`` are owned by portals.txt (including the
    compatibility import paths).  Older builds also copied those rows into
    profiles.json, so deleting the TXT later left hundreds of stale JSON copies
    that came back on every launch.  The current import set is now authoritative
    for text-sourced rows.
    """
    ensure_files()
    merged, seen = [], set()
    disabled = _load_disabled_keys()

    imported = parse_import_file()
    imported_keys = set()
    for raw in imported:
        profile = _normalize_profile(raw, "text")
        if profile:
            imported_keys.add(_profile_key(profile))

    saved_rows = _read_json_profiles()
    stale_text_found = False
    kept_saved = []
    for raw in saved_rows:
        profile = _normalize_profile(raw)
        if not profile:
            continue
        key = _profile_key(profile)
        if str(profile.get("source") or "").lower() == "text" and key not in imported_keys:
            stale_text_found = True
            continue
        kept_saved.append(profile)

    # One-time self-heal: compact profiles.json so stale imported copies are
    # physically gone, not merely hidden in memory.
    if stale_text_found:
        try:
            save_profiles(kept_saved)
        except Exception as exc:
            LOG.warning("Unable to compact stale text-import profiles: %s", exc)

    for raw in kept_saved + imported:
        profile = _normalize_profile(raw)
        if not profile:
            continue
        key = _profile_key(profile)
        if key in seen or key in disabled:
            continue
        seen.add(key)
        merged.append(profile)
    return merged



def save_profiles(items):
    with STATE_IO_LOCK:
        ensure_files()
        clean = []
        seen = set()
        for raw in items if isinstance(items, list) else []:
            profile = _normalize_profile(raw)
            if not profile:
                continue
            key = (profile["portal"].rstrip("/").lower(), profile["mac"])
            if key in seen:
                continue
            seen.add(key)
            clean.append(profile)
        temp = CONFIG_FILE + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(clean, handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, CONFIG_FILE)
            _fsync_parent_dir(CONFIG_FILE)
        finally:
            if os.path.exists(temp):
                try: os.unlink(temp)
                except OSError as exc: LOG.debug('profile temp cleanup failed: %s', exc)

def _profile_key(profile):
    normalized = _normalize_profile(profile)
    if not normalized:
        return None
    return (normalized["portal"].rstrip("/").lower(), normalized["mac"])

def _load_disabled_keys():
    data = load_json_file(DISABLED_FILE, list, [])
    result = set()
    for row in data if isinstance(data, list) else []:
        if isinstance(row, list) and len(row) == 2:
            result.add((str(row[0]), str(row[1])))
    return result


def _save_disabled_keys(keys):
    with STATE_IO_LOCK:
        ensure_files()
        temp = DISABLED_FILE + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(sorted([list(k) for k in keys]), handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, DISABLED_FILE)
            _fsync_parent_dir(DISABLED_FILE)
        finally:
            if os.path.exists(temp):
                try: os.unlink(temp)
                except OSError: pass

def disable_profiles(profiles):
    keys = _load_disabled_keys()
    for profile in profiles if isinstance(profiles, list) else []:
        key = _profile_key(profile)
        if key:
            keys.add(key)
    _save_disabled_keys(keys)

def enable_profile(profile):
    key = _profile_key(profile)
    if not key:
        return
    keys = _load_disabled_keys()
    if key in keys:
        keys.remove(key)
        _save_disabled_keys(keys)


def load_disabled_profiles():
    """Return known profiles currently disabled, including text-import entries."""
    disabled = _load_disabled_keys()
    result, seen = [], set()
    for raw in _read_json_profiles() + parse_import_file():
        profile = _normalize_profile(raw)
        if not profile:
            continue
        key = _profile_key(profile)
        if key in disabled and key not in seen:
            seen.add(key); result.append(profile)
    return result


def replace_profile(old_profile, new_profile, session=None):
    normalized = _normalize_profile(new_profile)
    if not normalized:
        raise ValueError("Invalid portal profile")
    old_key = _profile_key(old_profile)
    rows = load_profiles()
    replaced = False
    for index, row in enumerate(rows):
        if _profile_key(row) == old_key:
            rows[index] = normalized; replaced = True; break
    if not replaced:
        rows.append(normalized)
    if old_key and old_key != _profile_key(normalized):
        disable_profiles([old_profile])
    enable_profile(normalized)
    save_profiles(rows)
    try:
        from .core.session import PortalSession
        PortalSession.invalidate(old_profile)
        PortalSession.invalidate(normalized)
    except Exception as exc:
        LOG.warning("Profile session invalidation failed during identity edit: %s", exc)
    if old_key and old_key != _profile_key(normalized):
        try:
            from .core.bouquets import unexport_live_integration
            unexport_live_integration(old_profile, reload=True)
        except Exception as exc:
            LOG.warning("Old profile live integration cleanup failed: %s", exc)
        try:
            from .core.recording import purge_profile_jobs
            purge_profile_jobs(old_profile, session=session, remove_timers=bool(session))
        except Exception as exc:
            LOG.warning("Old profile recording cleanup failed: %s", exc)
    return normalized


def duplicate_profile(profile, name=None):
    normalized = _normalize_profile(profile)
    if not normalized:
        raise ValueError("Invalid portal profile")
    clone = dict(normalized)
    clone["name"] = (str(name or normalized.get("name") or "Portal") + " Copy")[:80]
    # Same portal+MAC cannot coexist by design; caller should change MAC when needed.
    return clone


def reorder_profile(profile, delta):
    rows = load_profiles(); key = _profile_key(profile)
    index = next((i for i, row in enumerate(rows) if _profile_key(row) == key), None)
    if index is None: return rows
    target = max(0, min(len(rows)-1, index + int(delta)))
    if target != index:
        rows[index], rows[target] = rows[target], rows[index]
        save_profiles(rows)
    return rows



def _remove_profile_from_import_path(profile, path):
    """Remove one portal/MAC pair from one import file safely."""
    key = _profile_key(profile)
    if not key or not path or not os.path.isfile(path):
        return False
    target_portal, target_mac = key
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return False

    current_portal = None
    output = []
    changed = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";", "//")):
            output.append(raw)
            if not stripped:
                current_portal = None
            continue

        portal = _extract_portal(stripped)
        macs = _extract_macs(stripped)
        if portal:
            try:
                normalized_portal = _normalize_profile(
                    {"portal": portal, "mac": target_mac, "source": "text"}, "text"
                )
                current_portal = _profile_key(normalized_portal)[0] if normalized_portal else None
            except Exception:
                current_portal = None

            # URL + MAC on the same line.
            if current_portal == target_portal and target_mac in macs:
                remaining = [m for m in macs if m != target_mac]
                if remaining:
                    # Preserve the portal and remaining MACs in an unambiguous form.
                    output.append(str(portal).rstrip("/") + "\n")
                    for mac in remaining:
                        output.append(mac + "\n")
                else:
                    # Keep a bare URL only if following grouped MACs may still belong to it.
                    output.append(str(portal).rstrip("/") + "\n")
                changed = True
                continue

            output.append(raw)
            continue

        # Grouped MAC line under a previously seen portal.
        mac_only = MAC_RE.match(stripped)
        if mac_only and current_portal == target_portal and _canonical_mac(mac_only.group(1)) == target_mac:
            changed = True
            continue
        output.append(raw)

    if not changed:
        return False

    temp = path + ".tmp.%d" % os.getpid()
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            handle.writelines(output)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        _fsync_parent_dir(path)
    finally:
        try:
            if os.path.exists(temp):
                os.unlink(temp)
        except OSError as exc:
            LOG.debug('import temp cleanup failed: %s', exc)
    return True


def _remove_profile_from_import(profile):
    """Remove a portal/MAC pair from all current and compatibility import files."""
    removed = False
    with STATE_IO_LOCK:
        for path in _import_paths():
            try:
                removed = _remove_profile_from_import_path(profile, path) or removed
            except Exception as exc:
                LOG.warning("Import cleanup failed for %s: %s", path, exc)
    return removed


def purge_profile_data(profile, session=None, remove_timers=True):
    """Remove plugin-owned data for a portal while keeping the portal profile itself."""
    cleanup = {}
    try:
        from .core.bouquets import unexport_live_integration
        cleanup["bouquet"] = unexport_live_integration(profile, reload=True)
    except Exception as exc:
        cleanup["bouquet_error"] = str(exc)
    try:
        from .core.recording import purge_profile_jobs
        cleanup["recordings"] = purge_profile_jobs(profile, session=session, remove_timers=bool(session and remove_timers))
    except Exception as exc:
        cleanup["recording_error"] = str(exc)
    try:
        from .repositories.database import DB
        cleanup["database"] = DB.purge_portal(profile)
    except Exception as exc:
        cleanup["database_error"] = str(exc)
    try:
        from .core.cache import CACHE
        CACHE.clear()  # persistent cache keys are SHA-256 and cannot be reversed safely per portal
        cleanup["cache_cleared"] = True
    except Exception as exc:
        cleanup["cache_error"] = str(exc)
    try:
        from .core.session import PortalSession
        cleanup["sessions_invalidated"] = PortalSession.invalidate(profile)
    except Exception as exc:
        cleanup["session_error"] = str(exc)
    return cleanup


def permanently_delete_profile(profile, session=None, purge_related=True):
    """Delete a portal and, by default, all plugin-owned data tied to its identity."""
    key = _profile_key(profile)
    if not key:
        return {"deleted": False, "cleanup": {}}
    saved = []
    for raw in _read_json_profiles():
        row = _normalize_profile(raw)
        if row and _profile_key(row) != key:
            saved.append(row)
    save_profiles(saved)
    _remove_profile_from_import(profile)
    disabled = _load_disabled_keys()
    if key in disabled:
        disabled.remove(key); _save_disabled_keys(disabled)
    cleanup = purge_profile_data(profile, session=session, remove_timers=True) if purge_related else {}
    if not purge_related:
        try:
            from .core.session import PortalSession
            PortalSession.invalidate(profile)
        except Exception as exc:
            LOG.warning('portal session invalidation failed during profile delete: %s', exc)
    return {"deleted": True, "cleanup": cleanup}


def load_api_keys():
    """Read external API credentials with mtime/size based caching."""
    global _API_KEYS_CACHE, _API_KEYS_SIGNATURE
    signature = _file_signature(API_KEYS_FILE)
    with _SETTINGS_CACHE_LOCK:
        if _API_KEYS_CACHE is not None and signature == _API_KEYS_SIGNATURE:
            return dict(_API_KEYS_CACHE)
    values = {}
    try:
        with open(API_KEYS_FILE, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith(("#", ";")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().upper()
                value = value.strip().strip('"').strip("'")
                if key and value:
                    values[key] = value[:4096]
    except FileNotFoundError:
        pass
    except OSError as exc:
        LOG.warning('API keys read failed: %s', exc)
    with _SETTINGS_CACHE_LOCK:
        _API_KEYS_CACHE = dict(values)
        _API_KEYS_SIGNATURE = signature
    return values

def update_api_keys(updates):
    """Atomically update selected keys in ``api_keys.conf`` without exposing secrets.

    Unknown keys/comments are preserved. Empty values intentionally clear a key.
    This is also the migration target for the legacy TMDB credential that older
    builds stored in ``settings.json``.
    """
    updates = {str(k or "").strip().upper(): str(v or "").strip() for k, v in (updates or {}).items()}
    updates = {k: v[:4096] for k, v in updates.items() if re.match(r"^[A-Z0-9_]{2,64}$", k)}
    if not updates:
        return load_api_keys()
    with STATE_IO_LOCK:
        ensure_files()
        try:
            with open(API_KEYS_FILE, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            lines = []
        seen = set()
        output = []
        for raw in lines:
            stripped = raw.strip()
            if stripped and not stripped.startswith(("#", ";")) and "=" in stripped:
                key = stripped.split("=", 1)[0].strip().upper()
                if key in updates:
                    output.append("%s=%s\n" % (key, updates[key]))
                    seen.add(key)
                    continue
            output.append(raw if raw.endswith("\n") else raw + "\n")
        if output and output[-1].strip():
            output.append("\n")
        for key in sorted(updates):
            if key not in seen:
                output.append("%s=%s\n" % (key, updates[key]))
        temp = API_KEYS_FILE + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                handle.writelines(output)
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, API_KEYS_FILE)
            _fsync_parent_dir(API_KEYS_FILE)
        finally:
            try:
                if os.path.exists(temp): os.unlink(temp)
            except OSError as exc:
                LOG.debug('API keys temp cleanup failed: %s', exc)
        _invalidate_api_keys_cache()
    return load_api_keys()


def save_tmdb_credential(value):
    """Persist TMDB credentials only in the private API-key file.

    Read tokens and v3 keys are mutually exclusive so a stale higher-priority
    token cannot shadow the credential the user just entered.
    """
    value = str(value or "").strip()[:4096]
    is_v3_key = bool(value and len(value) <= 64 and re.match(r"^[A-Za-z0-9_-]+$", value))
    updates = {
        "TMDB_API_KEY": value if is_v3_key else "",
        "TMDB_READ_TOKEN": "" if is_v3_key else value,
        # Clear the legacy alias too; otherwise an old TMDB_API_TOKEN entry
        # would still outrank the credential the user just saved.
        "TMDB_API_TOKEN": "",
    }
    update_api_keys(updates)
    return value


def _strip_legacy_tmdb_credential(raw_settings):
    """Remove the pre-Us31 TMDB token copy from settings.json atomically."""
    if not isinstance(raw_settings, dict) or "tmdb_credential" not in raw_settings:
        return False
    clean = dict(raw_settings)
    clean.pop("tmdb_credential", None)
    temp = SETTINGS_FILE + ".migrate.%d.%d" % (os.getpid(), threading.get_ident())
    with STATE_IO_LOCK:
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(clean, handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, SETTINGS_FILE)
            _fsync_parent_dir(SETTINGS_FILE)
            _invalidate_settings_cache()
            return True
        finally:
            try:
                if os.path.exists(temp): os.unlink(temp)
            except OSError:
                pass


def _load_settings_file_raw():
    """Return raw persisted settings; malformed files are preserved as .corrupt-* copies."""
    ensure_files()
    return load_json_file(SETTINGS_FILE, dict, {})

def load_settings():
    global _SETTINGS_CACHE, _SETTINGS_CACHE_SIGNATURE
    ensure_files()
    signature = (_file_signature(SETTINGS_FILE), _file_signature(API_KEYS_FILE))
    with _SETTINGS_CACHE_LOCK:
        if _SETTINGS_CACHE is not None and signature == _SETTINGS_CACHE_SIGNATURE:
            return copy.deepcopy(_SETTINGS_CACHE)
    data = load_json_file(SETTINGS_FILE, dict, {})
    legacy_tmdb = str(data.get("tmdb_credential") or "").strip()[:4096] if isinstance(data, dict) else ""
    result = dict(DEFAULT_SETTINGS)
    result.update(data)
    if result.get("theme") not in THEMES:
        result["theme"] = DEFAULT_SETTINGS["theme"]
    try:
        result["service_type"] = int(result.get("service_type", 4097))
    except (TypeError, ValueError):
        result["service_type"] = 4097
    if result["service_type"] not in (1, 4097, 5001, 5002, 8193):
        result["service_type"] = 4097
    try:
        result["timeout"] = max(3, min(30, int(result.get("timeout", 10))))
    except (TypeError, ValueError):
        result["timeout"] = 10
    for key, default, low, high in (("image_cache_mb",80,20,1024),("persistent_cache_mb",10240,512,65536),("content_page_size",50,20,250),("catchup_hours",72,12,720),("search_max_pages",250,5,250),("parental_session_minutes",30,0,240),("stream_retry_count",1,0,3),("proxy_port",17999,1024,65535),("epg_refresh_budget",45,10,180),("search_time_budget",12,5,30),("progress_save_seconds",10,3,30),("next_episode_countdown",10,5,30),("completion_threshold",93,80,99),("completion_remaining_seconds",180,30,900),("download_reserve_mb",1024,256,8192)):
        try: result[key] = max(low, min(high, int(result.get(key, default))))
        except (TypeError, ValueError): result[key] = default
    result["load_images"] = bool(result.get("load_images", True))
    for key in ("show_live", "show_movies", "show_series", "show_catchup", "live_preview", "parental_lock", "hide_adult", "diagnostic_logging", "first_run_wizard", "clean_titles", "show_quality_badges", "show_channel_numbers", "remember_location", "smart_engine", "hide_empty_categories", "prefetch_images", "show_watched", "smart_recovery", "multi_portal_search", "tmdb_enabled", "crash_safe_progress", "auto_remove_completed", "per_title_engine"):
        result[key] = bool(result.get(key, DEFAULT_SETTINGS.get(key, False)))
    # Playback engine is explicitly user-owned. Legacy Smart Engine/per-title
    # preferences are retained on disk for downgrade compatibility but are never
    # allowed to override service_type in this build.
    result["smart_engine"] = False
    result["per_title_engine"] = False
    for _retired in ("skip_intro_enabled","skip_intro_seconds","skip_credits_enabled","credits_tail_seconds"):
        result.pop(_retired,None)
    if result.get("channel_list_mode") not in ("compact", "epg", "large"):
        result["channel_list_mode"] = "epg"
    if result.get("parental_mode") not in ("pin", "hide"):
        result["parental_mode"] = "pin"
    if result.get("resume_behavior") not in ("ask", "always", "start"):
        result["resume_behavior"] = "ask"
    pin = str(result.get("parental_pin", "0000"))
    result["parental_pin"] = pin if (not pin or (pin.isdigit() and 4 <= len(pin) <= 8)) else "0000"
    pin_hash = str(result.get("parental_pin_hash") or "").strip().lower()
    pin_salt = str(result.get("parental_pin_salt") or "").strip().lower()
    if not (re.match(r"^[0-9a-f]{64}$", pin_hash or "") and re.match(r"^[0-9a-f]{32}$", pin_salt or "")):
        pin_hash, pin_salt = "", ""
    result["parental_pin_hash"] = pin_hash
    result["parental_pin_salt"] = pin_salt
    try:
        rounds = int(result.get("parental_pin_rounds") or (90000 if pin_hash else 210000))
    except (TypeError, ValueError):
        rounds = 90000 if pin_hash else 210000
    result["parental_pin_rounds"] = max(90000, min(1000000, rounds))
    # One-time migration for legacy non-default plaintext PINs. Keep the
    # factory 0000 value recognizable so the UI can continue warning users
    # that parental control still uses its default PIN.
    if not pin_hash and result["parental_pin"] and result["parental_pin"] != "0000":
        try:
            from .core.parental import hash_pin
            migrated = hash_pin(result["parental_pin"])
            result.update(migrated)
            save_settings(migrated)
        except Exception as exc:
            # Reading settings must never make the plugin unusable. A failed
            # migration simply leaves the legacy value in place for this run.
            LOG.warning("Legacy parental PIN migration failed: %s", exc)
    hidden = result.get("hidden_categories")
    if not isinstance(hidden, dict): hidden = {}
    result["hidden_categories"] = {k: [str(x) for x in hidden.get(k, []) if str(x).strip()] for k in ("itv", "vod", "series")}
    for setting_name in ("pinned_categories", "empty_categories", "protected_categories"):
        values = result.get(setting_name)
        if not isinstance(values, dict): values = {}
        result[setting_name] = {k: [str(x) for x in values.get(k, []) if str(x).strip()] for k in ("itv", "vod", "series")}
    words = result.get("adult_keywords")
    result["adult_keywords"] = [str(x).casefold() for x in words if str(x).strip()] if isinstance(words, list) else list(DEFAULT_SETTINGS["adult_keywords"])
    # Credentials live only in api_keys.conf from Us31 onward. Migrate any
    # legacy settings.json copy once, then immediately remove the plaintext copy.
    api = load_api_keys()
    external_tmdb = str(api.get("TMDB_READ_TOKEN") or api.get("TMDB_API_TOKEN") or api.get("TMDB_API_KEY") or "").strip()
    persisted_tmdb = bool(external_tmdb)
    source = "api_keys.conf" if external_tmdb else "none"
    if not external_tmdb and legacy_tmdb:
        try:
            save_tmdb_credential(legacy_tmdb)
            api = load_api_keys()
            external_tmdb = str(api.get("TMDB_READ_TOKEN") or api.get("TMDB_API_TOKEN") or api.get("TMDB_API_KEY") or "").strip()
            persisted_tmdb = bool(external_tmdb)
            source = "api_keys.conf" if external_tmdb else "none"
        except Exception:
            # Do not lose a working legacy credential if the filesystem is
            # temporarily read-only; it remains available for this process only.
            external_tmdb = legacy_tmdb
            source = "legacy-memory"
    # Remove the legacy plaintext copy only after the value is safely present
    # in api_keys.conf. A read-only filesystem therefore cannot destroy it.
    if legacy_tmdb and persisted_tmdb:
        try: _strip_legacy_tmdb_credential(data)
        except Exception as exc: LOG.warning("Could not remove migrated legacy TMDB credential from settings: %s", exc)
    result["remember_location"] = bool(result.get("remember_location", True))
    result["tmdb_credential"] = external_tmdb[:4096]
    result["tmdb_credential_source"] = source
    external_lang = str(api.get("TMDB_LANGUAGE") or "").strip()
    lang = external_lang or str(result.get("tmdb_language") or "ar-EG")
    result["tmdb_language"] = lang if lang in ("ar-EG", "en-US") else "ar-EG"
    result["imdb_api_key"] = str(api.get("IMDB_API_KEY") or "").strip()[:4096]
    result["imdb_api_endpoint"] = str(api.get("IMDB_API_ENDPOINT") or "").strip()[:1024]
    result["imdb_access_key_id"] = str(api.get("IMDB_ACCESS_KEY_ID") or "").strip()[:512]
    result["imdb_secret_access_key"] = str(api.get("IMDB_SECRET_ACCESS_KEY") or "").strip()[:4096]
    result["imdb_session_token"] = str(api.get("IMDB_SESSION_TOKEN") or "").strip()[:4096]
    result["imdb_region"] = str(api.get("IMDB_REGION") or "us-east-1").strip()[:64]
    result["imdb_dataset_id"] = str(api.get("IMDB_DATASET_ID") or "").strip()[:512]
    result["imdb_revision_id"] = str(api.get("IMDB_REVISION_ID") or "").strip()[:512]
    result["imdb_asset_id"] = str(api.get("IMDB_ASSET_ID") or "").strip()[:512]
    result["subdl_api_key"] = str(api.get("SUBDL_API_KEY") or "").strip()[:4096]
    result["api_keys_file"] = API_KEYS_FILE
    result["api_keys_file_exists"] = os.path.isfile(API_KEYS_FILE)
    signature = (_file_signature(SETTINGS_FILE), _file_signature(API_KEYS_FILE))
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE = copy.deepcopy(result)
        _SETTINGS_CACHE_SIGNATURE = signature
    return copy.deepcopy(result)


def save_settings(settings):
    with STATE_IO_LOCK:
        ensure_files()
        current = dict(DEFAULT_SETTINGS)
        current.update(_load_settings_file_raw())
        if isinstance(settings, dict):
            current.update(settings)
        temp = SETTINGS_FILE + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                for retired_key in ("animations","signature_overlay","smart_home","global_search","language"):
                    current.pop(retired_key, None)
                # Never mirror credentials loaded from api_keys.conf into settings.json.
                for secret_key in ("tmdb_credential","imdb_api_key","imdb_api_endpoint","imdb_access_key_id","imdb_secret_access_key","imdb_session_token","imdb_region","imdb_dataset_id","imdb_revision_id","imdb_asset_id","api_keys_file","api_keys_file_exists","tmdb_credential_source"):
                    current.pop(secret_key, None)
                json.dump(current, handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, SETTINGS_FILE)
            _fsync_parent_dir(SETTINGS_FILE)
            _invalidate_settings_cache()
        finally:
            if os.path.exists(temp):
                try: os.unlink(temp)
                except OSError as exc: LOG.debug('settings temp cleanup failed: %s', exc)

def load_theme(readonly=False):
    if readonly:
        # Import-safe theme lookup: preserve the user's visual choice without
        # creating, quarantining or chmod'ing any persistent config file.
        try:
            if os.path.getsize(SETTINGS_FILE) > 1024 * 1024:
                return "nova_fhd"
            with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
                data=json.load(handle)
            theme=str((data or {}).get("theme") or "nova_fhd")
            return theme if theme in THEMES else "nova_fhd"
        except (OSError, ValueError, TypeError):
            return "nova_fhd"
    return load_settings().get("theme", "nova_fhd")

def save_theme(theme):
    if theme not in THEMES:
        raise ValueError("Unsupported theme")
    save_settings({"theme": theme})



# Content persistence is centralized in SQLite repositories.
from .repositories.content import FAVORITES, HISTORY

def load_favorites():
    return FAVORITES.list()

def toggle_favorite(profile, media_type, item):
    return FAVORITES.toggle(profile, media_type, item)

def is_favorite(profile, media_type, item):
    return FAVORITES.contains(profile, media_type, item)

def load_content_states(profile, media_type, items):
    """Return favorite/resume state aligned with ``items`` using bulk SQLite reads."""
    return FAVORITES.states(profile, media_type, items)

def load_recently_played():
    return HISTORY.list()

def add_recently_played(profile, media_type, item, position=0, duration=0, completed=False, force=False):
    HISTORY.save(profile, media_type, item, position, duration, completed, force=force)

def touch_recently_played(profile, media_type, item):
    """Update Last Played ordering without changing saved resume position."""
    HISTORY.touch(profile, media_type, item)

def load_playback_progress(profile, media_type, item):
    return HISTORY.progress(profile, media_type, item)


def load_continue_watching():
    return HISTORY.continue_list()

def mark_watched(profile, media_type, item, watched=True):
    return HISTORY.mark_watched(profile, media_type, item, watched)

def remove_from_history(profile, media_type, item):
    return HISTORY.remove(profile, media_type, item)

def clear_history(profile=None):
    return HISTORY.clear(profile)
