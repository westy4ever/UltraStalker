"""Pure UI helpers extracted from ui.py without changing UI behavior."""
try:
    import colorsys
except Exception:
    from . import compat_colorsys as colorsys
import hashlib
import unicodedata

from .ui_parts.catalog import clean_display_text as _clean_display_text

ACCENT_NAMES = ("cyan", "purple", "pink", "gold", "emerald", "ruby")


def content_refresh_feedback(result, translate):
    """Return the one shared post-refresh message used by Settings and Home.

    Keeping the copy here prevents the Home green-button feedback from drifting
    away from Settings.  This helper is intentionally pure/lightweight: no UI
    objects, artwork work or provider calls happen here.
    """
    tr = translate if callable(translate) else (lambda value: value)
    data = result or {}
    source = str(data.get("source") or "Source")
    categories = data.get("categories") or {}
    total_categories = sum(int(v or 0) for v in categories.values()) if isinstance(categories, dict) else 0
    items = data.get("items") or {}
    total_items = sum(int(v or 0) for v in items.values()) if isinstance(items, dict) else 0
    if source == "Xtream":
        return tr("Xtream content refreshed. Categories are fresh now; each folder and series will fetch fresh provider data when opened.")
    if source == "M3U":
        return tr("M3U playlist refreshed from the server. %d catalogue items are ready.") % total_items
    return tr("Portal content refreshed. %d categories were reloaded; content pages and series will now open from fresh provider data.") % total_categories


def content_refresh_failure(error, translate):
    """Return the shared translated failure line for Settings and Home."""
    tr = translate if callable(translate) else (lambda value: value)
    return tr("Content refresh failed: %s") % str(error or tr("Unknown error"))

def _search_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().strip()


def _accent_for(value):
    text = str(value or "")
    return ACCENT_NAMES[int(hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:4], 16) % len(ACCENT_NAMES)]


