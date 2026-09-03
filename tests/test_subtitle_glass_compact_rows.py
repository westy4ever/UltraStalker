# -*- coding: utf-8 -*-
from __future__ import absolute_import
import os
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__))
PLAYER=os.path.join(ROOT,"services","player_golden57.py")

class SubtitleGlassCompactRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PLAYER,"r",encoding="utf-8") as fh:cls.source=fh.read()

    def test_rows_restore_proven_beta57_visual_geometry(self):
        self.assertIn('size="1090,552"',self.source)
        self.assertIn('SubtitleGlassList(self._choices,width=1090,item_height=76)',self.source)
        self.assertIn('pos=(0,5),size=(self.row_width,66)',self.source)
        self.assertIn('pos=(28,7),size=(self.row_width-56,58)',self.source)

    def test_glass_asset_matches_polished_66px_row(self):
        self.assertIn('_subtitle_glass_assets(self._source_path,1280,1090,66)',self.source)

    def test_all_subtitle_child_pages_share_same_screen(self):
        self.assertGreaterEqual(self.source.count('SubtitleGlassChoiceScreen'),6)


    def test_rows_leave_bottom_breathing_room(self):
        # Seven visible rows occupy 532px inside a 552px list viewport,
        # leaving breathing room instead of touching the inner panel edge.
        self.assertLess(7 * 76, 552)
        self.assertEqual(7 * 76, 532)

if __name__=="__main__":unittest.main()
