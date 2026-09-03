# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest


class CanonicalArtworkAndSeriesCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.path.dirname(os.path.dirname(__file__))
        def read(name):
            with open(os.path.join(root,name),"r",encoding="utf-8") as fh:return fh.read()
        cls.ui=read("ui.py")
        cls.grid=read("ui_grid_base.py")
        cls.details=read("ui_screens_details.py")
        cls.series=read("ui_screens_series.py")

    def test_content_owned_artwork_cache_exists(self):
        self.assertIn('CONTENT_ART_DIR = os.path.join(PERSISTENT_GENERATED_DIR, "content_art")',self.ui)
        self.assertIn("def _promote_content_art(",self.ui)
        self.assertIn("def _load_content_art(",self.ui)
        self.assertIn('os.path.join(root,"poster.jpg")',self.ui)
        self.assertIn('os.path.join(root,"backdrop.jpg")',self.ui)

    def test_visual_bundle_promotes_art_to_content_owned_hdd(self):
        start=self.ui.index("def _save_visual_bundle")
        end=self.ui.index("\ndef _persistent_write_ok",start)
        block=self.ui[start:end]
        self.assertIn("_promote_content_art(",block)
        self.assertIn('poster=payload.get("poster")',block)
        self.assertIn('backdrop=payload.get("backdrop")',block)

    def test_grid_checks_hdd_before_provider_download(self):
        start=self.grid.index("def fast_worker",self.grid.index("def _prefetch_visible_page_details"))
        end=self.grid.index("try:",self.grid.index("# 3) Only provider-less unresolved cards",start))
        block=self.grid[start:end]
        hdd=block.index("_load_visual_bundle(")
        provider=block.index("_download_portal_artwork(")
        self.assertLess(hdd,provider)

        # Explicit blue-button folder check must short-circuit a complete
        # content-owned HDD pair before manifest/detail/provider/TMDB work.
        cstart=self.grid.index("        def process_item(item):",self.grid.index("def _cache_folder_artwork_worker"))
        cend=self.grid.index("                # Merge every trusted HDD identity source",cstart)
        cblock=self.grid[cstart:cend]
        owned=cblock.index("_load_content_art(")
        early=cblock.index("if poster_ok and backdrop_ok:")
        manifest=self.grid.index("load_artwork_v2_manifest",cstart)
        self.assertLess(owned,early)
        self.assertLess(cend,manifest)
        self.assertIn("return 0,1,0,0",cblock[early:])

    def test_grid_hands_exact_poster_to_details(self):
        self.assertIn('detail_item["_ultra_poster_source"]=str(palette_source)',self.grid)

    def test_details_authoritative_poster_cannot_be_repainted(self):
        self.assertIn('self._poster_authoritative_source=str(item.get("_ultra_poster_source")',self.details)
        self.assertIn('if poster_authority and os.path.isfile(poster_authority):',self.details)
        self.assertIn('if not getattr(self,"_poster_authoritative_source",""):',self.details)

    def test_seasons_and_episodes_have_persistent_catalog_cache(self):
        self.assertIn('_SERIES_CATALOG_CACHE_DIR=os.path.join(_SERIES_CACHE_BASE,"series_catalog")',self.series)
        self.assertIn("_SERIES_CATALOG_TTL=6*60*60",self.series)
        self.assertIn('cached,updated=_series_cache_read(self.profile,self.series_item,"seasons")',self.series)
        self.assertIn('cached,updated=_series_cache_read(self.profile,self.series_item,"episodes",season)',self.series)

    def test_fresh_seasons_cache_returns_before_portal_request(self):
        start=self.series.index("    def load_seasons(self):")
        end=self.series.index("\n    @staticmethod",start)
        block=self.series[start:end]
        cached=block.index("if cached and age<_SERIES_CATALOG_TTL:")
        request=block.index("self.client.series_seasons")
        self.assertLess(cached,request)
        self.assertIn("return",block[cached:request])

    def test_fresh_episode_cache_skips_portal_catalog_request(self):
        start=self.series.index("    def load_episodes(self, season):")
        end=self.series.index("\n    def select(self):",start)
        block=self.series[start:end]
        fresh=block.index("if cached and age<_SERIES_CATALOG_TTL:")
        portal=block.index("self.client.series_episodes")
        self.assertLess(fresh,portal)
        self.assertIn("return",block[fresh:portal])


if __name__=="__main__":
    unittest.main()
