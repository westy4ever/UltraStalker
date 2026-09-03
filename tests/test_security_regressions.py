# -*- coding: utf-8 -*-
from __future__ import absolute_import

import contextlib
import hashlib
import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest import mock

from .. import netsec, storage
from ..core import bouquets, backup


class URLValidationTests(unittest.TestCase):
    def test_rejects_non_http_schemes_and_parser_ambiguity(self):
        bad = [
            "file:///etc/passwd",
            "ftp://example.com/file",
            "https://example.com\\@127.0.0.1/",
            "https://example.com/\r\nX-Test: injected",
            " https://example.com/",
            "https://user:pass@example.com/",
            "https://example.com:99999/",
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(urllib.error.URLError):
                    netsec.validate_http_url(value)

    def test_credentials_require_https(self):
        with self.assertRaises(urllib.error.URLError):
            netsec.validate_http_url("http://example.com/api", require_https=True)
        self.assertEqual(
            netsec.validate_http_url("https://example.com/api", require_https=True),
            "https://example.com/api",
        )

    def test_provider_cross_origin_redirect_strips_sensitive_headers(self):
        req = urllib.request.Request("https://portal.example/list")
        for key in (
            "Authorization", "Cookie", "Proxy-Authorization", "Referer",
            "X-API-Key", "API-Key", "X-Auth-Token", "X-Access-Token",
        ):
            req.add_unredirected_header(key, "secret")
        redirected = netsec.ProviderRedirectHandler().redirect_request(
            req, None, 302, "Found", {}, "https://cdn.example/list"
        )
        lowered = {k.lower() for k in list(redirected.headers) + list(redirected.unredirected_hdrs)}
        for key in netsec.ProviderRedirectHandler.SENSITIVE:
            self.assertNotIn(key, lowered)

    def test_https_downgrade_redirect_is_blocked(self):
        req = urllib.request.Request("https://portal.example/list")
        with self.assertRaises(urllib.error.HTTPError):
            netsec.ProviderRedirectHandler().redirect_request(
                req, None, 302, "Found", {}, "http://portal.example/list"
            )


class SSRFRegressionTests(unittest.TestCase):
    @staticmethod
    def _answer(ip, port=80):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def test_public_destination_allowed(self):
        with mock.patch.object(socket, "getaddrinfo", return_value=self._answer("93.184.216.34")):
            self.assertEqual(netsec.validate_remote_media_url("https://media.example/a.jpg"), "https://media.example/a.jpg")

    def test_private_destination_needs_exact_origin(self):
        with mock.patch.object(socket, "getaddrinfo", return_value=self._answer("192.168.1.20", 8080)):
            self.assertEqual(
                netsec.validate_remote_media_url(
                    "http://portal.local:8080/a.jpg",
                    trusted_private_origins=("http://portal.local:8080",),
                ),
                "http://portal.local:8080/a.jpg",
            )
            for trusted in (
                ("http://portal.local:8000",),
                ("https://portal.local:8080",),
                (),
            ):
                with self.subTest(trusted=trusted):
                    with self.assertRaises(urllib.error.URLError):
                        netsec.validate_remote_media_url("http://portal.local:8080/a.jpg", trusted_private_origins=trusted)

    def test_loopback_and_link_local_always_blocked(self):
        for ip in ("127.0.0.1", "169.254.169.254"):
            with self.subTest(ip=ip):
                with mock.patch.object(socket, "getaddrinfo", return_value=self._answer(ip, 8080)):
                    with self.assertRaises(urllib.error.URLError):
                        netsec.validate_remote_media_url(
                            "http://portal.local:8080/a.jpg",
                            trusted_private_origins=("http://portal.local:8080",),
                        )

    def test_media_redirect_strips_all_sensitive_headers(self):
        req = urllib.request.Request("https://media.example/a.jpg")
        for key in (
            "Authorization", "Cookie", "Proxy-Authorization", "Referer",
            "X-API-Key", "API-Key", "X-Auth-Token", "X-Access-Token",
        ):
            req.add_unredirected_header(key, "secret")
        with mock.patch.object(socket, "getaddrinfo", return_value=self._answer("93.184.216.34", 443)):
            redirected = netsec.SafeMediaRedirectHandler().redirect_request(
                req, None, 302, "Found", {}, "https://cdn.example/a.jpg"
            )
        lowered = {k.lower() for k in list(redirected.headers) + list(redirected.unredirected_hdrs)}
        for key in netsec.SafeMediaRedirectHandler.SENSITIVE:
            self.assertNotIn(key, lowered)

    def test_media_urls_use_central_parser_checks(self):
        with self.assertRaises(urllib.error.URLError):
            netsec.validate_remote_media_url("https://example.com\\@127.0.0.1/a.jpg")
        with self.assertRaises(urllib.error.URLError):
            netsec.validate_remote_media_url("https://example.com/a.jpg\nX-Test: injected")


class ProxyRegressionTests(unittest.TestCase):
    def test_proxy_token_is_random_and_required(self):
        first = bouquets._new_proxy_token()
        second = bouquets._new_proxy_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 24)
        entry = {"proxy_token": first}
        self.assertTrue(bouquets._token_matches(entry, first))
        self.assertFalse(bouquets._token_matches(entry, second))
        self.assertFalse(bouquets._token_matches(entry, None))

    def test_tokenless_entries_are_rejected(self):
        self.assertFalse(bouquets._token_matches({}, None))
        self.assertFalse(bouquets._token_matches({}, "unexpected"))

    def test_legacy_export_is_atomically_migrated_to_token(self):
        with tempfile.TemporaryDirectory() as root:
            enigma = os.path.join(root, "enigma2")
            epg = os.path.join(root, "epgimport")
            cfg = os.path.join(root, "cfg")
            os.makedirs(enigma); os.makedirs(epg); os.makedirs(cfg)
            registry_path = os.path.join(cfg, "bouquet_registry.json")
            pkey, ckey, port = "profile123", "channel456", 17999
            entry = {
                "profile": {"portal": "http://portal.local", "mac": "00:11:22:33:44:55"},
                "service_type": 4097, "channels": {ckey: {"id": "1", "cmd": "ffmpeg http://x"}},
                "proxy_port": port,
            }
            registry = {"_meta": {"format": 2, "proxy_port": port}, pkey: entry}
            old_stream = bouquets._proxy_url(pkey, ckey, port, None)
            old_ref = bouquets._service_ref(old_stream, 4097)
            old_xmltv = bouquets._xmltv_url(pkey, port, None)
            paths = {
                "bouquet": os.path.join(enigma, "userbouquet.ultrastalker_%s.tv" % pkey),
                "epg_channels": os.path.join(epg, "ultrastalker_%s.channels.xml" % pkey),
                "epg_source": os.path.join(epg, "ultrastalker_%s.sources.xml" % pkey),
            }
            with open(paths["bouquet"], "w", encoding="utf-8") as h: h.write("#SERVICE %s:Name\n" % old_ref)
            with open(paths["epg_channels"], "w", encoding="utf-8") as h: h.write("<channel>%s</channel>\n" % old_ref)
            with open(paths["epg_source"], "w", encoding="utf-8") as h: h.write("<url>%s</url>\n" % old_xmltv)
            with mock.patch.object(bouquets, "CONFIG_DIR", cfg), \
                 mock.patch.object(bouquets, "REGISTRY_FILE", registry_path), \
                 mock.patch.object(bouquets, "ENIGMA2_DIR", enigma), \
                 mock.patch.object(bouquets, "EPGIMPORT_DIR", epg):
                bouquets._write_registry(registry)
                migrated = bouquets._migrate_legacy_proxy_tokens()
            token = migrated[pkey].get("proxy_token")
            self.assertTrue(token)
            self.assertEqual(migrated["_meta"]["format"], 3)
            new_ref = bouquets._service_ref(bouquets._proxy_url(pkey, ckey, port, token), 4097)
            for key in ("bouquet", "epg_channels"):
                with open(paths[key], "r", encoding="utf-8") as h: text = h.read()
                self.assertIn(new_ref, text); self.assertNotIn(old_ref, text)
            with open(paths["epg_source"], "r", encoding="utf-8") as h: source = h.read()
            self.assertIn(bouquets._xmltv_url(pkey, port, token), source)
            self.assertNotIn(old_xmltv + "</url>", source)

    def test_legacy_token_migration_rolls_back_on_partial_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            enigma = os.path.join(root, "enigma2"); epg = os.path.join(root, "epgimport"); cfg = os.path.join(root, "cfg")
            os.makedirs(enigma); os.makedirs(epg); os.makedirs(cfg)
            registry_path = os.path.join(cfg, "bouquet_registry.json")
            pkey, ckey, port = "profile123", "channel456", 17999
            registry = {pkey: {"profile": {}, "service_type": 4097, "channels": {ckey: {"cmd": "x"}}, "proxy_port": port}}
            old_ref = bouquets._service_ref(bouquets._proxy_url(pkey, ckey, port, None), 4097)
            bouquet_path = os.path.join(enigma, "userbouquet.ultrastalker_%s.tv" % pkey)
            channels_path = os.path.join(epg, "ultrastalker_%s.channels.xml" % pkey)
            source_path = os.path.join(epg, "ultrastalker_%s.sources.xml" % pkey)
            with open(bouquet_path, "w", encoding="utf-8") as h: h.write(old_ref)
            with open(channels_path, "w", encoding="utf-8") as h: h.write("malformed-without-expected-ref")
            with open(source_path, "w", encoding="utf-8") as h: h.write(bouquets._xmltv_url(pkey, port, None))
            original_bouquet = open(bouquet_path, "rb").read()
            with mock.patch.object(bouquets, "CONFIG_DIR", cfg), \
                 mock.patch.object(bouquets, "REGISTRY_FILE", registry_path), \
                 mock.patch.object(bouquets, "ENIGMA2_DIR", enigma), \
                 mock.patch.object(bouquets, "EPGIMPORT_DIR", epg):
                bouquets._write_registry(registry)
                with self.assertRaises(ValueError): bouquets._migrate_legacy_proxy_tokens()
                current = bouquets._read_registry()
            self.assertFalse(current[pkey].get("proxy_token"))
            self.assertEqual(open(bouquet_path, "rb").read(), original_bouquet)

    def test_non_loopback_bind_is_refused(self):
        with mock.patch.object(bouquets, "PROXY_HOST", "0.0.0.0"):
            with self.assertRaises(RuntimeError):
                bouquets._assert_loopback_bind()


