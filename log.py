# -*- coding: utf-8 -*-
"""Small rotating logger with secret redaction and private file permissions."""
import logging
import logging.handlers
import os
import re
import urllib.parse
from .securefs import secure_private_dir

LOG_DIR = "/tmp/ultrastalker"
LOG_FILE = os.path.join(LOG_DIR, "plugin.log")
_LOGGER = None
_OPTIONAL_ONCE = set()

_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_SECRET_RE = re.compile(r"(?i)(token|authorization|api[_ -]?key|secret|credential|password|passwd|bearer|username)([\"'=:\s]+)([^&\s,}\"]+)")
_QUERY_RE = re.compile(r"(?i)([?&](?:token|auth|authorization|key|api_key|apikey|password|passwd|mac|play_token)=)([^&\s]+)")
_USERINFO_RE = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
_RAW_URL_RE = re.compile(r"(?i)https?://[^\s'\"<>]+")
_ENCODED_URL_RE = re.compile(r"(?i)https?%3A(?:%2F|/){2}[^\s'\"<>]+")

_AUTH_HEADER_RE = re.compile(r"(?i)\b(authorization)([\"'=:\s]+)(?:(?:bearer|basic)\s+)?([^&\s,}\"]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+([^&\s,}\"]+)")
_COOKIE_HEADER_RE = re.compile(r"(?im)\b(set-cookie|cookie)([\"'=:\s]+)([^\r\n]*)")

def _redact_url(match):
    value = match.group(0)
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or "host"
        port = (":" + str(parsed.port)) if parsed.port else ""
        return "%s://%s%s/[REDACTED]" % (parsed.scheme, host, port)
    except Exception:
        return "[URL REDACTED]"


def redact(value):
    text = str(value or "")
    text = _MAC_RE.sub("XX:XX:XX:XX:XX:XX", text)
    text = _USERINFO_RE.sub(r"\1[REDACTED]:[REDACTED]@", text)
    # Redact URL-shaped values first. Otherwise a path segment such as
    # ``/secret token:`` can be mistaken for a key/value pair and hide the
    # real token that follows it from the secret matcher.
    text = _RAW_URL_RE.sub(_redact_url, text)
    text = _ENCODED_URL_RE.sub("[ENCODED URL REDACTED]", text)
    text = _QUERY_RE.sub(r"\1[REDACTED]", text)
    # Cookie values may carry arbitrary session identifiers whose key names are
    # provider-specific.  Redact the whole header rather than maintaining an
    # incomplete allow/deny list of cookie names.
    text = _COOKIE_HEADER_RE.sub(r"\1\2[REDACTED]", text)
    text = _AUTH_HEADER_RE.sub(r"\1\2[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_RE.sub(r"\1\2[REDACTED]", text)
    return text


class _RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


class _SecureRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def emit(self, record):
        try:secure_private_dir(LOG_DIR)
        except Exception:pass
        try:
            super().emit(record)
        finally:
            self._secure_modes()

    def _secure_modes(self):
        try: os.chmod(LOG_DIR, 0o700)
        except OSError: pass
        for path in (LOG_FILE, LOG_FILE + ".1", LOG_FILE + ".2"):
            try:
                if os.path.exists(path): os.chmod(path, 0o600)
            except OSError:
                pass

    def doRollover(self):
        super().doRollover()
        self._secure_modes()


def get_logger():
    """Return the process logger without touching persistent configuration.

    This function is deliberately import-safe: many Ultra Stalker modules keep a
    module-level LOG reference, so consulting settings here would turn a harmless
    import into /etc/enigma2 I/O and possible config creation. Runtime startup
    applies the user's diagnostic preference explicitly via configure_logger().
    """
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("UltraStalker")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            handler = _SecureRotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=2, delay=True)
            handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
            logger.addHandler(handler)
        except Exception:
            logger.addHandler(logging.NullHandler())
    _LOGGER = logger
    return logger


def configure_logger(diagnostic=None):
    """Apply runtime log verbosity after normal plugin/session startup.

    Persistent settings are read only when this function is explicitly called,
    never as a side effect of importing a module.
    """
    logger=get_logger()
    if diagnostic is None:
        try:
            from .storage import load_settings
            diagnostic=bool(load_settings().get("diagnostic_logging", False))
        except Exception:
            diagnostic=False
    enabled=bool(diagnostic)
    logger.setLevel(logging.DEBUG if enabled else logging.INFO)
    try:
        from .core.runtime_log import configure_runtime_log
        configure_runtime_log(persistent=enabled)
    except Exception:
        pass
    return logger


def exception(message):
    try:
        get_logger().exception(message)
    except Exception:
        pass


def diagnostic_failure(capability, exc):
    """Record non-critical fail-soft paths only when diagnostic logging is enabled."""
    try:
        logger=get_logger()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Diagnostic fail-soft path [%s]: %s",str(capability or "unknown"),exc,exc_info=True)
    except Exception:
        pass


def optional_failure(capability, exc):
    """Record compatibility failures once; include traceback in diagnostic mode."""
    try:
        logger=get_logger();cap=str(capability or "unknown")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Optional capability unavailable [%s]: %s",cap,exc,exc_info=True)
        else:
            key=(cap,type(exc).__name__,str(exc)[:120])
            if key not in _OPTIONAL_ONCE:
                _OPTIONAL_ONCE.add(key);logger.info("Optional capability unavailable [%s]: %s",cap,exc)
    except Exception:pass
