# -*- coding: utf-8 -*-
"""M3U compatibility adapter for Ultra Stalker.

The rest of Ultra Stalker sees the same client contract it expects from a
Stalker portal. This module is intentionally isolated: UI/player/cache code does
not need to know where the catalogue came from.
"""
import concurrent.futures
import hashlib
import json
import os
import pickle
import re
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .persistent_cache import ROOT as _PERSISTENT_ROOT, hdd_ready, ensure_persistent_dirs
from .securefs import secure_private_dir
from .netsec import provider_urlopen
from .log import diagnostic_failure

try:
    from .title_clean import clean_title as _portal_clean_title
except Exception:
    def _portal_clean_title(value):
        return str(value or "").strip()

try:
    from .log import LOG
except Exception:
    import logging
    LOG=logging.getLogger("UltraStalker.M3U")

_CACHE_ROOT=os.path.join(_PERSISTENT_ROOT,"m3u")
_MEM_LOCK=threading.RLock()
_SERIES_PERSIST_EXECUTOR=concurrent.futures.ThreadPoolExecutor(max_workers=1)

def _ensure_cache_root():
    """Return True only when the canonical persistent HDD cache is safe to write."""
    try:
        if not hdd_ready() or not ensure_persistent_dirs(_CACHE_ROOT):
            return False
        secure_private_dir(_CACHE_ROOT,0o700)
        return True
    except (OSError,ValueError) as exc:
        LOG.warning("M3U cache root unavailable: %s",exc)
        return False

def _unique_tmp(path):
    return "%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())

def _private_replace(tmp,path):
    os.chmod(tmp,0o600)
    os.replace(tmp,path)
    os.chmod(path,0o600)

def _prepare_private_read(path):
    """Harden legacy cache permissions and refuse symlink/non-regular cache files."""
    try:
        if not os.path.isdir(_CACHE_ROOT):
            return False
        secure_private_dir(_CACHE_ROOT,0o700)
        st=os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            LOG.warning("Refusing unsafe M3U cache file: %s",os.path.basename(path))
            return False
        os.chmod(path,0o600)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        LOG.warning("M3U cache permission hardening failed for %s: %s",os.path.basename(path),exc)
        return False

_MODE_HINTS={}

class _SafeCacheUnpickler(pickle.Unpickler):
    """Unpickle only plain built-in container/scalar data from plugin-owned caches."""
    def find_class(self, module, name):
        raise pickle.UnpicklingError("global objects are not allowed in UltraStalker cache files")

def _safe_pickle_load(file_obj):
    return _SafeCacheUnpickler(file_obj).load()

_SERIES_GROUP_WORDS=("series","tv series","shows","show","مسلسل","مسلسلات","دراما")
_MOVIE_GROUP_WORDS=("movie","movies","vod","film","films","cinema","افلام","أفلام","فيلم")
_LIVE_GROUP_WORDS=("live","channel","channels","قنوات","قناة","sports","news","bein","osn")

