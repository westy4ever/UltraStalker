# -*- coding: utf-8 -*-
"""Bounded Stalker portal compatibility helpers for Ultra Stalker.

This module intentionally contains only Ultra Stalker implementation code.
It discovers same-origin middleware hints published by a portal frontend and
builds optional MAG-compatible identity variants without changing the primary
Stalker client path.
"""
from __future__ import absolute_import

import hashlib
import re
import urllib.parse

MAX_FRONTEND_BYTES = 384 * 1024
MAX_DISCOVERY_URLS = 4
MAX_DISCOVERED_ENDPOINTS = 6

EMULATOR_USER_AGENT = (
    "Mozilla/5.0 (U; Linux; C; Emulator/1.2.12) "
    "AppleWebKit/533.3 (KHTML, like Gecko) Safari/533.3"
)

_PHP_STRING_RE = re.compile(r"(?is)(['\"])([^'\"\r\n]{1,240}?\.php(?:\?[^'\"\r\n]{0,160})?)\1")
_HINT_RE = re.compile(r"(?i)(ajax|loader|portal|middleware|server|api|endpoint)")


def _origin_parts(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    scheme = str(parsed.scheme or "").lower()
    host = str(parsed.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return None
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, int(port), "%s://%s" % (scheme, parsed.netloc)


def same_origin(first, second):
    a = _origin_parts(first); b = _origin_parts(second)
    return bool(a and b and a[:3] == b[:3])


def portal_roots(entry_url):
    """Return at most three sensible same-origin middleware roots."""
    parsed = urllib.parse.urlsplit(str(entry_url or ""))
    origin_info = _origin_parts(entry_url)
    if not origin_info:
        return []
    origin = origin_info[3]
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    roots = []

    def add(path_value):
        path_value = (str(path_value or "").rstrip("/") or "")
        value = origin + path_value
        if value not in roots:
            roots.append(value)

    low = path.lower()
    if low.endswith("/stalker_portal/c"):
        add(path[:-2])
        add("")
    elif low.endswith("/c"):
        parent = path[:-2].rstrip("/")
        if parent:
            add(parent)
        add("")
    elif low.endswith(".php"):
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent:
            add(parent)
        add("")
    else:
        if path and path != "/":
            add(path)
        add("")

    if origin + "/stalker_portal" not in roots:
        roots.append(origin + "/stalker_portal")
    return roots[:3]


def frontend_candidates(entry_url):
    """Small, deterministic frontend probe set; never brute-force paths."""
    parsed = urllib.parse.urlsplit(str(entry_url or ""))
    origin_info = _origin_parts(entry_url)
    if not origin_info:
        return []
    origin = origin_info[3]
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    bases = []

    def add_base(base):
        base = str(base or "").rstrip("/")
        if base and base not in bases:
            bases.append(base)

    if path.lower().endswith("/stalker_portal/c"):
        add_base(origin + path)
        add_base(origin + "/c")
    elif path.lower().endswith("/c"):
        add_base(origin + path)
        add_base(origin + "/stalker_portal/c")
    else:
        for root in portal_roots(entry_url):
            if root.endswith("/stalker_portal"):
                add_base(root + "/c")
            elif root == origin:
                add_base(origin + "/c")
        add_base(origin + "/stalker_portal/c")

    out = []
    for base in bases[:2]:
        for name in ("xpcom.common.js", "version.js"):
            value = base + "/" + name
            if value not in out:
                out.append(value)
    return out[:MAX_DISCOVERY_URLS]


def _safe_endpoint_url(value, frontend_url, entry_url):
    value = str(value or "").strip().replace("\\/", "/")
    if not value or len(value) > 320 or "\x00" in value:
        return ""
    value = value.split("#", 1)[0]
    # Query strings from frontend constants are not part of endpoint identity.
    value = value.split("?", 1)[0]
    if not value.lower().endswith(".php"):
        return ""
    if ".." in value.split("/"):
        return ""

    lower_value = value.lower()
    if lower_value.startswith(("http://", "https://")):
        # Absolute declarations stay absolute. Never reinterpret another host
        # as a local middleware suffix. same_origin() below decides validity.
        url = value
    elif value.startswith("//"):
        # Treat a network-path reference as an absolute host declaration too.
        # This prevents //other-host/loader.php becoming /other-host/loader.php.
        entry = urllib.parse.urlsplit(str(entry_url or ""))
        if str(entry.scheme or "").lower() not in ("http", "https"):
            return ""
        url = str(entry.scheme).lower() + ":" + value
    elif value.startswith("/"):
        origin = _origin_parts(entry_url)
        if not origin:
            return ""
        url = origin[3] + re.sub(r"/+", "/", value)
    else:
        url = urllib.parse.urljoin(str(frontend_url or ""), value)

    if not same_origin(url, entry_url):
        return ""
    parsed = urllib.parse.urlsplit(url)
    clean_path = re.sub(r"/+", "/", parsed.path or "/")
    if not clean_path.lower().endswith(".php"):
        return ""
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, clean_path, "", ""))


