"""Dynamic chrome builders extracted from ui.py with dependency injection.

The public builder functions retain their original bodies; only the UI-owned
cache primitives are configured after ui.py defines them.
"""

import os
import hashlib
import threading

try:
    from PIL import Image as _PILImage, ImageFilter as _PILImageFilter, ImageDraw
except Exception:
    _PILImage = None
    _PILImageFilter = None
    ImageDraw = None

from .log import optional_failure
from .ui_dynamic_palette import _dynamic_palette
from .ui_helpers import _lift_dynamic_accent, _mix_rgb

THUMB_CACHE_DIR = ""
CATEGORY_ADAPTIVE_TMP_DIR = ""
_persistent_write_ok = None
_persistent_write_require = None
_valid_cache_file = None

def configure_dynamic_chrome(thumb_cache_dir, persistent_write_ok, persistent_write_require, valid_cache_file, category_adaptive_tmp_dir=""):
    global THUMB_CACHE_DIR, CATEGORY_ADAPTIVE_TMP_DIR, _persistent_write_ok, _persistent_write_require, _valid_cache_file
    THUMB_CACHE_DIR = thumb_cache_dir
    CATEGORY_ADAPTIVE_TMP_DIR = category_adaptive_tmp_dir
    _persistent_write_ok = persistent_write_ok
    _persistent_write_require = persistent_write_require
    _valid_cache_file = valid_cache_file

def _build_dynamic_details_gradient(source_path,target_path,size=(1920,1080)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    """Create a cached dark colour field matched to the current artwork.

    The gradient intentionally contains no recognisable image content. It is
    safe on Enigma2 renderers because it is a normal opaque JPEG, not a live
    alpha/blur effect. The builder works on 1-pixel strips then scales them, so
    palette generation is cheap even on slower receivers.
    """
    if _PILImage is None or not source_path or not os.path.isfile(source_path):return None
    if _valid_cache_file(target_path):return target_path
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        primary,secondary=_dynamic_palette(source_path);tw,th=int(size[0]),int(size[1]);base=(2,7,12)
        strip=[]
        for x in range(tw):
            nx=x/float(max(1,tw-1))
            # primary -> secondary -> near-black. Colour is strongest in the
            # poster/left area and is almost gone by the hero's far edge.
            if nx<=0.20:
                t=nx/0.20;left=primary;right=secondary
            elif nx<=0.48:
                t=(nx-0.20)/0.28;left=secondary;right=base
            else:
                t=min(1.0,(nx-0.48)/0.52);left=base;right=(1,4,8)
            ease=t*t*(3.0-2.0*t)
            strip.append(tuple(int(left[i]*(1.0-ease)+right[i]*ease) for i in range(3)))
        row=_PILImage.new("RGB",(tw,1));row.putdata(strip);img=row.resize((tw,th))
        # Darken progressively toward the bottom while retaining a subtle tint.
        vmask=_PILImage.new("L",(1,th));vmask.putdata([int(22+74*((y/float(max(1,th-1)))**1.35)) for y in range(th)])
        vmask=vmask.resize((tw,th));black=_PILImage.new("RGB",(tw,th),(1,4,8));img=_PILImage.composite(black,img,vmask)
        if _PILImageFilter is not None:img=img.filter(_PILImageFilter.GaussianBlur(radius=4))
        _persistent_write_require(temp);img.save(temp,"JPEG",quality=88,optimize=False,progressive=False)
        _persistent_write_require(target_path);os.replace(temp,target_path)
        return target_path if _valid_cache_file(target_path) else None
    except Exception as exc:
        optional_failure("ui.dynamic_gradient",exc)
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        return None


def _build_dynamic_poster_accent(source_path,target_path,size=(360,640)):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return None
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):return None
    if _valid_cache_file(target_path):return target_path
    temp=target_path+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
    try:
        primary,_secondary=_dynamic_palette(source_path);accent=_lift_dynamic_accent(primary,0.48,0.46);w,h=int(size[0]),int(size[1])
        out=_PILImage.new("RGBA",(w,h),(0,0,0,0));draw=ImageDraw.Draw(out)
        r,g,b=accent
        # Same lifted palette as the details chrome so poster and panel always match.
        draw.rounded_rectangle((1,1,w-2,h-2),radius=20,outline=(r,g,b,225),width=3)
        draw.rounded_rectangle((6,6,w-7,h-7),radius=17,outline=(min(255,r+30),min(255,g+30),min(255,b+30),92),width=1)
        _persistent_write_require(temp);out.save(temp,"PNG")
        _persistent_write_require(target_path);os.replace(temp,target_path)
        return target_path if _valid_cache_file(target_path) else None
    except Exception as exc:
        optional_failure("ui.dynamic_accent",exc)
        try:
            if os.path.exists(temp) and _persistent_write_ok(temp):os.unlink(temp)
        except Exception as exc:optional_failure("ui.silent_guard",exc)
        return None




