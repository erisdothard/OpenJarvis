"""Error classification for managed agent execution."""

from __future__ import annotations


class AgentTickError(Exception):
    """Base class for agent tick errors."""

    retryable: bool = False
    needs_human: bool = False


class RetryableError(AgentTickError):
    """Transient error that should be retried with backoff."""

    retryable = True


class FatalError(AgentTickError):
    """Permanent error that requires user intervention."""

    retryable = False


class EscalateError(AgentTickError):
    """Agent is uncertain and needs human input."""

    retryable = False
    needs_human = True


class QuotaExhaustedError(AgentTickError):
    """Model quota exhausted — should advance to next model in fallback chain."""

    retryable = False
    fallback_eligible = True


_RETRYABLE_PATTERNS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "temporary",
    "unavailable",
    "503",
    "429",
    "502",
)

_QUOTA_EXHAUSTED_PATTERNS = (
    "quota",
    "resource_exhausted",
    "resource exhausted",
    "daily limit",
    "exceeded your current",
    "billing",
    "insufficient_quota",
)

_FATAL_PATTERNS = (
    "permission",
    "access denied",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "invalid_api_key",
    "not found",
    "401",
    "403",
)


def classify_error(exc: Exception) -> AgentTickError:
    """Classify an arbitrary exception into a RetryableError or FatalError."""
    if isinstance(exc, AgentTickError):
        return exc

    # EngineConnectionError means the provider is unavailable/unconfigured —
    # advance to next model in the fallback chain, don't retry the same one.
    try:
        from openjarvis.engine._base import EngineConnectionError

        if isinstance(exc, EngineConnectionError):
            return QuotaExhaustedError(str(exc))
    except ImportError:
        pass

    msg = str(exc).lower()

    # Check fatal patterns first (more specific)
    if isinstance(exc, PermissionError):
        return FatalError(str(exc))
    for pattern in _FATAL_PATTERNS:
        if pattern in msg:
            return FatalError(str(exc))

    # Quota exhaustion: 429/rate-limit + quota-specific language
    is_rate_limit = any(
        p in msg for p in ("429", "rate limit", "rate_limit", "too many requests")
    )
    if is_rate_limit and any(p in msg for p in _QUOTA_EXHAUSTED_PATTERNS):
        return QuotaExhaustedError(str(exc))

    # Check retryable patterns
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return RetryableError(str(exc))
    for pattern in _RETRYABLE_PATTERNS:
        if pattern in msg:
            return RetryableError(str(exc))

    # Default: assume retryable (better to retry than to give up)
    return RetryableError(str(exc))


def retry_delay(attempt: int) -> int:
    """Exponential backoff delay in seconds: min(10 * 2^attempt, 300)."""
    return min(10 * (2**attempt), 300)


def suggest_action(error: AgentTickError) -> str:
    """Return a human-readable suggested action for the given error."""
    if isinstance(error, QuotaExhaustedError):
        return "Model quota exhausted across entire fallback chain \u2014 check API quotas or add models to fallback chain"
    msg = str(error).lower()
    if any(p in msg for p in ("rate limit", "rate_limit", "429", "too many requests")):
        return "Rate limited \u2014 agent will auto-retry on next tick"
    if any(p in msg for p in ("timeout", "timed out", "connection", "unavailable")):
        return "Engine not reachable \u2014 check that your inference engine is running"
    if any(p in msg for p in ("401", "403", "permission", "unauthorized", "api key")):
        return "Check API key configuration in Settings"
    if "not found" in msg or "404" in msg:
        return "Model or endpoint not found \u2014 verify model name and engine URL"
    return "Unexpected error \u2014 check the full trace for details"
