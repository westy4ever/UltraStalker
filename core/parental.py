# -*- coding: utf-8 -*-
"""Session-scoped parental unlock state, PIN hashing and brute-force lockout."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

_UNLOCK_UNTIL = 0.0
_FAILED_ATTEMPTS = []
_LOCKED_UNTIL = 0.0
_FAILURE_WINDOW = 10 * 60
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 60
_PBKDF2_LEGACY_ROUNDS = 90000
_PBKDF2_ROUNDS = 210000


def is_unlocked():
    return time.time() < _UNLOCK_UNTIL


def unlock(minutes=30):
    global _UNLOCK_UNTIL, _FAILED_ATTEMPTS, _LOCKED_UNTIL
    try:
        minutes = max(0, int(minutes))
    except (TypeError, ValueError):
        minutes = 30
    _UNLOCK_UNTIL = time.time() + minutes * 60 if minutes else time.time() + 1
    _FAILED_ATTEMPTS = []
    _LOCKED_UNTIL = 0.0
    return _UNLOCK_UNTIL


def lock_now():
    global _UNLOCK_UNTIL
    _UNLOCK_UNTIL = 0.0


def remaining_minutes():
    return max(0, int((_UNLOCK_UNTIL - time.time() + 59) // 60)) if is_unlocked() else 0


def lockout_remaining():
    return max(0, int(_LOCKED_UNTIL - time.time() + 0.999))


def _prune_failures(now=None):
    global _FAILED_ATTEMPTS
    now = time.time() if now is None else float(now)
    _FAILED_ATTEMPTS = [ts for ts in _FAILED_ATTEMPTS if now - ts <= _FAILURE_WINDOW]


def hash_pin(pin, salt=None, rounds=None):
    value = str(pin or "")
    if not (value.isdigit() and 4 <= len(value) <= 8):
        raise ValueError("PIN must contain 4-8 digits")
    salt_bytes = os.urandom(16) if salt is None else bytes.fromhex(str(salt))
    rounds = _PBKDF2_ROUNDS if rounds is None else int(rounds)
    rounds = max(_PBKDF2_LEGACY_ROUNDS, min(1000000, rounds))
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt_bytes, rounds)
    return {"parental_pin": "", "parental_pin_salt": salt_bytes.hex(), "parental_pin_hash": digest.hex(), "parental_pin_rounds": rounds}


def _matches_pin(value, settings):
    value = str(value or "")
    settings = settings if isinstance(settings, dict) else {}
    stored_hash = str(settings.get("parental_pin_hash") or "").strip().lower()
    salt = str(settings.get("parental_pin_salt") or "").strip().lower()
    if stored_hash and salt:
        try:
            try:
                rounds = int(settings.get("parental_pin_rounds") or _PBKDF2_LEGACY_ROUNDS)
            except (TypeError, ValueError):
                rounds = _PBKDF2_LEGACY_ROUNDS
            candidate = hash_pin(value, salt, rounds=rounds).get("parental_pin_hash", "")
            return hmac.compare_digest(candidate, stored_hash)
        except (ValueError, TypeError):
            return False
    legacy = str(settings.get("parental_pin") or "0000")
    return hmac.compare_digest(value, legacy)


def pin_is_default(settings):
    settings = settings if isinstance(settings, dict) else {}
    if settings.get("parental_pin_hash") and settings.get("parental_pin_salt"):
        return False
    return str(settings.get("parental_pin") or "0000") == "0000"


def verify_pin(value, settings):
    """Return ``(ok, message)`` and enforce a session-scoped attempt lockout."""
    global _LOCKED_UNTIL
    now = time.time()
    remaining = lockout_remaining()
    if remaining:
        return False, "Too many incorrect attempts. Try again in %d seconds." % remaining
    if _matches_pin(value, settings):
        unlock(settings.get("parental_session_minutes", 30))
        return True, ""
    _prune_failures(now)
    _FAILED_ATTEMPTS.append(now)
    if len(_FAILED_ATTEMPTS) >= _MAX_ATTEMPTS:
        _LOCKED_UNTIL = now + _LOCKOUT_SECONDS
        _FAILED_ATTEMPTS[:] = []
        return False, "Too many incorrect attempts. Parental PIN is locked for %d seconds." % _LOCKOUT_SECONDS
    left = _MAX_ATTEMPTS - len(_FAILED_ATTEMPTS)
    return False, "Incorrect parental PIN. %d attempt%s remaining." % (left, "" if left == 1 else "s")
