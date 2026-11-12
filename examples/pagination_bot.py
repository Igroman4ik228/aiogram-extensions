"""Preview the catalog offline or run /catalog in Telegram with --live and BOT_TOKEN."""

import argparse
import asyncio
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.formatting import Bold, Text, as_list

from aiogram_extensions.pagination import CursorPage, Page, Paginator
from aiogram_extensions.pagination.aiogram import NavigationConfig, TextPagination
from aiogram_extensions.utilities import truncate_text


def render_page(page: Page[str] | CursorPage[str]) -> Text:
    rows = [truncate_text(item, max_units=80) for item in page.items]
    return as_list(Bold("Catalog"), *(rows or ["No items."]))


def catalog_source() -> Paginator[str]:
    return Paginator(["Apple", "Banana", "Cherry", "Date", "Elderberry"], page_size=2)


def build_router() -> Router:
    router = Router(name="catalog")
    pagination = TextPagination(
        catalog_source(),
        render=render_page,
        config=NavigationConfig(prefix="catalog", current_text="{page}/{pages}", next_text="Next"),
    )

    router.include_router(pagination.router)

    @router.message(Command("catalog"))
    async def catalog(message: Message) -> None:
        if message.from_user is not None:
            await pagination.answer(message)

    return router


async def main(*, live: bool) -> None:
    if live:
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise SystemExit("Set BOT_TOKEN before running with --live.")

        dispatcher = Dispatcher()
        dispatcher.include_router(build_router())

        async with Bot(token) as bot:
            # aiogram's public signature contains an untyped UNSET default.
            await dispatcher.start_polling(  # pyright: ignore[reportUnknownMemberType]
                bot, close_bot_session=False, handle_as_tasks=False
            )
        return

    paginator = catalog_source()

    for number in (1, 2):
        page = await paginator.page(number)
        print(f"Page {page.number}/{page.total_pages}")
        print(render_page(page).render()[0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")

    asyncio.run(main(live=parser.parse_args().live))
