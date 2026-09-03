"""Premium grid base extracted from ui.py without changing class behavior."""

from Screens.Screen import Screen
from .ui_async import AsyncScreenMixin
from .ui_grid_artwork import GridArtworkMixin
import time
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag
from .core.call_compat import call_compatible
from .log import diagnostic_failure

def configure_grid_base(**deps):
    globals().update(deps)

class PremiumGridBase(Screen, AsyncScreenMixin, GridArtworkMixin):
    columns=1; page_size=1; card_positions=[]; image_size=(100,100); placeholder="placeholder_live_x.png"
    selection_asset="grid_live_selected_921.png"

    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        Screen.__init__(self,session);self._async_init();self.onClose.append(self._stop_async)
        self._ui_diag_open_mono=time.monotonic();self._ui_diag_page_request_mono=self._ui_diag_open_mono;self._ui_diag_first_poster_logged=False
        _ui_diag("ui_open",screen="grid",media_type=media_type)
        self.profile=profile;self.client=client;self.media_type=media_type;self.genre=str(genre or "*")
        # Settings are immutable for the lifetime of a grid screen. Reading the
        # JSON file once avoids 12-24 filesystem reads every time a page is
        # rendered while still picking up changes when the screen is reopened.
        self._grid_settings=load_settings()
        self.category_title=_clean_display_text(category_title,70) or ("Live TV" if media_type=="itv" else ("Movies" if media_type=="vod" else "Series"))
        self._nav_key=(str(profile.get("portal") or "").rstrip("/").lower(),str(media_type),self.genre)
        self.page=1;self.grid_items=[];self.index=0;self._grid_item_state={};self._explicit_quality_cache={}
        self._restore_page=1;self._restore_index=0
        # Folder-local Search + Sort view state. Movies/Series only.
        self._folder_catalog=None
        self._folder_catalog_loading=False
        self._folder_catalog_waiters=[]
        self._search_query=""
        self._sort_mode=0
        self._view_items=None
        self._view_active=False
        self._folder_artwork_cache_running=False
        self._folder_artwork_cache_done=0
        self._folder_artwork_cache_total=0
        self._folder_artwork_progress=queue.Queue()
        self._session_nav_key=(current_plugin_launch(),)+tuple(self._nav_key)
        try:
            _g=_GRID_NAV_STATE.get(self._session_nav_key,{})
            self._restore_page=max(1,int(_g.get("page",1)));self._restore_index=max(0,int(_g.get("index",0)))
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._portal_page_cache=OrderedDict();self._portal_page_size=0;self._portal_total=0
        self._portal_page_cache_limit=8;self._last_nav_at=0.0;self._nav_burst_until=0.0
        self._prepared_page_cache=OrderedDict();self._prepared_page_lock=threading.RLock();self._prepared_page_pending=set()
        # Per-screen visual RAM. Keyed by stable content identity, not page
        # number, so revisiting/sorting never blanks cards or re-downloads art.
        self._page_visual_ram=OrderedDict();self._page_visual_ram_limit=512
        self._grid_visual_locks={}
        try:
            _desk=getDesktop(0).size();self._grid_sx=float(_desk.width())/1920.0;self._grid_sy=float(_desk.height())/1080.0
        except Exception:
            self._grid_sx=1.0;self._grid_sy=1.0
        self._scaled_card_positions=[(int(x*self._grid_sx),int(y*self._grid_sy)) for x,y in self.card_positions]
        self._page_load_handle=None; self._pending_page_request=None
        self._page_prefetch_futures=[]
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

        # beta51: HDD-only visible poster watcher. Background jobs may finish after
        # their one-shot UI event became stale due to page/generation timing. This
        # watcher never uses network; it simply notices newly persisted posters
        # for the 12 visible cards and paints them immediately.
        self._visible_poster_watch_timer=eTimer();self._visible_poster_watch_conn=None
        try:self._visible_poster_watch_conn=self._visible_poster_watch_timer.timeout.connect(self._poll_visible_poster_cache)
        except Exception:self._visible_poster_watch_timer.callback.append(self._poll_visible_poster_cache)
        self._page_prefetch_seen=set();self._page_quality_seen=set();self._page_art_retry_count={};self._page_prefetch_lock=threading.RLock();self._page_prefetch_generation=0;self._page_prefetch_cancel=threading.Event()
        # beta47: poster-only progressive folder warmer. It never fetches backdrop/details.
        self._progressive_poster_cancel=threading.Event();self._progressive_poster_started=False
        self.onClose.append(self._stop_progressive_poster_prefetch)
        self.onClose.append(self._stop_visible_poster_watch)
        self.onClose.append(self._stop_grid_ui_hooks)
        self.onClose.append(self._grid_art_stop);self.onClose.append(self._stop_grid_epg);self.onClose.append(self._stop_detail_prefetch);self.onClose.append(self._stop_grid_focus_timer);self.onClose.append(self._stop_series_hierarchy_prefetch);self.onClose.append(self._stop_grid_idle_warm_timer);self.onClose.append(self._runtime_grid_close)
        self["brand"]=Label("");self["section"]=Label((("LIVE TV  /  "+self.category_title[:28]) if media_type=="itv" else self.category_title[:48]));self["title"]=Label(self.category_title)
        self["clock"]=Label(time.strftime("%H:%M"));self._grid_clock=eTimer();self._grid_clock_conn=None
        try:self._grid_clock_conn=self._grid_clock.timeout.connect(self._update_grid_clock)
        except Exception:self._grid_clock.callback.append(self._update_grid_clock)
        try:self._grid_clock.start(30000,False)
        except Exception as exc:optional_failure("ui",exc)
        self.onClose.append(self._stop_grid_clock)
        self.onClose.append(self._ui_diag_grid_close)
        self["rating"]=Label("");self["meta1"]=Label("");self["meta2"]=Label("")
        self["status"]=Label("Loading...");self["page_label"]=Label("Page 1")
        self["red"]=Label("Search" if media_type in ("vod","series") else "Back");self["green"]=Label("Favorite")
        self["yellow"]=Label("Default" if media_type in ("vod","series") else "Previous page");self["blue"]=Label("Cache Artwork" if media_type in ("vod","series") else "Next page")
        self["selection"]=Pixmap()
        if media_type in ("vod","series"):
            self["page_adaptive_bg"]=Pixmap()
            self["poster_folder_bg"]=Pixmap();self["poster_title_bg"]=Pixmap();self["poster_clock_bg"]=Pixmap();self["poster_counter_bg"]=Pixmap()
            self["date_label"]=Label(time.strftime("%A, %d %B %Y"))
            self._poster_hud_last={};self._poster_hud_source="";self._poster_hud_pending=set();self._poster_hud_jobs=queue.Queue()
            self._grid_mood_jobs=queue.Queue();self._grid_mood_token=0;self._grid_mood_source="";self._grid_mood_pending=""
        slot_names=[]
        for pos in range(self.page_size):
            name="art%d"%pos;slot_names.append(name);self[name]=Pixmap();self["item_title%d"%pos]=Label("");self["item_meta%d"%pos]=Label("")
            if media_type in ("vod","series"):self["card_chrome%d"%pos]=Pixmap()
        self._grid_art_init(slot_names,self.image_size,profile,client)
        _red_action=self.open_folder_search if media_type in ("vod","series") else self.close
        _yellow_action=self.cycle_folder_sort if media_type in ("vod","series") else self.previous_page
        _blue_action=self.cache_folder_artwork if media_type in ("vod","series") else self.next_page
        self["actions"]=ActionMap(["OkCancelActions","ColorActions","DirectionActions","MenuActions","InfoActions"],{
            "cancel":self.close,"red":_red_action,"ok":self.open_selected,"left":self.move_left,"right":self.move_right,
            "up":self.move_up,"down":self.move_down,"green":self.toggle_selected_favorite,"yellow":_yellow_action,
            "blue":_blue_action,"menu":self.open_menu,"info":self.show_information},-1)
        self.onLayoutFinish.append(self._grid_layout_ready)
        if media_type in ("vod","series"):self.onLayoutFinish.append(self._refresh_search_sort_controls)
        try:self.onHide.append(self._grid_hidden_release)
        except Exception as exc:optional_failure("ui.grid_hide_hook",exc)
        try:self.onShown.append(self._grid_shown_resume)
        except Exception as exc:optional_failure("ui.grid_show_hook",exc)

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
            if not (display or poster):
                self._page_visual_ram.pop(key,None)
                return {}
            out={}
            if display:out["display"]=display
            if poster:out["poster"]=poster
            if palette:out["palette"]=palette
            if card:out["card"]=card
            if row.get("provider_locked"):out["provider_locked"]=True
            self._page_visual_ram.move_to_end(key)
            return out
        except Exception as exc:
            optional_failure("ui.page_visual_get",exc)
            return {}

    def _page_visual_put(self,item,display=None,poster=None,palette=None,card=None,provider_locked=None):
        if self.media_type not in ("vod","series") or not isinstance(item,dict):
            return
        key=self._page_visual_key(item)
        if not key:return
        try:
            row=dict(self._page_visual_ram.get(key) or {})
            for field,value in (("display",display),("poster",poster),("palette",palette),("card",card)):
                valid=self._page_visual_valid_path(value)
                if valid:row[field]=valid
            if provider_locked is not None:
                row["provider_locked"]=bool(provider_locked)
            if not (self._page_visual_valid_path(row.get("display")) or self._page_visual_valid_path(row.get("poster"))):
                return
            self._page_visual_ram[key]=row
            self._page_visual_ram.move_to_end(key)
            while len(self._page_visual_ram)>int(self._page_visual_ram_limit or 512):
                self._page_visual_ram.popitem(last=False)
        except Exception as exc:
            optional_failure("ui.page_visual_put",exc)

    def _remember_grid_visual(self,slot,display=None,poster=None,palette=None,card=None,provider_locked=None):
        try:
            pos=int(slot)
            if pos<0 or pos>=len(self.grid_items):return
            item=self.grid_items[pos]
            if display is None:display=(getattr(self,"_grid_slot_paths",{}) or {}).get(pos)
            if palette is None:palette=(getattr(self,"_grid_palette_sources",{}) or {}).get(pos)
            if card is None:card=(getattr(self,"_grid_card_paths",{}) or {}).get(pos)
            self._page_visual_put(item,display=display,poster=poster,palette=palette,card=card,provider_locked=provider_locked)
        except Exception as exc:
            optional_failure("ui.page_visual_remember",exc)

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
        except Exception as exc:optional_failure("ui",exc)
        self._update_grid_clock()
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

    def _drain_visible_poster_results(self, limit=1):
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
            if not apath or not os.path.isfile(str(apath)):
                continue
            if matched_pos is not None:
                try:
                    target_item=self.grid_items[matched_pos]
                    if isinstance(ameta,dict) and ameta.get("tmdb_id"):
                        target_item["_locked_tmdb_id"]=ameta.get("tmdb_id")
                        target_item["_locked_tmdb_type"]=ameta.get("media_type") or ("tv" if self.media_type=="series" else "movie")
                        self._grid_item_state.setdefault(id(target_item),{})["tmdb_id"]=ameta.get("tmdb_id")
                    palette_source=str((ameta or {}).get("palette_source") or "") if isinstance(ameta,dict) else ""
                    if palette_source and os.path.isfile(palette_source):
                        self._grid_palette_sources[matched_pos]=palette_source
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
        # Paint a tiny progressive stream of posters even during navigation.
        if not self._screen_closed and self.media_type in ("vod","series"):
            try:
                self._drain_visible_poster_results(1)
                self._grid_drain_art_jobs(limit=1)
            except Exception as exc:
                optional_failure("ui.poster_micro_drain",exc)
        if time.monotonic() < float(getattr(self,"_nav_burst_until",0.0) or 0.0):
            return
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
        for value in (
            item.get("rating_imdb"),item.get("imdb_rating"),item.get("rating"),
            item.get("kinopoisk_rating"),item.get("vote_average"),
        ):
            match=re.search(r"\d+(?:\.\d+)?",str(value or ""))
            if match:
                try:return float(match.group(0))
                except Exception as exc:diagnostic_failure("ui.grid.failsoft.341",exc)
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

    def _folder_title_value(self,item):
        raw=item.get("name") or item.get("title") or item.get("original_name") or item.get("original_title") or ""
        try:return _catalogue_title(raw) if self.media_type in ("vod","series") else premium_title(raw,(self._grid_settings or {}).get("clean_titles",True))
        except Exception:return str(raw or "")

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
        label=self._FOLDER_SORT_LABELS[self._sort_mode % len(self._FOLDER_SORT_LABELS)]
        try:self["red"].setText("Search")
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.376",exc)
        try:self["yellow"].setText(label)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.378",exc)
        try:
            blue_label=str(self["blue"].getText() or "Cache Artwork")
            units=sum(1.55 if ord(ch)>0x2ff else (0.60 if ch in " ilI1|.,:'" else 1.0) for ch in blue_label)
            size=22 if units<=11 else (20 if units<=15 else (18 if units<=19 else 16))
            if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",size))
        except Exception as exc:optional_failure("ui.folder_cache_label_font",exc)
        # The yellow box geometry never changes. Only the font scales, always
        # centered horizontally/vertically by the existing skin.
        try:
            units=sum(1.55 if ord(ch)>0x2ff else (0.60 if ch in " ilI1|.,:'" else 1.0) for ch in label)
            size=22 if units<=10 else (20 if units<=14 else (18 if units<=18 else 16))
            if self["yellow"].instance is not None:self["yellow"].instance.setFont(gFont("Regular",size))
        except Exception as exc:optional_failure("ui.folder_sort_label_font",exc)
        try:
            base=("MOVIES" if self.media_type=="vod" else "SERIES")+"  /  "+self.category_title[:28]
            if self._search_query:
                base+="  •  SEARCH: "+_clean_display_text(self._search_query,28)
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
                item=(dict(row) if row.get("_xtream") else _strip_portal_artwork(row))
                identity=self._folder_item_identity(item,len(rows)+pos)
                if identity in seen:continue
                seen.add(identity);rows.append(item);added+=1
                if total and len(rows)>=total:break
            if total and len(rows)>=total:break
            if not added:break
            if not total and len(page_rows)<native:break
        return rows

    def _ensure_folder_catalog(self,callback):
        if self.media_type not in ("vod","series"):return
        if isinstance(self._folder_catalog,list):
            callback(self._folder_catalog);return
        self._folder_catalog_waiters.append(callback)
        if self._folder_catalog_loading:return
        self._folder_catalog_loading=True
        self["status"].setText("Loading folder index...")
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
        rows=list(self._folder_catalog or [])
        query=self._folder_search_norm(self._search_query)
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
                self._page_quality_seen.clear()
                self._page_art_retry_count.clear()
        except Exception as exc:optional_failure("ui.folder_prefetch_reset",exc)

        rows=self._build_folder_view()
        total=len(rows);total_pages=max(1,(total+self.page_size-1)//self.page_size)
        target=max(1,min(int(page),total_pages))
        start=(target-1)*self.page_size
        valid=rows[start:start+self.page_size]
        self.page=target;self.grid_items=valid
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
            self._prefetch_visible_page_details(priority_index=self.index)
            try:self._start_visible_poster_watch()
            except Exception as exc:optional_failure("ui.visible_poster_view_watch_start",exc)
            try:self._start_progressive_poster_prefetch()
            except Exception as exc:optional_failure("ui.progressive_poster_view_start",exc)
        else:
            try:self["selection"].hide()
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.507",exc)
            try:self["title"].setText(self.category_title);self["rating"].setText("");self["meta1"].setText("");self["meta2"].setText("")
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.509",exc)
            self["status"].setText("No matching content")

    def _drain_folder_artwork_progress(self):
        while True:
            try:done,total=self._folder_artwork_progress.get_nowait()
            except queue.Empty:break
            except Exception:break
            self._folder_artwork_cache_done=int(done or 0)
            self._folder_artwork_cache_total=int(total or 0)
            if self._folder_artwork_cache_running and total:
                # The worker has already processed every catalogue row once done==total.
                # Do not leave the UI stuck in an artificial "running" state while
                # executor teardown/callback delivery finishes in the background.
                if int(done or 0) >= int(total or 0):
                    self._folder_artwork_cache_running=False
                    try:self["status"].setText("Artwork ready • %d/%d"%(done,total))
                    except Exception as exc:diagnostic_failure("ui.grid.cache_done_status",exc)
                    try:
                        self["blue"].setText("Cache Artwork")
                        if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",22))
                    except Exception as exc:diagnostic_failure("ui.grid.cache_done_blue",exc)
                else:
                    try:self["status"].setText("Caching %d / %d"%(done,total))
                    except Exception as exc:diagnostic_failure("ui.grid.failsoft.521",exc)
                    try:
                        self["blue"].setText("%d / %d"%(done,total))
                        if self["blue"].instance is not None:self["blue"].instance.setFont(gFont("Regular",22))
                    except Exception as exc:diagnostic_failure("ui.grid.failsoft.525",exc)

    def _cache_folder_artwork_worker(self, rows, cancel_event=None):
        cfg=self._grid_settings or {};credential=str(cfg.get("tmdb_credential") or "").strip()
        tmdb_available=bool(cfg.get("tmdb_enabled",True) and credential)
        profile=self.profile;media_type=self.media_type
        language=cfg.get("tmdb_language","ar-EG")
        timeout=min(7,max(3,int(cfg.get("timeout",10) or 10)))
        total=len(rows)
        stats={"downloaded":0,"hot":0,"failed":0,"recovered":0}
        stats_lock=threading.Lock()

        def cancelled():
            return bool(cancel_event is not None and cancel_event.is_set())

        def valid_file(path,min_size=1024):
            try:return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path))>min_size)
            except Exception:return False

        def process_item(item):
            if cancelled():raise _ArtworkCancelled()
            downloaded=0;hot=0;failed=0;recovered=0
            try:
                # beta59: TRUE HDD-FIRST blue-button check.  Every successful
                # artwork save is promoted into content-owned poster/backdrop files.
                # Those two stat() calls are therefore the cheapest authoritative
                # answer to "did we already cache this title?".  A complete pair
                # skips JSON manifests, canonical alias scans, Xtream get_*_info,
                # TMDB, image normalisation and derivative rebuilding entirely.
                # This makes a second folder check scale like local HDD directory
                # reads instead of repeating the enrichment pipeline.
                owned={}
                try:owned=_load_content_art(profile,media_type,item) or {}
                except Exception as exc:optional_failure("ui.folder_cache_owned_preflight",exc)
                poster=str((owned or {}).get("poster") or "")
                backdrop=str((owned or {}).get("backdrop") or "")
                poster_ok=valid_file(poster,256)
                backdrop_ok=valid_file(backdrop,256)
                if poster_ok and backdrop_ok:
                    return 0,1,0,0

                # Older installs may predate content-owned promotion but already
                # have a complete visual bundle on HDD.  One local bundle lookup
                # is still vastly cheaper than touching provider/TMDB.  Keep any
                # partial local hit so the later resolver only fills the missing
                # half of the pair.
                visual={}
                try:visual=_load_visual_bundle(profile,media_type,item,snapshot={}) or {}
                except Exception as exc:optional_failure("ui.folder_cache_visual_preflight",exc)
                if not poster_ok:
                    vp=str((visual or {}).get("poster") or "")
                    if valid_file(vp,256):poster=vp;poster_ok=True
                if not backdrop_ok:
                    vb=str((visual or {}).get("backdrop") or "")
                    if valid_file(vb,256):backdrop=vb;backdrop_ok=True
                if poster_ok and backdrop_ok:
                    return 0,1,0,0

                # Merge every trusted HDD identity source before deciding artwork
                # is unavailable. Details may already know the exact TMDB id even
                # when the artwork manifest was created during a transient CDN miss.
                snap=load_artwork_v2_manifest(profile,media_type,item) or {}
                detail=load_detail_snapshot(profile,media_type,item) or {}
                # beta28: Portal and Xtream share the same HDD-first blue-button
                # cache semantics. Older Stalker runs may already have the exact
                # verified artwork in the cross-source/shared snapshot even when
                # this portal's per-item manifest has not been rebound yet. Merge
                # that local snapshot BEFORE any provider/TMDB network work.
                shared=load_shared_detail_snapshot(profile,media_type,item) or {}
                merged=dict(shared if isinstance(shared,dict) else {})
                if isinstance(detail,dict):merged.update({k:v for k,v in detail.items() if v not in (None,"")})
                if isinstance(snap,dict):merged.update({k:v for k,v in snap.items() if v not in (None,"")})

                # Cross-portal bridge: if either source already knows an external
                # identity, hydrate from the portal-independent snapshot before
                # doing any network work.
                global_meta={}
                known_tmdb=merged.get("tmdb_id") or item.get("tmdb_id") or item.get("tmdbid")
                known_imdb=merged.get("imdb_id") or item.get("imdb_id") or item.get("imdb")
                if known_tmdb:
                    try:global_meta=load_detail_snapshot_by_tmdb(merged.get("media_type") or media_type,known_tmdb) or {}
                    except Exception:global_meta={}
                if not global_meta and known_imdb:
                    try:global_meta=load_detail_snapshot_by_imdb(known_imdb) or {}
                    except Exception:global_meta={}
                if isinstance(global_meta,dict) and global_meta:
                    global_meta.update({k:v for k,v in merged.items() if v not in (None,"")})
                    merged=global_meta
                snap=merged

                # Preserve any content-owned/visual preflight hit.  Snapshot paths
                # are only used to fill a side that is still missing.
                if not poster_ok:
                    sp=str(snap.get("poster_local") or "")
                    if valid_file(sp):poster=sp;poster_ok=True
                if not backdrop_ok:
                    sb=str(snap.get("backdrop_local") or "")
                    if valid_file(sb):backdrop=sb;backdrop_ok=True

                # Identity-aware visual lookup is needed only for a missing side.
                if not (poster_ok and backdrop_ok):
                    try:
                        richer_visual=_load_visual_bundle(profile,media_type,item,snapshot=snap) or {}
                        if richer_visual:visual=richer_visual
                    except Exception as exc:optional_failure("ui.folder_cache_visual_lookup",exc)
                if not poster_ok:
                    vp=str((visual or {}).get("poster") or "")
                    if valid_file(vp):poster=vp;poster_ok=True
                if not backdrop_ok:
                    vb=str((visual or {}).get("backdrop") or "")
                    if valid_file(vb):backdrop=vb;backdrop_ok=True

                # If both originals are already local, this item is a true hot hit.
                # Do not touch Xtream/TMDB and do not rebuild already prepared
                # derivatives. Missing derivatives can be rebuilt locally later.
                if poster_ok and backdrop_ok:
                    hot=1
                    payload={}
                    vg=str((visual or {}).get("grid_thumb") or "")
                    vpb=str((visual or {}).get("prepared_backdrop") or "")
                    if not valid_file(vg,256):
                        try:
                            st=os.stat(poster);raw="%s|%s|%s|%sx%s|us-card-cover-v4"%(poster,int(st.st_mtime),int(st.st_size),int(self._grid_image_size[0]),int(self._grid_image_size[1]))
                            dg=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest();vg=os.path.join(THUMB_CACHE_DIR,"gridposter_%s_%dx%d.png"%(dg,int(self._grid_image_size[0]),int(self._grid_image_size[1])))
                            if not _valid_cache_file(vg,ttl=0):vg=(_build_cover_thumbnail(poster,vg,self._grid_image_size) if getattr(self,"_poster_cover_mode",False) else _build_thumbnail(poster,vg,self._grid_image_size))
                            if vg and os.path.isfile(vg):payload["grid_thumb"]=vg
                        except Exception as exc:optional_failure("ui.folder_cache_hot_thumb",exc)
                    if not valid_file(vpb,256):
                        try:
                            stamp=str(os.path.getmtime(backdrop));digest=hashlib.sha1((backdrop+"|"+stamp+"|us198-present").encode("utf-8","ignore")).hexdigest()[:24]
                            vpb=os.path.join(THUMB_CACHE_DIR,"us220_detail_%s_1620x620.png"%digest)
                            if not _valid_cache_file(vpb,ttl=0):vpb=_build_integrated_backdrop(backdrop,vpb,(1620,620))
                            if vpb and os.path.isfile(vpb):payload["prepared_backdrop"]=vpb
                        except Exception as exc:optional_failure("ui.folder_cache_hot_backdrop",exc)
                    if payload:
                        payload.setdefault("poster",poster);payload.setdefault("backdrop",backdrop)
                        try:_save_visual_bundle(profile,media_type,item,payload,snapshot=snap)
                        except Exception as exc:optional_failure("ui.folder_cache_hot_bundle",exc)
                    return 0,1,0,0

                # M3U+Xtream hybrid and native Xtream use the same authoritative
                # rich provider path. Force info so a stale list-level icon cannot
                # suppress get_vod_info/get_series_info. Persist BOTH poster and
                # backdrop before optional TMDB enrichment.
                if isinstance(item,dict) and item.get("_xtream") and hasattr(self.client,"enrich_provider_artwork") and not (poster_ok and backdrop_ok):
                    try:
                        enriched,provider_poster,provider_backdrop=_download_xtream_provider_pair(
                            item,profile,self.client,media_type,cancel_event,force_info=True,timeout=4.0)
                        if isinstance(enriched,dict):item.update(enriched)
                        if not poster_ok and valid_file(provider_poster,256):
                            poster=provider_poster;poster_ok=True;downloaded=1
                        if not backdrop_ok and valid_file(provider_backdrop,256):
                            backdrop=provider_backdrop;backdrop_ok=True;downloaded=1
                    except Exception as exc:optional_failure("ui.folder_cache_xtream_provider",exc)

                # Canonical artwork is physically global on HDD. Another portal
                # may have no per-item manifest yet while the exact TMDB files are
                # already present from a different server.
                canonical_id=snap.get("tmdb_id") or item.get("tmdb_id") or item.get("tmdbid")
                canonical_type=snap.get("media_type") or ("tv" if media_type=="series" else "movie")
                if canonical_id and not (poster_ok and backdrop_ok):
                    try:
                        cpaths=canonical_art_paths(canonical_type,canonical_id) or {}
                        cp=str(cpaths.get("poster") or "");cb=str(cpaths.get("backdrop") or "")
                        if not poster_ok and valid_file(cp):poster=cp;poster_ok=True
                        if not backdrop_ok and valid_file(cb):backdrop=cb;backdrop_ok=True
                    except Exception as exc:optional_failure("ui.folder_cache_global_canonical",exc)

                # Exact-ID recovery: once identity is known, never fall back to a
                # fresh title search just because artwork is missing.
                locked_item=dict(item)
                locked_id=snap.get("tmdb_id")
                locked_type=snap.get("media_type") or ("tv" if media_type=="series" else "movie")
                if locked_id:
                    locked_item["_locked_tmdb_id"]=locked_id
                    locked_item["_locked_tmdb_type"]=locked_type
                locked_imdb=str(snap.get("imdb_id") or item.get("imdb_id") or item.get("imdb") or "").strip().lower()
                if locked_imdb:
                    locked_item["imdb_id"]=locked_imdb

                data=snap
                if not (poster_ok and backdrop_ok) and tmdb_available:
                    resolver=ArtworkV2(credential,language,timeout)
                    data=resolver.resolve(profile,media_type,locked_item,full=True,cancel_event=cancel_event) or {}
                    poster=str(data.get("poster_local") or poster or "")
                    backdrop=str(data.get("backdrop_local") or backdrop or "")
                    poster_ok=valid_file(poster)
                    backdrop_ok=valid_file(backdrop)

                    # A matched identity with a missing image is retryable, not
                    # "unavailable". Retry the exact TMDB id once more after the
                    # resolver has persisted paths/canonical metadata.
                    retry_id=(data.get("tmdb_id") if isinstance(data,dict) else None) or locked_id
                    if retry_id and not (poster_ok and backdrop_ok) and not cancelled():
                        retry_item=dict(locked_item)
                        retry_item["_locked_tmdb_id"]=retry_id
                        retry_item["_locked_tmdb_type"]=(data.get("media_type") if isinstance(data,dict) else None) or locked_type
                        retry=ArtworkV2(credential,language,timeout).resolve(profile,media_type,retry_item,full=True,cancel_event=cancel_event) or {}
                        if isinstance(retry,dict) and retry.get("matched"):
                            fresh=dict(data if isinstance(data,dict) else {})
                            fresh.update({k:v for k,v in retry.items() if v not in (None,"")})
                            data=fresh
                        poster=str((data or {}).get("poster_local") or poster or "")
                        backdrop=str((data or {}).get("backdrop_local") or backdrop or "")
                        poster_ok=valid_file(poster)
                        backdrop_ok=valid_file(backdrop)
                        if poster_ok or backdrop_ok:recovered=1

                    # Final canonical rebind after resolver/retry.
                    final_id=(data.get("tmdb_id") if isinstance(data,dict) else None) or locked_id
                    final_type=(data.get("media_type") if isinstance(data,dict) else None) or locked_type
                    if final_id and not (poster_ok and backdrop_ok):
                        try:
                            cpaths=canonical_art_paths(final_type,final_id) or {}
                            cp=str(cpaths.get("poster") or "");cb=str(cpaths.get("backdrop") or "")
                            if not poster_ok and valid_file(cp):poster=cp;poster_ok=True
                            if not backdrop_ok and valid_file(cb):backdrop=cb;backdrop_ok=True
                            if poster_ok or backdrop_ok:recovered=1
                        except Exception as exc:optional_failure("ui.folder_cache_final_canonical",exc)
                    if poster_ok or backdrop_ok:downloaded=1
                else:
                    hot=1

                # Build the COMPLETE list/detail visual bundle before this item is
                # counted as done. That makes the next folder open HDD-hot, with no
                # delayed adaptive colour/chrome generation.
                payload={}
                if poster_ok:
                    payload["poster"]=poster
                    try:
                        st=os.stat(poster);raw="%s|%s|%s|%sx%s|us-card-cover-v4"%(poster,int(st.st_mtime),int(st.st_size),int(self._grid_image_size[0]),int(self._grid_image_size[1]))
                        dg=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
                        thumb=os.path.join(THUMB_CACHE_DIR,"gridposter_%s_%dx%d.png"%(dg,int(self._grid_image_size[0]),int(self._grid_image_size[1])))
                        if not _valid_cache_file(thumb,ttl=0):
                            thumb=(_build_cover_thumbnail(poster,thumb,self._grid_image_size) if getattr(self,"_poster_cover_mode",False) else _build_thumbnail(poster,thumb,self._grid_image_size))
                        if thumb and os.path.isfile(thumb):payload["grid_thumb"]=thumb
                    except Exception as exc:optional_failure("ui.folder_cache_thumb",exc)

                    try:
                        card=_grid_card_chrome_from_poster(poster,selected=False)
                        if card and os.path.isfile(card):payload["grid_card"]=card
                    except Exception as exc:optional_failure("ui.folder_cache_adaptive_card",exc)
                    try:
                        # Prebuild the focused clean card too. Runtime will hit the
                        # deterministic file path immediately when focus moves here.
                        _grid_card_chrome_from_poster(poster,selected=True)
                    except Exception as exc:optional_failure("ui.folder_cache_focus_card",exc)
                    try:
                        selection=_grid_selection_asset_from_poster(poster)
                        if selection and os.path.isfile(selection):payload["grid_selection"]=selection
                    except Exception as exc:optional_failure("ui.folder_cache_selection",exc)
                    try:
                        mood=_grid_page_mood_from_poster(poster)
                        if mood and os.path.isfile(mood):payload["grid_mood"]=mood
                    except Exception as exc:optional_failure("ui.folder_cache_adaptive_mood",exc)
                    try:
                        hud_key="poster_hud_"+hashlib.sha1(str(poster).encode("utf-8","ignore")).hexdigest()[:16]
                        _build_poster_adaptive_chrome_clean(poster,hud_key)
                    except Exception as exc:optional_failure("ui.folder_cache_poster_hud",exc)

                if backdrop_ok:
                    payload["backdrop"]=backdrop
                    try:
                        stamp=str(os.path.getmtime(backdrop))
                    except Exception:stamp="0"
                    digest=hashlib.sha1((backdrop+"|"+stamp+"|us198-present").encode("utf-8","ignore")).hexdigest()[:24]
                    target=os.path.join(THUMB_CACHE_DIR,"us220_detail_%s_1620x620.png"%digest)
                    if not _valid_cache_file(target,ttl=0):
                        target=_build_integrated_backdrop(backdrop,target,(1620,620))
                    if target and os.path.isfile(target):payload["prepared_backdrop"]=target

                if payload:
                    _save_visual_bundle(profile,media_type,item,payload,snapshot=data)

                # "Unavailable" now means no real poster AND no real backdrop
                # after trusted direct-ID recovery, not merely one failed request.
                if not poster_ok and not backdrop_ok:failed=1
            except _ArtworkCancelled:
                raise
            except Exception as exc:
                failed=1;optional_failure("ui.folder_artwork_cache_item",exc)
            return downloaded,hot,failed,recovered

        worker_count=3 if total>=3 and not _memory_pressure() else 2
        done=0
        pool=ThreadPoolExecutor(max_workers=worker_count,thread_name_prefix="ultra-folder-cache")
        futures=[]
        try:
            futures=[pool.submit(process_item,item) for item in rows]
            for future in as_completed(futures):
                if cancelled():
                    for f in futures:
                        try:f.cancel()
                        except Exception as exc:diagnostic_failure("ui.grid.failsoft.781",exc)
                    raise _ArtworkCancelled()
                try:
                    downloaded,hot,failed,recovered=future.result()
                except _ArtworkCancelled:
                    raise
                except Exception as exc:
                    downloaded,hot,failed,recovered=0,0,1,0
                    optional_failure("ui.folder_artwork_cache_future",exc)
                with stats_lock:
                    stats["downloaded"]+=downloaded;stats["hot"]+=hot;stats["failed"]+=failed;stats["recovered"]+=recovered
                done+=1
                self._folder_artwork_cache_done=done
                if not self._screen_closed:
                    try:self._folder_artwork_progress.put((done,total))
                    except Exception as exc:optional_failure("ui.folder_artwork_progress_queue",exc)
        finally:
            try:pool.shutdown(wait=True,cancel_futures=True)
            except TypeError:pool.shutdown(wait=True)
        return {"total":total,"done":done,"downloaded":stats["downloaded"],"hot":stats["hot"],"failed":stats["failed"],"recovered":stats["recovered"]}

    def cache_folder_artwork(self):
        if self.media_type not in ("vod","series"):return
        if self._folder_artwork_cache_running:
            self["status"].setText("Artwork cache is already running")
            return
        self["blue"].setText("Preparing...")
        self["status"].setText("Preparing current folder artwork cache...")
        def catalog_ready(rows):
            rows=[dict(x) for x in (rows or []) if isinstance(x,dict)]
            if not rows:
                self["blue"].setText("Cache Artwork");self["status"].setText("Folder is empty");return
            self._folder_artwork_cache_running=True
            self._folder_artwork_cache_done=0;self._folder_artwork_cache_total=len(rows)
            self["blue"].setText("0 / %d"%len(rows))
            self["status"].setText("Caching 0 / %d"%len(rows))
            def ok(result):
                self._folder_artwork_cache_running=False
                self["blue"].setText("Cache Artwork")
                self._refresh_search_sort_controls()
                result=result or {}
                recovered=int(result.get("recovered",0) or 0)
                suffix=(" • %d recovered"%recovered) if recovered else ""
                self["status"].setText(("Artwork ready • %d/%d • %d already cached • %d unavailable%s")%(
                    int(result.get("done",0)),int(result.get("total",0)),int(result.get("hot",0)),int(result.get("failed",0)),suffix))
                # Repaint the current page from the newly completed HDD cache.
                self._page_prefetch_seen.clear()
                self._prefetch_visible_page_details(priority_index=self.index)
            def failed(error):
                self._folder_artwork_cache_running=False
                self["blue"].setText("Cache Artwork")
                self._refresh_search_sort_controls()
                self["status"].setText(_friendly_error(error))
            self._run_async(lambda handle:self._cache_folder_artwork_worker(rows,handle.cancel_event),ok,failed)
        self._ensure_folder_catalog(catalog_ready)

    def open_folder_search(self):
        if self.media_type not in ("vod","series"):return
        title="Search in %s"%self.category_title
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard as E2VirtualKeyBoard
        except Exception:
            E2VirtualKeyBoard=None
        if E2VirtualKeyBoard is not None:
            self.session.openWithCallback(self._folder_search_entered,E2VirtualKeyBoard,title=title,text=self._search_query)
        else:
            self.session.openWithCallback(self._folder_search_entered,InputBox,title=title,text=self._search_query,maxSize=60)

    def _folder_search_entered(self,term=None):
        if term is None:return
        # Empty input intentionally resets Search while preserving the current Sort.
        self._search_query=one_line(term or "",60)
        self._refresh_search_sort_controls()
        def ready(_rows):
            self._load_folder_view_page(1,0)
        self._ensure_folder_catalog(ready)

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
        self._portal_page_cache[page]=items
        self._portal_page_cache.move_to_end(page)
        while len(self._portal_page_cache) > self._portal_page_cache_limit:
            self._portal_page_cache.popitem(last=False)
        if not self._portal_page_size:
            self._portal_page_size=max(1,int(result.get("page_size") or len(items) or self.page_size))
        self._portal_total=max(self._portal_total,int(result.get("total") or 0))
        return items

    def _runtime_grid_close(self):
        if getattr(self,"media_type",None)=="itv":
            _live_restart_trace("grid_close",page=int(getattr(self,"page",0) or 0),index=int(getattr(self,"index",0) or 0),
                                generation=int(getattr(self,"_grid_generation",0) or 0),
                                inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
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
        """Fetch and fully prepare one grid page off the GUI thread."""
        data=self._grid_page_data(target,cancel_event)
        valid=[x for x in data if isinstance(x,dict)] if isinstance(data,list) else []
        if self.media_type in ("vod","series"):
            # Preserve Xtream provider artwork long enough to inspect it. Some
            # panels return one branding/default cover for many unrelated rows.
            valid=[(dict(x) if isinstance(x,dict) and x.get("_xtream") else _strip_portal_artwork(x))
                   for x in valid]
            try:
                art_counts={}
                for row in valid:
                    value=str(_image_url(row) or "").strip()
                    if value:
                        art_counts[value]=art_counts.get(value,0)+1
                generic_urls={url for url,count in art_counts.items() if count>=3}
                if generic_urls:
                    for row in valid:
                        value=str(_image_url(row) or "").strip()
                        if value in generic_urls:
                            row["_generic_provider_art"]=True
                            row["_generic_provider_art_url"]=value
            except Exception as exc:
                optional_failure("ui.generic_provider_art_detect",exc)
        states=load_content_states(self.profile,self.media_type,valid) if valid else []
        qualities=load_content_qualities(self.profile,self.media_type,valid) if valid and self.media_type in ("vod","series") else ["" for _ in valid]
        state_rows=[]
        cfg=self._grid_settings or {}
        prepared_titles=[]
        prepared_fitted=[]
        prepared_meta=[]
        prepared_art=[]
        for pos,item in enumerate(valid):
            state=states[pos] if pos<len(states) else {}
            quality=qualities[pos] if pos<len(qualities) else ""
            row=dict(state or {});row["quality"]=quality or "";state_rows.append(row)
            raw=item.get("name") or item.get("title") or item.get("id") or "Item"
            title=_clean_live_channel_name(raw) if self.media_type=="itv" else _catalogue_title(raw)
            prepared_titles.append(title)
            if self.media_type=="itv":
                prepared_fitted.append((one_line(title,36),None))
                prepared_meta.append(str((target-1)*self.page_size+pos+1))
            else:
                fitted_title,fitted_font=_grid_fit_card_title(title,226)
                prepared_fitted.append((fitted_title,int(fitted_font)))
                bits=[]
                explicit=self._explicit_item_quality(item)
                badges=explicit or str(row.get("quality") or "") or quality_badges(raw)
                if badges:bits.append(badges)
                if item.get("year"):bits.append(str(item.get("year"))[:4])
                if row.get("favorite"):bits.append("★")
                if row.get("completed") and cfg.get("show_watched",True):bits.append("WATCHED")
                elif row.get("position") and row.get("duration"):bits.append("%d%%"%min(99,int(row["position"]*100.0/row["duration"])))
                prepared_meta.append("  •  ".join(bits)[:28])

            art_row=self._page_visual_get(item) if self.media_type in ("vod","series") else {}
            if self.media_type in ("vod","series") and not art_row:
                try:
                    provider_value=None if item.get("_generic_provider_art") else _image_url(item)
                    use_local_first=bool(self._grid_is_m3u_source())
                    snap=(load_shared_detail_snapshot(self.profile,self.media_type,item)
                          if isinstance(item,dict) and item.get("_xtream")
                          else load_detail_snapshot(self.profile,self.media_type,item))
                    # Persistent final visuals win for BOTH Portal and M3U.
                    # Provider/TMDB network is fallback only when this content
                    # has never completed a local visual pass.
                    bundle=_load_visual_bundle(self.profile,self.media_type,item,snap) or {}
                    poster_local=str(bundle.get("poster") or "")
                    grid_thumb=str(bundle.get("grid_thumb") or "")
                    if grid_thumb and _valid_cache_file(grid_thumb,ttl=0):
                        display=grid_thumb
                        art_row["display"]=display
                        art_row["palette"]=poster_local or grid_thumb
                        art_row["visual_locked"]=True
                        card=str(bundle.get("grid_card") or "")
                        if card and os.path.isfile(card):art_row["card"]=card
                    elif poster_local and os.path.isfile(poster_local):
                        # Legacy cached items may have the final poster but no
                        # grid derivative yet. Build it once, persist it, then
                        # all future opens use the exact same file.
                        try:
                            grid_thumb=self._grid_poster_thumb_path(poster_local) or ""
                            if grid_thumb and not _valid_cache_file(grid_thumb,ttl=0):
                                grid_thumb=_build_cover_thumbnail(poster_local,grid_thumb,self._grid_image_size) or ""
                            if grid_thumb and os.path.isfile(grid_thumb):
                                card=str(bundle.get("grid_card") or "")
                                if not (card and os.path.isfile(card)):
                                    try:card=_grid_card_chrome_from_poster(poster_local) or ""
                                    except Exception:card=""
                                _save_visual_bundle(self.profile,self.media_type,item,{"poster":poster_local,"grid_thumb":grid_thumb,"grid_card":card},snapshot=snap)
                                art_row["display"]=grid_thumb;art_row["palette"]=poster_local;art_row["visual_locked"]=True
                                if card and os.path.isfile(card):art_row["card"]=card
                            else:
                                art_row["poster"]=poster_local;art_row["visual_locked"]=True
                        except Exception as exc:optional_failure("ui.prepare_page_thumb_build",exc)
                    elif provider_value and not use_local_first:
                        # First-ever Portal load keeps the historical provider
                        # path. Once persisted above, this branch is never hit again.
                        art_row["provider_locked"]=True
                    else:
                        manifest=load_artwork_v2_manifest(self.profile,self.media_type,item) or {}
                        local=str(manifest.get("poster_local") or "")
                        if local and os.path.isfile(local) and os.path.getsize(local)>100:
                            try:
                                thumb=self._grid_poster_thumb_path(local) or ""
                                if thumb and not _valid_cache_file(thumb,ttl=0):
                                    thumb=_build_cover_thumbnail(local,thumb,self._grid_image_size) or ""
                            except Exception as exc:
                                optional_failure("ui.prepare_manifest_thumb",exc);thumb=""
                            if thumb and os.path.isfile(thumb):
                                art_row["display"]=thumb;art_row["palette"]=local
                                try:_save_visual_bundle(self.profile,self.media_type,item,{"poster":local,"grid_thumb":thumb},snapshot=snap)
                                except Exception as save_exc:optional_failure("ui.prepare_manifest_thumb_save",save_exc)
                            else:
                                art_row["poster"]=local
                            if use_local_first:art_row["visual_locked"]=True
                except Exception as exc:optional_failure("ui.prepare_page_art",exc)
            prepared_art.append(art_row)
        return {"items":valid,"states":state_rows,"titles":prepared_titles,"fitted":prepared_fitted,"meta":prepared_meta,"art":prepared_art}

    def _cache_prepared_page(self,target,payload):
        if not isinstance(payload,dict):return
        with self._prepared_page_lock:
            self._prepared_page_cache[int(target)]=payload
            self._prepared_page_cache.move_to_end(int(target))
            while len(self._prepared_page_cache)>4:self._prepared_page_cache.popitem(last=False)
            self._prepared_page_pending.discard(int(target))

    def _stop_progressive_poster_prefetch(self):
        try:self._progressive_poster_cancel.set()
        except Exception as exc:optional_failure("ui.progressive_poster_stop",exc)
        self._progressive_poster_started=False

    def _progressive_poster_item(self,item,cancel_event):
        """Persist ONE list poster. Provider artwork owns the grid when available."""
        if cancel_event.is_set() or self._screen_closed or not isinstance(item,dict):return
        cfg=self._grid_settings or {};profile=self.profile;media_type=self.media_type

        # HARD HDD LOCK: progressive warming must never redownload/re-resolve a
        # poster that already has a final content-owned file.  This was one of
        # the hidden causes of HDD/network churn on every revisit.
        try:
            snap=(load_shared_detail_snapshot(profile,media_type,item)
                  if item.get("_xtream") else load_detail_snapshot(profile,media_type,item))
            bundle=_load_visual_bundle(profile,media_type,item,snap) or {}
            local=str(bundle.get("grid_thumb") or bundle.get("poster") or "")
            if local and os.path.isfile(local) and os.path.getsize(local)>256:
                return
        except Exception as exc:optional_failure("ui.progressive_hard_hdd_lock",exc)

        provider_value=None if item.get("_generic_provider_art") else _image_url(item)
        if provider_value:
            try:
                poster=_download_portal_artwork(provider_value,profile,self.client,False,2.0,cancel_event,item=item)
                if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                    try:
                        snap=_portal_snapshot(item,media_type,poster_local=poster,backdrop_local=None)
                        snap["_grid_provider_locked"]=True
                        _save_visual_bundle(profile,media_type,item,{"poster":poster,"provider_locked":True},snapshot=snap)
                    except Exception as exc:diagnostic_failure("ui.grid.provider_bundle",exc)
                    return
            except Exception as exc:
                if not cancel_event.is_set():optional_failure("ui.progressive_provider_poster",exc)

            # Rich provider info is the only rescue for a provider-owned card.
            if cancel_event.is_set():return
            if item.get("_xtream") and hasattr(self.client,"enrich_provider_artwork"):
                try:
                    enriched=dict(item)
                    enriched=call_compatible(
                        self.client.enrich_provider_artwork,
                        (((enriched,media_type,cancel_event), {"force":True}),
                         ((enriched,media_type,cancel_event), {})),
                    ) or enriched
                    rich_value=_image_url(enriched)
                    if rich_value:
                        poster=_download_portal_artwork(rich_value,profile,self.client,False,2.4,cancel_event,item=enriched)
                        if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                            try:
                                snap=_portal_snapshot(enriched,media_type,poster_local=poster,backdrop_local=None)
                                snap["_grid_provider_locked"]=True
                                _save_visual_bundle(profile,media_type,item,{"poster":poster,"provider_locked":True},snapshot=snap)
                            except Exception as exc:diagnostic_failure("ui.grid.provider_rich_bundle",exc)
                            return
                except Exception as exc:
                    if not cancel_event.is_set():optional_failure("ui.progressive_provider_rich_rescue",exc)
            return

        # No provider artwork exists: cached/TMDB artwork is allowed.
        try:
            snap=load_artwork_v2_manifest(profile,media_type,item) or {}
            poster=str(snap.get("poster_local") or "")
            if poster and os.path.isfile(poster) and os.path.getsize(poster)>1024:return
            bundle=_load_visual_bundle(profile,media_type,item,snap) or {}
            poster=str(bundle.get("poster") or "")
            if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:return
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.cache",exc)

        if cancel_event.is_set():return
        credential=str(cfg.get("tmdb_credential") or "").strip()
        if cfg.get("load_images",True) and cfg.get("tmdb_enabled",True) and credential:
            try:
                resolver=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),
                                   min(3,max(2,int(cfg.get("timeout",10) or 10))))
                resolver.resolve(profile,media_type,item,full=False,cancel_event=cancel_event)
            except Exception as exc:
                if not cancel_event.is_set():optional_failure("ui.progressive_poster",exc)

    def _start_progressive_poster_prefetch(self):
        """Current page always wins; then warm the following pages."""
        if self.media_type not in ("vod","series") or self._screen_closed:return

        # Preempt any old background walk immediately.
        try:self._progressive_poster_cancel.set()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1088",exc)
        self._progressive_poster_cancel=threading.Event()
        cancel_event=self._progressive_poster_cancel
        self._progressive_poster_started=True
        start_page=max(1,int(getattr(self,"page",1) or 1))

        def worker():
            # Let the visible 4-worker lane run alone for a short burst.
            for _ in range(8):
                if cancel_event.is_set() or self._screen_closed:return
                time.sleep(0.08)
            try:
                total=int(self._portal_total or 0)
                total_pages=max(1,(total+self.page_size-1)//self.page_size) if total else max(start_page+1,2)
                order=list(range(start_page+1,total_pages+1))+list(range(1,start_page))
                for page_no in order:
                    if cancel_event.is_set() or self._screen_closed:return
                    try:rows=self._grid_page_data(page_no,cancel_event=cancel_event) or []
                    except Exception as exc:
                        if not cancel_event.is_set():optional_failure("ui.progressive_page_fetch",exc)
                        continue
                    rows=[dict(x) for x in rows if isinstance(x,dict)]
                    if not rows:continue
                    pool=ThreadPoolExecutor(max_workers=(1 if _memory_pressure() else 4),
                                            thread_name_prefix="ultra-poster-next")
                    futures=[]
                    try:
                        futures=[pool.submit(self._progressive_poster_item,row,cancel_event) for row in rows]
                        for future in as_completed(futures):
                            if cancel_event.is_set() or self._screen_closed:
                                for f in futures:f.cancel()
                                return
                            try:future.result()
                            except Exception as exc:optional_failure("ui.progressive_poster_future",exc)
                    finally:
                        try:pool.shutdown(wait=True,cancel_futures=True)
                        except TypeError:pool.shutdown(wait=True)
            finally:
                if self._progressive_poster_cancel is cancel_event:
                    self._progressive_poster_started=False
        try:_PROGRESSIVE_POSTER_EXECUTOR.submit(worker)
        except Exception:
            self._progressive_poster_started=False

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
        """Persist provider posters for an adjacent M3U page without repainting UI."""
        if not self._grid_is_m3u_source() or self.media_type not in ("vod","series") or not isinstance(payload,dict):
            return
        items=[dict(x) for x in (payload.get("items") or []) if isinstance(x,dict)]
        profile=self.profile;media_type=self.media_type
        for item in items[:self.page_size]:
            if self._screen_closed:return
            try:
                snap=(load_shared_detail_snapshot(profile,media_type,item)
                      if item.get("_xtream") else load_detail_snapshot(profile,media_type,item))
                bundle=_load_visual_bundle(profile,media_type,item,snap) or {}
                local=str(bundle.get("poster") or "")
                if local and os.path.isfile(local) and os.path.getsize(local)>256:
                    continue
                provider_value=None if item.get("_generic_provider_art") else _image_url(item)
                if not provider_value:continue
                poster=_download_portal_artwork(provider_value,profile,self.client,False,1.8,None,item=item)
                if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                    psnap=_portal_snapshot(item,media_type,poster_local=poster,backdrop_local=None)
                    _save_visual_bundle(profile,media_type,item,{"poster":poster,"provider_locked":True},snapshot=psnap)
            except Exception as exc:
                optional_failure("ui.m3u_adjacent_art_warm",exc)

    def _prefetch_adjacent_pages(self):
        if self._screen_closed or self._view_active:return
        total=int(self._portal_total or 0)
        total_pages=max(1,(total+self.page_size-1)//self.page_size) if total else max(1,self.page+1)
        candidates=[]
        if self.page+1<=total_pages:candidates.append(self.page+1)
        if self.page>1:candidates.append(self.page-1)
        for target in candidates:
            with self._prepared_page_lock:
                if target in self._prepared_page_cache or target in self._prepared_page_pending:continue
                self._prepared_page_pending.add(target)
            def worker(page_no=target):
                try:
                    payload=self._prepare_grid_page(page_no,None)
                    self._cache_prepared_page(page_no,payload)
                    if self._grid_is_m3u_source() and self.media_type in ("vod","series"):
                        try:_CATEGORY_PREFETCH_EXECUTOR.submit(self._warm_m3u_prepared_page_art,payload)
                        except Exception as exc:optional_failure("ui.m3u_adjacent_art_submit",exc)
                except Exception as exc:
                    with self._prepared_page_lock:self._prepared_page_pending.discard(page_no)
                    optional_failure("ui.adjacent_page_prefetch",exc)
            try:_CATEGORY_PREFETCH_EXECUTOR.submit(worker)
            except Exception:
                with self._prepared_page_lock:self._prepared_page_pending.discard(target)

    def _apply_prepared_page(self,target,payload,restore_index=None):
        if self._grid_is_m3u_source() and self.media_type in ("vod","series"):
            payload=self._refresh_prepared_m3u_art_from_hdd(payload)
        valid=list((payload or {}).get("items") or [])
        if not valid and target>1:
            self["status"].setText("No more items");return False
        self.page=target;self.grid_items=valid
        state_rows=list((payload or {}).get("states") or [])
        self._grid_item_state={}
        for item,row in zip(self.grid_items,state_rows):self._grid_item_state[id(item)]=dict(row or {})
        self._prepared_titles_for_render=list((payload or {}).get("titles") or [])
        self._prepared_fitted_for_render=list((payload or {}).get("fitted") or [])
        self._prepared_meta_for_render=list((payload or {}).get("meta") or [])
        self._prepared_art_for_render=list((payload or {}).get("art") or [])
        self.index=max(0,min(int(restore_index or 0),len(valid)-1)) if valid else 0
        self._grid_full_chrome_warm=False
        self._render_grid();self._remember_grid_state()
        try:_ui_diag("ui_page_ready",screen="grid",media_type=self.media_type,page=self.page,items=len(self.grid_items),
                     elapsed_ms=int(max(0.0,(time.monotonic()-float(getattr(self,"_ui_diag_page_request_mono",time.monotonic())))*1000.0)))
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1171",exc)
        total=int(self._portal_total or 0)
        total_pages=max(1,(total+self.page_size-1)//self.page_size) if total else max(1,self.page)
        absolute=min(total,(self.page-1)*self.page_size+self.index+1) if total else ((self.page-1)*self.page_size+self.index+1)
        kind="Channel" if self.media_type=="itv" else ("Movie" if self.media_type=="vod" else "Series")
        self["status"].setText("")
        self["page_label"].setText("Page %d / %d  •  %s %d / %d"%(self.page,total_pages,kind,absolute,total or absolute))
        try:self._grid_idle_warm_timer.stop();self._grid_idle_warm_timer.start(850,True)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1179",exc)
        if self.media_type in ("vod","series"):
            try:self._prefetch_visible_page_details(priority_index=self.index)
            except Exception as exc:optional_failure("ui.visible_page_poster_warm",exc)
            try:self._start_visible_poster_watch()
            except Exception as exc:optional_failure("ui.visible_poster_watch_start",exc)
            try:self._start_progressive_poster_prefetch()
            except Exception as exc:optional_failure("ui.progressive_poster_start",exc)
        self._prefetch_adjacent_pages()
        return True

    def load_page(self,page,restore_index=None):
        if self.media_type=="itv":
            _live_restart_trace("page_request",page=int(page or 1),current_page=int(getattr(self,"page",0) or 0),
                                index=int(getattr(self,"index",0) or 0),generation=int(getattr(self,"_grid_generation",0) or 0),
                                inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        if self.media_type in ("vod","series") and self._view_active and isinstance(self._folder_catalog,list):
            self._load_folder_view_page(page,restore_index);return
        target=max(1,int(page))
        if self.media_type=="series":
            self._stop_series_hierarchy_prefetch()
        self._ui_diag_page_request_mono=time.monotonic();self._ui_diag_page_target=target;self._ui_diag_first_poster_logged=False
        with self._prepared_page_lock:
            prepared=self._prepared_page_cache.pop(target,None)
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
        self["status"].setText("Loading page %d..."%target)
        # Cancel the previous page as soon as navigation starts, not only after
        # the next portal response arrives. Downloads already reading data abort
        # at the next chunk boundary and no new external enrichment is started.
        try:
            old_cancel=getattr(self,"_page_prefetch_cancel",None)
            if old_cancel is not None:old_cancel.set()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
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
                self._page_prefetch_seen.clear(); self._page_quality_seen.clear(); self._page_art_retry_count.clear()
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
                self["status"].setText(_friendly_error(e))
                _runtime_endurance_log("grid_page_error",media_type=self.media_type,page=target)
        self._page_load_handle=self._run_async(lambda handle:self._prepare_grid_page(target,handle.cancel_event),ok,failed)

    def _render_grid(self):
        if self.media_type=="itv":
            _live_restart_trace("render_begin",page=int(getattr(self,"page",0) or 0),index=int(getattr(self,"index",0) or 0),
                                generation=int(getattr(self,"_grid_generation",0) or 0),
                                items=len(getattr(self,"grid_items",[]) or []),inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        self._grid_cancel_queued_downloads()
        self._grid_generation+=1;self._grid_decode_queue=[];self._grid_waiting={};self._grid_queued=set();self._grid_slot_paths={};self._grid_palette_sources={};self._grid_card_paths={};self._grid_display_titles={};self._grid_visual_locks={}
        cfg=self._grid_settings or {}
        self._grid_prepared_art={pos:row for pos,row in enumerate(getattr(self,"_prepared_art_for_render",[]) or [])}
        for pos in range(self.page_size):
            title_widget=self["item_title%d"%pos];meta_widget=self["item_meta%d"%pos];art=self["art%d"%pos]
            if pos>=len(self.grid_items):
                title_widget.setText("");meta_widget.setText("")
                widgets=[art,title_widget,meta_widget]
                try:widgets.append(self["card_chrome%d"%pos])
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                for widget in widgets:
                    try:widget.hide()
                    except Exception as exc:optional_failure("ui",exc)
                continue
            item=self.grid_items[pos];raw=item.get("name") or item.get("title") or item.get("id") or "Item"
            prepared_titles=getattr(self,"_prepared_titles_for_render",[]) or []
            title=prepared_titles[pos] if pos<len(prepared_titles) else (_clean_live_channel_name(raw) if self.media_type=="itv" else _catalogue_title(raw))
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
            meta_widget.setText(prepared_meta[pos] if pos<len(prepared_meta) else self._card_meta(item,pos))
            if self.media_type in ("vod","series"):
                try:
                    chrome=self["card_chrome%d"%pos]
                    if chrome.instance is not None:chrome.instance.setPixmapFromFile(asset("poster_card_full_neutral.png"))
                    chrome.show()
                except Exception as exc:optional_failure("ui.grid_neutral_card",exc)
            for widget in (art,title_widget,meta_widget):
                try:widget.show()
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
        self._update_selection()
        if self.media_type=="itv":
            _live_restart_trace("render_end",page=int(getattr(self,"page",0) or 0),index=int(getattr(self,"index",0) or 0),
                                generation=int(getattr(self,"_grid_generation",0) or 0),
                                slots=len(getattr(self,"_grid_slot_paths",{}) or {}),inflight=len(getattr(self,"_grid_download_inflight",{}) or {}))
        self._prepared_titles_for_render=[];self._prepared_fitted_for_render=[];self._prepared_meta_for_render=[];self._prepared_art_for_render=[]

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
        except Exception as exc:optional_failure("ui",exc)
        self._remember_grid_state()
        self._update_header(self.grid_items[self.index])
        if self.media_type in ("vod","series"):
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
                self._nav_burst_until=time.monotonic()+0.30
                self._grid_focus_timer.stop();self._grid_focus_timer.start(300,True)
                # Network hierarchy prefetch only after the focus is genuinely stable.
                # Every navigation key resets this timer, so fast browsing creates zero
                # get_series_info traffic.
                if self.media_type=="series":
                    self._series_prefetch_timer.stop();self._series_prefetch_timer.start(900,True)
            except Exception:
                self._apply_debounced_grid_focus()
            try:
                # Browsing is RAM/HDD-only. Network/TMDB enrichment belongs to
                # explicit Cache Artwork or the Details screen, never arrow pauses.
                self._detail_prefetch_timer.stop()
                self._grid_idle_warm_timer.stop()
            except Exception as exc:optional_failure("ui",exc)

    def _stop_series_hierarchy_prefetch(self):
        try:self._series_prefetch_timer.stop()
        except Exception as exc:optional_failure("ui.series_prefetch_timer_stop",exc)
        try:self._series_hierarchy_prefetch_cancel.set()
        except Exception as exc:optional_failure("ui.series_prefetch_cancel",exc)
        try:self._series_hierarchy_prefetch_token+=1
        except Exception:self._series_hierarchy_prefetch_token=1

    def _prefetch_selected_series_hierarchy(self):
        if self._screen_closed or self.media_type!="series" or not self.grid_items:
            return
        if not hasattr(self.client,"prefetch_series_hierarchy"):
            return
        try:item=dict(self.grid_items[self.index] or {})
        except Exception:return
        # Cancel any previous focus request and make this the only relevant one.
        # urllib may finish an in-flight read before noticing cancellation, but it
        # now runs on a dedicated lane and can no longer block page/Portal prefetch.
        try:self._series_hierarchy_prefetch_cancel.set()
        except Exception as exc:optional_failure("ui.series_prefetch_cancel_previous",exc)
        cancel_event=threading.Event();self._series_hierarchy_prefetch_cancel=cancel_event
        try:self._series_hierarchy_prefetch_token+=1
        except Exception:self._series_hierarchy_prefetch_token=1
        token=int(self._series_hierarchy_prefetch_token or 0)
        generation=int(getattr(self,"_grid_generation",0) or 0)
        def worker():
            if cancel_event.is_set() or self._screen_closed:
                return
            if token!=int(getattr(self,"_series_hierarchy_prefetch_token",0) or 0):
                return
            if generation!=int(getattr(self,"_grid_generation",0) or 0):
                return
            try:self.client.prefetch_series_hierarchy(item,cancel_event=cancel_event)
            except Exception as exc:
                if not cancel_event.is_set():optional_failure("ui.m3u_series_focus_prefetch",exc)
        try:_SERIES_HIERARCHY_PREFETCH_EXECUTOR.submit(worker)
        except Exception as exc:optional_failure("ui.m3u_series_focus_submit",exc)

    def _apply_debounced_grid_focus(self):
        if self._screen_closed or not self.grid_items or self.media_type not in ("vod","series"):
            return
        # Card chrome never changes on focus. The cursor moved synchronously in
        # _update_selection(); this deferred pass may only swap the independent
        # laser/mood asset once its cache is ready.
        self._grid_pending_prev_focus=-1
        self._grid_apply_current_selector()
        try:self._apply_poster_live_hud(self.grid_items[self.index])
        except Exception as exc:optional_failure("ui.poster_hud_debounced",exc)

    def _warm_idle_grid_page(self):
        if self._screen_closed or not self.grid_items or self.media_type not in ("vod","series"):
            return
        self._grid_full_chrome_warm=False
        # Full-page Pillow/TMDB warming made BACK/navigation progressively heavy.
        # Keep only the selected card and its closest neighbours hot.
        self._grid_apply_current_selector()

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
        if self.media_type not in ("vod","series") or self._screen_closed:return
        try:
            self._visible_poster_watch_timer.stop()
            self._visible_poster_watch_timer.start(120,False)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1522",exc)

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
                    st=os.stat(poster)
                    raw="%s|%s|%s|%sx%s|visible-watch-v1"%(poster,int(st.st_mtime),int(st.st_size),
                        int(self._grid_image_size[0]),int(self._grid_image_size[1]))
                    dg=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
                    thumb=os.path.join(THUMB_CACHE_DIR,"gridposter_%s_%dx%d.png"%(
                        dg,int(self._grid_image_size[0]),int(self._grid_image_size[1])))
                    if _valid_cache_file(thumb,ttl=0):
                        display=thumb
                    else:
                        built=(_build_cover_thumbnail(poster,thumb,self._grid_image_size)
                               if getattr(self,"_poster_cover_mode",False)
                               else _build_thumbnail(poster,thumb,self._grid_image_size))
                        if built and os.path.isfile(built):display=built
                except Exception:
                    display=poster

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
                self._grid_queue_decode(generation,pos,display)
                self._remember_grid_visual(pos,display=display,poster=poster,palette=poster)
        except Exception as exc:
            optional_failure("ui.visible_poster_watch",exc)

    def _prefetch_visible_page_details(self, priority_index=None, max_items=None):
        """Warm visible posters first; quality discovery is deliberately separate.

        us118 poster scheduler rules:
          * poster-only page prefetch; real backdrops are details-screen only
          * selected card is submitted first
          * poster work never waits for portal/episode quality probes
          * HDD hits are pushed to the UI immediately
          * transient poster misses get one bounded retry
        """
        if self.media_type not in ("vod", "series") or self._screen_closed:
            return
        cfg=self._grid_settings or {}; profile=self.profile; media_type=self.media_type
        artwork_enabled=bool(cfg.get("load_images",True))
        generation=int(getattr(self,"_page_prefetch_generation",0) or 0);cancel_event=getattr(self,"_page_prefetch_cancel",None)
        if cancel_event is None or cancel_event.is_set():return
        credential=str(cfg.get("tmdb_credential") or "").strip();tmdb_enabled=bool(cfg.get("tmdb_enabled",True) and credential)
        items=[dict(x) for x in self.grid_items[:self.page_size] if isinstance(x,dict)]
        # Native decoder/Pillow allocations can briefly spike while a poster-heavy
        # page is warming. On low-memory receiver states, keep the selected card
        # and nearest neighbours responsive instead of allowing background prefetch
        # to push Enigma2 into an OOM/native restart with no Python crashlog.
        if _memory_pressure():
            try:
                _runtime_endurance_log("prefetch_memory_guard", media_type=self.media_type, available_mb=_available_memory_mb())
            except Exception as exc:
                optional_failure("ui.silent_guard",exc)
            items=items[:4]
        if priority_index is not None and 0<=int(priority_index)<len(items):
            # Submit in visual distance order instead of merely moving one card
            # to the front and leaving the rest in stale page order. With four
            # workers this makes the selected card and its neighbours appear
            # first while the remaining page keeps warming behind them.
            pi=int(priority_index); prow,pcol=divmod(pi,max(1,int(self.columns or 1)))
            ranked=list(enumerate(items))
            ranked.sort(key=lambda pair:(abs(divmod(pair[0],max(1,int(self.columns or 1)))[0]-prow)+abs(divmod(pair[0],max(1,int(self.columns or 1)))[1]-pcol),pair[0]))
            items=[pair[1] for pair in ranked]
        if max_items is not None:
            try:items=items[:max(1,int(max_items))]
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.1635",exc)

        def key_for(item):
            try:
                from .persistent_cache import content_cache_key
                return content_cache_key(profile,media_type,item)
            except Exception:
                return hashlib.sha1(repr(sorted(item.items())).encode("utf-8","ignore")).hexdigest()

        # TWO-STAGE VISIBLE POSTER PIPELINE.
        # Cards with a real provider URL are provider-owned. TMDB/HDD/bundle
        # enrichment may supply metadata elsewhere, but can never repaint them.
        provider_owned=set()
        for _item in items:
            try:
                if isinstance(_item,dict) and not _item.get("_generic_provider_art") and _image_url(_item):
                    provider_owned.add(key_for(_item))
            except Exception as exc:optional_failure("ui.provider_lock_seed",exc)

        def emit_poster(item,cache_key,path,meta=None,snapshot=None):
            if not path or not os.path.isfile(path):return False
            payload=dict(meta or {})
            source=str(payload.get("identity_source") or "")
            is_provider=source.startswith("provider")
            if cache_key in provider_owned and not is_provider:
                return False
            try:
                st=os.stat(path)
                raw="%s|%s|%s|%sx%s|visible-fast-v1"%(path,int(st.st_mtime),int(st.st_size),
                    int(self._grid_image_size[0]),int(self._grid_image_size[1]))
                dg=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
                thumb=os.path.join(THUMB_CACHE_DIR,"gridposter_%s_%dx%d.png"%(
                    dg,int(self._grid_image_size[0]),int(self._grid_image_size[1])))
                if not _valid_cache_file(thumb,ttl=0):
                    thumb=(_build_cover_thumbnail(path,thumb,self._grid_image_size)
                           if getattr(self,"_poster_cover_mode",False)
                           else _build_thumbnail(path,thumb,self._grid_image_size))
            except Exception:
                thumb=None
            try:
                snap=snapshot if isinstance(snapshot,dict) else _portal_snapshot(item,media_type,poster_local=path,backdrop_local=None)
                _save_visual_bundle(profile,media_type,item,{"poster":path,"grid_thumb":thumb or path},snapshot=snap)
            except Exception as exc:
                optional_failure("ui.visible_emit_bundle",exc)
            payload["palette_source"]=path
            if is_provider:
                payload["provider_locked"]=True
            self._art_prefetch_jobs.put((generation,cache_key,thumb or path,payload))
            return True

        def rescue_worker(item,cache_key,generation=generation,cancel_event=cancel_event):
            def cancelled():
                return bool(cancel_event.is_set() or self._screen_closed or
                            generation!=int(getattr(self,"_page_prefetch_generation",0) or 0))
            if cancelled():return

            # Rich provider info ONLY for unresolved Xtream rows.
            enriched=dict(item)
            if isinstance(item,dict) and item.get("_xtream") and hasattr(self.client,"enrich_provider_artwork"):
                try:
                    enriched=call_compatible(
                        self.client.enrich_provider_artwork,
                        (((enriched,media_type,cancel_event), {"force":True}),
                         ((enriched,media_type,cancel_event), {})),
                    ) or enriched
                    if cancelled():return
                    rich_value=_image_url(enriched)
                    if rich_value:
                        provider=_download_portal_artwork(rich_value,profile,self.client,False,1.8,cancel_event,item=enriched)
                        if provider and os.path.isfile(provider) and os.path.getsize(provider)>256:
                            if emit_poster(item,cache_key,provider,{"identity_source":"provider-rich"},
                                           _portal_snapshot(enriched,media_type,poster_local=provider,backdrop_local=None)):
                                return
                except Exception as exc:
                    if not cancelled():optional_failure("ui.visible_provider_rich_rescue",exc)

            if cancelled():return
            if cache_key in provider_owned:
                # A provider-owned card may retry its provider/rich-provider
                # cover, but TMDB is never allowed to replace its grid poster.
                self._art_prefetch_jobs.put((generation,cache_key,"__RETRY__",{"provider_locked":True}))
                return
            if not artwork_enabled or not tmdb_enabled:return

            # TMDB POSTER-ONLY rescue applies only when the provider has no art.
            try:
                timeout=min(4,max(2,int(cfg.get("timeout",10) or 10)))
                resolver=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),timeout)
                data=resolver.resolve(profile,media_type,enriched,full=False,cancel_event=cancel_event) or {}
                poster=str(data.get("poster_local") or "") if isinstance(data,dict) else ""
                if poster and os.path.isfile(poster) and os.path.getsize(poster)>1024:
                    emit_poster(item,cache_key,poster,{
                        "tmdb_id":data.get("tmdb_id"),
                        "media_type":data.get("media_type"),
                        "identity_source":data.get("identity_source"),
                    },data)
                    return
            except Exception as exc:
                if not cancelled():optional_failure("ui.visible_tmdb_poster_rescue",exc)

            if not cancelled():
                # One bounded retry remains available through the existing drain path.
                self._art_prefetch_jobs.put((generation,cache_key,"__RETRY__",None))

        for item in items:
            if cancel_event.is_set():break
            cache_key=key_for(item)
            ram_visual=self._page_visual_get(item)
            if ram_visual and (ram_visual.get("display") or ram_visual.get("poster")):
                with self._page_prefetch_lock:
                    self._page_prefetch_seen.add(cache_key)
                continue
            with self._page_prefetch_lock:
                if cache_key in self._page_prefetch_seen:continue
                self._page_prefetch_seen.add(cache_key)

            def fast_worker(item=item,cache_key=cache_key,generation=generation,cancel_event=cancel_event):
                def cancelled():
                    return bool(cancel_event.is_set() or self._screen_closed or
                                generation!=int(getattr(self,"_page_prefetch_generation",0) or 0))
                if cancelled():return

                # 1) HDD/content-owned artwork always wins first paint.
                # Provider URLs are network fallback, never the first thing tried
                # when we already own a valid local poster for this content.
                try:
                    cached=load_artwork_v2_manifest(profile,media_type,item) or {}
                    bundle=_load_visual_bundle(profile,media_type,item,cached) or {}
                    poster=str(bundle.get("poster") or "")
                    provider_value=None if item.get("_generic_provider_art") else _image_url(item)
                    if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                        source="provider-cache" if provider_value else (cached.get("identity_source") or "hdd")
                        if emit_poster(item,cache_key,poster,{"identity_source":source},cached):
                            return
                    poster=str(cached.get("poster_local") or "")
                    if poster and os.path.isfile(poster) and os.path.getsize(poster)>256:
                        source="provider-cache" if provider_value else (cached.get("identity_source") or "hdd")
                        if emit_poster(item,cache_key,poster,{
                            "tmdb_id":cached.get("tmdb_id"),
                            "media_type":cached.get("media_type"),
                            "identity_source":source,
                        },cached):
                            return
                except Exception as exc:
                    optional_failure("ui.visible_fast_hdd",exc)

                if cancelled():return

                # 2) Network provider is only used when the local content cache missed.
                provider_value=None if item.get("_generic_provider_art") else _image_url(item)
                if provider_value:
                    try:
                        provider=_download_portal_artwork(provider_value,profile,self.client,False,1.15,cancel_event,item=item)
                        if provider and os.path.isfile(provider) and os.path.getsize(provider)>256:
                            snap=_portal_snapshot(item,media_type,poster_local=provider,backdrop_local=None)
                            snap["_grid_provider_locked"]=True
                            if emit_poster(item,cache_key,provider,{"identity_source":"provider-fast"},snap):
                                return
                    except Exception as exc:
                        if not cancelled():optional_failure("ui.visible_fast_provider",exc)
                    if cancelled():return
                    try:
                        future=_POSTER_RESCUE_EXECUTOR.submit(rescue_worker,item,cache_key)
                        self._page_prefetch_futures.append(future)
                    except Exception as exc:optional_failure("ui.visible_provider_rescue_submit",exc)
                    return

                # 3) Only provider-less unresolved cards enter TMDB rescue.
                try:
                    future=_POSTER_RESCUE_EXECUTOR.submit(rescue_worker,item,cache_key)
                    self._page_prefetch_futures.append(future)
                except Exception as exc:
                    optional_failure("ui.visible_rescue_submit",exc)

            try:
                self._page_prefetch_futures=[f for f in self._page_prefetch_futures if not f.done()]
                future=_FAST_POSTER_EXECUTOR.submit(fast_worker)
                self._page_prefetch_futures.append(future)
            except Exception as exc:
                optional_failure("ui.visible_fast_submit",exc)

        # us198: grid navigation is poster-only.  Do not probe seasons,
        # episodes or create_link while the user is merely moving around a page.
        # Those portal calls were the main source of the sluggish page changes and
        # delayed BACK from details.  Quality is resolved lazily only when needed.
        return

    def _update_header(self,item):
        raw=item.get("name") or item.get("title") or "Item"
        if self.media_type=="itv":
            title=_clean_live_channel_name(raw)
        else:
            title=(getattr(self,"_grid_display_titles",{}) or {}).get(self.index)
            if title is None:title=_catalogue_title(raw)
        self["title"].setText(_clean_display_text(title,44))
        if self.media_type=="itv":
            cid=str(item.get("id") or item.get("ch_id") or "");epg=self._epg_cache.get(cid,{})
            now=epg.get("now") or item.get("epg_title") or item.get("now") or "EPG information will appear here"
            nxt=epg.get("next") or ""
            self["rating"].setText("NOW  %s"%_clean_display_text(now,90))
            self["meta1"].setText(("NEXT  "+_clean_display_text(nxt,90)) if nxt else "")
            badges=[]
            if item.get("allow_archive") or item.get("tv_archive_duration") or item.get("archive"):badges.append("Catch-up")
            state=self._grid_item_state.get(id(item),{})
            if state.get("favorite"):badges.append("Favorite")
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
            def fit(bg,label,value,cx,minw,maxw,y,h,fs,pad):
                value=str(value or "")
                # Better Arabic/Latin width estimate than raw character count.
                units=sum(1.45 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in value)
                w=max(minw,min(maxw,int(max(1.0,units)*fs*0.57+pad)));x=int(cx-w/2)
                self[bg].instance.move(ePoint(int(x*sx),int(y*sy)));self[bg].instance.resize(eSize(int(w*sx),int(h*sy)))
                self[label].instance.move(ePoint(int((x+12)*sx),int((y+6)*sy)));self[label].instance.resize(eSize(int((w-24)*sx),int((h-12)*sy)))
            fit("poster_folder_bg","section",self["section"].getText(),670,250,650,38,54,18,58)
            fit("poster_title_bg","title",self["title"].getText(),1215,250,590,38,54,22,64)
            fit("poster_counter_bg","page_label",self["page_label"].getText(),1650,260,430,936,46,18,52)
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

    def _nav_allowed(self):
        now=time.monotonic()
        # Give the remote-control path exclusive GUI priority during a key burst.
        self._nav_burst_until=now+0.30
        if now-self._last_nav_at < 0.035:
            return False
        self._last_nav_at=now
        return True

    def _update_page_counter(self):
        total = int(self._active_total() or 0)
        total_pages = max(1, (total + self.page_size - 1) // self.page_size) if total else 1
        absolute = min(total, (self.page - 1) * self.page_size + self.index + 1) if total else (0 if not self.grid_items else ((self.page - 1) * self.page_size + self.index + 1))
        kind = "Channel" if self.media_type == "itv" else ("Movie" if self.media_type == "vod" else "Series")
        try:self["page_label"].setText("Page %d / %d  •  %s %d / %d" % (self.page, total_pages, kind, absolute, total or absolute))
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def move_left(self):
        if self.grid_items and self._nav_allowed():self.index=max(0,self.index-1);self._update_selection();self._update_page_counter()
    def move_right(self):
        if self.grid_items and self._nav_allowed():self.index=min(len(self.grid_items)-1,self.index+1);self._update_selection();self._update_page_counter()
    def move_up(self):
        if not self.grid_items or not self._nav_allowed():
            return
        if self.index >= self.columns:
            self.index -= self.columns
            self._update_selection();self._update_page_counter()
            return
        column=self.index % self.columns
        if self.page > 1:
            # Keep the same column on the previous page's final row.
            self.load_page(self.page - 1, max(0, self.page_size - self.columns + column))
            return
        # Universal vertical wrap: first row of page 1 -> final item/page.
        total=int(self._active_total() or 0)
        if total > 0:
            last_page=max(1,(total+self.page_size-1)//self.page_size)
            self.load_page(last_page,self.page_size-1)
        else:
            self.index=len(self.grid_items)-1;self._update_selection();self._update_page_counter()

    def move_down(self):
        if not self.grid_items or not self._nav_allowed():
            return
        target = self.index + self.columns
        if target < len(self.grid_items):
            self.index = target
            self._update_selection();self._update_page_counter()
            return
        total=int(self._active_total() or 0)
        total_pages=max(1,(total+self.page_size-1)//self.page_size) if total else 0
        if total_pages and self.page >= total_pages:
            # Universal vertical wrap: final row/item -> very first item.
            self.load_page(1,0)
            return
        # Crossing the final visible row naturally advances to the next page.
        column = self.index % self.columns
        self.load_page(self.page + 1, column)

    def next_page(self):
        self.load_page(self.page + 1, self.index % self.columns if self.grid_items else 0)

    def previous_page(self):
        if self.page <= 1:
            return
        column = self.index % self.columns if self.grid_items else 0
        self.load_page(self.page - 1, max(0, self.page_size - self.columns + column))

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
                palette_source=(getattr(self,"_grid_palette_sources",{}).get(self.index) or self._grid_slot_paths.get(self.index) or "")
                if isinstance(detail_item,dict) and palette_source and os.path.isfile(str(palette_source)):
                    detail_item["_ultra_palette_source"]=str(palette_source)
                    detail_item["_ultra_poster_source"]=str(palette_source)
            except Exception as exc:
                optional_failure("ui.grid_palette_handoff",exc)
            self.session.openWithCallback(self._details_returned,ContentDetailsScreen,self.profile,self.client,self.media_type,detail_item)

    def _pause_grid_background_for_child(self):
        for name in ("_grid_focus_timer","_detail_prefetch_timer","_grid_idle_warm_timer"):
            timer=getattr(self,name,None)
            if timer is not None:
                try:timer.stop()
                except Exception as exc:diagnostic_failure("ui.grid.failsoft.1974",exc)
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1978",exc)
        for future in list(getattr(self,"_page_prefetch_futures",[]) or []):
            try:future.cancel()
            except Exception as exc:diagnostic_failure("ui.grid.failsoft.1981",exc)
        self._page_prefetch_futures=[]

    def _resume_grid_background_after_child(self):
        self._page_prefetch_generation=int(getattr(self,"_page_prefetch_generation",0) or 0)+1
        self._page_prefetch_cancel=threading.Event()
        self._page_prefetch_seen=set()
        # No immediate page-wide warm on BACK. Selected-card enrichment is enough;
        # neighbours resolve lazily as the user moves.
        try:self._detail_prefetch_timer.start(700,True)
        except Exception as exc:diagnostic_failure("ui.grid.failsoft.1991",exc)

    def _details_returned(self,result=None):
        # Return to the exact poster and page that opened the details screen.
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
            # Refresh text/state in place. Re-rendering the whole grid used to
            # replace every loaded poster with the Play placeholder on BACK.
            for pos,item in enumerate(self.grid_items[:self.page_size]):
                try:self["item_meta%d"%pos].setText(self._card_meta(item,pos))
                except Exception as exc:optional_failure("ui.grid_return_meta",exc)
                # Rebind the exact final visual persisted for this content.
                # Never "upgrade" a card on BACK: that caused the poster to
                # shrink/grow while async artwork and thumbnail jobs raced.
                try:
                    is_m3u_locked=bool(self._grid_is_m3u_source() and pos in getattr(self,"_grid_visual_locks",{}))
                    snap=(load_shared_detail_snapshot(self.profile,self.media_type,item)
                          if isinstance(item,dict) and item.get("_xtream")
                          else load_detail_snapshot(self.profile,self.media_type,item))
                    bundle=_load_visual_bundle(self.profile,self.media_type,item,snap) or {}
                    display=str(bundle.get("grid_thumb") or bundle.get("poster") or "")
                    poster=str(bundle.get("poster") or display or "")
                    if display and os.path.isfile(display):
                        self._grid_palette_sources[pos]=poster
                        self._grid_visual_locks[pos]=display
                        self._grid_queue_decode(self._grid_generation,pos,display)
                        card=str(bundle.get("grid_card") or "")
                        # Returning from playback/details must never leave a poster with
                        # its footer/chrome missing. Prefer the persisted card; if an old
                        # cache bundle predates grid_card, rebuild only this local chrome.
                        if not (card and os.path.isfile(card)):
                            try:card=str(_grid_card_chrome_from_poster(poster,selected=(pos==self.index)) or "")
                            except Exception:card=""
                        if card and os.path.isfile(card):
                            self._grid_card_paths[pos]=card
                            try:
                                self["card_chrome%d"%pos].instance.setPixmapFromFile(card);self["card_chrome%d"%pos].show()
                            except Exception as card_exc:optional_failure("ui.grid_return_card",card_exc)
                        self._remember_grid_visual(pos,display=display,poster=poster,palette=poster,card=card)
                except Exception as exc:optional_failure("ui.grid_return_frozen_visual",exc)
            # Enigma2 can leave the previously focused pixmap surface stale
            # after the child screen closes. Rebind/show the selected slot now,
            # instead of waiting for a RIGHT/LEFT key to trigger a repaint.
            try:
                slot=self.index
                for name in ("art%d"%slot,"item_title%d"%slot,"item_meta%d"%slot,"card_chrome%d"%slot):
                    try:self[name].show()
                    except Exception as exc:diagnostic_failure("ui.grid.failsoft.2031",exc)
                path=(getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot) or "")
                if path and os.path.isfile(path):
                    thumb=self._grid_poster_thumb_path(path)
                    art_path=thumb if thumb and _valid_cache_file(thumb,ttl=0) else path
                    art=self["art%d"%slot]
                    if art.instance is not None:art.instance.setPixmapFromFile(art_path)
                    art.show()
                    focus_card=_grid_card_chrome_from_poster(path,selected=True)
                    if focus_card:
                        chrome=self["card_chrome%d"%slot]
                        if chrome.instance is not None:chrome.instance.setPixmapFromFile(focus_card)
                        chrome.show()
                    self._grid_focus_chrome_slot=slot
            except Exception as exc:optional_failure("ui.grid_return_repaint",exc)
            self._update_selection()
            try:self._grid_apply_current_selector()
            except Exception as exc:optional_failure("ui.grid_return_selector",exc)
            self._resume_grid_background_after_child()
        else:
            self._update_selection()
            self._resume_grid_background_after_child()

    def _play_live(self,item):
        command=item.get("cmd") or item.get("command") or item.get("url")
        if not command:self["status"].setText("No stream command");return
        self["status"].setText("Creating stream link...")
        def ok(url):
            if not isinstance(url,str) or not url.strip():self["status"].setText("Portal returned an invalid stream link");return
            name=str(item.get("name") or item.get("title") or "Live channel");add_recently_played(self.profile,"itv",item)
            engine=_configured_playback_engine(self._grid_settings or {})
            selected_page=self.page; selected_index=self.index
            def returned(result=None):
                self["status"].setText("Player closed")
                # Keep the channel selected and make sure no player service is
                # resurrected behind the grid.
                if self.page != selected_page or not self.grid_items:
                    self.load_page(selected_page,selected_index)
                else:
                    self.index=max(0,min(selected_index,len(self.grid_items)-1))
                    self._update_selection()
            self.session.openWithCallback(returned,UltraStalkerPlayer,url.strip(),name,"itv",engine,_player_payload(item,self.profile,media_type="itv"))
        self._run_async(lambda handle:self.client.create_link(item,"itv",cancel_event=handle.cancel_event),ok,lambda e:self["status"].setText(_friendly_error(e)))

    def toggle_selected_favorite(self):
        if not self.grid_items:return
        item=self.grid_items[self.index];state=toggle_favorite(self.profile,self.media_type,item);self["status"].setText("Added to favorites" if state else "Removed from favorites")
        cached=self._grid_item_state.setdefault(id(item),{'favorite':False,'position':0,'duration':0,'completed':0});cached['favorite']=bool(state)
        try:
            self["item_meta%d" % self.index].setText(self._card_meta(self.grid_items[self.index], self.index))
        except Exception as exc:
            optional_failure("ui", exc)
        self._update_header(self.grid_items[self.index])

    def show_information(self):
        if not self.grid_items:return
        item=self.grid_items[self.index]
        if self.media_type in ("vod","series"):self.session.open(ContentDetailsScreen,self.profile,self.client,self.media_type,item)
        else:
            cid=str(item.get("id") or item.get("ch_id") or "");epg=self._epg_cache.get(cid,{})
            text="%s\n\nNOW: %s\nNEXT: %s"%(self["title"].getText(),epg.get("now") or "No EPG",epg.get("next") or "No EPG")
            self.session.open(MessageBox,text,MessageBox.TYPE_INFO,timeout=12)

    def open_menu(self):
        choices=[("Refresh page","refresh"),("First page","first"),("Toggle favorite","favorite")]
        if self.media_type=="vod" and self.grid_items:
            state=self._grid_item_state.get(id(self.grid_items[self.index]),{})
            choices.append(("Mark unwatched" if state.get("completed") else "Mark watched","unwatch" if state.get("completed") else "watch"))
        self.session.openWithCallback(self._menu_selected,ChoiceBox,title="Grid options",list=choices)
    def _menu_selected(self,choice):
        if not choice:return
        if choice[1]=="refresh":
            for item in self.grid_items:
                url = self._grid_absolute_url(_image_url(item))
                if url:
                    _clear_artwork_failure(url)
            self.load_page(self.page)
        elif choice[1]=="first":self.load_page(1)
        elif choice[1]=="favorite":self.toggle_selected_favorite()
        elif choice[1] in ("watch","unwatch") and self.grid_items:
            item=self.grid_items[self.index];watched=choice[1]=="watch";mark_watched(self.profile,self.media_type,item,watched)
            cached=self._grid_item_state.setdefault(id(item),{'favorite':False,'position':0,'duration':0,'completed':0});cached['completed']=1 if watched else 0
            if not watched:cached['position']=0
            try:self["item_meta%d"%self.index].setText(self._card_meta(item,self.index))
            except Exception as exc:optional_failure("ui",exc)
            self._update_header(item);self["status"].setText("Marked watched" if watched else "Marked unwatched")

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

