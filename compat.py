# -*- coding: utf-8 -*-
"""Runtime compatibility helpers for UltraStalker.

Keep this module free of Enigma2 imports so plugin discovery can diagnose a
broken/unsupported receiver image before importing the heavy UI/runtime tree.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import sys
from typing import Final

MIN_PYTHON: Final[tuple[int, int]] = (3, 12)
MAX_TESTED_PYTHON: Final[tuple[int, int]] = (3, 15)
MAX_TESTED_PATCH: Final[tuple[int, int, int]] = (3, 15, 0)
SUPPORTED_PYTHONS: Final[tuple[tuple[int, int], ...]] = ((3, 12), (3, 13), (3, 14), (3, 15))


def python_version_tuple() -> tuple[int, int, int]:
    info = sys.version_info
    return int(info.major), int(info.minor), int(info.micro)


def _gil_enabled() -> bool:
    probe = getattr(sys, "_is_gil_enabled", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:
            return True
    return True


def python_compatibility() -> dict[str, object]:
    version = python_version_tuple()
    major_minor = version[:2]
    if major_minor < MIN_PYTHON:
        status = "unsupported-old"
        supported = False
        message = "Python 3.12, 3.13, 3.14 or 3.15 is required"
    elif major_minor in SUPPORTED_PYTHONS:
        if not _gil_enabled():
            status = "unsupported-free-threaded"
            supported = False
            message = "Free-threaded/no-GIL Python builds are not supported by Ultra Stalker on Enigma2"
        else:
            status = "supported"
            supported = True
            message = "Supported on Python 3.12, 3.13, 3.14 and 3.15 (standard GIL builds); Python 3.15 source/API compatibility audited against CPython 3.15.0rc2"
    else:
        # Future CPython is allowed but reported as untested. Source-only code
        # should not brick plugin discovery just because the image moved ahead.
        status = "untested-newer"
        supported = True
        message = "Newer Python detected; running in forward-compatible mode"
    return {
        "version": "%d.%d.%d" % version,
        "status": status,
        "supported": supported,
        "message": message,
        "tested_min": "%d.%d" % MIN_PYTHON,
        "tested_max": "%d.%d.%d" % MAX_TESTED_PATCH,
        "python314_ready": major_minor == (3, 14),
        "python315_ready": major_minor == (3, 15),
        "gil_enabled": _gil_enabled(),
    }


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def dependency_report() -> dict[str, object]:
    """Check imports that correspond to package/runtime requirements.

    sqlite3, Pillow, Twisted and the Enigma2 base modules are fatal because
    the current artwork and Details/Backdrop UI graph imports them directly.
    Keep this list aligned with the IPK Depends field so a missing feed package
    produces a clear startup error instead of a later initialization failure.
    """
    checks = {
        "sqlite3": _module_available("sqlite3"),
        "pillow": _module_available("PIL.Image"),
        "twisted": all(_module_available(name) for name in ("twisted.internet", "twisted.web.client", "twisted.web.http_headers")),
        "enigma": _module_available("enigma"),
        "components": _module_available("Components"),
        "screens": _module_available("Screens"),
        "plugins": _module_available("Plugins.Plugin"),
    }
    fatal = []
    if not checks["sqlite3"]:
        fatal.append("Python sqlite3 (package: python3-sqlite3)")
    if not checks["pillow"]:
        fatal.append("Pillow/PIL (package: python3-pillow)")
    if not checks["twisted"]:
        fatal.append("Twisted networking (package: python3-twisted)")
    if not checks["enigma"]:
        fatal.append("Enigma2 Python module (enigma)")
    if not checks["components"]:
        fatal.append("Enigma2 Components package")
    if not checks["screens"]:
        fatal.append("Enigma2 Screens package")
    if not checks["plugins"]:
        fatal.append("Enigma2 Plugins package")
    warnings = []
    return {"checks": checks, "fatal": fatal, "warnings": warnings, "ok": not fatal}


def startup_preflight() -> dict[str, object]:
    py = python_compatibility()
    deps = dependency_report()
    fatal = list(deps["fatal"])
    warnings = list(deps["warnings"])
    if not bool(py["supported"]):
        fatal.insert(0, "%s (detected Python %s)" % (py["message"], py["version"]))
    elif py["status"] == "untested-newer":
        warnings.insert(0, "%s: Python %s" % (py["message"], py["version"]))
    return {
        "ok": not fatal,
        "python": py,
        "dependencies": deps,
        "fatal": fatal,
        "warnings": warnings,
    }


def format_preflight_error(report: dict[str, object]) -> str:
    fatal = list(report.get("fatal") or [])
    lines = ["Ultra Stalker cannot start on this receiver image.", ""]
    if fatal:
        lines.append("Missing or unsupported requirements:")
        lines.extend("- %s" % item for item in fatal)
    lines.extend(("", "Install/fix the listed requirement(s), then restart Enigma2."))
    return "\n".join(lines)


def runtime_capabilities() -> dict[str, object]:
    serviceapp_paths = (
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/Extensions/ServiceApp",
    )
    return {
        "python": platform.python_version(),
        "enigma_module": _module_available("enigma"),
        "virtual_keyboard": _module_available("Screens.VirtualKeyBoard"),
        "audio_selection": _module_available("Screens.AudioSelection"),
        "subtitle_display": _module_available("Screens.SubtitleDisplay"),
        "serviceapp": any(os.path.exists(p) for p in serviceapp_paths),
        "gstplayer": os.path.exists("/usr/bin/gstplayer"),
        "exteplayer3": os.path.exists("/usr/bin/exteplayer3"),
    }


def require_supported_python() -> None:
    """Backward-compatible helper for callers outside plugin discovery."""
    result = python_compatibility()
    if not bool(result["supported"]):
        raise RuntimeError(str(result["message"]))
