"""Poster and Live grid screens extracted from ui.py without changing class behavior."""

from . import _
from .ui_grid_base import PremiumGridBase
from .ui_dynamic_palette import _dynamic_palette
from .ui_helpers import _lift_dynamic_accent
import re
import json
import threading
import time
import os
import hashlib
import queue
from .core.shared_executors import CACHE_IO_EXECUTOR as _LIVE_ROW_FIT_EXECUTOR
try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _PILImageFilter
except Exception:
    _PILImage = None
    _ImageDraw = None
    _PILImageFilter = None

POSTER_GRID_SKIN = ""
POSTER_GRID_V2_SKIN = ""
LIVE_GRID_SKIN = ""

def configure_grid_screens(**deps):
    globals().update(deps)
    if "POSTER_GRID_SKIN" in deps:
        PremiumPosterGridScreen.skin = deps["POSTER_GRID_SKIN"]
    if "POSTER_GRID_V2_SKIN" in deps:
        PremiumPosterGridV2Screen.skin = deps["POSTER_GRID_V2_SKIN"]
    if "LIVE_GRID_SKIN" in deps:
        PremiumLiveGridScreen.skin = deps["LIVE_GRID_SKIN"]



def _pgv2_clean_title_detached(item):
    raw=(item or {}).get("name") or (item or {}).get("title") or ""
    try:return _clean_display_text(raw,80) or str(raw or "")
    except Exception:return str(raw or "")[:80]


def _pgv2_cinematic_title_detached(media_type,item,row=None):
    try:
        from .ui_cinematic_global import PremiumGlobalCinematicScreen
        proxy=object.__new__(PremiumGlobalCinematicScreen);proxy.media_type=media_type
        return PremiumGlobalCinematicScreen._rail_title(proxy,item,row if isinstance(row,dict) else {}) or ""
    except Exception:
        try:
            from .title_clean import display_title
            return display_title((item or {}).get("name") or (item or {}).get("title") or "") or ""
        except Exception:return ""


def _pgv2_logo_cache_path_detached(media_type,tmdb_id,lang):
    try:
        from .title_logo_runtime import ultra_title_logo_cache_path
        return ultra_title_logo_cache_path(media_type,tmdb_id,lang,(900,125))
    except Exception:return ""


def _pgv2_logo_seal_path_detached(profile,media_type,item):
    try:
        from .persistent_cache import BLUE_CACHE,content_cache_key
        key=str(content_cache_key(profile,media_type,item) or "")
        if not key:return ""
        folder=os.path.join(BLUE_CACHE,"pgv2_title_logo_ready")
        if not os.path.isdir(folder):os.makedirs(folder,mode=0o700)
        return os.path.join(folder,hashlib.sha1(key.encode("utf-8","ignore")).hexdigest()+".json")
    except Exception:return ""


def _pgv2_read_logo_seal_detached(profile,media_type,item):
    try:
        path=_pgv2_logo_seal_path_detached(profile,media_type,item)
        if not path:return {}
        cached=_TITLE_LOGO_SEAL_RAM.get(path) if "_TITLE_LOGO_SEAL_RAM" in globals() else None
        if isinstance(cached,dict) and int(cached.get("schema") or 0)>=3 and cached.get("checked"):
            return dict(cached)
        if not os.path.isfile(path):return {}
        with open(path,"r",encoding="utf-8") as h:data=json.load(h)
        if isinstance(data,dict) and int(data.get("schema") or 0)>=3 and data.get("checked"):
            try:_pgv2_logo_seal_ram_put(path,data)
            except Exception:pass
            return data
        return {}
    except Exception:return {}


def _pgv2_write_logo_seal_detached(profile,media_type,item,row,logo_path):
    try:
        path=_pgv2_logo_seal_path_detached(profile,media_type,item)
        if not path:return False
        payload={"schema":5,"logo_policy":2,"tmdb_id":(row or {}).get("tmdb_id"),"title_logo_local":str(logo_path or ""),"checked":True,"updated_at":int(time.time())}
        tmp=path+".tmp.%s.%s"%(os.getpid(),threading.get_ident())
        with open(tmp,"w",encoding="utf-8") as h:
            json.dump(payload,h,ensure_ascii=False,separators=(",",":"))  # rebuildable title-logo cache seal
        os.chmod(tmp,0o600);os.replace(tmp,path)
        try:_pgv2_logo_seal_ram_put(path,payload)
        except Exception:pass
        return True
    except Exception:
        try:
            if "tmp" in locals() and os.path.exists(tmp):os.unlink(tmp)
        except OSError:pass
        return False


# R228: one tiny shared title-logo state for PG2 / Cinematic / BG1 / BG2.
# Positive logo seals are durable while the source file exists. A negative seal is
# intentionally short-lived so remote artwork can recover later without making every
# focus pay the same expensive discovery path.
_TITLE_LOGO_SEAL_RAM = {}
_TITLE_LOGO_SEAL_RAM_MAX = 512
_TITLE_LOGO_NEGATIVE_TTL = 6 * 60 * 60

def _pgv2_logo_seal_ram_put(path,data):
    try:
        key=str(path or "")
        if not key or not isinstance(data,dict):return
        _TITLE_LOGO_SEAL_RAM[key]=dict(data)
        while len(_TITLE_LOGO_SEAL_RAM)>_TITLE_LOGO_SEAL_RAM_MAX:
            _TITLE_LOGO_SEAL_RAM.pop(next(iter(_TITLE_LOGO_SEAL_RAM)))
    except Exception:
        pass

def _pgv2_known_tmdb_id_detached(item,row=None):
    item=item if isinstance(item,dict) else {}
    row=row if isinstance(row,dict) else {}
    for obj,key in ((item,"_player_title_logo_tmdb_id"),(row,"_player_title_logo_tmdb_id"),
                    (item,"_locked_tmdb_id"),(row,"_locked_tmdb_id"),
                    (row,"tmdb_id"),(item,"tmdb_id")):
        value=obj.get(key)
        if value not in (None,""):return str(value)
    return ""

def _pgv2_title_logo_probe_missing_detached(item,row=None):
    """Zero-network hint: current hydrated row already checked and found no logo.

    It is presentation-only, not a permanent negative cache. The background resolver
    may still promote a later TMDb/Fanart hit if this hint was stale.
    """
    sources=[]
    if isinstance(row,dict):sources.append(row)
    if isinstance(item,dict):sources.append(item)
    checked=False
    for src in sources:
        if any(bool(src.get(k)) for k in ("title_logo_probe_v2_checked","title_logo_probe_checked","title_logo_checked")):
            checked=True
        for key in ("title_logo_local","logo_local"):
            path=str(src.get(key) or "")
            if path and os.path.isfile(path) and os.path.getsize(path)>256:return False
        if str(src.get("logo_url") or "").strip():return False
    return bool(checked)

def _pgv2_logo_seal_state_detached(profile,media_type,item,row=None):
    """Return (state, path, seal) where state is logo/missing/unknown.

    This is HDD/RAM only and never decodes an image or touches the network.
    """
    seal=_pgv2_read_logo_seal_detached(profile,media_type,item) or {}
    if not seal:return ("unknown","",{})
    current_id=_pgv2_known_tmdb_id_detached(item,row)
    seal_id=str(seal.get("tmdb_id") or "")
    if current_id and seal_id and current_id!=seal_id:return ("unknown","",seal)
    path=str(seal.get("title_logo_local") or "")
    if path and os.path.isfile(path) and os.path.getsize(path)>256:return ("logo",path,seal)
    if path:return ("unknown","",seal)
    try:age=max(0,int(time.time())-int(seal.get("updated_at") or 0))
    except Exception:age=_TITLE_LOGO_NEGATIVE_TTL+1
    # R230: a pre-policy negative seal may only mean "no Arabic/English logo".
    # Re-open it once under the new any-language fallback policy. Positive old
    # seals above remain valid and fast.
    if seal.get("checked") and int(seal.get("logo_policy") or 0)>=2 and age<=_TITLE_LOGO_NEGATIVE_TTL:return ("missing","",seal)
    return ("unknown","",seal)

def _pgv2_logo_canvas_matches_detached(path,canvas_size):
    try:
        w,h=int(canvas_size[0]),int(canvas_size[1])
        return bool(str(path or "").endswith("_%sx%s.png"%(w,h)))
    except Exception:return False

def _pgv2_bridge_logo_from_seal_detached(profile,media_type,item,row=None,canvas_size=(900,125),cancel_event=None):
    """Worker-only cross-view bridge from any already-verified logo seal.

    A 900x125 PG2 hit can therefore become BG/Cinematic locally in milliseconds
    instead of repeating Details/TMDb/Fanart discovery.
    """
    state,source,seal=_pgv2_logo_seal_state_detached(profile,media_type,item,row)
    if state!="logo" or not source:return (state,"",seal)
    if cancel_event is not None and cancel_event.is_set():return ("unknown","",seal)
    if _pgv2_logo_canvas_matches_detached(source,canvas_size):return ("logo",source,seal)
    try:
        st=os.stat(source);w,h=int(canvas_size[0]),int(canvas_size[1])
        from .persistent_cache import GENERATED
        folder=os.path.join(GENERATED,"title_logo_bridge_v1")
        if not os.path.isdir(folder):os.makedirs(folder,mode=0o700)
        sig="%s|%s|%s|%sx%s"%(source,int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size),w,h)
        target=os.path.join(folder,hashlib.sha1(sig.encode("utf-8","ignore")).hexdigest()[:28]+".png")
        if os.path.isfile(target) and os.path.getsize(target)>256:return ("logo",target,seal)
        from .title_logo_runtime import prepare_title_logo_file
        margins={(900,125):(40,13),(420,144):(16,12),(432,210):(28,24)}
        mx,my=margins.get((w,h),(max(12,int(w*0.045)),max(6,int(h*0.08))))
        path=prepare_title_logo_file(source,target,canvas_size=(w,h),margin_x=mx,margin_y=my) or ""
        if path and os.path.isfile(path) and os.path.getsize(path)>256:return ("logo",path,seal)
    except Exception:
        pass
    return ("unknown","",seal)

