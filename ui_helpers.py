"""Pure UI helpers extracted from ui.py without changing UI behavior."""
import colorsys
import hashlib
import unicodedata

from .ui_parts.catalog import clean_display_text as _clean_display_text

ACCENT_NAMES = ("cyan", "purple", "pink", "gold", "emerald", "ruby")

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
    text = str(value or "?").strip().upper()
    ch = next((c for c in text if c.isalnum()), "0")
    if ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789": ch = "0"
    return "logo_letter_%s.png" % ch


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


