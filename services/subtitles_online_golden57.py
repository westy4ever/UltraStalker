# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import hashlib
import json
import os
import re
import zipfile
try:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:
    from urllib import urlencode
    from urllib2 import Request, urlopen

SUBDL_SEARCH = "https://api.subdl.com/api/v1/subtitles"
SUBDL_DOWNLOAD = "https://dl.subdl.com"
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
    req=Request(str(url), headers={"User-Agent":"UltraStalker/10.0", "Accept":"*/*"})
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


def search_arabic(api_key, item, media_type, display_name=""):
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

    req=Request(SUBDL_SEARCH+"?"+urlencode(params),headers={"Accept":"application/json","User-Agent":"UltraStalker/10.0"})
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
    req=Request(url,headers={"Accept":"application/json","User-Agent":"UltraStalker/10.0"})
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
