# -*- coding: utf-8 -*-
"""Ultra Stalker safe online updater.

Manifest schema (JSON):
{
  "version": "9.1.1",
  "name": "Ultra Stalker Final V9.1.1",
  "ipk": "https://github.com/K3bOra/-UltraStalker/releases/download/v9.1.0/UltraStalker_Final_V9.1_UPDATE.ipk",
  "sha256": "<64 hex>",
  "notes": "Bug fixes and performance improvements",
  "notes_i18n": {"ar": "...", "el": "..."}
}

The official manifest URL may be compiled into DEFAULT_MANIFEST_URL for public
releases. During staging it can be supplied in api_keys.conf as:
UPDATE_MANIFEST_URL=https://raw.githubusercontent.com/.../update.json
"""
from __future__ import print_function

import hashlib
import json
import os
import re
import subprocess
import threading
import time

try:
    from urllib.request import Request, urlopen
except Exception:  # pragma: no cover - Python 2 is not supported, kept harmless
    Request = None
    urlopen = None

from .version import PLUGIN_VERSION
from . import _
from .localization import current_language

DEFAULT_MANIFEST_URL = "https://raw.githubusercontent.com/K3bOra/-UltraStalker/main/update.json"
OFFICIAL_UPDATE_REPO = "https://github.com/K3bOra/-UltraStalker"
# Native Final V9.1.1 release location for receivers already on this updater.
OFFICIAL_UPDATE_TAG = "v9.1.1"
OFFICIAL_UPDATE_ASSET = "UltraStalker_Final_V9.1.1_UPDATE.ipk"
OFFICIAL_IPK_URL = "%s/releases/download/%s/%s" % (OFFICIAL_UPDATE_REPO, OFFICIAL_UPDATE_TAG, OFFICIAL_UPDATE_ASSET)
# Public V9.1 receivers have this URL pinned in their installed updater. Keep it
# approved as the V9.1 -> V9.1.1 bridge; the public update.json for V9.1.1 must
# point here so existing receivers can accept the package without an intermediary.
V91_BRIDGE_UPDATE_TAG = "v9.1.0"
V91_BRIDGE_UPDATE_ASSET = "UltraStalker_Final_V9.1_UPDATE.ipk"
V91_BRIDGE_IPK_URL = "%s/releases/download/%s/%s" % (OFFICIAL_UPDATE_REPO, V91_BRIDGE_UPDATE_TAG, V91_BRIDGE_UPDATE_ASSET)
# Older bridge retained for receivers that still carry the legacy pinned URL.
LEGACY_UPDATE_TAG = "v10.0.60"
LEGACY_UPDATE_ASSET = "UltraStalker_V7_UPDATE.ipk"
LEGACY_IPK_URL = "%s/releases/download/%s/%s" % (OFFICIAL_UPDATE_REPO, LEGACY_UPDATE_TAG, LEGACY_UPDATE_ASSET)
APPROVED_IPK_URLS = frozenset((OFFICIAL_IPK_URL, V91_BRIDGE_IPK_URL, LEGACY_IPK_URL))
TMP_IPK = "/tmp/UltraStalker_online_update.ipk"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_IPK_BYTES = 64 * 1024 * 1024

V911_RELEASE_NOTES = (
    "• Server + Live search • History up to 100\n"
    "• Aspect Ratio controls in Player\n"
    "• Portal isolation + poster identity lock\n"
    "• Native SubsSupport + SubsSupportPro\n"
    "• Pause while choosing subtitles + session restore\n"
    "• 29-language Auto-Fit + faster Splash"
)
V911_UPGRADE_MARKER = "/etc/enigma2/ultrastalker/.upgrade_v9_1_1_detected"
V911_HIGHLIGHTS_SEEN_MARKER = "/etc/enigma2/ultrastalker/.upgrade_v9_1_1_highlights_seen"


def _localized_notes(version, manifest_notes=""):
    if str(version or "").strip() == "9.1.1":
        return _(V911_RELEASE_NOTES)
    return str(manifest_notes or _("New fixes • Better performance • Improvements"))


def _log(message):
    try:
        from .log import get_logger
        get_logger().info("Updater: %s", message)
    except Exception:
        pass


def _manifest_url():
    # Production updater is intentionally pinned. Staging/api_keys.conf cannot
    # redirect update traffic away from the official Ultra Stalker manifest.
    return DEFAULT_MANIFEST_URL


def configured():
    return bool(_manifest_url())


def _version_tuple(value):
    nums = [int(x) for x in re.findall(r"\d+", str(value or ""))[:4]]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums)