def _build_dynamic_details_chrome(source_path, cache_key):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return {}
    """Generate subdued UI chrome whose edges follow the current artwork palette.

    Only borders, corner glints and very light glass tints follow the artwork.
    The body remains dark/neutral so text readability is stable across movies.
    """
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):
        return {}
    try:
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.48,0.46)
        accent2=_lift_dynamic_accent(secondary,0.42,0.38)
        base=(3,12,20)
        specs={
            "panel":((1388,410),30,True),
            "panel_detail":((1460,410),30,True),
            "overview":((1283,142),22,True),
            "overview_detail":((1368,142),22,True),
            "poster_card":((360,560),22,True),
            "poster_overlay":((360,560),22,True),
            "cast_card":((620,106),18,True),
            "director_card":((285,106),18,True),
            "writer_card":((430,106),18,True),
            "cast_card_ep":((555,106),18,True),
            "director_card_ep":((260,106),18,True),
            "writer_card_ep":((430,106),18,True),
            "poster_footer":((320,86),18,False),
            "poster_footer_detail":((360,86),18,False),
            "quality":((150,50),22,False),
            "year":((130,50),22,False),
            "runtime":((150,50),22,False),
            "country":((160,50),22,False),
            "genre":((815,40),19,False),
            "genre_detail":((850,40),19,False),
            "rating_pill":((130,42),18,False),
            "episodes_panel":((430,782),20,True),
            "series_row":((426,72),22,False),
            "series_row_selected":((426,72),22,False),
        }
        result={}
        for name,(size,radius,is_panel) in specs.items():
            target=os.path.join(THUMB_CACHE_DIR,"dyn220laser_%s_%s.png"%(str(cache_key),name))
            if _valid_cache_file(target):
                result[name]=target;continue
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            w,h=size
            out=_PILImage.new("RGBA",size,(0,0,0,0))
            glow=_PILImage.new("RGBA",size,(0,0,0,0));gd=ImageDraw.Draw(glow)
            gd.rounded_rectangle((6,6,w-7,h-7),radius=radius,outline=accent+(92 if is_panel else 78,),width=3)
            if _PILImageFilter is not None:glow=glow.filter(_PILImageFilter.GaussianBlur(radius=7 if is_panel else 5))
            out=_PILImage.alpha_composite(out,glow)
            draw=ImageDraw.Draw(out)
            # us217: adaptive glass. Keep the backdrop visible through the
            # information area while preserving enough darkness for readable text.
            fill=_mix_rgb(base,accent,0.10 if is_panel else 0.14)
            if name in ("panel", "panel_detail"):
                # us219: genuinely translucent adaptive glass.  The cinematic
                # backdrop must remain clearly visible through the information body.
                fill_alpha=62; border_alpha=174; border_width=1
            elif name in ("overview", "overview_detail"):
                fill_alpha=82; border_alpha=188; border_width=1
            elif name in ("cast_card", "director_card", "writer_card", "cast_card_ep", "director_card_ep", "writer_card_ep"):
                fill_alpha=88; border_alpha=184; border_width=1
            elif name in ("quality", "year", "runtime", "country", "genre", "genre_detail", "poster_footer", "poster_footer_detail"):
                fill_alpha=98; border_alpha=196; border_width=1
            else:
                fill_alpha=112; border_alpha=(190 if is_panel else 202); border_width=1
            if name == "series_row":
                # us218: one clean floating glass layer only. No doubled inner
                # picture/rim beneath the season/episode row.
                fill=_mix_rgb((4,12,18),accent,0.10)
                fill_alpha=108
                border_alpha=130
                border_width=1
            elif name == "series_row_selected":
                # Selection is a single adaptive glass card with one crisp edge.
                # Avoid the old blurred halo which looked like a second image/card.
                fill=_mix_rgb((4,13,18),accent,0.18)
                fill_alpha=126
                border_alpha=232
                border_width=2
            if name == "poster_overlay":
                draw.rounded_rectangle((2,2,w-3,h-3),radius=radius,outline=accent+(238,),width=3)
            else:
                draw.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=fill+(fill_alpha,),outline=accent+(border_alpha,),width=border_width)
            inner=_mix_rgb(accent,(255,255,255),0.40)
            # Detail glass gets one delicate inner rim and a visible polished
            # reflection. Series rows intentionally remain single-layer cards.
            if name not in ("poster_overlay","series_row","series_row_selected"):
                draw.rounded_rectangle((7,7,w-8,h-8),radius=max(5,radius-6),outline=inner+(22,),width=1)
            if name != "poster_overlay":
                sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=ImageDraw.Draw(sheen)
                sheen_alpha = 17 if name in ("panel","panel_detail") else (15 if is_panel else 13)
                sd.rounded_rectangle((12,7,w-13,max(14,h//2)),radius=max(5,radius-8),fill=inner+(sheen_alpha,))
                if _PILImageFilter is not None:sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=4 if is_panel else 3))
                out=_PILImage.alpha_composite(out,sheen)
            draw=ImageDraw.Draw(out)
            _persistent_write_require(temp);out.save(temp,"PNG")
            _persistent_write_require(target);os.replace(temp,target);result[name]=target
        ring_target=os.path.join(THUMB_CACHE_DIR,"dyn220laser_%s_rating_ring.png"%str(cache_key))
        if not _valid_cache_file(ring_target):
            temp=ring_target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            ring=_PILImage.new("RGBA",(108,108),(0,0,0,0));d=ImageDraw.Draw(ring)
            d.ellipse((5,5,102,102),fill=_mix_rgb(base,accent,0.07)+(238,),outline=accent+(205,),width=4)
            d.ellipse((12,12,95,95),outline=_mix_rgb(accent,(255,255,255),0.35)+(65,),width=1)
            _persistent_write_require(temp);ring.save(temp,"PNG");_persistent_write_require(ring_target);os.replace(temp,ring_target)
        result["rating_ring"]=ring_target
        return result
    except Exception as exc:
        optional_failure("ui.dynamic_details_chrome",exc)
        return {}


def _build_aux_adaptive_chrome(source_path, cache_key):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return {}
    """Build adaptive glass assets for Downloads and Information overlays.

    The material intentionally mirrors the approved floating episode rows: dark
    glass body, restrained artwork tint, polished top sheen and a contained
    adaptive edge. Geometry is fixed; only the palette changes.
    """
    if _PILImage is None or ImageDraw is None:
        return {}
    try:
        if source_path and os.path.isfile(str(source_path)):
            primary, secondary = _dynamic_palette(str(source_path))
        else:
            primary, secondary = (58,116,140), (30,68,92)
        accent = _lift_dynamic_accent(primary,0.50,0.46)
        accent2 = _lift_dynamic_accent(secondary,0.42,0.36)
        specs = {
            "download_panel": ((1280,800),30,"panel"),
            "download_inner": ((1180,590),26,"inner"),
            "download_row": ((1090,72),22,"row"),
            "download_row_selected": ((1090,72),22,"selected"),
            "info_panel": ((1320,820),30,"panel"),
            "info_inner": ((1220,520),24,"inner"),
            "info_overview": ((1220,300),24,"inner"),
            "info_cast": ((560,150),22,"inner"),
            "info_director": ((280,150),22,"inner"),
            "info_writer": ((340,150),22,"inner"),
        }
        result={}
        for name,(size,radius,kind) in specs.items():
            target=os.path.join(THUMB_CACHE_DIR,"dyn174aux_%s_%s.png"%(str(cache_key),name))
            if _valid_cache_file(target):
                result[name]=target; continue
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            w,h=size
            out=_PILImage.new("RGBA",size,(0,0,0,0))
            glow=_PILImage.new("RGBA",size,(0,0,0,0)); gd=ImageDraw.Draw(glow)
            ga=118 if kind=="selected" else (58 if kind in ("panel","inner") else 44)
            gw=4 if kind=="selected" else 2
            gd.rounded_rectangle((5,5,w-6,h-6),radius=radius,outline=accent+(ga,),width=gw)
            if _PILImageFilter is not None: glow=glow.filter(_PILImageFilter.GaussianBlur(radius=7 if kind in ("panel","selected") else 4))
            out=_PILImage.alpha_composite(out,glow)
            d=ImageDraw.Draw(out)
            base=(4,10,16)
            mix=0.085 if kind=="panel" else (0.105 if kind=="inner" else (0.12 if kind=="row" else 0.20))
            fill=_mix_rgb(base,accent,mix)
            alpha=238 if kind in ("panel","inner") else 244
            border=236 if kind=="selected" else (150 if kind=="row" else 190)
            bw=2 if kind=="selected" else 1
            d.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=fill+(alpha,),outline=accent+(border,),width=bw)
            inner=_mix_rgb(accent2,(255,255,255),0.34)
            d.rounded_rectangle((7,7,w-8,h-8),radius=max(8,radius-6),outline=inner+(50 if kind!="selected" else 86,),width=1)
            sheen=_PILImage.new("RGBA",size,(0,0,0,0)); sd=ImageDraw.Draw(sheen)
            hi=_mix_rgb(accent,(255,255,255),0.46)
            top=max(24,int(h*0.45))
            for yy in range(8,top,6):
                t=(yy-8.0)/max(1.0,top-8.0); a=max(0,int((24 if kind in ("row","selected") else 14)*(1.0-t)**1.5))
                sd.rounded_rectangle((12,yy,w-13,min(h-12,yy+8)),radius=max(6,radius-8),fill=hi+(a,))
            if _PILImageFilter is not None: sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=4))
            out=_PILImage.alpha_composite(out,sheen)
            _persistent_write_require(temp);out.save(temp,"PNG",optimize=False); _persistent_write_require(target);os.replace(temp,target)
            result[name]=target
        return result
    except Exception as exc:
        optional_failure("ui.aux_adaptive_chrome",exc); return {}


