import pathlib, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]

class Beta72HomeDetailRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home=(ROOT/'ui_screens_home.py').read_text(encoding='utf-8')
        cls.ui=(ROOT/'ui.py').read_text(encoding='utf-8')
        cls.details=(ROOT/'ui_screens_details.py').read_text(encoding='utf-8')

    def test_home_has_hdd_only_first_paint(self):
        self.assertIn('def _home_cached_art(', self.ui)
        self.assertIn('cached=_home_cached_art(item,self.profile,(190,272),ph,mtype)', self.home)
        self.assertIn('if cached and cached!=placeholder_path:', self.home)
        fast=self.ui.split('def _home_cached_art(',1)[1].split('def _home_prepare_art(',1)[0]
        self.assertNotIn('ArtworkV2(', fast)
        self.assertNotIn('_download_portal_artwork(', fast)

    def test_details_ram_state_wins_on_return(self):
        restore=self.details.split('def _restore_details_visual_state(self):',1)[1].split('def _details_shown_resume',1)[0]
        self.assertIn('if key not in state or state.get(key) in (None,"",{},[]):', restore)
        self.assertIn('state["backdrop_present"]=frozen', restore)
        self.assertIn('if key not in state or state.get(key) in (None,"",{},[]):', restore)

    def test_details_freezes_and_invalidates_backdrop_work(self):
        hidden=self.details.split('def _details_hidden_release(self):',1)[1].split('def _restore_details_visual_state',1)[0]
        self.assertIn('self._return_backdrop_path=current', hidden)
        self.assertIn('self._backdrop_token += 1', hidden)
        self.assertIn('self._backdrop_jobs.get_nowait()', hidden)

if __name__=='__main__': unittest.main()