def is_newer(remote, current=None):
    return _version_tuple(remote) > _version_tuple(current or PLUGIN_VERSION)


def _https_url(url):
    text = str(url or "").strip()
    if not text.lower().startswith("https://"):
        raise ValueError("Update source must use HTTPS")
    return text


def _read_url(url, max_bytes, timeout=12):
    if urlopen is None:
        raise RuntimeError("HTTPS client unavailable")
    req = Request(_https_url(url), headers={
        "User-Agent": "UltraStalker/%s" % PLUGIN_VERSION,
        "Accept": "application/json, application/octet-stream, */*",
        "Cache-Control": "no-cache",
    })
    response = urlopen(req, timeout=max(4, int(timeout or 12)))
    try:
        length = response.headers.get("Content-Length")
        if length and int(length) > int(max_bytes):
            raise ValueError("Update payload is too large")
        chunks = []
        total = 0
        while True:
            block = response.read(64 * 1024)
            if not block:
                break
            total += len(block)
            if total > int(max_bytes):
                raise ValueError("Update payload exceeded safety limit")
            chunks.append(block)
        return b"".join(chunks)
    finally:
        try:
            response.close()
        except Exception:
            pass


def check_for_update(timeout=10):
    url = _manifest_url()
    if not url:
        return {"ok": False, "configured": False, "error": "Update source is not configured"}
    try:
        raw = _read_url(url, MAX_MANIFEST_BYTES, timeout=timeout)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid update manifest")
        version = str(data.get("version") or "").strip()
        ipk = _https_url(data.get("ipk"))
        if ipk not in APPROVED_IPK_URLS:
            raise ValueError("Update manifest points to an unapproved package location")
        sha = str(data.get("sha256") or "").strip().lower()
        if not re.match(r"^[0-9a-f]{64}$", sha):
            raise ValueError("Invalid SHA256 in update manifest")
        if not re.match(r"^\d+(?:\.\d+){1,3}$", version):
            raise ValueError("Invalid version in update manifest")
        notes_value = data.get("notes")
        notes_i18n = data.get("notes_i18n")
        if isinstance(notes_i18n, dict):
            try:
                code = str(current_language() or "en").strip().lower()
            except Exception:
                code = "en"
            localized = notes_i18n.get(code) or notes_i18n.get("en")
            if localized not in (None, ""):
                notes_value = localized
        result = {
            "ok": True,
            "configured": True,
            "available": is_newer(version),
            "version": version,
            "current": PLUGIN_VERSION,
            "name": str(data.get("name") or ("Ultra Stalker V%s" % version))[:80],
            "ipk": ipk,
            "sha256": sha,
            "notes": str(notes_value or "New fixes and improvements")[:1200],
            "manifest": url,
        }
        return result
    except Exception as exc:
        _log("check failed: %s" % exc)
        return {"ok": False, "configured": True, "error": str(exc)}


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest().lower()


def download_update(info, progress=None, timeout=20):
    info = dict(info or {})
    url = _https_url(info.get("ipk"))
    expected = str(info.get("sha256") or "").strip().lower()
    if not re.match(r"^[0-9a-f]{64}$", expected):
        raise ValueError("Missing update checksum")
    if urlopen is None:
        raise RuntimeError("HTTPS client unavailable")
    req = Request(url, headers={"User-Agent": "UltraStalker/%s" % PLUGIN_VERSION, "Accept": "application/octet-stream"})
    response = urlopen(req, timeout=max(8, int(timeout or 20)))
    tmp = TMP_IPK + ".part"
    total = 0
    try:
        length = response.headers.get("Content-Length")
        expected_size = int(length) if length and str(length).isdigit() else 0
        if expected_size > MAX_IPK_BYTES:
            raise ValueError("Update package is too large")
        with open(tmp, "wb") as handle:
            while True:
                block = response.read(128 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_IPK_BYTES:
                    raise ValueError("Update package exceeded safety limit")
                handle.write(block)
                if callable(progress):
                    try:
                        pct = int((total * 100) / expected_size) if expected_size else 0
                        progress(max(0, min(99, pct)), total, expected_size)
                    except Exception:
                        pass
            handle.flush()
            os.fsync(handle.fileno())
        actual = _sha256_file(tmp)
        if actual != expected:
            raise ValueError("Downloaded update failed SHA256 verification")
        os.replace(tmp, TMP_IPK)
        if callable(progress):
            try:
                progress(100, total, expected_size)
            except Exception:
                pass
        return TMP_IPK
    finally:
        try:
            response.close()
        except Exception:
            pass
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _repair_legacy_opkg_list():
    """Remove only invalid root ownership entries left by older packages.

    This must run before opkg starts solving the local IPK; a package preinst is
    too late on affected receivers because opkg computes obsolete files first.
    """
    info = "/usr/lib/opkg/info/enigma2-plugin-extensions-ultrastalker.list"
    try:
        if not os.path.isfile(info):
            return
        with open(info, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        cleaned = [line for line in lines if line.strip() not in ("", "/", "./")]
        if cleaned == lines:
            return
        tmp = info + ".ultrastalker-clean"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.writelines(cleaned)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, info)
    except Exception as exc:
        _log("legacy opkg list repair skipped: %s" % exc)


def _run_opkg_install(args):
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.communicate()[0]
    try:
        text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or "")
    except Exception:
        text = str(out or "")
    return int(proc.returncode or 0), text


