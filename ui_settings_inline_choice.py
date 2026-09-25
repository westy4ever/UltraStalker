# -*- coding: utf-8 -*-
"""Exact reuse of the Settings floating-choice row grammar inside existing screens.

This is intentionally not a redesign.  Geometry, row assets, selected glow,
5-visible-row scrolling and horizontal 9-slice resizing are copied from the
proven NovaSettingsScreen inline context picker.
"""
from __future__ import absolute_import
import hashlib
import os

from Components.ActionMap import ActionMap
from .ui_icon_menu import IconMenuList
from .ui_dynamic_chrome import _build_category_adaptive_chrome
from .ui_fixed_adaptive import fixed_settings_rows

try:
    from enigma import getDesktop, ePoint, eSize, eLabel, gFont
except Exception:
    getDesktop=ePoint=eSize=eLabel=gFont=None

try:
    from PIL import Image as _SettingsImage
except Exception:
    _SettingsImage=None


class SettingsInlineChoiceOverlay(object):
    ROW_H = 80
    MAX_VISIBLE = 5

    def __init__(self, screen, widget_name, actions_name, asset_fn, translate_fn=None, failure_fn=None, action_priority=-2):
        self.screen=screen
        self.widget_name=str(widget_name)
        self.actions_name=str(actions_name)
        self.asset=asset_fn
        self.tr=translate_fn or (lambda x:x)
        self.failure=failure_fn or (lambda *args,**kwargs:None)
        self.active=False
        self.choices=[]
        self.labels=[]
        self.last_idx=0
        self.row_asset=None
        self.selected_asset=None
        self.accept_callback=None
        self.close_callback=None
        self._region=(1850,590,400)

        # Exact Settings list component / renderer.
        try:
            screen[self.widget_name]
        except Exception:
            screen[self.widget_name]=IconMenuList([],width=650,item_height=58,icon_size=0,primary_font=22,secondary_font=16,row_style="settings_dialog")
        try:screen[self.widget_name].hide()
        except Exception:pass
        try:
            if self._selection_changed not in screen[self.widget_name].onSelectionChanged:
                screen[self.widget_name].onSelectionChanged.append(self._selection_changed)
        except Exception as exc:self._fail("ui.settings_inline_choice_hook",exc)

        screen[self.actions_name]=ActionMap(
            ["OkCancelActions","DirectionActions","MenuActions","UltraStalkerMenuActions","ColorActions","InfoActions"],
            {
                "cancel":self.close,
                "ok":self.accept,
                "up":lambda:self.move("up"),
                "down":lambda:self.move("down"),
                "left":lambda:self.move("left"),
                "right":lambda:self.move("right"),
                "menu":self.close,
                # While the overlay is visible it behaves modally inside the same
                # Screen, so colour/info keys must not leak to the page below it.
                "red":self._noop,"green":self._noop,"yellow":self._noop,"blue":self._noop,"info":self._noop,
            },
            int(action_priority),
        )
        try:screen[self.actions_name].setEnabled(False)
        except Exception:pass

    def _fail(self,name,exc):
        try:self.failure(name,exc)
        except Exception:pass

    def _noop(self):
        return None

    def _measure(self,text,font_size=22):
        value=str(text or "")
        try:
            if eLabel is not None and gFont is not None:
                probe=eLabel();probe.setFont(gFont("Regular",int(font_size)));probe.setText(value)
                size=probe.calculateSize();return max(0,int(size.width()))
        except Exception as exc:self._fail("ui.settings_inline_choice_measure",exc)
        return max(0,int(len(value)*font_size*0.56))

    def _geom(self,x,y,w,h):
        try:
            inst=self.screen[self.widget_name].instance
            if inst is None:return
            if getDesktop is None or ePoint is None or eSize is None:return
            desktop=getDesktop(0).size();sx=float(desktop.width())/1920.0;sy=float(desktop.height())/1080.0
            inst.move(ePoint(int(round(x*sx)),int(round(y*sy))))
            inst.resize(eSize(max(1,int(round(w*sx))),max(1,int(round(h*sy)))))
        except Exception as exc:self._fail("ui.settings_inline_choice_geom",exc)

    def _compact_episode_assets(self,card_w):
        """Byte-for-byte Settings row source + the same horizontal 9-slice resize."""
        card_w=max(220,min(560,int(card_w or 426)))
        # R64: utility menus are sourced only from the frozen application pair.
        # No Hero/palette-based Settings row writer is allowed to run here.
        normal=str(self.asset("series_floating_row.png") or "")
        selected=str(self.asset("series_floating_row_selected.png") or "")
        try:
            chrome=fixed_settings_rows() or {}
            candidate=str(chrome.get("normal") or "")
            candidate_selected=str(chrome.get("selected") or "")
            if candidate and os.path.isfile(candidate):normal=candidate
            if candidate_selected and os.path.isfile(candidate_selected):selected=candidate_selected
        except Exception as exc:
            self._fail("ui.settings_inline_choice_fixed_rows",exc)
        if card_w==426:
            return normal,selected
        out=[]
        for src,kind in ((normal,"normal"),(selected,"selected")):
            if not (src and os.path.isfile(src)):
                out.append(src);continue
            try:
                if _SettingsImage is None:
                    out.append(src);continue
                stamp=int(os.path.getmtime(src))
                sig=hashlib.sha1((src+"|"+str(stamp)+"|"+str(card_w)+"|floating-choice-v1").encode("utf-8","ignore")).hexdigest()[:16]
                dst=os.path.join(os.path.dirname(src),"dyn_settings_context_%s_%s_%d.png"%(sig,kind,card_w))
                if not (os.path.isfile(dst) and os.path.getsize(dst)>64):
                    im=_SettingsImage.open(src).convert("RGBA")
                    sw,sh=im.size;target_h=72
                    if sh!=target_h:
                        resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                        im=im.resize((sw,target_h),resample);sw,sh=im.size
                    cap=max(36,min(72,sw//4,(card_w-16)//2))
                    resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                    if card_w>cap*2+8 and sw>cap*2+8:
                        canvas=_SettingsImage.new("RGBA",(card_w,target_h),(0,0,0,0))
                        left=im.crop((0,0,cap,target_h));right=im.crop((sw-cap,0,sw,target_h))
                        center=im.crop((cap,0,sw-cap,target_h)).resize((card_w-cap*2,target_h),resample)
                        canvas.paste(left,(0,0));canvas.paste(center,(cap,0));canvas.paste(right,(card_w-cap,0))
                    else:
                        canvas=im.resize((card_w,target_h),resample)
                    tmp=dst+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG");os.replace(tmp,dst)
                out.append(dst)
            except Exception as exc:
                self._fail("ui.settings_inline_choice_resize",exc);out.append(src)
        return (out+[None,None])[:2]


    def _compact_category_assets(self,card_w,adaptive_source=None,adaptive_accent=None,adaptive_accent_soft=None):
        """Reuse the exact Categories adaptive row material, then resize only horizontally.

        This is not a look-alike skin.  The source row is produced by the same
        ``_build_category_adaptive_chrome`` function used by the Categories rail.
        Small subtitle menus merely nine-slice that proven 62px row to the text
        width requested by the caller.
        """
        card_w=max(180,min(560,int(card_w or 320)))
        normal=selected=""
        try:
            # Explicit adaptive_source means the caller owns colour authority.
            # This is used by the Player so subtitle rows inherit the exact
            # Player palette and never borrow Home/Settings hero colours.
            explicit_source=str(adaptive_source or "")
            if explicit_source and os.path.isfile(explicit_source):
                source=explicit_source
                accent_key=str(adaptive_accent or "")+"|"+str(adaptive_accent_soft or "")
                key=hashlib.sha1((accent_key+"|player-category-inline-v1").encode("utf-8","ignore")).hexdigest()[:18]
                chrome=_build_category_adaptive_chrome(
                    source,key,"",426,
                    accent_override=adaptive_accent,accent2_override=adaptive_accent_soft,
                ) or {}
            else:
                from .ui_screens_settings import _settings_category_backdrop
                source=str(_settings_category_backdrop() or "")
                chrome={}
                if source and os.path.isfile(source):
                    stamp=int(os.path.getmtime(source))
                    key=hashlib.sha1((source+"|"+str(stamp)+"|category-inline-source-v1").encode("utf-8","ignore")).hexdigest()[:18]
                    chrome=_build_category_adaptive_chrome(source,key,"",426) or {}
            normal=str(chrome.get("row") or "")
            selected=str(chrome.get("row_selected") or "")
        except Exception as exc:
            self._fail("ui.settings_inline_choice_category_rows",exc)
        if not normal:
            normal=str(self.asset("series_floating_row.png") or "")
        if not selected:
            selected=str(self.asset("series_floating_row_selected.png") or normal)
        if card_w==426:
            return normal,selected
        out=[]
        for src,kind in ((normal,"normal"),(selected,"selected")):
            if not (src and os.path.isfile(src)):
                out.append(src);continue
            try:
                if _SettingsImage is None:
                    out.append(src);continue
                stamp=int(os.path.getmtime(src))
                sig=hashlib.sha1((src+"|"+str(stamp)+"|"+str(card_w)+"|category-inline-v1").encode("utf-8","ignore")).hexdigest()[:16]
                dst=os.path.join(os.path.dirname(src),"dyn_category_inline_%s_%s_%d.png"%(sig,kind,card_w))
                if not (os.path.isfile(dst) and os.path.getsize(dst)>64):
                    im=_SettingsImage.open(src).convert("RGBA")
                    sw,sh=im.size;target_h=62
                    if sh!=target_h:
                        resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                        im=im.resize((sw,target_h),resample);sw,sh=im.size
                    cap=max(30,min(58,sw//4,max(30,(card_w-12)//2)))
                    resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                    if card_w>cap*2+8 and sw>cap*2+8:
                        canvas=_SettingsImage.new("RGBA",(card_w,target_h),(0,0,0,0))
                        left=im.crop((0,0,cap,target_h));right=im.crop((sw-cap,0,sw,target_h))
                        center=im.crop((cap,0,sw-cap,target_h)).resize((card_w-cap*2,target_h),resample)
                        canvas.paste(left,(0,0));canvas.paste(center,(cap,0));canvas.paste(right,(card_w-cap,0))
                    else:
                        canvas=im.resize((card_w,target_h),resample)
                    tmp=dst+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG");os.replace(tmp,dst)
                out.append(dst)
            except Exception as exc:
                self._fail("ui.settings_inline_choice_category_resize",exc);out.append(src)
        return (out+[None,None])[:2]

    def _compact_neutral_assets(self, card_w):
        """Use the existing authored neutral cinematic glass, with no adaptive palette.

        The source art is already part of Ultra Stalker; this only performs the
        same horizontal 9-slice fit used by the other inline menus.
        """
        card_w=max(180,min(560,int(card_w or 320)))
        normal=str(self.asset("cinematic_row_glass_premium_68.png") or "")
        selected=str(self.asset("cinematic_row_selected_neutral.png") or normal)
        if card_w==460:
            return normal,selected
        out=[]
        try:
            from .persistent_cache import GENERATED as _GENERATED
            cache=os.path.join(_GENERATED,"inline_neutral_glass")
            if not os.path.isdir(cache):
                os.makedirs(cache,mode=0o700)
        except Exception:
            cache="/tmp"
        for src,kind in ((normal,"normal"),(selected,"selected")):
            if not (src and os.path.isfile(src)):
                out.append(src);continue
            try:
                if _SettingsImage is None:
                    out.append(src);continue
                stamp=int(os.path.getmtime(src))
                sig=hashlib.sha1((src+"|"+str(stamp)+"|"+str(card_w)+"|neutral-inline-v1").encode("utf-8","ignore")).hexdigest()[:16]
                dst=os.path.join(cache,"neutral_inline_%s_%s_%d.png"%(sig,kind,card_w))
                if not (os.path.isfile(dst) and os.path.getsize(dst)>64):
                    im=_SettingsImage.open(src).convert("RGBA")
                    sw,sh=im.size;target_h=54
                    if sh!=target_h:
                        resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                        im=im.resize((sw,target_h),resample);sw,sh=im.size
                    cap=max(28,min(56,sw//4,max(28,(card_w-12)//2)))
                    resample=getattr(getattr(_SettingsImage,"Resampling",_SettingsImage),"LANCZOS",getattr(_SettingsImage,"LANCZOS",1))
                    if card_w>cap*2+8 and sw>cap*2+8:
                        canvas=_SettingsImage.new("RGBA",(card_w,target_h),(0,0,0,0))
                        left=im.crop((0,0,cap,target_h));right=im.crop((sw-cap,0,sw,target_h))
                        center=im.crop((cap,0,sw-cap,target_h)).resize((card_w-cap*2,target_h),resample)
                        canvas.paste(left,(0,0));canvas.paste(center,(cap,0));canvas.paste(right,(card_w-cap,0))
                    else:
                        canvas=im.resize((card_w,target_h),resample)
                    tmp=dst+".tmp.%d"%os.getpid();canvas.save(tmp,"PNG");os.replace(tmp,dst)
                out.append(dst)
            except Exception as exc:
                self._fail("ui.settings_inline_choice_neutral_resize",exc);out.append(src)
        return (out+[None,None])[:2]

    def show(self,choices,selection=0,right=1850,region_top=590,region_h=400,on_accept=None,on_close=None,
             min_card_w=300,max_card_w=560,padding=58,anchor_bottom=None,center_x=None,left_x=None,
             visual_style="settings",row_h=None,max_visible=None,
             adaptive_source=None,adaptive_accent=None,adaptive_accent_soft=None):
        self.choices=list(choices or [])
        self.accept_callback=on_accept
        self.close_callback=on_close
        self._region=(int(right),int(region_top),int(region_h))
        self.labels=[self.tr(str(c[0])) if isinstance(c,(tuple,list)) and c else self.tr(str(c)) for c in self.choices]

        # Exact NovaSettingsScreen._context_prepare_floating_choices geometry.
        # Text-only category menus must fit both normal white labels and the
        # slightly larger selected green label without clipping.
        style_name=str(visual_style or "settings").lower()
        use_category=(style_name=="category")
        use_neutral=(style_name=="neutral_glass")
        use_text_only=(style_name=="text_only")
        base_measure=22
        selected_measure=26 if use_text_only else 22
        label_px=max([max(self._measure(x,base_measure), self._measure(x,selected_measure)) for x in self.labels] or [220])
        try:min_w=max(220,int(min_card_w))
        except Exception:min_w=300
        try:max_w=max(min_w,min(560,int(max_card_w)))
        except Exception:max_w=560
        try:pad=max(32,min(90,int(padding)))
        except Exception:pad=58
        card_w=max(min_w,min(max_w,label_px+pad))
        list_w=card_w+8
        default_row_h=66 if use_category else (62 if use_neutral else (66 if use_text_only else self.ROW_H))
        try:row_h=max(54,min(90,int(row_h if row_h is not None else default_row_h)))
        except Exception:row_h=default_row_h
        try:visible_limit=max(1,min(8,int(max_visible if max_visible is not None else self.MAX_VISIBLE)))
        except Exception:visible_limit=self.MAX_VISIBLE
        visible=max(1,min(visible_limit,int(len(self.choices) or 1)))
        list_h=visible*row_h
        if left_x is not None:
            list_x=int(left_x)
        elif center_x is None:
            list_x=int(right)-list_w
        else:
            list_x=int(round(float(center_x)-float(list_w)/2.0))
        if anchor_bottom is None:
            list_y=int(region_top)+max(0,(int(region_h)-list_h)//2)
        else:
            list_y=max(0,int(anchor_bottom)-list_h)
        self._geom(list_x,list_y,list_w,list_h)
        if use_text_only:
            # No glass/adaptive work at all for the Movies Categories MENU rail.
            self.row_asset,self.selected_asset=None,None
        elif use_category:
            self.row_asset,self.selected_asset=self._compact_category_assets(
                card_w,adaptive_source=adaptive_source,
                adaptive_accent=adaptive_accent,adaptive_accent_soft=adaptive_accent_soft,
            )
        elif use_neutral:
            self.row_asset,self.selected_asset=self._compact_neutral_assets(card_w)
        else:
            self.row_asset,self.selected_asset=self._compact_episode_assets(card_w)

        try:
            lst=self.screen[self.widget_name]
            if lst.instance is not None:
                lst.instance.setSelectionEnable(0);lst.instance.setTransparent(1)
                try:lst.instance.setScrollbarMode(2)
                except Exception:pass
            lst.row_width=list_w
            lst.set_layout(row_h,0,22,16,row_style=("category_menu_text" if use_text_only else "settings_dialog"))
        except Exception as exc:self._fail("ui.settings_inline_choice_layout",exc)

        selection=max(0,min(int(selection or 0),len(self.choices)-1)) if self.choices else 0
        rows=[]
        for i,c in enumerate(self.choices):
            details={"selected":i==selection,"row_asset":self.row_asset,"row_selected_asset":self.selected_asset,"meta":""}
            rows.append((self.labels[i],None,c,details))
        try:
            self.screen[self.widget_name].set_icon_rows(rows)
            self.screen[self.widget_name].moveToIndex(selection)
            self.screen[self.widget_name].show()
        except Exception as exc:self._fail("ui.settings_inline_choice_show",exc)
        self.last_idx=selection;self.active=True
        try:self.screen[self.actions_name].setEnabled(True)
        except Exception:pass
        return True

    def _selection_changed(self):
        if not self.active:return
        try:idx=self.screen[self.widget_name].getSelectedIndex()
        except Exception:return
        old=self.last_idx
        if idx==old:return
        self.last_idx=idx
        for j in set((old,idx)):
            if 0<=j<len(self.choices):
                c=self.choices[j]
                details={"selected":j==idx,"row_asset":self.row_asset,"row_selected_asset":self.selected_asset,"meta":""}
                try:self.screen[self.widget_name].update_icon_row(j,(self.labels[j],None,c,details))
                except Exception as exc:self._fail("ui.settings_inline_choice_row",exc)
        try:self.screen[self.widget_name].l.invalidate()
        except Exception as exc:self._fail("ui.settings_inline_choice_refresh",exc)

    def move(self,direction):
        if not self.active:return False
        try:
            lst=self.screen[self.widget_name]
            if direction=="up":lst.wrap_up()
            elif direction=="down":lst.wrap_down()
            elif direction=="left":lst.page_left()
            elif direction=="right":lst.page_right()
            return True
        except Exception as exc:self._fail("ui.settings_inline_choice_move",exc)
        return False

    def selected_index(self):
        try:return int(self.screen[self.widget_name].getSelectedIndex())
        except Exception:return -1

    def selected_choice(self):
        idx=self.selected_index()
        return self.choices[idx] if 0<=idx<len(self.choices) else None

    def accept(self):
        if not self.active:return False
        choice=self.selected_choice();callback=self.accept_callback
        self._hide_internal()
        if callback is not None:
            try:callback(choice)
            except Exception as exc:self._fail("ui.settings_inline_choice_accept",exc)
        return True

    def _hide_internal(self):
        self.active=False
        try:self.screen[self.actions_name].setEnabled(False)
        except Exception:pass
        try:self.screen[self.widget_name].hide();self.screen[self.widget_name].set_icon_rows([])
        except Exception as exc:self._fail("ui.settings_inline_choice_hide",exc)
        self.choices=[];self.labels=[];self.accept_callback=None

    def hide(self):
        """Hide without invoking the caller close callback."""
        if not self.active:return False
        self._hide_internal();self.close_callback=None
        return True

    def close(self):
        if not self.active:return False
        callback=self.close_callback
        self._hide_internal();self.close_callback=None
        if callback is not None:
            try:callback()
            except Exception as exc:self._fail("ui.settings_inline_choice_close",exc)
        return True
