# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function
from ..version import PLUGIN_VERSION
from .. import _
from ..title_clean import display_title as _subtitle_display_title, tmdb_search_aliases as _subtitle_search_aliases

import hashlib
import json
import os
import re
import unicodedata
import zipfile
try:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:
    from urllib import urlencode
    from urllib2 import Request, urlopen

SUBDL_SEARCH = "https://api.subdl.com/api/v1/subtitles"
SUBDL_DOWNLOAD = "https://dl.subdl.com"
SUBSOURCE_BASE = "https://api.subsource.net/api/v1"
CONFIG_DIR = "/etc/enigma2/ultrastalker"
CACHE_DIR = "/media/hdd/UltraStalker/subtitles"

def _safe_int(value):
    try:
        value = str(value or "").strip()
        if not value:
            return 0
        m = re.search(r"\d+", value)
        return int(m.group(0)) if m else 0
    except Exception:
        return 0

def _pick(item, *keys):
    for key in keys:
        value = item.get(key) if isinstance(item, dict) else None
        if value not in (None, ""):
            return value
    return None

def _identity(item, media_type, display_name=""):
    item = item if isinstance(item, dict) else {}
    kind = "tv" if str(media_type or "").lower() in ("series", "episode") else "movie"
    title = str(_pick(item, "_series_title", "series_name", "name", "title", "movie_name") or display_name or "").strip()
    year = _safe_int(_pick(item, "year", "release_year", "releasedate", "release_date"))
    tmdb = str(_pick(item, "tmdb_id", "tmdb", "_tmdb_id", "tmdbId") or "").strip()
    imdb = str(_pick(item, "imdb_id", "imdb", "_imdb_id", "imdbId") or "").strip()
    season = _safe_int(_pick(item, "season_number", "season", "_season_number", "season_num"))
    episode = _safe_int(_pick(item, "episode_number", "episode", "_episode_number", "episode_num", "number"))
    return {"type":kind, "title":title, "year":year, "tmdb_id":tmdb, "imdb_id":imdb,
            "season":season, "episode":episode}

def _cache_key(identity):
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def _ensure_cache():
    for path in (CACHE_DIR, "/tmp/UltraStalker-subtitles"):
        try:
            os.makedirs(path, mode=0o700, exist_ok=True)
            probe=os.path.join(path,".write-test")
            with open(probe,"w") as h: h.write("1")
            os.unlink(probe)
            return path
        except Exception:
            pass
    return "/tmp"

def cached_subtitle(item, media_type, display_name=""):
    ident=_identity(item, media_type, display_name)
    root=_ensure_cache()
    prefix=_cache_key(ident)
    for ext in (".srt",".ass",".ssa",".vtt"):
        path=os.path.join(root,prefix+ext)
        if os.path.isfile(path) and os.path.getsize(path)>16:
            return path, ident
    return "", ident

def _download(url, timeout=15):
    if str(url).startswith("/"):
        url=SUBDL_DOWNLOAD+str(url)
    req=Request(str(url), headers={"User-Agent":"UltraStalker/%s" % PLUGIN_VERSION, "Accept":"*/*"})
    with urlopen(req, timeout=timeout) as res:
        data=res.read(8*1024*1024+1)
    if len(data)>8*1024*1024:
        raise RuntimeError("subtitle download too large")
    return data

def _decode_text(data):
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig","replace")
    if data.startswith((b"\xff\xfe",b"\xfe\xff")):
        return data.decode("utf-16","replace")
    for enc in ("utf-8","cp1256","windows-1256","iso-8859-6"):
        try:
            return data.decode(enc)
        except Exception:
            pass
    return data.decode("utf-8","replace")

def _normalize_srt_bytes(data):
    text=_decode_text(data)
    text=text.replace("\r\n","\n").replace("\r","\n")
    return text.encode("utf-8")


