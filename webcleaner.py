# -*- coding: utf-8 -*-
"""Ultra Stalker Web Cleaner receiver service.

Explicit-start only and LAN-facing on port 7725. Easy LAN access is the
default; optional PIN/QR pairing keeps the random HttpOnly session-token mode.  The cleaner is deliberately isolated from the playback/UI
stack: it uses only Python stdlib and never starts at Enigma2 boot by itself.

Runtime rule: ``working`` is emitted only after real content plus stream
proof.  A successful API response, MAG handshake, profile, or Xtream auth=1 is
not enough by itself.
"""
from __future__ import absolute_import

import datetime
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import struct
import zlib
import binascii
import ssl
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 7725
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.join(ROOT, "web")
INDEX_FILE = os.path.join(WEB_ROOT, "cleaner.html")
MAX_BODY = 64 * 1024 * 1024

API_TIMEOUT = 5.0
STREAM_TIMEOUT = 7.0
HOST_PROBE_TIMEOUT = 2.5
MAX_HOST_WORKERS = 4
API_RETRIES = 1
MAX_API_BYTES = 2 * 1024 * 1024
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
STREAM_PROBE_BYTES = 256 * 1024
MAX_STREAM_CANDIDATES = 3

UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG254 stbapp ver: 4 rev: 272 Safari/533.3"
XUA = "Model: MAG254; Link: Ethernet"

_LOCK = threading.RLock()
_SERVER = None
_THREAD = None
_TOKEN = None
_PAIR_CODE = None
_PAIR_FAILURES = {}
_ACCESS_MODE = "easy"
_LAST_URL = None
_QR_URL = None
_QR_PATH = None
_VALIDATION_JOB = None
_VALIDATION_THREAD = None


# R146: Web Cleaner shares the receiver interface language.
_WEB_I18N_DONORS = {'Ultra Stalker • Playlist Cleaner': 'Web Cleaner', 'Local processing • private by design': 'Web Cleaner Ready', 'Playlist Intelligence': 'Diagnostics', 'Clean once. Keep only what works.': 'Web Cleaner Ready', 'Drop one mixed file. Ultra Stalker detects, cleans, groups and prepares Portal, Xtream and M3U entries before deep validation.': 'Add a Portal or M3U source', 'Choose a TXT / JSON / M3U file': 'Open', 'Mixed formats are fine. Nothing is uploaded while parsing.': 'Web Cleaner', '＋ Choose File': 'Open', 'Ready for a file': 'Ready', 'Cleaner engine is standing by': 'Ready', 'Reading': 'Checking...', 'Cleaning': 'Checking...', 'Organizing': 'Checking...', 'Add Connection': 'Add a Portal or M3U source', 'Add Portal, Xtream or M3U directly from your browser. Saved connections appear in Ultra Stalker on the receiver.': 'Add a Portal or M3U source', 'Receiver storage': 'Your saved content', 'Portal': 'Portal', 'Name (optional)': 'Portal name', 'Portal URL': 'Portal URL (HTTPS preferred)', 'Server URL': 'Portal URL (HTTPS preferred)', 'M3U URL': 'Portal / M3U URL', 'MAC address': 'MAC address', 'Username': 'Username', 'Password': 'Password', '◉ Test Connection': 'TEST', '＋ Save to Ultra Stalker': 'Save', 'Choose a connection type, enter its details, then test or save it.': 'Add a Portal or M3U source', 'Edit Saved Connection': 'Portal Manager', 'Select one saved source, edit its details or move it to a new position. The list stays compact even with hundreds of connections.': 'Portal Manager', 'Loading…': 'Checking...', 'Choose a saved connection…': 'Your saved content', 'Choose a saved connection to edit or move it.': 'Your saved content', 'Type': 'Type', 'PORTAL': 'Portal', 'Position / Order': 'Edit Order', 'Name': 'Portal name', 'Save Changes': 'Save', 'Intelligence report': 'Diagnostics', 'What survived cleaning, then what survived real stream proof.': 'Diagnostics', 'Deep validation comes next': 'Check All', 'Total imported': 'Result', 'Recognized entries': 'Ready', 'Removed / ignored': 'No results', 'WORKING': 'WORKING', 'Pending': 'Pending', 'DUPLICATES': 'DUPLICATES', 'EXPIRED': 'EXPIRED', 'INVALID URL': 'INVALID URL', 'UNREACHABLE': 'UNREACHABLE', 'OTHER': 'OTHER', 'Working is only awarded after real media bytes or an HLS media segment is verified. API success alone is never enough.': 'Check All', 'Cleaned preview': 'Ready', 'Passwords hidden • grouped by service': 'Web Cleaner', 'Preparing deep check': 'Checking...', 'checked •': 'STATUS', 'active •': 'STATUS', 'Queueing services…': 'Checking...', '◉ Deep Check Services': 'Check All', '■ Stop Deep Check': 'Cancel', '↓ Download Clean File': 'Download', '↓ Detailed Report': 'Diagnostics', '⇧ Add Valid to UltraStalker': 'Save', 'ULTRA STALKER • PLAYLIST CLEANER': 'Web Cleaner', 'Connection type': 'Type', 'Living Room': 'Portal name', 'username': 'Username', 'password': 'Password', 'Saved connection': 'Your saved content', 'Ready': 'Ready', 'Testing…': 'Testing…', 'Testing on the receiver…': 'Testing…', 'Saving…': 'Save', 'Saving changes on the receiver…': 'Save', 'Unavailable': 'OFFLINE', 'Processing your file': 'Checking...', 'Reading, cleaning and organizing entries': 'Checking...', 'File ready': 'Ready', 'Cleaned, grouped and ready for deep validation': 'Ready', 'Deep checking services': 'Checking...', 'Preparing receiver validation queue': 'Checking...', 'Results ready': 'Ready', 'Deep Check complete': 'Ready', 'Real stream proof completed': 'Ready', 'Real stream/playback proof finished': 'Ready', 'Deep Check stopped': 'Cancel', 'Validation failed': 'Content temporarily unavailable. Try again.', 'No entries were promoted to Working': 'No content returned', 'Run Deep Check first. Only validated Working entries can be added.': 'Check All', 'Adding...': 'Save', 'Add Valid to UltraStalker': 'Save', 'Content temporarily unavailable. Try again.': 'Content temporarily unavailable. Try again.'}
_WEB_I18N_DONORS.update({'Deep Check Services':'Check All','Starting Deep Check…':'Checking...'})


def _wc_tr(text):
    try:
        from .localization import translate
        return translate(text)
    except Exception:
        return str(text or "")


def _web_i18n_payload():
    try:
        from .localization import current_language
        language = current_language()
    except Exception:
        language = "en"
    return {"language": language, "values": {source: _wc_tr(donor) for source, donor in _WEB_I18N_DONORS.items()}}


def _inject_web_i18n(html):
    payload = json.dumps(_web_i18n_payload(), ensure_ascii=False, separators=(",", ":"))
    # This page has already passed Web Cleaner access control. Attach the
    # short-lived in-memory session token to same-origin API requests as an
    # explicit header too, so mobile/embedded cookie policy cannot make the
    # HTML open successfully while every editor action silently gets HTTP 403.
    token = json.dumps(str(_TOKEN or ""), ensure_ascii=False)
    script = r'''<script id="us-web-i18n">
window.US_WEB_I18N=__PAYLOAD__;
window.US_WEB_SESSION_TOKEN=__TOKEN__;
(function(){
 var pack=window.US_WEB_I18N||{language:'en',values:{}}; var vals=pack.values||{};
 document.documentElement.lang=pack.language||'en';
 window.usTr=function(v){var s=String(v==null?'':v);return Object.prototype.hasOwnProperty.call(vals,s)?vals[s]:s};
 window.usStatus=function(v){var raw=String(v||'').trim().toLowerCase();var map={working:'WORKING',expired:'EXPIRED',unreachable:'UNREACHABLE',invalid_url:'INVALID URL',duplicate:'DUPLICATES',duplicates:'DUPLICATES',pending:'Pending',other:'OTHER',offline:'OFFLINE'};return window.usTr(map[raw]||'STATUS')};
 var nativeFetch=window.fetch?window.fetch.bind(window):null;
 if(nativeFetch){window.fetch=function(input,init){init=init||{};var url=(typeof input==='string')?input:((input&&input.url)||'');if(url.indexOf('/api/')===0){var headers={};var src=init.headers||{};if(typeof Headers!=='undefined'&&src instanceof Headers){src.forEach(function(v,k){headers[k]=v})}else{for(var k in src){if(Object.prototype.hasOwnProperty.call(src,k))headers[k]=src[k]}}if(window.US_WEB_SESSION_TOKEN)headers['X-US-Token']=window.US_WEB_SESSION_TOKEN;init.headers=headers;init.credentials='same-origin'}return nativeFetch(input,init)}}
 function textNode(n){if(!n||n.nodeType!==3)return;var raw=n.nodeValue||'';var t=raw.trim();if(!t||!Object.prototype.hasOwnProperty.call(vals,t))return;var i=raw.indexOf(t);n.nodeValue=raw.slice(0,i)+vals[t]+raw.slice(i+t.length)}
 function node(el){if(!el)return;if(el.nodeType===3){textNode(el);return}if(el.nodeType!==1)return;var attrs=['placeholder','title','aria-label'];for(var ai=0;ai<attrs.length;ai++){var a=attrs[ai];var v=el.getAttribute&&el.getAttribute(a);if(v&&Object.prototype.hasOwnProperty.call(vals,v))el.setAttribute(a,vals[v])}var kids=el.childNodes||[];for(var ki=0;ki<kids.length;ki++)textNode(kids[ki])}
 function all(root){if(!root)return;node(root);if(root.querySelectorAll){var els=root.querySelectorAll('*');for(var i=0;i<els.length;i++)node(els[i])}}
 document.addEventListener('DOMContentLoaded',function(){all(document.body);document.documentElement.style.visibility='visible'});
})();
</script>'''.replace("__PAYLOAD__", payload).replace("__TOKEN__", token)
    return html.replace("</head>", script + "</head>", 1) if "</head>" in html else script + html


