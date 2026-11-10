"""Create and close an owned Redis client without connecting to a server."""

import asyncio

from aiogram_extensions.redis import RedisConfig, RedisConnectionManager, RedisLifecycleError


async def main() -> None:
    manager = RedisConnectionManager(RedisConfig(prefix="example"))
    async with manager:
        print(f"Owned client: {manager.owns_client}")
        print(f"Key: {manager.key('rate_limit', 'incoming', '123', '456')}")
        # The first Redis command would connect. This offline example sends none.
    try:
        _ = manager.client
    except RedisLifecycleError:
        print("Manager closed: True")


if __name__ == "__main__":
    asyncio.run(main())
