"""Exception -> HTTP mapping (Sec. 6). Never leaks the ORS key or a stack trace."""
import logging
import time
from dataclasses import dataclass, field

from django.conf import settings

from .locations import InvalidLocation, LocationNotFound
from .optimizer import InfeasiblePlan
from .routing import NoRouteError, ProviderError, ProviderTimeout

logger = logging.getLogger(__name__)

# Order matters: subclasses before their parents.
ERROR_TABLE = (
    (InvalidLocation, 400, "invalid_location"),
    (LocationNotFound, 404, "location_not_found"),
    (NoRouteError, 422, "no_route"),
    (InfeasiblePlan, 422, "no_feasible_fuel_plan"),
    (ProviderTimeout, 504, "routing_timeout"),
    (ProviderError, 502, "routing_provider_error"),
)


@dataclass
class ErrorInfo:
    status: int
    code: str
    message: str
    calls: int
    extra: dict = field(default_factory=dict)


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _redact(text: str) -> str:
    key = getattr(settings, "ORS_API_KEY", "")
    return text.replace(key, "[redacted]") if key else text


def describe_error(exc: Exception) -> ErrorInfo:
    calls = getattr(exc, "total_external_calls", getattr(exc, "external_calls", 0))
    for cls, status, code in ERROR_TABLE:
        if isinstance(exc, cls):
            extra = {}
            at_mile = getattr(exc, "at_mile", None)
            if at_mile is not None:
                extra["at_mile"] = round(float(at_mile), 1)
            return ErrorInfo(status, code, _redact(str(exc)) or code.replace("_", " "), calls, extra)
    logger.error("Unhandled error in route plan", exc_info=exc)
    return ErrorInfo(500, "internal_error", "Unexpected server error.", calls)


def error_body(info: ErrorInfo, started: float) -> dict:
    return {
        "error": {"code": info.code, "message": info.message, **info.extra},
        "meta": {"external_api_calls": info.calls, "cache_hit": False,
                "processing_ms": elapsed_ms(started)},
    }