def _png_chunk(kind, data):
    kind = bytes(kind)
    data = bytes(data)
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def _write_qr_png(text, path):
    """Create a dependency-free PNG QR image for the receiver pairing screen.

    QR matrix generation is bundled locally (python-qrcode BSD); PNG encoding
    uses only stdlib so Pillow is never required by Web Cleaner.
    """
    from .webqr import QRCode, ERROR_CORRECT_M
    qr = QRCode(error_correction=ERROR_CORRECT_M, box_size=1, border=4)
    qr.add_data(str(text or ""), optimize=0)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    cells = len(matrix)
    if cells <= 0:
        raise ValueError("empty QR matrix")
    scale = max(4, min(10, 320 // cells))
    width = cells * scale
    rows = []
    for row in matrix:
        pixels = bytearray()
        for dark in row:
            pixels.extend(([0] if dark else [255]) * scale)
        scan = b"\x00" + bytes(pixels)  # PNG filter 0, grayscale 8-bit
        rows.extend([scan] * scale)
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", width, width, 8, 0, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(raw, 9)) + _png_chunk(b"IEND", b"")
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _pair_page(error=""):
    msg = str(error or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    alert = ('<div class="error">%s</div>' % msg) if msg else ""
    title = _wc_tr("Web Cleaner")
    instruction = _wc_tr("Enter the code once. Your browser keeps the secure session until Web Cleaner is stopped.")
    button = _wc_tr("Open")
    try:
        from .localization import current_language
        language = current_language()
    except Exception:
        language = "en"
    html = """<!doctype html><html lang=\"__LANG__\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>__TITLE__</title><style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 70% 10%,#063657 0,#031522 38%,#02070d 72%);color:#eef7fb;font-family:Arial,sans-serif;padding:22px}.card{width:min(520px,100%);padding:30px;border:1px solid #24506a;border-radius:24px;background:rgba(8,18,27,.86);box-shadow:0 22px 70px rgba(0,0,0,.45),0 0 28px rgba(27,160,220,.08)}.brand{font-size:12px;letter-spacing:.22em;color:#78c9ef;text-transform:uppercase}.title{font-size:29px;margin:10px 0 8px}.sub{color:#a9c5d3;line-height:1.5;margin-bottom:22px}.code{width:100%;font-size:30px;letter-spacing:.28em;text-align:center;padding:16px;border-radius:14px;border:1px solid #35617a;background:#06111a;color:#fff;outline:none}.code:focus{border-color:#58caf7;box-shadow:0 0 0 3px rgba(88,202,247,.12)}button{width:100%;margin-top:14px;padding:15px;border:0;border-radius:14px;font-size:17px;font-weight:700;background:linear-gradient(135deg,#28bff2,#2c77ff);color:white;cursor:pointer}.error{margin:0 0 14px;padding:11px 13px;border-radius:11px;background:rgba(214,68,68,.13);border:1px solid rgba(255,105,105,.42);color:#ffc2c2}.foot{margin-top:18px;color:#7894a3;font-size:12px;text-align:center}</style></head><body><form class=\"card\" method=\"post\" action=\"/pair\"><div class=\"brand\">Ultra Stalker</div><div class=\"title\">__TITLE__</div><div class=\"sub\">__INSTRUCTION__</div>__ALERT__<input class=\"code\" name=\"code\" inputmode=\"numeric\" pattern=\"[0-9]{6}\" maxlength=\"6\" autocomplete=\"one-time-code\" autofocus required><button type=\"submit\">__BUTTON__</button><div class=\"foot\">Ultra Stalker</div></form></body></html>"""
    html = html.replace("__LANG__", str(language)).replace("__TITLE__", title).replace("__INSTRUCTION__", instruction).replace("__BUTTON__", button).replace("__ALERT__", alert)
    return html.encode("utf-8")


def _configured_access_mode():
    try:
        from .storage import load_settings
        mode = str(load_settings().get("web_cleaner_access") or "easy").strip().lower()
    except Exception:
        mode = "easy"
    return "protected" if mode == "protected" else "easy"


def _lan_client_allowed(value):
    try:
        raw = str(value or "").split("%", 1)[0]
        ip = ipaddress.ip_address(raw)
        return bool(ip.is_private or ip.is_loopback)
    except Exception:
        return False


def _pair_check(client_ip, supplied):
    global _PAIR_FAILURES
    now = time.monotonic()
    key = str(client_ip or "unknown")
    code = str(supplied or "").strip()
    with _LOCK:
        attempts = [x for x in (_PAIR_FAILURES.get(key) or []) if now - float(x) < 60.0]
        if len(attempts) >= 5:
            wait = max(1, int(60.0 - (now - attempts[0])))
            _PAIR_FAILURES[key] = attempts
            return False, _wc_tr("Content temporarily unavailable. Try again.")
        if _PAIR_CODE and len(code) == 6 and code.isdigit() and secrets.compare_digest(code, str(_PAIR_CODE)):
            _PAIR_FAILURES.pop(key, None)
            return True, ""
        attempts.append(now)
        _PAIR_FAILURES[key] = attempts
        return False, _wc_tr("Incorrect pairing code.")


class ValidationFailure(Exception):
    def __init__(self, status, detail, http_code=None):
        super().__init__(detail)
        self.status = str(status or "invalid_response")
        self.detail = str(detail or self.status)
        self.http_code = http_code


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _url_parts(url):
    try:
        return urllib.parse.urlsplit(str(url or "").strip())
    except Exception:
        return urllib.parse.urlsplit("")


def _host_from_url(url):
    try:
        p = _url_parts(url)
        host = (p.hostname or "").lower()
        if not host:
            return ""
        port = p.port or (443 if p.scheme == "https" else 80)
        return "%s:%d" % (host, int(port))
    except Exception:
        return ""


def _safe_target(url):
    """Reject loopback/link-local/non-unicast targets; private LAN portals stay valid."""
    try:
        p = _url_parts(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False, "invalid URL"
        infos = socket.getaddrinfo(
            p.hostname,
            p.port or (443 if p.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        for info in infos:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
            if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                return False, "blocked local/link target"
        return True, ""
    except Exception as exc:
        return False, str(exc)[:160]


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = _url_parts(req.full_url)
        new = _url_parts(newurl)
        if (old.hostname or "").lower() != (new.hostname or "").lower():
            raise urllib.error.HTTPError(newurl, 403, "cross-host redirect blocked", headers, fp)
        if new.scheme not in ("http", "https"):
            raise urllib.error.HTTPError(newurl, 403, "redirect scheme blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SafeStreamRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = _url_parts(newurl)
        if new.scheme not in ("http", "https"):
            raise urllib.error.HTTPError(newurl, 403, "redirect scheme blocked", headers, fp)
        ok, why = _safe_target(newurl)
        if not ok:
            raise urllib.error.HTTPError(newurl, 403, "unsafe stream redirect: %s" % why, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SSL_CONTEXT = ssl.create_default_context()
_API_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_SSL_CONTEXT),
    _SameHostRedirect(),
)
_STREAM_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_SSL_CONTEXT),
    _SafeStreamRedirect(),
)


def _fetch(url, headers=None, timeout=API_TIMEOUT, max_bytes=512000, retries=API_RETRIES, stream=False):
    ok, why = _safe_target(url)
    if not ok:
        raise ValueError(why)
    request_headers = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    opener = _STREAM_OPENER if stream else _API_OPENER
    last = None
    for attempt in range(max(0, int(retries)) + 1):
        try:
            req = urllib.request.Request(url, headers=request_headers)
            with opener.open(req, timeout=float(timeout)) as response:
                data = response.read(int(max_bytes))
                return int(getattr(response, "status", response.getcode())), dict(response.headers), data
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt >= int(retries):
                raise
        except (socket.timeout, TimeoutError, ConnectionError, OSError, ssl.SSLError) as exc:
            last = exc
            if attempt >= int(retries):
                raise
        time.sleep(0.18 * (attempt + 1))
    if last:
        raise last
    raise IOError("request failed")


def _net_status(exc):
    text = str(exc).lower()
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in text or "timeout" in text:
        return "timeout"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return "auth_failed"
        if exc.code == 403:
            return "blocked"
        if exc.code == 404:
            return "invalid_response"
        return "http_error"
    if isinstance(exc, ssl.SSLError) or "certificate" in text or "tls" in text:
        return "tls_error"
    if isinstance(exc, ValueError) and "blocked" in text:
        return "blocked"
    return "unreachable"


def _failure_from_exc(exc, prefix=""):
    status = _net_status(exc)
    detail = (prefix + (": " if prefix else "") + str(exc))[:220]
    code = exc.code if isinstance(exc, urllib.error.HTTPError) else None
    return ValidationFailure(status, detail, http_code=code)


def _root(url):
    p = _url_parts(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, "", "", "")).rstrip("/")


def _looks_token_error(value):
    try:
        text = json.dumps(value, ensure_ascii=False).lower() if isinstance(value, (dict, list)) else str(value).lower()
    except Exception:
        text = str(value).lower()
    return any(marker in text for marker in (
        "not valid token", "not_valid_token", "invalid token", "token expired",
        "authorization failed", "authorization error",
    ))


def _json_load(body, label="response"):
    try:
        text = body.decode("utf-8", "replace").strip()
        if not text:
            raise ValidationFailure("invalid_response", "%s was empty" % label)
        obj = json.loads(text)
    except ValidationFailure:
        raise
    except Exception as exc:
        raise ValidationFailure("invalid_response", "%s was not valid JSON: %s" % (label, str(exc)[:120]))
    if _looks_token_error(obj):
        raise ValidationFailure("invalid_token", "%s reported an invalid/expired token" % label)
    return obj


def _fetch_json(url, headers=None, timeout=API_TIMEOUT, max_bytes=MAX_API_BYTES, label="response"):
    try:
        code, response_headers, body = _fetch(
            url,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
            retries=API_RETRIES,
            stream=False,
        )
    except Exception as exc:
        raise _failure_from_exc(exc, label)
    obj = _json_load(body, label=label)
    return code, response_headers, obj


def _parse_expiry(value):
    text = str(value or "").strip()
    if not text or text.lower() in ("null", "none", "unlimited", "never", "0"):
        return None
    if text.isdigit():
        try:
            number = int(text)
            if number > 100000000:
                return number < int(time.time())
        except Exception:
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(text[:19], fmt).timestamp() < time.time()
        except Exception:
            pass
    return None


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "blocked", "disabled")


# ---------------------------------------------------------------------------
# Real stream proof
# ---------------------------------------------------------------------------

def _is_probably_text_error(data, content_type=""):
    head = bytes(data[:512] or b"").lstrip().lower()
    ctype = str(content_type or "").lower()
    if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
        return True
    return head.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"["))


def _mpeg_ts_score(data, step=188):
    data = bytes(data or b"")
    if len(data) < step * 3:
        return 0
    best = 0
    limit = min(step, len(data))
    for start in range(limit):
        if data[start] != 0x47:
            continue
        score = 0
        pos = start
        while pos < len(data) and data[pos] == 0x47 and score < 12:
            score += 1
            pos += step
        if score > best:
            best = score
            if best >= 5:
                break
    return best


def _media_signature(data):
    data = bytes(data or b"")
    if not data:
        return ""
    if _mpeg_ts_score(data, 188) >= 3:
        return "mpeg-ts"
    if _mpeg_ts_score(data, 192) >= 3:
        return "m2ts"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"FLV"):
        return "flv"
    if data.startswith(b"RIFF") and len(data) >= 12:
        return "riff-media"
    if data.startswith(b"\x00\x00\x01\xba"):
        return "mpeg-ps"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xF0) == 0xF0:
        return "aac/adts"
    return ""


