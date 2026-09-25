# -*- coding: utf-8 -*-
"""Bounded maintenance for reproducible files in ``generated``.

This module intentionally has no Enigma2 imports.  It protects the persistent
poster/backdrop library and only manages assets that can be regenerated.

Design rules:
* one caller-independent cache identity for dynamic Details chrome;
* bounded generated cache by both file count and bytes;
* conservative orphan cleanup (temporary/broken/deprecated experiment files);
* recent-use ledger so active generated assets win over stale ones during LRU.
"""
from __future__ import absolute_import

import hashlib
import json
import os
import threading
import time

from .. import persistent_cache

# Hard ceiling and lower trim target.  File count is intentional: the receiver
# incident was dominated by >100k tiny PNGs even though their byte size was not
# exceptional.  Bytes remain a second independent safety rail.
MAX_GENERATED_FILES = 75000
TARGET_GENERATED_FILES = 60000
MAX_GENERATED_BYTES = 12 * 1024 * 1024 * 1024
TARGET_GENERATED_BYTES = 10 * 1024 * 1024 * 1024

# A recently-used generated asset is not an eviction candidate.  The ledger is
# tiny compared with the artwork vault and is flushed only by background
# maintenance, never on every navigation event.
RECENT_USE_SECONDS = 14 * 24 * 60 * 60
ACCESS_LEDGER_MAX = 90000
ACCESS_LEDGER_PATH = os.path.join(persistent_cache.INDEX, "generated_access_v1.json")

# These prefixes came only from abandoned experimental UI paths and are not
# referenced by the retained public runtime.  They are safe orphan candidates.
_DEPRECATED_PREFIXES = (
    "livev2_preview_",
    "livev2_info_",
    "homehero_preview210_",
)

# Title-logo masters/presentations are small, user-visible identity assets.
# They are regenerated much less predictably than ordinary chrome and the
# Player intentionally resolves them from HDD first.  Never evict these as
# generic budget images; otherwise a valid logo can disappear between screens
# and the Player falls back to plain title text until another screen rebuilds it.
_BUDGET_PROTECTED_DIRS = (
    "pgv2_title_logos/",
    "title_logos/",
    "player_title_logos/",
)

_ACCESS_LOCK = threading.RLock()
_ACCESS_PENDING = {}


def _generated_root():
    return os.path.realpath(str(persistent_cache.GENERATED or ""))


def _relative_generated(path):
    try:
        root = _generated_root()
        real = os.path.realpath(str(path or ""))
        if not root or not real or os.path.commonpath((root, real)) != root:
            return ""
        rel = os.path.relpath(real, root)
        return "" if rel.startswith("..") else rel
    except Exception:
        return ""


def canonical_dynamic_details_key(source_path, schema="details-v1"):
    """Stable caller-independent key for one source artwork revision.

    Old callers embedded screen names such as cin536/cin534/bdgrid in the key,
    so the same poster could create several identical 24-file chrome bundles.
    The canonical key depends only on the actual source file revision and the
    builder schema, allowing all screens to share one bundle.
    """
    try:
        real = os.path.realpath(str(source_path or ""))
        if not real or not os.path.isfile(real):
            return ""
        st = os.stat(real)
        mtime_ns = getattr(st, "st_mtime_ns", int(float(st.st_mtime) * 1000000000))
        raw = "%s|%s|%s|%s" % (str(schema), real, int(st.st_size), int(mtime_ns))
        return "canon_%s" % hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]
    except Exception:
        return ""


def canonical_dynamic_rows_key(source_path, selected_rim_only=False, cinematic_premium=False):
    """Stable identity for the two adaptive Settings/episode row PNGs."""
    try:
        real = os.path.realpath(str(source_path or ""))
        if not real or not os.path.isfile(real):
            return ""
        st = os.stat(real)
        mtime_ns = getattr(st, "st_mtime_ns", int(float(st.st_mtime) * 1000000000))
        raw = "rows-v1|%s|%s|%s|%d|%d" % (
            real, int(st.st_size), int(mtime_ns), bool(selected_rim_only), bool(cinematic_premium)
        )
        return "canonrows_%s" % hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]
    except Exception:
        return ""


