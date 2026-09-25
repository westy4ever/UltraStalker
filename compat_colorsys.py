# -*- coding: utf-8 -*-
"""Small receiver-safe fallback for the stdlib ``colorsys`` module.

Some minimal Enigma2 Python images omit colorsys even though it is normally
part of Python's standard library. Ultra Stalker only needs rgb<->hls here, so
keep an internal implementation to avoid making plugin startup depend on the
receiver packaging that optional module.

The formulas mirror CPython's colorsys behaviour for normalized float inputs.
"""

_ONE_THIRD = 1.0 / 3.0
_ONE_SIXTH = 1.0 / 6.0
_TWO_THIRD = 2.0 / 3.0


def rgb_to_hls(r, g, b):
    maxc = max(r, g, b)
    minc = min(r, g, b)
    summ = maxc + minc
    rangec = maxc - minc
    l = summ / 2.0
    if minc == maxc:
        return 0.0, l, 0.0
    s = rangec / summ if l <= 0.5 else rangec / (2.0 - summ)
    rc = (maxc - r) / rangec
    gc = (maxc - g) / rangec
    bc = (maxc - b) / rangec
    if r == maxc:
        h = bc - gc
    elif g == maxc:
        h = 2.0 + rc - bc
    else:
        h = 4.0 + gc - rc
    h = (h / 6.0) % 1.0
    return h, l, s


def _v(m1, m2, hue):
    hue %= 1.0
    if hue < _ONE_SIXTH:
        return m1 + (m2 - m1) * hue * 6.0
    if hue < 0.5:
        return m2
    if hue < _TWO_THIRD:
        return m1 + (m2 - m1) * (_TWO_THIRD - hue) * 6.0
    return m1


def hls_to_rgb(h, l, s):
    if s == 0.0:
        return l, l, l
    m2 = l * (1.0 + s) if l <= 0.5 else l + s - (l * s)
    m1 = 2.0 * l - m2
    return _v(m1, m2, h + _ONE_THIRD), _v(m1, m2, h), _v(m1, m2, h - _ONE_THIRD)
