# -*- coding: utf-8 -*-
"""TMDB metadata enrichment for Ultra Stalker.

The Stalker portal remains the playback/content authority. TMDB is optional and
is used only to enrich metadata/artwork when a user supplies their own API
credential.
"""
import os
import re
import json
import time
import hashlib
import difflib
import logging
import threading
import weakref
import unicodedata
import urllib.parse
import urllib.request
from .securefs import secure_private_dir
from .log import diagnostic_failure
import urllib.error

from .netsec import credential_urlopen, SafeMediaRedirectHandler, validate_remote_media_url, build_safe_media_opener, build_safe_https_media_opener
from .tmdb_stages import query_list as _enrich_query_list, external_imdb_id as _external_imdb_id, direct_tmdb_identity as _direct_tmdb_identity
from .age_rating import certification_from_tmdb, rating_endpoint
try:
    from PIL import Image as _PILImage, ImageFilter as _PILImageFilter, ImageStat as _PILImageStat
except Exception:
    _PILImage=None
    _PILImageFilter=None
    _PILImageStat=None

CONFIG_DIR = "/etc/enigma2/ultrastalker"
from .persistent_cache import INDEX as CACHE_DIR, POSTERS as POSTER_CACHE_DIR, BACKDROPS as BACKDROP_CACHE_DIR, GENERATED as IMAGE_CACHE_DIR, SOURCE_POSTERS as SOURCE_POSTER_CACHE_DIR, SOURCE_BACKDROPS as SOURCE_BACKDROP_CACHE_DIR, hdd_ready, hdd_read_ready, ensure_persistent_dirs, persistent_write_gate
from .title_clean import clean_title as _shared_clean_title
from .media_library import load as load_media_record, save as save_media_record, ensure_adaptive as ensure_media_adaptive, item_paths as media_item_paths
from .core.image_budget import image_budgeted
_IMAGE_DL_EXECUTOR = None  # Beta56: image bytes run synchronously inside the global hydration caller lane.
_IMAGE_LOCK_GUARD = threading.Lock()
_IMAGE_LOCKS = weakref.WeakValueDictionary()
API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
CACHE_TTL = None  # verified TMDB metadata is persistent; explicit cache clear/prune owns eviction
CACHE_SCHEMA = 20
LEGACY_METADATA_CACHE_SCHEMAS = (19,)  # warm metadata bridge only; never an artwork authority
UA = "UltraStalker-Nova/10"
LOG = logging.getLogger("UltraStalker.TMDB")

_MEM_CACHE = {}
_MEM_CACHE_ORDER = []
_MEM_CACHE_LOCK = threading.RLock()
_MEM_CACHE_LIMIT = 160

def _mem_get(key):
    with _MEM_CACHE_LOCK:
        value=_MEM_CACHE.get(key)
        if value is not None:
            try:_MEM_CACHE_ORDER.remove(key)
            except ValueError:pass
            _MEM_CACHE_ORDER.append(key)
            return dict(value) if isinstance(value,dict) else value
    return None

def _mem_put(key,value):
    if value is None:return
    with _MEM_CACHE_LOCK:
        _MEM_CACHE[key]=dict(value) if isinstance(value,dict) else value
        try:_MEM_CACHE_ORDER.remove(key)
        except ValueError:pass
        _MEM_CACHE_ORDER.append(key)
        while len(_MEM_CACHE_ORDER)>_MEM_CACHE_LIMIT:
            old=_MEM_CACHE_ORDER.pop(0);_MEM_CACHE.pop(old,None)


def shutdown_tmdb_workers(wait=False):
    return True


def _tmdb_certification(details, media_type, supplemental=None):
    return certification_from_tmdb(details,media_type,supplemental=supplemental)


def _path_lock(path):
    key = os.path.abspath(str(path or ""))
    with _IMAGE_LOCK_GUARD:
        lock = _IMAGE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _IMAGE_LOCKS[key] = lock
        return lock



@image_budgeted
def _is_landscape_image(path, ratio=1.25):
    if not path or not os.path.isfile(str(path)):return False
    if _PILImage is None:return False
    try:
        with _PILImage.open(path) as im:
            w,h=im.size
        return bool(w>=640 and h>=320 and float(w)/float(max(1,h))>=float(ratio))
    except Exception:return False

def _valid_image_file(path, min_bytes=1024):
    """Cheap structural validation for cached artwork without a Pillow dependency."""
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        if size <= int(min_bytes):
            return False
        with open(path, "rb") as handle:
            head = handle.read(32)
            handle.seek(max(0, size - 64))
            tail = handle.read(64)
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            # PNG must begin with IHDR and terminate with an IEND chunk.
            return head[12:16] == b"IHDR" and b"IEND" in tail
        if head.startswith(b"\xff\xd8\xff"):
            # JPEG decoders require the end-of-image marker; allow metadata bytes
            # after it but require the marker near the end of the cached file.
            return b"\xff\xd9" in tail
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            if len(head) < 12:
                return False
            declared = int.from_bytes(head[4:8], "little") + 8
            return 12 <= declared <= size
        return False
    except Exception:
        return False


_TECH = {
    "4k","8k","uhd","fhd","hd","2160p","1080p","720p","3840p","4320p",
    "hdr","hdr10","dolby","atmos","hevc","h265","h264","avc","top","vip",
    "subs","sub","dub","dubs","raw","bluray","bdrip","brrip","hdrip","webrip","webdl","x264","x265","multi","audio","vod","movie","movies","series","tv",
    "ar","en","tr","s","do","fr","de","es","it","pl","nl","ru","pt","br","us","uk","eg","ksa","uae","arabic","english","turkish"," مترجم","مدبلج"
}


def _ensure():
    # Persistent artwork is governed by core.maintenance.prune_cache and the
    # user's multi-GB persistent_cache_mb setting. Never create /media/hdd on
    # receiver flash while the real disk is absent or late during boot.
    if not hdd_ready():
        return False
    return ensure_persistent_dirs(CACHE_DIR, IMAGE_CACHE_DIR, POSTER_CACHE_DIR, BACKDROP_CACHE_DIR)


def _year(value):
    m = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(m.group(0)) if m else None


