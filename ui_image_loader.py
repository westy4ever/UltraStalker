"""Image loading mixin extracted from ui.py without changing behavior."""

import hashlib
import os
import queue
import threading
import urllib.parse
import urllib.request

from enigma import ePicLoad, eTimer

from .log import optional_failure
from .storage import load_settings

LOG = None

IMAGE_CACHE_DIR = ""
_ArtworkCancelled = Exception
_IMAGE_EXECUTOR = None
_artwork_attempt_allowed = None
_artwork_file_lock = None
_build_thumbnail = None
_cache_artwork_path = None
_clear_artwork_failure = None
_find_original_artwork = None
_image_url = None
_optimized_artwork_url = None
_persistent_write_ok = None
_persistent_write_require = None
_queue_thumbnail_build = None
_record_artwork_failure = None
_safe_image_headers = None
_safe_image_opener = None
_thumb_path = None
_valid_cache_file = None
asset = None

def configure_image_loader(
    log,
    image_cache_dir,
    artwork_cancelled,
    image_executor,
    artwork_attempt_allowed,
    artwork_file_lock,
    build_thumbnail,
    cache_artwork_path,
    clear_artwork_failure,
    find_original_artwork,
    image_url,
    optimized_artwork_url,
    persistent_write_ok,
    persistent_write_require,
    queue_thumbnail_build,
    record_artwork_failure,
    safe_image_headers,
    safe_image_opener,
    thumb_path,
    valid_cache_file,
    asset_func,
):
    global LOG, IMAGE_CACHE_DIR, _ArtworkCancelled, _IMAGE_EXECUTOR
    global _artwork_attempt_allowed, _artwork_file_lock, _build_thumbnail, _cache_artwork_path
    global _clear_artwork_failure, _find_original_artwork, _image_url, _optimized_artwork_url
    global _persistent_write_ok, _persistent_write_require, _queue_thumbnail_build
    global _record_artwork_failure, _safe_image_headers, _safe_image_opener, _thumb_path
    global _valid_cache_file, asset
    LOG = log
    IMAGE_CACHE_DIR = image_cache_dir
    _ArtworkCancelled = artwork_cancelled
    _IMAGE_EXECUTOR = image_executor
    _artwork_attempt_allowed = artwork_attempt_allowed
    _artwork_file_lock = artwork_file_lock
    _build_thumbnail = build_thumbnail
    _cache_artwork_path = cache_artwork_path
    _clear_artwork_failure = clear_artwork_failure
    _find_original_artwork = find_original_artwork
    _image_url = image_url
    _optimized_artwork_url = optimized_artwork_url
    _persistent_write_ok = persistent_write_ok
    _persistent_write_require = persistent_write_require
    _queue_thumbnail_build = queue_thumbnail_build
    _record_artwork_failure = record_artwork_failure
    _safe_image_headers = safe_image_headers
    _safe_image_opener = safe_image_opener
    _thumb_path = thumb_path
    _valid_cache_file = valid_cache_file
    asset = asset_func

