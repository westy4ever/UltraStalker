# -*- coding: utf-8 -*-
"""Ultra Stalker title-logo transport and presentation core.

This module owns the receiver-side title-logo path.  It intentionally downloads
only display-sized artwork, streams bytes directly to a temporary file, validates
image dimensions before RGBA decode, and writes a versioned Ultra-only cache.
There are no Enigma2 UI imports here, so screens can call it only from their
existing background workers and paint the finished file on the UI thread.
"""
from __future__ import absolute_import

import os
import re
import hashlib
import tempfile
import urllib.parse
import urllib.request

try:
    from PIL import Image as _PILImage
except Exception:
    _PILImage = None

from .netsec import build_safe_media_opener, validate_remote_media_url
from .persistent_cache import TITLE_LOGOS

ULTRA_TITLE_LOGO_CACHE_VERSION = "flat_v1"
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_PIXELS = 2000000
DEFAULT_MAX_DIMENSION = 2048


def _media_tag(media_type):
    return "tv" if str(media_type or "").lower() in ("series", "tv", "episode") else "movie"


def title_logo_language(title):
    return "ar" if re.search(r"[\u0600-\u06ff]", str(title or "")) else "en"


def _title_logo_cache_language_tag(lang):
    """Return a cache-safe language lane without collapsing foreign fallbacks.

    Historical builds mapped every non-Arabic language to ``en``. That becomes
    unsafe once R230 is allowed to display any-language logos as a last resort.
    Keep the proven ``ar``/``en`` paths unchanged, and reserve ``any`` for an
    explicitly non-preferred fallback discovered by the unified resolver.
    """
    raw=str(lang or "en").lower().split("-")[0].strip()
    if raw.startswith("ar"):return "ar"
    if raw.startswith("en"):return "en"
    if raw in ("any","fallback","other","*"):return "any"
    if raw in ("00","null","neutral"):return "00"
    raw=re.sub(r"[^a-z0-9]","",raw)[:8]
    return raw or "en"


# R230: title-logo language is an identity policy, never a display-title policy.
# Arabic originals: Arabic -> English. Every non-Arabic original: English first.
# The unified resolver may then use a separate ``any`` fallback lane only after
# preferred + neutral logos fail. This keeps English/Arabic caches truthful while
# maximizing logo coverage without letting a foreign logo poison either lane.
_TITLE_LOGO_ARABIC_LANGS = {"ar"}
_TITLE_LOGO_REGIONAL_LANGS = {
    # Indian subcontinent
    "hi", "ta", "te", "ml", "kn", "bn", "pa", "ur", "gu", "mr", "or", "as", "ne", "si",
    # East / Southeast Asia
    "ko", "ja", "zh", "th", "id", "ms", "vi", "tl", "fil", "km", "my", "lo", "mn",
    # Central / West Asia families covered by the requested Asian exception
    "fa", "ps", "kk", "ky", "uz", "tg", "tk",
    # Turkish explicit exception
    "tr",
}
_TITLE_LOGO_ARABIC_COUNTRIES = {"EG","SA","AE","LB","SY","IQ","JO","KW","QA","BH","OM","YE","MA","DZ","TN","LY","SD","PS"}
_TITLE_LOGO_REGIONAL_COUNTRIES = {
    "IN","PK","BD","LK","NP","KR","KP","JP","CN","HK","TW","TH","ID","MY","PH","VN","SG","KH","MM","MN","TR",
    "IR","AF","KZ","KG","UZ","TJ","TM","LA","BN","BT",
}


def title_logo_policy_metadata_known(item=None, row=None):
    """Whether canonical origin metadata is present enough to choose a logo lane."""
    item = item if isinstance(item, dict) else {}
    row = row if isinstance(row, dict) else {}
    if str(row.get("original_language") or item.get("original_language") or "").strip():
        return True
    for source in (row, item):
        if source.get("countries") or source.get("origin_country") or source.get("country_hint"):
            return True
    return False


def title_logo_policy_languages(item=None, row=None):
    """Return preferred explicit logo languages for this work.

    R230 policy:
      * Arabic original/country -> Arabic, then English.
      * English or ANY other original/country -> English first.

    A separate any-language fallback is intentionally NOT returned here; the
    shared resolver runs it only after preferred explicit lanes and neutral art
    have failed, storing it in an isolated ``any`` cache lane.
    """
    item = item if isinstance(item, dict) else {}
    row = row if isinstance(row, dict) else {}
    original_language = str(row.get("original_language") or item.get("original_language") or "").lower().split("-")[0].strip()

    if original_language:
        if original_language in _TITLE_LOGO_ARABIC_LANGS:
            return ("ar", "en")
        return ("en",)

    countries = []
    for source in (row, item):
        values = source.get("countries") or source.get("origin_country") or []
        if isinstance(values, (str, bytes)):
            values = [values]
        for value in values:
            if isinstance(value, dict):
                value = value.get("iso_3166_1") or value.get("name")
            value = str(value or "").upper().strip()
            if value:
                countries.append(value)
        hint = str(source.get("country_hint") or "").upper().strip()
        if hint:
            countries.append(hint)
    country_set = set(countries)
    if country_set.intersection(_TITLE_LOGO_ARABIC_COUNTRIES):
        return ("ar", "en")
    return ("en",)


