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
    # Strip purely decorative edge symbols/emoji that portals commonly prepend
    # or append to VOD/Series names.  Internal punctuation remains untouched.
    text=re.sub(r"^[\s\u2600-\u27ff\u2b00-\u2bff\U0001F000-\U0001FAFF★☆✦✧◆◇●○■□▶▷►]+", "", text)
    text=re.sub(r"[\s\u2600-\u27ff\u2b00-\u2bff\U0001F000-\U0001FAFF★☆✦✧◆◇●○■□▶▷►]+$", "", text)
    # Common routing/service labels wrapped in braces/brackets are not titles.
    text=re.sub(r"^[\[{(]\s*(?:4K|8K|UHD|FHD|HD|AR|EN|ARA|ENG|VIP|TOP|NEW|MOVIE|MOVIES|SERIES|TV)(?:[-_ /|]+(?:4K|8K|UHD|FHD|HD|AR|EN|ARA|ENG|VIP|TOP|NEW|SUBS?|DUBS?))*\s*[\]})]\s*[-:|]*\s*", "", text, flags=re.I)
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
    # Strip residual provider/routing prefixes only when they are KNOWN technical tokens.
    # Never treat an arbitrary short title token as routing metadata: e.g. "X-Men" must stay "X-Men".
    _route=r"(?:DER|GER|DEU|ENG|ARA|LAT|AR|EN|TR|DO|FR|ES|IT|PL|NL|RU|PT|BR|US|UK|EG|KSA|UAE|SUBS?|DUBS?|RAW|4K|8K|UHD|FHD|HD|VIP|TOP)"
    text=re.sub(r"^(?:"+_route+r")(?:[-_/](?:"+_route+r")){0,4}\s*(?:[|┃:/-]+)\s*(?=.+)","",text,flags=re.I)
    text=re.sub(r"\s*(?:[|┃/]+)\s*(?:DER|GER|DEU|ENG|ARA|LAT|SUBS?|DUBS?|RAW|4K|8K|UHD|FHD|HD)(?:\s*(?:[|┃/]+)\s*(?:DER|GER|DEU|ENG|ARA|LAT|SUBS?|DUBS?|RAW|4K|8K|UHD|FHD|HD))*\s*$","",text,flags=re.I)
    # Broken/incomplete country suffixes are common in Stalker names, e.g. "Dark (DE".
    # They are catalogue routing metadata, not part of the visible title.
    text=re.sub(r"\s*[\[(]\s*(?:[A-Z]{2,3}|USA|UAE|KSA)\s*[\])]?$", " ", text, flags=re.I)
    # Also remove trailing standalone provider/routing tokens after a slash/pipe.
    text=re.sub(r"\s*(?:[|┃/]+)\s*[A-Z0-9]{2,6}(?:[-_][A-Z0-9]{1,6}){0,4}\s*$", " ", text)
    # Final pass for provider route prefixes such as "4K-AR -", "AR-AS-D |",
    # "VIP_MOVIES:" while preserving real hyphenated film names.
    text=re.sub(r"^(?:(?:4K|8K|UHD|FHD|HD|AR|EN|ARA|ENG|VIP|TOP|MOVIES?|SERIES|TV|SUBS?|DUBS?)(?:[-_ /]+(?:4K|8K|UHD|FHD|HD|AR|EN|ARA|ENG|VIP|TOP|MOVIES?|SERIES|TV|SUBS?|DUBS?))*)\s*(?:[-:|┃]+)\s*", "", text, flags=re.I)
    text=re.sub(r"\s+"," ",text).strip(" -_|:.()[]{}")
    return text or str(value or "").strip()