def _build_home_adaptive_focus(source_path, cache_key):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return {}
    """Premium glass focus for Home cards, derived from the current hero palette.

    The focus never paints an Enigma2-style slab or an external rectangle.  It
    reinforces the same layered glass material used by the floating episode rows:
    a quiet adaptive inner wash, a polished inner highlight and a controlled edge.
    """
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path): return {}
    try:
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.72,0.82)
        accent2=_lift_dynamic_accent(secondary,0.62,0.72)
        specs={"menu":((238,190),20),"recent":((565,330),24)};result={}
        for name,(size,radius) in specs.items():
            target=os.path.join(THUMB_CACHE_DIR,"dyn228home_%s_%sfocus.png"%(str(cache_key),name))
            if _valid_cache_file(target):result[name]=target;continue
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident());w,h=size
            out=_PILImage.new("RGBA",size,(0,0,0,0))
            # Focused card gets a restrained adaptive glass wash, not a solid colour.
            glass=_PILImage.new("RGBA",size,(0,0,0,0));gd=ImageDraw.Draw(glass)
            focus_fill=_mix_rgb((6,13,18),accent,0.19 if name=="menu" else 0.145)
            gd.rounded_rectangle((3,3,w-4,h-4),radius=radius,fill=focus_fill+(78,))
            out=_PILImage.alpha_composite(out,glass)
            # Edge energy is kept inside the card bounds.
            edge=_PILImage.new("RGBA",size,(0,0,0,0));ed=ImageDraw.Draw(edge)
            ed.rounded_rectangle((4,4,w-5,h-5),radius=radius-1,outline=accent+(190,),width=4)
            if _PILImageFilter is not None:edge=edge.filter(_PILImageFilter.GaussianBlur(radius=4))
            out=_PILImage.alpha_composite(out,edge)
            d=ImageDraw.Draw(out)
            d.rounded_rectangle((2,2,w-3,h-3),radius=radius,outline=accent+(245,),width=3)
            inner=_mix_rgb(accent2,(255,255,255),0.36)
            d.rounded_rectangle((7,7,w-8,h-8),radius=max(8,radius-6),outline=inner+(92,),width=1)
            # A glassy top glint gives the same raised-layer feeling as episode rows.
            sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=ImageDraw.Draw(sheen)
            for yy in range(8,max(10,h//2),4):
                t=(yy-8.0)/max(1.0,(h//2)-8.0);a=max(0,int(24*(1.0-t)**1.8))
                sd.rounded_rectangle((12,yy,w-13,min(h-10,yy+5)),radius=max(5,radius-8),fill=inner+(a,))
            if _PILImageFilter is not None:sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=3))
            out=_PILImage.alpha_composite(out,sheen)
            _persistent_write_require(temp);out.save(temp,"PNG",optimize=False);_persistent_write_require(target);os.replace(temp,target);result[name]=target
        return result
    except Exception as exc:optional_failure("ui.home_adaptive_focus",exc);return {}


def _build_category_adaptive_chrome(source_path, cache_key, media_type=""):
    # Category chrome is session UI, not durable media. Keep it in /tmp so the
    # adaptive background never depends on HDD mount/spin-up state. This also
    # prevents the fast RAM-prefetched category screen from outrunning the
    # persistent-cache initializer and silently losing its adaptive layer.
    try:
        os.makedirs(CATEGORY_ADAPTIVE_TMP_DIR, mode=0o700, exist_ok=True)
    except Exception as exc:
        optional_failure("ui.category_adaptive_tmp", exc)
        return {}
    """Build Select-Portal-grade floating glass rows for Categories.

    The Home hero only supplies the adaptive palette.  The rows themselves stay
    dark/glassy so they remain calm and readable, with the same floating visual
    language as Select Portal rather than an opaque black list slab.
    """
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):
        return {}
    try:
        primary, secondary = _dynamic_palette(source_path)
        accent = _lift_dynamic_accent(primary, 0.70, 0.78)
        accent2 = _lift_dynamic_accent(secondary, 0.58, 0.68)
        base = (4, 12, 18)
        specs = {
            "row": (674, 62, 18, False),
            "row_selected": (674, 62, 18, True),
            "info": (508, 74, 20, False),
        }
        out_paths = {}
        for name, (w, h, r, selected) in specs.items():
            target = os.path.join(CATEGORY_ADAPTIVE_TMP_DIR, "cat224_%s_%s.png" % (str(cache_key), name))
            if _valid_cache_file(target):
                out_paths[name] = target
                continue
            tmp = target + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
            img = _PILImage.new("RGBA", (w, h), (0, 0, 0, 0))

            # Soft energy stays inside the rounded silhouette, like Select Portal.
            glow = _PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            glow_alpha = 148 if selected else 46
            gd.rounded_rectangle((5, 5, w-6, h-6), radius=r,
                                 outline=accent + (glow_alpha,), width=(3 if selected else 2))
            if _PILImageFilter is not None:
                glow = glow.filter(_PILImageFilter.GaussianBlur(radius=7 if selected else 4))
            img = _PILImage.alpha_composite(img, glow)

            d = ImageDraw.Draw(img)
            # Deliberately translucent: this is glass, not a black rectangle.
            fill_mix = 0.115 if selected else 0.064
            fill = _mix_rgb(base, accent, fill_mix)
            fill_alpha = 162 if selected else 142
            edge_alpha = 235 if selected else 108
            d.rounded_rectangle((2, 2, w-3, h-3), radius=r,
                                fill=fill + (fill_alpha,),
                                outline=accent + (edge_alpha,),
                                width=(2 if selected else 1))

            # Polished inner rim and a faint upper glass reflection.
            inner = _mix_rgb(accent2, (255,255,255), 0.34)
            d.rounded_rectangle((7, 7, w-8, h-8), radius=max(9, r-6),
                                outline=inner + ((82 if selected else 38),), width=1)
            sheen = _PILImage.new("RGBA", (w, h), (0,0,0,0))
            sd = ImageDraw.Draw(sheen)
            hi = _mix_rgb(accent, (255,255,255), 0.54)
            top = max(24, int(h*0.46))
            for yy in range(9, top, 5):
                t = (yy-9.0)/max(1.0, top-9.0)
                a = max(0, int((34 if selected else 20) * (1.0-t)**1.65))
                sd.rounded_rectangle((13, yy, w-14, min(h-12, yy+7)),
                                     radius=max(7, r-9), fill=hi + (a,))
            if _PILImageFilter is not None:
                sheen = sheen.filter(_PILImageFilter.GaussianBlur(radius=4))
            img = _PILImage.alpha_composite(img, sheen)

            img.save(tmp, "PNG", optimize=False)
            os.replace(tmp, target)
            out_paths[name] = target
        # Use the exact adaptive atmosphere language that originally worked on
        # Series.  Movies and Live now run this identical code path with the
        # exact Home hero source passed in-memory by PortalHomeScreen.
        page_key = hashlib.sha1((str(source_path)+"|"+str(cache_key)+"|series-proven-v2-lowmem").encode("utf-8","ignore")).hexdigest()[:20]
        page_target = os.path.join(CATEGORY_ADAPTIVE_TMP_DIR,"catpage_%s_960x540.png"%page_key)
        if _valid_cache_file(page_target,ttl=0):
            out_paths["page"] = page_target
        else:
            page_tmp = page_target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            try:
                primary,secondary=_dynamic_palette(source_path)
                pw,ph=120,68; base=(2,7,12)
                page_small=_PILImage.new("RGB",(pw,ph),base); pp=page_small.load()
                pr,pg,pb=primary; sr,sg,sb=secondary
                # Same radial field as the working poster-grid/Series mood, but
                # moderated so it remains cinematic rather than eye-searing.
                for yy in range(ph):
                    ny=yy/float(max(1,ph-1))
                    for xx in range(pw):
                        nx=xx/float(max(1,pw-1)); dx=(nx-0.50)/0.62; dy=(ny-0.53)/0.58
                        radial=max(0.0,1.0-(dx*dx+dy*dy)); edge=max(0.0,1.0-abs(nx-0.5)*2.0)
                        a=0.43*radial*(0.98-0.24*ny); bmix=0.14*edge*(1.0-0.30*ny)
                        rr=int(base[0]*(1-a-bmix)+pr*a+sr*bmix); gg=int(base[1]*(1-a-bmix)+pg*a+sg*bmix); bb=int(base[2]*(1-a-bmix)+pb*a+sb*bmix)
                        pp[xx,yy]=(max(0,min(255,rr)),max(0,min(255,gg)),max(0,min(255,bb)))
                resampling=getattr(getattr(_PILImage,"Resampling",_PILImage),"BICUBIC",3)
                page_img=page_small.resize((960,540),resampling)
                if _PILImageFilter is not None: page_img=page_img.filter(_PILImageFilter.GaussianBlur(12))
                page_img.save(page_tmp,"PNG",optimize=False); os.replace(page_tmp,page_target)
                out_paths["page"] = page_target
            except Exception as exc:
                optional_failure("ui.category_page_adaptive",exc)
                try:
                    if os.path.exists(page_tmp): os.unlink(page_tmp)
                except Exception as exc: optional_failure("ui.silent_guard",exc)
        return out_paths
    except Exception as exc:
        optional_failure("ui.category_adaptive_chrome", exc)
        return {}


def _build_poster_adaptive_chrome_clean(source_path, cache_key=None):
    """Movies/Series HUD: Live-style adaptive glass without any inner lines."""
    if not _persistent_write_ok(THUMB_CACHE_DIR): return {}
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):
        return {}
    try:
        try:stamp=str(int(os.path.getmtime(source_path)))
        except Exception:stamp="0"
        key=cache_key or hashlib.sha1((source_path+"|"+stamp+"|posterhud_clean_v1").encode("utf-8","ignore")).hexdigest()[:18]
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.62,0.76)
        accent2=_lift_dynamic_accent(secondary,0.44,0.58)
        specs={
            "counter":(575,54,18,152),
            "channel_top":(500,52,17,164),
            "clock":(260,82,20,156),
        }
        result={}
        for name,(w,h,r,alpha) in specs.items():
            target=os.path.join(THUMB_CACHE_DIR,"posterhud_clean_%s_%s.png"%(key,name))
            if _valid_cache_file(target,ttl=0):
                result[name]=target;continue
            tmp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            fill=_mix_rgb((4,12,18),accent,0.14 if name!="channel_top" else 0.18)
            img=_PILImage.new("RGBA",(w,h),(0,0,0,0))

            # Floating depth stays outside/under the card only.
            depth=_PILImage.new("RGBA",(w,h),(0,0,0,0));dd=ImageDraw.Draw(depth)
            dd.rounded_rectangle((5,7,w-6,h-3),radius=r,fill=(0,0,0,96))
            if _PILImageFilter is not None:
                depth=depth.filter(_PILImageFilter.GaussianBlur(radius=5))
            img=_PILImage.alpha_composite(img,depth)

            # One clean adaptive body + one external neon rim. No inner rectangle,
            # no horizontal sheen and no decorative arc inside the glass.
            d=ImageDraw.Draw(img)
            outer=_mix_rgb(accent,(255,255,255),0.18)
            d.rounded_rectangle((2,2,w-3,h-3),radius=r,fill=fill+(alpha,),outline=outer+(228,),width=2)

            _persistent_write_require(tmp);img.save(tmp,"PNG",optimize=False)
            _persistent_write_require(target);os.replace(tmp,target)
            result[name]=target
        return result
    except Exception as exc:
        optional_failure("ui.poster_clean_adaptive_chrome",exc)
        return {}


