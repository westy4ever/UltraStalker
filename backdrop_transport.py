# -*- coding: utf-8 -*-
"""Ultra Stalker TMDb ORIGINAL byte transport.

This module deliberately owns only the request/response layer. The screen owns
cache placement and Pixmap binding, so the successful Deferred path stays:
request -> response -> readBody -> one screen callback.
"""
from __future__ import absolute_import

from twisted.internet import reactor
from twisted.web.client import Agent, readBody
from twisted.web.http_headers import Headers

try:
    from twisted.web.client import BrowserLikePolicyForHTTPS
    _TLS_CONTEXT = BrowserLikePolicyForHTTPS()
except Exception:
    try:
        from twisted.web.client import WebClientContextFactory
        _TLS_CONTEXT = WebClientContextFactory()
    except Exception:
        _TLS_CONTEXT = None

_ORIGINAL_PREFIXES = (
    "https://image.tmdb.org/t/p/original/",
    "http://image.tmdb.org/t/p/original/",
)
_USER_AGENT = b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def _trusted_original(url):
    value = str(url or "").strip()
    return value if value.startswith(_ORIGINAL_PREFIXES) else ""


class FastBackdropTransport(object):
    """One reusable Twisted Agent for a screen lifetime."""
    def __init__(self):
        self._agent = Agent(reactor, contextFactory=_TLS_CONTEXT) if _TLS_CONTEXT is not None else Agent(reactor)

    def fetch(self, url, is_current=None, on_body=None, on_status=None):
        value = _trusted_original(url)
        if not value:
            raise ValueError("untrusted backdrop URL")
        request = self._agent.request(
            b"GET",
            value.encode("utf-8"),
            Headers({"User-Agent": [_USER_AGENT]}),
        )

        def response_ready(response):
            if callable(is_current):
                try:
                    if not is_current():return None
                except Exception:return None
            code = int(getattr(response, "code", 0) or 0)
            if code == 200:
                body = readBody(response)
                if callable(on_body):body.addCallback(on_body)
                return body
            if callable(on_status):
                try:on_status(code)
                except Exception:pass
            return None

        request.addCallback(response_ready)
        return request
