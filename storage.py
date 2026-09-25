# -*- coding: utf-8 -*-
import copy
from . import _
import hashlib
from .language_catalog import installed_interface_language_codes, normalize_description_choice
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
LIBRARY_DIR = os.path.join(CONFIG_DIR, ".uslib")
PORTAL_LIBRARY_FILE = os.path.join(LIBRARY_DIR, ".catalog_a")
XTREAM_LIBRARY_FILE = os.path.join(LIBRARY_DIR, ".catalog_b")
THEMES = ("nova_fhd", "oled_black", "midnight_purple")
DEFAULT_SETTINGS = {"theme": "nova_fhd", "service_type": 4097, "timeout": 10, "load_images": True, "epg_hours": 4, "catchup_hours": 72, "search_max_pages": 250, "image_cache_mb": 128, "persistent_cache_mb": 20480, "content_page_size": 50, "parental_lock": False, "hide_adult": True, "diagnostic_logging": False, "first_run_wizard": False, "show_live": True, "show_movies": True, "show_series": True, "show_catchup": True, "live_preview": False, "parental_pin": "0000", "parental_pin_hash": "", "parental_pin_salt": "", "hidden_categories": {"itv": [], "vod": [], "series": []}, "hidden_categories_by_profile": {}, "protected_categories": {"itv": [], "vod": [], "series": []}, "adult_keywords": ["adult", "xxx", "18+", "porn"],
    "clean_titles": True, "show_quality_badges": True,
    "show_channel_numbers": True, "channel_list_mode": "epg", "remember_location": False,
    "smart_engine": False, "hide_empty_categories": True, "pinned_categories": {"itv": [], "vod": [], "series": []},
    "empty_categories": {"itv": [], "vod": [], "series": []}, "animations": "subtle", "prefetch_images": True,
    "show_watched": True, "parental_mode": "pin", "parental_session_minutes": 30, "download_reserve_mb": 1024,
    "smart_recovery": True, "stream_retry_count": 1, "multi_portal_search": True,
    "tmdb_enabled": True, "tmdb_credential": "", "tmdb_language": "ar-EG", "description_language": "ar-en",
    "proxy_port": 17999, "epg_refresh_budget": 45, "search_time_budget": 12,
    "resume_behavior": "always", "crash_safe_progress": True, "progress_save_seconds": 10,
    "next_episode_countdown": 10, "auto_remove_completed": True, "completion_threshold": 93, "completion_remaining_seconds": 180,
    "per_title_engine": True, "web_cleaner_access": "easy", "plugin_language": "en", "font_scale": "normal", "show_main_menu": True,
    "onboarding_v1_completed": False, "onboarding_v2_completed": False, "onboarding_final_v9_completed": False,
    "movies_view_mode": "cinematic", "series_view_mode": "cinematic"}

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
_SETTINGS_CACHE_CHECK_MONO = 0.0
_API_KEYS_CACHE_CHECK_MONO = 0.0
_FILES_ENSURED = False
_CACHE_STAT_TTL = 0.75
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
    global _SETTINGS_CACHE, _SETTINGS_CACHE_SIGNATURE, _SETTINGS_CACHE_CHECK_MONO
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE = None
        _SETTINGS_CACHE_SIGNATURE = None
        _SETTINGS_CACHE_CHECK_MONO = 0.0


def _invalidate_api_keys_cache():
    global _API_KEYS_CACHE, _API_KEYS_SIGNATURE, _API_KEYS_CACHE_CHECK_MONO
    with _SETTINGS_CACHE_LOCK:
        _API_KEYS_CACHE = None
        _API_KEYS_SIGNATURE = None
        _API_KEYS_CACHE_CHECK_MONO = 0.0
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



