# -*- coding: utf-8 -*-
"""Lazy TMDb compatibility facade for Ultra Stalker.

The full TMDb implementation pulls in image/cache/media-library helpers and is
expensive on weak receivers.  Most screens only need the historical TMDBClient
name injected at configure time, not the implementation itself.  Keep that API
stable while deferring the real module until the first actual TMDb operation.
"""
from __future__ import absolute_import

_IMPL = None


class TMDBError(Exception):
    """Public TMDb error type kept stable across the lazy boundary."""
    pass


def _impl():
    global _IMPL
    if _IMPL is None:
        from . import tmdb_impl as real
        _IMPL = real
    return _IMPL


class TMDBClient(object):
    """Construction proxy returning the real TMDBClient instance on demand."""
    def __new__(cls, *args, **kwargs):
        return _impl().TMDBClient(*args, **kwargs)

    @staticmethod
    def image_url(path, size="original"):
        return _impl().TMDBClient.image_url(path, size)


def shutdown_tmdb_workers(wait=False):
    return _impl().shutdown_tmdb_workers(wait=wait)


def _query_variants(value):
    return _impl()._query_variants(value)


def _score(query, result, media_type, wanted_year=None, requested_type=None, country_hint="", description_hint=""):
    return _impl()._score(query, result, media_type, wanted_year, requested_type, country_hint, description_hint)


def _year(value):
    return _impl()._year(value)


def _country_hint(item, raw=""):
    return _impl()._country_hint(item, raw)


def _latin_heavy(value):
    return _impl()._latin_heavy(value)


def _norm(value):
    return _impl()._norm(value)


# Fail-soft compatibility for any old internal caller that reaches a less
# common module attribute.  It still remains lazy until that attribute is used.
def __getattr__(name):
    # Python's import machinery probes module metadata such as ``__path__``
    # during ``from .tmdb import ...``.  If every missing dunder is forwarded
    # to the heavy implementation, that harmless probe defeats the lazy facade
    # and imports tmdb_impl during plugin startup.  Keep metadata probes cheap;
    # only real public compatibility attributes should activate the engine.
    if str(name).startswith("__"):
        raise AttributeError(name)
    return getattr(_impl(), name)


__all__ = [
    "TMDBClient", "TMDBError", "shutdown_tmdb_workers",
    "_query_variants", "_score", "_year", "_country_hint",
    "_latin_heavy", "_norm",
]
