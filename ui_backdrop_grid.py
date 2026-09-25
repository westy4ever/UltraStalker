# -*- coding: utf-8 -*-
"""Backdrop Grid 1.

R208 is used only as the visual reference. Runtime behavior stays on the
Ultra-owned clean Cinematic authority: same clean backdrop cadence, same
Details authority and same hold-last-good rules, but with page-local workers so
Cinematic/BG1/BG2 never queue focus work on one another.
"""
from __future__ import absolute_import

import hashlib
import os
import queue
import re
import threading

try:
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw, ImageFilter as _ImageFilter
except Exception:
    _PILImage = _ImageDraw = _ImageFilter = None

try:
    from enigma import ePoint, eSize, gFont
except Exception:
    ePoint = eSize = gFont = None

from . import _
from .age_rating import display_certification
from . import ui_cinematic_global as _cin_direct
from .core.executor import LazyThreadPoolExecutor
from .log import optional_failure
from .ui_dynamic_palette import _dynamic_palette
from .ui_helpers import _lift_dynamic_accent, _mix_rgb
from .ui_fixed_adaptive import fixed_exact_surface
from .ui_grid_screens import PremiumPosterGridScreen
from .ui_grid_base import PremiumGridBase
from .ui_grid_artwork import GridArtworkMixin
from .ui_parts.catalog import strip_arabic_tashkeel as _strip_arabic_tashkeel
from .ui_cinematic_global import PremiumGlobalCinematicScreen

BACKDROP_GRID_SKIN = ""



def _age_rating_display(value):
    return display_certification(value)

def configure_backdrop_grid(**deps):
    globals().update(deps)
    if "BACKDROP_GRID_SKIN" in deps:
        PremiumBackdropGridScreen.skin = deps["BACKDROP_GRID_SKIN"]


def _valid_file(path, minimum=256):
    try:
        return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path)) > int(minimum))
    except Exception:
        return False


