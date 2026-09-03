# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from Plugins.Extensions.UltraStalker.persistent_cache import (
    _canonical_metadata_title,
    _snapshot_identity_compatible,
)


class PersistentPureRevisitTests(unittest.TestCase):
    def test_composite_pure_branding_is_removed_from_cache_identity(self):
        self.assertEqual(
            _canonical_metadata_title("السادة الأفاضل Pure (2025)"),
            "السادة الأفاضل",
        )

    def test_real_movie_named_pure_is_not_erased(self):
        self.assertEqual(_canonical_metadata_title("Pure"), "pure")

    def test_old_generic_pure_snapshot_is_rejected_for_real_title(self):
        item = {
            "_raw_name": "السادة الأفاضل Pure (2025)",
            "name": "السادة الأفاضل Pure (2025)",
            "year": "2025",
        }
        stale = {
            "tmdb_id": 1,
            "title": "Pure",
            "original_title": "Pure",
            "year": "2025",
            "identity_verified": True,
            "identity_source": "tmdb_search",
        }
        self.assertFalse(_snapshot_identity_compatible(item, stale))

    def test_correct_snapshot_survives_revisit_check(self):
        item = {
            "_raw_name": "السادة الأفاضل Pure (2025)",
            "name": "السادة الأفاضل Pure (2025)",
            "year": "2025",
        }
        good = {
            "tmdb_id": 2,
            "title": "السادة الأفاضل",
            "original_title": "السادة الأفاضل",
            "year": "2025",
            "identity_verified": True,
            "identity_source": "tmdb_search",
        }
        self.assertTrue(_snapshot_identity_compatible(item, good))


if __name__ == "__main__":
    unittest.main()
