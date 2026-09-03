# -*- coding: utf-8 -*-
import inspect
import unittest
from Plugins.Extensions.UltraStalker import client


class PortalHostnameTransportCompatTests(unittest.TestCase):
    def test_portal_runtime_uses_beta57_hostname_transport_with_guarded_fallback(self):
        src = inspect.getsource(client.StalkerClient._persistent_http_get)
        self.assertIn('http.client.HTTPConnection(parsed.hostname', src)
        self.assertIn('http.client.HTTPSConnection(parsed.hostname', src)
        self.assertNotIn('conn = _PinnedPortalHTTPConnection(', src)
        self.assertNotIn('conn = _PinnedPortalHTTPSConnection(', src)
        self.assertIn('validate_http_url(url)', src)
        self.assertIn('approved_downgrade=', src)
        self.assertIn('self.allow_http_fallback', src)

    def test_get_once_restores_beta57_approved_http_fallback(self):
        src = inspect.getsource(client.StalkerClient._get_once)
        self.assertIn('self.allow_http_fallback', src)
        self.assertIn('self.host.startswith("https://")', src)
        self.assertIn('self.used_http_fallback = True', src)


if __name__ == '__main__':
    unittest.main()
