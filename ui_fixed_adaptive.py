# -*- coding: utf-8 -*-
"""Frozen application chrome copied from the user's approved Home adaptive.

R63 deliberately does *not* invent a colour or re-sample the screenshot.  The
installer copies the already-generated Home mood/focus PNGs byte-for-byte before
Enigma2 restarts.  Runtime screens then reuse those persistent files, and the
single Settings/Portal row pair is generated once by the existing approved row
builder from the exact frozen adaptive source and copied into the same persistent
bundle.
"""
from __future__ import absolute_import

import json
import os
import shutil
import tempfile
import threading
import glob

from .persistent_cache import ROOT, ensure_persistent_dirs
from .log import optional_failure

FIXED_DIR = os.path.join(ROOT, "fixed_ui_adaptive_r63")
MANIFEST = os.path.join(FIXED_DIR, "manifest.json")
BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "assets_fhd", "fixed_master_r63")
BUNDLED_MANIFEST = os.path.join(BUNDLED_DIR, "manifest.json")
_LOCK = threading.RLock()


def _valid(path, minimum=64):
    try:
        return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path)) >= int(minimum))
    except Exception:
        return False


def _read_manifest_raw():
    try:
        if not _valid(MANIFEST, 16):
            return {}
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _bundled_manifest_raw():
    try:
        if not _valid(BUNDLED_MANIFEST, 16):
            return {}
        with open(BUNDLED_MANIFEST, "r", encoding="utf-8") as fh:
            data=json.load(fh)
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}

def _bundled_bundle():
    """Exact R63 master shipped in-package; available from first install/paint."""
    try:
        names={
            "source":"adaptive_source.jpg",
            "ambient":"home_ambient.png",
            "menu":"home_menu.png",
            "recent":"home_recent.png",
            "focus_menu":"home_menu_focus.png",
            "focus_recent":"home_recent_focus.png",
        }
        paths={k:os.path.join(BUNDLED_DIR,v) for k,v in names.items()}
        if not all(_valid(x,64) for x in paths.values()):
            return {}
        meta=_bundled_manifest_raw()
        data={
            "schema":1,
            "hero_id":"bundled-r63",
            "source":paths["source"],
            "mood":{"ambient":paths["ambient"],"menu":paths["menu"],"recent":paths["recent"]},
            "focus":{"menu":paths["focus_menu"],"recent":paths["focus_recent"]},
        }
        if meta.get("value_color") is not None:
            data["value_color"]=int(meta.get("value_color"))
        return data
    except Exception:
        return {}


def _atomic_manifest(data):
    try:
        ensure_persistent_dirs(FIXED_DIR)
        fd, tmp = tempfile.mkstemp(prefix=".fixed-ui-", suffix=".json", dir=FIXED_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush(); os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, MANIFEST)
        finally:
            if os.path.exists(tmp):
                try: os.unlink(tmp)
                except OSError: pass
        return True
    except Exception as exc:
        optional_failure("ui.fixed_adaptive_manifest", exc)
        return False


def _copy_exact(src, dst):
    """Byte-for-byte copy; no decode, palette extraction or image rewrite."""
    if not _valid(src, 64):
        return ""
    try:
        ensure_persistent_dirs(FIXED_DIR)
        tmp = dst + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        with open(src, "rb") as rf, open(tmp, "wb") as wf:
            shutil.copyfileobj(rf, wf, 1024 * 1024)
            wf.flush(); os.fsync(wf.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dst)
        return dst if _valid(dst, 64) else ""
    except Exception as exc:
        optional_failure("ui.fixed_adaptive_copy", exc)
        try:
            if os.path.exists(tmp): os.unlink(tmp)
        except Exception:
            pass
        return ""


def _bundle_valid(data):
    if not isinstance(data, dict) or int(data.get("schema") or 0) != 1:
        return False
    mood = data.get("mood") if isinstance(data.get("mood"), dict) else {}
    focus = data.get("focus") if isinstance(data.get("focus"), dict) else {}
    required = [mood.get("ambient"), mood.get("menu"), mood.get("recent"), focus.get("menu"), focus.get("recent"), data.get("source")]
    return all(_valid(p, 64) for p in required)


def load_fixed_bundle():
    with _LOCK:
        # Package master is the immutable visual authority. HDD copy is only a
        # compatibility/persistence mirror and can disappear without UI flicker.
        bundled=_bundled_bundle()
        if _bundle_valid(bundled):
            return bundled
        data = _read_manifest_raw()
        return data if _bundle_valid(data) else {}