def _hls_uri_lines(text):
    lines = [x.strip() for x in str(text or "").splitlines()]
    variants = []
    segments = []
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for nxt in lines[i + 1:]:
                if nxt and not nxt.startswith("#"):
                    variants.append(nxt)
                    break
        elif line.startswith("#EXT-X-PART"):
            match = re.search(r'URI="([^"]+)"', line, re.I)
            if match:
                segments.append(match.group(1))
        elif line and not line.startswith("#"):
            segments.append(line)
    return variants, segments


def _probe_hls(url, text, headers=None, depth=0):
    if "#EXTM3U" not in str(text or ""):
        return {"ok": False, "status": "invalid_response", "detail": "stream response was not an HLS playlist"}
    variants, segments = _hls_uri_lines(text)
    if variants and depth < 2:
        child = urllib.parse.urljoin(url, variants[0])
        try:
            code, hh, body = _fetch(child, headers=headers, timeout=STREAM_TIMEOUT, max_bytes=256000, retries=0, stream=True)
            child_text = body.decode("utf-8", "replace")
            return _probe_hls(child, child_text, headers=headers, depth=depth + 1)
        except Exception as exc:
            fail = _failure_from_exc(exc, "HLS variant")
            return {"ok": False, "status": fail.status, "detail": fail.detail}
    for raw in segments[:MAX_STREAM_CANDIDATES]:
        seg = urllib.parse.urljoin(url, raw)
        try:
            code, hh, body = _fetch(
                seg,
                headers=dict(headers or {}, **{"Range": "bytes=0-%d" % (STREAM_PROBE_BYTES - 1)}),
                timeout=STREAM_TIMEOUT,
                max_bytes=STREAM_PROBE_BYTES,
                retries=0,
                stream=True,
            )
            ctype = str(hh.get("Content-Type") or hh.get("content-type") or "")
            sig = _media_signature(body)
            if sig:
                return {"ok": True, "status": "working", "detail": "HLS media segment verified (%s, %d bytes)" % (sig, len(body)), "proof": sig, "bytes": len(body)}
            if len(body) >= 4096 and not _is_probably_text_error(body, ctype):
                return {"ok": True, "status": "working", "detail": "HLS media segment bytes verified (%d bytes)" % len(body), "proof": "hls-segment", "bytes": len(body)}
        except Exception as exc:
            last = _failure_from_exc(exc, "HLS segment")
            continue
    if segments:
        if 'last' in locals():
            return {"ok": False, "status": last.status, "detail": last.detail}
        return {"ok": False, "status": "stream_unavailable", "detail": "HLS playlist loaded but no media segment could be verified"}
    return {"ok": False, "status": "invalid_response", "detail": "HLS playlist contained no playable variant/segment"}


def _stream_probe(url, headers=None):
    raw = str(url or "").strip().strip('"').strip("'")
    if not raw:
        return {"ok": False, "status": "stream_unavailable", "detail": "empty stream URL"}
    scheme = _url_parts(raw).scheme.lower()
    if scheme not in ("http", "https"):
        return {"ok": False, "status": "unsupported_stream_protocol", "detail": "cannot prove playback for %s stream on Web Cleaner" % (scheme or "unknown")}
    h = dict(headers or {})
    h.setdefault("Accept", "*/*")
    h["Range"] = "bytes=0-%d" % (STREAM_PROBE_BYTES - 1)
    try:
        try:
            code, rh, body = _fetch(raw, headers=h, timeout=STREAM_TIMEOUT, max_bytes=STREAM_PROBE_BYTES, retries=0, stream=True)
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                h.pop("Range", None)
                code, rh, body = _fetch(raw, headers=h, timeout=STREAM_TIMEOUT, max_bytes=STREAM_PROBE_BYTES, retries=0, stream=True)
            else:
                raise
        ctype = str(rh.get("Content-Type") or rh.get("content-type") or "")
        stripped = body.lstrip()
        if stripped.startswith(b"#EXTM3U"):
            return _probe_hls(raw, body.decode("utf-8", "replace"), headers=headers, depth=0)
        sig = _media_signature(body)
        if sig:
            return {"ok": True, "status": "working", "detail": "stream media bytes verified (%s, %d bytes)" % (sig, len(body)), "proof": sig, "bytes": len(body)}
        if _is_probably_text_error(body, ctype):
            return {"ok": False, "status": "invalid_response", "detail": "stream URL returned text/HTML/JSON instead of media"}
        return {"ok": False, "status": "stream_unavailable", "detail": "stream responded but media signature/segment could not be verified (%d bytes)" % len(body)}
    except Exception as exc:
        fail = _failure_from_exc(exc, "stream probe")
        return {"ok": False, "status": fail.status, "detail": fail.detail, "http_code": fail.http_code}


def _pick_failed_probe(probes):
    probes = [x for x in probes if isinstance(x, dict) and not x.get("ok")]
    if not probes:
        return "stream_unavailable", "no stream candidate could be verified"
    priority = (
        "blocked", "auth_failed", "invalid_token", "timeout", "tls_error",
        "unreachable", "http_error", "invalid_response", "stream_unavailable",
        "unsupported_stream_protocol",
    )
    for status in priority:
        for probe in probes:
            if probe.get("status") == status:
                return status, probe.get("detail") or status
    return probes[-1].get("status") or "stream_unavailable", probes[-1].get("detail") or "stream proof failed"


# ---------------------------------------------------------------------------
# Xtream deep check
# ---------------------------------------------------------------------------

def _xtream_url(x):
    host = str(x.get("host") or "").rstrip("/")
    user = str(x.get("user") or "")
    password = str(x.get("pass") or "")
    if not host or not user or not password:
        return ""
    return host + "/get.php?" + urllib.parse.urlencode({"username": user, "password": password, "type": "m3u_plus", "output": "ts"})


def _xtream_api(host, user, password, action=None, extra=None):
    params = {"username": user, "password": password}
    if action:
        params["action"] = action
    if extra:
        params.update(extra)
    return host.rstrip("/") + "/player_api.php?" + urllib.parse.urlencode(params)


def _playlist_stream_urls(text, base_url, limit=12):
    lines = [x.strip() for x in str(text or "").splitlines()]
    out = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://", "rtp://")):
            value = line
        elif "://" not in line:
            value = urllib.parse.urljoin(base_url, line)
        else:
            continue
        if value not in out:
            out.append(value)
            if len(out) >= int(limit):
                break
    return out


def _xtream_api_stream_candidates(host, user, password, category_id=None):
    extra = {"category_id": category_id} if category_id not in (None, "") else None
    try:
        _, _, obj = _fetch_json(
            _xtream_api(host, user, password, "get_live_streams", extra),
            label="Xtream live streams",
            max_bytes=MAX_API_BYTES,
        )
    except ValidationFailure:
        return []
    rows = obj if isinstance(obj, list) else []
    out = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        sid = row.get("stream_id") or row.get("id")
        if sid in (None, ""):
            continue
        ext = str(row.get("container_extension") or "ts").strip(".") or "ts"
        for candidate_ext in (ext, "ts", "m3u8"):
            url = "%s/live/%s/%s/%s.%s" % (
                host.rstrip("/"),
                urllib.parse.quote(user, safe=""),
                urllib.parse.quote(password, safe=""),
                sid,
                candidate_ext,
            )
            if url not in out:
                out.append(url)
            if len(out) >= 6:
                return out
    return out


def validate_xtream(x):
    out = dict(x or {})
    host = str(out.get("host") or "").rstrip("/")
    user = str(out.get("user") or "")
    password = str(out.get("pass") or "")
    started = time.time()
    if not host or not user or not password:
        out.update(status="invalid", detail="missing server/username/password")
        return out
    if _url_parts(host).scheme not in ("http", "https"):
        out.update(status="invalid", detail="invalid Xtream server URL")
        return out

    try:
        _, _, obj = _fetch_json(_xtream_api(host, user, password), label="Xtream player_api")
        ui = obj.get("user_info") if isinstance(obj, dict) else None
        si = obj.get("server_info") if isinstance(obj, dict) else None
        if not isinstance(ui, dict):
            raise ValidationFailure("invalid_response", "player_api returned no user_info")

        auth = str(ui.get("auth", "")).strip().lower()
        account_status = str(ui.get("status", "")).strip().lower()
        exp = ui.get("exp_date")
        active_cons = ui.get("active_cons")
        max_cons = ui.get("max_connections")

        if auth not in ("1", "true"):
            raise ValidationFailure("auth_failed", "player_api authentication failed")
        if account_status in ("expired",):
            out.update(status="expired", detail="Xtream account status is expired", exp_date=exp)
            return out
        if account_status in ("banned", "blocked"):
            out.update(status="blocked", detail="Xtream account status is %s" % account_status, exp_date=exp)
            return out
        if account_status in ("disabled", "inactive"):
            out.update(status="inactive", detail="Xtream account status is %s" % account_status, exp_date=exp)
            return out
        if _parse_expiry(exp) is True:
            out.update(status="expired", detail="Xtream account expiration is in the past", exp_date=exp)
            return out

        # Content evidence: live categories first.  Failure here is recorded but
        # does not override a later, stronger real-playlist + stream proof.
        category_count = 0
        category_id = None
        category_note = ""
        try:
            _, _, cats = _fetch_json(_xtream_api(host, user, password, "get_live_categories"), label="Xtream live categories")
            if isinstance(cats, list):
                category_count = len(cats)
                for row in cats:
                    if isinstance(row, dict) and row.get("category_id") not in (None, ""):
                        category_id = row.get("category_id")
                        break
        except ValidationFailure as exc:
            category_note = exc.status

        # Prefer the provider's own get.php playlist because it carries the exact
        # stream routing the account is expected to use.  Then fall back to API
        # stream_id construction when needed.
        m3u = str(out.get("m3u") or _xtream_url(out) or "")
        stream_candidates = []
        playlist_entries = 0
        playlist_ok = False
        try:
            _, _, body = _fetch(m3u, timeout=API_TIMEOUT, max_bytes=MAX_PLAYLIST_BYTES, retries=API_RETRIES, stream=False)
            text = body.decode("utf-8", "replace")
            playlist_entries = text.count("#EXTINF")
            playlist_ok = "#EXTM3U" in text[:8192] and playlist_entries > 0
            if playlist_ok:
                stream_candidates.extend(_playlist_stream_urls(text, m3u, limit=12))
        except Exception:
            pass

        if len(stream_candidates) < MAX_STREAM_CANDIDATES:
            for url in _xtream_api_stream_candidates(host, user, password, category_id=category_id):
                if url not in stream_candidates:
                    stream_candidates.append(url)

        if not stream_candidates:
            raise ValidationFailure("no_content", "account authenticated but no real stream candidate was found")

        probes = []
        for candidate in stream_candidates[:MAX_STREAM_CANDIDATES]:
            probe = _stream_probe(candidate, headers={"User-Agent": UA})
            probes.append(probe)
            if probe.get("ok"):
                out.update(
                    status="working",
                    detail="player_api + content + real stream proof: %s" % probe.get("detail"),
                    exp_date=exp,
                    active_cons=active_cons,
                    max_connections=max_cons,
                    allowed_output_formats=ui.get("allowed_output_formats"),
                    category_count=category_count,
                    playlist_entries=playlist_entries,
                    playlist_verified=bool(playlist_ok),
                    stream_proof=probe.get("proof"),
                    m3u=m3u,
                    ms=int((time.time() - started) * 1000),
                )
                if isinstance(si, dict):
                    out["server_timezone"] = si.get("timezone")
                return out

        failed_status, failed_detail = _pick_failed_probe(probes)
        try:
            ac = int(str(active_cons or "0"))
            mc = int(str(max_cons or "0"))
        except Exception:
            ac = mc = 0
        if mc > 0 and ac >= mc and failed_status in ("blocked", "auth_failed", "http_error", "stream_unavailable"):
            failed_status = "max_connections"
            failed_detail = "account is active but connection limit is reached and stream proof failed"
        out.update(
            status=failed_status,
            detail=failed_detail,
            exp_date=exp,
            active_cons=active_cons,
            max_connections=max_cons,
            category_count=category_count,
            category_note=category_note,
            playlist_entries=playlist_entries,
            playlist_verified=bool(playlist_ok),
            m3u=m3u,
            ms=int((time.time() - started) * 1000),
        )
        return out
    except ValidationFailure as exc:
        out.update(status=exc.status, detail=exc.detail, ms=int((time.time() - started) * 1000))
        if exc.http_code is not None:
            out["http_code"] = exc.http_code
        return out
    except Exception as exc:
        out.update(status=_net_status(exc), detail=str(exc)[:220], ms=int((time.time() - started) * 1000))
        return out


