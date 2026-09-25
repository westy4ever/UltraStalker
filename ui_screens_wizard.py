"""First-run wizard extracted from ui.py without changing behavior."""

import threading
import os
import time

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.InputBox import InputBox
from Components.ActionMap import ActionMap
from Components.Label import Label

from . import _
from .version import PLUGIN_VERSION
from .storage import load_profiles, save_profiles, enable_profile, load_settings, save_settings
from .core.portal_security import requires_http_consent, HTTP_WARNING, http_warning_for, mark_http_consent
from .log import optional_failure
from .ui_async import AsyncScreenMixin

PortalHomeScreen = None
PortalListScreen = None
WIZARD_DONE_FILE = ""
WIZARD_SKIN = ""
_client_from_profile = None
_friendly_portal_error = None
_looks_like_m3u_url = None
_probe_m3u_url = None
_validate_profile_client = None

def configure_wizard_screen(
    portal_home_screen,
    portal_list_screen,
    wizard_done_file,
    wizard_skin,
    client_from_profile,
    friendly_portal_error,
    looks_like_m3u_url,
    probe_m3u_url,
    validate_profile_client,
):
    global PortalHomeScreen, PortalListScreen, WIZARD_DONE_FILE, WIZARD_SKIN
    global _client_from_profile, _friendly_portal_error, _looks_like_m3u_url, _probe_m3u_url, _validate_profile_client
    PortalHomeScreen = portal_home_screen
    PortalListScreen = portal_list_screen
    WIZARD_DONE_FILE = wizard_done_file
    WIZARD_SKIN = wizard_skin
    # class body was evaluated before ui.py injected the scaled skin.
    FirstRunWizardScreen.skin = wizard_skin
    _client_from_profile = client_from_profile
    _friendly_portal_error = friendly_portal_error
    _looks_like_m3u_url = looks_like_m3u_url
    _probe_m3u_url = probe_m3u_url
    _validate_profile_client = validate_profile_client