def extract_declared_endpoints(js_text, frontend_url, entry_url):
    """Extract a few same-origin PHP loader candidates declared by frontend JS."""
    text = str(js_text or "")
    if not text:
        return []
    if len(text) > MAX_FRONTEND_BYTES:
        text = text[:MAX_FRONTEND_BYTES]
    scored = []
    seen = set()
    roots = portal_roots(entry_url)
    for match in _PHP_STRING_RE.finditer(text):
        raw = match.group(2)
        context = text[max(0, match.start() - 140):min(len(text), match.end() + 140)]
        context_low = context.lower()
        low = raw.lower()
        score = 0
        if _HINT_RE.search(context): score += 4
        if low.endswith("/server/load.php") or low.endswith("server/load.php"): score += 6
        if low.endswith("/portal.php") or low.endswith("portal.php"): score += 5
        if low.endswith("/portal1.php") or low.endswith("portal1.php"): score += 5
        if "load" in low or "portal" in low: score += 2

        candidates = []
        raw_value = str(raw or "").strip().replace("\\/", "/")
        raw_lower = raw_value.lower()
        is_host_reference = raw_lower.startswith(("http://", "https://")) or raw_value.startswith("//")

        # Absolute or network-path declarations are evaluated only as declared.
        # A rejected foreign host must never be recycled as a local path suffix.
        if not is_host_reference:
            suffix = "/" + raw_value.split("?", 1)[0].lstrip("/")
            # Frontends often concatenate portal_path with a quoted PHP suffix.
            # In that case the middleware root is more authoritative than treating
            # the quoted string as an origin-root absolute path.
            if "portal_path" in context_low or not raw_value.startswith("/"):
                for root in roots:
                    candidate = _safe_endpoint_url(root.rstrip("/") + suffix, frontend_url, entry_url)
                    if candidate and candidate not in candidates:
                        candidates.append(candidate)

        direct = _safe_endpoint_url(raw_value, frontend_url, entry_url)
        if direct and direct not in candidates:
            candidates.append(direct)

        for order, endpoint in enumerate(candidates):
            if endpoint in seen:
                continue
            seen.add(endpoint)
            scored.append((-score, match.start(), order, endpoint))
    scored.sort()
    return [row[3] for row in scored[:MAX_DISCOVERED_ENDPOINTS]]


def compatibility_state_key(entry_url):
    """Non-sensitive root key. No MAC/device identifiers enter the key."""
    roots = portal_roots(entry_url)
    root = roots[0] if roots else str(entry_url or "")
    raw = root.rstrip("/").lower().encode("utf-8", "ignore")
    return "portal_compat:" + hashlib.sha256(raw).hexdigest()


def compatible_identity(mac_value, random_value=""):
    """Build the optional random-aware MAG identity requested by middleware."""
    mac = str(mac_value or "")
    rnd = str(random_value or "")
    mac_bytes = mac.encode("utf-8", "ignore")
    sn = hashlib.md5(mac_bytes).hexdigest().upper()[:13]
    device_id = hashlib.sha256(mac_bytes).hexdigest().upper()
    signature = hashlib.sha256((sn + rnd).encode("utf-8", "ignore")).hexdigest().upper()
    return {
        "sn": sn,
        "device_id": device_id,
        "device_id2": device_id,
        "signature": signature,
    }


def endpoint_log_label(url):
    """Return only a non-secret endpoint path for diagnostics."""
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
        return re.sub(r"/+", "/", parsed.path or "/")[:180]
    except Exception:
        return "[endpoint]"