def ensure_files(force=False):
    """Ensure config files once per process; avoid rescanning them on every settings read."""
    global _FILES_ENSURED
    if _FILES_ENSURED and not force:
        return
    with STATE_IO_LOCK:
        if _FILES_ENSURED and not force:
            return
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
                    handle.write("TMDB_API_KEY=\nTMDB_READ_TOKEN=\nIMDB_API_KEY=\nIMDB_API_ENDPOINT=\nSUBDL_API_KEY=\nSUBSOURCE_API_KEY=\n")
                    handle.write("FANART_API_KEY=a13e8825394b61f42d0f34b0b2b90eb9\n")
                    handle.write("FANART_CLIENT_KEY=d9e41c82d5b199f6d3ead16fafc26d89\n")
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
                defaults = {
                    "FANART_API_KEY": "a13e8825394b61f42d0f34b0b2b90eb9",
                    "FANART_CLIENT_KEY": "d9e41c82d5b199f6d3ead16fafc26d89",
                }
                standard_keys=("TMDB_API_KEY","TMDB_READ_TOKEN","IMDB_API_KEY","IMDB_API_ENDPOINT","SUBDL_API_KEY","SUBSOURCE_API_KEY","FANART_API_KEY","FANART_CLIENT_KEY")
                missing = [k for k in standard_keys if k not in existing]
                empty_default_keys=set()
                for raw in current_lines:
                    stripped=raw.strip()
                    if stripped and not stripped.startswith(("#",";")) and "=" in stripped:
                        key,value=stripped.split("=",1);key=key.strip().upper();value=value.strip()
                        if key in defaults and not value:empty_default_keys.add(key)
                if missing or empty_default_keys:
                    temp = API_KEYS_FILE + ".migrate.%d.%d" % (os.getpid(), threading.get_ident())
                    with open(temp, "w", encoding="utf-8") as handle:
                        for raw in current_lines:
                            stripped=raw.strip()
                            if stripped and not stripped.startswith(("#",";")) and "=" in stripped:
                                key,value=stripped.split("=",1);key=key.strip().upper();value=value.strip()
                                if key in defaults and not value:
                                    handle.write("%s=%s\n" % (key,defaults[key]))
                                    continue
                            handle.write(raw if raw.endswith("\n") else raw+"\n")
                        if current_lines and current_lines[-1].strip():
                            handle.write("\n")
                        for key in missing:
                            handle.write("%s=%s\n" % (key, defaults.get(key,"")))
                        handle.flush(); os.fsync(handle.fileno())
                    os.chmod(temp, 0o600)
                    os.replace(temp, API_KEYS_FILE)
                    _fsync_parent_dir(API_KEYS_FILE)
                    _invalidate_api_keys_cache()
            except OSError as exc:
                LOG.warning('API keys migration failed: %s', exc)
        _FILES_ENSURED = bool(os.path.isdir(CONFIG_DIR) and os.path.isfile(IMPORT_FILE) and os.path.isfile(API_KEYS_FILE))


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
    # Optional provider-reported connection telemetry. Purely descriptive.
    for field in ("active_connections", "max_connections"):
        try:
            value = int(profile.get(field))
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 999:
            result[field] = value
    connection_source = str(profile.get("connections_source") or "").strip().lower()
    if connection_source == "provider_exact":
        result["connections_source"] = connection_source

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



def _library_file(kind):
    return XTREAM_LIBRARY_FILE if str(kind or "").lower() == "xtream" else PORTAL_LIBRARY_FILE

def ensure_server_library_files():
    """Create passive local catalog files. They are never scanned during normal startup."""
    ensure_files()
    try:
        os.makedirs(LIBRARY_DIR, mode=0o700, exist_ok=True)
        os.chmod(LIBRARY_DIR, 0o700)
    except OSError as exc:
        LOG.debug("server library directory init failed: %s", exc)
    for kind, path in (("portal", PORTAL_LIBRARY_FILE), ("xtream", XTREAM_LIBRARY_FILE)):
        if os.path.exists(path):
            continue
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# Ultra Stalker %s catalog\n" % kind)
                handle.write("# Passive text catalog: no validation or network work occurs until imported.\n")
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(path, 0o600)
        except OSError as exc:
            LOG.warning("server library file init failed: %s", exc)

