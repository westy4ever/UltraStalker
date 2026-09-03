# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import re
import unittest


class HomeSnapshotAndDetailTitleFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_screens_home.py"),"r",encoding="utf-8") as fh:
            cls.home=fh.read()
        with open(os.path.join(root,"ui_screens_details.py"),"r",encoding="utf-8") as fh:
            cls.details=fh.read()
        with open(os.path.join(root,"ui_skin_templates.py"),"r",encoding="utf-8") as fh:
            cls.skin=fh.read()

    def test_home_snapshot_uses_hdd_runtime_dir(self):
        self.assertIn('return os.path.join(directory,"home_visual_snapshot.json")',self.home)
        self.assertIn('"persistent":True',self.home)

    def test_home_does_not_release_hero_when_child_opens(self):
        start=self.home.index("    def _home_hidden_release(self):")
        end=self.home.index("\n    def _layout_ready",start)
        block=self.home[start:end]
        self.assertNotIn("_image_suspend()",block)
        self.assertIn("self._home_return_pending=True",block)

    def test_home_fast_return_performs_no_decode_or_full_rebind(self):
        start=self.home.index("    def _home_fast_return(self):")
        end=self.home.index("\n    def _home_shown",start)
        block=self.home[start:end]
        self.assertNotIn("_decode_picture",block)
        self.assertNotIn("_force_home_visual_rebind",block)
        self.assertNotIn("_apply_home_hero",block)

    def test_details_skin_starts_safe_small_not_oversized(self):
        detail=re.search(r'DETAIL_SKIN.*?<widget name="name"[^>]*font="Regular;(\d+)"',self.skin,re.S)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.group(1),"16")
        series=re.search(r'SERIES_EPISODES_SKIN.*?<widget name="name"[^>]*font="Regular;(\d+)"',self.skin,re.S)
        self.assertIsNotNone(series)
        self.assertEqual(series.group(1),"16")

    def test_title_fit_is_deferred_after_layout_and_updates(self):
        self.assertIn("self._title_fit_timer.start(25,True)",self.details)
        self.assertIn("self._title_fit_pass<3",self.details)
        self.assertGreaterEqual(self.details.count("self._schedule_title_fit()"),3)

    def test_title_fit_uses_widget_width_and_full_text(self):
        start=self.details.index("    def _fit_single_line_font")
        end=self.details.index("\n    def _fit_description_font",start)
        block=self.details[start:end]
        self.assertIn("width=max(80,int(inst.size().width())-28)",block)
        self.assertIn("estimated=int(float(width)/units)",block)
        self.assertIn("0x0600<=code<=0x06ff",block)
        self.assertIn('elif "A"<=ch<="Z"',block)
        self.assertIn("inst.setFont(gFont(\"Regular\",chosen))",block)


if __name__=="__main__":
    unittest.main()