SUBTITLE_LANGUAGES = {
    # Canonical Ultra Stalker key -> SubDL API code + accepted result aliases.
    # The menu is generated from the same 29-language interface catalogue.
    "EN": {"name": "English", "api_code": "EN", "aliases": ("EN", "ENG", "ENGLISH")},
    "AR": {"name": "العربية", "api_code": "AR", "aliases": ("AR", "ARA", "ARABIC")},
    "DE": {"name": "Deutsch", "api_code": "DE", "aliases": ("DE", "GER", "DEU", "GERMAN")},
    "FR": {"name": "Français", "api_code": "FR", "aliases": ("FR", "FRE", "FRA", "FRENCH")},
    "TR": {"name": "Türkçe", "api_code": "TR", "aliases": ("TR", "TUR", "TURKISH")},
    "ES": {"name": "Español", "api_code": "ES", "aliases": ("ES", "SPA", "SPANISH", "CASTILIAN")},
    "IT": {"name": "Italiano", "api_code": "IT", "aliases": ("IT", "ITA", "ITALIAN")},
    "PT_BR": {"name": "Português (Brasil)", "api_code": "PT", "aliases": ("PT", "POR", "POB", "PORTUGUESE", "BRAZILIAN PORTUGUESE", "PT-BR", "PT_BR")},
    "NL": {"name": "Nederlands", "api_code": "NL", "aliases": ("NL", "DUT", "NLD", "DUTCH")},
    "PL": {"name": "Polski", "api_code": "PL", "aliases": ("PL", "POL", "POLISH")},
    "RU": {"name": "Русский", "api_code": "RU", "aliases": ("RU", "RUS", "RUSSIAN")},
    "EL": {"name": "Ελληνικά", "api_code": "EL", "aliases": ("EL", "ELL", "GRE", "GREEK")},
    "CS": {"name": "Čeština", "api_code": "CS", "aliases": ("CS", "CZE", "CES", "CZECH")},
    "SK": {"name": "Slovenčina", "api_code": "SK", "aliases": ("SK", "SLO", "SLK", "SLOVAK")},
    "HU": {"name": "Magyar", "api_code": "HU", "aliases": ("HU", "HUN", "HUNGARIAN")},
    "RO": {"name": "Română", "api_code": "RO", "aliases": ("RO", "RUM", "RON", "ROMANIAN", "MOLDAVIAN", "MOLDOVAN")},
    "SR": {"name": "Српски", "api_code": "SR", "aliases": ("SR", "SCC", "SRP", "SERBIAN")},
    "HR": {"name": "Hrvatski", "api_code": "HR", "aliases": ("HR", "HRV", "CROATIAN")},
    "BG": {"name": "Български", "api_code": "BG", "aliases": ("BG", "BUL", "BULGARIAN")},
    "UK": {"name": "Українська", "api_code": "UK", "aliases": ("UK", "UKR", "UKRAINIAN")},
    "SV": {"name": "Svenska", "api_code": "SV", "aliases": ("SV", "SWE", "SWEDISH")},
    "DA": {"name": "Dansk", "api_code": "DA", "aliases": ("DA", "DAN", "DANISH")},
    "FI": {"name": "Suomi", "api_code": "FI", "aliases": ("FI", "FIN", "FINNISH")},
    # SubDL currently exposes Chinese under the provider code ZH. Keep both
    # Ultra Stalker interface variants selectable while querying that authority.
    "ZH_CN": {"name": "简体中文", "api_code": "ZH", "aliases": ("ZH", "CHI", "ZHO", "CHINESE", "ZH-CN", "ZH_CN")},
    "ZH_TW": {"name": "繁體中文", "api_code": "ZH", "aliases": ("ZH", "CHI", "ZHO", "CHINESE", "ZH-TW", "ZH_TW")},
    "JA": {"name": "日本語", "api_code": "JA", "aliases": ("JA", "JPN", "JAPANESE")},
    "KO": {"name": "한국어", "api_code": "KO", "aliases": ("KO", "KOR", "KOREAN")},
    "ID": {"name": "Bahasa Indonesia", "api_code": "ID", "aliases": ("ID", "IND", "INDONESIAN")},
    "FA": {"name": "فارسی", "api_code": "FA", "aliases": ("FA", "PER", "FAS", "PERSIAN", "FARSI", "FARSI_PERSIAN")},
}

INTERFACE_TO_SUBDL_LANGUAGE = {
    "en":"EN", "ar":"AR", "de":"DE", "fr":"FR", "tr":"TR",
    "es":"ES", "it":"IT", "pt-br":"PT_BR", "nl":"NL", "pl":"PL",
    "ru":"RU", "el":"EL", "cs":"CS", "sk":"SK", "hu":"HU",
    "ro":"RO", "sr":"SR", "hr":"HR", "bg":"BG", "uk":"UK",
    "sv":"SV", "da":"DA", "fi":"FI", "zh-cn":"ZH_CN",
    "zh-tw":"ZH_TW", "ja":"JA", "ko":"KO", "id":"ID", "fa":"FA",
}

def _language_spec(language):
    code=str(language or "AR").strip().upper().replace("-","_")
    if code not in SUBTITLE_LANGUAGES:
        raise RuntimeError("Unsupported subtitle language: %s" % code)
    spec=SUBTITLE_LANGUAGES[code]
    return code, spec["name"], tuple(spec["aliases"]), str(spec.get("api_code") or code).upper()

def interface_subtitle_language_code(interface_code):
    return INTERFACE_TO_SUBDL_LANGUAGE.get(str(interface_code or "en").strip().lower().replace("_","-"), "EN")

def subtitle_language_name(language):
    try:
        return _language_spec(language)[1]
    except Exception:
        return _("English")

def is_supported_subtitle_language(language):
    code=str(language or "").strip().upper().replace("-","_")
    return code in SUBTITLE_LANGUAGES

def _language_identity(item, media_type, display_name, language):
    ident=_identity(item,media_type,display_name)
    ident["subtitle_language"]=str(language or "AR").upper()
    return ident

def cached_subtitle_language(item, media_type, display_name="", language="AR"):
    code, _name, _aliases, _api_code=_language_spec(language)
    ident=_language_identity(item,media_type,display_name,code)
    root=_ensure_cache();prefix=_cache_key(ident)
    for ext in (".srt",".ass",".ssa",".vtt"):
        path=os.path.join(root,prefix+ext)
        if os.path.isfile(path) and os.path.getsize(path)>16:
            return path, ident
    return "", ident

