# -*- coding: utf-8 -*-
"""Profile-scoped category visibility helpers.

Only provider category *indexes* stay visible to background refresh so new folders
can be discovered. Hidden category content is never requested by category-aware
loaders until the user restores that category.
"""
import hashlib
import json

_MEDIA_TYPES = ("itv", "vod", "series")


def category_id(item):
    row = item if isinstance(item, dict) else {}
    return str(row.get("id") or row.get("genre_id") or row.get("category_id") or row.get("name") or row.get("title") or "").strip()


def profile_visibility_key(profile):
    p = profile if isinstance(profile, dict) else {}
    identity = {
        "portal": str(p.get("portal") or p.get("url") or p.get("host") or "").rstrip("/").lower(),
        "mac": str(p.get("mac") or "").upper(),
        "username": str(p.get("username") or p.get("user") or ""),
        "name": str(p.get("name") or p.get("title") or ""),
        "type": str(p.get("type") or p.get("kind") or ""),
    }
    raw = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:32]


def _clean_ids(values):
    out = []
    seen = set()
    for value in values if isinstance(values, (list, tuple, set)) else []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def hidden_ids(settings, profile, media_type):
    """Return hidden IDs for one profile/media pair.

    Old global hidden_categories remain a read-only compatibility fallback until
    that profile/media pair is first edited. An explicitly stored empty scoped
    list therefore means "show all" and correctly overrides the legacy value.
    """
    cfg = settings if isinstance(settings, dict) else {}
    media = str(media_type or "").lower()
    if media not in _MEDIA_TYPES:
        return set()
    scoped = cfg.get("hidden_categories_by_profile")
    if isinstance(scoped, dict):
        bucket = scoped.get(profile_visibility_key(profile))
        if isinstance(bucket, dict) and media in bucket:
            return set(_clean_ids(bucket.get(media)))
    legacy = cfg.get("hidden_categories")
    if isinstance(legacy, dict):
        return set(_clean_ids(legacy.get(media)))
    return set()


def hidden_update(settings, profile, media_type, values):
    """Build a settings payload that updates only this profile/media pair."""
    cfg = settings if isinstance(settings, dict) else {}
    media = str(media_type or "").lower()
    if media not in _MEDIA_TYPES:
        return {"hidden_categories_by_profile": dict(cfg.get("hidden_categories_by_profile") or {})}
    current = cfg.get("hidden_categories_by_profile")
    scoped = {}
    if isinstance(current, dict):
        for key, value in current.items():
            if isinstance(value, dict):
                scoped[str(key)] = {m: _clean_ids(value.get(m)) for m in _MEDIA_TYPES if m in value}
    pkey = profile_visibility_key(profile)
    bucket = dict(scoped.get(pkey) or {})
    bucket[media] = _clean_ids(values)
    scoped[pkey] = bucket
    return {"hidden_categories_by_profile": scoped}


def visible_categories(rows, settings, profile, media_type):
    hidden = hidden_ids(settings, profile, media_type)
    return [row for row in (rows or []) if isinstance(row, dict) and category_id(row) not in hidden]