class FirstRunWizardScreen(Screen, AsyncScreenMixin):
    skin = WIZARD_SKIN
    def __init__(self, session):
        Screen.__init__(self, session)
        self._async_init(); self.onClose.append(self._stop_async)
        self.step = "welcome"; self._new_portal = ""; self._new_mac = ""; self._created_profile = None
        self["title"] = Label(_("WELCOME"))
        self["body"] = Label("")
        self["green"] = Label(_("Start setup")); self["blue"] = Label(_("Skip wizard"))
        self["actions"] = ActionMap(["OkCancelActions","ColorActions"], {
            "ok":self.next, "green":self.next, "blue":self.skip, "cancel":self.skip,
        }, -1)
        self.onLayoutFinish.append(self._render)

    def _render(self):
        if self.step == "welcome":
            body=_("1 / 4\n\nSet up your first portal in a few steps.\nYou will enter the Portal URL and MAC address, then Ultra Stalker will test the connection before saving it.")
            green=_("Start setup")
        elif self.step == "portal":
            body=_("2 / 4\n\nEnter the Portal URL. HTTPS is recommended whenever your provider supports it.")
            green=_("Enter URL")
        elif self.step == "mac":
            body=_("3 / 4\n\nEnter the MAG/STB MAC address supplied for this subscription.\nExample: 00:1A:79:XX:XX:XX")
            green=_("Enter MAC")
        elif self.step == "testing":
            if _looks_like_m3u_url(self._new_portal):
                body=_("4 / 4\n\nTesting the M3U playlist…\nChecking network access and playlist content.")
            else:
                body=_("4 / 4\n\nTesting the portal connection…\nChecking network access, MAG authentication and account response.")
            green=_("Testing…")
        elif self.step == "failed":
            body=_("Connection test failed.\n\n%s\n\nPress GREEN to edit the Portal URL and try again, or BLUE to finish setup later from Portal Manager.") % getattr(self,"_last_error",_("Unknown error"))
            green=_("Retry setup")
        else:
            body=_("Setup complete. Opening your portal…")
            green=_("Open portal")
        self["body"].setText(body); self["green"].setText(green)

    def next(self):
        if self._busy or self.step == "testing": return
        if self.step in ("welcome","portal","failed"):
            self.step="portal"; self._render()
            self.session.openWithCallback(self._got_portal,InputBox,title=_("Portal URL (HTTPS preferred)"),text=self._new_portal or "https://",maxSize=250)
        elif self.step == "mac":
            self.session.openWithCallback(self._got_mac,InputBox,title=_("MAC address"),text=self._new_mac or "00:1A:79:",maxSize=17)
        elif self.step == "success":
            self._open_after_finish(self._created_profile)

    def _got_portal(self, portal):
        value=str(portal or "").strip()
        if not value:
            self.step="portal"; self._render(); return
        self._new_portal=value
        if requires_http_consent(value):
            self.session.openWithCallback(self._wizard_http_confirmed,MessageBox,http_warning_for(value),MessageBox.TYPE_YESNO)
            return
        self._route_wizard_source()

    def _wizard_http_confirmed(self,answer):
        if not answer:
            self.step="portal"; self._render(); return
        self._route_wizard_source()

    def _route_wizard_source(self):
        raw=str(self._new_portal or "").strip()
        if _looks_like_m3u_url(raw):
            self._begin_m3u_test()
            return
        self.step="testing"; self._render()
        def work(handle):
            return _probe_m3u_url(raw,timeout=min(int(load_settings().get("timeout",10) or 10),8),
                                  cancel_event=getattr(handle,"cancel_event",None))
        def ok(is_m3u):
            if is_m3u:self._begin_m3u_test()
            else:
                self.step="mac";self._render()
                self.session.openWithCallback(self._got_mac,InputBox,title=_("MAC address"),text=self._new_mac or "00:1A:79:",maxSize=17)
        def failed(exc):
            self.step="mac";self._render()
            self.session.openWithCallback(self._got_mac,InputBox,title=_("MAC address"),text=self._new_mac or "00:1A:79:",maxSize=17)
        if not self._run_async(work,ok,failed):
            failed(RuntimeError("source probe unavailable"))

    def _begin_m3u_test(self):
        profile={"portal":self._new_portal,"mac":"","source_type":"m3u","state":"M3U • READY",
                 "account_state":"M3U PLAYLIST","health":"unknown","source":"saved",
                 "allow_http_fallback":False,"http_fallback_accepted":False,
                 "tls_fallback_accepted":False,"tls_mode":"strict","device_profile":"auto"}
        profile=mark_http_consent(profile,True)
        self.step="testing";self._render()
        def work(handle):
            client=_client_from_profile(profile,timeout=max(15,int(load_settings().get("timeout",10) or 10)))
            try:
                started=time.monotonic()
                if str(profile.get("source_type") or "").lower()=="m3u" and hasattr(client,"probe"):
                    probe=client.probe(cancel_event=getattr(handle,"cancel_event",None))
                    info={"status":"ONLINE","account_status":"M3U PLAYLIST","probe":probe}
                else:
                    info=client.account_info()
                health=client.health_snapshot() or {}
                latency=health.get("latency_ms") or int((time.monotonic()-started)*1000)
                return info,health,latency,getattr(client,"security_warning","")
            finally:client.close()
        self._run_async(work,lambda result:self._test_ok(profile,result),self._test_failed)

    def _got_mac(self, mac):
        value=str(mac or "").strip().upper()
        if not value:
            self.step="mac"; self._render(); return
        self._new_mac=value
        profile={"portal":self._new_portal,"mac":self._new_mac,"source_type":"stalker","state":"Not checked","allow_http_fallback":False,"http_fallback_accepted":False,"tls_fallback_accepted":False,"tls_mode":"auto","device_profile":"auto"}
        profile=mark_http_consent(profile,True)
        try: _validate_profile_client(profile)
        except Exception as exc:
            self._last_error=_friendly_portal_error(exc); self.step="failed"; self._render(); return
        self.step="testing"; self._render()
        def work(handle):
            if handle.cancelled():return None
            client=_client_from_profile(profile,timeout=min(int(load_settings().get("timeout",10) or 10),15))
            try:
                started=time.monotonic(); info=client.account_info()
                if handle.cancelled():return None
                health=client.health_snapshot() or {}
                latency=health.get("latency_ms") or int((time.monotonic()-started)*1000)
                return info,health,latency,getattr(client,"security_warning","")
            finally: client.close()
        self._run_async(work,lambda result:self._test_ok(profile,result),self._test_failed)

    def _test_ok(self, profile, result):
        info,health,latency,warning=result
        expiry=info.get("phone") or info.get("end_date") or info.get("expire_billing_date") or ""
        state=info.get("status") or info.get("account_status") or "ONLINE"
        profile.update({"state":"%s%s%s"%(state,(" / "+str(expiry)) if expiry else "",(" / INSECURE HTTP" if warning else "")),"account_state":str(state),"expiry":str(expiry or ""),"latency_ms":latency,"health":health.get("health") or ("online" if latency < 1500 else "slow"),"last_success":int(time.time())})
        try:
            rows=load_profiles()
            source_type=str(profile.get("source_type") or "stalker").lower()
            wanted_url=str(profile.get("portal") or "").rstrip("/").casefold()
            key=(wanted_url,str(profile.get("mac") or "").upper())
            replaced=False
            for i,row in enumerate(rows):
                if source_type=="m3u":
                    if (str(row.get("source_type") or "").lower()=="m3u" and
                        str(row.get("portal") or "").rstrip("/").casefold()==wanted_url):
                        rows[i]=profile;replaced=True;break
                else:
                    row_key=(str(row.get("portal") or "").rstrip("/").casefold(),str(row.get("mac") or "").upper())
                    if row_key==key:rows[i]=profile;replaced=True;break
            if not replaced:rows.append(profile)
            enable_profile(profile);save_profiles(rows)

            loaded=load_profiles();saved=None
            for row in loaded:
                if source_type=="m3u":
                    if (str(row.get("source_type") or "").lower()=="m3u" and
                        str(row.get("portal") or "").rstrip("/").casefold()==wanted_url):
                        saved=row;break
                else:
                    if ((str(row.get("portal") or "").rstrip("/").casefold(),str(row.get("mac") or "").upper())==key):
                        saved=row;break
            if saved is None:
                raise RuntimeError("Saved source did not survive profile reload")

            self._created_profile=saved;self.step="success";self._mark_done();self._render()
            self._open_after_finish(saved)
        except Exception as exc:
            self._last_error=_("Connection worked, but the profile could not be saved: %s")%exc; self.step="failed"; self._render()

    def _test_failed(self, exc):
        self._last_error=_friendly_portal_error(exc); self.step="failed"; self._render()
        self.session.open(MessageBox,self._last_error,MessageBox.TYPE_ERROR,timeout=8)

    def _mark_done(self):
        try:
            os.makedirs(os.path.dirname(WIZARD_DONE_FILE),mode=0o700,exist_ok=True)
            tmp="%s.tmp.%d.%d"%(WIZARD_DONE_FILE,os.getpid(),threading.get_ident())
            with open(tmp,"w") as h:
                h.write(PLUGIN_VERSION+"\n")
                h.flush();os.fsync(h.fileno())
            os.chmod(tmp,0o600);os.replace(tmp,WIZARD_DONE_FILE)
            save_settings({"first_run_wizard":False})
        except Exception as exc:
            try:
                if "tmp" in locals() and os.path.exists(tmp):os.unlink(tmp)
            except OSError:pass
            optional_failure("ui",exc)

    def skip(self):
        if self._busy:
            try:self._stop_async()
            except Exception as exc:optional_failure("ui",exc)
        self._mark_done(); self._open_after_finish(None)

    def _open_after_finish(self, profile=None):
        try:
            if profile:
                self.session.openWithCallback(self._next_closed,PortalHomeScreen,profile)
            else:
                self.session.openWithCallback(self._next_closed,PortalListScreen)
        except Exception:
            self.close()

    def _next_closed(self,*args,**kwargs):
        self.close()