class PremiumBackdropGridScreen(PremiumPosterGridScreen, PremiumGlobalCinematicScreen):
    """R208 presentation + independent clean Cinematic behavior."""

    skin = BACKDROP_GRID_SKIN
    columns = 8
    page_size = 8
    image_size = (206, 310)
    selection_asset = "poster_card_selected_neutral_small.png"
    card_positions = [(51 + (230 * i), 708) for i in range(8)]
    visible_card_rating_prefetch = True
    # Age on BG1/BG2 is selected-title HUD metadata from Details Authority;
    # do not fan out certification requests for every hidden poster card.
    visible_card_age_prefetch = False

    # Poster-grid lifecycle must stay owned by the real grid engine.  Cinematic
    # deliberately disables these methods because it has no visible posters;
    # allowing those no-op overrides through the multiple-inheritance MRO makes
    # BG1/BG2 render empty poster rails.  Keep Cinematic only as the selected-
    # title/backdrop/details behavior authority and pin all poster-page plumbing
    # back to the normal grid implementation.
    def _grid_art_init(self, slot_names, image_size, profile, client):
        return GridArtworkMixin._grid_art_init(self, slot_names, image_size, profile, client)

    def _grid_art_layout_ready(self):
        return GridArtworkMixin._grid_art_layout_ready(self)

    def _grid_layout_ready(self):
        return PremiumGridBase._grid_layout_ready(self)

    def _prepare_grid_page(self, target, cancel_event=None):
        return PremiumGridBase._prepare_grid_page(self, target, cancel_event)

    def _start_progressive_poster_prefetch(self):
        return PremiumGridBase._start_progressive_poster_prefetch(self)

    def _prefetch_visible_page_details(self, priority_index=None, max_items=None):
        return PremiumGridBase._prefetch_visible_page_details(self, priority_index=priority_index, max_items=max_items)

    def _start_visible_poster_watch(self):
        return PremiumGridBase._start_visible_poster_watch(self)

    def _poll_visible_poster_cache(self):
        return PremiumGridBase._poll_visible_poster_cache(self)

    def _warm_idle_grid_page(self):
        return PremiumGridBase._warm_idle_grid_page(self)

    # Clean Cinematic selected-title authority. These are behavior methods only;
    # the visible R208 HUD below is intentionally separate.
    _record = PremiumGlobalCinematicScreen._record
    _record_key = PremiumGlobalCinematicScreen._record_key
    _rail_title = PremiumGlobalCinematicScreen._rail_title
    _quality = PremiumGlobalCinematicScreen._quality
    _item_fallback_title = PremiumGlobalCinematicScreen._item_fallback_title
    _grid_hidden_release = PremiumGlobalCinematicScreen._grid_hidden_release
    _grid_shown_resume = PremiumGlobalCinematicScreen._grid_shown_resume
    open_selected = PremiumGlobalCinematicScreen.open_selected
    open_menu = PremiumGlobalCinematicScreen.open_menu
    cache_folder_artwork = PremiumGlobalCinematicScreen.cache_folder_artwork
    _folder_artwork_ready_hook = PremiumGlobalCinematicScreen._folder_artwork_ready_hook
    _folder_artwork_finalize_hook = PremiumGlobalCinematicScreen._folder_artwork_finalize_hook
    _cache_folder_artwork_worker = PremiumGlobalCinematicScreen._cache_folder_artwork_worker
    _apply_folder_artwork_result = PremiumGlobalCinematicScreen._apply_folder_artwork_result

    # Exact clean backdrop behavior used by Cinematic. Each BG page receives its
    # own state, transport and executors in __init__.
    _fast_bd_current_sig = PremiumGlobalCinematicScreen._fast_bd_current_sig
    _fast_bd_cancel = PremiumGlobalCinematicScreen._fast_bd_cancel
    _fast_bd_original_cache_path = staticmethod(PremiumGlobalCinematicScreen._fast_bd_original_cache_path)
    _fast_bd_local_full_quality = staticmethod(PremiumGlobalCinematicScreen._fast_bd_local_full_quality)
    _fast_bd_clean_local_from_row = classmethod(PremiumGlobalCinematicScreen._fast_bd_clean_local_from_row.__func__)
    _fast_bd_url_from_row = staticmethod(PremiumGlobalCinematicScreen._fast_bd_url_from_row)
    _fast_bd_cached_original = PremiumGlobalCinematicScreen._fast_bd_cached_original
    _fast_bd_focus_stable = PremiumGlobalCinematicScreen._fast_bd_focus_stable
    _fast_bd_bind_local = PremiumGlobalCinematicScreen._fast_bd_bind_local
    _fast_bd_fast_store = PremiumGlobalCinematicScreen._fast_bd_fast_store
    _fast_bd_fast_cleanup = PremiumGlobalCinematicScreen._fast_bd_fast_cleanup
    _fast_bd_try_fast_warm = PremiumGlobalCinematicScreen._fast_bd_try_fast_warm
    _fast_bd_body_ready = PremiumGlobalCinematicScreen._fast_bd_body_ready
    _fast_bd_http_status = PremiumGlobalCinematicScreen._fast_bd_http_status
    _fast_bd_download_error = PremiumGlobalCinematicScreen._fast_bd_download_error
    _fast_bd_start_url = PremiumGlobalCinematicScreen._fast_bd_start_url
    _fast_bd_meta_fallback = PremiumGlobalCinematicScreen._fast_bd_meta_fallback
    _fast_bd_schedule = PremiumGlobalCinematicScreen._fast_bd_schedule
    _apply_debounced_grid_focus = PremiumGlobalCinematicScreen._apply_debounced_grid_focus

    def __init__(self, session, profile, client, media_type, genre, category_title=""):
        # Every page gets its own lazy worker lanes. Cinematic keeps its original
        # defaults; BG1/BG2 can never make its navigation queue wait.
        view = "bg2" if int(getattr(self, "page_size", 8) or 8) == 7 else "bg1"
        self._cin_fast_meta_executor = LazyThreadPoolExecutor(1, "ultrastalker-%s-fastmeta" % view)
        self._cin_hybrid_executor = LazyThreadPoolExecutor(1, "ultrastalker-%s-details" % view)
        self._cin_logo_executor = LazyThreadPoolExecutor(2, "ultrastalker-%s-logo" % view)
        self._cin_page_executor = LazyThreadPoolExecutor(1, "ultrastalker-%s-page" % view)
        self._cin_visual_executor = LazyThreadPoolExecutor(1, "ultrastalker-%s-visual" % view)
        self._bg_selector_executor = LazyThreadPoolExecutor(1, "ultrastalker-%s-selector" % view)

        PremiumGlobalCinematicScreen._cin_init_authority_state(self)
        PremiumPosterGridScreen.__init__(self, session, profile, client, media_type, genre, category_title)
        try:
            self._grid_decode_cache_limit = 2
        except Exception:
            pass

        # Initialize the clean Cinematic authority without adopting its visible
        # panel. The R208 skin binds bd_backdrop/title_logo and all old HUD fields.
        self._cin_authority_indexing = False
        PremiumGlobalCinematicScreen._cin_init_authority_widgets(self)
        self["bd_backdrop"] = self["cin_backdrop"]
        self["bd_bottom_mood"] = Pixmap()
        self["bd_title_fallback"] = Label("")
        self["bd_year"] = Label("")
        self["bd_quality"] = Label("")
        self["bd_quality_logo"] = Pixmap()
        self["bd_runtime_icon"] = Pixmap()
        self["bd_runtime"] = Label("")
        self["bd_country"] = Label("")
        self["bd_genre"] = Label("")
        self["bd_age_rating"] = Label("")
        self["bd_overview"] = Label("")
        for name in ("bd_year_bg", "bd_quality_bg", "bd_runtime_bg", "bd_country_bg", "bd_genre_bg", "bd_overview_bg"):
            self[name] = Pixmap()

        self._bd_selection_source = ""
        self._bg_last_record = {}
        # R214 Stage 2: the Backdrop information shell is a resident surface set.
        # Cold open starts from the exact bundled fixed-green master; after the
        # first title adaptive is ready, the last complete adaptive set stays
        # bound until the next complete set replaces it. No blank/neutral bridge.
        self._bd_hud_paths = {}
        self._bd_last_hud = {}
        self._bd_hud_adaptive_active = False
        self._bd_hud_jobs = queue.Queue()
        self._bd_hud_pending = set()
        self._bd_hud_active_token = None
        # R214 Stage 4: title-logo hold-last-good remains unchanged for BG1/BG2,
        # but a title that has been authoritatively resolved without a usable logo
        # owns the text fallback instead of inheriting the previous title's logo.
        # This is session-local on purpose: a transient provider failure may retry
        # on the next focus and can still promote to a real logo later.
        self._bd_logo_missing = set()
        self._bd_logo_result_sigs = set()
        # R222: remember which selected-item signature actually owns the pixmap
        # currently bound to title_logo.  BG used to resolve a valid PGV2 logo,
        # paint it, then immediately hide it again because an older hydrated row
        # still carried a *_checked flag without its new local-logo field.
        self._bd_logo_bound_sig = ""
        self.onLayoutFinish.append(self._bg_layout_ready)
        self.onClose.append(self._bg_shutdown_page_workers)
        PremiumGlobalCinematicScreen._cin_install_actions(
            self, self._bd_move_left, self._bd_move_right, self._bd_move_up, self._bd_move_down
        )
        try:
            self["red"].setText(_("Search")); self["green"].setText(_("Favorite"))
            self["yellow"].setText(_("Default")); self["blue"].setText(_("Cache Artwork"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Independent worker lifetime
    # ------------------------------------------------------------------
    def _bg_shutdown_page_workers(self):
        self._bd_hud_pending.clear()
        self._bd_hud_active_token = None
        try:
            self._bd_logo_missing.clear();self._bd_logo_result_sigs.clear();self._bd_logo_bound_sig=""
        except Exception:pass
        for name in ("_bg_selector_executor", "_cin_visual_executor", "_cin_page_executor", "_cin_hybrid_executor", "_cin_fast_meta_executor", "_cin_logo_executor"):
            ex = getattr(self, name, None)
            if ex is not None:
                try:
                    ex.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # R208 geometry/presentation, unchanged in screen coordinates
    # ------------------------------------------------------------------
    def _bg_layout_ready(self):
        # Cinematic presentation fade is a static ePixmap in the skin. R208's old
        # bottom mood layer must not add a second fade on top of it.
        try:
            self["bd_bottom_mood"].hide()
            if self["bd_bottom_mood"].instance is not None:
                self["bd_bottom_mood"].instance.setPixmap(None)
        except Exception:
            pass
        self._bd_force_geometry()
        self._bd_apply_fixed_info_hud()
        self._bd_apply_fixed_rail_chrome()
        self._grid_apply_current_selector(allow_build=True)

    def _bd_force_geometry(self):
        if ePoint is None or eSize is None:
            return
        try:
            # BG2 overrides this method with its own exact R208 coordinates.
            for name, x, y, w, h in (("page_label",55,722,470,28),("status",650,722,620,28)):
                inst = self[name].instance
                if inst is not None:
                    inst.move(ePoint(x,y)); inst.resize(eSize(w,h))
            for name, x, y, w, h in (("title_logo",55,80,432,210),("bd_title_fallback",55,106,500,180)):
                inst = self[name].instance
                if inst is not None:
                    inst.move(ePoint(x,y)); inst.resize(eSize(w,h))
            hud = (
                ("bd_year_bg",55,315,130,50),("bd_year",100,322,72,36),
                ("bd_quality_bg",198,315,150,50),("bd_quality_logo",212,323,122,34),("bd_quality",214,322,118,36),
                ("bd_runtime_bg",361,315,150,50),("bd_runtime",411,320,88,40),
                ("bd_genre_bg",55,375,440,50),("bd_age_rating",72,380,92,40),("bd_genre",168,382,310,36),
                ("bd_overview_bg",55,435,440,232),("bd_overview",76,447,398,206),
            )
            for name,x,y,w,h in hud:
                inst = self[name].instance
                if inst is not None:
                    inst.move(ePoint(x,y)); inst.resize(eSize(w,h))
            for pos in range(self.page_size):
                x = 55 + (230 * pos)
                for name,gx,gy,gw,gh in (
                    ("art%d"%pos,x,712,206,310),("card_chrome%d"%pos,x-4,708,214,318),
                    ("item_title%d"%pos,2000,0,1,1),("item_meta%d"%pos,2000,0,1,1)):
                    inst = self[name].instance
                    if inst is not None:
                        inst.move(ePoint(gx,gy)); inst.resize(eSize(gw,gh))
            for name in ("selection", "selection_adaptive"):
                inst = self[name].instance
                if inst is not None:
                    inst.move(ePoint(51,708)); inst.resize(eSize(214,318))
            self._scaled_card_positions = [(int((51+230*i)*self._grid_sx), int(708*self._grid_sy)) for i in range(self.page_size)]
        except Exception as exc:
            optional_failure("backdrop_grid.r208_geometry", exc)

    def _bd_pin_counter_status(self):
        self._bd_force_counter_only()

    def _bd_force_counter_only(self):
        if ePoint is None or eSize is None:
            return
        try:
            y = 649 if int(getattr(self,"page_size",8) or 8) == 7 else 680
            for name,x,w in (("page_label",55,470),("status",650,620)):
                inst=self[name].instance
                if inst is not None:
                    inst.move(ePoint(x,y)); inst.resize(eSize(w,28))
        except Exception as exc:
            optional_failure("backdrop_grid.r208_counter", exc)

    def _fit_poster_live_hud(self):
        self._bd_force_counter_only()

    def _update_page_counter(self):
        try:
            PremiumPosterGridScreen._update_page_counter(self)
        finally:
            self._bd_force_counter_only()

    # ------------------------------------------------------------------
    # R214 Stage 2: fixed-green first paint + hold-last-good adaptive HUD
    # ------------------------------------------------------------------
    def _bd_hud_specs(self):
        return {
            "bd_year_bg": (130, 50, "bd_year", "pill"),
            "bd_quality_bg": (150, 50, "bd_quality", "pill"),
            "bd_runtime_bg": (150, 50, "bd_runtime", "pill"),
            "bd_genre_bg": (440, 50, "bd_genre", "pill"),
            "bd_overview_bg": ((456 if int(getattr(self,"page_size",8) or 8)==7 else 440), 232,
                               ("bd2_overview" if int(getattr(self,"page_size",8) or 8)==7 else "bd_overview"), "overview"),
        }

    def _bd_set_hud_pixmap(self, widget, path):
        """Bind one already-prepared small HUD surface without blanking it first."""
        try:
            path=str(path or "")
            if not _valid_file(path):return False
            inst=self[widget].instance
            if inst is None:return False
            if str((getattr(self,"_bd_hud_paths",{}) or {}).get(widget) or "") != path:
                inst.setPixmapFromFile(path)
                self._bd_hud_paths[widget]=path
            self[widget].show()
            return True
        except Exception as exc:
            optional_failure("backdrop_grid.hud_bind.%s"%widget,exc);return False

    def _bd_complete_hud(self, hud):
        specs=self._bd_hud_specs()
        if not isinstance(hud,dict):return False
        return all(_valid_file(str(hud.get(widget) or "")) for widget in specs)

    def _bd_apply_hud_set(self, hud, adaptive=False):
        """Swap only complete sets. Existing surfaces stay resident on failure."""
        if not self._bd_complete_hud(hud):return False
        active={widget:str(hud.get(widget) or "") for widget in self._bd_hud_specs()}
        # All files are validated before the first native bind, so there is no
        # partial/neutral intermediate state. Small pixmaps are replaced in-place.
        for widget,path in active.items():
            if not self._bd_set_hud_pixmap(widget,path):return False
        self._bd_last_hud=dict(active)
        if adaptive:self._bd_hud_adaptive_active=True
        return True

    def _bd_fixed_hud(self):
        out={}
        for widget,(w,h,tag,_kind) in self._bd_hud_specs().items():
            try:path=str(fixed_exact_surface(w,h,False,tag) or "")
            except Exception:path=""
            if not _valid_file(path):return {}
            out[widget]=path
        return out

    def _bd_apply_fixed_info_hud(self):
        """Cold first paint only. Never re-apply green between adaptive titles."""
        if getattr(self,"_bd_hud_adaptive_active",False):
            held=dict(getattr(self,"_bd_last_hud",{}) or {})
            if self._bd_complete_hud(held):
                for widget,path in held.items():self._bd_set_hud_pixmap(widget,path)
            return
        fixed=self._bd_fixed_hud()
        if fixed:self._bd_apply_hud_set(fixed,adaptive=False)

    def _bd_hud_source(self,row):
        row=row if isinstance(row,dict) else {}
        for key in ("poster_local","_poster_local","backdrop_local","_backdrop_source_local"):
            path=str(row.get(key) or "")
            if _valid_file(path):return path
        return ""

    def _bd_hud_cache_identity(self, source):
        try:
            st=os.stat(source)
            stamp="%s|%s|%s"%(str(source),int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size))
            return hashlib.sha1(("r214-bg-hud-v2|"+stamp).encode("utf-8","ignore")).hexdigest()[:20]
        except Exception:return ""

    def _bd_hud_cache_paths(self, source):
        identity=self._bd_hud_cache_identity(source)
        if not identity:return {}
        root=str(globals().get("THUMB_CACHE_DIR") or "")
        if not root:return {}
        out={}
        for widget,(w,h,_tag,_kind) in self._bd_hud_specs().items():
            out[widget]=os.path.join(root,"bg214hud_%s_%s_%dx%d.png"%(identity,widget,w,h))
        return out

    def _bd_build_adaptive_hud(self, source):
        """Build only five small BG surfaces on the dedicated visual worker."""
        source=str(source or "")
        if not _valid_file(source) or _PILImage is None or _ImageDraw is None:return {}
        targets=self._bd_hud_cache_paths(source)
        if not targets:return {}
        if all(_valid_file(path) for path in targets.values()):return targets
        try:
            os.makedirs(os.path.dirname(next(iter(targets.values()))),mode=0o700,exist_ok=True)
        except Exception:return {}
        try:
            primary,secondary=_dynamic_palette(source)
            accent=_lift_dynamic_accent(primary,0.48,0.46)
            accent2=_lift_dynamic_accent(secondary,0.42,0.38)
            base=(3,12,20)
            for widget,(w,h,_tag,kind) in self._bd_hud_specs().items():
                target=targets[widget]
                if _valid_file(target):continue
                radius=22
                temp=target+".tmp.%d"%os.getpid()
                out=_PILImage.new("RGBA",(w,h),(0,0,0,0))
                glow=_PILImage.new("RGBA",(w,h),(0,0,0,0));gd=_ImageDraw.Draw(glow)
                gd.rounded_rectangle((6,6,w-7,h-7),radius=radius,outline=accent+((92 if kind=="overview" else 78),),width=3)
                if _ImageFilter is not None:
                    try:glow=glow.filter(_ImageFilter.GaussianBlur(radius=7 if kind=="overview" else 5))
                    except Exception:pass
                out=_PILImage.alpha_composite(out,glow);d=_ImageDraw.Draw(out)
                fill=_mix_rgb(base,accent,0.10 if kind=="overview" else 0.14)
                if kind=="overview":
                    fill_alpha,border_alpha=82,188
                    d.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=None,outline=accent+(border_alpha,),width=1)
                    d.rounded_rectangle((10,9,w-11,h-10),radius=max(7,radius-7),fill=fill+(fill_alpha,))
                else:
                    fill_alpha,border_alpha=98,196
                    d.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=fill+(fill_alpha,),outline=accent+(border_alpha,),width=1)
                inner=_mix_rgb(accent2,(255,255,255),0.46)
                if kind=="overview":
                    d.rounded_rectangle((11,10,w-12,h-11),radius=max(6,radius-8),outline=inner+(22,),width=1)
                else:
                    d.rounded_rectangle((7,7,w-8,h-8),radius=max(5,radius-6),outline=inner+(22,),width=1)
                sheen=_PILImage.new("RGBA",(w,h),(0,0,0,0));sd=_ImageDraw.Draw(sheen)
                if kind=="overview":
                    sd.rounded_rectangle((15,12,w-16,max(20,h//2)),radius=max(5,radius-10),fill=inner+(15,))
                else:
                    sd.rounded_rectangle((12,7,w-13,max(14,h//2)),radius=max(5,radius-8),fill=inner+(13,))
                if _ImageFilter is not None:
                    try:sheen=sheen.filter(_ImageFilter.GaussianBlur(radius=4 if kind=="overview" else 3))
                    except Exception:pass
                out=_PILImage.alpha_composite(out,sheen)
                out.save(temp,"PNG",compress_level=3);os.replace(temp,target)
            return targets if all(_valid_file(path) for path in targets.values()) else {}
        except Exception as exc:
            optional_failure("backdrop_grid.adaptive_hud_build",exc);return {}

    def _bd_schedule_selected_adaptive_hud(self,item,row):
        source=self._bd_hud_source(row)
        if not source:return
        key=self._record_key(row if isinstance(row,dict) else {},item)
        if not key:return
        # Cheap cache hit first; no palette/Pillow work and no reset to green.
        cached=self._bd_hud_cache_paths(source)
        if self._bd_complete_hud(cached):
            self._bd_apply_hud_set(cached,adaptive=True);return
        token=(str(key),self._bd_hud_cache_identity(source),int(getattr(self,"page_size",8) or 8))
        # The keyed executor may cancel an older queued title before its worker
        # body runs. Drop that stale pending token here so revisiting the title
        # can schedule it normally instead of being blocked forever.
        if token==getattr(self,"_bd_hud_active_token",None) and token in self._bd_hud_pending:return
        self._bd_hud_pending.clear()
        self._bd_hud_pending.add(token);self._bd_hud_active_token=token
        def worker():
            hud={}
            try:hud=self._bd_build_adaptive_hud(source)
            except Exception as exc:optional_failure("backdrop_grid.adaptive_hud_worker",exc)
            try:self._bd_hud_jobs.put({"token":token,"key":str(key),"hud":hud})
            except Exception:pass
        try:
            self._cin_visual_executor.submit(worker,_task_key="bg-hud:%x"%id(self),_replace_task_key=True)
            self._cin_kick_poll()
        except Exception:
            self._bd_hud_pending.discard(token)
            if getattr(self,"_bd_hud_active_token",None)==token:self._bd_hud_active_token=None

    def _bd_drain_hud_jobs(self):
        budget=2
        while budget>0:
            try:r=self._bd_hud_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            budget-=1
            token=r.get("token");self._bd_hud_pending.discard(token)
            if getattr(self,"_bd_hud_active_token",None)==token:self._bd_hud_active_token=None
            if getattr(self,"_screen_closed",False) or not getattr(self,"grid_items",None):continue
            item=self.grid_items[self.index]
            row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index),{}) or self._record(item) or {}
            if str(r.get("key") or "")!=str(self._record_key(row,item) or ""):continue
            hud=r.get("hud") if isinstance(r.get("hud"),dict) else {}
            if hud:self._bd_apply_hud_set(hud,adaptive=True)

    def _cin_drain_jobs(self):
        self._bd_drain_hud_jobs()
        return PremiumGlobalCinematicScreen._cin_drain_jobs(self)

    # ------------------------------------------------------------------
    # R208 rail chrome + selector only. No adaptive poster-card recolouring.
    # ------------------------------------------------------------------
    def _bd_frame_asset(self):
        return asset("poster_frame_neutral_214x318.png")

    def _bd_apply_fixed_rail_chrome(self):
        fixed = self._bd_frame_asset(); fixed_pix = None
        try:
            fixed_pix = cached_png(fixed) if _valid_file(fixed) else None
        except Exception:
            fixed_pix = None
        for pos in range(int(getattr(self,"page_size",8) or 8)):
            try:
                self["item_title%d"%pos].setText(""); self["item_title%d"%pos].hide()
                self["item_meta%d"%pos].setText(""); self["item_meta%d"%pos].hide()
            except Exception:
                pass
            try:
                chrome=self["card_chrome%d"%pos]
                if pos < len(getattr(self,"grid_items",[]) or []):
                    if chrome.instance is not None:
                        if fixed_pix is not None: chrome.instance.setPixmap(fixed_pix)
                        elif _valid_file(fixed): chrome.instance.setPixmapFromFile(fixed)
                    chrome.show()
                else:
                    chrome.hide()
                    if chrome.instance is not None: chrome.instance.setPixmap(None)
            except Exception as exc:
                optional_failure("backdrop_grid.r208_fixed_frame",exc)

    def _bd_selector_dimensions(self):
        return (214,318)

    def _bd_selection_overlay_for_poster(self, path):
        try:
            if not path or not os.path.isfile(path) or _PILImage is None or _ImageDraw is None:
                return ""
            w,h=self._bd_selector_dimensions()
            st=os.stat(path);stamp="%s|%s"%(int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size))
            key=hashlib.sha1((str(path)+"|"+stamp+"|r208-selector-%sx%s-v1"%(w,h)).encode("utf-8","ignore")).hexdigest()[:20]
            target=os.path.join(THUMB_CACHE_DIR,"r208sel_%s_%sx%s.png"%(key,w,h))
            if _valid_file(target): return target
            primary,_secondary=_dynamic_palette(path); r,g,b=_lift_dynamic_accent(primary,0.60,0.58)
            canvas=_PILImage.new("RGBA",(w,h),(0,0,0,0))
            glow=_PILImage.new("RGBA",(w,h),(0,0,0,0));gd=_ImageDraw.Draw(glow)
            gd.rectangle((5,5,w-6,h-6),outline=(r,g,b,245),width=10)
            if _ImageFilter is not None: glow=glow.filter(_ImageFilter.GaussianBlur(5))
            canvas=_PILImage.alpha_composite(canvas,glow)
            d=_ImageDraw.Draw(canvas);core=(min(255,r+92),min(255,g+92),min(255,b+92),255)
            d.rectangle((1,1,w-2,h-2),outline=core,width=3)
            os.makedirs(os.path.dirname(target),exist_ok=True)
            tmp=target+".tmp.%s"%os.getpid();canvas.save(tmp,"PNG",compress_level=2);os.replace(tmp,target)
            return target if _valid_file(target) else ""
        except Exception as exc:
            optional_failure("backdrop_grid.r208_selector_build",exc);return ""

    def _grid_schedule_adaptive_selector(self,generation,slot,path):
        if getattr(self,"media_type",None) not in ("vod","series") or slot != getattr(self,"index",-1): return
        if not path or not os.path.isfile(path): return
        token=(generation,slot,path,True)
        if token in self._grid_accent_pending:return
        self._grid_accent_pending.add(token)
        def worker():
            result=""
            try: result=self._bd_selection_overlay_for_poster(path)
            except Exception as exc: optional_failure("backdrop_grid.r208_selector_worker",exc)
            try:self._grid_accent_jobs.put((generation,slot,path,result,None,None))
            except Exception:pass
        try:
            self._bg_selector_executor.submit(worker,_task_key="r208-selector:%x"%id(self),_replace_task_key=True)
            self._cin_kick_poll()
        except Exception:self._grid_accent_pending.discard(token)

    def _grid_drain_adaptive_selector(self):
        changed=False
        while True:
            try:generation,slot,path,result,*_=self._grid_accent_jobs.get_nowait()
            except queue.Empty:break
            except Exception:break
            self._grid_accent_pending.discard((generation,slot,path,False));self._grid_accent_pending.discard((generation,slot,path,True))
            if generation!=self._grid_generation or slot!=getattr(self,"index",-1):continue
            current=getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot)
            if current!=path or not result:continue
            try:
                if self["selection_adaptive"].instance is not None:
                    if result!=getattr(self,"_grid_selection_adaptive_visual_path",""):
                        self["selection_adaptive"].instance.setPixmapFromFile(result);self._grid_selection_adaptive_visual_path=result
                    self["selection_adaptive"].show();self._bd_selection_source=path;changed=True
                self["selection"].hide()
                if self["selection"].instance is not None:self["selection"].instance.setPixmap(None)
            except Exception as exc:optional_failure("backdrop_grid.r208_selector_apply",exc)
        return changed

    def _grid_apply_current_selector(self,allow_build=False):
        try:
            self["selection"].hide()
            if self["selection"].instance is not None:self["selection"].instance.setPixmap(None)
            self._grid_selection_visual_path=""
        except Exception:pass
        slot=int(getattr(self,"index",0) or 0)
        path=getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot)
        have=bool(getattr(self,"_grid_selection_adaptive_visual_path","") and getattr(self["selection_adaptive"],"instance",None) is not None)
        if have:
            try:self["selection_adaptive"].show()
            except Exception:pass
        if not path or not os.path.isfile(path):return
        if getattr(self,"_bd_selection_source","")==path and have:return
        if allow_build:self._grid_schedule_adaptive_selector(self._grid_generation,slot,path)

    def _grid_schedule_page_mood(self,path):
        return

    def _grid_drain_page_mood(self):
        try:
            while True:self._grid_mood_jobs.get_nowait()
        except Exception:pass

    def _prepare_grid_page(self,*args,**kwargs):
        payload=PremiumPosterGridScreen._prepare_grid_page(self,*args,**kwargs)
        try:
            for row in (payload or {}).get("art",[]) or []:
                if isinstance(row,dict):row.pop("card",None)
        except Exception:pass
        return payload

    # ------------------------------------------------------------------
    # R208 information placement, fed only by the clean Details Authority.
    # ------------------------------------------------------------------
    def _bg_fit_label(self,name,text,max_size,min_size,width_pad=10):
        if gFont is None:return
        try:
            inst=self[name].instance
            if inst is None:return
            value=" ".join(str(text or "").replace("\n"," ").split())
            width=max(24,int(inst.size().width())-int(width_pad))
            chosen=int(max_size)
            for size in range(int(max_size),int(min_size)-1,-1):
                try:
                    probe_len=max(1,len(value));estimate=probe_len*size*0.58
                    if estimate<=width:chosen=size;break
                except Exception:pass
            inst.setFont(gFont("Regular",chosen))
        except Exception:pass

    def _bg_fit_genre(self,text):
        if gFont is None:return
        try:
            inst=self["bd_genre"].instance
            if inst is None:return
            value=str(text or "")
            size=20
            if len(value)>72:size=12
            elif len(value)>58:size=13
            elif len(value)>46:size=14
            elif len(value)>36:size=16
            elif len(value)>28:size=18
            for candidate in range(size,11,-1):
                inst.setFont(gFont("Regular",candidate))
                try:
                    if int(inst.calculateSize().width())<=398:break
                except Exception:break
        except Exception:pass

    def _bg_fit_overview(self,text):
        if gFont is None:return
        try:
            inst=self["bd_overview"].instance
            if inst is None:return
            value=str(text or "")
            start=22
            if len(value)>520:start=17
            elif len(value)>420:start=18
            elif len(value)>320:start=19
            elif len(value)>240:start=20
            for candidate in range(start,16,-1):
                inst.setFont(gFont("Regular",candidate))
                try:
                    if int(inst.calculateSize().height())<=198:break
                except Exception:break
        except Exception:pass

    def _bg_details_overview(self,item,row):
        try:
            from .details_authority import details_overview
            text,ready=details_overview(self.profile,self.media_type,item,row)
            if ready:return str(text or "").strip()
        except Exception:pass
        return str((row or {}).get("overview") or "").strip()

    def _bd_title_logo_probe_missing(self,item,row):
        try:
            from .ui_grid_screens import _pgv2_title_logo_probe_missing_detached
            return bool(_pgv2_title_logo_probe_missing_detached(item,row))
        except Exception:
            return False

    def _bd_sync_title_logo_fallback(self,item,row,known_sig=None):
        """R229 BG current-item-first identity: title now, logo only as an upgrade.

        BG1/BG2 never keep another item's logo after selection changes.  A logo
        already bound/cached for THIS exact item wins immediately; otherwise the
        current title remains visible while stable-focus resolution runs.
        """
        row=dict(row or {})
        title=self._rail_title(item,row) or str(row.get("title") or row.get("name") or (item or {}).get("name") or (item or {}).get("title") or "")
        try:self["bd_title_fallback"].setText(str(title or "")[:120])
        except Exception:pass
        sig=str(known_sig or "");cached=""
        if not sig:
            try:sig,cached=self._cin_title_logo_signature(item,row)
            except Exception:sig="";cached=""
        else:
            try:_same,cached=self._cin_title_logo_signature(item,row)
            except Exception:cached=""
        held=str(getattr(self,"_cin_logo_path","") or "")
        bound_sig=str(getattr(self,"_bd_logo_bound_sig","") or "")

        # Already-bound current identity stays visible.
        if sig and bound_sig==sig and held and _valid_file(held):
            try:self._bd_logo_missing.discard(sig)
            except Exception:pass
            try:self["title_logo"].show();self["bd_title_fallback"].hide()
            except Exception:pass
            return "logo"

        # Current-item cached logo can promote the text synchronously.  This is
        # still zero-network and avoids waiting for the stable-focus worker.
        if cached and _valid_file(cached) and self["title_logo"].instance is not None:
            try:
                if self._cin_logo_path!=cached:self["title_logo"].instance.setPixmapFromFile(cached)
                self._cin_logo_path=cached;self._bd_logo_bound_sig=sig
                if sig:self._bd_logo_missing.discard(sig)
                self["title_logo"].show();self["bd_title_fallback"].hide()
                return "logo"
            except Exception:pass

        # New selection with no current cached logo: retire any previous identity
        # immediately.  The title remains the authoritative first paint whether
        # resolution eventually returns a logo or confirms there is none.
        try:self["title_logo"].hide()
        except Exception:pass
        self._cin_logo_path="";self._bd_logo_bound_sig=""
        try:self["bd_title_fallback"].show()
        except Exception:pass
        return "text"

    def _bg_apply_details_record(self,item,row,hold_missing=False):
        """Paint raw Details metadata while keeping old text until replacement exists."""
        row=dict(row or {});self._bg_last_record=row
        self._bd_sync_title_logo_fallback(item,row)

        year=str(row.get("year") or "")[:4]
        if year or not hold_missing:
            try:self["bd_year"].setText(year)
            except Exception:pass
        q=""
        try:
            _quality_state=(getattr(self,"_grid_item_state",{}) or {}).get(id(item),{}) or {}
            q=str(_quality_state.get("quality") or "").strip()[:10]
        except Exception:pass
        try:
            qa=_cin_direct._quality_asset_name(q) if q else None;qp=asset(qa) if qa else ""
            if _valid_file(qp) and self["bd_quality_logo"].instance is not None:
                self["bd_quality_logo"].instance.setPixmapFromFile(qp);self["bd_quality_logo"].show();self["bd_quality"].setText("");self["bd_quality"].hide()
            else:
                self["bd_quality_logo"].hide();self["bd_quality"].setText(q or _("AUTO"));self["bd_quality"].show()
        except Exception:
            try:self["bd_quality_logo"].hide();self["bd_quality"].setText(q or _("AUTO"));self["bd_quality"].show()
            except Exception:pass

        try:
            if self.media_type=="series":
                seasons=row.get("number_of_seasons") or row.get("seasons_count") or row.get("season_count") or 0
                try:seasons=int(seasons or 0)
                except Exception:seasons=0
                runtime=((_('%d season')%seasons) if seasons==1 else ((_('%d seasons')%seasons) if seasons>1 else ""))
                icon=asset("us166_details_folder_yellow_32.png")
            else:
                rv=row.get("runtime") or row.get("duration") or 0
                try:rv=int(float(rv or 0))
                except Exception:rv=0
                runtime=((_('%s min')%rv) if rv else "")
                icon=asset("us173_movie_runtime_32_icononly.png")
            if runtime or not hold_missing:
                self["bd_runtime"].setText(runtime)
                if runtime and _valid_file(icon) and self["bd_runtime_icon"].instance is not None:
                    self["bd_runtime_icon"].instance.setPixmapFromFile(icon);self["bd_runtime_icon"].show()
                elif not hold_missing:self["bd_runtime_icon"].hide()
                self._bg_fit_label("bd_runtime",runtime,21,12,4)
        except Exception:pass

        try:
            genres=row.get("genres") or row.get("genre") or []
            if isinstance(genres,str):genres=[genres]
            values=[]
            for g in genres[:3]:
                if isinstance(g,dict):g=g.get("name") or ""
                g=str(g or "").strip()
                if g:values.append(g)
            value=" / ".join(values)[:78]
            if value or not hold_missing:
                self["bd_genre"].setText(value);self._bg_fit_genre(value)
            age_value=_age_rating_display(row.get("certification") or row.get("age_rating"))
            if age_value or not hold_missing:self["bd_age_rating"].setText(age_value)
        except Exception:pass
        try:
            overview=_strip_arabic_tashkeel(self._bg_details_overview(item,row)[:650])
            if overview or not hold_missing:
                self["bd_overview"].setText(overview);self._bg_fit_overview(overview)
        except Exception:pass

    def _cin_apply_record(self,item,row,allow_build=False,hold_missing=False):
        # Keep the exact R208 geometry. Metadata text and adaptive glass both obey
        # hold-last-good: text swaps as soon as the new Details value exists, while
        # the resident five-surface HUD swaps only when its complete set is ready.
        row=dict(row or {})
        self._cin_last_record=row
        self._cin_record_key=self._record_key(row,item)
        self._cin_show_title_logo(item,row)
        self._bg_apply_details_record(item,row,hold_missing=hold_missing)
        self._bd_schedule_selected_adaptive_hud(item,row)
        return True

    def _cin_apply_package(self,item,row,package=None):
        # Package is data/cache identity only on BG pages. The visible presentation
        # is the exact R208 shell, so Cinematic row/chrome assets must never repaint it.
        package=package if isinstance(package,dict) and package else self._cin_load_package(item,row)
        if not package:return False
        try:
            poster=str((row or {}).get("poster_local") or "");backdrop=str((row or {}).get("backdrop_local") or "")
            if poster and os.path.isfile(poster):
                if isinstance(item,dict):
                    item["_cin_provider_poster_local"]=poster;item["_ultra_palette_source"]=poster
                    item["_ultra_poster_source"]=poster;item["_player_poster"]=poster;item["_adaptive_source_local"]=poster
                self._grid_palette_sources[self.index]=poster
            if backdrop and os.path.isfile(backdrop) and isinstance(item,dict):
                item["_cin_provider_backdrop_local"]=backdrop;item["_backdrop_source_local"]=backdrop
        except Exception as exc:optional_failure("backdrop_grid.package_handoff",exc)
        self._cin_visual_key=str(package.get("key") or self._record_key(row,item))
        return True

    def _cin_schedule_visuals(self,item,row):
        # Stable focus only: build the five small HUD surfaces and the selected
        # poster trace here, never in the arrow/key-repeat hot path.
        self._bd_schedule_selected_adaptive_hud(item,row)
        self._grid_apply_current_selector(allow_build=True)
        return

    # BG title-logo slot is the exact R208 432x210 canvas, not a stretched
    # Cinematic 420x144 raster.
    def _cin_title_logo_signature(self,item,row):
        """Return a stable CURRENT-ITEM request key plus any HDD-cached logo.

        Do not gate discovery on an already-known TMDb id.  Details and Poster
        Grid V2 both allow the shared resolver to discover canonical identity
        from the current catalogue item; BG1/BG2 used to return an empty
        signature here when ``tmdb_id`` had not hydrated yet, which meant their
        resolver worker was never scheduled at all.

        The request key is therefore owned by the selected catalogue item and
        stays identical before/after metadata hydration.  The cached-logo lookup
        still uses the shared Ultra authority and its strongest known identity.
        This also lets a worker that started before TMDb hydration survive the
        result gate after the row gains its canonical id.
        """
        try:
            row=row if isinstance(row,dict) else {}
            title=self._rail_title(item,row) or str(row.get("title") or row.get("name") or "")
            key=str(self._page_visual_key(item) or "")
            if not key:
                try:
                    import hashlib
                    basis="%s|%s"%(self.media_type,str(title or "").strip().casefold())
                    key=hashlib.sha1(basis.encode("utf-8","ignore")).hexdigest()
                except Exception:key=str(id(item))
            cached=_cin_direct.ultra_title_logo_cached(self.media_type,item,row,(432,210),title) or ""
            return ("%s:item:%s"%(self.media_type,key),str(cached or ""))
        except Exception:return ("","")

    def _cin_show_title_logo(self,item,row):
        sig,cached=self._cin_title_logo_signature(item,row)
        seal_state="unknown";probe_missing=False
        try:
            if _valid_file(cached) and self["title_logo"].instance is not None:
                if sig:self._bd_logo_missing.discard(sig)
                if self._cin_logo_path!=cached:self["title_logo"].instance.setPixmapFromFile(cached);self._cin_logo_path=cached
                self._bd_logo_bound_sig=sig
                self["title_logo"].show();self["bd_title_fallback"].hide();return
            from .ui_grid_screens import _pgv2_logo_seal_state_detached,_pgv2_title_logo_probe_missing_detached
            seal_state,_seal_path,_seal=_pgv2_logo_seal_state_detached(self.profile,self.media_type,item,row)
            probe_missing=(_pgv2_title_logo_probe_missing_detached(item,row) if seal_state!="logo" else False)
            if seal_state=="missing" or probe_missing:
                self._bd_sync_title_logo_fallback(item,row,known_sig=sig)
                if seal_state=="missing":return
            else:
                held=str(getattr(self,"_cin_logo_path","") or "")
                if held and _valid_file(held):self["title_logo"].show();self["bd_title_fallback"].hide()
                else:self["title_logo"].hide();self["bd_title_fallback"].show();self._cin_logo_path="";self._bd_logo_bound_sig=""
        except Exception:pass
        if sig and sig!=str(getattr(self,"_cin_logo_active_sig","") or ""):
            try:
                old=getattr(self,"_cin_logo_cancel",None)
                if old is not None:old.set()
            except Exception:pass
            self._cin_logo_cancel=threading.Event();self._cin_logo_active_sig=sig
        if not sig or sig in self._cin_logo_pending:return
        if getattr(self,"_cin_logo_cancel",None) is None:self._cin_logo_cancel=threading.Event();self._cin_logo_active_sig=sig
        self._cin_logo_pending.add(sig);item_copy=dict(item or {});row_copy=dict(row or {})
        ev=self._cin_logo_cancel
        def worker():
            path="";logo_state="pending";fresh=dict(row_copy or {})
            try:
                from .ui_grid_screens import (
                    _pgv2_bridge_logo_from_seal_detached,_pgv2_authoritative_logo_row_detached,
                    _pgv2_resolve_title_logo_detached,_pgv2_write_logo_seal_detached
                )
                state,bridged,_seal=_pgv2_bridge_logo_from_seal_detached(
                    self.profile,self.media_type,item_copy,fresh,(432,210),cancel_event=ev
                )
                if ev is not None and ev.is_set():return
                if state=="missing":logo_state="missing"
                elif state=="logo" and bridged:
                    path=bridged;logo_state="logo"
                else:
                    settings=(getattr(self,"_grid_settings",None) or {})
                    fresh=_pgv2_authoritative_logo_row_detached(
                        self.profile,self.media_type,item_copy,fresh,cancel_event=ev,settings=settings
                    )
                    if ev is not None and ev.is_set():return
                    path,fresh=_pgv2_resolve_title_logo_detached(
                        self.profile,self.media_type,item_copy,fresh,cancel_event=ev,settings=settings,canvas_size=(432,210)
                    )
                    if ev is not None and ev.is_set():return
                    logo_state="logo" if _valid_file(path) else "missing"
                    _pgv2_write_logo_seal_detached(self.profile,self.media_type,item_copy,fresh,path)
            except Exception as exc:optional_failure("backdrop_grid.clean_title_logo",exc)
            try:
                cancelled=bool(ev is not None and ev.is_set())
                if not cancelled:
                    if logo_state=="logo" and _valid_file(path):self._bd_logo_missing.discard(sig)
                    elif logo_state=="missing":self._bd_logo_missing.add(sig)
                    self._bd_logo_result_sigs.add(sig)
            except Exception:pass
            try:self._cin_jobs.put({"kind":"title_logo","sig":sig,"path":path or "","missing":(logo_state=="missing")})
            except Exception:pass
        try:
            future=self._cin_logo_executor.submit(worker,_task_key="bg-logo:%x"%id(self),_replace_task_key=True)
            def _logo_done(done,_sig=sig):
                if done.cancelled():self._cin_logo_pending.discard(_sig)
            future.add_done_callback(_logo_done);self._cin_kick_poll()
        except Exception:self._cin_logo_pending.discard(sig)

    # ------------------------------------------------------------------
    # Page paint/navigation. No BG-specific backdrop selector exists here.
    # ------------------------------------------------------------------
    def _index_cinematic_authority_page(self):
        return self._cin_index_current_page()

    def _render_grid(self):
        self._cin_authority_indexing=True
        try:result=PremiumPosterGridScreen._render_grid(self)
        finally:self._cin_authority_indexing=False
        self._index_cinematic_authority_page();self._bd_force_geometry();self._bd_apply_fixed_rail_chrome();self._update_selection()
        return result

    def _poster_selection_only(self):
        if not self.grid_items:return
        self.index=max(0,min(self.index,len(self.grid_items)-1));self._remember_grid_state()
        # Arrow-repeat is presentation-only. Geometry is fixed at page paint, and
        # the last prepared adaptive selector follows the focus immediately.  A
        # new selector is built only after stable focus, never for skipped titles.
        try:
            px,py=self._scaled_card_positions[self.index]
            if self["selection_adaptive"].instance is not None:self["selection_adaptive"].instance.move(ePoint(px,py))
        except Exception:pass
        self._grid_apply_current_selector(allow_build=False)
        try:self._refresh_favorite_button(self.grid_items[self.index])
        except Exception:pass

    def _update_selection(self):
        if not self.grid_items:return
        self._poster_selection_only()
        if getattr(self,"_cin_authority_indexing",False):
            try:self._grid_focus_timer.stop()
            except Exception:pass
            return
        item=self.grid_items[self.index]
        # Key-repeat stays RAM/UI-only: no detail snapshot reads, font fitting,
        # palette work or logo network scheduling for titles the user skips.  This
        # is the Stage-3 hold_missing=True behavior implemented by not repainting
        # the information cluster at all until stable focus.  The
        # stable-focus gate below owns all of that work.  We only keep the visible
        # identity truthful if this already-indexed row proves there is no logo.
        try:
            row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index),{}) or {}
            self._bd_sync_title_logo_fallback(item,row)
        except Exception:pass
        self._cin_after_navigation(item)

    def _update_header(self,item):
        # Preserve the R208 information shell while focus is moving. Stable focus
        # atomically replaces it with the next Details record.
        return

    def _drain_jobs(self):
        if getattr(self,"_cin_cache_exclusive",False):
            return PremiumGlobalCinematicScreen._drain_jobs(self)
        PremiumPosterGridScreen._drain_jobs(self)
        if not self._screen_closed:self._cin_drain_jobs()
        # The inherited Cinematic result consumer intentionally holds an old logo
        # when a replacement path is empty. That is correct for Cinematic, but BG1
        # and BG2 own a text fallback. Reconcile only when a BG logo worker has
        # actually completed, so the normal pending state still holds the old logo.
        try:
            completed=set(getattr(self,"_bd_logo_result_sigs",set()) or set())
            if completed:self._bd_logo_result_sigs.difference_update(completed)
            if completed and self.grid_items:
                current=self.grid_items[self.index]
                row=(getattr(self,"_cin_page_records",{}) or {}).get(int(self.index),{}) or self._record(current) or {}
                current_sig,_cached=self._cin_title_logo_signature(current,row)
                if current_sig in completed:
                    held=str(getattr(self,"_cin_logo_path","") or "")
                    # The inherited Cinematic consumer has just painted the async
                    # result. Seal ownership before BG's fallback reconciliation,
                    # otherwise stale *_checked row flags hide the successful logo.
                    if held and _valid_file(held):
                        self._bd_logo_bound_sig=current_sig
                        try:self._bd_logo_missing.discard(current_sig)
                        except Exception:pass
                        try:self["title_logo"].show();self["bd_title_fallback"].hide()
                        except Exception:pass
                    else:
                        self._bd_sync_title_logo_fallback(current,row,known_sig=current_sig)
            else:
                held=str(getattr(self,"_cin_logo_path","") or "")
                if held and _valid_file(held):self["bd_title_fallback"].hide()
        except Exception:pass

    # ------------------------------------------------------------------
    # R208 continuous rail navigation, with Cinematic's quiet-window behavior.
    # ------------------------------------------------------------------
    def _bd_total(self):
        try:return max(len(self.grid_items or []),int(self._active_total() or 0))
        except Exception:return len(self.grid_items or [])

    def _bd_absolute(self):
        return max(0,(int(self.page or 1)-1)*int(self.page_size or 1)+int(self.index or 0))

    def _bd_virtual_absolute(self):
        page=int(self._grid_virtual_page() or 1);index=int(self._grid_virtual_index() or 0)
        return max(0,(page-1)*int(self.page_size or 1)+index)

    def _bd_go_absolute(self,absolute):
        total=self._bd_total()
        if total<=0:return
        absolute=int(absolute)%total;target_page=absolute//self.page_size+1;target_index=absolute%self.page_size
        pending=bool(int(getattr(self,"_grid_page_nav_target",0) or 0))
        if target_page==int(self.page or 1) and self.grid_items and not pending:
            self.index=min(target_index,max(0,len(self.grid_items)-1));self._update_selection();self._update_page_counter()
        else:
            self._queue_grid_page_target(target_page,target_index,110)

    def _bd_move_right(self):
        if not self.grid_items or not self._nav_allowed():return
        total=self._bd_total()
        if total>0:self._bd_go_absolute((self._bd_virtual_absolute()+1)%total)

    def _bd_move_left(self):
        if not self.grid_items or not self._nav_allowed():return
        total=self._bd_total()
        if total>0:self._bd_go_absolute((self._bd_virtual_absolute()-1)%total)

    def _bd_move_up(self):
        if not self.grid_items or not self._nav_allowed():return
        total=self._bd_total();page_size=max(1,int(self.page_size or 1))
        if total<=page_size:return
        absolute=min(self._bd_virtual_absolute(),total-1);page=(absolute//page_size)+1;row=absolute%page_size
        if page<=1:
            total_pages=max(1,(total+page_size-1)//page_size)
            target=min((total_pages-1)*page_size+row,total-1)
        else:
            target=max(0,absolute-page_size)
        self._bd_go_absolute(target)

    def _bd_move_down(self):
        if not self.grid_items or not self._nav_allowed():return
        total=self._bd_total();page_size=max(1,int(self.page_size or 1))
        if total<=page_size:return
        absolute=min(self._bd_virtual_absolute(),total-1);page=(absolute//page_size)+1;row=absolute%page_size
        next_start=page*page_size
        if next_start>=total:target=min(row,total-1)
        else:target=min(absolute+page_size,total-1)
        self._bd_go_absolute(target)
