import threading
import re
"""Content details screen extracted from ui.py without changing class behavior."""

from . import _
from .age_rating import display_certification
from .localization import current_language
from Screens.Screen import Screen
from .ui_async import AsyncScreenMixin
from .ui_image_loader import ImageLoaderMixin
from .ui_transition import TransitionMixin
from .ui_fixed_adaptive import fixed_home_assets, cleanup_legacy_application_outputs
import time
import json
import tempfile
import os
import hashlib
import gc
import unicodedata
import weakref
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag
from .core.call_compat import call_compatible
from .log import diagnostic_failure
from .tmdb import TMDBClient
from .fanart import fetch_artwork as _fanart_fetch
from .storage import load_api_keys
from .ui_dynamic_chrome import canonical_dynamic_details_key
from .ui_settings_inline_choice import SettingsInlineChoiceOverlay
from .ui_parts.catalog import strip_arabic_tashkeel as _shared_strip_arabic_tashkeel

from twisted.internet import reactor
_DETAILS_TITLE_LOGO_API = None
def _details_title_logo_api():
    global _DETAILS_TITLE_LOGO_API
    if _DETAILS_TITLE_LOGO_API is None:
        from .title_logo_runtime import valid_ultra_title_logo
        from .title_logo_ultra import resolve_ultra_title_logo, ultra_title_logo_cached
        _DETAILS_TITLE_LOGO_API = (valid_ultra_title_logo, resolve_ultra_title_logo, ultra_title_logo_cached)
    return _DETAILS_TITLE_LOGO_API

def valid_ultra_title_logo(*args, **kwargs):
    return _details_title_logo_api()[0](*args, **kwargs)
def resolve_ultra_title_logo(*args, **kwargs):
    return _details_title_logo_api()[1](*args, **kwargs)
def ultra_title_logo_cached(*args, **kwargs):
    return _details_title_logo_api()[2](*args, **kwargs)

DETAIL_SKIN = ""


def _details_backdrop_truth(path):
    """Physical Details truth: a real validated landscape HDD master only."""
    try:
        from .artwork_v2 import _valid_backdrop
        return bool(_valid_backdrop(str(path or "")))
    except Exception:
        return False

def configure_details_screen(**deps):
    globals().update(deps)
    if "DETAIL_SKIN" in deps:
        ContentDetailsScreen.skin = deps["DETAIL_SKIN"]


def _visual_source_fingerprint(path):
    """Cheap binding for reproducible Details derivatives to their poster source."""
    try:
        path=os.path.realpath(str(path or ""))
        if not path or not os.path.isfile(path):
            return ""
        st=os.stat(path)
        mtime_ns=getattr(st,"st_mtime_ns",int(float(st.st_mtime)*1000000000))
        return hashlib.sha1((path+"|%s|%s"%(int(mtime_ns),int(st.st_size))).encode("utf-8","ignore")).hexdigest()
    except Exception:
        return ""


_ARAB_CAST_COUNTRY_TOKENS = {
    "EG","LB","SA","AE","KW","QA","BH","OM","JO","SY","IQ","PS","YE","MA","DZ","TN","LY","SD"
}

def _person_name_is_latin(value):
    """True only when every Unicode letter in a person name is Latin-script."""
    letters=[]
    for ch in str(value or ""):
        try:
            if unicodedata.category(ch).startswith("L"):
                letters.append(ch)
        except Exception:
            pass
    if not letters:
        return False
    for ch in letters:
        try:
            if "LATIN" not in unicodedata.name(ch, ""):
                return False
        except Exception:
            return False
    return True

def _details_is_arabic_work(data, visible_title=""):
    row=data if isinstance(data,dict) else {}
    if re.search(r"[\u0600-\u06ff]",str(visible_title or "")):
        return True
    for key in ("original_language","language","lang","audio_language"):
        raw=str(row.get(key) or "").strip().lower().replace("_","-")
        if raw=="ar" or raw.startswith("ar-"):
            return True
    country=row.get("origin_country") or row.get("country_code") or row.get("country") or row.get("production_countries") or ""
    values=[]
    if isinstance(country,(list,tuple,set)):
        values=list(country)
    else:
        values=[country]
    aliases=("egypt","lebanon","saudi","emirates","kuwait","qatar","bahrain","oman","jordan","syria","iraq","palestine","yemen","morocco","algeria","tunisia","libya","sudan")
    for value in values:
        if isinstance(value,dict):
            value=value.get("iso_3166_1") or value.get("code") or value.get("name") or ""
        raw=str(value or "").strip();upper=raw.upper();low=raw.casefold()
        if upper in _ARAB_CAST_COUNTRY_TOKENS or any(x in low for x in aliases):
            return True
    return False

def _pick_latin_person_name(client, actor):
    """Pick an English/Latin display name, asking TMDB person details only when needed."""
    if not isinstance(actor,dict):
        return ""
    candidates=[str(actor.get("name") or "").strip(),str(actor.get("original_name") or "").strip()]
    for value in candidates:
        if value and _person_name_is_latin(value):
            return value
    person_id=actor.get("id")
    if not person_id:
        return ""
    try:
        person=client._get("/person/%s"%int(person_id),{"language":"en-US"}) or {}
    except Exception:
        person={}
    candidates=[str(person.get("name") or "").strip()] + [str(x or "").strip() for x in (person.get("also_known_as") or [])]
    for value in candidates:
        if value and _person_name_is_latin(value):
            return value
    return ""

def _clean_year_value(value):
    raw=str(value or "").strip()
    m=re.search(r"(?:19|20)\d{2}",raw)
    return m.group(0) if m else raw[:4]


def _age_rating_display(value):
    return display_certification(value)

def _detail_genre_value(item):
    """Return real content genre, never an Xtream/M3U category label in disguise."""
    row=item if isinstance(item,dict) else {}
    value=row.get("genre") or row.get("genres") or ""
    if isinstance(value,(list,tuple)):
        value=" / ".join(str(x) for x in value[:3] if str(x or "").strip())
    value=str(value or "").strip()
    # Raw M3U/Xtream catalogue rows commonly copy group/category into ``genre``.
    # That is navigation metadata, not movie/series metadata, and must not paint
    # the Details genre pill while the canonical HDD record is loading.
    if value and (row.get("_m3u_series") or row.get("_xtream")):
        norm=lambda x:re.sub(r"\s+"," ",str(x or "").strip().casefold())
        current=norm(value)
        categories={norm(row.get(k)) for k in ("category_name","category","category_id","group","group_title") if row.get(k) not in (None,"")}
        if current and current in categories:
            value=""
    if not value and not (row.get("_m3u_series") or row.get("_xtream")):
        value=str(row.get("category_name") or "").strip()
    return value


DETAILS_GLASS_MENU_SKIN = """
<screen name="DetailsAdaptiveMenuScreen" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
 <widget name="frame" position="488,242" size="944,596" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
 <widget name="inner" position="526,342" size="868,372" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
 <widget name="title" position="552,282" size="816,48" font="Regular;32" foregroundColor="#ffffff" transparent="1" zPosition="4" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="list" position="558,360" size="804,336" scrollbarMode="showNever" transparent="1" zPosition="5"/>
 <widget name="hint" position="552,766" size="816,30" font="Regular;17" halign="center" foregroundColor="#a9c5d3" transparent="1" zPosition="5" shadowColor="#000000" shadowOffset="1,1"/>
</screen>
"""

def _strip_arabic_tashkeel(value):
    """Details description display-only Arabic tashkeel cleanup."""
    return _shared_strip_arabic_tashkeel(value)


class DetailsAdaptiveMenuScreen(Screen):
    """Compact copy of the adaptive subtitle chooser, sized for Details actions."""
    skin=DETAILS_GLASS_MENU_SKIN
    def __init__(self,session,title,choices,source_path=""):
        Screen.__init__(self,session)
        from .services.player_native import SubtitleGlassList, _subtitle_glass_assets
        self._glass_builder=_subtitle_glass_assets
        self._choices=[(_(str(c[0])),c[1]) if isinstance(c,(tuple,list)) and len(c)>1 else c for c in list(choices or [])]
        self._source_path=str(source_path or "")
        self["frame"]=Pixmap();self["inner"]=Pixmap()
        self["title"]=Label(_(str(title or "Options")))
        self["hint"]=Label(_("OK  Select   •   BACK  Close   •   UP / DOWN  Navigate"))
        self["list"]=SubtitleGlassList(self._choices,width=804,item_height=84)
        self["actions"]=ActionMap(["OkCancelActions","DirectionActions"],{
            "ok":self.accept,"cancel":self.cancel_close,"up":self.up,"down":self.down,
            "left":self.page_up,"right":self.page_down},-2)
        self["list"].onSelectionChanged.append(self._selection_changed)
        self.onLayoutFinish.append(self._layout_ready)

    def _layout_ready(self):
        # Reuse the subtitle chooser recipe, but render exact native popup sizes.
        # Details actions deliberately use 66px cards inside 84px list rows: the
        # 18px breathing room keeps all four options visually separated and
        # evenly aligned without changing the shared subtitle/player renderer.
        # The old Details menu clipped 1280x800 subtitle panels into 940x560 widgets,
        # which produced the broken/discontinuous borders visible on receiver.
        base=self._glass_builder(self._source_path,944,804,66)
        assets=dict(base or {})
        try:
            from PIL import Image as _DImg
            cache=os.path.join(PERSISTENT_GENERATED_DIR,"details_menu_exact")
            os.makedirs(cache,mode=0o700,exist_ok=True)
            for key,size in (("frame",(944,596)),("inner",(868,372))):
                src=str((base or {}).get(key) or "")
                if not src or not os.path.isfile(src):continue
                stamp=str(int(os.path.getmtime(src)))
                target=os.path.join(cache,hashlib.sha1((src+"|"+stamp+"|"+key+"|exact-v1").encode("utf-8","ignore")).hexdigest()[:20]+".png")
                if not os.path.isfile(target):
                    with _DImg.open(src) as im:
                        im=im.convert("RGBA").resize(size,_DImg.Resampling.LANCZOS)
                        im.save(target,"PNG")
                assets[key]=target
        except Exception as exc:optional_failure("ui.details_menu_exact_assets",exc)
        for name,key in (("frame","frame"),("inner","inner")):
            try:
                path=assets.get(key)
                if path and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
            except Exception as exc:optional_failure("ui.details_menu_panel",exc)
        self["list"].set_assets(assets.get("row"),assets.get("row_selected"))
        try:
            if self["list"].instance is not None:self["list"].instance.setSelectionEnable(0)
        except Exception:pass
        self["list"].set_choices(self._choices,0)

    def _selection_changed(self):
        try:self["list"].selection_changed()
        except Exception as exc:optional_failure("ui.details_menu_selection",exc)
    def up(self):
        try:self["list"].up()
        except Exception:pass
    def down(self):
        try:self["list"].down()
        except Exception:pass
    def page_up(self):
        try:self["list"].pageUp()
        except Exception:pass
    def page_down(self):
        try:self["list"].pageDown()
        except Exception:pass
    def accept(self):
        choice=self["list"].selected_choice()
        if choice is not None:self.close(choice)
    def cancel_close(self):self.close(None)



HERO_PIN_TOAST_SKIN = """
<screen name="HeroPinToastScreen" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
 <widget name="panel" position="580,420" size="760,214" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
 <widget name="check" position="624,486" size="64,64" alphatest="blend" scale="1" transparent="1" zPosition="3"/>
 <widget name="title" position="716,468" size="560,40" font="Regular;28" foregroundColor="#ffffff" transparent="1" zPosition="4" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="message" position="716,516" size="560,58" font="Regular;19" foregroundColor="#b9d7e6" transparent="1" valign="top" zPosition="4" shadowColor="#000000" shadowOffset="1,1"/>
</screen>
"""

class HeroPinToastScreen(Screen):
    """Small self-closing Ultra Stalker notice for explicit Hero selection."""
    skin=HERO_PIN_TOAST_SKIN
    def __init__(self,session,title,message,timeout_ms=1800):
        Screen.__init__(self,session)
        self["panel"]=Pixmap();self["check"]=Pixmap()
        self["title"]=Label(_(str(title or "Hero selected")))
        self["message"]=Label(_(str(message or "")))
        self["actions"]=ActionMap(["OkCancelActions"],{"ok":self.close,"cancel":self.close},-2)
        self._timeout=max(900,int(timeout_ms or 1800));self._timer=eTimer();self._timer_conn=None
        try:self._timer_conn=self._timer.timeout.connect(self.close)
        except Exception:self._timer.callback.append(self.close)
        self.onLayoutFinish.append(self._ready)
        self.onClose.append(self._cleanup)
    def _ready(self):
        try:
            panel=asset("us66_neutral_panel.png")
            if panel and os.path.isfile(panel) and self["panel"].instance is not None:
                self["panel"].instance.setPixmapFromFile(panel);self["panel"].show()
        except Exception:pass
        try:
            icon=asset("settings_icons_40/progress_safe.png")
            if icon and os.path.isfile(icon) and self["check"].instance is not None:
                self["check"].instance.setPixmapFromFile(icon);self["check"].show()
        except Exception:pass
        try:self._timer.start(self._timeout,True)
        except Exception:pass
    def _cleanup(self):
        try:self._timer.stop()
        except Exception:pass
        try:
            if self._timer_conn is not None:self._timer_conn.disconnect()
        except Exception:pass

