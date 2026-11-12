"""Run the documented router through aiogram's public dispatcher boundary."""

import runpy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

from aiogram_extensions.rate_limit import (
    MemoryRateLimitBackend,
    RateLimitConfig,
    RateLimiter,
    RateLimitExceededError,
    RateRule,
)
from aiogram_extensions.rate_limit.middleware import OutgoingRateLimitMiddleware


async def test_catalog_command_and_buttons_edit_the_message(project_root: Path) -> None:
    example = runpy.run_path(str(project_root / "examples" / "pagination_bot.py"))
    dispatcher = Dispatcher()
    dispatcher.include_router(example["build_router"]())

    bot = Bot("123456:synthetic")

    sent = Message(message_id=20, date=datetime.now(UTC), chat=Chat(id=10, type="private")).as_(bot)
    transport = AsyncMock(side_effect=[sent, True, True, True, True, True])
    bot.session.make_request = transport

    user = User(id=30, is_bot=False, first_name="Reader")
    command = Message(
        message_id=1, date=datetime.now(UTC), chat=sent.chat, from_user=user, text="/catalog"
    )

    async with bot:
        await dispatcher.feed_update(bot, Update(update_id=1, message=command))

        assert transport.await_count == 1

        initial = transport.call_args_list[0].args[1]
        assert isinstance(initial, SendMessage)
        assert initial.text == "Catalog\nApple\nBanana"
        assert initial.parse_mode is None
        assert initial.entities is not None
        assert initial.entities[0].type == "bold"
        assert isinstance(initial.reply_markup, InlineKeyboardMarkup)

        data = initial.reply_markup.inline_keyboard[0][-1].callback_data
        query = CallbackQuery(
            id="next", from_user=user, chat_instance="chat", message=sent, data=data
        )

        other_user = User(id=31, is_bot=False, first_name="Guest")
        guest_query = query.model_copy(update={"id": "guest", "from_user": other_user})

        await dispatcher.feed_update(bot, Update(update_id=2, callback_query=guest_query))

        denied = transport.call_args.args[1]
        assert transport.await_count == 2
        assert isinstance(denied, AnswerCallbackQuery)
        assert denied.text == "Navigation expired. Open the list again."

        await dispatcher.feed_update(bot, Update(update_id=3, callback_query=query))

        assert isinstance(transport.call_args_list[2].args[1], AnswerCallbackQuery)

        edit = transport.call_args_list[3].args[1]
        assert isinstance(edit, EditMessageText)
        assert edit.text == "Catalog\nCherry\nDate"
        assert edit.entities == initial.entities
        assert edit.reply_markup is not None
        assert [button.text for button in edit.reply_markup.inline_keyboard[0]] == [
            "←",
            "2/3",
            "Next",
        ]

        await dispatcher.feed_update(bot, Update(update_id=4, callback_query=query))

        assert transport.await_count == 5

        answer = transport.call_args.args[1]
        assert isinstance(answer, AnswerCallbackQuery)
        assert answer.text == "Navigation expired. Open the list again."

        await dispatcher.emit_shutdown()
        current_data = edit.reply_markup.inline_keyboard[0][0].callback_data

        current_query = query.model_copy(update={"id": "closed", "data": current_data})

        await dispatcher.feed_update(bot, Update(update_id=5, callback_query=current_query))

        closed = transport.call_args.args[1]
        assert isinstance(closed, AnswerCallbackQuery)
        assert closed.text == "Navigation expired. Open the list again."


async def test_catalog_button_can_be_retried_after_a_rejected_edit(project_root: Path) -> None:
    example = runpy.run_path(str(project_root / "examples" / "pagination_bot.py"))
    dispatcher = Dispatcher()
    dispatcher.include_router(example["build_router"]())

    chat = Chat(id=10, type="private")
    user = User(id=30, is_bot=False, first_name="Reader")
    now = datetime.now(UTC)
    command = Message(message_id=1, date=now, chat=chat, from_user=user, text="/catalog")
    sent = Message(message_id=20, date=now, chat=chat)

    rejected = TelegramBadRequest(method=EditMessageText(text="Catalog"), message="Rejected edit")
    transport = AsyncMock(side_effect=[sent, True, rejected, True, True])

    async with Bot("123456:synthetic") as bot:
        bot.session.make_request = transport

        await dispatcher.feed_update(bot, Update(update_id=1, message=command))

        send = transport.call_args.args[1]
        assert isinstance(send, SendMessage)
        assert isinstance(send.reply_markup, InlineKeyboardMarkup)

        data = send.reply_markup.inline_keyboard[0][-1].callback_data
        query = CallbackQuery(
            id="next", from_user=user, chat_instance="chat", message=sent, data=data
        )

        with pytest.raises(TelegramBadRequest, match="Rejected edit"):
            await dispatcher.feed_update(bot, Update(update_id=2, callback_query=query))

        retry = query.model_copy(update={"id": "retry"})

        await dispatcher.feed_update(bot, Update(update_id=3, callback_query=retry))

    edit = transport.call_args.args[1]
    assert isinstance(edit, EditMessageText)
    assert edit.text == "Catalog\nCherry\nDate"


async def test_catalog_next_button_works_when_send_message_quota_is_exhausted(
    project_root: Path,
) -> None:
    example = runpy.run_path(str(project_root / "examples" / "pagination_bot.py"))
    dispatcher = Dispatcher()
    dispatcher.include_router(example["build_router"]())
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="method", scope="method", limit=1, window=60),)),
        direction="outgoing",
        backend=MemoryRateLimitBackend(clock=lambda: 0.0),
    )

    chat = Chat(id=10, type="private")
    user = User(id=30, is_bot=False, first_name="Reader")
    now = datetime.now(UTC)
    command = Message(message_id=1, date=now, chat=chat, from_user=user, text="/catalog")
    sent = Message(message_id=20, date=now, chat=chat)
    transport = AsyncMock(side_effect=[sent, True, True])

    async with Bot("123456:synthetic") as bot:
        bot.session.make_request = transport
        bot.session.middleware(OutgoingRateLimitMiddleware(limiter))

        await dispatcher.feed_update(bot, Update(update_id=1, message=command))
        initial = transport.call_args.args[1]
        assert isinstance(initial, SendMessage)
        assert isinstance(initial.reply_markup, InlineKeyboardMarkup)

        with pytest.raises(RateLimitExceededError) as denied:
            await bot.send_message(chat.id, "Another message")

        assert denied.value.result.triggered_rules == ("method",)
        assert transport.await_count == 1

        query = CallbackQuery(
            id="next",
            from_user=user,
            chat_instance="chat",
            message=sent,
            data=initial.reply_markup.inline_keyboard[0][-1].callback_data,
        )
        await dispatcher.feed_update(bot, Update(update_id=2, callback_query=query))

    assert transport.await_count == 3
    answer = transport.await_args_list[1].args[1]
    assert isinstance(answer, AnswerCallbackQuery)
    assert answer.callback_query_id == query.id
    edit = transport.await_args_list[2].args[1]
    assert isinstance(edit, EditMessageText)
    assert edit.message_id == sent.message_id
    assert edit.text == "Catalog\nCherry\nDate"
