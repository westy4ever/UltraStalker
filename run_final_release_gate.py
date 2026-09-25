# -*- coding: utf-8 -*-
"""UltraStalker final release gate.

Runs static package-tree checks plus the focused security release gates and the
full regression suite. Intended to be executed from the installed/plugin tree
before promoting an RC build to Stable.
"""
from __future__ import print_function

import compileall
import os
import stat
import subprocess
import sys
import shutil

PLUGIN_DIR = os.path.abspath(os.path.dirname(__file__))
PYTHON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PLUGIN_DIR)))
EXPECTED_VERSION = "9.1.1"



def _clean_bytecode():
    failures = []
    for root, dirs, files in os.walk(PLUGIN_DIR, topdown=False):
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                path = os.path.join(root, name)
                try:
                    os.unlink(path)
                except OSError as exc:
                    failures.append("cannot remove bytecode %s: %s" % (path, exc))
        for name in dirs:
            if name == "__pycache__":
                path = os.path.join(root, name)
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    failures.append("cannot remove __pycache__ %s: %s" % (path, exc))
    return failures

def _failures_static():
    failures = []

    # Source must compile without creating packaged bytecode.
    if not compileall.compile_dir(PLUGIN_DIR, quiet=1, force=True):
        failures.append("Python syntax/compile validation failed")

    # Remove bytecode created only for validation.
    failures.extend(_clean_bytecode())

    # No unsafe packaged file modes.
    for root, dirs, files in os.walk(PLUGIN_DIR):
        for name in dirs:
            path=os.path.join(root,name)
            try:mode=os.stat(path).st_mode
            except OSError as exc:
                failures.append("cannot stat directory %s: %s"%(path,exc));continue
            if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_IWOTH):
                failures.append("unsafe packaged directory mode: %s"%path)
        for name in files:
            path = os.path.join(root, name)
            try:
                mode = os.stat(path).st_mode
            except OSError as exc:
                failures.append("cannot stat %s: %s" % (path, exc))
                continue
            if mode & (stat.S_ISUID | stat.S_ISGID):
                failures.append("setuid/setgid packaged file: %s" % path)
            if mode & stat.S_IWOTH:
                failures.append("world-writable packaged file: %s" % path)

    # Keep runtime version metadata coherent.
    version_path = os.path.join(PLUGIN_DIR, "version.py")
    try:
        scope = {}
        with open(version_path, "r", encoding="utf-8") as handle:
            exec(compile(handle.read(), version_path, "exec"), scope, scope)
        actual = str(scope.get("PLUGIN_VERSION") or "")
        if actual != EXPECTED_VERSION:
            failures.append("version.py mismatch: expected %s, got %s" % (EXPECTED_VERSION, actual or "<missing>"))
    except Exception as exc:
        failures.append("cannot validate version.py: %s" % exc)

    # Required release tooling must ship with the package.
    for name in ("run_release_gates.py", "run_tests.py", "run_receiver_soak_check.py", "run_ownership_gate.py"):
        if not os.path.isfile(os.path.join(PLUGIN_DIR, name)):
            failures.append("missing release tool: %s" % name)

    # V7 core guarantees: online updater and LAN web surface must survive every
    # public update. Free Portal/Xtream catalogs are persistent user/runtime
    # data and this package may intentionally omit replacement payloads.
    for name in ("updater.py", "webcleaner.py"):
        if not os.path.isfile(os.path.join(PLUGIN_DIR, name)):
            failures.append("missing V7 core component: %s" % name)

    # R270 catalog packaging policy: both real catalogue payloads are part of
    # the release baseline.  Missing/header-only files are a hard failure because
    # the installer intentionally refreshes these two built-in libraries.
    package_root = os.path.abspath(os.path.join(PLUGIN_DIR, "../../../../../../.."))
    required_catalog_payloads = (
        (os.path.join(package_root, "etc/enigma2/ultrastalker/.uslib/.catalog_a"), 20000, "Free Portal Library"),
        (os.path.join(package_root, "etc/enigma2/ultrastalker/.uslib/.catalog_b"), 300000, "Free Xtream Library"),
    )
    for path, min_size, label in required_catalog_payloads:
        try:
            if not os.path.isfile(path):
                failures.append("missing bundled %s" % label)
            elif os.path.getsize(path) < min_size:
                failures.append("truncated bundled %s" % label)
        except OSError:
            failures.append("cannot validate bundled %s" % label)

    return failures


def _run(script):
    path = os.path.join(PLUGIN_DIR, script)
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = PYTHON_ROOT + (os.pathsep + current if current else "")
    return subprocess.call([sys.executable, path], env=env)


def main():
    failures = _failures_static()
    if failures:
        print("FINAL RELEASE GATE: FAIL")
        for item in failures:
            print(" - %s" % item)
        return 1

    if _run("run_ownership_gate.py") != 0:
        print("FINAL RELEASE GATE: FAIL (ownership/provenance gate)")
        return 1

    if _run("run_release_gates.py") != 0:
        print("FINAL RELEASE GATE: FAIL (focused security gates)")
        return 1

    if _run("run_r161_gate.py") != 0:
        print("FINAL RELEASE GATE: FAIL (R161 Search history gate)")
        return 1

    if _run("run_r164_gate.py") != 0:
        print("FINAL RELEASE GATE: FAIL (R164 federated Search/Web/Home gate)")
        return 1

    if _run("run_r165_gate.py") != 0:
        print("FINAL RELEASE GATE: FAIL (Xtream category Search gate)")
        return 1

    if _run("run_localization_gate.py") != 0:
        print("FINAL RELEASE GATE: FAIL (localization completeness gate)")
        return 1

    if _run("run_tests.py") != 0:
        print("FINAL RELEASE GATE: FAIL (full regression suite)")
        return 1

    cleanup_failures = _clean_bytecode()
    if cleanup_failures:
        print("FINAL RELEASE GATE: FAIL (post-test bytecode cleanup)")
        for item in cleanup_failures:
            print(" - %s" % item)
        return 1

    print("FINAL RELEASE GATE: PASS")
    print(" - version metadata coherent: %s" % EXPECTED_VERSION)
    print(" - Python source compile validation passed")
    print(" - no packaged bytecode / unsafe file modes")
    print(" - Final V9.1.1 Free Portal / Free Xtream catalogue payloads are present and non-truncated")
    print(" - ownership/provenance gate passed")
    print(" - focused security gates passed")
    print(" - current Search-history / Home-recent contract gate passed")
    print(" - current federated Search / Web manager / Home hierarchy gate passed")
    print(" - Xtream category-id progressive Search gate passed")
    print(" - localization completeness gate passed")
    print(" - regression tool passed (full suite pre-package; release-clean package self-check when tests are intentionally omitted)")
    print(" - receiver soak remains a deployment-time gate; run run_receiver_soak_check.py after real hardware soak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
