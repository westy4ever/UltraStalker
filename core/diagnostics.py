# -*- coding: utf-8 -*-
"""Privacy-safe runtime diagnostics and support-bundle export."""
import json
import os
import platform
import re
import time
import zipfile
from ..securefs import secure_private_dir
import tempfile

from .session import PortalSession
from .perf import PERF
from .maintenance import cache_stats, runtime_health
from .endurance import ENDURANCE
from ..repositories.database import DB
from ..compat import python_compatibility, runtime_capabilities
from ..version import PLUGIN_VERSION, BUILD_NAME
from ..log import redact as _log_redact, get_logger

CONFIG_DIR = '/etc/enigma2/ultrastalker'
LOG_FILE = '/tmp/ultrastalker/plugin.log'
LOG = get_logger()


_BUNDLE_URL_RE = re.compile(r"(?i)https?://[^\s'\"<>]+")
_BUNDLE_ENCODED_URL_RE = re.compile(r"(?i)https?%3A(?:%2F|/){2}[^\s'\"<>]+")

def _strict_redact_text(value):
    """Support-bundle redaction hides private hosts as well as URL paths."""
    text = _log_redact(value)
    text = _BUNDLE_URL_RE.sub("[URL REDACTED]", text)
    text = _BUNDLE_ENCODED_URL_RE.sub("[ENCODED URL REDACTED]", text)
    return text

_SENSITIVE_KEY_PARTS = (
    'credential', 'api_key', 'apikey', 'secret', 'token', 'password', 'passwd',
    'pin', 'authorization', 'access_key', 'session_key', 'dataset_id',
    'revision_id', 'asset_id', 'endpoint', 'portal', 'url', 'mac',
)


def redact(value):
    """Redact secrets, MACs and every URL-shaped value from arbitrary text."""
    return _strict_redact_text(value)


def _redact_tree(value, key_hint=''):
    """Recursively sanitize a structure before it can enter a support bundle."""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            key_text = str(key)
            low = key_text.lower()
            if any(part in low for part in _SENSITIVE_KEY_PARTS):
                if child in (None, '', False, 0):
                    out[key] = child
                elif 'mac' in low:
                    out[key] = 'XX:XX:XX:XX:XX:XX'
                else:
                    # Private portal/API hosts are sensitive too; do not retain
                    # even a sanitized hostname in support bundles.
                    out[key] = '[REDACTED]'
            else:
                out[key] = _redact_tree(child, key_text)
        return out
    if isinstance(value, list):
        return [_redact_tree(item, key_hint) for item in value]
    if isinstance(value, tuple):
        return [_redact_tree(item, key_hint) for item in value]
    if isinstance(value, str):
        return _strict_redact_text(value)
    return value

def _receiver_info():
    info = {}
    for path, key in (("/etc/image-version", "image_version"), ("/etc/issue", "image_issue"), ("/etc/os-release", "os_release")):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as handle:
                info[key] = redact(handle.read(8192)).strip()
        except OSError:
            pass
    return info


def snapshot(profile=None):
    data = {
        "plugin_version": PLUGIN_VERSION,
        "build": BUILD_NAME,
        "generated_at": int(time.time()),
        "python": platform.python_version(),
        "python_compatibility": python_compatibility(),
        "platform": platform.platform(),
        "database": DB.health(),
        "receiver": _receiver_info(),
        "runtime_capabilities": runtime_capabilities(),
        "performance": PERF.snapshot(),
        "runtime_health": runtime_health(),
        "endurance": ENDURANCE.snapshot("diagnostics"),
        "persistent_cache": cache_stats(),
    }
    try:
        from .bouquets import proxy_status
        data["bouquet_proxy"] = proxy_status()
    except Exception as exc:
        data["bouquet_proxy"] = {"running": False, "error": redact(exc)}
    if profile:
        data['profile'] = {
            "portal": "[REDACTED]" if profile.get('portal') else "",
            "mac": "XX:XX:XX:XX:XX:XX",
            "health": str(profile.get('health') or 'unknown'),
            "latency_ms": profile.get('latency_ms', 0),
            "last_success": profile.get('last_success', 0),
            "last_error": redact(profile.get('last_error', '')),
        }
        try:
            data['session'] = _redact_tree(PortalSession(profile).health())
        except Exception as exc:
            data['session_error'] = redact(exc)
    return _redact_tree(data)


def _secure_temp_for(path):
    directory = os.path.dirname(path) or '.'
    if directory.startswith("/tmp/"):
        secure_private_dir(directory)
    else:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.%s.' % os.path.basename(path), suffix='.tmp', dir=directory)
    try: os.chmod(temp, 0o600)
    except OSError: pass
    return fd, temp

def export(path='/tmp/ultrastalker-diagnostics.json', profile=None):
    temp = None
    fd = None
    try:
        fd, temp = _secure_temp_for(path)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            fd = None
            json.dump(snapshot(profile), handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path); temp = None
        os.chmod(path, 0o600)
    finally:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        if temp:
            try: os.unlink(temp)
            except OSError: pass
    return path


def export_support_bundle(path='/tmp/ultrastalker-support.zip', profile=None):
    diag = _redact_tree(snapshot(profile))
    settings = {}
    try:
        from ..storage import load_settings
        settings = _redact_tree(load_settings())
    except Exception as exc:
        LOG.debug("Support bundle could not load settings: %s", exc)
    temp = None
    fd = None
    try:
        fd, temp = _secure_temp_for(path)
        os.close(fd); fd = None
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('diagnostics.json', json.dumps(diag, ensure_ascii=False, indent=2))
            archive.writestr('settings-redacted.json', json.dumps(settings, ensure_ascii=False, indent=2))
            try:
                with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as handle:
                    lines = handle.readlines()[-1500:]
                archive.writestr('plugin-log-redacted.txt', ''.join(_strict_redact_text(line) for line in lines))
            except Exception as exc:
                LOG.debug("Support bundle could not include plugin log: %s", exc)
            try:
                arch = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ARCHITECTURE.md')
                with open(arch, 'r', encoding='utf-8', errors='replace') as handle:
                    archive.writestr('ARCHITECTURE.md', handle.read())
            except Exception as exc:
                LOG.debug("Support bundle could not include architecture file: %s", exc)
        os.replace(temp, path); temp = None
        os.chmod(path, 0o600)
        return path
    finally:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        if temp:
            try: os.unlink(temp)
            except OSError: pass
