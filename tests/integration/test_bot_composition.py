"""Exercise the combined bot through its public Dispatcher entry point."""

import runpy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

from aiogram_extensions.integrations import Event, EventDispatcher, TaskEvent
from aiogram_extensions.messaging import MessageQueue
from aiogram_extensions.messaging.aiogram import message_queue_context


async def test_catalog_and_queued_reply_share_dispatcher_and_session(project_root: Path) -> None:
    example = runpy.run_path(str(project_root / "examples" / "memory_queue_bot.py"))
    now = datetime.now(UTC)
    chat = Chat(id=123, type="private")
    user = User(id=42, is_bot=False, first_name="Reader")
    catalog = Message(message_id=20, date=now, chat=chat)
    command = Message(message_id=1, date=now, chat=chat, from_user=user, text="/catalog")
    hello = Message(message_id=2, date=now, chat=chat, from_user=user, text="/hello")

    deliveries: list[TaskEvent] = []

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent):
            deliveries.append(event)

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=[catalog.as_(bot), True, True, catalog.as_(bot)])
        bot.session.make_request = transport
        dispatcher = example["build_dispatcher"](bot)
        queue = MessageQueue(bot=bot, events=EventDispatcher((observe,)))

        async with message_queue_context(dispatcher, queue):
            await dispatcher.feed_update(bot, Update(update_id=1, message=command))
            assert transport.await_args is not None
            initial = transport.await_args.args[1]
            assert isinstance(initial, SendMessage)
            assert initial.text == "Catalog\nApple\nBanana"
            assert isinstance(initial.reply_markup, InlineKeyboardMarkup)
            next_page = initial.reply_markup.inline_keyboard[0][-1].callback_data

            query = CallbackQuery(
                id="next", from_user=user, chat_instance="demo", message=catalog, data=next_page
            )
            await dispatcher.feed_update(bot, Update(update_id=2, callback_query=query))
            await dispatcher.feed_update(bot, Update(update_id=3, message=hello))

    methods = [call.args[1] for call in transport.await_args_list]
    assert [type(method) for method in methods] == [
        SendMessage,
        AnswerCallbackQuery,
        EditMessageText,
        SendMessage,
    ]
    assert methods[2].text == "Catalog\nCherry\nDate"
    assert methods[3].text == "Hello from Memory Queue!"
    assert methods[3].reply_parameters.message_id == 2
    assert [event.kind for event in deliveries] == [
        "task_enqueued",
        "task_started",
        "task_completed",
    ]
    assert queue.dead_letters() == ()
