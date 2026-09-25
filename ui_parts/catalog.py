# -*- coding: utf-8 -*-
"""Pure display-label normalization shared by catalogue/list screens."""
import re
import unicodedata


def strip_arabic_tashkeel(value):
    """Remove optional Arabic tashkeel for display only.

    Preserve decomposed maddah/hamza marks because they are part of the letter
    identity, matching the Details description policy. Stored/provider text and
    language selection are never changed.
    """
    text=str(value or "")
    if not text:
        return text
    preserve={0x0653,0x0654,0x0655,0x065F}
    out=[]
    for ch in text:
        cp=ord(ch)
        if cp in preserve:
            out.append(ch)
            continue
        in_arabic_block=(0x0600<=cp<=0x06FF) or (0x0750<=cp<=0x077F) or (0x08A0<=cp<=0x08FF)
        if in_arabic_block and unicodedata.category(ch) in ("Mn","Me"):
            continue
        out.append(ch)
    return "".join(out)


def clean_display_text(value, max_chars=140):
    raw = unicodedata.normalize("NFKC", str(value or ""))
    chars = []
    for ch in raw:
        category = unicodedata.category(ch)
        if ch.isspace(): chars.append(" ")
        elif category in ("Cc", "Cf", "Cs", "Co", "Cn"): continue
        else: chars.append(ch)
    clean = re.sub(r"\s+", " ", "".join(chars)).strip(" -|•\t\r\n")
    clean = "".join(ch for ch in clean if not (
        ch == "\u0640" or 0x064B <= ord(ch) <= 0x065F or ord(ch) == 0x0670
        or 0x06D6 <= ord(ch) <= 0x06ED
    ))
    clean = unicodedata.normalize("NFC", clean)
    if max_chars and len(clean) > max_chars:
        clean = clean[:max_chars - 1].rstrip() + "…"
    return clean


def clean_live_channel_name(value, enabled=True):
    raw=str(value or "").strip()
    if not enabled:
        return raw or "Channel"
    text=raw
    try:text=unicodedata.normalize("NFKC",text)
    except Exception:pass
    text=re.sub(r"^\s*#+\s*","",text); text=re.sub(r"\s*#+\s*$","",text)
    text=re.sub(r"^\s*[A-Z]{2,3}\s*[:|•/_-]\s*","",text)
    text=re.sub(r"(?i)\(\s*(?:EVENT\s*ONLY|BACKUP|TEST|VIP)\s*\)"," ",text)
    tokens=("3840P","2160P","1440P","1080P","720P","576P","480P","FULL HD","FULLHD","FHD","ULTRA HD","ULTRAHD","UHD","HDR10+","HDR10","HDR","DOLBY VISION","DOLBYVISION","HEVC","H.265","H265","H.264","H264","60FPS","50FPS","30FPS","8K","4K","RAW")
    for _ in range(5):
        old=text
        for token in sorted(tokens,key=len,reverse=True):
            text=re.sub(r"(?i)(?<![A-Z0-9])"+re.escape(token)+r"(?![A-Z0-9])"," ",text)
        text=re.sub(r"\s*[\[\]{}<>|•·]+\s*"," ",text)
        text=re.sub(r"\s*[:;,_/\\]+\s*"," ",text)
        text=re.sub(r"\s{2,}"," ",text).strip(" -:|•·_")
        if text==old:break
    return text or str(value or "Channel").strip()
