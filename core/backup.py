# -*- coding: utf-8 -*-
"""Validated, size-bounded, secret-safe and rollback-safe backup/restore."""
from __future__ import absolute_import

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile

from ..version import PLUGIN_VERSION
from ..storage import (
    CONFIG_DIR, CONFIG_FILE, DISABLED_FILE, SETTINGS_FILE, API_KEYS_FILE,
    STATE_IO_LOCK, _fsync_parent_dir,
)
from ..repositories.database import DB_PATH, DB
from ..log import get_logger

LOG = get_logger()

BACKUP_DIR = "/media/hdd/UltraStalker/Backup"
LEGACY_BACKUP_DIR = os.path.join(CONFIG_DIR, "backups")
BACKUP_PREFIX = "ultrastalker-backup-"
OPTIONAL_FILES = {
    "profiles.json": CONFIG_FILE,
    "disabled_profiles.json": DISABLED_FILE,
    "settings.json": SETTINGS_FILE,
    "recent_searches.json": os.path.join(CONFIG_DIR, "recent_searches.json"),
    "smart_engines.json": os.path.join(CONFIG_DIR, "smart_engines.json"),
    "title_engines.json": os.path.join(CONFIG_DIR, "title_engines.json"),
    "ui_state.json": os.path.join(CONFIG_DIR, "ui_state.json"),
    ".wizard_done": os.path.join(CONFIG_DIR, ".wizard_done"),
    "portals.txt": os.path.join(CONFIG_DIR, "portals.txt"),
    "portal.txt": os.path.join(CONFIG_DIR, "portal.txt"),
    # Keep legacy catalog members recognized so old backups remain readable.
    # These two files are bundled release data, not user state: new backups do
    # not capture them and restore never replaces/deletes the package catalogs.
    "server_catalog_portal.txt": os.path.join(CONFIG_DIR, ".uslib", ".catalog_a"),
    "server_catalog_xtream.txt": os.path.join(CONFIG_DIR, ".uslib", ".catalog_b"),
}
BUNDLED_CATALOG_MEMBERS = frozenset((
    "server_catalog_portal.txt",
    "server_catalog_xtream.txt",
))
SECRET_FILES = {"api_keys.conf": API_KEYS_FILE}
DATABASE_MEMBER = "ultrastalker.db"
MANIFEST_MEMBER = "manifest.json"
BACKUP_AUTH_KEY_FILE = os.path.join(CONFIG_DIR, "backup_auth.key")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 96 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 192 * 1024 * 1024
_SAFE_SECRET_WORDS = ("credential", "api_key", "apikey", "secret", "token", "password", "passwd")
_BACKUP_IO_LOCK = threading.RLock()

_SAFE_EXACT_SECRET_KEYS = {
    "parental_pin", "tmdb_credential", "tmdb_api_key", "tmdb_read_token", "tmdb_api_token",
    "imdb_api_key", "imdb_access_key_id", "imdb_secret_access_key", "imdb_session_token",
}



def _ensure_dirs():
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    try: os.chmod(CONFIG_DIR, 0o700)
    except OSError: pass
    try: os.chmod(BACKUP_DIR, 0o700)
    except OSError: pass



def _backup_auth_key(create=False):
    _ensure_dirs()
    if os.path.isfile(BACKUP_AUTH_KEY_FILE):
        with open(BACKUP_AUTH_KEY_FILE, "rb") as handle:
            key = handle.read(64)
        if len(key) == 32:
            return key
        raise ValueError("Backup authentication key is invalid")
    if not create:
        return None
    key = os.urandom(32)
    temp = "%s.tmp.%d.%d" % (BACKUP_AUTH_KEY_FILE, os.getpid(), threading.get_ident())
    with open(temp, "wb") as handle:
        handle.write(key); handle.flush(); os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, BACKUP_AUTH_KEY_FILE)
    _fsync_parent_dir(BACKUP_AUTH_KEY_FILE)
    return key


def _manifest_auth_payload(manifest):
    clean = dict(manifest or {})
    clean.pop("authentication", None)
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _manifest_hmac(manifest, key):
    return hmac.new(key, _manifest_auth_payload(manifest), hashlib.sha256).hexdigest()


def _safe_name(path):
    return os.path.basename(str(path or ""))


def list_backups():
    _ensure_dirs()
    rows = []
    for root in (BACKUP_DIR, LEGACY_BACKUP_DIR, "/tmp"):
        try: names = os.listdir(root)
        except OSError: continue
        for name in names:
            if not (name.startswith(BACKUP_PREFIX) and name.endswith(".zip")):
                continue
            path = os.path.join(root, name)
            try: rows.append((os.path.getmtime(path), path))
            except OSError: pass
    rows.sort(reverse=True)
    seen, out = set(), []
    for _mtime, path in rows:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real); out.append(path)
    return out