def install_ipk(path=TMP_IPK):
    path = str(path or TMP_IPK)
    if not (os.path.isfile(path) and os.path.getsize(path) > 1024):
        return {"ok": False, "code": -1, "output": "Downloaded package is missing"}
    _repair_legacy_opkg_list()
    code, text = _run_opkg_install(["opkg", "install", path])
    # Some OpenBH/opkg solver builds report 'No candidates to install' when
    # the same package revision is already registered. A verified local IPK
    # may be safely force-reinstalled in that narrow case.
    if code != 0 and "no candidates to install" in text.lower():
        code2, text2 = _run_opkg_install(["opkg", "install", "--force-reinstall", path])
        text = text + "\n[UltraStalker retry --force-reinstall]\n" + text2
        code = code2
    return {"ok": code == 0, "code": int(code), "output": text[-6000:]}


def _cleanup_update_temp_files():
    for candidate in (TMP_IPK, TMP_IPK + ".part"):
        try:
            if os.path.exists(candidate):
                os.unlink(candidate)
        except OSError:
            pass


def install_update(info, progress=None):
    path = download_update(info, progress=progress)
    result = install_ipk(path)
    result["path"] = path
    # Cleanup only after opkg reports success. Restart is user-controlled from
    # the update screen, so the success UI remains visible and deterministic.
    if result.get("ok"):
        _cleanup_update_temp_files()
    return result


# ---------- Premium Enigma2 update UI ----------

def _asset(name):
    return os.path.join(os.path.dirname(__file__), "assets_fhd", name)


class UpdateNoticeScreen(object):
    """Factory-style screen class imported lazily so updater core stays lightweight."""
    pass


