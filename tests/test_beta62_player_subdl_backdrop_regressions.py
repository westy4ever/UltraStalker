# -*- coding: utf-8 -*-
from __future__ import absolute_import
import os
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__))

class Beta63RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT,"services","player_golden57.py"),"r",encoding="utf-8") as fh:cls.player=fh.read()
        with open(os.path.join(ROOT,"ui_screens_details.py"),"r",encoding="utf-8") as fh:cls.details=fh.read()

    def test_subdl_uses_working_beta57_direct_lifecycle(self):
        online_branch=self.player.split('elif action=="online_ar":',1)[1].split('elif action=="style_color":',1)[0]
        self.assertIn('self._start_online_arabic_subtitles()',online_branch)
        self.assertNotIn('_deferred_subtitle_action',self.player)
        self.assertNotIn('subtitle_action_timer',self.player)

    def test_selected_subdl_candidate_is_downloaded_directly(self):
        choice_block=self.player.split('def _online_subtitle_choice_selected',1)[1].split('def _enforce_default_subtitles_off',1)[0]
        self.assertIn('download_candidate(candidate,ident)',choice_block)
        self.assertNotIn('download_candidates_with_fallback',choice_block)

    def test_details_restore_keeps_live_backdrop_authoritative(self):
        restore=self.details.split('def _restore_details_visual_state(self):',1)[1].split('def _details_shown_resume(self):',1)[0]
        self.assertIn('if key not in state or state.get(key) in (None,"",{},[]):',restore)
        self.assertIn('state["backdrop_present"]=frozen',restore)
        self.assertNotIn('_backdrop_presentation_target(path)',restore)

    def test_player_return_only_restores_existing_visual_bundle(self):
        close_block=self.details.split('def closed(result=None):',1)[1].split('engine=_configured_playback_engine()',1)[0]
        self.assertIn('self._restore_details_visual_state()',close_block)
        self.assertNotIn('self._image_layout_ready()',close_block)
        after_restore=close_block.split('try:self._restore_details_visual_state()',1)[1]
        self.assertNotIn('self._refresh_detail_visuals()',after_restore)

if __name__=="__main__":unittest.main()
