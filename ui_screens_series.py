"""Series episodes screen extracted from ui.py without changing class behavior."""

from . import _
from enigma import ePoint, eSize
import queue
import os
import json
import time
import hashlib
import logging

from .core.tasks import TASKS
from .persistent_cache import INDEX as _SERIES_CACHE_BASE, content_cache_key, hdd_read_ready, persistent_write_gate
from .ui_screens_details import ContentDetailsScreen
from .services.player_visuals import _progress_neon_frame, _fallback_player_frames
from .ui_dynamic_chrome import _build_dynamic_settings_episode_rows, _cached_dynamic_settings_episode_rows, canonical_dynamic_rows_key

SERIES_EPISODES_SKIN = ""
_SERIES_CATALOG_CACHE_DIR=os.path.join(_SERIES_CACHE_BASE,"series_catalog")
_SERIES_CATALOG_TTL=6*60*60

def _perf57_series(event, **fields):
    try:
        payload=" ".join("%s=%s"%(str(k),str(v)) for k,v in fields.items())
        logging.getLogger("UltraStalker").info("PERF57 series_%s %s",str(event),payload)
    except Exception:
        pass

def _series_cache_identity(profile,series_item,season=None):
    try:key=content_cache_key(profile or {},"series",series_item or {})
    except Exception:key=""
    if not key:return ""
    if season is None:return key
    raw="|".join(str((season or {}).get(k) or "") for k in ("season_number","season","season_id","id","name","title"))
    return key+"_"+hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()[:16]

def _series_cache_path(profile,series_item,kind,season=None):
    key=_series_cache_identity(profile,series_item,season)
    if not key:return ""
    return os.path.join(_SERIES_CATALOG_CACHE_DIR,"%s_%s.json"%(str(kind),key))

def _series_cache_read(profile,series_item,kind,season=None):
    path=_series_cache_path(profile,series_item,kind,season)
    if not path or not hdd_read_ready(force=True):return [],0
    try:
        with open(path,"r",encoding="utf-8") as fh:data=json.load(fh)
        rows=data.get("rows") if isinstance(data,dict) else []
        updated=int((data or {}).get("updated_at") or 0) if isinstance(data,dict) else 0
        return ([dict(x) for x in rows if isinstance(x,dict)] if isinstance(rows,list) else []),updated
    except Exception:
        return [],0

def _series_cache_write(profile,series_item,kind,rows,season=None):
    path=_series_cache_path(profile,series_item,kind,season)
    if not path or not rows:return False
    temp=""
    try:
        os.makedirs(_SERIES_CATALOG_CACHE_DIR,mode=0o700,exist_ok=True)
        if not persistent_write_gate(path):return False
        temp=path+".tmp.%d"%os.getpid()
        if not persistent_write_gate(temp):return False
        with open(temp,"w",encoding="utf-8") as fh:
            json.dump({"schema":1,"updated_at":int(time.time()),"rows":list(rows)},fh,ensure_ascii=False,separators=(",",":"),default=str)
            fh.flush();os.fsync(fh.fileno())
        os.replace(temp,path);temp=""
        return True
    except Exception:
        return False
    finally:
        if temp:
            try:
                if os.path.exists(temp) and persistent_write_gate(temp):os.unlink(temp)
            except OSError:pass

def configure_series_screen(**deps):
    globals().update(deps)
    if "SERIES_EPISODES_SKIN" in deps:
        SeriesEpisodesScreen.skin = deps["SERIES_EPISODES_SKIN"]