def _pgv2_title_logo_language_order_detached(row):
    """Use the same lightweight identity policy as Cinematic/BG/Details."""
    try:
        from .title_logo_runtime import title_logo_policy_languages
        return title_logo_policy_languages({},row if isinstance(row,dict) else {})
    except Exception:
        return ("en",)


def _pgv2_cached_logo_for_policy_detached(media_type,tmdb_id,row):
    """Poster Grid V2 HDD-only bridge to the shared Ultra logo authority.

    PGV2 keeps its own 900x125 render canvas, but identity, language policy and
    cache ownership now come from ``title_logo_ultra`` exactly like Cinematic
    and Backdrop Grid.  No network or decode work is allowed in this fast path.
    """
    if tmdb_id in (None,""):return ""
    fresh=dict(row or {}) if isinstance(row,dict) else {}
    fresh["tmdb_id"]=tmdb_id
    fresh.setdefault("_locked_tmdb_id",tmdb_id)
    try:
        from .title_logo_ultra import ultra_title_logo_cached
        return ultra_title_logo_cached(media_type,{"tmdb_id":tmdb_id,"_locked_tmdb_id":tmdb_id},fresh,(900,125),"") or ""
    except Exception:return ""


def _pgv2_authoritative_logo_row_detached(profile,media_type,item,row,cancel_event=None,settings=None):
    """Exact PGV2 identity gate, shared by every title-logo presentation.

    Poster Grid V2 is the receiver-proven path: consume its current HDD row, and
    when canonical identity is not already sealed, hydrate through Details
    Authority before logo discovery.  Keeping this tiny gate shared prevents
    Cinematic/BG from drifting back to a weaker resolver path.
    """
    fresh=dict(row or {}) if isinstance(row,dict) else {}
    if cancel_event is not None and cancel_event.is_set():return fresh
    try:
        identity_ready=bool(
            fresh.get("identity_verified") or fresh.get("identity_pointer_verified") or
            fresh.get("_details_authority_ready") or fresh.get("_locked_tmdb_id") or
            (isinstance(item,dict) and item.get("_locked_tmdb_id"))
        )
        if not identity_ready:
            from .details_authority import resolve_canonical
            authoritative=resolve_canonical(
                profile,media_type,dict(item or {}),
                cancel_event=cancel_event,settings=(settings or {})
            ) or {}
            if cancel_event is not None and cancel_event.is_set():return fresh
            if authoritative:
                merged=dict(fresh)
                for k,v in authoritative.items():
                    if v not in (None,"",[],{}):merged[k]=v
                fresh=merged
    except Exception as exc:
        try:optional_failure("ui.pgv2_logo_authoritative_row",exc)
        except Exception:pass
    return fresh


def _pgv2_resolve_title_logo_detached(profile,media_type,item,row,cancel_event=None,settings=None,canvas_size=(900,125)):
    """Resolve through the receiver-proven Poster Grid V2 logo authority.

    Discovery/identity/language/neutral/Fanart/cache policy is identical for all
    views.  Only the final presentation canvas differs per screen.
    """
    fresh=dict(row or {}) if isinstance(row,dict) else {}
    if cancel_event is not None and cancel_event.is_set():return ("",fresh)
    try:
        from .title_logo_ultra import resolve_ultra_title_logo
        visible=_pgv2_cinematic_title_detached(media_type,item,fresh) or _pgv2_clean_title_detached(item)
        return resolve_ultra_title_logo(
            profile,media_type,dict(item or {}),fresh,
            canvas_size=canvas_size,visible_title=visible,
            cancel_event=cancel_event,settings=settings,
        )
    except Exception as exc:
        try:optional_failure("ui.pgv2_logo_shared_authority",exc)
        except Exception:pass
        return ("",fresh)


class PremiumPosterGridScreen(PremiumGridBase):
    # This skin visibly renders YEAR + TMDb score on every card.  Cold identity
    # hydration can finish just after the first metadata pass, so two bounded
    # missing-only sweeps finish all 14 visible cards without requiring focus.
    visible_card_rating_prefetch=True
    visible_card_age_prefetch=True
    visible_card_rating_retry_delays=(1.60,3.20)
    skin=POSTER_GRID_SKIN;columns=7;page_size=14;image_size=(206,310);placeholder="grid_placeholder_movie_921.png";selection_asset="poster_card_selected_neutral_small.png"
    card_positions=[(x,y) for y in (137,537) for x in (47,303,559,815,1071,1327,1583)]
    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        self.placeholder="grid_placeholder_series_921.png" if media_type=="series" else "grid_placeholder_movie_921.png"
        self.selection_asset="poster_card_selected_neutral_small.png"
        self._poster_neutral_card_asset="state_blank.png"
        # Keep the proven Beta49 catalogue/navigation behavior.  Small-card
        # poster fill is handled by a dedicated thumbnail flag so it cannot
        # change paging/catalogue semantics.
        self._poster_cover_mode=False
        self._poster_small_fill=True
        PremiumGridBase.__init__(self,session,profile,client,media_type,genre,category_title)