class ImageLoaderMixin:
    def _image_init(self, widget_name, size, profile, client):
        self._image_widget = widget_name
        self._image_size = size
        self._image_profile = profile
        self._image_client = client
        self._image_jobs = queue.Queue()
        self._image_token = 0
        self._image_closed = False
        self._image_slots = threading.BoundedSemaphore(6)
        self._picload = ePicLoad()
        self._pic_connection = None
        self._image_pending_path = None
        self._image_active_path = None
        self._image_displayed_path = None
        self._image_decode_busy = False
        self._image_decode_timer = eTimer()
        self._image_decode_timer_conn = None
        try:
            self._image_decode_timer_conn = self._image_decode_timer.timeout.connect(self._decode_pending_picture)
        except Exception:
            self._image_decode_timer.callback.append(self._decode_pending_picture)
        try:
            self._picload.PictureData.get().append(self._picture_ready)
        except Exception:
            try:
                self._pic_connection = self._picload.PictureData.connect(self._picture_ready)
            except Exception as exc:
                optional_failure("ui", exc)
    def _image_layout_ready(self):
        try:
            self._picload.setPara((self._image_size[0], self._image_size[1], 1, 1, False, 1, "#000000"))
        except Exception as exc:
            optional_failure("ui", exc)
    def _decode_picture(self, path):
        if self._image_closed or getattr(self, "_screen_closed", False):
            return
        path = str(path or "").strip()
        if not path:
            return
        # Rapid list navigation used to decode the same placeholder repeatedly.
        # OpenBH then reported gAccel allocation failures. Debounce and skip
        # duplicate requests while keeping the latest requested artwork.
        if path in (self._image_displayed_path, self._image_pending_path, self._image_active_path):
            return
        self._image_pending_path = path
        try:
            self._image_decode_timer.stop()
            self._image_decode_timer.start(60, True)
        except Exception:
            self._decode_pending_picture()

    def _decode_pending_picture(self):
        if self._image_closed or getattr(self, "_screen_closed", False):
            return
        if self._image_decode_busy:
            try:
                self._image_decode_timer.start(45, True)
            except Exception as exc:
                optional_failure("ui", exc)
            return
        path = self._image_pending_path
        self._image_pending_path = None
        if not path or path == self._image_displayed_path:
            return
        self._image_active_path = path
        self._image_decode_busy = True
        try:
            result = self._picload.startDecode(path)
            if result not in (None, 0):
                # Do not spin forever when a receiver rejects a very large JPEG.
                # Try the native pixmap loader once; if that also fails, one delayed
                # retry remains available for transient decoder contention.
                displayed = False
                try:
                    widget = self[self._image_widget]
                    if widget.instance is not None:
                        widget.instance.setPixmapFromFile(path)
                        widget.show()
                        self._image_displayed_path = path
                        displayed = True
                except Exception:
                    displayed = False
                self._image_decode_busy = False
                self._image_active_path = None
                if not displayed:
                    self._image_pending_path = path
                    self._image_decode_timer.start(180, True)
        except Exception:
            self._image_decode_busy = False
            self._image_active_path = None
            try:
                if self[self._image_widget].instance is not None:
                    self[self._image_widget].instance.setPixmapFromFile(path)
                    self[self._image_widget].show()
                    self._image_displayed_path = path
            except Exception as exc:
                optional_failure("ui", exc)
    def _picture_ready(self, *args):
        if self._image_closed or getattr(self, "_screen_closed", False):
            return
        try:
            ptr = self._picload.getData()
            if ptr is not None and self[self._image_widget].instance is not None:
                self[self._image_widget].instance.setPixmap(ptr)
                self[self._image_widget].show()
                self._image_displayed_path = self._image_active_path
        except Exception as exc:
            optional_failure("ui", exc)
        finally:
            self._image_decode_busy = False
            self._image_active_path = None
            if self._image_pending_path and self._image_pending_path != self._image_displayed_path:
                try:
                    self._image_decode_timer.start(45, True)
                except Exception as exc:
                    optional_failure("ui", exc)
    def _image_suspend(self):
        """Release the currently decoded native image while this Screen is hidden.

        Enigma2 keeps parent Screens alive underneath child Screens.  On Broadcom
        receivers that means their gPixmap/Nexus allocations also stay alive
        unless we explicitly drop them.  This is intentionally lighter than
        _image_stop(): the decoder/callback wiring remains reusable when the
        Screen becomes visible again.
        """
        try:self._image_token += 1
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        self._image_pending_path = None
        self._image_active_path = None
        self._image_displayed_path = None
        self._image_decode_busy = False
        try:self._image_decode_timer.stop()
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        try:
            widget=self[self._image_widget]
            if widget.instance is not None:widget.instance.setPixmap(None)
            try:widget.hide()
            except Exception as exc:optional_failure("ui.silent_guard",exc)
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _image_stop(self):
        self._image_closed = True
        self._image_token += 1
        self._image_pending_path = None
        self._image_active_path = None
        try:
            self._image_decode_timer.stop()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._image_decode_timer_conn is not None:
                self._image_decode_timer_conn.disconnect()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._decode_pending_picture in self._image_decode_timer.callback:
                self._image_decode_timer.callback.remove(self._decode_pending_picture)
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._pic_connection is not None:
                self._pic_connection.disconnect()
        except Exception as exc:
            optional_failure("ui.picload_disconnect", exc)
        try:
            callbacks=self._picload.PictureData.get()
            if self._picture_ready in callbacks:
                callbacks.remove(self._picture_ready)
        except Exception as exc:
            optional_failure("ui.picload_callback_remove", exc)
        try:
            while True:
                self._image_jobs.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:
            optional_failure("ui.image_queue_drain", exc)
        self._pic_connection = None
        self._image_decode_timer_conn = None
        # Release the native decoder wrapper after its PictureData callback has
        # been detached. This is lifecycle-only and does not alter rendered art.
        try:self._picload=None
        except Exception as exc:optional_failure("ui.picload_release",exc)
        # Release the last decoded pixmap immediately instead of waiting for the
        # Screen/component object graph to be garbage-collected. This matters on
        # long sessions that repeatedly open/close poster-heavy detail screens.
        try:
            widget=self[self._image_widget]
            if widget.instance is not None:widget.instance.setPixmap(None)
        except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _absolute_image_url(self, value):
        if not value:
            return None
        if value.startswith("//"):
            scheme = urllib.parse.urlsplit(self._image_profile.get("portal", "http://")).scheme or "http"
            return scheme + ":" + value
        if value.startswith(("http://", "https://")):
            return value
        return urllib.parse.urljoin(self._image_profile.get("portal", "").rstrip("/") + "/", value.lstrip("/"))

    def _load_item_image(self, item, placeholder):
        if not load_settings().get("load_images", True):
            self._decode_picture(asset(placeholder))
            return
        url = _optimized_artwork_url(self._absolute_image_url(_image_url(item)), False)
        if not url:
            self._decode_picture(asset(placeholder))
            return
        self._image_token += 1
        token = self._image_token
        digest = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
        thumb = _thumb_path(digest, self._image_size)
        if _valid_cache_file(thumb):
            self._decode_picture(thumb)
            return
        original = _find_original_artwork(digest)
        if original:
            self._decode_picture(original)
            _queue_thumbnail_build(original, thumb, self._image_size)
            return
        self._decode_picture(asset(placeholder))
        if not _artwork_attempt_allowed(url):
            return

        def worker():
            temp = None
            acquired = False
            try:
                if token != self._image_token or self._image_closed or getattr(self, "_screen_closed", False):
                    return
                # Executor already provides queueing; wait briefly for an image slot
                # instead of silently dropping artwork when five requests are active.
                acquired = self._image_slots.acquire(True, 20)
                if not acquired or token != self._image_token or self._image_closed or getattr(self, "_screen_closed", False):
                    return
                lock = _artwork_file_lock(digest)
                with lock:
                    # Another screen may have completed the same URL while we waited.
                    original = _find_original_artwork(digest)
                    if original:
                        display_path = _build_thumbnail(original, thumb, self._image_size) or original
                        if not self._image_closed and not getattr(self, "_screen_closed", False):
                            self._image_jobs.put((token, display_path))
                        return
                    headers = _safe_image_headers(url, self._image_profile, self._image_client)
                    req = urllib.request.Request(url, headers=headers)
                    image_opener = _safe_image_opener(url, self._image_profile)
                    temp = os.path.join(IMAGE_CACHE_DIR, "%s.download.%d.%d" % (digest, os.getpid(), threading.get_ident()))
                    total=0;head=b""
                    _persistent_write_require(temp)
                    with image_opener.open(req, timeout=3.5) as response, open(temp,"wb") as handle:
                        content_type=(response.headers.get("Content-Type") or "").lower()
                        while True:
                            if token != self._image_token or self._image_closed or getattr(self, "_screen_closed", False):
                                raise _ArtworkCancelled()
                            chunk=response.read(64*1024)
                            if not chunk:break
                            if not head:head=chunk[:16]
                            total+=len(chunk)
                            if total>3*1024*1024:raise ValueError("artwork exceeds 3 MB")
                            handle.write(chunk)
                        handle.flush()
                    if total<=100:raise ValueError("empty artwork")
                    if head.startswith(b"\x89PNG\r\n\x1a\n"):ext=".png"
                    elif head.startswith(b"\xff\xd8\xff"):ext=".jpg"
                    elif head[:4]==b"RIFF" and head[8:12]==b"WEBP":ext=".webp"
                    elif "png" in content_type:ext=".png"
                    elif "jpeg" in content_type or "jpg" in content_type:ext=".jpg"
                    elif "webp" in content_type:ext=".webp"
                    else:raise ValueError("unsupported artwork")
                    path=os.path.join(IMAGE_CACHE_DIR,digest+ext)
                    if not _persistent_write_ok(path): return None
                    os.replace(temp,path);temp=None;_cache_artwork_path(digest,path)
                    display_path = _build_thumbnail(path, thumb, self._image_size) or path
                    _clear_artwork_failure(url)
                    if not self._image_closed and not getattr(self, "_screen_closed", False):
                        self._image_jobs.put((token, display_path))
            except _ArtworkCancelled:
                return
            except Exception:
                _record_artwork_failure(url)
                LOG.exception("image download failed: %s", url)
            finally:
                if acquired:
                    try: self._image_slots.release()
                    except Exception as exc: optional_failure("ui", exc)
                if temp:
                    try:
                        if _persistent_write_ok(temp): os.unlink(temp)
                    except Exception as exc: optional_failure("ui", exc)
        try:
            _IMAGE_EXECUTOR.submit(worker)
        except Exception:
            LOG.exception("unable to queue image download")

    def _drain_image_jobs(self):
        while True:
            try:
                token, path = self._image_jobs.get_nowait()
            except queue.Empty:
                break
            if token == self._image_token:
                self._decode_picture(path)

