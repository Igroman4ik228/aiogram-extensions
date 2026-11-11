"""A live prefix of pending tasks must not starve later abandoned entries."""

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from journal import DeliveryJournal
from taskiq_redis import RedisStreamBroker

if TYPE_CHECKING:
    from redis.typing import StreamIdT


@pytest.mark.usefixtures("transport_wheel")
async def test_reclaim_scans_past_recently_active_prefix(
    journal: DeliveryJournal,
    redis_url: str,
) -> None:
    queue = f"{journal.prefix}:cursor"
    group = f"{journal.prefix}:group"
    broker = RedisStreamBroker(
        redis_url,
        queue_name=queue,
        consumer_group_name=group,
        consumer_id="0",
        xread_block=50,
        idle_timeout=500,
        unacknowledged_batch_size=1,
        unacknowledged_lock_timeout=1,
    )
    await broker.startup()

    reader = broker.listen()
    refresh: asyncio.Task[None] | None = None
    try:
        heads: list[StreamIdT] = [
            await journal.client.xadd(queue, {"data": "head"}) for _ in range(10)
        ]
        tail = await journal.client.xadd(queue, {"data": "tail"})
        await journal.client.xreadgroup(group, "occupied", {queue: ">"}, count=11)
        await journal.client.xclaim(queue, group, "occupied", 0, [tail], idle=10000)

        async def keep_heads_recent() -> None:
            while True:
                await journal.client.xclaim(queue, group, "occupied", 0, heads, idle=0)
                await asyncio.sleep(0.02)

        refresh = asyncio.create_task(keep_heads_recent())
        async with asyncio.timeout(2):
            message = await anext(reader)

        assert message.data == b"tail"

        acknowledgement = message.ack()
        if acknowledgement is not None:
            await acknowledgement
    finally:
        if refresh is not None:
            refresh.cancel()
            with suppress(asyncio.CancelledError):
                await refresh
        await reader.aclose()
        await broker.shutdown()
