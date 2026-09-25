"""TMDb age-certification extraction shared by Details and catalogue resolvers.

Policy is intentionally simple and display-oriented:
- Arabic originals prefer Egypt (EG).
- Everything else prefers the US (US).
- If the preferred country is unavailable, fall back to the other common
  country and finally to any non-empty TMDb certification rather than showing
  a blank label.

TMDb's certification string is always kept raw in metadata/cache.  UI code uses
``display_certification`` to present one simple age-oriented label for users who
do not need to know the source country's rating vocabulary.
"""

_ARAB_REGIONS = set((
    "EG","SA","AE","KW","QA","BH","OM","JO","LB","SY","IQ","PS","YE",
    "MA","DZ","TN","LY","SD","MR","SO","DJ","KM",
))


def normalize_media_type(media_type):
    raw=str(media_type or "").strip().lower()
    if raw in ("tv","series","show"):return "tv"
    return "movie"


def _country_codes(details):
    payload=details if isinstance(details,dict) else {}
    out=[]
    for key in ("origin_country","production_countries"):
        values=payload.get(key) or []
        if not isinstance(values,(list,tuple,set)):values=[values]
        for value in values:
            if isinstance(value,dict):value=value.get("iso_3166_1") or value.get("code") or ""
            code=str(value or "").strip().upper()
            if code and code not in out:out.append(code)
    return out


def is_arabic_work(details):
    payload=details if isinstance(details,dict) else {}
    language=str(payload.get("original_language") or "").strip().lower().replace("_","-")
    if language=="ar" or language.startswith("ar-"):return True
    # Some provider/TMDb rows can miss original_language while retaining origin.
    # Treat an Arab-country origin as Arabic for certification-country priority.
    return any(code in _ARAB_REGIONS for code in _country_codes(payload))


def _rating_rows(payload,media_type):
    """Return ``[(region, value), ...]`` from appended/direct/proxy TMDb payloads."""
    src=payload if isinstance(payload,dict) else {}
    mt=normalize_media_type(media_type)
    rows=[]
    if mt=="tv":
        block=src.get("content_ratings") if isinstance(src.get("content_ratings"),dict) else src
        results=block.get("results") if isinstance(block,dict) else []
        # A few receiver-side caches preserve the result list under data/results.
        if not results and isinstance(block,dict) and isinstance(block.get("data"),dict):
            results=(block.get("data") or {}).get("results") or []
        for entry in (results or []):
            if not isinstance(entry,dict):continue
            value=str(entry.get("rating") or "").strip()
            if not value:continue
            rows.append((str(entry.get("iso_3166_1") or "").strip().upper(),value))
        return rows

    block=src.get("release_dates") if isinstance(src.get("release_dates"),dict) else src
    results=block.get("results") if isinstance(block,dict) else []
    if not results and isinstance(block,dict) and isinstance(block.get("data"),dict):
        results=(block.get("data") or {}).get("results") or []
    for entry in (results or []):
        if not isinstance(entry,dict):continue
        region=str(entry.get("iso_3166_1") or "").strip().upper()
        candidates=[]
        for release in (entry.get("release_dates") or []):
            if not isinstance(release,dict):continue
            value=str(release.get("certification") or "").strip()
            if not value:continue
            try:release_type=int(release.get("type") or 99)
            except Exception:release_type=99
            # Prefer theatrical/digital certificates, then any usable release.
            priority=0 if release_type==3 else (1 if release_type==4 else 2)
            candidates.append((priority,release_type,value))
        if candidates:
            candidates.sort(key=lambda value:(value[0],value[1]))
            rows.append((region,candidates[0][2]))
    return rows


