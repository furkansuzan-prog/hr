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

from app.core.metrics import KPI_CACHE_ERRORS, KPI_CACHE_HITS, KPI_CACHE_MISSES

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
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
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
            KPI_CACHE_ERRORS.inc()
        if raw:
            KPI_CACHE_HITS.inc()
            return json.loads(raw)
        KPI_CACHE_MISSES.inc()
        return None

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
            KPI_CACHE_ERRORS.inc()

    def compute_once(self, key: str, compute, *, lock_ttl: int = 30, wait_s: float = 15.0) -> dict:
        """Dogpile protection (B-21): when a key is cold, only ONE caller (across workers with Redis, across threads
        without) computes it; concurrent callers wait for the cached value. If the holder dies or is slow, waiters
        fall back to computing themselves after `wait_s` - correctness never depends on the lock."""
        hit = self.get(key)
        if hit is not None:
            return hit
        if not self.enabled:
            return compute()
        lock_key = key + ":lock"
        if self._redis:
            try:
                got = bool(self._redis.set(lock_key, "1", nx=True, ex=lock_ttl))
            except Exception:
                got = True  # Redis trouble: just compute
        else:
            with self._locks_guard:
                lk = self._locks.setdefault(key, threading.Lock())
            got = lk.acquire(timeout=wait_s)
            if got:
                try:
                    hit = self.get(key)  # filled while we waited for the thread lock
                    if hit is not None:
                        return hit
                    value = compute()
                    self.set(key, value)
                    return value
                finally:
                    lk.release()
                    with self._locks_guard:
                        self._locks.pop(key, None)
        if got:
            try:
                value = compute()
                self.set(key, value)
                return value
            finally:
                try:
                    self._redis.delete(lock_key)
                except Exception:
                    pass
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            time.sleep(0.05)
            hit = self.get(key)
            if hit is not None:
                return hit
        value = compute()
        self.set(key, value)
        return value

    def clear_local(self) -> None:
        self._local.clear()


cache = Cache()
