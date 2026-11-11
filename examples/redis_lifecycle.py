"""Inject Redis into an aiogram handler without connecting to either service."""

import asyncio
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Chat, Message, Update
from redis.asyncio import Redis

from aiogram_extensions.redis import RedisConfig, RedisLifecycleError, redis_context


async def main() -> None:
    dispatcher = Dispatcher()

    @dispatcher.message(Command("redis"))
    async def redis_status(_message: Message, redis: Redis) -> None:
        # A live handler can call await redis.ping() here.
        print(f"Redis injected: {redis is dispatcher['redis']}")

    async with redis_context(dispatcher, RedisConfig(prefix="example")) as manager:
        print(f"Owned client: {manager.owns_client}")
        print(f"Key: {manager.key('rate_limit', 'incoming', '123', '456')}")

        # Feed a local /redis update. For a live bot, run polling inside the context.
        message = Message(
            message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text="/redis"
        )

        async with Bot("123456:offline-example-token") as bot:
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))

    try:
        _ = manager.client
    except RedisLifecycleError:
        print("Manager closed: True")


if __name__ == "__main__":
    asyncio.run(main())
