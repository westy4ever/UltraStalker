# -*- coding: utf-8 -*-
"""Ultra Stalker ownership/provenance release gate.

Static release hygiene only. This gate does not alter runtime behavior.
"""
from __future__ import print_function

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.dirname(__file__))


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return ""


def _failures():
    failures = []

    required = (
        "COPYRIGHT.txt",
        "THIRD_PARTY_NOTICES.txt",
        os.path.join("webqr", "LICENSE.python-qrcode.txt"),
        "FONT_AWESOME_FREE_LICENSE.txt",
        "version.py",
        "FINAL_BUILD_TYPE.txt",
    )
    for rel in required:
        if not os.path.isfile(os.path.join(PLUGIN_DIR, rel)):
            failures.append("missing ownership/license file: %s" % rel)

    copyright_text = _read(os.path.join(PLUGIN_DIR, "COPYRIGHT.txt"))
    if "Copyright (c) 2026 Ahmed L-HadarY" not in copyright_text:
        failures.append("Ultra Stalker copyright identity missing")
    if "Third-party components" not in copyright_text:
        failures.append("third-party exclusion missing from copyright notice")

    notices = _read(os.path.join(PLUGIN_DIR, "THIRD_PARTY_NOTICES.txt"))
    for marker in ("webqr/LICENSE.python-qrcode.txt", "FONT_AWESOME_FREE_LICENSE.txt"):
        if marker not in notices:
            failures.append("third-party notice missing: %s" % marker)

    version_text = _read(os.path.join(PLUGIN_DIR, "version.py"))
    build = None
    build_name = ""
    try:
        scope = {}
        exec(compile(version_text, os.path.join(PLUGIN_DIR, "version.py"), "exec"), scope, scope)
        build = int(scope.get("PLUGIN_BUILD"))
        build_name = str(scope.get("BUILD_NAME") or "")
    except Exception as exc:
        failures.append("cannot parse version.py ownership metadata: %s" % exc)
    if build is not None:
        if build < 165:
            failures.append("version.py PLUGIN_BUILD is below the supported feature baseline")
        if build_name != "Ultra Stalker Final V9.1.1":
            failures.append("version.py BUILD_NAME is not the Final V9.1.1 identity")
        final_build = _read(os.path.join(PLUGIN_DIR, "FINAL_BUILD_TYPE.txt")).strip()
        if final_build != "Ultra Stalker Final V9.1.1":
            failures.append("FINAL_BUILD_TYPE is not the Final V9.1.1 identity")


    return failures


def main():
    failures = _failures()
    if failures:
        print("OWNERSHIP GATE: FAIL")
        for item in failures:
            print(" - %s" % item)
        return 1
    print("OWNERSHIP GATE: PASS")
    print(" - Ultra Stalker copyright notice present")
    print(" - third-party license notices retained")
    print(" - build metadata coherent with current version.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
