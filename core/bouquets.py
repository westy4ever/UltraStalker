# -*- coding: utf-8 -*-
"""Enigma2 bouquet and EPGImport integration with dynamic Stalker links.

Exported bouquets never persist short-lived provider URLs.  They point to a
loopback-only proxy which resolves a fresh Stalker link at tune time.  The
registry also owns the exported files, so an export can be removed cleanly.
"""
from __future__ import absolute_import

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import threading
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

from ..client import StalkerClient
from ..title_clean import clean_title, catalogue_title
from ..storage import CONFIG_DIR, load_settings, load_json_file, _fsync_parent_dir
from ..log import get_logger
from .recording import event_times

REGISTRY_FILE = os.path.join(CONFIG_DIR, "bouquet_registry.json")
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 17999  # Backwards-compatible default; persisted exports own their actual port.
PROXY_PORT_FALLBACK_END = 18019
EPGIMPORT_DIR = "/etc/epgimport"
ENIGMA2_DIR = "/etc/enigma2"
_XMLTV_TTL = 2 * 3600
_XMLTV_BACKGROUND_AFTER = 45 * 60
_XMLTV_DEFAULT_BUDGET = 45
_SERVER = None
_SERVER_THREAD = None
_SERVER_PORT = None
_REGISTRY_LOCK = threading.RLock()
_XMLTV_LOCKS = {}
_XMLTV_REFRESHING = set()
_XMLTV_REFRESH_LOCK = threading.RLock()
_EPG_MANAGER_THREAD = None
_EPG_MANAGER_STOP = threading.Event()
_LAST_PROXY_ERROR = ""
_XMLTV_PAUSED = threading.Event()
_XMLTV_ACTIVE = {}
_XMLTV_ORPHANS = {}
_XMLTV_ORPHAN_MAX_THREADS = 12
_PROXY_REQUEST_SLOTS = threading.BoundedSemaphore(8)
_PROXY_SOCKET_TIMEOUT = 12.0
LOG = get_logger()
_PICON_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ultrastalker-export-picon")


def _profile_key(profile):
    raw = "%s|%s" % (
        str((profile or {}).get("portal") or "").rstrip("/").lower(),
        str((profile or {}).get("mac") or "").upper(),
    )
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def _channel_key(channel):
    raw = "%s|%s" % (
        str((channel or {}).get("id") or (channel or {}).get("ch_id") or ""),
        str((channel or {}).get("cmd") or (channel or {}).get("command") or (channel or {}).get("url") or ""),
    )
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def _read_registry():
    return load_json_file(REGISTRY_FILE, dict, {})


def _write_registry(data):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix="bouquet_registry.", suffix=".tmp", dir=CONFIG_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush(); os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, REGISTRY_FILE); _fsync_parent_dir(REGISTRY_FILE); temp = None
    finally:
        if temp:
            try: os.unlink(temp)
            except OSError: pass


def _registry_entries(registry):
    return {
        key: value for key, value in (registry or {}).items()
        if key != "_meta" and isinstance(value, dict) and isinstance(value.get("channels"), dict)
    }


def _setting_port():
    try:
        value = int(load_settings().get("proxy_port", PROXY_PORT) or PROXY_PORT)
    except (TypeError, ValueError):
        value = PROXY_PORT
    return value if 1024 <= value <= 65535 else PROXY_PORT


def _registry_port(registry=None):
    registry = registry if isinstance(registry, dict) else _read_registry()
    meta = registry.get("_meta") if isinstance(registry.get("_meta"), dict) else {}
    try:
        value = int(meta.get("proxy_port") or 0)
        if 1024 <= value <= 65535:
            return value
    except (TypeError, ValueError):
        pass
    for entry in _registry_entries(registry).values():
        try:
            value = int(entry.get("proxy_port") or 0)
            if 1024 <= value <= 65535:
                return value
        except (TypeError, ValueError):
            pass
    return _setting_port()


def _port_available(port):
    if _SERVER is not None and _SERVER_PORT == int(port):
        return True
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((PROXY_HOST, int(port)))
        return True
    except OSError:
        return False
    finally:
        try: sock.close()
        except OSError: pass


def _choose_export_port(registry):
    entries = _registry_entries(registry)
    if entries:
        port = _registry_port(registry)
        if not _port_available(port):
            raise RuntimeError(
                "Dynamic bouquet proxy port %d is already in use. Existing Stalker bouquets use this port; "
                "stop the conflicting service or remove/re-export the Stalker bouquets." % port
            )
        return port
    preferred = _setting_port()
    candidates = [preferred]
    for port in range(PROXY_PORT, PROXY_PORT_FALLBACK_END + 1):
        if port not in candidates:
            candidates.append(port)
    for port in candidates:
        if _port_available(port):
            return port
    raise RuntimeError("No free loopback port is available for the dynamic Stalker bouquet proxy")


def _client(profile):
    cfg = load_settings()
    profile = profile if isinstance(profile, dict) else {}
    portal = str(profile.get("portal") or "")
    source_type = str(profile.get("source_type") or "").strip().lower()
    low = portal.lower()
    if not source_type:
        source_type = "m3u" if (".m3u" in low or "type=m3u" in low or "output=m3u" in low or ("get.php" in low and ("username=" in low or "password=" in low))) else "stalker"
    if source_type == "m3u":
        from ..m3u_adapter import M3UClient
        return M3UClient(portal, profile.get("mac") or "M3U", timeout=cfg.get("timeout", 10))
    return StalkerClient(
        portal, profile.get("mac"), timeout=cfg.get("timeout", 10),
        allow_http_fallback=profile.get("allow_http_fallback", False),
        tls_mode=profile.get("tls_mode", "auto"),
        device_profile=profile.get("device_profile", "auto"),
        allow_tls_fallback=profile.get("tls_fallback_accepted", False),
        http_fallback_accepted=profile.get("http_fallback_accepted", False),
    )


def _xmltv_id(profile_key, channel_key):
    return "spobh.%s.%s" % (profile_key, channel_key)


def _new_proxy_token():
    # Capability token for loopback proxy URLs.  24 random bytes gives ample
    # entropy while keeping exported service references reasonably short.
    return secrets.token_urlsafe(24)


def _proxy_url(profile_key, channel_key, port=None, token=None):
    base = "http://%s:%d/stream" % (PROXY_HOST, int(port or _registry_port()))
    if token:
        return "%s/%s/%s/%s" % (base, token, profile_key, channel_key)
    # Legacy registry entries created before capability tokens existed.
    return "%s/%s/%s" % (base, profile_key, channel_key)


def _xmltv_url(profile_key, port=None, token=None):
    base = "http://%s:%d/xmltv" % (PROXY_HOST, int(port or _registry_port()))
    if token:
        return "%s/%s/%s" % (base, token, profile_key)
    return "%s/%s" % (base, profile_key)


