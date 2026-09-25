"""Portal home screen extracted from ui.py without changing class behavior."""

from . import _
import hashlib
import json
import os
import queue
import re
import tempfile
import threading
import time
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag
from .log import diagnostic_failure, memory_snapshot as _mem34

try:
    from PIL import Image as _PILImage
except Exception:
    _PILImage = None

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePoint, eSize, eLabel, gFont, getDesktop, loadPNG

from .ui_async import AsyncScreenMixin
from .ui_image_loader import ImageLoaderMixin
from .ui_transition import TransitionMixin
from .ui_fixed_adaptive import fixed_home_assets, cleanup_legacy_application_outputs
from .ui_helpers import content_refresh_feedback, content_refresh_failure, fit_label_to_box, fit_inline_label_row
from .ui_icon_menu import IconMenuList
from .title_clean import display_title as _home_display_title

_HOME_LOGO_API = None
def _home_logo_api():
    global _HOME_LOGO_API
    if _HOME_LOGO_API is None:
        from .title_logo_runtime import (
            ultra_title_logo_cache_path, title_logo_language, valid_ultra_title_logo,
        )
        _HOME_LOGO_API = (ultra_title_logo_cache_path, title_logo_language, valid_ultra_title_logo)
    return _HOME_LOGO_API

def _home_title_logo_cache_path(*args, **kwargs):
    return _home_logo_api()[0](*args, **kwargs)
def _home_title_logo_language(*args, **kwargs):
    return _home_logo_api()[1](*args, **kwargs)
def _home_valid_title_logo(*args, **kwargs):
    return _home_logo_api()[2](*args, **kwargs)

_HOME_LOGO_SERVICE = None
def _home_logo_service():
    """Lazy shared Ultra title-logo service used only when Home has no ready logo.

    Home still paints from HDD first.  A missing Hero logo gets one bounded
    background resolve through the same Stage-5 policy/cache as the catalogue
    screens, so the persistent Hero can recover after cache namespace changes
    without blocking first paint.
    """
    global _HOME_LOGO_SERVICE
    if _HOME_LOGO_SERVICE is None:
        from .title_logo_ultra import ultra_title_logo_cached, resolve_ultra_title_logo
        _HOME_LOGO_SERVICE = (ultra_title_logo_cached, resolve_ultra_title_logo)
    return _HOME_LOGO_SERVICE

def _home_ultra_title_logo_cached(*args, **kwargs):
    return _home_logo_service()[0](*args, **kwargs)

def _home_resolve_ultra_title_logo(*args, **kwargs):
    return _home_logo_service()[1](*args, **kwargs)

# Runtime dependencies are injected by ui.py after its shared helpers are ready.
HOME_SKIN = ""
BACKDROP_CACHE_DIR = ""
HOME_HERO_FILE = ""
HOME_HERO_SCHEMA = 0
HOME_HERO_TTL = 0
HOME_RUNTIME_DIR = ""
IMAGE_CACHE_DIR = ""
THUMB_CACHE_DIR = ""
LOG = None
PortalSession = None
TMDBClient = None
UltraStalkerPlayer = None
_CATEGORY_PREFETCH_EXECUTOR = None
_HOME_HERO_LOCK = None
_IMAGE_EXECUTOR = None
PERSISTENT_GENERATED_DIR = ""
_fit_live_picon_canvas = None
_build_home_adaptive_focus = None
_build_home_mood_assets = None
_cleanup_legacy_home_hero_storage = None
_category_cache_get = None
_category_cache_put = None
_invalidate_source_navigation_cache = None
_clean_display_text = None
_configured_playback_engine = None
_client_from_profile = None
_fsync_parent_dir = None
_home_prepare_art = None
_home_cached_art = None
_player_payload = None
_valid_cache_file = None
add_recently_played = None
asset = None
current_plugin_launch = None
load_playback_progress = None
load_profiles = None
load_recently_played = None
history_revision = None
load_settings = None
load_ui_state = None
optional_failure = None
premium_title = None
save_profiles = None
save_ui_state = None
home_boot_pixmap = None
splash_home_is_warm = None
splash_home_account_info = None
splash_home_recent_assets = None

# Forward screen references are filled after those classes are defined.
ContentDetailsScreen = None
NovaSettingsScreen = None
PortalBrowserScreen = None
PortalGlobalSearchScreen = None

def configure_home_screen(**deps):
    globals().update(deps)
    if "HOME_SKIN" in deps:
        PortalHomeScreen.skin = deps["HOME_SKIN"]

