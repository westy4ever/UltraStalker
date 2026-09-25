"""Lightweight splash + one-time first-run choices.

The first-run Interface Language / TMDb Information Language / Clean Names
choices deliberately live on the existing Splash screen.  No extra screen, Home overlay, blur or dim layer is created.
After the normal progress reaches 100%, its lower lane is reused for the exact
fixed Settings glass rows, then the Portal List opens normally.
"""

from . import _
import os
import queue
import re
import threading
import time

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryPixmapAlphaBlend, MultiContentEntryText
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Tools.LoadPixmap import LoadPixmap
from enigma import (
    eTimer, ePoint, eSize, getDesktop, eListboxPythonMultiContent, gFont,
    RT_HALIGN_LEFT, RT_VALIGN_CENTER,
)

from .storage import load_settings, save_settings, load_theme, THEMES
from .language_catalog import interface_language_choices, description_language_choices, default_description_for_interface
from .localization import set_plugin_language
from .log import optional_failure, get_logger

LOG = get_logger()

FirstRunWizardScreen = None
PortalListScreen = None
WIZARD_DONE_FILE = ""
_schedule_startup_cache_maintenance = None
restore_plugin_service = None

# New marker owns the normal Splash flow. Old Home/wizard markers are also saved
# at completion so an upgraded install never falls back into obsolete first-run
# UI paths.
_SPLASH_ONBOARDING_LEGACY_KEY = "onboarding_splash_v1_completed"
_SPLASH_ONBOARDING_KEY = "onboarding_final_v9_completed"
_FINAL_V9_ONBOARDING_MARKER = "/media/hdd/UltraStalker/.onboarding_final_v9_done"

# Splash.skin is evaluated when this module is imported, before ui.py can inject
# runtime callbacks. Keep the original asset/theme and scaling semantics local.
ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets_fhd")
_ACTIVE_THEME_CACHE = None
_ACTIVE_THEME_LOCK = threading.RLock()


def _active_theme():
    global _ACTIVE_THEME_CACHE
    if _ACTIVE_THEME_CACHE in THEMES:
        return _ACTIVE_THEME_CACHE
    with _ACTIVE_THEME_LOCK:
        if _ACTIVE_THEME_CACHE in THEMES:
            return _ACTIVE_THEME_CACHE
        try:
            value = load_theme(readonly=True)
        except Exception:
            value = "nova_fhd"
        _ACTIVE_THEME_CACHE = value if value in THEMES else "nova_fhd"
        return _ACTIVE_THEME_CACHE


def asset(name):
    themed = os.path.join(ASSET_DIR, "themes", _active_theme(), name)
    return themed if os.path.exists(themed) else os.path.join(ASSET_DIR, name)


def _scale_skin(xml):
    try:
        desktop = getDesktop(0).size()
        width, height = int(desktop.width()), int(desktop.height())
    except Exception:
        return xml
    if width == 1920 and height == 1080:
        return xml
    sx, sy = width / 1920.0, height / 1080.0

    def pair(match):
        return '%s="%d,%d"' % (
            match.group(1),
            round(int(match.group(2)) * sx),
            round(int(match.group(3)) * sy),
        )

    xml = re.sub(r'(position|size)="(\d+),(\d+)"', pair, xml)
    xml = re.sub(
        r'font="([^;]+);(\d+)"',
        lambda m: 'font="%s;%d"' % (m.group(1), max(14, round(int(m.group(2)) * sy))),
        xml,
    )
    return xml


def configure_splash_screen(
    first_run_wizard_screen,
    portal_list_screen,
    wizard_done_file,
    scale_skin,
    schedule_startup_cache_maintenance,
    asset_func,
    restore_plugin_service_func,
):
    global FirstRunWizardScreen, PortalListScreen, WIZARD_DONE_FILE
    global _schedule_startup_cache_maintenance, restore_plugin_service
    FirstRunWizardScreen = first_run_wizard_screen
    PortalListScreen = portal_list_screen
    WIZARD_DONE_FILE = wizard_done_file
    _schedule_startup_cache_maintenance = schedule_startup_cache_maintenance
    restore_plugin_service = restore_plugin_service_func


