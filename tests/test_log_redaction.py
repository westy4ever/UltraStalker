# -*- coding: utf-8 -*-
"""Regression tests for log secret redaction."""
import logging
import unittest

from ..log import _RedactingFormatter, redact


class LogRedactionTests(unittest.TestCase):
    def assertSecretsAbsent(self, output, *secrets):
        lowered = output.lower()
        for secret in secrets:
            self.assertNotIn(str(secret).lower(), lowered)

    def test_xtream_path_and_query_credentials_are_hidden(self):
        raw = "GET http://iptv.example:8080/live/alice/hunter2/123.ts?token=tok123"
        out = redact(raw)
        self.assertIn("http://iptv.example:8080/[REDACTED]", out)
        self.assertSecretsAbsent(out, "alice", "hunter2", "tok123", "/live/")

    def test_authorization_and_proxy_authorization_are_hidden(self):
        for raw, secret in (
            ("Authorization: Bearer eyJhbGci.secret.sig", "eyJhbGci.secret.sig"),
            ("Proxy-Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ):
            out = redact(raw)
            self.assertSecretsAbsent(out, secret)
            self.assertIn("[REDACTED]", out)

    def test_cookie_and_set_cookie_headers_are_redacted_wholesale(self):
        raw = "Cookie: PHPSESSID=session-secret; stb_lang=en; mac=00:11:22:33:44:55"
        out = redact(raw)
        self.assertEqual(out, "Cookie: [REDACTED]")
        self.assertSecretsAbsent(out, "session-secret", "00:11:22:33:44:55")

        raw2 = "Set-Cookie: sid=another-secret; Path=/; HttpOnly"
        out2 = redact(raw2)
        self.assertEqual(out2, "Set-Cookie: [REDACTED]")
        self.assertSecretsAbsent(out2, "another-secret")

    def test_plain_username_password_and_api_key_are_hidden(self):
        out = redact("username=alice password=hunter2 api_key=abcd1234")
        self.assertSecretsAbsent(out, "alice", "hunter2", "abcd1234")
        self.assertEqual(out, "username=[REDACTED] password=[REDACTED] api_key=[REDACTED]")

    def test_encoded_url_is_hidden(self):
        raw = "next=http%3A%2F%2Fserver%2Flive%2Falice%2Fhunter2%2F1.ts"
        out = redact(raw)
        self.assertEqual(out, "next=[ENCODED URL REDACTED]")
        self.assertSecretsAbsent(out, "alice", "hunter2")

    def test_formatter_redacts_after_logging_interpolation(self):
        formatter = _RedactingFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            "UltraStalker", logging.ERROR, __file__, 1,
            "request failed url=%s Cookie: %s username=%s",
            ("https://user:pass@example.com/live/1", "sid=session-secret", "alice"),
            None,
        )
        out = formatter.format(record)
        self.assertSecretsAbsent(out, "user", "pass", "session-secret", "alice")
        self.assertIn("[REDACTED]", out)

    def test_non_secret_text_is_preserved(self):
        raw = "Player startup failed after 3 retries on service 4097"
        self.assertEqual(redact(raw), raw)


if __name__ == "__main__":
    unittest.main()