class PersistedStateRegressionTests(unittest.TestCase):
    def test_corrupt_json_is_quarantined(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "settings.json")
            notice = os.path.join(root, "recovery.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"broken":')
            with mock.patch.object(storage, "CONFIG_DIR", root), mock.patch.object(storage, "RECOVERY_NOTICE_FILE", notice):
                result = storage.load_json_file(path, dict, {"safe": True})
            self.assertEqual(result, {"safe": True})
            self.assertFalse(os.path.exists(path))
            quarantined = [name for name in os.listdir(root) if name.startswith("settings.json.corrupt-")]
            self.assertEqual(len(quarantined), 1)


class BackupRegressionTests(unittest.TestCase):
    def _write_archive(self, path, members):
        hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in members.items()}
        manifest = {
            "product": "UltraStalker",
            "format": 7,
            "members": sorted(members),
            "sha256": hashes,
            "authenticated": False,
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
            archive.writestr(backup.MANIFEST_MEMBER, json.dumps(manifest))

    def test_backup_integrity_accepts_valid_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as root:
            good = os.path.join(root, "good.zip")
            self._write_archive(good, {"settings.json": b'{"theme":"nova_fhd"}'})
            manifest = backup.inspect_backup(good)
            self.assertEqual(manifest["format"], 7)

            bad = os.path.join(root, "bad.zip")
            hashes = {"settings.json": hashlib.sha256(b"original").hexdigest()}
            manifest = {
                "product": "UltraStalker", "format": 7,
                "members": ["settings.json"], "sha256": hashes,
                "authenticated": False,
            }
            with zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("settings.json", b"tampered")
                archive.writestr(backup.MANIFEST_MEMBER, json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                backup.inspect_backup(bad)

    def test_backup_rejects_path_traversal_member(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "traversal.zip")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../settings.json", b"{}")
                archive.writestr(backup.MANIFEST_MEMBER, json.dumps({
                    "product": "UltraStalker", "format": 7,
                    "members": ["../settings.json"],
                    "sha256": {"../settings.json": hashlib.sha256(b"{}").hexdigest()},
                    "authenticated": False,
                }))
            with self.assertRaisesRegex(ValueError, "unsupported path"):
                backup.inspect_backup(path)


    def test_restore_mid_commit_failure_rolls_back_and_reopens_database(self):
        with tempfile.TemporaryDirectory() as root:
            config_dir = os.path.join(root, "config")
            os.makedirs(config_dir)
            profiles = os.path.join(config_dir, "profiles.json")
            settings = os.path.join(config_dir, "settings.json")
            with open(profiles, "wb") as handle:
                handle.write(b'{"profile":"old"}')
            with open(settings, "wb") as handle:
                handle.write(b'{"theme":"old"}')

            archive_path = os.path.join(root, "restore.zip")
            self._write_archive(archive_path, {
                "profiles.json": b'{"profile":"new"}',
                "settings.json": b'{"theme":"new"}',
            })

            targets = {"profiles.json": profiles, "settings.json": settings}
            real_replace = os.replace
            restore_swaps = {profiles + ".restore": profiles, settings + ".restore": settings}
            seen_restore_swaps = []

            def fail_second_restore_swap(src, dst):
                if restore_swaps.get(src) == dst:
                    seen_restore_swaps.append((src, dst))
                    if len(seen_restore_swaps) == 2:
                        raise OSError("injected mid-commit failure")
                return real_replace(src, dst)

            resumed = mock.Mock()
            with mock.patch.object(backup, "CONFIG_DIR", config_dir), \
                    mock.patch.object(backup, "BACKUP_DIR", os.path.join(config_dir, "backups")), \
                    mock.patch.object(backup, "OPTIONAL_FILES", targets), \
                    mock.patch.object(backup, "SECRET_FILES", {}), \
                    mock.patch.object(backup, "_quiesce_background_runtime", return_value="session"), \
                    mock.patch.object(backup, "_resume_background_runtime", resumed), \
                    mock.patch.object(backup.DB, "write_guard", side_effect=lambda: contextlib.nullcontext()), \
                    mock.patch.object(backup.DB, "close") as close_db, \
                    mock.patch.object(backup.DB, "reset_after_restore") as reset_db, \
                    mock.patch.object(backup.os, "replace", side_effect=fail_second_restore_swap):
                with self.assertRaisesRegex(OSError, "injected mid-commit failure"):
                    backup.restore_backup(archive_path)

            with open(profiles, "rb") as handle:
                self.assertEqual(handle.read(), b'{"profile":"old"}')
            with open(settings, "rb") as handle:
                self.assertEqual(handle.read(), b'{"theme":"old"}')
            close_db.assert_called_once_with()
            reset_db.assert_called_once_with()
            resumed.assert_called_once_with("session")

    def test_staged_restore_rejects_corrupt_json(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "settings.json"), "w", encoding="utf-8") as handle:
                handle.write("{broken")
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                backup._validate_staged(root)


if __name__ == "__main__":
    unittest.main()
