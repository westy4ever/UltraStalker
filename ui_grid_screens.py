"""Poster and Live grid screens extracted from ui.py without changing class behavior."""

from .ui_grid_base import PremiumGridBase

POSTER_GRID_SKIN = ""
LIVE_GRID_SKIN = ""

def configure_grid_screens(**deps):
    globals().update(deps)
    if "POSTER_GRID_SKIN" in deps:
        PremiumPosterGridScreen.skin = deps["POSTER_GRID_SKIN"]
    if "LIVE_GRID_SKIN" in deps:
        PremiumLiveGridScreen.skin = deps["LIVE_GRID_SKIN"]

class PremiumPosterGridScreen(PremiumGridBase):
    skin=POSTER_GRID_SKIN;columns=6;page_size=12;image_size=(250,310);placeholder="grid_placeholder_movie_921.png";selection_asset="poster_card_selected_neutral.png"
    card_positions=[(x,y) for y in (137,537) for x in (47,347,647,947,1247,1547)]
    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        self.placeholder="grid_placeholder_series_921.png" if media_type=="series" else "grid_placeholder_movie_921.png"
        self.selection_asset="poster_card_selected_neutral.png"
        self._poster_cover_mode=True
        PremiumGridBase.__init__(self,session,profile,client,media_type,genre,category_title)