def search_language(api_key, item, media_type, display_name="", language="AR"):
    api_key=str(api_key or "").strip()
    if not api_key:
        raise RuntimeError("SubDL API key is not configured")
    code, language_name, aliases, api_code=_language_spec(language)
    cached, ident=cached_subtitle_language(item,media_type,display_name,code)
    cached_candidate={"cached_path":cached,"name":language_name,"score":10000} if cached else None
    params={"api_key":api_key,"type":ident["type"],"languages":api_code,
            "subs_per_page":"20","releases":"1","unpack":"1","client":"custom_integration"}
    if ident["tmdb_id"]: params["tmdb_id"]=ident["tmdb_id"]
    elif ident["imdb_id"]: params["imdb_id"]=ident["imdb_id"]
    elif ident["title"]:
        params["film_name"]=ident["title"]
        if ident["year"]:params["year"]=str(ident["year"])
    else: raise RuntimeError("No title or TMDB/IMDb id is available for subtitle search")
    if ident["type"]=="tv":
        if ident["season"]:params["season_number"]=str(ident["season"])
        if ident["episode"]:params["episode_number"]=str(ident["episode"])
    req=Request(SUBDL_SEARCH+"?"+urlencode(params),headers={"Accept":"application/json","User-Agent":"UltraStalker/%s" % PLUGIN_VERSION})
    with urlopen(req,timeout=15) as res:
        payload=json.loads(res.read(2*1024*1024).decode("utf-8","replace"))
    if not isinstance(payload,dict) or not payload.get("status"):
        raise RuntimeError(str((payload or {}).get("error") or "SubDL search failed"))
    subtitles=payload.get("subtitles") or []
    if not subtitles:
        if cached_candidate:return [cached_candidate], ident
        raise RuntimeError("No %s subtitle found"%language_name)
    candidates=[]
    for row in subtitles:
        if not isinstance(row,dict):continue
        release=str(row.get("release_name") or row.get("name") or (language_name+" subtitle"))
        unpack=row.get("unpack_files") or []
        for f in unpack:
            if not isinstance(f,dict):continue
            fs=_safe_int(f.get("season"));fe=_safe_int(f.get("episode"))
            if ident["type"]=="tv":
                if ident["season"] and fs and fs!=ident["season"]:continue
                if ident["episode"] and fe and fe!=ident["episode"]:continue
            lang=str(f.get("language") or "").upper()
            if lang and lang not in aliases:continue
            fmt=str(f.get("format") or "").lower()
            score=100+(50 if ident["episode"] and fe==ident["episode"] else 0)+(20 if ident["season"] and fs==ident["season"] else 0)+(10 if fmt=="srt" else 0)
            url=str(f.get("url") or "")
            if url:candidates.append({"url":url,"name":str(f.get("name") or release),"format":fmt,"archive":False,"score":score})
        url=str(row.get("url") or "")
        if url:
            score=20+(20 if str(row.get("language") or "").upper() in aliases else 0)+(30 if ident["episode"] and _safe_int(row.get("episode"))==ident["episode"] else 0)
            candidates.append({"url":url,"name":release,"format":"","archive":True,"score":score})
    candidates.sort(key=lambda x:int(x.get("score") or 0),reverse=True)
    if cached_candidate:candidates.insert(0,cached_candidate)
    if not candidates:raise RuntimeError("SubDL returned no downloadable %s subtitle"%language_name)
    return candidates[:20], ident

def search_arabic(api_key, item, media_type, display_name=""):
    return search_language(api_key,item,media_type,display_name,"AR")

def _legacy_search_arabic(api_key, item, media_type, display_name=""):
    api_key=str(api_key or "").strip()
    if not api_key:
        raise RuntimeError("SubDL API key is not configured")
    cached, ident=cached_subtitle(item,media_type,display_name)
    cached_candidate={"cached_path":cached,"name":"Cached Arabic subtitle","score":10000} if cached else None

    params={"api_key":api_key,"type":ident["type"],"languages":"AR",
            "subs_per_page":"20","releases":"1","unpack":"1","client":"custom_integration"}
    if ident["tmdb_id"]: params["tmdb_id"]=ident["tmdb_id"]
    elif ident["imdb_id"]: params["imdb_id"]=ident["imdb_id"]
    elif ident["title"]:
        params["film_name"]=ident["title"]
        if ident["year"]:params["year"]=str(ident["year"])
    else: raise RuntimeError("No title or TMDB/IMDb id is available for subtitle search")
    if ident["type"]=="tv":
        if ident["season"]:params["season_number"]=str(ident["season"])
        if ident["episode"]:params["episode_number"]=str(ident["episode"])

    req=Request(SUBDL_SEARCH+"?"+urlencode(params),headers={"Accept":"application/json","User-Agent":"UltraStalker/%s" % PLUGIN_VERSION})
    with urlopen(req,timeout=15) as res:
        payload=json.loads(res.read(2*1024*1024).decode("utf-8","replace"))
    if not isinstance(payload,dict) or not payload.get("status"):
        raise RuntimeError(str((payload or {}).get("error") or "SubDL search failed"))
    subtitles=payload.get("subtitles") or []
    if not subtitles:
        if cached_candidate:return [cached_candidate], ident
        raise RuntimeError("No Arabic subtitle found")

    candidates=[]
    for row in subtitles:
        if not isinstance(row,dict):continue
        release=str(row.get("release_name") or row.get("name") or "Arabic subtitle")
        unpack=row.get("unpack_files") or []
        for f in unpack:
            if not isinstance(f,dict):continue
            fs=_safe_int(f.get("season"));fe=_safe_int(f.get("episode"))
            if ident["type"]=="tv":
                if ident["season"] and fs and fs!=ident["season"]:continue
                if ident["episode"] and fe and fe!=ident["episode"]:continue
            lang=str(f.get("language") or "").upper()
            if lang and lang not in ("AR","ARA","ARABIC"):continue
            fmt=str(f.get("format") or "").lower()
            score=100+(50 if ident["episode"] and fe==ident["episode"] else 0)+(20 if ident["season"] and fs==ident["season"] else 0)+(10 if fmt=="srt" else 0)
            url=str(f.get("url") or "")
            if url:candidates.append({"url":url,"name":str(f.get("name") or release),"format":fmt,"archive":False,"score":score})
        url=str(row.get("url") or "")
        if url:
            score=20+(20 if str(row.get("language") or "").upper() in ("AR","ARA","ARABIC") else 0)+(30 if ident["episode"] and _safe_int(row.get("episode"))==ident["episode"] else 0)
            candidates.append({"url":url,"name":release,"format":"","archive":True,"score":score})
    candidates.sort(key=lambda x:int(x.get("score") or 0),reverse=True)
    if cached_candidate:
        # Cache is a convenience choice, never a reason to skip the live API search.
        candidates.insert(0,cached_candidate)
    if not candidates:raise RuntimeError("SubDL returned no downloadable Arabic subtitle")
    return candidates[:20], ident