def certification_from_tmdb(details,media_type,supplemental=None):
    """Pick one *displayable* raw certification by country priority.

    Text-only/non-age tokens such as NR/Unrated must never terminate the age
    lane.  If the preferred region has no age-bearing value, continue through
    the fallback regions and finally any TMDb region until a value can be
    rendered as Ultra's +N badge.  The chosen raw TMDb value is still stored
    unchanged; only displayability controls selection here.
    """
    rows=_rating_rows(details,media_type)
    if supplemental:
        for row in _rating_rows(supplemental,media_type):
            if row not in rows:rows.append(row)
    if not rows:return ""
    preferred=("EG","US") if is_arabic_work(details) else ("US","GB")
    for region in preferred:
        for row_region,value in rows:
            if row_region==region and value and display_certification(value):return value
    # Regional perfection is secondary to a usable age badge.  Skip NR and
    # other vocabulary-only labels and take the first real age-bearing value.
    for _region,value in rows:
        if value and display_certification(value):return value
    return ""


def rating_endpoint(media_type,tmdb_id):
    mt=normalize_media_type(media_type)
    suffix="content_ratings" if mt=="tv" else "release_dates"
    return "/%s/%s/%s"%(mt,int(tmdb_id),suffix)

# Display-only simplification. Stored metadata/cache remains untouched.
# The user-facing contract is strict: when a badge is shown it is always +N.
# Source labels such as NR/B/12A/MA15+ stay raw in metadata but are never leaked
# into the card UI.
_DISPLAY_CERTIFICATION_MAP = {
    # US / TV
    "G": "+0", "PG": "+8", "PG-13": "+13", "R": "+17", "NC-17": "+18",
    "TV-Y": "+0", "TV-Y7": "+7", "TV-G": "+0", "TV-PG": "+8",
    "TV-14": "+14", "TV-MA": "+17",
    # UK / common international
    "U": "+0", "12A": "+12", "R18": "+18",
    # Mexico / Latin-America style labels that TMDb can expose as fallbacks
    "AA": "+0", "A": "+0", "B": "+12", "B15": "+15", "C": "+18", "D": "+18",
    # Australia / Brazil / Korea style text-only gates
    "M": "+15", "MA15+": "+15", "R18+": "+18", "X18+": "+18",
    "L": "+0", "ALL": "+0",
}

_NO_AGE_LABELS = set((
    "NR", "N/R", "UNRATED", "NOT-RATED", "NOT RATED", "NOTRATED",
    "TBD", "NONE", "N/A", "NA", "UNKNOWN", "-", "--",
))


def display_certification(value):
    """Return only Ultra's compact ``+N`` age label, or blank.

    The raw certification is never mutated. Numeric-bearing international labels
    (12A, B15, R18+, FSK16...) are reduced to their age number. Known text-only
    schemes are mapped explicitly. Unknown/non-age labels are hidden instead of
    leaking provider vocabulary such as ``NR`` into the UI.
    """
    import re
    text=str(value or "").strip()
    if not text:
        return ""
    key=text.upper().replace("_","-").strip()
    key=re.sub(r"\s+","-",key)
    if key in _NO_AGE_LABELS or key.replace("-"," ") in _NO_AGE_LABELS:
        return ""

    compact=key.replace("-","")
    aliases={
        "PG13":"PG-13","NC17":"NC-17","TVY":"TV-Y","TVY7":"TV-Y7",
        "TVG":"TV-G","TVPG":"TV-PG","TV14":"TV-14","TVMA":"TV-MA",
        "MA15":"MA15+","R18":"R18+","X18":"X18+",
    }
    canonical=aliases.get(compact,key)
    mapped=_DISPLAY_CERTIFICATION_MAP.get(canonical)
    if mapped:
        return mapped

    # Bare numbers and numeric-bearing rating vocabularies are safe to simplify.
    # Keep a sane upper bound so years/IDs can never become an age badge.
    m=re.match(r"^\+?(\d{1,2})\+?$",key)
    if not m:
        m=re.match(r"^[A-Z]{0,8}[- ]?(\d{1,2})(?:[A-Z]{0,3}|\+)?$",key)
    if m:
        try:
            age=int(m.group(1))
        except Exception:
            age=-1
        if 0 <= age <= 21:
            return "+%d"%age
    return ""
