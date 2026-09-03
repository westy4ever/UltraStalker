# -*- coding: utf-8 -*-
"""Regression guard for silent broad exception handlers in critical UI/player paths."""
import ast
import logging
import os
import unittest
from unittest import mock

from .. import log as logmod


class FailSoftDiagnosticsTests(unittest.TestCase):
    def test_critical_modules_do_not_silently_pass_broad_exceptions(self):
        base=os.path.dirname(os.path.dirname(__file__))
        for rel in (
            "services/player.py",
            "ui_grid_base.py",
            "ui_screens_details.py",
            "ui.py",
            "artwork_v2.py",
            "tmdb.py",
            "m3u_adapter.py",
            "ui_screens_browser.py",
            "ui_screens_home.py",
            "ui_screens_portallist.py",
        ):
            with open(os.path.join(base,rel),"r",encoding="utf-8") as fh:
                tree=ast.parse(fh.read(),filename=rel)
            silent=[]
            for node in ast.walk(tree):
                if not isinstance(node,ast.ExceptHandler) or node.type is None:
                    continue
                is_exception=isinstance(node.type,ast.Name) and node.type.id=="Exception"
                if is_exception and len(node.body)==1 and isinstance(node.body[0],ast.Pass):
                    silent.append(node.lineno)
            self.assertEqual(silent,[],"silent broad exceptions in %s at %r"%(rel,silent))

    def test_diagnostic_failure_is_quiet_at_info_level(self):
        logger=logging.getLogger("UltraStalker.test.failsoft.info")
        logger.setLevel(logging.INFO)
        with mock.patch.object(logmod,"get_logger",return_value=logger), mock.patch.object(logger,"debug") as debug:
            logmod.diagnostic_failure("player.cleanup",RuntimeError("boom"))
            debug.assert_not_called()

    def test_diagnostic_failure_emits_traceback_at_debug_level(self):
        logger=logging.getLogger("UltraStalker.test.failsoft.debug")
        logger.setLevel(logging.DEBUG)
        with mock.patch.object(logmod,"get_logger",return_value=logger), mock.patch.object(logger,"debug") as debug:
            exc=RuntimeError("boom")
            logmod.diagnostic_failure("player.cleanup",exc)
            debug.assert_called_once()
            self.assertTrue(debug.call_args.kwargs.get("exc_info"))


if __name__ == "__main__":
    unittest.main()
