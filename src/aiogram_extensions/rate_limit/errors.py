"""Errors reported by rate limiting operations."""

from .result import RateLimitResult


class RateLimitError(Exception):
    """Base error for rate limiting operations."""


class RateLimitBackendError(RateLimitError):
    """The backend could not decide; the operation must not proceed."""


class RateLimitExceededError(RateLimitError):
    """An outgoing request was rejected locally before reaching Telegram."""

    def __init__(self, result: RateLimitResult) -> None:
        self.result = result
        super().__init__(f"Rate limit exceeded; retry after {result.retry_after:g} seconds")