def tmdb_search_title(value):
    """Aggressive SEARCH-ONLY title cleaner.

    IMPORTANT: this function must never be used as a catalogue/cache identity.
    The provider's raw label stays untouched for UI and persistent artwork keys.
    Only TMDB/Fanart matching gets this normalized title.
    """
    text=_normalize_unicode_digits(value).strip()
    if not text:
        return ""
    # Decorative edge glyphs / emoji / bullets.
    text=re.sub(r"^[\s\u2600-\u27ff\u2b00-\u2bff\U0001F000-\U0001FAFF★☆✦✧◆◇●○■□▶▷►]+", "", text)
    text=re.sub(r"[\s\u2600-\u27ff\u2b00-\u2bff\U0001F000-\U0001FAFF★☆✦✧◆◇●○■□▶▷►]+$", "", text)
    # Leading provider/platform route labels. Keep this search-only and require a
    # separator so real titles such as X-Men, Se7en or F1 are never damaged.
    edge=r"(?:AMZ|AMZN|NF|NETFLIX|AS|A\+|D\+|DSNP|DISNEY\+|APTV|APPLETV\+|HBO|MAX|HMAX|PM|PRIME|VIP|TOP|VOD|MOVIES?|SERIES|TV|4K|8K|UHD|FHD|HD|AR|ARA|EN|ENG|TR|FR|DE|ES|IT|SUBS?|DUBS?|RAW)"
    # Bracketed tags on either edge.
    text=re.sub(r"^\s*[\[{(]\s*"+edge+r"(?:\s*[-_/|+]\s*"+edge+r")*\s*[\]})]\s*[-:|┃]*\s*", "", text, flags=re.I)
    text=re.sub(r"\s*[-:|┃]*\s*[\[{(]\s*"+edge+r"(?:\s*[-_/|+]\s*"+edge+r")*\s*[\]})]\s*$", "", text, flags=re.I)
    # Plain edge tags: "NF - Title", "D+ | Title", "Title | AMZ".
    for _ in range(3):
        old=text
        text=re.sub(r"^\s*"+edge+r"(?:\s*[-_/+]\s*"+edge+r"){0,4}\s*(?:[-:|┃]+)\s*(?=\S)", "", text, flags=re.I)
        text=re.sub(r"\s*(?:[-:|┃]+)\s*"+edge+r"(?:\s*[-_/+]\s*"+edge+r"){0,4}\s*$", "", text, flags=re.I)
        if text==old: break
    # Year/quality/language decorations at the edges are search noise.
    text=re.sub(r"\s*[\[(]\s*(?:19|20)\d{2}\s*[\])]\s*$", " ", text)
    text=re.sub(r"\s*(?:[-|┃:]+)\s*(?:19|20)\d{2}\s*$", " ", text)
    text=re.sub(r"\s*(?:[-|┃:/]+)\s*(?:4K|8K|UHD|FHD|HD|2160P|1080P|720P|HDR10?|DOLBY|ATMOS|HEVC|H\.?26[45]|X26[45]|WEB[ ._-]?(?:DL|RIP)|BLU[ ._-]?RAY|SUBS?|DUBBED|DUB|RAW|AR|EN|ARA|ENG)\s*$", " ", text, flags=re.I)
    # Generic portal codes are stripped only when isolated by a strong separator.
    text=re.sub(r"^\s*[A-Z0-9+]{2,6}\s+(?:[-|┃:])\s+(?=\S)", "", text)
    text=re.sub(r"\s+(?:[-|┃:])\s+[A-Z0-9+]{2,6}\s*$", "", text)
    # Existing safe search cleanup.
    text=re.sub(r"^(.+?)[,\s]+the$",lambda m:"The "+m.group(1).strip(),text,flags=re.I)
    text=re.sub(r"^(?:\s*[^|┃]{0,40}[|┃])+\s*","",text)
    text=re.sub(r"\([^)]*\)"," ",text)
    text=re.sub(r"\[[^\]]*\]"," ",text)
    text=re.sub(r"\b(?:4K|8K|UHD|FHD|HD|2160P|1080P|720P|HDR10?|DOLBY|ATMOS|HEVC|H\.?26[45]|X26[45]|BLU[ ._-]?RAY|WEB[ ._-]?(?:DL|RIP)|SUBS?|DUBBED|DUB|RAW|VIP|TOP|MOVIES?|SERIES|TV)\b"," ",text,flags=re.I)
    text=re.sub(r"\b(?:مترجم|مدبلج|حصري|جديد|قريبا|قريباً|قريبًا)\b"," ",text,flags=re.I)
    text=re.sub(r"[._'*]+"," ",text)
    text=re.sub(r"\s+"," ",text).strip(" -_|:.")
    return text or clean_title(value)