def _is_loopback_address(value):
    try:
        return ipaddress.ip_address(str(value or "").split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _assert_loopback_bind():
    # Keep this explicit guard even though PROXY_HOST is currently a constant.
    # It prevents a future configuration/refactor from accidentally exposing
    # an unaudited proxy listener to the LAN/WAN.
    if not _is_loopback_address(PROXY_HOST):
        raise RuntimeError("Refusing to start bouquet proxy on non-loopback host %r" % (PROXY_HOST,))


def _token_matches(entry, supplied):
    expected = str((entry or {}).get("proxy_token") or "")
    if not expected or supplied is None:
        return False
    return hmac.compare_digest(expected, str(supplied))


def _replace_required(path, replacements):
    """Atomically replace legacy proxy references in an existing export.

    Missing generated files are harmless (there is no consumer to migrate), but
    an existing file must contain every expected legacy reference.  Refusing a
    partial rewrite prevents publishing a token that would strand an old bouquet.
    """
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    changed = text
    for old, new in replacements:
        if old not in changed:
            raise ValueError("legacy proxy reference not found in %s" % path)
        changed = changed.replace(old, new)
    if changed != text:
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            mode = 0o644
        _atomic_text(path, changed, mode)
        return True
    return False


def _migrate_legacy_proxy_tokens(registry=None):
    """Upgrade tokenless bouquet exports atomically before the proxy serves them.

    Registry publication is the commit point.  Existing bouquet/EPGImport files
    are rewritten first and rolled back on any failure, so a legacy export never
    becomes unusable halfway through migration.
    """
    with _REGISTRY_LOCK:
        original = registry if isinstance(registry, dict) else _read_registry()
        legacy = [(key, value) for key, value in _registry_entries(original).items()
                  if not str(value.get("proxy_token") or "")]
        if not legacy:
            return original
        working = dict(original)
        touched = {}
        try:
            for pkey, old_entry in legacy:
                entry = dict(old_entry)
                token = _new_proxy_token()
                port = int(entry.get("proxy_port") or _registry_port(original))
                service_type = int(entry.get("service_type") or 4097)
                paths = _bouquet_paths(pkey)
                channel_replacements = []
                for ckey in (entry.get("channels") or {}):
                    old_ref = _service_ref(_proxy_url(pkey, ckey, port, None), service_type)
                    new_ref = _service_ref(_proxy_url(pkey, ckey, port, token), service_type)
                    channel_replacements.append((old_ref, new_ref))
                file_jobs = [
                    (paths["bouquet"], channel_replacements),
                    (paths["epg_channels"], channel_replacements),
                    (paths["epg_source"], [(_xmltv_url(pkey, port, None), _xmltv_url(pkey, port, token))]),
                ]
                for path, replacements in file_jobs:
                    if path not in touched:
                        touched[path] = _snapshot_file(path)
                    _replace_required(path, replacements)
                entry["proxy_token"] = token
                entry["updated_at"] = int(time.time())
                working[pkey] = entry
            meta = dict(working.get("_meta") or {})
            meta.update({"format": 3, "proxy_port": _registry_port(working), "updated_at": int(time.time())})
            working["_meta"] = meta
            _write_registry(working)
            return working
        except Exception:
            for path, snapshot in reversed(list(touched.items())):
                _restore_snapshot(path, snapshot)
            raise


def _service_ref(url, service_type=4097, identity=None):
    encoded = urllib.parse.quote(str(url or ""), safe="")
    if identity not in (None, ""):
        digest = hashlib.sha1(str(identity).encode("utf-8", "ignore")).hexdigest().upper()
        sid, tsid, onid = digest[:4], digest[4:8], digest[8:12]
        return "%d:0:1:%s:%s:%s:0:0:0:0:%s" % (int(service_type or 4097), sid, tsid, onid, encoded)
    return "%d:0:1:0:0:0:0:0:0:0:%s" % (int(service_type or 4097), encoded)


def _safe_name(value):
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:180]


def _receiver_name(row, media_type="itv"):
    # When the browser supplies _receiver_name it is the exact final display
    # title already shown inside Ultra Stalker. Do not run a second, different
    # cleanup pass during receiver export.
    explicit = (row or {}).get("_receiver_name")
    if explicit not in (None, ""):
        return _safe_name(explicit)
    raw = (row or {}).get("name") or (row or {}).get("title") or "Item"
    if str(media_type or "itv").lower() in ("vod", "movie", "series"):
        return _safe_name(catalogue_title(raw))
    if str(media_type or "itv").lower() == "episode":
        return _safe_name(raw)
    return _safe_name(clean_title(raw))


def _picon_root():
    for root in ("/media/hdd/picon", "/media/usb/picon", "/usr/share/enigma2/picon"):
        parent = os.path.dirname(root)
        if os.path.isdir(parent) and os.access(parent, os.W_OK):
            try:
                os.makedirs(root, exist_ok=True)
                return root
            except OSError:
                pass
    return None


def _picon_filename(service_ref):
    parts = str(service_ref or "").split(":")[:10]
    if len(parts) < 10:
        return ""
    return "_".join(parts) + ".png"


def _publish_picon(service_ref, source_path):
    source = str(source_path or "")
    if not source or not os.path.isfile(source):
        return ""
    root = _picon_root()
    name = _picon_filename(service_ref)
    if not root or not name:
        return ""
    target = os.path.join(root, name)
    try:
        if source.lower().endswith(".png"):
            shutil.copyfile(source, target)
        else:
            try:
                from PIL import Image
                with Image.open(source) as image:
                    image.convert("RGBA").save(target, "PNG", optimize=True)
            except Exception:
                return ""
        return target if os.path.isfile(target) else ""
    except Exception as exc:
        LOG.debug("Receiver picon publish failed: %s", exc)
        return ""


def _queue_picon_publish(service_ref, source_path):
    """Publish cached artwork after bouquet commit so VOD/Series export never waits on image conversion."""
    source = str(source_path or "")
    if not source or not os.path.isfile(source):
        return False
    try:
        _PICON_EXPORT_EXECUTOR.submit(_publish_picon, service_ref, source)
        return True
    except Exception as exc:
        LOG.debug("Receiver picon queue failed: %s", exc)
        return False