def _library_display_name(profile, index):
    name=str((profile or {}).get("name") or "").strip()
    if name:
        return name[:80]
    raw=str((profile or {}).get("portal") or "")
    try:
        parsed=urllib.parse.urlsplit(raw)
        host=parsed.hostname or parsed.netloc
        if host:
            return str(host)[:80]
    except Exception:
        pass
    return ((_("Xtream Server %d") if str((profile or {}).get("source_type") or "").lower()=="m3u" else _("Portal Server %d")) % (int(index)+1))

def load_server_library(kind):
    """Parse one passive catalog on demand, without network or profile initialization."""
    kind=str(kind or "").lower()
    if kind not in ("portal", "xtream"):
        return []
    ensure_server_library_files()
    path=_library_file(kind)
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            lines=handle.readlines()
    except OSError:
        return []
    rows=[]; seen=set()
    # Reuse the mature import parser first. It is text-only and performs no I/O beyond parsing.
    parsed=[]
    try:
        _parse_import_lines(lines, parsed, set())
    except Exception as exc:
        LOG.warning("server library parse failed: %s", exc)
        parsed=[]
    # Preserve optional display names on common pipe-separated Portal rows.
    if kind == "portal":
        named = {}
        for raw in lines:
            line=str(raw or "").strip()
            if not line or line.startswith(("#",";","//")) or "|" not in line:
                continue
            parts=[x.strip().strip('"\'') for x in line.split("|")]
            url_idx=next((i for i,x in enumerate(parts) if x.lower().startswith(("http://","https://"))),None)
            mac_idx=next((i for i,x in enumerate(parts) if MAC_RE.match(x)),None)
            if url_idx is None or mac_idx is None:
                continue
            candidate=_normalize_profile({"portal":parts[url_idx],"mac":parts[mac_idx],"source":"library","source_type":"stalker"},"library")
            if candidate and url_idx>0 and parts[0]:
                named[_profile_key(candidate)]=parts[0][:80]
        for item in parsed:
            key=_profile_key(item)
            if key in named:item["name"]=named[key]

    # Supplement common single-line Xtream forms: URL|USER|PASS and NAME|URL|USER|PASS.
    if kind == "xtream":
        for raw in lines:
            line=str(raw or "").strip()
            if not line or line.startswith(("#",";","//")) or "|" not in line:
                continue
            parts=[x.strip().strip('"\'') for x in line.split("|")]
            url_idx=next((i for i,x in enumerate(parts) if x.lower().startswith(("http://","https://"))),None)
            if url_idx is None or len(parts) < url_idx+3:
                continue
            url,user,pwd=parts[url_idx],parts[url_idx+1],parts[url_idx+2]
            full=_xtream_playlist_url(url,user,pwd)
            if full:
                p=_normalize_profile({"portal":full,"source":"library","source_type":"m3u","name":parts[0] if url_idx>0 else ""},"library")
                if p: parsed.append(p)
    for profile in parsed:
        p=_normalize_profile(profile, "library")
        if not p:
            continue
        st=str(p.get("source_type") or "stalker").lower()
        if kind == "portal" and st != "stalker":
            continue
        if kind == "xtream" and st != "m3u":
            continue
        key=_profile_key(p)
        if not key or key in seen:
            continue
        seen.add(key)
        p=dict(p); p["source"]="library"; p["name"]=_library_display_name(p,len(rows))
        rows.append(p)
    return rows

