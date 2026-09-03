"""Grid artwork mixin extracted from ui.py without changing behavior."""

import os
import time
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag

# Runtime dependencies are injected by ui.py after shared caches/executors exist.
def configure_grid_artwork(**deps):
    globals().update(deps)

class GridArtworkMixin:
    """Bounded three-page artwork cache with one sequential OpenBH decoder."""
    def _grid_art_init(self, slot_names, image_size, profile, client):
        self._grid_slot_names = list(slot_names)
        self._grid_image_size = tuple(image_size)
        self._grid_profile = profile
        self._grid_client = client
        self._grid_generation = 0
        self._grid_download_jobs = queue.Queue()
        self._grid_decode_queue = []
        self._grid_decode_busy = False
        self._grid_decode_active = None
        self._grid_closed = False
        self._grid_slots = threading.BoundedSemaphore(6)
        self._grid_decode_cache = OrderedDict()
        self._grid_decode_cache_limit = max(self.page_size + 2, 14)
        global _GLOBAL_GRID_PIXMAP_CACHE_LIMIT
        try:_GLOBAL_GRID_PIXMAP_CACHE_LIMIT = 2 if _available_memory_mb() < 220 else 6
        except Exception:_GLOBAL_GRID_PIXMAP_CACHE_LIMIT = 2
        self._grid_waiting = {}
        self._grid_queued = set()
        self._grid_slot_paths = {}
        self._grid_palette_sources = {}
        self._grid_accent_jobs = queue.Queue()
        self._grid_accent_pending = set()
        self._grid_download_inflight = {}
        self._grid_download_futures = {}
        self._grid_poster_thumb_pending = set()
        self._grid_download_lock = threading.Lock()
        try:_native_image_pressure_relief(force=False)
        except Exception as exc:optional_failure("ui.grid_pressure_relief_init",exc)
        self._grid_picload = ePicLoad()
        self._grid_pic_connection = None
        try:
            self._grid_picload.PictureData.get().append(self._grid_picture_ready)
        except Exception:
            try:
                self._grid_pic_connection = self._grid_picload.PictureData.connect(self._grid_picture_ready)
            except Exception as exc:
                optional_failure("ui", exc)
    def _grid_art_layout_ready(self):
        try:
            self._grid_picload.setPara((self._grid_image_size[0], self._grid_image_size[1], 1, 1, False, 1, "#000000"))
        except Exception as exc:
            optional_failure("ui", exc)
    def _grid_cache_get(self, path):
        ptr = self._grid_decode_cache.get(path)
        if ptr is not None:
            try:
                self._grid_decode_cache.move_to_end(path)
            except Exception as exc:
                optional_failure("ui", exc)
            return ptr
        # us200: keep a tiny process-wide decoded poster cache so leaving the
        # plugin/grid and coming back in the same Enigma2 session is instant.
        try:
            with _GLOBAL_GRID_PIXMAP_CACHE_LOCK:
                ptr = _GLOBAL_GRID_PIXMAP_CACHE.get(path)
                if ptr is not None:
                    _GLOBAL_GRID_PIXMAP_CACHE.move_to_end(path)
            if ptr is not None:
                self._grid_decode_cache[path] = ptr
                return ptr
        except Exception as exc:
            optional_failure("ui.grid_global_cache_get", exc)
        return None

    def _grid_cache_put(self, path, ptr):
        if ptr is None:
            return
        self._grid_decode_cache[path] = ptr
        try:
            self._grid_decode_cache.move_to_end(path)
        except Exception as exc:
            optional_failure("ui", exc)
        while len(self._grid_decode_cache) > self._grid_decode_cache_limit:
            try:
                self._grid_decode_cache.popitem(last=False)
            except Exception:
                break
        try:
            with _GLOBAL_GRID_PIXMAP_CACHE_LOCK:
                _GLOBAL_GRID_PIXMAP_CACHE[path] = ptr
                _GLOBAL_GRID_PIXMAP_CACHE.move_to_end(path)
                while len(_GLOBAL_GRID_PIXMAP_CACHE) > _GLOBAL_GRID_PIXMAP_CACHE_LIMIT:
                    _GLOBAL_GRID_PIXMAP_CACHE.popitem(last=False)
        except Exception as exc:
            optional_failure("ui.grid_global_cache_put", exc)

    def _grid_set_local(self, slot, path):
        try:
            widget = self[self._grid_slot_names[slot]]
            png = cached_png(path)
            if widget.instance is not None:
                if png is not None:
                    widget.instance.setPixmap(png)
                else:
                    widget.instance.setPixmapFromFile(path)
            widget.show()
        except Exception as exc:
            optional_failure("ui", exc)
    def _grid_set_ptr(self, slot, ptr):
        try:
            widget = self[self._grid_slot_names[slot]]
            if widget.instance is not None and ptr is not None:
                widget.instance.setPixmap(ptr)
            widget.show()
        except Exception as exc:
            optional_failure("ui", exc)
    def _grid_absolute_url(self, value, item=None):
        if not value:
            return None
        if value.startswith("//"):
            base=str((item or {}).get("_art_base") or self._grid_profile.get("portal","http://"))
            scheme=urllib.parse.urlsplit(base or "http://").scheme or "http"
            return scheme+":"+value
        if value.startswith(("http://","https://")):
            return _normalize_provider_image_url(value)
        # M3U/Xtream rows carry their own artwork origin. This is deliberately
        # item-scoped because visible Live rows are downloaded concurrently.
        art_base=str((item or {}).get("_art_base") or "") if isinstance(item,dict) else ""
        base=art_base or self._grid_profile.get("portal","")
        return _normalize_provider_image_url(urllib.parse.urljoin(str(base).rstrip("/")+"/",value.lstrip("/")))

    def _ui_diag_first_real_art(self,path,slot):
        try:
            if getattr(self,"_ui_diag_first_poster_logged",False):return
            value=str(path or "");name=os.path.basename(value).lower()
            if not value or "placeholder" in name or name.startswith(("poster_movie","poster_series","placeholder_live")):return
            self._ui_diag_first_poster_logged=True
            started=float(getattr(self,"_ui_diag_page_request_mono",time.monotonic()) or time.monotonic())
            _ui_diag("ui_first_art",screen="grid",media_type=getattr(self,"media_type",""),page=getattr(self,"page",0),slot=int(slot),
                     elapsed_ms=int(max(0.0,(time.monotonic()-started)*1000.0)))
        except Exception:pass

    def _grid_queue_decode(self, generation, slot, path):
        try:
            locked=str((getattr(self,"_grid_visual_locks",{}) or {}).get(slot) or "")
            current=str((getattr(self,"_grid_slot_paths",{}) or {}).get(slot) or "")
            if locked and current and str(path)!=current:
                return
        except Exception as exc:optional_failure("ui.grid_visual_lock_check",exc)
        self._grid_waiting.setdefault(path, set()).add((generation, slot))
        self._grid_slot_paths[slot] = path
        palette_path=getattr(self,"_grid_palette_sources",{}).get(slot) or path
        try:
            if hasattr(self,"_remember_grid_visual"):
                self._remember_grid_visual(slot,display=path,poster=palette_path,palette=palette_path)
        except Exception as exc:optional_failure("ui.grid_visual_ram_decode",exc)
        if getattr(self,"media_type",None) in ("vod","series"):
            # Every visible card owns a normal adaptive chrome derived from its
            # poster. Selection is a separate overlay and never changes the card.
            self._grid_schedule_adaptive_selector(generation, slot, palette_path)
        if slot == getattr(self,"index",-1): self._grid_schedule_page_mood(palette_path)
        ptr = self._grid_cache_get(path)
        if ptr is not None:
            self._grid_set_ptr(slot, ptr)
            self._ui_diag_first_real_art(path,slot)
            try:
                if getattr(self,"media_type",None) in ("vod","series") and hasattr(self,"_grid_is_m3u_source") and self._grid_is_m3u_source():
                    name=os.path.basename(str(path or "")).lower()
                    if not ("placeholder" in name or name.startswith(("poster_movie","poster_series"))):
                        self._grid_visual_locks.setdefault(slot,str(path))
            except Exception as exc:optional_failure("ui.grid_visual_lock_cached",exc)
            return
        active_path = self._grid_decode_active[1] if self._grid_decode_active else None
        if path not in self._grid_queued and path != active_path:
            self._grid_decode_queue.append((generation, path))
            self._grid_queued.add(path)
            self._grid_start_decode()

    def _grid_schedule_adaptive_selector(self, generation, slot, path):
        if getattr(self, "media_type", None) not in ("vod", "series") or not path or not os.path.isfile(path):
            return
        selected_now = (slot == getattr(self, "index", -1))
        token=(generation,slot,path,selected_now)
        if token in self._grid_accent_pending:
            return
        self._grid_accent_pending.add(token)
        # Every visible card gets one normal adaptive chrome. Focus never
        # repaints the card itself; only the independent selection overlay changes.
        def worker():
            card=_grid_card_chrome_from_poster(path)
            result=_grid_selection_asset_from_poster(path) if selected_now else None
            try:self._grid_accent_jobs.put((generation,slot,path,result,card,None))
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:_GRID_ACCENT_EXECUTOR.submit(worker)
        except Exception:
            self._grid_accent_pending.discard(token)

    def _grid_drain_adaptive_selector(self):
        changed=False
        while True:
            try:
                job=self._grid_accent_jobs.get_nowait();generation,slot,path,result=job[:4];card=job[4] if len(job)>4 else None;focus_card=job[5] if len(job)>5 else None
            except queue.Empty:break
            try:
                self._grid_accent_pending.discard((generation,slot,path,False))
                self._grid_accent_pending.discard((generation,slot,path,True))
            except Exception as exc:optional_failure("ui.grid_accent_pending_cleanup",exc)
            if generation != self._grid_generation:
                continue
            if card and (getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot))==path:
                try:
                    if not hasattr(self,"_grid_card_paths"):self._grid_card_paths={}
                    self._grid_card_paths[slot]=card
                    try:
                        if hasattr(self,"_remember_grid_visual"):
                            self._remember_grid_visual(slot,palette=path,card=card)
                        if 0<=slot<len(getattr(self,"grid_items",[]) or []):
                            _save_visual_bundle(self.profile,self.media_type,self.grid_items[slot],{"poster":path,"grid_card":card},snapshot=load_detail_snapshot(self.profile,self.media_type,self.grid_items[slot]))
                    except Exception as exc:optional_failure("ui.grid_visual_persist_card",exc)
                    name="card_chrome%d"%slot
                    widget=self[name]
                    if widget.instance is not None:widget.instance.setPixmapFromFile(card);widget.show()
                except Exception as exc:optional_failure("ui.grid_adaptive_card_apply",exc)
            if result and slot == getattr(self,"index",-1) and (getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot)) == path:
                try:
                    # One visual swap only. Re-applying the same PNG can make
                    # OpenBH briefly rebuild the pixmap surface and look like a blink.
                    if result != getattr(self,"_grid_selection_visual_path",""):
                        self["selection"].instance.setPixmapFromFile(result)
                        self._grid_selection_visual_path=result
                        changed=True
                    self["selection"].show()
                    try:
                        if 0<=slot<len(getattr(self,"grid_items",[]) or []):
                            _save_visual_bundle(self.profile,self.media_type,self.grid_items[slot],{"poster":path,"grid_selection":result},snapshot=load_detail_snapshot(self.profile,self.media_type,self.grid_items[slot]))
                    except Exception as persist_exc:optional_failure("ui.grid_visual_persist_selection",persist_exc)
                except Exception as exc:optional_failure("ui.grid_adaptive_selector_apply",exc)
        return changed

    def _grid_apply_current_selector(self):
        if getattr(self,"media_type",None) not in ("vod","series"):
            return
        slot=getattr(self,"index",0)
        path=getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot)
        if not path or not os.path.isfile(path):
            return

        # CACHE-ONLY browsing. Blue Cache Artwork is the only code path allowed
        # to create adaptive card/laser/mood assets.
        try:
            stamp=str(int(os.path.getmtime(path)))
            sel_key=hashlib.sha1((path+"|"+stamp+"|us-card-select-v8-no-reflection-arc").encode("utf-8","ignore")).hexdigest()
            ready=_GRID_ACCENT_CACHE.get(sel_key) or os.path.join(THUMB_CACHE_DIR,"selcard_%s_266x398_v8.png"%sel_key)
            if ready and _valid_cache_file(ready,ttl=0):
                if ready != getattr(self,"_grid_selection_visual_path",""):
                    self["selection"].instance.setPixmapFromFile(ready);self._grid_selection_visual_path=ready
                self["selection"].show()
            else:
                # Keep the previous overlay visible until this poster's adaptive
                # overlay is ready, then _grid_drain_adaptive_selector swaps once.
                self._grid_schedule_adaptive_selector(self._grid_generation,slot,path)
        except Exception as exc:optional_failure("ui.grid_selector_cache_only",exc)

        try:
            mood_key=hashlib.sha1((path+"|"+stamp+"|grid-page-mood-v5-lowmem").encode("utf-8","ignore")).hexdigest()
            mood=os.path.join(THUMB_CACHE_DIR,"gridmood_%s_960x540_v5.jpg"%mood_key)
            if _valid_cache_file(mood,ttl=0) and mood!=getattr(self,"_grid_mood_source",""):
                self["page_adaptive_bg"].instance.setPixmap(None);self["page_adaptive_bg"].instance.setPixmapFromFile(mood)
                self["page_adaptive_bg"].show();self._grid_mood_source=path
            elif not _valid_cache_file(mood,ttl=0):
                # Keep navigation non-blocking: build a missing page mood once in
                # the existing background executor, then reuse it from HDD on
                # every later visit/selection. No palette work runs on the UI thread.
                self._grid_schedule_page_mood(path)
        except Exception as exc:optional_failure("ui.grid_mood_cache_only",exc)

    def _grid_schedule_page_mood(self,path):
        if getattr(self,"media_type",None) not in ("vod","series") or not path or not os.path.isfile(path): return
        path=str(path)
        if self._grid_mood_source==path or self._grid_mood_pending==path: return
        try:
            stamp=str(int(os.path.getmtime(path)));key=hashlib.sha1((path+"|"+stamp+"|grid-page-mood-v5-lowmem").encode("utf-8","ignore")).hexdigest();target=os.path.join(THUMB_CACHE_DIR,"gridmood_%s_960x540_v5.jpg"%key)
            if _valid_cache_file(target,ttl=0):
                self["page_adaptive_bg"].instance.setPixmap(None);self["page_adaptive_bg"].instance.setPixmapFromFile(target);self["page_adaptive_bg"].show();self._grid_mood_source=path;return
        except Exception as exc: optional_failure("ui.grid_mood_cache",exc)
        self._grid_mood_pending=path;self._grid_mood_token+=1;token=self._grid_mood_token
        def worker():
            out=_grid_page_mood_from_poster(path)
            try:self._grid_mood_jobs.put((token,path,out))
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:_GRID_ACCENT_EXECUTOR.submit(worker)
        except Exception:
            if self._grid_mood_pending==path:self._grid_mood_pending=""

    def _grid_drain_page_mood(self):
        if getattr(self,"media_type",None) not in ("vod","series"): return
        while True:
            try:token,path,result=self._grid_mood_jobs.get_nowait()
            except queue.Empty:break
            if self._grid_mood_pending==path:self._grid_mood_pending=""
            if token!=self._grid_mood_token or self._screen_closed:continue
            current=getattr(self,"_grid_palette_sources",{}).get(getattr(self,"index",0)) or self._grid_slot_paths.get(getattr(self,"index",0))
            if current!=path or not result:continue
            try:
                self["page_adaptive_bg"].instance.setPixmap(None);self["page_adaptive_bg"].instance.setPixmapFromFile(result);self["page_adaptive_bg"].show();self._grid_mood_source=path
                try:
                    slot=getattr(self,"index",0)
                    if 0<=slot<len(getattr(self,"grid_items",[]) or []):
                        _save_visual_bundle(self.profile,self.media_type,self.grid_items[slot],{"poster":path,"grid_mood":result},snapshot=load_detail_snapshot(self.profile,self.media_type,self.grid_items[slot]))
                except Exception as persist_exc:optional_failure("ui.grid_visual_persist_mood",persist_exc)
            except Exception as exc:optional_failure("ui.grid_mood_apply",exc)

    def _grid_download_future_done(self, future):
        try:
            with self._grid_download_lock:self._grid_download_futures.pop(future,None)
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _grid_cancel_queued_downloads(self):
        """Cancel old page downloads that have not started yet.

        Running downloads are allowed to finish into the HDD cache; generation
        guards prevent them from repainting the new page. This keeps the global
        executor queue bounded during rapid page flipping without corrupting
        shared URL waiters.
        """
        try:
            with self._grid_download_lock:
                pending=list(self._grid_download_futures.items())
            for future,url in pending:
                try:cancelled=future.cancel()
                except Exception:cancelled=False
                if cancelled:
                    with self._grid_download_lock:
                        self._grid_download_futures.pop(future,None)
                        self._grid_download_inflight.pop(url,None)
        except Exception as exc:optional_failure("ui.grid_cancel_queued",exc)

    def _grid_poster_thumb_path(self, source_path):
        try:
            st=os.stat(source_path)
            raw="%s|%s|%s|%sx%s|us-card-cover-v4"%(source_path,int(st.st_mtime),int(st.st_size),int(self._grid_image_size[0]),int(self._grid_image_size[1]))
            digest=hashlib.sha1(raw.encode("utf-8","ignore")).hexdigest()
            return os.path.join(THUMB_CACHE_DIR,"gridposter_%s_%dx%d.png"%(digest,int(self._grid_image_size[0]),int(self._grid_image_size[1])))
        except Exception:
            return None

    def _grid_use_persistent_poster(self, generation, slot, source_path):
        """Use/build one sharp poster representation for every grid lifecycle.

        Canonical TMDB posters survive restarts, but us199 decoded the larger
        w500 JPEG again for all 12 cards every time a grid screen was reopened.
        The tiny persistent thumb makes cold/reopen grids cheap while keeping
        network resolution completely out of the hot path.
        """
        if getattr(self,"media_type",None) in ("vod","series"):
            if not hasattr(self,"_grid_palette_sources"):self._grid_palette_sources={}
            self._grid_palette_sources[slot]=source_path
            self._grid_schedule_adaptive_selector(generation,slot,source_path)
            if slot==getattr(self,"index",-1):self._grid_schedule_page_mood(source_path)
        thumb=self._grid_poster_thumb_path(source_path)
        if thumb and _valid_cache_file(thumb, ttl=0):
            # True hot path: decode the tiny cached thumb only. Chrome, selector
            # and mood are already scheduled off-thread above. Building them
            # synchronously here made reopening a 12-card page feel frozen.
            self._grid_queue_decode(generation,slot,thumb)
            return True
        if _PILImage is None or not thumb:
            self._grid_queue_decode(generation,slot,source_path)
            return True
        token=(generation,slot,thumb)
        pending=getattr(self,"_grid_poster_thumb_pending",None)
        if pending is None:
            self._grid_poster_thumb_pending=set(); pending=self._grid_poster_thumb_pending
        if token in pending:
            return True
        pending.add(token)
        def worker():
            out=None
            try:
                if self._grid_closed or generation!=self._grid_generation:
                    return
                out=(_build_cover_thumbnail(source_path,thumb,self._grid_image_size) if getattr(self,"_poster_cover_mode",False) else _build_thumbnail(source_path,thumb,self._grid_image_size))
                try:
                    if 0<=slot<len(getattr(self,"grid_items",[]) or []):
                        card=_grid_card_chrome_from_poster(source_path)
                        _save_visual_bundle(self.profile,self.media_type,self.grid_items[slot],{"poster":source_path,"grid_thumb":out or thumb,"grid_card":card},snapshot=load_detail_snapshot(self.profile,self.media_type,self.grid_items[slot]))
                except Exception as exc:optional_failure("ui.grid_bundle_build",exc)
            finally:
                try: pending.discard(token)
                except Exception as exc: optional_failure("ui.grid_pending_cleanup", exc)
            try:
                self._grid_download_jobs.put((generation,slot,out or source_path))
            except Exception as exc:
                optional_failure("ui.silent_guard",exc)
        try:
            _POSTER_THUMB_EXECUTOR.submit(worker)
        except Exception:
            pending.discard(token)
            self._grid_queue_decode(generation,slot,source_path)
        return True

    def _grid_request_art(self, slot, item, placeholder):
        generation = self._grid_generation
        if not getattr(self, "_grid_settings", {}).get("load_images", True):
            self._grid_set_local(slot, asset(placeholder))
            return
        if getattr(self, "media_type", None) in ("vod", "series"):
            prepared_map=(getattr(self,"_grid_prepared_art",{}) or {})
            if slot in prepared_map:
                prepared=prepared_map.get(slot) or {}
                display=str(prepared.get("display") or "")
                poster=str(prepared.get("poster") or "")
                palette=str(prepared.get("palette") or "")
                card=str(prepared.get("card") or "")
                if display and os.path.isfile(display):
                    if palette:self._grid_palette_sources[slot]=palette
                    if prepared.get("visual_locked"):
                        self._grid_visual_locks[slot]=display
                    self._grid_queue_decode(generation,slot,display)
                    if card and os.path.isfile(card):
                        self._grid_card_paths[slot]=card
                        try:
                            self["card_chrome%d"%slot].instance.setPixmapFromFile(card);self["card_chrome%d"%slot].show()
                        except Exception as exc:optional_failure("ui.grid_prepared_card",exc)
                    return
                if poster and os.path.isfile(poster):
                    if prepared.get("visual_locked"):
                        self._grid_visual_locks[slot]=poster
                    self._grid_use_persistent_poster(generation,slot,poster)
                    return
                self._grid_set_local(slot,asset(placeholder));return
            # Cold fallback exists only for legacy/non-prepared paths.
            try:
                snap=(load_shared_detail_snapshot(self.profile,self.media_type,item)
                      if isinstance(item,dict) and item.get("_xtream")
                      else load_detail_snapshot(self.profile,self.media_type,item))
                bundle=_load_visual_bundle(self.profile,self.media_type,item,snap)
                bundled=str((bundle or {}).get("grid_thumb") or (bundle or {}).get("poster") or "")
                if bundled and os.path.isfile(bundled):
                    palette=str((bundle or {}).get("poster") or bundled)
                    if not hasattr(self,"_grid_palette_sources"):self._grid_palette_sources={}
                    self._grid_palette_sources[slot]=palette
                    self._grid_queue_decode(generation,slot,bundled)
                    # Apply already-generated card chrome; never rebuild it on reopen.
                    card=str((bundle or {}).get("grid_card") or "")
                    if card and os.path.isfile(card):
                        self._grid_card_paths[slot]=card
                        try:
                            self["card_chrome%d"%slot].instance.setPixmapFromFile(card);self["card_chrome%d"%slot].show()
                        except Exception as exc:optional_failure("ui.silent_guard",exc)
                    return
            except Exception as exc:optional_failure("ui.grid_visual_bundle",exc)
            try:
                # Artwork v2 owns the poster hot path. It is a direct HDD lookup
                # and never inspects portal artwork or legacy image caches.
                snap = load_artwork_v2_manifest(self.profile, self.media_type, item)
                if (not isinstance(snap,dict) or not snap.get("poster_local")) and isinstance(item,dict) and item.get("_xtream"):
                    snap = load_shared_detail_snapshot(self.profile,self.media_type,item)
                local = str((snap or {}).get("poster_local") or "")
                if local and os.path.isfile(local) and os.path.getsize(local) > 100:
                    self._grid_use_persistent_poster(generation, slot, local)
                    return
            except Exception as exc:
                optional_failure("ui.grid_hdd_poster_v2", exc)
            if slot not in getattr(self, "_grid_slot_paths", {}):
                self._grid_set_local(slot, asset(placeholder))
            # Provider-first list policy: ALL Movies/Series may use their server
            # poster immediately. TMDB remains an asynchronous quality/metadata
            # upgrade and never blocks the first visible paint.
        # Direct provider-art path: Live picons and Movies/Series provider covers.
        raw_art=(None if (self.media_type in ("vod","series") and item.get("_generic_provider_art")) else _image_url(item))
        if self.media_type=="itv":
            # HDD-only page-render hot path. This runs only while a page is being
            # painted, never on every UP/DOWN keypress, so it cannot race Live
            # selection navigation. Cached picons bypass the placeholder entirely.
            cached_live=_cached_live_picon_path(raw_art,self._grid_profile,item)
            if cached_live:
                self._grid_slot_paths[slot]=cached_live
                self._grid_palette_sources[slot]=cached_live
                self._grid_set_local(slot,cached_live)
                return
        if slot not in getattr(self, "_grid_slot_paths", {}):
            self._grid_set_local(slot, asset(placeholder))
        url = _optimized_artwork_url(self._grid_absolute_url(raw_art, item), False)
        if self.media_type=="itv":
            try:
                LOG.info("Live picon route channel=%r raw=%r resolved=%r",
                         str((item or {}).get("name") or (item or {}).get("title") or ""),
                         raw_art,url)
            except Exception:
                pass
            if not url:
                try:LOG.warning("Live picon TEMP portal has no URL channel=%r raw=%r",str((item or {}).get("name") or ""),raw_art)
                except Exception:pass
                return
            # Live picons use a dedicated persistent HDD cache. The URL digest is
            # checked locally first; only a missing icon is downloaded.
            live_key=url
            with self._grid_download_lock:
                waiters=self._grid_download_inflight.get(live_key)
                if waiters is not None:
                    waiters.add((generation,slot));return
                self._grid_download_inflight[live_key]=set([(generation,slot)])
            def live_worker():
                acquired=False
                channel=str((item or {}).get("name") or "")
                _live_restart_trace("picon_worker_begin",channel=channel,slot=int(slot),generation=int(generation),url=url)
                try:
                    acquired=self._grid_slots.acquire(True,12)
                    if not acquired or self._grid_closed or getattr(self,"_screen_closed",False):return
                    path=_download_live_portal_temp_picon(url,self._grid_profile,self._grid_client,item=item,timeout=5.5)
                    _live_restart_trace("picon_worker_result",channel=channel,slot=int(slot),generation=int(generation),
                                        ok=bool(path and os.path.isfile(path)),path=path or "")
                    if not path or not os.path.isfile(path):raise ValueError("persistent live picon returned no local file")
                    with self._grid_download_lock:
                        completed=list(self._grid_download_inflight.pop(live_key,set([(generation,slot)])))
                    if not self._grid_closed and not getattr(self,"_screen_closed",False):
                        for waiter_generation,waiter_slot in completed:self._grid_download_jobs.put((waiter_generation,waiter_slot,path))
                except Exception as exc:
                    _live_restart_trace("picon_worker_error",channel=channel,slot=int(slot),generation=int(generation),error=repr(exc))
                    try:LOG.exception("Live picon TEMP portal worker failed channel=%r url=%r error=%s",channel,url,exc)
                    except Exception:pass
                finally:
                    with self._grid_download_lock:self._grid_download_inflight.pop(live_key,None)
                    if acquired:
                        try:self._grid_slots.release()
                        except Exception:pass
                    _live_restart_trace("picon_worker_end",channel=channel,slot=int(slot),generation=int(generation))
            try:
                future=_IMAGE_EXECUTOR.submit(live_worker)
                with self._grid_download_lock:self._grid_download_futures[future]=live_key
                future.add_done_callback(self._grid_download_future_done)
            except Exception:
                with self._grid_download_lock:self._grid_download_inflight.pop(live_key,None)
            return
        if not url or not _artwork_attempt_allowed(url):
            if self.media_type=="itv":
                try:LOG.warning("Live picon unavailable/blocked channel=%r raw=%r resolved=%r",
                                str((item or {}).get("name") or ""),raw_art,url)
                except Exception:pass
            return
        digest = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
        thumb = _thumb_path(digest, self._grid_image_size)
        if _valid_cache_file(thumb):
            if self.media_type=="itv":
                try:LOG.info("Live picon cache hit channel=%r path=%s",str((item or {}).get("name") or ""),thumb)
                except Exception:pass
            self._grid_queue_decode(generation, slot, thumb);return
        original = _find_original_artwork(digest)
        if original:
            self._grid_queue_decode(generation, slot, original);_queue_thumbnail_build(original, thumb, self._grid_image_size);return
        with self._grid_download_lock:
            waiters = self._grid_download_inflight.get(url)
            if waiters is not None:
                waiters.add((generation, slot));return
            self._grid_download_inflight[url] = set([(generation, slot)])
        def worker():
            temp=None;acquired=False;completed_waiters=None;file_lock=None
            try:
                acquired=self._grid_slots.acquire(True,12)
                if not acquired or self._grid_closed or getattr(self,"_screen_closed",False):return
                file_lock=_artwork_file_lock(digest);file_lock.acquire()
                existing=_find_original_artwork(digest)
                if existing:
                    display_path=_build_thumbnail(existing,thumb,self._grid_image_size) or existing
                    if self.media_type=="itv":
                        try:LOG.info("Live picon ready channel=%r path=%s",str((item or {}).get("name") or ""),display_path)
                        except Exception:pass
                    with self._grid_download_lock:completed_waiters=list(self._grid_download_inflight.pop(url,set([(generation,slot)])))
                    if not self._grid_closed and not getattr(self,"_screen_closed",False):
                        for waiter_generation,waiter_slot in completed_waiters:self._grid_download_jobs.put((waiter_generation,waiter_slot,display_path))
                    return
                # Live picons must use the exact same trusted provider-artwork
                # pipeline as working Stalker portal images.  Previous builds
                # duplicated the HTTP downloader here with tighter limits and
                # subtly different format handling, so a URL that worked via the
                # portal path could still fail silently in the Premium Live grid.
                if self.media_type=="itv":
                    path=_download_portal_artwork(raw_art,self._grid_profile,self._grid_client,False,5.5,item=item)
                    if not path or not os.path.isfile(path):
                        raise ValueError("shared provider-artwork pipeline returned no picon")
                    display_path=_build_thumbnail(path,thumb,self._grid_image_size) or path
                    _clear_artwork_failure(url)
                else:
                    # Reuse the exact provider-artwork pipeline used elsewhere.
                    # This handles headers/redirects/formats consistently and avoids
                    # a second slower downloader implementation in the grid.
                    path=_download_portal_artwork(raw_art,self._grid_profile,self._grid_client,False,2.2,item=item)
                    if not path or not os.path.isfile(path):
                        raise ValueError("provider artwork pipeline returned no poster")
                    display_path=(_build_cover_thumbnail(path,thumb,self._grid_image_size)
                                  if getattr(self,"_poster_cover_mode",False)
                                  else _build_thumbnail(path,thumb,self._grid_image_size)) or path
                    _clear_artwork_failure(url)
                with self._grid_download_lock:completed_waiters=list(self._grid_download_inflight.pop(url,set([(generation,slot)])))
                if not self._grid_closed and not getattr(self,"_screen_closed",False):
                    for waiter_generation,waiter_slot in completed_waiters:self._grid_download_jobs.put((waiter_generation,waiter_slot,display_path))
            except Exception as exc:
                if acquired:_record_artwork_failure(url)
                if self.media_type=="itv":
                    LOG.exception("Live picon download failed channel=%r raw=%r resolved=%r error=%s",
                                  str((item or {}).get("name") or ""),_image_url(item),url,exc)
                else:
                    LOG.exception("grid artwork download failed: %s",url)
            finally:
                with self._grid_download_lock:self._grid_download_inflight.pop(url,None)
                if file_lock is not None:
                    try:file_lock.release()
                    except Exception as exc:optional_failure("ui.grid_file_lock_release",exc)
                if acquired:
                    try:self._grid_slots.release()
                    except Exception as exc:optional_failure("ui.grid_slot_release",exc)
                if temp:
                    try:
                        if _persistent_write_ok(temp): os.unlink(temp)
                    except OSError:pass
        try:
            future=_IMAGE_EXECUTOR.submit(worker)
            with self._grid_download_lock:self._grid_download_futures[future]=url
            future.add_done_callback(self._grid_download_future_done)
        except Exception:
            with self._grid_download_lock:self._grid_download_inflight.pop(url,None)

    def _grid_drain_art_jobs(self, limit=None):
        count=0
        while True:
            if limit is not None and count>=max(1,int(limit)):
                break
            try:
                generation, slot, path = self._grid_download_jobs.get_nowait()
            except queue.Empty:
                break
            if generation != self._grid_generation:
                continue
            self._grid_queue_decode(generation, slot, path)
            count+=1
        self._grid_start_decode()

    def _grid_start_decode(self):
        if self._grid_closed or self._grid_decode_busy:
            return
        while self._grid_decode_queue:
            generation, path = self._grid_decode_queue.pop(0)
            self._grid_queued.discard(path)
            if generation != self._grid_generation:
                continue
            self._grid_decode_busy = True
            self._grid_decode_active = (generation, path)
            try:
                result = self._grid_picload.startDecode(path)
                if result not in (None, 0):
                    self._grid_decode_busy = False
                    self._grid_decode_active = None
                    continue
                return
            except Exception:
                self._grid_decode_busy = False
                self._grid_decode_active = None

    def _grid_picture_ready(self, *args):
        active = self._grid_decode_active
        try:
            if active:
                _generation, path = active
                ptr = self._grid_picload.getData()
                if ptr is not None:
                    # Cache a successful decode even if the user changed page
                    # while ePicLoad was working. New-generation waiters for the
                    # same artwork can then be fulfilled immediately.
                    self._grid_cache_put(path, ptr)
                    waiting = list(self._grid_waiting.pop(path, set()))
                    for item_generation, slot in waiting:
                        if item_generation == self._grid_generation:
                            self._grid_set_ptr(slot, ptr)
                            self._ui_diag_first_real_art(path,slot)
                            try:
                                if getattr(self,"media_type",None) in ("vod","series") and hasattr(self,"_grid_is_m3u_source") and self._grid_is_m3u_source():
                                    name=os.path.basename(str(path or "")).lower()
                                    if not ("placeholder" in name or name.startswith(("poster_movie","poster_series"))):
                                        self._grid_visual_locks.setdefault(slot,str(path))
                            except Exception as exc:optional_failure("ui.grid_visual_lock_set",exc)
        except Exception as exc:
            optional_failure("ui", exc)
        self._grid_decode_busy = False
        self._grid_decode_active = None
        self._grid_start_decode()

    def _grid_art_stop(self):
        self._grid_closed = True
        self._grid_generation += 1
        try:
            cancel=getattr(self,"_page_prefetch_cancel",None)
            if cancel is not None:cancel.set()
        except Exception as exc:optional_failure("ui.grid_prefetch_cancel_close",exc)
        self._grid_cancel_queued_downloads()
        self._grid_decode_queue = []
        self._grid_waiting = {}
        self._grid_queued = set()
        self._grid_slot_paths = {}
        self._grid_accent_jobs = queue.Queue()
        self._grid_accent_pending = set()
        self._grid_poster_thumb_pending = set()
        with self._grid_download_lock:
            self._grid_download_inflight.clear()
        self._grid_decode_cache.clear()
        # Full-screen adaptive/background and card chrome used to survive until
        # Python GC. On Broadcom/OpenBH that can retain native Nexus image memory
        # across repeated grid opens even after the Screen is visually gone.
        for _name in ("selection","page_adaptive_bg"):
            _release_pixmap_widget(self,_name)
        for _slot in range(int(getattr(self,"page_size",0) or 0)):
            _release_pixmap_widget(self,"card_chrome%d"%_slot)
        for name in getattr(self, "_grid_slot_names", []):
            try:
                widget = self[name]
                if widget.instance is not None:
                    widget.instance.setPixmap(None)
                widget.hide()
            except Exception as exc:
                optional_failure("ui", exc)
        try:
            while True:
                self._grid_download_jobs.get_nowait()
        except queue.Empty:
            pass
        # These queues may contain artwork dictionaries/pixmap paths from workers
        # that completed just before close. Drop them now rather than retaining
        # them until Python eventually collects the whole Screen graph.
        for queue_name in ("_grid_accent_jobs","_grid_mood_jobs","_art_prefetch_jobs","_quality_prefetch_jobs","_folder_artwork_progress","_epg_jobs","_live_chrome_jobs"):
            q=getattr(self,queue_name,None)
            if q is None:continue
            try:
                while True:q.get_nowait()
            except queue.Empty:
                pass
            except Exception as exc:optional_failure("ui.grid_queue_drain",exc)
        try:
            if self._grid_pic_connection is not None:
                self._grid_pic_connection.disconnect()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            callbacks=self._grid_picload.PictureData.get()
            if self._grid_picture_ready in callbacks:
                callbacks.remove(self._grid_picture_ready)
        except Exception as exc:
            optional_failure("ui.grid_picload_callback_remove", exc)
        try:self._grid_picload=None
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        _native_image_pressure_relief(force=True)