def _atomic_text(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush(); os.fsync(handle.fileno())
        try: os.chmod(temp, mode)
        except OSError: pass
        os.replace(temp, path)
        _fsync_parent_dir(path)
    finally:
        try:
            if os.path.exists(temp): os.unlink(temp)
        except OSError:
            pass


def _bouquet_paths(profile_key):
    bouquet_name = "userbouquet.ultrastalker_%s.tv" % profile_key
    return {
        "bouquet_name": bouquet_name,
        "bouquet": os.path.join(ENIGMA2_DIR, bouquet_name),
        "epg_channels": os.path.join(EPGIMPORT_DIR, "ultrastalker_%s.channels.xml" % profile_key),
        "epg_source": os.path.join(EPGIMPORT_DIR, "ultrastalker_%s.sources.xml" % profile_key),
        "xmltv_cache": _xmltv_cache_path(profile_key),
    }



def _snapshot_file(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        try: mode = os.stat(path).st_mode & 0o777
        except OSError: mode = 0o644
        return True, data, mode
    except OSError:
        return False, b"", 0o644


def _restore_snapshot(path, snapshot):
    existed, data, mode = snapshot
    try:
        if existed:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            temp = path + ".rollback.%d.%d" % (os.getpid(), threading.get_ident())
            with open(temp, "wb") as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            try: os.chmod(temp, mode)
            except OSError: pass
            os.replace(temp, path)
            _fsync_parent_dir(path)
        else:
            try:
                os.unlink(path)
                _fsync_parent_dir(path)
            except OSError:
                pass
    except Exception as exc:
        LOG.warning("Bouquet rollback failed path=%s: %s", path, exc)


def export_live_integration(profile, channels, service_type=4097):
    """Atomically export dynamic bouquet + EPGImport state.

    Registry publication is the final commit point. If any file write fails,
    every touched file is restored so Enigma2 never sees a half-exported portal.
    """
    profile = dict(profile or {})
    rows = [dict(row) for row in (channels or []) if isinstance(row, dict) and (row.get("cmd") or row.get("command") or row.get("url"))]
    if not rows:
        raise ValueError("Portal returned no exportable live channels")
    pkey = _profile_key(profile)
    paths = _bouquet_paths(pkey)
    bouquet_index = os.path.join(ENIGMA2_DIR, "bouquets.tv")
    touched = [paths["bouquet"], bouquet_index, paths["epg_channels"], paths["epg_source"], REGISTRY_FILE]
    snapshots = {path: _snapshot_file(path) for path in touched}
    old_registry = _read_registry()
    old_entries = _registry_entries(old_registry)
    proxy_started_for_export = False
    try:
        with _REGISTRY_LOCK:
            registry = _read_registry()
            port = _choose_export_port(registry)
            was_running = _SERVER is not None
            start_proxy_server(port=port, require_registry=False, strict=True)
            proxy_started_for_export = not was_running and _SERVER is not None
            proxy_token = _new_proxy_token()

            registry_channels = {}
            bouquet_lines = ["#NAME Live TV"]
            channel_xml = ['<?xml version="1.0" encoding="utf-8"?>', "<channels>"]
            for row in rows:
                ckey = _channel_key(row); registry_channels[ckey] = row
                row["_receiver_media_type"] = "itv"
                name = _receiver_name(row, "itv")
                ref = _service_ref(_proxy_url(pkey, ckey, port, proxy_token), service_type, identity=ckey)
                bouquet_lines.append("#SERVICE %s:%s" % (ref, name)); bouquet_lines.append("#DESCRIPTION %s" % name)
                _publish_picon(ref, row.get("_receiver_picon_local"))
                channel_xml.append('  <channel id="%s">%s</channel>' % (escape(_xmltv_id(pkey, ckey), {'"': '&quot;'}), escape(ref)))
            channel_xml.append("</channels>")

            include = '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % paths["bouquet_name"]
            try:
                with open(bouquet_index, "r", encoding="utf-8", errors="replace") as handle:
                    existing = handle.read().splitlines()
            except OSError:
                existing = ["#NAME Bouquets (TV)"]
            if not any(paths["bouquet_name"] in line for line in existing): existing.append(include)

            os.makedirs(EPGIMPORT_DIR, mode=0o755, exist_ok=True)
            xmltv_url = _xmltv_url(pkey, port, proxy_token)
            source_xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n<sources>\n'
                '  <sourcecat sourcecatname="Ultra Stalker">\n'
                '    <source type="gen_xmltv" nocheck="1" channels="%s">\n'
                '      <description>%s</description>\n      <url>%s</url>\n'
                '    </source>\n  </sourcecat>\n</sources>\n'
            ) % (escape(paths["epg_channels"]), escape("Ultra Stalker - " + _safe_name(profile.get("name") or profile.get("portal") or "Portal")), escape(xmltv_url))

            _atomic_text(paths["bouquet"], "\n".join(bouquet_lines) + "\n", 0o644)
            _atomic_text(bouquet_index, "\n".join(existing) + "\n", 0o644)
            _atomic_text(paths["epg_channels"], "\n".join(channel_xml) + "\n", 0o644)
            _atomic_text(paths["epg_source"], source_xml, 0o644)

            registry["_meta"] = {"format": 2, "proxy_port": port, "updated_at": int(time.time())}
            registry[pkey] = {
                "profile": profile, "service_type": int(service_type or 4097), "channels": registry_channels,
                "updated_at": int(time.time()),
                "epg_hours": max(2, min(24, int(load_settings().get("epg_hours", 4) or 4))),
                "proxy_port": port,
                "proxy_token": proxy_token,
            }
            _write_registry(registry)

        _ensure_xmltv_placeholder(pkey)
        refresh_xmltv_async(pkey, force=True)
        return {"profile_key": pkey, "channels": len(rows), "bouquet": paths["bouquet"],
                "epg_channels": paths["epg_channels"], "epg_source": paths["epg_source"],
                "xmltv_url": xmltv_url, "proxy_port": port}
    except Exception:
        for path in reversed(touched): _restore_snapshot(path, snapshots[path])
        if proxy_started_for_export and not old_entries:
            try: stop_proxy_server()
            except Exception as exc: LOG.debug("Bouquet export rollback proxy stop failed: %s", exc)
        raise



def export_live_category_bouquet(profile, channels, category_name, category_id=None, service_type=4097):
    """Export one Live category as its own Enigma2 TV bouquet.

    This intentionally does not reload the plugin/browser screen.  The dynamic
    proxy registry is merged in-place so exported channels keep working outside
    Ultra Stalker while the caller can preserve its exact category selection.
    Re-exporting the same category overwrites the same bouquet file atomically.
    """
    profile = dict(profile or {})
    rows = [dict(row) for row in (channels or []) if isinstance(row, dict) and (row.get("cmd") or row.get("command") or row.get("url"))]
    if not rows:
        raise ValueError("Category returned no exportable live channels")

    pkey = _profile_key(profile)
    label = _safe_name(category_name or "Live")
    identity = str(category_id or category_name or "live")
    digest = hashlib.sha1(identity.encode("utf-8", "ignore")).hexdigest()[:10]
    bouquet_name = "userbouquet.ultrastalker_%s_cat_%s.tv" % (pkey, digest)
    bouquet_path = os.path.join(ENIGMA2_DIR, bouquet_name)
    bouquet_index = os.path.join(ENIGMA2_DIR, "bouquets.tv")
    shared_paths = _bouquet_paths(pkey)
    touched = [bouquet_path, bouquet_index, shared_paths["epg_channels"], shared_paths["epg_source"], REGISTRY_FILE]
    snapshots = {path: _snapshot_file(path) for path in touched}
    old_registry = _read_registry()
    old_entries = _registry_entries(old_registry)
    proxy_started_for_export = False

    try:
        with _REGISTRY_LOCK:
            registry = _read_registry()
            port = _choose_export_port(registry)
            was_running = _SERVER is not None
            start_proxy_server(port=port, require_registry=False, strict=True)
            proxy_started_for_export = not was_running and _SERVER is not None

            entry = dict(registry.get(pkey) or {})
            existing_channels = dict(entry.get("channels") or {})
            token = str(entry.get("proxy_token") or "") or _new_proxy_token()
            bouquet_lines = ["#NAME %s" % label]
            exported_keys = []
            for row in rows:
                ckey = _channel_key(row)
                existing_channels[ckey] = row
                exported_keys.append(ckey)
                row["_receiver_media_type"] = "itv"
                name = _receiver_name(row, "itv")
                ref = _service_ref(_proxy_url(pkey, ckey, port, token), service_type, identity=ckey)
                bouquet_lines.append("#SERVICE %s:%s" % (ref, name))
                bouquet_lines.append("#DESCRIPTION %s" % name)
                _publish_picon(ref, row.get("_receiver_picon_local"))

            try:
                with open(bouquet_index, "r", encoding="utf-8", errors="replace") as handle:
                    existing = handle.read().splitlines()
            except OSError:
                existing = ["#NAME Bouquets (TV)"]
            include = '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % bouquet_name
            if not any(bouquet_name in line for line in existing):
                existing.append(include)

            _atomic_text(bouquet_path, "\n".join(bouquet_lines) + "\n", 0o644)
            _atomic_text(bouquet_index, "\n".join(existing) + "\n", 0o644)

            # Category exports now publish the same EPGImport mapping as full exports.
            # Previously the bouquet could play perfectly but ChannelSelection had no EPG source.
            os.makedirs(EPGIMPORT_DIR, mode=0o755, exist_ok=True)
            channel_xml = ['<?xml version="1.0" encoding="utf-8"?>', '<channels>']
            for map_key, map_row in existing_channels.items():
                if not isinstance(map_row, dict):
                    continue
                map_row["_receiver_media_type"] = "itv"
                map_ref = _service_ref(_proxy_url(pkey, map_key, port, token), service_type, identity=map_key)
                channel_xml.append('  <channel id="%s">%s</channel>' % (escape(_xmltv_id(pkey, map_key), {'"': '&quot;'}), escape(map_ref)))
            channel_xml.append('</channels>')
            xmltv_url = _xmltv_url(pkey, port, token)
            source_xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n<sources>\n'
                '  <sourcecat sourcecatname="Ultra Stalker">\n'
                '    <source type="gen_xmltv" nocheck="1" channels="%s">\n'
                '      <description>%s</description>\n      <url>%s</url>\n'
                '    </source>\n  </sourcecat>\n</sources>\n'
            ) % (escape(shared_paths["epg_channels"]), escape("Ultra Stalker - " + _safe_name(profile.get("name") or profile.get("portal") or "Portal")), escape(xmltv_url))
            _atomic_text(shared_paths["epg_channels"], "\n".join(channel_xml) + "\n", 0o644)
            _atomic_text(shared_paths["epg_source"], source_xml, 0o644)

            exports = dict(entry.get("category_exports") or {})
            exports[digest] = {
                "category_id": identity, "category_name": label,
                "bouquet_name": bouquet_name, "channels": exported_keys,
                "updated_at": int(time.time()),
            }
            registry["_meta"] = {"format": 2, "proxy_port": port, "updated_at": int(time.time())}
            registry[pkey] = {
                "profile": profile,
                "service_type": int(service_type or entry.get("service_type") or 4097),
                "channels": existing_channels,
                "updated_at": int(time.time()),
                "epg_hours": max(2, min(24, int(load_settings().get("epg_hours", 4) or 4))),
                "proxy_port": port,
                "proxy_token": token,
                "category_exports": exports,
            }
            _write_registry(registry)

        _ensure_xmltv_placeholder(pkey)
        refresh_xmltv_async(pkey, force=True)
        return {
            "profile_key": pkey, "channels": len(rows), "bouquet": bouquet_path,
            "bouquet_name": bouquet_name, "category": label, "proxy_port": port,
        }
    except Exception:
        for path in reversed(touched):
            _restore_snapshot(path, snapshots[path])
        if proxy_started_for_export and not old_entries:
            try: stop_proxy_server()
            except Exception as exc: LOG.debug("Category bouquet rollback proxy stop failed: %s", exc)
        raise


