"""Grid artwork mixin extracted from ui.py without changing behavior."""

import threading
import os
import time
from .core.runtime_log import diagnostic_breadcrumb as _ui_diag

# Runtime dependencies are injected by ui.py after shared caches/executors exist.
def configure_grid_artwork(**deps):
    globals().update(deps)

def _live_picon_candidates(item):
    """Return ordered usable Live logo fields without placeholder values.

    Some providers publish a dead stream_icon while a working picon/logo field is
    present on the same row.  The old first-field-only path then painted the
    placeholder forever.  Keep this Live-only and bounded.
    """
    if not isinstance(item,dict):
        return []
    out=[]
    for key in ("stream_icon","picon","logo","logo_url","image","img","pic","cover","cover_url"):
        value=item.get(key)
        values=value if isinstance(value,(list,tuple)) else [value]
        for entry in values:
            text=str(entry or "").strip()
            low=text.casefold()
            if not text or low in ("null","none","[]","{}"):
                continue
            if any(tag in low for tag in ("placeholder","noimage","no_image","default_poster","default-cover")):
                continue
            if text not in out:
                out.append(text)
    return out

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
        self._grid_slot_identities = {}
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
        # Release: every visible poster gets a NORMAL card chrome plus a separate
        # adaptive focus overlay. The card never changes just because it is
        # selected; this removes the redraw pulse seen under the focus frame.
        def worker():
            card=_grid_card_chrome_from_poster(path,selected=False)
            result=_grid_selection_asset_from_poster(path)
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
            if card and slot != getattr(self,"index",-1) and (getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot))==path:
                try:
                    if not hasattr(self,"_grid_card_paths"):self._grid_card_paths={}
                    self._grid_card_paths[slot]=card
                    try:
                        if hasattr(self,"_remember_grid_visual"):self._remember_grid_visual(slot,palette=path,card=card)
                    except Exception as exc:optional_failure("ui.grid_visual_ram_card",exc)
                    name="card_chrome%d"%slot
                    widget=self[name]
                    if widget.instance is not None:widget.instance.setPixmapFromFile(card);widget.show()
                except Exception as exc:optional_failure("ui.grid_adaptive_card_apply",exc)
            if result and slot == getattr(self,"index",-1) and (getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot)) == path:
                try:
                    # Dual-layer focus: the thick base selector below is
                    # never replaced. Adaptive colour swaps only on the overlay,
                    # so an OpenBH blank frame cannot make focus disappear.
                    # The queued result may resolve to the same cached path that
                    # was previously bound. Rebind it anyway: the native Enigma2
                    # surface can be gone even though our path marker still matches.
                    if self["selection_adaptive"].instance is not None:
                        self["selection_adaptive"].instance.setPixmap(None)
                        self["selection_adaptive"].instance.setPixmapFromFile(result)
                    self._grid_selection_adaptive_visual_path=result
                    changed=True
                    self["selection_adaptive"].show()
                    self["selection"].show()
                    self._grid_focus_chrome_slot=slot
                except Exception as exc:optional_failure("ui.grid_adaptive_selector_apply",exc)
        return changed

    def _grid_mood_target(self,path):
        try:
            st=os.stat(str(path));stamp="%s|%s"%(int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size))
            key=hashlib.sha1((str(path)+"|"+stamp+"|grid-page-mood-v8-stable").encode("utf-8","ignore")).hexdigest()
            return os.path.join(THUMB_CACHE_DIR,"gridmood_%s_960x540_v8_stable.jpg"%key)
        except Exception:return ""

    def _grid_apply_current_selector(self, allow_build=False):
        if getattr(self,"media_type",None) not in ("vod","series"):
            return
        slot=getattr(self,"index",0)
        path=getattr(self,"_grid_palette_sources",{}).get(slot) or self._grid_slot_paths.get(slot)

        # Permanent thick base focus: this pixmap is never swapped while browsing.
        try:
            stable=asset(getattr(self,"selection_asset","poster_card_selected_neutral_small.png"))
            if getattr(self,"_grid_selection_visual_path","") != stable:
                self["selection"].instance.setPixmapFromFile(stable)
                self._grid_selection_visual_path=stable
            self["selection"].show()
        except Exception as exc:optional_failure("ui.grid_selector_stable",exc)

        if not path or not os.path.isfile(path):
            try:self["selection_adaptive"].hide()
            except Exception:pass
            return

        # Adaptive colour lives on a second overlay. Cached assets switch
        # immediately during navigation; a cache miss leaves the thick base
        # visible and queues the overlay without ever blanking focus.
        try:
            stamp=str(int(os.path.getmtime(path)))
            sel_key=hashlib.sha1((path+"|"+stamp+"|us-card-select-v12-thickoutside-222x398").encode("utf-8","ignore")).hexdigest()
            ready=_GRID_ACCENT_CACHE.get(sel_key) or os.path.join(THUMB_CACHE_DIR,"selcard_%s_222x398_v12.png"%sel_key)
            if ready and _valid_cache_file(ready,ttl=0):
                # Rebind cached adaptive focus even when the source marker is
                # unchanged. OpenBH can drop the native pixmap surface across
                # hide/show or child transitions while our Python-side path
                # still points at the same cached asset.
                if self["selection_adaptive"].instance is not None:
                    self["selection_adaptive"].instance.setPixmap(None)
                    self["selection_adaptive"].instance.setPixmapFromFile(ready)
                self._grid_selection_adaptive_visual_path=ready
                self["selection_adaptive"].show()
            else:
                self["selection_adaptive"].hide()
                if allow_build:
                    self._grid_schedule_adaptive_selector(self._grid_generation,slot,path)
        except Exception as exc:optional_failure("ui.grid_selector_adaptive_overlay",exc)

        try:
            mood=self._grid_mood_target(path)
            if mood and _valid_cache_file(mood,ttl=0):
                # Rebind even when the source did not change. OpenBH can drop the
                # full-screen pixmap across page/child transitions while retaining
                # our Python-side source marker.
                self["page_adaptive_bg"].instance.setPixmap(None);self["page_adaptive_bg"].instance.setPixmapFromFile(mood)
                self["page_adaptive_bg"].show();self._grid_mood_source=path
            elif allow_build:
                self._grid_schedule_page_mood(path)
        except Exception as exc:optional_failure("ui.grid_mood_cache_only",exc)

    def _grid_schedule_page_mood(self,path):
        if getattr(self,"media_type",None) not in ("vod","series") or not path or not os.path.isfile(path):return
        path=str(path)
        try:
            target=self._grid_mood_target(path)
            if target and _valid_cache_file(target,ttl=0):
                self["page_adaptive_bg"].instance.setPixmap(None);self["page_adaptive_bg"].instance.setPixmapFromFile(target);self["page_adaptive_bg"].show();self._grid_mood_source=path;return
        except Exception as exc:optional_failure("ui.grid_mood_cache",exc)
        if self._grid_mood_pending==path:return
        self._grid_mood_pending=path;self._grid_mood_token+=1;token=self._grid_mood_token
        def worker():
            out=_grid_page_mood_from_poster(path)
            try:self._grid_mood_jobs.put((token,path,out))
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:_GRID_MOOD_EXECUTOR.submit(worker)
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
            if getattr(self,"_poster_small_fill",False):
                mode_tag="us-card-smallfill-v1"
            else:
                mode_tag="us-card-cover-v4" if getattr(self,"_poster_cover_mode",False) else "us-card-fit-v1"
            raw="%s|%s|%s|%sx%s|%s"%(source_path,int(st.st_mtime),int(st.st_size),int(self._grid_image_size[0]),int(self._grid_image_size[1]),mode_tag)
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
        # Test67: never hold first paint behind the single Pillow lane. Decode the
        # already-downloaded original immediately; build the small persistent thumb
        # silently for the next open.
        self._grid_queue_decode(generation,slot,source_path)
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
                out=(_build_cover_thumbnail(source_path,thumb,self._grid_image_size) if (getattr(self,"_poster_small_fill",False) or getattr(self,"_poster_cover_mode",False)) else _build_thumbnail(source_path,thumb,self._grid_image_size))
            finally:
                try: pending.discard(token)
                except Exception as exc: optional_failure("ui.grid_pending_cleanup", exc)
            # Original was already queued for first paint above. Do not swap it
            # again when the thumb finishes; that second decode caused visible
            # one-by-one repaint/flicker. The thumb is for future opens only.
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
            prepared_seen=(slot in prepared_map)
            if prepared_seen:
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
                # No local poster yet. Paint the placeholder now, but DO NOT
                # return: the visible-page direct provider path below is allowed
                # to fetch the catalogue poster automatically. This is display
                # hydration only; it never invokes Xtream enrichment or TMDB.
                self._grid_set_local(slot,asset(placeholder))
            # Cold fallback exists only for legacy/non-prepared paths.
            if not prepared_seen:
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
                        card=str((bundle or {}).get("grid_card") or "")
                        if card and os.path.isfile(card):
                            self._grid_card_paths[slot]=card
                            try:
                                self["card_chrome%d"%slot].instance.setPixmapFromFile(card);self["card_chrome%d"%slot].show()
                            except Exception as exc:optional_failure("ui.silent_guard",exc)
                        return
                except Exception as exc:optional_failure("ui.grid_visual_bundle",exc)
                try:
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
            # R231: do NOT return here.  Cold VOD/Series cards fall through to
            # the display-only provider first-paint hedge below.  The canonical
            # ArtworkV2/TMDb page hydrator still starts after first paint, but it
            # now runs on its own executor lane.  Whichever source completes first
            # can paint; canonical artwork keeps final authority.
        # Visible provider first-paint hedge.  Stalker list artwork is kept in the
        # private _visible_provider_art_url field while Xtream retains its normal
        # provider cover. Generic/repeated placeholders are explicitly rejected.
        if self.media_type in ("vod","series"):
            raw_art=(None if item.get("_generic_provider_art") else (item.get("_visible_provider_art_url") or _image_url(item)))
            live_candidates=[]
        else:
            live_candidates=_live_picon_candidates(item)
            raw_art=(live_candidates[0] if live_candidates else _image_url(item))
        if self.media_type=="itv":
            # HDD-first across all provider logo fields. A stale stream_icon must
            # not hide a valid picon/logo already present on the same catalogue row.
            for _raw_live in (live_candidates or [raw_art]):
                cached_live=_cached_live_picon_path(_raw_live,self._grid_profile,item)
                if cached_live:
                    self._grid_slot_paths[slot]=cached_live
                    self._grid_palette_sources[slot]=cached_live
                    self._grid_set_local(slot,cached_live)
                    return
        if slot not in getattr(self, "_grid_slot_paths", {}):
            self._grid_set_local(slot, asset(placeholder))
        url = _optimized_artwork_url(self._grid_absolute_url(raw_art, item), False)
        live_urls=[]
        if self.media_type=="itv":
            for _raw_live in (live_candidates or [raw_art]):
                try:
                    _resolved=_optimized_artwork_url(self._grid_absolute_url(_raw_live,item),False)
                except Exception:
                    _resolved=None
                if _resolved and _resolved not in live_urls:
                    live_urls.append(_resolved)
            if live_urls:
                url=live_urls[0]
            try:
                LOG.info("Live picon route channel=%r raw=%r resolved=%r candidates=%d",
                         str((item or {}).get("name") or (item or {}).get("title") or ""),
                         raw_art,url,len(live_urls))
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
                    path=None
                    # At most three catalogue-provided candidates, and only when
                    # the previous one failed. Successful cache/download remains one request.
                    for _candidate_url in (live_urls or [url])[:3]:
                        path=_download_live_portal_temp_picon(_candidate_url,self._grid_profile,self._grid_client,item=item,timeout=5.5)
                        if path and os.path.isfile(path):
                            break
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
            # VOD/Series: cached provider bytes are a normal first-paint source.
            original = _find_original_artwork(digest)
            if original and os.path.isfile(original):
                self._grid_use_persistent_poster(generation,slot,original);return
            self._grid_queue_decode(generation,slot,thumb);return
        original = _find_original_artwork(digest)
        if original:
            if self.media_type in ("vod","series"):
                self._grid_use_persistent_poster(generation,slot,original);return
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
                    # Test67: direct catalogue/provider poster is allowed for the
                    # VISIBLE page only. This is not Poster Rescue: no Xtream rich
                    # info, no TMDB, no backdrop, no adjacent-page walk.
                    path=_download_portal_artwork(raw_art,self._grid_profile,self._grid_client,False,2.5,item=item)
                    if not path or not os.path.isfile(path):
                        raise ValueError("provider poster returned no local file")
                    display_path=path
                    _clear_artwork_failure(url)
                with self._grid_download_lock:completed_waiters=list(self._grid_download_inflight.pop(url,set([(generation,slot)])))
                if not self._grid_closed and not getattr(self,"_screen_closed",False):
                    for waiter_generation,waiter_slot in completed_waiters:
                        self._grid_download_jobs.put((waiter_generation,waiter_slot,display_path,("provider" if self.media_type in ("vod","series") else "plain")))
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
            executor=(_VISIBLE_PROVIDER_POSTER_EXECUTOR if self.media_type in ("vod","series") else _IMAGE_EXECUTOR)
            future=executor.submit(worker)
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
                job=self._grid_download_jobs.get_nowait()
                generation,slot,path=job[0],job[1],job[2]
                kind=(job[3] if len(job)>3 else "plain")
            except queue.Empty:
                break
            if generation != self._grid_generation:
                continue
            if kind=="provider" and getattr(self,"media_type",None) in ("vod","series") and path and os.path.isfile(path):
                # R231 canonical-wins gate. Provider art is a first-paint hedge,
                # never a late downgrade. If canonical ArtworkV2 already painted
                # this slot, keep it and merely retain the downloaded provider file
                # in the shared source cache for future cold opens.
                canonical=(getattr(self,"_grid_canonical_poster_slots",{}) or {}).get(slot)
                if canonical and os.path.isfile(str(canonical)):
                    count+=1
                    continue
                self._grid_palette_sources[slot]=path
                try:self._remember_grid_visual(slot,poster=path,palette=path,provider_locked=True)
                except Exception:pass
                self._grid_use_persistent_poster(generation,slot,path)
            else:
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
        self._grid_slot_identities = {}
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