def normalize_title_logo_url(value):
    """Return a public logo URL, forcing TMDb CDN requests to w300.

    The rewrite happens before any socket is opened, so a stored ``original`` or
    larger TMDb URL can never enter the receiver's title-logo download path.
    Non-TMDb providers are left unchanged and are still bounded by byte/pixel
    guards below.
    """
    text = str(value or "").strip().replace("\\/", "/")
    if not text or text.lower() == "n/a" or not text.startswith(("http://", "https://")):
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
        host = str(parts.hostname or "").lower().rstrip(".")
        if host == "image.tmdb.org" or host.endswith(".image.tmdb.org"):
            path = re.sub(r"(/t/p/)(?:original|w\d+|h\d+)(/)", r"\1w300\2", parts.path, count=1, flags=re.I)
            text = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    except Exception:
        return ""
    return text


def ultra_title_logo_cache_path(media_type, tmdb_id, lang="en", canvas_size=(432, 210)):
    try:
        width = max(1, int(canvas_size[0])); height = max(1, int(canvas_size[1]))
        ident = int(tmdb_id)
        language = _title_logo_cache_language_tag(lang)
        folder = TITLE_LOGOS
        return os.path.join(folder, "%s_%s_%s_%sx%s.png" % (_media_tag(media_type), ident, language, width, height))
    except Exception:
        return ""


def ultra_title_logo_fallback_path(media_type, source_key, lang="en", canvas_size=(432, 210)):
    try:
        width = max(1, int(canvas_size[0])); height = max(1, int(canvas_size[1]))
        key = str(source_key or "").strip()
        if not key:
            return ""
        language = _title_logo_cache_language_tag(lang)
        digest = hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()[:24]
        folder = os.path.join(TITLE_LOGOS, "fallback")
        return os.path.join(folder, "%s_%s_%s_%sx%s.png" % (_media_tag(media_type), digest, language, width, height))
    except Exception:
        return ""


def valid_ultra_title_logo(path, min_bytes=256):
    try:
        return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path)) > int(min_bytes))
    except Exception:
        return False


def title_logo_provider(value):
    """Classify a normalized logo URL without trusting caller metadata."""
    text=normalize_title_logo_url(value)
    if not text:return ""
    try:
        host=str(urllib.parse.urlsplit(text).hostname or "").lower().rstrip(".")
    except Exception:
        return ""
    if host=="image.tmdb.org" or host.endswith(".image.tmdb.org"):
        return "tmdb"
    if host=="assets.fanart.tv" or host.endswith(".assets.fanart.tv"):
        return "fanart"
    return "other"


def fetch_prepare_title_logo_candidates(urls, target_path, canvas_size=(432,210), timeout=6,
                                        max_bytes=DEFAULT_MAX_BYTES, max_pixels=DEFAULT_MAX_PIXELS):
    """Try display-sized logo candidates in order and stop at the first safe hit.

    A broken/404/corrupt TMDb logo therefore cannot terminate the title-logo
    pipeline.  Callers can pass TMDb candidates first and Fanart candidates
    second while this function keeps every individual download bounded.
    """
    if valid_ultra_title_logo(target_path):return target_path
    seen=set()
    for raw in (urls or []):
        url=normalize_title_logo_url(raw)
        if not url or url in seen:continue
        seen.add(url)
        try:
            path=fetch_prepare_title_logo(url,target_path,canvas_size=canvas_size,timeout=timeout,
                                          max_bytes=max_bytes,max_pixels=max_pixels)
            if valid_ultra_title_logo(path):return path
        except Exception:
            # Provider fallback is policy, not an exceptional UI condition.
            continue
    return ""