def export_receiver_items(profile, items, media_type, label=None, service_type=4097):
    """Append selected Live/VOD/Episode items to a receiver-side bouquet.

    The bouquet always uses stable loopback proxy references so expiring provider
    links are resolved only when Enigma2 tunes the item. Cached artwork is
    published as a native picon when the caller supplies _receiver_picon_local.
    """
    profile = dict(profile or {})
    media_type = str(media_type or "itv").lower()
    if media_type == "movie":
        media_type = "vod"
    if media_type not in ("itv", "vod", "episode"):
        raise ValueError("Unsupported receiver media type: %s" % media_type)
    rows = [dict(row) for row in (items or []) if isinstance(row, dict) and (row.get("cmd") or row.get("command") or row.get("url"))]
    if not rows:
        raise ValueError("No playable items to export")

    pkey = _profile_key(profile)
    group = "live" if media_type == "itv" else ("movies" if media_type == "vod" else "series")
    bouquet_name = "userbouquet.ultrastalker_%s_smart_%s.tv" % (pkey, group)
    bouquet_path = os.path.join(ENIGMA2_DIR, bouquet_name)
    bouquet_index = os.path.join(ENIGMA2_DIR, "bouquets.tv")
    shared_paths = _bouquet_paths(pkey)
    touched = [bouquet_path, bouquet_index, REGISTRY_FILE]
    if media_type == "itv":
        touched += [shared_paths["epg_channels"], shared_paths["epg_source"]]
    snapshots = {path: _snapshot_file(path) for path in touched}
    old_registry = _read_registry()
    old_entries = _registry_entries(old_registry)
    proxy_started_for_export = False

    try:
        with _REGISTRY_LOCK:
            registry = _read_registry()
            port = _choose_export_port(registry)
            was_running = _SERVER is not None
            start_proxy_server(port=port, require_registry=False, strict=True)
            proxy_started_for_export = not was_running and _SERVER is not None

            entry = dict(registry.get(pkey) or {})
            channels = dict(entry.get("channels") or {})
            token = str(entry.get("proxy_token") or "") or _new_proxy_token()
            smart = dict(entry.get("smart_exports") or {})
            saved = dict((smart.get(group) or {}).get("items") or {})

            for row in rows:
                row["_receiver_media_type"] = media_type
                ckey = _channel_key(row)
                channels[ckey] = row
                saved[ckey] = True

            display_label = _safe_name(label or ("Live" if group == "live" else ("Movies" if group == "movies" else "Series")))
            bouquet_lines = ["#NAME %s" % display_label]
            for ckey in saved:
                row = channels.get(ckey)
                if not isinstance(row, dict):
                    continue
                row_type = str(row.get("_receiver_media_type") or media_type).lower()
                ref = _service_ref(_proxy_url(pkey, ckey, port, token), service_type, identity=ckey)
                name = _receiver_name(row, row_type)
                bouquet_lines.append("#SERVICE %s:%s" % (ref, name))
                bouquet_lines.append("#DESCRIPTION %s" % name)
                (_publish_picon(ref, row.get("_receiver_picon_local")) if row_type == "itv" else _queue_picon_publish(ref, row.get("_receiver_picon_local")))

            try:
                with open(bouquet_index, "r", encoding="utf-8", errors="replace") as handle:
                    existing = handle.read().splitlines()
            except OSError:
                existing = ["#NAME Bouquets (TV)"]
            include = '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % bouquet_name
            if not any(bouquet_name in line for line in existing):
                existing.append(include)
            _atomic_text(bouquet_path, "\n".join(bouquet_lines) + "\n", 0o644)
            _atomic_text(bouquet_index, "\n".join(existing) + "\n", 0o644)

            smart[group] = {"bouquet_name": bouquet_name, "items": saved, "updated_at": int(time.time())}
            registry["_meta"] = {"format": 2, "proxy_port": port, "updated_at": int(time.time())}
            registry[pkey] = {
                "profile": profile,
                "service_type": int(service_type or entry.get("service_type") or 4097),
                "channels": channels,
                "updated_at": int(time.time()),
                "epg_hours": max(2, min(24, int(load_settings().get("epg_hours", 4) or 4))),
                "proxy_port": port,
                "proxy_token": token,
                "category_exports": dict(entry.get("category_exports") or {}),
                "smart_exports": smart,
            }
            _write_registry(registry)

        if media_type == "itv":
            # Reuse the normal profile EPGImport mapping for Smart Live items.
            entry = _read_registry().get(pkey) or {}
            channels = entry.get("channels") or {}
            token = str(entry.get("proxy_token") or "")
            port = int(entry.get("proxy_port") or _registry_port())
            channel_xml = ['<?xml version="1.0" encoding="utf-8"?>', '<channels>']
            for ckey, row in channels.items():
                if not isinstance(row, dict) or str(row.get("_receiver_media_type") or "itv").lower() != "itv":
                    continue
                ref = _service_ref(_proxy_url(pkey, ckey, port, token), service_type, identity=ckey)
                channel_xml.append('  <channel id="%s">%s</channel>' % (escape(_xmltv_id(pkey, ckey), {'"': '&quot;'}), escape(ref)))
            channel_xml.append('</channels>')
            os.makedirs(EPGIMPORT_DIR, mode=0o755, exist_ok=True)
            xmltv_url = _xmltv_url(pkey, port, token)
            source_xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n<sources>\n'
                '  <sourcecat sourcecatname="Ultra Stalker">\n'
                '    <source type="gen_xmltv" nocheck="1" channels="%s">\n'
                '      <description>%s</description>\n      <url>%s</url>\n'
                '    </source>\n  </sourcecat>\n</sources>\n'
            ) % (escape(shared_paths["epg_channels"]), escape("Ultra Stalker - " + _safe_name(profile.get("name") or profile.get("portal") or "Portal")), escape(xmltv_url))
            _atomic_text(shared_paths["epg_channels"], "\n".join(channel_xml) + "\n", 0o644)
            _atomic_text(shared_paths["epg_source"], source_xml, 0o644)
            _ensure_xmltv_placeholder(pkey)
            refresh_xmltv_async(pkey, force=True)

        return {"profile_key": pkey, "items": len(rows), "bouquet": bouquet_path, "bouquet_name": bouquet_name, "group": group}
    except Exception:
        for path in reversed(touched):
            _restore_snapshot(path, snapshots[path])
        if proxy_started_for_export and not old_entries:
            try: stop_proxy_server()
            except Exception as exc: LOG.debug("Smart receiver export rollback proxy stop failed: %s", exc)
        raise