# ---------------------------------------------------------------------------
# Stalker / MAG deep check
# ---------------------------------------------------------------------------

def _portal_endpoints(url):
    raw = str(url or "").strip()
    p = _url_parts(raw)
    base = urllib.parse.urlunsplit((p.scheme, p.netloc, "", "", "")).rstrip("/")
    path = (p.path or "").rstrip("/")
    low = path.lower()
    cands = []
    if path.endswith("portal.php") or path.endswith("server/load.php"):
        cands.append(base + path)
    if "stalker_portal" in low:
        prefix = path[:low.find("stalker_portal")].rstrip("/")
        cands.append(base + prefix + "/stalker_portal/server/load.php")
    if low.endswith("/c"):
        cands.append(base + path[:-2] + "/server/load.php")
    cands.extend([
        base + "/portal.php",
        base + "/server/load.php",
        base + "/stalker_portal/server/load.php",
    ])
    out = []
    seen = set()
    for value in cands:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out[:5]


def _portal_headers(mac, token=None):
    headers = {
        "User-Agent": UA,
        "Cookie": "mac=%s; stb_lang=en; timezone=Europe%%2FLondon" % mac,
        "X-User-Agent": XUA,
        "Accept": "*/*",
    }
    if token:
        headers["Authorization"] = "Bearer " + str(token)
    return headers


def _portal_call(endpoint, headers, params, label="portal request"):
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(params)
    _, _, obj = _fetch_json(url, headers=headers, label=label, max_bytes=MAX_API_BYTES)
    js = obj.get("js") if isinstance(obj, dict) and "js" in obj else obj
    if _looks_token_error(js):
        raise ValidationFailure("invalid_token", "%s reported invalid token" % label)
    return js


def _portal_identity(mac):
    sn = hashlib.md5(mac.encode("utf-8")).hexdigest().upper()[:13]
    device_id = hashlib.sha256(mac.encode("utf-8")).hexdigest().upper()
    device_id2 = hashlib.sha256(device_id.encode("utf-8")).hexdigest().upper()
    hw_version_2 = hashlib.sha1(mac.encode("utf-8")).hexdigest()
    prehash = hashlib.sha1((sn + mac).encode("utf-8")).hexdigest()
    signature = hashlib.sha256((device_id + device_id).encode("utf-8")).hexdigest().upper()
    return sn, device_id, device_id2, hw_version_2, prehash, signature


def _portal_profile_params(mac, random_token="", full=True):
    sn, device_id, device_id2, hw_version_2, prehash, signature = _portal_identity(mac)
    if not full:
        return {
            "type": "stb", "action": "get_profile", "JsHttpRequest": "1-xml",
            "sn": sn, "device_id": "", "timestamp": str(int(time.time())),
        }
    metrics = json.dumps({"type": "STB", "model": "MAG254", "mac": mac, "sn": sn, "uid": "", "random": random_token or ""}, separators=(",", ":"))
    return {
        "type": "stb", "action": "get_profile", "JsHttpRequest": "1-xml",
        "hd": "1",
        "ver": "ImageDescription: 0.2.18-r23-254; PORTAL version: 5.3.0; API Version: JS API version: 343; STB API version: 146; Player Engine version: 0x58c",
        "num_banks": "2", "sn": sn, "stb_type": "MAG254", "client_type": "STB",
        "image_version": "218", "video_out": "hdmi", "device_id": device_id,
        "device_id2": device_id2, "signature": signature, "auth_second_step": "1",
        "hw_version": "2.6-IB-00", "not_valid_token": "0", "metrics": metrics,
        "hw_version_2": hw_version_2, "timestamp": str(int(time.time())),
        "api_signature": "262", "prehash": prehash,
    }


def _portal_handshake(endpoint, mac):
    base_headers = _portal_headers(mac)
    variants = [
        {"type": "stb", "action": "handshake", "token": "", "JsHttpRequest": "1-xml"},
        {"type": "stb", "action": "handshake", "token": "", "mac": mac, "JsHttpRequest": "1-xml"},
    ]
    last = None
    for params in variants:
        try:
            js = _portal_call(endpoint, base_headers, params, label="Portal handshake")
            token = js.get("token") if isinstance(js, dict) else None
            if token:
                return str(token), str(js.get("random") or "") if isinstance(js, dict) else ""
            last = ValidationFailure("auth_failed", "handshake returned no token")
        except ValidationFailure as exc:
            last = exc
            if exc.status in ("timeout", "unreachable", "tls_error", "http_error", "blocked"):
                break
    # Compatibility path used by mature MAG clients when a portal requests a
    # prehash-style handshake.
    if last and last.status not in ("timeout", "unreachable", "tls_error", "http_error", "blocked"):
        try:
            fake = secrets.token_hex(16).upper()
            prehash = hashlib.sha1(fake.encode("utf-8")).hexdigest()
            h = dict(base_headers)
            h["Authorization"] = "Bearer " + fake
            js = _portal_call(endpoint, h, {
                "type": "stb", "action": "handshake", "JsHttpRequest": "1-xml",
                "mac": mac, "prehash": prehash,
            }, label="Portal prehash handshake")
            token = js.get("token") if isinstance(js, dict) else None
            if token:
                return str(token), str(js.get("random") or "") if isinstance(js, dict) else ""
        except ValidationFailure as exc:
            last = exc
    raise last or ValidationFailure("auth_failed", "handshake returned no token")


def _portal_profile(endpoint, mac, token, random_token):
    headers = _portal_headers(mac, token)
    last = None
    for full in (True, False):
        try:
            js = _portal_call(endpoint, headers, _portal_profile_params(mac, random_token, full=full), label="Portal get_profile")
            if isinstance(js, dict):
                msg = str(js.get("msg") or "").lower()
                if "error" in msg and not js.get("play_token"):
                    last = ValidationFailure("inactive", "profile rejected: %s" % str(js.get("msg") or "error")[:120])
                    continue
                return js
            last = ValidationFailure("invalid_response", "get_profile returned no profile object")
        except ValidationFailure as exc:
            last = exc
            if exc.status in ("invalid_token", "timeout", "unreachable", "tls_error", "blocked"):
                break
    raise last or ValidationFailure("inactive", "profile unavailable")


def _portal_account(endpoint, mac, token):
    headers = _portal_headers(mac, token)
    last = None
    for type_name in ("account_info", "account"):
        try:
            js = _portal_call(endpoint, headers, {
                "type": type_name, "action": "get_main_info", "JsHttpRequest": "1-xml",
            }, label="Portal get_main_info")
            if isinstance(js, dict):
                return js
            last = ValidationFailure("invalid_response", "get_main_info returned no account object")
        except ValidationFailure as exc:
            last = exc
            if exc.status in ("invalid_token", "timeout", "unreachable", "tls_error", "blocked"):
                break
    raise last or ValidationFailure("invalid_response", "account information unavailable")


def _classify_portal_account(info):
    if not isinstance(info, dict):
        return "invalid_response", "account information was not an object", None
    status = str(info.get("status") or info.get("account_status") or "").strip().lower()
    blocked = info.get("blocked")
    expiry = (
        info.get("end_date") or info.get("exp_date") or info.get("expire_billing_date")
        or info.get("expire_date") or info.get("expiration")
    )
    if _truthy(blocked) or status in ("blocked", "banned"):
        return "blocked", "portal reports account blocked", expiry
    if status in ("disabled", "inactive", "not active", "not_active"):
        return "inactive", "portal reports account inactive", expiry
    if status in ("expired",) or _parse_expiry(expiry) is True:
        return "expired", "portal account is expired", expiry
    return "", "", expiry


def _rows_from_portal(js):
    if isinstance(js, list):
        return [x for x in js if isinstance(x, dict)]
    if isinstance(js, dict):
        for key in ("data", "items", "channels", "results"):
            value = js.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        if js and any(k in js for k in ("cmd", "id", "name")):
            return [js]
    return []


