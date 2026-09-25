# -*- coding: utf-8 -*-
"""Ultra Stalker bundled provider credentials.

The payloads below are deliberately obfuscated so release packages do not carry
provider keys as readable plaintext. This is package obfuscation, not a security
boundary: a determined person with the package and runtime code can recover the
values. User credentials in /etc/enigma2/ultrastalker/api_keys.conf always win.
"""
import base64
import hashlib
import zlib

_BLOBS = {
    "TMDB_API_KEY": "u7E5`@*ssd^nmF?!Q}$S*XkB-oZ*l8iNG9vUbKc_Q{30Ig0hi?",
    "SUBDL_API_KEY": "b#?P>2@{R&l&BOWB1E5ItuG*Aimp8%lyAvdQ*@(3LZE%b#PpW9381{`l0pDMxnp~*^H9#O7y",
    "SUBSOURCE_API_KEY": "oT>BGzEc^QX(p(C<aoLP&p_M;R=8Lff`>2dWzZdNBcz70w4|896B06oEfT<wX5w71B62MFUyih5!U}*0",
}


def _stream(name, size):
    seed = hashlib.sha256(("UltraStalker|builtin|%s|R253" % name).encode("utf-8")).digest()
    output = bytearray()
    counter = 0
    while len(output) < size:
        output.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:size])


def _decode(name, payload):
    encrypted = base64.b85decode(payload.encode("ascii"))
    key = _stream(name, len(encrypted))
    packed = bytes(a ^ b for a, b in zip(encrypted, key))
    return zlib.decompress(packed).decode("utf-8").strip()


def builtin_api_credentials():
    """Return bundled defaults. Callers must let explicit user values override."""
    values = {}
    for name, payload in _BLOBS.items():
        try:
            value = _decode(name, payload)
        except Exception:
            value = ""
        if value:
            values[name] = value
    return values