_EP_PATTERNS=(
    re.compile(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b"),
    re.compile(r"(?i)\b(\d{1,2})x(\d{1,3})\b"),
    re.compile(r"(?i)\bseason[\s._-]*(\d{1,2}).{0,12}episode[\s._-]*(\d{1,3})\b"),
)
_CLEAN_EP_PATTERNS=(
    re.compile(r"(?i)[\s._-]*\bS\d{1,2}\s*E\d{1,3}\b.*$"),
    re.compile(r"(?i)[\s._-]*\b\d{1,2}x\d{1,3}\b.*$"),
    re.compile(r"(?i)[\s._-]*\bseason[\s._-]*\d{1,2}.{0,12}episode[\s._-]*\d{1,3}\b.*$"),
)

def looks_like_m3u_url(value):
    s=str(value or "").strip()
    if not s:return False
    low=s.lower()
    parsed=urllib.parse.urlsplit(s)
    path=(parsed.path or "").lower()
    query=(parsed.query or "").lower()
    if path.endswith((".m3u",".m3u8")):return True
    if "type=m3u" in query or "output=m3u" in query:return True
    if "get.php" in path and ("username=" in query or "password=" in query):return True
    if low.startswith(("m3u://","m3u8://")):return True
    return False

def _slug_id(*parts):
    raw="|".join(str(x or "") for x in parts)
    return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()[:20]

def _attr_map(info):
    attrs={}
    for key,val1,val2 in re.findall(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')',info or ""):
        attrs[key.lower()]=val1 if val1!="" else val2
    return attrs

def _episode_numbers(name):
    text=str(name or "")
    for rx in _EP_PATTERNS:
        m=rx.search(text)
        if m:
            try:return int(m.group(1)),int(m.group(2))
            except Exception:return None
    return None

def _series_base(name):
    text=str(name or "").strip()
    for rx in _CLEAN_EP_PATTERNS:
        cleaned=rx.sub("",text).strip(" ._-|")
        if cleaned and cleaned!=text:return cleaned
    return text

def _kind(group,name,url):
    g=str(group or "").casefold()
    n=str(name or "").casefold()
    if _episode_numbers(name):return "series"
    if any(w.casefold() in g for w in _SERIES_GROUP_WORDS):return "series"
    if any(w.casefold() in g for w in _MOVIE_GROUP_WORDS):return "vod"
    if any(w.casefold() in g for w in _LIVE_GROUP_WORDS):return "itv"
    path=urllib.parse.urlsplit(str(url or "")).path.lower()
    if path.endswith((".mp4",".mkv",".avi",".mov",".wmv",".m4v")):return "vod"
    return "itv"

class M3UClient:
    PAGE_SIZE=50

    def __init__(self,url,mac="",timeout=10,**kwargs):
        self.url=str(url or "").strip()
        self.mac=str(mac or "M3U")
        # Stalker APIs are small JSON calls; M3U playlists can be tens of MB.
        # Keep the caller's timeout as a floor but give playlist transfers a
        # realistic read window so large providers are not falsely marked offline.
        self.timeout=max(15,int(timeout or 10))
        self.full_timeout=max(45,self.timeout)
        self.probe_timeout=max(12,min(self.full_timeout,20))
        self.token="m3u"
        self.endpoint=self.url
        self.security_warning=""
        self._loaded=False
        self._loaded_at=0.0
        self._lock=threading.RLock()
        self._items={"itv":[],"vod":[],"series":[]}
        self._genres={"itv":[],"vod":[],"series":[]}
        self._series_index={}
        self._last_error=""
        self._last_success=0.0
        with _MEM_LOCK:
            self._mode=str(_MODE_HINTS.get(hashlib.sha1(self.url.encode("utf-8","ignore")).hexdigest()) or "m3u")
        self._xtream=self._parse_xtream_credentials()
        if self._mode=="m3u":
            persisted=self._read_mode_hint()
            if persisted:
                self._mode=persisted
                with _MEM_LOCK:_MODE_HINTS[self._cache_key()]=persisted
        self._xtream_account={}
        self._xtream_categories={"itv":{},"vod":{},"series":{}}
        self._xtream_category_ids={"itv":{},"vod":{},"series":{}}
        self._xtream_loaded_categories={"itv":set(),"vod":set(),"series":set()}
        # Per-session rich info cache. Xtream list endpoints often omit cover/backdrop
        # while get_series_info/get_vod_info carries the authoritative provider art.
        self._xtream_info_cache={}
        self._xtream_live_art_index=None
        self._xtream_series_name_index=None
        self._cache_stale=False
        # beta58: one full catalogue write can be several MB. Focus-prefetch used
        # to enqueue one complete JSON+pickle write per series, saturating the HDD
        # long after the user left M3U. Coalesce bursts into at most one follow-up.
        self._series_persist_lock=threading.RLock()
        self._series_persist_dirty=False
        self._series_persist_running=False

    def close(self):
        return None

    def reset_failure_backoff(self):
        return None

    def _cache_key(self):
        return hashlib.sha1(self.url.encode("utf-8","ignore")).hexdigest()

    def _cache_path(self):
        return os.path.join(_CACHE_ROOT,self._cache_key()+".json")

    def _fast_cache_path(self):
        return os.path.join(_CACHE_ROOT,self._cache_key()+".pkl")

    def _mode_path(self):
        return os.path.join(_CACHE_ROOT,self._cache_key()+".mode")

    def _read_mode_hint(self):
        try:
            path=self._mode_path()
            if os.path.isfile(path) and _prepare_private_read(path):
                with open(path,"r",encoding="utf-8") as f:
                    mode=str(f.read() or "").strip().lower()
                if mode in ("m3u","xtream"):
                    return mode

            # Migration from beta14: the old catalogue JSON already stored mode,
            # but there was no dedicated .mode file yet. Read only the marker,
            # then persist it in the new fast path.
            cache=self._cache_path()
            if os.path.isfile(cache) and _prepare_private_read(cache):
                with open(cache,"r",encoding="utf-8") as f:
                    data=json.load(f)
                mode=str(data.get("mode") or "").strip().lower() if isinstance(data,dict) else ""
                if mode in ("m3u","xtream"):
                    self._write_mode_hint(mode)
                    return mode
            return ""
        except (OSError,UnicodeError,json.JSONDecodeError,TypeError,ValueError) as exc:
            LOG.debug("M3U mode hint read failed: %s",exc)
            return ""

    def _write_mode_hint(self, mode):
        mode=str(mode or "").strip().lower()
        if mode not in ("m3u","xtream"):return
        tmp=""
        try:
            if not _ensure_cache_root():return
            path=self._mode_path();tmp=_unique_tmp(path)
            with open(tmp,"w",encoding="utf-8") as f:
                f.write(mode+"\n")
                f.flush();os.fsync(f.fileno())
            _private_replace(tmp,path)
        except (OSError,UnicodeError) as exc:
            LOG.warning("M3U mode hint write failed: %s",exc)
            try:
                if tmp and os.path.exists(tmp):os.unlink(tmp)
            except OSError:
                pass

    def _read_persistent(self):
        json_path=self._cache_path()
        fast_path=self._fast_cache_path()
        try:
            data=None
            source_path=""

            # Internal hot cache: same payload as JSON, much faster to decode on
            # low-power Enigma2 receivers with very large Xtream catalogues.
            if os.path.isfile(fast_path) and _prepare_private_read(fast_path):
                try:
                    with open(fast_path,"rb") as f:
                        candidate=_safe_pickle_load(f)
                    if isinstance(candidate,dict) and candidate.get("_fast_schema")==1:
                        data=candidate
                        source_path=fast_path
                except (OSError,pickle.PickleError,EOFError,TypeError,ValueError) as exc:
                    LOG.debug("M3U fast cache read failed: %s",exc)
                    data=None

            # Backward-compatible beta17 migration.
            if data is None:
                if not os.path.isfile(json_path) or not _prepare_private_read(json_path):return False
                with open(json_path,"r",encoding="utf-8") as f:
                    data=json.load(f)
                source_path=json_path
                if isinstance(data,dict):
                    try:self._write_fast_cache(data)
                    except Exception as exc: diagnostic_failure("m3u.failsoft", exc)

            if not isinstance(data,dict):return False
            cached_mode=str(data.get("mode") or self._mode or "m3u").lower()
            # beta24: M3U rows gained Xtream stream identity markers used for
            # provider picons/posters/backdrops. Old HDD catalogue rows cannot be
            # safely upgraded without their original stream URLs being re-parsed,
            # so rebuild this catalogue once while leaving artwork caches intact.
            try:catalog_schema=int(data.get("catalog_schema") or 0)
            except (TypeError, ValueError):catalog_schema=0
            if self._xtream and cached_mode=="m3u" and catalog_schema < 2:
                LOG.info("M3U catalogue schema upgrade required (%s -> 2)",catalog_schema)
                return False
            ttl=(24*3600 if cached_mode=="xtream" else 6*3600)
            age=max(0,time.time()-os.path.getmtime(source_path))
            self._cache_stale=bool(age>ttl)
            self._items=data.get("items") or self._items
            self._genres=data.get("genres") or self._genres
            self._series_index=data.get("series_index") or {}
            self._xtream_categories=data.get("xtream_categories") or self._xtream_categories
            self._xtream_category_ids=data.get("xtream_category_ids") or {
                k:{name:cid for cid,name in (self._xtream_categories.get(k,{}) or {}).items()}
                for k in ("itv","vod","series")
            }
            self._mode=str(data.get("mode") or self._mode or "m3u")
            self._xtream_account=data.get("xtream_account") if isinstance(data.get("xtream_account"),dict) else self._xtream_account
            self._loaded=True;self._loaded_at=time.time()
            return True
        except Exception as exc:
            LOG.warning("M3U persistent cache read failed: %s",exc)
            return False

    def _write_fast_cache(self, payload):
        if not isinstance(payload,dict):return False
        tmp=""
        try:
            if not _ensure_cache_root():return False
            path=self._fast_cache_path();tmp=_unique_tmp(path)
            data=dict(payload);data["_fast_schema"]=1
            with open(tmp,"wb") as f:
                pickle.dump(data,f,protocol=pickle.HIGHEST_PROTOCOL)
                f.flush();os.fsync(f.fileno())
            _private_replace(tmp,path)
            return True
        except (OSError,pickle.PickleError,TypeError,ValueError) as exc:
            LOG.warning("M3U fast cache write failed: %s",exc)
            try:
                if tmp and os.path.exists(tmp):os.unlink(tmp)
            except OSError:
                pass
            return False

    def _write_persistent(self):
        tmp=""
        try:
            if not _ensure_cache_root():return False
            path=self._cache_path();tmp=_unique_tmp(path)
            payload={"catalog_schema":2,"items":self._items,"genres":self._genres,"series_index":self._series_index,
                     "mode":self._mode,"xtream_account":self._xtream_account,
                     "xtream_categories":self._xtream_categories,
                     "xtream_category_ids":self._xtream_category_ids}
            with open(tmp,"w",encoding="utf-8") as f:
                json.dump(payload,f,ensure_ascii=False,separators=(",",":"))
                f.flush();os.fsync(f.fileno())
            _private_replace(tmp,path)
            self._write_fast_cache(payload)
            return True
        except (OSError,UnicodeError,TypeError,ValueError) as exc:
            LOG.warning("M3U cache write failed: %s",exc)
            try:
                if tmp and os.path.exists(tmp):os.unlink(tmp)
            except OSError:
                pass
            return False

    def _series_persist_worker(self):
        while True:
            with self._series_persist_lock:
                if not self._series_persist_dirty:
                    self._series_persist_running=False
                    return
                self._series_persist_dirty=False
            self._write_persistent()

    def _schedule_series_persist(self):
        with self._series_persist_lock:
            self._series_persist_dirty=True
            if self._series_persist_running:
                return False
            self._series_persist_running=True
        try:
            _SERIES_PERSIST_EXECUTOR.submit(self._series_persist_worker)
            return True
        except Exception:
            with self._series_persist_lock:
                self._series_persist_running=False
            raise

    def _parse_xtream_credentials(self):
        """Extract Xtream-style credentials from a get.php URL without logging secrets."""
        try:
            parsed=urllib.parse.urlsplit(self._normalized_url())
            query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
            username=str((query.get("username") or [""])[0] or "")
            password=str((query.get("password") or [""])[0] or "")
            if not username or not password:
                return {}
            path=parsed.path or "/"
            directory=path.rsplit("/",1)[0] if "/" in path else ""
            base=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,directory.rstrip("/"),"","")).rstrip("/")
            if not base:
                base="%s://%s"%(parsed.scheme,parsed.netloc)
            return {"base":base,"username":username,"password":password}
        except Exception:
            return {}

    def _xtream_stream_identity(self, url):
        """Return (kind, stream_id) for a stream URL belonging to this Xtream account.

        This never returns or logs credentials. It is used only to enrich M3U rows
        with metadata from player_api.php while preserving the playlist routing.
        """
        if not self._xtream:
            return ("", "")
        try:
            parts=urllib.parse.urlsplit(str(url or "").strip())
            base=urllib.parse.urlsplit(self._xtream.get("base", ""))
            if not parts.hostname or parts.hostname.lower() != (base.hostname or "").lower():
                return ("", "")
            seg=[urllib.parse.unquote(x) for x in (parts.path or "").split("/") if x]
            if len(seg) < 2:
                return ("", "")
            kind=str(seg[-4] if len(seg)>=4 else "").lower()
            if kind not in ("live","movie","series"):
                # Some panels omit the live/ prefix but still use /user/pass/id.ext.
                kind=""
            tail=seg[-1].rsplit(".",1)[0].strip()
            if not tail or not tail.isdigit():
                return ("", "")
            return ({"live":"itv","movie":"vod","series":"series"}.get(kind,kind), tail)
        except Exception:
            return ("", "")

    @staticmethod
    def _live_match_key(value):
        """Conservative channel-name key used only as an artwork fallback.

        Xtream panels sometimes expose a proxy id in get.php while player_api
        returns a different stream_id.  In that case the same channel is still
        identifiable by epg_channel_id or its display name.  Keep Unicode
        letters/digits so Arabic channel names remain matchable.
        """
        text=str(value or "").casefold().strip()
        if not text:return ""
        # Remove common quality/transport decorations without changing the
        # actual channel identity (OSN Alfa Cinema 1, beIN Sports 2, ...).
        text=re.sub(r"(?i)\b(?:uhd|fhd|full[ ._-]?hd|hd|sd|4k|2160p?|1080p?|720p?|576p?|480p?|hevc|h265|h264)\b"," ",text)
        text=re.sub(r"[\[\]{}()|:_./\\-]+"," ",text)
        return " ".join(text.split())

    def _ensure_xtream_live_art_index(self, cancel_event=None):
        if isinstance(self._xtream_live_art_index,dict):
            return self._xtream_live_art_index
        index={"sid":{},"epg":{},"name":{}}
        if not self._xtream:
            self._xtream_live_art_index=index; return index
        try:
            rows=self._xtream_request("get_live_streams",timeout=self.full_timeout,cancel_event=cancel_event)
            for row in rows if isinstance(rows,list) else []:
                if not isinstance(row,dict):continue
                sid=str(row.get("stream_id") or "").strip()
                epg=str(row.get("epg_channel_id") or row.get("tvg_id") or "").strip().casefold()
                name=self._live_match_key(row.get("name") or row.get("title") or "")
                if sid:index["sid"][sid]=row
                if epg:index["epg"].setdefault(epg,[]).append(row)
                if name:index["name"].setdefault(name,[]).append(row)
        except Exception as exc:
            LOG.warning("Xtream live artwork index failed: %s",exc)
        self._xtream_live_art_index=index
        return index

    def _enrich_m3u_live_rows(self, rows, cancel_event=None):
        if not self._xtream or not isinstance(rows,list):
            return rows
        index=self._ensure_xtream_live_art_index(cancel_event)
        sid_index=index.get("sid",{}) if isinstance(index,dict) else {}
        epg_index=index.get("epg",{}) if isinstance(index,dict) else {}
        name_index=index.get("name",{}) if isinstance(index,dict) else {}
        matched=0
        for item in rows:
            if not isinstance(item,dict):continue
            src=sid_index.get(str(item.get("stream_id") or "").strip()) or {}
            match_by="stream_id" if src else ""
            if not src:
                epg=str(item.get("tvg_id") or item.get("epg_channel_id") or "").strip().casefold()
                choices=epg_index.get(epg,[]) if epg else []
                if len(choices)==1:
                    src=choices[0];match_by="epg_channel_id"
            if not src:
                key=self._live_match_key(item.get("name") or item.get("title") or "")
                choices=name_index.get(key,[]) if key else []
                # Name fallback is deliberately uniqueness-gated to avoid ever
                # attaching another channel's logo when providers duplicate names.
                if len(choices)==1:
                    src=choices[0];match_by="channel_name"
            icon=str(src.get("stream_icon") or src.get("logo") or src.get("icon") or "").strip() if isinstance(src,dict) else ""
            if icon:
                for key in ("logo","logo_url","picon","stream_icon"):item[key]=icon
                if src.get("stream_id") is not None:item["_xtream_stream_id"]=str(src.get("stream_id"))
                item["_xtream"]=True;item["_xtream_kind"]="itv";item["_art_base"]=self._xtream.get("base","")
                matched+=1
                try:LOG.info("M3U live picon matched channel=%r by=%s stream_icon=%r",str(item.get("name") or ""),match_by,icon)
                except Exception as exc: diagnostic_failure("m3u.failsoft", exc)
            else:
                try:LOG.warning("M3U live picon no Xtream match channel=%r stream_id=%r tvg_id=%r",str(item.get("name") or ""),str(item.get("stream_id") or ""),str(item.get("tvg_id") or ""))
                except Exception as exc: diagnostic_failure("m3u.failsoft", exc)
        try:LOG.info("M3U live Xtream artwork enrichment matched=%d total=%d",matched,len(rows))
        except Exception as exc: diagnostic_failure("m3u.failsoft", exc)
        return rows

    def _ensure_xtream_series_name_index(self, cancel_event=None):
        if isinstance(self._xtream_series_name_index,dict):
            return self._xtream_series_name_index
        index={}
        if self._xtream:
            try:
                rows=self._xtream_request("get_series",timeout=self.full_timeout,cancel_event=cancel_event)
                for row in rows if isinstance(rows,list) else []:
                    if not isinstance(row,dict):continue
                    name=_portal_clean_title(row.get("name") or "").casefold().strip()
                    if name and name not in index:index[name]=row
            except Exception as exc:
                LOG.warning("Xtream series artwork index failed: %s",exc)
        self._xtream_series_name_index=index
        return index

    def _xtream_url(self, action=None, extra=None):
        if not self._xtream:
            return ""
        params={
            "username":self._xtream["username"],
            "password":self._xtream["password"],
        }
        if action:
            params["action"]=str(action)
        for key,value in (extra or {}).items():
            if value is not None:
                params[str(key)]=str(value)
        return self._xtream["base"]+"/player_api.php?"+urllib.parse.urlencode(params)

    def _xtream_request(self, action=None, extra=None, timeout=None, cancel_event=None):
        if not self._xtream:
            raise ValueError("Xtream credentials are not available in this M3U URL.")
        if cancel_event is not None and cancel_event.is_set():
            return None

        url=self._xtream_url(action,extra)
        last_exc=None
        for browser in (False,True):
            try:
                headers=self._request_headers(browser=browser,range_prefix=False)
                headers["Accept"]="application/json,text/plain,*/*"
                req=urllib.request.Request(url,headers=headers)
                with provider_urlopen(req,timeout=max(5,int(timeout or self.full_timeout))) as response:
                    raw=response.read(32*1024*1024)
                    encoding=str(response.info().get("Content-Encoding") or "")
                if cancel_event is not None and cancel_event.is_set():
                    return None
                text=self._decode_body(raw,encoding).strip()
                data=json.loads(text)
                LOG.info("Xtream API action=%s bytes=%s",action or "account",len(raw))
                return data
            except Exception as exc:
                last_exc=exc
                # Retry once with alternate UA; credentials are intentionally never logged.
                continue
        raise last_exc or RuntimeError("Xtream API request failed")

    def _xtream_probe(self, cancel_event=None):
        data=self._xtream_request(timeout=max(15,self.probe_timeout),cancel_event=cancel_event)
        if not isinstance(data,dict):
            raise ValueError("Xtream API returned an invalid account response.")
        user=data.get("user_info") if isinstance(data.get("user_info"),dict) else {}
        auth=str(user.get("auth","")).strip().lower()
        status=str(user.get("status") or "").strip()
        if auth not in ("1","true") and status.lower() not in ("active","enabled"):
            raise ValueError("Xtream account was not authenticated%s."%((" ("+status+")") if status else ""))
        self._xtream_account=data
        self._mode="xtream"
        with _MEM_LOCK:
            _MODE_HINTS[self._cache_key()]="xtream"
        self._write_mode_hint("xtream")
        self._last_error=""
        self._last_success=time.time()
        return {"ok":True,"mode":"xtream","status":200}

    def _xtream_stream_url(self, kind, stream_id, extension=""):
        if not self._xtream:
            return ""
        base=self._xtream["base"]
        username=urllib.parse.quote(self._xtream["username"],safe="")
        password=urllib.parse.quote(self._xtream["password"],safe="")
        sid=str(stream_id or "")
        ext=str(extension or "").strip().lstrip(".")
        if kind=="itv":
            ext=ext or "ts"
            return "%s/live/%s/%s/%s.%s"%(base,username,password,sid,ext)
        if kind=="vod":
            ext=ext or "mp4"
            return "%s/movie/%s/%s/%s.%s"%(base,username,password,sid,ext)
        ext=ext or "mp4"
        return "%s/series/%s/%s/%s.%s"%(base,username,password,sid,ext)

    def _xtream_category_map(self, action, kind, cancel_event=None):
        rows=self._xtream_request(action,timeout=self.full_timeout,cancel_event=cancel_event)
        mapping={}
        order=[]
        for row in rows if isinstance(rows,list) else []:
            if not isinstance(row,dict):continue
            cid=str(row.get("category_id") or "")
            name=str(row.get("category_name") or row.get("name") or "Other").strip() or "Other"
            if cid:
                mapping[cid]=name
                order.append(name)
        self._xtream_categories[kind]=mapping
        return order

    def _category_rows_to_map(self, rows):
        mapping={}
        order=[]
        for row in rows if isinstance(rows,list) else []:
            if not isinstance(row,dict):continue
            cid=str(row.get("category_id") or "")
            name=str(row.get("category_name") or row.get("name") or "Other").strip() or "Other"
            if cid:
                mapping[cid]=name
                if name not in order:order.append(name)
        return mapping,order

    def _xtream_category_cache_path(self, kind, category_id):
        raw="%s|%s"%(str(kind or "itv").lower(),str(category_id or "*"))
        suffix=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
        return os.path.join(_CACHE_ROOT,"%s.cat.%s.pkl"%(self._cache_key(),suffix))

    def _read_xtream_category_cache(self, kind, category_id):
        path=self._xtream_category_cache_path(kind,category_id)
        try:
            if not os.path.isfile(path) or not _prepare_private_read(path):return None
            with open(path,"rb") as f:data=_safe_pickle_load(f)
            if not isinstance(data,dict) or int(data.get("schema") or 0)!=1:return None
            rows=data.get("rows")
            return rows if isinstance(rows,list) else None
        except (OSError,pickle.PickleError,EOFError,TypeError,ValueError) as exc:
            LOG.debug("Xtream category cache read failed %s/%s: %s",kind,category_id,exc)
            return None

    def _write_xtream_category_cache(self, kind, category_id, rows):
        if not isinstance(rows,list):return False
        tmp=""
        try:
            if not _ensure_cache_root():return False
            path=self._xtream_category_cache_path(kind,category_id)
            tmp=_unique_tmp(path)
            with open(tmp,"wb") as f:
                pickle.dump({"schema":1,"saved_at":int(time.time()),"rows":rows},
                            f,protocol=pickle.HIGHEST_PROTOCOL)
                f.flush();os.fsync(f.fileno())
            _private_replace(tmp,path)
            return True
        except (OSError,pickle.PickleError,TypeError,ValueError) as exc:
            LOG.warning("Xtream category cache write failed %s/%s: %s",kind,category_id,exc)
            try:
                if tmp and os.path.exists(tmp):os.unlink(tmp)
            except OSError:
                pass
            return False

    def _normalise_xtream_rows(self, kind, rows, category_id=""):
        kind=str(kind or "itv").lower()
        out=[]
        mapping=self._xtream_categories.get(kind,{})
        forced_cid=str(category_id or "")
        for pos,row in enumerate(rows if isinstance(rows,list) else []):
            if not isinstance(row,dict):continue
            cid=str(row.get("category_id") or forced_cid or "")
            group=mapping.get(cid) or str(row.get("category_name") or "Other")

            if kind=="itv":
                sid=row.get("stream_id")
                name=str(row.get("name") or "Channel")
                ext=str(row.get("container_extension") or "ts")
                icon=str(row.get("stream_icon") or row.get("logo") or row.get("icon") or "")
                url=self._xtream_stream_url("itv",sid,ext)
                out.append({
                    "id":"xt-live-%s"%sid,"ch_id":"xt-live-%s"%sid,
                    "stream_id":sid,"name":name,"title":name,"number":pos+1,
                    "cmd":url,"url":url,
                    "logo":icon,"logo_url":icon,"picon":icon,"stream_icon":icon,
                    "genre":group,"category":group,"category_id":group,"_xtream_category_id":cid,
                    "tvg_id":str(row.get("epg_channel_id") or ""),"epg_channel_id":str(row.get("epg_channel_id") or ""),
                    "allow_archive":row.get("tv_archive") or row.get("archive") or 0,
                    "tv_archive_duration":row.get("tv_archive_duration") or 0,
                    "_xtream":True,"_xtream_kind":"itv","_art_base":self._xtream.get("base",""),
                })
                continue

            if kind=="vod":
                sid=row.get("stream_id")
                raw_name=str(row.get("name") or "Movie")
                name=_portal_clean_title(raw_name) or raw_name
                ext=str(row.get("container_extension") or "mp4")
                cover=str(row.get("cover_big") or row.get("movie_image") or row.get("cover_tmdb") or row.get("stream_icon") or row.get("cover") or "")
                backdrop=row.get("backdrop_path") or row.get("backdrop") or ""
                if isinstance(backdrop,(list,tuple)):backdrop=backdrop[0] if backdrop else ""
                url=self._xtream_stream_url("vod",sid,ext)
                out.append({
                    "id":"xt-vod-%s"%sid,"movie_id":"xt-vod-%s"%sid,"stream_id":sid,
                    "name":name,"title":name,"_raw_name":raw_name,
                    "cmd":url,"url":url,
                    "logo":cover,"logo_url":cover,"cover":cover,"cover_url":cover,
                    "poster":cover,"poster_url":cover,
                    "backdrop":str(backdrop or ""),"backdrop_url":str(backdrop or ""),
                    "genre":group,"category":group,"category_id":group,"_xtream_category_id":cid,
                    "rating":row.get("rating") or row.get("rating_5based") or "",
                    "year":row.get("year") or row.get("releaseDate") or row.get("release_date") or "",
                    "tmdb_id":row.get("tmdb_id") or row.get("tmdb") or "",
                    "imdb_id":row.get("imdb_id") or row.get("imdb") or "",
                    "plot":row.get("plot") or row.get("description") or "",
                    "container_extension":ext,
                    "_xtream":True,"_xtream_kind":"vod","_art_base":self._xtream.get("base",""),
                })
                continue

            sid=row.get("series_id")
            raw_name=str(row.get("name") or "Series")
            name=_portal_clean_title(raw_name) or raw_name
            cover=str(row.get("cover_tmdb") or row.get("cover_big") or row.get("cover") or row.get("stream_icon") or row.get("movie_image") or "")
            backdrop=row.get("backdrop_path") or row.get("backdrop") or ""
            if isinstance(backdrop,(list,tuple)):backdrop=backdrop[0] if backdrop else ""
            item={
                "id":"xt-series-%s"%sid,"series_id":str(sid),"movie_id":"xt-series-%s"%sid,
                "name":name,"title":name,"_raw_name":raw_name,
                "genre":group,"category":group,"category_id":group,"_xtream_category_id":cid,
                "logo":cover,"cover":cover,"cover_url":cover,"poster":cover,"poster_url":cover,
                "backdrop":str(backdrop or ""),"backdrop_url":str(backdrop or ""),
                "rating":row.get("rating") or row.get("rating_5based") or "",
                "year":row.get("year") or row.get("releaseDate") or row.get("release_date") or "",
                "tmdb_id":row.get("tmdb_id") or row.get("tmdb") or "",
                "imdb_id":row.get("imdb_id") or row.get("imdb") or "",
                "plot":row.get("plot") or "",
                "_m3u_series":True,"_xtream":True,"_xtream_kind":"series","_art_base":self._xtream.get("base",""),
            }
            out.append(item)
            self._series_index.setdefault(item["id"],{"item":item,"seasons":{},"xtream_series_id":str(sid)})
        return out

    def _load_xtream(self, cancel_event=None):
        """Bootstrap Xtream with categories only. Content catalogues stay lazy."""
        if not self._xtream:
            raise ValueError("Xtream credentials unavailable.")
        if cancel_event is not None and cancel_event.is_set():return

        started=time.monotonic()
        actions={"itv":"get_live_categories","vod":"get_vod_categories","series":"get_series_categories"}
        results={}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3,thread_name_prefix="US-Xtream-Boot") as pool:
            futures={kind:pool.submit(self._xtream_request,action,None,self.full_timeout,cancel_event)
                     for kind,action in actions.items()}
            for kind,future in futures.items():
                try:results[kind]=future.result()
                except Exception as exc:
                    LOG.warning("Xtream categories failed %s: %s",kind,exc)
                    results[kind]=[]

        genres={"itv":[],"vod":[],"series":[]}
        maps={"itv":{},"vod":{},"series":{}}
        reverse={"itv":{},"vod":{},"series":{}}
        for kind in ("itv","vod","series"):
            mapping,order=self._category_rows_to_map(results.get(kind))
            maps[kind]=mapping
            genres[kind]=order
            reverse[kind]={name:cid for cid,name in mapping.items() if name}

        self._xtream_categories=maps
        self._xtream_category_ids=reverse
        self._genres=genres
        self._items={"itv":[],"vod":[],"series":[]}
        self._mode="xtream"
        with _MEM_LOCK:_MODE_HINTS[self._cache_key()]="xtream"
        self._write_mode_hint("xtream")
        self._loaded=True
        self._loaded_at=time.time()
        self._last_success=time.time()
        self._last_error=""
        LOG.info("Xtream bootstrap categories live=%s vod=%s series=%s elapsed_ms=%s",
                 len(genres["itv"]),len(genres["vod"]),len(genres["series"]),
                 int((time.monotonic()-started)*1000))

    def _xtream_category_id(self, kind, genre):
        kind=str(kind or "itv").lower()
        if genre in (None,"","*"):return ""
        text=str(genre).strip()
        if text in self._xtream_categories.get(kind,{}):
            return text
        cid=self._xtream_category_ids.get(kind,{}).get(text)
        if cid:return str(cid)
        return ""

    def _load_xtream_category(self, kind, genre="*", cancel_event=None, force=False):
        kind=str(kind or "itv").lower()
        self._ensure(cancel_event)
        cid=self._xtream_category_id(kind,genre)
        if genre not in (None,"","*") and not cid:
            LOG.error("Xtream category routing failed kind=%s requested=%r",kind,genre)
            raise ValueError("Xtream category ID could not be resolved: %s"%genre)
        cache_key=cid or "*"

        if not force:
            cached=self._read_xtream_category_cache(kind,cache_key)
            if isinstance(cached,list):
                self._xtream_loaded_categories.setdefault(kind,set()).add(cache_key)
                return [dict(x) if isinstance(x,dict) else x for x in cached]

            # beta18 migration: its main cache contains the entire catalogue.
            # Seed the selected category from that HDD/RAM data once, with no
            # network wait, then persist it in beta19's smaller category cache.
            legacy_rows=list(self._items.get(kind,[]) or [])
            if legacy_rows:
                if genre not in ("*","",None):
                    migrated=[x for x in legacy_rows
                              if str((x or {}).get("category_id") or (x or {}).get("category") or "")==str(genre)]
                else:
                    migrated=legacy_rows
                if migrated:
                    self._write_xtream_category_cache(kind,cache_key,migrated)
                    self._xtream_loaded_categories.setdefault(kind,set()).add(cache_key)
                    return [dict(x) if isinstance(x,dict) else x for x in migrated]

        action={"itv":"get_live_streams","vod":"get_vod_streams","series":"get_series"}.get(kind)
        if not action:return []
        extra={"category_id":cid} if cid else None
        started=time.monotonic()
        LOG.info("Xtream route kind=%s requested=%r category_id=%s action=%s",kind,genre,cid or "*",action)
        raw=self._xtream_request(action,extra,timeout=self.full_timeout,cancel_event=cancel_event)
        rows=self._normalise_xtream_rows(kind,raw,cid)
        self._write_xtream_category_cache(kind,cache_key,rows)
        self._xtream_loaded_categories.setdefault(kind,set()).add(cache_key)
        LOG.info("Xtream lazy category kind=%s category=%s rows=%s elapsed_ms=%s",
                 kind,cid or "*",len(rows),int((time.monotonic()-started)*1000))
        return rows

    def _request_headers(self, browser=False, range_prefix=False):
        headers={
            "User-Agent":("Mozilla/5.0 (Linux; Enigma2) AppleWebKit/537.36 Chrome/120 Safari/537.36"
                          if browser else "VLC/3.0.20 LibVLC/3.0.20"),
            "Accept":"audio/x-mpegurl,application/x-mpegurl,application/vnd.apple.mpegurl,text/plain,*/*",
            "Accept-Encoding":"gzip, deflate",
            "Connection":"close",
        }
        if range_prefix:
            headers["Range"]="bytes=0-131071"
        return headers

    def _normalized_url(self):
        url=str(self.url or "").strip()
        low=url.lower()
        if low.startswith("m3u://"):url="http://"+url[6:]
        elif low.startswith("m3u8://"):url="http://"+url[7:]
        return url

    def _is_timeout_error(self, exc):
        if isinstance(exc,(socket.timeout,TimeoutError)):return True
        reason=getattr(exc,"reason",None)
        if isinstance(reason,(socket.timeout,TimeoutError)):return True
        return "timed out" in str(exc or "").lower()

    def _open_playlist(self, timeout, browser=False, range_prefix=False):
        url=self._normalized_url()
        req=urllib.request.Request(url,headers=self._request_headers(browser,range_prefix))
        return provider_urlopen(req,timeout=max(3,int(timeout or self.timeout)))

    @staticmethod
    def _bounded_decompress(raw, wbits, max_output):
        """Decompress *raw* without allowing an attacker-controlled output blow-up."""
        limit=max(1,int(max_output or 1))
        obj=zlib.decompressobj(wbits)
        parts=[]
        total=0
        view=memoryview(raw)
        for offset in range(0,len(view),256*1024):
            pending=view[offset:offset+256*1024]
            while pending:
                room=limit-total
                if room <= 0:
                    raise ValueError("Compressed M3U exceeds the decoded size limit")
                chunk=obj.decompress(pending,room+1)
                if chunk:
                    total+=len(chunk)
                    if total > limit:
                        raise ValueError("Compressed M3U exceeds the decoded size limit")
                    parts.append(chunk)
                pending=obj.unconsumed_tail
        room=limit-total
        tail=obj.flush(room+1)
        total+=len(tail)
        if total > limit:
            raise ValueError("Compressed M3U exceeds the decoded size limit")
        if tail:parts.append(tail)
        return b"".join(parts)

    def _decode_body(self, raw, encoding="", max_output=128*1024*1024):
        encoding=str(encoding or "").lower().strip()
        if encoding=="gzip":
            try:
                raw=self._bounded_decompress(raw,16+zlib.MAX_WBITS,max_output)
            except Exception as exc:
                LOG.warning("M3U gzip decode failed: %s",exc)
                raise
        elif encoding=="deflate":
            try:
                raw=self._bounded_decompress(raw,zlib.MAX_WBITS,max_output)
            except zlib.error:
                raw=self._bounded_decompress(raw,-zlib.MAX_WBITS,max_output)
            except Exception as exc:
                LOG.warning("M3U deflate decode failed: %s",exc)
                raise
        try:return raw.decode("utf-8-sig")
        except UnicodeDecodeError:return raw.decode("latin-1","replace")

    def probe(self,cancel_event=None,allow_cache=False):
        """Validate provider, optionally using a fresh persistent catalogue cache."""
        if cancel_event is not None and cancel_event.is_set():
            return {"ok":False,"cancelled":True}

        # Opening a source should be instant when a fresh catalogue already
        # exists on HDD. Bulk health checks intentionally do not use this path.
        if allow_cache and not self._loaded and self._read_persistent():
            return {"ok":True,"mode":self._mode,"status":200,"cached":True}

        # Once this URL proved to be Xtream, never waste 20-60 seconds on the
        # known-bad get.php path again. The mode survives GUI exits and reboots.
        if self._mode=="xtream" and self._xtream:
            return self._xtream_probe(cancel_event)

        last_exc=None
        for browser in (False,True):
            if cancel_event is not None and cancel_event.is_set():
                return {"ok":False,"cancelled":True}
            try:
                attempt_timeout=self.probe_timeout + (20 if browser else 0)
                with self._open_playlist(attempt_timeout,browser=browser,range_prefix=True) as response:
                    status=int(getattr(response,"status",200) or 200)
                    final_url=str(getattr(response,"geturl",lambda:self.url)() or self.url)
                    info=response.info()
                    encoding=str(info.get("Content-Encoding") or "")
                    content_type=str(info.get("Content-Type") or "")
                    raw=response.read(128*1024)
                text=self._decode_body(raw,encoding,max_output=4*1024*1024)
                valid=text.lstrip().startswith("#EXTM3U") or "#EXTINF" in text
                LOG.info("M3U probe status=%s valid=%s bytes=%s type=%s",
                         status,valid,len(raw),content_type or "?")
                if valid:
                    self._mode="m3u"
                    with _MEM_LOCK:
                        _MODE_HINTS[self._cache_key()]="m3u"
                    self._write_mode_hint("m3u")
                    self._last_error=""
                    self._last_success=time.time()
                    return {"ok":True,"mode":"m3u","status":status,"bytes":len(raw),"content_type":content_type}
                last_exc=ValueError("Server responded, but the response was not an M3U playlist.")
            except Exception as exc:
                last_exc=exc
                LOG.warning("M3U probe attempt browser=%s failed: %s",browser,exc)

        # get.php 504/timeout is common on large lists. If this URL contains
        # Xtream credentials, test player_api.php instead of declaring offline.
        if self._xtream:
            try:
                result=self._xtream_probe(cancel_event)
                LOG.info("M3U source accepted through Xtream API fallback")
                return result
            except Exception as xt_exc:
                LOG.warning("Xtream fallback probe failed: %s",xt_exc)
                last_exc=xt_exc

        self._last_error=str(last_exc or "M3U probe failed")
        raise last_exc or RuntimeError("M3U probe failed")

    def _fetch(self,cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():return ""

        last_exc=None
        # Four bounded attempts: VLC/browser, then one timeout retry for each.
        attempts=((False,0),(True,0),(False,1),(True,1))
        for browser,retry_no in attempts:
            if cancel_event is not None and cancel_event.is_set():return ""
            try:
                timeout=self.full_timeout + (15 if retry_no else 0)
                with self._open_playlist(timeout,browser=browser,range_prefix=False) as response:
                    status=int(getattr(response,"status",200) or 200)
                    final_url=str(getattr(response,"geturl",lambda:self.url)() or self.url)
                    info=response.info()
                    content_type=str(info.get("Content-Type") or "")
                    encoding=str(info.get("Content-Encoding") or "").lower().strip()

                    chunks=[];total=0;limit=96*1024*1024
                    while total <= limit:
                        if cancel_event is not None and cancel_event.is_set():return ""
                        chunk=response.read(min(256*1024,(limit+1)-total))
                        if not chunk:break
                        chunks.append(chunk);total+=len(chunk)
                    if total > limit:
                        raise ValueError("M3U playlist exceeds the compressed/raw size limit")
                    raw=b"".join(chunks)

                text=self._decode_body(raw,encoding,max_output=128*1024*1024)
                if not text.lstrip().startswith("#EXTM3U") and "#EXTINF" not in text:
                    raise ValueError("The URL did not return a valid M3U playlist.")

                LOG.info("M3U fetch status=%s type=%s encoding=%s bytes=%s final=%s attempt=%s",
                         status,content_type or "?",encoding or "identity",len(raw),final_url,
                         "%s/%s"%("browser" if browser else "vlc",retry_no+1))
                return text
            except Exception as exc:
                last_exc=exc
                LOG.warning("M3U full fetch attempt browser=%s retry=%s failed: %s",browser,retry_no,exc)
                # Non-timeout HTTP errors can still be UA-specific, so allow the
                # alternate UA once. Repeated non-timeout errors need no 60s retry.
                if retry_no and not self._is_timeout_error(exc):
                    break

        self._last_error=str(last_exc or "M3U fetch failed")
        raise last_exc or RuntimeError("M3U fetch failed")

    def _m3u_art_base(self):
        """Return a stable origin/directory for relative artwork in M3U rows."""
        try:
            parts=urllib.parse.urlsplit(self.url)
            if parts.scheme in ("http","https") and parts.netloc:
                path=parts.path or "/"
                if not path.endswith("/"):
                    path=path.rsplit("/",1)[0]+"/" if "/" in path else "/"
                return urllib.parse.urlunsplit((parts.scheme,parts.netloc,path,"",""))
        except (TypeError, ValueError):
            LOG.debug("Invalid M3U artwork base URL", exc_info=True)
        return self.url

    def _parse(self,text):
        # Parse in one pass. Avoid splitlines() + a second full ``entries`` list,
        # which used to multiply peak RAM use on large receiver playlists.
        import io
        live=[];movies=[];series_groups={}
        genre_order={"itv":[],"vod":[],"series":[]}
        art_base=self._m3u_art_base()
        pending=None

        def add_genre(kind,group):
            if group not in genre_order[kind]:genre_order[kind].append(group)

        for raw in io.StringIO(str(text or "")):
            line=raw.strip()
            if not line:continue
            if line.startswith("#EXTINF"):
                body=line.split(":",1)[1] if ":" in line else ""
                left,sep,title=body.partition(",")
                attrs=_attr_map(left)
                pending={
                    "name":(attrs.get("tvg-name") or title or "Untitled").strip(),
                    "group":(attrs.get("group-title") or "Other").strip() or "Other",
                    "logo":(attrs.get("tvg-logo") or "").strip(),
                    "tvg_id":(attrs.get("tvg-id") or "").strip(),
                }
                continue
            if line.startswith("#"):
                continue
            if pending is None:
                pending={"name":"Stream","group":"Other","logo":"","tvg_id":""}
            e=pending;pending=None
            name=e["name"];group=e["group"];url=line;logo=e["logo"]
            kind=_kind(group,name,url)
            common={
                "name":name,"title":name,"cmd":url,"url":url,
                "logo":logo,"logo_url":logo,"picon":logo,"stream_icon":logo,
                "genre":group,"category":group,"category_id":group,
                "tvg_id":e.get("tvg_id") or "",
                "_art_base":art_base,
            }
            provider_kind,provider_sid=self._xtream_stream_identity(url)
            if provider_sid:
                common["stream_id"]=provider_sid
                common["_xtream"]=True
                common["_xtream_kind"]=provider_kind or kind
                common["_art_base"]=self._xtream.get("base","") or common.get("_art_base")
            if kind=="series":
                nums=_episode_numbers(name)
                if not nums:
                    row=dict(common);row["id"]="m3u-vod-"+_slug_id(url,name);row["movie_id"]=row["id"]
                    movies.append(row);add_genre("vod",group);continue
                season,episode=nums;base=_series_base(name) or name
                skey=(group.casefold(),base.casefold())
                sid="m3u-series-"+_slug_id(group,base)
                bucket=series_groups.setdefault(skey,{"id":sid,"name":base,"group":group,"logo":logo,"episodes":[]})
                ep=dict(common)
                ep.update({"id":"m3u-ep-"+_slug_id(url,name),"episode_id":str(episode),"episode":episode,"number":episode,"season":season,"season_id":str(season),"movie_id":sid,"series_id":sid})
                bucket["episodes"].append(ep);add_genre("series",group)
            elif kind=="vod":
                row=dict(common);row["id"]="m3u-vod-"+_slug_id(url,name);row["movie_id"]=row["id"]
                movies.append(row);add_genre("vod",group)
            else:
                row=dict(common);row["id"]="m3u-live-"+_slug_id(url,name);row["ch_id"]=row["id"];row["number"]=len(live)+1
                live.append(row);add_genre("itv",group)

        series=[]
        self._series_index={}
        for bucket in series_groups.values():
            eps=sorted(bucket["episodes"],key=lambda x:(int(x.get("season") or 0),int(x.get("episode") or 0)))
            provider_series=bool(self._xtream and any(isinstance(ep,dict) and ep.get("_xtream") for ep in eps))
            item={
                "id":bucket["id"],"series_id":bucket["id"],"movie_id":bucket["id"],
                "name":bucket["name"],"title":bucket["name"],"genre":bucket["group"],
                "category":bucket["group"],"category_id":bucket["group"],
                "logo":bucket["logo"],"cover":bucket["logo"],"cover_url":bucket["logo"],
                "_m3u_series":True,"_xtream":provider_series,"_xtream_kind":"series",
                "_art_base":(self._xtream.get("base","") if provider_series else art_base),
            }
            series.append(item)
            seasons={}
            for ep in eps:seasons.setdefault(str(ep.get("season") or 1),[]).append(ep)
            self._series_index[bucket["id"]]={"item":item,"seasons":seasons}

        self._items={"itv":live,"vod":movies,"series":series}
        self._genres=genre_order

    def _ensure(self,cancel_event=None,force=False):
        with self._lock:
            if self._loaded and not force:return
            if not force and self._read_persistent():return

            # If a prior probe already identified Xtream mode, skip get.php.
            if self._mode=="xtream" and self._xtream:
                self._load_xtream(cancel_event)
                self._write_persistent()
                return

            try:
                text=self._fetch(cancel_event)
                if not text.lstrip().startswith("#EXTM3U") and "#EXTINF" not in text:
                    raise ValueError("The URL did not return a valid M3U playlist.")
                self._parse(text)
                self._mode="m3u"
                self._loaded=True;self._loaded_at=time.time();self._last_success=time.time();self._last_error=""
                self._write_persistent()
                return
            except Exception as m3u_exc:
                LOG.warning("M3U full load failed; trying Xtream fallback: %s",m3u_exc)
                if not self._xtream:
                    self._last_error=str(m3u_exc);raise
                try:
                    self._xtream_probe(cancel_event)
                    self._load_xtream(cancel_event)
                    self._write_persistent()
                    return
                except Exception as xt_exc:
                    self._last_error=str(xt_exc)
                    raise xt_exc

    def authorize(self,cancel_event=None):
        try:
            self._ensure(cancel_event)
            self.token="m3u";return {"token":"m3u","source_type":"m3u"}
        except Exception as exc:
            self._last_error=str(exc);raise

    def health_snapshot(self):
        return {"health":"excellent" if self._loaded else "unknown","latency_ms":0,"last_error":self._last_error,"source_type":"m3u"}

    def _format_xtream_expiry(self, value):
        raw=str(value or "").strip()
        if not raw or raw in ("0","None","null","NULL"):
            return ""
        try:
            stamp=int(float(raw))
            if stamp <= 0:return ""
            return time.strftime("%B %d, %Y",time.localtime(stamp))
        except Exception:
            return raw

    def account_info(self):
        if self._mode=="xtream" and self._xtream:
            if not self._xtream_account:
                self._xtream_probe()
            user=self._xtream_account.get("user_info") if isinstance(self._xtream_account.get("user_info"),dict) else {}
            exp=self._format_xtream_expiry(user.get("exp_date"))
            return {
                "status":str(user.get("status") or "Active"),
                "account_status":str(user.get("status") or "Active"),
                "source_type":"m3u",
                "playlist_mode":"xtream",
                "expire_billing_date":exp,
                "end_date":exp,
                "playlist_items":sum(len(v) for v in self._items.values()) if self._loaded else 0,
            }
        self._ensure()
        return {"status":"Active","account_status":"Active","source_type":"m3u","playlist_mode":"m3u","playlist_items":sum(len(v) for v in self._items.values())}

    def genres(self,media_type="itv",cancel_event=None):
        self._ensure(cancel_event)
        typ=str(media_type or "itv").lower()

        # Migration safety: beta18/beta19 caches may contain visible genre names
        # but no Xtream provider category IDs. Without this, every folder falls
        # back to get_*_streams without category_id and shows the same catalogue.
        if self._mode=="xtream" and not (self._xtream_categories.get(typ) or {}):
            LOG.info("Xtream category mapping missing for %s; refreshing category bootstrap only",typ)
            self._load_xtream(cancel_event)
            self._write_persistent()

        if self._mode=="xtream":
            mapping=self._xtream_categories.get(typ,{}) or {}
            rows=[]
            for name in self._genres.get(typ,[]):
                cid=self._xtream_category_ids.get(typ,{}).get(str(name))
                if not cid:
                    for raw_id,raw_name in mapping.items():
                        if str(raw_name)==str(name):
                            cid=str(raw_id);break
                rows.append({
                    "id":str(cid or ""),
                    "genre_id":str(cid or ""),
                    "category_id":str(cid or ""),
                    "title":str(name),
                    "name":str(name),
                    "_xtream":True,
                })
            return rows

        return [{"id":g,"title":g,"name":g} for g in self._genres.get(typ,[])]

    def ordered_page(self,media_type="itv",genre="*",page=1,cancel_event=None):
        self._ensure(cancel_event)
        typ=str(media_type or "itv").lower()
        if self._mode=="xtream":
            rows=self._load_xtream_category(typ,genre,cancel_event)
        else:
            rows=list(self._items.get(typ,[]))
            if genre not in ("*","",None):
                rows=[x for x in rows if str(x.get("category_id") or x.get("category") or "")==str(genre)]
            if typ=="itv" and self._xtream:
                rows=self._enrich_m3u_live_rows(rows,cancel_event)
        page=max(1,int(page or 1));size=self.PAGE_SIZE;start=(page-1)*size
        return {"items":rows[start:start+size],"page":page,"page_size":size,"total":len(rows)}

    def ordered_list(self,media_type="itv",genre="*",page=1,cancel_event=None):
        return self.ordered_page(media_type,genre,page,cancel_event).get("items",[])

    def ordered_all(self,media_type="itv",genre="*",start_page=1,max_pages=None,max_items=None,cancel_event=None):
        self._ensure(cancel_event)
        typ=str(media_type or "itv").lower()
        if self._mode=="xtream":
            rows=self._load_xtream_category(typ,genre,cancel_event)
        else:
            rows=list(self._items.get(typ,[]))
            if genre not in ("*","",None):
                rows=[x for x in rows if str(x.get("category_id") or x.get("category") or "")==str(genre)]
            if typ=="itv" and self._xtream:
                rows=self._enrich_m3u_live_rows(rows,cancel_event)
        return rows[:int(max_items or len(rows))]

    @staticmethod
    def _first_info_value(*values):
        def useful(value):
            if value in (None,"",False):
                return False
            text=str(value).strip()
            return text.lower() not in ("0","false","none","null","n/a","na","[]","{}")
        for value in values:
            if isinstance(value,(list,tuple)):
                for part in value:
                    if useful(part):
                        return part
            elif useful(value):
                return value
        return ""

    def _merge_xtream_provider_info(self, item, payload, kind):
        """Merge rich Xtream info into a normalized catalogue row in-place."""
        if not isinstance(item,dict) or not isinstance(payload,dict):
            return item
        info=payload.get("info") if isinstance(payload.get("info"),dict) else {}
        movie_data=payload.get("movie_data") if isinstance(payload.get("movie_data"),dict) else {}
        src={}
        # Preserve useful movie_data values when `info` repeats the same field
        # with panel placeholders such as 0/false/N/A.
        for origin in (movie_data,info):
            for key,value in origin.items():
                if self._first_info_value(value):
                    src[key]=value
        if kind=="series":
            # Xstreamity-style rich-series priority: server cover_big first.
            cover=self._first_info_value(src.get("cover_big"),src.get("cover"),src.get("movie_image"),src.get("cover_tmdb"),src.get("stream_icon"))
        else:
            # Xstreamity 5.58 reference order for VOD provider artwork.
            cover=self._first_info_value(src.get("cover_big"),src.get("movie_image"),src.get("cover_tmdb"),src.get("cover"),src.get("stream_icon"))
        backdrop=self._first_info_value(src.get("backdrop_path"),src.get("backdrop"),src.get("backdrops"),src.get("background"),src.get("fanart"))
        if cover:
            cover=str(cover)
            for key in ("logo","logo_url","cover","cover_url","poster","poster_url"):
                item[key]=cover
        if backdrop:
            backdrop=str(backdrop)
            item["backdrop"]=backdrop; item["backdrop_url"]=backdrop; item["backdrop_path"]=backdrop
        # Rich get_vod_info/get_series_info is authoritative for identity and
        # metadata. List rows are frequently stale or decorated.
        mappings=(
            ("tmdb_id",("tmdb_id","tmdb")),
            ("imdb_id",("imdb_id","imdb")),
            ("year",("year","releaseDate","release_date","releasedate")),
            ("plot",("plot","description","overview")),
            ("description",("description","plot","overview")),
            ("rating",("rating","rating_5based","rating_imdb")),
            ("rating_imdb",("rating_imdb","rating")),
            ("genre",("genre","genres","category_name")),
            ("actors",("actors","cast","actor")),
            ("cast",("cast","actors","actor")),
            ("director",("director","directors")),
            ("writer",("writer","writers","creator")),
            ("duration",("duration","runtime","time")),
            ("time",("time","duration","runtime")),
            ("country",("country","country_name")),
        )
        for dst,keys in mappings:
            for key in keys:
                value=self._first_info_value(src.get(key))
                if value:
                    item[dst]=value
                    break

        # Rich provider title is a search alias only. The visible list title
        # remains untouched so users do not see random language/title changes.
        rich_name=self._first_info_value(
            src.get("name"),src.get("title"),src.get("movie_name"),
            src.get("o_name"),src.get("original_name"),src.get("original_title")
        )
        if rich_name:
            # Search hint only. Generic package labels must never outrank the
            # real catalogue title as original_title.
            item["_provider_name"]=str(rich_name)

        # Some Xtream panels put an IMDb tt-id in tmdb_id.
        raw_tid=str(item.get("tmdb_id") or "").strip()
        imdb_match=re.search(r"tt\d{5,12}",raw_tid,re.I)
        if imdb_match:
            item["imdb_id"]=imdb_match.group(0).lower()

        item["_provider_info_loaded"]=True
        return item

    def enrich_provider_artwork(self, item, media_type, cancel_event=None, force=False):
        """Fetch authoritative Xtream metadata for M3U/Xtream catalogue rows.

        ``force`` is used after a list-level artwork URL failed. This matters on
        panels where get_vod_streams/get_series carries an empty/stale icon while
        get_vod_info/get_series_info contains the working cover/backdrop.
        """
        self._ensure(cancel_event)
        if not isinstance(item,dict) or not self._xtream:
            return item
        typ=str(media_type or item.get("_xtream_kind") or "").lower()
        if typ in ("itv","live"):
            return item
        if not force and (item.get("cover") or item.get("poster") or item.get("logo")):
            return item
        sid=""
        if typ=="series":
            raw_sid=str(item.get("series_id") or "").strip()
            if raw_sid.isdigit():
                sid=raw_sid
            else:
                key=_portal_clean_title(item.get("name") or item.get("title") or "").casefold().strip()
                src=self._ensure_xtream_series_name_index(cancel_event).get(key) or {}
                sid=str(src.get("series_id") or "").strip()
                if sid:
                    item["series_id"]=sid
                    self._merge_xtream_provider_info(item,{"info":src},"series")
        else:
            sid=str(item.get("stream_id") or "").strip()
        if not sid:
            return item
        item["_xtream"]=True;item["_xtream_kind"]=("series" if typ=="series" else "vod");item["_art_base"]=self._xtream.get("base","")
        cache_key=("series" if typ=="series" else "vod",sid)
        payload=self._xtream_info_cache.get(cache_key)
        if payload is None:
            action="get_series_info" if typ=="series" else "get_vod_info"
            extra={"series_id":sid} if typ=="series" else {"vod_id":sid}
            payload=self._xtream_request(action,extra,timeout=self.full_timeout,cancel_event=cancel_event)
            if not isinstance(payload,dict): payload={}
            self._xtream_info_cache[cache_key]=payload
        return self._merge_xtream_provider_info(item,payload,"series" if typ=="series" else "vod")

    def _series_index_keys(self, series_item):
        if not isinstance(series_item,dict):return []
        keys=[]
        for value in (series_item.get("id"),series_item.get("series_id"),series_item.get("xtream_series_id"),series_item.get("stream_id")):
            text=str(value or "").strip()
            if text and text not in keys:keys.append(text)
        return keys

    def _series_index_lookup(self, series_item):
        keys=self._series_index_keys(series_item)
        for key in keys:
            data=self._series_index.get(key)
            if isinstance(data,dict):
                return key,data
        return (keys[0] if keys else ""),{}

    def prefetch_series_hierarchy(self, series_item, cancel_event=None):
        """Warm one focused M3U/Xtream series before the user presses OK."""
        if not isinstance(series_item,dict):return False
        self._ensure(cancel_event)
        if not series_item.get("_xtream"):
            return True
        self._ensure_xtream_series_info(series_item,cancel_event)
        return True

    def _ensure_xtream_series_info(self, series_item, cancel_event=None):
        self._ensure(cancel_event)
        if not isinstance(series_item,dict) or not series_item.get("_xtream"):
            return
        sid_key,data=self._series_index_lookup(series_item)
        if data.get("seasons"):
            return
        real_sid=str(data.get("xtream_series_id") or series_item.get("series_id") or "").strip()
        if not real_sid:
            # Recover a missing/decoration-changed series_id from the Xtream name index.
            key=_portal_clean_title(series_item.get("name") or series_item.get("title") or "").casefold().strip()
            try:src=self._ensure_xtream_series_name_index(cancel_event).get(key) or {}
            except Exception:src={}
            real_sid=str(src.get("series_id") or "").strip()
            if real_sid:series_item["series_id"]=real_sid
        if not real_sid:
            return
        cache_key=("series",real_sid)
        info=self._xtream_info_cache.get(cache_key)
        if info is None:
            info=self._xtream_request("get_series_info",{"series_id":real_sid},
                                      timeout=self.full_timeout,cancel_event=cancel_event)
            if not isinstance(info,dict):info={}
            self._xtream_info_cache[cache_key]=info
        self._merge_xtream_provider_info(series_item,info,"series")
        if isinstance(data.get("item"),dict):
            self._merge_xtream_provider_info(data["item"],info,"series")
        episodes_by_season={}
        episodes=(info.get("episodes") if isinstance(info,dict) else {}) or {}
        for season_no,rows in episodes.items() if isinstance(episodes,dict) else []:
            bucket=[]
            for row in rows if isinstance(rows,list) else []:
                if not isinstance(row,dict):continue
                eid=row.get("id") or row.get("episode_id")
                epnum=row.get("episode_num") or row.get("episode") or len(bucket)+1
                ext=row.get("container_extension") or "mp4"
                title=str(row.get("title") or row.get("name") or ("Episode %s"%epnum))
                url=self._xtream_stream_url("series",eid,ext)
                bucket.append({
                    "id":"xt-ep-%s"%eid,"episode_id":str(eid),"episode":int(epnum or 0),"number":int(epnum or 0),
                    "season":int(season_no or 1),"season_id":str(season_no or 1),
                    "movie_id":real_sid,"series_id":real_sid,
                    "name":title,"title":title,"cmd":url,"url":url,
                    "container_extension":ext,
                    "_xtream":True,"_xtream_kind":"series_episode",
                })
            episodes_by_season[str(season_no)]=bucket
        data=dict(data or {})
        data["seasons"]=episodes_by_season
        data["xtream_series_id"]=real_sid
        # Alias every known identity to the same hierarchy. This prevents a rich
        # provider merge or decorated list id from making one series "disappear".
        alias_keys=self._series_index_keys(series_item)
        if sid_key and sid_key not in alias_keys:alias_keys.append(sid_key)
        if real_sid not in alias_keys:alias_keys.append(real_sid)
        for key in alias_keys:
            self._series_index[key]=data
        # Do not hold the seasons callback behind a full catalogue disk write.
        try:self._schedule_series_persist()
        except Exception as exc:diagnostic_failure("m3u.series_async_persist",exc)


    def series_seasons(self,series_item,cancel_event=None):
        self._ensure(cancel_event)
        if isinstance(series_item,dict) and series_item.get("_xtream"):
            self._ensure_xtream_series_info(series_item,cancel_event)
        sid,data=self._series_index_lookup(series_item)
        out=[]
        for season_no,episodes in sorted((data.get("seasons") or {}).items(),key=lambda x:int(x[0])):
            out.append({"id":season_no,"season_id":season_no,"season":int(season_no),"name":"Season %s"%season_no,"episodes":episodes,"series":episodes,"parent_series_id":sid})
        return out

    def series_episodes(self,series_item,season_item,cancel_event=None):
        embedded=(season_item or {}).get("episodes") or (season_item or {}).get("series")
        if isinstance(embedded,list):return [dict(x) for x in embedded]
        if isinstance(series_item,dict) and series_item.get("_xtream"):
            self._ensure_xtream_series_info(series_item,cancel_event)
        sid,data=self._series_index_lookup(series_item)
        season=str((season_item or {}).get("season") or (season_item or {}).get("season_id") or "1")
        return [dict(x) for x in ((data or {}).get("seasons") or {}).get(season,[])]

    def create_link(self,item,media_type="itv",*args,**kwargs):
        if isinstance(item,dict):
            return str(item.get("url") or item.get("cmd") or item.get("command") or "")
        return str(item or "")

    def epg(self,*args,**kwargs):return []
    def full_epg(self,*args,**kwargs):return []
    def catchup_channels(self,*args,**kwargs):return []
    def catchup_programs(self,*args,**kwargs):return []
    def create_catchup_link(self,*args,**kwargs):return ""