def make_update_screen_class():
    from Screens.Screen import Screen
    from Components.Label import Label
    from Components.ActionMap import ActionMap
    from enigma import eTimer, gFont

    class _UpdateNoticeScreen(Screen):
        skin = """<screen name="UltraStalkerUpdateNotice" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
          <eLabel position="0,0" size="1920,1080" backgroundColor="#A0000000" zPosition="1"/>
          <ePixmap position="525,278" size="870,520" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/update_notice_glass_870x520.png" alphatest="blend" scale="1" zPosition="2"/>
          <ePixmap position="590,318" size="220,70" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/brand_header_full.png" alphatest="blend" scale="1" zPosition="4"/>

          <widget name="title" position="575,332" size="770,48" font="Regular;35" foregroundColor="#ffffff" halign="right" valign="center" transparent="1" zPosition="5"/>
          <widget name="version" position="575,392" size="770,38" font="Regular;24" foregroundColor="#74d8ff" halign="center" valign="center" transparent="1" zPosition="5"/>
          <widget name="notes" position="615,438" size="690,206" font="Regular;24" foregroundColor="#e4eef5" halign="center" valign="center" noWrap="0" transparent="1" zPosition="5"/>
          <widget name="status" position="585,650" size="750,34" font="Regular;19" foregroundColor="#a9c1d0" halign="center" valign="center" transparent="1" zPosition="5"/>

          <ePixmap position="623,710" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_green.png" alphatest="blend" zPosition="3"/>
          <widget name="green" position="653,719" size="250,38" font="Regular;22" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
          <ePixmap position="987,710" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_red.png" alphatest="blend" zPosition="3"/>
          <widget name="red" position="1017,719" size="250,38" font="Regular;22" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
        </screen>"""

        def __init__(self, session, info):
            Screen.__init__(self, session)
            self.info = dict(info or {})
            self._busy = False
            self._finished = False
            self._result = None
            self._restart_ready = False
            self._timer = eTimer()
            self["title"] = Label(_("Ultra Stalker Update"))
            self["version"] = Label(_("Version %s is ready to install") % str(self.info.get("version") or ""))
            self["notes"] = Label(_localized_notes(self.info.get("version"), self.info.get("notes")))
            self["status"] = Label(_("Verified package • Your current version stays safe until installation completes"))
            self["green"] = Label(_("Update"))
            self["red"] = Label(_("Back"))
            self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
                "ok": self._primary,
                "green": self._primary,
                "cancel": self._later,
                "red": self._later,
            }, -1)
            self.onLayoutFinish.append(self._fit_notes)

        def _fit_notes(self):
            try:
                inst = self["notes"].instance
                if inst is None:
                    return
                try:
                    inst.setNoWrap(0)
                except Exception:
                    pass
                for size in range(24, 12, -1):
                    inst.setFont(gFont("Regular", size))
                    try:
                        measured = inst.calculateSize()
                        if int(measured.height()) <= 200:
                            break
                    except Exception:
                        if size <= 17:
                            break
            except Exception as exc:
                _log("update notes fit failed: %s" % exc)

        def _set_status(self, text):
            try:
                self["status"].setText(str(text or ""))
            except Exception:
                pass

        def _later(self):
            if self._busy:
                return
            self.close(False)

        def _primary(self):
            if self._busy:
                return
            if self._restart_ready:
                self.close(True)
                return
            self._busy = True
            self["green"].setText(_("Updating…"))
            self["red"].setText("")
            self._set_status(_("Downloading and verifying the package…"))

            def progress(pct, done, total):
                if total:
                    self._pending_status = _("Downloading update… %d%%") % int(pct or 0)
                else:
                    self._pending_status = _("Downloading update…")

            def worker():
                try:
                    result = install_update(self.info, progress=progress)
                except Exception as exc:
                    result = {"ok": False, "output": str(exc)}
                self._result = result
                self._finished = True

            self._finished = False
            self._pending_status = ""
            threading.Thread(target=worker, name="UltraStalkerUpdater", daemon=True).start()

            def tick():
                try:
                    if self._pending_status:
                        self._set_status(self._pending_status)
                    if not self._finished:
                        self._timer.start(250, True)
                        return
                    result = dict(self._result or {})
                    self._busy = False
                    if result.get("ok"):
                        self._restart_ready = True
                        self["title"].setText(_("Update completed successfully"))
                        self["version"].setText(_("Ultra Stalker V%s installed") % str(self.info.get("version") or ""))
                        self["notes"].setText(_("Update completed successfully"))
                        self._set_status(_("Verified package installed • Temporary update files cleaned"))
                        self["green"].setText(_("Close"))
                        self["red"].setText(_("Close"))
                    else:
                        self._restart_ready = False
                        self["title"].setText(_("Update couldn’t be installed"))
                        self["version"].setText(_("Your current version is unchanged"))
                        self["notes"].setText(_("The update stopped safely before replacing your working version. You can try again now or go back."))
                        self._set_status(str(result.get("output") or _("Please try again later"))[:120])
                        self["green"].setText(_("Retry"))
                        self["red"].setText(_("Back"))
                except Exception:
                    self._busy = False
            try:
                self._timer.timeout.connect(tick)
            except Exception:
                self._timer.callback.append(tick)
            self._timer.start(250, True)

    return _UpdateNoticeScreen


