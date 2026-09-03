# -*- coding: utf-8 -*-
from __future__ import absolute_import
import os
import unittest

class M3USeriesFastPathAndPosterFirstPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        def read(name):
            with open(os.path.join(root,name),"r",encoding="utf-8") as fh:
                return fh.read()
        cls.m3u=read("m3u_adapter.py")
        cls.grid=read("ui_grid_base.py")

    def test_series_info_persist_is_background_only(self):
        start=self.m3u.index("    def _ensure_xtream_series_info")
        end=self.m3u.index("\n    def series_seasons",start)
        block=self.m3u[start:end]
        self.assertIn("self._schedule_series_persist()",block)
        self.assertIn("def _schedule_series_persist",self.m3u)
        self.assertIn("_SERIES_PERSIST_EXECUTOR.submit(self._series_persist_worker)",self.m3u)
        # No synchronous catalogue write may block season delivery.
        sync_lines=[line.strip() for line in block.splitlines() if "_write_persistent()" in line]
        self.assertEqual(sync_lines,[])
        # Bursts are coalesced instead of queueing one full HDD write per focus.
        self.assertIn("if self._series_persist_running:",self.m3u)

    def test_series_identity_aliases_id_and_series_id(self):
        self.assertIn("def _series_index_keys",self.m3u)
        self.assertIn("def _series_index_lookup",self.m3u)
        start=self.m3u.index("    def _ensure_xtream_series_info")
        end=self.m3u.index("\n    def series_seasons",start)
        block=self.m3u[start:end]
        self.assertIn("for key in alias_keys:",block)
        self.assertIn("self._series_index[key]=data",block)
        self.assertIn("if real_sid not in alias_keys:alias_keys.append(real_sid)",block)

    def test_seasons_and_episodes_use_alias_lookup(self):
        start=self.m3u.index("    def series_seasons")
        end=self.m3u.index("\n    def create_link",start)
        block=self.m3u[start:end]
        self.assertGreaterEqual(block.count("self._series_index_lookup(series_item)"),2)

    def test_focused_series_prefetches_hierarchy(self):
        self.assertIn("def _prefetch_selected_series_hierarchy",self.grid)
        start=self.grid.index("    def _update_selection")
        end=self.grid.index("\n    def _prefetch_selected_series_hierarchy",start)
        block=self.grid[start:end]
        self.assertIn('if self.media_type=="series":',block)
        self.assertIn("self._series_prefetch_timer.start(900,True)",block)
        self.assertIn("self.client.prefetch_series_hierarchy",self.grid)
        self.assertIn("_SERIES_HIERARCHY_PREFETCH_EXECUTOR.submit(worker)",self.grid)
        # Hierarchy network work must not share the adjacent-page/category executor.
        prefetch_start=self.grid.index("    def _prefetch_selected_series_hierarchy")
        prefetch_end=self.grid.index("\n    def _apply_debounced_grid_focus",prefetch_start)
        prefetch_block=self.grid[prefetch_start:prefetch_end]
        self.assertNotIn("_CATEGORY_PREFETCH_EXECUTOR.submit(worker)",prefetch_block)

    def test_prepared_page_builds_cover_thumb_before_first_paint(self):
        start=self.grid.index("    def _prepare_grid_page")
        end=self.grid.index("\n    def _cache_prepared_page",start)
        block=self.grid[start:end]
        self.assertIn("_build_cover_thumbnail(poster_local,grid_thumb,self._grid_image_size)",block)
        self.assertIn('art_row["display"]=display',block)
        self.assertIn("_build_cover_thumbnail(local,thumb,self._grid_image_size)",block)

if __name__=="__main__":
    unittest.main()