def export_series_category_bouquets(profile, series_groups, category_name, category_id=None, service_type=4097):
    """Export one Series category as a parent bouquet containing one child bouquet per series.

    series_groups: [{"name": str, "poster": path, "episodes": [playable rows]}]
    This deliberately leaves Live export untouched and writes all files atomically before
    publishing cached artwork in the background.
    """
    profile = dict(profile or {})
    groups = []
    for group in (series_groups or []):
        if not isinstance(group, dict):
            continue
        episodes = [dict(row) for row in (group.get("episodes") or [])
                    if isinstance(row, dict) and (row.get("cmd") or row.get("command") or row.get("url"))]
        if not episodes:
            continue
        groups.append({"name": _safe_name(group.get("name") or "Series"),
                       "poster": str(group.get("poster") or ""), "episodes": episodes})
    if not groups:
        raise ValueError("No playable series episodes to export")

    pkey = _profile_key(profile)
    label = _safe_name(category_name or "Series")
    identity = str(category_id or label or "series")
    digest = hashlib.sha1(identity.encode("utf-8", "ignore")).hexdigest()[:10]
    parent_name = "userbouquet.ultrastalker_%s_seriescat_%s.tv" % (pkey, digest)
    parent_path = os.path.join(ENIGMA2_DIR, parent_name)
    bouquet_index = os.path.join(ENIGMA2_DIR, "bouquets.tv")
    old_flat_name = "userbouquet.ultrastalker_%s_smart_series.tv" % pkey
    old_flat_path = os.path.join(ENIGMA2_DIR, old_flat_name)

    child_specs = []
    for pos, group in enumerate(groups):
        sdigest = hashlib.sha1((identity + "|" + group["name"] + "|" + str(pos)).encode("utf-8", "ignore")).hexdigest()[:10]
        child_name = "userbouquet.ultrastalker_%s_series_%s.tv" % (pkey, sdigest)
        child_specs.append((group, child_name, os.path.join(ENIGMA2_DIR, child_name)))

    touched = [parent_path, bouquet_index, old_flat_path, REGISTRY_FILE] + [x[2] for x in child_specs]
    snapshots = {path: _snapshot_file(path) for path in touched}
    old_registry = _read_registry()
    old_entries = _registry_entries(old_registry)
    proxy_started_for_export = False
    pending_picons = []

    try:
        with _REGISTRY_LOCK:
            registry = _read_registry()
            port = _choose_export_port(registry)
            was_running = _SERVER is not None
            start_proxy_server(port=port, require_registry=False, strict=True)
            proxy_started_for_export = not was_running and _SERVER is not None
            entry = dict(registry.get(pkey) or {})
            channels = dict(entry.get("channels") or {})
            token = str(entry.get("proxy_token") or "") or _new_proxy_token()

            parent_lines = ["#NAME %s" % label]
            total = 0
            export_children = []
            for group, child_name, child_path in child_specs:
                child_lines = ["#NAME %s" % group["name"]]
                episode_keys = []
                for row in group["episodes"]:
                    row["_receiver_media_type"] = "episode"
                    ckey = _channel_key(row)
                    channels[ckey] = row
                    episode_keys.append(ckey)
                    ref = _service_ref(_proxy_url(pkey, ckey, port, token), service_type, identity=ckey)
                    name = _receiver_name(row, "episode")
                    child_lines.append("#SERVICE %s:%s" % (ref, name))
                    child_lines.append("#DESCRIPTION %s" % name)
                    poster = str(row.get("_receiver_picon_local") or group.get("poster") or "")
                    if poster:
                        pending_picons.append((ref, poster))
                    total += 1
                _atomic_text(child_path, "\n".join(child_lines) + "\n", 0o644)
                parent_lines.append('#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % child_name)
                parent_lines.append("#DESCRIPTION %s" % group["name"])
                export_children.append({"name": group["name"], "bouquet_name": child_name, "episodes": episode_keys})

            _atomic_text(parent_path, "\n".join(parent_lines) + "\n", 0o644)
            try:
                with open(bouquet_index, "r", encoding="utf-8", errors="replace") as handle:
                    existing = handle.read().splitlines()
            except OSError:
                existing = ["#NAME Bouquets (TV)"]
            include = '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % parent_name
            existing = [line for line in existing if old_flat_name not in line]
            if not any(parent_name in line for line in existing):
                existing.append(include)
            _atomic_text(bouquet_index, "\n".join(existing) + "\n", 0o644)
            try:
                if os.path.isfile(old_flat_path):
                    os.unlink(old_flat_path)
                    _fsync_parent_dir(old_flat_path)
            except OSError:
                pass

            exports = dict(entry.get("series_category_exports") or {})
            exports[digest] = {"category_id": identity, "category_name": label, "bouquet_name": parent_name,
                               "children": export_children, "updated_at": int(time.time())}
            registry["_meta"] = {"format": 2, "proxy_port": port, "updated_at": int(time.time())}
            registry[pkey] = {
                "profile": profile,
                "service_type": int(service_type or entry.get("service_type") or 4097),
                "channels": channels,
                "updated_at": int(time.time()),
                "epg_hours": max(2, min(24, int(load_settings().get("epg_hours", 4) or 4))),
                "proxy_port": port,
                "proxy_token": token,
                "category_exports": dict(entry.get("category_exports") or {}),
                "series_category_exports": exports,
                "smart_exports": dict(entry.get("smart_exports") or {}),
            }
            _write_registry(registry)

        for ref, poster in pending_picons:
            _queue_picon_publish(ref, poster)
        return {"profile_key": pkey, "items": total, "series": len(groups), "bouquet": parent_path,
                "bouquet_name": parent_name, "category": label, "proxy_port": port}
    except Exception:
        for path in reversed(touched):
            _restore_snapshot(path, snapshots[path])
        if proxy_started_for_export and not old_entries:
            try: stop_proxy_server()
            except Exception as exc: LOG.debug("Series category rollback proxy stop failed: %s", exc)
        raise


def unexport_live_integration(profile_or_key, reload=True):
    """Remove one portal's bouquet, EPGImport files, XMLTV cache and registry row."""
    pkey = str(profile_or_key or "") if isinstance(profile_or_key, str) else _profile_key(profile_or_key or {})
    paths = _bouquet_paths(pkey)
    removed = []
    with _REGISTRY_LOCK:
        registry = _read_registry()
        if pkey in registry:
            registry.pop(pkey, None)
        remaining = _registry_entries(registry)
        if remaining:
            registry["_meta"] = {"format": 2, "proxy_port": _registry_port(registry), "updated_at": int(time.time())}
            _write_registry(registry)
        else:
            try:
                os.unlink(REGISTRY_FILE)
                _fsync_parent_dir(REGISTRY_FILE)
                removed.append(REGISTRY_FILE)
            except OSError:
                pass

    for key in ("bouquet", "epg_channels", "epg_source", "xmltv_cache"):
        path = paths[key]
        try:
            os.unlink(path); _fsync_parent_dir(path); removed.append(path)
        except OSError:
            pass

    # Also remove any per-category Live bouquets owned by this profile.
    category_prefix = "userbouquet.ultrastalker_%s_cat_" % pkey
    try:
        for name in os.listdir(ENIGMA2_DIR):
            if not (name.startswith(category_prefix) and name.endswith(".tv")):
                continue
            path = os.path.join(ENIGMA2_DIR, name)
            try:
                os.unlink(path); _fsync_parent_dir(path); removed.append(path)
            except OSError:
                pass
    except OSError:
        pass

    bouquet_index = os.path.join(ENIGMA2_DIR, "bouquets.tv")
    try:
        with open(bouquet_index, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        filtered = [line for line in lines if paths["bouquet_name"] not in line and category_prefix not in line]
        if filtered != lines:
            _atomic_text(bouquet_index, "\n".join(filtered) + "\n", 0o644)
    except OSError:
        pass

    if not _registry_entries(_read_registry()):
        stop_proxy_server()
    if reload:
        reload_bouquets()
    return {"profile_key": pkey, "removed": removed}


def unexport_all_live_integrations(reload=True):
    """Remove every plugin-owned dynamic bouquet/EPGImport export."""
    registry = _read_registry(); keys = list(_registry_entries(registry).keys())
    removed=[]
    for pkey in keys:
        try:
            result = unexport_live_integration(pkey, reload=False)
            removed.extend(result.get("removed", []))
        except Exception as exc:
            LOG.warning("Could not remove stale live integration %s: %s", pkey, exc)
    # Also clean orphaned generated files if registry state was damaged/missing.
    import glob
    patterns = [
        os.path.join(ENIGMA2_DIR, "userbouquet.ultrastalker_*.tv"),
        os.path.join(EPGIMPORT_DIR, "ultrastalker_*.channels.xml"),
        os.path.join(EPGIMPORT_DIR, "ultrastalker_*.sources.xml"),
        os.path.join(CONFIG_DIR, "xmltv_*.xml"),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern):
            try: os.unlink(path); _fsync_parent_dir(path); removed.append(path)
            except OSError: pass
    try: os.unlink(REGISTRY_FILE); _fsync_parent_dir(REGISTRY_FILE)
    except OSError: pass
    bouquet_index=os.path.join(ENIGMA2_DIR,"bouquets.tv")
    try:
        with open(bouquet_index,"r",encoding="utf-8",errors="replace") as handle: lines=handle.read().splitlines()
        filtered=[line for line in lines if "userbouquet.ultrastalker_" not in line]
        if filtered != lines: _atomic_text(bouquet_index,"\n".join(filtered)+"\n",0o644)
    except OSError: pass
    stop_proxy_server()
    if reload: reload_bouquets()
    return {"exports_removed": len(keys), "files_removed": len(set(removed))}


def reload_bouquets():
    try:
        from enigma import eDVBDB
        db = eDVBDB.getInstance()
        db.reloadBouquets()
        try: db.reloadServicelist()
        except Exception: pass
        return True
    except Exception:
        return False


def _xmltv_cache_path(profile_key):
    return os.path.join(CONFIG_DIR, "xmltv_%s.xml" % profile_key)


def _xmltv_channel_lines(profile_key, channels):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<tv generator-info-name="UltraStalker">']
    for ckey, channel in channels:
        name = _safe_name(channel.get("name") or channel.get("title") or "Channel")
        cid = _xmltv_id(profile_key, ckey)
        lines.append('  <channel id="%s"><display-name>%s</display-name></channel>' % (escape(cid, {'"': '&quot;'}), escape(name)))
    return lines


def _ensure_xmltv_placeholder(profile_key):
    cache = _xmltv_cache_path(profile_key)
    try:
        if os.path.getsize(cache) > 100:
            return cache
    except OSError:
        pass
    entry = _read_registry().get(profile_key) or {}
    channels = list((entry.get("channels") or {}).items())[:1200]
    lines = _xmltv_channel_lines(profile_key, channels)
    lines.append("</tv>")
    _atomic_text(cache, "\n".join(lines) + "\n", 0o600)
    return cache


def _event_xml(profile_key, channel_key, row):
    start, stop = event_times(row)
    if not start:
        return ""
    if not stop:
        stop = start + max(60, int(row.get("duration") or 3600))
    channel_id = _xmltv_id(profile_key, channel_key)
    start_text = time.strftime("%Y%m%d%H%M%S %z", time.localtime(start))
    stop_text = time.strftime("%Y%m%d%H%M%S %z", time.localtime(stop))
    title = _safe_name(row.get("name") or row.get("title") or row.get("descr") or "Programme")
    desc = _safe_name(row.get("description") or row.get("descr") or row.get("plot") or "")
    parts = ['  <programme start="%s" stop="%s" channel="%s">' % (start_text, stop_text, escape(channel_id))]
    parts.append("    <title>%s</title>" % escape(title))
    if desc and desc != title:
        parts.append("    <desc>%s</desc>" % escape(desc))
    parts.append("  </programme>")
    return "\n".join(parts)


def _epg_worker(profile, work, hours, deadline, cancel_event=None, clients=None, clients_lock=None):
    client = _client(profile)
    if clients is not None:
        lock = clients_lock or _XMLTV_REFRESH_LOCK
        with lock: clients.add(client)
    out = []
    try:
        for ckey, channel in work:
            if (cancel_event is not None and cancel_event.is_set()) or time.monotonic() >= deadline: break
            cid = channel.get("id") or channel.get("ch_id")
            if not cid: continue
            try:
                rows = client.epg(cid, hours, cancel_event=cancel_event)
            except Exception as exc:
                if not (cancel_event is not None and cancel_event.is_set()):
                    LOG.debug("EPG channel refresh failed channel=%s: %s", cid, exc)
                rows = None
            if rows is not None: out.append((ckey, rows))
            if time.monotonic() < deadline and not (cancel_event is not None and cancel_event.is_set()):
                if cancel_event is not None: cancel_event.wait(0.05)
                else: time.sleep(0.05)
        return out
    finally:
        if clients is not None:
            lock = clients_lock or _XMLTV_REFRESH_LOCK
            with lock: clients.discard(client)
        try: client.close()
        except Exception as exc: LOG.debug("EPG worker client close failed: %s", exc)


def generate_xmltv(profile_key, force=False, max_seconds=None, cancel_event=None, active_clients=None, active_clients_lock=None):
    registry = _read_registry(); entry = registry.get(profile_key) if isinstance(registry, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("channels"), dict): raise ValueError("Unknown Stalker bouquet")
    cache = _xmltv_cache_path(profile_key)
    if not force:
        try:
            if os.path.getsize(cache) > 100 and time.time() - os.path.getmtime(cache) < _XMLTV_TTL: return cache
        except OSError: pass
    lock = _XMLTV_LOCKS.setdefault(profile_key, threading.Lock())
    with lock:
        if not force:
            try:
                if os.path.getsize(cache) > 100 and time.time() - os.path.getmtime(cache) < _XMLTV_TTL: return cache
            except OSError: pass
        cancel_event = cancel_event or threading.Event()
        profile = entry.get("profile") or {}; channels = list((entry.get("channels") or {}).items())[:1200]
        hours = max(2, min(24, int(entry.get("epg_hours") or 4)))
        if max_seconds is None:
            try: max_seconds = int(load_settings().get("epg_refresh_budget", _XMLTV_DEFAULT_BUDGET) or _XMLTV_DEFAULT_BUDGET)
            except (TypeError, ValueError): max_seconds = _XMLTV_DEFAULT_BUDGET
        max_seconds = max(10, min(180, int(max_seconds))); deadline = time.monotonic() + max_seconds
        workers = min(6, max(1, len(channels))); chunks = [channels[index::workers] for index in range(workers)]
        results = []; result_lock = threading.Lock(); threads = []
        clients = active_clients if active_clients is not None else set(); clients_lock = active_clients_lock or threading.RLock()
        def run_chunk(chunk):
            try:
                rows = _epg_worker(profile, chunk, hours, deadline, cancel_event, clients, clients_lock)
                with result_lock: results.extend(rows)
            except Exception as exc:
                if not cancel_event.is_set(): LOG.warning("EPG worker failed profile=%s: %s", profile_key, exc)
        for index, chunk in enumerate(chunks):
            if not chunk: continue
            thread = threading.Thread(target=run_chunk, args=(chunk,), name="stalker-epg-worker-%d" % index, daemon=True)
            thread.start(); threads.append(thread)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            thread.join(remaining)
        alive = [thread for thread in threads if thread.is_alive()]
        if alive:
            cancel_event.set()
            with clients_lock: open_clients = list(clients)
            for client in open_clients:
                try: client.cancel_pending_requests()
                except Exception as exc: LOG.debug("EPG client cancellation failed: %s", exc)
            grace_deadline = time.monotonic() + 3.0
            for thread in alive:
                remaining = grace_deadline - time.monotonic()
                if remaining <= 0: break
                thread.join(remaining)
            still_threads = [thread for thread in threads if thread.is_alive()]
            if still_threads:
                LOG.warning("EPG workers exceeded cancellation grace; abandoning this generation without blocking forever: %s", ",".join(thread.name for thread in still_threads))
                # Workers are daemon threads and have already received both the
                # cancel event and client.cancel_pending_requests(). Never perform
                # an unbounded join here: a wedged socket/library must not wedge
                # XMLTV refresh forever. Give one final bounded grace period.
                final_deadline=time.monotonic()+2.0
                for thread in still_threads:
                    remaining=final_deadline-time.monotonic()
                    if remaining<=0:break
                    thread.join(remaining)
                still_threads=[thread for thread in still_threads if thread.is_alive()]
                if still_threads:
                    LOG.error("EPG workers abandoned after bounded shutdown: %s", ",".join(thread.name for thread in still_threads))
                    with _XMLTV_REFRESH_LOCK:
                        _XMLTV_ORPHANS[profile_key] = {
                            "threads": list(still_threads),
                            "cancel": cancel_event,
                            "clients": clients,
                            "clients_lock": clients_lock,
                            "since": time.monotonic(),
                        }
        with result_lock: completed_results = list(results)
        lines = _xmltv_channel_lines(profile_key, channels)
        for ckey, events in completed_results:
            for row in events if isinstance(events, list) else []:
                if isinstance(row, dict):
                    xml = _event_xml(profile_key, ckey, row)
                    if xml: lines.append(xml)
        lines.append("</tv>"); _atomic_text(cache, "\n".join(lines) + "\n", 0o600); return cache


def _orphan_threads(state):
    if isinstance(state, dict):
        return list(state.get("threads") or ())
    return list(state or ())


def _reap_xmltv_orphans_locked():
    alive_total = 0
    for profile_key, state in list(_XMLTV_ORPHANS.items()):
        alive = [thread for thread in _orphan_threads(state) if thread.is_alive()]
        if not alive:
            _XMLTV_ORPHANS.pop(profile_key, None)
            continue
        alive_total += len(alive)
        if isinstance(state, dict):
            state["threads"] = alive
        else:
            _XMLTV_ORPHANS[profile_key] = {"threads": alive, "cancel": None, "clients": set(), "clients_lock": threading.RLock(), "since": time.monotonic()}
    return alive_total


def _cancel_xmltv_state(state):
    if not isinstance(state, dict):
        return
    cancel_event = state.get("cancel")
    if cancel_event is not None:
        cancel_event.set()
    clients_lock = state.get("clients_lock") or _XMLTV_REFRESH_LOCK
    try:
        with clients_lock:
            clients = list(state.get("clients") or ())
    except Exception as exc:
        LOG.debug("XMLTV cancellation client snapshot failed: %s", exc)
        clients = []
    for client in clients:
        try: client.cancel_pending_requests()
        except Exception as exc: LOG.debug("XMLTV client cancellation failed: %s", exc)


def refresh_xmltv_async(profile_key, force=True):
    if _XMLTV_PAUSED.is_set(): return False
    with _XMLTV_REFRESH_LOCK:
        orphan_total = _reap_xmltv_orphans_locked()
        own_orphans = _orphan_threads(_XMLTV_ORPHANS.get(profile_key, ()))
        if own_orphans:
            return False
        if orphan_total >= _XMLTV_ORPHAN_MAX_THREADS:
            LOG.warning("XMLTV refresh suppressed: orphan worker cap reached (%d)", orphan_total)
            return False
        if profile_key in _XMLTV_REFRESHING: return False
        _XMLTV_REFRESHING.add(profile_key)
        cancel_event = threading.Event(); clients = set(); clients_lock = threading.RLock()
        state = {"thread": None, "cancel": cancel_event, "clients": clients, "clients_lock": clients_lock}
        _XMLTV_ACTIVE[profile_key] = state
    def worker():
        try:
            generate_xmltv(profile_key, force=force, cancel_event=cancel_event, active_clients=clients, active_clients_lock=clients_lock)
        except Exception as exc:
            if not cancel_event.is_set(): LOG.warning("XMLTV refresh failed profile=%s: %s", profile_key, exc)
        finally:
            with _XMLTV_REFRESH_LOCK:
                _XMLTV_REFRESHING.discard(profile_key); _XMLTV_ACTIVE.pop(profile_key, None)
    thread = threading.Thread(target=worker, name="stalker-epg-%s" % profile_key[:6], daemon=True)
    state["thread"] = thread; thread.start(); return True


def pause_xmltv_refreshes(wait=True, timeout=8.0):
    _XMLTV_PAUSED.set()
    with _XMLTV_REFRESH_LOCK:
        _reap_xmltv_orphans_locked()
        states = list(_XMLTV_ACTIVE.values())
        orphan_states = [state for state in _XMLTV_ORPHANS.values() if isinstance(state, dict)]
    for state in states + orphan_states:
        _cancel_xmltv_state(state)
    if wait:
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        threads = []
        for state in states:
            thread = state.get("thread")
            if thread is not None: threads.append(thread)
        for state in orphan_states:
            threads.extend(_orphan_threads(state))
        seen = set()
        for thread in threads:
            marker = id(thread)
            if marker in seen or not thread.is_alive(): continue
            seen.add(marker)
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            thread.join(remaining)
    with _XMLTV_REFRESH_LOCK:
        _reap_xmltv_orphans_locked()
        active_alive = any((state.get("thread") is not None and state["thread"].is_alive()) for state in _XMLTV_ACTIVE.values())
        orphan_alive = any(_orphan_threads(state) for state in _XMLTV_ORPHANS.values())
        return not active_alive and not orphan_alive


def resume_xmltv_refreshes():
    _XMLTV_PAUSED.clear()


def _xmltv_for_http(profile_key):
    entry = _read_registry().get(profile_key) or {}
    if not isinstance(entry, dict) or not isinstance(entry.get("channels"), dict):
        raise ValueError("Unknown Stalker bouquet")
    cache = _ensure_xmltv_placeholder(profile_key)
    try:
        age = time.time() - os.path.getmtime(cache)
    except OSError:
        age = _XMLTV_TTL + 1
    if age >= _XMLTV_TTL:
        refresh_xmltv_async(profile_key, force=True)
    return cache


def _epg_manager_loop():
    while not _EPG_MANAGER_STOP.wait(900):
        if _XMLTV_PAUSED.is_set(): continue
        try: registry = _read_registry()
        except Exception as exc:
            LOG.warning("EPG manager registry read failed: %s", exc); continue
        for pkey in _registry_entries(registry):
            cache = _xmltv_cache_path(pkey)
            try: age = time.time() - os.path.getmtime(cache)
            except OSError: age = _XMLTV_BACKGROUND_AFTER + 1
            if age >= _XMLTV_BACKGROUND_AFTER:
                refresh_xmltv_async(pkey, force=True)


def _start_epg_manager():
    global _EPG_MANAGER_THREAD
    if _EPG_MANAGER_THREAD is not None and _EPG_MANAGER_THREAD.is_alive():
        return
    _EPG_MANAGER_STOP.clear()
    _EPG_MANAGER_THREAD = threading.Thread(target=_epg_manager_loop, name="stalker-epg-manager", daemon=True)
    _EPG_MANAGER_THREAD.start()


class _Handler(BaseHTTPRequestHandler):
    server_version = "UltraStalker/2"
    sys_version = ""

    def log_message(self, *args):
        return

    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        try:
            self.connection.settimeout(_PROXY_SOCKET_TIMEOUT)
        except (AttributeError, OSError):
            pass

    def _send_text(self, code, text):
        body = str(text).encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _do_GET_inner(self):
        parsed = urllib.parse.urlsplit(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        try:
            if parts and parts[0] == "stream" and len(parts) in (3, 4):
                if len(parts) == 4:
                    supplied_token, pkey, ckey = parts[1], parts[2], parts[3]
                else:
                    supplied_token, pkey, ckey = None, parts[1], parts[2]
                entry = _read_registry().get(pkey) or {}
                if not _token_matches(entry, supplied_token):
                    self._send_text(403, "Forbidden")
                    return
                channel = (entry.get("channels") or {}).get(ckey)
                if not isinstance(channel, dict):
                    self._send_text(404, "Unknown channel")
                    return
                client = _client(entry.get("profile") or {})
                media_type = str(channel.get("_receiver_media_type") or "itv").lower()
                try: url = client.create_link(channel, media_type)
                finally:
                    try: client.close()
                    except Exception as exc: LOG.debug("Bouquet proxy client close failed: %s", exc)
                if not url:
                    raise RuntimeError("Portal returned no stream URL")
                self.send_response(302)
                self.send_header("Location", str(url))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                return
            if parts and parts[0] == "xmltv" and len(parts) in (2, 3):
                if len(parts) == 3:
                    supplied_token, pkey = parts[1], parts[2]
                else:
                    supplied_token, pkey = None, parts[1]
                entry = _read_registry().get(pkey) or {}
                if not _token_matches(entry, supplied_token):
                    self._send_text(403, "Forbidden")
                    return
                path = _xmltv_for_http(pkey)
                with open(path, "rb") as handle:
                    body = handle.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=900, stale-while-revalidate=3600")
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_text(404, "Not found")
        except Exception as exc:
            # Never log self.path here: tokenized proxy URLs are capabilities.
            LOG.warning("Bouquet proxy request failed detail=%s", exc)
            self._send_text(502, "Stream resolution failed")

    def do_GET(self):
        client_host = self.client_address[0] if self.client_address else ""
        if not _is_loopback_address(client_host):
            self._send_text(403, "Loopback access only")
            return
        if not _PROXY_REQUEST_SLOTS.acquire(False):
            self._send_text(503, "Proxy busy")
            return
        try:
            self._do_GET_inner()
        finally:
            _PROXY_REQUEST_SLOTS.release()


def start_proxy_server(port=None, require_registry=True, strict=False):
    global _SERVER, _SERVER_THREAD, _SERVER_PORT, _LAST_PROXY_ERROR
    # Import/UI fast path: if the proxy is already alive, do not touch the
    # registry just to rediscover the port. Explicit conflicting ports retain
    # the old strict/non-strict behaviour.
    if _SERVER is not None:
        if port is None or int(port) == int(_SERVER_PORT or 0):
            resume_xmltv_refreshes(); _start_epg_manager(); return _SERVER
        message = "Dynamic bouquet proxy is already running on port %s" % _SERVER_PORT
        _LAST_PROXY_ERROR = message
        if strict: raise RuntimeError(message)
        return None
    resume_xmltv_refreshes()
    registry = _read_registry()
    try:
        registry = _migrate_legacy_proxy_tokens(registry)
    except Exception as exc:
        _LAST_PROXY_ERROR = "Legacy bouquet token migration failed: %s" % exc
        LOG.warning("%s", _LAST_PROXY_ERROR)
        if strict:
            raise RuntimeError(_LAST_PROXY_ERROR)
        return None
    if require_registry and not _registry_entries(registry):
        return None
    target_port = int(port or _registry_port(registry))
    if _SERVER is not None:
        if _SERVER_PORT == target_port:
            _start_epg_manager()
            return _SERVER
        message = "Dynamic bouquet proxy is already running on port %s" % _SERVER_PORT
        _LAST_PROXY_ERROR = message
        if strict: raise RuntimeError(message)
        return None
    try:
        _assert_loopback_bind()
        server = ThreadingHTTPServer((PROXY_HOST, target_port), _Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="stalker-bouquet-proxy", daemon=True)
        thread.start()
        _SERVER = server; _SERVER_THREAD = thread; _SERVER_PORT = target_port; _LAST_PROXY_ERROR = ""
        _start_epg_manager()
        return server
    except OSError as exc:
        _LAST_PROXY_ERROR = "Cannot bind %s:%d: %s" % (PROXY_HOST, target_port, exc)
        if strict:
            raise RuntimeError(_LAST_PROXY_ERROR)
        return None


def proxy_status():
    registry = _read_registry()
    return {
        "running": _SERVER is not None,
        "port": _SERVER_PORT or _registry_port(registry),
        "exports": len(_registry_entries(registry)),
        "last_error": _LAST_PROXY_ERROR,
    }


def stop_proxy_server():
    global _SERVER, _SERVER_THREAD, _SERVER_PORT, _EPG_MANAGER_THREAD
    pause_xmltv_refreshes(wait=True, timeout=5.0)
    server = _SERVER; server_thread = _SERVER_THREAD; manager_thread = _EPG_MANAGER_THREAD
    _SERVER = None; _SERVER_THREAD = None; _SERVER_PORT = None
    _EPG_MANAGER_STOP.set(); _EPG_MANAGER_THREAD = None
    if manager_thread is not None and manager_thread.is_alive():
        try: manager_thread.join(2.0)
        except Exception as exc: LOG.debug("EPG manager join failed: %s", exc)
    if server is not None:
        try: server.shutdown()
        except Exception as exc: LOG.debug("Proxy shutdown failed: %s", exc)
        try: server.server_close()
        except Exception as exc: LOG.debug("Proxy close failed: %s", exc)
    if server_thread is not None and server_thread.is_alive():
        try: server_thread.join(1.5)
        except Exception as exc: LOG.debug("Proxy thread join failed: %s", exc)