class PremiumPosterGridV2Screen(PremiumGridBase):
    # release Poster Grid V2: persistent HQ title logos + Cinematic-style atomic handoff.
    # Catalogue/navigation/cache semantics stay in PremiumGridBase.
    skin=POSTER_GRID_V2_SKIN;columns=9;page_size=27;image_size=(206,309);placeholder="grid_placeholder_movie_921.png";selection_asset="pgv2_selection_exact_206x309.png"
    card_positions=[(x,y) for y in (4,319,634) for x in (9,221,433,645,857,1069,1281,1493,1705)]
    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        self.placeholder="grid_placeholder_series_921.png" if media_type=="series" else "grid_placeholder_movie_921.png"
        self.selection_asset="pgv2_selection_exact_206x309.png"
        self._poster_neutral_card_asset="poster_card_full_neutral_small.png"
        self._poster_cover_mode=False;self._poster_small_fill=True
        self._pgv2_hero_key="";self._pgv2_hero_jobs=queue.Queue();self._pgv2_hero_token=0;self._pgv2_hero_pending=set();self._pgv2_has_committed_hero=False;self._pgv2_last_logo_path="";self._pgv2_material_key="";self._pgv2_material_jobs=queue.Queue();self._pgv2_material_token=0;self._pgv2_material_pending=set()
        self._pgv2_scope="%x"%id(self);self._pgv2_hero_future=None;self._pgv2_material_future=None;self._pgv2_hero_cancel=threading.Event();self._pgv2_material_cancel=threading.Event()
        PremiumGridBase.__init__(self,session,profile,client,media_type,genre,category_title)
        self["pgv2_connected_material"]=Pixmap();self["hero_banner"]=Pixmap();self["hero_logo"]=Pixmap();self["hero_clean_title"]=Label("")
        self._pgv2_timer=eTimer();self._pgv2_timer_conn=None
        try:self._pgv2_timer_conn=self._pgv2_timer.timeout.connect(self._pgv2_drain_hero)
        except Exception:self._pgv2_timer.callback.append(self._pgv2_drain_hero)
        try:self._pgv2_timer.start(90,False)
        except Exception as exc:optional_failure("ui.pgv2_timer_start",exc)
        self.onClose.append(self._pgv2_stop)

    def _pgv2_stop(self):
        try:self._pgv2_timer.stop()
        except Exception:pass
        try:
            self._pgv2_hero_cancel.set();self._pgv2_material_cancel.set()
            for future in (self._pgv2_hero_future,self._pgv2_material_future):
                if future is not None:future.cancel()
        except Exception:pass
        self._pgv2_hero_future=None;self._pgv2_material_future=None
        try:self._pgv2_hero_token+=1;self._pgv2_material_token+=1
        except Exception:pass
        self._pgv2_hero_pending.clear();self._pgv2_material_pending.clear()
        try:
            if self._pgv2_timer_conn is not None:self._pgv2_timer_conn.disconnect()
        except Exception:pass
        self._pgv2_timer_conn=None
        try:
            if self._pgv2_drain_hero in self._pgv2_timer.callback:self._pgv2_timer.callback.remove(self._pgv2_drain_hero)
        except Exception:pass
        for q in (self._pgv2_hero_jobs,self._pgv2_material_jobs):
            try:
                while True:q.get_nowait()
            except queue.Empty:pass
            except Exception:pass

    def _pgv2_clean_title(self,item):
        return _pgv2_clean_title_detached(item)

    def _pgv2_contain_asset(self,source,kind,build=False):
        """Return an exact-size transparent hero canvas without stretching source art."""
        if not source or not os.path.isfile(source):return ""
        try:
            st=os.stat(source);dims=(900,125)
            sig="%s|%s|%s|%s|%sx%s|v84-hq"%(source,int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size),kind,dims[0],dims[1])
            from .persistent_cache import GENERATED
            target=os.path.join(GENERATED,"pgv2_%s_fit_%s.png"%(kind,hashlib.sha1(sig.encode("utf-8","ignore")).hexdigest()[:24]))
            if os.path.isfile(target) and os.path.getsize(target)>512:return target
            if not build:return ""
            from PIL import Image,ImageOps
            with Image.open(source) as im:
                try:im=ImageOps.exif_transpose(im)
                except Exception:pass
                im=im.convert("RGBA")
                res=getattr(getattr(Image,"Resampling",Image),"LANCZOS",1)
                im.thumbnail(dims,res)
                canvas=Image.new("RGBA",dims,(0,0,0,0))
                canvas.alpha_composite(im,((dims[0]-im.width)//2,(dims[1]-im.height)//2))
                tmp=target+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG",compress_level=3,optimize=False);os.replace(tmp,target)
            return target if os.path.isfile(target) else ""
        except Exception as exc:
            optional_failure("ui.pgv2_contain",exc);return ""

    def _update_header(self,item):
        # R229: header text remains owned by the card/hero shell. Selection identity
        # is painted by _pgv2_show_current_title_first(), then upgraded to a logo.
        try:self["title"].setText("")
        except Exception:pass
        try:return PremiumGridBase._update_header(self,item)
        except Exception:return None

    def _pgv2_show_current_title_first(self):
        """R229 current-item-first identity: text now, logo only as an upgrade.

        Arrow navigation must never inherit another title's logo.  The selected
        title is painted from the in-memory item immediately.  A same-canvas logo
        already present in the shared RAM seal may promote it synchronously; all
        HDD/network discovery remains behind stable focus.
        """
        if self._screen_closed or not self.grid_items:return
        item=self.grid_items[self.index]
        title=self._pgv2_clean_title(item)
        try:
            self["hero_logo"].hide();self._pgv2_last_logo_path=""
            self._pgv2_fit_official_title(title);self["hero_clean_title"].show()
            self["hero_banner"].hide()
        except Exception:pass
        # RAM-only positive promotion. Do not touch HDD or start discovery on an
        # arrow repeat; that would make navigation pay for artwork again.
        try:
            seal_path=_pgv2_logo_seal_path_detached(self.profile,self.media_type,item)
            seal=dict(_TITLE_LOGO_SEAL_RAM.get(seal_path) or {})
            current_id=_pgv2_known_tmdb_id_detached(item,{})
            seal_id=str(seal.get("tmdb_id") or "")
            source=str(seal.get("title_logo_local") or "")
            identity_ok=not (current_id and seal_id and current_id!=seal_id)
            if identity_ok and source and _pgv2_logo_canvas_matches_detached(source,(900,125)) and os.path.isfile(source) and os.path.getsize(source)>512:
                self["hero_logo"].instance.setPixmapFromFile(source);self["hero_logo"].show();self["hero_clean_title"].hide();self._pgv2_last_logo_path=source
        except Exception:pass

    def _update_selection(self):
        """R229: selection identity is text-first; logo discovery stays debounced."""
        PremiumGridBase._update_selection(self)
        try:self._pgv2_show_current_title_first()
        except Exception as exc:optional_failure("ui.pgv2_title_first",exc)
        if self.media_type in ("vod","series"):
            try:
                self._nav_burst_until=time.monotonic()+0.18
                self._grid_focus_timer.stop();self._grid_focus_timer.start(180,True)
            except Exception:pass

    def _apply_debounced_grid_focus(self):
        PremiumGridBase._apply_debounced_grid_focus(self)
        try:self._pgv2_schedule_hero()
        except Exception as exc:optional_failure("ui.pgv2_hero_schedule",exc)
        try:self._pgv2_schedule_connected_material()
        except Exception as exc:optional_failure("ui.pgv2_material_schedule",exc)


    def _pgv2_material_source(self):
        """Local poster only. Stable focus owns this work; navigation never touches HDD/PIL."""
        try:
            path=str((getattr(self,"_grid_palette_sources",{}) or {}).get(self.index) or (getattr(self,"_grid_slot_paths",{}) or {}).get(self.index) or "")
            return path if path and os.path.isfile(path) else ""
        except Exception:
            return ""

    @staticmethod
    def _pgv2_build_connected_material(source,key):
        """Build the connected adaptive wall + exact 206x309 bright focus asset."""
        if _PILImage is None or _ImageDraw is None or not source or not os.path.isfile(source):return ("","")
        try:
            st=os.stat(source)
            sig="%s|%s|%s|pgv2-connected-laser-v76"%(source,int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size))
            from .persistent_cache import GENERATED
            target=os.path.join(GENERATED,"pgv2_connected_%s.png"%hashlib.sha1(sig.encode("utf-8","ignore")).hexdigest()[:24])
            focus_target=os.path.join(GENERATED,"pgv2_focus_%s.png"%hashlib.sha1((sig+"|focus-exact-206x309-v76").encode("utf-8","ignore")).hexdigest()[:24])
            primary,_secondary=_dynamic_palette(source)
            r,g,b=_lift_dynamic_accent(primary,0.48,0.46)
            W,H=1920,1080

            if not (os.path.isfile(target) and os.path.getsize(target)>512):
                canvas=_PILImage.new("RGBA",(W,H),(0,0,0,0))
                draw=_ImageDraw.Draw(canvas)
                def glass_rect(box,vertical=True):
                    x0,y0,x1,y1=[int(v) for v in box]
                    if x1<=x0 or y1<=y0:return
                    span=max(1,(y1-y0-1) if vertical else (x1-x0-1))
                    for step in range(span+1):
                        t=float(step)/float(span)
                        level=(0.58-0.31*t) if t < 0.76 else (0.29+0.13*(t-0.76)/0.24)
                        rr=int(max(4,min(255,r*level)));gg=int(max(6,min(255,g*level)));bb=int(max(8,min(255,b*level)))
                        if vertical:draw.line((x0,y0+step,x1,y0+step),fill=(rr,gg,bb,246))
                        else:draw.line((x0+step,y0,x0+step,y1),fill=(rr,gg,bb,246))

                wall_bottom=943
                glass_rect((0,0,8,wall_bottom),vertical=False)
                glass_rect((1911,0,1919,wall_bottom),vertical=False)
                glass_rect((0,0,1919,3),vertical=True)
                for c in range(8):
                    x=215+(212*c);glass_rect((x,0,x+5,wall_bottom),vertical=False)
                for y in (313,628):glass_rect((0,y,1919,y+5),vertical=True)

                # V76 Floating Hero: the Poster Wall ends with the last poster row.
                # No footer/glass slab is painted below it. The native dark page background remains visible.
                # Only a very soft adaptive atmosphere sits behind the Hero so Banner/Logo/Title floats naturally.
                hero_fx=_PILImage.new("RGBA",(W,H),(0,0,0,0))
                hx=_ImageDraw.Draw(hero_fx)
                # low-energy adaptive bloom, intentionally without a hard rectangle or visible frame
                hx.rounded_rectangle((545,952,1375,1074),radius=30,fill=(r,g,b,42))
                if _PILImageFilter is not None:hero_fx=hero_fx.filter(_PILImageFilter.GaussianBlur(34.0))
                canvas=_PILImage.alpha_composite(canvas,hero_fx)
                hero_shadow=_PILImage.new("RGBA",(W,H),(0,0,0,0))
                hs=_ImageDraw.Draw(hero_shadow)
                hs.rounded_rectangle((592,966,1328,1078),radius=22,fill=(0,0,0,105))
                if _PILImageFilter is not None:hero_shadow=hero_shadow.filter(_PILImageFilter.GaussianBlur(16.0))
                canvas=_PILImage.alpha_composite(canvas,hero_shadow)

                laser_hot=tuple(min(255,int(v*.48+255*.52)) for v in (r,g,b))
                laser_core=tuple(min(255,int(v*.20+255*.80)) for v in (r,g,b))
                paths=[]
                for c in range(8):
                    x=218+(212*c);paths.append((x,0,x,wall_bottom))
                for y in (316,631):paths.append((0,y,1919,y))
                paths.append((0,946,1919,946))

                bloom=_PILImage.new("RGBA",(W,H),(0,0,0,0));bd=_ImageDraw.Draw(bloom)
                for line in paths:bd.line(line,fill=(r,g,b,170),width=5)
                if _PILImageFilter is not None:bloom=bloom.filter(_PILImageFilter.GaussianBlur(3.2))
                canvas=_PILImage.alpha_composite(canvas,bloom)
                halo=_PILImage.new("RGBA",(W,H),(0,0,0,0));hd=_ImageDraw.Draw(halo)
                for line in paths:hd.line(line,fill=(r,g,b,225),width=3)
                if _PILImageFilter is not None:halo=halo.filter(_PILImageFilter.GaussianBlur(1.35))
                canvas=_PILImage.alpha_composite(canvas,halo)
                d=_ImageDraw.Draw(canvas)
                for line in paths:
                    d.line(line,fill=laser_hot+(245,),width=2)
                    d.line(line,fill=laser_core+(255,),width=1)
                tmp=target+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG",compress_level=3,optimize=False);os.replace(tmp,target)

            if not (os.path.isfile(focus_target) and os.path.getsize(focus_target)>256):
                FW,FH=206,309
                glow=_PILImage.new("RGBA",(FW,FH),(0,0,0,0));gd=_ImageDraw.Draw(glow)
                for inset,width,alpha in ((1,10,155),(2,7,210),(3,4,245)):
                    gd.rounded_rectangle((inset,inset,FW-1-inset,FH-1-inset),radius=8,outline=(r,g,b,alpha),width=width)
                if _PILImageFilter is not None:glow=glow.filter(_PILImageFilter.GaussianBlur(3.0))
                fd=_ImageDraw.Draw(glow)
                hot=tuple(min(255,int(v*.28+255*.72)) for v in (r,g,b))
                core=tuple(min(255,int(v*.10+255*.90)) for v in (r,g,b))
                fd.rounded_rectangle((1,1,FW-2,FH-2),radius=8,outline=hot+(255,),width=4)
                fd.rounded_rectangle((2,2,FW-3,FH-3),radius=7,outline=core+(255,),width=2)
                ftmp=focus_target+".tmp.%d"%os.getpid();glow.save(ftmp,"PNG",compress_level=3,optimize=False);os.replace(ftmp,focus_target)
            return (target if os.path.isfile(target) else "",focus_target if os.path.isfile(focus_target) else "")
        except Exception as exc:
            optional_failure("ui.pgv2_material_build",exc);return ("","")

    def _pgv2_schedule_connected_material(self):
        if self._screen_closed or not self.grid_items:return
        source=self._pgv2_material_source()
        if not source:return
        try:
            from .persistent_cache import content_cache_key
            item=self.grid_items[self.index];key=str(content_cache_key(self.profile,self.media_type,item) or "")
        except Exception:key=str(source)
        if not key:return
        # Only the current stable focus matters. Cancel queued obsolete material
        # jobs so the single worker never spends time painting cards already left.
        try:
            self._pgv2_material_cancel.set()
            if self._pgv2_material_future is not None:self._pgv2_material_future.cancel()
        except Exception:pass
        self._pgv2_material_cancel=threading.Event();cancel=self._pgv2_material_cancel
        self._pgv2_material_pending.clear();self._pgv2_material_token+=1;token=self._pgv2_material_token
        self._pgv2_material_pending.add(key);result_q=self._pgv2_material_jobs;build_material=type(self)._pgv2_build_connected_material
        def worker():
            if cancel.is_set():return
            path,focus_path=build_material(source,key)
            if cancel.is_set():return
            result_q.put((token,key,path,focus_path))
        try:
            future=_PGV2_MATERIAL_EXECUTOR.submit(worker,priority=1,_task_key="pgv2-material:%s:%s"%(self._pgv2_scope,key))
            self._pgv2_material_future=future
            if future.done():
                try:future.result()
                except Exception:self._pgv2_material_pending.discard(key)
        except Exception as exc:
            self._pgv2_material_pending.discard(key);optional_failure("ui.pgv2_material_submit",exc)

    def _pgv2_apply_connected_material(self,token,key,path,focus_path=""):
        if token!=self._pgv2_material_token:return
        if path and os.path.isfile(path):
            try:
                self["pgv2_connected_material"].instance.setPixmapFromFile(path);self["pgv2_connected_material"].show();self._pgv2_material_key=key
            except Exception as exc:optional_failure("ui.pgv2_material_apply",exc)
        if focus_path and os.path.isfile(focus_path):
            try:
                self["selection"].instance.setPixmapFromFile(focus_path);self["selection"].show();self._grid_selection_visual_path=focus_path
                self["selection_adaptive"].hide()
            except Exception as exc:optional_failure("ui.pgv2_focus_apply",exc)

    def _grid_apply_current_selector(self,allow_build=False):
        """Poster Grid V2 owns one exact-size focus; legacy oversized overlay stays disabled."""
        try:self["selection_adaptive"].hide()
        except Exception:pass
        try:
            if not getattr(self,"_grid_selection_visual_path",""):
                neutral=asset(self.selection_asset)
                self["selection"].instance.setPixmapFromFile(neutral);self._grid_selection_visual_path=neutral
            self["selection"].show()
        except Exception as exc:optional_failure("ui.pgv2_selector",exc)

    # PERFLAB15: Poster Grid V2 reuses the official stable behaviour, but strips
    # every adaptive/presentation layer.  The page owns only poster pixmaps, one
    # static selector and the official title-logo handoff.  No backdrop prefetch,
    # rating pass, page mood, per-card chrome or connected 1920x1080 material is
    # allowed in this screen.
    def _apply_debounced_grid_focus(self):
        if self._screen_closed or not self.grid_items:
            return
        try:self._grid_apply_current_selector(allow_build=False)
        except Exception as exc:optional_failure("ui.pgv2_simple_selector",exc)
        try:self._pgv2_schedule_hero()
        except Exception as exc:optional_failure("ui.pgv2_hero_schedule",exc)

    def _grid_schedule_adaptive_selector(self,generation,slot,path):
        return

    def _grid_schedule_page_mood(self,path):
        return

    def _apply_poster_live_hud(self,item=None):
        return

    def _card_rating_layout(self,item,pos,meta_text=None):
        # PGV2 intentionally has no rating/year presentation.
        for _n in ("item_year%d"%pos,"item_star%d"%pos,"item_score%d"%pos):
            try:
                self[_n].hide()
            except Exception:
                pass
        return False

    def _pgv2_schedule_connected_material(self):
        return

    def _pgv2_apply_connected_material(self,token,key,path,focus_path=""):
        try:
            if self["pgv2_connected_material"].instance is not None:
                self["pgv2_connected_material"].instance.setPixmap(None)
            self["pgv2_connected_material"].hide()
        except Exception:
            pass
        try:self["selection_adaptive"].hide()
        except Exception:pass

    def _grid_apply_current_selector(self,allow_build=False):
        # One reusable static focus, identical for every title.
        try:self["selection_adaptive"].hide()
        except Exception:pass
        try:
            stable=asset(self.selection_asset)
            if getattr(self,"_grid_selection_visual_path","")!=stable:
                if self["selection"].instance is not None:
                    self["selection"].instance.setPixmap(None)
                    self["selection"].instance.setPixmapFromFile(stable)
                self._grid_selection_visual_path=stable
            self["selection"].show()
        except Exception as exc:optional_failure("ui.pgv2_static_selector",exc)

    def _pgv2_cinematic_title(self,item,row=None):
        return _pgv2_cinematic_title_detached(self.media_type,item,row)

    def _pgv2_fit_official_title(self,title):
        title=str(title or "").strip()
        try:self["hero_clean_title"].setText(title)
        except Exception:return
        try:
            n=len(title)
            size=72 if n<=18 else 64 if n<=28 else 56 if n<=42 else 48 if n<=58 else 40
            if globals().get("gFont") is not None and self["hero_clean_title"].instance is not None:
                self["hero_clean_title"].instance.setFont(gFont("Regular",size))
        except Exception:pass

    def _pgv2_logo_cache_path(self,tmdb_id,lang):
        return _pgv2_logo_cache_path_detached(self.media_type,tmdb_id,lang)

    def _pgv2_logo_seal_path(self,item):
        return _pgv2_logo_seal_path_detached(self.profile,self.media_type,item)

    def _pgv2_write_logo_seal(self,item,row,logo_path):
        return _pgv2_write_logo_seal_detached(self.profile,self.media_type,item,row,logo_path)

    def _pgv2_read_logo_seal(self,item):
        return _pgv2_read_logo_seal_detached(self.profile,self.media_type,item)

    def _folder_artwork_ready_hook(self,item,data):
        # BLUE cache treats the V2 title-logo probe as part of the durable item package.
        return bool(self._pgv2_read_logo_seal(item))

    def _folder_artwork_finalize_hook(self,item,poster,backdrop,payload,data,cancel_event=None):
        try:
            from .details_authority import resolve_canonical
            fresh=dict(data or {})
            authoritative=resolve_canonical(self.profile,self.media_type,dict(item),cancel_event=cancel_event,settings=(self._grid_settings or {})) or {}
            for k,v in authoritative.items():
                if v not in (None,"",[],{}):fresh[k]=v
            logo,fresh=self._pgv2_resolve_title_logo(dict(item),fresh)
            self._pgv2_write_logo_seal(item,fresh,logo)
            return True
        except Exception as exc:
            optional_failure("ui.pgv2_blue_logo_finalize",exc);return False

    def _pgv2_resolve_title_logo(self,item,row,cancel_event=None,settings=None):
        return _pgv2_resolve_title_logo_detached(self.profile,self.media_type,item,row,cancel_event=cancel_event,settings=settings)

    def _pgv2_record(self,item):
        """HDD/current-focus identity bridge matching Backdrop/Cinematic.

        Poster Grid V2 used to call ``self._record`` even though PremiumGridBase
        does not define that method.  Keep this screen self-contained and use
        the same manifest + Details snapshot + locked-id bridge as the working
        title-logo screens. No network work happens here.
        """
        try:
            from .artwork_v2 import load_manifest
            from .persistent_cache import load_detail_snapshot,load_detail_snapshot_by_tmdb
            art=load_manifest(self.profile,self.media_type,item) or {}
            detail=load_detail_snapshot(self.profile,self.media_type,item) or {}
            if art.get("tmdb_id"):
                direct=load_detail_snapshot_by_tmdb(art.get("media_type") or self.media_type,art.get("tmdb_id")) or {}
                if direct:
                    merged=dict(detail);merged.update(direct);detail=merged
            row=dict(detail);row.update(art)
            if not row.get("tmdb_id") and isinstance(item,dict):
                locked=item.get("_locked_tmdb_id")
                if not locked:
                    try:locked=(getattr(self,"_grid_item_state",{}) or {}).get(id(item),{}).get("tmdb_id")
                    except Exception:locked=None
                if locked:
                    row["tmdb_id"]=locked
                    row["media_type"]=item.get("_locked_tmdb_type") or row.get("media_type") or ("tv" if self.media_type=="series" else "movie")
            return row
        except Exception as exc:
            try:optional_failure("ui.pgv2_record",exc)
            except Exception:pass
            return {}

    def _pgv2_schedule_hero(self):
        """Stable-focus hero handoff with shared seal-first logo resolution."""
        if self._screen_closed or not self.grid_items:return
        item=self.grid_items[self.index]
        try:
            from .persistent_cache import content_cache_key
            key=str(content_cache_key(self.profile,self.media_type,item) or "")
        except Exception:key=str(id(item))
        if not key:return

        try:
            self._pgv2_hero_cancel.set()
            if self._pgv2_hero_future is not None:self._pgv2_hero_future.cancel()
        except Exception:pass
        self._pgv2_hero_cancel=threading.Event();cancel=self._pgv2_hero_cancel
        self._pgv2_hero_pending.clear();self._pgv2_hero_token+=1;token=self._pgv2_hero_token

        row_fast=self._pgv2_record(item) or {}
        visible=self._pgv2_cinematic_title(item,row_fast)
        # Current-canvas shared cache is the fastest path.
        try:
            locked=(item.get("_locked_tmdb_id") if isinstance(item,dict) else None) or (row_fast.get("_locked_tmdb_id") if isinstance(row_fast,dict) else None) or (row_fast.get("tmdb_id") if isinstance(row_fast,dict) else None)
            fast_row=dict(row_fast or {})
            if locked not in (None,""):
                fast_row["_locked_tmdb_id"]=locked;fast_row["tmdb_id"]=locked
                cached=_pgv2_cached_logo_for_policy_detached(self.media_type,locked,fast_row)
                if cached and os.path.isfile(cached) and os.path.getsize(cached)>512:
                    self._pgv2_apply_hero(token,key,item,"",cached,visible,known_missing=False)
                    return
        except Exception as exc:optional_failure("ui.pgv2_cached_logo_fast",exc)

        seal_state,seal_path,_seal=_pgv2_logo_seal_state_detached(self.profile,self.media_type,item,row_fast)
        probe_missing=(_pgv2_title_logo_probe_missing_detached(item,row_fast) if seal_state!="logo" else False)
        # Same-canvas positive seal can paint synchronously; no worker/network hop.
        if seal_state=="logo" and _pgv2_logo_canvas_matches_detached(seal_path,(900,125)):
            self._pgv2_apply_hero(token,key,item,"",seal_path,visible,known_missing=False)
            return
        # A proven negative seal owns presentation immediately and needs no retry
        # until its short TTL expires. A row-level probe is presentation-only and
        # still schedules the resolver in case Fanart/TMDb can promote a logo.
        if seal_state=="missing" or probe_missing:
            self._pgv2_apply_hero(token,key,item,"","",visible,known_missing=True)
            if seal_state=="missing":return
        elif not self._pgv2_has_committed_hero:
            self._pgv2_apply_hero(token,key,item,"","",visible,known_missing=False)

        self._pgv2_hero_pending.add(key)
        profile=dict(self.profile or {});media_type=self.media_type;settings=dict(self._grid_settings or {})
        item_copy=dict(item) if isinstance(item,dict) else item;row_seed=dict(row_fast or {});result_q=self._pgv2_hero_jobs
        def worker():
            if cancel.is_set():return
            fresh=dict(row_seed or {});out_logo="";display_title=visible;logo_state="pending"
            try:
                # Cross-view verified result first. This avoids repeating canonical
                # discovery when PG2/BG/Cinematic already solved the same title.
                state,bridged,_seal=_pgv2_bridge_logo_from_seal_detached(
                    profile,media_type,item_copy,fresh,(900,125),cancel_event=cancel
                )
                if cancel.is_set():return
                if state=="missing":
                    result_q.put((token,key,item_copy,"","",display_title,"missing"));return
                if state=="logo" and bridged:
                    result_q.put((token,key,item_copy,"",bridged,display_title,"logo"));return

                fresh=_pgv2_authoritative_logo_row_detached(profile,media_type,item_copy,fresh,cancel_event=cancel,settings=settings)
                if cancel.is_set():return
                out_logo,fresh=_pgv2_resolve_title_logo_detached(profile,media_type,item_copy,fresh,cancel_event=cancel,settings=settings,canvas_size=(900,125))
                if cancel.is_set():return
                logo_state="logo" if out_logo and os.path.isfile(out_logo) else "missing"
                _pgv2_write_logo_seal_detached(profile,media_type,item_copy,fresh,out_logo)
                try:
                    from .title_clean import display_title as _display_title
                    display_title=_display_title(fresh.get("original_title") or fresh.get("title") or fresh.get("name") or display_title) or display_title
                except Exception:pass
            except Exception as exc:
                optional_failure("ui.pgv2_cinematic_exact_logo",exc);logo_state="pending"
            if cancel.is_set():return
            fitted=out_logo if out_logo and os.path.isfile(out_logo) else ""
            result_q.put((token,key,item_copy,"",fitted,display_title,logo_state))
        try:
            future=_PGV2_HERO_EXECUTOR.submit(worker,priority=0,_task_key="pgv2-hero:%s:%s"%(self._pgv2_scope,key))
            self._pgv2_hero_future=future
            if future.done():
                try:future.result()
                except Exception:self._pgv2_hero_pending.discard(key)
        except Exception as exc:
            self._pgv2_hero_pending.discard(key);optional_failure("ui.pgv2_hero_submit",exc)

    def _pgv2_apply_hero(self,token,key,item,banner,logo,official_title="",known_missing=False):
        if token!=self._pgv2_hero_token:return
        self._pgv2_hero_key=key
        valid_logo=bool(logo and os.path.isfile(logo) and os.path.getsize(logo)>512)
        try:
            self["hero_banner"].hide()
            if valid_logo:
                self["hero_logo"].instance.setPixmapFromFile(logo);self["hero_logo"].show();self["hero_clean_title"].hide();self._pgv2_last_logo_path=logo
            elif known_missing:
                # Current title is confirmed/probed logo-less. Never leave a stale
                # previous identity on screen: show THIS title immediately.
                self["hero_logo"].hide();self._pgv2_last_logo_path=""
                self._pgv2_fit_official_title(official_title or self._pgv2_clean_title(item));self["hero_clean_title"].show()
            else:
                held=str(getattr(self,"_pgv2_last_logo_path","") or "")
                if self._pgv2_has_committed_hero and held and os.path.isfile(held) and os.path.getsize(held)>512:
                    self["hero_logo"].show();self["hero_clean_title"].hide()
                else:
                    self["hero_logo"].hide();self._pgv2_fit_official_title(official_title or self._pgv2_clean_title(item));self["hero_clean_title"].show()
            self._pgv2_has_committed_hero=True
        except Exception as exc:optional_failure("ui.pgv2_hero_apply",exc)

    def _pgv2_drain_hero(self):
        for _ in range(4):
            try:payload=self._pgv2_hero_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            try:
                if len(payload)>=7:token,key,item,banner,logo,official_title,logo_state=payload[:7]
                else:
                    token,key,item,banner,logo,official_title=payload;logo_state="logo" if logo else "pending"
            except Exception:continue
            self._pgv2_hero_pending.discard(key)
            self._pgv2_apply_hero(token,key,item,banner,logo,official_title,known_missing=(logo_state=="missing"))
        for _ in range(4):
            try:token,key,path,focus_path=self._pgv2_material_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            self._pgv2_material_pending.discard(key)
            self._pgv2_apply_connected_material(token,key,path,focus_path)


class PremiumLiveGridScreen(PremiumGridBase):
    skin=LIVE_GRID_SKIN;columns=1;page_size=17;image_size=(64,36);placeholder="us168_live_placeholder_220x132.png";selection_asset="us205_live_selection_neutral.png"
    card_positions=[(58,8+i*60) for i in range(17)]
    PREVIEW_RECT=(870,55,900,506)

    def __init__(self,session,profile,client,media_type,genre,category_title="",live_categories=None):
        # R66: keep the already-filtered browser category rows with the Live
        # screen.  The Player drawer can then switch between Channels and
        # Categories in-place without opening another screen or re-fetching the
        # browser's category menu.
        self._live_categories=[dict(x) for x in (live_categories or []) if isinstance(x,dict)]
        # Live picons persist on HDD and are reused before any network request.
        # This compact Live screen intentionally has no clock/date HUD or timer.
        self.disable_grid_clock=True
        self._preview_old_service=None
        try:self._preview_old_service=session.nav.getCurrentlyPlayingServiceReference()
        except Exception as exc:optional_failure("ui",exc)
        self._active_preview_key=None;self._active_preview_url=None;self._active_preview_item=None;self._active_preview_engine=None
        self._preview_token=0;self._preview_jobs=queue.Queue();self._preview_handle=None;self._preview_task_id=None;self._pending_preview_key=None
        self._preview_pending_url=None;self._preview_pending_item=None;self._preview_pending_engine=None;self._preview_engine_candidates=[];self._preview_engine_pos=0
        self._preview_poll_count=0;self._preview_restore_state=None;self._main_preview_reference=None
        # OpenATV 8.0 compatibility: never run Pillow picon fitting on the
        # Enigma2 GUI thread.  Raw/cached picons paint immediately; the exact
        # trimmed 64x36 derivative is prepared on the bounded cache-I/O lane
        # and swapped in through the existing grid result queue.
        self._live_row_fit_pending=set()
        PremiumGridBase.__init__(self,session,profile,client,media_type,genre,category_title)
        try:self._last_fullscreen_key=str((_GRID_NAV_STATE.get(self._session_nav_key,{}) or {}).get("last_fullscreen_key") or "")
        except Exception:self._last_fullscreen_key=""
        self._preview_ready_timer=eTimer();self._preview_ready_conn=None
        try:self._preview_ready_conn=self._preview_ready_timer.timeout.connect(self._check_preview_ready)
        except Exception:self._preview_ready_timer.callback.append(self._check_preview_ready)
        self["channel_name"]=Label("");self["preview_status"]=Label("")
        self["page_adaptive_bg"]=Pixmap();self["preview_frame"]=Pixmap();self["live_info_bg"]=Pixmap();self["page_label_bg"]=Pixmap()
        self["tech_bg1"]=Pixmap();self["tech_bg2"]=Pixmap();self["tech_bg3"]=Pixmap();self["tech_bg4"]=Pixmap()
        for _ri in range(self.page_size):self["row_adaptive%d"%_ri]=Pixmap()
        self._live_static_chrome_bound=False
        self["epg_hint"]=Label(_("NOW / NEXT"));self["tech1"]=Label(_("AUTO"));self["tech2"]=Label(_("LIVE"));self["tech3"]=Label(_("CHANNEL"));self["tech4"]=Label(_("OK Preview"))
        self["now_progress"]=ProgressBar()
        try:self["now_progress"].setValue(0)
        except Exception as exc:optional_failure("ui",exc)
        self.onLayoutFinish.append(self._prepare_preview_window);self.onLayoutFinish.append(self._apply_live_static_chrome);self.onClose.append(self._stop_live_preview);self.onClose.append(self._stop_live_grid_hooks)
        self["red"].setText(_("Back"));self["green"].setText(_("Favorite"));self["yellow"].setText(_("Previous page"));self["blue"].setText(_("Next page"));self["brand"].setText("")
        try:force_session_silence(self.session,"",force=False)
        except Exception as exc:optional_failure("ui",exc)

    def _grid_set_local(self,slot,path):
        """Paint Live picons without ever blocking the Enigma2 GUI thread.

        Older code trimmed/scaled provider picons with Pillow synchronously here.
        OpenATV 8.0 can spend long enough inside those image operations that the
        whole GUI appears frozen and BACK cannot be processed.  Paint the local
        source immediately, then prepare the exact fitted derivative off-thread.
        """
        source=str(path or "")
        render_path=source
        try:
            is_placeholder=(not source or os.path.basename(source)==os.path.basename(asset(self.placeholder)))
            is_fitted=(source and os.path.basename(os.path.dirname(source))=="live_row_picon_fit")
            # First paint must be GUI-cheap.  ePixmap handles scaling of the
            # already-local source; Pillow work is deferred below.
            result=super(PremiumLiveGridScreen,self)._grid_set_local(slot,render_path)
            if is_placeholder or is_fitted or not os.path.isfile(source):
                return result

            generation=int(getattr(self,"_grid_generation",0) or 0)
            token=(generation,int(slot),source)
            pending=getattr(self,"_live_row_fit_pending",None)
            if pending is None:
                self._live_row_fit_pending=set();pending=self._live_row_fit_pending
            if token in pending or getattr(self,"_screen_closed",False) or getattr(self,"_grid_closed",False):
                return result
            pending.add(token)

            def worker(gen=generation,pos=int(slot),src=source,key=token):
                fitted=src
                try:
                    if getattr(self,"_screen_closed",False) or getattr(self,"_grid_closed",False):
                        return
                    fitted=_fit_live_row_picon_canvas(src,PERSISTENT_GENERATED_DIR,(64,36)) or src
                    if (fitted!=src and os.path.isfile(str(fitted))
                            and gen==int(getattr(self,"_grid_generation",0) or 0)
                            and not getattr(self,"_screen_closed",False)
                            and not getattr(self,"_grid_closed",False)):
                        self._grid_download_jobs.put((gen,pos,str(fitted),"live_fit"))
                except Exception as exc:
                    optional_failure("ui.live_row_render_fit_async",exc)
                finally:
                    try:pending.discard(key)
                    except Exception:pass
            try:
                _LIVE_ROW_FIT_EXECUTOR.submit(worker)
            except Exception as exc:
                pending.discard(token)
                optional_failure("ui.live_row_render_fit_submit",exc)
            return result
        except Exception as exc:
            optional_failure("ui.live_row_render_fit",exc)
            return super(PremiumLiveGridScreen,self)._grid_set_local(slot,render_path)

    def _stop_live_grid_hooks(self):
        for result_queue_name in ("_preview_jobs",):
            result_queue=getattr(self,result_queue_name,None)
            if result_queue is None:continue
            try:
                while True:result_queue.get_nowait()
            except queue.Empty:
                pass
            except Exception as exc:
                optional_failure("ui.live_grid_queue_cleanup",exc)
        for hook_name,callback in (
            ("onLayoutFinish",self._prepare_preview_window),
            ("onLayoutFinish",self._apply_live_static_chrome),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.live_grid_hook_cleanup",exc)

    def _prepare_preview_window(self):
        # session.VideoPicture + Renderer/Pig owns decoder-0 geometry.
        # eVideoWidget applies the preview rectangle when shown and restores full
        # screen automatically when the screen/renderer is hidden.
        try:LOG.info("Live preview Pig renderer ready rect=%s", self.PREVIEW_RECT)
        except Exception as exc:optional_failure("ui",exc)

    def _apply_live_static_chrome(self):
        """Bind the one fixed purple Live theme. No palette/adaptive work."""
        try:
            chrome={
                "page":asset("live_static_page_purple.jpg"),
                "selected":asset("live_static_selection_purple.png"),
                "preview":asset("live_static_preview_purple.png"),
                "info":asset("live_static_info_purple.png"),
                "tech":asset("live_static_tech_purple.png"),
                "row":asset("live_static_row_purple.png"),
                "counter":asset("live_static_counter_purple.png"),
                "accent":(116,58,190),
            }
            self._apply_live_chrome(chrome)
            self._live_static_chrome_bound=True
        except Exception as exc:optional_failure("ui.live_static_chrome",exc)

    # Compatibility alias for any older return/rebind path. It is static now.
    def _apply_live_neutral_chrome(self):
        return self._apply_live_static_chrome()

    def _apply_live_chrome(self,chrome):
        if not isinstance(chrome,dict):return
        # Keep the exact last-good files. Rebinding an already-built PNG is
        # virtually free and avoids regenerating adaptive artwork after Player.
        try:self._live_last_chrome=dict(chrome)
        except Exception:self._live_last_chrome={}
        try:
            for key,widget in (("page","page_adaptive_bg"),("selected","selection"),("preview","preview_frame"),("info","live_info_bg"),("counter","page_label_bg")):
                path=chrome.get(key)
                if path and os.path.isfile(path):self[widget].instance.setPixmapFromFile(path);self[widget].show()
            tech=chrome.get("tech")
            if tech and os.path.isfile(tech):
                for name in ("tech_bg1","tech_bg2","tech_bg3","tech_bg4"):
                    self[name].instance.setPixmapFromFile(tech);self[name].show()
            rowglass=chrome.get("row")
            if rowglass and os.path.isfile(rowglass):
                for _ri in range(self.page_size):
                    self["row_adaptive%d"%_ri].instance.setPixmapFromFile(rowglass)
                    self["row_adaptive%d"%_ri].show()
            accent=chrome.get("accent")
            if accent:
                color="#%02x%02x%02x"%tuple(accent[:3])
                try:self["now_progress"].instance.setForegroundColor(parseColor(color))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["channel_name"].instance.setForegroundColor(parseColor(color))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
        except Exception as exc:optional_failure("ui.live211_apply",exc)

    def _force_live_visual_rebind(self):
        """Restore fixed Live chrome plus retained picons after Full Screen."""
        if getattr(self,"_screen_closed",False):return
        _r58_rebind_mono=time.monotonic()
        try:self._apply_live_static_chrome()
        except Exception as exc:optional_failure("ui.live_force_static",exc)
        for slot,path in list((getattr(self,"_grid_slot_paths",{}) or {}).items()):
            if slot >= len(getattr(self,"grid_items",[]) or []):continue
            try:
                ptr=self._grid_cache_get(path) if path else None
                if ptr is not None:self._grid_set_ptr(slot,ptr)
                elif path and os.path.isfile(path):self._grid_set_local(slot,path)
            except Exception as exc:optional_failure("ui.live_force_art",exc)
        try:self._update_selection()
        except Exception as exc:optional_failure("ui.live_force_selection",exc)
        try:
            LOG.info("PERF58 live_return_rebind page=%s index=%s slots=%s elapsed_ms=%s",self.page,self.index,len(getattr(self,"_grid_slot_paths",{}) or {}),int((time.monotonic()-_r58_rebind_mono)*1000.0))
        except Exception:pass

    def _grid_queue_decode(self,generation,slot,path):
        # beta28: Live picons are tiny, already-local display assets. On OpenBH
        # the shared ePicLoad queue used for large posters can complete without a
        # usable Live pixmap callback on some image/provider combinations. Render
        # the prepared local picon directly into artN and keep ePicLoad for VOD/
        # Series only. This also makes the final Live stage deterministic: once
        # download/cache produced a valid file, the row gets that exact file.
        if generation!=getattr(self,"_grid_generation",generation):
            _live_picon_diag("RENDER_STALE slot=%r generation=%r current=%r path=%r"%(slot,generation,getattr(self,"_grid_generation",None),path))
            return
        _live_picon_diag("RENDER_ENTER slot=%r path=%r exists=%r size=%r"%(slot,path,bool(path and os.path.isfile(path)),(os.path.getsize(path) if path and os.path.isfile(path) else -1)))
        if path and os.path.isfile(path):
            try:
                self._grid_slot_paths[slot]=path
                self._grid_palette_sources[slot]=path
                self._grid_set_local(slot,path)
                _live_picon_diag("RENDER_SET_LOCAL_OK slot=%r channel=%r path=%r widget_instance=%r"%(slot,str((self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else {}).get("name") or ""),path,getattr(self["art%d"%slot],"instance",None) is not None))
                if self.media_type=="itv":
                    try:LOG.info("Live picon direct render slot=%s channel=%r path=%s",slot,str((self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else {}).get("name") or ""),path)
                    except Exception:pass
                return
            except Exception as exc:
                optional_failure("ui.live_direct_picon_render",exc)
        # Defensive fallback only; valid Live cache files should never need it.
        PremiumGridBase._grid_queue_decode(self,generation,slot,path)

    @staticmethod
    def _preview_key(item):
        return str((item or {}).get("id") or (item or {}).get("ch_id") or (item or {}).get("cmd") or (item or {}).get("name") or "")

    def _drain_jobs(self):
        PremiumGridBase._drain_jobs(self)
        if self._screen_closed:return
        while True:
            try:
                job=self._preview_jobs.get_nowait();callback,value,is_error=job[:3];task_id=job[3] if len(job)>3 else None
            except queue.Empty:break
            except Exception:continue
            if task_id is not None and self._preview_task_id is not None and task_id!=self._preview_task_id:continue
            self._preview_handle=None;self._preview_task_id=None
            if callback:
                try:callback(value)
                except Exception:
                    try:self["preview_status"].setText(_("Preview unavailable"))
                    except Exception as exc:optional_failure("ui",exc)
            elif is_error:
                try:self["preview_status"].setText(_("Preview unavailable"))
                except Exception as exc:optional_failure("ui",exc)

    def _cancel_preview_request(self):
        handle=self._preview_handle;self._preview_handle=None;self._preview_task_id=None;self._pending_preview_key=None
        try:self._preview_ready_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        self._preview_pending_url=None;self._preview_pending_item=None;self._preview_pending_engine=None;self._preview_engine_candidates=[];self._preview_engine_pos=0;self._preview_poll_count=0
        if handle is not None:
            try:handle.cancel()
            except Exception as exc:optional_failure("ui",exc)

    def _fit_live_epg_line(self,name,max_size=21,min_size=11):
        """Keep complete NOW/NEXT text visible inside the fixed Live info card."""
        try:
            widget=self[name];inst=widget.instance
            if inst is None:return
            value=" ".join(str(widget.getText() or "").replace("\n"," ").split())
            if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            width=max(120,int(inst.size().width())-12)
            chosen=int(max_size)
            for size in range(int(max_size),int(min_size)-1,-1):
                inst.setFont(gFont("Regular",size))
                try:measured=int(inst.calculateSize().width())
                except Exception:measured=0
                # OpenBH can under-estimate mixed-script shaping a little.
                if measured<=0 or int(measured*1.08)<=width:
                    chosen=size;break
                chosen=max(int(min_size),size-1)
            inst.setFont(gFont("Regular",chosen))
        except Exception as exc:optional_failure("ui.live_epg_font_fit",exc)

    def _update_header(self,item):
        PremiumGridBase._update_header(self,item)
        try:
            self._fit_live_epg_line("rating",21,11)
            self._fit_live_epg_line("meta1",19,11)
            cid=str(item.get("id") or item.get("ch_id") or "");epg=self._epg_cache.get(cid,{})
            self["now_progress"].setValue(int(epg.get("percent") or 0))
        except Exception as exc:optional_failure("ui",exc)

    def _update_selection(self):
        PremiumGridBase._update_selection(self)
        if not self.grid_items:return
        item=self.grid_items[self.index];raw=item.get("name") or item.get("title") or "Channel"
        clean_name=_clean_display_text(_clean_live_channel_name(raw,bool((self._grid_settings or {}).get("clean_titles",True))),80)
        self["channel_name"].setText(clean_name)
        # Responsive one-line title. Keep short names bold/large, reduce only when
        # the actual label gets long enough to risk clipping its 820px card.
        try:
            units=sum(1.65 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in clean_name)
            font_size=32 if units<=26 else (29 if units<=34 else (26 if units<=43 else (23 if units<=53 else 20)))
            if self["channel_name"].instance is not None:self["channel_name"].instance.setFont(gFont("Regular",font_size))
        except Exception as exc:optional_failure("ui.live_title_font",exc)
        q=quality_badges(raw) or _("AUTO");self["tech1"].setText(q[:16])
        self["tech2"].setText(_("Catch-up") if (item.get("allow_archive") or item.get("tv_archive_duration") or item.get("archive")) else _("LIVE"))
        self["tech3"].setText(_("Channel %d")%max(1,(int(self.page or 1)-1)*int(self.page_size or 1)+int(self.index)+1))
        key=self._preview_key(item)
        if self._pending_preview_key:
            self["preview_status"].setText(_("Loading preview..."));self["tech4"].setText(_("Loading...") if key==self._pending_preview_key else _("OK Preview"))
        elif self._active_preview_key:
            self["preview_status"].setText("");self["tech4"].setText(_("OK Full Screen") if key==self._active_preview_key else _("OK Preview"))
        else:
            self["preview_status"].setText("");self["tech4"].setText(_("OK Preview"))

    def _live_nav_total(self):
        try:return max(len(self.grid_items or []),int(self._portal_total or 0))
        except Exception:return len(self.grid_items or [])

    def _live_nav_absolute(self):
        return max(0,(int(self.page or 1)-1)*int(self.page_size or 1)+int(self.index or 0))

    def _live_nav_go_absolute(self,absolute):
        total=self._live_nav_total()
        if total<=0:return
        absolute=max(0,min(total-1,int(absolute)))
        target_page=absolute//self.page_size+1
        target_index=absolute%self.page_size
        if target_page==self.page and self.grid_items:
            self.index=min(target_index,max(0,len(self.grid_items)-1))
            self._update_selection();self._update_page_counter()
        else:
            self.load_page(target_page,target_index)

    def move_up(self):
        """Exact Mini List rule: previous item globally; #1 wraps to final channel."""
        if not self.grid_items or not self._nav_allowed():return
        total=self._live_nav_total()
        if total<=0:return
        current=self._live_nav_absolute()
        self._live_nav_go_absolute((current-1)%total)

    def move_down(self):
        """Exact Mini List rule: next item globally; final channel wraps to #1."""
        if not self.grid_items or not self._nav_allowed():return
        total=self._live_nav_total()
        if total<=0:return
        current=self._live_nav_absolute()
        self._live_nav_go_absolute((current+1)%total)

    def _live_page_shift(self,direction):
        """Exact Mini List LEFT/RIGHT page rule with the same row preserved."""
        if not self.grid_items or not self._nav_allowed():return
        total=self._live_nav_total()
        if total<=0:return
        page_size=max(1,int(self.page_size or 1))
        current_abs=self._live_nav_absolute()
        current_page=current_abs//page_size
        row=current_abs%page_size
        last_page=max(0,(total-1)//page_size)

        if int(direction)<0:
            if current_page<=0:
                target=0
            else:
                target=(current_page-1)*page_size+row
        else:
            if current_page>=last_page:
                target=total-1
            else:
                target=min(total-1,(current_page+1)*page_size+row)
        self._live_nav_go_absolute(target)

    def move_left(self):
        self._live_page_shift(-1)

    def move_right(self):
        self._live_page_shift(1)

    def open_selected(self):
        if self._busy or not self.grid_items:return
        item=self.grid_items[self.index];key=self._preview_key(item)
        if self._active_preview_key==key and self._active_preview_url:
            self._open_preview_fullscreen(item)
        elif key and key==getattr(self,"_last_fullscreen_key",""):
            # Returning to the same folder/channel should not force the viewer
            # through Preview again. Resolve a fresh link and go straight back
            # to Full Screen.
            self._open_direct_fullscreen(item)
        else:
            self._start_preview_request(item)

    def _open_direct_fullscreen(self,item):
        if not isinstance(item,dict) or self._screen_closed:return
        self._preview_token+=1;token=self._preview_token;self._cancel_preview_request()
        self["preview_status"].setText(_("Opening channel..."));self["tech4"].setText(_("Opening..."))
        def work():return self.client.create_link(item,"itv")
        def ok(url):
            if token!=self._preview_token or self._screen_closed:return
            url=url.strip() if isinstance(url,str) else ""
            if not url:
                try:self["preview_status"].setText(_("Stream unavailable"));self["tech4"].setText(_("OK Preview"))
                except Exception as exc:optional_failure("ui",exc)
                return
            cfg=load_settings();engine=_configured_playback_engine(cfg)
            self._launch_live_fullscreen(item,url,engine,False)
        def fail(err):
            if token==self._preview_token and not self._screen_closed:
                try:self["preview_status"].setText(_("Stream unavailable"));self["tech4"].setText(_("OK Preview"))
                except Exception as exc:optional_failure("ui",exc)
        try:
            handle=TASKS.submit(work,self._preview_jobs,ok=ok,fail=fail);self._preview_handle=handle;self._preview_task_id=getattr(handle,"task_id",None) if handle is not None else None
        except Exception:fail(None)

    def _start_preview_request(self,item):
        if not isinstance(item,dict) or self._screen_closed:return
        self._preview_token+=1;token=self._preview_token;self._cancel_preview_request();key=self._preview_key(item);self._pending_preview_key=key
        self._perf58_preview_req_mono=time.monotonic()
        try:LOG.info("PERF58 live_preview_request page=%s index=%s key=%s",self.page,self.index,key)
        except Exception:pass
        self["preview_status"].setText(_("Loading preview..."));self["tech4"].setText(_("Loading..."))
        def work():return self.client.create_link(item,"itv")
        def ok(url):
            if token!=self._preview_token or self._screen_closed:return
            if not isinstance(url,str) or not url.strip():
                self._pending_preview_key=None;self["preview_status"].setText(_("Preview unavailable"));self["tech4"].setText(_("OK Preview"));return
            try:LOG.info("PERF58 live_preview_link_ready page=%s index=%s elapsed_ms=%s",self.page,self.index,int((time.monotonic()-getattr(self,"_perf58_preview_req_mono",time.monotonic()))*1000.0))
            except Exception:pass
            self._begin_preview_video(url.strip(),item,key)
        def fail(err):
            if token==self._preview_token:
                self._pending_preview_key=None
                try:self["preview_status"].setText(_("Preview unavailable"));self["tech4"].setText(_("OK Preview"))
                except Exception as exc:optional_failure("ui",exc)
        try:
            handle=TASKS.submit(work,self._preview_jobs,ok=ok,fail=fail);self._preview_handle=handle;self._preview_task_id=getattr(handle,"task_id",None) if handle is not None else None
        except Exception:fail(None)

    def _preview_main_engines(self,url):
        cfg=load_settings()
        try:engine=int(cfg.get("service_type",4097))
        except Exception:engine=4097
        if engine not in (1,4097,5001,5002,8193):engine=4097
        return [engine]


    def _begin_preview_video(self,url,item,key):
        self._perf58_preview_video_mono=time.monotonic()
        self._preview_restore_state=(self._active_preview_key,self._active_preview_url,self._active_preview_item,self._active_preview_engine)
        self._preview_pending_url=url;self._preview_pending_item=dict(item);self._pending_preview_key=key;self._preview_engine_candidates=self._preview_main_engines(url);self._preview_engine_pos=0
        self._try_preview_engine()

    def _try_preview_engine(self):
        try:self._preview_ready_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        if self._screen_closed:return
        if self._preview_engine_pos>=len(self._preview_engine_candidates):self._preview_failed_restore();return
        engine=self._preview_engine_candidates[self._preview_engine_pos];self._preview_engine_pos+=1;self._preview_pending_engine=engine;self._preview_poll_count=0
        url=self._preview_pending_url or "";item=self._preview_pending_item or {}
        try:
            ref=eServiceReference(int(engine),0,url);ref.setName(str(item.get("name") or item.get("title") or "Live preview"))
            result=self.session.nav.playService(ref)
            if result is False:raise RuntimeError("engine %s rejected preview"%engine)
            self._main_preview_reference=ref
            self["preview_status"].setText(_("Starting preview..."))
            try:LOG.info("Live preview Pig/decoder0 start engine=%s",engine)
            except Exception as exc:optional_failure("ui",exc)
            self._preview_ready_timer.start(250,True)
        except Exception as exc:
            try:LOG.exception("Live preview decoder0 start failed: %s",exc)
            except Exception as exc:optional_failure("ui",exc)
            self._try_preview_engine()

    @staticmethod
    def _read_decoder_dimension(path):
        try:
            raw=open(path,"r").read().strip().lower()
            if not raw:return 0
            if raw.startswith("0x"):raw=raw[2:]
            try:return int(raw,16)
            except Exception:return int(raw,10)
        except Exception:return 0

    def _main_video_size(self):
        width=height=0
        try:
            current=self.session.nav.getCurrentlyPlayingServiceReference()
            if current is None:return 0,0
            service=self.session.nav.getCurrentService();info=service and service.info()
            if info is not None:
                width=int(info.getInfo(iServiceInformation.sVideoWidth) or 0);height=int(info.getInfo(iServiceInformation.sVideoHeight) or 0)
        except Exception:width=height=0
        if width<=0 or height<=0:
            for base in ("/proc/stb/vmpeg/0","/proc/stb/video/0"):
                w=self._read_decoder_dimension(base+"/xres");h=self._read_decoder_dimension(base+"/yres")
                if w>0 and h>0:width,height=w,h;break
        return width,height

    def _check_preview_ready(self):
        if self._screen_closed:return
        width,height=self._main_video_size()
        current_ok=False
        try:
            cur=self.session.nav.getCurrentlyPlayingServiceReference()
            current_ok=(cur is not None and self._main_preview_reference is not None and cur.toString()==self._main_preview_reference.toString())
        except Exception:current_ok=False
        if current_ok and width>0 and height>0:
            key=self._pending_preview_key;item=self._preview_pending_item or {};url=self._preview_pending_url or ""
            self._active_preview_key=key;self._active_preview_url=url;self._active_preview_item=dict(item);self._active_preview_engine=self._preview_pending_engine
            self._pending_preview_key=None;self._preview_pending_url=None;self._preview_pending_item=None;self._preview_restore_state=None
            self["preview_status"].setText("");current=self.grid_items[self.index] if self.grid_items else {}
            self["tech4"].setText(_("OK Full Screen") if self._preview_key(current)==self._active_preview_key else _("OK Preview"))
            try:LOG.info("Live preview Pig/decoder0 ready %sx%s",width,height)
            except Exception as exc:optional_failure("ui",exc)
            try:
                _r58_now=time.monotonic()
                LOG.info("PERF58 live_preview_ready page=%s index=%s size=%sx%s video_ms=%s total_ms=%s",self.page,self.index,width,height,int((_r58_now-getattr(self,"_perf58_preview_video_mono",_r58_now))*1000.0),int((_r58_now-getattr(self,"_perf58_preview_req_mono",_r58_now))*1000.0))
            except Exception:pass
            return
        self._preview_poll_count+=1
        if self._preview_poll_count<24:self._preview_ready_timer.start(250,True);return
        try:LOG.warning("Live preview Pig/decoder0 engine %s never reported video",self._preview_pending_engine)
        except Exception as exc:optional_failure("ui",exc)
        self._try_preview_engine()

    def _preview_failed_restore(self):
        # Deterministic preview: one start attempt for the requested channel.
        # Never recreate the previous decoder/service automatically when a new
        # preview fails; repeated hidden restore cycles were visible in dmesg as
        # VIDEO_STOP/VIDEO_PLAY churn and can leak Broadcom native memory.
        self._pending_preview_key=None
        failed_url=self._preview_pending_url or ""
        self._preview_pending_url=None;self._preview_pending_item=None;self._preview_pending_engine=None
        self._preview_restore_state=None;self._main_preview_reference=None
        self._preview_engine_candidates=[];self._preview_engine_pos=0
        self["preview_status"].setText(_("Preview unavailable"));self["tech4"].setText(_("OK Preview"))
        try:force_session_silence(self.session,failed_url,force=True)
        except Exception as exc:optional_failure("ui.preview_failed_stop",exc)


    def _open_preview_fullscreen(self,item):
        url=self._active_preview_url
        if not url:return self._start_preview_request(item)
        cfg=load_settings();engine=_configured_playback_engine(cfg)
        self._launch_live_fullscreen(item,url,engine,True)

    def _launch_live_fullscreen(self,item,url,engine,reuse_current):
        name=str(item.get("name") or item.get("title") or "Live channel")
        add_recently_played(self.profile,"itv",item)
        key=self._preview_key(item);selected_index=self.index
        try:LOG.info("PERF58 live_fullscreen_launch page=%s index=%s reuse=%s engine=%s",self.page,selected_index,bool(reuse_current),engine)
        except Exception:pass
        self._last_fullscreen_key=key
        try:
            _state=dict(_GRID_NAV_STATE.get(self._session_nav_key,{}) or {})
            _state["last_fullscreen_key"]=key
            _GRID_NAV_STATE[self._session_nav_key]=_state
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        def returned(result=None):
            if self._screen_closed:return

            # UltraStalkerPlayer EXIT already hard-stops the owned IPTV service
            # and restores the receiver service captured at the plugin boundary.
            # Do NOT call force_session_silence() here: its native stop would kill
            # that just-restored TV service. Do NOT auto-start the portal preview
            # either: that was recreating the fullscreen channel behind the UI,
            # leaving its audio alive until another service replaced it.
            self._main_preview_reference=None
            self._active_preview_key=None;self._active_preview_url=None;self._active_preview_item=None;self._active_preview_engine=None
            self._pending_preview_key=None;self._preview_pending_url=None;self._preview_pending_item=None
            self._preview_pending_engine=None;self._preview_engine_candidates=[];self._preview_engine_pos=0
            self["preview_status"].setText("");self["tech4"].setText(_("OK Preview"))
            self.index=max(0,min(selected_index,len(self.grid_items)-1)) if self.grid_items else 0

            # The restored receiver TV remains the service shown by the Pig/video
            # window. Rebind all Ultra Stalker adaptive/glass layers around it.
            _r58_return_mono=time.monotonic()
            try:self._force_live_visual_rebind()
            except Exception as exc:optional_failure("ui.live_return_chrome",exc)
            try:LOG.info("PERF58 live_fullscreen_return page=%s index=%s rebind_total_ms=%s",self.page,self.index,int((time.monotonic()-_r58_return_mono)*1000.0))
            except Exception:pass
        payload=_player_payload(item,self.profile,media_type="itv")
        payload["_player_client_ref"]=self.client;payload["_live_client_ref"]=self.client
        try:
            _cid=str(item.get("id") or item.get("ch_id") or item.get("channel_id") or item.get("stream_id") or "")
            _summary=(getattr(self,"_epg_cache",{}) or {}).get(_cid,{}) if _cid else {}
            if isinstance(_summary,dict):
                if _summary.get("now"):payload["now"]=str(_summary.get("now"))
                if _summary.get("next"):payload["next"]=str(_summary.get("next"))
        except Exception as exc:optional_failure("ui.live_player_epg_payload",exc)
        snapshot=[]
        for _src in list(self.grid_items or []):
            if not isinstance(_src,dict):continue
            _row=dict(_src)
            try:
                _cid=str(_row.get("id") or _row.get("ch_id") or _row.get("channel_id") or _row.get("stream_id") or "")
                _summary=(getattr(self,"_epg_cache",{}) or {}).get(_cid,{}) if _cid else {}
                if isinstance(_summary,dict):
                    if _summary.get("now"):_row["now"]=str(_summary.get("now"))
                    if _summary.get("next"):_row["next"]=str(_summary.get("next"))
            except Exception:pass
            snapshot.append(_row)
        absolute_index=max(0,(int(self.page)-1)*int(self.page_size)+int(selected_index))
        payload["_live_folder_channels"]=snapshot
        payload["_live_absolute_index"]=absolute_index
        payload["_live_folder_total"]=int(self._portal_total or len(snapshot))
        payload["_live_page_size"]=int(self.page_size)
        payload["_live_folder_page"]=int(self.page)
        payload["_live_folder_title"]=str(self.category_title or _("Live TV"))
        payload["_live_category_id"]=str(self.genre or "*")
        payload["_live_categories"]=[dict(x) for x in (getattr(self,"_live_categories",[]) or []) if isinstance(x,dict)]
        payload["_live_page_loader"]=self._grid_page_data
        payload["_live_client_ref"]=self.client
        _r58_open_mono=time.monotonic()
        self.session.openWithCallback(returned,UltraStalkerPlayer,url,name,"itv",engine,payload,bool(reuse_current))
        try:LOG.info("PERF58 live_fullscreen_open_return page=%s index=%s elapsed_ms=%s",self.page,selected_index,int((time.monotonic()-_r58_open_mono)*1000.0))
        except Exception:pass

    def _stop_live_preview(self):
        try:self._preview_ready_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._preview_ready_conn is not None:self._preview_ready_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._check_preview_ready in self._preview_ready_timer.callback:self._preview_ready_timer.callback.remove(self._check_preview_ready)
        except Exception as exc:optional_failure("ui.preview_timer_callback",exc)
        preview_url=self._active_preview_url or self._preview_pending_url or ""
        self._preview_token+=1;self._cancel_preview_request()
        self._main_preview_reference=None
        self._active_preview_key=None;self._active_preview_url=None;self._active_preview_item=None;self._active_preview_engine=None
        try:force_session_silence(self.session,preview_url,force=True)
        except Exception as exc:optional_failure("ui.preview_close_stop",exc)


