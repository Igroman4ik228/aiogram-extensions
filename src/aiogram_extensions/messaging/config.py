"""Queue and retry settings."""

from dataclasses import dataclass, field
from typing import Literal

from aiogram_extensions._validation import (
    ConfigurationError,
    positive_integer,
    require_instance,
    seconds,
)


@dataclass(frozen=True, kw_only=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    retry_unknown: bool = False

    def __post_init__(self) -> None:
        positive_integer("max_attempts", self.max_attempts)

        seconds("base_delay", self.base_delay)
        seconds("max_delay", self.max_delay)

        if self.max_delay < self.base_delay:
            raise ConfigurationError("max_delay", "must be at least base_delay")

        if type(self.retry_unknown) is not bool:
            raise ConfigurationError("retry_unknown", "must be a boolean")


@dataclass(frozen=True, kw_only=True)
class QueueConfig:
    workers: int = 1
    maxsize: int = 1000
    overflow: Literal["wait", "reject"] = "wait"
    enqueue_timeout: float | None = None
    drain_timeout: float = 30.0
    max_payload_bytes: int = 262144
    idempotency_ttl: float = 86400.0
    terminal_ttl: float = 86400.0
    retry: RetryConfig = field(default_factory=RetryConfig)

    def __post_init__(self) -> None:
        positive_integer("workers", self.workers)
        positive_integer("maxsize", self.maxsize)
        positive_integer("max_payload_bytes", self.max_payload_bytes)

        seconds("drain_timeout", self.drain_timeout)
        seconds("idempotency_ttl", self.idempotency_ttl)
        seconds("terminal_ttl", self.terminal_ttl)

        if self.enqueue_timeout is not None:
            seconds("enqueue_timeout", self.enqueue_timeout)

        if self.overflow not in ("wait", "reject"):
            raise ConfigurationError("overflow", "must be wait or reject")

        require_instance("retry", self.retry, RetryConfig)
