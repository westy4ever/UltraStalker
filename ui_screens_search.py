# -*- coding: utf-8 -*-
"""Clean poster-first global Movies/Series search."""
from __future__ import absolute_import
import hashlib
import os
import re
import queue
import threading
import time
import urllib.parse
import urllib.request
from .core.shared_executors import VISIBLE_ARTWORK_EXECUTOR
from collections import OrderedDict
import weakref

from . import _
from Screens.Screen import Screen
try:
    from Screens.VirtualKeyBoard import VirtualKeyBoard
except Exception:
    VirtualKeyBoard = None
try:
    from Screens.InputBox import InputBox
except Exception:
    InputBox = None
try:
    from Screens.ChoiceBox import ChoiceBox
except Exception:
    ChoiceBox = None

from .ui_async import AsyncScreenMixin
from .ui_transition import TransitionMixin
from .persistent_cache import load_detail_snapshot, load_shared_detail_snapshot
from .ui_artwork_helpers import _image_url
from . import ui_image_loader as _uil
from .category_visibility import hidden_ids as _hidden_category_ids
from .services.player_overlays import SubtitleGlassChoiceScreen
from .ui_settings_inline_choice import SettingsInlineChoiceOverlay
from .artwork_v2 import ArtworkV2, load_manifest as load_artwork_v2_manifest
from .log import memory_snapshot as _mem34

SEARCH_SKIN = ""
_SEARCH_ART_EXECUTOR = VISIBLE_ARTWORK_EXECUTOR


def configure_search_screen(**deps):
    globals().update(deps)
    if "SEARCH_SKIN" in deps:
        PortalGlobalSearchScreen.skin = deps["SEARCH_SKIN"]


def _norm_search(value):
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())

def _canonical_search_title(value):
    """Raw provider-name identity for Search grouping.

    Search is intentionally exempt from Clean Names.  Quality/language/server
    decorations often distinguish different playable copies, so grouping on the
    cleaned catalogue title made several genuinely different rows look identical.
    We only normalize case and whitespace; raw decorations remain significant.
    """
    text = str(value or "").strip().casefold()
    return " ".join(text.split())

def _title_contains_query(item, term):
    raw = str((item or {}).get("name") or (item or {}).get("title") or "")
    needle = _norm_search(term)
    return bool(needle and needle in _norm_search(raw))

def _row_category_id(item):
    row = item if isinstance(item, dict) else {}
    return str(row.get("category_id") or row.get("genre_id") or row.get("category") or "").strip()

def _fast_client_search_detached(client, term, limit, cancel_event, settings, time_budget=5.0, max_pages=16, on_partial=None):
    if cancel_event is not None and cancel_event.is_set():return []
    settings=settings if isinstance(settings,dict) else {}
    fn=getattr(client,"search_content_fast",None) or getattr(client,"search_content",None)
    if callable(fn):
        try:
            return fn(term,media_types=("vod","series"),limit=limit,max_pages=min(int(max_pages or 16),int(settings.get("search_max_pages",40) or 40)),cancel_event=cancel_event,time_budget=min(float(time_budget or 5.0),float(settings.get("search_time_budget",8) or 8)),on_partial=on_partial) or []
        except TypeError:
            # Older/alternate client implementations may not expose the
            # progressive callback yet. Preserve compatibility and publish the
            # completed provider batch once instead of abandoning its native
            # search path.
            try:
                rows = fn(term,media_types=("vod","series"),limit=limit,cancel_event=cancel_event) or []
                if rows and callable(on_partial):
                    try:on_partial(list(rows))
                    except Exception:pass
                return rows
            except Exception:pass
        except Exception:pass
    out=[];needle=_norm_search(term);deadline=time.monotonic()+max(1.0,float(time_budget or 5.0));max_local_pages=max(1,min(12,int(max_pages or 12)))
    for typ in ("vod","series"):
        if len(out)>=limit:break
        for page in range(1,max_local_pages+1):
            if time.monotonic()>=deadline or (cancel_event is not None and cancel_event.is_set()):break
            try:payload=client.ordered_page(typ,"*",page,cancel_event=cancel_event) or {}
            except Exception:break
            rows=[x for x in payload.get("items",[]) if isinstance(x,dict)]
            if not rows:break
            for raw in rows:
                hay=_norm_search(str(raw.get("name") or raw.get("title") or "")+" "+str(raw.get("description") or raw.get("descr") or ""))
                if needle and needle in hay:
                    item=dict(raw);item["_search_media_type"]=typ;out.append(item)
                    if callable(on_partial):
                        try:on_partial([dict(item)])
                        except Exception:pass
                    if len(out)>=limit:break
            if len(out)>=limit:break
    return out

def _valid(path):
    try:
        return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path)) > 100)
    except Exception:
        return False