class ContentDetailsScreen(Screen, AsyncScreenMixin, ImageLoaderMixin, TransitionMixin):
    skin = DETAIL_SKIN

    def __init__(self, session, profile, client, media_type, item):
        Screen.__init__(self, session)
        self._async_init(); self.onClose.append(self._stop_async); self.onClose.append(self._image_stop); self.onClose.append(self._release_details_pixmaps); self.onClose.append(self._runtime_details_close)
        self._ui_diag_open_mono=time.monotonic();_ui_diag("ui_open",screen="details",media_type=media_type)
        self.profile=profile; self.client=client; self.media_type=media_type
        self._runtime_artwork_logged=False
        _runtime_endurance_log("details_open",media_type=self.media_type)
        _raw_item=dict(item) if isinstance(item,dict) else {}
        _fast_open=bool(_raw_item.pop("_ultra_fast_open",False))
        _fast_bundle=_raw_item.pop("_ultra_fast_bundle",{}) if isinstance(_raw_item,dict) else {}
        _search_seed_snapshot=_raw_item.pop("_search_seed_snapshot",{}) if isinstance(_raw_item,dict) else {}
        _search_seed_trusted=bool(_raw_item.pop("_search_seed_trusted",False)) if isinstance(_raw_item,dict) else False
        self._search_visual_peers=_raw_item.pop("_search_visual_peers",[]) if isinstance(_raw_item,dict) else []
        self._search_force_complete=bool(_raw_item.pop("_search_force_complete",False)) if isinstance(_raw_item,dict) else False
        self._search_peer_sync_fp=""
        self._is_xtream_item=bool(_raw_item.get("_xtream"))
        _handoff_clean_backdrop=str(_raw_item.get("_details_clean_backdrop_source") or "")
        _handoff_clean_locked=bool(_raw_item.get("_details_clean_backdrop_locked") and _handoff_clean_backdrop and os.path.isfile(_handoff_clean_backdrop))
        # Dual-source policy: preserve raw provider artwork/metadata for every
        # Movies/Series provider. TMDB may upgrade it later, but never erase it.
        self._portal_item = dict(_raw_item)
        item = dict(_raw_item)
        # release: Series entered from Poster Grid gets an immediate shell from
        # the already-visible card/backdrop. Persistent metadata is allowed to
        # enrich after layout instead of blocking Screen construction on HDD I/O.
        # Metadata is tiny compared with artwork.  Even the fast-open path reads
        # the one shared HDD snapshot so Portal/Xtream/M3U paint identical fields
        # on first frame instead of waiting for a later provider/network pass.
        self._local_detail_cache = load_shared_detail_snapshot(profile,media_type,self._portal_item)
        if isinstance(self._local_detail_cache,dict) and self._local_detail_cache and not identity_cache_compatible(self._portal_item,self._local_detail_cache):
            self._local_detail_cache=dict(self._local_detail_cache)
            self._local_detail_cache["_identity_stale"]=True
            self._local_detail_cache.pop("tmdb_id",None)
            self._local_detail_cache.pop("poster_local",None)
            self._local_detail_cache.pop("backdrop_local",None)
            # A rejected identity must not keep poisoning Details through its old
            # UK/cast/genre/overview overlay after the id itself was discarded.
            self._local_detail_cache.pop("item_overlay",None)
        if isinstance(_search_seed_snapshot,dict) and _search_seed_snapshot.get("tmdb_id"):
            try:
                if _search_seed_trusted or identity_cache_compatible(self._portal_item,_search_seed_snapshot):
                    current=dict(self._local_detail_cache or {})
                    # Search already proved title/year/media compatibility. Keep
                    # that trust scoped to presentation/cache identity only; the
                    # selected copy still owns its profile/id/cmd for playback.
                    merged=dict(current);merged.update(_search_seed_snapshot)
                    self._local_detail_cache=merged
                    if _search_seed_trusted:
                        self._portal_item["_ultra_search_verified_tmdb_id"]=_search_seed_snapshot.get("tmdb_id")
            except Exception as exc:optional_failure("ui.search_peer_seed",exc)
        self._persistent_visual_bundle = (dict(_fast_bundle) if _fast_open and isinstance(_fast_bundle,dict)
                                          else _load_visual_bundle(profile,media_type,self._portal_item,self._local_detail_cache))
        _pvb=self._persistent_visual_bundle if isinstance(self._persistent_visual_bundle,dict) else {}
        # Component-level hard locks.  Each visual becomes immutable as soon as
        # its final HDD file exists; we never wait for every other component.
        self._persistent_poster_locked=bool(_pvb.get("poster_final") or (_pvb.get("poster") and os.path.isfile(str(_pvb.get("poster") or ""))))
        self._persistent_detail_poster_locked=bool(_pvb.get("detail_poster_final") or (_pvb.get("detail_poster") and os.path.isfile(str(_pvb.get("detail_poster") or ""))))
        self._persistent_backdrop_source_locked=bool(_pvb.get("backdrop_source_final") or (_pvb.get("backdrop") and os.path.isfile(str(_pvb.get("backdrop") or ""))))
        self._persistent_backdrop_locked=bool(_pvb.get("backdrop_final") or (_pvb.get("backdrop_present") and os.path.isfile(str(_pvb.get("backdrop_present") or ""))))
        # release: a legacy/prepared presentation backdrop is never allowed to
        # freeze Details against a newer verified canonical TMDB/Fanart backdrop.
        # Poster locks remain untouched. Backdrop presentation may be refreshed
        # from the same canonical master whenever the resolver produces one.
        if self.media_type in ("vod","series") and not _handoff_clean_locked:
            self._persistent_backdrop_source_locked=False
            self._persistent_backdrop_locked=False
        _pvb_chrome=_pvb.get("chrome") if isinstance(_pvb.get("chrome"),dict) else {}
        self._persistent_adaptive_locked=bool(
            _pvb.get("adaptive_final") or _pvb.get("details_adaptive_ready") or
            (_pvb.get("theme") and os.path.isfile(str(_pvb.get("theme") or "")) and
             _pvb_chrome.get("panel_detail") and _pvb_chrome.get("overview_detail"))
        )
        self._persistent_visual_frozen=bool(self._persistent_poster_locked and self._persistent_backdrop_locked and self._persistent_adaptive_locked)
        if isinstance(self._local_detail_cache,dict) and self._local_detail_cache:
            merged=dict(self._portal_item)
            # Metadata/artwork IDs are local-first, but playback command and the
            # portal display identity always remain authoritative.
            cached_item=self._local_detail_cache.get("item_overlay") or {}
            if self._local_detail_cache.get("_details_authority_ready"):
                # First frame uses the canonical raw TMDb fields directly.  The
                # legacy item_overlay was a compatibility copy and could retain
                # stale/cleaned values from an older language policy.
                cached_item={}
                _genres=self._local_detail_cache.get("genres") or []
                _cast=self._local_detail_cache.get("cast") or []
                _directors=self._local_detail_cache.get("directors") or []
                _writers=self._local_detail_cache.get("writers") or []
                if self._local_detail_cache.get("overview"):cached_item["description"]=self._local_detail_cache.get("overview")
                if self._local_detail_cache.get("year") not in (None,""):cached_item["year"]=self._local_detail_cache.get("year")
                if self._local_detail_cache.get("runtime") not in (None,""):cached_item["time"]=self._local_detail_cache.get("runtime")
                if self._local_detail_cache.get("number_of_seasons") not in (None,""):cached_item["number_of_seasons"]=self._local_detail_cache.get("number_of_seasons")
                if self._local_detail_cache.get("number_of_episodes") not in (None,""):cached_item["number_of_episodes"]=self._local_detail_cache.get("number_of_episodes")
                if _genres:cached_item["genre"]=" / ".join(str(x) for x in _genres[:3] if str(x or "").strip())
                if _cast:cached_item["actors"]=(", ".join(str(x) for x in _cast[:8]) if isinstance(_cast,(list,tuple)) else str(_cast))
                if _directors:cached_item["director"]=(", ".join(str(x) for x in _directors[:3]) if isinstance(_directors,(list,tuple)) else str(_directors))
                if _writers:cached_item["writer"]=(", ".join(str(x) for x in _writers[:4]) if isinstance(_writers,(list,tuple)) else str(_writers))
                _countries=self._local_detail_cache.get("countries") or []
                if _countries:cached_item["country"]=(str(_countries[0]) if isinstance(_countries,(list,tuple)) else str(_countries))
            if isinstance(cached_item,dict):
                for k,v in cached_item.items():
                    if v not in (None,"",[],{}): merged[k]=v
            if self._local_detail_cache.get("tmdb_id"):
                merged["_locked_tmdb_id"]=self._local_detail_cache.get("tmdb_id")
                merged["_locked_tmdb_type"]=self._local_detail_cache.get("media_type")
            item=merged
        self.item=item
        self._backdrop_token=0; self._backdrop_jobs=queue.Queue()
        self._backdrop_rank=0; self._backdrop_displayed_path=""; self._backdrop_present_pending=""
        self._portal_backdrop_loaded=False
        self._portal_backdrop_source_local=str(item.get("_backdrop_source_local") or item.get("_cin_provider_backdrop_local") or "") if isinstance(item,dict) else ""
        self._portal_backdrop_present_local=str(item.get("_backdrop_present_local") or "") if isinstance(item,dict) else ""
        self._details_backdrop_authority_source=_handoff_clean_backdrop if _handoff_clean_locked else ""
        self._details_backdrop_authority_locked=bool(_handoff_clean_locked)
        if self._details_backdrop_authority_locked:
            try:
                _authority_fp=_visual_source_fingerprint(self._details_backdrop_authority_source)
                if str(self._persistent_visual_bundle.get("backdrop_present_source_fp") or "")!=_authority_fp:
                    self._persistent_visual_bundle.pop("backdrop_present",None)
                    self._persistent_visual_bundle.pop("backdrop_present_source_fp",None)
                self._persistent_visual_bundle["backdrop"]=self._details_backdrop_authority_source
            except Exception as exc:optional_failure("ui.r197_handoff_backdrop_bundle_guard",exc)
        if self._portal_backdrop_source_local and not os.path.isfile(self._portal_backdrop_source_local):self._portal_backdrop_source_local=""
        if self._portal_backdrop_present_local and not os.path.isfile(self._portal_backdrop_present_local):self._portal_backdrop_present_local=""
        # Older handoff code sometimes passed the already-prepared 1620x620 file
        # back as if it were a canonical source.  That path then got cropped a
        # second time after Player return.  Keep prepared and canonical identities
        # separate from the moment this screen is constructed.
        if self._portal_backdrop_source_local and os.path.basename(self._portal_backdrop_source_local).startswith("us221_detail_"):
            if not self._portal_backdrop_present_local:self._portal_backdrop_present_local=self._portal_backdrop_source_local
            self._portal_backdrop_source_local=""
        self._details_cancel_event=threading.Event()
        self._details_worker_futures=set()
        self._adaptive_palette_locked=False; self._adaptive_source_local=""; self._details_visual_state={}
        self._poster_laser_color_hint=str(item.get("adaptive_accent") or item.get("adaptive_primary") or item.get("_adaptive_accent") or "") if isinstance(item,dict) else ""
        self._adaptive_authoritative_source=str(item.get("_ultra_palette_source") or item.get("_adaptive_source_local") or item.get("_player_poster") or "") if isinstance(item,dict) else ""
        if self._adaptive_authoritative_source and not os.path.isfile(self._adaptive_authoritative_source):
            self._adaptive_authoritative_source=""
        self._poster_authoritative_source=str(item.get("_ultra_poster_source") or item.get("_player_poster") or item.get("_cin_provider_poster_local") or self._adaptive_authoritative_source or "") if isinstance(item,dict) else ""
        if self._poster_authoritative_source and not os.path.isfile(self._poster_authoritative_source):
            self._poster_authoritative_source=""
        self._adaptive_token=0; self._adaptive_jobs=queue.Queue(); self._adaptive_pending_source=""
        self._sharp_detail_pending=set(); self._sharp_detail_exact_sources=set()
        self._tmdb_token=0; self._tmdb_jobs=queue.Queue(); self._tmdb_data=None
        # Stage 1: remember which verified external identity currently owns the
        # visual package. Provider/Grid art may paint first, but it is provisional.
        self._canonical_visual_tmdb_id=""; self._canonical_visual_media_type=""
        self._xtream_auto_retry_count=0;self._xtream_auto_retry_timer=eTimer();self._xtream_auto_retry_conn=None
        try:self._xtream_auto_retry_conn=self._xtream_auto_retry_timer.timeout.connect(self._xtream_auto_retry)
        except Exception:self._xtream_auto_retry_timer.callback.append(self._xtream_auto_retry)
        self.onClose.append(self._stop_xtream_auto_retry)
        # Ultra-owned title-logo path. Network requests are display-sized and decoded only after safety guards.
        self._title_logo_token=0; self._title_logo_jobs=queue.Queue(); self._title_logo_pending=""
        self._title_logo_future=None;self._title_logo_target_size=None;self._title_logo_cache_target="";self._cast_language_token=0
        self._artwork_gap_retry=set()
        # Explicit Hero pin follows the proven release retry behaviour: one press
        # waits briefly for the already-running backdrop cache to expose its HDD
        # file, then prepares the Hero in background.
        self._hero_pin_token=0;self._hero_pin_future=None
        self._hero_pin_attempts=0;self._hero_pin_timer=eTimer();self._hero_pin_conn=None
        try:self._hero_pin_conn=self._hero_pin_timer.timeout.connect(self._pin_current_as_home_hero_retry)
        except Exception:self._hero_pin_timer.callback.append(self._pin_current_as_home_hero_retry)
        self.onClose.append(self._stop_hero_pin_timer)
        self._backdrop_watch_timer=eTimer(); self._backdrop_watch_conn=None
        try:self._backdrop_watch_conn=self._backdrop_watch_timer.timeout.connect(self._poll_recovered_backdrop)
        except Exception:self._backdrop_watch_timer.callback.append(self._poll_recovered_backdrop)
        self.onClose.append(self._stop_backdrop_watch)
        self.onClose.append(self._stop_details_ui_hooks)
        _clean_titles=bool(load_settings().get("clean_titles",True))
        raw_name=str(item.get("_raw_name") or item.get("name") or item.get("title") or "Content")
        cleaned_name=_catalogue_title(raw_name) if _clean_titles else raw_name.strip()
        self._server_display_name=_clean_display_text(cleaned_name or raw_name,120)
        name=self._server_display_name
        self["title"] = Label(_("MOVIE") if media_type=="vod" else _("SERIES"))
        self["subtitle"] = Label(str(item.get("genre") or item.get("category_name") or (_("Movies") if media_type=="vod" else _("Series"))).strip()[:70])
        self["name"] = Label(name)
        self._title_fit_timer=eTimer();self._title_fit_conn=None;self._title_fit_pass=0
        self._series_count_timer=eTimer();self._series_count_conn=None;self._series_count_pending=False
        try:self._series_count_conn=self._series_count_timer.timeout.connect(self._ensure_series_count_late)
        except Exception:self._series_count_timer.callback.append(self._ensure_series_count_late)
        self.onClose.append(self._stop_series_count_timer)
        try:self._title_fit_conn=self._title_fit_timer.timeout.connect(self._title_fit_tick)
        except Exception:self._title_fit_timer.callback.append(self._title_fit_tick)
        self.onClose.append(self._stop_title_fit_timer)
        meta=[]
        for label,key in ((_('Year'),"year"),(_('Rating'),"rating"),(_('Genre'),"genre"),(_('Duration'),"time"),(_('Season'),"season")):
            if item.get(key): meta.append("%s: %s"%(label,item.get(key)))
        progress=load_playback_progress(profile,media_type,item)
        if is_favorite(profile,media_type,item): meta.append(_("Favorite"))
        if progress.get("completed"): meta.append(_("Watched"))
        elif progress.get("duration") and progress.get("position"): meta.append(_("Resume %d%%")%min(99,int(progress["position"]*100.0/progress["duration"])))
        quality_source = " ".join(str(item.get(k) or "") for k in ("quality","resolution","format","video_quality","name","title"))
        badges=quality_badges(quality_source)
        if badges: meta.insert(0,badges)
        self["meta"] = Label("   •   ".join(meta)[:120])
        _initial_provider_desc=item.get("description") or item.get("descr") or item.get("plot") or ""
        desc=_initial_provider_desc or _("Loading metadata…")
        # R262: provider synopsis is first-paint only. Provider info callbacks
        # may enrich ids/quality, but they must not alternate the visible box
        # between two provider languages while TMDb authority is resolving.
        self._provider_description_painted=bool(str(_initial_provider_desc or "").strip())
        self._description_source=str(desc or "").strip()[:2200];self._description_pages=self._make_description_pages(self._description_source);self._desc_page=0
        self["description"] = Label(self._description_pages[0] if self._description_pages else self._description_source)
        self._desc_scroll_timer=eTimer();self._desc_scroll_conn=None
        self._desc_guard_timer=eTimer();self._desc_guard_conn=None
        try:self._desc_guard_conn=self._desc_guard_timer.timeout.connect(self._ensure_description_visible)
        except Exception:self._desc_guard_timer.callback.append(self._ensure_description_visible)
        try:self._desc_scroll_conn=self._desc_scroll_timer.timeout.connect(self._auto_scroll_description)
        except Exception:self._desc_scroll_timer.callback.append(self._auto_scroll_description)
        self.onClose.append(self._stop_description_scroll)
        self.onClose.append(self._ui_diag_details_close)
        self["status"] = Label("")
        self["dynamic_bg"] = Pixmap()
        self["cinematic_bg"] = Pixmap()
        self["cinematic_edge"] = Pixmap()
        self["poster"] = Pixmap()
        self["title_logo"] = Pixmap()
        self._settings_inline_menu=SettingsInlineChoiceOverlay(self,"settings_menu_list","settings_menu_actions",asset,_,optional_failure)
        # Legacy accent_frame remains as a hidden compatibility target for old
        # cached bundles.  The visible Details border is now four tiny Labels,
        # so no 380x580 frame surface is allocated or scaled over the poster.
        self["accent_frame"] = Pixmap()
        self["poster_laser_top"] = Label("")
        self["poster_laser_bottom"] = Label("")
        self["poster_laser_left"] = Label("")
        self["poster_laser_right"] = Label("")
        self["poster_card_bg"] = Pixmap()
        self["poster_border_overlay"] = Pixmap()
        self["poster_footer_bg"] = Pixmap()
        self["panel_bg"] = Pixmap()
        self["overview_bg"] = Pixmap()
        self["cast_card_bg"] = Pixmap()
        self["quality_pill_bg"] = Pixmap()
        self["year_pill_bg"] = Pixmap()
        self["runtime_pill_bg"] = Pixmap()
        self["country_pill_bg"] = Pixmap()
        self["genre_pill_bg"] = Pixmap()
        self["tmdb_pill_bg"] = Pixmap()
        self["imdb_pill_bg"] = Pixmap()
        self["rating_ring"] = Pixmap()
        self["poster_imdb_logo"] = Pixmap()
        self["quality_logo"] = Pixmap()
        self["country_flag"] = Pixmap()
        self["runtime_icon"] = Pixmap()
        self["red"] = Label(_("Back"))
        self["green"] = Label(_("Unfavorite") if is_favorite(profile, media_type, item) else _("Favorite"))
        self["yellow"] = Label(_("Information"))
        self["red_line"] = Label("")
        self["green_line"] = Label("")
        self["yellow_line"] = Label("")
        self["blue_line"] = Label("")
        play_label = _("Play") if media_type == "vod" else _("Episodes")
        if media_type == "vod" and progress.get("position") and not progress.get("completed"):
            play_label = _("Resume")
        self["blue"] = Label(play_label)
        portal_imdb=item.get("rating_imdb") or item.get("imdb_rating") or ""
        _cached_tmdb_rating=(self._local_detail_cache.get("rating") if isinstance(getattr(self,"_local_detail_cache",None),dict) else "")
        try:
            _cached_tmdb_text=("%.1f"%float(_cached_tmdb_rating)) if float(_cached_tmdb_rating or 0)>0 else "--"
        except Exception:
            _cached_tmdb_text="--"
        self["rating_score"] = Label(_cached_tmdb_text)
        self["rating_source"] = Label(_("TMDB rating") if _cached_tmdb_text!="--" else _("Metadata"))
        self["imdb_text"] = Label(("★ %s"%str(portal_imdb)[:4]) if portal_imdb else "N/A")
        self["poster_imdb_value"] = Label(str(portal_imdb)[:4] if portal_imdb else "--")
        self["poster_match_text"] = Label(_("TMDb • portal metadata"))
        portal_cast=item.get("actors") or item.get("cast") or item.get("actor") or ""
        self["cast_text"] = Label(str(portal_cast or "").strip()[:220])
        # Fast-path explicit quality before any network/runtime lookup. Scan the
        # whole portal payload (title, description, metadata, nested stream info).
        # AUTO is reserved only for genuinely unknown items.
        explicit_quality = self._detail_quality_value()
        self["quality_text"] = Label(explicit_quality or "")
        _year_raw=str(item.get("year") or item.get("release_year") or "")
        _year_match=re.search(r"(?:19|20)\d{2}",_year_raw)
        self["year_text"] = Label(_clean_year_value(_year_raw))
        duration=item.get("time") or item.get("duration") or item.get("length") or ""
        if media_type == "series":
            # Series overview shows the season count only. Episode count belongs
            # inside the season/episode browser after the user opens the series.
            seasons=item.get("number_of_seasons") or item.get("seasons_count") or item.get("season_count") or item.get("seasons") or item.get("season") or ""
            if isinstance(seasons,(list,tuple)):seasons=len(seasons)
            if seasons:self["runtime_text"] = Label(self._season_count_text(seasons))
            else:self["runtime_text"] = Label("—")
        else:
            self["runtime_text"] = Label(str(duration)[:14])
        self._country_raw=str(item.get("country_code") or item.get("country") or "").strip(); self["country_text"] = Label(self._normalized_country(self._country_raw))
        self["genre_text"] = Label((str(item.get("genre") or "").strip() or _detail_genre_value(item))[:54])
        _age_seed=(self._local_detail_cache.get("certification") or self._local_detail_cache.get("age_rating") or item.get("certification") or item.get("age_rating")) if isinstance(getattr(self,"_local_detail_cache",None),dict) else (item.get("certification") or item.get("age_rating"))
        self["age_rating_text"] = Label(_age_rating_display(_age_seed))
        self._image_init("poster",(352,552),profile,client)
        self.onLayoutFinish.append(self._layout_ready)
        try:self.onHide.append(self._details_hidden_release)
        except Exception as exc:optional_failure("ui.details_hide_hook",exc)
        try:self.onShown.append(self._details_shown_resume)
        except Exception as exc:optional_failure("ui.details_show_hook",exc)
        self["actions"] = ActionMap(["OkCancelActions","ColorActions","MenuActions","UltraStalkerMenuActions","DirectionActions"],{
            "cancel":self._fast_close,"red":self._fast_close,"ok":self.play,"blue":self.play,
            "green":self.toggle_favorite,"yellow":self.show_information,"menu":self.open_download_menu,
            "up":self.description_up,"down":self.description_down,
        },-1)

    def _track_details_future(self, future):
        if future is None:return None
        futures=getattr(self,"_details_worker_futures",None)
        if not isinstance(futures,set):
            futures=set();self._details_worker_futures=futures
        futures.add(future)
        try:future.add_done_callback(lambda done,owned=futures:owned.discard(done))
        except Exception:pass
        return future

    def _cancel_details_futures(self):
        futures=getattr(self,"_details_worker_futures",None)
        if not isinstance(futures,set):return
        for future in list(futures):
            if future is None or future.done():
                futures.discard(future);continue
            try:future.cancel()
            except Exception as exc:optional_failure("ui.details_future_cancel",exc)
            if future.done():futures.discard(future)

    def _ui_diag_details_close(self):
        try:_ui_diag("ui_close",screen="details",media_type=self.media_type,
                     lifetime_ms=int(max(0.0,(time.monotonic()-self._ui_diag_open_mono)*1000.0)))
        except Exception as exc:diagnostic_failure("ui.details.failsoft.185",exc)

    def _stop_details_ui_hooks(self):
        try:self._details_cancel_event.set()
        except Exception as exc:optional_failure("ui.details_cancel_cleanup",exc)
        self._cancel_details_futures()
        self._title_logo_token += 1;self._cast_language_token += 1
        future=getattr(self,"_title_logo_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        self._title_logo_future=None
        for result_queue_name in ("_adaptive_jobs","_backdrop_jobs","_tmdb_jobs","_title_logo_jobs"):
            result_queue=getattr(self,result_queue_name,None)
            if result_queue is None:continue
            try:
                while True:result_queue.get_nowait()
            except queue.Empty:
                pass
            except Exception as exc:
                optional_failure("ui.details_queue_cleanup",exc)
        for hook_name,callback in (
            ("onLayoutFinish",self._layout_ready),
            ("onHide",self._details_hidden_release),
            ("onShown",self._details_shown_resume),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.details_hook_cleanup",exc)

    def _details_hidden_release(self):
        """Suspend background work while a child screen is open, but keep the UI state.

        Previous builds released every Details pixmap on onHide(). Enigma2 then
        returned to a live screen whose adaptive/glass widgets had already lost
        their native pixmaps, causing the black/no-glass state seen after Info,
        Episodes or Player. Full pixmap release belongs only to onClose().
        """
        if getattr(self,"_screen_closed",False):return
        self._details_images_suspended=True
        try:self._backdrop_watch_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._desc_scroll_timer.stop();self._desc_guard_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        # Freeze the exact presentation that was visible before the child/player.
        # Async backdrop jobs completing behind the player must not repaint Details
        # on return with a differently cropped source.
        try:
            current=str(getattr(self,"_backdrop_displayed_path","") or self._details_visual_state.get("backdrop_present") or getattr(self,"_portal_backdrop_present_local","") or "")
            canonical=str(self._details_visual_state.get("backdrop") or getattr(self,"_portal_backdrop_source_local","") or "")
            if canonical and os.path.isfile(canonical) and not os.path.basename(canonical).startswith("us221_detail_"):
                self._return_backdrop_source=canonical
            if current and os.path.isfile(current):
                self._return_backdrop_path=current
                self._details_visual_state["backdrop_present"]=current
        except Exception as exc:optional_failure("ui.details_freeze_backdrop",exc)
        try:
            self._backdrop_token += 1
            self._backdrop_present_pending=""
            while True:self._backdrop_jobs.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:optional_failure("ui.details_freeze_queue",exc)

    def _restore_details_visual_state(self, reload_disk=True):
        """Restore Details visuals, optionally re-reading the HDD bundle.

        First-paint callers already own the validated in-memory bundle loaded at
        screen construction, so they must not hit HDD again on the GUI thread.
        Child/player returns still request a fresh disk rehydrate.
        """
        if getattr(self,"_screen_closed",False):return
        state=dict(getattr(self,"_details_visual_state",{}) or {})
        # Child/player screens can release pixmaps under memory pressure. Rehydrate
        # the exact persistent visual bundle first, so return never depends on a
        # stale in-RAM path.
        try:
            snap=(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None))
            disk=(_load_visual_bundle(self.profile,self.media_type,getattr(self,"_portal_item",self.item),snapshot=snap) or {}) if reload_disk else dict(getattr(self,"_persistent_visual_bundle",{}) or {})
            authority=str(getattr(self,"_poster_authoritative_source","") or "")
            if authority and os.path.isfile(authority):
                afp=_visual_source_fingerprint(authority)
                if str(disk.get("detail_poster_source_fp") or "")!=afp:
                    disk.pop("detail_poster",None)
                    state.pop("detail_poster",None)
                # The current Grid/Cinematic selection is the strongest poster
                # identity for this screen. Never let a stale disk row replace it.
                state["poster"]=authority
            # Live RAM state is authoritative. Disk is fallback only; overwriting
            # it here was the source of the post-player backdrop jump/crop.
            for key,value in disk.items():
                if key not in state or state.get(key) in (None,"",{},[]):
                    if value not in (None,"",{},[]):state[key]=value
            frozen=str(getattr(self,"_return_backdrop_path","") or "")
            authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
            canonical=str(authority or getattr(self,"_return_backdrop_source","") or state.get("backdrop") or getattr(self,"_portal_backdrop_source_local","") or "")
            exact=""
            if canonical and os.path.isfile(canonical) and not os.path.basename(canonical).startswith("us221_detail_"):
                try:
                    candidate=self._backdrop_presentation_target(canonical)
                    if candidate and _valid_cache_file(candidate):exact=candidate
                except Exception as exc:optional_failure("ui.details_return_backdrop_target",exc)
            if exact:state["backdrop_present"]=exact
            elif frozen and os.path.isfile(frozen):state["backdrop_present"]=frozen
            elif getattr(self,"_portal_backdrop_present_local","") and os.path.isfile(self._portal_backdrop_present_local):state["backdrop_present"]=self._portal_backdrop_present_local
            self._details_visual_state.update(state)
        except Exception as exc:optional_failure("ui.details_restore_bundle",exc)

        # Re-apply the exact adaptive chrome that was already built on HDD.
        chrome=state.get("chrome")
        if isinstance(chrome,dict) and chrome:
            try:self._apply_detail_chrome(chrome)
            except Exception as exc:optional_failure("ui.details_restore_chrome",exc)
        else:
            try:self._apply_detail_chrome(None)
            except Exception as exc:optional_failure("ui.silent_guard",exc)

        # Background/theme/backdrop are restored directly from cached files.
        for widget,key in (
            ("dynamic_bg","theme"),
            ("cinematic_bg","backdrop_present"),
            ("cinematic_edge","edge"),
        ):
            try:
                path=str(state.get(key) or "")
                if path and os.path.isfile(path) and self[widget].instance is not None:
                    if widget=="cinematic_bg":
                        # Player/video modes on a few images can leave the native
                        # pixmap widget with stale geometry. Reassert the approved
                        # Details/Series hero rectangle before rebinding the file.
                        try:self[widget].instance.move(ePoint(300,0));self[widget].instance.resize(eSize(1620,670))
                        except Exception as exc:optional_failure("ui.details_restore_backdrop_geometry",exc)
                    try:self[widget].instance.setPixmap(None)
                    except Exception:pass
                    self[widget].instance.setPixmapFromFile(path)
                    self[widget].show()
                    if widget=="cinematic_bg":
                        self._backdrop_displayed_path=path
                        self._backdrop_rank=max(5,int(getattr(self,"_backdrop_rank",0) or 0))
                        self._backdrop_present_pending=""
            except Exception as exc:
                optional_failure("ui.details_restore_%s"%widget,exc)

        # Poster is already HDD-cached; restore directly if the decoder was reset.
        try:
            poster=str(state.get("detail_poster") or state.get("poster") or getattr(self,"_image_displayed_path","") or "")
            if poster and os.path.isfile(poster) and self["poster"].instance is not None:
                try:self["poster"].instance.setPixmap(None)
                except Exception:pass
                self["poster"].instance.setPixmapFromFile(poster)
                self["poster"].show()
        except Exception as exc:optional_failure("ui.details_restore_poster",exc)

        try:self._sync_detail_text_direction()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._ensure_description_visible()
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _details_shown_resume(self):
        if not getattr(self,"_details_images_suspended",False):return
        if getattr(self,"_screen_closed",False):return
        self._details_images_suspended=False
        try:self._image_layout_ready()
        except Exception as exc:optional_failure("ui.silent_guard",exc)

        # First paint after return is synchronous and HDD-only.
        try:self._restore_details_visual_state()
        except Exception as exc:optional_failure("ui.details_resume_restore",exc)

        try:self._schedule_description_scroll();self._desc_guard_timer.start(700,True)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            if int(getattr(self,"_backdrop_rank",0) or 0)<5:self._backdrop_watch_timer.start(1400,True)
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _release_details_pixmaps(self):
        # These widgets can own 1620x620 / full-screen native images. Clearing
        # only the poster is insufficient on long Movies/Series sessions.
        for _name in (
            "dynamic_bg","cinematic_bg","cinematic_edge","poster","accent_frame",
            "poster_card_bg","poster_border_overlay","poster_footer_bg","panel_bg","overview_bg",
            "cast_card_bg","quality_pill_bg","year_pill_bg",
            "runtime_pill_bg","country_pill_bg","genre_pill_bg","tmdb_pill_bg","imdb_pill_bg",
            "rating_ring","poster_imdb_logo","quality_logo","country_flag","runtime_icon"
        ):
            _release_pixmap_widget(self,_name)
        try:self._tmdb_data=None
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._local_detail_cache={}
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._backdrop_jobs=queue.Queue();self._adaptive_jobs=queue.Queue();self._tmdb_jobs=queue.Queue()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        _native_image_pressure_relief(force=True)

    def _fast_close(self):
        """Close details immediately and invalidate every stale visual callback."""
        try:self._details_cancel_event.set()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._cancel_details_futures()
        try:self._adaptive_token += 1; self._backdrop_token += 1; self._tmdb_token += 1
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._backdrop_watch_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._desc_scroll_timer.stop(); self._desc_guard_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self.close()

    def _stop_backdrop_watch(self):
        try:self._backdrop_watch_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            if self._backdrop_watch_conn is not None:self._backdrop_watch_conn.disconnect()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            if self._poll_recovered_backdrop in self._backdrop_watch_timer.callback:self._backdrop_watch_timer.callback.remove(self._poll_recovered_backdrop)
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _poll_recovered_backdrop(self):
        """Notice a recovered HDD backdrop without doing image work on the UI thread."""
        if self._screen_closed or getattr(self,"_persistent_backdrop_locked",False):return
        try:
            snap=load_artwork_v2_manifest(self.profile,self.media_type,getattr(self,"_portal_item",self.item)) or {}
            authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
            if getattr(self,"_details_backdrop_authority_locked",False) and authority:
                real=authority
            elif int(snap.get("clean_backdrop_selector_version") or 0)>=3:
                real=str(snap.get("backdrop_local") or "")
            else:
                real=""
            if _details_backdrop_truth(real):
                target=self._backdrop_presentation_target(real)
                if target and _valid_cache_file(target):
                    current=str(getattr(self,"_backdrop_displayed_path","") or "")
                    if target!=current and self["cinematic_bg"].instance is not None:
                        self["cinematic_bg"].instance.setPixmap(None);self["cinematic_bg"].instance.setPixmapFromFile(target);self["cinematic_bg"].show()
                        self._backdrop_displayed_path=target;self._backdrop_rank=max(5,int(getattr(self,"_backdrop_rank",0) or 0))
                        self["status"].setText(_("Backdrop ready • HDD"))
                        _runtime_endurance_log("details_backdrop_hot_reload",media_type=self.media_type,tmdb_id=snap.get("tmdb_id"))
                elif getattr(self,"_backdrop_present_pending","")!=real:
                    self._schedule_backdrop_presentation(real,5)
            if not self._screen_closed and int(getattr(self,"_backdrop_rank",0) or 0) < 5:
                self._backdrop_watch_timer.start(1100,True)
        except Exception as exc:
            optional_failure("ui.backdrop_watch",exc)
            try:
                if not self._screen_closed:self._backdrop_watch_timer.start(1800,True)
            except Exception as exc:optional_failure("ui.backdrop_watch_restart",exc)

    def _runtime_details_close(self):
        try:self._details_cancel_event.set()
        except Exception as exc:optional_failure("ui.details_cancel",exc)
        _runtime_endurance_log("details_close",media_type=self.media_type,backdrop_rank=int(getattr(self,"_backdrop_rank",0) or 0),tmdb=bool(isinstance(getattr(self,"_tmdb_data",None),dict) and self._tmdb_data.get("matched")))

    def _decode_picture(self, path):
        """Paint the canonical poster immediately; prepare the rounded crop later.

        R180 called Pillow synchronously here, which could freeze Enigma2 for
        seconds on the first uncached Search result. R181 never does image
        resampling on the GUI thread.
        """
        source=str(path or "")
        if not source:
            return
        ImageLoaderMixin._decode_picture(self,source)
        try:
            if not os.path.isfile(source) or source.endswith(".png") and "detailcover_" in os.path.basename(source):
                return
            self._poster_prepare_token=int(getattr(self,"_poster_prepare_token",0) or 0)+1
            token=self._poster_prepare_token;screen_ref=weakref.ref(self)
            def worker():
                owner=screen_ref()
                if owner is None or owner._screen_closed:return ""
                try:return str(_detail_cover_artwork(source,(352,552)) or "")
                except Exception as exc:
                    optional_failure("ui.detail_cover_async",exc);return ""
            future=_IMAGE_EXECUTOR.submit(worker)
            self._track_details_future(future)
            def done(_f):
                try:prepared=str(_f.result() or "")
                except Exception:prepared=""
                if not prepared or prepared==source:return
                owner=screen_ref()
                if owner is None:return
                try:reactor.callFromThread(owner._apply_prepared_poster,prepared,source,token)
                except Exception:pass
            try:future.add_done_callback(done)
            except Exception:pass
        except Exception as exc:
            optional_failure("ui.detail_cover_async_submit",exc)

    def _apply_prepared_poster(self, prepared, source, token):
        try:
            if self._screen_closed or int(token)!=int(getattr(self,"_poster_prepare_token",0) or 0):return
            if not prepared or not os.path.isfile(str(prepared)):return
            current=str(getattr(self,"_image_displayed_path","") or getattr(self,"_image_pending_path","") or "")
            if current and current not in (str(source),str(prepared)):
                return
            # Persist the exact rounded Details derivative, bound to its canonical
            # source fingerprint. Next open paints the finished poster immediately
            # instead of flashing raw -> rounded again.
            self._details_visual_state["poster"]=str(source)
            self._details_visual_state["detail_poster"]=str(prepared)
            self._details_visual_state["detail_poster_source_fp"]=_visual_source_fingerprint(source)
            self._persistent_detail_poster_locked=True
            self._persist_visual_bundle_async(self._details_visual_state,(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None)))
            ImageLoaderMixin._decode_picture(self,str(prepared))
        except Exception as exc:optional_failure("ui.detail_cover_async_apply",exc)

    @staticmethod
    def _make_description_pages(text,limit=360):
        # release Details: keep every synopsis in the original compact Overview box; never page/scroll it.
        # No page cycling: adaptive font fitting uses the existing spare vertical room.
        value=str(text or "").replace("\r"," ").replace("\n"," ").strip()
        # Display-only cleanup: remove Arabic tashkeel from this description box
        # without changing TMDb/provider language choice or the persisted raw text.
        value=_strip_arabic_tashkeel(value)
        return [value]
    def _publish_visible_description_authority(self,text):
        """Stage 3 compatibility hook.

        The canonical raw TMDb ``overview`` is now the only description authority.
        Details no longer publishes a second provider/Arabic-preservation overlay.
        """
        return bool(str(text or "").strip())

    def _set_description_source(self,text):
        # Raw TMDb/provider language authority stays untouched.  The display page
        # may remove Arabic tashkeel only; it never changes locale, substitutes
        # English, or rewrites the persisted description source.
        incoming=str(text or "").strip()
        self._description_source=incoming;self._description_pages=self._make_description_pages(self._description_source);self._desc_page=0
        self["description"].setText(self._description_pages[0] if self._description_pages else self._description_source)
        try:self._fit_description_font()
        except Exception as exc:diagnostic_failure("ui.details.failsoft.385",exc)
        try:self._publish_visible_description_authority(self._description_source)
        except Exception as exc:optional_failure("ui.details_visible_overview_publish_call",exc)
        self._sync_detail_text_direction();self._schedule_description_scroll()
        try:self._desc_guard_timer.start(180,True)
        except Exception:self._ensure_description_visible()
        return True
    def _ensure_description_visible(self):
        try:
            text=str(self["description"].getText() or "").strip()
            if not text:
                pages=getattr(self,"_description_pages",[]) or []
                fallback=(pages[0] if pages else getattr(self,"_description_source","")).strip()
                self["description"].setText(fallback or _("No description available."))
            self["description"].show()
        except Exception as exc:optional_failure("ui",exc)

    def _stop_description_scroll(self):
        try:self._desc_scroll_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:self._desc_guard_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._desc_scroll_conn is not None:self._desc_scroll_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._desc_guard_conn is not None:self._desc_guard_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._auto_scroll_description in self._desc_scroll_timer.callback:self._desc_scroll_timer.callback.remove(self._auto_scroll_description)
        except Exception as exc:optional_failure("ui.desc_scroll_callback",exc)
        try:
            if self._ensure_description_visible in self._desc_guard_timer.callback:self._desc_guard_timer.callback.remove(self._ensure_description_visible)
        except Exception as exc:optional_failure("ui.desc_guard_callback",exc)
    def _schedule_description_scroll(self):
        try:self._desc_scroll_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            return
        except Exception as exc:optional_failure("ui",exc)
    def _show_description_page(self,index):
        pages=getattr(self,"_description_pages",[]) or [getattr(self,"_description_source","")]
        if not pages:return
        self._desc_page=index%len(pages);self["description"].setText(pages[self._desc_page]);self._sync_detail_text_direction()
    def _auto_scroll_description(self):
        pages=getattr(self,"_description_pages",[]) or []
        if len(pages)>1:self._show_description_page(getattr(self,"_desc_page",0)+1);self._schedule_description_scroll()
    def description_up(self):
        self._show_description_page(getattr(self,"_desc_page",0)-1);self._schedule_description_scroll()
    def description_down(self):
        self._show_description_page(getattr(self,"_desc_page",0)+1);self._schedule_description_scroll()

    def _apply_provider_metadata(self, enriched):
        """Merge provider/Xtream metadata into the visible Details screen.

        Provider data is first-paint truth. TMDB may upgrade individual fields later,
        but a TMDB miss must never blank valid server metadata.
        """
        if not isinstance(enriched,dict):return
        try:
            # Keep the enriched row as the current portal item so subsequent artwork,
            # playback and TMDB resolution see structured ids/titles returned by server.
            current=dict(getattr(self,"_portal_item",{}) or {})
            current.update({k:v for k,v in enriched.items() if v not in (None,"",[],{})})
            self._portal_item=current
            self.item.update({k:v for k,v in current.items() if v not in (None,"",[],{})})
        except Exception as exc:diagnostic_failure("ui.details.failsoft.448",exc)

        def first(*keys):
            for key in keys:
                value=enriched.get(key)
                if value not in (None,"",[],{}):return value
            return ""

        # Provider metadata is allowed as instant first paint only.  Once a
        # verified TMDb snapshot owns this screen, do not let a later provider
        # callback repaint its description/genres/cast/runtime/rating with a
        # cleaned or differently-localized copy.  Clean Names remains independent.
        _tmdb_owner=getattr(self,"_tmdb_data",None)
        tmdb_owns_metadata=bool(isinstance(_tmdb_owner,dict) and _tmdb_owner.get("matched") and
                                (_tmdb_owner.get("identity_verified") or _tmdb_owner.get("identity_pointer_verified")) and
                                _tmdb_owner.get("tmdb_id"))

        title=first("name","title","movie_name")
        if title:
            try:
                # Provider metadata may repeat the raw catalogue decoration.
                # Keep the same cleaned title used on first paint so late
                # metadata cannot re-introduce AR-AS-D / year / country tags.
                cleaned=(_catalogue_title(str(title)) if load_settings().get("clean_titles",True) else str(title or "").strip())
                visible=_clean_display_text(cleaned or str(title),120)
                self._server_display_name=visible or getattr(self,"_server_display_name","")
                if str(self["name"].getText() or "")!=visible:
                    self["name"].setText(visible)
                    self._fit_single_line_font("name",visible,42,14);self._schedule_title_fit()
            except Exception as exc:diagnostic_failure("ui.details.failsoft.459",exc)

        overview=first("description","plot","descr","overview")
        if overview and not tmdb_owns_metadata and not bool(getattr(self,"_provider_description_painted",False)):
            try:
                self._set_description_source(str(overview));self._provider_description_painted=True
            except Exception as exc:diagnostic_failure("ui.details.failsoft.464",exc)

        year=first("year","release_year","releaseDate","release_date")
        if year and not tmdb_owns_metadata:
            try:
                m=re.search(r"(?:19|20)\\d{2}",str(year))
                self["year_text"].setText(_clean_year_value(year))
            except Exception as exc:diagnostic_failure("ui.details.failsoft.471",exc)

        country=first("country","country_code","countries","origin_country","production_country","production_countries")
        if country and not tmdb_owns_metadata:
            try:
                code=self._country_code(country)
                self._country_raw=code or str(country).strip()
                self["country_text"].setText(self._normalized_country(self._country_raw))
                self._refresh_detail_visuals()
            except Exception as exc:optional_failure("ui.details_provider_country",exc)

        genre=_detail_genre_value(enriched)
        if genre and not tmdb_owns_metadata:
            try:self["genre_text"].setText(str(genre)[:54]);self["subtitle"].setText(str(genre)[:78])
            except Exception as exc:diagnostic_failure("ui.details.failsoft.477",exc)
        if not tmdb_owns_metadata:
            try:
                _provider_age=first("certification","age_rating")
                if _provider_age:self["age_rating_text"].setText(_age_rating_display(_provider_age))
            except Exception as exc:optional_failure("ui.details.provider_age_rating",exc)

        actors=first("actors","cast","actor")
        if isinstance(actors,(list,tuple)):actors=", ".join([str(x) for x in actors[:8]])
        if actors and not tmdb_owns_metadata:
            try:self["cast_text"].setText(str(actors).strip()[:220])
            except Exception as exc:diagnostic_failure("ui.details.failsoft.483",exc)

        rating=first("rating_imdb","imdb_rating","rating")
        if rating and not tmdb_owns_metadata:
            try:
                score=float(rating)
                self["imdb_text"].setText("★ %.1f"%score)
                self["poster_imdb_value"].setText("%.1f"%score)
                self["rating_source"].setText(_("Provider rating"))
            except Exception:
                try:self["imdb_text"].setText(str(rating)[:24])
                except Exception as exc:diagnostic_failure("ui.details.failsoft.507",exc)

        runtime=first("time","duration","runtime","length")
        if runtime and self.media_type=="vod" and not tmdb_owns_metadata:
            try:self["runtime_text"].setText(str(runtime)[:14]);self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
            except Exception as exc:diagnostic_failure("ui.details.failsoft.512",exc)

        seasons=first("number_of_seasons","seasons_count","season_count")
        if seasons and self.media_type=="series" and not tmdb_owns_metadata:
            try:self["runtime_text"].setText(self._season_count_text(seasons));self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
            except Exception as exc:diagnostic_failure("ui.details.failsoft.517",exc)

        try:
            self["poster_match_text"].setText(_("Server • TMDb enrichment"))
            self["status"].setText(_("Server metadata loaded"))
            self._sync_detail_text_direction()
            self._refresh_detail_visuals()
        except Exception as exc:diagnostic_failure("ui.details.failsoft.524",exc)

    def _stop_xtream_auto_retry(self):
        try:self._xtream_auto_retry_timer.stop()
        except Exception:pass
        try:
            if self._xtream_auto_retry_conn is not None:self._xtream_auto_retry_conn.disconnect()
        except Exception:pass
        try:
            if self._xtream_auto_retry in self._xtream_auto_retry_timer.callback:self._xtream_auto_retry_timer.callback.remove(self._xtream_auto_retry)
        except Exception:pass

    def _schedule_xtream_auto_retry(self):
        if self._screen_closed or not getattr(self,"_is_xtream_item",False):return
        if int(getattr(self,"_xtream_auto_retry_count",0) or 0)>=1:return
        self._xtream_auto_retry_count=1
        try:self._xtream_auto_retry_timer.start(1100,True)
        except Exception:self._xtream_auto_retry()

    def _xtream_auto_retry(self):
        if self._screen_closed or not getattr(self,"_is_xtream_item",False):return
        # If the first TMDb pass could not resolve from the visible row, allow the
        # deferred provider-info request now and retry TMDb once with the enriched
        # ids/title. Provider networking never competes with a successful first
        # backdrop paint.
        self._start_deferred_provider_enrichment(retry_tmdb=True)

    def _start_deferred_provider_enrichment(self, retry_tmdb=False):
        if self._screen_closed:return
        base_item=dict(getattr(self,"_provider_enrich_deferred_item",{}) or {})
        if not base_item:return
        if getattr(self,"_provider_enrich_started",False):return
        if not (hasattr(self.client,"enrich_provider_artwork") and base_item.get("_xtream")):return
        self._provider_enrich_started=True
        self._provider_enrich_retry_tmdb=bool(retry_tmdb)

        def work(handle):
            enriched=dict(base_item)
            try:
                enriched=call_compatible(
                    self.client.enrich_provider_artwork,
                    (((enriched,self.media_type,handle.cancel_event), {"force":True}),
                     ((enriched,self.media_type,handle.cancel_event), {})),
                ) or enriched
            except Exception as exc:diagnostic_failure("ui.details.failsoft.546",exc)
            return enriched

        def ok(enriched):
            if self._screen_closed:return
            self._apply_provider_metadata(enriched if isinstance(enriched,dict) else base_item)
            if bool(getattr(self,"_provider_enrich_retry_tmdb",False)):
                self._provider_enrich_retry_tmdb=False
                self._load_tmdb_metadata()

        def fail(_error):
            if self._screen_closed:return
            self._apply_provider_metadata(base_item)

        self._run_async(work,ok,fail)

    def _load_provider_metadata(self):
        """Fetch provider details first, then let TMDB enrich the merged row."""
        if self._screen_closed:return
        base_item=dict(getattr(self,"_portal_item",self.item) or {})

        # Once the visual source files are on HDD, do not call provider
        # enrichment again just to rediscover artwork URLs.  Render the row we
        # already have and let the HDD-only metadata loader below do the rest.
        if getattr(self,"_persistent_poster_locked",False) and getattr(self,"_persistent_backdrop_source_locked",False):
            self._apply_provider_metadata(base_item)
            self._load_tmdb_metadata()
            return

        # Already-rich Stalker rows need no extra request; just render them.
        can_enrich=bool(hasattr(self.client,"enrich_provider_artwork") and isinstance(base_item,dict) and base_item.get("_xtream"))
        if not can_enrich:
            self._apply_provider_metadata(base_item)
            if not getattr(self,"_persistent_visual_frozen",False):self._load_provider_art()
            self._load_tmdb_metadata()
            return

        # Backdrop-priority network budget. The visible catalogue/search row is
        # enough to start TMDb immediately; keep the Xtream provider-info request
        # parked until the first backdrop lands (or until TMDb genuinely misses).
        # This removes a third simultaneous network consumer from first paint.
        self._apply_provider_metadata(base_item)
        self._provider_enrich_deferred_item=dict(base_item)
        self._provider_enrich_started=False
        self._provider_enrich_retry_tmdb=False
        cfg_now=load_settings()
        if bool(cfg_now.get("tmdb_enabled",True)) and str(cfg_now.get("tmdb_credential") or "").strip():
            self._load_tmdb_metadata()
        else:
            self._start_deferred_provider_enrichment(retry_tmdb=False)

    def _load_provider_art(self):
        """VOD/Series artwork is TMDB-global only in Beta54.

        Provider metadata may help identity/quality, but provider poster/backdrop
        bytes are never downloaded or painted by Details.
        """
        return


    def _stop_series_count_timer(self):
        try:self._series_count_timer.stop()
        except Exception as exc:optional_failure("ui.series_count_timer_stop",exc)

    def _apply_series_count_cached(self,count):
        _owner=(getattr(self,"_tmdb_data",None) if isinstance(getattr(self,"_tmdb_data",None),dict) else None) or (getattr(self,"_local_detail_cache",None) if isinstance(getattr(self,"_local_detail_cache",None),dict) else {})
        if isinstance(_owner,dict) and _owner.get("_details_authority_ready") and _owner.get("tmdb_id"):
            return
        try:count=int(count or 0)
        except Exception:count=0
        if count<=0 or self._screen_closed:return
        try:self["runtime_text"].setText(self._season_count_text(count));self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
        except Exception:return
        try:
            self.item["number_of_seasons"]=count
            if isinstance(getattr(self,"_portal_item",None),dict):self._portal_item["number_of_seasons"]=count
            snap=dict(getattr(self,"_local_detail_cache",{}) or {})
            overlay=dict(snap.get("item_overlay") or {})
            overlay["number_of_seasons"]=count
            snap["item_overlay"]=overlay
            save_detail_snapshot(self.profile,self.media_type,getattr(self,"_portal_item",self.item),snap)
            self._local_detail_cache=snap
        except Exception as exc:optional_failure("ui.series_count_cache_save",exc)

    def _ensure_series_count_late(self):
        if self._screen_closed or self.media_type!="series" or self._series_count_pending:return
        _owner=(getattr(self,"_tmdb_data",None) if isinstance(getattr(self,"_tmdb_data",None),dict) else None) or (getattr(self,"_local_detail_cache",None) if isinstance(getattr(self,"_local_detail_cache",None),dict) else {})
        if isinstance(_owner,dict) and _owner.get("_details_authority_ready") and _owner.get("tmdb_id"):
            return
        # The provider season browser is authoritative.  TMDB/cache may report
        # seasons that the IPTV portal does not actually expose, so always verify
        # the displayed count asynchronously against series_seasons().
        if not hasattr(self.client,"series_seasons"):return
        self._series_count_pending=True
        portal_item=dict(getattr(self,"_portal_item",self.item) or {})
        def work(handle):
            try:
                rows=self.client.series_seasons(portal_item,cancel_event=handle.cancel_event)
            except TypeError:
                rows=self.client.series_seasons(portal_item)
            rows=[dict(x) for x in (rows or []) if isinstance(x,dict)] if isinstance(rows,list) else []
            # Persist the single seasons catalogue while already off the GUI
            # thread. Embedded episode lists remain inside these rows, so the
            # child browser can open a season instantly without N extra writes.
            if rows and not handle.cancel_event.is_set():
                try:
                    from .ui_screens_series import _series_cache_write
                    _series_cache_write(self.profile,portal_item,"seasons",rows)
                except Exception as exc:optional_failure("ui.series_hierarchy_prefetch_cache",exc)
            return rows
        def ok(rows):
            self._series_count_pending=False
            rows=[dict(x) for x in (rows or []) if isinstance(x,dict)]
            self._prefetched_series_seasons=rows
            self._apply_series_count_cached(len(rows))
        def fail(exc):
            self._series_count_pending=False
            optional_failure("ui.series_count_late",exc)
        self._run_async(work,ok,fail)

    def _layout_ready(self):
        try:
            if self["title_logo"].instance:
                self._title_logo_target_size=(self["title_logo"].instance.size().width(),self["title_logo"].instance.size().height())
        except Exception:
            self._title_logo_target_size=None
        # HDD-only title-logo first paint. Search may already carry a verified
        # TMDb id from the result card; do not wait for the resolver callback to
        # rediscover a logo variant that is already cached on disk.
        try:
            _logo_cached=ultra_title_logo_cached(
                self.media_type,
                getattr(self,"_portal_item",self.item),
                getattr(self,"_local_detail_cache",{}) or {},
                self._title_logo_target_size or (432,210),
                str(getattr(self,"_server_display_name","") or ""),
            )
            if _logo_cached and valid_ultra_title_logo(_logo_cached) and self["title_logo"].instance is not None:
                self["title_logo"].instance.setPixmapFromFile(_logo_cached);self["title_logo"].show()
        except Exception as exc:optional_failure("ui.details_cached_logo_first_paint",exc)
        if self.media_type=="series" and not getattr(self,"_is_series_browser_child",False):
            try:self._series_count_timer.start(120,True)
            except Exception as exc:optional_failure("ui.series_count_timer_start",exc)
        try:_ui_diag("ui_ready",screen="details",media_type=self.media_type,elapsed_ms=int((time.monotonic()-self._ui_diag_open_mono)*1000.0))
        except Exception as exc:diagnostic_failure("ui.details.failsoft.606",exc)
        _runtime_endurance_log("details_layout",media_type=self.media_type,cache=bool(getattr(self,"_local_detail_cache",{})))
        self._image_layout_ready()
        self._transition_init(("title", "subtitle", "name", "description", "dynamic_bg", "cinematic_bg", "cinematic_edge", "poster", "title_logo", "poster_footer_bg", "panel_bg", "overview_bg", "quality_pill_bg", "year_pill_bg", "runtime_pill_bg", "country_pill_bg", "genre_pill_bg", "tmdb_pill_bg", "imdb_pill_bg", "rating_ring", "poster_imdb_logo", "poster_imdb_value", "poster_match_text", "quality_logo", "country_flag", "runtime_icon", "rating_score", "rating_source", "imdb_text", "quality_text", "year_text", "runtime_text", "country_text", "genre_text", "age_rating_text", "cast_text", "status", "red", "green", "yellow", "blue"))
        self._fit_remote_hint_underlines()
        ph="grid_placeholder_movie_921.png" if self.media_type=="vod" else "grid_placeholder_series_921.png"
        cached=getattr(self,"_local_detail_cache",{})
        bundle=getattr(self,"_persistent_visual_bundle",{}) or {}
        # Migrate an older HDD backdrop source to one final 1620x620 presentation
        # locally, before any provider/TMDB work.  This is a one-time local
        # conversion; subsequent opens use backdrop_present directly.
        if not str(bundle.get("backdrop_present") or ""):
            local_backdrop=str(bundle.get("backdrop") or "")
            if local_backdrop and _details_backdrop_truth(local_backdrop):
                try:
                    local_present=self._schedule_backdrop_presentation(local_backdrop,5)
                    if local_present and os.path.isfile(local_present):
                        bundle=dict(bundle);bundle["backdrop_present"]=local_present
                        self._persistent_visual_bundle=bundle;self._persistent_backdrop_locked=True
                except Exception as exc:optional_failure("ui.details_local_backdrop_migrate",exc)
        cfg_now=load_settings();external_ready=bool(cfg_now.get("tmdb_enabled",True) and str(cfg_now.get("tmdb_credential") or "").strip())
        cached_source=str((cached or {}).get("identity_source") or "") if isinstance(cached,dict) else ""
        cached_poster=_verified_external_art(cached,"poster")
        # Apply the grid's exact palette immediately on entry. The displayed
        # Details poster may be upgraded later, but it must never repaint the
        # screen with a different adaptive identity.
        authoritative=getattr(self,"_adaptive_authoritative_source","")
        poster_authority=getattr(self,"_poster_authoritative_source","")
        if authoritative and os.path.isfile(authoritative) and not getattr(self,"_persistent_visual_frozen",False):
            self._schedule_poster_visuals(authoritative)
        bundled_poster=str(bundle.get("poster") or "")
        bundled_detail_poster=str(bundle.get("detail_poster") or "")
        # The exact poster handed off by the currently selected Cinematic/Grid
        # item is authoritative for this Details open.  A historical Details
        # derivative from another similarly-named title must never win simply
        # because it exists on HDD.  Use the cached derivative only when it is
        # fingerprint-bound to the same source poster.  Otherwise show the
        # authoritative source directly; Details does NO poster/adaptive build.
        authority_fp=_visual_source_fingerprint(poster_authority) if poster_authority and os.path.isfile(poster_authority) else ""
        detail_fp=str(bundle.get("detail_poster_source_fp") or "")
        detail_matches_authority=bool(authority_fp and bundled_detail_poster and os.path.isfile(bundled_detail_poster) and detail_fp==authority_fp)
        if poster_authority and os.path.isfile(poster_authority):
            if detail_matches_authority:
                try:
                    if self["poster"].instance is not None:
                        self["poster"].instance.setPixmapFromFile(bundled_detail_poster);self["poster"].show()
                    self._details_visual_state["poster"]=poster_authority
                    self._details_visual_state["detail_poster"]=bundled_detail_poster
                    self._persistent_detail_poster_locked=True
                except Exception as exc:optional_failure("ui.details_final_poster_apply",exc)
            else:
                self._details_visual_state["poster"]=poster_authority
                self._details_visual_state.pop("detail_poster",None)
                self._persistent_detail_poster_locked=False
                self._decode_picture(poster_authority)
        elif bundled_detail_poster and os.path.isfile(bundled_detail_poster):
            try:
                if self["poster"].instance is not None:
                    self["poster"].instance.setPixmapFromFile(bundled_detail_poster);self["poster"].show()
                self._details_visual_state["poster"]=bundled_poster or bundled_detail_poster
                self._details_visual_state["detail_poster"]=bundled_detail_poster
                self._persistent_detail_poster_locked=True
            except Exception as exc:optional_failure("ui.details_final_poster_apply",exc)
        elif bundled_poster and os.path.isfile(bundled_poster):
            self._details_visual_state["poster"]=bundled_poster
            self._decode_picture(bundled_poster)
        elif cached_poster:
            self._details_visual_state["poster"]=cached_poster
            self._decode_picture(cached_poster)
        else:
            # Xtream provider art loads asynchronously immediately after layout.
            # Stalker retains the neutral placeholder/external-only policy.
            self._decode_picture(asset(ph))
        try:
            self["dynamic_bg"].hide();self["cinematic_bg"].hide();self["cinematic_edge"].hide()
        except Exception as exc:optional_failure("ui",exc)
        self._apply_detail_chrome(None)
        try:
            if bundle:
                active_poster=(poster_authority if poster_authority and os.path.isfile(poster_authority) else str(bundle.get("poster") or ""))
                active_detail=(str(bundle.get("detail_poster") or "") if (not poster_authority or detail_matches_authority) else "")
                self._details_visual_state.update({
                    "poster":active_poster,
                    "backdrop":str(bundle.get("backdrop") or ""),
                    "backdrop_present":str(bundle.get("backdrop_present") or ""),
                    "detail_poster":active_detail,
                    "theme":str(bundle.get("theme") or ""),
                    "accent":str(bundle.get("accent") or ""),
                    "edge":str(bundle.get("edge") or ""),
                    "chrome":dict(bundle.get("chrome") or {}),
                })
                self._restore_details_visual_state(reload_disk=False)
                if active_poster:self._adaptive_source_local=str(active_poster)
                if getattr(self,"_persistent_adaptive_locked",False):self._adaptive_palette_locked=True
                if getattr(self,"_persistent_poster_locked",False) and not (poster_authority and os.path.isfile(poster_authority)):
                    self._poster_authoritative_source=str(bundle.get("poster") or self._poster_authoritative_source or "")
                    self._adaptive_authoritative_source=str(bundle.get("poster") or self._adaptive_authoritative_source or "")
                if getattr(self,"_persistent_backdrop_locked",False):
                    self._backdrop_rank=max(5,int(getattr(self,"_backdrop_rank",0) or 0))
        except Exception as exc:optional_failure("ui.details_bundle_restore",exc)
        if isinstance(cached,dict) and cached.get("matched") and cached.get("identity_verified") and cached_source!="portal_payload":
            try:
                self._apply_tmdb_metadata(dict(cached));self["status"].setText(_("Cached • HDD"))
            except Exception as exc:optional_failure("ui.local_detail_cache_apply",exc)
        try:self._schedule_search_visual_peer_sync(cached if isinstance(cached,dict) else None)
        except Exception as exc:optional_failure("ui.search_peer_layout_sync",exc)
        self._load_cinematic_backdrop(False)
        # Dual-source details: server/provider first, TMDB enriches the merged row.
        self._load_provider_metadata()
        try:self._backdrop_watch_timer.start(1400,True)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._sync_detail_text_direction();self._refresh_detail_visuals();self._schedule_description_scroll();self._ensure_description_visible()
        try:self._desc_guard_timer.start(700,True)
        except Exception as exc:optional_failure("ui",exc)
        try:self["accent_frame"].hide()
        except Exception as exc:optional_failure("ui",exc)
        try:self._apply_adaptive_detail_fonts()
        except Exception as exc:optional_failure("ui.detail_initial_adaptive_fonts",exc)

    def _stop_title_fit_timer(self):
        try:self._title_fit_timer.stop()
        except Exception as exc:optional_failure("ui.detail_title_timer_stop",exc)
        try:
            if self._title_fit_conn is not None:
                self._title_fit_conn.disconnect();self._title_fit_conn=None
        except Exception as exc:optional_failure("ui.detail_title_timer_disconnect",exc)

    def _schedule_title_fit(self):
        # R262: one stable title-fit sequence per actual title.  The old timer
        # called _schedule_title_fit() from inside its own tick, resetting the
        # pass counter forever; late provider/TMDb paints could then alternate
        # 42px and 46px fits and make the visible name pulse.
        try:
            value=" ".join(str(self["name"].getText() or "").replace("\n"," ").split()).strip()
        except Exception:
            value=""
        key=value
        if key and key==getattr(self,"_title_fit_key","") and int(getattr(self,"_title_fit_pass",0) or 0)>=2:
            return
        self._title_fit_key=key
        self._title_fit_pass=0
        try:
            self._title_fit_timer.stop()
            self._title_fit_timer.start(45,True)
        except Exception as exc:optional_failure("ui.detail_title_timer_start",exc)

    def _title_fit_tick(self):
        try:self._fit_single_line_font("name",self["name"].getText(),42,14)
        except Exception as exc:optional_failure("ui.detail_title_tick",exc)
        self._title_fit_pass=int(getattr(self,"_title_fit_pass",0) or 0)+1
        if self._title_fit_pass<2:
            try:self._title_fit_timer.start(120,True)
            except Exception as exc:optional_failure("ui.detail_title_timer_retry",exc)

    def _fit_single_line_font(self, widget_name, text, max_size=42, min_size=16):
        value=" ".join(str(text or "").replace("\n"," ").split()).strip()
        try:
            widget=self[widget_name]
            inst=widget.instance
            if inst is None:return
            fixed_width = 1318 if widget_name == "name" else (680 if widget_name == "genre_text" else (1318 if widget_name == "cast_text" else 0))
            width=max(80,(int(fixed_width)-28) if fixed_width else (int(inst.size().width())-28))
            try:
                if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            except Exception as exc:optional_failure("ui.detail_title_nowrap",exc)

            def glyph_units():
                units=0.0;has_ar=False;has_latin=False
                for ch in value:
                    code=ord(ch)
                    if 0x0600<=code<=0x06ff:
                        has_ar=True;units+=0.78
                    elif "A"<=ch<="Z":
                        has_latin=True;units+=0.72
                    elif "a"<=ch<="z":
                        has_latin=True;units+=0.59
                    elif ch.isdigit():units+=0.58
                    elif ch.isspace():units+=0.34
                    elif ch in ".,:;!?'`-_/()[]{}":units+=0.36
                    else:units+=0.66
                return units*(1.16 if has_ar and has_latin else 1.08)

            units=max(1.0,glyph_units())
            estimated=int(float(width)/units)
            chosen=max(int(min_size),min(int(max_size),estimated))

            for size in range(chosen,int(min_size)-1,-1):
                try:
                    probe=eLabel();probe.setFont(gFont("Regular",size));probe.setText(value)
                    native=int(probe.calculateSize().width())
                except Exception:native=0
                if native<=0 or native<=width:
                    chosen=size;break
            inst.setFont(gFont("Regular",chosen))
            if widget_name in ("name","genre_text","cast_text") and hasattr(inst,"setHAlign"):
                inst.setHAlign(RT_HALIGN_CENTER)
            if widget_name=="name":self._center_detail_label_geometry("name",470,1318,10)
        except Exception as exc:
            optional_failure("ui.detail_title_adaptive_font",exc)


    def _fit_description_font(self):
        try:
            value=str(self["description"].getText() or "").strip()
            widget=self["description"]
            width=max(200,int(widget.instance.size().width()))
            height=max(40,int(widget.instance.size().height()))
            # Receiver Arabic metrics tend to underestimate wrapping. Use a
            # deliberately conservative width factor so long synopses shrink
            # enough to occupy the spare lower lines inside the SAME box.
            factor=0.73 if self._contains_arabic(value) else 0.59
            chosen=13
            for size in range(24,12,-1):
                chars_per_line=max(12,int(width/max(1.0,size*factor)))
                explicit=value.count("\n")
                weighted=len(value)+explicit*chars_per_line
                lines=max(1,int(math.ceil(float(weighted)/float(chars_per_line))))
                needed=lines*int(size*1.28)
                if needed<=height:
                    chosen=size
                    break
            widget.instance.setFont(gFont("Regular",chosen))
        except Exception as exc:
            optional_failure("ui.detail_description_adaptive_font",exc)

    def _fit_compact_text(self, widget_name, text=None, max_size=22, min_size=10, padding=14):
        """Fit a single-line fixed-width Details label to the largest safe font.

        This is deliberately visual-only: it never edits provider/TMDB text. It
        measures the exact rendered label when possible, then falls back to a
        conservative glyph-width estimate so long translated strings shrink
        before they clip while short values keep the larger native size.
        """
        try:
            widget=self[widget_name]
            inst=widget.instance
            if inst is None:return
            value=str(widget.getText() if text is None else text or "").replace("\n"," ").strip()
            if not value:return
            try:
                if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            except Exception:pass
            fixed_width = 680 if widget_name == "genre_text" else (1318 if widget_name == "cast_text" else 0)
            width=max(24,(int(fixed_width)-int(padding)) if fixed_width else (int(inst.size().width())-int(padding)))
            units=0.0
            has_ar=False
            for ch in value:
                code=ord(ch)
                if 0x0600<=code<=0x06ff:
                    has_ar=True;units+=0.80
                elif "A"<=ch<="Z":units+=0.72
                elif "a"<=ch<="z":units+=0.59
                elif ch.isdigit():units+=0.58
                elif ch.isspace():units+=0.34
                elif ch in ".,:;!?'`-_/()[]{}•":units+=0.36
                else:units+=0.68
            estimate_factor=1.17 if has_ar else 1.10
            estimated=max(int(min_size),min(int(max_size),int(float(width)/max(1.0,units*estimate_factor))))
            chosen=int(min_size)
            for size in range(int(max_size),int(min_size)-1,-1):
                if size>estimated+2:
                    continue
                try:
                    probe=eLabel();probe.setFont(gFont("Regular",size));probe.setText(value)
                    native=int(probe.calculateSize().width())
                except Exception:
                    native=0
                painted=max(native,int(units*size*estimate_factor)) if native>0 else int(units*size*estimate_factor)
                if painted<=width:
                    chosen=size;break
            inst.setFont(gFont("Regular",chosen))
            if widget_name in ("name","genre_text","cast_text") and hasattr(inst,"setHAlign"):
                inst.setHAlign(RT_HALIGN_CENTER)
            if widget_name=="genre_text":self._center_detail_label_geometry("genre_text",1185,680,14)
            elif widget_name=="cast_text":self._center_detail_label_geometry("cast_text",470,1318,14)
            elif widget_name=="name":self._center_detail_label_geometry("name",470,1318,10)
        except Exception as exc:
            optional_failure("ui.detail_compact_adaptive_font",exc)

    @staticmethod
    def _season_count_text(count):
        try:
            number=int(count)
            return ((_('%d season') if number==1 else _('%d seasons')) % number)
        except Exception:
            return _('%s seasons') % str(count or '')

    def _fit_country_font(self):
        """Fit the country name to its pill without changing any other detail UI."""
        try:
            widget=self["country_text"]
            inst=widget.instance
            if inst is None:return
            value=str(widget.getText() or "").strip()
            if not value:return
            # Use the same adaptive principle as the other fitted labels, but
            # reserve real breathing room inside the compact country pill.
            # Receiver font metrics are optimistic here, so combine the native
            # measurement with a glyph-width estimate and choose the safer one.
            width=max(30,int(inst.size().width())-20)
            units=0.0
            for ch in value:
                if "A"<=ch<="Z":units+=0.72
                elif "a"<=ch<="z":units+=0.59
                elif ch.isdigit():units+=0.58
                elif ch.isspace():units+=0.34
                elif ch in ".,:;!?'`-_/()[]{}":units+=0.36
                else:units+=0.66
            estimated=max(11,min(22,int(float(width)/max(1.0,units*1.10))))
            chosen=11
            for size in range(estimated,10,-1):
                try:
                    probe=eLabel();probe.setFont(gFont("Regular",size));probe.setText(value)
                    native=int(probe.calculateSize().width())
                except Exception:
                    native=0
                painted=int(native*1.20)+6 if native>0 else 0
                if painted<=0 or painted<=width:
                    chosen=size;break
            inst.setFont(gFont("Regular",chosen))
        except Exception as exc:
            optional_failure("ui.detail_country_adaptive_font",exc)

    def _apply_adaptive_detail_fonts(self):
        self._fit_single_line_font("name",self["name"].getText(),46,24)
        self._fit_description_font()
        # Every fixed metadata pill follows the same rule: short text keeps the
        # generous native size; longer/localized text shrinks only as much as
        # needed to remain inside its existing box. No layout positions change.
        self._fit_compact_text("quality_text",max_size=21,min_size=11,padding=12)
        self._fit_compact_text("year_text",max_size=22,min_size=12,padding=8)
        self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
        self._fit_country_font()
        self._fit_compact_text("genre_text",max_size=26,min_size=17,padding=16)

    @staticmethod
    def _contains_arabic(value):
        return bool(re.search(r"[\u0600-\u06ff]", str(value or "")))

    def _align_detail_widget(self,name,value):
        try:
            inst=self[name].instance
            if inst is not None and hasattr(inst,"setHAlign"):
                inst.setHAlign(RT_HALIGN_RIGHT if self._contains_arabic(value) else RT_HALIGN_LEFT)
        except Exception as exc:optional_failure("ui",exc)

    def _center_detail_label_geometry(self, name, box_x, box_width, pad=8):
        """Center the *rendered label box* inside its visual container.

        OpenBH/eLabel can re-interpret halign when BiDi text is updated at
        runtime. V6.5.20 keeps the V6.5.19 geometric centering and does not trust halign alone: it measures the
        actual rendered text, shrinks the label widget to that width, then moves
        the widget so its geometric center equals the container center. This
        makes English, Arabic, German and mixed Arabic/Latin strings land at the
        same physical center on the receiver.
        """
        try:
            widget=self[name]
            inst=widget.instance
            if inst is None:return
            value=str(widget.getText() or "").replace("\n"," ").strip()
            if not value:return
            try:
                measured=int(inst.calculateSize().width())
            except Exception:
                measured=0
            max_width=max(24,int(box_width)-int(pad)*2)
            target=max(24,min(max_width,(measured+int(pad)*2) if measured>0 else max_width))
            pos=inst.position();size=inst.size()
            y=int(pos.y());height=int(size.height())
            x=int(round(float(box_x)+(float(box_width)-float(target))/2.0))
            inst.resize(eSize(int(target),height))
            inst.move(ePoint(x,y))
            if hasattr(inst,"setHAlign"):inst.setHAlign(RT_HALIGN_CENTER)
            if hasattr(inst,"setVAlign"):inst.setVAlign(RT_VALIGN_CENTER)
            if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
        except Exception as exc:
            optional_failure("ui.detail_geometric_center",exc)

    def _sync_detail_text_direction(self):
        # release: retain release physical centering while compacting the vertical Details cluster.
        # The three visual containers are fixed in the Details skin:
        # title/overview span, genre pill, and full-width cast glass strip.
        for name in ("name","genre_text","cast_text"):
            try:
                inst=self[name].instance
                if inst is not None:
                    if hasattr(inst,"setHAlign"): inst.setHAlign(RT_HALIGN_CENTER)
                    if hasattr(inst,"setVAlign"): inst.setVAlign(RT_VALIGN_CENTER)
                    if hasattr(inst,"setNoWrap"): inst.setNoWrap(1)
            except Exception as exc:optional_failure("ui.detail_force_center",exc)
        self._center_detail_label_geometry("name",470,1318,10)
        self._center_detail_label_geometry("genre_text",1185,680,14)
        self._center_detail_label_geometry("cast_text",470,1318,14)
        try:self._align_detail_widget("description",self["description"].getText())
        except Exception as exc:optional_failure("ui",exc)

    @staticmethod
    def _extract_numeric_rating(text):
        m=re.search(r"(\d+(?:\.\d+)?)", str(text or ""))
        return m.group(1) if m else ""

    @staticmethod
    def _quality_asset_name(value):
        text=str(value or "").upper()
        if re.search(r"\b(?:4K|UHD|2160P?)\b", text):
            return "us65_quality_4k_122x34.png"
        if re.search(r"\b(?:FULL[ ._-]?HD|FHD|1080P?)\b", text):
            return "us65_quality_fullhd_122x34.png"
        if re.search(r"\b(?:HD|720P?)\b", text):
            return "us65_quality_hd_122x34.png"
        if re.search(r"\b(?:SD|576P?|480P?)\b", text):
            return "us93_quality_sd_122x34.png"
        return None

    _COUNTRY_NAMES = {
        "EG":"مصر","LB":"لبنان","SA":"السعودية","AE":"الإمارات","KW":"الكويت","QA":"قطر","BH":"البحرين","OM":"عُمان","JO":"الأردن","SY":"سوريا","IQ":"العراق","PS":"فلسطين","YE":"اليمن","MA":"المغرب","DZ":"الجزائر","TN":"تونس","LY":"ليبيا","SD":"السودان","US":"USA","GB":"UK","FR":"France","DE":"Germany","IT":"Italy","ES":"Spain","TR":"Turkey","IN":"India","KR":"Korea","JP":"Japan","CN":"China","CA":"Canada","MX":"Mexico","BR":"Brazil","AR":"Argentina","RU":"Russia","AU":"Australia"
    }
    # Compact English display codes beside flags.  These are UI labels only;
    # provider/TMDB country data remains untouched.  Keep the familiar receiver
    # abbreviations requested by users (EG, KSA, UAE, UK, USA, ...).
    _COUNTRY_CODES_EN = {
        "EG":"EG","LB":"LB","SA":"KSA","AE":"UAE","KW":"KW","QA":"QA","BH":"BH","OM":"OM","JO":"JO","SY":"SY","IQ":"IQ","PS":"PS","YE":"YE","MA":"MA","DZ":"DZ","TN":"TN","LY":"LY","SD":"SD",
        "US":"USA","GB":"UK","FR":"FR","DE":"DE","IT":"IT","ES":"ES","TR":"TR","IN":"IN","KR":"KR","JP":"JP","CN":"CN","CA":"CA","MX":"MX","BR":"BR","AR":"AR","RU":"RU","AU":"AU"
    }

    @classmethod
    def _country_code(cls,value):
        # Accept provider/TMDb country values in all shapes we actually receive:
        # ISO code, localized/English name, dict, list/tuple, or stringified data.
        if isinstance(value,(list,tuple,set)):
            for row in value:
                code=cls._country_code(row)
                if code:return code
            return ""
        if isinstance(value,dict):
            for key in ("iso_3166_1","country_code","code","name"):
                code=cls._country_code(value.get(key))
                if code:return code
            return ""
        raw=str(value or "").strip()
        if not raw:return ""
        upper=raw.upper().strip()
        aliases={
            "EGYPT":"EG","ARAB REPUBLIC OF EGYPT":"EG","مصر":"EG","جمهورية مصر العربية":"EG",
            "LEBANON":"LB","لبنان":"LB","SAUDI ARABIA":"SA","السعودية":"SA",
            "UNITED ARAB EMIRATES":"AE","UAE":"AE","الإمارات":"AE",
            "UNITED STATES":"US","UNITED STATES OF AMERICA":"US","USA":"US",
            "UNITED KINGDOM":"GB","GREAT BRITAIN":"GB","UK":"GB",
            "TURKEY":"TR","TÜRKIYE":"TR","TURKIYE":"TR",
            "SOUTH KOREA":"KR","REPUBLIC OF KOREA":"KR","KOREA":"KR",
            "NORTH KOREA":"KP","JAPAN":"JP","CHINA":"CN","INDIA":"IN",
            "FRANCE":"FR","GERMANY":"DE","ITALY":"IT","SPAIN":"ES",
            "CANADA":"CA","MEXICO":"MX","BRAZIL":"BR","ARGENTINA":"AR",
            "RUSSIA":"RU","RUSSIAN FEDERATION":"RU","AUSTRALIA":"AU",
            "KUWAIT":"KW","QATAR":"QA","BAHRAIN":"BH","OMAN":"OM",
            "JORDAN":"JO","SYRIA":"SY","IRAQ":"IQ","PALESTINE":"PS",
            "MOROCCO":"MA","ALGERIA":"DZ","TUNISIA":"TN","LIBYA":"LY","SUDAN":"SD"
        }
        if upper in aliases:return aliases[upper]
        # Reverse-map the labels already supported by the UI.
        for code,name in cls._COUNTRY_NAMES.items():
            if upper==str(name).upper():return code
        if len(upper)==2 and upper.isalpha():return upper
        # Handle stringified provider objects/lists, e.g. {'iso_3166_1': 'EG'}.
        m=re.search(r"(?:ISO_3166_1|COUNTRY_CODE|CODE)[^A-Z]{0,12}['\"]?([A-Z]{2})\b",upper)
        if m:return m.group(1)
        m=re.search(r"\b([A-Z]{2})\b",upper)
        if m and os.path.isfile(asset("flags_iso/%s.png"%m.group(1).lower())):return m.group(1)
        return ""

    @classmethod
    def _normalized_country(cls,value):
        raw=str(value or "").strip()
        code=cls._country_code(raw)
        if current_language()=="en":
            return cls._COUNTRY_CODES_EN.get(code, code or (raw[:12] if raw else ""))
        return cls._COUNTRY_NAMES.get(code, raw[:12] if raw else "")

    @classmethod
    def _country_flag_asset(cls,value):
        code=cls._country_code(value)
        if not code:return None
        name="flags_iso/%s.png"%code.lower()
        return name if os.path.isfile(asset(name)) else None

    def _set_widget_pixmap(self, name, asset_name):
        try:
            inst=self[name].instance
            path=asset(asset_name) if asset_name else None
            if inst is not None and path and os.path.isfile(path):
                inst.setPixmapFromFile(path)
                self[name].show()
            else:
                self[name].hide()
        except Exception as exc:optional_failure("ui",exc)

    def _fit_remote_hint_underlines(self):
        """Make each colored underline match the rendered action label exactly."""
        try:
            from enigma import eSize
            for name in ("red","green","yellow","blue"):
                text=str(self[name].getText() or "")
                width=0
                try:
                    probe=eLabel();probe.setFont(gFont("Regular",26));probe.setText(text)
                    width=int(probe.calculateSize().width())
                except Exception:
                    width=max(24,int(len(text)*15))
                width=max(8,min(width,270))
                comp=self[name+"_line"]
                if getattr(comp,"instance",None) is not None:
                    comp.instance.resize(eSize(width,3))
                    comp.show()
        except Exception as exc:
            optional_failure("ui.details_remote_hint_underlines",exc)

    _poster_laser_color_cache = {}

    def _poster_laser_color_from_chrome(self, chrome):
        """Reuse the existing adaptive color without rebuilding any artwork.

        Hotfix 15 deliberately never reads or displays the retired 380x580
        poster frame.  New cached chrome carries accent_color directly; older
        caches are sampled from their already-existing glass border.
        """
        try:
            explicit=str((chrome or {}).get("accent_color") or getattr(self,"_poster_laser_color_hint","") or "").strip() if isinstance(chrome,dict) else str(getattr(self,"_poster_laser_color_hint","") or "").strip()
            if re.match(r"^#[0-9a-fA-F]{6}$",explicit):
                return explicit.lower()
            candidates=[]
            if isinstance(chrome,dict):
                for key in ("panel_detail","overview_detail","quality","genre_detail","panel","overview"):
                    path=str(chrome.get(key) or "")
                    if path:candidates.append(path)
            try:
                bundle=getattr(self,"_persistent_visual_bundle",{}) or {}
                for key in ("grid_card","card","grid_thumb"):
                    path=str(bundle.get(key) or "") if isinstance(bundle,dict) else ""
                    if path:candidates.append(path)
            except Exception:
                pass
            for path in candidates:
                if not path or not os.path.isfile(path):
                    continue
                cached=self._poster_laser_color_cache.get(path)
                if cached:
                    return cached
                from PIL import Image as _LaserImage
                with _LaserImage.open(path) as image:
                    image=image.convert("RGBA")
                    w,h=image.size
                    # Dynamic glass draws its exact adaptive border at x=2.
                    # Sample the vertical midpoint where rounded corners cannot
                    # interfere.  This is a tiny HDD read, not a palette build.
                    x=min(max(0,2),w-1); y=max(0,min(h-1,h//2))
                    r,g,b,a=image.getpixel((x,y))
                    if a<=40:
                        # Defensive 2..8px scan for legacy glass variants.
                        best=None
                        for sx in range(2,min(9,w)):
                            rr,gg,bb,aa=image.getpixel((sx,y))
                            if best is None or aa>best[0]:best=(aa,rr,gg,bb)
                        if best:
                            a,r,g,b=best
                    if a>40:
                        color="#%02x%02x%02x"%(r,g,b)
                        self._poster_laser_color_cache[path]=color
                        if len(self._poster_laser_color_cache)>96:
                            self._poster_laser_color_cache.clear();self._poster_laser_color_cache[path]=color
                        return color
        except Exception as exc:
            optional_failure("ui.details_laser_color",exc)
        # Chrome can legitimately be absent on first open or after an old cache
        # migration. Sample the already-visible local poster so the 2px laser is
        # deterministic instead of randomly disappearing. No network is involved.
        try:
            source=str(getattr(self,"_poster_authoritative_source","") or getattr(self,"_image_displayed_path","") or (getattr(self,"_details_visual_state",{}) or {}).get("poster") or "")
            if source and os.path.isfile(source):
                from PIL import Image as _LaserPoster, ImageStat as _LaserStat
                with _LaserPoster.open(source) as im:
                    im=im.convert("RGB");im.thumbnail((48,72))
                    pixels=[]
                    for r,g,b in list(im.getdata()):
                        hi=max(r,g,b);lo=min(r,g,b);sat=hi-lo;lum=(r+g+b)//3
                        if sat>=28 and 42<=lum<=220:pixels.append((r,g,b,sat))
                    if pixels:
                        pixels.sort(key=lambda x:x[3],reverse=True);top=pixels[:max(8,min(48,len(pixels)//3 or 8))]
                        r=sum(x[0] for x in top)//len(top);g=sum(x[1] for x in top)//len(top);b=sum(x[2] for x in top)//len(top)
                        return "#%02x%02x%02x"%(min(255,int(r*1.10+10)),min(255,int(g*1.10+10)),min(255,int(b*1.10+10)))
        except Exception as exc:
            optional_failure("ui.details_laser_poster_fallback",exc)
        return "#d88463"

    def _apply_poster_laser_outline(self, chrome):
        # release: Details poster remains intentionally frameless. Some provider
        # posters arrive undersized; a fixed outline exposes the empty widget
        # rectangle and looks wrong. Keep compatibility widgets hidden.
        for name in ("poster_laser_top","poster_laser_bottom","poster_laser_left","poster_laser_right"):
            try:self[name].hide()
            except Exception:pass

    def _sharp_detail_chrome_cache_path(self, source, key, target_size):
        """Compute the exact nine-slice cache path without decoding the PNG."""
        try:
            source=str(source or "")
            if not source or not os.path.isfile(source):return ""
            tw,th=[int(x) for x in target_size]
            cache=os.path.join(PERSISTENT_GENERATED_DIR,"details_sharp_v6513")
            stamp="%s|%s|%s|%s|%s"%(source,os.path.getmtime(source),key,tw,th)
            return os.path.join(cache,hashlib.sha1(stamp.encode("utf-8","ignore")).hexdigest()[:24]+".png")
        except Exception:
            return ""

    def _queue_sharp_detail_chrome_asset(self, widget, source, key, target_size):
        """Build an exact-size glass asset off the Enigma2 GUI thread.

        The source is painted immediately.  If an exact nine-slice copy is needed,
        it is generated in the bounded async pool and swapped in later.  This keeps
        Details/Series opening independent from Pillow PNG resize/optimize time.
        """
        source=str(source or "")
        if not source or not os.path.isfile(source) or getattr(self,"_screen_closed",False):return
        identity=(source,str(key),tuple(int(x) for x in target_size))
        if source in getattr(self,"_sharp_detail_exact_sources",set()) or identity in getattr(self,"_sharp_detail_pending",set()):return
        cached=self._sharp_detail_chrome_cache_path(source,key,target_size)
        if cached and _valid_cache_file(cached):
            try:
                if self[widget].instance is not None:self[widget].instance.setPixmapFromFile(cached)
            except Exception as exc:optional_failure("ui.details_sharp_cached_apply",exc)
            return
        self._sharp_detail_pending.add(identity)
        cancel_event=getattr(self,"_details_cancel_event",None)
        screen_ref=weakref.ref(self)
        def work():
            if cancel_event is not None and cancel_event.is_set():return ""
            return self._sharp_detail_chrome_asset(source,key,target_size)
        def done(future):
            error=None;path=""
            try:path=str(future.result() or "")
            except Exception as exc:error=exc
            def apply():
                screen=screen_ref()
                if screen is None:return
                screen._sharp_detail_pending.discard(identity)
                if error is not None:
                    optional_failure("ui.details_sharp_async",error);return
                if getattr(screen,"_screen_closed",False):return
                if cancel_event is not None and cancel_event.is_set():return
                if path==source:screen._sharp_detail_exact_sources.add(source)
                if path and os.path.isfile(path):
                    try:
                        if screen[widget].instance is not None:screen[widget].instance.setPixmapFromFile(path)
                    except Exception as exc:optional_failure("ui.details_sharp_async_apply",exc)
            try:reactor.callFromThread(apply)
            except Exception:apply()
        try:
            task_id=hashlib.sha1((source+"|"+str(key)+"|"+str(tuple(target_size))).encode("utf-8","ignore")).hexdigest()[:20]
            future=_DETAIL_PREFETCH_EXECUTOR.submit(work,priority=1,_task_key="details-sharp:%s"%task_id)
            self._track_details_future(future)
            future.add_done_callback(done)
            try:self._async_set_poll_interval(120)
            except Exception:pass
        except Exception as exc:
            self._sharp_detail_pending.discard(identity)
            optional_failure("ui.details_sharp_async_submit",exc)

    def _sharp_detail_chrome_asset(self, source, key, target_size):
        """Return an exact-size nine-slice copy so Enigma2 never blurs rounded glass corners."""
        try:
            source=str(source or "")
            if not source or not os.path.isfile(source):return source
            tw,th=[int(x) for x in target_size]
            from PIL import Image as _SharpImg
            with _SharpImg.open(source) as _probe:
                sw,sh=_probe.size
            if (sw,sh)==(tw,th):return source
            out=self._sharp_detail_chrome_cache_path(source,key,(tw,th))
            if not out:return source
            os.makedirs(os.path.dirname(out),mode=0o700,exist_ok=True)
            if _valid_cache_file(out):return out
            with _SharpImg.open(source) as im:
                im=im.convert("RGBA");sw,sh=im.size
                # Preserve the source corners and edge glow; stretch only the centers.
                cx=max(10,min(38,sw//5,tw//5));cy=max(8,min(22,sh//4,th//4))
                left=right=cx;top=bottom=cy
                canvas=_SharpImg.new("RGBA",(tw,th),(0,0,0,0))
                sx=(0,left,sw-right,sw);sy=(0,top,sh-bottom,sh)
                dx=(0,left,tw-right,tw);dy=(0,top,th-bottom,th)
                for yi in range(3):
                    for xi in range(3):
                        tile=im.crop((sx[xi],sy[yi],sx[xi+1],sy[yi+1]))
                        size=(dx[xi+1]-dx[xi],dy[yi+1]-dy[yi])
                        if tile.size!=size:tile=tile.resize(size,_SharpImg.Resampling.LANCZOS)
                        canvas.alpha_composite(tile,(dx[xi],dy[yi]))
                canvas.save(out,"PNG",optimize=True)
            return out
        except Exception as exc:
            optional_failure("ui.details_sharp_chrome",exc)
            return source

    def _apply_detail_chrome(self, chrome):
        # release: cast strip is generated natively at 1318x50 by the SAME
        # dynamic-glass recipe as genre_detail. Never stretch the 680px genre
        # bitmap across the cast row; that was the source of broken end-caps.
        try:
            chrome=dict(chrome or {}) if isinstance(chrome,dict) else {}
            # GUI-thread firewall: never invoke the Pillow chrome builder here.
            # The screen paints neutral/cached glass immediately and
            # _schedule_poster_visuals() builds any missing adaptive assets on the
            # bounded background executor, then reapplies them in-place.
            if chrome:
                self._details_visual_state["chrome"]=dict(chrome)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        for old_widget in ("accent_frame","poster_card_bg","poster_border_overlay"):
            try:
                comp=self[old_widget]
                if getattr(comp,"instance",None) is not None:
                    try:comp.instance.setPixmap(None)
                    except Exception:pass
                comp.hide()
            except Exception:
                pass
        mapping={
            "panel_bg":("panel_detail","us66_neutral_panel.png"),
            "overview_bg":("overview_detail","us89_overview_card.png"),
            "cast_card_bg":("cast_detail","us66_neutral_pill_genre.png"),
            "poster_footer_bg":("poster_footer_detail","us66_neutral_poster_footer.png"),
            "imdb_pill_bg":("quality","us66_neutral_pill_quality.png"),
            "tmdb_pill_bg":("quality","us66_neutral_pill_quality.png"),
            "quality_pill_bg":("quality","us66_neutral_pill_quality.png"),
            "year_pill_bg":("year","us66_neutral_pill_year.png"),
            "runtime_pill_bg":("runtime","us66_neutral_pill_runtime.png"),
            "country_pill_bg":("country","us66_neutral_pill_country.png"),
            "genre_pill_bg":("genre_detail","us66_neutral_pill_genre.png"),
        }
        for widget,(key,fallback) in mapping.items():
            try:
                try:
                    component=self[widget]
                except Exception:
                    continue
                if self.__class__.__name__ == "SeriesEpisodesScreen":
                    if key == "panel_detail": key = "panel"
                    elif key == "overview_detail": key = "overview"
                    elif key == "genre_detail": key = "genre"
                    elif key == "poster_footer_detail": key = "poster_footer"
                    elif key == "cast_card": key = "cast_card_ep"
                    elif key == "director_card": key = "director_card_ep"
                    elif key == "writer_card": key = "writer_card_ep"
                path=(chrome or {}).get(key) if isinstance(chrome,dict) else None
                if not path or not os.path.isfile(path):path=asset(fallback) if fallback else None
                _exact={"panel_detail":(1410,368),"overview_detail":(1318,170),"poster_footer_detail":(360,86)}.get(key)
                inst=component.instance
                if inst is not None and path and os.path.isfile(path):
                    # A Player transition can invalidate the native gPixmap while
                    # the Python-side cached path is still correct. Clear before
                    # rebinding the same HDD asset; never rebuild it here.
                    try:inst.setPixmap(None)
                    except Exception:pass
                    # Never resize/optimize PNGs on the GUI thread. Paint the
                    # available glass immediately, then refine to its cached exact
                    # nine-slice form asynchronously when necessary.
                    inst.setPixmapFromFile(path);self[widget].show()
                    if _exact:self._queue_sharp_detail_chrome_asset(widget,path,key,_exact)
            except Exception as exc:optional_failure("ui.detail_chrome_apply",exc)
        self._apply_poster_laser_outline(chrome)
        for widget in ("rating_ring","rating_source","imdb_text","poster_match_text","status"):
            try:self[widget].hide()
            except Exception as exc:optional_failure("ui.detail_rating_hide",exc)
        for widget in ("tmdb_pill_bg","imdb_pill_bg"):
            try:self[widget].hide()
            except Exception:pass
        for widget in ("poster_footer_bg","rating_score","poster_imdb_logo","poster_imdb_value"):
            try:self[widget].show()
            except Exception as exc:optional_failure("ui.detail_rating_show",exc)

    def _detail_quality_value(self):
        item=self.item if isinstance(self.item,dict) else {}
        values=[]
        def walk(v,depth=0):
            if depth>4:return
            if isinstance(v,dict):
                for k,x in v.items():
                    if str(k).lower() in ("password","token","cookie"):continue
                    walk(x,depth+1)
            elif isinstance(v,(list,tuple)):
                for x in v[:40]:walk(x,depth+1)
            elif v is not None:
                values.append(str(v))
        walk(item)
        joined=" ".join(values).upper()
        # Explicit catalogue text wins immediately: title, description and all
        # portal metadata. This is intentionally checked before runtime cache.
        if re.search(r"\b(?:4K|UHD|2160P?|3840\s*[Xx×]\s*2160)\b",joined):return "4K"
        if re.search(r"\b(?:FULL[ ._-]?HD|FHD|1080P?|1920\s*[Xx×]\s*1080)\b",joined):return "FHD"
        if re.search(r"\b(?:HD|720P?|1280\s*[Xx×]\s*720)\b",joined):return "HD"
        if re.search(r"\b(?:SD|576P?|480P?|720\s*[Xx×]\s*(?:576|480))\b",joined):return "SD"
        h=str(item.get("height") or item.get("video_height") or item.get("resolution_height") or item.get("stream_height") or "").strip()
        w=str(item.get("width") or item.get("video_width") or item.get("resolution_width") or item.get("stream_width") or "").strip()
        try:
            hv=int(float(re.sub(r"[^0-9.]","",h) or 0)); wv=int(float(re.sub(r"[^0-9.]","",w) or 0))
            if hv>=2000 or wv>=3500:return "4K"
            if hv>=1000 or wv>=1800:return "FHD"
            if hv>=700 or wv>=1200:return "HD"
            if hv>=400 or wv>=640:return "SD"
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            cached=load_content_quality(self.profile,self.media_type,getattr(self,"_portal_item",item))
            if cached:return cached
        except Exception as exc:optional_failure("ui.quality_cache_read",exc)
        return ""

    def _refresh_detail_visuals(self):
        raw_quality = str(self["quality_text"].getText() or self._detail_quality_value() or "")
        quality_asset = self._quality_asset_name(raw_quality or str(self.item.get("name") or self.item.get("title") or ""))
        self._set_widget_pixmap("quality_logo", quality_asset)
        try:
            if quality_asset:
                self["quality_text"].setText("")
            elif not self["quality_text"].getText():
                self["quality_text"].setText((raw_quality or _("AUTO"))[:10])
        except Exception as exc:optional_failure("ui",exc)

        # Movie duration is a terminal playable item, not a folder.  Use the
        # same premium circular play glyph as the episode list; TV/series keep
        # the yellow folder because the pill represents seasons/containers.
        if self.media_type == "vod":
            self._set_widget_pixmap("runtime_icon", "us173_movie_runtime_32_icononly.png")
        else:
            self._set_widget_pixmap("runtime_icon", "us166_details_folder_yellow_32.png")

        country_raw = getattr(self,"_country_raw","") or self.item.get("country_code") or self.item.get("country") or self["country_text"].getText() or ""
        country_value = self._normalized_country(country_raw)
        try:self["country_text"].setText(country_value)
        except Exception as exc:optional_failure("ui",exc)
        self._fit_country_font()
        self._set_widget_pixmap("country_flag", self._country_flag_asset(country_raw))
        try:
            self._fit_compact_text("quality_text",max_size=21,min_size=11,padding=12)
            self._fit_compact_text("year_text",max_size=22,min_size=12,padding=8)
            self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
            self._fit_compact_text("genre_text",max_size=26,min_size=17,padding=16)
        except Exception as exc:optional_failure("ui.detail_visual_font_refresh",exc)

        imdb_raw = self["imdb_text"].getText() or self.item.get("rating_imdb") or self.item.get("imdb_rating") or ""
        imdb_value = self._extract_numeric_rating(imdb_raw)
        if not imdb_value:
            imdb_value = "N/A" if str(imdb_raw).strip().upper().find("N/A") >= 0 else "--"
        try:self["poster_imdb_value"].setText(str(imdb_value)[:4])
        except Exception as exc:optional_failure("ui",exc)
        self._set_widget_pixmap("poster_imdb_logo", "us65_logo_imdb_94x36.png")

        match_text = _("TMDb • portal metadata")
        if isinstance(getattr(self, "_tmdb_data", None), dict) and self._tmdb_data.get("matched"):
            if str(self._tmdb_data.get("identity_source") or "")=="portal_payload":
                match_text=_("Portal metadata • HDD")
            else:
                confidence=int(round(float(self._tmdb_data.get("confidence") or 0)*100))
                match_text = (_("TMDB • %d%% match") % confidence) if confidence else _("TMDb linked")
        elif self["rating_source"].getText():
            match_text = self["rating_source"].getText()
        try:self["poster_match_text"].setText(match_text[:34])
        except Exception as exc:optional_failure("ui",exc)

    def _persist_portal_hot_snapshot(self):
        # Portal artwork is intentionally disabled for Movies/Series.
        # Keep the method as a compatibility no-op because old callbacks may
        # still call it while upgrading from previous builds.
        return

    def _drain_jobs(self):
        AsyncScreenMixin._drain_jobs(self)
        if self._screen_closed:return
        self._drain_image_jobs()
        while True:
            try:
                payload=self._adaptive_jobs.get_nowait()
                token,source,chrome,theme_path,accent_path,fallback=payload[:6]
                edge=payload[6] if len(payload)>6 else None
            except queue.Empty:break
            if token!=self._adaptive_token or self._screen_closed:continue
            self._adaptive_pending_source=""
            try:
                if not self._adaptive_palette_locked and source and os.path.isfile(source):
                    self._adaptive_source_local=source
                    if theme_path and self["dynamic_bg"].instance is not None:
                        self["dynamic_bg"].instance.setPixmapFromFile(theme_path);self["dynamic_bg"].show()
                    # Legacy large poster accent frame is retired.  Preserve the
                    # path in state for compatibility, but never allocate/show it.
                    try:self["accent_frame"].hide()
                    except Exception:pass
                    if chrome:self._apply_detail_chrome(chrome)
                    if edge and os.path.isfile(edge) and self["cinematic_edge"].instance is not None:
                        self["cinematic_edge"].instance.setPixmapFromFile(edge);self["cinematic_edge"].show()
                    try:
                        self._details_visual_state.update({
                            "theme":theme_path if theme_path and os.path.isfile(theme_path) else "",
                            "accent":accent_path if accent_path and os.path.isfile(accent_path) else "",
                            "chrome":dict(chrome) if isinstance(chrome,dict) else {},
                            "edge":edge if edge and os.path.isfile(edge) else "",
                            "poster":str(getattr(self,"_image_displayed_path","") or source or ""),
                        })
                        self._persist_visual_bundle_async(self._details_visual_state,(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None)))
                        self._persistent_adaptive_locked=True
                        self._schedule_search_visual_peer_sync(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None))
                    except Exception as exc:optional_failure("ui.silent_guard",exc)
                    self._adaptive_palette_locked=True
                # R197: never use the poster/fallback as a temporary Details
                # backdrop.  That provisional rank-1 image was the first of the
                # visible two/three-backdrop swaps reported on receiver.  Hold the
                # screen background until the one approved landscape presentation
                # is ready instead.
            except Exception as exc:optional_failure("ui.poster_visual_apply",exc)
        while True:
            try:payload=self._backdrop_jobs.get_nowait()
            except queue.Empty:break
            try:
                token,path,rank=payload[:3]
                canonical_source=payload[3] if len(payload)>3 else ""
            except Exception:
                continue
            if token!=self._backdrop_token:continue
            try:
                rank=max(0,int(rank or 0))
                authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
                if getattr(self,"_details_backdrop_authority_locked",False) and authority and canonical_source and os.path.abspath(str(canonical_source))!=os.path.abspath(authority):
                    continue
                if path and os.path.isfile(path) and rank>=getattr(self,"_backdrop_rank",0) and self["cinematic_bg"].instance is not None:
                    self["cinematic_bg"].instance.setPixmapFromFile(path);self["cinematic_bg"].show()
                    self._backdrop_rank=rank;self._backdrop_displayed_path=path;self._backdrop_present_pending="";self._details_visual_state["backdrop_present"]=path
                    if canonical_source and os.path.isfile(str(canonical_source)):
                        self._details_visual_state["backdrop"]=str(canonical_source)
                        if not getattr(self,"_details_backdrop_authority_locked",False):
                            self._details_backdrop_authority_source=str(canonical_source);self._details_backdrop_authority_locked=True
                    try:
                        self._persist_visual_bundle_async(self._details_visual_state,(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None)))
                        self._persistent_backdrop_locked=True
                        self._persistent_backdrop_source_locked=bool(self._details_visual_state.get("backdrop") and os.path.isfile(str(self._details_visual_state.get("backdrop") or "")))
                        self._schedule_search_visual_peer_sync(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None))
                    except Exception as exc:optional_failure("ui.silent_guard",exc)
                    except Exception as exc:optional_failure("ui.silent_guard",exc)
                    self._portal_backdrop_loaded=bool(rank>=2)
                    if rank>=4 and canonical_source and os.path.isfile(str(canonical_source)):
                        self._portal_backdrop_source_local=str(canonical_source)
                    if rank>=2:self._persist_portal_hot_snapshot()
                    if not getattr(self,"_runtime_artwork_logged",False) and rank>=2:
                        self._runtime_artwork_logged=True
                        _runtime_endurance_log("details_artwork_ready",media_type=self.media_type,rank=rank)
            except Exception as exc:optional_failure("ui.backdrop_rank_apply",exc)
        while True:
            try: token,path,lang=self._title_logo_jobs.get_nowait()
            except queue.Empty: break
            if token != self._title_logo_token or self._screen_closed: continue
            try:
                if path and os.path.isfile(str(path)) and self["title_logo"].instance is not None:
                    self["title_logo"].instance.setPixmapFromFile(str(path));self["title_logo"].show()
                else:
                    self["title_logo"].hide()
            except Exception as exc:optional_failure("ui.details_title_logo_apply",exc)
            finally:
                try:
                    if path and os.path.isfile(str(path)):os.remove(str(path))
                except Exception:pass
        while True:
            try: token,data=self._tmdb_jobs.get_nowait()
            except queue.Empty: break
            if token != self._tmdb_token or self._screen_closed: continue
            self._apply_tmdb_metadata(data)

    def _picture_ready(self, *args):
        # Poster decoding stays independent from the widescreen backdrop.  The
        # displayed poster is the one authoritative palette source for the
        # lifetime of this screen.  This removes the old portal/TMDB race where
        # a correct colour was repainted a moment later by another async job.
        ImageLoaderMixin._picture_ready(self, *args)
        try:
            path=str(getattr(self,"_image_displayed_path","") or "")
            base=os.path.basename(path)
            if path and os.path.isfile(path) and base not in ("grid_placeholder_movie_921.png","grid_placeholder_series_921.png"):
                palette_source=getattr(self,"_adaptive_authoritative_source","") or getattr(self,"_poster_authoritative_source","") or path
                try:
                    if base.startswith("detailcover_"):
                        self._details_visual_state["detail_poster"]=path
                        if palette_source and os.path.isfile(str(palette_source)):
                            self._details_visual_state["poster"]=str(palette_source)
                    else:
                        self._details_visual_state["poster"]=path
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                self._schedule_poster_visuals(palette_source)
                self._persist_portal_hot_snapshot()
        except Exception as exc:optional_failure("ui.poster_visual_schedule",exc)

    def _schedule_poster_visuals(self, path):
        """Apply/build adaptive Details chrome without GUI-thread HDD reads.

        The poster itself has already painted.  Reusing/building adaptive chrome
        is strictly second-paint work and must never delay Backdrop/Title Logo
        callbacks queued behind it on Enigma2's main loop.
        """
        path=str(path or "")
        try:
            bundle=dict(getattr(self,"_persistent_visual_bundle",{}) or {})
            chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
            complete=bool(bundle.get("theme") and os.path.isfile(str(bundle.get("theme") or "")) and chrome.get("panel_detail") and chrome.get("overview_detail"))
            if complete:
                self._persistent_adaptive_locked=True;self._adaptive_palette_locked=True
                self._adaptive_source_local=str(bundle.get("poster") or path or "")
                self._details_visual_state.update({
                    "poster":str(bundle.get("poster") or path or ""),"backdrop":str(bundle.get("backdrop") or ""),
                    "backdrop_present":str(bundle.get("backdrop_present") or ""),"detail_poster":str(bundle.get("detail_poster") or ""),
                    "theme":str(bundle.get("theme") or ""),"accent":str(bundle.get("accent") or ""),"edge":str(bundle.get("edge") or ""),"chrome":dict(chrome),
                })
                # No HDD reload here: the constructor already validated this bundle.
                self._restore_details_visual_state(reload_disk=False);return
        except Exception as exc:optional_failure("ui.details_ram_adaptive_reuse",exc)
        if not path or not os.path.isfile(path) or self._screen_closed:return
        if getattr(self,"_adaptive_pending_source","")==path:return
        self._adaptive_pending_source=path;self._adaptive_token+=1;token=self._adaptive_token
        cancel_event=self._details_cancel_event;adaptive_jobs=self._adaptive_jobs;screen_ref=weakref.ref(self)
        profile_copy=dict(self.profile or {});item_copy=dict(getattr(self,"_portal_item",self.item) or {})
        snap_copy=dict((getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None) or {}))
        def worker():
            try:
                if cancel_event.is_set():return
                # Disk lookup happens here, never on the GUI thread.  Another view
                # may have completed the exact adaptive package milliseconds ago.
                disk=_load_visual_bundle(profile_copy,self.media_type,item_copy,snapshot=snap_copy) or {}
                dchrome=disk.get("chrome") if isinstance(disk.get("chrome"),dict) else {}
                if disk.get("theme") and os.path.isfile(str(disk.get("theme") or "")) and dchrome.get("panel_detail") and dchrome.get("overview_detail"):
                    adaptive_jobs.put((token,str(disk.get("poster") or path),dchrome,str(disk.get("theme") or ""),str(disk.get("accent") or ""),None,str(disk.get("edge") or "")))
                    return
                stamp=str(os.path.getmtime(path));digest=hashlib.sha1((path+"|"+stamp+"|us64-details-local").encode("utf-8","ignore")).hexdigest()[:20]
                chrome=_build_dynamic_details_chrome(path,canonical_dynamic_details_key(path) or ("us64_"+digest)) or {}
                theme=_build_dynamic_details_gradient(path,os.path.join(THUMB_CACHE_DIR,"us64_%s_bg.jpg"%digest),(1920,1080)) or ""
                if cancel_event.is_set():return
                adaptive_jobs.put((token,path,chrome,theme,"","",None))
            except Exception as exc:
                screen=screen_ref()
                if screen is not None:screen._adaptive_pending_source=""
                optional_failure("ui.details_local_adaptive_build",exc)
        try:
            future=_DETAIL_PREFETCH_EXECUTOR.submit(worker,priority=2,_task_key="details-adaptive:%s"%str(getattr(self,"_details_content_key","") or id(self)))
            self._track_details_future(future)
        except Exception as exc:self._adaptive_pending_source="";optional_failure("ui.details_local_adaptive_submit",exc)

    def _persist_visual_bundle_async(self, payload=None, snapshot=None):
        """Persist presentation derivatives off the Enigma2 GUI thread."""
        try:
            state=dict(payload if isinstance(payload,dict) else (getattr(self,"_details_visual_state",{}) or {}))
            if not state:return
            profile_copy=dict(self.profile or {})
            item_copy=dict(getattr(self,"_portal_item",self.item) or {})
            snap_copy=dict(snapshot if isinstance(snapshot,dict) else ((getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None) or {})))
            key="details-persist:%s"%str(getattr(self,"_details_content_key","") or id(self))
            def worker():
                try:_save_visual_bundle(profile_copy,self.media_type,item_copy,state,snapshot=snap_copy)
                except Exception as exc:optional_failure("ui.details_visual_persist_bg",exc)
            future=_DETAIL_PREFETCH_EXECUTOR.submit(worker,priority=3,_task_key=key,_replace_task_key=True)
            self._track_details_future(future)
        except Exception as exc:optional_failure("ui.details_visual_persist_submit",exc)

    def _schedule_search_visual_peer_sync(self, data=None):
        """Search peer publishing is HDD maintenance, never first-paint GUI work."""
        if not list(getattr(self,"_search_visual_peers",[]) or []):return
        try:
            row=dict(data or {}) if isinstance(data,dict) else {}
            screen_ref=weakref.ref(self)
            key="details-peer-sync:%s"%str(getattr(self,"_details_content_key","") or id(self))
            def worker():
                screen=screen_ref()
                if screen is None or screen._screen_closed:return
                screen._sync_search_visual_peers(row)
            future=_DETAIL_PREFETCH_EXECUTOR.submit(worker,priority=3,_task_key=key,_replace_task_key=True)
            self._track_details_future(future)
        except Exception as exc:optional_failure("ui.details_peer_sync_submit",exc)

    def _load_cinematic_backdrop(self, portal_fallback=False):
        """Reuse only the verified local backdrop handed off by Cinematic/Grid.

        This does not inspect provider URLs and never performs network I/O. It
        simply lets Details display the exact HDD backdrop already accepted by
        the parent screen, eliminating the blank-background handoff regression.
        """
        authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
        source=authority if getattr(self,"_details_backdrop_authority_locked",False) and authority else str(getattr(self,"_portal_backdrop_source_local","") or "")
        present="" if getattr(self,"_details_backdrop_authority_locked",False) else str(getattr(self,"_portal_backdrop_present_local","") or "")
        try:
            if present and _valid_cache_file(present) and self["cinematic_bg"].instance is not None:
                try:self["cinematic_bg"].instance.move(ePoint(300,0));self["cinematic_bg"].instance.resize(eSize(1620,670))
                except Exception as exc:optional_failure("ui.details_handoff_present_geometry",exc)
                self["cinematic_bg"].instance.setPixmapFromFile(present);self["cinematic_bg"].show()
                self._backdrop_rank=max(4,int(getattr(self,"_backdrop_rank",0) or 0));self._backdrop_displayed_path=present
                self._details_visual_state["backdrop_present"]=present
                if source and _details_backdrop_truth(source):self._details_visual_state["backdrop"]=source
                return
        except Exception as exc:optional_failure("ui.details_handoff_present",exc)
        if not _details_backdrop_truth(source):return
        try:
            target=self._backdrop_presentation_target(source)
            if target and _valid_cache_file(target):
                if self["cinematic_bg"].instance is not None:
                    self["cinematic_bg"].instance.setPixmapFromFile(target);self["cinematic_bg"].show()
                    self._backdrop_rank=max(4,int(getattr(self,"_backdrop_rank",0) or 0));self._backdrop_displayed_path=target
                    self._details_visual_state["backdrop"]=source;self._details_visual_state["backdrop_present"]=target
            else:
                self._schedule_backdrop_presentation(source,4)
        except Exception as exc:optional_failure("ui.details_handoff_backdrop",exc)

    def _apply_provider_bootstrap_visuals(self, data):
        """Apply no-key provider bootstrap art without pretending it is TMDb metadata."""
        if self._screen_closed or not isinstance(data,dict):return
        try:
            poster=str(data.get("poster_local") or "")
            if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                current=str(getattr(self,"_poster_authoritative_source","") or "")
                if not (current and os.path.isfile(current)):
                    self._poster_authoritative_source=poster
                    self._adaptive_authoritative_source=poster
                    self._persistent_poster_locked=True
                    self._details_visual_state["poster"]=poster
                    self._image_token += 1
                    self._decode_picture(poster)
            backdrop=str(data.get("backdrop_local") or "")
            if _details_backdrop_truth(backdrop) and not getattr(self,"_details_backdrop_authority_locked",False):
                # Keep provider landscape only as a recovery candidate. It is not
                # a visible Details authority and therefore cannot create the
                # first wrong backdrop before the clean TMDb source arrives.
                self._portal_backdrop_source_local=backdrop
        except Exception as exc:optional_failure("ui.details_provider_bootstrap_apply",exc)

    def _apply_first_paint_component(self, token, kind, payload):
        """Apply one verified artwork component without waiting for siblings."""
        if self._screen_closed or int(token)!=int(getattr(self,"_tmdb_token",0) or 0):return
        if not isinstance(payload,dict):return
        try:
            if kind=="identity":
                title=str(getattr(self,"_server_display_name","") or self["name"].getText() or payload.get("title") or "")
                if payload.get("tmdb_id"):
                    self._schedule_details_title_logo(payload,title)
                return
            if kind=="poster":
                poster=str(payload.get("poster_local") or "")
                payload_verified=bool(
                    payload.get("tmdb_id") and
                    (payload.get("identity_verified") or payload.get("identity_pointer_verified")) and
                    identity_cache_compatible(getattr(self,"_portal_item",self.item),payload)
                )
                if poster and os.path.isfile(poster) and (
                        payload_verified or
                        (not getattr(self,"_persistent_poster_locked",False) and
                         not getattr(self,"_poster_authoritative_source",""))):
                    # Provider/Grid art is first-paint only. A verified canonical
                    # poster must be allowed to supersede it, including a stale
                    # rounded derivative that was locked by an older identity.
                    if payload_verified:
                        self._persistent_poster_locked=False
                        self._persistent_detail_poster_locked=False
                        self._details_visual_state.pop("detail_poster",None)
                        self._details_visual_state.pop("detail_poster_source_fp",None)
                        bundle=dict(getattr(self,"_persistent_visual_bundle",{}) or {})
                        bundle.pop("detail_poster",None)
                        bundle.pop("detail_poster_source_fp",None)
                        bundle.pop("detail_poster_final",None)
                        self._persistent_visual_bundle=bundle
                    self._poster_authoritative_source=poster
                    if payload_verified or not getattr(self,"_adaptive_authoritative_source",""):
                        self._adaptive_authoritative_source=poster
                    self._details_visual_state["poster"]=poster
                    self._image_token += 1
                    self._decode_picture(poster)
                return
            if kind=="backdrop":
                # R197 single Details backdrop authority. Never paint the raw
                # component and then replace it with a crop/clean candidate. If
                # Cinematic handed us its already-clean winner, only that source
                # may be presented. Otherwise wait until the resolver marks one
                # clean source and present it once.
                authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
                if getattr(self,"_details_backdrop_authority_locked",False) and authority:
                    self._schedule_backdrop_presentation(authority,5)
                    return
                backdrop=str(payload.get("backdrop_local") or "")
                sealed=int(payload.get("clean_backdrop_selector_version") or 0)>=3
                if sealed and _details_backdrop_truth(backdrop):
                    self._details_backdrop_authority_source=backdrop;self._details_backdrop_authority_locked=True
                    self._portal_backdrop_source_local=backdrop;self._details_visual_state["backdrop"]=backdrop
                    self._schedule_backdrop_presentation(backdrop,5)
                return
        except Exception as exc:optional_failure("ui.details_first_paint_component",exc)

    def _load_tmdb_metadata(self):
        cfg=load_settings()
        credential=str(cfg.get("tmdb_credential") or "").strip()
        self._tmdb_token += 1
        token=self._tmdb_token
        media_type=self.media_type
        item=dict(getattr(self,"_portal_item",self.item) or {}) if isinstance(getattr(self,"_portal_item",self.item),dict) else {}
        language=cfg.get("tmdb_language","ar-EG")
        timeout=min(6,max(3,int(cfg.get("timeout",10) or 10)))

        # Local snapshot is useful even when TMDB is disabled or no credential
        # exists. A portal_payload snapshot is rendered immediately; if it also
        # carries a structured id, direct-ID enrichment may continue in the
        # background without delaying the visible screen.
        hot={}
        try:
            portal_item=getattr(self,"_portal_item",self.item)
            hot=load_detail_snapshot(self.profile,self.media_type,portal_item)
            art_hot=load_artwork_v2_manifest(self.profile,self.media_type,portal_item)
            art_hot_ok=bool(isinstance(art_hot,dict) and art_hot.get("matched") and (
                art_hot.get("identity_pointer_verified") or identity_cache_compatible(portal_item,art_hot)))
            if art_hot_ok:
                # Artwork owns bytes/provenance; the Details snapshot owns text.
                # Merge in that direction so an old artwork manifest can never
                # restore stale-language overview/genres/cast over raw TMDb info.
                merged=dict(art_hot)
                if isinstance(hot,dict) and hot.get("_details_authority_ready"):
                    for _meta_key in ('overview','overview_language','description','plot','descr','genres','genre','cast','actors','actor','crew','directors','director','writers','writer','runtime','duration','time','length','number_of_seasons','seasons_count','season_count','number_of_episodes','episodes_count','episode_count','release_date','first_air_date','year','release_year','releaseDate','countries','country','country_code','production_country','production_countries','origin_country','rating','vote_count','original_language','certification','age_rating'):
                        merged.pop(_meta_key,None)
                merged.update(hot or {}); hot=merged
                if art_hot.get("tmdb_id") and not _detail_metadata_complete(hot):
                    direct=load_detail_snapshot_by_tmdb(art_hot.get("media_type") or self.media_type,art_hot.get("tmdb_id"))
                    if isinstance(direct,dict) and direct:
                        enriched=dict(hot)
                        if direct.get("_details_authority_ready"):
                            for _meta_key in ('overview','overview_language','description','plot','descr','genres','genre','cast','actors','actor','crew','directors','director','writers','writer','runtime','duration','time','length','number_of_seasons','seasons_count','season_count','number_of_episodes','episodes_count','episode_count','release_date','first_air_date','year','release_year','releaseDate','countries','country','country_code','production_country','production_countries','origin_country','rating','vote_count','original_language','certification','age_rating'):
                                enriched.pop(_meta_key,None)
                        enriched.update(direct);hot=enriched
            if (isinstance(hot,dict) and hot.get("tmdb_id") and
                    not hot.get("identity_pointer_verified") and
                    not identity_cache_compatible(portal_item,hot)):
                hot={}
            if isinstance(hot,dict) and hot.get("matched") and hot.get("identity_verified"):
                source=str(hot.get("identity_source") or "")
                if source!="portal_payload" and str(hot.get("source") or "").upper()!="PORTAL":
                    self._tmdb_jobs.put((token,hot))
                poster_ok=bool(_verified_external_art(hot,"poster"))
                backdrop_ok=bool(_details_backdrop_truth(_verified_external_art(hot,"backdrop")))
                if hot.get("tmdb_id"):
                    item["_locked_tmdb_id"]=hot.get("tmdb_id");item["_locked_tmdb_type"]=hot.get("media_type")
                # External/direct-ID verified snapshots are complete hot hits.
                # Portal payload snapshots remain eligible for direct-ID enrich.
                try:
                    from .details_authority import metadata_language_ready
                    _language_ready=metadata_language_ready(hot,cfg)
                except Exception:
                    _language_ready=bool(hot.get("_details_authority_ready") and hot.get("tmdb_id"))
                if poster_ok and backdrop_ok and source!="portal_payload" and _detail_metadata_complete(hot) and _language_ready and (bool(hot.get("title_logo_checked")) or bool(hot.get("_details_authority_ready"))):
                    # Stable Focus already completed the same Details Authority
                    # for the language currently selected by the user.
                    return
                # A portal snapshot is first paint/fallback only.  When it has no
                # structured external id we still continue to a verified title search
                # so good TMDB artwork can replace low-quality portal art.
        except Exception as exc:optional_failure("ui.hdd_hot_read",exc)

        # A visual HDD lock must NOT block missing metadata.  Older cache rows may
        # already own perfect poster/backdrop files while lacking TMDB countries.
        # Continue the metadata resolver in that case; ArtworkV2 reuses the locked
        # manifest and therefore does not replace the existing visual files.
        if not credential:
            # R269: provider/server artwork fallback is retired. With no usable
            # TMDb credential, retain the already-local first paint only.
            return
        if not cfg.get("tmdb_enabled",True):
            return

        cancel_event=self._details_cancel_event;tmdb_jobs=self._tmdb_jobs;profile=dict(self.profile or {});screen_ref=weakref.ref(self)
        def component_callback(kind, partial):
            owner=screen_ref()
            if owner is None or cancel_event.is_set():return
            try:reactor.callFromThread(owner._apply_first_paint_component,token,kind,partial)
            except Exception:pass
        def clean_worker(base_data):
            if cancel_event.is_set() or not isinstance(base_data,dict) or not base_data.get("tmdb_id"):return
            try:
                from .backdrop_clean_runtime import upgrade as _upgrade_clean_backdrop
                clean=_upgrade_clean_backdrop(profile,media_type,item,settings=cfg,cancel_event=cancel_event) or {}
                fresh=clean.get("data") if isinstance(clean,dict) else {}
                if isinstance(fresh,dict) and fresh and not cancel_event.is_set():
                    # Clean-backdrop is a visual repair only.  Do not let its
                    # manifest copy overwrite the selected-language raw metadata.
                    visual_keys=(
                        "tmdb_id","media_type","poster_local","backdrop_local",
                        "poster_path","backdrop_path","poster_url","backdrop_url","_backdrop_url","tmdb_backdrop_url",
                        "adaptive_primary","adaptive_secondary","adaptive_dark","adaptive_accent","adaptive_fingerprint",
                        "clean_backdrop_choice","clean_backdrop_iso","clean_backdrop_selector_version",
                        "backdrop_source","backdrop_state","identity_verified","identity_pointer_verified",
                    )
                    merged=dict(base_data)
                    for key in visual_keys:
                        value=fresh.get(key)
                        if value not in (None,"",[],{}):merged[key]=value
                    tmdb_jobs.put((token,merged))
            except Exception as _clean_exc:
                optional_failure("ui.details_clean_backdrop_upgrade",_clean_exc)
        def worker():
            if cancel_event.is_set():return
            try:
                from .details_authority import resolve_canonical
                data=resolve_canonical(profile,media_type,item,cancel_event=cancel_event,settings=cfg,provider_client=self.client,provider_downloader=_download_portal_artwork,component_callback=component_callback) or {}
                if cancel_event.is_set():return
                # First complete canonical result reaches UI immediately. Clean
                # backdrop and any heavier presentation enrichment come later.
                tmdb_jobs.put((token,data if isinstance(data,dict) else {}))
                if isinstance(data,dict) and data.get("tmdb_id") and not cancel_event.is_set():
                    try:
                        clean_future=_DETAIL_PREFETCH_EXECUTOR.submit(clean_worker,dict(data),priority=3,_task_key="details-clean:%s"%str(getattr(self,"_details_content_key","") or id(self)))
                        owner=screen_ref()
                        if owner is not None:owner._track_details_future(clean_future)
                    except Exception as exc:optional_failure("ui.details_clean_submit",exc)
            except Exception as exc:
                if not cancel_event.is_set():tmdb_jobs.put((token,{"matched":False,"error":str(exc)}))
        try:
            future=_DETAIL_PREFETCH_EXECUTOR.submit(worker,priority=0,_task_key="details:%s"%str(getattr(self,"_details_content_key","") or id(self)))
            self._track_details_future(future)
        except Exception as exc:optional_failure("ui",exc)

    def _backdrop_presentation_target(self, source):
        source=str(source or "")
        if not source or not os.path.isfile(source):return ""
        try:stamp=str(os.path.getmtime(source))
        except Exception:stamp="0"
        digest=hashlib.sha1((source+"|"+stamp+"|us221-present-lower").encode("utf-8","ignore")).hexdigest()[:24]
        return os.path.join(THUMB_CACHE_DIR,"us221_detail_%s_1620x620_lower.png"%digest)

    def _schedule_backdrop_presentation(self, source, rank=5):
        source=str(source or "")
        if not source or not os.path.isfile(source) or self._screen_closed:return ""
        authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
        if getattr(self,"_details_backdrop_authority_locked",False) and authority:
            if os.path.abspath(source)!=os.path.abspath(authority):return ""
            source=authority
        bundle=getattr(self,"_persistent_visual_bundle",{}) or {}
        target=self._backdrop_presentation_target(source)
        final_present=str(bundle.get("backdrop_present") or "")
        if final_present and target and os.path.abspath(final_present) == os.path.abspath(target) and os.path.isfile(final_present):
            self._persistent_backdrop_locked=True
            return final_present
        if target and _valid_cache_file(target):
            try:
                _save_visual_bundle(self.profile,self.media_type,getattr(self,"_portal_item",self.item),{"backdrop":source,"backdrop_present":target},snapshot=(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None)))
                self._persistent_visual_bundle=_load_visual_bundle(self.profile,self.media_type,getattr(self,"_portal_item",self.item),(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None))) or bundle
                self._persistent_backdrop_locked=True
            except Exception as exc:optional_failure("ui.backdrop_present_lock",exc)
            return target
        if getattr(self,"_backdrop_present_pending","")==source:return ""
        self._backdrop_present_pending=source
        self._backdrop_token += 1; token=self._backdrop_token
        cancel_event=self._details_cancel_event;backdrop_jobs=self._backdrop_jobs;screen_ref=weakref.ref(self)
        def stale():
            screen=screen_ref()
            return bool(cancel_event.is_set() or screen is None or token!=getattr(screen,"_backdrop_token",None))
        def worker():
            try:
                if stale():return
                prepared=_build_integrated_backdrop(source,target,(1620,620))
                if prepared and os.path.isfile(prepared) and not stale():
                    backdrop_jobs.put((token,prepared,int(rank or 5),source))
                else:
                    screen=screen_ref()
                    if screen is not None and token==getattr(screen,"_backdrop_token",None):screen._backdrop_present_pending=""
            except Exception as exc:
                screen=screen_ref()
                if screen is not None and token==getattr(screen,"_backdrop_token",None):screen._backdrop_present_pending=""
                optional_failure("ui.backdrop_present_worker",exc)
        try:
            future=_BACKDROP_PRESENT_EXECUTOR.submit(worker)
            self._track_details_future(future)
        except Exception as exc:optional_failure("ui.backdrop_present_submit",exc)
        return ""

    def _sync_search_visual_peers(self, data=None):
        """Publish one verified Details package to equivalent Search copies.

        Playback ownership is never shared: each peer keeps its own profile/id/
        command. Only verified TMDb metadata and paths to immutable HDD artwork/
        derived chrome are aliased. This is deliberately Search-scoped.
        """
        peers=list(getattr(self,"_search_visual_peers",[]) or [])
        if not peers:return
        source=data if isinstance(data,dict) else {}
        local=getattr(self,"_local_detail_cache",{}) or {}
        if not source.get("tmdb_id") and isinstance(local,dict):source=local
        if not isinstance(source,dict) or not source.get("tmdb_id"):return
        trusted=bool(source.get("identity_verified") or source.get("identity_pointer_verified") or source.get("_details_authority_ready"))
        if not trusted:return
        tmdb_id=source.get("tmdb_id")
        snap=dict(local if isinstance(local,dict) and str(local.get("tmdb_id") or "")==str(tmdb_id) else source)
        if not snap.get("tmdb_id"):return
        # Merge the currently materialized presentation with the persisted one;
        # component saves call this method again, so peers become progressively
        # complete without another network resolve.
        payload={}
        try:
            bundle=getattr(self,"_persistent_visual_bundle",{}) or {}
            if isinstance(bundle,dict):payload.update(bundle)
        except Exception:pass
        try:
            state=getattr(self,"_details_visual_state",{}) or {}
            if isinstance(state,dict):payload.update(state)
        except Exception:pass
        for key in ("poster_local","backdrop_local"):
            value=str(snap.get(key) or "")
            if value and os.path.isfile(value):
                payload["poster" if key=="poster_local" else "backdrop"]=value
        chrome=payload.get("chrome") if isinstance(payload.get("chrome"),dict) else {}
        fingerprint="%s|%s|%s|%s|%s|%s|%s|%s"%(str(tmdb_id),str(payload.get("poster") or ""),str(payload.get("backdrop") or ""),str(payload.get("detail_poster") or ""),str(payload.get("backdrop_present") or ""),str(payload.get("theme") or ""),str(chrome.get("panel_detail") or ""),str(chrome.get("overview_detail") or ""))
        if fingerprint and fingerprint==str(getattr(self,"_search_peer_sync_fp","") or ""):
            return
        for peer in peers:
            try:
                profile=peer.get("profile") or {};typ=peer.get("media_type") or self.media_type;item=peer.get("item") or {}
                if not isinstance(item,dict):continue
                trusted_peer=bool(peer.get("search_match_verified"))
                if not trusted_peer and not identity_cache_compatible(item,snap):continue
                peer_item=dict(item)
                if trusted_peer:
                    peer_item["_ultra_search_verified_tmdb_id"]=tmdb_id
                    peer_item["_locked_tmdb_id"]=tmdb_id
                    peer_item["_locked_tmdb_type"]=snap.get("media_type") or ("tv" if typ=="series" else "movie")
                save_detail_snapshot(profile,typ,peer_item,snap)
                if payload:_save_visual_bundle(profile,typ,peer_item,payload,snapshot=snap)
            except Exception as exc:
                optional_failure("ui.search_peer_publish",exc)
        self._search_peer_sync_fp=fingerprint

    def _commit_canonical_visual_identity(self, data):
        """Make one verified TMDb identity own poster and backdrop together.

        Grid/provider artwork is deliberately allowed for instant first paint.
        Once Details has a verified, provider-compatible TMDb identity, however,
        poster/backdrop derivatives and locks from any provisional identity must
        stop owning the screen. This is the Olympus-style correction path.
        """
        if not isinstance(data,dict) or not data.get("tmdb_id"):
            return
        trusted=bool(
            data.get("identity_verified") or
            data.get("identity_pointer_verified") or
            data.get("_details_authority_ready")
        )
        if not trusted:
            return
        if not identity_cache_compatible(getattr(self,"_portal_item",self.item),data):
            return

        canonical_id=str(data.get("tmdb_id") or "")
        canonical_type=str(data.get("media_type") or ("tv" if self.media_type=="series" else "movie"))
        previous_id=str(getattr(self,"_canonical_visual_tmdb_id","") or "")
        previous_type=str(getattr(self,"_canonical_visual_media_type","") or "")
        identity_changed=bool(
            (previous_id and previous_id!=canonical_id) or
            (previous_type and previous_type!=canonical_type)
        )
        self._canonical_visual_tmdb_id=canonical_id
        self._canonical_visual_media_type=canonical_type

        poster=str(data.get("poster_local") or "")
        if poster and os.path.isfile(poster):
            old=str(getattr(self,"_poster_authoritative_source","") or "")
            if old!=poster or identity_changed:
                # A prepared Details poster is bound to its source bytes. Never
                # let an old derivative/lock survive a canonical correction.
                self._poster_prepare_token=int(getattr(self,"_poster_prepare_token",0) or 0)+1
                self._persistent_poster_locked=False
                self._persistent_detail_poster_locked=False
                bundle=dict(getattr(self,"_persistent_visual_bundle",{}) or {})
                fp=_visual_source_fingerprint(poster)
                if str(bundle.get("detail_poster_source_fp") or "")!=fp:
                    bundle.pop("detail_poster",None)
                    bundle.pop("detail_poster_source_fp",None)
                    bundle.pop("detail_poster_final",None)
                    self._details_visual_state.pop("detail_poster",None)
                    self._details_visual_state.pop("detail_poster_source_fp",None)
                bundle["poster"]=poster
                self._persistent_visual_bundle=bundle
                self._poster_authoritative_source=poster
                self._adaptive_authoritative_source=poster
                self._details_visual_state["poster"]=poster
                self._image_token += 1
                self._decode_picture(poster)

        backdrop=str(data.get("backdrop_local") or "")
        if _details_backdrop_truth(backdrop):
            old=str(getattr(self,"_details_backdrop_authority_source","") or "")
            if old!=backdrop or identity_changed:
                # Backdrop and poster now follow the same verified identity. Drop
                # only stale presentation ownership; keep last-good pixels visible
                # until the new canonical presentation is ready.
                fp=_visual_source_fingerprint(backdrop)
                bundle=dict(getattr(self,"_persistent_visual_bundle",{}) or {})
                if str(bundle.get("backdrop_present_source_fp") or "")!=fp:
                    bundle.pop("backdrop_present",None)
                    bundle.pop("backdrop_present_source_fp",None)
                    bundle.pop("backdrop_final",None)
                    self._details_visual_state.pop("backdrop_present",None)
                bundle["backdrop"]=backdrop
                self._persistent_visual_bundle=bundle
                self._details_backdrop_authority_source=backdrop
                self._details_backdrop_authority_locked=True
                self._portal_backdrop_source_local=backdrop
                self._persistent_backdrop_source_locked=False
                self._persistent_backdrop_locked=False
                self._details_visual_state["backdrop"]=backdrop
                self._schedule_backdrop_presentation(backdrop,5)

        # The snapshot has already been saved by the Details authority before this
        # method runs. Persist current derived ownership against that same identity.
        try:
            self._persist_visual_bundle_async(self._details_visual_state,data)
        except Exception as exc:
            optional_failure("ui.canonical_visual_bundle_persist",exc)

    def _apply_tmdb_metadata(self, data):
        if not isinstance(data,dict): return
        # Final presentation firewall: no search result may paint metadata/artwork
        # unless it independently agrees with the provider catalogue item.  This
        # also invalidates poisoned snapshots created by older builds.
        if (data.get("matched") and data.get("tmdb_id") and
                not data.get("identity_pointer_verified") and
                not identity_cache_compatible(getattr(self,"_portal_item",self.item),data)):
            data={"matched":False,"reason":"identity_guard_rejected","confidence":float(data.get("confidence") or 0)}
        previous_good=self._tmdb_data if isinstance(getattr(self,"_tmdb_data",None),dict) and self._tmdb_data.get("matched") and self._tmdb_data.get("identity_verified") else None
        identity_source=str(data.get("identity_source") or "")
        confidence=float(data.get("confidence") or 0)
        evidence=int(data.get("identity_evidence") or 0)
        trusted_identity = bool((data.get("identity_verified") or data.get("identity_pointer_verified")) and data.get("tmdb_id") and str(data.get("source") or "").upper()!="PORTAL")
        if data.get("matched") and trusted_identity:
            if getattr(self,"_is_xtream_item",False):
                try:
                    if not (_verified_external_art(data,"poster") and _details_backdrop_truth(_verified_external_art(data,"backdrop"))):self._schedule_xtream_auto_retry()
                except Exception:pass
            try:
                # Persist only trusted identities. An ignored fuzzy result must
                # never poison the next HDD-first open with its overlay.
                if data.get("tmdb_id") and data.get("identity_verified"):
                    overlay={
                        "year":data.get("year"), "country":((data.get("countries") or [""])[0] if isinstance(data.get("countries"),list) else ""),
                        "genre":" / ".join([str(x) for x in (data.get("genres") or [])[:3]]),
                        "description":data.get("overview"), "actors":", ".join([str(x) for x in (data.get("cast") or [])[:6]]),
                        "director":", ".join([str(x) for x in (data.get("directors") or [])[:3]]),
                        "writer":", ".join([str(x) for x in (data.get("writers") or [])[:4]]),
                        "number_of_episodes":data.get("number_of_episodes"), "number_of_seasons":data.get("number_of_seasons"),
                    }
                    snap=dict(data); snap["item_overlay"]={k:v for k,v in overlay.items() if v not in (None,"",[],{})}
                    # Verified external artwork is canonical and HDD-hot.
                    # Portal poster/backdrop fields are never copied into it.
                    portal_item=getattr(self,"_portal_item",self.item)
                    for obsolete in ("portal_poster_local","portal_backdrop_local"):
                        snap.pop(obsolete,None)
                    save_detail_snapshot(self.profile,self.media_type,portal_item,snap)
                    self._local_detail_cache=snap
                    self._schedule_search_visual_peer_sync(snap)
            except Exception as exc:optional_failure("ui.local_detail_cache_save",exc)
        if not data.get("matched"):
            if getattr(self,"_is_xtream_item",False):self._schedule_xtream_auto_retry()
            if isinstance(previous_good,dict):
                # A late timeout/miss must never erase a verified TMDB screen.
                return
            # Test62: HDD artwork that already owns this Details screen is also
            # immutable on a TMDB miss.  This includes explicit BLUE manual rescue
            # files and pre-existing canonical/bundle artwork.  A metadata miss is
            # not permission to replace a good poster with a placeholder or hide a
            # good backdrop that was visible a moment earlier.
            bundle=getattr(self,"_persistent_visual_bundle",{}) or {}
            authority=str(getattr(self,"_poster_authoritative_source","") or "")
            authority_ok=bool(authority and os.path.isfile(authority))
            handoff_backdrop=str(getattr(self,"_portal_backdrop_source_local","") or "")
            handoff_backdrop_ok=bool(handoff_backdrop and os.path.isfile(handoff_backdrop))
            locked_p=bool(authority_ok or (getattr(self,"_persistent_poster_locked",False) and str(bundle.get("poster") or "")))
            locked_b=bool(handoff_backdrop_ok or ((getattr(self,"_persistent_backdrop_source_locked",False) or getattr(self,"_persistent_backdrop_locked",False)) and str(bundle.get("backdrop") or bundle.get("backdrop_present") or getattr(self,"_backdrop_displayed_path","") or "")))
            if not locked_p:
                try:
                    ph="grid_placeholder_movie_921.png" if self.media_type=="vod" else "grid_placeholder_series_921.png"
                    self._decode_picture(asset(ph))
                except Exception as exc:optional_failure("ui.tmdb_miss_placeholder",exc)
            if not locked_b:
                try:self["cinematic_bg"].hide()
                except Exception as exc:optional_failure("ui.tmdb_miss_backdrop_hide",exc)
            self["status"].setText((_("TMDb match unavailable") if not (locked_p or locked_b) else (_("Backdrop ready • HDD")+" • "+_("TMDb match unavailable"))))
            return
        self._tmdb_data=data
        # Stage 1: verified canonical identity owns all visual lanes before any
        # later poster/backdrop presentation logic can preserve provisional art.
        self._commit_canonical_visual_identity(data)
        # Country/flag is harmless structural metadata and is often absent from
        # provider rows even when TMDb resolved it. Apply it before the stricter
        # artwork/title identity gate, but only when the screen still has no country.
        try:
            if not str(getattr(self,"_country_raw","") or "").strip():
                fallback_countries=(data.get("countries") or data.get("production_countries") or data.get("origin_country") or [])
                fallback_code=self._country_code(fallback_countries)
                if fallback_code:
                    self._country_raw=fallback_code
                    self["country_text"].setText(self._normalized_country(fallback_code))
                    self._refresh_detail_visuals()
        except Exception as exc:optional_failure("ui.details_country_fallback",exc)
        if not trusted_identity:
            # Never let a merely similar external title repaint correct portal
            # metadata/artwork. This specifically protects ambiguous names and
            # translated Asian catalogues.
            self["status"].setText(_("Portal metadata • external match ignored"))
            return
        # Keep the portal/server title as the visible title. TMDB localized,
        # English and original titles are identity/search aliases only.
        title=str(getattr(self,"_server_display_name","") or self["name"].getText() or data.get("title") or "")
        if title and str(self["name"].getText() or "")!=title:
            self["name"].setText(title)
            self._fit_single_line_font("name",title,42,14);self._schedule_title_fit()
        self._schedule_details_title_logo(data,title)
        genres=" / ".join([str(x) for x in (data.get("genres") or [])[:3]])
        if genres:self["subtitle"].setText(genres[:78])
        badges=self._detail_quality_value()
        self["quality_text"].setText((badges or "")[:10]); self._refresh_detail_visuals()
        real_year=str(data.get("year") or "").strip()
        if real_year:self["year_text"].setText(_clean_year_value(real_year))
        if self.media_type == "series":
            season_count=data.get("number_of_seasons") or 0
            if season_count:self["runtime_text"].setText(self._season_count_text(season_count));self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
        elif data.get("runtime"):
            self["runtime_text"].setText((_(("%s min"))%data.get("runtime"))[:14]);self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
        countries=data.get("countries") or []
        country_code=self._country_code(countries)
        if country_code:
            self._country_raw=country_code
            self["country_text"].setText(self._normalized_country(country_code))
            self._refresh_detail_visuals()
        self["genre_text"].setText(genres[:54])
        self["age_rating_text"].setText(_age_rating_display(data.get("certification") or data.get("age_rating")))
        try:
            self._fit_compact_text("quality_text",max_size=21,min_size=11,padding=12)
            self._fit_compact_text("year_text",max_size=22,min_size=12,padding=8)
            self._fit_compact_text("runtime_text",max_size=24,min_size=18,padding=4)
            self._fit_compact_text("genre_text",max_size=26,min_size=17,padding=16)
        except Exception as exc:optional_failure("ui.detail_tmdb_font_refresh",exc)
        try:
            rating=float(data.get("rating") or 0)
            if rating>0:
                self["rating_score"].setText("%.1f"%rating);self["rating_source"].setText(_("TMDB rating"))
        except Exception as exc:optional_failure("ui",exc)
        imdb_id=str(data.get("imdb_id") or "").strip()
        imdb_data=data.get("imdb") if isinstance(data.get("imdb"),dict) else {}
        portal_imdb=self.item.get("rating_imdb") or self.item.get("imdb_rating") or ""
        imdb_rating=imdb_data.get("rating") or portal_imdb
        if imdb_rating:
            try:imdb_label="★ %.1f"%float(imdb_rating)
            except Exception:imdb_label="★ %s"%str(imdb_rating)
        elif imdb_id:imdb_label="N/A"
        else:imdb_label="N/A"
        self["imdb_text"].setText(imdb_label[:24])
        self._refresh_detail_visuals()
        # R262 visible synopsis authority: never repaint Details from a stale
        # or wrong-language snapshot.  Once the selected TMDb information
        # language is ready, use the same Arabic-first/fallback rule shared by
        # Cinematic/BG1/BG2.  This removes AR/EN/AR/EN flicker and prevents a
        # late English callback from replacing an available Arabic synopsis.
        try:
            from .details_authority import metadata_language_ready, details_overview
            _cfg_now=load_settings()
            if metadata_language_ready(data,_cfg_now):
                _visible_overview,_overview_ready=details_overview(self.profile,self.media_type,getattr(self,"_portal_item",self.item),data)
                if _overview_ready and str(_visible_overview or "").strip():
                    self._set_description_source(str(_visible_overview).strip())
        except Exception as exc:optional_failure("ui.details_overview_language_lock",exc)
        cast=[str(x) for x in (data.get("cast") or [])[:6] if str(x).strip()]
        if not cast and isinstance(data.get("imdb"),dict):cast=[str(x) for x in (data["imdb"].get("cast") or [])[:6] if str(x).strip()]
        self["cast_text"].setText(", ".join(cast)[:220])
        try:self._sync_detail_text_direction()
        except Exception as exc:optional_failure("ui.detail_cast_center",exc)
        poster=data.get("poster_local")
        real_source=str(data.get("backdrop_local") or "")
        if trusted_identity and data.get("tmdb_id") and (not (poster and os.path.isfile(str(poster))) or not _details_backdrop_truth(real_source)):
            retry_key="%s:%s"%(str(data.get("media_type") or self.media_type),str(data.get("tmdb_id")))
            if retry_key not in self._artwork_gap_retry and not self._screen_closed:
                self._artwork_gap_retry.add(retry_key)
                profile=dict(self.profile or {});media_type=self.media_type;item=dict(self.item);cfg_now=load_settings()
                credential=str(cfg_now.get("tmdb_credential") or "").strip()
                language=cfg_now.get("tmdb_language","ar-EG")
                timeout=min(6,max(3,int(cfg_now.get("timeout",10) or 10)))
                item["_locked_tmdb_id"]=data.get("tmdb_id");item["_locked_tmdb_type"]=data.get("media_type") or self.media_type
                token=self._tmdb_token;cancel_event=self._details_cancel_event;tmdb_jobs=self._tmdb_jobs
                def art_gap_worker():
                    if cancel_event.is_set() or not credential:return
                    try:
                        resolver=ArtworkV2(credential,language,timeout)
                        repaired=resolver.resolve(profile,media_type,item,full=True,cancel_event=cancel_event)
                        # release last-mile focus repair: when metadata resolved but the
                        # landscape master is still physically absent, call the dedicated
                        # real-backdrop resolver immediately.  No BLUE press and no scan
                        # of the rest of the folder.
                        if not _details_backdrop_truth((repaired or {}).get("backdrop_local") if isinstance(repaired,dict) else ""):
                            bfix=resolver.resolve_backdrop_only(profile,media_type,item,cancel_event=cancel_event)
                            if isinstance(bfix,dict) and _details_backdrop_truth(bfix.get("backdrop_local")):
                                merged=dict(repaired or {});merged.update(bfix);merged["matched"]=True;merged["identity_verified"]=True
                                repaired=merged
                                try:
                                    from .artwork_v2 import save_manifest
                                    save_manifest(profile,media_type,item,repaired)
                                except Exception as exc:
                                    optional_failure("ui.artwork_gap_direct_commit_manifest",exc)
                        if isinstance(repaired,dict) and repaired.get("matched") and not cancel_event.is_set():
                            tmdb_jobs.put((token,repaired))
                    except Exception as exc:optional_failure("ui.artwork_gap_retry",exc)
                try:self._track_details_future(_DETAIL_PREFETCH_EXECUTOR.submit(art_gap_worker,priority=0))
                except Exception as exc:optional_failure("ui.artwork_gap_submit",exc)
        if (not getattr(self,"_persistent_poster_locked",False)) and poster and os.path.isfile(str(poster)) and identity_source!="portal_payload":
            # When Details was opened from the Grid, the exact local poster shown
            # there owns this screen for its lifetime. TMDB may enrich metadata/
            # backdrop, but never blank/swap the poster during entry.
            if not getattr(self,"_poster_authoritative_source",""):
                self._image_token += 1
                self._decode_picture(str(poster))
        # us198: a real HDD backdrop is never processed on the TMDB worker.
        # Reuse the persistent 1620x620 presentation cache immediately; only a
        # cache miss is sent to the single low-priority presentation worker.
        authority=str(getattr(self,"_poster_authoritative_source","") or "")
        authority_ok=bool(authority and os.path.isfile(authority))
        if (not getattr(self,"_persistent_poster_locked",False)) and not (poster and os.path.isfile(str(poster))) and not authority_ok:
            ph="grid_placeholder_movie_921.png" if self.media_type=="vod" else "grid_placeholder_series_921.png"
            try:self._decode_picture(asset(ph))
            except Exception as exc:optional_failure("ui.external_poster_gap",exc)
        # R197 Details backdrop is single-source and single-paint.  Never show a
        # raw landscape and then swap to the 1620x620 presentation, and never let
        # an unsealed/provider candidate beat the clean source handed in from
        # Cinematic.  If no handoff exists, wait for the clean selector (v3) and
        # then present that source exactly once.
        authority=str(getattr(self,"_details_backdrop_authority_source","") or "")
        locked=bool(getattr(self,"_details_backdrop_authority_locked",False) and authority and os.path.isfile(authority))
        sealed=int(data.get("clean_backdrop_selector_version") or 0)>=3
        chosen=authority if locked else (real_source if sealed and _details_backdrop_truth(real_source) else "")
        if chosen:
            if not locked:
                self._details_backdrop_authority_source=chosen;self._details_backdrop_authority_locked=True
                self._portal_backdrop_source_local=chosen;self._details_visual_state["backdrop"]=chosen
            rank=5
            prepared=self._backdrop_presentation_target(chosen)
            if prepared and _valid_cache_file(prepared):
                try:
                    if self["cinematic_bg"].instance is not None and prepared!=str(getattr(self,"_backdrop_displayed_path","") or ""):
                        self["cinematic_bg"].instance.setPixmapFromFile(prepared);self["cinematic_bg"].show()
                        self._backdrop_rank=rank;self._backdrop_displayed_path=prepared;self._details_visual_state["backdrop_present"]=prepared
                except Exception as exc:optional_failure("ui.r197_details_backdrop_cached_apply",exc)
            elif not getattr(self,"_persistent_backdrop_locked",False):
                self._schedule_backdrop_presentation(chosen,rank)
        # Adaptive colour is intentionally single-source.  The first palette
        # built from the displayed portal backdrop/poster owns the screen for
        # its lifetime.  Late TMDB callbacks may improve metadata/artwork, but
        # must never repaint the chrome with an unrelated cached palette.
        # This also prevents stale async results from visibly changing colour
        # a second after the user opens Movies/Series details.
        confidence=int(round(float(data.get("confidence") or 0)*100))
        if identity_source=="portal_payload":
            source=_("Portal metadata • HDD")
        else:
            source=_("TMDB • %d%% match")%confidence
            if imdb_id:source += _(" • IMDb linked")
        self["status"].setText(source)
        _runtime_endurance_log("details_metadata",media_type=self.media_type,source=identity_source or "none",matched=True)
        self._refresh_detail_visuals()
        self._sync_detail_text_direction();self._ensure_description_visible()
        # No-backdrop titles still release deferred provider enrichment once the
        # canonical TMDb pass is complete; this is maintenance, never first paint.
        self._start_deferred_provider_enrichment(retry_tmdb=False)

    def toggle_favorite(self):
        added = toggle_favorite(self.profile, self.media_type, self.item)
        self["green"].setText(_("Unfavorite") if added else _("Favorite"))
        self._fit_remote_hint_underlines()
        self["status"].setText(_("Added to favorites") if added else _("Removed from favorites"))

    def _details_menu_source(self):
        for value in (
            getattr(self,"_adaptive_source_local",""),
            getattr(self,"_backdrop_displayed_path",""),
            getattr(self,"_portal_backdrop_source_local",""),
            (self._details_visual_state.get("backdrop") if isinstance(getattr(self,"_details_visual_state",None),dict) else ""),
            (self.item.get("_backdrop_source_local") if isinstance(self.item,dict) else ""),
        ):
            try:
                value=str(value or "")
                if value and os.path.isfile(value):return value
            except Exception:pass
        return ""

    def open_download_menu(self):
        choices=[(_("Set as Home Hero"),"set_home_hero"),(_("Reload external artwork"),"reload")]
        if self.media_type=="vod":
            choices.insert(1,(_("Download Movie"),"movie"))
            choices.insert(2,(_("Downloads Manager"),"manager"))
        # R168 menu-light pass: same Details actions/anchor, now pure floating
        # text.  No adaptive/glass assets are generated or rebound on MENU open.
        # BACK still removes only the rows and leaves the Details page untouched.
        self._settings_inline_menu.show(choices,selection=0,right=1850,region_top=240,region_h=400,on_accept=self._download_menu_selected,min_card_w=220,max_card_w=460,padding=44,anchor_bottom=670,visual_style="text_only",row_h=58,max_visible=5)

    def _schedule_foreign_english_cast(self, data, visible_title, current_cast):
        """Arabic catalogue keeps Arabic cast; every other catalogue paints Latin/English names only."""
        if _details_is_arabic_work(data,visible_title):return
        tmdb_id=(data or {}).get("tmdb_id")
        if not tmdb_id:return
        current=[str(x or "").strip() for x in (current_cast or []) if str(x or "").strip()]
        if current and all(_person_name_is_latin(x) for x in current):return
        self._cast_language_token += 1;cast_token=self._cast_language_token
        cfg=load_settings();credential=str(cfg.get("tmdb_credential") or "").strip()
        if not cfg.get("tmdb_enabled",True) or not credential:return
        mt="tv" if self.media_type=="series" else "movie"
        timeout=min(7,max(3,int(cfg.get("timeout",10) or 10)))
        cancel_event=self._details_cancel_event;screen_ref=weakref.ref(self)
        def apply_result(names):
            screen=screen_ref()
            if screen is not None:screen._apply_foreign_english_cast(names,cast_token)
        def worker():
            try:
                if cancel_event.is_set():return
                client=TMDBClient(credential,"en-US",timeout)
                payload=client._get("/%s/%s/credits"%(mt,int(tmdb_id)),{"language":"en-US"}) or {}
                rows=[x for x in (payload.get("cast") or []) if isinstance(x,dict)]
                names=[]
                for row in rows:
                    name=_pick_latin_person_name(client,row)
                    if name and name not in names:names.append(name)
                    if len(names)>=6:break
                if names and not cancel_event.is_set():reactor.callFromThread(apply_result,names)
            except Exception as exc:optional_failure("ui.details_cast_language_fetch",exc)
        try:self._track_details_future(_DETAIL_PREFETCH_EXECUTOR.submit(worker,priority=1))
        except Exception as exc:optional_failure("ui.details_cast_language_submit",exc)

    def _apply_foreign_english_cast(self, names, cast_token):
        if self._screen_closed or cast_token!=self._cast_language_token:return
        try:
            self["cast_text"].setText(_clean_display_text(", ".join([str(x) for x in names[:6]]),220))
            self._fit_compact_text("cast_text",max_size=18,min_size=12,padding=14)
            self._sync_detail_text_direction()
        except Exception as exc:optional_failure("ui.details_cast_language_apply",exc)

    @staticmethod
    def _logo_title_key(value):
        value=str(value or "").casefold()
        value=re.sub(r"[^\w\u0600-\u06ff]+"," ",value,flags=re.UNICODE)
        return " ".join(value.split())

    def _details_title_logo_cache_path(self, data, visible_title=""):
        try:
            item=dict(getattr(self,"_portal_item",{}) or getattr(self,"item",{}) or {})
            return ultra_title_logo_cached(self.media_type,item,data or {},self._title_logo_target_size or (432,210),visible_title)
        except Exception:return ""

    def _schedule_details_title_logo(self, data, visible_title):
        """Use the one Ultra-owned background title-logo service."""
        try:
            # R181: identity first-paint and final metadata may report the exact
            # same logo milliseconds apart. Never cancel/restart that same job.
            row=dict(data or {}) if isinstance(data,dict) else {}
            size=self._title_logo_target_size or (432,210)
            item=dict(getattr(self,"_portal_item",{}) or getattr(self,"item",{}) or {})
            request_key=(str(row.get("tmdb_id") or ""),str(row.get("logo_url") or ""),str(row.get("logo_language") or ""),str(visible_title or ""),tuple(size))
            previous=getattr(self,"_title_logo_future",None)
            if request_key==getattr(self,"_title_logo_request_key",None) and previous is not None and not previous.done():
                return
            self._title_logo_request_key=request_key
            self._title_logo_token += 1;req_id=self._title_logo_token
            # R101: keep the last good title logo visible while a refreshed
            # identity/logo is resolving. Cached hits below swap immediately.
            cached=ultra_title_logo_cached(self.media_type,item,row,size,visible_title)
            self._title_logo_cache_target=cached
            if cached:
                self._apply_ultra_details_title_logo(cached,req_id);return
            if previous is not None and not previous.done():
                try:previous.cancel()
                except Exception:pass
            cfg=load_settings() or {};self_ref=weakref.ref(self);row_copy=dict(row);item_copy=dict(item)
            def worker():
                owner=self_ref()
                if owner is None or owner._screen_closed or owner._details_cancel_event.is_set() or req_id!=owner._title_logo_token:return
                try:
                    path,_resolved=resolve_ultra_title_logo(owner.profile,owner.media_type,item_copy,row_copy,canvas_size=size,visible_title=visible_title,cancel_event=owner._details_cancel_event,settings=cfg)
                    owner=self_ref()
                    if owner is not None and not owner._screen_closed and req_id==owner._title_logo_token:
                        reactor.callFromThread(owner._apply_ultra_details_title_logo,path or "",req_id,True)
                except Exception as exc:optional_failure("ui.details_title_logo_worker",exc)
            future=_DETAIL_LOGO_EXECUTOR.submit(worker,_task_key="details-title-logo:%x"%id(self),_replace_task_key=True)
            self._title_logo_future=future;self._track_details_future(future)
        except Exception as exc:optional_failure("ui.details_title_logo_schedule",exc)

    def _apply_ultra_details_title_logo(self,path,req_id,final=False):
        try:
            if req_id!=self._title_logo_token or self._screen_closed:return
            path=str(path or "")
            if valid_ultra_title_logo(path):
                self._title_logo_cache_target=path
                if self["title_logo"].instance is not None:
                    self["title_logo"].instance.setPixmapFromFile(path);self["title_logo"].show()
                return
            if final:
                self._title_logo_cache_target=""
                try:
                    self["title_logo"].hide()
                    if self["title_logo"].instance is not None:self["title_logo"].instance.setPixmap(None)
                except Exception:pass
        except Exception as exc:optional_failure("ui.details_title_logo_apply",exc)

    def _stop_hero_pin_timer(self):
        try:self._hero_pin_timer.stop()
        except Exception:pass
        try:
            if self._hero_pin_conn is not None:self._hero_pin_conn.disconnect()
        except Exception:pass
        try:
            if self._pin_current_as_home_hero_retry in self._hero_pin_timer.callback:self._hero_pin_timer.callback.remove(self._pin_current_as_home_hero_retry)
        except Exception:pass

    def _pin_current_as_home_hero_retry(self):
        if getattr(self,"_screen_closed",False):return
        self._pin_current_as_home_hero(_retry=True)

    def _show_hero_pin_toast(self,final=False):
        try:
            if final:
                self.session.open(HeroPinToastScreen,_('Hero pinned'),_('Your selected backdrop is now the active Hero.'),1700)
            else:
                self.session.open(HeroPinToastScreen,_('Hero selected'),_('Preparing the adaptive Hero in the background…'),1900)
        except Exception as exc:optional_failure("ui.hero_pin_toast",exc)

    @staticmethod
    def _hero_old_generated_paths(hero):
        paths=[]
        if not isinstance(hero,dict):return paths
        for key in ("prepared",):
            value=str(hero.get(key) or "")
            if value:paths.append(value)
        for key in ("home_mood","adaptive_focus"):
            block=hero.get(key) if isinstance(hero.get(key),dict) else {}
            for value in block.values():
                value=str(value or "")
                if value:paths.append(value)
        return paths

    def _pin_current_as_home_hero(self,_retry=False):
        """V7.8-style explicit Hero pin using the already-cached selected backdrop.

        The selected HDD backdrop is the only source.  No TMDb redownload occurs.
        Home's top composite plus the exact adaptive mood/focus files are prepared
        serially in background, then one single persistent Hero state is published.
        """
        if not _retry:
            self._hero_pin_attempts=0
            try:self._hero_pin_timer.stop()
            except Exception:pass
        self._hero_pin_attempts=int(getattr(self,"_hero_pin_attempts",0) or 0)+1

        source=""
        candidates=[]
        tmdb=getattr(self,"_tmdb_data",None)
        if isinstance(tmdb,dict):candidates.extend([tmdb.get("backdrop_local"),tmdb.get("display_backdrop_local")])
        cache=getattr(self,"_local_detail_cache",None)
        if isinstance(cache,dict):candidates.extend([cache.get("backdrop_local"),cache.get("display_backdrop_local")])
        state=getattr(self,"_details_visual_state",None)
        if isinstance(state,dict):candidates.extend([state.get("backdrop"),state.get("backdrop_present")])
        live_item=self.item if isinstance(getattr(self,"item",None),dict) else {}
        portal_item=getattr(self,"_portal_item",None) if isinstance(getattr(self,"_portal_item",None),dict) else {}
        for obj in (live_item,portal_item):
            candidates.extend([obj.get("_backdrop_source_local"),obj.get("_cin_provider_backdrop_local"),obj.get("backdrop_local"),obj.get("display_backdrop_local")])
        candidates.extend([getattr(self,"_portal_backdrop_source_local",""),getattr(self,"_adaptive_source_local",""),getattr(self,"_backdrop_displayed_path","")])
        for candidate in candidates:
            try:
                candidate=str(candidate or "")
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>4096:
                    source=os.path.realpath(candidate);break
            except Exception:pass
        if not source:
            if self._hero_pin_attempts<21 and not getattr(self,"_screen_closed",False):
                try:self["status"].setText(_("Preparing Hero… waiting for cached backdrop"));self._hero_pin_timer.start(300,True)
                except Exception:pass
                return
            try:self["status"].setText(_("Hero not set • selected backdrop is not cached on HDD"))
            except Exception:pass
            return
        try:self._hero_pin_timer.stop()
        except Exception:pass

        item_snapshot=dict(live_item);media_type=str(self.media_type or "")
        raw_id=str(item_snapshot.get("tmdb_id") or item_snapshot.get("id") or item_snapshot.get("stream_id") or item_snapshot.get("series_id") or "")
        tmdb_id=(tmdb or {}).get("tmdb_id") if isinstance(tmdb,dict) and tmdb.get("matched") else None
        if not tmdb_id and isinstance(cache,dict) and cache.get("matched"):tmdb_id=cache.get("tmdb_id")
        if not tmdb_id:tmdb_id=item_snapshot.get("_locked_tmdb_id") or item_snapshot.get("tmdb_id")
        try:overview=self["description"].getText() or ""
        except Exception:overview=""

        request_token=str(int(time.time()*1000000));hero_dir=os.path.dirname(str(HOME_HERO_FILE or ""))
        marker=os.path.join("/tmp","ultrastalker_home_hero_pending")
        try:
            with open(marker,"w",encoding="ascii") as fh:fh.write(request_token)
        except Exception:pass
        self._show_hero_pin_toast(False)
        try:self["status"].setText(_("Hero selected • preparing adaptive materials…"))
        except Exception:pass

        source=str(source)
        def token_current():
            try:
                with open(marker,"r",encoding="ascii") as fh:return fh.read().strip()==request_token
            except Exception:return False

        def safe_unlink_generated(path,keep=()):
            try:
                path=str(path or "");base=os.path.basename(path)
                if not path or path in keep or not os.path.isfile(path):return
                # Never delete canonical movie/series artwork. Only old Hero files
                # and the release adaptive Home derivatives referenced by hero.json.
                if (os.path.realpath(path).startswith(os.path.realpath(hero_dir)+os.sep) or
                    base.startswith("dyn228home_") or base.startswith("dyn280home_")):
                    os.unlink(path)
            except OSError:pass
            except Exception:pass

        def worker():
            temp_home=""
            created=[]
            try:
                os.makedirs(hero_dir,mode=0o700,exist_ok=True)
                stamp=os.stat(source)
                digest=hashlib.sha1((source+"|%s|%s|manual-hero-v78-contract"%(int(stamp.st_mtime),int(stamp.st_size))).encode("utf-8","ignore")).hexdigest()[:20]
                temp_home=os.path.join(hero_dir,".hero.%s.%s.png"%(os.getpid(),request_token))
                prepared=_prepare_single_home_hero(source,temp_home,(1920,1080))
                if not prepared or not os.path.isfile(prepared) or os.path.getsize(prepared)<=1024:return False
                if not token_current():return False

                # R64: Set Hero changes artwork only.  Application chrome is
                # frozen and no legacy Home adaptive writer may run here.
                try:cleanup_legacy_application_outputs()
                except Exception:pass
                fixed=fixed_home_assets() or {}
                if not fixed:
                    return False
                adaptive_focus=dict(fixed.get("focus") or {})
                home_mood=dict(fixed.get("mood") or {})

                required=[adaptive_focus.get("menu"),adaptive_focus.get("recent"),home_mood.get("ambient"),home_mood.get("menu"),home_mood.get("recent")]
                if not all(p and os.path.isfile(str(p)) and os.path.getsize(str(p))>100 for p in required):return False

                old_hero={}
                try:
                    with open(HOME_HERO_FILE,"r",encoding="utf-8") as fh:
                        old=json.load(fh);old_hero=old.get("hero") if isinstance(old,dict) and isinstance(old.get("hero"),dict) else {}
                except Exception:old_hero={}

                with _HOME_HERO_LOCK:
                    if not token_current():return False
                    final_home=os.path.join(hero_dir,"hero.png")
                    os.replace(temp_home,final_home);temp_home=""
                    title_logo_local=str(getattr(self,"_title_logo_cache_target","") or "")
                    if not (title_logo_local and os.path.isfile(title_logo_local) and os.path.getsize(title_logo_local)>256):title_logo_local=""
                    hero_title=str(getattr(self,"_server_display_name","") or live_item.get("name") or live_item.get("title") or "")
                    hero={
                        "id":"manual-"+(raw_id or digest),"media_type":media_type,"tmdb_id":tmdb_id,
                        "title":hero_title,"overview":overview,"rating":0,"language":"","collection":"ULTRA STALKER",
                        "prepared":final_home,"backdrop_local":source,"display_backdrop_local":source,
                        "source_backdrop_local":source,"adaptive_focus":adaptive_focus,"home_mood":home_mood,
                        "title_logo_local":title_logo_local,"title_logo_lang":"en" if title_logo_local else "",
                        "manual_pin":True,"manual_pin_token":int(request_token),
                    }
                    payload={"schema":HOME_HERO_SCHEMA,"expires":0,"persistent":True,"manual_pin":True,
                             "manual_pin_token":int(request_token),"hero":hero}
                    fd,tmp=tempfile.mkstemp(prefix=".hero-state.",suffix=".tmp",dir=hero_dir)
                    try:
                        with os.fdopen(fd,"w",encoding="utf-8") as fh:
                            json.dump(payload,fh,ensure_ascii=False,separators=(",",":"));fh.flush();os.fsync(fh.fileno())
                        os.chmod(tmp,0o600);os.replace(tmp,HOME_HERO_FILE)
                    finally:
                        if os.path.exists(tmp):
                            try:os.unlink(tmp)
                            except OSError:pass

                keep=set([final_home]+[str(x or "") for x in required])
                for old_path in self._hero_old_generated_paths(old_hero):safe_unlink_generated(old_path,keep)
                try:
                    if token_current():os.unlink(marker)
                except OSError:pass
                return True
            except Exception as exc:
                optional_failure("ui.details_set_home_hero_v78_contract",exc);return False
            finally:
                if temp_home and os.path.exists(temp_home):
                    try:os.unlink(temp_home)
                    except OSError:pass
                if not token_current():
                    for path in created:safe_unlink_generated(path)

        def finished(result):
            if not result:return
            try:self["status"].setText(_("Hero pinned • active across Ultra Stalker"))
            except Exception:pass
            if not getattr(self,"_screen_closed",False):self._show_hero_pin_toast(True)

        try:
            old=getattr(self,"_hero_pin_future",None)
            if old is not None and not old.done():
                try:old.cancel()
                except Exception:pass
            future=_IMAGE_EXECUTOR.submit(worker)
            self._hero_pin_future=future
            def done(_f):
                try:result=bool(_f.result())
                except Exception:result=False
                if result:
                    try:reactor.callFromThread(finished,True)
                    except Exception:pass
            try:future.add_done_callback(done)
            except Exception:pass
        except Exception as exc:
            optional_failure("ui.details_set_home_hero_submit",exc)
            try:self["status"].setText(_("Hero failed: %s")%str(exc)[:90])
            except Exception:pass

    def _download_menu_selected(self, choice):
        if not choice:return
        action=choice[1]
        if action=="set_home_hero":
            self._pin_current_as_home_hero();return
        if action=="manager":
            self.session.open(DownloadsManagerScreen, getattr(self,"_adaptive_source_local","") or getattr(self,"_backdrop_displayed_path","") or (self.item.get("_backdrop_source_local") if isinstance(self.item,dict) else ""));return
        if action=="reload":
            self.reload_image();return
        if action=="movie":
            item=dict(self.item)
            job=movie_job(self.profile,item)
            def resolver():return self.client.create_link(item,"vod")
            ok,msg=DOWNLOADS.add(job,resolver)
            self["status"].setText(_(msg))

    def reload_image(self):
        self._image_token += 1
        ph = "grid_placeholder_movie_921.png" if self.media_type == "vod" else "grid_placeholder_series_921.png"
        self._decode_picture(asset(ph))
        try:self["cinematic_bg"].hide()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._load_tmdb_metadata()
        self["status"].setText(_("Reloading external artwork..."))

    def show_information(self):
        # Reuse the exact player information overlay instead of the legacy
        # details information screen. Keep this import local so normal details
        # opening cost remains unchanged.
        from .services.player_native import PlayerInformationOverlay
        title = _clean_display_text(self.item.get("name") or self.item.get("title") or _("Information"),90) if isinstance(self.item,dict) else _("Information")
        payload = dict(self.item) if isinstance(self.item,dict) else {}
        tmdb_data = getattr(self,"_tmdb_data",None)
        if isinstance(tmdb_data,dict):
            for key,value in tmdb_data.items():
                if value not in (None,"",[],{}):
                    payload[key]=value
        # The visible details values are authoritative for this overlay because
        # they already contain the final translated/cleaned metadata.
        try: payload["description"] = self["description"].getText() or payload.get("description") or ""
        except Exception as exc:optional_failure("ui.info_overlay_field",exc)
        try: payload["cast"] = self["cast_text"].getText() or payload.get("cast") or ""
        except Exception as exc:optional_failure("ui.info_overlay_field",exc)
        poster_path = ""
        try:
            state = getattr(self,"_details_visual_state",{}) or {}
            poster_path = str(state.get("detail_poster") or state.get("poster") or getattr(self,"_poster_authoritative_source","") or getattr(self,"_image_displayed_path","") or "")
            if poster_path and not os.path.isfile(poster_path): poster_path=""
        except Exception as exc:
            optional_failure("ui.info_overlay_poster",exc);poster_path=""
        meta_parts=[]
        for value in (self["quality_text"].getText(), self["year_text"].getText(), self["runtime_text"].getText(), self["country_text"].getText()):
            value=str(value or "").strip()
            if value and value not in ("—","-"): meta_parts.append(value)
        meta = "   •   ".join(meta_parts)
        self.session.openWithCallback(lambda *args:self._restore_details_visual_state(),PlayerInformationOverlay,title,payload,self.media_type,meta,poster_path)

    def play(self):
        if self._busy:return
        if self.media_type == "series":
            
            series_payload=dict(self.item) if isinstance(self.item,dict) else self.item
            if isinstance(series_payload,dict):
                # Carry the already-resolved canonical HDD metadata into Seasons
                # and Episodes.  The child screen is intentionally fast-open and
                # must not lose country/genre/ratings/season count/title-logo id
                # merely because the source changed from Portal to Xtream/M3U.
                snap=(getattr(self,"_tmdb_data",None) or getattr(self,"_local_detail_cache",None) or {})
                if isinstance(snap,dict):
                    overlay=snap.get("item_overlay") if isinstance(snap.get("item_overlay"),dict) else {}
                    for key,value in overlay.items():
                        if value not in (None,"",[],{}):series_payload[key]=value
                    for key in ("tmdb_id","imdb_id","tvdb_id","year","rating","number_of_seasons","number_of_episodes","logo_url","poster_local","backdrop_local"):
                        value=snap.get(key)
                        if value not in (None,"",[],{}):series_payload[key]=value
                    countries=snap.get("countries") or snap.get("production_countries") or snap.get("origin_country") or []
                    if countries and not series_payload.get("country"):
                        if isinstance(countries,(list,tuple)):series_payload["country"]=str(countries[0] or "")
                        else:series_payload["country"]=str(countries)
                    genres=snap.get("genres") or []
                    if genres:
                        if isinstance(genres,(list,tuple)):series_payload["genre"]=" / ".join(str(x) for x in genres[:3] if str(x or "").strip())
                        else:series_payload["genre"]=str(genres)
                    if snap.get("overview"):series_payload["description"]=snap.get("overview")
                    cast=snap.get("cast") or []
                    if cast:
                        series_payload["actors"]=(", ".join(str(x) for x in cast[:8]) if isinstance(cast,(list,tuple)) else str(cast))
                state=dict(getattr(self,"_details_visual_state",{}) or {})
                poster=str(getattr(self,"_adaptive_source_local","") or getattr(self,"_poster_authoritative_source","") or state.get("poster") or "")
                canonical=str(state.get("backdrop") or getattr(self,"_portal_backdrop_source_local","") or "")
                present=str(getattr(self,"_backdrop_displayed_path","") or state.get("backdrop_present") or getattr(self,"_portal_backdrop_present_local","") or "")
                if canonical and os.path.basename(canonical).startswith("us221_detail_"):
                    if not present:present=canonical
                    canonical=""
                if poster and os.path.isfile(poster):series_payload["_adaptive_source_local"]=poster
                if canonical and os.path.isfile(canonical):series_payload["_backdrop_source_local"]=canonical
                if present and os.path.isfile(present):series_payload["_backdrop_present_local"]=present
                fast_bundle={}
                for key in ("poster","detail_poster","backdrop","backdrop_present","theme","accent","edge","chrome"):
                    value=state.get(key)
                    if value not in (None,"",{},[]):fast_bundle[key]=value
                if poster and os.path.isfile(poster):fast_bundle["poster"]=poster
                if canonical and os.path.isfile(canonical):fast_bundle["backdrop"]=canonical
                if present and os.path.isfile(present):fast_bundle["backdrop_present"]=present
                series_payload["_ultra_fast_bundle"]=fast_bundle
                prefetched=getattr(self,"_prefetched_series_seasons",None)
                if isinstance(prefetched,list) and prefetched:series_payload["_prefetched_series_seasons"]=[dict(x) for x in prefetched if isinstance(x,dict)]
            self.session.openWithCallback(lambda *args:self._restore_details_visual_state(),SeriesEpisodesScreen, self.profile, self.client, series_payload)
            return
        command=self.item.get("cmd") or self.item.get("command") or self.item.get("url")
        if not command:self["status"].setText(_("No stream command"));return
        self["status"].setText(_("Creating stream link..."))
        def ok(url):
            url=url if isinstance(url,str) else ""
            name=str(self.item.get("name") or self.item.get("title") or "Ultra Stalker stream")
            try:
                self["status"].setText(_("Opening player: %s") % name)
                def closed(result=None):
                    engine = result.get("engine") if isinstance(result, dict) else load_settings().get("service_type", 4097)
                    if isinstance(result,dict) and result.get("quality"):
                        try:
                            remember_content_quality(self.profile,self.media_type,self.item,result.get("quality"),result.get("video_width",0),result.get("video_height",0))
                            self["quality_text"].setText(str(result.get("quality") or ""))
                            self._refresh_detail_visuals()
                        except Exception as exc: optional_failure("ui.detail_quality_return",exc)
                    try:self._restore_details_visual_state()
                    except Exception as exc:optional_failure("ui.detail_player_restore",exc)
                    self["status"].setText(_("Player closed  •  engine %s") % engine)
                engine=_configured_playback_engine()
                payload=_player_payload(self.item, self.profile, media_type=self.media_type)
                payload["_player_client_ref"]=self.client
                tmdb_data=getattr(self,"_tmdb_data",None)
                if isinstance(tmdb_data,dict):
                    poster_local=tmdb_data.get("poster_local")
                    existing=str(payload.get("_player_poster") or "")
                    if not (existing and os.path.isfile(existing)) and poster_local and os.path.isfile(poster_local):payload["_player_poster"]=poster_local
                    if tmdb_data.get("poster_url") and not payload.get("_player_poster_url"):payload["_player_poster_url"]=str(tmdb_data.get("poster_url"))
                    # The Details screen is the canonical identity authority at
                    # play time.  Carry that exact id into Player so a stale raw
                    # provider tmdb_id cannot select another title's logo.
                    verified_id=tmdb_data.get("tmdb_id") or (self.item.get("_locked_tmdb_id") if isinstance(self.item,dict) else None)
                    if verified_id not in (None,""):
                        payload["_player_title_logo_tmdb_id"]=verified_id
                        payload["_locked_tmdb_id"]=verified_id
                        payload["tmdb_id"]=verified_id
                    # Player deliberately does not consume the Details canvas.
                    # It resolves/uses the same Cinematic 420x144 authority for
                    # this exact verified id, then derives only its local 1220x72
                    # presentation from that source.
                    payload["_player_title_logo_title"]=str(getattr(self,"_server_display_name","") or name)
                self.session.openWithCallback(closed, UltraStalkerPlayer, str(url).strip(), name, self.media_type, engine, payload)
            except Exception as exc:self["status"].setText(_("Player failed: %s")%exc)
        self._run_async(lambda handle:self.client.create_link(self.item,self.media_type,cancel_event=handle.cancel_event),ok,lambda e:ok(""))

