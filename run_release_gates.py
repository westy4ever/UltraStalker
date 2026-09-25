# -*- coding: utf-8 -*-
"""UltraStalker release-critical regression gates.

Run before packaging a release candidate/final build:
    python3 /usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/run_release_gates.py

These gates intentionally cover high-impact regressions that must never be
lost from the broader test suite: download path containment and one-shot
signature compatibility (no retry after an internal TypeError).
"""
from __future__ import print_function

import ast
import hashlib
import os
import sys
import unittest


def _paths():
    plugin_dir = os.path.abspath(os.path.dirname(__file__))
    python_root = os.path.dirname(os.path.dirname(os.path.dirname(plugin_dir)))
    return plugin_dir, python_root


def _static_gate(plugin_dir):
    failures = []

    downloads_path = os.path.join(plugin_dir, "downloads.py")
    with open(downloads_path, "r", encoding="utf-8") as handle:
        downloads_src = handle.read()
    try:
        tree = ast.parse(downloads_src, filename=downloads_path)
    except SyntaxError as exc:
        return ["downloads.py does not parse: %s" % (exc,)]

    module_funcs = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "_validated_download_path" not in module_funcs:
        failures.append("downloads.py is missing _validated_download_path")

    manager = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DownloadManager"), None)
    methods = {} if manager is None else {node.name: node for node in manager.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if manager is None:
        failures.append("downloads.DownloadManager is missing")
    for required in ("_load", "add", "_download"):
        node = methods.get(required)
        if node is None:
            failures.append("downloads.DownloadManager.%s is missing" % required)
            continue
        segment = ast.get_source_segment(downloads_src, node) or ""
        if "_validated_download_path" not in segment:
            failures.append("downloads.DownloadManager.%s no longer validates download paths" % required)

    # Player subtitle modal visibility contract. Subtitle navigation must never
    # outlive a hidden Player InfoBar; the chooser owns visibility until exit.
    overlays_path = os.path.join(plugin_dir, "services", "player_overlays.py")
    native_path = os.path.join(plugin_dir, "services", "player_native.py")
    try:
        with open(overlays_path, "r", encoding="utf-8") as handle:
            overlays_src = handle.read()
        with open(native_path, "r", encoding="utf-8") as handle:
            native_src = handle.read()
        overlays_tree = ast.parse(overlays_src, filename=overlays_path)
        native_tree = ast.parse(native_src, filename=native_path)
        overlay_classes = {node.name: node for node in overlays_tree.body if isinstance(node, ast.ClassDef)}
        visibility = overlay_classes.get("UltraInfobarVisibility")
        visibility_methods = {} if visibility is None else {node.name: node for node in visibility.body if isinstance(node, ast.FunctionDef)}
        for required in ("_us_infobar_modal_hold_active", "startHideTimer", "doTimerHide", "_toggle_infobar"):
            if required not in visibility_methods:
                failures.append("UltraInfobarVisibility.%s is missing" % required)
        for required in ("startHideTimer", "doTimerHide", "_toggle_infobar"):
            node = visibility_methods.get(required)
            segment = ast.get_source_segment(overlays_src, node) or "" if node is not None else ""
            if node is not None and "_us_infobar_modal_hold_active" not in segment:
                failures.append("UltraInfobarVisibility.%s no longer honors modal subtitle hold" % required)

        native_classes = {node.name: node for node in native_tree.body if isinstance(node, ast.ClassDef)}
        player = native_classes.get("UltraStalkerPlayer")
        player_methods = {} if player is None else {node.name: node for node in player.body if isinstance(node, ast.FunctionDef)}
        for required in ("_subtitle_inline_lock", "_subtitle_inline_finish", "_subtitle_native_hold_begin", "_subtitle_native_hold_end", "hide"):
            if required not in player_methods:
                failures.append("UltraStalkerPlayer.%s is missing" % required)
        hide_node = player_methods.get("hide")
        hide_segment = ast.get_source_segment(native_src, hide_node) or "" if hide_node is not None else ""
        if hide_node is not None and "_us_infobar_modal_hold_active" not in hide_segment:
            failures.append("UltraStalkerPlayer.hide no longer protects active subtitle workflow")
    except (OSError, SyntaxError) as exc:
        failures.append("subtitle visibility gate could not parse player sources: %s" % exc)

    # R270 current-architecture regression gate.
    #
    # The old R149/R150/R151/R154 checks pinned implementation details that were
    # intentionally retired by later releases (three-view derivative vaults,
    # exact historical player hashes, old BG HUD cache names, etc.).  Keep the
    # *behavioral guarantees* instead: one canonical backdrop authority, current
    # Details metadata/adaptive authority, uninterrupted full-category BLUE
    # caching, Splash prewarm, and the approved Live handoff contract.
    try:
        def read_src(name):
            with open(os.path.join(plugin_dir, name), "r", encoding="utf-8") as handle:
                return handle.read()

        persistent_txt = read_src("persistent_cache.py")
        provider_bootstrap_txt = read_src("provider_bootstrap.py")
        dyn_txt = read_src("ui_dynamic_chrome.py")
        details_ui_txt = read_src("ui_screens_details.py")
        details_auth_txt = read_src("details_authority.py")
        cin_txt = read_src("ui_cinematic_global.py")
        bg_txt = read_src("ui_backdrop_grid.py")
        grid_txt = read_src("ui_grid_base.py")
        home_txt = read_src("ui_screens_home.py")
        splash_runtime_txt = read_src("ui.py")
        live_ui_txt = read_src("ui_grid_screens.py")
        client_txt = read_src("client.py")
        m3u_txt = read_src("m3u_adapter.py")

        # One persistent artwork authority. View-specific three-view artwork is
        # retired; Cinematic/BG consume the canonical HDD backdrop itself.
        if 'return os.path.join("/media/hdd", PLUGIN_DIRNAME)' not in persistent_txt:
            failures.append("R270 artwork gate: persistent cache root is not fixed to /media/hdd/UltraStalker")
        if 'BACKDROPS = os.path.join(ROOT, "backdrops")' not in persistent_txt:
            failures.append("R270 artwork gate: canonical backdrops directory missing")
        if 'def _prepare_three_view_fast_chrome' not in cin_txt or 'R259: retired durable Three-View fast package' not in cin_txt:
            failures.append("R270 artwork gate: retired three-view derivative package contract missing")
        if 'def _prepare_shared_cinematic_backdrop' not in cin_txt or 'return source if source and os.path.isfile(source)' not in cin_txt:
            failures.append("R270 artwork gate: Cinematic/BG no longer reuse the canonical backdrop directly")
        if 'current_sig=self._cin_file_sig(row.get("backdrop_local"))' not in cin_txt or 'backdrop_source_sig' not in cin_txt:
            failures.append("R270 artwork gate: prepared backdrop identity is not tied to canonical backdrop_local")
        if 'class PremiumBackdropGridScreen(PremiumPosterGridScreen, PremiumGlobalCinematicScreen)' not in bg_txt:
            failures.append("R270 artwork gate: Backdrop Grid no longer inherits the clean Cinematic authority")
        if '_fast_bd_schedule = PremiumGlobalCinematicScreen._fast_bd_schedule' not in bg_txt:
            failures.append("R270 artwork gate: BG clean backdrop scheduling diverged from Cinematic")

        # Provider/server artwork fallback must remain retired. The compatibility
        # shim may purge legacy data, but it must never download/provider-bootstrap
        # visual bytes again.
        try:
            pb_tree = ast.parse(provider_bootstrap_txt, filename="provider_bootstrap.py")
            pb_funcs = {n.name: n for n in pb_tree.body if isinstance(n, ast.FunctionDef)}
            enabled = pb_funcs.get("bootstrap_enabled")
            ensured = pb_funcs.get("ensure_provider_art")
            enabled_seg = ast.get_source_segment(provider_bootstrap_txt, enabled) or "" if enabled is not None else ""
            ensured_seg = ast.get_source_segment(provider_bootstrap_txt, ensured) or "" if ensured is not None else ""
            if enabled is None or "return False" not in enabled_seg:
                failures.append("R270 artwork gate: provider artwork bootstrap can be enabled")
            if ensured is None or "return {}" not in ensured_seg or "downloader(" in ensured_seg or ".download(" in ensured_seg:
                failures.append("R270 artwork gate: retired provider artwork shim can still fetch artwork")
        except SyntaxError as exc:
            failures.append("R270 artwork gate: provider_bootstrap.py does not parse: %s" % exc)
        for authority_name in ("details_authority.py", "artwork_v2_impl.py"):
            authority_txt = read_src(authority_name)
            if "ensure_provider_art(" in authority_txt:
                failures.append("R270 artwork gate: active provider artwork fallback returned in %s" % authority_name)

        # R269 first-entry Details adaptive contract. Native overview geometry is
        # 1318x170, old 136px derivatives cannot be reused, and exact-size glass
        # work runs on the bounded Details executor rather than single-flight UI
        # async work.
        if '"overview_detail":((1318,170),22,True)' not in dyn_txt:
            failures.append("R270 Details gate: overview_detail is not native 1318x170")
        if 'dyn269overview_inset_' not in dyn_txt:
            failures.append("R270 Details gate: R269 overview cache namespace missing")
        try:
            d_tree = ast.parse(details_ui_txt, filename="ui_screens_details.py")
            d_classes = [n for n in d_tree.body if isinstance(n, ast.ClassDef)]
            queue_node = None
            apply_node = None
            for cls in d_classes:
                for node in cls.body:
                    if isinstance(node, ast.FunctionDef) and node.name == "_queue_sharp_detail_chrome_asset":
                        queue_node = node
                    if isinstance(node, ast.FunctionDef) and node.name == "_apply_detail_chrome":
                        apply_node = node
            queue_seg = ast.get_source_segment(details_ui_txt, queue_node) or "" if queue_node is not None else ""
            apply_seg = ast.get_source_segment(details_ui_txt, apply_node) or "" if apply_node is not None else ""
            if queue_node is None or "_DETAIL_PREFETCH_EXECUTOR.submit" not in queue_seg or "self._run_async" in queue_seg:
                failures.append("R270 Details gate: exact adaptive glass is not using the bounded Details executor")
            if apply_node is None or '"overview_bg":("overview_detail","us89_overview_card.png")' not in apply_seg or '"overview_detail":(1318,170)' not in apply_seg:
                failures.append("R270 Details gate: Details overview widget/exact-size mapping changed")
        except SyntaxError as exc:
            failures.append("R270 Details gate: ui_screens_details.py does not parse: %s" % exc)

        # One Details metadata/synopsis authority for Details/Cinematic/BG. The
        # selected information language is resolved centrally; provider synopsis
        # is allowed only as same-item text fallback, never as artwork authority.
        if 'def details_overview(' not in details_auth_txt or 'load_detail_snapshot_by_tmdb' not in details_auth_txt:
            failures.append("R270 Details gate: canonical synopsis authority missing")
        if 'def resolve_metadata_fast(' not in details_auth_txt or 'merged["_details_authority_ready"]=True' not in details_auth_txt:
            failures.append("R270 Details gate: fast canonical metadata hydration missing")
        if 'details_overview(self.profile,self.media_type,item,row)' not in cin_txt:
            failures.append("R270 Details gate: Cinematic no longer consumes Details synopsis authority")
        if 'details_overview(self.profile,self.media_type,item,row)' not in bg_txt:
            failures.append("R270 Details gate: Backdrop Grid no longer consumes Details synopsis authority")
        if 'resolve_metadata_fast' not in cin_txt:
            failures.append("R270 Details gate: Cinematic fast metadata lane missing")

        # R267 full-category BLUE cache contract. Enumerate the entire category,
        # count/skip valid Q60 files before TMDb, then process only missing titles
        # sequentially. Fifty may never become a terminal work limit again.
        for required in (
            'def _cache_backdrop_category_rows(',
            'def _page_backdrop_existing_q60(',
            'def _cache_current_page_backdrops_worker(',
            'catalogue=supplied if supplied is not None else self._cache_backdrop_category_rows(cancel_event)',
            'existing=self._page_backdrop_existing_q60(item,hot)',
            'for item,hot in pending:',
            'fp=str(details.get("backdrop_path") or "").strip()',
            'choice="gallery_fallback"',
            'result["incomplete"]=True',
        ):
            if required not in grid_txt:
                failures.append("R270 BLUE gate: missing current full-category contract: %s" % required)
        if 'pending[:50]' in grid_txt or 'catalogue[:50]' in grid_txt or 'rows[:50]' in grid_txt:
            failures.append("R270 BLUE gate: a 50-title processing cap returned")

        # R268 Splash does real startup work before Home is opened. Progress must
        # represent work stages rather than an immediate 100% paint.
        for required in (
            'def splash_warm_home_profile(',
            "report(25,_('Preparing Home artwork'))",
            "(\"itv\",50,_('Preparing Live categories'))",
            "(\"vod\",75,_('Preparing Movie categories'))",
            "report(100,_('Ready'))",
            '_remember_splash_home_warm(',
        ):
            if required not in splash_runtime_txt:
                failures.append("R270 Splash gate: missing prewarm stage: %s" % required)
        if 'splash_home_is_warm' not in home_txt or 'splash_home_recent_assets' not in home_txt:
            failures.append("R270 Splash gate: Home does not consume Splash warm state")

        # Live provider policy and Preview -> Full Screen/folder handoff remain
        # behavior-checked. Do not pin an ancient player file SHA: later subtitle
        # and stability fixes are legitimate as long as these contracts survive.
        if 'def _live_link_decision(' not in client_txt or '"use_http_tmp_link","use_load_balancing","disable_ad"' not in client_txt:
            failures.append("R270 Live gate: Stalker Live provider-policy decision missing")
        if 'params["force_ch_link_check"] = "1" if self._live_force_link_check else "0"' not in client_txt:
            failures.append("R270 Live gate: portal force-link policy missing")
        if 'return "%s/live/%s/%s/%s.%s"' not in m3u_txt or 'def create_link(self,item,media_type="itv",*args,**kwargs):' not in m3u_txt:
            failures.append("R270 Live gate: Xtream direct Live URL contract changed")
        for required in (
            'if self._active_preview_key==key and self._active_preview_url:',
            'self._open_preview_fullscreen(item)',
            'self._launch_live_fullscreen(item,url,engine,True)',
            'payload["_live_folder_channels"]=snapshot',
            'def _fit_live_epg_line(',
        ):
            if required not in live_ui_txt:
                failures.append("R270 Live gate: missing current Preview/Full-Screen contract: %s" % required)

        # Home focus ownership remains explicit so Menu and Recent focus never
        # paint simultaneously.
        if 'self["selection"].hide();self._set_recent_row_focus(self.recent_index);self._home_focus_drawn_row="recent"' not in home_txt:
            failures.append("R270 Home gate: Recent focus does not explicitly hide Menu focus")
        if 'self._set_recent_row_focus(None);self["selection"].show();self._home_focus_drawn_row="menu"' not in home_txt:
            failures.append("R270 Home gate: Menu focus does not explicitly clear Recent focus")
    except (OSError, SyntaxError) as exc:
        failures.append("R270 current-architecture gate could not inspect sources: %s" % exc)

    compat_path = os.path.join(plugin_dir, "core", "call_compat.py")
    with open(compat_path, "r", encoding="utf-8") as handle:
        compat_src = handle.read()
    try:
        compat_tree = ast.parse(compat_src, filename=compat_path)
    except SyntaxError as exc:
        return failures + ["call_compat.py does not parse: %s" % (exc,)]
    compat_funcs = {node.name: node for node in ast.walk(compat_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    call_node = compat_funcs.get("call_compatible")
    if call_node is None:
        failures.append("core.call_compat.call_compatible is missing")
    else:
        segment = ast.get_source_segment(compat_src, call_node) or ""
        # Signature selection must happen before invocation. inspect.signature is
        # the mechanism used by this compatibility helper to avoid retrying a
        # callable after an internal TypeError.
        if "signature" not in segment or "bind" not in segment:
            failures.append("call_compatible no longer pre-binds candidate signatures")

    return failures


def _critical_suite(python_root):
    tests_dir=os.path.join(os.path.dirname(__file__),"tests")
    if not os.path.isdir(tests_dir):return None
    if python_root not in sys.path:
        sys.path.insert(0, python_root)

    from Plugins.Extensions.UltraStalker.tests.test_download_path_security import DownloadPathSecurityTests
    from Plugins.Extensions.UltraStalker.tests.test_call_compat import CallCompatTests

    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DownloadPathSecurityTests))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(CallCompatTests))
    return suite


def main():
    plugin_dir, python_root = _paths()
    failures = _static_gate(plugin_dir)
    if failures:
        print("RELEASE GATE: FAIL")
        for item in failures:
            print(" - %s" % item)
        return 1

    suite=_critical_suite(python_root)
    if suite is None:
        print("RELEASE GATE: PASS (release-clean package: focused tests not packaged; static gates passed)")
        return 0
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        print("RELEASE GATE: FAIL")
        return 1
    print("RELEASE GATE: PASS (download path + TypeError compatibility)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
