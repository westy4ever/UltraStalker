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

    result = unittest.TextTestRunner(verbosity=2).run(_critical_suite(python_root))
    if not result.wasSuccessful():
        print("RELEASE GATE: FAIL")
        return 1
    print("RELEASE GATE: PASS (download path + TypeError compatibility)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
