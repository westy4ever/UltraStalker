# -*- coding: utf-8 -*-
"""Language registry for Ultra Stalker.

Interface language packs are deliberately separate from runtime/provider data.
Built-in languages remain available without an external pack. Additional
languages become selectable only when a reviewed JSON pack exists under
``locale_packs/<code>.json``. This prevents offering a language that silently
falls back to English for most of the interface.

TMDb information languages are registered separately. They control raw movie/series
information metadata; title-logo/artwork policy remains separate.
"""
import json
import os

_BASE_DIR = os.path.dirname(__file__)
PACK_DIR = os.path.join(_BASE_DIR, "locale_packs")
PACK_SCHEMA = 1
MIN_COMPLETE_TRANSLATIONS = 1250

BUILTIN_INTERFACE_LANGUAGES = (
    ("en", "English"),
    ("ar", "العربية"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("tr", "Türkçe"),
)

# Registry of languages we can add as reviewed packs without touching runtime
# code again. Merely being listed here does NOT make a language selectable.
INTERFACE_LANGUAGE_CATALOG = (
    ("en", "English"), ("ar", "العربية"), ("de", "Deutsch"),
    ("fr", "Français"), ("tr", "Türkçe"), ("es", "Español"),
    ("it", "Italiano"), ("pt", "Português"), ("pt-br", "Português (Brasil)"),
    ("nl", "Nederlands"), ("pl", "Polski"), ("ru", "Русский"),
    ("el", "Ελληνικά"), ("cs", "Čeština"), ("sk", "Slovenčina"),
    ("hu", "Magyar"), ("ro", "Română"), ("sr", "Српски"),
    ("hr", "Hrvatski"), ("bg", "Български"), ("uk", "Українська"),
    ("sv", "Svenska"), ("no", "Norsk"), ("da", "Dansk"),
    ("fi", "Suomi"), ("zh-cn", "简体中文"), ("zh-tw", "繁體中文"),
    ("ja", "日本語"), ("ko", "한국어"), ("id", "Bahasa Indonesia"),
    ("fa", "فارسی"),
)

DESCRIPTION_LANGUAGES = (
    ("ar-en", "Arabic + English (AR + EN)"),
    ("same", "Same as Interface"),
    ("ar-EG", "العربية"), ("en-US", "English"), ("de-DE", "Deutsch"),
    ("fr-FR", "Français"), ("tr-TR", "Türkçe"), ("es-ES", "Español"),
    ("it-IT", "Italiano"), ("pt-PT", "Português"), ("pt-BR", "Português (Brasil)"),
    ("nl-NL", "Nederlands"), ("pl-PL", "Polski"), ("ru-RU", "Русский"),
    ("el-GR", "Ελληνικά"), ("cs-CZ", "Čeština"), ("sk-SK", "Slovenčina"),
    ("hu-HU", "Magyar"), ("ro-RO", "Română"), ("sr-RS", "Српски"),
    ("hr-HR", "Hrvatski"), ("bg-BG", "Български"), ("uk-UA", "Українська"),
    ("sv-SE", "Svenska"), ("nb-NO", "Norsk"), ("da-DK", "Dansk"),
    ("fi-FI", "Suomi"), ("zh-CN", "简体中文"), ("zh-TW", "繁體中文"),
    ("ja-JP", "日本語"), ("ko-KR", "한국어"), ("id-ID", "Bahasa Indonesia"),
    ("fa-IR", "فارسی"),
)

_CATALOG_NAMES = dict(INTERFACE_LANGUAGE_CATALOG)
_BUILTIN_CODES = tuple(code for code, _name in BUILTIN_INTERFACE_LANGUAGES)


def normalize_interface_code(code):
    return str(code or "en").strip().lower().replace("_", "-")


def pack_path(code):
    code = normalize_interface_code(code)
    if not code or code in _BUILTIN_CODES:
        return ""
    return os.path.join(PACK_DIR, "%s.json" % code)


def _pack_payload(code, path):
    """Return a validated structured language pack or an empty dict.

    Incomplete work-in-progress packs are deliberately not selectable.  This
    keeps the language menu honest while translations are built incrementally.
    """
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) < 64:
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}
        meta = data.get("_meta") or {}
        translations = data.get("translations") or {}
        if not isinstance(meta, dict) or not isinstance(translations, dict):
            return {}
        if int(meta.get("schema") or 0) != PACK_SCHEMA:
            return {}
        if normalize_interface_code(meta.get("code")) != normalize_interface_code(code):
            return {}
        if not bool(meta.get("complete")):
            return {}
        if len(translations) < MIN_COMPLETE_TRANSLATIONS:
            return {}
        if not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in translations.items()):
            return {}
        return {"_meta": dict(meta), "translations": dict(translations)}
    except Exception:
        return {}