def _country_hint(item, raw=""):
    item=item if isinstance(item,dict) else {}
    aliases={"KOREA":"KR","SOUTH KOREA":"KR","KOREAN":"KR","KR":"KR","KOR":"KR","TURKEY":"TR","TURKIYE":"TR","TURKISH":"TR","TR":"TR","TUR":"TR","EGYPT":"EG","EG":"EG","EGY":"EG","USA":"US","UNITED STATES":"US","US":"US","JAPAN":"JP","JP":"JP","JPN":"JP","CHINA":"CN","CN":"CN","CHN":"CN","INDIA":"IN","IN":"IN","IND":"IN","MALAYSIA":"MY","MALAYSIAN":"MY","MY":"MY","MYS":"MY","INDONESIA":"ID","INDONESIAN":"ID","ID":"ID","IDN":"ID","THAILAND":"TH","THAI":"TH","TH":"TH","THA":"TH","SINGAPORE":"SG","SG":"SG","SGP":"SG","PHILIPPINES":"PH","PH":"PH","PHL":"PH","SA":"SA","KSA":"SA","AE":"AE","UAE":"AE"}
    for k in ("country_code","country","origin_country","production_country","region"):
        v=str(item.get(k) or "").strip().upper()
        if v in aliases:return aliases[v]
        if len(v)==2 and v.isalpha():return v
    raw_text=str(raw or "").strip()
    # Prefer an explicit trailing country token such as ``(KR)``. This is common
    # in Korean/Turkish Stalker catalogues and is much more trustworthy than a
    # generic category prefix.
    for token in reversed(re.findall(r"\(([A-Za-z]{2,3})\)", raw_text)):
        key=str(token or "").upper()
        if key in aliases:return aliases[key]
    head=raw_text.split(" - ",1)[0].upper()
    for token in re.split(r"[-_ ]+",head):
        if token in aliases:return aliases[token]
    return ""


def _language_for_country(code):
    return {"KR":"ko","TR":"tr","EG":"ar","SA":"ar","AE":"ar","JP":"ja","CN":"zh","IN":"hi","MY":"ms","ID":"id","TH":"th","SG":"en","PH":"tl","US":"en","GB":"en"}.get(str(code or "").upper(),"")


def _has_arabic(value):
    return bool(re.search(r"[\u0600-\u06ff]",str(value or "")))


def _latin_heavy(value):
    text=str(value or "").strip()
    if not text:return False
    latin=len(re.findall(r"[A-Za-z]",text))
    letters=len(re.findall(r"[^\W\d_]",text,re.UNICODE))
    return latin >= max(2,int(letters*0.55))


def _normalize_unicode_digits(value):
    """Normalize Arabic-Indic/Persian and other Unicode decimal digits to ASCII.

    TMDB Arabic translations frequently use ٠١٢٣٤٥٦٧٨٩ while Stalker
    catalogues use 0123456789.  Treating those as different broke otherwise
    exact identities such as ``السفارة 87`` vs ``السفارة ٨٧``.
    """
    text=str(value or "")
    out=[]
    for ch in text:
        try:
            if unicodedata.category(ch) == "Nd":
                out.append(str(unicodedata.digit(ch)))
            else:
                out.append(ch)
        except Exception:
            out.append(ch)
    return "".join(out)


def clean_title(value):
    return _shared_clean_title(value)


def _ascii_search_title(value):
    """Aggressive Stalker/TMDB search title.

    MAG/Stalker catalogues often combine a routing prefix, a Latin title and a
    translated Arabic title in one display name. TMDB matching is much more
    reliable when the routing prefix and translated suffix are not sent as if
    they were part of the canonical title.
    """
    text=str(value or "").strip()
    if not text:return ""
    # Search-only cleanup: parenthetical country/year/routing notes are never
    # useful to TMDB's text query because year is supplied separately.
    text=re.sub(r"\[[^\]]*\]", " ", text)
    text=re.sub(r"\([^)]*\)", " ", text)
    # Drop a compact routing prefix such as AR-TR-S, 4K-AR-DO, EN-SUBS, etc.
    # Require a visible separator after it so legitimate title words survive.
    text=re.sub(r"^(?:[A-Za-z0-9]{1,6}(?:[-_][A-Za-z0-9]{1,8}){1,6})\s*[-|:]\s*", " ", text)
    text=clean_title(text)
    # NFKD keeps Turkish/European letters searchable while dropping the Arabic
    # translation suffix from mixed-script names: Güller -> Guller.
    ascii_text=unicodedata.normalize("NFKD",text).encode("ascii","ignore").decode("ascii","ignore")
    ascii_text=re.sub(r"\b(?:4K|8K|UHD|FHD|HD|RAW|SUBS?|DUBS?|DUBBED|TOP|VIP|AR|EN|TR|DO)\b", " ", ascii_text, flags=re.I)
    ascii_text=re.sub(r"\s+", " ", ascii_text).strip(" -_|:.")
    return ascii_text


def _query_variants(value):
    raw=str(value or "").strip()
    if not raw:return []
    # R61 search-only repair for common malformed catalogue labels.  Examples:
    # `Fargo ( 1996` / `The Father ( 2020` and zero-padded chart prefixes such
    # as `006-Pulp Fiction`.  The visible title and persistent identity key keep
    # the provider's original text; only the remote TMDB query is repaired.
    search_raw=re.sub(r"\s*[\[(]\s*(?:19|20)\d{2}\s*[\])]?[\s]*$", " ", raw).strip()
    search_raw=re.sub(r"^\s*0\d{2,3}\s*[-.:]\s*(?=\S)", "", search_raw).strip() or raw
    clean=clean_title(search_raw)
    ascii_title=_ascii_search_title(search_raw)
    variants=[]
    # Mixed-script Stalker names should try the canonical-looking Latin title
    # first, matching the behaviour of mature Stalker clients.
    has_ascii=bool(re.search(r"[A-Za-z]",clean)); has_ar=bool(re.search(r"[\u0600-\u06ff]",clean))
    order=(ascii_title,clean) if has_ascii and has_ar else (clean,ascii_title)
    expanded=[]
    for q in order:
        q=re.sub(r"\s+"," ",str(q or "")).strip(" -_|:.")
        if not q:continue
        expanded.append(q)
        # Some Stalker catalogues append a catalogue slot/part number after an
        # editorial marker: "السفارة 87 (قريبا) - 3".  Keep the normal query,
        # but also try a search-only form without that final counter.
        q2=re.sub(r"\s+[-|:]\s*\d{1,2}\s*$","",q).strip(" -_|:.")
        if q2 and q2!=q:expanded.append(q2)
    for q in expanded:
        nq=re.sub(r"[^a-z0-9\u0600-\u06ff]+"," ",q.casefold()).strip()
        if nq and all(re.sub(r"[^a-z0-9\u0600-\u06ff]+"," ",x.casefold()).strip()!=nq for x in variants):
            variants.append(q)
    return variants


def _norm(value):
    text = _normalize_unicode_digits(clean_title(value)).casefold()
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _description_similarity(a, b):
    a=_norm(a); b=_norm(b)
    if len(a)<24 or len(b)<24:return 0.0
    sa=set(x for x in a.split() if len(x)>2); sb=set(x for x in b.split() if len(x)>2)
    if not sa or not sb:return 0.0
    return float(len(sa & sb))/float(max(1,len(sa | sb)))

