# -*- coding: utf-8 -*-
import threading
import time
from ..client import StalkerClient


def _looks_like_m3u_url(value):
    low=str(value or "").strip().lower()
    if not low:return False
    return (".m3u" in low or "type=m3u" in low or "output=m3u" in low or
            ("get.php" in low and ("username=" in low or "password=" in low)))


class PortalSession:
    _instances = {}
    _lock = threading.RLock()

    @staticmethod
    def _key(profile):
        profile = profile if isinstance(profile, dict) else {}
        return (
            str(profile.get("portal", "")).rstrip("/").lower(),
            str(profile.get("mac", "")).upper(),
            str(profile.get("source_type") or ("m3u" if _looks_like_m3u_url(profile.get("portal")) else "stalker")).lower(),
            str(profile.get("tls_mode", "auto")).lower(),
            str(profile.get("device_profile", "auto")).lower(),
        )

    def __new__(cls, profile, timeout=10):
        key = cls._key(profile)
        with cls._lock:
            obj = cls._instances.get(key)
            if obj is None:
                obj = super().__new__(cls)
                cls._instances[key] = obj
                obj._initialized = False
            return obj

    def __init__(self, profile, timeout=10):
        if self._initialized:
            self.client.timeout = timeout
            return
        self.profile = dict(profile)
        source_type=str(profile.get("source_type") or ("m3u" if _looks_like_m3u_url(profile.get("portal")) else "stalker")).lower()
        if source_type=="m3u":
            from ..m3u_adapter import M3UClient
            self.client=M3UClient(profile["portal"],profile.get("mac") or "M3U",timeout=timeout)
        else:
            self.client = StalkerClient(
                profile["portal"], profile["mac"], timeout=timeout,
                tls_mode=profile.get("tls_mode", "auto"),
                device_profile=profile.get("device_profile", "auto"),
            )
        self.last_success = 0
        self.last_error = ""
        self.latency_ms = 0
        self._initialized = True

    @classmethod
    def invalidate(cls, profile=None):
        """Drop cached client/session state after portal edits or deletion."""
        with cls._lock:
            if not profile:
                removed=list(cls._instances.values())
                cls._instances.clear()
            else:
                portal = str((profile or {}).get("portal") or "").rstrip("/").lower()
                mac = str((profile or {}).get("mac") or "").upper()
                keys = [key for key in cls._instances if key[0] == portal and key[1] == mac]
                removed=[cls._instances.pop(key, None) for key in keys]
        for session in removed:
            try:
                session.client.close()
            except Exception:
                pass
        return len([session for session in removed if session is not None])

    def authorize(self):
        start = time.monotonic()
        try:
            result = self.client.authorize()
            self.last_success = time.time()
            self.last_error = ""
            self.latency_ms = int((time.monotonic() - start) * 1000)
            return result
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def health(self):
        return {
            "last_success": self.last_success,
            "last_error": self.last_error,
            "latency_ms": self.latency_ms,
            "endpoint": getattr(self.client, "endpoint", None),
            "authorized": bool(getattr(self.client, "token", None)),
            "security_warning": getattr(self.client, "security_warning", ""),
        }
