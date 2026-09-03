# -*- coding: utf-8 -*-
"""Small pure stages extracted from TMDB enrichment identity preparation."""
import re


def query_list(item, raw, query_fields, query_variants, normalizer, limit=4):
    queries=[]
    ordered=[item.get(k) or "" for k in query_fields] + [raw]
    for source in ordered:
        for q in query_variants(source):
            if q and normalizer(q) not in [normalizer(x) for x in queries]: queries.append(q)
    return queries[:int(limit)]


def external_imdb_id(item):
    for key in ("imdb_id","imdb","external_imdb_id"):
        value=str((item or {}).get(key) or "").strip()
        match=re.search(r"tt\d{5,12}",value,re.I)
        if match:return match.group(0).lower()
    return ""


def direct_tmdb_identity(item, locked_tmdb_id=None):
    direct=locked_tmdb_id
    if direct:return direct,False
    for key in ("tmdb_id","tmdbid","tmdb"):
        value=str((item or {}).get(key) or "").strip()
        match=re.search(r"(?<!\d)(\d{2,10})(?!\d)",value)
        if match:
            try:return int(match.group(1)),True
            except Exception:pass
    return None,False