def _build_live_adaptive_chrome_211(source_path, cache_key=None):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return {}
    """Live-only adaptive glass. Fast and cached; never touches Home/TMDB artwork."""
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):
        return {}
    try:
        try: stamp=str(int(os.path.getmtime(source_path)))
        except Exception: stamp="0"
        key=cache_key or hashlib.sha1((source_path+"|"+stamp+"|livehud3d_v3").encode("utf-8","ignore")).hexdigest()[:18]
        primary,secondary=_dynamic_palette(source_path)
        accent=_lift_dynamic_accent(primary,0.62,0.76)
        accent2=_lift_dynamic_accent(secondary,0.44,0.58)
        base=(3,10,17); result={}

        # Cheap page mood: draw tiny, upscale. No 2M-pixel Python loop on every channel.
        target=os.path.join(THUMB_CACHE_DIR,"live211_%s_page.png"%key)
        if not _valid_cache_file(target):
            tmp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            tiny=_PILImage.new("RGB",(96,54),base); d=ImageDraw.Draw(tiny)
            wash=_mix_rgb(base,accent,0.24)
            d.ellipse((38,-8,112,66),fill=wash)
            d.rectangle((0,0,96,54),fill=base+(0,) if False else base)
            # overlay a subtle right-side accent gradient with compositing
            ov=_PILImage.new("RGBA",(96,54),(0,0,0,0)); od=ImageDraw.Draw(ov)
            for x in range(34,96):
                t=(x-34)/62.0; a=int(46*t)
                od.rectangle((x,0,x,54),fill=accent+(a,))
            tiny=_PILImage.alpha_composite(tiny.convert("RGBA"),ov).convert("RGB")
            img=tiny.resize((1920,1080),_PILImage.Resampling.BILINEAR if hasattr(_PILImage,'Resampling') else _PILImage.BILINEAR)
            if _PILImageFilter is not None: img=img.filter(_PILImageFilter.GaussianBlur(radius=18))
            _persistent_write_require(tmp);img.save(tmp,"PNG",optimize=False); _persistent_write_require(target);os.replace(tmp,target)
        result["page"]=target

        specs={
            "selected":(748,58,20,True,112),
            "preview":(940,548,28,False,0),
            "info":(940,244,26,False,112),
            "tech":(205,46,18,False,96),
            "row":(748,58,18,False,54),
            # HUD cards intentionally match the Mini List glass vocabulary.
            "counter":(575,54,18,False,152),
            "channel_top":(500,52,17,False,164),
            "clock":(260,82,20,False,156),
        }
        for name,(w,h,r,selected,alpha) in specs.items():
            target=os.path.join(THUMB_CACHE_DIR,"live211_%s_%s.png"%(key,name))
            if _valid_cache_file(target): result[name]=target; continue
            tmp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            img=_PILImage.new("RGBA",(w,h),(0,0,0,0))
            d=ImageDraw.Draw(img)
            if name=="preview":
                # Rim only. Never create a black plate behind the live video.
                outer=_mix_rgb(accent,(255,255,255),0.18)
                d.rounded_rectangle((2,2,w-3,h-3),radius=r,outline=outer+(105,),width=2)
                d.rounded_rectangle((8,8,w-9,h-9),radius=max(12,r-7),outline=accent2+(38,),width=1)
            elif name in ("counter","channel_top","clock"):
                # Same 3D floating glass recipe as the Mini List header/footer:
                # contained depth, adaptive body, bright rim, inner rim, top sheen.
                fill=_mix_rgb((4,12,18),accent,0.14 if name!="channel_top" else 0.18)
                depth=_PILImage.new("RGBA",(w,h),(0,0,0,0));dd=ImageDraw.Draw(depth)
                dd.rounded_rectangle((5,7,w-6,h-3),radius=r,fill=(0,0,0,96))
                if _PILImageFilter is not None:depth=depth.filter(_PILImageFilter.GaussianBlur(radius=5))
                img=_PILImage.alpha_composite(img,depth);d=ImageDraw.Draw(img)
                d.rounded_rectangle((2,2,w-3,h-3),radius=r,fill=fill+(alpha,),outline=accent+(228,),width=2)
                inner=_mix_rgb(accent2,(255,255,255),0.34)
                d.rounded_rectangle((7,7,w-8,h-8),radius=max(8,r-6),outline=inner+(82,),width=1)
                sheen=_PILImage.new("RGBA",(w,h),(0,0,0,0));sd=ImageDraw.Draw(sheen)
                sd.rounded_rectangle((12,6,w-13,max(12,int(h*.48))),radius=max(6,r-8),fill=inner+(22,))
                if _PILImageFilter is not None:sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=3))
                img=_PILImage.alpha_composite(img,sheen);d=ImageDraw.Draw(img)
                d.arc((9,7,w-10,max(24,int(h*.44))),180,360,fill=_mix_rgb(accent,(255,255,255),0.52)+(74,),width=1)
            else:
                fill=_mix_rgb(base,accent,0.13 if selected else 0.055)
                d.rounded_rectangle((2,2,w-3,h-3),radius=r,fill=fill+(alpha,),outline=accent+((220 if selected else 70),),width=(2 if selected else 1))
                inner=_mix_rgb(accent2,(255,255,255),0.30)
                d.rounded_rectangle((7,7,w-8,h-8),radius=max(9,r-6),outline=inner+((76 if selected else 28),),width=1)
                if selected:
                    hi=_mix_rgb(accent,(255,255,255),0.50)
                    d.rounded_rectangle((12,8,w-13,min(h-10,22)),radius=max(6,r-9),fill=hi+(18,))
            _persistent_write_require(tmp);img.save(tmp,"PNG",optimize=False); _persistent_write_require(target);os.replace(tmp,target); result[name]=target
        result["accent"]=accent
        return result
    except Exception as exc:
        optional_failure("ui.live211_adaptive",exc); return {}


