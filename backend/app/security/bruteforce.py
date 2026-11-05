"""Brute-force protection for local password login (SECURITY_PRIVACY G6).

Two independent controls:

1. Per-account lockout - ``users.failed_login_count`` / ``users.locked_until``. After
   ``lockout_threshold`` consecutive wrong passwords the account is locked for ``lockout_minutes``.
   A successful login resets the counter. While locked, every attempt (even with the right password)
   gets the SAME 401 "Invalid credentials" response as a wrong password, so an attacker cannot use the
   lock to learn whether an e-mail address exists. The lock is not extended by attempts made while locked
   (bounds the denial-of-service an attacker can cause against a known account).

2. Per-IP sliding-window limiter - counts FAILED login attempts per client IP. When the window holds
   ``ip_max_failures`` failures, further attempts from that IP get 429 with ``Retry-After`` until the oldest
   failure leaves the window. Successful logins are not counted so a shared office NAT is not locked out by
   normal use, and are not able to reset the window either. Redis (``HRSWI_REDIS_URL``) is used when
   reachable so the limit holds across workers; otherwise an in-process window is used (per worker).

All knobs come from environment variables and are read at call time (tests monkeypatch ``settings()``).
The client IP is ``request.client.host``; behind a reverse proxy run uvicorn with ``--proxy-headers`` and
``--forwarded-allow-ips`` set to the proxy, otherwise every request appears to come from the proxy.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger("hrswi.security")


@dataclass(frozen=True)
class BruteForceSettings:
    enabled: bool = True
    lockout_threshold: int = 5
    lockout_minutes: int = 15
    ip_max_failures: int = 20
    ip_window_seconds: int = 300


def settings() -> BruteForceSettings:
    return BruteForceSettings(
        enabled=os.getenv("HRSWI_LOGIN_PROTECTION_ENABLED", "true").lower() == "true",
        lockout_threshold=int(os.getenv("HRSWI_LOGIN_LOCKOUT_THRESHOLD", "5")),
        lockout_minutes=int(os.getenv("HRSWI_LOGIN_LOCKOUT_MINUTES", "15")),
        ip_max_failures=int(os.getenv("HRSWI_LOGIN_IP_MAX_FAILURES", "20")),
        ip_window_seconds=int(os.getenv("HRSWI_LOGIN_IP_WINDOW_SECONDS", "300")),
    )


def now() -> datetime:
    """Wall clock used for lockout decisions; tests monkeypatch this to simulate time passing."""
    return datetime.now(UTC)


def _epoch() -> float:
    return now().timestamp()


class _LocalWindow:
    """Thread-safe in-process sliding window. Memory is bounded by evicting the least recently used keys."""

    def __init__(self, max_keys: int = 50_000) -> None:
        self._d: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_keys

    def _prune(self, key: str, t: float, window: int) -> deque[float]:
        q = self._d.get(key)
        if q is None:
            return deque()
        while q and q[0] <= t - window:
            q.popleft()
        if not q:
            del self._d[key]
        return q

    def retry_after(self, key: str, limit: int, window: int) -> int | None:
        t = _epoch()
        with self._lock:
            q = self._prune(key, t, window)
            if len(q) >= limit:
                return max(1, math.ceil(q[0] + window - t))
        return None

    def hit(self, key: str, window: int) -> None:
        t = _epoch()
        with self._lock:
            self._prune(key, t, window)
            q = self._d.setdefault(key, deque())
            q.append(t)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


class LoginRateLimiter:
    PREFIX = "hrswi:login-fail:ip:"

    def __init__(self) -> None:
        self._local = _LocalWindow()
        self._redis = None
        self._redis_checked = False

    def _r(self):
        if not self._redis_checked:
            self._redis_checked = True
            url = os.getenv("HRSWI_REDIS_URL")
            if url:
                try:
                    import redis

                    self._redis = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
                    self._redis.ping()
                except Exception as e:  # degrade to per-process limiting, never fail logins because of Redis
                    log.warning("redis unavailable for login rate limiting, using in-process window: %s", e)
                    self._redis = None
        return self._redis

    def retry_after(self, ip: str | None) -> int | None:
        """Seconds the caller must wait, or None when another attempt is allowed."""
        s = settings()
        if not s.enabled or not ip:
            return None
        r = self._r()
        if r is not None:
            try:
                key, t = self.PREFIX + ip, _epoch()
                p = r.pipeline()
                p.zremrangebyscore(key, 0, t - s.ip_window_seconds)
                p.zcard(key)
                p.zrange(key, 0, 0, withscores=True)
                _, count, oldest = p.execute()
                if count >= s.ip_max_failures:
                    first = oldest[0][1] if oldest else t
                    return max(1, math.ceil(first + s.ip_window_seconds - t))
                return None
            except Exception as e:
                log.warning("redis login limiter error, falling back to in-process: %s", e)
        return self._local.retry_after(ip, s.ip_max_failures, s.ip_window_seconds)

    def record_failure(self, ip: str | None) -> None:
        s = settings()
        if not s.enabled or not ip:
            return
        r = self._r()
        if r is not None:
            try:
                key, t = self.PREFIX + ip, _epoch()
                p = r.pipeline()
                p.zadd(key, {f"{t}:{uuid.uuid4().hex}": t})
                p.expire(key, s.ip_window_seconds + 1)
                p.execute()
                return
            except Exception as e:
                log.warning("redis login limiter error, falling back to in-process: %s", e)
        self._local.hit(ip, s.ip_window_seconds)

    def reset(self) -> None:
        """Clear all in-process state (tests / operator tooling). Redis keys expire on their own."""
        self._local.clear()


login_limiter = LoginRateLimiter()


def is_locked(locked_until: datetime | None, at: datetime | None = None) -> bool:
    return locked_until is not None and locked_until > (at or now())