def _portal_catalogue(endpoint, mac, token):
    headers = _portal_headers(mac, token)
    # Live is preferred because it is the cheapest and most direct playback
    # proof.  VOD is a fallback for services that expose no live catalogue.
    attempts = [("itv", "get_genres"), ("vod", "get_categories")]
    errors = []
    for media_type, cat_action in attempts:
        category_id = "*"
        category_count = 0
        try:
            cats = _portal_call(endpoint, headers, {
                "type": media_type, "action": cat_action, "JsHttpRequest": "1-xml",
            }, label="Portal %s" % cat_action)
            cat_rows = _rows_from_portal(cats)
            category_count = len(cat_rows)
            for row in cat_rows:
                cid = row.get("id") or row.get("category_id") or row.get("genre_id")
                if cid not in (None, "", "0"):
                    category_id = cid
                    break
        except ValidationFailure as exc:
            errors.append(exc)

        params = {"type": media_type, "action": "get_ordered_list", "p": "1", "JsHttpRequest": "1-xml"}
        if media_type == "itv":
            params["genre"] = category_id
        else:
            params["category"] = category_id
        try:
            js = _portal_call(endpoint, headers, params, label="Portal get_ordered_list")
            rows = _rows_from_portal(js)
            playable = [r for r in rows if r.get("cmd") or r.get("command") or r.get("url")]
            if playable:
                return media_type, playable, category_count
        except ValidationFailure as exc:
            errors.append(exc)

        if media_type == "itv":
            try:
                js = _portal_call(endpoint, headers, {
                    "type": "itv", "action": "get_all_channels", "JsHttpRequest": "1-xml",
                }, label="Portal get_all_channels")
                rows = _rows_from_portal(js)
                playable = [r for r in rows if r.get("cmd") or r.get("command") or r.get("url")]
                if playable:
                    return media_type, playable, category_count
            except ValidationFailure as exc:
                errors.append(exc)
    for exc in errors:
        if exc.status in ("invalid_token", "blocked", "auth_failed", "timeout", "tls_error", "unreachable"):
            raise exc
    raise ValidationFailure("no_content", "portal authorized but no playable catalogue item was returned")


def _strip_player_wrapper(value):
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    first, sep, remainder = text.partition(" ")
    if sep and first.lower() in ("ffmpeg", "auto"):
        text = remainder.lstrip()
    match = re.search(r"(?i)(https?|rtmp|rtsp|udp|rtp)://\S+", text)
    if match:
        text = match.group(0)
    return text.strip().strip('"').strip("'")


def _needs_create_link(raw):
    low = str(raw or "").lower()
    return "localhost" in low or "///" in low or "/ch/" in low or "http" not in low


def _portal_stream_link(endpoint, mac, token, row, media_type="itv"):
    raw = str(row.get("cmd") or row.get("command") or row.get("url") or "").strip() if isinstance(row, dict) else str(row or "").strip()
    if not raw:
        raise ValidationFailure("stream_unavailable", "catalogue item contained no stream command")
    direct = _strip_player_wrapper(raw)
    if not _needs_create_link(raw) and _url_parts(direct).scheme.lower() in ("http", "https", "rtmp", "rtsp", "udp", "rtp"):
        return direct
    headers = _portal_headers(mac, token)
    api_type = "vod" if media_type in ("vod", "series", "episode") else "itv"
    js = _portal_call(endpoint, headers, {
        "type": api_type,
        "action": "create_link",
        "cmd": raw,
        "series": "0" if api_type == "itv" else "",
        "forced_storage": "0" if api_type == "itv" else "",
        "disable_ad": "0",
        "download": "0",
        "force_ch_link_check": "0",
        "JsHttpRequest": "1-xml",
    }, label="Portal create_link")
    value = None
    if isinstance(js, dict):
        value = js.get("cmd") or js.get("url") or js.get("link")
    elif isinstance(js, str):
        value = js
    clean = _strip_player_wrapper(value)
    if not clean:
        raise ValidationFailure("stream_unavailable", "create_link returned no playable URL")
    return clean


