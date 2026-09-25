# -*- coding: utf-8 -*-
"""Ultra Stalker localization closure gate.

Fails release validation when plugin-owned UI literals are missing from the
master catalog, a language pack is incomplete/stale, formatting contracts are
broken, or a newly introduced translation silently remains identical to the
English source without explicit review.
"""
from __future__ import print_function
import ast
import json
import os
import re
import sys
from html.parser import HTMLParser

PLUGIN_DIR = os.path.abspath(os.path.dirname(__file__))
PYTHON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PLUGIN_DIR)))
if PYTHON_ROOT not in sys.path:
    sys.path.insert(0, PYTHON_ROOT)

from Plugins.Extensions.UltraStalker.localization import _T
from Plugins.Extensions.UltraStalker.language_catalog import installed_interface_language_codes, pack_path
from Plugins.Extensions.UltraStalker.webcleaner import _WEB_I18N_DONORS

_PLACEHOLDER_RE = re.compile(r'%(?:\([^)]+\))?[#0 +\-]?(?:\d+|\*)?(?:\.\d+)?[diouxXeEfFgGcrs%]')
_UI_CALLS = set(("Label", "StaticText", "MessageBox", "ChoiceBox", "InputBox"))
_UI_METHODS = set(("setText", "setTitle", "setMessage", "setLabel", "setDescription", "setTitleText", "_context_open_notice", "_context_open_choice", "_open_glass_notice", "_open_glass_choice"))
_REMOTE_UI_LABELS = frozenset(("MENU", "BACK", "OK", "GREEN", "RED", "YELLOW", "BLUE", "UP", "DOWN", "LEFT", "RIGHT", "ARROWS", "EXIT"))
_NONLATIN_CODES = frozenset(("ar", "bg", "el", "fa", "ja", "ko", "ru", "sr", "uk", "zh-cn", "zh-tw"))
# Functional UI words that must not survive untranslated inside a non-Latin pack.
# Product/technology names (TMDb, Xtream, M3U, HLS, HTTPS, Python, etc.) are
# deliberately absent from this set.
_NONLATIN_FUNCTIONAL_ENGLISH = frozenset((
    "web", "cleaner", "hero", "live", "bouquet", "smart", "home", "engine",
    "catch-up", "catchup", "cache", "fallback", "player", "proxy", "online",
    "cinematic", "deep", "check", "clean", "names", "artwork", "backdrop",
    "poster", "grid", "recovery", "receiver", "browser", "autoplay", "replay",
    "adaptive", "glass", "resume", "build", "download", "downloads", "current",
    "ready", "remaining", "unavailable", "start", "export", "information",
    "director", "cast", "metadata", "archive", "season", "episode", "confirm",
    "expires", "english"
))


_DYNAMIC_TRANSLATION_KEYS = (
    "White", "Cream", "Yellow", "Gold", "Orange", "Lime", "Green", "Mint",
    "Cyan", "Sky Blue", "Blue", "Lavender", "Purple", "Pink", "Coral",
    "Very High", "High", "Upper", "Lower", "Low", "Very Low",
    "4:3 Letterbox", "4:3 PanScan", "16:9", "16:9 Always", "16:10 Letterbox",
    "16:10 PanScan", "16:9 Letterbox",
)
_WEB_TECHNICAL_ALLOW = frozenset(("Ultra Stalker", "Xtream", "XTREAM", "M3U"))
_RAW_VISIBLE_INTENTIONAL = frozenset(("IMDb  %s", "\nAhmed L-HadarY"))


class _WebVisibleParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.skip = 0
        self.values = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        for key, value in attrs:
            if key in ("placeholder", "title", "aria-label") and value:
                self.values.append(str(value).strip())
    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if self.skip:
            return
        value = str(data or "").strip()
        if value:
            self.values.append(value)


def _web_static_strings():
    path = os.path.join(PLUGIN_DIR, "web", "cleaner.html")
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    parser = _WebVisibleParser()
    parser.feed(source)
    return set(parser.values), source


