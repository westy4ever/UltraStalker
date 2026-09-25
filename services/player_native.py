# -*- coding: utf-8 -*-
"""Native Ultra Stalker playback screen.

Owns the Enigma2 service lifecycle, engine selection, resume flow, overlays,
and receiver-state handoff used by Ultra Stalker.
"""
from __future__ import absolute_import, print_function

from ..core.image_budget import image_budgeted
from .. import _

import hashlib
import json
import gc
import ctypes
import os
import signal
import time
import queue
import re
import threading
import urllib.request
import urllib.parse

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter, ImageEnhance as _ImageEnhance
except Exception:
    _PILImage = None; _ImageDraw = None; _ImageFilter = None

from ..securefs import secure_private_dir
from ..persistent_cache import ROOT as PERSISTENT_CACHE_ROOT, GENERATED as PERSISTENT_GENERATED_DIR, persistent_write_gate
from ..storage import add_recently_played, touch_recently_played, load_settings, save_settings, load_profiles
from ..title_clean import display_title as _catalogue_title
from ..identity import content_digest
from ..ui_artwork_helpers import _fit_live_picon_canvas, _normalize_provider_image_url
from ..netsec import build_safe_media_opener, validate_remote_media_url
from ..ui_settings_inline_choice import SettingsInlineChoiceOverlay
from .subtitles_online import (
    cached_subtitle, search_language, download_candidate, parse_srt,
    search_subsource_language, download_subsource_candidate,
    interface_subtitle_language_code, subtitle_language_name,
    is_supported_subtitle_language,
)
from ..language_catalog import interface_language_choices
from ..core.recovery import runtime_interruption_action, smart_recovery_allowed
from ..core.runtime_log import breadcrumb as runtime_breadcrumb
from ..log import get_logger, optional_failure
from ..core.executor import LazyThreadPoolExecutor
from .player_runtime import _matching_external_player_pids, _descendant_pids, _all_external_player_pids

try:
    from urllib.parse import unquote, urlparse
except ImportError:
    from urllib import unquote
    from urlparse import urlparse

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
try:
    from Components.MultiContent import MultiContentEntryPixmapAlphaBlend
except Exception:
    MultiContentEntryPixmapAlphaBlend = MultiContentEntryPixmapAlphaTest
try:
    from Components.ScrollLabel import ScrollLabel
except Exception:
    ScrollLabel = Label
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Components.ServiceEventTracker import ServiceEventTracker
try:
    from Components.ServiceEventTracker import InfoBarBase
except Exception:
    class InfoBarBase(object):
        def __init__(self, *args, **kwargs):
            pass
from enigma import ePicLoad, ePoint, eSize, getDesktop, eServiceReference, eTimer, iPlayableService, iServiceInformation, gFont, eListboxPythonMultiContent, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_HALIGN_CENTER, RT_VALIGN_CENTER, loadPNG
from skin import parseColor
try:
    from enigma import eDVBVolumecontrol
except Exception:
    eDVBVolumecontrol = None
from Screens.MessageBox import MessageBox
try:
    from Screens.SubtitleDisplay import SubtitleDisplay
except Exception:
    SubtitleDisplay = None
try:
    from Screens.ChoiceBox import ChoiceBox
except Exception:
    ChoiceBox = None
from Screens.Screen import Screen
from Tools.BoundFunction import boundFunction

LOG = get_logger()
# Beta59: player visuals and the native player share one progress-frame worker.
from .player_visuals import _PROGRESS_FRAME_EXECUTOR, _PROGRESS_FRAME_PENDING, _PROGRESS_FRAME_LOCK

# Stage-1 player split: helpers are imported back into this module so external
# callers keep the established Ultra Stalker player API surface.
from .player_core import (
    _PLAYER_BG_EXECUTOR, _process_rss_kb, _malloc_trim, force_session_silence,
    _optional_infobar, InfoBarAudioSelection, InfoBarMoviePlayerSummarySupport, eAVSwitch,
    InfoBarNotifications, InfoBarSeek, InfoBarSubtitleSupport, InfoBarSummarySupport,
    shutdown_player_workers, ENGINE_NAMES, _engine_label, _extension, _quality,
    _available_engines,
)
from .player_artwork import (
    _fallback_player_frames, _adaptive_player_frames, _progress_neon_frame,
    _schedule_progress_neon_frame, _asset,
    _image_value, _adaptive_info_frames, _cached_poster,
    _prepare_player_poster_fill, _prepare_live_picon, _player_title_logo_source,
    _prepare_player_title_logo_cache_path, _prepare_player_title_logo,
    _apply_player_font_scale,
)
from .player_overlays import (
    ONLINE_SUBTITLE_SKIN, OnlineSubtitleOverlay, SUBTITLE_STYLE_COLORS, SUBTITLE_POSITION_PRESETS,
    _subtitle_color_name, _subtitle_position_name, _subtitle_glass_assets,
    SubtitleGlassList, SUBTITLE_GLASS_SKIN, SubtitleGlassChoiceScreen,
    PLAYER_INFO_SKIN, PlayerInformationOverlay, LIVE_ZAP_SKIN,
    _clean_live_name, _zap_palette, _zap_assets, _fit_zap_picon, UltraStalkerLiveZapList,
    UltraInfobarVisibility, NEXT_EPISODE_SKIN, UltraStalkerNextEpisodePrompt,
)
from ..receiver_video import (
    capture_aspect_mode, restore_aspect_mode, apply_aspect_mode,
    next_aspect_mode, aspect_mode_label,
)

# Session-scoped switch intentionally remains owned by the main player module
# because UltraStalkerPlayer mutates it with ``global`` during playback.
NEXT_EPISODE_AUTOPLAY_SESSION = True

# R45: subtitle resume is deliberately RAM-only. The map lives for the current
# Enigma2 process and disappears on GUI/receiver restart. No subtitle path or
# timing state is written by this feature.
_SUBTITLE_SESSION_STATE = {}
_SUBTITLE_SESSION_LOCK = threading.RLock()
_SUBTITLE_SESSION_MAX = 96


class _ExternalSubtitleProviderBridge(object):
    """Ultra-owned adapter around the public SubsSupport / SubsSupportPro API.

    The external plugin owns search, download, rendering, timing, FPS and its
    own subtitle UI. Ultra Stalker only starts/stops the provider and forwards
    lifecycle hints such as seek/close. No subtitle files are copied, moved,
    parsed, cached or activated by Ultra while this bridge is active.
    """

    PROVIDERS = ("subssupport", "subssupportpro")

    def __init__(self, player):
        self.player = player
        self.kind = ""
        self.owner = None
        self.status_screen_cls = None

    def _load_classes(self, kind):
        kind = str(kind or "").strip().lower()
        if kind == "subssupportpro":
            from Plugins.Extensions.SubsSupportPro import SubsProSupport
            from Plugins.Extensions.SubsSupportPro.subtitles import SubsStatusScreen
            return SubsProSupport, SubsStatusScreen
        if kind == "subssupport":
            from Plugins.Extensions.SubsSupport import SubsSupport
            from Plugins.Extensions.SubsSupport.subtitles import SubsStatusScreen
            return SubsSupport, SubsStatusScreen
        raise ValueError("unsupported subtitle provider")

    def _build_owner(self, kind):
        owner_cls, status_cls = self._load_classes(kind)
        kwargs = dict(
            session=self.player.session,
            autoLoad=False,
            showGUIInfoMessages=True,
            embeddedSupport=False,
            preferEmbedded=False,
            searchSupport=True,
        )
        if kind == "subssupportpro":
            kwargs["useSubclassKeymap"] = False
        owner = owner_cls(**kwargs)
        self.kind = kind
        self.owner = owner
        self.status_screen_cls = status_cls
        return owner

    def ensure(self, kind):
        kind = str(kind or "").strip().lower()
        if kind not in self.PROVIDERS:
            return None
        if self.owner is not None and self.kind == kind:
            return self.owner
        self.close()
        return self._build_owner(kind)

    def open_menu(self, kind):
        owner = self.ensure(kind)
        if owner is None:
            return False
        owner.subsMenu()
        return True

    def is_loaded(self):
        owner = self.owner
        if owner is None:
            return False
        try:
            return bool(owner.isSubsLoaded())
        except Exception:
            return False

    def open_status(self):
        owner = self.owner
        status_cls = self.status_screen_cls
        if owner is None or status_cls is None:
            return False
        if not self.is_loaded():
            owner.subsMenu()
            return True
        self.player.session.open(
            status_cls,
            owner.setSubsDelay, owner.getSubsDelay,
            owner.subscribeOnSubsDelayChanged, owner.unsubscribeOnSubsDelayChanged,
            owner.setSubsDelayToNextSubtitle, owner.setSubsDelayToPrevSubtitle,
            owner.setSubsFps, owner.getSubsFps,
            200, False,
        )
        return True

    def after_seek(self):
        owner = self.owner
        if owner is None or not self.is_loaded():
            return
        try:
            owner.playAfterSeek()
        except Exception as exc:
            optional_failure("player.external_subtitle_seek", exc)

    def play_state(self, value):
        owner = self.owner
        if owner is None or not self.is_loaded():
            return
        try:
            value = str(value or "")
            if value == "||" or value.startswith(">>") or value.startswith("<<") or value.startswith("/"):
                owner.pauseSubs()
            elif value == ">":
                owner.resumeSubs()
                owner.playAfterSeek()
        except Exception as exc:
            optional_failure("player.external_subtitle_play_state", exc)

    def service_started(self):
        owner = self.owner
        if owner is None or not self.is_loaded():
            return
        try:
            owner.resumeSubs()
            owner.playAfterSeek()
        except Exception as exc:
            optional_failure("player.external_subtitle_service_started", exc)

    def snapshot(self):
        """Return provider-owned subtitle state without copying its file."""
        owner = self.owner
        if owner is None or not self.is_loaded():
            return None
        try:
            path = str(owner.getSubsPath() or "")
        except Exception:
            path = ""
        if not path or not os.path.isfile(path):
            return None
        try:
            delay = owner.getSubsDelay()
            delay = int(delay) if delay is not None else 0
        except Exception:
            delay = 0
        try:
            fps = owner.getSubsFps()
            fps = float(fps) if fps is not None else None
        except Exception:
            fps = None
        return {"source": self.kind, "path": path, "delay_ms": delay, "fps": fps}

    def restore(self, state):
        """Restore one RAM-session subtitle through the provider public API."""
        state = state if isinstance(state, dict) else {}
        kind = str(state.get("source") or "").strip().lower()
        path = str(state.get("path") or "")
        if kind not in self.PROVIDERS or not path or not os.path.isfile(path):
            return False
        owner = self.ensure(kind)
        if owner is None or not owner.loadSubs(path, newService=True):
            return False
        try:
            delay = state.get("delay_ms")
            if delay is not None:
                owner.setSubsDelay(int(delay))
        except Exception as exc:
            optional_failure("player.external_subtitle_restore_delay", exc)
        try:
            fps = state.get("fps")
            if fps is not None:
                owner.setSubsFps(float(fps))
        except Exception as exc:
            optional_failure("player.external_subtitle_restore_fps", exc)
        try:
            owner.resumeSubs()
            owner.playAfterSeek()
        except Exception as exc:
            optional_failure("player.external_subtitle_restore_resume", exc)
        return True

    def close(self):
        owner = self.owner
        self.owner = None
        self.kind = ""
        self.status_screen_cls = None
        if owner is None:
            return
        try:
            owner.exitSubs()
        except Exception as exc:
            optional_failure("player.external_subtitle_close", exc)
        try:
            cleanup_embedded=getattr(owner,"exitEmbeddedSubs",None)
            if callable(cleanup_embedded):cleanup_embedded()
        except Exception as exc:
            optional_failure("player.external_subtitle_embedded_close", exc)


PLAYER_SKIN = """
<screen name="UltraStalkerPlayer" position="0,0" size="1920,1080" backgroundColor="#ff000000" flags="wfNoBorder">
    <widget name="resume_mask" position="0,0" size="1920,1080" font="Regular;1" foregroundColor="#000000" backgroundColor="#000000" transparent="0" zPosition="100" />

    <widget name="adaptive_main" position="60,792" size="1800,200" alphatest="blend" transparent="1" zPosition="5" />

    <!-- VOD/episode poster: separate oversized neon layer fixes Enigma2 clipping. -->
    <widget name="logo" position="60,617" size="250,375" alphatest="blend" scale="1" backgroundColor="#07111d" transparent="0" zPosition="7" />
    <widget name="poster_neon_halo" position="30,597" size="310,415" alphatest="blend" transparent="1" zPosition="9" />
    <widget name="adaptive_poster" position="50,607" size="270,395" alphatest="blend" transparent="1" zPosition="10" />
    <!-- Live-only 220x132 picon card, centered and aspect-safe. -->
    <!-- Adaptive frame stays behind the actual picon. The picon is the lit foreground subject. -->
    <widget name="adaptive_live_picon" position="97,828" size="240,152" alphatest="blend" transparent="1" zPosition="9" />
    <widget name="live_picon" position="107,838" size="220,132" alphatest="blend" scale="1" transparent="1" zPosition="12" />

    <!-- Live retains its original compact labels. VOD/episodes use the centered premium title area below. -->
    <widget name="channel" position="350,797" size="1110,56" font="Regular;34" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1" />
    <widget name="category" position="1480,808" size="120,32" font="Regular;18" foregroundColor="#ffffff" backgroundColor="#132633" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="quality" position="1615,808" size="110,32" font="Regular;18" foregroundColor="#d8e8ef" backgroundColor="#132633" halign="center" valign="center" transparent="1" zPosition="6" />
    <widget name="retry_status" position="1470,808" size="245,32" font="Regular;17" foregroundColor="#f4d27a" backgroundColor="#000000" halign="right" valign="center" transparent="1" zPosition="20" noWrap="1" />
    <widget name="episode_marker" position="370,808" size="170,28" font="Regular;22" halign="left" valign="center" foregroundColor="#dce8ed" backgroundColor="#000000" transparent="1" zPosition="8" noWrap="1" />
    <widget name="vod_title" position="430,804" size="1220,72" font="Regular;52" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="8" noWrap="1" />
    <widget name="title_logo" position="430,804" size="1220,72" alphatest="blend" scale="1" transparent="1" zPosition="9" />

    <!-- Clock/date live inside the main Player panel at its far right. -->
    <widget name="server_name" position="1460,866" size="375,28" font="Regular;24" halign="right" valign="center" foregroundColor="#d7eef7" backgroundColor="#000000" transparent="1" zPosition="22" noWrap="1" shadowColor="#000000" shadowOffset="1,1" />
    <widget source="global.CurrentTime" render="Label" position="1725,804" size="110,38" font="Regular;31" halign="right" valign="center" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="10">
        <convert type="ClockToText">Format:%%H:%%M</convert>
    </widget>
    <widget source="global.CurrentTime" render="Label" position="1705,844" size="130,22" font="Regular;14" halign="right" valign="center" foregroundColor="#d7dee2" backgroundColor="#000000" transparent="1" zPosition="10">
        <convert type="ClockToText">Format:%%a %%d %%b %%Y</convert>
    </widget>

    <widget name="skip_hint" position="370,850" size="265,28" font="Regular;16" foregroundColor="#7fe8ff" backgroundColor="#000000" transparent="1" zPosition="8" />
    <widget name="now" position="650,850" size="525,28" font="Regular;18" foregroundColor="#d4dde3" backgroundColor="#000000" halign="center" transparent="1" zPosition="6" />
    <widget name="connection" position="1140,850" size="440,28" font="Regular;16" foregroundColor="#e0eef2" backgroundColor="#000000" halign="right" transparent="1" zPosition="7" />

    <widget name="vod_progress" position="370,890" size="1350,8" borderWidth="0" foregroundColor="#203542" backgroundColor="#203542" zPosition="5" />
    <widget name="adaptive_progress_track" position="370,890" size="1350,8" alphatest="blend" transparent="1" zPosition="6" />
    <widget name="progress_neon" position="370,877" size="1350,34" alphatest="blend" transparent="1" zPosition="9" />
    <widget name="elapsed_time" position="370,901" size="190,24" font="Regular;16" foregroundColor="#d8e0e5" backgroundColor="#000000" transparent="1" zPosition="6" noWrap="1"/>
    <widget name="total_time" position="840,901" size="410,24" font="Regular;16" foregroundColor="#d8e0e5" backgroundColor="#000000" halign="center" transparent="1" zPosition="6" noWrap="1"/>
    <widget name="remaining_time" position="1530,901" size="190,24" font="Regular;16" foregroundColor="#d8e0e5" backgroundColor="#000000" halign="right" transparent="1" zPosition="6" noWrap="1"/>
    <!-- Live only: transparent Now/Next EPG text sits directly on the InfoBar. -->
    <widget name="live_epg_now" position="350,874" size="1340,29" font="Regular;21" foregroundColor="#ffffff" backgroundColor="#000000" transparent="1" zPosition="12" noWrap="1" />
    <widget name="live_epg_next" position="350,904" size="1340,27" font="Regular;18" foregroundColor="#a9bac5" backgroundColor="#000000" transparent="1" zPosition="12" noWrap="1" />

    <!-- Adaptive bordered technical cards. -->
    <widget name="chip_video_quality" position="370,934" size="150,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="video_quality" position="370,934" size="150,42" font="Regular;19" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
    <!-- Movies/Series only: one-shot decoder quality badge.  The 134x42 slot
         contains the existing 122x34 badge at native size with 6x4 px breathing room. -->
    <widget name="vod_quality_badge" position="378,934" size="134,42" alphatest="blend" scale="1" transparent="1" zPosition="7"/>
    <widget name="chip_video_codec" position="570,934" size="130,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="video_codec" position="570,934" size="130,42" font="Regular;19" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
    <widget name="chip_audio_codec" position="750,934" size="120,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="audio_codec" position="750,934" size="120,42" font="Regular;19" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
    <widget name="chip_stream" position="920,934" size="150,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="extension" position="920,934" size="150,42" font="Regular;19" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
    <widget name="chip_audio" position="1120,934" size="150,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="audio_tag" position="1120,934" size="150,42" font="Regular;19" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
    <widget name="chip_subtitles" position="1320,934" size="170,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="subtitle_tag" position="1320,934" size="170,42" font="Regular;19" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
    <widget name="chip_engine" position="1540,934" size="210,42" alphatest="blend" transparent="1" zPosition="5"/>
    <widget name="engine" position="1540,934" size="210,42" font="Regular;18" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>

    <widget name="online_subtitle" position="170,805" size="1580,150" font="Regular;38" halign="center" valign="bottom" foregroundColor="#ffffff" shadowColor="#000000" shadowOffset="3,3" transparent="1" zPosition="4" />

    <!-- Premium key bar aligns directly under the main Player panel; buttons only. -->
    <widget name="adaptive_keybar" position="320,1002" size="1540,58" alphatest="blend" transparent="1" zPosition="5" />
    <ePixmap pixmap="%(cinred)s" position="385,1010" size="170,42" alphatest="blend" scale="1" zPosition="7" />
    <widget name="key_red" position="393,1010" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="8"  shadowColor="#000000" shadowOffset="1,1"/>
    <ePixmap pixmap="%(cingreen)s" position="765,1010" size="170,42" alphatest="blend" scale="1" zPosition="7" />
    <widget name="key_green" position="773,1010" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="8"  shadowColor="#000000" shadowOffset="1,1"/>
    <ePixmap pixmap="%(cinyellow)s" position="1145,1010" size="170,42" alphatest="blend" scale="1" zPosition="7" />
    <widget name="key_yellow" position="1153,1010" size="154,42" font="Regular;22" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="8"  shadowColor="#000000" shadowOffset="1,1"/>
    <ePixmap pixmap="%(cinblue)s" position="1525,1010" size="170,42" alphatest="blend" scale="1" zPosition="7" />
    <widget name="key_blue" position="1525,1010" size="170,42" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" transparent="1" zPosition="8" noWrap="1" shadowColor="#000000" shadowOffset="1,1"/>
</screen>
""" % {
    "cinred": _asset("us6521_key_red_170x42.png"),
    "cingreen": _asset("us6521_key_green_170x42.png"),
    "cinyellow": _asset("us6521_key_yellow_170x42.png"),
    "cinblue": _asset("us6521_key_blue_170x42.png"),
}


PLAYER_SKIN=_apply_player_font_scale(PLAYER_SKIN)

