"""Portal list screen extracted from ui.py without changing class behavior."""

from . import _
import hashlib
import json
import os
import queue
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.InputBox import InputBox
try:
    from Screens.VirtualKeyBoard import VirtualKeyBoard
except Exception:
    VirtualKeyBoard = None
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, getDesktop, ePoint, eSize, eLabel, gFont
from skin import parseColor

from .ui_async import AsyncScreenMixin
from .ui_image_loader import ImageLoaderMixin
from .ui_transition import TransitionMixin
from .ui_helpers import fit_color_key_labels
from .ui_fixed_adaptive import fixed_settings_rows, fixed_value_color, fixed_settings_chrome, cleanup_legacy_application_outputs
# R112: settings popup factories are injected by ui.py.  Keeping this screen
# independent prevents the 150KB settings implementation from loading merely
# to paint the portal list.
SettingsGlassChoiceScreen = None
SettingsGlassNoticeScreen = None
SettingsGlassInputScreen = None
from .log import diagnostic_failure, get_logger

LOG = get_logger()

MAIN_SKIN = ""
_plugin_original_service_getter = lambda: None
_plugin_service_captured_getter = lambda: False


def _portal_category_backdrop():
    """Return the shared bundled background used by portal surfaces."""
    fallback = asset("category_palestine_static_1920x1080.jpg")
    return fallback if fallback and os.path.isfile(fallback) else ""


