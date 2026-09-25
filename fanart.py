# -*- coding: utf-8 -*-
from __future__ import absolute_import
import json
import urllib.parse
import urllib.request
import urllib.error
import threading
import time
from .netsec import build_safe_https_media_opener
from .version import PLUGIN_VERSION

_BASE = "https://webservice.fanart.tv/v3.2"
_UA = "Ultra Stalker/%s Fanart" % PLUGIN_VERSION
_API_LOCK = threading.Lock()
_API_LAST = [0.0]
_API_MIN_GAP = 0.45

class FanartError(Exception):
    pass

def _safe_asset_url(value):
    """Normalize trusted Fanart asset URLs without widening the host allowlist.

    Some older Fanart rows still carry http://assets.fanart.tv URLs.  The CDN
    supports HTTPS, so upgrade only that exact trusted host (and protocol-relative
    variants) before ranking.  Any other scheme/host remains rejected.
    """
    text=str(value or "").strip()
    if not text:return ""
    if text.startswith("//"):
        text="https:"+text
    try:
        parts=urllib.parse.urlsplit(text)
        host=str(parts.hostname or "").lower().rstrip(".")
        scheme=str(parts.scheme or "").lower()
        if host!="assets.fanart.tv":return ""
        if scheme=="http":
            text=urllib.parse.urlunsplit(("https",parts.netloc,parts.path,parts.query,parts.fragment))
            parts=urllib.parse.urlsplit(text);scheme="https"
        if scheme!="https":return ""
        return text
    except Exception:
        return ""

def _trace(cb, msg):
    if not callable(cb): return
    try: cb("FANART %s" % str(msg))
    except Exception: pass

def _rank(rows, landscape):
    good=[]
    for row in rows or []:
        if not isinstance(row,dict): continue
        url=_safe_asset_url(row.get("url"))
        if not url: continue
        try:w=int(row.get("width") or 0);h=int(row.get("height") or 0)
        except Exception:w=h=0
        if w and h:
            ratio=float(w)/max(1.0,float(h))
            if landscape and ratio < 1.28: continue
            if not landscape and ratio > 1.05: continue
        try:likes=int(row.get("likes") or 0)
        except Exception:likes=0
        lang=str(row.get("lang") or "00").lower()
        lang_score=2 if lang in ("en","00","") else 1
        good.append((lang_score,likes,w*h,url))
    good.sort(reverse=True)
    return good[0][3] if good else ""

def _rank_logo(rows, wanted_lang):
    """Return the best clear-logo for an explicit language or ``*`` fallback.

    ``*`` is R230's isolated last-resort lane: prefer any EXPLICITLY tagged
    language after preferred and neutral lookups failed. Untagged ``00`` rows
    stay in the caller's neutral stage instead of being mixed into ``any``.
    """
    want=str(wanted_lang or "en").lower().split("-")[0].strip() or "en"
    exact=[]
    for row in rows or []:
        if not isinstance(row,dict): continue
        url=_safe_asset_url(row.get("url"))
        if not url: continue
        lang=str(row.get("lang") or "00").lower().strip() or "00"
        if want=="*":
            if lang in ("00","null",""):continue
        elif lang!=want:
            continue
        try:w=int(row.get("width") or 0);h=int(row.get("height") or 0)
        except Exception:w=h=0
        try:likes=int(row.get("likes") or 0)
        except Exception:likes=0
        exact.append((likes,w*h,w,url))
    exact.sort(reverse=True)
    return exact[0][3] if exact else ""

def _request(params, resource, ident, timeout, trace_cb, auth_mode):
    url="%s/%s/%s?%s"%(_BASE,resource,urllib.parse.quote(ident),urllib.parse.urlencode(params))
    req=urllib.request.Request(url,headers={"User-Agent":_UA,"Accept":"application/json","Connection":"close"})
    opener=build_safe_https_media_opener(allowed_hosts=("webservice.fanart.tv",))
    request_timeout=max(3.0,min(float(timeout or 5.0),12.0))
    last_exc=None
    # release: Fanart documents token-bucket 429 responses. Serialize this tiny
    # metadata call across Ultra Stalker and retry ONCE after Retry-After. This
    # lane is background-only, so it can never stall Backdrop/HUD painting.
    with _API_LOCK:
        for attempt in (0,1):
            gap=_API_MIN_GAP-(time.monotonic()-float(_API_LAST[0] or 0.0))
            if gap>0: time.sleep(gap)
            _trace(trace_cb,"REQUEST resource=%s id=%s auth=%s attempt=%d"%(resource,ident,auth_mode,attempt+1))
            try:
                with opener.open(req,timeout=request_timeout) as r:
                    _API_LAST[0]=time.monotonic()
                    status=int(getattr(r,"status",200) or 200)
                    ctype=str(r.headers.get("Content-Type") or "").lower()
                    raw=r.read(2*1024*1024+1)
                    _trace(trace_cb,"RESPONSE status=%s content_type=%r bytes=%d auth=%s"%(status,ctype,len(raw),auth_mode))
                    if status<200 or status>=300: raise FanartError("HTTP %s"%status)
                    if len(raw)>2*1024*1024: raise FanartError("response too large")
                return json.loads(raw.decode("utf-8","replace")) if raw else {}
            except urllib.error.HTTPError as exc:
                _API_LAST[0]=time.monotonic()
                code=int(getattr(exc,"code",0) or 0)
                _trace(trace_cb,"HTTP_ERROR status=%s auth=%s attempt=%d"%(code,auth_mode,attempt+1))
                last_exc=FanartError("HTTP %s"%code)
                if code==429 and attempt==0:
                    retry=1.25
                    try:
                        raw_retry=str((getattr(exc,"headers",None) or {}).get("Retry-After") or "").strip()
                        if raw_retry: retry=float(raw_retry)
                    except Exception: retry=1.25
                    retry=max(0.75,min(retry,5.0))
                    _trace(trace_cb,"RATE_LIMIT retry_after=%.2f"%retry)
                    time.sleep(retry)
                    continue
                raise last_exc
            except Exception as exc:
                _API_LAST[0]=time.monotonic()
                _trace(trace_cb,"ERROR type=%s auth=%s message=%r"%(exc.__class__.__name__,auth_mode,str(exc)[:160]))
                if isinstance(exc,FanartError): raise
                raise FanartError(str(exc))
    raise last_exc or FanartError("request failed")

