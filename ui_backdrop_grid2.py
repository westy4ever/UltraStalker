# -*- coding: utf-8 -*-
"""Backdrop Grid 2: exact R208 seven-poster presentation on clean behavior."""
from __future__ import absolute_import
try:
    from enigma import ePoint, eSize, gFont
except Exception:
    ePoint=eSize=gFont=None
from . import ui_backdrop_grid as _bg

BACKDROP_GRID2_SKIN=""

def configure_backdrop_grid2(**deps):
    globals().update(deps)
    if "BACKDROP_GRID2_SKIN" in deps:
        PremiumBackdropGrid2Screen.skin=deps["BACKDROP_GRID2_SKIN"]

class PremiumBackdropGrid2Screen(_bg.PremiumBackdropGridScreen):
    skin=BACKDROP_GRID2_SKIN
    columns=7
    page_size=7
    image_size=(232,349)
    card_positions=[(55+(263*i),679) for i in range(7)]

    def _bd_frame_asset(self):
        return _bg.asset("poster_frame_neutral_bd2_232x349.png")

    def _bd_selector_dimensions(self):
        return (232,349)

    def _bg_fit_overview(self,text):
        if gFont is None:return
        try:
            inst=self["bd_overview"].instance
            if inst is None:return
            value=str(text or "");start=27
            if len(value)>520:start=22
            elif len(value)>420:start=23
            elif len(value)>320:start=24
            elif len(value)>240:start=25
            elif len(value)>170:start=26
            for candidate in range(start,20,-1):
                inst.setFont(gFont("Regular",candidate))
                try:
                    if int(inst.calculateSize().height())<=198:break
                except Exception:break
        except Exception:pass

    def _bd_force_geometry(self):
        if ePoint is None or eSize is None:return
        try:
            for name,x,y,w,h in (("page_label",55,649,470,28),("status",650,649,620,28)):
                inst=self[name].instance
                if inst is not None:inst.move(ePoint(x,y));inst.resize(eSize(w,h))
            for name,x,y,w,h in (("title_logo",55,60,432,210),("bd_title_fallback",55,86,500,180)):
                inst=self[name].instance
                if inst is not None:inst.move(ePoint(x,y));inst.resize(eSize(w,h))
            hud=(
                ("bd_year_bg",55,295,130,50),("bd_year",100,302,72,36),
                ("bd_quality_bg",198,295,150,50),("bd_quality_logo",212,303,122,34),("bd_quality",214,302,118,36),
                ("bd_runtime_bg",361,295,150,50),("bd_runtime_icon",374,304,32,32),("bd_runtime",411,300,88,40),
                ("bd_genre_bg",55,355,440,50),("bd_age_rating",72,360,92,40),("bd_genre",168,362,310,36),
                ("bd_overview_bg",55,415,456,232),("bd_overview",76,427,414,206),
            )
            for name,x,y,w,h in hud:
                inst=self[name].instance
                if inst is not None:inst.move(ePoint(x,y));inst.resize(eSize(w,h))
            for pos in range(self.page_size):
                x=55+(263*pos)
                for name,gx,gy,gw,gh in (("art%d"%pos,x,679,232,349),("card_chrome%d"%pos,x,679,232,349),("item_title%d"%pos,2000,0,1,1),("item_meta%d"%pos,2000,0,1,1)):
                    inst=self[name].instance
                    if inst is not None:inst.move(ePoint(gx,gy));inst.resize(eSize(gw,gh))
            for name in ("selection","selection_adaptive"):
                inst=self[name].instance
                if inst is not None:inst.move(ePoint(55,679));inst.resize(eSize(232,349))
            self._scaled_card_positions=[(int((55+263*i)*self._grid_sx),int(679*self._grid_sy)) for i in range(self.page_size)]
        except Exception as exc:_bg.optional_failure("backdrop_grid2.r208_geometry",exc)
