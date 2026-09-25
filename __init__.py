# -*- coding: utf-8 -*-
"""Ultra Stalker package localization entry point.

The plugin language is intentionally independent from Enigma2's global
language. Only strings explicitly marked with ``_()`` are translated; provider
content is left byte-for-byte/display-for-display as supplied by the source.
"""
from .localization import translate as _, current_language, language_name, set_plugin_language

__all__ = ["_", "current_language", "language_name", "set_plugin_language"]