def _mark_v911_highlights_seen():
    try:
        directory = os.path.dirname(V911_HIGHLIGHTS_SEEN_MARKER)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(V911_HIGHLIGHTS_SEEN_MARKER, "w", encoding="utf-8") as handle:
            handle.write("Ultra Stalker Final V9.1.1 highlights seen\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except Exception as exc:
        _log("V9.1.1 highlights marker failed: %s" % exc)
        return False


def make_release_highlights_screen_class():
    from Screens.Screen import Screen
    from Components.Label import Label
    from Components.ActionMap import ActionMap
    from enigma import gFont

    class _ReleaseHighlightsScreen(Screen):
        skin = """<screen name="UltraStalkerReleaseHighlights" position="0,0" size="1920,1080" backgroundColor="#00000000" flags="wfNoBorder">
          <eLabel position="0,0" size="1920,1080" backgroundColor="#A0000000" zPosition="1"/>
          <ePixmap position="525,278" size="870,520" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/update_notice_glass_870x520.png" alphatest="blend" scale="1" zPosition="2"/>
          <ePixmap position="590,318" size="220,70" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/brand_header_full.png" alphatest="blend" scale="1" zPosition="4"/>
          <widget name="title" position="575,332" size="770,48" font="Regular;35" foregroundColor="#ffffff" halign="right" valign="center" transparent="1" zPosition="5"/>
          <widget name="version" position="575,392" size="770,38" font="Regular;24" foregroundColor="#74d8ff" halign="center" valign="center" transparent="1" zPosition="5"/>
          <widget name="notes" position="600,438" size="720,230" font="Regular;21" foregroundColor="#e4eef5" halign="center" valign="center" noWrap="0" transparent="1" zPosition="5"/>
          <ePixmap position="805,710" size="310,58" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/assets_fhd/oa_key_green.png" alphatest="blend" zPosition="3"/>
          <widget name="green" position="835,719" size="250,38" font="Regular;22" foregroundColor="#ffffff" halign="center" valign="center" transparent="1" zPosition="6" shadowColor="#000000" shadowOffset="1,1"/>
        </screen>"""

        def __init__(self, session):
            Screen.__init__(self, session)
            self["title"] = Label(_("Ultra Stalker Update"))
            self["version"] = Label("V9.1.1")
            self["notes"] = Label(_(V911_RELEASE_NOTES))
            self["green"] = Label(_("Continue"))
            self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
                "ok": self._done, "green": self._done, "cancel": self._done, "red": self._done,
            }, -1)
            self.onLayoutFinish.append(self._fit_notes)

        def _fit_notes(self):
            try:
                inst = self["notes"].instance
                if inst is None:
                    return
                try:
                    inst.setNoWrap(0)
                except Exception:
                    pass
                for size in range(21, 12, -1):
                    inst.setFont(gFont("Regular", size))
                    try:
                        measured = inst.calculateSize()
                        if int(measured.height()) <= 224:
                            break
                    except Exception:
                        if size <= 17:
                            break
            except Exception as exc:
                _log("V9.1.1 highlights fit failed: %s" % exc)

        def _done(self):
            _mark_v911_highlights_seen()
            self.close(True)

    return _ReleaseHighlightsScreen


def maybe_show_v911_highlights(session, delay_ms=700):
    """Show the localized V9.1.1 highlights once after an actual upgrade.

    Fresh installs do not get the upgrade marker, and this never resets existing
    onboarding/settings/cache state. Markers are append-only: nothing is deleted.
    """
    if not os.path.isfile(V911_UPGRADE_MARKER) or os.path.isfile(V911_HIGHLIGHTS_SEEN_MARKER):
        return None
    if getattr(session, "_ultrastalker_v911_highlights_scheduled", False):
        return None
    setattr(session, "_ultrastalker_v911_highlights_scheduled", True)
    try:
        from enigma import eTimer
        timer = eTimer()
        def show():
            try:
                timer.stop()
            except Exception:
                pass
            if os.path.isfile(V911_HIGHLIGHTS_SEEN_MARKER):
                return
            try:
                session.open(make_release_highlights_screen_class())
            except Exception as exc:
                _log("V9.1.1 highlights screen failed: %s" % exc)
        try:
            conn = timer.timeout.connect(show)
        except Exception:
            timer.callback.append(show); conn = None
        holder = getattr(session, "_ultrastalker_update_timers", None)
        if not isinstance(holder, list):
            holder = []; setattr(session, "_ultrastalker_update_timers", holder)
        holder.append(timer)
        if conn is not None:
            holder.append(conn)
        timer.start(max(250, int(delay_ms or 700)), True)
        return timer
    except Exception as exc:
        _log("V9.1.1 highlights scheduling failed: %s" % exc)
        return None


def show_update_notice(session, info):
    cls = make_update_screen_class()
    return session.open(cls, info)


def deferred_auto_check(session, delay_ms=12000):
    """Check once after plugin paint. Silent when current/offline/unconfigured."""
    if not configured():
        return None
    try:
        from enigma import eTimer
    except Exception:
        return None
    timer = eTimer()
    state = {"done": False, "result": None}

    def launch_check():
        def worker():
            state["result"] = check_for_update(timeout=8)
            state["done"] = True
        threading.Thread(target=worker, name="UltraStalkerUpdateCheck", daemon=True).start()
        poll.start(300, True)

    poll = eTimer()
    def finish_check():
        if not state["done"]:
            poll.start(300, True)
            return
        info = dict(state.get("result") or {})
        if info.get("ok") and info.get("available"):
            try:
                show_update_notice(session, info)
            except Exception as exc:
                _log("notice failed: %s" % exc)
    try:
        timer.timeout.connect(launch_check)
        poll.timeout.connect(finish_check)
    except Exception:
        timer.callback.append(launch_check)
        poll.callback.append(finish_check)
    holder = getattr(session, "_ultrastalker_update_timers", None)
    if not isinstance(holder, list):
        holder = []
        setattr(session, "_ultrastalker_update_timers", holder)
    holder.extend([timer, poll])
    timer.start(max(1000, int(delay_ms or 12000)), True)
    return timer