def fixed_home_assets():
    data = load_fixed_bundle()
    if not data:
        return {}
    return {
        "mood": dict(data.get("mood") or {}),
        "focus": dict(data.get("focus") or {}),
        "source": str(data.get("source") or ""),
    }


def fixed_adaptive_source():
    data = load_fixed_bundle()
    return str(data.get("source") or "") if data else ""


def capture_from_parts(source, mood, focus, hero_id=""):
    """Freeze the *existing* approved outputs once, then never replace them."""
    with _LOCK:
        current = _read_manifest_raw()
        if _bundle_valid(current):
            return current
        mood = mood if isinstance(mood, dict) else {}
        focus = focus if isinstance(focus, dict) else {}
        required = [mood.get("ambient"), mood.get("menu"), mood.get("recent"), focus.get("menu"), focus.get("recent"), source]
        if not all(_valid(p, 64) for p in required):
            return {}
        try:
            ensure_persistent_dirs(FIXED_DIR)
            source_ext = os.path.splitext(str(source))[1].lower()
            if source_ext not in (".jpg", ".jpeg", ".png", ".webp"):
                source_ext = ".img"
            paths = {
                "ambient": os.path.join(FIXED_DIR, "home_ambient.png"),
                "menu": os.path.join(FIXED_DIR, "home_menu.png"),
                "recent": os.path.join(FIXED_DIR, "home_recent.png"),
                "focus_menu": os.path.join(FIXED_DIR, "home_menu_focus.png"),
                "focus_recent": os.path.join(FIXED_DIR, "home_recent_focus.png"),
                "source": os.path.join(FIXED_DIR, "adaptive_source" + source_ext),
            }
            copied = {
                "ambient": _copy_exact(mood.get("ambient"), paths["ambient"]),
                "menu": _copy_exact(mood.get("menu"), paths["menu"]),
                "recent": _copy_exact(mood.get("recent"), paths["recent"]),
                "focus_menu": _copy_exact(focus.get("menu"), paths["focus_menu"]),
                "focus_recent": _copy_exact(focus.get("recent"), paths["focus_recent"]),
                "source": _copy_exact(source, paths["source"]),
            }
            if not all(_valid(copied.get(k), 64) for k in copied):
                return {}
            data = {
                "schema": 1,
                "hero_id": str(hero_id or ""),
                "source": copied["source"],
                "mood": {"ambient": copied["ambient"], "menu": copied["menu"], "recent": copied["recent"]},
                "focus": {"menu": copied["focus_menu"], "recent": copied["focus_recent"]},
            }
            if not _atomic_manifest(data):
                return {}
            return data
        except Exception as exc:
            optional_failure("ui.fixed_adaptive_capture", exc)
            return {}


def capture_from_hero(hero):
    if not isinstance(hero, dict):
        return {}
    mood = hero.get("home_mood") if isinstance(hero.get("home_mood"), dict) else {}
    focus = hero.get("adaptive_focus") if isinstance(hero.get("adaptive_focus"), dict) else {}
    source = ""
    for key in ("source_backdrop_local", "display_backdrop_local", "backdrop_local", "prepared"):
        candidate = str(hero.get(key) or "")
        if _valid(candidate, 4096):
            source = candidate
            break
    return capture_from_parts(source, mood, focus, hero.get("id") or hero.get("tmdb_id") or "home")


def _clarify_packed_color(value):
    """Make the already-frozen accent easier to read without changing its hue source.

    R64 never samples artwork here.  It only raises the luminance of the exact
    packed colour already stored by R63 so secondary/value text is crisp on a TV.
    """
    try:
        value = int(value) & 0xFFFFFF
        r=(value>>16)&255; g=(value>>8)&255; b=value&255
        peak=max(r,g,b)
        if peak <= 0:
            return value
        target=max(235, min(255, int(round(peak*1.12))))
        gain=min(1.22, float(target)/float(peak))
        r=min(255,int(round(r*gain))); g=min(255,int(round(g*gain))); b=min(255,int(round(b*gain)))
        return (r<<16)|(g<<8)|b
    except Exception:
        return int(value or 0)


def fixed_value_color(default=0x74D8FF):
    """Return the persisted fixed accent only.  Never analyse Hero artwork in R64."""
    with _LOCK:
        data=load_fixed_bundle()
        if data:
            try:
                stored=data.get("value_color")
                if stored is not None:
                    return _clarify_packed_color(int(stored))
            except Exception:
                pass
        return _clarify_packed_color(int(default))


