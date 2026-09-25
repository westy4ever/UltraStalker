# -*- coding: utf-8 -*-
"""Receiver video-mode helpers owned by Ultra Stalker.

This module keeps receiver aspect handling in one place so UI and Player do not
carry duplicate platform-specific logic.  It talks only to Enigma2 receiver
interfaces and procfs fallbacks exposed by the image.
"""

import os


_ASPECT_CHOICES = (
    (0, "4:3 Letterbox"),
    (1, "4:3 PanScan"),
    (2, "16:9"),
    (3, "16:9 Always"),
    (4, "16:10 Letterbox"),
    (5, "16:10 PanScan"),
    (6, "16:9 Letterbox"),
)
_ASPECT_LABELS = dict(_ASPECT_CHOICES)
_ASPECT_ORDER = tuple(code for code, _label in _ASPECT_CHOICES)

# Procfs values are strings, while Enigma2's setter/getter uses integer mode
# identifiers.  Keep the translation data declarative and independent from
# player/UI state.
_PROC_MODE_TO_CODE = {
    ("4:3", "letterbox"): 0,
    ("4:3", "panscan"): 1,
    ("16:9", "default"): 2,
    ("16:9", "panscan"): 3,
    ("16:10", "letterbox"): 4,
    ("16:10", "panscan"): 5,
    ("16:9", "letterbox"): 6,
}


def _switch_class(explicit=None):
    if explicit is not None:
        return explicit
    try:
        from enigma import eAVSwitch
        return eAVSwitch
    except Exception:
        try:
            from enigma import eAVControl
            return eAVControl
        except Exception:
            return None


def _read_proc_text(path):
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except Exception:
        return ""


def _proc_aspect_code():
    aspect = _read_proc_text("/proc/stb/video/aspect")
    if not aspect:
        return None
    policy = _read_proc_text("/proc/stb/video/policy").lower()
    if aspect == "16:9" and policy not in ("letterbox", "panscan"):
        policy = "default"
    return _PROC_MODE_TO_CODE.get((aspect, policy))


def capture_aspect_mode(switch_cls=None):
    """Return the active Enigma2 aspect-mode code, or ``None`` if unknown.

    Prefer procfs when the image exposes it because that is the receiver's
    concrete current policy.  Fall back to the Enigma2 AV switch getter only
    when procfs cannot describe the active mode.
    """
    proc_value = _proc_aspect_code()
    if proc_value in _ASPECT_LABELS:
        return proc_value

    cls = _switch_class(switch_cls)
    if cls is not None:
        try:
            inst = cls.getInstance()
            getter = getattr(inst, "getAspectRatio", None)
            if callable(getter):
                value = int(getter())
                if value in _ASPECT_LABELS:
                    return value
        except Exception:
            pass
    return None


def apply_aspect_mode(value, switch_cls=None):
    """Apply one validated aspect-mode code through the receiver API."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return False
    if code not in _ASPECT_LABELS:
        return False
    cls = _switch_class(switch_cls)
    if cls is None:
        return False
    try:
        cls.getInstance().setAspectRatio(code)
        return True
    except Exception:
        return False


def restore_aspect_mode(value, switch_cls=None):
    if value is None:
        return False
    return apply_aspect_mode(value, switch_cls=switch_cls)


def next_aspect_mode(previous=None):
    """Return the next mode in Ultra Stalker's explicit display-mode cycle."""
    if previous not in _ASPECT_ORDER:
        return _ASPECT_ORDER[0]
    index = _ASPECT_ORDER.index(previous) + 1
    return _ASPECT_ORDER[index % len(_ASPECT_ORDER)]


def aspect_mode_label(value, default="Auto"):
    try:
        return _ASPECT_LABELS.get(int(value), default)
    except (TypeError, ValueError):
        return default
