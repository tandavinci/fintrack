"""FastAPI application entry point with global error handling and request ID middleware."""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.cache import close_redis
from backend.app.config import settings
from backend.app.routers import advisor, alerts, auth, backtest, dashboard, deep_memory, deep_progress, education, fusion, insights, model_status, portfolio, quant_signal, reports, signal_alerts, stocks, websocket
from backend.app.utils.retry import CircuitBreakerError, ErrorResponse

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="BSE Stock Investment Tracker - Real-time stock tracking with Google Trends integration",
)


# ---------------------------------------------------------------------------
# Request ID Middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that generates a unique request_id for each incoming request.

    The request_id is attached to request.state and included in the
    X-Request-ID response header for tracing.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# Add request ID middleware first (outermost)
app.add_middleware(RequestIDMiddleware)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Global Exception Handlers
# ---------------------------------------------------------------------------


def _get_request_id(request: Request) -> str:
    """Safely extract request_id from request state."""
    return getattr(request.state, "request_id", "")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors with consistent error format."""
    errors = exc.errors()
    field_errors = {}
    for error in errors:
        loc = ".".join(str(part) for part in error.get("loc", []))
        field_errors[loc] = error.get("msg", "Validation error")

    body = ErrorResponse.build(
        code="VALIDATION_ERROR",
        message="Request validation failed.",
        details={"fields": field_errors},
        request_id=_get_request_id(request),
    )
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(CircuitBreakerError)
async def circuit_breaker_exception_handler(
    request: Request, exc: CircuitBreakerError
) -> JSONResponse:
    """Handle circuit breaker open errors (external service unavailable)."""
    body = ErrorResponse.build(
        code="SERVICE_UNAVAILABLE",
        message=f"External service '{exc.service_name}' is temporarily unavailable.",
        details={
            "service": exc.service_name,
            "recovery_in_seconds": round(exc.recovery_time, 1),
        },
        request_id=_get_request_id(request),
    )
    return JSONResponse(status_code=503, content=body)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler for unhandled exceptions.

    Returns a sanitized 500 error in the consistent format.
    Logs the full traceback server-side for debugging.
    """
    import logging
    import traceback

    logger = logging.getLogger(__name__)
    logger.error(
        "Unhandled exception for request %s %s: %s\n%s",
        request.method,
        request.url.path,
        str(exc),
        traceback.format_exc(),
    )

    body = ErrorResponse.build(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred. Please try again later.",
        details={},
        request_id=_get_request_id(request),
    )
    return JSONResponse(status_code=500, content=body)


# ---------------------------------------------------------------------------
# Lifecycle Events
# ---------------------------------------------------------------------------


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up Redis connection on application shutdown."""
    await close_redis()


# ---------------------------------------------------------------------------
# Router Registration
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(stocks.router)
app.include_router(portfolio.router)
app.include_router(alerts.router)
app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(insights.router)
app.include_router(education.router)
app.include_router(websocket.router)
app.include_router(quant_signal.router)  # G3: read-only live quant signal (no DB)
app.include_router(deep_progress.router)  # G4: read-only SSE deep progress (never runs the deep lane)
app.include_router(fusion.router)  # G5: read-only recommendation fusion view (reads stored recs only)
app.include_router(signal_alerts.router)  # G5: read-only system-generated alerts (AI/RISK/NEWS), distinct from /api/v1/alerts
app.include_router(model_status.router)  # G5: read-only Model Status (gateway call-log summary + Ollama liveness)
app.include_router(deep_memory.router)  # G5: read-only Agent Debate viewer (reads deep_memory table only; never runs the deep lane)
app.include_router(backtest.router)  # G5: read-only Backtest Lab (deterministic rule-based engine, offloaded to a worker thread)
app.include_router(advisor.router)  # G5: auth'd grounded advisor chat (quick/chat path via gateway only; NEVER the deep lane)


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": settings.app_name}