def fixed_settings_rows():
    """Return the exact R63 row pair bundled with the plugin from first paint."""
    with _LOCK:
        bundled_normal=os.path.join(BUNDLED_DIR,"utility_row.png")
        bundled_selected=os.path.join(BUNDLED_DIR,"utility_row_selected.png")
        if _valid(bundled_normal,64) and _valid(bundled_selected,64):
            return {"normal":bundled_normal,"selected":bundled_selected,"value_color":fixed_value_color()}
        data=load_fixed_bundle()
        if not data:
            return {}
        row_normal=os.path.join(FIXED_DIR,"utility_row.png")
        row_selected=os.path.join(FIXED_DIR,"utility_row_selected.png")
        if _valid(row_normal,64) and _valid(row_selected,64):
            return {"normal":row_normal,"selected":row_selected,"value_color":fixed_value_color()}
        return {}



def _fixed_fallback_row(selected=False):
    base = os.path.join(os.path.dirname(__file__), "assets_fhd", "fixed_master_r63")
    path = os.path.join(base, "utility_row_selected.png" if selected else "utility_row.png")
    return path if _valid(path, 64) else ""


def _fixed_row_sources():
    rows = fixed_settings_rows() or {}
    normal = str(rows.get("normal") or "")
    selected = str(rows.get("selected") or "")
    if not _valid(normal, 64):
        normal = _fixed_fallback_row(False)
    if not _valid(selected, 64):
        selected = _fixed_fallback_row(True) or normal
    return normal, selected


