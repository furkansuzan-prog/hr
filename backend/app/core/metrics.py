"""Minimal Prometheus metrics (text exposition format 0.0.4) without third-party dependencies.

Exposed by GET /metrics (see app.main). Design rules:
- Labels are LOW-CARDINALITY only: HTTP method, the route TEMPLATE (``/api/v1/kpi/evaluate/{code}``, never the raw
  path with ids) and the status code. Unmatched paths are folded into ``route="<unmatched>"``.
- No personal data and no tenant names/ids are ever used as label values.
- Thread-safe (one lock per metric family); cheap enough to update on every request.

Multi-process caveat: counters live in the memory of ONE worker process. With ``uvicorn --workers N`` each scrape
hits whichever worker accepted the connection, so values jump between workers. Options (docs/OPERATIONS.md):
scrape each worker separately (one port per worker / sidecar), run one worker per container and scale containers
(recommended on Kubernetes), or switch to ``prometheus_client`` multiprocess mode. ``process_start_time_seconds``
and the ``worker`` pid label on ``hrswi_worker_info`` make worker restarts / switches visible.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Iterable, Sequence

DEFAULT_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
UNMATCHED_ROUTE = "<unmatched>"


def _escape_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt_labels(names: Sequence[str], values: Sequence[str], extra: Iterable[tuple[str, str]] = ()) -> str:
    pairs = [*zip(names, values, strict=True), *extra]
    if not pairs:
        return ""
    return "{" + ",".join(f'{k}="{_escape_label(str(v))}"' for k, v in pairs) + "}"


def _fmt_value(v: float) -> str:
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


class Counter:
    def __init__(self, name: str, help_: str, labelnames: Sequence[str] = ()) -> None:
        self.name, self.help, self.labelnames = name, help_, tuple(labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters can only increase")
        key = tuple(str(labels[n]) for n in self.labelnames)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        key = tuple(str(labels[n]) for n in self.labelnames)
        with self._lock:
            return self._values.get(key, 0.0)

    def collect(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        if not items and not self.labelnames:
            items = [((), 0.0)]
        out += [f"{self.name}{_fmt_labels(self.labelnames, k)} {_fmt_value(v)}" for k, v in items]
        return out

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


class Gauge:
    def __init__(self, name: str, help_: str, labelnames: Sequence[str] = ()) -> None:
        self.name, self.help, self.labelnames = name, help_, tuple(labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        key = tuple(str(labels[n]) for n in self.labelnames)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(str(labels[n]) for n in self.labelnames)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def collect(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        out += [f"{self.name}{_fmt_labels(self.labelnames, k)} {_fmt_value(v)}" for k, v in items]
        return out


class Histogram:
    def __init__(self, name: str, help_: str, labelnames: Sequence[str] = (),
                 buckets: Sequence[float] = DEFAULT_BUCKETS) -> None:
        self.name, self.help, self.labelnames = name, help_, tuple(labelnames)
        self.buckets = tuple(sorted(buckets))
        # per label set: [bucket counts (non-cumulative)..., +Inf count], sum
        self._data: dict[tuple[str, ...], tuple[list[int], list[float]]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(str(labels[n]) for n in self.labelnames)
        idx = len(self.buckets)
        for i, b in enumerate(self.buckets):
            if value <= b:
                idx = i
                break
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                entry = ([0] * (len(self.buckets) + 1), [0.0])
                self._data[key] = entry
            entry[0][idx] += 1
            entry[1][0] += value

    def count(self, **labels: str) -> int:
        key = tuple(str(labels[n]) for n in self.labelnames)
        with self._lock:
            entry = self._data.get(key)
            return sum(entry[0]) if entry else 0

    def collect(self) -> list[str]:
        with self._lock:
            items = sorted((k, (list(c), s[0])) for k, (c, s) in self._data.items())
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for key, (counts, total) in items:
            cum = 0
            for b, c in zip(self.buckets, counts, strict=False):  # counts has one extra (+Inf) slot
                cum += c
                out.append(f"{self.name}_bucket{_fmt_labels(self.labelnames, key, [('le', _fmt_value(b))])} {cum}")
            cum += counts[-1]
            out.append(f"{self.name}_bucket{_fmt_labels(self.labelnames, key, [('le', '+Inf')])} {cum}")
            out.append(f"{self.name}_sum{_fmt_labels(self.labelnames, key)} {_fmt_value(total)}")
            out.append(f"{self.name}_count{_fmt_labels(self.labelnames, key)} {cum}")
        return out

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


class Registry:
    def __init__(self) -> None:
        self._metrics: list[Counter | Gauge | Histogram] = []

    def register(self, m):
        self._metrics.append(m)
        return m

    def render(self) -> str:
        lines: list[str] = []
        for m in self._metrics:
            lines.extend(m.collect())
        return "\n".join(lines) + "\n"


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
REGISTRY = Registry()

HTTP_REQUESTS = REGISTRY.register(Counter(
    "http_requests_total", "HTTP requests by method, route template and status code.", ("method", "route", "status")))
HTTP_DURATION = REGISTRY.register(Histogram(
    "http_request_duration_seconds", "HTTP request latency (until response headers) by method and route template.",
    ("method", "route")))
HTTP_IN_PROGRESS = REGISTRY.register(Gauge(
    "http_requests_in_progress", "HTTP requests currently being processed by this worker."))
KPI_CACHE_HITS = REGISTRY.register(Counter("kpi_cache_hits_total", "KPI result cache hits."))
KPI_CACHE_MISSES = REGISTRY.register(Counter("kpi_cache_misses_total", "KPI result cache misses."))
KPI_CACHE_ERRORS = REGISTRY.register(Counter(
    "kpi_cache_errors_total", "KPI cache backend errors (request fell back to computing)."))
PROCESS_START = REGISTRY.register(Gauge(
    "process_start_time_seconds", "Start time of the process since unix epoch in seconds."))
WORKER_INFO = REGISTRY.register(Gauge("hrswi_worker_info", "Constant 1; identifies the worker process.", ("pid",)))

PROCESS_START.set(time.time())
HTTP_IN_PROGRESS.set(0)
WORKER_INFO.set(1, pid=str(os.getpid()))


def normalise_method(method: str) -> str:
    m = (method or "").upper()
    return m if m in ALLOWED_METHODS else "OTHER"


def route_template(scope: dict) -> str:
    """Route template of the matched route, never the raw path (ids would explode label cardinality)."""
    route = scope.get("route")
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    return path if isinstance(path, str) and path else UNMATCHED_ROUTE


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    m = normalise_method(method)
    HTTP_REQUESTS.inc(method=m, route=route, status=str(status))
    HTTP_DURATION.observe(seconds, method=m, route=route)


def render() -> str:
    return REGISTRY.render()