def download_candidate(candidate, ident):
    if candidate.get("cached_path"):
        return {"path":candidate["cached_path"],"cached":True,"identity":ident,"release":candidate.get("name") or "Cached Arabic"}
    root=_ensure_cache();base=os.path.join(root,_cache_key(ident))
    data=_download(candidate.get("url") or "")
    is_archive=bool(candidate.get("archive")) or data[:2]==b"PK"
    if is_archive:
        zpath=base+".zip"
        with open(zpath,"wb") as h:h.write(data)
        try:
            with zipfile.ZipFile(zpath,"r") as z:
                files=[n for n in z.namelist() if os.path.splitext(n)[1].lower() in (".srt",".ass",".ssa",".vtt")]
                if not files:raise RuntimeError("subtitle archive has no supported file")
                def fscore(n):
                    low=n.lower();s=10 if low.endswith(".srt") else 0
                    if ident.get("episode") and re.search(r"(?:e|ep|episode)[ ._-]*0*%d(?:\D|$)"%ident["episode"],low):s+=30
                    return s
                files.sort(key=fscore,reverse=True);raw=z.read(files[0]);ext=os.path.splitext(files[0])[1].lower()
        finally:
            try:os.unlink(zpath)
            except Exception:pass
    else:
        raw=data;fmt=str(candidate.get("format") or "").lower()
        ext=("."+fmt) if fmt else os.path.splitext(str(candidate.get("url") or "").split("?",1)[0])[1].lower()
        if ext not in (".srt",".ass",".ssa",".vtt"):ext=".srt"
    target=base+ext
    with open(target,"wb") as h:h.write(_normalize_srt_bytes(raw) if ext in (".srt",".vtt") else raw)
    try:os.chmod(target,0o600)
    except Exception:pass
    return {"path":target,"cached":False,"identity":ident,"release":candidate.get("name") or "Arabic"}



# Official SubSource API language slugs. Ultra keeps one canonical language
# catalogue and maps it per provider so the Player menu stays identical.
SUBSOURCE_LANGUAGE_SLUGS = {
    "EN":"english", "AR":"arabic", "DE":"german", "FR":"french",
    "TR":"turkish", "ES":"spanish", "IT":"italian", "PT_BR":"portuguese",
    "NL":"dutch", "PL":"polish", "RU":"russian", "EL":"greek",
    "CS":"czech", "SK":"slovak", "HU":"hungarian", "RO":"romanian",
    "SR":"serbian", "HR":"croatian", "BG":"bulgarian", "UK":"ukrainian",
    "SV":"swedish", "DA":"danish", "FI":"finnish", "ZH_CN":"chinese",
    "ZH_TW":"chinese", "JA":"japanese", "KO":"korean",
    "ID":"indonesian", "FA":"farsi_persian",
}

def _subsource_headers(api_key):
    return {
        "Accept":"application/json",
        "X-API-Key":str(api_key or "").strip(),
        "User-Agent":"UltraStalker/%s" % PLUGIN_VERSION,
    }

def _subsource_json(api_key, endpoint, params=None, timeout=15):
    api_key=str(api_key or "").strip()
    if not api_key:
        raise RuntimeError("SubSource API key is not configured")
    url=SUBSOURCE_BASE+str(endpoint)
    if params:
        url += "?" + urlencode([(str(k),str(v)) for k,v in params.items() if v not in (None,"")])
    req=Request(url,headers=_subsource_headers(api_key))
    with urlopen(req,timeout=timeout) as res:
        payload=json.loads(res.read(2*1024*1024).decode("utf-8","replace"))
    if not isinstance(payload,dict) or not payload.get("success"):
        message=(payload or {}).get("message") or (payload or {}).get("error") or "SubSource request failed"
        raise RuntimeError(str(message))
    return payload

def _subsource_release_text(value):
    if isinstance(value,(list,tuple)):
        return " ".join(str(x or "").strip() for x in value if str(x or "").strip()).strip()
    return str(value or "").strip()

