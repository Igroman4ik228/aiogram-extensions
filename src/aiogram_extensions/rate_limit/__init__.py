"""Backend-independent rate limiting contracts."""

from dataclasses import dataclass
from typing import Literal

from aiogram_extensions._validation import (
    ConfigurationError,
    identifier,
    positive_integer,
    require_instance,
    seconds,
)

Scope = Literal["bot", "chat", "group", "method", "custom", "user_chat"]
Algorithm = Literal["fixed_window", "sliding_window"]

__all__ = [
    "Algorithm",
    "RateLimitBackendError",
    "RateLimitConfig",
    "RateLimitError",
    "RateLimitResult",
    "RateRule",
    "Scope",
]


@dataclass(frozen=True, kw_only=True)
class RateRule:
    name: str
    limit: int
    window: float
    scope: Scope = "user_chat"
    algorithm: Algorithm = "fixed_window"

    def __post_init__(self) -> None:
        identifier("name", self.name)
        positive_integer("limit", self.limit)
        seconds("window", self.window)
        if self.scope not in ("bot", "chat", "group", "method", "custom", "user_chat"):
            raise ConfigurationError("scope", "is not supported")
        if self.algorithm not in ("fixed_window", "sliding_window"):
            raise ConfigurationError("algorithm", "is not supported")


@dataclass(frozen=True, kw_only=True)
class RateLimitConfig:
    rules: tuple[RateRule, ...]
    on_limit: Literal["reject", "wait"] = "reject"

    def __post_init__(self) -> None:
        require_instance("rules", self.rules, tuple)
        if not self.rules:
            raise ConfigurationError("rules", "must be non-empty")
        for rule in self.rules:
            require_instance("rules", rule, RateRule)
        if len({rule.name for rule in self.rules}) != len(self.rules):
            raise ConfigurationError("rules", "must have unique names")
        if self.on_limit not in ("reject", "wait"):
            raise ConfigurationError("on_limit", "must be reject or wait")


class RateLimitError(Exception):
    """Base error for rate limiting operations."""


class RateLimitBackendError(RateLimitError):
    """The backend could not decide; the operation must not proceed."""


@dataclass(frozen=True, kw_only=True)
class RateLimitResult:
    allowed: bool
    retry_after: float = 0.0
    triggered_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        seconds("retry_after", self.retry_after, allow_zero=True)
        if type(self.allowed) is not bool:
            raise ConfigurationError("allowed", "must be a boolean")
        require_instance("triggered_rules", self.triggered_rules, tuple)
        for name in self.triggered_rules:
            identifier("triggered_rules", name)
        if self.allowed and (self.retry_after or self.triggered_rules):
            raise ConfigurationError("allowed", "results cannot contain blocking rules or a wait")
        if not self.allowed and (not self.triggered_rules or self.retry_after == 0):
            raise ConfigurationError("denied", "results require blocking rules and a positive wait")
