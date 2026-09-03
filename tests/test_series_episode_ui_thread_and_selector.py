# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class SeriesEpisodeUiThreadAndSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_screens_series.py"),"r",encoding="utf-8") as fh:
            cls.series=fh.read()
        with open(os.path.join(root,"ui_grid_base.py"),"r",encoding="utf-8") as fh:
            cls.grid=fh.read()
        with open(os.path.join(root,"ui_grid_artwork.py"),"r",encoding="utf-8") as fh:
            cls.art=fh.read()

    def test_episode_render_has_no_persistence_reads(self):
        start=self.series.index("    def _render_episode_rows")
        end=self.series.index("\n    def load_episodes",start)
        block=self.series[start:end]
        self.assertNotIn("load_content_states(",block)
        self.assertNotIn("load_content_quality(",block)
        self.assertNotIn("load_content_qualities(",block)

    def test_episode_worker_preloads_states_and_quality(self):
        start=self.series.index("    def load_episodes(self, season):")
        end=self.series.index("\n    def select(self):",start)
        block=self.series[start:end]
        self.assertIn("def prepare(handle):",block)
        self.assertIn('load_content_states(self.profile,"episode",valid)',block)
        self.assertIn('load_content_qualities(self.profile,"episode",valid)',block)
        self.assertIn('load_content_quality(self.profile,"series",self.series_item or {})',block)

    def test_quality_badge_is_ram_only(self):
        start=self.series.index("    def _episode_quality_badge")
        end=self.series.index("\n    def _render_season_rows",start)
        block=self.series[start:end]
        self.assertIn("_episode_quality_cache",block)
        self.assertIn("_episode_parent_quality",block)
        self.assertNotIn("load_content_quality",block)

    def test_focus_applies_selector_immediately(self):
        start=self.grid.index("    def _update_selection(self):")
        end=self.grid.index("\n    def _apply_debounced_grid_focus",start)
        block=self.grid[start:end]
        immediate=block.index("self._grid_apply_current_selector()")
        debounce=block.index("self._grid_focus_timer.start(300,True)")
        self.assertLess(immediate,debounce)

    def test_selector_miss_schedules_current_poster_overlay(self):
        start=self.art.index("    def _grid_apply_current_selector(self):")
        end=self.art.index("\n    def ",start+10)
        block=self.art[start:end]
        self.assertIn("else:",block)
        self.assertIn("self._grid_schedule_adaptive_selector(self._grid_generation,slot,path)",block)

    def test_overlay_swap_keeps_old_visual_until_result_ready(self):
        self.assertIn(
            'if result != getattr(self,"_grid_selection_visual_path",""):',
            self.art,
        )
        self.assertIn(
            'self["selection"].instance.setPixmapFromFile(result)',
            self.art,
        )


if __name__=="__main__":
    unittest.main()
