# -*- coding: utf-8 -*-
import os, re, json, time, threading, urllib.request, urllib.error, urllib.parse
from collections import OrderedDict
from .persistent_cache import ROOT as CACHE_ROOT, hdd_read_ready, persistent_write_gate
from .log import get_logger
from .netsec import validate_remote_media_url, build_safe_media_opener
from .storage import load_settings
from .core.download_guard import ensure_capacity, reserve_bytes, CHECK_INTERVAL_BYTES

LOG=get_logger()
ROOT=os.path.join(CACHE_ROOT,"Downloads")
STATE_FILE=os.path.join(ROOT,"downloads.json")

def _validated_download_path(path):
    """Return a canonical download target confined to the Downloads root."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("invalid download path")
    candidate=os.path.abspath(path)
    base=os.path.abspath(ROOT)
    try:
        if os.path.commonpath((base,candidate)) != base:
            raise ValueError("download path escaped Downloads root")
        # Resolve existing symlinks too, so a tampered state file cannot route
        # a download through a symlinked directory outside the managed root.
        real_base=os.path.realpath(base); real_candidate=os.path.realpath(candidate)
        if os.path.commonpath((real_base,real_candidate)) != real_base:
            raise ValueError("download path escaped Downloads root")
    except (OSError, ValueError):
        raise ValueError("download path escaped Downloads root")
    return candidate

def _mkdir(path):
    path=os.path.abspath(str(path or "")); base=os.path.abspath(CACHE_ROOT)
    try:
        if os.path.commonpath((base,path)) != base: raise OSError("download path escaped UltraStalker root")
        if not persistent_write_gate(path): raise OSError("Ultra Stalker HDD is not ready")
        return True
    except Exception as exc:
        LOG.debug("Download directory unavailable %s: %s",path,exc); return False

def _clean(text,fallback="download"):
    text=str(text or "").strip(); text=re.sub(r'[\\/:*?"<>|\x00-\x1f]+','_',text); text=re.sub(r'\s+',' ',text).strip(' ._')
    return (text or fallback)[:140]

def _atomic_json(path,data):
    if not persistent_write_gate(path): raise OSError("download HDD unavailable")
    tmp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
    if not persistent_write_gate(tmp): raise OSError("download HDD unavailable")
    try:
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        if not persistent_write_gate(path): raise OSError("download HDD disappeared before state commit")
        try: os.chmod(tmp,0o600)
        except OSError: pass
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp) and persistent_write_gate(tmp):
            try: os.unlink(tmp)
            except OSError: pass

class DownloadManager(object):
    def __init__(self):
        self.lock=threading.RLock(); self.jobs=OrderedDict(); self._resolvers={}; self._wake=threading.Event(); self._stop=threading.Event()
        self._active_response=None; self._active_response_lock=threading.RLock(); self.worker=None; self._loaded=False; self._load()
    def _ensure_worker(self):
        with self.lock:
            if self._stop.is_set(): return False
            if self.worker is not None and self.worker.is_alive(): return True
            self.worker=threading.Thread(target=self._loop,name="UltraStalkerDownloadManager",daemon=True); self.worker.start(); return True
    def shutdown(self,wait=False,timeout=2.5):
        self._stop.set(); self._wake.set()
        with self.lock:
            for row in self.jobs.values():
                if row.get("status") in ("queued","resolving","downloading"):
                    row["cancel"]=True
                    if row.get("status") != "downloading": row["status"]="paused"
            if not self._save(): LOG.warning("Download state could not be saved during shutdown")
        with self._active_response_lock:
            response=self._active_response; self._active_response=None
        if response is not None:
            try:response.close()
            except Exception:pass
        thread=self.worker
        if wait and thread is not None and thread.is_alive(): thread.join(max(0.0,float(timeout or 0.0)))
        return not bool(thread is not None and thread.is_alive())
    def _load(self):
        # Fail closed when the real HDD is not mounted. Never consume a shadow
        # /media/hdd tree from receiver flash. A failed late-mount attempt stays
        # retryable so a manager first touched before HDD mount does not remain
        # permanently empty for the rest of the Enigma2 session.
        if not hdd_read_ready(force=True):
            return False
        self._loaded=True
        try:
            with open(STATE_FILE,"r",encoding="utf-8") as f: rows=json.load(f)
            for row in rows if isinstance(rows,list) else []:
                if not isinstance(row,dict): continue
                try:
                    row["path"]=_validated_download_path(row.get("path"))
                except ValueError as exc:
                    LOG.warning("Rejected unsafe persisted download %r: %s", row.get("id"), exc)
                    continue
                if row.get("status") in ("downloading","resolving"): row["status"]="paused"
                self.jobs[str(row.get("id"))]=row
            return True
        except FileNotFoundError:
            return True
        except (OSError, ValueError, TypeError) as exc:
            LOG.debug("Download state load skipped: %s", exc); return True
    def _ensure_loaded(self):
        if not self._loaded:
            self._load()
        return self._loaded
    def _save(self):
        try:
            _atomic_json(STATE_FILE,list(self.jobs.values())); return True
        except Exception as exc:
            LOG.debug("Download state save failed: %s",exc); return False
    def snapshot(self):
        with self.lock:
            self._ensure_loaded(); return [dict(x) for x in self.jobs.values()]
    def add(self,job,resolver):
        with self.lock:
            self._ensure_loaded()
            if not hdd_read_ready(force=True): return (False,"Download HDD is unavailable")
            jid=str(job.get("id") or "")
            if not jid:return(False,"Invalid download")
            old=self.jobs.get(jid)
            if old and old.get("status") in ("queued","resolving","downloading","completed"):return(False,"Already downloaded" if old.get("status")=="completed" else "Already queued")
            row=dict(job); row.setdefault("status","queued"); row.setdefault("downloaded",0); row.setdefault("total",0); row.setdefault("created",int(time.time())); row["error"]=""
            try: row["path"]=_validated_download_path(row.get("path"))
            except ValueError: return(False,"Invalid download path")
            self.jobs[jid]=row; self._resolvers[jid]=resolver
            if not self._save():
                self.jobs.pop(jid,None); self._resolvers.pop(jid,None); return(False,"Download HDD is unavailable")
            self._ensure_worker(); self._wake.set(); return(True,"Added to downloads")
    def cancel(self,jid):
        with self.lock:
            self._ensure_loaded(); row=self.jobs.get(str(jid))
            if not row:return False
            row["cancel"]=True
            if row.get("status")=="queued":row["status"]="cancelled"
            self._save();self._wake.set();return True
    def retry(self,jid,resolver=None):
        with self.lock:
            self._ensure_loaded(); row=self.jobs.get(str(jid))
            if not row:return False
            row["status"]="queued";row["cancel"]=False;row["error"]=""
            if resolver:self._resolvers[str(jid)]=resolver
            self._save();self._ensure_worker();self._wake.set();return True
    def _next(self):
        with self.lock:
            for jid,row in self.jobs.items():
                if row.get("status")=="queued" and not row.get("cancel") and jid in self._resolvers:
                    row["status"]="resolving";self._save();return jid,row
        return None,None
    def _loop(self):
        if not _mkdir(ROOT): return
        while not self._stop.is_set():
            jid,row=self._next()
            if not row:self._wake.wait(1.0);self._wake.clear();continue
            try:self._download(jid,row)
            except Exception as exc:
                with self.lock:
                    live=self.jobs.get(jid)
                    if live and not self._stop.is_set(): live["status"]="failed";live["error"]=str(exc)[:220];self._save()
    @staticmethod
    def _content_range_total(headers):
        value=str((headers or {}).get("Content-Range") or "")
        m=re.search(r'\*/(\d+)$',value) or re.search(r'bytes\s+\d+-\d+/(\d+)$',value,re.I)
        return int(m.group(1)) if m else 0
    def _download(self,jid,row):
        resolver=self._resolvers.get(jid)
        if not resolver:raise RuntimeError("Download needs portal reconnection")
        url=resolver()
        if not isinstance(url,str) or not url.strip():raise RuntimeError("Portal returned no stream link")
        url=url.strip()
        scheme=str(urllib.parse.urlsplit(url).scheme or "").lower()
        if scheme not in ("http","https"):
            raise RuntimeError("Downloads support HTTP/HTTPS streams only")
        trusted_origin=str(row.get("portal_origin") or "").strip()
        trusted=(trusted_origin,) if trusted_origin else ()
        try:
            validate_remote_media_url(url, trusted_private_origins=trusted)
            opener=build_safe_media_opener(trusted_private_origins=trusted)
        except Exception as exc:
            raise RuntimeError("Unsafe download destination: %s" % exc)
        try: final=_validated_download_path(row.get("path"))
        except ValueError as exc: raise RuntimeError("Unsafe download path: %s" % exc)
        row["path"]=final; part=final+".part"
        if not _mkdir(os.path.dirname(final)):raise RuntimeError("Download HDD is unavailable")
        if not hdd_read_ready(force=True):raise RuntimeError("Download HDD is unavailable")
        existing=os.path.getsize(part) if os.path.isfile(part) else 0;response=None
        reserve=reserve_bytes(load_settings())
        ensure_capacity(part, reserve=reserve)
        for attempt in range(2):
            headers={"User-Agent":"Mozilla/5.0 (UltraStalker/10)","Accept":"*/*","Connection":"close"}
            if existing>0:headers["Range"]="bytes=%d-"%existing
            try:response=opener.open(urllib.request.Request(url,headers=headers),timeout=18);break
            except urllib.error.HTTPError as exc:
                if exc.code==416 and existing>0:
                    remote_total=self._content_range_total(getattr(exc,"headers",{}))
                    try:exc.close()
                    except Exception:pass
                    if remote_total and remote_total==existing:
                        if not persistent_write_gate(part, final):
                            raise RuntimeError("Download HDD disappeared before completion")
                        os.replace(part,final)
                        with self.lock:
                            live=self.jobs[jid];live["status"]="completed";live["downloaded"]=existing;live["total"]=existing;live["completed_at"]=int(time.time());self._save()
                        return
                    if persistent_write_gate(part):
                        try:os.unlink(part)
                        except OSError:pass
                    existing=0
                    if attempt==0:continue
                raise
        if response is None:raise RuntimeError("Could not open download stream")
        with self._active_response_lock:self._active_response=response
        try:
            code=getattr(response,"status",None) or response.getcode()
            if existing and int(code or 0)==200:existing=0
            mode="ab" if existing and int(code or 0)==206 else "wb";clen=int(response.headers.get("Content-Length") or 0);total=existing+clen if clen else 0
            if clen:
                ensure_capacity(part, incoming_bytes=clen, reserve=reserve)
            with self.lock:
                live=self.jobs[jid];live["status"]="downloading";live["downloaded"]=existing;live["total"]=total;live["error"]="";self._save()
            last_save=time.time();downloaded=existing;last_capacity_check=existing
            if not persistent_write_gate(part):
                raise RuntimeError("Download HDD disappeared before file write")
            with open(part,mode) as out:
                while not self._stop.is_set():
                    with self.lock:
                        if self.jobs[jid].get("cancel"):self.jobs[jid]["status"]="cancelled";self._save();return
                    chunk=response.read(256*1024)
                    if not chunk:break
                    if downloaded-last_capacity_check >= CHECK_INTERVAL_BYTES:
                        ensure_capacity(part, reserve=reserve)
                        last_capacity_check=downloaded
                    out.write(chunk);downloaded+=len(chunk)
                    if time.time()-last_save>=1.0:
                        with self.lock:self.jobs[jid]["downloaded"]=downloaded;self.jobs[jid]["total"]=total;self._save()
                        last_save=time.time()
                out.flush();os.fsync(out.fileno())
            if self._stop.is_set():
                with self.lock:
                    if jid in self.jobs:self.jobs[jid]["status"]="paused";self._save()
                return
            if downloaded<=0:raise RuntimeError("Empty download")
            if total and downloaded<total:raise RuntimeError("Incomplete download (%d/%d bytes)"%(downloaded,total))
            if not persistent_write_gate(part, final):
                raise RuntimeError("Download HDD disappeared before final commit")
            os.replace(part,final)
            with self.lock:
                live=self.jobs[jid];live["status"]="completed";live["downloaded"]=downloaded;live["total"]=total or downloaded;live["completed_at"]=int(time.time());self._save()
        finally:
            with self._active_response_lock:
                if self._active_response is response:self._active_response=None
            try:response.close()
            except Exception:pass

_MANAGER=None
_MANAGER_LOCK=threading.RLock()

def _get_manager():
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER=DownloadManager()
        return _MANAGER

class _LazyDownloadManager(object):
    """Import-safe facade. The real manager and HDD state load are lazy."""
    def snapshot(self): return _get_manager().snapshot()
    def add(self,job,resolver): return _get_manager().add(job,resolver)
    def cancel(self,jid): return _get_manager().cancel(jid)
    def retry(self,jid,resolver=None): return _get_manager().retry(jid,resolver)
    def shutdown(self,wait=False,timeout=2.5):
        with _MANAGER_LOCK:
            manager=_MANAGER
        return True if manager is None else manager.shutdown(wait=wait,timeout=timeout)

MANAGER=_LazyDownloadManager()
def shutdown_download_manager(wait=False,timeout=2.5):return MANAGER.shutdown(wait=wait,timeout=timeout)


def _portal_origin(profile):
    try:
        parts=urllib.parse.urlsplit(str((profile or {}).get("portal", "") or "").strip())
        scheme=(parts.scheme or "").lower();host=parts.hostname
        if scheme not in ("http","https") or not host:return ""
        port=parts.port or (443 if scheme=="https" else 80)
        display="[%s]"%host if ":" in host and not host.startswith("[") else host
        return "%s://%s:%d"%(scheme,display,port)
    except Exception:
        return ""

def movie_job(profile, item):
    title=_clean(item.get("name") or item.get("title") or "Movie")
    ident=str(item.get("id") or item.get("movie_id") or item.get("cmd") or title)
    jid="movie:"+re.sub(r"\W+","_",ident)[:90]
    path=os.path.join(ROOT,"Movies",title+".ts")
    return {"id":jid,"kind":"movie","title":title,"path":path,"portal_origin":_portal_origin(profile)}


def episode_job(profile, series_item, season, item, index=0):
    series=_clean((series_item or {}).get("name") or (series_item or {}).get("title") or (item or {}).get("_series_title") or "Series")
    sn=(season or {}).get("season") or (season or {}).get("season_number") or (season or {}).get("season_id") or 1
    try: sn=int(str(sn).split(":")[-1])
    except Exception: sn=1
    ep=(item or {}).get("episode_num") or (item or {}).get("episode") or (item or {}).get("number") or (index+1)
    try: ep=int(str(ep).split(":")[-1])
    except Exception: ep=index+1
    ident=str((item or {}).get("id") or (item or {}).get("episode_id") or "%s-%s-%s"%(series,sn,ep))
    jid="episode:"+re.sub(r"\W+","_",ident)[:90]
    title="%s - S%02dE%02d"%(series,sn,ep)
    path=os.path.join(ROOT,"Series",series,"Season %02d"%sn,title+".ts")
    return {"id":jid,"kind":"episode","title":title,"path":path,"season":sn,"episode":ep,"portal_origin":_portal_origin(profile)}