class PortalHomeScreen(Screen, AsyncScreenMixin, ImageLoaderMixin, TransitionMixin):
    skin = HOME_SKIN
    CARDS = (
        ("Live TV", "Watch live channels", "itv"),("Movies", "Browse movie library", "vod"),("Series", "Explore TV series", "series"),
        ("Catch-up", "Watch past programs", "catchup"),("Favorites", "Your saved content", "favorites"),("Search", "Find content quickly", "search"),("Settings", "Portal & player settings", "settings"),
    )
    CARD_X=(55,313,571,829,1087,1345,1603)
    RECENT_PANEL_X=(55,677,1299)
    RECENT_ROW_X=(285,907,1529)
    RECENT_ROW_Y=(758,856,954)

    def __init__(self,session,profile):
        _perf_t0=time.monotonic()
        if LOG is not None: LOG.info("PERF25 home init_enter mono_ms=%d",int(_perf_t0*1000))
        Screen.__init__(self,session);self._async_init();self.onClose.append(self._stop_async);self.onClose.append(self._image_stop)
        self._ui_diag_open_mono=time.monotonic();_ui_diag("ui_open",screen="home")
        self.profile=profile
        _home_cfg=load_settings() or {}
        self._home_clean_titles=bool(_home_cfg.get("clean_titles",True))
        self.portal_session=PortalSession(profile,timeout=_home_cfg.get("timeout",10));self.client=self.portal_session.client
        # First-run language/Clean Names now completes on the existing Splash
        # before Portal List opens. Home owns no onboarding widgets or actions.
        try:
            _desk=getDesktop(0).size();self._home_sx=float(_desk.width())/1920.0;self._home_sy=float(_desk.height())/1080.0
        except Exception:
            self._home_sx=1.0;self._home_sy=1.0
        self.index=0;self.recent_index=0;self.focus_row="menu"
        self._recent_entries=[None]*9;self._recent_art_paths=[None]*9;self._recent_progress_paths=[None]*9;self._recent_group_art_paths=[None]*3
        self._recent_focus_drawn=-1;self._recent_generation=0;self._recent_futures=set();self._home_jobs=queue.Queue();self._hero_data=None
        self._recent_render_revision=-1
        # Point 5: Recent/Home lower cards are a resident presentation surface,
        # just like the fixed menu cards above them.  Prepare them before the
        # first visible paint and never rebuild them again merely because the
        # deferred Hero pass runs.
        self._recent_snapshot_ready=False
        self._recent_lists_initialized=False
        self._home_has_shown_once=False;self._home_return_pending=False;self._home_runtime_visible=True
        self["brand"]=Label("")
        self["hero_kicker"]=Label("");self["hero_title"]=Label("");self["hero_subtitle"]=Label("");self["hero_rating"]=Label("")
        self["hero_title_logo"]=Pixmap();self._home_title_logo_token=0;self._home_title_logo_path=""
        self._home_title_logo_identity="";self._home_title_logo_future=None;self._home_title_logo_cancel_event=None;self._home_title_logo_checked_identity=""
        self.onClose.append(self._home_cancel_title_logo_job)
        self["portal_state"]=Label("●")
        self["portal_connected"]=Label(_("Connected"))
        self["portal_expiry_prefix"]=Label("")
        self["portal_expiry"]=Label("")
        self._set_home_account_line()
        self["selection"]=Pixmap();self["hero_backdrop"]=Pixmap();self["ambient_bg"]=Pixmap();self["continue_header"]=Label(_("CONTINUE / RECENT"))
        for i in range(7):self["menu_bg%d"%i]=Pixmap()
        for i in range(7):self["menu_icon%d"%i]=Pixmap()
        for i in range(3):
            self["recent_bg%d"%i]=Pixmap();self["recent_art%d"%i]=Pixmap()
            # R181: one native MultiContent list draws all three rows for this
            # panel. This replaces 12 resident widgets per panel.
            self["recent_list%d"%i]=IconMenuList([],width=305,item_height=98,icon_size=0,primary_font=22,secondary_font=13,row_style="home_recent")
        for i,(title,subtitle,_action) in enumerate(self.CARDS):self["card_title%d"%i]=Label(_(title));self["card_sub%d"%i]=Label(_(subtitle))
        self._recent_row_normal_path=asset("fixed_master_r63/home_recent_row.png")
        self._recent_row_selected_path=asset("fixed_master_r63/home_recent_row_selected.png")
        self["status"]=Label(_("LEFT / RIGHT Navigate • DOWN Recent • OK Open • BACK Portals"))
        # Home reuses the exact Settings passive-notice master.  It lives only
        # in the Hero/backdrop lane, above the cards and Recent section.
        self["refresh_notice_panel"]=Pixmap();self["refresh_notice_title"]=Label("");self["refresh_notice_message"]=Label("")
        self._refresh_notice_visible=False;self._content_refresh_running=False
        for _name in ("refresh_notice_panel","refresh_notice_title","refresh_notice_message"):
            try:self[_name].hide()
            except Exception:pass
        self._image_init("hero_backdrop",(1920,1080),profile,self.client)
        self["actions"]=ActionMap(["OkCancelActions","DirectionActions","MenuActions","UltraStalkerMenuActions","InfoActions","ColorActions"],{
            "cancel":self.close,"left":self.move_left,"right":self.move_right,"up":self.move_up,"down":self.move_down,"ok":self.open_selected,"menu":self.open_settings,"info":self.open_account,"green":self.refresh_current_content,"yellow":self.open_search_shortcut},-1)
        self["refresh_notice_actions"]=ActionMap(["OkCancelActions","ColorActions"],{
            "ok":self._hide_refresh_notice,"cancel":self._hide_refresh_notice,"green":self._hide_refresh_notice},-1000)
        try:self["refresh_notice_actions"].setEnabled(False)
        except Exception:pass
        # Single-slot Hero: no rotation/fill timer and no automatic Home artwork jobs.
        self._hero_timer=None;self._hero_timer_conn=None;self._hero_fill_running=False
        self.onLayoutFinish.append(self._layout_ready)
        self._home_rebind_timer=eTimer();self._home_rebind_conn=None
        try:self._home_rebind_conn=self._home_rebind_timer.timeout.connect(self._home_delayed_rebind)
        except Exception:self._home_rebind_timer.callback.append(self._home_delayed_rebind)
        self.onClose.append(self._stop_home_rebind_timer)
        # PERF59: passive whole-plugin navigation/memory audit.  A short and a
        # later Home-return probe show whether child-screen memory/fds/threads
        # actually settle after close, without forcing GC or changing runtime
        # behaviour.  The probe is cancelled immediately if Home is hidden again.
        self._perf59_cycle=0;self._perf59_return_stage=0;self._perf59_return_action=""
        self._perf59_return_probe=eTimer();self._perf59_return_probe_conn=None
        try:self._perf59_return_probe_conn=self._perf59_return_probe.timeout.connect(self._perf59_return_probe_fire)
        except Exception:self._perf59_return_probe.callback.append(self._perf59_return_probe_fire)
        self.onClose.append(self._perf59_stop_return_probe)
        # PERF26: first paint must not wait for Hero/adaptive PNG decode.
        # Bind the Home shell immediately, then apply the persisted Hero one
        # event-loop turn later. The settled visual result is unchanged.
        self._startup_hero_timer=eTimer();self._startup_hero_conn=None
        try:self._startup_hero_conn=self._startup_hero_timer.timeout.connect(self._startup_hero_apply)
        except Exception:self._startup_hero_timer.callback.append(self._startup_hero_apply)
        self.onClose.append(self._stop_startup_hero_timer)
        # Remote focus state is persisted after a short quiet window instead of
        # rewriting the UI-state JSON on every LEFT/RIGHT keypress.
        self._home_focus_state_pending=None;self._home_focus_state_timer=eTimer();self._home_focus_state_conn=None
        try:self._home_focus_state_conn=self._home_focus_state_timer.timeout.connect(self._flush_home_focus_state)
        except Exception:self._home_focus_state_timer.callback.append(self._flush_home_focus_state)
        self.onClose.append(self._flush_home_focus_state);self.onClose.append(self._stop_home_focus_state_timer)
        # R129 SPEED: Home first paint must stay responsive while the heavy but
        # non-essential Recent/category warmers settle.  Defer those jobs to
        # separate one-shot timers instead of running them in the same UI turn
        # that binds the Hero.  Nothing visual is removed; only scheduling moves.
        self._home_recent_refresh_timer=eTimer();self._home_recent_refresh_conn=None
        try:self._home_recent_refresh_conn=self._home_recent_refresh_timer.timeout.connect(self._home_recent_refresh_fire)
        except Exception:self._home_recent_refresh_timer.callback.append(self._home_recent_refresh_fire)
        self._home_prefetch_delay_timer=eTimer();self._home_prefetch_delay_conn=None
        try:self._home_prefetch_delay_conn=self._home_prefetch_delay_timer.timeout.connect(self._home_prefetch_delay_fire)
        except Exception:self._home_prefetch_delay_timer.callback.append(self._home_prefetch_delay_fire)
        self.onClose.append(self._stop_home_speed_timers)
        self.onClose.append(self._stop_home_background_jobs)
        self.onClose.append(self._stop_home_ui_hooks)
        self.onClose.append(self._ui_diag_home_close)
        try:self.onShown.append(self._home_shown)
        except Exception as exc:optional_failure("ui",exc)
        try:self.onHide.append(self._home_hidden_release)
        except Exception as exc:optional_failure("ui.home_hide_hook",exc)
        if LOG is not None: LOG.info("PERF25 home init_done elapsed_ms=%d",int((time.monotonic()-_perf_t0)*1000))
        _mem34("home_init_done")

    def _stop_startup_hero_timer(self):
        try:self._startup_hero_timer.stop()
        except Exception:pass
        try:
            if self._startup_hero_conn is not None:self._startup_hero_conn.disconnect()
        except Exception:pass
        try:
            if self._startup_hero_apply in self._startup_hero_timer.callback:self._startup_hero_timer.callback.remove(self._startup_hero_apply)
        except Exception:pass

    def _startup_hero_apply(self):
        try:self._startup_hero_timer.stop()
        except Exception:pass
        if getattr(self,"_screen_closed",False) or not getattr(self,"_home_runtime_visible",True):
            return
        _phase=time.monotonic()
        try:self._load_home_hero()
        except Exception as exc:optional_failure("ui.home_startup_hero",exc)
        try:self._update_focus()
        except Exception as exc:optional_failure("ui.home_startup_focus",exc)
        if LOG is not None:LOG.info("PERF26 home deferred_hero_done elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        _mem34("home_hero_settled", recent_futures=len(getattr(self,"_recent_futures",set()) or set()))
        # R132/R81 transplant: on this receiver family the proven R81 order is
        # faster in practice. Warm category routing immediately after Hero paint
        # so the user reaches Live/Movies/Series from RAM, rather than starting
        # portal I/O exactly while the first LEFT/RIGHT presses are happening.
        _warm=False
        try:_warm=bool(splash_home_is_warm and splash_home_is_warm(self.profile))
        except Exception:_warm=False
        if not _warm:
            try:self._start_category_prefetch()
            except Exception as exc:optional_failure("ui.category_prefetch_start",exc)
        elif LOG is not None:
            LOG.info("R268 home category_prefetch skipped reason=splash_warm")
        # Lower cards were already committed before first paint.  Do not run a
        # second setList/art bind here: that redundant deferred rebuild was the
        # visible flash reported on Home.  Keep the exact resident snapshot.
        _recent_phase=time.monotonic()
        if not bool(getattr(self,"_recent_snapshot_ready",False)):
            try:
                self._load_recent_cards(initial=True)
                self._recent_snapshot_ready=True
            except Exception as exc:optional_failure("ui.home_recent_deferred",exc)
        if LOG is not None:LOG.info("POINT5 home recent_resident_keep elapsed_ms=%d",int((time.monotonic()-_recent_phase)*1000))

    def _pause_home_runtime_timers(self):
        self._home_runtime_visible=False
        # Home stays alive underneath child screens. Keep Hero maintenance asleep until Home is shown again.
        for timer in (getattr(self,"_hero_timer",None),):
            if timer is None:continue
            try:timer.stop()
            except Exception as exc:optional_failure("ui.home_runtime_timer_pause",exc)

    def _resume_home_runtime_timers(self):
        if getattr(self,"_screen_closed",False):return
        self._home_runtime_visible=True
        for timer,interval in ((getattr(self,"_hero_timer",None),60000),):
            if timer is None:continue
            try:
                timer.stop();timer.start(interval,False)
            except Exception as exc:optional_failure("ui.home_runtime_timer_resume",exc)

    def _cancel_recent_futures(self):
        futures=getattr(self,"_recent_futures",None)
        if not isinstance(futures,set):
            futures=set();self._recent_futures=futures
        for future in list(futures):
            if future is None or future.done():
                futures.discard(future);continue
            try:future.cancel()
            except Exception as exc:optional_failure("ui.home_recent_future_cancel",exc)
            if future.done():futures.discard(future)

    def _ui_diag_home_close(self):
        _mem34("home_close")
        try:_ui_diag("ui_close",screen="home",lifetime_ms=int(max(0.0,(time.monotonic()-self._ui_diag_open_mono)*1000.0)))
        except Exception as exc: diagnostic_failure("ui.home.failsoft", exc)

    def _stop_home_ui_hooks(self):
        for hook_name, callback in (
            ("onShown", self._home_shown),
            ("onHide", self._home_hidden_release),
            ("onLayoutFinish", self._layout_ready),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.home_hook_cleanup",exc)

    def _load_menu_icons(self):
        """Load Home icons as live Pixmap widgets above all adaptive layers.

        OpenBH can occasionally drop static ePixmap surfaces after large TMDB
        artwork/cache activity.  These seven navigation icons are core UI, so
        reload them explicitly whenever Home is laid out or shown and keep
        their z-order above adaptive card/focus artwork.
        """
        names=("home_live.png","home_movies.png","home_series.png","home_catchup.png","home_favorites.png","home_search.png","home_settings.png")
        for i,name in enumerate(names):
            try:
                path=asset(name)
                if os.path.isfile(path) and os.path.getsize(path)>256:
                    pix=home_boot_pixmap(path) if callable(home_boot_pixmap) else None
                    if pix is not None:self["menu_icon%d"%i].instance.setPixmap(pix)
                    else:self["menu_icon%d"%i].instance.setPixmapFromFile(path)
                    self["menu_icon%d"%i].show()
            except Exception as exc:
                optional_failure("ui.home_icon_%d"%i,exc)

    def _stop_home_rebind_timer(self):
        try:self._home_rebind_timer.stop()
        except Exception as exc:optional_failure("ui.home_rebind_timer_stop",exc)
        try:
            if self._home_rebind_conn is not None:self._home_rebind_conn.disconnect()
        except Exception as exc:optional_failure("ui.home_rebind_timer_disconnect",exc)
        try:
            if self._home_delayed_rebind in self._home_rebind_timer.callback:self._home_rebind_timer.callback.remove(self._home_delayed_rebind)
        except Exception as exc:optional_failure("ui.home_rebind_timer_callback",exc)

    def _home_hero_source(self,hero):
        if not isinstance(hero,dict):return None
        for key in ("prepared","display_backdrop_local","backdrop_local","display_poster_local","poster_local"):
            src=str(hero.get(key) or "")
            try:
                if src and os.path.isfile(src) and os.path.getsize(src)>4096:return src
            except Exception as exc:optional_failure("ui.home_source_check",exc)
        return None

    def _ensure_home_visual_materials(self,hero):
        """R171 Home fix: fixed green chrome is skin-owned, never rebuilt/rebound.

        Hero state remains artwork/content only.  The immutable Home ambient,
        menu/recent cards and focus surfaces are already bound by HOME_SKIN on
        the first frame, so touching them here would only create a black->green
        flash and unnecessary PNG work.
        """
        return hero

    def _force_pixmap_file(self,name,path,show=True):
        if not path or not os.path.isfile(str(path)):return False
        try:
            widget=self[name]
            if widget.instance is None:return False
            try:widget.instance.setPixmap(None)
            except Exception as exc:optional_failure("ui.home_pixmap_clear",exc)
            try:
                pix=loadPNG(str(path))
                if pix is not None:widget.instance.setPixmap(pix)
                else:widget.instance.setPixmapFromFile(str(path))
            except Exception:
                widget.instance.setPixmapFromFile(str(path))
            if show:widget.show()
            return True
        except Exception as exc:
            optional_failure("ui.home_pixmap_force",exc);return False

    def _rebind_home_materials(self,hero):
        """Compatibility hook; the fixed Home chrome is permanently skin-bound.

        Dynamic Hero artwork is handled by _apply_home_hero().  Do not reload
        ambient/menu/recent/focus PNGs here on child return or Hero refresh.
        """
        return

    def _force_home_visual_rebind(self):
        """Restore the exact pre-child Home hero, adaptive mood and 3D glass."""
        if getattr(self,"_screen_closed",False):return
        try:self._image_layout_ready()
        except Exception as exc:optional_failure("ui.home_rebind_layout",exc)

        hero=self._hero_data if isinstance(getattr(self,"_hero_data",None),dict) else None
        if not hero:
            try:hero=self._read_hero_cache()
            except Exception:hero=None
        if isinstance(hero,dict):
            hero=self._ensure_home_visual_materials(dict(hero))
            self._hero_data=hero
            try:self._apply_home_hero(hero)
            except Exception as exc:optional_failure("ui.home_rebind_apply",exc)
            try:self._rebind_home_materials(hero)
            except Exception as exc:optional_failure("ui.home_rebind_native",exc)
        else:
            try:self._load_home_hero(force=False)
            except Exception as exc:optional_failure("ui.home_rebind_fallback",exc)

        try:self._update_focus()
        except Exception as exc:optional_failure("ui.home_rebind_focus",exc)

    def _home_delayed_rebind(self):
        try:self._home_rebind_timer.stop()
        except Exception as exc:optional_failure("ui.home_rebind_timer",exc)
        if getattr(self,"_screen_closed",False):return
        try:self._home_fast_return()
        except Exception as exc:optional_failure("ui.home_delayed_pin_check",exc)
        try:self._force_home_visual_rebind()
        except Exception as exc:optional_failure("ui.home_delayed_rebind",exc)
        # While one explicit single-slot Hero is being prepared, poll only a
        # tiny /tmp marker.  No network/HDD scan/image work occurs here.
        try:
            marker=os.path.join("/tmp","ultrastalker_home_hero_pending")
            if os.path.isfile(marker):
                age=max(0.0,time.time()-os.path.getmtime(marker))
                # release could legitimately need a minute or two on receiver HDD/CPU.
                # Poll only this tiny /tmp marker; no network or image build runs here.
                if age<180.0:self._home_rebind_timer.start(1000,True)
        except Exception as exc:optional_failure("ui.home_single_pin_poll",exc)

    def _home_fast_return(self):
        """Child return: keep warm visuals, but honor a newly pinned Home Hero."""
        if getattr(self,"_screen_closed",False):return
        try:
            state=self._read_hero_state() or {}
            pinned=self._read_hero_cache()
            if pinned:
                pinned=self._ensure_home_visual_materials(dict(pinned))
                self._hero_data=pinned
                self._apply_home_hero(pinned)
                self._rebind_home_materials(pinned)
        except Exception as exc:optional_failure("ui.home_fast_manual_pin",exc)
        try:self._update_focus()
        except Exception as exc:optional_failure("ui.home_fast_focus",exc)


    def _perf59_stop_return_probe(self):
        try:self._perf59_return_probe.stop()
        except Exception:pass
        try:
            if self._perf59_return_probe_conn is not None:self._perf59_return_probe_conn.disconnect()
        except Exception:pass
        try:
            if self._perf59_return_probe_fire in self._perf59_return_probe.callback:self._perf59_return_probe.callback.remove(self._perf59_return_probe_fire)
        except Exception:pass

    def _perf59_schedule_return_probe(self, action):
        self._perf59_return_action=str(action or "")
        self._perf59_return_stage=1
        try:
            self._perf59_return_probe.stop();self._perf59_return_probe.start(1500,True)
        except Exception:pass

    def _perf59_return_probe_fire(self):
        if getattr(self,"_screen_closed",False) or not getattr(self,"_home_runtime_visible",True):return
        stage=int(getattr(self,"_perf59_return_stage",0) or 0)
        if stage not in (1,2):return
        cycle=int(getattr(self,"_perf59_cycle",0) or 0);action=str(getattr(self,"_perf59_return_action","") or "")
        if LOG is not None:LOG.info("PERF59 home_return_settle cycle=%d action=%s stage=%d",cycle,action,stage)
        _mem34("home_return_settle",action=action,cycle=cycle,stage=stage)
        if stage==1:
            self._perf59_return_stage=2
            try:self._perf59_return_probe.start(4500,True)
            except Exception:pass
        else:
            self._perf59_return_stage=0

    def _home_shown(self):
        if getattr(self,"_screen_closed",False):return
        _perf59_return_t0=time.monotonic()
        try:self._home_clean_titles=bool((load_settings() or {}).get("clean_titles",True))
        except Exception:pass
        self._resume_home_runtime_timers()
        self._home_images_suspended=False
        returning=bool(getattr(self,"_home_return_pending",False) and getattr(self,"_home_has_shown_once",False))
        self._home_return_pending=False
        if not returning and not getattr(self,"_v911_highlights_checked",False):
            self._v911_highlights_checked=True
            try:
                from .updater import maybe_show_v911_highlights
                maybe_show_v911_highlights(self.session,delay_ms=850)
            except Exception as exc:
                optional_failure("ui.home_v911_highlights",exc)
        # Reassert resident Recent artwork geometry whenever Home becomes visible.
        # This is intentionally local-only: no artwork lookup, Pillow work or
        # network request.  It fixes the rare Live-card enlargement seen after
        # returning from fullscreen playback.
        for _g in range(3):
            try:self._set_recent_art_geometry(_g)
            except Exception as exc:optional_failure("ui.home_recent_geometry_restore",exc)
        try:
            _preferred=self.recent_index if self.focus_row=="recent" and self._recent_group(self.recent_index)==0 else 0
            self._bind_recent_group_art(0,_preferred)
        except Exception as exc:optional_failure("ui.home_recent_live_hard_rebind",exc)
        if returning:
            # Child screens leave all non-hero Home pixmaps bound. Repaint only
            # the hero released by _image_suspend(); a full rebind here was the
            # source of the heavy return-to-Home pause.
            try:self._home_rebind_timer.stop()
            except Exception as exc:optional_failure("ui.home_fast_timer_stop",exc)
            try:self._home_fast_return()
            except Exception as exc:optional_failure("ui.home_fast_return",exc)
            # Manual Home preparation runs off-thread and may finish a fraction
            # after Details closes. One delayed HDD-state check is cheap and lets
            # the ready composite appear without waiting for the 60s hero timer.
            try:
                self._home_rebind_timer.stop();self._home_rebind_timer.start(900,True)
            except Exception as exc:optional_failure("ui.home_pin_ready_recheck",exc)
        else:
            # PERF26: cold Home no longer performs a full Hero/adaptive rebind
            # synchronously in onShown. _layout_ready already armed the
            # startup-Hero timer, so the fallback shell becomes interactive
            # immediately and the exact same Hero is applied just after paint.
            pass
        self._home_has_shown_once=True
        _perf59_action=getattr(self,"_mem34_last_action","") if returning else ""
        _perf59_cycle=int(getattr(self,"_perf59_cycle",0) or 0)
        _mem34("home_return" if returning else "home_shown", action=_perf59_action, cycle=_perf59_cycle if returning else 0)
        # Recent stays resident underneath child screens.  Only rebuild after a
        # real history mutation (playback/progress/watch/remove), detected via the
        # in-process revision token.  Normal Home -> child -> Home navigation does
        # zero SQLite/artwork work and paints the exact same Recent state instantly.
        if returning:
            try:
                _history_rev=int(history_revision()) if callable(history_revision) else int(getattr(self,"_recent_render_revision",-1))
            except Exception:
                _history_rev=int(getattr(self,"_recent_render_revision",-1))
            if _history_rev!=int(getattr(self,"_recent_render_revision",-1)):
                self._schedule_home_recent_refresh(1)
            if LOG is not None:LOG.info("PERF59 home_return_sync cycle=%d action=%s recent_dirty=%s elapsed_ms=%d",_perf59_cycle,str(_perf59_action or ""),str(_history_rev!=int(getattr(self,"_recent_render_revision",-1))),int((time.monotonic()-_perf59_return_t0)*1000))
            self._perf59_schedule_return_probe(_perf59_action)

    def _home_hidden_release(self):
        if getattr(self,"_screen_closed",False):return
        try:self._perf59_return_probe.stop()
        except Exception:pass
        self._perf59_return_stage=0
        _mem34("home_hidden", action=getattr(self,"_mem34_last_action","") or "", cycle=int(getattr(self,"_perf59_cycle",0) or 0))
        self._pause_home_runtime_timers()
        self._home_images_suspended=False
        self._home_return_pending=True
        # Keep the fully prepared hero + adaptive Home surfaces bound underneath
        # child screens. This makes Home return behave like a warm poster cache.
        # Pending UI-only warmers are cancelled so they cannot steal the event
        # loop while the child Browser is trying to paint its first frame.
        try:self._home_recent_refresh_timer.stop()
        except Exception:pass
        try:self._home_prefetch_delay_timer.stop()
        except Exception:pass
        try:self._home_rebind_timer.stop()
        except Exception as exc:optional_failure("ui.home_hide_timer",exc)
        # Recent rows/art remain bound and resident while child screens are open.
        # Do not clear, hide, or rebuild them merely because Home lost visibility.
        # A real playback/history mutation is handled once on return by revision.


    def _fit_home_localized_text(self):
        # R48: the seven fixed cards keep their exact geometry.  Only their
        # description text becomes receiver-measured and language independent.
        for idx in range(7):
            try:
                fit_label_to_box(self,"card_sub%d"%idx,max_size=14,min_size=9,padding=12,
                                 allow_two_lines=True,prefer_two_lines=True,height_padding=3)
            except Exception as exc:
                optional_failure("ui.home_card_localized_fit",exc)
        self._fit_home_account_row()

    def _fit_home_account_row(self):
        # Keep Arabic/Persian words and the Latin expiry date in separate
        # labels.  That prevents bidi reordering from throwing the date across
        # the Home screen while retaining the exact fixed status lane.
        try:
            fit_inline_label_row(
                self,("portal_connected","portal_expiry_prefix","portal_expiry"),
                start_x=78,end_x=1135,y=488,height=34,max_size=19,min_size=13,gap=8,padding=5,
            )
        except Exception as exc:
            optional_failure("ui.home_account_localized_fit",exc)

    def _layout_ready(self):
        _perf_t0=time.monotonic()
        self._fit_home_localized_text()
        for _g in range(3):
            try:
                _lst=self["recent_list%d"%_g]
                _lst.row_width=305;_lst.set_layout(98,0,22,13,row_style="home_recent")
                if _lst.instance is not None:
                    _lst.instance.setSelectionEnable(0);_lst.instance.setTransparent(1);_lst.instance.setScrollbarMode(2)
            except Exception as exc:optional_failure("ui.home_recent_list_layout",exc)
        try:_ui_diag("ui_ready",screen="home",elapsed_ms=int((time.monotonic()-self._ui_diag_open_mono)*1000.0))
        except Exception as exc: diagnostic_failure("ui.home.failsoft", exc)
        _phase=time.monotonic();self._image_layout_ready()
        if LOG is not None: LOG.info("PERF25 home layout_image elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        _phase=time.monotonic();self._load_menu_icons()
        if LOG is not None: LOG.info("PERF25 home layout_icons elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        _phase=time.monotonic();self._restore_home_focus()
        if LOG is not None: LOG.info("PERF25 home layout_focus elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        # Point 5: build the purely-local Recent snapshot before first paint.
        # It is only SQLite/HDD cache work, never network I/O.  The old deferred
        # pass made the lower Home cards appear a beat after the fixed upper
        # cards (and then repaint again during Hero settle), which looked like
        # flashing even though the content itself was static.
        _phase=time.monotonic()
        try:
            self._load_recent_cards(initial=True)
            self._recent_snapshot_ready=True
        except Exception as exc:
            optional_failure("ui.home_recent_first_paint",exc)
        if LOG is not None: LOG.info("POINT5 home layout_recent_resident elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        _phase=time.monotonic();self._load_account_state()
        if LOG is not None: LOG.info("PERF25 home layout_account_queue elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        # R268: selected-portal cold-start work now completes behind Splash.
        # When that warm state exists, bind the already-decoded Hero before the
        # first Home paint instead of showing Home and firing an 80ms settle.
        _phase=time.monotonic()
        _warm=False
        try:_warm=bool(splash_home_is_warm and splash_home_is_warm(self.profile))
        except Exception:_warm=False
        if _warm:
            try:self._startup_hero_apply()
            except Exception as exc:optional_failure("ui.home_warm_hero_first_paint",exc)
            if LOG is not None: LOG.info("R268 home layout_hero_warm elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        else:
            try:self._startup_hero_timer.stop();self._startup_hero_timer.start(80,True)
            except Exception:self._startup_hero_apply()
            if LOG is not None: LOG.info("PERF26 home layout_hero_deferred elapsed_ms=%d",int((time.monotonic()-_phase)*1000))
        if LOG is not None: LOG.info("PERF25 home layout_done elapsed_ms=%d mono_ms=%d",int((time.monotonic()-_perf_t0)*1000),int(time.monotonic()*1000))
        _mem34("home_layout_done")

    def _schedule_home_recent_refresh(self, delay_ms=120):
        if getattr(self,"_screen_closed",False):return
        try:
            self._home_recent_refresh_timer.stop()
            self._home_recent_refresh_timer.start(max(1,int(delay_ms or 1)),True)
        except Exception:
            self._home_recent_refresh_fire()

    def _home_recent_refresh_fire(self):
        try:self._home_recent_refresh_timer.stop()
        except Exception:pass
        if getattr(self,"_screen_closed",False) or not getattr(self,"_home_runtime_visible",True):return
        _phase=time.monotonic()
        try:self._load_recent_cards()
        except Exception as exc:optional_failure("ui.home_recent_deferred",exc)
        if LOG is not None:LOG.info("R129 home recent_deferred elapsed_ms=%d",int((time.monotonic()-_phase)*1000))

    def _schedule_home_prefetch(self, delay_ms=320):
        if getattr(self,"_screen_closed",False) or getattr(self,"_category_prefetch_started",False):return
        try:
            self._home_prefetch_delay_timer.stop()
            self._home_prefetch_delay_timer.start(max(1,int(delay_ms or 1)),True)
        except Exception:
            self._home_prefetch_delay_fire()

    def _home_prefetch_delay_fire(self):
        try:self._home_prefetch_delay_timer.stop()
        except Exception:pass
        if getattr(self,"_screen_closed",False) or not getattr(self,"_home_runtime_visible",True):return
        try:self._start_category_prefetch()
        except Exception as exc:optional_failure("ui.category_prefetch_start",exc)

    def _stop_home_speed_timers(self):
        for timer in (getattr(self,"_home_recent_refresh_timer",None),getattr(self,"_home_prefetch_delay_timer",None)):
            if timer is None:continue
            try:timer.stop()
            except Exception:pass
        for timer,conn,callback in (
            (getattr(self,"_home_recent_refresh_timer",None),getattr(self,"_home_recent_refresh_conn",None),self._home_recent_refresh_fire),
            (getattr(self,"_home_prefetch_delay_timer",None),getattr(self,"_home_prefetch_delay_conn",None),self._home_prefetch_delay_fire),
        ):
            if timer is None:continue
            try:
                if conn is not None:conn.disconnect()
            except Exception:pass
            try:
                if callback in timer.callback:timer.callback.remove(callback)
            except Exception:pass

    def _start_category_prefetch(self):
        """Warm category lists after Home is visible without blocking navigation.

        One low-priority worker reuses the already connected Home client.  Category
        screens can then render from RAM immediately instead of constructing a new
        portal session and waiting several seconds for authorization/categories.
        """
        if getattr(self, "_category_prefetch_started", False):
            return
        self._category_prefetch_started = True
        profile = dict(self.profile or {})
        client = self.client
        def worker():
            _prefetch_t0=time.monotonic()
            if LOG is not None:LOG.info("PERF30 category_prefetch begin")
            # Match Home card order. Live is normally almost free on Stalker, then
            # Movies/Series are warmed before the viewer reaches them. One worker
            # keeps portal pressure bounded and never blocks the Enigma2 thread.
            for media in ("itv", "vod", "series"):
                try:
                    cached=_category_cache_get(profile, media)
                    if cached is not None:
                        if LOG is not None:LOG.info("PERF30 category_prefetch media=%s cache_hit=yes rows=%d",media,len(cached) if isinstance(cached,list) else -1)
                        continue
                    _media_t0=time.monotonic()
                    rows = client.genres(media)
                    if isinstance(rows, list):
                        _category_cache_put(profile, media, rows)
                    if LOG is not None:LOG.info("PERF30 category_prefetch media=%s elapsed_ms=%d rows=%d",media,int((time.monotonic()-_media_t0)*1000),len(rows) if isinstance(rows,list) else -1)
                except Exception as exc:
                    optional_failure("ui.category_prefetch_%s" % media, exc)
            if LOG is not None:LOG.info("PERF30 category_prefetch done elapsed_ms=%d",int((time.monotonic()-_prefetch_t0)*1000))
        try:
            _CATEGORY_PREFETCH_EXECUTOR.submit(worker)
        except Exception as exc:
            optional_failure("ui.category_prefetch_submit", exc)

    def _stop_home_background_jobs(self):
        self._cancel_recent_futures()
        # AsyncScreenMixin has already marked the Screen closed; discard any
        # artwork payloads that completed in the same event-loop turn.
        try:
            while True:self._home_jobs.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:optional_failure("ui.home_queue_drain",exc)
        self._hero_data=None

    def _restore_home_focus(self):
        state=load_ui_state(self.profile)
        try:self.index=max(0,min(len(self.CARDS)-1,int(state.get("home_index",self.index))))
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            _saved_recent=int(state.get("home_recent_index",self.recent_index))
            # Migrate the old 3-panel index (0/1/2) to the first row of the
            # new Live/Movie/Series columns exactly once.
            if not state.get("home_recent_grid_v1") and 0<=_saved_recent<=2:_saved_recent*=3
            self.recent_index=max(0,min(8,_saved_recent))
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        row=str(state.get("home_focus_row") or self.focus_row or "menu")
        self.focus_row=row if row in ("menu","recent") else "menu"
        self._update_focus()

    def _stop_hero_timer(self):
        return

    def _hero_tick(self):
        # Automatic Home-Hero rotation/loading was removed.  Only an explicit
        # Set as Home Hero action may change the single persistent slot.
        return

    def refresh_hero(self):
        try:self["status"].setText(_("Home Hero changes only from Movie/Series Details MENU"))
        except Exception:pass

    def _show_refresh_notice(self,message):
        self._refresh_notice_visible=True
        self["refresh_notice_title"].setText(_("Refresh Content"))
        self["refresh_notice_message"].setText(str(message or _("Done")))
        for name in ("refresh_notice_panel","refresh_notice_title","refresh_notice_message"):
            try:self[name].show()
            except Exception:pass
        try:self["actions"].setEnabled(False)
        except Exception:pass
        try:self["refresh_notice_actions"].setEnabled(True)
        except Exception:pass

    def _hide_refresh_notice(self):
        self._refresh_notice_visible=False
        for name in ("refresh_notice_panel","refresh_notice_title","refresh_notice_message"):
            try:self[name].hide()
            except Exception:pass
        try:self["refresh_notice_actions"].setEnabled(False)
        except Exception:pass
        try:self["actions"].setEnabled(True)
        except Exception:pass

    def _home_expiry_text(self, expiry=None):
        value=str(expiry or self.profile.get("expiry") or "").strip()
        if not value:
            raw=str(self.profile.get("state") or "").strip()
            if "/" in raw:
                value=raw.split("/",2)[1].strip()
        return _clean_display_text(value,64) if value else ""

    def _set_home_account_line(self, expiry=None):
        """Keep Home connection state visually stable and split-color only CONNECTED."""
        try:self["portal_state"].setText("●")
        except Exception:pass
        try:self["portal_connected"].setText(_("Connected"));self["portal_connected"].show()
        except Exception:pass
        expiry_text=self._home_expiry_text(expiry)
        try:
            # Never mix RTL wording and the LTR date inside one Label.  The
            # prefix/value split is direction-safe for every interface language.
            prefix=((_("Expires: %s") % "").strip()) if expiry_text else ""
            self["portal_expiry_prefix"].setText(("|  "+prefix) if prefix else "")
            self["portal_expiry"].setText(expiry_text if expiry_text else "")
            self["portal_expiry_prefix"].show();self["portal_expiry"].show()
            self._fit_home_account_row()
        except Exception:pass

    def refresh_current_content(self):
        """Explicit GREEN refresh for the current Portal/Xtream/M3U source only."""
        if getattr(self,"_content_refresh_running",False) or getattr(self,"_refresh_notice_visible",False):
            return
        if getattr(self,"_busy",False):
            try:self["status"].setText(_("Please wait"))
            except Exception:pass
            return
        profile=dict(self.profile or {})
        client=self.client
        self._content_refresh_running=True
        self._set_home_account_line()
        def work(handle):
            method=getattr(client,"refresh_content",None)
            if not callable(method):
                raise RuntimeError(_("This source does not support content refresh."))
            result=method(cancel_event=handle.cancel_event)
            if handle.cancelled():
                raise RuntimeError(_("Content refresh cancelled"))
            try:
                if callable(_invalidate_source_navigation_cache):
                    _invalidate_source_navigation_cache(profile)
            except Exception as exc:
                optional_failure("ui.home_content_refresh_navigation_cache",exc)
            return result or {}
        def done(result):
            self._content_refresh_running=False
            message=content_refresh_feedback(result, _)
            self._set_home_account_line()
            try:self["status"].setText(message)
            except Exception:pass
            self._show_refresh_notice(message)
        def failed(exc):
            self._content_refresh_running=False
            message=content_refresh_failure(exc, _)
            self._set_home_account_line()
            try:self["status"].setText(message)
            except Exception:pass
            self._show_refresh_notice(message)
        handle=self._run_async(work,done,failed)
        if not handle:
            self._content_refresh_running=False
            self._set_home_account_line()

    def _queue_home_focus_state(self, **updates):
        self._home_focus_state_pending=dict(updates or {})
        try:self._home_focus_state_timer.stop();self._home_focus_state_timer.start(250,True)
        except Exception:self._flush_home_focus_state()

    def _flush_home_focus_state(self):
        pending=getattr(self,"_home_focus_state_pending",None)
        self._home_focus_state_pending=None
        if not pending:return
        try:save_ui_state(self.profile,**pending)
        except Exception as exc:optional_failure("ui.home_focus_state_flush",exc)

    def _stop_home_focus_state_timer(self):
        try:self._home_focus_state_timer.stop()
        except Exception as exc:optional_failure("ui.home_focus_state_stop",exc)
        try:
            if self._home_focus_state_conn is not None:self._home_focus_state_conn.disconnect()
        except Exception as exc:optional_failure("ui.home_focus_state_disconnect",exc)
        try:
            if self._flush_home_focus_state in self._home_focus_state_timer.callback:self._home_focus_state_timer.callback.remove(self._flush_home_focus_state)
        except Exception as exc:optional_failure("ui.home_focus_state_callback",exc)

    def _update_focus(self):
        try:
            sx=float(getattr(self,"_home_sx",1.0) or 1.0);sy=float(getattr(self,"_home_sy",1.0) or 1.0)
            if self.focus_row=="recent":
                self["selection"].hide();self._set_recent_row_focus(self.recent_index);self._home_focus_drawn_row="recent"
                group=self._recent_group(self.recent_index);self._bind_recent_group_art(group,self.recent_index)
                try:
                    _packed=self._recent_entries[self.recent_index] if self.recent_index<len(self._recent_entries) else None
                    label=self._recent_row_text(self.recent_index,_packed)[0] or _("Recent")
                except Exception:label=_("Recent")
                self._queue_home_focus_state(home_index=self.index,home_recent_index=self.recent_index,home_focus_row=self.focus_row,home_recent_grid_v1=1)
                self["status"].setText(_("%s • LEFT / RIGHT section • UP / DOWN item • OK resume")%label)
            else:
                self._set_recent_row_focus(None);self["selection"].show();self._home_focus_drawn_row="menu"
                self["selection"].instance.move(ePoint(int((self.CARD_X[self.index])*sx),int(532*sy)))
                action=self.CARDS[self.index][2]
                self._queue_home_focus_state(home_key=action,home_index=self.index,home_recent_index=self.recent_index,home_focus_row=self.focus_row,home_recent_grid_v1=1)
                self["status"].setText(_("%s • DOWN recent • OK open • BACK portals")%_(self.CARDS[self.index][0]))
        except Exception as exc:optional_failure("ui",exc)

    def move_left(self):
        if self.focus_row=="recent":
            group=self._recent_group(self.recent_index);row=self._recent_row(self.recent_index)
            target=max(0,group-1)*3+row
            if target==self.recent_index:return
            self.recent_index=target
        else:
            target=max(0,self.index-1)
            if target==self.index:return
            self.index=target
        self._update_focus()
    def move_right(self):
        if self.focus_row=="recent":
            group=self._recent_group(self.recent_index);row=self._recent_row(self.recent_index)
            target=min(2,group+1)*3+row
            if target==self.recent_index:return
            self.recent_index=target
        else:
            target=min(len(self.CARDS)-1,self.index+1)
            if target==self.index:return
            self.index=target
        self._update_focus()
    def move_down(self):
        if self.focus_row=="menu":
            self.focus_row="recent";group=min(2,max(0,int(round(self.index*2.0/max(1,len(self.CARDS)-1)))));self.recent_index=group*3;self._update_focus();return
        row=self._recent_row(self.recent_index)
        if row<2:self.recent_index+=1;self._update_focus()
    def move_up(self):
        if self.focus_row!="recent":return
        row=self._recent_row(self.recent_index);group=self._recent_group(self.recent_index)
        if row>0:self.recent_index-=1;self._update_focus();return
        self.focus_row="menu";self.index=min(len(self.CARDS)-1,int(round(group*(len(self.CARDS)-1)/2.0)));self._update_focus()

    def _current_category_adaptive_source(self):
        """Return the exact Home hero artwork currently on screen.

        Categories must not guess a different palette by media type.  The first
        working Series implementation inherited the visible Home hero palette;
        pass that same concrete source to Series, Movies and Live so all three
        execute one identical adaptive pipeline.
        """
        hero = self._hero_data if isinstance(getattr(self, "_hero_data", None), dict) else None
        if not hero:
            try: hero = self._read_hero_cache()
            except Exception: hero = None
        if not isinstance(hero, dict):
            return None
        for key in ("display_backdrop_local", "backdrop_local", "prepared", "display_poster_local", "poster_local"):
            src = hero.get(key)
            try:
                if src and os.path.isfile(str(src)) and os.path.getsize(str(src)) > 4096:
                    return str(src)
            except Exception as exc:
                optional_failure("ui.silent_guard",exc)
        return None

    def open_selected(self):
        if self.focus_row=="recent":return self._open_recent(self.recent_index)
        action=self.CARDS[self.index][2]
        self._mem34_last_action=action
        self._perf59_cycle=int(getattr(self,"_perf59_cycle",0) or 0)+1
        if LOG is not None:LOG.info("PERF59 nav_open cycle=%d action=%s",self._perf59_cycle,str(action))
        _mem34("home_before_open", action=action, cycle=self._perf59_cycle)
        _perf29_t0=time.monotonic()
        if LOG is not None: LOG.info("PERF29 home card_open_begin action=%s mono_ms=%d",action,int(_perf29_t0*1000))
        if action=="search":self.session.open(PortalGlobalSearchScreen,self.profile,self.client)
        elif action=="settings":self.session.open(NovaSettingsScreen,self.profile,self.client)
        else:self.session.open(PortalBrowserScreen,self.profile,action,self.client,self._current_category_adaptive_source())
        if LOG is not None: LOG.info("PERF29 home card_open_return action=%s elapsed_ms=%d",action,int((time.monotonic()-_perf29_t0)*1000))
    def open_search_shortcut(self):
        """Home yellow-key shortcut: open the exact same global Search screen as the Search card.

        Do not mutate Home selection/focus and do not route through Menu. This keeps the
        shortcut visually and behaviorally identical to opening the Search card itself.
        """
        try:
            if PortalGlobalSearchScreen is not None:
                self.session.open(PortalGlobalSearchScreen,self.profile,self.client)
        except Exception as exc:
            optional_failure("ui.home_search_shortcut",exc)

    def open_settings(self):self.session.open(NovaSettingsScreen,self.profile,self.client)
    def open_account(self):self.session.open(PortalBrowserScreen,self.profile,"account",self.client,self._current_category_adaptive_source())

    @staticmethod
    def _recent_profile_key(profile):
        profile=profile if isinstance(profile,dict) else {}
        return (str(profile.get("portal") or "").rstrip("/").lower(),str(profile.get("mac") or "").upper())

    def _recent_visible_on_this_home(self,entry,item):
        """Show Recent/Continue rows owned by this exact portal only.

        Federated Search and in-player server handoff may play a copy from another
        configured source, but that source owns its own history/artwork.  Never
        surface a foreign Portal/Xtream row on the current Home merely because
        playback was launched while this Home happened to be open.
        """
        current=self._recent_profile_key(self.profile)
        source=(str((entry or {}).get("portal") or "").rstrip("/").lower(),str((entry or {}).get("mac") or "").upper())
        return bool(source==current)

    def _recent_source_profile(self,entry):
        """Resolve the actual history source without persisting credentials."""
        source=(str((entry or {}).get("portal") or "").rstrip("/").lower(),str((entry or {}).get("mac") or "").upper())
        if source==self._recent_profile_key(self.profile):return self.profile
        try:
            for row in load_profiles() or []:
                if self._recent_profile_key(row)==source:return row
        except Exception as exc:
            optional_failure("ui.home_recent_source_profile",exc)
        return None

    def _recent_for_this_portal(self):
        portal=str(self.profile.get("portal") or "").rstrip("/").lower();mac=str(self.profile.get("mac") or "").upper();out=[None,None,None]
        try:rows=load_recently_played() or []
        except Exception:rows=[]
        for entry in rows:
            if not isinstance(entry,dict):continue
            item=entry.get("item") if isinstance(entry.get("item"),dict) else entry
            if not self._recent_visible_on_this_home(entry,item):continue
            item=item;mtype=str(entry.get("media_type") or item.get("_saved_media_type") or "").lower()
            slot=0 if mtype in ("itv","live") else (1 if mtype=="vod" else (2 if mtype in ("series","episode") else -1))
            if slot>=0 and out[slot] is None:out[slot]=(entry,item,mtype)
            if all(out):break
        return out

    @staticmethod
    def _recent_series_identity(item,mtype):
        """Return one stable *show* identity for Home Recent-Series de-duplication.

        Episode history is intentionally keyed per episode for resume.  Home is
        different: its three Series slots represent three shows, so seasons and
        episodes from the same parent series must collapse to the newest row.
        Prefer an explicit series TMDb id, then the provider parent-series id,
        and use a normalized parent title only when the source exposes no ids.
        """
        item=item if isinstance(item,dict) else {}
        mt=str(mtype or "").lower()
        tmdb_keys=("_series_tmdb_id","series_tmdb_id","tv_tmdb_id")
        if mt=="series":tmdb_keys=tmdb_keys+("_locked_tmdb_id","tmdb_id")
        for key in tmdb_keys:
            value=item.get(key)
            if value not in (None,""):return "tmdb:%s"%str(value).strip()
        provider_keys=("_series_id","series_id","series_uid","parent_id","series") if mt=="episode" else ("series_id","id","series_uid","_series_id")
        for key in provider_keys:
            value=item.get(key)
            if value not in (None,""):return "provider:%s"%str(value).strip()
        if mt=="episode":raw=item.get("_series_title") or item.get("series_title") or item.get("series_name") or ""
        else:raw=item.get("original_name") or item.get("original_title") or item.get("name") or item.get("title") or ""
        title=re.sub(r"[^\w\u0600-\u06ff]+"," ",str(raw or "").casefold(),flags=re.UNICODE)
        title=re.sub(r"\s+"," ",title).strip()
        return "title:%s"%title if title else ""

    def _continue_for_this_portal(self):
        """Return three Live, three Movie and three *different-show* Series rows.

        The history query is already newest-first.  For Series we therefore
        keep the first row for each show identity, which naturally means the
        latest season/episode progress wins.  Home remains a pure local
        SQLite/cache consumer: no portal, TMDb or artwork network work happens
        here.
        """
        portal=str(self.profile.get("portal") or "").rstrip("/").lower();mac=str(self.profile.get("mac") or "").upper()
        groups=[[],[],[]];seen_series=set()
        try: recent=load_recently_played() or []
        except Exception: recent=[]
        for entry in recent:
            if not isinstance(entry,dict):continue
            item=entry.get("item") if isinstance(entry.get("item"),dict) else entry
            if not self._recent_visible_on_this_home(entry,item):continue
            mtype=str(entry.get("media_type") or item.get("_saved_media_type") or "").lower()
            group=0 if mtype in ("itv","live") else (1 if mtype=="vod" else (2 if mtype in ("series","episode") else -1))
            if group<0:continue
            if group==2:
                identity=self._recent_series_identity(item,mtype)
                if identity and identity in seen_series:continue
                if identity:seen_series.add(identity)
            if len(groups[group])<3:groups[group].append((entry,item,mtype))
            if all(len(rows)>=3 for rows in groups):break
        for rows in groups:
            while len(rows)<3:rows.append(None)
        return groups[0]+groups[1]+groups[2]

    @staticmethod
    def _recent_group(index):
        try:return max(0,min(2,int(index)//3))
        except Exception:return 0

    @staticmethod
    def _recent_row(index):
        try:return max(0,min(2,int(index)%3))
        except Exception:return 0

    @staticmethod
    def _recent_group_range(group):
        base=max(0,min(2,int(group)))*3
        return range(base,base+3)

    @staticmethod
    def _recent_placeholder(group):
        return ("us168_live_placeholder_220x132.png","grid_placeholder_movie_921.png","grid_placeholder_series_921.png")[max(0,min(2,int(group)))]

    def _recent_list_entry(self,index,selected=False):
        try:index=max(0,min(8,int(index)))
        except Exception:index=0
        packed=self._recent_entries[index] if index<len(self._recent_entries) else None
        title,meta,pct=self._recent_row_text(index,packed)
        details={
            "selected":bool(selected),
            "row_asset":self._recent_row_normal_path,
            "row_selected_asset":self._recent_row_selected_path,
            "meta":str(meta or "")[:46],
            "progress":max(0,min(100,int(pct or 0))),
            "progress_laser":self._recent_progress_paths[index] if index<len(self._recent_progress_paths) else "",
            "show_progress":self._recent_group(index)!=0 and int(pct or 0)>0,
        }
        return (title,None,packed,details)

    def _set_recent_row_focus(self,index):
        """Invalidate at most two MultiContent entries; no Pixmap widget swapping."""
        try:new=-1 if index is None else max(0,min(8,int(index)))
        except Exception:new=-1
        try:old=int(getattr(self,"_recent_focus_drawn",-1))
        except Exception:old=-1
        if old==new:return
        for absolute,selected in ((old,False),(new,True)):
            if not (0<=absolute<9):continue
            group=self._recent_group(absolute);row=self._recent_row(absolute)
            try:self["recent_list%d"%group].update_icon_row(row,self._recent_list_entry(absolute,selected))
            except Exception as exc:optional_failure("ui.home_recent_focus",exc)
        self._recent_focus_drawn=new

    def _bind_recent_group_art(self,group,index=None):
        """Bind one already-cached art file for the group; never create/download art.

        Empty focus rows no longer hide/show the group art.  The lower Home card
        is a resident surface: while at least one row in that group has content,
        keep a valid artwork bound exactly like the fixed cards above it.
        """
        group=max(0,min(2,int(group)))
        chosen=None
        candidates=[]
        if index is not None and self._recent_group(index)==group:
            selected=int(index)
            if selected<len(self._recent_entries) and self._recent_entries[selected]:
                candidates.append(selected)
        candidates.extend(i for i in self._recent_group_range(group) if i not in candidates)
        for i in candidates:
            if i>=len(self._recent_entries):continue
            packed=self._recent_entries[i]
            path=self._recent_art_paths[i] if i<len(self._recent_art_paths) else None
            if packed and path and os.path.isfile(str(path)):
                chosen=(i,str(path));break
        if chosen is None:
            # Empty recent slots stay genuinely empty. Do not synthesize a
            # group icon/placeholder merely because the panel exists.
            self._recent_group_art_paths[group]=""
            try:self["recent_art%d"%group].hide()
            except Exception as exc:optional_failure("ui.home_recent_art_empty",exc)
            return
        path=chosen[1]
        if group==0:
            # Live Recent artwork is never bound from a raw provider image.
            # A raw picon can be re-expanded by ePixmap after returning from a
            # child Player screen even when the widget geometry is still 220x132.
            # Always bind the persistent aspect-safe 220x132 transparent canvas.
            try:
                placeholder=asset("us168_live_placeholder_220x132.png")
                already_fitted=("%slive_picon_fit%s"%(os.sep,os.sep)) in os.path.abspath(path) and str(path).endswith("_220x132.png")
                if not already_fitted and os.path.basename(str(path))!=os.path.basename(str(placeholder)):
                    fitted=_fit_live_picon_canvas(str(path),PERSISTENT_GENERATED_DIR,(220,132)) if callable(_fit_live_picon_canvas) else ""
                    path=str(fitted or placeholder)
                    try:self._recent_art_paths[chosen[0]]=path
                    except Exception:pass
            except Exception as exc:
                optional_failure("ui.home_recent_live_hard_fit",exc)
                path=asset("us168_live_placeholder_220x132.png")
        # Home stays resident underneath child screens.  Some OE-A images can
        # leave a Pixmap instance with the geometry from a previously painted
        # surface after returning from fullscreen playback.  Re-assert the
        # native Recent-card geometry on every bind, even when the artwork path
        # itself did not change.  Live is therefore always hard-clamped to the
        # intended 220x132 slot instead of occasionally expanding on return.
        self._set_recent_art_geometry(group)
        if self._recent_group_art_paths[group]==path:
            try:
                inst=self["recent_art%d"%group].instance
                if group==0 and inst is not None:inst.setPixmapFromFile(path)
                self["recent_art%d"%group].show()
            except Exception:pass
            return
        self._recent_group_art_paths[group]=path
        try:
            inst=self["recent_art%d"%group].instance
            if inst is not None:inst.setPixmapFromFile(path)
            self["recent_art%d"%group].show()
        except Exception as exc:optional_failure("ui.home_recent_art_bind",exc)

    @staticmethod
    def _recent_number(value):
        if value in (None,""):return ""
        try:return str(int(value))
        except Exception:return str(value).strip()

    def _recent_row_text(self,index,packed):
        group=self._recent_group(index)
        if not packed:
            return "", "", 0
        entry,item,mtype=packed;item=item if isinstance(item,dict) else {}
        clean=bool(getattr(self,"_home_clean_titles",True))
        raw=item.get("_raw_name") or item.get("name") or item.get("title") or item.get("episode_name") or _("Recent")
        title=premium_title(raw,True) if clean else str(raw).strip()
        if group==2 and mtype=="episode":
            series_raw=item.get("_series_title") or item.get("series_title") or item.get("series_name") or ""
            if series_raw:title=premium_title(series_raw,True) if clean else str(series_raw).strip()
        title=_clean_display_text(title,38)
        if group==0:return title, _("Live TV  •  reopen channel"), 0
        pos=max(0,int(entry.get("_position") or 0)) if isinstance(entry,dict) else 0
        dur=max(0,int(entry.get("_duration") or 0)) if isinstance(entry,dict) else 0
        completed=bool(entry.get("_completed")) if isinstance(entry,dict) else False
        pct=min(100,int(pos*100.0/dur)) if pos and dur else (100 if completed else 0)
        if group==1:
            meta=_("Watched  •  play again") if completed else (_("Continue watching  •  %d%%")%pct if pct else _("Movie  •  open"))
            return title,meta,pct
        season=self._recent_number(item.get("_season_number") or item.get("season") or item.get("season_num") or item.get("season_number") or item.get("season_id"))
        episode=self._recent_number(item.get("episode_num") or item.get("episode") or item.get("number") or item.get("episode_number"))
        if mtype=="episode":
            bits=[]
            if season:bits.append("S%02d"%int(season) if season.isdigit() else "S%s"%season)
            if episode:bits.append("E%02d"%int(episode) if episode.isdigit() else "E%s"%episode)
            if pct:bits.append("%d%%"%pct)
            elif completed:bits.append("100%")
            meta="  •  ".join(bits) or _("Episode  •  open")
        else:meta=_("Series  •  open")
        return title,meta,pct

    def _set_recent_art_geometry(self, slot):
        """Restore the native media-art geometry for a populated recent panel."""
        normal=((60,836,220,132),(697,766,190,272),(1319,766,190,272))
        try:
            x,y,w,h=normal[int(slot)]
            desk=getDesktop(0).size();sx=float(desk.width())/1920.0;sy=float(desk.height())/1080.0
            inst=self["recent_art%d"%int(slot)].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                inst.resize(eSize(int(round(w*sx)),int(round(h*sy))))
                try:inst.setScale(1)
                except Exception:pass
        except Exception as exc:optional_failure("ui.home_recent_geometry",exc)



    def _load_recent_cards(self,initial=False):
        """Fill the three resident Recent panels from SQLite/local artwork only.

        ``initial`` means first-paint preparation.  Later history refreshes reuse
        the already-instantiated MultiContent lists and invalidate entries in
        place instead of clearing/recreating the three lower cards.
        """
        self["continue_header"].setText(_("CONTINUE / RECENT"))
        self._cancel_recent_futures()
        self._recent_generation=int(getattr(self,"_recent_generation",0) or 0)+1
        self._recent_entries=self._continue_for_this_portal();self._recent_art_paths=[None]*9;self._recent_progress_paths=[None]*9
        _warm_recent=False;_warm_art={};_warm_progress={}
        try:
            _warm_recent=bool(splash_home_is_warm and splash_home_is_warm(self.profile))
            if _warm_recent and callable(splash_home_recent_assets):_warm_art,_warm_progress=splash_home_recent_assets(self.profile)
        except Exception:
            _warm_recent=False;_warm_art={};_warm_progress={}
        # Artwork lookup remains HDD/RAM-only. No network job is launched here.
        # Live picons are special: the Home slot is 220x132 but the source must
        # never be stretched/upscaled to fill it.  Start with the lightweight
        # placeholder, then fit the already-cached local picon on the shared
        # image worker.  This keeps Pillow and file generation off the GUI thread.
        for i in range(9):
            packed=self._recent_entries[i] if i<len(self._recent_entries) else None
            if not packed:continue
            _entry,item,mtype=packed;group=self._recent_group(i)
            size=(220,132) if group==0 else (190,272);ph=self._recent_placeholder(group)
            # Movies/Series use the exact Cinematic laser renderer. Generation
            # is background-only and persistent-cache backed: a percentage is
            # rendered once, then every later Home paint reuses the PNG.
            if _warm_recent:
                _pre=str(_warm_progress.get(i) or _warm_progress.get(str(i)) or "")
                if _pre and os.path.isfile(_pre):self._recent_progress_paths[i]=_pre
            elif group in (1,2) and _IMAGE_EXECUTOR is not None:
                try:
                    _title,_meta,_pct=self._recent_row_text(i,packed)
                    _pct=max(0,min(100,int(_pct or 0)))
                except Exception:
                    _pct=0
                if _pct>0:
                    generation=int(self._recent_generation);index=int(i);pct=int(_pct)
                    def _laser_work(_pct=pct):
                        try:
                            from .ui_cinematic_global import _cinematic_row_progress_frame
                            return _cinematic_row_progress_frame("#5fc49a",_pct,width=277) or ""
                        except Exception as exc:
                            optional_failure("ui.home_recent_laser_build",exc);return ""
                    future=_IMAGE_EXECUTOR.submit(_laser_work)
                    self._recent_futures.add(future)
                    def _laser_done(_future,_index=index,_generation=generation):
                        try:path=_future.result() or ""
                        except Exception as exc:
                            optional_failure("ui.home_recent_laser_future",exc);path=""
                        try:self._recent_futures.discard(_future)
                        except Exception:pass
                        if path:self._home_jobs.put(("recent_progress_laser",_generation,_index,str(path)))
                    future.add_done_callback(_laser_done)
            try:
                _pre_art=str(_warm_art.get(i) or _warm_art.get(str(i)) or "") if _warm_recent else ""
                if _pre_art and os.path.isfile(_pre_art):
                    self._recent_art_paths[i]=_pre_art
                    continue
                # Search may have played a copy from another configured source.
                # Use that source's portal/MAC only for local artwork cache keys;
                # this adds no network work and no profile-file read on Home paint.
                _art_profile=self.profile
                try:
                    _src_portal=str((_entry or {}).get("portal") or "")
                    _src_mac=str((_entry or {}).get("mac") or "")
                    if _src_portal:_art_profile={"portal":_src_portal,"mac":_src_mac}
                except Exception:pass
                cached=_home_cached_art(item,_art_profile,size,ph,mtype)
                if _warm_recent:
                    self._recent_art_paths[i]=str(cached or asset(ph))
                elif group==0 and cached and os.path.isfile(str(cached)) and os.path.basename(str(cached))!=os.path.basename(str(asset(ph))):
                    self._recent_art_paths[i]=asset(ph)
                    generation=int(self._recent_generation)
                    if _IMAGE_EXECUTOR is not None and callable(_fit_live_picon_canvas):
                        future=_IMAGE_EXECUTOR.submit(_fit_live_picon_canvas,str(cached),PERSISTENT_GENERATED_DIR,(220,132))
                        self._recent_futures.add(future)
                        def _done(_future,_index=i,_generation=generation):
                            try:
                                path=_future.result() or ""
                            except Exception as exc:
                                optional_failure("ui.home_recent_live_fit",exc);path=""
                            try:self._recent_futures.discard(_future)
                            except Exception:pass
                            if path:self._home_jobs.put(("recent_live_fit",_generation,_index,str(path)))
                        future.add_done_callback(_done)
                    else:
                        self._recent_art_paths[i]=str(cached)
                else:
                    self._recent_art_paths[i]=cached
            except Exception as exc:
                optional_failure("ui.home_recent_local_cache",exc);self._recent_art_paths[i]=asset(ph)
        focused=self.recent_index if self.focus_row=="recent" else -1
        first_bind=bool(initial or not getattr(self,"_recent_lists_initialized",False))
        for group in range(3):
            rows=[]
            for row in range(3):
                absolute=group*3+row
                rows.append(self._recent_list_entry(absolute,absolute==focused))
            try:
                if first_bind:
                    self["recent_list%d"%group].set_icon_rows(rows)
                else:
                    # Keep the native list resident. Per-row invalidation avoids
                    # the whole lower panel briefly disappearing on history refresh.
                    for row,entry in enumerate(rows):
                        self["recent_list%d"%group].update_icon_row(row,entry)
            except Exception as exc:optional_failure("ui.home_recent_list_bind",exc)
            preferred=self.recent_index if self.focus_row=="recent" and self._recent_group(self.recent_index)==group else group*3
            self._bind_recent_group_art(group,preferred)
        self._recent_lists_initialized=True
        self._recent_snapshot_ready=True
        self._recent_focus_drawn=focused
        try:self._recent_render_revision=int(history_revision()) if callable(history_revision) else int(getattr(self,"_recent_render_revision",-1))
        except Exception:pass

    @staticmethod
    def _home_live_category_id(item):
        item=item if isinstance(item,dict) else {}
        for key in ("_live_category_id","tv_genre_id","genre_id","category_id","genre"):
            value=item.get(key)
            if value not in (None,""):
                return str(value)
        return ""

    @staticmethod
    def _home_live_same_channel(left,right):
        left=left if isinstance(left,dict) else {};right=right if isinstance(right,dict) else {}
        for key in ("id","ch_id","stream_id"):
            a=left.get(key);b=right.get(key)
            if a not in (None,"") and b not in (None,"") and str(a)==str(b):
                return True
        def command(row):
            value=str(row.get("cmd") or row.get("command") or row.get("url") or "").strip().strip('"').strip("'")
            low=value.lower()
            for prefix in ("ffmpeg ","auto "):
                if low.startswith(prefix):
                    value=value[len(prefix):].strip();low=value.lower()
            return value
        a=command(left);b=command(right)
        if a and b and a==b:return True
        an=str(left.get("name") or left.get("title") or "").strip().casefold()
        bn=str(right.get("name") or right.get("title") or "").strip().casefold()
        return bool(an and bn and an==bn)

    @staticmethod
    def _home_live_category_row_id(row):
        row=row if isinstance(row,dict) else {}
        for key in ("id","genre_id","category_id","genre"):
            value=row.get(key)
            if value not in (None,""):return str(value)
        return ""

    @staticmethod
    def _home_live_category_row_title(row):
        row=row if isinstance(row,dict) else {}
        return str(row.get("title") or row.get("name") or row.get("genre_name") or row.get("category_name") or "").strip()

    def _home_live_context(self,item):
        """Resolve the saved Home Live card back to its real provider folder.

        The player drawer needs the same folder snapshot/page metadata that the
        normal Live grid supplies.  History stores only the played row, so rebuild
        that context from the provider catalogue in the background.  No playback
        or OK/BACK behavior is changed here; this only restores the missing data.
        """
        target=dict(item or {})
        category_id=self._home_live_category_id(target)
        category_title=str(target.get("_live_folder_title") or target.get("tv_genre_name") or target.get("genre_name") or target.get("category_name") or "").strip()
        # Categories are intentionally lazy for normal Home reopen.  The Player
        # drawer already knows how to request them only when BACK is pressed, so
        # reopening a channel must not wait on an unrelated category request.
        categories=[]

        # Older history entries predate the saved folder marker.  A native Live
        # search is the cheap compatibility bridge and usually returns tv_genre_id.
        if not category_id:
            query=str(target.get("name") or target.get("title") or "").strip()
            if query:
                try:
                    matches=self.client.search_content_fast(query,media_types=("itv",),limit=24,max_pages=8,time_budget=4) or []
                except Exception:matches=[]
                for row in matches:
                    if self._home_live_same_channel(row,target):
                        category_id=self._home_live_category_id(row)
                        if category_id:
                            target.update(row);break

        def scan_folder(cid,deadline):
            page=1
            while page<=80 and time.monotonic()<deadline:
                try:payload=self.client.ordered_page("itv",cid,page) or {}
                except Exception:break
                rows=[dict(x) for x in (payload.get("items") or []) if isinstance(x,dict)]
                try:page_size=max(1,int(payload.get("page_size") or len(rows) or 1))
                except Exception:page_size=max(1,len(rows) or 1)
                try:total=max(len(rows),int(payload.get("total") or len(rows)))
                except Exception:total=len(rows)
                for idx,row in enumerate(rows):
                    if self._home_live_same_channel(row,target):
                        return {
                            "channels":rows,"absolute_index":(page-1)*page_size+idx,
                            "total":total,"page_size":page_size,"page":page,
                        }
                if not rows:break
                if total and page*page_size>=total:break
                if not total and len(rows)<page_size:break
                page+=1
            return None

        context=None
        if category_id:
            context=scan_folder(category_id,time.monotonic()+5.0)

        # Last compatibility bridge for very old rows whose provider search does
        # not expose a category marker.  Keep it bounded so Home never becomes a
        # portal-wide crawler merely to reopen one recent channel.
        if context is None and not category_id:
            try:categories=[dict(x) for x in (self.client.genres("itv") or []) if isinstance(x,dict)]
            except Exception:categories=[]
            deadline=time.monotonic()+5.0
            for cat in categories[:32]:
                if time.monotonic()>=deadline:break
                cid=self._home_live_category_row_id(cat)
                if not cid:continue
                found=scan_folder(cid,deadline)
                if found is not None:
                    category_id=cid;category_title=self._home_live_category_row_title(cat);context=found;break

        if context is None:
            context={"channels":[target],"absolute_index":0,"total":1,"page_size":1,"page":1}
        context["category_id"]=str(category_id or "")
        context["category_title"]=category_title or _("Live TV")
        context["categories"]=categories
        return context

    def _home_live_page_loader(self,category_id):
        client=self.client;cid=str(category_id or "")
        if not cid:return None
        def loader(page):
            try:data=client.ordered_page("itv",cid,max(1,int(page or 1))) or {}
            except Exception:return []
            return [dict(x) for x in (data.get("items") or []) if isinstance(x,dict)]
        return loader

    def _open_recent(self,slot):
        packed=self._recent_entries[slot] if 0<=slot<len(self._recent_entries) else None
        if not packed:self["status"].setText(_("Nothing has been played in this section yet"));return
        entry,item,mtype=packed
        source_profile=self._recent_source_profile(entry)
        if not isinstance(source_profile,dict):
            self["status"].setText(_("No stream command"));return
        source_owned=self._recent_profile_key(source_profile)!=self._recent_profile_key(self.profile)
        source_client=self.client
        if source_owned:
            try:
                source_client=_client_from_profile(source_profile,timeout=min(8,int(load_settings().get("timeout",10) or 10)))
            except Exception as exc:
                optional_failure("ui.home_recent_source_client",exc)
                self["status"].setText(_("No stream command"));return
        def close_source_client():
            if source_owned and source_client is not None:
                try:source_client.close()
                except Exception:pass
        if mtype=="series":
            def series_closed(result=None):
                close_source_client();self._home_shown()
            self.session.openWithCallback(series_closed,ContentDetailsScreen,source_profile,source_client,"series",item);return
        command=item.get("cmd") or item.get("command") or item.get("url")
        if not command:
            close_source_client();self["status"].setText(_("Saved item has no stream command"));return
        is_live=mtype in ("itv","live")
        self["status"].setText(_("Opening %s...")%(_("channel") if is_live else _("saved item")))
        episode_id=item.get("id") or item.get("episode_id") or item.get("series") or item.get("number")
        def work():
            if mtype=="episode":return {"url":source_client.create_link(item,"series",episode_id),"live_context":None}
            if is_live:
                # Search currently covers Movies/Series only; native Live Recent
                # therefore remains on the active Home client/context path.
                context=self._home_live_context(item)
                url=source_client.create_link(item,"itv")
                return {"url":url,"live_context":context}
            return {"url":source_client.create_link(item,"vod"),"live_context":None}
        def ok(result):
            result=result if isinstance(result,dict) else {"url":result,"live_context":None}
            url=result.get("url") if isinstance(result.get("url"),str) else "";live_context=result.get("live_context") if isinstance(result.get("live_context"),dict) else None
            if not str(url or "").strip():
                close_source_client();self["status"].setText(_("No stream command"));return
            name=str(item.get("name") or item.get("title") or item.get("episode_name") or _("Your saved content"))
            mt="itv" if is_live else mtype
            play_item=dict(item)
            if mt=="itv" and live_context:
                play_item["_live_category_id"]=str(live_context.get("category_id") or "")
                play_item["_live_folder_title"]=str(live_context.get("category_title") or _("Live TV"))
                play_item["_live_absolute_index"]=int(live_context.get("absolute_index") or 0)
                play_item["_live_folder_page"]=int(live_context.get("page") or 1)
                play_item["_live_page_size"]=int(live_context.get("page_size") or 1)
                add_recently_played(source_profile,"itv",play_item)
            engine=_configured_playback_engine()
            fallback=None
            payload=_player_payload(play_item,source_profile,fallback,mt)
            payload["_player_client_ref"]=source_client
            if mt=="itv":payload["_live_client_ref"]=source_client
            if mt=="itv" and live_context:
                channels=[dict(x) for x in (live_context.get("channels") or []) if isinstance(x,dict)]
                payload["_live_folder_channels"]=channels
                payload["_live_absolute_index"]=int(live_context.get("absolute_index") or 0)
                payload["_live_folder_total"]=int(live_context.get("total") or len(channels))
                payload["_live_page_size"]=max(1,int(live_context.get("page_size") or len(channels) or 1))
                payload["_live_folder_page"]=max(1,int(live_context.get("page") or 1))
                payload["_live_folder_title"]=str(live_context.get("category_title") or _("Live TV"))
                payload["_live_category_id"]=str(live_context.get("category_id") or "")
                payload["_live_categories"]=[dict(x) for x in (live_context.get("categories") or []) if isinstance(x,dict)]
                payload["_live_page_loader"]=self._home_live_page_loader(live_context.get("category_id"))
                payload["_live_client_ref"]=source_client
            self.session.openWithCallback(lambda result=None:close_source_client(),UltraStalkerPlayer,url.strip(),name,mt,engine,payload)
        def fail(error):
            close_source_client();self["status"].setText(_("No stream command"))
        self._run_async(work,ok,fail)

    def _read_hero_state(self):
        try:
            if not HOME_HERO_FILE or not os.path.isfile(HOME_HERO_FILE):return {}
            with open(HOME_HERO_FILE,"r",encoding="utf-8") as fh:data=json.load(fh)
            return data if isinstance(data,dict) else {}
        except Exception:
            return {}

    def _read_hero_cache(self):
        data=self._read_hero_state()
        if int(data.get("schema") or 0)!=HOME_HERO_SCHEMA:return None
        hero=data.get("hero") if isinstance(data.get("hero"),dict) else None
        if not hero:return None
        prepared=str(hero.get("prepared") or "")
        try:
            if prepared and os.path.isfile(prepared) and os.path.getsize(prepared)>1024:return hero
        except Exception:pass
        return None

    @staticmethod
    def _home_logo_identity(hero):
        hero=hero if isinstance(hero,dict) else {}
        return str(hero.get("id") or "")+"|"+str(hero.get("media_type") or "")+"|"+str(hero.get("tmdb_id") or "")+"|"+str(hero.get("manual_pin_token") or "")

    def _home_hide_title_logo(self):
        self._home_title_logo_path=""
        try:
            if self["hero_title_logo"].instance is not None:self["hero_title_logo"].instance.setPixmap(None)
            self["hero_title_logo"].hide()
        except Exception:pass

    @staticmethod
    def _home_logo_lang_from_path(path):
        try:
            match=re.search(r"_(ar|en)_\d+x\d+\.png$",os.path.basename(str(path or "")).lower())
            return match.group(1) if match else ""
        except Exception:
            return ""

    def _home_cancel_title_logo_job(self):
        event=getattr(self,"_home_title_logo_cancel_event",None)
        if event is not None:
            try:event.set()
            except Exception:pass
        future=getattr(self,"_home_title_logo_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        self._home_title_logo_future=None;self._home_title_logo_cancel_event=None

    def _home_persist_title_logo_authority(self,identity,path,resolved):
        """Persist the proven Hero logo + origin metadata for instant next boot."""
        if not (path and _home_valid_title_logo(path) and HOME_HERO_FILE):return
        resolved=resolved if isinstance(resolved,dict) else {}
        try:
            lock=_HOME_HERO_LOCK
            if lock is None:
                class _NoLock(object):
                    def __enter__(self):return self
                    def __exit__(self,*args):return False
                lock=_NoLock()
            with lock:
                with open(HOME_HERO_FILE,"r",encoding="utf-8") as fh:state=json.load(fh)
                hero=state.get("hero") if isinstance(state,dict) and isinstance(state.get("hero"),dict) else None
                if not hero or self._home_logo_identity(hero)!=identity:return
                hero["title_logo_local"]=str(path)
                lang=self._home_logo_lang_from_path(path)
                if lang:hero["title_logo_lang"]=lang
                for key in ("original_language","origin_country","countries","production_countries","country_hint"):
                    value=resolved.get(key)
                    if value not in (None,"",[],{}):hero[key]=value
                folder=os.path.dirname(HOME_HERO_FILE) or "/tmp"
                fd,tmp=tempfile.mkstemp(prefix=".hero-logo.",suffix=".tmp",dir=folder)
                try:
                    with os.fdopen(fd,"w",encoding="utf-8") as fh:
                        json.dump(state,fh,ensure_ascii=False,separators=(",",":"));fh.flush();os.fsync(fh.fileno())
                    os.chmod(tmp,0o600);os.replace(tmp,HOME_HERO_FILE)
                finally:
                    if os.path.exists(tmp):
                        try:os.unlink(tmp)
                        except OSError:pass
        except Exception as exc:
            try:optional_failure("ui.home_title_logo_persist",exc)
            except Exception:pass

    def _home_schedule_title_logo(self,hero,title,identity,token):
        """Resolve a missing persistent Hero logo off the Enigma2 GUI thread.

        This is intentionally one-shot per Hero identity.  The 432x210 widget
        geometry remains untouched; only the old cache-only transport is repaired.
        """
        if getattr(self,"_screen_closed",False):return
        if identity and identity==getattr(self,"_home_title_logo_checked_identity",""):return
        future=getattr(self,"_home_title_logo_future",None)
        if future is not None and not future.done() and identity==getattr(self,"_home_title_logo_identity",""):
            return
        self._home_cancel_title_logo_job()
        cancel_event=threading.Event();self._home_title_logo_cancel_event=cancel_event
        hero_snapshot=dict(hero or {});profile_snapshot=dict(self.profile or {})
        try:settings_snapshot=dict(load_settings() or {})
        except Exception:settings_snapshot={}
        media_type=str(hero_snapshot.get("media_type") or "vod")

        def worker():
            path="";resolved={}
            try:
                path,resolved=_home_resolve_ultra_title_logo(
                    profile_snapshot,media_type,hero_snapshot,hero_snapshot,
                    canvas_size=(432,210),visible_title=title,cancel_event=cancel_event,settings=settings_snapshot)
            except Exception as exc:
                try:optional_failure("ui.home_title_logo_resolve",exc)
                except Exception:pass
            if cancel_event.is_set() or getattr(self,"_screen_closed",False):return
            self._home_jobs.put(("title_logo",token,identity,str(path or ""),title,dict(resolved or {})))

        try:
            self._home_title_logo_future=_IMAGE_EXECUTOR.submit(worker)
            try:self._async_set_poll_interval(120)
            except Exception:pass
        except Exception as exc:
            self._home_title_logo_future=None;self._home_title_logo_cancel_event=None
            try:optional_failure("ui.home_title_logo_submit",exc)
            except Exception:pass

    def _home_apply_title_logo(self,hero):
        """Restore the historical Home Hero title-logo slot.

        The existing 432x210 widget at the historical skin position is retained.
        HDD cache/local Hero artwork wins immediately; if the Stage-5 cache was
        rotated or the Hero was pinned before a logo existed, Home starts one
        bounded background resolve and swaps text -> logo when ready.
        """
        hero=hero if isinstance(hero,dict) else {}
        title=_home_display_title(hero.get("title") or "")
        media_type=str(hero.get("media_type") or "vod")
        identity=self._home_logo_identity(hero)
        if identity!=getattr(self,"_home_title_logo_identity",""):
            self._home_cancel_title_logo_job()
            self._home_title_logo_identity=identity;self._home_title_logo_checked_identity=""
            self._home_title_logo_token=int(getattr(self,"_home_title_logo_token",0) or 0)+1
        elif not int(getattr(self,"_home_title_logo_token",0) or 0):
            self._home_title_logo_token=1
        token=int(self._home_title_logo_token)
        source=""
        try:
            # Stage-5-policy-aware shared cache first.
            source=_home_ultra_title_logo_cached(media_type,hero,hero,(432,210),title) or ""
            # A manually pinned Hero may already own the exact proven 432x210
            # file even when its older JSON lacks origin metadata.
            if not source:
                candidate=str(hero.get("title_logo_local") or "")
                base=os.path.basename(candidate).lower();real=os.path.realpath(candidate) if candidate else ""
                policy_cache=(os.sep+"ultra_v4"+os.sep) in real
                if policy_cache and _home_valid_title_logo(candidate) and ("432x210" in base or "_432_210" in base):source=candidate
        except Exception:source=""
        if source and _home_valid_title_logo(source):
            self._home_cancel_title_logo_job()
            try:
                if self["hero_title_logo"].instance is not None:
                    pix=home_boot_pixmap(source) if callable(home_boot_pixmap) else None
                    if pix is not None:self["hero_title_logo"].instance.setPixmap(pix)
                    else:self["hero_title_logo"].instance.setPixmapFromFile(source)
                self["hero_title_logo"].show();self._home_title_logo_path=source
                self["hero_title"].setText("");self["hero_title"].hide();return True
            except Exception:pass
        # Keep the familiar text fallback visible while the logo resolves; Home
        # first paint never waits on TMDb/Fanart.
        self._home_hide_title_logo()
        try:
            if title:self["hero_title"].setText(title);self["hero_title"].show()
            else:self["hero_title"].setText("");self["hero_title"].hide()
        except Exception:pass
        _warm=False
        try:_warm=bool(splash_home_is_warm and splash_home_is_warm(self.profile))
        except Exception:_warm=False
        if not _warm:self._home_schedule_title_logo(hero,title,identity,token)
        elif LOG is not None:LOG.info("R268 home title_logo resolve skipped reason=splash_warm")
        return False

    def _apply_home_hero(self,hero):
        if not isinstance(hero,dict):return
        self._hero_data=hero;overview=str(hero.get("overview") or _("No programme description"))
        try:self["status"].setText(_("%s • DOWN recent • YELLOW new hero • OK open • BACK portals")%_(self.CARDS[self.index][0]))
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        # R96: restore the historical Home title-logo slot from the unified
        # Ultra cache. Kicker/subtitle/rating stay hidden; the clean backdrop and
        # one title logo own the hero, with cleaned text only as a no-logo fallback.
        for _name in ("hero_kicker","hero_subtitle","hero_rating"):
            try:self[_name].setText("");self[_name].hide()
            except Exception as exc:optional_failure("ui.home_hero_copy_hide",exc)
        try:self._home_apply_title_logo(hero)
        except Exception as exc:optional_failure("ui.home_title_logo",exc)
        path=hero.get("prepared");raw=hero.get("backdrop_local");display_raw=hero.get("display_backdrop_local")
        try:
            # R171 Home fix: fixed green ambient/cards/focus are immutable skin
            # pixmaps.  Hero changes may replace only the upper Hero artwork.
            if path and os.path.isfile(path) and os.path.getsize(path) > 1024:
                try:
                    pix=home_boot_pixmap(path) if callable(home_boot_pixmap) else None
                    if pix is not None:self["hero_backdrop"].instance.setPixmap(pix)
                    else:self["hero_backdrop"].instance.setPixmapFromFile(path)
                    self["hero_backdrop"].show()
                    self._image_displayed_path = path
                except Exception:
                    self._decode_picture(path)
            elif display_raw and os.path.isfile(display_raw) and os.path.getsize(display_raw) > 1024:
                self._decode_picture(display_raw)
            elif raw and os.path.isfile(raw) and os.path.getsize(raw) > 1024:
                self._decode_picture(raw)
        except Exception as exc:optional_failure("ui.home_hero_art_apply",exc)

    def _load_home_hero(self,force=False,background_fill=False):
        # Explicit single-slot authority only.  No TMDb featured Hero, no local
        # Hero pool, no auto-download, no rotation and no background fill.
        try:
            if _cleanup_legacy_home_hero_storage is not None:
                _cleanup_legacy_home_hero_storage()
        except Exception as exc:optional_failure("ui.hero_legacy_cleanup_home",exc)
        hero=self._read_hero_cache()
        if hero:
            hero=self._ensure_home_visual_materials(dict(hero))
            self._hero_data=hero
            self._apply_home_hero(hero)
            self._rebind_home_materials(hero)
            return
        # No explicit selection means no dynamic Hero at all.  Keep the stock
        # Home chrome and never resurrect an old cached picture by accident.
        try:self["hero_backdrop"].hide()
        except Exception:pass
        # Fixed green Home chrome belongs to HOME_SKIN and stays visible even
        # when no explicit Hero is pinned.
        try:self._home_hide_title_logo();self["hero_title"].setText("");self["hero_title"].hide()
        except Exception:pass

    def _load_account_state(self):
        # R268: account lookup was already performed by the selected-portal
        # Splash warmup. Reuse it synchronously so no startup network job begins
        # after Home becomes visible.
        try:
            warm_info=splash_home_account_info(self.profile) if callable(splash_home_account_info) else {}
        except Exception:
            warm_info={}
        if isinstance(warm_info,dict) and warm_info:
            self._apply_home_account_info(warm_info,persist=False)
            return
        try:
            if splash_home_is_warm and splash_home_is_warm(self.profile):
                self._set_home_account_line(self.profile.get("expiry") or "")
                return
        except Exception:
            pass
        def work():return self.client.account_info()
        def ok(info):
            self._apply_home_account_info(info,persist=True)
        self._run_async(work,ok,lambda e:None)

    def _apply_home_account_info(self,info,persist=True):
        if not isinstance(info,dict):return
        expiry=info.get("phone") or info.get("end_date") or info.get("expire_billing_date") or ""
        state=info.get("status") or info.get("account_status") or "CONNECTED"
        self._set_home_account_line(expiry)
        try:
            self.profile["expiry"]=str(expiry or "")
            self.profile["account_state"]=str(state or "")
            if expiry:self.profile["state"]="%s / %s"%(str(state),str(expiry))
            if not persist:return
            profiles=load_profiles()
            target_portal=str(self.profile.get("portal") or "").rstrip("/").lower()
            target_mac=str(self.profile.get("mac") or "").upper()
            replaced=False
            for row in profiles:
                if (str(row.get("portal") or "").rstrip("/").lower()==target_portal and
                    str(row.get("mac") or "").upper()==target_mac):
                    row.update({"expiry":self.profile.get("expiry","") ,
                                "account_state":self.profile.get("account_state",""),
                                "state":self.profile.get("state","")})
                    replaced=True;break
            if not replaced:profiles.append(dict(self.profile))
            save_profiles(profiles)
        except Exception as exc:optional_failure("ui.home_account_persist",exc)

    def _drain_jobs(self):
        AsyncScreenMixin._drain_jobs(self)
        if self._screen_closed:return
        self._drain_image_jobs()
        while True:
            try:job=self._home_jobs.get_nowait()
            except queue.Empty:break
            if not job:continue
            if job[0]=="hero":
                # Ignore stale automatic Hero completions that were queued before
                # an explicit Details -> Set as Home Hero action completed.
                # Re-apply the authoritative pinned state instead.
                try:
                    state=self._read_hero_state() or {}
                    if bool(state.get("manual_pin")):
                        pinned=self._read_hero_cache()
                        if pinned:
                            self._apply_home_hero(pinned)
                            continue
                except Exception as exc:optional_failure("ui.home_manual_pin_queue_guard",exc)
                self._apply_home_hero(job[1])
            elif job[0]=="title_logo":
                _kind,token,identity,path,title=job[:5]
                resolved=job[5] if len(job)>5 and isinstance(job[5],dict) else {}
                try:
                    if int(token)!=int(getattr(self,"_home_title_logo_token",0) or 0):continue
                    if identity!=self._home_logo_identity(getattr(self,"_hero_data",None) or {}):continue
                    self._home_title_logo_future=None;self._home_title_logo_cancel_event=None
                    self._home_title_logo_checked_identity=identity
                    if path and _home_valid_title_logo(path):
                        self["hero_title_logo"].instance.setPixmapFromFile(path);self["hero_title_logo"].show();self._home_title_logo_path=path
                        self["hero_title"].setText("");self["hero_title"].hide()
                        # Keep the in-memory Hero authoritative immediately and
                        # persist just enough origin/logo metadata for instant
                        # cache reuse after the next receiver restart.
                        hero=getattr(self,"_hero_data",None)
                        if isinstance(hero,dict):
                            hero["title_logo_local"]=path
                            lang=self._home_logo_lang_from_path(path)
                            if lang:hero["title_logo_lang"]=lang
                            for key in ("original_language","origin_country","countries","production_countries","country_hint"):
                                value=resolved.get(key)
                                if value not in (None,"",[],{}):hero[key]=value
                        self._home_persist_title_logo_authority(identity,path,resolved)
                    elif title:
                        self._home_hide_title_logo();self["hero_title"].setText(title);self["hero_title"].show()
                except Exception as exc:optional_failure("ui.home_title_logo_apply",exc)
            elif job[0]=="recent_live_fit":
                _kind,generation,index,path=job
                try:
                    if int(generation)!=int(getattr(self,"_recent_generation",0) or 0):continue
                    index=max(0,min(8,int(index)))
                    if self._recent_group(index)!=0:continue
                    if not (path and os.path.isfile(str(path))):continue
                    self._recent_art_paths[index]=str(path)
                    preferred=self.recent_index if self.focus_row=="recent" and self._recent_group(self.recent_index)==0 else 0
                    self._bind_recent_group_art(0,preferred)
                except Exception as exc:optional_failure("ui.home_recent_live_fit_apply",exc)
            elif job[0]=="recent_progress_laser":
                _kind,generation,index,path=job
                try:
                    if int(generation)!=int(getattr(self,"_recent_generation",0) or 0):continue
                    index=max(0,min(8,int(index)))
                    if self._recent_group(index)==0:continue
                    if not (path and os.path.isfile(str(path))):continue
                    self._recent_progress_paths[index]=str(path)
                    selected=(self.focus_row=="recent" and int(self.recent_index)==index)
                    group=self._recent_group(index);row=self._recent_row(index)
                    self["recent_list%d"%group].update_icon_row(row,self._recent_list_entry(index,selected))
                except Exception as exc:optional_failure("ui.home_recent_laser_apply",exc)

