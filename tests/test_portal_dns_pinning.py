# -*- coding: utf-8 -*-
import socket
import unittest
from unittest import mock

from .. import client


class _FakeSocket(object):
    def __init__(self, *args):
        self.args = args
        self.connected = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def bind(self, _address):
        pass

    def connect(self, address):
        self.connected.append(address)

    def close(self):
        self.closed = True


class PortalDNSPinningTests(unittest.TestCase):
    def test_private_lan_portal_is_allowed_and_deduplicated(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 8080)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 8080)),
        ]
        with mock.patch.object(socket, "getaddrinfo", return_value=answers) as resolver:
            endpoints = client._resolve_portal_endpoints("portal.local", 8080)
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0][3], ("192.168.1.20", 8080))
        resolver.assert_called_once_with("portal.local", 8080, 0, socket.SOCK_STREAM)

    def test_multicast_and_unspecified_destinations_are_blocked(self):
        for address in ("0.0.0.0", "224.0.0.1", "::"):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, 80, 0, 0) if family == socket.AF_INET6 else (address, 80)
            answers = [(family, socket.SOCK_STREAM, 6, "", sockaddr)]
            with self.subTest(address=address), mock.patch.object(socket, "getaddrinfo", return_value=answers):
                with self.assertRaises(client.PortalError):
                    client._resolve_portal_endpoints("portal.local", 80)

    def test_http_connection_uses_only_pre_resolved_endpoint(self):
        endpoint = (socket.AF_INET, socket.SOCK_STREAM, 6, ("203.0.113.50", 8080))
        fake = _FakeSocket()
        with mock.patch.object(socket, "socket", return_value=fake), \
                mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS must not run in connect")):
            conn = client._PinnedPortalHTTPConnection(
                "portal.example", 8080, timeout=7, portal_endpoints=(endpoint,)
            )
            conn.connect()
        self.assertIs(conn.sock, fake)
        self.assertEqual(fake.connected, [("203.0.113.50", 8080)])
        self.assertEqual(fake.timeout, 7)

    def test_https_wrap_preserves_original_hostname_for_sni(self):
        endpoint = (socket.AF_INET, socket.SOCK_STREAM, 6, ("203.0.113.51", 443))
        fake = _FakeSocket()
        context = mock.Mock()
        wrapped = object()
        context.wrap_socket.return_value = wrapped
        with mock.patch.object(socket, "socket", return_value=fake), \
                mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS must not run in connect")):
            conn = client._PinnedPortalHTTPSConnection(
                "portal.example", 443, timeout=5, context=context,
                portal_endpoints=(endpoint,),
            )
            conn.connect()
        context.wrap_socket.assert_called_once_with(fake, server_hostname="portal.example")
        self.assertIs(conn.sock, wrapped)


if __name__ == "__main__":
    unittest.main()
