# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import unittest

from ..core.portal_security import (
    HTTP_WARNING, http_warning_for, mark_http_consent, requires_http_consent,
)
from ..storage import _normalize_profile


class PortalHTTPConsentTests(unittest.TestCase):
    def test_http_requires_consent_but_https_does_not(self):
        self.assertTrue(requires_http_consent("http://portal.example"))
        self.assertFalse(requires_http_consent("https://portal.example"))

    def test_consent_marker_is_only_retained_for_http(self):
        http_profile = mark_http_consent({"portal": "http://portal.example"}, True)
        https_profile = mark_http_consent({"portal": "https://portal.example"}, True)
        self.assertTrue(http_profile["explicit_http_accepted"])
        self.assertFalse(https_profile["explicit_http_accepted"])

    def test_normalization_persists_one_time_http_consent_and_clears_https(self):
        base = {"mac": "00:1A:79:11:22:33", "source_type": "stalker"}
        http_profile = dict(base, portal="http://portal.example", explicit_http_accepted=True)
        https_profile = dict(base, portal="https://portal.example", explicit_http_accepted=True)
        self.assertTrue(_normalize_profile(http_profile)["explicit_http_accepted"])
        self.assertFalse(_normalize_profile(https_profile)["explicit_http_accepted"])

    def test_sensitive_http_warning_never_echoes_url_or_secret(self):
        secret = "DoNotLeak123"
        value = "http://portal.example/list?username=user&password=" + secret
        warning = http_warning_for(value)
        self.assertIn("HIGH-RISK HTTP warning", warning)
        self.assertNotIn(secret, warning)
        self.assertNotIn(value, warning)
        self.assertNotEqual(warning, HTTP_WARNING)

    def test_portal_open_has_legacy_one_time_consent_gate(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui_screens_portallist.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('not bool(profile.get("explicit_http_accepted", False))', source)
        self.assertIn("def _portal_open_http_confirmed", source)
        self.assertIn("profile = mark_http_consent(self.profiles[idx], True)", source)
        self.assertIn("save_profiles(self.profiles)", source)


if __name__ == "__main__":
    unittest.main()