def import_server_library_profiles(items):
    """Copy selected catalog rows into normal saved profiles; no network checks are performed."""
    selected=[]
    for raw in items if isinstance(items,list) else []:
        p=_normalize_profile(raw,"library")
        if p:
            p=dict(p); p["source"]="library"; selected.append(p)
    if not selected:
        return {"added":0,"duplicates":0,"total":0}
    existing=load_profiles()
    existing_keys=set(k for k in (_profile_key(x) for x in existing) if k)
    saved=_read_json_profiles()
    added=0; dup=0
    for p in selected:
        key=_profile_key(p)
        if not key or key in existing_keys:
            dup+=1; continue
        existing_keys.add(key); saved.append(p); added+=1
    if added:
        save_profiles(saved)
    return {"added":added,"duplicates":dup,"total":len(selected)}

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
    """Remove one source identity from a text import file, including annotated MAC rows.

    Historical portal lists often store rows as ``MAC - expiry`` rather than a
    bare MAC.  The former deleter only matched a line containing *only* the MAC,
    so the entry was removed from profiles.json and immediately resurrected by
    load_profiles().  Treat any MAC found inside the active portal block as the
    same identity.  Standalone M3U/get.php URLs are removed by URL identity.
    """
    normalized = _normalize_profile(profile)
    key = _profile_key(normalized)
    if not normalized or not key or not path or not os.path.isfile(path):
        return False
    target_portal, target_mac = key
    target_is_m3u = str(normalized.get("source_type") or "").lower() == "m3u"
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
        if not stripped:
            output.append(raw)
            current_portal = None
            continue
        if stripped.startswith(("#", ";", "//")):
            output.append(raw)
            continue

        portal = _extract_portal(stripped)
        macs = _extract_macs(stripped)
        if portal:
            try:
                candidate = _normalize_profile({
                    "portal": portal,
                    "mac": "" if target_is_m3u else target_mac,
                    "source": "text",
                    "source_type": "m3u" if target_is_m3u else "stalker",
                }, "text")
                current_portal = _profile_key(candidate)[0] if candidate else None
            except Exception:
                current_portal = None

            # Standalone M3U/Xtream source: deleting the URL line deletes the
            # imported source itself, not merely its saved JSON mirror.
            if target_is_m3u and current_portal == target_portal:
                changed = True
                current_portal = None
                continue

            # Portal + MAC on one line.  Keep any other MACs, but remove the
            # selected identity regardless of trailing expiry/comment text.
            if (not target_is_m3u and current_portal == target_portal and
                    target_mac in macs):
                remaining = [m for m in macs if m != target_mac]
                output.append(str(portal).rstrip("/") + "\n")
                for mac in remaining:
                    output.append(mac + "\n")
                changed = True
                continue

            output.append(raw)
            continue

        # Grouped MAC rows may be ``MAC``, ``MAC - expiry``, ``MAC | note`` etc.
        # Match the MAC token, not the entire line.
        if not target_is_m3u and current_portal == target_portal and target_mac in macs:
            remaining = [m for m in macs if m != target_mac]
            for mac in remaining:
                output.append(mac + "\n")
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
    """Read API credentials with a short stat debounce plus mtime/size validation."""
    global _API_KEYS_CACHE, _API_KEYS_SIGNATURE, _API_KEYS_CACHE_CHECK_MONO
    now = time.monotonic()
    with _SETTINGS_CACHE_LOCK:
        if _API_KEYS_CACHE is not None and (now - _API_KEYS_CACHE_CHECK_MONO) < _CACHE_STAT_TTL:
            return dict(_API_KEYS_CACHE)
    signature = _file_signature(API_KEYS_FILE)
    with _SETTINGS_CACHE_LOCK:
        _API_KEYS_CACHE_CHECK_MONO = now
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
    # R269: restore the bundled device/provider credentials introduced in R253.
    # Explicit user values in api_keys.conf always win and bundled values are
    # never written back to disk. TMDb is one credential family: if any user
    # TMDb token/key is present, do not inject a bundled sibling.
    try:
        from .builtin_credentials import builtin_api_credentials
        bundled = builtin_api_credentials() or {}
    except Exception as exc:
        LOG.debug("bundled API credential decode failed: %s", exc)
        bundled = {}
    if not any(str(values.get(k) or "").strip() for k in ("TMDB_READ_TOKEN", "TMDB_API_TOKEN", "TMDB_API_KEY")):
        tmdb_default = str(bundled.get("TMDB_API_KEY") or "").strip()
        if tmdb_default:
            values["TMDB_API_KEY"] = tmdb_default
    for key in ("SUBDL_API_KEY", "SUBSOURCE_API_KEY"):
        if not str(values.get(key) or "").strip():
            default_value = str(bundled.get(key) or "").strip()
            if default_value:
                values[key] = default_value

    # Existing Fanart defaults keep their historical behavior.
    values.setdefault("FANART_API_KEY", "a13e8825394b61f42d0f34b0b2b90eb9")
    values.setdefault("FANART_CLIENT_KEY", "d9e41c82d5b199f6d3ead16fafc26d89")
    with _SETTINGS_CACHE_LOCK:
        _API_KEYS_CACHE = dict(values)
        _API_KEYS_SIGNATURE = signature
        _API_KEYS_CACHE_CHECK_MONO = time.monotonic()
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
    # R113: credential activation atomically detaches the temporary provider
    # bootstrap cache.  Physical deletion is daemonized, so saving Settings never
    # blocks on a large artwork tree.
    try:
        from .provider_bootstrap import reconcile_tmdb_state
        reconcile_tmdb_state(force=True)
    except Exception as exc:
        LOG.warning("Provider bootstrap credential transition failed: %s", exc)
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
    global _SETTINGS_CACHE, _SETTINGS_CACHE_SIGNATURE, _SETTINGS_CACHE_CHECK_MONO
    ensure_files()
    now = time.monotonic()
    with _SETTINGS_CACHE_LOCK:
        if _SETTINGS_CACHE is not None and (now - _SETTINGS_CACHE_CHECK_MONO) < _CACHE_STAT_TTL:
            return copy.deepcopy(_SETTINGS_CACHE)
    signature = (_file_signature(SETTINGS_FILE), _file_signature(API_KEYS_FILE))
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE_CHECK_MONO = now
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
    for key in ("show_live", "show_movies", "show_series", "show_catchup", "live_preview", "parental_lock", "hide_adult", "diagnostic_logging", "first_run_wizard", "clean_titles", "show_quality_badges", "show_channel_numbers", "remember_location", "smart_engine", "hide_empty_categories", "prefetch_images", "show_watched", "smart_recovery", "multi_portal_search", "tmdb_enabled", "crash_safe_progress", "auto_remove_completed", "per_title_engine", "show_main_menu", "onboarding_v1_completed", "onboarding_v2_completed", "onboarding_final_v9_completed"):
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
    access_mode = str(result.get("web_cleaner_access") or "easy").strip().lower()
    result["web_cleaner_access"] = access_mode if access_mode in ("easy", "protected") else "easy"
    plugin_language = str(result.get("plugin_language") or "en").strip().lower()
    result["plugin_language"] = plugin_language if plugin_language in installed_interface_language_codes() else "en"
    # Description language is independent from Interface Language and from the
    # long-standing internal TMDb metadata mode used by artwork/title-logo.
    # Migrate the temporary R140 "current" value to the equivalent explicit
    # choice without changing that internal metadata mode.
    _description_choice = normalize_description_choice(result.get("description_language"))
    if _description_choice == "current":
        _description_choice = "en-US" if str(result.get("tmdb_language") or "ar-EG") == "en-US" else "ar-en"
    result["description_language"] = _description_choice
    font_scale = str(result.get("font_scale") or "normal").strip().lower()
    result["font_scale"] = font_scale if font_scale in ("normal", "large", "larger", "xlarge") else "normal"
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
    scoped_hidden = result.get("hidden_categories_by_profile")
    if not isinstance(scoped_hidden, dict): scoped_hidden = {}
    cleaned_scoped = {}
    for profile_key, bucket in scoped_hidden.items():
        if not isinstance(bucket, dict): continue
        clean_bucket = {}
        for media_key in ("itv", "vod", "series"):
            if media_key in bucket:
                clean_bucket[media_key] = [str(x) for x in bucket.get(media_key, []) if str(x).strip()] if isinstance(bucket.get(media_key), list) else []
        if clean_bucket: cleaned_scoped[str(profile_key)] = clean_bucket
    result["hidden_categories_by_profile"] = cleaned_scoped
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
    result["subsource_api_key"] = str(api.get("SUBSOURCE_API_KEY") or "").strip()[:4096]
    result["fanart_api_key"] = str(api.get("FANART_API_KEY") or "").strip()[:4096]
    result["api_keys_file"] = API_KEYS_FILE
    result["api_keys_file_exists"] = os.path.isfile(API_KEYS_FILE)
    signature = (_file_signature(SETTINGS_FILE), _file_signature(API_KEYS_FILE))
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE = copy.deepcopy(result)
        _SETTINGS_CACHE_SIGNATURE = signature
        _SETTINGS_CACHE_CHECK_MONO = time.monotonic()
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
# PERF40: keep sqlite3/repository initialization off the cold ui.py import path.
# Home/Settings/Portal browsing do not need the database until a favorite/history
# API is actually used.  The public storage API remains unchanged.
_CONTENT_REPOSITORIES = None
_CONTENT_REPOSITORIES_LOCK = threading.RLock()
# In-process history revision. Home uses this as a zero-I/O dirty token so
# Recent cards stay fully resident across child screens and rebuild only after
# playback/history actually changes. It intentionally resets on Enigma restart;
# cold Home still loads SQLite once as before.
_HISTORY_REVISION = 0
_HISTORY_REVISION_LOCK = threading.RLock()

