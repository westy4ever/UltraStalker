import os
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__))

def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as fh:
        return fh.read()

class PersistentFinalVisualsTests(unittest.TestCase):
    def test_content_manifest_keeps_existing_final_files_immutable(self):
        src=read('ui.py')
        self.assertIn('Component-level hard lock',src)
        self.assertIn('if existing and os.path.isfile(existing):',src)
        self.assertIn('continue',src)
        self.assertIn('details_adaptive_ready',src)

    def test_portal_and_m3u_prepare_page_are_persistent_first(self):
        src=read('ui_grid_base.py')
        block=src[src.index('def _prepare_grid_page'):src.index('def _cache_prepared_page')]
        self.assertIn('Persistent final visuals win for BOTH Portal and M3U',block)
        self.assertLess(block.index('_load_visual_bundle'),block.index('elif provider_value and not use_local_first'))
        self.assertIn('art_row["visual_locked"]=True',block)

    def test_details_reopen_freezes_visual_pipeline(self):
        src=read('ui_screens_details.py')
        self.assertIn('self._persistent_visual_frozen=bool(',src)
        block=src[src.index('def _schedule_poster_visuals'):src.index('def _load_cinematic_backdrop')]
        self.assertIn('if getattr(self,"_persistent_adaptive_locked",False):',block)
        self.assertIn('return',block)

    def test_back_from_details_rebinds_exact_persisted_visual(self):
        src=read('ui_grid_base.py')
        block=src[src.index('def _details_returned'):src.index('def _play_live')]
        self.assertIn('Never "upgrade" a card on BACK',block)
        self.assertIn('bundle=_load_visual_bundle',block)
        self.assertIn('display=str(bundle.get("grid_thumb") or bundle.get("poster") or "")',block)

if __name__ == '__main__':
    unittest.main()
