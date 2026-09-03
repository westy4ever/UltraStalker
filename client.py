# -*- coding: utf-8 -*-
"""Native Stalker/MAG protocol client used by Ultra Stalker.

The UI, storage and plugin namespace are Ultra Stalker.  This client keeps
only the protocol behaviours needed for portal compatibility: endpoint
selection, MAG handshake/profile, category/content retrieval, series expansion
and stream-link resolution.
"""
from __future__ import annotations

import gzip
import zlib
import hashlib
import http.client
import http.cookiejar
import http.cookies
import ipaddress
import json
import random
import re
import socket
import ssl
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime

from .core.cache import CACHE, CachePolicy
from .core.perf import PERF
from .repositories.database import DB
from .core.errors import (
    PortalConnectionError, PortalAuthenticationError, PortalRateLimitError,
    PortalResponseError, PlaybackLinkError,
)
from .log import get_logger
from .netsec import validate_http_url

LOG = get_logger()


def _resolve_portal_endpoints(hostname, port):
    """Resolve an explicitly configured portal once for the next socket.

    Portal origins are user-approved and may legitimately live on RFC1918/ULA
    networks, so unlike untrusted artwork/media fetches this helper does not
    reject private addresses.  It does reject non-unicast destination classes
    and, crucially, returns the exact sockaddr values used by connect(), avoiding
    a second DNS lookup between validation and the actual connection.
    """
    host = str(hostname or "").strip().rstrip(".")
    if not host:
        raise PortalError("Portal hostname is empty")
    try:
        infos = socket.getaddrinfo(host, int(port), 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise PortalError("Portal hostname could not be resolved: %s" % exc)
    endpoints = []
    seen = set()
    for family, socktype, proto, _canonname, sockaddr in infos:
        key = (family, socktype, proto, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            raw = str(sockaddr[0]).split("%", 1)[0]
            address = ipaddress.ip_address(raw)
        except (ValueError, TypeError, IndexError):
            raise PortalError("Portal hostname resolved to an invalid address")
        if address.is_unspecified or address.is_multicast:
            raise PortalError("Portal hostname resolved to a blocked destination class")
        endpoints.append((family, socktype or socket.SOCK_STREAM, proto, sockaddr))
    if not endpoints:
        raise PortalError("Portal hostname resolved to no usable address")
    return tuple(endpoints)


class _PinnedPortalSocketMixin(object):
    """Connect to pre-resolved portal endpoints without resolving DNS again."""
    def __init__(self, *args, **kwargs):
        self._portal_endpoints = tuple(kwargs.pop("portal_endpoints", ()) or ())
        super(_PinnedPortalSocketMixin, self).__init__(*args, **kwargs)

    def _connect_pinned_socket(self):
        last_error = None
        for family, socktype, proto, sockaddr in self._portal_endpoints:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(self.timeout)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        if last_error is not None:
            raise last_error
        raise OSError("No pinned portal address available")


class _PinnedPortalHTTPConnection(_PinnedPortalSocketMixin, http.client.HTTPConnection):
    def connect(self):
        self.sock = self._connect_pinned_socket()
        if self._tunnel_host:
            self._tunnel()


class _PinnedPortalHTTPSConnection(_PinnedPortalSocketMixin, http.client.HTTPSConnection):
    def connect(self):
        self.sock = self._connect_pinned_socket()
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class PortalError(PortalConnectionError):
    """Backward-compatible protocol error base."""
    code = "PORTAL"


class PortalAuthError(PortalError, PortalAuthenticationError):
    code = "PORTAL_AUTH"


class PortalThrottleError(PortalError, PortalRateLimitError):
    code = "PORTAL_RATE_LIMIT"


class PortalDataError(PortalError, PortalResponseError):
    code = "PORTAL_RESPONSE"


class PortalLinkError(PortalError, PlaybackLinkError):
    code = "PLAYBACK_LINK"


class PortalSecurityConsentError(PortalError):
    code = "PORTAL_SECURITY_CONSENT"


class PortalCancelledError(PortalError):
    code = "PORTAL_CANCELLED"


def _bounded_gzip_decompress(payload, limit):
    """Incrementally inflate gzip data without ever allocating beyond ``limit``."""
    limit = max(1, int(limit))
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    try:
        for offset in range(0, len(payload), 64 * 1024):
            chunk = payload[offset:offset + 64 * 1024]
            remaining = limit + 1 - len(out)
            if remaining <= 0:
                raise PortalDataError("Expanded portal response is too large")
            out.extend(inflater.decompress(chunk, remaining))
            if len(out) > limit or inflater.unconsumed_tail:
                raise PortalDataError("Expanded portal response is too large")
        remaining = limit + 1 - len(out)
        out.extend(inflater.flush(max(0, remaining)))
        if len(out) > limit:
            raise PortalDataError("Expanded portal response is too large")
        return bytes(out)
    except PortalDataError:
        raise
    except zlib.error as exc:
        raise PortalDataError("Portal returned invalid gzip data: %s" % exc)


class StalkerClient:
    MAX_RESPONSE_BYTES = 16 * 1024 * 1024
    RETRY_CODES = (408, 429, 500, 502, 503, 504)
    RETRY_DELAYS = (0.35, 0.8)
    CIRCUIT_FAILURE_LIMIT = 5
    CIRCUIT_COOLDOWN = 60
    _circuit_state = {}
    _latency_state = {}

    _PLAYABLE_SCHEMES = ("http", "https", "rtmp", "rtsp", "udp", "rtp")
    MAX_CATALOG_PAGES = 250
    MAX_CATALOG_ITEMS = 25000
    SEARCH_CACHE_TTL = 90
    TLS_MODES = ("auto", "strict", "compatible")
    DEVICE_PROFILES = {
        "mag250": {
            "model": "MAG250", "x_user_agent": "Model: MAG250; Link: WiFi",
            "user_agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG250 stbapp ver: 2 rev: 250 Safari/533.3",
            "ver": "ImageDescription: 0.2.18-r23-250; PORTAL version: 5.3.0; API Version: JS API version: 343; STB API version: 146; Player Engine version: 0x58c",
            "image_version": "218", "hw_version": "1.7-BD-00", "api_signature": "262",
        },
        "mag254": {
            "model": "MAG254", "x_user_agent": "Model: MAG254; Link: Ethernet",
            "user_agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG254 stbapp ver: 4 rev: 272 Safari/533.3",
            "ver": "ImageDescription: 0.2.18-r23-254; PORTAL version: 5.3.0; API Version: JS API version: 343; STB API version: 146; Player Engine version: 0x58c",
            "image_version": "218", "hw_version": "2.6-IB-00", "api_signature": "262",
        },
        "mag256": {
            "model": "MAG256", "x_user_agent": "Model: MAG256; Link: Ethernet",
            "user_agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/537.4 (KHTML, like Gecko) MAG256 stbapp Safari/537.4",
            "ver": "ImageDescription: 2.20.03-256; PORTAL version: 5.3.0; API Version: JS API version: 343; STB API version: 146",
            "image_version": "220", "hw_version": "1.0-BD-00", "api_signature": "263",
        },
    }

    def __init__(self, portal, mac, timeout=10, allow_http_fallback=False, tls_mode="auto", device_profile="auto",
                 allow_tls_fallback=False, http_fallback_accepted=False):
        self.entry_url = self._normalize_entry_url(portal)
        # HARDENED BUILD: never downgrade an HTTPS portal to HTTP and never
        # disable certificate verification, even when an old profile still
        # contains previously-approved compatibility flags. Explicit http://
        # portal URLs remain explicit user configuration; there is no fallback.
        self.http_fallback_available = False
        self.http_fallback_accepted = False
        self.allow_http_fallback = False
        self.allow_tls_fallback = False
        self.mac = self._normalize_mac(mac)
        self.timeout = max(3, min(int(timeout or 10), 30))
        self.security_warning = ""
        self.used_http_fallback = False
        self.used_unverified_tls = False
        # HARDENED BUILD: verified TLS only. "strict" is forced so stale
        # compatible/auto-fallback settings cannot re-enable unverified TLS.
        self.tls_mode = "strict"
        self.device_profile = str(device_profile or "auto").strip().lower()
        if self.device_profile not in tuple(self.DEVICE_PROFILES) + ("auto",):
            self.device_profile = "auto"
        self.resolved_device_profile = None

        self.host, self.path_prefix, self.referer = self._split_entry(self.entry_url)
        self._state_host = self.host
        self._state_entry_url = self.entry_url
        self.portal = self.host
        self.endpoint = None
        self.token = None
        self.token_random = ""
        self.play_token = ""
        self.profile_initialized = False
        self.account_valid = False

        self.cookies = http.cookiejar.CookieJar()
        self.opener = self._build_opener(verified=True)
        self._transport_local = threading.local()
        self._transport_lock = threading.RLock()
        self._transport_connections = {}
        self._transport_generation = 0
        self._server_cookies = {}
        self._server_cookie_lock = threading.RLock()
        # Compatible TLS cannot authenticate a self-signed/invalid certificate.
        # Pin the first certificate seen for each origin for this client session
        # so a later connection cannot silently switch certificates mid-session.
        self._compatible_tls_pins = {}
        self._persisted_compatible_tls_pins = {}
        # Stable series hierarchy cache. Some Stalker portals intermittently
        # return only a suffix/subset of seasons for the same movie_id.
        self._series_hierarchy_lock = threading.RLock()
        self._series_hierarchy_memory = {}; self._series_hierarchy_memory_at={}
        self._timezone = self._local_timezone()
        self._persisted_endpoint = None
        self._persisted_device_profile = None
        self._load_transport_state()


    @staticmethod
    def _tls_context(verified=True):
        if verified:
            context = ssl.create_default_context()
        else:
            # HARDENED BUILD: callers are not permitted to request an
            # unauthenticated TLS context. Fail closed instead.
            raise PortalSecurityConsentError("Unverified TLS is disabled in this hardened build")
        try:
            if hasattr(ssl, "TLSVersion"):
                context.minimum_version = ssl.TLSVersion.TLSv1_2
        except (AttributeError, ValueError):
            pass
        try:
            context.options |= getattr(ssl, "OP_NO_COMPRESSION", 0)
        except (AttributeError, TypeError):
            pass
        return context

    def _build_opener(self, verified=True):
        context = self._tls_context(verified=verified)
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=context),
        )

    @staticmethod
    def _is_certificate_error(reason):
        if isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return True
        text = str(reason or "").lower()
        return "certificate_verify_failed" in text or "certificate verify failed" in text or "self signed certificate" in text

    def _enable_compatible_tls(self):
        # HARDENED BUILD: compatibility TLS is deliberately unavailable.
        raise PortalSecurityConsentError("TLS certificate verification failed; unverified TLS is disabled in this hardened build")

    def _pin_compatible_tls_peer(self, conn, hostname, port):
        if not self.used_unverified_tls and self.tls_mode != "compatible":
            return
        sock = getattr(conn, "sock", None)
        if sock is None:
            raise PortalError("Compatible TLS connection did not expose a peer certificate")
        cert = sock.getpeercert(binary_form=True)
        if not cert:
            raise PortalError("Compatible TLS peer did not provide a certificate")
        fingerprint = hashlib.sha256(cert).hexdigest()
        key = "%s:%s" % (str(hostname or "").lower(), int(port or 443))
        previous = self._compatible_tls_pins.get(key)
        if previous is not None and previous != fingerprint:
            self._close_transport_socket(conn)
            raise PortalSecurityConsentError("Portal TLS certificate changed during the session; connection blocked by hardened TLS policy")
        if previous is None:
            self._compatible_tls_pins[key] = fingerprint
            LOG.warning("Compatible TLS certificate pinned for %s sha256=%s", key, fingerprint[:16])
            self._persist_transport_state()

    def _transport_state_key(self):
        raw = (self._state_host.rstrip("/").lower() + "|" + self.mac.upper()).encode("utf-8", "ignore")
        return "transport:" + hashlib.sha256(raw).hexdigest()

    def _load_transport_state(self):
        """Reuse only non-secret transport discoveries from prior sessions."""
        try:
            state = DB.state_get(self._transport_state_key())
        except Exception:
            state = {}
        if not isinstance(state, dict):
            return
        endpoint = str(state.get("endpoint") or "").strip()
        if endpoint:
            try:
                ep = urllib.parse.urlsplit(endpoint)
                host = urllib.parse.urlsplit(self.host)
                same_origin = ep.netloc == host.netloc and ep.path.lower().endswith((".php", "/load.php"))
                approved_downgrade = ep.scheme == "http" and host.scheme == "https" and self.allow_http_fallback
                if same_origin and (ep.scheme == host.scheme or approved_downgrade):
                    if approved_downgrade:
                        self.host = "http://" + self.host[8:]
                        self.entry_url = "http://" + self.entry_url[8:]
                        self.referer = "http://" + self.referer[8:]
                        self.used_http_fallback = True
                        self.security_warning = "WARNING: HTTPS unavailable; using approved insecure HTTP"
                    self.endpoint = endpoint.rstrip("/")
                    self._persisted_endpoint = self.endpoint
            except Exception as exc:
                LOG.debug("Persisted transport endpoint could not be restored: %s", exc)
        profile = str(state.get("device_profile") or "").lower()
        if profile in self.DEVICE_PROFILES:
            self.resolved_device_profile = profile
            self._persisted_device_profile = profile
        pins = state.get("compatible_tls_pins")
        if isinstance(pins, dict):
            clean_pins = {}
            for key, fingerprint in pins.items():
                key = str(key or "").strip().lower()
                fingerprint = str(fingerprint or "").strip().lower()
                if key and len(fingerprint) == 64 and all(ch in "0123456789abcdef" for ch in fingerprint):
                    clean_pins[key] = fingerprint
            self._compatible_tls_pins.update(clean_pins)
            self._persisted_compatible_tls_pins = dict(clean_pins)

    def _persist_transport_state(self):
        endpoint = str(self.endpoint or "")
        profile = str(self.resolved_device_profile or "")
        pins = dict(self._compatible_tls_pins)
        if (endpoint == self._persisted_endpoint and profile == self._persisted_device_profile
                and pins == self._persisted_compatible_tls_pins):
            return
        payload = {
            "portal": self._state_entry_url,
            "mac": self.mac,
            "endpoint": endpoint,
            "device_profile": profile if profile in self.DEVICE_PROFILES else "",
            "compatible_tls_pins": pins,
        }
        try:
            DB.state_put(self._transport_state_key(), payload)
            self._persisted_endpoint = endpoint
            self._persisted_device_profile = payload["device_profile"]
            self._persisted_compatible_tls_pins = dict(pins)
        except Exception as exc:
            LOG.debug("Could not persist portal transport state: %s", exc)


    @classmethod
    def clear_persisted_tls_pins(cls, portal, mac):
        """Forget compatible-TLS TOFU pins after an explicit security-mode change."""
        try:
            entry = cls._normalize_entry_url(portal)
            normalized_mac = cls._normalize_mac(mac)
            host, _prefix, _referer = cls._split_entry(entry)
            raw = (host.rstrip("/").lower() + "|" + normalized_mac.upper()).encode("utf-8", "ignore")
            key = "transport:" + hashlib.sha256(raw).hexdigest()
            state = DB.state_get(key)
            if not isinstance(state, dict):
                state = {}
            if state.get("compatible_tls_pins"):
                state["compatible_tls_pins"] = {}
                DB.state_put(key, state)
                LOG.warning("Compatible TLS certificate approval reset for %s", host)
            return True
        except Exception as exc:
            LOG.debug("Could not reset compatible TLS certificate approval: %s", exc)
            return False

    # ------------------------------------------------------------------
    # URL / identity helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_entry_url(portal):
        value = str(portal or "").strip()
        if not value:
            raise PortalError("Portal URL is empty")
        if not value.lower().startswith(("http://", "https://")):
            value = "https://" + value
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise PortalError("Invalid portal URL")
        if parsed.username or parsed.password:
            raise PortalError("Credentials in portal URL are not supported")
        if len(value) > 2048:
            raise PortalError("Portal URL is too long")
        clean_path = re.sub(r"/+", "/", parsed.path or "/")
        return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, clean_path, "", "")).rstrip("/")

    @staticmethod
    def _normalize_mac(mac):
        value = str(mac or "").strip().upper().replace("-", ":")
        if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", value):
            raise PortalError("Invalid MAC format")
        return value

    @staticmethod
    def _split_entry(entry):
        parsed = urllib.parse.urlsplit(entry)
        host = "%s://%s" % (parsed.scheme, parsed.netloc)
        path = (parsed.path or "").rstrip("/")
        if path.endswith("/stalker_portal/c"):
            prefix = "/stalker_portal/c/"
        elif path.endswith("/c"):
            prefix = "/c/"
        elif "/stalker_portal/" in path:
            prefix = "/stalker_portal/c/"
        else:
            prefix = "/c/"
        return host, prefix, host + prefix

    def _portal_key(self):
        return (self.host.lower(), self.mac)

    def _candidate_endpoints(self):
        parsed = urllib.parse.urlsplit(self.entry_url)
        entry_path = parsed.path or ""
        candidates = []
        if self.endpoint:
            candidates.append(self.endpoint)
        if entry_path.lower().endswith((".php", "/load.php")):
            candidates.append(self.entry_url)

        if self.path_prefix == "/stalker_portal/c/":
            candidates.extend([
                self.host + "/stalker_portal/server/load.php",
                self.host + "/stalker_portal/portal.php",
                self.host + "/portal.php",
                self.host + "/server/load.php",
            ])
        else:
            # /c/ portals normally resolve to /portal.php. Prefer that exact
            # order because some servers return HTML from /server/load.php.
            candidates.extend([
                self.host + "/portal.php",
                self.host + "/server/load.php",
                self.host + "/stalker_portal/server/load.php",
                self.host + "/stalker_portal/portal.php",
            ])

        # Custom subdirectory portals occasionally place the loader next to /c/.
        base_path = entry_path
        if base_path.endswith("/c"):
            base_path = base_path[:-2]
        elif base_path.endswith("/stalker_portal/c"):
            base_path = base_path[:-2]
        base_path = base_path.rstrip("/")
        if base_path and base_path != "/stalker_portal":
            candidates.extend([
                self.host + base_path + "/portal.php",
                self.host + base_path + "/server/load.php",
            ])

        result = []
        for item in candidates:
            item = re.sub(r"(?<!:)//+", "/", item)
            if item not in result:
                result.append(item)
        return result

    # ------------------------------------------------------------------
    # HTTP / session
    # ------------------------------------------------------------------
    @staticmethod
    def _local_timezone():
        try:
            with open("/etc/timezone", "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if "/" in value and not value.startswith("#"):
                return value
        except (OSError, UnicodeError):
            pass
        return time.tzname[0] if time.tzname else "Europe/London"

    def _profile_spec(self, profile_name=None):
        key = str(profile_name or self.resolved_device_profile or self.device_profile or "auto").lower()
        if key == "auto":
            key = "mag254" if (self.path_prefix == "/stalker_portal/c/" or (self.endpoint and "/stalker_portal/" in self.endpoint)) else "mag250"
        return self.DEVICE_PROFILES.get(key, self.DEVICE_PROFILES["mag250"])

    def _headers(self):
        parsed = urllib.parse.urlsplit(self.host)
        encoded_mac = urllib.parse.quote(self.mac, safe="")
        encoded_tz = urllib.parse.quote(self._timezone, safe="")
        with self._server_cookie_lock:
            cookies = dict(self._server_cookies)
        cookies.update({"mac": encoded_mac, "stb_lang": "en", "timezone": encoded_tz})
        if self.token:
            cookies["token"] = urllib.parse.quote(str(self.token), safe="")
        cookie = "; ".join("%s=%s" % (key, value) for key, value in cookies.items() if key and value is not None)
        spec = self._profile_spec()
        headers = {
            "Pragma": "no-cache",
            "Accept": "*/*",
            "Host": parsed.netloc,
            "User-Agent": spec["user_agent"],
            "X-User-Agent": spec["x_user_agent"],
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip",
            "Referer": self.referer,
            "Cookie": cookie,
        }
        if self.token:
            headers["Authorization"] = "Bearer " + str(self.token)
        return headers

    @staticmethod
    def _unwrap(data):
        if isinstance(data, dict) and "js" in data:
            return data.get("js")
        return data

    @staticmethod
    def _as_list(js):
        if isinstance(js, list):
            return js
        if isinstance(js, dict):
            for key in ("data", "items", "episodes", "seasons", "series"):
                value = js.get(key)
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def _int_value(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return int(default)

    @staticmethod
    def _item_identity(row):
        if not isinstance(row, dict):
            return str(row)
        return str(row.get("id") or row.get("ch_id") or row.get("movie_id") or row.get("series_id") or row.get("cmd") or row.get("name") or row.get("title") or repr(sorted(row.items())))

    def _adaptive_timeout(self):
        samples = self._latency_state.get(self._portal_key(), [])
        if not samples:
            return self.timeout
        avg_ms = sum(samples) / float(len(samples))
        return int(max(5, min(18, (avg_ms / 1000.0) * 4.0 + 3.0)))

    def _record_success(self, elapsed_ms):
        key = self._portal_key()
        samples = list(self._latency_state.get(key, []))[-4:]
        samples.append(max(1, int(elapsed_ms)))
        self._latency_state[key] = samples
        self._circuit_state[key] = {"failures": 0, "opened_at": 0}

    def _record_failure(self):
        key = self._portal_key()
        state = dict(self._circuit_state.get(key, {"failures": 0, "opened_at": 0}))
        state["failures"] = int(state.get("failures", 0)) + 1
        if state["failures"] >= self.CIRCUIT_FAILURE_LIMIT:
            state["opened_at"] = time.monotonic()
        self._circuit_state[key] = state

    def reset_failure_backoff(self):
        """Clear only transient request backoff for this portal.

        UI navigation and preview cancellation are expected control flow and must
        never strand the whole portal behind the circuit breaker.
        Authorization/cookies remain untouched.
        """
        self._circuit_state[self._portal_key()] = {"failures": 0, "opened_at": 0}

    def _check_circuit(self):
        state = self._circuit_state.get(self._portal_key()) or {}
        opened_at = float(state.get("opened_at", 0) or 0)
        if not opened_at:
            return
        # Keep protection against a genuinely dead portal, but never lock the
        # interactive browser for a full minute.  After a very short quiet
        # period allow one normal request to recover the connection; success
        # resets the state in _record_success().
        age = time.monotonic() - opened_at
        if age < 2.0:
            raise PortalError("Portal temporarily paused after repeated failures (%ds)" % max(1, int(2.0-age+0.999)))
        self._circuit_state[self._portal_key()] = {"failures": 0, "opened_at": 0}

    def _cache_key(self, params):
        return CACHE.key(self.host, self.mac, params)

    def _cache_get(self, params):
        return CACHE.get(self._cache_key(params))

    def _cache_put(self, params, value):
        CACHE.put(self._cache_key(params), value, CachePolicy.ttl(params.get("action"), 90))

    def _remember_response_cookies(self, headers):
        try:
            values = headers.get_all("Set-Cookie") or []
        except Exception:
            value = headers.get("Set-Cookie") if headers is not None else None
            values = [value] if value else []
        if not values:
            return
        with self._server_cookie_lock:
            for raw in values:
                try:
                    jar = http.cookies.SimpleCookie(); jar.load(str(raw))
                    for key, morsel in jar.items():
                        if key.lower() not in ("mac", "token", "stb_lang", "timezone"):
                            self._server_cookies[key] = morsel.value
                except Exception:
                    pass

    def _set_current_transport(self, key, conn):
        self._transport_local.connection=(key,conn)
        with self._transport_lock:
            self._transport_connections[threading.get_ident()] = (key,conn)

    @staticmethod
    def _close_transport_socket(conn):
        try:
            sock=getattr(conn,"sock",None)
            if sock is not None: sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try: conn.close()
        except OSError: pass

    def _drop_current_transport(self, close=True):
        ident=threading.get_ident(); entry=getattr(self._transport_local,"connection",None)
        if close and entry:
            self._close_transport_socket(entry[1])
        self._transport_local.connection=None
        with self._transport_lock:
            current=self._transport_connections.get(ident)
            if current is not None and (entry is None or current[1] is entry[1]):
                self._transport_connections.pop(ident,None)

    def close(self):
        """Release every persistent socket owned by this portal client."""
        with self._transport_lock:
            entries=list(self._transport_connections.values())
            self._transport_connections.clear()
            self._transport_generation += 1
        seen=set()
        for _key,conn in entries:
            marker=id(conn)
            if marker in seen: continue
            seen.add(marker)
            self._close_transport_socket(conn)
        self._transport_local.connection=None

    def cancel_pending_requests(self):
        """Interrupt active sockets without discarding authorization state."""
        self.close()

    def _persistent_http_get(self, url, headers, timeout, cancel_event=None, _redirects=0):
        try:
            validate_http_url(url)
        except urllib.error.URLError as exc:
            raise PortalError("Invalid portal URL: %s" % exc)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise PortalError("Unsupported portal transport")
        verified = not self.used_unverified_tls and self.tls_mode != "compatible"
        generation = self._transport_generation
        key = (generation, parsed.scheme, parsed.netloc, bool(verified))
        entry = getattr(self._transport_local, "connection", None)
        conn = entry[1] if entry and entry[0] == key else None
        if conn is None:
            if entry:
                self._drop_current_transport(close=True)
            if parsed.scheme == "https":
                context = self._tls_context(verified=verified)
                conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout, context=context)
                if not verified:
                    conn.connect()
                    self._pin_compatible_tls_peer(conn, parsed.hostname, parsed.port or 443)
            else:
                conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
            self._set_current_transport(key, conn)
            PERF.increment("http.connections")
        else:
            try:
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
            except Exception:
                pass
            self._set_current_transport(key, conn)
            PERF.increment("http.keepalive_reuse")
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise PortalCancelledError("Request cancelled")
            conn.request("GET", target, headers=headers)
            response = conn.getresponse()
            if generation != self._transport_generation:
                raise PortalCancelledError("Request cancelled")
            self._remember_response_cookies(response.headers)
            chunks=[]; total=0
            while True:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    self._drop_current_transport(close=True)
                    raise PortalCancelledError("Request cancelled")
                chunk=response.read(min(64*1024, self.MAX_RESPONSE_BYTES + 1 - total))
                if generation != self._transport_generation:
                    raise PortalCancelledError("Request cancelled")
                if not chunk: break
                total += len(chunk); chunks.append(chunk)
                if total > self.MAX_RESPONSE_BYTES:
                    raise PortalDataError("Portal response is too large")
            payload=b"".join(chunks)
            status=int(response.status or 0)
            if response.will_close or str(response.headers.get("Connection", "")).lower() == "close":
                self._drop_current_transport(close=True)
            if status in (301,302,303,307,308):
                location=str(response.headers.get("Location") or "").strip()
                if not location or _redirects >= 4:
                    raise PortalError("Portal redirect could not be resolved")
                redirected=urllib.parse.urljoin(url,location)
                try:
                    validate_http_url(redirected)
                except urllib.error.URLError as exc:
                    raise PortalError("Invalid portal redirect: %s" % exc)
                target_parts=urllib.parse.urlsplit(redirected)
                source_scheme=str(parsed.scheme or "").lower()
                target_scheme=str(target_parts.scheme or "").lower()
                source_host=str(parsed.hostname or "").lower()
                target_host=str(target_parts.hostname or "").lower()
                source_port=parsed.port or (443 if source_scheme == "https" else 80 if source_scheme == "http" else None)
                target_port=target_parts.port or (443 if target_scheme == "https" else 80 if target_scheme == "http" else None)
                approved_downgrade=(source_scheme == "https" and target_scheme == "http" and self.allow_http_fallback)
                same_origin=(source_scheme == target_scheme and source_host == target_host and source_port == target_port)
                approved_http_origin=(approved_downgrade and source_host == target_host and target_port == 80)
                if not (same_origin or approved_http_origin):
                    raise PortalError("Portal redirect changed the approved origin")
                if source_scheme == "https" and target_scheme == "http" and not self.allow_http_fallback:
                    raise PortalSecurityConsentError("Portal attempted an unapproved HTTPS to HTTP redirect")
                redirect_headers=dict(headers)
                redirect_headers["Host"]=target_parts.netloc
                redirect_headers["Referer"]=url
                PERF.increment("http.redirects")
                return self._persistent_http_get(redirected,redirect_headers,timeout,cancel_event=cancel_event,_redirects=_redirects+1)
            if status >= 400:
                raise urllib.error.HTTPError(url, status, response.reason, response.headers, None)
            return payload, response.headers
        except PortalError:
            self._drop_current_transport(close=True)
            raise
        except Exception:
            self._drop_current_transport(close=True)
            raise

    def _request_endpoint(self, endpoint, params, extra_headers=None, cancel_event=None):
        self._check_circuit()
        url = endpoint + "?" + urllib.parse.urlencode(params)
        attempts = len(self.RETRY_DELAYS) + 1
        last_error = None
        attempt = 0
        while attempt < attempts:
            headers = self._headers()
            if extra_headers:
                headers.update(extra_headers)
            retryable = False
            try:
                started = time.monotonic()
                payload, response_headers = self._persistent_http_get(url, headers, self._adaptive_timeout(), cancel_event=cancel_event)
                if str(response_headers.get("Content-Encoding", "")).lower() == "gzip":
                    payload = _bounded_gzip_decompress(payload, self.MAX_RESPONSE_BYTES)
                raw = payload.decode("utf-8-sig", "replace").strip()
                elapsed_ms = int((time.monotonic() - started) * 1000)
                PERF.record("http.request_ms", elapsed_ms)
                PERF.increment("http.requests")
                self._record_success(elapsed_ms)
                try:
                    return json.loads(raw)
                except (ValueError, TypeError):
                    snippet = re.sub(r"\s+", " ", raw)[:110]
                    raise PortalDataError("Portal returned non-JSON data%s" % ((": " + snippet) if snippet else ""))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    last_error = PortalAuthError("HTTP %s authorization failed" % exc.code)
                elif exc.code == 429:
                    last_error = PortalThrottleError("HTTP 429 rate limit")
                else:
                    last_error = PortalError("HTTP %s" % exc.code)
                retryable = exc.code in self.RETRY_CODES
            except ssl.SSLCertVerificationError as exc:
                if self.tls_mode == "auto" and not self.used_unverified_tls and self.allow_tls_fallback:
                    self._enable_compatible_tls()
                    continue
                last_error = PortalSecurityConsentError(
                    "TLS certificate verification failed. This hardened build does not allow unverified TLS."
                )
                retryable = False
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                if (self.tls_mode == "auto" and not self.used_unverified_tls and self.host.startswith("https://")
                        and self._is_certificate_error(reason)):
                    if self.allow_tls_fallback:
                        self._enable_compatible_tls()
                        # Retry the exact same request immediately without consuming
                        # a network retry slot: this is a transport-mode fallback.
                        continue
                    last_error = PortalSecurityConsentError(
                        "TLS certificate verification failed. This hardened build does not allow unverified TLS."
                    )
                    retryable = False
                else:
                    LOG.warning("Portal connection transport error endpoint=%s action=%s detail=%s", endpoint, params.get("action"), reason)
                    if isinstance(reason, socket.gaierror):
                        last_error = PortalError("Portal host could not be resolved")
                    elif isinstance(reason, ConnectionRefusedError):
                        last_error = PortalError("Portal connection was refused")
                    else:
                        last_error = PortalError("Portal connection failed")
                    retryable = isinstance(reason, (socket.timeout, TimeoutError, OSError))
            except (socket.timeout, TimeoutError):
                last_error = PortalError("Connection timed out")
                retryable = True
            except (http.client.HTTPException, ConnectionError, OSError) as exc:
                LOG.warning("Portal connection transport error endpoint=%s action=%s detail=%s", endpoint, params.get("action"), exc)
                if isinstance(exc, ConnectionRefusedError):
                    last_error = PortalError("Portal connection was refused")
                else:
                    last_error = PortalError("Portal connection failed")
                retryable = True
            except PortalError as exc:
                last_error = exc
                retryable = False
            except Exception as exc:
                LOG.exception("Unexpected portal request failure endpoint=%s action=%s", endpoint, params.get("action"))
                last_error = PortalError("Portal request failed")
                retryable = False
            if not retryable or attempt >= attempts - 1:
                break
            delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
            if cancel_event is not None and getattr(cancel_event, "wait", lambda _x: False)(delay):
                raise PortalCancelledError("Request cancelled")
            if cancel_event is None:
                time.sleep(delay)
            attempt += 1
        raise last_error or PortalError("Portal request failed")

    def _get_once(self, params, use_cache=False, extra_headers=None, cancel_event=None):
        if use_cache:
            cached = self._cache_get(params)
            if cached is not None:
                return cached
        errors = []
        security_consent_error = None
        for endpoint in self._candidate_endpoints():
            try:
                data = self._request_endpoint(endpoint, params, extra_headers=extra_headers, cancel_event=cancel_event)
                self.endpoint = endpoint
                self.portal = endpoint
                self._persist_transport_state()
                if use_cache:
                    self._cache_put(params, data)
                return data
            except PortalCancelledError:
                # A user moving between rows/pages cancels preview/page workers
                # deliberately.  This is not a portal failure and must not feed
                # the shared circuit breaker.
                raise
            except PortalSecurityConsentError as exc:
                security_consent_error = exc
                errors.append("%s: %s" % (endpoint, exc))
            except PortalError as exc:
                # Do not recursively count an already-open circuit as another
                # network failure.  Propagate it unchanged.
                if "temporarily paused after repeated failures" in str(exc).lower():
                    raise
                errors.append("%s: %s" % (endpoint, exc))

        if self.allow_http_fallback and not self.used_http_fallback and self.host.startswith("https://"):
            self.host = "http://" + self.host[8:]
            self.entry_url = "http://" + self.entry_url[8:]
            self.referer = "http://" + self.referer[8:]
            self.endpoint = None
            self.used_http_fallback = True
            self.security_warning = "WARNING: HTTPS unavailable; using insecure HTTP"
            return self._get_once(params, use_cache=use_cache, extra_headers=extra_headers, cancel_event=cancel_event)
        if security_consent_error is not None:
            self._record_failure()
            raise security_consent_error
        # HARDENED BUILD: HTTPS failures never trigger or offer HTTP fallback.

        self._record_failure()
        raise PortalError(errors[-1] if errors else "No usable portal endpoint")

    def _get(self, params, use_cache=False, retry_auth=True, extra_headers=None, cancel_event=None):
        try:
            data = self._get_once(params, use_cache=use_cache, extra_headers=extra_headers, cancel_event=cancel_event)
            unwrapped = self._unwrap(data)
            text = json.dumps(unwrapped, ensure_ascii=False).lower() if isinstance(unwrapped, (dict, list)) else str(unwrapped).lower()
            if retry_auth and params.get("action") != "handshake" and any(
                marker in text for marker in ("authorization failed", "not valid token", "token expired")
            ):
                self._reset_auth()
                self.authorize(cancel_event=cancel_event)
                return self._get_once(params, use_cache=False, extra_headers=extra_headers, cancel_event=cancel_event)
            return data
        except PortalError as exc:
            message = str(exc).lower()
            if retry_auth and params.get("action") != "handshake" and any(
                marker in message for marker in ("http 401", "http 403", "authorization", "token")
            ):
                self._reset_auth()
                self.authorize(cancel_event=cancel_event)
                return self._get_once(params, use_cache=False, extra_headers=extra_headers, cancel_event=cancel_event)
            raise

    def _reset_auth(self):
        self.token = None
        self.token_random = ""
        self.play_token = ""
        self.profile_initialized = False
        self.account_valid = False

    # ------------------------------------------------------------------
    # MAG authorization
    # ------------------------------------------------------------------
    def authorize(self, cancel_event=None):
        self._reset_auth()
        variants = [
            {"type": "stb", "action": "handshake", "token": "", "JsHttpRequest": "1-xml"},
            {"type": "stb", "action": "handshake", "token": "", "mac": self.mac, "JsHttpRequest": "1-xml"},
        ]
        last_error = None
        js = None
        for params in variants:
            try:
                js = self._unwrap(self._get(params, retry_auth=False, cancel_event=cancel_event))
                if isinstance(js, dict) and js.get("token"):
                    break
                msg = str(js.get("msg") or "") if isinstance(js, dict) else ""
                if "missing" in msg.lower():
                    fake_token = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(32))
                    prehash = hashlib.sha1(fake_token.encode("utf-8")).hexdigest()
                    fake_headers = {"Authorization": "Bearer " + fake_token}
                    js = self._unwrap(self._get({
                        "type": "stb", "action": "handshake", "JsHttpRequest": "1-xml",
                        "mac": self.mac, "prehash": prehash,
                    }, retry_auth=False, extra_headers=fake_headers, cancel_event=cancel_event))
                    if isinstance(js, dict) and js.get("token"):
                        break
            except PortalError as exc:
                last_error = exc

        token = js.get("token") if isinstance(js, dict) else None
        if not token:
            raise last_error or PortalError("Handshake did not return a token")
        self.token = str(token)
        self.token_random = str(js.get("random") or "")
        self._initialize_profile(cancel_event=cancel_event)
        self._validate_account(cancel_event=cancel_event)
        return self.token

    def _profile_identity(self):
        mac = self.mac
        sn = hashlib.md5(mac.encode("utf-8")).hexdigest().upper()[:13]
        device_id = hashlib.sha256(mac.encode("utf-8")).hexdigest().upper()
        device_id2 = hashlib.sha256(device_id.encode("utf-8")).hexdigest().upper()
        hw_version_2 = hashlib.sha1(mac.encode("utf-8")).hexdigest()
        prehash = hashlib.sha1((sn + mac).encode("utf-8")).hexdigest()
        signature = hashlib.sha256((device_id + device_id).encode("utf-8")).hexdigest().upper()
        return sn, device_id, device_id2, hw_version_2, prehash, signature

    def _full_profile_params(self, profile_name=None):
        sn, device_id, device_id2, hw_version_2, prehash, signature = self._profile_identity()
        spec = self._profile_spec(profile_name)
        model = spec["model"]
        in_stalker_path = bool(self.endpoint and "/stalker_portal/" in self.endpoint)
        metrics_data = {
            "type": "stb" if in_stalker_path else "STB",
            "model": model,
            "mac": self.mac,
            "sn": sn,
            "uid": device_id2 if in_stalker_path else "",
            "random": self.token_random if in_stalker_path else "",
        }
        metrics = urllib.parse.quote(json.dumps(metrics_data, separators=(",", ":")), safe="")
        return OrderedDict([
            ("type", "stb"), ("action", "get_profile"), ("JsHttpRequest", "1-xml"),
            ("hd", "1"), ("ver", spec["ver"]),
            ("num_banks", "2"), ("sn", sn), ("stb_type", model),
            ("client_type", "STB"), ("image_version", spec["image_version"]), ("video_out", "hdmi"),
            ("device_id", device_id), ("device_id2", device_id2), ("signature", signature),
            ("auth_second_step", "1"), ("hw_version", spec["hw_version"]),
            ("not_valid_token", "0"), ("metrics", metrics), ("hw_version_2", hw_version_2),
            ("timestamp", str(int(time.time()))), ("api_signature", spec["api_signature"]), ("prehash", prehash),
        ])

    def _basic_profile_params(self):
        sn, _device_id, _device_id2, _hw, _prehash, _sig = self._profile_identity()
        return OrderedDict([
            ("type", "stb"), ("action", "get_profile"), ("JsHttpRequest", "1-xml"),
            ("sn", sn), ("device_id", ""), ("timestamp", str(int(time.time()))),
        ])

    def _initialize_profile(self, cancel_event=None):
        if self.profile_initialized or not self.token:
            return
        last_error = None
        if self.device_profile == "auto":
            preferred = "mag254" if (self.path_prefix == "/stalker_portal/c/" or (self.endpoint and "/stalker_portal/" in self.endpoint)) else "mag250"
            profile_names = [preferred] + [x for x in ("mag250", "mag254", "mag256") if x != preferred]
        else:
            profile_names = [self.device_profile]
        attempts = [(name, self._full_profile_params(name)) for name in profile_names]
        attempts.append((None, self._basic_profile_params()))
        for name, params in attempts:
            try:
                self.resolved_device_profile = name or self.resolved_device_profile
                js = self._unwrap(self._get(params, retry_auth=False, cancel_event=cancel_event))
                if isinstance(js, dict):
                    msg = str(js.get("msg") or "")
                    self.play_token = str(js.get("play_token") or "")
                    if msg and not self.play_token and "error" in msg.lower():
                        continue
                    self.profile_initialized = True
                    self._persist_transport_state()
                    return
            except PortalError as exc:
                last_error = exc
        # Some older portals are token-only. Keep compatibility but record it.
        self.profile_initialized = True
        if last_error:
            LOG.warning("MAG profile fallback exhausted: %s", last_error)

    def _validate_account(self, cancel_event=None):
        try:
            js = self._unwrap(self._get({
                "type": "account_info", "action": "get_main_info", "JsHttpRequest": "1-xml"
            }, retry_auth=False, cancel_event=cancel_event))
            self.account_valid = isinstance(js, dict)
            if not self.account_valid:
                # Match mature clients: retry the lighter profile when account info
                # was not accepted after the full profile.
                self._get(self._basic_profile_params(), retry_auth=False, cancel_event=cancel_event)
        except PortalError:
            self.account_valid = False

    # ------------------------------------------------------------------
    # Portal data
    # ------------------------------------------------------------------
    def account_info(self):
        if not self.token:
            self.authorize()
        js = self._unwrap(self._get({
            "type": "account_info", "action": "get_main_info", "JsHttpRequest": "1-xml"
        }, use_cache=True))
        return js if isinstance(js, dict) else {"raw": js}

    def genres(self, media_type="itv", cancel_event=None):
        """Return categories exactly in the order supplied by the portal.

        Some portals reject requests without ``sortby``.  In that uncommon case
        we retry with the old compatibility value, but we never sort the result
        locally.
        """
        if not self.token:
            self.authorize(cancel_event=cancel_event)
        media_type = str(media_type or "itv").lower()
        actions = ("get_genres",) if media_type == "itv" else ("get_categories", "get_genres")
        errors = []
        for action in actions:
            for compatibility_sort in (False, True):
                try:
                    params = {"type": media_type, "action": action, "JsHttpRequest": "1-xml"}
                    if compatibility_sort:
                        params["sortby"] = "number"
                    js = self._unwrap(self._get(params, use_cache=True, cancel_event=cancel_event))
                    rows = self._as_list(js)
                    if rows or isinstance(js, list):
                        return rows if rows else js
                except PortalError as exc:
                    errors.append(str(exc))
        if errors:
            raise PortalError(errors[-1])
        return []

    def ordered_page(self, media_type="itv", genre="*", page=1, cancel_event=None):
        """Return one native portal page plus pagination metadata.

        No local sorting is performed.  The first request deliberately omits
        ``sortby`` so the portal's own bouquet/catalogue order is respected.
        """
        if not self.token:
            self.authorize(cancel_event=cancel_event)
        media_type = str(media_type or "itv").lower()
        last_error = None
        for compatibility_sort in (False, True):
            params = {
                "type": media_type, "action": "get_ordered_list",
                "p": int(page), "JsHttpRequest": "1-xml",
            }
            if compatibility_sort:
                params["sortby"] = "number"
            if media_type == "itv":
                params["genre"] = genre
            elif media_type == "vod":
                params["category"] = genre
            elif media_type == "series":
                params.update({
                    "movie_id": "0", "season_id": "0", "episode_id": "0", "category": genre
                })
            try:
                js = self._unwrap(self._get(params, use_cache=True, cancel_event=cancel_event))
                rows = [row for row in self._as_list(js) if isinstance(row, dict) and row]
                meta = js if isinstance(js, dict) else {}
                result = {
                    "items": rows,
                    "page": self._int_value(meta.get("cur_page") or meta.get("page") or page or 1, page or 1),
                    "page_size": max(1, self._int_value(meta.get("max_page_items") or meta.get("page_size") or len(rows) or 1, len(rows) or 1)),
                    "total": max(0, self._int_value(meta.get("total_items") or meta.get("total") or 0, 0)),
                }
                if rows or not compatibility_sort:
                    return result
            except PortalError as exc:
                last_error = exc
        if last_error:
            raise last_error
        return {"items": [], "page": int(page), "page_size": 1, "total": 0}

    def ordered_list(self, media_type="itv", genre="*", page=1, cancel_event=None):
        return self.ordered_page(media_type, genre, page, cancel_event=cancel_event).get("items", [])

    def ordered_all(self, media_type="itv", genre="*", start_page=1, max_pages=None, max_items=None, cancel_event=None):
        """Collect a portal catalogue across native pages with loop protection."""
        max_pages = max(1, min(self._int_value(max_pages, self.MAX_CATALOG_PAGES), self.MAX_CATALOG_PAGES))
        max_items = max(1, min(self._int_value(max_items, self.MAX_CATALOG_ITEMS), self.MAX_CATALOG_ITEMS))
        page = max(1, self._int_value(start_page, 1))
        out, seen = [], set()
        for _ in range(max_pages):
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                break
            payload = (self.ordered_page(media_type, genre, page, cancel_event=cancel_event)
                       if cancel_event is not None else self.ordered_page(media_type, genre, page))
            rows = payload.get("items") or []
            if not rows:
                break
            added = 0
            for row in rows:
                marker = self._item_identity(row)
                if marker in seen:
                    continue
                seen.add(marker)
                out.append(row)
                added += 1
                if len(out) >= max_items:
                    return out
            if not added:
                break
            total = max(0, self._int_value(payload.get("total"), 0))
            page_size = max(1, self._int_value(payload.get("page_size"), len(rows) or 1))
            if total and len(out) >= total:
                break
            if not total and len(rows) < page_size:
                break
            page += 1
        return out

    def _search_params(self, media_type, page, key=None, query=None):
        params = {"type": media_type, "action": "get_ordered_list", "p": int(page), "JsHttpRequest": "1-xml"}
        if media_type == "itv":
            params["genre"] = "*"
        elif media_type == "vod":
            params["category"] = "*"
        else:
            params.update({"movie_id": "0", "season_id": "0", "episode_id": "0", "category": "*"})
        if key:
            params[key] = str(query or "")
        return params

    def _search_catalogue(self, media_type, query, needle, limit, max_pages, cancel_event=None, deadline=None):
        """Search one catalogue with native search then a bounded full scan."""
        def stopped():
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                return True
            return deadline is not None and time.monotonic() >= deadline

        selected_key = None
        first_payload = None
        for key in ("search", "search_str", "name"):
            if stopped(): return []
            try:
                js = self._unwrap(self._get(self._search_params(media_type, 1, key, query), use_cache=False, cancel_event=cancel_event))
                rows = self._as_list(js)
                if rows:
                    selected_key, first_payload = key, js
                    break
            except PortalCancelledError:
                return []
            except PortalError as exc:
                LOG.debug("Portal search variant failed type=%s key=%s: %s", media_type, key, exc)
        pages = first_payload if first_payload is not None else None
        result, seen = [], set(); page = 1
        for _ in range(max_pages):
            if stopped(): break
            if page == 1 and pages is not None:
                js = pages
            else:
                try:
                    params = self._search_params(media_type, page, selected_key, query) if selected_key else self._search_params(media_type, page)
                    js = self._unwrap(self._get(params, use_cache=not bool(selected_key), cancel_event=cancel_event))
                except PortalCancelledError:
                    break
                except PortalError:
                    break
            rows = [row for row in self._as_list(js) if isinstance(row, dict)]
            if not rows: break
            new_page = 0
            for row in rows:
                if stopped(): break
                marker = self._item_identity(row)
                if marker in seen: continue
                seen.add(marker); new_page += 1
                title = str(row.get("name") or row.get("title") or row.get("id") or "")
                hay = (title + " " + str(row.get("description") or row.get("descr") or "")).casefold()
                if needle not in hay: continue
                item = dict(row); item["_search_media_type"] = media_type; result.append(item)
                if len(result) >= limit: return result
            if stopped() or not new_page: break
            meta = js if isinstance(js, dict) else {}
            total = max(0, self._int_value(meta.get("total_items") or meta.get("total"), 0))
            page_size = max(1, self._int_value(meta.get("max_page_items") or meta.get("page_size"), len(rows) or 1))
            if total and len(seen) >= total: break
            if not total and len(rows) < page_size: break
            page += 1
        return result

    def search_content(self, query, media_types=("itv", "vod", "series"), limit=120, max_pages=None, cancel_event=None, time_budget=12):
        """Unified search with cache, cancellation and an overall time budget."""
        started=time.monotonic()
        needle = str(query or "").strip().casefold()
        if not needle: return []
        limit = max(1, self._int_value(limit, 120))
        max_pages = max(1, min(self._int_value(max_pages, self.MAX_CATALOG_PAGES), self.MAX_CATALOG_PAGES))
        budget=max(1.0,min(30.0,float(time_budget or 12)))
        media_types=tuple(str(x).lower() for x in media_types)
        cache_params={"action":"search_content","q":needle,"types":",".join(media_types),"limit":limit,"pages":max_pages,"budget":int(budget)}
        cache_key=self._cache_key(cache_params)
        cached=CACHE.get(cache_key)
        if isinstance(cached,list):
            PERF.increment("search.cache_hits")
            return cached
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)(): return []
        deadline=started+budget
        if not self.token:
            self.authorize(cancel_event=cancel_event)
        result, seen = [], set()
        for media_type in media_types:
            if time.monotonic() >= deadline or (cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)()): break
            remaining = limit - len(result)
            if remaining <= 0: break
            rows = self._search_catalogue(media_type, query, needle, remaining, max_pages, cancel_event=cancel_event, deadline=deadline)
            for item in rows:
                marker = (media_type, self._item_identity(item))
                if marker in seen: continue
                seen.add(marker); result.append(item)
                if len(result) >= limit: break
            if len(result) >= limit: break
        if not (cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)()):
            CACHE.put(cache_key,result,self.SEARCH_CACHE_TTL)
        PERF.record("search.total_ms",(time.monotonic()-started)*1000.0)
        PERF.increment("search.executions")
        return result

    def epg(self, channel_id, hours=4, cancel_event=None):
        if not self.token:
            self.authorize(cancel_event=cancel_event)
        js = self._unwrap(self._get({
            "type": "itv", "action": "get_short_epg", "ch_id": channel_id,
            "size": max(1, min(24, int(hours or 4))), "JsHttpRequest": "1-xml",
        }, use_cache=True, cancel_event=cancel_event))
        return self._as_list(js)

    def full_epg(self, channel_id, from_ts=None, to_ts=None, page=1, max_pages=120, cancel_event=None):
        """Return EPG rows across all portal pages, with repeated-page protection."""
        if not self.token:
            self.authorize(cancel_event=cancel_event)
        errors = []
        start_page = max(1, self._int_value(page, 1))
        max_pages = max(1, min(self._int_value(max_pages, 120), self.MAX_CATALOG_PAGES))
        for action in ("get_epg_info", "get_epg", "get_short_epg"):
            collected, seen = [], set()
            current_page = start_page
            for _ in range(max_pages):
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    return collected
                params = {
                    "type": "itv", "action": action, "ch_id": channel_id,
                    "p": int(current_page), "JsHttpRequest": "1-xml",
                }
                if from_ts is not None:
                    params["from"] = int(from_ts)
                if to_ts is not None:
                    params["to"] = int(to_ts)
                try:
                    js = self._unwrap(self._get(params, use_cache=True, cancel_event=cancel_event))
                except PortalError as exc:
                    errors.append(str(exc))
                    break
                rows = [row for row in self._as_list(js) if isinstance(row, dict)]
                if not rows:
                    break
                added = 0
                for row in rows:
                    start_value = self._int_value(row.get("start_timestamp") or row.get("start") or row.get("time"), 0)
                    duration = self._int_value(row.get("duration") or row.get("length"), 0)
                    if from_ts is not None and start_value and (start_value + max(0, duration)) < int(from_ts):
                        continue
                    if to_ts is not None and start_value and start_value > int(to_ts):
                        continue
                    marker = str(row.get("id") or "") + "|" + str(start_value) + "|" + str(row.get("name") or row.get("title") or row.get("descr") or "")
                    if marker in seen:
                        continue
                    seen.add(marker)
                    collected.append(row)
                    added += 1
                if not added:
                    # A portal ignoring the page number returns the same rows.
                    break
                meta = js if isinstance(js, dict) else {}
                total = max(0, self._int_value(meta.get("total_items") or meta.get("total"), 0))
                page_size = max(1, self._int_value(meta.get("max_page_items") or meta.get("page_size"), len(rows) or 1))
                if total and len(seen) >= total:
                    break
                if not total and len(rows) < page_size:
                    break
                current_page += 1
            if collected:
                return collected
        if errors:
            raise PortalError(errors[-1])
        return []

    # ------------------------------------------------------------------
    # Series hierarchy (same API shape used by working MAG clients)
    # ------------------------------------------------------------------
    @staticmethod
    def _series_id(item):
        if not isinstance(item, dict):
            return None
        return item.get("id") or item.get("movie_id") or item.get("series_id")

    @staticmethod
    def _series_row_identity(row, index=0):
        """Return a stable identity for a season/episode row."""
        if not isinstance(row, dict):
            return ("invalid", index)
        episode = row.get("episode_id") or row.get("episode")
        season = row.get("season") or row.get("season_number")
        sid = row.get("season_id")
        rid = row.get("id")
        if episode not in (None, ""):
            return ("episode", str(season or sid or ""), str(episode), str(rid or ""))
        number = StalkerClient._season_number(row, "")
        if number not in (None, ""):
            return ("season", str(number))
        if sid not in (None, ""):
            return ("season_id", str(sid))
        if rid not in (None, ""):
            return ("id", str(rid))
        return ("row", str(row.get("name") or row.get("title") or ""), index)

    @classmethod
    def _merge_series_rows(cls, *collections):
        """Merge portal responses without allowing a smaller retry to erase rows."""
        merged = OrderedDict()
        for rows in collections:
            for index, row in enumerate(rows or []):
                if not isinstance(row, dict) or not row:
                    continue
                key = cls._series_row_identity(row, index)
                previous = merged.get(key)
                if previous is None:
                    merged[key] = dict(row)
                else:
                    combined = dict(previous)
                    for field, value in row.items():
                        if value not in (None, "", [], {}):
                            combined[field] = value
                    merged[key] = combined
        return list(merged.values())

    @classmethod
    def _series_response_suspicious(cls, rows):
        """Detect the common partial-series response without rejecting valid S1-only shows."""
        if not rows:
            return True
        if any((r.get("episode_id") or r.get("episode")) for r in rows if isinstance(r, dict)):
            return False
        numbers = []
        for index, row in enumerate(rows, 1):
            try:
                raw = cls._season_number(row, index)
                text = str(raw).strip()
                if ":" in text:
                    text = text.rsplit(":", 1)[-1]
                numbers.append(int(text))
            except Exception:
                continue
        if not numbers:
            return False
        unique = sorted(set(numbers))
        if unique[0] > 1:
            return True
        return unique != list(range(unique[0], unique[-1] + 1))

    def _series_stable_cache_key(self, movie_id):
        return self._cache_key({
            "action": "stable_series_hierarchy",
            "movie_id": str(movie_id),
        })

    def _series_rows_once(self, movie_id, page=None, cancel_event=None):
        params = {
            "type": "series", "action": "get_ordered_list", "movie_id": movie_id,
            "season_id": "0", "episode_id": "0", "JsHttpRequest": "1-xml",
        }
        if page is not None:
            params["p"] = str(page)
            params["page"] = str(page)
        js = self._unwrap(self._get(params, use_cache=False, cancel_event=cancel_event))
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return []
        return [row for row in self._as_list(js) if isinstance(row, dict) and row]

    def _series_rows(self, movie_id, cancel_event=None):
        """Resolve the richest available hierarchy for a series."""
        cache_key = self._series_stable_cache_key(movie_id)
        with self._series_hierarchy_lock:
            remembered = list(self._series_hierarchy_memory.get(str(movie_id), []) or [])
            remembered_at=float(getattr(self,"_series_hierarchy_memory_at",{}).get(str(movie_id),0) or 0)
        if remembered and (time.monotonic()-remembered_at) < 120.0:
            return remembered
        persisted = CACHE.get(cache_key)
        if isinstance(persisted, list):
            remembered = self._merge_series_rows(remembered, persisted)

        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return remembered
        fresh = self._series_rows_once(movie_id, cancel_event=cancel_event)
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return remembered
        combined = self._merge_series_rows(remembered, fresh)

        suspicious = self._series_response_suspicious(fresh)
        if remembered and len(fresh) < len(remembered):
            suspicious = True
        if suspicious:
            try:
                retry = self._series_rows_once(movie_id, cancel_event=cancel_event)
                combined = self._merge_series_rows(combined, retry)
            except Exception:
                LOG.exception("series hierarchy retry failed for movie_id=%s", movie_id)

            seen_count = len(combined)
            for page in range(1, 5):
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    break
                try:
                    page_rows = self._series_rows_once(movie_id, page=page, cancel_event=cancel_event)
                except Exception:
                    break
                if not page_rows:
                    break
                merged = self._merge_series_rows(combined, page_rows)
                if len(merged) <= seen_count:
                    break
                combined = merged
                seen_count = len(combined)
                if not self._series_response_suspicious(combined):
                    break

        richest = combined or fresh or remembered
        if richest:
            with self._series_hierarchy_lock:
                current = self._series_hierarchy_memory.get(str(movie_id), [])
                if len(richest) >= len(current or []):
                    self._series_hierarchy_memory[str(movie_id)] = list(richest)
                    self._series_hierarchy_memory_at[str(movie_id)] = time.monotonic()
                else:
                    richest = list(current)
                    self._series_hierarchy_memory_at[str(movie_id)] = time.monotonic()
            try:
                CACHE.put(cache_key, richest, 1800)
            except Exception:
                LOG.exception("unable to persist stable series hierarchy")
        return richest

    @staticmethod
    def _season_number(season, fallback=1):
        sid = str(season.get("id") or season.get("season_id") or "") if isinstance(season, dict) else ""
        if ":" in sid:
            tail = sid.rsplit(":", 1)[-1]
            if tail:
                return tail
        if isinstance(season, dict):
            return season.get("season") or season.get("season_number") or fallback
        return fallback

    def series_seasons(self, series_item, cancel_event=None):
        if not isinstance(series_item, dict):
            return []
        series_id = self._series_id(series_item)
        if series_id in (None, ""):
            return []
        rows = self._series_rows(series_id, cancel_event=cancel_event)
        result = []
        for index, row in enumerate(rows, 1):
            season = dict(row)
            sid = season.get("id") or season.get("season_id") or "%s:%s" % (series_id, index)
            number = self._season_number(season, index)
            season["id"] = sid
            season["season_id"] = sid
            season["season"] = number
            season["parent_series_id"] = series_id
            season.setdefault("name", "Season %s" % number)
            result.append(season)

        # A minority of portals return flat episode dictionaries. Group those
        # without inventing extra API actions that may repeat the series list.
        if result and not any(isinstance(x.get("series"), list) for x in result):
            if any(x.get("episode") or x.get("episode_id") for x in result):
                grouped = OrderedDict()
                for episode in result:
                    number = str(episode.get("season") or episode.get("season_id") or "1")
                    grouped.setdefault(number, []).append(episode)
                return [{
                    "id": "%s:%s" % (series_id, number),
                    "season_id": "%s:%s" % (series_id, number),
                    "season": number,
                    "name": "Season %s" % number,
                    "episodes": episodes,
                    "parent_series_id": series_id,
                } for number, episodes in grouped.items()]
        return result

    def _build_episode_rows(self, series_item, season):
        series_id = self._series_id(series_item)
        season_id = season.get("id") or season.get("season_id") or "%s:1" % series_id
        season_number = self._season_number(season, 1)
        embedded = season.get("episodes")
        if isinstance(embedded, list) and embedded:
            episode_values = embedded
        else:
            episode_values = season.get("series")
        if not isinstance(episode_values, list):
            return []

        command = season.get("cmd") or season.get("command") or season.get("url") or ""
        result = []
        for index, value in enumerate(episode_values, 1):
            if isinstance(value, dict):
                episode = dict(value)
                episode_id = episode.get("episode_id") or episode.get("episode") or episode.get("number") or episode.get("episode_num") or episode.get("episode_number") or episode.get("id") or index
                episode.setdefault("cmd", command)
            else:
                episode_id = value
                episode = {"cmd": command}
            episode["id"] = episode_id
            episode["episode_id"] = episode_id
            episode["episode"] = episode_id
            episode["number"] = episode_id
            episode["season"] = season_number
            episode["season_id"] = season_id
            episode["stream_id"] = season_id
            episode["movie_id"] = series_id
            episode.setdefault("name", "Episode %s" % episode_id)
            for key in ("description", "actors", "director", "writer", "genres_str", "rating_imdb", "year", "country", "country_code", "original_name", "original_title", "imdb_id", "tmdb_id", "tmdbid", "tmdb_type", "screenshot_uri", "cover", "cover_url", "poster", "poster_url", "backdrop", "backdrop_url", "backdrop_path", "fanart"):
                if key not in episode and season.get(key) not in (None, ""):
                    episode[key] = season.get(key)
            result.append(episode)
        return result

    def series_episodes(self, series_item, season_item, cancel_event=None):
        if not isinstance(series_item, dict) or not isinstance(season_item, dict):
            return []
        rows = self._build_episode_rows(series_item, season_item)
        if rows:
            return rows

        series_id = self._series_id(series_item)
        if series_id in (None, ""):
            return []
        requested_id = str(season_item.get("id") or season_item.get("season_id") or "")
        fresh = self._series_rows(series_id, cancel_event=cancel_event)
        target = None
        for row in fresh:
            candidate_id = str(row.get("id") or row.get("season_id") or "")
            if candidate_id == requested_id:
                target = row
                break
        if target is None:
            requested_number = str(self._season_number(season_item, ""))
            for row in fresh:
                if str(self._season_number(row, "")) == requested_number:
                    target = row
                    break
        return self._build_episode_rows(series_item, target or season_item)

    # ------------------------------------------------------------------
    # Playback link resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_command(item_or_cmd):
        if isinstance(item_or_cmd, dict):
            return item_or_cmd.get("cmd") or item_or_cmd.get("command") or item_or_cmd.get("url") or ""
        return item_or_cmd or ""

    @staticmethod
    def _strip_player_wrapper(value):
        text = str(value or "").strip().strip('"').strip("'")
        if not text:
            return ""
        # Normal portal response: "ffmpeg http://..." or "auto http://...".
        first, sep, remainder = text.partition(" ")
        if sep and first.lower() in ("ffmpeg", "auto"):
            text = remainder.lstrip()
        # A few portals include ffmpeg options. Prefer the first playable URL.
        match = re.search(r"(?i)(https?|rtmp|rtsp|udp|rtp)://\S+", text)
        if match:
            text = match.group(0)
        return text.strip().strip('"').strip("'")

    @staticmethod
    def _needs_create_link(raw):
        low = str(raw or "").lower()
        # Match the working-client rule exactly: a wrapped *direct HTTP* command
        # is already playable after removing "ffmpeg/auto" and should not be
        # sent back to create_link.
        return "localhost" in low or "///" in low or "/ch/" in low or "http" not in low

    def _vod_media_command(self, item, media_type, cancel_event=None):
        command = str(self._extract_command(item) or "")
        if not command.startswith("/media/"):
            return command
        stream_id = None
        if isinstance(item, dict):
            stream_id = item.get("stream_id") or item.get("season_id") or item.get("id") or item.get("movie_id")
        if stream_id in (None, ""):
            return command
        request_type = "series" if media_type in ("series", "episode") else "vod"
        params = {
            "type": request_type, "action": "get_ordered_list", "movie_id": stream_id,
            "season_id": "0", "episode_id": "0", "category": "1", "sortby": "",
            "p": "1", "JsHttpRequest": "1-xml",
        }
        try:
            rows = self._as_list(self._unwrap(self._get(params, cancel_event=cancel_event)))
            media_id = rows[0].get("id") if rows and isinstance(rows[0], dict) else None
            if media_id not in (None, ""):
                parsed_path = urllib.parse.urlsplit(command).path or command
                extension = ""
                last = parsed_path.rsplit("/", 1)[-1]
                if "." in last:
                    extension = "." + last.rsplit(".", 1)[-1]
                return "/media/file_%s%s" % (media_id, extension)
        except PortalError:
            pass
        return command

    def create_link(self, item_or_cmd, media_type="itv", series_id=None, cancel_event=None):
        if not self.token:
            self.authorize(cancel_event=cancel_event)
        media_type = str(media_type or "itv").lower()
        command = self._extract_command(item_or_cmd)
        if media_type in ("vod", "series", "episode") and isinstance(item_or_cmd, dict):
            command = self._vod_media_command(item_or_cmd, media_type, cancel_event=cancel_event)
        raw = str(command or "").strip().strip('"').strip("'")
        if not raw:
            raise PortalError("Empty stream command")

        if not self._needs_create_link(raw):
            direct = self._strip_player_wrapper(raw)
            parsed = urllib.parse.urlsplit(direct)
            if parsed.scheme.lower() in self._PLAYABLE_SCHEMES:
                return direct

        api_type = "vod" if media_type in ("series", "episode") else media_type
        params = {
            "type": api_type,
            "action": "create_link",
            "cmd": raw,
            "series": str(series_id if series_id is not None else ("0" if api_type == "itv" else "")),
            "forced_storage": "0" if api_type == "itv" else "",
            "disable_ad": "0",
            "download": "0",
            "force_ch_link_check": "0",
            "JsHttpRequest": "1-xml",
        }

        def request_link():
            js = self._unwrap(self._get(params, retry_auth=False, cancel_event=cancel_event))
            if isinstance(js, dict):
                return js.get("cmd") or js.get("url") or js.get("link")
            return js if isinstance(js, str) else None

        value = request_link()
        if not value:
            self._reset_auth()
            self.authorize(cancel_event=cancel_event)
            value = request_link()
        clean = self._strip_player_wrapper(value)
        if not clean:
            raise PortalError("Portal did not return a stream link")
        parsed = urllib.parse.urlsplit(clean)
        if parsed.scheme.lower() not in self._PLAYABLE_SCHEMES:
            raise PortalError("Portal returned an unsupported stream URL")
        return clean

    def catchup_channels(self, genre="*", page=1, max_pages=None, cancel_event=None):
        result = []
        # Catch-up discovery must never scan a huge portal catalogue for minutes.
        # Twenty native pages is a generous bounded discovery pass and remains
        # fully cancellable through cancel_event.
        max_pages = max(1, min(int(max_pages or 20), 20))
        rows = self.ordered_all("itv", genre, start_page=page, max_pages=max_pages, cancel_event=cancel_event)
        for row in rows:
            archive = (
                row.get("use_http_tmp_link") or row.get("allow_archive") or row.get("allow_timeshift")
                or row.get("tv_archive_duration") or row.get("archive")
            )
            if archive not in (None, "", 0, "0", False, "false", "False"):
                result.append(row)
        return result

    def catchup_programs(self, channel_id, hours=72, cancel_event=None):
        now = int(time.time())
        return self.full_epg(channel_id, now - max(1, int(hours)) * 3600, now, max_pages=120, cancel_event=cancel_event)

    def create_catchup_link(self, channel, program, cancel_event=None):
        channel = channel if isinstance(channel, dict) else {}
        program = program if isinstance(program, dict) else {}
        command = self._extract_command(channel)
        if not command:
            raise PortalError("Catch-up channel has no stream command")
        try:
            start = int(program.get("start_timestamp") or program.get("start") or program.get("time") or 0)
        except Exception:
            start = 0
        try:
            duration = int(program.get("duration") or program.get("length") or 0)
        except Exception:
            duration = 0
        if duration > 24 * 3600 and start:
            duration = max(1, duration - start)
        errors = []
        for action in ("create_link", "create_archive_link"):
            params = {
                "type": "itv", "action": action, "cmd": command, "JsHttpRequest": "1-xml"
            }
            if start:
                params.update({"utc": start, "start": start})
            if duration:
                params["duration"] = duration
            try:
                js = self._unwrap(self._get(params, cancel_event=cancel_event))
                value = js.get("cmd") or js.get("url") or js.get("link") if isinstance(js, dict) else js
                clean = self._strip_player_wrapper(value)
                if urllib.parse.urlsplit(clean).scheme.lower() in self._PLAYABLE_SCHEMES:
                    return clean
            except PortalError as exc:
                errors.append(str(exc))
        raise PortalError(errors[-1] if errors else "Portal did not return a catch-up link")

    def health_snapshot(self):
        samples = self._latency_state.get(self._portal_key(), [])
        avg = int(sum(samples) / float(len(samples))) if samples else 0
        state = self._circuit_state.get(self._portal_key()) or {}
        failures = int(state.get("failures", 0) or 0)
        if failures >= self.CIRCUIT_FAILURE_LIMIT:
            health = "offline"
        elif avg and avg < 700:
            health = "excellent"
        elif avg and avg < 1500:
            health = "online"
        elif avg:
            health = "slow"
        else:
            health = "unknown"
        return {"health": health, "latency_ms": avg, "failures": failures,
                "tls_mode": self.tls_mode, "unverified_tls": bool(self.used_unverified_tls),
                "http_fallback": bool(self.used_http_fallback),
                "device_profile": self.resolved_device_profile or self.device_profile,
                "security_warning": self.security_warning}
