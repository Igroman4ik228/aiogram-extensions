"""Retry decisions for Telegram delivery."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, TypeAlias

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from aiogram_extensions._validation import seconds
from aiogram_extensions.rate_limit import RateLimitExceededError

from .config import RetryConfig

logger = logging.getLogger(__name__)

RetryPolicy: TypeAlias = Callable[[Exception, int], float | None]
FailureReason: TypeAlias = Literal[
    "transient",
    "permanent",
    "unknown",
    "rate_limited",
    "retry_policy_error",
    "drain_timeout",
    "cancelled",
    "worker_failed",
]
AttemptObserver: TypeAlias = Callable[[int, float | None], Awaitable[None]]


def _classify(error: Exception) -> tuple[FailureReason, float]:
    if isinstance(error, TelegramRetryAfter):
        return "rate_limited", error.retry_after
    if isinstance(error, RateLimitExceededError):
        return "rate_limited", error.result.retry_after
    if isinstance(error, TelegramEntityTooLarge):
        return "permanent", 0
    if isinstance(error, (TelegramNetworkError, TelegramServerError, TimeoutError)):
        return "transient", 0
    if isinstance(error, TelegramAPIError):
        return "permanent", 0
    return "unknown", 0


def _retry_delay(
    policy: RetryPolicy | None,
    error: Exception,
    attempt: int,
    config: RetryConfig,
    backoff: float,
    reason: FailureReason,
) -> float | None:
    if policy is not None:
        delay = policy(error, attempt)
        if delay is not None:
            seconds("retry delay", delay, allow_zero=True)
        return delay

    if reason in ("transient", "rate_limited") or (reason == "unknown" and config.retry_unknown):
        return backoff
    return None


async def deliver(
    send: Callable[[], Awaitable[None]],
    config: RetryConfig,
    policy: RetryPolicy | None,
    observe: AttemptObserver,
) -> FailureReason | None:
    backoff = config.base_delay
    for attempt in range(1, config.max_attempts + 1):
        await observe(attempt, None)
        try:
            await send()
        except Exception as error:  # noqa: BLE001 -- The queue classifies all delivery errors.
            reason, minimum = _classify(error)
            if attempt == config.max_attempts:
                return reason

            try:
                delay = _retry_delay(policy, error, attempt, config, backoff, reason)
                seconds("minimum retry delay", minimum, allow_zero=True)
            except Exception:  # noqa: BLE001 -- A user policy must not terminate a worker.
                logger.warning("Invalid retry decision")
                return "retry_policy_error"

            if delay is None:
                return reason
        else:
            return None

        delay = max(delay, minimum)
        await observe(attempt, delay)
        await asyncio.sleep(delay)
        backoff = min(backoff * 2, config.max_delay)

    raise AssertionError
