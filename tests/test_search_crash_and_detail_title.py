# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class SearchCrashAndDetailTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_screens_search.py"),"r",encoding="utf-8") as fh:
            cls.search=fh.read()
        with open(os.path.join(root,"ui_screens_details.py"),"r",encoding="utf-8") as fh:
            cls.details=fh.read()

    def test_virtual_keyboard_has_safe_fallback_import(self):
        self.assertIn("from Screens.VirtualKeyBoard import VirtualKeyBoard",self.search)
        self.assertIn("VirtualKeyBoard = None",self.search)
        self.assertIn("from Screens.InputBox import InputBox",self.search)
        self.assertIn("InputBox = None",self.search)

    def test_search_info_icons_are_created_before_ready_layout(self):
        self.assertIn('self["info_icon1"]=Pixmap()',self.search)
        self.assertIn('self["info_icon2"]=Pixmap()',self.search)
        self.assertIn('self["info_icon3"]=Pixmap()',self.search)

    def test_prompt_never_references_undefined_keyboard(self):
        self.assertIn("if VirtualKeyBoard is not None:",self.search)
        self.assertIn("elif InputBox is not None:",self.search)
        self.assertIn("Search keyboard is unavailable on this image.",self.search)

    def test_details_title_is_not_hard_truncated(self):
        self.assertIn('self["name"] = Label(name)',self.details)
        self.assertNotIn('self["name"] = Label(name[:58])',self.details)
        self.assertNotIn('_clean_display_text(str(title),58)',self.details)

    def test_adaptive_title_has_safe_range_and_direct_width_fit(self):
        self.assertIn(
            "def _fit_single_line_font(self, widget_name, text, max_size=42, min_size=16):",
            self.details,
        )
        self.assertIn("estimated=int(float(width)/units)",self.details)
        self.assertIn("for size in range(chosen,int(min_size)-1,-1):",self.details)
        self.assertIn('if hasattr(inst,"setNoWrap"):inst.setNoWrap(1)',self.details)

    def test_provider_title_update_refits_font(self):
        self.assertIn('self._fit_single_line_font("name",visible,42,16)',self.details)


if __name__=="__main__":
    unittest.main()