def _build_home_mood_assets(source_path, cache_key):
    if not _persistent_write_ok(THUMB_CACHE_DIR): return {}
    """Build episode-card quality glass for the ten Home cards.

    The hero artwork stays dominant.  Menu and Continue/Recent cards remain nearly
    black, but use layered translucent glass, an extremely restrained adaptive tint,
    a soft internal sheen and polished edges.  The palette is felt rather than seen.
    """
    if _PILImage is None or ImageDraw is None or not source_path or not os.path.isfile(source_path):return {}
    try:
        primary,secondary=_dynamic_palette(source_path)
        p=_lift_dynamic_accent(primary,0.54,0.64); q=_lift_dynamic_accent(secondary,0.48,0.56)
        out={}; tw,th=1920,1080
        # Keep a dark transparent-ready ambient base for receiver compositing.  The
        # backdrop remains the visual hero; this layer only prevents harsh black gaps.
        target=os.path.join(THUMB_CACHE_DIR,"dyn228home_%s_ambient.png"%cache_key)
        if not _valid_cache_file(target):
            temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident())
            base=_PILImage.new("RGBA",(tw,th),(3,8,13,255))
            wash=_PILImage.new("RGBA",(tw,th),(0,0,0,0));d=ImageDraw.Draw(wash)
            d.ellipse((-520,-340,1300,900),fill=p+(13,));d.ellipse((980,-240,2350,780),fill=q+(9,));d.ellipse((260,650,1720,1420),fill=p+(5,))
            if _PILImageFilter is not None:wash=wash.filter(_PILImageFilter.GaussianBlur(radius=180))
            im=_PILImage.alpha_composite(base,wash);_persistent_write_require(temp);im.save(temp,"PNG",optimize=False);_persistent_write_require(target);os.replace(temp,target)
        out["ambient"]=target

        for name,size,tint in (("menu",(238,190),0.070),("recent",(565,330),0.050)):
            target=os.path.join(THUMB_CACHE_DIR,"dyn280home_%s_%sglass.png"%(cache_key,name))
            if not _valid_cache_file(target):
                temp=target+".tmp.%d.%d"%(os.getpid(),threading.get_ident());w,h=size
                radius=20 if name=="menu" else 24
                basec=_mix_rgb((3,8,12),p,tint)
                outim=_PILImage.new("RGBA",size,(0,0,0,0))
                # Soft contained depth shadow gives the glass layer a lifted body.
                depth=_PILImage.new("RGBA",size,(0,0,0,0));dd=ImageDraw.Draw(depth)
                dd.rounded_rectangle((5,7,w-6,h-4),radius=radius,fill=(0,0,0,110))
                if _PILImageFilter is not None:depth=depth.filter(_PILImageFilter.GaussianBlur(radius=6))
                outim=_PILImage.alpha_composite(outim,depth)
                d=ImageDraw.Draw(outim)
                d.rounded_rectangle((2,2,w-3,h-3),radius=radius,fill=basec+(242,),outline=p+(142,),width=2)
                # A second inner skin creates the "glass inside the frame" depth.
                innerc=_mix_rgb((8,15,20),q,0.060 if name=="menu" else 0.042)
                d.rounded_rectangle((7,7,w-8,h-8),radius=max(9,radius-6),fill=innerc+(82,),outline=_mix_rgb(p,(255,255,255),0.30)+(68,),width=1)
                # Top glass reflection: broad, extremely subtle and feathered.
                sheen=_PILImage.new("RGBA",size,(0,0,0,0));sd=ImageDraw.Draw(sheen)
                hi=_mix_rgb(p,(255,255,255),0.40)
                top_end=max(24,int(h*0.48))
                for yy in range(9,top_end,5):
                    t=(yy-9.0)/max(1.0,top_end-9.0);a=max(0,int((30 if name=="menu" else 22)*(1.0-t)**1.55))
                    sd.rounded_rectangle((12,yy,w-13,min(h-12,yy+7)),radius=max(6,radius-8),fill=hi+(a,))
                if _PILImageFilter is not None:sheen=sheen.filter(_PILImageFilter.GaussianBlur(radius=4))
                outim=_PILImage.alpha_composite(outim,sheen)
                # Lower vignette keeps posters/text legible and makes the upper glass catch light.
                shade=_PILImage.new("RGBA",size,(0,0,0,0));sh=ImageDraw.Draw(shade)
                for yy in range(int(h*0.58),h-8,5):
                    t=(yy-h*0.58)/max(1.0,h*0.42);a=int(8+24*t)
                    sh.rectangle((8,yy,w-9,min(h-8,yy+6)),fill=(0,0,0,a))
                outim=_PILImage.alpha_composite(outim,shade)
                # 10.0.80: no top arc. Keep the glass body/inner sheen only so
                # Home menu and Continue/Recent cards read cleanly with no curved
                # rail across their upper edge.
                _persistent_write_require(temp);outim.save(temp,"PNG",optimize=False);_persistent_write_require(target);os.replace(temp,target)
            out[name]=target
        return out
    except Exception as exc:optional_failure("ui.home_mood_assets",exc);return {}