def validate_portal(x, preferred_endpoint=None):
    out = dict(x or {})
    url = str(out.get("url") or "").strip()
    mac = str(out.get("mac") or "").upper().replace("-", ":")
    started = time.time()
    if not url or not re.match(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$", mac):
        out.update(status="invalid", detail="missing/invalid portal or MAC")
        return out

    endpoints = _portal_endpoints(url)
    if preferred_endpoint and preferred_endpoint in endpoints:
        endpoints.remove(preferred_endpoint)
        endpoints.insert(0, preferred_endpoint)
    last = None
    failures = []
    for endpoint in endpoints:
        handshake_ok = False
        try:
            token, random_token = _portal_handshake(endpoint, mac)
            handshake_ok = True
            profile = _portal_profile(endpoint, mac, token, random_token)
            account = _portal_account(endpoint, mac, token)
            account_status, account_detail, expiry = _classify_portal_account(account)
            if account_status:
                out.update(
                    status=account_status,
                    detail=account_detail,
                    endpoint=endpoint,
                    exp_date=expiry,
                    ms=int((time.time() - started) * 1000),
                )
                return out

            media_type, rows, category_count = _portal_catalogue(endpoint, mac, token)
            probes = []
            for row in rows[:MAX_STREAM_CANDIDATES]:
                try:
                    stream_url = _portal_stream_link(endpoint, mac, token, row, media_type=media_type)
                    probe = _stream_probe(stream_url, headers=_portal_headers(mac, token))
                    probes.append(probe)
                    if probe.get("ok"):
                        out.update(
                            status="working",
                            detail="handshake + profile + account + content + real stream proof: %s" % probe.get("detail"),
                            endpoint=endpoint,
                            exp_date=expiry,
                            media_type=media_type,
                            category_count=category_count,
                            content_sample=len(rows),
                            stream_proof=probe.get("proof"),
                            play_token=bool(profile.get("play_token")) if isinstance(profile, dict) else False,
                            ms=int((time.time() - started) * 1000),
                        )
                        return out
                except ValidationFailure as exc:
                    probes.append({"ok": False, "status": exc.status, "detail": exc.detail})
            status, detail = _pick_failed_probe(probes)
            out.update(
                status=status,
                detail=detail,
                endpoint=endpoint,
                exp_date=expiry,
                media_type=media_type,
                category_count=category_count,
                content_sample=len(rows),
                ms=int((time.time() - started) * 1000),
            )
            return out
        except ValidationFailure as exc:
            last = exc
            failures.append((endpoint, exc, handshake_ok))
            # Once a real Stalker handshake succeeded, account/token/content
            # failures are meaningful for this MAC and must not be hidden by a
            # later 404 from another guessed endpoint.
            if handshake_ok and exc.status in ("blocked", "auth_failed", "invalid_token", "inactive", "expired", "no_content"):
                out.update(status=exc.status, detail=exc.detail, endpoint=endpoint, ms=int((time.time() - started) * 1000))
                if exc.http_code is not None:
                    out["http_code"] = exc.http_code
                return out
            # Endpoint/path mismatch may continue. Real network/TLS failures
            # should not multiply timeouts across every candidate path.
            if exc.status in ("timeout", "unreachable", "tls_error"):
                break
            continue
        except Exception as exc:
            last = _failure_from_exc(exc, "portal check")
            failures.append((endpoint, last, handshake_ok))
            if last.status in ("timeout", "unreachable", "tls_error"):
                break
    if failures:
        # Preserve the most diagnostic failure instead of blindly returning the
        # final guessed endpoint's error.  In particular auth/token failures
        # outrank a later 404/invalid-response path miss.
        priority = {
            "blocked": 100, "invalid_token": 95, "auth_failed": 90,
            "inactive": 85, "expired": 85, "no_content": 80,
            "timeout": 70, "tls_error": 68, "unreachable": 66,
            "http_error": 40, "invalid_response": 30,
        }
        endpoint, failure, _ = max(failures, key=lambda row: priority.get(row[1].status, 10))
    else:
        endpoint, failure = "", (last or ValidationFailure("invalid_response", "no compatible Stalker endpoint passed handshake/profile"))
    out.update(status=failure.status, detail=failure.detail, ms=int((time.time() - started) * 1000))
    if endpoint:
        out["endpoint"] = endpoint
    if failure.http_code is not None:
        out["http_code"] = failure.http_code
    return out


# ---------------------------------------------------------------------------
# M3U deep check
# ---------------------------------------------------------------------------

def validate_m3u(x):
    out = dict(x or {})
    url = str(out.get("url") or "").strip()
    started = time.time()
    if not url:
        out.update(status="invalid", detail="missing playlist URL")
        return out
    try:
        code, headers, body = _fetch(url, timeout=API_TIMEOUT, max_bytes=MAX_PLAYLIST_BYTES, retries=API_RETRIES, stream=False)
        text = body.decode("utf-8", "replace")
        head = text[:8192]
        extinf = text.count("#EXTINF")
        if "#EXTM3U" not in head or extinf <= 0:
            out.update(status="invalid_response", detail="HTTP %s returned no valid #EXTM3U/#EXTINF playlist" % code, ms=int((time.time() - started) * 1000))
            return out
        candidates = _playlist_stream_urls(text, url, limit=12)
        if not candidates:
            out.update(status="no_content", detail="playlist is valid but contains no stream URL", playlist_entries=extinf, ms=int((time.time() - started) * 1000))
            return out
        probes = []
        for candidate in candidates[:MAX_STREAM_CANDIDATES]:
            probe = _stream_probe(candidate, headers={"User-Agent": UA})
            probes.append(probe)
            if probe.get("ok"):
                out.update(
                    status="working",
                    detail="playlist + real stream proof: %s" % probe.get("detail"),
                    playlist_entries=extinf,
                    stream_proof=probe.get("proof"),
                    ms=int((time.time() - started) * 1000),
                )
                return out
        status, detail = _pick_failed_probe(probes)
        out.update(status=status, detail=detail, playlist_entries=extinf, ms=int((time.time() - started) * 1000))
        return out
    except Exception as exc:
        out.update(status=_net_status(exc), detail=str(exc)[:220], ms=int((time.time() - started) * 1000))
        if isinstance(exc, urllib.error.HTTPError):
            out["http_code"] = exc.code
        return out


# ---------------------------------------------------------------------------
# Host intelligence + aggregation
# ---------------------------------------------------------------------------

def _kind_host(kind, item):
    if kind == "xtream":
        return _host_from_url(item.get("host"))
    return _host_from_url(item.get("url"))


def _job_url(kind, item):
    return str(item.get("host") if kind == "xtream" else item.get("url") or "")


def _host_preflight(jobs):
    """Two TCP attempts before declaring an entire host dead.

    This avoids hundreds of identical API timeouts while still requiring more
    than one transient failure before host-wide skipping.
    """
    first_url = ""
    for kind, item in jobs:
        candidate = _job_url(kind, item)
        if candidate:
            first_url = candidate
            break
    p = _url_parts(first_url)
    if not p.hostname:
        return True, ""
    port = p.port or (443 if p.scheme == "https" else 80)
    last = None
    statuses = []
    for attempt in range(2):
        sock = None
        try:
            infos = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
            if not infos:
                raise OSError("no address")
            family, socktype, proto, _canon, sockaddr = infos[0]
            sock = socket.socket(family, socktype or socket.SOCK_STREAM, proto)
            sock.settimeout(HOST_PROBE_TIMEOUT)
            sock.connect(sockaddr)
            return True, ""
        except Exception as exc:
            last = exc
            statuses.append(_net_status(exc))
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        if attempt == 0:
            time.sleep(0.2)
    status = "timeout" if statuses and all(x == "timeout" for x in statuses) else "unreachable"
    return False, "%s: host preflight failed twice: %s" % (status, str(last)[:160])


def _progress_label(kind, item):
    """Privacy-safe label for the browser progress UI (never includes passwords)."""
    raw = item.get("host") if kind == "xtream" else item.get("url")
    host = _host_from_url(raw) or str(raw or "")
    if kind == "portal":
        mac = str(item.get("mac") or "").upper()
        if mac:
            parts = mac.split(":")
            if len(parts) == 6:
                mac = "%s:%s:**:**:%s:%s" % (parts[0], parts[1], parts[4], parts[5])
            return "PORTAL • %s • %s" % (host, mac)
    return "%s • %s" % (str(kind or "unknown").upper(), host)


def validate_all(payload, progress=None, cancel_event=None):
    grouped = {}
    total = 0
    for kind in ("portal", "xtream", "m3u"):
        rows = payload.get(kind) if isinstance(payload, dict) else []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            total += 1
            host = _kind_host(kind, item) or "__invalid__%s" % len(grouped)
            grouped.setdefault(host, []).append((kind, item))

    if progress:
        progress({"event": "start", "total": total, "hosts": len(grouped), "phase": "Preparing deep check"})

    endpoint_cache = {}
    endpoint_lock = threading.Lock()

    def emit(event, **extra):
        if progress:
            data = {"event": event}
            data.update(extra)
            try:
                progress(data)
            except Exception:
                pass

    def run_host(host, jobs):
        results = []
        if cancel_event is not None and cancel_event.is_set():
            return results
        if not host.startswith("__invalid__"):
            emit("host_preflight", phase="Host preflight", current="Testing %s" % host, host=host)
            reachable, why = _host_preflight(jobs)
            if not reachable:
                status = "timeout" if why.startswith("timeout:") else "unreachable"
                for kind, item in jobs:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    emit("item_start", kind=kind, current=_progress_label(kind, item), phase="Host unavailable")
                    row = dict(item)
                    row.update(status=status, detail=why)
                    results.append((kind, row))
                    emit("item_done", kind=kind, status=status, current=_progress_label(kind, item), phase="Host unavailable")
                return results
        for kind, item in jobs:
            if cancel_event is not None and cancel_event.is_set():
                break
            label = _progress_label(kind, item)
            emit("item_start", kind=kind, current=label, phase="Checking %s" % kind.upper())
            if kind == "portal":
                with endpoint_lock:
                    preferred = endpoint_cache.get(host)
                row = validate_portal(item, preferred_endpoint=preferred)
                endpoint = row.get("endpoint")
                if endpoint:
                    with endpoint_lock:
                        endpoint_cache[host] = endpoint
            elif kind == "xtream":
                row = validate_xtream(item)
            else:
                row = validate_m3u(item)
            results.append((kind, row))
            emit("item_done", kind=kind, status=str(row.get("status") or "unknown"), current=label, phase="Verified %s" % kind.upper())
        return results

    flat = []
    hosts = list(grouped.items())
    if hosts:
        with ThreadPoolExecutor(max_workers=min(MAX_HOST_WORKERS, len(hosts))) as pool:
            futures = [pool.submit(run_host, host, jobs) for host, jobs in hosts]
            for future in as_completed(futures):
                try:
                    flat.extend(future.result())
                except Exception as exc:
                    flat.append(("unknown", {"status": "invalid_response", "detail": str(exc)[:220]}))

    emit("finalizing", phase="Finalizing report", current="Building Working-only results")
    result = {"portal": [], "xtream": [], "m3u": []}
    counts = {}
    counts_by_kind = {"portal": {}, "xtream": {}, "m3u": {}}
    for kind, row in flat:
        if kind in result:
            result[kind].append(row)
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        if kind in counts_by_kind:
            bucket = counts_by_kind[kind]
            bucket[status] = bucket.get(status, 0) + 1
    return {
        "results": result,
        "counts": counts,
        "counts_by_kind": counts_by_kind,
        "hosts": len(grouped),
        "engine": "deepcheck-v5",
        "proof_rule": "working requires real media bytes or HLS media segment",
    }


def _validation_progress(job_id, event):
    """Apply progress only to the job that emitted it.

    A cancelled/superseded worker may still be finishing an in-flight socket
    timeout for a few seconds. Binding progress to the immutable job id keeps
    that old worker from ever contaminating a newer file/job.
    """
    global _VALIDATION_JOB
    with _LOCK:
        job = _VALIDATION_JOB
        if (not isinstance(job, dict) or job.get("id") != job_id or
                job.get("state") != "running"):
            return
        now = time.time()
        job["updated"] = now
        ev = str((event or {}).get("event") or "")
        if "phase" in (event or {}):
            job["phase"] = str(event.get("phase") or "")[:120]
        if "current" in (event or {}):
            job["current"] = str(event.get("current") or "")[:240]
        if ev == "start":
            job["total"] = int(event.get("total") or job.get("total") or 0)
            job["hosts"] = int(event.get("hosts") or 0)
        elif ev == "item_start":
            job["active"] = int(job.get("active") or 0) + 1
        elif ev == "item_done":
            job["completed"] = int(job.get("completed") or 0) + 1
            job["active"] = max(0, int(job.get("active") or 0) - 1)
            status = str(event.get("status") or "unknown")
            counts = job.setdefault("counts", {})
            counts[status] = int(counts.get(status) or 0) + 1
            kind = str(event.get("kind") or "unknown")
            by_kind = job.setdefault("counts_by_kind", {})
            bucket = by_kind.setdefault(kind, {})
            bucket[status] = int(bucket.get(status) or 0) + 1


def _cancel_validation_job(job_id=None, clear=False, reason="Deep Check cancelled"):
    """Cancel the current validation without letting it leak into the next one."""
    global _VALIDATION_JOB
    with _LOCK:
        job = _VALIDATION_JOB
        if not isinstance(job, dict):
            return True, {"ok": True, "state": "idle"}
        if job_id and job.get("id") != job_id:
            return False, _validation_job_snapshot()
        ev = job.get("_cancel_event")
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass
        if job.get("state") == "running":
            job["state"] = "cancelled"
            job["phase"] = "Deep Check cancelled"
            job["current"] = str(reason or "Deep Check cancelled")[:240]
            job["active"] = 0
            job["updated"] = time.time()
        if clear:
            _VALIDATION_JOB = None
            return True, {"ok": True, "state": "idle", "cleared": True}
        return True, _validation_job_snapshot()


def _validation_job_snapshot():
    with _LOCK:
        job = _VALIDATION_JOB
        if not isinstance(job, dict):
            return {"ok": True, "state": "idle"}
        snap = {
            "ok": True,
            "id": job.get("id"),
            "state": job.get("state"),
            "phase": job.get("phase") or "",
            "current": job.get("current") or "",
            "total": int(job.get("total") or 0),
            "completed": int(job.get("completed") or 0),
            "active": int(job.get("active") or 0),
            "hosts": int(job.get("hosts") or 0),
            "counts": dict(job.get("counts") or {}),
            "counts_by_kind": dict(job.get("counts_by_kind") or {}),
            "started": float(job.get("started") or 0),
            "updated": float(job.get("updated") or 0),
        }
        if job.get("state") == "done":
            snap["result"] = job.get("result")
        elif job.get("state") == "failed":
            snap["error"] = str(job.get("error") or "Deep Check failed")[:300]
        return snap


def _start_validation_job(payload):
    global _VALIDATION_JOB, _VALIDATION_THREAD
    total = 0
    for kind in ("portal", "xtream", "m3u"):
        rows = payload.get(kind) if isinstance(payload, dict) else []
        total += sum(1 for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict))
    if total <= 0:
        raise ValueError("no entries to validate")

    # Every Start is a fresh job. If an older file is still being checked,
    # cancel it first instead of returning its stale 3982-style progress.
    cancel_event = threading.Event()
    with _LOCK:
        old = _VALIDATION_JOB
        if isinstance(old, dict):
            old_ev = old.get("_cancel_event")
            if old_ev is not None:
                try:
                    old_ev.set()
                except Exception:
                    pass
        now = time.time()
        job_id = secrets.token_urlsafe(9)
        _VALIDATION_JOB = {
            "id": job_id,
            "state": "running",
            "phase": "Preparing deep check",
            "current": "Queueing services",
            "total": total,
            "completed": 0,
            "active": 0,
            "hosts": 0,
            "counts": {},
            "counts_by_kind": {"portal": {}, "xtream": {}, "m3u": {}},
            "started": now,
            "updated": now,
            "result": None,
            "error": "",
            "_cancel_event": cancel_event,
        }

    def progress(event):
        _validation_progress(job_id, event)

    def worker(expected_id, expected_cancel):
        global _VALIDATION_JOB
        try:
            result = validate_all(payload, progress=progress, cancel_event=expected_cancel)
            with _LOCK:
                if (isinstance(_VALIDATION_JOB, dict) and
                        _VALIDATION_JOB.get("id") == expected_id and
                        _VALIDATION_JOB.get("state") == "running"):
                    _VALIDATION_JOB["state"] = "done"
                    _VALIDATION_JOB["phase"] = "Deep Check complete"
                    _VALIDATION_JOB["current"] = "Real stream proof finished"
                    _VALIDATION_JOB["result"] = result
                    _VALIDATION_JOB["counts"] = dict(result.get("counts") or {})
                    _VALIDATION_JOB["counts_by_kind"] = dict(result.get("counts_by_kind") or {})
                    _VALIDATION_JOB["completed"] = int(_VALIDATION_JOB.get("total") or 0)
                    _VALIDATION_JOB["active"] = 0
                    _VALIDATION_JOB["updated"] = time.time()
        except Exception as exc:
            with _LOCK:
                if (isinstance(_VALIDATION_JOB, dict) and
                        _VALIDATION_JOB.get("id") == expected_id and
                        _VALIDATION_JOB.get("state") == "running"):
                    _VALIDATION_JOB["state"] = "failed"
                    _VALIDATION_JOB["phase"] = "Deep Check failed"
                    _VALIDATION_JOB["current"] = ""
                    _VALIDATION_JOB["error"] = str(exc)[:300]
                    _VALIDATION_JOB["active"] = 0
                    _VALIDATION_JOB["updated"] = time.time()

    _VALIDATION_THREAD = threading.Thread(target=worker, args=(job_id, cancel_event), name="UltraStalkerDeepCheck", daemon=True)
    _VALIDATION_THREAD.start()
    return _validation_job_snapshot()


def _existing_keys():
    from . import storage
    keys = set()
    try:
        for p in storage.parse_import_file():
            typ = str(p.get("source_type") or "stalker")
            portal = str(p.get("portal") or "").rstrip("/")
            mac = str(p.get("mac") or "").upper()
            if typ == "m3u":
                keys.add(("m3u", portal))
            else:
                keys.add(("portal", portal.lower(), mac))
    except Exception:
        pass
    return keys


