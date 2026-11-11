"""Exact window boundaries and wait cancellation with a controlled clock."""

import asyncio

import pytest

from aiogram_extensions.integrations import Event, EventDispatcher, LimitExceededEvent
from aiogram_extensions.rate_limit import (
    Algorithm,
    MemoryRateLimitBackend,
    RateLimitConfig,
    RateLimitContext,
    RateLimiter,
    RateLimitResult,
    RateRule,
)


@pytest.mark.parametrize("attribute", ["config", "direction"])
def test_limiter_configuration_cannot_be_replaced(attribute: str) -> None:
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="pair", limit=1, window=60),)), direction="incoming"
    )
    current = getattr(limiter, attribute)

    with pytest.raises(AttributeError):
        setattr(limiter, attribute, current)


async def test_direct_check_requires_context_for_configured_chat_rule() -> None:
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="chat", scope="chat", limit=1, window=60),)),
        direction="outgoing",
    )

    with pytest.raises(ValueError, match="chat requires additional rate limit context"):
        await limiter.check(RateLimitContext(bot_id=1))


@pytest.mark.parametrize("algorithm", ["fixed_window", "sliding_window"])
async def test_rejection_does_not_charge_other_rules(algorithm: Algorithm) -> None:
    now = 0.0
    limiter = RateLimiter(
        RateLimitConfig(
            rules=(
                RateRule(name="chat", scope="chat", limit=2, window=10),
                RateRule(name="pair", limit=1, window=4, algorithm=algorithm),
            ),
        ),
        direction="incoming",
        backend=MemoryRateLimitBackend(clock=lambda: now),
    )
    first = RateLimitContext(bot_id=1, chat_id=2, user_id=3)
    other = RateLimitContext(bot_id=1, chat_id=2, user_id=4)

    assert (await limiter.check(first)).allowed
    now = 1
    assert await limiter.check(first) == RateLimitResult(
        allowed=False, retry_after=3, triggered_rules=("pair",)
    )
    assert (await limiter.check(other)).allowed
    now = 2
    assert await limiter.check(first) == RateLimitResult(
        allowed=False, retry_after=8, triggered_rules=("chat", "pair")
    )
    now = 10
    assert (await limiter.check(first)).allowed


@pytest.mark.parametrize(
    ("algorithm", "second_admitted", "expected_delay"),
    [("fixed_window", True, 0), ("sliding_window", False, 2)],
)
async def test_window_boundaries_follow_accepted_events(
    algorithm: Algorithm, *, second_admitted: bool, expected_delay: float
) -> None:
    now = 0.0
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="pair", limit=2, window=4, algorithm=algorithm),)),
        direction="incoming",
        backend=MemoryRateLimitBackend(clock=lambda: now),
    )
    context = RateLimitContext(bot_id=1, chat_id=2, user_id=3)

    assert (await limiter.check(context)).allowed
    now = 2
    assert (await limiter.check(context)).allowed
    now = 4
    assert (await limiter.check(context)).allowed
    decision = await limiter.check(context)

    assert decision.allowed is second_admitted
    assert decision.retry_after == expected_delay
    now = 8
    assert (await limiter.check(context)).allowed


@pytest.mark.parametrize("algorithm", ["fixed_window", "sliding_window"])
async def test_window_shorter_than_clock_precision_still_limits(algorithm: Algorithm) -> None:
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="pair", limit=1, window=1e-9, algorithm=algorithm),)),
        direction="incoming",
        backend=MemoryRateLimitBackend(clock=lambda: 2.0**30),
    )
    context = RateLimitContext(bot_id=1, chat_id=2, user_id=3)

    assert (await limiter.check(context)).allowed
    decision = await limiter.check(context)

    assert decision.triggered_rules == ("pair",)
    assert decision.retry_after > 0


async def test_cancelled_wait_does_not_reserve_next_window() -> None:
    now = 0.0
    waiting = asyncio.Event()

    async def record(event: Event) -> None:
        if isinstance(event, LimitExceededEvent):
            waiting.set()

    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="pair", limit=1, window=60),), on_limit="wait"),
        direction="incoming",
        backend=MemoryRateLimitBackend(clock=lambda: now),
        events=EventDispatcher((record,)),
    )
    context = RateLimitContext(bot_id=1, chat_id=2, user_id=3)
    assert (await limiter.acquire(context)).allowed

    task = asyncio.create_task(limiter.acquire(context))
    try:
        async with asyncio.timeout(3):
            await waiting.wait()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    now = 60
    assert (await limiter.check(context)).allowed
