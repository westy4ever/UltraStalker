# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class PageVisualRamRevisitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root,"ui_grid_base.py"),"r",encoding="utf-8") as fh:
            cls.base_source=fh.read()
        with open(os.path.join(root,"ui_grid_artwork.py"),"r",encoding="utf-8") as fh:
            cls.art_source=fh.read()

    def test_visual_ram_is_content_keyed_and_bounded(self):
        self.assertIn("self._page_visual_ram=OrderedDict()",self.base_source)
        self.assertIn("content_cache_key(self.profile,self.media_type,item)",self.base_source)
        self.assertIn("self._page_visual_ram_limit=512",self.base_source)

    def test_folder_revisit_restores_visuals_instead_of_blank_rows(self):
        start=self.base_source.index("    def _load_folder_view_page")
        end=self.base_source.index("\n    def ",start+10)
        block=self.base_source[start:end]
        self.assertIn(
            "self._prepared_art_for_render=[self._page_visual_get(item) or {} for item in valid]",
            block,
        )
        self.assertNotIn("self._prepared_art_for_render=[{} for _ in valid]",block)

    def test_warm_visual_skips_network_prefetch(self):
        start=self.base_source.index("    def _prefetch_visible_page_details")
        end=self.base_source.index("\n    def ",start+10)
        block=self.base_source[start:end]
        ram=block.index("ram_visual=self._page_visual_get(item)")
        submit=block.index("_FAST_POSTER_EXECUTOR.submit(fast_worker)")
        self.assertLess(ram,submit)
        self.assertIn('if ram_visual and (ram_visual.get("display") or ram_visual.get("poster")):',block)
        self.assertIn("continue",block[ram:submit])

    def test_decode_path_remembers_display_and_palette(self):
        self.assertIn(
            "self._remember_grid_visual(slot,display=path,poster=palette_path,palette=palette_path)",
            self.art_source,
        )

    def test_adaptive_card_is_preserved_in_visual_ram(self):
        self.assertIn(
            "self._remember_grid_visual(slot,palette=path,card=card)",
            self.art_source,
        )

    def test_ram_get_validates_files_and_rejects_placeholders(self):
        self.assertIn("def _page_visual_valid_path",self.base_source)
        self.assertIn('"placeholder" in name',self.base_source)
        self.assertIn('name.startswith(("poster_movie","poster_series","placeholder_live"))',self.base_source)


if __name__=="__main__":
    unittest.main()
