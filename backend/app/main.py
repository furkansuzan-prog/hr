import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import analytics, core
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var

settings = get_settings()
settings.validate_for_runtime()
configure_logging()
log = logging.getLogger("hrswi.http")

app = FastAPI(
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
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error", extra={"path": request.url.path})
        response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    dur = round((time.perf_counter() - start) * 1000, 1)
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
