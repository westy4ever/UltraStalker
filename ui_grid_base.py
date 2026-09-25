"""Premium grid base extracted from ui.py without changing class behavior."""

from . import _
from .age_rating import display_certification, certification_from_tmdb, rating_endpoint
from Screens.Screen import Screen
from .ui_async import AsyncScreenMixin
from .ui_grid_artwork import GridArtworkMixin
from .ui_fixed_adaptive import fixed_home_assets, cleanup_legacy_application_outputs
import threading
import os
import time
import json
import tempfile
import gc
import logging
from .core.shared_executors import CATALOGUE_EXECUTOR, CACHE_IO_EXECUTOR, VISIBLE_ARTWORK_EXECUTOR, METADATA_EXECUTOR
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag
from .core.call_compat import call_compatible
from .log import diagnostic_failure, memory_snapshot as _mem34
from twisted.internet import reactor

# R56: keep the R52 fast cached-poster reader available even when Enigma2
# retains an older ui.py dependency-injection state across an IPK upgrade.
# Importing artwork_v2 is lightweight; the heavy implementation remains lazy.
try:
    from .artwork_v2 import load_fast_local_poster as _direct_fast_local_poster
except Exception:
    _direct_fast_local_poster = None

def configure_grid_base(**deps):
    globals().update(deps)


# release: one catalogue-only lane shared by Poster Grid screens.  This mirrors
# the lightweight Cinematic page warmer: provider catalogue/state only, never
# artwork/TMDB/Pillow.  One worker keeps portal sockets serialized and RAM calm.
_GRID_PAGE_WARM_EXECUTOR=CATALOGUE_EXECUTOR
_VISIBLE_POSTER_HDD_EXECUTOR=CACHE_IO_EXECUTOR
_VISIBLE_POSTER_EXECUTOR=VISIBLE_ARTWORK_EXECUTOR
_VISIBLE_TMDB_EXECUTOR=METADATA_EXECUTOR


def _page_backdrop_q60_path(url):
    """Exact persistent Q60 path used by the R242/R262 Cinematic lane."""
    try:
        import hashlib as _hashlib
        from .persistent_cache import BACKDROPS
        text=str(url or "").strip()
        if not (text.startswith("https://image.tmdb.org/t/p/original/") or text.startswith("http://image.tmdb.org/t/p/original/")):
            return ""
        digest=_hashlib.sha1(text.encode("utf-8","ignore")).hexdigest()[:32]
        return os.path.join(BACKDROPS,digest+".jpg")
    except Exception:
        return ""


def _page_backdrop_q60_valid(path):
    """True only for the persistent URL-hash Q60 files used by all 3 views.

    Legacy ArtworkV2 w1280 files can live in the same BACKDROPS directory, so a
    size-only check is unsafe here: BLUE must never call one of those "already".
    """
    try:
        import re as _re
        from .persistent_cache import BACKDROPS
        path=str(path or "").strip()
        if not (path and os.path.isfile(path) and os.path.getsize(path)>4096):return False
        real=os.path.realpath(path);root=os.path.realpath(str(BACKDROPS or ""))
        if not root or not (real==root or real.startswith(root+os.sep)):return False
        return bool(_re.match(r"^[0-9a-f]{32}\.jpg$",os.path.basename(real),_re.I))
    except Exception:
        return False


def _page_backdrop_original_url(value):
    """Normalize a TMDb file_path/URL to the exact ORIGINAL authority URL."""
    try:
        import re as _re
        text=str(value or "").strip()
        if not text:return ""
        if text.startswith("/"):
            return "https://image.tmdb.org/t/p/original"+text
        if "image.tmdb.org/t/p/" not in text:return ""
        text=_re.sub(r"(/t/p/)(?:original|w\d+|h\d+)(/)",r"\1original\2",text,count=1,flags=_re.I)
        return text if text.startswith(("https://","http://")) else ""
    except Exception:
        return ""


def _page_backdrop_build_q60(raw_path,destination):
    """Build the same at-most-1920x1080 JPEG quality-60 file as Cinematic.

    This helper is button-only and imports Pillow lazily so plugin/Home startup
    stays untouched.  No crop, no upscale, no presentation derivative.
    """
    raw_path=str(raw_path or "").strip();destination=str(destination or "").strip()
    if not raw_path or not destination:return ""
    try:
        from PIL import Image as _Image
        from .persistent_cache import ensure_persistent_dirs
    except Exception:
        return ""
    if not (os.path.isfile(raw_path) and os.path.getsize(raw_path)>1024):return ""
    if not ensure_persistent_dirs(os.path.dirname(destination)):return ""
    temp=destination+".tmp.%s.%s"%(os.getpid(),threading.get_ident())
    try:
        with _Image.open(raw_path) as image:
            image=image.convert("RGB");w,h=image.size
            if w<16 or h<16:return ""
            scale=min(1.0,1920.0/float(w),1080.0/float(h))
            if scale<0.999:
                res=getattr(getattr(_Image,"Resampling",_Image),"LANCZOS",1)
                image=image.resize((max(1,int(round(w*scale))),max(1,int(round(h*scale)))),res)
            image.save(temp,"JPEG",quality=60,subsampling=2,optimize=False,progressive=False)
        if not (os.path.isfile(temp) and os.path.getsize(temp)>4096):return ""
        os.replace(temp,destination)
        return destination
    except Exception as exc:
        try:optional_failure("ui.page_backdrop_q60_build",exc)
        except Exception:pass
        return ""
    finally:
        try:
            if os.path.exists(temp):os.unlink(temp)
        except Exception:pass


def _page_backdrop_fetch_original_q60(url,destination,timeout=10,cancel_event=None):
    """Fetch one TMDb ORIGINAL to /tmp, then persist only its Q60 derivative."""
    url=str(url or "").strip();destination=str(destination or "").strip()
    if _page_backdrop_q60_valid(destination):return destination
    if not url or not destination:return ""
    try:
        import urllib.request as _urlrequest
        import urllib.parse as _urlparse
        from .netsec import build_safe_https_media_opener
    except Exception:
        return ""
    try:
        parts=_urlparse.urlsplit(url)
        if parts.scheme.lower()!="https" or (parts.hostname or "").lower()!="image.tmdb.org":return ""
        if cancel_event is not None and cancel_event.is_set():return ""
        suffix=".png" if parts.path.lower().endswith(".png") else ".jpg"
        fd,raw_path=tempfile.mkstemp(prefix="us_blue_q60_original_",suffix=suffix,dir="/tmp")
        os.close(fd)
        try:
            req=_urlrequest.Request(url,headers={"User-Agent":"Ultra Stalker/8.7.1 PageBackdropQ60","Accept":"image/*","Connection":"close"})
            opener=build_safe_https_media_opener(allowed_hosts=("image.tmdb.org",))
            total=0
            with opener.open(req,timeout=max(5.0,min(float(timeout or 10),15.0))) as response:
                status=int(getattr(response,"status",200) or 200)
                if status<200 or status>=300:return ""
                declared=int(response.headers.get("Content-Length") or 0)
                if declared and declared>20*1024*1024:return ""
                with open(raw_path,"wb") as handle:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():return ""
                        chunk=response.read(64*1024)
                        if not chunk:break
                        total+=len(chunk)
                        if total>20*1024*1024:return ""
                        handle.write(chunk)
            if total<=1024:return ""
            if cancel_event is not None and cancel_event.is_set():return ""
            return _page_backdrop_build_q60(raw_path,destination)
        finally:
            try:
                if os.path.isfile(raw_path):os.unlink(raw_path)
            except Exception:pass
    except Exception as exc:
        try:optional_failure("ui.page_backdrop_q60_download",exc)
        except Exception:pass
        return ""

def _progressive_poster_item_detached(profile, media_type, item, cfg, client, cancel_event=None):
    """Resolve one visible poster without retaining a Grid Screen instance."""
    if media_type not in ("vod","series") or not isinstance(item,dict):
        return "",{}
    if cancel_event is not None and cancel_event.is_set():
        return "",{}
    cfg=dict(cfg or {});profile=dict(profile or {})
    credential=str(cfg.get("tmdb_credential") or "").strip()
    if not cfg.get("load_images",True):
        return "",{}
    # R269: provider/server artwork bootstrap is retired. The bundled TMDb
    # credential is the default artwork authority; without it, do not hydrate
    # network artwork from the portal/server.
    if not credential:
        return "",{}
    if not cfg.get("tmdb_enabled",True):
        return "",{}
    try:
        snap=load_artwork_v2_manifest(profile,media_type,item) or {}
        poster=str(snap.get("poster_local") or "")
        if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
            return poster,snap
    except Exception:
        snap={}
    if cancel_event is not None and cancel_event.is_set():
        return "",{}
    try:
        data=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(5,max(3,int(cfg.get("timeout",10) or 10)))).resolve(
            profile,media_type,item,full=False,cancel_event=cancel_event) or {}
        poster=str(data.get("poster_local") or "")
        if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
            return poster,data
        if data.get("poster_candidate_unavailable"):
            purl=str(item.get("_visible_provider_art_url") or item.get("_rescue_provider_art_url") or "").strip()
            if not purl:
                try:purl=str(_image_url(item) or "").strip()
                except Exception:purl=""
            if purl and not item.get("_generic_provider_art"):
                provider=_download_portal_artwork(purl,profile,client,False,4.5,cancel_event,item=item) or ""
                if provider and os.path.isfile(provider):
                    try:
                        from .artwork_v2 import save_manual_rescue_art
                        saved=save_manual_rescue_art(profile,media_type,item,poster=provider,source="provider_auto_missing") or {}
                        rescued=str(saved.get("poster_local") or "")
                        if rescued and os.path.isfile(rescued):return rescued,saved
                    except Exception as exc:optional_failure("ui.grid_visible_provider_rescue_save",exc)
        return "",data
    except Exception as exc:
        optional_failure("ui.auto_visible_poster",exc)
        return "",{}


def _catalogue_error_kind(exc):
    """Classify catalogue failures without changing provider/client behavior."""
    low=str(exc or "").casefold()
    hard=(
        "security consent", "fallback is not approved", "certificate verification",
        "unapproved https", "unsupported portal transport", "invalid portal url",
    )
    if any(marker in low for marker in hard):
        return "hard"
    transient=(
        "connection", "timed out", "timeout", "non-json", "invalid page",
        "temporarily paused", "remote end closed", "connection reset",
        "broken pipe", "connection aborted", "eof", "http 502", "http 503",
        "http 504", "bad gateway", "service unavailable", "gateway timeout",
        "premature catalogue page",
    )
    return "transient" if any(marker in low for marker in transient) else "other"


