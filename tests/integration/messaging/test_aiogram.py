"""Exercise Dispatcher injection and Telegram delivery over loopback HTTP."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import BufferedInputFile, Chat, FSInputFile, InputMediaPhoto, Message, Update
from aiohttp import web
from aiohttp.test_utils import TestServer

from aiogram_extensions.integrations import Event, EventDispatcher, TaskEvent
from aiogram_extensions.messaging import (
    EnqueueResult,
    MessageQueue,
    QueueConfig,
    QueueError,
    QueueNotRunningError,
    RetryConfig,
)
from aiogram_extensions.messaging.aiogram import message_queue_context
from aiogram_extensions.rate_limit import (
    MemoryRateLimitBackend,
    RateLimitConfig,
    RateLimiter,
    RateRule,
)
from aiogram_extensions.rate_limit.middleware import OutgoingRateLimitMiddleware


def _update(message_id: int, text: str) -> Update:
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=123, type="private"),
            text=text,
        ),
    )


async def test_dispatcher_queue_retries_a_locally_limited_reply_and_deduplicates_updates() -> None:
    sent: list[tuple[str, str]] = []
    scheduled: list[TaskEvent] = []
    now = 0.0

    async def telegram(request: web.Request) -> web.Response:
        data = await request.post()
        sent.append((str(data["chat_id"]), str(data["text"])))
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": len(sent),
                    "date": 0,
                    "chat": {"id": 123, "type": "private"},
                },
            }
        )

    async def observe(event: Event) -> None:
        nonlocal now
        if isinstance(event, TaskEvent) and event.kind == "retry_scheduled":
            scheduled.append(event)
            now = 1.0

    dispatcher = Dispatcher()

    @dispatcher.message()
    async def reply(message: Message, message_queue: MessageQueue) -> EnqueueResult:
        return await message_queue.send_message(
            message.chat.id,
            f"Echo: {message.text}",
            idempotency_key=f"{message.chat.id}:{message.message_id}",
        )

    app = web.Application()
    app.router.add_post("/bot123456:synthetic/sendMessage", telegram)
    async with TestServer(app) as server:
        session = AiohttpSession(api=TelegramAPIServer.from_base(str(server.make_url("/"))))
        limiter = RateLimiter(
            RateLimitConfig(rules=(RateRule(name="outgoing", scope="bot", limit=1, window=0.01),)),
            direction="outgoing",
            backend=MemoryRateLimitBackend(clock=lambda: now),
        )
        session.middleware(OutgoingRateLimitMiddleware(limiter))
        async with Bot("123456:synthetic", session=session) as bot:
            queue = MessageQueue(
                bot=bot,
                config=QueueConfig(retry=RetryConfig(base_delay=0.001)),
                events=EventDispatcher((observe,)),
            )
            async with message_queue_context(dispatcher, queue) as attached:
                assert attached is queue
                first = await dispatcher.feed_update(bot, _update(1, "first"))
                duplicate = await dispatcher.feed_update(bot, _update(1, "first"))
                second = await dispatcher.feed_update(bot, _update(2, "second"))

            assert "message_queue" not in dispatcher.workflow_data
            with pytest.raises(QueueNotRunningError):
                await queue.send_message(123, "outside the context")

    assert duplicate.duplicate is True
    assert duplicate.generation == first.generation
    assert sent == [("123", "Echo: first"), ("123", "Echo: second")]
    (retry,) = scheduled
    assert (retry.generation, retry.attempt) == (second.generation, 1)
    assert retry.retry_after == pytest.approx(0.01)
    assert queue.dead_letters() == ()


async def test_album_upload_replays_after_a_real_http_bad_request(tmp_path: Path) -> None:
    uploads: list[list[tuple[str, bytes]]] = []
    request_shape: list[tuple[str, list[str | None]]] = []
    local_file = tmp_path / "local.png"
    local_file.write_bytes(b"local original")

    async def telegram(request: web.Request) -> web.Response:
        data = await request.post()
        media = json.loads(str(data["media"]))
        request_shape.append((str(data["chat_id"]), [item.get("caption") for item in media]))
        attachments: list[tuple[str, bytes]] = []
        for item in media:
            attachment = data[item["media"].removeprefix("attach://")]
            assert isinstance(attachment, web.FileField)
            attachments.append((attachment.filename, attachment.file.read()))
        uploads.append(attachments)
        if len(uploads) == 1:
            return web.json_response(
                {"ok": False, "error_code": 400, "description": "Bad Request"}, status=400
            )
        return web.json_response(
            {
                "ok": True,
                "result": [
                    {"message_id": number, "date": 0, "chat": {"id": 123, "type": "private"}}
                    for number in (1, 2)
                ],
            }
        )

    app = web.Application()
    app.router.add_post("/bot123456:synthetic/sendMediaGroup", telegram)
    async with TestServer(app) as server:
        session = AiohttpSession(api=TelegramAPIServer.from_base(str(server.make_url("/"))))
        async with Bot("123456:synthetic", session=session) as bot:
            queue = MessageQueue(bot=bot)
            buffered = BufferedInputFile(b"buffered original", filename="memory.png")
            async with queue:
                first = await queue.send_media_group(
                    123,
                    [
                        InputMediaPhoto(media=buffered, caption="first"),
                        InputMediaPhoto(media=FSInputFile(local_file)),
                    ],
                )
                buffered.data = b"caller mutation"

            (failed,) = queue.dead_letters()
            assert (failed.generation, failed.reason, failed.attempts) == (
                first.generation,
                "permanent",
                1,
            )

            async with queue:
                replayed = await queue.replay(failed.task_id, failed.generation)

            assert replayed.task_id == first.task_id
            assert replayed.generation != first.generation
            assert queue.dead_letters() == ()

    assert request_shape == [("123", ["first", None])] * 2
    assert uploads == [[("memory.png", b"buffered original"), ("local.png", b"local original")]] * 2


async def test_queue_context_releases_custom_binding_after_a_handler_error() -> None:
    dispatcher = Dispatcher(message_queue="existing")
    bound: list[MessageQueue] = []

    @dispatcher.message()
    async def fail(_message: Message, *, outbox: MessageQueue) -> None:
        bound.append(outbox)
        raise RuntimeError

    async with Bot("123456:synthetic") as bot:
        queue = MessageQueue(bot=bot)
        with pytest.raises(RuntimeError):
            async with message_queue_context(dispatcher, queue, key="outbox"):
                await dispatcher.feed_update(bot, _update(1, "fail"))

        assert bound == [queue]
        assert "outbox" not in dispatcher.workflow_data
        assert dispatcher["message_queue"] == "existing"
        with pytest.raises(QueueNotRunningError):
            await queue.send_message(123, "after handler error")


async def test_queue_context_refuses_an_occupied_key_before_starting_workers() -> None:
    dispatcher = Dispatcher(message_queue="existing")
    async with Bot("123456:synthetic") as bot:
        queue = MessageQueue(bot=bot)
        with pytest.raises(ValueError, match="key is already registered"):
            async with message_queue_context(dispatcher, queue):
                pytest.fail("existing dependency was overwritten")

        assert dispatcher["message_queue"] == "existing"
        with pytest.raises(QueueNotRunningError):
            await queue.send_message(123, "queue was never started")


async def test_queue_context_rejects_a_second_dispatcher_without_stopping_the_first() -> None:
    dispatcher = Dispatcher()
    other = Dispatcher()

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=True)
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot)
        async with message_queue_context(dispatcher, queue):
            with pytest.raises(QueueError, match="already managed by a context"):
                async with message_queue_context(other, queue):
                    pass

            assert dispatcher["message_queue"] is queue
            assert "message_queue" not in other.workflow_data
            await queue.send_message(123, "first context still running")

    transport.assert_awaited_once()
