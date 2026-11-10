"""Open and close an aiogram session locally, without Telegram API requests."""

import asyncio

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


async def main() -> None:
    session = AiohttpSession()
    # This synthetic token passes local validation; no API methods are called.
    async with Bot(token="123456:offline-example-token", session=session):  # noqa: S106
        client = await session.create_session()
        print(f"Session open: {not client.closed}")

    print(f"Session closed: {client.closed}")


if __name__ == "__main__":
    asyncio.run(main())