class PremiumGridBase(Screen, AsyncScreenMixin, GridArtworkMixin):
    columns=1; page_size=1; card_positions=[]; image_size=(100,100); placeholder="us168_live_placeholder_220x132.png"
    selection_asset="us205_live_selection_neutral.png"
    # R49: only screens that actually draw per-card scores opt in.
    # This keeps Poster Grid V2/Cinematic free of metadata work by default.
    visible_card_rating_prefetch=False

    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        Screen.__init__(self,session);self._async_init();self.onClose.append(self._stop_async)
        self._ui_diag_open_mono=time.monotonic();self._ui_diag_page_request_mono=self._ui_diag_open_mono;self._ui_diag_first_poster_logged=False
        _ui_diag("ui_open",screen="grid",media_type=media_type)
        self.profile=profile;self.client=client;self.media_type=media_type;self.genre=str(genre or "*")
        # Settings are immutable for the lifetime of a grid screen. Reading the
        # JSON file once avoids 12-24 filesystem reads every time a page is
        # rendered while still picking up changes when the screen is reopened.
        self._grid_settings=load_settings()
        self.category_title=_clean_display_text(category_title,70) or (_("Live TV") if media_type=="itv" else (_("Movies") if media_type=="vod" else _("Series")))
        self._nav_key=(str(profile.get("portal") or "").rstrip("/").lower(),str(media_type),self.genre)
        self.page=1;self.grid_items=[];self.index=0;self._grid_item_state={};self._explicit_quality_cache={}
        self._restore_page=1;self._restore_index=0
        # View modes use different page sizes (Poster 14, PGV2 27, Cinematic 12,
        # Backdrop 8/7).  A raw page number from another presentation therefore
        # cannot be restored safely: e.g. Backdrop page 4 (items 25-32) becomes
        # Poster page 4 (starts at item 43) and opens as a false empty grid.
        # Keep navigation state presentation-local for VOD/Series. Live has only
        # one presentation and retains its historical key for player-return data.
        self._view_nav_id=(self.__class__.__name__,int(getattr(self,"page_size",1) or 1)) if media_type in ("vod","series") else None
        self._initial_grid_paint_done=False
        # Folder-local Search + Sort view state. Movies/Series only.
        self._folder_catalog=None
        self._folder_catalog_loading=False
        self._folder_catalog_waiters=[]
        self._search_query=""
        # R76: transient provider-search rows are kept separate from the full
        # folder catalogue. Search no longer has to inventory every page before
        # showing results, while Sort can still request the complete catalogue.
        self._folder_search_rows=None
        self._sort_mode=0
        self._view_items=None
        self._view_active=False
        self._folder_artwork_cache_running=False
        self._folder_artwork_cache_done=0
        self._folder_artwork_cache_total=0
        self._folder_artwork_progress=queue.Queue()
        self._folder_artwork_ui_finalized=False
        self._session_nav_key=(current_plugin_launch(),)+tuple(self._nav_key)
        if self._view_nav_id is not None:
            self._session_nav_key=self._session_nav_key+("view",)+tuple(self._view_nav_id)
        try:
            _g=_GRID_NAV_STATE.get(self._session_nav_key,{})
            self._restore_page=max(1,int(_g.get("page",1)));self._restore_index=max(0,int(_g.get("index",0)))
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._portal_page_cache=OrderedDict();self._portal_page_size=0;self._portal_total=0
        self._portal_page_cache_limit=6;self._last_nav_at=0.0;self._nav_burst_until=0.0
        self._prepared_page_cache=OrderedDict();self._prepared_page_lock=threading.RLock();self._prepared_page_pending=set();self._prepared_page_cache_limit=4
        self._grid_page_warm_running=False;self._grid_page_warm_token=0
        # Per-screen visual RAM. Keyed by stable content identity, not page
        # number, so revisiting/sorting never blanks cards or re-downloads art.
        self._page_visual_ram=OrderedDict();self._page_visual_ram_limit=512
        # R226: stable per-screen age authority, keyed by content identity rather
        # than page/slot. Returning to a page republishes certification from RAM
        # before first paint and never repeats a completed age lookup.
        self._visible_age_cache=OrderedDict();self._visible_age_cache_limit=1024
        self._grid_visual_locks={}
        try:
            _desk=getDesktop(0).size();self._grid_sx=float(_desk.width())/1920.0;self._grid_sy=float(_desk.height())/1080.0
        except Exception:
            self._grid_sx=1.0;self._grid_sy=1.0
        self._scaled_card_positions=[(int(x*self._grid_sx),int(y*self._grid_sy)) for x,y in self.card_positions]
        self._page_load_handle=None; self._pending_page_request=None
        # Stage 8: VOD/Series page navigation owns a tiny virtual destination.
        # Arrow repeat can advance across several pages without starting one
        # provider request per intermediate page.  The currently painted page
        # stays visible until the final target is ready.
        self._grid_page_nav_target=0;self._grid_page_nav_index=0;self._grid_page_nav_generation=0
        self._grid_page_nav_timer=eTimer();self._grid_page_nav_conn=None
        try:self._grid_page_nav_conn=self._grid_page_nav_timer.timeout.connect(self._commit_grid_page_nav)
        except Exception:self._grid_page_nav_timer.callback.append(self._commit_grid_page_nav)
        self.onClose.append(self._stop_grid_page_nav)
        self._page_prefetch_futures=[];self._page_prefetch_future_jobs=queue.Queue()
        self._quality_prefetch_jobs=queue.Queue();self._art_prefetch_jobs=queue.Queue()
        self._epg_cache={};self._epg_jobs=queue.Queue();self._epg_pending=None
        self._epg_timer=eTimer();self._epg_timer_conn=None
        try:self._epg_timer_conn=self._epg_timer.timeout.connect(self._fetch_epg)
        except Exception:self._epg_timer.callback.append(self._fetch_epg)
        self._detail_prefetch_timer=eTimer();self._detail_prefetch_conn=None
        try:self._detail_prefetch_conn=self._detail_prefetch_timer.timeout.connect(self._prefetch_selected_details)
        except Exception:self._detail_prefetch_timer.callback.append(self._prefetch_selected_details)
        # Debounce the expensive adaptive focus work.  Navigation itself stays
        # immediate; palette/chrome/background work only starts when the user
        # pauses briefly on an item.
        self._grid_focus_timer=eTimer();self._grid_focus_timer_conn=None
        try:self._grid_focus_timer_conn=self._grid_focus_timer.timeout.connect(self._apply_debounced_grid_focus)
        except Exception:self._grid_focus_timer.callback.append(self._apply_debounced_grid_focus)
        # beta58: hierarchy warming has its own longer debounce. 300 ms was far
        # too eager for remote-control browsing and created a queue of network calls.
        self._series_prefetch_timer=eTimer();self._series_prefetch_timer_conn=None
        try:self._series_prefetch_timer_conn=self._series_prefetch_timer.timeout.connect(self._prefetch_selected_series_hierarchy)
        except Exception:self._series_prefetch_timer.callback.append(self._prefetch_selected_series_hierarchy)
        self._series_hierarchy_prefetch_cancel=threading.Event()
        self._series_hierarchy_prefetch_token=0
        self._grid_idle_warm_timer=eTimer();self._grid_idle_warm_conn=None;self._grid_full_chrome_warm=False
        try:self._grid_idle_warm_conn=self._grid_idle_warm_timer.timeout.connect(self._warm_idle_grid_page)
        except Exception:self._grid_idle_warm_timer.callback.append(self._warm_idle_grid_page)
        # PERF34: one passive delayed RSS sample after each page settles. It reads
        # /proc only and never runs GC or changes pixmap/cache ownership.
        self._mem34_timer=eTimer();self._mem34_timer_conn=None
        try:self._mem34_timer_conn=self._mem34_timer.timeout.connect(self._mem34_grid_settled)
        except Exception:self._mem34_timer.callback.append(self._mem34_grid_settled)
        self.onClose.append(self._mem34_stop_timer)

        # beta51: HDD-only visible poster watcher. Background jobs may finish after
        # their one-shot UI event became stale due to page/generation timing. This
        # watcher never uses network; it simply notices newly persisted posters
        # for the 12 visible cards and paints them immediately.
        self._visible_poster_watch_timer=eTimer();self._visible_poster_watch_conn=None
        try:self._visible_poster_watch_conn=self._visible_poster_watch_timer.timeout.connect(self._poll_visible_poster_cache)
        except Exception:self._visible_poster_watch_timer.callback.append(self._poll_visible_poster_cache)
        self._page_prefetch_seen=set();self._visible_tmdb_pending=set();self._visible_tmdb_rating_cache={};self._page_quality_seen=set();self._page_art_retry_count={};self._page_prefetch_lock=threading.RLock();self._page_prefetch_generation=0;self._page_prefetch_cancel=threading.Event()
        # PERFLAB12 balanced focus lane: keep visible poster hydration bounded,
        # but give heavy full-artwork work a separate cancel token per focus.
        self._focus_art_cancel=threading.Event();self._focus_art_future=None;self._focus_art_token=0
        # beta47: poster-only progressive folder warmer. It never fetches backdrop/details.
        self._progressive_poster_cancel=threading.Event();self._progressive_poster_started=False
        self.onClose.append(self._stop_progressive_poster_prefetch)
        self.onClose.append(self._stop_focus_heavy_artwork)
        self.onClose.append(self._stop_visible_poster_watch)
        self.onClose.append(self._stop_grid_ui_hooks)
        self.onClose.append(self._grid_art_stop);self.onClose.append(self._stop_grid_epg);self.onClose.append(self._stop_detail_prefetch);self.onClose.append(self._stop_grid_focus_timer);self.onClose.append(self._stop_series_hierarchy_prefetch);self.onClose.append(self._stop_grid_idle_warm_timer);self.onClose.append(self._runtime_grid_close)
        self["brand"]=Label("");self["section"]=Label(((_("Live TV")+"  /  "+self.category_title[:28]) if media_type=="itv" else self.category_title[:48]));self["title"]=Label(self.category_title)
        self["clock"]=Label("");self._grid_clock=None;self._grid_clock_conn=None
        if not getattr(self,"disable_grid_clock",False):
            self["clock"].setText(time.strftime("%H:%M"));self._grid_clock=eTimer()
            try:self._grid_clock_conn=self._grid_clock.timeout.connect(self._update_grid_clock)
            except Exception:self._grid_clock.callback.append(self._update_grid_clock)
            try:self._grid_clock.start(30000,False)
            except Exception as exc:optional_failure("ui",exc)
            self.onClose.append(self._stop_grid_clock)
        self.onClose.append(self._ui_diag_grid_close)
        self["rating"]=Label("");self["meta1"]=Label("");self["meta2"]=Label("")
        self["status"]=Label(_("Loading..."));self["page_label"]=Label(_("Page 1"))
        self["red"]=Label(_("Search") if media_type in ("vod","series") else _("Back"));self["green"]=Label(_("Favorite"))
        self["yellow"]=Label(_("Default") if media_type in ("vod","series") else _("Previous page"));self["blue"]=Label(_("Check Artwork") if media_type in ("vod","series") else _("Next page"))
        self["selection"]=Pixmap()
        if media_type in ("vod","series"):
            self["selection_adaptive"]=Pixmap()
            self["page_adaptive_bg"]=Pixmap()
            self["poster_folder_bg"]=Pixmap();self["poster_title_bg"]=Pixmap();self["poster_clock_bg"]=Pixmap();self["poster_counter_bg"]=Pixmap()
            self["date_label"]=Label(time.strftime("%A, %d %B %Y"))
            self._poster_hud_last={};self._poster_hud_source="";self._poster_hud_pending=set();self._poster_hud_jobs=queue.Queue()
            self._grid_mood_jobs=queue.Queue();self._grid_mood_token=0;self._grid_mood_source="";self._grid_mood_pending=""
        slot_names=[]
        for pos in range(self.page_size):
            name="art%d"%pos;slot_names.append(name);self[name]=Pixmap();self["item_title%d"%pos]=Label("");self["item_meta%d"%pos]=Label("")
            if media_type in ("vod","series"):
                self["card_chrome%d"%pos]=Pixmap()
                # Fixed local rating widgets.  The gold star is a packaged PNG asset,
                # not a font glyph and never participates in poster/network loading.
                self["item_age%d"%pos]=Label("");self["item_year%d"%pos]=Label("");self["item_star%d"%pos]=Pixmap();self["item_score%d"%pos]=Label("")
        self._grid_art_init(slot_names,self.image_size,profile,client)
        _red_action=self.open_folder_search if media_type in ("vod","series") else self.close
        _yellow_action=self.cycle_folder_sort if media_type in ("vod","series") else self.previous_page
        _blue_action=self.cache_folder_artwork if media_type in ("vod","series") else self.next_page
        self["actions"]=ActionMap(["OkCancelActions","ColorActions","DirectionActions","MenuActions","UltraStalkerMenuActions","InfoActions"],{
            "cancel":self.close,"red":_red_action,"ok":self.open_selected,"left":self.move_left,"right":self.move_right,
            "up":self.move_up,"down":self.move_down,"green":self.toggle_selected_favorite,"yellow":_yellow_action,
            "blue":_blue_action,"menu":self.open_menu,"info":self.show_information},-1)
        self.onLayoutFinish.append(self._grid_layout_ready)
        if media_type in ("vod","series"):self.onLayoutFinish.append(self._refresh_search_sort_controls)
        try:self.onHide.append(self._grid_hidden_release)
        except Exception as exc:optional_failure("ui.grid_hide_hook",exc)
        try:self.onShown.append(self._grid_shown_resume)
        except Exception as exc:optional_failure("ui.grid_show_hook",exc)
        _mem34("grid_init_done", mode=self.__class__.__name__, media=str(media_type or ""))

    def _mem34_stop_timer(self):
        try:self._mem34_timer.stop()
        except Exception:pass
        try:
            if self._mem34_timer_conn is not None:self._mem34_timer_conn.disconnect()
        except Exception:pass
        try:
            if self._mem34_grid_settled in self._mem34_timer.callback:self._mem34_timer.callback.remove(self._mem34_grid_settled)
        except Exception:pass

    def _mem34_schedule_settled(self):
        try:self._mem34_timer.stop();self._mem34_timer.start(2500,True)
        except Exception:self._mem34_grid_settled()

    def _mem34_grid_settled(self):
        try:self._mem34_timer.stop()
        except Exception:pass
        _mem34(
            "grid_settled", mode=self.__class__.__name__, media=str(getattr(self,"media_type","") or ""),
            page=int(getattr(self,"page",0) or 0), items=len(getattr(self,"grid_items",[]) or []),
            visual_ram=len(getattr(self,"_page_visual_ram",{}) or {}), slot_paths=len(getattr(self,"_grid_slot_paths",{}) or {}),
            inflight=len(getattr(self,"_grid_download_inflight",{}) or {})
        )

    def _grid_is_m3u_source(self):
        try:
            source=str((self.profile or {}).get("source_type") or "").lower().strip()
            portal=str((self.profile or {}).get("portal") or "").lower()
            return source=="m3u" or ".m3u" in portal or "type=m3u" in portal or "output=m3u" in portal
        except Exception:
            return False

    def _page_visual_key(self,item):
        try:
            from .persistent_cache import content_cache_key
            return str(content_cache_key(self.profile,self.media_type,item) or "")
        except Exception:
            try:return hashlib.sha1(repr(sorted((item or {}).items())).encode("utf-8","ignore")).hexdigest()
            except Exception:return ""

    def _age_cache_get(self,item):
        if self.media_type not in ("vod","series") or not isinstance(item,dict):
            return (False,"")
        key=self._page_visual_key(item)
        if not key:return (False,"")
        try:
            row=dict((getattr(self,"_visible_age_cache",{}) or {}).get(key) or {})
            if not row:return (False,"")
            try:self._visible_age_cache.move_to_end(key)
            except Exception:pass
            return (bool(row.get("checked")),str(row.get("raw") or "").strip())
        except Exception as exc:
            optional_failure("ui.age_cache_get",exc);return (False,"")

    def _age_cache_put(self,item,raw="",checked=False):
        if self.media_type not in ("vod","series") or not isinstance(item,dict):return
        value=str(raw or "").strip();done=bool(checked or value)
        if not done:return
        key=self._page_visual_key(item)
        if not key:return
        try:
            self._visible_age_cache[key]={"raw":value,"checked":done}
            self._visible_age_cache.move_to_end(key)
            while len(self._visible_age_cache)>int(getattr(self,"_visible_age_cache_limit",1024) or 1024):
                self._visible_age_cache.popitem(last=False)
        except Exception as exc:optional_failure("ui.age_cache_put",exc)

    def _age_cache_seed_item(self,item):
        """Publish only a resolved/displayable age result before first paint.

        Old/provider rows can carry text tokens (NR, Unrated, unknown regional
        vocab) together with a stale `_age_rating_checked` marker.  Such a token
        must stay raw for provenance but must NOT close the canonical TMDb age
        lane.  A checked blank, on the other hand, is a valid cached negative
        result and prevents useless repeat lookups when we already proved that
        no +N badge exists.
        """
        if self.media_type not in ("vod","series") or not isinstance(item,dict):return False
        raw=str(item.get("certification") or item.get("age_rating") or "").strip()
        shown=display_certification(raw) if raw else ""
        if raw:
            item["certification"]=raw;item["age_rating"]=raw
            if shown:
                item["_age_rating_checked"]=True
                self._age_cache_put(item,raw,True)
                return True
            # Non-displayable text is unresolved, even if an older build marked
            # it checked.  Do not persist that stale terminal state into R227.
            item.pop("_age_rating_checked",None)
        elif item.get("_age_rating_checked"):
            self._age_cache_put(item,"",True)
            return True
        checked,cached=self._age_cache_get(item)
        if not checked:return False
        item["_age_rating_checked"]=True
        if cached:
            item["certification"]=cached;item["age_rating"]=cached
        else:
            # A cached canonical negative must not resurrect an old provider NR.
            item.pop("certification",None);item.pop("age_rating",None)
        return True

    def _page_visual_valid_path(self,path):
        value=str(path or "")
        if not value or not os.path.isfile(value):
            return ""
        name=os.path.basename(value).lower()
        if "placeholder" in name or name.startswith(("poster_movie","poster_series","placeholder_live")):
            return ""
        try:
            if os.path.getsize(value)<=100:return ""
        except Exception:return ""
        return value

    def _page_visual_get(self,item):
        if self.media_type not in ("vod","series") or not isinstance(item,dict):
            return {}
        key=self._page_visual_key(item)
        if not key:return {}
        try:
            row=dict(self._page_visual_ram.get(key) or {})
            if not row:return {}
            display=self._page_visual_valid_path(row.get("display"))
            poster=self._page_visual_valid_path(row.get("poster"))
            palette=self._page_visual_valid_path(row.get("palette"))
            card=self._page_visual_valid_path(row.get("card"))
            backdrop=self._page_visual_valid_path(row.get("backdrop"))
            if not (display or poster or backdrop):
                self._page_visual_ram.pop(key,None)
                return {}
            out={}
            if display:out["display"]=display
            if poster:out["poster"]=poster
            if palette:out["palette"]=palette
            if card:out["card"]=card
            if backdrop:out["backdrop"]=backdrop
            if row.get("provider_locked"):out["provider_locked"]=True
            self._page_visual_ram.move_to_end(key)
            return out
        except Exception as exc:
            optional_failure("ui.page_visual_get",exc)
            return {}

    def _page_visual_put(self,item,display=None,poster=None,palette=None,card=None,backdrop=None,provider_locked=None):
        if self.media_type not in ("vod","series") or not isinstance(item,dict):
            return
        key=self._page_visual_key(item)
        if not key:return
        try:
            row=dict(self._page_visual_ram.get(key) or {})
            for field,value in (("display",display),("poster",poster),("palette",palette),("card",card),("backdrop",backdrop)):
                valid=self._page_visual_valid_path(value)
                if valid:row[field]=valid
            if provider_locked is not None:
                row["provider_locked"]=bool(provider_locked)
            if not (self._page_visual_valid_path(row.get("display")) or self._page_visual_valid_path(row.get("poster")) or self._page_visual_valid_path(row.get("backdrop"))):
                return
            self._page_visual_ram[key]=row
            self._page_visual_ram.move_to_end(key)
            while len(self._page_visual_ram)>int(self._page_visual_ram_limit or 512):
                self._page_visual_ram.popitem(last=False)
        except Exception as exc:
            optional_failure("ui.page_visual_put",exc)

    def _remember_grid_visual(self,slot,display=None,poster=None,palette=None,card=None,backdrop=None,provider_locked=None):
        try:
            pos=int(slot)
            if pos<0 or pos>=len(self.grid_items):return
            item=self.grid_items[pos]
            if display is None:display=(getattr(self,"_grid_slot_paths",{}) or {}).get(pos)
            if palette is None:palette=(getattr(self,"_grid_palette_sources",{}) or {}).get(pos)
            if card is None:card=(getattr(self,"_grid_card_paths",{}) or {}).get(pos)
            # R37: a visual slot is not an identity.  Stamp the exact portal +
            # content key that owns the pixels currently painted in this slot so
            # Player can never inherit a stale poster from an earlier title.
            key=self._page_visual_key(item)
            if key:
                if not isinstance(getattr(self,"_grid_slot_identities",None),dict):self._grid_slot_identities={}
                self._grid_slot_identities[pos]=key
            self._page_visual_put(item,display=display,poster=poster,palette=palette,card=card,backdrop=backdrop,provider_locked=provider_locked)
        except Exception as exc:
            optional_failure("ui.page_visual_remember",exc)

    def _visible_player_poster_path(self,item=None,index=None):
        """Return a poster that is proven to belong to the selected content.

        R37 identity lock: slot numbers are recycled while the user navigates.
        A path from slot N is therefore accepted only when that slot is stamped
        with the same portal-scoped content key as the item being played.
        """
        try:
            pos=int(self.index if index is None else index)
        except Exception:
            pos=0
        try:
            current=item if isinstance(item,dict) else (self.grid_items[pos] if 0<=pos<len(self.grid_items) else {})
            visual=self._page_visual_get(current) if isinstance(current,dict) else {}
            current_key=self._page_visual_key(current) if isinstance(current,dict) else ""
            slot_key=str((getattr(self,"_grid_slot_identities",{}) or {}).get(pos) or "")
            candidates=[
                (visual or {}).get("poster"),
                (visual or {}).get("display"),
            ]
            if isinstance(current,dict):
                handed_key=str(current.get("_player_poster_identity") or "")
                if not handed_key or handed_key==current_key:
                    candidates.extend((current.get("_player_poster"),current.get("_ultra_poster_source"),current.get("_adaptive_source_local")))
            if current_key and slot_key==current_key:
                candidates.extend(((getattr(self,"_grid_slot_paths",{}) or {}).get(pos),(getattr(self,"_grid_palette_sources",{}) or {}).get(pos)))
            for candidate in candidates:
                path=str(candidate or "").strip()
                if not path or not os.path.isfile(path):continue
                try:
                    if os.path.getsize(path)<=256:continue
                except Exception:
                    continue
                bn=os.path.basename(path).lower()
                if "placeholder" in bn or bn.startswith("poster_movie") or bn.startswith("poster_series"):continue
                if isinstance(current,dict) and current_key:
                    current["_player_poster_identity"]=current_key
                return path
        except Exception as exc:
            optional_failure("ui.visible_player_poster",exc)
        return ""

    def _stop_grid_ui_hooks(self):
        for queue_name in (
            "_quality_prefetch_jobs",
            "_art_prefetch_jobs",
            "_epg_jobs",
            "_folder_artwork_progress",
            "_poster_hud_jobs",
            "_grid_mood_jobs",
        ):
            result_queue=getattr(self,queue_name,None)
            if result_queue is None:continue
            try:
                while True:result_queue.get_nowait()
            except queue.Empty:
                pass
            except Exception as exc:
                optional_failure("ui.grid_queue_cleanup",exc)
        self._epg_pending=None
        for hook_name,callback in (
            ("onLayoutFinish",self._grid_layout_ready),
            ("onLayoutFinish",self._refresh_search_sort_controls),
            ("onHide",self._grid_hidden_release),
            ("onShown",self._grid_shown_resume),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.grid_hook_cleanup",exc)

    def _ui_diag_grid_close(self):
        try:
            _ui_diag("ui_close",screen="grid",media_type=getattr(self,"media_type",""),page=getattr(self,"page",0),
                     lifetime_ms=int(max(0.0,(time.monotonic()-float(getattr(self,"_ui_diag_open_mono",time.monotonic())))*1000.0)))
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.161",exc)

    def _grid_layout_ready(self):
        try:_ui_diag("ui_layout",screen="grid",media_type=self.media_type,elapsed_ms=int((time.monotonic()-self._ui_diag_open_mono)*1000.0))
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.165",exc)
        self._grid_art_layout_ready()
        try:
            initial_selector=asset(self.selection_asset)
            self["selection"].instance.setPixmapFromFile(initial_selector);self["selection"].show()
            self._grid_selection_visual_path=initial_selector
            if self.media_type in ("vod","series"):
                self["selection_adaptive"].hide()
                self._grid_selection_adaptive_visual_path=""
        except Exception as exc:optional_failure("ui",exc)
        if not getattr(self,"disable_grid_clock",False):self._update_grid_clock()
        _mem34("grid_layout_ready", mode=self.__class__.__name__, media=str(self.media_type or ""))
        self.load_page(self._restore_page, self._restore_index)

    def _grid_hidden_release(self):
        if getattr(self,"_screen_closed",False) or getattr(self,"_grid_closed",False):return
        self._grid_images_suspended=True

        # Child screens are temporary. Keep all already-decoded poster, chrome,
        # selector and mood pixmaps alive. 10.0.75-78 released every surface here,
        # then raced an async page rebuild on BACK; the selected card returned
        # naked until the next LEFT/RIGHT forced another selection repaint.
        try:self._grid_cancel_queued_downloads()
        except Exception as exc:optional_failure("ui.grid_hide_cancel",exc)
        try:self._grid_focus_timer.stop()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.186",exc)
        try:self._detail_prefetch_timer.stop()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.188",exc)
        try:self._grid_idle_warm_timer.stop()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.190",exc)
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.194",exc)
        self._cancel_detached_page_prefetch_futures()
        for future in list(getattr(self,"_page_prefetch_futures",[]) or []):
            try:future.cancel()
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.197",exc)
        self._page_prefetch_futures=[]

    def _grid_shown_resume(self):
        if not getattr(self,"_grid_images_suspended",False):return
        if getattr(self,"_screen_closed",False) or getattr(self,"_grid_closed",False):return
        self._grid_images_suspended=False
        try:self._grid_art_layout_ready()
        except Exception as exc:optional_failure("ui.grid_resume_layout",exc)

        if not getattr(self,"grid_items",None):return
        # Rebind only the current selection/header. All poster/chrome surfaces
        # were preserved, so a full _render_grid() here is wasted work and is the
        # source of the visible blank-card flash on OpenBH.
        try:self._update_selection()
        except Exception as exc:optional_failure("ui.grid_resume_selection",exc)
        try:
            if self.media_type in ("vod","series"):self._grid_apply_current_selector()
            elif self.media_type=="itv" and hasattr(self,"_force_live_visual_rebind"):self._force_live_visual_rebind()
        except Exception as exc:optional_failure("ui.grid_resume_rebind",exc)

    def _cancel_detached_page_prefetch_futures(self):
        """Cancel futures submitted by the detached HDD discovery worker."""
        q=getattr(self,"_page_prefetch_future_jobs",None)
        if q is None:return
        while True:
            try:_generation,_key,future=q.get_nowait()
            except queue.Empty:break
            except Exception:break
            try:future.cancel()
            except Exception:pass

    def _adopt_detached_page_prefetch_futures(self, limit=16):
        q=getattr(self,"_page_prefetch_future_jobs",None)
        if q is None:return
        current=int(getattr(self,"_page_prefetch_generation",0) or 0)
        for _ in range(max(1,int(limit or 1))):
            try:generation,key,future=q.get_nowait()
            except queue.Empty:break
            except Exception:break
            if generation!=current or getattr(self,"_screen_closed",False):
                try:future.cancel()
                except Exception:pass
                continue
            if future.done():
                try:future.result()
                except Exception:
                    try:
                        with self._page_prefetch_lock:self._page_prefetch_seen.discard(key)
                    except Exception:pass
                continue
            self._page_prefetch_futures.append(future)

    def _drain_visible_poster_results(self, limit=1):
        self._adopt_detached_page_prefetch_futures()
        applied=0
        retry_positions=[]
        for _ in range(max(1,int(limit or 1))):
            try:
                _ajob=self._art_prefetch_jobs.get_nowait()
                if len(_ajob)>=4:agen,akey,apath,ameta=_ajob[0],_ajob[1],_ajob[2],_ajob[3]
                else:agen,akey,apath=_ajob;ameta=None
            except queue.Empty:
                break
            if agen != int(getattr(self,"_page_prefetch_generation",0) or 0):
                continue
            matched_pos=None
            for pos,item in enumerate(self.grid_items[:self.page_size]):
                try:
                    from .persistent_cache import content_cache_key
                    ikey=content_cache_key(self.profile,self.media_type,item)
                except Exception:
                    ikey=""
                if ikey==akey:
                    matched_pos=pos
                    break
            if apath=="__RETRY__":
                try:
                    with self._page_prefetch_lock:
                        self._page_prefetch_seen.discard(akey)
                        count=int(self._page_art_retry_count.get(akey,0) or 0)
                        if count<1 and matched_pos is not None:
                            self._page_art_retry_count[akey]=count+1
                            retry_positions.append(matched_pos)
                except Exception as exc:
                    optional_failure("ui.art_retry_state",exc)
                continue
            # release: metadata is independent from poster decode.  A canonical
            # TMDb score may already be present on HDD even when this worker did
            # not return a usable poster path.  Apply identity/rating first so a
            # poster cache miss can never blank the score row.
            if matched_pos is not None and isinstance(ameta,dict):
                try:
                    target_item=self.grid_items[matched_pos]
                    if ameta.get("tmdb_id"):
                        target_item["_locked_tmdb_id"]=ameta.get("tmdb_id")
                        target_item["_locked_tmdb_type"]=ameta.get("media_type") or ("tv" if self.media_type=="series" else "movie")
                        self._grid_item_state.setdefault(id(target_item),{})["tmdb_id"]=ameta.get("tmdb_id")
                    # TMDb full metadata already contains the canonical release/first-air
                    # year.  Keep a valid provider year if present; otherwise publish
                    # TMDb's year onto the live card so Movies and Series render the
                    # same YEAR + star + score row without any extra network request.
                    try:
                        if self._folder_year_value(target_item) <= 0:
                            _tmdb_year=self._folder_year_value(ameta)
                            if _tmdb_year > 0:
                                target_item["year"]=_tmdb_year
                    except Exception as exc:
                        optional_failure("ui.grid_tmdb_year_apply",exc)
                    try:
                        _tmdb_score=float(ameta.get("rating") or ameta.get("vote_average") or 0)
                    except Exception:
                        _tmdb_score=0.0
                    if 0.0 < _tmdb_score <= 10.0:
                        target_item["_tmdb_rating"]=_tmdb_score
                        try:
                            _rk=self._page_visual_key(target_item)
                            if _rk:self._visible_tmdb_rating_cache[_rk]=_tmdb_score
                        except Exception:pass
                    _raw_age=str(ameta.get("certification") or ameta.get("age_rating") or "").strip()
                    _raw_age_display=display_certification(_raw_age)
                    _age_checked=bool(_raw_age_display or (not _raw_age and ameta.get("_age_rating_checked")))
                    if _age_checked:
                        target_item["_age_rating_checked"]=True
                        if _raw_age:
                            target_item["certification"]=_raw_age
                            target_item["age_rating"]=_raw_age
                        else:
                            target_item.pop("certification",None);target_item.pop("age_rating",None)
                        self._age_cache_put(target_item,_raw_age,True)
                    elif _raw_age:
                        # Preserve unresolved provider vocabulary only as raw item
                        # metadata; it must not poison the RAM terminal cache.
                        target_item["certification"]=_raw_age;target_item["age_rating"]=_raw_age
                        target_item.pop("_age_rating_checked",None)
                    try:
                        _bd_local=str(ameta.get("backdrop_local") or "")
                        if _bd_local and os.path.isfile(_bd_local) and os.path.getsize(_bd_local)>1024:
                            target_item["_backdrop_source_local"]=_bd_local
                            self._page_visual_put(target_item,backdrop=_bd_local)
                    except Exception as exc:optional_failure("ui.grid_backdrop_ram_publish",exc)
                    try:self._card_rating_layout(target_item,matched_pos,self._card_meta(target_item,matched_pos))
                    except Exception as exc:optional_failure("ui.grid_tmdb_meta_apply_layout",exc)
                except Exception as exc:
                    optional_failure("ui.grid_tmdb_meta_apply",exc)
            if not apath or not os.path.isfile(str(apath)):
                continue
            if matched_pos is not None:
                try:
                    target_item=self.grid_items[matched_pos]
                    palette_source=str((ameta or {}).get("palette_source") or "") if isinstance(ameta,dict) else ""
                    if palette_source and os.path.isfile(palette_source):
                        self._grid_palette_sources[matched_pos]=palette_source
                    if not hasattr(self,"_grid_canonical_poster_slots"):
                        self._grid_canonical_poster_slots={}
                    self._grid_canonical_poster_slots[matched_pos]=str(apath)
                    self._grid_queue_decode(self._grid_generation,matched_pos,str(apath))
                    self._remember_grid_visual(
                        matched_pos,
                        display=str(apath),
                        poster=palette_source or str(apath),
                        palette=palette_source or str(apath),
                        provider_locked=bool(isinstance(ameta,dict) and ameta.get("provider_locked")),
                    )
                    applied+=1
                except Exception as exc:
                    optional_failure("ui.art_prefetch_apply",exc)
        for retry_pos in retry_positions[:1]:
            try:self._prefetch_visible_page_details(priority_index=retry_pos,max_items=1)
            except Exception as exc:optional_failure("ui.art_retry_submit",exc)
        return applied

    def _drain_jobs(self):
        # Remote-control movement gets absolute GUI priority. Do not decode or
        # apply posters before checking the navigation burst window.
        if time.monotonic() < float(getattr(self,"_nav_burst_until",0.0) or 0.0):
            return
        if not self._screen_closed and self.media_type in ("vod","series"):
            try:
                self._drain_visible_poster_results(1)
                self._grid_drain_art_jobs(limit=1)
            except Exception as exc:
                optional_failure("ui.poster_micro_drain",exc)
        AsyncScreenMixin._drain_jobs(self)
        if not self._screen_closed:
            self._grid_drain_art_jobs();self._grid_drain_adaptive_selector();self._grid_drain_page_mood();self._drain_grid_epg();self._drain_folder_artwork_progress();self._drain_poster_live_hud()
            while True:
                try:qgen,qkey,qvalue=self._quality_prefetch_jobs.get_nowait()
                except queue.Empty:break
                if qgen != int(getattr(self,"_page_prefetch_generation",0) or 0):continue
                for pos,item in enumerate(self.grid_items[:self.page_size]):
                    try:
                        from .persistent_cache import content_cache_key
                        ikey=content_cache_key(self.profile,self.media_type,item)
                    except Exception:
                        ikey=""
                    if ikey==qkey:
                        self._grid_item_state.setdefault(id(item),{})["quality"]=qvalue
                        try:self["item_meta%d"%pos].setText(self._card_meta(item,pos))
                        except Exception as exc:optional_failure("ui.silent_guard",exc)
                        if pos==self.index:self._update_header(item)
                        break
            # Keep the visual cascade progressive after navigation settles too.
            self._drain_visible_poster_results(2)
            pending=getattr(self,"_pending_page_request",None)
            if pending and not self._busy:
                self._pending_page_request=None
                self.load_page(pending[0], pending[1])

    def _remember_grid_state(self):
        self._restore_page=max(1,int(self.page or 1));self._restore_index=max(0,int(self.index or 0))
        # Search/Sort is a temporary view of this open folder. Preserve it while
        # Details is open, but do not poison the next fresh folder open with a
        # page number that belongs to filtered results.
        if not getattr(self,"_view_active",False):
            try:
                _state=dict(_GRID_NAV_STATE.get(self._session_nav_key,{}) or {})
                _state.update({"page":self._restore_page,"index":self._restore_index})
                _GRID_NAV_STATE[self._session_nav_key]=_state
            except Exception as exc:optional_failure("ui.grid_state",exc)
        return (self._restore_page,self._restore_index)

    _FOLDER_SORT_LABELS=("Default","Top Rated","Newest","Oldest","A-Z","Z-A")

    @staticmethod
    def _folder_search_norm(value):
        text=str(value or "").casefold()
        # Arabic-friendly normalization without changing what is displayed.
        text=re.sub(r"[\u064b-\u065f\u0670\u06d6-\u06ed]","",text)
        text=text.replace("\u0640","")
        text=re.sub(r"[\s._\-–—:/|]+"," ",text)
        return " ".join(text.split())

    @staticmethod
    def _folder_rating_value(item):
        """Return TMDb rating only for poster cards.

        Provider/IMDb ratings are intentionally excluded.  The poster score must
        match the canonical TMDb value used by Details.  `_tmdb_rating` is filled
        by the existing artwork/TMDb hydration lane and is therefore network-free
        on the GUI thread.
        """
        for value in (
            item.get("_tmdb_rating"),item.get("tmdb_rating"),item.get("vote_average"),
        ):
            match=re.search(r"\d+(?:\.\d+)?",str(value or ""))
            if match:
                try:
                    score=float(match.group(0))
                    if 0.0 < score <= 10.0:return score
                except Exception as exc:diagnostic_failure("ui.grid.tmdb_rating",exc)
        return -1.0

    @staticmethod
    def _folder_year_value(item):
        for value in (
            item.get("year"),item.get("released"),item.get("release_date"),
            item.get("releasedate"),item.get("first_air_date"),item.get("create_date"),
        ):
            match=re.search(r"(?:19|20)\d{2}",str(value or ""))
            if match:
                try:return int(match.group(0))
                except Exception as exc:diagnostic_failure("ui.grid.failsoft.353",exc)
        return 0

    def _display_item_title(self,item,default=""):
        item=item if isinstance(item,dict) else {}
        clean=bool((self._grid_settings or {}).get("clean_titles",True))
        raw=item.get("_raw_name") or item.get("name") or item.get("title") or item.get("original_name") or item.get("original_title") or default
        raw=str(raw or "")
        # Display-only rescue for provider route/catalogue codes such as ``SH``.
        # If a meaningful localized title is present on the same item/default,
        # prefer it for presentation without changing identity/cache fields.
        if re.fullmatch(r"[A-Za-z0-9+._-]{1,8}",raw.strip()):
            for alt in (item.get("_raw_name"),item.get("_provider_name"),item.get("original_name"),item.get("original_title"),default):
                alt=str(alt or "").strip()
                if len(alt)>=3 and re.search(r"[\u0600-\u06ff]",alt):
                    raw=alt;break
        if not clean:
            return raw.strip()
        try:
            if self.media_type=="itv":return _clean_live_channel_name(raw,True)
            if self.media_type in ("vod","series"):return _catalogue_title(raw)
            return premium_title(raw,True)
        except Exception:return raw.strip()

    def _folder_title_value(self,item):
        return self._display_item_title(item,"")

    def _folder_item_identity(self,item,index=0):
        for key in ("id","movie_id","series_id","ch_id","cmd","command","url"):
            value=item.get(key)
            if value not in (None,""):return "%s:%s"%(key,value)
        return "title:%s:%d"%(self._folder_search_norm(self._folder_title_value(item)),int(index))

    def _active_total(self):
        if self.media_type in ("vod","series") and self._view_active and isinstance(self._view_items,list):
            return len(self._view_items)
        return int(self._portal_total or 0)

    def _refresh_search_sort_controls(self):
        if self.media_type not in ("vod","series"):return
        label=_(self._FOLDER_SORT_LABELS[self._sort_mode % len(self._FOLDER_SORT_LABELS)])
        try:self["red"].setText(_("Search"))
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.376",exc)
        try:self["yellow"].setText(label)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.378",exc)
        try:
            blue_label=str(self["blue"].getText() or "Check Artwork")
            units=sum(1.55 if ord(ch)>0x2ff else (0.60 if ch in " ilI1|.,:'" else 1.0) for ch in blue_label)
            size=22 if units<=11 else (21 if units<=15 else (19 if units<=19 else 17))
            if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",size))
        except Exception as exc:optional_failure("ui.folder_cache_label_font",exc)
        # The yellow box geometry never changes. Only the font scales, always
        # centered horizontally/vertically by the existing skin.
        try:
            units=sum(1.55 if ord(ch)>0x2ff else (0.60 if ch in " ilI1|.,:'" else 1.0) for ch in label)
            size=22 if units<=10 else (21 if units<=14 else (19 if units<=18 else 17))
            if self["yellow"].instance is not None:self["yellow"].instance.setFont(gFont("Regular",size))
        except Exception as exc:optional_failure("ui.folder_sort_label_font",exc)
        try:
            base=(_("Movies") if self.media_type=="vod" else _("Series"))+"  /  "+self.category_title[:28]
            if self._search_query:
                base+="  •  "+_("Search")+": "+_clean_display_text(self._search_query,28)
            self["section"].setText(base)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.397",exc)

    def _load_folder_catalog_data(self,cancel_event=None):
        if self.media_type not in ("vod","series"):return []
        first=self._portal_page(1,cancel_event=cancel_event)
        native=max(1,int(self._portal_page_size or len(first) or self.page_size))
        total=max(0,int(self._portal_total or 0))
        max_pages=max(1,min(int((self._grid_settings or {}).get("search_max_pages",250) or 250),500))
        expected_pages=((total+native-1)//native) if total else max_pages
        expected_pages=max(1,min(expected_pages,max_pages))
        rows=[];seen=set();previous_signature=None
        for native_page in range(1,expected_pages+1):
            if cancel_event is not None and cancel_event.is_set():raise _ArtworkCancelled()
            page_rows=first if native_page==1 else self._portal_page(native_page,cancel_event=cancel_event)
            if not page_rows:break
            signature=tuple(self._folder_item_identity(row,pos) for pos,row in enumerate(page_rows[:4]))
            if native_page>1 and signature and signature==previous_signature:
                # Some portals ignore p= and repeat page one forever.
                break
            previous_signature=signature
            added=0
            for pos,row in enumerate(page_rows):
                if not isinstance(row,dict):continue
                # Xtream provider artwork is authoritative. Do not strip cover/backdrop
                # before folder caching/search; Stalker keeps the external-art policy.
                if row.get("_xtream"):
                    item=dict(row)
                else:
                    raw_provider_art="";raw_provider_backdrop="";raw_provider_backdrops=[]
                    try:raw_provider_art=str(_image_url(row) or "").strip()
                    except Exception:raw_provider_art=""
                    try:
                        from .ui_artwork_helpers import _backdrop_url as _manual_backdrop_url, _backdrop_candidates as _manual_backdrop_candidates
                        raw_provider_backdrop=str(_manual_backdrop_url(row) or "").strip()
                        raw_provider_backdrops=[str(x or "").strip() for x in (_manual_backdrop_candidates(row) or []) if str(x or "").strip()]
                    except Exception:raw_provider_backdrop="";raw_provider_backdrops=[]
                    item=_strip_portal_artwork(row)
                    # Preserve every real provider candidate privately for BLUE.
                    # Some Stalker portals expose a dead first screenshot followed
                    # by a valid CDN fanart URL; keeping only candidate #1 caused
                    # an avoidable permanent 'left N' after TMDB had its chance.
                    if raw_provider_art:item["_rescue_provider_art_url"]=raw_provider_art
                    if raw_provider_backdrop:item["_rescue_provider_backdrop_url"]=raw_provider_backdrop
                    if raw_provider_backdrops:item["_rescue_provider_backdrop_candidates"]=raw_provider_backdrops
                identity=self._folder_item_identity(item,len(rows)+pos)
                if identity in seen:continue
                seen.add(identity);rows.append(item);added+=1
                if total and len(rows)>=total:break
            if total and len(rows)>=total:break
            if not added:break
            if not total and len(page_rows)<native:break
        # Reject obvious provider-wide placeholder art from the explicit rescue
        # chain. A repeated list-level icon is not a real missing-title poster.
        try:
            rescue_counts={};backdrop_counts={}
            for item in rows:
                if not isinstance(item,dict):continue
                url=str(item.get("_rescue_provider_art_url") or "").strip()
                burl=str(item.get("_rescue_provider_backdrop_url") or "").strip()
                if url:rescue_counts[url]=rescue_counts.get(url,0)+1
                if burl:backdrop_counts[burl]=backdrop_counts.get(burl,0)+1
            generic_rescue={url for url,count in rescue_counts.items() if count>=3}
            generic_backdrop={url for url,count in backdrop_counts.items() if count>=3}
            if generic_rescue or generic_backdrop:
                for item in rows:
                    if not isinstance(item,dict):continue
                    if str(item.get("_rescue_provider_art_url") or "").strip() in generic_rescue:
                        item["_rescue_provider_art_generic"]=True
                    if str(item.get("_rescue_provider_backdrop_url") or "").strip() in generic_backdrop:
                        item["_rescue_provider_backdrop_generic"]=True
        except Exception as exc:optional_failure("ui.folder_rescue_generic_detect",exc)
        return rows

    def _ensure_folder_catalog(self,callback):
        if self.media_type not in ("vod","series"):return
        if isinstance(self._folder_catalog,list):
            callback(self._folder_catalog);return
        self._folder_catalog_waiters.append(callback)
        if self._folder_catalog_loading:return
        self._folder_catalog_loading=True
        self["status"].setText(_("Loading folder index..."))
        def ok(rows):
            self._folder_catalog_loading=False
            self._folder_catalog=[x for x in (rows or []) if isinstance(x,dict)]
            if not self._portal_total:self._portal_total=len(self._folder_catalog)
            waiters=list(self._folder_catalog_waiters);self._folder_catalog_waiters=[]
            for cb in waiters:
                try:cb(self._folder_catalog)
                except Exception as exc:optional_failure("ui.folder_catalog_waiter",exc)
        def failed(error):
            self._folder_catalog_loading=False
            self._folder_catalog_waiters=[]
            self["status"].setText(_friendly_error(error))
        self._run_async(lambda handle:self._load_folder_catalog_data(handle.cancel_event),ok,failed)

    def _build_folder_view(self):
        query=self._folder_search_norm(self._search_query)
        # R76: a live Search view may come from the same bounded provider-search
        # lane used by Home Search. Keep it independent from _folder_catalog so
        # clearing Search returns to normal native paging without a giant scan.
        if query and isinstance(getattr(self,"_folder_search_rows",None),list):
            rows=list(self._folder_search_rows or [])
        else:
            rows=list(self._folder_catalog or [])
        if query:
            filtered=[]
            for item in rows:
                haystack=" ".join(self._folder_search_norm(item.get(key)) for key in ("name","title","original_name","original_title"))
                if query in haystack:filtered.append(item)
            rows=filtered
        mode=self._sort_mode % len(self._FOLDER_SORT_LABELS)
        if mode==1:
            rows=sorted(rows,key=lambda item:(self._folder_rating_value(item)<0,-self._folder_rating_value(item),self._folder_search_norm(self._folder_title_value(item))))
        elif mode==2:
            rows=sorted(rows,key=lambda item:(self._folder_year_value(item)<=0,-self._folder_year_value(item),self._folder_search_norm(self._folder_title_value(item))))
        elif mode==3:
            rows=sorted(rows,key=lambda item:(self._folder_year_value(item)<=0,self._folder_year_value(item) if self._folder_year_value(item)>0 else 9999,self._folder_search_norm(self._folder_title_value(item))))
        elif mode==4:
            rows=sorted(rows,key=lambda item:self._folder_search_norm(self._folder_title_value(item)))
        elif mode==5:
            rows=sorted(rows,key=lambda item:self._folder_search_norm(self._folder_title_value(item)),reverse=True)
        self._view_items=rows
        self._view_active=True
        return rows

    def _load_folder_view_page(self,page,restore_index=None):
        self._ui_diag_page_request_mono=time.monotonic();self._ui_diag_first_poster_logged=False

        # Folder-view navigation is a real page transition too.  Reset the
        # previous page's poster-prefetch lifecycle exactly like load_page():
        # otherwise _page_prefetch_seen keeps the old card keys and revisiting a
        # page leaves provider-owned cards as permanent placeholders.
        try:
            old_cancel=getattr(self,"_page_prefetch_cancel",None)
            if old_cancel is not None:
                old_cancel.set()
        except Exception as exc:optional_failure("ui.folder_prefetch_cancel",exc)

        self._cancel_detached_page_prefetch_futures()
        for future in list(getattr(self,"_page_prefetch_futures",[]) or []):
            try:future.cancel()
            except Exception as exc:optional_failure("ui.folder_prefetch_future_cancel",exc)
        self._page_prefetch_futures=[]
        self._page_prefetch_cancel=threading.Event()
        try:self._page_prefetch_generation+=1
        except Exception:self._page_prefetch_generation=1
        try:
            with self._page_prefetch_lock:
                self._page_prefetch_seen.clear()
                self._visible_tmdb_pending.clear()
                self._page_quality_seen.clear()
                self._page_art_retry_count.clear()
        except Exception as exc:optional_failure("ui.folder_prefetch_reset",exc)

        rows=self._build_folder_view()
        total=len(rows);total_pages=max(1,(total+self.page_size-1)//self.page_size)
        target=max(1,min(int(page),total_pages))
        start=(target-1)*self.page_size
        valid=rows[start:start+self.page_size]
        if self.media_type in ("vod","series"):
            for _age_item in valid:
                try:self._age_cache_seed_item(_age_item)
                except Exception as exc:optional_failure("ui.age_cache_folder_first_paint",exc)
        self.page=target;self.grid_items=valid
        self._initial_grid_paint_done=True
        states=load_content_states(self.profile,self.media_type,self.grid_items) if self.grid_items else []
        qualities=load_content_qualities(self.profile,self.media_type,self.grid_items) if self.grid_items else []
        self._grid_item_state={}
        for item,state,quality in zip(self.grid_items,states,qualities):
            row=dict(state or {});row["quality"]=quality or "";self._grid_item_state[id(item)]=row
        self.index=max(0,min(int(restore_index or 0),len(valid)-1)) if valid else 0
        self._grid_full_chrome_warm=False
        # Folder views used to fall through to _grid_request_art() with no
        # prepared-art map. That forced up to 12 synchronous HDD snapshot /
        # visual-bundle / manifest lookups during the GUI paint itself.
        #
        # Paint the page immediately, then let the existing visible-poster
        # workers fill cards progressively. An explicit empty row is important:
        # _grid_request_art() sees the slot as prepared and never enters its
        # legacy synchronous cold fallback.
        self._prepared_art_for_render=[self._page_visual_get(item) or {} for item in valid]
        self._prepared_titles_for_render=[]
        self._prepared_fitted_for_render=[]
        self._prepared_meta_for_render=[]
        self._render_grid();self._remember_grid_state();self._refresh_search_sort_controls()
        try:_ui_diag("ui_page_ready",screen="grid",media_type=self.media_type,page=self.page,items=len(self.grid_items),
                     folder_view=True,elapsed_ms=int(max(0.0,(time.monotonic()-self._ui_diag_page_request_mono)*1000.0)))
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.496",exc)
        self._update_page_counter()
        if valid:
            self["status"].setText("")
            # Test69 Lean: search/sort views are event-driven too. No idle HDD
            # scanner or neighbour warming is started after first paint.
        else:
            try:self["selection"].hide()
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.507",exc)
            try:self["title"].setText(self.category_title);self["rating"].setText("");self["meta1"].setText("");self["meta2"].setText("")
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.509",exc)
            self["status"].setText(_("No matching content"))

    def _apply_folder_artwork_result(self,result):
        if getattr(self,"_folder_artwork_ui_finalized",False):return
        self._folder_artwork_ui_finalized=True
        self._folder_artwork_cache_running=False
        result=result or {}
        try:
            self["blue"].setText(str(getattr(self,"_folder_artwork_idle_label","") or _("Check Artwork")));self._refresh_search_sort_controls()
        except Exception as exc:optional_failure("ui.check_artwork_controls",exc)
        if result.get("backdrop_page_cache"):
            total=int(result.get("total",0) or 0);done=int(result.get("done",0) or 0)
            cached=int(result.get("cached",0) or 0);downloaded=int(result.get("downloaded",0) or 0);failed=int(result.get("failed",0) or 0)
            try:
                label=_("Backdrop cache incomplete") if (result.get("incomplete") or done<total) else _("Backdrop cache complete")
                self["status"].setText(_("%s • %d/%d • new %d • already %d • failed %d")%(label,done,total,downloaded,cached,failed))
            except Exception:pass
            try:self._page_prefetch_seen.clear()
            except Exception:pass
            return
        total=int(result.get("total",0) or 0)
        pm=int(result.get("poster_missing",0) or 0);bm=int(result.get("backdrop_missing",0) or 0)
        pr=int(result.get("poster_recovered",0) or 0);br=int(result.get("backdrop_recovered",0) or 0)
        pf=int(result.get("poster_failed",0) or 0);bf=int(result.get("backdrop_failed",0) or 0)
        bu=int(result.get("backdrop_upgraded",0) or 0)
        if not pm and not bm:
            # release: report only physically verified poster/backdrop components.
            # "fully cached" was misleading when Details later rejected a
            # canonical identity handoff and therefore could not present it.
            text=_("Artwork check • posters %d/%d • backdrops %d/%d • 0 missing")%(total,total,total,total)
            self["status"].setText(text)
        else:
            self["status"].setText(_("Artwork cache • posters %d/%d (left %d) • backdrops %d/%d (left %d)")%(pr,pm,pf,br,bm,bf))
        try:
            pt=int(result.get("presentation_total",0) or 0);prdy=int(result.get("presentation_ready",0) or 0)
            if pt:
                left=max(0,pt-prdy)
                suffix=_(" • display-ready %d/%d")%(prdy,pt)
                self["status"].setText(str(self["status"].getText() or "")+suffix)
        except Exception as exc:optional_failure("ui.check_artwork_display_ready_status",exc)
        self._page_prefetch_seen.clear()
        try:self._poll_visible_poster_cache()
        except Exception as exc:optional_failure("ui.check_artwork_repaint",exc)

    def _drain_folder_artwork_progress(self):
        while True:
            try:event=self._folder_artwork_progress.get_nowait()
            except queue.Empty:break
            except Exception:break
            if isinstance(event,tuple) and len(event)==2 and event[0]=="__complete__":
                try:self._apply_folder_artwork_result(event[1])
                except Exception as exc:optional_failure("ui.check_artwork_terminal",exc)
                continue
            if isinstance(event,tuple) and len(event)==2 and event[0]=="__page_backdrop_progress__":
                info=event[1] if isinstance(event[1],dict) else {}
                done=int(info.get("done",0) or 0);total=int(info.get("total",0) or 0)
                cached=int(info.get("cached",0) or 0);downloaded=int(info.get("downloaded",0) or 0);failed=int(info.get("failed",0) or 0)
                self._folder_artwork_cache_done=done;self._folder_artwork_cache_total=total
                if self._folder_artwork_cache_running and total:
                    try:self["status"].setText(_("Backdrop cache • %d/%d • new %d • already %d • failed %d")%(done,total,downloaded,cached,failed))
                    except Exception:pass
                    try:
                        self["blue"].setText("%d / %d"%(done,total))
                        if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",22))
                    except Exception:pass
                continue
            try:done,total=event
            except Exception:continue
            self._folder_artwork_cache_done=int(done or 0)
            self._folder_artwork_cache_total=int(total or 0)
            if self._folder_artwork_cache_running and total:
                try:self["status"].setText(_("Caching %d / %d")%(done,total))
                except Exception as exc:diagnostic_failure("ui.grid.failsoft.521",exc)
                try:
                    self["blue"].setText("%d / %d"%(done,total))
                    if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",22))
                except Exception as exc:diagnostic_failure("ui.grid.failsoft.525",exc)

    def _blue_resume_path(self):
        try:
            from .persistent_cache import LOOKUP,ensure_persistent_dirs
            raw="%s|%s|%s"%(str(self.profile.get("portal") or "").rstrip("/").lower(),str(self.media_type),str(self.genre))
            key=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
            return os.path.join(LOOKUP,"blue_resume_%s.json"%key)
        except Exception:return ""

    def _blue_item_sig(self,item):
        """Stable category identity; never hash the entire mutable provider dict."""
        row=item if isinstance(item,dict) else {}
        mt=str(self.media_type or "")
        tmdb=row.get("tmdb_id") or row.get("_locked_tmdb_id")
        if tmdb:return "tmdb:%s:%s"%(mt,str(tmdb))
        provider_id=row.get("stream_id") or row.get("series_id") or row.get("movie_id") or row.get("id") or row.get("ch_id")
        title=_catalogue_title(row.get("name") or row.get("title") or "").casefold().strip()
        year=str(row.get("year") or row.get("release_date") or row.get("first_air_date") or "")[:4]
        raw="%s|%s|%s|%s"%(mt,str(provider_id or ""),title,year)
        return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()

    def _blue_resume_done_path(self):
        path=self._blue_resume_path()
        return (path+".done") if path else ""

    def _load_blue_resume(self):
        path=self._blue_resume_path()
        if not path or not hdd_read_ready():return []
        try:
            with open(path,"r",encoding="utf-8") as h:row=json.load(h)
            pending=row.get("pending") if isinstance(row,dict) else []
            done=set()
            done_path=self._blue_resume_done_path()
            if done_path and os.path.isfile(done_path):
                with open(done_path,"r",encoding="utf-8") as h:
                    for line in h:
                        sig=line.strip()
                        if sig:done.add(sig)
            return [dict(x) for x in pending if isinstance(x,dict) and self._blue_item_sig(x) not in done]
        except Exception:return []

    def _save_blue_resume(self,pending):
        """Seed or clear Blue resume state without O(N²) list rewrites.

        The full pending list is written once. Each completion is appended as a
        tiny signature journal entry by _mark_blue_resume_done().
        """
        path=self._blue_resume_path()
        if not path:return False
        try:
            from .persistent_cache import ensure_persistent_dirs,persistent_write_gate
            if not ensure_persistent_dirs(os.path.dirname(path)):return False
            done_path=self._blue_resume_done_path()
            if not pending:
                for candidate in (path,done_path):
                    try:
                        if candidate and os.path.isfile(candidate) and persistent_write_gate(candidate):os.unlink(candidate)
                    except OSError:pass
                return True
            # Seed only once for this run. A current catalogue merge in
            # cache_folder_artwork refreshes stale resumes before this worker.
            temp=path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            if not persistent_write_gate(temp):return False
            with open(temp,"w",encoding="utf-8") as h:
                json.dump({"schema":56,"pending":pending,"updated_at":int(time.time())},h,ensure_ascii=False,separators=(",",":"))  # resumable cache, safe to rebuild
            if not persistent_write_gate(path):return False
            os.replace(temp,path)
            try:
                if done_path and os.path.isfile(done_path) and persistent_write_gate(done_path):os.unlink(done_path)
            except OSError:pass
            return True
        except Exception as exc:optional_failure("ui.blue_resume_save",exc);return False

    def _mark_blue_resume_done(self,item):
        path=self._blue_resume_done_path();sig=self._blue_item_sig(item)
        if not path or not sig:return False
        try:
            from .persistent_cache import ensure_persistent_dirs,persistent_write_gate
            if not ensure_persistent_dirs(os.path.dirname(path)) or not persistent_write_gate(path):return False
            with open(path,"a",encoding="ascii") as h:h.write(sig+"\n")  # completion journal is rebuildable cache state
            return True
        except Exception as exc:optional_failure("ui.blue_resume_done",exc);return False

    def _cache_folder_artwork_worker(self, rows, cancel_event=None):
        # Test110: one artwork engine only. Settings/maintenance uses the exact
        # same TMDB-first, missing-only rescue path as BLUE.
        return self._check_missing_posters_worker(rows,cancel_event)

    def _check_missing_posters_worker(self, rows, cancel_event=None):
        """TMDB-first two-pass artwork audit.

        Pass 1: TMDB gets first chance to fill highest-quality masters.
        Pass 2: provider/Xtream fills any component still physically missing.
        Existing TMDB files are immutable and provider rescue never overwrites them.
        """
        cfg=self._grid_settings or {};profile=self.profile;media_type=self.media_type
        credential=str(cfg.get("tmdb_credential") or "").strip()
        tmdb_available=bool(cfg.get("tmdb_enabled",True) and credential)
        language=cfg.get("tmdb_language","ar-EG");timeout=min(9,max(4,int(cfg.get("timeout",10) or 10)))
        rows=[dict(x) for x in (rows or []) if isinstance(x,dict)]
        try:
            from .persistent_cache import LOGS as _BLUE_LOG_DIR
            trace_path=os.path.join(_BLUE_LOG_DIR,"blue_artwork_trace.log")
        except Exception:
            trace_path="/tmp/UltraStalker_blue_artwork_trace.log"
        def blue_trace(message):
            try:
                parent=os.path.dirname(trace_path)
                if parent and not os.path.isdir(parent):os.makedirs(parent,exist_ok=True)
                with open(trace_path,"a",encoding="utf-8") as fh:
                    fh.write("%s %s\n"%(time.strftime("%Y-%m-%d %H:%M:%S"),str(message or "")))
            except Exception:
                pass
        try:
            parent=os.path.dirname(trace_path)
            if parent and not os.path.isdir(parent):os.makedirs(parent,exist_ok=True)
            with open(trace_path,"w",encoding="utf-8") as fh:
                fh.write("Ultra Stalker BLUE artwork trace FIRST VALID ARTWORK WINS\n")
        except Exception:
            pass
        blue_trace("START media=%s rows=%d tmdb_available=%s genre=%r"%(media_type,len(rows),tmdb_available,str(getattr(self,"genre","") or "")))
        def cancelled():return bool(cancel_event is not None and cancel_event.is_set())
        def valid_file(path,min_size=512):
            try:return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path))>min_size)
            except Exception:return False
        def backdrop_truth(path):
            # release: a backdrop is cached only when the actual HDD file passes
            # the same landscape/dimension validation used by ArtworkV2. A JSON
            # pointer or any existing JPEG is not enough. This is the single truth
            # used by BLUE for preflight, commit verification and final counting.
            try:
                from .artwork_v2 import _valid_backdrop
                return bool(_valid_backdrop(str(path or "")))
            except Exception:
                return False
        def state(item):
            try:data=load_artwork_v2_manifest(profile,media_type,item) or {}
            except Exception:data={}
            poster=str(data.get("poster_local") or "")
            backdrop=str(data.get("backdrop_local") or "")
            return data,poster,(backdrop if backdrop_truth(backdrop) else "")
        def tmdb_master_state(data):
            """Return canonical TMDB component readiness, ignoring BLUE/provider fallbacks."""
            try:
                tid=(data or {}).get("tmdb_id")
                if not tid:return None,None
                paths=canonical_art_paths((data or {}).get("media_type") or media_type,tid) or {}
                return valid_file(paths.get("poster")),backdrop_truth(paths.get("backdrop"))
            except Exception:return None,None
        def _uniq_urls(values):
            out=[]
            for value in values or []:
                text=str(value or "").strip()
                if text and text not in out:out.append(text)
            return out
        provider_enriched={}
        def provider_candidates(item):
            # BLUE/provider rescue must inspect every provider artwork field, not
            # just _image_url()'s first hit.  Some Stalker rows put a generic
            # placeholder in screenshot_uri/movie_image while the exact cover is
            # present in poster/cover (or only in the richer search row).  Using
            # the first field made a real provider poster effectively invisible.
            posters=[];backs=[]
            if not item.get("_rescue_provider_art_generic"):
                posters.extend((item.get("_rescue_provider_art_url"),item.get("_visible_provider_art_url")))
            if not item.get("_rescue_provider_backdrop_generic"):
                backs.extend(item.get("_rescue_provider_backdrop_candidates") or [])
                backs.extend((item.get("_rescue_provider_backdrop_url"),item.get("_visible_provider_backdrop_url")))
            for key in ("poster","poster_url","cover","cover_url","movie_image","image","img","hd","pic","screenshot_uri","screenshot","stream_icon","logo","logo_url","picon"):
                value=item.get(key)
                values=value if isinstance(value,(list,tuple)) else [value]
                for entry in values:
                    text=str(entry or "").strip()
                    low=text.casefold()
                    if not text or low in ("null","none","[]","{}") or any(tag in low for tag in ("placeholder","noimage","no_image","default_poster","default-cover")):
                        continue
                    posters.append(text)
            try:
                from .ui_artwork_helpers import _backdrop_candidates as _manual_backdrop_candidates
                backs.extend(_manual_backdrop_candidates(item) or [])
            except Exception:pass
            return _uniq_urls(posters),_uniq_urls(backs)
        def enrich_provider(item,need_p,need_b):
            # Provider search is intentionally disabled here. Pass 2 may only
            # reuse artwork already present on the catalogue row.
            return dict(item)
        try:
            from .artwork_v2 import save_manual_rescue_art
        except Exception:
            save_manual_rescue_art=None

        def adopt_existing_hdd(item):
            """Promote already-cached local artwork into the shared master store.

            Old builds can have perfectly usable poster/backdrop files referenced by
            detail snapshots/visual bundles but not yet represented in artwork_v2.
            BLUE must treat those HDD files as hits instead of re-downloading them.
            """
            if not callable(save_manual_rescue_art):
                return False,False
            try:
                data,p,b=state(item)
                need_p=not valid_file(p);need_b=not valid_file(b)
                if not (need_p or need_b):
                    return False,False
                snap=(load_shared_detail_snapshot(self.profile,self.media_type,item)
                      if isinstance(item,dict) and item.get("_xtream")
                      else load_detail_snapshot(self.profile,self.media_type,item))
                bundle=_load_visual_bundle(self.profile,self.media_type,item,snap) or {}
                local_p=str(bundle.get("poster") or "")
                local_b=str(bundle.get("backdrop") or "")
                use_p=local_p if need_p and valid_file(local_p) else None
                use_b=local_b if need_b and backdrop_truth(local_b) else None
                if not (use_p or use_b):
                    return False,False
                saved=save_manual_rescue_art(profile,media_type,item,poster=use_p,backdrop=use_b,source="existing_hdd_adopt") or {}
                got_p=bool(use_p and valid_file(saved.get("poster_local")))
                got_b=bool(use_b and backdrop_truth(saved.get("backdrop_local")))
                blue_trace("HDD_ADOPT title=%r poster=%s backdrop=%s"%(str(item.get("name") or item.get("title") or "")[:120],got_p,got_b))
                return got_p,got_b
            except Exception as exc:
                optional_failure("ui.artwork_existing_hdd_adopt",exc)
                return False,False

        # R171: release BLUE is strictly missing-only.  The old forced title
        # diagnostic performed TMDB/provider probes before preflight even when every
        # asset was already hot.  Diagnostics do not belong in the user cache lane.

        # Preflight: immutable existing masters never enter any worker. First
        # adopt artwork that older builds already cached on HDD but never indexed.
        # Keep the initial physical state so the final UI counter can report TMDB
        # recoveries too (older code counted provider rescue only).
        initial_state={}
        missing=[];hot=0
        clean_upgrade_candidates=0;clean_backdrop_upgraded=0
        # release unified BLUE pass. The visible counter belongs to TITLES, not
        # internal stages. A cached title is checked/finalised once here; a title
        # missing artwork is finalised once after its artwork rescue below. This
        # prevents the old 1..N artwork pass followed by another 1..N Cinematic
        # pass that looked like the cache had restarted from zero.
        blue_done=0
        blue_total=len(rows)
        try:self._folder_artwork_progress.put((0,blue_total))
        except Exception:pass

        def _blue_finish_title(item):
            """Finish one title inside the same BLUE lifecycle.

            Poster/backdrop are the hard prerequisites. Optional screen-specific
            presentation hooks (Cinematic final backdrop/adaptive/details) are
            executed here, before this title advances the single folder counter.
            """
            if cancelled():raise _ArtworkCancelled()
            data,p,b=state(item)
            if not (valid_file(p) and backdrop_truth(b)):
                return False
            ready_hook=getattr(self,"_folder_artwork_ready_hook",None)
            if callable(ready_hook):
                try:
                    if ready_hook(item,data):
                        return True
                except Exception as exc:
                    optional_failure("ui.artwork_blue_ready_hook",exc)
            finalize_hook=getattr(self,"_folder_artwork_finalize_hook",None)
            if callable(finalize_hook):
                try:
                    return bool(finalize_hook(item,p,b,data,data,cancel_event))
                except Exception as exc:
                    optional_failure("ui.artwork_blue_finalize_hook",exc)
                    return False
            return True

        for item in rows:
            try:
                _d0,_p0,_b0=state(item)
                initial_state[self._page_visual_key(item)]=(not valid_file(_p0),not valid_file(_b0))
            except Exception:
                initial_state[self._page_visual_key(item)]=(True,True)
            if cancelled():raise _ArtworkCancelled()
            adopt_existing_hdd(item)
            data,p,b=state(item)

            # R92 clean-backdrop maintenance is a separate, targeted lane inside
            # the explicit BLUE worker. Existing poster/backdrop readiness no
            # longer suppresses the upgrade. It uses only the verified TMDb id,
            # never title search, and leaves the old backdrop in place on error.
            # R171 missing-only contract: a physically valid backdrop is already hot.
            # BLUE never spends network/CPU replacing valid existing artwork merely
            # because an older selector version produced it. Explicit cache fills
            # missing components; it does not refresh finished titles from scratch.

            # release persistent BLUE contract: a screen-specific completed vault
            # is authoritative before any TMDB/provider canonical-master retry.
            # Once Cinematic has successfully persisted poster/backdrop/final
            # backdrop/adaptive/details for a title, pressing BLUE again must be
            # a read-only verification. It must never invalidate, rewrite, touch
            # timestamps, resolve identity or re-download that title merely
            # because the canonical TMDB master path differs from the trusted HDD
            # vault. Missing/corrupt physical files still fall through to repair.
            ready_hook=getattr(self,"_folder_artwork_ready_hook",None)
            persistent_ready=False
            if callable(ready_hook):
                try:persistent_ready=bool(ready_hook(item,data))
                except Exception as exc:optional_failure("ui.artwork_blue_persistent_ready",exc)
            if persistent_ready:
                hot+=1
                blue_done+=1
                try:self._folder_artwork_progress.put((blue_done,blue_total))
                except Exception:pass
                continue

            cp,cb=tmdb_master_state(data)
            # If this title has never completed the persistent screen vault, BLUE
            # may still upgrade/repair canonical masters as before.
            needs_art=False
            if cp is not None:
                if cp and cb:hot+=1
                else:needs_art=True
            elif valid_file(p) and backdrop_truth(b):
                hot+=1
            else:
                needs_art=True
            if needs_art:
                missing.append(item)
            else:
                _blue_finish_title(item)
                blue_done+=1
                try:self._folder_artwork_progress.put((blue_done,blue_total))
                except Exception:pass
        # PERFLAB8: process every missing title end-to-end before moving to the
        # next one.  The old two-pass worker resolved TMDB for the entire folder
        # first, then provider rescue, then finalisation.  When the first visible
        # page (typically 14 titles) was already cached, the BLUE counter sat at
        # 14 for minutes while hundreds of missing titles were silently processed.
        # Keep the same TMDB-first/provider-second policy, but make progress
        # title-granular so Cache Artwork never looks frozen and each completed
        # title is durable before the next one starts.
        resolver=ArtworkV2(credential,language,timeout) if tmdb_available else None
        if resolver is not None:
            try: resolver.trace_cb=blue_trace
            except Exception: pass
        poster_recovered=0;backdrop_recovered=0
        for item in missing:
            if cancelled():raise _ArtworkCancelled()
            data={}
            resolve_item=dict(item)
            # Xtream info may contribute identity/metadata only.  Provider art
            # itself still waits until TMDB has had first refusal for this title.
            if item.get("_xtream") and hasattr(self.client,"enrich_provider_artwork"):
                try:
                    try: rich=self.client.enrich_provider_artwork(dict(item),media_type,cancel_event,force=True) or {}
                    except TypeError: rich=self.client.enrich_provider_artwork(dict(item),media_type,cancel_event) or {}
                    identity_keys=("tmdb_id","tmdb","imdb_id","imdb","year","releaseDate","release_date","releasedate",
                                   "_provider_name","original_name","original_title","o_name","english_name","english_title")
                    for key in identity_keys:
                        value=rich.get(key) if isinstance(rich,dict) else None
                        if value not in (None,"",[],{}): resolve_item[key]=value
                    blue_trace("IDENTITY_INFO title=%r tmdb_id=%r imdb_id=%r year=%r"%(
                        str(item.get("name") or item.get("title") or "")[:120],resolve_item.get("tmdb_id") or resolve_item.get("tmdb"),
                        resolve_item.get("imdb_id") or resolve_item.get("imdb"),resolve_item.get("year")))
                except Exception as exc:
                    optional_failure("ui.artwork_identity_xtream_info",exc)
                    blue_trace("IDENTITY_INFO_ERROR title=%r error=%r"%(str(item.get("name") or item.get("title") or "")[:120],str(exc)[:140]))

            if resolver is not None:
                _blue_fast_art_only=bool(getattr(self,"_folder_artwork_fast_art_only",False))
                # Preserve the release-only Generation War identity diagnostics.
                # These probes never alter the selected identity or artwork; they
                # are trace-only and intentionally remain unchanged in PERFLAB8.
                try:
                    _focus_title=str(item.get("name") or item.get("title") or "").strip()
                    if "generation war" in _focus_title.casefold():
                        _id_fields=("name","title","_raw_name","_provider_name","original_name","original_title","o_name","english_name","english_title","tmdb_id","tmdb","imdb_id","imdb","year","release_date","first_air_date")
                        _parts=[]
                        for _k in _id_fields:
                            _v=resolve_item.get(_k)
                            if _v not in (None,"",[],{}): _parts.append("%s=%r"%(_k,str(_v)[:160]))
                        blue_trace("IDENTITY_FOCUS_INPUT "+" ".join(_parts))
                        _direct=str(resolve_item.get("tmdb_id") or resolve_item.get("tmdb") or "").strip()
                        if _direct.isdigit():
                            try:
                                _det=resolver.tmdb._get("/tv/%s"%_direct,{"language":"en-US"}) or {}
                                blue_trace("IDENTITY_FOCUS_DIRECT id=%s name=%r original=%r year=%r"%(_direct,str(_det.get("name") or "")[:120],str(_det.get("original_name") or "")[:120],str(_det.get("first_air_date") or "")[:10]))
                            except Exception as _exc:
                                blue_trace("IDENTITY_FOCUS_DIRECT_ERROR id=%s error=%r"%(_direct,str(_exc)[:160]))
                        _queries=[]
                        for _k in ("name","title","_raw_name","_provider_name","original_name","original_title","o_name","english_name","english_title"):
                            _q=str(resolve_item.get(_k) or "").strip()
                            if _q and _q not in _queries: _queries.append(_q)
                        _yr=str(resolve_item.get("year") or resolve_item.get("first_air_date") or resolve_item.get("release_date") or "")[:4]
                        for _q in _queries[:8]:
                            for _lang in (language,"en-US"):
                                try:
                                    _params={"query":_q,"language":_lang,"include_adult":"false"}
                                    if _yr.isdigit(): _params["first_air_date_year"]=_yr
                                    _pay=resolver.tmdb._get("/search/tv",_params) or {}
                                    _rs=[]
                                    for _r in (_pay.get("results") or [])[:3]:
                                        _rs.append("%s:%s|%s|%s"%(str(_r.get("id") or ""),str(_r.get("name") or "")[:70],str(_r.get("original_name") or "")[:70],str(_r.get("first_air_date") or "")[:10]))
                                    blue_trace("IDENTITY_FOCUS_SEARCH q=%r lang=%s year=%r top=%r"%(_q,_lang,_yr,_rs))
                                except Exception as _exc:
                                    blue_trace("IDENTITY_FOCUS_SEARCH_ERROR q=%r lang=%s error=%r"%(_q,_lang,str(_exc)[:160]))
                except Exception as _exc:
                    blue_trace("IDENTITY_FOCUS_TRACE_ERROR error=%r"%str(_exc)[:180])
                try:
                    data=resolver.resolve(profile,media_type,resolve_item,full=(not _blue_fast_art_only),cancel_event=cancel_event) or {}
                    if _blue_fast_art_only and not cancelled():
                        # BLUE remains poster/backdrop focused.  If full=False left
                        # the landscape master absent, fetch that exact component
                        # without hydrating credits/cast profiles.
                        try:
                            _fresh,_fp,_fb=state(item)
                            if not backdrop_truth(_fb):
                                # Issue 4: BLUE already has a verified identity from
                                # resolve().  Hand that exact id to the backdrop-only
                                # lane instead of making a difficult/translated title
                                # prove itself through search again after its HDD
                                # backdrop bytes were cleaned or moved.
                                _backdrop_item=dict(resolve_item)
                                _verified_id=str((data or {}).get("tmdb_id") or "").strip()
                                if _verified_id.isdigit():
                                    _backdrop_item["_locked_tmdb_id"]=_verified_id
                                    _backdrop_item["_locked_tmdb_type"]=(data or {}).get("media_type") or media_type
                                _back=resolver.resolve_backdrop_only(profile,media_type,_backdrop_item,cancel_event=cancel_event) or {}
                                if isinstance(_back,dict) and _back:
                                    merged=dict(data);merged.update(_back);data=merged
                        except Exception as _exc:
                            optional_failure("ui.artwork_blue_backdrop_only",_exc)
                except Exception as exc:
                    optional_failure("ui.artwork_tmdb_master",exc);data={}

            # resolve() normally persists the canonical files; re-commit the
            # lightweight per-row pointer when identity was matched.
            if isinstance(data,dict) and data.get("matched") and data.get("tmdb_id"):
                try:
                    from .artwork_v2 import save_manifest as _save_artwork_v2_manifest
                    _save_artwork_v2_manifest(profile,media_type,item,data)
                except Exception as exc: optional_failure("ui.artwork_tmdb_pointer_commit",exc)
            try:
                title=str(item.get("name") or item.get("title") or "")[:120]
                _fresh,_fp,_fb=state(item)
                blue_trace("TMDB title=%r matched=%s tmdb_id=%r poster=%s backdrop=%s committed_poster=%s committed_backdrop=%s error=%r"%(title,bool(data.get("matched")),data.get("tmdb_id"),bool(valid_file(data.get("poster_local"))),bool(valid_file(data.get("backdrop_local"))),bool(valid_file(_fp)),bool(backdrop_truth(_fb)),str(data.get("error") or "")[:100]))
            except Exception:pass

            # Provider fallback is evaluated immediately for this title, only for
            # components that are still physically missing after TMDB/Fanart.
            _state_data,_state_p,_state_b=state(item)
            need_p=not valid_file(_state_p);need_b=not backdrop_truth(_state_b)
            if need_p or need_b:
                enriched=enrich_provider(item,need_p,need_b)
                purls,burls=provider_candidates(enriched)
                try:
                    title=str(item.get("name") or item.get("title") or "")[:120]
                    blue_trace("PROVIDER title=%r need_p=%s need_b=%s p_candidates=%d b_candidates=%d xtream=%s"%(title,need_p,need_b,len(purls),len(burls),bool(item.get("_xtream"))))
                except Exception:pass
                provider_p="";provider_b=""
                if need_p:
                    for purl in purls:
                        if cancelled():raise _ArtworkCancelled()
                        try:provider_p=_download_portal_artwork(purl,profile,self.client,False,6.0,cancel_event,item=enriched,trace_cb=blue_trace,force_attempt=True) or ""
                        except Exception as exc:optional_failure("ui.artwork_provider_poster_rescue",exc);provider_p=""
                        if valid_file(provider_p):break
                    try:blue_trace("PROVIDER_POSTER title=%r success=%s tried=%d"%(str(item.get("name") or item.get("title") or "")[:120],bool(valid_file(provider_p)),len(purls)))
                    except Exception:pass
                if need_b:
                    for burl in burls:
                        if cancelled():raise _ArtworkCancelled()
                        try:provider_b=_download_portal_artwork(burl,profile,self.client,True,6.5,cancel_event,item=enriched,trace_cb=blue_trace,force_attempt=True) or ""
                        except Exception as exc:optional_failure("ui.artwork_provider_backdrop_rescue",exc);provider_b=""
                        if valid_file(provider_b):break
                    try:blue_trace("PROVIDER_BACKDROP title=%r success=%s tried=%d"%(str(item.get("name") or item.get("title") or "")[:120],bool(valid_file(provider_b)),len(burls)))
                    except Exception:pass
                if callable(save_manual_rescue_art) and (valid_file(provider_p) or valid_file(provider_b)):
                    try:
                        saved=save_manual_rescue_art(profile,media_type,item,
                            poster=(provider_p if need_p and valid_file(provider_p) else None),
                            backdrop=(provider_b if need_b and valid_file(provider_b) else None),
                            source="provider_missing_only") or {}
                        if need_p and valid_file(saved.get("poster_local")):poster_recovered+=1
                        if need_b and valid_file(saved.get("backdrop_local")):backdrop_recovered+=1
                        try:blue_trace("SAVE title=%r poster_saved=%s backdrop_saved=%s"%(str(item.get("name") or item.get("title") or "")[:120],bool(valid_file(saved.get("poster_local"))),bool(valid_file(saved.get("backdrop_local")))))
                        except Exception:pass
                        # The provider downloader is transport only.  Once copied
                        # into the canonical/fallback master, discard the URL-hash
                        # transport file so one component has one persistent copy.
                        for source,target in ((provider_p,saved.get("poster_local")),(provider_b,saved.get("backdrop_local"))):
                            try:
                                source=str(source or "");target=str(target or "")
                                base=os.path.basename(source)
                                if source and target and source!=target and os.path.isfile(source) and not (base.startswith("movie_") or base.startswith("tv_") or base.startswith("fallback_")):
                                    os.unlink(source)
                            except Exception:pass
                    except Exception as exc:optional_failure("ui.artwork_provider_rescue_save",exc)

            # Finish the screen-specific readiness for this title now, then move
            # the one user-facing counter exactly once.  No hidden folder-wide
            # second pass means 14/N can no longer sit frozen while work continues.
            try:_blue_finish_title(item)
            except _ArtworkCancelled:raise
            except Exception as exc:optional_failure("ui.artwork_blue_finish_missing",exc)
            blue_done+=1
            try:self._folder_artwork_progress.put((blue_done,blue_total))
            except Exception:pass
            try:blue_trace("ITEM_DONE %d/%d title=%r"%(blue_done,blue_total,str(item.get("name") or item.get("title") or "")[:120]))
            except Exception:pass

        # Final count from the single shared store; no source is allowed to replace a hit.
        # Count *all* physical recoveries, including TMDB/Fanart, not just provider
        # fallback. This makes the BLUE status agree with what the user can see.
        complete=0;still_p=0;still_b=0;all_p_recovered=0;all_b_recovered=0
        for item in rows:
            data,p,b=state(item)
            p_ok=valid_file(p);b_ok=backdrop_truth(b)
            was_p_missing,was_b_missing=initial_state.get(self._page_visual_key(item),(True,True))
            if was_p_missing and p_ok:all_p_recovered+=1
            if was_b_missing and b_ok:all_b_recovered+=1
            if p_ok and b_ok:
                complete+=1
            else:
                if not p_ok:still_p+=1
                if not b_ok:still_b+=1
        poster_recovered=all_p_recovered
        backdrop_recovered=all_b_recovered
        poster_target=poster_recovered+still_p
        backdrop_target=backdrop_recovered+still_b
        result={"total":len(rows),"done":complete,"work_total":len(missing),"downloaded":poster_recovered+backdrop_recovered,"hot":hot,"failed":still_p+still_b,"poster_missing":poster_target,"backdrop_missing":backdrop_target,"poster_recovered":poster_recovered,"backdrop_recovered":backdrop_recovered,"poster_failed":still_p,"backdrop_failed":still_b,"backdrop_upgrade_candidates":clean_upgrade_candidates,"backdrop_upgraded":clean_backdrop_upgraded,"recovered":poster_recovered}
        try:blue_trace("FINAL total=%d poster_recovered=%d poster_left=%d backdrop_recovered=%d backdrop_left=%d clean_candidates=%d clean_upgraded=%d"%(len(rows),poster_recovered,still_p,backdrop_recovered,still_b,clean_upgrade_candidates,clean_backdrop_upgraded))
        except Exception:pass
        return result

    def _cache_backdrop_category_rows(self, cancel_event=None):
        """Materialize the complete current category before BLUE starts artwork work.

        The provider may paginate its API internally, but Cache Artwork itself has
        no 50-title work limit and no batch boundary.  This deliberately builds one
        authoritative category list first so a later provider-page hiccup cannot
        terminate a partially completed artwork run after the first native page.
        """
        # Search/sort/folder views already own the authoritative list in RAM.
        if bool(getattr(self,"_view_active",False)):
            rows=list(getattr(self,"_view_items",[]) or [])
            if not rows:
                try:rows=list(self._build_folder_view() or [])
                except Exception:rows=[]
            return [dict(row) for row in rows if isinstance(row,dict)]

        # A previously built folder catalogue is equally authoritative.  Reuse it
        # only when it actually covers the provider-advertised category total.
        target=max(0,int(self._cache_backdrop_category_total() or 0))
        existing=getattr(self,"_folder_catalog",None)
        if isinstance(existing,list) and existing and (not target or len(existing)>=target):
            return [dict(row) for row in existing if isinstance(row,dict)][:target or None]

        rows=[];seen=set();previous_signature=None
        first=[]
        try:first=self._portal_page(1,cancel_event=cancel_event) or []
        except Exception as exc:
            optional_failure("ui.blue_category_first_page",exc)
            first=[]
        if not first:return rows

        native=max(1,int(getattr(self,"_portal_page_size",0) or len(first) or self.page_size or 1))
        target=max(target,max(0,int(getattr(self,"_portal_total",0) or 0)))
        expected_pages=((target+native-1)//native) if target else 500
        expected_pages=max(1,min(int(expected_pages or 1),500))

        def fetch_page(page):
            if page==1:return list(first)
            last_error=None
            for attempt in range(4):
                if cancel_event is not None and cancel_event.is_set():raise _ArtworkCancelled()
                try:
                    page_rows=self._portal_page(page,cancel_event=cancel_event) or []
                    if page_rows:return page_rows
                except Exception as exc:
                    last_error=exc
                    optional_failure("ui.blue_category_page",exc)
                # A cache/auth hiccup must not end the whole BLUE run.  Invalidate
                # only the provider catalogue cache generation, then retry the same
                # native page.  Normal artwork/cache state is untouched.
                try:
                    bump=getattr(self.client,"_bump_content_cache_generation",None)
                    if callable(bump):bump()
                except Exception:pass
                try:time.sleep(0.10*(attempt+1))
                except Exception:pass
            if last_error is not None:
                optional_failure("ui.blue_category_page_final",last_error)
            return []

        for native_page in range(1,expected_pages+1):
            if cancel_event is not None and cancel_event.is_set():raise _ArtworkCancelled()
            page_rows=fetch_page(native_page)
            if not page_rows:break

            # Detect a provider that truly repeats a page.  Use title/year as part
            # of the signature because several Stalker portals reuse generic ids.
            try:signature=tuple(self._blue_item_sig(row) for row in page_rows[:6] if isinstance(row,dict))
            except Exception:signature=()
            if native_page>1 and signature and signature==previous_signature:
                break
            previous_signature=signature

            added=0
            for row in page_rows:
                if cancel_event is not None and cancel_event.is_set():raise _ArtworkCancelled()
                if not isinstance(row,dict):continue
                sig=self._blue_item_sig(row)
                if sig in seen:continue
                seen.add(sig);rows.append(dict(row));added+=1
                if target and len(rows)>=target:break
            if target and len(rows)>=target:break
            if not added:break
            if not target and len(page_rows)<native:break

        return rows

    def _cache_backdrop_category_total(self):
        """Best GUI-safe total for BLUE Cache Artwork without network work."""
        if bool(getattr(self,"_view_active",False)):
            try:return len(list(getattr(self,"_view_items",[]) or []))
            except Exception:return 0
        try:return max(0,int(getattr(self,"_portal_total",0) or 0))
        except Exception:return 0

    def _page_backdrop_existing_q60(self, item, hot=None):
        """Return a verified existing Q60 for this item without downloading.

        This is the fast skip gate.  A title that normal browsing already placed
        in /backdrops must not be resolved/downloaded again by Cache Artwork.
        """
        row=item if isinstance(item,dict) else {};hot=hot if isinstance(hot,dict) else {}
        candidates=[]
        for src in (hot,row):
            for key in ("backdrop_local","backdrop_fast","backdrop_display"):
                value=str(src.get(key) or "").strip()
                if value:candidates.append(value)
        try:
            from .persistent_cache import BACKDROPS
            root=os.path.realpath(str(BACKDROPS or ""))
        except Exception:root=""
        for path in candidates:
            try:
                real=os.path.realpath(path)
                if root and (real==root or real.startswith(root+os.sep)) and _page_backdrop_q60_valid(real):return real
            except Exception:pass
        # A normal-browsing manifest commonly stores backdrop_path plus a w1280
        # URL, while the Q60 file is keyed by the ORIGINAL URL. Rebuild that exact
        # URL locally so BLUE can skip it without a TMDb request or conversion.
        for src in (hot,row):
            for key in ("backdrop_path","tmdb_backdrop_path","_backdrop_path",
                        "backdrop_url","_backdrop_url","tmdb_backdrop_url"):
                url=_page_backdrop_original_url(src.get(key))
                destination=_page_backdrop_q60_path(url)
                if destination and _page_backdrop_q60_valid(destination):return destination
        return ""

    def _cache_current_page_backdrops_worker(self, rows=None, cancel_event=None):
        """BLUE button: cache the complete current category in one uninterrupted run.

        The full category is enumerated first, then every valid local Q60 is counted
        and skipped before any TMDb work.  Only the remaining titles are resolved
        and cached sequentially.  There is no 50-title processing boundary.
        """
        cfg=dict(getattr(self,"_grid_settings",None) or load_settings() or {})
        credential=str(cfg.get("tmdb_credential") or "").strip()
        language=str(cfg.get("tmdb_language") or "ar-EG").strip() or "ar-EG"
        try:timeout=min(10,max(4,int(cfg.get("timeout",10) or 10)))
        except Exception:timeout=8

        supplied=[dict(x) for x in (rows or []) if isinstance(x,dict)] if rows is not None else None
        planned=len(supplied) if supplied is not None else self._cache_backdrop_category_total()
        done=0;cached=0;downloaded=0;failed=0;gallery=0;primary=0
        result={"backdrop_page_cache":True,"total":planned,"done":0,"cached":0,"downloaded":0,"failed":0,"gallery":0,"primary":0}
        if not (cfg.get("tmdb_enabled",True) and credential):
            result["failed"]=0;result["reason"]="tmdb_unavailable";result["incomplete"]=True;return result
        try:
            from .artwork_v2_impl import ArtworkV2 as _ArtworkV2Impl, save_manifest as _save_manifest
            from .tmdb import TMDBClient
            from .backdrop_clean_runtime import rank_clean_backdrops
            resolver=_ArtworkV2Impl(credential,language,timeout)
        except Exception as exc:
            optional_failure("ui.page_backdrop_cache_import",exc)
            result["failed"]=0;result["reason"]="resolver_unavailable";result["incomplete"]=True;return result

        def emit_progress(force=False):
            display_total=max(int(planned or 0),int(done or 0))
            try:self._folder_artwork_progress.put(("__page_backdrop_progress__",{"done":done,"total":display_total,"cached":cached,"downloaded":downloaded,"failed":failed}))
            except Exception:pass

        # Important: finish category enumeration before touching artwork.  This
        # prevents a provider-page error from turning a successful first chunk
        # into an apparent terminal 50-title run.
        try:
            catalogue=supplied if supplied is not None else self._cache_backdrop_category_rows(cancel_event)
        except _ArtworkCancelled:
            catalogue=[]
        except Exception as exc:
            optional_failure("ui.blue_category_enumeration",exc);catalogue=[]
        catalogue=[dict(x) for x in (catalogue or []) if isinstance(x,dict)]
        if not planned:planned=len(catalogue)
        if len(catalogue)>planned:planned=len(catalogue)
        result["total"]=planned

        # Pass 1: count every already-cached Q60 across the whole category first.
        # This is HDD/manifest-only and therefore happens before any TMDb request,
        # download or conversion.  Pending titles are kept for one-by-one work.
        pending=[]
        for item in catalogue:
            if cancel_event is not None and cancel_event.is_set():break
            try:hot=load_artwork_v2_manifest(self.profile,self.media_type,item) or {}
            except Exception:hot={}
            existing=self._page_backdrop_existing_q60(item,hot)
            if existing:
                cached+=1;done+=1
            else:
                pending.append((item,hot))
            # Keep the GUI alive without creating an enormous progress queue.
            if done and (done%8)==0:emit_progress()
        emit_progress(True)

        # Pass 2: process only what is missing, sequentially, until the category
        # is exhausted.  No worker batch, page batch, or 50-item stop exists here.
        #
        # R270 reliability follow-up: BLUE used to stop identity resolution after
        # _resolve_identity(). Details does not: it also runs the verified rescue
        # resolver, then performs a last-mile backdrop recovery.  That mismatch
        # made BLUE report a title as "failed" even though opening Details could
        # resolve the same TMDb title/backdrop a few seconds later.  Keep BLUE on
        # the same verified identity policy and only declare a real failure after
        # a second pass has also missed.
        def process_missing(item,hot):
            ok=False;choice="";iso="";tmdb_id=None;mt=""
            try:
                mt,tmdb_id,_confidence,_identity_source,_candidate=resolver._resolve_identity(self.media_type,item,hot)
                if not tmdb_id:
                    rmt,rid,_rconf,_rseed,rtrusted=resolver._resolve_poster_rescue(self.media_type,item)
                    if rid and rtrusted:
                        mt,tmdb_id=rmt,rid
                if not tmdb_id:
                    raise RuntimeError("verified TMDb id unavailable after rescue")
                lang_short=str(language or "en").split("-")[0].lower() or "en"
                details=resolver.tmdb._get("/%s/%s"%(mt,int(tmdb_id)),{
                    "append_to_response":"images",
                    "include_image_language":"null,en,%s"%lang_short,
                }) or {}
                fp=str(details.get("backdrop_path") or "").strip()
                if fp:
                    choice="tmdb_primary"
                else:
                    images=details.get("images") if isinstance(details.get("images"),dict) else {}
                    ranked=rank_clean_backdrops(images.get("backdrops") or [],language,16)
                    if not ranked:
                        images=resolver.tmdb._get("/%s/%s/images"%(mt,int(tmdb_id)),{}) or {}
                        ranked=rank_clean_backdrops(images.get("backdrops") or [],language,16)
                    if ranked:
                        pick=ranked[0];fp=str(pick.get("file_path") or "").strip();iso=str(pick.get("iso_639_1") or "")
                        choice="gallery_fallback"
                if not fp:
                    raise RuntimeError("TMDb backdrop unavailable")
                url=str(TMDBClient.image_url(fp,"original") or "")
                destination=_page_backdrop_q60_path(url)
                if not destination:
                    raise RuntimeError("Q60 destination unavailable")
                if _page_backdrop_q60_valid(destination):
                    ok=True;state="cached"
                else:
                    built=_page_backdrop_fetch_original_q60(url,destination,timeout,cancel_event)
                    ok=bool(_page_backdrop_q60_valid(built));state="downloaded" if ok else ""
                if ok:
                    pointer={
                        "matched":True,"identity_verified":True,"tmdb_id":int(tmdb_id),"media_type":str(mt),
                        "backdrop_path":fp,"backdrop_url":url,"backdrop_source":choice+":q60",
                        "clean_backdrop_selector_version":3,"clean_backdrop_choice":choice,"clean_backdrop_iso":iso,
                    }
                    try:_save_manifest(self.profile,self.media_type,item,pointer)
                    except Exception as exc:optional_failure("ui.page_backdrop_pointer",exc)
                    return True,state,choice
            except Exception as exc:
                optional_failure("ui.page_backdrop_cache_item",exc)
            return False,"",""

        retry_pending=[]
        for item,hot in pending:
            if cancel_event is not None and cancel_event.is_set():break
            ok,state,choice=process_missing(item,hot)
            if ok:
                if state=="cached":cached+=1
                else:downloaded+=1
                if choice=="tmdb_primary":primary+=1
                elif choice=="gallery_fallback":gallery+=1
                done+=1;emit_progress(True)
            else:
                retry_pending.append((item,hot))

        # One terminal rescue pass.  This catches short TMDb/network hiccups and
        # lets newly written verified manifests from the first pass participate.
        # Failed is therefore a genuine two-pass miss, not merely "not ready on
        # the first request".  Keep it sequential and bounded: no hidden worker,
        # no repeated loop and no 30-second per-title sleep.
        for item,hot in retry_pending:
            if cancel_event is not None and cancel_event.is_set():break
            try:
                hot=load_artwork_v2_manifest(self.profile,self.media_type,item) or hot or {}
            except Exception:
                pass
            ok,state,choice=process_missing(item,hot)
            if ok:
                if state=="cached":cached+=1
                else:downloaded+=1
                if choice=="tmdb_primary":primary+=1
                elif choice=="gallery_fallback":gallery+=1
            else:
                failed+=1
            done+=1;emit_progress(True)

        result.update({"total":planned,"done":done,"cached":cached,"downloaded":downloaded,"failed":failed,"gallery":gallery,"primary":primary})
        # Rows that could not be enumerated are not artwork failures.  Keep the
        # counts truthful and mark the run incomplete instead of inventing fails.
        if planned and len(catalogue)<planned and not (cancel_event is not None and cancel_event.is_set()):
            result["incomplete"]=True
            result["reason"]="category_enumeration_incomplete"
        elif cancel_event is not None and cancel_event.is_set():
            result["incomplete"]=True;result["reason"]="cancelled"
        return result

    def cache_folder_artwork(self):
        """Cache every title in the current category in one continuous run."""
        if self.media_type not in ("vod","series") or getattr(self,"_folder_artwork_cache_running",False):return False
        cfg=dict(getattr(self,"_grid_settings",None) or load_settings() or {})
        if not (cfg.get("tmdb_enabled",True) and str(cfg.get("tmdb_credential") or "").strip()):return False
        # GUI-safe count only: the already-open category owns its catalogue total.
        # Fifty is deliberately *not* a cap; it is only the worker chunk size.
        planned=self._cache_backdrop_category_total()
        if planned<=0:
            try:planned=len(list(getattr(self,"grid_items",[]) or []))
            except Exception:planned=0
        if planned<=0:
            try:self["status"].setText(_("No content"))
            except Exception:pass
            return False
        self._folder_artwork_cache_running=True;self._folder_artwork_ui_finalized=False
        self._folder_artwork_cache_done=0;self._folder_artwork_cache_total=planned
        try:self._folder_artwork_idle_label=str(self["blue"].getText() or _("Cache Artwork"))
        except Exception:self._folder_artwork_idle_label=_("Cache Artwork")
        try:
            self["status"].setText(_("Backdrop cache • checking %d/%d • new %d • already %d")%(0,planned,0,0))
            self["blue"].setText("0 / %d"%planned)
            if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",22))
        except Exception:pass
        try:self._async_set_poll_interval(120)
        except Exception:pass
        def worker():return self._cache_current_page_backdrops_worker(None,None)
        try:
            future=CACHE_IO_EXECUTOR.submit(worker,_task_key="page-q60:%x"%id(self),_replace_task_key=True)
            self._folder_artwork_future=future
            def finished(done_future):
                try:payload=done_future.result() or {"backdrop_page_cache":True,"total":planned,"done":0,"cached":0,"downloaded":0,"failed":planned}
                except Exception as exc:
                    optional_failure("ui.page_backdrop_cache_future",exc);payload={"backdrop_page_cache":True,"total":planned,"done":0,"cached":0,"downloaded":0,"failed":planned}
                try:self._folder_artwork_progress.put(("__complete__",payload))
                except Exception:pass
            future.add_done_callback(finished)
            return True
        except Exception as exc:
            self._folder_artwork_cache_running=False;optional_failure("ui.page_backdrop_cache_submit",exc)
            try:self["blue"].setText(getattr(self,"_folder_artwork_idle_label",_("Cache Artwork")))
            except Exception:pass
            return False

    def _open_recent_folder_search_menu(self, keyboard):
        """Use the exact same persistent Recent Search store as Home Search."""
        try:
            recent=[str(x).strip() for x in (_load_recent_searches() or []) if str(x or "").strip()]
        except Exception:
            recent=[]
        if not recent or ChoiceBox is None:return
        choices=[(term,term) for term in recent[:10]]
        def picked(choice):
            if not choice:return
            try:term=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice[0]
            except Exception:term=choice
            term=str(term or "").strip()
            if not term:return
            try:keyboard.close(term)
            except Exception as exc:optional_failure("ui.folder_search_recent_close",exc)
        try:
            owner_session=getattr(keyboard,"session",None) or getattr(self,"session",None)
            if owner_session is None:return
            owner_session.openWithCallback(picked,ChoiceBox,title=_("Recent searches"),list=choices)
        except Exception as exc:optional_failure("ui.folder_search_recent_menu",exc)

    def _attach_recent_folder_search_menu(self, keyboard):
        if keyboard is None:return
        try:
            amap=ActionMap(["MenuActions","UltraStalkerMenuActions"],{"menu":lambda:self._open_recent_folder_search_menu(keyboard)},-1000)
            keyboard["ultrastalker_recent_search_actions"]=amap
            try:amap.execBegin()
            except Exception as exc:optional_failure("ui.folder_search_recent_exec",exc)
        except Exception as exc:optional_failure("ui.folder_search_recent_action",exc)

    def open_folder_search(self):
        if self.media_type not in ("vod","series"):return
        title=_("Search in %s")%self.category_title
        try:
            recent=[str(x).strip() for x in (_load_recent_searches() or []) if str(x or "").strip()]
        except Exception:
            recent=[]
        if recent:title+="  •  "+_("Recent: %s")%", ".join(recent[:3])
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard as E2VirtualKeyBoard
        except Exception:
            E2VirtualKeyBoard=None
        if E2VirtualKeyBoard is not None:
            keyboard=self.session.openWithCallback(self._folder_search_entered,E2VirtualKeyBoard,title=title,text=self._search_query)
            self._attach_recent_folder_search_menu(keyboard)
        else:
            self.session.openWithCallback(self._folder_search_entered,InputBox,title=title,text=self._search_query,maxSize=60)

    def _folder_search_entered(self,term=None):
        if term is None:return
        # VirtualKeyBoard may return non-str text wrappers on some images. Keep
        # the callback fail-soft, then invalidate any older normal-page request
        # before the filtered view is painted. Without this guard a late page
        # callback can overwrite Search and make it look as if OK did nothing.
        try:
            if isinstance(term,bytes):term=term.decode("utf-8","ignore")
            elif isinstance(term,(list,tuple)) and len(term)==1:term=term[0]
        except Exception:pass
        self._search_query=one_line(term or "",60)
        self._folder_search_rows=None
        if self._search_query:
            try:_save_recent_search(self._search_query)
            except Exception as exc:optional_failure("ui.folder_search_recent_save",exc)
        try:
            pending=getattr(self,"_page_load_handle",None)
            if pending is not None:
                try:pending.cancel()
                except Exception:pass
            self._pending_page_request=None
            old_cancel=getattr(self,"_page_prefetch_cancel",None)
            if old_cancel is not None:old_cancel.set()
            self._page_prefetch_generation=int(getattr(self,"_page_prefetch_generation",0) or 0)+1
        except Exception as exc:optional_failure("ui.folder_search_cancel_stale_page",exc)
        self._refresh_search_sort_controls()

        # Empty input is a true reset. Do not build the full folder catalogue just
        # to get back to the provider page the user already had open.
        if not self._search_query:
            self._folder_search_rows=None
            try:self["status"].setText("")
            except Exception:pass
            # Preserve the user's current Sort mode exactly like the old search
            # flow. Default sort can jump straight back to native paging; a
            # non-default sort still requires the complete folder catalogue.
            if int(getattr(self,"_sort_mode",0) or 0):
                self._view_active=True
                def _sorted_ready(_rows):
                    try:self._load_folder_view_page(1,0)
                    except Exception as exc:optional_failure("ui.folder_search_reset_sorted",exc)
                self._ensure_folder_catalog(_sorted_ready)
            else:
                self._view_active=False;self._view_items=None
                try:self.load_page(1,0)
                except Exception as exc:optional_failure("ui.folder_search_reset",exc)
            return

        try:self["status"].setText(_("Searching..."))
        except Exception:pass

        # If Sort/BLUE already built the complete folder index, local search is
        # genuinely instant and remains authoritative. Otherwise use the same
        # bounded provider-native lane as Home Search FIRST instead of scanning up
        # to hundreds of category pages before the user sees anything.
        if isinstance(self._folder_catalog,list):
            started=time.monotonic();matches=self._build_folder_view()
            self._load_folder_view_page(1,0)
            try:_ui_diag("folder_search_fast",media_type=self.media_type,source="local",rows=len(matches),elapsed_ms=int((time.monotonic()-started)*1000))
            except Exception:pass
            return

        query=self._search_query
        started=time.monotonic()
        def provider_job(handle):
            # R78: Search from a grid means THIS FOLDER ONLY.  Home already owns
            # portal-wide search.  Ask the provider for a category-scoped native
            # search first; if it cannot do that, provider_ok() falls back to the
            # old complete-folder index so correctness is never traded for speed.
            fn=getattr(self.client,"search_category_fast",None)
            if not callable(fn):return []
            category=str(getattr(self,"genre","") or "*")
            try:
                rows=fn(query,media_type=self.media_type,category=category,limit=120,max_pages=18,
                        cancel_event=handle.cancel_event,time_budget=5) or []
                # M3U/Xtream routing occasionally stores the visible category
                # title rather than the internal id. Retry that exact SAME folder
                # label only when the primary route produced nothing.
                if (not rows and self._grid_is_m3u_source() and str(getattr(self,"category_title","") or "").strip()
                        and str(getattr(self,"category_title","") or "").strip()!=category):
                    rows=fn(query,media_type=self.media_type,category=str(self.category_title).strip(),limit=120,max_pages=18,
                            cancel_event=handle.cancel_event,time_budget=5) or []
            except TypeError:
                rows=fn(query,self.media_type,category,120,18,handle.cancel_event,5) or []
            out=[];seen=set();needle=self._folder_search_norm(query)
            for row in rows:
                if handle.cancelled() or not isinstance(row,dict):continue
                hay=" ".join(self._folder_search_norm(row.get(k)) for k in ("name","title","original_name","original_title","_raw_name"))
                if needle and needle not in hay:continue
                item=dict(row);ident=self._folder_item_identity(item,len(out))
                if ident in seen:continue
                seen.add(ident);out.append(item)
            return out
        def _accurate_folder_fallback():
            # Native fast search should be the normal path. If a provider does
            # not support it (or returns an empty native result), fall back to
            # the old authoritative folder index instead of lying to the user
            # with an immediate empty screen. This preserves R75 correctness
            # while keeping the common path fast.
            try:self["status"].setText(_("Searching..."))
            except Exception:pass
            def ready(_rows):
                try:
                    self._folder_search_rows=None
                    self._view_active=True
                    matches=self._build_folder_view()
                    self._load_folder_view_page(1,0)
                    _ui_diag("folder_search_fast",media_type=self.media_type,source="folder_fallback",rows=len(matches),elapsed_ms=int((time.monotonic()-started)*1000))
                except Exception as exc:optional_failure("ui.folder_search_fallback_ready",exc)
            self._ensure_folder_catalog(ready)
        def provider_ok(rows):
            clean=[x for x in (rows or []) if isinstance(x,dict)]
            if not clean:
                try:_ui_diag("folder_search_fast",media_type=self.media_type,source="provider_empty_fallback",rows=0,elapsed_ms=int((time.monotonic()-started)*1000))
                except Exception:pass
                _accurate_folder_fallback();return
            self._folder_search_rows=clean
            self._view_active=True
            self._load_folder_view_page(1,0)
            try:_ui_diag("folder_search_fast",media_type=self.media_type,source="category_provider",rows=len(self._folder_search_rows),elapsed_ms=int((time.monotonic()-started)*1000))
            except Exception:pass
        def provider_failed(error):
            try:_ui_diag("folder_search_fast",media_type=self.media_type,source="provider_error_fallback",rows=0,elapsed_ms=int((time.monotonic()-started)*1000))
            except Exception:pass
            _accurate_folder_fallback()
        self._run_async(provider_job,provider_ok,provider_failed)

    def cycle_folder_sort(self):
        if self.media_type not in ("vod","series"):return
        next_mode=(self._sort_mode+1)%len(self._FOLDER_SORT_LABELS)
        def ready(_rows):
            self._sort_mode=next_mode
            self._refresh_search_sort_controls()
            self._load_folder_view_page(1,0)
        self._ensure_folder_catalog(ready)

    def _portal_page(self, page, cancel_event=None):
        page=max(1,int(page))
        if cancel_event is not None and cancel_event.is_set():
            raise _ArtworkCancelled()
        if page in self._portal_page_cache:
            items = self._portal_page_cache[page]
            self._portal_page_cache.move_to_end(page)
            return items
        result=self.client.ordered_page(self.media_type,self.genre,page,cancel_event=cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise _ArtworkCancelled()
        items=[x for x in result.get("items",[]) if isinstance(x,dict)]
        if (not items and self._grid_is_m3u_source() and self.media_type in ("vod","series")
                and str(getattr(self,"category_title","") or "").strip()
                and str(getattr(self,"category_title","") or "").strip()!=str(self.genre or "").strip()):
            try:
                alt=self.client.ordered_page(self.media_type,str(self.category_title).strip(),page,cancel_event=cancel_event) or {}
                alt_items=[x for x in alt.get("items",[]) if isinstance(x,dict)]
                if alt_items:result=alt;items=alt_items
            except Exception as exc:optional_failure("ui.m3u_category_title_retry",exc)
        if not self._portal_page_size:
            self._portal_page_size=max(1,int(result.get("page_size") or len(items) or self.page_size))
        self._portal_total=max(self._portal_total,int(result.get("total") or 0))

        # PERFLAB18: a large Stalker catalogue may intermittently return a
        # successful-but-empty native page even though total_items proves that
        # the requested offset is still inside the catalogue.  Never freeze that
        # false EOF into the per-screen cache.  Invalidate only the provider API
        # cache generation and retry the exact same native page once immediately;
        # the bounded page-resilience lane below owns any further retries/reauth.
        native=max(1,int(self._portal_page_size or len(items) or self.page_size or 1))
        known_total=max(0,int(self._portal_total or 0))
        premature_empty=(not items and known_total>0 and ((page-1)*native)<known_total)
        if premature_empty:
            _runtime_endurance_log("grid_native_premature_empty",media_type=self.media_type,page=page,total=known_total,native_page_size=native)
            try:
                bump=getattr(self.client,"_bump_content_cache_generation",None)
                if callable(bump):bump()
            except Exception as exc:
                optional_failure("ui.grid_native_empty_cache_generation",exc)
            try:
                retry=self.client.ordered_page(self.media_type,self.genre,page,cancel_event=cancel_event) or {}
                retry_items=[x for x in retry.get("items",[]) if isinstance(x,dict)]
                if retry_items:
                    result=retry;items=retry_items
                    self._portal_page_size=max(1,int(result.get("page_size") or self._portal_page_size or len(items) or 1))
                    self._portal_total=max(self._portal_total,int(result.get("total") or 0))
            except Exception as exc:
                optional_failure("ui.grid_native_empty_retry",exc)

        # Only cache a native page when it contains real rows or is genuinely
        # beyond the provider-advertised total.  A premature empty must remain
        # retryable instead of becoming a 30-minute/local-screen false EOF.
        native=max(1,int(self._portal_page_size or len(items) or self.page_size or 1))
        known_total=max(0,int(self._portal_total or 0))
        expected_more=known_total>0 and ((page-1)*native)<known_total
        if items or not expected_more:
            self._portal_page_cache[page]=items
            self._portal_page_cache.move_to_end(page)
            while len(self._portal_page_cache) > self._portal_page_cache_limit:
                self._portal_page_cache.popitem(last=False)
        return items

    def _runtime_grid_close(self):
        _mem34("grid_close", mode=self.__class__.__name__, media=str(getattr(self,"media_type","") or ""), page=int(getattr(self,"page",0) or 0), items=len(getattr(self,"grid_items",[]) or []), visual_ram=len(getattr(self,"_page_visual_ram",{}) or {}))
        if getattr(self,"media_type",None)=="itv":
            _live_restart_trace("grid_close",page=int(getattr(self,"page",0) or 0),index=int(getattr(self,"index",0) or 0),
                                generation=int(getattr(self,"_grid_generation",0) or 0),
                                inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:self._cancel_detached_page_prefetch_futures()
        except Exception as exc:optional_failure("ui.grid_detached_future_close",exc)
        _runtime_endurance_log("grid_close",media_type=self.media_type,page=getattr(self,"page",0),items=len(getattr(self,"grid_items",[]) or []))

    def _grid_page_data(self, target, cancel_event=None):
        # The portal's native page size is often 14 while our poster grid is 12
        # and live grid is 24. Stitch native pages before slicing so no item is
        # skipped and the original portal order stays exact.
        target=max(1,int(target))
        if not self._portal_page_size:
            self._portal_page(1,cancel_event=cancel_event)
        native=max(1,self._portal_page_size)
        start=(target-1)*self.page_size;end=start+self.page_size
        if self._portal_total and start>=self._portal_total:
            return []
        first=(start//native)+1;last=((max(start,end-1))//native)+1
        merged=[]
        for native_page in range(first,last+1):
            if cancel_event is not None and cancel_event.is_set():
                raise _ArtworkCancelled()
            merged.extend(self._portal_page(native_page,cancel_event=cancel_event))
        base=(first-1)*native
        return merged[max(0,start-base):max(0,end-base)]

    def _prepare_grid_page(self,target,cancel_event=None):
        """Fetch one page and return first-paint data with zero image generation.

        Test69 Lean contract: catalogue/state reads may happen here, but Pillow,
        adaptive chrome, thumbnail creation and visual-bundle writes never block
        the page becoming visible.  Any missing deterministic derivative is built
        only after the original poster has already painted.
        """
        data=self._grid_page_data(target,cancel_event)
        valid=[x for x in data if isinstance(x,dict)] if isinstance(data,list) else []
        if self.media_type in ("vod","series"):
            prepared_valid=[]
            for x in valid:
                if not isinstance(x,dict):continue
                if x.get("_xtream"):
                    row=dict(x);visible_provider=str(_image_url(row) or "").strip()
                else:
                    raw=dict(x)
                    visible_provider=str(raw.get("_visible_provider_art_url") or raw.get("_rescue_provider_art_url") or _image_url(raw) or "").strip()
                    visible_backdrop=""
                    try:
                        from .ui_artwork_helpers import _backdrop_url as _visible_backdrop_url
                        visible_backdrop=str(raw.get("_visible_provider_backdrop_url") or raw.get("_rescue_provider_backdrop_url") or _visible_backdrop_url(raw) or "").strip()
                    except Exception:
                        visible_backdrop=str(raw.get("_visible_provider_backdrop_url") or raw.get("_rescue_provider_backdrop_url") or "").strip()
                    row=_strip_portal_artwork(raw)
                    if visible_provider:row["_visible_provider_art_url"]=visible_provider
                    if visible_backdrop:row["_visible_provider_backdrop_url"]=visible_backdrop
                prepared_valid.append(row)
            valid=prepared_valid
            try:
                art_counts={}
                for row in valid:
                    value=str(row.get("_visible_provider_art_url") or _image_url(row) or "").strip()
                    if value:art_counts[value]=art_counts.get(value,0)+1
                generic_urls={url for url,count in art_counts.items() if count>=3}
                for row in valid:
                    value=str(row.get("_visible_provider_art_url") or _image_url(row) or "").strip()
                    if value in generic_urls:
                        row["_generic_provider_art"]=True;row["_generic_provider_art_url"]=value
            except Exception as exc:optional_failure("ui.generic_provider_art_detect",exc)

        states=load_content_states(self.profile,self.media_type,valid) if valid else []
        qualities=load_content_qualities(self.profile,self.media_type,valid) if valid and self.media_type in ("vod","series") else ["" for _ in valid]
        state_rows=[];cfg=self._grid_settings or {}
        prepared_titles=[];prepared_fitted=[];prepared_meta=[];prepared_art=[]
        _r50_cache_t0=time.monotonic();_r50_cache_checks=0;_r50_cache_hits=0
        _r52_fast_hits=0;_r52_fallbacks=0;_r81_bridge_hits=0
        for pos,item in enumerate(valid):
            if self.media_type in ("vod","series"):
                try:self._age_cache_seed_item(item)
                except Exception as exc:optional_failure("ui.age_cache_first_paint",exc)
            state=states[pos] if pos<len(states) else {}
            quality=qualities[pos] if pos<len(qualities) else ""
            row=dict(state or {});row["quality"]=quality or "";state_rows.append(row)
            raw=item.get("name") or item.get("title") or item.get("id") or _("Result")
            title=self._display_item_title(item,"Item")
            prepared_titles.append(title)
            if self.media_type=="itv":
                prepared_fitted.append((one_line(title,36),None));prepared_meta.append(str((target-1)*self.page_size+pos+1))
            else:
                fitted_title,fitted_font=_grid_fit_card_title(title,226)
                prepared_fitted.append((fitted_title,int(fitted_font)))
                bits=[];explicit=self._explicit_item_quality(item)
                badges=explicit or str(row.get("quality") or "") or quality_badges(raw)
                if badges:bits.append(badges)
                if item.get("year"):bits.append(str(item.get("year"))[:4])
                if row.get("favorite"):bits.append("★")
                if row.get("completed") and cfg.get("show_watched",True):bits.append("WATCHED")
                elif row.get("position") and row.get("duration"):bits.append("%d%%"%min(99,int(row["position"]*100.0/row["duration"])))
                prepared_meta.append("  •  ".join(bits)[:28])

            # R50 cache-first first paint: page preparation already runs off the
            # Enigma2 GUI thread, so a RAM miss may safely consult the canonical
            # ArtworkV2 manifest here.  Only local files are accepted: no TMDb,
            # provider network, Pillow generation or visual-bundle work.  This
            # restores the expected contract that physically cached artwork is
            # visible on the first paint instead of trickling in seconds later.
            art_row=self._page_visual_get(item) if self.media_type in ("vod","series") else {}
            if self.media_type in ("vod","series") and not art_row:
                try:
                    _r50_cache_checks+=1
                    # R52: first-paint cache lookup uses the tiny per-item identity
                    # pointer first. A canonical poster hit avoids canonical metadata,
                    # adaptive JSON, alias and manual-rescue reads on the critical path.
                    _fast_loader=globals().get("load_artwork_v2_fast_local_poster") or _direct_fast_local_poster
                    if _fast_loader is None:
                        raise NameError("load_artwork_v2_fast_local_poster is not available")
                    snap=_fast_loader(self.profile,self.media_type,item) or {}
                    _fast_poster=str(snap.get("poster_local") or "")
                    if _fast_poster and os.path.isfile(_fast_poster) and os.path.getsize(_fast_poster)>256:
                        _r52_fast_hits+=1
                    else:
                        _r52_fallbacks+=1
                        snap=load_artwork_v2_manifest(self.profile,self.media_type,item) or {}
                    if snap.get("_fast_bridge_source"):
                        _r81_bridge_hits+=1
                    _tmdb_id=snap.get("tmdb_id")
                    if _tmdb_id:
                        item["_locked_tmdb_id"]=_tmdb_id
                        item["_locked_tmdb_type"]=snap.get("media_type") or ("tv" if self.media_type=="series" else "movie")
                    try:
                        _score=float(snap.get("rating") or snap.get("vote_average") or 0)
                    except Exception:
                        _score=0.0
                    if 0.0 < _score <= 10.0:
                        item["_tmdb_rating"]=_score
                    try:
                        _cached_raw_age=str(snap.get("certification") or snap.get("age_rating") or "").strip()
                        _cached_age_checked=bool(snap.get("_age_rating_checked") or _cached_raw_age)
                        if _cached_age_checked:
                            item["_age_rating_checked"]=True
                            if _cached_raw_age:
                                item["certification"]=_cached_raw_age;item["age_rating"]=_cached_raw_age
                            self._age_cache_put(item,_cached_raw_age,True)
                    except Exception as exc:
                        optional_failure("ui.r226_age_manifest_seed",exc)
                    try:
                        if self._folder_year_value(item)<=0:
                            _year=self._folder_year_value(snap)
                            if _year>0:item["year"]=_year
                    except Exception:
                        pass
                    _poster=str(snap.get("poster_local") or "")
                    _backdrop=str(snap.get("backdrop_local") or "")
                    if _poster and os.path.isfile(_poster) and os.path.getsize(_poster)>256:
                        _display=_poster
                        try:
                            _thumb=self._grid_poster_thumb_path(_poster) or ""
                            if _thumb and os.path.isfile(_thumb) and os.path.getsize(_thumb)>100:
                                _display=_thumb
                        except Exception:
                            pass
                        art_row={"display":_display,"poster":_poster,"palette":_poster}
                        if _backdrop and os.path.isfile(_backdrop) and os.path.getsize(_backdrop)>1024:
                            art_row["backdrop"]=_backdrop
                        _r50_cache_hits+=1
                except Exception as exc:
                    optional_failure("ui.r50_cache_first_manifest",exc)
            prepared_art.append(art_row)
        if self.media_type in ("vod","series"):
            try:
                import logging
                logging.getLogger("UltraStalker").info(
                    "PERF52 cache_first_fast media=%s mode=%s page=%d checks=%d fast_hits=%d bridge_hits=%d fallback=%d poster_hits=%d elapsed_ms=%d",
                    str(self.media_type or ""),self.__class__.__name__,int(target or 0),int(_r50_cache_checks),int(_r52_fast_hits),int(_r81_bridge_hits),int(_r52_fallbacks),int(_r50_cache_hits),int((time.monotonic()-_r50_cache_t0)*1000)
                )
            except Exception:
                pass
        return {"items":valid,"states":state_rows,"titles":prepared_titles,"fitted":prepared_fitted,"meta":prepared_meta,"art":prepared_art}

    def _cache_prepared_page(self,target,payload):
        if not isinstance(payload,dict):return
        with self._prepared_page_lock:
            self._prepared_page_cache[int(target)]=payload
            self._prepared_page_cache.move_to_end(int(target))
            while len(self._prepared_page_cache)>max(4,int(getattr(self,"_prepared_page_cache_limit",4) or 4)):self._prepared_page_cache.popitem(last=False)
            self._prepared_page_pending.discard(int(target))

    def _stop_progressive_poster_prefetch(self):
        try:self._progressive_poster_cancel.set()
        except Exception as exc:optional_failure("ui.progressive_poster_stop",exc)
        self._progressive_poster_started=False

    def _stop_focus_heavy_artwork(self):
        """Cancel only the selected title full-artwork job.

        Visible poster page hydration uses its own bounded lane and is deliberately
        left alone so the 14 on-screen cards can keep filling while navigation
        remains responsive.
        """
        try:self._focus_art_cancel.set()
        except Exception:pass
        try:
            future=getattr(self,"_focus_art_future",None)
            if future is not None:future.cancel()
        except Exception:pass
        self._focus_art_future=None

    def _reset_focus_heavy_artwork(self):
        self._stop_focus_heavy_artwork()
        try:self._focus_art_token=int(getattr(self,"_focus_art_token",0) or 0)+1
        except Exception:self._focus_art_token=1
        self._focus_art_cancel=threading.Event()

    def _progressive_poster_item(self,item,cancel_event):
        """Hydrate one visible poster without letting the worker retain this Screen."""
        if self._screen_closed:return "",{}
        return _progressive_poster_item_detached(
            self.profile,self.media_type,item,self._grid_settings or {},self.client,cancel_event)

    def _start_progressive_poster_prefetch(self):
        """Start lazy hydration for the current visible page only."""
        if self._screen_closed or self.media_type not in ("vod","series") or not self.grid_items:
            return
        self._progressive_poster_started=True
        # PERFLAB12: hydrate every visible poster through the existing bounded
        # 3-worker lane, but do NOT start page-wide TMDb metadata/background
        # full-artwork work. Heavy metadata/backdrop belongs to stable focus only.
        try:self._prefetch_visible_page_details(priority_index=self.index,max_items=self.page_size)
        except Exception as exc:optional_failure("ui.auto_visible_page_start",exc)


    def _prefetch_visible_tmdb_metadata(self):
        """Fill missing visible-card TMDb ratings without artwork/backdrop work.

        R49: Backdrop Grid ratings used to become complete only after each title
        received stable focus because the poster lane stops immediately on a warm
        cached poster.  This dedicated metadata-only lane resolves only the
        missing score/year/identity for the currently visible cards.  It never
        downloads poster/backdrop bytes and never runs on Poster Grid V2 or
        Cinematic unless a screen explicitly opts in via
        ``visible_card_rating_prefetch``.
        """
        if self._screen_closed or self.media_type not in ("vod","series") or not self.grid_items:
            return
        generation=int(getattr(self,"_page_prefetch_generation",0) or 0)
        cancel_event=getattr(self,"_page_prefetch_cancel",None)
        profile=dict(self.profile or {})
        media_type=str(self.media_type or "")
        cfg=dict(self._grid_settings or {})
        credential=str(cfg.get("tmdb_credential") or "").strip()
        if not (cfg.get("tmdb_enabled",True) and credential):
            return
        result_q=self._art_prefetch_jobs
        pending_set=self._visible_tmdb_pending
        pending_lock=self._page_prefetch_lock
        rating_cache=self._visible_tmdb_rating_cache
        need_age=bool(getattr(self,"visible_card_age_prefetch",False))

        selected=[]
        center=max(0,min(int(getattr(self,"index",0) or 0),len(self.grid_items)-1))
        rows=list(enumerate(self.grid_items[:self.page_size]))
        rows.sort(key=lambda pair:(abs(pair[0]-center),pair[0]))
        for pos,item in rows:
            if not isinstance(item,dict):
                continue
            key=self._page_visual_key(item)
            if not key:
                continue
            try:
                self._age_cache_seed_item(item)
            except Exception as exc:
                optional_failure("ui.age_cache_prefetch_seed",exc)
            try:
                existing=float(item.get("_tmdb_rating") or 0)
            except Exception:
                existing=0.0
            if not (0.0 < existing <= 10.0):
                try:
                    existing=float(rating_cache.get(key) or 0)
                    if 0.0 < existing <= 10.0:
                        item["_tmdb_rating"]=existing
                        self._card_rating_layout(item,pos,self._card_meta(item,pos))
                except Exception:
                    existing=0.0
            # R61: YEAR is part of the visible metadata contract too.  A card
            # with a warm score but a blank year must still enter the metadata
            # lane; previously the rating-only gate skipped it forever.
            try:
                existing_year=int(self._folder_year_value(item) or 0)
            except Exception:
                existing_year=0
            existing_age=str(item.get("certification") or item.get("age_rating") or "").strip()
            age_ready=bool(display_certification(existing_age) or (not existing_age and item.get("_age_rating_checked")))
            if 0.0 < existing <= 10.0 and existing_year > 0 and (not need_age or age_ready):
                continue
            pending_key=(generation,key)
            try:
                with pending_lock:
                    if pending_key in pending_set:
                        continue
                    pending_set.add(pending_key)
            except Exception:
                continue
            selected.append((pos,dict(item),key,pending_key))

        if not selected:
            return
        try:
            import logging
            logging.getLogger("UltraStalker").info(
                "PERF49 rating_page_begin media=%s mode=%s page=%d missing=%d visible=%d",
                media_type,self.__class__.__name__,int(getattr(self,"page",0) or 0),len(selected),len(rows)
            )
        except Exception:
            pass

        for pos,it,key,pending_key in selected:
            def worker(it=dict(it), k=key, gen=generation, cancel=cancel_event, pk=pending_key, slot=pos):
                t0=time.monotonic()
                ok=False
                source="none"
                age_lookup_checked=False
                try:
                    if cancel is not None and cancel.is_set():
                        return False
                    # R52: if the item already has a verified local pointer, consume
                    # its cached canonical rating after first paint before touching
                    # TMDb. This keeps warm-cache pages network-free while the
                    # poster critical path remains pointer-only and fast.
                    data={}
                    try:
                        local=load_artwork_v2_manifest(profile,media_type,it) or {}
                        _local_score=float(local.get("rating") or local.get("vote_average") or 0)
                        if 0.0 < _local_score <= 10.0:
                            data=local
                            source="local"
                    except Exception:
                        data={}
                    if not data:
                        try:
                            # R60: use the exact same identity resolver as visible
                            # poster/focus hydration. The title-level ArtworkV2 lock
                            # deduplicates this against an in-flight poster resolve,
                            # so a cold page does not search TMDB twice with two
                            # different matching rules. full=False never asks for
                            # backdrop/credits/logo work.
                            resolver=ArtworkV2(
                                credential,
                                cfg.get("tmdb_language","ar-EG"),
                                min(5,max(3,int(cfg.get("timeout",10) or 10)))
                            )
                            data=resolver.resolve(profile,media_type,it,full=False,cancel_event=cancel) or {}
                            source="artwork_v2"
                            # Legacy manifests can already own a poster + tmdb_id
                            # from older builds but lack YEAR/rating. In that narrow
                            # case, fetch only the canonical details row by id. No
                            # fuzzy search and no image bytes are involved.
                            try:
                                _score=float(data.get("rating") or data.get("vote_average") or 0)
                            except Exception:
                                _score=0.0
                            try:
                                _year_ok=bool(self._folder_year_value(data)>0)
                            except Exception:
                                _year_ok=False
                            if data.get("tmdb_id") and (not (0.0 < _score <= 10.0) or not _year_ok):
                                _mt=str(data.get("media_type") or ("tv" if media_type=="series" else "movie"))
                                details=resolver.tmdb._get("/%s/%s"%(_mt,data.get("tmdb_id")),{"language":resolver.language}) or {}
                                if isinstance(details,dict) and details:
                                    merged=dict(data)
                                    _date=details.get("first_air_date") if _mt=="tv" else details.get("release_date")
                                    merged["rating"]=details.get("vote_average")
                                    merged["vote_count"]=details.get("vote_count")
                                    merged["release_date"]=_date or merged.get("release_date") or ""
                                    merged["year"]=_date or merged.get("year")
                                    data=merged
                                    source="tmdb_id"
                                    try:
                                        from .artwork_v2 import save_manifest as _r60_save_manifest
                                        _r60_save_manifest(profile,media_type,it,data)
                                    except Exception as _save_exc:
                                        optional_failure("ui.r60_metadata_manifest",_save_exc)
                        except Exception as exc:
                            optional_failure("ui.visible_tmdb_metadata",exc)
                            data={}
                    # R61: ArtworkV2 returns a fail-safe dict on an identity miss.
                    # That dict is intentionally non-empty (matched=False, sticky
                    # local paths, etc.), so the old `if not data` gate silently
                    # skipped the advertised compatibility fallback.  Judge the
                    # result by the metadata the card actually needs instead.
                    try:
                        _r61_score=float((data or {}).get("rating") or (data or {}).get("vote_average") or 0)
                    except Exception:
                        _r61_score=0.0
                    try:
                        _r61_year=bool(self._folder_year_value(data or {})>0)
                    except Exception:
                        _r61_year=False
                    _r61_age=str((data or {}).get("certification") or (data or {}).get("age_rating") or "").strip()
                    _r61_age_ready=bool(display_certification(_r61_age) or (not _r61_age and (data or {}).get("_age_rating_checked")))

                    # R227: if identity is already known but the age token is only
                    # vocabulary (NR/Unrated/etc.), do a tiny exact-id rating read
                    # before falling back to any title search.  This is the same
                    # canonical TMDb endpoint used by Details, and it can choose a
                    # usable region when the preferred region says NR.
                    if need_age and not _r61_age_ready and (data or {}).get("tmdb_id"):
                        try:
                            _age_mt=str((data or {}).get("media_type") or ("tv" if media_type=="series" else "movie"))
                            _age_id=int((data or {}).get("tmdb_id"))
                            _age_client=None
                            try:_age_client=resolver.tmdb
                            except Exception:_age_client=None
                            if _age_client is None:
                                from .tmdb import TMDBClient as _AgeTMDBClient
                                _age_client=_AgeTMDBClient(credential,cfg.get("tmdb_language","ar-EG"),min(5,max(3,int(cfg.get("timeout",10) or 10))))
                            _age_payload=_age_client._get(rating_endpoint(_age_mt,_age_id)) or {}
                            _age_raw=certification_from_tmdb(data or {},_age_mt,supplemental=_age_payload)
                            age_lookup_checked=True
                            merged=dict(data or {})
                            if _age_raw:
                                merged["certification"]=_age_raw;merged["age_rating"]=_age_raw
                            else:
                                # Do not let a provider NR survive as if canonical.
                                merged["certification"]="";merged["age_rating"]=""
                            merged["_age_rating_checked"]=True
                            data=merged
                            _r61_age=str(_age_raw or "")
                            _r61_age_ready=bool(display_certification(_r61_age) or not _r61_age)
                            source="tmdb_age_exact"
                        except Exception as exc:
                            optional_failure("ui.visible_tmdb_age_exact",exc)
                    if not (0.0 < _r61_score <= 10.0 and _r61_year and (not need_age or _r61_age_ready)):
                        try:
                            # Final compatibility fallback for unusual rows that
                            # the ArtworkV2 identity resolver cannot fully resolve.
                            # Search-only title normalization lives in tmdb_impl;
                            # catalogue/cache identity remains untouched.
                            from .tmdb import TMDBClient
                            fallback=TMDBClient(
                                credential,
                                cfg.get("tmdb_language","ar-EG"),
                                min(5,max(3,int(cfg.get("timeout",10) or 10)))
                            ).enrich(media_type,it,artwork_mode="metadata") or {}
                            if isinstance(fallback,dict) and fallback:
                                merged=dict(data or {})
                                for _fk,_fv in fallback.items():
                                    if _fv not in (None,""):
                                        merged[_fk]=_fv
                                data=merged
                                source="tmdb"
                                if need_age and (fallback.get("tmdb_id") or fallback.get("matched")):
                                    age_lookup_checked=True
                        except Exception as exc:
                            optional_failure("ui.visible_tmdb_metadata_fallback",exc)
                    if cancel is not None and cancel.is_set():
                        return False
                    _meta_age=str(data.get("certification") or data.get("age_rating") or "").strip()
                    _meta_age_display=display_certification(_meta_age)
                    # A non-displayable raw label is not a resolved age.  If the
                    # canonical lane actually completed, publish a checked blank
                    # rather than caching NR forever as terminal metadata.
                    if need_age and not _meta_age_display and age_lookup_checked:
                        _meta_age=""
                    _meta_age_checked=bool(
                        _meta_age_display or
                        (not _meta_age and (age_lookup_checked or data.get("_age_rating_checked")))
                    )
                    meta={
                        "tmdb_id":data.get("tmdb_id"),
                        "media_type":data.get("media_type") or ("tv" if media_type=="series" else "movie"),
                        "rating":data.get("rating") or data.get("vote_average"),
                        "year":data.get("year") or data.get("release_date") or data.get("first_air_date"),
                        "release_date":data.get("release_date") or data.get("first_air_date") or "",
                        "certification":_meta_age,
                        "age_rating":_meta_age,
                        "_age_rating_checked":_meta_age_checked,
                        "palette_source":"",
                    }
                    if meta.get("tmdb_id") or meta.get("rating") or meta.get("year") or meta.get("certification"):
                        result_q.put((gen,k,"",meta))
                        ok=True
                        return True
                    return False
                finally:
                    try:
                        with pending_lock:
                            pending_set.discard(pk)
                    except Exception:
                        pass
                    try:
                        import logging
                        logging.getLogger("UltraStalker").info(
                            "PERF52 rating_item_done media=%s page=%d slot=%d ok=%s source=%s elapsed_ms=%d",
                            media_type,int(getattr(self,"page",0) or 0),int(slot),bool(ok),str(source),int((time.monotonic()-t0)*1000)
                        )
                    except Exception:
                        pass

            try:
                future=_VISIBLE_TMDB_EXECUTOR.submit(worker,_task_key="grid-tmdb:%x:%s:%s"%(id(self),generation,key))
                self._page_prefetch_futures.append(future)
            except Exception as exc:
                try:
                    with pending_lock:
                        pending_set.discard(pending_key)
                except Exception:
                    pass
                optional_failure("ui.visible_tmdb_metadata_submit",exc)

    def _prefetch_focused_artwork(self):
        """Hydrate poster + real backdrop for the stable focused title only."""
        if self._screen_closed or self.media_type not in ("vod","series") or not self.grid_items:
            return
        try:item=self.grid_items[max(0,min(self.index,len(self.grid_items)-1))]
        except Exception:return
        if not isinstance(item,dict):return
        cfg=self._grid_settings or {};credential=str(cfg.get("tmdb_credential") or "").strip()
        if not cfg.get("load_images",True):return
        key=self._page_visual_key(item)
        generation=int(getattr(self,"_page_prefetch_generation",0) or 0)
        token=int(getattr(self,"_focus_art_token",0) or 0)
        cancel_event=getattr(self,"_focus_art_cancel",None)
        profile=dict(self.profile or {});media_type=str(self.media_type or "")
        result_q=self._art_prefetch_jobs
        def hydrate_focus(it=dict(item),k=key,gen=generation,focus_token=token,cancel=cancel_event):
            if cancel is not None and cancel.is_set():return False
            try:
                if not credential:
                    return False
                if cfg.get("tmdb_enabled",True):
                    data=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(6,max(3,int(cfg.get("timeout",10) or 10)))).resolve(
                        profile,media_type,it,full=True,cancel_event=cancel) or {}
                else:
                    return False
                if cancel is not None and cancel.is_set():return False
                if focus_token!=int(getattr(self,"_focus_art_token",0) or 0):return False
                poster=str(data.get("poster_local") or "")
                if poster and os.path.isfile(poster) and gen==int(getattr(self,"_page_prefetch_generation",0) or 0):
                    result_q.put((gen,k,poster,{
                        "tmdb_id":data.get("tmdb_id"),
                        "media_type":data.get("media_type") or ("tv" if media_type=="series" else "movie"),
                        "rating":data.get("rating") or data.get("vote_average"),
                        "year":data.get("year") or data.get("release_date") or data.get("first_air_date"),
                        "release_date":data.get("release_date") or data.get("first_air_date") or "",
                        "certification":data.get("certification") or data.get("age_rating") or "",
                        "age_rating":data.get("age_rating") or data.get("certification") or "",
                        "_age_rating_checked":bool(data.get("_age_rating_checked") or data.get("certification") or data.get("age_rating")),
                        "backdrop_local":data.get("backdrop_local"),
                        "provider_locked":bool(data.get("provider_locked") or data.get("provider_bootstrap")),
                        "palette_source":poster,
                    }))
                return bool(data.get("poster_local") or data.get("backdrop_local"))
            except Exception as exc:
                if not (cancel is not None and cancel.is_set()):optional_failure("ui.auto_focused_artwork",exc)
                return False
        try:
            future=_DETAIL_PREFETCH_EXECUTOR.submit(hydrate_focus,priority=0,_task_key="focus-art:%x:%s"%(id(self),token))
            self._focus_art_future=future
        except Exception as exc:optional_failure("ui.auto_focused_art_submit",exc)

    def _refresh_prepared_m3u_art_from_hdd(self,payload):
        """Refresh a prepared M3U page from HDD only, never network."""
        if not self._grid_is_m3u_source() or self.media_type not in ("vod","series") or not isinstance(payload,dict):
            return payload
        items=list(payload.get("items") or [])
        art=list(payload.get("art") or [])
        while len(art)<len(items):art.append({})
        for pos,item in enumerate(items):
            if not isinstance(item,dict):continue
            row=dict(art[pos] or {})
            if row.get("display") or row.get("poster"):
                row["visual_locked"]=True;art[pos]=row;continue
            try:
                snap=(load_shared_detail_snapshot(self.profile,self.media_type,item)
                      if item.get("_xtream") else load_detail_snapshot(self.profile,self.media_type,item))
                bundle=_load_visual_bundle(self.profile,self.media_type,item,snap) or {}
                display=str(bundle.get("grid_thumb") or bundle.get("poster") or "")
                poster=str(bundle.get("poster") or "")
                if display and os.path.isfile(display):
                    row["display"]=display;row["palette"]=poster or display;row["visual_locked"]=True
                    card=str(bundle.get("grid_card") or "")
                    if card and os.path.isfile(card):row["card"]=card
                elif poster and os.path.isfile(poster):
                    row["poster"]=poster;row["visual_locked"]=True
            except Exception as exc:optional_failure("ui.m3u_prepared_hdd_refresh",exc)
            art[pos]=row
        payload=dict(payload);payload["art"]=art
        return payload

    def _warm_m3u_prepared_page_art(self,payload):
        """Schedule adjacent M3U VOD/Series through the global hydration lane."""
        if not self._grid_is_m3u_source() or self.media_type not in ("vod","series") or not isinstance(payload,dict):return
        cfg=self._grid_settings or {};credential=str(cfg.get("tmdb_credential") or "").strip()
        if not (cfg.get("tmdb_enabled",True) and cfg.get("load_images",True) and credential):return
        for item in [dict(x) for x in (payload.get("items") or []) if isinstance(x,dict)][:self.page_size]:
            if self._screen_closed:return
            def hydrate(it=item):
                try:
                    snap=load_shared_detail_snapshot(self.profile,self.media_type,it) if it.get("_xtream") else load_detail_snapshot(self.profile,self.media_type,it)
                    bundle=_load_visual_bundle(self.profile,self.media_type,it,snap) or {}
                    if str(bundle.get("poster") or "") and os.path.isfile(str(bundle.get("poster") or "")):return True
                    data=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(5,max(3,int(cfg.get("timeout",10) or 10)))).resolve(self.profile,self.media_type,it,full=True,cancel_event=None) or {}
                    return bool(data.get("matched"))
                except Exception as exc:optional_failure("ui.m3u_adjacent_global_warm",exc);return False
            try:_DETAIL_PREFETCH_EXECUTOR.submit(hydrate,priority=2,_task_key="hydrate:%s"%self._page_visual_key(item))
            except Exception as exc:optional_failure("ui.m3u_adjacent_art_submit",exc)

    def _prefetch_adjacent_pages(self):
        """Test69 Lean: explicit page navigation loads pages on demand."""
        return

    def _warm_poster_grid_catalogue_pages(self):
        """Warm neighbouring Poster Grid pages like Cinematic, without artwork work.

        Only VOD/Series provider catalogue + persisted state are prepared.  No
        poster/backdrop downloads, TMDB calls, Pillow generation or adaptive
        chrome is started here.  The next/previous page therefore enters through
        _prepared_page_cache immediately while card artwork remains cache-first.
        """
        if getattr(self,"_screen_closed",False) or self.media_type not in ("vod","series"):
            return
        if getattr(self,"_view_active",False):
            return
        total=int(self._active_total() or 0)
        if total<=int(self.page_size or 1):
            return
        total_pages=max(1,(total+int(self.page_size or 1)-1)//int(self.page_size or 1))
        current=max(1,min(int(self.page or 1),total_pages))
        # Prioritise the direction users normally browse: next page first, then
        # previous page second. Two prepared pages keep navigation hot without
        # turning provider catalogue work into background contention without turning a large catalogue into a RAM scan.
        targets=[]
        for target in (current+1,current-1):
            if target<1 or target>total_pages or target==current or target in targets:
                continue
            targets.append(target)
        if not targets:
            return
        self._grid_page_warm_token=int(getattr(self,"_grid_page_warm_token",0) or 0)+1
        token=self._grid_page_warm_token
        def worker():
            try:
                for target in targets:
                    if getattr(self,"_screen_closed",False) or token!=int(getattr(self,"_grid_page_warm_token",0) or 0):
                        break
                    with self._prepared_page_lock:
                        if target in self._prepared_page_cache or target in self._prepared_page_pending:
                            continue
                        self._prepared_page_pending.add(target)
                    try:
                        payload=self._prepare_grid_page_resilient(target,None)
                        if payload and token==int(getattr(self,"_grid_page_warm_token",0) or 0):
                            self._cache_prepared_page(target,payload)
                        else:
                            with self._prepared_page_lock:self._prepared_page_pending.discard(target)
                    except Exception as exc:
                        with self._prepared_page_lock:self._prepared_page_pending.discard(target)
                        optional_failure("ui.poster_grid_catalogue_warm",exc)
            finally:
                if token==int(getattr(self,"_grid_page_warm_token",0) or 0):
                    self._grid_page_warm_running=False
        # A newer page transition supersedes an older neighbour plan.  We do not
        # cancel the provider call mid-socket; the token simply prevents stale
        # payloads from taking over the active warm set.
        if getattr(self,"_grid_page_warm_running",False):
            return
        self._grid_page_warm_running=True
        try:_GRID_PAGE_WARM_EXECUTOR.submit(worker)
        except Exception as exc:
            self._grid_page_warm_running=False;optional_failure("ui.poster_grid_catalogue_warm_submit",exc)

    def _apply_prepared_page(self,target,payload,restore_index=None):
        # Stage 8 latest-page-wins gate.  If the remote has already advanced the
        # virtual destination, an older page may finish in the meantime but must
        # never flash on screen.  Keep it as a warm RAM page and wait for the
        # actual destination instead.
        try:
            _nav_target=int(getattr(self,"_grid_page_nav_target",0) or 0)
            if self.media_type in ("vod","series") and _nav_target and int(target)!=_nav_target:
                if isinstance(payload,dict):self._cache_prepared_page(int(target),payload)
                return False
        except Exception as exc:optional_failure("ui.grid_page_nav_stale_result",exc)
        if self._grid_is_m3u_source() and self.media_type in ("vod","series"):
            payload=self._refresh_prepared_m3u_art_from_hdd(payload)
        valid=list((payload or {}).get("items") or [])
        if not valid and target>1:
            # Initial-open self-heal: a remembered page can become invalid after
            # catalogue shrinkage or after an older build shared page numbers
            # between presentations with different page sizes. Never leave a
            # fully constructed grid showing stars/frames with no titles/posters.
            # Once a real page has painted, normal NEXT-page "No more items"
            # behavior is preserved exactly.
            if not getattr(self,"_initial_grid_paint_done",False) and not getattr(self,"grid_items",None):
                self._restore_page=1;self._restore_index=0
                try:
                    _state=dict(_GRID_NAV_STATE.get(self._session_nav_key,{}) or {})
                    _state.update({"page":1,"index":0})
                    _GRID_NAV_STATE[self._session_nav_key]=_state
                except Exception as exc:optional_failure("ui.grid_initial_restore_reset",exc)
                self["status"].setText(_("Loading page %d...") % 1)
                self.load_page(1,0)
                return False
            self["status"].setText(_("No more items"))
            if int(getattr(self,"_grid_page_nav_target",0) or 0)==int(target):
                self._grid_page_nav_target=0;self._grid_page_nav_index=0
            return False
        if not valid and target==1:
            # Never navigate BACK on the viewer's behalf.  Keep the child grid
            # surface open after bounded + isolated recovery and show an honest
            # empty/error state; BACK remains an explicit user action.
            message=_("No content")
            self.page=1;self.grid_items=[];self.index=0
            self._prepared_titles_for_render=[];self._prepared_fitted_for_render=[];self._prepared_meta_for_render=[];self._prepared_art_for_render=[]
            try:self._render_grid()
            except Exception as exc:optional_failure("ui.grid_initial_empty_render",exc)
            for _n in ("selection","selection_adaptive"):
                try:self[_n].hide()
                except Exception:pass
            try:self["status"].setText(message);self["page_label"].setText(_("No content"))
            except Exception:pass
            if int(getattr(self,"_grid_page_nav_target",0) or 0)==int(target):
                self._grid_page_nav_target=0;self._grid_page_nav_index=0
            return False
        self.page=target;self.grid_items=valid
        self._initial_grid_paint_done=True
        state_rows=list((payload or {}).get("states") or [])
        self._grid_item_state={}
        for item,row in zip(self.grid_items,state_rows):self._grid_item_state[id(item)]=dict(row or {})
        self._prepared_titles_for_render=list((payload or {}).get("titles") or [])
        self._prepared_fitted_for_render=list((payload or {}).get("fitted") or [])
        self._prepared_meta_for_render=list((payload or {}).get("meta") or [])
        self._prepared_art_for_render=list((payload or {}).get("art") or [])
        self.index=max(0,min(int(restore_index or 0),len(valid)-1)) if valid else 0
        self._grid_full_chrome_warm=False
        _perf49_render_t0=time.monotonic()
        self._render_grid();self._remember_grid_state()
        try:
            if self.media_type in ("vod","series") and int(getattr(self,"_grid_page_nav_target",0) or 0)==int(target):
                self._grid_page_nav_target=0;self._grid_page_nav_index=0
        except Exception:pass
        try:
            import logging
            logging.getLogger("UltraStalker").info(
                "PERF49 grid_first_paint media=%s mode=%s page=%d items=%d render_ms=%d",
                str(self.media_type or ""),self.__class__.__name__,int(self.page or 0),len(self.grid_items),int((time.monotonic()-_perf49_render_t0)*1000)
            )
        except Exception:
            pass
        _mem34("grid_page_painted", mode=self.__class__.__name__, media=str(self.media_type or ""), page=int(self.page or 0), items=len(self.grid_items), visual_ram=len(getattr(self,"_page_visual_ram",{}) or {}))
        self._mem34_schedule_settled()
        try:_ui_diag("ui_page_ready",screen="grid",media_type=self.media_type,page=self.page,items=len(self.grid_items),
                     elapsed_ms=int(max(0.0,(time.monotonic()-float(getattr(self,"_ui_diag_page_request_mono",time.monotonic())))*1000.0)))
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1171",exc)
        total=int(self._portal_total or 0)
        total_pages=max(1,(total+self.page_size-1)//self.page_size) if total else max(1,self.page)
        absolute=min(total,(self.page-1)*self.page_size+self.index+1) if total else ((self.page-1)*self.page_size+self.index+1)
        kind="Channel" if self.media_type=="itv" else ("Movie" if self.media_type=="vod" else "Series")
        self["status"].setText("")
        self["page_label"].setText(_("Page %d / %d  •  %s %d / %d")%(self.page,total_pages,kind,absolute,total or absolute))
        # release zero-button artwork: first paint remains cache-first, then only
        # the visible cards are queued for poster hydration. No adjacent pages or
        # folder-wide scan is started here.
        try:self._start_progressive_poster_prefetch()
        except Exception as exc:optional_failure("ui.auto_visible_page",exc)
        # R49: Backdrop Grid opts in to a metadata-only visible rating pass.
        # It runs after first paint on METADATA_EXECUTOR, so the GUI/navigation
        # path stays untouched and all eight visible scores can fill without
        # requiring the user to stop on every title.
        if bool(getattr(self,"visible_card_rating_prefetch",False)) and self.media_type in ("vod","series"):
            # Give cache-first poster decode a short head start, then complete
            # missing visible rating/year metadata automatically.  Poster Grid 1
            # opts into two bounded retry sweeps because cold poster identity can
            # finish just after the first metadata pass; previously those last
            # two/three cards only completed after the user focused them.
            try:
                _rating_generation=int(getattr(self,"_page_prefetch_generation",0) or 0)
                def _start_rating_sweep(gen=_rating_generation, tag="initial"):
                    if self._screen_closed:return
                    if gen!=int(getattr(self,"_page_prefetch_generation",0) or 0):return
                    try:self._prefetch_visible_tmdb_metadata()
                    except Exception as exc:optional_failure("ui.visible_rating_page_%s"%tag,exc)
                reactor.callLater(0.45,_start_rating_sweep)
                _retry_delays=tuple(getattr(self,"visible_card_rating_retry_delays",()) or ())
                for _retry_no,_delay in enumerate(_retry_delays):
                    def _retry(gen=_rating_generation, tag="retry%d"%(_retry_no+1)):
                        _start_rating_sweep(gen,tag)
                    reactor.callLater(float(_delay),_retry)
            except Exception as exc:
                optional_failure("ui.visible_rating_page_schedule",exc)
        try:
            import logging
            logging.getLogger("UltraStalker").info(
                "PERF50 page_background_started media=%s mode=%s page=%d rating_pass=%s rating_delay_ms=%d",
                str(self.media_type or ""),self.__class__.__name__,int(self.page or 0),bool(getattr(self,"visible_card_rating_prefetch",False) and self.media_type in ("vod","series")),450 if bool(getattr(self,"visible_card_rating_prefetch",False) and self.media_type in ("vod","series")) else 0
            )
        except Exception:
            pass
        # release: same catalogue-only neighbour warming that made Cinematic
        # page changes immediate.  It runs after first paint and never touches
        # artwork, so remote-control movement keeps GUI priority.
        try:self._warm_poster_grid_catalogue_pages()
        except Exception as exc:optional_failure("ui.poster_grid_catalogue_warm_start",exc)
        return True

    def load_page(self,page,restore_index=None):
        if self.media_type=="itv":
            _live_restart_trace("page_request",page=int(page or 1),current_page=int(getattr(self,"page",0) or 0),
                                index=int(getattr(self,"index",0) or 0),generation=int(getattr(self,"_grid_generation",0) or 0),
                                inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        if self.media_type in ("vod","series") and self._view_active and isinstance(self._view_items,list):
            self._load_folder_view_page(page,restore_index);return
        target=max(1,int(page))
        if self.media_type=="series":
            self._stop_series_hierarchy_prefetch()
        self._ui_diag_page_request_mono=time.monotonic();self._ui_diag_page_target=target;self._ui_diag_first_poster_logged=False
        with self._prepared_page_lock:
            prepared=self._prepared_page_cache.pop(target,None)
        # Never trust a cached empty first page.  An empty portal/API response is
        # indistinguishable from the transient loader/cache miss that previously
        # left a fully drawn but content-less grid on screen.
        if prepared is not None and target==1 and not list((prepared or {}).get("items") or []):
            prepared=None
        if prepared is not None:
            self._apply_prepared_page(target,prepared,restore_index)
            return
        # Page navigation is an explicit user action. Clear only transient
        # breaker state left by cancelled previews/prefetch workers.
        try:self.client.reset_failure_backoff()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        if self._busy:
            self._pending_page_request=(target,restore_index)
            old=getattr(self,"_page_load_handle",None)
            if old is not None:
                try: old.cancel()
                except Exception as exc: optional_failure("ui.page_load_cancel", exc)
            # This screen serializes only its portal page request. The cancelled
            # worker receives its own cancel_event and exits without touching
            # sockets used by other screens.
            self._busy=False
        self._pending_page_request=None
        # Keep the last-good page visually clean during VOD/Series page hops.
        # Initial category open still shows Loading because there is no useful
        # surface to preserve yet.
        if self.media_type in ("vod","series") and getattr(self,"grid_items",None):
            self["status"].setText("")
        else:
            self["status"].setText(_("Loading page %d...")%target)
        # Cancel the previous page as soon as navigation starts, not only after
        # the next portal response arrives. Downloads already reading data abort
        # at the next chunk boundary and no new external enrichment is started.
        try:
            old_cancel=getattr(self,"_page_prefetch_cancel",None)
            if old_cancel is not None:old_cancel.set()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._cancel_detached_page_prefetch_futures()
        for future in list(getattr(self,"_page_prefetch_futures",[]) or []):
            try:future.cancel()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._page_prefetch_futures=[]
        self._page_prefetch_cancel=threading.Event()
        try:self._page_prefetch_generation+=1
        except Exception:self._page_prefetch_generation=1
        request_generation=self._page_prefetch_generation
        try:
            with self._page_prefetch_lock:
                self._page_prefetch_seen.clear(); self._visible_tmdb_pending.clear(); self._page_quality_seen.clear(); self._page_art_retry_count.clear()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        _runtime_endurance_log("grid_page_request",media_type=self.media_type,page=target,prefetch_generation=request_generation)
        def ok(data):
            if request_generation!=int(getattr(self,"_page_prefetch_generation",0) or 0):return
            # Network result is prepared off the GUI thread before this callback
            # whenever possible. Fallback preparation here is only for page 1 or
            # a cache miss and is still shared with the RAM-prefetch path.
            payload=self._prepare_grid_page(target,None) if not isinstance(data,dict) else data
            self._apply_prepared_page(target,payload,restore_index)
            _runtime_endurance_log("grid_page",media_type=self.media_type,page=self.page,items=len(self.grid_items),prefetch_generation=self._page_prefetch_generation)
        def failed(e):
            if request_generation==int(getattr(self,"_page_prefetch_generation",0) or 0):
                # Silent self-heal owns normal one-shot network failures. Only
                # expose a final error after all bounded attempts really fail.
                # If an older page is still painted, keep it visually clean.
                kind=_catalogue_error_kind(e)
                if kind=="transient" and getattr(self,"grid_items",None):
                    # A page-change failure never destroys the last-good page.
                    self["status"].setText("")
                else:
                    message=_("Content temporarily unavailable. Try again.") if kind=="transient" else _friendly_error(e)
                    self["status"].setText(message)
                    # On an initial category open there is no last-good grid to
                    # preserve.  Do not leave a blank poster/backdrop/cinematic
                    # surface: return to the already-painted Categories screen
                    # and carry the final error back to its status line.
                    if not getattr(self,"grid_items",None):
                        # Do not auto-pop back to Categories.  Preserve the child
                        # screen so the viewer sees the real failure and chooses
                        # when to go back.
                        try:
                            self.grid_items=[];self.index=0
                            self._prepared_titles_for_render=[];self._prepared_fitted_for_render=[];self._prepared_meta_for_render=[];self._prepared_art_for_render=[]
                            self._render_grid()
                            for _n in ("selection","selection_adaptive"):
                                try:self[_n].hide()
                                except Exception:pass
                            self["page_label"].setText(_("Categories could not be loaded"))
                        except Exception as exc:optional_failure("ui.grid_initial_error_hold",exc)
                if int(getattr(self,"_grid_page_nav_target",0) or 0)==int(target):
                    self._grid_page_nav_target=0;self._grid_page_nav_index=0
                _runtime_endurance_log("grid_page_error",media_type=self.media_type,page=target,error_kind=kind)
        self._page_load_handle=self._run_async(lambda handle:self._prepare_grid_page_resilient(target,handle.cancel_event),ok,failed)

    def _prepare_initial_page_isolated_rescue(self,target,cancel_event=None):
        """One final fresh-client rescue for an initial category open.

        The Browser and Grid normally share the authenticated client.  Some
        receivers/portals leave that transport in a transient loader state even
        though a fresh session succeeds immediately.  Fetch only the native
        pages needed for the first visible grid into this screen's RAM cache,
        then run the normal local preparation path.  The shared client is never
        replaced or cancelled.
        """
        if int(target or 1)!=1 or getattr(self,"grid_items",None):
            return None
        isolated=None
        try:
            timeout=min(7,max(3,int((self._grid_settings or {}).get("timeout",10) or 10)))
            isolated=_new_isolated_source_client(dict(self.profile or {}),timeout=timeout)
            def fetch_native(native_page):
                if cancel_event is not None and cancel_event.is_set():
                    raise _ArtworkCancelled()
                result=isolated.ordered_page(self.media_type,self.genre,native_page,cancel_event=cancel_event) or {}
                items=[x for x in result.get("items",[]) if isinstance(x,dict)]
                if (not items and self._grid_is_m3u_source() and self.media_type in ("vod","series")
                        and str(getattr(self,"category_title","") or "").strip()
                        and str(getattr(self,"category_title","") or "").strip()!=str(self.genre or "").strip()):
                    try:
                        alt=isolated.ordered_page(self.media_type,str(self.category_title).strip(),native_page,cancel_event=cancel_event) or {}
                        alt_items=[x for x in alt.get("items",[]) if isinstance(x,dict)]
                        if alt_items:result=alt;items=alt_items
                    except Exception as exc:optional_failure("ui.grid_isolated_m3u_title_retry",exc)
                return result,items
            result,items=fetch_native(1)
            native=max(1,int(result.get("page_size") or len(items) or self.page_size or 1))
            total=max(0,int(result.get("total") or 0))
            if not items:
                return None
            self._portal_page_cache.clear()
            self._portal_page_size=native
            self._portal_total=total
            self._portal_page_cache[1]=items
            start=0;end=max(1,int(self.page_size or 1))
            last=((max(start,end-1))//native)+1
            for native_page in range(2,last+1):
                result_n,items_n=fetch_native(native_page)
                self._portal_total=max(self._portal_total,int(result_n.get("total") or 0))
                if items_n:self._portal_page_cache[native_page]=items_n
            payload=self._prepare_grid_page(1,cancel_event)
            if list((payload or {}).get("items") or []):
                _runtime_endurance_log("grid_initial_isolated_rescue",media_type=self.media_type,page=1,items=len((payload or {}).get("items") or []))
                return payload
        except _ArtworkCancelled:
            raise
        except Exception as exc:
            optional_failure("ui.grid_initial_isolated_rescue",exc)
        finally:
            if isolated is not None:
                try:isolated.close()
                except Exception:pass
        return None

    def _prepare_grid_page_resilient(self,target,cancel_event=None):
        """Retry only transient catalogue reads inside the same grid screen.

        This intentionally mirrors the user's successful manual second-arrow /
        BACK-and-enter recovery, but does it without repainting an error between
        attempts. Artwork/TMDB/player work is not retried here.
        """
        last_error=None
        delays=(0.0,0.18,0.42,0.75)
        for attempt,delay in enumerate(delays):
            if cancel_event is not None and cancel_event.is_set():
                raise _ArtworkCancelled()
            if attempt:
                if cancel_event is not None and getattr(cancel_event,"wait",lambda _x:False)(delay):
                    raise _ArtworkCancelled()
                try:
                    close_pending=getattr(self.client,"cancel_pending_requests",None)
                    if callable(close_pending):close_pending()
                except Exception as exc:optional_failure("ui.grid_retry_transport",exc)
                try:
                    reset=getattr(self.client,"reset_failure_backoff",None)
                    if callable(reset):reset()
                except Exception as exc:optional_failure("ui.grid_retry_backoff",exc)
                # Match the proven Categories recovery path.  On the third
                # transient attempt establish one fresh authorized portal
                # session; this is the programmatic equivalent of the manual
                # refresh/re-enter that users report immediately succeeding.
                if attempt==2:
                    try:
                        authorize=getattr(self.client,"authorize",None)
                        if callable(authorize):
                            try:authorize(cancel_event=cancel_event)
                            except TypeError:authorize()
                    except Exception as exc:
                        last_error=exc
                        optional_failure("ui.grid_retry_authorize",exc)
            try:
                payload=self._prepare_grid_page(target,cancel_event)
                page_items=list((payload or {}).get("items") or [])
                if target==1 and not page_items:
                    # Successful-but-empty first responses are a known MAG/Xtream
                    # failure mode.  Drop only the per-screen catalogue page
                    # cache and advance this portal's API cache generation once,
                    # then let the existing bounded retry/reauthorize lane run.
                    if attempt < len(delays)-1:
                        try:self._portal_page_cache.clear()
                        except Exception:pass
                        self._portal_page_size=0;self._portal_total=0
                        if not getattr(self,"_grid_empty_generation_bumped",False):
                            try:
                                bump=getattr(self.client,"_bump_content_cache_generation",None)
                                if callable(bump):bump()
                            except Exception as bump_exc:
                                optional_failure("ui.grid_empty_cache_generation",bump_exc)
                            self._grid_empty_generation_bumped=True
                        last_error=Exception("Empty catalogue response")
                        _runtime_endurance_log("grid_page_empty_retry",media_type=self.media_type,page=target,attempt=attempt+1)
                        continue
                    # Final shared-client empty on initial open: make one clean
                    # isolated transport attempt before accepting an empty page.
                    rescue=self._prepare_initial_page_isolated_rescue(target,cancel_event)
                    if rescue is not None:return rescue

                # PERFLAB18: do not accept a short grid page as End Of Folder
                # when total_items says the page should still be full.  This is
                # exactly the 2357-item / native-page-57 failure: 56*14 = 784,
                # then one grid card painted and navigation falsely stopped.
                total=max(0,int(self._portal_total or 0))
                start=max(0,(int(target)-1)*int(self.page_size or 1))
                expected=min(int(self.page_size or 1),max(0,total-start)) if total>start else 0
                if expected>0 and len(page_items)<expected:
                    try:self._portal_page_cache.clear()
                    except Exception:pass
                    last_error=Exception("Premature catalogue page: got %d of %d items"%(len(page_items),expected))
                    _runtime_endurance_log("grid_page_short_retry",media_type=self.media_type,page=target,attempt=attempt+1,got=len(page_items),expected=expected,total=total)
                    if attempt < len(delays)-1:
                        continue
                    raise last_error
                return payload
            except _ArtworkCancelled:
                raise
            except Exception as exc:
                last_error=exc
                kind=_catalogue_error_kind(exc)
                _runtime_endurance_log("grid_page_retry",media_type=self.media_type,page=target,attempt=attempt+1,error_kind=kind)
                if kind!="transient" or attempt>=len(delays)-1:
                    if attempt>=len(delays)-1 and target==1 and not getattr(self,"grid_items",None):
                        rescue=self._prepare_initial_page_isolated_rescue(target,cancel_event)
                        if rescue is not None:return rescue
                    raise
        raise last_error or Exception("Catalogue request failed")

    def _render_grid(self):
        if self.media_type=="itv":
            _live_restart_trace("render_begin",page=int(getattr(self,"page",0) or 0),index=int(getattr(self,"index",0) or 0),
                                generation=int(getattr(self,"_grid_generation",0) or 0),
                                items=len(getattr(self,"grid_items",[]) or []),inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        self._grid_cancel_queued_downloads()
        self._grid_generation+=1;self._grid_decode_queue=[];self._grid_waiting={};self._grid_queued=set();self._grid_slot_paths={};self._grid_slot_identities={};self._grid_palette_sources={};self._grid_card_paths={};self._grid_display_titles={};self._grid_visual_locks={};self._grid_canonical_poster_slots={}
        cfg=self._grid_settings or {}
        self._grid_prepared_art={pos:row for pos,row in enumerate(getattr(self,"_prepared_art_for_render",[]) or [])}
        for pos in range(self.page_size):
            title_widget=self["item_title%d"%pos];meta_widget=self["item_meta%d"%pos];art=self["art%d"%pos]
            if pos>=len(self.grid_items):
                title_widget.setText("");meta_widget.setText("")
                widgets=[art,title_widget,meta_widget]
                if self.media_type in ("vod","series"):
                    for _n in ("item_age%d"%pos,"item_year%d"%pos,"item_star%d"%pos,"item_score%d"%pos):
                        try:widgets.append(self[_n])
                        except Exception:pass
                try:widgets.append(self["card_chrome%d"%pos])
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                for widget in widgets:
                    try:widget.hide()
                    except Exception as exc:optional_failure("ui",exc)
                continue
            item=self.grid_items[pos];raw=item.get("name") or item.get("title") or item.get("id") or _("Result")
            prepared_titles=getattr(self,"_prepared_titles_for_render",[]) or []
            title=prepared_titles[pos] if pos<len(prepared_titles) else self._display_item_title(item,"Item")
            self._grid_display_titles[pos]=title
            prepared_fitted=getattr(self,"_prepared_fitted_for_render",[]) or []
            fitted_row=prepared_fitted[pos] if pos<len(prepared_fitted) else None
            if self.media_type=="itv":
                title_widget.setText((fitted_row[0] if fitted_row else one_line(title,36)))
            else:
                if fitted_row:
                    fitted_title,fitted_font=fitted_row
                else:
                    fitted_title,fitted_font=_grid_fit_card_title(title,226)
                title_widget.setText(fitted_title)
                try:
                    if title_widget.instance is not None:
                        title_widget.instance.setFont(gFont("Regular",int(fitted_font)))
                except Exception as exc:optional_failure("ui.grid_title_autofit",exc)
            prepared_meta=getattr(self,"_prepared_meta_for_render",[]) or []
            _meta_text=prepared_meta[pos] if pos<len(prepared_meta) else self._card_meta(item,pos)
            meta_widget.setText(_meta_text)
            if self.media_type in ("vod","series"):
                self._card_rating_layout(item,pos,_meta_text)
            if self.media_type in ("vod","series"):
                try:
                    chrome=self["card_chrome%d"%pos]
                    if chrome.instance is not None:chrome.instance.setPixmapFromFile(asset(getattr(self,"_poster_neutral_card_asset","poster_card_full_neutral.png")))
                    chrome.show()
                except Exception as exc:optional_failure("ui.grid_neutral_card",exc)
            for widget in (art,title_widget):
                try:widget.show()
                except Exception as exc:optional_failure("ui",exc)
            if self.media_type not in ("vod","series") or (self._folder_rating_value(item)<=0 and self._folder_year_value(item)<=0):
                try:meta_widget.show()
                except Exception as exc:optional_failure("ui",exc)
        visible_art=[]
        selected_row, selected_col = divmod(self.index, self.columns)
        for pos,item in enumerate(self.grid_items[:self.page_size]):
            row, col = divmod(pos, self.columns)
            distance = abs(row - selected_row) + abs(col - selected_col)
            visible_art.append((distance, pos, item))
        visible_art.sort(key=lambda pair: (pair[0], pair[1]))
        for _distance,pos,item in visible_art:
            self._grid_request_art(pos,item,self.placeholder)
        # PERFLAB9: BLUE/previous-page work may have already persisted the
        # canonical poster while this page still owns a placeholder/prepared
        # visual.  Do one bounded HDD-only pass for the visible page after every
        # render.  No network is started here; it only paints posters that are
        # already physically cached, so a card no longer needs remote focus to
        # wake up its image.
        if self.media_type in ("vod","series"):
            try:self._poll_visible_poster_cache()
            except Exception as exc:optional_failure("ui.perflab9_cached_page_repaint",exc)
        self._update_selection()
        if self.media_type in ("vod","series"):
            try:self._grid_apply_current_selector(allow_build=True)
            except Exception as exc:optional_failure("ui.grid_initial_adaptive",exc)
        if self.media_type=="itv":
            _live_restart_trace("render_end",page=int(getattr(self,"page",0) or 0),index=int(getattr(self,"index",0) or 0),
                                generation=int(getattr(self,"_grid_generation",0) or 0),
                                slots=len(getattr(self,"_grid_slot_paths",{}) or {}),inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        self._prepared_titles_for_render=[];self._prepared_fitted_for_render=[];self._prepared_meta_for_render=[];self._prepared_art_for_render=[]

    def _card_rating_layout(self,item,pos,meta_text=None):
        """Render YEAR + packaged gold star + score without touching artwork/network.

        The three widgets are screen-resident, so navigation only changes text/visibility;
        the star pixmap itself is loaded once by the skin and stays stable like a label.
        """
        if self.media_type not in ("vod","series"):
            return False
        try:
            year=int(self._folder_year_value(item) or 0)
            score=float(self._folder_rating_value(item))
        except Exception:
            year=0;score=-1.0
        try:
            meta=self["item_meta%d"%pos];aw=self["item_age%d"%pos];yw=self["item_year%d"%pos];sw=self["item_star%d"%pos];rw=self["item_score%d"%pos]
        except Exception:
            return False
        # Always reset both rendering paths before painting.  This prevents stale
        # Enigma2 surfaces from stacking after returning from Details/Player.
        try:meta.hide()
        except Exception:pass
        for w in (aw,yw,sw,rw):
            try:w.hide()
            except Exception:pass
        try:aw.setText("");yw.setText("");rw.setText("")
        except Exception:pass
        age=display_certification(item.get("certification") or item.get("age_rating"))
        has_age=bool(age)
        has_year=year>0
        has_score=score>0.0
        if has_score and score>10.0:score=10.0
        if has_age:
            try:aw.setText(age);aw.show()
            except Exception:pass
        if has_year:
            try:yw.setText(str(year));yw.show()
            except Exception:pass
        if has_score:
            try:rw.setText("%.1f"%score);sw.show();rw.show()
            except Exception:pass
        if has_age or has_year or has_score:
            return True
        if meta_text is not None:
            try:meta.setText(meta_text)
            except Exception:pass
        try:meta.show()
        except Exception:pass
        return False

    def _refresh_favorite_button(self,item=None,state=None):
        """Reflect the selected item's cached favorite state on the green key.

        Navigation must stay RAM-only, so this deliberately uses _grid_item_state
        populated with the page instead of querying SQLite on every arrow press.
        """
        try:
            if item is None:
                if not self.grid_items:return
                item=self.grid_items[max(0,min(int(self.index),len(self.grid_items)-1))]
            if state is None:
                state=(getattr(self,"_grid_item_state",{}) or {}).get(id(item),{})
            favorite=bool((state or {}).get("favorite")) if isinstance(state,dict) else bool(state)
            self["green"].setText(_("Unfavorite") if favorite else _("Favorite"))
        except Exception as exc:
            optional_failure("ui.favorite_button_state",exc)

    def _card_meta(self,item,pos):
        bits=[]
        if self.media_type=="itv":
            absolute=max(1,(int(self.page or 1)-1)*int(self.page_size or 1)+int(pos)+1)
            return str(absolute)
        raw=item.get("name") or item.get("title") or ""
        state=self._grid_item_state.get(id(item),{})
        explicit=self._explicit_item_quality(item)
        cached=str(state.get("quality") or "")
        badges=explicit or cached or quality_badges(raw)
        if badges:bits.append(badges)
        if item.get("year"):bits.append(str(item.get("year"))[:4])
        if state.get("favorite"):bits.append("★")
        if state.get("completed") and (self._grid_settings or {}).get("show_watched",True):bits.append("WATCHED")
        elif state.get("position") and state.get("duration"):bits.append("%d%%"%min(99,int(state["position"]*100.0/state["duration"])))
        return "  •  ".join(bits)[:28]

    def _update_selection(self):
        if not self.grid_items:return
        self.index=max(0,min(self.index,len(self.grid_items)-1))
        if self.media_type in ("vod","series"):
            try:self._reset_focus_heavy_artwork()
            except Exception as exc:optional_failure("ui.focus_heavy_nav_reset",exc)
        # Zero-work navigation rule: never touch poster/chrome pixmaps here.
        # Remember the old focused slot and restore it only after focus settles.
        try:
            prev_slot=int(getattr(self,"_grid_focus_chrome_slot",-1))
            if prev_slot>=0 and prev_slot!=self.index:self._grid_pending_prev_focus=prev_slot
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1344",exc)
        try:
            # Native coordinates were calculated once when the grid screen opened.
            # No getDesktop(), floating-point scale or geometry calculation per key.
            px,py=self._scaled_card_positions[self.index]
            self["selection"].instance.move(ePoint(px,py))
            self["selection"].show()
            if self.media_type in ("vod","series"):
                self["selection_adaptive"].instance.move(ePoint(px,py))
        except Exception as exc:optional_failure("ui",exc)
        self._remember_grid_state()
        self._update_header(self.grid_items[self.index])
        self._refresh_favorite_button(self.grid_items[self.index])
        if self.media_type in ("vod","series"):
            # Beta58: if this title was already queued as a normal visible job,
            # promote that exact queued job to selected priority instead of
            # submitting duplicate TMDB work. This is RAM-only.
            try:
                selected_key=self._page_visual_key(self.grid_items[self.index])
                if selected_key:_FAST_POSTER_EXECUTOR.reprioritize("hydrate:%s"%selected_key,0)
            except Exception as exc:optional_failure("ui.selected_reprioritize",exc)
            # Selection colour follows the newly focused poster immediately.
            # This is cache-only on a warm card; a miss schedules one background
            # overlay build while leaving the previous overlay visible.
            try:self._grid_apply_current_selector()
            except Exception as exc:optional_failure("ui.grid_selector_immediate",exc)
            # Keep the cursor/header synchronous and cheap.  The adaptive
            # background/laser and selected-detail nudge are intentionally
            # delayed so a fast LEFT/RIGHT/UP/DOWN run does not spawn work for
            # every card crossed on the way.
            try:
                self._nav_burst_until=time.monotonic()+0.36
                self._grid_focus_timer.stop();self._grid_focus_timer.start(360,True)
                # Test69 Lean: no speculative Series hierarchy network call.
            except Exception:
                self._apply_debounced_grid_focus()
            try:self._detail_prefetch_timer.stop()
            except Exception as exc:optional_failure("ui",exc)

    def _stop_series_hierarchy_prefetch(self):
        try:self._series_prefetch_timer.stop()
        except Exception as exc:optional_failure("ui.series_prefetch_timer_stop",exc)
        try:self._series_hierarchy_prefetch_cancel.set()
        except Exception as exc:optional_failure("ui.series_prefetch_cancel",exc)
        try:self._series_hierarchy_prefetch_token+=1
        except Exception:self._series_hierarchy_prefetch_token=1

    def _prefetch_selected_series_hierarchy(self):
        """Test69 Lean: Series hierarchy is fetched only when Details/Episodes needs it."""
        return

    def _apply_debounced_grid_focus(self):
        if self._screen_closed or not self.grid_items or self.media_type not in ("vod","series"):
            return
        # Test71: after focus settles, restore the previous card's normal
        # adaptive chrome, then build/apply a selected-clean chrome for the
        # current card. The laser stays a separate OUTER overlay, so the poster
        # itself keeps its full 206x310 geometry with no inner double frame.
        try:
            prev=int(getattr(self,"_grid_pending_prev_focus",-1))
            if prev>=0 and prev!=self.index:
                p=(getattr(self,"_grid_palette_sources",{}).get(prev) or self._grid_slot_paths.get(prev))
                if p and os.path.isfile(p):self._grid_schedule_adaptive_selector(self._grid_generation,prev,p)
        except Exception as exc:optional_failure("ui.grid_restore_prev_chrome",exc)
        self._grid_pending_prev_focus=-1
        self._grid_apply_current_selector(allow_build=True)
        try:self._apply_poster_live_hud(self.grid_items[self.index])
        except Exception as exc:optional_failure("ui.poster_hud_debounced",exc)
        # release: after focus is stable, hydrate this title's poster + real
        # backdrop automatically.  Rapid navigation never reaches this point.
        try:self._prefetch_focused_artwork()
        except Exception as exc:optional_failure("ui.auto_focus_hydrate",exc)

    def _warm_idle_grid_page(self):
        """Adaptive focus has its own debounce; no page-wide idle warming."""
        return

    def _stop_grid_idle_warm_timer(self):
        try:self._grid_idle_warm_timer.stop()
        except Exception as exc:optional_failure("ui.grid_idle_warm_stop",exc)
        try:
            if self._grid_idle_warm_conn is not None:self._grid_idle_warm_conn.disconnect()
        except Exception as exc:optional_failure("ui.grid_idle_warm_disconnect",exc)
        try:
            if self._warm_idle_grid_page in self._grid_idle_warm_timer.callback:self._grid_idle_warm_timer.callback.remove(self._warm_idle_grid_page)
        except Exception as exc:optional_failure("ui.grid_idle_warm_callback",exc)

    def _stop_grid_focus_timer(self):
        try:self._grid_focus_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            if self._grid_focus_timer_conn is not None:self._grid_focus_timer_conn.disconnect()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            if self._apply_debounced_grid_focus in self._grid_focus_timer.callback:self._grid_focus_timer.callback.remove(self._apply_debounced_grid_focus)
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _stop_detail_prefetch(self):
        try:self._detail_prefetch_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._detail_prefetch_conn is not None:self._detail_prefetch_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._prefetch_selected_details in self._detail_prefetch_timer.callback:self._detail_prefetch_timer.callback.remove(self._prefetch_selected_details)
        except Exception as exc:optional_failure("ui.prefetch_timer_callback",exc)
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._cancel_detached_page_prefetch_futures()
        for future in list(getattr(self,"_page_prefetch_futures",[]) or []):
            try:future.cancel()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._page_prefetch_futures=[]

    def _prefetch_selected_details(self):
        # Navigation enrichment is strictly selected-card only. Neighbours are
        # loaded when focus actually reaches them, keeping the GUI thread and HDD
        # quiet while the user moves through a large catalogue.
        self._prefetch_visible_page_details(priority_index=self.index,max_items=1)

    def _explicit_item_quality(self, item):
        cache=getattr(self,"_explicit_quality_cache",None)
        key=id(item)
        if isinstance(cache,dict) and key in cache:return cache[key]
        try:
            values=[]
            def walk(v,depth=0):
                if depth>4:return
                if isinstance(v,dict):
                    for k,x in v.items():
                        if str(k).lower() in ("password","token","cookie"):continue
                        walk(x,depth+1)
                elif isinstance(v,(list,tuple)):
                    for x in v[:40]:walk(x,depth+1)
                elif v is not None: values.append(str(v))
            walk(item if isinstance(item,dict) else {})
            result=normalize_quality(" ".join(values))
            if isinstance(cache,dict):cache[key]=result
            return result
        except Exception:
            if isinstance(cache,dict):cache[key]=""
            return ""

    def _discover_item_quality(self, item, media_type, cancel_event=None):
        """Discover catalogue quality without starting playback.

        Artwork/network failures must never suppress this path.  We inspect the
        full portal rows first, then resolve the first episode/link only as a
        metadata probe.  No service is started.
        """
        q=self._explicit_item_quality(item)
        if q:return q
        def cancelled():
            return bool(cancel_event is not None and getattr(cancel_event,"is_set",lambda:False)())
        try:
            if media_type=="series" and not cancelled():
                seasons=self.client.series_seasons(item,cancel_event=cancel_event) or []
                for season in seasons[:3]:
                    if cancelled():return ""
                    q=self._explicit_item_quality(season)
                    if q:return q
                    episodes=self.client.series_episodes(item,season,cancel_event=cancel_event) or []
                    for ep in episodes[:8]:
                        q=self._explicit_item_quality(ep)
                        if q:return q
                    if episodes and not cancelled():
                        try:
                            link=self.client.create_link(episodes[0],"series",series_id=item.get("id") or item.get("series_id"),cancel_event=cancel_event)
                            q=normalize_quality("%s %s"%(self._explicit_item_quality(episodes[0]),str(link or "")))
                            if q:return q
                        except Exception as exc:optional_failure("ui.series_quality_link_probe",exc)
            elif media_type=="vod" and not cancelled():
                try:
                    link=self.client.create_link(item,"vod",cancel_event=cancel_event)
                    q=normalize_quality(str(link or ""))
                    if q:return q
                except Exception as exc:optional_failure("ui.vod_quality_link_probe",exc)
        except Exception as exc:
            if not cancelled():optional_failure("ui.quality_discovery",exc)
        return ""

    # Portal artwork is completely disabled for VOD/Series. TMDB artwork only.
    def _stop_visible_poster_watch(self):
        try:self._visible_poster_watch_timer.stop()
        except Exception as exc:optional_failure("ui.visible_poster_watch_stop",exc)
        try:
            if self._visible_poster_watch_conn is not None:self._visible_poster_watch_conn.disconnect()
        except Exception as exc:optional_failure("ui.visible_poster_watch_disconnect",exc)
        try:
            if self._poll_visible_poster_cache in self._visible_poster_watch_timer.callback:self._visible_poster_watch_timer.callback.remove(self._poll_visible_poster_cache)
        except Exception as exc:optional_failure("ui.visible_poster_watch_callback",exc)

    def _start_visible_poster_watch(self):
        """Test69 Lean: provider/BLUE queues repaint explicitly; no 120ms HDD scanner."""
        return

    def _poll_visible_poster_cache(self):
        """Paint newly persisted visible posters without any network activity."""
        if self._screen_closed or self.media_type not in ("vod","series"):return
        try:
            generation=int(getattr(self,"_grid_generation",0) or 0)
            current_items=list(self.grid_items[:self.page_size])
            for pos,item in enumerate(current_items):
                if not isinstance(item,dict):continue

                # Already displaying a real local poster: nothing to do.
                existing=str((getattr(self,"_grid_slot_paths",{}) or {}).get(pos) or "")
                if existing and os.path.isfile(existing):
                    # Ignore shipped placeholders; they are local files too.
                    bn=os.path.basename(existing).lower()
                    if not ("placeholder" in bn or bn.startswith("poster_movie") or bn.startswith("poster_series")):
                        continue

                poster=""
                snapshot={}
                try:
                    snapshot=load_artwork_v2_manifest(self.profile,self.media_type,item) or {}
                    candidate=str(snapshot.get("poster_local") or "")
                    if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>256:
                        poster=candidate
                except Exception:
                    snapshot={}

                if not poster:
                    try:
                        bundle=_load_visual_bundle(self.profile,self.media_type,item,snapshot) or {}
                        candidate=str(bundle.get("poster") or bundle.get("grid_thumb") or "")
                        if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>256:
                            poster=candidate
                    except Exception as exc:
                        diagnostic_failure("ui.grid.failsoft.poster_bundle",exc)

                if not poster:
                    continue

                display=poster
                try:
                    thumb=self._grid_poster_thumb_path(poster) or ""
                    if thumb and _valid_cache_file(thumb,ttl=0):display=thumb
                except Exception:display=poster

                # Recheck that this same item still occupies this slot.
                if generation!=int(getattr(self,"_grid_generation",0) or 0):return
                if pos>=len(self.grid_items) or self.grid_items[pos] is not item:
                    # Item objects can be copied by some views; compare stable key.
                    try:
                        from .persistent_cache import content_cache_key
                        if content_cache_key(self.profile,self.media_type,self.grid_items[pos]) != content_cache_key(self.profile,self.media_type,item):
                            continue
                    except Exception:
                        continue
                self._grid_palette_sources[pos]=poster
                if display==poster:
                    # No thumb yet: first paint now, thumbnail later. Never run
                    # Pillow synchronously from the 120ms UI watcher.
                    self._grid_use_persistent_poster(generation,pos,poster)
                else:
                    self._grid_queue_decode(generation,pos,display)
                    if pos==getattr(self,"index",-1):self._grid_schedule_page_mood(poster)
                self._remember_grid_visual(pos,display=display,poster=poster,palette=poster)
        except Exception as exc:
            optional_failure("ui.visible_poster_watch",exc)

    def _prefetch_visible_page_details(self, priority_index=None, max_items=None):
        """Hydrate visible posters with a detached, HDD-first page worker.

        The serialized HDD worker and any network miss workers capture only
        immutable/page-local values plus result queues. They never retain the
        Grid Screen, so closing or flipping pages cannot leave a stale Screen
        alive behind slow disk/network work.
        """
        if self._screen_closed or self.media_type not in ("vod","series") or not self.grid_items:return
        generation=int(getattr(self,"_page_prefetch_generation",0) or 0)
        cancel_event=getattr(self,"_page_prefetch_cancel",None)
        rows=list(enumerate(self.grid_items[:self.page_size]))
        if priority_index is None:priority_index=self.index
        try:priority_index=int(priority_index)
        except Exception:priority_index=0
        rows.sort(key=lambda pair:(0 if pair[0]==priority_index else 1,abs(pair[0]-priority_index),pair[0]))
        if max_items is not None:
            try:rows=rows[:max(1,int(max_items))]
            except Exception:pass

        selected=[]
        for pos,item in rows:
            if not isinstance(item,dict):continue
            key=self._page_visual_key(item)
            if not key:continue
            try:
                with self._page_prefetch_lock:
                    if key in self._page_prefetch_seen:continue
                    self._page_prefetch_seen.add(key)
            except Exception:continue
            selected.append((pos,dict(item),key))
        if not selected:return

        profile=dict(self.profile or {});media_type=str(self.media_type or "")
        cfg=dict(self._grid_settings or {});client=self.client
        result_q=self._art_prefetch_jobs;future_q=self._page_prefetch_future_jobs
        selected_index=int(priority_index)

        def submit_miss(pos,it,key):
            if cancel_event is not None and cancel_event.is_set():return
            def hydrate_visible(it=dict(it),k=key,gen=generation,cancel=cancel_event):
                if cancel is not None and cancel.is_set():return False
                poster,data=_progressive_poster_item_detached(profile,media_type,it,cfg,client,cancel)
                if cancel is not None and cancel.is_set():return False
                if poster and os.path.isfile(poster):
                    result_q.put((gen,k,poster,{
                        "tmdb_id":data.get("tmdb_id") if isinstance(data,dict) else None,
                        "media_type":(data.get("media_type") if isinstance(data,dict) else None) or ("tv" if media_type=="series" else "movie"),
                        "rating":(data.get("rating") or data.get("vote_average")) if isinstance(data,dict) else None,
                        "year":(data.get("year") or data.get("release_date") or data.get("first_air_date")) if isinstance(data,dict) else None,
                        "release_date":(data.get("release_date") or data.get("first_air_date") or "") if isinstance(data,dict) else "",
                        "certification":(data.get("certification") or data.get("age_rating") or "") if isinstance(data,dict) else "",
                        "age_rating":(data.get("age_rating") or data.get("certification") or "") if isinstance(data,dict) else "",
                        "backdrop_local":data.get("backdrop_local") if isinstance(data,dict) else None,
                        "palette_source":poster,
                    }))
                    return True
                # A transient visible-card miss used to leave this key marked as
                # already-prefetched forever.  That made the card stay blank until
                # focus triggered the separate full-artwork path.  Feed the miss
                # into the existing bounded retry lane: it clears the seen marker
                # and retries this one visible slot once, never as a page-wide loop.
                result_q.put((gen,k,"__RETRY__",None))
                return False
            try:
                priority=0 if pos==selected_index else 1
                # release: visible posters have their own small 3-worker lane.
                # This keeps first-open pages fast without contending with the
                # independent TMDb metadata lane or opening 14 downloads at once.
                future=_VISIBLE_POSTER_EXECUTOR.submit(hydrate_visible,_task_key="grid-poster:%x:%s:%s"%(id(self),generation,key))
                future_q.put((generation,key,future))
            except Exception:
                result_q.put((generation,key,"__RETRY__",None))

        def hdd_batch():
            for pos,it,key in selected:
                if cancel_event is not None and cancel_event.is_set():return
                try:
                    snap=load_artwork_v2_manifest(profile,media_type,it) or {}
                    poster=str(snap.get("poster_local") or "")
                    meta={
                        "tmdb_id":snap.get("tmdb_id"),
                        "media_type":snap.get("media_type") or ("tv" if media_type=="series" else "movie"),
                        "rating":snap.get("rating") or snap.get("vote_average"),
                        "year":snap.get("year") or snap.get("release_date") or snap.get("first_air_date"),
                        "release_date":snap.get("release_date") or snap.get("first_air_date") or "",
                        "certification":snap.get("certification") or snap.get("age_rating") or "",
                        "age_rating":snap.get("age_rating") or snap.get("certification") or "",
                        "backdrop_local":snap.get("backdrop_local"),
                        "palette_source":poster if poster and os.path.isfile(poster) else "",
                    }
                    # release: publish cached TMDb metadata even when artwork is
                    # absent/stale.  The GUI drain treats this as a metadata-only
                    # result and the normal miss lane may still resolve artwork.
                    if meta.get("tmdb_id") or meta.get("rating") or meta.get("year") or meta.get("certification"):
                        result_q.put((generation,key,poster if poster and os.path.isfile(poster) and os.path.getsize(poster)>256 else "",meta))
                    if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                        continue
                except Exception:pass
                if cancel_event is not None and cancel_event.is_set():return
                submit_miss(pos,it,key)
        try:
            future=_VISIBLE_POSTER_HDD_EXECUTOR.submit(hdd_batch,_task_key="grid-hdd:%x:%s"%(id(self),generation))
            self._page_prefetch_futures.append(future)
        except Exception as exc:
            for _pos,_it,key in selected:
                try:
                    with self._page_prefetch_lock:self._page_prefetch_seen.discard(key)
                except Exception:pass
            optional_failure("ui.visible_hdd_batch_submit",exc)

    def _update_header(self,item):
        raw=item.get("name") or item.get("title") or _("Result")
        title=(getattr(self,"_grid_display_titles",{}) or {}).get(self.index)
        if title is None:title=self._display_item_title(item,"Item")
        self["title"].setText(_clean_display_text(title,44))
        if self.media_type=="itv":
            cid=str(item.get("id") or item.get("ch_id") or "");epg=self._epg_cache.get(cid,{})
            now=epg.get("now") or item.get("epg_title") or item.get("now") or _("EPG information will appear here")
            nxt=epg.get("next") or ""
            self["rating"].setText(_("NOW  %s")%_clean_display_text(now,90))
            self["meta1"].setText((_("NEXT")+"  "+_clean_display_text(nxt,90)) if nxt else "")
            badges=[]
            if item.get("allow_archive") or item.get("tv_archive_duration") or item.get("archive"):badges.append(_("Catch-up"))
            state=self._grid_item_state.get(id(item),{})
            if state.get("favorite"):badges.append(_("Favorite"))
            self["meta2"].setText("  •  ".join(badges))
            self._schedule_epg(item)
        else:
            # Movies/Series top rating/meta widgets are intentionally hidden in
            # the current HUD. Do not scan quality/metadata or resize glass here.
            # A remote-control arrow must only change the visible title text.
            return

    def _fit_poster_live_hud(self):
        if self.media_type not in ("vod","series"):return
        try:
            sx=float(getDesktop(0).size().width())/1920.0;sy=float(getDesktop(0).size().height())/1080.0
            def fit(bg,label,value,cx,minw,maxw,y,h,fs,pad,minfs):
                value=str(value or "")
                # Better Arabic/Latin width estimate than raw character count.
                units=max(1.0,sum(1.45 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in value))
                usable=max(1.0,float(maxw-24))
                fitted_fs=int(fs)
                expected=units*fitted_fs*0.57
                if expected>usable:
                    fitted_fs=max(int(minfs),min(int(fs),int(usable/(units*0.57))))
                w=max(minw,min(maxw,int(units*fitted_fs*0.57+pad)));x=int(cx-w/2)
                self[bg].instance.move(ePoint(int(x*sx),int(y*sy)));self[bg].instance.resize(eSize(int(w*sx),int(h*sy)))
                self[label].instance.move(ePoint(int((x+12)*sx),int((y+6)*sy)));self[label].instance.resize(eSize(int((w-24)*sx),int((h-12)*sy)))
                try:self[label].instance.setFont(gFont("Regular",fitted_fs))
                except Exception as exc:optional_failure("ui.poster_hud_font_fit",exc)
            fit("poster_folder_bg","section",self["section"].getText(),670,250,650,38,54,18,58,12)
            fit("poster_title_bg","title",self["title"].getText(),1215,250,590,38,54,22,64,13)
            fit("poster_counter_bg","page_label",self["page_label"].getText(),1650,260,430,936,46,18,52,12)
        except Exception as exc:optional_failure("ui.poster_hud_fit",exc)

    def _apply_poster_hud_chrome(self, source, chrome):
        if self.media_type not in ("vod","series") or not isinstance(chrome,dict):return
        # Counter uses the exact same floating glass family as the top folder card.
        for ck,wk in (("counter","poster_folder_bg"),("channel_top","poster_title_bg"),("clock","poster_clock_bg"),("counter","poster_counter_bg")):
            path=chrome.get(ck)
            if path and os.path.isfile(path):
                try:self[wk].instance.setPixmapFromFile(path);self[wk].show()
                except Exception as exc:optional_failure("ui.poster_hud_apply",exc)
        self._poster_hud_last=dict(chrome);self._poster_hud_source=str(source or "")
        self._fit_poster_live_hud()

    def _drain_poster_live_hud(self):
        if self.media_type not in ("vod","series"):return
        while True:
            try:source,chrome=self._poster_hud_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            self._poster_hud_pending.discard(source)
            # Only repaint for the currently selected poster. Old jobs may finish
            # after rapid navigation and must never yank the HUD colour backwards.
            pos=max(0,min(int(self.index or 0),len(self.grid_items)-1)) if self.grid_items else 0
            current=str((getattr(self,"_grid_palette_sources",{}) or {}).get(pos) or "")
            if source==current:self._apply_poster_hud_chrome(source,chrome)

    def _apply_poster_live_hud(self, item=None):
        if self.media_type not in ("vod","series"):return
        try:
            pos=max(0,min(int(self.index or 0),len(self.grid_items)-1)) if self.grid_items else 0
            source=str((getattr(self,"_grid_palette_sources",{}) or {}).get(pos) or "")
            if not source or not os.path.isfile(source):
                return
            self._fit_poster_live_hud()
            if source==getattr(self,"_poster_hud_source","") and getattr(self,"_poster_hud_last",None):
                # Same-source is not proof that the native Pixmap is still painted.
                # OpenBH may retain our Python cache marker after a hide/show or
                # child-screen transition while the adaptive glass surface itself
                # is gone. Rebind the already-cached chrome instead of returning.
                self._apply_poster_hud_chrome(source,self._poster_hud_last)
                return
            key="poster_hud_"+hashlib.sha1(source.encode("utf-8","ignore")).hexdigest()[:16]
            expected={
                "counter":os.path.join(THUMB_CACHE_DIR,"posterhud_clean_%s_counter.png"%key),
                "channel_top":os.path.join(THUMB_CACHE_DIR,"posterhud_clean_%s_channel_top.png"%key),
                "clock":os.path.join(THUMB_CACHE_DIR,"posterhud_clean_%s_clock.png"%key),
            }
            if all(_valid_cache_file(p,ttl=0) for p in expected.values()):
                self._apply_poster_hud_chrome(source,expected)
                return

            # beta59: restore the adaptive folder/title/clock/counter HUD without
            # putting Pillow back on the navigation path.  This method is already
            # called only after the 300 ms stable-focus debounce; a single-worker
            # adaptive lane builds one missing HUD family in the background, while
            # rapid LEFT/RIGHT browsing remains cache-only.
            if source in getattr(self,"_poster_hud_pending",set()):
                return
            self._poster_hud_pending.add(source)
            def worker(src=source,hkey=key):
                try:
                    chrome=_build_poster_adaptive_chrome_clean(src,hkey) or {}
                    if chrome:self._poster_hud_jobs.put((src,chrome))
                    else:self._poster_hud_jobs.put((src,{}))
                except Exception as exc:
                    optional_failure("ui.poster_hud_background_build",exc)
                    try:self._poster_hud_jobs.put((src,{}))
                    except Exception as queue_exc:optional_failure("ui.poster_hud_background_queue",queue_exc)
            try:_ADAPTIVE_EXECUTOR.submit(worker)
            except Exception as exc:
                self._poster_hud_pending.discard(source)
                optional_failure("ui.poster_hud_background_submit",exc)
        except Exception as exc:optional_failure("ui.poster_live_hud_cache_only",exc)

    def _stop_grid_page_nav(self):
        try:self._grid_page_nav_timer.stop()
        except Exception:pass
        try:
            if self._grid_page_nav_conn is not None:self._grid_page_nav_conn.disconnect()
        except Exception:pass
        try:
            if self._commit_grid_page_nav in self._grid_page_nav_timer.callback:self._grid_page_nav_timer.callback.remove(self._commit_grid_page_nav)
        except Exception:pass
        self._grid_page_nav_target=0;self._grid_page_nav_index=0

    def _grid_nav_total_pages(self):
        total=int(self._active_total() or 0)
        # Zero means the provider did not publish a catalogue total.  Preserve
        # R217's open-ended next-page behavior instead of pretending the current
        # page is the last one.
        return max(1,(total+int(self.page_size or 1)-1)//int(self.page_size or 1)) if total else 0

    def _grid_virtual_page(self):
        return int(getattr(self,"_grid_page_nav_target",0) or getattr(self,"page",1) or 1)

    def _grid_virtual_index(self):
        if int(getattr(self,"_grid_page_nav_target",0) or 0):
            return max(0,int(getattr(self,"_grid_page_nav_index",0) or 0))
        return max(0,int(getattr(self,"index",0) or 0))

    def _queue_grid_page_target(self,target,restore_index=0,delay_ms=110):
        """Coalesce rapid VOD/Series page hops into one final provider request."""
        if self.media_type not in ("vod","series"):
            self.load_page(target,restore_index);return
        total_pages=self._grid_nav_total_pages();target=max(1,int(target or 1))
        if total_pages>0:target=min(target,total_pages)
        restore_index=max(0,int(restore_index or 0))
        # If a burst returns to the page that is still painted, cancel the queued
        # page request and apply only the requested local index immediately.
        if target==int(getattr(self,"page",1) or 1) and not bool(getattr(self,"_busy",False)):
            try:self._grid_page_nav_timer.stop()
            except Exception:pass
            self._grid_page_nav_target=0;self._grid_page_nav_index=0
            if self.grid_items:
                self.index=min(restore_index,max(0,len(self.grid_items)-1));self._update_selection();self._update_page_counter()
            return
        self._grid_page_nav_target=target;self._grid_page_nav_index=restore_index
        self._grid_page_nav_generation=int(getattr(self,"_grid_page_nav_generation",0) or 0)+1
        try:self._grid_page_nav_timer.stop();self._grid_page_nav_timer.start(max(1,int(delay_ms)),True)
        except Exception:self._commit_grid_page_nav()

    def _queue_grid_page_delta(self,delta,restore_index=0,wrap=True):
        total_pages=self._grid_nav_total_pages();base=self._grid_virtual_page();step=int(delta or 0)
        if total_pages==1:return
        target=base+step
        if total_pages>1:
            if wrap:target=((target-1)%total_pages)+1
            else:target=max(1,min(target,total_pages))
        else:
            # Unknown provider total: page forward freely and clamp backward at 1,
            # matching the original on-demand catalogue behavior.
            target=max(1,target)
            if target==base:return
        self._queue_grid_page_target(target,restore_index,110)

    def _commit_grid_page_nav(self):
        target=int(getattr(self,"_grid_page_nav_target",0) or 0)
        if target<=0 or getattr(self,"_screen_closed",False):return
        index=max(0,int(getattr(self,"_grid_page_nav_index",0) or 0))
        # Keep the virtual target alive while the provider request is in flight.
        # More key presses can replace it; load_page's generation gate will then
        # discard/cancel the older request before it can paint.
        self.load_page(target,index)

    def _nav_allowed(self):
        now=time.monotonic()
        # Stage 8: never discard a real DirectionActions event.  Heavy artwork,
        # metadata and adaptive work already sit behind stable-focus timers; the
        # arrow hot path should therefore only mark the burst and move focus.
        self._nav_burst_until=now+0.30
        self._last_nav_at=now
        return True

    def _update_page_counter(self):
        total = int(self._active_total() or 0)
        total_pages = max(1, (total + self.page_size - 1) // self.page_size) if total else 1
        absolute = min(total, (self.page - 1) * self.page_size + self.index + 1) if total else (0 if not self.grid_items else ((self.page - 1) * self.page_size + self.index + 1))
        kind = "Channel" if self.media_type == "itv" else ("Movie" if self.media_type == "vod" else "Series")
        try:self["page_label"].setText(_("Page %d / %d  •  %s %d / %d") % (self.page, total_pages, kind, absolute, total or absolute))
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def move_left(self):
        if self.grid_items and self._nav_allowed():self.index=max(0,self.index-1);self._update_selection();self._update_page_counter()
    def move_right(self):
        if self.grid_items and self._nav_allowed():self.index=min(len(self.grid_items)-1,self.index+1);self._update_selection();self._update_page_counter()
    def move_up(self):
        if not self.grid_items or not self._nav_allowed():
            return
        # Normal in-page movement remains immediate. If a page hop is already
        # queued, repeated boundary presses advance the virtual page instead of
        # repeatedly loading the page still painted underneath it.
        if not int(getattr(self,"_grid_page_nav_target",0) or 0) and self.index >= self.columns:
            self.index -= self.columns
            self._update_selection();self._update_page_counter()
            return
        column=self.index % self.columns
        restore=max(0,self.page_size-self.columns+column)
        if self.media_type in ("vod","series"):
            self._queue_grid_page_delta(-1,restore,wrap=True);return
        if self.page > 1:
            self.load_page(self.page - 1,restore);return
        total=int(self._active_total() or 0)
        if total > 0:
            last_page=max(1,(total+self.page_size-1)//self.page_size);self.load_page(last_page,self.page_size-1)
        else:
            self.index=len(self.grid_items)-1;self._update_selection();self._update_page_counter()

    def move_down(self):
        if not self.grid_items or not self._nav_allowed():
            return
        target = self.index + self.columns
        if not int(getattr(self,"_grid_page_nav_target",0) or 0) and target < len(self.grid_items):
            self.index = target
            self._update_selection();self._update_page_counter()
            return
        column = self.index % self.columns
        if self.media_type in ("vod","series"):
            self._queue_grid_page_delta(1,column,wrap=True);return
        total=int(self._active_total() or 0)
        total_pages=max(1,(total+self.page_size-1)//self.page_size) if total else 0
        if total_pages and self.page >= total_pages:
            self.load_page(1,0);return
        self.load_page(self.page + 1,column)

    def next_page(self):
        column=self.index % self.columns if self.grid_items else 0
        if self.media_type in ("vod","series"):
            self._queue_grid_page_delta(1,column,wrap=False);return
        self.load_page(self.page + 1,column)

    def previous_page(self):
        column=self.index % self.columns if self.grid_items else 0
        if self.media_type in ("vod","series"):
            self._queue_grid_page_delta(-1,max(0,self.page_size-self.columns+column),wrap=False);return
        if self.page <= 1:return
        self.load_page(self.page - 1,max(0,self.page_size-self.columns+column))

    def open_selected(self):
        if self._busy or not self.grid_items:return
        item=self.grid_items[self.index]
        if self.media_type=="itv":
            self._play_live(item)
        else:
            self._remember_grid_state()
            self._pause_grid_background_for_child()
            # Carry the exact poster palette source used by the grid into Details.
            # This makes List -> Details one continuous adaptive identity.
            detail_item=dict(item) if isinstance(item,dict) else item
            try:
                if isinstance(detail_item,dict):
                    # The screen that visibly owns the selected title is the
                    # identity/logo authority handed into Details/Player.
                    # R111 speed: the focused card already carries verified identity
                    # in RAM after normal hydration.  Reuse it before touching HDD.
                    state=(getattr(self,"_grid_item_state",{}) or {}).get(id(item),{})
                    locked=(detail_item.get("_locked_tmdb_id") or state.get("tmdb_id") or detail_item.get("tmdb_id"))
                    if locked in (None,""):
                        try:
                            record_reader=getattr(self,"_record",None)
                            row=record_reader(item) if callable(record_reader) else {}
                            locked=(row or {}).get("_locked_tmdb_id") or (row or {}).get("tmdb_id")
                        except Exception:
                            locked=None
                    # PGV2 seal is now fallback-only. A live locked id is newer/stronger
                    # than a rebuildable HDD seal and avoids a file read on normal OK.
                    if locked in (None,""):
                        try:
                            seal_reader=getattr(self,"_pgv2_read_logo_seal",None)
                            seal=seal_reader(item) if callable(seal_reader) else {}
                            locked=(seal or {}).get("tmdb_id")
                        except Exception:
                            locked=None
                    if locked not in (None,""):
                        detail_item["_locked_tmdb_id"]=locked;detail_item["tmdb_id"]=locked
                palette_source=(getattr(self,"_grid_palette_sources",{}).get(self.index) or self._grid_slot_paths.get(self.index) or "")
                visible_poster=self._visible_player_poster_path(item,self.index)
                if isinstance(detail_item,dict) and visible_poster:
                    detail_item["_player_poster"]=str(visible_poster)
                    detail_item["_ultra_palette_source"]=str(visible_poster)
                    detail_item["_ultra_poster_source"]=str(visible_poster)
                    detail_item["_adaptive_source_local"]=str(visible_poster)
                    _poster_key=self._page_visual_key(item) if isinstance(item,dict) else ""
                    if _poster_key:detail_item["_player_poster_identity"]=_poster_key
                # Never fall back to a naked palette slot here.  A slot without
                # identity proof can belong to the previous title.
                    detail_item["_ultra_poster_source"]=str(palette_source)
                    detail_item["_player_poster"]=str(palette_source)
                # release instant-open handoff for Series: Details paints first,
                # then its normal async metadata/artwork lanes may enrich in-place.
                # Avoid synchronous HDD snapshot/bundle reads in the constructor.
                if isinstance(detail_item,dict) and self.media_type=="series":
                    detail_item["_ultra_fast_open"]=True
                    fast_bundle={}
                    if palette_source and os.path.isfile(str(palette_source)):
                        fast_bundle["poster"]=str(palette_source)
                    back=str(detail_item.get("_backdrop_source_local") or detail_item.get("_cin_provider_backdrop_local") or "")
                    if back and os.path.isfile(back):
                        fast_bundle["backdrop"]=back
                    detail_item["_ultra_fast_bundle"]=fast_bundle
            except Exception as exc:
                optional_failure("ui.grid_palette_handoff",exc)
            self.session.openWithCallback(self._details_returned,ContentDetailsScreen,self.profile,self.client,self.media_type,detail_item)

    def _pause_grid_background_for_child(self):
        try:self._stop_focus_heavy_artwork()
        except Exception:pass
        for name in ("_grid_focus_timer","_detail_prefetch_timer","_grid_idle_warm_timer"):
            timer=getattr(self,name,None)
            if timer is not None:
                try:timer.stop()
                except Exception as exc:diagnostic_failure("ui.grid.failsoft.1974",exc)
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1978",exc)
        self._cancel_detached_page_prefetch_futures()
        for future in list(getattr(self,"_page_prefetch_futures",[]) or []):
            try:future.cancel()
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.1981",exc)
        self._page_prefetch_futures=[]

    def _resume_grid_background_after_child(self):
        try:self._reset_focus_heavy_artwork()
        except Exception:pass
        self._page_prefetch_generation=int(getattr(self,"_page_prefetch_generation",0) or 0)+1
        self._page_prefetch_cancel=threading.Event()
        self._page_prefetch_seen=set()
        self._visible_tmdb_pending=set()
        # No immediate page-wide warm on BACK. Selected-card enrichment is enough;
        # neighbours resolve lazily as the user moves.
        try:self._detail_prefetch_timer.start(700,True)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1991",exc)

    def _details_returned(self,result=None):
        # Return to the exact poster and page that opened the details screen.
        _r57_return_mono=time.monotonic()
        target_page=max(1,int(getattr(self,"_restore_page",self.page) or 1))
        target_index=max(0,int(getattr(self,"_restore_index",self.index) or 0))
        if self.page != target_page or not self.grid_items:
            self.load_page(target_page,target_index)
            return
        self.index=max(0,min(target_index,len(self.grid_items)-1)) if self.grid_items else 0
        if self.grid_items:
            states=load_content_states(self.profile,self.media_type,self.grid_items)
            qualities=load_content_qualities(self.profile,self.media_type,self.grid_items) if self.media_type in ("vod","series") else ["" for _ in self.grid_items]
            self._grid_item_state={}
            for item,state,quality in zip(self.grid_items,states,qualities):
                row=dict(state or {});row["quality"]=quality or "";self._grid_item_state[id(item)]=row
            # Refresh text/state in place.  BACK from Details/Player must stay
            # RAM-only: Stage 3 already remembers the exact decoded visual for
            # every hydrated slot in _page_visual_ram.  Older code reopened a
            # detail snapshot + artwork bundle for every visible card here,
            # turning one BACK key into 14/27 synchronous HDD reads.
            for pos,item in enumerate(self.grid_items[:self.page_size]):
                try:
                    _meta=self._card_meta(item,pos)
                    self["item_meta%d"%pos].setText(_meta)
                    if self.media_type in ("vod","series"):
                        self._card_rating_layout(item,pos,_meta)
                except Exception as exc:optional_failure("ui.grid_return_meta",exc)
                try:
                    visual=self._page_visual_get(item) or {}
                    display=str(visual.get("display") or "")
                    poster=str(visual.get("poster") or display or "")
                    palette=str(visual.get("palette") or poster or "")
                    card=str(visual.get("card") or "")
                    if display:
                        self._grid_slot_paths[pos]=display
                        if palette:self._grid_palette_sources[pos]=palette
                        self._grid_visual_locks[pos]=display
                        self._grid_queue_decode(self._grid_generation,pos,display)
                    if card:
                        self._grid_card_paths[pos]=card
                        try:
                            chrome=self["card_chrome%d"%pos]
                            if chrome.instance is not None:chrome.instance.setPixmapFromFile(card)
                            chrome.show()
                        except Exception as card_exc:optional_failure("ui.grid_return_card_ram",card_exc)
                except Exception as exc:optional_failure("ui.grid_return_ram_visual",exc)
            # R57: Cinematic is not a poster-card grid.  Its native surfaces are
            # restored by PremiumGlobalCinematicScreen._grid_shown_resume(), so
            # never probe poster-only card_chrome/_grid_card_paths on BACK.
            _r57_cinematic=(self.__class__.__name__=="PremiumGlobalCinematicScreen")
            if not _r57_cinematic:
                # Enigma2 can leave the previously focused pixmap surface stale
                # after the child screen closes. Rebind/show the selected slot now,
                # instead of waiting for a RIGHT/LEFT key to trigger a repaint.
                try:
                    slot=self.index
                    for name in ("art%d"%slot,"item_title%d"%slot,"card_chrome%d"%slot):
                        try:self[name].show()
                        except Exception as exc:diagnostic_failure("ui.grid.failsoft.2031",exc)
                    try:
                        _meta=self._card_meta(self.grid_items[slot],slot)
                        self["item_meta%d"%slot].setText(_meta)
                        self._card_rating_layout(self.grid_items[slot],slot,_meta)
                    except Exception as exc:optional_failure("ui.grid_return_rating_repaint",exc)
                    visual=self._page_visual_get(self.grid_items[slot]) or {}
                    art_path=str(visual.get("display") or self._grid_slot_paths.get(slot) or "")
                    if art_path:
                        art=self["art%d"%slot]
                        if art.instance is not None:art.instance.setPixmapFromFile(art_path)
                        art.show()
                    card=str(visual.get("card") or (getattr(self,"_grid_card_paths",{}) or {}).get(slot) or "")
                    if card:
                        chrome=self["card_chrome%d"%slot]
                        if chrome.instance is not None:chrome.instance.setPixmapFromFile(card)
                        chrome.show()
                    self._grid_focus_chrome_slot=slot
                except Exception as exc:optional_failure("ui.grid_return_repaint",exc)
                try:self._grid_apply_current_selector()
                except Exception as exc:optional_failure("ui.grid_return_selector",exc)
            self._update_selection()
            self._resume_grid_background_after_child()
            try:
                logging.getLogger("UltraStalker").info(
                    "PERF57 series_child_return media=%s mode=%s page=%s index=%s cinematic=%s elapsed_ms=%s",
                    self.media_type,self.__class__.__name__,self.page,self.index,_r57_cinematic,
                    int((time.monotonic()-_r57_return_mono)*1000.0))
            except Exception:pass
        else:
            self._update_selection()
            self._resume_grid_background_after_child()

    def _play_live(self,item):
        command=item.get("cmd") or item.get("command") or item.get("url")
        if not command:self["status"].setText(_("No stream command"));return
        self["status"].setText(_("Creating stream link..."))
        def ok(url):
            url=url if isinstance(url,str) else ""
            name=str(item.get("name") or item.get("title") or "Live channel");add_recently_played(self.profile,"itv",item)
            engine=_configured_playback_engine(self._grid_settings or {})
            selected_page=self.page; selected_index=self.index
            def returned(result=None):
                self["status"].setText(_("Player closed"))
                # Keep the channel selected and make sure no player service is
                # resurrected behind the grid.
                if self.page != selected_page or not self.grid_items:
                    self.load_page(selected_page,selected_index)
                else:
                    self.index=max(0,min(selected_index,len(self.grid_items)-1))
                    self._update_selection()
            payload=_player_payload(item,self.profile,media_type="itv")
            payload["_player_client_ref"]=self.client;payload["_live_client_ref"]=self.client
            self.session.openWithCallback(returned,UltraStalkerPlayer,url.strip(),name,"itv",engine,payload)
        self._run_async(lambda handle:self.client.create_link(item,"itv",cancel_event=handle.cancel_event),ok,lambda e:ok(""))

    def toggle_selected_favorite(self):
        if not self.grid_items:return
        item=self.grid_items[self.index];state=toggle_favorite(self.profile,self.media_type,item);self["status"].setText(_("Added to favorites") if state else _("Removed from favorites"))
        cached=self._grid_item_state.setdefault(id(item),{'favorite':False,'position':0,'duration':0,'completed':0});cached['favorite']=bool(state)
        self._refresh_favorite_button(item,cached)
        try:
            self["item_meta%d" % self.index].setText(self._card_meta(self.grid_items[self.index], self.index))
        except Exception as exc:
            optional_failure("ui", exc)
        self._update_header(self.grid_items[self.index])

    @staticmethod
    def _home_hero_old_generated_paths(hero):
        paths=[]
        if not isinstance(hero,dict):return paths
        value=str(hero.get("prepared") or "")
        if value:paths.append(value)
        for key in ("home_mood","adaptive_focus"):
            block=hero.get(key) if isinstance(hero.get(key),dict) else {}
            for candidate in block.values():
                candidate=str(candidate or "")
                if candidate:paths.append(candidate)
        return paths

    def _selected_cached_backdrop_for_hero(self):
        """Return the current selected item's already-cached HDD backdrop only."""
        if not self.grid_items:return "",{}
        item=self.grid_items[self.index] if 0 <= int(self.index) < len(self.grid_items) else None
        if not isinstance(item,dict):return "",{}
        candidates=[]
        for key in ("_backdrop_source_local","_cin_provider_backdrop_local","backdrop_local","display_backdrop_local","source_backdrop_local"):
            candidates.append(item.get(key))
        # Cinematic keeps the canonical row/package for the focused item in RAM.
        try:
            row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index)) or {}
            if isinstance(row,dict):
                candidates.extend([row.get("backdrop_local"),row.get("display_backdrop_local")])
        except Exception:pass
        # HDD manifest is read-only here; MENU must never trigger a network resolve.
        manifest={}
        try:manifest=load_artwork_v2_manifest(self.profile,self.media_type,item) or {}
        except Exception:manifest={}
        if isinstance(manifest,dict):
            candidates.extend([manifest.get("backdrop_local"),manifest.get("display_backdrop_local"),manifest.get("source_backdrop_local")])
        for candidate in candidates:
            try:
                candidate=str(candidate or "")
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>4096:
                    return os.path.realpath(candidate),manifest
            except Exception:pass
        return "",manifest

    def _pin_selected_as_home_hero(self):
        """Pin the focused Cinematic/Backdrop item as Home Hero from cached art only.

        This is the same manual-Hero contract used by Details: no TMDb/network work
        is started by MENU, and only the selected canonical HDD backdrop is used.
        """
        if self.media_type not in ("vod","series") or not self.grid_items:return
        item=self.grid_items[self.index]
        if not isinstance(item,dict):return
        source,manifest=self._selected_cached_backdrop_for_hero()
        if not source:
            try:self["status"].setText(_("Hero not set • selected backdrop is not cached on HDD"))
            except Exception:pass
            return
        item_snapshot=dict(item);media_type=str(self.media_type or "")
        raw_id=str(item_snapshot.get("tmdb_id") or item_snapshot.get("id") or item_snapshot.get("stream_id") or item_snapshot.get("series_id") or "")
        tmdb_id=item_snapshot.get("_locked_tmdb_id") or item_snapshot.get("tmdb_id") or (manifest.get("tmdb_id") if isinstance(manifest,dict) else None)
        title=str(item_snapshot.get("name") or item_snapshot.get("title") or item_snapshot.get("series_name") or "")
        overview=str(item_snapshot.get("plot") or item_snapshot.get("description") or item_snapshot.get("overview") or "")
        try:
            text=self["description"].getText() or ""
            if text:overview=text
        except Exception:pass
        try:
            text=self["bd_overview"].getText() or ""
            if text:overview=text
        except Exception:pass
        title_logo_local=""
        for candidate in (getattr(self,"_cin_logo_path",""),getattr(self,"_bd_logo_path",""),item_snapshot.get("title_logo_local")):
            try:
                candidate=str(candidate or "")
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate)>256:
                    title_logo_local=os.path.realpath(candidate);break
            except Exception:pass

        request_token=str(int(time.time()*1000000));hero_dir=os.path.dirname(str(HOME_HERO_FILE or ""))
        marker=os.path.join("/tmp","ultrastalker_home_hero_pending")
        try:
            with open(marker,"w",encoding="ascii") as fh:fh.write(request_token)
        except Exception:pass
        try:self["status"].setText(_("Hero selected • preparing adaptive materials…"))
        except Exception:pass

        def token_current():
            try:
                with open(marker,"r",encoding="ascii") as fh:return fh.read().strip()==request_token
            except Exception:return False

        def safe_unlink_generated(path,keep=()):
            try:
                path=str(path or "");base=os.path.basename(path)
                if not path or path in keep or not os.path.isfile(path):return
                if (os.path.realpath(path).startswith(os.path.realpath(hero_dir)+os.sep) or
                    base.startswith("dyn228home_") or base.startswith("dyn280home_")):
                    os.unlink(path)
            except OSError:pass
            except Exception:pass

        def worker():
            temp_home="";created=[]
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
                if not all(x and os.path.isfile(str(x)) and os.path.getsize(str(x))>100 for x in required):return False
                old_hero={}
                try:
                    with open(HOME_HERO_FILE,"r",encoding="utf-8") as fh:
                        old=json.load(fh);old_hero=old.get("hero") if isinstance(old,dict) and isinstance(old.get("hero"),dict) else {}
                except Exception:old_hero={}
                with _HOME_HERO_LOCK:
                    if not token_current():return False
                    final_home=os.path.join(hero_dir,"hero.png")
                    os.replace(temp_home,final_home);temp_home=""
                    hero={
                        "id":"manual-"+(raw_id or digest),"media_type":media_type,"tmdb_id":tmdb_id,
                        "title":title,"overview":overview,"rating":0,"language":"","collection":"ULTRA STALKER",
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
                for old_path in self._home_hero_old_generated_paths(old_hero):safe_unlink_generated(old_path,keep)
                try:
                    if token_current():os.unlink(marker)
                except OSError:pass
                return True
            except Exception as exc:
                optional_failure("ui.grid_set_home_hero",exc);return False
            finally:
                if temp_home and os.path.exists(temp_home):
                    try:os.unlink(temp_home)
                    except OSError:pass
                if not token_current():
                    for path in created:safe_unlink_generated(path)

        try:
            future=_IMAGE_EXECUTOR.submit(worker)
            def done(_f):
                try:ok=bool(_f.result())
                except Exception:ok=False
                def paint():
                    if getattr(self,"_screen_closed",False):return
                    try:self["status"].setText(_("Hero pinned • active across Ultra Stalker") if ok else _("Hero failed"))
                    except Exception:pass
                try:reactor.callFromThread(paint)
                except Exception:pass
            future.add_done_callback(done)
        except Exception as exc:
            optional_failure("ui.grid_set_home_hero_submit",exc)
            try:self["status"].setText(_("Hero failed: %s")%str(exc)[:90])
            except Exception:pass

    def show_information(self):
        if not self.grid_items:return
        item=self.grid_items[self.index]
        if self.media_type in ("vod","series"):
            detail_item=dict(item) if isinstance(item,dict) else item
            try:
                visible_poster=self._visible_player_poster_path(item,self.index)
                if isinstance(detail_item,dict) and visible_poster:
                    detail_item["_ultra_palette_source"]=str(visible_poster);detail_item["_ultra_poster_source"]=str(visible_poster);detail_item["_player_poster"]=str(visible_poster);detail_item["_adaptive_source_local"]=str(visible_poster)
                    _poster_key=self._page_visual_key(item) if isinstance(item,dict) else ""
                    if _poster_key:detail_item["_player_poster_identity"]=_poster_key
                if isinstance(item,dict) and isinstance(detail_item,dict):
                    for key in ("_backdrop_source_local","_cin_provider_backdrop_local","_cin_provider_poster_url","_cin_provider_backdrop_url"):
                        if item.get(key) not in (None,""):detail_item[key]=item.get(key)
            except Exception as exc:optional_failure("ui.grid_info_visual_handoff",exc)
            self.session.open(ContentDetailsScreen,self.profile,self.client,self.media_type,detail_item)
        else:
            cid=str(item.get("id") or item.get("ch_id") or "");epg=self._epg_cache.get(cid,{})
            text=_("%s\n\nNOW: %s\nNEXT: %s")%(self["title"].getText(),epg.get("now") or _("No EPG"),epg.get("next") or _("No EPG"))
            self.session.open(MessageBox,text,MessageBox.TYPE_INFO,timeout=12)

    def open_menu(self):
        choices=[(_("Refresh page"),"refresh"),(_("First page"),"first"),(_("Toggle favorite"),"favorite")]
        if self.media_type=="vod" and self.grid_items:
            state=self._grid_item_state.get(id(self.grid_items[self.index]),{})
            choices.append((_("Mark unwatched") if state.get("completed") else _("Mark watched"),"unwatch" if state.get("completed") else "watch"))
        self.session.openWithCallback(self._menu_selected,ChoiceBox,title=_("Grid options"),list=choices)
    def _menu_selected(self,choice):
        if not choice:return
        if choice[1]=="refresh":
            # R231: clear the exact provider first-paint URL used by both Xtream
            # and Stalker. Stalker public rows intentionally strip artwork and
            # keep it in _visible_provider_art_url, so _image_url(item) alone
            # could not clear the backoff that the visible poster worker uses.
            for item in self.grid_items:
                raw=(item.get("_visible_provider_art_url") or item.get("_rescue_provider_art_url") or _image_url(item)) if isinstance(item,dict) else None
                url = self._grid_absolute_url(raw,item) if raw else None
                if url:
                    _clear_artwork_failure(url)
            self.load_page(self.page)
        elif choice[1]=="first":self.load_page(1)
        elif choice[1]=="favorite":self.toggle_selected_favorite()
        elif choice[1]=="set_home_hero":self._pin_selected_as_home_hero()
        elif choice[1] in ("watch","unwatch") and self.grid_items:
            item=self.grid_items[self.index];watched=choice[1]=="watch";mark_watched(self.profile,self.media_type,item,watched)
            cached=self._grid_item_state.setdefault(id(item),{'favorite':False,'position':0,'duration':0,'completed':0});cached['completed']=1 if watched else 0
            if not watched:cached['position']=0
            try:self["item_meta%d"%self.index].setText(self._card_meta(item,self.index))
            except Exception as exc:optional_failure("ui",exc)
            self._update_header(item);self["status"].setText(_("Marked watched") if watched else _("Marked unwatched"))

    def _update_grid_clock(self):
        try:self["clock"].setText(time.strftime("%H:%M"))
        except Exception as exc:optional_failure("ui",exc)
        if self.media_type in ("vod","series"):
            try:self["date_label"].setText(time.strftime("%A, %d %B %Y"))
            except Exception as exc:optional_failure("ui.poster_grid_date",exc)
    def _stop_grid_clock(self):
        try:self._grid_clock.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._grid_clock_conn is not None:self._grid_clock_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._update_grid_clock in self._grid_clock.callback:self._grid_clock.callback.remove(self._update_grid_clock)
        except Exception as exc:optional_failure("ui",exc)

    def _schedule_epg(self,item):
        if self.media_type!="itv":return
        cid=str(item.get("id") or item.get("ch_id") or "")
        if not cid or cid in self._epg_cache:return
        self._epg_pending=cid
        try:self._epg_timer.stop();self._epg_timer.start(350,True)
        except Exception as exc:optional_failure("ui",exc)
    def _fetch_epg(self):
        cid=self._epg_pending
        if not cid:return
        def work():
            try:return (cid,epg_summary(self.client.epg(cid,4)),None)
            except Exception as exc:return (cid,None,exc)
        def done(fut):
            if getattr(self,"_screen_closed",False):return
            try:self._epg_jobs.put(fut.result())
            except Exception as exc:optional_failure("ui",exc)
        _GRID_EPG_EXECUTOR.submit(work).add_done_callback(done)
    def _drain_grid_epg(self):
        while True:
            try:cid,summary,error=self._epg_jobs.get_nowait()
            except queue.Empty:break
            if not error and summary:
                self._epg_cache[cid]=summary
                if self.grid_items and str(self.grid_items[self.index].get("id") or self.grid_items[self.index].get("ch_id") or "")==cid:self._update_header(self.grid_items[self.index])
    def _stop_grid_epg(self):
        try:self._epg_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._epg_timer_conn is not None:self._epg_timer_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._fetch_epg in self._epg_timer.callback:self._epg_timer.callback.remove(self._fetch_epg)
        except Exception as exc:optional_failure("ui.grid_epg_callback",exc)