def _score(query, result, media_type, wanted_year=None, requested_type=None, country_hint="", description_hint=""):

    title = result.get("title") if media_type == "movie" else result.get("name")
    original = result.get("original_title") if media_type == "movie" else result.get("original_name")
    q = _norm(query)
    t = _norm(title or "")
    o = _norm(original or "")
    base = max(difflib.SequenceMatcher(None, q, t).ratio(), difflib.SequenceMatcher(None, q, o).ratio())
    if q and (q == t or q == o):
        base = 1.0
    else:
        # TMDB often appends a type/editorial suffix to new Arabic titles while
        # Stalker exposes only the core programme name, e.g.
        # ``قلب مفتوح`` -> ``قلب مفتوح مسلسل قصير`` and
        # ``القصة الكاملة`` -> ``القصة الكاملة دراما``.  The core title is
        # still strong identity evidence; do not punish it as a fuzzy match.
        q_tokens=q.split()
        for candidate in (t,o):
            if not q or not candidate:
                continue
            if candidate.startswith(q + " ") or q.startswith(candidate + " "):
                base=max(base,0.96)
                continue
            c_tokens=candidate.split()
            if len(q_tokens) >= 2 and len(c_tokens) >= len(q_tokens):
                qset=set(q_tokens); cset=set(c_tokens)
                if qset and qset.issubset(cset) and len(cset-qset) <= 3:
                    base=max(base,0.91)
    date = result.get("release_date") if media_type == "movie" else result.get("first_air_date")
    ry = _year(date)
    if wanted_year and ry:
        diff = abs(int(wanted_year)-int(ry))
        if diff == 0: base += 0.16
        elif diff == 1: base += 0.05
        elif diff >= 3: base -= 0.18
    if requested_type and media_type != requested_type:
        base -= 0.24
    if country_hint:
        wanted_lang=_language_for_country(country_hint)
        original_lang=str(result.get("original_language") or "").lower()
        origin=[str(x).upper() for x in (result.get("origin_country") or [])]
        if wanted_lang and original_lang==wanted_lang: base += 0.12
        if country_hint.upper() in origin: base += 0.12
        if wanted_lang and original_lang and original_lang!=wanted_lang: base -= 0.08
    ds=_description_similarity(description_hint,result.get("overview") or "")
    if ds>=0.28: base += 0.16
    elif ds>=0.16: base += 0.08
    elif description_hint and len(str(description_hint))>80 and result.get("overview") and ds<0.04: base -= 0.10
    try:
        pop = float(result.get("popularity") or 0)
        base += min(0.02, pop / 15000.0)
    except Exception as exc:
        diagnostic_failure("tmdb.failsoft", exc)
    return max(0.0, min(1.0, base))


from .tmdb import TMDBError