def _load_translations(code):
    if code in ("ar", "de", "fr", "tr"):
        return dict((key, str((value or {}).get(code) or "")) for key, value in _T.items())
    path = pack_path(code)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return dict(data.get("translations") or {})


def _remote_fragment_only(text):
    """True when a raw composition fragment is only a physical remote label."""
    words = re.findall(r"[A-Za-z][A-Za-z-]*", str(text or ""))
    return bool(words) and all(word.upper() in _REMOTE_UI_LABELS for word in words)


def _inside_translation(node, parents, stop):
    current = node
    while current is not None and current is not stop:
        parent = parents.get(current)
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == "_" and parent.args and parent.args[0] is current:
            return True
        current = parent
    return False



def _rendered_literal_nodes(expr):
    """Yield raw literal fragments that can directly contribute to one UI string.

    Deliberately do not descend into lookup keys, function arguments, or other
    implementation data just because their AST happens to sit below the sink.
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        yield expr
        return
    if isinstance(expr, ast.BinOp):
        if isinstance(expr.op, ast.Add):
            for item in _rendered_literal_nodes(expr.left):
                yield item
            for item in _rendered_literal_nodes(expr.right):
                yield item
        elif isinstance(expr.op, ast.Mod):
            # Only the left side is the format template. The right side is data.
            for item in _rendered_literal_nodes(expr.left):
                yield item
        return
    if isinstance(expr, ast.JoinedStr):
        for value in expr.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield value
            elif isinstance(value, ast.FormattedValue):
                for item in _rendered_literal_nodes(value.value):
                    yield item
        return
    if isinstance(expr, ast.IfExp):
        for item in _rendered_literal_nodes(expr.body):
            yield item
        for item in _rendered_literal_nodes(expr.orelse):
            yield item
        return
    if isinstance(expr, ast.BoolOp):
        for value in expr.values:
            for item in _rendered_literal_nodes(value):
                yield item
        return
    if isinstance(expr, ast.Call):
        # _("...") is handled by the ancestor check. For "...".format(...),
        # only the receiver is the visible template; format arguments are data.
        if isinstance(expr.func, ast.Attribute) and expr.func.attr == "format":
            for item in _rendered_literal_nodes(expr.func.value):
                yield item
        elif isinstance(expr.func, ast.Name) and expr.func.id == "_" and expr.args:
            for item in _rendered_literal_nodes(expr.args[0]):
                yield item
        return

def _literal_calls_and_raw_ui():
    used = set()
    raw = []
    composed = []
    for root, dirs, files in os.walk(PLUGIN_DIR):
        if os.path.sep + "webqr" in root:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, PLUGIN_DIR)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
            except Exception as exc:
                raw.append((rel, 0, "AST", str(exc)))
                continue
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        used.add(arg.value)
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                else:
                    continue
                if call_name not in _UI_CALLS and call_name not in _UI_METHODS:
                    continue
                values = list(node.args) + [kw.value for kw in node.keywords if kw.arg in ("title", "text", "message", "subtitle")]
                for value in values:
                    # Inspect every string fragment contributing directly to a guarded
                    # visible UI sink. This catches "BACK " + _("Categories"), f-strings,
                    # and .format() constructions instead of only bare constants.
                    literals = list(_rendered_literal_nodes(value))
                    for literal in literals:
                        text = literal.value
                        if not re.search(r"[A-Za-z]{2,}", text):
                            continue
                        if _inside_translation(literal, parents, node):
                            continue
                        if text in _RAW_VISIBLE_INTENTIONAL:
                            continue
                        if literal is value:
                            raw.append((rel, getattr(node, "lineno", 0), call_name, text))
                        elif _remote_fragment_only(text):
                            continue
                        else:
                            composed.append((rel, getattr(node, "lineno", 0), call_name, text))
    return used, raw, composed


def _master_literal_duplicates():
    """Find duplicate literal keys across _T = {...} and _T.update({...})."""
    path = os.path.join(PLUGIN_DIR, "localization.py")
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    seen = {}
    duplicates = []

    def collect(mapping):
        for key_node in mapping.keys:
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                continue
            key = key_node.value
            line = getattr(key_node, "lineno", 0)
            if key in seen:
                duplicates.append((key, seen[key], line))
            else:
                seen[key] = line

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_T" for target in node.targets) and isinstance(node.value, ast.Dict):
            collect(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update" and isinstance(node.func.value, ast.Name) and node.func.value.id == "_T" and node.args and isinstance(node.args[0], ast.Dict):
            collect(node.args[0])
    return seen, duplicates


def _functional_english_tokens(text):
    tokens = set(word.lower() for word in re.findall(r"[A-Za-z][A-Za-z-]*", str(text or "")))
    return sorted(tokens & _NONLATIN_FUNCTIONAL_ENGLISH)


def main():
    failures = []
    master = set(_T)
    used, raw_ui, composed_ui = _literal_calls_and_raw_ui()
    master_literal_defs, duplicate_master = _master_literal_duplicates()
    for key, first_line, again_line in duplicate_master:
        failures.append("duplicate master translation key: %r at localization.py:%d and :%d" % (key, first_line, again_line))
    if set(master_literal_defs) != master:
        failures.append("master literal-definition audit does not match runtime catalog (%d literal / %d runtime)" % (len(master_literal_defs), len(master)))
    for key in _DYNAMIC_TRANSLATION_KEYS:
        if key not in master:
            failures.append("dynamic UI translation key missing from master: %r" % key)
    for source, donor in sorted(_WEB_I18N_DONORS.items()):
        if donor not in master:
            failures.append("Web Cleaner donor key missing from master: %r -> %r" % (source, donor))
    try:
        web_static, web_source = _web_static_strings()
        for text in sorted(web_static):
            if not re.search(r"[A-Za-z]{2,}", text):
                continue
            if text in _WEB_TECHNICAL_ALLOW or text.startswith("http://") or text.startswith("https://") or re.match(r"^[0-9A-FX:.-]+$", text):
                continue
            if text not in _WEB_I18N_DONORS:
                failures.append("Web Cleaner visible text lacks localization mapping: %r" % text)
        for literal in re.findall(r"textContent\s*=\s*['\"]([^'\"]*[A-Za-z]{2,}[^'\"]*)['\"]", web_source):
            if literal in _WEB_TECHNICAL_ALLOW:
                continue
            if literal not in _WEB_I18N_DONORS:
                failures.append("Web Cleaner dynamic text lacks localization mapping: %r" % literal)
        if "e.message" in web_source and re.search(r"textContent\s*=.*e\.message", web_source):
            failures.append("Web Cleaner exposes raw backend/browser error text to UI")
        if "x.detail" in web_source or "proof: ${" in web_source:
            failures.append("Web Cleaner exposes raw validation detail/proof labels to UI")
    except Exception as exc:
        failures.append("cannot validate Web Cleaner localization: %s" % exc)
    for key in sorted(used - master):
        failures.append("missing master translation key: %r" % key)
    for rel, line, call_name, text in raw_ui:
        failures.append("raw English UI literal bypasses translator: %s:%s %s %r" % (rel, line, call_name, text))
    for rel, line, call_name, text in composed_ui:
        failures.append("concatenated/composed English UI fragment bypasses translator: %s:%s %s %r" % (rel, line, call_name, text))

    allow_path = os.path.join(PLUGIN_DIR, "LOCALIZATION_SAME_SOURCE_ALLOWLIST.json")
    try:
        with open(allow_path, "r", encoding="utf-8") as handle:
            allow = (json.load(handle) or {}).get("languages") or {}
    except Exception as exc:
        failures.append("cannot load same-source localization allowlist: %s" % exc)
        allow = {}

    codes = tuple(installed_interface_language_codes())
    if len(codes) != 29:
        failures.append("expected 29 interface languages, got %d" % len(codes))

    for code in codes:
        if code == "en":
            continue
        try:
            tr = _load_translations(code)
        except Exception as exc:
            failures.append("cannot load language %s: %s" % (code, exc))
            continue
        missing = sorted(master - set(tr))
        extra = sorted(set(tr) - master)
        if missing:
            failures.append("%s missing %d key(s): %s" % (code, len(missing), ", ".join(repr(x) for x in missing[:4])))
        if extra:
            failures.append("%s has %d stale/extra key(s): %s" % (code, len(extra), ", ".join(repr(x) for x in extra[:4])))
        reviewed_same = set(allow.get(code) or ())
        stale_review = sorted(reviewed_same - master)
        if stale_review:
            failures.append("%s same-source allowlist has stale key(s): %s" % (code, ", ".join(repr(x) for x in stale_review[:4])))
        for reviewed_key in sorted(reviewed_same & master):
            if str(tr.get(reviewed_key, "")) != reviewed_key:
                failures.append("%s same-source allowlist entry is no longer identical and must be removed: %r" % (code, reviewed_key))
        for key in master:
            value = str(tr.get(key, ""))
            if not value.strip():
                failures.append("%s empty translation: %r" % (code, key))
                continue
            if _PLACEHOLDER_RE.findall(key) != _PLACEHOLDER_RE.findall(value):
                failures.append("%s placeholder mismatch: %r" % (code, key))
            if key.count("\n") != value.count("\n"):
                failures.append("%s newline mismatch: %r" % (code, key))
            if value == key and key not in reviewed_same:
                failures.append("%s unreviewed English-identical translation: %r" % (code, key))
            if code in _NONLATIN_CODES:
                leaked = _functional_english_tokens(value)
                if leaked:
                    failures.append("%s functional English leaked into translation %r: %s" % (code, key, ", ".join(leaked)))

    # Settings organization contract: language controls live together and only
    # those two controls are present in the dedicated section.
    settings_path = os.path.join(PLUGIN_DIR, "ui_screens_settings.py")
    try:
        with open(settings_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        match = re.search(r'"language_metadata"\s*:\s*\[(.*?)\n\s*\],\n\s*"parental"\s*:', source, re.S)
        if not match:
            failures.append("Language & Metadata settings section missing")
        else:
            block = match.group(1)
            action_fields = re.findall(r',\s*"([a-z0-9_]+)"\s*,', block)
            if sorted(action_fields) != ["description_language", "plugin_language"]:
                failures.append("Language & Metadata section contains unexpected settings: %r" % action_fields)
        # Neither control may remain in its former sections.
        search_match = re.search(r'"search"\s*:\s*\[(.*?)\n\s*\],\n\s*"language_metadata"\s*:', source, re.S)
        appearance_match = re.search(r'"appearance"\s*:\s*\[(.*?)\n\s*\],\n\s*"storage"\s*:', source, re.S)
        if search_match and '"description_language"' in search_match.group(1):
            failures.append("Description Language still appears under Search & Metadata")
        if appearance_match and '"plugin_language"' in appearance_match.group(1):
            failures.append("Interface Language still appears under Appearance")
    except Exception as exc:
        failures.append("cannot validate settings language section: %s" % exc)

    if failures:
        print("LOCALIZATION GATE: FAIL")
        for item in failures:
            print(" - %s" % item)
        return 1
    print("LOCALIZATION GATE: PASS")
    print(" - %d master UI strings" % len(master))
    print(" - %d interface languages" % len(codes))
    print(" - literal _() source coverage complete")
    print(" - language packs exact/non-empty with placeholders/newlines preserved")
    print(" - English-identical strings limited to reviewed intentional cases")
    print(" - no direct or composed raw English UI literals in guarded GUI call sites")
    print(" - duplicate master keys: 0; same-source allowlist is exact and reviewed")
    print(" - non-Latin packs contain no guarded functional-English leakage")
    print(" - dynamic subtitle/aspect labels are contract-checked")
    print(" - Web Cleaner static/dynamic UI is mapped to the same 29-language catalog")
    print(" - Language & Metadata section contains exactly two controls")
    return 0

if __name__ == "__main__":
    sys.exit(main())
