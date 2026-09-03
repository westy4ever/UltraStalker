import unittest
from Plugins.Extensions.UltraStalker.title_clean import clean_title, catalogue_title, tmdb_search_title

class StrictCatalogueTitleTests(unittest.TestCase):
    def test_multi_segment_provider_prefix_is_removed_everywhere(self):
        raw='AR-AS-D - Agent Kim Reactivated (2026) (KR)'
        expected='Agent Kim Reactivated'
        self.assertEqual(clean_title(raw), expected)
        self.assertEqual(catalogue_title(raw), expected)
        self.assertEqual(tmdb_search_title(raw), expected)

    def test_real_hyphenated_title_survives(self):
        self.assertEqual(catalogue_title('Spider-Man: Homecoming (2017) (US)'), 'Spider-Man: Homecoming')

if __name__ == '__main__':
    unittest.main()
