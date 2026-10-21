"""Retry logic, circuit breaker, and error handling utilities.

Provides:
- Consistent error response format
- Retry decorator with exponential backoff
- Circuit breaker for external service calls (BSE API, Google Trends)
- Stale-data indicator helpers
"""

import asyncio
import functools
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Error Response Format
# ---------------------------------------------------------------------------


class ErrorResponse:
    """Builds a consistent error response following the API error format.

    Format:
    {
        "error": {
            "code": "ERROR_CODE",
            "message": "Human-readable message",
            "details": {},
            "timestamp": "ISO-8601",
            "request_id": "req_abc123"
        }
    }
    """

    @staticmethod
    def build(
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a structured error response dict."""
        return {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id or "",
            }
        }


# ---------------------------------------------------------------------------
# Retry Decorator with Exponential Backoff
# ---------------------------------------------------------------------------

# Default retry configuration matching design doc
RETRY_CONFIG = {
    "max_retries": 3,
    "backoff_base": 2,
    "backoff_max": 30,
    "retryable_exceptions": (
        ConnectionError,
        TimeoutError,
        OSError,
    ),
}


def retry_with_backoff(
    max_retries: int = RETRY_CONFIG["max_retries"],
    backoff_base: int = RETRY_CONFIG["backoff_base"],
    backoff_max: int = RETRY_CONFIG["backoff_max"],
    retryable_exceptions: tuple[type[BaseException], ...] = RETRY_CONFIG[
        "retryable_exceptions"
    ],
):
    """Decorator that retries a function with exponential backoff.

    Supports both sync and async functions.

    Args:
        max_retries: Maximum number of retry attempts (default 3).
        backoff_base: Base for exponential backoff calculation (default 2).
        backoff_max: Maximum wait time in seconds between retries (default 30).
        retryable_exceptions: Tuple of exception types that trigger a retry.

    Usage:
        @retry_with_backoff()
        async def fetch_stock_price(stock_id: int):
            ...

        @retry_with_backoff(max_retries=5, retryable_exceptions=(TimeoutError,))
        def sync_operation():
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt < max_retries:
                        wait_time = min(
                            backoff_base**attempt, backoff_max
                        )
                        logger.warning(
                            "Retry %d/%d for %s after %.1fs: %s",
                            attempt + 1,
                            max_retries,
                            func.__name__,
                            wait_time,
                            str(exc),
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            "All %d retries exhausted for %s: %s",
                            max_retries,
                            func.__name__,
                            str(exc),
                        )
            raise last_exception  # type: ignore[misc]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt < max_retries:
                        wait_time = min(
                            backoff_base**attempt, backoff_max
                        )
                        logger.warning(
                            "Retry %d/%d for %s after %.1fs: %s",
                            attempt + 1,
                            max_retries,
                            func.__name__,
                            wait_time,
                            str(exc),
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(
                            "All %d retries exhausted for %s: %s",
                            max_retries,
                            func.__name__,
                            str(exc),
                        )
            raise last_exception  # type: ignore[misc]

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitState(Enum):
    """States of the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when the circuit breaker is open and calls are rejected."""

    def __init__(self, service_name: str, recovery_time: float):
        self.service_name = service_name
        self.recovery_time = recovery_time
        super().__init__(
            f"Circuit breaker OPEN for '{service_name}'. "
            f"Recovery in {recovery_time:.1f}s."
        )


class CircuitBreaker:
    """Circuit breaker pattern for external service calls.

    States:
    - CLOSED: Normal operation, requests pass through.
    - OPEN: After `failure_threshold` consecutive failures, reject calls
      immediately for `recovery_timeout` seconds.
    - HALF_OPEN: After timeout, allow 1 test request to determine recovery.

    Args:
        service_name: Identifier for the external service.
        failure_threshold: Number of consecutive failures before opening (default 5).
        recovery_timeout: Seconds to wait before transitioning to half-open (default 60).
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._last_success_time: float | None = None

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, transitioning to half-open if timeout elapsed."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
        return self._state

    @property
    def time_until_recovery(self) -> float:
        """Seconds remaining before the circuit transitions to half-open."""
        if self._state != CircuitState.OPEN or self._last_failure_time is None:
            return 0.0
        elapsed = time.time() - self._last_failure_time
        remaining = self.recovery_timeout - elapsed
        return max(0.0, remaining)

    def record_success(self) -> None:
        """Record a successful call, resetting the circuit to closed."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._last_success_time = time.time()
        logger.info("Circuit breaker '%s': SUCCESS - state CLOSED", self.service_name)

    def record_failure(self) -> None:
        """Record a failed call, potentially opening the circuit."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker '%s': OPEN after %d failures. "
                "Recovery in %.0fs.",
                self.service_name,
                self._failure_count,
                self.recovery_timeout,
            )
        else:
            logger.warning(
                "Circuit breaker '%s': failure %d/%d",
                self.service_name,
                self._failure_count,
                self.failure_threshold,
            )

    def can_execute(self) -> bool:
        """Check whether a call is allowed through the circuit.

        Returns True if closed or half-open. Raises CircuitBreakerError if open.
        """
        current_state = self.state  # triggers half-open transition check
        if current_state == CircuitState.OPEN:
            raise CircuitBreakerError(
                self.service_name, self.time_until_recovery
            )
        return True

    def reset(self) -> None:
        """Force reset the circuit breaker to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = None
        logger.info("Circuit breaker '%s': manually RESET to CLOSED", self.service_name)


def circuit_breaker_call(breaker: CircuitBreaker):
    """Decorator that wraps an async function with circuit breaker logic.

    Usage:
        bse_circuit = CircuitBreaker("bse_api", failure_threshold=5, recovery_timeout=60)

        @circuit_breaker_call(bse_circuit)
        async def fetch_bse_price(stock_code: str):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            breaker.can_execute()  # raises CircuitBreakerError if open
            try:
                result = await func(*args, **kwargs)
                breaker.record_success()
                return result
            except Exception as exc:
                breaker.record_failure()
                raise exc

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Pre-configured Circuit Breakers for External Services
# ---------------------------------------------------------------------------

bse_api_circuit = CircuitBreaker(
    service_name="bse_api",
    failure_threshold=5,
    recovery_timeout=60.0,
)

google_trends_circuit = CircuitBreaker(
    service_name="google_trends",
    failure_threshold=5,
    recovery_timeout=60.0,
)


# ---------------------------------------------------------------------------
# Stale Data Indicator
# ---------------------------------------------------------------------------


class StaleDataIndicator:
    """Helper to build stale-data response fields when external services are unavailable.

    Used when cached/fallback data is served instead of fresh data from
    BSE API or Google Trends.
    """

    @staticmethod
    def build(
        last_updated: datetime | None,
        service_name: str,
        data: Any = None,
    ) -> dict[str, Any]:
        """Build a response with stale-data indicator.

        Args:
            last_updated: Timestamp when data was last successfully fetched.
            service_name: Name of the unavailable service.
            data: The cached/fallback data to include.

        Returns:
            Dict with stale indicator, timestamp, and data.
        """
        return {
            "data": data,
            "is_stale": True,
            "stale_reason": f"{service_name} is currently unavailable",
            "last_updated": last_updated.isoformat() if last_updated else None,
            "message": (
                f"Showing cached data. {service_name} was last available at "
                f"{last_updated.isoformat() if last_updated else 'unknown'}."
            ),
        }
