"""KPI result cache.

Keys embed the tenant's data_version, so any data change (import commit, back-dated correction, seeding)
invalidates exactly that tenant's results by construction; the TTL is only a safety net.
Uses Redis when HRSWI_REDIS_URL is set (shared across API workers), otherwise a bounded in-process dict.
Cached values are aggregate KPI results for an already-authorised scope - no personal data.
"""
import json
import logging
import os
import threading
import time
from collections import OrderedDict

log = logging.getLogger("hrswi.cache")
TTL = int(os.getenv("HRSWI_KPI_CACHE_TTL", "900"))


class _Local:
    def __init__(self, max_items: int = 20_000):
        self._d: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items

    def get(self, k: str) -> str | None:
        with self._lock:
            v = self._d.get(k)
            if not v:
                return None
            if v[0] < time.time():
                self._d.pop(k, None)
                return None
            self._d.move_to_end(k)
            return v[1]

    def set(self, k: str, v: str, ttl: int) -> None:
        with self._lock:
            self._d[k] = (time.time() + ttl, v)
            self._d.move_to_end(k)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


class Cache:
    def __init__(self) -> None:
        self.enabled = os.getenv("HRSWI_KPI_CACHE", "true").lower() == "true"
        self._local = _Local()
        self._redis = None
        url = os.getenv("HRSWI_REDIS_URL")
        if url:
            try:
                import redis

                self._redis = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
                self._redis.ping()
            except Exception as e:  # cache is an optimisation; never fail requests because of it
                log.warning("redis unavailable, using in-process cache: %s", e)
                self._redis = None

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            raw = self._redis.get(key) if self._redis else self._local.get(key)
        except Exception:
            raw = None
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        raw = json.dumps(value, default=str)
        try:
            if self._redis:
                self._redis.setex(key, TTL, raw)
            else:
                self._local.set(key, raw, TTL)
        except Exception:
            pass

    def clear_local(self) -> None:
        self._local.clear()


cache = Cache()
