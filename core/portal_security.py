# -*- coding: utf-8 -*-
"""Shared portal transport policy helpers used by UI entry points."""
from __future__ import absolute_import

import urllib.parse

HTTP_WARNING = (
    "Security warning: this source uses plain HTTP.\n\n"
    "Your MAC address, login details, authentication tokens and viewing requests "
    "may be visible or modified by devices on the network. HTTPS is strongly "
    "recommended.\n\nUse this HTTP source anyway?"
)

_SENSITIVE_QUERY_KEYS = frozenset((
    "user", "username", "pass", "password", "token", "auth", "apikey", "api_key",
    "key", "mac", "access_token", "refresh_token",
))


def _http_url_contains_credentials(value):
    """Return True when an HTTP URL itself appears to carry credentials/secrets."""
    raw = str(value or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
    except Exception:
        return False
    if str(parts.scheme or "").lower() != "http":
        return False
    if parts.username is not None or parts.password is not None:
        return True
    try:
        for key, _unused in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
            if str(key or "").strip().lower() in _SENSITIVE_QUERY_KEYS:
                return True
    except Exception:
        return False
    return False


def http_warning_for(value):
    """Build the HTTP consent message without echoing the supplied URL or secrets."""
    if _http_url_contains_credentials(value):
        return (
            "HIGH-RISK HTTP warning: this source contains login or authentication "
            "data in an unencrypted HTTP URL.\n\nThe credentials/tokens and your "
            "viewing requests may be read or modified by devices on the network. "
            "Use HTTPS whenever the provider supports it.\n\nUse this HTTP source anyway?"
        )
    return HTTP_WARNING


def normalized_scheme(value):
    raw = str(value or "").strip()
    try:
        return str(urllib.parse.urlsplit(raw).scheme or "").lower()
    except Exception:
        return ""


def requires_http_consent(value):
    return normalized_scheme(value) == "http"


def mark_http_consent(profile, accepted=True):
    result = dict(profile or {})
    result["explicit_http_accepted"] = bool(accepted and requires_http_consent(result.get("portal")))
    return result