def _database_snapshot(target):
    DB.initialize()
    # sqlite backup() produces a coherent snapshot while normal readers/writers continue.
    src = sqlite3.connect(DB_PATH, timeout=8)
    dst = sqlite3.connect(target, timeout=8)
    try:
        src.execute('PRAGMA busy_timeout=8000')
        src.backup(dst)
        dst.commit()
    finally:
        dst.close(); src.close()


def _safe_settings_value(value):
    if isinstance(value, dict):
        clean = {}
        for key, child in value.items():
            low = str(key).casefold()
            # Safe backups are advertised as excluding API/private transport secrets,
            # not as losing parental/category state. Preserve pinned_categories and
            # the PBKDF2 hash+salt required to keep an enabled parental lock valid.
            # Only the legacy clear-text parental_pin is removed.
            if low in _SAFE_EXACT_SECRET_KEYS:
                continue
            if any(word in low for word in _SAFE_SECRET_WORDS):
                if low not in ("parental_pin_hash", "parental_pin_salt"):
                    continue
            if low.endswith("endpoint") or low.endswith("_url"):
                continue
            clean[key] = _safe_settings_value(child)
        return clean
    if isinstance(value, list):
        return [_safe_settings_value(item) for item in value]
    return value


def _safe_settings_payload(source):
    """Return settings suitable for a backup advertised as containing no secrets."""
    try:
        with open(source, "r", encoding="utf-8", errors="strict") as handle:
            data = json.load(handle)
    except Exception:
        return b"{}\n"
    if not isinstance(data, dict):
        return b"{}\n"
    # A legacy clear-text parental PIN must not leak into a safe archive, but
    # dropping it while parental_lock remains enabled would silently reset the
    # restored lock. Convert it to the same PBKDF2 hash+salt representation used
    # by current settings before redaction.
    if data.get("parental_pin") and not (data.get("parental_pin_hash") and data.get("parental_pin_salt")):
        try:
            from .parental import hash_pin
            migrated = dict(data); migrated.update(hash_pin(str(data.get("parental_pin") or "0000")))
            data = migrated
        except Exception as exc:
            LOG.warning("Safe backup could not hash legacy parental PIN: %s", exc)
    clean = _safe_settings_value(data)
    return (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha256_archive_member(archive, member):
    digest = hashlib.sha256()
    with archive.open(member, "r") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _unique_backup_path(path):
    """Return a non-existing destination so backups never overwrite each other."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    suffix = 1
    while True:
        candidate = "%s-%02d%s" % (root, suffix, ext)
        if not os.path.exists(candidate):
            return candidate
        suffix += 1


def create_backup(include_secrets=True, destination=None, authenticate=False):
    """Create one complete, portable user-state backup on HDD.

    Backups intentionally include every user-entered credential/API value.
    Artwork/poster/backdrop caches are excluded because they already live
    persistently on HDD and can be many gigabytes.

    ``include_secrets`` and ``authenticate`` remain accepted for old callers,
    but Ultra Stalker always creates a complete portable backup.
    """
    with _BACKUP_IO_LOCK:
        _ensure_dirs()
        if destination is None:
            # Never silently fall back to flash for the new full backup. A receiver
            # without /media/hdd should fail clearly rather than fill /etc or /tmp.
            if not os.path.isdir('/media/hdd'):
                raise ValueError("HDD is not available at /media/hdd")
            destination = os.path.join(BACKUP_DIR, BACKUP_PREFIX + time.strftime("%Y%m%d-%H%M%S") + ".zip")
        path = _unique_backup_path(destination)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="spobh-backup-")
        archive_temp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
        db_copy = os.path.join(temp_dir, DATABASE_MEMBER)
        included = []
        try:
            # Capture DB + JSON/text state under the same writer barrier.  The ZIP
            # compression itself happens afterwards, outside the locks, so normal
            # playback/UI writers are paused only for the short snapshot copy.
            staged_members = {}
            with STATE_IO_LOCK:
                with DB.write_guard():
                    _database_snapshot(db_copy)
                    staged_members[DATABASE_MEMBER] = db_copy
                    for member, source in list(OPTIONAL_FILES.items()) + list(SECRET_FILES.items()):
                        if member in BUNDLED_CATALOG_MEMBERS:
                            continue
                        if not os.path.isfile(source):
                            continue
                        staged = os.path.join(temp_dir, member)
                        shutil.copy2(source, staged)
                        staged_members[member] = staged

            manifest = {
                "product": "UltraStalker", "format": 8,
                "plugin_version": PLUGIN_VERSION, "created_at": int(time.time()),
                "contains_secrets": True,
                "backup_kind": "complete-user-state",
                "artwork_included": False,
                "portable": True,
            }
            hashes = {}
            with zipfile.ZipFile(archive_temp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for member in sorted(staged_members):
                    source = staged_members[member]
                    archive.write(source, member)
                    included.append(member)
                    hashes[member] = _sha256_file(source)
                manifest["members"] = sorted(included)
                manifest["sha256"] = {member: hashes[member] for member in sorted(hashes)}
                manifest["integrity_scope"] = "corruption-detection"
                manifest["authenticated"] = False
                archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=2))
            if os.path.getsize(archive_temp) > MAX_ARCHIVE_BYTES:
                raise ValueError("Backup exceeds the maximum archive size")
            # Verify the fully-written temporary archive before publishing it.
            inspect_backup(archive_temp)
            os.chmod(archive_temp, 0o600)
            os.replace(archive_temp, path)
            _fsync_parent_dir(path)
            os.chmod(path, 0o600)
            return {"path": path, "members": sorted(included), "contains_secrets": True}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            try:
                if os.path.exists(archive_temp): os.unlink(archive_temp)
            except OSError: pass

def _validate_json_member(path, member):
    if member == "api_keys.conf" or not member.endswith(".json"):
        return
    with open(path, "r", encoding="utf-8", errors="strict") as handle:
        json.load(handle)


def inspect_backup(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path): raise ValueError("Backup file does not exist")
    if os.path.getsize(path) > MAX_ARCHIVE_BYTES: raise ValueError("Backup archive is too large")
    allowed = set(OPTIONAL_FILES) | set(SECRET_FILES) | {DATABASE_MEMBER, MANIFEST_MEMBER}
    total = 0
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist(); names = [info.filename for info in infos]
        if len(names) != len(set(names)): raise ValueError("Backup contains duplicate members")
        for info in infos:
            name = info.filename
            if name != _safe_name(name) or name not in allowed:
                raise ValueError("Backup contains an unsupported path: %s" % name)
            if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                raise ValueError("Backup member is too large: %s" % name)
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Backup expands beyond the safe size limit")
        if MANIFEST_MEMBER not in names: raise ValueError("Backup manifest is missing")
        manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8", "strict"))
        if manifest.get("product") != "UltraStalker": raise ValueError("This is not a UltraStalker backup")
        backup_format = int(manifest.get("format") or 0)
        if backup_format not in (1, 2, 3, 4, 5, 6, 7, 8): raise ValueError("Unsupported backup format")
        if backup_format >= 8:
            if manifest.get("backup_kind") != "complete-user-state" or not bool(manifest.get("contains_secrets")):
                raise ValueError("Backup is not marked as complete user state")
            if DATABASE_MEMBER not in names:
                raise ValueError("Complete backup database is missing")
        declared = manifest.get("members")
        if isinstance(declared, list):
            actual = sorted(name for name in names if name != MANIFEST_MEMBER)
            if sorted(str(name) for name in declared) != actual:
                raise ValueError("Backup manifest member list does not match archive contents")
        if backup_format >= 6:
            hashes = manifest.get("sha256")
            if not isinstance(hashes, dict):
                raise ValueError("Backup integrity manifest is missing")
            for member in names:
                if member == MANIFEST_MEMBER:
                    continue
                expected = str(hashes.get(member) or "").lower()
                if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
                    raise ValueError("Backup integrity hash is invalid for %s" % member)
                if _sha256_archive_member(archive, member) != expected:
                    raise ValueError("Backup integrity check failed for %s" % member)
        if bool(manifest.get("authenticated")):
            auth = manifest.get("authentication")
            if not isinstance(auth, dict) or auth.get("scheme") != "hmac-sha256":
                raise ValueError("Backup authentication metadata is invalid")
            key = _backup_auth_key(create=False)
            if key is None:
                raise ValueError("Authenticated backup belongs to another receiver or the local authentication key is missing")
            expected = str(auth.get("digest") or "").lower()
            actual = _manifest_hmac(manifest, key)
            if len(expected) != 64 or not hmac.compare_digest(actual, expected):
                raise ValueError("Backup authentication failed")
            manifest["authentication_verified"] = True
        return manifest


def _validate_staged(temp_dir):
    db_source = os.path.join(temp_dir, DATABASE_MEMBER)
    if os.path.isfile(db_source):
        check = sqlite3.connect(db_source)
        try:
            result = check.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok": raise ValueError("Backup database failed integrity check: %s" % result)
        finally: check.close()
    for member in OPTIONAL_FILES:
        source = os.path.join(temp_dir, member)
        if os.path.isfile(source): _validate_json_member(source, member)
    return db_source


def _quiesce_background_runtime():
    """Stop every known background writer before restore commit.

    A restore must fail closed: if a recording/XMLTV worker cannot be stopped,
    state replacement is not allowed to proceed.  The restore task itself may
    be running inside TASKS, so only unrelated handles are cancelled/waited.
    """
    from .tasks import TASKS
    current = TASKS.current_task_id()
    TASKS.cancel_all(exclude_task_id=current)
    if not TASKS.wait_idle(timeout=8.0, exclude_task_id=current):
        raise RuntimeError("background plugin tasks did not quiesce before restore")

    recording_session = None
    try:
        from .recording import stop_recording_manager
        recording_session = stop_recording_manager(wait=True)
        from .bouquets import pause_xmltv_refreshes
        if not pause_xmltv_refreshes(wait=True, timeout=8.0):
            raise RuntimeError("XMLTV workers did not stop before restore")
    except Exception:
        # If recording was already stopped before XMLTV failed to quiesce, put
        # the manager back so a rejected restore does not silently disable it.
        if recording_session is not None:
            try:
                from .recording import start_recording_manager
                start_recording_manager(recording_session)
            except Exception as restart_exc:
                LOG.error("Could not restart recording manager after rejected restore: %s", restart_exc)
        try:
            from .bouquets import resume_xmltv_refreshes
            resume_xmltv_refreshes()
        except Exception as resume_exc:
            LOG.error("Could not resume XMLTV refreshes after rejected restore: %s", resume_exc)
        raise

    try:
        from .session import PortalSession
        PortalSession.invalidate()
    except Exception as exc:
        # Session invalidation is desirable but is not itself a disk writer.
        LOG.warning("Restore session invalidation failed: %s", exc)
    return recording_session

def _resume_background_runtime(recording_session):
    try:
        from .bouquets import resume_xmltv_refreshes
        resume_xmltv_refreshes()
    except Exception as exc:
        LOG.warning("Restore could not resume XMLTV refresh: %s", exc)
    if recording_session is not None:
        try:
            from .recording import start_recording_manager
            start_recording_manager(recording_session)
        except Exception as exc:
            LOG.warning("Restore could not restart recording manager: %s", exc)


def restore_backup(path, restore_secrets=False, cancel_event=None):
    """Restore staged state transactionally while background writers are quiesced.

    Expensive ZIP extraction and validation happen before runtime is paused. Only
    the short commit/rollback phase owns the global state/DB writer locks.
    """
    def check_cancel():
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise RuntimeError("Restore cancelled before commit")

    check_cancel()
    _ensure_dirs()
    manifest = inspect_backup(path)
    if int(manifest.get("format") or 0) >= 8:
        restore_secrets = True
    check_cancel()
    temp_dir = tempfile.mkdtemp(prefix="spobh-restore-")
    rollback_dir = tempfile.mkdtemp(prefix="spobh-rollback-", dir=CONFIG_DIR)
    restored, removed, committed, prepared, existed = [], [], [], [], {}
    recording_session = None
    runtime_quiesced = False
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                check_cancel()
                member = info.filename
                if member == MANIFEST_MEMBER or (member in SECRET_FILES and not restore_secrets):
                    continue
                source = os.path.join(temp_dir, member)
                with archive.open(info, "r") as src, open(source, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)

        check_cancel()
        db_source = _validate_staged(temp_dir)
        check_cancel()
        targets = {member: target for member, target in OPTIONAL_FILES.items()
                   if member not in BUNDLED_CATALOG_MEMBERS}
        if restore_secrets: targets.update(SECRET_FILES)
        if os.path.isfile(db_source): targets[DATABASE_MEMBER] = DB_PATH
        exact_restore = int(manifest.get("format") or 0) >= 8

        for member, target in targets.items():
            check_cancel()
            source = os.path.join(temp_dir, member)
            source_exists = os.path.isfile(source)
            # Format 8 is a complete state snapshot: absence is meaningful.  If a
            # file did not exist when the backup was taken, remove any newer local
            # copy during restore instead of silently mixing old and restored state.
            if not source_exists and not exact_restore:
                continue
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            incoming = None
            if source_exists:
                incoming = target + ".restore"
                shutil.copy2(source, incoming); os.chmod(incoming, 0o600)
            prepared.append((member, target, incoming))

        check_cancel()
        for member, target, _incoming in prepared:
            existed[target] = os.path.exists(target)
            if existed[target]: shutil.copy2(target, os.path.join(rollback_dir, member + ".old"))

        check_cancel()
        recording_session = _quiesce_background_runtime()
        runtime_quiesced = True
        # Cancellation is honored until the transactional commit begins. Once
        # os.replace starts, rollback/completion must run to a deterministic end.
        check_cancel()
        # STATE_IO_LOCK blocks profile/settings writers; DB.write_guard blocks
        # history/cache/favorite writers until the database swap is complete.
        with STATE_IO_LOCK:
            with DB.write_guard():
                commit_failed = False
                try:
                    DB.close()
                    if any(member == DATABASE_MEMBER for member, _target, _incoming in prepared):
                        for suffix in ("-wal", "-shm"):
                            sidecar = DB_PATH + suffix
                            try:
                                os.unlink(sidecar)
                                _fsync_parent_dir(sidecar)
                            except OSError:
                                pass
                    for member, target, incoming in prepared:
                        if incoming is None:
                            if os.path.exists(target):
                                os.unlink(target)
                                _fsync_parent_dir(target)
                                removed.append(member)
                        else:
                            os.replace(incoming, target)
                            _fsync_parent_dir(target)
                        committed.append((member, target)); restored.append(member)
                except Exception as commit_exc:
                    commit_failed = True
                    rollback_errors = []
                    for member, target in reversed(committed):
                        old = os.path.join(rollback_dir, member + ".old")
                        try:
                            if existed.get(target) and os.path.isfile(old):
                                shutil.copy2(old, target + ".rollback")
                                os.replace(target + ".rollback", target)
                                _fsync_parent_dir(target)
                            elif not existed.get(target) and os.path.exists(target):
                                os.unlink(target)
                                _fsync_parent_dir(target)
                        except OSError as rollback_exc:
                            rollback_errors.append("%s: %s" % (member, rollback_exc))
                            LOG.error("Backup restore rollback failed for %s: %s", member, rollback_exc)
                    if rollback_errors:
                        raise RuntimeError(
                            "Restore commit failed and rollback was incomplete (%s). Original error: %s"
                            % ("; ".join(rollback_errors[:4]), commit_exc)
                        ) from commit_exc
                    raise
                finally:
                    try:
                        from ..storage import _invalidate_settings_cache
                        _invalidate_settings_cache()
                    except Exception as exc: LOG.warning("Backup restore could not invalidate settings cache: %s", exc)
                    try:
                        from .cache import CACHE
                        CACHE.clear_memory()
                    except Exception as exc: LOG.warning("Backup restore could not clear memory cache: %s", exc)
                    # DB.close() happens before the first state-file swap.  A
                    # failed os.replace() therefore still needs to reopen/reset
                    # the repository after rollback; otherwise the UI resumes
                    # with a closed/stale database handle.  Preserve the original
                    # commit/rollback exception if reset itself also fails.
                    try:
                        DB.reset_after_restore()
                    except Exception as reset_exc:
                        if commit_failed:
                            LOG.error("Backup restore could not reset database after failed commit: %s", reset_exc)
                        else:
                            raise

        # Dynamic recordings/bouquets are intentionally not backup members: they
        # are derived from a specific live portal runtime and can become stale or
        # dangerous after profiles/state are rolled back. Reconcile by removing
        # old derived integrations after a successful state commit.
        reconciliation = {}
        try:
            from .recording import reset_recording_integrations
            reconciliation["recordings"] = reset_recording_integrations(recording_session, remove_timers=True)
        except Exception as exc:
            reconciliation["recordings_error"] = str(exc)[:240]
            LOG.warning("Restore recording reconciliation failed: %s", exc)
        try:
            from .bouquets import unexport_all_live_integrations
            reconciliation["bouquets"] = unexport_all_live_integrations(reload=True)
        except Exception as exc:
            reconciliation["bouquets_error"] = str(exc)[:240]
            LOG.warning("Restore bouquet reconciliation failed: %s", exc)
        return {"path": path, "restored": sorted(restored), "removed": sorted(removed), "manifest": manifest, "reconciliation": reconciliation}
    finally:
        if runtime_quiesced:
            _resume_background_runtime(recording_session)
        for _member, _target, incoming in prepared:
            if not incoming:continue
            try:
                if os.path.exists(incoming): os.unlink(incoming)
            except OSError: pass
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(rollback_dir, ignore_errors=True)
