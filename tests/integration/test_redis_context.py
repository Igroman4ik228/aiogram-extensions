"""Verify dispatcher data and client ownership across context exits."""

from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from redis.asyncio import Redis

from aiogram_extensions.redis import redis_context


async def test_redis_context_unbinds_borrowed_client_after_error() -> None:
    dispatcher = Dispatcher(redis="existing dependency")

    async with Redis() as client:
        close = AsyncMock(wraps=client.aclose)
        client.aclose = close

        async def run_with_redis() -> None:
            async with redis_context(dispatcher, client=client, key="cache") as manager:
                assert dispatcher["cache"] is client
                assert not manager.owns_client
                raise RuntimeError

        with pytest.raises(RuntimeError):
            await run_with_redis()

        close.assert_not_awaited()
        assert "cache" not in dispatcher.workflow_data
        assert dispatcher["redis"] == "existing dependency"


async def test_redis_context_rejects_an_occupied_key() -> None:
    dispatcher = Dispatcher(redis="existing dependency")

    with pytest.raises(ValueError, match="key is already registered"):
        async with redis_context(dispatcher):
            pytest.fail("existing dependency was overwritten")

    assert dispatcher["redis"] == "existing dependency"
