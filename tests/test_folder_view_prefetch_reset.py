# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class FolderViewPrefetchResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_grid_base.py"),"r",encoding="utf-8") as fh:
            cls.source=fh.read()
        start=cls.source.index("    def _load_folder_view_page")
        end=cls.source.index("\n    def ",start+10)
        cls.block=cls.source[start:end]

    def test_folder_view_cancels_previous_prefetch_event(self):
        self.assertIn("old_cancel.set()",self.block)

    def test_folder_view_cancels_previous_futures(self):
        self.assertIn("future.cancel()",self.block)
        self.assertIn("self._page_prefetch_futures=[]",self.block)

    def test_folder_view_rotates_generation(self):
        self.assertIn("self._page_prefetch_generation+=1",self.block)
        self.assertIn("self._page_prefetch_cancel=threading.Event()",self.block)

    def test_folder_view_clears_seen_quality_and_retry_state(self):
        self.assertIn("self._page_prefetch_seen.clear()",self.block)
        self.assertIn("self._page_quality_seen.clear()",self.block)
        self.assertIn("self._page_art_retry_count.clear()",self.block)

    def test_reset_happens_before_render(self):
        reset=self.block.index("self._page_prefetch_seen.clear()")
        render=self.block.index("self._render_grid()")
        self.assertLess(reset,render)


if __name__=="__main__":
    unittest.main()
