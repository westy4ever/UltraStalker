# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class GridProviderLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        path=os.path.join(root,"ui_grid_base.py")
        with open(path,"r",encoding="utf-8") as fh:
            cls.source=fh.read()

    def test_provider_owned_cards_seed_lock_before_emit(self):
        self.assertIn("provider_owned=set()",self.source)
        self.assertIn("provider_owned.add(key_for(_item))",self.source)

    def test_non_provider_emit_is_rejected_for_provider_owned_card(self):
        self.assertIn("if cache_key in provider_owned and not is_provider:",self.source)
        self.assertIn("return False",self.source)

    def test_tmdb_rescue_is_blocked_for_provider_owned_card(self):
        self.assertIn("if cache_key in provider_owned:",self.source)
        self.assertIn("TMDB is never allowed to replace its grid poster",self.source)

    def test_hdd_content_cache_is_attempted_before_provider_network(self):
        start=self.source.index("def fast_worker",self.source.index("def _prefetch_visible_page_details"))
        end=self.source.index("# 3) Only provider-less unresolved cards",start)
        block=self.source[start:end]
        hdd_pos=block.index("_load_visual_bundle(")
        provider_pos=block.index("_download_portal_artwork(")
        self.assertLess(hdd_pos,provider_pos)

    def test_prepared_page_keeps_stalker_lock_but_allows_m3u_local_prepaint(self):
        start=self.source.index("art_row=self._page_visual_get(item)")
        end=self.source.index("prepared_art.append(art_row)",start)
        block=self.source[start:end]
        self.assertIn("use_local_first=bool(self._grid_is_m3u_source())",block)
        self.assertIn("if provider_value and not use_local_first:",block)
        self.assertIn('art_row["provider_locked"]=True',block)
        self.assertIn('art_row["visual_locked"]=True',block)
        self.assertIn("load_shared_detail_snapshot",block)


if __name__=="__main__":
    unittest.main()
