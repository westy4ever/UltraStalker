# -*- coding: utf-8 -*-
"""Shared Ultra Stalker catalogue title cleaner used by Stalker/TMDB and M3U/Xtream."""
import re
import unicodedata

_TECH = {
    "4k","8k","uhd","fhd","hd","2160p","1080p","720p","3840p","4320p",
    "hdr","hdr10","dolby","atmos","hevc","h265","h264","avc","top","vip",
    "subs","sub","dub","dubs","raw","bluray","bdrip","brrip","hdrip","webrip","webdl","x264","x265","multi","audio","vod","movie","movies","series","tv",
    "ar","en","tr","s","do","fr","de","es","it","pl","nl","ru","pt","br","us","uk","eg","ksa","uae","der","ger","deu","lat","ara","eng","tur","kor","jpn","ind","arabic","english","turkish"," مترجم","مدبلج"
}

def _normalize_unicode_digits(value):
    text=str(value or "")
    out=[]
    for ch in text:
        try:
            if unicodedata.category(ch)=="Nd":
                out.append(str(unicodedata.digit(ch)))
            else:
                out.append(ch)
        except Exception:
            out.append(ch)
    return "".join(out)

def clean_title(value):
    text=_normalize_unicode_digits(value).strip()
    m=re.match(r"^([A-Z0-9]{2,6}(?:[-_][A-Z0-9]{1,6})*)\s+-\s+(.+)$",text)
    if m and ("-" in m.group(1) or "_" in m.group(1) or re.search(r"[\u0600-\u06ff]",m.group(2))):
        text=m.group(2).strip()
    text=re.sub(r"\[(?:[^\]]*?(?:4K|8K|UHD|FHD|HD|2160P|1080P|720P|HDR|DOLBY|SUB|DUB)[^\]]*?)\]"," ",text,flags=re.I)
    text=re.sub(r"\((?:US|USA|UK|GB|EG|EGY|KSA|SA|UAE|AE|KR|KOR|TR|TUR|JP|JPN|CN|CHN|IN|IND|AR|EN|SUBS?|DUB|DUBBED|مترجم|مدبلج)\)"," ",text,flags=re.I)
    text=re.sub(r"^\s*[\[(]\s*(?:قريب(?:ا[\u064b-\u0652]?|اً)?|حصري(?:ا[\u064b-\u0652]?|اً)?|جديد|قادم|يعرض\s+الآن|soon|coming\s+soon|new)\s*[\])]+\s*"," ",text,flags=re.I)
    text=re.sub(r"^\s*(?:قريب(?:ا[\u064b-\u0652]?|اً)?|حصري(?:ا[\u064b-\u0652]?|اً)?|جديد|قادم|يعرض\s+الآن)\s*[-:|]+\s*"," ",text,flags=re.I)
    text=re.sub(r"[\[(]\s*(?:قريب(?:ا[\u064b-\u0652]?|اً)?|حصري(?:ا[\u064b-\u0652]?|اً)?|جديد|قادم|يعرض\s+الآن|soon|coming\s+soon|new)\s*[\])]"," ",text,flags=re.I)
    text=re.sub(r"\((?:19|20)\d{2}\)"," ",text)
    parts=re.split(r"\s[-|:]\s",text)
    while len(parts)>1:
        first=re.sub(r"[^\w+]+"," ",parts[0],flags=re.UNICODE).strip().casefold()
        toks=[t for t in first.split() if t]
        known=sum(1 for t in toks if t in _TECH or re.match(r"^(?:4k|8k|\d{3,4}p)$",t))
        if len(toks)>=2 and known>=max(2,len(toks)-1):
            parts.pop(0)
        else:
            break
    text=" - ".join(parts)
    text=re.sub(r"\b(?:4K|8K|UHD|FHD|HD|2160P|1080P|720P|3840P|4320P|HDR10?|DOLBY|ATMOS|HEVC|X264|X265|BLU[ ._-]?RAY|BDRIP|BRRIP|HDRIP|WEB[ ._-]?(?:DL|RIP)|MULTI[ ._-]?AUDIO|H\.?(?:264|265)|AR[-_ ]?SUBS?|SUBS?|DUBBED|DUB|RAW)\b"," ",text,flags=re.I)
    text=re.sub(r"^(?:#+\s*)?(?:AR|EN|BE|UK|US|EG|KSA|TOP|VIP|MOVIES?|SERIES|TV)(?:[-_ ](?:SUBS?|DUBS?|RAW|4K|8K|UHD|HD|TOP))*\s*[:|\-]+\s*","",text,flags=re.I)
    # Strip residual provider/routing codes (DER, GER, AR-AS-D, etc.) only when they are technical uppercase chunks next to a separator.
    text=re.sub(r"^(?:[A-Z0-9]{1,6}(?:[-_/][A-Z0-9]{1,6}){0,4})\s*(?:[|┃:/-]+)\s*(?=.+)","",text)
    text=re.sub(r"\s*(?:[|┃/]+)\s*(?:DER|GER|DEU|ENG|ARA|LAT|SUBS?|DUBS?|RAW|4K|8K|UHD|FHD|HD)(?:\s*(?:[|┃/]+)\s*(?:DER|GER|DEU|ENG|ARA|LAT|SUBS?|DUBS?|RAW|4K|8K|UHD|FHD|HD))*\s*$","",text,flags=re.I)
    # Broken/incomplete country suffixes are common in Stalker names, e.g. "Dark (DE".
    # They are catalogue routing metadata, not part of the visible title.
    text=re.sub(r"\s*[\[(]\s*(?:[A-Z]{2,3}|USA|UAE|KSA)\s*[\])]?$", " ", text, flags=re.I)
    # Also remove trailing standalone provider/routing tokens after a slash/pipe.
    text=re.sub(r"\s*(?:[|┃/]+)\s*[A-Z0-9]{2,6}(?:[-_][A-Z0-9]{1,6}){0,4}\s*$", " ", text)
    text=re.sub(r"\s+"," ",text).strip(" -_|:.()[]")
    return text or str(value or "").strip()