class PremiumLiveGridScreen(PremiumGridBase):
    skin=LIVE_GRID_SKIN;columns=1;page_size=12;image_size=(70,40);placeholder="grid_placeholder_live_921.png";selection_asset="us205_live_selection_neutral.png"
    card_positions=[(58,170+i*68) for i in range(12)]
    PREVIEW_RECT=(870,145,900,506)

    def __init__(self,session,profile,client,media_type,genre,category_title=""):
        # Live picons persist on HDD and are reused before any network request.
        self._preview_old_service=None
        try:self._preview_old_service=session.nav.getCurrentlyPlayingServiceReference()
        except Exception as exc:optional_failure("ui",exc)
        self._active_preview_key=None;self._active_preview_url=None;self._active_preview_item=None;self._active_preview_engine=None
        self._preview_token=0;self._preview_jobs=queue.Queue();self._preview_handle=None;self._preview_task_id=None;self._pending_preview_key=None
        self._preview_pending_url=None;self._preview_pending_item=None;self._preview_pending_engine=None;self._preview_engine_candidates=[];self._preview_engine_pos=0
        self._preview_poll_count=0;self._preview_restore_state=None;self._main_preview_reference=None
        PremiumGridBase.__init__(self,session,profile,client,media_type,genre,category_title)
        try:self._last_fullscreen_key=str((_GRID_NAV_STATE.get(self._session_nav_key,{}) or {}).get("last_fullscreen_key") or "")
        except Exception:self._last_fullscreen_key=""
        self._preview_ready_timer=eTimer();self._preview_ready_conn=None
        try:self._preview_ready_conn=self._preview_ready_timer.timeout.connect(self._check_preview_ready)
        except Exception:self._preview_ready_timer.callback.append(self._check_preview_ready)
        self["channel_name"]=Label("");self["current_channel_top"]=Label("");self["date_label"]=Label(time.strftime("%A, %d %B %Y"));self["preview_status"]=Label("")
        self["page_adaptive_bg"]=Pixmap();self["preview_frame"]=Pixmap();self["live_info_bg"]=Pixmap();self["page_label_bg"]=Pixmap();self["current_channel_bg"]=Pixmap();self["clock_glass_bg"]=Pixmap()
        self["tech_bg1"]=Pixmap();self["tech_bg2"]=Pixmap();self["tech_bg3"]=Pixmap();self["tech_bg4"]=Pixmap()
        for _ri in range(self.page_size):self["row_adaptive%d"%_ri]=Pixmap()
        self._live_chrome_jobs=queue.Queue();self._live_chrome_token=0;self._live_chrome_key=None
        self["epg_hint"]=Label("NOW / NEXT");self["tech1"]=Label("AUTO");self["tech2"]=Label("LIVE");self["tech3"]=Label("CHANNEL");self["tech4"]=Label("OK Preview")
        self["now_progress"]=ProgressBar()
        try:self["now_progress"].setValue(0)
        except Exception as exc:optional_failure("ui",exc)
        self.onLayoutFinish.append(self._prepare_preview_window);self.onLayoutFinish.append(self._apply_live_neutral_chrome);self.onClose.append(self._stop_live_preview);self.onClose.append(self._stop_live_grid_hooks)
        self["red"].setText("Back");self["green"].setText("Favorite");self["yellow"].setText("Previous page");self["blue"].setText("Next page");self["brand"].setText("")
        try:force_session_silence(self.session,"",force=False)
        except Exception as exc:optional_failure("ui",exc)

    def _stop_live_grid_hooks(self):
        for result_queue_name in ("_preview_jobs","_live_chrome_jobs"):
            result_queue=getattr(self,result_queue_name,None)
            if result_queue is None:continue
            try:
                while True:result_queue.get_nowait()
            except queue.Empty:
                pass
            except Exception as exc:
                optional_failure("ui.live_grid_queue_cleanup",exc)
        for hook_name,callback in (
            ("onLayoutFinish",self._prepare_preview_window),
            ("onLayoutFinish",self._apply_live_neutral_chrome),
        ):
            try:
                hooks=getattr(self,hook_name,None)
                if hooks is not None and callback in hooks:hooks.remove(callback)
            except Exception as exc:
                optional_failure("ui.live_grid_hook_cleanup",exc)

    def _prepare_preview_window(self):
        # session.VideoPicture + Renderer/Pig owns decoder-0 geometry.
        # eVideoWidget applies the preview rectangle when shown and restores full
        # screen automatically when the screen/renderer is hidden.
        try:LOG.info("Live preview Pig renderer ready rect=%s", self.PREVIEW_RECT)
        except Exception as exc:optional_failure("ui",exc)

    def _apply_live_neutral_chrome(self):
        try:
            chrome={"page":asset("us205_live_page_neutral.png"),"selected":asset("us205_live_selection_neutral.png"),"preview":asset("us205_live_preview_glass.png"),"info":asset("us205_live_info_glass.png"),"tech":asset("us205_live_tech_glass.png")}
            # Generate correct-size neutral HUD materials once. This prevents
            # clock/channel/counter glass from disappearing while a picon is pending.
            neutral_source=asset("us205_live_selection_neutral.png")
            neutral_hud=_build_live_adaptive_chrome_211(neutral_source,"live_neutral_hud_v3") if os.path.isfile(neutral_source) else {}
            for _key in ("counter","channel_top","clock","row"):
                _path=(neutral_hud or {}).get(_key)
                if _path and os.path.isfile(_path):chrome[_key]=_path
            if not chrome.get("counter"):chrome["counter"]=chrome["tech"]
            self._apply_live_chrome(chrome)
        except Exception as exc:optional_failure("ui.live211_neutral",exc)

    def _apply_live_chrome(self,chrome):
        if not isinstance(chrome,dict):return
        # Keep the exact last-good files. Rebinding an already-built PNG is
        # virtually free and avoids regenerating adaptive artwork after Player.
        try:self._live_last_chrome=dict(chrome)
        except Exception:self._live_last_chrome={}
        try:
            for key,widget in (("page","page_adaptive_bg"),("selected","selection"),("preview","preview_frame"),("info","live_info_bg"),("counter","page_label_bg"),("channel_top","current_channel_bg"),("clock","clock_glass_bg")):
                path=chrome.get(key)
                if path and os.path.isfile(path):self[widget].instance.setPixmapFromFile(path);self[widget].show()
            tech=chrome.get("tech")
            if tech and os.path.isfile(tech):
                for name in ("tech_bg1","tech_bg2","tech_bg3","tech_bg4"):
                    self[name].instance.setPixmapFromFile(tech);self[name].show()
            rowglass=chrome.get("row")
            if rowglass and os.path.isfile(rowglass):
                for _ri in range(self.page_size):
                    self["row_adaptive%d"%_ri].instance.setPixmapFromFile(rowglass)
                    self["row_adaptive%d"%_ri].show()
            accent=chrome.get("accent")
            if accent:
                color="#%02x%02x%02x"%tuple(accent[:3])
                try:self["now_progress"].instance.setForegroundColor(parseColor(color))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
                try:self["channel_name"].instance.setForegroundColor(parseColor(color))
                except Exception as exc:optional_failure("ui.silent_guard",exc)
        except Exception as exc:optional_failure("ui.live211_apply",exc)

    def _force_live_visual_rebind(self):
        """Restore every Live visual surface after returning from Full Screen."""
        if getattr(self,"_screen_closed",False):return

        # Snapshot adaptive state BEFORE painting neutral fallback, because
        # _apply_live_chrome() itself updates _live_last_chrome.
        last=dict(getattr(self,"_live_last_chrome",{}) or {})

        # Neutral assets are guaranteed and give us an immediate complete frame.
        try:self._apply_live_neutral_chrome()
        except Exception as exc:optional_failure("ui.live_force_neutral",exc)

        # Then synchronously re-apply the exact last adaptive material if valid.
        if last:
            try:self._apply_live_chrome(last)
            except Exception as exc:optional_failure("ui.live_force_last_chrome",exc)

        # Re-show visible channel artwork from the retained slot map/cache.
        for slot,path in list((getattr(self,"_grid_slot_paths",{}) or {}).items()):
            if slot >= len(getattr(self,"grid_items",[]) or []):continue
            try:
                ptr=self._grid_cache_get(path) if path else None
                if ptr is not None:self._grid_set_ptr(slot,ptr)
                elif path and os.path.isfile(path):self._grid_set_local(slot,path)
            except Exception as exc:optional_failure("ui.live_force_art",exc)

        # The selection widget must be moved/shown after all background layers.
        try:self._update_selection()
        except Exception as exc:optional_failure("ui.live_force_selection",exc)

        # Force the selected-channel adaptive pass even if its key is unchanged.
        try:
            item=self.grid_items[self.index] if self.grid_items and 0<=self.index<len(self.grid_items) else None
            path=self._live_palette_source_for_item(item,self.index)
            self._schedule_live_chrome(path,force=True,item=item,slot=self.index)
        except Exception as exc:optional_failure("ui.live_force_schedule",exc)

    def _live_palette_source_for_item(self,item=None,slot=None):
        """Resolve palette art from the currently selected channel identity."""
        item=item or (self.grid_items[self.index] if self.grid_items and 0<=self.index<len(self.grid_items) else None)
        slot=self.index if slot is None else int(slot)
        if not isinstance(item,dict):return None
        try:
            raw_url=_image_url(item)
            url=_optimized_artwork_url(self._grid_absolute_url(raw_url,item),False) if raw_url else None
            if url:
                digest=hashlib.sha1(url.encode("utf-8","ignore")).hexdigest()
                thumb=_thumb_path(digest,self._grid_image_size)
                if _valid_cache_file(thumb):return thumb
                original=_find_original_artwork(digest)
                if original and os.path.isfile(original):return original
        except Exception as exc:optional_failure("ui.live_palette_url",exc)
        # Slot path is only a fallback for this exact visible row, never a global last source.
        try:
            path=(getattr(self,"_grid_slot_paths",{}) or {}).get(slot)
            if path and os.path.isfile(path) and "placeholder" not in os.path.basename(path).casefold():
                return path
        except Exception as exc:optional_failure("ui.live_palette_slot",exc)
        return None

    def _schedule_live_chrome(self,path=None,force=False,item=None,slot=None):
        if self._screen_closed:return
        slot=self.index if slot is None else int(slot)
        item=item or (self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else None)
        identity=self._preview_key(item) if isinstance(item,dict) else ""
        path=self._live_palette_source_for_item(item,slot) or path
        if not path or not os.path.isfile(path):
            # Never inherit the previous channel colour when the current picon is unresolved.
            self._live_chrome_token+=1
            self._live_chrome_key=None
            self._live_chrome_identity=identity
            self._apply_live_neutral_chrome()
            return
        try:stamp=str(int(os.path.getmtime(path)))
        except Exception:stamp="0"
        key=hashlib.sha1((str(identity)+"|"+path+"|"+stamp+"|livehud3d_v3").encode("utf-8","ignore")).hexdigest()[:18]
        if key==self._live_chrome_key and identity==getattr(self,"_live_chrome_identity","") and not force:return
        self._live_chrome_token+=1;token=self._live_chrome_token
        self._live_chrome_key=key;self._live_chrome_identity=identity
        def worker():
            chrome=_build_live_adaptive_chrome_211(path,key)
            try:self._live_chrome_jobs.put((token,key,identity,path,chrome))
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:_GRID_ACCENT_EXECUTOR.submit(worker)
        except Exception as exc:optional_failure("ui.live211_submit",exc)

    def _grid_queue_decode(self,generation,slot,path):
        # beta28: Live picons are tiny, already-local display assets. On OpenBH
        # the shared ePicLoad queue used for large posters can complete without a
        # usable Live pixmap callback on some image/provider combinations. Render
        # the prepared local picon directly into artN and keep ePicLoad for VOD/
        # Series only. This also makes the final Live stage deterministic: once
        # download/cache produced a valid file, the row gets that exact file.
        if generation!=getattr(self,"_grid_generation",generation):
            _live_picon_diag("RENDER_STALE slot=%r generation=%r current=%r path=%r"%(slot,generation,getattr(self,"_grid_generation",None),path))
            return
        _live_picon_diag("RENDER_ENTER slot=%r path=%r exists=%r size=%r"%(slot,path,bool(path and os.path.isfile(path)),(os.path.getsize(path) if path and os.path.isfile(path) else -1)))
        if path and os.path.isfile(path):
            try:
                self._grid_slot_paths[slot]=path
                self._grid_palette_sources[slot]=path
                self._grid_set_local(slot,path)
                _live_picon_diag("RENDER_SET_LOCAL_OK slot=%r channel=%r path=%r widget_instance=%r"%(slot,str((self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else {}).get("name") or ""),path,getattr(self["art%d"%slot],"instance",None) is not None))
                if self.media_type=="itv":
                    try:LOG.info("Live picon direct render slot=%s channel=%r path=%s",slot,str((self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else {}).get("name") or ""),path)
                    except Exception:pass
                if slot==getattr(self,"index",-1):
                    item=self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else None
                    self._schedule_live_chrome(path,item=item,slot=slot)
                return
            except Exception as exc:
                optional_failure("ui.live_direct_picon_render",exc)
        # Defensive fallback only; valid Live cache files should never need it.
        PremiumGridBase._grid_queue_decode(self,generation,slot,path)
        if slot==getattr(self,"index",-1):
            item=self.grid_items[slot] if self.grid_items and 0<=slot<len(self.grid_items) else None
            self._schedule_live_chrome(path,item=item,slot=slot)

    @staticmethod
    def _preview_key(item):
        return str((item or {}).get("id") or (item or {}).get("ch_id") or (item or {}).get("cmd") or (item or {}).get("name") or "")

    def _drain_jobs(self):
        PremiumGridBase._drain_jobs(self)
        while True:
            try:token,key,identity,path,chrome=self._live_chrome_jobs.get_nowait()
            except queue.Empty:break
            except Exception:continue
            if self._screen_closed or token!=self._live_chrome_token or key!=self._live_chrome_key:continue
            current_item=self.grid_items[self.index] if self.grid_items and 0<=self.index<len(self.grid_items) else None
            current_identity=self._preview_key(current_item) if isinstance(current_item,dict) else ""
            current_path=self._live_palette_source_for_item(current_item,self.index)
            if identity!=current_identity:continue
            if current_path and os.path.abspath(current_path)!=os.path.abspath(path):continue
            self._apply_live_chrome(chrome)
        if self._screen_closed:return
        while True:
            try:
                job=self._preview_jobs.get_nowait();callback,value,is_error=job[:3];task_id=job[3] if len(job)>3 else None
            except queue.Empty:break
            except Exception:continue
            if task_id is not None and self._preview_task_id is not None and task_id!=self._preview_task_id:continue
            self._preview_handle=None;self._preview_task_id=None
            if callback:
                try:callback(value)
                except Exception:
                    try:self["preview_status"].setText("Preview unavailable")
                    except Exception as exc:optional_failure("ui",exc)
            elif is_error:
                try:self["preview_status"].setText("Preview unavailable")
                except Exception as exc:optional_failure("ui",exc)

    def _cancel_preview_request(self):
        handle=self._preview_handle;self._preview_handle=None;self._preview_task_id=None;self._pending_preview_key=None
        try:self._preview_ready_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        self._preview_pending_url=None;self._preview_pending_item=None;self._preview_pending_engine=None;self._preview_engine_candidates=[];self._preview_engine_pos=0;self._preview_poll_count=0
        if handle is not None:
            try:handle.cancel()
            except Exception as exc:optional_failure("ui",exc)

    def _update_header(self,item):
        PremiumGridBase._update_header(self,item)
        try:
            cid=str(item.get("id") or item.get("ch_id") or "");epg=self._epg_cache.get(cid,{})
            self["now_progress"].setValue(int(epg.get("percent") or 0))
        except Exception as exc:optional_failure("ui",exc)

    def _update_selection(self):
        PremiumGridBase._update_selection(self)
        if not self.grid_items:return
        item=self.grid_items[self.index];raw=item.get("name") or item.get("title") or "Channel"
        clean_name=_clean_display_text(_clean_live_channel_name(raw),80)
        self["channel_name"].setText(clean_name)
        # Responsive one-line title. Keep short names bold/large, reduce only when
        # the actual label gets long enough to risk clipping its 820px card.
        try:
            units=sum(1.65 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in clean_name)
            font_size=32 if units<=26 else (29 if units<=34 else (26 if units<=43 else (23 if units<=53 else 20)))
            if self["channel_name"].instance is not None:self["channel_name"].instance.setFont(gFont("Regular",font_size))
        except Exception as exc:optional_failure("ui.live_title_font",exc)
        q=quality_badges(raw) or "AUTO";self["tech1"].setText(q[:16])
        self["tech2"].setText("Catch-up" if (item.get("allow_archive") or item.get("tv_archive_duration") or item.get("archive")) else "LIVE")
        self["tech3"].setText("Channel %d"%max(1,(int(self.page or 1)-1)*int(self.page_size or 1)+int(self.index)+1))
        key=self._preview_key(item)
        if self._pending_preview_key:
            self["preview_status"].setText("Loading preview...");self["tech4"].setText("Loading..." if key==self._pending_preview_key else "OK Preview")
        elif self._active_preview_key:
            self["preview_status"].setText("");self["tech4"].setText("OK Full Screen" if key==self._active_preview_key else "OK Preview")
        else:
            self["preview_status"].setText("");self["tech4"].setText("OK Preview")
        # Palette source must follow the currently selected picon, not whichever
        # thumbnail path happened to be decoded first. Force is safe because the
        # generated chrome is HDD-cached; it only guarantees a real rebind.
        self["current_channel_top"].setText(clean_name)
        try:
            units=sum(1.65 if ord(ch)>0x2ff else (0.58 if ch in " ilI1|.,:'" else 1.0) for ch in clean_name)
            top_size=25 if units<=28 else (22 if units<=38 else (19 if units<=50 else 16))
            if self["current_channel_top"].instance is not None:self["current_channel_top"].instance.setFont(gFont("Regular",top_size))
        except Exception as exc:optional_failure("ui.live_top_title_font",exc)
        palette=self._live_palette_source_for_item(item,self.index)
        self._schedule_live_chrome(palette,item=item,slot=self.index)

    def _live_nav_total(self):
        try:return max(len(self.grid_items or []),int(self._portal_total or 0))
        except Exception:return len(self.grid_items or [])

    def _live_nav_absolute(self):
        return max(0,(int(self.page or 1)-1)*int(self.page_size or 1)+int(self.index or 0))

    def _live_nav_go_absolute(self,absolute):
        total=self._live_nav_total()
        if total<=0:return
        absolute=max(0,min(total-1,int(absolute)))
        target_page=absolute//self.page_size+1
        target_index=absolute%self.page_size
        if target_page==self.page and self.grid_items:
            self.index=min(target_index,max(0,len(self.grid_items)-1))
            self._update_selection();self._update_page_counter()
        else:
            self.load_page(target_page,target_index)

    def move_up(self):
        """Exact Mini List rule: previous item globally; #1 wraps to final channel."""
        if not self.grid_items or not self._nav_allowed():return
        total=self._live_nav_total()
        if total<=0:return
        current=self._live_nav_absolute()
        self._live_nav_go_absolute((current-1)%total)

    def move_down(self):
        """Exact Mini List rule: next item globally; final channel wraps to #1."""
        if not self.grid_items or not self._nav_allowed():return
        total=self._live_nav_total()
        if total<=0:return
        current=self._live_nav_absolute()
        self._live_nav_go_absolute((current+1)%total)

    def _live_page_shift(self,direction):
        """Exact Mini List LEFT/RIGHT page rule with the same row preserved."""
        if not self.grid_items or not self._nav_allowed():return
        total=self._live_nav_total()
        if total<=0:return
        page_size=max(1,int(self.page_size or 1))
        current_abs=self._live_nav_absolute()
        current_page=current_abs//page_size
        row=current_abs%page_size
        last_page=max(0,(total-1)//page_size)

        if int(direction)<0:
            if current_page<=0:
                target=0
            else:
                target=(current_page-1)*page_size+row
        else:
            if current_page>=last_page:
                target=total-1
            else:
                target=min(total-1,(current_page+1)*page_size+row)
        self._live_nav_go_absolute(target)

    def move_left(self):
        self._live_page_shift(-1)

    def move_right(self):
        self._live_page_shift(1)

    def open_selected(self):
        if self._busy or not self.grid_items:return
        item=self.grid_items[self.index];key=self._preview_key(item)
        if self._active_preview_key==key and self._active_preview_url:
            self._open_preview_fullscreen(item)
        elif key and key==getattr(self,"_last_fullscreen_key",""):
            # Returning to the same folder/channel should not force the viewer
            # through Preview again. Resolve a fresh link and go straight back
            # to Full Screen.
            self._open_direct_fullscreen(item)
        else:
            self._start_preview_request(item)

    def _open_direct_fullscreen(self,item):
        if not isinstance(item,dict) or self._screen_closed:return
        self._preview_token+=1;token=self._preview_token;self._cancel_preview_request()
        self["preview_status"].setText("Opening channel...");self["tech4"].setText("Opening...")
        def work():return self.client.create_link(item,"itv")
        def ok(url):
            if token!=self._preview_token or self._screen_closed:return
            if not isinstance(url,str) or not url.strip():
                self["preview_status"].setText("Channel unavailable");self["tech4"].setText("OK Preview");return
            cfg=load_settings();engine=_configured_playback_engine(cfg)
            self._launch_live_fullscreen(item,url.strip(),engine,False)
        def fail(err):
            if token==self._preview_token:
                self["preview_status"].setText("Channel unavailable");self["tech4"].setText("OK Preview")
        try:
            handle=TASKS.submit(work,self._preview_jobs,ok=ok,fail=fail);self._preview_handle=handle;self._preview_task_id=getattr(handle,"task_id",None) if handle is not None else None
        except Exception:fail(None)

    def _start_preview_request(self,item):
        if not isinstance(item,dict) or self._screen_closed:return
        self._preview_token+=1;token=self._preview_token;self._cancel_preview_request();key=self._preview_key(item);self._pending_preview_key=key
        self["preview_status"].setText("Loading preview...");self["tech4"].setText("Loading...")
        def work():return self.client.create_link(item,"itv")
        def ok(url):
            if token!=self._preview_token or self._screen_closed:return
            if not isinstance(url,str) or not url.strip():
                self._pending_preview_key=None;self["preview_status"].setText("Preview unavailable");self["tech4"].setText("OK Preview");return
            self._begin_preview_video(url.strip(),item,key)
        def fail(err):
            if token==self._preview_token:
                self._pending_preview_key=None
                try:self["preview_status"].setText("Preview unavailable");self["tech4"].setText("OK Preview")
                except Exception as exc:optional_failure("ui",exc)
        try:
            handle=TASKS.submit(work,self._preview_jobs,ok=ok,fail=fail);self._preview_handle=handle;self._preview_task_id=getattr(handle,"task_id",None) if handle is not None else None
        except Exception:fail(None)

    def _preview_main_engines(self,url):
        cfg=load_settings()
        try:engine=int(cfg.get("service_type",4097))
        except Exception:engine=4097
        if engine not in (1,4097,5001,5002,8193):engine=4097
        return [engine]


    def _begin_preview_video(self,url,item,key):
        self._preview_restore_state=(self._active_preview_key,self._active_preview_url,self._active_preview_item,self._active_preview_engine)
        self._preview_pending_url=url;self._preview_pending_item=dict(item);self._pending_preview_key=key;self._preview_engine_candidates=self._preview_main_engines(url);self._preview_engine_pos=0
        self._try_preview_engine()

    def _try_preview_engine(self):
        try:self._preview_ready_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        if self._screen_closed:return
        if self._preview_engine_pos>=len(self._preview_engine_candidates):self._preview_failed_restore();return
        engine=self._preview_engine_candidates[self._preview_engine_pos];self._preview_engine_pos+=1;self._preview_pending_engine=engine;self._preview_poll_count=0
        url=self._preview_pending_url or "";item=self._preview_pending_item or {}
        try:
            ref=eServiceReference(int(engine),0,url);ref.setName(str(item.get("name") or item.get("title") or "Live preview"))
            result=self.session.nav.playService(ref)
            if result is False:raise RuntimeError("engine %s rejected preview"%engine)
            self._main_preview_reference=ref
            self["preview_status"].setText("Starting preview...")
            try:LOG.info("Live preview Pig/decoder0 start engine=%s",engine)
            except Exception as exc:optional_failure("ui",exc)
            self._preview_ready_timer.start(250,True)
        except Exception as exc:
            try:LOG.exception("Live preview decoder0 start failed: %s",exc)
            except Exception as exc:optional_failure("ui",exc)
            self._try_preview_engine()

    @staticmethod
    def _read_decoder_dimension(path):
        try:
            raw=open(path,"r").read().strip().lower()
            if not raw:return 0
            if raw.startswith("0x"):raw=raw[2:]
            try:return int(raw,16)
            except Exception:return int(raw,10)
        except Exception:return 0

    def _main_video_size(self):
        width=height=0
        try:
            current=self.session.nav.getCurrentlyPlayingServiceReference()
            if current is None:return 0,0
            service=self.session.nav.getCurrentService();info=service and service.info()
            if info is not None:
                width=int(info.getInfo(iServiceInformation.sVideoWidth) or 0);height=int(info.getInfo(iServiceInformation.sVideoHeight) or 0)
        except Exception:width=height=0
        if width<=0 or height<=0:
            for base in ("/proc/stb/vmpeg/0","/proc/stb/video/0"):
                w=self._read_decoder_dimension(base+"/xres");h=self._read_decoder_dimension(base+"/yres")
                if w>0 and h>0:width,height=w,h;break
        return width,height

    def _check_preview_ready(self):
        if self._screen_closed:return
        width,height=self._main_video_size()
        current_ok=False
        try:
            cur=self.session.nav.getCurrentlyPlayingServiceReference()
            current_ok=(cur is not None and self._main_preview_reference is not None and cur.toString()==self._main_preview_reference.toString())
        except Exception:current_ok=False
        if current_ok and width>0 and height>0:
            key=self._pending_preview_key;item=self._preview_pending_item or {};url=self._preview_pending_url or ""
            self._active_preview_key=key;self._active_preview_url=url;self._active_preview_item=dict(item);self._active_preview_engine=self._preview_pending_engine
            self._pending_preview_key=None;self._preview_pending_url=None;self._preview_pending_item=None;self._preview_restore_state=None
            self["preview_status"].setText("");current=self.grid_items[self.index] if self.grid_items else {}
            self["tech4"].setText("OK Full Screen" if self._preview_key(current)==self._active_preview_key else "OK Preview")
            try:LOG.info("Live preview Pig/decoder0 ready %sx%s",width,height)
            except Exception as exc:optional_failure("ui",exc)
            return
        self._preview_poll_count+=1
        if self._preview_poll_count<24:self._preview_ready_timer.start(250,True);return
        try:LOG.warning("Live preview Pig/decoder0 engine %s never reported video",self._preview_pending_engine)
        except Exception as exc:optional_failure("ui",exc)
        self._try_preview_engine()

    def _preview_failed_restore(self):
        # Deterministic preview: one start attempt for the requested channel.
        # Never recreate the previous decoder/service automatically when a new
        # preview fails; repeated hidden restore cycles were visible in dmesg as
        # VIDEO_STOP/VIDEO_PLAY churn and can leak Broadcom native memory.
        self._pending_preview_key=None
        failed_url=self._preview_pending_url or ""
        self._preview_pending_url=None;self._preview_pending_item=None;self._preview_pending_engine=None
        self._preview_restore_state=None;self._main_preview_reference=None
        self._preview_engine_candidates=[];self._preview_engine_pos=0
        self["preview_status"].setText("Preview unavailable");self["tech4"].setText("OK Preview")
        try:force_session_silence(self.session,failed_url,force=True)
        except Exception as exc:optional_failure("ui.preview_failed_stop",exc)


    def _open_preview_fullscreen(self,item):
        url=self._active_preview_url
        if not url:return self._start_preview_request(item)
        cfg=load_settings();engine=_configured_playback_engine(cfg)
        self._launch_live_fullscreen(item,url,engine,True)

    def _launch_live_fullscreen(self,item,url,engine,reuse_current):
        name=str(item.get("name") or item.get("title") or "Live channel")
        add_recently_played(self.profile,"itv",item)
        key=self._preview_key(item);selected_index=self.index
        self._last_fullscreen_key=key
        try:
            _state=dict(_GRID_NAV_STATE.get(self._session_nav_key,{}) or {})
            _state["last_fullscreen_key"]=key
            _GRID_NAV_STATE[self._session_nav_key]=_state
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        def returned(result=None):
            if self._screen_closed:return

            # UltraStalkerPlayer EXIT already hard-stops the owned IPTV service
            # and restores the receiver service captured at the plugin boundary.
            # Do NOT call force_session_silence() here: its native stop would kill
            # that just-restored TV service. Do NOT auto-start the portal preview
            # either: that was recreating the fullscreen channel behind the UI,
            # leaving its audio alive until another service replaced it.
            self._main_preview_reference=None
            self._active_preview_key=None;self._active_preview_url=None;self._active_preview_item=None;self._active_preview_engine=None
            self._pending_preview_key=None;self._preview_pending_url=None;self._preview_pending_item=None
            self._preview_pending_engine=None;self._preview_engine_candidates=[];self._preview_engine_pos=0
            self["preview_status"].setText("");self["tech4"].setText("OK Preview")
            self.index=max(0,min(selected_index,len(self.grid_items)-1)) if self.grid_items else 0

            # The restored receiver TV remains the service shown by the Pig/video
            # window. Rebind all Ultra Stalker adaptive/glass layers around it.
            try:self._force_live_visual_rebind()
            except Exception as exc:optional_failure("ui.live_return_chrome",exc)
        payload=_player_payload(item,self.profile,media_type="itv")
        snapshot=[dict(x) for x in list(self.grid_items or []) if isinstance(x,dict)]
        absolute_index=max(0,(int(self.page)-1)*int(self.page_size)+int(selected_index))
        payload["_live_folder_channels"]=snapshot
        payload["_live_absolute_index"]=absolute_index
        payload["_live_folder_total"]=int(self._portal_total or len(snapshot))
        payload["_live_page_size"]=int(self.page_size)
        payload["_live_folder_page"]=int(self.page)
        payload["_live_folder_title"]=str(self.category_title or "Live Channels")
        payload["_live_page_loader"]=self._grid_page_data
        payload["_live_client_ref"]=self.client
        self.session.openWithCallback(returned,UltraStalkerPlayer,url,name,"itv",engine,payload,bool(reuse_current))

    def _stop_live_preview(self):
        try:self._preview_ready_timer.stop()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._preview_ready_conn is not None:self._preview_ready_conn.disconnect()
        except Exception as exc:optional_failure("ui",exc)
        try:
            if self._check_preview_ready in self._preview_ready_timer.callback:self._preview_ready_timer.callback.remove(self._check_preview_ready)
        except Exception as exc:optional_failure("ui.preview_timer_callback",exc)
        preview_url=self._active_preview_url or self._preview_pending_url or ""
        self._preview_token+=1;self._cancel_preview_request()
        self._main_preview_reference=None
        self._active_preview_key=None;self._active_preview_url=None;self._active_preview_item=None;self._active_preview_engine=None
        try:force_session_silence(self.session,preview_url,force=True)
        except Exception as exc:optional_failure("ui.preview_close_stop",exc)


