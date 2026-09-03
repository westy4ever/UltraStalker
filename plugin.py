# -*- coding: utf-8 -*-
import os

from Plugins.Plugin import PluginDescriptor

from . import _
from .compat import startup_preflight, format_preflight_error
from .log import get_logger, configure_logger

LOG = get_logger()

# Load only this plugin's namespaced player keymap.
try:
    import keymapparser
    _KEYMAP = os.path.join(os.path.dirname(__file__), "keymap.xml")
    if os.path.isfile(_KEYMAP):
        keymapparser.readKeymap(_KEYMAP)
except Exception as exc:
    LOG.warning("Keymap load failed: %s", exc)


def _show_startup_error(session, report):
    text = format_preflight_error(report)
    try:
        from Screens.MessageBox import MessageBox
        session.open(MessageBox, text, MessageBox.TYPE_ERROR, timeout=15)
    except Exception as exc:
        LOG.error("Startup compatibility failure: %s; MessageBox unavailable: %s", text.replace("\n", " | "), exc)


def _preflight(session=None):
    report = startup_preflight()
    if not report.get("ok"):
        LOG.error("Startup preflight failed: %s", "; ".join(report.get("fatal") or []))
        if session is not None:
            _show_startup_error(session, report)
        return None
    for warning in report.get("warnings") or []:
        LOG.warning("Startup preflight warning: %s", warning)
    return report


def _defer_runtime_services(session, screen=None):
    """Recover runtime services after the Splash has been opened."""
    try:
        from enigma import eTimer
        from .core.recording import start_recording_manager
        from .core.bouquets import start_proxy_server
        timer = eTimer()
        def run():
            try: configure_logger()
            except Exception: pass
            try: start_recording_manager(session, initial_tick=False)
            except Exception as exc: LOG.warning("Deferred recording manager start failed: %s", exc)
            try: start_proxy_server()
            except Exception as exc: LOG.warning("Deferred proxy start failed: %s", exc)
            try: timer.stop()
            except Exception: pass
        try: conn = timer.timeout.connect(run)
        except Exception:
            timer.callback.append(run); conn = None
        holder = screen if screen is not None else session
        setattr(holder, "_ultrastalker_runtime_start_timer", timer)
        if conn is not None: setattr(holder, "_ultrastalker_runtime_start_conn", conn)
        timer.start(150, True)
    except Exception as exc:
        LOG.warning("Runtime service deferral unavailable: %s", exc)


def main(session, **kwargs):
    if _preflight(session) is None:
        return
    try:
        from .ui import SplashScreen, begin_plugin_launch
        from .core.runtime_manager import capability_report
        report = capability_report(include_hdd=False)
        if not report.get("pillow"):
            LOG.warning("Pillow unavailable; adaptive artwork features are limited")
        begin_plugin_launch(session)
        screen = session.open(SplashScreen)
        _defer_runtime_services(session, screen)
    except Exception as exc:
        LOG.exception("Plugin startup failed after preflight")
        try:
            from Screens.MessageBox import MessageBox
            session.open(MessageBox, _("Ultra Stalker failed to start:\n\n%s") % exc, MessageBox.TYPE_ERROR, timeout=15)
        except Exception:
            pass


def session_start(reason, session=None, **kwargs):
    if reason == 0 and session is not None:
        if _preflight(None) is None:
            return
        try:
            from .core.recording import start_recording_manager
            from .core.bouquets import start_proxy_server
            try: configure_logger()
            except Exception as exc: LOG.warning("Logger setup failed at session start: %s", exc)
            try: start_recording_manager(session)
            except Exception as exc: LOG.warning("Recording manager start failed: %s", exc)
            try: start_proxy_server()
            except Exception as exc: LOG.warning("Bouquet proxy start failed: %s", exc)
        except Exception as exc:
            LOG.exception("Runtime service imports failed after preflight: %s", exc)
        return
    if reason != 0:
        try:
            from .core.runtime_manager import shutdown_runtime
            shutdown_runtime(wait=False)
        except Exception as exc:
            LOG.warning("Runtime shutdown failed: %s", exc)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name=_("Ultra Stalker"),
            description=_("Ultra Stalker"),
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=main,
        ),
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=session_start),
    ]
