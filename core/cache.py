# -*- coding: utf-8 -*-
import hashlib
import json
import threading
import time
from collections import OrderedDict
from ..repositories.database import DB


class CachePolicy:
    TTL = {"get_genres": 21600, "get_ordered_list": 1800, "get_short_epg": 600, "get_epg_info": 600, "account_info": 900, "capabilities": 86400}

    @classmethod
    def ttl(cls, action, default=120):
        return int(cls.TTL.get(str(action or ""), default))


class CacheManager:
    def __init__(self, max_items=128):
        self.max_items = max_items
        self._memory = OrderedDict()
        self._lock = threading.RLock()

    def key(self, portal, mac, params):
        raw = json.dumps([str(portal).rstrip('/').lower(), str(mac).upper(), sorted((str(k), str(v)) for k, v in params.items())], separators=(',', ':'), ensure_ascii=True)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def get(self, key):
        now = time.time()
        with self._lock:
            row = self._memory.get(key)
            if row:
                expires, value = row
                if expires >= now:
                    self._memory.move_to_end(key)
                    return value
                self._memory.pop(key, None)
        entry = DB.cache_get_entry(key)
        if entry is not None:
            value, remaining_ttl = entry
            # Never extend a persistent row beyond its original DB expiration.
            memory_ttl = max(1, min(90, int(remaining_ttl or 1)))
            with self._lock:
                self._memory[key] = (now + memory_ttl, value)
                self._memory.move_to_end(key)
                while len(self._memory) > self.max_items:
                    self._memory.popitem(last=False)
            return value
        return None

    def put(self, key, value, ttl):
        ttl = max(1, int(ttl))
        expires = time.time() + ttl
        with self._lock:
            self._memory[key] = (expires, value)
            self._memory.move_to_end(key)
            while len(self._memory) > self.max_items:
                self._memory.popitem(last=False)
        DB.cache_put(key, value, ttl)

    def clear_memory(self):
        with self._lock:
            self._memory.clear()

    def clear(self):
        self.clear_memory()
        DB.clear_cache()


CACHE = CacheManager()
