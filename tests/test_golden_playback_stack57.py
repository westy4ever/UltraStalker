# -*- coding: utf-8 -*-
import ast
import os
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__))

class GoldenPlaybackStack57Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT,"client.py"),"r",encoding="utf-8") as h:cls.client=h.read()
        with open(os.path.join(ROOT,"services","player_golden57.py"),"r",encoding="utf-8") as h:cls.player=h.read()
        with open(os.path.join(ROOT,"services","player.py"),"r",encoding="utf-8") as h:cls.boundary=h.read()

    def test_portal_playback_uses_same_live_client_directly(self):
        self.assertNotIn("playback_bridge57",self.client)
        self.assertNotIn("Golden57StalkerClient",self.client)
        self.assertIn("if not self.token:",self.client)
        self.assertIn("self.authorize(cancel_event=cancel_event)",self.client)
        self.assertIn('"action": "create_link"',self.client)
        self.assertIn("self._get(params, retry_auth=False, cancel_event=cancel_event)",self.client)
        self.assertFalse(os.path.exists(os.path.join(ROOT,"playback_bridge57.py")))
        self.assertFalse(os.path.exists(os.path.join(ROOT,"client_playback57.py")))

    def test_player_uses_live_portal_client_and_beta57_subdl_runtime(self):
        self.assertIn("from ..client import StalkerClient",self.player)
        self.assertIn("from .subtitles_online_golden57 import cached_subtitle, search_arabic, download_candidate, parse_srt",self.player)

    def test_public_player_boundary_exports_runtime_shutdown(self):
        self.assertIn("shutdown_player_workers",self.boundary)
        self.assertIn("__all__ = (\"UltraStalkerPlayer\", \"force_session_silence\", \"shutdown_player_workers\")",self.boundary)

    def test_golden_subtitle_runtime_is_packaged(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT,"services","subtitles_online_golden57.py")))

if __name__ == '__main__':
    unittest.main()
