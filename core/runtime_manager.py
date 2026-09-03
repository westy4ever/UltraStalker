# -*- coding: utf-8 -*-
import os
from ..log import get_logger
LOG=get_logger()

def capability_report(include_hdd=True):
    result={"pillow":False,"hdd":None,"external_player":False}
    try:
        import PIL.Image
        result["pillow"]=True
    except Exception:pass
    if include_hdd:
        try:
            from ..persistent_cache import hdd_read_ready
            result["hdd"]=bool(hdd_read_ready(force=True))
        except Exception:
            result["hdd"]=False
    result["external_player"]=any(os.path.exists(p) for p in ("/usr/bin/exteplayer3","/usr/bin/gstplayer"))
    return result

def shutdown_runtime(wait=False):
    steps=[]
    def call(label,func):
        try:func();steps.append((label,True))
        except Exception as exc:LOG.warning("Runtime shutdown step failed [%s]: %s",label,exc);steps.append((label,False))
    from .recording import stop_recording_manager
    from .bouquets import stop_proxy_server
    from .session import PortalSession
    from ..ui import shutdown_ui_workers
    from ..tmdb import shutdown_tmdb_workers
    from ..artwork_v2 import shutdown_artwork_workers
    from ..downloads import shutdown_download_manager
    from ..services.player import shutdown_player_workers
    from .tasks import TASKS
    from .runtime_log import shutdown_runtime_log
    from ..repositories.database import DB
    call("recording",lambda:stop_recording_manager(wait=bool(wait)))
    call("bouquet_epg",stop_proxy_server)
    call("portal_session",PortalSession.invalidate)
    call("downloads",lambda:shutdown_download_manager(wait=True,timeout=1.5))
    call("artwork",lambda:shutdown_artwork_workers(wait=True,timeout=1.5))
    call("player_frames",lambda:shutdown_player_workers(wait=bool(wait)))
    call("ui",lambda:shutdown_ui_workers(wait=bool(wait)))
    call("tmdb",lambda:shutdown_tmdb_workers(wait=bool(wait)))
    call("tasks",lambda:TASKS.shutdown(wait=bool(wait)))
    call("sqlite_checkpoint",lambda:DB.checkpoint(truncate=True))
    call("runtime_log",lambda:shutdown_runtime_log(wait=bool(wait)))
    return steps
