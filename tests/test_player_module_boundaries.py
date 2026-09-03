# -*- coding: utf-8 -*-
"""Structural regression tests for the player visual/lifecycle boundary."""
from __future__ import absolute_import

import ast
import os
import unittest


class PlayerModuleBoundaryTests(unittest.TestCase):
    def _tree(self, filename):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "services", filename)
        with open(path, "r", encoding="utf-8") as handle:
            return ast.parse(handle.read(), filename=filename)

    def test_visual_helpers_are_not_defined_in_player(self):
        tree = self._tree("player.py")
        names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        moved = {
            "_fallback_player_frames", "_adaptive_player_frames", "_progress_neon_frame",
            "_schedule_progress_neon_frame", "_adaptive_info_frames", "_cached_poster",
            "_prepare_player_poster_fill", "_prepare_live_picon",
        }
        self.assertFalse(names.intersection(moved))

    def test_visual_module_has_no_screen_or_player_classes(self):
        tree = self._tree("player_visuals.py")
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual([], classes)

    def test_player_module_stays_below_refactor_ceiling(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "services", "player.py")
        with open(path, "r", encoding="utf-8") as handle:
            line_count = sum(1 for _ in handle)
        self.assertLess(line_count, 3500)


if __name__ == "__main__":
    unittest.main()