class PortalGlobalSearchScreen(Screen, AsyncScreenMixin, TransitionMixin):
    skin = SEARCH_SKIN
    PAGE_SIZE = 7
    POSTER_SIZE = (206, 310)

    def __init__(self, session, profile, client):
        Screen.__init__(self, session)
        self._async_init()
        self.onClose.append(self._stop_async)
        self.profile = profile
        self.client = client
        self.results = []
        self.query = ""
        self.index = 0
        self._search_partial_jobs = queue.Queue()
        self._poster_jobs = queue.Queue()
        self._poster_generation = 0
        self._poster_cancel_event = threading.Event()
        self._poster_futures = set()
        self._poster_ready_cache = OrderedDict()
        self._search_seen = set()
        self._search_groups = OrderedDict()
        self._search_errors = []
        self._search_portal_count = 0
        self._search_source_mix = {"portal": 0, "xtream": 0}
        self._search_settings = {}
        self._search_generation = 0
        self._xtream_warm_generation = 0
        self._xtream_warm_cancel_event = threading.Event()
        self._xtream_warm_future = None
        self._render_signature = ()
        self._render_slot_signatures = [None] * self.PAGE_SIZE
        self._render_art_identities = [None] * self.PAGE_SIZE
        self._render_art_start = None
        self._render_selected_slot = None
        self["search_query"] = Label(_("Search"))
        self["search_portal_hits"] = Label("")
        self["search_xtream_hits"] = Label("")
        self["search_portal_mix"] = Label("")
        self["search_mix_dot"] = Label("")
        self["search_xtream_mix"] = Label("")
        self["search_status"] = Label("")
        self["page_label"] = Label("")
        self["search_button_label"] = Label(_("Search"))
        self["menu_hint"] = Label("MENU  •  " + _("Recent searches"))
        self._history_visible = False
        self._history_terms = []
        self._history_green_accent = "#32D57B"
        self._history_green_soft = "#8FE9BA"
        self._history_pending = None
        self._history_open_timer = eTimer()
        self._history_open_timer_conn = None
        try:
            self._history_open_timer_conn = self._history_open_timer.timeout.connect(self._history_open_deferred)
        except Exception:
            self._history_open_timer.callback.append(self._history_open_deferred)
        self.onClose.append(self._stop_history_open_timer)
        _asset_root = os.path.join(os.path.dirname(__file__), "assets_fhd")
        self._history_overlay = SettingsInlineChoiceOverlay(
            self, "history_inline_list", "history_inline_actions",
            lambda name: os.path.join(_asset_root, str(name or "")), _,
            lambda *args, **kwargs: None, action_priority=-20000
        )
        for i in range(self.PAGE_SIZE):
            self["result_art%d" % i] = Pixmap()
            self["result_title%d" % i] = Label("")
            self["result_meta%d" % i] = Label("")
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions", "MenuActions", "UltraStalkerMenuActions"],
            {
                "cancel": self._cancel_action,
                "red": self._cancel_action,
                "green": self._green_action,
                "menu": self._toggle_history,
                "ok": self._ok_action,
                "left": self._left_action,
                "right": self._right_action,
                "up": self._up_action,
                "down": self._down_action,
            },
            -1,
        )
        self._first_prompt_pending = True
        self._prompt_timer = eTimer()
        self._prompt_timer_conn = None
        try:
            self._prompt_timer_conn = self._prompt_timer.timeout.connect(self._open_first_prompt)
        except Exception:
            self._prompt_timer.callback.append(self._open_first_prompt)
        self.onClose.append(self._stop_prompt_timer)
        self.onClose.append(self._stop_search_hooks)
        self.onLayoutFinish.append(self._ready)
        self.onShown.append(self._shown)
        _mem34("search_init_done")

    def _ready(self):
        # Search is intentionally static/lightweight. The shared Palestine
        # background is already painted by the skin before this callback runs;
        # no Hero lookup, adaptive backdrop build, palette work or glass
        # generation is allowed on this screen.
        self._history_hide()
        self._render()

    def _shown(self):
        if not self._first_prompt_pending:
            return
        self._first_prompt_pending = False
        try:
            self.onShown.remove(self._shown)
        except Exception:
            pass
        try:
            self._prompt_timer.start(120, True)
        except Exception:
            self._open_first_prompt()

    def _open_first_prompt(self):
        if not getattr(self, "_screen_closed", False):
            self.prompt()

    def _stop_prompt_timer(self):
        try:
            self._prompt_timer.stop()
        except Exception:
            pass
        try:
            if self._prompt_timer_conn is not None:
                self._prompt_timer_conn.disconnect()
        except Exception:
            pass
        try:
            if self._open_first_prompt in self._prompt_timer.callback:
                self._prompt_timer.callback.remove(self._open_first_prompt)
        except Exception:
            pass

    def _stop_history_open_timer(self):
        try:self._history_open_timer.stop()
        except Exception:pass
        try:
            if self._history_open_timer_conn is not None:
                self._history_open_timer_conn.disconnect()
        except Exception:pass
        try:
            if self._history_open_deferred in self._history_open_timer.callback:
                self._history_open_timer.callback.remove(self._history_open_deferred)
        except Exception:pass
        self._history_pending = None

    def _history_hide(self):
        self._history_visible = False
        self._history_pending = None
        try:self._history_open_timer.stop()
        except Exception:pass
        try:
            if getattr(self, "_history_overlay", None) is not None:
                self._history_overlay.hide()
        except Exception:
            pass

    def _history_closed(self):
        self._history_visible = False

    def _history_status_left(self):
        """Anchor History 15px after the *rendered* green status text.

        The old probe label slightly overestimated the on-screen text width on
        receiver builds, leaving a visibly large empty gap.  Prefer the real
        skinned label instance so the overlay follows the actual Sources text,
        while the existing 654 bottom anchor keeps the exact 10px poster gap.
        """
        try:
            text = str(self["search_status"].getText() or "")
        except Exception:
            try:text = str(getattr(self["search_status"], "text", "") or "")
            except Exception:text = ""
        width = 0
        try:
            inst = self["search_status"].instance
            if inst is not None:
                size = inst.calculateSize()
                width = int(size.width())
        except Exception:
            width = 0
        if width <= 0:
            try:
                # The detached eLabel probe is consistently wider than the real
                # skinned label on FHD receivers; compensate only in fallback.
                width = int(round(float(self._history_overlay._measure(text, 18)) * 0.78))
            except Exception:
                width = int(len(text) * 8)
        # search_status begins at x=560.  Keep a deliberate 15px visual gap
        # before Recent searches, without changing its vertical/poster spacing.
        return max(720, min(1280, 560 + max(0, width) + 15))

    def _history_selected(self, choice):
        self._history_visible = False
        term = ""
        try:
            if isinstance(choice, (tuple, list)) and len(choice) > 1:
                term = str(choice[1] or "").strip()
            else:
                term = str(choice or "").strip()
        except Exception:
            term = ""
        if term:
            self._search(term)

    def _history_open_deferred(self):
        pending=self._history_pending
        self._history_pending=None
        if not pending or getattr(self,"_screen_closed",False):return
        overlay=getattr(self,"_history_overlay",None)
        if overlay is None:return
        choices,selected=pending
        fixed_green_source=os.path.join(os.path.dirname(__file__), "assets_fhd", "fixed_master_r63", "utility_row_selected.png")
        try:
            shown=overlay.show(
                choices, selection=selected, left_x=self._history_status_left(), anchor_bottom=654,
                on_accept=self._history_selected, on_close=self._history_closed,
                min_card_w=180, max_card_w=520, padding=38,
                visual_style="category", row_h=70, max_visible=5,
                adaptive_source=fixed_green_source,
                adaptive_accent=self._history_green_accent,
                adaptive_accent_soft=self._history_green_soft,
            )
            self._history_visible=bool(shown)
        except Exception:
            self._history_visible=False

    def _toggle_history(self):
        overlay = getattr(self, "_history_overlay", None)
        if overlay is None:
            return
        if self._history_visible or getattr(overlay, "active", False):
            try:overlay.close()
            except Exception:self._history_hide()
            return
        if self._history_pending is not None:
            self._history_pending=None
            try:self._history_open_timer.stop()
            except Exception:pass
            return
        try:terms=[str(x).strip() for x in (_load_recent_searches() or []) if str(x or "").strip()]
        except Exception:terms=[]
        if not terms:return
        self._history_terms=terms[:100]
        try:selected=self._history_terms.index(self.query) if self.query in self._history_terms else 0
        except Exception:selected=0
        self._history_pending=([(term,term) for term in self._history_terms],selected)
        # MENU is both the opener and an overlay-owned close key. Deferring one
        # event-loop turn prevents a single physical press from being consumed
        # twice by receiver images that re-evaluate ActionMaps mid-dispatch.
        try:self._history_open_timer.start(1,True)
        except Exception:self._history_open_deferred()

    def _history_move(self,delta):
        if not self._history_visible:return False
        try:return bool(self._history_overlay.move("up" if int(delta)<0 else "down"))
        except Exception:return False

    def _history_pick(self):
        if not self._history_visible:return False
        try:return bool(self._history_overlay.accept())
        except Exception:
            self._history_hide();return True

    def _cancel_action(self):
        if self._history_visible:
            try:self._history_overlay.close()
            except Exception:self._history_hide()
            return
        self.close()

    def _green_action(self):
        if self._history_visible:
            try:self._history_overlay.close()
            except Exception:self._history_hide()
        self.prompt()

    def _ok_action(self):
        if self._history_pick():return
        self.select()

    def _left_action(self):
        if self._history_visible:return
        self.move_left()

    def _right_action(self):
        if self._history_visible:return
        self.move_right()

    def _up_action(self):
        if self._history_move(-1):return
        self.page_left()

    def _down_action(self):
        if self._history_move(1):return
        self.page_right()

    def _advance_poster_generation(self):
        old_event=getattr(self,"_poster_cancel_event",None)
        if old_event is not None:
            try:old_event.set()
            except Exception:pass
        futures=getattr(self,"_poster_futures",None)
        if not isinstance(futures,set):
            futures=set();self._poster_futures=futures
        for future in list(futures):
            if future is None or future.done():
                futures.discard(future);continue
            try:future.cancel()
            except Exception as exc:optional_failure("ui.search_poster_cancel",exc)
            if future.done():futures.discard(future)
        self._poster_generation += 1
        self._poster_cancel_event = threading.Event()
        try:
            while True:self._poster_jobs.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:optional_failure("ui.search_poster_queue_drain",exc)
        return self._poster_generation,self._poster_cancel_event

    def _poster_ready_get(self, ident):
        cache=getattr(self,"_poster_ready_cache",None)
        if not isinstance(cache,OrderedDict):return ""
        path=str(cache.get(str(ident)) or "")
        if path and _valid(path):
            try:cache.move_to_end(str(ident))
            except Exception:pass
            return path
        cache.pop(str(ident),None)
        return ""

    def _poster_ready_put(self, ident, path):
        path=str(path or "")
        if not path or not _valid(path):return
        cache=getattr(self,"_poster_ready_cache",None)
        if not isinstance(cache,OrderedDict):
            cache=OrderedDict();self._poster_ready_cache=cache
        key=str(ident);cache[key]=path
        try:cache.move_to_end(key)
        except Exception:pass
        while len(cache)>56:
            try:cache.popitem(last=False)
            except Exception:break

    def _stop_search_hooks(self):
        _mem34("search_close", results=len(getattr(self,"results",[]) or []), ready_posters=len(getattr(self,"_poster_ready_cache",{}) or {}))
        self._cancel_xtream_warm()
        self._advance_poster_generation()
        for q in (self._search_partial_jobs, self._poster_jobs):
            try:
                while True:
                    q.get_nowait()
            except Exception:
                pass
        for i in range(self.PAGE_SIZE):
            try:
                art = self["result_art%d" % i]
                if art.instance is not None:
                    art.instance.setPixmap(None)
            except Exception:
                pass
        # PERF35: closed Search screens can stay referenced by Enigma2 for an
        # event-loop turn. Drop screen-local result/copy dictionaries now so a
        # large multi-portal result set is not retained during that grace period.
        try:self.results=[]
        except Exception:pass
        try:self._search_groups.clear()
        except Exception:pass
        try:self._search_seen.clear()
        except Exception:pass
        try:self._search_errors=[]
        except Exception:pass
        try:self._poster_ready_cache.clear()
        except Exception:pass
        try:self._search_settings={}
        except Exception:pass
        self._render_signature=()
        self._render_slot_signatures=[None]*self.PAGE_SIZE
        self._render_art_identities=[None]*self.PAGE_SIZE
        self._render_art_start=None

    def _drain_jobs(self):
        AsyncScreenMixin._drain_jobs(self)
        if self._screen_closed:
            return
        self._drain_search_partials()
        self._drain_poster_jobs()

    @staticmethod
    def _profile_key(profile):
        return (
            str((profile or {}).get("portal") or "").rstrip("/").lower(),
            str((profile or {}).get("mac") or "").upper(),
        )

    @staticmethod
    def _norm(value):
        return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())

    @staticmethod
    def _source_key(profile):
        p = profile if isinstance(profile, dict) else {}
        return (
            str(p.get("portal") or p.get("url") or p.get("host") or "").rstrip("/").lower(),
            str(p.get("mac") or "").upper(),
            str(p.get("username") or p.get("user") or ""),
            str(p.get("type") or p.get("kind") or "").lower(),
            str(p.get("name") or p.get("title") or ""),
        )

    def _group_key(self, item):
        typ = str((item or {}).get("_search_media_type") or "vod").lower()
        raw = (item or {}).get("name") or (item or {}).get("title") or ""
        title = _canonical_search_title(raw) or _norm_search(raw)
        return (typ, title)

    def _group_copies(self, item):
        rows = (item or {}).get("_search_copies") if isinstance(item, dict) else None
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)]
        return [item] if isinstance(item, dict) else []

    def _group_stats(self, item):
        copies = self._group_copies(item)
        servers = {self._source_key(row.get("_search_profile") or {}) for row in copies}
        return len(copies), len(servers)

    def _search_totals(self):
        copies = 0
        servers = set()
        for group in self.results:
            rows = self._group_copies(group)
            copies += len(rows)
            for row in rows:
                servers.add(self._source_key(row.get("_search_profile") or {}))
        return len(self.results), copies, len(servers)

    def _search_status_text(self, searching=False):
        titles, copies, servers = self._search_totals()
        if not titles:
            return _("Searching...") if searching else _("No matching content")
        prefix = (_("Searching...") + " " if searching else "")
        body=((_("%d results") % titles) + " • " + (_("%d sources") % servers))
        return prefix + body

    def _search_family_result_counts(self):
        """Count displayed result cards by family, with no double count.

        A federated card can own copies from both families. Search already paints
        such a card with the Xtream/M3U metadata colour, so the same rule owns
        it here. The two numbers therefore always add up to the visible result
        total instead of inflating the count when a title exists on both sides.
        """
        portal=0;xtream=0
        for group in list(getattr(self,"results",[]) or []):
            has_xtream=False
            for row in self._group_copies(group):
                profile=(row or {}).get("_search_profile") or {}
                source_type=str((row or {}).get("_search_source_type") or profile.get("source_type") or "").strip().lower()
                if source_type in ("xtream","m3u"):
                    has_xtream=True;break
            if has_xtream:xtream+=1
            else:portal+=1
        return portal,xtream

    @staticmethod
    def _visual_peer_year(item):
        row=item if isinstance(item,dict) else {}
        for value in (row.get("year"),row.get("release_year"),row.get("release_date"),row.get("releasedate"),row.get("first_air_date"),row.get("name"),row.get("title"),row.get("_raw_name")):
            match=re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)",str(value or ""))
            if match:return match.group(1)
        return ""

    def _visual_peer_title_keys(self,item):
        """Cheap Search-only title aliases for visual/cache ownership.

        Search keeps the raw provider label on screen.  For presentation reuse we
        also allow the same aggressive aliases already used only for TMDb search,
        e.g. ``Colony كلوني`` -> {``colony كلوني``, ``colony``}.  These keys are
        never used for playback ownership and are only consulted when Details is
        opened, so arrow/search hot paths remain untouched.
        """
        row=item if isinstance(item,dict) else {}
        raw=row.get("_raw_name") or row.get("name") or row.get("title") or ""
        keys=set()
        try:
            from .title_clean import tmdb_search_aliases
            for value in tmdb_search_aliases(raw) or []:
                key=_norm_search(value)
                if key and len(re.sub(r"[^\w\u0600-\u06ff]+","",key,flags=re.UNICODE))>=3:
                    keys.add(key)
        except Exception:
            pass
        try:title=premium_title(raw,True)
        except Exception:title=str(raw or "")
        title=_norm_search(title)
        title=re.sub(r"(?:^|\s)pure$","",title,flags=re.I).strip()
        if title:keys.add(title)
        return keys

    def _visual_peer_title(self,item):
        keys=self._visual_peer_title_keys(item)
        return sorted(keys,key=lambda value:(-len(value),value))[0] if keys else ""

    def _visual_peer_match(self,left,right):
        if not isinstance(left,dict) or not isinstance(right,dict):return False
        ltype=str(left.get("_search_media_type") or left.get("media_type") or "vod").lower()
        rtype=str(right.get("_search_media_type") or right.get("media_type") or "vod").lower()
        if ltype=="movie":ltype="vod"
        if rtype=="movie":rtype="vod"
        if ltype=="tv":ltype="series"
        if rtype=="tv":rtype="series"
        if ltype!=rtype:return False
        if not (self._visual_peer_title_keys(left) & self._visual_peer_title_keys(right)):return False
        ly=self._visual_peer_year(left);ry=self._visual_peer_year(right)
        if ly and ry:return ly==ry
        # Without a year, do not let a shortened multilingual alias prove a
        # remake/near-match.  Require the strongest cleaned title to agree.
        return self._visual_peer_title(left)==self._visual_peer_title(right)

    def _visual_peer_snapshot_compatible(self,item,snap):
        if not isinstance(item,dict) or not isinstance(snap,dict) or not snap.get("tmdb_id"):return False
        try:
            if identity_cache_compatible(item,snap):return True
        except Exception:
            pass
        probe={
            "_search_media_type":"series" if str(snap.get("media_type") or "").lower() in ("tv","series") else "vod",
            "name":snap.get("identity_catalogue_title") or snap.get("title") or snap.get("original_title") or snap.get("name") or "",
            "year":snap.get("identity_catalogue_year") or snap.get("year") or snap.get("release_date") or snap.get("first_air_date") or "",
        }
        return self._visual_peer_match(item,probe)

    def _cached_search_visual_seed(self,profile,typ,item):
        """Reuse an already-verified normal-catalogue identity on Search open.

        This is deliberately synchronous but tiny: a bounded handful of HDD JSON
        pointer reads only after OK/Details, never during typing, result rendering
        or arrow navigation.  It closes the raw-Search-name vs cleaned-catalogue
        gap without adding any background scan or TMDb request.
        """
        row=item if isinstance(item,dict) else {}
        year=self._visual_peer_year(row)
        aliases=list(self._visual_peer_title_keys(row))
        if not aliases:return {},{},-1
        # Prefer the strongest/full alias before a shortened Latin rescue key.
        aliases.sort(key=lambda value:(-len(value),value))
        best_snap={};best_bundle={};best_score=-1
        try:
            from .media_library import load_alias as _load_media_alias
        except Exception:
            return {},{},-1
        kind="tv" if str(typ or "").lower() in ("series","tv") else "movie"
        for alias in aliases[:4]:
            # A shortened cross-script alias is safe only when the provider gives
            # us a year.  Exact/full titles may still use the library's ambiguity
            # protected no-year pointer.
            if not year and alias!=self._visual_peer_title(row):
                continue
            try:snap=_load_media_alias(kind,alias,year or None) or {}
            except Exception:snap={}
            if not isinstance(snap,dict) or not snap.get("tmdb_id") or not snap.get("identity_pointer_verified"):
                continue
            if not self._visual_peer_snapshot_compatible(row,snap):
                continue
            trusted=dict(row);trusted["_ultra_search_verified_tmdb_id"]=snap.get("tmdb_id")
            try:bundle=_load_visual_bundle(profile,typ,trusted,snapshot=snap) or {}
            except Exception:bundle={}
            poster=str(bundle.get("poster") or snap.get("poster_local") or "")
            backdrop=str(bundle.get("backdrop") or snap.get("backdrop_local") or "")
            chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
            overlay=snap.get("item_overlay") if isinstance(snap.get("item_overlay"),dict) else {}
            score=0
            if poster and os.path.isfile(poster):score+=2
            if backdrop and os.path.isfile(backdrop):score+=2
            if chrome.get("panel_detail") and chrome.get("overview_detail"):score+=2
            if str(snap.get("overview") or overlay.get("description") or "").strip():score+=1
            if snap.get("identity_verified") or snap.get("identity_pointer_verified") or snap.get("_details_authority_ready"):score+=1
            if score>best_score:
                best_snap=dict(snap);best_bundle=dict(bundle);best_score=score
        return best_snap,best_bundle,best_score

    @staticmethod
    def _clean_search_copy(row):
        clean=dict(row or {})
        for key in ("_search_profile","_search_portal_name","_search_source_type","_search_group","_search_group_key","_search_group_title","_search_copies","_search_visual_peers","_search_seed_snapshot","_search_seed_trusted","_search_force_complete"):
            clean.pop(key,None)
        return clean

    def _search_visual_peers_for(self,item):
        peers=[];seen=set()
        for group in list(getattr(self,"results",[]) or []):
            for row in self._group_copies(group):
                if not self._visual_peer_match(item,row):continue
                profile=row.get("_search_profile") or {}
                typ=str(row.get("_search_media_type") or item.get("_search_media_type") or "vod").lower()
                clean=self._clean_search_copy(row)
                ident=(self._source_key(profile),typ,str(clean.get("id") or clean.get("stream_id") or clean.get("movie_id") or clean.get("series_id") or clean.get("cmd") or clean.get("url") or clean.get("name") or clean.get("title") or ""))
                if ident in seen:continue
                seen.add(ident);peers.append({"profile":dict(profile or {}),"media_type":typ,"item":clean,"search_match_verified":True})
        return peers

    def _best_search_visual_seed(self,selected,peers):
        best_score=-1;best_snap={};best_bundle={}
        for peer in peers or []:
            profile=peer.get("profile") or {};typ=peer.get("media_type") or "vod";clean=peer.get("item") or {}
            try:snap=load_shared_detail_snapshot(profile,typ,clean) or {}
            except Exception:snap={}
            if not isinstance(snap,dict) or not snap.get("tmdb_id"):continue
            if not self._visual_peer_snapshot_compatible(selected,snap):continue
            trusted=dict(clean);trusted["_ultra_search_verified_tmdb_id"]=snap.get("tmdb_id")
            try:bundle=_load_visual_bundle(profile,typ,trusted,snapshot=snap) or {}
            except Exception:bundle={}
            poster=str(bundle.get("poster") or snap.get("poster_local") or "")
            backdrop=str(bundle.get("backdrop") or snap.get("backdrop_local") or "")
            chrome=bundle.get("chrome") if isinstance(bundle.get("chrome"),dict) else {}
            overlay=snap.get("item_overlay") if isinstance(snap.get("item_overlay"),dict) else {}
            score=0
            if poster and os.path.isfile(poster):score+=2
            if backdrop and os.path.isfile(backdrop):score+=2
            if chrome.get("panel_detail") and chrome.get("overview_detail"):score+=2
            if str(snap.get("overview") or overlay.get("description") or "").strip():score+=1
            if snap.get("_details_authority_ready") or snap.get("identity_verified"):score+=1
            if score>best_score:
                best_score=score;best_snap=dict(snap);best_bundle=dict(bundle)
        return best_snap,best_bundle,best_score

    def _set_source_mix_labels(self):
        mix=getattr(self,"_search_source_mix",{}) or {}
        pcount=int(mix.get("portal",0) or 0);xcount=int(mix.get("xtream",0) or 0)
        visible=bool(pcount or xcount)
        portal_text=("%d PORTAL" % pcount) if visible else ""
        dot_text="•" if visible else ""
        xtream_text=("%d XTREAM" % xcount) if visible else ""
        portal_hits,xtream_hits=self._search_family_result_counts()
        have_results=bool(getattr(self,"results",None))
        self._set_label_if_changed(self["search_portal_hits"],str(portal_hits) if (have_results and pcount) else "")
        self._set_label_if_changed(self["search_xtream_hits"],str(xtream_hits) if (have_results and xcount) else "")
        self._set_label_if_changed(self["search_portal_mix"],portal_text)
        self._set_label_if_changed(self["search_mix_dot"],dot_text)
        self._set_label_if_changed(self["search_xtream_mix"],xtream_text)
        # Keep the family summary as three independent colour lanes. Result-card
        # counts sit exactly above their family labels with a 2px vertical gap.
        try:
            width=int(self._history_overlay._measure(portal_text,17)) if portal_text else 0
        except Exception:
            width=max(0,len(portal_text)*9)
        try:
            xwidth=int(self._history_overlay._measure(xtream_text,17)) if xtream_text else 0
        except Exception:
            xwidth=max(0,len(xtream_text)*9)
        try:
            portal_x=560
            dot_x=portal_x+max(0,width)+18
            xtream_x=dot_x+28
            dot_inst=self["search_mix_dot"].instance
            xtream_inst=self["search_xtream_mix"].instance
            ph_inst=self["search_portal_hits"].instance
            xh_inst=self["search_xtream_hits"].instance
            if dot_inst is not None:dot_inst.move(ePoint(dot_x,518))
            if xtream_inst is not None:xtream_inst.move(ePoint(xtream_x,518))
            if ph_inst is not None:ph_inst.move(ePoint(max(0,portal_x+max(0,width)//2-45),488))
            if xh_inst is not None:xh_inst.move(ePoint(max(0,xtream_x+max(0,xwidth)//2-45),488))
        except Exception as exc:
            optional_failure("ui.search_source_mix_position",exc)

    def _cancel_xtream_warm(self):
        try:self._xtream_warm_cancel_event.set()
        except Exception:pass
        future=getattr(self,"_xtream_warm_future",None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        self._xtream_warm_future=None

    def _schedule_xtream_result_warm(self, generation):
        # R181 performance recovery: intentionally disabled. Search results must
        # become completely idle once rendered; no hidden catalogue enrichment.
        return

    def _merge_search_rows(self, rows):
        changed = 0
        groups = getattr(self, "_search_groups", None)
        if not isinstance(groups, OrderedDict):
            groups = OrderedDict(); self._search_groups = groups
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            p = item.get("_search_profile") or {}
            typ = item.get("_search_media_type") or "vod"
            identity = (
                item.get("id")
                or item.get("movie_id")
                or item.get("series_id")
                or item.get("cmd")
                or item.get("url")
                or item.get("name")
                or item.get("title")
            )
            copy_key = (self._source_key(p), typ, str(identity))
            if copy_key in self._search_seen:
                continue
            self._search_seen.add(copy_key)
            gkey = self._group_key(item)
            group = groups.get(gkey)
            if group is None:
                group = dict(item)
                group["_search_group"] = True
                group["_search_group_key"] = gkey
                group["_search_group_title"] = str(item.get("_raw_name") or item.get("name") or item.get("title") or "").strip()
                group["_search_copies"] = []
                groups[gkey] = group
                self.results.append(group)
            group["_search_copies"].append(item)
            # If the first source lacked artwork but a later copy has it, let the
            # single grouped card inherit only the artwork-related fields.
            try:
                if not _image_url(group) and _image_url(item):
                    # Promote the later copy to representative so relative artwork
                    # URLs stay paired with the profile they came from.
                    copies_ref = group.get("_search_copies")
                    group_title = group.get("_search_group_title")
                    group_key = group.get("_search_group_key")
                    group.update(item)
                    group["_search_group"] = True
                    group["_search_group_key"] = group_key
                    group["_search_group_title"] = group_title
                    group["_search_copies"] = copies_ref
            except Exception:
                pass
            changed += 1
        return changed

    def _fast_client_search(self, client, term, limit, cancel_event, time_budget=5.0, max_pages=16):
        return _fast_client_search_detached(client,term,limit,cancel_event,self._search_settings,time_budget,max_pages)

    def _open_recent_search_menu_for_keyboard(self, keyboard):
        try:
            recent = [str(x).strip() for x in (_load_recent_searches() or []) if str(x or "").strip()]
        except Exception:
            recent = []
        if not recent or ChoiceBox is None:
            return
        choices = [(term, term) for term in recent[:100]]

        def picked(choice):
            if not choice:
                return
            try:
                term = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice[0]
            except Exception:
                term = choice
            term = str(term or "").strip()
            if not term:
                return
            try:
                keyboard.close(term)
            except Exception as exc:
                optional_failure("ui.search_recent_keyboard_close", exc)

        try:
            # R36: the parent Search screen can temporarily lose its `session`
            # attribute while the stock VirtualKeyBoard owns the modal stack.
            # Use the active keyboard's session first, which remains valid for
            # the lifetime of the keyboard, then fall back to the parent only.
            owner_session = getattr(keyboard, "session", None) or getattr(self, "session", None)
            if owner_session is None:
                raise RuntimeError("No active session for recent search menu")
            owner_session.openWithCallback(picked, ChoiceBox, title=_("Recent searches"), list=choices)
        except Exception as exc:
            optional_failure("ui.search_recent_menu", exc)

    def _attach_recent_menu_to_stock_keyboard(self, keyboard):
        """Keep the keyboard's existing black MENU history, but release it cleanly.

        The map is attached after the stock keyboard starts, so it needs one
        manual execBegin(). R159 pairs that with a guaranteed execEnd() on close;
        otherwise MENU can remain captured by a dead keyboard after returning to
        Search and the inline fixed-green history never receives the key again.
        """
        if keyboard is None:
            return
        try:
            amap = ActionMap(
                ["MenuActions","UltraStalkerMenuActions"],
                {"menu": lambda: self._open_recent_search_menu_for_keyboard(keyboard)},
                -1000,
            )
            keyboard["ultrastalker_recent_search_actions"] = amap
            try:amap.execBegin()
            except Exception as exc:optional_failure("ui.search_recent_keyboard_exec", exc)

            def cleanup():
                try:amap.execEnd()
                except Exception:pass
                try:
                    if getattr(keyboard, "widgets", {}).get("ultrastalker_recent_search_actions") is amap:
                        keyboard.widgets.pop("ultrastalker_recent_search_actions", None)
                except Exception:pass
            try:keyboard.onClose.append(cleanup)
            except Exception:pass
        except Exception as exc:
            optional_failure("ui.search_recent_keyboard_action", exc)

    def prompt(self):
        self._history_hide()
        recent = _load_recent_searches()
        title = _("Search Movies & Series")
        if recent:
            title += "  •  " + _("Recent: %s") % ", ".join(recent[:3])
        if VirtualKeyBoard is not None:
            # Always use the receiver's native class so its stock skin renders.
            keyboard = self.session.openWithCallback(self._search, VirtualKeyBoard, title=title, text=self.query)
            self._attach_recent_menu_to_stock_keyboard(keyboard)
        elif InputBox is not None:
            self.session.openWithCallback(self._search, InputBox, title=title, text=self.query, maxSize=60)
        else:
            self.session.open(MessageBox, _("Search keyboard is unavailable on this image."), MessageBox.TYPE_ERROR, timeout=5)

    def _search(self, term=None):
        self._history_hide()
        term = one_line(term or "", 60)
        if not term:
            return
        # A new query owns the screen immediately. Cancel any older provider
        # request before clearing the UI so stale callbacks can never leak rows
        # into the new search.
        if getattr(self, "_busy", False) or getattr(self, "_active_async_handle", None) is not None:
            try:self._cancel_active_async()
            except Exception:pass
        self._cancel_xtream_warm()
        self._search_generation = int(getattr(self, "_search_generation", 0) or 0) + 1
        generation = self._search_generation
        self.query = term
        _save_recent_search(term)
        cfg = load_settings()
        self._search_settings = dict(cfg)
        self.results = []
        self.index = 0
        # Cancel poster work from the previous query immediately. Per-slot
        # identities below then keep partial results on the same page incremental.
        self._advance_poster_generation()
        # Keep the current slot snapshots until the empty render below has
        # actively cleared the previous query from screen. Resetting them here
        # would make the renderer believe old posters/labels were already empty.
        self._render_art_start = None
        self._render_signature = ()
        self._search_seen = set()
        self._search_groups = OrderedDict()
        self._search_errors = []
        self._search_portal_count = 0
        self._search_source_mix = {"portal":0,"xtream":0}
        self._set_source_mix_labels()
        try:
            while True:
                self._search_partial_jobs.get_nowait()
        except Exception:
            pass
        self["search_query"].setText(_("Search: %s") % term)
        self._set_search_status(_("Searching..."), "#c9d9e5")
        self._render()
        multi = bool(cfg.get("multi_portal_search", True))
        limit = max(20, min(100, int(cfg.get("content_page_size", 50) or 50)))

        search_profile_base=dict(self.profile or {})
        search_settings=dict(self._search_settings or {})
        partial_queue=self._search_partial_jobs
        def profile_key(profile):
            return (str((profile or {}).get("portal") or "").rstrip("/").lower(),str((profile or {}).get("mac") or "").upper())
        def work(handle):
            profiles = load_profiles() if multi else [search_profile_base]
            current_key = profile_key(search_profile_base)
            profiles = [p for p in profiles if isinstance(p, dict)]
            if not profiles:
                profiles = [search_profile_base]

            def source_priority(p):
                # Current source first, then Xtream/M3U immediately, then the
                # remaining Stalker portals.  The previous serial-current +
                # queued-rest layout could leave the Xtream source at the end
                # of a 17-portal queue until the wall-clock deadline expired.
                if profile_key(p) == current_key:
                    return (0, 0)
                if str((p or {}).get("source_type") or "stalker").lower() == "m3u":
                    return (1, 0)
                return (2, 0)

            profiles = sorted(profiles, key=source_priority)
            source_mix={"portal":sum(1 for p in profiles if str((p or {}).get("source_type") or "stalker").lower()!="m3u"),
                        "xtream":sum(1 for p in profiles if str((p or {}).get("source_type") or "stalker").lower()=="m3u")}
            partial_queue.put((generation, [], None, len(profiles), source_mix))
            all_rows = []
            errors = []
            source_count = len(profiles)

            local_cancel = threading.Event()
            active_clients = set()
            active_clients_lock = threading.RLock()

            def stop_local_workers():
                local_cancel.set()
                try:
                    with active_clients_lock:
                        clients = list(active_clients)
                except Exception:
                    clients = []
                for owned_client in clients:
                    try: owned_client.close()
                    except Exception: pass

            try:
                handle.add_cancel_callback(stop_local_workers)
            except Exception:
                pass

            def search_profile(p, pos, budget=5.0, pages=16):
                if handle.cancelled() or local_cancel.is_set():
                    return [], None
                cl = None
                hidden_vod = _hidden_category_ids(search_settings, p, "vod")
                hidden_series = _hidden_category_ids(search_settings, p, "series")
                source_type = str((p or {}).get("source_type") or "stalker").lower()
                default_name = ("Xtream %d" if source_type == "m3u" else "Server %d") % (pos + 1)
                portal_name = str(p.get("name") or default_name)

                def tag_rows(rows):
                    tagged = []
                    for row in rows or []:
                        if not isinstance(row, dict) or not _title_contains_query(row, term):
                            continue
                        item = dict(row)
                        media = str(item.get("_search_media_type") or row.get("_search_media_type") or "vod").lower()
                        cid = _row_category_id(item)
                        hidden = hidden_series if media == "series" else hidden_vod
                        if cid and cid in hidden:
                            continue
                        item["_search_profile"] = dict(p)
                        item["_search_portal_name"] = portal_name
                        item["_search_source_type"] = "xtream" if source_type == "m3u" else "portal"
                        tagged.append(item)
                    return tagged

                def publish_partial(rows):
                    if handle.cancelled() or local_cancel.is_set() or generation != getattr(self, "_search_generation", generation):
                        return
                    tagged = tag_rows(rows)
                    if tagged:
                        partial_queue.put((generation, tagged, None, source_count))

                try:
                    cl = _client_from_profile(p, timeout=min(4, int(cfg.get("timeout", 10) or 10)))
                    with active_clients_lock:
                        active_clients.add(cl)
                    rows = _fast_client_search_detached(
                        cl, term, limit, local_cancel, search_settings,
                        time_budget=budget, max_pages=pages, on_partial=publish_partial,
                    )
                    return tag_rows(rows), None
                except Exception as exc:
                    if local_cancel.is_set() or handle.cancelled():
                        return [], None
                    return [], "%s: %s" % (p.get("name") or p.get("portal") or default_name, exc)
                finally:
                    if cl is not None:
                        try:
                            with active_clients_lock:
                                active_clients.discard(cl)
                        except Exception:
                            pass
                        try: cl.close()
                        except Exception: pass

            # All sources start from one queue.  This is the key federation
            # change: an Xtream source is no longer held behind a synchronous
            # Stalker search.  Progressive callbacks from either family can
            # paint the screen immediately.
            jobs = queue.Queue()
            for pos, p in enumerate(profiles):
                jobs.put((pos, p))
            result_lock = threading.RLock()
            stop = threading.Event()
            deadline = time.monotonic() + (42.0 if source_mix.get("xtream") else 18.0)

            def worker():
                while not stop.is_set() and not handle.cancelled() and not local_cancel.is_set() and time.monotonic() < deadline:
                    try:
                        pos, p = jobs.get_nowait()
                    except queue.Empty:
                        return
                    source_type = str((p or {}).get("source_type") or "stalker").lower()
                    is_current = profile_key(p) == current_key
                    # Xtream catalogues can be large; they get a full-catalogue
                    # page allowance and start immediately, while ordinary
                    # secondary portals keep the lean search budget.
                    # Stalker now runs native search for Movies + Series before
                    # any catalogue augmentation. Give that best-effort second
                    # phase enough room without delaying progressive native hits.
                    budget = 36.0 if source_type == "m3u" else (10.0 if is_current else 6.0)
                    pages = 80 if source_type == "m3u" else (24 if is_current else 16)
                    tagged, err = search_profile(p, pos, budget=budget, pages=pages)
                    if stop.is_set() or handle.cancelled() or local_cancel.is_set():
                        return
                    with result_lock:
                        if err:
                            errors.append(err)
                        if tagged:
                            all_rows.extend(tagged)
                            partial_queue.put((generation, tagged, None, source_count))

            threads = []
            for idx in range(min(6, jobs.qsize())):
                thread = threading.Thread(target=worker, name="ultrastalker-search-source-%d" % idx, daemon=True)
                thread.start()
                threads.append(thread)

            while time.monotonic() < deadline and not handle.cancelled():
                if not any(t.is_alive() for t in threads):
                    break
                time.sleep(0.05)
            stop.set()
            stop_local_workers()
            grace = time.monotonic() + 1.5
            for thread in threads:
                remain = grace - time.monotonic()
                if remain <= 0:
                    break
                thread.join(remain)
            try:
                alive = sum(1 for thread in threads if thread.is_alive())
                _mem34("search_workers_settled", source_threads_alive=alive, source_threads_total=len(threads), active_clients=len(active_clients))
            except Exception:
                pass
            return list(all_rows), list(errors), source_count, source_mix

        def ok(result):
            if generation != getattr(self, "_search_generation", generation):
                return
            rows, errors, count, mix = result
            self._search_portal_count = count
            self._search_source_mix = dict(mix or {})
            self._set_source_mix_labels()
            self._merge_search_rows(rows)
            self._search_errors = list(dict.fromkeys(self._search_errors + errors))
            self._render()
            final_text = self._search_status_text(False)
            if self.results:
                final_text = _("Done") + "  •  " + final_text
            self._set_search_status(final_text, "#00E676" if self.results else "#c9d9e5")
            self._set_source_mix_labels()
            # R181: no speculative provider/TMDb/details warm-up after Search.
            # The selected title alone owns hydration when the user opens it.
            _mem34("search_finished", results=len(self.results), ready_posters=len(getattr(self,"_poster_ready_cache",{}) or {}), portals=int(self._search_portal_count or 0))

        self._run_async(work, ok, lambda e: self._set_search_status(_friendly_error(e), "#ff8a80"))

    def _drain_search_partials(self):
        changed = False
        while True:
            try:
                job = self._search_partial_jobs.get_nowait()
                if len(job) >= 5:
                    job_generation, rows, error, count, mix = job[:5]
                elif len(job) >= 4:
                    job_generation, rows, error, count = job[:4];mix=None
                else:
                    job_generation = getattr(self, "_search_generation", 0)
                    rows, error, count = job[:3];mix=None
            except queue.Empty:
                break
            except Exception:
                break
            if int(job_generation or 0) != int(getattr(self, "_search_generation", 0) or 0):
                continue
            self._search_portal_count = max(self._search_portal_count, int(count or 0))
            if isinstance(mix,dict):
                self._search_source_mix=dict(mix);self._set_source_mix_labels()
            if error:
                self._search_errors.append(error)
            if self._merge_search_rows(rows):
                changed = True
        if changed:
            self._render()
            self._set_source_mix_labels()
            self._set_search_status(self._search_status_text(True), "#c9d9e5")

    def _visible_range(self):
        page = self.index // self.PAGE_SIZE if self.results else 0
        start = page * self.PAGE_SIZE
        return page, start, self.results[start : start + self.PAGE_SIZE]

    @staticmethod
    def _set_label_if_changed(widget, value):
        text = str(value or "")
        try:
            if widget.getText() == text:
                return False
        except Exception:
            pass
        widget.setText(text)
        return True

    def _set_search_status(self, value, color="#c9d9e5"):
        self._set_label_if_changed(self["search_status"], value)
        try:
            inst = self["search_status"].instance
            if inst is not None:
                inst.setForegroundColor(parseColor(str(color)))
        except Exception as exc:
            optional_failure("ui.search_status_color", exc)

    def _set_result_focus(self, slot, selected):
        """One lightweight focus cue: selected title turns green.

        This remains visible even while its poster is still downloading, so
        navigation never depends on artwork readiness. No frame/pixmap is built.
        """
        try:
            widget=self["result_title%d" % int(slot)]
            if widget.instance is not None:
                widget.instance.setForegroundColor(parseColor("#00E676" if selected else "#FFFFFF"))
        except Exception as exc:
            optional_failure("ui.search_focus_text",exc)

    def _result_meta_text(self, item):
        copies, sources = self._group_stats(item)
        typ=str((item or {}).get("_search_media_type") or "vod").lower()
        label=_("SERIES") if typ=="series" else _("MOVIE")
        line="%s  •  %s • %s" % (label, (_("%d results") % copies), (_("%d sources") % sources))
        if sources==1:
            source_name=""
            for row in self._group_copies(item):
                source_name=str((row or {}).get("_search_portal_name") or ((row or {}).get("_search_profile") or {}).get("name") or "").strip()
                if source_name:
                    break
            if source_name:
                if len(source_name)>30:
                    source_name=source_name[:29].rstrip()+"…"
                line += "\n" + source_name
        return line

    def _result_meta_color(self, item):
        # Portal-only grouped results keep the established red metadata line.
        # If any copy in the federated group belongs to Xtream/M3U, make that
        # line white so the source family is visible without changing the text.
        copies=self._group_copies(item)
        for row in copies:
            source_type=str((row or {}).get("_search_source_type") or "").strip().lower()
            if source_type in ("xtream","m3u"):
                return "#FFFFFF"
            profile=(row or {}).get("_search_profile") or {}
            if str((profile or {}).get("source_type") or "").strip().lower()=="m3u":
                return "#FFFFFF"
        return "#FF5A5F"

    def _render(self):
        query_text = (_("Search: %s") % self.query) if self.query else _("Search")
        self._set_label_if_changed(self["search_query"], query_text)
        page, start, rows = self._visible_range()
        pages = max(1, (len(self.results) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        titles, copies, servers = self._search_totals()
        page_text = ((_("Page %d / %d  •  %d result(s)") % (page + 1, pages, titles)) if self.results else "")
        self._set_label_if_changed(self["page_label"], page_text)

        # A page change invalidates queued poster downloads; partial results that
        # merely append to the current page do not. The old renderer bumped one
        # global generation whenever the page signature changed, repeatedly
        # cancelling and rescheduling posters already in flight.
        if getattr(self, "_render_art_start", None) != start:
            self._advance_poster_generation()
            self._render_art_start = start
        generation = self._poster_generation

        slot_signatures = list(getattr(self, "_render_slot_signatures", [None] * self.PAGE_SIZE))
        art_identities = list(getattr(self, "_render_art_identities", [None] * self.PAGE_SIZE))
        if len(slot_signatures) != self.PAGE_SIZE:slot_signatures = [None] * self.PAGE_SIZE
        if len(art_identities) != self.PAGE_SIZE:art_identities = [None] * self.PAGE_SIZE

        current_signature=[]
        for slot in range(self.PAGE_SIZE):
            art = self["result_art%d" % slot]
            title = self["result_title%d" % slot]
            meta = self["result_meta%d" % slot]
            if slot >= len(rows):
                current_signature.append("")
                if slot_signatures[slot] is not None or art_identities[slot] is not None:
                    try:
                        if art.instance is not None:
                            art.instance.setPixmap(None)
                    except Exception:
                        pass
                    art.hide()
                    self._set_label_if_changed(title, "")
                    self._set_label_if_changed(meta, "")
                    slot_signatures[slot] = None
                    art_identities[slot] = None
                self._set_result_focus(slot,False)
                continue

            item = rows[slot]
            ident = self._poster_identity(item)
            current_signature.append(ident)
            # Search always shows the exact provider name, independent from the
            # global Clean Names preference.  This is how users can distinguish
            # multiple 4K/FHD/language/server variants of the same cleaned title.
            raw = ((item.get("_search_group_title") if item.get("_search_group") else None) or item.get("_raw_name") or item.get("name") or item.get("title") or _("Result"))
            title_text = str(raw).strip()[:52]
            meta_text = self._result_meta_text(item)
            meta_color = self._result_meta_color(item)
            # Include the artwork source/copy count in the signature. Native
            # search often returns a sparse first copy, then a later portal adds
            # the usable poster URL. Requeue that card when its source improves.
            try:art_source=str(_image_url(item) or "")
            except Exception:art_source=""
            slot_signature = (ident, title_text, meta_text, meta_color, art_source)
            if slot_signatures[slot] != slot_signature:
                self._set_label_if_changed(title, title_text)
                self._set_label_if_changed(meta, meta_text)
                try:
                    if meta.instance is not None:
                        meta.instance.setForegroundColor(parseColor(meta_color))
                except Exception as exc:
                    optional_failure("ui.search_result_meta_color",exc)
                # If a later partial result promoted real artwork onto the same
                # logical title, allow one fresh poster attempt without changing
                # Search result speed/order.
                previous=slot_signatures[slot]
                source_changed=bool(previous and len(previous)>4 and previous[4] != art_source)
                slot_signatures[slot] = slot_signature
                if source_changed and art_identities[slot] == ident:
                    art_identities[slot] = None

            if art_identities[slot] != ident:
                try:
                    if art.instance is not None:
                        art.instance.setPixmap(None)
                    art.hide()
                except Exception:
                    pass
                art_identities[slot] = ident
                self._schedule_poster(generation, slot, item)

        self._render_signature = tuple(current_signature)

        # The only selection visual is the selected title itself turning green.
        # It exists before the poster arrives, so there is always exactly one
        # visible focus cue and no per-card frame/green line to generate.
        selected_slot = self.index - start if start <= self.index < start + len(rows) else None
        old_slot = getattr(self, "_render_selected_slot", None)
        if old_slot != selected_slot:
            if old_slot is not None and 0 <= old_slot < self.PAGE_SIZE:
                self._set_result_focus(old_slot,False)
            if selected_slot is not None and 0 <= selected_slot < self.PAGE_SIZE:
                self._set_result_focus(selected_slot,True)
            self._render_selected_slot = selected_slot
        elif selected_slot is not None:
            self._set_result_focus(selected_slot,True)
        self._render_slot_signatures = slot_signatures
        self._render_art_identities = art_identities

    def _poster_identity(self, item):
        if isinstance(item, dict) and item.get("_search_group"):
            return "group:%s:%s" % tuple(item.get("_search_group_key") or self._group_key(item))
        return "%s:%s:%s" % (
            self._profile_key(item.get("_search_profile") or self.profile),
            item.get("_search_media_type") or "vod",
            item.get("id")
            or item.get("movie_id")
            or item.get("series_id")
            or item.get("name")
            or item.get("title")
            or "",
        )

    def _absolute_art_url(self, value, profile):
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("//"):
            scheme = urllib.parse.urlsplit(str((profile or {}).get("portal") or "http://")).scheme or "http"
            return scheme + ":" + text
        if text.startswith(("http://", "https://")):
            return text
        return urllib.parse.urljoin(
            str((profile or {}).get("portal") or "").rstrip("/") + "/", text.lstrip("/")
        )

    def _local_search_poster(self, item):
        # A grouped search card may have several portal copies. Check the
        # canonical ArtworkV2 manifest and detail snapshots for every copy, not
        # only the first sparse native-search row.
        rows=self._group_copies(item)
        if not rows:rows=[item]
        for row in rows:
            if not isinstance(row,dict):continue
            profile = row.get("_search_profile") if isinstance(row.get("_search_profile"), dict) else self.profile
            typ = row.get("_search_media_type") or item.get("_search_media_type") or "vod"
            clean = dict(row)
            clean.pop("_search_profile", None);clean.pop("_search_portal_name", None)
            candidates=[]
            snapshots=[]
            try:
                snap=load_artwork_v2_manifest(profile,typ,clean) or {}
                snapshots.append(snap);candidates.append(str(snap.get("poster_local") or ""))
            except Exception:pass
            try:
                snap = load_shared_detail_snapshot(profile, typ, clean) if clean.get("_xtream") else load_detail_snapshot(profile, typ, clean)
                snapshots.append(snap or {});candidates.append(str((snap or {}).get("poster_local") or ""))
            except Exception:pass
            # The Search card already paid for these tiny pointer reads. Reuse a
            # verified id on OK so Details can skip the extra TMDb title-search
            # round trip. This mutates only the in-memory result row; playback
            # id/cmd/url remain untouched and no new HDD scan is introduced.
            for snap in snapshots:
                try:
                    if not isinstance(snap,dict) or not snap.get("tmdb_id"):continue
                    trusted=bool(snap.get("identity_pointer_verified") or snap.get("identity_verified") or snap.get("_details_authority_ready"))
                    if not trusted or not identity_cache_compatible(clean,snap):continue
                    row["_locked_tmdb_id"]=snap.get("tmdb_id")
                    row["_locked_tmdb_type"]=snap.get("media_type") or ("tv" if str(typ).lower() in ("series","tv") else "movie")
                    row["_ultra_search_verified_tmdb_id"]=snap.get("tmdb_id")
                    break
                except Exception:
                    continue
            for p in candidates:
                if not _valid(p):continue
                digest = hashlib.sha1((p + "|search206x310").encode("utf-8", "ignore")).hexdigest()
                thumb = _uil._thumb_path(digest, self.POSTER_SIZE)
                if _valid(thumb):return thumb
                try:return _uil._build_thumbnail(p, thumb, self.POSTER_SIZE) or p
                except Exception:return p
        return ""

    def _schedule_poster(self, generation, slot, item):
        """Hydrate each visible Search poster without focus or heavy UI chrome.

        Provider originals are temporary: only the receiver-sized 206x310
        derivative survives in the Search cache. If the native search row is
        sparse, try other copies of the same title, then the canonical ArtworkV2
        w342 path. This keeps Search fast while making artwork deterministic.
        """
        ident = self._poster_identity(item)
        ready=self._poster_ready_get(ident)
        if ready:
            try:self._poster_jobs.put((generation,slot,ident,ready))
            except Exception:pass
            return
        local = self._local_search_poster(item)
        if local:
            self._poster_ready_put(ident,local)
            try:self._poster_jobs.put((generation, slot, ident, local))
            except Exception:pass
            return

        # Prepare a small unique candidate list on the UI thread. Prefer copies
        # that already advertise artwork, because native MAG search rows are
        # often sparse while another source copy contains a usable cover URL.
        rows=self._group_copies(item)
        if not rows:rows=[item]
        prepared=[];seen=set()
        def add_candidate(row):
            if not isinstance(row,dict):return
            profile=row.get("_search_profile") if isinstance(row.get("_search_profile"),dict) else self.profile
            typ=str(row.get("_search_media_type") or item.get("_search_media_type") or "vod").lower()
            clean=dict(row)
            for key in ("_search_profile","_search_portal_name","_search_group","_search_group_key","_search_group_title","_search_copies"):
                clean.pop(key,None)
            try:raw=self._absolute_art_url(_image_url(row),profile)
            except Exception:raw=""
            key=(self._profile_key(profile),typ,str(clean.get("id") or clean.get("movie_id") or clean.get("series_id") or clean.get("name") or clean.get("title") or ""),str(raw or ""))
            if key in seen:return
            seen.add(key);prepared.append((dict(profile or {}),typ,clean,str(raw or "")))
        for row in rows:
            try:
                if _image_url(row):add_candidate(row)
            except Exception:pass
        for row in rows:
            if len(prepared)>=5:break
            add_candidate(row)
        prepared=prepared[:5]

        cancel_event=getattr(self,"_poster_cancel_event",None)
        poster_jobs=self._poster_jobs
        poster_size=tuple(self.POSTER_SIZE)
        futures=self._poster_futures
        screen_ref=weakref.ref(self)
        settings=dict(getattr(self,"_search_settings",{}) or load_settings() or {})
        base_client=self.client

        def worker():
            temp=None
            try:
                if cancel_event is None or cancel_event.is_set():return
                path=""
                # 1) Direct provider artwork. Use the strict safe transport first,
                # then the established provider transport used elsewhere in the
                # plugin for private/sibling portal CDNs.
                for profile,typ,clean,raw_url in prepared:
                    if cancel_event.is_set():return
                    if not raw_url:continue
                    try:url=_uil._optimized_artwork_url(raw_url,False) or raw_url
                    except Exception:url=raw_url
                    digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
                    thumb=_uil._thumb_path(digest,poster_size)
                    if _uil._valid_cache_file(thumb):
                        path=thumb;break
                    try:
                        try:headers=_uil._safe_image_headers(url,profile,base_client,clean)
                        except TypeError:headers=_uil._safe_image_headers(url,profile,base_client)
                        req=urllib.request.Request(url,headers=headers)
                        temp=os.path.join(os.path.dirname(thumb),"search_%s.download.%d.%d"%(digest,os.getpid(),threading.get_ident()))
                        _uil._persistent_write_require(temp)
                        total=0
                        try:
                            opener=_uil._safe_image_opener(url,profile)
                            response_ctx=opener.open(req,timeout=4.2)
                        except Exception:
                            response_ctx=provider_urlopen(req,timeout=4.8)
                        with response_ctx as response, open(temp,"wb") as handle:
                            while True:
                                if cancel_event.is_set():return
                                chunk=response.read(64*1024)
                                if not chunk:break
                                total+=len(chunk)
                                if total>5*1024*1024:raise ValueError("poster too large")
                                handle.write(chunk)
                        if total>256 and not cancel_event.is_set():
                            path=_uil._build_thumbnail(temp,thumb,poster_size) or ""
                        try:
                            if temp and os.path.exists(temp):os.unlink(temp)
                        except Exception:pass
                        temp=None
                        if path and _valid(path):break
                    except Exception:
                        if temp:
                            try:
                                if os.path.exists(temp):os.unlink(temp)
                            except Exception:pass
                            temp=None
                        continue

                # 2) Canonical w342 poster. This is the same lightweight poster
                # policy used by the proven grids, and is attempted for sparse
                # provider rows even when the title has never been opened before.
                if (not path or not _valid(path)) and not cancel_event.is_set():
                    credential=str(settings.get("tmdb_credential") or "").strip()
                    if settings.get("tmdb_enabled",True) and settings.get("load_images",True) and credential:
                        resolver=ArtworkV2(credential,str(settings.get("tmdb_language") or "ar-EG"),min(5,max(3,int(settings.get("timeout",10) or 10))))
                        for profile,typ,clean,raw_url in prepared:
                            if cancel_event.is_set():return
                            try:resolved=resolver.resolve(profile,typ,clean,full=False,cancel_event=cancel_event) or {}
                            except Exception:resolved={}
                            canonical=str(resolved.get("poster_local") or "")
                            if not _valid(canonical):continue
                            digest=hashlib.sha1((canonical+"|search206x310").encode("utf-8","ignore")).hexdigest()
                            thumb=_uil._thumb_path(digest,poster_size)
                            path=thumb if _valid(thumb) else (_uil._build_thumbnail(canonical,thumb,poster_size) or canonical)
                            if path and _valid(path):break

                if path and _valid(path) and not cancel_event.is_set():
                    poster_jobs.put((generation,slot,ident,path))
            except Exception as exc:
                if cancel_event is None or not cancel_event.is_set():optional_failure("ui.search_poster",exc)
            finally:
                if temp:
                    try:
                        if os.path.exists(temp):os.unlink(temp)
                    except Exception:pass

        try:
            future=_SEARCH_ART_EXECUTOR.submit(worker,_task_key="search-poster:%x:%s:%s:%s"%(id(self),generation,slot,ident))
            futures.add(future)
            def forget(done,owned=futures,ref=screen_ref):
                owned.discard(done)
                screen=ref()
                if screen is not None and getattr(screen,"_screen_closed",False):return
            future.add_done_callback(forget)
        except Exception as exc:
            optional_failure("ui.search_poster_submit",exc)

    def _drain_poster_jobs(self):
        _page, _start, rows = self._visible_range()
        while True:
            try:
                generation, slot, ident, path = self._poster_jobs.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            if generation != self._poster_generation or slot >= len(rows) or not _valid(path):
                continue
            if ident != self._poster_identity(rows[slot]):
                continue
            try:
                self._poster_ready_put(ident,path)
                w = self["result_art%d" % slot]
                if w.instance is not None:
                    w.instance.setPixmapFromFile(path)
                w.show()
            except Exception as exc:
                optional_failure("ui.search_poster_apply", exc)

    def move_left(self):
        if not self.results:
            return
        self.index = (self.index - 1) % len(self.results)
        self._render()

    def move_right(self):
        if not self.results:
            return
        self.index = (self.index + 1) % len(self.results)
        self._render()

    def page_left(self):
        if not self.results:
            return
        self.index = max(0, self.index - self.PAGE_SIZE)
        self._render()

    def page_right(self):
        if not self.results:
            return
        self.index = min(len(self.results) - 1, self.index + self.PAGE_SIZE)
        self._render()

    def _result_context(self, item):
        profile = item.get("_search_profile") if isinstance(item, dict) else None
        if not isinstance(profile, dict):
            profile = self.profile
        owned = self._profile_key(profile) != self._profile_key(self.profile)
        client = self.client if not owned else _client_from_profile(
            profile, timeout=min(8, int(load_settings().get("timeout", 10) or 10))
        )
        return profile, client, owned

    @staticmethod
    def _quality_label(item, position=0):
        row = item if isinstance(item, dict) else {}
        raw = " ".join(str(row.get(k) or "") for k in ("quality", "resolution", "video_quality", "name", "title"))
        upper = raw.upper()
        parts = []
        if any(x in upper for x in ("8K", "4320")): parts.append("8K")
        elif any(x in upper for x in ("4K", "2160", "3840", "UHD")): parts.append("4K UHD")
        elif any(x in upper for x in ("1080", "FHD")): parts.append("1080P FHD")
        elif any(x in upper for x in ("720", " HD")): parts.append("720P HD")
        elif "SD" in upper: parts.append("SD")
        if "DOLBY VISION" in upper or " DV " in (" " + upper + " "): parts.append("Dolby Vision")
        elif "HDR" in upper: parts.append("HDR")
        if any(x in upper for x in ("HEVC", "H265", "H.265", "X265")): parts.append("HEVC")
        return " • ".join(parts) if parts else (_("Result")+" %d" % (int(position) + 1))

    @staticmethod
    def _copy_category_label(item):
        row = item if isinstance(item, dict) else {}
        value = row.get("category_name") or row.get("genre_name") or row.get("category_title") or row.get("category")
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text and not text.isdigit():
                return text[:46]
        return ""

    def _glass_source(self, group):
        try:
            ident = self._poster_identity(group)
            path = self._poster_ready_get(ident) or self._local_search_poster(group)
            return path if _valid(path) else ""
        except Exception:
            return ""

    def _server_buckets(self, group):
        buckets = OrderedDict()
        for row in self._group_copies(group):
            profile = row.get("_search_profile") or {}
            key = self._source_key(profile)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "name": str(row.get("_search_portal_name") or profile.get("name") or profile.get("portal") or _("Portal server")),
                    "copies": [],
                }
                buckets[key] = bucket
            bucket["copies"].append(row)
        return list(buckets.values())

    def _open_group_servers(self, group):
        buckets = self._server_buckets(group)
        if not buckets:
            return
        self._choice_group = group
        title = str(group.get("_search_group_title") or group.get("name") or group.get("title") or _("Result"))
        copies, servers = self._group_stats(group)
        choices = []
        for bucket in buckets:
            count = len(bucket.get("copies") or [])
            choices.append(("%s  •  %d %s" % (bucket.get("name") or _("Portal server"), count, _("%d result(s)") % count), bucket))
        self.session.openWithCallback(
            self._server_choice_returned,
            SubtitleGlassChoiceScreen,
            "%s  •  %s" % (title, (_("%d results • %d portals") % (copies, servers))),
            choices,
            source_path=self._glass_source(group),
        )

    def _server_choice_returned(self, choice):
        if not choice:
            self._render(); return
        try:
            bucket = choice[1]
        except Exception:
            return
        copies = list((bucket or {}).get("copies") or []) if isinstance(bucket, dict) else []
        if not copies:
            return
        if len(copies) == 1:
            self._open_result_details(copies[0]); return
        self._choice_server_bucket = bucket
        group = getattr(self, "_choice_group", None) or {}
        title = str(group.get("_search_group_title") or group.get("name") or group.get("title") or _("Result"))
        server_name = str((bucket or {}).get("name") or _("Portal server"))
        rows = []
        for pos, item in enumerate(copies):
            quality = self._quality_label(item, pos)
            category = self._copy_category_label(item)
            label = quality + (("  •  " + category) if category else "")
            rows.append((label, item))
        self.session.openWithCallback(
            self._copy_choice_returned,
            SubtitleGlassChoiceScreen,
            "%s  •  %s" % (title, server_name),
            rows,
            source_path=self._glass_source(group),
        )

    def _copy_choice_returned(self, choice):
        if not choice:
            group = getattr(self, "_choice_group", None)
            if isinstance(group, dict):
                self._open_group_servers(group)
            return
        try:
            item = choice[1]
        except Exception:
            return
        if isinstance(item, dict):
            self._open_result_details(item)

    def _open_result_details(self, item):
        """Open Search details using the same proven contract as Favorites.

        Search is only responsible for choosing the source-owned item/context.
        It must not resolve visual peers, read HDD visual bundles, score cached
        seeds or perform any TMDb work before Enigma2 opens Details.  Once the
        screen is open, ContentDetailsScreen follows its normal cache/provider
        completion path exactly as it does for a saved Favorite.
        """
        typ = item.get("_search_media_type") or "vod"
        profile, client, owned = self._result_context(item)
        clean = self._clean_search_copy(item)
        # Favorites parity: source context + item -> Details immediately.
        # Playback ownership (profile/id/cmd/url) remains the selected Search
        # copy; only the temporary owned client is retained until return.
        self._details_owned_client = client if owned else None
        self.session.openWithCallback(
            self._details_returned, ContentDetailsScreen, profile, client, typ, clean
        )

    def _details_returned(self, _result=None):
        client = getattr(self, "_details_owned_client", None)
        self._details_owned_client = None
        if client is not None:
            try: client.close()
            except Exception: pass
        self._render()

    def select(self):
        if not self.results or not (0 <= self.index < len(self.results)):
            return
        if self._busy:
            try:
                self._cancel_active_async()
            except Exception:
                pass
        item = self.results[self.index]
        copies = self._group_copies(item)
        if len(copies) <= 1:
            self._open_result_details(copies[0] if copies else item)
            return
        self._open_group_servers(item)