class TMDBClient:
    def __init__(self, credential, language="ar-EG", timeout=8):
        self.credential = str(credential or "").strip()
        self.language = str(language or "ar-EG").strip() or "ar-EG"
        self.timeout = max(3, min(20, int(timeout or 8)))
        _ensure()

    @property
    def configured(self):
        return bool(self.credential)

    def _auth(self, params):
        headers = {"User-Agent": UA, "Accept": "application/json"}
        # v3 keys are normally 32 hex chars. Read access tokens are JWT-like/long.
        if len(self.credential) <= 64 and re.match(r"^[A-Za-z0-9_-]+$", self.credential):
            params["api_key"] = self.credential
        else:
            headers["Authorization"] = "Bearer " + self.credential
        return headers

    def _get(self, path, params=None):
        if not self.configured:
            raise TMDBError("TMDB credential is not configured")
        params = dict(params or {})
        headers = self._auth(params)
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        try:
            with credential_urlopen(req, timeout=self.timeout) as response:
                data = response.read(2*1024*1024 + 1)
            if len(data) > 2*1024*1024:
                raise TMDBError("TMDB response too large")
            payload = json.loads(data.decode("utf-8", "replace"))
            if isinstance(payload, dict) and payload.get("success") is False:
                raise TMDBError(str(payload.get("status_message") or "TMDB request failed"))
            return payload
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(8192).decode("utf-8", "replace")
                msg = json.loads(body).get("status_message")
            except Exception:
                msg = None
            raise TMDBError(msg or "TMDB HTTP %s" % exc.code)
        except urllib.error.URLError as exc:
            raise TMDBError("TMDB network error: %s" % getattr(exc, "reason", exc))
        except ValueError as exc:
            raise TMDBError("Invalid TMDB response: %s" % exc)


    def test(self):
        data = self._get("/configuration")
        return bool(isinstance(data, dict) and data.get("images"))

    def _cache_key(self, media_type, query, year, country="", identity="", schema=None):
        # A verified portal TMDB/IMDb id must never share a cache row with a
        # fuzzy name search. This was the root of stale same-name matches (Sugar).
        # R261: schema may be selected only for the read-only metadata bridge.
        # Artwork/full resolution always stays on CACHE_SCHEMA so pre-R260
        # gallery-selected poster/backdrop URLs can never regain authority.
        if schema is None:
            schema=CACHE_SCHEMA
        raw = "%s|%s|%s|%s|%s|%s|v%s" % (media_type, _norm(query), year or "", str(country or "").upper(), str(identity or ""), self.language, int(schema))
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def _cache_read(self, key):
        hot=_mem_get(key)
        if hot is not None:return hot
        if not hdd_read_ready(force=True):
            return None
        path = os.path.join(CACHE_DIR, key + ".json")
        try:
            if CACHE_TTL not in (None, 0, False) and time.time() - os.path.getmtime(path) > CACHE_TTL:
                return None
            with open(path, "r", encoding="utf-8") as h:
                data = json.load(h)
            if isinstance(data,dict):_mem_put(key,data);return data
            return None
        except Exception:
            return None

    def _cache_write(self, key, data):
        path = os.path.join(CACHE_DIR, key + ".json")
        lock = _path_lock(path)
        temp = path + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        with lock:
            # Revalidate at the actual write boundary.  The HDD may disappear
            # while a TMDB network request is in flight.
            if not hdd_ready() or not ensure_persistent_dirs(CACHE_DIR):
                return False
            try:
                with open(temp, "w", encoding="utf-8") as h:
                    json.dump(data, h, ensure_ascii=False, separators=(",", ":"))
                    h.flush(); os.fsync(h.fileno())
                os.chmod(temp, 0o600)
                os.replace(temp, path);_mem_put(key,data)
                return True
            except Exception:
                try:
                    if os.path.exists(temp) and persistent_write_gate(temp): os.unlink(temp)
                except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
                return False

    @staticmethod
    def image_url(path, size):
        path = str(path or "").strip()
        if not path: return None
        if not path.startswith("/"): path = "/" + path
        return IMAGE_BASE + "/" + size + path

    def _cached_image_path(self, url, tag):
        """Return an already-downloaded TMDB image without scheduling I/O/network.

        This is the cold-boot fast path: metadata survives power loss and the
        deterministic URL+tag filename lets us reopen HDD artwork immediately.
        """
        if not url or not hdd_read_ready(): return None
        digest = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
        ext = ".png" if url.lower().split("?")[0].endswith(".png") else ".jpg"
        lowtag=str(tag or "").lower()
        if "backdrop" in lowtag: cache_dir=SOURCE_BACKDROP_CACHE_DIR
        elif "poster" in lowtag: cache_dir=SOURCE_POSTER_CACHE_DIR
        else: cache_dir=IMAGE_CACHE_DIR
        path=os.path.join(cache_dir, "%s_%s%s" % (tag,digest,ext))
        return path if _valid_image_file(path) else None

    def _download_image(self, url, tag):
        if not url or not hdd_ready(): return None
        digest = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
        ext = ".png" if url.lower().split("?")[0].endswith(".png") else ".jpg"
        lowtag=str(tag or "").lower()
        if "backdrop" in lowtag:
            cache_dir=SOURCE_BACKDROP_CACHE_DIR
        elif "poster" in lowtag:
            cache_dir=SOURCE_POSTER_CACHE_DIR
        else:
            cache_dir=IMAGE_CACHE_DIR
        path = os.path.join(cache_dir, "%s_%s%s" % (tag, digest, ext))
        lock = _path_lock(path)
        with lock:
            temp = path + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
            try:
                if _valid_image_file(path):
                    return path
                # A truncated/corrupt prior cache file must not become a permanent hit.
                try:
                    if os.path.exists(path) and persistent_write_gate(path): os.unlink(path)
                except OSError:
                    pass
                validate_remote_media_url(url)
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept":"image/*"})
                opener = build_safe_media_opener()
                with opener.open(req, timeout=min(max(float(self.timeout), 2.5), 6.0)) as response:
                    media_cap = 8*1024*1024
                    data = response.read(media_cap + 1)
                if not (1024 < len(data) <= media_cap): return None
                head = data[:16]
                if not (head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
                    return None
                if not persistent_write_gate(temp): return None
                with open(temp, "wb") as h:
                    h.write(data); h.flush(); os.fsync(h.fileno())
                if not persistent_write_gate(path): return None
                os.replace(temp, path)
                return path if _valid_image_file(path) else None
            except Exception:
                return None
            finally:
                try:
                    if os.path.exists(temp) and persistent_write_gate(temp): os.unlink(temp)
                except OSError:
                    pass

    def enrich(self, portal_media_type, item, artwork_mode="full"):
        requested_type = "tv" if str(portal_media_type).lower() in ("series","tv") else "movie"
        alternate_type = "movie" if requested_type == "tv" else "tv"
        item = item if isinstance(item, dict) else {}
        raw = str(item.get("name") or item.get("title") or "").strip()
        description_hint=str(item.get("description") or item.get("descr") or item.get("overview") or item.get("plot") or "").strip()
        wanted_year = None
        for k in ("year","release_year","released","release_date","date"):
            wanted_year = _year(item.get(k))
            if wanted_year: break
        if not wanted_year:wanted_year=_year(raw)
        country_hint=_country_hint(item,raw)

        query_fields=("original_name","original_title","o_name","movie_name","series_name","title","name","display_name")
        # Stage 1: normalize catalogue identity before network/cache work.
        queries=_enrich_query_list(item,raw,query_fields,_query_variants,_norm,limit=4)
        if not queries:return None
        query=queries[0]

        # Read portal identity BEFORE the name-cache lookup. A direct TMDB id
        # from the Stalker row is the strongest catalogue signal and must not be
        # shadowed by an older fuzzy cache entry for the same display name.
        locked_tmdb_id=None
        try:
            locked_tmdb_id=int(item.get("_locked_tmdb_id") or 0) or None
        except Exception:locked_tmdb_id=None
        locked_tmdb_type=str(item.get("_locked_tmdb_type") or "").strip().lower()
        explicit_tmdb_type=str(item.get("tmdb_type") or item.get("tmdb_media_type") or "").strip().lower()
        if explicit_tmdb_type in ("series","show"): explicit_tmdb_type="tv"
        if explicit_tmdb_type in ("vod","film"): explicit_tmdb_type="movie"
        direct_tmdb_id,portal_tmdb_id=_direct_tmdb_identity(item,locked_tmdb_id)
        provided_iid=_external_imdb_id(item)
        identity_discriminator=("tmdb:%s:%s"%(explicit_tmdb_type or locked_tmdb_type or requested_type,direct_tmdb_id)) if direct_tmdb_id else (("imdb:"+provided_iid) if provided_iid else "search")
        key=self._cache_key(requested_type,query,wanted_year,country_hint,identity_discriminator)
        cached=self._cache_read(key)

        # R261 navigation-speed bridge. R260 bumped the TMDb cache schema to
        # enforce official primary artwork, which unintentionally made Home /
        # category metadata hydration cold and forced thousands of needless
        # title lookups. Metadata-only callers do not consume poster/backdrop
        # authority, so they may reuse the proven v19 identity/rating/year row.
        # Full/poster artwork calls NEVER use this bridge: they stay on v20 and
        # therefore resolve/cache the official Details poster_path/backdrop_path.
        if cached is None and str(artwork_mode or "full").lower()=="metadata":
            for _legacy_schema in LEGACY_METADATA_CACHE_SCHEMAS:
                try:
                    _legacy_key=self._cache_key(requested_type,query,wanted_year,country_hint,identity_discriminator,schema=_legacy_schema)
                    _legacy=self._cache_read(_legacy_key)
                except Exception:
                    _legacy=None
                if not isinstance(_legacy,dict):
                    continue
                cached=dict(_legacy)
                cached["_metadata_cache_bridge_schema"]=int(_legacy_schema)
                # Explicit firewall: legacy artwork fields are not exposed even
                # to metadata consumers, preventing a later merge from reviving
                # a gallery-selected image by accident.
                for _art_key in (
                    "poster_url","poster_local","poster_path",
                    "backdrop_url","backdrop_urls","backdrop_local","backdrop_path",
                ):
                    cached.pop(_art_key,None)
                # R262: legacy navigation cache may carry a synopsis produced by
                # another information-language policy.  Keep its warm identity,
                # year/rating/runtime etc., but never let that old text become a
                # visible overview authority.
                for _text_key in ("overview","overview_language","description","descr","plot"):
                    cached.pop(_text_key,None)
                cached["_metadata_cache_bridge_textless"]=True
                break
        if cached:
            # Schema18 presentation firewall: a search-cache row must still agree
            # with the current provider title. A discovered IMDb/TMDB id cannot
            # self-certify a different same-year programme.
            cq=_norm(query);ct=[_norm(cached.get("title") or ""),_norm(cached.get("original_title") or "")]
            ct=[x for x in ct if x]
            structured_same=bool((provided_iid and str(cached.get("imdb_id") or "").lower()==str(provided_iid).lower()) or (portal_tmdb_id and str(cached.get("tmdb_id") or "")==str(portal_tmdb_id)))
            title_same=bool(cq and any(cq==x or (min(len(cq),len(x))>=8 and (cq in x or x in cq) and len(set(cq.split()) & set(x.split()))>=max(1,int(min(len(set(cq.split())),len(set(x.split())))*0.8))) for x in ct))
            if not structured_same and not title_same:
                cached=None
        if cached:
            # Keep valid persisted local artwork across GUI/full receiver restarts.
            # A prior build deliberately nulled these fields on every cache read,
            # which turned a warm HDD cache back into network work after reboot.
            if cached.get("poster_local") and not _valid_image_file(cached.get("poster_local")):
                cached["poster_local"] = None
            if cached.get("backdrop_local") and not _valid_image_file(cached.get("backdrop_local")):
                cached["backdrop_local"] = None
            urls=[]
            for value in (cached.get("backdrop_urls") or []) + ([cached.get("backdrop_url")] if cached.get("backdrop_url") else []):
                value=str(value or "").strip()
                for candidate in (value, value.replace("/w1280/","/w780/") if "/w1280/" in value else ""):
                    if candidate and candidate not in urls:urls.append(candidate)
            # Beta56: no private TMDB image pool. Reuse HDD first, then any
            # missing image bytes are fetched synchronously by the caller, which
            # is already running inside the global two-worker hydration lane.
            metadata_only = str(artwork_mode or "full").lower() == "metadata"
            if not metadata_only and cached.get("poster_url") and not cached.get("poster_local"):
                cached["poster_local"]=self._cached_image_path(cached.get("poster_url"),"poster")
                if not cached.get("poster_local"):
                    try:cached["poster_local"]=self._download_image(cached.get("poster_url"),"poster")
                    except Exception as exc:diagnostic_failure("tmdb.cached_poster",exc)
            poster_only = str(artwork_mode or "full").lower() == "poster"
            if not metadata_only and not poster_only and not cached.get("backdrop_local"):
                for idx,url in enumerate(urls[:3]):
                    local=self._cached_image_path(url,"backdrop_cached_%d"%idx)
                    if not local:
                        try:local=self._download_image(url,"backdrop_cached_%d"%idx)
                        except Exception as exc:diagnostic_failure("tmdb.cached_backdrop",exc);local=None
                    if local:
                        cached["backdrop_local"]=local;cached["backdrop_url"]=url;break
            return cached

        ranked=[]
        seen=set()
        def add_results(rows, media_type, q):
            if not isinstance(rows,list):return
            for row in rows[:20]:
                if not isinstance(row,dict) or not row.get("id"):continue
                sig=(media_type,str(row.get("id")))
                if sig in seen:continue
                seen.add(sig)
                # For country-tagged foreign catalogues, reject obviously wrong
                # language/country candidates before they can win on title alone.
                if country_hint and _language_for_country(country_hint):
                    wanted_lang=_language_for_country(country_hint); ol=str(row.get("original_language") or "").lower(); origins=[str(x).upper() for x in (row.get("origin_country") or [])]
                    if ol and wanted_lang and ol!=wanted_lang and country_hint not in origins:
                        continue
                score=_score(q,row,media_type,wanted_year,requested_type,country_hint,description_hint)
                ranked.append((score,row,media_type,q))

        authoritative_direct=False
        # 0) Direct portal identity wins. Ultra Stalker Checker proves that many
        # catalogues already publish the correct TMDB id even when the display
        # title is Arabic/Korean/etc. Use the portal namespace first and only
        # fall back to the alternate namespace when a locked cache explicitly
        # says so or the requested namespace does not exist.
        if direct_tmdb_id:
            direct_candidates=[]
            namespaces=[]
            preferred_direct_type = locked_tmdb_type if locked_tmdb_type in ("movie","tv") else (explicit_tmdb_type if explicit_tmdb_type in ("movie","tv") else requested_type)
            namespaces.append(preferred_direct_type)
            if locked_tmdb_id and alternate_type not in namespaces:
                namespaces.append(alternate_type)
            for media_type in namespaces:
                try:
                    row=self._get("/%s/%s"%(media_type,direct_tmdb_id),{"language":self.language})
                    if not isinstance(row,dict) or not row.get("id"):
                        continue
                    best_direct=0.0; best_q=query
                    for q in queries:
                        value=_score(q,row,media_type,wanted_year,requested_type,country_hint,description_hint)
                        if value>best_direct:
                            best_direct=value; best_q=q
                    candidate_year=_year(row.get("release_date") if media_type=="movie" else row.get("first_air_date"))
                    year_ok=not (wanted_year and candidate_year and abs(int(wanted_year)-int(candidate_year))>=3)
                    # Require meaningful title agreement. A direct ID adds only a
                    # small tie-break bonus after validation, never forced 100%.
                    if locked_tmdb_id and year_ok:
                        direct_candidates.append((0.997,row,media_type,best_q))
                    elif portal_tmdb_id and media_type == preferred_direct_type and year_ok:
                        # Portal-supplied TMDB id is structured identity evidence;
                        # translated display text is not allowed to veto it.
                        direct_candidates.append((0.992,row,media_type,best_q))
                    elif best_direct>=0.62 and year_ok:
                        direct_candidates.append((min(0.97,best_direct+0.03),row,media_type,best_q))
                    else:
                        LOG.info("Rejected direct TMDB id=%s namespace=%s title=%r score=%.3f year=%r", direct_tmdb_id, media_type, row.get("title") or row.get("name"), best_direct, candidate_year)
                except Exception as exc:
                    LOG.debug("Direct TMDB lookup failed id=%s namespace=%s: %s", direct_tmdb_id, media_type, exc)
            for candidate in direct_candidates:
                score,row,media_type,q=candidate
                sig=(media_type,str(row.get("id")))
                if sig in seen: continue
                seen.add(sig); ranked.append(candidate)
            if direct_candidates and (locked_tmdb_id or portal_tmdb_id):
                # A structured portal TMDB id (or an HDD-locked id) is already
                # the answer.  Do not let a later IMDb/name search outscore it
                # and silently switch Sugar/Korean/etc to another work.
                direct_candidates.sort(key=lambda x:x[0],reverse=True)
                ranked=[direct_candidates[0]]
                authoritative_direct=True

        # 1) If portal provides IMDb id, this is the most reliable lookup when
        # no authoritative TMDB identity was already resolved above.
        iid=provided_iid
        if iid and not authoritative_direct and not locked_tmdb_id and not portal_tmdb_id:
            try:
                found=self._get("/find/%s"%iid,{"external_source":"imdb_id","language":self.language})
                add_results(found.get("movie_results"),"movie",query)
                add_results(found.get("tv_results"),"tv",query)
            except Exception as exc:
                try:LOG.info("TMDB find failed imdb=%s: %s",iid,exc)
                except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)

        # 2) Search cleaned names with year first, then without. Search the portal
        # type first but permit type fallback because many Stalker catalogues mix them.
        if not authoritative_direct and not locked_tmdb_id and not portal_tmdb_id and (not ranked or max([x[0] for x in ranked] or [0]) < 0.84):
            for q in queries:
                media_type=requested_type
                attempts=[True,False] if wanted_year else [False]
                for with_year in attempts:
                    search_language="en-US" if (_latin_heavy(q) and (country_hint in ("KR","TR","IN","MY","ID","TH","JP","CN","PH") or any(ord(ch)>127 for ch in raw))) else self.language
                    params={"query":q,"language":search_language,"include_adult":"false"}
                    if with_year and wanted_year:
                        params["year" if media_type=="movie" else "first_air_date_year"]=wanted_year
                    try:
                        payload=self._get("/search/"+media_type,params)
                        add_results(payload.get("results"),media_type,q)
                    except Exception as exc:
                        try:LOG.info("TMDB search failed type=%s q=%r year=%s: %s",media_type,q,wanted_year if with_year else None,exc)
                        except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
                    if ranked and max(x[0] for x in ranked)>=0.91:break
                if ranked and max(x[0] for x in ranked)>=0.86:break
            requested_best=max([x[0] for x in ranked if x[2]==requested_type] or [0])
            # Country-tagged catalogues are frequently filed under the wrong portal
            # namespace (movie inside Series or vice versa). Search the alternate
            # namespace unless the requested-type match is already very strong.
            alternate_gate = 0.88 if country_hint else 0.66
            if requested_best < alternate_gate:
                for q in queries[:3]:
                    media_type=alternate_type
                    search_language="en-US" if (_latin_heavy(q) and (country_hint in ("KR","TR","IN","MY","ID","TH","JP","CN","PH") or any(ord(ch)>127 for ch in raw))) else self.language
                    params={"query":q,"language":search_language,"include_adult":"false"}
                    if wanted_year:params["year" if media_type=="movie" else "first_air_date_year"]=wanted_year
                    try:
                        payload=self._get("/search/"+media_type,params)
                        add_results(payload.get("results"),media_type,q)
                    except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
                    if ranked and max(x[0] for x in ranked)>=0.90:break

        # 3) Last resort: TMDB multi-search catches catalogues classified incorrectly.
        requested_best=max([x[0] for x in ranked if x[2]==requested_type] or [0])
        if not authoritative_direct and not locked_tmdb_id and not portal_tmdb_id and (not ranked or requested_best < 0.48) and not wanted_year:
            for q in queries[:1]:
                try:
                    search_language="en-US" if (_latin_heavy(q) and (country_hint in ("KR","TR","IN","MY","ID","TH","JP","CN","PH") or any(ord(ch)>127 for ch in raw))) else self.language
                    payload=self._get("/search/multi",{"query":q,"language":search_language,"include_adult":"false"})
                    for row in payload.get("results") or []:
                        mt=str(row.get("media_type") or "")
                        if mt in ("movie","tv"):add_results([row],mt,q)
                except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)

        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:
            try:LOG.info("TMDB no result raw=%r cleaned=%r year=%r",raw,queries,wanted_year)
            except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
            return {"matched":False,"query":query,"confidence":0.0,"reason":"no_results"}
        confidence,best,media_type,matched_query=ranked[0]
        by=_year(best.get("release_date") if media_type=="movie" else best.get("first_air_date"))
        threshold=0.54 if wanted_year and by and abs(int(wanted_year)-int(by))<=1 else 0.62
        if media_type != requested_type: threshold=max(threshold,0.86)
        if wanted_year and by and abs(int(wanted_year)-int(by))>=3: threshold=max(threshold,0.76)
        if confidence < threshold:
            try:LOG.info("TMDB low confidence raw=%r cleaned=%r best=%r score=%.3f",raw,queries,(best.get("title") or best.get("name")),confidence)
            except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
            return {"matched":False,"query":query,"confidence":confidence,"reason":"low_confidence"}

        # Independent identity evidence. Exact title alone is not enough for
        # ambiguous one-word titles. Prefer no enrichment over confidently wrong art.
        best_title=best.get("title") if media_type=="movie" else best.get("name")
        best_original=best.get("original_title") if media_type=="movie" else best.get("original_name")
        title_exact=bool(_norm(matched_query) and _norm(matched_query) in (_norm(best_title or ""),_norm(best_original or "")))
        title_tokens=[x for x in _norm(matched_query).split() if x]
        evidence=1 if title_exact else 0
        if title_exact and (len(title_tokens)>=2 or len(_norm(matched_query))>=12): evidence+=1
        if wanted_year and by and abs(int(wanted_year)-int(by))<=1:evidence+=1
        if country_hint:
            wanted_lang=_language_for_country(country_hint); ol=str(best.get("original_language") or "").lower(); origins=[str(x).upper() for x in (best.get("origin_country") or [])]
            if (wanted_lang and ol==wanted_lang) or country_hint in origins:evidence+=1
        desc_sim=_description_similarity(description_hint,best.get("overview") or "")
        if desc_sim>=0.16:evidence+=1
        # Search-derived identity needs strong title agreement. Year/IMDb learned
        # from the candidate itself never upgrades a weak title into a match.
        raw_primary=_norm(clean_title(raw))
        candidate_titles=[_norm(best_title or ""),_norm(best_original or "")]
        primary_exact=bool(raw_primary and raw_primary in candidate_titles)
        primary_tokens=set(raw_primary.split());candidate_token_sets=[set(x.split()) for x in candidate_titles if x]
        primary_overlap=max([float(len(primary_tokens & x))/float(max(1,max(len(primary_tokens),len(x)))) for x in candidate_token_sets] or [0.0])
        strong_primary=bool(primary_exact or (len(raw_primary)>=8 and primary_overlap>=0.75 and max([difflib.SequenceMatcher(None,raw_primary,x).ratio() for x in candidate_titles if x] or [0.0])>=0.78))
        structured_independent=bool(locked_tmdb_id or portal_tmdb_id or provided_iid)
        identity_verified=bool(structured_independent or (strong_primary and evidence>=2))
        # Short one-word names are inherently ambiguous (Sugar is the classic
        # failure). Description similarity alone is not enough to switch the
        # identity; require a year/country or structured external id.
        structured_evidence = bool(locked_tmdb_id or portal_tmdb_id or provided_iid or (wanted_year and by and abs(int(wanted_year)-int(by))<=1))
        if country_hint:
            wanted_lang=_language_for_country(country_hint); ol=str(best.get("original_language") or "").lower(); origins=[str(x).upper() for x in (best.get("origin_country") or [])]
            structured_evidence = structured_evidence or bool((wanted_lang and ol==wanted_lang) or country_hint in origins)
        if len(title_tokens) <= 1 and not structured_evidence:
            identity_verified=False
        if not identity_verified:
            try:LOG.info("TMDB rejected ambiguous identity raw=%r best=%r score=%.3f evidence=%s",raw,best_title,confidence,evidence)
            except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
            return {"matched":False,"query":query,"confidence":confidence,"reason":"ambiguous_identity"}

        identity_source = "locked_cache" if locked_tmdb_id else ("portal_tmdb_id" if portal_tmdb_id else ("portal_imdb_id" if provided_iid else ("imdb_id" if iid else "verified_search")))
        tmdb_id=best.get("id")
        try:
            _rating_append="release_dates" if media_type=="movie" else "content_ratings"
            details=self._get("/%s/%s"%(media_type,tmdb_id),{
                "language":self.language,
                "append_to_response":"credits,images,external_ids,%s"%_rating_append,
                "include_image_language":"ar,en,null",
            })
        except Exception as exc:
            return {"matched":False,"query":query,"confidence":confidence,"error":str(exc),"reason":"details_failed"}
        # Stage 3 raw metadata: trust TMDb's selected locale exactly.  When a
        # descriptive field is empty, ask English and use it only for that field.
        # No script inspection, translations-endpoint override or Ultra language
        # preference is allowed here.
        fallback=None
        overview=str(details.get("overview") or "").strip()
        overview_language=self.language if overview else ""
        if not overview:
            try:fallback=self._get("/%s/%s"%(media_type,tmdb_id),{"language":"en-US","append_to_response":"credits,external_ids"})
            except Exception:fallback=None
            overview=str((fallback or {}).get("overview") or "").strip();overview_language="en-US" if overview else ""
        title=details.get("title") if media_type=="movie" else details.get("name")
        original_title=details.get("original_title") if media_type=="movie" else details.get("original_name")
        date=details.get("release_date") if media_type=="movie" else details.get("first_air_date")
        genres=[str(x.get("name")) for x in (details.get("genres") or []) if isinstance(x,dict) and x.get("name")]
        if not genres and self.language.lower()!="en-us":
            if fallback is None:
                try:fallback=self._get("/%s/%s"%(media_type,tmdb_id),{"language":"en-US","append_to_response":"credits,external_ids"})
                except Exception:fallback={}
            genres=[str(x.get("name")) for x in ((fallback or {}).get("genres") or []) if isinstance(x,dict) and x.get("name")]
        runtime=details.get("runtime")
        if media_type=="tv" and not runtime:
            runtimes=details.get("episode_run_time") or []
            runtime=runtimes[0] if isinstance(runtimes,list) and runtimes else None
        credits=details.get("credits") if isinstance(details.get("credits"),dict) else {}
        cast=[str(p.get("name")) for p in (credits.get("cast") or [])[:6] if isinstance(p,dict) and p.get("name")]
        directors=[];writers=[];crew=[]
        for p in (credits.get("crew") or []):
            if not isinstance(p,dict) or not p.get("name"):continue
            name=str(p.get("name"));job=str(p.get("job") or "");dept=str(p.get("department") or "")
            if job=="Director" and name not in directors:directors.append(name)
            if job in ("Writer","Screenplay","Story","Teleplay","Original Story","Novel") or dept=="Writing":
                if name not in writers:writers.append(name)
            if job in ("Director","Creator","Executive Producer") and name not in crew:crew.append(name)
        if media_type=="tv":
            for p in (details.get("created_by") or []):
                if isinstance(p,dict) and p.get("name"):
                    name=str(p.get("name"))
                    if name not in writers:writers.insert(0,name)
                    if name not in crew:crew.insert(0,name)
        external_ids=details.get("external_ids") if isinstance(details.get("external_ids"),dict) else {}
        imdb_id=str(external_ids.get("imdb_id") or iid or "").strip()
        countries=[]
        for row in (details.get("production_countries") or details.get("origin_country") or []):
            value=(row.get("iso_3166_1") or row.get("name")) if isinstance(row,dict) else row
            if value and str(value) not in countries:countries.append(str(value))
        certification=_tmdb_certification(details,media_type)
        if not certification:
            try:
                rating_payload=self._get(rating_endpoint(media_type,tmdb_id)) or {}
            except Exception as exc:
                try:LOG.info("TMDB age-rating lookup failed type=%s id=%s: %s",media_type,tmdb_id,exc)
                except Exception:pass
                rating_payload={}
            certification=_tmdb_certification(details,media_type,supplemental=rating_payload)

        # Raw selected-locale credits.  English is fallback-only when TMDb left a
        # credit field empty; never force Latin names by country/original language.
        if self.language.lower()!="en-us" and (not cast or not directors or not writers):
            try:
                if fallback is None or not isinstance((fallback or {}).get("credits"),dict):
                    fallback=self._get("/%s/%s"%(media_type,tmdb_id),{"language":"en-US","append_to_response":"credits,external_ids"}) or {}
                encredits=fallback.get("credits") if isinstance(fallback.get("credits"),dict) else {}
                if not cast:
                    cast=[str(p.get("name")) for p in (encredits.get("cast") or [])[:6] if isinstance(p,dict) and p.get("name")]
                if not directors or not writers:
                    edirectors=[];ewriters=[]
                    for p in (encredits.get("crew") or []):
                        if not isinstance(p,dict) or not p.get("name"):continue
                        ename=str(p.get("name"));job=str(p.get("job") or "");dept=str(p.get("department") or "")
                        if job=="Director" and ename not in edirectors:edirectors.append(ename)
                        if job in ("Writer","Screenplay","Story","Teleplay","Original Story","Novel") or dept=="Writing":
                            if ename not in ewriters:ewriters.append(ename)
                    if media_type=="tv":
                        for p in (fallback.get("created_by") or []):
                            if isinstance(p,dict) and p.get("name") and str(p.get("name")) not in ewriters:ewriters.insert(0,str(p.get("name")))
                    if not directors and edirectors:directors=edirectors[:3]
                    if not writers and ewriters:writers=ewriters[:4]
            except Exception as exc:
                LOG.debug("English credit fallback failed id=%s: %s",tmdb_id,exc)

        images=details.get("images") if isinstance(details.get("images"),dict) else {}
        # release: Ultra title-logo policy: take the first valid TMDB logo returned with
        # the existing details request; do not perform a separate logo lookup.
        logo_path="";logo_language=""
        logos=images.get("logos") if isinstance(images,dict) else []
        if logos:
            try:
                from .title_logo_runtime import title_logo_policy_languages
                _policy_row={
                    "original_language":str(details.get("original_language") or best.get("original_language") or "").lower(),
                    "origin_country":details.get("origin_country") or best.get("origin_country") or [],
                    "countries":details.get("production_countries") or [],
                }
                _allowed=title_logo_policy_languages({},_policy_row) or ("en",)
                _logo_rows=[x for x in logos if isinstance(x,dict) and x.get("file_path")]
                _picked=None
                for _wanted in _allowed:
                    _same=[x for x in _logo_rows if str(x.get("iso_639_1") or "").lower()==_wanted]
                    _same.sort(key=lambda x:(float(x.get("vote_average") or 0),int(x.get("width") or 0)),reverse=True)
                    if _same:_picked=_same[0];break
                # R230 coverage policy: preferred explicit -> neutral -> any
                # explicit language. Generic artwork carries the real language
                # tag; the unified resolver decides whether to use/cache it.
                if _picked is None:
                    _neutral=[x for x in _logo_rows if x.get("iso_639_1") in (None,"")]
                    _neutral.sort(key=lambda x:(float(x.get("vote_average") or 0),int(x.get("width") or 0)),reverse=True)
                    if _neutral:_picked=_neutral[0]
                if _picked is None:
                    _other=[x for x in _logo_rows if str(x.get("iso_639_1") or "").lower() not in set(_allowed) and x.get("iso_639_1") not in (None,"")]
                    _other.sort(key=lambda x:(float(x.get("vote_average") or 0),int(x.get("width") or 0)),reverse=True)
                    if _other:_picked=_other[0]
                if _picked:
                    logo_path=str(_picked.get("file_path") or "")
                    logo_language=str(_picked.get("iso_639_1") or "").lower()
            except Exception:
                logo_path="";logo_language=""
        # R260 official-artwork authority: TMDb Details primary paths are the
        # only normal poster/backdrop choice. Gallery rows can be used only when
        # the verified title Details payload has no primary path at all.
        backdrop_path=str(details.get("backdrop_path") or "").strip()
        poster_path=str(details.get("poster_path") or "").strip()
        backdrop_paths=[]
        def _add_backdrop_path(value):
            value=str(value or "").strip()
            if value and value not in backdrop_paths:backdrop_paths.append(value)
        _add_backdrop_path(backdrop_path)

        # If the selected locale omits artwork, ask the same verified title once
        # without a locale before touching the gallery. This remains a Details
        # primary, not a ranked alternative image.
        if not backdrop_path or not poster_path:
            try:
                primary_fallback=self._get("/%s/%s"%(media_type,tmdb_id),{}) or {}
            except Exception:
                primary_fallback={}
            if not backdrop_path:
                backdrop_path=str(primary_fallback.get("backdrop_path") or "").strip()
                _add_backdrop_path(backdrop_path)
            if not poster_path:
                poster_path=str(primary_fallback.get("poster_path") or "").strip()

        # Gallery is strict fallback-only. Never replace a Details primary with
        # a prettier/larger/reranked image, so cached artwork cannot rotate.
        if not backdrop_path or not poster_path:
            try:
                image_payload=self._get("/%s/%s/images"%(media_type,tmdb_id),{"include_image_language":"ar,en,null"}) or {}
            except Exception as exc:
                try:LOG.info("TMDB images fallback lookup failed type=%s id=%s: %s",media_type,tmdb_id,exc)
                except Exception:pass
                image_payload={}
            if not backdrop_path:
                backs=[x for x in (image_payload.get("backdrops") or []) if isinstance(x,dict) and x.get("file_path") and int(x.get("width") or 0)>=int(x.get("height") or 1)]
                backs.sort(key=lambda x:(int(x.get("width") or 0),float(x.get("vote_average") or 0),int(x.get("vote_count") or 0)),reverse=True)
                if backs:
                    backdrop_path=str(backs[0].get("file_path") or "").strip()
                    _add_backdrop_path(backdrop_path)
            if not poster_path:
                posts=[x for x in (image_payload.get("posters") or []) if isinstance(x,dict) and x.get("file_path") and int(x.get("height") or 0)>int(x.get("width") or 0)]
                lang=str(self.language or "").split("-")[0].lower()
                def _poster_rank(row):
                    iso=str(row.get("iso_639_1") or "").lower()
                    lang_bonus=3 if iso==lang else (2 if iso=="en" else (1 if not iso else 0))
                    return (lang_bonus,int(row.get("width") or 0),float(row.get("vote_average") or 0),int(row.get("vote_count") or 0))
                posts.sort(key=_poster_rank,reverse=True)
                if posts:poster_path=str(posts[0].get("file_path") or "").strip()
        _add_backdrop_path(backdrop_path)
        result={
            "matched":True,"identity_verified":True,"identity_source":identity_source,"identity_evidence":int(evidence),"source":"TMDB","media_type":media_type,"tmdb_id":tmdb_id,"confidence":round(float(confidence),3),
            "query":matched_query,"title":str(title or query),"original_title":str(original_title or ""),"original_language":str(details.get("original_language") or best.get("original_language") or "").lower(),"overview":overview,"overview_language":overview_language,"country_hint":country_hint,
            "year":_year(date),"release_date":date or "","rating":details.get("vote_average"),"vote_count":details.get("vote_count"),
            "certification":certification,"age_rating":certification,
            "genres":genres,"runtime":runtime,"number_of_seasons":details.get("number_of_seasons") or 0,"number_of_episodes":details.get("number_of_episodes") or 0,"cast":cast,"crew":crew[:4],"directors":directors[:3],"writers":writers[:4],
            "imdb_id":imdb_id,"countries":countries[:3],"poster_url":self.image_url(poster_path,"w500"),
            "logo_url":self.image_url(logo_path,"w300") if logo_path else "","logo_language":logo_language,
            "backdrop_url":self.image_url((backdrop_paths[0] if backdrop_paths else backdrop_path),"w1280"),
            "backdrop_urls":[self.image_url(x,"w1280") for x in backdrop_paths[:6] if self.image_url(x,"w1280")],
        }
        try:LOG.info("TMDB match raw=%r query=%r -> %r type=%s score=%.3f id=%s",raw,matched_query,title,media_type,confidence,tmdb_id)
        except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
        self._cache_write(key,{k:v for k,v in result.items() if k not in ("poster_local","backdrop_local")})
        # Artwork Engine V5 ownership rule (us184): TMDBClient.enrich is
        # metadata/API only.  It may expose TMDB image URLs, but it must never
        # download image bytes or populate *_local. ArtworkV2 is the single
        # owner of HDD poster/backdrop files and decoder normalization.
        result.pop("poster_local",None)
        result.pop("backdrop_local",None)
        try:self._cache_write(key,{k:v for k,v in result.items() if k not in ("poster_local","backdrop_local")})
        except Exception as exc: diagnostic_failure("tmdb.failsoft", exc)
        return result