def _subsource_episode_match(release_info, season, episode):
    season=_safe_int(season);episode=_safe_int(episode)
    if not season or not episode:return False
    raw=_subsource_release_text(release_info).upper()
    compact=re.sub(r"[-._\s]+","",raw)
    patterns=(
        "S%02dE%02d"%(season,episode), "S%dE%d"%(season,episode),
        "S%02dE%d"%(season,episode), "S%dE%02d"%(season,episode),
        "%dX%02d"%(season,episode), "%dX%d"%(season,episode),
    )
    if any(p in compact for p in patterns):return True
    if re.search(r"\bS\s*0*%d\s*[-_. ]?\s*E\s*0*%d\b"%(season,episode),raw,re.I):return True
    if re.search(r"\b0*%d\s*[xX]\s*0*%d\b"%(season,episode),raw,re.I):return True
    # SubSource season-specific results can expose episode-only release names.
    if re.search(r"(?:^|[^0-9])0*%d(?:[^0-9]|$)"%episode,raw):
        if not re.search(r"\b(?:E|EP|EPISODE)\s*0*\d+\b",raw,re.I):return True
    return False

def _subsource_season_pack(release_info, season):
    season=_safe_int(season)
    if not season:return False
    raw=_subsource_release_text(release_info).upper()
    compact=re.sub(r"[-._\s]+","",raw)
    if not re.search(r"\b(?:COMPLETE|FULL|PACK|SEASON\s*PACK)\b",raw,re.I):return False
    return (bool(re.search(r"(?:^|[^A-Z0-9])S(?:EASON)?0*%d(?:[^A-Z0-9]|$)"%season,raw,re.I))
            or ("SEASON%d"%season) in compact or ("S%02d"%season) in compact)

def _subsource_norm_text(value):
    text=unicodedata.normalize("NFKC",str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())

def _subsource_search_titles(ident):
    raw=str((ident or {}).get("title") or "").strip()
    out=[]
    # display_title() is intentionally used only for provider lookup. It removes
    # route/provider/quality decoration such as [CoffeePrison], 2160p, NF, etc.
    # The original item/cache identity remains untouched.
    for value in [_subtitle_display_title(raw)]:
        if value and value not in out:out.append(value)
    try:
        for value in _subtitle_search_aliases(raw):
            value=str(value or "").strip()
            if value and value not in out:out.append(value)
    except Exception:
        pass
    if raw and raw not in out:out.append(raw)
    return out

def _subsource_row_values(row, keys):
    if not isinstance(row,dict):return []
    wanted=set(str(k).casefold() for k in keys)
    out=[]
    def collect(obj,depth=0):
        if not isinstance(obj,dict) or depth>2:return
        for key,value in obj.items():
            lk=str(key).casefold()
            if lk in wanted and value not in (None,""):
                out.append(value)
            elif isinstance(value,dict) and lk in ("movie","show","media","item","data"):
                collect(value,depth+1)
    collect(row)
    return out

def _subsource_row_type(row):
    vals=_subsource_row_values(row,("type","mediaType","media_type","movieType","kind","contentType"))
    for value in vals:
        text=str(value or "").strip().casefold()
        if any(tok in text for tok in ("series","tv","show")):return "tv"
        if any(tok in text for tok in ("movie","film")):return "movie"
    return ""

def _subsource_row_years(row):
    years=set()
    for value in _subsource_row_values(row,("year","releaseYear","release_year","released","releaseDate","release_date")):
        year=_safe_int(value)
        if 1900<=year<=2100:years.add(year)
    for value in _subsource_row_values(row,("title","name","movieTitle","movie_title","originalTitle","original_title","displayTitle")):
        for token in re.findall(r"(?:19|20)\d{2}",str(value or "")):
            try:years.add(int(token))
            except Exception:pass
    return years

def _subsource_row_title_norms(row):
    out=set()
    vals=_subsource_row_values(row,("title","name","movieTitle","movie_title","originalTitle","original_title","displayTitle","fullTitle"))
    for value in vals:
        raw=str(value or "").strip()
        if not raw:continue
        variants=[raw]
        try:variants.insert(0,_subtitle_display_title(raw))
        except Exception:pass
        for text in variants:
            # Year decoration is validated separately, not as part of the title.
            text=re.sub(r"(?:^|[^0-9])(?:19|20)\d{2}(?:[^0-9]|$)"," ",str(text or ""))
            norm=_subsource_norm_text(text)
            if norm:out.add(norm)
    return out

def _subsource_row_imdb_values(row):
    out=set()
    for value in _subsource_row_values(row,("imdb","imdbId","imdb_id","imdbID")):
        text=str(value or "").strip().casefold()
        if text:out.add(text)
    try:
        text=json.dumps(row,ensure_ascii=False,sort_keys=True).casefold()
        out.update(re.findall(r"tt\d{5,12}",text))
    except Exception:
        pass
    return out

