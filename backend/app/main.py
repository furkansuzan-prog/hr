import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.exc import TimeoutError as SATimeoutError

import app.services.prewarm
from app.api.routers import admin_data, analytics, core
from app.core import metrics
from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging, request_id_var

settings = get_settings()
settings.validate_for_runtime()
configure_logging()
log = logging.getLogger("hrswi.http")

@asynccontextmanager
async def lifespan(_app):
    # B-22: sync endpoints run in AnyIO's thread pool (40 threads by default) but each worker has only
    # pool_size + max_overflow DB connections; cap the threads so requests queue cheaply instead of timing out
    # on the connection pool.
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = int(os.getenv("HRSWI_THREAD_LIMIT", str(engine.pool.size() + engine.pool._max_overflow)))
    yield


app = FastAPI(
    lifespan=lifespan,
    title="HRSWI API",
    version="0.1.0",
    description="HR Sustainability & Workforce Intelligence - modular monolith API. "
    "All analytical numbers are produced by the versioned KPI engine; every endpoint enforces tenant and "
    "legal-entity scope.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Locale"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    metrics.HTTP_IN_PROGRESS.inc()
    try:
        response = await call_next(request)
    except SATimeoutError:
        # DB pool exhausted (B-22): transient overload, not a server bug -> 503 + Retry-After so clients back off
        log.warning("db pool timeout", extra={"path": request.url.path})
        response = JSONResponse({"detail": "Service temporarily overloaded, retry shortly", "request_id": rid},
                                status_code=503, headers={"Retry-After": "2"})
    except Exception:
        log.exception("unhandled error", extra={"path": request.url.path})
        response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    finally:
        metrics.HTTP_IN_PROGRESS.dec()
    elapsed = time.perf_counter() - start
    # route TEMPLATE (set on the shared scope by the router), never the raw path -> bounded label cardinality
    metrics.observe_request(request.method, metrics.route_template(request.scope), response.status_code, elapsed)
    dur = round(elapsed * 1000, 1)
    ctx = getattr(request.state, "ctx", None)
    log.info("request", extra={"path": request.url.path, "status": response.status_code, "duration_ms": dur,
                               "user_id": getattr(ctx, "user_id", None), "tenant_id": getattr(ctx, "tenant_id", None)})
    request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(core.router)
app.include_router(analytics.router)
app.include_router(admin_data.router)


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request) -> Response:
    """Prometheus text exposition. Aggregate, label-bounded counters only (no personal data, no tenant names).
    If HRSWI_METRICS_TOKEN is set, a matching ``Authorization: Bearer <token>`` header is required.
    Counters are per worker process - see docs/OPERATIONS.md (monitoring)."""
    expected = os.getenv("HRSWI_METRICS_TOKEN")
    if expected:
        auth = request.headers.get("Authorization", "")
        supplied = auth[7:] if auth[:7].lower() == "bearer " else ""
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return PlainTextResponse("unauthorized\n", status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE)