def add_valid(results):
    """Atomically append only DeepCheck-verified working rows to portals.txt."""
    from . import storage
    storage.ensure_files()
    existing = _existing_keys()
    additions = []
    added = {"portal": 0, "xtream": 0, "m3u": 0}
    skipped = 0

    for row in (results.get("portal") or []) if isinstance(results, dict) else []:
        if not isinstance(row, dict) or row.get("status") != "working" or not row.get("stream_proof"):
            continue
        url = str(row.get("url") or "").strip().rstrip("/")
        mac = str(row.get("mac") or "").strip().upper().replace("-", ":")
        key = ("portal", url.lower(), mac)
        if not url or not mac or key in existing:
            skipped += 1
            continue
        existing.add(key)
        additions.append(url + "|" + mac)
        added["portal"] += 1

    for row in (results.get("xtream") or []) if isinstance(results, dict) else []:
        if not isinstance(row, dict) or row.get("status") != "working" or not row.get("stream_proof"):
            continue
        url = str(row.get("m3u") or _xtream_url(row) or "").strip().rstrip("/")
        key = ("m3u", url)
        if not url or key in existing:
            skipped += 1
            continue
        existing.add(key)
        additions.append(url)
        added["xtream"] += 1

    for row in (results.get("m3u") or []) if isinstance(results, dict) else []:
        if not isinstance(row, dict) or row.get("status") != "working" or not row.get("stream_proof"):
            continue
        url = str(row.get("url") or "").strip().rstrip("/")
        key = ("m3u", url)
        if not url or key in existing:
            skipped += 1
            continue
        existing.add(key)
        additions.append(url)
        added["m3u"] += 1

    if not additions:
        return {"ok": True, "added": added, "skipped": skipped, "path": storage.IMPORT_FILE, "message": "Nothing new to add"}

    with storage.STATE_IO_LOCK:
        try:
            with open(storage.IMPORT_FILE, "r", encoding="utf-8", errors="replace") as handle:
                current = handle.read()
        except OSError:
            current = "# Ultra Stalker import file\n"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = storage.IMPORT_FILE + ".webcleaner-" + stamp + ".bak"
        try:
            with open(backup, "w", encoding="utf-8") as handle:
                handle.write(current)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(backup, 0o600)
        except OSError:
            backup = ""
        text = current
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n# Added by Ultra Stalker Web Cleaner %s\n" % stamp
        text += "\n".join(additions) + "\n"
        tmp = storage.IMPORT_FILE + ".webcleaner.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, storage.IMPORT_FILE)
        try:
            storage._fsync_parent_dir(storage.IMPORT_FILE)
        except Exception:
            pass
    return {"ok": True, "added": added, "skipped": skipped, "path": storage.IMPORT_FILE, "backup": backup}



def _web_connection_payload(payload):
    """Normalize one browser Add Connection form into the existing profile model."""
    if not isinstance(payload, dict):
        raise ValueError("invalid connection request")
    kind = str(payload.get("kind") or "").strip().lower()
    name = str(payload.get("name") or "").strip()[:80]
    if kind == "portal":
        url = str(payload.get("url") or "").strip().rstrip("/")
        mac = str(payload.get("mac") or "").strip().upper().replace("-", ":")
        if not url or not mac:
            raise ValueError("Portal URL and MAC address are required")
        return kind, name, {"url": url, "mac": mac}
    if kind == "xtream":
        host = str(payload.get("url") or payload.get("host") or "").strip().rstrip("/")
        user = str(payload.get("username") or payload.get("user") or "").strip()
        password = str(payload.get("password") or payload.get("pass") or "")
        if not host or not user or not password:
            raise ValueError("Server URL, username and password are required")
        return kind, name, {"host": host, "user": user, "pass": password}
    if kind == "m3u":
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("M3U URL is required")
        return kind, name, {"url": url}
    raise ValueError("connection type must be portal, xtream or m3u")


def test_web_connection(payload):
    kind, name, item = _web_connection_payload(payload)
    if kind == "portal":
        result = validate_portal(item)
    elif kind == "xtream":
        result = validate_xtream(item)
    else:
        result = validate_m3u(item)
    return {"ok": True, "kind": kind, "name": name, "result": result}


def save_web_connection(payload):
    """Save one browser-created connection using Ultra Stalker's normal profile storage."""
    from . import storage
    kind, name, item = _web_connection_payload(payload)
    storage.ensure_files()
    if kind == "portal":
        raw = {
            "portal": item["url"], "mac": item["mac"], "name": name,
            "source": "web", "source_type": "stalker", "state": "Not checked",
            "explicit_http_accepted": item["url"].lower().startswith("http://"),
        }
    elif kind == "xtream":
        playlist = _xtream_url(item)
        if not playlist:
            raise ValueError("Unable to build Xtream playlist URL")
        raw = {
            "portal": playlist, "name": name, "source": "web",
            "source_type": "m3u", "state": "Not checked",
            "explicit_http_accepted": playlist.lower().startswith("http://"),
        }
    else:
        raw = {
            "portal": item["url"], "name": name, "source": "web",
            "source_type": "m3u", "state": "Not checked",
            "explicit_http_accepted": item["url"].lower().startswith("http://"),
        }
    normalized = storage._normalize_profile(raw, "web")
    if not normalized:
        raise ValueError("Connection details are not valid for Ultra Stalker")
    key = storage._profile_key(normalized)
    for existing in storage.load_profiles():
        if storage._profile_key(existing) == key:
            return {"ok": True, "saved": False, "duplicate": True, "kind": kind, "message": "Connection already exists"}
    saved = storage._read_json_profiles()
    saved.append(normalized)
    storage.save_profiles(saved)
    return {
        "ok": True, "saved": True, "duplicate": False, "kind": kind,
        "name": normalized.get("name") or name,
        "message": "%s connection saved to Ultra Stalker" % kind.upper(),
    }


def _web_profile_kind(profile):
    """Return browser editor kind without changing the persisted profile model."""
    p = profile if isinstance(profile, dict) else {}
    if str(p.get("source_type") or "").strip().lower() != "m3u":
        return "portal"
    raw = str(p.get("portal") or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.path or "").lower().endswith("/get.php") and query.get("username") and query.get("password"):
            return "xtream"
    except Exception:
        pass
    low = raw.lower()
    if "get.php" in low and "username=" in low and "password=" in low:
        return "xtream"
    return "m3u"


def _web_profile_editor_row(profile, index):
    p = profile if isinstance(profile, dict) else {}
    kind = _web_profile_kind(p)
    raw_url = str(p.get("portal") or "").strip()
    from . import storage
    row = {
        "position": int(index) + 1,
        "kind": kind,
        "name": str(p.get("name") or "").strip(),
        "key": list(storage._profile_key(p) or ("", "")),
        "url": raw_url,
        "mac": str(p.get("mac") or "").strip() if kind == "portal" else "",
        "username": "",
        "password": "",
    }
    if kind == "xtream":
        try:
            parsed = urllib.parse.urlsplit(raw_url)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            row["username"] = (query.get("username") or [""])[0]
            row["password"] = (query.get("password") or [""])[0]
            prefix = (parsed.path or "").rsplit("/", 1)[0]
            row["url"] = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, prefix, "", "")).rstrip("/")
        except Exception:
            pass
    return row


def list_web_connections():
    """Return one compact, ordered browser-editable view of receiver connections."""
    from . import storage
    rows = storage.load_profiles()
    return {"ok": True, "count": len(rows), "connections": [_web_profile_editor_row(p, i) for i, p in enumerate(rows)]}


def update_web_connection(payload):
    """Edit one existing connection and/or move it to a new real list position."""
    from . import storage
    if not isinstance(payload, dict):
        raise ValueError("invalid connection edit request")
    old_key_raw = payload.get("key")
    if not isinstance(old_key_raw, (list, tuple)) or len(old_key_raw) != 2:
        raise ValueError("missing connection identity")
    old_key = (str(old_key_raw[0] or ""), str(old_key_raw[1] or ""))
    rows = list(storage.load_profiles())
    current_index = None
    for i, profile in enumerate(rows):
        if storage._profile_key(profile) == old_key:
            current_index = i
            break
    if current_index is None:
        raise ValueError("connection no longer exists; reload the list")

    current = dict(rows[current_index])
    current_kind = _web_profile_kind(current)
    requested_kind = str(payload.get("kind") or current_kind).strip().lower()
    if requested_kind != current_kind:
        raise ValueError("connection type cannot be changed in the editor")
    kind, name, item = _web_connection_payload({
        "kind": current_kind,
        "name": payload.get("name"),
        "url": payload.get("url"),
        "mac": payload.get("mac"),
        "username": payload.get("username"),
        "password": payload.get("password"),
    })

    updated = dict(current)
    connection_changed = False
    if kind == "portal":
        new_portal = item["url"]
        new_mac = item["mac"]
        connection_changed = (new_portal.rstrip("/").lower(), new_mac) != (str(current.get("portal") or "").rstrip("/").lower(), str(current.get("mac") or ""))
        updated.update({"portal": new_portal, "mac": new_mac, "source_type": "stalker"})
    elif kind == "xtream":
        playlist = _xtream_url(item)
        if not playlist:
            raise ValueError("Unable to build Xtream playlist URL")
        connection_changed = playlist.rstrip("/").lower() != str(current.get("portal") or "").rstrip("/").lower()
        updated.update({"portal": playlist, "source_type": "m3u"})
    else:
        new_url = item["url"]
        connection_changed = new_url.rstrip("/").lower() != str(current.get("portal") or "").rstrip("/").lower()
        updated.update({"portal": new_url, "source_type": "m3u"})
    updated["name"] = name
    # A browser edit becomes an explicit saved receiver row.  This prevents a
    # changed URL/MAC from being resurrected by an older text import later.
    updated["source"] = "web"
    updated["explicit_http_accepted"] = str(updated.get("portal") or "").lower().startswith("http://")
    if connection_changed:
        updated["state"] = "Not checked"
        updated["health"] = "unknown"
        for key in ("account_state", "expiry", "last_error", "last_success", "active_connections", "max_connections", "connections_source"):
            updated.pop(key, None)
    normalized = storage._normalize_profile(updated, "web")
    if not normalized:
        raise ValueError("edited connection is not valid")
    new_key = storage._profile_key(normalized)
    for i, other in enumerate(rows):
        if i != current_index and storage._profile_key(other) == new_key:
            raise ValueError("another saved connection already uses these details")

    rows[current_index] = normalized
    try:
        target = int(payload.get("position") or (current_index + 1))
    except (TypeError, ValueError):
        target = current_index + 1
    target = max(1, min(target, len(rows)))
    moved = rows.pop(current_index)
    rows.insert(target - 1, moved)
    storage.save_profiles(rows)
    if old_key != new_key:
        try:
            storage.disable_profiles([current])
        except Exception:
            pass
    final_index = next((i for i, x in enumerate(rows) if storage._profile_key(x) == new_key), target - 1)
    return {
        "ok": True,
        "message": "Connection updated on the receiver",
        "connection": _web_profile_editor_row(rows[final_index], final_index),
        "count": len(rows),
    }