def _subsource_pick_movie_id(rows, ident, query_mode="text"):
    """Return only a verified SubSource movieId; never pick row #1 blindly."""
    if not isinstance(rows,list):return ""
    expected_type=str((ident or {}).get("type") or "").strip().casefold()
    expected_year=_safe_int((ident or {}).get("year"))
    expected_imdb=str((ident or {}).get("imdb_id") or "").strip().casefold()
    expected_titles=set()
    for value in _subsource_search_titles(ident):
        # Provider search aliases can still carry a year; validate it separately.
        value=re.sub(r"(?:^|[^0-9])(?:19|20)\d{2}(?:[^0-9]|$)"," ",str(value or ""))
        norm=_subsource_norm_text(value)
        if norm:expected_titles.add(norm)
    scored=[]
    for row in rows:
        if not isinstance(row,dict):continue
        mid=str(row.get("movieId") or row.get("id") or "").strip()
        if not mid:continue
        row_type=_subsource_row_type(row)
        if expected_type and row_type and row_type!=expected_type:
            continue
        row_years=_subsource_row_years(row)
        if expected_year and row_years and expected_year not in row_years:
            continue
        row_titles=_subsource_row_title_norms(row)
        title_match=bool(expected_titles and row_titles and expected_titles.intersection(row_titles))
        imdb_match=bool(expected_imdb and expected_imdb in _subsource_row_imdb_values(row))
        # IMDb is a stable authority. Text lookup must have an exact normalized
        # title match; this is the guard that prevents Enola Holmes -> Squid Game.
        if str(query_mode)=="imdb":
            if not (imdb_match or title_match):continue
        elif not title_match:
            continue
        score=100 if imdb_match else 0
        if title_match:score+=60
        if expected_year and expected_year in row_years:score+=20
        if expected_type and row_type==expected_type:score+=10
        season=_safe_int((ident or {}).get("season"))
        if season:
            try:text=json.dumps(row,ensure_ascii=False,sort_keys=True)
            except Exception:text=""
            if re.search(r"(?:season\s*0*%d\b|\bs0*%d\b)"%(season,season),text,re.I):score+=10
        scored.append((score,mid))
    scored.sort(key=lambda x:x[0],reverse=True)
    return scored[0][1] if scored else ""

def _subsource_movie_id(api_key, ident):
    attempts=[];seen=set()
    # A real IMDb id is the strongest SubSource lookup authority for both movies
    # and series.  Text fallbacks use Ultra's cleaned catalogue search aliases.
    if ident.get("imdb_id"):
        params={"searchType":"imdb","imdb":ident.get("imdb_id")}
        attempts.append((params,"imdb"))
    for title in _subsource_search_titles(ident):
        title=str(title or "").strip()
        if not title:continue
        key=title.casefold()
        if key in seen:continue
        seen.add(key)
        params={"searchType":"text","q":title}
        if ident.get("type")=="tv" and ident.get("season"):
            params["season"]=ident.get("season")
        attempts.append((params,"text"))
    last_error=None
    for params,mode in attempts:
        try:
            payload=_subsource_json(api_key,"/movies/search",params)
            mid=_subsource_pick_movie_id(payload.get("data") or [],ident,mode)
            if mid:return mid
        except Exception as exc:
            last_error=exc
    if last_error:raise last_error
    raise RuntimeError("SubSource could not safely match this title")

def _subsource_identity(item, media_type, display_name, language):
    code, _name, _aliases, _api_code=_language_spec(language)
    ident=_language_identity(item,media_type,display_name,code)
    ident["subtitle_provider"]="subsource"
    return ident

def cached_subsource_language(item, media_type, display_name="", language="AR"):
    ident=_subsource_identity(item,media_type,display_name,language)
    root=_ensure_cache();prefix=_cache_key(ident)
    for ext in (".srt",".ass",".ssa",".vtt"):
        path=os.path.join(root,prefix+ext)
        if os.path.isfile(path) and os.path.getsize(path)>16:return path,ident
    return "",ident

def search_subsource_language(api_key, item, media_type, display_name="", language="AR"):
    api_key=str(api_key or "").strip()
    if not api_key:raise RuntimeError("SubSource API key is not configured")
    code,language_name,_aliases,_api_code=_language_spec(language)
    slug=SUBSOURCE_LANGUAGE_SLUGS.get(code)
    if not slug:raise RuntimeError("SubSource does not support this subtitle language")
    cached,ident=cached_subsource_language(item,media_type,display_name,code)
    cached_candidate={"cached_path":cached,"name":language_name+" • cached","score":10000,"provider":"subsource"} if cached else None
    movie_id=_subsource_movie_id(api_key,ident)
    rows=[];seen=set()
    # Keep receiver traffic bounded. Three rating-sorted pages cover up to 300
    # releases while respecting SubSource's documented API rate limits.
    for page in range(1,4):
        payload=_subsource_json(api_key,"/subtitles",{
            "movieId":movie_id,"language":slug,"sort":"rating","limit":100,"page":page
        })
        batch=payload.get("data") or []
        if not isinstance(batch,list) or not batch:break
        fresh=0
        for row in batch:
            if not isinstance(row,dict):continue
            sid=str(row.get("subtitleId") or row.get("id") or "").strip()
            if not sid or sid in seen:continue
            seen.add(sid);rows.append(row);fresh+=1
        if len(batch)<100 or fresh==0:break
    candidates=[]
    for row in rows:
        sid=str(row.get("subtitleId") or row.get("id") or "").strip()
        if not sid:continue
        release=_subsource_release_text(row.get("releaseInfo") or row.get("release") or row.get("name")) or (language_name+" subtitle")
        score=50
        if ident.get("type")=="tv":
            exact=_subsource_episode_match(release,ident.get("season"),ident.get("episode"))
            pack=_subsource_season_pack(release,ident.get("season"))
            if ident.get("episode") and not (exact or pack):continue
            score += 180 if exact else (60 if pack else 0)
        try:score += min(30,int(float(row.get("rating") or 0)*3))
        except Exception:pass
        candidates.append({"subtitle_id":sid,"name":release,"archive":True,"score":score,"provider":"subsource"})
    candidates.sort(key=lambda x:int(x.get("score") or 0),reverse=True)
    if cached_candidate:candidates.insert(0,cached_candidate)
    if not candidates:raise RuntimeError("No %s subtitle found"%language_name)
    return candidates[:20],ident