def _two_line_title(value, line_chars=24, max_chars=52):
    clean = _clean_display_text(value, max_chars)
    if len(clean) <= line_chars:
        return clean
    cut = clean.rfind(" ", 0, line_chars + 1)
    if cut < max(8, line_chars // 2):
        cut = clean.find(" ", line_chars)
    if cut < 0:
        cut = line_chars
    first = clean[:cut].strip()
    second = clean[cut:].strip()
    if len(second) > line_chars + 3:
        second = second[:line_chars + 2].rstrip() + "…"
    return first + "\n" + second if second else first


def _letter_placeholder(value):
    # One generic fallback replaces the retired 36-file alphabet placeholder set.
    return "settings_icons_40/channel_list.png"


def _lift_dynamic_accent(rgb, lightness=0.48, saturation=0.50):
    try:
        r,g,b=[max(0,min(255,int(v)))/255.0 for v in rgb]
        h,l,s=colorsys.rgb_to_hls(r,g,b)
        s=max(0.28,min(0.66,max(s,saturation)))
        l=max(0.34,min(0.56,lightness))
        rr,gg,bb=colorsys.hls_to_rgb(h,l,s)
        return (int(rr*255),int(gg*255),int(bb*255))
    except Exception:
        return (108,128,142)


def _mix_rgb(a,b,t):
    t=max(0.0,min(1.0,float(t)))
    return tuple(int(a[i]*(1.0-t)+b[i]*t) for i in range(3))

# R48: language-agnostic text fitting for fixed glass buttons/cards.
def _fit_normalized_text(value):
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _fit_text_weight(value):
    total = 0.0
    for ch in str(value or ""):
        if ch.isspace():
            total += 0.42
        elif ord(ch) > 0x2FFF:
            total += 1.00
        elif ord(ch) > 127:
            total += 0.80
        else:
            total += 0.62
    return total


def _fit_balanced_two_lines(value):
    """Losslessly balance a localized phrase over at most two word lines."""
    text = _fit_normalized_text(value)
    parts = text.split(" ")
    if len(parts) < 2:
        return text
    best = None
    for idx in range(1, len(parts)):
        left = " ".join(parts[:idx])
        right = " ".join(parts[idx:])
        lw = _fit_text_weight(left)
        rw = _fit_text_weight(right)
        score = max(lw, rw) * 4.0 + abs(lw - rw)
        if best is None or score < best[0]:
            best = (score, left, right)
    return (best[1] + "\n" + best[2]) if best else text


def fit_label_to_box(screen, name, max_size=22, min_size=10, padding=16,
                     allow_two_lines=True, prefer_two_lines=False, height_padding=2):
    """Fit one Label inside its existing receiver geometry without truncation.

    The receiver's own calculateSize() result is authoritative.  The fallback
    estimator exists only for images where calculateSize() is temporarily zero.
    Text is never shortened; long localized wording may use two centered lines.
    """
    try:
        widget = screen[name]
        inst = widget.instance
        if inst is None:
            return None
        try:
            original = widget.getText()
        except Exception:
            original = getattr(widget, "text", "")
        value = _fit_normalized_text(original)
        if not value:
            return None
        from enigma import gFont
        box_w = max(20, int(inst.size().width()) - int(padding))
        box_h = max(10, int(inst.size().height()) - int(height_padding))

        def measure(text, size, nowrap):
            try:
                if hasattr(inst, "setNoWrap"):
                    inst.setNoWrap(1 if nowrap else 0)
            except Exception:
                pass
            widget.setText(text)
            inst.setFont(gFont("Regular", int(size)))
            try:
                calc = inst.calculateSize()
                mw, mh = int(calc.width()), int(calc.height())
            except Exception:
                mw = mh = 0
            if mw <= 0:
                longest = max((line for line in str(text).split("\n")), key=len, default="")
                mw = int(max(1.0, _fit_text_weight(longest)) * float(size))
            if mh <= 0:
                mh = int(float(size) * (2.0 if "\n" in str(text) else 1.25))
            return mw, mh

        wrapped = _fit_balanced_two_lines(value) if allow_two_lines else value
        candidates = []
        if prefer_two_lines and "\n" in wrapped:
            candidates.append((wrapped, False))
        candidates.append((value, True))
        if allow_two_lines and "\n" in wrapped and not prefer_two_lines:
            candidates.append((wrapped, False))

        for text, nowrap in candidates:
            start = int(max_size)
            stop = int(min_size)
            for size in range(start, stop - 1, -1):
                mw, mh = measure(text, size, nowrap)
                if int(mw * 1.05) <= box_w and int(mh * 1.03) <= box_h:
                    return {"text": text, "size": size, "wrapped": "\n" in text}

        # Preserve the full wording even when an exotic translation is wider
        # than expected.  The minimum size is safer than clipping characters.
        fallback = wrapped if allow_two_lines and "\n" in wrapped else value
        nowrap = "\n" not in fallback
        measure(fallback, int(min_size), nowrap)
        return {"text": fallback, "size": int(min_size), "wrapped": not nowrap}
    except Exception:
        return None


def fit_color_key_labels(screen, names=("red", "green", "yellow", "blue"),
                         max_size=22, min_size=10, padding=16):
    """Make fixed red/green/yellow/blue glass keys language-independent."""
    results = {}
    for item in names:
        if isinstance(item, (tuple, list)):
            name = item[0]
            item_max = int(item[1]) if len(item) > 1 else int(max_size)
        else:
            name = item
            item_max = int(max_size)
        try:
            screen[name]
        except Exception:
            continue
        result = fit_label_to_box(
            screen, name, max_size=item_max, min_size=min_size,
            padding=padding, allow_two_lines=True, prefer_two_lines=False,
        )
        if result:
            results[name] = result
    return results


def fit_inline_label_row(screen, names, start_x, end_x, y, height,
                         max_size=19, min_size=13, gap=8, padding=4):
    """Lay out separate localized/mixed-direction labels as one compact row.

    Each label remains directionally independent, so Arabic/Persian prefixes do
    not reorder adjacent Latin dates.  The whole row shrinks only if required.
    """
    try:
        from enigma import gFont, ePoint, eSize
        entries = []
        for name in names:
            try:
                widget = screen[name]
                inst = widget.instance
                if inst is None:
                    continue
                try:
                    text = widget.getText()
                except Exception:
                    text = getattr(widget, "text", "")
                text = str(text or "").strip()
                if text:
                    entries.append((name, widget, inst, text))
            except Exception:
                continue
        if not entries:
            return None
        available = max(40, int(end_x) - int(start_x))
        chosen = int(min_size)
        widths = []
        for size in range(int(max_size), int(min_size) - 1, -1):
            trial = []
            for _name, widget, inst, text in entries:
                try:
                    if hasattr(inst, "setNoWrap"):
                        inst.setNoWrap(1)
                except Exception:
                    pass
                inst.setFont(gFont("Regular", size))
                try:
                    calc = inst.calculateSize()
                    width = int(calc.width())
                except Exception:
                    width = 0
                if width <= 0:
                    width = int(max(1.0, _fit_text_weight(text)) * float(size))
                trial.append(max(8, width + int(padding)))
            total = sum(trial) + int(gap) * max(0, len(trial) - 1)
            widths = trial
            chosen = size
            if total <= available:
                break
        x = int(start_x)
        for (_name, _widget, inst, _text), width in zip(entries, widths):
            inst.setFont(gFont("Regular", chosen))
            inst.move(ePoint(x, int(y)))
            inst.resize(eSize(max(8, int(width)), int(height)))
            x += int(width) + int(gap)
        return {"size": chosen, "width": x - int(start_x) - int(gap)}
    except Exception:
        return None