def tmdb_search_aliases(value):
    """Return Ultra mixed-script search aliases while preserving the full title.

    For mixed-script catalogue names we keep both a conservative Latin-only
    search variant and the complete cleaned title. The visible title is never
    changed by this helper.
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

# Display-only metadata vocabulary.  This deliberately lives OUTSIDE
# catalogue_title(): identity/TMDb/cache callers keep the conservative title,
# while UI labels may discard much noisier provider decoration.
_DISPLAY_NOISE = {
    "4k","8k","uhd","fhd","hd","sd","2160p","1440p","1080p","720p","576p","480p",
    "hdr","hdr10","hdr10+","dv","dolbyvision","dolby","vision","atmos",
    "hevc","avc","av1","h264","h265","x264","x265","10bit","8bit",
    "webdl","webrip","web","dl","rip","bluray","blurayrip","bdrip","brrip","bdremux","remux","hdtv","cam","camrip","ts","telesync",
    "aac","aac2","aac20","aac51","ac3","eac3","dd","ddp","ddp51","dts","dtshd","truehd","audio","dual","multi",
    "subs","sub","subtitle","subtitles","dub","dubs","dubbed","raw",
    "arabic","english","korean","japanese","chinese","hindi","tamil","telugu","thai","turkish","french","german","spanish","italian","portuguese","russian",
    "ara","eng","kor","jpn","chn","hin","tam","tel","tha","tur","fre","fra","ger","deu","spa","ita","por","rus",
    "netflix","nf","amazon","amzn","prime","primevideo","disney","disney+","dsnp","hbo","max","hmax","appletv","appletv+","aptv","shahid","watchit","osn","starzplay",
    "vip","top","vod","movie","movies","film","films","series","tv","new","latest","exclusive","premiere","pure",
    "مترجم","مدبلج","حصري","حصريا","حصرياً","حصريًا","جديد","قريبا","قريباً","قريبًا","افلام","أفلام","فيلم","مسلسلات","مسلسل",
}
_DISPLAY_AMBIGUOUS_CODES={"ar","en","tr","fr","de","es","it","pt","ru","us","uk","in","kr","jp","cn","eg","ae","sa","br"}
_DISPLAY_BARE_SAFE = _DISPLAY_NOISE - _DISPLAY_AMBIGUOUS_CODES - {"film","films","movie","movies","series","tv","new","latest","pure"}

# R100 display-only bracket normalization. IPTV labels sometimes contain
# full-width/small/ornate parentheses plus invisible RTL/Bidi controls. They
# render like normal brackets on Enigma2 but do not match ASCII-only regexes.
_DISPLAY_BRACKET_TRANSLATE = str.maketrans({
    "\uff08":"(", "\uff09":")", "\ufe59":"(", "\ufe5a":")",
    "\uff3b":"[", "\uff3d":"]", "\ufe47":"[", "\ufe48":"]",
    "\uff5b":"{", "\uff5d":"}", "\ufe5b":"{", "\ufe5c":"}",
    "\ufd3e":"(", "\ufd3f":")", "\u2768":"(", "\u2769":")",
    "\u27ee":"(", "\u27ef":")",
})
_DISPLAY_BIDI_CONTROLS = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_DISPLAY_ANNOUNCE_RE = r"(?:قريب(?:ا|اً|ًا)?|حصري(?:ا|اً|ًا)?|جديد|قادم|يعرض\s+الآن|مترجم|مدبلج|soon|coming\s+soon|new|exclusive|premiere)"

def _normalize_display_brackets(value):
    text=str(value or "")
    text=_DISPLAY_BIDI_CONTROLS.sub("", text)
    return text.translate(_DISPLAY_BRACKET_TRANSLATE)

def _raw_display_bracket_purge(value):
    """Remove bracket decoration before conservative identity cleanup.

    This is DISPLAY-ONLY. It deliberately understands malformed one-sided
    wrappers and RTL-rendered labels. Raw provider identity is never changed.
    """
    text=_normalize_display_brackets(value).strip()
    if not text:
        return text

    # R101 rescue: some providers publish a short catalogue code outside the
    # real localized title, e.g. ``SH (سيدو والحرية)``.  The user's bracket
    # policy must not throw away the meaningful Arabic programme name and leave
    # only the route/code.  This is DISPLAY-ONLY; raw identity remains intact.
    short_code=r"[A-Za-z0-9+._-]{1,8}"
    wrapped_ar=r"[^()\[\]{}]{2,180}[\u0600-\u06ff][^()\[\]{}]{1,180}"
    rescue_patterns=(
        r"^\s*("+short_code+r")\s*(?:[-|┃•·:]+\s*)?[\(\[\{]\s*("+wrapped_ar+r")\s*[\)\]\}]\s*$",
        r"^\s*[\(\[\{]\s*("+wrapped_ar+r")\s*[\)\]\}]\s*(?:[-|┃•·:]+\s*)?("+short_code+r")\s*$",
    )
    for idx,pat in enumerate(rescue_patterns):
        m=re.match(pat,text,flags=re.I)
        if not m:
            continue
        candidate=(m.group(2) if idx==0 else m.group(1)).strip()
        letters=re.sub(r"[^\w\u0600-\u06ff]+","",candidate,flags=re.UNICODE)
        if len(letters)>=3 and re.search(r"[\u0600-\u06ff]",candidate):
            text=candidate
            break

    # Complete wrappers: user policy is explicit -- anything inside a wrapper
    # is display decoration. Iterate for adjacent/nested simple groups.
    for _ in range(6):
        old=text
        text=re.sub(r"\[[^\[\]]{0,180}\]", " ", text)
        text=re.sub(r"\([^()]{0,180}\)", " ", text)
        text=re.sub(r"\{[^{}]{0,180}\}", " ", text)
        if text==old:
            break

    # Known malformed prefix decoration:  "قريبا) Title", "2026] Title",
    # including the visually mirrored RTL forms seen on receiver skins.
    prefix_noise=r"(?:"+_DISPLAY_ANNOUNCE_RE+r"|(?:19|20)\d{2}|4K|8K|UHD|FHD|HD|2160P|1080P|720P|HDR10?|WEB[ ._-]?(?:DL|RIP)|SUBS?|DUBBED|DUB|ARABIC|ENGLISH|KOREAN|JAPANESE|CHINESE)"
    for _ in range(4):
        old=text
        text=re.sub(r"^\s*[\(\[\{]?\s*"+prefix_noise+r"\s*[\)\]\}]\s*[-:|┃•·]*\s*", "", text, flags=re.I)
        text=re.sub(r"^\s*"+prefix_noise+r"\s*[\)\]\}]\s*[-:|┃•·]*\s*", "", text, flags=re.I)
        if text==old:
            break

    # Known malformed suffix decoration: "Title (قريبا", "Title 2026)".
    text=re.sub(r"\s+[\(\[\{]\s*"+prefix_noise+r"\s*$", "", text, flags=re.I)
    text=re.sub(r"\s+"+prefix_noise+r"\s*[\)\]\}]\s*$", "", text, flags=re.I)

    # Generic unmatched opener after meaningful title: by user policy,
    # everything from that opener to EOL is decoration even when unknown.
    # Preserve a leading opener so we can still rescue a title after a malformed
    # prefix instead of deleting the whole label.
    m=re.search(r"[\(\[\{]", text)
    if m and m.start()>0:
        head=text[:m.start()].strip(" -_|┃•·:._")
        if head:
            text=head

    # Any remaining orphan closers are punctuation only. Known prefix/suffix
    # noise was already removed above, so deleting the glyph cannot damage a
    # legitimate title token such as X-Men or Face/Off.
    text=re.sub(r"[\)\]\}]", " ", text)
    text=re.sub(r"^[\(\[\{]+", " ", text)
    text=re.sub(r"\s+", " ", text).strip(" -_|┃•·:._()[]{}")
    return text

def _display_token(value):
    t=str(value or "").strip().casefold()
    t=t.replace("h.264","h264").replace("h.265","h265").replace("dolby vision","dolbyvision")
    t=re.sub(r"[._\-\s]+","",t)
    return t

def _display_is_noise_segment(value):
    raw=str(value or "").strip(" \t-_|┃:;,.()[]{}")
    if not raw:return True
    if re.fullmatch(r"(?:19|20)\d{2}",raw):return True
    low=raw.casefold().replace("+","+")
    # Common compound technical forms.
    if re.fullmatch(r"(?i)(?:\d{3,4}p|[48]k|hdr10?\+?|web[ ._-]?(?:dl|rip)|blu[ ._-]?ray|h\.?26[45]|x26[45]|ddp?\s*5[ .]?1|aac\s*[25][ .]?[01]|eac3|ac3|dts(?:[ ._-]?hd)?|10bit|8bit)",raw):return True
    words=[w for w in re.split(r"[^\w+]+",low,flags=re.UNICODE) if w]
    if not words:return True
    known=0
    for w in words:
        n=_display_token(w)
        if w in _DISPLAY_NOISE or n in _DISPLAY_NOISE or w in _DISPLAY_AMBIGUOUS_CODES:
            known+=1;continue
        if re.fullmatch(r"\d{3,4}p",w) or re.fullmatch(r"[48]k",w):known+=1;continue
        if re.fullmatch(r"(?:19|20)\d{2}",w):known+=1;continue
    if known==len(words):return True
    # Provider/release-group codes are safe to drop only as an isolated segment
    # next to a strong separator, never from the middle of a real title.
    if len(words)==1 and re.fullmatch(r"[A-Z0-9+]{2,7}",raw) and not re.fullmatch(r"\d+",raw):return True
    return False

def display_title(value, enabled=None):
    """Aggressive DISPLAY-ONLY movie/series title cleaner.

    Raw provider titles remain untouched for identity, TMDb matching, persistent
    cache keys and cross-source grouping.  This function is intended only for
    visible labels/fallback text.
    """
    original=str(value or "").strip()
    if enabled is None:
        try:
            from .storage import load_settings
            enabled=bool((load_settings() or {}).get("clean_titles",True))
        except Exception:
            enabled=True
    if not enabled:
        return original
    # Purge display wrappers from the RAW label first. This preserves evidence
    # such as one-sided/Unicode brackets that conservative clean_title() may
    # otherwise normalize away before the UI has a chance to act on it.
    raw_display=_raw_display_bracket_purge(original)
    text=clean_title(raw_display or original)
    if not text:return raw_display or original
    _raw_orphan_closer=bool(re.search(r"[\)\]\}]\s*$", _normalize_display_brackets(original)))
    # Some legacy labels arrive as adjacent tags like [KOR][1080P]. The
    # conservative cleaner may consume the first opening bracket while leaving
    # a harmless routing token plus closing bracket; repair that display-only.
    text=re.sub(r"^(?:KOR|JPN|CHN|IND|ARA|ENG|TUR|FRA|GER|DEU|SPA|ITA|POR|RUS|THA|HIN|TAM|TEL)\]\s*", "", text, flags=re.I)

    # Clean Titles ON means the visible label is the programme name only.
    # Provider/editorial decorations inside (), [] or {} are therefore removed
    # unconditionally at DISPLAY time. Raw/identity titles stay untouched.
    # Iterate to cover adjacent/nested wrappers such as [KOR][1080P] Title.
    for _ in range(4):
        old=text
        text=re.sub(r"\[[^\[\]]{0,120}\]", " ", text)
        text=re.sub(r"\([^()]{0,120}\)", " ", text)
        text=re.sub(r"\{[^{}]{0,120}\}", " ", text)
        if text==old:break
    # Portals also ship malformed ONE-SIDED wrappers. Treat both directions
    # symmetrically at DISPLAY time: ``Title (2026`` / ``Title 2026)`` and
    # ``Title [قريبا`` / ``Title قريبا]`` must all present as ``Title``.
    # Opening-only tails are unambiguous: everything from the unmatched opener
    # to the end is decoration. For closing-only tails, strip the orphan closer
    # first, then remove a trailing year/noise/announcement segment below.
    text=re.sub(r"\s*[\(\[\{][^\)\]\}]{1,120}$", " ", text)
    _had_orphan_closer=_raw_orphan_closer or bool(re.search(r"[\)\]\}]\s*$", text))
    if _had_orphan_closer:
        text=re.sub(r"[\)\]\}]\s*$", "", text).rstrip()
        # A year immediately before an orphan closer is provider decoration.
        text=re.sub(r"\s+(?:19|20)\d{2}\s*$", "", text)
        # Likewise for known announcement/technical tails. Iterate so forms
        # such as ``Title Arabic 1080p)`` collapse cleanly without touching
        # the real title on the left.
        for _ in range(6):
            m=re.search(r"(?:\s+|[._]+)([^\s._|┃•·]+)\s*$",text)
            if not m:break
            token=m.group(1)
            norm=_display_token(token)
            low=token.casefold()
            noisy=(low in _DISPLAY_NOISE or norm in _DISPLAY_NOISE or
                   bool(re.fullmatch(r"(?i)\d{3,4}p|[48]k|hdr10?\+?|h\.?26[45]|x26[45]|10bit|8bit",token)))
            if not noisy:break
            head=text[:m.start()].strip(" -_|┃•·:._()[]{}")
            if not head:break
            text=head
        text=re.sub(r"\s*(?:[-|┃•·:]+\s*)?(?:قريب(?:ا|اً|ًا)?|مترجم|مدبلج|حصري(?:ا|اً|ًا)?|جديد)\s*$","",text,flags=re.I)

    # Strong separators are reliable portal boundaries. Peel metadata from both
    # edges but never split real punctuation such as X-Men, Face/Off or a colon
    # inside a film title.
    parts=[p.strip() for p in re.split(r"\s*(?:\||┃|•|·)\s*|\s+-\s+",text) if p.strip()]
    if len(parts)>1:
        while len(parts)>1 and _display_is_noise_segment(parts[0]):parts.pop(0)
        while len(parts)>1 and _display_is_noise_segment(parts[-1]):parts.pop()
        text=" - ".join(parts)

    # Edge platform/provider labels without a separator.
    # Only unambiguous provider brands are stripped without a separator.
    # Ambiguous words such as TOP/MAX/PRIME stay unless isolated by a strong
    # portal separator, so real titles like Top Gun and Max Payne survive.
    platforms=r"(?:NETFLIX|NF|AMAZON|AMZN|PRIME\s*VIDEO|DISNEY\+?|DSNP|HBO|HMAX|APPLE\s*TV\+?|APTV|SHAHID|WATCHIT|OSN|STARZPLAY)"
    text=re.sub(r"^\s*"+platforms+r"\s+(?=\S)","",text,flags=re.I)
    text=re.sub(r"\s+"+platforms+r"\s*$","",text,flags=re.I)

    # Remove trailing year decorations, but preserve numeric titles such as 1917
    # when the year is the whole remaining title.
    def _strip_year_tail(t):
        # Bare numeric suffixes can be part of the real title (Blade Runner 2049,
        # Death Race 2000). Only a strong separator marks a plain year as
        # provider decoration; bracketed years were already handled above.
        m=re.search(r"\s*[-|┃•·]\s*((?:19|20)\d{2})\s*$",t)
        if not m:return t
        head=t[:m.start()].strip(" -_|┃•·:.()[]{}")
        return head if head else t
    text=_strip_year_tail(text)

    # Peel unbracketed technical tails one token at a time. Ambiguous two-letter
    # language/country codes require a strong separator and are intentionally not
    # removed here.
    for _ in range(14):
        m=re.search(r"(?:\s+|[._]+)([^\s._|┃•·]+)\s*$",text)
        if not m:break
        token=m.group(1)
        norm=_display_token(token)
        low=token.casefold()
        noisy=(low in _DISPLAY_BARE_SAFE or norm in _DISPLAY_BARE_SAFE or
               bool(re.fullmatch(r"(?i)\d{3,4}p|[48]k|hdr10?\+?|h\.?26[45]|x26[45]|10bit|8bit",token)))
        if not noisy:break
        head=text[:m.start()].strip(" -_|┃•·:._()[]{}")
        if not head:break
        text=head

    # Arabic/English announcement words are decoration when they are a separate
    # prefix/suffix, never when embedded inside the real title.
    text=re.sub(r"^(?:حصري(?:ا|اً|ًا)?|جديد|قريب(?:ا|اً|ًا)?|مترجم|مدبلج)\s*(?:[-|┃•·:]+\s*)?","",text,flags=re.I)
    text=re.sub(r"\s*(?:[-|┃•·:]+\s*)?(?:مترجم|مدبلج|حصري(?:ا|اً|ًا)?|جديد)\s*$","",text,flags=re.I)

    # Remove standalone PURE branding only when a meaningful title survives.
    without=re.sub(r"(?iu)(?<!\w)pure(?!\w)"," ",text)
    without=re.sub(r"\s+"," ",without).strip(" -_|┃•·:.()[]{}")
    if len(re.sub(r"[\W_]+","",without,flags=re.UNICODE))>=2:text=without

    text=re.sub(r"\s+"," ",text).strip(" -_|┃•·:.()[]{}")
    return text or catalogue_title(original) or original

