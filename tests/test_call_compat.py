# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from Plugins.Extensions.UltraStalker.core.call_compat import call_compatible


class CallCompatTests(unittest.TestCase):
    def test_internal_typeerror_is_not_retried(self):
        calls = []

        def loader(page, cancel_event=None):
            calls.append((page, cancel_event))
            raise TypeError("bug inside loader")

        with self.assertRaisesRegex(TypeError, "bug inside loader"):
            call_compatible(loader, (((3,), {}), ((3, None), {})))
        self.assertEqual(calls, [(3, None)])

    def test_legacy_required_second_argument_selected_before_call(self):
        calls = []

        def loader(page, cancel_event):
            calls.append((page, cancel_event))
            return [page]

        result = call_compatible(loader, (((7,), {}), ((7, None), {})))
        self.assertEqual(result, [7])
        self.assertEqual(calls, [(7, None)])

    def test_new_force_keyword_is_preferred(self):
        calls = []

        def enrich(item, media_type, cancel_event=None, force=False):
            calls.append(force)
            return dict(item, forced=force)

        result = call_compatible(
            enrich,
            ((({"name": "row"}, "vod", None), {"force": True}),
             (({"name": "row"}, "vod", None), {})),
        )
        self.assertTrue(result["forced"])
        self.assertEqual(calls, [True])

    def test_legacy_enrich_without_force_is_selected(self):
        calls = []

        def enrich(item, media_type, cancel_event=None):
            calls.append(media_type)
            return item

        item = {"name": "x"}
        result = call_compatible(
            enrich,
            (((item, "series", None), {"force": True}),
             ((item, "series", None), {})),
        )
        self.assertIs(result, item)
        self.assertEqual(calls, ["series"])

    def test_keyword_and_positional_legacy_shapes_do_not_double_call(self):
        calls = []

        def ordered_all(media_type, genre, start_page, max_pages, max_items, cancel_event):
            calls.append((media_type, genre, start_page, max_pages, max_items, cancel_event))
            return ["ok"]

        result = call_compatible(
            ordered_all,
            ((("itv", "news"), {"start_page": 1, "max_pages": 500, "max_items": 20000, "cancel_event": None}),
             (("itv", "news", 1, 500, 20000, None), {})),
        )
        self.assertEqual(result, ["ok"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
