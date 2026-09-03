# -*- coding: utf-8 -*-
"""Pure/runtime-light helpers used by the UltraStalker player.

Kept separate from player.py so formatting and engine-selection logic can be
reviewed and tested without touching the Enigma2 Screen lifecycle.
"""
from __future__ import absolute_import

import os
import re

try:
    from urllib.parse import unquote, urlparse
except ImportError:
    from urllib import unquote
    from urlparse import urlparse

from ..log import optional_failure

ENGINE_NAMES = {
    1: "Native",
    4097: "GStreamer",
    5001: "GstPlayer",
    5002: "ExtePlayer3",
    8193: "ServiceApp",
}


def engine_label(value):
    try:
        value = int(value)
    except Exception:
        return str(value)
    return "%s %s" % (ENGINE_NAMES.get(value, "Engine"), value)


def extension(url):
    try:
        path = unquote(urlparse(str(url)).path)
        ext = os.path.splitext(path)[-1].lower()
        if ext in (".ts", ".m3u8", ".mp4", ".mkv", ".avi", ".mpd", ".mov", ".webm"):
            return ext.lstrip(".").upper()
    except Exception as exc:
        optional_failure("player", exc)
    return "STREAM"


def quality(item, name):
    item = item if isinstance(item, dict) else {}
    fields = (
        "quality", "video_quality", "resolution", "video_resolution", "height",
        "width", "format", "stream_quality", "name", "title", "cmd", "command",
        "url", "stream_url", "filename",
    )
    text = " ".join(str(item.get(k) or "") for k in fields) + " " + str(name or "")
    up = text.upper()
    if re.search(r"\b(?:4K|UHD|2160P?|3840(?:X2160)?)\b", up):
        return "UHD 2160p"
    if re.search(r"\b(?:FULL[ ._-]?HD|FHD|1080P?|1920(?:X1080)?)\b", up):
        return "FHD 1080p"
    if re.search(r"\b(?:HD|720P?|1280(?:X720)?)\b", up):
        return "HD 720p"
    if re.search(r"\b(?:SD|576P?|480P?|720X576|720X480)\b", up):
        return "SD"
    return "AUTO"


def meta_text(item, media_type):
    item = item or {}
    values = []
    year = item.get("year") or item.get("release_date") or item.get("created")
    if year:
        values.append(str(year)[:10])
    genre = item.get("genre") or item.get("genres_str") or item.get("category_name")
    if genre:
        values.append(str(genre).replace(";", " / ")[:38])
    duration = item.get("time") or item.get("duration") or item.get("length")
    if duration:
        values.append(str(duration)[:14])
    rating = item.get("rating") or item.get("age") or item.get("age_rating")
    if rating:
        values.append("Rating %s" % str(rating)[:8])
    if not values:
        values.append({
            "itv": "Live TV", "live": "Live TV", "vod": "Movie", "series": "Series",
            "episode": "Episode", "catchup": "Catch-up",
        }.get(media_type, "Ultra Stalker stream"))
    return "   •   ".join(values)[:92]


def next_meta(item, media_type):
    item = item or {}
    value = item.get("next_time") or item.get("next_start") or item.get("end_time") or item.get("time_to")
    if value:
        return str(value)[:34]
    if media_type in ("vod", "series", "episode", "catchup"):
        return "Resume • Audio • Subtitles"
    return "EPG information when available"


def available_engines(preferred, url):
    try:
        preferred = int(preferred)
    except Exception:
        preferred = 4097

    available = [1, 4097]
    if os.path.exists("/usr/bin/gstplayer"):
        available.append(5001)
    if os.path.exists("/usr/bin/exteplayer3"):
        available.append(5002)
    serviceapp = any(os.path.exists(path) for path in (
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/Extensions/ServiceApp",
        "/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp/serviceapp.so",
    ))
    if serviceapp:
        available.append(8193)

    low = str(url or "").lower()
    if ".m3u8" in low or "hls" in low:
        if preferred == 1:
            preferred = 4097
        order = [4097, 5001, 5002, 8193, 1]
    elif low.startswith(("rtsp://", "rtmp://")):
        order = [4097, 5002, 5001, 8193, 1]
    else:
        order = [4097, 5002, 5001, 8193, 1]

    result = []
    for value in [preferred] + order:
        if value in available and value not in result:
            result.append(value)
    return result or [4097]