def tmdb_search_title(value):
    """Aggressive TMDB search alias only; never use for visible UI text."""
    # Start from the same strict catalogue cleaner used by the visible UI.
    # This keeps multi-segment provider prefixes such as AR-AS-D from leaking
    # a trailing token (e.g. "D - Title") into TMDB search.
    text=clean_title(value)
    if not text:
        return ""
    text=re.sub(r"^(.+?)[,\s]+the$",lambda m:"The "+m.group(1).strip(),text,flags=re.I)
    text=re.sub(r"^\s*[A-Z0-9]{1,6}\s*:\s*","",text,flags=re.I)
    # Remove leading catalogue/provider segments separated by |/┃.
    text=re.sub(r"^(?:\s*[^|┃]{0,40}[|┃])+\s*","",text)
    text=re.sub(r"\([^)]*\)"," ",text)
    text=re.sub(r"\[[^\]]*\]"," ",text)
    text=re.sub(r"^\s*[A-Za-z]{1,3}(?:-[A-Za-z]{1,3})?\s*-\s*","",text)
    text=re.sub(r"\b(?:4K|8K|UHD|FHD|HD|2160P|1080P|720P|HDR10?|DOLBY|ATMOS|HEVC|H\.?26[45]|X26[45]|BLU[ ._-]?RAY|WEB[ ._-]?(?:DL|RIP)|SUBS?|DUBBED|DUB|RAW|VIP|TOP|MOVIES?|SERIES|TV)\b"," ",text,flags=re.I)
    text=re.sub(r"\b(?:مترجم|مدبلج|حصري|جديد|قريبا|قريباً|قريبًا)\b"," ",text,flags=re.I)
    text=re.sub(r"[._'*]+"," ",text)
    text=re.sub(r"\s+"," ",text).strip(" -_|:.")
    return text or clean_title(value)


def tmdb_search_aliases(value):
    """Return Xstreamity-style search aliases, preserving the full cleaned title.

    For mixed-script catalogue names, Xstreamity drops non-ASCII text during
    search normalization. We keep BOTH forms so matching is broader without
    changing the title shown to the user.
    """
    full=tmdb_search_title(value)
    out=[]
    has_ascii=bool(re.search(r"[A-Za-z]",full))
    has_non_ascii=bool(re.search(r"[^\x00-\x7f]",full))
    if has_ascii and has_non_ascii:
        latin=unicodedata.normalize("NFKD",full).encode("ascii","ignore").decode("ascii")
        latin=re.sub(r"\s+"," ",latin).strip(" -_|:.")
        if latin:
            out.append(latin)
    if full and full not in out:
        out.append(full)
    return out



def catalogue_title(value):
    """Visible/search title with catalogue branding and year decoration removed.

    'Pure' is treated as branding only when another meaningful title remains.
    An item literally named 'Pure' is preserved.
    """
    text=clean_title(value)
    text=re.sub(r"[\[(]\s*(?:19|20)\d{2}\s*[\])]", " ", text)
    text=re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    # Remove standalone PURE only if other meaningful content survives.
    without=re.sub(r"(?iu)(?<!\w)pure(?!\w)", " ", text)
    without=re.sub(r"\s+"," ",without).strip(" -_|:.()[]")
    meaningful=re.sub(r"[\W_]+","",without,flags=re.UNICODE)
    if len(meaningful)>=2:
        text=without
    text=re.sub(r"\s+"," ",text).strip(" -_|:.()[]")
    return text or clean_title(value)