def note_generated_use(path, now=None):
    """Record an in-memory last-use stamp for a generated asset.

    No disk write happens here, so fast navigation remains write-light.
    """
    if getattr(persistent_cache, "PERFLAB_SESSION_MODE", False):
        # Session assets are never pruned while this Enigma2 process is alive,
        # so a per-file LRU ledger would only consume RAM and create stale work.
        return
    rel = _relative_generated(path)
    if not rel:
        return
    stamp = int(now if now is not None else time.time())
    with _ACCESS_LOCK:
        _ACCESS_PENDING[rel] = stamp
        # Bound memory even if a pathological provider exposes endless names.
        if len(_ACCESS_PENDING) > 120000:
            for key, _value in sorted(_ACCESS_PENDING.items(), key=lambda row: row[1])[:20000]:
                _ACCESS_PENDING.pop(key, None)


def _load_ledger():
    try:
        with open(ACCESS_LEDGER_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data.get("files") if isinstance(data, dict) else data
        if not isinstance(rows, dict):
            return {}
        out = {}
        for rel, stamp in rows.items():
            try:
                rel = str(rel)
                stamp = int(stamp)
            except Exception:
                continue
            if rel and stamp > 0:
                out[rel] = stamp
        return out
    except Exception:
        return {}


def _write_ledger(rows):
    try:
        if not persistent_cache.hdd_read_ready(force=True):
            return False
        parent = os.path.dirname(ACCESS_LEDGER_PATH)
        if not persistent_cache.ensure_persistent_dirs(parent):
            return False
        temp = ACCESS_LEDGER_PATH + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        payload = {"schema": 1, "updated_at": int(time.time()), "files": rows}
        if not persistent_cache.persistent_write_gate(temp):
            return False
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        if not persistent_cache.persistent_write_gate(ACCESS_LEDGER_PATH):
            return False
        os.replace(temp, ACCESS_LEDGER_PATH)
        return True
    except Exception:
        try:
            if os.path.exists(temp) and persistent_cache.persistent_write_gate(temp):
                os.unlink(temp)
        except Exception:
            pass
        return False


def _collect_package_references(root):
    """Return generated files referenced by current Cinematic package JSONs."""
    protected = set()
    try:
        names = os.listdir(root)
    except OSError:
        return protected

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            rel = _relative_generated(value)
            if rel:
                protected.add(rel)

    for name in names:
        if not (name.startswith("cinematic_") and name.endswith("_package.json")):
            continue
        path = os.path.join(root, name)
        try:
            # Package files are tiny. Refuse unexpectedly huge/corrupt files.
            if os.path.getsize(path) > 1024 * 1024:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                visit(json.load(handle))
            protected.add(name)
        except Exception:
            continue
    return protected


def _scan_generated(root):
    rows = []
    for base, _dirs, files in os.walk(root):
        if not persistent_cache.hdd_read_ready(force=True):
            break
        for name in files:
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, root)
            rows.append((rel, path, int(st.st_size), float(st.st_mtime)))
    return rows


def _safe_unlink(path):
    try:
        if not persistent_cache.persistent_write_gate(path):
            return False
        os.unlink(path)
        return True
    except OSError:
        return False