def _fixed_nine_slice(src, dst, width, height):
    """Resize already-frozen chrome only. No palette extraction or Hero analysis."""
    if _valid(dst, 64):
        return dst
    if not _valid(src, 64):
        return ""
    try:
        from PIL import Image
        ensure_persistent_dirs(FIXED_DIR)
        width=max(32,int(width)); height=max(24,int(height))
        im=Image.open(src).convert("RGBA")
        sw,sh=im.size
        if sw<8 or sh<8:
            return ""
        cx=max(12,min(sw//4,72,(width-8)//2))
        cy=max(8,min(sh//3,22,(height-8)//2))
        resample=getattr(getattr(Image,"Resampling",Image),"LANCZOS",getattr(Image,"LANCZOS",1))
        out=Image.new("RGBA",(width,height),(0,0,0,0))
        xs=(0,cx,sw-cx,sw); ys=(0,cy,sh-cy,sh)
        dx=(0,cx,width-cx,width); dy=(0,cy,height-cy,height)
        for yi in range(3):
            for xi in range(3):
                box=(xs[xi],ys[yi],xs[xi+1],ys[yi+1])
                tile=im.crop(box)
                tw=max(1,dx[xi+1]-dx[xi]); th=max(1,dy[yi+1]-dy[yi])
                if tile.size!=(tw,th):
                    tile=tile.resize((tw,th),resample)
                # Preserve the frozen row's original alpha. Using the RGBA tile
                # as its own paste mask squares alpha and makes large Cinematic/
                # Backdrop surfaces look almost invisible on receiver.
                out.alpha_composite(tile,(dx[xi],dy[yi]))
        tmp=dst+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
        out.save(tmp,"PNG")
        os.chmod(tmp,0o600); os.replace(tmp,dst)
        return dst if _valid(dst,64) else ""
    except Exception as exc:
        optional_failure("ui.fixed_settings_nineslice",exc)
        try:
            if os.path.exists(tmp):os.unlink(tmp)
        except Exception:
            pass
        return ""


def fixed_exact_surface(width, height, selected=False, tag="surface"):
    """Return the exact bundled R63 material at the requested geometry.

    Common Cinematic/Backdrop surfaces are shipped pre-sized inside the IPK, so
    first paint performs zero Pillow work. Unusual popup sizes are a synchronous
    nine-slice of the same fixed bundled row, cached once on HDD. No artwork
    sampling or palette generation happens here.
    """
    with _LOCK:
        rows = fixed_settings_rows() or {}
        source = str(rows.get("selected" if selected else "normal") or "")
        if not _valid(source, 64):
            return ""
        width=max(32,int(width)); height=max(24,int(height))
        safe="".join(ch if (ch.isalnum() or ch in ("-","_")) else "_" for ch in str(tag or "surface"))[:48]
        if not selected:
            packaged=os.path.join(BUNDLED_DIR,"surfaces","%s_%dx%d.png"%(safe,width,height))
            if _valid(packaged,64):
                return packaged
        ensure_persistent_dirs(FIXED_DIR)
        target=os.path.join(FIXED_DIR,"r125_green_%s_%dx%d%s.png"%(safe,width,height,"_selected" if selected else ""))
        return _fixed_nine_slice(source,target,width,height)


def fixed_settings_chrome(panel_w=900, panel_h=260, row_w=420, row_h=64):
    """Return deterministic Settings chrome derived only from the frozen master.

    R125 does not call any adaptive/palette builder here. The exact
    frozen utility rows are the material source; size-specific surfaces are made
    once in FIXED_DIR and reused forever across entries/restarts/Hero changes.
    """
    with _LOCK:
        normal,selected=_fixed_row_sources()
        if not normal:
            return {}
        ensure_persistent_dirs(FIXED_DIR)
        panel_w=max(320,int(panel_w)); panel_h=max(120,int(panel_h))
        row_w=max(160,int(row_w)); row_h=max(36,int(row_h))
        packaged_notice=os.path.join(BUNDLED_DIR,"settings_notice_560x260.png")
        if panel_w==560 and panel_h==260 and _valid(packaged_notice,64):
            panel=packaged_notice
        else:
            panel=os.path.join(FIXED_DIR,"r125_settings_panel_%dx%d.png"%(panel_w,panel_h))
            _fixed_nine_slice(normal,panel,panel_w,panel_h)
        row=os.path.join(FIXED_DIR,"r125_settings_row_%dx%d.png"%(row_w,row_h))
        sel=os.path.join(FIXED_DIR,"r125_settings_selected_%dx%d.png"%(row_w,row_h))
        _fixed_nine_slice(normal,row,row_w,row_h)
        _fixed_nine_slice(selected or normal,sel,row_w,row_h)
        return {
            "panel":panel if _valid(panel,64) else "",
            "row":row if _valid(row,64) else "",
            "selected":sel if _valid(sel,64) else "",
            "inner":panel if _valid(panel,64) else "",
            "button":sel if _valid(sel,64) else "",
            "input":row if _valid(row,64) else "",
            "value_color":fixed_value_color(),
        }

def cleanup_legacy_application_outputs(force=False):
    """Delete obsolete Hero-dependent application chrome and repoint hero.json.

    Only application/utility derivatives are removed.  Poster Grid/content
    adaptive files are intentionally outside these patterns and are untouched.
    The persistent fixed R63 bundle is never inside the session generated roots.
    """
    with _LOCK:
        marker=os.path.join(FIXED_DIR,'.r70_legacy_cleanup_done')
        if (not force) and os.path.isfile(marker):
            return 0
        removed=0
        patterns=(
            'dyn228home_*','dyn280home_*','dyn_settings_episode_*',
            'settingsglass_v5_*','settingsfit_v1_*','settings_popup_blur_*',
            'dyn_settings_context_*',
        )
        try:
            sessions=os.path.join(ROOT,'.sessions')
            for gen in glob.glob(os.path.join(sessions,'*','generated')):
                for pat in patterns:
                    for path in glob.glob(os.path.join(gen,pat)):
                        try:
                            if os.path.isfile(path) or os.path.islink(path):
                                os.unlink(path); removed+=1
                        except OSError:
                            pass
            # Rewrite the one Home state so no legacy adaptive path can be
            # rebound later after a navigation/restart.  Hero artwork itself is kept.
            data=load_fixed_bundle()
            hero_file=os.path.join(ROOT,'hero','hero.json')
            if data and os.path.isfile(hero_file):
                try:
                    with open(hero_file,'r',encoding='utf-8') as fh: state=json.load(fh)
                    if isinstance(state,dict) and isinstance(state.get('hero'),dict):
                        hero=dict(state['hero'])
                        hero['home_mood']=dict(data.get('mood') or {})
                        hero['adaptive_focus']=dict(data.get('focus') or {})
                        state['hero']=hero
                        fd,tmp=tempfile.mkstemp(prefix='.hero-r64-',suffix='.tmp',dir=os.path.dirname(hero_file))
                        try:
                            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                                json.dump(state,fh,ensure_ascii=False,separators=(',',':'));fh.flush();os.fsync(fh.fileno())
                            os.chmod(tmp,0o600);os.replace(tmp,hero_file)
                        finally:
                            if os.path.exists(tmp):
                                try:os.unlink(tmp)
                                except OSError:pass
                except Exception as exc:
                    optional_failure('ui.fixed_adaptive_rebind_hero',exc)
            ensure_persistent_dirs(FIXED_DIR)
            with open(marker,'w',encoding='ascii') as fh: fh.write('r70\n')
        except Exception as exc:
            optional_failure('ui.fixed_adaptive_cleanup',exc)
        return removed

