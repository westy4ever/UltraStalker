# -*- coding: utf-8 -*-
import os
import time

from Plugins.Plugin import PluginDescriptor

from . import _
from .compat import startup_preflight, format_preflight_error
from .log import get_logger, configure_logger

LOG = get_logger()

# Load only Ultra Stalker's namespaced keymaps (Player + MENU fallback).
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
            try:
                from .webcleaner import start as start_webcleaner
                start_webcleaner()
            except Exception as exc: LOG.warning("Web Cleaner auto-start failed: %s", exc)
            try:
                from .updater import deferred_auto_check
                deferred_auto_check(session, delay_ms=5000)
            except Exception as exc: LOG.warning("Online update check unavailable: %s", exc)
            # R113: manual api_keys.conf edits are reconciled after first paint
            # and entirely off the UI thread.  Normal in-app credential saves
            # still detach the bootstrap cache immediately in storage.py.
            try:
                import threading
                def _provider_bootstrap_reconcile_worker():
                    try:
                        from .provider_bootstrap import reconcile_tmdb_state
                        state=reconcile_tmdb_state()
                        LOG.info("R113 provider bootstrap state: %s", state)
                    except Exception as _exc:
                        LOG.warning("R113 provider bootstrap reconcile unavailable: %s", _exc)
                _bt=threading.Thread(target=_provider_bootstrap_reconcile_worker,name="UltraStalkerProviderBootstrapReconcile")
                _bt.daemon=True;_bt.start()
                setattr(holder,"_ultrastalker_provider_bootstrap_reconcile_thread",_bt)
            except Exception as exc:
                LOG.warning("R113 provider bootstrap reconcile scheduling failed: %s", exc)
            try:
                from .release_cleanup_r249 import schedule_title_logo_migration
                schedule_title_logo_migration()
            except Exception as exc:
                LOG.warning("R249 title-logo migration scheduling failed: %s", exc)
            try: timer.stop()
            except Exception: pass
        try: conn = timer.timeout.connect(run)
        except Exception:
            timer.callback.append(run); conn = None
        holder = screen if screen is not None else session
        setattr(holder, "_ultrastalker_runtime_start_timer", timer)
        if conn is not None: setattr(holder, "_ultrastalker_runtime_start_conn", conn)
        # Do not make recorder/proxy/web-cleaner imports compete with first paint.
        # Session-start already owns normal startup; this delayed pass is only an
        # idempotent safety net for hot-installed/reloaded plugins.
        timer.start(8000, True)
    except Exception as exc:
        LOG.warning("Runtime service deferral unavailable: %s", exc)



def _schedule_stale_session_cleanup(session, delay_ms=20000):
    """PERFLAB10: clean old presentation sessions off the UI thread.

    The current session and all canonical/BLUE artwork are outside the deletion
    target.  A short startup delay also keeps HDD traversal away from first paint.
    """
    try:
        if getattr(session, "_ultrastalker_session_cleanup_timer", None) is not None:
            return
        from enigma import eTimer
        timer = eTimer()

        def worker():
            # Late-mounted HDDs are common on receivers. Retry a couple of times
            # in this background worker instead of blocking Enigma2 startup.
            try:
                import time
                from .persistent_cache import cleanup_stale_session_caches
                result = None
                for attempt in range(3):
                    result = cleanup_stale_session_caches()
                    if result.get("ready"):
                        break
                    if attempt < 2:
                        time.sleep(10.0)
                if isinstance(result, dict):
                    LOG.info(
                        "PerfLab stale-session cleanup: removed=%s live=%s errors=%s ready=%s",
                        result.get("removed", 0), result.get("skipped_live", 0),
                        result.get("errors", 0), result.get("ready", False),
                    )
            except Exception as exc:
                LOG.warning("PerfLab stale-session cleanup unavailable: %s", exc)

        def run():
            try: timer.stop()
            except Exception: pass
            try:
                import threading
                thread = threading.Thread(target=worker, name="UltraStalkerSessionCleanup")
                thread.daemon = True
                setattr(session, "_ultrastalker_session_cleanup_thread", thread)
                thread.start()
            except Exception as exc:
                LOG.warning("PerfLab session cleanup worker start failed: %s", exc)

        try: conn = timer.timeout.connect(run)
        except Exception:
            timer.callback.append(run); conn = None
        setattr(session, "_ultrastalker_session_cleanup_timer", timer)
        if conn is not None:
            setattr(session, "_ultrastalker_session_cleanup_conn", conn)
        timer.start(int(delay_ms), True)
    except Exception as exc:
        LOG.warning("PerfLab stale-session cleanup scheduling failed: %s", exc)


