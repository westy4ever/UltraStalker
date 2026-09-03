# -*- coding: utf-8 -*-
import unittest
from unittest import mock

from .. import client
from ..core.session import PortalSession


class HTTPSDowngradePolicyTests(unittest.TestCase):
    def _client(self, portal="https://portal.example/c/", **kwargs):
        with mock.patch.object(client.DB, "state_get", return_value={}):
            return client.StalkerClient(portal, "00:1A:79:00:00:01", **kwargs)

    def test_legacy_approval_flags_cannot_enable_http_fallback(self):
        c = self._client(
            allow_http_fallback=True,
            http_fallback_accepted=True,
            allow_tls_fallback=True,
            tls_mode="compatible",
        )
        self.assertFalse(c.allow_http_fallback)
        self.assertFalse(c.http_fallback_accepted)
        self.assertFalse(c.allow_tls_fallback)
        self.assertEqual(c.tls_mode, "strict")
        self.assertFalse(c.used_http_fallback)

    def test_persisted_http_endpoint_is_ignored_for_https_portal(self):
        state = {"endpoint": "http://portal.example/server/load.php"}
        with mock.patch.object(client.DB, "state_get", return_value=state):
            c = client.StalkerClient(
                "https://portal.example/c/", "00:1A:79:00:00:01",
                allow_http_fallback=True, http_fallback_accepted=True,
            )
        self.assertTrue(c.host.startswith("https://"))
        self.assertIsNone(c.endpoint)
        self.assertFalse(c.used_http_fallback)

    def test_https_request_failure_never_recurses_to_http(self):
        c = self._client(allow_http_fallback=True, http_fallback_accepted=True)
        c._candidate_endpoints = lambda: ["https://portal.example/server/load.php"]
        with mock.patch.object(c, "_request_endpoint", side_effect=client.PortalError("TLS failed")) as request:
            with self.assertRaises(client.PortalError):
                c._get_once({"type": "stb", "action": "handshake"})
        request.assert_called_once()
        self.assertTrue(c.host.startswith("https://"))
        self.assertFalse(c.used_http_fallback)

    def test_explicit_http_portal_remains_explicit_configuration(self):
        c = self._client(portal="http://portal.example/c/")
        self.assertTrue(c.host.startswith("http://"))
        self.assertFalse(c.used_http_fallback)
        self.assertEqual(c.security_warning, "")

    def test_session_identity_ignores_removed_legacy_transport_flags(self):
        base = {
            "portal": "https://portal.example/c/",
            "mac": "00:1A:79:00:00:01",
            "source_type": "stalker",
            "tls_mode": "strict",
            "device_profile": "auto",
        }
        legacy = dict(base, allow_http_fallback=True, http_fallback_accepted=True, tls_fallback_accepted=True)
        clean = dict(base, allow_http_fallback=False, http_fallback_accepted=False, tls_fallback_accepted=False)
        self.assertEqual(PortalSession._key(legacy), PortalSession._key(clean))


if __name__ == "__main__":
    unittest.main()