def maintain_generated_cache(max_files=MAX_GENERATED_FILES, max_bytes=MAX_GENERATED_BYTES,
                             target_files=TARGET_GENERATED_FILES, target_bytes=TARGET_GENERATED_BYTES,
                             now=None):
    """Bound and clean only reproducible ``generated`` assets.

    Posters, backdrops, index metadata, library metadata, favourites and user
    configuration are outside this function by construction.
    """
    if getattr(persistent_cache, "PERFLAB_SESSION_MODE", False):
        # Critical PerfLab2 rule: never delete presentation assets while the UI
        # may still hold their paths. The whole directory is session-scoped.
        return {"skipped": "perflab2_current_session_no_runtime_prune"}
    now = int(now if now is not None else time.time())
    result = {
        "before_files": 0, "before_bytes": 0,
        "orphan_removed_files": 0, "orphan_removed_bytes": 0,
        "budget_removed_files": 0, "budget_removed_bytes": 0,
        "after_files": 0, "after_bytes": 0,
        "max_files": int(max_files), "max_bytes": int(max_bytes),
        "target_files": int(target_files), "target_bytes": int(target_bytes),
    }
    if not persistent_cache.hdd_read_ready(force=True):
        result["skipped"] = "hdd_unavailable"
        return result
    root = _generated_root()
    if not root or not os.path.isdir(root):
        result["skipped"] = "generated_missing"
        return result

    with _ACCESS_LOCK:
        pending = dict(_ACCESS_PENDING)
        _ACCESS_PENDING.clear()
    ledger = _load_ledger()
    ledger.update(pending)
    protected = _collect_package_references(root)
    rows = _scan_generated(root)
    result["before_files"] = len(rows)
    result["before_bytes"] = sum(row[2] for row in rows)

    # Pass 1: unquestionable orphans only. No valid current artwork is guessed.
    survivors = []
    for rel, path, size, mtime in rows:
        name = os.path.basename(rel)
        lower = name.lower()
        stale_tmp = (".tmp." in lower or lower.endswith(".tmp")) and (now - int(mtime) > 3600)
        broken_image = lower.endswith((".png", ".jpg", ".jpeg", ".webp")) and size <= 100
        deprecated = name.startswith(_DEPRECATED_PREFIXES)
        if rel not in protected and (stale_tmp or broken_image or deprecated):
            if _safe_unlink(path):
                result["orphan_removed_files"] += 1
                result["orphan_removed_bytes"] += size
                ledger.pop(rel, None)
                continue
        survivors.append((rel, path, size, mtime))

    total_files = len(survivors)
    total_bytes = sum(row[2] for row in survivors)
    over_limit = total_files > int(max_files) or total_bytes > int(max_bytes)

    if over_limit:
        # JSON/metadata is tiny and can be expensive to reconstruct. Budget
        # eviction therefore targets reproducible image assets only.
        candidates = []
        for rel, path, size, mtime in survivors:
            if rel in protected:
                continue
            rel_norm=str(rel or "").replace("\\","/")
            if any(rel_norm.startswith(prefix) for prefix in _BUDGET_PROTECTED_DIRS):
                continue
            if not path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            last_use = max(int(mtime), int(ledger.get(rel, 0) or 0))
            candidates.append((last_use, mtime, rel, path, size))
        candidates.sort(key=lambda row: (row[0], row[1], row[2]))

        # First evict stale/untracked assets. If the hard ceiling is still
        # exceeded, continue oldest-first while keeping package-referenced and
        # recently-used assets protected.
        recent_cutoff = now - RECENT_USE_SECONDS
        stale = [row for row in candidates if row[0] < recent_cutoff]
        warm = [row for row in candidates if row[0] >= recent_cutoff]
        current_session = set(pending)
        for group, allow_warm in ((stale, False), (warm, True)):
            for last_use, _mtime, rel, path, size in group:
                if total_files <= int(target_files) and total_bytes <= int(target_bytes):
                    break
                # Assets touched in the current receiver session are genuinely
                # hot. Package-referenced assets were excluded above. If the
                # hard ceiling is still exceeded after stale eviction, older
                # warm assets from previous sessions may be regenerated later.
                if rel in current_session:
                    continue
                if (not allow_warm) and last_use >= recent_cutoff:
                    continue
                if _safe_unlink(path):
                    total_files -= 1
                    total_bytes -= size
                    ledger.pop(rel, None)
                    result["budget_removed_files"] += 1
                    result["budget_removed_bytes"] += size
            if total_files <= int(target_files) and total_bytes <= int(target_bytes):
                break

        # Current-session and package-referenced files are the final safety
        # boundary. They may temporarily leave the cache a little above target,
        # but unbounded growth across sessions is stopped.

    # Drop ledger entries whose files no longer exist, and bound ledger size.
    existing = set(rel for rel, _path, _size, _mtime in _scan_generated(root))
    ledger = {rel: int(stamp) for rel, stamp in ledger.items() if rel in existing}
    if len(ledger) > ACCESS_LEDGER_MAX:
        ledger = dict(sorted(ledger.items(), key=lambda row: row[1], reverse=True)[:ACCESS_LEDGER_MAX])
    _write_ledger(ledger)

    result["after_files"] = len(existing)
    try:
        result["after_bytes"] = sum(os.path.getsize(os.path.join(root, rel)) for rel in existing)
    except Exception:
        result["after_bytes"] = max(0, result["before_bytes"] - result["orphan_removed_bytes"] - result["budget_removed_bytes"])
    return result