def fetch_artwork(project_key, media_type, tmdb_id=None, tvdb_id=None, timeout=5.0, personal_key="", trace_cb=None, logo_lang=""):
    project=str(project_key or "").strip()
    personal=str(personal_key or "").strip()
    if not project and not personal:
        _trace(trace_cb,"SKIP no_keys")
        return {}
    mt="tv" if str(media_type or "").lower() in ("tv","series","episode") else "movie"
    ident=str(tvdb_id or "").strip() if mt=="tv" else str(tmdb_id or "").strip()
    if not ident:
        _trace(trace_cb,"SKIP no_id media=%s"%mt)
        return {}
    resource="tv" if mt=="tv" else "movies"

    # Official API semantics: api_key = project key, client_key = personal key.
    attempts=[]
    if project and personal:
        attempts.append(({"api_key":project,"client_key":personal},"project+personal"))
        # 6.4.1 exposed ambiguous config names and some users may have entered
        # the two values in the opposite fields. One auth-only retry keeps the
        # test useful without logging or exposing either secret.
        attempts.append(({"api_key":personal,"client_key":project},"swapped_fallback"))
    elif project:
        attempts.append(({"api_key":project},"project_only"))
        attempts.append(({"client_key":project},"single_as_personal_fallback"))
    else:
        attempts.append(({"client_key":personal},"personal_only"))
        attempts.append(({"api_key":personal},"single_as_project_fallback"))

    data=None; last=None
    for params,mode in attempts:
        try:
            data=_request(params,resource,ident,timeout,trace_cb,mode)
            if isinstance(data,dict):
                _trace(trace_cb,"AUTH_OK mode=%s keys=%d"%(mode,len(data)))
                break
        except FanartError as exc:
            last=exc
            # Retry only auth-like failures; other failures are real transport/API failures.
            if "HTTP 401" not in str(exc) and "HTTP 403" not in str(exc):
                raise
            data=None
    if data is None:
        raise last or FanartError("request failed")
    if not isinstance(data,dict): return {}
    if mt=="movie":
        poster_rows=data.get("movieposter") or []
        backdrop_rows=data.get("moviebackground") or []
        logo_rows=(data.get("hdmovielogo") or []) + (data.get("movielogo") or [])
        banner_rows=data.get("moviebanner") or []
    else:
        poster_rows=data.get("tvposter") or []
        backdrop_rows=data.get("showbackground") or []
        logo_rows=(data.get("hdtvlogo") or []) + (data.get("clearlogo") or []) + (data.get("tvlogo") or [])
        banner_rows=data.get("tvbanner") or []
    poster=_rank(poster_rows,False)
    backdrop=_rank(backdrop_rows,True)
    wanted_logo_lang=str(logo_lang or "").lower().split("-")[0].strip()
    title_logo=_rank_logo(logo_rows,wanted_logo_lang) if wanted_logo_lang else ""
    banner=_rank(banner_rows,True)
    logo_langs=[]
    try:
        for r in logo_rows:
            if isinstance(r,dict):
                lg=str(r.get("lang") or "00").lower().strip() or "00"
                if lg not in logo_langs:logo_langs.append(lg)
    except Exception:logo_langs=[]
    _trace(trace_cb,"RESULT media=%s id=%s poster_rows=%d backdrop_rows=%d logo_rows=%d logo_lang=%s logo_langs=%s poster=%s backdrop=%s logo=%s"%(mt,ident,len(poster_rows),len(backdrop_rows),len(logo_rows),wanted_logo_lang or "none",",".join(logo_langs[:12]),bool(poster),bool(backdrop),bool(title_logo)))
    return {"poster_url":poster,"backdrop_url":backdrop,"title_logo_url":title_logo,"banner_url":banner,"title_logo_lang":wanted_logo_lang if title_logo else "","title_logo_row_count":len(logo_rows),"title_logo_langs":",".join(logo_langs[:24]),"source":"fanart.tv","raw_id":ident}
