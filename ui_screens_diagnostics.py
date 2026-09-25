"""Diagnostics screen extracted from ui.py without changing behavior."""

from . import _
import os
import threading
import time

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label

from .core.diagnostics import snapshot as diagnostic_snapshot, export_support_bundle
from .core.backup import list_backups
from .persistent_cache import PORTAL_ART as IMAGE_CACHE_DIR, hdd_read_ready
from .storage import load_settings, load_theme
from .ultra import engine_memory_count
from .version import PLUGIN_VERSION, BUILD_NAME
from .log import optional_failure

DIAGNOSTICS_SKIN = ""

class DiagnosticsScreen(Screen):
    skin = DIAGNOSTICS_SKIN
    def __init__(self, session, profile=None):
        Screen.__init__(self, session); self.profile=profile
        self["title"]=Label(_("SYSTEM DIAGNOSTICS  •  NOVA FHD"))
        self["body"]=Label(""); self["status"]=Label(_("GREEN: refresh  •  YELLOW: support bundle"))
        self["green"]=Label(_("Refresh")); self["yellow"]=Label(_("Support bundle"))
        self["actions"]=ActionMap(["OkCancelActions","ColorActions"],{"cancel":self.close,"green":self.refresh,"yellow":self.export_report},-1)
        self.onLayoutFinish.append(self.refresh)
    def refresh(self):
        try:d=diagnostic_snapshot(self.profile)
        except Exception as exc:d={"error":str(exc)}
        files=0; size=0
        try:
            if hdd_read_ready(force=True):
                for n in os.listdir(IMAGE_CACHE_DIR):
                    p=os.path.join(IMAGE_CACHE_DIR,n)
                    if os.path.isfile(p):files+=1; size+=os.path.getsize(p)
        except Exception as exc:optional_failure("ui",exc)
        db=d.get("database") or {}; perf=d.get("performance") or {}; cfg=load_settings()
        http=perf.get("http.request_ms") or {}; keepalive=perf.get("http.keepalive_reuse") or {}; bulk=perf.get("sqlite.bulk_state_ms") or {}; search_perf=perf.get("search.total_ms") or {}
        lines=[
            _("Plugin version        %s  •  %s")%(PLUGIN_VERSION,BUILD_NAME),
            _("Python                %s")%d.get("python",_("unknown")),
            _("Python support        %s") % str((d.get("python_compatibility") or {}).get("message") or _("unknown")),
            _("Platform              %s")%str(d.get("platform",_("unknown")))[:70],
            _("Active threads        %d")%threading.active_count(),
            _("Image cache           %d files  •  %.1f MB")%(files,size/(1024.0*1024.0)),
            _("Persistent cache      %d files  •  %.1f MB  •  %s") % (int((d.get("persistent_cache") or {}).get("files",0)), float((d.get("persistent_cache") or {}).get("bytes",0))/(1024.0*1024.0), str((d.get("persistent_cache") or {}).get("root") or _("unknown"))[:55]),
            _("Process health        %s") % str(d.get("runtime_health") or {})[:100],
            _("Endurance guard       %s") % str(d.get("endurance") or {})[:100],
            _("TLS security          %s") % ((_("UNVERIFIED / COMPATIBLE") if str((self.profile or {}).get("tls_mode") or "auto").lower()=="compatible" or bool((self.profile or {}).get("tls_fallback_accepted",False)) else _("VERIFIED / STRICT-AUTO"))),
            _("Database health       %s")%str(db)[:100],
            _("HTTP performance      %s req • avg %sms • keepalive %s")%(int((perf.get("http.requests") or {}).get("count",0)),http.get("avg_ms","-"),int(keepalive.get("count",0))),
            _("SQLite page state     %s runs • avg %sms")%(int(bulk.get("count",0)),bulk.get("avg_ms","-")),
            _("Search performance    %s runs • avg %sms")%(int(search_perf.get("count",0)),search_perf.get("avg_ms","-")),
            _("Portal selected       %s")%(str((self.profile or {}).get("portal") or _("None"))[:70]),
            _("Last portal success   %s")%(time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(int((self.profile or {}).get("last_success") or 0))) if (self.profile or {}).get("last_success") else _("Never")),
            _("Last portal error     %s")%str((self.profile or {}).get("last_error") or _("None"))[:70],
            _("Theme                 %s")%load_theme().replace("_"," ").title(),
            _("Playback preference   %s")%cfg.get("service_type",4097),
            _("Smart engine memories  %s")%engine_memory_count(),
            _("Smart recovery         %s / retries %s")%((_("ON") if cfg.get("smart_recovery",True) else _("OFF")),cfg.get("stream_retry_count",1)),
            _("Saved backups          %d")%len(list_backups()),
            _("Channel list mode      %s")%cfg.get("channel_list_mode","epg"),
            _("Clean titles           %s")%(_("ON") if cfg.get("clean_titles",True) else _("OFF")),
        ]
        self["body"].setText("\n\n".join(lines)); self["status"].setText(_("Diagnostics refreshed at %s")%time.strftime("%H:%M:%S"))
    def export_report(self):
        try:path=export_support_bundle('/tmp/ultrastalker-support.zip',self.profile); self["status"].setText(_("Exported: %s")%path)
        except Exception as exc:self["status"].setText(_("Export failed: %s")%exc)

