"""Backend-independent rate limiting contracts."""

from .config import (
    Algorithm,
    RateLimitConfig,
    RateRule,
    Scope,
)
from .errors import (
    RateLimitBackendError,
    RateLimitError,
    RateLimitExceededError,
)
from .limiter import RateLimitContext, RateLimiter
from .memory import MemoryRateLimitBackend
from .redis import RedisRateLimitBackend
from .result import (
    RateLimitResult,
)

__all__ = [
    "Algorithm",
    "MemoryRateLimitBackend",
    "RateLimitBackendError",
    "RateLimitConfig",
    "RateLimitContext",
    "RateLimitError",
    "RateLimitExceededError",
    "RateLimitResult",
    "RateLimiter",
    "RateRule",
    "RedisRateLimitBackend",
    "Scope",
]