def _valid_pack(code, path):
    return bool(_pack_payload(code, path))


def installed_interface_language_codes():
    codes = list(_BUILTIN_CODES)
    for code, _name in INTERFACE_LANGUAGE_CATALOG:
        if code in codes:
            continue
        if _valid_pack(code, pack_path(code)):
            codes.append(code)
    return tuple(codes)


def interface_language_choices():
    installed = set(installed_interface_language_codes())
    return [(_CATALOG_NAMES.get(code, code), code) for code, _name in INTERFACE_LANGUAGE_CATALOG if code in installed]


def interface_language_name(code):
    code = normalize_interface_code(code)
    return _CATALOG_NAMES.get(code, "English")


def description_language_choices():
    return list(DESCRIPTION_LANGUAGES)


_DESCRIPTION_CODES = frozenset(code for code, _name in DESCRIPTION_LANGUAGES)
_LEGACY_DESCRIPTION_CODES = frozenset(("current",))
_INTERFACE_TO_DESCRIPTION = {
    "en":"en-US", "ar":"ar-EG", "de":"de-DE", "fr":"fr-FR",
    "tr":"tr-TR", "es":"es-ES", "it":"it-IT", "pt":"pt-PT",
    "pt-br":"pt-BR", "nl":"nl-NL", "pl":"pl-PL", "ru":"ru-RU",
    "el":"el-GR", "cs":"cs-CZ", "sk":"sk-SK", "hu":"hu-HU",
    "ro":"ro-RO", "sr":"sr-RS", "hr":"hr-HR", "bg":"bg-BG",
    "uk":"uk-UA", "sv":"sv-SE", "no":"nb-NO", "da":"da-DK",
    "fi":"fi-FI", "zh-cn":"zh-CN", "zh-tw":"zh-TW", "ja":"ja-JP",
    "ko":"ko-KR", "id":"id-ID", "fa":"fa-IR",
}


def normalize_description_choice(value):
    raw = str(value or "ar-en").strip()
    if raw in _DESCRIPTION_CODES or raw in _LEGACY_DESCRIPTION_CODES:
        return raw
    return "ar-en"


def default_description_for_interface(interface_code="en"):
    """Return the explicit description locale paired with an interface locale.

    This is used when the user changes Interface Language: Description Language
    follows the newly selected interface once, then remains independently
    user-selectable.  AR + EN is deliberately never selected automatically.
    """
    return _INTERFACE_TO_DESCRIPTION.get(normalize_interface_code(interface_code), "en-US")


def effective_description_language(choice, interface_code="en"):
    choice = normalize_description_choice(choice)
    if choice == "current":
        return ""
    if choice == "ar-en":
        return "ar-EG"
    if choice == "same":
        return default_description_for_interface(interface_code)
    return choice


def description_language_name(choice):
    choice = normalize_description_choice(choice)
    for code, name in DESCRIPTION_LANGUAGES:
        if code == choice:
            return name
    return "Arabic + English (AR + EN)"
