"""Errors reported by rate limiting operations."""


class RateLimitError(Exception):
    """Base error for rate limiting operations."""


class RateLimitBackendError(RateLimitError):
    """The backend could not decide; the operation must not proceed."""
