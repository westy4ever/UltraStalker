# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import tempfile
import unittest
from unittest import mock

from .. import downloads


class DownloadPathSecurityTests(unittest.TestCase):
    def test_validator_accepts_only_download_root(self):
        with tempfile.TemporaryDirectory() as root:
            managed = os.path.join(root, "Downloads")
            os.makedirs(managed)
            with mock.patch.object(downloads, "ROOT", managed):
                good = os.path.join(managed, "Movies", "movie.ts")
                self.assertEqual(downloads._validated_download_path(good), os.path.abspath(good))
                with self.assertRaises(ValueError):
                    downloads._validated_download_path(os.path.join(root, "escaped.ts"))
                with self.assertRaises(ValueError):
                    downloads._validated_download_path(os.path.join(managed, "..", "escaped.ts"))

    def test_validator_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as root:
            managed = os.path.join(root, "Downloads")
            outside = os.path.join(root, "outside")
            os.makedirs(managed); os.makedirs(outside)
            link = os.path.join(managed, "Movies")
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with mock.patch.object(downloads, "ROOT", managed):
                with self.assertRaises(ValueError):
                    downloads._validated_download_path(os.path.join(link, "movie.ts"))

    def test_tampered_state_path_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            managed = os.path.join(root, "Downloads")
            os.makedirs(managed)
            state = os.path.join(managed, "downloads.json")
            rows = [
                {"id": "good", "path": os.path.join(managed, "Movies", "good.ts"), "status": "paused"},
                {"id": "evil", "path": os.path.join(root, "outside.ts"), "status": "paused"},
            ]
            with open(state, "w", encoding="utf-8") as handle:
                json.dump(rows, handle)
            with mock.patch.object(downloads, "ROOT", managed), \
                 mock.patch.object(downloads, "STATE_FILE", state), \
                 mock.patch.object(downloads, "hdd_read_ready", return_value=True):
                manager = downloads.DownloadManager()
                self.assertIn("good", manager.jobs)
                self.assertNotIn("evil", manager.jobs)
                manager.shutdown()

    def test_add_rejects_external_path_before_persisting(self):
        with tempfile.TemporaryDirectory() as root:
            managed = os.path.join(root, "Downloads")
            os.makedirs(managed)
            state = os.path.join(managed, "downloads.json")
            with mock.patch.object(downloads, "ROOT", managed), \
                 mock.patch.object(downloads, "STATE_FILE", state), \
                 mock.patch.object(downloads, "hdd_read_ready", return_value=True):
                manager = downloads.DownloadManager()
                ok, message = manager.add(
                    {"id": "evil", "path": os.path.join(root, "outside.ts")},
                    lambda: "https://example.com/video.ts",
                )
                self.assertFalse(ok)
                self.assertEqual(message, "Invalid download path")
                self.assertNotIn("evil", manager.jobs)
                manager.shutdown()

    def test_runtime_path_mutation_is_rejected_before_network_open(self):
        with tempfile.TemporaryDirectory() as root:
            managed = os.path.join(root, "Downloads")
            os.makedirs(managed)
            outside = os.path.join(root, "outside.ts")
            row = {"id": "job", "path": outside, "portal_origin": ""}
            with mock.patch.object(downloads, "ROOT", managed), \
                 mock.patch.object(downloads, "validate_remote_media_url", return_value="https://example.com/video.ts"), \
                 mock.patch.object(downloads, "build_safe_media_opener") as opener_factory:
                manager = object.__new__(downloads.DownloadManager)
                manager._resolvers = {"job": lambda: "https://example.com/video.ts"}
                with self.assertRaisesRegex(RuntimeError, "Unsafe download path"):
                    manager._download("job", row)
                opener_factory.assert_called_once()
                opener_factory.return_value.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
