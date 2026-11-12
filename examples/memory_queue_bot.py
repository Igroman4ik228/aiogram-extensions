"""Run /catalog and queued /hello offline or poll Telegram with --live and BOT_TOKEN."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from typing import TypeVar

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from aiogram_extensions.messaging import MessageQueue, QueueConfig
from aiogram_extensions.messaging.aiogram import message_queue_context
from aiogram_extensions.pagination import CursorPage, Page, Paginator
from aiogram_extensions.pagination.aiogram import TextPagination
from aiogram_extensions.rate_limit import RateLimitConfig, RateLimiter, RateRule
from aiogram_extensions.rate_limit.middleware import (
    IncomingRateLimitMiddleware,
    OutgoingRateLimitMiddleware,
)

_Result = TypeVar("_Result")


class OfflineSession(AiohttpSession):
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[_Result],
        timeout: int | None = None,  # noqa: ARG002, ASYNC109 -- aiogram session interface.
    ) -> _Result:
        response = self.check_response(
            bot,
            method,
            200,
            json.dumps({"ok": True, "result": self._result(method)}),
        )
        if response.result is None:
            raise RuntimeError("Offline request returned no result")
        return response.result

    @staticmethod
    def _result(method: object) -> object:
        match method:
            case SendMessage():
                print(f"Sent to {method.chat_id}: {method.text}")
                markup = method.reply_markup
                return {
                    "message_id": 20 if markup is not None else 21,
                    "date": 0,
                    "chat": {"id": method.chat_id, "type": "private"},
                    "text": method.text,
                    "reply_markup": markup.model_dump(mode="json") if markup is not None else None,
                }
            case AnswerCallbackQuery():
                print("Callback answered")
                return True
            case EditMessageText():
                print(f"Edited {method.message_id}: {method.text}")
                return True
            case _:
                raise TypeError


def render_catalog(page: Page[str] | CursorPage[str]) -> str:
    return "\n".join(("Catalog", *page.items))


def build_dispatcher(bot: Bot) -> Dispatcher:
    router = Router(name="memory-queue-demo")
    pagination = TextPagination(
        Paginator(["Apple", "Banana", "Cherry", "Date"], page_size=2),
        render=render_catalog,
    )
    router.include_router(pagination.router)

    incoming = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="messages", limit=5, window=10),)),
        direction="incoming",
    )
    # Only message updates consume the incoming message quota.
    router.message.outer_middleware(IncomingRateLimitMiddleware(incoming))

    outgoing = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="per_method", scope="method", limit=5, window=1),)),
        direction="outgoing",
    )
    bot.session.middleware(OutgoingRateLimitMiddleware(outgoing))

    @router.message(Command("catalog"))
    async def catalog(message: Message) -> Message | None:
        if message.from_user is not None:
            return await pagination.answer(message)
        return None

    @router.message(Command("hello"))
    async def hello(message: Message, message_queue: MessageQueue) -> None:
        await message_queue.send(
            message.reply("Hello from Memory Queue!"),
            idempotency_key=f"hello:{message.chat.id}:{message.message_id}",
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def preview(bot: Bot, dispatcher: Dispatcher) -> None:
    now = datetime.now(UTC)
    chat = Chat(id=123, type="private")
    user = User(id=123, is_bot=False, first_name="Reader")
    catalog = Message(message_id=1, date=now, chat=chat, from_user=user, text="/catalog")
    sent = await dispatcher.feed_update(bot, Update(update_id=1, message=catalog))
    if not isinstance(sent, Message) or sent.reply_markup is None:
        raise AssertionError

    query = CallbackQuery(
        id="next",
        from_user=user,
        chat_instance="demo",
        message=sent,
        data=sent.reply_markup.inline_keyboard[0][-1].callback_data,
    )
    await dispatcher.feed_update(bot, Update(update_id=2, callback_query=query))

    hello = Message(message_id=2, date=now, chat=chat, from_user=user, text="/hello")
    await dispatcher.feed_update(bot, Update(update_id=3, message=hello))


async def run(bot: Bot, *, live: bool) -> None:
    queue = MessageQueue(bot=bot, config=QueueConfig(workers=2, maxsize=32))
    dispatcher = build_dispatcher(bot)
    async with message_queue_context(dispatcher, queue):
        if live:
            await dispatcher.start_polling(  # pyright: ignore[reportUnknownMemberType]
                bot, close_bot_session=False, handle_as_tasks=False
            )
        else:
            await preview(bot, dispatcher)


async def main(*, live: bool) -> None:
    token = os.environ.get("BOT_TOKEN") if live else "123456:synthetic"
    if not token:
        raise SystemExit("Set BOT_TOKEN before running with --live.")

    async with Bot(token, session=None if live else OfflineSession()) as bot:
        await run(bot, live=live)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    asyncio.run(main(live=parser.parse_args().live))
