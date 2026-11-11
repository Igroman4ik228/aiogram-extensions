"""Exercise the same public contract with local memory and a real Redis server."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import replace
from socket import socket
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetMe

from aiogram_extensions.integrations import (
    BackendErrorEvent,
    Event,
    EventDispatcher,
    LimitExceededEvent,
)
from aiogram_extensions.rate_limit import (
    Algorithm,
    MemoryRateLimitBackend,
    RateLimitBackendError,
    RateLimitConfig,
    RateLimitContext,
    RateLimiter,
    RateRule,
    RedisRateLimitBackend,
)
from aiogram_extensions.rate_limit.middleware import OutgoingRateLimitMiddleware
from aiogram_extensions.redis import RedisConfig, RedisConnectionManager


def _redis_config() -> RedisConfig:
    url = os.environ.get("AIEXT_TEST_REDIS_URL")
    if url is None:
        pytest.skip("Set AIEXT_TEST_REDIS_URL to run against an isolated Redis")

    return RedisConfig(url=url, prefix=f"aie-test-{uuid4().hex}")


@pytest.fixture(params=["memory", "redis"])
async def backends(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[MemoryRateLimitBackend | RedisRateLimitBackend, ...]]:
    if request.param == "memory":
        backend = MemoryRateLimitBackend()
        yield (backend, backend)
        return

    config = _redis_config()

    async with AsyncExitStack() as stack:
        managers = [
            await stack.enter_async_context(RedisConnectionManager(config)) for _ in range(2)
        ]
        yield tuple(RedisRateLimitBackend(manager) for manager in managers)


@pytest.mark.parametrize("algorithm", ["fixed_window", "sliding_window"])
async def test_concurrent_checks_share_budget_and_isolate_context(
    backends: tuple[MemoryRateLimitBackend | RedisRateLimitBackend, ...], algorithm: Algorithm
) -> None:
    config = RateLimitConfig(
        rules=(RateRule(name="pair", limit=7, window=10, algorithm=algorithm),)
    )
    first, second = (RateLimiter(config, direction="incoming", backend=b) for b in backends)
    context = RateLimitContext(bot_id=1, user_id=2, chat_id=3)

    decisions = await asyncio.gather(*(limiter.check(context) for limiter in (first, second) * 20))

    assert sum(result.allowed for result in decisions) == 7
    assert all(
        result.triggered_rules == ("pair",) and 0 < result.retry_after <= 10
        for result in decisions
        if not result.allowed
    )

    for isolated in (
        RateLimitContext(bot_id=4, user_id=2, chat_id=3),
        RateLimitContext(bot_id=1, user_id=4, chat_id=3),
        RateLimitContext(bot_id=1, user_id=2, chat_id=4),
    ):
        assert (await second.check(isolated)).allowed


async def test_combined_rules_do_not_charge_on_denial(
    backends: tuple[MemoryRateLimitBackend | RedisRateLimitBackend, ...],
) -> None:
    config = RateLimitConfig(
        rules=(
            RateRule(name="chat", scope="chat", limit=2, window=10),
            RateRule(name="pair", limit=1, window=5, algorithm="sliding_window"),
        )
    )
    first, second = (RateLimiter(config, direction="incoming", backend=b) for b in backends)
    one = RateLimitContext(bot_id=1, user_id=2, chat_id=3)
    two = RateLimitContext(bot_id=1, user_id=3, chat_id=3)

    assert (await first.check(one)).allowed
    assert (await second.check(one)).triggered_rules == ("pair",)
    assert (await second.check(two)).allowed

    denial = await first.check(one)

    assert denial.triggered_rules == ("chat", "pair")
    assert 5 < denial.retry_after <= 10


@pytest.mark.parametrize("algorithm", ["fixed_window", "sliding_window"])
async def test_wait_rechecks_after_window(
    backends: tuple[MemoryRateLimitBackend | RedisRateLimitBackend, ...], algorithm: Algorithm
) -> None:
    observed: list[Event] = []

    async def record(event: Event) -> None:
        observed.append(event)

    limiter = RateLimiter(
        RateLimitConfig(
            rules=(RateRule(name="bot", scope="bot", limit=1, window=0.25, algorithm=algorithm),),
            on_limit="wait",
        ),
        direction="outgoing",
        backend=backends[0],
        events=EventDispatcher((record,)),
    )
    context = RateLimitContext(bot_id=1)

    assert (await limiter.check(context)).allowed

    async with asyncio.timeout(3):
        assert (await limiter.acquire(context)).allowed

    assert observed
    assert isinstance(observed[0], LimitExceededEvent)
    assert 0 < observed[0].retry_after <= 0.25


async def test_telegram_cooldown_is_shared_across_limiters(
    backends: tuple[MemoryRateLimitBackend | RedisRateLimitBackend, ...],
) -> None:
    config = RateLimitConfig(rules=(RateRule(name="bot", scope="bot", limit=5, window=10),))
    first, second = (RateLimiter(config, direction="outgoing", backend=b) for b in backends)
    request = GetMe()
    flood = TelegramRetryAfter(method=request, message="slow down", retry_after=10)

    async with Bot("123456:synthetic") as bot:
        bot.session.middleware(OutgoingRateLimitMiddleware(first))
        bot.session.make_request = AsyncMock(side_effect=flood)

        with pytest.raises(TelegramRetryAfter):
            await bot(request)

        decision = await second.check(RateLimitContext(bot_id=bot.id))

    assert decision.triggered_rules == ("telegram_retry_after",)
    assert 0 < decision.retry_after <= 10


async def test_redis_command_error_fails_closed_without_charging_other_rules() -> None:
    observed: list[Event] = []

    async def record(event: Event) -> None:
        observed.append(event)

    async with RedisConnectionManager(_redis_config()) as manager:
        limiter = RateLimiter(
            RateLimitConfig(
                rules=(
                    RateRule(name="all", scope="bot", limit=1, window=10),
                    RateRule(name="broken", scope="bot", limit=1, window=10),
                )
            ),
            direction="incoming",
            backend=RedisRateLimitBackend(manager),
            events=EventDispatcher((record,)),
        )

        key = manager.key("rate_limit", "incoming", "1", "broken", "fixed_window", "bot")
        await manager.client.set(key, "invalid", ex=10)

        context = RateLimitContext(bot_id=1)

        with pytest.raises(RateLimitBackendError):
            await limiter.check(context)

        assert observed == [BackendErrorEvent(component="rate_limit", operation="check")]

        await manager.client.delete(key)
        assert (await limiter.check(context)).allowed


async def test_redis_acl_error_does_not_charge_any_rule() -> None:
    config = _redis_config()

    username = f"limiter-{uuid4().hex}"
    password = uuid4().hex

    parsed = urlsplit(config.url)
    address = parsed.netloc.rsplit("@", 1)[-1]
    limited_url = parsed._replace(netloc=f"{username}:{password}@{address}").geturl()

    policy = RateLimitConfig(
        rules=(
            RateRule(name="fixed", scope="bot", limit=1, window=10),
            RateRule(name="sliding", scope="bot", limit=1, window=10, algorithm="sliding_window"),
        )
    )
    context = RateLimitContext(bot_id=1)

    async with RedisConnectionManager(config) as admin:
        # redis-py leaves the ACL methods' **kwargs unannotated.
        await admin.client.acl_setuser(  # pyright: ignore[reportUnknownMemberType]
            username,
            enabled=True,
            passwords=[f"+{password}"],
            keys=[f"{config.prefix}:*"],
            commands=["+@all", "-pexpire"],
        )

        try:
            async with RedisConnectionManager(replace(config, url=limited_url)) as restricted:
                limiter = RateLimiter(
                    policy, direction="incoming", backend=RedisRateLimitBackend(restricted)
                )

                with pytest.raises(RateLimitBackendError):
                    await limiter.check(context)
        finally:
            await admin.client.acl_deluser(username)  # pyright: ignore[reportUnknownMemberType]

        retry = RateLimiter(policy, direction="incoming", backend=RedisRateLimitBackend(admin))

        assert (await retry.check(context)).allowed


@pytest.mark.parametrize("algorithm", ["fixed_window", "sliding_window"])
async def test_redis_removes_idle_window_after_ttl(algorithm: Algorithm) -> None:
    rule = RateRule(name="all", scope="bot", limit=1, window=1, algorithm=algorithm)

    async with RedisConnectionManager(_redis_config()) as manager:
        limiter = RateLimiter(
            RateLimitConfig(rules=(rule,)),
            direction="incoming",
            backend=RedisRateLimitBackend(manager),
        )
        key = manager.key("rate_limit", "incoming", "1", rule.name, algorithm, "bot")

        assert (await limiter.check(RateLimitContext(bot_id=1))).allowed
        assert 0 < await manager.client.pttl(key) <= 1000

        await asyncio.sleep(1.05)

        assert not await manager.client.exists(key)


async def test_unstarted_redis_manager_fails_closed() -> None:
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="bot", scope="bot", limit=1, window=10),)),
        direction="incoming",
        backend=RedisRateLimitBackend(RedisConnectionManager()),
    )

    with pytest.raises(RateLimitBackendError, match="manager is unavailable"):
        await limiter.check(RateLimitContext(bot_id=1))


async def test_redis_connection_failure_stops_outgoing_request() -> None:
    with socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        config = RedisConfig(url=f"redis://127.0.0.1:{port}/0")

        async with RedisConnectionManager(config) as manager, Bot("123456:synthetic") as bot:
            limiter = RateLimiter(
                RateLimitConfig(rules=(RateRule(name="bot", scope="bot", limit=1, window=10),)),
                direction="outgoing",
                backend=RedisRateLimitBackend(manager),
            )
            transport = AsyncMock()
            bot.session.middleware(OutgoingRateLimitMiddleware(limiter))
            bot.session.make_request = transport

            with pytest.raises(RateLimitBackendError):
                await bot.get_me()

            transport.assert_not_awaited()