def _cookie_token(value):
    for part in str(value or "").split(";"):
        if "=" in part:
            key, val = part.strip().split("=", 1)
            if key == "uswc":
                return val
    return ""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "UltraStalkerWebCleaner/0.3"

    def log_message(self, fmt, *args):
        return

    def _authorized(self):
        global _TOKEN, _ACCESS_MODE
        client_ip = self.client_address[0] if self.client_address else ""
        if _ACCESS_MODE == "easy":
            return _lan_client_allowed(client_ip)
        if not _TOKEN:
            return False
        if _cookie_token(self.headers.get("Cookie")) == _TOKEN:
            return True
        if self.headers.get("X-US-Token") == _TOKEN:
            return True
        try:
            query = urllib.parse.parse_qs(_url_parts(self.path).query)
            return (query.get("token") or [""])[0] == _TOKEN
        except Exception:
            return False

    def _query_token(self):
        try:
            query = urllib.parse.parse_qs(_url_parts(self.path).query)
            return (query.get("token") or [""])[0]
        except Exception:
            return ""

    def _query_pair_code(self):
        try:
            query = urllib.parse.parse_qs(_url_parts(self.path).query)
            return (query.get("pair") or [""])[0]
        except Exception:
            return ""

    def _send_pair_page(self, code=200, error=""):
        data = _pair_page(error)
        self.send_response(int(code))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect_authorized(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", "uswc=%s; Path=/; HttpOnly; SameSite=Strict" % _TOKEN)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, code, obj):
        data = _json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self, set_cookie=False):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as handle:
                page = handle.read()
            data = _inject_web_i18n(page).encode("utf-8")
        except OSError:
            self._send_json(500, {"error": _wc_tr("Content temporarily unavailable. Try again.")})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self' data: 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        if set_cookie:
            self.send_header("Set-Cookie", "uswc=%s; Path=/; HttpOnly; SameSite=Strict" % _TOKEN)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = _url_parts(self.path).path
        if path in ("/", "/cleaner.html"):
            client_ip = self.client_address[0] if self.client_address else ""
            if _ACCESS_MODE == "easy":
                if not _lan_client_allowed(client_ip):
                    self._send_json(403, {"error": "LAN access only"})
                    return
                self._serve_index(set_cookie=False)
                return
            cookie_ok = bool(_TOKEN and _cookie_token(self.headers.get("Cookie")) == _TOKEN)
            token_ok = bool(_TOKEN and (self._query_token() == _TOKEN or self.headers.get("X-US-Token") == _TOKEN))
            pair_query = self._query_pair_code()
            if cookie_ok:
                self._serve_index(set_cookie=False)
                return
            if pair_query:
                ok, why = _pair_check(self.client_address[0] if self.client_address else "", pair_query)
                if ok:
                    # QR pairing exchanges the short on-screen code for the long
                    # HttpOnly session token, then scrubs the code from the URL.
                    self._redirect_authorized()
                else:
                    code = 429 if str(why).startswith("Too many") else 401
                    self._send_pair_page(code, why)
                return
            if token_ok:
                # Legacy token links remain accepted but are never displayed or
                # encoded by Beta 3. Redirect removes the token from history.
                self._redirect_authorized()
                return
            self._send_pair_page(200)
            return
        if path == "/api/connections":
            if not self._authorized():
                self._send_json(403, {"error": "unauthorized"})
                return
            self._send_json(200, list_web_connections())
            return
        if path == "/api/status":
            if not self._authorized():
                self._send_json(403, {"error": "unauthorized"})
                return
            self._send_json(200, {"ok": True, "port": PORT, "version": "V6", "engine": "deepcheck-v5", "access_mode": _ACCESS_MODE, "pairing": _ACCESS_MODE == "protected", "live_progress": True})
            return
        if path == "/api/validate-progress":
            if not self._authorized():
                self._send_json(403, {"error": "unauthorized"})
                return
            snap = _validation_job_snapshot()
            requested = (urllib.parse.parse_qs(_url_parts(self.path).query).get("job") or [""])[0]
            if requested and requested != snap.get("id"):
                self._send_json(404, {"error": "validation job not found"})
                return
            self._send_json(200, snap)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = _url_parts(self.path).path
        if path == "/pair":
            if _ACCESS_MODE != "protected":
                if _lan_client_allowed(self.client_address[0] if self.client_address else ""):
                    self._redirect_authorized()
                else:
                    self._send_json(403, {"error": "LAN access only"})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except Exception:
                n = 0
            if n <= 0 or n > 4096:
                self._send_pair_page(400, _wc_tr("Incorrect pairing code."))
                return
            raw = self.rfile.read(n).decode("utf-8", "replace")
            ctype = str(self.headers.get("Content-Type") or "").lower()
            try:
                if "application/json" in ctype:
                    obj = json.loads(raw)
                    supplied = obj.get("code") if isinstance(obj, dict) else ""
                else:
                    supplied = (urllib.parse.parse_qs(raw).get("code") or [""])[0]
            except Exception:
                supplied = ""
            ok, why = _pair_check(self.client_address[0] if self.client_address else "", supplied)
            if ok:
                self._redirect_authorized()
            else:
                code = 429 if str(why).startswith("Too many") else 401
                self._send_pair_page(code, why)
            return

        if not self._authorized():
            self._send_json(403, {"error": "unauthorized"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except Exception:
            n = 0
        if n <= 0 or n > MAX_BODY:
            self._send_json(413, {"error": "request too large or empty"})
            return
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
            if path == "/api/validate-start":
                self._send_json(202, _start_validation_job(payload))
                return
            if path == "/api/validate-cancel":
                requested = str(payload.get("job") or "") if isinstance(payload, dict) else ""
                clear = bool(payload.get("clear")) if isinstance(payload, dict) else False
                ok, snap = _cancel_validation_job(requested or None, clear=clear, reason="Stopped by browser")
                if not ok:
                    self._send_json(404, {"error": "validation job not found"})
                else:
                    self._send_json(200, snap)
                return
            if path == "/api/validate":
                # Backward-compatible synchronous endpoint used by existing tests/tools.
                self._send_json(200, validate_all(payload))
                return
            if path == "/api/add-valid":
                results = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(results, dict):
                    raise ValueError("missing validation results")
                self._send_json(200, add_valid(results))
                return
            if path == "/api/connection-test":
                self._send_json(200, test_web_connection(payload))
                return
            if path == "/api/connection-save":
                self._send_json(200, save_web_connection(payload))
                return
            if path == "/api/connection-update":
                self._send_json(200, update_web_connection(payload))
                return
            self._send_json(404, {"error": "not found"})
        except Exception as exc:
            self._send_json(400, {"error": str(exc)[:300]})


def receiver_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "RECEIVER-IP"


def start():
    global _SERVER, _THREAD, _TOKEN, _PAIR_CODE, _PAIR_FAILURES, _ACCESS_MODE, _LAST_URL, _QR_URL, _QR_PATH, _VALIDATION_JOB, _VALIDATION_THREAD
    with _LOCK:
        if _SERVER is not None:
            return status()
        _VALIDATION_JOB = None
        _VALIDATION_THREAD = None
        _ACCESS_MODE = _configured_access_mode()
        _TOKEN = secrets.token_urlsafe(18)
        _PAIR_FAILURES = {}
        ip = receiver_ip()
        _LAST_URL = "http://%s:%d/" % (ip, PORT)
        if _ACCESS_MODE == "protected":
            _PAIR_CODE = "%06d" % secrets.randbelow(1000000)
            _QR_URL = "%s?pair=%s" % (_LAST_URL, urllib.parse.quote(_PAIR_CODE))
            _QR_PATH = "/tmp/ultrastalker_webcleaner_qr.png"
            try:
                _write_qr_png(_QR_URL, _QR_PATH)
            except Exception:
                _QR_PATH = None
        else:
            _PAIR_CODE = None
            _QR_URL = None
            _QR_PATH = None
        try:
            _SERVER = _Server(("0.0.0.0", PORT), Handler)
        except Exception:
            for path in (_QR_PATH,):
                try:
                    if path and os.path.exists(path):
                        os.unlink(path)
                except Exception:
                    pass
            _TOKEN = None
            _PAIR_CODE = None
            _PAIR_FAILURES = {}
            _LAST_URL = None
            _QR_URL = None
            _QR_PATH = None
            _SERVER = None
            raise
        _THREAD = threading.Thread(target=_SERVER.serve_forever, name="UltraStalkerWebCleaner", daemon=True)
        _THREAD.start()
        return status()


def stop():
    global _SERVER, _THREAD, _TOKEN, _PAIR_CODE, _PAIR_FAILURES, _LAST_URL, _QR_URL, _QR_PATH, _VALIDATION_JOB, _VALIDATION_THREAD
    with _LOCK:
        srv = _SERVER
        thr = _THREAD
        vthr = _VALIDATION_THREAD
        qr_path = _QR_PATH
        if isinstance(_VALIDATION_JOB, dict):
            ev = _VALIDATION_JOB.get("_cancel_event")
            if ev is not None:
                try:
                    ev.set()
                except Exception:
                    pass
        _VALIDATION_JOB = None
        _VALIDATION_THREAD = None
        _SERVER = None
        _THREAD = None
        _TOKEN = None
        _PAIR_CODE = None
        _PAIR_FAILURES = {}
        _LAST_URL = None
        _QR_URL = None
        _QR_PATH = None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    if thr is not None and thr.is_alive():
        try:
            thr.join(timeout=1.0)
        except Exception:
            pass
    if vthr is not None and vthr.is_alive():
        try:
            # In-flight socket operations may need their configured timeout, but
            # cancellation prevents any additional accounts from being started.
            vthr.join(timeout=1.0)
        except Exception:
            pass
    try:
        if qr_path and os.path.exists(qr_path):
            os.unlink(qr_path)
    except Exception:
        pass
    return status()


def status():
    with _LOCK:
        return {
            "running": _SERVER is not None,
            "port": PORT,
            "url": _LAST_URL or "",
            "ip": receiver_ip(),
            "access_mode": _ACCESS_MODE,
            "pairing": _ACCESS_MODE == "protected",
            "pair_code": _PAIR_CODE or "",
            "qr_path": _QR_PATH or "",
            "qr_ready": bool(_QR_PATH and os.path.isfile(_QR_PATH)),
        }
