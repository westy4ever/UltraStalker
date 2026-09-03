# -*- coding: utf-8 -*-
from __future__ import absolute_import

import io
import os
import tempfile
import unittest
import zipfile

from ..services import subtitles_online as sub


class SubtitleArchiveHardeningTests(unittest.TestCase):
    def _zip(self, members):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in members:
                zf.writestr(name, payload)
        return buf.getvalue()

    def test_safe_members_drive_selection_not_raw_name_list(self):
        data = self._zip([
            ("bad/episode.srt", b"x" * 200),
            ("good/movie.srt", b"1\n00:00:00,000 --> 00:00:01,000\nhello\n"),
        ])
        original_ratio = sub.MAX_COMPRESSION_RATIO
        # Force the highly-compressible first member to be rejected by safety checks.
        sub.MAX_COMPRESSION_RATIO = 2
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                infos = sub._safe_archive_members(zf)
                names = [info.filename for info in infos]
                self.assertIn("good/movie.srt", names)
                self.assertNotIn("bad/episode.srt", names)
                chosen = sub._choose_archive_member(infos, {"episode": 0})
                self.assertEqual(chosen.filename, "good/movie.srt")
        finally:
            sub.MAX_COMPRESSION_RATIO = original_ratio

    def test_duplicate_archive_names_are_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("same.srt", b"one")
            zf.writestr("same.srt", b"two")
        with zipfile.ZipFile(io.BytesIO(buf.getvalue()), "r") as zf:
            with self.assertRaises(RuntimeError):
                sub._safe_archive_members(zf)

    def test_directory_entries_and_unsupported_files_are_ignored(self):
        data = self._zip([
            ("folder/", b""),
            ("folder/readme.txt", b"ignore"),
            ("folder/subtitle.vtt", b"WEBVTT\n"),
        ])
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            infos = sub._safe_archive_members(zf)
            self.assertEqual([i.filename for i in infos], ["folder/subtitle.vtt"])

    def test_total_uncompressed_limit_applies_to_entire_archive(self):
        data = self._zip([("a.txt", b"a" * 80), ("b.srt", b"b" * 80)])
        original = sub.MAX_ARCHIVE_TOTAL_BYTES
        sub.MAX_ARCHIVE_TOTAL_BYTES = 100
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                with self.assertRaises(RuntimeError):
                    sub._safe_archive_members(zf)
        finally:
            sub.MAX_ARCHIVE_TOTAL_BYTES = original

    def test_episode_scoring_prefers_matching_safe_member(self):
        data = self._zip([
            ("show.S01E02.ass", b"x"),
            ("show.S01E03.srt", b"y"),
            ("generic.srt", b"z"),
        ])
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            infos = sub._safe_archive_members(zf)
            chosen = sub._choose_archive_member(infos, {"episode": 2})
            self.assertEqual(chosen.filename, "show.S01E02.ass")


if __name__ == "__main__":
    unittest.main()
