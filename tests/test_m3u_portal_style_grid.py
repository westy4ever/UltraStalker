# -*- coding: utf-8 -*-
from __future__ import absolute_import
import os
import unittest

class M3UPortalStyleGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_grid_base.py"),"r",encoding="utf-8") as fh:
            cls.base=fh.read()
        with open(os.path.join(root,"ui_grid_artwork.py"),"r",encoding="utf-8") as fh:
            cls.art=fh.read()

    def test_m3u_source_detector_exists(self):
        self.assertIn("def _grid_is_m3u_source(self):",self.base)
        self.assertIn('source=="m3u"',self.base)

    def test_prepared_m3u_page_uses_local_art_even_with_provider_url(self):
        start=self.base.index("    def _prepare_grid_page")
        end=self.base.index("\n    def _cache_prepared_page",start)
        block=self.base[start:end]
        self.assertIn("use_local_first=bool(self._grid_is_m3u_source())",block)
        self.assertIn('if provider_value and not use_local_first:',block)
        self.assertIn('art_row["visual_locked"]=True',block)

    def test_first_real_m3u_poster_locks_slot(self):
        self.assertIn("self._grid_visual_locks.setdefault(slot,str(path))",self.art)
        self.assertGreaterEqual(self.art.count("self._grid_visual_locks.setdefault(slot,str(path))"),2)

    def test_locked_slot_rejects_late_repaint(self):
        start=self.art.index("    def _grid_queue_decode")
        end=self.art.index("\n    def _grid_schedule_adaptive_selector",start)
        block=self.art[start:end]
        self.assertIn("if locked and current and str(path)!=current:",block)
        self.assertIn("return",block)

    def test_details_return_does_not_repaint_locked_m3u_card(self):
        start=self.base.index("    def _details_returned")
        end=self.base.index("\n    def _play_live",start)
        block=self.base[start:end]
        self.assertIn("self._grid_is_m3u_source()",block)
        self.assertIn("_grid_visual_locks",block)

    def test_adjacent_m3u_art_is_warmed(self):
        self.assertIn("def _warm_m3u_prepared_page_art",self.base)
        start=self.base.index("    def _prefetch_adjacent_pages")
        end=self.base.index("\n    def _apply_prepared_page",start)
        block=self.base[start:end]
        self.assertIn("self._warm_m3u_prepared_page_art",block)

    def test_prepared_page_refreshes_hdd_before_paint(self):
        start=self.base.index("    def _apply_prepared_page")
        end=self.base.index("\n    def load_page",start)
        block=self.base[start:end]
        self.assertIn("self._refresh_prepared_m3u_art_from_hdd(payload)",block)

if __name__=="__main__":
    unittest.main()