# Provider-reported connection telemetry only. Never synthesize or default a
# count: the badge appears only when the server itself returned BOTH values.
def _provider_connection_int(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        text = str(value).strip()
        if not text or not re.match(r"^\d+$", text):
            return None
        number = int(text)
    except Exception:
        return None
    return number if 0 <= number <= 999 else None

def _provider_payload_dicts(payload):
    """Yield provider dictionaries recursively without interpreting free text."""
    stack = [payload]
    seen = set()
    while stack:
        current = stack.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        if isinstance(current, dict):
            yield current
            for value in current.values():
                if isinstance(value, (dict, list, tuple)):
                    stack.append(value)
        elif isinstance(current, (list, tuple)):
            for value in current:
                if isinstance(value, (dict, list, tuple)):
                    stack.append(value)

def _extract_provider_connections(payloads):
    """Return a real (active,max) pair only from one explicit provider object.

    We intentionally do not combine an active value from one response/object with
    a limit from another.  That would look clever but could manufacture a false
    pair.  If the provider does not return both values together, no badge is shown.
    """
    # Known provider field pairs. Xtream's user_info pair is first and therefore
    # wins when present. The remaining pairs are accepted only when BOTH numeric
    # values live in the same provider dictionary.
    exact_pairs = (
        ("active_cons", "max_connections"),
        ("active_connections", "max_connections"),
        ("current_connections", "max_connections"),
        ("current_cons", "max_cons"),
        ("used_connections", "allowed_connections"),
        ("connections_active", "connections_limit"),
        ("online_connections", "max_online_connections"),
        ("online", "max_online"),
        ("active_streams", "max_streams"),
    )
    for source_name, payload in payloads or ():
        for node in _provider_payload_dicts(payload):
            for active_key, max_key in exact_pairs:
                if active_key not in node or max_key not in node:
                    continue
                active = _provider_connection_int(node.get(active_key))
                maximum = _provider_connection_int(node.get(max_key))
                if active is None or maximum is None or maximum <= 0:
                    continue
                return {
                    "active": active,
                    "max": maximum,
                    "source": str(source_name or "provider_exact"),
                    "active_key": active_key,
                    "max_key": max_key,
                }
    return None

def _probe_provider_connections(client, account_info=None, cancel_event=None):
    """Perform a patient, fresh provider check for real connection counts.

    Xtream player_api is authoritative.  Stalker/MAG panels vary, so when the
    main account object has no pair we also ask fresh account + profile variants.
    No request runs while simply moving the selection; this is called ONLY by
    Check / Check All and its result is persisted in the profile.
    """
    if cancel_event is not None and cancel_event.is_set():
        return None
    payloads = []

    # Xtream/M3U adapter. account_info() triggers a real player_api probe when
    # needed; Check All also calls probe() first.  Use that fresh response first.
    if hasattr(client, "_xtream_probe"):
        if isinstance(account_info, dict):
            payloads.append(("provider_exact", account_info))
        found = _extract_provider_connections(payloads)
        if found:
            return found
        if not getattr(client, "_xtream", None):
            return None
        try:
            client._xtream_probe(cancel_event=cancel_event)
            account = getattr(client, "_xtream_account", None)
            if account:
                payloads.append(("provider_exact", account))
        except Exception as exc:
            optional_failure("ui.portal_connections_xtream", exc)
        return _extract_provider_connections(payloads)

    # Stalker/MAG: do not stop at a cached/fast account response.  Ask the
    # provider again without cache, then inspect get_profile variants too.
    if hasattr(client, "_get") and hasattr(client, "_unwrap"):
        try:
            if not getattr(client, "token", ""):
                client.authorize(cancel_event=cancel_event)
        except Exception as exc:
            optional_failure("ui.portal_connections_authorize", exc)
            return None
        for type_name in ("account_info", "account"):
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                fresh = client._unwrap(client._get({
                    "type": type_name, "action": "get_main_info", "JsHttpRequest": "1-xml"
                }, use_cache=False, cancel_event=cancel_event))
                if isinstance(fresh, (dict, list, tuple)):
                    payloads.append(("provider_exact", fresh))
                    found = _extract_provider_connections(payloads)
                    if found:
                        return found
            except Exception as exc:
                optional_failure("ui.portal_connections_account", exc)
        profile_params = []
        try:
            profile_params.append(client._full_profile_params(getattr(client, "resolved_device_profile", None)))
        except Exception as exc:
            optional_failure("ui.portal_connections_full_profile_params", exc)
        try:
            profile_params.append(client._basic_profile_params())
        except Exception as exc:
            optional_failure("ui.portal_connections_basic_profile_params", exc)
        for params in profile_params:
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                fresh = client._unwrap(client._get(params, use_cache=False, retry_auth=False, cancel_event=cancel_event))
                if isinstance(fresh, (dict, list, tuple)):
                    payloads.append(("provider_exact", fresh))
                    found = _extract_provider_connections(payloads)
                    if found:
                        return found
            except Exception as exc:
                optional_failure("ui.portal_connections_profile", exc)
        # Keep the original account payload as a final exact fallback only after
        # the fresh provider probes have had a real chance to answer.
        if isinstance(account_info, dict):
            payloads.append(("provider_exact", account_info))
        return _extract_provider_connections(payloads)
    return None

def _clear_profile_connections(profile):
    if not isinstance(profile, dict):
        return
    profile.pop("active_connections", None)
    profile.pop("max_connections", None)
    profile.pop("connections_source", None)


def _portal_connection_badge(profile):
    p = profile if isinstance(profile, dict) else {}
    if str(p.get("connections_source") or "") != "provider_exact":
        return None
    active = _provider_connection_int(p.get("active_connections"))
    maximum = _provider_connection_int(p.get("max_connections"))
    if active is None or maximum is None or maximum <= 0:
        return None
    # EXACT Cinematic footer assets, not recreated/look-alike artwork.
    icon = asset("us6521_key_yellow_170x42.png" if active >= maximum else "us6521_key_green_170x42.png")
    return icon, ("%d / %d" % (active, maximum))

def _portal_settings_text_width_px(text, font_size=24):
    """Exact copy of the Plugin Settings text measurement rule."""
    value=str(text or "")
    try:
        probe=eLabel()
        probe.setFont(gFont("Regular",int(font_size)))
        probe.setText(value)
        size=probe.calculateSize()
        return max(0,int(size.width()))
    except Exception as exc:
        optional_failure("ui.portal_settings_text_measure",exc)
        return max(0,int(len(value)*font_size*0.56))


def configure_portal_list(**deps):
    globals().update(deps)
    # Portal selection now deliberately shares the approved Settings Test8
    # visual shell instead of the legacy portal-specific full-page skin.
    try:
        scaler=deps.get("font_scale_skin")
        PortalListScreen.skin = scaler(PORTAL_SELECTION_SETTINGS_SKIN) if callable(scaler) else PORTAL_SELECTION_SETTINGS_SKIN
        PortalLibraryListScreen.skin = scaler(PORTAL_LIBRARY_SETTINGS_SKIN) if callable(scaler) else PORTAL_LIBRARY_SETTINGS_SKIN
        PortalManagerPremiumScreen.skin = scaler(PORTAL_MANAGER_PREMIUM_SKIN) if callable(scaler) else PORTAL_MANAGER_PREMIUM_SKIN
    except Exception as exc:
        optional_failure("ui.portal_skin_config",exc)



PORTAL_MANAGER_PREMIUM_SKIN = """<screen name="PortalManagerPremiumScreen" position="center,center" size="1920,1080" backgroundColor="#02070b" flags="wfNoBorder">
 <widget name="ambient_bg" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
 <widget name="hero_backdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
 <widget name="list" position="50,20" size="430,1040" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
 <widget name="context_title" position="1160,618" size="650,40" font="Regular;29" foregroundColor="#ffffff" transparent="1" zPosition="11" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="context_subtitle" position="1160,658" size="650,28" font="Regular;17" foregroundColor="#91b7ca" transparent="1" zPosition="11" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="context_list" position="1160,700" size="650,220" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="12"/>
 <widget name="status" position="540,1028" size="1320,34" font="Regular;18" halign="right" valign="center" foregroundColor="#b7d9eb" shadowColor="#000000" shadowOffset="1,1" transparent="1" zPosition="9"/>
</screen>"""


PORTAL_SELECTION_SETTINGS_SKIN = """<screen name="PortalListScreen" position="center,center" size="1920,1080" backgroundColor="#02070b" flags="wfNoBorder">
 <widget name="ambient_bg" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
 <widget name="hero_backdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
 <widget name="list" position="50,20" size="620,1040" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
 <widget name="source_badges" position="1430,748" size="430,240" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
 <!-- One compact top footer row: check progress | server | connection | page/source. -->
 <widget name="check_progress" position="860,993" size="310,30" font="Regular;16" halign="right" valign="center" foregroundColor="#9fc6df" transparent="1" zPosition="8" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="status" position="1175,993" size="180,30" font="Regular;16" halign="left" valign="center" foregroundColor="#d8e7ef" shadowColor="#000000" shadowOffset="1,1" transparent="1" zPosition="9" noWrap="1"/>
 <widget name="footer_connection" position="1360,993" size="120,30" font="Regular;16" halign="left" valign="center" foregroundColor="#39ff88" shadowColor="#000000" shadowOffset="1,1" transparent="1" zPosition="9" noWrap="1"/>
 <widget name="portal_page" position="1485,993" size="375,30" font="Regular;16" halign="right" valign="center" foregroundColor="#9fc6df" transparent="1" zPosition="8" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>

 <!-- Compact direct portal actions sit directly below the footer row. -->
 <ePixmap position="1156,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_red_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="red" position="1164,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
 <ePixmap position="1334,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_green_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="green" position="1342,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
 <ePixmap position="1512,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_yellow_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="yellow" position="1520,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
 <ePixmap position="1690,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_blue_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="blue" position="1698,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>

 <!-- Inline Portal Check notice. It lives inside this screen; no modal black screen. -->
 <widget name="check_notice_panel" position="1010,790" size="390,190" alphatest="blend" scale="1" transparent="1" zPosition="20"/>
 <widget name="check_notice_title" position="1030,807" size="350,36" font="Regular;25" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="21" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="check_notice_message" position="1030,850" size="350,60" font="Regular;19" halign="center" valign="center" foregroundColor="#f4f8fb" transparent="1" zPosition="21" noWrap="0" shadowColor="#000000" shadowOffset="1,1"/>
 <widget name="check_notice_button" position="1120,923" size="170,42" alphatest="blend" scale="1" transparent="1" zPosition="21"/>
 <widget name="check_notice_ok" position="1128,923" size="154,42" font="Regular;19" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="22" shadowColor="#000000" shadowOffset="1,1"/>

 <!-- One-time setup stays inline over the live Portal List. No blur/dim layer. -->
</screen>"""


# Free Portal / Free Xtream keep the proven rows and source badges, but use a
# dedicated compact footer. The old verbose key-hint line is not rendered.
PORTAL_LIBRARY_SETTINGS_SKIN = """<screen name="PortalLibraryListScreen" position="center,center" size="1920,1080" backgroundColor="#02070b" flags="wfNoBorder">
 <widget name="ambient_bg" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="1"/>
 <widget name="hero_backdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" transparent="1" zPosition="2"/>
 <widget name="list" position="50,20" size="620,1040" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
 <widget name="source_badges" position="1430,758" size="430,240" foregroundColor="#ffffff" foregroundColorSelected="#ffffff" selectionPixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/portal_selection_clear.png" scrollbarMode="showNever" transparent="1" zPosition="7"/>
 <widget name="portal_page" position="1156,1001" size="704,27" font="Regular;17" halign="right" valign="center" foregroundColor="#9fc6df" transparent="1" zPosition="8" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>

 <!-- Compact library actions aligned directly below the Page/Source line. -->
 <ePixmap position="1156,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_red_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="red" position="1164,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
 <ePixmap position="1334,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_green_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="green" position="1342,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
 <ePixmap position="1512,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_yellow_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="yellow" position="1520,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
 <ePixmap position="1690,1034" size="170,42" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/us6521_key_blue_170x42.png" alphatest="blend" scale="1" zPosition="8"/>
 <widget name="blue" position="1698,1034" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="9" shadowColor="#000000" shadowOffset="1,1"/>
</screen>"""


class PortalManagerPremiumScreen(Screen):
    """Premium Portal Manager using the approved Settings row grammar.

    The manager exposes only non-duplicated portal utilities.  Destructive
    delete/disable actions remain on the main Portal List color keys, while the
    manager's multi-select flow is dedicated to moving portals.
    """
    skin = PORTAL_MANAGER_PREMIUM_SKIN
    ROW_W = 430
    ROW_H = 80

    _ACTIONS = (
        ("Free Portal Library", "portal_library", "Browse the bundled Portal catalog and import selected entries", "settings_icons/web_access.png"),
        ("Free Xtream Library", "xtream_library", "Browse the bundled Xtream catalog and import selected entries", "settings_icons/channel_list.png"),
        ("Backup & Restore", "backup_restore", "Backup or restore all portals, settings and user state", "settings_icons/backup.png"),
        ("Re-enable disabled portal", "enable_disabled", "Restore a disabled portal", "settings_icons/recovery.png"),
        ("Rename portal", "rename", "Rename the selected portal", "settings_icons/advanced.png"),
        ("Edit URL / MAC", "edit", "Edit portal address or MAC", "settings_icons/web_access.png"),
        ("MAG device profile", "device_profile", "Choose the MAG device profile", "settings_icons/playback.png"),
        ("Export Live bouquet + EPG", "export_bouquet", "Create Live bouquet and EPG source", "settings_icons/epg.png"),
        ("Remove exported bouquet + EPG", "unexport_bouquet", "Remove the exported Live integration", "settings_icons/clean.png"),
        ("Duplicate with another MAC", "duplicate", "Clone this portal with another MAC", "settings_icons/backup.png"),
        ("Move portals", "move_portals", "Select one or more portals, then press MENU to move them", "settings_icons/auto_switch.png"),
        ("Clear portal data", "purge_data", "Clear plugin-owned data but keep portal", "settings_icons/cache.png"),
    )

    def __init__(self, session, parent):
        Screen.__init__(self, session)
        self.parent = parent
        self._mode = "actions"
        self._visual_index = -1
        self._rebuilding = False
        self._row_asset = asset("fixed_master_r63/utility_row.png")
        self._row_selected_asset = asset("fixed_master_r63/utility_row_selected.png")
        self._value_color = int("74d8ff", 16)
        try:
            _fixed=fixed_settings_rows() or {}
            _n=str(_fixed.get("normal") or "");_s=str(_fixed.get("selected") or "")
            if _n and os.path.isfile(_n):self._row_asset=_n
            if _s and os.path.isfile(_s):self._row_selected_asset=_s
            self._value_color=int(_fixed.get("value_color") or fixed_value_color(self._value_color))
        except Exception as exc:optional_failure("ui.portal_manager_fixed_init",exc)
        self._multi_profiles = []
        self._multi_selected = set()
        self._visible_actions = []
        self._last_summary = ""
        self["ambient_bg"] = Pixmap()
        self["hero_backdrop"] = Pixmap()
        self["list"] = IconMenuList([], width=self.ROW_W, item_height=self.ROW_H, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self["status"] = Label("")
        self["context_title"] = Label("")
        self["context_subtitle"] = Label("")
        self["context_list"] = IconMenuList([], width=650, item_height=80, icon_size=0, primary_font=22, secondary_font=16, row_style="settings_dialog")
        self._context_active = False
        self._context_callback = None
        self._context_choices = []
        self._context_last_idx = 0
        self._context_row_asset = None
        self._context_selected_asset = None
        self["list"].onSelectionChanged.append(self._selection_changed)
        actions = {
            "cancel": self._cancel, "ok": self._ok, "menu": self._menu,
            "up": lambda: self._move("up"), "down": lambda: self._move("down"),
            "left": lambda: self._move("left"), "right": lambda: self._move("right"),
            "red": self._red, "green": self._green, "yellow": self._yellow,
            "1": lambda: self._number(1), "2": lambda: self._number(2), "3": lambda: self._number(3),
            "4": lambda: self._number(4), "5": lambda: self._number(5), "6": lambda: self._number(6),
            "7": lambda: self._number(7), "8": lambda: self._number(8), "9": lambda: self._number(9),
            "0": lambda: self._number(0),
        }
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions","UltraStalkerMenuActions", "NumberActions", "DirectionActions"], actions, -1)
        self.onLayoutFinish.append(self._layout_ready)
        self.onClose.append(self._cleanup)
        self._context_hide_widgets()
        self._render_actions(0)

    def _cleanup(self):
        try:
            callbacks = self["list"].onSelectionChanged
            if self._selection_changed in callbacks:
                callbacks.remove(self._selection_changed)
        except Exception as exc:
            optional_failure("ui.portal_manager_cleanup", exc)
        try:
            callbacks = self["context_list"].onSelectionChanged
            if self._context_selection_changed in callbacks:
                callbacks.remove(self._context_selection_changed)
        except Exception as exc:
            optional_failure("ui.portal_manager_context_cleanup", exc)

    def _hero_visuals(self):
        prepared = _portal_category_backdrop()
        ambient = ""
        return prepared, ambient

    def _layout_ready(self):
        try:cleanup_legacy_application_outputs()
        except Exception as exc:optional_failure("ui.fixed_adaptive_cleanup_portal_manager",exc)
        prepared, ambient = self._hero_visuals()
        try:
            if ambient:
                self["ambient_bg"].instance.setPixmapFromFile(ambient); self["ambient_bg"].show()
            else:
                self["ambient_bg"].hide()
        except Exception as exc:
            optional_failure("ui.portal_manager_ambient", exc)
        try:
            if prepared and os.path.isfile(prepared):
                self["hero_backdrop"].instance.setPixmapFromFile(prepared); self["hero_backdrop"].show()
        except Exception as exc:
            optional_failure("ui.portal_manager_backdrop", exc)
        try:
            chrome = fixed_settings_rows() or {}
            normal = str(chrome.get("normal") or "")
            selected = str(chrome.get("selected") or "")
            if normal and os.path.isfile(normal):
                self._row_asset = normal
            if selected and os.path.isfile(selected):
                self._row_selected_asset = selected
            self._value_color = int(chrome.get("value_color") or fixed_value_color(self._value_color))
            _accent=parseColor("#%06x" % (int(self._value_color)&0xFFFFFF))
            for _name,_size in (("context_subtitle",19),("status",19)):
                try:
                    _inst=self[_name].instance
                    if _inst is not None:
                        _inst.setForegroundColor(_accent);_inst.setFont(gFont("Regular",_size))
                except Exception:pass
        except Exception as exc:
            optional_failure("ui.portal_manager_material", exc)
        try:
            if self["list"].instance is not None:
                self["list"].instance.setSelectionEnable(0)
                self["list"].instance.setTransparent(1)
                self["list"].instance.setScrollbarMode(2)
        except Exception as exc:
            optional_failure("ui.portal_manager_list_native", exc)
        # Repaint once so the dynamic Settings assets replace the safe fallback.
        if self._mode == "actions":
            self._render_actions(self._safe_index())
        elif self._mode == "multi":
            self._render_multi(self._safe_index())

    def _safe_index(self):
        try:
            return int(self["list"].getSelectedIndex() or 0)
        except Exception:
            return 0

    def _move(self, direction):
        target = self["context_list"] if self._context_active else self["list"]
        try:
            if direction == "up": target.wrap_up()
            elif direction == "down": target.wrap_down()
            elif direction == "left": target.page_left()
            elif direction == "right": target.page_right()
        except Exception as exc:
            optional_failure("ui.portal_manager_nav", exc)

    def _context_geom(self,name,x,y,w,h):
        try:
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst=self[name].instance
            if inst is not None:
                inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
                inst.resize(eSize(max(1,int(round(w*sx))),max(1,int(round(h*sy)))))
        except Exception as exc:optional_failure("ui.portal_inline_context_geom",exc)

    def _context_hide_widgets(self):
        for name in ("context_title","context_subtitle","context_list"):
            try:self[name].hide()
            except Exception as exc:optional_failure("ui.portal_inline_context_hide",exc)

    def _context_compact_episode_assets(self,card_w):
        # Exact Settings floating-row rule: reuse the same approved normal/selected
        # assets and resize horizontally only, preserving rounded ends and glow.
        card_w=max(300,min(560,int(card_w or 426)))
        normal=str(getattr(self,"_row_asset","") or "")
        selected=str(getattr(self,"_row_selected_asset","") or "")
        if card_w==426:return normal,selected
        out=[]
        for src,kind in ((normal,"normal"),(selected,"selected")):
            if not (src and os.path.isfile(src)):
                out.append(src);continue
            try:
                stamp=int(os.path.getmtime(src))
                sig=hashlib.sha1((src+"|"+str(stamp)+"|"+str(card_w)+"|floating-choice-v1").encode("utf-8","ignore")).hexdigest()[:16]
                dst=os.path.join(os.path.dirname(src),"dyn_settings_context_%s_%s_%d.png"%(sig,kind,card_w))
                if not (os.path.isfile(dst) and os.path.getsize(dst)>64):
                    from PIL import Image as _SettingsImage
                    im=_SettingsImage.open(src).convert("RGBA")
                    sw,sh=im.size;target_h=72
                    if sh!=target_h:
                        resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                        im=im.resize((sw,target_h),resample);sw,sh=im.size
                    cap=max(36,min(72,sw//4,(card_w-16)//2))
                    resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                    if card_w>cap*2+8 and sw>cap*2+8:
                        canvas=_SettingsImage.new("RGBA",(card_w,target_h),(0,0,0,0))
                        left=im.crop((0,0,cap,target_h));right=im.crop((sw-cap,0,sw,target_h))
                        center=im.crop((cap,0,sw-cap,target_h)).resize((card_w-cap*2,target_h),resample)
                        canvas.paste(left,(0,0));canvas.paste(center,(cap,0));canvas.paste(right,(card_w-cap,0))
                    else:canvas=im.resize((card_w,target_h),resample)
                    tmp=dst+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG");os.replace(tmp,dst)
                out.append(dst)
            except Exception as exc:
                optional_failure("ui.portal_context_episode_resize",exc);out.append(src)
        return (out+[None,None])[:2]

    def _context_prepare_floating_choices(self,title,subtitle,labels,count,show_text=False):
        label_px=max([_portal_settings_text_width_px(x,22) for x in labels] or [220])
        card_w=max(300,min(560,label_px+58));list_w=card_w+8;row_h=80
        visible=max(1,min(5,int(count or 1)));region_top,region_h=590,400
        list_h=visible*row_h;list_x=1850-list_w;list_y=region_top+max(0,(region_h-list_h)//2)
        if show_text:
            heading_w=min(740,max(420,_portal_settings_text_width_px(str(subtitle or title or ""),17)+24))
            heading_x=1850-heading_w
            self._context_geom("context_title",heading_x,max(600,list_y-92),heading_w,36)
            self._context_geom("context_subtitle",heading_x,max(638,list_y-54),heading_w,88)
            list_y=max(list_y,748)
        self._context_geom("context_list",list_x,list_y,list_w,list_h)
        self._context_row_asset,self._context_selected_asset=self._context_compact_episode_assets(card_w)
        try:
            if self["context_list"].instance is not None:
                self["context_list"].instance.setSelectionEnable(0);self["context_list"].instance.setTransparent(1)
                try:self["context_list"].instance.setScrollbarMode(2)
                except Exception:pass
            self["context_list"].row_width=list_w
            self["context_list"].set_layout(row_h,0,22,16,row_style="settings_dialog")
        except Exception as exc:optional_failure("ui.portal_floating_context_layout",exc)

    def _context_open_choice(self,callback,title,choices,selection=0,subtitle="Choose an option",show_text=False):
        self._context_hide_widgets();self._context_active=True;self._context_callback=callback;self._context_choices=list(choices or [])
        labels=[_(str(c[0])) if isinstance(c,(tuple,list)) and c else _(str(c)) for c in self._context_choices]
        title_text=_(str(title or "Settings"));subtitle_text=_(str(subtitle or ""))
        self._context_prepare_floating_choices(title_text,subtitle_text,labels,len(labels),show_text=show_text)
        self["context_title"].setText(title_text);self["context_subtitle"].setText(subtitle_text)
        selection=max(0,min(int(selection or 0),len(self._context_choices)-1)) if self._context_choices else 0
        rows=[]
        for i,c in enumerate(self._context_choices):
            details={"selected":i==selection,"row_asset":self._context_row_asset,"row_selected_asset":self._context_selected_asset,"meta":""}
            rows.append((labels[i],None,c,details))
        self["context_list"].set_icon_rows(rows)
        try:self["context_list"].moveToIndex(selection)
        except Exception as exc:optional_failure("ui.portal_inline_context_index",exc)
        self._context_last_idx=selection
        try:
            if self._context_selection_changed not in self["context_list"].onSelectionChanged:self["context_list"].onSelectionChanged.append(self._context_selection_changed)
        except Exception as exc:optional_failure("ui.portal_inline_context_hook",exc)
        self["context_list"].show()
        if show_text:
            self["context_title"].show();self["context_subtitle"].show()

    def _context_selection_changed(self):
        if not self._context_active:return
        try:idx=self["context_list"].getSelectedIndex()
        except Exception:return
        old=getattr(self,"_context_last_idx",idx)
        if idx==old:return
        self._context_last_idx=idx
        for j in set((old,idx)):
            if 0<=j<len(self._context_choices):
                c=self._context_choices[j];label=str(c[0]) if isinstance(c,(tuple,list)) and c else str(c)
                details={"selected":j==idx,"row_asset":self._context_row_asset,"row_selected_asset":self._context_selected_asset,"meta":""}
                self["context_list"].update_icon_row(j,(label,None,c,details))
        try:self["context_list"].l.invalidate()
        except Exception as exc:optional_failure("ui.portal_inline_context_refresh",exc)

    def _context_accept(self):
        try:idx=self["context_list"].getSelectedIndex()
        except Exception:idx=-1
        value=self._context_choices[idx] if 0<=idx<len(self._context_choices) else None
        self._context_close(value,False)

    def _context_close(self,value=None,cancelled=False):
        callback=self._context_callback
        self._context_active=False;self._context_callback=None;self._context_choices=[]
        self._context_hide_widgets()
        if callback is not None:
            try:callback(None if cancelled else value)
            except Exception as exc:optional_failure("ui.portal_inline_context_callback",exc)

    def _selected_portal(self):
        try:
            idx = self.parent._index()
            if idx is not None and 0 <= idx < len(self.parent.profiles):
                return self.parent.profiles[idx]
        except Exception as exc:
            optional_failure("ui.portal_selected",exc)
        return None

    @staticmethod
    def _portal_name(profile, index=0):
        p = profile if isinstance(profile, dict) else {}
        if str(p.get("source_type") or "").lower() == "m3u":
            return str(p.get("name") or (_("M3U Playlist %d") % (index + 1)))
        return str(p.get("name") or (_("Portal Server %d") % (index + 1)))

    def _live_integration_exported(self, profile):
        """Return True when this portal already owns Live export state.

        Portal Manager only needs this while it is open, so keep the bouquet
        module lazy.  Treat either a committed registry row or surviving owned
        files as exported; in a partial/stale state the useful action is still
        Remove, because unexport_live_integration() is the cleanup path.
        """
        if not isinstance(profile, dict) or not profile:
            return False
        try:
            from .core.bouquets import _profile_key, _read_registry, _bouquet_paths
            pkey = _profile_key(profile)
            registry = _read_registry() or {}
            if isinstance(registry.get(pkey), dict):
                return True
            paths = _bouquet_paths(pkey) or {}
            for name in ("bouquet", "epg_channels", "epg_source", "xmltv_cache"):
                path = str(paths.get(name) or "")
                if path and os.path.exists(path):
                    return True
        except Exception as exc:
            optional_failure("ui.portal_manager_live_export_state", exc)
        return False

    def _action_entries(self):
        """Build the visible Portal Manager rows for the selected portal."""
        profile = self._selected_portal()
        exported = self._live_integration_exported(profile) if profile else False
        try:
            has_disabled = bool(load_disabled_profiles())
        except Exception as exc:
            optional_failure("ui.portal_manager_disabled_state", exc)
            has_disabled = False
        rows = []
        for entry in self._ACTIONS:
            action = entry[1] if len(entry) > 1 else ""
            if action == "enable_disabled" and not has_disabled:
                continue
            if action == "device_profile" and (not profile or str(profile.get("source_type") or "stalker").lower() == "m3u"):
                continue
            if action == "duplicate" and (not profile or str(profile.get("source_type") or "stalker").lower() == "m3u"):
                continue
            if action == "export_bouquet" and exported:
                continue
            if action == "unexport_bouquet" and not exported:
                continue
            rows.append(entry)
        return rows

    def _action_value(self, action):
        profile = self._selected_portal() or {}
        if action == "enable_disabled":
            try: return _("%d disabled") % len(load_disabled_profiles())
            except Exception: return _("Disabled portals")
        if action == "rename": return self._portal_name(profile) if profile else _("No portal selected")
        if action == "edit":
            raw = str(profile.get("portal") or "")
            return raw[:30] if raw else _("No portal selected")
        if action == "device_profile":
            if not profile: return _("No portal selected")
            device = str(profile.get("device_profile") or "Auto")
            return _("Auto") if device.lower() == "auto" else device.upper()
        if action == "move_portals": return _("%d portals • Multi-select") % len(getattr(self.parent, "profiles", []) or [])
        return ""

    @staticmethod
    def _settings_icon(icon_rel):
        """Use the exact 40x40 icon copies used by Settings Test8.

        Enigma2 MultiContent clips pixmaps instead of scaling them; handing it
        the authored 256x256 source icon is why Test30 looked icon-less on the
        receiver.
        """
        try:
            base = os.path.basename(str(icon_rel or ""))
            compact = asset("settings_icons_40/" + base) if base else ""
            if compact and os.path.isfile(compact):
                return compact
        except Exception as exc:
            optional_failure("ui.portal_icon_compact",exc)
        try:
            return asset(icon_rel)
        except Exception:
            return ""

    def _render_actions(self, selected=0):
        self._mode = "actions"
        entries = self._action_entries()
        self._visible_actions = list(entries)
        selected = max(0, min(int(selected or 0), max(0, len(entries) - 1))) if entries else 0
        rows = []
        for idx, (title, action, desc, icon_rel) in enumerate(entries):
            value = self._action_value(action)
            meta = {
                "selected": bool(idx == selected), "value": value,
                "center_title": not bool(value),
                "row_asset": self._row_asset, "row_selected_asset": self._row_selected_asset,
                "value_color": self._value_color,
                "utility_accent_strong": True,
            }
            rows.append((_(title), self._settings_icon(icon_rel), action, meta))
        self._rebuilding = True
        try:
            self["list"].set_icon_rows(rows)
            if rows: self["list"].moveToIndex(selected)
        finally:
            self._rebuilding = False
        self._visual_index = selected
        self._update_status()

    def _render_multi(self, selected=0):
        self._mode = "multi"
        self._multi_profiles = list(load_profiles())
        valid = set(range(len(self._multi_profiles)))
        self._multi_selected.intersection_update(valid)
        selected = max(0, min(int(selected or 0), max(0, len(self._multi_profiles) - 1))) if self._multi_profiles else 0
        rows = []
        for idx, profile in enumerate(self._multi_profiles):
            picked = idx in self._multi_selected
            is_m3u = str(profile.get("source_type") or "").lower() == "m3u"
            identity = str(profile.get("mac") or "M3U")
            value = (((_("SELECTED") + " • ") if picked else "") + identity)[:32]
            meta = {
                "selected": bool(idx == selected), "value": value,
                "row_asset": self._row_asset, "row_selected_asset": self._row_selected_asset,
                "value_color": self._value_color,
                "utility_accent_strong": True,
            }
            icon_rel = "settings_icons/multi_search.png" if picked else ("settings_icons/web_access.png" if not is_m3u else "settings_icons/channel_list.png")
            rows.append((self._portal_name(profile, idx), self._settings_icon(icon_rel), profile, meta))
        self._rebuilding = True
        try:
            self["list"].set_icon_rows(rows)
            if rows: self["list"].moveToIndex(selected)
        finally:
            self._rebuilding = False
        self._visual_index = selected
        self._update_status()

    def _selection_changed(self):
        if self._rebuilding:
            return
        idx = self._safe_index()
        if idx != self._visual_index:
            old = self._visual_index
            self._visual_index = idx
            # Only repaint the two affected rows.  Do not rebuild the whole list
            # on remote navigation; this is the same light interaction contract
            # used elsewhere after the restart/double-key work.
            if self._mode == "actions":
                entries = list(self._visible_actions)
                for j in set((old, idx)):
                    if 0 <= j < len(entries):
                        title, action, _desc, icon_rel = entries[j]
                        value = self._action_value(action)
                        meta = {"selected": j == idx, "value": value, "center_title": not bool(value), "row_asset": self._row_asset, "row_selected_asset": self._row_selected_asset, "value_color": self._value_color, "utility_accent_strong": True}
                        self["list"].update_icon_row(j, (_(title), self._settings_icon(icon_rel), action, meta))
            elif self._mode == "multi":
                for j in set((old, idx)):
                    if 0 <= j < len(self._multi_profiles):
                        profile = self._multi_profiles[j]; picked = j in self._multi_selected
                        is_m3u = str(profile.get("source_type") or "").lower() == "m3u"
                        value = (((_("SELECTED") + " • ") if picked else "") + str(profile.get("mac") or "M3U"))[:32]
                        meta = {"selected": j == idx, "value": value, "row_asset": self._row_asset, "row_selected_asset": self._row_selected_asset, "value_color": self._value_color, "utility_accent_strong": True}
                        icon_rel = "settings_icons/multi_search.png" if picked else ("settings_icons/web_access.png" if not is_m3u else "settings_icons/channel_list.png")
                        self["list"].update_icon_row(j, (self._portal_name(profile, j), self._settings_icon(icon_rel), profile, meta))
        self._update_status()

    def _update_status(self):
        if self._mode == "multi":
            self["status"].setText(_("Move %d selected • ARROWS choose destination • GREEN Place Here • BACK Cancel") % len(self._multi_selected))
            return
        idx = self._safe_index()
        entries = list(self._visible_actions)
        desc = _(entries[idx][2]) if 0 <= idx < len(entries) else _("Choose a portal tool")
        selected = self._selected_portal()
        portal_name = self._portal_name(selected) if selected else _("No portal selected")
        suffix = (" • " + self._last_summary) if self._last_summary else ""
        self["status"].setText(_("Portal Manager • %s • %s • ARROWS Navigate • OK Select • BACK Portals%s") % (portal_name, desc, suffix))

    def _ok(self):
        if self._context_active:
            self._context_accept();return
        idx = self._safe_index()
        if self._mode == "actions":
            entries = list(self._visible_actions)
            if not (0 <= idx < len(entries)):
                return
            title, action, _desc, _icon = entries[idx]
            if action == "portal_library":
                self.session.open(PortalLibraryListScreen, "portal")
                return
            if action == "xtream_library":
                self.session.open(PortalLibraryListScreen, "xtream")
                return
            profile=self._selected_portal()
            if action == "backup_restore":
                self._context_open_choice(self._backup_restore_selected,_('Backup & Restore'),[(_("Backup all settings & portals"),"backup_all"),(_("Restore backup"),"restore")],0,_('Choose a backup or restore action'))
                return
            if action == "enable_disabled":
                disabled=list(load_disabled_profiles())
                if not disabled:
                    self.session.open(SettingsGlassNoticeScreen,_('Re-enable portal'),_('No disabled portals.'),False);return
                rows=[(((row.get("name") or row.get("portal") or "Portal")+"  •  "+str(row.get("mac") or "")),row) for row in disabled]
                self._context_open_choice(self._enable_disabled_selected,_('Re-enable portal'),rows,0,_('Choose a disabled portal to enable'))
                return
            if action == "device_profile":
                if not profile:return
                self.parent._editing_profile=profile
                current=str(profile.get("device_profile") or "auto").lower();choices=[(_("Auto"),"auto"),("MAG250","mag250"),("MAG254","mag254"),("MAG256","mag256")];selection=next((i for i,x in enumerate(choices) if x[1]==current),0)
                self._context_open_choice(self._device_profile_selected,_('MAG device profile'),choices,selection,_('Choose the device profile for this portal'))
                return
            if action == "rename":
                if not profile:return
                self.parent._editing_profile=profile
                self.session.openWithCallback(self._rename_finished,SettingsGlassInputScreen,title=_('Portal name'),text=str(profile.get('name') or _('Portal Server')),maxSize=80)
                return
            if action == "edit":
                if not profile:return
                self.parent._editing_profile=profile
                self.session.openWithCallback(self._edit_url_finished,SettingsGlassInputScreen,title=_('Portal / M3U URL'),text=str(profile.get('portal') or 'https://'),maxSize=250)
                return
            if action == "duplicate":
                if not profile:return
                self.parent._duplicating_profile=profile
                self.session.openWithCallback(self._duplicate_finished,SettingsGlassInputScreen,title=_('MAC for duplicate'),text='00:1A:79:',maxSize=17)
                return
            if action == "purge_data":
                if not profile:return
                self.parent._purge_profile=profile
                self._context_open_choice(self._purge_selected,_('Clear portal data'),[(_('Yes'),True),(_('No'),False)],1,_('Clear plugin-owned data for this portal?  Favorites, History, Resume, exported bouquet/EPG and related timers are removed; the portal profile is kept.'),show_text=True)
                return
            if action == "move_portals":
                # One clean entry point: choose Move portals, select the targets,
                # then MENU starts placement directly. No second Move row.
                anchor = min(self._multi_selected) if self._multi_selected else 0
                self._render_multi(anchor); return
            self.close((title, action))
            return
        if self._mode == "multi":
            if not (0 <= idx < len(self._multi_profiles)):
                return
            if idx in self._multi_selected: self._multi_selected.remove(idx)
            else: self._multi_selected.add(idx)
            self._render_multi_row(idx)
            # Multi-select should stay fast: after OK toggles the current portal,
            # move focus straight to the next portal so several targets can be
            # marked with consecutive OK presses.  Keep focus on the last row.
            if idx + 1 < len(self._multi_profiles):
                try:
                    self["list"].moveToIndex(idx + 1)
                except Exception as exc:
                    optional_failure("ui.portal_manager_multi_select_advance", exc)
            self._update_status()
            return

    def _backup_restore_selected(self,choice):
        if not choice:return
        action=choice[1]
        if action=="backup_all":
            self.parent._portal_backup_action(choice);return
        backups=list_backups()
        if not backups:
            self.session.open(SettingsGlassNoticeScreen,_('Backup & Restore'),_('No backups found on HDD.\n\nExpected folder: /media/hdd/UltraStalker/Backup/'),False);return
        rows=[]
        for path in backups:
            try:
                manifest=inspect_backup(path);kind='FULL' if manifest.get('contains_secrets') else 'LEGACY'
                label=os.path.basename(path)+'  •  '+str(manifest.get('plugin_version') or 'unknown')+'  •  '+kind
            except Exception:label=os.path.basename(path)+'  •  INVALID'
            rows.append((label,path))
        self._context_open_choice(self._restore_backup_selected,_('Choose backup to restore'),rows,0,_('Select a saved Ultra Stalker backup'))

    def _restore_backup_selected(self,choice):
        if not choice:return
        path=choice[1]
        try:inspect_backup(path)
        except Exception as exc:
            self.session.open(SettingsGlassNoticeScreen,_('Invalid Backup'),_('Invalid backup: %s')%exc,False);return
        self.parent._portal_restore_path=path
        self._context_open_choice(self._restore_backup_confirmed,_('Restore backup'),[(_('Yes'),True),(_('No'),False)],1,_('Restore this backup completely? The selected backup will be restored directly. No additional backup copy will be created.'),show_text=True)

    def _restore_backup_confirmed(self,choice):
        if choice and bool(choice[1]):self.parent._portal_restore_confirmed(True)

    def _enable_disabled_selected(self,choice):
        if choice:
            current=self._safe_index()
            self.parent._disabled_portal_selected(choice)
            self._last_summary=_("Portal enabled")
            self._render_actions(current)

    def _device_profile_selected(self,choice):
        if choice:self.parent._portal_device_selected(choice);self._last_summary=_("Portal updated");self._update_status()

    def _rename_finished(self,value):
        self.parent._portal_renamed(value);self._last_summary=_("Portal renamed") if value is not None else '';self._update_status()

    def _edit_url_finished(self,value):
        self.parent._portal_edit_url(value);self._update_status()

    def _duplicate_finished(self,value):
        self.parent._portal_duplicate_mac(value);self._last_summary=_("Portal duplicated") if value else '';self._update_status()

    def _purge_selected(self,choice):
        if choice and bool(choice[1]):self.parent._purge_profile_confirmed(True);self._last_summary=_("Portal data cleared; portal profile kept");self._update_status()

    def _render_multi_row(self, idx):
        if not (0 <= idx < len(self._multi_profiles)):
            return
        profile = self._multi_profiles[idx]; picked = idx in self._multi_selected
        is_m3u = str(profile.get("source_type") or "").lower() == "m3u"
        value = (((_("SELECTED") + " • ") if picked else "") + str(profile.get("mac") or "M3U"))[:32]
        meta = {"selected": idx == self._safe_index(), "value": value, "row_asset": self._row_asset, "row_selected_asset": self._row_selected_asset, "value_color": self._value_color, "utility_accent_strong": True}
        icon_rel = "settings_icons/multi_search.png" if picked else ("settings_icons/web_access.png" if not is_m3u else "settings_icons/channel_list.png")
        self["list"].update_icon_row(idx, (self._portal_name(profile, idx), self._settings_icon(icon_rel), profile, meta))

    def _menu(self):
        if self._context_active:
            return
        # Move portals is now a single flow: select targets, then MENU starts
        # placement directly instead of bouncing back through a second row.
        if self._mode == "multi":
            if not self._multi_selected:
                self._last_summary = _("Select portals first, then MENU → Move")
                self._update_status()
                return
            targets = [self._multi_profiles[i] for i in sorted(self._multi_selected) if 0 <= i < len(self._multi_profiles)]
            if not targets:
                self._last_summary = _("No selected portals are available to move")
                self._update_status()
                return
            try:
                self.parent._begin_bulk_move(targets)
            except Exception as exc:
                optional_failure("ui.portal_manager_begin_bulk_move", exc)
                self._last_summary = _("No selected portals are available to move")
                self._update_status()
                return
            self.close(None)
            return
        self._cancel()

    def _cancel(self):
        if self._context_active:
            self._context_close(None,True);return
        if self._mode == "multi":
            self._multi_selected = set()
            move_idx=next((i for i,row in enumerate(self._visible_actions) if len(row)>1 and row[1]=="move_portals"),0)
            self._render_actions(move_idx); return
        self.close(None)

    def _yellow(self):
        if self._context_active:
            return
        if self._mode == "multi":
            self._multi_selected = set(range(len(self._multi_profiles))); self._render_multi(self._safe_index())

    def _red(self):
        if self._context_active:
            self._context_close(None,True);return
        if self._mode == "multi":
            self._multi_selected = set(); self._render_multi(self._safe_index())
        elif self._mode == "actions":
            self.close(None)

    def _green(self):
        if self._context_active:
            self._context_accept();return
        if self._mode == "multi":
            self._ok()

    def _number(self, digit):
        if self._context_active:
            return
        if self._mode != "actions":
            return
        # Preserve the old ChoiceBox shortcut contract: 1..9 select rows 1..9,
        # 0 selects row 10.  Actions beyond ten remain available by scrolling.
        idx = 9 if int(digit) == 0 else int(digit) - 1
        if 0 <= idx < min(10, len(self._visible_actions)):
            try: self["list"].moveToIndex(idx)
            except Exception as exc: optional_failure("ui.portal_manager_number", exc)
            self._ok()



class PortalListScreen(Screen, AsyncScreenMixin, ImageLoaderMixin, TransitionMixin):
    skin = PORTAL_SELECTION_SETTINGS_SKIN
    PORTAL_ROW_W = 620
    PORTAL_ROW_H = 80

    def __init__(self, session):
        _perf_t0 = time.monotonic()
        LOG.info("PERF25 portals init_enter mono_ms=%d", int(_perf_t0 * 1000))
        Screen.__init__(self, session)
        self._async_init()
        self.onClose.append(self._stop_async)
        self.onClose.append(self._image_stop)
        _phase = time.monotonic()
        self.profiles = load_profiles()
        LOG.info("PERF25 portals profiles_loaded elapsed_ms=%d count=%d", int((time.monotonic() - _phase) * 1000), len(self.profiles or []))
        # A brand-new plugin session must start content grids from page 1.
        # Test69 Lean: navigation reset is session-RAM scoped by begin_plugin_launch().
        # Do NOT rewrite + fsync one state JSON per portal every time the plugin opens.
        # Persistent favorites/resume/home state remain untouched and Grid navigation
        # cannot leak across launches because its key includes current_plugin_launch().
        self._portal_old_service = _plugin_original_service_getter() if _plugin_service_captured_getter() else None
        try:self.session.nav.stopService()
        except Exception as exc:optional_failure("ui",exc)
        self.onClose.append(self._restore_background_service)
        initial_warning = _("Ready")
        self["status"] = Label(initial_warning)
        self["page_bg"] = Pixmap()
        self["ambient_bg"] = Pixmap()
        self["hero_backdrop"] = Pixmap()
        self["list"] = IconMenuList([], width=self.PORTAL_ROW_W, item_height=self.PORTAL_ROW_H, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self["source_badges"] = IconMenuList([], width=430, item_height=self.PORTAL_ROW_H, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self["portal_page"] = Label("")
        self["check_progress"] = Label("")
        self._portal_page_text = ""
        self._portal_footer_name = ""
        self._portal_footer_connection = ""
        self._portal_check_progress_text = ""
        self._portal_row_asset = asset("fixed_master_r63/utility_row.png")
        self._portal_row_selected_asset = asset("fixed_master_r63/utility_row_selected.png")
        self._portal_value_color = int("74d8ff", 16)
        # R64: bind the frozen application chrome before the first portal paint.
        try:
            _fixed=fixed_settings_rows() or {}
            _n=str(_fixed.get("normal") or "");_s=str(_fixed.get("selected") or "")
            if _n and os.path.isfile(_n):self._portal_row_asset=_n
            if _s and os.path.isfile(_s):self._portal_row_selected_asset=_s
            self._portal_value_color=int(_fixed.get("value_color") or fixed_value_color(self._portal_value_color))
        except Exception as exc:optional_failure("ui.portal_fixed_init",exc)
        self._portal_page_size = 13
        self._portal_page = 1
        self._portal_visual_index = 0
        self._portal_rebuilding = False
        self["red"] = Label(_("Delete portal"))
        self["green"] = Label(_("Select"))
        self["yellow"] = Label(_("Check All"))
        self["blue"] = Label(_("Disable"))
        self["footer_connection"] = Label("")
        self["check_notice_panel"] = Pixmap()
        self["check_notice_title"] = Label("")
        self["check_notice_message"] = Label("")
        self["check_notice_button"] = Pixmap()
        self["check_notice_ok"] = Label(_("OK"))
        self._check_notice_visible = False
        for _notice_name in ("check_notice_panel", "check_notice_title", "check_notice_message", "check_notice_button", "check_notice_ok"):
            try:self[_notice_name].hide()
            except Exception:pass
        # R181: onboarding is a disposable child screen. PortalList carries zero
        # onboarding Pixmaps/Labels while normal navigation is running.
        self._portal_select_mode = False
        self._portal_selected = set()
        self._onboarding_prompted_this_screen = False
        self._reorder_mode = False
        # Bulk placement mode is entered from Portal Manager -> Select portals
        # -> MENU -> Move.  Network/account state is untouched; only the local
        # saved profile order changes when GREEN confirms the destination.
        self._bulk_move_mode = False
        self._bulk_move_profiles = []
        self._portal_progress_jobs = queue.Queue()
        self._portal_progress_timer = eTimer()
        self._portal_progress_conn = None
        try:
            self._portal_progress_conn = self._portal_progress_timer.timeout.connect(self._drain_portal_progress)
        except Exception:
            self._portal_progress_timer.callback.append(self._drain_portal_progress)
        self._portal_progress_active=False
        self.onClose.append(self._stop_portal_progress)
        self.onClose.append(self._stop_portal_ui_hooks)
        self._image_init("preview", (310, 245), {"portal": ""}, None)
        self.onLayoutFinish.append(self._layout_ready)
        try:self.onShown.append(self._portal_list_shown)
        except Exception as exc:optional_failure("ui",exc)
        self["list"].onSelectionChanged.append(self._selection_changed)
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions","UltraStalkerMenuActions", "NumberActions", "InfoActions", "DirectionActions"], {
            "cancel": self._portal_cancel, "green": self._portal_green, "red": self._portal_red,
            "yellow": self._portal_yellow, "blue": self._portal_blue, "ok": self._portal_ok,
            "up": self._portal_up, "down": self._portal_down, "left": self._portal_prev_page, "right": self._portal_next_page,
            "menu": self._portal_menu, "info": self._portal_info,
        }, -1)
        self.refresh()
        LOG.info("PERF25 portals init_done elapsed_ms=%d", int((time.monotonic() - _perf_t0) * 1000))

    def _fit_portal_color_keys(self):
        # R48: actual receiver glyph measurement, shared by every interface
        # language.  Button glass/positions never move; only the label font
        # adapts, with a lossless two-line fallback for exceptional wording.
        try:
            fit_color_key_labels(self,("red","green","yellow","blue"),max_size=22,min_size=9,padding=14)
        except Exception as exc:
            optional_failure("ui.portal_color_key_fit",exc)

    def _start_portal_progress_poll(self):
        if getattr(self,"_screen_closed",False):return
        if getattr(self,"_portal_progress_active",False):return
        try:
            self._portal_progress_timer.start(300,False)
            self._portal_progress_active=True
        except Exception as exc:optional_failure("ui.portal_progress_start",exc)

    def _pause_portal_progress_poll(self):
        if not getattr(self,"_portal_progress_active",False):return
        try:self._portal_progress_timer.stop()
        except Exception as exc:optional_failure("ui.portal_progress_pause",exc)
        self._portal_progress_active=False

    def _stop_portal_progress(self):
        self._portal_progress_active=False
        try:self._portal_progress_timer.stop()
        except Exception as exc:optional_failure("ui.portal_progress_stop",exc)
        try:
            if self._portal_progress_conn is not None:self._portal_progress_conn.disconnect()
        except Exception as exc:optional_failure("ui.portal_progress_stop",exc)
        try:
            if self._drain_portal_progress in self._portal_progress_timer.callback:self._portal_progress_timer.callback.remove(self._drain_portal_progress)
        except Exception as exc:optional_failure("ui.portal_progress_callback",exc)

    def _stop_portal_ui_hooks(self):
        try:
            while True:self._portal_progress_jobs.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:
            optional_failure("ui.portal_progress_queue_drain",exc)
        for hook_name, callback in (
            ("onLayoutFinish", self._layout_ready),
            ("onShown", self._portal_list_shown),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.portal_hook_cleanup",exc)
        try:
            callbacks=self["list"].onSelectionChanged
            if self._selection_changed in callbacks:callbacks.remove(self._selection_changed)
        except Exception as exc:
            optional_failure("ui.portal_selection_cleanup",exc)

    def _drain_portal_progress(self):
        if getattr(self, "_screen_closed", False):
            return
        latest = None
        while True:
            try:latest = self._portal_progress_jobs.get_nowait()
            except queue.Empty:break
        if latest:
            done,total,name,success = latest
            mark = _("OK") if success else _("FAILED")
            self._set_check_progress(_("Checking %d/%d  •  %s  •  %s") % (done,total,str(name)[:24],mark))
        if not getattr(self,"_busy",False) and self._portal_progress_jobs.empty():
            self._pause_portal_progress_poll()

    def _portal_cancel(self):
        if getattr(self, "_check_notice_visible", False):
            self._hide_check_notice()
            return
        if self._portal_select_mode:
            self._portal_select_mode = False
            self._portal_selected.clear()
            self._render_portal_rows(self._index() or 0)
            self._selection_changed()
            return
        if self._bulk_move_mode:
            self._cancel_bulk_move()
            return
        if self._reorder_mode:
            self._set_reorder_mode(False)
            return
        self.close()

    def _portal_ok(self):
        if getattr(self, "_check_notice_visible", False):
            self._hide_check_notice()
            return
        if self._bulk_move_mode:
            self["status"].setText(_("Move selected portals • GREEN Place Here • BACK Cancel"))
            return
        if self._portal_select_mode:
            self._toggle_portal_selection_and_advance()
            return
        if self._reorder_mode:
            self._set_reorder_mode(False)
            return
        self.open_portal()

    def _portal_green(self):
        if getattr(self, "_check_notice_visible", False):
            return
        if self._bulk_move_mode:
            self._place_bulk_move()
            return
        self._toggle_portal_select_mode()

    def _portal_blue(self):
        if getattr(self, "_check_notice_visible", False):
            return
        if self._bulk_move_mode:
            self["status"].setText(_("Move selected portals • GREEN Place Here • BACK Cancel"))
            return
        self._request_disable_portals()

    def _portal_red(self):
        if getattr(self, "_check_notice_visible", False):
            return
        if self._bulk_move_mode:
            self["status"].setText(_("Move selected portals • GREEN Place Here • BACK Cancel"))
            return
        if self._portal_select_mode:
            self._request_delete_selected_portals()
            return
        self.delete_portal()

    def _portal_yellow(self):
        if getattr(self, "_check_notice_visible", False):
            return
        if self._bulk_move_mode:
            self["status"].setText(_("Move selected portals • GREEN Place Here • BACK Cancel"))
            return
        self.check_all_portals()

    def _portal_menu(self):
        if getattr(self, "_check_notice_visible", False):
            return
        if self._bulk_move_mode:
            self["status"].setText(_("Move selected portals • GREEN Place Here • BACK Cancel"))
            return
        self.open_portal_tools()

    def _portal_info(self):
        if getattr(self, "_check_notice_visible", False):
            return
        if self._bulk_move_mode:
            self["status"].setText(_("Move selected portals • GREEN Place Here • BACK Cancel"))
            return
        self.open_diagnostics()

    def _toggle_portal_select_mode(self):
        if self._busy or self._bulk_move_mode or not self.profiles:
            return
        self._portal_select_mode = not bool(self._portal_select_mode)
        if not self._portal_select_mode:
            self._portal_selected.clear()
        self._render_portal_rows(self._index() or 0)
        self._selection_changed()

    def _toggle_portal_selection_and_advance(self):
        idx = self._index()
        if idx is None or not self.profiles:
            return
        if idx in self._portal_selected:
            self._portal_selected.remove(idx)
        else:
            self._portal_selected.add(idx)
        # Update only the current row first, then advance automatically exactly
        # like Category selection.  The list itself owns page transitions.
        try:
            self["list"].update_icon_row(idx, self._portal_row_tuple(idx, idx))
        except Exception:
            self._render_portal_rows(idx)
        if idx + 1 < len(self.profiles):
            try:self["list"].moveToIndex(idx + 1)
            except Exception as exc:optional_failure("ui.portal_select_advance", exc)
        self._selection_changed()

    def _selected_portal_targets(self):
        if self._portal_select_mode:
            return [self.profiles[i] for i in sorted(self._portal_selected) if 0 <= i < len(self.profiles)]
        idx = self._index()
        return [self.profiles[idx]] if idx is not None and 0 <= idx < len(self.profiles) else []

    def _request_delete_selected_portals(self):
        targets = self._selected_portal_targets()
        if not targets:
            self["status"].setText(_("No portal selected"))
            return
        self._direct_delete_targets = list(targets)
        prompt = (_("Delete %d selected portals") % len(targets)) + "\n\n" + _("Permanently delete selected portal from saved profiles, imports, cached sessions and plugin-owned data?")
        self.session.openWithCallback(self._direct_delete_confirmed, MessageBox, prompt, MessageBox.TYPE_YESNO)

    def _direct_delete_confirmed(self, answer):
        if not answer:
            return
        targets = list(getattr(self, "_direct_delete_targets", []) or [])
        deleted = 0
        failed = 0
        for profile in targets:
            try:
                result = permanently_delete_profile(profile, session=self.session, purge_related=True) or {}
                if result.get("deleted", True): deleted += 1
                else: failed += 1
            except Exception as exc:
                failed += 1
                optional_failure("ui.portal_direct_bulk_delete", exc)
        self._portal_select_mode = False
        self._portal_selected.clear()
        self.profiles = load_profiles()
        self.refresh()
        self["status"].setText(_("Deleted %d portals") % deleted if not failed else _("Deleted %d portals • %d failed") % (deleted, failed))

    def _request_disable_portals(self):
        targets = self._selected_portal_targets()
        if not targets:
            self["status"].setText(_("No portal selected"))
            return
        self._direct_disable_targets = list(targets)
        prompt = _("Disable %d selected portals?") % len(targets)
        self.session.openWithCallback(self._direct_disable_confirmed, MessageBox, prompt, MessageBox.TYPE_YESNO)

    def _direct_disable_confirmed(self, answer):
        if not answer:
            return
        targets = list(getattr(self, "_direct_disable_targets", []) or [])
        try:
            disable_profiles(targets)
            count = len(targets)
            self._portal_select_mode = False
            self._portal_selected.clear()
            self.profiles = load_profiles()
            self.refresh()
            self["status"].setText(_("%d disabled") % count)
        except Exception as exc:
            self["status"].setText(_("Disabling failed portals failed: %s") % exc)

    @staticmethod
    def _bulk_profile_key(profile):
        p = profile if isinstance(profile, dict) else {}
        return (
            str(p.get("portal") or "").rstrip("/").lower(),
            str(p.get("mac") or "").strip().upper(),
            str(p.get("source_type") or "stalker").strip().lower(),
        )

    def _begin_bulk_move(self, targets):
        if self._busy or not self.profiles:
            return
        wanted = {self._bulk_profile_key(p) for p in (targets or []) if isinstance(p, dict)}
        # Resolve against the parent's CURRENT order. This prevents stale manager
        # snapshots from moving the wrong row after an external profile edit.
        block = [p for p in self.profiles if self._bulk_profile_key(p) in wanted]
        if not block:
            self["status"].setText(_("No selected portals are available to move"))
            return
        self._reorder_mode = False
        self._bulk_move_profiles = list(block)
        self._bulk_move_mode = True
        self["green"].setText(_("Place Here"))
        self["blue"].setText(_("Disable"))
        self._fit_portal_color_keys()
        # Start on the first selected source, exactly where the user left off,
        # then let normal UP/DOWN/LEFT/RIGHT page navigation choose the slot.
        first_key = self._bulk_profile_key(block[0])
        first_idx = 0
        for pos, profile in enumerate(self.profiles):
            if self._bulk_profile_key(profile) == first_key:
                first_idx = pos
                break
        try:
            self["list"].moveToIndex(first_idx)
            self._selection_changed()
        except Exception as exc:
            optional_failure("ui.portal_bulk_move_anchor", exc)
        self["status"].setText(_("Move %d selected • ARROWS choose destination • GREEN Place Here • BACK Cancel") % len(block))

    def _cancel_bulk_move(self):
        count = len(self._bulk_move_profiles)
        self._bulk_move_mode = False
        self._bulk_move_profiles = []
        self["green"].setText(_("Select"))
        self["blue"].setText(_("Disable"))
        self._fit_portal_color_keys()
        self["status"].setText(_("Move cancelled") if count else _("Portal order unchanged"))

    def _place_bulk_move(self):
        if not self._bulk_move_mode or not self._bulk_move_profiles or not self.profiles:
            return
        destination = self._index()
        if destination is None:
            destination = 0
        destination = max(0, min(int(destination), len(self.profiles) - 1))
        selected_keys = {self._bulk_profile_key(p) for p in self._bulk_move_profiles}
        block = [p for p in self.profiles if self._bulk_profile_key(p) in selected_keys]
        remaining = [p for p in self.profiles if self._bulk_profile_key(p) not in selected_keys]
        if not block:
            self._cancel_bulk_move()
            self["status"].setText(_("Selected portals are no longer available"))
            return
        # Destination is a POSITION, not an anchor row. Thus stopping on visible
        # row 1 means the moved block starts at #1, row 10 means it starts at #10.
        insert_at = max(0, min(destination, len(remaining)))
        reordered = remaining[:insert_at] + block + remaining[insert_at:]
        previous = list(self.profiles)
        try:
            save_profiles(reordered)
            self.profiles = load_profiles()
        except Exception as exc:
            self.profiles = previous
            self.refresh()
            self["status"].setText(_("Move save failed: %s") % exc)
            return
        moved = len(block)
        self._bulk_move_mode = False
        self._bulk_move_profiles = []
        self["green"].setText(_("Select"))
        self["blue"].setText(_("Disable"))
        self._fit_portal_color_keys()
        target = max(0, min(insert_at, max(0, len(self.profiles) - 1)))
        self._render_portal_rows(target)
        try:self["list"].moveToIndex(target)
        except Exception as exc:optional_failure("ui.portal_bulk_move_focus", exc)
        self._selection_changed()
        self["status"].setText(_("Moved %d portals • block starts at position %d") % (moved, target + 1))

    def rename_selected_portal(self):
        idx = self._index()
        if idx is None or self._busy:
            return
        self._editing_profile = self.profiles[idx]
        current_name = str(self._editing_profile.get("name") or (_("Portal Server %d") % (idx + 1)))
        if VirtualKeyBoard is not None:
            try:
                self.session.openWithCallback(self._portal_renamed, VirtualKeyBoard, title=_("Portal name"), text=current_name)
                return
            except Exception as exc:
                optional_failure("ui.portal_rename_keyboard", exc)
        self.session.openWithCallback(
            self._portal_renamed, InputBox, title=_("Portal name"), text=current_name, maxSize=80
        )

    def _set_reorder_mode(self, enabled):
        self._reorder_mode = bool(enabled and self.profiles and not self._busy)
        self["blue"].setText(_("Done") if self._reorder_mode else _("Edit Order"))
        self._fit_portal_color_keys()
        if self._reorder_mode:
            self["status"].setText(_("Edit order  •  UP/DOWN moves the selected portal  •  BLUE/OK saves"))
        else:
            self["status"].setText(_("Portal order saved"))

    def toggle_reorder_mode(self):
        if self._busy or not self.profiles:
            return
        self._set_reorder_mode(not self._reorder_mode)

    def _portal_up(self):
        if self._reorder_mode:
            self._move_selected_portal(-1)
            return
        try:self["list"].wrap_up()
        except Exception as exc:optional_failure("ui.portal_nav",exc)

    def _portal_down(self):
        if self._reorder_mode:
            self._move_selected_portal(1)
            return
        try:self["list"].wrap_down()
        except Exception as exc:optional_failure("ui.portal_nav",exc)

    def _portal_change_page(self, delta):
        if self._reorder_mode or not self.profiles:
            return
        idx = self._index()
        if idx is None:
            idx = 0
        row = idx % self._portal_page_size
        pages = max(1, (len(self.profiles) + self._portal_page_size - 1) // self._portal_page_size)
        current = idx // self._portal_page_size
        target_page = max(0, min(pages - 1, current + int(delta)))
        if target_page == current:
            return
        target = min(len(self.profiles) - 1, target_page * self._portal_page_size + row)
        self["list"].moveToIndex(target)
        self._selection_changed()

    def _portal_prev_page(self):
        if self._reorder_mode or not self.profiles:
            return
        # Use the exact same list navigation primitive as every other scrolling
        # Ultra Stalker list: preserve row between pages; first-page LEFT -> #1.
        try:self["list"].page_left()
        except Exception as exc:optional_failure("ui.portal_page_left",exc)
        self._selection_changed()

    def _portal_next_page(self):
        if self._reorder_mode or not self.profiles:
            return
        # Same-row page jump; final-page RIGHT -> actual final item.
        try:self["list"].page_right()
        except Exception as exc:optional_failure("ui.portal_page_right",exc)
        self._selection_changed()

    def _move_selected_portal(self, delta):
        idx = self._index()
        if idx is None or not self.profiles:
            return
        target = max(0, min(len(self.profiles)-1, idx + int(delta)))
        if target == idx:
            self["status"].setText(_("Already at the top") if delta < 0 else _("Already at the bottom"))
            return
        previous = list(self.profiles)
        self.profiles[idx], self.profiles[target] = self.profiles[target], self.profiles[idx]
        try:
            save_profiles(self.profiles)
        except Exception as exc:
            self.profiles = previous
            self.refresh()
            self["status"].setText(_("Order save failed: %s") % exc)
            return
        self._render_portal_rows(target)
        self._selection_changed()
        self["status"].setText(_("Moved to position %d of %d  •  BLUE/OK when done") % (target+1, len(self.profiles)))

    def _portal_list_shown(self):
        _perf_t0 = time.monotonic()
        LOG.info("PERF25 portals shown_enter mono_ms=%d", int(_perf_t0 * 1000))
        # External edits to portals.txt must become visible the first time the
        # user returns to this screen. Reloading this tiny local config is cheap
        # and deliberately does not touch channel/VOD/artwork caches.
        try:
            current_key=None
            idx=self._index()
            if idx is not None and self.profiles:
                p=self.profiles[idx];current_key=(str(p.get("portal") or "").rstrip("/").lower(),str(p.get("mac") or "").upper())
            fresh=load_profiles()
            if fresh != self.profiles:
                self.profiles=fresh
                target=0
                if current_key:
                    for pos,p in enumerate(self.profiles):
                        if (str(p.get("portal") or "").rstrip("/").lower(),str(p.get("mac") or "").upper())==current_key:
                            target=pos;break
                self._render_portal_rows(target)
                if self.profiles:self["list"].moveToIndex(target)
            elif self.profiles:
                self["list"].moveToIndex(max(0,min(getattr(self,"_portal_visual_index",0),len(self.profiles)-1)))
        except Exception as exc:optional_failure("ui.portal_fresh_import",exc)
        try:self._selection_changed()
        except Exception as exc:optional_failure("ui",exc)
        LOG.info("PERF25 portals shown_done elapsed_ms=%d", int((time.monotonic() - _perf_t0) * 1000))
        # R181 point 3: first-run setup is now an inline Settings-row list on Home.
        # PortalList must never open the old child popup.

    def _preview_first_setup(self):
        # Obsolete R178/R181 popup retired. First-run setup lives inline on Home.
        return

    def _maybe_show_first_setup(self):
        # Home owns onboarding_v3. Keep PortalList navigation completely clean.
        return

    def _restore_background_service(self):
        restore_plugin_service(self.session)

    def open_diagnostics(self):
        idx = self._index()
        profile = self.profiles[idx] if idx is not None else None
        self.session.open(DiagnosticsScreen, profile)

    def open_portal_tools(self):
        # Premium Test8-style Portal Manager.  The screen returns the exact same
        # action tuple as the legacy ChoiceBox, so all proven portal operations
        # below remain untouched.
        self.session.openWithCallback(self._portal_tool_selected, PortalManagerPremiumScreen, self)

    def _portal_tool_selected(self,choice):
        if not choice:return
        action=choice[1];idx=self._index();profile=self.profiles[idx] if idx is not None else None
        if action=="check_all":return self.check_all_portals()
        if action=="backup_restore":return self._portal_backup_restore()
        if action=="enable_disabled":return self._choose_disabled_portal()
        if not profile:return
        if action=="rename":
            self._editing_profile=profile;self.session.openWithCallback(self._portal_renamed,InputBox,title=_("Portal name"),text=str(profile.get("name") or _("Portal Server")),maxSize=80)
        elif action=="edit":
            self._editing_profile=profile;self.session.openWithCallback(self._portal_edit_url,InputBox,title=_("Portal / M3U URL"),text=str(profile.get("portal") or "https://"),maxSize=250)
        elif action=="tls_mode":
            self["status"].setText(_("TLS verification is locked to strict mode in this hardened build"))
        elif action=="http_fallback":
            self["status"].setText(_("HTTP fallback is disabled in this hardened build"))
        elif action=="device_profile":
            self._editing_profile=profile
            self.session.openWithCallback(
                self._portal_device_selected,
                SettingsGlassChoiceScreen,
                _("MAG device profile"),
                [(_("Auto"),"auto"),("MAG250","mag250"),("MAG254","mag254"),("MAG256","mag256")],
                subtitle=_("Choose the device profile for this portal")
            )
        elif action=="export_bouquet":
            self._export_live_integration(profile)
        elif action=="unexport_bouquet":
            try:
                result=unexport_live_integration(profile,reload=True)
                self["status"].setText(_("Exported bouquet and EPG removed"))
            except Exception as exc:
                self["status"].setText(_("Remove export failed: %s")%exc)
        elif action=="duplicate":
            self._duplicating_profile=profile;self.session.openWithCallback(self._portal_duplicate_mac,InputBox,title=_("MAC for duplicate"),text="00:1A:79:",maxSize=17)
        elif action == "move_selected":
            self["status"].setText(_("Select portals first, then MENU → Move"))
        elif action=="disable":self.delete_portal()
        elif action=="purge_data":
            self._purge_profile=profile;self.session.openWithCallback(self._purge_profile_confirmed,MessageBox,_("Clear plugin-owned data for this portal?\n\nThis removes its exported bouquet/EPG, matching recording refresh jobs/timers, Favorites, History and Resume data, but keeps the portal itself."),MessageBox.TYPE_YESNO)
        elif action=="delete_permanent":
            self._permanent_profile=profile;self.session.openWithCallback(self._permanent_delete_confirmed,MessageBox,_("Permanently delete this portal and its plugin-owned data?\n\nThis removes its exported bouquet/EPG, recording refresh jobs, Favorites, History and Resume data. Any matching Stalker recording timers will also be removed."),MessageBox.TYPE_YESNO)
        elif action=="diagnostics":self.session.open(DiagnosticsScreen,profile)

    def _portal_backup_restore(self):
        """Portal-level entry point to the exact same complete backup engine.

        This is intentionally available even when no portal exists yet, so a
        fresh installation can restore its saved portals before creating a
        temporary/throwaway profile.
        """
        rows=[(_("Backup all settings & portals"), "backup_all"), (_("Restore backup"), "restore")]
        self.session.openWithCallback(
            self._portal_backup_action,
            SettingsGlassChoiceScreen,
            _("Backup & Restore"),
            rows,
            subtitle=_("Choose a backup or restore action")
        )

    def _portal_backup_action(self, choice):
        if not choice:return
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if action=="backup_all":
            self["status"].setText(_("Creating complete HDD backup…"))
            def work():return create_backup()
            def done(result):
                path=str(result.get("path") or "") if isinstance(result,dict) else ""
                self["status"].setText(_("Backup: %s")%path)
                self.session.open(SettingsGlassNoticeScreen,_('Backup Created'),_("Complete backup created on HDD:\n%s\n\nPortals, settings and user API credentials are included. Artwork cache is not copied.")%path,False)
            def failed(exc):
                self["status"].setText(_("Backup failed: %s")%exc)
                self.session.open(SettingsGlassNoticeScreen,_('Backup Failed'),_("Backup failed: %s")%exc,False)
            self._run_async(work,done,failed)
            return
        if action=="restore":
            backups=list_backups()
            if not backups:
                self.session.open(MessageBox,_("No backups found on HDD.\n\nExpected folder: /media/hdd/UltraStalker/Backup/"),MessageBox.TYPE_INFO,timeout=8)
                return
            rows=[]
            for path in backups:
                try:
                    manifest=inspect_backup(path)
                    kind="FULL" if manifest.get("contains_secrets") else "LEGACY"
                    label=os.path.basename(path)+"  •  "+str(manifest.get("plugin_version") or "unknown")+"  •  "+kind
                except Exception:
                    label=os.path.basename(path)+"  •  INVALID"
                rows.append((label,path))
            self.session.openWithCallback(
                self._portal_restore_selected,
                SettingsGlassChoiceScreen,
                _("Choose backup to restore"),
                rows,
                subtitle=_("Select a saved Ultra Stalker backup")
            )

    def _portal_restore_selected(self, choice):
        if not choice:return
        path=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else ""
        try:inspect_backup(path)
        except Exception as exc:
            self.session.open(MessageBox,_("Invalid backup: %s")%exc,MessageBox.TYPE_ERROR,timeout=8)
            return
        self._portal_restore_path=path
        text=_("Restore this backup completely?\n\nThe selected backup will be restored directly. No additional backup copy will be created.")
        self.session.openWithCallback(self._portal_restore_confirmed,MessageBox,text,MessageBox.TYPE_YESNO)

    def _portal_restore_confirmed(self, answer):
        if not answer:return
        path=str(getattr(self,"_portal_restore_path","") or "")
        if not path:return
        self["status"].setText(_("Validating and restoring backup…"))
        def work(handle):
            return restore_backup(path,restore_secrets=True,cancel_event=handle.cancel_event)
        def done(result):
            try:self.profiles=load_profiles();self.refresh()
            except Exception as exc:optional_failure("ui.portal_restore_refresh",exc)
            restored=", ".join(result.get("restored",[])) if isinstance(result,dict) else ""
            self["status"].setText(_("Restore complete • restart Enigma2/plugin"))
            self.session.open(SettingsGlassNoticeScreen,_('Restore Complete'),_("Restore complete. Restart Enigma2/plugin before continuing.\n\nRestored: %s")%restored,False)
        def failed(exc):
            self["status"].setText(_("Restore failed: %s")%exc)
            self.session.open(SettingsGlassNoticeScreen,_('Restore Failed'),_("Restore failed: %s")%exc,False)
        self._run_async(work,done,failed)

    def _export_live_integration(self, profile):
        if self._busy:
            return
        self["status"].setText(_("Exporting full Live bouquet..."))
        cfg=load_settings()
        def work(handle):
            client=_client_from_profile(profile,cfg.get("timeout",10))
            try:
                channels=client.ordered_all("itv","*",1,max_pages=cfg.get("search_max_pages",250),max_items=25000,cancel_event=handle.cancel_event)
                if handle.cancelled(): return {}
                return export_live_integration(profile,channels,cfg.get("service_type",4097))
            finally:
                client.close()
        def ok(result):
            server=start_proxy_server();reload_bouquets()
            if server is None:
                self["status"].setText(_("Bouquet export failed: dynamic proxy is not running"))
                self.session.open(SettingsGlassNoticeScreen,_("Live Export"),_("The files were written but the dynamic bouquet proxy could not start. Remove/re-export the bouquet after resolving the port conflict."),False)
                return
            count=int((result or {}).get("channels") or 0)
            self["status"].setText(_("Bouquet exported • %d channels • proxy %s")%(count,(result or {}).get("proxy_port") or ""))
            self.session.open(
                SettingsGlassNoticeScreen,
                _("Live Export Complete"),
                _("Live bouquet exported with dynamic Stalker links.\n\n%d channels\n\nAn EPGImport source was also created. Open EPG-Importer, enable the Ultra Stalker source, then run an import.")%count,
                False
            )
        self._run_async(work,ok,lambda e:self["status"].setText(_("Bouquet export failed: %s")%e))

    def _portal_tls_selected(self,choice):
        if not choice:return
        old=getattr(self,"_editing_profile",None) or {};new=dict(old)
        new["tls_mode"]="strict";new["tls_fallback_accepted"]=False
        try:
            changed = (str(old.get("tls_mode") or "auto") != str(new.get("tls_mode") or "auto") or bool(old.get("tls_fallback_accepted",False)) != bool(new.get("tls_fallback_accepted",False)))
            replace_profile(old,new,session=self.session)
            if changed:
                StalkerClient.clear_persisted_tls_pins(new.get("portal"), new.get("mac"))
            self.profiles=load_profiles();self.refresh();self["status"].setText(_("TLS security preference saved"))
        except Exception as exc:self["status"].setText(_("TLS mode save failed: %s")%exc)

    def _portal_http_fallback_selected(self,choice):
        if not choice:return
        old=getattr(self,"_editing_profile",None) or {};new=dict(old)
        new["allow_http_fallback"]=False
        new["http_fallback_accepted"]=False
        try:replace_profile(old,new,session=self.session);self.profiles=load_profiles();self.refresh();self["status"].setText(_("HTTP fallback disabled by hardened build"))
        except Exception as exc:self["status"].setText(_("HTTP fallback save failed: %s")%exc)

    def _portal_device_selected(self,choice):
        if not choice:return
        old=getattr(self,"_editing_profile",None) or {};new=dict(old);new["device_profile"]=choice[1]
        try:replace_profile(old,new,session=self.session);self.profiles=load_profiles();self.refresh();self["status"].setText(_("MAG profile saved: %s")%choice[1])
        except Exception as exc:self["status"].setText(_("MAG profile save failed: %s")%exc)

    def _portal_renamed(self,value):
        if value is None:return
        old=getattr(self,"_editing_profile",None) or {};new=dict(old);new["name"]=str(value or "").strip()[:80]
        try:replace_profile(old,new,session=self.session);self.profiles=load_profiles();self.refresh();self["status"].setText(_("Portal renamed"))
        except Exception as exc:self["status"].setText(_("Rename failed: %s")%exc)

    def _portal_edit_url(self,value):
        if not value:return
        self._edited_url=str(value).strip();old=getattr(self,"_editing_profile",{})
        self._edited_source_type="m3u" if _looks_like_m3u_url(self._edited_url) else "stalker"
        if requires_http_consent(self._edited_url):
            self.session.openWithCallback(self._portal_edit_http_confirmed,SettingsGlassNoticeScreen,_('HTTP security warning'),http_warning_for(self._edited_url),True)
            return
        if self._edited_source_type=="m3u":self._portal_edit_m3u_save()
        else:self.session.openWithCallback(self._portal_edit_mac,SettingsGlassInputScreen,title=_("MAC address"),text=str(old.get("mac") or "00:1A:79:"),maxSize=17)

    def _portal_edit_http_confirmed(self,answer):
        if not answer:
            self["status"].setText(_("HTTP portal update cancelled"))
            return
        old=getattr(self,"_editing_profile",{})
        if getattr(self,"_edited_source_type","stalker")=="m3u":self._portal_edit_m3u_save()
        else:self.session.openWithCallback(self._portal_edit_mac,SettingsGlassInputScreen,title=_("MAC address"),text=str(old.get("mac") or "00:1A:79:"),maxSize=17)

    def _portal_edit_m3u_save(self):
        old=getattr(self,"_editing_profile",None) or {};new=dict(old)
        new["portal"]=getattr(self,"_edited_url",old.get("portal"));new["source_type"]="m3u";new["mac"]="";new["account_state"]="M3U PLAYLIST";new["state"]="M3U • Not checked"
        _clear_profile_connections(new)
        new=mark_http_consent(new,True)
        try:
            _validate_profile_client(new);replace_profile(old,new,session=self.session)
            self.profiles=load_profiles();self.refresh();self["status"].setText(_("M3U source updated"))
        except Exception as exc:self.session.open(SettingsGlassNoticeScreen,_("Update failed"),_("Update failed: %s")%exc,False)

    def _portal_edit_mac(self,value):
        if not value:return
        old=getattr(self,"_editing_profile",None) or {};new=dict(old);new["portal"]=getattr(self,"_edited_url",old.get("portal"));new["mac"]=str(value).strip().upper();new["source_type"]="stalker"
        if (str(new.get("portal") or "").strip() != str(old.get("portal") or "").strip() or
                str(new.get("mac") or "").upper() != str(old.get("mac") or "").upper()):
            _clear_profile_connections(new)
        new=mark_http_consent(new,True)
        if str(new.get("portal") or "").strip() != str(old.get("portal") or "").strip():
            new["allow_http_fallback"]=False;new["http_fallback_accepted"]=False;new["tls_fallback_accepted"]=False
        try:
            _validate_profile_client(new);replace_profile(old,new,session=self.session);self.profiles=load_profiles();self.refresh();self["status"].setText(_("Portal updated"))
        except Exception as exc:self.session.open(SettingsGlassNoticeScreen,_("Update failed"),_("Update failed: %s")%exc,False)

    def _portal_duplicate_mac(self,value):
        if not value:return
        base=getattr(self,"_duplicating_profile",None) or {};clone=duplicate_profile(base);clone["mac"]=str(value).strip().upper();clone["state"]="Not checked";clone["health"]="unknown";_clear_profile_connections(clone)
        try:
            _validate_profile_client(clone);rows=load_profiles();rows.append(clone);save_profiles(rows);enable_profile(clone);self.profiles=load_profiles();self.refresh();self["status"].setText(_("Portal duplicated"))
        except Exception as exc:self.session.open(SettingsGlassNoticeScreen,_("Duplicate failed"),_("Duplicate failed: %s")%exc,False)

    def _choose_disabled_portal(self):
        rows=load_disabled_profiles()
        if not rows:self.session.open(MessageBox,_("No disabled portals."),MessageBox.TYPE_INFO,timeout=5);return
        choices=[]
        for row in rows:
            label=(row.get("name") or row.get("portal") or "Portal")+"  •  "+str(row.get("mac") or "")
            choices.append((label,row))
        self.session.openWithCallback(
            self._disabled_portal_selected,
            SettingsGlassChoiceScreen,
            _("Re-enable portal"),
            choices,
            subtitle=_("Choose a disabled portal to enable")
        )

    def _disabled_portal_selected(self,choice):
        if not choice:return
        try:enable_profile(choice[1]);self.profiles=load_profiles();self.refresh();self["status"].setText(_("Portal enabled"))
        except Exception as exc:self["status"].setText(_("Enable failed: %s")%exc)

    def _purge_profile_confirmed(self,answer):
        if not answer:return
        try:
            cleanup=purge_profile_data(getattr(self,"_purge_profile",{}),session=self.session,remove_timers=True)
            errors=[str(v) for k,v in cleanup.items() if str(k).endswith("_error") and v]
            if errors:
                self["status"].setText(_("Portal data cleared with warnings: %s")%errors[0])
            else:
                self["status"].setText(_("Portal data cleared; portal profile kept"))
        except Exception as exc:self["status"].setText(_("Clear portal data failed: %s")%exc)

    def _permanent_delete_confirmed(self,answer):
        if not answer:return
        try:
            result=permanently_delete_profile(getattr(self,"_permanent_profile",{}),session=self.session,purge_related=True)
            self.profiles=load_profiles();self.refresh()
            cleanup=(result or {}).get("cleanup") or {};errors=[str(v) for k,v in cleanup.items() if str(k).endswith("_error") and v]
            self["status"].setText(_("Portal deleted with cleanup warning: %s")%errors[0] if errors else _("Portal and related plugin data permanently deleted"))
        except Exception as exc:self["status"].setText(_("Delete failed: %s")%exc)

    def choose_theme(self):
        choices = [(name.replace("_", " ").title(), name) for name in THEMES]
        self.session.openWithCallback(
            self._theme_selected,
            SettingsGlassChoiceScreen,
            _("Choose visual theme"),
            choices,
            selection=THEMES.index(_active_theme()) if _active_theme() in THEMES else 0,
            subtitle=_("Choose the visual theme")
        )

    def _theme_selected(self, choice):
        if not choice:
            return
        try:
            save_theme(choice[1])
            global _ACTIVE_THEME_CACHE
            _ACTIVE_THEME_CACHE = choice[1]
            self.session.open(MessageBox, _("Theme saved. Restart Enigma2 to apply: %s") % choice[0], MessageBox.TYPE_INFO, timeout=8)
        except Exception as exc:
            self.session.open(MessageBox, _("Theme save failed: %s") % exc, MessageBox.TYPE_ERROR, timeout=8)


    def _portal_hero_visuals(self):
        prepared = _portal_category_backdrop()
        ambient = ""
        return prepared, ambient

    @staticmethod
    def _portal_settings_icon(name):
        try:
            compact = asset("settings_icons_40/" + str(name or ""))
            if compact and os.path.isfile(compact):
                return compact
        except Exception as exc:
            optional_failure("ui.portal_settings_icon",exc)
        return asset("home_settings.png")

    def _apply_portal_settings_materials(self):
        _perf_all = time.monotonic()
        try:cleanup_legacy_application_outputs()
        except Exception as exc:optional_failure("ui.fixed_adaptive_cleanup_portals",exc)
        prepared, ambient = self._portal_hero_visuals()
        try:
            if ambient:
                self["ambient_bg"].instance.setPixmapFromFile(ambient); self["ambient_bg"].show()
            else:
                self["ambient_bg"].hide()
        except Exception as exc:
            optional_failure("ui.portal_settings_ambient", exc)

        # PERF28: On the target OpenBH renderer the skin-level page_bg is not
        # the visible Portal List backdrop.  hero_backdrop is the actual painted
        # layer.  PERF27 hid it and therefore removed the approved Palestine
        # background.  Restore the proven R26 visual path exactly; keep the
        # surrounding cache-first row work and timing instrumentation intact.
        _phase = time.monotonic()
        try:
            if prepared and os.path.isfile(prepared):
                self["hero_backdrop"].instance.setPixmapFromFile(prepared)
                self["hero_backdrop"].show()
            else:
                self["hero_backdrop"].hide()
        except Exception as exc:
            optional_failure("ui.portal_settings_backdrop", exc)
        LOG.info("PERF28 portals backdrop_restore elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))

        _phase = time.monotonic()
        generated = False
        try:
            chrome = fixed_settings_rows() or {}
            normal = str(chrome.get("normal") or "")
            selected = str(chrome.get("selected") or "")
            if normal and os.path.isfile(normal):
                self._portal_row_asset = normal
            if selected and os.path.isfile(selected):
                self._portal_row_selected_asset = selected
            self._portal_value_color = int(chrome.get("value_color") or fixed_value_color(self._portal_value_color))
            _accent=parseColor("#%06x" % (int(self._portal_value_color)&0xFFFFFF))
            for _name,_size in (("check_progress",18),("portal_page",18)):
                try:
                    _inst=self[_name].instance
                    if _inst is not None:
                        _inst.setForegroundColor(_accent);_inst.setFont(gFont("Regular",_size))
                except Exception:pass
            # The list was first painted during __init__; force a physical row
            # rebuild so no legacy blue fallback pixmap can survive that first paint.
            for _name in ("list","source_badges"):
                try:self[_name]._render_row_signatures=None
                except Exception:pass
        except Exception as exc:
            optional_failure("ui.portal_settings_material", exc)
        LOG.info("PERF27 portals row_chrome elapsed_ms=%d generated=%s", int((time.monotonic() - _phase) * 1000), "yes" if generated else "no")

        _phase = time.monotonic()
        for name in ("list", "source_badges"):
            try:
                if self[name].instance is not None:
                    self[name].instance.setSelectionEnable(0)
                    self[name].instance.setTransparent(1)
                    self[name].instance.setScrollbarMode(2)
            except Exception as exc:
                optional_failure("ui.portal_settings_list_native", exc)
        LOG.info("PERF27 portals native_list elapsed_ms=%d total_ms=%d", int((time.monotonic() - _phase) * 1000), int((time.monotonic() - _perf_all) * 1000))

    def _layout_ready(self):
        _perf_t0 = time.monotonic()
        self._image_layout_ready()
        _phase = time.monotonic()
        self._apply_portal_settings_materials()
        LOG.info("PERF25 portals layout_materials elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
        self._hide_check_notice()
        # Test69 Lean: profiles were already loaded in __init__. Repainting is
        # enough; avoid a second synchronous disk parse during first layout.
        _phase = time.monotonic()
        self.refresh()
        self._fit_portal_color_keys()
        LOG.info("PERF25 portals layout_refresh elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
        notices = consume_recovery_notices()
        if notices:
            self["status"].setText(_("Recovered malformed data safely • %s") % notices[-1])
        LOG.info("PERF25 portals layout_done elapsed_ms=%d mono_ms=%d", int((time.monotonic() - _perf_t0) * 1000), int(time.monotonic() * 1000))

    @staticmethod
    def _portal_display_parts(profile):
        p = profile if isinstance(profile, dict) else {}
        health = str(p.get("health") or "unknown").lower()
        raw_state = str(p.get("state") or "Not checked").strip()
        explicit = str(p.get("account_state") or "").strip()
        if str(p.get("source_type") or "").lower()=="m3u":
            if health in ("online","excellent"):status="M3U • ONLINE"
            elif health=="slow":status="M3U • SLOW"
            elif health=="offline":status="M3U • OFFLINE"
            else:status="M3U • READY"
        elif explicit:
            status = explicit
        elif health in ("online", "excellent"):
            status = "ONLINE"
        elif health == "slow":
            status = "SLOW"
        elif health == "offline":
            status = "OFFLINE"
        else:
            status = "NOT CHECKED"
        expiry = str(p.get("expiry") or "").strip()
        if not expiry and "/" in raw_state:
            parts = [x.strip() for x in raw_state.split("/") if x.strip()]
            for part in parts[1:]:
                if "INSECURE HTTP" not in part.upper():
                    expiry = part
                    break
        if not expiry:
            expiry = _("No expiry date")
        return _(status)[:18] if status in ("M3U • ONLINE","M3U • SLOW","M3U • OFFLINE","M3U • READY","ONLINE","SLOW","OFFLINE","NOT CHECKED") else status[:18], expiry[:38]

    @staticmethod
    def _portal_card_expiry(expiry):
        text = " ".join(str(expiry or "").split())
        if not text:
            return _("No expiry")
        text = re.sub(r",?\s*\d{1,2}:\d{2}\s*(?:am|pm)$", "", text, flags=re.I).strip(" ,")
        return text[:24]

    def _portal_row_value(self, profile):
        p = profile if isinstance(profile, dict) else {}
        status, _expiry = self._portal_display_parts(p)
        is_m3u = str(p.get("source_type") or "").lower() == "m3u"
        if is_m3u:
            return str(status or "M3U • READY")[:38]
        mac = str(p.get("mac") or "").strip()
        if mac:
            return (str(status or "PORTAL") + " • " + mac)[:38]
        return str(status or "PORTAL")[:38]

    @staticmethod
    def _portal_status_text(profile):
        p = profile if isinstance(profile, dict) else {}
        health = str(p.get("health") or "unknown").strip().lower()
        explicit = str(p.get("account_state") or "").strip().upper()
        is_m3u = str(p.get("source_type") or "").lower() == "m3u"
        if health in ("online", "excellent"):
            return "ONLINE"
        if health == "slow":
            return "SLOW"
        if health == "offline":
            return "OFFLINE"
        if explicit in ("ONLINE", "CONNECTED", "ACTIVE", "ENABLED"):
            return "ONLINE"
        if explicit in ("OFFLINE", "DISABLED", "EXPIRED", "BLOCKED"):
            return explicit[:12]
        if is_m3u:
            return "READY"
        return (explicit or "NOT CHECKED")[:12]

    def _portal_status_color(self, profile):
        status = self._portal_status_text(profile)
        if status == "ONLINE":
            # Deliberately vivid neon green so a working source is obvious at a
            # glance even against blue/cyan adaptive portal chrome.
            return 0x39FF88
        if status == "SLOW":
            return 0xFFD166
        if status in ("OFFLINE", "DISABLED", "EXPIRED", "BLOCKED"):
            return 0xFF6677
        return self._portal_value_color

    def _portal_row_tuple(self, index, selected_idx):
        p = self.profiles[index]
        is_m3u = str(p.get("source_type") or "").lower() == "m3u"
        # R69: source_type=m3u stays visually M3U exactly as the Portal footer
        # reports it. Do not reinterpret credential/API URLs as XTREME here.
        name = p.get("name") or ((_("M3U Playlist %d") % (index + 1)) if is_m3u else (_("Portal Server %d") % (index + 1)))
        identity = "M3U" if is_m3u else str(p.get("mac") or "").strip()
        picked = bool(getattr(self, "_portal_select_mode", False) and index in getattr(self, "_portal_selected", set()))
        if picked:
            name = "✓  " + str(name)
            identity = ((_("SELECTED") + " • ") + identity)[:34]
        meta = {
            "selected": bool(index == selected_idx),
            "source_number": int(index) + 1,
            "status_text": _(self._portal_status_text(p)),
            "status_color": self._portal_status_color(p),
            "identity_text": identity,
            # R69: ONLINE remains green and untouched. MAC stays right-aligned
            # in the identity lane; short M3U is centered inside that same lane
            # so it does not look glued to the card edge.
            "identity_color": 0xFF3045,
            "portal_mac_compact": bool(not is_m3u),
            "portal_identity_right": True,
            "portal_identity_source": bool(is_m3u),
            "server_text": "",
            "server_color": 0xEEF6FB,
            "row_asset": self._portal_row_asset,
            "row_selected_asset": self._portal_row_selected_asset,
            "value_color": self._portal_value_color,
            "utility_accent_strong": True,
        }
        if index == selected_idx:
            connection_badge = _portal_connection_badge(p)
            if connection_badge:
                meta["connection_badge_asset"] = connection_badge[0]
                meta["connection_badge_text"] = connection_badge[1]
        icon = self._portal_settings_icon("multi_search.png" if picked else ("channel_list.png" if is_m3u else "web_access.png"))
        return (name, icon, index, meta)

    def _render_source_badges(self, selected_idx=None):
        if selected_idx is None:
            selected_idx = self._index()
        current = self.profiles[selected_idx] if selected_idx is not None and 0 <= selected_idx < len(self.profiles) else {}
        current_m3u = str(current.get("source_type") or "").lower() == "m3u"
        portal_count = sum(1 for p in self.profiles if str((p or {}).get("source_type") or "").lower() != "m3u")
        m3u_count = len(self.profiles) - portal_count
        specs = (
            (_("PORTAL"), "portal", "web_access.png", portal_count, not current_m3u),
            ("M3U", "m3u", "channel_list.png", m3u_count, current_m3u),
        )
        rows = []
        for title, row_key, icon_name, count, active in specs:
            meta = {
                "selected": bool(active and bool(self.profiles)),
                "value": _("%d sources") % int(count),
                "row_asset": self._portal_row_asset,
                "row_selected_asset": self._portal_row_selected_asset,
                "value_color": self._portal_value_color,
                "utility_accent_strong": True,
            }
            rows.append((title, self._portal_settings_icon(icon_name), row_key, meta))

        # release TEST item #5: third right-side card reuses the exact same
        # floating glass row as PORTAL/M3U. It shows the selected source URL
        # plus expiry. Xtream credentials are intentionally not painted on screen.
        source_url = str(current.get("portal") or "").strip() if current else ""
        if source_url and current_m3u:
            try:
                from urllib.parse import urlsplit
                parsed = urlsplit(source_url)
                if parsed.scheme and parsed.netloc:
                    source_url = "%s://%s" % (parsed.scheme, parsed.netloc)
            except Exception as exc:
                optional_failure("ui.portal_source_address", exc)
        _status, source_expiry = self._portal_display_parts(current) if current else ("", "")
        expiry_text = str(source_expiry or "-").strip() or "-"
        address_title = source_url or _("No source selected")
        address_meta = {
            "selected": False,
            "value": _("Expiry: %s") % expiry_text,
            "row_asset": self._portal_row_asset,
            "row_selected_asset": self._portal_row_selected_asset,
            "value_color": self._portal_value_color,
            "adaptive_title": True,
            "utility_accent_strong": True,
        }
        rows.append((address_title, self._portal_settings_icon("channel_list.png" if current_m3u else "web_access.png"), "source_info", address_meta))
        try:
            self["source_badges"].set_icon_rows(rows)
        except Exception as exc:
            optional_failure("ui.portal_source_badges", exc)

    def _render_portal_rows(self, selected_idx=None):
        if selected_idx is None:
            selected_idx = self._index()
        if selected_idx is None:
            selected_idx = 0
        if self.profiles:
            selected_idx = max(0, min(int(selected_idx), len(self.profiles) - 1))
            rows = [self._portal_row_tuple(i, selected_idx) for i in range(len(self.profiles))]
        else:
            meta = {
                "selected": True, "value": _("Add a Portal or M3U source"),
                "row_asset": self._portal_row_asset, "row_selected_asset": self._portal_row_selected_asset,
                "value_color": self._portal_value_color,
                "utility_accent_strong": True,
            }
            rows = [(_("No profiles found"), self._portal_settings_icon("web_access.png"), None, meta)]
        self._portal_rebuilding = True
        try:
            self["list"].set_icon_rows(rows)
            if self.profiles:
                self["list"].moveToIndex(selected_idx)
        finally:
            self._portal_rebuilding = False
        self._portal_visual_index = selected_idx
        self._render_source_badges(selected_idx if self.profiles else None)

    def _load_category_chrome(self):
        # R64: Portal surfaces are application UI, not content artwork.  The old
        # Hero/category adaptive writer is deliberately dead here so a later
        # navigation path can never recolour or overwrite the fixed chrome.
        self._category_chrome = {}
        return {}

    def _render_genre_rows(self):
        if self.level != "genres": return
        chrome=self._category_chrome or self._load_category_chrome()
        try: selected=self["list"].getSelectedIndex()
        except Exception: selected=0
        cfg=load_settings(); pinned=set(cfg.get("pinned_categories",{}).get(self.media_type,[])); protected=set(cfg.get("protected_categories",{}).get(self.media_type,[])); words=cfg.get("adult_keywords",[])
        rows=[]
        for i,e in enumerate(self.content_items):
            gid=str(e.get("id") or e.get("genre_id") or e.get("name") or e.get("title") or "")
            raw=e.get("title") or e.get("name") or e.get("id") or _("Categories")
            title=premium_title(raw,cfg.get("clean_titles",True))
            if gid in pinned:title="★  "+title
            sensitive=(gid in protected or any(w in str(e.get("name") or e.get("title") or "").casefold() for w in words))
            if cfg.get("parental_lock") and cfg.get("parental_mode","pin")=="pin" and sensitive:title="[PIN]  "+title
            details={"selected":i==selected,"row_asset":chrome.get("row"),"row_selected_asset":chrome.get("row_selected")}
            rows.append((title,asset("us89_folder_yellow_42.png"),e,details))
        self["list"].set_icon_rows(rows)
        try:self["list"].moveToIndex(max(0,min(selected,len(rows)-1)))
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _selection_changed(self):
        if getattr(self, "_portal_rebuilding", False):
            return
        idx = self._index()
        if idx is None:
            self["portal_page"].setText(_("No Portal / M3U sources"))
            self["status"].setText(_("MENU Portal Manager  •  Add a Portal or M3U source to begin"))
            self["footer_connection"].setText("")
            self._render_source_badges(None)
            return

        old = getattr(self, "_portal_visual_index", idx)
        if idx != old:
            self._portal_visual_index = idx
            for j in set((old, idx)):
                if 0 <= j < len(self.profiles):
                    try:
                        self["list"].update_icon_row(j, self._portal_row_tuple(j, idx))
                    except Exception as exc:
                        optional_failure("ui.portal_settings_row_update", exc)

        total = len(self.profiles)
        try:
            nav_page_size = max(1, int(self["list"]._visible_page_rows()))
        except Exception:
            nav_page_size = 10
        pages = max(1, (total + nav_page_size - 1) // nav_page_size)
        page = (idx // nav_page_size) + 1
        p = self.profiles[idx]
        is_m3u = str(p.get("source_type") or "").lower() == "m3u"
        name = p.get("name") or ((_("M3U Playlist %d") % (idx + 1)) if is_m3u else (_("Portal Server %d") % (idx + 1)))
        status, _expiry = self._portal_display_parts(p)
        page_text = _("Page %d / %d  •  Source %d / %d  •  %s") % (page, pages, idx + 1, total, "M3U" if is_m3u else _("PORTAL"))
        if getattr(self, "_portal_select_mode", False):
            page_text += "  •  " + (_("%d portals • Multi-select") % len(getattr(self, "_portal_selected", set())))
        self["portal_page"].setText(page_text)
        self._portal_page_text = page_text
        if getattr(self, "_bulk_move_mode", False):
            footer_name = _("Move %d selected • ARROWS choose destination • GREEN Place Here • BACK Cancel") % len(self._bulk_move_profiles)
            footer_text = ""
            self["status"].setText(footer_name)
            self["footer_connection"].setText("")
        else:
            # Keep the footer deliberately clean: selected server name + connection
            # state only.  The old ARROWS/OK/MENU help text is intentionally gone.
            footer_name = str(name)[:42]
            self["status"].setText(footer_name)
            state = str(status or "").strip().upper()
            connected = state in ("ONLINE", "CONNECTED", "ACTIVE", "ENABLED", "READY")
            footer_text = _("Portal Connected") if connected else _(state or "NOT CHECKED")
            self["footer_connection"].setText(footer_text[:20])
            try:
                color = "#39ff88" if connected else ("#ffd166" if state == "SLOW" else ("#ff6677" if state in ("OFFLINE", "DISABLED", "EXPIRED", "BLOCKED") else "#9fc6df"))
                if self["footer_connection"].instance is not None:
                    self["footer_connection"].instance.setForegroundColor(parseColor(color))
            except Exception as exc:
                optional_failure("ui.portal_footer_connection_color", exc)
        self._portal_footer_name = footer_name
        self._portal_footer_connection = footer_text[:20]
        self._position_footer_group()
        self._render_source_badges(idx)

    def _set_check_progress(self, text):
        """Set transient Check All text and keep the compact footer row aligned."""
        self._portal_check_progress_text = str(text or "")
        self["check_progress"].setText(self._portal_check_progress_text)
        self._position_footer_group()

    def _position_footer_group(self):
        """Lay out check | server | connection | page/source on one 5px-gap row."""
        try:
            # R65: geometry only.  Give the existing footer text enough real
            # room so the portal name and ONLINE/CONNECTED field can never
            # collide or clip.  Font, colour and content remain untouched.
            gap = 8
            right_edge = 1860
            row_y = 990
            row_h = 36
            page_text = str(getattr(self, "_portal_page_text", "") or "")
            name_text = str(getattr(self, "_portal_footer_name", "") or "")[:42]
            conn_text = str(getattr(self, "_portal_footer_connection", "") or "")[:20]
            check_text = str(getattr(self, "_portal_check_progress_text", "") or "")

            # Keep page/check geometry unchanged.  Only the portal name and
            # connection fields get a conservative receiver-font measurement.
            font_px = 16
            footer_text_measure_px = 20
            # FIX25: never squeeze the permanent footer trio just to keep its
            # left edge inside the source-card column.  The user-facing rule is
            # the opposite: keep the complete text readable and pin the RIGHT
            # edge of the final PORTAL/M3U word to the same x=1860 edge used by
            # the source cards and the blue action key.  If the row is wider,
            # it is allowed to grow naturally to the left.
            page_w = max(190, min(360, _portal_settings_text_width_px(page_text, font_px) + 12))
            conn_w = 0 if not conn_text else max(110, min(190, _portal_settings_text_width_px(conn_text, footer_text_measure_px) + 18))
            name_w = max(130, min(340, _portal_settings_text_width_px(name_text, footer_text_measure_px) + 20))
            check_w = 0 if not check_text else max(160, min(520, _portal_settings_text_width_px(check_text, font_px) + 12))

            page_x = right_edge - page_w
            conn_x = page_x - (gap if conn_w else 0) - conn_w
            name_x = conn_x - gap - name_w
            check_x = name_x - (gap if check_w else 0) - check_w
            # Check progress is transient and may extend farther left.  Clamp
            # only that transient field to the safe canvas; never shrink the
            # permanent name/connection/page fields.
            if check_w and check_x < 620:
                check_x = 620
                check_w = max(1, name_x - gap - check_x)

            desktop = getDesktop(0).size()
            sx = float(desktop.width()) / 1920.0
            sy = float(desktop.height()) / 1080.0
            y = int(round(row_y * sy))
            h = max(1, int(round(row_h * sy)))
            layout = (
                ("check_progress", check_x if check_w else name_x, max(1, check_w)),
                ("status", name_x, name_w),
                ("footer_connection", conn_x, max(1, conn_w)),
                ("portal_page", page_x, page_w),
            )
            for widget_name, x, w in layout:
                inst = self[widget_name].instance
                if inst is not None:
                    inst.move(ePoint(int(round(x * sx)), y))
                    inst.resize(eSize(max(1, int(round(w * sx))), h))
        except Exception as exc:
            optional_failure("ui.portal_footer_group", exc)

    def _show_check_notice(self, title, message):
        """Show Check All result inside PortalListScreen, never as a new modal screen."""
        self._check_notice_visible = True
        self["check_notice_title"].setText(str(title or _("Portal Check")))
        self["check_notice_message"].setText(str(message or ""))
        self["check_notice_ok"].setText(_("OK"))
        try:
            panel = asset("update_notice_glass_870x520.png")
            if panel and os.path.isfile(panel) and self["check_notice_panel"].instance is not None:
                self["check_notice_panel"].instance.setPixmapFromFile(panel)
            button = asset("us6521_key_green_170x42.png")
            if button and os.path.isfile(button) and self["check_notice_button"].instance is not None:
                self["check_notice_button"].instance.setPixmapFromFile(button)
        except Exception as exc:
            optional_failure("ui.portal_check_notice_art", exc)
        for widget_name in ("check_notice_panel", "check_notice_title", "check_notice_message", "check_notice_button", "check_notice_ok"):
            try:self[widget_name].show()
            except Exception as exc:optional_failure("ui.portal_check_notice_show", exc)

    def _hide_check_notice(self):
        self._check_notice_visible = False
        for widget_name in ("check_notice_panel", "check_notice_title", "check_notice_message", "check_notice_button", "check_notice_ok"):
            try:self[widget_name].hide()
            except Exception as exc:optional_failure("ui.portal_check_notice_hide", exc)

    def refresh(self):
        current = self._index()
        if current is None:
            current = getattr(self, "_portal_visual_index", 0)
        self._render_portal_rows(current)
        self._selection_changed()

    def _index(self):
        if not self.profiles:
            return None
        i = self["list"].getSelectedIndex()
        return i if 0 <= i < len(self.profiles) else None

    def reload_text_file(self):
        self.profiles = load_profiles(); self.refresh(); self["status"].setText(_("Profiles reloaded"))

    def add_portal(self):
        if not self._busy:
            self.session.openWithCallback(self._got_portal, InputBox, title=_("Portal / M3U URL"), text="https://")

    def _got_portal(self, portal):
        if portal:
            self._new_portal = str(portal).strip()
            if requires_http_consent(self._new_portal):
                self.session.openWithCallback(self._got_portal_http_confirmed,MessageBox,http_warning_for(self._new_portal),MessageBox.TYPE_YESNO)
                return
            self._route_new_source()

    def _got_portal_http_confirmed(self,answer):
        if answer:
            self._route_new_source()
        else:
            self["status"].setText(_("HTTP source was not added"))

    def _route_new_source(self):
        raw=str(getattr(self,"_new_portal","") or "").strip()
        if not raw:return
        if _looks_like_m3u_url(raw):
            self._new_source_type="m3u"
            self._save_m3u_profile()
            return
        self._new_source_type="detecting"
        self["status"].setText(_("Checking source type…"))
        def work(handle):
            return _probe_m3u_url(raw,timeout=min(int(load_settings().get("timeout",10) or 10),8),
                                  cancel_event=getattr(handle,"cancel_event",None))
        def ok(is_m3u):
            if is_m3u:
                self._new_source_type="m3u";self._save_m3u_profile()
            else:
                self._new_source_type="stalker"
                self["status"].setText(_("Stalker portal detected"))
                self.session.openWithCallback(self._got_mac,InputBox,title=_("MAC address"),text="00:1A:79:")
        def failed(exc):
            # A Stalker endpoint commonly rejects a playlist-style probe.  Do
            # not treat that as an add failure; continue with the normal MAC flow.
            self._new_source_type="stalker"
            self["status"].setText(_("Enter MAC for Stalker portal"))
            self.session.openWithCallback(self._got_mac,InputBox,title=_("MAC address"),text="00:1A:79:")
        if not self._run_async(work,ok,failed):
            failed(RuntimeError(_("Source probe unavailable")))

    def _save_m3u_profile(self):
        try:
            raw_portal=str(getattr(self,"_new_portal","") or "").strip()
            new_profile={"portal":raw_portal,"mac":"","source_type":"m3u","state":"M3U • READY",
                         "account_state":"M3U PLAYLIST","health":"unknown","source":"saved",
                         "allow_http_fallback":False,"http_fallback_accepted":False,
                         "tls_fallback_accepted":False,"tls_mode":"strict","device_profile":"auto"}
            new_profile=mark_http_consent(new_profile,True)
            _validate_profile_client(new_profile)

            rows=load_profiles()
            wanted=raw_portal.rstrip("/").casefold()
            replaced=False
            for idx,row in enumerate(rows):
                if (str(row.get("portal") or "").rstrip("/").casefold()==wanted and
                    str(row.get("source_type") or "").lower()=="m3u"):
                    rows[idx]=new_profile;replaced=True;break
            if not replaced:rows.append(new_profile)

            enable_profile(new_profile)
            save_profiles(rows)

            loaded=load_profiles()
            found=None
            for row in loaded:
                if str(row.get("source_type") or "").lower()!="m3u":continue
                saved_url=str(row.get("portal") or "").rstrip("/").casefold()
                if saved_url==wanted or saved_url.endswith(wanted):
                    found=row;break
            if found is None:
                raise RuntimeError(_("Playlist was written but did not survive profile reload"))

            self.profiles=loaded;self.refresh()
            try:
                idx=self.profiles.index(found)
                self["list"].moveToIndex(idx)
                self._portal_visual_index=idx
                self._selection_changed()
            except Exception as exc: diagnostic_failure("ui.portallist.failsoft", exc)
            self["status"].setText(_("M3U playlist added"))
        except Exception as exc:
            self.profiles=load_profiles()
            self.refresh()
            self.session.open(MessageBox,_("M3U add failed: %s")%exc,MessageBox.TYPE_ERROR,timeout=8)

    def _got_mac(self, mac):
        if not mac:
            return
        try:
            raw_portal = self._new_portal.strip()
            allow_fallback = not raw_portal.lower().startswith(("http://", "https://"))
            _validate_profile_client({"portal": raw_portal, "mac": mac.strip().upper(), "allow_http_fallback": False, "http_fallback_accepted": False})
            new_profile = {"portal": raw_portal, "mac": mac.strip().upper(), "source_type":"stalker", "state": "Not checked",
                           "allow_http_fallback": False, "http_fallback_accepted": False,
                           "tls_fallback_accepted": False, "tls_mode": "auto", "device_profile": "auto"}
            new_profile = mark_http_consent(new_profile, True)
            enable_profile(new_profile)
            self.profiles.append(new_profile)
            try:
                save_profiles(self.profiles)
            except Exception as exc:
                self.profiles.pop()
                self.session.open(MessageBox, _("Save failed: %s") % exc, MessageBox.TYPE_ERROR, timeout=7)
                return
            self.refresh()
        except Exception as exc:
            self.session.open(MessageBox, str(exc), MessageBox.TYPE_ERROR, timeout=6)

    def delete_portal(self):
        idx = self._index()
        if idx is None or self._busy:
            return
        self._delete_index = idx
        self.session.openWithCallback(
            self._delete_confirmed,
            MessageBox,
            _("Permanently delete selected portal?\n\nIt will be removed from saved profiles, all portal import files, cached sessions and plugin-owned data."),
            MessageBox.TYPE_YESNO
        )

    def _delete_confirmed(self, answer):
        if not answer:
            return
        idx = getattr(self, "_delete_index", None)
        if idx is not None and 0 <= idx < len(self.profiles):
            old = self.profiles[idx]
            try:
                result = permanently_delete_profile(old, session=self.session, purge_related=True)
                self.profiles = load_profiles()
                self["status"].setText(_("Portal permanently deleted") if result.get("deleted") else _("Portal delete failed"))
            except Exception as exc:
                self["status"].setText(_("Delete failed: %s") % exc)
            self.refresh()

    def check_portal(self):
        idx = self._index()
        if idx is None or self._busy:
            return
        p = self.profiles[idx]; self["status"].setText(_("Checking portal..."))
        def work(handle):
            if handle.cancelled():return None
            # A real Check is deliberately patient.  Give slow providers enough
            # time to return account/profile telemetry instead of bailing early.
            started = time.monotonic(); client = _client_from_profile(p, timeout=15)
            try:
                cancel_event = getattr(handle, "cancel_event", None)
                if str(p.get("source_type") or "").lower() == "m3u" and hasattr(client, "probe"):
                    client.probe(cancel_event=cancel_event, allow_cache=False)
                info = client.account_info()
                if handle.cancelled():return None
                health_data = client.health_snapshot()
                health_latency = health_data.get("latency_ms") or int((time.monotonic() - started) * 1000)
                connections = _probe_provider_connections(client, info, cancel_event=cancel_event)
                if handle.cancelled():return None
                return (info, health_latency, health_data, client.security_warning, connections)
            finally:
                client.close()
        def ok(result):
            info, latency, health_data, security_warning, connections = result
            expiry = info.get("phone") or info.get("end_date") or info.get("expire_billing_date") or ""
            state = info.get("status") or info.get("account_status") or "ONLINE"
            warning = security_warning
            p["state"] = "%s%s%s" % (state, (" / " + str(expiry)) if expiry else "", (" / INSECURE HTTP" if warning else ""))
            p["account_state"] = str(state)
            p["expiry"] = str(expiry or "")
            p["latency_ms"] = health_data.get("latency_ms") or latency
            p["health"] = health_data.get("health") or ("online" if latency < 1500 else "slow")
            if connections:
                p["active_connections"] = int(connections["active"])
                p["max_connections"] = int(connections["max"])
                p["connections_source"] = "provider_exact"
            else:
                _clear_profile_connections(p)
            p["last_success"] = int(time.time()); p.pop("last_error", None)
            try:
                save_profiles(self.profiles)
                save_note = ""
            except Exception as exc:
                save_note = " (state not saved: %s)" % exc
            self.refresh()
            self["status"].setText((security_warning or _("Portal Connected")) + save_note)
        def fail(exc):
            p["state"]="OFFLINE"; p["health"]="offline"; p["last_error"]=str(exc)[:240]; p.pop("latency_ms", None)
            _clear_profile_connections(p)
            try: save_profiles(self.profiles)
            except Exception as exc: optional_failure("ui",exc)
            self.refresh(); self["status"].setText(_friendly_portal_error(exc))
        self._run_async(work, ok, fail)

    def check_all_portals(self):
        if self._busy or not self.profiles:
            return
        snapshot = list(self.profiles)
        progress_jobs=self._portal_progress_jobs
        self._set_check_progress(_("Checking %d portals...") % len(snapshot))
        self._start_portal_progress_poll()

        def work(handle):
            total = len(snapshot)
            cancel_event = getattr(handle, "cancel_event", None)

            def probe_one(profile):
                client = None
                try:
                    started = time.monotonic()
                    # Check All is still bounded, but no longer gives up after a
                    # tiny health-only window.  Slow providers get a real account
                    # + connection-telemetry chance, just like explicit Check.
                    client = _client_from_profile(profile, timeout=15)
                    if str(profile.get("source_type") or "").lower() == "m3u" and hasattr(client, "probe"):
                        client.probe(cancel_event=cancel_event, allow_cache=False)
                    info = client.account_info()
                    health_data = client.health_snapshot()
                    latency = health_data.get("latency_ms") or int((time.monotonic() - started) * 1000)
                    connections = _probe_provider_connections(client, info, cancel_event=cancel_event)
                    expiry = info.get("phone") or info.get("end_date") or info.get("expire_billing_date") or ""
                    state = info.get("status") or info.get("account_status") or "ONLINE"
                    warning = getattr(client, "security_warning", "")
                    label = "%s%s%s" % (state, (" / " + str(expiry)) if expiry else "", (" / INSECURE HTTP" if warning else ""))
                    return (profile, True, label, latency, str(expiry or ""), str(state), connections)
                except Exception as exc:
                    return (profile, False, "OFFLINE: %s" % str(exc)[:100], 0, "", "OFFLINE", None)
                finally:
                    if client is not None:
                        try: client.close()
                        except Exception as exc: optional_failure("ui.portal_check_close", exc)

            results = []
            workers = max(1, min(4, total))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="us-portal-check") as pool:
                future_map = {pool.submit(probe_one, profile): profile for profile in snapshot}
                done = 0
                for future in as_completed(future_map):
                    if handle.cancelled():
                        break
                    profile = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = (profile, False, "OFFLINE: %s" % str(exc)[:100], 0, "", "OFFLINE", None)
                    results.append(result)
                    done += 1
                    if not handle.cancelled():
                        try:
                            progress_jobs.put((done, total, profile.get("name") or profile.get("portal") or "Portal", bool(result[1])))
                        except Exception as exc:
                            optional_failure("ui.portal_progress_queue", exc)
            return results

        def ok(results):
            self._drain_portal_progress()
            self._pause_portal_progress_poll()
            failed = []
            for profile, success, state, latency, expiry, account_state, connections in results:
                profile["state"] = state
                profile["account_state"] = account_state
                profile["expiry"] = expiry
                profile["health"] = ("excellent" if latency < 700 else ("online" if latency < 1500 else "slow")) if success else "offline"
                if latency:
                    profile["latency_ms"] = latency
                else:
                    profile.pop("latency_ms", None)
                if success:
                    if connections:
                        profile["active_connections"] = int(connections["active"])
                        profile["max_connections"] = int(connections["max"])
                        profile["connections_source"] = "provider_exact"
                    else:
                        profile.pop("active_connections", None)
                        profile.pop("max_connections", None)
                        profile.pop("connections_source", None)
                    profile["last_success"] = int(time.time())
                    profile.pop("last_error", None)
                else:
                    _clear_profile_connections(profile)
                    profile["last_error"] = state[:240]
                    failed.append(profile)
            try:
                save_profiles(self.profiles)
            except Exception as exc:
                self._set_check_progress("")
                self["status"].setText(_("Checks done; save failed: %s") % exc)
                self.refresh()
                return
            self.refresh()
            self._set_check_progress("")
            if not failed:
                self._show_check_notice(_("Portal Check"), _("All portals passed the check."))
                return
            self._failed_profiles = failed
            self._show_check_notice(
                _("Portal Check"),
                _("%d portal(s) working.  %d failed.\n\nFailed portals were NOT disabled; use RED if you want to disable one.") % (len(results)-len(failed), len(failed))
            )

        def failed_bulk(error):
            self._drain_portal_progress()
            self._pause_portal_progress_poll()
            self._set_check_progress("")
            self["status"].setText(_("Bulk check failed: %s") % error)
        if not self._run_async(work, ok, failed_bulk):
            self._pause_portal_progress_poll()

    def _delete_failed_confirmed(self, answer):
        failed = getattr(self, "_failed_profiles", [])
        self._failed_profiles = []
        if not answer or not failed:
            return
        failed_keys = set((p.get("portal", "").rstrip("/").lower(), p.get("mac", "").upper()) for p in failed)
        kept = [p for p in self.profiles if (p.get("portal", "").rstrip("/").lower(), p.get("mac", "").upper()) not in failed_keys]
        try:
            disable_profiles(failed)
            save_profiles(kept)
            self.profiles = kept
            self.refresh()
            self["status"].setText(_("Disabled %d failed portal(s)") % len(failed))
        except Exception as exc:
            self["status"].setText(_("Disabling failed portals failed: %s") % exc)

    def _portal_open_http_confirmed(self, answer):
        idx = getattr(self, "_http_open_index", None)
        self._http_open_index = None
        if not answer:
            self["status"].setText(_("HTTP portal was not opened"))
            return
        if idx is None or idx < 0 or idx >= len(self.profiles):
            return
        profile = mark_http_consent(self.profiles[idx], True)
        self.profiles[idx] = profile
        try:
            save_profiles(self.profiles)
        except Exception as exc:
            self.session.open(MessageBox, _("Could not save HTTP consent: %s") % exc, MessageBox.TYPE_ERROR, timeout=7)
            return
        try:
            self["list"].moveToIndex(idx)
        except Exception as exc:
            optional_failure("ui.http_consent_selection", exc)
        self.open_portal()

    def open_portal(self):
        _perf_t0 = time.monotonic()
        idx = self._index()
        if idx is None or self._busy:
            return
        profile=self.profiles[idx]
        LOG.info("PERF25 portal_open begin mono_ms=%d source_type=%s", int(_perf_t0 * 1000), str(profile.get("source_type") or "stalker")[:12])
        # Legacy/imported HTTP profiles may predate the explicit transport
        # consent field.  Require a one-time UI acknowledgement before the
        # first connection, then persist it so normal opens are not noisy.
        if requires_http_consent(profile.get("portal")) and not bool(profile.get("explicit_http_accepted", False)):
            self._http_open_index = idx
            self.session.openWithCallback(
                self._portal_open_http_confirmed, MessageBox,
                http_warning_for(profile.get("portal")), MessageBox.TYPE_YESNO
            )
            return
        try:
            _validate_profile_client(profile)
        except Exception as exc:
            self.session.open(MessageBox, _("Invalid profile: %s") % exc, MessageBox.TYPE_ERROR, timeout=7)
            return

        if str(profile.get("source_type") or "").lower()=="m3u":
            # Large get.php playlists can take minutes to download. Opening a
            # source must never block on a full catalogue parse/probe. Home and
            # the category screens load only what the user actually opens.
            self["status"].setText(_("Opening M3U playlist…"))
            _phase = time.monotonic()
            from .ui_screens_splash import HomeWarmupSplashScreen
            self.session.openWithCallback(self._portal_home_closed, HomeWarmupSplashScreen, profile, PortalHomeScreen)
            LOG.info("R268 portal_open warm_splash_return elapsed_ms=%d total_ms=%d", int((time.monotonic() - _phase) * 1000), int((time.monotonic() - _perf_t0) * 1000))
            return

        _phase = time.monotonic()
        from .ui_screens_splash import HomeWarmupSplashScreen
        self.session.openWithCallback(self._portal_home_closed, HomeWarmupSplashScreen, profile, PortalHomeScreen)
        LOG.info("R268 portal_open warm_splash_return elapsed_ms=%d total_ms=%d", int((time.monotonic() - _phase) * 1000), int((time.monotonic() - _perf_t0) * 1000))

    def _portal_home_closed(self, result=None):
        # Home can discover fresher account/expiry state than PortalList had at
        # construction time. Reload persisted profiles while preserving selection.
        try:
            idx=self._index()
            self.profiles=load_profiles()
            self.refresh()
            if idx is not None and self.profiles:
                try:self["list"].moveToIndex(max(0,min(int(idx),len(self.profiles)-1)))
                except Exception as exc: diagnostic_failure("ui.portallist.failsoft", exc)
            self._selection_changed()
        except Exception as exc:
            optional_failure("ui.portal_home_refresh", exc)



class PortalLibraryListScreen(PortalListScreen):
    """Passive bundled Portal/Xtream library.

    Stability rules for OpenBH/Python 3.14:
      * no catalog parsing or list rebuild while Enigma2 is inside applySkin();
      * only one 13-row window is materialized in the GUI list at a time.
    The full TXT catalog stays as ordinary Python dictionaries in RAM.
    """

    LIBRARY_PAGE_SIZE = 13

    def __init__(self, session, kind="portal"):
        self.library_kind = "xtream" if str(kind or "").lower() == "xtream" else "portal"
        self._library_selected = set()
        self._library_mode_ready = False
        self._library_page_start = 0
        self._library_abs_index = 0
        PortalListScreen.__init__(self, session)
        # PortalListScreen has now created the proven native widgets.
        self._library_mode_ready = True
        self.profiles = []
        self._portal_visual_index = 0
        self._portal_rebuilding = False
        self._reorder_mode = False
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "cancel": self.close,
            "ok": self._library_toggle,
            "green": self._library_import,
            "red": self._library_clear,
            "yellow": self._library_select_all,
            "blue": self._library_clear,
            "up": self._portal_up,
            "down": self._portal_down,
            "left": self._portal_prev_page,
            "right": self._portal_next_page,
        }, -1)
        # Keep the GUI list empty until onShown.  This prevents a 6k-row Xtream
        # catalog from being built while Screen.createGUIScreen() is still active.
        try:
            self["list"].set_icon_rows([])
        except Exception as exc:
            optional_failure("ui.portal_library_clear_before_show", exc)

    def _reload_library_profiles(self):
        self.profiles = list(load_server_library(self.library_kind))

    def _layout_ready(self):
        # Called by Enigma2 from applySkin().  Visual setup only.  Never parse the
        # TXT catalog and never rebuild the list from inside createGUIScreen().
        self._image_layout_ready()
        self._apply_portal_settings_materials()
        self._fit_portal_color_keys()

    def _portal_list_shown(self):
        # onShown runs after applySkin/createGUIScreen has completed.  Load the
        # local TXT once, keep it in RAM, and materialize only the visible page.
        try:
            self._reload_library_profiles()
            self._library_abs_index = 0
            self._library_page_start = 0
            self._render_library_page(0)
            self._selection_changed()
        except Exception as exc:
            optional_failure("ui.portal_library_shown", exc)
            try:
                self["status"].setText(_("Open failed: %s") % exc)
            except Exception:
                pass

    def _portal_row_tuple(self, index, selected_idx):
        row = PortalListScreen._portal_row_tuple(self, index, selected_idx)
        try:
            title, icon, payload, meta = row
            meta = dict(meta or {})
            if index in self._library_selected:
                meta["status_text"] = _("SELECTED")
                meta["status_color"] = 0x39FF88
            else:
                meta["status_text"] = _("AVAILABLE")
                meta["status_color"] = self._portal_value_color
            return (title, icon, payload, meta)
        except Exception:
            return row

    def _render_library_page(self, absolute_index=None):
        total = len(self.profiles)
        if total <= 0:
            self._library_abs_index = 0
            self._library_page_start = 0
            self._portal_rebuilding = True
            try:
                self["list"].set_icon_rows([])
            finally:
                self._portal_rebuilding = False
            self._render_source_badges(None)
            return

        if absolute_index is None:
            absolute_index = self._library_abs_index
        absolute_index = max(0, min(int(absolute_index), total - 1))
        page_size = int(self.LIBRARY_PAGE_SIZE)
        start = (absolute_index // page_size) * page_size
        end = min(total, start + page_size)
        rows = [self._portal_row_tuple(i, absolute_index) for i in range(start, end)]
        local_index = absolute_index - start

        self._portal_rebuilding = True
        try:
            self["list"].set_icon_rows(rows)
            if rows:
                self["list"].moveToIndex(local_index)
        finally:
            self._portal_rebuilding = False
        self._library_page_start = start
        self._library_abs_index = absolute_index
        self._portal_visual_index = absolute_index
        self._render_source_badges(absolute_index)

    def _index(self):
        if not getattr(self, "_library_mode_ready", False):
            return PortalListScreen._index(self)
        if not self.profiles:
            return None
        try:
            local = int(self["list"].getSelectedIndex())
        except Exception:
            local = 0
        absolute = int(self._library_page_start) + max(0, local)
        if absolute >= len(self.profiles):
            absolute = len(self.profiles) - 1
        self._library_abs_index = absolute
        return absolute

    def refresh(self):
        if not getattr(self, "_library_mode_ready", False):
            return PortalListScreen.refresh(self)
        self._render_library_page(self._library_abs_index)
        self._selection_changed()

    def _selection_changed(self):
        if getattr(self, "_portal_rebuilding", False):
            return
        idx = self._index()
        if idx is None:
            title = _("Xtream Library") if self.library_kind == "xtream" else _("Portal Library")
            self["portal_page"].setText(_("%s • Library is empty") % title)
            self["status"].setText(_("BACK Portal Manager"))
            self._render_source_badges(None)
            return

        # If native list navigation changed only the local row, refresh the two
        # visible row styles without creating any off-screen rows.
        old = getattr(self, "_portal_visual_index", idx)
        self._portal_visual_index = idx
        for absolute in set((old, idx)):
            if self._library_page_start <= absolute < self._library_page_start + self.LIBRARY_PAGE_SIZE:
                local = absolute - self._library_page_start
                if 0 <= absolute < len(self.profiles):
                    try:
                        self["list"].update_icon_row(local, self._portal_row_tuple(absolute, idx))
                    except Exception as exc:
                        optional_failure("ui.portal_library_row_update", exc)

        total = len(self.profiles)
        page_size = int(self.LIBRARY_PAGE_SIZE)
        pages = max(1, (total + page_size - 1) // page_size)
        page = (idx // page_size) + 1
        title = _("Xtream Library") if self.library_kind == "xtream" else _("Portal Library")
        self["portal_page"].setText(_("%s  •  Page %d / %d  •  Source %d / %d") % (title, page, pages, idx + 1, total))
        # The dedicated Library skin intentionally has no verbose key-hint strip.
        self["status"].setText("")
        self._render_source_badges(idx)

    def _move_to_absolute(self, target):
        total = len(self.profiles)
        if total <= 0:
            return
        target = max(0, min(int(target), total - 1))
        target_page = (target // self.LIBRARY_PAGE_SIZE) * self.LIBRARY_PAGE_SIZE
        if target_page != self._library_page_start:
            self._render_library_page(target)
        else:
            self._library_abs_index = target
            try:
                self["list"].moveToIndex(target - self._library_page_start)
            except Exception as exc:
                optional_failure("ui.portal_library_move", exc)
        self._selection_changed()

    def _portal_up(self):
        if not self.profiles:
            return
        idx = self._index()
        if idx is None:
            idx = 0
        self._move_to_absolute((idx - 1) % len(self.profiles))

    def _portal_down(self):
        if not self.profiles:
            return
        idx = self._index()
        if idx is None:
            idx = 0
        self._move_to_absolute((idx + 1) % len(self.profiles))

    def _portal_prev_page(self):
        if not self.profiles:
            return
        idx = self._index() or 0
        row = idx % self.LIBRARY_PAGE_SIZE
        current_page = idx // self.LIBRARY_PAGE_SIZE
        # First-page boundary: LEFT always reaches the first real source.
        # Normal page changes keep the existing same-row behavior.
        if current_page <= 0:
            target = 0
        else:
            target_page = current_page - 1
            target = min(len(self.profiles) - 1, target_page * self.LIBRARY_PAGE_SIZE + row)
        self._move_to_absolute(target)

    def _portal_next_page(self):
        if not self.profiles:
            return
        idx = self._index() or 0
        row = idx % self.LIBRARY_PAGE_SIZE
        pages = max(1, (len(self.profiles) + self.LIBRARY_PAGE_SIZE - 1) // self.LIBRARY_PAGE_SIZE)
        current_page = idx // self.LIBRARY_PAGE_SIZE
        # Final-page boundary: RIGHT always reaches the last real source,
        # including a short final page with only a few entries.
        if current_page >= pages - 1:
            target = len(self.profiles) - 1
        else:
            target_page = current_page + 1
            target = min(len(self.profiles) - 1, target_page * self.LIBRARY_PAGE_SIZE + row)
        self._move_to_absolute(target)

    def _library_toggle(self):
        idx = self._index()
        if idx is None:
            return
        if idx in self._library_selected:
            self._library_selected.remove(idx)
        else:
            self._library_selected.add(idx)
        # Match category-selection ergonomics: one OK toggles the current
        # source and immediately advances to the next source.  Crossing a
        # 13-row page boundary is handled by _move_to_absolute(), so Free
        # Portal and Free Xtream can be selected rapidly with OK, OK, OK.
        if idx + 1 < len(self.profiles):
            self._move_to_absolute(idx + 1)
        else:
            self.refresh()

    def _library_clear(self):
        if not self._library_selected:
            return
        self._library_selected.clear()
        self.refresh()

    def _library_select_all(self):
        self._library_selected = set(range(len(self.profiles)))
        self.refresh()

    def _library_import(self):
        if not self._library_selected:
            self["status"].setText(_("Nothing selected  •  OK Select  •  GREEN Import Selected  •  BACK Portal Manager"))
            return
        chosen = [self.profiles[i] for i in sorted(self._library_selected) if 0 <= i < len(self.profiles)]
        try:
            result = import_server_library_profiles(chosen) or {}
        except Exception as exc:
            optional_failure("ui.portal_library_import", exc)
            self["status"].setText(_("Import failed  •  BACK Portal Manager"))
            return
        added = int(result.get("added", 0) or 0)
        dup = int(result.get("duplicates", 0) or 0)
        self._library_selected.clear()
        self.refresh()
        self["status"].setText(_("Import complete  •  %d added  •  %d already existed  •  no server checks were run") % (added, dup))
