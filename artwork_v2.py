# -*- coding: utf-8 -*-
"""Lazy compatibility facade for the Ultra Stalker artwork v2 engine.

FINAL LAZY ARTWORK FACADE
-------------------------
The full artwork resolver is intentionally large and expensive to import on
receiver-class CPUs.  Historically every Ultra Stalker cold start imported the
whole engine even when the user only opened Home/Settings/Live categories.

Keep this public module path stable for all existing callers, but defer the real
implementation until one of its APIs is actually used.  No artwork policy,
cache layout, network behavior or resolver code is changed; the former module
lives byte-for-byte as ``artwork_v2_impl.py``.
"""
from __future__ import absolute_import

import importlib
import threading

_IMPL = None
_IMPL_LOCK = threading.RLock()


def _impl():
    global _IMPL
    mod = _IMPL
    if mod is not None:
        return mod
    with _IMPL_LOCK:
        mod = _IMPL
        if mod is None:
            mod = importlib.import_module(".artwork_v2_impl", __package__)
            _IMPL = mod
        return mod


class ArtworkV2(object):
    """Construction proxy preserving the historical ``ArtworkV2(...)`` API."""
    def __new__(cls, *args, **kwargs):
        return _impl().ArtworkV2(*args, **kwargs)


def load_manifest(*args, **kwargs):
    return _impl().load_manifest(*args, **kwargs)


def load_fast_local_poster(*args, **kwargs):
    return _impl().load_fast_local_poster(*args, **kwargs)


def load_manual_rescue_art(*args, **kwargs):
    return _impl().load_manual_rescue_art(*args, **kwargs)


def canonical_art_paths(*args, **kwargs):
    return _impl().canonical_art_paths(*args, **kwargs)


def identity_cache_compatible(*args, **kwargs):
    return _impl().identity_cache_compatible(*args, **kwargs)


def save_manifest(*args, **kwargs):
    return _impl().save_manifest(*args, **kwargs)


def save_manual_rescue_art(*args, **kwargs):
    return _impl().save_manual_rescue_art(*args, **kwargs)


def _valid_backdrop(*args, **kwargs):
    return _impl()._valid_backdrop(*args, **kwargs)


def _download(*args, **kwargs):
    return _impl()._download(*args, **kwargs)


def shutdown_artwork_workers(*args, **kwargs):
    # Preserve runtime-manager shutdown semantics without forcing the heavy
    # implementation to import solely because Enigma2 is closing.
    if _IMPL is None:
        return True
    return _IMPL.shutdown_artwork_workers(*args, **kwargs)


def __getattr__(name):
    # Compatibility escape hatch for any external/custom skin helper importing
    # a less common artwork_v2 symbol.  Accessing it correctly activates the
    # original implementation rather than silently changing behavior.
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_impl(), name)


__all__ = (
    "ArtworkV2", "load_manifest", "load_fast_local_poster", "load_manual_rescue_art",
    "canonical_art_paths", "identity_cache_compatible", "save_manifest",
    "save_manual_rescue_art", "shutdown_artwork_workers",
)