def _stream_public_image_to_file(url, target, timeout=6, max_bytes=DEFAULT_MAX_BYTES):
    value = normalize_title_logo_url(url)
    if not value:
        return ""
    validate_remote_media_url(value)
    headers = {
        "User-Agent": "UltraStalker/TitleLogo",
        "Accept": "image/png,image/webp,image/jpeg,image/*;q=0.9,*/*;q=0.1",
        "Connection": "close",
    }
    request = urllib.request.Request(value, headers=headers)
    opener = build_safe_media_opener()
    total = 0
    with opener.open(request, timeout=max(2.0, min(float(timeout or 6), 10.0))) as response:
        code = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 200)
        if code < 200 or code >= 300:
            raise IOError("title-logo request returned HTTP %s" % code)
        content_length = str(response.headers.get("Content-Length") or "").strip()
        if content_length.isdigit() and int(content_length) > int(max_bytes):
            raise ValueError("title-logo response exceeds byte limit")
        with open(target, "wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > int(max_bytes):
                    raise ValueError("title-logo response exceeds byte limit")
                handle.write(chunk)
    if total <= 128:
        raise ValueError("title-logo response is empty")
    return target


def _looks_like_title_logo(im):
    """Reject poster/backdrop-shaped images before they enter the logo cache.

    Real clearlogos are normally transparent artwork.  Opaque artwork is still
    accepted when it is decisively wide, covering providers that flatten logos
    onto a solid background.  Portrait posters are intentionally rejected: an
    empty/text fallback is better than painting a poster in the title-logo slot.
    """
    try:
        sw,sh=im.size
        if sw<=0 or sh<=0:return False
        ratio=float(sw)/float(sh)
        rgba=im.convert("RGBA")
        alpha=rgba.getchannel("A")
        amin,amax=alpha.getextrema()
        has_transparency=bool(amin < 245)
        if has_transparency:
            return ratio >= 0.55
        return ratio >= 1.35
    except Exception:
        return False


def prepare_title_logo_file(source_path, target_path, canvas_size=(432, 210), max_pixels=DEFAULT_MAX_PIXELS,
                            max_dimension=DEFAULT_MAX_DIMENSION, margin_x=28, margin_y=24):
    """Prepare one exact-size transparent canvas after header-only safety checks."""
    if _PILImage is None:
        return ""
    source_path = str(source_path or ""); target_path = str(target_path or "")
    if not source_path or not target_path or not os.path.isfile(source_path):
        return ""
    width = max(1, int(canvas_size[0])); height = max(1, int(canvas_size[1]))
    parent = os.path.dirname(target_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)
    tmp_target = target_path + ".tmp.%s" % os.getpid()
    try:
        with _PILImage.open(source_path) as src:
            sw, sh = src.size
            if sw <= 0 or sh <= 0 or sw > int(max_dimension) or sh > int(max_dimension) or (sw * sh) > int(max_pixels):
                raise ValueError("title-logo dimensions exceed safe decode limit")
            # Decode only after the header dimensions have passed the guard.
            if not _looks_like_title_logo(src):
                raise ValueError("title-logo source looks like poster/backdrop artwork")
            im = src.convert("RGBA")
        try:
            bbox = im.getchannel("A").getbbox()
            if bbox:
                im = im.crop(bbox)
        except Exception:
            pass
        if im.width < 1 or im.height < 1:
            raise ValueError("title-logo has no visible pixels")
        max_w = max(1, width - int(margin_x)); max_h = max(1, height - int(margin_y))
        scale = min(float(max_w) / float(im.width), float(max_h) / float(im.height))
        nw = max(1, int(round(im.width * scale))); nh = max(1, int(round(im.height * scale)))
        if (nw, nh) != im.size:
            resampling = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", 1)
            im = im.resize((nw, nh), resampling)
        canvas = _PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(im, ((width - nw) // 2, max(0, (height - nh) // 2)))
        canvas.save(tmp_target, "PNG", compress_level=3, optimize=False)
        try:
            os.chmod(tmp_target, 0o600)
        except OSError:
            pass
        os.replace(tmp_target, target_path)
        return target_path if valid_ultra_title_logo(target_path) else ""
    finally:
        try:
            if os.path.exists(tmp_target):
                os.unlink(tmp_target)
        except OSError:
            pass


def fetch_prepare_title_logo(url, target_path, canvas_size=(432, 210), timeout=6,
                             max_bytes=DEFAULT_MAX_BYTES, max_pixels=DEFAULT_MAX_PIXELS):
    """Stream, validate, decode and persist one title logo with bounded memory."""
    if valid_ultra_title_logo(target_path):
        return target_path
    normalized = normalize_title_logo_url(url)
    if not normalized:
        return ""
    raw_path = ""
    try:
        fd, raw_path = tempfile.mkstemp(prefix="ultra_title_logo_src_", suffix=".img")
        os.close(fd)
        _stream_public_image_to_file(normalized, raw_path, timeout=timeout, max_bytes=max_bytes)
        return prepare_title_logo_file(raw_path, target_path, canvas_size=canvas_size, max_pixels=max_pixels)
    finally:
        try:
            if raw_path and os.path.exists(raw_path):
                os.unlink(raw_path)
        except OSError:
            pass