class SplashChoiceList(MenuList):
    """One lightweight list using the exact fixed Settings 426x72 glass rows."""

    def __init__(self):
        self.row_width = 434
        self.row_height = 74
        self._choices = []
        self._last_selected = 0
        self._normal = None
        self._selected = None
        try:
            self._normal = LoadPixmap(path=asset("fixed_master_r63/utility_row.png"), cached=True)
            self._selected = LoadPixmap(path=asset("fixed_master_r63/utility_row_selected.png"), cached=True)
        except Exception:
            try:
                self._normal = LoadPixmap(path=asset("fixed_master_r63/utility_row.png"))
                self._selected = LoadPixmap(path=asset("fixed_master_r63/utility_row_selected.png"))
            except Exception:
                self._normal = self._selected = None
        MenuList.__init__(self, [], enableWrapAround=True, content=eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", 22))
        self.l.setItemHeight(self.row_height)

    def _entry(self, index, selected=False):
        label, value = self._choices[index]
        row = [value]
        frame = self._selected if selected else self._normal
        if frame is not None:
            row.append(MultiContentEntryPixmapAlphaBlend(
                pos=(4, 1), size=(426, 72), png=frame
            ))
        row.append(MultiContentEntryText(
            pos=(22, 1), size=(390, 72), font=0,
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
            text=str(label or ""), color=0x00FFFFFF, color_sel=0x00FFFFFF,
        ))
        return row

    def set_choices(self, choices, selected_index=0):
        self._choices = list(choices or [])
        if not self._choices:
            self.list = []
            self.l.setList([])
            return
        idx = max(0, min(len(self._choices) - 1, int(selected_index or 0)))
        self._last_selected = idx
        self.list = [self._entry(i, i == idx) for i in range(len(self._choices))]
        self.l.setList(self.list)
        try:
            self.moveToIndex(idx)
        except Exception:
            pass

    def refresh_selected_rows(self):
        if not self._choices:
            return
        try:
            idx = int(self.getSelectedIndex())
        except Exception:
            return
        old = int(self._last_selected)
        if idx == old:
            return
        self._last_selected = idx
        for pos in set((old, idx)):
            if 0 <= pos < len(self._choices):
                try:
                    self.list[pos] = self._entry(pos, pos == idx)
                    self.l.invalidateEntry(pos)
                except Exception:
                    try:
                        self.l.setList(self.list)
                    except Exception:
                        pass

    def selected_value(self):
        try:
            idx = int(self.getSelectedIndex())
            if 0 <= idx < len(self._choices):
                return self._choices[idx][1]
        except Exception:
            pass
        return None


class SplashScreen(Screen):
    # R38: no cosmetic dwell on the initial plugin Splash. It remains visible
    # only for the real staged initialization below; portal-specific warmup is
    # owned separately by HomeWarmupSplashScreen and is intentionally unchanged.
    MIN_VISIBLE_MS = 0
    # R235: R234-approved Splash visuals are frozen; only R214 inline onboarding is restored.
    skin = _scale_skin("""<screen name="SplashScreen" position="center,center" size="1920,1080" backgroundColor="#01040a" flags="wfNoBorder"><ePixmap position="0,0" size="1920,1080" pixmap="%s" alphatest="blend" zPosition="1"/><ePixmap position="473,256" size="975,326" pixmap="%s" alphatest="blend" scale="1" zPosition="5"/><widget name="status" position="510,600" size="900,42" font="Regular;24" halign="center" valign="center" foregroundColor="#d5e4ee" backgroundColor="#000000" transparent="1" zPosition="10" shadowColor="#000000" shadowOffset="1,1"/><widget name="progress_track" position="580,670" size="760,14" pixmap="%s" alphatest="blend" transparent="1" zPosition="6"/><widget name="progress" position="586,675" size="748,4" borderWidth="0" backgroundColor="#0b1a27" foregroundColor="#8d1414" zPosition="7"/><widget name="progress_glow" position="557,656" size="86,42" pixmap="%s" alphatest="blend" transparent="1" zPosition="9"/><widget name="edition" position="710,722" size="500,30" font="Regular;17" halign="center" valign="center" foregroundColor="#66859a" backgroundColor="#000000" transparent="1" zPosition="10" shadowColor="#000000" shadowOffset="1,1"/><widget name="onboarding_title" position="435,662" size="1000,38" font="Regular;26" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="20" shadowColor="#000000" shadowOffset="1,1"/><widget name="onboarding_subtitle" position="385,704" size="1100,46" font="Regular;18" halign="center" valign="center" foregroundColor="#b7c9d4" backgroundColor="#000000" transparent="1" zPosition="20" shadowColor="#000000" shadowOffset="1,1"/><widget name="onboarding_list" position="718,710" size="434,370" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="%s" scrollbarMode="showNever" transparent="1" zPosition="21"/></screen>""" % (asset("splash_bg_premium.png"), asset("splash_logo_palestine_painted.png"), asset("splash_premium_track.png"), asset("splash_premium_glow.png"), asset("portal_selection_clear.png")))

    def __init__(self, session):
        self._perf_construct_mono = time.monotonic()
        self._splash_start_mono = self._perf_construct_mono
        LOG.info("PERF25 splash init_enter mono_ms=%d", int(self._perf_construct_mono * 1000))
        Screen.__init__(self, session)
        self["status"] = Label(_("Preparing your experience"))
        self["edition"] = Label(_("ULTRA STALKER  •  PREMIUM"))
        self["progress"] = ProgressBar(); self["progress"].setRange((0, 100)); self["progress"].setValue(8)
        self["progress_track"] = Pixmap()
        self["progress_glow"] = Pixmap()
        self["onboarding_title"] = Label("")
        self["onboarding_subtitle"] = Label("")
        self["onboarding_list"] = SplashChoiceList()
        self._stage = 0
        self._runtime_loaded = False
        self._maintenance_scheduled = False
        self._onboarding_active = False
        self._onboarding_step = 0
        self._onboarding_language = "en"
        self._onboarding_tmdb_language = "ar-en"
        self._onboarding_clean = True
        try:
            cfg = load_settings() or {}
        except Exception:
            cfg = {}
        self._onboarding_needed = not (bool(cfg.get(_SPLASH_ONBOARDING_KEY, False)) or os.path.isfile(_FINAL_V9_ONBOARDING_MARKER))
        self._onboarding_language = str(cfg.get("plugin_language") or "en")
        self._onboarding_tmdb_language = str(cfg.get("description_language") or default_description_for_interface(self._onboarding_language))
        self._onboarding_clean = bool(cfg.get("clean_titles", True))

        try:
            self["onboarding_title"].hide(); self["onboarding_subtitle"].hide(); self["onboarding_list"].hide()
        except Exception:
            pass
        if self._onboarding_needed:
            # First run is setup-only. The laser starts from the next launch.
            try:
                for name in ("progress_track", "progress", "progress_glow", "status", "edition"):
                    self[name].hide()
            except Exception:
                pass

        self["onboarding_actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok": self._onboarding_accept,
                "cancel": self._onboarding_back,
                "up": lambda: self._onboarding_move(-1),
                "down": lambda: self._onboarding_move(1),
                "left": lambda: self._onboarding_page(-1),
                "right": lambda: self._onboarding_page(1),
            },
            -1200,
        )
        try:
            self["onboarding_actions"].setEnabled(False)
        except Exception:
            pass
        try:
            if self._onboarding_selection_changed not in self["onboarding_list"].onSelectionChanged:
                self["onboarding_list"].onSelectionChanged.append(self._onboarding_selection_changed)
        except Exception as exc:
            optional_failure("ui.splash_onboarding_hook", exc)

        self._timer = eTimer(); self._conn = None
        try:
            self._conn = self._timer.timeout.connect(self._go)
        except Exception:
            try:
                self._timer.callback.append(self._go)
            except Exception as exc:
                optional_failure("ui", exc)
        self.onLayoutFinish.append(self._apply_onboarding_list_native_style)
        self.onLayoutFinish.append(self._start_splash_timer)
        self.onClose.append(self._stop)
        self.onClose.append(self._safe_restore_service)

    def _apply_onboarding_list_native_style(self):
        """Suppress Enigma's native blue list focus; the Settings glass row owns focus."""
        try:
            inst = self["onboarding_list"].instance
            if inst is not None:
                inst.setSelectionEnable(0)
                inst.setTransparent(1)
                inst.setScrollbarMode(2)
        except Exception as exc:
            optional_failure("ui.splash_onboarding_native_list", exc)

    def _safe_restore_service(self):
        try:
            if callable(restore_plugin_service):
                restore_plugin_service(self.session)
        except Exception as exc:
            optional_failure("ui.splash_restore", exc)

    def _schedule_maintenance_once(self):
        if self._maintenance_scheduled:
            return
        self._maintenance_scheduled = True
        if callable(_schedule_startup_cache_maintenance):
            try:
                _schedule_startup_cache_maintenance()
            except Exception as exc:
                optional_failure("ui.splash_maintenance_schedule", exc)

    def _ensure_runtime(self):
        if self._runtime_loaded and PortalListScreen is not None:
            return True
        try:
            _phase = time.monotonic()
            LOG.info("PERF25 splash ui_import_begin mono_ms=%d", int(_phase * 1000))
            from . import ui as runtime_ui
            LOG.info("PERF25 splash ui_import_done elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
            try:
                _launch_phase = time.monotonic()
                runtime_ui.begin_plugin_launch(self.session)
                LOG.info("PERF25 splash begin_plugin_launch_done elapsed_ms=%d", int((time.monotonic() - _launch_phase) * 1000))
            except Exception as exc:
                optional_failure("ui.splash_begin_launch", exc)
            self._runtime_loaded = bool(PortalListScreen is not None)
            # Cache maintenance is deliberately delayed until the first-run
            # selector is finished so remote navigation owns the receiver.
            return self._runtime_loaded
        except Exception as exc:
            optional_failure("ui.splash_lazy_runtime", exc)
            self["status"].setText(_("Ultra Stalker failed to initialize"))
            return False

    def _start_splash_timer(self):
        self._splash_start_mono = time.monotonic()
        LOG.info(
            "PERF25 splash first_layout mono_ms=%d construct_to_layout_ms=%d",
            int(time.monotonic() * 1000),
            int((time.monotonic() - self._perf_construct_mono) * 1000),
        )
        self._set_splash_progress(8)
        try:
            self._timer.start(20, True)
        except Exception:
            self._go()

    def _set_splash_progress(self, pct):
        pct = max(0, min(100, int(pct)))
        try:
            self["progress"].setValue(pct)
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            desktop = getDesktop(0).size(); sx = float(desktop.width()) / 1920.0; sy = float(desktop.height()) / 1080.0
            bar_x, bar_w, glow_w = 586, 748, 86
            x = bar_x + int(round((bar_w * pct) / 100.0)) - glow_w // 2
            x = max(bar_x - glow_w // 2, min(bar_x + bar_w - glow_w // 2, x))
            self["progress_glow"].instance.move(ePoint(int(round(x * sx)), int(round(656 * sy))))
        except Exception as exc:
            optional_failure("ui", exc)

    def _stop(self):
        try:
            self._timer.stop()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._conn is not None:
                self._conn.disconnect()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._go in self._timer.callback:
                self._timer.callback.remove(self._go)
        except Exception as exc:
            optional_failure("ui.splash_timer_callback", exc)

    def _onboarding_choices(self):
        if self._onboarding_step == 1:
            try:
                rows = [(str(name), str(code)) for name, code in interface_language_choices()]
            except Exception:
                rows = []
            return rows or [
                ("English", "en"), ("العربية", "ar"), ("Deutsch", "de"),
                ("Français", "fr"), ("Türkçe", "tr"),
            ]
        if self._onboarding_step == 2:
            # Reuse the exact Settings authority. No duplicate TMDb-language
            # registry is allowed on first run.
            try:
                rows = [(_(label), str(code)) for code, label in description_language_choices()]
            except Exception:
                rows = []
            return rows or [
                ("العربية", "ar-EG"), ("English", "en-US"),
                ("Français", "fr-FR"),
            ]
        return [(_("Clean"), True), (_("Original"), False)]

    def _choice_index(self, choices, value):
        wanted = str(value if value is not None else "").strip().lower()
        for pos, row in enumerate(choices or []):
            try:
                if str(row[1]).strip().lower() == wanted:
                    return pos
            except Exception:
                pass
        return 0

    def _onboarding_list_geom(self, count):
        # Five rows occupy the original lower Splash lane. Two rows are centered
        # in the same region rather than creating a new popup geometry.
        try:
            count = max(1, min(5, int(count or 1)))
            base_x, w = 718, 434
            y = 710 if self._onboarding_step in (1, 2) else 756
            h = count * 74
            desktop = getDesktop(0).size(); sx = float(desktop.width()) / 1920.0; sy = float(desktop.height()) / 1080.0
            inst = self["onboarding_list"].instance
            if inst is not None:
                inst.move(ePoint(int(round(base_x * sx)), int(round(y * sy))))
                inst.resize(eSize(int(round(w * sx)), int(round(h * sy))))
        except Exception as exc:
            optional_failure("ui.splash_onboarding_geom", exc)

    def _show_onboarding_step(self):
        choices = self._onboarding_choices()
        if self._onboarding_step == 1:
            value = self._onboarding_language
        elif self._onboarding_step == 2:
            value = self._onboarding_tmdb_language
        else:
            value = self._onboarding_clean
        idx = self._choice_index(choices, value)
        try:
            if self._onboarding_step == 1:
                title = _("Choose Language")
                subtitle = ""
            elif self._onboarding_step == 2:
                # Exact Settings title + exact Settings choice registry.
                title = _("TMDb Information Language")
                subtitle = ""
            else:
                # Exact Settings wording: the first-run screen explains the
                # same clean/raw choice without inventing a second vocabulary.
                title = _("Clean Display Names")
                subtitle = _("Do you want plugin names cleaned or original?")
            self["onboarding_title"].setText(title)
            self["onboarding_subtitle"].setText(subtitle)
            self["onboarding_list"].set_choices(choices, idx)
            self._onboarding_list_geom(min(5, len(choices)))
            self["onboarding_title"].show()
            if subtitle:
                self["onboarding_subtitle"].show()
            else:
                self["onboarding_subtitle"].hide()
            self["onboarding_list"].show()
            self._apply_onboarding_list_native_style()
        except Exception as exc:
            optional_failure("ui.splash_onboarding_render", exc)

    def _start_onboarding(self):
        self._stop()
        self._onboarding_active = True
        self._onboarding_step = 1
        # First run is setup-only. Progress appears starting with the next boot.
        self._set_splash_progress(100)
        try:
            for name in ("status", "edition", "progress_track", "progress", "progress_glow"):
                self[name].hide()
        except Exception:
            pass
        try:
            self["onboarding_actions"].setEnabled(True)
        except Exception:
            pass
        self._show_onboarding_step()
        LOG.info("Splash first-run selector active=1")

    def _onboarding_selection_changed(self):
        if not self._onboarding_active:
            return
        try:
            self["onboarding_list"].refresh_selected_rows()
        except Exception as exc:
            optional_failure("ui.splash_onboarding_selection", exc)

    def _onboarding_move(self, step):
        if not self._onboarding_active:
            return
        try:
            if int(step or 0) < 0:
                self["onboarding_list"].up()
            else:
                self["onboarding_list"].down()
        except Exception as exc:
            optional_failure("ui.splash_onboarding_move", exc)

    def _onboarding_page(self, direction):
        """LEFT/RIGHT move by one visible page; UP/DOWN remain one row."""
        if not self._onboarding_active:
            return
        try:
            total = len(getattr(self["onboarding_list"], "_choices", []) or [])
            page = 5
            # LEFT/RIGHT are page navigation, never single-row navigation.
            # A one-page list deliberately does nothing.
            if total <= page:
                return
            current = int(self["onboarding_list"].getSelectedIndex())
            delta = page if int(direction or 0) > 0 else -page
            target = current + delta
            if target < 0 or target >= total:
                return
            self["onboarding_list"].moveToIndex(target)
            self["onboarding_list"].refresh_selected_rows()
        except Exception as exc:
            optional_failure("ui.splash_onboarding_page", exc)

    def _onboarding_accept(self):
        if not self._onboarding_active:
            return
        value = self["onboarding_list"].selected_value()
        if value is None:
            return
        if self._onboarding_step == 1:
            self._onboarding_language = str(value or "en")
            # Apply exactly once on OK, never while arrows are moving. This also
            # makes the next prompts use the language the user just selected.
            try:
                set_plugin_language(self._onboarding_language)
            except Exception as exc:
                optional_failure("ui.splash_onboarding_language", exc)
            self._onboarding_step = 2
            self._show_onboarding_step()
            return
        if self._onboarding_step == 2:
            # Store the same value Settings uses for TMDb Information Language.
            self._onboarding_tmdb_language = str(value or default_description_for_interface(self._onboarding_language))
            self._onboarding_step = 3
            self._show_onboarding_step()
            return

        self._onboarding_clean = bool(value)
        code = str(self._onboarding_language or "en")
        tmdb_code = str(self._onboarding_tmdb_language or default_description_for_interface(code))
        try:
            save_settings({
                "plugin_language": code,
                "description_language": tmdb_code,
                "clean_titles": self._onboarding_clean,
                "onboarding_v1_completed": True,
                "onboarding_v2_completed": True,
                "onboarding_v3_completed": True,
                _SPLASH_ONBOARDING_LEGACY_KEY: True,
                _SPLASH_ONBOARDING_KEY: True,
            })
            try:
                marker_dir = os.path.dirname(_FINAL_V9_ONBOARDING_MARKER)
                if marker_dir:
                    os.makedirs(marker_dir, exist_ok=True)
                with open(_FINAL_V9_ONBOARDING_MARKER, "w", encoding="ascii") as marker_handle:
                    marker_handle.write("Ultra Stalker Final V9.1 onboarding complete\n")
            except Exception as exc:
                optional_failure("ui.splash_onboarding_final_marker", exc)
            set_plugin_language(code)
        except Exception as exc:
            optional_failure("ui.splash_onboarding_save", exc)
        self._onboarding_active = False
        self._onboarding_needed = False
        try:
            self["onboarding_actions"].setEnabled(False)
            self["onboarding_list"].set_choices([], 0)
            self["onboarding_list"].hide(); self["onboarding_title"].hide(); self["onboarding_subtitle"].hide()
        except Exception:
            pass
        self._open_portal_list()

    def _onboarding_back(self):
        if not self._onboarding_active:
            return
        if self._onboarding_step == 3:
            self._onboarding_step = 2
            self._show_onboarding_step()
        elif self._onboarding_step == 2:
            self._onboarding_step = 1
            self._show_onboarding_step()
        # Step 1 is mandatory on first run; BACK deliberately does nothing.

    def _open_portal_list(self):
        if not self._runtime_loaded or PortalListScreen is None:
            self.close(); return
        self._schedule_maintenance_once()
        try:
            _phase = time.monotonic()
            LOG.info("PERF25 splash portal_list_open_begin mono_ms=%d", int(_phase * 1000))
            self.session.openWithCallback(self._next_screen_closed, PortalListScreen)
            LOG.info("PERF25 splash portal_list_open_return elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
        except Exception:
            self.close()

    def _go(self):
        # Short yielded stages let Enigma2 paint every progress state before the
        # expensive runtime import.
        if self._stage == 0:
            self._stage = 1
            self["status"].setText(_("Preparing interface")); self._set_splash_progress(25)
            try:
                self._timer.start(15, True)
            except Exception:
                self._go()
            return
        if self._stage == 1:
            self._stage = 2
            self["status"].setText(_("Loading Ultra Stalker")); self._set_splash_progress(50)
            try:
                self._timer.start(15, True)
            except Exception:
                self._go()
            return
        if self._stage == 2:
            self._stage = 3
            if not self._ensure_runtime():
                self._set_splash_progress(100)
                try:
                    self._timer.start(1200, True)
                except Exception:
                    self.close()
                return
            # R268: while the initial Splash is already visible, decode the
            # persistent Home Hero/title-logo and seven menu icons into the
            # tiny boot pixmap cache. Portal-specific network work waits until
            # the viewer selects a source.
            try:
                from . import ui as runtime_ui
                runtime_ui.splash_prime_home_pixmaps_gui()
            except Exception as exc:
                optional_failure("ui.splash_home_global_prime",exc)
            self["status"].setText(_("Preparing your library")); self._set_splash_progress(75)
            try:
                self._timer.start(20, True)
            except Exception:
                self._go()
            return
        if self._stage == 3:
            self._stage = 4
            self["status"].setText(_("Ready")); self._set_splash_progress(100)
            try:
                self._timer.start(25, True)
            except Exception:
                self._go()
            return

        # Progress is complete. On first run, stay on this exact Splash and
        # replace only the lower progress lane with Settings-style rows.
        if self._onboarding_needed:
            self._start_onboarding()
            return
        elapsed=int((time.monotonic()-self._splash_start_mono)*1000.0)
        if elapsed < self.MIN_VISIBLE_MS:
            try:self._timer.start(max(1,self.MIN_VISIBLE_MS-elapsed),True)
            except Exception:pass
            return
        self._stop()
        self._open_portal_list()

    def _next_screen_closed(self, *args, **kwargs):
        self.close()


class HomeWarmupSplashScreen(Screen):
    """R268 selected-portal warmup using the exact approved Splash visuals.

    The Portal List remains the ownership boundary for selecting a source. Once
    a portal is selected, this screen keeps Home hidden while its reusable
    PortalSession, category catalogues, account state and local Hero pixmaps are
    prepared. Home is opened only after the warmup is complete.
    """
    skin = SplashScreen.skin
    MIN_VISIBLE_MS = 1350

    def __init__(self, session, profile, home_screen):
        Screen.__init__(self, session)
        self.profile = dict(profile or {})
        self._home_screen = home_screen
        self._started = False
        self._finished = False
        self._opened = False
        self._closed = False
        self._pixmaps_primed = False
        self._start_mono = time.monotonic()
        self._events = queue.Queue()
        self._worker = None
        self["status"] = Label(_("Preparing Home"))
        self["edition"] = Label(_("ULTRA STALKER  •  PREMIUM"))
        self["progress"] = ProgressBar(); self["progress"].setRange((0,100)); self["progress"].setValue(0)
        self["progress_track"] = Pixmap(); self["progress_glow"] = Pixmap()
        self["onboarding_title"] = Label(""); self["onboarding_subtitle"] = Label("")
        self["onboarding_list"] = SplashChoiceList()
        try:
            self["onboarding_title"].hide(); self["onboarding_subtitle"].hide(); self["onboarding_list"].hide()
        except Exception:
            pass
        self._timer=eTimer();self._conn=None
        try:self._conn=self._timer.timeout.connect(self._poll)
        except Exception:
            try:self._timer.callback.append(self._poll)
            except Exception:pass
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._stop)

    def _set_progress(self,pct,text=None):
        pct=max(0,min(100,int(pct)))
        if text:
            try:self["status"].setText(_(str(text)))
            except Exception:pass
        try:self["progress"].setValue(pct)
        except Exception:pass
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            bar_x,bar_w,glow_w=586,748,86
            x=bar_x+int(round((bar_w*pct)/100.0))-glow_w//2
            x=max(bar_x-glow_w//2,min(bar_x+bar_w-glow_w//2,x))
            self["progress_glow"].instance.move(ePoint(int(round(x*sx)),int(round(656*sy))))
        except Exception:pass

    def _start(self):
        if self._started:return
        self._started=True;self._start_mono=time.monotonic();self._set_progress(25,_('Preparing Home artwork'))
        def report(pct,text):
            try:self._events.put(("progress",int(pct),str(text or "")))
            except Exception:pass
        def worker():
            error=""
            try:
                from . import ui as runtime_ui
                runtime_ui.splash_warm_home_profile(self.profile,progress=report)
            except Exception as exc:
                error=str(exc)
                try:optional_failure("ui.home_warmup_splash",exc)
                except Exception:pass
            try:self._events.put(("done",100,_('Ready') if not error else _('Ready')))
            except Exception:pass
        try:
            self._worker=threading.Thread(target=worker,name="UltraStalkerHomeWarmup")
            self._worker.daemon=True;self._worker.start()
        except Exception as exc:
            optional_failure("ui.home_warmup_thread",exc);self._finished=True
        try:self._timer.start(45,False)
        except Exception:self._poll()

    def _poll(self):
        if self._closed:return
        while True:
            try:event=self._events.get_nowait()
            except queue.Empty:break
            except Exception:break
            if not event:continue
            if event[0]=="progress":
                self._set_progress(event[1],event[2])
                if int(event[1])>=25 and not self._pixmaps_primed:
                    self._pixmaps_primed=True
                    try:
                        from . import ui as runtime_ui
                        runtime_ui.splash_prime_home_pixmaps_gui()
                    except Exception as exc:
                        optional_failure("ui.home_warmup_pixmaps",exc)
            elif event[0]=="done":self._finished=True;self._set_progress(100,event[2])
        if self._finished and not self._opened:
            elapsed=int((time.monotonic()-self._start_mono)*1000.0)
            if elapsed>=self.MIN_VISIBLE_MS:
                self._opened=True
                try:self._timer.stop()
                except Exception:pass
                try:self.session.openWithCallback(self._home_closed,self._home_screen,self.profile)
                except Exception as exc:
                    optional_failure("ui.home_warmup_open",exc);self.close()

    def _home_closed(self,result=None):
        if not self._closed:self.close(result)

    def _stop(self):
        self._closed=True
        try:self._timer.stop()
        except Exception:pass
        try:
            if self._conn is not None:self._conn.disconnect()
        except Exception:pass
        try:
            if self._poll in self._timer.callback:self._timer.callback.remove(self._poll)
        except Exception:pass
