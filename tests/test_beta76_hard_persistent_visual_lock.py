import os, unittest

ROOT=os.path.dirname(os.path.dirname(__file__))

def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as fh:return fh.read()

class HardPersistentVisualLockTests(unittest.TestCase):
    def test_manifest_has_component_locks_and_no_repeat_write(self):
        src=read('ui.py')
        block=src[src.index('def _write_content_manifest'):src.index('def _promote_content_art')]
        self.assertIn('poster_final',block)
        self.assertIn('backdrop_final',block)
        self.assertIn('grid_final',block)
        self.assertIn('adaptive_final',block)
        self.assertIn('if not dirty and old:',block)
        self.assertIn('return True',block)

    def test_details_keeps_source_and_final_backdrop_separate(self):
        src=read('ui_screens_details.py')
        self.assertIn('backdrop_present',src)
        drain=src[src.index("while True:\n            try:payload=self._backdrop_jobs.get_nowait()"):src.index("while True:\n            try: token,data=self._tmdb_jobs.get_nowait()")]
        self.assertIn('self._details_visual_state["backdrop_present"]=path',drain)
        self.assertIn('self._details_visual_state["backdrop"]=str(canonical_source)',drain)

    def test_details_network_artwork_is_dead_after_hdd_lock(self):
        src=read('ui_screens_details.py')
        provider=src[src.index('def _load_provider_art'):src.index('def _layout_ready')]
        self.assertIn('_persistent_poster_locked',provider)
        self.assertIn('_persistent_backdrop_source_locked',provider)
        tmdb=src[src.index('def _load_tmdb_metadata'):src.index('def _backdrop_presentation_target')]
        self.assertIn('Hard HDD visual lock',tmdb)
        self.assertIn('_persistent_backdrop_source_locked',tmdb)

    def test_progressive_grid_never_redownloads_locked_poster(self):
        src=read('ui_grid_base.py')
        block=src[src.index('def _progressive_poster_item'):src.index('def _start_progressive_poster_prefetch')]
        self.assertIn('HARD HDD LOCK',block)
        self.assertLess(block.index('_load_visual_bundle'),block.index('_download_portal_artwork'))

    def test_final_detail_poster_is_persistent_derivative(self):
        src=read('ui_screens_details.py')
        layout=src[src.index('def _layout_ready'):src.index('def _title_fit_tick')]
        self.assertIn('detail_poster',layout)
        self.assertIn('_detail_cover_artwork',layout)
        self.assertIn('setPixmapFromFile',layout)

if __name__=='__main__':unittest.main()