def _bump_history_revision():
    global _HISTORY_REVISION
    with _HISTORY_REVISION_LOCK:
        _HISTORY_REVISION += 1
        return _HISTORY_REVISION

def history_revision():
    with _HISTORY_REVISION_LOCK:
        return int(_HISTORY_REVISION)

def _content_repositories():
    global _CONTENT_REPOSITORIES
    repos = _CONTENT_REPOSITORIES
    if repos is not None:
        return repos
    with _CONTENT_REPOSITORIES_LOCK:
        repos = _CONTENT_REPOSITORIES
        if repos is None:
            from .repositories.content import FAVORITES, HISTORY
            repos = (FAVORITES, HISTORY)
            _CONTENT_REPOSITORIES = repos
        return repos

def load_favorites():
    return _content_repositories()[0].list()

def toggle_favorite(profile, media_type, item):
    return _content_repositories()[0].toggle(profile, media_type, item)

def is_favorite(profile, media_type, item):
    return _content_repositories()[0].contains(profile, media_type, item)

def load_content_states(profile, media_type, items):
    """Return favorite/resume state aligned with ``items`` using bulk SQLite reads."""
    return _content_repositories()[0].states(profile, media_type, items)

def load_recently_played():
    return _content_repositories()[1].list()

def add_recently_played(profile, media_type, item, position=0, duration=0, completed=False, force=False):
    result = _content_repositories()[1].save(profile, media_type, item, position, duration, completed, force=force)
    _bump_history_revision()
    return result

def touch_recently_played(profile, media_type, item):
    """Update Last Played ordering without changing saved resume position."""
    result = _content_repositories()[1].touch(profile, media_type, item)
    _bump_history_revision()
    return result

def load_playback_progress(profile, media_type, item):
    return _content_repositories()[1].progress(profile, media_type, item)


def load_continue_watching():
    return _content_repositories()[1].continue_list()

def mark_watched(profile, media_type, item, watched=True):
    result = _content_repositories()[1].mark_watched(profile, media_type, item, watched)
    _bump_history_revision()
    return result

def remove_from_history(profile, media_type, item):
    result = _content_repositories()[1].remove(profile, media_type, item)
    _bump_history_revision()
    return result

def clear_history(profile=None):
    result = _content_repositories()[1].clear(profile)
    _bump_history_revision()
    return result
