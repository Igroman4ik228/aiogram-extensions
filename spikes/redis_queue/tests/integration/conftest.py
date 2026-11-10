"""Real Redis boundary for the durable application journal."""

import uuid
from collections.abc import AsyncIterator

import pytest
from journal import DeliveryJournal
from redis.asyncio import Redis


@pytest.fixture
async def journal(redis_url: str) -> AsyncIterator[DeliveryJournal]:
    async with Redis.from_url(redis_url, decode_responses=True) as client:  # pyright: ignore[reportUnknownMemberType]
        yield DeliveryJournal(client, f"journal:{uuid.uuid4().hex}")
