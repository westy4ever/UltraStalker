# -*- coding: utf-8 -*-
import atexit
import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from ..core.perf import PERF

DB_PATH = '/etc/enigma2/ultrastalker/ultrastalker.db'
SCHEMA_VERSION = 5
LOG = logging.getLogger('UltraStalker.Database')


class Database:
    ALLOWED_TABLES = {'favorites', 'history'}
    SQLITE_IN_CHUNK = 400

    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        # Every mutation is serialized with backup-restore commit. Reads can
        # continue and reconnect automatically if the database inode changes.
        self._write_lock = threading.RLock()
        self._ready = False
        self._local = threading.local()
        self._generation = 0
        self._progress_cache = {}
        self._last_checkpoint = 0.0

    @contextlib.contextmanager
    def write_guard(self):
        """Serialize all SQLite writers with destructive maintenance/restore."""
        with self._write_lock:
            yield

    def _new_connection(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=8000')
        PERF.increment('sqlite.connections')
        return db

    def connect(self):
        """Return one SQLite connection per worker thread.

        If restore replaces the database file, the inode check transparently
        reopens the connection on the next access.
        """
        db = getattr(self._local, 'db', None)
        identity = getattr(self._local, 'db_identity', None)
        generation = getattr(self._local, 'db_generation', -1)
        if db is not None and generation != self._generation:
            try: db.close()
            except Exception as exc: LOG.debug('database close after generation change failed: %s', exc)
            db = None
            identity = None
        try:
            stat = os.stat(self.path)
            current = (stat.st_dev, stat.st_ino)
        except OSError:
            current = None
        if db is not None and identity is not None and current is not None and identity != current:
            try: db.close()
            except Exception as exc: LOG.debug('database close after inode change failed: %s', exc)
            db = None
        if db is None:
            db = self._new_connection()
            self._local.db = db
            self._local.db_generation = self._generation
            try:
                stat = os.stat(self.path)
                self._local.db_identity = (stat.st_dev, stat.st_ino)
            except OSError:
                self._local.db_identity = None
        return db

    def close(self):
        db = getattr(self._local, 'db', None)
        if db is not None:
            try: db.close()
            except Exception as exc: LOG.debug('database close failed: %s', exc)
        self._local.db = None
        self._local.db_identity = None
        self._local.db_generation = -1

    def checkpoint(self, truncate=False):
        try:
            self.initialize()
            with self._write_lock:
                mode="TRUNCATE" if truncate else "PASSIVE"
                self.connect().execute("PRAGMA wal_checkpoint(%s)" % mode).fetchone()
                self._last_checkpoint=time.monotonic()
            return True
        except Exception as exc:
            LOG.warning('SQLite checkpoint failed: %s', exc)
            return False

    def reset_after_restore(self):
        """Invalidate this thread and schema state after an atomic DB replacement."""
        self.close()
        with self._lock:
            self._generation += 1
            self._ready = False
        self.initialize()

    def __del__(self):
        try: self.close()
        except Exception: pass

    def initialize(self):
        with self._lock:
            if self._ready:
                return
            with self._write_lock:
                if self._ready:
                    return
                with PERF.timer('sqlite.initialize_ms'):
                    with self.connect() as db:
                        db.executescript('''
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS favorites(content_key TEXT PRIMARY KEY,portal TEXT NOT NULL,media_type TEXT NOT NULL,payload TEXT NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS history(content_key TEXT PRIMARY KEY,portal TEXT NOT NULL,media_type TEXT NOT NULL,payload TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,duration INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_history_updated ON history(updated_at DESC);
CREATE TABLE IF NOT EXISTS search_history(term TEXT PRIMARY KEY,normalized TEXT NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cache(cache_key TEXT PRIMARY KEY,payload TEXT NOT NULL,expires_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cache_expiry ON cache(expires_at);
CREATE TABLE IF NOT EXISTS portal_state(portal_key TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at INTEGER NOT NULL);
''')
                        previous = 0
                        try:
                            row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                            previous = int(row['value']) if row and str(row['value']).isdigit() else 0
                        except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
                            LOG.warning('schema version read failed; treating as legacy DB: %s', exc)
                            previous = 0
                        if previous < 4:
                            db.execute("UPDATE history SET position=0,duration=0,completed=0 WHERE media_type IN ('vod','series','episode','catchup')")
                            try:
                                legacy = '/etc/enigma2/ultrastalker/resumepoints.pkl'
                                if os.path.exists(legacy): os.unlink(legacy)
                            except OSError as exc:
                                LOG.debug('legacy resumepoint cleanup failed: %s', exc)
                        db.execute('INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)', (SCHEMA_VERSION, int(time.time())))
                        db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
                        db.execute('DELETE FROM cache WHERE expires_at < ?', (int(time.time()),))
                try: os.chmod(self.path, 0o600)
                except OSError: pass
                self._ready = True

    def list_payloads(self, table, limit=250):
        if table not in self.ALLOWED_TABLES:
            raise ValueError('Unsupported table')
        self.initialize()
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM %s ORDER BY updated_at DESC LIMIT ?' % table, (max(1, min(int(limit), 1000)),)).fetchall()
        out = []
        for row in rows:
            try: out.append(json.loads(row['payload']))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                LOG.debug('invalid JSON payload in %s: %s', table, exc)
        return out

    def toggle_favorite(self, key, portal, media_type, payload):
        self.initialize(); now = int(time.time())
        with self._write_lock:
            with self.connect() as db:
                if db.execute('SELECT 1 FROM favorites WHERE content_key=?', (key,)).fetchone():
                    db.execute('DELETE FROM favorites WHERE content_key=?', (key,)); return False
                db.execute('INSERT INTO favorites(content_key,portal,media_type,payload,updated_at) VALUES(?,?,?,?,?)', (key, portal, media_type, json.dumps(payload, ensure_ascii=False), now)); return True

    def is_favorite(self, key):
        self.initialize(); PERF.increment('sqlite.favorite_single')
        with self.connect() as db:
            return db.execute('SELECT 1 FROM favorites WHERE content_key=?', (key,)).fetchone() is not None

    def content_states(self, keys):
        self.initialize()
        unique = []; seen = set()
        for key in keys or ():
            key = str(key or '')
            if key and key not in seen:
                seen.add(key); unique.append(key)
        if not unique: return {}
        result = {key: {'favorite': False, 'position': 0, 'duration': 0, 'completed': 0} for key in unique}
        started = time.monotonic(); query_count = 0
        with self.connect() as db:
            for offset in range(0, len(unique), self.SQLITE_IN_CHUNK):
                chunk = unique[offset:offset+self.SQLITE_IN_CHUNK]
                placeholders = ','.join('?' for _ in chunk)
                for row in db.execute('SELECT content_key FROM favorites WHERE content_key IN (%s)' % placeholders, tuple(chunk)).fetchall():
                    result[row['content_key']]['favorite'] = True
                query_count += 1
                for row in db.execute('SELECT content_key,position,duration,completed FROM history WHERE content_key IN (%s)' % placeholders, tuple(chunk)).fetchall():
                    state = result[row['content_key']]
                    state['position'] = int(row['position'] or 0); state['duration'] = int(row['duration'] or 0); state['completed'] = int(row['completed'] or 0)
                query_count += 1
        PERF.increment('sqlite.bulk_state_queries', query_count)
        PERF.record('sqlite.bulk_state_ms', (time.monotonic()-started)*1000.0)
        return result

    def add_history(self, key, portal, media_type, payload, position=0, duration=0, completed=False, force=False):
        self.initialize(); position=max(0,int(position or 0)); duration=max(0,int(duration or 0)); completed=bool(completed)
        now_mono=time.monotonic(); now_wall=int(time.time()*1000)
        previous=self._progress_cache.get(str(key))
        if not force and previous:
            ppos,pdur,pcompleted,pts=previous
            if abs(position-ppos) < 15*90000 and duration == pdur and completed == pcompleted and (now_mono-pts) < 30.0:
                return False
        packed=json.dumps(payload, ensure_ascii=False)
        sql="INSERT INTO history(content_key,portal,media_type,payload,position,duration,completed,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(content_key) DO UPDATE SET payload=excluded.payload,position=excluded.position,duration=excluded.duration,completed=excluded.completed,updated_at=excluded.updated_at"
        with self._write_lock:
            with self.connect() as db:
                db.execute(sql,(key,portal,media_type,packed,position,duration,1 if completed else 0,now_wall))
        self._progress_cache[str(key)]=(position,duration,completed,now_mono)
        if now_mono-self._last_checkpoint > 900.0:
            try:self.connect().execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone();self._last_checkpoint=now_mono
            except sqlite3.Error as exc:LOG.debug('periodic WAL checkpoint failed: %s', exc)
        return True

    def touch_history(self, key, portal, media_type, payload):
        self.initialize(); now = int(time.time()*1000); packed = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            with self.connect() as db:
                row = db.execute('SELECT 1 FROM history WHERE content_key=?', (key,)).fetchone()
                if row: db.execute('UPDATE history SET portal=?,media_type=?,payload=?,updated_at=? WHERE content_key=?', (portal, media_type, packed, now, key))
                else: db.execute('INSERT INTO history(content_key,portal,media_type,payload,position,duration,completed,updated_at) VALUES(?,?,?,?,0,0,0,?)', (key, portal, media_type, packed, now))

    def progress(self, key):
        self.initialize(); PERF.increment('sqlite.progress_single')
        with self.connect() as db: row = db.execute('SELECT position,duration,completed FROM history WHERE content_key=?', (key,)).fetchone()
        return dict(row) if row else {'position': 0, 'duration': 0, 'completed': 0}

    def list_history(self, limit=500, continue_only=False, include_completed=True):
        self.initialize(); limit = max(1, min(int(limit or 500), 5000)); where = []
        if continue_only: where.extend(('position >= ?', 'completed=0'))
        elif not include_completed: where.append('completed=0')
        sql = 'SELECT content_key,portal,media_type,payload,position,duration,completed,updated_at FROM history'; params = []
        if continue_only: params.append(45*90000)
        if where: sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY updated_at DESC, rowid DESC LIMIT ?'; params.append(limit)
        with self.connect() as db: rows = db.execute(sql, tuple(params)).fetchall()
        out = []
        for row in rows:
            try: payload = json.loads(row['payload'])
            except Exception: continue
            if not isinstance(payload, dict): continue
            payload = dict(payload); payload['_content_key'] = row['content_key']; payload['_position'] = int(row['position'] or 0); payload['_duration'] = int(row['duration'] or 0); payload['_completed'] = bool(row['completed']); payload['_updated_at'] = int(row['updated_at'] or 0)
            out.append(payload)
        return out

    def set_history_completed(self, key, completed=True):
        self.initialize()
        with self._write_lock:
            with self.connect() as db:
                if completed: db.execute('UPDATE history SET completed=1,position=CASE WHEN duration>0 THEN duration ELSE position END,updated_at=? WHERE content_key=?', (int(time.time()*1000), key))
                else: db.execute('UPDATE history SET completed=0,position=0,updated_at=? WHERE content_key=?', (int(time.time()*1000), key))

    def remove_history(self, key):
        self.initialize()
        with self._write_lock:
            with self.connect() as db: db.execute('DELETE FROM history WHERE content_key=?', (key,))

    def clear_history(self, portal=None, mac=None):
        self.initialize()
        with self._write_lock:
            with self.connect() as db:
                if portal and mac:
                    rows = db.execute("SELECT content_key,payload FROM history WHERE lower(rtrim(portal,'/'))=?", (str(portal).rstrip('/').lower(),)).fetchall()
                    for row in rows:
                        try: payload = json.loads(row['payload'])
                        except Exception: continue
                        if str((payload or {}).get('mac') or '').upper() == str(mac).upper(): db.execute('DELETE FROM history WHERE content_key=?', (row['content_key'],))
                elif portal: db.execute("DELETE FROM history WHERE lower(rtrim(portal,'/'))=?", (str(portal).rstrip('/').lower(),))
                else: db.execute('DELETE FROM history')

    def cache_get_entry(self, key):
        """Return (payload, remaining_ttl_seconds) without extending DB expiry."""
        self.initialize(); now = int(time.time())
        with self.connect() as db:
            row = db.execute('SELECT payload,expires_at FROM cache WHERE cache_key=?', (key,)).fetchone()
        if not row: return None
        expires_at = int(row['expires_at'] or 0)
        if expires_at < now:
            with self._write_lock:
                with self.connect() as db: db.execute('DELETE FROM cache WHERE cache_key=?', (key,))
            return None
        try: value = json.loads(row['payload'])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            LOG.debug('invalid cache JSON for key %s: %s', str(key)[:96], exc)
            return None
        return value, max(1, expires_at - now)

    def cache_get(self, key):
        entry = self.cache_get_entry(key)
        return entry[0] if entry is not None else None

    def cache_put(self, key, payload, ttl):
        self.initialize(); now = int(time.time())
        try: packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        except (TypeError, ValueError): return
        with self._write_lock:
            with self.connect() as db: db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)', (key, packed, now+max(1, int(ttl)), now))

    def clear_cache(self):
        self.initialize()
        with self._write_lock:
            with self.connect() as db: db.execute('DELETE FROM cache')

    def state_get(self, key):
        self.initialize()
        with self.connect() as db: row = db.execute('SELECT payload FROM portal_state WHERE portal_key=?', (key,)).fetchone()
        if not row: return {}
        try: return json.loads(row['payload'])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            LOG.debug('invalid portal_state JSON for key %s: %s', str(key)[:96], exc)
            return {}

    def state_put(self, key, payload):
        self.initialize()
        with self._write_lock:
            with self.connect() as db: db.execute('INSERT OR REPLACE INTO portal_state VALUES(?,?,?)', (key, json.dumps(payload, ensure_ascii=False), int(time.time())))

    def purge_portal(self, profile):
        self.initialize(); portal = str((profile or {}).get('portal') or '').rstrip('/').lower(); mac = str((profile or {}).get('mac') or '').upper(); counts = {'favorites': 0, 'history': 0, 'portal_state': 0}
        if not portal or not mac: return counts
        with self._write_lock:
            with self.connect() as db:
                for table in ('favorites', 'history'):
                    rows = db.execute("SELECT content_key,payload FROM %s WHERE lower(rtrim(portal,'/'))=?" % table, (portal,)).fetchall()
                    for row in rows:
                        try: payload = json.loads(row['payload'])
                        except Exception: continue
                        if str((payload or {}).get('mac') or '').upper() == mac:
                            db.execute('DELETE FROM %s WHERE content_key=?' % table, (row['content_key'],)); counts[table] += 1
                rows = db.execute('SELECT portal_key,payload FROM portal_state').fetchall()
                for row in rows:
                    try: payload = json.loads(row['payload'])
                    except Exception: continue
                    if str((payload or {}).get('portal') or '').rstrip('/').lower() == portal and str((payload or {}).get('mac') or '').upper() == mac:
                        db.execute('DELETE FROM portal_state WHERE portal_key=?', (row['portal_key'],)); counts['portal_state'] += 1
        return counts

    def health(self):
        try:
            self.initialize()
            with self.connect() as db:
                integrity = db.execute('PRAGMA quick_check').fetchone()[0]
                counts = {t: db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('favorites', 'history', 'cache')}
                migrations = [int(row[0]) for row in db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()]
            try: db_bytes = int(os.path.getsize(self.path))
            except OSError: db_bytes = 0
            return {'ok': integrity == 'ok', 'integrity': integrity, 'schema_version': SCHEMA_VERSION, 'applied_migrations': migrations, 'counts': counts, 'bytes': db_bytes}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}


DB = Database()
atexit.register(DB.close)
