"""Exercise middleware through aiogram without contacting Telegram."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import Chat, InlineQuery, Message, Update, User

from aiogram_extensions.rate_limit import (
    MemoryRateLimitBackend,
    RateLimitConfig,
    RateLimiter,
    RateLimitExceededError,
    RateRule,
)
from aiogram_extensions.rate_limit.middleware import (
    IncomingRateLimitMiddleware,
    OutgoingRateLimitMiddleware,
)


async def test_incoming_middleware_isolates_pairs_and_honors_exemptions() -> None:
    dispatcher = Dispatcher()
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="incoming", limit=1, window=60),)),
        direction="incoming",
    )
    dispatcher.update.outer_middleware(
        IncomingRateLimitMiddleware(
            limiter,
            allowed_users={20},
            allowed_chats={30},
            exempt=lambda event: (
                isinstance(event, Update)
                and event.message is not None
                and event.message.text == "/help"
            ),
        )
    )

    @dispatcher.message()
    async def receive(message: Message) -> int:
        return message.message_id

    @dispatcher.inline_query()
    async def inline(query: InlineQuery) -> str:
        return query.id

    cases = (
        (2, 3, "hello", True),
        (2, 3, "again", False),
        (2, 3, "/help", True),
        (4, 3, "hello", True),
        (2, 5, "hello", True),
        (20, 3, "hello", True),
        (20, 3, "again", True),
        (2, 30, "hello", True),
        (2, 30, "again", True),
        (None, 3, "anonymous", True),
        (None, 3, "anonymous", True),
    )

    async with Bot("123456:synthetic") as bot:
        for update_id, (user_id, chat_id, text, allowed) in enumerate(cases, start=1):
            user = User(id=user_id, is_bot=False, first_name="Reader") if user_id else None
            message = Message(
                message_id=update_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="group"),
                from_user=user,
                text=text,
            )

            result = await dispatcher.feed_update(bot, Update(update_id=update_id, message=message))

            assert result == (update_id if allowed else None)

        query = InlineQuery(
            id="inline",
            from_user=User(id=2, is_bot=False, first_name="Reader"),
            query="",
            offset="",
        )

        for update_id in (100, 101):
            assert (
                await dispatcher.feed_update(bot, Update(update_id=update_id, inline_query=query))
                == "inline"
            )


async def test_outgoing_cooldown_is_bot_wide_and_request_is_not_retried() -> None:
    now = 0.0
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="chat", scope="chat", limit=2, window=60),)),
        direction="outgoing",
        backend=MemoryRateLimitBackend(clock=lambda: now),
    )

    request = SendMessage(chat_id=10, text="hello")
    flood = TelegramRetryAfter(method=request, message="slow down", retry_after=5)
    transport = AsyncMock(side_effect=[flood, True, True])

    async with Bot("123456:synthetic") as bot, Bot("654321:synthetic") as other:
        for client in (bot, other):
            client.session.middleware(OutgoingRateLimitMiddleware(limiter))
            client.session.make_request = transport

        with pytest.raises(TelegramRetryAfter) as raised:
            await bot(request)

        assert raised.value is flood
        assert transport.await_count == 1

        now = 2
        with pytest.raises(RateLimitExceededError) as denied:
            await bot.get_me()

        assert denied.value.result.triggered_rules == ("telegram_retry_after",)
        assert denied.value.result.retry_after == 3
        assert transport.await_count == 1

        await other(request)

        now = 5
        await bot(request)
        with pytest.raises(RateLimitExceededError) as charged:
            await bot(request)

        assert charged.value.result.triggered_rules == ("chat",)
        assert transport.await_count == 3


async def test_outgoing_composes_bot_method_chat_group_and_custom_rules() -> None:
    limiter = RateLimiter(
        RateLimitConfig(
            rules=(
                RateRule(name="bot", scope="bot", limit=3, window=60),
                RateRule(name="method", scope="method", limit=2, window=60),
                RateRule(name="chat", scope="chat", limit=1, window=60),
                RateRule(name="group", scope="group", limit=1, window=60),
                RateRule(name="custom", scope="custom", limit=1, window=60),
            )
        ),
        direction="outgoing",
    )

    transport = AsyncMock(return_value=True)

    async with Bot("123456:synthetic") as bot:
        bot.session.make_request = transport
        bot.session.middleware(
            OutgoingRateLimitMiddleware(
                limiter,
                chat_type=lambda _, chat_id: "supergroup" if chat_id == -100 else None,
                custom_key=lambda _, method: (
                    method.text if isinstance(method, SendMessage) else None
                ),
            )
        )

        await bot.send_message(-100, "one")

        with pytest.raises(RateLimitExceededError) as group:
            await bot.send_message(-100, "one")

        assert group.value.result.triggered_rules == ("chat", "group", "custom")

        await bot.send_message(-200, "two")
        with pytest.raises(RateLimitExceededError) as unknown:
            await bot.send_message(-200, "three")

        assert unknown.value.result.triggered_rules == ("method", "chat")

        await bot.get_me()
        with pytest.raises(RateLimitExceededError) as global_limit:
            await bot.get_me()

        assert global_limit.value.result.triggered_rules == ("bot",)
        assert transport.await_count == 3
