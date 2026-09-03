# -*- coding: utf-8 -*-
"""UltraStalker automated regression-test runner.

Usage on a receiver/install tree:
    python3 /usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/run_tests.py

The runner derives the Enigma2 Python root so package-relative imports in the
suite work consistently whether it is launched from the plugin directory or
by absolute path.
"""
from __future__ import print_function

import os
import sys
import unittest


def _paths():
    plugin_dir = os.path.abspath(os.path.dirname(__file__))
    # .../python/Plugins/Extensions/UltraStalker -> .../python
    python_root = os.path.dirname(os.path.dirname(os.path.dirname(plugin_dir)))
    return plugin_dir, python_root


def main():
    plugin_dir, python_root = _paths()
    if python_root not in sys.path:
        sys.path.insert(0, python_root)

    tests_dir = os.path.join(plugin_dir, "tests")
    suite = unittest.defaultTestLoader.discover(
        start_dir=tests_dir,
        pattern="test_*.py",
        top_level_dir=python_root,
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
