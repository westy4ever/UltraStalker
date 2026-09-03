# -*- coding: utf-8 -*-
"""Stable content identity shared by resume/history, quality and HDD caches.

Portal stream commands and translated display titles can change between calls.
Use server/content identifiers first and a deterministic series/episode tuple for
episodes so every subsystem talks about the same logical title.
"""
from __future__ import absolute_import
import hashlib
import json


def _text(value):
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _episode_identity(item, legacy=False):
    item = item if isinstance(item, dict) else {}
    if legacy in ("us110", "title_tuple"):
        season = item.get("season") or item.get("season_id") or item.get("season_number")
        episode = item.get("episode") or item.get("number") or item.get("episode_num") or item.get("episode_number")
        title = item.get("episode_name") or item.get("name") or item.get("title")
        series_ref = item.get("series_id") or item.get("series") or item.get("movie_id") or item.get("series_uid") or item.get("parent_id")
        episode_id = item.get("episode_id")
        if any(v not in (None, "") for v in (season, episode, title, series_ref, episode_id)):
            return {"series": series_ref, "season": season, "episode": episode, "episode_id": episode_id, "title": title}
        return item.get("id") or item.get("cmd") or item.get("command") or item.get("url") or title or "unknown"
    if legacy:
        return {
            "episode_id": item.get("episode_id") or item.get("id"),
            "season": item.get("season") or item.get("season_id"),
            "episode": item.get("episode") or item.get("number") or item.get("episode_num"),
            "cmd": item.get("cmd") or item.get("command") or item.get("url"),
            "title": item.get("episode_name") or item.get("name") or item.get("title"),
        }
    season = item.get("season") or item.get("season_id") or item.get("season_number")
    episode = item.get("episode") or item.get("number") or item.get("episode_num") or item.get("episode_number")
    title = item.get("episode_name") or item.get("name") or item.get("title")
    series_ref = item.get("series_id") or item.get("_series_id") or item.get("series") or item.get("movie_id") or item.get("series_uid") or item.get("parent_id")
    episode_id = item.get("episode_id")
    generic_id = item.get("id")
    # Strong identity never depends on a translated/renamed episode title. An
    # explicit episode_id is authoritative. A generic ``id`` is deliberately
    # weaker because some Stalker portals repeat the parent/season id on every
    # episode; in that case the series/season/episode tuple is safer.
    if episode_id not in (None, ""):
        return {"episode_id": episode_id, "series": series_ref, "season": season, "episode": episode}
    if series_ref not in (None, "") and (season not in (None, "") or episode not in (None, "")):
        return {"series": series_ref, "season": season, "episode": episode}
    if generic_id not in (None, ""):
        return {"episode_id": generic_id, "season": season, "episode": episode}
    if season not in (None, "") and episode not in (None, ""):
        return {"series_title": item.get("_series_title") or "", "season": season, "episode": episode}
    # Only weak portals without structural IDs fall back to mutable text/URL.
    return item.get("cmd") or item.get("command") or item.get("url") or title or "unknown"


def stable_content_identity(media_type, item, legacy_episode=False):
    item = item if isinstance(item, dict) else {}
    mtype = _text(media_type).lower()
    if mtype == "episode":
        return _episode_identity(item, legacy=legacy_episode)

    ordered = {
        "itv": ("ch_id", "id", "channel_id", "cmd", "command", "url", "name", "title"),
        "live": ("ch_id", "id", "channel_id", "cmd", "command", "url", "name", "title"),
        "vod": ("movie_id", "id", "video_id", "tmdb_id", "imdb_id", "cmd", "command", "url", "name", "title"),
        "series": ("series_id", "id", "series_uid", "tmdb_id", "imdb_id", "cmd", "command", "url", "name", "title"),
        "catchup": ("id", "ch_id", "event_id", "start_timestamp", "cmd", "command", "url", "name", "title"),
    }.get(mtype, ("id", "movie_id", "series_id", "episode_id", "ch_id", "cmd", "command", "url", "name", "title"))
    for key in ordered:
        value = item.get(key)
        if value not in (None, ""):
            return "%s:%s" % (key, _text(value)[:420])

    # Deterministic weak fallback. Year keeps same-name remakes apart where the
    # portal exposes no usable id at all.
    title = _text(item.get("original_name") or item.get("original_title") or item.get("name") or item.get("title") or "unknown")
    year = _text(item.get("year") or item.get("release_year") or item.get("first_air_date"))[:10]
    return {"title": title[:280], "year": year}


def content_digest(profile, media_type, item, algorithm="sha256", legacy_episode=False):
    profile = profile if isinstance(profile, dict) else {}
    portal = _text(profile.get("portal")).rstrip("/").lower()
    mac = _text(profile.get("mac")).upper()
    identity = stable_content_identity(media_type, item, legacy_episode=legacy_episode)
    raw = json.dumps([portal, mac, _text(media_type).lower(), identity], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data = raw.encode("utf-8", "ignore")
    if str(algorithm).lower() == "sha1":
        return hashlib.sha1(data).hexdigest()
    return hashlib.sha256(data).hexdigest()
