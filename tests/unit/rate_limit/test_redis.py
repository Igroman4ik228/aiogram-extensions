"""Verify how the rate limiter handles Redis replies."""

from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from aiogram_extensions.integrations import BackendErrorEvent, Event, EventDispatcher
from aiogram_extensions.rate_limit import (
    RateLimitBackendError,
    RateLimitConfig,
    RateLimitContext,
    RateLimiter,
    RateRule,
    RedisRateLimitBackend,
)
from aiogram_extensions.redis import RedisConnectionManager


@pytest.mark.parametrize(
    "reply",
    [None, (), [], [0], [0, 0, 0], [0, True], [-1, 0], [0, -1], [0, "5"]],
)
async def test_invalid_redis_reply_raises_backend_error(reply: object) -> None:
    client = AsyncMock(spec=Redis)
    client.eval = AsyncMock(return_value=reply)
    observed: list[Event] = []

    async def record(event: Event) -> None:
        observed.append(event)

    async with RedisConnectionManager(client=client) as manager:
        limiter = RateLimiter(
            RateLimitConfig(rules=(RateRule(name="bot", scope="bot", limit=1, window=1),)),
            direction="incoming",
            backend=RedisRateLimitBackend(manager),
            events=EventDispatcher((record,)),
        )

        with pytest.raises(RateLimitBackendError, match="invalid rate limit reply"):
            await limiter.check(RateLimitContext(bot_id=1))

    assert observed == [BackendErrorEvent(component="rate_limit", operation="check")]
