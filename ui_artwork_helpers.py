"""Artwork/metadata helpers extracted from ui.py without changing behavior."""
import html
import json
import os
import urllib.parse

from .log import optional_failure

_PORTAL_ART_KEYS = (
    "cover","cover_url","poster","poster_url","movie_image","screenshot_uri","screenshot",
    "image","img","hd","pic","backdrop","backdrops","backdrop_url","backdrop_urls",
    "backdrop_path","background","background_url","background_image","fanart","fanart_url",
    "fanart_image","wallpaper","screenshots","preview",
)

def _image_url(item):
    if not isinstance(item, dict):
        return None
    for key in ("stream_icon", "picon", "logo", "logo_url", "poster", "poster_url", "cover", "cover_url", "movie_image", "screenshot_uri", "screenshot", "image", "img", "hd", "pic"):

        value = item.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() not in ("null", "none"):
            return value.strip()
    return None


def _first_art_value(value):
    if isinstance(value, (list, tuple)):
        for v in value:
            out=_first_art_value(v)
            if out:return out
        return None
    if isinstance(value, dict):
        for k in ("url", "src", "path", "image"):
            out=_first_art_value(value.get(k))
            if out:return out
        return None
    if isinstance(value, str):
        text=value.strip()
        if not text or text.lower() in ("null", "none", "[]", "{}"):
            return None
        if text.startswith("["):
            try:
                parsed=json.loads(text)
                out=_first_art_value(parsed)
                if out:return out
            except Exception as exc:optional_failure("ui",exc)
        return text
    return None


def _backdrop_candidates(item):
    """Return only fields that can legitimately contain landscape fanart.

    Poster/cover/movie_image are intentionally excluded: us8 enlarged a
    portrait poster into the background and that looked worse than no fanart.
    """
    if not isinstance(item, dict):return []
    result=[]
    for key in ("backdrop", "backdrops", "backdrop_url", "backdrop_urls", "backdrop_path", "background", "background_url", "background_image", "fanart", "fanart_url", "fanart_image", "wallpaper", "screenshot_uri", "screenshots", "screenshot", "preview"):

        value=item.get(key)
        values=value if isinstance(value,(list,tuple)) else [value]
        if isinstance(value,str) and value.strip().startswith("["):
            try:values=json.loads(value)
            except Exception:values=[value]
        for entry in values if isinstance(values,(list,tuple)) else [values]:
            out=_first_art_value(entry)
            if out and out not in result:result.append(out)
    return result


def _backdrop_url(item):
    items=_backdrop_candidates(item)
    return items[0] if items else None


def _strip_portal_artwork(item):
    """Return a content row with every portal-supplied VOD/Series artwork field removed.

    IDs, titles and metadata remain available for external identity matching.  This
    makes the no-portal-artwork policy structural rather than a ranking preference.
    """
    if not isinstance(item,dict):
        return item
    clean=dict(item)
    for key in _PORTAL_ART_KEYS:
        clean.pop(key,None)
    return clean


def _verified_external_art(snapshot, kind):
    """Return only externally sourced artwork from a persisted snapshot.

    us109/110 could store a portal fallback inside an otherwise TMDB-linked
    snapshot.  Reject those legacy paths as well as portal_payload snapshots.
    """
    if not isinstance(snapshot,dict):return ""
    if str(snapshot.get("identity_source") or "")=="portal_payload":return ""
    if str(snapshot.get("source") or "").upper()=="PORTAL":return ""
    key="%s_local"%str(kind)
    portal_key="portal_%s_local"%str(kind)
    value=str(snapshot.get(key) or "")
    legacy=str(snapshot.get(portal_key) or "")
    if not value or (legacy and os.path.abspath(value)==os.path.abspath(legacy)):return ""
    return value if os.path.isfile(value) and os.path.getsize(value)>100 else ""


def _normalize_provider_image_url(value):
    text=html.unescape(str(value or "").strip())
    # Some playlist generators leave JSON-style escaped slashes or wrap URLs
    # again in quotes. Normalize those before URL parsing.
    if len(text)>=2 and text[0]==text[-1] and text[0] in ("\"","'"):
        text=text[1:-1].strip()
    text=text.replace("\\/","/")
    if not text:return ""
    try:
        parts=urllib.parse.urlsplit(text)
        if parts.scheme.lower() not in ("http","https"):
            return text
        path=urllib.parse.quote(urllib.parse.unquote(parts.path),safe="/:@-._~!$&'()*+,;=")
        query=urllib.parse.quote(urllib.parse.unquote(parts.query),safe="=&:@/?-._~!$'()*+,;")
        return urllib.parse.urlunsplit((parts.scheme,parts.netloc,path,query,parts.fragment))
    except Exception:
        return text.replace(" ","%20")


