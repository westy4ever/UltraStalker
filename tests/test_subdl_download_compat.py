# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from Plugins.Extensions.UltraStalker.services import subtitles_online as sub


class SubDLDownloadCompatTests(unittest.TestCase):
    def test_relative_download_url_is_bound_to_official_host(self):
        self.assertEqual(
            sub._normalize_subdl_download_url("/subtitle/123/file456"),
            "https://dl.subdl.com/subtitle/123/file456",
        )

    def test_non_subdl_host_is_rejected(self):
        with self.assertRaises(RuntimeError):
            sub._normalize_subdl_download_url("https://example.com/subtitle/file.srt")

    def test_http_is_rejected(self):
        with self.assertRaises(RuntimeError):
            sub._normalize_subdl_download_url("http://dl.subdl.com/subtitle/file.srt")

    def test_selected_candidate_falls_back_after_404(self):
        original=sub.download_candidate
        calls=[]
        try:
            def fake(candidate, ident):
                calls.append(candidate["name"])
                if candidate["name"]=="first":
                    raise RuntimeError("HTTP Error 404: Not Found")
                return {"path":"/tmp/ok.srt","release":candidate["name"]}
            sub.download_candidate=fake
            rows=[
                {"name":"first","url":"/subtitle/1"},
                {"name":"second","url":"/subtitle/2"},
            ]
            result=sub.download_candidates_with_fallback(rows,{},preferred_index=0,max_attempts=8)
            self.assertEqual(calls,["first","second"])
            self.assertEqual(result["release"],"second")
            self.assertTrue(result["fallback_used"])
            self.assertEqual(result["resolved_index"],1)
        finally:
            sub.download_candidate=original

    def test_all_404s_return_friendly_error(self):
        original=sub.download_candidate
        try:
            def fake(candidate, ident):
                raise RuntimeError("HTTP Error 404: Not Found")
            sub.download_candidate=fake
            with self.assertRaisesRegex(RuntimeError,"No working Arabic subtitle"):
                sub.download_candidates_with_fallback(
                    [{"name":"a"},{"name":"b"}],{},preferred_index=0,max_attempts=8
                )
        finally:
            sub.download_candidate=original


if __name__=="__main__":
    unittest.main()