def download_subsource_candidate(api_key, candidate, ident):
    if candidate.get("cached_path"):
        return {"path":candidate["cached_path"],"cached":True,"identity":ident,"release":candidate.get("name") or "Cached subtitle"}
    api_key=str(api_key or "").strip()
    if not api_key:raise RuntimeError("SubSource API key is not configured")
    sid=str(candidate.get("subtitle_id") or "").strip()
    if not sid:raise RuntimeError("SubSource subtitle id is missing")
    req=Request(SUBSOURCE_BASE+"/subtitles/"+sid+"/download",headers={
        "X-API-Key":api_key,"Accept":"*/*","User-Agent":"UltraStalker/%s"%PLUGIN_VERSION
    })
    with urlopen(req,timeout=20) as res:data=res.read(8*1024*1024+1)
    if len(data)>8*1024*1024:raise RuntimeError("subtitle download too large")
    root=_ensure_cache();base=os.path.join(root,_cache_key(ident))
    if data[:2]==b"PK":
        zpath=base+".zip"
        with open(zpath,"wb") as h:h.write(data)
        try:
            with zipfile.ZipFile(zpath,"r") as z:
                files=[n for n in z.namelist() if os.path.splitext(n)[1].lower() in (".srt",".ass",".ssa",".vtt")]
                if not files:raise RuntimeError("subtitle archive has no supported file")
                def fscore(name):
                    low=name.lower();score=10 if low.endswith(".srt") else 0
                    ep=_safe_int(ident.get("episode"))
                    if ep and re.search(r"(?:e|ep|episode)[ ._-]*0*%d(?:\D|$)"%ep,low):score+=50
                    if ep and re.search(r"(?:^|[^0-9])0*%d(?:[^0-9]|$)"%ep,low):score+=20
                    return score
                files.sort(key=fscore,reverse=True);raw=z.read(files[0]);ext=os.path.splitext(files[0])[1].lower()
        finally:
            try:os.unlink(zpath)
            except Exception:pass
    else:
        raw=data;ext=".srt"
    target=base+ext
    with open(target,"wb") as h:h.write(_normalize_srt_bytes(raw) if ext in (".srt",".vtt") else raw)
    try:os.chmod(target,0o600)
    except Exception:pass
    return {"path":target,"cached":False,"identity":ident,"release":candidate.get("name") or "SubSource subtitle"}

