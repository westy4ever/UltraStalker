# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class SeriesAsyncLaneAndGridChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_screens_series.py"),"r",encoding="utf-8") as fh:
            cls.series=fh.read()
        with open(os.path.join(root,"ui_grid_artwork.py"),"r",encoding="utf-8") as fh:
            cls.art=fh.read()
        with open(os.path.join(root,"ui_grid_base.py"),"r",encoding="utf-8") as fh:
            cls.base=fh.read()

    def test_seasons_use_independent_async_lane(self):
        self.assertIn("def _run_series_async",self.series)
        self.assertIn("TASKS.submit(func,self._series_jobs",self.series)
        start=self.series.index("    def load_seasons(self):")
        end=self.series.index("\n    @staticmethod",start)
        block=self.series[start:end]
        self.assertIn("self._run_series_async(",block)
        self.assertIn("self.client.series_seasons",block)
        self.assertNotIn("self._run_async(",block)

    def test_load_seasons_is_not_blocked_by_details_busy(self):
        start=self.series.index("    def load_seasons(self):")
        end=self.series.index("\n    @staticmethod",start)
        block=self.series[start:end]
        self.assertNotIn("if self._busy:",block)
        self.assertIn('self["status"].setText("Loading seasons...")',block)

    def test_load_episodes_is_not_blocked_by_details_busy(self):
        start=self.series.index("    def load_episodes(self, season):")
        end=self.series.index("\n    def select",start)
        block=self.series[start:end]
        self.assertNotIn("if self._busy:",block)
        self.assertIn("self._run_series_async",block)

    def test_series_callbacks_are_token_guarded(self):
        self.assertIn("token=self._series_request_token",self.series)
        self.assertIn("token!=self._series_request_token",self.series)

    def test_all_visible_cards_schedule_adaptive_chrome(self):
        start=self.art.index("    def _grid_queue_decode")
        end=self.art.index("\n    def _grid_schedule_adaptive_selector",start)
        block=self.art[start:end]
        self.assertIn('if getattr(self,"media_type",None) in ("vod","series"):',block)
        self.assertIn("self._grid_schedule_adaptive_selector(generation, slot, palette_path)",block)
        self.assertNotIn("_near",block)

    def test_card_chrome_never_switches_to_selected_variant(self):
        self.assertNotIn("_grid_card_chrome_from_poster(path, selected=True)",self.art)
        self.assertNotIn("_grid_card_chrome_cached(path,selected=True)",self.art)

    def test_focus_debounce_no_longer_restores_previous_card_chrome(self):
        start=self.base.index("    def _apply_debounced_grid_focus(self):")
        end=self.base.index("\n    def _warm_idle_grid_page",start)
        block=self.base[start:end]
        self.assertNotIn("card_chrome%d",block)
        self.assertIn("self._grid_apply_current_selector()",block)
        self.assertIn("self._apply_poster_live_hud",block)

        # Missing HUD chrome is rebuilt only after stable focus and only on the
        # dedicated adaptive worker, never inline on arrow navigation.
        hstart=self.base.index("    def _apply_poster_live_hud(self, item=None):")
        hend=self.base.index("\n    def _nav_allowed",hstart)
        hblock=self.base[hstart:hend]
        self.assertIn("_ADAPTIVE_EXECUTOR.submit(worker)",hblock)
        self.assertIn("_poster_hud_pending",hblock)
        self.assertNotIn("chrome=_build_poster_adaptive_chrome_clean(source,key)",hblock)


if __name__=="__main__":
    unittest.main()
