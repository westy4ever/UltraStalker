# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from Plugins.Extensions.UltraStalker.artwork_v2 import ArtworkV2, _safe_catalogue_query, identity_cache_compatible


class _FakeTMDB(object):
    def __init__(self):
        self.queries = []

    def _get(self, path, params):
        self.queries.append(str((params or {}).get("query") or ""))
        return {"results": []}


class ArtworkPureGuardTests(unittest.TestCase):
    def test_generic_pure_query_is_rejected(self):
        self.assertEqual(_safe_catalogue_query("Pure"), "")
        self.assertEqual(_safe_catalogue_query("PURE"), "")

    def test_composite_title_keeps_real_movie_name(self):
        value = _safe_catalogue_query("السادة الافاضل Pure (2025)")
        self.assertEqual(value, "السادة الافاضل")



    def test_old_pure_identity_cache_is_rejected(self):
        item = {
            "_raw_name": "السادة الافاضل Pure (2025)",
            "name": "السادة الافاضل Pure (2025)",
            "year": "2025",
        }
        cached = {
            "tmdb_id": 12345,
            "title": "Pure",
            "original_title": "Pure",
            "year": "2025",
        }
        self.assertFalse(identity_cache_compatible(item, cached))

    def test_rescue_never_sends_pure_to_tmdb(self):
        art = ArtworkV2.__new__(ArtworkV2)
        art.language = "ar-EG"
        art.lang = "ar"
        art.timeout = 4
        art.tmdb = _FakeTMDB()
        item = {
            "_provider_name": "Pure",
            "_raw_name": "السادة الافاضل Pure (2025)",
            "name": "السادة الافاضل Pure (2025)",
            "title": "السادة الافاضل Pure (2025)",
            "year": "2025",
        }
        art._resolve_poster_rescue("vod", item)
        normalized = [q.strip().casefold() for q in art.tmdb.queries]
        self.assertNotIn("pure", normalized)
        self.assertTrue(any("السادة" in q for q in art.tmdb.queries))


if __name__ == "__main__":
    unittest.main()