def search_and_download_arabic(api_key, item, media_type, display_name=""):
    api_key=str(api_key or "").strip()
    if not api_key:
        raise RuntimeError("SubDL API key is not configured")

    cached, ident = cached_subtitle(item, media_type, display_name)
    if cached:
        return {"path":cached, "cached":True, "identity":ident, "release":"Cached Arabic"}

    params={"api_key":api_key, "type":ident["type"], "languages":"AR",
            "subs_per_page":"20", "releases":"1", "unpack":"1", "client":"custom_integration"}
    if ident["tmdb_id"]:
        params["tmdb_id"]=ident["tmdb_id"]
    elif ident["imdb_id"]:
        params["imdb_id"]=ident["imdb_id"]
    elif ident["title"]:
        params["film_name"]=ident["title"]
        if ident["year"]: params["year"]=str(ident["year"])
    else:
        raise RuntimeError("No title or TMDB/IMDb id is available for subtitle search")
    if ident["type"]=="tv":
        if ident["season"]: params["season_number"]=str(ident["season"])
        if ident["episode"]: params["episode_number"]=str(ident["episode"])

    url=SUBDL_SEARCH+"?"+urlencode(params)
    req=Request(url,headers={"Accept":"application/json","User-Agent":"UltraStalker/%s" % PLUGIN_VERSION})
    with urlopen(req,timeout=15) as res:
        payload=json.loads(res.read(2*1024*1024).decode("utf-8","replace"))
    if not isinstance(payload,dict) or not payload.get("status"):
        raise RuntimeError(str((payload or {}).get("error") or "SubDL search failed"))

    subtitles=payload.get("subtitles") or []
    if not subtitles:
        raise RuntimeError("No Arabic subtitle found")

    # Prefer exact per-file episode downloads, then a normal archive.
    candidates=[]
    for sub in subtitles:
        if not isinstance(sub,dict): continue
        unpack=sub.get("unpack_files") or []
        for f in unpack:
            if not isinstance(f,dict): continue
            fseason=_safe_int(f.get("season")); fepisode=_safe_int(f.get("episode"))
            if ident["type"]=="tv":
                if ident["season"] and fseason and fseason!=ident["season"]: continue
                if ident["episode"] and fepisode and fepisode!=ident["episode"]: continue
            lang=str(f.get("language") or "").upper()
            if lang and lang not in ("AR","ARA","ARABIC"): continue
            fmt=str(f.get("format") or "").lower()
            score=100
            if ident["episode"] and fepisode==ident["episode"]: score+=50
            if ident["season"] and fseason==ident["season"]: score+=20
            if fmt=="srt": score+=10
            candidates.append((score,str(f.get("url") or ""),str(f.get("name") or f.get("release_name") or "Arabic"),fmt,False))
        surl=str(sub.get("url") or "")
        if surl:
            score=20
            if str(sub.get("language") or "").upper() in ("AR","ARA","ARABIC"): score+=20
            if ident["episode"] and _safe_int(sub.get("episode"))==ident["episode"]: score+=30
            candidates.append((score,surl,str(sub.get("release_name") or sub.get("name") or "Arabic"),"",True))

    candidates=[c for c in candidates if c[1]]
    if not candidates:
        raise RuntimeError("SubDL returned no downloadable Arabic subtitle")
    candidates.sort(key=lambda c:c[0],reverse=True)

    root=_ensure_cache(); base=os.path.join(root,_cache_key(ident))
    last_error=None
    for _score,url,name,fmt,is_archive in candidates[:8]:
        try:
            data=_download(url)
            if is_archive or data[:2]==b"PK":
                zpath=base+".zip"
                with open(zpath,"wb") as h:h.write(data)
                try:
                    with zipfile.ZipFile(zpath,"r") as z:
                        files=[n for n in z.namelist() if os.path.splitext(n)[1].lower() in (".srt",".ass",".ssa",".vtt")]
                        if not files: raise RuntimeError("subtitle archive has no supported file")
                        # Prefer an episode-number filename for TV, then SRT.
                        def fscore(n):
                            low=n.lower(); s=0
                            if low.endswith(".srt"):s+=10
                            if ident["episode"] and re.search(r"(?:e|ep|episode)[ ._-]*0*%d(?:\D|$)"%ident["episode"],low):s+=30
                            return s
                        files.sort(key=fscore,reverse=True)
                        raw=z.read(files[0]); ext=os.path.splitext(files[0])[1].lower()
                finally:
                    try:os.unlink(zpath)
                    except Exception:pass
            else:
                raw=data; ext=("."+fmt.lower()) if fmt else os.path.splitext(url.split("?",1)[0])[1].lower()
                if ext not in (".srt",".ass",".ssa",".vtt"): ext=".srt"
            target=base+ext
            with open(target,"wb") as h:
                h.write(_normalize_srt_bytes(raw) if ext in (".srt",".vtt") else raw)
            try: os.chmod(target,0o600)
            except Exception: pass
            return {"path":target,"cached":False,"identity":ident,"release":name}
        except Exception as exc:
            last_error=exc
    raise RuntimeError("Subtitle download failed: %s"%(last_error or "unknown error"))

def parse_srt(path):
    with open(path,"rb") as h:
        text=_decode_text(h.read(8*1024*1024))
    text=text.replace("\r\n","\n").replace("\r","\n")
    cues=[]
    ext=os.path.splitext(path)[1].lower()
    if ext in (".ass",".ssa"):
        # ASS Dialogue: Marked,Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        atime=re.compile(r"(\d+):(\d{2}):(\d{2})[.](\d{1,2})")
        for line in text.split("\n"):
            if not line.lstrip().lower().startswith("dialogue:"):
                continue
            parts=line.split(",",9)
            if len(parts)<10: continue
            sm=atime.search(parts[1]); em=atime.search(parts[2])
            if not sm or not em: continue
            sv=[int(x) for x in sm.groups()]; ev=[int(x) for x in em.groups()]
            start=(sv[0]*3600+sv[1]*60+sv[2])*1000+sv[3]*10
            end=(ev[0]*3600+ev[1]*60+ev[2])*1000+ev[3]*10
            body=parts[9].replace("\\N","\n").replace("\\n","\n")
            body=re.sub(r"\{[^}]*\}","",body)
            body=re.sub(r"<[^>]+>","",body).strip()
            if body and end>start:cues.append((start,end,body[:500]))
        return cues

    timing=re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
    for block in re.split(r"\n\s*\n",text):
        lines=[x.strip("\ufeff ") for x in block.split("\n") if x.strip()]
        if not lines: continue
        ti=-1; m=None
        for idx,line in enumerate(lines[:3]):
            m=timing.search(line)
            if m: ti=idx; break
        if not m: continue
        vals=[int(x) for x in m.groups()]
        # Millisecond fields may be 1-3 digits; normalize to ms.
        a_ms=int(str(vals[3]).ljust(3,"0")[:3]); b_ms=int(str(vals[7]).ljust(3,"0")[:3])
        start=((vals[0]*3600+vals[1]*60+vals[2])*1000+a_ms)
        end=((vals[4]*3600+vals[5]*60+vals[6])*1000+b_ms)
        body="\n".join(lines[ti+1:])
        body=body.replace("\\N","\n").replace("\\n","\n")
        body=re.sub(r"\{\\[^}]*\}","",body)
        body=re.sub(r"\{[^}]*\}","",body)
        body=re.sub(r"<[^>]+>","",body).strip()
        if body and end>start:
            cues.append((start,end,body[:500]))
    return cues
