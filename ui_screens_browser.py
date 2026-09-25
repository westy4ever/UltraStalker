"""Portal browser screen extracted from ui.py without changing class behavior."""

from . import _
from Screens.Screen import Screen
from .ui_async import AsyncScreenMixin
from .ui_image_loader import ImageLoaderMixin
from .ui_transition import TransitionMixin
from .ui_fixed_adaptive import fixed_home_assets
import time
import weakref
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag
from .log import diagnostic_failure, get_logger, memory_snapshot as _mem34
LOG = get_logger()
from .core.call_compat import call_compatible
from .category_visibility import category_id as _visibility_category_id, hidden_ids as _profile_hidden_ids, hidden_update as _profile_hidden_update
from .ui_settings_inline_choice import SettingsInlineChoiceOverlay

BROWSER_SKIN = ""


def _provider_display_raw(item, clean_titles=True, default=""):
    item=item if isinstance(item,dict) else {}
    raw=item.get("_raw_name")
    if raw not in (None,""):
        return str(raw)
    return str(item.get("name") or item.get("title") or default or "")

def configure_browser_screen(**deps):
    globals().update(deps)
    if "BROWSER_SKIN" in deps:
        PortalBrowserScreen.skin = deps["BROWSER_SKIN"]

class PortalBrowserScreen(Screen, AsyncScreenMixin, ImageLoaderMixin, TransitionMixin):
    skin = BROWSER_SKIN
    ALL_MODES = (
        ("Live TV", "itv", "settings_icons_40/channel_list.png"),
        ("Movies", "vod", "settings_icons_40/artwork.png"),
        ("Series", "series", "settings_icons_40/next_episode.png"),
        ("Catch-up TV", "catchup", "settings_icons_40/catchup.png"),
        ("Favorites", "favorites", "settings_icons_40/watched.png"),
        ("Continue Watching", "recent", "settings_icons_40/resume.png"),
        ("Account Information", "account", "settings_icons_40/web_access.png"),
        ("Global Search", "search", "settings_icons_40/multi_search.png"),
        ("Settings", "settings", "settings_icons_40/advanced.png"),
    )
    MODES = ALL_MODES

    def __init__(self, session, profile, initial_action=None, shared_client=None, adaptive_source=None):
        _perf29_init_t0=time.monotonic()
        LOG.info("PERF29 browser init_begin action=%s mono_ms=%d",str(initial_action or "root"),int(_perf29_init_t0*1000))
        Screen.__init__(self, session)
        self._async_init(); self.onClose.append(self._stop_async); self.onClose.append(self._image_stop)
        self._ui_diag_open_mono=time.monotonic();_ui_diag("ui_open",screen="browser")
        self.profile = profile
        self.initial_action = initial_action
        self._home_adaptive_source = str(adaptive_source or "")
        self._initial_action_done = False
        self._initial_action_scheduled = False
        self._initial_action_timer=eTimer();self._initial_action_timer_conn=None
        try:self._initial_action_timer_conn=self._initial_action_timer.timeout.connect(self._activate_initial_action)
        except Exception:self._initial_action_timer.callback.append(self._activate_initial_action)
        self.onClose.append(self._stop_initial_action_timer)
        self._home_entry = bool(initial_action)
        initial_cfg=load_settings();self._selection_cfg=self._selection_snapshot(initial_cfg);self._selection_cfg_mono=time.monotonic()
        self.portal_session = None if shared_client is not None else PortalSession(profile, timeout=initial_cfg.get("timeout", 10))
        self.client = shared_client if shared_client is not None else self.portal_session.client
        self._epg_jobs = queue.Queue(); self._epg_token = 0; self._epg_cache = {}; self._epg_future = None
        self._live_picon_cache_progress=queue.Queue();self._live_picon_cache_running=False;self._live_picon_cache_future=None
        self._epg_timer = eTimer(); self._epg_timer_conn = None
        try: self._epg_timer_conn = self._epg_timer.timeout.connect(self._fetch_selected_epg)
        except Exception: self._epg_timer.callback.append(self._fetch_selected_epg)
        self.onClose.append(self._stop_epg_preview)
        self.onClose.append(self._release_browser_pixmaps)
        self.onClose.append(self._stop_browser_ui_hooks)
        self.onClose.append(self._ui_diag_browser_close)
        try:self.onShown.append(self._browser_shown_reset)
        except Exception as exc:optional_failure("ui",exc)
        try:self.onHide.append(self._browser_hidden_release)
        except Exception as exc:optional_failure("ui.browser_hide_hook",exc)
        self.level = "root"; self.media_type = None; self.genre = None; self.content_items = []; self.page = 1; self.catchup_channel = None; self.active_modes = []
        self._category_select_mode=False; self._category_selected_ids=set(); self._category_known_items=[]
        self["brand_header"] = Pixmap()
        self["title"] = Label("")
        self["counter"] = Label(_("Portal sections"))
        self["section"] = Label(profile.get("portal", ""))
        self["top_divider"] = Label("")
        self["list"] = IconMenuList([], width=720, item_height=74, icon_size=58, primary_font=29, secondary_font=19)
        self["category_menu_list"] = IconMenuList([], width=434, item_height=80, icon_size=0, primary_font=22, secondary_font=16, row_style="settings_dialog")
        self._category_menu_active=False; self._category_menu_level=None; self._category_menu_parent=None; self._category_menu_choices=[]; self._category_menu_last_idx=0
        self._category_menu_overlay=SettingsInlineChoiceOverlay(self,"category_menu_list","category_menu_overlay_actions",asset,_,optional_failure)
        self["category_clean_bg"] = Label(" ")
        self["page_adaptive_bg"] = Pixmap(); self["preview"] = Pixmap(); self["accent_frame"] = Pixmap(); self["info_title"] = Label(_("Choose a section"))
        self["info_card1"] = Pixmap(); self["info_card2"] = Pixmap(); self["info_card3"] = Pixmap()
        self["info_icon1"] = Pixmap(); self["info_icon2"] = Pixmap(); self["info_icon3"] = Pixmap()
        self._category_chrome = {}
        self._movies_category_visual_ready = False
        self._category_static_bg_loaded = ""
        self["info"] = Label(_("Live, Movies, Series, Favorites and Continue Watching"))
        self["status"] = Label(_("Ready"))
        self._favorite_card_index=0;self._favorite_card_entries=[];self._favorite_card_page=0
        self._favorite_root_index=0;self._favorites_bucket_type=None
        self._favorite_art_jobs=queue.Queue();self._favorite_art_pending=set();self._favorite_art_futures=set();self._favorite_art_generation=0;self._favorite_art_local={}
        self.onClose.append(self._stop_favorite_art_jobs)
        # Favorites selector uses the exact Home-card component, kept separate
        # from the inline content grid so the three cards stay visible while a
        # bucket is open.
        for _fi in range(3):
            self["fav_root_bg%d"%_fi]=Pixmap();self["fav_root_sel%d"%_fi]=Pixmap();self["fav_root_icon%d"%_fi]=Pixmap();self["fav_root_title%d"%_fi]=Label("");self["fav_root_meta%d"%_fi]=Label("")
        for _fi in range(7):
            self["fav_card%d"%_fi]=Pixmap();self["fav_sel%d"%_fi]=Pixmap();self["fav_art%d"%_fi]=Pixmap();self["fav_title%d"%_fi]=Label("");self["fav_meta%d"%_fi]=Label("")
        self["red_bg"] = Pixmap(); self["green_bg"] = Pixmap(); self["yellow_bg"] = Pixmap(); self["blue_bg"] = Pixmap()
        self["red"] = Label(_("Back")); self["green"] = Label(_("Authorize"))
        self["yellow"] = Label(_("Refresh")); self["blue"] = Label(_("Open / Play"))
        self._category_row_card_w = 426
        self._category_title_font = 0
        self._image_init("preview", (530,382), profile, self.client)
        self.onLayoutFinish.append(self._layout_ready)
        self["list"].onSelectionChanged.append(self._selection_changed)
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions","UltraStalkerMenuActions", "DirectionActions", "InfobarAudioSelectionActions", "InfobarSubtitleSelectionActions", "InfobarEPGActions"], {
            "cancel": self.back, "red": self.back, "green": self.reauthorize,
            "yellow": self.refresh_current, "blue": self.blue_action, "ok": self.select,
            "up": self._nav_up, "down": self._nav_down,
            "left": self._nav_left, "right": self._nav_right,
            "menu": self.open_tools, "audioSelection": self.open_audio, "subtitleSelection": self.open_subtitles, "showEventInfo": self.show_selected_epg,
        }, -1)
        if self.initial_action in ("itv", "vod", "series"):
            # Cold-start direct entry from Home.  Never seed the legacy Browser
            # root list, otherwise an uncached category request can expose it for
            # 2-3 seconds after an Enigma2 restart.
            self.media_type = self.initial_action
            self.level = "genres_loading"
            try:self["list"].set_icon_rows([]);self["list"].hide()
            except Exception as exc:optional_failure("ui.category_cold_empty", exc)
            self._set_category_clean_visibility(True)
        elif self.initial_action in ("catchup","favorites"):
            # Beta135: utility pages entered from Home start blank/hero-first.
            # Do not seed the generic Browser root labels/list because OpenBH can
            # paint one frame of Ready/Back/Authorize/Refresh before the utility
            # layout takes over.  Actions stay bound; only transient chrome is
            # suppressed.
            self.level = "utility_loading"
            try:self["list"].set_icon_rows([]);self["list"].hide()
            except Exception as exc:optional_failure("ui.utility_cold_empty",exc)
            for _n in ("title","counter","section","top_divider","info_title","info","status","red","green","yellow","blue"):
                try:self[_n].setText("")
                except Exception:pass
        else:
            self._set_root_rows()
        LOG.info("PERF29 browser init_done action=%s elapsed_ms=%d",str(initial_action or "root"),int((time.monotonic()-_perf29_init_t0)*1000))
        _mem34("browser_init_done", action=str(initial_action or "root"))

    def _category_surface_active(self):
        return getattr(self, "level", None) in ("genres", "genres_loading")

    def _category_dynamic_visuals_enabled(self):
        """Dynamic category visuals are permanently disabled for Live/Movies/Series.

        All three category pages share one bundled static background and floating rows.
        """
        media = str(getattr(self, "media_type", "") or "").lower()
        return not (self._category_surface_active() and media in ("itv", "vod", "series"))

    def _movies_static_category_chrome(self):
        """Zero-work category chrome for Live, Movies and Series.

        Both pages use the approved floating-row grammar.  They do not resolve,
        load or generate any row background/glass asset.
        """
        return {}

    def _static_category_background_path(self):
        """Return the one receiver-local background shared by Live, Movies and Series."""
        media = str(getattr(self, "media_type", "") or "").lower()
        if self._category_surface_active() and media in ("itv", "vod", "series"):
            path = asset("category_palestine_static_1920x1080.jpg")
            if os.path.isfile(path):
                return path
        return ""

    def _apply_static_category_background(self):
        """Paint the shared Live/Movies/Series background once, with no worker.

        The pixmap stays resident while a child grid is open so returning to
        Categories does not decode it again. It is released only when leaving
        the static category surface or closing the Browser.
        """
        path = self._static_category_background_path()
        if not path:
            try:
                if self["page_adaptive_bg"].instance is not None:
                    self["page_adaptive_bg"].instance.setPixmap(None)
                self["page_adaptive_bg"].hide()
            except Exception as exc:
                optional_failure("ui.category_static_bg_clear", exc)
            self._category_static_bg_loaded = ""
            return False
        self._set_category_clean_visibility(True)
        self._browser_geom("page_adaptive_bg", 0, 0, 1920, 1080)
        try:
            if self["page_adaptive_bg"].instance is not None:
                if str(getattr(self, "_category_static_bg_loaded", "") or "") != path:
                    self["page_adaptive_bg"].instance.setPixmap(None)
                    self["page_adaptive_bg"].instance.setPixmapFromFile(path)
                    self._category_static_bg_loaded = path
                self["page_adaptive_bg"].show()
                return True
        except Exception as exc:
            optional_failure("ui.category_static_bg_apply", exc)
        return False

    def _decode_pending_picture(self):
        """Never let a stale Browser preview decode paint over Categories.

        The Browser root queues its right-side section artwork through ePicLoad.
        On fast cached category opens that decode can fire *after* the screen has
        already switched to ``genres``.  Hiding the preview widget is therefore
        not enough: the delayed decoder callback simply shows it again.

        Categories intentionally have no preview artwork, so drop queued preview
        work while the category rail is active.  Leaving Categories restores the
        normal ImageLoaderMixin path automatically.
        """
        if self._category_surface_active():
            self._image_pending_path = None
            self._image_active_path = None
            self._image_displayed_path = None
            self._image_decode_busy = False
            try:self._image_decode_timer.stop()
            except Exception as exc:optional_failure("ui.category_preview_timer_stop", exc)
            try:
                if self["preview"].instance is not None:
                    self["preview"].instance.setPixmap(None)
                self["preview"].hide()
            except Exception as exc:optional_failure("ui.category_preview_drop", exc)
            return
        return ImageLoaderMixin._decode_pending_picture(self)

    def _picture_ready(self, *args):
        """Drain late ePicLoad completions without revealing them in Categories."""
        if self._category_surface_active():
            try:
                # Consume the completed native pixmap so ePicLoad is left in a
                # clean reusable state, but deliberately never assign/show it.
                self._picload.getData()
            except Exception as exc:optional_failure("ui.category_preview_drain", exc)
            self._image_pending_path = None
            self._image_active_path = None
            self._image_displayed_path = None
            self._image_decode_busy = False
            try:
                if self["preview"].instance is not None:
                    self["preview"].instance.setPixmap(None)
                self["preview"].hide()
            except Exception as exc:optional_failure("ui.category_preview_late_hide", exc)
            return
        return ImageLoaderMixin._picture_ready(self, *args)

    def _release_browser_pixmaps(self):
        # Keep the static Categories/Favorites backdrop resident while a child
        # screen (notably the Live player) is on top.  Favorites is intentionally
        # one inline page; dropping page_adaptive_bg here produced the black
        # return frame until BACK forced a fresh render.
        keep_static_bg = (
            not getattr(self, "_screen_closed", False)
            and (
                (self._category_surface_active()
                 and str(getattr(self, "media_type", "") or "").lower() in ("itv", "vod", "series")
                 and bool(getattr(self, "_category_static_bg_loaded", "")))
                or str(getattr(self, "level", "") or "") in ("favorites_root", "favorites_cards", "favorites_live")
            )
        )
        names=("preview","accent_frame","info_card1","info_card2","info_card3")
        if not keep_static_bg:
            names=("page_adaptive_bg",)+names
            self._category_static_bg_loaded = ""
            self._utility_static_bg_loaded = ""
            self._favorites_static_bg_loaded = ""
        for _name in names:
            _release_pixmap_widget(self,_name)
        try:self._category_chrome={}
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        _native_image_pressure_relief(force=True)

    def _restore_category_side_identity(self):
        """Keep the clean category page clean after returning from child screens."""
        if not self._category_surface_active() or self.media_type not in ("itv","vod","series"):
            return
        if not self._category_dynamic_visuals_enabled():
            # Live, Movies and Series share the exact same receiver-local static page.
            # Never redo this work on arrow-key selection changes.
            if getattr(self, "_movies_category_visual_ready", False):
                return
            self._set_category_clean_visibility(True)
            self._apply_static_category_background()
            self._movies_category_visual_ready = True
            return
        self._set_category_clean_visibility(True)
        try:self["page_adaptive_bg"].show()
        except Exception as exc:optional_failure("ui.category_backdrop_restore",exc)

    def _apply_browser_status_color(self):
        try:
            text=str(self["status"].getText() or "").strip().casefold()
            color="#69e79a" if text=="ready" else "#7fd7ff"
            if self["status"].instance is not None:self["status"].instance.setForegroundColor(parseColor(color))
        except Exception as exc:optional_failure("ui.browser_status_color",exc)

    def _ui_diag_browser_close(self):
        _mem34("browser_close", action=str(getattr(self,"initial_action","") or "root"), level=str(getattr(self,"level","") or ""), items=len(getattr(self,"content_items",[]) or []))
        try:_ui_diag("ui_close",screen="browser",level=getattr(self,"level",""),
                     lifetime_ms=int(max(0.0,(time.monotonic()-self._ui_diag_open_mono)*1000.0)))
        except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)

    def _stop_browser_ui_hooks(self):
        for result_queue_name in ("_epg_jobs","_live_picon_cache_progress"):
            result_queue=getattr(self,result_queue_name,None)
            if result_queue is None:continue
            try:
                while True:result_queue.get_nowait()
            except queue.Empty:
                pass
            except Exception as exc:
                optional_failure("ui.browser_queue_cleanup",exc)
        self._epg_pending=None
        self._epg_token += 1
        future=getattr(self,"_epg_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception as exc:optional_failure("ui.browser_epg_future_cancel",exc)
        self._epg_future=None
        for hook_name,callback in (
            ("onShown",self._browser_shown_reset),
            ("onHide",self._browser_hidden_release),
            ("onLayoutFinish",self._layout_ready),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.browser_hook_cleanup",exc)
        try:
            callbacks=self["list"].onSelectionChanged
            if self._selection_changed in callbacks:callbacks.remove(self._selection_changed)
        except Exception as exc:
            optional_failure("ui.browser_selection_cleanup",exc)

    def _selection_snapshot(self, cfg):
        cfg=cfg if isinstance(cfg,dict) else {}
        return {
            "clean_titles":bool(cfg.get("clean_titles",True)),
            "load_images":bool(cfg.get("load_images",True)),
            "parental_lock":bool(cfg.get("parental_lock",False)),
            "parental_mode":str(cfg.get("parental_mode","pin") or "pin"),
            "adult_keywords":list(cfg.get("adult_keywords",[]) or []),
            "pinned_categories":{str(k):list(v or []) for k,v in dict(cfg.get("pinned_categories",{}) or {}).items()},
            "protected_categories":{str(k):list(v or []) for k,v in dict(cfg.get("protected_categories",{}) or {}).items()},
        }

    def _selection_settings(self):
        # Selection/key-repeat is RAM-only. Settings are refreshed on onShown and
        # explicit settings mutations; do not reopen persistent JSON every 350 ms.
        if float(getattr(self,"_selection_cfg_mono",0.0) or 0.0)<=0.0:
            self._refresh_selection_settings()
        return self._selection_cfg

    def _refresh_selection_settings(self):
        self._selection_cfg=self._selection_snapshot(load_settings());self._selection_cfg_mono=time.monotonic()
        try:self._image_load_images=bool(self._selection_cfg.get("load_images",True))
        except Exception:pass

    def _browser_shown_reset(self):
        try:self._refresh_selection_settings()
        except Exception as exc:optional_failure("ui.browser_settings_refresh",exc)
        # Do not jump to the first row when a child screen closes.  Enigma2
        # fires onShown again after modal dialogs/players, so moving to index 0
        # here destroyed the user's navigation position.
        try:
            if getattr(self,"_browser_images_suspended",False):
                self._browser_images_suspended=False
                self._image_layout_ready()
        except Exception as exc:optional_failure("ui.browser_resume_image",exc)
        # Favorites never opens a second Browser page.  After a modal child
        # (especially UltraStalkerPlayer) closes, re-show the exact inline
        # Favorites surface and current card without resetting selection.
        if str(getattr(self,"level","") or "") in ("favorites_root","favorites_cards","favorites_live"):
            try:
                self._apply_favorites_static_mode(show_list=(self.level=="favorites_live"))
                self._render_favorites_root()
                if self.level=="favorites_cards":
                    self._render_favorite_cards()
            except Exception as exc:optional_failure("ui.favorites_resume_surface",exc)
        if self._category_surface_active() and self.media_type in ("itv","vod","series"):
            try:
                self._apply_category_portal_geometry()
                if self._category_dynamic_visuals_enabled():
                    self._load_category_backdrop()
                    if self.level == "genres":
                        self._load_category_chrome()
                else:
                    # Live+Movies+Series: no Hero decode/build and no adaptive
                    # palette/chrome generation on screen resume.
                    self._category_chrome = self._movies_static_category_chrome()
                self._restore_category_side_identity()
            except Exception as exc:optional_failure("ui.category_resume_identity",exc)
        try:self._selection_changed()
        except Exception as exc:optional_failure("ui",exc)
        try:self._apply_browser_status_color()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._schedule_initial_action()

    def _browser_hidden_release(self):
        if getattr(self,"_screen_closed",False):return
        # Live, Movies and Series keep the same static background decoded while a
        # child grid is open. Returning to Categories costs no second decode.
        self._browser_images_suspended=True
        try:self._image_suspend()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._release_browser_pixmaps()

    def _utility_mode_active(self):
        return self.level in ("catchup_channels","catchup_channels_loading","catchup_programs","catchup_programs_loading","favorites")

    def _apply_utility_glass_mode(self):
        """Static compact glass identity for Catch-up and Favorites."""
        try:
            self._browser_geom("list",62,650,1796,300)
            self["list"].row_width=1796
            self["list"].set_layout(58,40,23,16,"utility_glass")

            # Same authored right column as Settings.
            for name,x,y,w,h in (
                ("preview",1260,132,360,260),
                ("accent_frame",0,0,1,1),
                ("info_card1",1110,438,660,78),
                ("info_card2",1110,532,660,78),
                ("info_card3",1110,626,660,78),
                ("info_icon1",1134,453,48,48),
                ("info_icon2",1134,547,48,48),
                ("info_icon3",1134,641,48,48),
                ("info_title",1200,438,540,78),
                ("info",1200,532,540,78),
                ("status",1200,626,540,78),
            ):
                try:self._browser_geom(name,x,y,w,h)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
            for divider_name in ("divider","separator","midline","vline","vertical_line"):
                try:self[divider_name].hide()
                except Exception as exc:optional_failure("ui.silent_guard",exc)
            if self["list"].instance is not None:
                try:self["list"].instance.setSelectionEnable(0)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["list"].instance.setTransparent(1)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["list"].instance.setScrollbarMode(2)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["list"].instance.setScrollbarMode(2)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
        except Exception as exc:optional_failure("ui.utility_browser_geom",exc)
        try:
            # Catch-up/Favorites use the same bundled static background as
            # Live/Movies/Series Categories. No Home Hero lookup, long-backdrop
            # derivative build or palette work is needed just to paint the page.
            _static_bg = asset("category_palestine_static_1920x1080.jpg")
            self._browser_geom("page_adaptive_bg",0,0,1920,1080)
            if self["page_adaptive_bg"].instance is not None and os.path.isfile(_static_bg):
                if str(getattr(self,"_utility_static_bg_loaded","") or "") != _static_bg:
                    self["page_adaptive_bg"].instance.setPixmap(None)
                    self["page_adaptive_bg"].instance.setPixmapFromFile(_static_bg)
                    self._utility_static_bg_loaded = _static_bg
                self["page_adaptive_bg"].show()
                self._category_static_bg_loaded = _static_bg
            for n in ("preview","accent_frame","info_card1","info_card2","info_card3","info_icon1","info_icon2","info_icon3","info_title","info"):
                try:self[n].hide()
                except Exception:pass
        except Exception as exc:optional_failure("ui.utility_static_bg",exc)
        try:
            if self["accent_frame"].instance is not None:
                self["accent_frame"].instance.setPixmap(None)
            self["accent_frame"].hide()
            self._browser_geom("accent_frame",2000,0,1,1)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            self._image_size=(360,260)
            self._picload.setPara((360,260,1,1,False,1,"#000000"))
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        chrome=_neutral_utility_glass()
        self._category_chrome=dict(chrome)
        card=chrome.get("side") or chrome.get("info")
        if card and os.path.isfile(card):
            for n in ("info_card1","info_card2","info_card3"):
                try:self[n].instance.setPixmapFromFile(card);self[n].show()
                except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _set_utility_preview_direct(self, filename):
        """Display utility-page hero art directly, bypassing ePicLoad artifacts."""
        try:
            path=asset(filename)
            if self["preview"].instance is not None and os.path.isfile(path):
                self["preview"].instance.setPixmapFromFile(path)
                self["preview"].show()
        except Exception as exc:
            optional_failure("ui.utility_preview_direct",exc)

    def _layout_ready(self):
        _perf29_layout_t0=time.monotonic()
        LOG.info("PERF29 browser layout_begin action=%s level=%s",str(self.initial_action or "root"),str(getattr(self,"level","") or ""))
        try:_ui_diag("ui_ready",screen="browser",elapsed_ms=int((time.monotonic()-self._ui_diag_open_mono)*1000.0))
        except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)
        self._image_layout_ready()

        # Test15 cold-start path: Home already chose Live/Movies/Series.  Keep the
        # Browser root completely unpainted while categories are fetched.  The user
        # sees the final clean hero immediately, then the real rows appear once.
        if self.initial_action in ("catchup","favorites") and not self._initial_action_done:
            # PERF30: utility pages already use the approved bundled static
            # Palestine background. Do not enter the legacy category mood path,
            # which can synchronously build a 1920x1080 extended backdrop and was
            # responsible for multi-second Catch-up opens on cold cache.
            _utility_t0=time.monotonic()
            try:
                self._apply_utility_glass_mode();self["page_adaptive_bg"].show()
            except Exception as exc:optional_failure("ui.utility_cold_static",exc)
            LOG.info("PERF30 utility_static_ready action=%s elapsed_ms=%d",str(self.initial_action or "root"),int((time.monotonic()-_utility_t0)*1000))
            for _name in ("title","counter","section","top_divider","list","accent_frame","preview","info_card1","info_card2","info_card3","info_icon1","info_icon2","info_icon3","info_title","info","status","red_bg","green_bg","yellow_bg","blue_bg","red","green","yellow","blue"):
                try:self[_name].hide()
                except Exception:pass
            self._schedule_initial_action()
            LOG.info("PERF29 browser layout_ready action=%s elapsed_ms=%d scheduled=yes",str(self.initial_action or "root"),int((time.monotonic()-_perf29_layout_t0)*1000))
            _mem34("browser_layout_ready", action=str(self.initial_action or "root"), level=str(getattr(self,"level","") or ""))
            return

        if self.initial_action in ("itv","vod","series"):
            # R129 SPEED: one authority owns category first paint.  Older builds
            # applied category geometry/static background here and then repeated
            # the same work immediately inside load_genres().  Let load_genres()
            # do it once, preserving the exact final pixels with less UI-thread work.
            self.media_type = self.initial_action
            if self.level not in ("genres", "genres_loading"):
                self.level = "genres_loading"
            if not self._initial_action_done:
                self._initial_action_done=True
                self._open_media_with_parental(self.initial_action)
            elif self.level == "genres":
                self._reveal_browser_content()
            LOG.info("PERF29 browser layout_ready action=%s elapsed_ms=%d category_entry=yes",str(self.initial_action or "root"),int((time.monotonic()-_perf29_layout_t0)*1000))
            _mem34("browser_layout_ready", action=str(self.initial_action or "root"), level=str(getattr(self,"level","") or ""))
            return

        for _name in ("title", "counter", "section", "top_divider", "list", "accent_frame", "preview", "info_card1", "info_card2", "info_card3", "info_icon1", "info_icon2", "info_icon3", "info_title", "info", "status", "red_bg", "green_bg", "yellow_bg", "blue_bg", "red", "green", "yellow", "blue"):
            try:
                self[_name].show()
            except Exception as exc:
                optional_failure("ui", exc)
        self._set_root_rows()
        self._decode_picture(asset("us80_section_live_530x382.png"))
        self._selection_changed()
        LOG.info("PERF29 browser layout_ready action=%s elapsed_ms=%d root=yes",str(self.initial_action or "root"),int((time.monotonic()-_perf29_layout_t0)*1000))
        _mem34("browser_layout_ready", action=str(self.initial_action or "root"), level=str(getattr(self,"level","") or ""))
        # Modal actions still wait for onShown; OpenATV rejects modal opens here.

    def _schedule_initial_action(self):
        if self._initial_action_done or self._initial_action_scheduled or not self.initial_action:return
        self._initial_action_scheduled=True
        try:self._initial_action_timer.start(1,True)
        except Exception:
            self._initial_action_scheduled=False
            self._activate_initial_action()

    def _stop_initial_action_timer(self):
        try:self._initial_action_timer.stop()
        except Exception as exc:optional_failure("ui.browser_initial_timer_stop",exc)
        try:
            if self._initial_action_timer_conn is not None:self._initial_action_timer_conn.disconnect()
        except Exception as exc:optional_failure("ui.browser_initial_timer_disconnect",exc)
        try:
            if self._activate_initial_action in self._initial_action_timer.callback:self._initial_action_timer.callback.remove(self._activate_initial_action)
        except Exception as exc:optional_failure("ui.browser_initial_timer_callback",exc)

    def _activate_initial_action(self):
        self._initial_action_scheduled=False
        if self._initial_action_done or not self.initial_action:
            return
        self._initial_action_done = True
        action = self.initial_action
        _perf29_action_t0=time.monotonic()
        LOG.info("PERF29 browser action_begin action=%s",str(action))
        if action in ("itv", "vod", "series"):
            self._open_media_with_parental(action)
        elif action == "catchup":
            self.load_catchup_channels()
        elif action == "favorites":
            self._show_saved_entries(load_favorites(), "Favorites")
        elif action == "recent":
            self._show_saved_entries(load_continue_watching(), "Continue Watching")
        elif action == "account":
            self.show_account_info()
        elif action == "settings":
            self.session.open(NovaSettingsScreen, self.profile, self.client)
        LOG.info("PERF29 browser action_return action=%s elapsed_ms=%d",str(action),int((time.monotonic()-_perf29_action_t0)*1000))

    def _drain_jobs(self):
        AsyncScreenMixin._drain_jobs(self)
        if not self._screen_closed:
            self._drain_image_jobs(); self._drain_epg_jobs(); self._drain_favorite_art_jobs()
            while True:
                try:msg=self._live_picon_cache_progress.get_nowait()
                except queue.Empty:break
                except Exception:break
                try:
                    phase=msg[0]
                    if phase=="scan":
                        _phase,done,total=msg
                        self["blue"].setText("%d / %d"%(done,total))
                        self["status"].setText(_("Scanning Live categories %d / %d")%(done,total))
                    elif phase=="cache":
                        _phase,done,total,cached,downloaded,unavailable=msg
                        self["blue"].setText("%d / %d"%(done,total))
                        self["status"].setText(_("Picons %d/%d • %d cached • %d downloaded • %d failed")%(
                            done,total,cached,downloaded,unavailable))
                    elif phase=="done":
                        _phase,done,total,cached,downloaded,unavailable=msg
                        self._live_picon_cache_running=False
                        self["blue"].setText(_("Cache Picons"))
                        self["status"].setText(_("Picons ready • %d/%d • %d cached • %d downloaded • %d failed")%(
                            done,total,cached,downloaded,unavailable))
                    elif phase=="error":
                        self._live_picon_cache_running=False
                        self["blue"].setText(_("Cache Picons"))
                        self["status"].setText(str(msg[1] if len(msg)>1 else _("Picon cache failed")))
                except Exception as exc:
                    optional_failure("ui.live_picon_cache_progress",exc)

    def _set_list_density(self, mode="root"):
        """Stable per-screen density. Never share cramped geometry across unrelated lists."""
        if mode == "root":
            self["list"].set_layout(72, 56, 31, 20, "double")
        elif mode == "genres":
            self["list"].row_width = max(434, int(getattr(self,"_category_row_card_w",426) or 426) + 8)
            if str(getattr(self, "media_type", "") or "").lower() in ("itv", "vod", "series"):
                # Approved Movies grammar copied verbatim to Series: floating
                # yellow folder + text only, no card/glass/pointer/animation.
                self["list"].set_layout(58, 36, 20, 14, "category_floating")
            else:
                self["list"].set_layout(66, 40, 22, 16, "category_portal")
        elif mode == "itv":
            cfg=getattr(self, "_grid_settings", None) or load_settings(); layout=cfg.get("channel_list_mode","epg")
            if layout == "large": self["list"].set_layout(70, 50, 27, 18, "channel")
            elif layout == "compact": self["list"].set_layout(52, 38, 23, 16, "single")
            else: self["list"].set_layout(62, 44, 24, 16, "channel")
        elif mode == "compact":
            self["list"].set_layout(54, 38, 25, 18, "compact_meta")
        else:
            self["list"].set_layout(56, 40, 26, 18, "compact_meta")


    def _browser_geom(self, name, x, y, w, h):
        """Move/resize one Browser widget using the same 1920x1080 design grid."""
        try:
            desktop=getDesktop(0).size(); sx=float(desktop.width())/1920.0; sy=float(desktop.height())/1080.0
            inst=self[name].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)), int(round(y*sy))))
                inst.resize(eSize(int(round(w*sx)), int(round(h*sy))))
        except Exception as exc:
            optional_failure("ui.browser_geom", exc)

    def _category_display_title(self, entry, cfg=None):
        cfg = cfg or load_settings()
        e = entry if isinstance(entry, dict) else {}
        gid = str(e.get("id") or e.get("genre_id") or e.get("name") or e.get("title") or "")
        raw = e.get("title") or e.get("name") or e.get("category_name") or e.get("genre_name") or e.get("id") or _("Categories")
        title = _clean_display_text(raw, 100) or _("Categories")
        pinned = set(cfg.get("pinned_categories", {}).get(self.media_type, []))
        protected = set(cfg.get("protected_categories", {}).get(self.media_type, []))
        words = cfg.get("adult_keywords", [])
        if gid in pinned:
            title = "★  " + title
        sensitive = gid in protected or any(str(w).casefold() in str(e.get("name") or e.get("title") or "").casefold() for w in words)
        if cfg.get("parental_lock") and cfg.get("parental_mode", "pin") == "pin" and sensitive:
            title = "[PIN]  " + title
        # Selection state is rendered in a dedicated right-side check column.
        # Never mix the check mark into provider/category text; this keeps Arabic
        # and Latin category names visually consistent and unambiguous.
        return title

    def _category_measure_width(self, text, font_size):
        try:
            fn = globals().get("_settings_text_width_px")
            if fn is not None:
                return max(1, int(fn(str(text or ""), int(font_size))))
        except Exception as exc:
            optional_failure("ui.category_text_measure", exc)
        return max(1, int(len(str(text or "")) * max(1, int(font_size)) * 0.58))

    def _prepare_category_global_geometry(self, entries, cfg=None):
        """Choose one stable row width for the entire category list.

        The width is based on the longest decorated title across every category,
        not just the 14 currently visible rows.  All pages therefore keep the
        exact same rail geometry while UP/DOWN paging.

        For Movies Categories we must guarantee that *both* states remain fully
        readable: the compact white title when the row is not selected, and the
        larger green title when the row is selected.  Pick one global width and
        one font pair that keep the longest title visible in both states.
        """
        cfg = cfg or load_settings()
        titles = [self._category_display_title(e, cfg) for e in (entries or [])]
        if not titles:
            titles = ["Category"]
        # Native text measurement is comparatively expensive on Enigma2.  Rank
        # cheaply in Python first, then probe only the titles most likely to be
        # widest.  This keeps portals with hundreds of categories from measuring
        # every label at every candidate font size.
        def _rough_width(value):
            total=0.0
            for ch in str(value or ""):
                code=ord(ch)
                if 0x0600<=code<=0x06ff:total+=1.35
                elif ch.isupper():total+=1.18
                elif ch.isdigit():total+=1.00
                elif ch.isspace():total+=0.55
                else:total+=1.0
            return total
        if len(titles)>48:
            titles=sorted(titles,key=_rough_width,reverse=True)[:48]
        media = str(getattr(self, "media_type", "") or "").lower()
        is_movies = media in ("itv", "vod", "series")
        max_width = 1320 if is_movies else 860
        if is_movies:
            # (normal_font_index, normal_px, selected_font_index, selected_px)
            # Keep Movies Categories calmer and denser: slightly smaller white
            # titles, and a selected green title that is emphasized without
            # ballooning into the line below.
            font_steps = (
                (0, 22, 11, 26),
                (3, 20, 11, 26),
                (4, 18, 3, 20),
                (5, 16, 4, 18),
                (6, 14, 5, 16),
            )
        else:
            font_steps = (
                (0, 22, 0, 22),
                (3, 19, 3, 19),
                (4, 17, 4, 17),
            )
        chosen_font, chosen_sel_font, chosen_width = (0 if is_movies else 0), (11 if is_movies else 0), 426
        for font_index, font_size, selected_font_index, selected_font_size in font_steps:
            longest_normal = max([self._category_measure_width(t, font_size) for t in titles] or [260])
            longest_selected = max([self._category_measure_width(t, selected_font_size) for t in titles] or [260])
            # Leave permanent breathing room for the folder icon plus trailing
            # padding so no title disappears at either size.
            needed = max(426, longest_normal + 102, longest_selected + 136)
            chosen_font, chosen_sel_font, chosen_width = font_index, selected_font_index, min(max_width, needed)
            if needed <= max_width:
                break
        chosen_width = int(chosen_width)
        if chosen_width % 2:
            chosen_width += 1
        self._category_row_card_w = max(426, min(max_width, chosen_width))
        self._category_title_font = chosen_font
        self._category_selected_title_font = chosen_sel_font
        return self._category_row_card_w

    def _set_category_clean_visibility(self, active):
        # Categories intentionally keep only the brand, exact Home backdrop and
        # floating rows.  A neutral full-screen underlay covers the Browser's
        # baked divider while the hero is being restored, so neither the old
        # top line nor any right-side Browser furniture can flash through.
        try:
            (self["category_clean_bg"].show() if active else self["category_clean_bg"].hide())
        except Exception as exc:
            optional_failure("ui.category_clean_underlay", exc)
        if active:
            # Category pages are intentionally brand-free.  Do not rely on hide()
            # alone: some Enigma2 images repaint the skin-time pixmap after layout.
            # Clear the native pixmap and move the widget off-screen as well.
            try:
                if self["brand_header"].instance is not None:
                    self["brand_header"].instance.setPixmap(None)
                self["brand_header"].hide()
                self._browser_geom("brand_header",2000,0,1,1)
            except Exception as exc:optional_failure("ui.category_brand_hard_hide",exc)
            # Hard-disable the legacy right-side preview, including any root-page
            # decode that was queued just before cached Categories became ready.
            try:self._image_suspend()
            except Exception as exc:optional_failure("ui.category_preview_suspend", exc)
            try:
                if self["preview"].instance is not None:
                    self["preview"].instance.setPixmap(None)
                self["preview"].hide()
            except Exception as exc:optional_failure("ui.category_preview_clear", exc)
        if not active:
            try:
                self._browser_geom("brand_header",48,12,300,96)
                _brand=asset("brand_header_full.png")
                if self["brand_header"].instance is not None and os.path.isfile(_brand):
                    self["brand_header"].instance.setPixmapFromFile(_brand)
            except Exception as exc:optional_failure("ui.category_brand_restore",exc)
        names=("brand_header","title","section","counter","top_divider","preview","accent_frame",
               "info_card1","info_card2","info_card3","info_icon1","info_icon2","info_icon3",
               "info_title","info","status","red_bg","green_bg","yellow_bg","blue_bg",
               "red","green","yellow","blue")
        for name in names:
            try:
                (self[name].hide() if active else self[name].show())
            except Exception as exc:
                optional_failure("ui.category_clean_visibility", exc)

    def _apply_category_portal_geometry(self):
        """Apply the clean full-height Categories rail.

        The Categories-only brand is hidden and the exact approved 66px row
        grammar is preserved.  Sixteen complete rows fit between 12px top/bottom
        margins, so density increases without shrinking cards or their spacing.
        """
        _cat_max = 1320 if str(getattr(self, "media_type", "") or "").lower() in ("itv", "vod", "series") else 860
        card_w=max(426,min(_cat_max,int(getattr(self,"_category_row_card_w",426) or 426)))
        list_w=card_w+8
        self._browser_geom("list",14,12,list_w,1056)
        try:
            self["list"].row_width=list_w
            if self["list"].instance is not None:
                try:self["list"].instance.setSelectionEnable(0)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["list"].instance.setTransparent(1)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["list"].instance.setScrollbarMode(2)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
        except Exception as exc:optional_failure("ui.category_settings_list",exc)
        self._set_category_clean_visibility(True)

    def _restore_browser_geometry(self):
        """Restore the normal Browser geometry when leaving Categories."""
        self._movies_category_visual_ready = False
        self._set_category_clean_visibility(False)
        try:
            if self["page_adaptive_bg"].instance is not None:
                self["page_adaptive_bg"].instance.setPixmap(None)
            self["page_adaptive_bg"].hide()
            self._category_static_bg_loaded = ""
        except Exception as exc:
            optional_failure("ui.silent_guard",exc)
        self._browser_geom("list", 78, 190, 754, 720)
        self._browser_geom("preview", 1110, 205, 530, 382)
        self._browser_geom("accent_frame", 1090, 194, 570, 405)
        self._browser_geom("info_card1", 1070, 620, 610, 70)
        self._browser_geom("info_card2", 1070, 708, 610, 70)
        self._browser_geom("info_card3", 1070, 796, 610, 70)
        self._browser_geom("info_icon1", 1090, 634, 42, 42)
        self._browser_geom("info_icon2", 1090, 722, 42, 42)
        self._browser_geom("info_icon3", 1090, 810, 42, 42)
        self._browser_geom("info_title", 1145, 620, 515, 70)
        self._browser_geom("info", 1145, 708, 515, 70)
        self._browser_geom("status", 1145, 796, 515, 70)
        try:
            if self["list"].instance is not None:
                try:self["list"].instance.setSelectionEnable(1)
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["list"].instance.setScrollbarMode(2)
                except Exception:
                    try:self["list"].instance.setScrollbarMode(2)
                    except Exception as exc:optional_failure("ui.silent_guard",exc)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            self._image_size=(530,382)
            self._picload.setPara((530,382,1,1,False,1,"#000000"))
        except Exception as exc:optional_failure("ui.silent_guard",exc)


    def _category_mood_source(self):
        """Return the cached source belonging to the exact current Home hero.

        Categories prefer the hero's raw local backdrop so Test15 can retain the
        artwork farther down the screen than Home's intentionally short prepared
        top-hero.  No network lookup is allowed here.
        """
        media = str(getattr(self, "media_type", "") or "").lower()
        if media in ("itv", "vod", "series"):
            return None
        hero = {}
        try:
            with open(HOME_HERO_FILE, "r", encoding="utf-8") as h:
                state = json.load(h)
            hero = state.get("hero") if isinstance(state, dict) and isinstance(state.get("hero"), dict) else {}
        except Exception:
            hero = {}
        # Categories need more vertical artwork than Home's prepared TOP hero,
        # whose alpha intentionally reaches zero around y=610.  Prefer the exact
        # raw cached backdrop belonging to that same hero, then build our own
        # receiver-safe long derivative from it.
        for key in ("display_backdrop_local", "backdrop_local"):
            candidate = str(hero.get(key) or "") if isinstance(hero, dict) else ""
            try:
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate) > 4096:
                    return candidate
            except Exception as exc:
                optional_failure("ui.category_raw_hero", exc)

        direct = str(getattr(self, "_home_adaptive_source", "") or "")
        try:
            if direct and os.path.isfile(direct) and os.path.getsize(direct) > 4096:
                return direct
        except Exception as exc:
            optional_failure("ui.silent_guard",exc)

        prepared = str(hero.get("prepared") or "") if isinstance(hero, dict) else ""
        try:
            if prepared and os.path.isfile(prepared) and os.path.getsize(prepared) > 4096:
                return prepared
        except Exception as exc:
            optional_failure("ui.category_prepared_fallback", exc)

        section_source = {
            "itv": "us80_section_live_530x382.png",
        }.get(media, "category_palestine_static_1920x1080.jpg")
        fallback = asset(section_source)
        return fallback if fallback and os.path.isfile(fallback) else None

    def _category_chrome_source(self):
        """Return a dynamic source only for category pages that still use it.

        Live, Movies and Series are permanently static and never read Home Hero state.
        """
        media = str(getattr(self, "media_type", "") or "").lower()
        if media in ("itv", "vod", "series"):
            return None
        hero = {}
        try:
            with open(HOME_HERO_FILE, "r", encoding="utf-8") as h:
                state = json.load(h)
            hero = state.get("hero") if isinstance(state, dict) and isinstance(state.get("hero"), dict) else {}
        except Exception:
            hero = {}
        for candidate in (str(hero.get("prepared") or ""), str(getattr(self, "_home_adaptive_source", "") or "")):
            try:
                if candidate and os.path.isfile(candidate) and os.path.getsize(candidate) > 4096:
                    return candidate
            except Exception as exc:
                optional_failure("ui.category_chrome_source", exc)
        return self._category_mood_source()

    def _load_category_backdrop(self):
        """Show the clean, lower-reaching Categories hero without building rows."""
        if not self._category_dynamic_visuals_enabled():
            # Approved static path for Live, Movies and Series. No Home Hero lookup,
            # derivative build, palette extraction, TMDb work or worker.
            self._apply_static_category_background()
            return self._category_static_bg_loaded or None
        try:
            src = self._category_mood_source()
            if not src:
                return None
            try:
                stamp = str(int(os.path.getmtime(src)))
            except Exception:
                stamp = "0"
            key = hashlib.sha1((src + "|" + stamp + "|category-long-hero-v1").encode("utf-8", "ignore")).hexdigest()[:16]
            backdrop = _build_category_extended_backdrop(src, key)
            if not backdrop or not os.path.isfile(backdrop):
                backdrop = src
            self._browser_geom("page_adaptive_bg", 0, 0, 1920, 1080)
            if self["page_adaptive_bg"].instance is not None and os.path.isfile(backdrop):
                self["page_adaptive_bg"].instance.setPixmapFromFile(backdrop)
                self["page_adaptive_bg"].show()
            self._set_category_clean_visibility(True)
            return backdrop
        except Exception as exc:
            optional_failure("ui.category_long_backdrop", exc)
            return None

    def _load_category_chrome(self):
        if not self._category_dynamic_visuals_enabled():
            # Static receiver-local rows only. No Home Hero palette lookup and no
            # _build_category_adaptive_chrome() call for Live/Movies/Series Categories.
            self._category_chrome = self._movies_static_category_chrome()
            return self._category_chrome
        try:
            src = self._category_chrome_source()
            if not src:
                self._category_chrome = {}
                return {}
            try:
                stamp = str(int(os.path.getmtime(src)))
            except Exception:
                stamp = "0"
            card_w=max(426,min(860,int(getattr(self,"_category_row_card_w",426) or 426)))
            key = hashlib.sha1((src + "|" + stamp + "|category-settings-v2|" + str(card_w)).encode("utf-8", "ignore")).hexdigest()[:16]
            self._category_chrome = _build_category_adaptive_chrome(
                src, key, getattr(self,"media_type","") or "", card_w
            )

            # The rows and the visible long hero derive from the same cached Home
            # source; Categories simply retain the picture farther down the screen.
            self._load_category_backdrop()
            return self._category_chrome
        except Exception as exc:
            optional_failure("ui.category_chrome_load", exc)
            self._category_chrome = {}
            return {}

    def _render_genre_rows(self):
        if self.level != "genres":
            return
        _r51_t0=time.monotonic()
        chrome = self._category_chrome or self._load_category_chrome()
        try:
            selected = self["list"].getSelectedIndex()
        except Exception:
            selected = 0
        cfg = load_settings()
        rows = []
        # R51: the folder asset is identical for every category row.  Resolving
        # asset() inside the loop caused one filesystem existence probe per row
        # (911 probes on a large Live portal) before Enigma2 could paint anything.
        # Resolve it once and reuse the stable receiver-local path.
        _category_folder_icon=asset("us89_folder_yellow_42.png")
        _cat_max = 1320 if str(getattr(self, "media_type", "") or "").lower() in ("itv", "vod", "series") else 860
        card_w=max(426,min(_cat_max,int(getattr(self,"_category_row_card_w",426) or 426)))
        title_font=int(getattr(self,"_category_title_font",0) or 0)
        selected_title_font=int(getattr(self,"_category_selected_title_font",10) or 10)
        selected_ids=set(getattr(self,"_category_selected_ids",set()) or set()) if getattr(self,"_category_select_mode",False) else set()
        for i, e in enumerate(self.content_items):
            title = self._category_display_title(e, cfg)
            gid=_visibility_category_id(e)
            details = {
                "selected": i == selected,
                "checked": bool(gid and gid in selected_ids),
                "row_asset": chrome.get("row"),
                "row_selected_asset": chrome.get("row_selected"),
                "card_w": card_w,
                "title_font": title_font,
                "selected_title_font": selected_title_font,
            }
            # Folder identity is stable on first paint and during selection. The
            # check mark lives in its own fixed right-side column.
            rows.append((title, _category_folder_icon, e, details))
        self["list"].set_icon_rows(rows)
        try:
            self["list"].moveToIndex(max(0, min(selected, len(rows) - 1)))
        except Exception as exc:
            optional_failure("ui.silent_guard",exc)
        _mem34("browser_genres_rendered", media=str(getattr(self,"media_type","") or ""), rows=len(rows))
        try:
            LOG.info("PERF51 category_rows media=%s rows=%d elapsed_ms=%d",str(getattr(self,"media_type","") or ""),len(rows),int((time.monotonic()-_r51_t0)*1000))
        except Exception:
            pass

    def _set_root_rows(self):
        try:self._restore_browser_geometry()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._set_list_density("root")
        cfg=load_settings(); self.active_modes=[]
        enabled={"itv":cfg.get("show_live",True),"vod":cfg.get("show_movies",True),"series":cfg.get("show_series",True),"catchup":cfg.get("show_catchup",True)}
        for mode in self.ALL_MODES:
            if mode[1] not in enabled or enabled[mode[1]]: self.active_modes.append(mode)
        self.MODES=tuple(self.active_modes)
        rows=[]
        fav_count = len(self._favorites_for_portal()); recent_count = len(load_continue_watching())
        health = self.client.health_snapshot() if getattr(self.client, "token", None) else {"health":"ready","latency_ms":0}
        descriptions={"itv":"Professional channel list with lazy Now / Next EPG","vod":"Movies with artwork, details, favorites and watched state","series":"Series, seasons, episodes and resume support","catchup":"Archived programmes and replay TV","favorites":"Your saved content  •  %d items" % fav_count,"recent":"Continue watching  •  %d items" % recent_count,"account":"Subscription and portal health details","search":"One search across Live, Movies and Series","settings":"Themes, lists, Smart Engine, privacy and cache"}
        for title,key,icon in self.MODES: rows.append((_(title),asset(icon),key,_(descriptions[key])))
        self["blue"].setText(_("Open / Play"))
        self["list"].set_icon_rows(rows); self["counter"].setText(_("PORTAL HOME  •  %d sections") % len(rows))
        try:self["list"].moveToIndex(0)
        except Exception as exc:optional_failure("ui",exc)

    def _selection_changed(self):
        try: idx=self["list"].getSelectedIndex()
        except Exception: return
        if self.level == "root" and 0 <= idx < len(self.MODES):
            title,key,_icon=self.MODES[idx]
            self["info_title"].setText(_(title))
            self["info"].setText(_("Press OK to browse %s") % _(title))
            ph={"itv":"us80_section_live_530x382.png","vod":"us80_section_movies_530x382.png","series":"us80_section_series_530x382.png","favorites":"us81_section_favorites_530x382.png","recent":"us80_section_movies_530x382.png","settings":"us81_section_settings_530x382.png","search":"us81_section_search_530x382.png","catchup":"us81_section_catchup_530x382.png","account":"us81_section_settings_530x382.png"}.get(key,"us81_section_settings_530x382.png")
            # Root art is local and authoritative for this focus. Cancel any
            # provider-art request left behind by the previous content row.
            self._image_supersede()
            self._decode_picture(asset(ph))
        elif 0 <= idx < len(self.content_items):
            item=self.content_items[idx]
            if self.level == "genres":
                try:self._restore_category_side_identity()
                except Exception as exc:optional_failure("ui.category_identity_selection",exc)
                try:
                    chrome=self._category_chrome or self._load_category_chrome()
                    cfg=self._selection_settings();_cat_max=1320 if str(getattr(self,"media_type","") or "").lower() in ("itv","vod","series") else 860;card_w=max(426,min(_cat_max,int(getattr(self,"_category_row_card_w",426) or 426)));title_font=int(getattr(self,"_category_title_font",0) or 0);selected_title_font=int(getattr(self,"_category_selected_title_font",10) or 10)
                    for j in set((idx, getattr(self,"_genre_last_idx",idx))):
                        if 0 <= j < len(self.content_items):
                            e=self.content_items[j];title=self._category_display_title(e,cfg)
                            gid=_visibility_category_id(e)
                            details={"selected":j==idx,"checked":bool(getattr(self,"_category_select_mode",False) and gid in set(getattr(self,"_category_selected_ids",set()) or set())),"row_asset":chrome.get("row"),"row_selected_asset":chrome.get("row_selected"),"card_w":card_w,"title_font":title_font,"selected_title_font":selected_title_font}
                            self["list"].update_icon_row(j,(title,asset("us89_folder_yellow_42.png"),e,details))
                    # update_icon_row() invalidates only the old/new selected
                    # entries. A full list invalidate here repaints every category
                    # on each arrow press and defeats the incremental renderer.
                    self._genre_last_idx=idx
                except Exception as exc: optional_failure("ui.category_selection",exc)
                # Categories intentionally show no provider preview; invalidate a
                # late item-art worker from the page we just left.
                try:self._image_supersede()
                except Exception as exc:optional_failure("ui.category_preview_supersede",exc)
                return
            if self.level == "favorites_live":
                try:
                    old=int(getattr(self,"_favorite_live_last_idx",idx) or idx)
                    entries=list(getattr(self,"_favorite_card_entries",[]) or [])
                    for j in set((old,idx)):
                        if 0<=j<len(entries):
                            e=entries[j];row_item=dict(e.get("item") or {});row_item["_saved_media_type"]="itv"
                            title=self._favorite_clean_title("itv",row_item,120)
                            self["list"].update_icon_row(j,(title,self._favorite_live_icon(row_item),row_item,{"selected":j==idx}))
                    self._favorite_live_last_idx=idx
                except Exception as exc:optional_failure("ui.favorite_live_selection",exc)
                return
            _clean_titles=bool(self._selection_settings().get("clean_titles",True))
            raw_title=_provider_display_raw(item,_clean_titles,item.get("id") or _("Result"))
            title=(_clean_display_text(item.get("title") or item.get("name") or item.get("category_name") or item.get("genre_name") or item.get("id") or _("Categories"),100) or _("Categories")) if self.level=="genres" else (premium_title(raw_title,True) if _clean_titles else str(raw_title).strip())
            self["info_title"].setText(title[:34])
            if self.media_type=="itv" and self.level=="items": self._schedule_epg_preview(item)
            if self.level == "genres":
                try:self["accent_frame"].hide()
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                self["info"].setText(_("Category folder"))
                section_icon = {
                    "itv": "us80_section_live_530x382.png",
                    "vod": "us80_section_movies_530x382.png",
                    "series": "us80_section_series_530x382.png",
                }.get(self.media_type, "us81_placeholder_movies_480x345.png")
                self._decode_picture(asset(section_icon))
            else:
                if self._utility_mode_active():
                    try:
                        if self["accent_frame"].instance is not None:
                            self["accent_frame"].instance.setPixmap(None)
                        self["accent_frame"].hide()
                    except Exception as exc:optional_failure("ui.silent_guard",exc)
                else:
                    self._set_accent(title, poster=(self.media_type in ("vod","series") and self.level=="items"))
                meta=[]
                for k in ("year","number","rating","genre"):
                    if item.get(k): meta.append(str(item.get(k)))
                self["info"].setText("  •  ".join(meta)[:58] or self._mode_name(self.media_type))
                if self._utility_mode_active():
                    try:self["preview"].hide()
                    except Exception:pass
                else:
                    ph=(_letter_placeholder(title) if self.media_type=="itv" else ("us81_placeholder_movies_480x345.png" if self.media_type=="vod" else "us81_placeholder_series_480x345.png"))
                    self._load_item_image(item if (self.media_type=="itv" or (isinstance(item,dict) and item.get("_xtream"))) else _strip_portal_artwork(item),ph)

    def _set_accent(self, value, poster=False):
        name = _accent_for(value)
        filename = "accent_poster_%s.png" % name if poster else "accent_logo_%s.png" % name
        try:
            self["accent_frame"].instance.setPixmapFromFile(asset(filename))
            self["accent_frame"].show()
        except Exception as exc: optional_failure("ui",exc)

    def _category_tools_choices(self):
        choices=[(_("Global Search"),"global_search")]
        if self.media_type=="itv":choices.append((_("Export current Live folder to TV"),"export_live_folder"))
        elif self.media_type=="vod":choices.append((_("Export current Movies folder to TV"),"export_media_folder"))
        elif self.media_type=="series":choices.append((_("Export current Series folder to TV"),"export_media_folder"))
        choices.append((_("Manage Categories"),"manage_categories"))
        if len(_profile_hidden_ids(load_settings(),self.profile,self.media_type)):
            choices.append((_("Show all categories"),"show_all_categories"))
        choices += [
            (_("Pin / unpin current category"),"pin_category"),
            (_("Protect / unprotect category with PIN"),"protect_category"),
            (_("Hide / unhide current category"),"hide_category"),
            (_("Portal settings"),"settings"),
            (_("Favorites"),"favorites"),
            (_("Continue Watching"),"recent"),
        ]
        return choices

    def _category_manage_choices(self):
        return [
            (_("Select Categories"),"category_select_mode"),
            (_("Keep selected only"),"category_keep_selected"),
            (_("Hide selected"),"category_hide_selected"),
            (_("Show all categories"),"show_all_categories"),
            (_("Clear selection"),"category_clear_selection"),
            (_("Cancel category selection"),"category_cancel_selection"),
        ]

    def _category_menu_show(self,choices,level="root",selection=0,parent=None):
        self._category_menu_active=True;self._category_menu_level=str(level or "root");self._category_menu_parent=parent;self._category_menu_choices=list(choices or [])
        media = str(getattr(self, "media_type", "") or "").lower()
        if media in ("itv", "vod", "series"):
            self._category_menu_overlay.show(
                self._category_menu_choices,selection=selection,right=1910,region_top=500,region_h=430,
                on_accept=self._category_menu_choice_accepted,on_close=self._category_menu_back_from_overlay,
                min_card_w=320,max_card_w=760,padding=56,anchor_bottom=1068,
                visual_style="text_only",row_h=66,max_visible=5)
        else:
            self._category_menu_overlay.show(
                self._category_menu_choices,selection=selection,right=1860,region_top=520,region_h=400,
                on_accept=self._category_menu_choice_accepted,on_close=self._category_menu_back_from_overlay,
                min_card_w=240,max_card_w=460,padding=44,anchor_bottom=1068)

    def _category_menu_close(self):
        self._category_menu_active=False;self._category_menu_level=None;self._category_menu_parent=None;self._category_menu_choices=[]
        try:self._category_menu_overlay.hide()
        except Exception as exc:optional_failure("ui.category_inline_menu_close",exc)

    def _category_menu_back_from_overlay(self):
        if not getattr(self,"_category_menu_active",False):return
        if self._category_menu_level=="manage" and self._category_menu_parent=="root":
            choices=self._category_tools_choices();idx=0
            for i,c in enumerate(choices):
                if len(c)>1 and c[1]=="manage_categories":idx=i;break
            self._category_menu_show(choices,"root",idx,None)
        else:
            self._category_menu_active=False;self._category_menu_level=None;self._category_menu_parent=None;self._category_menu_choices=[]

    def _category_menu_back(self):
        if not getattr(self,"_category_menu_active",False):return False
        try:self._category_menu_overlay.close()
        except Exception as exc:optional_failure("ui.category_inline_menu_back",exc)
        return True

    def _category_menu_choice_accepted(self,choice):
        if not choice:
            self._category_menu_active=False;return
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else None
        if action=="manage_categories":
            self._category_menu_show(self._category_manage_choices(),"manage",0,"root");return
        self._category_menu_active=False
        if action=="category_select_mode":
            self._start_category_selection(reset=not bool(getattr(self,"_category_select_mode",False)));return
        self._tool_selected(choice)

    def _category_menu_accept(self):
        if not getattr(self,"_category_menu_active",False):return False
        try:return self._category_menu_overlay.accept()
        except Exception as exc:optional_failure("ui.category_inline_menu_accept",exc);return True

    def _nav_up(self):
        if getattr(self,"_category_menu_active",False):return self._category_menu_overlay.move("up")
        if self.level in ("favorites_root","favorites_cards"):return
        return self["list"].wrap_up()

    def _nav_down(self):
        if getattr(self,"_category_menu_active",False):return self._category_menu_overlay.move("down")
        if self.level in ("favorites_root","favorites_cards"):return
        return self["list"].wrap_down()

    def _nav_left(self):
        if getattr(self,"_category_menu_active",False):return self._category_menu_overlay.move("left")
        if self.level=="favorites_root":return self._favorite_root_move(-1)
        if self.level=="favorites_cards":return self._favorite_move(-1)
        return self["list"].page_left()

    def _nav_right(self):
        if getattr(self,"_category_menu_active",False):return self._category_menu_overlay.move("right")
        if self.level=="favorites_root":return self._favorite_root_move(1)
        if self.level=="favorites_cards":return self._favorite_move(1)
        return self["list"].page_right()

    def open_tools(self):
        if self.level=="genres":
            if getattr(self,"_category_menu_active",False):
                self._category_menu_close();return
            if getattr(self,"_category_select_mode",False):
                self._category_menu_show(self._category_manage_choices(),"manage",0,None);return
            self._category_menu_show(self._category_tools_choices(),"root",0,None);return
        if self.level == "items":
            choices = [(_("Global Search"), "global_search"), (_("EPG for selected channel"), "epg"), (_("Search current list"), "search")]
            if self.media_type in ("itv", "vod", "series"):
                choices += [((_('Send selected to Receiver') if self.media_type != "series" else _('Send selected Series to Receiver')), "send_receiver")]
            if self.media_type == "vod" and self.content_items:
                try: current_state=self._content_state_cache.get(id(self.content_items[self["list"].getSelectedIndex()]),{})
                except Exception: current_state={}
                choices += [(_("Mark selected unwatched"), "mark_current_unwatched")] if current_state.get("completed") else [(_("Mark selected watched"), "mark_current_watched")]
            if self._home_entry and self.initial_action == "recent":
                choices += [(_("Mark selected watched"), "mark_watched"), (_("Mark selected unwatched"), "mark_unwatched"), (_("Remove selected from Continue Watching"), "remove_history"), (_("Clear Continue Watching"), "clear_history")]
            choices += [(_("Hide / unhide current category"), "hide_category"), (_("Audio tracks"), "audio"), (_("Subtitles"), "subtitles"), (_("Next page"), "next"), (_("Previous page"), "prev"), (_("Portal settings"), "settings"), (_("Favorites"), "favorites"), (_("Continue Watching"), "recent")]
        else:
            choices = [(_("Global Search"), "global_search"), (_("Portal settings"), "settings"), (_("Favorites"), "favorites"), (_("Recently played"), "recent")]
        self.session.openWithCallback(self._tool_selected, ChoiceBox, title=_("Portal tools"), list=choices)

    def _tool_selected(self, choice):
        if not choice:
            return
        action = choice[1]
        if action == "global_search": self.open_global_search()
        elif action == "search": self.open_search()
        elif action == "export_live_folder": self.export_current_live_folder()
        elif action == "export_media_folder": self.export_current_media_folder()
        elif action == "send_receiver": self.send_selected_to_receiver()
        elif action == "pin_category": self.toggle_current_category_pinned()
        elif action == "protect_category": self.toggle_current_category_protected()
        elif action == "hide_category": self.toggle_current_category_hidden()
        elif action == "manage_categories": self._start_category_selection()
        elif action == "category_select_mode": self._start_category_selection(reset=not bool(getattr(self,"_category_select_mode",False)))
        elif action == "show_all_categories": self._show_all_categories()
        elif action == "category_keep_selected": self._keep_selected_categories_only()
        elif action == "category_hide_selected": self._hide_selected_categories()
        elif action == "category_select_all": self._select_all_visible_categories()
        elif action == "category_clear_selection": self._clear_category_selection()
        elif action == "category_cancel_selection": self._cancel_category_selection()
        elif action == "audio": self.open_audio()
        elif action == "subtitles": self.open_subtitles()
        elif action == "epg": self.show_selected_epg()
        elif action == "favorites": self._show_saved_entries(load_favorites(), "Favorites")
        elif action == "recent": self._show_saved_entries(load_continue_watching(), "Continue Watching")
        elif action == "next" and self.level == "items": self.load_items(self.media_type, self.genre, self.page + 1)
        elif action == "prev" and self.level == "items": self.load_items(self.media_type, self.genre, max(1, self.page - 1))
        elif action == "settings": self.open_playback_settings()
        elif action in ("mark_watched","mark_unwatched","remove_history"):
            self._history_action(action)
        elif action in ("mark_current_watched","mark_current_unwatched"):
            idx=self["list"].getSelectedIndex()
            if 0<=idx<len(self.content_items):
                item=self.content_items[idx];watched=action=="mark_current_watched";mark_watched(self.profile,self.media_type,item,watched);self._render_content_rows(keep_index=idx);self["status"].setText(_("Marked watched") if watched else _("Marked unwatched"))
        elif action == "clear_history":
            self.session.openWithCallback(self._clear_history_confirmed,MessageBox,_("Clear Continue Watching for this portal?"),MessageBox.TYPE_YESNO)

    def _receiver_cached_art(self, media_type, item, client=None, allow_fetch=False):
        """Resolve receiver artwork, fetching only a single selected Live picon when needed."""
        try:
            if media_type == "itv":
                raw = _image_url(item)
                cached = _cached_live_picon_path(raw, self.profile, item) or ""
                if cached or not allow_fetch or not raw:
                    return cached
                try:
                    return _download_live_portal_temp_picon(raw, self.profile, client or self.client, item=item, timeout=4.5) or ""
                except Exception:
                    return _download_public_live_picon(raw, self.profile, item=item, timeout=4.0) or ""
            snap = load_detail_snapshot(self.profile, media_type, item) or {}
            bundle = _load_visual_bundle(self.profile, media_type, item, snap) or {}
            return str(bundle.get("poster") or snap.get("poster_local") or "")
        except Exception as exc:
            optional_failure("ui.receiver_cached_art", exc)
            return ""

    def send_selected_to_receiver(self):
        if self._busy or self.level != "items" or self.media_type not in ("itv", "vod", "series"):
            return
        idx = self["list"].getSelectedIndex()
        if not (0 <= idx < len(self.content_items)):
            return
        item = dict(self.content_items[idx] or {})
        media_type = self.media_type
        cfg = load_settings()
        keep_index = idx
        raw_title = item.get("name") or item.get("title") or _("Result")
        title = premium_title(raw_title, load_settings().get("clean_titles", True))
        self["status"].setText(_("Sending to Receiver: %s") % title)

        def work(handle):
            isolated = _new_isolated_source_client(dict(self.profile or {}), timeout=min(6, int(cfg.get("timeout", 10) or 10)))
            try:
                if media_type in ("itv", "vod"):
                    row = dict(item)
                    row["_receiver_name"] = premium_title(row.get("name") or row.get("title") or _("Result"), cfg.get("clean_titles", True))
                    art = self._receiver_cached_art(media_type, row, client=isolated, allow_fetch=(media_type == "itv"))
                    if art:
                        row["_receiver_picon_local"] = art
                    result = export_receiver_items(
                        self.profile, [row], media_type,
                        _("Favorites") if media_type == "itv" else _("Movies"),
                        cfg.get("service_type", 4097),
                    )
                    return result

                # Series title itself is not a playable Enigma2 service. Export
                # its real episodes, keeping them grouped in one receiver bouquet.
                rows = []
                seasons = call_compatible(
                    isolated.series_seasons,
                    (((item,), {"cancel_event": handle.cancel_event}), ((item, handle.cancel_event), {}), ((item,), {})),
                ) or []
                series_name = _clean_display_text(item.get("name") or item.get("title") or "Series", 80)
                poster = self._receiver_cached_art("series", item)
                for season in seasons[:50]:
                    if handle.cancelled():
                        break
                    episodes = call_compatible(
                        isolated.series_episodes,
                        (((item, season), {"cancel_event": handle.cancel_event}), ((item, season, handle.cancel_event), {}), ((item, season), {})),
                    ) or []
                    season_no = int((season or {}).get("season") or (season or {}).get("season_id") or 0 or 0)
                    for pos, episode in enumerate(episodes, 1):
                        if not isinstance(episode, dict):
                            continue
                        row = dict(episode)
                        ep_no = row.get("episode_num") or row.get("episode") or row.get("number") or pos
                        try: ep_no = int(ep_no)
                        except Exception: ep_no = pos
                        try: s_no = int(row.get("season") or season_no or 0)
                        except Exception: s_no = season_no or 0
                        row["_receiver_name"] = "%s - S%02dE%02d" % (series_name, s_no, ep_no)
                        row["_receiver_media_type"] = "episode"
                        if poster:
                            row["_receiver_picon_local"] = poster
                        rows.append(row)
                        if len(rows) >= 1000:
                            break
                    if len(rows) >= 1000:
                        break
                if not rows:
                    raise RuntimeError(_("No playable episodes were found"))
                return export_receiver_items(self.profile, rows, "episode", series_name, cfg.get("service_type", 4097))
            finally:
                try: isolated.close()
                except Exception: pass

        def ok(result):
            try: self["list"].moveToIndex(max(0, min(keep_index, len(self.content_items)-1)))
            except Exception: pass
            reload_bouquets()
            count = int((result or {}).get("items") or 0)
            self["status"].setText(_("Sent to Receiver • %d item(s)") % count)
            self.session.open(MessageBox, _("Sent to the receiver bouquet list.\n\n%s\n%d item(s)") % (title, count), MessageBox.TYPE_INFO, timeout=8)

        def failed(exc):
            try: self["list"].moveToIndex(max(0, min(keep_index, len(self.content_items)-1)))
            except Exception: pass
            self["status"].setText(_("Send to Receiver failed: %s") % exc)

        self._run_async(work, ok, failed)

    def export_current_live_folder(self):
        """Export the selected Live category without disturbing category focus."""
        if self._busy or self.level != "genres" or self.media_type != "itv":
            return
        idx = self["list"].getSelectedIndex()
        if not (0 <= idx < len(self.content_items)):
            return
        category = dict(self.content_items[idx] or {})
        category_id = str(category.get("id") or category.get("genre_id") or category.get("name") or category.get("title") or "*")
        category_name = _clean_display_text(category.get("name") or category.get("title") or category_id or _("LIVE"), 80)
        keep_index = idx
        self["status"].setText(_("Exporting Live folder: %s") % category_name)
        cfg = load_settings()

        def work(handle):
            isolated = _new_isolated_source_client(dict(self.profile or {}), timeout=cfg.get("timeout", 10))
            try:
                rows = call_compatible(
                    isolated.ordered_all,
                    ((("itv", category_id), {"start_page":1, "max_pages":500, "max_items":25000, "cancel_event":handle.cancel_event}),
                     (("itv", category_id, 1, 500, 25000, handle.cancel_event), {})),
                ) or []
                if handle.cancelled():
                    return {}
                # Live-only export rule: use the same final label as Premium Live
                # and only this channel's already-cached picon. No download, scan,
                # refit, fallback substitution, or second cleanup pass here.
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    raw = row.get("name") or row.get("title") or row.get("id") or "Channel"
                    row["_receiver_name"] = _clean_live_channel_name(raw)
                    try:
                        cached = _cached_live_picon_path(_image_url(row), self.profile, row)
                        if cached:
                            row["_receiver_picon_local"] = cached
                    except Exception:
                        pass
                return export_live_category_bouquet(self.profile, rows, category_name, category_id, cfg.get("service_type", 4097))
            finally:
                try: isolated.close()
                except Exception: pass

        def ok(result):
            # Critical: do NOT call load_genres()/refresh_current() here.  That
            # old path reset the category rail to index 0 after export.
            try:
                self["list"].moveToIndex(max(0, min(keep_index, len(self.content_items)-1)))
            except Exception:
                pass
            reload_bouquets()
            count = int((result or {}).get("channels") or 0)
            self["status"].setText(_("Live folder exported • %d channels") % count)
            self.session.open(MessageBox, _("Live folder exported to the TV bouquet list.\n\n%s\n%d channels") % (category_name, count), MessageBox.TYPE_INFO, timeout=10)

        def failed(exc):
            try:
                self["list"].moveToIndex(max(0, min(keep_index, len(self.content_items)-1)))
            except Exception:
                pass
            self["status"].setText(_("Live folder export failed: %s") % exc)

        self._run_async(work, ok, failed)

    def export_current_media_folder(self):
        """Export Movies quickly, or Series as one child bouquet per series."""
        if self._busy or self.level != "genres" or self.media_type not in ("vod", "series"):
            return
        idx = self["list"].getSelectedIndex()
        if not (0 <= idx < len(self.content_items)):
            return
        category = dict(self.content_items[idx] or {})
        category_id = str(category.get("id") or category.get("genre_id") or category.get("name") or category.get("title") or "*")
        category_name = _clean_display_text(category.get("name") or category.get("title") or category_id or _("Result"), 80)
        media_type = self.media_type
        keep_index = idx
        cfg = load_settings()
        self["status"].setText(_("Exporting %s folder: %s") % ((_("Movies") if media_type == "vod" else _("Series")), category_name))

        def _local_art_only(row):
            # Export must never scan detail snapshots or rebuild artwork. Only reuse
            # a path already present on the resolved row, otherwise skip the picon.
            for key in ("_receiver_picon_local", "poster_local", "cover_local", "image_local", "picon_local", "logo_local"):
                path = str((row or {}).get(key) or "")
                try:
                    if path and os.path.isfile(path):
                        return path
                except Exception:
                    pass
            return ""

        def work(handle):
            isolated = _new_isolated_source_client(dict(self.profile or {}), timeout=min(6, int(cfg.get("timeout", 10) or 10)))
            try:
                items = call_compatible(
                    isolated.ordered_all,
                    (((media_type, category_id), {"start_page":1, "max_pages":500, "max_items":25000, "cancel_event":handle.cancel_event}),
                     ((media_type, category_id, 1, 500, 25000, handle.cancel_event), {})),
                ) or []
                if handle.cancelled():
                    return {}

                if media_type == "vod":
                    rows = []
                    for item in items:
                        if not isinstance(item, dict) or not (item.get("cmd") or item.get("command") or item.get("url")):
                            continue
                        row = dict(item)
                        row["_receiver_name"] = premium_title(row.get("name") or row.get("title") or "Movie", cfg.get("clean_titles", True))
                        art = _local_art_only(row)
                        if art:
                            row["_receiver_picon_local"] = art
                        rows.append(row)
                    if not rows:
                        raise RuntimeError(_("No playable movies were found"))
                    return export_receiver_items(self.profile, rows, "vod", category_name, cfg.get("service_type", 4097))

                # One worker per series (bounded). Each worker owns its client,
                # avoiding the old serial Series -> Season -> Episodes chain.
                series_items = [dict(x) for x in items if isinstance(x, dict)]
                if not series_items:
                    raise RuntimeError(_("No series were found"))

                def fetch_series(series):
                    client = _new_isolated_source_client(dict(self.profile or {}), timeout=min(6, int(cfg.get("timeout", 10) or 10)))
                    try:
                        series_name = premium_title(series.get("name") or series.get("title") or "Series", cfg.get("clean_titles", True))
                        poster = _local_art_only(series)
                        try:
                            seasons = call_compatible(
                                client.series_seasons,
                                (((series,), {"cancel_event":handle.cancel_event}), ((series, handle.cancel_event), {}), ((series,), {})),
                            ) or []
                        except Exception:
                            seasons = []
                        rows = []
                        for season in seasons:
                            if handle.cancelled():
                                break
                            try:
                                episodes = call_compatible(
                                    client.series_episodes,
                                    (((series, season), {"cancel_event":handle.cancel_event}), ((series, season, handle.cancel_event), {}), ((series, season), {})),
                                ) or []
                            except Exception:
                                episodes = []
                            try: season_no = int((season or {}).get("season") or (season or {}).get("season_id") or 0)
                            except Exception: season_no = 0
                            for pos, episode in enumerate(episodes, 1):
                                if not isinstance(episode, dict) or not (episode.get("cmd") or episode.get("command") or episode.get("url")):
                                    continue
                                row = dict(episode)
                                try: ep_no = int(row.get("episode_num") or row.get("episode") or row.get("number") or pos)
                                except Exception: ep_no = pos
                                try: s_no = int(row.get("season") or season_no or 0)
                                except Exception: s_no = season_no or 0
                                row["_receiver_name"] = "%s - S%02dE%02d" % (series_name, s_no, ep_no)
                                row["_receiver_media_type"] = "episode"
                                if poster:
                                    row["_receiver_picon_local"] = poster
                                rows.append(row)
                        return {"name": series_name, "poster": poster, "episodes": rows}
                    finally:
                        try: client.close()
                        except Exception: pass

                groups = []
                workers = max(1, min(4, len(series_items)))
                pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ultrastalker-series-export")
                try:
                    futures = [pool.submit(fetch_series, series) for series in series_items]
                    for future in as_completed(futures):
                        if handle.cancelled():
                            break
                        try:
                            group = future.result()
                        except Exception:
                            group = None
                        if isinstance(group, dict) and group.get("episodes"):
                            groups.append(group)
                finally:
                    pool.shutdown(wait=False)
                if handle.cancelled():
                    return {}
                if not groups:
                    raise RuntimeError(_("No playable episodes were found"))
                # Preserve the provider/category order rather than completion order.
                order = {premium_title(x.get("name") or x.get("title") or "Series", cfg.get("clean_titles", True)): n for n, x in enumerate(series_items)}
                groups.sort(key=lambda g: order.get(g.get("name"), 999999))
                return export_series_category_bouquets(self.profile, groups, category_name, category_id, cfg.get("service_type", 4097))
            finally:
                try: isolated.close()
                except Exception: pass

        def ok(result):
            try: self["list"].moveToIndex(max(0, min(keep_index, len(self.content_items)-1)))
            except Exception: pass
            reload_bouquets()
            count = int((result or {}).get("items") or 0)
            series_count = int((result or {}).get("series") or 0)
            if media_type == "series":
                self["status"].setText(_("Series folder sent • %d series") % series_count)
                self.session.open(MessageBox, _("Series folder exported.\n\n%s\n%d series • %d episodes") % (category_name, series_count, count), MessageBox.TYPE_INFO, timeout=10)
            else:
                self["status"].setText(_("Movies folder sent • %d movie(s)") % count)
                self.session.open(MessageBox, _("Movies folder exported.\n\n%s\n%d movie(s)") % (category_name, count), MessageBox.TYPE_INFO, timeout=10)

        def failed(exc):
            try: self["list"].moveToIndex(max(0, min(keep_index, len(self.content_items)-1)))
            except Exception: pass
            self["status"].setText(_("Folder export failed: %s") % exc)

        self._run_async(work, ok, failed)

    def _history_action(self,action):
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):return
        item=self.content_items[idx];media_type=item.get("_saved_media_type") or self.media_type or "vod"
        clean=dict(item);clean.pop("_saved_media_type",None);clean.pop("_history_position",None);clean.pop("_history_duration",None);clean.pop("_history_completed",None)
        try:
            if action=="remove_history":remove_from_history(self.profile,media_type,clean);message=_("Removed from Continue Watching")
            else:mark_watched(self.profile,media_type,clean,action=="mark_watched");message=_("Marked watched") if action=="mark_watched" else _("Marked unwatched")
            self._show_saved_entries(load_continue_watching(),"Continue Watching");self["status"].setText(message)
        except Exception as exc:self["status"].setText(_("History update failed: %s")%exc)

    def _clear_history_confirmed(self,answer):
        if not answer:return
        try:clear_history(self.profile);self._show_saved_entries([],"Continue Watching");self["status"].setText(_("Continue Watching cleared"))
        except Exception as exc:self["status"].setText(_("Clear failed: %s")%exc)

    def show_selected_epg(self):
        if self.level != "items" or self.media_type != "itv":
            self["status"].setText(_("EPG is available for live channels"))
            return
        idx = self["list"].getSelectedIndex()
        if not (0 <= idx < len(self.content_items)):
            return
        item = self.content_items[idx]
        channel_id = item.get("id") or item.get("ch_id")
        if not channel_id:
            self["status"].setText(_("Channel has no EPG identifier"))
            return
        self._epg_record_channel = dict(item)
        self["status"].setText(_("Loading EPG..."))
        now = int(time.time())
        hours = int(load_settings().get("epg_hours", 4) or 4)
        def ok(rows):
            choices=[]
            for row in rows[:40]:
                if not isinstance(row, dict):
                    continue
                title=_clean_display_text(row.get("name") or row.get("title") or row.get("descr") or "Programme", 100)
                begin,end=event_times(row)
                if begin:
                    stamp=time.strftime("%H:%M",time.localtime(begin))
                    if end:
                        stamp += "–" + time.strftime("%H:%M",time.localtime(end))
                else:
                    stamp=_clean_display_text(row.get("time") or row.get("start") or "",24)
                choices.append(((stamp+"  "+title).strip(), row))
            if not choices:
                self.session.open(MessageBox,_("No EPG data returned"),MessageBox.TYPE_INFO,timeout=8)
                self["status"].setText(_("Ready"))
                return
            self.session.openWithCallback(self._epg_program_selected,ChoiceBox,title=_("EPG • choose a programme"),list=choices)
            self["status"].setText(_("Choose a programme"))
        self._run_async(
            lambda: self.client.full_epg(channel_id, now - 3600, now + (hours * 3600), max_pages=120),
            ok, lambda e: self["status"].setText(_("EPG failed: %s") % e)
        )

    def _epg_program_selected(self, choice):
        if not choice or not isinstance(choice[1], dict):
            self["status"].setText(_("Ready"))
            return
        self._selected_epg_program = dict(choice[1])
        self.session.openWithCallback(
            self._epg_program_action, ChoiceBox, title=str(choice[0])[:120],
            list=[(_("Record this programme"), "record"), (_("Programme details"), "details")],
        )

    def _epg_program_action(self, choice):
        if not choice:
            return
        program=getattr(self,"_selected_epg_program",{}) or {}
        channel=getattr(self,"_epg_record_channel",{}) or {}
        if choice[1] == "details":
            title=str(program.get("name") or program.get("title") or program.get("descr") or "Programme")
            description=str(program.get("description") or program.get("descr") or program.get("plot") or _("No programme description"))
            self.session.open(MessageBox,title+"\n\n"+description,MessageBox.TYPE_INFO,timeout=15)
            return
        if choice[1] != "record":
            return
        self["status"].setText(_("Preparing recording timer..."))
        cfg=load_settings()
        def work():
            return prepare_portal_recording(self.profile,channel,program,cfg.get("service_type",4097))
        def installed(prepared):
            try:
                timer=install_recording_timer(self.session,prepared)
                begin=int(getattr(timer,"begin",prepared.get("begin",0)) or 0)
                self["status"].setText(_("Recording timer added • %s") % (time.strftime("%H:%M",time.localtime(begin)) if begin else _("scheduled")))
            except Exception as exc:
                self["status"].setText(_("Timer failed: %s") % exc)
        self._run_async(work,installed,lambda e:self["status"].setText(_("Timer failed: %s")%e))

    def open_playback_settings(self):
        cfg = load_settings()
        cache_files = 0
        cache_mb = 0.0
        try:
            names = [os.path.join(IMAGE_CACHE_DIR, x) for x in os.listdir(IMAGE_CACHE_DIR)] if hdd_read_ready(force=True) else []
            cache_files = len([x for x in names if os.path.isfile(x)])
            cache_mb = sum(os.path.getsize(x) for x in names if os.path.isfile(x)) / (1024.0 * 1024.0)
        except Exception as exc:
            optional_failure("ui", exc)
        choices = [
            (_("Playback engine  •  %s") % cfg.get("service_type", 4097), "service"),
            (_("Portal timeout  •  %ss") % cfg.get("timeout", 10), "timeout"),
            (_("EPG window  •  %sh") % cfg.get("epg_hours", 4), "epg"),
            (_("Poster loading  •  %s") % (_("ON") if cfg.get("load_images", True) else _("OFF")), "images"),
            (_("Clear image cache  •  %d files / %.1f MB") % (cache_files, cache_mb), "clear_cache"),
            (_("Visible home sections"), "sections"),
            (_("Live preview  •  %s") % (_("ON") if cfg.get("live_preview", False) else _("OFF")), "preview"),
            (_("Parental lock  •  %s") % (_("ON") if cfg.get("parental_lock", False) else _("OFF")), "parental"),
            (_("Change parental PIN"), "pin"),
            (_("Manage hidden categories"), "hidden"),
            (_("Interface  •  Nova FHD / 1920×1080"), "interface"),
            (_("Clean technical prefixes  •  %s") % (_("ON") if cfg.get("clean_titles",True) else _("OFF")), "clean_titles"),
            (_("Quality badges  •  %s") % (_("ON") if cfg.get("show_quality_badges",True) else _("OFF")), "quality_badges"),
            (_("Channel list  •  %s") % cfg.get("channel_list_mode","epg").upper(), "channel_list_mode"),
            (_("Player Engine Lock  •  ON"), "engine_lock_info"),
        ]
        self.session.openWithCallback(self._premium_setting_selected, ChoiceBox, title=_("Portal settings"), list=choices)

    def _premium_setting_selected(self, choice):
        if not choice:
            return
        action = choice[1]
        if action == "service":
            current = load_settings().get("service_type", 4097)
            choices = [("DVB / native (1)", 1), ("IPTV / GStreamer (4097)", 4097), ("GstPlayer (5001)", 5001), ("ExtePlayer3 (5002)", 5002), ("ServiceApp / DreamOS (8193)", 8193)]
            self.session.openWithCallback(self._service_type_selected, ChoiceBox, title=_("Playback service type (current: %s)") % current, list=choices)
        elif action == "timeout":
            choices = [(_("%d seconds") % x, x) for x in (5, 8, 10, 15, 20, 30)]
            self.session.openWithCallback(lambda c: self._save_numeric_setting("timeout", c), ChoiceBox, title=_("Portal timeout"), list=choices)
        elif action == "epg":
            choices = [(_("%d hours") % x, x) for x in (2, 4, 6, 8, 12, 24)]
            self.session.openWithCallback(lambda c: self._save_numeric_setting("epg_hours", c), ChoiceBox, title=_("EPG window"), list=choices)
        elif action in ("clean_titles", "quality_badges", "remember_location"):
            key = {"quality_badges":"show_quality_badges"}.get(action, action)
            enabled = not bool(load_settings().get(key, True)); save_settings({key: enabled})
            self["status"].setText(_("Setting updated"))
            self.refresh_current()
        elif action == "engine_lock_info":
            self.session.open(MessageBox,_("Playback engine is locked to the value selected under Playback engine. Automatic Smart Engine switching is disabled."),MessageBox.TYPE_INFO,timeout=7)
        elif action == "channel_list_mode":
            modes=[(_("Professional EPG"),"epg"),(_("Compact"),"compact"),(_("Large"),"large")]
            self.session.openWithCallback(self._channel_mode_selected, ChoiceBox, title=_("Channel list layout"), list=modes)
        elif action == "images":
            enabled = not bool(load_settings().get("load_images", True))
            save_settings({"load_images": enabled})
            self._image_load_images=bool(enabled);self._selection_cfg_mono=0.0
            self["status"].setText(_("Poster loading enabled") if enabled else _("Poster loading disabled"))
        elif action == "clear_cache":
            removed = cleanup_image_cache(max_files=0, max_bytes=0)
            try: prune_persistent_cache(0)
            except Exception as exc: optional_failure("cache-prune", exc)
            self["status"].setText(_("Artwork + metadata cache cleaned"))
        elif action == "sections":
            self.open_section_settings()
        elif action == "preview":
            enabled=not bool(load_settings().get("live_preview",False)); save_settings({"live_preview":enabled}); self["status"].setText(_("Live preview enabled") if enabled else _("Live preview disabled"))
        elif action == "parental":
            cfg=load_settings();enabled=not bool(cfg.get("parental_lock",False))
            if enabled and parental_pin_is_default(cfg):
                self.session.openWithCallback(
                    self._save_parental_pin_and_enable,
                    InputBox,
                    title=_("Set parental PIN to enable lock (4-8 digits)"),
                    text="",
                    maxSize=8,
                    type=Input.PIN,
                )
                return
            save_settings({"parental_lock":enabled})
            parental_lock_now()
            self["status"].setText(_("Parental lock enabled") if enabled else _("Parental lock disabled"))
            self.refresh_current()
        elif action == "pin":
            self.session.openWithCallback(self._save_parental_pin, InputBox, title=_("New parental PIN (4-8 digits)"), text="", maxSize=8, type=Input.PIN)
        elif action == "hidden":
            self.show_hidden_categories()
        elif action == "interface":
            self.session.open(MessageBox, _("Nova FHD is the only interface in this build. All plugin screens use the same 1920×1080 design system."), MessageBox.TYPE_INFO, timeout=8)

    def _save_numeric_setting(self, key, choice):
        if choice:
            save_settings({key: int(choice[1])})
            self["status"].setText(_("Setting saved"))

    def _browser_theme_selected(self, choice):
        if choice:
            save_theme(choice[1])
            self["status"].setText(_("Theme saved. Restart Enigma2 to apply"))

    def _channel_mode_selected(self, choice):
        if choice:
            save_settings({"channel_list_mode": choice[1]}); self["status"].setText(_("Channel layout saved: %s") % choice[0])
            self.refresh_current()

    def _service_type_selected(self, choice):
        if choice:
            save_settings({"service_type": int(choice[1])})
            self["status"].setText(_("Playback service type saved: %s") % choice[1])

    def _stop_favorite_art_jobs(self):
        self._favorite_art_generation += 1
        for future in list(getattr(self,"_favorite_art_futures",set()) or set()):
            try:future.cancel()
            except Exception:pass
        try:self._favorite_art_futures.clear()
        except Exception:pass
        try:self._favorite_art_pending.clear()
        except Exception:pass
        try:
            while True:self._favorite_art_jobs.get_nowait()
        except Exception:pass

    def _favorite_entry_key(self, media_type, item):
        item=item if isinstance(item,dict) else {}
        raw="|".join((str(media_type or ""),str(item.get("id") or item.get("movie_id") or item.get("series_id") or item.get("stream_id") or ""),str(item.get("name") or item.get("title") or ""),str(_image_url(item) or "")))
        return hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()[:24]

    def _favorite_clean_title(self, media_type, item, max_chars=80):
        """Favorites always use the same cleaned catalogue names as normal browsing.

        Search intentionally keeps provider/raw names so source provenance stays visible;
        Favorites are a personal library, so technical/provider noise is not useful there.
        """
        item=item if isinstance(item,dict) else {}
        mt=str(media_type or "vod").lower()
        raw=item.get("name") or item.get("title") or item.get("id") or (_("Channel") if mt=="itv" else _("Result"))
        if mt=="itv":
            cleaned=_clean_live_channel_name(raw,True)
        else:
            cleaned=premium_title(raw,True)
        return _clean_display_text(cleaned or raw,max_chars)

    def _favorites_for_portal(self, media_type=None):
        """Favorites are owned by the exact portal profile, not server URL alone."""
        portal=str(self.profile.get("portal") or "").rstrip("/").lower()
        mac=str(self.profile.get("mac") or "").upper()
        wanted=str(media_type or "").lower()
        out=[]
        for entry in (load_favorites() or []):
            if not isinstance(entry,dict) or not isinstance(entry.get("item"),dict):continue
            if str(entry.get("portal") or "").rstrip("/").lower()!=portal:continue
            # R135 safety: same endpoint with another MAC/M3U synthetic identity
            # is a different portal profile. Never expose or play its saved IDs.
            if str(entry.get("mac") or "").upper()!=mac:continue
            mt=str(entry.get("media_type") or "vod").lower()
            if wanted and mt!=wanted:continue
            out.append(entry)
        return out

    def _apply_favorites_static_mode(self, show_list=False):
        """Favorites is fully static visually; only content artwork may load."""
        try:
            bg=asset("category_palestine_static_1920x1080.jpg")
            self._browser_geom("page_adaptive_bg",0,0,1920,1080)
            if self["page_adaptive_bg"].instance is not None and os.path.isfile(bg):
                if str(getattr(self,"_favorites_static_bg_loaded","") or "") != bg:
                    self["page_adaptive_bg"].instance.setPixmapFromFile(bg)
                    self._favorites_static_bg_loaded=bg
                self["page_adaptive_bg"].show()
        except Exception as exc:optional_failure("ui.favorites_static_bg",exc)
        for name in ("brand_header","title","counter","section","top_divider","preview","accent_frame","info_card1","info_card2","info_card3","info_icon1","info_icon2","info_icon3","info_title","info","status","red_bg","green_bg","yellow_bg","blue_bg","red","green","yellow","blue"):
            try:self[name].hide()
            except Exception:pass
        try:
            if show_list:self["list"].show()
            else:self["list"].hide()
        except Exception:pass

    def _hide_favorite_root_cards(self):
        for slot in range(3):
            for n in ("fav_root_bg%d"%slot,"fav_root_sel%d"%slot,"fav_root_icon%d"%slot,"fav_root_title%d"%slot,"fav_root_meta%d"%slot):
                try:self[n].hide()
                except Exception:pass

    def _render_favorites_root(self):
        """Render the exact Home card component for Live / Movies / Series.

        These three cards stay on screen even while their inline favorite grid
        is open.  The same Home fallback/focus pixmaps and 112x82 Home icons are
        reused; only the subtitle changes to the saved-item count.
        """
        entries=(
            ("itv",_("Live TV"),"home_live.png"),
            ("vod",_("Movies"),"home_movies.png"),
            ("series",_("Series"),"home_series.png"),
        )
        fixed=fixed_home_assets() or {}
        fixed_mood=fixed.get("mood") if isinstance(fixed.get("mood"),dict) else {}
        fixed_focus=fixed.get("focus") if isinstance(fixed.get("focus"),dict) else {}
        normal_card=str(fixed_mood.get("menu") or asset("home129_menu_fallback.png"))
        focus_card=str(fixed_focus.get("menu") or asset("home129_menu_focus.png"))
        xs=(360,720,1080);y=420
        active=int(getattr(self,"_favorite_root_index",0) or 0)
        for slot,(mt,label,icon_name) in enumerate(entries):
            x=xs[slot];selected=(slot==active);count=len(self._favorites_for_portal(mt))
            try:
                self._browser_geom("fav_root_bg%d"%slot,x,y,238,190)
                self["fav_root_bg%d"%slot].instance.setPixmapFromFile(normal_card);self["fav_root_bg%d"%slot].show()
            except Exception:pass
            try:
                self._browser_geom("fav_root_sel%d"%slot,x,y,238,190)
                self["fav_root_sel%d"%slot].instance.setPixmapFromFile(focus_card)
                (self["fav_root_sel%d"%slot].show() if selected else self["fav_root_sel%d"%slot].hide())
            except Exception:pass
            try:
                self._browser_geom("fav_root_icon%d"%slot,x+63,y+14,112,82)
                self["fav_root_icon%d"%slot].instance.setPixmapFromFile(asset(icon_name));self["fav_root_icon%d"%slot].show()
            except Exception:pass
            try:
                self._browser_geom("fav_root_title%d"%slot,x+13,y+102,212,32)
                self["fav_root_title%d"%slot].setText(label);self["fav_root_title%d"%slot].show()
            except Exception:pass
            try:
                self._browser_geom("fav_root_meta%d"%slot,x+13,y+140,212,28)
                self["fav_root_meta%d"%slot].setText(_("%d items")%count);self["fav_root_meta%d"%slot].show()
            except Exception:pass
        _mem34("favorites_root_rendered", rows=3)

    def _show_favorites_root(self):
        self._favorite_art_generation+=1;self._favorites_bucket_type=None;self._favorite_card_entries=[];self._favorite_card_index=0
        self._favorite_root_index=max(0,min(2,int(getattr(self,"_favorite_root_index",0) or 0)))
        self.level="favorites_root";self.media_type=None;self.content_items=[];self.all_content_items=[]
        self._apply_favorites_static_mode(show_list=False);self._hide_favorite_cards();self._render_favorites_root()

    def _favorite_root_move(self, delta):
        self._favorite_root_index=(int(getattr(self,"_favorite_root_index",0) or 0)+int(delta))%3
        self._render_favorites_root()

    def _favorite_live_icon(self,item):
        key=self._favorite_entry_key("itv",item)
        path=str((getattr(self,"_favorite_art_local",{}) or {}).get(key) or "")
        if not (path and os.path.isfile(path)):
            try:
                cached=_cached_live_picon_path(_image_url(item),self.profile,item) or ""
                if cached and os.path.isfile(cached):path=_fit_live_picon_canvas(cached,PERSISTENT_GENERATED_DIR,(220,132)) or cached
            except Exception:path=""
        return path if path and os.path.isfile(path) else asset("home_live.png")

    def _show_favorites_bucket(self, media_type):
        """Open a favorites bucket inline, without leaving the Favorites page."""
        mt=str(media_type or "vod").lower();self._favorites_bucket_type=mt;self.media_type=mt
        self._favorite_art_generation+=1;self._favorite_art_pending.clear();self._favorite_card_index=0
        entries=self._favorites_for_portal(mt);self._favorite_card_entries=list(entries)
        self.level="favorites_cards";self._apply_favorites_static_mode(show_list=False);self._render_favorites_root();self._render_favorite_cards()

    def _schedule_favorite_artwork(self, entries, media_type):
        """Hydrate only the visible favorite page so Favorites stays lightweight."""
        mt=str(media_type or "vod").lower();generation=int(getattr(self,"_favorite_art_generation",0) or 0);profile=dict(self.profile or {})
        for entry in list(entries or []):
            item=dict((entry or {}).get("item") or {})
            if not item:continue
            key=self._favorite_entry_key(mt,item)
            if key in self._favorite_art_pending:continue
            if mt=="itv":
                existing=self._favorite_live_icon(item)
                if existing and os.path.isfile(existing) and os.path.basename(existing)!="home_live.png":continue
            else:
                art,poster,_bundle,_snap=self._favorite_poster_path(mt,item)
                if art and os.path.isfile(art) and "placeholder" not in os.path.basename(art).casefold():continue
            self._favorite_art_pending.add(key)
            def work(_mt=mt,_item=item,_key=key,_gen=generation):
                path="";isolated=None
                try:
                    isolated=_new_isolated_source_client(profile,timeout=6)
                    if _mt=="itv":
                        raw=_image_url(_item)
                        path=_cached_live_picon_path(raw,profile,_item) or ""
                        if not path and raw:
                            try:path=_download_live_portal_temp_picon(raw,profile,isolated,item=_item,timeout=5.0) or ""
                            except Exception:path=_download_public_live_picon(raw,profile,item=_item,timeout=4.0) or ""
                        if path and os.path.isfile(path):path=_fit_live_picon_canvas(path,PERSISTENT_GENERATED_DIR,(220,132)) or path
                    else:
                        snap=load_detail_snapshot(profile,_mt,_item) or {};bundle=_load_visual_bundle(profile,_mt,_item,snap) or {};poster=str(bundle.get("poster") or snap.get("poster_local") or "")
                        if not (poster and os.path.isfile(poster)):
                            raw=_image_url(_item);resolved=_resolve_portal_artwork_url(raw,profile,_item) if raw else ""
                            if resolved:poster=_download_portal_artwork(resolved,profile,isolated,False,5.5,None,item=_item) or ""
                            if poster and os.path.isfile(poster):
                                try:_save_visual_bundle(profile,_mt,_item,{"poster":poster},snapshot=snap)
                                except Exception:pass
                        if poster and os.path.isfile(poster):
                            digest=hashlib.sha1((poster+"|favorite-grid-206x310-v4").encode("utf-8","ignore")).hexdigest()[:20];thumb=os.path.join(PERSISTENT_GENERATED_DIR,"favorite_grid_%s.png"%digest)
                            if not os.path.isfile(thumb):_build_cover_thumbnail(poster,thumb,(206,310))
                            if os.path.isfile(thumb):
                                path=thumb
                                try:_save_visual_bundle(profile,_mt,_item,{"grid_thumb":thumb,"poster":poster},snapshot=snap)
                                except Exception:pass
                except Exception as exc:optional_failure("ui.favorite_art_worker",exc)
                finally:
                    if isolated is not None:
                        try:isolated.close()
                        except Exception:pass
                try:self._favorite_art_jobs.put((_gen,_key,_mt,path))
                except Exception:pass
            try:
                future=_IMAGE_EXECUTOR.submit(work);self._favorite_art_futures.add(future)
                future.add_done_callback(lambda f,s=self: s._favorite_art_futures.discard(f))
            except Exception:
                self._favorite_art_pending.discard(key)

    def _drain_favorite_art_jobs(self):
        changed=False
        while True:
            try:gen,key,mt,path=self._favorite_art_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            self._favorite_art_pending.discard(key)
            if int(gen)!=int(getattr(self,"_favorite_art_generation",0) or 0):continue
            if path and os.path.isfile(path):self._favorite_art_local[key]=path;changed=True
        if changed and self.level=="favorites_cards":
            try:self._render_favorite_cards()
            except Exception:pass

    def _hide_favorite_cards(self):
        for _fi in range(7):
            for _name in ("fav_card%d"%_fi,"fav_sel%d"%_fi,"fav_art%d"%_fi,"fav_title%d"%_fi,"fav_meta%d"%_fi):
                try:self[_name].hide()
                except Exception:pass

    def _favorite_poster_path(self, media_type, item):
        """Return the light 206x310 poster derivative plus its source."""
        try:
            key=self._favorite_entry_key(media_type,item)
            local=str((getattr(self,"_favorite_art_local",{}) or {}).get(key) or "")
            if local and os.path.isfile(local):return local,local,{},{}
            snap=load_detail_snapshot(self.profile,media_type,item) or {}
            bundle=_load_visual_bundle(self.profile,media_type,item,snap) or {}
            poster=str(bundle.get("poster") or snap.get("poster_local") or "")
            thumb=str(bundle.get("grid_thumb") or "")
            if thumb and os.path.isfile(thumb):return thumb,poster or thumb,bundle,snap
            if poster and os.path.isfile(poster):
                key2=hashlib.sha1((poster+"|favorite-grid-206x310-v4").encode("utf-8","ignore")).hexdigest()[:20]
                out=os.path.join(PERSISTENT_GENERATED_DIR,"favorite_grid_%s.png"%key2)
                if not os.path.isfile(out):_build_cover_thumbnail(poster,out,(206,310))
                if os.path.isfile(out):
                    try:_save_visual_bundle(self.profile,media_type,item,{"grid_thumb":out},snapshot=snap)
                    except Exception:pass
                    return out,poster,bundle,snap
        except Exception as exc:optional_failure("ui.favorite_grid_art",exc)
        ph=asset("grid_placeholder_series_921.png" if media_type=="series" else "grid_placeholder_movie_921.png")
        return ph,ph,{},{}

    def _favorite_set_title_color(self, slot, selected):
        try:
            inst=self["fav_title%d"%slot].instance
            if inst is not None:inst.setForegroundColor(parseColor("#00E676" if selected else "#FFFFFF"))
        except Exception:pass

    def _render_favorite_cards(self):
        """Render one inline seven-item page under the fixed Home cards.

        Live uses only 220x132 picons + channel name. Movies/Series use only a
        206x310 poster + title. No frames, ratings, years or metadata are drawn.
        """
        entries=list(getattr(self,"_favorite_card_entries",[]) or []);mt=str(getattr(self,"_favorites_bucket_type",None) or "vod").lower()
        total=len(entries);self._hide_favorite_cards()
        if total<=0:
            try:
                self._browser_geom("fav_title0",680,720,560,44);self["fav_title0"].setText(_("No saved content"));self._favorite_set_title_color(0,False);self["fav_title0"].show()
            except Exception:pass
            _mem34("favorites_cards_rendered", media=mt, rows=0)
            return
        self._favorite_card_index=max(0,min(int(self._favorite_card_index),total-1))
        start=(self._favorite_card_index//7)*7;self._favorite_card_page=start
        visible=entries[start:start+7]
        self._schedule_favorite_artwork(visible,mt)
        for slot,entry in enumerate(visible):
            pos=start+slot;item=dict(entry.get("item") or {});selected=(pos==self._favorite_card_index);x=55+(256*slot)
            # Explicitly keep every decorative frame/meta surface hidden.
            for n in ("fav_card%d"%slot,"fav_sel%d"%slot,"fav_meta%d"%slot):
                try:self[n].hide()
                except Exception:pass
            if mt=="itv":
                art=self._favorite_live_icon(item);y=882
                try:self._browser_geom("fav_art%d"%slot,x,y,220,132);self["fav_art%d"%slot].instance.setPixmapFromFile(art);self["fav_art%d"%slot].show()
                except Exception:pass
                title=self._favorite_clean_title("itv",item,42)
                try:self._browser_geom("fav_title%d"%slot,x,y+142,220,46);self["fav_title%d"%slot].setText(title);self._favorite_set_title_color(slot,selected);self["fav_title%d"%slot].show()
                except Exception:pass
            else:
                art,poster,bundle,snap=self._favorite_poster_path(mt,item);y=704
                try:self._browser_geom("fav_art%d"%slot,x,y,206,310);self["fav_art%d"%slot].instance.setPixmapFromFile(art);self["fav_art%d"%slot].show()
                except Exception:pass
                title=self._favorite_clean_title(mt,item,44)
                try:self._browser_geom("fav_title%d"%slot,x+2,y+318,202,48);self["fav_title%d"%slot].setText(title);self._favorite_set_title_color(slot,selected);self["fav_title%d"%slot].show()
                except Exception:pass
        _mem34("favorites_cards_rendered", media=mt, rows=len(visible))

    def _favorite_move(self, delta):
        total=len(getattr(self,"_favorite_card_entries",[]) or [])
        if total<=0:return
        self._favorite_card_index=(self._favorite_card_index+int(delta))%total
        self._render_favorite_cards()

    def _open_favorite_card(self):
        entries=list(getattr(self,"_favorite_card_entries",[]) or [])
        if not entries:return
        entry=entries[max(0,min(self._favorite_card_index,len(entries)-1))]
        portal=str(self.profile.get("portal") or "").rstrip("/").lower();mac=str(self.profile.get("mac") or "").upper()
        if str(entry.get("portal") or "").rstrip("/").lower()!=portal or str(entry.get("mac") or "").upper()!=mac:
            return
        item=entry.get("item") or {};mt=str(entry.get("media_type") or "vod")
        if mt=="itv":
            old=self.media_type;self.media_type="itv";self.play_item(item);self.media_type=old
        else:
            self.session.openWithCallback(self._favorite_child_returned,ContentDetailsScreen,self.profile,self.client,mt,item)

    def _favorite_child_returned(self, result=None):
        if getattr(self,"level",None)!="favorites_cards":return
        mt=str(getattr(self,"_favorites_bucket_type",None) or "vod");keep=int(getattr(self,"_favorite_card_index",0) or 0)
        self._show_favorites_bucket(mt)
        if self.level=="favorites_cards":
            self._favorite_card_index=max(0,min(keep,max(0,len(self._favorite_card_entries)-1)));self._render_favorite_cards()

    def _show_saved_entries(self, entries, title):
        _perf29_saved_t0=time.monotonic()
        if str(title).lower()=="favorites":
            self._show_favorites_root();LOG.info("PERF29 browser favorites_ready elapsed_ms=%d entries=%d",int((time.monotonic()-_perf29_saved_t0)*1000),len(entries or []));return
        self._set_list_density("itv" if (self.media_type == "itv") else "compact")
        portal = str(self.profile.get("portal") or "").rstrip("/").lower()
        mac = str(self.profile.get("mac") or "").upper()
        matching = [x for x in entries if str(x.get("portal") or "").rstrip("/").lower() == portal and str(x.get("mac") or "").upper() == mac and isinstance(x.get("item"), dict)]
        if str(title).lower()=="favorites":
            self._favorite_card_entries=list(matching);self._favorite_card_index=0;self.level="favorites_cards"
            try:self["list"].hide()
            except Exception:pass
            for n in ("preview","accent_frame","info_card1","info_card2","info_card3","info_icon1","info_icon2","info_icon3","info_title","info"):
                try:self[n].hide()
                except Exception:pass
            self["section"].setText(_("Favorites"));self["status"].setText(_("LEFT / RIGHT browse  •  OK open / play"))
            self._render_favorite_cards();return
        self._hide_favorite_cards()
        self.content_items = []
        for entry in matching:
            saved_item = dict(entry["item"])
            saved_item["_saved_media_type"] = entry.get("media_type") or "vod"
            saved_item["_history_position"] = int(entry.get("_position") or 0)
            saved_item["_history_duration"] = int(entry.get("_duration") or 0)
            saved_item["_history_completed"] = bool(entry.get("_completed"))
            self.content_items.append(saved_item)
        self.all_content_items = list(self.content_items)
        self.media_type = matching[0].get("media_type") if matching else "vod"
        self.level = "items"
        rows = []
        for entry in matching:
            item = entry["item"]
            media_type = entry.get("media_type") or "vod"
            title_text = self._favorite_clean_title(media_type,item,120) if str(title).lower()=="favorites" else _clean_display_text(item.get("name") or item.get("title") or item.get("id") or _("Result"), 120)
            icon = "settings_icons_40/channel_list.png" if media_type == "itv" else ("settings_icons_40/artwork.png" if media_type == "vod" else "settings_icons_40/next_episode.png")
            position=int(entry.get("_position") or 0);duration=int(entry.get("_duration") or 0);completed=bool(entry.get("_completed"))
            meta=self._mode_name(media_type)
            if completed: meta += "  •  " + _("WATCHED")
            elif position and duration: meta += "  •  %d%%" % min(99,int(position*100.0/duration))
            elif position: meta += "  •  " + _("RESUME")
            rows.append((title_text, asset(icon), item, meta))
        if not rows:
            rows = [(_("No saved content"), asset("us89_folder_yellow_42.png"), None, {"meta":title,"row_asset":_neutral_utility_glass().get("utility_row"),"row_selected_asset":_neutral_utility_glass().get("utility_selected")})]
        self["list"].set_icon_rows(rows)
        self["section"].setText(title)
        self["counter"].setText(_("%d items") % len(matching))
        self["status"].setText(_("MENU: tools  •  OK: open / play"))
        self._selection_changed()

    def _show_skeleton(self, label="Loading premium content"):
        """Keep browser loading visually clean.

        us199: never render fake category/content rows, the Enigma2 default
        red selection bar or a temporary scrollbar while portal data is still
        arriving.  The premium page stays calm, then the real rows appear in
        one reveal when the request completes.
        """
        self["counter"].setText(_("Loading"))
        try:self["list"].hide()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        for name in ("accent_frame","preview","info_card1","info_card2","info_card3","info_icon1","info_icon2","info_icon3","info_title","info"):
            try:self[name].hide()
            except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _reveal_browser_content(self):
        """Reveal real Browser content after portal rows are ready.

        Categories are intentionally special: only their floating rail is
        revealed.  Re-showing the generic preview/info widgets here was the
        reason the large section icon survived on the right in Test12.
        """
        if self.level == "genres":
            try:self["list"].show()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
            self._set_category_clean_visibility(True)
            try:self["page_adaptive_bg"].show()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
            return
        for name in ("list","preview","info_title","info"):
            try:self[name].show()
            except Exception as exc:optional_failure("ui.silent_guard",exc)

    def open_search(self):
        if self.level != "items" or not self.content_items:
            self["status"].setText(_("Search is available inside content lists"))
            return
        recent = _load_recent_searches()
        prompt = _("Search content") + (("  •  " + (_("Recent: %s") % ", ".join(recent[:3]))) if recent else "")
        self.session.openWithCallback(self._search_done, InputBox, title=prompt, text="", maxSize=60)

    def _search_done(self, term):
        self._set_list_density("itv" if (self.media_type == "itv") else "compact")
        term = str(term or "").strip()
        if not term: return
        _save_recent_search(term)
        q = _search_key(term)
        rows=[]
        icon="settings_icons_40/channel_list.png" if self.media_type=="itv" else ("settings_icons_40/artwork.png" if self.media_type=="vod" else "settings_icons_40/next_episode.png")
        source = getattr(self, "all_content_items", self.content_items)
        indexed = getattr(self, "_content_search_index", None)
        if not indexed or len(indexed) != len(source):
            indexed = [(_search_key(e.get("name") or e.get("title") or ""), e) for e in source]
            self._content_search_index = indexed
        self._filtered_items=[e for key, e in indexed if q in key]
        self.content_items = list(self._filtered_items)
        for e in self._filtered_items:
            title=str(e.get("name") or e.get("title") or e.get("id") or _("Result"))
            sec=str(e.get("year") or e.get("number") or e.get("genre") or "")
            rows.append((title,asset(icon),e,sec))
        if not rows: rows=[(_("No search results"),asset(icon),None,term)]
        self["list"].set_icon_rows(rows)
        self["counter"].setText(_("%d results") % len(self._filtered_items))
        self["section"].setText(_("%s  /  Search: %s") % (self._mode_name(self.media_type), term[:30]))
        self["status"].setText(_("MENU: new search  •  BLUE: restore list"))

    def back(self):
        if getattr(self,"_category_menu_active",False):
            self._category_menu_back();return
        if self.level=="genres" and getattr(self,"_category_select_mode",False):
            self._cancel_category_selection()
            return
        # Category loading is asynchronous, but the clean Categories skin hides
        # the normal status label.  Historically BACK was blocked by _busy here,
        # which made a slow/dead portal request look like a frozen receiver.
        # BACK must always be a hard escape from the loading-only Categories
        # surface: cancel only this screen request and return immediately.
        if self._busy and self.level == "genres_loading":
            self._cancel_active_async()
            self.close() if self._home_entry else self._show_root()
            return
        # Catch-up is network-heavy on some portals. BACK is a hard UI escape:
        # cancel the request immediately and never force the viewer to wait for
        # a portal timeout or a multi-page scan to finish.
        if self._busy and str(self.level).startswith("catchup"):
            self._cancel_active_async()
            if self.level in ("catchup_programs_loading","catchup_programs"):
                self.level="catchup_channels"
                if getattr(self,"_catchup_channels_cache",None):
                    valid=list(self._catchup_channels_cache)
                    self.content_items=valid; self.all_content_items=list(valid)
                    menu=[(str(x.get("name") or x.get("title") or _("Channel")),asset("settings_icons_40/channel_list.png"),x,_('Archive')) for x in valid]
                    self["list"].set_icon_rows(menu);self["section"].setText(_("Catch-up TV  /  Channels"));self["counter"].setText(_("%d channels")%len(valid));self["status"].setText(_("Ready"));self._selection_changed();return
            self.close() if self._home_entry else self._show_root()
            return
        if self._busy: self["status"].setText(_("Please wait")); return
        if self.level in ("favorites_cards","favorites_live"):
            self._show_favorites_root();return
        if self.level=="favorites_root":
            self.close() if self._home_entry else self._show_root();return
        if self.level=="root": self.close()
        elif self.level=="catchup_programs": self.load_catchup_channels()
        elif self.level=="catchup_channels":
            self.close() if self._home_entry else self._show_root()
        elif self.level=="items":
            if self._home_entry and self.initial_action in ("favorites", "recent"):
                self.close()
            else:
                self.load_genres(self.media_type)
        else:
            self.close() if self._home_entry else self._show_root()

    def _show_root(self):
        if getattr(self,"_category_menu_active",False):self._category_menu_close()
        self.level="root"; self.media_type=None; self.genre=None; self.content_items=[]
        self._set_root_rows(); self["section"].setText(self.profile.get("portal","")); self["counter"].setText(_("PORTAL HOME  •  %d sections") % len(self.MODES))
        self["info_title"].setText(_("Choose a section")); self["info"].setText(_("Live, Movies, Series, Favorites and Continue Watching"))
        self._decode_picture(asset("us80_section_live_530x382.png")); self["status"].setText(_("Ready"))

    def reauthorize(self):
        if self._busy: return
        self["status"].setText(_("Reauthorizing..."))
        def work(): self.client.token=None; return self.client.authorize()
        self._run_async(work,lambda _result:self["status"].setText(_("Authorized")),lambda e:self["status"].setText(str(e)))

    def refresh_current(self):
        if self.level=="catchup_programs" and self.catchup_channel: self.load_catchup_programs(self.catchup_channel)
        elif self.level=="catchup_channels": self.load_catchup_channels()
        elif self.level=="genres": self.load_genres(self.media_type, force_refresh=True)
        elif self.level=="items": self.load_items(self.media_type,self.genre)
        else: self._show_root()

    def blue_action(self):
        if self.level=="genres" and self.media_type=="itv":
            return self.cache_all_live_picons()
        return self.select()

    def cache_all_live_picons(self):
        """Background Live picon cache with persistent failed-only retry.

        First run scans the provider and uses beta39 downloader for missing picons.
        If failures remain, the next blue-button press retries ONLY that persistent
        failed queue; it does not enumerate all Live categories again.
        """
        if self._live_picon_cache_running:
            self["status"].setText(_("Picon cache is already running"))
            return
        self._live_picon_cache_running=True
        profile=dict(self.profile or {})
        progress=self._live_picon_cache_progress

        # Fast path: a previous completed pass left failures. Retry those only.
        retry_rows=_load_live_picon_failed_queue(profile)
        retry_only=bool(retry_rows)
        if retry_only:
            self["blue"].setText("0 / %d"%len(retry_rows))
            self["status"].setText(_("Retrying %d failed picons only")%len(retry_rows))
        else:
            self["blue"].setText(_("Scanning..."))
            self["status"].setText(_("Scanning Live categories..."))

        genres=list(self.content_items or [])

        def work():
            isolated=None
            try:
                if retry_only:
                    rows=list(retry_rows)
                    total=len(rows)
                    _live_restart_trace("bulk_cache_retry_failed_start",total=total)
                else:
                    _live_restart_trace("bulk_cache_start",genres=len(genres))
                    isolated=_new_isolated_source_client(profile,timeout=6)
                    seen_urls=set();rows=[];skipped=0
                    total_genres=max(1,len(genres))
                    for gi,genre in enumerate(genres,1):
                        try:progress.put(("scan",gi,total_genres))
                        except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)
                        gid=str(genre.get("id") or genre.get("genre_id") or genre.get("name") or "*")
                        try:
                            batch=call_compatible(
                                isolated.ordered_all,
                                ((("itv",gid), {"start_page":1,"max_pages":500,"max_items":20000,"cancel_event":None}),
                                 (("itv",gid,1,500,20000,None), {})),
                            ) or []
                        except Exception:
                            batch=[]
                        for item in batch:
                            if not isinstance(item,dict):
                                skipped+=1;continue
                            raw=str(_image_url(item) or "")
                            if not raw:
                                skipped+=1;continue
                            try:
                                resolved=_normalize_provider_image_url(_source_art_url(raw,profile,item))
                                parts=urllib.parse.urlsplit(resolved or "")
                                if parts.scheme not in ("http","https") or not parts.hostname:
                                    skipped+=1;continue
                            except Exception:
                                skipped+=1;continue
                            if resolved in seen_urls:continue
                            seen_urls.add(resolved)
                            rows.append((item,resolved))
                    total=len(rows)

                if not total:
                    _save_live_picon_failed_queue(profile,[])
                    progress.put(("done",0,0,0,0,0))
                    _live_restart_trace("bulk_cache_done",done=0,total=0,cached=0,downloaded=0,failed=0)
                    return

                # TRUE RESUME remains for a normal/full pass. On failed-only retry
                # the visible total is intentionally only the failed queue.
                missing=[];cached=0
                for item,resolved in rows:
                    existing=_cached_live_picon_path(resolved,profile,item)
                    if existing:
                        cached+=1
                    else:
                        missing.append((item,resolved))

                done=cached;downloaded=0;failed=0
                try:progress.put(("cache",done,total,cached,downloaded,failed))
                except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)
                _live_restart_trace("bulk_cache_resume",done=done,total=total,cached=cached,missing=len(missing),retry_only=retry_only)

                if not missing:
                    _save_live_picon_failed_queue(profile,[])
                    progress.put(("done",done,total,cached,downloaded,failed))
                    _live_restart_trace("bulk_cache_done",done=done,total=total,cached=cached,downloaded=0,failed=0,retry_only=retry_only)
                    return

                # IMPORTANT: original beta39 downloader path remains unchanged.
                def one(pair):
                    item,resolved=pair
                    existing=_cached_live_picon_path(resolved,profile,item)
                    if existing:return ("cached",existing,pair)
                    path=_download_public_live_picon(resolved,profile,item=item,timeout=4.0)
                    return ("downloaded",path,pair) if path else ("failed",None,pair)

                failed_rows=[]
                pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix="ultra-picon-cache")
                futures=[pool.submit(one,pair) for pair in missing]
                try:
                    for future in as_completed(futures):
                        pair=None
                        try:
                            state,path,pair=future.result()
                        except Exception:
                            state="failed";path=None
                        if state=="cached":cached+=1
                        elif state=="downloaded":downloaded+=1
                        else:
                            failed+=1
                            if pair is not None: failed_rows.append(pair)
                        done+=1
                        try:progress.put(("cache",done,total,cached,downloaded,failed))
                        except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)
                        if done%100==0 or done==total:
                            _live_restart_trace("bulk_cache_progress",done=done,total=total,cached=cached,downloaded=downloaded,failed=failed,retry_only=retry_only)
                finally:
                    try:pool.shutdown(wait=True,cancel_futures=True)
                    except TypeError:pool.shutdown(wait=True)

                # Persist ONLY unresolved failures. Next press starts here directly.
                _save_live_picon_failed_queue(profile,failed_rows)
                progress.put(("done",done,total,cached,downloaded,failed))
                _live_restart_trace("bulk_cache_done",done=done,total=total,cached=cached,downloaded=downloaded,failed=failed,retry_only=retry_only)
            except Exception as exc:
                _live_restart_trace("bulk_cache_error",error=repr(exc),retry_only=retry_only)
                try:progress.put(("error",_friendly_error(exc)))
                except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)
            finally:
                if isolated is not None:
                    try:isolated.close()
                    except Exception as exc: diagnostic_failure("ui.browser.failsoft", exc)

        try:
            self._live_picon_cache_future=_PICON_CACHE_EXECUTOR.submit(work)
        except Exception as exc:
            self._live_picon_cache_running=False
            self["blue"].setText(_("Cache Picons"))
            self["status"].setText(_friendly_error(exc))

    def select(self):
        if getattr(self,"_category_menu_active",False):return self._category_menu_accept()
        if self._busy: return
        if self.level=="genres" and getattr(self,"_category_select_mode",False):
            return self._toggle_category_selection()
        if self.level=="favorites_root":
            return self._show_favorites_bucket(("itv","vod","series")[max(0,min(2,int(getattr(self,"_favorite_root_index",0) or 0)))])
        if self.level=="favorites_cards":return self._open_favorite_card()
        if self.level=="favorites_live":
            idx=self["list"].getSelectedIndex()
            if 0<=idx<len(self.content_items):
                item=self.content_items[idx]
                old=self.media_type;self.media_type="itv";self.play_item(item);self.media_type=old
            return
        idx=self["list"].getSelectedIndex()
        if self.level=="root" and 0 <= idx < len(self.MODES):
            action = self.MODES[idx][1]
            if action in ("itv", "vod", "series"):
                self._open_media_with_parental(action)
            elif action == "catchup":
                self.load_catchup_channels()
            elif action == "favorites":
                self._show_saved_entries(load_favorites(), "Favorites")
            elif action == "recent":
                self._show_saved_entries(load_continue_watching(), "Continue Watching")
            elif action == "account":
                self.show_account_info()
            elif action == "search":
                self.open_global_search()
            elif action == "settings":
                self.open_playback_settings()
        elif self.level=="genres" and 0 <= idx < len(self.content_items):
            self._open_genre_with_parental(self.content_items[idx], idx)
        elif self.level=="catchup_channels" and 0 <= idx < len(self.content_items):
            self.load_catchup_programs(self.content_items[idx])
        elif self.level=="catchup_programs" and 0 <= idx < len(self.content_items):
            self.play_catchup_program(self.content_items[idx])
        elif self.level=="items" and 0 <= idx < len(self.content_items):
            item=self.content_items[idx]
            selected_type = item.get("_saved_media_type") or self.media_type
            if selected_type=="itv":
                previous_type = self.media_type; self.media_type = selected_type
                self.play_item(item); self.media_type = previous_type
            else: self.session.openWithCallback(lambda result=None:self._restore_current_list_selection(),ContentDetailsScreen,self.profile,self.client,selected_type,item)

    def _restore_current_list_selection(self):
        """Keep the exact list row selected after returning from details."""
        try:self._selection_changed()
        except Exception as exc:optional_failure("ui",exc)

    def _grid_returned(self, result=None):
        """Keep the selected category after leaving its poster/live grid."""
        try:self._selection_changed()
        except Exception as exc:optional_failure("ui",exc)
        # Initial grid loads that exhaust their bounded self-heal return here
        # instead of stranding the viewer on an empty child screen.
        try:
            if isinstance(result,(tuple,list)) and len(result)>=2 and result[0]=="content_load_error":
                self["status"].setText(str(result[1] or _("Content temporarily unavailable. Try again.")))
        except Exception as exc:optional_failure("ui.grid_return_error_status",exc)

    def load_genres(self, media_type, force_refresh=False):
        if self._busy:return
        _perf29_genres_t0=time.monotonic()
        LOG.info("PERF29 genres begin media=%s force=%s",str(media_type),bool(force_refresh))
        # Preserve category focus across manual/background catalogue rebuilds.
        # A stale-cache refresh used to repaint the list and force index 0, so a
        # viewer navigating the middle of Categories could suddenly jump to the
        # first folder when the silent provider refresh completed.
        restore_category_id = ""
        restore_category_index = 0
        try:
            if self.level == "genres" and self.media_type == media_type and self.content_items:
                restore_category_index = max(0, int(self["list"].getSelectedIndex() or 0))
                if restore_category_index < len(self.content_items):
                    restore_category_id = _visibility_category_id(self.content_items[restore_category_index])
        except Exception as exc:
            optional_failure("ui.category_focus_snapshot", exc)
        # Enter the final Categories surface before any network/cache wait.
        self.media_type = media_type
        self.level = "genres_loading"
        self._apply_category_portal_geometry()
        if self._category_dynamic_visuals_enabled():
            self._load_category_backdrop()
        else:
            # Movies/Series Categories use the same static page immediately; no Hero/Adaptive
            # work is launched while category cache/network is in flight.
            self._category_chrome = self._movies_static_category_chrome()
            self._restore_category_side_identity()
        self._set_list_density("genres")
        cached = None if force_refresh else _category_cache_get(self.profile, media_type)
        LOG.info("PERF29 genres cache media=%s hit=%s lookup_elapsed_ms=%d",str(media_type),bool(cached is not None),int((time.monotonic()-_perf29_genres_t0)*1000))
        # Hot/last-good cache entry needs no portal transport mutation at all.
        # Touching client backoff/socket state on every cached category open was
        # wasted work and could contend with Home's background prefetch.
        if cached is None or force_refresh:
            try:self.client.reset_failure_backoff()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        # Hot/last-good Categories are already usable truth.  Do not hide the
        # existing list for a cache hit and manufacture a blank transition frame.
        if cached is None:
            try:self["list"].hide()
            except Exception as exc:optional_failure("ui.category_loading_list_hide", exc)
            self["status"].setText(_("Loading categories..."))
            self._show_skeleton("Loading categories")
        def ok(genres,persist=True):
            # Snapshot the *current* focus too. This matters for the second,
            # silent in-place refresh after cached Categories are already usable.
            # It overrides the entry snapshot if the viewer has moved meanwhile.
            focus_id = restore_category_id
            focus_index = restore_category_index
            try:
                if self.level == "genres" and self.media_type == media_type and self.content_items:
                    focus_index = max(0, int(self["list"].getSelectedIndex() or 0))
                    if focus_index < len(self.content_items):
                        focus_id = _visibility_category_id(self.content_items[focus_index])
            except Exception as exc:
                optional_failure("ui.category_focus_refresh_snapshot", exc)
            if persist and isinstance(genres, list):
                _category_cache_put(self.profile, media_type, genres)
            provider_rows=[e for e in genres if isinstance(e,dict)] if isinstance(genres, list) else []
            cfg=load_settings(); hidden=_profile_hidden_ids(cfg,self.profile,media_type); protected=set(cfg.get("protected_categories",{}).get(media_type,[])); words=cfg.get("adult_keywords",[])
            def gid(e): return _visibility_category_id(e)
            def adult(e):
                text=str(e.get("name") or e.get("title") or "").casefold();return any(w in text for w in words)
            empty=set(cfg.get("empty_categories",{}).get(media_type,[]))
            manageable=[]
            for e in provider_rows:
                sensitive=(gid(e) in protected or adult(e))
                if cfg.get("parental_lock") and cfg.get("parental_mode","pin")=="hide" and sensitive: continue
                if (not cfg.get("parental_lock")) and cfg.get("hide_adult",True) and adult(e): continue
                if cfg.get("hide_empty_categories",True) and gid(e) in empty: continue
                manageable.append(e)
            self._category_known_items=list(manageable)
            valid=[e for e in manageable if gid(e) not in hidden]
            if not valid and not manageable:
                if media_type == "itv":
                    screen = PremiumLiveGridScreen
                else:
                    view_key="movies_view_mode" if media_type=="vod" else "series_view_mode"
                    view_mode=str(cfg.get(view_key) or "cinematic").lower()
                    screen=PremiumGlobalCinematicScreen if view_mode=="cinematic" else (PremiumBackdropGrid2Screen if view_mode=="backdrop2" else (PremiumBackdropGridScreen if view_mode=="backdrop" else (PremiumPosterGridScreen if view_mode=="poster_legacy" else PremiumPosterGridV2Screen)))
                self.session.openWithCallback(self._grid_returned, screen, self.profile, self.client, media_type, "*", self._mode_name(media_type))
                return
            if not valid and manageable:
                self.media_type=media_type; self.content_items=[]; self.level="genres"
                self._category_chrome = {}
                self._apply_category_portal_geometry(); self._load_category_chrome()
                self["section"].setText(_("%s  /  Categories") % self._mode_name(media_type))
                self["list"].set_icon_rows([]); self._reveal_browser_content()
                self["counter"].setText(_("%d categories") % 0); self["status"].setText(_("All categories are hidden • MENU to restore")); self._apply_browser_status_color()
                return
            pinned=set(cfg.get("pinned_categories",{}).get(media_type,[]))
            # Preserve the exact order returned by the portal. Pinned categories
            # keep their visual star but no longer jump ahead of portal order.
            self.media_type=media_type; self.content_items=valid; self.level="genres"
            # One measurement across the complete category list.  Paging through
            # groups of 14 must never change the row width.
            self._prepare_category_global_geometry(valid,cfg)
            # Never reuse adaptive assets from the previously opened media family.
            # Live/Movies/Series use the same static page and must not rebuild page mood.
            self._category_chrome = {}
            self._apply_category_portal_geometry()
            self._load_category_chrome()
            self["section"].setText(_("%s  /  Categories") % self._mode_name(media_type))
            # Restore by stable category ID first. If that folder vanished (for
            # example because it was hidden or removed by the provider), keep the
            # nearest valid row instead of throwing focus back to the top.
            target_index = 0
            if valid:
                target_index = max(0, min(int(focus_index or 0), len(valid) - 1))
                if focus_id:
                    for _i, _entry in enumerate(valid):
                        if gid(_entry) == focus_id:
                            target_index = _i
                            break
            try:self["list"].moveToIndex(target_index)
            except Exception as exc:optional_failure("ui.category_focus_restore",exc)
            self._render_genre_rows()
            self._reveal_browser_content()
            self["blue"].setText(_("Cache Picons") if media_type=="itv" else _("Open / Play"))
            self["counter"].setText(_("%d categories") % len(valid)); self["status"].setText(_("Ready")); self._apply_browser_status_color(); self._selection_changed()
            LOG.info("PERF29 genres first_paint media=%s categories=%d elapsed_ms=%d",str(media_type),len(valid),int((time.monotonic()-_perf29_genres_t0)*1000))
        # Last-good categories are first-paint truth.  A stale HDD/RAM row is
        # shown immediately, then refreshed silently in-place; a transient
        # Stalker failure can therefore never replace useful categories with a
        # blank backdrop.  Fresh hot-cache rows need no network request.
        cached_painted = cached is not None
        if cached_painted:
            ok(cached,persist=False)
            # Never mark the screen busy after last-good first paint. Refresh a
            # stale catalogue on an isolated client so OK/navigation remains
            # instant and the shared Portal transport is not disturbed.
            try:
                stale=_category_cache_age(self.profile,media_type) > _CATEGORY_CACHE_TTL
            except Exception:
                stale=False
            if stale:
                profile_copy=dict(self.profile or {});media_copy=str(media_type or "")
                def apply_silent_category_refresh(rows):
                    if getattr(self,"_screen_closed",False):return
                    if self.level=="genres" and self.media_type==media_copy and isinstance(rows,list) and rows:
                        ok(rows,persist=False)
                def refresh_last_good():
                    isolated=None
                    try:
                        isolated=_new_isolated_source_client(profile_copy,timeout=6)
                        rows=isolated.genres(media_copy)
                        if isinstance(rows,list) and rows:
                            _category_cache_put(profile_copy,media_copy,rows)
                            try:self._jobs.put((apply_silent_category_refresh,rows,False))
                            except Exception as queue_exc:optional_failure("ui.category_refresh_delivery",queue_exc)
                    except Exception as exc:
                        optional_failure("ui.category_last_good_refresh",exc)
                    finally:
                        try:
                            close_pending=getattr(isolated,"cancel_pending_requests",None)
                            if callable(close_pending):close_pending()
                        except Exception:pass
                try:_CATEGORY_PREFETCH_EXECUTOR.submit(refresh_last_good)
                except Exception as exc:optional_failure("ui.category_last_good_refresh_submit",exc)
            return

        def load_categories_resilient(handle):
            _perf29_net_t0=time.monotonic()
            """Recover transient category failures inside the same screen.

            Several MAG/Stalker portals intermittently return HTML/non-JSON or
            drop the first keep-alive request even though the very next manual
            re-entry succeeds.  Requiring the viewer to BACK/enter three or four
            times is pointless.  Keep recovery local to Categories: retry on a
            fresh transport, refresh authorization once, and only expose the
            error screen after all bounded attempts are exhausted.
            """
            cancel_event = getattr(handle, "cancel_event", None)
            last_error = None
            # First try is immediate. Subsequent waits are deliberately short so
            # a healthy portal still feels instant while flaky loaders self-heal.
            retry_delays = (0.0, 0.22, 0.55, 0.90)
            for attempt, delay in enumerate(retry_delays):
                if attempt:
                    if cancel_event is not None and getattr(cancel_event, "wait", lambda _x: False)(delay):
                        raise last_error or Exception("Request cancelled")
                    # A stale keep-alive socket is a common source of one-shot
                    # non-JSON/empty loader responses.  Drop only client transport
                    # state; do not alter portal/profile data or UI state.
                    try:
                        close_pending = getattr(self.client, "cancel_pending_requests", None)
                        if callable(close_pending):
                            close_pending()
                    except Exception as exc:
                        optional_failure("ui.category_retry_transport", exc)
                    try:
                        reset_backoff = getattr(self.client, "reset_failure_backoff", None)
                        if callable(reset_backoff):
                            reset_backoff()
                    except Exception as exc:
                        optional_failure("ui.category_retry_backoff", exc)
                    # Third pass mirrors what users were effectively doing by
                    # leaving/re-entering: establish one fresh authorized portal
                    # session.  M3U/Xtream adapters simply skip this branch.
                    if attempt == 2:
                        try:
                            authorize = getattr(self.client, "authorize", None)
                            if callable(authorize):
                                authorize(cancel_event=cancel_event)
                        except TypeError:
                            try: authorize()
                            except Exception as exc: last_error = exc
                        except Exception as exc:
                            last_error = exc
                try:
                    _rows=self.client.genres(media_type, cancel_event=cancel_event)
                    LOG.info("PERF29 genres provider_done media=%s attempt=%d elapsed_ms=%d rows=%d",str(media_type),int(attempt+1),int((time.monotonic()-_perf29_net_t0)*1000),len(_rows) if isinstance(_rows,list) else -1)
                    return _rows
                except Exception as exc:
                    last_error = exc
                    low = str(exc).casefold()
                    # Security-consent failures need explicit user approval and
                    # must never be hidden behind automatic retries.
                    if any(marker in low for marker in (
                        "security consent", "fallback is not approved",
                        "certificate verification", "unapproved https"
                    )):
                        raise
                    if attempt >= len(retry_delays) - 1:
                        raise
            raise last_error or Exception("Categories could not be loaded")

        def failed(exc):
            # If a last-good catalogue is already on-screen, this was only a
            # silent refresh. Keep the useful rows and suppress transient portal
            # noise exactly like the M3U path.
            if cached_painted and self.level=="genres" and self.content_items:
                try:self["status"].setText("")
                except Exception:pass
                return
            # Never leave the viewer on a backdrop-only loading surface. Restore
            # normal Browser chrome and show the actual portal error immediately.
            self.level="root"
            self.content_items=[]
            try:self._restore_browser_geometry()
            except Exception as restore_exc:optional_failure("ui.category_error_restore",restore_exc)
            try:
                self["section"].setText(_("Categories unavailable"))
                self["status"].setText(_friendly_error(exc))
                self["counter"].setText("")
                self["list"].set_icon_rows([(_("Categories could not be loaded"),asset("us89_folder_yellow_42.png"),None,_friendly_error(exc))])
                self["list"].show()
            except Exception as render_exc:optional_failure("ui.category_error_render",render_exc)
        self._run_async(load_categories_resilient,ok,failed)

    def load_items(self, media_type, genre, page=1):
        if self._busy:return
        self._set_list_density("itv" if media_type == "itv" else "compact")
        self["status"].setText(_("Loading content..."))
        self._show_skeleton("Loading content")
        def ok(data):
            valid=[e for e in data if isinstance(e,dict)] if isinstance(data, list) else []
            self.media_type=media_type; self.genre=genre; self.page=max(1, int(page)); self.content_items=valid; self.all_content_items=list(valid); self.level="items"
            if not valid and genre not in (None,"","*"):
                cfg=load_settings(); empty=cfg.get("empty_categories",{}); vals=list(empty.get(media_type,[]))
                if str(genre) not in vals: vals.append(str(genre)); empty[media_type]=vals; save_settings({"empty_categories":empty})
            self._render_content_rows()
            self._reveal_browser_content()
            self["section"].setText(_("%s  /  Content") % self._mode_name(media_type))
            self["counter"].setText(_("Page %d  •  %d items") % (self.page, len(valid))); self["status"].setText(_("Ready") if valid else _("No content"))
            self._selection_changed()
        def load_items_resilient(handle):
            cancel_event=getattr(handle,"cancel_event",None);last_error=None
            delays=(0.0,0.18,0.42,0.75)
            for attempt,delay in enumerate(delays):
                if attempt:
                    if cancel_event is not None and getattr(cancel_event,"wait",lambda _x:False)(delay):
                        raise last_error or Exception("Request cancelled")
                    try:
                        close_pending=getattr(self.client,"cancel_pending_requests",None)
                        if callable(close_pending):close_pending()
                    except Exception as exc:optional_failure("ui.content_retry_transport",exc)
                    try:
                        reset=getattr(self.client,"reset_failure_backoff",None)
                        if callable(reset):reset()
                    except Exception as exc:optional_failure("ui.content_retry_backoff",exc)
                try:
                    return self.client.ordered_list(media_type,genre,page,cancel_event=cancel_event)
                except Exception as exc:
                    last_error=exc;low=str(exc).casefold()
                    if any(marker in low for marker in ("security consent","fallback is not approved","certificate verification","unapproved https")):
                        raise
                    transient=any(marker in low for marker in ("connection","timed out","timeout","non-json","invalid page","temporarily paused","remote end closed","connection reset","broken pipe","http 502","http 503","http 504"))
                    if (not transient) or attempt>=len(delays)-1:raise
            raise last_error or Exception("Content could not be loaded")
        def failed_items(error):
            low=str(error).casefold()
            transient=any(marker in low for marker in ("connection","timed out","timeout","non-json","invalid page","temporarily paused","remote end closed","connection reset","broken pipe","http 502","http 503","http 504"))
            message=_("Content temporarily unavailable. Try again.") if transient else _friendly_error(error)
            # A failed portal request must never leave the Browser on its hidden
            # loading skeleton.  If this was a refresh of an already-painted
            # list, keep that last-good content.  Otherwise reveal one honest
            # error row so the viewer always has a visible, escapable surface.
            if self.level=="items" and getattr(self,"content_items",None):
                try:self._reveal_browser_content()
                except Exception as exc:optional_failure("ui.content_error_reveal_last_good",exc)
                try:self["status"].setText(message)
                except Exception as exc:optional_failure("ui.content_error_status_last_good",exc)
                return
            self.media_type=media_type;self.genre=genre;self.page=max(1,int(page));self.level="items";self.content_items=[];self.all_content_items=[]
            try:
                icon="settings_icons_40/channel_list.png" if media_type=="itv" else ("settings_icons_40/artwork.png" if media_type=="vod" else "settings_icons_40/next_episode.png")
                self["list"].set_icon_rows([(_("Content could not be loaded"),asset(icon),None,message)])
                self["section"].setText(_("%s  /  Content") % self._mode_name(media_type))
                self["counter"].setText("")
                self["status"].setText(message)
                self._reveal_browser_content()
            except Exception as exc:optional_failure("ui.content_error_render",exc)
        self._run_async(load_items_resilient,ok,failed_items)

    def _live_content_row(self, e, pos, cfg, state=None):
        state=state or {}
        _clean_titles=bool(cfg.get("clean_titles",True))
        raw=_provider_display_raw(e,_clean_titles,e.get("id") or "Channel")
        title=_clean_live_channel_name(raw,True) if _clean_titles else str(raw).strip()
        cid=str(e.get("id") or e.get("ch_id") or e.get("number") or pos)
        epg=self._epg_cache.get(cid,{})
        number=str(e.get("number") or e.get("num") or pos) if cfg.get("show_channel_numbers",True) else ""
        badges=[]
        if state.get("favorite"): badges.append("★")
        if e.get("allow_archive") or e.get("tv_archive_duration") or e.get("archive"): badges.append(_("CATCH-UP"))
        if epg.get("percent"): badges.append("%d%%"%epg["percent"])
        icon=asset(_letter_placeholder(title))
        try:
            raw_picon=_image_url(e)
            cached=_cached_live_picon_path(raw_picon,self.profile,e) if raw_picon else ""
            if cached:
                fitted=_fit_live_picon_canvas(cached,PERSISTENT_GENERATED_DIR,(60,60))
                if fitted: icon=fitted
        except Exception as exc:
            optional_failure("ui.live_row_picon_fit",exc)
        details={"number":number,"now":epg.get("now") or e.get("epg_title") or e.get("now") or _("No EPG information"),"next":epg.get("next") or "","badges":"  ".join(badges)}
        return (title,icon,e,details)

    def _render_content_rows(self, keep_index=None):
        cfg=load_settings(); rows=[]
        states=load_content_states(self.profile,self.media_type,self.content_items) if self.content_items else []
        self._content_state_cache={id(item):state for item,state in zip(self.content_items,states)}
        if self.media_type=="itv":
            self._set_list_density("itv")
            for pos,e in enumerate(self.content_items,1):
                rows.append(self._live_content_row(e,pos,cfg,self._content_state_cache.get(id(e))))
        else:
            self._set_list_density("compact")
            icon="settings_icons_40/artwork.png" if self.media_type=="vod" else "settings_icons_40/next_episode.png"
            for e in self.content_items:
                _clean_titles=bool(cfg.get("clean_titles",True))
                raw=_provider_display_raw(e,_clean_titles,e.get("id") or _("Result"))
                title=premium_title(raw,True) if _clean_titles else str(raw).strip(); meta=[]
                if cfg.get("show_quality_badges",True):
                    badges=quality_badges(raw)
                    if badges: meta.append(badges)
                if e.get("year"): meta.append(str(e.get("year"))[:10])
                state=self._content_state_cache.get(id(e),{})
                if state.get("favorite"): meta.append("★")
                if state.get("completed"): meta.append("WATCHED")
                elif state.get("duration") and state.get("position"):
                    meta.append("%d%%"%min(99,int(state["position"]*100.0/state["duration"])))
                rows.append((title,asset(icon),e,"  •  ".join(meta)[:36]))
        if not rows:
            icon="settings_icons_40/channel_list.png" if self.media_type=="itv" else ("settings_icons_40/artwork.png" if self.media_type=="vod" else "settings_icons_40/next_episode.png")
            rows=[(_("No content returned"),asset(icon),None,_("Try another category"))]
        self["list"].set_icon_rows(rows)
        if keep_index is not None:
            try:self["list"].moveToIndex(max(0,min(int(keep_index),len(rows)-1)))
            except Exception as exc:optional_failure("ui",exc)

    def _refresh_live_epg_row(self, index):
        if self.media_type!="itv" or not (0<=index<len(self.content_items)):return
        item=self.content_items[index];cfg=load_settings();state=getattr(self,"_content_state_cache",{}).get(id(item),{})
        self["list"].update_icon_row(index,self._live_content_row(item,index+1,cfg,state))

    def _schedule_epg_preview(self, item):
        if self.media_type!="itv" or self.level!="items" or not isinstance(item,dict): return
        cid=str(item.get("id") or item.get("ch_id") or "")
        if not cid or cid in self._epg_cache: return
        self._epg_token+=1; self._epg_pending=(self._epg_token,cid)
        # A focus change makes a queued EPG request obsolete. Running network
        # work is allowed to finish, but it no longer owns this Screen.
        future=getattr(self,"_epg_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception as exc:optional_failure("ui.browser_epg_requeue_cancel",exc)
        self._epg_future=None
        try:self._epg_timer.stop();self._epg_timer.start(350,True)
        except Exception as exc:optional_failure("ui",exc)

    def _fetch_selected_epg(self):
        pending=getattr(self,"_epg_pending",None)
        if not pending:return
        token,cid=pending
        client=self.client
        jobs=self._epg_jobs
        screen_ref=weakref.ref(self)
        def work():
            try:return (token,cid,epg_summary(client.epg(cid,4)),None)
            except Exception as exc:return (token,cid,None,exc)
        def done(fut,ref=screen_ref,result_queue=jobs):
            screen=ref()
            if screen is None or getattr(screen,"_screen_closed",False):return
            try:result_queue.put(fut.result())
            except Exception as exc:optional_failure("ui",exc)
            finally:
                if screen is not None and getattr(screen,"_epg_future",None) is fut:
                    screen._epg_future=None
        try:
            future=_GRID_EPG_EXECUTOR.submit(work)
            self._epg_future=future
            future.add_done_callback(done)
        except Exception as exc:
            self._epg_future=None
            optional_failure("ui.browser_epg_submit",exc)

    def _drain_epg_jobs(self):
        while True:
            try:token,cid,summary,error=self._epg_jobs.get_nowait()
            except queue.Empty:break
            if error is None and summary:
                self._epg_cache[cid]=summary
                try:
                    idx=self["list"].getSelectedIndex(); self._refresh_live_epg_row(idx)
                    if 0<=idx<len(self.content_items):
                        item=self.content_items[idx]; current=str(item.get("id") or item.get("ch_id") or "")
                        if current==cid:
                            now=summary.get("now") or _("No programme description")
                            nxt=summary.get("next") or _("No programme description")
                            self["info"].setText(((_("NOW  %s") % now) + "\n" + (_("NEXT") + "  " + str(nxt)))[:118])
                except Exception as exc:optional_failure("ui",exc)

    def _stop_epg_preview(self):
        future=getattr(self,"_epg_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception as exc:optional_failure("ui.browser_epg_stop_cancel",exc)
        self._epg_future=None
        try:self._epg_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._epg_timer_conn is not None:self._epg_timer_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._fetch_selected_epg in self._epg_timer.callback:self._epg_timer.callback.remove(self._fetch_selected_epg)
        except Exception as exc:optional_failure("ui.epg_callback",exc)

    def open_global_search(self):
        self.session.open(PortalGlobalSearchScreen,self.profile,self.client)

    def toggle_current_category_pinned(self):
        if self.level!="genres" or not self.media_type:
            self["status"].setText(_("Open a category list first"));return
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):return
        item=self.content_items[idx]; gid=str(item.get("id") or item.get("genre_id") or item.get("name") or item.get("title") or "")
        cfg=load_settings(); pins=cfg.get("pinned_categories",{}); values=list(pins.get(self.media_type,[]))
        if gid in values: values.remove(gid); state=_("unpinned")
        else: values.append(gid); state=_("pinned")
        pins[self.media_type]=values; save_settings({"pinned_categories":pins}); self["status"].setText(_("Category %s")%state); self.load_genres(self.media_type)

    def toggle_current_category_protected(self):
        if self.level!="genres" or not self.media_type:self["status"].setText(_("Open a category list first"));return
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):return
        item=self.content_items[idx];gid=str(item.get("id") or item.get("genre_id") or item.get("name") or item.get("title") or "")
        cfg=load_settings();protected=cfg.get("protected_categories",{});values=list(protected.get(self.media_type,[]))
        if gid in values:values.remove(gid);state=_("unprotected")
        else:values.append(gid);state=_("protected")
        protected[self.media_type]=values;save_settings({"protected_categories":protected});self["status"].setText(_("Category %s")%state);self.load_genres(self.media_type)

    def _open_media_with_parental(self, media_type):
        # Category-level protection replaces the old all-or-nothing VOD lock.
        # A cancelled Live preview/page request must never poison the next
        # Movies/Series/Live navigation action.
        try:self.client.reset_failure_backoff()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self.load_genres(media_type)

    def _category_sensitive(self, media_type, item):
        cfg=load_settings();gid=str(item.get("id") or item.get("genre_id") or item.get("name") or item.get("title") or "")
        if gid in set(cfg.get("protected_categories",{}).get(media_type,[])):return True
        text=str(item.get("name") or item.get("title") or "").casefold()
        return any(word in text for word in cfg.get("adult_keywords",[]))

    def _open_genre_with_parental(self, item, index=0):
        cfg=load_settings()
        if cfg.get("parental_lock") and cfg.get("parental_mode","pin")=="pin" and self._category_sensitive(self.media_type,item) and not parental_is_unlocked():
            self._pending_genre=(dict(item),int(index),self.media_type)
            self.session.openWithCallback(self._parental_category_pin_done,InputBox,title=_("Parental PIN"),text="",maxSize=8,type=Input.PIN);return
        self._open_genre_direct(item,index,self.media_type)

    def _open_genre_direct(self,g,index,media_type):
        genre_id=str(g.get("id") or g.get("genre_id") or g.get("category_id") or g.get("name") or g.get("title") or "*")
        category_title=_clean_display_text(g.get("title") or g.get("name") or g.get("category_name") or g.get("genre_name") or _("Result"),100) or _("Result")
        if media_type=="itv":
            screen=PremiumLiveGridScreen
            live_categories=[dict(x) for x in (getattr(self,"content_items",[]) or []) if isinstance(x,dict)] if getattr(self,"level","")=="genres" else []
            # Preserve the Browser's parental contract inside the in-player
            # category drawer. A PIN-protected category is not exposed there
            # until the existing parental session has already been unlocked.
            try:
                _pcfg=load_settings()
                if _pcfg.get("parental_lock") and _pcfg.get("parental_mode","pin")=="pin" and not parental_is_unlocked():
                    live_categories=[x for x in live_categories if not self._category_sensitive("itv",x)]
            except Exception as exc:
                optional_failure("ui.live_player_category_parental_filter",exc)
            self.session.openWithCallback(self._grid_returned,screen,self.profile,self.client,media_type,genre_id,category_title,live_categories)
            return
        else:
            cfg=load_settings();view_key="movies_view_mode" if media_type=="vod" else "series_view_mode"
            view_mode=str(cfg.get(view_key) or "cinematic").lower()
            screen=PremiumGlobalCinematicScreen if view_mode=="cinematic" else (PremiumBackdropGrid2Screen if view_mode=="backdrop2" else (PremiumBackdropGridScreen if view_mode=="backdrop" else (PremiumPosterGridScreen if view_mode=="poster_legacy" else PremiumPosterGridV2Screen)))
        self.session.openWithCallback(self._grid_returned,screen,self.profile,self.client,media_type,genre_id,category_title)

    def _parental_category_pin_done(self,value):
        if value is None:
            return
        cfg=load_settings();ok,message=parental_verify_pin(value,cfg)
        if ok:
            pending=getattr(self,"_pending_genre",None)
            if pending:self._open_genre_direct(pending[0],pending[1],pending[2])
            self._pending_genre=None
        else:
            self.session.open(MessageBox,message or _("Incorrect parental PIN"),MessageBox.TYPE_ERROR,timeout=6)

    def _save_parental_pin(self, value):
        value=str(value or "")
        if value.isdigit() and 4 <= len(value) <= 8:
            save_settings(parental_hash_pin(value))
            parental_lock_now()
            self["status"].setText(_("Parental PIN saved securely"))
            self.refresh_current()
        elif value:
            self.session.open(SettingsGlassNoticeScreen,_("Parental PIN"),_("PIN must contain 4-8 digits"),False)

    def _save_parental_pin_and_enable(self, value):
        value=str(value or "")
        if not value:
            self["status"].setText(_("Parental lock remains disabled"))
            self.refresh_current()
            return
        if not (value.isdigit() and 4 <= len(value) <= 8):
            self.session.open(SettingsGlassNoticeScreen,_("Parental PIN"),_("PIN must contain 4-8 digits. Parental Lock was not enabled."),False)
            self.refresh_current()
            return
        payload=parental_hash_pin(value)
        payload["parental_lock"]=True
        save_settings(payload)
        parental_lock_now()
        self["status"].setText(_("Parental PIN saved • Parental lock enabled"))
        self.refresh_current()

    def open_section_settings(self):
        cfg=load_settings(); rows=[]
        for label,key in ((_("Live TV"),"show_live"),(_("Movies"),"show_movies"),(_("Series"),"show_series"),(_("Catch-up TV"),"show_catchup")):
            rows.append(("%s  •  %s"%(label,_("ON") if cfg.get(key,True) else _("OFF")),key))
        self.session.openWithCallback(self._toggle_section,ChoiceBox,title=_("Visible home sections"),list=rows)

    def _toggle_section(self, choice):
        if choice:
            key=choice[1]; save_settings({key:not bool(load_settings().get(key,True))}); self._show_root(); self["status"].setText(_("Section visibility updated"))

    def _save_hidden_category_ids(self, values):
        cfg=load_settings();payload=_profile_hidden_update(cfg,self.profile,self.media_type,set(values or []));save_settings(payload);self._selection_cfg_mono=0.0

    def _start_category_selection(self,reset=True):
        if self.level!="genres" or self.media_type not in ("itv","vod","series"):
            self["status"].setText(_("Open a category list first"));return
        if not self.content_items:
            self["status"].setText(_("No visible categories to select • MENU can restore hidden categories"));return
        self._category_select_mode=True
        if reset:self._category_selected_ids=set()
        self._render_genre_rows()
        self["status"].setText(_("Selection mode • OK selects and moves down • MENU applies"))

    def _toggle_category_selection(self):
        if not self._category_select_mode or self.level!="genres":return
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):return
        gid=_visibility_category_id(self.content_items[idx])
        if not gid:return
        if gid in self._category_selected_ids:self._category_selected_ids.remove(gid)
        else:self._category_selected_ids.add(gid)
        self._render_genre_rows()
        if idx+1<len(self.content_items):
            try:self["list"].moveToIndex(idx+1)
            except Exception as exc:optional_failure("ui.category_select_next",exc)
        self["status"].setText(_("%d categories selected • MENU to apply")%len(self._category_selected_ids))

    def _select_all_visible_categories(self):
        if not self._category_select_mode:return
        self._category_selected_ids=set(_visibility_category_id(e) for e in self.content_items if _visibility_category_id(e));self._render_genre_rows()
        self["status"].setText(_("%d categories selected • MENU to apply")%len(self._category_selected_ids))

    def _clear_category_selection(self):
        if not self._category_select_mode:return
        self._category_selected_ids=set();self._render_genre_rows();self["status"].setText(_("Selection cleared • OK selects and moves down"))

    def _cancel_category_selection(self):
        self._category_select_mode=False;self._category_selected_ids=set()
        if self.level=="genres":self._render_genre_rows();self["status"].setText(_("Ready"))

    def _hide_selected_categories(self):
        if not self._category_select_mode:return
        selected=set(self._category_selected_ids or set())
        if not selected:self["status"].setText(_("Select at least one category first"));return
        current=_profile_hidden_ids(load_settings(),self.profile,self.media_type);current.update(selected);self._save_hidden_category_ids(current)
        self._category_select_mode=False;self._category_selected_ids=set();self.load_genres(self.media_type)

    def _keep_selected_categories_only(self):
        if not self._category_select_mode:return
        selected=set(self._category_selected_ids or set())
        if not selected:self["status"].setText(_("Select at least one category first"));return
        known=set(_visibility_category_id(e) for e in (self._category_known_items or self.content_items) if _visibility_category_id(e))
        # Save only IDs known right now. Any folder the provider adds later has a
        # new ID, is absent from this hidden set and therefore appears immediately.
        hidden=set(gid for gid in known if gid not in selected);self._save_hidden_category_ids(hidden)
        self._category_select_mode=False;self._category_selected_ids=set();self.load_genres(self.media_type)

    def _show_all_categories(self):
        if self.media_type not in ("itv","vod","series"):return
        self._save_hidden_category_ids(set());self._category_select_mode=False;self._category_selected_ids=set();self.load_genres(self.media_type)

    def toggle_current_category_hidden(self):
        if self.level not in ("genres","items") or not self.media_type:self["status"].setText(_("Open a category first"));return
        category=str(self.genre or "")
        if self.level=="genres":
            idx=self["list"].getSelectedIndex()
            if 0<=idx<len(self.content_items):category=_visibility_category_id(self.content_items[idx])
        if not category or category=="*":self["status"].setText(_("This category cannot be hidden"));return
        values=_profile_hidden_ids(load_settings(),self.profile,self.media_type)
        if category in values:values.remove(category);state=_("visible")
        else:values.add(category);state=_("hidden")
        self._save_hidden_category_ids(values);self["status"].setText(_("Category %s")%state)
        if self.level=="genres":self.load_genres(self.media_type)

    def show_hidden_categories(self):
        cfg=load_settings();lines=[]
        for typ in ("itv","vod","series"):
            values=sorted(_profile_hidden_ids(cfg,self.profile,typ));lines.append("%s: %s"%(self._mode_name(typ),", ".join(values) or _("None")))
        self.session.open(MessageBox,_("Hidden categories")+"\n\n"+"\n".join(lines)+"\n\n"+_("Use MENU → Manage Categories to change visibility."),MessageBox.TYPE_INFO,timeout=15)

    def show_account_info(self):
        self["status"].setText(_("Loading account information..."))
        def ok(info):
            if not isinstance(info,dict): info={"Information":info}
            preferred=("login","phone","fname","lsname","tariff_plan","account_balance","expire_billing_date","end_date","status")
            lines=[]
            for key in preferred:
                if info.get(key) not in (None,""): lines.append("%s: %s"%(key.replace("_"," ").title(),info.get(key)))
            health=self.client.health_snapshot(); lines.extend([_("Portal Health: %s")%health.get("health","unknown"),_("Average Latency: %s ms")%health.get("latency_ms",0)])
            if not lines:
                for key,value in list(info.items())[:18]:
                    if value not in (None,"",[],{}): lines.append("%s: %s"%(str(key).replace("_"," ").title(),value))
            self.session.open(MessageBox,_("Account Information")+"\n\n"+("\n".join(lines) if lines else _("No account data returned")),MessageBox.TYPE_INFO,timeout=20); self["status"].setText(_("Ready"))
        self._run_async(self.client.account_info,ok,lambda e:self["status"].setText(_("Account info failed: %s")%e))

    def load_catchup_channels(self):
        if self._busy:return
        _perf29_catch_t0=time.monotonic();LOG.info("PERF29 catchup begin")
        self.level="catchup_channels_loading";self._apply_utility_glass_mode()
        self._set_list_density("itv")
        self["status"].setText(_("Loading catch-up channels...  •  BACK cancels")); self._show_skeleton(_("Loading catch-up"))
        def ok(rows):
            valid=[x for x in rows if isinstance(x,dict)]; self.level="catchup_channels"; self.media_type="itv"; self.content_items=valid; self.all_content_items=list(valid); self._catchup_channels_cache=list(valid)
            menu=[(str(x.get("name") or x.get("title") or _("Channel")),asset("settings_icons_40/channel_list.png"),x,_('Archive')) for x in valid]
            self["list"].set_icon_rows(menu or [(_("No catch-up channels"),asset("us86_episode_40.png"),None,{"meta":_("No EPG information"),"row_asset":_neutral_utility_glass().get("utility_row"),"row_selected_asset":_neutral_utility_glass().get("utility_selected")})]); self._reveal_browser_content(); self["section"].setText(_("Catch-up TV  /  Channels")); self["counter"].setText(_("%d channels")%len(valid)); self["status"].setText(_("Ready") if valid else _("No catch-up channels")); self._selection_changed();LOG.info("PERF29 catchup first_paint channels=%d elapsed_ms=%d",len(valid),int((time.monotonic()-_perf29_catch_t0)*1000))
        self._run_async(lambda handle:self.client.catchup_channels("*",1,max_pages=20,cancel_event=handle.cancel_event),ok,lambda e:self["status"].setText(_("Catch-up failed: %s")%e))

    def load_catchup_programs(self, channel):
        if self._busy:return
        self.level="catchup_programs_loading";self._apply_utility_glass_mode()
        self._set_list_density("compact")
        self.catchup_channel=channel; cid=channel.get("id") or channel.get("ch_id")
        if not cid: self["status"].setText(_("Channel has no archive identifier")); return
        self["status"].setText(_("Loading archive programmes...  •  BACK cancels"))
        def ok(rows):
            valid=[x for x in rows if isinstance(x,dict)]; self.level="catchup_programs"; self.content_items=valid
            menu=[]
            for x in valid:
                title=_clean_display_text(x.get("name") or x.get("title") or x.get("descr") or _("Programme"), 120); start=_clean_display_text(x.get("time") or x.get("start") or x.get("start_timestamp") or "", 30); menu.append((title,asset("settings_icons_40/catchup.png"),x,start))
            self["list"].set_icon_rows(menu or [(_("No archived programmes"),asset("settings_icons_40/catchup.png"),None,_("Try another channel"))]); self["section"].setText(_("Catch-up  /  %s")%str(channel.get("name") or _("Channel"))[:45]); self["counter"].setText(_("%d programmes")%len(valid)); self["status"].setText(_("Ready") if valid else _("No archive data")); self._selection_changed()
        hours=load_settings().get("catchup_hours",72)
        self._run_async(lambda handle:self.client.catchup_programs(cid,hours,cancel_event=handle.cancel_event),ok,lambda e:self["status"].setText(_("Archive EPG failed: %s")%e))

    def play_catchup_program(self, program):
        if not self.catchup_channel: return
        self["status"].setText(_("Creating catch-up stream..."))
        def ok(url):
            name=str(program.get("name") or program.get("title") or _("Catch-up programme"))
            if not isinstance(url, str) or not url.strip():
                self["status"].setText(_("Portal returned an invalid catch-up link")); return
            self["status"].setText(_("Opening Ultra Stalker player..."))
            def closed(result=None):
                engine = result.get("engine") if isinstance(result, dict) else load_settings().get("service_type", 4097)
                self["status"].setText(_("Player closed  •  engine %s") % engine)
            engine=_configured_playback_engine()
            payload=_player_payload(program, self.profile, {"_catchup_channel": dict(self.catchup_channel)}, "catchup")
            payload["_player_client_ref"]=self.client
            self.session.openWithCallback(closed, UltraStalkerPlayer, url.strip(), name, "catchup", engine, payload)
        self._run_async(lambda handle:self.client.create_catchup_link(self.catchup_channel,program,cancel_event=handle.cancel_event),ok,lambda e:self["status"].setText(_("Catch-up play failed: %s")%e))

    def open_audio(self):
        if AudioSelection is None: self.session.open(MessageBox,_("Audio selection is not available in this image"),MessageBox.TYPE_INFO,timeout=6)
        else:
            try: self.session.open(AudioSelection, infobar=None)
            except Exception as exc: self.session.open(MessageBox,_("Audio selection failed: %s")%exc,MessageBox.TYPE_ERROR,timeout=6)

    def open_subtitles(self):
        try:
            from Screens.AudioSelection import SubtitleSelection
            self.session.open(SubtitleSelection, infobar=None)
        except Exception as exc: self.session.open(MessageBox,_("Subtitle selection is unavailable: %s")%exc,MessageBox.TYPE_INFO,timeout=6)

    def _mode_name(self, media_type):
        for title,key,_icon in self.MODES:
            if key==media_type:return _(title)
        return str(media_type or _("Result"))

    def play_item(self,item):
        if not isinstance(item,dict) or self._busy:return
        command=item.get("cmd") or item.get("command") or item.get("url")
        if not command:self["status"].setText(_("No stream command"));return
        self["status"].setText(_("Creating stream link..."))
        def work(handle):
            # Playback URL first.  Provider picon I/O must never sit between a
            # successful create_link and the Enigma2 service handoff: temporary
            # Stalker links can be short-lived, and Player already reuses a
            # cached picon or fetches it asynchronously after opening.
            return self.client.create_link(item,self.media_type or "itv",cancel_event=handle.cancel_event)
        def ok(result):
            url=result if isinstance(result,str) else ""
            name=str(item.get("name") or item.get("title") or "Ultra Stalker stream")
            try:
                add_recently_played(self.profile, self.media_type or "itv", item)
                self["status"].setText(_("Opening player: %s") % name)
                def closed(result=None):
                    if not (isinstance(result,dict) and result.get("service_restored")):
                        try:force_session_silence(self.session,str(url).strip(),force=True)
                        except Exception as exc:optional_failure("ui.detail_return_stop",exc)
                    engine = result.get("engine") if isinstance(result, dict) else load_settings().get("service_type", 4097)
                    self["status"].setText(_("Player closed  •  engine %s") % engine)
                engine=_configured_playback_engine()
                payload=_player_payload(item, self.profile, media_type=self.media_type or "itv")
                payload["_player_client_ref"]=self.client
                if (self.media_type or "itv") in ("itv","live"):payload["_live_client_ref"]=self.client
                self.session.openWithCallback(closed, UltraStalkerPlayer, str(url).strip(), name, self.media_type or "itv", engine, payload)
            except Exception as exc:self["status"].setText(_("Player failed: %s")%exc)
        self._run_async(work,ok,lambda e:ok(""))

