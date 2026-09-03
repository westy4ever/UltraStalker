# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class HomeFastReturnAndTitleFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_screens_home.py"),"r",encoding="utf-8") as fh:
            cls.home=fh.read()
        with open(os.path.join(root,"ui_screens_details.py"),"r",encoding="utf-8") as fh:
            cls.details=fh.read()

    def test_home_tracks_child_return_state(self):
        self.assertIn("self._home_has_shown_once=False;self._home_return_pending=False",self.home)
        self.assertIn("self._home_return_pending=True",self.home)

    def test_home_child_return_uses_lightweight_restore(self):
        start=self.home.index("    def _home_shown(self):")
        end=self.home.index("\n    def _home_hidden_release",start)
        block=self.home[start:end]
        self.assertIn("if returning:",block)
        self.assertIn("self._home_fast_return()",block)
        # Delayed full rebind must remain cold-first-show only.
        returning=block[block.index("if returning:"):block.index("else:",block.index("if returning:"))]
        self.assertNotIn("self._home_rebind_timer.start(90,True)",returning)

    def test_fast_return_reuses_already_bound_visuals(self):
        start=self.home.index("    def _home_fast_return(self):")
        end=self.home.index("\n    def _home_shown",start)
        block=self.home[start:end]
        self.assertNotIn("_decode_picture",block)
        self.assertNotIn("_ensure_home_visual_materials",block)
        self.assertNotIn("_rebind_home_materials",block)
        self.assertNotIn("_build_home_mood_assets",block)
        self.assertNotIn("_build_home_adaptive_focus",block)
        self.assertIn("self._update_focus()",block)

    def test_recent_cards_still_refresh_after_fast_return(self):
        start=self.home.index("    def _home_shown(self):")
        end=self.home.index("\n    def _home_hidden_release",start)
        block=self.home[start:end]
        self.assertIn("self._load_recent_cards()",block)

    def test_title_fit_uses_widget_width_and_glyph_units(self):
        start=self.details.index("    def _fit_single_line_font")
        end=self.details.index("\n    def ",start+10)
        block=self.details[start:end]
        self.assertIn("def glyph_units():",block)
        self.assertIn("estimated=int(float(width)/units)",block)
        self.assertIn("1.16 if has_ar and has_latin else 1.08",block)
        self.assertIn("0x0600<=code<=0x06ff",block)

    def test_title_fit_keeps_full_range_and_single_line(self):
        self.assertIn(
            "def _fit_single_line_font(self, widget_name, text, max_size=42, min_size=16):",
            self.details,
        )
        self.assertIn('if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)',self.details)


if __name__=="__main__":
    unittest.main()
