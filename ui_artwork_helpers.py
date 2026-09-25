"""Artwork/metadata helpers extracted from ui.py without changing behavior."""
import html
import json
import os
import urllib.parse
import hashlib

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





def _fit_live_row_picon_canvas(source_path, cache_root, size=(64,36)):
    """Normalize one Live list-row picon without stretching it.

    Provider picons frequently arrive on oversized transparent canvases.  The
    old 12-row Live screen rendered those pixels literally, so a perfectly good
    logo could look tiny even though the widget geometry was correct.  For the
    list only, trim transparent padding, then aspect-fit the visible logo into
    an exact-size transparent canvas.  Upscaling is allowed because these are
    small UI logos, not posters.
    """
    try:
        from PIL import Image as PILImage, ImageOps as PILImageOps
    except Exception:
        return source_path if source_path and os.path.isfile(str(source_path)) else ""
    try:
        source=str(source_path or "")
        if not source or not os.path.isfile(source) or os.path.getsize(source)<=100:
            return ""
        tw,th=max(1,int(size[0])),max(1,int(size[1]))
        try: stamp="%s|%s"%(os.path.getmtime(source),os.path.getsize(source))
        except Exception: stamp="0"
        root=os.path.join(str(cache_root or ""),"live_row_picon_fit")
        if not root:
            return source
        os.makedirs(root,exist_ok=True)
        key=hashlib.sha1((source+"|"+stamp+"|row-alpha-trim-upscale-v1|%dx%d"%(tw,th)).encode("utf-8","ignore")).hexdigest()[:24]
        target=os.path.join(root,key+"_%dx%d.png"%(tw,th))
        if os.path.isfile(target) and os.path.getsize(target)>100:
            return target
        temp=target+".tmp.%d"%os.getpid()
        with PILImage.open(source) as im:
            try: im=PILImageOps.exif_transpose(im)
            except Exception: pass
            im=im.convert("RGBA")
            # Remove transparent provider padding before scaling.  A small alpha
            # threshold also ignores anti-aliased ghost pixels at the outer edge.
            try:
                alpha=im.getchannel("A")
                mask=alpha.point(lambda value: 255 if value>8 else 0)
                bbox=mask.getbbox()
                if bbox and bbox!=(0,0,im.width,im.height):
                    cropped=im.crop(bbox)
                    if cropped.width>0 and cropped.height>0:
                        im=cropped
            except Exception:
                pass
            sw,sh=im.size
            if sw<=0 or sh<=0:
                return source
            resampling=getattr(getattr(PILImage,"Resampling",PILImage),"LANCZOS",1)
            scale=min(float(tw)/float(sw),float(th)/float(sh))
            nw=max(1,int(round(sw*scale)));nh=max(1,int(round(sh*scale)))
            if (nw,nh)!=(sw,sh):
                im=im.resize((nw,nh),resampling)
            canvas=PILImage.new("RGBA",(tw,th),(0,0,0,0))
            canvas.alpha_composite(im,((tw-nw)//2,(th-nh)//2))
            canvas.save(temp,"PNG",compress_level=3,optimize=False)
        os.replace(temp,target)
        return target if os.path.isfile(target) and os.path.getsize(target)>100 else source
    except Exception as exc:
        optional_failure("ui.live_row_picon_fit",exc)
        try:
            if 'temp' in locals() and os.path.exists(temp): os.unlink(temp)
        except Exception: pass
        return source_path if source_path and os.path.isfile(str(source_path)) else ""

def _fit_live_picon_canvas(source_path, cache_root, size=(220,132)):
    """Render a Live picon exactly like an Enigma2 aspect-safe info-bar slot.

    The source is never stretched. It is fitted inside a transparent 220x132
    canvas, centered on both axes, and cached as PNG so Home and Player reuse
    the exact same pixels.
    """
    try:
        from PIL import Image as PILImage, ImageOps as PILImageOps
    except Exception:
        return source_path if source_path and os.path.isfile(str(source_path)) else ""
    try:
        source=str(source_path or "")
        if not source or not os.path.isfile(source) or os.path.getsize(source)<=100:
            return ""
        tw,th=max(1,int(size[0])),max(1,int(size[1]))
        try: stamp="%s|%s"%(os.path.getmtime(source),os.path.getsize(source))
        except Exception: stamp="0"
        root=os.path.join(str(cache_root or ""),"live_picon_fit")
        if not root:
            return source
        os.makedirs(root,exist_ok=True)
        key=hashlib.sha1((source+"|"+stamp+"|enigma-fit-v2-noupscale|%dx%d"%(tw,th)).encode("utf-8","ignore")).hexdigest()[:24]
        target=os.path.join(root,key+"_%dx%d.png"%(tw,th))
        if os.path.isfile(target) and os.path.getsize(target)>100:
            return target
        temp=target+".tmp.%d"%os.getpid()
        with PILImage.open(source) as im:
            try: im=PILImageOps.exif_transpose(im)
            except Exception: pass
            im=im.convert("RGBA")
            resampling=getattr(getattr(PILImage,"Resampling",PILImage),"LANCZOS",1)
            sw,sh=im.size
            if sw<=0 or sh<=0:
                return source
            scale=min(1.0,float(tw)/float(sw),float(th)/float(sh))
            nw=max(1,int(round(sw*scale))); nh=max(1,int(round(sh*scale)))
            if (nw,nh)!=(sw,sh): im=im.resize((nw,nh),resampling)
            canvas=PILImage.new("RGBA",(tw,th),(0,0,0,0))
            canvas.alpha_composite(im,((tw-nw)//2,(th-nh)//2))
            canvas.save(temp,"PNG",compress_level=3,optimize=False)
        os.replace(temp,target)
        return target if os.path.isfile(target) and os.path.getsize(target)>100 else source
    except Exception as exc:
        optional_failure("ui.live_picon_fit",exc)
        try:
            if 'temp' in locals() and os.path.exists(temp): os.unlink(temp)
        except Exception: pass
        return source_path if source_path and os.path.isfile(str(source_path)) else ""