def main(session, **kwargs):
    _perf_t0 = time.monotonic()
    LOG.info("PERF25 startup main_enter mono_ms=%d", int(_perf_t0 * 1000))
    _phase = time.monotonic()
    if _preflight(session) is None:
        return
    LOG.info("PERF25 startup preflight_done elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
    try:
        # R249: one-time backdrop-only detach is intentionally tiny and runs
        # before artwork can reuse any legacy backdrop tree. Large deletion and
        # title-logo migration remain off the UI thread.
        try:
            from .release_cleanup_r249 import prepare_backdrops_once
            prepare_backdrops_once()
        except Exception as exc:
            LOG.warning("R249 backdrop layout preparation unavailable: %s", exc)
        # Test69 Lean boot: paint the lightweight Splash before importing the
        # full UI graph.  The splash lazily loads ui.py after its native pixmap
        # is already visible, so pressing the plugin icon never looks frozen.
        _phase = time.monotonic()
        from .ui_screens_splash import SplashScreen
        LOG.info("PERF25 startup splash_import_done elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
        _phase = time.monotonic()
        from .core.runtime_manager import capability_report
        report = capability_report(include_hdd=False)
        LOG.info("PERF25 startup capability_done elapsed_ms=%d", int((time.monotonic() - _phase) * 1000))
        if not report.get("pillow"):
            LOG.error("Pillow unavailable; refusing to start incomplete artwork architecture")
            try:
                from Screens.MessageBox import MessageBox
                session.open(MessageBox, _("Ultra Stalker requires Pillow for artwork and Adaptive UI.\n\nInstall the required runtime capability, then reopen the plugin."), MessageBox.TYPE_ERROR, timeout=20)
            except Exception as msg_exc:LOG.error("Pillow capability message failed: %s",msg_exc)
            return
        _phase = time.monotonic()
        screen = session.open(SplashScreen)
        LOG.info("PERF25 startup splash_open_return elapsed_ms=%d total_ms=%d", int((time.monotonic() - _phase) * 1000), int((time.monotonic() - _perf_t0) * 1000))
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
            from .core.runtime_manager import capability_report
            if not capability_report(include_hdd=False).get("pillow"):
                LOG.error("Runtime services deferred because Pillow capability is unavailable")
                return
        except Exception as exc:
            LOG.warning("Capability gate unavailable at session start: %s",exc)
        try:
            from .release_cleanup_r249 import prepare_backdrops_once, schedule_title_logo_migration
            prepare_backdrops_once()
            schedule_title_logo_migration()
        except Exception as exc:
            LOG.warning("R249 HDD cleanup scheduling unavailable at session start: %s", exc)
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
        _schedule_stale_session_cleanup(session)
        return
    if reason != 0:
        try:
            from .webcleaner import stop as stop_webcleaner
            stop_webcleaner()
        except Exception as exc:
            LOG.warning("Web Cleaner shutdown failed: %s", exc)
        try:
            from .core.runtime_manager import shutdown_runtime
            shutdown_runtime(wait=False)
        except Exception as exc:
            LOG.warning("Runtime shutdown failed: %s", exc)



def main_menu_entry(menuid, **kwargs):
    """Expose Ultra Stalker directly in Enigma2 Main Menu when enabled.

    The setting is read when the menu is opened, so toggling it takes effect
    immediately without restarting Enigma2. The normal Plugins-menu entry is
    always kept as a safe way back into the plugin.
    """
    if str(menuid or "") != "mainmenu":
        return []
    try:
        from .storage import load_settings
        if not bool(load_settings().get("show_main_menu", True)):
            return []
    except Exception as exc:
        LOG.warning("Main-menu visibility read failed: %s", exc)
    return [(_("Ultra Stalker"), main, "ultrastalker_main", 50)]


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name=_("Ultra Stalker"),
            description=_("Ultra Stalker"),
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=main,
        ),
        PluginDescriptor(where=PluginDescriptor.WHERE_MENU, fnc=main_menu_entry),
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=session_start),
    ]