class SeriesEpisodesScreen(ContentDetailsScreen):
    skin = SERIES_EPISODES_SKIN

    def __init__(self, session, profile, client, series_item):
        self._perf57_series_open_mono=time.monotonic()
        self._prefetched_series_seasons = [dict(x) for x in ((series_item or {}).get("_prefetched_series_seasons") or []) if isinstance(x,dict)] if isinstance(series_item,dict) else []
        if isinstance(series_item,dict):
            series_item=dict(series_item);series_item.pop("_prefetched_series_seasons",None)
        self.series_item = series_item
        self.level = "seasons"
        self.content_items = []
        self.current_season = None
        self._episode_visual_index = 0
        self._last_season_index = 0
        self._last_episode_index_by_season = {}
        self._episode_rebuilding = False
        self._episode_state_cache = {}
        self._episode_quality_cache = {}
        self._episode_parent_quality = ""
        self._series_jobs = queue.Queue()
        self._series_request_token = 0
        self._series_task_handles = []
        self._series_chrome_pending = ""
        self._series_chrome_source = ""
        self._is_series_browser_child = True
        # The parent Details screen already owns the authoritative poster/backdrop.
        # Enter the season browser as a zero-wait child instead of re-reading the
        # same heavy Details bundle synchronously.
        _series_open_item = dict(series_item) if isinstance(series_item, dict) else series_item
        if isinstance(_series_open_item, dict):
            _series_open_item["_ultra_fast_open"] = True
            _handoff_bundle = _series_open_item.get("_ultra_fast_bundle")
            if not isinstance(_handoff_bundle, dict):
                _handoff_bundle = {}
            _present = str(_series_open_item.get("_backdrop_present_local") or "")
            _source = str(_series_open_item.get("_backdrop_source_local") or "")
            _poster = str(_series_open_item.get("_adaptive_source_local") or _series_open_item.get("_ultra_poster_source") or "")
            if _poster and os.path.isfile(_poster): _handoff_bundle.setdefault("poster", _poster)
            if _source and os.path.isfile(_source): _handoff_bundle.setdefault("backdrop", _source)
            if _present and os.path.isfile(_present): _handoff_bundle.setdefault("backdrop_present", _present)
            _series_open_item["_ultra_fast_bundle"] = _handoff_bundle
        ContentDetailsScreen.__init__(self, session, profile, client, "series", _series_open_item)
        _perf57_series("child_init",elapsed_ms=int((time.monotonic()-self._perf57_series_open_mono)*1000.0),prefetched=len(self._prefetched_series_seasons or []),fast_bundle=bool((_series_open_item or {}).get("_ultra_fast_bundle") if isinstance(_series_open_item,dict) else False))
        self.series_item = series_item
        self["list_header"] = Label(_("SEASONS"))
        self["list_context"] = Label(_("Choose a season"))
        self["counter"] = Label(_("Loading..."))
        self["list_panel_bg"] = Pixmap()
        self["list"] = IconMenuList([], width=430, item_height=73, icon_size=40, primary_font=21, secondary_font=15, row_style="settings_episode")
        self._series_row_assets = {}
        # Compatibility-only components retained for hierarchy logic; they are not rendered.
        self["info_kicker"] = Label("")
        self["selection_hint"] = Label("")
        self["info_title"] = Label("")
        self["info"] = Label("")
        self["episode_badge"] = Label("")
        self["rating_badge"] = Label("")
        self["year_badge"] = Label("")
        self["resume_badge"] = Label("")
        self["blue"].setText(_("Open / Play"))
        self["list"].onSelectionChanged.append(self._selection_changed)
        self.onClose.append(self._stop_series_ui_hooks)
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions","UltraStalkerMenuActions", "DirectionActions"], {
            "cancel":self.back, "red":self.back, "green":self.toggle_favorite,
            "yellow":self.show_information, "blue":self.select, "ok":self.select,
            "up":lambda:self["list"].wrap_up(), "down":lambda:self["list"].wrap_down(),
            "left":lambda:self["list"].page_left(), "right":lambda:self["list"].page_right(),
            "menu":self.open_episode_download_menu,
        }, -1)

    def _stop_series_ui_hooks(self):
        self._series_request_token += 1
        for handle in list(getattr(self,"_series_task_handles",[]) or []):
            try:handle.cancel()
            except Exception as exc:optional_failure("ui.series_task_cancel",exc)
        self._series_task_handles=[]
        try:
            callbacks=self["list"].onSelectionChanged
            if self._selection_changed in callbacks:callbacks.remove(self._selection_changed)
        except Exception as exc:
            optional_failure("ui.series_selection_cleanup",exc)

    def _run_series_async(self, func, ok, fail=None):
        """Independent seasons/episodes lane; never shares Details._busy."""
        if getattr(self,"_screen_closed",False):
            return False
        self._series_request_token += 1
        token=self._series_request_token

        def guarded_ok(value):
            if getattr(self,"_screen_closed",False) or token!=self._series_request_token:
                return
            if ok:ok(value)

        def guarded_fail(value):
            if getattr(self,"_screen_closed",False) or token!=self._series_request_token:
                return
            if fail:fail(value)

        try:
            handle=TASKS.submit(func,self._series_jobs,ok=guarded_ok,fail=guarded_fail)
            if handle is None:
                if fail:fail(RuntimeError("Series request was not started"))
                return False
            self._series_task_handles=[
                old for old in self._series_task_handles
                if not old.cancelled() and not (getattr(old,"future",None) is not None and old.future.done())
            ]
            self._series_task_handles.append(handle)
            # PERFLAB9: this lane submits directly to TASKS instead of using
            # AsyncScreenMixin._run_async().  Explicitly wake the GUI delivery
            # heartbeat so seasons/episodes cannot finish in the worker queue
            # while the visible rail remains empty until another screen event.
            try:self._async_set_poll_interval(120)
            except Exception as exc:optional_failure("ui.series_async_heartbeat",exc)
            return handle
        except Exception as exc:
            if fail:fail(exc)
            return False

    def _drain_series_jobs(self):
        if getattr(self,"_screen_closed",False):return
        while True:
            try:
                callback,value,is_error,task_id=self._series_jobs.get_nowait()
            except queue.Empty:
                break
            except Exception:
                continue
            if task_id is not None:
                self._series_task_handles=[
                    handle for handle in self._series_task_handles
                    if getattr(handle,"task_id",None)!=task_id
                ]
            if callback:
                try:callback(value)
                except Exception as exc:
                    try:self["status"].setText(_("Series UI error: %s")%exc)
                    except Exception:pass
            elif is_error:
                try:self["status"].setText(str(value))
                except Exception:pass

    def _series_shown_reset(self):
        try:self._selection_changed()
        except Exception as exc:optional_failure("ui",exc)

    def _series_description(self):
        item = self.series_item if isinstance(self.series_item, dict) else {}
        text = (item.get("description") or item.get("descr") or item.get("plot") or item.get("overview") or item.get("comment") or "")
        text = _clean_display_text(text,1100)
        return text if text else _("Series information will appear here")

    def _selected_description(self, item):
        if isinstance(item, dict):
            for key in ("description", "descr", "plot", "overview", "comment", "episode_description", "episode_plot"):
                value = item.get(key)
                if value:
                    text = _clean_display_text(value,1500)
                    if text:
                        return text
        return self._series_description()

    @staticmethod
    def _valid_episode_progress(progress):
        progress = progress if isinstance(progress, dict) else {}
        try:
            position = max(0, int(progress.get("position") or 0))
        except Exception:
            position = 0
        try:
            duration = max(0, int(progress.get("duration") or 0))
        except Exception:
            duration = 0
        minimum = 45 * 90000
        if position < minimum:
            return (0, duration, False)
        if duration and position > duration + (5 * 90000):
            return (0, duration, False)
        cfg = load_settings()
        threshold = max(80, min(99, int(cfg.get("completion_threshold", 93)))) / 100.0
        remaining_limit = max(30, min(900, int(cfg.get("completion_remaining_seconds", 180)))) * 90000
        completed = bool(progress.get("completed") or (duration and (position >= int(duration * threshold) or max(0, duration-position) <= remaining_limit)))
        return (position, duration, completed)

    def _series_rating(self):
        item = self.series_item if isinstance(self.series_item, dict) else {}
        for key in ("rating_imdb", "imdb", "rating", "imdb_rating", "kp_rating"):
            value = item.get(key)
            if value not in (None, "", "N/A", "N/a"):
                try:
                    n = float(str(value).replace(",", ".").split("/")[0].strip())
                    if 0 <= n <= 10:
                        return ("%.1f" % n).rstrip("0").rstrip(".")
                except Exception:
                    text = str(value).strip()
                    if text:
                        return text[:6]
        return "N/A"

    def _series_year(self):
        item = self.series_item if isinstance(self.series_item, dict) else {}
        for key in ("year", "release_year", "released", "release_date", "date"):
            value = item.get(key)
            if value not in (None, ""):
                text = str(value)
                import re
                m = re.search(r"(19|20)\d{2}", text)
                if m:
                    return m.group(0)
        return "N/A"

    def _refresh_series_badges(self, episodes=None):
        if episodes is None:
            episodes = 0
            if self.level == "episodes":
                episodes = len(self.content_items or [])
            elif isinstance(self.current_season, dict) and isinstance(self.current_season.get("episodes"), list):
                episodes = len(self.current_season.get("episodes") or [])
        self["episode_badge"].setText((_("EPISODES  %s") % episodes) if episodes else "")
        self["rating_badge"].setText(("IMDb  %s" % self._series_rating()) if self._series_rating() != "N/A" else "")
        self["year_badge"].setText((_("YEAR  %s") % self._series_year()) if self._series_year() != "N/A" else "")

    def _queue_exact_series_rows(self, source, sig):
        source=str(source or "")
        if not source or not os.path.isfile(source) or getattr(self,"_screen_closed",False): return
        pending="%s|%s"%(source,sig)
        if getattr(self,"_series_chrome_pending","")==pending: return
        self._series_chrome_pending=pending
        def work(handle):
            if handle.cancel_event.is_set(): return {}
            return _build_dynamic_settings_episode_rows(source,canonical_dynamic_rows_key(source,selected_rim_only=True,cinematic_premium=True) or sig,selected_rim_only=True,cinematic_premium=True) or {}
        def apply(rows):
            if getattr(self,"_screen_closed",False) or getattr(self,"_series_chrome_pending","")!=pending: return
            self._series_chrome_pending=""
            rows=rows if isinstance(rows,dict) else {}
            if not (rows.get("normal") and rows.get("selected")): return
            self._series_row_assets={"row_asset":rows.get("normal"),"row_selected_asset":rows.get("selected")}
            row_value=rows.get("value_color")
            if isinstance(row_value,(tuple,list)) and len(row_value)>=3:
                self._series_progress_accent="#%02x%02x%02x"%(int(row_value[0]),int(row_value[1]),int(row_value[2]))
            if self.content_items:
                idx=max(0,self["list"].getSelectedIndex())
                if self.level=="episodes": self._render_episode_rows(idx,update_details=False)
                else: self._render_season_rows(idx,update_details=False)
        def fail(exc):
            if getattr(self,"_series_chrome_pending","")==pending:self._series_chrome_pending=""
            optional_failure("ui.series_exact_cinematic_rows",exc)
        try:
            handle=TASKS.submit(work,self._series_jobs,ok=apply,fail=fail,key="series-row:"+hashlib.sha1(pending.encode("utf-8","ignore")).hexdigest())
            if handle is not None:self._series_task_handles.append(handle)
        except Exception as exc:
            self._series_chrome_pending="";optional_failure("ui.series_exact_cinematic_rows_submit",exc)

    def _apply_detail_chrome(self, chrome):
        ContentDetailsScreen._apply_detail_chrome(self, chrome)
        # No enclosing rail panel here: seasons/episodes float directly over the
        # cinematic background, using the same adaptive visual language as portals.
        try:self["list_panel_bg"].hide()
        except Exception as exc:optional_failure("ui.series_panel_hide",exc)
        try:self["accent_frame"].hide()
        except Exception as exc:optional_failure("ui.series_panel_accent_hide",exc)
        try:
            # Never build Pillow chrome on the Enigma2 GUI thread.  Reuse the exact
            # cached Cinematic rows immediately; if they do not exist yet, paint the
            # already-available Details rows/fallback and build the two 426x72 rows
            # in the background.  This removes the multi-second OK->Seasons stall.
            source = str(getattr(self,"_adaptive_source_local","") or getattr(self,"_portal_backdrop_source_local","") or getattr(self,"_backdrop_displayed_path","") or "")
            cin_rows = {}
            sig = ""
            if source and os.path.isfile(source):
                try:
                    st=os.stat(source);sig="seriescin649_%s_%s"%(int(st.st_mtime),int(st.st_size))
                    cin_rows=_cached_dynamic_settings_episode_rows(source,sig,selected_rim_only=True,cinematic_premium=True) or {}
                except Exception as exc:optional_failure("ui.series_cached_cinematic_rows",exc)
            self._series_row_assets = {
                "row_asset": (cin_rows or {}).get("normal") or ((chrome or {}).get("series_row") if isinstance(chrome,dict) else None),
                "row_selected_asset": (cin_rows or {}).get("selected") or ((chrome or {}).get("series_row_selected") if isinstance(chrome,dict) else None),
            }
            self._series_progress_accent = str((chrome or {}).get("accent_color") or "#69c9f4") if isinstance(chrome,dict) else "#69c9f4"
            if sig and not (cin_rows.get("normal") and cin_rows.get("selected")):
                self._queue_exact_series_rows(source,sig)
            if self.content_items:
                idx=max(0,self["list"].getSelectedIndex())
                if self.level == "episodes": self._render_episode_rows(idx, update_details=False)
                else: self._render_season_rows(idx, update_details=False)
        except Exception as exc:optional_failure("ui.series_row_chrome",exc)

    def _layout_ready(self):
        _r57_layout=time.monotonic()
        ContentDetailsScreen._layout_ready(self)
        _r57_base_ms=int((time.monotonic()-_r57_layout)*1000.0)
        try:
            self["poster"].hide(); self["poster_border_overlay"].hide(); self["poster_footer_bg"].hide(); self["poster_imdb_logo"].hide(); self["poster_imdb_value"].hide(); self["poster_match_text"].hide()
        except Exception as exc:optional_failure("ui.series_poster_hide",exc)
        try:
            if self["list"].instance is not None:
                self["list"].instance.setSelectionEnable(0)
        except Exception as exc:optional_failure("ui.series_selection_disable",exc)
        self.load_seasons()
        _perf57_series("layout_ready",base_ms=_r57_base_ms,total_ms=int((time.monotonic()-_r57_layout)*1000.0),open_total_ms=int((time.monotonic()-getattr(self,"_perf57_series_open_mono",_r57_layout))*1000.0))

    def _drain_jobs(self):
        ContentDetailsScreen._drain_jobs(self)
        self._drain_series_jobs()

    @staticmethod
    def _episode_date(item):
        item = item if isinstance(item, dict) else {}
        for key in ("air_date", "release_date", "released", "date", "added"):
            value = item.get(key)
            if value:
                value = str(value).strip()
                m = re.search(r"(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
                if m:
                    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
                return value[:16]
        return ""

    def _episode_quality_badge(self, item, number):
        item = item if isinstance(item, dict) else {}
        raw = " ".join(str(item.get(k) or "") for k in ("quality", "resolution", "format", "video_quality", "name", "title", "description", "descr"))
        explicit=normalize_quality(raw)
        if explicit:return explicit
        learned=(getattr(self,"_episode_quality_cache",{}) or {}).get(id(item),"")
        if learned:return learned
        parent=str(getattr(self,"_episode_parent_quality","") or "")
        if parent:return parent
        return "EP %s" % number


    @staticmethod
    def _season_cursor_key(season):
        row=season if isinstance(season,dict) else {}
        value=(row.get("season_number") or row.get("season") or row.get("season_id") or row.get("id") or row.get("name") or row.get("title") or "")
        return str(value)

    def _season_row_tuple(self, index, selected_index):
        valid = self.content_items or []
        if not (0 <= int(index) < len(valid)):
            return None
        item = valid[int(index)]
        if not isinstance(item, dict):
            return None
        pos = int(index) + 1
        sid = item.get("season") or item.get("season_id") or item.get("id") or pos
        title = str(item.get("name") or item.get("title") or (_("Season %s") % sid))
        eps = item.get("episodes")
        count = len(eps) if isinstance(eps, list) else 0
        selected = bool(int(index) == int(selected_index))
        meta = {"kind":"season", "selected":selected, "right":(_("%d EPISODES") % count if count else _("Ready to browse")), "badge":_("SELECTED") if selected else "", "compact_row":True, "center_title":True, "series_title_polish":True}
        meta.update(getattr(self,"_series_row_assets",{}) or {})
        return (_clean_display_text(title, 100), asset("us166_details_folder_yellow_32.png"), item, meta)

    def _render_season_rows(self, selected_index=0, update_details=True):
        valid = self.content_items or []
        if valid:
            selected_index = max(0, min(int(selected_index or 0), len(valid)-1))
        menu = [row for row in (self._season_row_tuple(i, selected_index) for i in range(len(valid))) if row is not None]
        if not menu:
            menu = [(_("No seasons returned"), asset("us166_details_folder_yellow_32.png"), None, {"kind":"season","selected":True,"right":"","badge":""})]
        self._episode_rebuilding = True
        try:
            self["list"].set_icon_rows(menu)
            if valid:
                self["list"].moveToIndex(selected_index)
        finally:
            self._episode_rebuilding = False
        self._episode_visual_index = selected_index
        if update_details:
            self._selection_changed()

    def _selection_changed(self):
        if getattr(self, "_episode_rebuilding", False):
            return
        idx = self["list"].getSelectedIndex()
        old = getattr(self, "_episode_visual_index", -1)
        if idx != old:
            builder = self._episode_row_tuple if self.level == "episodes" else self._season_row_tuple
            for j in set((old, idx)):
                if 0 <= j < len(self.content_items):
                    row = builder(j, idx)
                    if row is not None:
                        try:self["list"].update_icon_row(j, row)
                        except Exception as exc:optional_failure("ui.series_selection_row_update",exc)
        self._episode_visual_index = idx
        if self.level == "seasons":
            self._last_season_index = max(0,int(idx or 0))
        elif self.level == "episodes" and isinstance(self.current_season,dict):
            self._last_episode_index_by_season[self._season_cursor_key(self.current_season)] = max(0,int(idx or 0))
        if not (0 <= idx < len(self.content_items)):
            return
        item = self.content_items[idx]
        if not isinstance(item, dict):
            return
        if self.level == "seasons":
            self["blue"].setText(_("Open season"))
            self["list_context"].setText("")
        else:
            ep_num = item.get("episode") or item.get("number") or (idx + 1)
            self["blue"].setText(_("Play episode %s") % ep_num)
        # The right-hand hero/details area intentionally remains the parent series details.
        # Only the left rail changes selection, exactly as requested.

    def _set_series_rail_geometry(self, episodes=False):
        """Cinematic-grade adaptive rail below the cached Title Logo."""
        try:
            if self["list"].instance is not None:
                self["list"].instance.move(ePoint(50,312))
                self["list"].instance.resize(eSize(430,730))
        except Exception as exc:optional_failure("ui.series_rail_geometry",exc)

    def load_seasons(self):
        if getattr(self,"_screen_closed",False):
            return
        _r57_start=time.monotonic()
        self.level="seasons";self.current_season=None
        self._set_series_rail_geometry(False)
        self["list_header"].setText("")

        def _season_sort_key(row):
            row=row if isinstance(row,dict) else {}
            for key in ("season_number","season","season_id"):
                value=row.get(key)
                try:return (0,int(value))
                except Exception:pass
            text=str(row.get("name") or row.get("title") or row.get("id") or "")
            match=re.search(r"(?i)\bseason\s*0*(\d+)\b",text)
            if match:
                try:return (0,int(match.group(1)))
                except Exception:pass
            nums=re.findall(r"\d+",text)
            if nums:
                try:return (0,int(nums[-1]))
                except Exception:pass
            return (1,text.casefold())

        def apply_rows(rows,source="server"):
            _r57_render=time.monotonic()
            valid=[dict(x) for x in rows if isinstance(x,dict)] if isinstance(rows,list) else []
            valid.sort(key=_season_sort_key)
            self.content_items=valid
            # Embedded episode lists stay attached to their season row.  Do not
            # fan out one fsync/write per season on the Enigma2 GUI thread: OK on
            # a season can consume the embedded rows directly with zero network.
            try:
                if valid and (not self["runtime_text"].getText() or self["runtime_text"].getText()=="—"):
                    self["runtime_text"].setText(self._season_count_text(len(valid)));self._fit_compact_text("runtime_text",max_size=24,min_size=17,padding=3)
            except Exception as exc:optional_failure("ui.series_season_count",exc)
            self["list_context"].setText("")
            _restore_season=max(0,min(int(getattr(self,"_last_season_index",0) or 0),len(valid)-1)) if valid else 0
            self._render_season_rows(_restore_season)
            self["counter"].setText("")
            self["status"].setText("" if valid else _("No seasons"))
            _perf57_series("seasons_paint",source=source,rows=len(valid),render_ms=int((time.monotonic()-_r57_render)*1000.0))
            return valid

        prefetched=[dict(x) for x in (getattr(self,"_prefetched_series_seasons",[]) or []) if isinstance(x,dict)]
        _r57_cache=time.monotonic()
        cached,updated=_series_cache_read(self.profile,self.series_item,"seasons")
        _r57_cache_ms=int((time.monotonic()-_r57_cache)*1000.0)
        if prefetched:
            # Parent Details already cached this hierarchy from its background
            # prefetch.  Reuse the RAM handoff immediately; no GUI-thread write.
            cached=prefetched;updated=int(time.time());self._prefetched_series_seasons=[]
        age=max(0,int(time.time())-int(updated or 0)) if updated else 10**9
        if cached:
            apply_rows(cached,"prefetch" if prefetched else "cache")
        else:
            self["status"].setText(_("Loading seasons..."))

        # Fresh HDD catalogue is authoritative for this entry. No network wait.
        if cached and age<_SERIES_CATALOG_TTL:
            _perf57_series("seasons_ready",source=("prefetch" if prefetched else "cache"),rows=len(cached),cache_read_ms=_r57_cache_ms,age_s=age,total_ms=int((time.monotonic()-_r57_start)*1000.0))
            return

        def ok(rows):
            valid=[dict(x) for x in rows if isinstance(x,dict)] if isinstance(rows,list) else []
            valid.sort(key=_season_sort_key)
            if valid:
                _series_cache_write(self.profile,self.series_item,"seasons",valid)
            # Repaint only if the server actually changed the catalogue.
            old_sig=[str((x or {}).get("id") or (x or {}).get("season_id") or (x or {}).get("season") or (x or {}).get("name") or "") for x in (self.content_items or [])]
            new_sig=[str((x or {}).get("id") or (x or {}).get("season_id") or (x or {}).get("season") or (x or {}).get("name") or "") for x in valid]
            if valid and new_sig!=old_sig:
                apply_rows(valid,"server")
            elif not cached:
                apply_rows(valid,"server")
        self._run_series_async(
            lambda handle:self.client.series_seasons(self.series_item,cancel_event=handle.cancel_event),
            ok,
            lambda e:self["status"].setText(_("Seasons failed: %s")%e) if not cached else None,
        )
        _perf57_series("seasons_async",cached=len(cached or []),cache_read_ms=_r57_cache_ms,total_ms=int((time.monotonic()-_r57_start)*1000.0))

    @staticmethod
    def _episode_progress_text(progress, number):
        progress = progress if isinstance(progress, dict) else {}
        position, duration, completed = SeriesEpisodesScreen._valid_episode_progress(progress)
        if completed:
            return "✓ " + _("Watched") + "  •  " + (_("Episode %s") % number)
        if position > 0:
            seconds = int(position // 90000)
            if seconds >= 3600:
                stamp = "%d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)
            else:
                stamp = "%d:%02d" % (seconds // 60, seconds % 60)
            return _("CONTINUE") + " ▶ %s  •  " % stamp + (_("Episode %s") % number)
        return _("Episode %s") % number

    def _episode_row_tuple(self, index, selected_index):
        valid = self.content_items or []
        if not (0 <= int(index) < len(valid)):
            return None
        item = valid[int(index)]
        if not isinstance(item, dict):
            return None
        pos = int(index) + 1
        num = item.get("episode") or item.get("number") or item.get("id") or pos
        title = str(item.get("name") or item.get("title") or item.get("episode_name") or (_("Episode %s") % num))
        progress = self._episode_state_cache.get(id(item), {})
        position, duration, completed = self._valid_episode_progress(progress)
        if completed:
            progress_text = _("WATCHED")
        elif position > 0:
            seconds = int(position // 90000)
            progress_text = _("CONTINUE") + " ▶ %d:%02d" % (seconds // 60, seconds % 60)
        else:
            progress_text = ""
        date_text = self._episode_date(item)
        right = progress_text or date_text
        badge = _("WATCHED") if completed else (_("CONTINUE") if position > 0 else self._episode_quality_badge(item, num))
        meta = {"kind":"episode", "selected":bool(int(index) == int(selected_index)), "right":right, "badge":badge, "compact_row":True, "series_title_polish":True}
        # Copy the Cinematic rail's proven laser path exactly.  That renderer
        # draws a native 300x34 frame, so 7% is really 7% and the luminous
        # endpoint lands exactly at the watched position.
        if position > 0 and duration > 0 and not completed:
            try:
                pct=max(1,min(99,int(round((float(position)*100.0)/float(duration)))))
                from .ui_cinematic_global import _cinematic_row_progress_frame
                accent=str(getattr(self,"_series_progress_accent","") or "#69c9f4")
                neon=_cinematic_row_progress_frame(accent,pct,width=268)
                if neon and os.path.isfile(neon):
                    meta["watch_progress"]=pct
                    meta["watch_progress_full_line"]=True
                    meta["watch_progress_neon"]=neon
                    meta["watch_progress_native_width"]=268
                    meta["watch_progress_below_title"]=True
                    meta["value_color"]=0xFFFFFF
                    meta["right"]=""
                    meta["badge"]=""
            except Exception as exc:
                optional_failure("ui.series_episode_cinematic_laser",exc)
        else:
            meta["center_title"]=True
        meta.update(getattr(self,"_series_row_assets",{}) or {})
        return (_clean_display_text(title, 100), asset("us86_episode_40.png"), item, meta)

    def _render_episode_rows(self, selected_index=None, update_details=True):
        if self.level != "episodes":
            return
        valid = self.content_items or []
        if valid:
            selected_index = max(0, min(int(selected_index or 0), len(valid)-1))
        else:
            selected_index = 0
        # Pure UI render: all state/quality persistence was loaded in the
        # background worker before this callback reached the Enigma2 main thread.
        menu = [row for row in (self._episode_row_tuple(i, selected_index) for i in range(len(valid))) if row is not None]
        if not menu:
            menu = [(_("No episodes returned"), asset("us86_episode_40.png"), None, {"kind":"episode","selected":True,"right":"","badge":""})]
        self._episode_rebuilding = True
        try:
            self["list"].set_icon_rows(menu)
            if valid:
                self["list"].moveToIndex(selected_index)
        finally:
            self._episode_rebuilding = False
        self._episode_visual_index = selected_index
        if update_details:
            self._selection_changed()

    def load_episodes(self, season):
        if getattr(self,"_screen_closed",False):
            return
        _r57_start=time.monotonic()
        self.current_season=season;self._set_series_rail_geometry(True)
        season_name=str((season or {}).get("name") or (season or {}).get("title") or (_("Season %s") % ((season or {}).get("season") or (season or {}).get("season_id") or "")))
        self["list_context"].setText("")
        self["info_kicker"].setText(_("EPISODE DETAILS"))
        self["selection_hint"].setText(_("OK  Play episode"))

        def show_rows(valid):
            _r57_paint=time.monotonic()
            valid=[dict(x) for x in valid if isinstance(x,dict)]
            parent_id=(self.series_item or {}).get("series_id") or (self.series_item or {}).get("id") or (self.series_item or {}).get("series_uid")
            parent_title=(self.series_item or {}).get("original_name") or (self.series_item or {}).get("original_title") or (self.series_item or {}).get("name") or (self.series_item or {}).get("title") or ""
            for episode_item in valid:
                if parent_id not in (None,""):episode_item.setdefault("_series_id",parent_id)
                if parent_title:episode_item.setdefault("_series_title",parent_title)
            self.level="episodes";self.content_items=valid
            self._episode_state_cache={}
            self._episode_quality_cache={}
            try:
                if valid:self["runtime_text"].setText((_("%d episode")%len(valid) if len(valid)==1 else _("%d episodes")%len(valid)));self._fit_compact_text("runtime_text",max_size=24,min_size=17,padding=3)
            except Exception as exc:optional_failure("ui.series_episode_count",exc)
            _season_key=self._season_cursor_key(season)
            _restore_episode=max(0,min(int((getattr(self,"_last_episode_index_by_season",{}) or {}).get(_season_key,0) or 0),len(valid)-1)) if valid else 0
            self._render_episode_rows(_restore_episode)
            self["counter"].setText("")
            self["status"].setText("" if valid else _("No episodes"))
            _perf57_series("episodes_paint",rows=len(valid),render_ms=int((time.monotonic()-_r57_paint)*1000.0))

        embedded=(season.get("episodes") if isinstance(season,dict) else None) or (season.get("series") if isinstance(season,dict) else None)
        embedded_rows=[]
        _r57_embedded=time.monotonic()
        if isinstance(embedded,list) and embedded:
            try:
                embedded_rows=self.client.series_episodes(self.series_item,season) or []
                embedded_rows=[dict(x) for x in embedded_rows if isinstance(x,dict)]
            except Exception as exc:
                optional_failure("ui.series_embedded_episode_rows",exc);embedded_rows=[]
        _r57_embedded_ms=int((time.monotonic()-_r57_embedded)*1000.0)
        _r57_cache=time.monotonic()
        cached,updated=_series_cache_read(self.profile,self.series_item,"episodes",season)
        _r57_cache_ms=int((time.monotonic()-_r57_cache)*1000.0)
        if embedded_rows:
            # The seasons catalogue already owns these embedded rows on HDD/RAM.
            # Paint first and skip a redundant fsync before the user sees them.
            cached=embedded_rows;updated=int(time.time())
        age=max(0,int(time.time())-int(updated or 0)) if updated else 10**9
        if cached:
            show_rows(cached)
        else:
            self["status"].setText(_("Loading episodes..."))

        def prepare_from_rows(valid,handle):
            valid=[dict(x) for x in valid if isinstance(x,dict)]
            if handle.cancel_event.is_set():return {"rows":[],"states":[],"qualities":[],"parent_quality":""}
            states=load_content_states(self.profile,"episode",valid) if valid else []
            if handle.cancel_event.is_set():return {"rows":[],"states":[],"qualities":[],"parent_quality":""}
            try:qualities=load_content_qualities(self.profile,"episode",valid) if valid else []
            except Exception as exc:
                optional_failure("ui.series_episode_quality_bulk",exc);qualities=["" for _ in valid]
            try:parent_quality=load_content_quality(self.profile,"series",self.series_item or {})
            except Exception as exc:
                optional_failure("ui.series_parent_quality",exc);parent_quality=""
            return {"rows":valid,"states":states,"qualities":qualities,"parent_quality":parent_quality}

        def apply_payload(payload):
            payload=payload if isinstance(payload,dict) else {}
            valid=list(payload.get("rows") or [])
            states=list(payload.get("states") or [])
            qualities=list(payload.get("qualities") or [])
            # Only replace catalogue if it changed; state/quality may update in place.
            if valid:
                old_sig=[str((x or {}).get("id") or (x or {}).get("episode_id") or (x or {}).get("episode") or (x or {}).get("number") or "") for x in (self.content_items or [])]
                new_sig=[str((x or {}).get("id") or (x or {}).get("episode_id") or (x or {}).get("episode") or (x or {}).get("number") or "") for x in valid]
                if new_sig!=old_sig:show_rows(valid)
            current=list(self.content_items or [])
            self._episode_state_cache={id(item):state for item,state in zip(current,states)}
            self._episode_quality_cache={id(item):quality for item,quality in zip(current,qualities)}
            self._episode_parent_quality=str(payload.get("parent_quality") or "")
            if self.level=="episodes":
                self._render_episode_rows(self["list"].getSelectedIndex() if current else 0)
                self._refresh_series_badges(len(current))

        # Fresh cached/embedded catalogue: no portal request. Only enrich local
        # watch-state/quality in the background.
        if cached and age<_SERIES_CATALOG_TTL:
            self._run_series_async(lambda handle:prepare_from_rows(cached,handle),apply_payload,lambda e:None)
            _perf57_series("episodes_ready",source=("embedded" if embedded_rows else "cache"),rows=len(cached),embedded_ms=_r57_embedded_ms,cache_read_ms=_r57_cache_ms,age_s=age,total_ms=int((time.monotonic()-_r57_start)*1000.0))
            return

        def prepare(handle):
            rows=self.client.series_episodes(self.series_item,season,cancel_event=handle.cancel_event) or []
            valid=[dict(x) for x in rows if isinstance(x,dict)]
            if valid:_series_cache_write(self.profile,self.series_item,"episodes",valid,season)
            return prepare_from_rows(valid,handle)

        self._run_series_async(
            prepare,
            apply_payload,
            lambda e:self["status"].setText(_("Episodes failed: %s")%e) if not cached else None,
        )
        _perf57_series("episodes_async",cached=len(cached or []),embedded=len(embedded_rows or []),embedded_ms=_r57_embedded_ms,cache_read_ms=_r57_cache_ms,total_ms=int((time.monotonic()-_r57_start)*1000.0))

    def select(self):
        if getattr(self,"_screen_closed",False):
            return
        idx = self["list"].getSelectedIndex()
        if not (0 <= idx < len(self.content_items)):
            return
        item = self.content_items[idx]
        if self.level == "seasons":
            self._last_season_index=max(0,int(idx or 0))
            self.load_episodes(item)
        else:
            if isinstance(self.current_season,dict):
                self._last_episode_index_by_season[self._season_cursor_key(self.current_season)]=max(0,int(idx or 0))
            self.play_episode(item)

    def _refresh_episode_progress_after_player(self, selected_index=0):
        """Reload persisted resume state after Player closes, then repaint laser."""
        current=list(self.content_items or [])
        if self.level != "episodes" or not current:
            return self._render_episode_rows(selected_index)
        def prepare(handle):
            return load_content_states(self.profile,"episode",current) if not handle.cancel_event.is_set() else []
        def apply(states):
            states=list(states or [])
            self._episode_state_cache={id(item):state for item,state in zip(current,states)}
            self._render_episode_rows(selected_index)
        if not self._run_series_async(prepare,apply,lambda e:self._render_episode_rows(selected_index)):
            self._render_episode_rows(selected_index)

    def play_episode(self, item):
        command = item.get("cmd") or item.get("command") or item.get("url")
        if not command:
            self["status"].setText(_("Episode has no stream command"))
            return
        self["status"].setText(_("Creating episode stream..."))
        def ok(url):
            url = url if isinstance(url,str) else ""
            name = str(item.get("name") or item.get("title") or item.get("episode_name") or _("Episode"))
            try:
                self["status"].setText(_("Opening player: %s") % name)
                selected_index = self["list"].getSelectedIndex()
                for _idx,_candidate in enumerate(self.content_items):
                    if _candidate is item or _candidate == item:
                        selected_index=_idx;break
                def closed(result=None):
                    if isinstance(result,dict) and isinstance(result.get("next_episode"),dict):
                        self.play_episode(result.get("next_episode"));return
                    engine = result.get("engine") if isinstance(result, dict) else load_settings().get("service_type", 4097)
                    if isinstance(result,dict) and result.get("quality"):
                        try:
                            remember_content_quality(self.profile,"episode",item,result.get("quality"),result.get("video_width",0),result.get("video_height",0))
                            # Parent-series quality is still useful as a fallback for
                            # episodes that have not yet been played, but the exact
                            # episode keeps its own learned decoder resolution.
                            remember_content_quality(self.profile,"series",self.series_item,result.get("quality"),result.get("video_width",0),result.get("video_height",0))
                            self["quality_text"].setText(str(result.get("quality") or ""))
                            self._refresh_detail_visuals()
                        except Exception as exc: optional_failure("ui.series_quality_return",exc)
                    try:self._restore_details_visual_state()
                    except Exception as exc:optional_failure("ui.series_player_visual_restore",exc)
                    self["status"].setText(_("Player closed  •  engine %s") % engine)
                    self._refresh_episode_progress_after_player(selected_index)
                engine=_configured_playback_engine()
                payload=_player_payload(item, self.profile, self.series_item, "episode")
                payload["_player_client_ref"]=self.client
                # Keep the player's InfoBar poster identical to the authoritative
                # series poster used by the episode Information overlay.  The
                # generic player payload can still see an older episode image in
                # cache; pin the final parent-series path here at the handoff.
                try:
                    state = getattr(self, "_details_visual_state", {}) or {}
                    player_poster = str(
                        state.get("detail_poster") or state.get("poster") or
                        getattr(self, "_poster_authoritative_source", "") or
                        getattr(self, "_image_displayed_path", "") or
                        self.series_item.get("_poster_source_local") or
                        self.series_item.get("_poster_local") or ""
                    )
                    if player_poster and os.path.isfile(player_poster):
                        payload["_player_poster"] = player_poster
                        payload["_adaptive_source_local"] = player_poster
                        # Recent/Continue is persisted from _history_item, not from
                        # the final player payload.  Pin the same authoritative
                        # parent-series poster there too so Home cannot resurrect
                        # an older episode/portal poster after playback closes.
                        history_item = payload.get("_history_item")
                        if isinstance(history_item, dict):
                            history_item["_player_poster"] = player_poster
                            history_item["_adaptive_source_local"] = player_poster
                except Exception as exc:
                    optional_failure("ui.series_player_poster_pin", exc)
                try:
                    payload["_series_title"]=str(self.series_item.get("name") or self.series_item.get("title") or "")
                    payload["_raw_series_title"]=str(self.series_item.get("_raw_name") or self.series_item.get("name") or self.series_item.get("title") or "")
                    if isinstance(self.current_season,dict):
                        payload["_season_number"]=self.current_season.get("season_number") or self.current_season.get("season") or self.current_season.get("id")
                    payload["_episode_number"]=item.get("episode_number") or item.get("episode") or item.get("number") or (selected_index+1)
                except Exception as exc:optional_failure("ui.subtitle_episode_identity",exc)
                if 0 <= selected_index + 1 < len(self.content_items) and isinstance(self.content_items[selected_index+1],dict):payload["_next_episode_item"]=dict(self.content_items[selected_index+1])
                self.session.openWithCallback(closed, UltraStalkerPlayer, url.strip(), name, "episode", engine, payload)
            except Exception as exc:
                self["status"].setText(_("Player failed: %s") % exc)
        episode_id = item.get("id") or item.get("episode_id") or item.get("series") or item.get("number")
        self._run_async(lambda handle:self.client.create_link(item, "series", episode_id,cancel_event=handle.cancel_event), ok, lambda e:ok(""))

    def open_episode_download_menu(self):
        if self.level != "episodes":
            self.session.open(DownloadsManagerScreen, getattr(self,"_adaptive_source_local","") or (self.series_item.get("_backdrop_source_local") if isinstance(self.series_item,dict) else ""));return
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):return
        choices=[(_("Download Episode"),"episode"),(_("Download Season"),"season"),(_("Downloads Manager"),"manager"),(_("Mark watched / unwatched"),"watched")]
        self.session.openWithCallback(self._episode_download_selected,ChoiceBox,title=_("Download"),list=choices)

    def _episode_download_selected(self, choice):
        if not choice:return
        action=choice[1]
        if action=="manager":self.session.open(DownloadsManagerScreen, getattr(self,"_adaptive_source_local","") or (self.series_item.get("_backdrop_source_local") if isinstance(self.series_item,dict) else ""));return
        if action=="watched":self.toggle_selected_watched();return
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):return
        rows=[(idx,self.content_items[idx])] if action=="episode" else list(enumerate(self.content_items))
        added=0; skipped=0
        parent_id=(self.series_item or {}).get("series_id") or (self.series_item or {}).get("id") or (self.series_item or {}).get("series_uid")
        for ep_index,item in rows:
            item=dict(item)
            episode_id=item.get("id") or item.get("episode_id") or item.get("series") or item.get("number")
            job=episode_job(self.profile,self.series_item,self.current_season,item,ep_index)
            def resolver(_item=item,_eid=episode_id,_parent=parent_id):
                return self.client.create_link(_item,"series",_eid or _parent)
            ok,msg=DOWNLOADS.add(job,resolver)
            if ok:added+=1
            else:skipped+=1
        self["status"].setText((_("Queued %d download(s)")%added) + ((_(" • %d already queued")%skipped) if skipped else ""))

    def toggle_selected_watched(self):
        if self.level != "episodes":
            self["status"].setText(_("Open a season first"))
            return
        idx=self["list"].getSelectedIndex()
        if not (0<=idx<len(self.content_items)):
            return
        item=self.content_items[idx];state=self._episode_state_cache.get(id(item),{});watched=not bool(state.get("completed"))
        try:
            mark_watched(self.profile,"episode",item,watched)
            self["status"].setText(_("Marked watched") if watched else _("Marked unwatched"))
            self._render_episode_rows(idx)
        except Exception as exc:
            self["status"].setText(_("Watch-state update failed: %s")%exc)

    def reload_current(self):
        if self.level == "episodes" and self.current_season is not None:
            self.load_episodes(self.current_season)
        else:
            self.load_seasons()

    def show_information(self):
        # Episodes use the exact same information overlay as the player/details
        # screen.  Keep the seasons level on the parent details implementation.
        if self.level != "episodes":
            return ContentDetailsScreen.show_information(self)

        idx = self["list"].getSelectedIndex()
        if not (0 <= idx < len(self.content_items)):
            return
        item = self.content_items[idx]
        if not isinstance(item, dict):
            return

        from .services.player_native import PlayerInformationOverlay

        parent = self.series_item if isinstance(self.series_item, dict) else {}
        payload = dict(parent)
        payload.update(dict(item))

        title = _clean_display_text(
            item.get("name") or item.get("title") or item.get("episode_name") or
            _("Episode %s") % (item.get("episode_number") or item.get("episode") or item.get("number") or (idx + 1)),
            90,
        )

        # Episode description wins; parent-series metadata is the fallback for
        # cast/director/writer when portals omit those fields per episode.
        desc = self._selected_description(item) or payload.get("description") or payload.get("overview") or ""
        if desc:
            payload["description"] = desc
        try:
            visible_cast = self["cast_text"].getText()
            if visible_cast and not (payload.get("cast") or payload.get("actors") or payload.get("actor")):
                payload["cast"] = visible_cast
        except Exception:
            pass
        try:
            visible_director = self["director_text"].getText()
            if visible_director and not (payload.get("director") or payload.get("directors")):
                payload["director"] = visible_director
        except Exception:
            pass
        try:
            visible_writer = self["writer_text"].getText()
            if visible_writer and not (payload.get("writer") or payload.get("writers") or payload.get("creator")):
                payload["writer"] = visible_writer
        except Exception:
            pass

        poster_path = ""
        try:
            state = getattr(self, "_details_visual_state", {}) or {}
            poster_path = str(
                state.get("detail_poster") or state.get("poster") or
                getattr(self, "_poster_authoritative_source", "") or
                getattr(self, "_image_displayed_path", "") or
                parent.get("_poster_source_local") or parent.get("_poster_local") or ""
            )
            if poster_path and not os.path.isfile(poster_path):
                poster_path = ""
        except Exception:
            poster_path = ""

        ep_num = item.get("episode_number") or item.get("episode") or item.get("number") or (idx + 1)
        quality = self._episode_quality_badge(item, ep_num)
        meta_parts = [_("EPISODE")]
        if quality and not str(quality).startswith("EP "):
            meta_parts.append(str(quality))
        date_text = self._episode_date(item)
        if date_text:
            meta_parts.append(date_text)
        meta = "   •   ".join(meta_parts)

        self.session.open(PlayerInformationOverlay, title, payload, "episode", meta, poster_path)

    def back(self):
        if self.level == "episodes":
            try:
                _idx=self["list"].getSelectedIndex()
                if isinstance(self.current_season,dict):
                    self._last_episode_index_by_season[self._season_cursor_key(self.current_season)]=max(0,int(_idx or 0))
            except Exception:pass
            self.load_seasons()
        else:
            self.close()

