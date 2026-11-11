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
)
from .result import (
    RateLimitResult,
)

__all__ = [
    "Algorithm",
    "RateLimitBackendError",
    "RateLimitConfig",
    "RateLimitError",
    "RateLimitResult",
    "RateRule",
    "Scope",
]