class UltraStalkerPlayer(
    InfoBarBase,
    UltraInfobarVisibility,
    InfoBarAudioSelection,
    InfoBarSeek,
    InfoBarNotifications,
    InfoBarSummarySupport,
    InfoBarSubtitleSupport,
    InfoBarMoviePlayerSummarySupport,
    Screen,
):
    """Full-screen Ultra Stalker live/VOD player and InfoBar controller."""

    skin = PLAYER_SKIN
    ALLOW_SUSPEND = True

    def __init__(self, session, streamurl, name, media_type="itv", servicetype=4097, item=None, reuse_current=False):
        Screen.__init__(self, session)
        self.session = session
        self._return_aspect_ratio = capture_aspect_mode(switch_cls=eAVSwitch)
        self.streamurl = str(streamurl or "").strip()
        self.media_type = str(media_type or "itv")
        self.item = item if isinstance(item, dict) else {}
        self._cfg_clean_titles = bool(load_settings().get("clean_titles", True))
        _raw_name = str(self.item.get("_raw_name") or name or "Ultra Stalker stream")
        if self.media_type in ("itv","live"):
            self.name = _clean_live_name(_raw_name,self._cfg_clean_titles)
        else:
            self.name = (_catalogue_title(_raw_name) or _raw_name) if self._cfg_clean_titles else _raw_name.strip()
        self._return_service_ref_string=str(self.item.get("_return_service_ref_string") or "")
        self._service_handoff_done=False
        self.reuse_current = bool(reuse_current and self.media_type in ("itv","live"))
        _zap_snapshot=list(self.item.get("_live_folder_channels") or []) if self.media_type in ("itv","live") else []
        self._zap_page_loader=self.item.get("_live_page_loader") if callable(self.item.get("_live_page_loader")) else None
        try:self._zap_page_size=max(1,int(self.item.get("_live_page_size") or len(_zap_snapshot) or 10))
        except Exception:self._zap_page_size=max(1,len(_zap_snapshot) or 10)
        try:self._zap_total=max(len(_zap_snapshot),int(self.item.get("_live_folder_total") or 0))
        except Exception:self._zap_total=len(_zap_snapshot)
        try:self._zap_index=max(0,int(self.item.get("_live_absolute_index") or 0))
        except Exception:self._zap_index=0
        try:_zap_page=max(1,int(self.item.get("_live_folder_page") or (self._zap_index//self._zap_page_size+1)))
        except Exception:_zap_page=max(1,self._zap_index//self._zap_page_size+1)
        if self.media_type in ("itv","live") and self._zap_total:
            self._zap_channels=[None]*self._zap_total
            _zap_base=max(0,(_zap_page-1)*self._zap_page_size)
            for _i,_row in enumerate(_zap_snapshot):
                _pos=_zap_base+_i
                if _pos<len(self._zap_channels) and isinstance(_row,dict):self._zap_channels[_pos]=_row
        else:
            self._zap_channels=_zap_snapshot
        self._zap_title=str(self.item.get("_live_folder_title") or _("Live TV"))
        self._zap_category_id=str(self.item.get("_live_category_id") or self.item.get("category_id") or self.item.get("genre_id") or "")
        self._zap_categories=[dict(x) for x in (self.item.get("_live_categories") or []) if isinstance(x,dict)]
        self._zap_pending_drawer_context=None
        self._zap_switch_context=None
        self._zap_last_ok=0.0
        self._zap_open=False
        self._zap_infobar_armed=False
        for mixin in (
            InfoBarBase,
            UltraInfobarVisibility,
            InfoBarAudioSelection,
            InfoBarSeek,
            InfoBarNotifications,
            InfoBarSummarySupport,
            InfoBarSubtitleSupport,
            InfoBarMoviePlayerSummarySupport,
        ):
            try:
                mixin.__init__(self)
            except Exception as exc:
                LOG.warning("Player mixin init failed (%s): %s", mixin, exc)

        cfg = load_settings()
        # Stage-4: Player settings are a session snapshot. The settings screen is
        # not reachable inside this Player, so repeatedly deep-copying the full
        # settings/API-key structure during timers and playback events adds I/O/CPU
        # without changing runtime behaviour. Keep the one normalized snapshot and
        # precompute hot-path values used by progress, resume and recovery logic.
        self._cfg_resume_behavior = str(cfg.get("resume_behavior", "always") or "always").lower()
        self._cfg_crash_safe_progress = bool(cfg.get("crash_safe_progress", True))
        self._cfg_progress_interval_ms = (int(cfg.get("progress_save_seconds", 10)) if self._cfg_crash_safe_progress else 10) * 1000
        self._cfg_next_episode_countdown = int(cfg.get("next_episode_countdown", 10) or 10)
        self._cfg_timeout = max(8.0, float(cfg.get("timeout", 10) or 10) + 5.0)
        self._cfg_diagnostic_logging = bool(cfg.get("diagnostic_logging", False))
        self._cfg_completion_threshold = max(80, min(99, int(cfg.get("completion_threshold", 93)))) / 100.0
        self._cfg_completion_remaining_pts = max(30, min(900, int(cfg.get("completion_remaining_seconds", 180)))) * 90000
        self._cfg_auto_remove_completed = bool(cfg.get("auto_remove_completed", True))
        self._history_profile = {"portal": self.item.get("_portal", ""), "mac": self.item.get("_mac", "")}
        self._history_item = self.item.get("_history_item") if isinstance(self.item.get("_history_item"), dict) else self.item
        try:
            self._subtitle_session_key = content_digest(self._history_profile, self.media_type, self._history_item)
        except Exception:
            self._subtitle_session_key = ""
        self._subtitle_session_restore_done = False
        self._subtitle_session_restore_pending = False
        self._subtitle_session_restore_attempts = 0
        self._subtitle_session_restore_started_at = 0.0
        self._subtitle_session_post_refresh_pending = False
        # Set only by a successful in-player Live channel selection.  The item is
        # committed to Recent/Home on the next real evStart, not merely when a
        # provider link was resolved, so failed tunes never replace the Home card.
        self._pending_live_recent_item = None
        try:configured_engine=int(cfg.get("service_type",servicetype))
        except Exception:configured_engine=int(servicetype or 4097)
        if configured_engine not in (1,4097,5001,5002,8193):configured_engine=4097
        self._cfg_service_type = configured_engine
        self.engines = [configured_engine]
        self.engine_index = 0
        self.servicetype = configured_engine
        self._engine_locked_to_settings = True
        self.reference = None
        self._owned_reference_string = ""
        self.started = False
        self.start_attempt = 0.0
        self.failed = False
        self.restored = False
        self.resume_prompted = False
        self._resume_target = 0
        self._resume_verify_attempts = 0
        self._resume_grace_until = 0.0
        self._watch_started_at = 0.0
        self._history_save_min_seconds = 10
        self._last_manual_seek_at = 0.0
        self._manual_seek_target_pts = None
        self._manual_seek_target_at = 0.0
        self._early_eof_pending = False
        self._startup_guard_until = 0.0
        self._startup_guard_checks = 0
        self._startup_guard_last_position = -1
        self._startup_guard_reason = ""
        self._last_progress_position = 0
        self._last_progress_duration = 0
        self._hard_stop_attempts = 0
        self._hard_stop_pending_result = None
        self._hard_stop_closing = False
        self._closing_playback = False
        self._owned_external_pids = set()
        self._external_player_baseline = set(_all_external_player_pids())
        self._resume_seek_count = 0
        self._resume_verify_cycles = 0
        self._resume_prepared = False
        self._resume_prompt_open = False
        self._native_resume_started = False
        self._resume_audio_guard = False
        self._resume_was_muted = None
        self._resume_started_at = 0.0
        self._observed_quality = ""
        self._observed_width = 0
        self._observed_height = 0
        self._static_episode_marker = None
        self._video_guard_misses = 0
        self._video_guard_last_engine = None
        self._engine_video_confirmed = False
        self._next_episode_prompted = False
        self._subtitle_user_override = False
        self._subtitle_disable_attempts = 0
        # Embedded subtitles are supplied by the active Enigma2 service, not
        # SubDL. Some ServiceApp/ExtePlayer engines publish their subtitle list
        # a little after video starts, so keep a short non-blocking probe state
        # instead of deciding that the stream has no tracks on the first read.
        self._embedded_subtitle_probe_pending = False
        self._embedded_subtitle_probe_attempts = 0
        self._embedded_subtitle_probe_started = 0.0
        self._recovery_attempts = 0
        self._recovery_inflight = False
        self._recovery_queue = queue.Queue()
        self._recovery_cancel = threading.Event()
        self._recovery_thread = None
        self._recovery_client_lock = threading.RLock()
        self._recovery_client = None
        # r10: bounded automatic fresh-link retry. Attempts are session-local and
        # reset only after the stream proves stable or the user switches title/channel.
        self._auto_retry_inflight = False
        self._auto_retry_future = None
        self._auto_retry_generation = 0
        self._auto_retry_reason = ""
        # Keep each failed retry attempt visible long enough to be readable.
        # This is only a startup/failure cadence guard; it never polls during
        # normal playback and is cancelled as soon as a stream starts.
        self._auto_retry_min_interval = 2.0
        self._auto_retry_next_allowed_at = 0.0
        self._auto_retry_deferred_reason = ""
        # Visual retry hold: once a retry chain starts, keep one stable status
        # line on-screen. Only the attempt number changes until playback is
        # genuinely confirmed or the chain ends.
        self._retry_visual_hold = False
        # VOD/episode retry success is confirmed from real advancing playback
        # position, not evStart/resolution metadata which may belong to a stale
        # decoder handoff.  This keeps the retry line visually continuous.
        self._retry_visual_progress_last = -1
        self._retry_visual_progress_advances = 0
        self._live_epg_generation = 0
        self._live_epg_future = None
        self._live_epg_queue = queue.Queue()
        self._live_epg_last_channel = ""
        self._runtime_reconnects = 0
        self._last_network_activity = 0.0
        self._event_suppress_until = 0.0
        self._play_generation = 0
        self._memory_fuse_level = 0
        self._memory_fuse_last_rss = 0
        self._memory_fuse_emergency = False
        # Stage-3: visual/status pollers run at full cadence only while the
        # InfoBar is visible. Playback/resume/progress persistence are separate.
        self._player_infobar_visible = True
        self._zap_switch_generation = 0
        self._zap_switch_queue = queue.Queue()
        self._zap_switch_inflight = False
        self._zap_switch_started_at = 0.0
        # Stage-3: keep the current async job handle so queued work can be
        # cancelled when the Player closes. Running workers are written to avoid
        # retaining the Screen object and may finish harmlessly in the background.
        self._zap_switch_future = None
        self._aspect_cycle_cursor = None

        runtime_breadcrumb("player_open",media_type=self.media_type,engine=int(self.servicetype or 0))

        self["resume_mask"] = Label("")
        self["connection"] = Label(_("PREPARING STREAM"))
        _current_server_profile=self._server_search_current_profile()
        try:
            self._active_server_name=self._player_server_label(_current_server_profile,0) if _current_server_profile.get("portal") else ""
        except Exception:
            self._active_server_name=""
        self["server_name"] = Label(str(self._active_server_name or "")[:48])
        self["logo"] = Pixmap();self["live_picon"] = Pixmap();self["adaptive_main"] = Pixmap();self["poster_neon_halo"] = Pixmap();self["adaptive_poster"] = Pixmap();self["adaptive_live_picon"] = Pixmap()
        self["adaptive_progress_track"] = Pixmap(); self["adaptive_keybar"] = Pixmap()
        self["chip_video_quality"] = Pixmap(); self["chip_video_codec"] = Pixmap(); self["chip_audio_codec"] = Pixmap()
        self["vod_quality_badge"] = Pixmap()
        # VOD/episode quality is a runtime-only one-shot session. Every new title
        # starts blank. A short decoder probe identifies the current source frame,
        # paints one badge, then destroys its own timer for the rest of playback.
        # Live keeps its existing dynamic quality card untouched.
        self._vod_quality_badge_locked = False
        self._vod_quality_badge_path = ""
        self._vod_quality_badge_resolution = (0, 0)
        self._vod_quality_probe_active = False
        self._vod_quality_probe_generation = 0
        self._vod_quality_probe_preplay = (0, 0)
        self._vod_quality_probe_started_at = 0.0
        self._vod_quality_probe_size_event = False
        self._vod_quality_probe_size_event_at = 0.0
        self._vod_quality_probe_updated_event = False
        self._vod_quality_probe_candidate = (0, 0)
        self._vod_quality_probe_candidate_count = 0
        self._vod_quality_probe_candidate_since = 0.0
        self["chip_stream"] = Pixmap(); self["chip_audio"] = Pixmap(); self["chip_subtitles"] = Pixmap(); self["chip_engine"] = Pixmap()
        self["channel"] = Label(self.name[:120])
        self["vod_title"] = Label(self.name[:180])
        self["episode_marker"] = Label("")
        self["title_logo"] = Pixmap()
        self._player_title_logo_token=0
        self._player_title_logo_queue=queue.Queue()
        self._player_title_logo_pending=False
        self._player_title_logo_path=""
        self._player_title_logo_future=None
        self["now"] = Label(self._now_text())
        # Static catalogue metadata does not change during one Player lifetime.
        # Build it once instead of re-walking the item dictionary/regex fields.
        self._static_quality = _quality(self.item, self.name)
        self["category"] = Label(self._category_label())
        self["engine"] = Label(_engine_label(self.servicetype))
        self["extension"] = Label(_("STREAM"))
        self["quality"] = Label(_("AUTO"))
        self["retry_status"] = Label("")
        self["live_epg_now"] = Label("")
        self["live_epg_next"] = Label("")
        self["video_quality"] = Label(_(self._static_quality))
        self["video_codec"] = Label(_("VIDEO"))
        self["audio_codec"] = Label(_("AUDIO"))
        self["skip_hint"] = Label("")
        self["vod_progress"] = ProgressBar(); self["vod_progress"].setRange((0,100)); self["vod_progress"].setValue(0)
        self["progress_neon"] = Pixmap(); self._adaptive_accent_neon = "#55b9ff"; self._progress_neon_value = -1
        # Stage-4 runtime caches: avoid rescanning sysfs and avoid repainting
        # identical stream-info labels on every status poll.
        self._network_rx_paths = ()
        self._network_rx_paths_refresh_at = 0.0
        self._stream_info_text_cache = {}
        self["audio_tag"] = Label(_("AUDIO"))
        self["subtitle_tag"] = Label(_("SUBTITLES"))
        self["online_subtitle"] = Label("")
        self._online_subtitle_cues = []
        self._online_subtitle_active = False
        self._online_subtitle_index = 0
        self._online_subtitle_path = ""
        self._online_subtitle_searching = False
        self._online_subtitle_queue = queue.Queue()
        self._online_subtitle_generation = 0
        self._online_subtitle_cancel = threading.Event()
        self._online_subtitle_future = None
        self._online_subtitle_offset_ms = 0
        self._online_subtitle_scale = 1.0
        self._online_subtitle_overlay = None
        self._online_subtitle_display = None
        self._online_subtitle_current_text = ""
        self._external_subtitle_bridge = _ExternalSubtitleProviderBridge(self)
        # Pause only while SubsSupport/Pro owns the foreground menu stack.
        self._external_subtitle_ui_hold=False
        self._external_subtitle_ui_seen_foreign=False
        self._external_subtitle_ui_resume_after=False
        self._external_subtitle_ui_hold_started=0.0
        self._last_play_state_value=">"
        self.external_subtitle_ui_timer=eTimer()
        self.external_subtitle_ui_timer_conn=None
        try:
            self.external_subtitle_ui_timer_conn=self.external_subtitle_ui_timer.timeout.connect(self._external_subtitle_ui_watch)
        except Exception:
            self.external_subtitle_ui_timer.callback.append(self._external_subtitle_ui_watch)
        _sub_cfg=cfg
        self._subtitle_style={
            "color":str(_sub_cfg.get("subtitle_color") or "#FFFFFF"),
            "background":bool(_sub_cfg.get("subtitle_background",False)),
            "position":max(-260,min(260,int(_sub_cfg.get("subtitle_position",0) or 0))),
            "size":max(30,min(56,int(_sub_cfg.get("subtitle_size",38) or 38))),
        }
        self._media_info_open = False
        self["key_red"] = Label(_("Exit"))
        self["key_green"] = Label(_("Aspect Ratio"))
        self["key_yellow"] = Label(_("Subtitles"))
        self["key_blue"] = Label(_("Search"))
        self["elapsed_time"] = Label("")
        self["total_time"] = Label("")
        self["remaining_time"] = Label("")

        # Compact in-player subtitle picker.  It reuses the exact Categories
        # adaptive row material and never opens a separate Screen, so playback
        # continues uninterrupted behind the choices.
        self._subtitle_inline_level=""
        self._subtitle_inline_locked=False
        self._subtitle_native_selector_hold=False
        self._subtitle_player_keymaps_suspended=False
        self._subtitle_inline_overlay=SettingsInlineChoiceOverlay(
            self,"subtitle_inline_list","subtitle_inline_actions",_asset,_,optional_failure,action_priority=-20000
        )

        # R24: manual Movies/Series alternate-server search.  It deliberately
        # reuses the exact same five-row inline glass component as Subtitles.
        # Nothing runs in the background until the user presses BLUE.
        self._server_search_mode_active=False
        self._server_search_locked=False
        self._server_search_generation=0
        self._server_search_cancel=threading.Event()
        self._server_search_queue=queue.Queue()
        self._server_search_future=None
        self._server_search_results=[]
        self._server_search_last_index=0
        self._server_search_owned_client=None
        self._server_search_query=""
        self._server_search_inline_overlay=SettingsInlineChoiceOverlay(
            self,"server_search_inline_list","server_search_inline_actions",_asset,_,optional_failure,action_priority=-21000
        )

        self["player_actions"] = ActionMap(
            ["UltraStalkerPlayerActions", "OkCancelActions", "ColorActions", "InfobarActions", "NumberActions"],
            {
                "cancel": self._exit_or_close_subtitle_overlay,
                "stop": self._exit_or_close_subtitle_overlay,
                "red": self.back,
                "ok": self.OKButton,
                "info": self.show_media_info,
                "blue": self.serverSearch,
                "tv": self.toggleStreamType,
                "green": self.cycleDisplayAspect,
                "yellow": self.subtitleSelection,
                "0": self.restartStream,
                # Numeric trick-play: keep the receiver's familiar 10-second
                # jumps, but never route 3 to the old Skip Credits feature.
                "1": self.seekBack10,
                "3": self.seekForward10,
                # R48: receiver-native seek bridge. LEFT/RIGHT and the
                # transport REW/FF keys route into the already-proven 10s
                # seek implementation, but only for seekable VOD-like media.
                "seekBack10Bridge": self.seekBridgeBack10,
                "seekForward10Bridge": self.seekBridgeForward10,
            },
            -2,
        )

        # Test68: direct Live fullscreen channel stepping. UP/DOWN never opens
        # the drawer and never leaves fullscreen; it switches only inside the
        # current Live folder snapshot, lazily loading the target portal page
        # when the next/previous channel is not in RAM yet.  OK/Back semantics
        # remain exactly as before.
        self["live_channel_step_actions"] = ActionMap(
            ["DirectionActions"],
            {"up": lambda: self._quickZap(-1), "down": lambda: self._quickZap(1)},
            -1500,
        )
        try:self["live_channel_step_actions"].setEnabled(self.media_type in ("itv","live"))
        except Exception:pass

        # us132: native-style decisive EXIT lifecycle.  OpenBH InfoBar
        # mixins can bind OkCancelActions too, so keep a dedicated, very-high
        # priority exit map whose only job is to reach our hard-stop path.
        # The normal player map remains responsible for all other keys.
        self["hard_exit_actions"] = ActionMap(
            ["UltraStalkerPlayerActions", "OkCancelActions"],
            {"cancel": self._exit_or_close_subtitle_overlay, "stop": self._exit_or_close_subtitle_overlay},
            -10000,
        )

        # OE-A/OpenBH InfoBar mixins also bind Yellow. Keep our Nova information
        # overlay on a dedicated higher-priority ColorActions map so the legacy
        # "Information (n)" popup can never steal the key.
        self["nova_yellow_actions"] = ActionMap(["ColorActions"], {"yellow": self.subtitleSelection}, -1000)
        self["online_subtitle_actions"] = ActionMap(
            ["InfobarSubtitleSelectionActions"],
            {"subtitleSelection": self.subtitleSelection},
            -1200,
        )
        self["subtitle_volume_watch_actions"] = ActionMap(
            ["VolumeActions"],
            {"volumeUp":self._subtitle_volume_osd_event,
             "volumeDown":self._subtitle_volume_osd_event,
             "volumeMute":self._subtitle_volume_osd_event},
            -1100,
        )

        eventmap = {iPlayableService.evStart: self._service_started}
        _ev_updated=getattr(iPlayableService,"evUpdatedInfo",None)
        if _ev_updated is not None:eventmap[_ev_updated]=self._native_resume_updated
        _ev_video_size=getattr(iPlayableService,"evVideoSizeChanged",None)
        if _ev_video_size is not None:eventmap[_ev_video_size]=self._vod_video_size_changed
        for event_name, callback in (("evTuneFailed", self._service_failed), ("evEOF", self._service_eof)):
            event_value = getattr(iPlayableService, event_name, None)
            if event_value is not None:
                eventmap[event_value] = callback
        self.__event_tracker = ServiceEventTracker(screen=self, eventmap=eventmap)
        try:
            # Always paint receiver-safe neutral glass first. Adaptive chrome
            # then replaces it on the same layout pass when artwork is ready.
            self.onLayoutFinish.append(self._apply_adaptive_player_chrome)
            self.onLayoutFinish.append(self._init_online_subtitle_display)
        except Exception as exc:optional_failure("player.optional_guard",exc)

        self.startup_timer = eTimer()
        try:
            self.startup_timer_conn = self.startup_timer.timeout.connect(self._startup_timeout)
        except Exception:
            self.startup_timer.callback.append(self._startup_timeout)


        # Proven native-style resume timer: wait briefly after evStart, then
        # issue exactly one absolute seekTo() against the live seek interface.
        self.resume_timer = eTimer()
        self.resume_timer_conn = None
        try:
            self.resume_timer_conn = self.resume_timer.timeout.connect(self._reference_resume)
        except Exception:
            self.resume_timer.callback.append(self._reference_resume)

        self.resume_verify_timer = eTimer()
        try:
            self.resume_verify_timer_conn = self.resume_verify_timer.timeout.connect(self._verify_resume_position)
        except Exception:
            self.resume_verify_timer.callback.append(self._verify_resume_position)

        # R46: subtitle session restore waits until playback/resume has settled.
        # This prevents provider timing from being restored against the pre-resume
        # position and gives Ultra's own renderer a live seek position before its
        # first cue evaluation.
        self.subtitle_session_restore_timer = eTimer()
        self.subtitle_session_restore_timer_conn = None
        try:
            self.subtitle_session_restore_timer_conn = self.subtitle_session_restore_timer.timeout.connect(self._subtitle_session_restore_tick)
        except Exception:
            self.subtitle_session_restore_timer.callback.append(self._subtitle_session_restore_tick)

        self.subtitle_default_timer = eTimer()
        try:
            self.subtitle_default_timer_conn = self.subtitle_default_timer.timeout.connect(self._enforce_default_subtitles_off)
        except Exception:
            self.subtitle_default_timer.callback.append(self._enforce_default_subtitles_off)

        # Native embedded-track discovery is intentionally separate from the
        # online subtitle timers. It only runs after the user chooses Embedded
        # subtitles and stops as soon as the receiver exposes real tracks.
        self.embedded_subtitle_probe_timer = eTimer()
        self.embedded_subtitle_probe_timer_conn = None
        try:
            self.embedded_subtitle_probe_timer_conn = self.embedded_subtitle_probe_timer.timeout.connect(self._embedded_subtitle_probe_tick)
        except Exception:
            self.embedded_subtitle_probe_timer.callback.append(self._embedded_subtitle_probe_tick)

        self.online_subtitle_timer = eTimer()
        try:
            self.online_subtitle_timer_conn = self.online_subtitle_timer.timeout.connect(self._online_subtitle_tick)
        except Exception:
            self.online_subtitle_timer.callback.append(self._online_subtitle_tick)
        self.online_subtitle_result_timer = eTimer()
        try:
            self.online_subtitle_result_timer_conn = self.online_subtitle_result_timer.timeout.connect(self._drain_online_subtitle_result)
        except Exception:
            self.online_subtitle_result_timer.callback.append(self._drain_online_subtitle_result)

        self.subtitle_volume_restore_timer=eTimer();self.subtitle_volume_restore_timer_conn=None
        try:self.subtitle_volume_restore_timer_conn=self.subtitle_volume_restore_timer.timeout.connect(self._restore_subtitle_after_volume_osd)
        except Exception:self.subtitle_volume_restore_timer.callback.append(self._restore_subtitle_after_volume_osd)

        self.server_search_timer=eTimer();self.server_search_timer_conn=None
        try:self.server_search_timer_conn=self.server_search_timer.timeout.connect(self._drain_server_search_result)
        except Exception:self.server_search_timer.callback.append(self._drain_server_search_result)

        self.progress_timer = eTimer()
        try:
            self.progress_timer_conn = self.progress_timer.timeout.connect(self._periodic_progress_save)
        except Exception:
            self.progress_timer.callback.append(self._periodic_progress_save)

        self.progress_visual_timer = eTimer()
        try:
            self.progress_visual_timer_conn = self.progress_visual_timer.timeout.connect(self._update_progress_visual)
        except Exception:
            self.progress_visual_timer.callback.append(self._update_progress_visual)

        self.hard_stop_timer = eTimer()
        try:
            self.hard_stop_timer_conn = self.hard_stop_timer.timeout.connect(self._verify_hard_stop)
        except Exception:
            self.hard_stop_timer.callback.append(self._verify_hard_stop)

        # Some ServiceApp/ExtePlayer builds emit a transient EOF one or two
        # seconds after evStart while the same service keeps playing. Do not
        # immediately restart the movie; verify actual playback first.
        self.eof_guard_timer = eTimer()
        try:
            self.eof_guard_timer_conn = self.eof_guard_timer.timeout.connect(self._verify_early_eof)
        except Exception:
            self.eof_guard_timer.callback.append(self._verify_early_eof)

        self.recovery_timer = eTimer()
        try:
            self.recovery_timer_conn = self.recovery_timer.timeout.connect(self._drain_recovery_result)
        except Exception:
            self.recovery_timer.callback.append(self._drain_recovery_result)

        self.auto_retry_delay_timer = eTimer()
        try:
            self.auto_retry_delay_timer_conn = self.auto_retry_delay_timer.timeout.connect(self._run_deferred_auto_retry)
        except Exception:
            self.auto_retry_delay_timer.callback.append(self._run_deferred_auto_retry)

        # Visual-only retry success guard.  Keep the retry line continuously
        # visible across failed attempts.  After a retry candidate emits evStart,
        # clear it only if that candidate stays alive for a short quiet window.
        self.retry_visual_success_timer = eTimer()
        try:
            self.retry_visual_success_timer_conn = self.retry_visual_success_timer.timeout.connect(self._confirm_retry_visual_success)
        except Exception:
            self.retry_visual_success_timer.callback.append(self._confirm_retry_visual_success)

        self.live_epg_timer = eTimer()
        self.live_epg_timer_conn = None
        try:
            self.live_epg_timer_conn = self.live_epg_timer.timeout.connect(self._drain_live_epg_result)
        except Exception:
            self.live_epg_timer.callback.append(self._drain_live_epg_result)

        self.zap_switch_timer = eTimer()
        try:
            self.zap_switch_timer_conn = self.zap_switch_timer.timeout.connect(self._drain_zap_switch_result)
        except Exception:
            self.zap_switch_timer.callback.append(self._drain_zap_switch_result)

        # R68: provider Live picons are resolved independently of TMDb/artwork.
        # A switched channel can therefore fetch its own stream_icon even when
        # the user never visited that category in the Live grid beforehand.
        self._live_picon_fetch_generation=0
        self._live_picon_fetch_inflight=False
        self._live_picon_fetch_future=None
        self._live_picon_fetch_queue=queue.Queue()

        # A stream must stay healthy for a while before retry budgets are reset.
        # This prevents a flapping channel from creating an endless reconnect loop.
        self.stable_timer = eTimer()
        try:
            self.stable_timer_conn = self.stable_timer.timeout.connect(self._mark_stream_stable)
        except Exception:
            self.stable_timer.callback.append(self._mark_stream_stable)

        # Aspect-preserving poster decoder used by the native player UI.
        self.PicLoad = ePicLoad()
        self.PicLoad_conn = None
        self._poster_path = None
        try:
            self.PicLoad.PictureData.get().append(self._decode_poster)
        except Exception:
            try:
                self.PicLoad_conn = self.PicLoad.PictureData.connect(self._decode_poster)
            except Exception:
                self.PicLoad_conn = None

        # Poll actual service data after playback starts. This affects labels only;
        # playback and MAG service behavior are unchanged.
        self.stream_info_timer = eTimer()
        self.stream_info_timer_conn = None
        try:
            self.stream_info_timer_conn = self.stream_info_timer.timeout.connect(self._update_stream_info)
        except Exception:
            self.stream_info_timer.callback.append(self._update_stream_info)

        # Short-lived VOD quality detector. It exists only during startup and is
        # stopped permanently as soon as the current title's badge is known.
        self.vod_quality_timer = eTimer()
        self.vod_quality_timer_conn = None
        try:
            self.vod_quality_timer_conn = self.vod_quality_timer.timeout.connect(self._vod_quality_probe_tick)
        except Exception:
            self.vod_quality_timer.callback.append(self._vod_quality_probe_tick)

        self.player_title_logo_timer=eTimer();self.player_title_logo_timer_conn=None
        try:self.player_title_logo_timer_conn=self.player_title_logo_timer.timeout.connect(self._drain_player_title_logo)
        except Exception:self.player_title_logo_timer.callback.append(self._drain_player_title_logo)

        # Last-resort memory fuse. This does not restart playback. Its job is to
        # shed optional UI memory long before Broadcom's OOM killer reaches the
        # ~600 MB RSS failure zone observed on the target receiver.
        self.memory_fuse_timer=eTimer();self.memory_fuse_timer_conn=None
        try:
            self.memory_fuse_timer_conn=self.memory_fuse_timer.timeout.connect(self._memory_fuse_tick)
        except Exception:
            self.memory_fuse_timer.callback.append(self._memory_fuse_tick)

        try:
            self.onPlayStateChanged.append(self._play_state_changed)
        except Exception as exc:
            optional_failure("player", exc)
        self.onLayoutFinish.append(self._layout_ready)
        try:self.onShow.append(self._restore_player_chrome_after_show)
        except Exception as exc:optional_failure("player.chrome_show_hook",exc)
        try:
            self.onShow.append(self._resume_hidden_visual_timers)
            self.onHide.append(self._pause_hidden_visual_timers)
        except Exception as exc:optional_failure("player.visual_timer_hooks",exc)
        self.onFirstExecBegin.append(self._begin_playback)
        self.onClose.append(self._cleanup)

    @staticmethod
    def _live_channel_font_size(text):
        """Large centered Live title that shrinks only when the name needs it."""
        value=str(text or "").strip()
        units=max(1.0,sum(1.32 if ord(ch)>127 else 1.0 for ch in value))
        if units<=22:return 34
        if units<=30:return 32
        if units<=38:return 30
        if units<=48:return 27
        if units<=60:return 24
        return max(18,min(22,int(1060.0/(units*0.58))))

    def _apply_live_channel_title(self):
        if self.media_type not in ("itv","live"):
            return
        title=_clean_live_name(str(self.name or "Live channel"),self._cfg_clean_titles)
        size=self._live_channel_font_size(title)
        try:
            self["channel"].setText(title[:180])
            if self["channel"].instance is not None:
                self["channel"].instance.setFont(gFont("Regular",size))
            self["channel"].show()
        except Exception as exc:
            optional_failure("player.live_title_fit",exc)

    @staticmethod
    def _balanced_button_lines(text):
        """Return a balanced two-line label without changing translated wording."""
        value=" ".join(str(text or "").replace("\n"," ").split()).strip()
        parts=value.split(" ")
        if len(parts)<2:
            return value
        best=None
        for idx in range(1,len(parts)):
            left=" ".join(parts[:idx]);right=" ".join(parts[idx:])
            score=max(len(left),len(right))*4+abs(len(left)-len(right))
            if best is None or score<best[0]:
                best=(score,left,right)
        return (best[1]+"\n"+best[2]) if best else value

    def _fit_player_key_label(self, name, max_size=22, min_size=11, padding=16):
        """Keep localized key-bar text inside the existing button geometry.

        Short English/Arabic labels retain their normal size. Longer localized
        strings shrink only as much as needed. If a very long phrase still does
        not fit at the minimum one-line size, it is balanced over two lines in
        the same button instead of escaping the glass asset.
        """
        try:
            widget=self[name];inst=widget.instance
            if inst is None:return
            try:value=widget.getText()
            except Exception:value=getattr(widget,"text","")
            value=" ".join(str(value or "").replace("\n"," ").split()).strip()
            if not value:return
            width=max(40,int(inst.size().width())-int(padding))
            chosen=int(max_size);fits=False
            try:
                if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            except Exception:pass
            for size in range(int(max_size),int(min_size)-1,-1):
                inst.setFont(gFont("Regular",size))
                try:measured=int(inst.calculateSize().width())
                except Exception:measured=0
                if measured<=0:
                    units=sum(0.48 if ch.isspace() else (0.78 if ord(ch)>127 else 0.62) for ch in value)
                    measured=int(max(1.0,units)*float(size))
                if int(measured*1.08)<=width:
                    chosen=size;fits=True;break
                chosen=size
            if fits:
                widget.setText(value);inst.setFont(gFont("Regular",chosen));return

            # Exceptional localization fallback: preserve every character and
            # use two centered lines inside the same 42px key button.
            wrapped=self._balanced_button_lines(value)
            if "\n" in wrapped:
                try:
                    if hasattr(inst,"setNoWrap"):inst.setNoWrap(0)
                except Exception:pass
                two_line_max=min(15,int(max_size))
                two_line_min=max(9,min(int(min_size),11))
                selected=two_line_min
                for size in range(two_line_max,two_line_min-1,-1):
                    widget.setText(wrapped);inst.setFont(gFont("Regular",size))
                    try:
                        measured=int(inst.calculateSize().width());height=int(inst.calculateSize().height())
                    except Exception:
                        measured=0;height=0
                    if (measured<=0 or int(measured*1.06)<=width) and (height<=0 or height<=40):
                        selected=size;break
                widget.setText(wrapped);inst.setFont(gFont("Regular",selected))
            else:
                widget.setText(value);inst.setFont(gFont("Regular",int(min_size)))
        except Exception as exc:
            optional_failure("player.keybar_localized_font_fit_%s"%name,exc)

    def _fit_player_keybar_labels(self):
        # R47: text-only localization fit. Button assets/positions remain exact.
        for name,max_size in (("key_red",22),("key_green",22),("key_yellow",22),("key_blue",20)):
            self._fit_player_key_label(name,max_size=max_size,min_size=11,padding=16)

    def _layout_ready(self):
        try:
            if not self._resume_prepared:
                self["resume_mask"].hide()
        except Exception as exc:
            optional_failure("player.resume_mask_layout", exc)
        artwork=_cached_poster(self.item, self.name, self.media_type)
        if self.media_type in ("itv","live"):
            try:self["vod_title"].hide();self["title_logo"].hide();self["episode_marker"].hide()
            except Exception:pass
            self._apply_live_channel_title()
            self._load_live_picon(artwork)
            self._apply_live_infobar_layout()
            self._refresh_live_epg()
        else:
            self._configure_vod_title_area()
            self._load_poster(artwork)
        self._fit_player_keybar_labels()
        self._update_stream_info()

    @staticmethod
    def _player_fallback_title(item, name, media_type, clean_titles=True):
        """Clean text used only when the persistent Player title logo is absent.

        Prefer catalogue metadata (and a parent-series title for episodes), then
        remove filename-style separators, episode tokens and common stream tags.
        Playback identity and title-logo lookup remain untouched.
        """
        data=item if isinstance(item,dict) else {}
        mt=str(media_type or "").lower()
        candidates=[]
        if mt in ("series","episode","tv"):
            candidates.extend((data.get("_series_title"),data.get("series_title"),data.get("series_name")))
        candidates.extend((data.get("title"),data.get("name"),name))
        raw=""
        for value in candidates:
            text=str(value or "").strip()
            if text and text.lower() not in ("none","null"):
                raw=text;break
        if not raw:return str(name or "").strip()
        if not clean_titles:
            return raw
        text=re.sub(r"\.(?:mkv|mp4|avi|mov|ts|m2ts|wmv|flv|webm)$","",raw,flags=re.I)
        text=re.sub(r"(?i)(?:^|[._\-\s])S\d{1,2}E\d{1,3}(?=$|[._\-\s])"," ",text)
        text=re.sub(r"(?i)(?:^|[._\-\s])\d{1,2}x\d{1,3}(?=$|[._\-\s])"," ",text)
        text=re.sub(r"(?i)(?:^|[._\-\s])Season[._\-\s]*\d{1,2}[._\-\s]*Episode[._\-\s]*\d{1,3}(?=$|[._\-\s])"," ",text)
        text=re.sub(r"[._]+"," ",text)
        # Player-only filename noise. Keep this deliberately narrow so real title
        # words survive while common release/stream tags do not leak on-screen.
        text=re.sub(r"(?i)(?:^|\s)(?:2160p|1080p|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dv|dolby[ ._-]*vision|web[ ._-]*dl|webrip|bluray|blu[ ._-]*ray|brrip|bdremux|remux|hdtv|x264|x265|h\.?264|h\.?265|hevc|av1|aac(?:2\.?0|5\.?1)?|ddp?5\.?1|eac3|ac3|dts(?:[ ._-]*hd)?)(?=$|\s)"," ",text)
        text=re.sub(r"\s+"," ",text).strip(" -_|:.")
        cleaned=_catalogue_title(text) or text
        cleaned=re.sub(r"\s+"," ",str(cleaned or "")).strip(" -_|:.")
        return cleaned or str(name or "").strip()

    @staticmethod
    def _player_title_font_size(text):
        value=str(text or "").strip()
        units=max(1.0,sum(1.32 if ord(ch)>127 else 1.0 for ch in value))
        if units<=16:return 58
        if units<=28:return 52
        if units<=40:return 44
        if units<=54:return 36
        if units<=70:return 30
        # 1220px title area: continue shrinking instead of freezing at 26px.
        return max(18,min(26,int(1180.0/(units*0.57))))

    @staticmethod
    def _player_fit_title(text):
        value=str(text or "").strip()
        if not value:return "",58
        size=UltraStalkerPlayer._player_title_font_size(value)
        def units_of(v):return sum(1.32 if ord(ch)>127 else 1.0 for ch in v)
        max_units=1180.0/(max(18,size)*0.57)
        if units_of(value)<=max_units:return value,size
        out=[];used=0.0
        budget=max(1.0,max_units-3.0)
        for ch in value:
            u=1.32 if ord(ch)>127 else 1.0
            if used+u>budget:break
            out.append(ch);used+=u
        fitted=("".join(out).rstrip()+"…") if out else value[:1]
        return fitted,size

    @staticmethod
    def _episode_display_number(item):
        item=item if isinstance(item,dict) else {}
        for key in ("_episode_number","episode_number","episode","number","episode_num","episode_id"):
            value=item.get(key)
            text=str(value or "").strip()
            if not text or text.lower() in ("none","null"):
                continue
            match=re.search(r"\d+", text)
            if not match:
                continue
            try:return str(int(match.group(0)))
            except Exception:return match.group(0)
        return ""

    def _episode_marker_text(self):
        cached=getattr(self,"_static_episode_marker",None)
        if cached is not None:return cached
        if self.media_type not in ("series","episode"):
            self._static_episode_marker="";return ""
        number=self._episode_display_number(self.item)
        self._static_episode_marker=(_("Episode %s") % number) if number else ""
        return self._static_episode_marker

    def _configure_vod_title_area(self):
        """VOD/episode Player: Cinematic authority -> local Player presentation.

        The Player never resolves a separate Player-logo identity.  It consumes
        the current screen/Cinematic 420x144 Ultra authority.  When that
        authority is missing, one background job resolves the *Cinematic*
        variant for the current locked item, then derives the 1220x72 Player
        canvas locally from that exact file.
        """
        try:
            for name in ("channel","category","quality","connection","now"):
                try:self[name].hide()
                except Exception:pass
            title=self._player_fallback_title(self.item,self.name,self.media_type,self._cfg_clean_titles)
            title,title_font=self._player_fit_title(title)

            # New-title boundary: invalidate every previous async/logo surface, not
            # merely hide it. A hidden Pixmap can be shown again by an InfoBar
            # restore path, which is how an old title logo leaked onto logo-less
            # films. The fallback title is authoritative until this title resolves.
            self._player_title_logo_token+=1
            token=self._player_title_logo_token
            future=getattr(self,"_player_title_logo_future",None)
            if future is not None:
                try:future.cancel()
                except Exception:pass
            self._player_title_logo_future=None
            self._player_title_logo_pending=False
            self._player_title_logo_path=""
            try:
                while True:self._player_title_logo_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                if self["title_logo"].instance is not None:self["title_logo"].instance.setPixmap(None)
                self["title_logo"].hide()
            except Exception:pass
            try:
                if self["vod_title"].instance is not None:self["vod_title"].instance.setFont(gFont("Regular",title_font))
            except Exception:pass
            marker=self._episode_marker_text()
            if marker:
                try:self["episode_marker"].setText(marker);self["episode_marker"].show()
                except Exception:pass
            else:
                try:self["episode_marker"].hide()
                except Exception:pass
            self["vod_title"].setText(title[:180]);self["vod_title"].show();self["title_logo"].hide();self._player_title_logo_path=""

            logo_item=(self.item.get("_quality_parent_item") if self.media_type=="episode" and isinstance(self.item,dict) and isinstance(self.item.get("_quality_parent_item"),dict) else self.item) if isinstance(self.item,dict) else {}
            if not isinstance(logo_item,dict):logo_item={}
            # Preserve the Player handoff/locked identity on the actual logo owner.
            for key in ("_player_title_logo_tmdb_id","_locked_tmdb_id"):
                value=(self.item.get(key) if isinstance(self.item,dict) else None)
                if value not in (None,""):logo_item[key]=value
            authority=_player_title_logo_source(self.item if isinstance(self.item,dict) else logo_item,title,self.media_type)
            if not authority:
                authority=_player_title_logo_source(logo_item,title,self.media_type)
            # If this exact authority already has a prepared Player canvas, paint
            # it immediately without any Pillow work on the GUI thread.
            prepared=""
            try:
                target=_prepare_player_title_logo_cache_path(authority,(1220,72)) if authority else ""
                if target and os.path.isfile(target) and os.path.getsize(target)>256:prepared=target
            except Exception:prepared=""
            if prepared:
                if self["title_logo"].instance is not None:
                    self["title_logo"].instance.setPixmap(None);self["title_logo"].instance.setPixmapFromFile(prepared)
                self["title_logo"].show();self["vod_title"].hide();self._player_title_logo_path=prepared;return

            if self._player_title_logo_pending:return
            self._player_title_logo_pending=True
            result_queue=self._player_title_logo_queue
            media_copy=str(self.media_type or "")
            profile_copy=dict(getattr(self,"_history_profile",{}) or {})
            logo_copy=dict(logo_item)
            authority_copy=str(authority or "")
            def worker():
                path="";source=authority_copy;resolved={}
                try:
                    # Missing Cinematic authority: resolve exactly the same
                    # 420x144 Ultra pipeline Cinematic uses, for this locked item.
                    if not (source and os.path.isfile(source) and os.path.getsize(source)>256):
                        from ..title_logo_ultra import resolve_ultra_title_logo
                        source,resolved=resolve_ultra_title_logo(profile_copy,media_copy,logo_copy,logo_copy,canvas_size=(420,144),visible_title=title,settings=load_settings() or {})
                    if source and os.path.isfile(source) and os.path.getsize(source)>256:
                        # Recent can be the first consumer of a freshly created
                        # Cinematic authority. Give the local Player derivative a
                        # few background-only retries so first open paints it; the
                        # user must never need to close/reopen the title.
                        for _attempt in range(3):
                            path=_prepare_player_title_logo(source,(1220,72)) or ""
                            if path:break
                            time.sleep(0.10)
                except Exception:pass
                try:result_queue.put((token,path,source,dict(resolved or {})))
                except Exception:pass
            try:
                self._player_title_logo_future=_PLAYER_BG_EXECUTOR.submit(worker)
                self.player_title_logo_timer.start(90,True)
            except Exception:
                self._player_title_logo_pending=False
                future=getattr(self,"_player_title_logo_future",None)
                if future is not None:
                    try:future.cancel()
                    except Exception:pass
                self._player_title_logo_future=None
        except Exception as exc:optional_failure("player.vod_title_area",exc)

    def _drain_player_title_logo(self):
        try:self.player_title_logo_timer.stop()
        except Exception:pass
        applied=False
        while True:
            try:job=self._player_title_logo_queue.get_nowait()
            except queue.Empty:break
            try:
                token,path=job[0],job[1];source=job[2] if len(job)>2 else "";resolved=job[3] if len(job)>3 and isinstance(job[3],dict) else {}
            except Exception:continue
            if token!=self._player_title_logo_token or self.restored or self._closing_playback:
                continue
            self._player_title_logo_pending=False
            self._player_title_logo_future=None
            try:
                if source and os.path.isfile(source) and os.path.getsize(source)>256:
                    tmdb_id=resolved.get("tmdb_id") or (self.item or {}).get("_player_title_logo_tmdb_id") or (self.item or {}).get("_locked_tmdb_id")
                    for obj in (self.item,self._history_item):
                        if not isinstance(obj,dict):continue
                        obj["_player_title_logo_cinematic_source"]=source
                        if tmdb_id not in (None,""):
                            obj["_player_title_logo_tmdb_id"]=tmdb_id;obj["_locked_tmdb_id"]=tmdb_id
                        for key in ("original_language","origin_country","countries","production_countries","original_title","original_name","logo_url","logo_language"):
                            value=resolved.get(key)
                            if value not in (None,"",[],{}):obj[key]=value
                if path and os.path.isfile(path) and os.path.getsize(path)>256 and self["title_logo"].instance is not None:
                    self["title_logo"].instance.setPixmap(None);self["title_logo"].instance.setPixmapFromFile(path);self["title_logo"].show();self["vod_title"].hide();self._player_title_logo_path=path;applied=True
                else:
                    # A confirmed logo-less current title must explicitly clear the
                    # old GPU/Pixmap buffer and reveal its own text title.
                    if self["title_logo"].instance is not None:self["title_logo"].instance.setPixmap(None)
                    self["title_logo"].hide();self._player_title_logo_path="";self["vod_title"].show()
            except Exception as exc:optional_failure("player.title_logo_apply",exc)
        if self._player_title_logo_pending and not applied:
            try:self.player_title_logo_timer.start(90,True)
            except Exception:pass


    def _load_live_picon(self, path):
        prepared=_prepare_live_picon(path,(220,132))
        try:
            self["logo"].hide();self["poster_neon_halo"].hide();self["adaptive_poster"].hide()
            if prepared and os.path.isfile(prepared) and self["live_picon"].instance is not None:
                self["live_picon"].instance.setPixmapFromFile(prepared);self["live_picon"].show()
        except Exception as exc:optional_failure("player.live_picon_apply",exc)

    def _resume_bookmark(self):
        try:
            position = max(0, int(self.item.get("_resume_position") or 0))
            duration = max(0, int(self.item.get("_resume_duration") or 0))
        except Exception:
            position = 0; duration = 0
        # Ignore only trivial starts and bookmarks that meet the same configured
        # completion policy used by history/Continue Watching.  Older builds had
        # a hard-coded four-minute tail here, so a perfectly valid bookmark could
        # be stored yet silently refused on the next open.
        if position < (10 * 90000):
            return 0
        if duration and (position >= duration or max(0,duration-position) <= 10*90000):
            return 0
        return position

    def _begin_playback(self):
        """Initialize playback and defer bookmark seeking until the service is ready.

        Ultra Stalker owns the bookmark identity and resume state. Playback starts
        first; the resume path waits for the active service to expose a usable seek
        interface before applying the stored absolute position.
        """
        self._resume_target = 0
        self._resume_seek_count = 0
        self._resume_verify_attempts = 0
        self._resume_verify_cycles = 0
        self._resume_prepared = False
        self._resume_prompt_open = False
        self._native_resume_started = False
        behavior = self._cfg_resume_behavior
        self.resume_prompted = bool(behavior == "start")
        if self.reuse_current:
            try:
                current=self.session.nav.getCurrentlyPlayingServiceReference()
                if current is not None:
                    self.reference=current
                    try:self._owned_reference_string=current.toString()
                    except Exception:self._owned_reference_string=""
                    self.started=True;self.failed=False;self._watch_started_at=time.time()
                    self.start_attempt=time.time();self._last_network_activity=time.monotonic()
                    self._runtime_reconnects=0;self._recovery_attempts=0
                    self["connection"].setText("")
                    runtime_breadcrumb("player_adopt_current",media_type=self.media_type,engine=int(self.servicetype or 0))
                    try:self.stable_timer.stop();self.stable_timer.start(30000,True)
                    except Exception as exc:optional_failure("player.optional_guard",exc)
                    if self.media_type in ("vod","series","episode"):
                        self._reset_vod_quality_probe();self._arm_vod_quality_probe()
                    try:self.stream_info_timer.start(700,True)
                    except Exception as exc:optional_failure("player.optional_guard",exc)
                    return
            except Exception as exc:
                optional_failure("player.adopt_current",exc)
        self.playStream(self.servicetype, self.streamurl)

    def _resume_before_start_answer(self, answer, bookmark):
        self._resume_prompt_open = False
        self.resume_prompted = True
        if answer:
            self._prepare_instant_resume(bookmark)
        self.playStream(self.servicetype, self.streamurl)

    def _prepare_instant_resume(self, position):
        self._resume_target = max(0, int(position or 0))
        if not self._resume_target:
            return
        self._resume_prepared = True
        self._resume_started_at = time.time()
        self._resume_verify_attempts = 0
        self._resume_seek_count = 0
        self._resume_verify_cycles = 0
        self._resume_grace_until = time.time() + 16.0
        # Keep playback lifecycle native. We wait only for seekability and then
        # issue one absolute bookmark seek once the active service is seekable.
        self["connection"].setText(_("RESUMING  •  preparing bookmark"))

    def _set_resume_audio_guard(self, enabled):
        if eDVBVolumecontrol is None:
            return
        try:
            volume = eDVBVolumecontrol.getInstance()
            if volume is None:
                return
            if enabled:
                if not self._resume_audio_guard:
                    try:
                        self._resume_was_muted = bool(volume.isMuted())
                    except Exception:
                        self._resume_was_muted = False
                    if not self._resume_was_muted:
                        volume.setMuted(True)
                    self._resume_audio_guard = True
            elif self._resume_audio_guard:
                if self._resume_was_muted is False:
                    volume.setMuted(False)
                self._resume_audio_guard = False
        except Exception as exc:
            optional_failure("player.resume_audio_guard", exc)

    def _release_resume_shield(self):
        self._resume_prepared = False
        try:
            self["resume_mask"].hide()
        except Exception as exc:
            optional_failure("player.resume_mask_hide", exc)
        self._set_resume_audio_guard(False)

    def _connect_picload(self):
        self.PicLoad_conn = None
        try:
            self.PicLoad.PictureData.get().append(self._decode_poster)
        except Exception:
            try:
                self.PicLoad_conn = self.PicLoad.PictureData.connect(self._decode_poster)
            except Exception:
                self.PicLoad_conn = None

    def _load_poster(self, path):
        """Fill the poster card exactly, using a high-quality center crop."""
        prepared=_prepare_player_poster_fill(path,(250,375))
        self._poster_path = prepared or path
        try:self["live_picon"].hide();self["adaptive_live_picon"].hide()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            if prepared and os.path.isfile(prepared) and self["logo"].instance is not None:
                self["logo"].instance.setPixmapFromFile(prepared)
                self["logo"].show()
                return
        except Exception as exc:
            optional_failure("player.poster_direct",exc)
        try:
            self.PicLoad.setPara([242, 330, 1, 1, 0, 1, "FF000000"])
            result = self.PicLoad.startDecode(self._poster_path)
            if result:
                try:
                    callbacks=self.PicLoad.PictureData.get()
                    if self._decode_poster in callbacks:callbacks.remove(self._decode_poster)
                except Exception as exc:optional_failure("player.optional_guard",exc)
                try:
                    if self.PicLoad_conn is not None:self.PicLoad_conn.disconnect()
                except Exception as exc:optional_failure("player.optional_guard",exc)
                self.PicLoad = ePicLoad()
                self._connect_picload()
                self.PicLoad.setPara([242, 330, 1, 1, 0, 1, "FF000000"])
                self.PicLoad.startDecode(self._poster_path)
        except Exception:
            try:
                self["logo"].instance.setPixmapFromFile(self._poster_path)
                self["logo"].show()
            except Exception as exc:
                optional_failure("player", exc)
    def _decode_poster(self, pic_info=None):
        try:
            ptr = self.PicLoad.getData()
            if ptr is not None and self["logo"].instance:
                self["logo"].instance.setPixmap(ptr)
                self["logo"].show()
        except Exception:
            try:
                if self._poster_path:
                    self["logo"].instance.setPixmapFromFile(self._poster_path)
                    self["logo"].show()
            except Exception as exc:
                optional_failure("player", exc)
    @staticmethod
    def _read_proc_number(paths, base=10):
        for pathname in paths:
            try:
                if os.path.exists(pathname):
                    value = open(pathname, "r").read().strip()
                    if value:
                        return int(value, base)
            except Exception as exc:
                optional_failure("player", exc)
        return None

    @staticmethod
    def _info_number(info, name, default=None):
        try:
            key = getattr(iServiceInformation, name)
            value = info.getInfo(key)
            if value == -2:
                raw = info.getInfoString(key)
                return int(str(raw).strip())
            if value is not None and value >= 0:
                return int(value)
        except Exception as exc:
            optional_failure("player", exc)
        return default

    @staticmethod
    def _video_codec_text(info):
        for pathname in ("/proc/stb/vmpeg/0/codec", "/proc/stb/vmpeg/0/vcodec"):
            try:
                if os.path.exists(pathname):
                    raw = open(pathname, "r").read().strip()
                    if raw:
                        return raw.upper().replace("HEVC", "H.265").replace("AVC", "H.264")[:12]
            except Exception as exc:
                optional_failure("player", exc)
        value = UltraStalkerPlayer._info_number(info, "sVideoType", None)
        return {
            0: "MPEG2", 1: "H.264", 2: "H.263", 3: "VC-1",
            4: "MPEG4", 5: "VC-1", 6: "MPEG1", 7: "H.265",
            8: "VP8", 9: "VP9", 10: "XVID", 12: "AVS",
            13: "AVS2", 16: "AV1",
        }.get(value, "VIDEO")

    @staticmethod
    def _audio_codec_text(service, info):
        try:
            tracks = service.audioTracks()
            if tracks and tracks.getNumberOfTracks() > 0:
                current = tracks.getCurrentTrack()
                if current < 0:
                    current = 0
                desc = str(tracks.getTrackInfo(current).getDescription() or "").strip()
                if desc:
                    token = desc.split()[0].upper()
                    aliases = {"AC-3": "AC3", "AC3+": "E-AC3", "HE-AAC": "AAC"}
                    return aliases.get(token, token)[:12]
        except Exception as exc:
            optional_failure("player", exc)
        try:
            key = getattr(iServiceInformation, "sAudioType")
            raw = info.getInfoString(key)
            if raw:
                return str(raw).upper()[:12]
        except Exception as exc:
            optional_failure("player", exc)
        return "AUDIO"

    def _set_stream_info_text(self, widget_name, value):
        text=str(value or "")
        cache=self._stream_info_text_cache
        if cache.get(widget_name)==text:return False
        self[widget_name].setText(text);cache[widget_name]=text
        return True

    def _provider_live_picon_url(self, item):
        """Resolve only provider-owned Live artwork. Never consult TMDb."""
        row=item if isinstance(item,dict) else {}
        raw=""
        for key in ("_player_picon_url","stream_icon","picon","logo","logo_url","icon","image","img"):
            value=str(row.get(key) or "").strip()
            if value and value.lower() not in ("null","none"):
                raw=value;break
        if not raw:return ""
        raw=_normalize_provider_image_url(raw)
        if raw.startswith("//"):raw="https:"+raw
        if raw.lower().startswith(("http://","https://")):return raw
        base=str(row.get("_art_base") or self.item.get("_art_base") or self.item.get("_portal") or "").strip()
        if not base:return ""
        try:return _normalize_provider_image_url(urllib.parse.urljoin(base.rstrip("/")+"/",raw.lstrip("/")))
        except Exception:return ""

    def _provider_live_picon_cached(self, item):
        row=item if isinstance(item,dict) else {}
        for key in ("_player_picon","_receiver_picon_local","picon_local","logo_local","image_local"):
            path=str(row.get(key) or "").strip()
            if path and os.path.isfile(path) and os.path.getsize(path)>100:return path
        url=self._provider_live_picon_url(row)
        if not url:return ""
        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
        root=os.path.join(PERSISTENT_CACHE_ROOT,"live_picons")
        for ext in (".png",".jpg",".jpeg",".webp"):
            path=os.path.join(root,digest+ext)
            try:
                if os.path.isfile(path) and os.path.getsize(path)>256:return path
            except OSError:pass
        return ""

    def _provider_live_picon_download(self, item):
        """Fetch one provider stream_icon into the existing Live picon cache."""
        cached=self._provider_live_picon_cached(item)
        if cached:return cached
        url=self._provider_live_picon_url(item)
        if not url:return ""
        parts=urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in ("http","https") or not parts.hostname:return ""
        root=os.path.join(PERSISTENT_CACHE_ROOT,"live_picons")
        try:os.makedirs(root,0o700,exist_ok=True)
        except TypeError:
            try:os.makedirs(root,0o700)
            except OSError:pass
        digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
        tmp=os.path.join(root,digest+".player.%d.%d"%(os.getpid(),threading.get_ident()))
        headers={"User-Agent":"UltraStalker/10","Accept":"image/png,image/jpeg,image/webp,image/*,*/*"}
        client=self.item.get("_live_client_ref") if isinstance(self.item,dict) else None
        try:
            if client is not None and hasattr(client,"_request_headers"):
                safe_headers=client._request_headers(browser=True,range_prefix=False) or {}
                for key,value in safe_headers.items():
                    if str(key).lower() not in ("authorization","cookie","x-auth-token","x-access-token"):
                        headers[str(key)]=str(value)
        except Exception:pass
        req=urllib.request.Request(url,headers=headers)
        trusted=[]
        for base in (str((item or {}).get("_art_base") or ""),str(self.item.get("_portal") or "")):
            try:
                parts=urllib.parse.urlsplit(base)
                if parts.scheme.lower() in ("http","https") and parts.hostname:
                    port=parts.port or (443 if parts.scheme.lower()=="https" else 80)
                    host=("[%s]"%parts.hostname) if ":" in parts.hostname and not parts.hostname.startswith("[") else parts.hostname
                    trusted.append("%s://%s:%d"%(parts.scheme.lower(),host,port))
            except Exception:pass
        validate_remote_media_url(url,trusted_private_origins=tuple(trusted))
        opener=build_safe_media_opener(trusted_private_origins=tuple(trusted))
        total=0;head=b""
        try:
            with opener.open(req,timeout=4.5) as response,open(tmp,"wb") as handle:
                while True:
                    chunk=response.read(64*1024)
                    if not chunk:break
                    if not head:head=chunk[:16]
                    total+=len(chunk)
                    if total>2*1024*1024:raise ValueError("live picon exceeds 2 MB")
                    handle.write(chunk)
            if total<=128:raise ValueError("empty live picon")
            if head.startswith(b"\x89PNG\r\n\x1a\n"):ext=".png"
            elif head.startswith(b"\xff\xd8\xff"):ext=".jpg"
            elif head[:4]==b"RIFF" and head[8:12]==b"WEBP":ext=".webp"
            else:ext=""
            if not ext:
                if _PILImage is None:raise ValueError("unsupported live picon format")
                target=os.path.join(root,digest+".png")
                with _PILImage.open(tmp) as im:
                    im.seek(0);im=im.convert("RGBA" if "A" in im.getbands() else "RGB");im.save(target,"PNG",optimize=False)
                try:os.unlink(tmp)
                except OSError:pass
            else:
                target=os.path.join(root,digest+ext);os.replace(tmp,target)
            if os.path.isfile(target) and os.path.getsize(target)>256:
                try:LOG.info("R68 live_provider_picon_download channel=%r key=%s bytes=%d",str((item or {}).get("name") or ""),digest[:10],int(total))
                except Exception:pass
                return target
            return ""
        except Exception as exc:
            try:
                if os.path.exists(tmp):os.unlink(tmp)
            except OSError:pass
            optional_failure("player.live_provider_picon_download",exc)
            return ""

    def _request_provider_live_picon(self, item):
        if self.media_type not in ("itv","live") or not isinstance(item,dict):return ""
        local=self._provider_live_picon_cached(item)
        if local:return local
        url=self._provider_live_picon_url(item)
        if not url:return ""
        self._live_picon_fetch_generation+=1
        generation=self._live_picon_fetch_generation
        self._live_picon_fetch_inflight=True
        row=dict(item);result_queue=self._live_picon_fetch_queue
        def worker():
            path=self._provider_live_picon_download(row)
            try:result_queue.put((generation,url,path))
            except Exception:pass
        try:
            try:LOG.info("R68 live_provider_picon_request channel=%r key=%s",str(row.get("name") or ""),hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()[:10])
            except Exception:pass
            self._live_picon_fetch_future=_PLAYER_BG_EXECUTOR.submit(worker)
        except Exception as exc:
            self._live_picon_fetch_inflight=False;optional_failure("player.live_provider_picon_worker",exc)
        return ""

    def _drain_provider_live_picon(self):
        if self.media_type not in ("itv","live"):return
        latest=None
        while True:
            try:latest=self._live_picon_fetch_queue.get_nowait()
            except queue.Empty:break
        if latest is None:return
        generation,url,path=latest
        if generation!=self._live_picon_fetch_generation:return
        self._live_picon_fetch_inflight=False;self._live_picon_fetch_future=None
        if not path or not os.path.isfile(path):return
        current_url=self._provider_live_picon_url(self.item)
        if current_url and current_url!=url:return
        self.item["_player_picon"]=path;self.item["_player_picon_url"]=url
        try:
            if 0<=int(getattr(self,"_zap_index",0))<len(getattr(self,"_zap_channels",[]) or []):
                row=(getattr(self,"_zap_channels",[]) or [])[int(self._zap_index)]
                if isinstance(row,dict):row["_player_picon"]=path;row["_player_picon_url"]=url
        except Exception:pass
        try:
            self._load_live_picon(path);self._apply_adaptive_player_chrome()
            try:LOG.info("R68 live_provider_picon_apply channel=%r key=%s",str(self.name or ""),hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()[:10])
            except Exception:pass
        except Exception as exc:optional_failure("player.live_provider_picon_apply",exc)

    def _vod_quality_resolution_sources(self, info=None):
        """Return decoder /proc and current-service dimensions separately.

        KiddaC-style service converters treat /proc/stb/vmpeg as the decoder
        source and service.info() as fallback. Keep the two values separate here
        so a previous title cannot win merely because it has the larger frame.
        """
        try:
            if info is None:
                service=self.session.nav.getCurrentService()
                info=service and service.info()
        except Exception:
            info=None

        def sane(width,height):
            try:
                width=int(width or 0);height=int(height or 0)
            except Exception:
                return (0,0)
            if 160 <= width <= 8192 and 120 <= height <= 4320:
                return (width,height)
            return (0,0)

        proc_pair=sane(self._read_proc_number(("/proc/stb/vmpeg/0/xres",),16) or 0,
                       self._read_proc_number(("/proc/stb/vmpeg/0/yres",),16) or 0)
        info_pair=(0,0)
        if info is not None:
            info_pair=sane(self._info_number(info,"sVideoWidth",0) or 0,
                           self._info_number(info,"sVideoHeight",0) or 0)
        return proc_pair,info_pair

    def _vod_quality_preplay_snapshot(self):
        proc_pair,info_pair=self._vod_quality_resolution_sources()
        if proc_pair!=(0,0) and info_pair!=(0,0) and proc_pair==info_pair:
            return proc_pair
        return proc_pair if proc_pair!=(0,0) else info_pair

    def _reset_vod_quality_probe(self):
        """Erase the previous title's badge and capture its decoder size only as a guard."""
        if self.media_type not in ("vod","series","episode"):
            return
        try:
            timer=getattr(self,"vod_quality_timer",None)
            if timer is not None:timer.stop()
        except Exception:
            pass
        try:pre=self._vod_quality_preplay_snapshot()
        except Exception:pre=(0,0)
        self._vod_quality_probe_generation=int(getattr(self,"_vod_quality_probe_generation",0) or 0)+1
        self._vod_quality_probe_active=False
        self._vod_quality_badge_locked=False
        self._vod_quality_badge_path=""
        self._vod_quality_badge_resolution=(0,0)
        self._vod_quality_probe_preplay=pre
        self._vod_quality_probe_started_at=0.0
        self._vod_quality_probe_size_event=False
        self._vod_quality_probe_size_event_at=0.0
        self._vod_quality_probe_updated_event=False
        self._vod_quality_probe_candidate=(0,0)
        self._vod_quality_probe_candidate_count=0
        self._vod_quality_probe_candidate_since=0.0
        self._observed_quality=""
        self._observed_width=0
        self._observed_height=0
        try:
            if self["vod_quality_badge"].instance is not None:
                self["vod_quality_badge"].instance.setPixmap(None)
            self["vod_quality_badge"].hide()
            self["chip_video_quality"].hide();self["video_quality"].hide()
        except Exception:
            pass

    def _arm_vod_quality_probe(self):
        """Start one short settlement session for the newly-started VOD service."""
        if self.media_type not in ("vod","series","episode"):
            return
        if getattr(self,"_vod_quality_badge_locked",False):
            return
        self._vod_quality_probe_active=True
        self._vod_quality_probe_started_at=time.monotonic()
        self._vod_quality_probe_size_event=False
        self._vod_quality_probe_size_event_at=0.0
        self._vod_quality_probe_updated_event=False
        self._vod_quality_probe_candidate=(0,0)
        self._vod_quality_probe_candidate_count=0
        self._vod_quality_probe_candidate_since=0.0
        try:
            self.vod_quality_timer.stop();self.vod_quality_timer.start(120,True)
        except Exception as exc:
            optional_failure("player.vod_quality_probe_start",exc)

    def _stop_vod_quality_probe(self):
        self._vod_quality_probe_active=False
        try:self.vod_quality_timer.stop()
        except Exception:pass

    def _vod_quality_updated_info_event(self):
        """Record fresh current-service information without doing any continuous work."""
        if self.media_type not in ("vod","series","episode") or not getattr(self,"_vod_quality_probe_active",False):
            return
        try:
            if not self._event_matches_current_reference():return
        except Exception:
            return
        self._vod_quality_probe_updated_event=True
        try:
            self.vod_quality_timer.stop();self.vod_quality_timer.start(60,True)
        except Exception:pass

    def _vod_video_size_changed(self):
        """Debounce decoder-size events for the current service, then let the short probe settle."""
        if self.media_type not in ("vod","series","episode"):
            return
        if not getattr(self,"_vod_quality_probe_active",False):
            return
        try:
            if not self._event_matches_current_reference():return
        except Exception:
            return
        self._vod_quality_probe_size_event=True
        self._vod_quality_probe_size_event_at=time.monotonic()
        try:
            self.vod_quality_timer.stop();self.vod_quality_timer.start(60,True)
        except Exception as exc:
            optional_failure("player.vod_quality_size_event",exc)

    @staticmethod
    def _vod_quality_choose_transition_pair(proc_pair, info_pair, pre):
        """Choose the new-service frame without letting the old decoder win a handoff."""
        zero=(0,0)
        proc_pair=tuple(proc_pair or zero);info_pair=tuple(info_pair or zero);pre=tuple(pre or zero)
        if proc_pair!=zero and info_pair!=zero:
            if proc_pair==info_pair:
                return proc_pair,"agree"
            # During a service replacement one source commonly updates before the
            # other. If exactly one still equals the pre-play frame, the other one
            # is the fresh title and must win, irrespective of pixel area.
            if proc_pair==pre and info_pair!=pre:
                return info_pair,"info-transition"
            if info_pair==pre and proc_pair!=pre:
                return proc_pair,"proc-transition"
            # Two different non-old values means the decoder is still settling.
            return zero,"disagree"
        return (proc_pair if proc_pair!=zero else info_pair),("single" if (proc_pair!=zero or info_pair!=zero) else "none")

    def _vod_quality_probe_tick(self):
        """Settle one new-title resolution, paint one badge, then stop forever for this playback."""
        if self.media_type not in ("vod","series","episode"):
            return self._stop_vod_quality_probe()
        if not getattr(self,"_vod_quality_probe_active",False) or getattr(self,"_vod_quality_badge_locked",False):
            return self._stop_vod_quality_probe()
        if self.restored or self._closing_playback or not self.started:
            return self._stop_vod_quality_probe()
        try:
            if not self._event_matches_current_reference():
                self.vod_quality_timer.start(120,True);return
        except Exception:
            self.vod_quality_timer.start(120,True);return

        now=time.monotonic()
        elapsed=max(0.0,now-float(getattr(self,"_vod_quality_probe_started_at",0.0) or 0.0))
        try:
            service=self.session.nav.getCurrentService()
            info=service and service.info()
            proc_pair,info_pair=self._vod_quality_resolution_sources(info)
        except Exception:
            proc_pair=info_pair=(0,0)

        pre=tuple(getattr(self,"_vod_quality_probe_preplay",(0,0)) or (0,0))
        pair,source=self._vod_quality_choose_transition_pair(proc_pair,info_pair,pre)
        if pair!=(0,0):
            if pair==getattr(self,"_vod_quality_probe_candidate",(0,0)):
                self._vod_quality_probe_candidate_count=int(getattr(self,"_vod_quality_probe_candidate_count",0) or 0)+1
            else:
                self._vod_quality_probe_candidate=pair
                self._vod_quality_probe_candidate_count=1
                self._vod_quality_probe_candidate_since=now
            hits=int(getattr(self,"_vod_quality_probe_candidate_count",0) or 0)
            stable_for=max(0.0,now-float(getattr(self,"_vod_quality_probe_candidate_since",now) or now))
            changed=(pre==(0,0) or pair!=pre)

            # Different from the old title: this is the clean handoff signal.
            # A few short samples are enough, and both source-order cases work
            # (4K->FHD and FHD->4K) without choosing the larger stale frame.
            if changed and hits>=3 and stable_for>=0.20:
                return self._lock_vod_quality_badge(pair[0],pair[1])

            # Same numeric resolution as the old title is accepted only after a
            # fresh decoder-size event for the NEW service. No polling-only path
            # may latch the previous movie's size. A short debounce lets adaptive
            # starts change again before the badge is committed.
            size_at=float(getattr(self,"_vod_quality_probe_size_event_at",0.0) or 0.0)
            if (not changed and getattr(self,"_vod_quality_probe_size_event",False)
                    and size_at>=float(getattr(self,"_vod_quality_probe_started_at",0.0) or 0.0)
                    and hits>=4 and stable_for>=0.65 and (now-size_at)>=0.55):
                return self._lock_vod_quality_badge(pair[0],pair[1])

            # Some external players omit evVideoSizeChanged when two titles have
            # identical frames. Only then use evUpdatedInfo as a conservative
            # startup fallback, never as an immediate carry-over latch.
            if (not changed and getattr(self,"_vod_quality_probe_updated_event",False)
                    and source=="agree" and hits>=8 and elapsed>=2.20):
                return self._lock_vod_quality_badge(pair[0],pair[1])

        # This is a startup-only detector: at most five seconds / ~42 tiny reads,
        # then absolutely no quality polling for the rest of a two-hour movie.
        if elapsed>=5.0:
            return self._stop_vod_quality_probe()
        try:self.vod_quality_timer.start(120,True)
        except Exception as exc:
            optional_failure("player.vod_quality_probe_tick",exc);self._stop_vod_quality_probe()

    def _vod_quality_badge_asset(self, width, height):
        """Return the fixed VOD/episode badge for one real decoder resolution."""
        try:
            width=int(width or 0); height=int(height or 0)
        except Exception:
            return ""
        if width <= 0 or height <= 0:
            return ""
        # Four deliberately broad buckets match the four bundled artwork badges.
        # Cinemascope sources such as 3840x1608 and 1920x800 classify by width.
        if width >= 3000 or height >= 1700:
            name="player_quality_4k_134x42.png"
        elif width >= 1700 or height >= 900:
            name="player_quality_fullhd_134x42.png"
        elif width >= 1200 or height >= 700:
            name="player_quality_hd_134x42.png"
        else:
            name="player_quality_sd_134x42.png"
        path=_asset(name)
        return path if path and os.path.isfile(path) else ""

    def _lock_vod_quality_badge(self, width, height):
        """Paint the Movies/Series quality icon once, then never reclassify it."""
        if self.media_type not in ("vod","series","episode"):
            return False
        if getattr(self,"_vod_quality_badge_locked",False):
            return False
        path=self._vod_quality_badge_asset(width,height)
        if not path:
            return False
        self._vod_quality_badge_path=path
        self._vod_quality_badge_resolution=(int(width or 0),int(height or 0))
        self._vod_quality_badge_locked=True
        if int(width or 0) >= 3000 or int(height or 0) >= 1700:
            quality="4K UHD"
        elif int(width or 0) >= 1700 or int(height or 0) >= 900:
            quality="FHD"
        elif int(width or 0) >= 1200 or int(height or 0) >= 700:
            quality="HD"
        else:
            quality="SD"
        self._observed_quality=quality
        self._observed_width=int(width or 0);self._observed_height=int(height or 0)
        self._stop_vod_quality_probe()
        try:
            if self["vod_quality_badge"].instance is not None:
                self["vod_quality_badge"].instance.setPixmapFromFile(path)
            self["vod_quality_badge"].show()
            self["chip_video_quality"].hide()
            self["video_quality"].hide()
        except Exception as exc:
            optional_failure("player.vod_quality_badge",exc)
        return True

    def _sync_vod_quality_badge(self):
        """Restore the latched badge after InfoBar hide/show without probing again."""
        try:
            if self.media_type in ("vod","series","episode"):
                self["chip_video_quality"].hide(); self["video_quality"].hide()
                path=str(getattr(self,"_vod_quality_badge_path","") or "")
                if getattr(self,"_vod_quality_badge_locked",False) and path and os.path.isfile(path):
                    if self["vod_quality_badge"].instance is not None:
                        self["vod_quality_badge"].instance.setPixmapFromFile(path)
                    self["vod_quality_badge"].show()
                else:
                    self["vod_quality_badge"].hide()
            else:
                self["vod_quality_badge"].hide()
        except Exception as exc:
            optional_failure("player.vod_quality_badge_sync",exc)

    def _update_stream_info(self):
        if getattr(self, "restored", False):
            return
        try:self._drain_provider_live_picon()
        except Exception as exc:optional_failure("player.live_provider_picon_drain",exc)
        try:
            service = self.session.nav.getCurrentService()
            info = service and service.info()
            if info:
                if self.media_type in ("vod","series","episode"):
                    # The regular stream-info timer never probes VOD quality. The
                    # startup-only quality session owns that work and dies after
                    # choosing one icon.
                    if getattr(self,"_vod_quality_badge_locked",False):
                        width,height=getattr(self,"_vod_quality_badge_resolution",(0,0))
                        quality=str(getattr(self,"_observed_quality","") or "AUTO")
                    else:
                        width=height=0;quality="AUTO"
                else:
                    proc_width = self._read_proc_number(("/proc/stb/vmpeg/0/xres",), 16) or 0
                    proc_height = self._read_proc_number(("/proc/stb/vmpeg/0/yres",), 16) or 0
                    info_width = self._info_number(info, "sVideoWidth", 0) or 0
                    info_height = self._info_number(info, "sVideoHeight", 0) or 0
                    candidates=[]
                    for cw,ch in ((proc_width,proc_height),(info_width,info_height)):
                        try:cw=int(cw or 0);ch=int(ch or 0)
                        except Exception:continue
                        if 160 <= cw <= 8192 and 120 <= ch <= 4320:candidates.append((cw*ch,cw,ch))
                    if candidates:_area,width,height=max(candidates,key=lambda row:row[0])
                    else:width=height=0
                    if width >= 3200 or height >= 1800:quality="4K UHD"
                    elif width >= 1700 or height >= 1000:quality="FHD"
                    elif width >= 1200 or height >= 700:quality="HD"
                    elif width > 0 and height > 0:quality="SD"
                    else:quality="AUTO"
                    self._observed_quality=quality
                    self._observed_width=int(width or 0);self._observed_height=int(height or 0)
                    if (width or height) and not self._engine_video_confirmed:
                        self._engine_video_confirmed=True;self._video_guard_misses=0
                self._set_stream_info_text("video_quality",quality[:15])
                self._set_stream_info_text("video_codec",self._video_codec_text(info))
                self._set_stream_info_text("audio_codec",self._audio_codec_text(service, info))
        except Exception as exc:
            optional_failure("player", exc)
        try:
            self.stream_info_timer.stop()
            interval=1000 if getattr(self,"_player_infobar_visible",True) else 5000
            self.stream_info_timer.start(interval, True)
        except Exception as exc:
            optional_failure("player", exc)

    def _memory_fuse_release_visuals(self, aggressive=False):
        # Playback service is deliberately left untouched. Only optional native
        # graphics/decoder helpers are released.
        # Never shed the core InfoBar glass.  With large portal libraries RSS can
        # already be above the old 430 MB threshold when Player opens; the old
        # fuse therefore stripped adaptive_main/keybar/chips a few seconds later
        # and left naked labels over video.  Only artwork/decorative layers are
        # disposable while playback continues.
        # Keep the core VOD/episode identity surfaces resident. They are small
        # compared with the video decoder and must survive InfoBar hide/show.
        # Only genuinely disposable live/progress pixmaps are shed here.
        for name in (
            "live_picon","adaptive_live_picon","progress_neon"
        ):
            try:
                widget=self[name]
                if widget.instance is not None:widget.instance.setPixmap(None)
                try:widget.hide()
                except Exception as exc:optional_failure("player.silent_guard",exc)
            except Exception as exc:optional_failure("player.silent_guard",exc)
        if aggressive:
            try:self.PicLoad.PictureData.get().remove(self._decode_poster)
            except Exception as exc:optional_failure("player.silent_guard",exc)
            try:self.stream_info_timer.stop()
            except Exception as exc:optional_failure("player.silent_guard",exc)
        try:
            with _PROGRESS_FRAME_LOCK:_PROGRESS_FRAME_PENDING.clear()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        try:gc.collect()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        _malloc_trim()

    def _memory_fuse_tick(self):
        if self.restored or self._closing_playback:
            try:self.memory_fuse_timer.stop()
            except Exception as exc:optional_failure("player.silent_guard",exc)
            return
        rss=_process_rss_kb();self._memory_fuse_last_rss=rss
        if not rss:return
        # 430 MB: shed decorative native pixmaps and Python garbage.
        if rss >= 430*1024 and self._memory_fuse_level < 1:
            self._memory_fuse_level=1
            runtime_breadcrumb("player_memory_fuse",level=1,rss_kb=int(rss))
            self._memory_fuse_release_visuals(False)
        # 510 MB: stop all non-essential player image polling/decoding.
        if rss >= 510*1024 and self._memory_fuse_level < 2:
            self._memory_fuse_level=2
            runtime_breadcrumb("player_memory_fuse",level=2,rss_kb=int(rss))
            self._memory_fuse_release_visuals(True)
            try:self["connection"].setText(_("MEMORY PROTECTION  •  playback preserved"))
            except Exception as exc:optional_failure("player.silent_guard",exc)
        # 555 MB: protect Enigma2 itself. Better to leave the current Player
        # cleanly than let the kernel kill the entire GUI around 600 MB.
        if rss >= 555*1024 and not self._memory_fuse_emergency:
            self._memory_fuse_emergency=True
            runtime_breadcrumb("player_memory_fuse",level=3,rss_kb=int(rss))
            try:self["connection"].setText(_("MEMORY SAFETY EXIT"))
            except Exception as exc:optional_failure("player.silent_guard",exc)
            try:self._save_history_progress(force=True)
            except Exception as exc:optional_failure("player.silent_guard",exc)
            self._close_player(save_progress=False)

    def _pause_hidden_visual_timers(self):
        """Throttle purely visual/status polling while the InfoBar is hidden."""
        self._player_infobar_visible=False
        try:self.progress_visual_timer.stop()
        except Exception as exc:optional_failure("player.hidden_progress_visual_stop",exc)
        # Stream metadata is informational only. Keep a slow hidden refresh so
        # adaptive resolution changes are still learned without 1 Hz GUI I/O.
        if self.started and self._memory_fuse_level < 2 and not self.restored and not self._closing_playback:
            try:self.stream_info_timer.stop();self.stream_info_timer.start(5000,True)
            except Exception as exc:optional_failure("player.hidden_stream_info_throttle",exc)

    def _resume_hidden_visual_timers(self):
        """Restore normal visual/status cadence as soon as the InfoBar is shown."""
        self._player_infobar_visible=True
        if self.restored or self._closing_playback or not self.started:return
        if self._memory_fuse_level < 2:
            try:self.stream_info_timer.stop();self._update_stream_info()
            except Exception as exc:optional_failure("player.visible_stream_info_resume",exc)
        if self.media_type in ("vod","series","episode","catchup"):
            try:
                self.progress_visual_timer.stop();self._update_progress_visual();self.progress_visual_timer.start(1000,False)
            except Exception as exc:optional_failure("player.visible_progress_visual_resume",exc)

    def _restore_player_chrome_after_show(self):
        """Re-bind the exact glass pixmaps after every InfoBar show.

        Enigma2 can drop native pixmap surfaces while a Screen is hidden even
        though the Python widgets survive. Rebinding cached files is cheap and
        avoids regenerating adaptive artwork on every OK press.
        """
        if getattr(self,"restored",False) or getattr(self,"_closing_playback",False):return
        # Live picons can arrive asynchronously after playback starts. The old
        # InfoBar kept the neutral blue frames created at init forever, while the
        # later-opened Mini List already saw the real picon palette. Refresh from
        # the now-cached picon before rebinding the InfoBar. Generated frames are
        # persistent/cached, so subsequent OK presses do not redo image work.
        if self.media_type in ("itv","live"):
            try:
                palette_item=self.item
                if 0<=int(getattr(self,"_zap_index",0))<len(getattr(self,"_zap_channels",[]) or []):
                    row=(getattr(self,"_zap_channels",[]) or [])[int(self._zap_index)]
                    if isinstance(row,dict):palette_item=row
                live_art=_cached_poster(palette_item,self.name,self.media_type)
                self._load_live_picon(live_art)
                self._apply_adaptive_player_chrome()
            except Exception as exc:optional_failure("player.live_infobar_adaptive_refresh",exc)
        frames=dict(getattr(self,"_player_chrome_frames",{}) or _fallback_player_frames())
        mapping=(("main","adaptive_main"),("track","adaptive_progress_track"),("keybar","adaptive_keybar"),
                 ("chip_quality","chip_video_quality"),("chip_video","chip_video_codec"),
                 ("chip_audio_codec","chip_audio_codec"),
                 ("chip_stream","chip_stream"),("chip_audio","chip_audio"),
                 ("chip_subtitles","chip_subtitles"),("chip_engine","chip_engine"))
        for key,name in mapping:
            try:
                path=frames.get(key)
                if path and os.path.isfile(path) and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
            except Exception as exc:optional_failure("player.chrome_reshow",exc)
        if self.media_type in ("itv","live"):
            # Adaptive chrome rebinds the shared progress-track pixmap. Re-apply
            # the Live-only layout immediately so EPG, not progress, owns that
            # strip every time the InfoBar is shown.
            try:self._apply_live_infobar_layout()
            except Exception as exc:optional_failure("player.live_layout_reshow",exc)
        self._sync_vod_quality_badge()
        # Artwork-specific chrome is restored without touching playback.
        try:
            if self.media_type in ("itv","live"):
                p=frames.get("live_picon")
                if p and os.path.isfile(p) and self["adaptive_live_picon"].instance is not None:
                    self["adaptive_live_picon"].instance.setPixmapFromFile(p);self["adaptive_live_picon"].show()
            else:
                # Poster is the bottom layer; both existing chrome layers sit above it.
                poster_path=str(getattr(self,"_poster_path","") or _cached_poster(self.item,self.name,self.media_type) or "")
                if poster_path and os.path.isfile(poster_path) and self["logo"].instance is not None:
                    self["logo"].instance.setPixmapFromFile(poster_path);self["logo"].show()
                p=frames.get("poster_halo")
                if p and os.path.isfile(p) and self["poster_neon_halo"].instance is not None:
                    self["poster_neon_halo"].instance.setPixmapFromFile(p);self["poster_neon_halo"].show()
                p=frames.get("poster")
                if p and os.path.isfile(p) and self["adaptive_poster"].instance is not None:
                    self["adaptive_poster"].instance.setPixmapFromFile(p);self["adaptive_poster"].show()
        except Exception as exc:optional_failure("player.chrome_reshow_art",exc)
        if self.media_type not in ("itv","live"):
            try:
                for name in ("channel","category","quality","connection","now"):
                    try:self[name].hide()
                    except Exception:pass
                marker=self._episode_marker_text()
                if marker:
                    try:self["episode_marker"].setText(marker);self["episode_marker"].show()
                    except Exception:pass
                else:
                    try:self["episode_marker"].hide()
                    except Exception:pass
                logo=str(getattr(self,"_player_title_logo_path","") or "")
                if logo and os.path.isfile(logo) and self["title_logo"].instance is not None:
                    self["title_logo"].instance.setPixmapFromFile(logo);self["title_logo"].show();self["vod_title"].hide()
                else:
                    self["title_logo"].hide();self["vod_title"].show()
            except Exception as exc:optional_failure("player.title_reshow",exc)

    def _apply_adaptive_player_chrome(self):
        try:
            palette_item=self.item
            if self.media_type in ("itv","live"):
                try:
                    if 0<=int(getattr(self,"_zap_index",0))<len(getattr(self,"_zap_channels",[]) or []):
                        row=(getattr(self,"_zap_channels",[]) or [])[int(self._zap_index)]
                        if isinstance(row,dict):palette_item=row
                except Exception:pass
            source=_cached_poster(palette_item,self.name,self.media_type)
            self._player_chrome_source=str(source or "")
            fallback=_fallback_player_frames()
            adaptive=_adaptive_player_frames(source)
            # Per-frame merge is deliberate.  A partially generated adaptive
            # set must never remove an otherwise valid neutral glass layer.
            frames=dict(fallback)
            if isinstance(adaptive,dict):
                for key,value in adaptive.items():
                    if value:
                        frames[key]=value
            self._player_chrome_frames=dict(frames)
            for key,name in (("main","adaptive_main"),("poster_halo","poster_neon_halo"),("poster","adaptive_poster"),("live_picon","adaptive_live_picon"),("track","adaptive_progress_track"),("keybar","adaptive_keybar"),
                             ("chip_quality","chip_video_quality"),("chip_video","chip_video_codec"),
                             ("chip_audio_codec","chip_audio_codec"),
                             ("chip_stream","chip_stream"),("chip_audio","chip_audio"),
                             ("chip_subtitles","chip_subtitles"),("chip_engine","chip_engine")):
                path=frames.get(key)
                if path and os.path.isfile(path) and self[name].instance is not None:
                    self[name].instance.setPixmapFromFile(path);self[name].show()
                else:
                    # Only poster-specific decoration may disappear. Core glass
                    # should always exist from the bundled fallback assets.
                    if key in ("poster_halo","poster","live_picon"):
                        self[name].hide()
            self._sync_vod_quality_badge()
            try:
                if self.media_type in ("itv","live"):
                    self["poster_neon_halo"].hide(); self["adaptive_poster"].hide();self["adaptive_live_picon"].show()
                else:
                    # VOD/episode layering is explicit and stable:
                    # real poster first, then the unchanged halo/frame layers above it.
                    self["adaptive_live_picon"].hide()
                    poster_path=str(getattr(self,"_poster_path","") or _cached_poster(self.item,self.name,self.media_type) or "")
                    if poster_path and os.path.isfile(poster_path) and self["logo"].instance is not None:
                        self["logo"].instance.setPixmapFromFile(poster_path);self["logo"].show()
                    self["poster_neon_halo"].show()
                    self["adaptive_poster"].show()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            accent=frames.get("accent") or "#b14b45"
            accent_soft=frames.get("accent_soft") or accent
            self._adaptive_accent_neon=frames.get("accent_neon") or accent_soft
            self._progress_neon_value=-1
            try:
                color=parseColor(accent)
                # Two-layer neon beam: a wide translucent aura plus a narrow white-hot
                # adaptive core.  No separate target/dot at the current position.
                hexrgb = accent.lstrip("#")
                # Status text follows the poster palette instead of fixed cyan.
                self["connection"].instance.setForegroundColor(parseColor(accent_soft))
                self["quality"].instance.setForegroundColor(parseColor(accent_soft))
            except Exception as exc: optional_failure("player.adaptive_colors",exc)
        except Exception as exc:optional_failure("player.adaptive_apply",exc)

    def _apply_live_infobar_layout(self):
        """Live-only compact layout: transparent Now/Next EPG owns the VOD progress strip."""
        if self.media_type not in ("itv","live"):
            try:
                self["live_epg_now"].hide();self["live_epg_next"].hide();self["vod_progress"].show()
            except Exception:pass
            return
        for name in ("category","quality","now","connection","elapsed_time","total_time","remaining_time","adaptive_progress_track","progress_neon","vod_progress"):
            try:self[name].hide()
            except Exception:pass
        try:
            if not getattr(self,"_retry_visual_hold",False):
                self["retry_status"].setText("")
            self["retry_status"].show()
            self["live_epg_now"].show();self["live_epg_next"].show()
        except Exception:pass

    @staticmethod
    def _epg_ts(value):
        try:
            if isinstance(value,(int,float)):return int(value)
            text=str(value or "").strip()
            if text.isdigit():return int(text)
            for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S"):
                try:return int(time.mktime(time.strptime(text[:19],fmt)))
                except Exception:pass
        except Exception:pass
        return 0

    @staticmethod
    def _epg_title(row):
        if not isinstance(row,dict):return ""
        return str(row.get("name") or row.get("title") or row.get("descr") or row.get("description") or "").replace("\n"," ").strip()

    @classmethod
    def _epg_lines(cls, rows):
        now=int(time.time());events=[]
        for row in rows if isinstance(rows,list) else []:
            if not isinstance(row,dict):continue
            start=cls._epg_ts(row.get("start_timestamp") or row.get("start") or row.get("time"))
            stop=cls._epg_ts(row.get("stop_timestamp") or row.get("stop") or row.get("end") or row.get("time_to"))
            # MAG short-EPG commonly supplies start + duration rather than an
            # absolute stop timestamp.  Accept both shapes so Now/Next works on
            # portals that never emit stop_timestamp.
            if not stop and start:
                try:
                    duration=int(row.get("duration") or row.get("length") or 0)
                    if duration>0:stop=start+duration
                except Exception:pass
            if stop and start and stop<start:stop=start+stop
            events.append((start,stop,row))
        events.sort(key=lambda x:x[0] or 0)
        current=None;nxt=None
        for idx,event in enumerate(events):
            st,sp,row=event
            if st and sp and st<=now<sp:
                current=event;nxt=events[idx+1] if idx+1<len(events) else None;break
        if current is None and events:
            future=[e for e in events if not e[0] or e[0]>=now]
            current=future[0] if future else events[0]
            try:pos=events.index(current);nxt=events[pos+1] if pos+1<len(events) else None
            except Exception:nxt=None
        def line(event):
            if not event:return ""
            st,sp,row=event;title=cls._epg_title(row)
            if not title:return ""
            if st and sp:return "%s - %s   %s"%(time.strftime("%H:%M",time.localtime(st)),time.strftime("%H:%M",time.localtime(sp)),title)
            return title
        return line(current),line(nxt)

    def _fit_live_epg_widget(self, name, max_size, min_size):
        try:
            widget=self[name];inst=widget.instance
            if inst is None:return
            if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)
            width=max(160,int(inst.size().width())-10)
            chosen=int(max_size)
            for size in range(int(max_size),int(min_size)-1,-1):
                inst.setFont(gFont("Regular",size))
                try:measured=int(inst.calculateSize().width())
                except Exception:measured=0
                if measured<=0 or int(measured*1.06)<=width:
                    chosen=size;break
                chosen=max(int(min_size),size-1)
            inst.setFont(gFont("Regular",chosen))
        except Exception as exc:optional_failure("player.live_epg_font_fit",exc)

    def _paint_live_epg(self, now_line="", next_line=""):
        if self.media_type not in ("itv","live"):return
        now_text=str(now_line or "").replace("\n"," ").strip()
        next_text=str(next_line or "").replace("\n"," ").strip()
        try:
            self["live_epg_now"].setText(now_text)
            self._fit_live_epg_widget("live_epg_now",21,10)
        except Exception:pass
        try:
            self["live_epg_next"].setText(next_text)
            self._fit_live_epg_widget("live_epg_next",18,10)
        except Exception:pass

    @staticmethod
    def _live_epg_row_ids(row):
        """Return every plausible provider EPG id, strongest field first.

        Home/Favorites/history rows can retain a playback ``id`` that is not the
        Stalker ``ch_id`` expected by get_short_epg.  Folder rows usually carry
        both.  Trying the provider-specific id first keeps normal category opens
        at one request while making compact reopen rows safe.
        """
        if not isinstance(row,dict):return []
        out=[]
        for key in ("ch_id","channel_id","id","stream_id"):
            value=row.get(key)
            if value in (None,""):continue
            text=str(value).strip()
            if text and text not in out:out.append(text)
        return out

    @staticmethod
    def _live_epg_row_command(row):
        if not isinstance(row,dict):return ""
        value=str(row.get("cmd") or row.get("command") or row.get("url") or "").strip().strip('"').strip("'")
        low=value.lower()
        for prefix in ("ffmpeg ","auto "):
            if low.startswith(prefix):
                value=value[len(prefix):].strip();low=value.lower()
        return value

    @staticmethod
    def _live_epg_row_name(row):
        if not isinstance(row,dict):return ""
        value=str(row.get("name") or row.get("title") or row.get("channel_name") or "").strip()
        try:value=_clean_live_name(value,True)
        except Exception:pass
        return re.sub(r"\s+"," ",value).strip().casefold()

    @classmethod
    def _live_epg_same_channel(cls,left,right):
        """Strict-enough identity bridge for compact Home/My List rows.

        This is used only after a direct short-EPG lookup failed.  Playback
        ownership remains untouched; the bridge resolves the provider row only
        for EPG identity.
        """
        if not isinstance(left,dict) or not isinstance(right,dict):return False
        lids=set(cls._live_epg_row_ids(left));rids=set(cls._live_epg_row_ids(right))
        if lids and rids and lids.intersection(rids):return True
        lc=cls._live_epg_row_command(left);rc=cls._live_epg_row_command(right)
        if lc and rc and lc==rc:return True
        ln=cls._live_epg_row_name(left);rn=cls._live_epg_row_name(right)
        return bool(ln and rn and ln==rn)

    def _live_epg_candidate_rows(self):
        """Snapshot rows that may own EPG for the channel currently playing.

        The in-player drawer owns an authoritative provider page even when the
        Player was opened from Home/Favorites.  Prefer that exact row over the
        compact saved item, then fall back to the item itself.
        """
        rows=[]
        try:
            index=int(getattr(self,"_zap_index",0) or 0);channels=getattr(self,"_zap_channels",[]) or []
            if 0<=index<len(channels) and isinstance(channels[index],dict):rows.append(dict(channels[index]))
        except Exception:pass
        if isinstance(self.item,dict):
            current=dict(self.item)
            if not rows or not self._live_epg_same_channel(rows[0],current):rows.append(current)
            else:
                # Keep runtime refs from self.item available to the preferred row.
                for key in ("_live_client_ref","_player_client_ref","_live_category_id","_live_folder_title"):
                    if current.get(key) is not None and rows[0].get(key) is None:rows[0][key]=current.get(key)
        return rows

    @classmethod
    def _live_epg_best_match(cls,target,rows):
        rows=[dict(x) for x in (rows or []) if isinstance(x,dict)]
        if not rows:return None
        # Exact command/id match wins.  Exact normalized title is the bounded
        # fallback used by provider search for old compact favorites/history.
        for row in rows:
            if cls._live_epg_same_channel(target,row):return row
        return None

    def _refresh_live_epg(self):
        if self.media_type not in ("itv","live") or self.restored or self._closing_playback:return
        candidates=self._live_epg_candidate_rows()
        row=candidates[0] if candidates else (self.item if isinstance(self.item,dict) else {})

        # Build channel identity before touching the labels. playService()/evStart
        # legitimately call this method several times for the SAME channel. The old
        # path blanked Now/Next on every call, which made good EPG visibly blink.
        request_key=(self._live_epg_row_command(row) or "|").strip()+"|"+(self._live_epg_row_name(row) or "")
        if request_key=="|":request_key="|".join(self._live_epg_row_ids(row)) or str(self.name or "live")
        same_channel=bool(request_key and request_key==str(getattr(self,"_live_epg_last_channel","") or ""))
        self._apply_live_infobar_layout()
        if not same_channel:
            # Genuine channel boundaries still clear once so the old channel can
            # never leak into the new one. Same-channel refreshes keep the paint.
            self._paint_live_epg("","")

        immediate=str(row.get("now") or row.get("program") or row.get("epg_title") or "").strip()
        nxt=str(row.get("next") or row.get("next_program") or row.get("epg_next") or "").strip()
        if not (immediate or nxt) and isinstance(self.item,dict):
            immediate=str(self.item.get("now") or self.item.get("program") or self.item.get("epg_title") or "").strip()
            nxt=str(self.item.get("next") or self.item.get("next_program") or self.item.get("epg_next") or "").strip()
        if immediate or nxt:self._paint_live_epg(immediate,nxt)

        # Remember identity even when this source has no EPG client. This prevents
        # repeated service lifecycle callbacks from clearing already-painted text.
        self._live_epg_last_channel=request_key
        client=(self.item.get("_live_client_ref") or self.item.get("_player_client_ref")) if isinstance(self.item,dict) else None
        if client is None or not hasattr(client,"epg"):return

        # A stable request identity is independent of whichever candidate id
        # eventually succeeds.  That lets the worker fall through from a compact
        # Home/My List row to the exact provider row without racing a later zap.
        self._live_epg_generation+=1;generation=self._live_epg_generation
        q=self._live_epg_queue
        category_id=str(getattr(self,"_zap_category_id","") or row.get("_live_category_id") or row.get("category_id") or row.get("genre_id") or "")
        query=str(row.get("name") or row.get("title") or self.name or "").strip()
        candidate_snapshot=[dict(x) for x in candidates if isinstance(x,dict)]

        def worker():
            error=None;tried=set()
            def try_row(test_row):
                nonlocal error
                for epg_id in self._live_epg_row_ids(test_row):
                    if epg_id in tried:continue
                    tried.add(epg_id)
                    try:
                        rows=client.epg(epg_id,4) or []
                        if rows:return epg_id,rows
                    except Exception as exc:error=exc
                return "",[]
            # Fast path: exact provider drawer row first, then saved Player row.
            for test_row in candidate_snapshot:
                cid,rows=try_row(test_row)
                if rows:q.put((generation,request_key,rows,None));return

            target=candidate_snapshot[0] if candidate_snapshot else dict(row or {})
            resolved=None
            # A saved Home/Favorites/My List row may be intentionally compact.
            # Resolve EPG identity only after direct ids fail; playback/create_link
            # identity is never changed by this fallback.
            if query and category_id and hasattr(client,"search_category_fast"):
                try:
                    found=client.search_category_fast(query,media_type="itv",category=category_id,limit=24,max_pages=8,time_budget=4) or []
                    resolved=self._live_epg_best_match(target,found)
                except Exception as exc:error=exc
            if resolved is None and query and hasattr(client,"search_content_fast"):
                try:
                    found=client.search_content_fast(query,media_types=("itv",),limit=24,max_pages=8,time_budget=4) or []
                    resolved=self._live_epg_best_match(target,found)
                except Exception as exc:error=exc
            if isinstance(resolved,dict):
                cid,rows=try_row(resolved)
                if rows:q.put((generation,request_key,rows,None));return
            q.put((generation,request_key,[],error))
        try:
            fut=getattr(self,"_live_epg_future",None)
            if fut is not None:
                try:fut.cancel()
                except Exception:pass
            self._live_epg_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.live_epg_timer.stop();self.live_epg_timer.start(100,False)
        except Exception as exc:optional_failure("player.live_epg_start",exc)

    def _drain_live_epg_result(self):
        if self.restored or self._closing_playback:
            try:self.live_epg_timer.stop()
            except Exception:pass
            return
        # Multiple refreshes are normal around playService()/evStart.  A stale
        # worker can finish after a newer generation has already started.  Never
        # let that stale queue item stop the timer and strand the current EPG
        # result behind it.
        matched=None
        while True:
            try:result=self._live_epg_queue.get_nowait()
            except queue.Empty:break
            try:generation,request_key,rows,error=result
            except Exception:continue
            if generation==self._live_epg_generation and request_key==self._live_epg_last_channel:
                matched=(rows,error)
        if matched is None:
            future=getattr(self,"_live_epg_future",None)
            if future is not None and not future.done():return
            try:self.live_epg_timer.stop()
            except Exception:pass
            return
        try:self.live_epg_timer.stop()
        except Exception:pass
        rows,error=matched
        if not error:
            now_line,next_line=self._epg_lines(rows)
            if now_line or next_line:self._paint_live_epg(now_line,next_line)

    def _now_text(self):
        # The compact InfoBar is for playback state, not a duplicate channel name.
        # For Live, center text is reserved for real EPG only.
        if self.media_type in ("vod", "series", "episode", "catchup"):
            return ""
        text = self.item.get("now") or self.item.get("program") or self.item.get("epg_title") or ""
        text = str(text or "").strip()
        if not text:
            return ""
        try:
            if _clean_live_name(text,self._cfg_clean_titles).casefold() == _clean_live_name(self.name,self._cfg_clean_titles).casefold():
                return ""
        except Exception:
            if text.casefold() == str(self.name or "").casefold():
                return ""
        return text[:62]

    def _init_online_subtitle_overlay(self):
        return

    def _init_online_subtitle_display(self):
        if self._online_subtitle_display is not None or SubtitleDisplay is None:
            return
        try:
            maker=getattr(self.session,"instantiateDialog",None)
            if callable(maker):
                self._online_subtitle_display=maker(SubtitleDisplay)
                self._online_subtitle_display.hideScreen()
                self._apply_subtitle_style_native()
        except Exception as exc:
            optional_failure("player.native_online_subtitle_init",exc)
            self._online_subtitle_display=None

    def _player_infobar_shown(self):
        return

    def _player_infobar_hidden(self):
        return

    def _set_online_subtitle_text(self,text):
        text=str(text or "")
        self._online_subtitle_current_text=text
        display=self._online_subtitle_display
        if display is None:
            # Safe fallback only; normal OpenBH path uses SubtitleDisplay.
            try:self["online_subtitle"].setText(text)
            except Exception:pass
            return
        try:
            if text:
                display.showSubtitles(text)
                self._apply_subtitle_style_native()
            else:
                display.hideSubtitles()
        except Exception as exc:
            optional_failure("player.native_online_subtitle_text",exc)

    def _subtitle_volume_osd_event(self):
        if self._online_subtitle_active and self._online_subtitle_current_text:
            try:self._set_online_subtitle_text(self._online_subtitle_current_text)
            except Exception:pass
            try:self.subtitle_volume_restore_timer.stop();self.subtitle_volume_restore_timer.start(80,True)
            except Exception:pass
        # Let the receiver's normal VolumeActions continue unchanged.
        return 0

    def _restore_subtitle_after_volume_osd(self):
        if not self._online_subtitle_active or not self._online_subtitle_current_text:return
        try:
            self._set_online_subtitle_text(self._online_subtitle_current_text)
            display=self._online_subtitle_display
            if display is not None:
                try:display.show()
                except Exception:pass
        except Exception as exc:optional_failure("player.subtitle_volume_restore",exc)

    def _subtitle_take_key_ownership(self):
        """Temporarily unbind the Player's EXIT handlers while subtitle UI owns input.

        This mirrors the ownership boundary of Enigma2's native Audio screen:
        while the child UI is active, the Player must not also receive the same
        BACK/EXIT event.  The inline subtitle UI itself remains unchanged.
        """
        if self._subtitle_player_keymaps_suspended:
            return
        self._subtitle_player_keymaps_suspended=True
        for name in ("hard_exit_actions","player_actions"):
            try:self[name].setEnabled(False)
            except Exception as exc:optional_failure("player.subtitle_key_owner_disable_%s"%name,exc)

    def _subtitle_release_key_ownership(self):
        if not self._subtitle_player_keymaps_suspended:
            return
        self._subtitle_player_keymaps_suspended=False
        for name in ("player_actions","hard_exit_actions"):
            try:self[name].setEnabled(True)
            except Exception as exc:optional_failure("player.subtitle_key_owner_enable_%s"%name,exc)

    def _subtitle_inline_lock(self):
        if self._subtitle_inline_locked:
            return
        self._subtitle_inline_locked=True
        self._subtitle_take_key_ownership()
        # Call our visibility controller explicitly. Some receiver images ship
        # InfoBar mixins with similarly named lockShow/unlockShow methods; MRO
        # must never decide whether the subtitle picker can disappear.
        try:UltraInfobarVisibility.lockShow(self)
        except Exception as exc:optional_failure("player.subtitle_inline_lock",exc)

    def _subtitle_native_hold_begin(self):
        if self._subtitle_native_selector_hold:
            return
        self._subtitle_native_selector_hold=True
        try:UltraInfobarVisibility.lockShow(self)
        except Exception as exc:optional_failure("player.subtitle_native_hold_begin",exc)

    def _subtitle_native_hold_end(self,*_args,**_kwargs):
        if not self._subtitle_native_selector_hold:
            return
        self._subtitle_native_selector_hold=False
        try:UltraInfobarVisibility.unlockShow(self)
        except Exception as exc:optional_failure("player.subtitle_native_hold_end",exc)
        # Returning from the receiver's native subtitle selector should restore
        # the normal Player InfoBar and only then resume its regular timeout.
        if not self.restored and not self._closing_playback:
            try:UltraInfobarVisibility.doShow(self)
            except Exception as exc:optional_failure("player.subtitle_native_hold_show",exc)

    def _subtitle_inline_finish(self):
        try:
            if getattr(self,"_subtitle_inline_overlay",None) is not None and self._subtitle_inline_overlay.active:
                self._subtitle_inline_overlay.hide()
        except Exception as exc:optional_failure("player.subtitle_inline_hide",exc)
        self._subtitle_inline_level=""
        if self._subtitle_inline_locked:
            self._subtitle_inline_locked=False
            try:UltraInfobarVisibility.unlockShow(self)
            except Exception as exc:optional_failure("player.subtitle_inline_unlock",exc)
        self._subtitle_release_key_ownership()

    def _subtitle_inline_show(self,choices,selection,on_accept,on_close,level):
        self._subtitle_inline_lock()
        self._subtitle_inline_level=str(level or "")
        frames=dict(getattr(self,"_player_chrome_frames",{}) or {})
        source=str(getattr(self,"_player_chrome_source","") or "")
        if not (source and os.path.isfile(source)):
            try:source=str(_cached_poster(self.item,self.name,self.media_type) or "")
            except Exception:source=""
        # Player owns the palette authority. If artwork is unavailable, use the
        # already-bound Player main glass as the neutral source rather than ever
        # borrowing Home/Settings hero material.
        if not (source and os.path.isfile(source)):
            candidate=str(frames.get("main") or "")
            if candidate and os.path.isfile(candidate):source=candidate
        return self._subtitle_inline_overlay.show(
            choices,selection=selection,center_x=960,anchor_bottom=780,
            on_accept=on_accept,on_close=on_close,
            min_card_w=180,max_card_w=520,padding=38,
            visual_style="category",row_h=70,max_visible=5,
            adaptive_source=source,adaptive_accent=frames.get("accent"),
            adaptive_accent_soft=frames.get("accent_soft"),
        )

    def _subtitle_back_from_tracks(self):
        self.subtitleSelection("subtitle_tracks")

    def _subtitle_back_from_settings(self):
        self.subtitleSelection("subtitle_settings")

    def _subtitle_back_from_size(self):
        self._open_subtitle_settings_menu("style_size")

    def _subtitle_back_from_color(self):
        self._open_subtitle_settings_menu("style_color")

    def _subtitle_back_from_background(self):
        self._open_subtitle_settings_menu("style_background")

    def _subtitle_back_from_position(self):
        self._open_subtitle_settings_menu("style_position")

    def _subtitle_back_from_online_languages(self):
        provider=str(getattr(self,"_online_subtitle_provider","subdl") or "subdl").lower()
        self._open_subtitle_tracks_menu("online_subsource" if provider=="subsource" else "online_subdl")

    def _subtitle_back_from_online_results(self):
        language=str(getattr(self,"_online_subtitle_language","EN") or "EN").upper().replace("-","_")
        self._open_online_subtitle_languages_menu(language)

    def subtitleSelection(self,preselect_action=None):
        """Show the compact subtitle hub inside the Player itself."""
        self._subtitle_user_override=True
        try:self.subtitle_default_timer.stop()
        except Exception as exc:optional_failure("player",exc)
        choices=[
            (_("Choose Subtitles"),"subtitle_tracks"),
            (_("Subtitle Settings"),"subtitle_settings"),
        ]
        _pre=str(preselect_action or "")
        selected=1 if (_pre=="subtitle_settings" or _pre.startswith(("style_","delay_","sync_","auto_sync"))) else 0
        try:
            return self._subtitle_inline_show(choices,selected,self._subtitle_hub_selected,self._subtitle_inline_finish,"hub")
        except Exception as exc:
            optional_failure("player.subtitle_menu",exc)
            self._subtitle_inline_finish()
            return self._open_native_subtitles()

    def _subtitle_hub_selected(self,choice=None):
        if not choice:
            return self._subtitle_inline_finish()
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if action=="subtitle_tracks":self._open_subtitle_tracks_menu()
        elif action=="subtitle_settings":
            if not self._external_subtitle_settings():self._open_subtitle_settings_menu()

    def _open_subtitle_tracks_menu(self,preselect_action=None):
        choices=[
            (_("Local subtitles"),"embedded"),
            (_("Online subtitles")+" • SubDL","online_subdl"),
            (_("Online subtitles")+" • SubSource","online_subsource"),
            (_("SubsSupport")+" • Enigma2","subssupport"),
            ("SubsSupportPro • Enigma2","subssupportpro"),
            (_("Disable subtitles"),"disable"),
        ]
        selected=0
        if preselect_action:
            for idx,row in enumerate(choices):
                if isinstance(row,(tuple,list)) and len(row)>1 and row[1]==preselect_action:
                    selected=idx;break
        return self._subtitle_inline_show(choices,selected,self._subtitle_track_selected,self._subtitle_back_from_tracks,"tracks")

    def _online_subtitle_language_choices(self,provider=None):
        provider=str(provider or getattr(self,"_online_subtitle_provider","subdl") or "subdl").strip().lower()
        provider_name="SubSource" if provider=="subsource" else "SubDL"
        choices=[]
        for display_name,interface_code in interface_language_choices():
            language=interface_subtitle_language_code(interface_code)
            choices.append(("%s • %s"%(display_name,provider_name),"online_%s"%language))
        return choices

    def _open_online_subtitle_languages_menu(self,preselect_language=None,provider=None):
        if provider:
            self._online_subtitle_provider=str(provider).strip().lower()
        elif not getattr(self,"_online_subtitle_provider",None):
            self._online_subtitle_provider="subdl"
        choices=self._online_subtitle_language_choices(self._online_subtitle_provider)
        selected=0
        wanted=str(preselect_language or "").strip().upper().replace("-","_")
        if wanted:
            wanted="online_%s"%wanted
            for idx,row in enumerate(choices):
                if isinstance(row,(tuple,list)) and len(row)>1 and row[1]==wanted:
                    selected=idx;break
        return self._subtitle_inline_show(
            choices,selected,self._online_subtitle_language_selected,
            self._subtitle_back_from_online_languages,"online_languages"
        )

    def _online_subtitle_language_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_online_languages()
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if str(action).startswith("online_"):
            return self._start_online_subtitles(str(action).split("_",1)[1],getattr(self,"_online_subtitle_provider","subdl"))

    def _transport_is_playing(self):
        try:
            state=getattr(self,"seekstate",None)
            play_state=getattr(InfoBarSeek,"SEEK_STATE_PLAY",None)
            if play_state is not None and state==play_state:return True
            if isinstance(state,(tuple,list)) and len(state)>3:
                value=str(state[3] or "")
                return value==">" or value.startswith(">>") or value.startswith("<<") or value.startswith("/")
        except Exception:pass
        value=str(getattr(self,"_last_play_state_value","") or "")
        return value==">" or value.startswith(">>") or value.startswith("<<") or value.startswith("/")

    def _set_transport_paused(self, paused):
        try:
            setter=getattr(self,"setSeekState",None)
            target=getattr(InfoBarSeek,"SEEK_STATE_PAUSE" if paused else "SEEK_STATE_PLAY",None)
            if callable(setter) and target is not None:
                setter(target);return True
        except Exception as exc:optional_failure("player.external_subtitle_seekstate",exc)
        try:
            service=self.session.nav.getCurrentService();pauseable=service.pause() if service else None
            if pauseable is None:return False
            if paused:pauseable.pause()
            else:pauseable.unpause()
            return True
        except Exception as exc:
            optional_failure("player.external_subtitle_pause",exc);return False

    def _external_subtitle_ui_hold_begin(self):
        if getattr(self,"_external_subtitle_ui_hold",False):return
        was_playing=self._transport_is_playing()
        self._external_subtitle_ui_hold=True
        self._external_subtitle_ui_seen_foreign=False
        self._external_subtitle_ui_resume_after=bool(was_playing)
        self._external_subtitle_ui_hold_started=time.monotonic()
        if was_playing:self._set_transport_paused(True)
        try:self.external_subtitle_ui_timer.stop();self.external_subtitle_ui_timer.start(180,True)
        except Exception as exc:optional_failure("player.external_subtitle_hold_timer",exc)

    def _external_subtitle_ui_hold_end(self):
        if not getattr(self,"_external_subtitle_ui_hold",False):return
        resume_after=bool(getattr(self,"_external_subtitle_ui_resume_after",False))
        self._external_subtitle_ui_hold=False
        self._external_subtitle_ui_seen_foreign=False
        self._external_subtitle_ui_resume_after=False
        self._external_subtitle_ui_hold_started=0.0
        try:self.external_subtitle_ui_timer.stop()
        except Exception:pass
        if resume_after and not self.restored and not self._closing_playback:
            self._set_transport_paused(False)

    def _external_subtitle_ui_watch(self):
        if not getattr(self,"_external_subtitle_ui_hold",False):return
        try:current=getattr(self.session,"current_dialog",None)
        except Exception:current=None
        if current is not None and current is not self:
            self._external_subtitle_ui_seen_foreign=True
        elif current is self and getattr(self,"_external_subtitle_ui_seen_foreign",False):
            self._external_subtitle_ui_hold_end();return
        elif not getattr(self,"_external_subtitle_ui_seen_foreign",False):
            if time.monotonic()-float(getattr(self,"_external_subtitle_ui_hold_started",0.0) or 0.0)>1.5:
                self._external_subtitle_ui_hold_end();return
        try:self.external_subtitle_ui_timer.start(180,True)
        except Exception as exc:optional_failure("player.external_subtitle_hold_watch",exc)

    def _subtitle_session_capture(self):
        key=str(getattr(self,"_subtitle_session_key","") or "")
        if not key:return
        state=None
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is not None:
            try:state=bridge.snapshot()
            except Exception as exc:optional_failure("player.subtitle_session_external_capture",exc)
        if state is None and bool(getattr(self,"_online_subtitle_active",False)):
            path=str(getattr(self,"_online_subtitle_path","") or "")
            if path and os.path.isfile(path):
                provider=str(getattr(self,"_online_subtitle_provider","subdl") or "subdl").lower()
                if provider not in ("subdl","subsource"):provider="subdl"
                state={
                    "source":provider,"path":path,
                    "offset_ms":int(getattr(self,"_online_subtitle_offset_ms",0) or 0),
                    "scale":float(getattr(self,"_online_subtitle_scale",1.0) or 1.0),
                    "language":str(getattr(self,"_online_subtitle_language","EN") or "EN"),
                    "language_name":str(getattr(self,"_online_subtitle_language_name","") or ""),
                }
        with _SUBTITLE_SESSION_LOCK:
            if state is None:
                _SUBTITLE_SESSION_STATE.pop(key,None);return
            state=dict(state);state["saved_at"]=time.time()
            _SUBTITLE_SESSION_STATE[key]=state
            if len(_SUBTITLE_SESSION_STATE)>_SUBTITLE_SESSION_MAX:
                oldest=sorted(_SUBTITLE_SESSION_STATE.items(),key=lambda kv:float((kv[1] or {}).get("saved_at",0.0)))
                for old_key,_old_state in oldest[:len(_SUBTITLE_SESSION_STATE)-_SUBTITLE_SESSION_MAX]:
                    _SUBTITLE_SESSION_STATE.pop(old_key,None)

    def _subtitle_session_state(self):
        key=str(getattr(self,"_subtitle_session_key","") or "")
        if not key or self.media_type not in ("vod","series","episode","catchup"):
            return {}
        with _SUBTITLE_SESSION_LOCK:
            return dict(_SUBTITLE_SESSION_STATE.get(key) or {})

    def _schedule_subtitle_session_restore(self, delay_ms=700):
        if getattr(self,"_subtitle_session_restore_done",False) or self.restored or self._closing_playback:
            return False
        state=self._subtitle_session_state()
        if not state:
            self._subtitle_session_restore_done=True
            return False
        path=str(state.get("path") or "")
        if not path or not os.path.isfile(path):
            key=str(getattr(self,"_subtitle_session_key","") or "")
            if key:
                with _SUBTITLE_SESSION_LOCK:_SUBTITLE_SESSION_STATE.pop(key,None)
            self._subtitle_session_restore_done=True
            return False
        self._subtitle_session_restore_pending=True
        if not getattr(self,"_subtitle_session_restore_started_at",0.0):
            self._subtitle_session_restore_started_at=time.monotonic()
        try:
            self.subtitle_session_restore_timer.stop()
            self.subtitle_session_restore_timer.start(max(80,int(delay_ms)),True)
            return True
        except Exception as exc:
            optional_failure("player.subtitle_session_restore_schedule",exc)
            return False

    def _subtitle_session_playback_ready(self):
        if not self.started or self.restored or self._closing_playback:
            return False
        # An explicit resume target means the decoder is still being moved to
        # the stored bookmark. Do not bind subtitle timing to the old position.
        if int(getattr(self,"_resume_target",0) or 0)>0:
            return False
        try:
            service=self.session.nav.getCurrentService();seek=service.seek() if service else None
            pos=seek.getPlayPosition() if seek else None
            if not pos or pos[0]:return False
            int(pos[1] or 0)
        except Exception:
            return False
        # On images using the native resume callback, give evUpdatedInfo a short
        # chance to issue its seek before restoring subtitles. The guard is
        # bounded so Start-from-beginning never waits indefinitely.
        bookmark=0
        try:bookmark=int(self._resume_bookmark() or 0)
        except Exception:bookmark=0
        elapsed=time.monotonic()-float(getattr(self,"_subtitle_session_restore_started_at",0.0) or time.monotonic())
        if bookmark and not bool(getattr(self,"resume_prompted",False)) and not bool(getattr(self,"_native_resume_started",False)) and elapsed<2.4:
            return False
        if self._watch_started_at and (time.time()-float(self._watch_started_at or 0.0))<0.75:
            return False
        return True

    def _subtitle_session_restore_tick(self):
        if bool(getattr(self,"_subtitle_session_post_refresh_pending",False)):
            self._subtitle_session_post_restore_refresh()
            return
        if getattr(self,"_subtitle_session_restore_done",False) or self.restored or self._closing_playback:
            self._subtitle_session_restore_pending=False
            return
        if not self._subtitle_session_playback_ready():
            self._subtitle_session_restore_attempts=int(getattr(self,"_subtitle_session_restore_attempts",0) or 0)+1
            if self._subtitle_session_restore_attempts<36:
                self._schedule_subtitle_session_restore(160)
            else:
                # Final bounded attempt. If the service never exposes a usable
                # position, leave playback alone rather than forcing stale timing.
                self._subtitle_session_restore_done=True
                self._subtitle_session_restore_pending=False
            return
        self._subtitle_session_restore_pending=False
        restored_ok=self._subtitle_session_restore()
        if restored_ok:
            # One extra refresh after the provider/renderer has had a frame to
            # settle. This replaces the old user "nudge" that made subtitles
            # suddenly appear after changing delay by a tiny amount.
            self._subtitle_session_post_refresh_pending=True
            try:self.subtitle_session_restore_timer.stop();self.subtitle_session_restore_timer.start(260,True)
            except Exception:pass
        else:
            self._subtitle_session_restore_done=True

    def _subtitle_session_post_restore_refresh(self):
        self._subtitle_session_post_refresh_pending=False
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is not None and bridge.owner is not None and bridge.is_loaded():
            try:
                bridge.owner.resumeSubs()
                bridge.after_seek()
            except Exception as exc:optional_failure("player.subtitle_session_external_refresh",exc)
        if bool(getattr(self,"_online_subtitle_active",False)):
            try:self._online_subtitle_tick()
            except Exception as exc:optional_failure("player.subtitle_session_online_refresh",exc)

    def _subtitle_session_restore(self):
        if getattr(self,"_subtitle_session_restore_done",False):return False
        state=self._subtitle_session_state()
        if not state:
            self._subtitle_session_restore_done=True
            return False
        path=str(state.get("path") or "")
        if not path or not os.path.isfile(path):
            key=str(getattr(self,"_subtitle_session_key","") or "")
            if key:
                with _SUBTITLE_SESSION_LOCK:_SUBTITLE_SESSION_STATE.pop(key,None)
            self._subtitle_session_restore_done=True
            return False
        source=str(state.get("source") or "").lower()
        try:
            if source in ("subssupport","subssupportpro"):
                self._disable_online_subtitles();self._disable_native_subtitles()
                bridge=getattr(self,"_external_subtitle_bridge",None)
                if bridge is not None and bridge.restore(state):
                    self._subtitle_user_override=True
                    self._subtitle_session_restore_done=True
                    return True
            elif source in ("subdl","subsource"):
                self._external_subtitle_stop()
                self._online_subtitle_provider=source
                self._online_subtitle_language=str(state.get("language") or "EN")
                self._online_subtitle_language_name=str(state.get("language_name") or "")
                if self._activate_online_subtitle(path,"Session"):
                    self._online_subtitle_offset_ms=max(-300000,min(300000,int(state.get("offset_ms") or 0)))
                    try:scale=float(state.get("scale") or 1.0)
                    except Exception:scale=1.0
                    self._online_subtitle_scale=scale if 0.94<=scale<=1.06 else 1.0
                    self._subtitle_session_restore_done=True
                    return True
        except Exception as exc:
            optional_failure("player.subtitle_session_restore",exc)
        return False

    def _external_subtitle_stop(self):
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is not None:
            bridge.close()

    def _external_subtitle_open(self,kind):
        """Hand subtitle ownership to SubsSupport/Pro without touching its files."""
        self._subtitle_inline_finish()
        if self.media_type not in ("vod","series","episode","catchup"):
            try:self.session.open(MessageBox,_('Online subtitles are available for Movies and Series.'),MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass
            return 0
        # Exactly one renderer owns external subtitles at a time. Ultra's own
        # SubDL/SubSource/native renderer is stopped before provider handoff.
        # Keep the movie paused until the provider menu stack returns to Player.
        self._external_subtitle_ui_hold_begin()
        self._disable_online_subtitles()
        self._disable_native_subtitles()
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is None:
            bridge=_ExternalSubtitleProviderBridge(self);self._external_subtitle_bridge=bridge
        try:
            if bridge.open_menu(kind):
                return 1
        except ImportError as exc:
            optional_failure("player.external_subtitle_import",exc)
            label="SubsSupportPro" if kind=="subssupportpro" else "SubsSupport"
            try:self.session.open(MessageBox,'%s\n\n%s'%(label,_("SubsSupport plugin is not installed.")),MessageBox.TYPE_INFO,timeout=6)
            except Exception:pass
            self._external_subtitle_ui_hold_end()
            return 0
        except Exception as exc:
            optional_failure("player.external_subtitle_open",exc)
            label="SubsSupportPro" if kind=="subssupportpro" else "SubsSupport"
            try:self.session.open(MessageBox,'%s\n\n%s'%(label,_("SubsSupport could not be opened.")),MessageBox.TYPE_INFO,timeout=6)
            except Exception:pass
            self._external_subtitle_ui_hold_end()
            return 0
        self._external_subtitle_ui_hold_end()
        return 0

    def _external_subtitle_settings(self):
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is None or bridge.owner is None:
            return False
        self._subtitle_inline_finish()
        try:
            return bool(bridge.open_status())
        except Exception as exc:
            optional_failure("player.external_subtitle_status",exc)
            return False

    def _external_subtitle_after_seek(self):
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is not None:
            bridge.after_seek()

    def _external_subtitle_play_state(self,value):
        bridge=getattr(self,"_external_subtitle_bridge",None)
        if bridge is not None:
            bridge.play_state(value)

    def _subtitle_track_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_tracks()
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if action=="embedded":
            self._external_subtitle_stop();self._subtitle_inline_finish();return self._open_native_subtitles()
        if action=="online_subdl":
            self._external_subtitle_stop();return self._open_online_subtitle_languages_menu(provider="subdl")
        if action=="online_subsource":
            self._external_subtitle_stop();return self._open_online_subtitle_languages_menu(provider="subsource")
        if action=="subssupport":
            return self._external_subtitle_open("subssupport")
        if action=="subssupportpro":
            return self._external_subtitle_open("subssupportpro")
        if action=="disable":
            self._external_subtitle_stop();self._disable_online_subtitles();self._disable_native_subtitles()
            try:self["connection"].setText(_("PLAYING  •  SUBTITLES OFF"))
            except Exception:pass
            return self.subtitleSelection("subtitle_tracks")

    def _open_subtitle_settings_menu(self,preselect_action=None):
        _size=int(self._subtitle_style.get("size",38) or 38)
        _size_name=("Normal" if _size<=38 else ("Large" if _size<=44 else ("Larger" if _size<=50 else "Extra Large")))
        choices=[
            (_("Subtitle Size • %s")%_(_size_name),"style_size"),
            (_("Subtitle Color • %s")%_(_subtitle_color_name(self._subtitle_style.get("color"))),"style_color"),
            (_("Subtitle Background • %s")%(_("Black") if self._subtitle_style.get("background") else _("Off")),"style_background"),
            (_("Subtitle Position • %s")%_(_subtitle_position_name(self._subtitle_style.get("position"))),"style_position"),
        ]
        if self._online_subtitle_active:
            choices += [
                (_("Auto Sync • lightweight"),"auto_sync"),
                (_("Subtitle delay -0.5 sec"),"delay_minus"),
                (_("Subtitle delay +0.5 sec"),"delay_plus"),
                (_("Reset subtitle sync"),"sync_reset"),
            ]
        selected=0
        if preselect_action:
            for idx,row in enumerate(choices):
                if isinstance(row,(tuple,list)) and len(row)>1 and row[1]==preselect_action:
                    selected=idx;break
        return self._subtitle_inline_show(choices,selected,self._subtitle_settings_selected,self._subtitle_back_from_settings,"settings")

    def _subtitle_settings_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_settings()
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if action=="style_size":self._open_subtitle_size_menu()
        elif action=="style_color":self._open_subtitle_color_menu()
        elif action=="style_background":self._open_subtitle_background_menu()
        elif action=="style_position":self._open_subtitle_position_menu()
        elif action=="delay_minus":self._adjust_subtitle_delay(-500);self._open_subtitle_settings_menu("delay_minus")
        elif action=="delay_plus":self._adjust_subtitle_delay(500);self._open_subtitle_settings_menu("delay_plus")
        elif action=="sync_reset":
            self._online_subtitle_offset_ms=0;self._online_subtitle_scale=1.0;self._save_subtitle_sync();self._open_subtitle_settings_menu("sync_reset")
        elif action=="auto_sync":self._auto_sync_subtitle()

    def _native_subtitle_track_list(self):
        """Return the current service's real embedded subtitle list.

        ``None`` means this image/engine does not expose ``getSubtitleList``
        through the public subtitle interface, in which case the stock Enigma2
        selector remains the authority. An empty list means the interface is
        available but tracks have not been published yet (or truly do not
        exist).
        """
        try:
            service=self.session.nav.getCurrentService()
            subtitle=service.subtitle() if service else None
            if subtitle is None:
                return []
            getter=getattr(subtitle,"getSubtitleList",None)
            if not callable(getter):
                return None
            tracks=getter()
            if tracks is None:
                return []
            try:
                return list(tracks)
            except TypeError:
                return [tracks] if tracks else []
        except Exception as exc:
            optional_failure("player.embedded_subtitle_probe",exc)
            return []

    def _open_native_subtitle_selector_now(self):
        """Open OpenBH's real subtitle screen without the InfoBar pre-gate.

        InfoBarSubtitleSupport.subtitleSelection() first checks getSubtitleList()
        and silently returns when ServiceApp has not published the list at that
        exact instant. Our probe has already done the readiness check, so calling
        that wrapper here can make the user fall straight back to the player.
        Open the stock SubtitleSelection screen itself instead. The UI and track
        handling remain 100% native Enigma2/OpenBH.
        """
        self._embedded_subtitle_probe_pending=False
        try:self.embedded_subtitle_probe_timer.stop()
        except Exception:pass
        try:self.selected_subtitle=None
        except Exception:pass
        self._subtitle_native_hold_begin()
        try:
            from Screens.AudioSelection import SubtitleSelection as _NativeSubtitleSelection
            self.session.openWithCallback(self._subtitle_native_hold_end,_NativeSubtitleSelection,infobar=self)
            return 1
        except Exception as exc:
            optional_failure("player.native_subtitle_screen",exc)
            self._subtitle_native_hold_end()
        # Last-resort compatibility path for images with a non-standard
        # AudioSelection module.  We cannot reliably observe that legacy
        # selector's close callback, so release the modal hold before handing
        # control to the image-provided implementation.
        try:
            return InfoBarSubtitleSupport.subtitleSelection(self)
        except Exception as exc:
            optional_failure("player.native_subtitleSelection", exc)
            return 0

    def _embedded_subtitle_probe_tick(self):
        if not self._embedded_subtitle_probe_pending:
            return 0
        if self.restored or self._closing_playback:
            self._embedded_subtitle_probe_pending=False
            return 0
        self._embedded_subtitle_probe_attempts += 1
        tracks=self._native_subtitle_track_list()
        # If the image does not expose getSubtitleList(), do not second-guess
        # it. Open the receiver's own selector immediately.
        if tracks is None:
            return self._open_native_subtitle_selector_now()
        if tracks:
            return self._open_native_subtitle_selector_now()
        # ServiceApp/ExtePlayer can publish subtitle PIDs several seconds after
        # picture/audio are already running. Give the service a real window to
        # finish metadata discovery without blocking the GUI.
        if self._embedded_subtitle_probe_attempts < 12:
            try:
                self["connection"].setText(_("WAITING  •  EMBEDDED SUBTITLES"))
            except Exception:pass
            try:
                self.embedded_subtitle_probe_timer.stop()
                self.embedded_subtitle_probe_timer.start(450,True)
                return 1
            except Exception as exc:
                optional_failure("player.embedded_subtitle_timer",exc)
                return self._open_native_subtitle_selector_now()
        self._embedded_subtitle_probe_pending=False
        try:self["connection"].setText("")
        except Exception:pass
        try:
            self.session.open(MessageBox,_("No embedded subtitles were reported by this stream."),MessageBox.TYPE_INFO,timeout=5)
        except Exception:pass
        return 0

    def _open_native_subtitles(self):
        """Open the receiver's native embedded-subtitle screen immediately.

        This is an explicit user action, so do not hide the native screen behind
        InfoBarSubtitleSupport's subtitle-list readiness gate. OpenBH's
        SubtitleSelection screen already listens for service-info updates and can
        populate tracks as ServiceApp publishes them.
        """
        self._subtitle_inline_finish()
        self._disable_online_subtitles()
        self._subtitle_user_override=True
        try:self.subtitle_default_timer.stop()
        except Exception:pass
        try:self.embedded_subtitle_probe_timer.stop()
        except Exception:pass
        self._embedded_subtitle_probe_pending=False
        return self._open_native_subtitle_selector_now()

    def _subtitle_picker_source(self):
        return str(getattr(self,"_poster_path","") or _cached_poster(self.item,self.name,self.media_type) or "")

    def _persist_subtitle_style(self):
        try:
            save_settings({
                "subtitle_color": str(self._subtitle_style.get("color") or "#FFFFFF"),
                "subtitle_background": bool(self._subtitle_style.get("background", False)),
                "subtitle_position": max(-260, min(260, int(self._subtitle_style.get("position", 0) or 0))),
                "subtitle_size": max(30, min(56, int(self._subtitle_style.get("size", 38) or 38))),
            })
        except Exception as exc:optional_failure("player.subtitle_style_save",exc)

    def _apply_subtitle_style_native(self):
        """Style the stock OpenBH SubtitleDisplay in-place.

        No replacement Screen, no alternate subtitle lifecycle. The receiver still
        owns show/hide/timing; Ultra only changes the existing label presentation.
        """
        display=self._online_subtitle_display
        if display is None:return
        try:
            label=display["subtitles"]
            inst=getattr(label,"instance",None)
            if inst is None:return
            font_size=max(30,min(56,int(self._subtitle_style.get("size",38) or 38)))
            try:inst.setFont(gFont("Regular",font_size))
            except Exception:pass
            try:
                fallback_inst=self["online_subtitle"].instance
                if fallback_inst is not None:fallback_inst.setFont(gFont("Regular",font_size))
            except Exception:pass
            inst.setForegroundColor(parseColor(str(self._subtitle_style.get("color") or "#FFFFFF")))
            background=bool(self._subtitle_style.get("background",False))
            try:inst.setBackgroundColor(parseColor("#000000"))
            except Exception:pass
            try:inst.setTransparent(0 if background else 1)
            except Exception:pass

            # Native showSubtitles has already measured the cue. Reposition that
            # same label, without replacing SubtitleDisplay or its timers.
            try:
                size=label.getSize()
                width=max(100,int(size[0])+36);height=max(int(font_size*2.4),int(size[1])+16)
                desktop=getDesktop(0).size()
                offset=max(-260,min(260,int(self._subtitle_style.get("position",0) or 0)))
                x=max(0,(desktop.width()-width)//2)
                y=max(20,min(desktop.height()-height-18,desktop.height()-height-32-offset))
                inst.resize(eSize(width,height));inst.move(ePoint(x,y))
            except Exception as exc:optional_failure("player.subtitle_style_position",exc)
        except Exception as exc:
            optional_failure("player.subtitle_style_native",exc)

    def _refresh_online_subtitle_style(self):
        self._persist_subtitle_style()
        if self._online_subtitle_active and self._online_subtitle_current_text:
            # Re-render the currently visible cue through the proven native path.
            self._set_online_subtitle_text(self._online_subtitle_current_text)
        else:
            self._apply_subtitle_style_native()

    def _open_subtitle_size_menu(self):
        current=max(30,min(56,int(self._subtitle_style.get("size",38) or 38)))
        presets=((_("Normal"),38),(_("Large"),44),(_("Larger"),50),(_("Extra Large"),56))
        choices=[(("✓ " if int(value)==current else "")+name,int(value)) for name,value in presets]
        selected=next((i for i,row in enumerate(choices) if int(row[1])==current),0)
        return self._subtitle_inline_show(choices,selected,self._subtitle_size_selected,self._subtitle_back_from_size,"size")

    def _subtitle_size_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_size()
        try:value=max(30,min(56,int(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else 38)))
        except Exception:return self._open_subtitle_settings_menu("style_size")
        self._subtitle_style["size"]=value;self._refresh_online_subtitle_style();self._open_subtitle_settings_menu("style_size")

    def _open_subtitle_color_menu(self):
        current=str(self._subtitle_style.get("color") or "#FFFFFF").upper()
        choices=[(("✓ " if value.upper()==current else "")+_(name),value) for name,value in SUBTITLE_STYLE_COLORS]
        selected=next((i for i,row in enumerate(choices) if str(row[1]).upper()==current),0)
        return self._subtitle_inline_show(choices,selected,self._subtitle_color_selected,self._subtitle_back_from_color,"color")

    def _subtitle_color_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_color()
        value=str(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice or "").upper()
        valid={v.upper() for _n,v in SUBTITLE_STYLE_COLORS}
        if value not in valid:return self._open_subtitle_color_menu()
        self._subtitle_style["color"]=value;self._refresh_online_subtitle_style();self._open_subtitle_settings_menu("style_color")

    def _open_subtitle_background_menu(self):
        enabled=bool(self._subtitle_style.get("background",False))
        choices=[(("✓ " if not enabled else "")+_("Off • text only"),False),(("✓ " if enabled else "")+_("Black • behind subtitle text"),True)]
        return self._subtitle_inline_show(choices,1 if enabled else 0,self._subtitle_background_selected,self._subtitle_back_from_background,"background")

    def _subtitle_background_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_background()
        self._subtitle_style["background"]=bool(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else False)
        self._refresh_online_subtitle_style();self._open_subtitle_settings_menu("style_background")

    def _open_subtitle_position_menu(self):
        current=int(self._subtitle_style.get("position",0) or 0)
        choices=[(("✓ " if int(offset)==current else "")+_(name),int(offset)) for name,offset in SUBTITLE_POSITION_PRESETS]
        selected=next((i for i,row in enumerate(choices) if int(row[1])==current),0)
        return self._subtitle_inline_show(choices,selected,self._subtitle_position_selected,self._subtitle_back_from_position,"position")

    def _subtitle_position_selected(self,choice=None):
        if not choice:return self._subtitle_back_from_position()
        try:value=int(choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else 0)
        except Exception:return self._open_subtitle_position_menu()
        self._subtitle_style["position"]=max(-260,min(260,value));self._refresh_online_subtitle_style();self._open_subtitle_settings_menu("style_position")

    def _disable_native_subtitles(self):
        try:
            service=self.session.nav.getCurrentService()
            subtitle=service.subtitle() if service else None
            if subtitle is not None:
                window=getattr(self,"subtitle_window",None)
                instance=getattr(window,"instance",None) if window is not None else None
                if instance is not None:
                    subtitle.disableSubtitles(instance)
                else:
                    try:subtitle.disableSubtitles()
                    except TypeError:pass
            window=getattr(self,"subtitle_window",None)
            if window is not None:window.hide()
        except Exception as exc:
            optional_failure("player.disable_native_subtitles",exc)

    def _disable_online_subtitles(self):
        self._online_subtitle_active=False
        self._online_subtitle_searching=False
        self._online_subtitle_generation += 1
        try:self._online_subtitle_cancel.set()
        except Exception:pass
        future=getattr(self,"_online_subtitle_future",None)
        if future is not None:
            try:future.cancel()
            except Exception:pass
        self._online_subtitle_future=None
        self._online_subtitle_cues=[]
        self._online_subtitle_index=0
        self._online_subtitle_path=""
        try:self.online_subtitle_timer.stop()
        except Exception:pass
        try:self.online_subtitle_result_timer.stop()
        except Exception:pass
        try:
            while True:self._online_subtitle_queue.get_nowait()
        except Exception:pass
        try:self._set_online_subtitle_text("")
        except Exception:pass

    def _start_online_subtitles(self, language="EN", provider="subdl"):
        language=str(language or "EN").strip().upper().replace("-","_")
        if not is_supported_subtitle_language(language):
            return
        language_name=subtitle_language_name(language)
        provider=str(provider or "subdl").strip().lower()
        if provider not in ("subdl","subsource"):provider="subdl"
        provider_name="SubSource" if provider=="subsource" else "SubDL"
        self._external_subtitle_stop()
        self._online_subtitle_provider=provider
        self._online_subtitle_language=language
        self._online_subtitle_language_name=language_name
        if self.media_type not in ("vod","series","episode"):
            self._subtitle_inline_finish()
            try:self.session.open(MessageBox,_("Online subtitles are available for Movies and Series."),MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass
            return
        if self._online_subtitle_searching:return
        settings_data=load_settings()
        key_name="subsource_api_key" if provider=="subsource" else "subdl_api_key"
        api_key=str(settings_data.get(key_name) or "").strip()
        if not api_key:
            self._subtitle_inline_finish()
            message=_("SubSource API key is not configured.") if provider=="subsource" else _("SubDL API key is not configured.")
            try:self.session.open(MessageBox,message,MessageBox.TYPE_ERROR,timeout=6)
            except Exception:pass
            return
        self._disable_native_subtitles()
        self._online_subtitle_searching=True
        self._online_subtitle_generation += 1
        generation=self._online_subtitle_generation
        try:self._online_subtitle_cancel.set()
        except Exception:pass
        self._online_subtitle_cancel=threading.Event()
        cancel_token=self._online_subtitle_cancel
        result_queue=self._online_subtitle_queue
        try:
            self["connection"].setText((_("SEARCHING  •  SUBSOURCE %s") if provider=="subsource" else _("SEARCHING  •  SUBDL %s"))%language_name.upper())
            self.show()
        except Exception:pass
        item=dict(self.item);media_type=self.media_type;name=self.name
        def worker():
            try:
                if provider=="subsource":
                    candidates,ident=search_subsource_language(api_key,item,media_type,name,language)
                else:
                    candidates,ident=search_language(api_key,item,media_type,name,language)
                if cancel_token.is_set():return
                result_queue.put(("choices",(candidates,ident,generation)))
            except Exception as exc:
                if cancel_token.is_set():return
                result_queue.put(("error",(str(exc),generation)))
        try:
            self._online_subtitle_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.online_subtitle_result_timer.start(250,False)
        except Exception as exc:
            self._online_subtitle_searching=False
            self._subtitle_inline_finish()
            cancel_token.set()
            future=getattr(self,"_online_subtitle_future",None)
            if future is not None:
                try:future.cancel()
                except Exception:pass
            self._online_subtitle_future=None
            optional_failure("player.subtitle_search_start",exc)
            try:self.session.open(MessageBox,_("Subtitle search failed: %s")%_("Unknown error"),MessageBox.TYPE_ERROR,timeout=7)
            except Exception:pass

    def _drain_online_subtitle_result(self):
        if self.restored or self._closing_playback:
            try:self.online_subtitle_result_timer.stop()
            except Exception:pass
            return
        while True:
            try:kind,payload=self._online_subtitle_queue.get_nowait()
            except queue.Empty:return
            generation=self._online_subtitle_generation
            if kind=="choices" and isinstance(payload,tuple) and len(payload)==3:
                candidates,ident,generation=payload
                if generation!=self._online_subtitle_generation:continue
                payload=(candidates,ident)
            elif kind=="error" and isinstance(payload,tuple) and len(payload)==2:
                message,generation=payload
                if generation!=self._online_subtitle_generation:continue
                payload=message
            elif kind=="downloaded" and isinstance(payload,tuple) and len(payload)==2:
                result,generation=payload
                if generation!=self._online_subtitle_generation:continue
                payload=result
            break
        self._online_subtitle_future=None
        self._online_subtitle_searching=False
        try:self.online_subtitle_result_timer.stop()
        except Exception:pass
        if kind=="error":
            raw_error=str(payload or "")
            optional_failure("player.online_subtitle_operation",RuntimeError(raw_error or "unknown subtitle error"))
            low=raw_error.lower()
            language_name=getattr(self,"_online_subtitle_language_name","English")
            if "no " in low and "subtitle" in low:
                payload=_("No %s subtitle found")%language_name
            elif "download" in low or "archive" in low or "extract" in low:
                payload=_("Subtitle download failed: %s")%_("Unknown error")
            else:
                payload=_("Subtitle search failed: %s")%_("Unknown error")
        if kind=="downloaded" and isinstance(payload,dict):
            try:
                self._activate_online_subtitle(str(payload.get("path") or ""),str(payload.get("release") or getattr(self,"_online_subtitle_language_name","English")))
                # Keep the subtitle workflow open after a successful choice so
                # the user can immediately adjust size/color/background/position.
                self.subtitleSelection("subtitle_settings")
                return
            except Exception as exc:
                optional_failure("player.subtitle_activate",exc)
                payload=_("Could not load subtitle: %s")%_("Unknown error")
        if kind=="choices":
            candidates,ident=payload
            self._online_subtitle_candidates=(candidates,ident)
            choices=[]
            for idx,c in enumerate(candidates):
                label=str(c.get("name") or (_("%s subtitle")%getattr(self,"_online_subtitle_language_name","English")))
                if c.get("cached_path"):label="★ "+label
                choices.append((label[:110],idx))
            if not choices:
                payload=_("No %s subtitle found")%getattr(self,"_online_subtitle_language_name","English")
            else:
                try:
                    self._subtitle_inline_show(choices,0,self._online_subtitle_choice_selected,self._subtitle_back_from_online_results,"online_results")
                    return
                except Exception as exc:
                    optional_failure("player.subtitle_results_open",exc)
                    payload=_("Could not open subtitle results: %s")%_("Unknown error")
        self._subtitle_inline_finish()
        try:
            self["connection"].setText(_("PLAYING  •  NO %s SUBTITLE")%getattr(self,"_online_subtitle_language_name","English").upper())
            self.session.open(MessageBox,str(payload or (_("No %s subtitle found")%getattr(self,"_online_subtitle_language_name","English"))),MessageBox.TYPE_INFO,timeout=8)
        except Exception:pass

    def _online_subtitle_choice_selected(self,choice=None):
        if not choice:
            language=str(getattr(self,"_online_subtitle_language","EN") or "EN").upper().replace("-","_")
            return self._open_online_subtitle_languages_menu(language)
        try:idx=int(choice[1])
        except Exception:return
        try:
            candidates,ident=self._online_subtitle_candidates
            candidate=candidates[idx]
        except Exception:return
        self._online_subtitle_searching=True
        self._online_subtitle_generation += 1
        generation=self._online_subtitle_generation
        try:self._online_subtitle_cancel.set()
        except Exception:pass
        self._online_subtitle_cancel=threading.Event()
        cancel_token=self._online_subtitle_cancel
        result_queue=self._online_subtitle_queue
        try:
            self["connection"].setText(_("DOWNLOADING  •  %s SUBTITLE")%getattr(self,"_online_subtitle_language_name","English").upper())
            self.show()
        except Exception:pass
        def worker():
            try:
                provider=str(getattr(self,"_online_subtitle_provider","subdl") or "subdl").lower()
                if provider=="subsource":
                    api_key=str(load_settings().get("subsource_api_key") or "").strip()
                    result=download_subsource_candidate(api_key,candidate,ident)
                else:
                    result=download_candidate(candidate,ident)
                if cancel_token.is_set():return
                result_queue.put(("downloaded",(result,generation)))
            except Exception as exc:
                if cancel_token.is_set():return
                result_queue.put(("error",(str(exc),generation)))
        try:
            self._online_subtitle_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.online_subtitle_result_timer.start(250,False)
        except Exception as exc:
            self._online_subtitle_searching=False
            cancel_token.set()
            future=getattr(self,"_online_subtitle_future",None)
            if future is not None:
                try:future.cancel()
                except Exception:pass
            self._online_subtitle_future=None
            optional_failure("player.subtitle_download_start",exc)
            try:self.session.open(MessageBox,_("Subtitle download failed: %s")%_("Unknown error"),MessageBox.TYPE_ERROR,timeout=7)
            except Exception:pass

    def _subtitle_sync_file(self):
        path=str(self._online_subtitle_path or "")
        return (path+".sync.json") if path else ""

    def _load_subtitle_sync(self):
        self._online_subtitle_offset_ms=0;self._online_subtitle_scale=1.0
        path=self._subtitle_sync_file()
        if not path:return
        try:
            with open(path,"r",encoding="utf-8") as h:data=json.load(h)
            self._online_subtitle_offset_ms=max(-300000,min(300000,int(data.get("offset_ms") or 0)))
            scale=float(data.get("scale") or 1.0)
            self._online_subtitle_scale=scale if 0.94<=scale<=1.06 else 1.0
        except Exception:pass

    def _save_subtitle_sync(self):
        path=self._subtitle_sync_file()
        if not path:return
        try:
            temp=path+".tmp"
            with open(temp,"w",encoding="utf-8") as h:
                json.dump({"offset_ms":int(self._online_subtitle_offset_ms),"scale":float(self._online_subtitle_scale)},h,separators=(",",":"))
                h.flush();os.fsync(h.fileno())
            os.replace(temp,path)
        except Exception as exc:optional_failure("player.subtitle_sync_save",exc)

    def _adjust_subtitle_delay(self,delta_ms):
        if not self._online_subtitle_active:return
        self._online_subtitle_offset_ms=max(-300000,min(300000,int(self._online_subtitle_offset_ms)+int(delta_ms)))
        self._save_subtitle_sync()
        try:
            self.session.open(MessageBox,_("Subtitle delay: %+.1f sec")%(self._online_subtitle_offset_ms/1000.0),MessageBox.TYPE_INFO,timeout=2)
        except Exception:pass

    def _auto_sync_subtitle(self):
        """Safe lightweight drift correction without an always-running AI model.

        Detect only the two common 23.976<->25fps timing drifts when evidence is
        strong. Constant offsets stay user-adjustable in 0.5s steps.
        """
        if not self._online_subtitle_active or not self._online_subtitle_cues:return
        try:
            service=self.session.nav.getCurrentService();seek=service.seek() if service else None
            length=seek.getLength() if seek else None
            duration_ms=int(length[1] or 0)//90 if length and not length[0] else 0
        except Exception:duration_ms=0
        last_ms=int(self._online_subtitle_cues[-1][1] or 0)
        if duration_ms<=20*60*1000 or last_ms<=15*60*1000:
            try:self.session.open(MessageBox,_("Auto Sync needs a stable movie duration. Use delay +/- for this subtitle."),MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass
            return
        ratio=float(duration_ms)/float(last_ms)
        candidates=(25.0/23.976,23.976/25.0)
        best=min(candidates,key=lambda x:abs(x-ratio))
        # Very conservative: only apply when measured ratio is close to a known
        # PAL/cinema drift and subtitle coverage reaches most of the movie.
        if abs(best-ratio)<=0.008 and last_ms>=int(duration_ms*0.88):
            self._online_subtitle_scale=float(best)
            self._save_subtitle_sync()
            try:self.session.open(MessageBox,_("Auto Sync applied • timing scale %.5f")%best,MessageBox.TYPE_INFO,timeout=4)
            except Exception:pass
        else:
            try:self.session.open(MessageBox,_("No safe automatic drift detected. Use subtitle delay +/- for this release."),MessageBox.TYPE_INFO,timeout=5)
            except Exception:pass

    def _activate_online_subtitle(self,path,release_name="Arabic"):
        cues=parse_srt(path)
        if not cues:
            raise RuntimeError("subtitle file contains no readable cues")
        # R46 ownership fence: no SubsSupport/Pro renderer may remain alive when
        # Ultra takes ownership for SubDL/SubSource, even if activation comes
        # from a path other than the normal source menu.
        self._external_subtitle_stop()
        self._disable_native_subtitles()
        if self._online_subtitle_display is None:
            self._init_online_subtitle_display()
        self._online_subtitle_path=path
        self._online_subtitle_cues=cues
        self._online_subtitle_index=0
        self._load_subtitle_sync()
        self._online_subtitle_active=True
        self._subtitle_user_override=True
        try:self["online_subtitle"].hide()
        except Exception:pass
        try:
            display=self._online_subtitle_display
            if display is not None:display.show()
        except Exception:pass
        self._set_online_subtitle_text("")
        try:self.online_subtitle_timer.stop();self.online_subtitle_timer.start(80,True)
        except Exception:pass
        try:self["connection"].setText(_("PLAYING  •  ARABIC SUBTITLES"))
        except Exception:pass
        return True

    def _online_subtitle_tick(self):
        if not self._online_subtitle_active or self.restored or self._closing_playback:
            try:self["online_subtitle"].setText("")
            except Exception:pass
            return
        try:
            service=self.session.nav.getCurrentService()
            seek=service.seek() if service else None
            pos=seek.getPlayPosition() if seek else None
            if not pos or pos[0]:
                try:self.online_subtitle_timer.start(140,True)
                except Exception:pass
                return
            ms=max(0,int(pos[1] or 0)//90)
            scale=float(self._online_subtitle_scale or 1.0)
            timeline_ms=max(0,int((ms-int(self._online_subtitle_offset_ms or 0))/scale))
        except Exception:
            try:self.online_subtitle_timer.start(140,True)
            except Exception:pass
            return
        ms=timeline_ms
        cues=self._online_subtitle_cues
        idx=max(0,min(int(self._online_subtitle_index or 0),max(0,len(cues)-1)))
        while idx<len(cues) and cues[idx][1] < ms:
            idx+=1
        while idx>0 and cues[idx-1][0] > ms:
            idx-=1
        self._online_subtitle_index=idx
        text=""
        if idx<len(cues):
            start,end,body=cues[idx]
            if start<=ms<=end:
                text=body
        try:self._set_online_subtitle_text(text)
        except Exception as exc:optional_failure("player.online_subtitle_tick",exc)
        # Wake near the next cue boundary instead of polling the decoder forever
        # every 180/300ms. This keeps subtitle timing responsive without making
        # Enigma2's main loop do constant seek() calls.
        try:
            wait=700
            if idx<len(cues):
                start,end,_body=cues[idx]
                boundary=end if start<=ms<=end else start
                wait=max(90,min(700,int(abs(boundary-ms))))
            self.online_subtitle_timer.start(wait,True)
        except Exception:pass

    def _enforce_default_subtitles_off(self):
        """Block cached/automatic subtitles until the user explicitly opens the selector."""
        if self.restored or self._subtitle_user_override:
            return
        try:
            # A truthy sentinel prevents InfoBarSubtitleSupport.__updatedInfo from
            # auto-enabling getCachedSubtitle() again on the next metadata event.
            self.selected_subtitle = (0, 0, 0, 0)
            service = self.session.nav.getCurrentService()
            subtitle = service.subtitle() if service else None
            if subtitle is not None:
                window = getattr(self, "subtitle_window", None)
                instance = getattr(window, "instance", None) if window is not None else None
                if instance is not None:
                    subtitle.disableSubtitles(instance)
                else:
                    try:
                        subtitle.disableSubtitles()
                    except TypeError:
                        pass
            window = getattr(self, "subtitle_window", None)
            if window is not None:
                window.hide()
        except Exception as exc:
            optional_failure("player.default_subtitles_off", exc)
        self._subtitle_disable_attempts += 1
        if self._subtitle_disable_attempts < 4 and not self._subtitle_user_override:
            try:
                self.subtitle_default_timer.start(900, True)
            except Exception as exc:
                optional_failure("player", exc)

    def _schedule_startup_guard(self, reason):
        """Debounce stale EOF/tune-failed events from ServiceApp/ExtePlayer.

        VOD and episodes sometimes decode a second of video and then emit a stale
        startup event for the previous engine/service. Never restart immediately;
        verify the active reference and advancing play position first.
        """
        if self.media_type not in ("vod", "series", "episode", "catchup"):
            return False
        self._early_eof_pending = True
        self._startup_guard_reason = str(reason or "startup interruption")
        self._startup_guard_checks = 0
        try:
            self.eof_guard_timer.stop()
            self.eof_guard_timer.start(1100, True)
            return True
        except Exception as exc:
            optional_failure("player", exc)
            return False

    def _category_label(self):
        return {
            "itv": _("LIVE"),
            "live": _("LIVE"),
            "vod": _("MOVIE"),
            "series": _("SERIES"),
            "episode": _("EPISODE"),
            "catchup": _("CATCH-UP"),
        }.get(self.media_type, self.media_type.upper()[:10])

    def playStream(self, servicetype, streamurl):
        runtime_breadcrumb("player_start_request",media_type=self.media_type,engine=int(servicetype or 0))
        if self.restored or self._closing_playback:
            return
        if not streamurl:
            self._final_failure("Empty stream URL")
            return

        try:
            service_value = int(servicetype)
        except Exception:
            service_value = 4097
        self.servicetype = service_value
        self._reset_vod_quality_probe()
        self._play_generation += 1
        self.started = False
        self.failed = False
        self._early_eof_pending = False
        self._startup_guard_checks = 0
        self._startup_guard_last_position = -1
        self._startup_guard_reason = ""
        self._startup_guard_until = time.time() + (8.0 if self.media_type in ("vod", "series", "episode", "catchup") else 0.0)
        self._video_guard_misses = 0
        self._video_guard_last_engine = int(service_value)
        self._engine_video_confirmed = False
        self._subtitle_user_override = False
        self._subtitle_disable_attempts = 0
        self._embedded_subtitle_probe_pending = False
        self._embedded_subtitle_probe_attempts = 0
        try:
            self.embedded_subtitle_probe_timer.stop()
        except Exception:
            pass
        try:
            self.subtitle_default_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        try:
            self.eof_guard_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        self.start_attempt = time.time()
        # R24: keep only the stable engine chip beside SUBTITLES.
        # The old floating engine/status text near the clock remains removed.
        try:self["engine"].setText(_engine_label(self.servicetype))
        except Exception:pass
        try:self["connection"].setText("")
        except Exception:pass
        self["extension"].setText(_("STREAM"))

        try:
            self.reference = eServiceReference(self.servicetype, 0, str(streamurl))
            self.reference.setName(self.name)
            try:self._owned_reference_string=self.reference.toString()
            except Exception:self._owned_reference_string=""
            # Replace the active service directly from the player screen. Stopping
            # the previous service first can emit a stale EOF and incorrectly trigger
            # the startup fallback path, so the new Enigma2 reference is played directly.
            result = self.session.nav.playService(self.reference)
            try: self._owned_external_pids.update(set(_matching_external_player_pids(self.streamurl))-set(self._external_player_baseline))
            except Exception as exc: optional_failure("player.capture_external",exc)
            if result is False:
                raise RuntimeError("service engine rejected the stream")
            self.startup_timer.stop()
            self.startup_timer.start(12000, True)
        except Exception as exc:
            self._try_next_engine("engine %s rejected stream: %s" % (self.servicetype, exc))

    def _service_started(self):
        if self.restored or self._closing_playback:
            return
        already_started = bool(self.started)
        self.started = True
        if not already_started or not self._watch_started_at:
            self._watch_started_at = time.time()
        try:
            self.startup_timer.stop()
        except Exception as exc:
            optional_failure("player", exc)
        self["connection"].setText("")
        if not getattr(self,"_retry_visual_hold",False):
            self._auto_retry_deferred_reason="";self._auto_retry_next_allowed_at=0.0
            try:self.auto_retry_delay_timer.stop()
            except Exception:pass
            try:self["retry_status"].setText("")
            except Exception:pass
        else:
            # evStart can fire before a retry candidate has real decoded playback.
            # Keep the line held. Live uses a short quiet guard; VOD/Series clear
            # only after the play position advances on the replacement service.
            try:self["retry_status"].show()
            except Exception:pass
            try:self.retry_visual_success_timer.stop()
            except Exception:pass
            if self.media_type in ("itv","live"):
                try:self.retry_visual_success_timer.start(1500, True)
                except Exception as exc:optional_failure("player.retry_visual_success_timer", exc)
            else:
                self._retry_visual_progress_last=-1
                self._retry_visual_progress_advances=0
        if self.media_type in ("itv","live"):
            try:self._refresh_live_epg()
            except Exception as exc:optional_failure("player.live_epg_started",exc)
        runtime_breadcrumb("player_started",media_type=self.media_type,engine=int(self.servicetype or 0))
        try:
            self._owned_external_pids.update(set(_all_external_player_pids())-set(self._external_player_baseline))
        except Exception as exc:optional_failure("player.capture_external_started",exc)
        self._last_network_activity = time.monotonic()
        try:self._schedule_subtitle_session_restore(850)
        except Exception as exc:optional_failure("player.subtitle_session_started_restore",exc)
        try:
            bridge=getattr(self,"_external_subtitle_bridge",None)
            if bridge is not None:bridge.service_started()
        except Exception as exc:optional_failure("player.external_subtitle_started",exc)
        try:
            self.memory_fuse_timer.stop();self.memory_fuse_timer.start(5000,False)
        except Exception as exc:optional_failure("player.silent_guard",exc)
        try:
            self.stable_timer.stop(); self.stable_timer.start(30000, True)
        except Exception as exc:
            optional_failure("player", exc)
        if not already_started:
            try:
                self.subtitle_default_timer.stop(); self.subtitle_default_timer.start(450, True)
            except Exception as exc:
                optional_failure("player", exc)
        # Last Viewed is intentionally separate from Resume. Touch recency as
        # soon as Enigma2 confirms real playback, while preserving any existing
        # resume point. Progress itself is still saved only after the 45s gate.
        if self.media_type in ("vod","series","episode","catchup"):
            try:
                profile=self._history_profile
                history_item=self._history_item
                touch_recently_played(profile,self.media_type,history_item)
            except Exception as exc:optional_failure("player",exc)
        elif self.media_type in ("itv","live"):
            # Home/Category launches already save the opening channel.  What used
            # to be missing was a channel selected later from inside the Player.
            # Commit that compact row only after Enigma2 confirms real playback.
            pending=getattr(self,"_pending_live_recent_item",None)
            if isinstance(pending,dict):
                try:
                    add_recently_played(self._history_profile,"itv",pending)
                    self._history_item=pending
                    self._pending_live_recent_item=None
                except Exception as exc:
                    optional_failure("player.live_recent_after_zap",exc)
        if self.media_type in ("vod","series","episode") and not already_started:
            self._arm_vod_quality_probe()
        self._update_stream_info()
        if self.media_type in ("vod", "series", "episode", "catchup"):
            try:
                self.progress_timer.stop(); self.progress_timer.start(self._cfg_progress_interval_ms, False)
                self.progress_visual_timer.stop(); self.progress_visual_timer.start(1000, False)
            except Exception as exc:
                optional_failure("player", exc)
            if not self.resume_prompted:
                self.resume_prompted=True

    def _suppress_service_events(self, seconds=1.25):
        self._event_suppress_until=max(float(getattr(self,"_event_suppress_until",0.0) or 0.0),time.time()+max(0.0,float(seconds or 0.0)))

    def _event_matches_current_reference(self):
        # stopService() intentionally emits EOF/TuneFailed on several OE-A images.
        # Ignore those stale events briefly; the startup watchdog still catches a
        # genuinely failed replacement service.
        if time.time() < float(getattr(self,"_event_suppress_until",0.0) or 0.0):
            return False
        try:
            current = self.session.nav.getCurrentlyPlayingServiceReference()
            if current is None or self.reference is None:
                return True
            return current.toString() == self.reference.toString()
        except Exception:
            return True

    def _service_failed(self):
        if self.restored or self._closing_playback:
            return
        if not self._event_matches_current_reference():
            return
        elapsed = time.time() - self.start_attempt if self.start_attempt else 999
        if self.media_type in ("vod", "series", "episode", "catchup") and elapsed < 8.0:
            if not getattr(self,"_retry_visual_hold",False):
                self["connection"].setText(_("PLAYING  •  verifying startup") if self.started else _("CONNECTING  •  verifying startup"))
            self._schedule_startup_guard("transient tune failed")
            return
        if self.started and self.media_type in ("itv", "live"):
            if self._handle_runtime_interruption("tune failed during playback"):
                return
        self._try_next_engine("tune failed")

    def _service_eof(self):
        if self.restored or self._closing_playback:
            return
        if not self._event_matches_current_reference():
            return
        if self._resume_target and time.time() < self._resume_grace_until:
            try:
                self.eof_guard_timer.stop(); self.eof_guard_timer.start(1800, True)
            except Exception as exc:
                optional_failure("player", exc)
            return
        elapsed = time.time() - self.start_attempt if self.start_attempt else 999
        if self.media_type in ("vod", "series", "episode", "catchup") and elapsed < 8.0:
            if not getattr(self,"_retry_visual_hold",False):
                self["connection"].setText(_("PLAYING  •  stabilizing stream") if self.started else _("CONNECTING  •  stabilizing stream"))
            self._schedule_startup_guard("transient early EOF")
            return
        if not self.started:
            self._try_next_engine("stream ended before playback started")
            return
        if self.media_type in ("itv", "live"):
            if self._handle_runtime_interruption("stream ended during playback"):
                return
        if self.media_type in ("vod", "series", "episode", "catchup"):
            # Treat EOF as a broken source when a known duration says the viewer
            # is still well before the configured completion threshold. Natural
            # end-of-title behavior remains untouched near the real end.
            try:
                position,duration=self._service_progress_snapshot()
                position=position or int(self._last_progress_position or 0)
                duration=duration or int(self._last_progress_duration or 0)
                completed=bool(duration and (position>=int(duration*self._cfg_completion_threshold) or max(0,duration-position)<=self._cfg_completion_remaining_pts))
            except Exception:
                position=duration=0;completed=False
            if duration>0 and position>0 and not completed:
                if self._begin_smart_recovery("unexpected EOF during playback"):
                    return
                self._final_failure("unexpected EOF during playback")
                return
            # Natural EOF is different from the user pressing BACK. Freeze the
            # periodic writer so it cannot overwrite the completed state while
            # the 10-second Next Episode prompt is on screen.
            try: self.progress_timer.stop()
            except Exception as exc: optional_failure("player", exc)
            self._save_history_progress(force=True, completed_override=True)
            next_item = self.item.get("_next_episode_item") if self.media_type == "episode" else None
            global NEXT_EPISODE_AUTOPLAY_SESSION
            if isinstance(next_item, dict) and next_item and NEXT_EPISODE_AUTOPLAY_SESSION:
                # Natural episode EOF: hand the next episode straight back to the
                # Series screen. Keep autoplay immediate and silent; no modal
                # countdown screen should interrupt the end credits.
                self._next_episode_prompted = True
                self._close_player({"next_episode": dict(next_item)}, save_progress=False)
                return
            self._close_player(save_progress=False)

    def _mark_stream_stable(self):
        if self.restored or not self.started:
            return
        self._runtime_reconnects = 0
        self._recovery_attempts = 0
        self._auto_retry_next_allowed_at = 0.0
        self._auto_retry_deferred_reason = ""
        try:self.auto_retry_delay_timer.stop()
        except Exception:pass
        self._retry_visual_clear(force=True)

    def _handle_runtime_interruption(self, reason):
        if self.restored or self._closing_playback:return False
        try:self.stable_timer.stop()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        try:self.retry_visual_success_timer.stop()
        except Exception:pass
        self.started=False
        return self._begin_smart_recovery(reason)


    def _verify_early_eof(self):
        if not self._early_eof_pending or self.restored or self.failed:
            return
        now = time.time()
        try:
            service = self.session.nav.getCurrentService()
            seekable = service.seek() if service else None
            pos = seekable.getPlayPosition() if seekable else None
            position = abs(int(pos[1])) if pos and not pos[0] else -1
        except Exception:
            position = -1
        ref_ok = self._event_matches_current_reference()
        previous = self._startup_guard_last_position
        self._startup_guard_last_position = position
        self._startup_guard_checks += 1
        # Advancing decoded position is definitive proof that playback survived
        # the stale event; keep the current engine and do not restart.
        if ref_ok and position >= 0 and previous >= 0 and position > previous + 9000:
            self._early_eof_pending = False
            self["connection"].setText("")
            return
        if ref_ok and self.started and position > 45000:
            self._early_eof_pending = False
            self["connection"].setText("")
            return
        # Never bounce engines during the protected startup window merely because
        # one probe cannot read a seek position yet. External players often expose
        # seek() a few seconds after video begins.
        if ref_ok and now < self._startup_guard_until and self._startup_guard_checks < 7:
            try:
                self.eof_guard_timer.start(1000, True)
            except Exception as exc:
                optional_failure("player", exc)
            return
        self._early_eof_pending = False
        self._try_next_engine(self._startup_guard_reason or "stream stopped during startup verification")

    def _startup_timeout(self):
        if self.restored or self._closing_playback:
            return
        if not self.started:
            self._try_next_engine("startup timeout")

    def _try_next_engine(self, reason):
        if self.restored or self._closing_playback or self.failed:return
        try:self.startup_timer.stop()
        except Exception as exc:optional_failure("player.silent_guard",exc)
        # Refresh the provider link before declaring the source unavailable.
        if self._begin_smart_recovery(reason):return
        self._final_failure(reason)


    def _retry_visual_set(self, text):
        """Hold one retry status line steady while only its text changes."""
        first = not bool(getattr(self,"_retry_visual_hold",False))
        self._retry_visual_hold = True
        if first:
            self._retry_visual_progress_last=-1
            self._retry_visual_progress_advances=0
        try:
            self["retry_status"].setText(str(text or ""))
            self["retry_status"].show()
        except Exception:
            pass
        if first:
            try: UltraInfobarVisibility.lockShow(self)
            except Exception: pass
            try: self.doShow()
            except Exception: pass

    def _retry_visual_clear(self, force=False):
        """Release the held retry line once real playback is confirmed."""
        try:self.retry_visual_success_timer.stop()
        except Exception:pass
        if not force and not bool(getattr(self,"_retry_visual_hold",False)):
            return
        was_held = bool(getattr(self,"_retry_visual_hold",False))
        self._retry_visual_hold = False
        self._retry_visual_progress_last=-1
        self._retry_visual_progress_advances=0
        try:self["retry_status"].setText("")
        except Exception:pass
        if was_held:
            try: UltraInfobarVisibility.unlockShow(self)
            except Exception: pass

    def _confirm_retry_visual_success(self):
        """Release a held retry line only after the retry candidate stays alive."""
        if self.restored or self._closing_playback:
            return
        if not getattr(self,"_retry_visual_hold",False):
            return
        if self.media_type not in ("itv","live"):
            return
        if not self.started:
            return
        if getattr(self,"_auto_retry_inflight",False):
            return
        if str(getattr(self,"_auto_retry_deferred_reason","") or ""):
            return
        self._retry_visual_clear(force=True)

    def _retry_visual_note_vod_progress(self, position):
        """Confirm a VOD/episode retry only from real advancing playback.

        Two advancing one-second samples on the current replacement reference are
        stronger evidence than evStart/resolution metadata and avoid visual
        hide/show loops on ServiceApp/exteplayer handoffs.
        """
        if self.media_type not in ("vod","series","episode","catchup"):
            return
        if not getattr(self,"_retry_visual_hold",False) or not self.started:
            return
        if getattr(self,"_auto_retry_inflight",False) or str(getattr(self,"_auto_retry_deferred_reason","") or ""):
            return
        try:
            if not self._event_matches_current_reference():
                return
        except Exception:
            return
        try:p=max(0,int(position or 0))
        except Exception:return
        if p<=0:return
        last=int(getattr(self,"_retry_visual_progress_last",-1) or -1)
        if last>=0 and p>last+18000:
            self._retry_visual_progress_advances=int(getattr(self,"_retry_visual_progress_advances",0) or 0)+1
        elif last>=0 and p<last:
            self._retry_visual_progress_advances=0
        self._retry_visual_progress_last=p
        if int(getattr(self,"_retry_visual_progress_advances",0) or 0)>=2:
            self._retry_visual_clear(force=True)

    def _run_deferred_auto_retry(self):
        if self.restored or self._closing_playback or self.started:
            self._auto_retry_deferred_reason = ""
            return
        reason = str(self._auto_retry_deferred_reason or self._auto_retry_reason or "retry failed")
        self._auto_retry_deferred_reason = ""
        self._begin_smart_recovery(reason)

    def _begin_smart_recovery(self, reason):
        """Resolve a fresh provider link and retry at most three times."""
        if self.restored or self._closing_playback or self._auto_retry_inflight:return False
        if self.media_type not in ("itv","live","vod","series","episode"):return False
        # Do not flash through 1/3, 2/3, 3/3 when a dead provider fails
        # immediately. Keep the previous attempt on-screen for at least two
        # seconds before starting the next fresh-link attempt.
        now = time.monotonic()
        if int(self._recovery_attempts or 0)>0 and now < float(self._auto_retry_next_allowed_at or 0.0):
            self._auto_retry_deferred_reason = str(reason or "retry failed")
            remaining = max(0.05, float(self._auto_retry_next_allowed_at) - now)
            try:
                self.auto_retry_delay_timer.stop()
                self.auto_retry_delay_timer.start(int(remaining * 1000.0), True)
            except Exception as exc:
                optional_failure("player.auto_retry_delay", exc)
            return True
        if int(self._recovery_attempts or 0)>=3:
            self._retry_visual_set(_("Stream unavailable"))
            self.failed=True
            runtime_breadcrumb("player_auto_retry_exhausted",media_type=self.media_type,reason=str(reason or ""))
            return True
        client=(self.item.get("_live_client_ref") or self.item.get("_player_client_ref")) if isinstance(self.item,dict) else None
        if client is None or not hasattr(client,"create_link"):return False
        try:self.retry_visual_success_timer.stop()
        except Exception:pass
        self._recovery_attempts=int(self._recovery_attempts or 0)+1
        attempt=self._recovery_attempts
        self._auto_retry_inflight=True;self._auto_retry_generation+=1;generation=self._auto_retry_generation
        self._auto_retry_reason=str(reason or "")
        self._auto_retry_next_allowed_at=time.monotonic()+float(self._auto_retry_min_interval or 2.0)
        self._retry_visual_set(_("Retrying stream • %d/3")%attempt)
        try:self["connection"].setText("")
        except Exception:pass
        item=dict(self._history_item if isinstance(self._history_item,dict) else self.item)
        media=self.media_type
        q=self._recovery_queue
        def worker():
            try:
                if media in ("itv","live"):
                    url=client.create_link(item,"itv")
                elif media=="episode":
                    sid=item.get("_series_id") or item.get("series_id") or self.item.get("_series_id") or self.item.get("series_id")
                    url=client.create_link(item,"series",sid)
                elif media=="series":
                    sid=item.get("series_id") or item.get("id")
                    url=client.create_link(item,"series",sid)
                else:
                    url=client.create_link(item,"vod")
                q.put((generation,str(url or ""),None))
            except Exception as exc:q.put((generation,"",exc))
        try:
            self._auto_retry_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.recovery_timer.stop();self.recovery_timer.start(100,False)
            runtime_breadcrumb("player_auto_retry",media_type=media,attempt=attempt,reason=self._auto_retry_reason)
            return True
        except Exception as exc:
            self._auto_retry_inflight=False;optional_failure("player.auto_retry_start",exc);return False

    def _cancel_smart_recovery(self):
        self._recovery_inflight=False;self._auto_retry_inflight=False;self._auto_retry_generation+=1
        self._auto_retry_deferred_reason="";self._auto_retry_next_allowed_at=0.0
        try:self.auto_retry_delay_timer.stop()
        except Exception:pass
        future=getattr(self,"_auto_retry_future",None)
        if future is not None:
            try:future.cancel()
            except Exception:pass
        self._auto_retry_future=None
        if self.restored or self._closing_playback:
            self._retry_visual_clear(force=True)

    def _drain_recovery_result(self):
        if self.restored or self._closing_playback:
            try:self.recovery_timer.stop()
            except Exception:pass
            return
        try:generation,url,error=self._recovery_queue.get_nowait()
        except queue.Empty:return
        try:self.recovery_timer.stop()
        except Exception:pass
        if generation!=self._auto_retry_generation:return
        self._auto_retry_inflight=False;self._auto_retry_future=None
        if error or not url:
            if int(self._recovery_attempts or 0)<3:
                self._begin_smart_recovery(str(error or self._auto_retry_reason or "retry failed"));return
            self._retry_visual_set(_("Stream unavailable"))
            self.failed=True;return
        old_url=str(self.streamurl or "")
        self.streamurl=url
        self._suppress_service_events(0.8)
        try:force_session_silence(self.session,old_url,force=True,stop_native=True,exclude_pids=self._external_player_baseline,owned_pids=self._owned_external_pids)
        except Exception as exc:optional_failure("player.auto_retry_stop_old",exc)
        self._owned_external_pids.clear()
        self._retry_visual_progress_last=-1
        self._retry_visual_progress_advances=0
        self.playStream(self.servicetype,url)

    def _final_failure(self, reason):
        if self.failed:
            return
        if int(getattr(self,"_recovery_attempts",0) or 0)>=3:
            self.failed=True
            self._retry_visual_set(_("Stream unavailable"))
            try:self["connection"].setText("")
            except Exception:pass
            return
        self.failed = True
        self["connection"].setText(_("PLAYBACK FAILED"))
        text = _("The stream could not start with the configured Enigma2 engine.\n\n%s\n\nTried: %s") % (
            reason,
            ", ".join(str(x) for x in self.engines),
        )
        try:
            self.session.openWithCallback(lambda answer=None: self.back(), MessageBox, text, MessageBox.TYPE_ERROR, timeout=10)
        except Exception:
            self.back()

    def restartStream(self):
        self._save_history_progress()
        self.playStream(self.servicetype, self.streamurl)

    @staticmethod
    def _server_search_norm(value):
        return " ".join(str(value or "").casefold().replace("_"," ").replace("-"," ").split())

    @staticmethod
    def _server_search_number(value):
        match=re.search(r"\d+",str(value or ""))
        try:return int(match.group(0)) if match else 0
        except Exception:return 0

    @staticmethod
    def _server_search_profile_key(profile):
        profile=profile if isinstance(profile,dict) else {}
        return (str(profile.get("portal") or "").rstrip("/").lower(),str(profile.get("mac") or "").upper())

    def _server_search_current_profile(self):
        profile={
            "portal":str((self.item or {}).get("_portal") or ""),
            "mac":str((self.item or {}).get("_mac") or ""),
            "name":str((self.item or {}).get("_server_name") or ""),
            "source_type":str((self.item or {}).get("_source_type") or ""),
            "allow_http_fallback":bool((self.item or {}).get("_allow_http_fallback",False)),
            "tls_mode":str((self.item or {}).get("_tls_mode") or "auto"),
            "device_profile":str((self.item or {}).get("_device_profile") or "auto"),
            "tls_fallback_accepted":bool((self.item or {}).get("_tls_fallback_accepted",False)),
            "http_fallback_accepted":bool((self.item or {}).get("_http_fallback_accepted",False)),
        }
        if profile.get("portal"):
            try:
                wanted=self._server_search_profile_key(profile)
                for idx,row in enumerate(load_profiles() or []):
                    if isinstance(row,dict) and self._server_search_profile_key(row)==wanted:
                        profile.update(dict(row));profile["_display_index"]=int(idx);break
            except Exception:pass
        return profile

    def _server_search_raw_title(self):
        item=self.item if isinstance(self.item,dict) else {}
        if self.media_type=="episode":
            for key in ("_raw_series_title","_series_title","series_title","series_name"):
                value=str(item.get(key) or "").strip()
                if value:return value
            parent=item.get("_quality_parent_item")
            if isinstance(parent,dict):
                for key in ("_raw_name","name","title"):
                    value=str(parent.get(key) or "").strip()
                    if value:return value
        for key in ("_raw_name","name","title"):
            value=str(item.get(key) or "").strip()
            if value:return value
        return str(self.name or "").strip()

    @staticmethod
    def _server_search_source_type(profile):
        profile=profile if isinstance(profile,dict) else {}
        source_type=str(profile.get("source_type") or "").strip().lower()
        if source_type:return source_type
        portal=str(profile.get("portal") or "").lower()
        if ".m3u" in portal or "type=m3u" in portal or "output=m3u" in portal or ("get.php" in portal and ("username=" in portal or "password=" in portal)):
            return "m3u"
        return "stalker"

    @classmethod
    def _server_search_source_name(cls,profile,pos=0):
        profile=profile if isinstance(profile,dict) else {}
        name=str(profile.get("name") or "").strip()
        if name:return name
        try:display_index=int(profile.get("_display_index"))
        except Exception:display_index=int(pos or 0)
        return (("Xtream %d" if cls._server_search_source_type(profile)=="m3u" else "Portal %d")%(display_index+1))

    @classmethod
    def _player_server_label(cls,profile,pos=0):
        """Stable Player-only label: source type + global configured slot.

        Custom profile names remain available to Search/manager screens, but the
        Player deliberately shows only Portal N / Xtream N so long user labels
        never spill into playback metadata.
        """
        profile=profile if isinstance(profile,dict) else {}
        try:display_index=int(profile.get("_display_index"))
        except Exception:display_index=int(pos or 0)
        kind="Xtream" if cls._server_search_source_type(profile)=="m3u" else "Portal"
        return "%s %d"%(kind,display_index+1)

    @classmethod
    def _server_search_new_client(cls,profile,timeout=5):
        profile=dict(profile or {})
        source_type=cls._server_search_source_type(profile)
        if source_type=="m3u":
            from ..m3u_adapter import M3UClient
            return M3UClient(profile.get("portal"),profile.get("mac") or "M3U",timeout=max(3,int(timeout or 5)))
        from ..client import StalkerClient
        return StalkerClient(
            profile.get("portal"),profile.get("mac"),timeout=max(3,int(timeout or 5)),
            allow_http_fallback=profile.get("allow_http_fallback",False),
            tls_mode=profile.get("tls_mode","auto"),device_profile=profile.get("device_profile","auto"),
            allow_tls_fallback=profile.get("tls_fallback_accepted",False),
            http_fallback_accepted=profile.get("http_fallback_accepted",False),
        )

    def _server_search_begin_hold(self):
        self._server_search_mode_active=True
        if not self._server_search_locked:
            self._server_search_locked=True
            try:UltraInfobarVisibility.lockShow(self)
            except Exception as exc:optional_failure("player.server_search_lock",exc)
        try:self.hideTimer.stop()
        except Exception:pass
        try:UltraInfobarVisibility.doShow(self)
        except Exception:pass

    def _server_search_end_hold(self,hide_infobar=False):
        overlay=getattr(self,"_server_search_inline_overlay",None)
        try:
            if overlay is not None and overlay.active:overlay.hide()
        except Exception as exc:optional_failure("player.server_search_hide",exc)
        self._server_search_mode_active=False
        if self._server_search_locked:
            self._server_search_locked=False
            try:UltraInfobarVisibility.unlockShow(self)
            except Exception as exc:optional_failure("player.server_search_unlock",exc)
        try:self["player_actions"].setEnabled(True);self["hard_exit_actions"].setEnabled(True)
        except Exception:pass
        if hide_infobar:
            try:self.hideTimer.stop()
            except Exception:pass
            try:
                self._us_infobar_visible=False
                Screen.hide(self)
            except Exception:pass
        else:
            try:UltraInfobarVisibility.doShow(self)
            except Exception:pass

    def _server_search_overlay_close(self):
        self._server_search_end_hold(hide_infobar=True)

    def _server_search_overlay_show(self,choices,selection=0,on_accept=None):
        self._server_search_begin_hold()
        frames=dict(getattr(self,"_player_chrome_frames",{}) or {})
        source=str(getattr(self,"_player_chrome_source","") or "")
        if not (source and os.path.isfile(source)):
            try:source=str(_cached_poster(self.item,self.name,self.media_type) or "")
            except Exception:source=""
        if not (source and os.path.isfile(source)):
            candidate=str(frames.get("main") or "")
            if candidate and os.path.isfile(candidate):source=candidate
        try:self["player_actions"].setEnabled(False);self["hard_exit_actions"].setEnabled(False)
        except Exception:pass
        return self._server_search_inline_overlay.show(
            choices,selection=selection,center_x=960,anchor_bottom=780,
            on_accept=on_accept,on_close=self._server_search_overlay_close,
            min_card_w=220,max_card_w=560,padding=38,
            visual_style="category",row_h=70,max_visible=5,
            adaptive_source=source,adaptive_accent=frames.get("accent"),
            adaptive_accent_soft=frames.get("accent_soft"),
        )

    def serverSearch(self):
        """BLUE: explicit raw-title server search for Live, Movies and Series episodes."""
        if self.media_type not in ("itv","live","vod","series","episode"):
            try:self["connection"].setText(_("Search"));UltraInfobarVisibility.doShow(self)
            except Exception:pass
            return False
        # If a search workflow is already active, BLUE simply re-opens the cached
        # server list. No second network scan is started.
        if self._server_search_mode_active and self._server_search_results:
            return self._server_search_show_results()
        raw=self._server_search_raw_title()
        if not raw:
            try:self["connection"].setText(_("No matching content"));UltraInfobarVisibility.doShow(self)
            except Exception:pass
            return False
        self._server_search_query=raw
        # If the subtitle picker is open, close only that picker first. Search
        # then owns the same InfoBar region and the same modal input grammar.
        try:
            if getattr(self,"_subtitle_inline_overlay",None) is not None and self._subtitle_inline_overlay.active:
                self._subtitle_inline_finish()
        except Exception:pass
        label=_("Search")+"  •  "+raw
        return self._server_search_overlay_show([(label,"search")],0,self._server_search_hub_selected)

    def _server_search_hub_selected(self,choice=None):
        action=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if action!="search":
            return self._server_search_overlay_close()
        return self._server_search_start()

    def _server_search_candidate_score(self,row,query):
        row=row if isinstance(row,dict) else {}
        title=str(row.get("name") or row.get("title") or "").strip()
        q=self._server_search_norm(query);t=self._server_search_norm(title)
        if not q or not t:return 99
        if t==q:return 0
        if q in t:return 1
        if t in q:return 2
        return 99

    def _server_search_start(self):
        self._server_search_begin_hold()
        try:
            if self._server_search_inline_overlay.active:self._server_search_inline_overlay.hide()
        except Exception:pass
        try:self["player_actions"].setEnabled(True);self["hard_exit_actions"].setEnabled(True)
        except Exception:pass
        self._server_search_generation+=1
        generation=self._server_search_generation
        try:self._server_search_cancel.set()
        except Exception:pass
        self._server_search_cancel=threading.Event()
        cancel_event=self._server_search_cancel
        query=str(self._server_search_query or self._server_search_raw_title() or "").strip()
        self._server_search_results=[]
        self._server_search_last_index=0
        try:self["connection"].setText(_("Searching...") );UltraInfobarVisibility.doShow(self)
        except Exception:pass
        media_types=("series",) if self.media_type=="episode" else (("itv",) if self.media_type in ("itv","live") else ("vod",))
        current_profile=self._server_search_current_profile()
        current_key=self._server_search_profile_key(current_profile)
        q=self._server_search_queue
        settings=load_settings()

        def worker():
            profiles=[]
            for _idx,_row in enumerate(load_profiles() or []):
                if not isinstance(_row,dict):continue
                _p=dict(_row);_p.setdefault("_display_index",int(_idx));profiles.append(_p)
            if current_profile.get("portal") and current_key not in [self._server_search_profile_key(p) for p in profiles]:
                current_copy=dict(current_profile);current_copy.setdefault("_display_index",int(len(profiles)));profiles.insert(0,current_copy)
            def pri(p):
                if self._server_search_profile_key(p)==current_key:return (0,0)
                return (1 if self._server_search_source_type(p)=="m3u" else 2,0)
            profiles=sorted(profiles,key=pri)
            jobs=queue.Queue()
            for pos,p in enumerate(profiles):jobs.put((pos,p))
            found=[];found_lock=threading.RLock();stop=threading.Event();deadline=time.monotonic()+28.0

            def one_source(pos,profile):
                if cancel_event.is_set() or stop.is_set():return
                client=None
                try:
                    source_type=self._server_search_source_type(profile)
                    client=self._server_search_new_client(profile,timeout=min(5,int(settings.get("timeout",10) or 10)))
                    budget=14.0 if source_type=="m3u" else (9.0 if self._server_search_profile_key(profile)==current_key else 6.0)
                    pages=60 if source_type=="m3u" else (24 if self._server_search_profile_key(profile)==current_key else 16)
                    local_stop=threading.Event()
                    class _SourceCancel(object):
                        def is_set(_self):
                            return bool(cancel_event.is_set() or stop.is_set() or local_stop.is_set())
                    source_cancel=_SourceCancel()
                    best=[99,None]
                    published=[99]
                    def publish_candidate(row,score):
                        if not isinstance(row,dict) or score>=99:return
                        if score>published[0]:return
                        record={
                            "profile":dict(profile),
                            "name":self._server_search_source_name(profile,pos),
                            "item":dict(row),
                            "copies":[dict(row)],
                            "source_type":source_type,
                            "position":int(pos),
                            "score":int(score),
                        }
                        with found_lock:
                            key=self._server_search_profile_key(profile)
                            replaced=False
                            for idx,old in enumerate(found):
                                if self._server_search_profile_key(old.get("profile") or {})==key:
                                    if int(old.get("score",99))>=int(score):found[idx]=record
                                    replaced=True;break
                            if not replaced:found.append(record)
                        published[0]=int(score)
                        q.put(("search_update",generation,record,None))
                    def on_partial(rows):
                        for row in rows or []:
                            if not isinstance(row,dict):continue
                            score=self._server_search_candidate_score(row,query)
                            if score<best[0]:best[0]=score;best[1]=dict(row)
                            # Publish an exact/decorated raw-title hit immediately.
                            if score<=1:publish_candidate(row,score)
                            # Exact raw identity is authoritative; stop scanning this
                            # source while the remaining servers continue in parallel.
                            if score==0:local_stop.set();break
                    rows=client.search_content_fast(query,media_types=media_types,limit=24,max_pages=pages,cancel_event=source_cancel,time_budget=budget,on_partial=on_partial) or []
                    ranked=[]
                    for row in rows:
                        if not isinstance(row,dict):continue
                        score=self._server_search_candidate_score(row,query)
                        if score<99:ranked.append((score,row))
                    if best[1] is not None:ranked.append((best[0],best[1]))
                    if not ranked:return
                    ranked.sort(key=lambda x:x[0])
                    score,row=ranked[0]
                    record={
                        "profile":dict(profile),
                        "name":self._server_search_source_name(profile,pos),
                        "item":dict(row),
                        "copies":[dict(x[1]) for x in ranked[:8]],
                        "source_type":source_type,
                        "position":int(pos),
                        "score":int(score),
                    }
                    publish_candidate(row,score)
                except Exception as exc:
                    optional_failure("player.server_search_source",exc)
                finally:
                    if client is not None:
                        try:client.close()
                        except Exception:pass

            def run_jobs():
                while not stop.is_set() and not cancel_event.is_set() and time.monotonic()<deadline:
                    try:pos,profile=jobs.get_nowait()
                    except queue.Empty:return
                    one_source(pos,profile)

            threads=[]
            for idx in range(min(6,jobs.qsize())):
                t=threading.Thread(target=run_jobs,name="ultrastalker-player-search-%d"%idx,daemon=True);t.start();threads.append(t)
            while time.monotonic()<deadline and not cancel_event.is_set():
                if not any(t.is_alive() for t in threads):break
                time.sleep(0.05)
            stop.set()
            for t in threads:
                try:t.join(0.2)
                except Exception:pass
            # One row per configured source, stable in configured order.
            unique={}
            for record in found:
                key=self._server_search_profile_key(record.get("profile") or {})
                if key not in unique:unique[key]=record
            result=sorted(unique.values(),key=lambda r:int(r.get("position",999)))
            q.put(("search",generation,result,None))

        try:
            self._server_search_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.server_search_timer.stop();self.server_search_timer.start(100,False)
            return True
        except Exception as exc:
            optional_failure("player.server_search_start",exc)
            try:self["connection"].setText(_("Unknown error"))
            except Exception:pass
            return False

    def _server_search_show_results(self):
        results=list(self._server_search_results or [])
        overlay=getattr(self,"_server_search_inline_overlay",None)
        if not results:
            try:self["connection"].setText(_("No matching content"))
            except Exception:pass
            raw=str(self._server_search_query or self._server_search_raw_title() or "")
            return self._server_search_overlay_show([(_("Search")+"  •  "+raw,"search")],0,self._server_search_hub_selected)
        choices=[(str(r.get("name") or _("Portal server")),r) for r in results]
        try:
            if overlay is not None and overlay.active:
                live_idx=int(overlay.selected_index())
                if live_idx>=0:self._server_search_last_index=live_idx
        except Exception:pass
        idx=max(0,min(int(self._server_search_last_index or 0),len(choices)-1))
        return self._server_search_overlay_show(choices,idx,self._server_search_server_selected)

    def _server_search_server_selected(self,choice=None):
        if not choice:return self._server_search_show_results()
        try:record=choice[1]
        except Exception:return self._server_search_show_results()
        if not isinstance(record,dict):return self._server_search_show_results()
        try:
            idx=[self._server_search_profile_key(x.get("profile") or {}) for x in self._server_search_results].index(self._server_search_profile_key(record.get("profile") or {}))
            self._server_search_last_index=idx
        except Exception:pass
        self._server_search_begin_hold()
        try:self["player_actions"].setEnabled(True);self["hard_exit_actions"].setEnabled(True)
        except Exception:pass
        name=str(record.get("name") or _("Portal server"))
        try:self["connection"].setText(_("Opening player: %s") % name);UltraInfobarVisibility.doShow(self)
        except Exception:pass
        self._server_search_generation+=1
        generation=self._server_search_generation
        try:self._server_search_cancel.set()
        except Exception:pass
        self._server_search_cancel=threading.Event();cancel_event=self._server_search_cancel
        q=self._server_search_queue
        target_season=self._server_search_number((self.item or {}).get("_season_number") or (self.item or {}).get("season_number") or (self.item or {}).get("season"))
        target_episode=self._server_search_number((self.item or {}).get("_episode_number") or (self.item or {}).get("episode_number") or (self.item or {}).get("episode") or (self.item or {}).get("number"))
        query=str(self._server_search_query or self._server_search_raw_title() or "")

        def resolve_worker():
            client=None
            try:
                profile=dict(record.get("profile") or {})
                client=self._server_search_new_client(profile,timeout=7)
                source_item=dict(record.get("item") or {})
                history_item=dict(source_item)
                if self.media_type=="episode":
                    if not target_season or not target_episode:raise RuntimeError("Episode identity unavailable")
                    seasons=client.series_seasons(source_item,cancel_event=cancel_event) or []
                    season_item=None
                    for row in seasons:
                        if not isinstance(row,dict):continue
                        n=self._server_search_number(row.get("season_number") or row.get("season") or row.get("name") or row.get("title") or row.get("id"))
                        if n==target_season:season_item=dict(row);break
                    if season_item is None:raise RuntimeError("Season %d unavailable"%target_season)
                    episodes=client.series_episodes(source_item,season_item,cancel_event=cancel_event) or []
                    episode_item=None
                    for row in episodes:
                        if not isinstance(row,dict):continue
                        n=self._server_search_number(row.get("episode_number") or row.get("episode") or row.get("number") or row.get("name") or row.get("title") or row.get("id"))
                        if n==target_episode:episode_item=dict(row);break
                    if episode_item is None:raise RuntimeError("Episode %d unavailable"%target_episode)
                    episode_id=episode_item.get("id") or episode_item.get("episode_id") or episode_item.get("series") or episode_item.get("number")
                    url=client.create_link(episode_item,"series",episode_id,cancel_event=cancel_event)
                    history_item=dict(episode_item)
                    sid=source_item.get("series_id") or source_item.get("id") or source_item.get("series_uid")
                    if sid not in (None,""):history_item["_series_id"]=sid
                    history_item["_series_title"]=query;history_item["_raw_series_title"]=query
                    history_item["_season_number"]=target_season;history_item["_episode_number"]=target_episode
                elif self.media_type in ("itv","live"):
                    url=client.create_link(source_item,"itv",cancel_event=cancel_event)
                    history_item=dict(source_item)
                else:
                    url=client.create_link(source_item,"vod",cancel_event=cancel_event)
                url=str(url or "").strip()
                if not url:raise RuntimeError("Empty stream URL")
                q.put(("switch",generation,{"url":url,"client":client,"profile":profile,"history_item":history_item,"server_name":name},None))
                client=None
            except Exception as exc:
                q.put(("switch",generation,None,exc))
            finally:
                if client is not None:
                    try:client.close()
                    except Exception:pass

        try:
            self._save_history_progress(force=True)
        except Exception:pass
        try:
            self._server_search_future=_PLAYER_BG_EXECUTOR.submit(resolve_worker)
            self.server_search_timer.stop();self.server_search_timer.start(100,False)
            return True
        except Exception as exc:
            optional_failure("player.server_switch_start",exc)
            try:self["connection"].setText(_("Unknown error")+"  •  "+name)
            except Exception:pass
            return self._server_search_show_results()

    def _drain_server_search_result(self):
        if self.restored or self._closing_playback:
            try:self.server_search_timer.stop()
            except Exception:pass
            return
        processed=False
        while True:
            try:kind,generation,payload,error=self._server_search_queue.get_nowait()
            except queue.Empty:break
            except Exception:break
            processed=True
            if int(generation or 0)!=int(self._server_search_generation or 0):
                # A late successful switch owns an isolated client. Close it if
                # the user already started another search/switch generation.
                if kind=="switch" and isinstance(payload,dict):
                    stale=payload.get("client")
                    if stale is not None:
                        try:stale.close()
                        except Exception:pass
                continue
            if kind=="search_update":
                record=dict(payload or {}) if isinstance(payload,dict) else {}
                if record:
                    first_result=not bool(self._server_search_results)
                    key=self._server_search_profile_key(record.get("profile") or {})
                    merged=[];replaced=False
                    for old in list(self._server_search_results or []):
                        if self._server_search_profile_key(old.get("profile") or {})==key:
                            if int(record.get("score",99))<=int(old.get("score",99)):merged.append(record)
                            else:merged.append(old)
                            replaced=True
                        else:merged.append(old)
                    if not replaced:merged.append(record)
                    self._server_search_results=sorted(merged,key=lambda r:int(r.get("position",999)))
                    try:self["connection"].setText(_("Searching...")+"  •  %d"%len(self._server_search_results))
                    except Exception:pass
                    # Keep the visible server list live: first paint appears
                    # immediately, then later sources are appended/refined while
                    # preserving the user's current selection row.
                    if first_result or (getattr(self,"_server_search_inline_overlay",None) is not None and self._server_search_inline_overlay.active):
                        self._server_search_show_results()
            elif kind=="search":
                self._server_search_future=None
                self._server_search_results=list(payload or [])
                if self._server_search_results:
                    try:self["connection"].setText(_("Done")+"  •  %d"%len(self._server_search_results))
                    except Exception:pass
                    self._server_search_show_results()
                else:
                    try:self["connection"].setText(_("No matching content"))
                    except Exception:pass
                    self._server_search_show_results()
            elif kind=="switch":
                self._server_search_future=None
                if error or not isinstance(payload,dict):
                    name=""
                    try:
                        if 0<=self._server_search_last_index<len(self._server_search_results):name=str(self._server_search_results[self._server_search_last_index].get("name") or "")
                    except Exception:pass
                    try:self["connection"].setText(_("Unknown error")+("  •  "+name if name else ""))
                    except Exception:pass
                    self._server_search_show_results()
                    continue
                new_client=payload.get("client")
                old_owned=getattr(self,"_server_search_owned_client",None)
                if old_owned is not None and old_owned is not new_client:
                    try:old_owned.close()
                    except Exception:pass
                self._server_search_owned_client=new_client
                profile=dict(payload.get("profile") or {})
                history_item=dict(payload.get("history_item") or {})
                self._history_profile={"portal":str(profile.get("portal") or ""),"mac":str(profile.get("mac") or "")}
                self._history_item=history_item
                self.item["_player_client_ref"]=new_client
                if self.media_type in ("itv","live"):self.item["_live_client_ref"]=new_client
                self.item["_portal"]=str(profile.get("portal") or "")
                self.item["_mac"]=str(profile.get("mac") or "")
                self.item["_source_type"]=str(profile.get("source_type") or self._server_search_source_type(profile))
                self.item["_server_name"]=str(payload.get("server_name") or self._server_search_source_name(profile,0))
                self.item["_allow_http_fallback"]=bool(profile.get("allow_http_fallback",False))
                self.item["_tls_mode"]=str(profile.get("tls_mode") or "auto")
                self.item["_device_profile"]=str(profile.get("device_profile") or "auto")
                self.item["_tls_fallback_accepted"]=bool(profile.get("tls_fallback_accepted",False))
                self.item["_http_fallback_accepted"]=bool(profile.get("http_fallback_accepted",False))
                self.streamurl=str(payload.get("url") or "")
                self._active_server_name=self._player_server_label(profile,0)
                try:self["server_name"].setText(self._active_server_name[:48])
                except Exception:pass
                if self.media_type in ("itv","live"):
                    # A cross-server Live handoff must never reuse the previous
                    # server's category/drawer rows. Keep playback safe and let
                    # the newly selected source own any subsequent Live lookup.
                    self._zap_channels=[];self._zap_total=0;self._zap_page_loader=None;self._zap_categories=[]
                    self._zap_category_id=str(history_item.get("category_id") or history_item.get("genre_id") or "")
                self._cancel_smart_recovery();self._recovery_attempts=0
                try:self["connection"].setText(_("Portal server")+"  •  "+str(payload.get("server_name") or ""))
                except Exception:pass
                # Keep the search-mode InfoBar pinned after the handoff. BACK
                # releases this hold without leaving playback; BLUE reopens the
                # cached server list instantly.
                self._server_search_begin_hold()
                self.playStream(self.servicetype,self.streamurl)
        if processed:
            try:UltraInfobarVisibility.doShow(self)
            except Exception:pass
        try:
            if self._server_search_future is None:self.server_search_timer.stop()
        except Exception:pass

    def toggleStreamType(self):
        # Engine selection remains globally locked to Settings. BLUE is now the
        # explicit alternate-server Search action; TV keeps this status helper.
        try:configured=int(self._cfg_service_type)
        except Exception:configured=self.servicetype
        self["connection"].setText(_("ENGINE LOCKED  •  %s  •  change it in Settings") % _engine_label(configured))
        try:self.doShow()
        except Exception as exc:optional_failure("player.optional_guard",exc)


    def _remember_manual_title_engine(self, chosen):
        # Engine selection is globally locked to Settings. Kept as a no-op for
        # compatibility with older callbacks that may still reference it.
        return

    def _engine_choice_answer(self, answer):
        try: chosen=int(self._cfg_service_type)
        except (TypeError,ValueError): chosen=int(self.servicetype or 4097)
        if chosen not in (1,4097,5001,5002,8193): chosen=4097
        self.engines=[chosen];self.engine_index=0;self.servicetype=chosen
        self["connection"].setText(_("ENGINE LOCKED  •  %s  •  change it in Settings") % _engine_label(chosen))
        self.playStream(chosen,self.streamurl)
        try:self.doShow()
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def hide(self):
        """Never let receiver InfoBar plumbing hide an active subtitle workflow.

        This is deliberately the last visibility authority: some Enigma2 images
        can call Screen.hide() through their own InfoBar mixins even after our
        one-shot timer has been stopped.  Playback close/restore always wins.
        """
        if not self.restored and not self._closing_playback:
            try:
                if UltraInfobarVisibility._us_infobar_modal_hold_active(self):
                    try:self.hideTimer.stop()
                    except Exception:pass
                    self._us_infobar_visible=True
                    try:Screen.show(self)
                    except Exception:pass
                    return
            except Exception as exc:
                optional_failure("player.subtitle_visibility_guard",exc)
        return Screen.hide(self)

    def _infobar_is_shown(self):
        try:
            return bool(self.infobarVisible())
        except Exception:
            return False

    def OKButton(self):
        if self.media_type in ("itv","live") and self._zap_channels:
            # First OK is always reserved for the InfoBar. Only a subsequent OK
            # while that InfoBar is actually visible opens the channel drawer.
            if (not self._zap_infobar_armed) or (not self._infobar_is_shown()):
                self._zap_infobar_armed=True
                try:self.doShow()
                except Exception:UltraInfobarVisibility.OkPressed(self)
                return
            self._zap_infobar_armed=False
            self._openZapList()
            return
        UltraInfobarVisibility.OkPressed(self)

    def _openZapList(self):
        if self._zap_open or self.media_type not in ("itv","live") or not self._zap_channels:
            return
        self._zap_open=True
        try:self.hideTimer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        channels=list(self._zap_channels)
        current_row=None
        try:
            if 0<=int(self._zap_index)<len(channels) and isinstance(channels[int(self._zap_index)],dict):
                current_row=channels[int(self._zap_index)]
        except Exception:
            current_row=None
        picon=_cached_poster(current_row or self.item,self.name,self.media_type)
        if self._zap_total>len(channels):channels.extend([None]*(self._zap_total-len(channels)))

        client=self.item.get("_live_client_ref")
        categories=list(getattr(self,"_zap_categories",[]) or [])
        def categories_loader():
            if categories:return [dict(x) for x in categories if isinstance(x,dict)]
            if client is None:return []
            return [dict(x) for x in (client.genres("itv") or []) if isinstance(x,dict)]
        def category_page_loader(category_id,page):
            if client is None:return {"items":[],"page":int(page or 1),"page_size":max(1,int(self._zap_page_size or 1)),"total":0}
            return client.ordered_page("itv",str(category_id or "*"),int(page or 1)) or {}
        try:
            self.session.openWithCallback(
                self._onZapSelected, UltraStalkerLiveZapList, channels, self._zap_index,
                self._zap_title, picon, self._zap_page_loader, self._zap_page_size, self._zap_total,
                categories, self._zap_category_id, categories_loader, category_page_loader, self._cfg_clean_titles
            )
        except Exception as exc:
            self._zap_open=False
            optional_failure("player.open_zap_list",exc)
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)

    def _onZapSelected(self,result=None):
        self._zap_open=False
        self._zap_infobar_armed=False
        if not result:
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        context=None
        if isinstance(result,dict):
            try:index=int(result.get("index") or 0);item=result.get("item")
            except Exception:return
            if not isinstance(item,dict):return
            context=dict(result)
        else:
            try:index,item=result
            except Exception:return
        self._zap_pending_drawer_context=context
        self._switchChannel(index,item)

    def _quickZap(self,delta):
        """Switch previous/next Live channel without opening the drawer."""
        if self.media_type not in ("itv","live") or self._zap_open:
            return
        total=max(int(getattr(self,"_zap_total",0) or 0),len(getattr(self,"_zap_channels",[]) or []))
        if total<=1:
            return
        if self._zap_switch_inflight:
            return
        step=-1 if int(delta or 0)<0 else 1
        target=(int(getattr(self,"_zap_index",0) or 0)+step)%total
        self._zap_infobar_armed=False
        channels=getattr(self,"_zap_channels",[]) or []
        row=channels[target] if 0<=target<len(channels) else None
        if isinstance(row,dict):
            self._switchChannel(target,row)
            return
        self._switchChannelByIndex(target)

    def _switchChannelByIndex(self,index):
        """Resolve an unloaded folder row lazily, then reuse the normal zap path."""
        client=self.item.get("_live_client_ref")
        if client is None:
            self["connection"].setText(_("CHANNEL SWITCH UNAVAILABLE"))
            return
        if self._zap_switch_inflight:
            return
        total=max(int(getattr(self,"_zap_total",0) or 0),len(getattr(self,"_zap_channels",[]) or []))
        if total<=0:
            return
        index=max(0,min(int(index),total-1))
        self._zap_pending_drawer_context=None
        self._zap_switch_context=None
        self._zap_switch_generation+=1
        generation=self._zap_switch_generation
        self._zap_switch_inflight=True
        self._zap_switch_started_at=time.monotonic()
        self["connection"].setText(_("SWITCHING CHANNEL..."))
        page_loader=self._zap_page_loader
        page_size=max(1,int(self._zap_page_size or 1))
        channels=getattr(self,"_zap_channels",[]) or []
        cached=channels[index] if 0<=index<len(channels) and isinstance(channels[index],dict) else None
        result_queue=self._zap_switch_queue
        def worker():
            page=0;rows=[];item=dict(cached) if isinstance(cached,dict) else None
            try:
                if item is None and callable(page_loader):
                    page=max(1,index//page_size+1)
                    try:loaded=page_loader(page)
                    except TypeError:loaded=page_loader(page,None)
                    rows=[dict(x) for x in (loaded or []) if isinstance(x,dict)]
                    base=(page-1)*page_size
                    rel=index-base
                    if 0<=rel<len(rows):item=dict(rows[rel])
                if not isinstance(item,dict):
                    raise ValueError("target channel row unavailable")
                url=client.create_link(item,"itv")
                result_queue.put((generation,index,item,str(url or ""),None,page,rows))
            except Exception as exc:
                result_queue.put((generation,index,item or {},"",exc,page,rows))
        try:
            self._zap_switch_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.zap_switch_timer.start(120,False)
        except Exception as exc:
            self._zap_switch_generation+=1;self._zap_switch_inflight=False;self._zap_switch_started_at=0.0
            future=getattr(self,"_zap_switch_future",None)
            if future is not None:
                try:future.cancel()
                except Exception:pass
            self._zap_switch_future=None
            optional_failure("player.quick_zap_worker_start",exc)
            self["connection"].setText(_("CHANNEL SWITCH FAILED"))

    def _switchChannel(self,index,item):
        if not isinstance(item,dict):
            return
        client=self.item.get("_live_client_ref")
        if client is None:
            self["connection"].setText(_("CHANNEL SWITCH UNAVAILABLE"))
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        if self._zap_switch_inflight:
            self["connection"].setText(_("CHANNEL SWITCH IN PROGRESS..."))
            return
        self._zap_switch_generation += 1
        generation=self._zap_switch_generation
        drawer_context=getattr(self,"_zap_pending_drawer_context",None)
        self._zap_pending_drawer_context=None
        self._zap_switch_context=(generation,drawer_context) if isinstance(drawer_context,dict) else None
        self._zap_switch_inflight=True
        self._zap_switch_started_at=time.monotonic()
        self["connection"].setText(_("SWITCHING CHANNEL..."))
        result_queue=self._zap_switch_queue
        item_snapshot=dict(item)
        def worker():
            try:
                url=client.create_link(item_snapshot,"itv")
                result_queue.put((generation,index,item_snapshot,str(url or ""),None))
            except Exception as exc:
                result_queue.put((generation,index,item_snapshot,"",exc))
        try:
            self._zap_switch_future=_PLAYER_BG_EXECUTOR.submit(worker)
            self.zap_switch_timer.start(120,False)
        except Exception as exc:
            self._zap_switch_generation+=1;self._zap_switch_inflight=False;self._zap_switch_started_at=0.0
            future=getattr(self,"_zap_switch_future",None)
            if future is not None:
                try:future.cancel()
                except Exception:pass
            self._zap_switch_future=None
            optional_failure("player.zap_worker_start",exc)
            self["connection"].setText(_("CHANNEL SWITCH FAILED"))

    def _drain_zap_switch_result(self):
        if self.restored or self._closing_playback:
            try:self.zap_switch_timer.stop()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        try:
            result=self._zap_switch_queue.get_nowait()
            generation,index,item,url,error=result[:5]
            loaded_page=(result[5] if len(result)>5 else 0)
            loaded_rows=(result[6] if len(result)>6 else [])
        except queue.Empty:
            # A pathological portal/library call must not leave channel switching
            # permanently locked. Invalidate the generation; a late worker result
            # will be ignored safely.
            try:timeout=self._cfg_timeout
            except Exception:timeout=15.0
            if self._zap_switch_inflight and self._zap_switch_started_at and time.monotonic()-self._zap_switch_started_at>timeout:
                self._zap_switch_generation+=1;self._zap_switch_inflight=False;self._zap_switch_started_at=0.0
                self._zap_switch_context=None;self._zap_pending_drawer_context=None
                try:self.zap_switch_timer.stop()
                except Exception as exc:optional_failure("player.optional_guard",exc)
                self["connection"].setText(_("CHANNEL SWITCH TIMED OUT"))
                try:self.doShow()
                except Exception as exc:optional_failure("player.optional_guard",exc)
            return
        if generation != self._zap_switch_generation:
            return
        self._zap_switch_future=None
        # Quick-zap may have lazily fetched the target portal page. Merge those
        # rows into the player folder snapshot on the GUI thread so subsequent
        # UP/DOWN presses are instant and the drawer sees the same warmed data.
        if loaded_page and loaded_rows:
            try:
                base=(int(loaded_page)-1)*max(1,int(self._zap_page_size or 1))
                if len(self._zap_channels)<self._zap_total:self._zap_channels.extend([None]*(self._zap_total-len(self._zap_channels)))
                for pos,row in enumerate(loaded_rows):
                    absolute=base+pos
                    if 0<=absolute<len(self._zap_channels) and isinstance(row,dict):self._zap_channels[absolute]=row
            except Exception as exc:optional_failure("player.quick_zap_merge_page",exc)
        self._zap_switch_inflight=False
        self._zap_switch_started_at=0.0
        try:self.zap_switch_timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        if error or not url:
            if getattr(self,"_zap_switch_context",None) and self._zap_switch_context[0]==generation:self._zap_switch_context=None
            self["connection"].setText(_("CHANNEL SWITCH FAILED"))
            try:self.doShow()
            except Exception as exc:optional_failure("player.optional_guard",exc)
            return

        # R66: a channel selected from a different in-player category becomes
        # the new authoritative drawer context only after its link was resolved
        # successfully. A failed zap therefore never strands the viewer in a
        # category/channel snapshot that is not actually playing.
        switch_context=getattr(self,"_zap_switch_context",None)
        context=(switch_context[1] if switch_context and switch_context[0]==generation and isinstance(switch_context[1],dict) else None)
        self._zap_switch_context=None
        if context is not None:
            try:
                category_id=str(context.get("category_id") or "")
                category_title=str(context.get("category_title") or _("Live TV"))
                channels=list(context.get("channels") or [])
                page_size=max(1,int(context.get("page_size") or self._zap_page_size or 1))
                total=max(len(channels),int(context.get("total") or len(channels)))
                if total and len(channels)<total:channels.extend([None]*(total-len(channels)))
                self._zap_category_id=category_id
                self._zap_title=category_title
                if isinstance(context.get("categories"),list):
                    self._zap_categories=[dict(x) for x in context.get("categories") if isinstance(x,dict)]
                self._zap_channels=channels
                self._zap_page_size=page_size
                self._zap_total=total
                client=self.item.get("_live_client_ref")
                if client is not None and category_id:
                    def _category_page(page,_client=client,_category=category_id):
                        data=_client.ordered_page("itv",_category,int(page or 1)) or {}
                        return [dict(x) for x in (data.get("items") or []) if isinstance(x,dict)] if isinstance(data,dict) else [dict(x) for x in (data or []) if isinstance(x,dict)]
                    self._zap_page_loader=_category_page
                self.item["_live_category_id"]=category_id
                self.item["_live_folder_title"]=category_title
                self.item["_live_folder_total"]=total
                self.item["_live_page_size"]=page_size
            except Exception as exc:
                optional_failure("player.zap_category_context",exc)

        old_url=self.streamurl
        self._suppress_service_events(1.25)
        try:force_session_silence(self.session,old_url,force=True,stop_native=True,exclude_pids=self._external_player_baseline,owned_pids=self._owned_external_pids)
        except Exception as exc:optional_failure("player.zap_stop_owned",exc)
        self._owned_external_pids.clear()

        self._zap_index=max(0,int(index))
        # R67: a successful category/channel switch must never inherit the old
        # channel artwork.  ``dict.update`` alone leaves stale _player_picon/logo
        # keys behind when the new catalogue row does not carry those exact keys,
        # which is why the InfoBar kept showing the first channel picon.
        for _art_key in ("_player_picon","_player_picon_url","_player_poster",
                         "stream_icon","picon","logo","poster","poster_url",
                         "cover","cover_url","icon","image","img",
                         "_receiver_picon_local","picon_local","logo_local","image_local"):
            try:self.item.pop(_art_key,None)
            except Exception:pass
        # A channel selected from the OKx2 drawer must never inherit EPG text
        # from the previous channel. Runtime client refs stay on self.item, but
        # all programme fields are replaced by the new row/network result.
        for _epg_key in ("now","program","epg_title","next","next_program","epg_next"):
            try:self.item.pop(_epg_key,None)
            except Exception:pass
        self.item.update(item)
        self.name=_clean_live_name(str(item.get("name") or item.get("title") or "Live channel"),self._cfg_clean_titles)
        try:
            recent_item=dict(item)
            # Never persist Player-only runtime objects or a whole folder snapshot.
            for _runtime_key in ("_live_folder_channels","_live_page_loader","_live_client_ref"):
                recent_item.pop(_runtime_key,None)
            recent_item["_live_category_id"]=str(self._zap_category_id or "")
            recent_item["_live_folder_title"]=str(self._zap_title or _("Live TV"))
            recent_item["_live_absolute_index"]=int(self._zap_index or 0)
            recent_item["_live_page_size"]=max(1,int(self._zap_page_size or 1))
            recent_item["_live_folder_page"]=(int(self._zap_index or 0)//max(1,int(self._zap_page_size or 1)))+1
            self._pending_live_recent_item=recent_item
        except Exception as exc:
            self._pending_live_recent_item=None
            optional_failure("player.live_recent_prepare",exc)
        # R68: picon identity is provider-only.  Reuse a local provider picon
        # immediately; otherwise show the neutral placeholder while a background
        # worker downloads the selected channel's stream_icon into live_picons.
        # This works even when its category was never opened in the Live grid.
        try:
            _new_picon=self._provider_live_picon_cached(self.item)
            if _new_picon:
                self.item["_player_picon"]=_new_picon
                self._load_live_picon(_new_picon)
            else:
                self._load_live_picon(_asset("us168_live_placeholder_220x132.png"))
                self._request_provider_live_picon(self.item)
        except Exception as exc:
            optional_failure("player.zap_picon_refresh",exc)
        self.streamurl=url
        self._recovery_attempts=0
        self._retry_visual_clear(force=True)
        self._apply_live_channel_title()
        try:self["now"].setText(self._now_text());self["category"].setText(self._category_label())
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:self._refresh_live_epg()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:
            engine=int(self._cfg_service_type)
        except (TypeError,ValueError):
            engine=int(self.servicetype or 4097)
        if engine not in (1,4097,5001,5002,8193):engine=4097
        self.engines=[engine];self.engine_index=0;self.servicetype=engine
        self.playStream(engine,url)
        # Re-run EPG ownership after the selected service handoff. Some images
        # clear/rebuild Player widgets during playService(), so a pre-handoff
        # refresh alone can disappear after an OKx2 channel selection.
        try:self._refresh_live_epg()
        except Exception as exc:optional_failure("player.live_epg_post_zap",exc)
        try:self.doShow()
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _cancel_pending_resume_for_manual_seek(self):
        """A viewer seek permanently cancels any pending automatic Resume seek."""
        self._resume_target=0
        self._resume_grace_until=0.0
        self.resume_prompted=True
        try:self.resume_timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:self.resume_verify_timer.stop()
        except Exception as exc:optional_failure("player.optional_guard",exc)
        try:self._release_resume_shield()
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _play_state_changed(self, state):
        try:
            value = state[3] or ""
        except Exception:
            value = ""
        self._last_play_state_value=str(value or "")
        try:self._external_subtitle_play_state(value)
        except Exception as exc:optional_failure("player.external_subtitle_playstate_hook",exc)
        if self.media_type in ("vod","series","episode","catchup"):
            if value.startswith(">>") or value.startswith("<<") or value.startswith("/"):
                # Manual trick-play owns the transport state now.  The retired
                # 1x1 status icon/speed labels no longer mirror it visually.
                self._cancel_pending_resume_for_manual_seek()
            elif value in ("||", ">"):
                self._save_history_progress(force=True)
            if value == "||" and self._cfg_crash_safe_progress:
                self._save_history_progress(force=True)

    def _schedule_resume_probe(self):
        attempt=max(0,int(self._resume_verify_attempts or 0))
        delay=min(1200,50*(2**min(attempt,4)))
        self.resume_verify_timer.stop(); self.resume_verify_timer.start(delay,True)

    def _perform_resume_seek(self):
        """Apply the saved bookmark once, but only after the decoder is truly seekable.

        ServiceApp/ExtePlayer3 can expose service.seek() at evStart before
        seekTo() is actually honoured.  We therefore require a valid length and
        play position, plus a short decoder-settle window, before issuing the
        single absolute seek.
        """
        if not self._resume_target or self.restored or self._closing_playback:return
        if self._resume_seek_count>0:return
        try:
            # Give ServiceApp a moment after evStart to publish a usable
            # iSeekableService. Readiness probes do not count as seek attempts.
            if self._watch_started_at and (time.time()-self._watch_started_at)<0.55:
                self._resume_verify_attempts+=1;self._schedule_resume_probe();return
            service=self.session.nav.getCurrentService();seekable=service.seek() if service else None
            if seekable is None:
                self._resume_verify_attempts+=1
                if self._resume_verify_attempts<14:self._schedule_resume_probe()
                else:self._resume_target=0;self._release_resume_shield()
                return
            native_probe=getattr(self,"isCurrentlySeekable",None)
            if callable(native_probe):
                try:
                    state=native_probe()
                    if isinstance(state,(tuple,list)):
                        state=(not state[0]) and (len(state)<2 or bool(state[1]))
                    if not bool(state):
                        self._resume_verify_attempts+=1;self._schedule_resume_probe();return
                except Exception as exc:optional_failure("player.optional_guard",exc)
            length=seekable.getLength();position=seekable.getPlayPosition()
            length_ok=bool(length and not length[0] and int(length[1] or 0)>30*90000)
            pos_ok=bool(position and not position[0] and int(position[1] or 0)>=0)
            if not (length_ok and pos_ok):
                self._resume_verify_attempts+=1
                if self._resume_verify_attempts<14:self._schedule_resume_probe()
                else:self._resume_target=0;self._release_resume_shield();self["connection"].setText(_("PLAYING  •  resume unavailable"))
                return
            target=min(int(self._resume_target),max(0,int(length[1])-10*90000))
            result=seekable.seekTo(target)
            try:self._external_subtitle_after_seek()
            except Exception as exc:optional_failure("player.external_subtitle_resume_seek",exc)
            # Enigma2 implementations commonly return None/0 on success.  What
            # matters is that the command was issued after verified readiness.
            self._resume_seek_count=1
            self._last_progress_position=target
            self["connection"].setText(_("RESUMING  •  %d:%02d")%(target//90000//60,target//90000%60))
            self.resume_verify_timer.stop();self.resume_verify_timer.start(450,True)
        except Exception as exc:
            optional_failure("player.resume_seek",exc)
            self._resume_verify_attempts+=1
            if self._resume_seek_count==0 and self._resume_verify_attempts<14:self._schedule_resume_probe()
            else:
                self._resume_target=0;self._release_resume_shield()

    def _verify_resume_position(self):
        if not self._resume_target or self.restored or self._closing_playback:return
        if self._resume_seek_count==0:
            self._perform_resume_seek();return
        try:
            service=self.session.nav.getCurrentService();seekable=service.seek() if service else None
            pos=seekable.getPlayPosition() if seekable else None
            current=max(0,int(pos[1])) if pos and not pos[0] else 0
        except Exception as exc:
            optional_failure("player.resume_verify",exc);current=0
        tolerance=7*90000
        if current and abs(int(current)-int(self._resume_target))<=tolerance:
            self._last_progress_position=current
            self._resume_target=0;self._resume_grace_until=0.0
            self._release_resume_shield();self["connection"].setText("")
            try:self._schedule_subtitle_session_restore(240)
            except Exception as exc:optional_failure("player.subtitle_session_resume_verified",exc)
            return
        self._resume_verify_cycles+=1
        if self._resume_verify_cycles<7:
            self.resume_verify_timer.stop();self.resume_verify_timer.start(350,True);return
        # One actual automatic seek only. If the engine ignored it, fail cleanly
        # rather than issuing a late retry that could override a manual seek.
        self._resume_target=0;self._resume_grace_until=0.0;self._release_resume_shield()
        self["connection"].setText(_("PLAYING  •  resume unavailable"))

    def _native_resume_updated(self):
        """Resume through the native CueSheet-style flow and mark fresh service info."""
        self._vod_quality_updated_info_event()
        if self.restored or self._closing_playback or self.media_type not in ("vod","series","episode","catchup"):
            return
        if getattr(self,"_native_resume_started",False):
            return
        bookmark=self._resume_bookmark()
        if not bookmark:
            self._native_resume_started=True
            return
        try:
            service=self.session.nav.getCurrentService()
            if service is None:return
            seekable=service.seek()
            if seekable is None:return
            length=seekable.getLength() or (None,0)
            total=abs(int(length[1] or 0)) if len(length)>1 else 0
            if bookmark <= 900000:return
            if total and bookmark >= total-900000:
                self._native_resume_started=True
                return
            # Mark immediately before issuing the one and only automatic seek.
            self._native_resume_started=True
            self.resume_prompted=True
            seekable.seekTo(int(bookmark))
            try:self._external_subtitle_after_seek()
            except Exception as exc:optional_failure("player.external_subtitle_native_resume_seek",exc)
            try:self._schedule_subtitle_session_restore(320)
            except Exception as exc:optional_failure("player.subtitle_session_native_resume",exc)
            self._last_progress_position=int(bookmark)
            self._update_player_time_labels(bookmark,total)
            self["connection"].setText(_("PLAYING  •  RESUMED %d:%02d:%02d") % (bookmark//90000//3600,(bookmark//90000%3600)//60,bookmark//90000%60))
        except Exception as exc:
            optional_failure("player.native_resume",exc)

    def _reference_resume(self):
        """Apply the saved stable-ID bookmark using the proven native flow."""
        if self.restored or self._closing_playback or self.resume_prompted:
            return
        self.resume_prompted = True
        bookmark = self._resume_bookmark()
        if not bookmark:
            return
        try:
            service = self.session.nav.getCurrentService()
            seekable = service.seek() if service else None
            if seekable is None:
                return
            length_result = seekable.getLength()
            length = abs(int(length_result[1])) if length_result and not length_result[0] else 0
        except Exception as exc:
            optional_failure("player.reference_resume_probe", exc)
            return
        if length and bookmark >= length - (10 * 90000):
            return
        behavior = self._cfg_resume_behavior
        if behavior == "start":
            return
        if behavior == "ask":
            seconds = int(bookmark // 90000)
            message = _("Resume playback from %d:%02d:%02d?") % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)
            try:
                self.session.openWithCallback(lambda answer: self._reference_resume_answer(answer, bookmark), MessageBox, message, MessageBox.TYPE_YESNO, timeout=10)
                return
            except Exception as exc:
                optional_failure("player.reference_resume_prompt", exc)
        self._reference_resume_answer(True, bookmark)

    def _reference_resume_answer(self, answer, position):
        if not answer or self.restored or self._closing_playback:
            return
        try:
            service = self.session.nav.getCurrentService()
            seekable = service.seek() if service else None
            if seekable is not None:
                seekable.seekTo(int(position))
                try:self._schedule_subtitle_session_restore(320)
                except Exception as exc:optional_failure("player.subtitle_session_reference_resume",exc)
                self._last_progress_position = int(position)
                self._resume_seek_count = 1
                self["connection"].setText(_("PLAYING  •  RESUMED %d:%02d") % (int(position)//90000//60, int(position)//90000%60))
        except Exception as exc:
            optional_failure("player.reference_resume_seek", exc)

    def _periodic_progress_save(self):
        if self.restored or self.media_type not in ("vod", "series", "episode", "catchup"):
            return
        self._save_history_progress()

    @staticmethod
    def _format_player_pts(value):
        try:
            seconds=max(0,int(value or 0)//90000)
        except Exception:
            seconds=0
        hours=seconds//3600
        minutes=(seconds%3600)//60
        secs=seconds%60
        return "%d:%02d:%02d"%(hours,minutes,secs)

    def _update_player_time_labels(self,position,duration):
        try:
            position=max(0,int(position or 0))
            duration=max(0,int(duration or 0))
            if duration>0:
                position=min(position,duration)
                remaining=max(0,duration-position)
                self["elapsed_time"].setText(self._format_player_pts(position))
                self["total_time"].setText(self._format_player_pts(duration))
                self["remaining_time"].setText(self._format_player_pts(remaining))
            else:
                self["elapsed_time"].setText(self._format_player_pts(position) if position>0 else "")
                self["total_time"].setText("")
                self["remaining_time"].setText("")
        except Exception as exc:
            optional_failure("player.time_labels",exc)

    def _update_progress_visual(self):
        if not getattr(self,"_player_infobar_visible",True):
            try:self.progress_visual_timer.stop()
            except Exception as exc:optional_failure("player.hidden_progress_visual_guard",exc)
            return
        if self.restored or self.media_type not in ("vod","series","episode","catchup"):
            try:
                self["progress_neon"].hide(); self["skip_hint"].setText("")
                self["elapsed_time"].setText(""); self["total_time"].setText(""); self["remaining_time"].setText("")
            except Exception as exc:
                optional_failure("player.progress_visual_hide", exc)
            return
        position,duration=self._service_progress_snapshot()
        self._retry_visual_note_vod_progress(position)
        self._update_player_time_labels(position,duration)
        if duration <= 0:
            try:self["vod_progress"].setValue(0);self["vod_progress"].show()
            except Exception:pass
            self._update_skip_hint(position, duration)
            return
        ratio=max(0.0,min(1.0,float(position)/float(duration)))
        try:
            value=max(0,min(100,int(round(ratio*100.0))))
            self["vod_progress"].setValue(value);self["vod_progress"].show()
            # Only swap the laser pixmap when the integer percentage changes. This keeps the
            # one-second timer cheap on the receiver while preserving smooth visual progress.
            if value != self._progress_neon_value:
                hx=str(self._adaptive_accent_neon or "#55b9ff").lstrip("#")
                path=os.path.join(os.path.join(PERSISTENT_GENERATED_DIR,"progress_neon239"),"%s_%03d.png"%(hx,value))
                if os.path.isfile(path) and self["progress_neon"].instance is not None:
                    self["progress_neon"].instance.setPixmapFromFile(path);self["progress_neon"].show();self._progress_neon_value=value
                else:
                    _schedule_progress_neon_frame(self._adaptive_accent_neon,value)
        except Exception as exc:optional_failure("player.progress_visual",exc)
        self._update_skip_hint(position, duration)

    def _update_skip_hint(self, position, duration):
        # 1/3 are pure relative seek. Skip Intro/Credits no longer exist.
        try:self["skip_hint"].setText("")
        except Exception as exc:optional_failure("player.optional_guard",exc)

    def _seek_relative_seconds(self, seconds):
        """Reliable repeated numeric seek: 1=-10s, 3=+10s.

        ServiceApp/exteplayer3 can report the PREVIOUS play position for a short
        window after a seek.  A second relative seek then starts from stale PTS
        and appears to jump forward/back to the same place.  Keep a short-lived
        absolute target and accumulate each key press on that target instead.
        """
        seconds = int(seconds or 0)
        if not seconds:
            return False
        now=time.monotonic()
        # Only suppress duplicate key events from the same physical press.  Do
        # not swallow deliberate quick repeated presses.
        if now-float(getattr(self,"_last_manual_seek_at",0.0) or 0.0) < 0.055:
            return False
        self._last_manual_seek_at=now
        self._cancel_pending_resume_for_manual_seek()
        delta=int(seconds*90000)
        try:
            service=self.session.nav.getCurrentService()
            seekable=service.seek() if service else None
            if seekable is None:return False
            check=getattr(seekable,"isCurrentlySeekable",None)
            if callable(check):
                try:
                    if not check():return False
                except Exception as exc:optional_failure("player.optional_guard",exc)

            pos=seekable.getPlayPosition()
            current=int(pos[1]) if pos and not pos[0] else int(getattr(self,"_last_progress_position",0) or 0)
            length=seekable.getLength()
            total=int(length[1]) if length and not length[0] else 0

            cached=getattr(self,"_manual_seek_target_pts",None)
            cached_at=float(getattr(self,"_manual_seek_target_at",0.0) or 0.0)
            # While the decoder is settling, the cached target is the truth.
            # After ~2.5s of no numeric seek, resume from the live position.
            base=int(cached) if cached is not None and (now-cached_at)<2.5 else max(0,current)
            target=max(0,base+delta)
            if total>0:
                target=min(target,max(0,total-90000))

            result=seekable.seekTo(int(target))
            self._external_subtitle_after_seek()
            self._manual_seek_target_pts=int(target)
            self._manual_seek_target_at=now
            self._last_progress_position=int(target)
            self._update_player_time_labels(target,total)
            try:
                self["skip_hint"].setText("+10s" if seconds > 0 else "-10s")
            except Exception as exc:
                optional_failure("player.optional_guard", exc)
            return result in (None,0,True) or True
        except Exception as exc:
            # Receiver fallback for engines that expose relative seek but not a
            # usable absolute play position.  Keep this off the normal path.
            try:
                service=self.session.nav.getCurrentService();seekable=service.seek() if service else None
                if seekable is not None:
                    seekable.seekRelative(1 if seconds>0 else -1,abs(delta));self._external_subtitle_after_seek();return True
            except Exception:
                pass
            optional_failure("player.numeric_seek_10", exc)
            return False

    def seekBack10(self):
        return self._seek_relative_seconds(-10)

    def seekForward10(self):
        return self._seek_relative_seconds(10)

    def _seek_bridge_allowed(self):
        # R48 is deliberately conservative: do not force seek semantics onto
        # linear Live TV streams.  Movie/series/episode/catch-up keep the exact
        # existing seek implementation and its engine safety checks.
        return self.media_type in ("vod", "series", "episode", "catchup")

    def seekBridgeBack10(self):
        if not self._seek_bridge_allowed():
            return False
        return self.seekBack10()

    def seekBridgeForward10(self):
        if not self._seek_bridge_allowed():
            return False
        return self.seekForward10()

    def cycleDisplayAspect(self):
        """Open an inline Aspect Ratio chooser instead of cycling with delays."""
        choices=[
            (_(aspect_mode_label(0)),0),
            (_(aspect_mode_label(1)),1),
            (_(aspect_mode_label(2)),2),
            (_(aspect_mode_label(3)),3),
            (_(aspect_mode_label(4)),4),
            (_(aspect_mode_label(5)),5),
            (_(aspect_mode_label(6)),6),
        ]
        current=capture_aspect_mode(switch_cls=eAVSwitch)
        selected=0
        for idx,row in enumerate(choices):
            if len(row)>1 and row[1]==current:
                selected=idx;break
        try:
            return self._subtitle_inline_show(
                choices,selected,self._aspect_ratio_selected,
                self._subtitle_inline_finish,"aspect_ratio"
            )
        except Exception as exc:
            optional_failure("player.aspect_menu",exc)
            self._subtitle_inline_finish()
            return False

    def _aspect_ratio_selected(self,choice=None):
        if not choice:
            return self._subtitle_inline_finish()
        value=choice[1] if isinstance(choice,(tuple,list)) and len(choice)>1 else choice
        if apply_aspect_mode(value,switch_cls=eAVSwitch):
            self._aspect_cycle_cursor=value
        else:
            try:self["connection"].setText(_("Aspect-ratio change failed"))
            except Exception:pass
        self._subtitle_inline_finish()
        return True
    def show_media_info(self):
        meta="%s   •   %s   •   %s   •   %s"%(self._category_label(),self["video_quality"].getText(),self["video_codec"].getText(),self["audio_codec"].getText())
        self._media_info_open=True
        try:
            if self._online_subtitle_overlay is not None:self._online_subtitle_overlay.hide()
        except Exception:pass
        try:
            self._set_infobar_visuals(False)
        except Exception as exc:optional_failure("player",exc)
        try:self.session.openWithCallback(self._media_info_closed,PlayerInformationOverlay,self.name,self.item,self.media_type,meta,self._poster_path)
        except Exception as exc:
            LOG.warning("Nova media information overlay failed: %s", exc)
            try:self["connection"].setText(_("MEDIA INFO UNAVAILABLE"))
            except Exception as exc:optional_failure("player",exc)
    def _media_info_closed(self,*args):
        self._media_info_open=False
        try:self.doShow()
        except Exception:
            try:self.show()
            except Exception as exc:optional_failure("player",exc)

    def _service_progress_snapshot(self, raw=False):
        """Read one sane 90kHz PTS snapshot from the active Enigma2 service."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service.seek() if service else None
            pos_result = seek.getPlayPosition() if seek else None
            len_result = seek.getLength() if seek else None
            position = int(pos_result[1]) if pos_result and not pos_result[0] else 0
            duration = int(len_result[1]) if len_result and not len_result[0] else 0
            # Negative values are error/sentinel values on several OE-A builds;
            # never abs() them into a fake positive resume timestamp.
            position = max(0, position)
            duration = max(0, duration)
            if duration and position > duration + (5 * 90000):
                position = 0
            return position, duration
        except Exception as exc:
            try:
                if self._cfg_diagnostic_logging: optional_failure("player.progress_snapshot",exc)
            except Exception as exc:
                optional_failure("player.optional_guard", exc)
            return 0, 0

    def _save_history_progress(self, force=False, completed_override=None):
        if self.media_type not in ("vod","series","episode","catchup"):
            return
        try:
            if not self.started or not self._watch_started_at:
                return
            elapsed = time.time() - float(self._watch_started_at)
            had_resume = int(self.item.get("_resume_position") or 0) >= (10 * 90000)
            # A resumed title must be allowed to update its bookmark even when
            # the viewer watches only a few seconds before pressing BACK.
            if not force and not had_resume and elapsed < float(self._history_save_min_seconds):
                return
        except Exception:
            if not force:
                return

        position, duration = self._service_progress_snapshot()
        if position > 0:
            self._last_progress_position = position
        if duration > 0:
            self._last_progress_duration = duration
        position = position or int(self._last_progress_position or 0)
        duration = duration or int(self._last_progress_duration or 0)
        try:
            existing=max(0,int(self.item.get("_resume_position") or 0))
            elapsed=max(0.0,time.time()-float(self._watch_started_at or time.time()))
            if duration and existing >= 45*90000 and elapsed < 75.0 and position >= int(duration*0.95) and existing < int(duration*0.85):
                position=existing
        except Exception as exc: optional_failure("player.progress_eof_guard",exc)
        minimum = int(self._history_save_min_seconds * 90000)
        if position < minimum and completed_override is not True:
            return
        if duration and position > duration + (5 * 90000):
            return
        try:
            profile=self._history_profile
            history_item=self._history_item
            threshold=self._cfg_completion_threshold
            remaining_limit=self._cfg_completion_remaining_pts
            completed_by_position=bool(duration and (position>=int(duration*threshold) or max(0,duration-position)<=remaining_limit))
            completed = bool(completed_override) if completed_override is not None else bool(self._cfg_auto_remove_completed and completed_by_position)
            add_recently_played(profile,self.media_type,history_item,position,duration,completed,force=bool(force))
        except Exception as exc:
            optional_failure("player", exc)

    def _current_service_is_owned(self):
        try:
            current=self.session.nav.getCurrentlyPlayingServiceReference()
            if current is None:return False
            current_text=current.toString()
            owned=str(getattr(self,"_owned_reference_string","") or "")
            if owned:return current_text==owned
            ref=getattr(self,"reference",None)
            return bool(ref is not None and current_text==ref.toString())
        except Exception:return False

    def _stop_owned_service(self, force_external=False):
        """Hard-stop only the native/external playback owned by this screen."""
        self._cancel_smart_recovery();self._closing_playback=True
        for timer_name in ("startup_timer","resume_verify_timer","subtitle_session_restore_timer","subtitle_default_timer","progress_timer","progress_visual_timer","eof_guard_timer","recovery_timer","auto_retry_delay_timer","retry_visual_success_timer","zap_switch_timer","stable_timer","stream_info_timer","vod_quality_timer","memory_fuse_timer"):
            timer=getattr(self,timer_name,None)
            if timer is not None:
                try:timer.stop()
                except Exception as exc:optional_failure("player.stop_timer",exc)
        # Unified hard stop: leaving a plugin player always stops the active
        # Enigma2 service first. ServiceApp can wrap/change the reference, so an
        # ownership-string comparison is not sufficient to guarantee silence.
        try:
            nav=getattr(self.session,"nav",None)
            if nav is not None:
                nav.stopService()
        except Exception as exc:optional_failure("player.stopService",exc)
        try:force_session_silence(self.session,self.streamurl,force=bool(force_external),stop_native=True,exclude_pids=self._external_player_baseline,owned_pids=self._owned_external_pids)
        except Exception as exc:optional_failure("player.hard_stop",exc)
        sig=signal.SIGKILL if force_external else signal.SIGTERM
        targets=set(self._owned_external_pids)
        try:targets.update(set(_matching_external_player_pids(self.streamurl))-set(self._external_player_baseline))
        except Exception as exc:optional_failure("player.external_match_stop",exc)
        for pid in list(targets):
            try:os.kill(int(pid),sig)
            except OSError:self._owned_external_pids.discard(pid)
            except Exception as exc:optional_failure("player.owned_external_stop",exc)
        return True

    def _service_still_owned(self):
        """True only while this exact native reference or matching player PID lives."""
        try:
            if self._current_service_is_owned():return True
        except Exception as exc:
            optional_failure("player.hard_stop_probe",exc)
        try:
            alive=[]
            for pid in list(self._owned_external_pids):
                if os.path.exists("/proc/%d"%int(pid)):alive.append(pid)
                else:self._owned_external_pids.discard(pid)
            matched=set(_matching_external_player_pids(self.streamurl))-set(self._external_player_baseline)
            descendants=_descendant_pids(self._owned_external_pids)-set(self._external_player_baseline)
            return bool(alive or matched or descendants)
        except Exception as exc:
            optional_failure("player.hard_stop_process_probe",exc)
            return True

    def _complete_service_handoff(self, restore_service=True):
        """Finish playback ownership and optionally return to the captured service."""
        self._closing_playback=True
        self._cancel_smart_recovery()

        exit_timers=(
            "startup_timer","resume_timer","resume_verify_timer","subtitle_session_restore_timer","subtitle_default_timer",
            "progress_timer","progress_visual_timer","hard_stop_timer","eof_guard_timer",
            "recovery_timer","auto_retry_delay_timer","retry_visual_success_timer","zap_switch_timer","stable_timer","stream_info_timer","vod_quality_timer",
            "memory_fuse_timer","hideTimer",
        )
        for name in exit_timers:
            timer=getattr(self,name,None)
            if timer is None:
                continue
            try:
                timer.stop()
            except Exception as exc:
                optional_failure("player.silent_guard",exc)

        nav=getattr(getattr(self,"session",None),"nav",None)
        if nav is None:
            return False

        try:
            nav.stopService()
        except Exception as exc:
            optional_failure("player.service_handoff_stop",exc)

        service_restored=False
        return_ref=str(getattr(self,"_return_service_ref_string","") or "")
        if restore_service and return_ref:
            try:
                nav.playService(eServiceReference(return_ref))
                service_restored=True
            except Exception as exc:
                optional_failure("player.service_handoff_restore",exc)

        if restore_service:
            restore_aspect_mode(getattr(self,"_return_aspect_ratio",None), switch_cls=eAVSwitch)

        self._service_handoff_done=True
        try:
            gc.collect()
            _malloc_trim()
        except Exception as exc:
            optional_failure("player.silent_guard",exc)
        return service_restored

    def _begin_hard_stop_close(self, result):
        # Compatibility entry point used by the rest of Ultra Stalker. Normal
        # EXIT follows the deterministic stop/restore/close flow.
        self._hard_stop_pending_result=result
        self._hard_stop_closing=True
        skip_restore=bool(isinstance(result,dict) and result.get("next_episode"))
        restored=self._complete_service_handoff(restore_service=not skip_restore)
        if isinstance(result,dict):result["service_restored"]=bool(restored)
        self._finalize_player_close()

    def _verify_hard_stop(self):
        # Kept only for compatibility with an already-armed timer. No retry loop.
        if self._hard_stop_closing:self._finalize_player_close()

    def _finalize_player_close(self):
        runtime_breadcrumb("player_close",media_type=self.media_type,engine=int(self.servicetype or 0),handoff=True)
        result=self._hard_stop_pending_result or {"engine":self.servicetype,"started":self.started,"failed":self.failed}
        self._hard_stop_pending_result=None;self._hard_stop_closing=False;self.reference=None
        self.close(result)

    def _close_player(self, extra=None, save_progress=True):
        if self.restored or self._hard_stop_closing:return
        try:self._subtitle_session_capture()
        except Exception as exc:optional_failure("player.subtitle_session_close_capture",exc)
        self._closing_playback=True
        if save_progress and self.media_type in ("vod","series","episode","catchup"):
            try:self._save_history_progress(force=True)
            except Exception as exc:optional_failure("player.close_save",exc)
        result={"engine":self.servicetype,"started":self.started,"failed":self.failed}
        # VOD quality is runtime-only presentation. Never feed it back into
        # Details/catalogue metadata; every title starts its own blank session.
        if isinstance(extra,dict):result.update(extra)
        self.restored=True
        self._suppress_service_events(2.5)
        self._begin_hard_stop_close(result)

    def _next_episode_answer(self, answer):
        global NEXT_EPISODE_AUTOPLAY_SESSION
        next_item = self.item.get("_next_episode_item") if isinstance(self.item, dict) else None
        if answer == "session_off":
            NEXT_EPISODE_AUTOPLAY_SESSION = False
            self._close_player(save_progress=False)
        elif answer and isinstance(next_item, dict) and next_item:
            self._close_player({"next_episode": dict(next_item)}, save_progress=False)
        else:
            self._close_player(save_progress=False)

    def _exit_or_close_subtitle_overlay(self):
        """EXIT closes Player-owned inline UI before leaving playback."""
        search_overlay=getattr(self,"_server_search_inline_overlay",None)
        try:
            if search_overlay is not None and search_overlay.active:
                return search_overlay.close()
            if getattr(self,"_server_search_mode_active",False):
                self._server_search_end_hold(hide_infobar=True)
                return True
        except Exception as exc:
            optional_failure("player.server_search_exit",exc)
        overlay=getattr(self,"_subtitle_inline_overlay",None)
        try:
            if overlay is not None and overlay.active:
                return overlay.close()
        except Exception as exc:
            optional_failure("player.subtitle_exit",exc)
        return self._close_player(save_progress=True)

    def back(self):
        self._close_player(save_progress=True)

    def _cleanup(self):
        try:self._live_picon_fetch_generation+=1;self._live_picon_fetch_inflight=False
        except Exception:pass
        runtime_breadcrumb("player_cleanup",media_type=self.media_type,engine=int(self.servicetype or 0))
        try:self._external_subtitle_ui_hold_end()
        except Exception:pass
        try:self._external_subtitle_stop()
        except Exception as exc:optional_failure("player.external_subtitle_cleanup",exc)
        try:self._player_title_logo_token+=1;self._player_title_logo_pending=False
        except Exception:pass
        try:self._online_subtitle_generation+=1;self._online_subtitle_cancel.set()
        except Exception:pass
        try:self._server_search_generation+=1;self._server_search_cancel.set()
        except Exception:pass
        try:
            owned=getattr(self,"_server_search_owned_client",None)
            if owned is not None:owned.close()
            self._server_search_owned_client=None
        except Exception:pass
        try:self._zap_switch_generation+=1;self._zap_switch_inflight=False;self._zap_switch_started_at=0.0
        except Exception:pass
        for future_name in ("_player_title_logo_future","_online_subtitle_future","_zap_switch_future","_auto_retry_future","_live_epg_future","_server_search_future"):
            future=getattr(self,future_name,None)
            if future is not None:
                try:future.cancel()
                except Exception as exc:optional_failure("player.future_cancel",exc)
            try:setattr(self,future_name,None)
            except Exception:pass
        # Never leave global receiver audio muted if the screen closes while a
        # resume probe is still in progress.
        try: self._release_resume_shield()
        except Exception as exc: optional_failure("player.resume_cleanup", exc)
        # Final idempotent safety net.  Repeat the complete native + pnav +
        # external stop sequence in case the screen was closed through an
        # unexpected Enigma2 path rather than back().
        if not getattr(self,"_service_handoff_done",False):
            try:
                force_session_silence(self.session, self.streamurl, force=True, stop_native=True, exclude_pids=self._external_player_baseline, owned_pids=self._owned_external_pids)
            except Exception as exc:
                optional_failure("player.cleanup_hard_stop", exc)
        for timer_name in ("startup_timer", "resume_timer", "resume_verify_timer", "subtitle_session_restore_timer", "subtitle_default_timer", "embedded_subtitle_probe_timer", "online_subtitle_timer", "online_subtitle_result_timer", "subtitle_volume_restore_timer", "external_subtitle_ui_timer", "server_search_timer", "progress_timer", "progress_visual_timer", "hard_stop_timer", "eof_guard_timer", "recovery_timer", "auto_retry_delay_timer", "retry_visual_success_timer", "live_epg_timer", "zap_switch_timer", "stable_timer", "stream_info_timer", "vod_quality_timer", "player_title_logo_timer", "memory_fuse_timer", "hideTimer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception as exc:
                    optional_failure("player", exc)
        for conn_name in ("startup_timer_conn", "resume_timer_conn", "resume_verify_timer_conn", "subtitle_session_restore_timer_conn", "subtitle_default_timer_conn", "embedded_subtitle_probe_timer_conn", "online_subtitle_timer_conn", "online_subtitle_result_timer_conn", "subtitle_volume_restore_timer_conn", "external_subtitle_ui_timer_conn", "server_search_timer_conn", "progress_timer_conn", "progress_visual_timer_conn", "hard_stop_timer_conn", "eof_guard_timer_conn", "recovery_timer_conn", "auto_retry_delay_timer_conn", "retry_visual_success_timer_conn", "live_epg_timer_conn", "zap_switch_timer_conn", "stable_timer_conn", "stream_info_timer_conn", "vod_quality_timer_conn", "player_title_logo_timer_conn", "memory_fuse_timer_conn", "PicLoad_conn", "hideTimer_conn"):
            conn = getattr(self, conn_name, None)
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception as exc:
                    optional_failure("player", exc)
        callback_map=(
            ("startup_timer",self._startup_timeout),("resume_timer",self._reference_resume),("resume_verify_timer",self._verify_resume_position),
            ("subtitle_session_restore_timer",self._subtitle_session_restore_tick),("subtitle_default_timer",self._enforce_default_subtitles_off),
            ("embedded_subtitle_probe_timer",self._embedded_subtitle_probe_tick),
            ("online_subtitle_timer",self._online_subtitle_tick),("online_subtitle_result_timer",self._drain_online_subtitle_result),
            ("subtitle_volume_restore_timer",self._restore_subtitle_after_volume_osd),("external_subtitle_ui_timer",self._external_subtitle_ui_watch),("server_search_timer",self._drain_server_search_result),
            ("progress_timer",self._periodic_progress_save),
            ("progress_visual_timer",self._update_progress_visual),("hard_stop_timer",self._verify_hard_stop),
            ("eof_guard_timer",self._verify_early_eof),("recovery_timer",self._drain_recovery_result),("auto_retry_delay_timer",self._run_deferred_auto_retry),("retry_visual_success_timer",self._confirm_retry_visual_success),("live_epg_timer",self._drain_live_epg_result),("zap_switch_timer",self._drain_zap_switch_result),
            ("stable_timer",self._mark_stream_stable),("stream_info_timer",self._update_stream_info),("vod_quality_timer",self._vod_quality_probe_tick),
            ("player_title_logo_timer",self._drain_player_title_logo),("hideTimer",self.doTimerHide),
        )
        for timer_name,callback in callback_map:
            timer=getattr(self,timer_name,None)
            if timer is None:continue
            try:
                if callback in timer.callback:timer.callback.remove(callback)
            except Exception as exc:optional_failure("player.timer_callback_remove",exc)
        try:
            callbacks=self.PicLoad.PictureData.get()
            if self._decode_poster in callbacks:callbacks.remove(self._decode_poster)
        except Exception as exc:optional_failure("player.picload_callback_remove",exc)
        try:
            if self._play_state_changed in self.onPlayStateChanged:self.onPlayStateChanged.remove(self._play_state_changed)
        except Exception as exc:optional_failure("player.playstate_callback_remove",exc)
        for hooks,callback,label in (
            (getattr(self,"onShow",None),self._resume_hidden_visual_timers,"player.show_timer_hook_remove"),
            (getattr(self,"onHide",None),self._pause_hidden_visual_timers,"player.hide_timer_hook_remove"),
        ):
            try:
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:optional_failure(label,exc)
        try:
            if self._online_subtitle_display is not None:
                self._online_subtitle_display.hideScreen()
                deleter=getattr(self.session,"deleteDialog",None)
                if callable(deleter):deleter(self._online_subtitle_display)
                self._online_subtitle_display=None
        except Exception as exc:optional_failure("player.native_online_subtitle_cleanup",exc)
        # Release decoded poster/picon buffers promptly on constrained receivers.
        for widget_name in ("logo","live_picon","adaptive_main","adaptive_poster","adaptive_live_picon","title_logo","vod_quality_badge","progress_neon"):
            try:
                widget=self[widget_name]
                if widget.instance is not None:widget.instance.setPixmap(None)
            except Exception as exc:optional_failure("player.optional_guard",exc)
